import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("jud_vat", Path(__file__).resolve().parents[1] / "lib/jud_vat.py")
vat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vat)

class VatPreparationTests(unittest.TestCase):
    def test_company_rejects_other_entity_even_with_same_name(self):
        vat.validate_company({"name": "Just Understanding Data Ltd", "id": 486529, "subdomain": "jus001"})
        for company in [{"name": "Brightpool Learning Ltd", "id": 486529, "subdomain": "jus001"},
                        {"name": "Just Understanding Data Ltd", "id": 1, "subdomain": "jus001"}]:
            with self.assertRaises(ValueError): vat.validate_company(company)

    def test_exact_host_and_account_selection(self):
        row = {"website_host": "jus001.freeagent.com", "username": "james", "vault_id": "v", "item_id": "1"}
        index = {"entries": [row, dict(row), dict(row, website_host="brightpool.freeagent.com", item_id="2")]}
        self.assertEqual(vat.select_logins(index, "jus001.freeagent.com", "james"), [row])
        self.assertEqual(vat.select_logins(index, "freeagent.com"), [])
        self.assertEqual(vat.select_logins(index, "jus001.freeagent.com", "other"), [])

    def test_invalid_period_rejected(self):
        with self.assertRaises(ValueError): vat.period("2026-08-31", "2026-06-01")

    def test_snapshot_private_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            vat.private_json(path, {"original": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): vat.private_json(path, {"original": False})
            self.assertEqual(json.loads(path.read_text()), {"original": True})

    def test_wrong_return_period_stops_before_account_reads(self):
        class Api:
            def api(self, method, path):
                assert path == "/vat_returns/2026-08-31"
                return {"vat_return": {"period_starts_on": "2026-07-01", "period_ends_on": "2026-08-31"}}
        with self.assertRaises(ValueError):
            vat.snapshot(Api(), {}, "2026-06-01", "2026-08-31", Path("must-not-exist.json"))

if __name__ == "__main__": unittest.main()
