"""recipes.py — the ONE recipe book for regenerating every registered
evidence payload (ADR-0081).

The freshness gate (test_proofindex.TestExporterFreshness) and the
refresh rail (refresh.py) both read THIS module — a gate and its fix
must never drift apart. Two kinds:

- "file": the write snippet regenerates the committed file(s) in place
  (the demo's main() writes its own OUT; appmarket/charting/portal take
  an explicit out path).
- "hash": the write snippet exports into the tree; the probe snippet
  exports into a tmpdir and prints one sha256 per file in
  sorted-filename order (used by the gate for read-only comparison).

Stdlib only (ADR-0005). This module is DATA; it executes nothing.
"""

RECIPES = [
    {'name': 'tamperproof', 'package': 'packages/travelpass', 'kind': 'file', 'write': "__import__('demo_tamper').main()", 'paths': ['apps/pwa/tamperproof.json']},
    {'name': 'deployproof', 'package': 'packages/travelpass', 'kind': 'file', 'write': "__import__('demo_deploy').main()", 'paths': ['apps/pwa/deployproof.json']},
    {'name': 'anchorproof', 'package': 'packages/ledgeranchor', 'kind': 'file', 'write': "__import__('demo_ledgeranchor').main()", 'paths': ['apps/pwa/anchorproof.json']},
    {'name': 'shipproof', 'package': 'packages/shipgate', 'kind': 'file', 'write': "__import__('demo_shipgate').main()", 'paths': ['apps/pwa/shipproof.json']},
    {'name': 'inferproof', 'package': 'packages/inferlock', 'kind': 'file', 'write': "__import__('demo_inferlock').main()", 'paths': ['apps/pwa/inferproof.json']},
    {'name': 'kgproof', 'package': 'packages/kgraph', 'kind': 'file', 'write': "__import__('demo_kgraph').main()", 'paths': ['apps/pwa/kgproof.json']},
    {'name': 'geoproof', 'package': 'packages/geolock', 'kind': 'file', 'write': "__import__('demo_geolock').main()", 'paths': ['apps/pwa/geoproof.json']},
    {'name': 'geointproof', 'package': 'packages/geoint', 'kind': 'file', 'write': "__import__('demo_geoint').main()", 'paths': ['apps/pwa/geointproof.json']},
    {'name': 'geodesyproof', 'package': 'packages/geodesy', 'kind': 'file', 'write': "__import__('demo_geodesy').main()", 'paths': ['apps/pwa/geodesyproof.json']},
    {'name': 'geoexportproof', 'package': 'packages/geoexport', 'kind': 'file', 'write': "__import__('demo_geoexport').main()", 'paths': ['apps/pwa/geoexportproof.json']},
    {'name': 'geostructureproof', 'package': 'packages/geostructure', 'kind': 'file', 'write': "__import__('demo_geostructure').main()", 'paths': ['apps/pwa/geostructureproof.json']},
    {'name': 'georuntimeproof', 'package': 'packages/georuntime', 'kind': 'file', 'write': "__import__('demo_georuntime').main()", 'paths': ['apps/pwa/georuntimeproof.json']},
    {'name': 'idseamproof', 'package': 'packages/idseam', 'kind': 'file', 'write': "__import__('demo_idseam').main()", 'paths': ['apps/pwa/idseamproof.json']},
    {'name': 'freshproof', 'package': 'packages/freshproof', 'kind': 'file', 'write': "__import__('demo_freshproof').main()", 'paths': ['apps/pwa/freshproof.json']},
    {'name': 'refreshproof', 'package': 'packages/refreshproof', 'kind': 'file', 'write': "__import__('demo_refreshproof').main()", 'paths': ['apps/pwa/refreshproof.json']},
    {'name': 'shellproof', 'package': 'packages/shellproof', 'kind': 'file', 'write': "__import__('demo_shellproof').main()", 'paths': ['apps/pwa/shellproof.json']},
    {'name': 'adrproof', 'package': 'packages/adrchain', 'kind': 'file', 'write': "__import__('demo_adrchain').main()", 'paths': ['apps/pwa/adrproof.json']},
    {'name': 'ledgerproof', 'package': 'packages/ledgergate', 'kind': 'file', 'write': "__import__('demo_ledgergate').main()", 'paths': ['apps/pwa/ledgerproof.json']},
    {'name': 'agentwrite', 'package': 'packages/agentwrite', 'kind': 'file', 'write': "__import__('demo_agentwrite').main()", 'paths': ['apps/pwa/agentwrite.json']},
    {'name': 'appmarket', 'package': 'packages/appmarket', 'kind': 'file', 'write': "import os; import demo_appmarket as d; d.export_appmarket(d.seed_appmarket(), os.path.join(os.getcwd(), '..', '..', 'apps', 'pwa', 'appmarket.json'))", 'paths': ['apps/pwa/appmarket.json']},
    {'name': 'charting', 'package': 'packages/charting', 'kind': 'file', 'write': "import os; import demo_charting as d; d.export_charting(d.seed_charting(), os.path.join(os.getcwd(), '..', '..', 'apps', 'pwa', 'charting.json'))", 'paths': ['apps/pwa/charting.json']},
    {'name': 'portal', 'package': 'packages/ips', 'kind': 'file', 'write': "import os; import demo_portal as d; d.export_record(os.path.join(os.getcwd(), '..', '..', 'apps', 'patient', 'record.json'))", 'paths': ['apps/patient/record.json']},
    {'name': 'dashboards', 'package': 'packages/travelpass', 'kind': 'hash', 'write': "import os, sys; _h = os.getcwd(); [sys.path.insert(0, os.path.abspath(os.path.join(_h, '..', p))) for p in ('crosslink', 'drugkb', 'surveillance', 'poe_screen', 'steward', 'medrec', 'inpatient', 'terminology', 'mammoprog', 'offlinespec', 'teachfile', 'formulary', 'earlywarn', 'cervixscreen', 'usguard', 'callrecall', 'sepsis', 'teleicu', 'acuity', 'telepath', 'teleread', 'reportkit', 'pacs', 'fertility', 'rota', 'triggers', 'vitals', 'mdt')]; from vhpilot import seed_pilot; from vhdash import export_dashboards; export_dashboards(seed_pilot(seed=42)['registries'], os.path.abspath(os.path.join(os.getcwd(), '..', '..', 'apps', 'pwa')))", 'probe': "import tempfile, glob, hashlib, os, sys; _h = os.getcwd(); [sys.path.insert(0, os.path.abspath(os.path.join(_h, '..', p))) for p in ('crosslink', 'drugkb', 'surveillance', 'poe_screen', 'steward', 'medrec', 'inpatient', 'terminology', 'mammoprog', 'offlinespec', 'teachfile', 'formulary', 'earlywarn', 'cervixscreen', 'usguard', 'callrecall', 'sepsis', 'teleicu', 'acuity', 'telepath', 'teleread', 'reportkit', 'pacs', 'fertility', 'rota', 'triggers', 'vitals', 'mdt')]; from vhpilot import seed_pilot; from vhdash import export_dashboards; td = tempfile.mkdtemp(); export_dashboards(seed_pilot(seed=42)['registries'], td); print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() for f in sorted(glob.glob(td + '/*.json'))))", 'paths': ['apps/pwa/vhc-br10.json', 'apps/pwa/vhc-fund.json', 'apps/pwa/vhc-helpdesk.json']},
    {'name': 'growth', 'package': 'packages/travelpass', 'kind': 'hash', 'write': "import os; from vhdashx import seed_growth, export_growth; export_growth(seed_growth(), os.path.abspath(os.path.join(os.getcwd(), '..', '..', 'apps', 'pwa')))", 'probe': "import tempfile, glob, hashlib; from vhdashx import seed_growth, export_growth; td = tempfile.mkdtemp(); export_growth(seed_growth(), td); print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() for f in sorted(glob.glob(td + '/*'))) )", 'paths': ['apps/pwa/vhc-growth.json']},
    {'name': 'emr', 'package': 'packages/emrdemo', 'kind': 'hash', 'write': "import os; __import__('demo_emr').export_all(os.path.abspath(os.path.join(os.getcwd(), '..', '..', 'apps', 'pwa')), seed=42)", 'probe': "import tempfile, glob, hashlib; td = tempfile.mkdtemp(); __import__('demo_emr').export_all(td, seed=42); print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() for f in sorted(glob.glob(td + '/*.json'))))", 'paths': ['apps/pwa/inpatient.cv.json', 'apps/pwa/inpatient.ng.json', 'apps/pwa/specialty.cv.json', 'apps/pwa/specialty.ng.json']},
    {'name': 'national', 'package': 'packages/natdemo', 'kind': 'hash', 'write': "import os; __import__('demo_national').export_all(os.path.abspath(os.path.join(os.getcwd(), '..', '..', 'apps', 'pwa')))", 'probe': "import tempfile, glob, hashlib; td = tempfile.mkdtemp(); __import__('demo_national').export_all(td); print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() for f in sorted(glob.glob(td + '/*.json'))))", 'paths': ['apps/pwa/national.cv.json', 'apps/pwa/national.ng.json']},
    {'name': 'sync', 'package': 'packages/syncmesh', 'kind': 'hash', 'write': "import os; __import__('demo_sync').export_all(os.path.abspath(os.path.join(os.getcwd(), '..', '..', 'apps', 'pwa')))", 'probe': "import tempfile, glob, hashlib; td = tempfile.mkdtemp(); __import__('demo_sync').export_all(td); print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() for f in sorted(glob.glob(td + '/*.json'))))", 'paths': ['apps/pwa/sync.cv.json', 'apps/pwa/sync.ng.json']},
]
