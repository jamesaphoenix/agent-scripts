"""Read-only end-to-end checks through the standard MCP Python client."""
import asyncio
import json
from datetime import timedelta
from .runtime import COMMIT, OFFICIAL_TOOLS, RELEASE, command, environment


def unwrap(response):
    if response.isError:
        raise RuntimeError("Blender tool returned an MCP error")
    value = response.structuredContent
    if value is None:
        blocks = [item.text for item in response.content if getattr(item, "type", None) == "text"]
        if len(blocks) != 1:
            raise RuntimeError("Blender tool did not return structured data")
        value = json.loads(blocks[0])
    if not isinstance(value, dict) or value.get("status") != "ok":
        raise RuntimeError("Blender add-on reported failure inside the tool result")
    return value.get("result", value)


async def inspect_server(launch=None, env=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    launch = launch or command()
    params = StdioServerParameters(command=launch[0], args=launch[1:], env=env or environment())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            if not OFFICIAL_TOOLS.issubset(names):
                raise RuntimeError("Connected server is not the expected Blender Lab tool contract")
            summary = unwrap(await session.call_tool("get_blendfile_summary_datablocks", {}))
            if not isinstance(summary.get("datablock_counts"), dict) or not isinstance(summary.get("scene_name"), str):
                raise RuntimeError("Blender scene summary is missing its verified fields")
            # Observe only. Do not switch files, save preferences or modify the user's scene.
            identity = unwrap(await session.call_tool("execute_blender_code", {"code":
                "import bpy\n"
                "module = 'bl_ext.lab_blender_org.mcp'\n"
                "result = {'blender': bpy.app.version_string, 'official_addon': module in bpy.context.preferences.addons, "
                "'scene': bpy.context.scene.name, 'dirty': bpy.data.is_dirty}\n"}))
            if identity.get("official_addon") is not True:
                raise RuntimeError("The official Blender Lab add-on is not enabled in the connected session")
            return {"status": "ok", "source": "Blender Lab", "release": RELEASE, "commit": COMMIT,
                    "server": initialized.serverInfo.name, "tools": len(names),
                    "scene": summary["scene_name"], "objects": summary["datablock_counts"].get("objects", 0),
                    "blender": identity.get("blender"), "scene_dirty": identity.get("dirty")}


def doctor(launch=None, env=None):
    return asyncio.run(asyncio.wait_for(inspect_server(launch, env), timeout=60))
