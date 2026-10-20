import json, subprocess, sys, os
from outbreak import detect_anomalies, cluster

def test_flat_baseline_no_alerts():
    assert detect_anomalies([10] * 30) == []

def test_spike_alerts():
    series = [10] * 20 + [45]
    a = detect_anomalies(series)
    assert len(a) == 1 and a[0]["count"] == 45

def test_min_count_guards_noise():
    series = [0] * 20 + [3]  # tiny absolute number — no alert
    assert detect_anomalies(series) == []

def test_cluster_detection():
    cases = [{"lat": 9.07, "lng": 7.49}, {"lat": 9.071, "lng": 7.491},
             {"lat": 9.072, "lng": 7.489}, {"lat": 6.45, "lng": 3.39}]
    c = cluster(cases, radius_km=5, min_cases=3)
    assert len(c) == 1 and c[0]["size"] == 3

def test_no_cluster_when_sparse():
    cases = [{"lat": 9.07, "lng": 7.49}, {"lat": 6.45, "lng": 3.39}]
    assert cluster(cases) == []

def test_cli_fail_loud():
    p = subprocess.run([sys.executable, "outbreak.py"],
        input=json.dumps({"series": [10]*20 + [50]}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert p.returncode == 1
