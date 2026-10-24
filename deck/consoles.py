"""Real terminals for the board: one ConPTY per console (pywinpty), streamed
to the UI over a websocket, with a status for each - working, resting or
needs you.

Claude Code consoles report their own status through hooks (hook.py, wired in
with --settings per console): a prompt or a tool run is "working", Stop is
"resting", a permission Notification is "needs you". Other CLIs are read from
their output: output flowing is working, a known approval prompt in the last
screen is needs you, quiet is resting - except Codex, which can work in silence
and says so in its terminal title ("⠋ | folder").
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from winpty import PtyProcess

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
KINDS = {
    "claude": {"label": "Claude Code", "exe": "claude"},
    "gemini": {"label": "Antigravity", "exe": "agy"},          # Google's Antigravity CLI (Gemini CLI: uninstalled 20 Oct, no Argon)
    "codex": {"label": "Codex", "exe": "codex"},               # OpenAI's Codex CLI, signed in with ChatGPT (npm @openai/codex)
    "shell": {"label": "PowerShell", "exe": "powershell.exe"},
    "custom": {"label": "Custom", "exe": ""},
}
# More agent CLIs the board knows by name: offered in the Add menu once they're on PATH. They run
# as you'd run them in a terminal (no resume - each start is a new session).
CATALOG = {"opencode": ("OpenCode", "opencode"), "auggie": ("Auggie", "auggie"), "aider": ("Aider", "aider"),
           "goose": ("Goose", "goose"), "amp": ("Amp", "amp"), "cursor": ("Cursor CLI", "cursor-agent"),
           "copilot": ("Copilot CLI", "copilot"), "qwen": ("Qwen Code", "qwen"), "kimi": ("Kimi CLI", "kimi"),
           "crush": ("Crush", "crush"), "droid": ("Droid", "droid"), "grok": ("Grok CLI", "grok"), "cline": ("Cline", "cline"),
           "kilo": ("Kilo Code", "kilocode"), "codebuff": ("Codebuff", "codebuff"), "continue": ("Continue", "cn"),
           "gemini-cli": ("Gemini CLI", "gemini")}
KINDS.update({k: {"label": label, "exe": exe, "extra": True} for k, (label, exe) in CATALOG.items()})


def installed(kind: str) -> bool:
    exe = KINDS.get(kind, {}).get("exe")
    return bool(exe and shutil.which(exe))
BUF = 200_000                       # characters kept per console, replayed when the board reconnects
PY = str(Path(sys.executable).with_name("python.exe")).replace("\\", "/")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-B]|\x1b[=>78]")
_NEEDS = re.compile(r"Do you want to (?:proceed|make this edit|create|run|allow)|\((?:y/n|Y/n|y/N)\)|\[(?:y/n|Y/n|y/N)\]|"
                    r"Allow (?:once|always|this)|Apply this change|Approve\??|Press Enter to continue|waiting for your (?:input|approval)|"
                    r"Do you trust|[●❯›>]\s*1\.\s*(?:Yes|Trust|Allow|Accept|Approve)|"     # numbered menus: Claude's, agy's
                    r"Would you like to (?:run|make|grant|send)|enter confirm", re.I)        # Codex's approvals and menus
_TITLE = re.compile(r"\x1b\][02];([^\x07\x1b]*)(?:\x07|\x1b\\)")                     # a program setting the terminal title
_SPIN = re.compile("[⠀-⣿]")        # a braille spinner opening it: Codex's "⠋ | folder" while it works
_CODEX_BUSY = re.compile(r"esc\s*to\s*interrupt|Waiting\s*for\s*startup", re.I)


_SESSION_VAR = re.compile(r"CLAUDECODE|CLAUDE_CODE_|CLAUDE_AGENT_SDK|CLAUDE_PID$|CLAUDE_EFFORT$|CLAUDE_PREVIEW_")


def clean_env() -> dict:
    """This process's environment minus another Claude Code session's markers. Launched from
    inside one, consoles inherited CLAUDE_CODE_CHILD_SESSION: transcripts off, so no resume (20 Oct 2026)."""
    return {k: v for k, v in os.environ.items() if not _SESSION_VAR.match(k)}


def claude_session_exists(sid: str, profile: str = "") -> bool:
    base = Path(profile) if profile else Path.home() / ".claude"          # an account profile keeps its own transcripts
    return any((base / "projects").glob(f"*/{sid}.jsonl"))


def npm_cli(package: str, name: str) -> list[str]:
    """An npm CLI is a .cmd shim; ConPTY wants a real program, so run its launcher script with node."""
    shim, node = shutil.which(name), shutil.which("node")
    if shim and node:
        pkg = Path(shim).parent / "node_modules" / package
        try:
            bins = json.loads((pkg / "package.json").read_text(encoding="utf-8"))["bin"]
            return [node, str(pkg / (bins[name] if isinstance(bins, dict) else bins))]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return ["cmd.exe", "/c", name]


def codex_home(profile: str = "") -> Path:
    return Path(profile) if profile else Path.home() / ".codex"


def codex_session_exists(sid: str, profile: str = "") -> bool:
    """Codex keeps each conversation as ~/.codex/sessions/<y>/<m>/<d>/rollout-<time>-<id>.jsonl."""
    return bool(sid) and any((codex_home(profile) / "sessions").glob(f"*/*/*/rollout-*-{sid}.jsonl"))


def codex_new_session(cwd: str, since: float, profile: str = "") -> str:
    """The Codex conversation started in `cwd` after `since` - its rollout's first line names
    both. Codex picks its own ids (no --session-id), and the desktop app writes here too."""
    root, want = codex_home(profile) / "sessions", os.path.normcase(os.path.normpath(cwd))
    for day in {time.strftime("%Y/%m/%d", time.localtime(since)), time.strftime("%Y/%m/%d")}:
        for f in (root / day).glob("rollout-*.jsonl"):
            try:
                if f.stat().st_birthtime < since:
                    continue
                with f.open(encoding="utf-8") as fh:
                    meta = json.loads(fh.readline()).get("payload", {})
            except (OSError, ValueError, AttributeError):
                continue
            if os.path.normcase(os.path.normpath(meta.get("cwd") or ".")) == want:
                return meta.get("id", "")
    return ""


def _until(ok, cap: float) -> bool:
    """Poll ok() for up to `cap` seconds; whether it came true."""
    end = time.time() + cap
    while not ok():
        if time.time() > end:
            return False
        time.sleep(0.05)
    return True


def _hooks_file(cid: str) -> Path:
    """A per-console settings file that adds Deck's status hooks to Claude Code."""
    cmd = f'"{PY}" "{(HERE / "hook.py").as_posix()}" {cid}'
    hook = [{"hooks": [{"type": "command", "command": cmd, "timeout": 5}]}]
    events = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Notification", "Stop", "SessionEnd")
    path = DATA / "hooks" / f"{cid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {e: [{"matcher": "*", **hook[0]}] if "Tool" in e else hook for e in events}}),
                    encoding="utf-8")
    return path


class Console:
    def __init__(self, node: dict, on_status, on_meta):
        self.node = node
        self.id = node["id"]
        self.on_status, self.on_meta = on_status, on_meta
        self.text = ""
        self.listeners: list = []              # callables(data) - called from the reader thread
        self.proc: PtyProcess | None = None
        self.state, self.since, self.detail = "starting", time.time(), ""
        self.hook_state = ""                    # Claude's own report; wins over the output guess
        self.last_out = 0.0
        self.total = 0                          # characters ever received: loops find "what came after my message"
        self.title, self._title_at = "", 0      # the terminal title it last set, and self.total right after it
        self._hit, self._hit_at = "", 0.0       # a question on screen, and since when
        self.cols, self.rows = 100, 30
        self._lock = threading.Lock()

    # ---- life ----
    def argv(self, resume: bool) -> list[str] | str:
        n, kind = self.node, self.node.get("kind", "shell")
        if not resume:
            n.pop("session", None)      # Fresh start: a new conversation - the CLIs refuse a used id
        if kind == "claude":
            exe = shutil.which("claude") or "claude"
            sid = n.setdefault("session", str(uuid.uuid4()))
            back = resume and claude_session_exists(sid, n.get("profile", ""))
            args = [exe, "--settings", str(_hooks_file(self.id))]
            args += ["--resume", sid] if back else ["--session-id", sid, "-n", n.get("title") or "Deck"]
            if n.get("model"):
                args += ["--model", n["model"]]
            if n.get("prompt") and not back:
                args.append(n["prompt"])                       # the first instruction, typed for you once
            return args
        if kind == "codex":
            back = resume and codex_session_exists(n.get("session", ""), n.get("profile", ""))
            if not back:
                n.pop("session", None)                          # start() watches for the new one
            args = npm_cli("@openai/codex", "codex") + (["resume", n["session"]] if back else [])
            if n.get("prompt") and not back:
                args.append(n["prompt"])
            return args
        if kind == "gemini":
            exe = shutil.which("agy") or "agy"
            back = resume and n.get("session")
            args = [exe] + (["--conversation", n["session"]] if back else [])
            if n.get("prompt") and not back:
                args += ["--prompt-interactive", n["prompt"]]
            return args
        if kind == "custom" and n.get("cmd"):
            return n["cmd"]                                    # a whole command line, run as written
        if KINDS.get(kind, {}).get("extra"):
            exe = shutil.which(KINDS[kind]["exe"]) or KINDS[kind]["exe"]
            args = ["cmd.exe", "/c", exe] if exe.lower().endswith((".cmd", ".bat")) else [exe]   # npm shims need cmd
            return args + ([n["prompt"]] if n.get("prompt") else [])
        return ["powershell.exe", "-NoLogo"]

    def start(self, resume: bool = True) -> None:
        argv = self.argv(resume)
        env = {**clean_env(), "COLORTERM": "truecolor", "DECK_CONSOLE": self.id, "FORCE_COLOR": "1"}
        profile = self.node.get("profile") or ""
        if profile:                                            # a second account: its own sign-in folder
            Path(profile).mkdir(parents=True, exist_ok=True)
            env[{"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}.get(self.node.get("kind"), "DECK_PROFILE")] = profile
        self.hook_state, self.last_out = "", 0.0
        self.title, self._title_at = "", self.total
        # A list, never a joined string: pywinpty quotes the list itself, and re-split a string
        # with its quotes kept - "C:\claude code\..." arrived wrapped in quotes (20 Oct 2026).
        self.proc = PtyProcess.spawn(argv, cwd=self.node.get("cwd") or str(Path.home()), env=env,
                                     dimensions=(self.rows, self.cols))
        self.state = ""
        self._set("starting", "")
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name=f"deck-{self.id}").start()
        if self.node.get("kind") in ("gemini", "codex") and not self.node.get("session"):
            threading.Thread(target=self._find_session, daemon=True).start()

    def _read(self, proc: PtyProcess) -> None:
        while True:
            try:
                data = proc.read(8192)
            except Exception:
                break
            if not data:
                if not proc.isalive():
                    break
                time.sleep(0.01)
                continue
            self._feed(data)
        if proc is self.proc:
            code = getattr(proc, "exitstatus", None)
            self._set("exited", f"exited{f' (code {code})' if code not in (None, 0) else ''}")

    def _feed(self, data: str) -> None:
        self.last_out = time.time()
        with self._lock:                   # listeners under the lock: subscribe() can't miss or repeat a chunk
            self.text = (self.text + data)[-BUF:]
            self.total += len(data)
            for m in _TITLE.finditer(self.text, max(0, len(self.text) - len(data) - 300)):    # one split across reads too
                self.title, self._title_at = m.group(1), self.total - len(self.text) + m.end()
            for fn in list(self.listeners):
                try:
                    fn(data)
                except Exception:
                    pass

    def _find_session(self) -> None:
        """agy and Codex pick their own conversation id: watch for the one this console starts.
        agy makes its brain folder at once; Codex writes its rollout at your first message."""
        proc, since, codex = self.proc, time.time() - 2, self.node.get("kind") == "codex"
        brain = Path.home() / ".gemini" / "antigravity-cli" / "brain"
        before = {p.name for p in brain.glob("*")} if brain.exists() else set()
        for _ in range(600 if codex else 90):          # ponytail: 30 min; untouched longer = a fresh start next time
            time.sleep(2)
            if self.proc is not proc:                   # restarted or closed: that run's watcher is done
                return
            if codex:
                sid = codex_new_session(self.node.get("cwd") or str(Path.home()), since, self.node.get("profile", ""))
            else:
                new = [p for p in brain.glob("*") if p.is_dir() and p.name not in before] if brain.exists() else []
                sid = max(new, key=lambda p: p.stat().st_ctime).name if new else ""
            if sid:
                self.node["session"] = sid
                self.on_meta(self)
                return

    def subscribe(self, fn) -> str:
        """Start streaming to fn; returns everything so far, to replay first."""
        with self._lock:
            self.listeners.append(fn)
            return self.text

    def unsubscribe(self, fn) -> None:
        with self._lock:
            if fn in self.listeners:
                self.listeners.remove(fn)

    def write(self, data: str) -> None:
        if self.proc and self.proc.isalive():
            self.proc.write(data)

    def send(self, text: str, submit: bool = True) -> None:
        """Type a message into the console - as one paste - then Enter once it's on screen. Codex holds
        typed keys as a paste and takes an Enter that arrives with them as a newline (and draws slowly
        while it starts up): Enter 0.2 s behind the text left messages sitting in its box (24 Oct 2026)."""
        mark = self.total
        self.write("\x1b[200~" + text + "\x1b[201~" if "\n" in text else text)
        if not submit:
            return
        if text:                                 # drawn and quiet again; a busy console never goes quiet: after 4 s
            _until(lambda: time.time() - self.last_out >= 0.3 and self.output_since(mark).strip(), 4)
        mark = self.total
        self.write("\r")
        if self.node.get("kind") == "codex" and not _until(lambda: self._busy(mark), 3):
            self.write("\r")                     # that Enter became a newline in its box: this one sends it

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = max(20, int(cols)), max(5, int(rows))
        if self.proc and self.proc.isalive():
            try:
                self.proc.setwinsize(self.rows, self.cols)
            except Exception:
                pass

    def kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.isalive():
            try:
                proc.terminate(force=True)
            except Exception:
                pass
        self._set("exited", "closed")

    # ---- status ----
    def _set(self, state: str, detail: str) -> None:
        if (state, detail) != (self.state, self.detail):
            if state != self.state:
                self.since = time.time()
            self.state, self.detail = state, detail
            self.on_status(self)

    def hook(self, event: str, message: str = "") -> None:
        if self.state == "exited":
            return
        if event in ("UserPromptSubmit", "PreToolUse", "PostToolUse"):
            self.hook_state = "working"
        elif event == "Notification":
            # "Claude needs your permission to use Bash" vs "Claude is waiting for your input" (just idle)
            self.hook_state = "resting" if re.search(r"waiting for your input", message, re.I) else "needs"
        elif event in ("Stop", "SessionStart"):
            self.hook_state = "resting"
        elif event == "SessionEnd":
            self.hook_state = ""
        self.tick(note=message)

    def screen_tail(self, n: int = 1500) -> str:
        with self._lock:
            return _ANSI.sub("", self.text[-n * 3:])[-n:]

    def output_since(self, mark: int) -> str:
        """Plain text the console printed after self.total was `mark` (as much as the buffer still holds)."""
        with self._lock:
            n = self.total - mark
            return _ANSI.sub("", self.text[-n:] if 0 < n <= len(self.text) else (self.text if n > 0 else ""))

    def _busy(self, since: int = 0) -> bool:
        """Codex at work by its own account - it can draw nothing for minutes while it works (24 Oct 2026):
        "⠋ | folder" as its terminal title, or "esc to interrupt" / "Waiting for startup" drawn after
        `since` and after its last title change. A turn's end resets the title, so old screen text doesn't count."""
        if self.node.get("kind") != "codex":
            return False
        if _SPIN.match(self.title):
            return True
        with self._lock:
            n = min(self.total - max(since, self._title_at), 3000)
            raw = self.text[-n:] if n > 0 else ""
        return bool(_CODEX_BUSY.search(_ANSI.sub("", raw)))

    @property
    def alive(self) -> bool:
        return bool(self.proc and self.proc.isalive())

    def tick(self, note: str = "") -> None:
        if self.state == "exited":
            return
        quiet = time.time() - self.last_out
        if self.hook_state:
            state = self.hook_state
            detail = (note or self.detail) if state == "needs" else ""
        else:
            last = [ln for ln in self.screen_tail(900).splitlines()                         # only where the cursor is,
                    if ln.strip(" \t│║┃|╭╮╰╯┌┐└┘─━═")][-3:]                               # skipping dialog borders:
            tail = "\n".join(last)                                                       # an answered question scrolls up
            hit = _NEEDS.search(tail)
            if not hit:
                self._hit = ""
            elif hit.group(0) != self._hit:
                self._hit, self._hit_at = hit.group(0), time.time()
            # Quiet output means it's waiting - but some TUIs repaint while they wait (agy's "Do you trust
            # this folder?" read as working, 20 Oct 2026), so a question that stays put counts as well.
            steady = bool(hit) and time.time() - self._hit_at >= 2.5
            if hit and (quiet > 1.5 or steady):
                line = next((ln.strip(" \t│║┃|") for ln in reversed(tail.splitlines()) if hit.group(0) in ln), hit.group(0))
                state, detail = "needs", line[:160]
            elif (self.last_out and quiet < 2.5) or self._busy():
                state, detail = "working", ""
            else:
                state, detail = ("resting" if self.last_out else "starting"), ""
        self._set(state, detail)

    def info(self) -> dict:
        return {"id": self.id, "state": self.state, "since": self.since, "detail": self.detail,
                "alive": bool(self.proc and self.proc.isalive())}


if __name__ == "__main__":
    import tempfile
    from unittest import mock

    agy = {"id": "a", "kind": "gemini", "session": "conv-1"}
    assert Console(agy, print, print).argv(True)[-2:] == ["--conversation", "conv-1"]
    assert "--conversation" not in Console(agy, print, print).argv(False) and "session" not in agy   # Fresh start

    home, since = Path(tempfile.mkdtemp()), time.time() - 1
    day = home / ".codex" / "sessions" / time.strftime("%Y/%m/%d")
    day.mkdir(parents=True)
    sid = "01a157ed-029a-7b81-8f03-4c185bed54d4"
    meta = {"type": "session_meta", "payload": {"id": sid, "cwd": "C:\\Orb Site\\"}}
    (day / f"rollout-2026-10-20T11-38-34-{sid}.jsonl").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    with mock.patch.object(Path, "home", lambda: home):
        assert codex_new_session(r"c:\orb site", since) == sid and codex_new_session(r"C:\other", since) == ""
        assert codex_new_session(r"C:\Orb Site", time.time() + 60) == ""          # made before this console started
        cx = {"id": "x", "kind": "codex", "session": sid}
        assert Console(cx, print, print).argv(True)[-2:] == ["resume", sid]
        assert "resume" not in Console(cx, print, print).argv(False) and "session" not in cx      # Fresh start
        gone = {"id": "y", "kind": "codex", "session": "deleted-id"}
        assert "resume" not in Console(gone, print, print).argv(True) and "session" not in gone   # vanished: start new

    seen = []
    rp = Console({"id": "r", "kind": "gemini"}, lambda c: seen.append(c.state), print)
    rp.text = "Do you trust the contents of this project?\n  Yes, I trust this folder\n> No, exit\n  ↑/↓ Navigate · enter Confirm\n"
    rp.last_out = time.time()                                      # it keeps repainting: never quiet
    rp.tick()
    assert rp.state == "working", rp.state                         # a moment's question could still be output going by
    rp._hit_at -= 3
    rp.tick()
    assert rp.state == "needs" and "enter Confirm" in rp.detail, (rp.state, rp.detail)   # still asking 3 s on: needs you

    class Pty:            # Codex 0.163 as measured on 24 Oct: draws typed text late, takes an Enter before that as a newline
        def __init__(self, con: Console, swallow: int = 0):
            self.con, self.swallow, self.drawn, self.enters, self.submitted = con, swallow, False, 0, 0

        def isalive(self):
            return True

        def write(self, d):
            if d != "\r":
                self.con._feed("\x1b[6n")                                      # it asks where the cursor is: nothing to see yet
                threading.Timer(0.4, lambda: (setattr(self, "drawn", True), self.con._feed("›" + d))).start()
                return
            self.enters += 1
            if self.drawn and self.swallow <= 0:
                self.submitted += 1
                self.con._feed("\x1b]0;⠋ | app\x07Working(0s • esc to interrupt)")
            else:
                self.swallow -= 1
                self.con._feed("\x1b[27;3H")                                   # a newline in its box

    for kind, swallow, enters, submitted in (("codex", 0, 1, 1), ("codex", 1, 2, 1), ("claude", 1, 1, 0)):
        con = Console({"id": "k", "kind": kind}, lambda c: None, lambda c: None)
        con.proc = pty = Pty(con, swallow)
        con.send("say hi")                                  # the old send: Enter 0.2 s on, before it was drawn - swallowed
        assert (pty.enters, pty.submitted) == (enters, submitted), (kind, swallow, pty.enters, pty.submitted)

    cx = Console({"id": "w", "kind": "codex"}, lambda c: None, lambda c: None)
    cx._feed("›fix it\x1b]0;⠋ | app\x07Working(0s • esc to interrupt)\x1b[29;57H⠋")
    cx.last_out -= 30                                       # then nothing for 30 s while it waits on the model
    cx.tick()
    assert cx.state == "working", cx.state
    cx._feed("• Done.Worked for 6s • 21:34\x1b]0;app\x07")
    cx.last_out -= 30
    cx.tick()
    assert cx.state == "resting", cx.state                  # its title says it stopped: the "esc to interrupt" above is old
    cx._feed("›next one\x1b[24;2HWaiting for startup·esccancel")
    cx.last_out -= 30
    cx.tick()
    assert cx.state == "working", cx.state                  # queued until its MCP servers are up
    print("consoles: checks pass")
