# Athena — a local AI assistant

A private AI assistant that runs entirely on your own PC. No API keys, no cloud,
no data leaving the machine. It controls apps, manages files, searches the web,
answers questions about your documents, and sets reminders.

## What it runs on

| | |
|---|---|
| Model | `qwen2.5:7b` via Ollama — chosen for reliable tool calling at 8 GB VRAM |
| Embeddings | `nomic-embed-text` for document search |
| Speech-to-text | faster-whisper on the GPU |
| Speech-to-speech out | Windows SAPI voice |

Measured on this machine (RTX 4060 Laptop, 8 GB): **~54 tokens/sec**, most
requests answered in 2–10 seconds.

## Setup

```bash
pip install -r requirements.txt
```

Then pull the models (Ollama must be installed):

```bash
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

Check everything is wired up:

```bash
python run.py doctor
```

## If it suddenly gets very slow

This is the one failure mode worth knowing about. Ollama silently falls back to
running the model on the CPU if it cannot reach the GPU at startup — everything
still works, it is just **40x slower** (three minutes per reply instead of four
seconds). It happened during this build, right after installing Ollama.

`python run.py doctor` reports which processor the model is on, and the terminal
UI warns at startup. To fix it, restart the Ollama server:

```bash
taskkill /F /IM ollama.exe
```

Then relaunch Ollama and confirm with `ollama ps` — the PROCESSOR column should
read `100% GPU`.

## Running it

```bash
python run.py           # terminal chat
python run.py gui       # desktop window
python run.py hotkey    # background, Ctrl+Alt+Space popup anywhere
python run.py voice     # talk to it
python run.py tools     # list everything it can do
```

## Starting with Windows

```bash
python run.py autostart on
python run.py autostart status
python run.py autostart off
```

Which interfaces start is `autostart.modes` in `config.yaml` (`hotkey`, `voice`,
`gui`). This writes one file — `Athena.vbs` in your Startup folder — which you
can read and delete by hand. No registry keys, no scheduled tasks, no admin
rights. It waits 20 seconds after login so Windows and networking settle first.

`.vbs` rather than `.bat` because it is the only reliable way to start a process
on Windows without a console window flashing up at login.

## What it can do

41 tools across six areas. `python run.py tools` lists them all.

**PC control** — open and close apps, focus/minimise/maximise windows, set
volume, media keys, screenshots, clipboard, lock, shut down, system stats.

**Files** — search by name or age, read PDFs/Word/text, write files, move,
delete to Recycle Bin, sort a messy folder into categories, measure folder sizes.

**Web** — search, read pages as text, download files, weather, open URLs.

**Your documents** — index a folder once, then ask questions about its contents.

**Reminders** — one-off or recurring, surviving restarts.

**Messaging** — WhatsApp and email by contact name. Build up a contact book
(*"save papa's number as 919876543210"*) and then *"send papa a whatsapp saying
running late"*.

**Memory** — tell it to remember something and it persists across sessions.

### Messaging composes, you send

Both WhatsApp and email open with the message filled in and wait for you to
press send. This is deliberate, not unfinished:

- The **Microsoft Store build of WhatsApp Desktop ignores `whatsapp://` deep
  links** — it launches the app and silently discards the phone number and
  text. `https://wa.me/<number>?text=<message>` is the route that works.
- Auto-sending would mean simulating an Enter keypress, and **Windows blocks
  background apps from taking foreground focus** (a security feature). A blind
  keystroke does not know which chat is focused, so the failure mode is a
  message reaching the wrong person. That is not a risk worth one keypress.

Anything that needs another app's window in the foreground is unreliable from
a background assistant. Launching apps, files, settings, radios and reminders
are all solid; driving another program's interface is not.

### Class work is checked before you see it

Attach a worksheet and it is answered one question at a time, rendered to a
page with real equations, tables and graphs. Asking all eight questions in one
pass is measurably worse — the model drops the instructions it was given and
stops drawing graphs entirely — so each question gets its own turn.

Then the arithmetic is checked in Python, because a 7B model's method is far
better than its sums. On the first probability worksheet every single wrong
answer was arithmetic, not method: `(4/5)^9` written as `0.214748` instead of
`0.134218`, a twelve-term total of `22806` instead of `25145`, `10.5/3.08`
called `3.57`. Telling the model to check its own work does not fix this — it
checked, wrote *"this is incorrect because probabilities cannot be negative"*,
and then used the negative probability anyway.

So `jarvis/verify.py` evaluates every `a = b = c` chain in the answer with
exact rational arithmetic and hands back the specific line that disagrees with
itself. That question is re-asked with its own mistakes quoted. Two rules,
both deliberately narrow:

- every arithmetic step in a chain must agree with the others — exactly, when
  the numbers are exact;
- anything the **conclusion** states as a probability must lie in `[0, 1]`.

Scoped to the conclusion because a model solving a cubic properly writes
*"p(1) = −6, so c = 1 is rejected"* on the way past, and complaining about
that sends a correct answer back to be redone.

It will not catch a conceptual mistake — substituting `P(X ≥ 3)` where the
question asked for `P(X > 3)` is arithmetic that adds up perfectly. Anything
still wrong after the retry is **marked on the page** rather than hidden, so
you know which steps to check by hand. Turn it off with `study.verify: false`.

```bash
python tests/verifytest.py
```

### Speak names, type numbers

Whisper is weakest on long digit strings. A number saved by voice in testing
came out as `91980578868` — wrong, and it shadowed the correct entry until it
was found. Type phone numbers, or read them back before trusting them.

Examples:

```
open chrome and search for flights to goa
find every pdf in my downloads from the last two weeks
what's eating my memory right now
index my documents folder
what did my lease agreement say about notice period
remind me to stand up every hour
organise my downloads folder
```

## Safety

Configured in `config.yaml`:

- **`allowed_roots`** — file tools refuse to touch anything outside these folders.
  Defaults to your user folder. Widen it deliberately.
- **`blocked_patterns`** — shell commands matching these are refused outright.
- **`confirm_destructive`** — deleting, moving, closing apps, running shell
  commands and powering off all ask first. Turning this off is not advised.

Deletes go to the Recycle Bin, never a hard delete.

## Adding your own tools

One function, no boilerplate. The JSON schema is generated from your type hints
and docstring:

```python
from ..registry import tool

@tool(tags=["custom"])
def coffee_count(days: int = 1) -> str:
    """Report how many coffees were logged.

    days: how many days back to count
    """
    return f"{days * 3} coffees."
```

Drop it in `jarvis/tools/`, import it from `jarvis/tools/__init__.py`, restart.
The model can now call it.

Mark anything risky with `@tool(destructive=True)` and it inherits the
confirmation prompt.

## A note on `winapi.py`

Window control, media keys, typing and screenshots go through direct Win32
calls rather than `pyautogui`/`pygetwindow`. Those two packages ship as
source-only distributions that build incorrectly on current setuptools — they
install their `__init__.py` without the Windows backend modules, so they import
and then fail at runtime. `jarvis/winapi.py` replaces both with about a hundred
lines of `ctypes`: no build step, no network, nothing to break on reinstall.

## Tuning

In `config.yaml`:

- **Faster but dimmer** — `model: qwen2.5:3b`.
- **Better at class work** — `study.model`, separate from the everyday model so
  conversation stays quick. See *Class work is checked before you see it*.
- **More patience on complex requests** — raise `llm.max_steps`.
- **Less wandering** — lower `llm.temperature`.

## Honest limitations

A 7B model is capable but not brilliant. It handles single and two-step
instructions well. Long chains of dependent reasoning will sometimes go sideways
— it may call the wrong tool, or declare success it did not achieve. The tool
trace printed in the terminal exists so you can see exactly what it really did.

If a request fails, phrasing it as a more direct instruction usually fixes it.
