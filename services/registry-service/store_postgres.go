// Postgres-backed registry stores — same Store/PatientStore seams as the
// in-memory defaults, bound on the state plane via DATABASE_URL.
//
// Semantics match MemoryStore exactly (contract tests run against both):
// upsert on put, ErrNotFound on miss, sorted list reads, atomic per-facility
// offline-id sequences, NIN uniqueness enforced by the database itself.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

const pgTimeout = 5 * time.Second

func pgCtx() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), pgTimeout)
}

// PostgresStore implements Store on Postgres (state plane).
type PostgresStore struct{ pool *pgxpool.Pool }

// NewPostgresStore connects and pings — a state-plane service must fail
// fast at boot, not on first request, if the database is unreachable.
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

func (p *PostgresStore) PutFacility(f Facility) error {
	if f.ID == "" || f.Name == "" {
		return ErrInvalid
	}
	services, err := json.Marshal(f.Services)
	if err != nil {
		return fmt.Errorf("services marshal: %w", err)
	}
	amenities, err := json.Marshal(f.Amenities)
	if err != nil {
		return fmt.Errorf("amenities marshal: %w", err)
	}
	ctx, cancel := pgCtx()
	defer cancel()
	_, err = p.pool.Exec(ctx, `
		INSERT INTO facilities (id, name, state, lga, ownership, level, services, bhcpf, power, water, latitude, longitude, beds, doctors, nurses, amenities, phone, hours)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
		ON CONFLICT (id) DO UPDATE SET
			name=EXCLUDED.name, state=EXCLUDED.state, lga=EXCLUDED.lga,
			ownership=EXCLUDED.ownership, level=EXCLUDED.level, services=EXCLUDED.services,
			bhcpf=EXCLUDED.bhcpf, power=EXCLUDED.power, water=EXCLUDED.water,
			latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude,
			beds=EXCLUDED.beds, doctors=EXCLUDED.doctors, nurses=EXCLUDED.nurses,
			amenities=EXCLUDED.amenities, phone=EXCLUDED.phone, hours=EXCLUDED.hours`,
		f.ID, f.Name, f.State, f.LGA, f.Ownership, f.Level, services,
		f.BHCPF, f.Power, f.Water, nullFloat(f.Latitude), nullFloat(f.Longitude),
		f.Beds, f.Doctors, f.Nurses, amenities, f.Phone, f.Hours)
	return err
}

func scanFacility(row pgx.Row) (Facility, error) {
	var f Facility
	var services, amenities []byte
	var lat, lon *float64
	err := row.Scan(&f.ID, &f.Name, &f.State, &f.LGA, &f.Ownership, &f.Level,
		&services, &f.BHCPF, &f.Power, &f.Water, &lat, &lon,
		&f.Beds, &f.Doctors, &f.Nurses, &amenities, &f.Phone, &f.Hours)
	if err != nil {
		return Facility{}, err
	}
	json.Unmarshal(services, &f.Services)
	json.Unmarshal(amenities, &f.Amenities)
	if lat != nil {
		f.Latitude = *lat
	}
	if lon != nil {
		f.Longitude = *lon
	}
	return f, nil
}

const facilityCols = "id, name, state, lga, ownership, level, services, bhcpf, power, water, latitude, longitude, beds, doctors, nurses, amenities, phone, hours"

func (p *PostgresStore) GetFacility(id string) (Facility, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	f, err := scanFacility(p.pool.QueryRow(ctx,
		"SELECT "+facilityCols+" FROM facilities WHERE id=$1", id))
	if errors.Is(err, pgx.ErrNoRows) {
		return Facility{}, ErrNotFound
	}
	return f, err
}

func (p *PostgresStore) ListFacilities() []Facility {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx, "SELECT "+facilityCols+" FROM facilities ORDER BY id")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Facility
	for rows.Next() {
		f, err := scanFacility(rows)
		if err == nil {
			out = append(out, f)
		}
	}
	return out
}

func (p *PostgresStore) PutProvider(pr Provider) error {
	if pr.ID == "" || pr.Name == "" {
		return ErrInvalid
	}
	ctx, cancel := pgCtx()
	defer cancel()
	_, err := p.pool.Exec(ctx, `
		INSERT INTO providers (id, name, license, cadre, facility, verified)
		VALUES ($1,$2,$3,$4,$5,$6)
		ON CONFLICT (id) DO UPDATE SET
			name=EXCLUDED.name, license=EXCLUDED.license, cadre=EXCLUDED.cadre,
			facility=EXCLUDED.facility, verified=EXCLUDED.verified`,
		pr.ID, pr.Name, pr.License, pr.Cadre, pr.Facility, pr.Verified)
	return err
}

func (p *PostgresStore) GetProvider(id string) (Provider, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	var pr Provider
	err := p.pool.QueryRow(ctx,
		"SELECT id, name, license, cadre, facility, verified FROM providers WHERE id=$1", id).
		Scan(&pr.ID, &pr.Name, &pr.License, &pr.Cadre, &pr.Facility, &pr.Verified)
	if errors.Is(err, pgx.ErrNoRows) {
		return Provider{}, ErrNotFound
	}
	return pr, err
}

func (p *PostgresStore) ListProviders() []Provider {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx,
		"SELECT id, name, license, cadre, facility, verified FROM providers ORDER BY id")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Provider
	for rows.Next() {
		var pr Provider
		if rows.Scan(&pr.ID, &pr.Name, &pr.License, &pr.Cadre, &pr.Facility, &pr.Verified) == nil {
			out = append(out, pr)
		}
	}
	return out
}

func nullFloat(v float64) *float64 {
	if v == 0 {
		return nil
	}
	return &v
}

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

// ---------------------------------------------------------------- patients

// PostgresPatientStore implements PatientStore on Postgres.
type PostgresPatientStore struct{ pool *pgxpool.Pool }

func NewPostgresPatientStore(pool *pgxpool.Pool) *PostgresPatientStore {
	return &PostgresPatientStore{pool: pool}
}

func (p *PostgresPatientStore) Register(pt Patient) (Patient, error) {
	if pt.Name == "" || pt.FacilityID == "" {
		return Patient{}, ErrInvalid
	}
	ctx, cancel := pgCtx()
	defer cancel()
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return Patient{}, err
	}
	defer tx.Rollback(ctx)
	if pt.ID == "" {
		// Atomic per-facility sequence: the same offline-id semantics as the
		// edge, coordinated by the database when connectivity exists.
		var seq int64
		err = tx.QueryRow(ctx, `
			INSERT INTO facility_seq (facility_id, next) VALUES ($1, 1)
			ON CONFLICT (facility_id) DO UPDATE SET next = facility_seq.next + 1
			RETURNING next`, pt.FacilityID).Scan(&seq)
		if err != nil {
			return Patient{}, err
		}
		pt.ID = OfflineID(pt.FacilityID, int(seq))
	}
	// Deterministic dedupe gate (ADR-0012) — checked inside the
	// transaction; the NIN unique index remains the final backstop.
	if existing, derr := p.FindDuplicate(pt); derr == nil {
		return Patient{}, fmt.Errorf("%w: %s", ErrDuplicate, DuplicateReason(existing))
	}
	var nin *string
	if pt.NIN != "" {
		nin = &pt.NIN
	}
	// KYC defaults: self-asserted; a syntactically valid NIN lifts to
	// nin-format (offline check only — authority verification is separate).
	pt.KYCLevel = KYCSelf
	if pt.NIN != "" && ValidNINFormat(pt.NIN) {
		pt.KYCLevel = KYCNINFormat
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO patients (id, nin, name, dob, phone, facility_id, kyc_level)
		VALUES ($1,$2,$3,$4,$5,$6,$7)`,
		pt.ID, nin, pt.Name, pt.DOB, pt.Phone, pt.FacilityID, pt.KYCLevel)
	if err != nil {
		if isUniqueViolation(err) {
			if nin != nil {
				return Patient{}, fmt.Errorf("%w: NIN already registered", ErrDuplicate)
			}
			return Patient{}, errors.New("patient id exists: " + pt.ID)
		}
		return Patient{}, err
	}
	return pt, tx.Commit(ctx)
}

func (p *PostgresPatientStore) LinkNIN(id, nin string) (Patient, error) {
	if nin == "" {
		return Patient{}, ErrInvalid
	}
	ctx, cancel := pgCtx()
	defer cancel()
	// Single guarded update: binds only records not already bound.
	tag, err := p.pool.Exec(ctx, `
		UPDATE patients SET nin=$2,
		    kyc_level = CASE WHEN kyc_level = 'self' THEN 'nin-format' ELSE kyc_level END
		WHERE id=$1 AND nin IS NULL`, id, nin)
	if err != nil {
		if isUniqueViolation(err) {
			return Patient{}, errors.New("NIN already bound")
		}
		return Patient{}, err
	}
	if tag.RowsAffected() == 0 {
		// Either unknown id or already bound — distinguish for the caller.
		if _, err := p.GetPatient(id); err != nil {
			return Patient{}, ErrNotFound
		}
		return Patient{}, ErrNINBound
	}
	return p.GetPatient(id)
}

const patientCols = "id, COALESCE(nin,''), name, dob, phone, facility_id, kyc_level, kyc_detail, COALESCE(created_at::text,'')"

func scanPatient(row pgx.Row) (Patient, error) {
	var pt Patient
	err := row.Scan(&pt.ID, &pt.NIN, &pt.Name, &pt.DOB, &pt.Phone, &pt.FacilityID,
		&pt.KYCLevel, &pt.KYCDetail, &pt.RegisteredAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return Patient{}, ErrNotFound
	}
	return pt, err
}

func (p *PostgresPatientStore) GetPatient(id string) (Patient, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	return scanPatient(p.pool.QueryRow(ctx,
		"SELECT "+patientCols+" FROM patients WHERE id=$1", id))
}

func (p *PostgresPatientStore) FindByNIN(nin string) (Patient, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	return scanPatient(p.pool.QueryRow(ctx,
		"SELECT "+patientCols+" FROM patients WHERE nin=$1", nin))
}

// FindDuplicate applies the deterministic dedupe rules (kyc.go) in one
// query: same NIN, same phone+DOB, or same normalised name+DOB.
func (p *PostgresPatientStore) FindDuplicate(pt Patient) (Patient, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	return scanPatient(p.pool.QueryRow(ctx, `
		SELECT `+patientCols+` FROM patients WHERE
		    ($1 <> '' AND nin = $1)
		    OR ($2 <> '' AND $3 <> '' AND phone = $2 AND dob = $3)
		    OR ($4 <> '' AND $3 <> ''
		        AND lower(regexp_replace(btrim(name), '\s+', ' ', 'g')) = $4
		        AND dob = $3)
		LIMIT 1`, pt.NIN, pt.Phone, pt.DOB, NormalizeName(pt.Name)))
}

// SetKYC raises the assurance level — the WHERE clause encodes the level
// ranking so a concurrent or stale write can never downgrade a record.
func (p *PostgresPatientStore) SetKYC(id, level, detail string) (Patient, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	rank := "CASE kyc_level WHEN 'phone' THEN 1 WHEN 'nin-format' THEN 2 WHEN 'nin-verified' THEN 3 WHEN 'biometric-verified' THEN 4 ELSE 0 END"
	tag, err := p.pool.Exec(ctx,
		"UPDATE patients SET kyc_level=$2, kyc_detail=$3 WHERE id=$1 AND "+rank+" <= $4",
		id, level, detail, kycRank(level))
	if err != nil {
		return Patient{}, err
	}
	if tag.RowsAffected() == 0 {
		if _, err := p.GetPatient(id); err != nil {
			return Patient{}, ErrNotFound
		}
	}
	return p.GetPatient(id)
}

// SaveOTP upserts the outstanding challenge for a patient (hash only) and
// resets the attempt counter.
func (p *PostgresPatientStore) SaveOTP(id, codeHash string, expires time.Time) error {
	ctx, cancel := pgCtx()
	defer cancel()
	tag, err := p.pool.Exec(ctx, `
		INSERT INTO patient_otp (patient_id, code_hash, expires_at)
		VALUES ($1, $2, $3)
		ON CONFLICT (patient_id) DO UPDATE
		SET code_hash=$2, expires_at=$3, attempts=0`, id, codeHash, expires)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// CheckOTP validates a presented code. The attempt counter increments in
// the same statement that reads the row, so concurrent guesses race safely
// and a crash can never reset the lockout.
func (p *PostgresPatientStore) CheckOTP(id, codeHash string, now time.Time) error {
	ctx, cancel := pgCtx()
	defer cancel()
	var stored string
	var expires time.Time
	var attempts int
	err := p.pool.QueryRow(ctx, `
		UPDATE patient_otp
		SET attempts = attempts + 1
		WHERE patient_id=$1
		RETURNING code_hash, expires_at, attempts`, id).
		Scan(&stored, &expires, &attempts)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if attempts > otpMaxAttempts {
		return ErrOTPLocked
	}
	if now.After(expires) {
		return ErrOTPExpired
	}
	if stored != codeHash {
		return ErrOTPMismatch
	}
	// One-time use: consume the challenge.
	_, err = p.pool.Exec(ctx, "DELETE FROM patient_otp WHERE patient_id=$1", id)
	return err
}

func (p *PostgresPatientStore) ListPatients() []Patient {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := p.pool.Query(ctx, "SELECT "+patientCols+" FROM patients ORDER BY id")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []Patient
	for rows.Next() {
		var pt Patient
		if rows.Scan(&pt.ID, &pt.NIN, &pt.Name, &pt.DOB, &pt.Phone, &pt.FacilityID,
			&pt.KYCLevel, &pt.KYCDetail, &pt.RegisteredAt) == nil {
			out = append(out, pt)
		}
	}
	return out
}

// ---------------------------------------------------------------- biometrics

// PostgresBiometricStore is the state-plane BiometricStore (ADR-0017).
// Templates are sealed before they reach the database — Postgres only
// ever holds sealx tokens.
type PostgresBiometricStore struct{ pool *pgxpool.Pool }

func NewPostgresBiometricStore(pool *pgxpool.Pool) *PostgresBiometricStore {
	return &PostgresBiometricStore{pool: pool}
}

func (b *PostgresBiometricStore) Enrol(t EnrolledTemplate) error {
	ctx, cancel := pgCtx()
	defer cancel()
	// ON CONFLICT DO NOTHING: re-enrolling the same finger is idempotent,
	// matching the memory store.
	_, err := b.pool.Exec(ctx, `
		INSERT INTO registry_biometrics (patient_id, finger, sealed, created_at)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (patient_id, finger) DO NOTHING`,
		t.PatientID, t.Finger, t.Sealed, t.CreatedAt)
	return err
}

func (b *PostgresBiometricStore) ForPatient(patientID string) []EnrolledTemplate {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := b.pool.Query(ctx,
		"SELECT patient_id, finger, sealed, created_at FROM registry_biometrics WHERE patient_id=$1 ORDER BY finger",
		patientID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	return scanTemplates(rows)
}

func (b *PostgresBiometricStore) All() []EnrolledTemplate {
	ctx, cancel := pgCtx()
	defer cancel()
	rows, err := b.pool.Query(ctx,
		"SELECT patient_id, finger, sealed, created_at FROM registry_biometrics ORDER BY patient_id, finger")
	if err != nil {
		return nil
	}
	defer rows.Close()
	return scanTemplates(rows)
}

func scanTemplates(rows pgx.Rows) []EnrolledTemplate {
	out := []EnrolledTemplate{}
	for rows.Next() {
		var t EnrolledTemplate
		var created time.Time
		if err := rows.Scan(&t.PatientID, &t.Finger, &t.Sealed, &created); err != nil {
			continue
		}
		t.CreatedAt = created.UTC().Format(time.RFC3339)
		out = append(out, t)
	}
	return out
}
