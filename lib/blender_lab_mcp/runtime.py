"""Keep the Blender Lab source distinct from unrelated PyPI blender-mcp packages."""
import os
from pathlib import Path
import shutil

RELEASE = "1.0.3"
COMMIT = "2cea8d566dde07fbac28a61d698909d69724e853"
SOURCE = f"git+https://projects.blender.org/lab/blender_mcp.git@{COMMIT}#subdirectory=mcp"
OFFICIAL_TOOLS = {"execute_blender_code", "get_blendfile_summary_datablocks", "get_python_api_docs", "search_manual_docs"}


def environment(source=None):
    env = dict(os.environ if source is None else source)
    host = env.get("BLENDER_MCP_HOST", "127.0.0.1")
    if host not in {"localhost", "127.0.0.1"}:
        raise ValueError("The official connector requires IPv4 loopback: localhost or 127.0.0.1")
    try:
        port = int(env.get("BLENDER_MCP_PORT", "9876"))
    except ValueError as error:
        raise ValueError("Blender MCP port must be an integer") from error
    if not 1024 <= port <= 65535:
        raise ValueError("Blender MCP port must be between 1024 and 65535")
    # The official connector uses these names, not the community BLENDER_HOST/PORT.
    env["BLENDER_MCP_HOST"] = host
    env["BLENDER_MCP_PORT"] = str(port)
    candidate = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    env.setdefault("BLENDER_PATH", str(candidate) if candidate.is_file() else "blender")
    return env


def command():
    uvx = shutil.which("uvx")
    if not uvx:
        raise RuntimeError("uvx is required to launch the official Blender Lab MCP server")
    return [uvx, "--from", SOURCE, "blender-mcp", "--transport", "stdio"]
