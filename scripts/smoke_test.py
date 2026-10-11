"""Start the server over stdio like Claude Code does, list tools, call the
read-only ones. Sends no input to VRoid unless --shot is given (capture only)."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    params = StdioServerParameters(command="uv", args=["run", "--directory", str(ROOT), "vroid-harness"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print(f"{len(tools)} tools")
            st = await s.call_tool("vroid_status", {})
            info = json.loads(st.content[0].text)
            print("permissions", info["permissions"], "running", info["running"],
                  "on-screen", info["window_fully_on_screen"])
            cs = await s.call_tool("vroid_current_screen", {})
            print("screen", cs.content[0].text)
            if "--shot" in sys.argv:
                sh = await s.call_tool("vroid_screenshot", {"tag": "smoke"})
                print("screenshot parts", [c.type for c in sh.content], sh.content[0].text[:160])


asyncio.run(main())
