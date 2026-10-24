"""Claude Code hook for a Deck console: tells the board what the agent is doing.

Run by Claude Code (consoles.py adds it with --settings) on SessionStart,
UserPromptSubmit, Pre/PostToolUse, Notification, Stop and SessionEnd. Prints
nothing - a UserPromptSubmit hook's output would land in the agent's context -
and never fails the agent: any error is swallowed.
"""
import json
import sys
import urllib.request
from pathlib import Path


def main() -> None:
    cid = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        ev = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        ev = {}
    data = Path(__file__).resolve().parent / "data"
    token = (data / "token.txt").read_text(encoding="utf-8").strip()
    port = (data / "port.txt").read_text(encoding="utf-8").strip() if (data / "port.txt").exists() else "8430"
    body = json.dumps({"console": cid, "event": ev.get("hook_event_name", ""), "message": str(ev.get("message", ""))[:300],
                       "session": ev.get("session_id", "")}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/hook", data=body, method="POST",
                                 headers={"X-Token": token, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=1.5).read()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
