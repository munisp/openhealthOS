package main

// Postgres integration tests — run the store contract against a live
// database when TEST_DATABASE_URL is set (CI provides a postgres service
// container; locally: docker compose -f infra/compose/docker-compose.yaml up -d postgres).
// Skipped by default so unit runs stay hermetic.

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func pgTestStore(t *testing.T) (*PostgresStore, *PostgresPatientStore) {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset — skipping postgres integration test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	store, err := NewPostgresStore(ctx, dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	t.Cleanup(store.Close)
	// Self-apply the migrations this module owns (idempotent by design) so
	// the test does not depend on CI having applied the latest schema.
	_, file, _, _ := runtime.Caller(0)
	mig := filepath.Join(filepath.Dir(file), "..", "..", "db", "migrations")
	for _, name := range []string{"0001_init.sql", "0005_registry_kyc.sql", "0007_facility_capacity_geo.sql"} {
		sql, rerr := os.ReadFile(filepath.Join(mig, name))
		if rerr != nil {
			t.Fatalf("read migration %s: %v", name, rerr)
		}
		if _, err := store.pool.Exec(ctx, string(sql)); err != nil {
			t.Fatalf("apply %s: %v", name, err)
		}
	}
	// Deterministic slate: wipe the tables this test owns.
	if _, err := store.pool.Exec(ctx,
		"TRUNCATE facilities, providers, patients, facility_seq, patient_otp CASCADE"); err != nil {
		t.Fatalf("truncate: %v", err)
	}
	return store, NewPostgresPatientStore(store.pool)
}

func TestPostgresFacilitiesProviders(t *testing.T) {
	store, _ := pgTestStore(t)
	f := Facility{ID: "NG-KD-0014", Name: "Kawo PHC", State: "Kaduna", LGA: "Kaduna North",
		Ownership: "public", Level: "PHC", Services: []string{"anc", "immunisation"},
		BHCPF: true, Power: "solar", Water: "borehole", Latitude: 10.52, Longitude: 7.44}
	if err := store.PutFacility(f); err != nil {
		t.Fatalf("put: %v", err)
	}
	got, err := store.GetFacility("NG-KD-0014")
	if err != nil || got.Name != "Kawo PHC" || !got.BHCPF || len(got.Services) != 2 {
		t.Fatalf("get: %v %+v", err, got)
	}
	// Upsert: same id, updated power.
	f.Power = "hybrid"
	store.PutFacility(f)
	got, _ = store.GetFacility("NG-KD-0014")
	if got.Power != "hybrid" {
		t.Fatalf("upsert did not apply: %+v", got)
	}
	if got.Latitude != 10.52 || got.Longitude != 7.44 {
		t.Fatalf("geo lost: %+v", got)
	}
	if _, err := store.GetFacility("NOPE"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
	if err := store.PutFacility(Facility{}); !errors.Is(err, ErrInvalid) {
		t.Fatalf("want ErrInvalid, got %v", err)
	}
	store.PutFacility(Facility{ID: "NG-KD-0001", Name: "A", State: "Kaduna", LGA: "X",
		Beds: 40, Doctors: 3, Nurses: 12, Amenities: []string{"pharmacy", "lab"},
		Latitude: 10.52, Longitude: 7.43, Phone: "+234800", Hours: "24/7"})
	// Capacity profile round-trips through Postgres (ADR-0014).
	capGot, _ := store.GetFacility("NG-KD-0001")
	if capGot.Beds != 40 || capGot.Doctors != 3 || capGot.Nurses != 12 ||
		len(capGot.Amenities) != 2 || capGot.Phone != "+234800" || capGot.Hours != "24/7" {
		t.Fatalf("capacity profile round-trip: %+v", capGot)
	}
	if list := store.ListFacilities(); len(list) != 2 || list[0].ID != "NG-KD-0001" {
		t.Fatalf("list not sorted/complete: %+v", list)
	}

	pr := Provider{ID: "PRV-1", Name: "Nurse A", Cadre: "nurse", Facility: "NG-KD-0014", Verified: true}
	if err := store.PutProvider(pr); err != nil {
		t.Fatalf("put provider: %v", err)
	}
	gotP, err := store.GetProvider("PRV-1")
	if err != nil || !gotP.Verified {
		t.Fatalf("get provider: %v %+v", err, gotP)
	}
	if _, err := store.GetProvider("NOPE"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}

func TestPostgresPatients(t *testing.T) {
	_, patients := pgTestStore(t)
	// Offline ids: atomic per-facility sequence.
	a, err := patients.Register(Patient{Name: "Amina", FacilityID: "NG-KD-0014"})
	if err != nil || a.ID != "OH-NG-KD-0014-000001" {
		t.Fatalf("register: %v %+v", err, a)
	}
	b, _ := patients.Register(Patient{Name: "Bala", FacilityID: "NG-KD-0014"})
	if b.ID != "OH-NG-KD-0014-000002" {
		t.Fatalf("sequence: %+v", b)
	}
	// Other facility has its own sequence.
	c, _ := patients.Register(Patient{Name: "Cici", FacilityID: "NG-KD-0099"})
	if c.ID != "OH-NG-KD-0099-000001" {
		t.Fatalf("per-facility sequence: %+v", c)
	}
	// NIN uniqueness enforced by the database.
	if _, err := patients.Register(Patient{Name: "X", FacilityID: "NG-KD-0014", NIN: "1234"}); err != nil {
		t.Fatalf("nin register: %v", err)
	}
	if _, err := patients.Register(Patient{Name: "Y", FacilityID: "NG-KD-0014", NIN: "1234"}); err == nil {
		t.Fatal("duplicate NIN accepted")
	}
	// Progressive enrolment: bind once, never twice.
	linked, err := patients.LinkNIN(a.ID, "9999")
	if err != nil || linked.NIN != "9999" {
		t.Fatalf("link: %v %+v", err, linked)
	}
	if _, err := patients.LinkNIN(a.ID, "8888"); !errors.Is(err, ErrNINBound) {
		t.Fatalf("re-bind: want ErrNINBound, got %v", err)
	}
	found, err := patients.FindByNIN("9999")
	if err != nil || found.ID != a.ID {
		t.Fatalf("find by nin: %v %+v", err, found)
	}
	if _, err := patients.LinkNIN("NOPE", "1"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("link unknown: want ErrNotFound, got %v", err)
	}
	if list := patients.ListPatients(); len(list) != 4 {
		t.Fatalf("list: %+v", list)
	}
}

func TestPostgresKYC(t *testing.T) {
	_, patients := pgTestStore(t)

	// Dedupe gate: phone+DOB and normalised name+DOB refused, family
	// member sharing a phone allowed.
	a, err := patients.Register(Patient{Name: "Amina Yusuf", DOB: "1990-01-01", Phone: "+234801", FacilityID: "F1"})
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	if _, err := patients.Register(Patient{Name: "A. Yusuf", DOB: "1990-01-01", Phone: "+234801", FacilityID: "F2"}); !errors.Is(err, ErrDuplicate) {
		t.Fatalf("phone+dob dup: want ErrDuplicate, got %v", err)
	}
	if _, err := patients.Register(Patient{Name: "amina   YUSUF", DOB: "1990-01-01", Phone: "+234802", FacilityID: "F2"}); !errors.Is(err, ErrDuplicate) {
		t.Fatalf("name+dob dup: want ErrDuplicate, got %v", err)
	}
	if _, err := patients.Register(Patient{Name: "Musa Yusuf", DOB: "2015-03-03", Phone: "+234801", FacilityID: "F1"}); err != nil {
		t.Fatalf("family phone share should register: %v", err)
	}

	// SetKYC never downgrades, level persisted.
	if _, err := patients.SetKYC(a.ID, KYCNINVerified, "nimc ok"); err != nil {
		t.Fatal(err)
	}
	if got, _ := patients.SetKYC(a.ID, KYCPhone, "stale"); got.KYCLevel != KYCNINVerified {
		t.Fatalf("downgrade refused: %s", got.KYCLevel)
	}
	if got, _ := patients.GetPatient(a.ID); got.KYCDetail != "nimc ok" {
		t.Fatalf("detail persisted: %+v", got)
	}

	// OTP lifecycle: save, wrong attempt counted, right code consumes.
	expires := time.Now().Add(10 * time.Minute)
	if err := patients.SaveOTP(a.ID, otpHash("424242"), expires); err != nil {
		t.Fatal(err)
	}
	if err := patients.CheckOTP(a.ID, otpHash("000000"), time.Now()); !errors.Is(err, ErrOTPMismatch) {
		t.Fatalf("wrong code: want ErrOTPMismatch, got %v", err)
	}
	if err := patients.CheckOTP(a.ID, otpHash("424242"), time.Now()); err != nil {
		t.Fatalf("right code: %v", err)
	}
	if err := patients.CheckOTP(a.ID, otpHash("424242"), time.Now()); !errors.Is(err, ErrNotFound) {
		t.Fatalf("consumed code: want ErrNotFound, got %v", err)
	}
	// Lockout survives: 5 mismatches then even the right code is refused.
	_ = patients.SaveOTP(a.ID, otpHash("999999"), expires)
	for i := 0; i < otpMaxAttempts; i++ {
		_ = patients.CheckOTP(a.ID, otpHash("111111"), time.Now())
	}
	if err := patients.CheckOTP(a.ID, otpHash("999999"), time.Now()); !errors.Is(err, ErrOTPLocked) {
		t.Fatalf("locked: want ErrOTPLocked, got %v", err)
	}
}

func TestPostgresBiometrics(t *testing.T) {
	store, _ := pgTestStore(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	// Self-apply the biometrics migration, then a clean slate.
	_, file, _, _ := runtime.Caller(0)
	sql, err := os.ReadFile(filepath.Join(filepath.Dir(file), "..", "..", "db", "migrations", "0011_biometrics.sql"))
	if err != nil {
		t.Fatalf("read migration: %v", err)
	}
	if _, err := store.pool.Exec(ctx, string(sql)); err != nil {
		t.Fatalf("apply 0011: %v", err)
	}
	if _, err := store.pool.Exec(ctx, "TRUNCATE registry_biometrics"); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	bs := NewPostgresBiometricStore(store.pool)
	t1 := EnrolledTemplate{PatientID: "P-1", Finger: "right-index", Sealed: "PLAIN:AAAA", CreatedAt: time.Now().UTC().Format(time.RFC3339)}
	if err := bs.Enrol(t1); err != nil {
		t.Fatalf("enrol: %v", err)
	}
	// Idempotent re-enrol of the same patient+finger.
	if err := bs.Enrol(t1); err != nil {
		t.Fatalf("re-enrol: %v", err)
	}
	bs.Enrol(EnrolledTemplate{PatientID: "P-1", Finger: "left-thumb", Sealed: "PLAIN:BBBB", CreatedAt: t1.CreatedAt})
	bs.Enrol(EnrolledTemplate{PatientID: "P-2", Finger: "right-index", Sealed: "PLAIN:CCCC", CreatedAt: t1.CreatedAt})
	if got := len(bs.ForPatient("P-1")); got != 2 {
		t.Fatalf("ForPatient: %d rows, want 2", got)
	}
	if got := len(bs.ForPatient("P-1")); got == 2 && bs.ForPatient("P-1")[0].Finger != "left-thumb" {
		t.Fatalf("ForPatient ordered by finger: %+v", bs.ForPatient("P-1"))
	}
	if got := len(bs.All()); got != 3 {
		t.Fatalf("All: %d rows, want 3", got)
	}
	if got := bs.ForPatient("P-1")[0].Sealed; got != "PLAIN:BBBB" {
		t.Fatalf("sealed token round-trip: %q", got)
	}
	if got := bs.ForPatient("P-1")[0].CreatedAt; got == "" {
		t.Fatal("created_at lost in round-trip")
	}
}
