"""A tiny stdio MCP server for tests/mcptest.py: two tools, newline-delimited JSON-RPC."""
import json
import sys

TOOLS = [
    {"name": "sum", "description": "Add two numbers.",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}},
    {"name": "send_note", "description": "Pretend to send a note.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
]

print("a log line a careless server prints to stdout", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue                                         # notifications/initialized
    m, p = msg["method"], msg.get("params") or {}
    if m == "initialize":
        res = {"protocolVersion": p.get("protocolVersion"), "capabilities": {"tools": {}}, "serverInfo": {"name": "echo", "version": "1"}}
    elif m == "tools/list":
        res = {"tools": TOOLS}
    elif m == "tools/call" and p["name"] == "sum":
        a = p["arguments"]
        res = {"content": [{"type": "text", "text": str(a["a"] + a["b"])}]}
    elif m == "tools/call":
        res = {"content": [{"type": "text", "text": "no network here"}], "isError": True}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "unknown"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
