import json, glob, subprocess, sys, os
from tenantguard import audit_migrations, check_row_isolation

REPO = os.path.join(os.path.dirname(__file__), "..", "..")

def test_real_migrations_audit():
    files = glob.glob(os.path.join(REPO, "db/migrations/*.sql"))
    out = audit_migrations(files)
    assert out["tables_seen"] > 0
    majors = [f for f in out["findings"] if f["severity"] == "major"]
    assert not majors, f"cross-tenant leakage risk: {majors}"

def test_audit_catches_unscoped_table(tmp_path):
    f = tmp_path / "0001.sql"
    f.write_text("CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, amount NUMERIC);")
    out = audit_migrations([str(f)])
    assert not out["isolated"]

def test_audit_passes_scoped_table(tmp_path):
    f = tmp_path / "0001.sql"
    f.write_text("CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, facility_id TEXT NOT NULL, amount NUMERIC);")
    assert audit_migrations([str(f)])["isolated"]

def test_row_isolation_guard():
    rows = [{"id": 1, "facility_id": "F1"}, {"id": 2, "facility_id": "F2"}]
    leaks = check_row_isolation(rows, "F1")
    assert leaks == [{"id": 2, "facility_id": "F2"}]
    assert check_row_isolation(rows[:1], "F1") == []

def test_cli_exit_code_on_leak(tmp_path):
    f = tmp_path / "0001.sql"
    f.write_text("CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY);")
    p = subprocess.run([sys.executable, "tenantguard.py"],
        input=json.dumps({"sql_files": [str(f)]}), capture_output=True, text=True,
        cwd=os.path.dirname(__file__))
    assert p.returncode == 1
