// Postgres-backed tuple store — the state-plane binding (migration
// 0009_rebac.sql). Semantics match MemoryStore exactly: upsert on grant,
// expired tuples invisible to Check/List, sorted list reads.
package rebac

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

const pgTimeout = 5 * time.Second

func pgCtx() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), pgTimeout)
}

// PostgresStore implements Store on Postgres.
type PostgresStore struct{ pool *pgxpool.Pool }

// NewPostgresStore connects and pings — fail fast at boot, not on first
// request, if the database is unreachable.
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

// NewPostgresStoreFromPool binds the store to an existing pool — services
// that already hold a state-plane connection share it.
func NewPostgresStoreFromPool(pool *pgxpool.Pool) *PostgresStore {
	return &PostgresStore{pool: pool}
}

func (p *PostgresStore) Close() { p.pool.Close() }

func (p *PostgresStore) Grant(t Tuple) error {
	if !t.valid() {
		return ErrInvalid
	}
	ctx, cancel := pgCtx()
	defer cancel()
	_, err := p.pool.Exec(ctx, `
		INSERT INTO rebac_tuples
		    (subject_type, subject_id, relation, object_type, object_id, granted_by, expires_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (subject_type, subject_id, relation, object_type, object_id)
		DO UPDATE SET granted_by = EXCLUDED.granted_by, expires_at = EXCLUDED.expires_at`,
		t.Subject.Type, t.Subject.ID, t.Relation, t.Object.Type, t.Object.ID,
		t.GrantedBy, t.ExpiresAt)
	return err
}

func (p *PostgresStore) Revoke(subject Entity, relation string, object Entity) error {
	ctx, cancel := pgCtx()
	defer cancel()
	_, err := p.pool.Exec(ctx, `
		DELETE FROM rebac_tuples
		WHERE subject_type=$1 AND subject_id=$2 AND relation=$3
		  AND object_type=$4 AND object_id=$5`,
		subject.Type, subject.ID, relation, object.Type, object.ID)
	return err
}

func (p *PostgresStore) Check(subject Entity, relation string, object Entity) (bool, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	var n int
	err := p.pool.QueryRow(ctx, `
		SELECT count(1) FROM rebac_tuples
		WHERE subject_type=$1 AND subject_id=$2 AND relation=$3
		  AND object_type=$4 AND object_id=$5
		  AND (expires_at IS NULL OR expires_at > now())`,
		subject.Type, subject.ID, relation, object.Type, object.ID).Scan(&n)
	return n > 0, err
}

func (p *PostgresStore) List(object Entity) ([]Tuple, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx, `
		SELECT subject_type, subject_id, relation, granted_by, expires_at
		FROM rebac_tuples
		WHERE object_type=$1 AND object_id=$2
		  AND (expires_at IS NULL OR expires_at > now())
		ORDER BY subject_type, subject_id, relation`,
		object.Type, object.ID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Tuple
	for rows.Next() {
		var t Tuple
		t.Object = object
		if err := rows.Scan(&t.Subject.Type, &t.Subject.ID, &t.Relation,
			&t.GrantedBy, &t.ExpiresAt); err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}
