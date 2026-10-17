import os
import tempfile
import textwrap
import unittest

from costguard import check, parse_cpu, parse_mem, scan


MANIFEST = textwrap.dedent("""\
apiVersion: apps/v1
kind: Deployment
metadata: { name: alpha }
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: alpha
          resources:
            requests: { cpu: 250m, memory: 128Mi }
            limits: { cpu: 500m, memory: 256Mi }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: beta }
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: beta
          resources:
            requests: { cpu: "1", memory: 1Gi }
---
apiVersion: v1
kind: Service
metadata: { name: ignored }
spec: {}
""")


class CostguardTest(unittest.TestCase):
    def test_unit_parsers(self):
        self.assertAlmostEqual(parse_cpu("500m"), 0.5)
        self.assertAlmostEqual(parse_cpu("2"), 2.0)
        self.assertAlmostEqual(parse_mem("256Mi"), 0.25)
        self.assertAlmostEqual(parse_mem("1Gi"), 1.0)
        self.assertAlmostEqual(parse_cpu(None), 0.0)

    def test_scan_priced_deployments_only(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "m.yaml"), "w").write(MANIFEST)
            costs = scan(d)
        self.assertEqual([c["name"] for c in costs], ["alpha", "beta"])
        alpha = costs[0]
        # 2 replicas × (0.25 vCPU × $12 + 0.125 GB × $1.8) = 2 × $3.225
        self.assertAlmostEqual(alpha["usd_month"], 6.45)
        beta = costs[1]
        self.assertAlmostEqual(beta["usd_month"], round(1 * 12 + 1 * 1.8, 2))

    def test_budget_gate(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "m.yaml"), "w").write(MANIFEST)
            _, total, ok = check(d, budget_usd=100)
            self.assertTrue(ok)
            _, total2, ok2 = check(d, budget_usd=5)
            self.assertFalse(ok2)
            self.assertEqual(total, total2)

    def test_real_repo_manifests_priced(self):
        repo = os.path.join(os.path.dirname(__file__), "..", "..")
        costs = scan(os.path.join(repo, "infra", "k8s"))
        names = {c["name"] for c in costs}
        # the nine platform deployments must all be priced
        self.assertIn("claims-orchestrator", names)
        self.assertIn("wallet-service", names)
        self.assertTrue(all(c["usd_month"] > 0 for c in costs))


if __name__ == "__main__":
    unittest.main()
