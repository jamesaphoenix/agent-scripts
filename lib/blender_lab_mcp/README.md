# Official Blender Lab MCP

The Blender Lab add-on and the community `blender-mcp` packages are different
implementations. They use different request framing, tool names and environment
variables even though the executable name and usual TCP port are the same.
Do not launch an unqualified `uvx blender-mcp` for Blender Lab.

This helper pins the official server to release **1.0.3**, commit
`2cea8d566dde07fbac28a61d698909d69724e853`, from
[Blender's own repository](https://projects.blender.org/lab/blender_mcp).
The Python package is in its `mcp/` subdirectory. This matches the installed
Blender Lab add-on 1.0.3. Review both versions together before updating the pin.

## Install and verify

Open Blender with the official add-on enabled. It is already installed on the
MacBook Pro. This helper does not download or replace add-ons.

From the agent-scripts checkout:

```sh
scripts/install-blender-lab-mcp.sh
```

The installer links the launcher into `~/.local/bin`, registers the named
`blender` stdio connection in Codex and user-scoped Claude Code, then tests each
saved client configuration independently. Other MCP entries are preserved.
Each client stores the pinned command itself (`uvx --from <official Git source
at the pinned commit> blender-mcp --transport stdio`) together with the bridge
host, port and Blender path, so neither client depends on where this checkout
lives and a removed worktree cannot break the connection. Rerun `configure`
after changing the pin or the bridge settings.
No host daemon, model provider, 1Password read or paid service is involved.
Restart an existing client session to refresh its discovered tool list.

For individual operations:

```sh
scripts/blender-lab-mcp doctor
scripts/blender-lab-mcp configure --client codex
scripts/blender-lab-mcp doctor --client both
```

`uv`/`uvx`, Python 3.13 for the health-check SDK, and the selected client CLIs must
be available. The first run installs public Python dependencies into uv's cache.
The ordinary server starts over stdio only, using the pinned Git source.

The official bridge settings are `BLENDER_MCP_HOST` and `BLENDER_MCP_PORT`,
defaulting to `127.0.0.1:9876`. The launcher accepts loopback hosts only. Change
the add-on preference and this port together if using another local Blender
instance. `BLENDER_PATH` selects the executable for official background tools;
on macOS it defaults to the Blender application bundle.

## What the doctor proves

The doctor uses the standard MCP Python client to initialise the configured
server, inspect its actual tool contract and call a real data-block summary.
It checks both MCP-level errors and the add-on's nested status, because an
`isError=false` response can still contain a bridge failure. It then observes
the Blender version and enabled official add-on. It never switches files,
saves preferences, edits objects or changes the active scene.

Successful output includes the source commit, release, scene name, object count
and server tool count. A socket listener or a successful configuration command
alone is insufficient evidence. The health check has a bounded timeout and
terminates its own MCP child process after use.

The official connector does not implement the community `BLENDER_MCP_SAFE_MODE`
flag. Scene-writing tools have access to Blender's Python API. Use an isolated
scene for authoring; keep reproducible asset generation in separate background
processes with `--factory-startup`. Do not run scripts that clear objects in a
user's unsaved live scene.

Project-scoped `.mcp.json` or `.codex/config.toml` entries can override a global
connection. Inspect those when a client still exposes community tool names such
as `get_scene_info` instead of Blender Lab's `get_blendfile_summary_datablocks`.
Do not terminate other sessions' MCP processes as an upgrade shortcut.

Checks without Blender or network:

```sh
python3 -B -m unittest discover -s tests -p test_blender_lab_mcp.py
```
