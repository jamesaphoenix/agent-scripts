import ast
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/install-launchd-tasks.sh").read_text()
embedded = source.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
function = next(
    node for node in ast.parse(embedded).body
    if isinstance(node, ast.FunctionDef) and node.name == "ensure_op_service_account"
)


class ServiceAccountInstallationTests(unittest.TestCase):
    def call(self, task):
        import shutil
        namespace = {"Any": object, "ROOT": ROOT, "os": os,
                     "shutil": shutil, "subprocess": subprocess}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "installer", "exec"), namespace)
        namespace["ensure_op_service_account"](task)

    @patch.dict(os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-test-token"})
    @patch("subprocess.run")
    def test_existing_jobs_still_require_the_keychain_gate(self, run):
        self.call({"requiresOpServiceAccount": True})
        self.assertEqual(run.call_args.args[0][-1], "--ensure")
        self.assertIn("cache-op-service-account-token.sh", run.call_args.args[0][0])

    @patch.dict(os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-test-token"})
    @patch("shutil.which", return_value="/test/op")
    @patch("subprocess.run")
    def test_profile_wrapper_authenticates_without_keychain_writes(self, run, which):
        self.call({"opServiceAccountSource": "profile-or-keychain"})
        run.assert_called_once_with(
            ["/test/op", "vault", "list", "--format", "json"],
            check=True, stdout=subprocess.DEVNULL,
        )

    @patch.dict(os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-test-token"})
    @patch("shutil.which", return_value="/test/op")
    @patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["op"]))
    def test_invalid_profile_authentication_stops_installation(self, run, which):
        with self.assertRaises(subprocess.CalledProcessError):
            self.call({"opServiceAccountSource": "profile-or-keychain"})

    @patch.dict(os.environ, {}, clear=True)
    @patch("subprocess.run")
    def test_missing_profile_token_keeps_the_keychain_fallback(self, run):
        self.call({"opServiceAccountSource": "profile-or-keychain"})
        self.assertEqual(run.call_args.args[0][-1], "--ensure")


if __name__ == "__main__":
    unittest.main()
