// Postgres-backed telehealth store — the same TelehealthService seam as
// the in-memory Service. State transitions are enforced by guarded UPDATEs
// (WHERE status=...), so the database itself serializes concurrent
// transitions: two nurses cannot assess-and-close the same consult twice.
package main

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// PostgresService implements TelehealthService on Postgres.
type PostgresService struct{ pool *pgxpool.Pool }

func NewPostgresService(ctx context.Context, dsn string) (*PostgresService, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres connect: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres ping: %w", err)
	}
	return &PostgresService{pool: pool}, nil
}

func (p *PostgresService) Close() { p.pool.Close() }

func pgCtx() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 5*time.Second)
}

func now() string { return time.Now().UTC().Format(time.RFC3339) }

const consultCols = "id, patient_id, facility_id, nurse_id, complaint, protocol, assessment, status, outcome, created_at::text, COALESCE(closed_at::text,'')"

func scanConsult(row pgx.Row) (Consult, error) {
	var c Consult
	err := row.Scan(&c.ID, &c.PatientID, &c.FacilityID, &c.NurseID, &c.Complaint,
		&c.Protocol, &c.Assessment, &c.Status, &c.Outcome, &c.CreatedAt, &c.ClosedAt)
	return c, err
}

func (p *PostgresService) consult(ctx context.Context, id string) (Consult, error) {
	c, err := scanConsult(p.pool.QueryRow(ctx,
		"SELECT "+consultCols+" FROM consults WHERE id=$1", id))
	if errors.Is(err, pgx.ErrNoRows) {
		return Consult{}, ErrNotFound
	}
	return c, err
}

func (p *PostgresService) Open(patientID, facilityID, nurseID, complaint string) Consult {
	ctx, cancel := pgCtx()
	defer cancel()
	var seq int64
	if err := p.pool.QueryRow(ctx, "SELECT nextval('consult_id_seq')").Scan(&seq); err != nil {
		return Consult{Status: "error"}
	}
	id := fmt.Sprintf("CONS-%04d", seq)
	_, err := p.pool.Exec(ctx, `
		INSERT INTO consults (id, patient_id, facility_id, nurse_id, complaint, status)
		VALUES ($1,$2,$3,$4,$5,'open')`, id, patientID, facilityID, nurseID, complaint)
	if err != nil {
		return Consult{Status: "error"}
	}
	c, _ := p.consult(ctx, id)
	return c
}

// guardedUpdate runs UPDATE … WHERE id=$1 AND status=$2, then maps
// zero-rows-affected to the right error (not-found vs bad-transition).
func (p *PostgresService) guardedConsultUpdate(consultID, requiredStatus, setSQL string, args ...any) (Consult, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	all := append([]any{consultID, requiredStatus}, args...)
	tag, err := p.pool.Exec(ctx,
		fmt.Sprintf("UPDATE consults SET %s WHERE id=$1 AND status=$2", setSQL), all...)
	if err != nil {
		return Consult{}, err
	}
	if tag.RowsAffected() == 0 {
		c, err := p.consult(ctx, consultID)
		if err != nil {
			return Consult{}, ErrNotFound
		}
		if c.Status == "open" && requiredStatus == "assessed" {
			return Consult{}, ErrNoAssessment
		}
		return Consult{}, ErrBadTransition
	}
	return p.consult(ctx, consultID)
}

func (p *PostgresService) Assess(consultID, protocol, assessment string) (Consult, error) {
	if protocol == "" || assessment == "" {
		return Consult{}, errors.New("protocol and assessment are required")
	}
	return p.guardedConsultUpdate(consultID, "open",
		"protocol=$3, assessment=$4, status='assessed'", protocol, assessment)
}

func (p *PostgresService) CloseRoutine(consultID string) (Consult, error) {
	return p.guardedConsultUpdate(consultID, "assessed",
		"status='closed', outcome='routine-closure', closed_at=now()")
}

func (p *PostgresService) Escalate(consultID string) (Consult, error) {
	return p.guardedConsultUpdate(consultID, "assessed",
		"status='escalated', outcome='doctor-consult'")
}

func (p *PostgresService) Refer(consultID, toFacility, urgency, reason string) (Referral, error) {
	switch urgency {
	case "routine", "urgent", "emergency":
	default:
		return Referral{}, ErrBadUrgency
	}
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Referral{}, err
	}
	defer tx.Rollback(ctx)
	var c Consult
	var closedAt *string
	err = tx.QueryRow(ctx, `
		SELECT id, patient_id, facility_id, nurse_id, complaint, protocol, assessment, status, outcome, created_at::text, COALESCE(closed_at::text,'')
		FROM consults WHERE id=$1 FOR UPDATE`, consultID).
		Scan(&c.ID, &c.PatientID, &c.FacilityID, &c.NurseID, &c.Complaint,
			&c.Protocol, &c.Assessment, &c.Status, &c.Outcome, &c.CreatedAt, &closedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return Referral{}, ErrNotFound
	}
	if err != nil {
		return Referral{}, err
	}
	if c.Status != "escalated" {
		return Referral{}, ErrBadTransition
	}
	var seq int64
	if err := tx.QueryRow(ctx, "SELECT nextval('referral_id_seq')").Scan(&seq); err != nil {
		return Referral{}, err
	}
	r := Referral{
		ID: fmt.Sprintf("REF-%04d", seq), ConsultID: consultID,
		FromFacility: c.FacilityID, ToFacility: toFacility,
		Urgency: urgency, Reason: reason, Status: "open", CreatedAt: now(),
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO referrals (id, consult_id, from_facility, to_facility, urgency, reason, status)
		VALUES ($1,$2,$3,$4,$5,$6,'open')`,
		r.ID, r.ConsultID, r.FromFacility, r.ToFacility, r.Urgency, r.Reason); err != nil {
		return Referral{}, err
	}
	if _, err := tx.Exec(ctx,
		"UPDATE consults SET outcome='referred' WHERE id=$1", consultID); err != nil {
		return Referral{}, err
	}
	return r, tx.Commit(ctx)
}

func scanReferral(row pgx.Row) (Referral, error) {
	var r Referral
	err := row.Scan(&r.ID, &r.ConsultID, &r.FromFacility, &r.ToFacility,
		&r.Urgency, &r.Reason, &r.Status, &r.CreatedAt)
	return r, err
}

func (p *PostgresService) transitionReferral(referralID, from, to string) (Referral, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	tag, err := p.pool.Exec(ctx,
		"UPDATE referrals SET status=$3 WHERE id=$1 AND status=$2", referralID, from, to)
	if err != nil {
		return Referral{}, err
	}
	if tag.RowsAffected() == 0 {
		var exists int
		err := p.pool.QueryRow(ctx, "SELECT 1 FROM referrals WHERE id=$1", referralID).Scan(&exists)
		if errors.Is(err, pgx.ErrNoRows) {
			return Referral{}, ErrNotFound
		}
		return Referral{}, ErrBadTransition
	}
	return scanReferral(p.pool.QueryRow(ctx, `
		SELECT id, consult_id, from_facility, to_facility, urgency, reason, status, created_at::text
		FROM referrals WHERE id=$1`, referralID))
}

func (p *PostgresService) AcceptReferral(referralID string) (Referral, error) {
	return p.transitionReferral(referralID, "open", "accepted")
}

func (p *PostgresService) CompleteReferral(referralID string) (Referral, error) {
	return p.transitionReferral(referralID, "accepted", "completed")
}

func (p *PostgresService) Prescribe(consultID, prescriber, medication, dose string, quantity int, pharmacyID string) (Prescription, error) {
	if quantity <= 0 {
		return Prescription{}, errors.New("quantity must be positive")
	}
	ctx, cancel := pgCtx()
	defer cancel()
	c, err := p.consult(ctx, consultID)
	if err != nil {
		return Prescription{}, err
	}
	if c.Status != "assessed" && c.Status != "escalated" {
		return Prescription{}, ErrNotPrescribable
	}
	var seq int64
	if err := p.pool.QueryRow(ctx, "SELECT nextval('prescription_id_seq')").Scan(&seq); err != nil {
		return Prescription{}, err
	}
	rx := Prescription{
		ID: fmt.Sprintf("RX-%04d", seq), ConsultID: consultID, Prescriber: prescriber,
		Medication: medication, Dose: dose, Quantity: quantity,
		PharmacyID: pharmacyID, Status: "routed",
	}
	_, err = p.pool.Exec(ctx, `
		INSERT INTO prescriptions (id, consult_id, prescriber, medication, dose, quantity, pharmacy_id, status)
		VALUES ($1,$2,$3,$4,$5,$6,$7,'routed')`,
		rx.ID, rx.ConsultID, rx.Prescriber, rx.Medication, rx.Dose, rx.Quantity, rx.PharmacyID)
	return rx, err
}

func (p *PostgresService) Dispense(prescriptionID string) (Prescription, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	tag, err := p.pool.Exec(ctx,
		"UPDATE prescriptions SET status='dispensed' WHERE id=$1 AND status='routed'", prescriptionID)
	if err != nil {
		return Prescription{}, err
	}
	if tag.RowsAffected() == 0 {
		var exists int
		err := p.pool.QueryRow(ctx, "SELECT 1 FROM prescriptions WHERE id=$1", prescriptionID).Scan(&exists)
		if errors.Is(err, pgx.ErrNoRows) {
			return Prescription{}, ErrNotFound
		}
		return Prescription{}, ErrBadTransition
	}
	var rx Prescription
	err = p.pool.QueryRow(ctx, `
		SELECT id, consult_id, prescriber, medication, dose, quantity, pharmacy_id, status
		FROM prescriptions WHERE id=$1`, prescriptionID).
		Scan(&rx.ID, &rx.ConsultID, &rx.Prescriber, &rx.Medication, &rx.Dose, &rx.Quantity, &rx.PharmacyID, &rx.Status)
	return rx, err
}

// ListPrescriptions supports the pharmacy pull fallback.
func (p *PostgresService) ListPrescriptions(status string) []Prescription {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx, `
		SELECT id, consult_id, prescriber, medication, dose, quantity, pharmacy_id, status
		FROM prescriptions WHERE ($1='' OR status=$1) ORDER BY id`, status)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Prescription{}
	for rows.Next() {
		var rx Prescription
		if err := rows.Scan(&rx.ID, &rx.ConsultID, &rx.Prescriber, &rx.Medication,
			&rx.Dose, &rx.Quantity, &rx.PharmacyID, &rx.Status); err == nil {
			out = append(out, rx)
		}
	}
	return out
}

var _ TelehealthService = (*PostgresService)(nil)
