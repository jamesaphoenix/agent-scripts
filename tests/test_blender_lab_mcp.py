"""No Blender, network, provider or user configuration required for these checks."""
import json
from pathlib import Path
import sys
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from blender_lab_mcp import configuration, doctor, runtime


class BlenderRuntimeTest(unittest.TestCase):
    def test_pins_official_source_and_forces_stdio(self):
        with patch.object(runtime.shutil, "which", return_value="/tools/uvx"):
            value = runtime.command()
        self.assertEqual(value[:2], ["/tools/uvx", "--from"])
        self.assertIn("https://projects.blender.org/lab/blender_mcp.git@", value[2])
        self.assertIn(runtime.COMMIT, value[2])
        self.assertTrue(value[2].endswith("#subdirectory=mcp"))
        self.assertEqual(value[-3:], ["blender-mcp", "--transport", "stdio"])

    def test_uses_official_environment_names_and_preserves_a_selected_local_port(self):
        env = runtime.environment({"BLENDER_HOST": "other-host", "BLENDER_MCP_PORT": "9877"})
        self.assertEqual(env["BLENDER_MCP_HOST"], "127.0.0.1")
        self.assertEqual(env["BLENDER_MCP_PORT"], "9877")
        self.assertTrue(env["BLENDER_PATH"])

    def test_rejects_remote_or_malformed_bridge_configuration(self):
        for overrides in [{"BLENDER_MCP_HOST": "0.0.0.0"}, {"BLENDER_MCP_HOST": "remote.example"},
                          {"BLENDER_MCP_PORT": "invalid"}, {"BLENDER_MCP_PORT": "80"}]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                runtime.environment(overrides)

    def test_gui_system_path_finds_user_installed_uvx(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary = home / ".local/bin/uvx"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o700)
            with patch.object(runtime.shutil, "which", return_value=None), patch.object(runtime.Path, "home", return_value=home):
                self.assertEqual(runtime.command()[0], str(binary))

    def test_missing_uvx_is_a_clear_failure(self):
        with patch.object(runtime, "executable", return_value=None), self.assertRaisesRegex(RuntimeError, "uvx"):
            runtime.command()


class BlenderHealthTest(unittest.TestCase):
    def response(self, value, error=False, structured=True):
        return SimpleNamespace(isError=error, structuredContent=value if structured else None,
                               content=[SimpleNamespace(type="text", text=json.dumps(value))])

    def test_reads_successful_standard_structured_and_text_results(self):
        expected = {"scene_name": "Scene", "datablock_counts": {"objects": 3}}
        for structured in [True, False]:
            self.assertEqual(doctor.unwrap(self.response({"status": "ok", "result": expected}, structured=structured)), expected)

    def test_failure_content_is_not_mistaken_for_success_when_mcp_error_flag_is_false(self):
        for value, error in [({"status": "error", "message": "bridge unavailable"}, False),
                             ({"status": "ok", "result": {}}, True), ({"unrelated": "payload"}, False)]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                doctor.unwrap(self.response(value, error))

    def test_codex_null_environment_is_valid_and_does_not_break_client_verification(self):
        response = SimpleNamespace(stdout=json.dumps({"transport": {"command": "/launcher", "args": ["serve"], "env": None}}))
        with patch.object(configuration.subprocess, "run", return_value=response):
            self.assertEqual(configuration.configured_command("codex"), (["/launcher", "serve"], {}))

    def test_a_disabled_codex_connection_is_not_reported_as_ready(self):
        response = SimpleNamespace(stdout=json.dumps({"enabled": False, "transport": {"command": "/launcher"}}))
        with patch.object(configuration.subprocess, "run", return_value=response), self.assertRaisesRegex(RuntimeError, "disabled"):
            configuration.configured_command("codex")

    def test_startup_timeout_preserves_unrelated_servers_comments_and_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            source = '# local comment\nmodel = "existing-model"\n[mcp_servers.blender]\ncommand = "/launcher"\n[mcp_servers.other]\nurl = "https://example.com/mcp"\n'
            path.write_text(source)
            path.chmod(0o600)
            configuration.set_codex_timeout(path)
            configuration.set_codex_timeout(path)
            self.assertTrue(path.read_text().startswith('# local comment\nmodel = "existing-model"'))
            value = tomllib.loads(path.read_text())
            self.assertEqual(value["mcp_servers"]["blender"]["startup_timeout_sec"], 60)
            self.assertEqual(value["mcp_servers"]["other"]["url"], "https://example.com/mcp")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
