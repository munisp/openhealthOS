package main

// Postgres IPD store — the state plane for wards, beds and ADT. Every
// multi-row invariant (bed exclusivity, one-open-admission, atomic
// transfer) runs inside a transaction with row locks, matching the
// memory store's semantics exactly.

import (
	"context"
	"encoding/json"
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

func pgCtx() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 5*time.Second)
}

func (p *PostgresStore) PutWard(w Ward) error {
	if w.ID == "" || w.FacilityID == "" || w.Name == "" {
		return ErrBadWard
	}
	ctx, cancel := pgCtx()
	defer cancel()
	_, err := p.pool.Exec(ctx, `
		INSERT INTO ipd_wards (id, facility_id, name, kind) VALUES ($1,$2,$3,$4)
		ON CONFLICT (id) DO UPDATE SET facility_id=$2, name=$3, kind=$4`,
		w.ID, w.FacilityID, w.Name, w.Kind)
	return err
}

func (p *PostgresStore) Ward(id string) (Ward, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	var w Ward
	err := p.pool.QueryRow(ctx,
		"SELECT id, facility_id, name, kind FROM ipd_wards WHERE id=$1", id).
		Scan(&w.ID, &w.FacilityID, &w.Name, &w.Kind)
	if errors.Is(err, pgx.ErrNoRows) {
		return Ward{}, ErrNotFound
	}
	return w, err
}

func (p *PostgresStore) Wards(facilityID string) []Ward {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		"SELECT id, facility_id, name, kind FROM ipd_wards WHERE ($1='' OR facility_id=$1) ORDER BY id", facilityID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Ward{}
	for rows.Next() {
		var w Ward
		if rows.Scan(&w.ID, &w.FacilityID, &w.Name, &w.Kind) == nil {
			out = append(out, w)
		}
	}
	return out
}

func (p *PostgresStore) PutBed(b Bed) error {
	if _, err := p.Ward(b.WardID); err != nil {
		return err
	}
	if b.State == "" {
		b.State = BedFree
	}
	ctx, cancel := pgCtx()
	defer cancel()
	_, err := p.pool.Exec(ctx, `
		INSERT INTO ipd_beds (id, ward_id, label, state) VALUES ($1,$2,$3,$4)
		ON CONFLICT (id) DO UPDATE SET ward_id=$2, label=$3, state=$4`,
		b.ID, b.WardID, b.Label, b.State)
	return err
}

func (p *PostgresStore) Bed(id string) (Bed, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	var b Bed
	err := p.pool.QueryRow(ctx,
		"SELECT id, ward_id, label, state FROM ipd_beds WHERE id=$1", id).
		Scan(&b.ID, &b.WardID, &b.Label, &b.State)
	if errors.Is(err, pgx.ErrNoRows) {
		return Bed{}, ErrNotFound
	}
	return b, err
}

func (p *PostgresStore) Beds(wardID string) []Bed {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		"SELECT id, ward_id, label, state FROM ipd_beds WHERE ward_id=$1 ORDER BY id", wardID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Bed{}
	for rows.Next() {
		var b Bed
		if rows.Scan(&b.ID, &b.WardID, &b.Label, &b.State) == nil {
			out = append(out, b)
		}
	}
	return out
}

const admCols = "id, patient_id, facility_id, ward_id, bed_id, reason, status, admitted_at, discharged_at, summary, moves"

func scanAdmission(row pgx.Row) (Admission, error) {
	var a Admission
	var admitted time.Time
	var discharged *time.Time
	var moves []byte
	err := row.Scan(&a.ID, &a.PatientID, &a.FacilityID, &a.WardID, &a.BedID,
		&a.Reason, &a.Status, &admitted, &discharged, &a.Summary, &moves)
	if err != nil {
		return Admission{}, err
	}
	a.AdmittedAt = admitted.UTC().Format(time.RFC3339)
	if discharged != nil {
		a.DischargedAt = discharged.UTC().Format(time.RFC3339)
	}
	a.Moves = []Move{}
	json.Unmarshal(moves, &a.Moves)
	return a, nil
}

func (p *PostgresStore) Admission(id string) (Admission, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	a, err := scanAdmission(p.pool.QueryRow(ctx,
		"SELECT "+admCols+" FROM ipd_admissions WHERE id=$1", id))
	if errors.Is(err, pgx.ErrNoRows) {
		return Admission{}, ErrNotFound
	}
	return a, err
}

func (p *PostgresStore) OpenAdmission(patientID string) (Admission, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	a, err := scanAdmission(p.pool.QueryRow(ctx,
		"SELECT "+admCols+" FROM ipd_admissions WHERE patient_id=$1 AND status='admitted'", patientID))
	if errors.Is(err, pgx.ErrNoRows) {
		return Admission{}, ErrNotFound
	}
	return a, err
}

func (p *PostgresStore) Admissions(facilityID, status string) []Admission {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		"SELECT "+admCols+` FROM ipd_admissions
		 WHERE ($1='' OR facility_id=$1) AND ($2='' OR status=$2)
		 ORDER BY admitted_at DESC`, facilityID, status)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Admission{}
	for rows.Next() {
		var a Admission
		var admitted time.Time
		var discharged *time.Time
		var moves []byte
		if rows.Scan(&a.ID, &a.PatientID, &a.FacilityID, &a.WardID, &a.BedID,
			&a.Reason, &a.Status, &admitted, &discharged, &a.Summary, &moves) != nil {
			continue
		}
		a.AdmittedAt = admitted.UTC().Format(time.RFC3339)
		if discharged != nil {
			a.DischargedAt = discharged.UTC().Format(time.RFC3339)
		}
		a.Moves = []Move{}
		json.Unmarshal(moves, &a.Moves)
		out = append(out, a)
	}
	return out
}

func (p *PostgresStore) NextID() string {
	ctx, cancel := pgCtx()
	defer cancel()
	var n int64
	if err := p.pool.QueryRow(ctx, `
		INSERT INTO ipd_seq (name, value) VALUES ('adm', 1)
		ON CONFLICT (name) DO UPDATE SET value = ipd_seq.value + 1
		RETURNING value`).Scan(&n); err != nil {
		return "ADM-ERR"
	}
	return "ADM-" + itoa4(int(n))
}

func (p *PostgresStore) Admit(a Admission) error {
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	// Lock the bed row: two concurrent admits to one bed serialise here.
	var state string
	err = tx.QueryRow(ctx, "SELECT state FROM ipd_beds WHERE id=$1 FOR UPDATE", a.BedID).Scan(&state)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if state != BedFree {
		return ErrBedOccupied
	}
	var wardOK bool
	if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM ipd_wards WHERE id=$1)", a.WardID).Scan(&wardOK); err != nil || !wardOK {
		return ErrNotFound
	}
	var open bool
	if err := tx.QueryRow(ctx,
		"SELECT EXISTS(SELECT 1 FROM ipd_admissions WHERE patient_id=$1 AND status='admitted')",
		a.PatientID).Scan(&open); err != nil {
		return err
	}
	if open {
		return ErrAlreadyAdmitted
	}
	if _, err := tx.Exec(ctx, "UPDATE ipd_beds SET state='occupied' WHERE id=$1", a.BedID); err != nil {
		return err
	}
	moves, _ := json.Marshal([]Move{})
	if _, err := tx.Exec(ctx, `
		INSERT INTO ipd_admissions (id, patient_id, facility_id, ward_id, bed_id, reason, status, admitted_at, moves)
		VALUES ($1,$2,$3,$4,$5,$6,'admitted',$7,$8)`,
		a.ID, a.PatientID, a.FacilityID, a.WardID, a.BedID, a.Reason, a.AdmittedAt, moves); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (p *PostgresStore) Transfer(id, toBedID, at string) (Admission, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Admission{}, err
	}
	defer tx.Rollback(ctx)
	var a Admission
	var admitted time.Time
	var moves []byte
	err = tx.QueryRow(ctx,
		"SELECT "+admCols+" FROM ipd_admissions WHERE id=$1 FOR UPDATE", id).
		Scan(&a.ID, &a.PatientID, &a.FacilityID, &a.WardID, &a.BedID,
			&a.Reason, &a.Status, &admitted, new(*time.Time), &a.Summary, &moves)
	if errors.Is(err, pgx.ErrNoRows) {
		return Admission{}, ErrNotFound
	}
	if err != nil {
		return Admission{}, err
	}
	if a.Status != AdmOpen {
		return Admission{}, ErrNotAdmitted
	}
	var state string
	err = tx.QueryRow(ctx, "SELECT state FROM ipd_beds WHERE id=$1 FOR UPDATE", toBedID).Scan(&state)
	if errors.Is(err, pgx.ErrNoRows) {
		return Admission{}, ErrNotFound
	}
	if state != BedFree {
		return Admission{}, ErrBedOccupied
	}
	a.Moves = []Move{}
	json.Unmarshal(moves, &a.Moves)
	a.Moves = append(a.Moves, Move{FromBedID: a.BedID, ToBedID: toBedID, At: at})
	newMoves, _ := json.Marshal(a.Moves)
	// Destination first, source second — same order as the memory store.
	if _, err := tx.Exec(ctx, "UPDATE ipd_beds SET state='occupied' WHERE id=$1", toBedID); err != nil {
		return Admission{}, err
	}
	if _, err := tx.Exec(ctx, "UPDATE ipd_beds SET state='cleaning' WHERE id=$1", a.BedID); err != nil {
		return Admission{}, err
	}
	if _, err := tx.Exec(ctx, "UPDATE ipd_admissions SET bed_id=$2, moves=$3 WHERE id=$1",
		id, toBedID, newMoves); err != nil {
		return Admission{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return Admission{}, err
	}
	a.BedID = toBedID
	a.AdmittedAt = admitted.UTC().Format(time.RFC3339)
	return a, nil
}

func (p *PostgresStore) Discharge(id, at, summary string) (Admission, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Admission{}, err
	}
	defer tx.Rollback(ctx)
	var status, bedID string
	err = tx.QueryRow(ctx,
		"SELECT status, bed_id FROM ipd_admissions WHERE id=$1 FOR UPDATE", id).Scan(&status, &bedID)
	if errors.Is(err, pgx.ErrNoRows) {
		return Admission{}, ErrNotFound
	}
	if status != AdmOpen {
		return Admission{}, ErrNotAdmitted
	}
	if _, err := tx.Exec(ctx,
		"UPDATE ipd_admissions SET status='discharged', discharged_at=$2, summary=$3 WHERE id=$1",
		id, at, summary); err != nil {
		return Admission{}, err
	}
	if _, err := tx.Exec(ctx, "UPDATE ipd_beds SET state='cleaning' WHERE id=$1", bedID); err != nil {
		return Admission{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return Admission{}, err
	}
	return p.Admission(id)
}

func (p *PostgresStore) SetBedState(id, state string) error {
	ctx, cancel := pgCtx()
	defer cancel()
	tag, err := p.pool.Exec(ctx, "UPDATE ipd_beds SET state=$2 WHERE id=$1", id, state)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}
