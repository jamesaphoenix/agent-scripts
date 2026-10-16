import ast
import datetime
import json
import os
import subprocess
import unittest
import tempfile
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
        namespace = {"Any": object, "ROOT": ROOT, "os": os, 'json':json,
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
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ['/bin/bash', '-c'])
        self.assertIn('run_op vault list --format json', command[2])
        self.assertIn('OP_CALL_TIMEOUT_SECONDS=20', command[2])
        self.assertIn('trap op_cli_cleanup_isolated_home EXIT', command[2])
        self.assertEqual(command[-1], str(ROOT / 'lib/op-cli.sh'))
        self.assertNotIn('synthetic-test-token', ' '.join(command))
        self.assertEqual(run.call_args.kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['timeout'], 50)

    @patch.dict(os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-test-token"})
    @patch("shutil.which", return_value="/test/op")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(['bounded-op-check'], 50))
    def test_authentication_timeout_stops_without_falling_back_to_keychain_writes(self, run, which):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.call({'opServiceAccountSource': 'profile-or-keychain'})
        run.assert_called_once()

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

    @patch.dict(os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-test-token"})
    @patch('subprocess.run')
    def test_recorded_rate_limit_defers_authentication_to_the_runtime_without_another_request(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cooldown.json';now=datetime.datetime.now(datetime.timezone.utc)
            path.write_text(json.dumps({'observedAt':now.isoformat(),'nextAttemptUnix':now.timestamp()+1800,
                'reason':'Verified 1Password rate limit','paidRequestSubmitted':False}))
            self.call({'opServiceAccountSource':'profile-or-keychain','credentialCooldownPath':str(path)})
        run.assert_not_called()

    @patch.dict(os.environ, {"OP_SERVICE_ACCOUNT_TOKEN": "synthetic-test-token"})
    @patch('subprocess.run',side_effect=subprocess.CalledProcessError(1,['op']))
    def test_auth_failure_is_not_a_reason_to_skip_installation_authentication(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cooldown.json';now=datetime.datetime.now(datetime.timezone.utc)
            path.write_text(json.dumps({'observedAt':now.isoformat(),'nextAttemptUnix':now.timestamp()+1800,
                'reason':'Authentication failure','paidRequestSubmitted':False}))
            with self.assertRaises(subprocess.CalledProcessError):
                self.call({'opServiceAccountSource':'profile-or-keychain','credentialCooldownPath':str(path)})
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
