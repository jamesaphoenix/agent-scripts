"""The server is started on demand by each MCP client, never as a host daemon."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from .runtime import command, environment, executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", "doctor", "configure"], nargs="?", default="serve")
    parser.add_argument("--client", choices=["direct", "codex", "claude", "both"], default="direct")
    parser.add_argument("--sdk-runtime", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        env = environment()
        if args.action == "serve":
            launch = command()
            os.execvpe(launch[0], launch, env)
        if args.action == "configure":
            from .configuration import configure
            clients = ("codex", "claude") if args.client in {"direct", "both"} else (args.client,)
            value = configure(clients)
        else:
            if not args.sdk_runtime:
                uv = executable("uv")
                if not uv:
                    raise RuntimeError("uv is required for the standard MCP client health check")
                launch = [uv, "run", "--no-project", "--python", "3.13", "--with", "mcp==1.26.0",
                          str(Path(__file__).resolve().parents[2] / "scripts/blender-lab-mcp"), "doctor",
                          "--client", args.client, "--sdk-runtime"]
                return subprocess.run(launch, env=env, timeout=120).returncode
            from .doctor import doctor
            from .configuration import configured_command
            clients = ("codex", "claude") if args.client == "both" else (args.client,)
            value = {}
            for client in clients:
                launch, extra = (None, {}) if client == "direct" else configured_command(client)
                value[client] = doctor(launch, {**env, **extra})
        print(json.dumps(value, indent=2))
        return 0
    except Exception as error:
        # SDK exception groups can contain upstream responses. Keep errors concise and local.
        print(f"blender-lab-mcp: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
