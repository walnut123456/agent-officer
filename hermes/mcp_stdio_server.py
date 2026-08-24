# -*- coding: utf-8 -*-
"""
MCP STDIO Server 入口。

通过标准输入/输出与客户端通信，适用于本地进程模式。
Java 端通过 StdioClientTransport 启动此脚本。

用法：
    python mcp_stdio_server.py
"""
import asyncio
import os
import sys
from pathlib import Path

# 确保 hermes_officer 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()


async def main():
    from mcp.server.stdio import stdio_server
    from hermes_officer.mcp.server import server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
