"""Modes: study, coding, research, system - or general (9 Oct 2026).

A mode changes three things for every request until switched back: a few
lines added to the system prompt (how to answer), the tool groups always shown
to the model (registry.select), and the chip on the HUD. Modes are defined in
config.yaml (modes:), the current one is data/mode.json - one switch shared by
voice, the HUD and the phone. Permissions do not change with the mode: the
risk tiers (Agent._needs_confirm) hold in every one.

Changing mode changes the system prompt, so the first request after a switch
re-reads the prompt (a second or two); after that it is cached as before.
"""
from __future__ import annotations

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

STATE = DATA / "mode.json"


def defined() -> dict[str, dict]:
    return {"general": {}, **(cfg.get("modes", {}) or {})}


def current() -> str:
    m = str(load_json(STATE, {}).get("mode", "general"))
    return m if m in defined() else "general"


def prompt() -> str:
    """The lines a mode adds to the system prompt ('' for general)."""
    p = str(defined().get(current(), {}).get("prompt", "") or "").strip()
    return f"\n\nMODE: {current().upper()}. {p}" if p else ""


def groups() -> list[str]:
    return list(defined().get(current(), {}).get("tools", []) or [])


@tool(tags=["system"])
def set_mode(mode: str = "general") -> str:
    """Switch Athena's mode - how she answers and which tools she keeps ready:
    study, coding, research, system, or general (normal). Use for "coding
    mode", "switch to research mode", "normal mode", "what mode are you in".

    mode: study, coding, research, system, general - or "status"
    """
    want = mode.strip().lower().replace(" mode", "")
    want = {"normal": "general", "default": "general", "off": "general", "code": "coding"}.get(want, want)
    if want in ("status", "current", "what", ""):
        return f"I'm in {current()} mode."
    if want not in defined():
        return f"There's no {want} mode. I have: {', '.join(defined())}."
    save_json(STATE, {"mode": want})          # the HUD reads it from here (web.py /live)
    blurb = str(defined()[want].get("say", "") or "")
    return f"{want.capitalize()} mode." + (f" {blurb}" if blurb else "")


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    STATE = Path(tempfile.mkdtemp()) / "mode.json"
    assert current() == "general" and prompt() == ""
    print(set_mode("coding"), "|", prompt()[:60], "|", groups())
    assert current() == "coding" and "CODING" in prompt()
    print(set_mode("normal"), set_mode("banana"))
    assert current() == "general"
    print("ok")
