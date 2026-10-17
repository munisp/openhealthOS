// Postgres DocStore: replicated docs survive restarts. Merges run in a
// transaction with SELECT ... FOR UPDATE so two concurrent edge uploads of
// the same entity serialise instead of lost-updating.
package main

import (
	"encoding/json"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/munisp/openhealthOS/shared/crdt"
)

func (p *PostgresStore) LoadDoc(id string) (*crdt.Doc, bool) {
	ctx, cancel := pgTimeout()
	defer cancel()
	var raw []byte
	err := p.pool.QueryRow(ctx, "SELECT body FROM sync_docs WHERE id=$1", id).Scan(&raw)
	if err != nil {
		return nil, false
	}
	var d crdt.Doc
	if json.Unmarshal(raw, &d) != nil {
		return nil, false
	}
	return &d, true
}

func (p *PostgresStore) StoreDoc(d *crdt.Doc) {
	ctx, cancel := pgTimeout()
	defer cancel()
	raw, err := json.Marshal(d)
	if err != nil {
		return
	}
	_, _ = p.pool.Exec(ctx, `INSERT INTO sync_docs (id, type, body) VALUES ($1,$2,$3)
		ON CONFLICT (id) DO UPDATE SET body=$3, updated=now()`, d.ID, d.Type, raw)
}

func (p *PostgresStore) ListDocs() []*crdt.Doc {
	ctx, cancel := pgTimeout()
	defer cancel()
	rows, err := p.pool.Query(ctx, "SELECT body FROM sync_docs ORDER BY id")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []*crdt.Doc
	for rows.Next() {
		var raw []byte
		if rows.Scan(&raw) != nil {
			continue
		}
		var d crdt.Doc
		if json.Unmarshal(raw, &d) == nil {
			out = append(out, &d)
		}
	}
	return out
}

// MergeBatchTx is the Postgres merge path: per-doc read-modify-write in a
// FOR UPDATE transaction — concurrent uploads of the same entity serialise.
func (p *PostgresStore) MergeBatchTx(b *crdt.Batch) (int, error) {
	ctx, cancel := pgTimeout()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx)
	merged := 0
	for _, remote := range b.Docs {
		if remote == nil || remote.ID == "" {
			continue
		}
		var raw []byte
		var local *crdt.Doc
		err := tx.QueryRow(ctx, "SELECT body FROM sync_docs WHERE id=$1 FOR UPDATE", remote.ID).Scan(&raw)
		if err == nil {
			var d crdt.Doc
			if json.Unmarshal(raw, &d) == nil {
				local = &d
			}
		} else if err != pgx.ErrNoRows {
			return merged, fmt.Errorf("load %s: %w", remote.ID, err)
		}
		m := crdt.MergeDocs(local, remote)
		mraw, err := json.Marshal(m)
		if err != nil {
			return merged, err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO sync_docs (id, type, body) VALUES ($1,$2,$3)
			ON CONFLICT (id) DO UPDATE SET body=$3, updated=now()`, m.ID, m.Type, mraw); err != nil {
			return merged, err
		}
		merged++
	}
	return merged, tx.Commit(ctx)
}
