"""Athena launcher.

    python run.py            terminal chat (default)
    python run.py gui        desktop HUD (opens as an app window)
    python run.py classic    the older Tk chat window
    python run.py hotkey     background listener, Ctrl+Alt+Space popup
    python run.py voice      talk to it, it talks back
    python run.py doctor     check that everything is installed and working
    python run.py tools      list every tool it can use
    python run.py voice-setup   download the natural voices (Kokoro / Piper)
    python run.py failures   what went wrong lately, most common first

    python run.py autostart on      start with Windows (modes from config.yaml)
    python run.py autostart off     stop starting with Windows
    python run.py autostart status  show whether it is enabled

    python run.py alerts setup      phone alerts: Telegram call + ntfy push
    python run.py alerts status     show what phone alerts are set up
    python run.py call-test         ring your phone once, to check it works
"""
from __future__ import annotations

import sys

# Windows consoles still default to a legacy codepage, which mangles any
# non-ASCII the model produces (degree signs, accents, dashes).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Importing the tools package is what registers every tool.
from jarvis import registry  # noqa: E402
from jarvis import tools  # noqa: F401,E402


def doctor() -> int:
    """Check the environment and report anything that needs fixing."""
    import importlib

    print("Athena environment check\n" + "=" * 40)
    problems: list[str] = []

    # --- python packages ---
    required = {
        "ollama": "core",
        "yaml": "core",
        "rich": "terminal UI",
        "psutil": "system info",
        "httpx": "web",
        "numpy": "document search",
        "PIL": "screenshots",
        "pycaw": "volume control",
        "bs4": "reading web pages",
        "ddgs": "web search",
        "pypdf": "reading PDFs",
        "docx": "reading Word files",
        "keyboard": "hotkey popup",
        "faster_whisper": "voice input",
        "sounddevice": "microphone",
        "pyttsx3": "voice output",
    }
    print("\nPython packages:")
    for mod, why in required.items():
        try:
            importlib.import_module(mod)
            print(f"  ok      {mod:18} ({why})")
        except ImportError:
            print(f"  MISSING {mod:18} ({why})")
            problems.append(f"pip install {mod}")

    # --- ollama ---
    print("\nOllama:")
    from jarvis.llm import LLM

    llm = LLM()
    ok, msg = llm.available()
    if ok:
        print(f"  ok      server up, model '{llm.model}' present")
    else:
        print(f"  PROBLEM {msg}")
        problems.append(msg)

    # embedding model is only needed for document search
    try:
        import ollama

        names = [m.model for m in ollama.Client(host=llm.host).list().models]
        if any(n.startswith("nomic-embed-text") for n in names):
            print("  ok      nomic-embed-text present (document search)")
        else:
            print("  NOTE    nomic-embed-text missing - document search will not work")
            problems.append("ollama pull nomic-embed-text")
    except Exception:
        pass

    # A model that quietly lands on the CPU is the difference between a
    # half-second reply and three minutes, so check rather than assume.
    if ok:
        print("\nWhere the model runs:")
        try:
            llm.client.generate(model=llm.model, prompt="hi", options={"num_predict": 1})
        except Exception:
            pass
        where = llm.processor()
        if where == "GPU":
            print("  ok      fully on the GPU")
        elif where in ("unknown", "not loaded"):
            print(f"  NOTE    could not determine ({where})")
        else:
            print(f"  SLOW    running on {where}")
            problems.append(
                "Model is not fully on the GPU. Close other GPU-heavy apps, or restart "
                "Ollama: taskkill /F /IM ollama.exe  then relaunch Ollama."
            )

    # --- gpu ---
    print("\nGPU:")
    try:
        import subprocess

        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, check=False)
        print(f"  ok      {r.stdout.strip() or 'no NVIDIA GPU detected'}")
    except FileNotFoundError:
        print("  NOTE    nvidia-smi not found; the model will run on CPU (slower)")

    # --- tools ---
    print(f"\nTools: {len(registry.REGISTRY)} registered")

    print("\n" + "=" * 40)
    if problems:
        print("To fix:\n")
        for p in dict.fromkeys(problems):
            print(f"  {p}")
        return 1
    print("Everything checks out. Run: python run.py")
    return 0


def alerts_status() -> int:
    from jarvis import phone
    from jarvis.config import cfg

    c = phone.contact()
    print("Phone alerts\n" + "=" * 40)
    print(f"  master switch   {'on' if cfg.get('alerts.enabled', True) else 'OFF'} "
          f"(alerts.enabled in config.yaml)")
    print(f"  Telegram call   {'set up' if c.get('telegram') else 'not set up'}")
    print(f"  ntfy push       {'set up' if c.get('ntfy_topic') else 'not set up'}")
    if not phone.configured():
        print("\nRun: python run.py alerts setup")
    return 0


def alerts_setup() -> int:
    """Ask for the Telegram user and make the ntfy topic. Nothing here costs
    money or needs an account on either service."""
    from jarvis import phone

    c = phone.contact()
    print("Phone alerts: Athena rings you on Telegram and sends a push to the")
    print("ntfy app when something important happens. Both are free and need")
    print("no account. Only a short line of text leaves this PC (\"Email from")
    print("Prof Example\", never the email), sent to CallMeBot, Google's speech")
    print("service and ntfy.sh.\n")

    have = c.get("telegram", "")
    prompt = ("Your Telegram @username (or phone number with +91)"
              + (" [Enter keeps the saved one]" if have else "") + ": ")
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nNothing changed.")
            return 1
        if not raw and have:
            user = have
            break
        user = phone.normalise_user(raw)
        if user:
            break
        print("  That is not a Telegram @username (5-32 letters, digits or _) "
              "or a phone number. Try again.")

    topic = c.get("ntfy_topic") or phone.new_topic()
    phone.save_contact(telegram=user, topic=topic)

    print(f"\nSaved to {phone.CONTACT_FILE} (not config.yaml).\n")
    print("Two things to do on your phone, once:\n")
    print(f"1. Telegram: open a chat with {phone.AUTH_BOT} and send  /start")
    print("   This lets the free call bot ring you. Without it, calls are refused.")
    print("   (If you gave an @username, make sure it is set under Telegram >")
    print("   Settings > Username.)\n")
    print("2. ntfy: install the \"ntfy\" app (Play Store / F-Droid / App Store),")
    print("   tap +, and subscribe to this topic on ntfy.sh:\n")
    print(f"       {topic}\n")
    print("   Treat that name like a password: anyone who knows it can read your")
    print("   alerts. For the loudest alert, allow ntfy to override Do Not Disturb")
    print("   in the app's notification settings.\n")
    print("Then check it all works:  python run.py call-test")
    return 0


def _log_to_file(mode: str) -> None:
    """Under pythonw there is no console: print() goes nowhere. Background
    modes write their own log instead of being wrapped in a hidden
    `cmd /c ... > log` - Windows closed that hidden cmd.exe as "not
    responding" at 14:30 on 25 Sep 2026 and took voice and the web server
    down with it."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from jarvis.config import DATA
    log = open(DATA / f"{mode}.log", "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "cli").lower().lstrip("-")
    if mode in ("voice", "web", "hotkey"):
        import ctypes
        # One copy of each: a manual start racing the web server's watchdog
        # ran voice twice (26 Sep 2026) - Whisper loaded twice, ~1 GB of VRAM,
        # everything heard and said twice. Checked before the log is opened,
        # which a refused copy would otherwise wipe.
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\Athena_{mode}")
        if ctypes.windll.kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
            return 0
        _log_to_file(mode)

    if mode in ("doctor", "check"):
        return doctor()

    if mode in ("tools", "list"):
        print(registry.describe_all())
        return 0

    if mode == "autostart":
        from jarvis import autostart

        action = (sys.argv[2] if len(sys.argv) > 2 else "status").lower()
        if action in ("on", "enable"):
            modes = sys.argv[3].split(",") if len(sys.argv) > 3 else None
            print(autostart.enable(modes))
        elif action in ("off", "disable"):
            print(autostart.disable())
        else:
            print(autostart.status())
        return 0

    if mode == "alerts":
        action = (sys.argv[2] if len(sys.argv) > 2 else "status").lower()
        return alerts_setup() if action == "setup" else alerts_status()

    if mode in ("call-test", "calltest", "test-call"):
        from jarvis import phone

        if not phone.configured():
            print("Phone alerts are not set up. Run: python run.py alerts setup")
            return 1
        print("Ringing your phone (Telegram call + ntfy push)...")
        # Waits for the call bot to answer, however long it takes: this is the
        # one place where seeing the real outcome matters more than speed.
        ok, words = phone.deliver(phone.TEST_MESSAGE)
        print(words)
        return 0 if ok else 1

    if mode == "key":
        # Kept out of config.yaml on purpose: that file gets pasted into
        # chats and committed to repos; an API key should not travel with it.
        from jarvis import cloud

        if len(sys.argv) < 3:
            print("Providers you can use for study.model:\n")
            for name, p in sorted(cloud.PROVIDERS.items()):
                have = "saved" if cloud.get_key(name) else "no key"
                print(f"  {name:12} {have:8} {p['note']}")
                print(f"  {'':12} {p['signup']}")
            print("\n  python run.py key <provider> <the-key>")
            return 0
        provider = sys.argv[2].lower()
        if provider not in cloud.PROVIDERS:
            print(f"Unknown provider. Try: {', '.join(sorted(cloud.PROVIDERS))}")
            return 1
        if len(sys.argv) < 4:
            # Asked for rather than passed as an argument: a key on the command
            # line is in your shell history, your terminal scrollback, and any
            # screenshot of either.
            import getpass

            print(cloud.how_to_get_one(provider))
            print()
            try:
                key = getpass.getpass(f"Paste your {provider} key here "
                                      f"(it will not show): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nNothing saved.")
                return 1
            if not key:
                print("Nothing pasted, nothing saved.")
                return 1
            cloud.set_key(provider, key)
            print(f"\nSaved to {cloud.KEYS_FILE}.")
            print(f'Now set  study.model: "{provider}:<model>"  in config.yaml.')
            return 0
        cloud.set_key(provider, sys.argv[3])
        print(f"Saved. Now set study.model in config.yaml, for example:\n"
              f"  study:\n    model: \"{provider}:<model-name>\"")
        return 0

    if mode == "store-key":
        # The RAGAZZA website's Athena key. Asked for, never passed on the
        # command line, for the same reason as `key` above.
        import getpass

        from jarvis import cloud

        try:
            key = getpass.getpass("Paste the RAGAZZA Athena key (it will not show): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nNothing saved.")
            return 1
        if not key.startswith("rgz_"):
            print("That doesn't look like a RAGAZZA key (they start with rgz_). Nothing saved.")
            return 1
        cloud.set_key("ragazza", key)
        print(f"Saved to {cloud.KEYS_FILE}. Store address: store.url in config.yaml.")
        return 0

    if mode == "models":
        from jarvis import cloud

        if len(sys.argv) < 3:
            print(f"python run.py models <{'|'.join(sorted(cloud.PROVIDERS))}>")
            return 0
        provider = sys.argv[2].lower()
        try:
            names = cloud.list_models(provider)
        except RuntimeError as e:
            print(e)
            return 1
        free = [n for n in names if n.endswith(":free")]
        if free:
            print(f"{len(free)} free of {len(names)} models on {provider}:\n")
            for n in free:
                print("   ", n)
        else:
            print(f"{len(names)} models on {provider}:\n")
            for n in names:
                print("   ", n)
        return 0

    if mode == "failures":
        # What went wrong lately, most common first: what to fix next.
        from jarvis import failures

        days = float(sys.argv[2]) if len(sys.argv) > 2 else 30
        print(failures.report(days))
        return 0

    if mode in ("voice-setup", "voices"):
        # Download the natural voices (Kokoro for the GPU, Piper for the CPU).
        from jarvis import tts

        return tts.setup(sys.argv[2] if len(sys.argv) > 2 else "auto")

    if mode in ("help", "h", "?"):
        print(__doc__)
        return 0

    if mode in ("cameras", "camera"):
        from jarvis import vision

        found = vision.cameras()
        if not found:
            print("No cameras found. Is ffmpeg installed and a webcam attached?")
            return 1
        print("Cameras ffmpeg can see (set vision.camera in config.yaml):\n")
        for name in found:
            mark = "  <- in use" if name == vision.camera_name() else ""
            print(f"  {name}{mark}")
        ok, msg = vision.available()
        print(f"\nVision model: {msg}" if ok else f"\n{msg}")
        return 0

    if mode == "gui":
        # The HUD in its own app window - same page the phone shows.
        from jarvis.ui import hud_window

        hud_window.run()
    elif mode in ("classic", "gui-classic"):
        from jarvis.ui import gui

        gui.run()
    elif mode == "hotkey":
        from jarvis.ui import hotkey

        hotkey.run()
    elif mode == "voice":
        from jarvis.ui import voice

        voice.run()
    elif mode in ("web", "phone"):
        from jarvis.ui import web

        web.run()
    elif mode in ("cli", "chat", "terminal"):
        from jarvis.ui import cli

        cli.run()
    else:
        print(f"Unknown mode '{mode}'.\n")
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
