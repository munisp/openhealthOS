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
		`INSERT INTO notifications (id, patient_id, phone, kind, text, state, attempts, created_at)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
		n.ID, n.PatientID, n.Phone, n.Kind, n.Text, n.State, n.Attempts, n.CreatedAt)
	return err
}

const notCols = `id, patient_id, phone, kind, text, state, attempts,
                 COALESCE(last_error,''), created_at, COALESCE(sent_at,'')`

func scanNot(row pgx.Row) (Notification, error) {
	var n Notification
	err := row.Scan(&n.ID, &n.PatientID, &n.Phone, &n.Kind, &n.Text,
		&n.State, &n.Attempts, &n.LastError, &n.CreatedAt, &n.SentAt)
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
