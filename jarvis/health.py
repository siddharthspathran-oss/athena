"""Self-maintenance: a nightly check, fixes Athena can make herself, a morning
report, and her mistakes saved as material for the next training round.

On 3 Oct 2026 one morning had Whisper stuck on the CPU (5 s to hear you),
Ollama dropping mid-request and a crashing close_app - all visible in the logs,
none noticed until you were annoyed. The nightly check reads the same logs.

    data/health_report.json     last night's check, read by the morning brief
    data/learn_candidates.jsonl requests that went wrong - fed to the next
                                fine-tuning round (finetune/gen_data.py)
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import threading
import time

from .config import DATA, ROOT, cfg
from .registry import tool
from .store import load_json, save_json

REPORT = DATA / "health_report.json"
CANDIDATES = DATA / "learn_candidates.jsonl"


def _run_test(name: str) -> tuple[bool, str]:
    try:
        p = subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "tests" / name)], capture_output=True,
                           text=True, timeout=600, cwd=str(ROOT), encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tail = [l for l in p.stdout.splitlines() if l.strip()][-1:] or [""]
        return p.returncode == 0, tail[0][:120]
    except Exception as e:
        return False, f"could not run: {e}"


def _failures_since(t0: float) -> list[dict]:
    out = []
    try:
        for line in (DATA / "failures.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if float(r.get("t") or 0) >= t0:
                    out.append(r)
    except Exception:
        pass
    return out


def check(fix: bool = True) -> dict:
    """Run every check; fix what can be fixed. Returns the report."""
    from .llm import LLM, heal_gpu
    report: dict = {"t": time.time(), "problems": [], "fixed": [], "ok": []}
    # 1. The tests that pin down every bug fixed so far.
    for name in ("routetest.py", "answerpagetest.py"):
        ok, tail = _run_test(name)
        (report["ok"] if ok else report["problems"]).append(f"{name}: {tail}")
    # 2. The model on the GPU.
    try:
        said = heal_gpu(LLM()) if fix else ""
        if said:
            report["fixed"].append(said)
        else:
            report["ok"].append("language model on the GPU")
    except Exception as e:
        report["problems"].append(f"Ollama: {e}")
    # 3. Whisper on the GPU (voice.log says which it settled on).
    try:
        log = (DATA / "voice.log").read_text(encoding="utf-8", errors="replace")
        if "running on the CPU" in log[-20000:] and "Whisper" in log:
            report["problems"].append("speech recognition fell back to the CPU - restart voice to fix")
        else:
            report["ok"].append("speech recognition on the GPU")
    except Exception:
        pass
    # 4. Disk.
    free = shutil.disk_usage("C:\\").free / 2**30
    (report["ok"] if free > 15 else report["problems"]).append(f"{free:.0f} GB free on C:")
    # 5. What went wrong in the last day - and keep it for training.
    fails = _failures_since(time.time() - 86400)
    kinds: dict[str, int] = {}
    for f in fails:
        for k in str(f.get("kind", "")).split(", "):
            k = k.split(" (")[0].split(":")[0]
            kinds[k] = kinds.get(k, 0) + 1
    report["mistakes"] = len(fails)
    report["mistake_kinds"] = dict(sorted(kinds.items(), key=lambda x: -x[1])[:5])
    report["examples"] = [f.get("said", "")[:80] for f in fails[-5:]]
    known = set()
    try:
        known = {json.loads(l)["said"] for l in CANDIDATES.read_text(encoding="utf-8").splitlines() if l.strip()}
    except Exception:
        pass
    added = 0
    with open(CANDIDATES, "a", encoding="utf-8") as fh:
        for f in fails:
            said = (f.get("said") or "").strip()
            if said and said not in known and not str(f.get("kind", "")).startswith("slow"):
                fh.write(json.dumps({"said": said, "answer": f.get("answer", ""), "kind": f.get("kind", ""),
                                     "t": f.get("t")}) + "\n")
                known.add(said)
                added += 1
    report["learn_total"] = len(known)
    report["learn_added"] = added
    save_json(REPORT, report)
    try:
        from . import rules
        rules.log(f"nightly check: {len(report['problems'])} problem(s), {len(report['fixed'])} fixed, "
                  f"{len(fails)} mistake(s) yesterday", "health")
    except Exception:
        pass
    return report


def summary(r: dict | None = None) -> str:
    r = r or load_json(REPORT, {})
    if not r:
        return "I haven't run a health check yet."
    when = time.strftime("%H:%M", time.localtime(r["t"]))
    bits = [f"Health check at {when}:"]
    bits.append("everything fine." if not r["problems"] else f"{len(r['problems'])} problem(s) - " + "; ".join(r["problems"]) + ".")
    if r.get("fixed"):
        bits.append("Fixed myself: " + "; ".join(r["fixed"]) + ".")
    if r.get("mistakes"):
        top = ", ".join(f"{k} ({v})" for k, v in r.get("mistake_kinds", {}).items())
        bits.append(f"{r['mistakes']} thing(s) went wrong yesterday: {top}.")
    if r.get("learn_total", 0) >= int(cfg.get("health.retrain_after", 40)):
        bits.append(f"I've saved {r['learn_total']} mistakes to learn from - say 'retrain Athena' to train on them tonight.")
    return " ".join(bits)


def morning_line() -> str:
    """For the daily brief: last night's check, if it found anything worth saying."""
    r = load_json(REPORT, {})
    if not r or time.time() - r.get("t", 0) > 20 * 3600:
        return ""
    if not r["problems"] and not r.get("fixed") and r.get("mistakes", 0) == 0:
        return ""
    return summary(r)


@tool(tags=["system"], timeout=900)
def health_check() -> str:
    """Check Athena herself - tests, GPU, speech recognition, disk - fix what
    she can, and report. Use for "how are you running", "check yourself",
    "health check", "any problems"."""
    return summary(check())


@tool(tags=["system"], timeout=60)
def retrain_athena() -> str:
    """Schedule tonight's fine-tuning round on the mistakes Athena has saved.
    The new model is tested against the current one and only offered if it wins."""
    n = sum(1 for _ in open(CANDIDATES, encoding="utf-8")) if CANDIDATES.exists() else 0
    ft = ROOT.parent / "finetune"
    if not (ft / "run_tonight.sh").exists():
        return "The training setup isn't on this PC."
    shutil.copy(CANDIDATES, ft / "data" / "learn_candidates.jsonl") if CANDIDATES.exists() else None
    at = cfg.get("health.retrain_at", "00:30")
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    ps = (f"$a = New-ScheduledTaskAction -Execute 'C:\\Program Files\\Git\\bin\\bash.exe' "
          f"-Argument '\"/c/claude code/finetune/run_tonight.sh\"' -WorkingDirectory 'C:\\claude code\\finetune'; "
          f"$t = New-ScheduledTaskTrigger -Once -At (Get-Date '{tomorrow} {at}'); "
          f"$s = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
          f"Register-ScheduledTask -TaskName 'Athena finetune' -Action $a -Trigger $t -Settings $s -Force | Out-Null")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (f"Training is set for {at} tonight on {n} saved mistakes plus the original examples. "
            "Leave the laptop on and plugged in; I'll compare the result with the current model in the morning.")


def _at_offline_class(now: dt.datetime) -> bool:
    """True during a class on the UPES timetable that is in a room, not on Teams."""
    try:
        from . import upes
        hhmm = now.strftime("%H:%M")
        return any(c["start"] <= hhmm < c["end"] and "teams" not in c["room"].lower()
                   for c in upes.classes(now.date(), cached_only=True) if not c["cancelled"])
    except Exception:
        return False


def start() -> None:
    """The nightly check, in the voice process. A PC that is shut down at
    night never saw 03:00, so the check never ran (4 Oct 2026): when the last
    one is over 20 hours old, it runs the next time the PC has sat idle for
    10 minutes instead - never while you are using it."""
    def loop():
        from .tools.teams import _idle_seconds
        tried = 0.0
        while True:
            now = dt.datetime.now()
            age = time.time() - load_json(REPORT, {}).get("t", 0)
            night = now.strftime("%H:%M") >= cfg.get("health.at", "03:00") and now.hour < 6
            try:
                from . import gaming
                idle = _idle_seconds() >= 600 and not gaming.on()
            except Exception:
                idle = False
            # Asked for (4 Oct 2026): run it while you are out at an offline
            # class - not a Teams one, when you are at the PC. Idle time alone
            # only once two days have gone by without a class (exams, holidays).
            due = age > 20 * 3600 and (night or (idle and (_at_offline_class(now) or age > 48 * 3600)))
            if due and time.time() - tried > 2 * 3600:
                tried = time.time()            # a check that crashed is not retried every minute
                try:
                    check()
                except Exception:
                    pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="athena-health").start()
