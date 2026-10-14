import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("fleet_credentials", Path(__file__).parents[1] / "lib/docker-credential-jud-gcr.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class RegistryCredentialsTest(unittest.TestCase):
    def run_operation(self, operation, data):
        with patch("sys.argv", ["helper", operation]), patch("sys.stdin", io.StringIO(data)):
            helper.main()

    def test_ephemeral_login_does_not_replace_adc(self):
        with patch.object(helper.subprocess, "run") as command:
            self.run_operation("store", '{"ServerURL":"europe-west3-docker.pkg.dev","Username":"oauth2accesstoken","Secret":"test-ephemeral"}')
            command.assert_not_called()

    def test_unknown_registry_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_operation("get", "https://example.com")

    def test_get_uses_official_helper_and_fixed_adc(self):
        with patch.object(helper.subprocess, "run") as command:
            self.run_operation("get", helper.REGISTRY)
            self.assertEqual(command.call_args.args[0], [helper.HELPER, "get"])
            self.assertEqual(command.call_args.kwargs["env"]["GOOGLE_APPLICATION_CREDENTIALS"], "/root/.config/gcloud/application_default_credentials.json")
            self.assertEqual(command.call_args.kwargs["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
