package main

// Postgres idempotency store (state plane, ADR-0013): the ref → tx record
// survives pod restarts, and the PRIMARY KEY makes a racing duplicate Put
// a constraint violation instead of a double-spend.

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const pgTimeout = 5 * time.Second

type PostgresIdemStore struct{ pool *pgxpool.Pool }

func NewPostgresIdemStore(pool *pgxpool.Pool) *PostgresIdemStore {
	return &PostgresIdemStore{pool: pool}
}

func (s *PostgresIdemStore) Get(ref string) (string, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), pgTimeout)
	defer cancel()
	var tx string
	err := s.pool.QueryRow(ctx, "SELECT tx FROM wallet_idem WHERE ref=$1", ref).Scan(&tx)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", false
	}
	if err != nil {
		return "", false
	}
	return tx, true
}

func (s *PostgresIdemStore) Put(ref, tx string) error {
	ctx, cancel := context.WithTimeout(context.Background(), pgTimeout)
	defer cancel()
	_, err := s.pool.Exec(ctx, "INSERT INTO wallet_idem (ref, tx) VALUES ($1, $2)", ref, tx)
	return err
}

var _ IdemStore = (*PostgresIdemStore)(nil)
