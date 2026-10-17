// Postgres-backed ingest store — the same Store seam as MemoryStore, with
// the property the whole sync design rests on: exactly-once survives
// restarts. The primary key on sync_events.id IS the dedup set; a replayed
// batch after a crash re-hits the constraint and reports as duplicate,
// not as new data.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// PostgresStore implements Store on Postgres (state plane).
type PostgresStore struct {
	pool *pgxpool.Pool
	// received counts every Apply attempt this process saw (the in-memory
	// counter — the durable record is the table itself).
	received   atomic.Int64
	duplicates atomic.Int64
}

func NewPostgresStore(ctx context.Context, dsn string) (*PostgresStore, error) {
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

func pgTimeout() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 5*time.Second)
}

func (p *PostgresStore) Seen(id string) bool {
	ctx, cancel := pgTimeout()
	defer cancel()
	var one int
	err := p.pool.QueryRow(ctx, "SELECT 1 FROM sync_events WHERE id=$1", id).Scan(&one)
	return err == nil
}

func (p *PostgresStore) Apply(ev Event) bool {
	p.received.Add(1)
	ctx, cancel := pgTimeout()
	defer cancel()
	payload, err := json.Marshal(ev.Payload)
	if err != nil {
		payload = []byte("{}")
	}
	tag, err := p.pool.Exec(ctx, `
		INSERT INTO sync_events (id, facility, type, ts, payload)
		VALUES ($1,$2,$3,$4,$5) ON CONFLICT (id) DO NOTHING`,
		ev.ID, ev.Facility, ev.Type, ev.TS, payload)
	if err != nil {
		return false
	}
	if tag.RowsAffected() == 0 {
		p.duplicates.Add(1)
		return false
	}
	return true
}

func (p *PostgresStore) Stats() (int, int, int) {
	ctx, cancel := pgTimeout()
	defer cancel()
	var applied int
	if err := p.pool.QueryRow(ctx, "SELECT count(*) FROM sync_events").Scan(&applied); err != nil {
		applied = -1
	}
	return int(p.received.Load()), applied, int(p.duplicates.Load())
}

func (p *PostgresStore) Events() []Event {
	ctx, cancel := pgTimeout()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		"SELECT id, facility, type, ts FROM sync_events ORDER BY received_at")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Event
	for rows.Next() {
		var ev Event
		var ts time.Time
		if rows.Scan(&ev.ID, &ev.Facility, &ev.Type, &ts) == nil {
			ev.TS = ts.Format(time.RFC3339)
			out = append(out, ev)
		}
	}
	return out
}

var _ Store = (*PostgresStore)(nil)
