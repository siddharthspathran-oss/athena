"""Add API keys for Athena without them showing on screen or in any chat.

    python add_keys.py

Asks for a provider and its key (hidden while you type), checks the key with
one tiny request, and saves it to data/api_keys.json. Blank provider = done.
"""
from getpass import getpass

from jarvis import cloud

print("Providers:", ", ".join(sorted(cloud.PROVIDERS)), "(or a new name - tell Claude which)")
while True:
    p = input("\nProvider (blank to finish): ").strip().lower()
    if not p:
        break
    key = getpass(f"{p} key (hidden): ").strip()
    if not key:
        continue
    cloud.set_key(p, key)
    if p not in cloud.PROVIDERS:
        print(f"  saved. Claude still has to wire '{p}' into Athena.")
        continue
    try:
        model = {"groq": "groq:llama-3.1-8b-instant", "gemini": "gemini:gemini-2.0-flash",
                 "openrouter": "openrouter:meta-llama/llama-3.2-3b-instruct:free",
                 "cerebras": "cerebras:llama3.1-8b", "nvidia": "nvidia:meta/llama-3.1-8b-instruct"}[p]
        print("  saved -", "works" if cloud.chat(model, [{"role": "user", "content": "Say ok"}]) else "saved, but the test got no reply")
    except Exception as e:
        print(f"  saved, but the test failed: {str(e)[:150]}")
print("\nDone. Tell Claude which providers you added.")
