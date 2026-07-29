from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class XeroExportSqlTests(unittest.TestCase):
    def test_supplier_bill_columns_are_qualified(self):
        source = (ROOT / "jobhub_enterprise.py").read_text(encoding="utf-8")
        start = source.index('"supplier_bills.csv": _query(')
        end = source.index('"approved_timesheets.csv": _query(', start)
        query = source[start:end]

        for column in (
            "invoice_no",
            "supplier",
            "invoice_date",
            "due_date",
            "subtotal_ex_gst",
            "status",
            "notes",
        ):
            self.assertIn(f"si.{column}", query)
        self.assertNotIn(' subtotal_ex_gst AS "UnitAmount"', query)
        self.assertNotIn(' status AS "Status"', query)


if __name__ == "__main__":
    unittest.main()
