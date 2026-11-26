"""MCP servers as Athena's own tools (28 Oct 2026).

Other assistants (Hermes Agent, isair/jarvis, PocketPaw) plug in MCP servers -
ready-made tool sets other people maintain: Gmail, Notion, GitHub, smart
homes. This is a small client for the stdio kind (newline-delimited JSON-RPC
on a child process's stdin/stdout), no extra package. Each server's tools are
registered as `<server>_<tool>` and come with the server's name ("check my
notion ...") or by meaning.

config.yaml:
    mcp:
      servers:
        github: {command: ["C:/.../github-mcp-server.exe", "stdio"], env: {GITHUB_PERSONAL_ACCESS_TOKEN: "${github}"},
                 words: "github|repos?"}

`${name}` is filled from the environment or the key store (add_keys.py), so no
key is written in config.yaml; a server whose key isn't saved yet waits. `words`
bring its tools along besides the server's own name. What a server returns was written elsewhere, so it is
treated like a web page (registry.UNTRUSTED); tools that change things (send,
create, delete, ...) ask first (destructive).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time

from . import registry
from .config import cfg

PROTOCOL = "2025-06-18"
WRITES = re.compile(r"(?:^|[_-])(?:send|create|add|delete|remove|update|write|edit|move|post|put|set|pay|buy|book|reply|"
                    r"invite|archive|upload|insert|drop|run|exec|execute|merge|close|comment|publish|share|"
                    # a browser's hands, and git's: one click can be "Place order"
                    # ("type" alone: GitHub's list_issue_types only reads)
                    r"click|type(?![a-z])|fill|press|select|drag|submit|evaluate|handle|install|push|fork|assign|"
                    r"star|dismiss|mark|rerun|cancel|transfer|lock)", re.I)
# Not a change, but it goes somewhere: after reading a page, a link it plants could
# carry what's in the conversation out in its address - so it asks then (OUTWARD).
GOES = re.compile(r"navigat", re.I)
SERVERS: dict[str, "Server"] = {}


class Server:
    """One MCP server: a child process speaking JSON-RPC, one line per message."""

    def __init__(self, name: str, command: list[str], env: dict | None = None) -> None:
        self.name, self.command, self.env = name, list(command), dict(env or {})
        self.proc: subprocess.Popen | None = None
        self.tools: list[dict] = []
        self.error = ""
        self._id, self._replies = 0, {}
        self._write, self._cv = threading.Lock(), threading.Condition()

    def start(self, timeout: float = 30) -> None:
        exe = shutil.which(self.command[0]) or self.command[0]           # npx is npx.cmd on Windows
        self.proc = subprocess.Popen([exe, *self.command[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     env={**os.environ, **{k: _resolve(v) for k, v in self.env.items()}},
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _die_with_athena(self.proc)
        threading.Thread(target=self._read, daemon=True, name=f"mcp-{self.name}").start()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "athena", "version": "1.0"}}, timeout)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self.request("tools/list", {}, timeout).get("tools", []) or []

    def _send(self, msg: dict) -> None:
        if self.proc is None or self.proc.poll() is not None:
            raise RuntimeError(f"the {self.name} server isn't running")
        try:
            with self._write:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
        except OSError as e:                                              # it died between the check and the write
            raise RuntimeError(f"the {self.name} server stopped ({e})") from e

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue                                                  # a server printing logs to stdout
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._cv:
                    self._replies[msg["id"]] = msg
                    self._cv.notify_all()
            elif "id" in msg and "method" in msg:                       # the server asking us something (sampling, roots...)
                self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})
        with self._cv:
            self._cv.notify_all()                                         # it exited: wake anyone waiting

    def request(self, method: str, params: dict, timeout: float = 60) -> dict:
        with self._write:
            self._id += 1
            rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        end = time.time() + timeout
        with self._cv:
            while rid not in self._replies:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"the {self.name} server stopped")
                if time.time() >= end:
                    raise RuntimeError(f"the {self.name} server didn't answer {method} in {timeout:.0f} s")
                self._cv.wait(min(1.0, end - time.time()))
            msg = self._replies.pop(rid)
        if "error" in msg:
            raise RuntimeError(f"{self.name}: {msg['error'].get('message', 'error')}")
        return msg.get("result") or {}

    def call(self, tool: str, args: dict, timeout: float = 90) -> str:
        r = self.request("tools/call", {"name": tool, "arguments": args}, timeout)
        text = "\n".join(c.get("text", "") for c in r.get("content") or [] if c.get("type") == "text").strip()
        if not text:
            text = json.dumps(r.get("structuredContent") or r.get("content") or {}, ensure_ascii=False)[:6000]
        return ("ERROR: " if r.get("isError") else "") + text[:12000]

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


def _resolve(value) -> str:
    """"${github}" -> the environment, else the key store - keys stay out of config.yaml."""
    from .cloud import get_key
    value = str(value)
    m = re.fullmatch(r"\$\{(\w+)\}", value.strip())
    if not m:
        return value
    return os.environ.get(m.group(1)) or get_key(m.group(1))


def _die_with_athena(proc: subprocess.Popen) -> None:
    """Put the server in a Windows job that is killed when this process ends, so a
    crash or a restart never leaves servers running behind it."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    if not hasattr(_die_with_athena, "job"):
        class Basic(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD), ("c", ctypes.c_size_t),
                        ("d", ctypes.c_size_t), ("e", wintypes.DWORD), ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", ctypes.c_uint64 * 6), ("p", ctypes.c_size_t), ("j", ctypes.c_size_t),
                        ("q", ctypes.c_size_t), ("r", ctypes.c_size_t)]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        job = k.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000                                    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        _die_with_athena.job = job
    k.AssignProcessToJobObject(wintypes.HANDLE(_die_with_athena.job), wintypes.HANDLE(int(proc._handle)))


def _tool_name(server: str, tool: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", f"{server}_{tool}")[:60].strip("_")


def register(server: Server, words: str = "", hint: str = "") -> list[str]:
    """The server's tools, as Athena's own - shown with the server's name, or by meaning.
    hint: what the model can't know, on every tool (whose account it is, which tool
    answers what) - without it "what changed in my athena repo" went to a CI tool
    with an invented run ID, twice (28 Oct 2026)."""
    names = []
    for t in server.tools:
        name = _tool_name(server.name, t["name"])

        def call(_tool=t["name"], **kw):
            return server.call(_tool, kw)
        params = t.get("inputSchema") or {"type": "object", "properties": {}}
        params.setdefault("properties", {})
        desc = (f"{(t.get('description') or t['name']).strip()[:400]} (from the {server.name} MCP server)"
                + (f" {hint}" if hint else ""))
        writes = bool(WRITES.search(t["name"]))
        registry.REGISTRY[name] = registry.Tool(
            name=name, description=desc, fn=call, destructive=writes, tags=["mcp", server.name], timeout=120,
            schema={"type": "function", "function": {"name": name, "description": desc, "parameters": params}})
        registry.UNTRUSTED.add(name)                                      # its answer was written elsewhere
        if writes or GOES.search(t["name"]):
            registry.OUTWARD.add(name)
        names.append(name)
    own = r"|".join(re.escape(w) for w in re.split(r"[\s_-]+", server.name) if len(w) > 2) or re.escape(server.name)
    registry.TOOL_GROUPS.append((f"mcp:{server.name}", re.compile(rf"\b(?:{own}{'|' + words if words else ''})", re.I),
                                 names))
    return names


def start_all(log=print) -> None:
    """Start every server in config.yaml (mcp.servers) and register its tools - in the
    background, so a slow server never holds Athena up."""
    servers = cfg.get("mcp.servers", {}) or {}

    def go(name, spec):
        s = Server(name, spec.get("command") or [], spec.get("env"))
        missing = [str(v)[2:-1] for v in (spec.get("env") or {}).values() if str(v).startswith("${") and not _resolve(v)]
        if missing:
            log(f"[mcp] {name} is waiting for its key: python add_keys.py, provider {missing[0]}")
            return
        try:
            s.start(float(spec.get("timeout", 45)))
            SERVERS[name] = s
            log(f"[mcp] {name}: {len(register(s, spec.get('words', ''), spec.get('hint', '')))} tools")
        except Exception as e:
            s.error = str(e)[:200]
            s.stop()
            log(f"[mcp] {name} didn't start: {s.error}")
    for name, spec in servers.items():
        if isinstance(spec, dict) and spec.get("command") and spec.get("enabled", True):
            threading.Thread(target=go, args=(str(name), spec), daemon=True, name=f"mcp-start-{name}").start()


@registry.tool(tags=["system"])
def mcp_servers() -> str:
    """Which MCP servers (plug-in tool sets) Athena has, and their tools. Use for
    "what plugins do you have", "list your MCP servers"."""
    if not (cfg.get("mcp.servers") or {}):
        return "No MCP servers yet - add one under mcp.servers in config.yaml."
    lines = [f"{n}: {len(s.tools)} tools - " + ", ".join(t['name'] for t in s.tools[:12]) for n, s in SERVERS.items()]
    off = [n for n in (cfg.get("mcp.servers") or {}) if n not in SERVERS]
    return "\n".join(lines + ([f"Not running: {', '.join(off)}."] if off else [])) or "None of the MCP servers started."
