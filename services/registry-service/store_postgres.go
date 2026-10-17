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
	ctx, cancel := pgCtx()
	defer cancel()
	_, err = p.pool.Exec(ctx, `
		INSERT INTO facilities (id, name, state, lga, ownership, level, services, bhcpf, power, water, latitude, longitude)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
		ON CONFLICT (id) DO UPDATE SET
			name=EXCLUDED.name, state=EXCLUDED.state, lga=EXCLUDED.lga,
			ownership=EXCLUDED.ownership, level=EXCLUDED.level, services=EXCLUDED.services,
			bhcpf=EXCLUDED.bhcpf, power=EXCLUDED.power, water=EXCLUDED.water,
			latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude`,
		f.ID, f.Name, f.State, f.LGA, f.Ownership, f.Level, services,
		f.BHCPF, f.Power, f.Water, nullFloat(f.Latitude), nullFloat(f.Longitude))
	return err
}

func scanFacility(row pgx.Row) (Facility, error) {
	var f Facility
	var services []byte
	var lat, lon *float64
	err := row.Scan(&f.ID, &f.Name, &f.State, &f.LGA, &f.Ownership, &f.Level,
		&services, &f.BHCPF, &f.Power, &f.Water, &lat, &lon)
	if err != nil {
		return Facility{}, err
	}
	json.Unmarshal(services, &f.Services)
	if lat != nil {
		f.Latitude = *lat
	}
	if lon != nil {
		f.Longitude = *lon
	}
	return f, nil
}

const facilityCols = "id, name, state, lga, ownership, level, services, bhcpf, power, water, latitude, longitude"

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
	var nin *string
	if pt.NIN != "" {
		nin = &pt.NIN
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO patients (id, nin, name, dob, phone, facility_id)
		VALUES ($1,$2,$3,$4,$5,$6)`,
		pt.ID, nin, pt.Name, pt.DOB, pt.Phone, pt.FacilityID)
	if err != nil {
		if isUniqueViolation(err) {
			if nin != nil {
				return Patient{}, errors.New("NIN already registered")
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
	tag, err := p.pool.Exec(ctx,
		"UPDATE patients SET nin=$2 WHERE id=$1 AND nin IS NULL", id, nin)
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

const patientCols = "id, COALESCE(nin,''), name, dob, phone, facility_id"

func (p *PostgresPatientStore) GetPatient(id string) (Patient, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	var pt Patient
	err := p.pool.QueryRow(ctx,
		"SELECT "+patientCols+" FROM patients WHERE id=$1", id).
		Scan(&pt.ID, &pt.NIN, &pt.Name, &pt.DOB, &pt.Phone, &pt.FacilityID)
	if errors.Is(err, pgx.ErrNoRows) {
		return Patient{}, ErrNotFound
	}
	return pt, err
}

func (p *PostgresPatientStore) FindByNIN(nin string) (Patient, error) {
	ctx, cancel := pgCtx()
	defer cancel()
	var pt Patient
	err := p.pool.QueryRow(ctx,
		"SELECT "+patientCols+" FROM patients WHERE nin=$1", nin).
		Scan(&pt.ID, &pt.NIN, &pt.Name, &pt.DOB, &pt.Phone, &pt.FacilityID)
	if errors.Is(err, pgx.ErrNoRows) {
		return Patient{}, ErrNotFound
	}
	return pt, err
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
		if rows.Scan(&pt.ID, &pt.NIN, &pt.Name, &pt.DOB, &pt.Phone, &pt.FacilityID) == nil {
			out = append(out, pt)
		}
	}
	return out
}
