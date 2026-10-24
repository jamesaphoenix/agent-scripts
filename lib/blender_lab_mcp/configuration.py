"""Register only the named Blender connection, preserving unrelated MCP servers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import re
import stat
import tempfile
import tomllib
from . import runtime


def set_codex_timeout(path):
    """Retain comments and unrelated settings while allowing a cold uv startup."""
    path = Path(path).resolve()
    original = path.read_text()
    section = re.search(r"(?ms)^\[mcp_servers\.blender\][ \t]*\n(.*?)(?=^\[|\Z)", original)
    if section is None:
        raise RuntimeError("Codex did not save its Blender server configuration")
    body = section.group(1)
    if re.search(r"(?m)^startup_timeout_sec\s*=", body):
        body = re.sub(r"(?m)^startup_timeout_sec\s*=.*$", "startup_timeout_sec = 60", body)
    else:
        body = "startup_timeout_sec = 60\n" + body
    updated = original[:section.start(1)] + body + original[section.end(1):]
    tomllib.loads(updated)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".blender-mcp-", delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), stat.S_IMODE(path.stat().st_mode))
            output.write(updated)
        if path.read_text() != original:
            raise RuntimeError("Codex configuration changed concurrently; retry configuration")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def configure(clients=("codex", "claude")):
    """Store the pinned official command itself, so no client depends on where this checkout lives."""
    launch = runtime.command()
    env = runtime.environment()
    settings = [f"{name}={env[name]}" for name in runtime.CLIENT_VARIABLES]
    configured = []
    for client in clients:
        executable = shutil.which(client)
        if not executable:
            raise RuntimeError(f"{client} CLI is not installed")
        scope = ["--scope", "user"] if client == "claude" else []
        flag = "--env" if client == "codex" else "-e"
        previous = subprocess.run([executable, "mcp", "get", "blender"], capture_output=True, text=True)
        if previous.returncode == 0:
            removed = subprocess.run([executable, "mcp", "remove", *scope, "blender"], capture_output=True)
            if removed.returncode:
                raise RuntimeError(f"Could not replace the {client} Blender connection")
        variables = [argument for setting in settings for argument in (flag, setting)]
        added = subprocess.run([executable, "mcp", "add", *scope, "blender", *variables, "--", *launch], capture_output=True)
        if added.returncode:
            raise RuntimeError(f"Could not register the {client} Blender connection")
        if client == "codex":
            set_codex_timeout(Path.home() / ".codex/config.toml")
        configured.append(client)
    return {"configured": configured, "server": "blender", "command": launch, "environment": dict(zip(runtime.CLIENT_VARIABLES, [env[n] for n in runtime.CLIENT_VARIABLES]))}


def configured_command(client):
    if client == "codex":
        result = subprocess.run([client, "mcp", "get", "blender", "--json"], check=True, capture_output=True, text=True)
        value = json.loads(result.stdout)
        if value.get("enabled") is False:
            raise RuntimeError("Codex Blender connection is disabled")
        transport = value.get("transport", value)
        return [transport["command"], *(transport.get("args") or [])], transport.get("env") or {}
    # Read only this user-scoped entry, never print unrelated configuration or credentials.
    config = json.loads((Path.home() / ".claude.json").read_text())
    value = config.get("mcpServers", {}).get("blender")
    if not value or value.get("type", "stdio") != "stdio":
        raise RuntimeError("Claude Code user-scoped Blender stdio connection is missing")
    return [value["command"], *(value.get("args") or [])], value.get("env") or {}
