package main

// store_postgres.go — state-plane persistence for dispatch + CRM
// (ADR-0019). Same CaseStore contract as the memory store; parity is
// proven by the TEST_DATABASE_URL-gated test suite.

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PostgresCaseStore struct {
	pool *pgxpool.Pool
}

func NewPostgresCaseStore(ctx context.Context, dsn string) (*PostgresCaseStore, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		return nil, err
	}
	return &PostgresCaseStore{pool: pool}, nil
}

func (p *PostgresCaseStore) nextSeq(ctx context.Context, tx pgx.Tx, name, prefix string, width int) (string, error) {
	var n int
	err := tx.QueryRow(ctx,
		`INSERT INTO dispatch_seq (name, value) VALUES ($1, 1)
		 ON CONFLICT (name) DO UPDATE SET value = dispatch_seq.value + 1
		 RETURNING value`, name).Scan(&n)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s-%0*d", prefix, width, n), nil
}

// ------------------------------------------------------------ incidents

func (p *PostgresCaseStore) Put(inc Incident) error {
	ctx := context.Background()
	tl, _ := json.Marshal(inc.Timeline)
	created, _ := time.Parse(time.RFC3339, inc.CreatedAt)
	updated, _ := time.Parse(time.RFC3339, inc.UpdatedAt)
	_, err := p.pool.Exec(ctx, `
		INSERT INTO dispatch_incidents
		  (id, channel, caller_phone, patient_id, lat, lng, loc_source,
		   complaint, triage, triage_reason, status, facility_id,
		   facility_name, facility_km, timeline, created_at, updated_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
		ON CONFLICT (id) DO UPDATE SET
		  channel=$2, caller_phone=$3, patient_id=$4, lat=$5, lng=$6,
		  loc_source=$7, complaint=$8, triage=$9, triage_reason=$10,
		  status=$11, facility_id=$12, facility_name=$13, facility_km=$14,
		  timeline=$15, created_at=$16, updated_at=$17`,
		inc.ID, inc.Channel, inc.CallerPhone, inc.PatientID, inc.Lat, inc.Lng,
		inc.LocSource, inc.Complaint, inc.Triage, inc.TriageReason, inc.Status,
		inc.FacilityID, inc.FacilityName, inc.FacilityKm, tl, created, updated)
	return err
}

const incidentCols = `id, channel, caller_phone, patient_id, lat, lng, loc_source,
	complaint, triage, triage_reason, status, facility_id, facility_name,
	facility_km, timeline, created_at, updated_at`

func scanIncident(row pgx.Row) (Incident, error) {
	var inc Incident
	var tl []byte
	var created, updated time.Time
	err := row.Scan(&inc.ID, &inc.Channel, &inc.CallerPhone, &inc.PatientID,
		&inc.Lat, &inc.Lng, &inc.LocSource, &inc.Complaint, &inc.Triage,
		&inc.TriageReason, &inc.Status, &inc.FacilityID, &inc.FacilityName,
		&inc.FacilityKm, &tl, &created, &updated)
	if err != nil {
		return Incident{}, err
	}
	_ = json.Unmarshal(tl, &inc.Timeline)
	inc.CreatedAt = created.UTC().Format(time.RFC3339)
	inc.UpdatedAt = updated.UTC().Format(time.RFC3339)
	return inc, nil
}

func (p *PostgresCaseStore) Get(id string) (Incident, error) {
	inc, err := scanIncident(p.pool.QueryRow(context.Background(),
		`SELECT `+incidentCols+` FROM dispatch_incidents WHERE id=$1`, id))
	if err == pgx.ErrNoRows {
		return Incident{}, ErrNotFound
	}
	return inc, err
}

func (p *PostgresCaseStore) List(status, facilityID string) []Incident {
	q := `SELECT ` + incidentCols + ` FROM dispatch_incidents WHERE ($1='' OR status=$1) AND ($2='' OR facility_id=$2) ORDER BY created_at`
	rows, err := p.pool.Query(context.Background(), q, status, facilityID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Incident{}
	for rows.Next() {
		if inc, err := scanIncident(rows); err == nil {
			out = append(out, inc)
		}
	}
	return out
}

func (p *PostgresCaseStore) OpenForCaller(phone string, since time.Time) (Incident, error) {
	inc, err := scanIncident(p.pool.QueryRow(context.Background(),
		`SELECT `+incidentCols+` FROM dispatch_incidents
		 WHERE caller_phone=$1 AND status NOT IN ('closed','cancelled')
		   AND created_at >= $2
		 ORDER BY created_at DESC LIMIT 1`, phone, since))
	if err == pgx.ErrNoRows {
		return Incident{}, ErrNotFound
	}
	return inc, err
}

func (p *PostgresCaseStore) NextID() string {
	ctx := context.Background()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return "INC-00000"
	}
	defer tx.Rollback(ctx)
	id, err := p.nextSeq(ctx, tx, "incident", "INC", 5)
	if err != nil {
		return "INC-00000"
	}
	_ = tx.Commit(ctx)
	return id
}

// -------------------------------------------------------------- contacts

func (p *PostgresCaseStore) UpsertContactOnIntake(phone, channel, patientID, ts string) Contact {
	ctx := context.Background()
	t, _ := time.Parse(time.RFC3339, ts)
	_, _ = p.pool.Exec(ctx, `
		INSERT INTO dispatch_contacts (phone, patient_id, preferred_channel, first_contact_at, last_contact_at, interactions)
		VALUES ($1, $2, $3, $4, $4, 1)
		ON CONFLICT (phone) DO UPDATE SET
		  patient_id = CASE WHEN $2 <> '' THEN $2 ELSE dispatch_contacts.patient_id END,
		  preferred_channel = $3, last_contact_at = $4,
		  interactions = dispatch_contacts.interactions + 1`,
		phone, patientID, channel, t)
	c, err := p.GetContact(phone)
	if err != nil {
		return Contact{Phone: phone}
	}
	return c
}

func (p *PostgresCaseStore) PutContact(c Contact) error {
	tags, _ := json.Marshal(c.Tags)
	first, _ := time.Parse(time.RFC3339, c.FirstContactAt)
	last, _ := time.Parse(time.RFC3339, c.LastContactAt)
	_, err := p.pool.Exec(context.Background(), `
		INSERT INTO dispatch_contacts
		  (phone, name, patient_id, language, preferred_channel, consent_follow_up,
		   tags, interactions, first_contact_at, last_contact_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
		ON CONFLICT (phone) DO UPDATE SET
		  name=$2, patient_id=$3, language=$4, preferred_channel=$5,
		  consent_follow_up=$6, tags=$7, interactions=$8,
		  first_contact_at=$9, last_contact_at=$10`,
		c.Phone, c.Name, c.PatientID, c.Language, c.PreferredChannel,
		c.ConsentFollowUp, tags, c.Interactions, first, last)
	return err
}

const contactCols = `phone, name, patient_id, language, preferred_channel,
	consent_follow_up, tags, interactions, first_contact_at, last_contact_at`

func scanContact(row pgx.Row) (Contact, error) {
	var c Contact
	var tags []byte
	var first, last time.Time
	err := row.Scan(&c.Phone, &c.Name, &c.PatientID, &c.Language,
		&c.PreferredChannel, &c.ConsentFollowUp, &tags, &c.Interactions, &first, &last)
	if err != nil {
		return Contact{}, err
	}
	_ = json.Unmarshal(tags, &c.Tags)
	c.FirstContactAt = first.UTC().Format(time.RFC3339)
	c.LastContactAt = last.UTC().Format(time.RFC3339)
	return c, nil
}

func (p *PostgresCaseStore) GetContact(phone string) (Contact, error) {
	c, err := scanContact(p.pool.QueryRow(context.Background(),
		`SELECT `+contactCols+` FROM dispatch_contacts WHERE phone=$1`, phone))
	if err == pgx.ErrNoRows {
		return Contact{}, ErrNotFound
	}
	return c, err
}

func (p *PostgresCaseStore) ListContacts(q string, limit int) []Contact {
	if limit <= 0 {
		limit = 1000
	}
	rows, err := p.pool.Query(context.Background(), `
		SELECT `+contactCols+` FROM dispatch_contacts
		WHERE ($1='' OR name ILIKE '%'||$1||'%' OR phone ILIKE '%'||$1||'%')
		ORDER BY last_contact_at DESC LIMIT $2`, q, limit)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Contact{}
	for rows.Next() {
		if c, err := scanContact(rows); err == nil {
			out = append(out, c)
		}
	}
	return out
}

// ---------------------------------------------------------- interactions

func (p *PostgresCaseStore) AddInteraction(i Interaction) error {
	ts, _ := time.Parse(time.RFC3339, i.TS)
	_, err := p.pool.Exec(context.Background(), `
		INSERT INTO dispatch_interactions
		  (id, phone, channel, direction, kind, summary, incident_id, case_id, actor, ts)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		i.ID, i.Phone, i.Channel, i.Direction, i.Kind, i.Summary,
		i.IncidentID, i.CaseID, i.Actor, ts)
	return err
}

func (p *PostgresCaseStore) Interactions(phone string) []Interaction {
	rows, err := p.pool.Query(context.Background(), `
		SELECT id, phone, channel, direction, kind, summary, incident_id, case_id, actor, ts
		FROM dispatch_interactions WHERE phone=$1 ORDER BY ts`, phone)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Interaction{}
	for rows.Next() {
		var i Interaction
		var ts time.Time
		if err := rows.Scan(&i.ID, &i.Phone, &i.Channel, &i.Direction, &i.Kind,
			&i.Summary, &i.IncidentID, &i.CaseID, &i.Actor, &ts); err == nil {
			i.TS = ts.UTC().Format(time.RFC3339)
			out = append(out, i)
		}
	}
	return out
}

// ----------------------------------------------------------------- cases

func (p *PostgresCaseStore) PutCase(c Case) error {
	ctx := context.Background()
	tl, _ := json.Marshal(c.Timeline)
	opened, _ := time.Parse(time.RFC3339, c.OpenedAt)
	updated, _ := time.Parse(time.RFC3339, c.UpdatedAt)
	var closed *time.Time
	if c.ClosedAt != "" {
		if t, err := time.Parse(time.RFC3339, c.ClosedAt); err == nil {
			closed = &t
		}
	}
	_, err := p.pool.Exec(ctx, `
		INSERT INTO dispatch_cases
		  (id, contact_phone, patient_id, incident_id, title, priority, status,
		   owner, facility_id, outcome, opened_at, updated_at, closed_at, timeline)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
		ON CONFLICT (id) DO UPDATE SET
		  contact_phone=$2, patient_id=$3, incident_id=$4, title=$5, priority=$6,
		  status=$7, owner=$8, facility_id=$9, outcome=$10, opened_at=$11,
		  updated_at=$12, closed_at=$13, timeline=$14`,
		c.ID, c.ContactPhone, c.PatientID, c.IncidentID, c.Title, c.Priority,
		c.Status, c.Owner, c.FacilityID, c.Outcome, opened, updated, closed, tl)
	return err
}

const caseCols = `id, contact_phone, patient_id, incident_id, title, priority,
	status, owner, facility_id, outcome, opened_at, updated_at, closed_at, timeline`

func scanCase(row pgx.Row) (Case, error) {
	var c Case
	var tl []byte
	var opened, updated time.Time
	var closed *time.Time
	err := row.Scan(&c.ID, &c.ContactPhone, &c.PatientID, &c.IncidentID, &c.Title,
		&c.Priority, &c.Status, &c.Owner, &c.FacilityID, &c.Outcome,
		&opened, &updated, &closed, &tl)
	if err != nil {
		return Case{}, err
	}
	_ = json.Unmarshal(tl, &c.Timeline)
	c.OpenedAt = opened.UTC().Format(time.RFC3339)
	c.UpdatedAt = updated.UTC().Format(time.RFC3339)
	if closed != nil {
		c.ClosedAt = closed.UTC().Format(time.RFC3339)
	}
	return c, nil
}

func (p *PostgresCaseStore) GetCase(id string) (Case, error) {
	c, err := scanCase(p.pool.QueryRow(context.Background(),
		`SELECT `+caseCols+` FROM dispatch_cases WHERE id=$1`, id))
	if err == pgx.ErrNoRows {
		return Case{}, ErrNotFound
	}
	return c, err
}

func (p *PostgresCaseStore) ListCases(status, owner string) []Case {
	rows, err := p.pool.Query(context.Background(), `
		SELECT `+caseCols+` FROM dispatch_cases
		WHERE ($1='' OR status=$1) AND ($2='' OR owner=$2) ORDER BY opened_at`, status, owner)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []Case{}
	for rows.Next() {
		if c, err := scanCase(rows); err == nil {
			out = append(out, c)
		}
	}
	return out
}

func (p *PostgresCaseStore) OpenCaseFor(phone string) (Case, error) {
	c, err := scanCase(p.pool.QueryRow(context.Background(), `
		SELECT `+caseCols+` FROM dispatch_cases
		WHERE contact_phone=$1 AND status NOT IN ('resolved','closed')
		ORDER BY opened_at DESC LIMIT 1`, phone))
	if err == pgx.ErrNoRows {
		return Case{}, ErrNotFound
	}
	return c, err
}

func (p *PostgresCaseStore) NextCaseID() string {
	return p.allocID("case", "CASE", 5)
}

// ----------------------------------------------------------------- tasks

func (p *PostgresCaseStore) PutTask(t CaseTask) error {
	due, _ := time.Parse(time.RFC3339, t.DueAt)
	var done *time.Time
	if t.DoneAt != "" {
		if d, err := time.Parse(time.RFC3339, t.DoneAt); err == nil {
			done = &d
		}
	}
	_, err := p.pool.Exec(context.Background(), `
		INSERT INTO dispatch_tasks (id, case_id, kind, due_at, done_at, status, assignee, note)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
		ON CONFLICT (id) DO UPDATE SET
		  case_id=$2, kind=$3, due_at=$4, done_at=$5, status=$6, assignee=$7, note=$8`,
		t.ID, t.CaseID, t.Kind, due, done, t.Status, t.Assignee, t.Note)
	return err
}

func scanTask(row pgx.Row) (CaseTask, error) {
	var t CaseTask
	var due time.Time
	var done *time.Time
	err := row.Scan(&t.ID, &t.CaseID, &t.Kind, &due, &done, &t.Status, &t.Assignee, &t.Note)
	if err != nil {
		return CaseTask{}, err
	}
	t.DueAt = due.UTC().Format(time.RFC3339)
	if done != nil {
		t.DoneAt = done.UTC().Format(time.RFC3339)
	}
	return t, nil
}

const taskCols = `id, case_id, kind, due_at, done_at, status, assignee, note`

func (p *PostgresCaseStore) ListTasks(caseID string) []CaseTask {
	rows, err := p.pool.Query(context.Background(),
		`SELECT `+taskCols+` FROM dispatch_tasks WHERE case_id=$1 ORDER BY id`, caseID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []CaseTask{}
	for rows.Next() {
		if t, err := scanTask(rows); err == nil {
			out = append(out, t)
		}
	}
	return out
}

func (p *PostgresCaseStore) OpenTasks() []CaseTask {
	rows, err := p.pool.Query(context.Background(),
		`SELECT `+taskCols+` FROM dispatch_tasks WHERE status='open' ORDER BY due_at`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []CaseTask{}
	for rows.Next() {
		if t, err := scanTask(rows); err == nil {
			out = append(out, t)
		}
	}
	return out
}

func (p *PostgresCaseStore) NextTaskID() string        { return p.allocID("task", "TASK", 5) }
func (p *PostgresCaseStore) NextInteractionID() string { return p.allocID("interaction", "INT", 6) }

func (p *PostgresCaseStore) allocID(name, prefix string, width int) string {
	ctx := context.Background()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return prefix + "-00000"
	}
	defer tx.Rollback(ctx)
	id, err := p.nextSeq(ctx, tx, name, prefix, width)
	if err != nil {
		return prefix + "-00000"
	}
	_ = tx.Commit(ctx)
	return id
}
