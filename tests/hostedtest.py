"""Checks everyday requests on a hosted model (llm.everyday: hosted), with a
stand-in for the provider so nothing is sent anywhere:

  - an action answers in ONE call when the model marks it final
  - conversation needs no tool; a question about the user or the PC does,
    so "what's my battery" can never be answered from nothing
  - a rate limit or no network falls back to the local model at once
  - no key means local, without trying
  - an action request is never answered without a tool
  - the recent turns go along, so a follow-up makes sense

    python tests/hostedtest.py
"""
import ctypes
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not hasattr(ctypes, "WinDLL"):
    from unittest import mock

    ctypes.WinDLL = lambda *a, **k: mock.MagicMock()
    ctypes.windll = mock.MagicMock()
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    for name in ("win32gui", "win32con", "win32api", "win32process", "win32clipboard",
                 "pycaw", "pycaw.pycaw", "comtypes", "comtypes.client", "winsound",
                 "keyboard", "send2trash", "playwright", "playwright.sync_api", "ddgs",
                 "pythoncom", "winreg", "msvcrt", "sounddevice"):
        try:
            __import__(name)
        except Exception:
            sys.modules[name] = mock.MagicMock()

import jarvis.tools  # noqa: E402,F401
from jarvis import agent, cloud, failures, memory, planner, profile, recall, registry  # noqa: E402
from jarvis.config import cfg  # noqa: E402

tmp = Path(tempfile.mkdtemp())
memory.MEM_FILE, profile.PROFILE, failures.FILE = tmp / "m.json", tmp / "p.md", tmp / "f.jsonl"
profile.journal = recall.note_voice = lambda *a, **k: None
planner.PLAN_LOG = tmp / "planner.log"
cfg._d.setdefault("llm", {})["everyday"] = "hosted"
cfg._d.setdefault("planner", {}).update(model="groq:test-model", fallback=["gemini:test-model"])
cloud.get_key = lambda provider: "test-key"

# Tools the requests below use, made harmless.
ran = []
for name, result in (("bluetooth", "Bluetooth is now on."), ("battery", "Battery 81%, charging."),
                     ("open_file", "Opened notes.pdf, from Documents.")):
    registry.REGISTRY[name].fn = (lambda n, r: (lambda **k: (ran.append((n, k)), r)[1]))(name, result)

calls: list[list[dict]] = []
script: list = []


overrun: list[str] = []


def fake_chat(model, messages, **k):
    calls.append(messages)
    if not script:                      # asked more often than the check expects
        overrun.append(messages[-1]["content"][:60])
        raise RuntimeError("test script exhausted")
    reply = script.pop(0)
    if isinstance(reply, Exception):
        raise reply
    return json.dumps(reply)


cloud.chat = fake_chat
planner.cloud.chat = fake_chat
agent.planner.is_multistep = lambda t: False


class LocalModel:
    def __init__(self):
        self.used = 0

    def chat(self, msgs, tools=None, **k):
        self.used += 1
        return NS(content="Answered on this PC.", tool_calls=None)

    chat_stream = None


def fresh():
    a = agent.Agent()
    a.llm = LocalModel()
    calls.clear()
    ran.clear()
    return a


def check(bad):
    a = fresh()
    script[:] = [{"tool": "bluetooth", "args": {"on": True}, "final": True}]
    out = a.ask("switch on the bluetooth thing")
    if out != "Bluetooth is now on." or len(calls) != 1 or a.llm.used:
        bad.append(f"final action: {out!r}, {len(calls)} hosted call(s), local used {a.llm.used}")

    a = fresh()
    script[:] = [{"done": "Paris."}]
    out = a.ask("what is the capital of france")
    if out != "Paris." or len(calls) != 1 or ran:
        bad.append(f"conversation: {out!r}, {len(calls)} call(s), tools run {ran}")

    a = fresh()
    script[:] = [{"done": "You're on 60 percent."},                  # made up: must be refused
                 {"tool": "battery", "args": {}},
                 {"done": "81 percent, charging."}]
    out = a.ask("hows my laptop charge doing")
    if ("battery", {}) not in ran or out != "81 percent, charging.":
        bad.append(f"personal question answered without a tool: {out!r}, tools {ran}")

    a = fresh()
    script[:] = [RuntimeError("429 from the provider: rate limit"), RuntimeError("could not reach")]
    t0 = time.time()
    out = a.ask("tell me something interesting about owls")
    took = time.time() - t0
    if out != "Answered on this PC." or took > 2:
        bad.append(f"rate limit: {out!r} after {took:.1f}s (want the local answer at once)")
    if "hosted model unavailable" not in failures.FILE.read_text(encoding="utf-8"):
        bad.append("the hosted fallback was not logged")

    saved = cloud.get_key
    cloud.get_key = lambda provider: ""
    a = fresh()
    script[:] = []
    out = a.ask("tell me something interesting about owls")
    cloud.get_key = saved
    if out != "Answered on this PC." or calls:
        bad.append(f"no key: {out!r}, {len(calls)} hosted call(s)")

    a = fresh()
    script[:] = [{"done": "Done, the hall lights are off."},        # an action, no tool: refused
                 {"tool": "bluetooth", "args": {"on": False}},
                 {"done": "Turned it off."}]
    a.ask("switch off the hall lights")
    if not ran:
        bad.append("an action request was answered without running anything")

    a = fresh()
    script[:] = [{"done": "Owls can turn their heads about 270 degrees."},
                 {"done": "Mostly mice and voles."}]
    a.ask("tell me something about owls")
    a.ask("and what do they eat")
    sent = [m["content"] for m in calls[-1] if m["role"] in ("user", "assistant")]
    if not any("owls" in c for c in sent[:-1]):
        bad.append(f"the follow-up went without the conversation: {sent}")


if __name__ == "__main__":
    bad: list[str] = []
    check(bad)
    bad += [f"hosted model asked more often than expected: {o!r}" for o in overrun]
    for b in bad:
        print("FAIL", b)
    print(f"7 checks, {len(bad)} failure(s)")
    sys.exit(1 if bad else 0)
