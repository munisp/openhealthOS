// Postgres-backed equipment store — the same EquipmentService seam as the
// in-memory Service. Ticket open/assign/resolve run in transactions with
// row locks, so the equipment status flip and the ticket transition are
// one atomic unit — no half-open work orders after a crash.
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

// PostgresService implements EquipmentService on Postgres.
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

func (p *PostgresService) nextID(ctx context.Context, prefix string) (string, error) {
	var seq int64
	if err := p.pool.QueryRow(ctx, "SELECT nextval('equipment_id_seq')").Scan(&seq); err != nil {
		return "", err
	}
	return fmt.Sprintf("%s-%06d", prefix, seq), nil
}

func (p *PostgresService) Register(e Equipment) (Equipment, error) {
	if e.Facility == "" || e.Type == "" {
		return Equipment{}, errors.New("facility and type are required")
	}
	ctx, cancel := pgCtx()
	defer cancel()
	if e.ID == "" {
		id, err := p.nextID(ctx, "EQ")
		if err != nil {
			return Equipment{}, err
		}
		e.ID = id
	}
	if e.Status == "" {
		e.Status = "working"
	}
	_, err := p.pool.Exec(ctx, `
		INSERT INTO equipment (id, facility, type, model, status, donated)
		VALUES ($1,$2,$3,$4,$5,$6)`,
		e.ID, e.Facility, e.Type, e.Model, e.Status, e.Donated)
	if err != nil {
		return Equipment{}, err
	}
	return p.GetEquipment(e.ID)
}

func (p *PostgresService) AddTechnician(t Technician) (Technician, error) {
	if t.ID == "" || t.Name == "" {
		return Technician{}, errors.New("id and name are required")
	}
	regions, err := json.Marshal(t.Regions)
	if err != nil {
		return Technician{}, err
	}
	ctx, cancel := pgCtx()
	defer cancel()
	_, err = p.pool.Exec(ctx, `
		INSERT INTO technicians (id, name, vetted, regions) VALUES ($1,$2,$3,$4)
		ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, vetted=EXCLUDED.vetted, regions=EXCLUDED.regions`,
		t.ID, t.Name, t.Vetted, regions)
	return t, err
}

func scanEquipment(row pgx.Row) (Equipment, error) {
	var e Equipment
	var intake time.Time
	err := row.Scan(&e.ID, &e.Facility, &e.Type, &e.Model, &e.Status, &e.Donated, &intake)
	e.IntakeAt = intake.UTC().Format(time.RFC3339)
	return e, err
}

func (p *PostgresService) GetEquipment(id string) (Equipment, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	e, err := scanEquipment(p.pool.QueryRow(ctx, `
		SELECT id, facility, type, model, status, donated, intake_at FROM equipment WHERE id=$1`, id))
	if errors.Is(err, pgx.ErrNoRows) {
		return Equipment{}, ErrNotFound
	}
	return e, err
}

func scanTicket(row pgx.Row) (Ticket, error) {
	var t Ticket
	var opened time.Time
	var resolved *time.Time
	err := row.Scan(&t.ID, &t.EquipmentID, &t.Issue, &t.Priority, &t.Status,
		&t.Technician, &opened, &resolved)
	t.OpenedAt = opened.UTC().Format(time.RFC3339)
	if resolved != nil {
		t.ResolvedAt = resolved.UTC().Format(time.RFC3339)
	}
	return t, err
}

const ticketCols = "id, equipment_id, issue, priority, status, technician, opened_at, resolved_at"

func (p *PostgresService) OpenTicket(equipmentID, issue, priority string) (Ticket, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Ticket{}, err
	}
	defer tx.Rollback(ctx)
	var exists int
	err = tx.QueryRow(ctx, "SELECT 1 FROM equipment WHERE id=$1 FOR UPDATE", equipmentID).Scan(&exists)
	if errors.Is(err, pgx.ErrNoRows) {
		return Ticket{}, ErrNotFound
	}
	if err != nil {
		return Ticket{}, err
	}
	id, err := p.nextID(ctx, "MT")
	if err != nil {
		return Ticket{}, err
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO tickets (id, equipment_id, issue, priority, status)
		VALUES ($1,$2,$3,$4,'open')`, id, equipmentID, issue, priority); err != nil {
		return Ticket{}, err
	}
	// The asset is down the moment the ticket exists — same transaction.
	if _, err := tx.Exec(ctx,
		"UPDATE equipment SET status='down' WHERE id=$1", equipmentID); err != nil {
		return Ticket{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return Ticket{}, err
	}
	return scanTicket(p.pool.QueryRow(ctx, "SELECT "+ticketCols+" FROM tickets WHERE id=$1", id))
}

func (p *PostgresService) Assign(ticketID, techID string) error {
	ctx, cancel := pgCtx()
	defer cancel()
	var vetted bool
	err := p.pool.QueryRow(ctx, "SELECT vetted FROM technicians WHERE id=$1", techID).Scan(&vetted)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if !vetted {
		return ErrUnvettedTech // marketplace gate holds on the durable store too
	}
	tag, err := p.pool.Exec(ctx,
		"UPDATE tickets SET status='assigned', technician=$2 WHERE id=$1 AND status='open'",
		ticketID, techID)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		var exists int
		if err := p.pool.QueryRow(ctx, "SELECT 1 FROM tickets WHERE id=$1", ticketID).Scan(&exists); err != nil {
			return ErrNotFound
		}
		return ErrBadTransition
	}
	return nil
}

func (p *PostgresService) Resolve(ticketID string) error {
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var equipmentID string
	err = tx.QueryRow(ctx,
		"UPDATE tickets SET status='resolved', resolved_at=now() WHERE id=$1 AND status='assigned' RETURNING equipment_id",
		ticketID).Scan(&equipmentID)
	if errors.Is(err, pgx.ErrNoRows) {
		var exists int
		if err := tx.QueryRow(ctx, "SELECT 1 FROM tickets WHERE id=$1", ticketID).Scan(&exists); err != nil {
			return ErrNotFound
		}
		return ErrBadTransition
	}
	if err != nil {
		return err
	}
	// Equipment returns to service in the same transaction as the close.
	if _, err := tx.Exec(ctx,
		"UPDATE equipment SET status='working' WHERE id=$1", equipmentID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (p *PostgresService) ListTickets(status string) []Ticket {
	ctx, cancel := pgCtx()
	defer cancel()
	query := "SELECT " + ticketCols + " FROM tickets"
	args := []any{}
	if status != "" {
		query += " WHERE status=$1"
		args = append(args, status)
	}
	query += " ORDER BY id"
	rows, err := p.pool.Query(ctx, query, args...)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Ticket{}
	for rows.Next() {
		if t, err := scanTicket(rows); err == nil {
			out = append(out, t)
		}
	}
	return out
}

var _ EquipmentService = (*PostgresService)(nil)

// AutoResolve closes an open AUTO: ticket on telemetry recovery — guarded
// transition mirrors the memory store exactly.
func (p *PostgresService) AutoResolve(ticketID string) error {
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var equipmentID string
	err = tx.QueryRow(ctx,
		`UPDATE tickets SET status='resolved', technician='auto:telemetry', resolved_at=now()
		 WHERE id=$1 AND status='open' AND issue LIKE 'AUTO:%' RETURNING equipment_id`,
		ticketID).Scan(&equipmentID)
	if err == pgx.ErrNoRows {
		var st string
		if qerr := tx.QueryRow(ctx, "SELECT status FROM tickets WHERE id=$1", ticketID).Scan(&st); qerr == pgx.ErrNoRows {
			return ErrNotFound
		}
		return ErrBadTransition
	}
	if err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, "UPDATE equipment SET status='working' WHERE id=$1", equipmentID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// --------------------------------------------------------- tags & custody (ADR-0052)

// Now reports wall time — the Postgres store has no injected clock.
func (p *PostgresService) Now() time.Time { return time.Now() }

// Scan verifies the signed tag, updates equipment.location, and appends an
// immutable custody_events row — all in one transaction so the location can
// never drift from its evidence.
func (p *PostgresService) Scan(tag, action, actor, location, notes string) (CustodyEvent, error) {
	now := time.Now()
	t, err := VerifyTag(tag, now)
	if err != nil {
		return CustodyEvent{}, err
	}
	if !scanActions[action] {
		return CustodyEvent{}, fmt.Errorf("unknown action %q", action)
	}
	if actor == "" {
		return CustodyEvent{}, errors.New("actor required — the audit names who")
	}
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return CustodyEvent{}, err
	}
	defer tx.Rollback(ctx)
	var status string
	err = tx.QueryRow(ctx, "SELECT status FROM equipment WHERE id=$1 FOR UPDATE", t.Asset).Scan(&status)
	if err == pgx.ErrNoRows {
		return CustodyEvent{}, ErrNotFound
	}
	if err != nil {
		return CustodyEvent{}, err
	}
	if location != "" {
		if _, err := tx.Exec(ctx, "UPDATE equipment SET location=$1 WHERE id=$2", location, t.Asset); err != nil {
			return CustodyEvent{}, err
		}
	}
	if action == "maintenance_flag" && status == "working" {
		if _, err := tx.Exec(ctx, "UPDATE equipment SET status='in-maintenance' WHERE id=$1", t.Asset); err != nil {
			return CustodyEvent{}, err
		}
	}
	ev := CustodyEvent{
		At: now.UTC().Format(time.RFC3339), Asset: t.Asset,
		Facility: t.Facility, Action: action, Actor: actor,
		Location: location, Notes: notes, StaleTag: t.Stale,
	}
	if _, err := tx.Exec(ctx,
		`INSERT INTO custody_events (asset_id, facility, action, actor, location, notes, stale_tag, scanned_at)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
		ev.Asset, ev.Facility, ev.Action, ev.Actor, ev.Location, ev.Notes, ev.StaleTag, now); err != nil {
		return CustodyEvent{}, err
	}
	return ev, tx.Commit(ctx)
}

// CustodyLog reads the audit spine, newest first; assetID "" = all assets.
func (p *PostgresService) CustodyLog(assetID string) []CustodyEvent {
	ctx, cancel := pgCtx()
	defer cancel()
	q := `SELECT scanned_at, asset_id, facility, action, actor, location, notes, stale_tag
	      FROM custody_events`
	args := []any{}
	if assetID != "" {
		q += " WHERE asset_id=$1"
		args = append(args, assetID)
	}
	q += " ORDER BY scanned_at DESC, id DESC LIMIT 500"
	rows, err := p.pool.Query(ctx, q, args...)
	if err != nil {
		return []CustodyEvent{}
	}
	defer rows.Close()
	out := []CustodyEvent{}
	for rows.Next() {
		var ev CustodyEvent
		var at time.Time
		if rows.Scan(&at, &ev.Asset, &ev.Facility, &ev.Action, &ev.Actor, &ev.Location, &ev.Notes, &ev.StaleTag) == nil {
			ev.At = at.UTC().Format(time.RFC3339)
			out = append(out, ev)
		}
	}
	return out
}
