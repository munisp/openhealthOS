// Postgres-backed pharmacy store — same Store seam as MemoryStore.
// Dispense runs in a transaction with a guarded UPDATE on the request
// status and a conditional stock decrement, so two pharmacists cannot
// dispense the same request twice and stock cannot go negative.
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

func (p *PostgresStore) UpsertStock(facilityID, drugCode string, delta, reorderLevel int) (StockLevel, error) {
	ctx, cancel := pgctx()
	defer cancel()
	var s StockLevel
	err := p.pool.QueryRow(ctx, `
		INSERT INTO pharmacy_stock (facility_id, drug_code, quantity, reorder_level)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (facility_id, drug_code) DO UPDATE
		  SET quantity = pharmacy_stock.quantity + $3,
		      reorder_level = GREATEST(pharmacy_stock.reorder_level, $4)
		RETURNING facility_id, drug_code, quantity, reorder_level`,
		facilityID, drugCode, delta, reorderLevel).
		Scan(&s.FacilityID, &s.DrugCode, &s.Quantity, &s.ReorderLevel)
	if err != nil {
		return StockLevel{}, err
	}
	if s.Quantity < 0 { // belt-and-braces; CHECK constraint also enforces
		return StockLevel{}, ErrOutOfStock
	}
	return s, nil
}

func (p *PostgresStore) Stock(facilityID string) []StockLevel {
	ctx, cancel := pgctx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		`SELECT facility_id, drug_code, quantity, reorder_level FROM pharmacy_stock WHERE facility_id=$1 ORDER BY drug_code`, facilityID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []StockLevel{}
	for rows.Next() {
		var s StockLevel
		if rows.Scan(&s.FacilityID, &s.DrugCode, &s.Quantity, &s.ReorderLevel) == nil {
			out = append(out, s)
		}
	}
	return out
}

func (p *PostgresStore) StockLevel(facilityID, drugCode string) (StockLevel, error) {
	ctx, cancel := pgctx()
	defer cancel()
	var s StockLevel
	err := p.pool.QueryRow(ctx,
		`SELECT facility_id, drug_code, quantity, reorder_level FROM pharmacy_stock WHERE facility_id=$1 AND drug_code=$2`,
		facilityID, drugCode).Scan(&s.FacilityID, &s.DrugCode, &s.Quantity, &s.ReorderLevel)
	if errors.Is(err, pgx.ErrNoRows) {
		return StockLevel{}, ErrNotFound
	}
	return s, err
}

func (p *PostgresStore) PutRequest(r DispenseRequest) error {
	ctx, cancel := pgctx()
	defer cancel()
	_, err := p.pool.Exec(ctx, `
		INSERT INTO dispense_requests
		  (id, prescription_id, patient_id, facility_id, drug_code, dose, quantity,
		   prescriber, allergies, status, reject_reason, created_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)`,
		r.ID, r.PrescriptionID, r.PatientID, r.FacilityID, r.DrugCode, r.Dose, r.Quantity,
		r.Prescriber, r.Allergies, r.Status, r.RejectReason, r.CreatedAt)
	return err
}

const requestCols = `id, prescription_id, patient_id, facility_id, drug_code, dose, quantity,
	prescriber, COALESCE(allergies,'{}'), status, COALESCE(reject_reason,''), created_at,
	COALESCE(dispensed_at,''), COALESCE(dispensed_by,'')`

func scanRequest(row pgx.Row) (DispenseRequest, error) {
	var r DispenseRequest
	err := row.Scan(&r.ID, &r.PrescriptionID, &r.PatientID, &r.FacilityID, &r.DrugCode,
		&r.Dose, &r.Quantity, &r.Prescriber, &r.Allergies, &r.Status, &r.RejectReason,
		&r.CreatedAt, &r.DispensedAt, &r.DispensedBy)
	return r, err
}

func (p *PostgresStore) GetRequest(id string) (DispenseRequest, error) {
	ctx, cancel := pgctx()
	defer cancel()
	r, err := scanRequest(p.pool.QueryRow(ctx,
		`SELECT `+requestCols+` FROM dispense_requests WHERE id=$1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return DispenseRequest{}, ErrNotFound
	}
	return r, err
}

func (p *PostgresStore) Requests(facilityID, status string) []DispenseRequest {
	ctx, cancel := pgctx()
	defer cancel()
	q := `SELECT ` + requestCols + ` FROM dispense_requests WHERE ($1='' OR facility_id=$1) AND ($2='' OR status=$2) ORDER BY id`
	rows, err := p.pool.Query(ctx, q, facilityID, status)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []DispenseRequest{}
	for rows.Next() {
		var r DispenseRequest
		if err := rows.Scan(&r.ID, &r.PrescriptionID, &r.PatientID, &r.FacilityID, &r.DrugCode,
			&r.Dose, &r.Quantity, &r.Prescriber, &r.Allergies, &r.Status, &r.RejectReason,
			&r.CreatedAt, &r.DispensedAt, &r.DispensedBy); err == nil {
			out = append(out, r)
		}
	}
	return out
}

// Dispense: one transaction, three guarded writes — the request flips
// only from pending, stock decrements only if sufficient, the claim
// draft lands in the same commit.
func (p *PostgresStore) Dispense(requestID, staffID string, at time.Time, draft ClaimDraft) error {
	ctx, cancel := pgctx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var req DispenseRequest
	err = tx.QueryRow(ctx,
		`SELECT `+requestCols+` FROM dispense_requests WHERE id=$1 FOR UPDATE`, requestID).
		Scan(&req.ID, &req.PrescriptionID, &req.PatientID, &req.FacilityID, &req.DrugCode,
			&req.Dose, &req.Quantity, &req.Prescriber, &req.Allergies, &req.Status,
			&req.RejectReason, &req.CreatedAt, &req.DispensedAt, &req.DispensedBy)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if req.Status != "pending" {
		return ErrBadTransition
	}

	tag, err := tx.Exec(ctx, `
		UPDATE pharmacy_stock SET quantity = quantity - $3
		WHERE facility_id=$1 AND drug_code=$2 AND quantity >= $3`,
		req.FacilityID, req.DrugCode, req.Quantity)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrOutOfStock
	}

	if _, err := tx.Exec(ctx, `
		UPDATE dispense_requests SET status='dispensed', dispensed_by=$2, dispensed_at=$3
		WHERE id=$1 AND status='pending'`, requestID, staffID, at.UTC().Format(time.RFC3339)); err != nil {
		return err
	}

	if _, err := tx.Exec(ctx, `
		INSERT INTO claim_drafts
		  (id, dispense_id, facility_id, patient_id, item_code, quantity, unit_price_ngn, total_ngn, status, created_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		draft.ID, draft.DispenseID, draft.FacilityID, draft.PatientID, draft.ItemCode,
		draft.Quantity, draft.UnitPriceNGN, draft.TotalNGN, draft.Status, draft.CreatedAt); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (p *PostgresStore) PutDraft(d ClaimDraft) error {
	ctx, cancel := pgctx()
	defer cancel()
	_, err := p.pool.Exec(ctx, `
		INSERT INTO claim_drafts
		  (id, dispense_id, facility_id, patient_id, item_code, quantity, unit_price_ngn, total_ngn, status, created_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		d.ID, d.DispenseID, d.FacilityID, d.PatientID, d.ItemCode,
		d.Quantity, d.UnitPriceNGN, d.TotalNGN, d.Status, d.CreatedAt)
	return err
}

func (p *PostgresStore) Drafts(facilityID, status string) []ClaimDraft {
	ctx, cancel := pgctx()
	defer cancel()
	rows, err := p.pool.Query(ctx, `
		SELECT id, dispense_id, facility_id, patient_id, item_code, quantity,
		       unit_price_ngn, total_ngn, status, created_at
		FROM claim_drafts WHERE ($1='' OR facility_id=$1) AND ($2='' OR status=$2) ORDER BY id`,
		facilityID, status)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []ClaimDraft{}
	for rows.Next() {
		var d ClaimDraft
		if err := rows.Scan(&d.ID, &d.DispenseID, &d.FacilityID, &d.PatientID, &d.ItemCode,
			&d.Quantity, &d.UnitPriceNGN, &d.TotalNGN, &d.Status, &d.CreatedAt); err == nil {
			out = append(out, d)
		}
	}
	return out
}

func (p *PostgresStore) MarkDraftSubmitted(id string) error {
	ctx, cancel := pgctx()
	defer cancel()
	tag, err := p.pool.Exec(ctx,
		`UPDATE claim_drafts SET status='submitted' WHERE id=$1 AND status='drafted'`, id)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

var _ Store = (*PostgresStore)(nil)
