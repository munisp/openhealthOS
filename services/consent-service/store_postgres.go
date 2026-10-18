// Postgres-backed consent store — the same ConsentService seam as the
// in-memory Manager, for the state plane. Artefacts, signatures and the
// audit log survive restarts; the HMAC scheme is identical (signWith), so
// an artefact issued before a restart verifies after it, and tampering is
// still detected by recomputation on every check.
package main

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// PostgresManager implements ConsentService on Postgres.
type PostgresManager struct {
	pool *pgxpool.Pool
	key  []byte
}

func NewPostgresManager(ctx context.Context, dsn string, key []byte) (*PostgresManager, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres connect: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres ping: %w", err)
	}
	return &PostgresManager{pool: pool, key: key}, nil
}

func (p *PostgresManager) Close() { p.pool.Close() }

func pgCtx() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 5*time.Second)
}

func now() string { return time.Now().UTC().Format(time.RFC3339) }

func (p *PostgresManager) Issue(patientID, grantee, scope, purpose string) (Consent, error) {
	if patientID == "" || grantee == "" || scope == "" || purpose == "" {
		return Consent{}, ErrInvalid
	}
	ctx, cancel := pgCtx()
	defer cancel()
	var seq int64
	if err := p.pool.QueryRow(ctx, "SELECT nextval('consent_id_seq')").Scan(&seq); err != nil {
		return Consent{}, err
	}
	c := Consent{
		ID: fmt.Sprintf("CNS-%06d", seq), PatientID: patientID, Grantee: grantee,
		Scope: scope, Purpose: purpose, Status: "active", IssuedAt: now(),
	}
	c.Signature = signWith(p.key, &c)
	_, err := p.pool.Exec(ctx, `
		INSERT INTO consents (id, patient_id, grantee, scope, purpose, status, issued_at, signature)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
		c.ID, c.PatientID, c.Grantee, c.Scope, c.Purpose, c.Status, c.IssuedAt, c.Signature)
	if err != nil {
		return Consent{}, err
	}
	p.record("ISSUE", c.ID, fmt.Sprintf("patient=%s grantee=%s scope=%s", patientID, grantee, scope))
	return c, nil
}

func (p *PostgresManager) Revoke(id string) error {
	ctx, cancel := pgCtx()
	defer cancel()
	c, err := p.byID(ctx, id)
	if err != nil {
		return err
	}
	if c.Status == "revoked" {
		return nil // idempotent
	}
	c.Status = "revoked"
	c.RevokedAt = now()
	c.Signature = signWith(p.key, &c)
	_, err = p.pool.Exec(ctx,
		"UPDATE consents SET status='revoked', revoked_at=$2, signature=$3 WHERE id=$1",
		id, c.RevokedAt, c.Signature)
	if err != nil {
		return err
	}
	p.record("REVOKE", id, "")
	return nil
}

func (p *PostgresManager) Check(patientID, grantee, scope string) (bool, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	var c Consent
	var revokedAt *string
	err := p.pool.QueryRow(ctx, `
		SELECT id, patient_id, grantee, scope, purpose, status, issued_at, revoked_at, signature
		FROM consents
		WHERE patient_id=$1 AND grantee=$2 AND scope=$3 AND status='active'
		LIMIT 1`, patientID, grantee, scope).
		Scan(&c.ID, &c.PatientID, &c.Grantee, &c.Scope, &c.Purpose, &c.Status, &c.IssuedAt, &revokedAt, &c.Signature)
	if errors.Is(err, pgx.ErrNoRows) {
		p.record("CHECK", "", fmt.Sprintf("patient=%s grantee=%s scope=%s allowed=false", patientID, grantee, scope))
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if revokedAt != nil {
		c.RevokedAt = *revokedAt
	}
	if !p.Verify(&c) {
		p.record("CHECK", c.ID, "DENIED: tampered artefact")
		return false, ErrTampered
	}
	p.record("CHECK", "", fmt.Sprintf("patient=%s grantee=%s scope=%s allowed=true", patientID, grantee, scope))
	return true, nil
}

func (p *PostgresManager) Verify(c *Consent) bool {
	return c.Signature == signWith(p.key, c)
}

func (p *PostgresManager) Audit() []AuditEntry {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		"SELECT at, event, consent_id, detail FROM consent_audit ORDER BY seq")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []AuditEntry
	for rows.Next() {
		var e AuditEntry
		var at time.Time
		if rows.Scan(&at, &e.Event, &e.ConsentID, &e.Detail) == nil {
			e.TS = at.UTC().Format(time.RFC3339)
			out = append(out, e)
		}
	}
	return out
}

func (p *PostgresManager) byID(ctx context.Context, id string) (Consent, error) {
	var c Consent
	var revokedAt *string
	err := p.pool.QueryRow(ctx, `
		SELECT id, patient_id, grantee, scope, purpose, status, issued_at, revoked_at, signature
		FROM consents WHERE id=$1`, id).
		Scan(&c.ID, &c.PatientID, &c.Grantee, &c.Scope, &c.Purpose, &c.Status, &c.IssuedAt, &revokedAt, &c.Signature)
	if errors.Is(err, pgx.ErrNoRows) {
		return Consent{}, ErrNotFound
	}
	if revokedAt != nil {
		c.RevokedAt = *revokedAt
	}
	return c, err
}

func (p *PostgresManager) record(event, id, detail string) {
	ctx, cancel := pgCtx()
	defer cancel()
	p.pool.Exec(ctx,
		"INSERT INTO consent_audit (event, consent_id, detail) VALUES ($1,$2,$3)",
		event, id, detail)
}

var _ ConsentService = (*PostgresManager)(nil)

// ForPatient lists a patient's consent artefacts, newest first.
func (p *PostgresManager) ForPatient(patientID string) []Consent {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx, `
		SELECT id, patient_id, grantee, scope, purpose, status, issued_at, revoked_at, signature
		FROM consents WHERE patient_id=$1 ORDER BY issued_at DESC`, patientID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Consent{}
	for rows.Next() {
		var c Consent
		var revokedAt *string
		if err := rows.Scan(&c.ID, &c.PatientID, &c.Grantee, &c.Scope, &c.Purpose, &c.Status, &c.IssuedAt, &revokedAt, &c.Signature); err != nil {
			continue
		}
		if revokedAt != nil {
			c.RevokedAt = *revokedAt
		}
		out = append(out, c)
	}
	return out
}
