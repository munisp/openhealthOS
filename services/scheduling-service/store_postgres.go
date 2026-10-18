// store_postgres.go — state-plane persistence (ADR-0017). Booking
// invariants live in single transactions with row locks: a double-book
// is impossible even with two facilities' edge nodes retrying through
// the same state plane.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PostgresStore struct {
	pool *pgxpool.Pool
}

func NewPostgresStore(ctx context.Context, dsn string) (*PostgresStore, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		return nil, err
	}
	return &PostgresStore{pool: pool}, nil
}

// nextID allocates from a durable counter table (Postgres sequences per
// prefix would churn on rollback; this is the telehealth pattern).
func (p *PostgresStore) nextID(ctx context.Context, tx pgx.Tx, prefix string) (string, error) {
	var n int
	err := tx.QueryRow(ctx,
		`INSERT INTO sched_seq (name, value) VALUES ($1, 1)
		 ON CONFLICT (name) DO UPDATE SET value = sched_seq.value + 1
		 RETURNING value`, prefix).Scan(&n)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s-%05d", prefix, n), nil
}

func (p *PostgresStore) PutSchedule(s Schedule) (Schedule, error) {
	if err := validSchedule(s); err != nil {
		return Schedule{}, err
	}
	ctx := context.Background()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Schedule{}, err
	}
	defer tx.Rollback(ctx)
	if s.ID == "" {
		if s.ID, err = p.nextID(ctx, tx, "SCH"); err != nil {
			return Schedule{}, err
		}
	}
	s.Active = true
	_, err = tx.Exec(ctx,
		`INSERT INTO sched_schedules
		 (id, facility_id, resource_id, resource_name, resource_type, service,
		  weekday, start_min, end_min, slot_minutes, capacity, active)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
		 ON CONFLICT (id) DO UPDATE SET
		  resource_name=EXCLUDED.resource_name, capacity=EXCLUDED.capacity`,
		s.ID, s.FacilityID, s.ResourceID, s.ResourceName, s.ResourceType, s.Service,
		s.Weekday, s.StartMin, s.EndMin, s.SlotMinutes, s.Capacity, s.Active)
	if err != nil {
		return Schedule{}, err
	}
	return s, tx.Commit(ctx)
}

func (p *PostgresStore) Schedules(facilityID string) []Schedule {
	rows, err := p.pool.Query(context.Background(),
		`SELECT id, facility_id, resource_id, resource_name, resource_type, service,
		        weekday, start_min, end_min, slot_minutes, capacity, active
		 FROM sched_schedules WHERE facility_id=$1 AND active ORDER BY id`, facilityID)
	if err != nil {
		return []Schedule{}
	}
	defer rows.Close()
	out := []Schedule{}
	for rows.Next() {
		var s Schedule
		if rows.Scan(&s.ID, &s.FacilityID, &s.ResourceID, &s.ResourceName, &s.ResourceType,
			&s.Service, &s.Weekday, &s.StartMin, &s.EndMin, &s.SlotMinutes, &s.Capacity, &s.Active) == nil {
			out = append(out, s)
		}
	}
	return out
}

func (p *PostgresStore) Generate(scheduleID, date string, now time.Time) ([]Slot, error) {
	ctx := context.Background()
	var s Schedule
	err := p.pool.QueryRow(ctx,
		`SELECT id, facility_id, resource_id, resource_name, resource_type, service,
		        weekday, start_min, end_min, slot_minutes, capacity, active
		 FROM sched_schedules WHERE id=$1 AND active`, scheduleID).
		Scan(&s.ID, &s.FacilityID, &s.ResourceID, &s.ResourceName, &s.ResourceType,
			&s.Service, &s.Weekday, &s.StartMin, &s.EndMin, &s.SlotMinutes, &s.Capacity, &s.Active)
	if err != nil {
		return nil, ErrNotFound
	}
	slots, err := generateSlots(&s, date)
	if err != nil {
		return nil, err
	}
	out := []Slot{}
	for _, sl := range slots {
		st, _ := time.Parse(time.RFC3339, sl.Start)
		en, _ := time.Parse(time.RFC3339, sl.End)
		var got Slot
		var gst, gen time.Time
		err := p.pool.QueryRow(ctx,
			`INSERT INTO sched_slots
			 (id, schedule_id, facility_id, service, resource_name, start_ts, end_ts, capacity, booked)
			 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,0)
			 ON CONFLICT (id) DO NOTHING
			 RETURNING id, schedule_id, facility_id, service, resource_name,
			           start_ts, end_ts, capacity, booked`,
			sl.ID, sl.ScheduleID, sl.FacilityID, sl.Service, sl.ResourceName, st, en, sl.Capacity).
			Scan(&got.ID, &got.ScheduleID, &got.FacilityID, &got.Service, &got.ResourceName,
				&gst, &gen, &got.Capacity, &got.Booked)
		if errors.Is(err, pgx.ErrNoRows) {
			// already existed — read the live counts
			err = p.pool.QueryRow(ctx,
				`SELECT id, schedule_id, facility_id, service, resource_name,
				        start_ts, end_ts, capacity, booked
				 FROM sched_slots WHERE id=$1`, sl.ID).
				Scan(&got.ID, &got.ScheduleID, &got.FacilityID, &got.Service, &got.ResourceName,
					&gst, &gen, &got.Capacity, &got.Booked)
		}
		if err != nil {
			return nil, err
		}
		got.Start, got.End = gst.UTC().Format(time.RFC3339), gen.UTC().Format(time.RFC3339)
		out = append(out, got)
	}
	return out, nil
}

const slotCols = "id, schedule_id, facility_id, service, resource_name, start_ts, end_ts, capacity, booked"

func scanSlot(row pgx.Row) (Slot, error) {
	var s Slot
	var st, en time.Time
	err := row.Scan(&s.ID, &s.ScheduleID, &s.FacilityID, &s.Service, &s.ResourceName,
		&st, &en, &s.Capacity, &s.Booked)
	s.Start, s.End = st.UTC().Format(time.RFC3339), en.UTC().Format(time.RFC3339)
	return s, err
}

func (p *PostgresStore) Slots(facilityID, service, from, to string) []Slot {
	q := `SELECT ` + slotCols + ` FROM sched_slots WHERE facility_id=$1`
	args := []any{facilityID}
	n := 1
	if service != "" {
		n++
		q += fmt.Sprintf(" AND service=$%d", n)
		args = append(args, service)
	}
	if from != "" {
		n++
		q += fmt.Sprintf(" AND start_ts >= $%d", n)
		args = append(args, from)
	}
	if to != "" {
		n++
		q += fmt.Sprintf(" AND start_ts <= $%d", n)
		args = append(args, to)
	}
	q += " ORDER BY start_ts"
	rows, err := p.pool.Query(context.Background(), q, args...)
	if err != nil {
		return []Slot{}
	}
	defer rows.Close()
	out := []Slot{}
	for rows.Next() {
		if s, err := scanSlot(rows); err == nil {
			out = append(out, s)
		}
	}
	return out
}

const apptCols = "id, slot_id, patient_id, facility_id, service, resource_name, start_ts, end_ts, status, reason, coverage, created_at"

func scanAppt(row pgx.Row) (Appointment, error) {
	var a Appointment
	var st, en, ca time.Time
	var cov []byte
	err := row.Scan(&a.ID, &a.SlotID, &a.PatientID, &a.FacilityID, &a.Service,
		&a.ResourceName, &st, &en, &a.Status, &a.Reason, &cov, &ca)
	if len(cov) > 0 {
		json.Unmarshal(cov, &a.Coverage)
	}
	a.Start, a.End = st.UTC().Format(time.RFC3339), en.UTC().Format(time.RFC3339)
	a.CreatedAt = ca.UTC().Format(time.RFC3339)
	return a, err
}

func (p *PostgresStore) Book(slotID, patientID, reason string, coverage map[string]any, now time.Time) (Appointment, error) {
	ctx := context.Background()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Appointment{}, err
	}
	defer tx.Rollback(ctx)
	sl, err := scanSlot(tx.QueryRow(ctx,
		`SELECT `+slotCols+` FROM sched_slots WHERE id=$1 FOR UPDATE`, slotID))
	if err != nil {
		return Appointment{}, ErrNotFound
	}
	if err := bookable(&sl, now); err != nil {
		return Appointment{}, err
	}
	id, err := p.nextID(ctx, tx, "APPT")
	if err != nil {
		return Appointment{}, err
	}
	cov, _ := json.Marshal(coverage)
	if _, err = tx.Exec(ctx,
		`INSERT INTO sched_appointments
		 (id, slot_id, patient_id, facility_id, service, resource_name,
		  start_ts, end_ts, status, reason, coverage, created_at)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'booked',$9,$10,$11)`,
		id, slotID, patientID, sl.FacilityID, sl.Service, sl.ResourceName,
		sl.Start, sl.End, reason, cov, now.UTC()); err != nil {
		return Appointment{}, err
	}
	if _, err = tx.Exec(ctx, `UPDATE sched_slots SET booked=booked+1 WHERE id=$1`, slotID); err != nil {
		return Appointment{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return Appointment{}, err
	}
	return p.Appointment(id)
}

func (p *PostgresStore) Cancel(appointmentID string, now time.Time) (Appointment, *Appointment, error) {
	ctx := context.Background()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Appointment{}, nil, err
	}
	defer tx.Rollback(ctx)
	a, err := scanAppt(tx.QueryRow(ctx,
		`SELECT `+apptCols+` FROM sched_appointments WHERE id=$1 FOR UPDATE`, appointmentID))
	if err != nil {
		return Appointment{}, nil, ErrNotFound
	}
	if a.Status != "booked" {
		return Appointment{}, nil, ErrBadTransition
	}
	if _, err = tx.Exec(ctx,
		`UPDATE sched_appointments SET status='cancelled' WHERE id=$1`, a.ID); err != nil {
		return Appointment{}, nil, err
	}
	if _, err = tx.Exec(ctx,
		`UPDATE sched_slots SET booked=GREATEST(booked-1,0) WHERE id=$1`, a.SlotID); err != nil {
		return Appointment{}, nil, err
	}
	// FIFO promotion into the freed slot, inside the same transaction.
	var promoted *Appointment
	var wid, wpid string
	err = tx.QueryRow(ctx,
		`SELECT id, patient_id FROM sched_waitlist
		 WHERE facility_id=$1 AND service=$2
		 ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED`,
		a.FacilityID, a.Service).Scan(&wid, &wpid)
	if err == nil {
		pid, ierr := p.nextID(ctx, tx, "APPT")
		if ierr == nil {
			_, ierr = tx.Exec(ctx,
				`INSERT INTO sched_appointments
				 (id, slot_id, patient_id, facility_id, service, resource_name,
				  start_ts, end_ts, status, reason, coverage, created_at)
				 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'booked','',NULL,$9)`,
				pid, a.SlotID, wpid, a.FacilityID, a.Service, a.ResourceName,
				a.Start, a.End, now.UTC())
		}
		if ierr == nil {
			_, ierr = tx.Exec(ctx, `UPDATE sched_slots SET booked=booked+1 WHERE id=$1`, a.SlotID)
		}
		if ierr == nil {
			_, ierr = tx.Exec(ctx, `DELETE FROM sched_waitlist WHERE id=$1`, wid)
		}
		if ierr != nil {
			return Appointment{}, nil, ierr
		}
		pa, perr := scanAppt(tx.QueryRow(ctx,
			`SELECT `+apptCols+` FROM sched_appointments WHERE id=$1`, pid))
		if perr == nil {
			promoted = &pa
		}
	}
	a.Status = "cancelled"
	if err = tx.Commit(ctx); err != nil {
		return Appointment{}, nil, err
	}
	return a, promoted, nil
}

func (p *PostgresStore) Reschedule(appointmentID, newSlotID string, now time.Time) (Appointment, error) {
	ctx := context.Background()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Appointment{}, err
	}
	defer tx.Rollback(ctx)
	a, err := scanAppt(tx.QueryRow(ctx,
		`SELECT `+apptCols+` FROM sched_appointments WHERE id=$1 FOR UPDATE`, appointmentID))
	if err != nil {
		return Appointment{}, ErrNotFound
	}
	if a.Status != "booked" {
		return Appointment{}, ErrBadTransition
	}
	nl, err := scanSlot(tx.QueryRow(ctx,
		`SELECT `+slotCols+` FROM sched_slots WHERE id=$1 FOR UPDATE`, newSlotID))
	if err != nil {
		return Appointment{}, ErrNotFound
	}
	if err := bookable(&nl, now); err != nil {
		return Appointment{}, err
	}
	// Take the new slot before releasing the old one — atomic.
	if _, err = tx.Exec(ctx, `UPDATE sched_slots SET booked=booked+1 WHERE id=$1`, newSlotID); err != nil {
		return Appointment{}, err
	}
	if _, err = tx.Exec(ctx,
		`UPDATE sched_slots SET booked=GREATEST(booked-1,0) WHERE id=$1`, a.SlotID); err != nil {
		return Appointment{}, err
	}
	if _, err = tx.Exec(ctx,
		`UPDATE sched_appointments SET slot_id=$2, service=$3, resource_name=$4,
		 start_ts=$5, end_ts=$6 WHERE id=$1`,
		appointmentID, newSlotID, nl.Service, nl.ResourceName, nl.Start, nl.End); err != nil {
		return Appointment{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return Appointment{}, err
	}
	return p.Appointment(appointmentID)
}

func (p *PostgresStore) Transition(appointmentID, action string) (Appointment, error) {
	tr, ok := transitions[action]
	if !ok {
		return Appointment{}, ErrBadTransition
	}
	res, err := p.pool.Exec(context.Background(),
		`UPDATE sched_appointments SET status=$2 WHERE id=$1 AND status = ANY($3)`,
		appointmentID, tr.to[0], tr.from)
	if err != nil {
		return Appointment{}, err
	}
	if res.RowsAffected() == 0 {
		if _, err := p.Appointment(appointmentID); err != nil {
			return Appointment{}, ErrNotFound
		}
		return Appointment{}, ErrBadTransition
	}
	return p.Appointment(appointmentID)
}

func (p *PostgresStore) Appointment(id string) (Appointment, error) {
	a, err := scanAppt(p.pool.QueryRow(context.Background(),
		`SELECT `+apptCols+` FROM sched_appointments WHERE id=$1`, id))
	if err != nil {
		return Appointment{}, ErrNotFound
	}
	return a, nil
}

func (p *PostgresStore) PatientAppointments(patientID string) []Appointment {
	return p.queryAppts(
		`SELECT `+apptCols+` FROM sched_appointments WHERE patient_id=$1 ORDER BY start_ts`, patientID)
}

func (p *PostgresStore) DayList(facilityID, date string) []Appointment {
	return p.queryAppts(
		`SELECT `+apptCols+` FROM sched_appointments
		 WHERE facility_id=$1 AND start_ts::date = $2::date AND status <> 'cancelled'
		 ORDER BY start_ts`, facilityID, date)
}

func (p *PostgresStore) queryAppts(q string, args ...any) []Appointment {
	rows, err := p.pool.Query(context.Background(), q, args...)
	if err != nil {
		return []Appointment{}
	}
	defer rows.Close()
	out := []Appointment{}
	for rows.Next() {
		if a, err := scanAppt(rows); err == nil {
			out = append(out, a)
		}
	}
	return out
}

func (p *PostgresStore) Waitlist(patientID, facilityID, service string, now time.Time) (WaitlistEntry, error) {
	if patientID == "" || facilityID == "" || service == "" {
		return WaitlistEntry{}, ErrBadSchedule
	}
	ctx := context.Background()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return WaitlistEntry{}, err
	}
	defer tx.Rollback(ctx)
	id, err := p.nextID(ctx, tx, "WAIT")
	if err != nil {
		return WaitlistEntry{}, err
	}
	var e WaitlistEntry
	var ca time.Time
	err = tx.QueryRow(ctx,
		`INSERT INTO sched_waitlist (id, patient_id, facility_id, service, created_at)
		 VALUES ($1,$2,$3,$4,$5)
		 ON CONFLICT (patient_id, facility_id, service)
		 DO UPDATE SET patient_id=EXCLUDED.patient_id
		 RETURNING id, patient_id, facility_id, service, created_at`,
		id, patientID, facilityID, service, now.UTC()).
		Scan(&e.ID, &e.PatientID, &e.FacilityID, &e.Service, &ca)
	if err != nil {
		return WaitlistEntry{}, err
	}
	e.CreatedAt = ca.UTC().Format(time.RFC3339)
	return e, tx.Commit(ctx)
}

func (p *PostgresStore) WaitlistFor(facilityID, service string) []WaitlistEntry {
	q := `SELECT id, patient_id, facility_id, service, created_at FROM sched_waitlist WHERE facility_id=$1`
	args := []any{facilityID}
	if service != "" {
		q += " AND service=$2"
		args = append(args, service)
	}
	q += " ORDER BY created_at, id"
	rows, err := p.pool.Query(context.Background(), q, args...)
	if err != nil {
		return []WaitlistEntry{}
	}
	defer rows.Close()
	out := []WaitlistEntry{}
	for rows.Next() {
		var e WaitlistEntry
		var ca time.Time
		if rows.Scan(&e.ID, &e.PatientID, &e.FacilityID, &e.Service, &ca) == nil {
			e.CreatedAt = ca.UTC().Format(time.RFC3339)
			out = append(out, e)
		}
	}
	return out
}

var _ Store = (*PostgresStore)(nil)
