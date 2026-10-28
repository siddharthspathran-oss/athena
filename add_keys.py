"""Add API keys for Athena without them showing on screen or in any chat.

    python add_keys.py

Asks for a provider and its key (hidden while you type), checks the key with
one tiny request, and saves it in Windows Credential Manager (Athena/<provider>).
Blank provider = done.
"""
from getpass import getpass

from jarvis import cloud


def _github(key: str) -> str:
    import json
    import urllib.request
    req = urllib.request.Request("https://api.github.com/user", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return f"signed in as {json.loads(r.read())['login']}. Restart Athena to start the GitHub tools."


def _wolfram(key: str) -> str:
    from jarvis.tools.calc import ask_wolfram
    out = ask_wolfram("2+2", key)
    if out.startswith("ERROR"):
        raise RuntimeError(out[7:])
    return "Wolfram answered. Ask Athena for something step by step."


def _telegram(key: str) -> str:
    from jarvis import telegram
    return f"your bot is {telegram.check(key)}. Restart Athena, then ask her for the Telegram code."


# Keys that aren't a brain: each checked its own way.
OTHER = {"telegram": _telegram, "github": _github, "wolfram": _wolfram}

print("Providers:", ", ".join(sorted(cloud.PROVIDERS) + sorted(OTHER)), "(or a new name - tell Claude which)")
while True:
    p = input("\nProvider (blank to finish): ").strip().lower()
    if not p:
        break
    key = getpass(f"{p} key (hidden): ").strip()
    if not key:
        continue
    cloud.set_key(p, key)
    if p in OTHER:
        try:
            print(f"  saved - {OTHER[p](key)}")
        except Exception as e:
            print(f"  saved, but {p} didn't accept it: {str(e)[:150]}")
        continue
    if p not in cloud.PROVIDERS:
        print(f"  saved. Claude still has to wire '{p}' into Athena.")
        continue
    try:
        model = {"groq": "groq:llama-3.1-8b-instant", "gemini": "gemini:gemini-2.0-flash",
                 "openrouter": "openrouter:meta-llama/llama-3.2-3b-instruct:free",
                 "cerebras": "cerebras:llama3.1-8b", "nvidia": "nvidia:meta/llama-3.1-8b-instruct",
                 "anthropic": "anthropic:claude-haiku-5-5"}[p]
        print("  saved -", "works" if cloud.chat(model, [{"role": "user", "content": "Say ok"}]) else "saved, but the test got no reply")
    except Exception as e:
        print(f"  saved, but the test failed: {str(e)[:150]}")
print("\nDone. Tell Claude which providers you added.")
