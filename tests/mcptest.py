"""MCP plug-ins (jarvis/mcp.py) against a tiny local server - no network, nothing installed.

    python tests/mcptest.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import mcp, registry  # noqa: E402
import jarvis.tools  # noqa: E402,F401

bad = []
s = mcp.Server("echo test", [sys.executable, str(Path(__file__).with_name("mcp_echo_server.py"))])
s.start(timeout=20)
if [t["name"] for t in s.tools] != ["sum", "send_note"]:
    bad.append(f"tools listed: {s.tools}")
names = mcp.register(s)
if names != ["echo_test_sum", "echo_test_send_note"]:
    bad.append(f"registered as {names}")
if float(registry.get("echo_test_sum").run(a=2, b="40")) != 42:      # "40" coerced to a number by the schema
    bad.append(f"add gave {registry.get('echo_test_sum').run(a=2, b='40')!r}")
if not registry.get("echo_test_send_note").destructive or "echo_test_send_note" not in registry.OUTWARD:
    bad.append("a tool that sends things doesn't ask first")
if registry.get("echo_test_sum").destructive or "echo_test_sum" not in registry.UNTRUSTED:
    bad.append("a read-only tool asks first, or its answer is trusted")
if not s.call("send_note", {"text": "hi"}).startswith("ERROR"):
    bad.append("a server error wasn't passed on as an ERROR")
if "echo_test_sum" not in registry.select("use echo to add two numbers"):
    bad.append("the server's tools don't come with its name")
if "echo_test_sum" in registry.select("what is your name"):
    bad.append("the server's tools come with every question")
for n in ("browser_click", "browser_type", "browser_fill_form", "push_files", "send_note", "create_issue"):
    if not mcp.WRITES.search(n):
        bad.append(f"{n} would run without asking")
for n in ("browser_snapshot", "list_issue_types", "list_pull_requests", "pull_request_read", "get_file_contents"):
    if mcp.WRITES.search(n):
        bad.append(f"{n} only reads but would ask")
w = mcp.Server("web tool", s.command)
w.tools = [{"name": "browser_navigate", "description": "go to a url"}]
mcp.register(w, r"amazon|track\s+my\s+order", "The account is test-user.")
if "The account is test-user." not in registry.get("web_tool_browser_navigate").description:
    bad.append("a server's hint isn't on its tools")
if "web_tool_browser_navigate" not in registry.select("track my order on amazon"):
    bad.append("a server's words don't bring its tools")
if "web_tool_browser_navigate" not in registry.OUTWARD or registry.get("web_tool_browser_navigate").destructive:
    bad.append("navigating doesn't ask after a page was read, or asks every time")
from jarvis import cloud  # noqa: E402
cloud.get_key = lambda p: "k-123" if p == "github" else ""
if mcp._resolve("${github}") != "k-123" or mcp._resolve("${nope}") != "" or mcp._resolve("plain") != "plain":
    bad.append("keys aren't read from the key store")
logs = []
mcp.cfg = type("C", (), {"get": lambda self, k, d=None: {"x": {"command": ["nothing-here.exe"], "env": {"T": "${nope}"}}}})()
mcp.start_all(logs.append)
time.sleep(0.5)
if not logs or "waiting for its key" not in logs[0] or "x" in mcp.SERVERS:
    bad.append(f"a server without its key wasn't held back: {logs}")
s.stop()
time.sleep(0.5)
try:
    s.request("tools/list", {}, timeout=2)
    bad.append("a stopped server still answered")
except RuntimeError:
    pass
for b in bad:
    print("FAIL", b)
print(f"mcp: {len(bad)} failure(s)")
sys.exit(1 if bad else 0)
