// Postgres-backed notification store — same Store seam as MemoryStore.
// Delivery states flip via guarded UPDATEs so two drain workers cannot
// double-send the same message.
package main

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PostgresStore struct{ pool *pgxpool.Pool }

func NewPostgresStore(dsn string) (*PostgresStore, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres connect: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres ping: %w", err)
	}
	// Artifact columns (media bridge): idempotent, nullable — old rows
	// simply carry no artifact.
	for _, ddl := range []string{
		`ALTER TABLE notifications ADD COLUMN IF NOT EXISTS blob_ref text`,
		`ALTER TABLE notifications ADD COLUMN IF NOT EXISTS mime text`,
	} {
		if _, err := pool.Exec(ctx, ddl); err != nil {
			pool.Close()
			return nil, fmt.Errorf("postgres migrate: %w", err)
		}
	}
	return &PostgresStore{pool: pool}, nil
}

func (p *PostgresStore) Close() { p.pool.Close() }

func pgctx() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 5*time.Second)
}

func (p *PostgresStore) PutSubscription(s Subscription) error {
	ctx, cancel := pgctx()
	defer cancel()
	_, err := p.pool.Exec(ctx,
		`INSERT INTO notification_subscriptions (patient_id, phone, language, facility_id, updated_at)
		 VALUES ($1,$2,$3,$4,$5)
		 ON CONFLICT (patient_id) DO UPDATE SET
		   phone=EXCLUDED.phone, language=EXCLUDED.language,
		   facility_id=EXCLUDED.facility_id, updated_at=EXCLUDED.updated_at`,
		s.PatientID, s.Phone, s.Language, s.Facility, s.UpdatedAt)
	return err
}

func (p *PostgresStore) Subscription(patientID string) (Subscription, error) {
	ctx, cancel := pgctx()
	defer cancel()
	var s Subscription
	err := p.pool.QueryRow(ctx,
		`SELECT patient_id, phone, language, facility_id, updated_at
		 FROM notification_subscriptions WHERE patient_id=$1`, patientID).
		Scan(&s.PatientID, &s.Phone, &s.Language, &s.Facility, &s.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return Subscription{}, ErrNotFound
	}
	return s, err
}

func (p *PostgresStore) Subscriptions() []Subscription {
	ctx, cancel := pgctx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		`SELECT patient_id, phone, language, facility_id, updated_at
		 FROM notification_subscriptions ORDER BY patient_id`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Subscription
	for rows.Next() {
		var s Subscription
		if rows.Scan(&s.PatientID, &s.Phone, &s.Language, &s.Facility, &s.UpdatedAt) == nil {
			out = append(out, s)
		}
	}
	return out
}

func (p *PostgresStore) Enqueue(n Notification) error {
	ctx, cancel := pgctx()
	defer cancel()
	_, err := p.pool.Exec(ctx,
		`INSERT INTO notifications (id, patient_id, phone, kind, text, state, attempts, created_at, blob_ref, mime)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NULLIF($9,''),NULLIF($10,''))`,
		n.ID, n.PatientID, n.Phone, n.Kind, n.Text, n.State, n.Attempts, n.CreatedAt, n.BlobRef, n.Mime)
	return err
}

const notCols = `id, patient_id, phone, kind, text, state, attempts,
                 COALESCE(last_error,''), created_at, COALESCE(sent_at,''),
                 COALESCE(blob_ref,''), COALESCE(mime,'')`

func scanNot(row pgx.Row) (Notification, error) {
	var n Notification
	err := row.Scan(&n.ID, &n.PatientID, &n.Phone, &n.Kind, &n.Text,
		&n.State, &n.Attempts, &n.LastError, &n.CreatedAt, &n.SentAt,
		&n.BlobRef, &n.Mime)
	return n, err
}

func (p *PostgresStore) Get(id string) (Notification, error) {
	ctx, cancel := pgctx()
	defer cancel()
	n, err := scanNot(p.pool.QueryRow(ctx,
		`SELECT `+notCols+` FROM notifications WHERE id=$1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return Notification{}, ErrNotFound
	}
	return n, err
}

func (p *PostgresStore) MarkSent(id, at string) error {
	ctx, cancel := pgctx()
	defer cancel()
	tag, err := p.pool.Exec(ctx,
		`UPDATE notifications SET state='sent', sent_at=$2 WHERE id=$1 AND state='queued'`, id, at)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrBadTransition
	}
	return nil
}

func (p *PostgresStore) RecordAttempt(id string) (int, error) {
	ctx, cancel := pgctx()
	defer cancel()
	var attempts int
	err := p.pool.QueryRow(ctx,
		`UPDATE notifications SET attempts=attempts+1 WHERE id=$1 RETURNING attempts`, id).
		Scan(&attempts)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, ErrNotFound
	}
	return attempts, err
}

func (p *PostgresStore) MarkFailed(id, reason string) error {
	ctx, cancel := pgctx()
	defer cancel()
	tag, err := p.pool.Exec(ctx,
		`UPDATE notifications SET state='failed', last_error=$2 WHERE id=$1 AND state IN ('queued')`, id, reason)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrBadTransition
	}
	return nil
}

func (p *PostgresStore) Pending(limit int) []Notification {
	ctx, cancel := pgctx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		`SELECT `+notCols+` FROM notifications WHERE state='queued' ORDER BY created_at LIMIT $1`, limit)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Notification
	for rows.Next() {
		if n, err := scanNot(rows); err == nil {
			out = append(out, n)
		}
	}
	return out
}

func (p *PostgresStore) ForPatient(patientID string) []Notification {
	ctx, cancel := pgctx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		`SELECT `+notCols+` FROM notifications WHERE patient_id=$1 ORDER BY created_at DESC`, patientID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Notification
	for rows.Next() {
		if n, err := scanNot(rows); err == nil {
			out = append(out, n)
		}
	}
	return out
}

func (p *PostgresStore) Stats() (queued, sent, failed int) {
	ctx, cancel := pgctx()
	defer cancel()
	_ = p.pool.QueryRow(ctx,
		`SELECT
		   COUNT(*) FILTER (WHERE state='queued'),
		   COUNT(*) FILTER (WHERE state='sent'),
		   COUNT(*) FILTER (WHERE state='failed')
		 FROM notifications`).Scan(&queued, &sent, &failed)
	return
}

var _ Store = (*PostgresStore)(nil)

// ---------- passkeys (ADR-0017) ----------

func (p *PostgresStore) PutPasskey(k PasskeyCredential) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	// Upsert: re-registering the same credential id refreshes the key.
	_, err := p.pool.Exec(ctx, `
		INSERT INTO portal_passkeys (cred_id, patient_id, pub_x, pub_y, sign_count, created_at)
		VALUES ($1, $2, $3, $4, $5, $6)
		ON CONFLICT (cred_id) DO UPDATE
		  SET patient_id=$2, pub_x=$3, pub_y=$4, sign_count=$5`,
		k.CredID, k.PatientID, k.PubX, k.PubY, int(k.SignCount), k.CreatedAt)
	return err
}

func (p *PostgresStore) Passkeys(patientID string) []PasskeyCredential {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	rows, err := p.pool.Query(ctx,
		"SELECT cred_id, patient_id, pub_x, pub_y, sign_count, created_at FROM portal_passkeys WHERE patient_id=$1 ORDER BY cred_id",
		patientID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	return scanPasskeys(rows)
}

func (p *PostgresStore) PasskeyByCredID(credID string) (PasskeyCredential, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var k PasskeyCredential
	var count int
	var created time.Time
	err := p.pool.QueryRow(ctx,
		"SELECT cred_id, patient_id, pub_x, pub_y, sign_count, created_at FROM portal_passkeys WHERE cred_id=$1",
		credID).Scan(&k.CredID, &k.PatientID, &k.PubX, &k.PubY, &count, &created)
	if errors.Is(err, pgx.ErrNoRows) {
		return PasskeyCredential{}, ErrNotFound
	}
	if err != nil {
		return PasskeyCredential{}, err
	}
	k.SignCount = uint32(count)
	k.CreatedAt = created.UTC().Format(time.RFC3339)
	return k, nil
}

func (p *PostgresStore) UpdatePasskeySignCount(credID string, count uint32) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	// Monotonic guard at the storage layer too: a stale write can never
	// roll the counter backwards (clone-detection invariant).
	tag, err := p.pool.Exec(ctx,
		"UPDATE portal_passkeys SET sign_count=$2 WHERE cred_id=$1 AND sign_count < $2",
		credID, int(count))
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		if _, err := p.PasskeyByCredID(credID); err != nil {
			return ErrNotFound
		}
	}
	return nil
}

func scanPasskeys(rows pgx.Rows) []PasskeyCredential {
	out := []PasskeyCredential{}
	for rows.Next() {
		var k PasskeyCredential
		var count int
		var created time.Time
		if err := rows.Scan(&k.CredID, &k.PatientID, &k.PubX, &k.PubY, &count, &created); err != nil {
			continue
		}
		k.SignCount = uint32(count)
		k.CreatedAt = created.UTC().Format(time.RFC3339)
		out = append(out, k)
	}
	return out
}
