"""Test all three MCP transport protocols."""
import json
import subprocess
import sys
import time

import requests


def test_streamable_http():
    print("=" * 50)
    print("Test 1: Streamable HTTP")
    print("=" * 50)

    # Initialize
    r = requests.post("http://localhost:1603/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1.0"}}
    }, headers={"Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"}, timeout=10)

    sid = r.headers.get("mcp-session-id", "")
    print(f"  Session: {sid}")

    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Mcp-Session-Id": sid}

    # Initialized notification
    requests.post("http://localhost:1603/mcp",
                  json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                  headers=headers, timeout=5)

    # List tools
    r = requests.post("http://localhost:1603/mcp",
                      json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                      headers=headers, timeout=10)
    data = json.loads(r.text.split("data: ")[1].strip())
    tools = data["result"]["tools"]
    print(f"  Tools: {len(tools)}")
    print("  PASS")
    return True


def test_sse():
    print("=" * 50)
    print("Test 2: SSE")
    print("=" * 50)

    # Connect SSE
    r = requests.get("http://localhost:1603/mcp/sse", stream=True, timeout=5)
    print(f"  SSE Status: {r.status_code}")

    # Read endpoint URL
    endpoint_url = None
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            endpoint_url = line[6:].strip()
            break

    if not endpoint_url:
        print("  FAIL: No endpoint URL")
        return False

    print(f"  Endpoint: {endpoint_url}")

    # Initialize via POST
    init_r = requests.post(f"http://localhost:1603{endpoint_url}", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "test-sse", "version": "1.0"}}
    }, headers={"Content-Type": "application/json"}, timeout=10)
    print(f"  Init POST Status: {init_r.status_code}")

    # Read initialize response from SSE
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith("data: "):
            data = line[6:].strip()
            if "result" in data:
                parsed = json.loads(data)
                server_info = parsed.get("result", {}).get("serverInfo", {})
                print(f"  Server: {server_info}")
                break

    r.close()
    print("  PASS")
    return True


def test_stdio():
    print("=" * 50)
    print("Test 3: STDIO")
    print("=" * 50)

    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "test-stdio", "version": "1.0"}}
    }) + "\n"

    proc = subprocess.Popen(
        [sys.executable, "mcp_stdio_server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=r"d:\IdeaProjects\hermes-officer\hermes",
        text=True,
    )

    # Send initialize
    proc.stdin.write(init_msg)
    proc.stdin.flush()

    # Read response
    time.sleep(2)
    proc.stdin.close()

    output = proc.stdout.read()
    proc.wait(timeout=5)

    if "result" in output:
        parsed = json.loads(output.strip().split("\n")[0])
        server_info = parsed.get("result", {}).get("serverInfo", {})
        print(f"  Server: {server_info}")
        print("  PASS")
        return True
    else:
        print(f"  Output: {output[:200]}")
        print("  FAIL")
        return False


if __name__ == "__main__":
    results = []
    results.append(("Streamable HTTP", test_streamable_http()))
    results.append(("SSE", test_sse()))
    results.append(("STDIO", test_stdio()))

    print("\n" + "=" * 50)
    print("Summary:")
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
