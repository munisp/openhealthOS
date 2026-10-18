import unittest

from billing import BillingOffice, Error


class TestBilling(unittest.TestCase):
    def setUp(self):
        self.office = BillingOffice("FAC-1")

    def _bill(self, insurance=0):
        b = self.office.open_bill("PAT-1", insurance_pct=insurance)
        b.add_line("CONSULT", "consultation", 200_000)      # ₦2,000
        b.add_line("LAB-FBC", "full blood count", 350_000)  # ₦3,500
        b.add_line("CARD", "patient card", 50_000, exemptible=False)
        return b

    def test_itemized_totals(self):
        b = self._bill()
        self.assertEqual(b.total_kobo(), 600_000)
        self.assertEqual(b.balance_kobo(), 600_000)

    def test_part_payment_and_settlement(self):
        b = self._bill()
        b.pay(600_000, "cash", "cashier-1")
        self.assertEqual(b.state, "settled")
        self.assertEqual(b.balance_kobo(), 0)

    def test_deposit_does_not_settle(self):
        b = self._bill()
        b.pay(600_000, "pos", "cashier-1", kind="deposit")
        self.assertEqual(b.state, "open")  # deposits are held, not receipted

    def test_overpay_never_negative_balance(self):
        b = self._bill()
        b.pay(700_000, "cash", "cashier-1")
        self.assertEqual(b.balance_kobo(), 0)

    def test_exemption_zeroes_exemptible_lines_only(self):
        b = self._bill()
        b.apply_exemption("under5", "medical-officer-1")
        self.assertEqual(b.balance_kobo(), 50_000)  # only the card remains

    def test_exemption_applies_to_later_lines_too(self):
        b = self._bill()
        b.apply_exemption("pregnant", "mo-1")
        b.add_line("SCAN", "obstetric scan", 500_000)
        self.assertEqual(b.receipt()["lines"][-1]["exempted"], True)

    def test_waiver_partial_and_full(self):
        b = self._bill()
        self.assertEqual(b.waive(400_000, "sw-1", "destitute — social work assessment"), 400_000)
        self.assertEqual(b.balance_kobo(), 200_000)
        self.assertEqual(b.waive(999_999, "sw-1", "full waiver"), 200_000)
        self.assertEqual(b.balance_kobo(), 0)

    def test_insurance_split(self):
        b = self._bill(insurance=90)
        self.assertEqual(b.insurer_due_kobo(), 540_000)
        self.assertEqual(b.patient_due_kobo(), 60_000)

    def test_no_anonymous_money(self):
        b = self._bill()
        with self.assertRaises(Error):
            b.pay(100, "cash", "")
        with self.assertRaises(Error):
            b.waive(100, "", "reason")
        with self.assertRaises(Error):
            b.apply_exemption("indigent", "")
        with self.assertRaises(Error):
            self.office.write_off(b.id, "admin", "")

    def test_integer_money_only(self):
        b = self._bill()
        with self.assertRaises(Error):
            b.add_line("X", "negative", -5)
        with self.assertRaises(Error):
            b.pay(-100, "cash", "c1")

    def test_office_views(self):
        b1 = self._bill()
        b1.pay(100_000, "cash", "c1")
        self._bill()
        self.assertEqual(self.office.daily_revenue(), 100_000)
        self.assertEqual(len(self.office.outstanding()), 2)


if __name__ == "__main__":
    unittest.main()
