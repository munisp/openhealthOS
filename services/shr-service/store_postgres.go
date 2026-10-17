// Postgres backend for the SHR — resources and the append-only access
// audit on the state plane. The access rule itself never changes: it lives
// in Store.authorizeLocked and runs identically over either backend.
package main

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type postgresBackend struct{ pool *pgxpool.Pool }

func newPostgresBackend(ctx context.Context, dsn string) (*postgresBackend, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres connect: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres ping: %w", err)
	}
	return &postgresBackend{pool: pool}, nil
}

func (b *postgresBackend) Close() { b.pool.Close() }

func pgTimeout() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 5*time.Second)
}

func (b *postgresBackend) put(r Resource) error {
	ctx, cancel := pgTimeout()
	defer cancel()
	_, err := b.pool.Exec(ctx, `
		INSERT INTO fhir_resources (resource_type, id, patient_id, source_facility, body, updated_at)
		VALUES ($1,$2,$3,$4,$5,$6)
		ON CONFLICT (resource_type, id) DO UPDATE SET
			patient_id=EXCLUDED.patient_id, source_facility=EXCLUDED.source_facility,
			body=EXCLUDED.body, updated_at=EXCLUDED.updated_at`,
		r.ResourceType, r.ID, r.PatientID, r.SourceFacility, r.Body, r.UpdatedAt)
	return err
}

func scanResource(row pgx.Row) (*Resource, error) {
	var r Resource
	err := row.Scan(&r.ResourceType, &r.ID, &r.PatientID, &r.SourceFacility, &r.Body, &r.UpdatedAt)
	return &r, err
}

const resourceCols = "resource_type, id, patient_id, source_facility, body, updated_at"

func (b *postgresBackend) get(rt, id string) (*Resource, bool) {
	ctx, cancel := pgTimeout()
	defer cancel()
	r, err := scanResource(b.pool.QueryRow(ctx,
		"SELECT "+resourceCols+" FROM fhir_resources WHERE resource_type=$1 AND id=$2", rt, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false
	}
	if err != nil {
		return nil, false
	}
	return r, true
}

func (b *postgresBackend) forPatient(patientID string) []*Resource {
	ctx, cancel := pgTimeout()
	defer cancel()
	rows, err := b.pool.Query(ctx,
		"SELECT "+resourceCols+" FROM fhir_resources WHERE patient_id=$1 ORDER BY resource_type, id",
		patientID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []*Resource
	for rows.Next() {
		if r, err := scanResource(rows); err == nil {
			out = append(out, r)
		}
	}
	return out
}

func (b *postgresBackend) appendAudit(e AuditEntry) {
	ctx, cancel := pgTimeout()
	defer cancel()
	b.pool.Exec(ctx, `
		INSERT INTO shr_audit (at, actor, patient_id, resource, decision, basis)
		VALUES ($1,$2,$3,$4,$5,$6)`,
		e.At, e.Actor, e.PatientID, e.Resource, e.Decision, e.Basis)
}

func (b *postgresBackend) auditLog() []AuditEntry {
	ctx, cancel := pgTimeout()
	defer cancel()
	rows, err := b.pool.Query(ctx,
		"SELECT at, actor, patient_id, resource, decision, basis FROM shr_audit ORDER BY seq")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []AuditEntry
	for rows.Next() {
		var e AuditEntry
		var at time.Time
		if rows.Scan(&at, &e.Actor, &e.PatientID, &e.Resource, &e.Decision, &e.Basis) == nil {
			e.At = at.UTC().Format(time.RFC3339)
			out = append(out, e)
		}
	}
	return out
}

var _ backend = (*postgresBackend)(nil)
