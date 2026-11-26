"""Phone interface: a small web server so Athena can be driven from a browser.

Stdlib only, no framework - this has to start reliably at login, not fight a
dependency tree.

Security matters more here than anywhere else in this project. Athena can run
PowerShell, delete files and read documents, so an open port is an open door:

  * every request needs a token, generated once and kept in data/web_token.txt
  * destructive tools are refused unless the phone explicitly confirms
  * the token never appears in a URL that gets logged - the page keeps it in
    localStorage and sends it as a header

Reachable over Tailscale, so it works on mobile data without exposing anything
to the public internet.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import chats, live, registry, render
from ..agent import Agent
from ..config import DATA, cfg
from .web_status import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    _web_log,
    _log,
    _net_prev,
    _cpu,
    _status_seen,
    _watched,
    _cpu_sampler,
    _gpu,
    _gpu_sampler,
    _overview_cache,
    _overview_lock,
    _gather_overview,
    _overview_cached,
    _system_status,
    _where,
    _where_model_runs,
    _live_payload,
    _JOBS,
    _jobs,
)
from .web_files import FileRoutes
from .web_base import (  # noqa: F401 - split out 31 Oct 2026; still importable from here
    TOKEN_FILE,
    _lock,
    get_token,
    ORB_KEY_FILE,
    orb_key,
    _tailscale_https,
    local_ips,
)


_agent: Agent | None = None
_last_steps: list[dict] = []
_allow_destructive = False


# The phone app's page, manifest and service worker: their own files since
# 31 Oct 2026 (they were ~560 lines of strings here), read once at start.
_HERE = Path(__file__).parent
PAGE = (_HERE / "phone_page.html").read_text(encoding="utf-8")


# What the phone installs. "standalone" is what removes the browser bar and
# makes it open like any other app from the home screen.
MANIFEST = (_HERE / "phone_manifest.json").read_text(encoding="utf-8")

# Chrome will not offer to install a site without one of these. It caches
# nothing on purpose - every useful thing here needs the PC, so serving a
# stale shell would only disguise the PC being unreachable.
SERVICE_WORKER = (_HERE / "phone_sw.js").read_text(encoding="utf-8")


def _overview() -> dict:
    """The cached overview, refreshed in the background when stale. Automation
    activity is read fresh - it is a small local file."""
    data = dict(_overview_cached())
    try:
        from .. import rules
        data["activity"] = rules.recent_activity(6)
        data["auto_paused"] = rules.paused()
    except Exception:
        pass
    try:                                     # live attendance (upes.py), from its last read
        from .. import upes
        data["attendance"] = upes.attendance(cached_only=True)
        from ..store import load_json as _lj
        data["upes_read"] = _lj(upes.CACHE, {}).get("t", 0)
        # Signed out is normal (MyUPES ends sign-ins ~25 min in); only stale data is worth showing.
        data["upes_signed_out"] = ((upes.DATA / "upes_signed_out.json").exists()
                                   and time.time() - data["upes_read"] > 20 * 3600)
    except Exception:
        pass
    try:                                       # the phone's Today card: goal tasks left, missions at work
        from .. import goals, missions
        data["goal_today"] = [{"task": t["task"], "goal": g["title"]} for g in goals.load() if g["status"] == "active"
                              for t in goals.today_tasks(g) if not t["done"]][:4]
        data["missions"] = [{"title": m["title"], "status": m["status"]} for m in missions.load()
                            if not m.get("parent") and m["status"] in ("running", "starting")]
        from .. import attention
        data["needs"] = [{"who": i["who"], "title": i["title"]} for i in attention.items()][:5]
    except Exception:
        pass
    return data


def _hud_page() -> bytes:
    """Read on every request, so editing the page needs no restart."""
    page = Path(__file__).parent / "hud.html"
    try:
        return page.read_bytes()
    except OSError:
        return PAGE.encode("utf-8")


class Handler(FileRoutes, BaseHTTPRequestHandler):
    server_version = "Athena"

    def log_message(self, *args) -> None:      # noqa: D102 - quieter console
        pass

    # ---------- helpers ----------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass        # the page hung up first (Stop, a closed window): nothing to deliver to

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Token", ""), get_token())

    def _orb_ok(self) -> bool:
        """The desk orb's own key (data/orb_key.txt): it can read Athena's
        state and press "listen", nothing else - never the full token."""
        return secrets.compare_digest(self.headers.get("X-Orb-Key", ""), orb_key())

    # ---------- routes ----------

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(200, _hud_page(), "text/html; charset=utf-8")
        elif path == "/classic":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/stage":
            # The floating panel (ui/stage_window.py); like the HUD, no data in the page itself.
            self._send(200, (Path(__file__).parent / "stage.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/live":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, _live_payload())
        elif path == "/orb":
            # The desk orb (C:\claude code\orb) polls this a few times a second.
            if not self._orb_ok():
                self._json(401, {"error": "Bad or missing orb key."})
                return
            v = live.read_voice()
            from .. import modes
            self._json(200, {"s": v.get("state", "idle"), "l": round(float(v.get("level") or 0), 2),
                             "m": modes.current()})
        elif path == "/status":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, _system_status())
        elif path.startswith("/view/"):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from . import views
            fn = views.GET.get(path[len("/view/"):])
            if fn is None:
                self._json(404, {"error": "No such page."})
                return
            try:
                self._json(200, fn())
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
        elif path == "/overview":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, _overview())
        elif path == "/health":
            self._json(200, {"ok": True, "tools": len(registry.REGISTRY)})
        elif path == "/manifest.webmanifest":
            # Unauthenticated on purpose: the browser fetches this before the
            # page can hand it a token, and it says nothing secret.
            self._send(200, MANIFEST.encode("utf-8"),
                       "application/manifest+json; charset=utf-8")
        elif path == "/sw.js":
            self._send(200, SERVICE_WORKER.encode("utf-8"),
                       "application/javascript; charset=utf-8")
        elif path in ("/face.js", "/hud.js", "/hud-missions.js", "/hud-live.js", "/hud.css", "/hud-pages.css",
                      "/neuron-brain.js", "/stage.js", "/stage.css"):
            # The HUD's code and styles (no data in them, so no token): served
            # from disk, so an edit only needs a page reload. hud.js and hud.css
            # came out of hud.html on 16 Oct 2026 (it had grown past 200 KB), and were
            # split in order on 31 Oct 2026 (hud-missions.js, hud-live.js, hud-pages.css).
            self._send(200, (Path(__file__).parent / path[1:]).read_bytes(),
                       "text/css; charset=utf-8" if path.endswith(".css") else "application/javascript; charset=utf-8")
        elif path.startswith("/icons/"):
            self._serve_icon(path)
        elif path == "/models":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from ..tools import study
            self._json(200, {"models": study.choices()})
        elif path == "/chats":
            self._list_chats()
        elif path == "/projects":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, {"projects": _projects()})
        elif path.startswith("/chats/") and path.endswith("/export"):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            cid = Path(path[len("/chats/"):-len("/export")]).name
            if cid == "current":
                cid = _agent.memory.chat_id or ""
            text = chats.export_md(cid)
            name = re.sub(r"[^\w\- ]+", "", chats.meta(cid).get("title") or "chat").strip()[:60] or "chat"
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{name}.md"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path.startswith("/chats/"):
            self._open_chat(path[len("/chats/"):])
        elif path.startswith("/answers/"):
            self._serve_answer(path)
        elif path.startswith("/mission/"):
            self._serve_mission(path)
        elif path == "/shares":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from .. import share
            self._json(200, {"shares": share.pending(), "folder": str(share.receive_folder())})
        elif path.startswith("/get/"):
            self._serve_share(path[len("/get/"):])
        else:
            self._json(404, {"error": "not found"})

    # ---------- files between the PC and the phone ----------


    # ---------- saved conversations ----------

    def _list_chats(self) -> None:
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return
        q = urllib.parse.parse_qs(self.path.partition("?")[2]).get("q", [""])[0].strip()
        found = chats.search(q) if q else chats.listing()[:200]
        self._json(200, {"chats": [
            {**c, "when": chats.when(c["updated"]),
             "live": c["id"] == _agent.memory.chat_id}
            for c in found], "projects": _projects(), "query": q})

    def _open_chat(self, chat_id: str) -> None:
        """Switch to a saved conversation and hand back its messages."""
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return
        chat_id = Path(chat_id).name
        with _lock:
            if chat_id == "current":
                if _agent.memory.stale():       # back after a break: a fresh chat, not the old one
                    _agent.memory.start_chat()
            elif chat_id == "new":
                pid = urllib.parse.parse_qs(self.path.partition("?")[2]).get("project", [""])[0]
                _agent.memory.start_chat(project=pid if chats.project(pid) else "")
            elif not chats.load(chat_id):
                self._json(404, {"error": "No such chat."})
                return
            else:
                _agent.memory.start_chat(chat_id)
            history = [{"role": m.get("role"), "text": m.get("content")}
                       for m in _agent.memory.full
                       if m.get("role") in ("user", "assistant")
                       and isinstance(m.get("content"), str)
                       and m["content"].strip()]
        self._json(200, {"id": _agent.memory.chat_id, "messages": history})


    def do_HEAD(self) -> None:
        """Answer a HEAD like the GET it mirrors, minus the body.

        BaseHTTPRequestHandler returns 501 without this, and reverse proxies
        and install checks probe with HEAD before they will trust a site.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()


    def do_DELETE(self) -> None:
        route = self.path.split("?")[0]
        if not route.startswith("/chats/"):
            self._json(404, {"error": "not found"})
            return
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token."})
            return
        chat_id = Path(route[len("/chats/"):]).name
        with _lock:
            gone = chats.delete(chat_id)
            if chat_id == _agent.memory.chat_id:
                _agent.memory.start_chat()
        self._json(200, {"deleted": gone, "id": _agent.memory.chat_id})

    def do_POST(self) -> None:
        route = self.path.split("?")[0]
        if route == "/ask/stop":                    # the Stop button: no lock, the answer holds it
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            _agent.cancelled = True
            self._json(200, {"ok": True})
            return
        if route in ("/chats/rewind", "/projects", "/projects/delete") or (
                route.startswith("/chats/") and route.endswith("/project")):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                body = {}
            if route == "/chats/rewind":            # Edit / Regenerate: cut back to before question n
                with _lock:
                    ok = _agent.memory.rewind(int(body.get("n", -1)), str(body.get("text", "")))
                self._json(200 if ok else 400, {"ok": ok})
            elif route == "/projects":
                self._json(200, {"project": chats.save_project(str(body.get("name", "")), str(body.get("notes", "")),
                                                               str(body.get("id", "")))})
            elif route == "/projects/delete":
                self._json(200, {"ok": chats.delete_project(str(body.get("id", "")))})
            else:                                   # /chats/<id>/project {"project": id or ""}
                cid = Path(route[len("/chats/"):-len("/project")]).name
                pid = str(body.get("project", ""))
                ok = (not pid or bool(chats.project(pid))) and chats.update(cid, project=pid)
                if ok and cid == _agent.memory.chat_id:
                    _agent.memory.project = pid
                self._json(200 if ok else 404, {"ok": bool(ok)})
            return
        if route.startswith("/chats/") and route.rsplit("/", 1)[-1] in ("rename", "pin"):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            chat_id, what = Path(route[len("/chats/"):].rsplit("/", 1)[0]).name, route.rsplit("/", 1)[-1]
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                body = {}
            ok = (chats.rename(chat_id, str(body.get("title", ""))) if what == "rename"
                  else chats.pin(chat_id, bool(body.get("on"))))
            self._json(200 if ok else 404, {"ok": ok})
            return
        if route.startswith("/chats/") and route.endswith("/restore"):      # Undo a delete
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            chat_id = Path(route[len("/chats/"):-len("/restore")]).name
            self._json(200, {"restored": chats.restore(chat_id)})
            return
        if route == "/orb/listen":                  # the orb's button: same as the HUD's mic button
            if not self._orb_ok():
                self._json(401, {"error": "Bad or missing orb key."})
                return
            live.request_wake()
            self._json(200, {"ok": True})
            return
        if route == "/look":
            self._look()
            return
        if route == "/read":
            self._read_document()
            return
        if route.startswith("/view/"):
            # Buttons on the Command Center pages: run a routine, control the
            # music, add or tick off a task, forget a memory.
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._json(400, {"error": "bad request"})
                return
            from . import views
            page = route[len("/view/"):]
            try:
                if page == "music":
                    out = views.music_action(body.get("action", ""))
                elif page == "routine":
                    out = views.run_routine(body.get("name", ""))
                elif page == "tasks":
                    out = views.task_action(body)
                elif page == "rules":                     # the Automations page: switch one off, remove one
                    out = views.rule_action(body)
                elif page == "goals":                     # the Goals page: tick a task, replan, drop
                    out = views.goal_action(body)
                elif page == "missions":                  # the Missions page: start, stop, follow up, open folder
                    out = views.mission_action(body)
                elif page == "alarm":
                    from .. import alarm
                    out = {"said": alarm.snooze(int(body.get("minutes", 10))) if body.get("action") == "snooze"
                           else alarm.stop()}
                elif page == "automation":
                    from .. import rules
                    out = {"said": rules.pause_automation() if body.get("pause") else rules.resume_automation(),
                           "paused": rules.paused()}
                elif page == "notify":
                    from .. import phone_notify
                    out = phone_notify.set_muted(body.get("muted", True))
                elif page == "forget":
                    out = views.forget(body.get("text", ""))
                elif page == "phone":
                    out = views.phone_action(body)
                else:
                    self._json(404, {"error": "No such action."})
                    return
            except Exception as e:
                out = {"said": f"ERROR: {e}"}
            _log(f"page action: {page} {body.get('action') or body.get('name') or ''}".strip())
            self._json(200, out)
            return
        if route.startswith("/projects/") and route.endswith("/files"):      # add a file to a project
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            pid = Path(route[len("/projects/"):-len("/files")]).name
            name = urllib.parse.parse_qs(self.path.partition("?")[2]).get("name", ["file"])[0]
            size = int(self.headers.get("Content-Length") or 0)
            if size > chats.MAX_FILE:
                self._json(413, {"error": f"{name}: over 30 MB."})
                return
            try:
                self._json(200, {"file": chats.add_file(pid, name, self.rfile.read(size))})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            return
        if route.startswith("/projects/") and route.endswith("/files/delete"):
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            pid = Path(route[len("/projects/"):-len("/files/delete")]).name
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                body = {}
            self._json(200, {"ok": chats.remove_file(pid, str(body.get("name", "")))})
            return
        if route == "/upload":
            self._receive_upload()
            return
        if route in ("/push/key", "/push/subscribe", "/push/test"):     # the phone app's own notifications
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from .. import webpush
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if route == "/push/key":
                    self._json(200, {"key": webpush.public_key()})
                elif route == "/push/subscribe":
                    n = webpush.subscribe(body.get("sub") or {}, str(body.get("label") or ""))
                    self._json(200, {"ok": True, "phones": n})
                else:
                    n = webpush.send("Athena", "Notifications from the Athena app are on.", "/", tag="test")
                    self._json(200, {"ok": bool(n), "sent": n})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:
                self._json(500, {"error": str(e)[:200]})
            return
        if route == "/voice/start":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            self._json(200, {"said": start_voice()})
            return
        if route == "/wake":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            live.request_wake()
            _log("mic: listening for your next sentence")
            self._json(200, {"ok": True})
            return
        if route == "/model":
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            try:
                n = int(self.headers.get("Content-Length", 0))
                wanted = json.loads(self.rfile.read(n) or b"{}").get("model", "")
            except Exception:
                self._json(400, {"error": "bad request"})
                return
            from ..tools import study
            self._json(200, {"said": study.set_model(wanted),
                             "model": study.current_model()})
            return
        if route in ("/phone/hear", "/phone/say"):      # phone voice: the voice process hears and speaks
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            from .. import phonevoice
            n = int(self.headers.get("Content-Length") or 0)
            if n > phonevoice.MAX_AUDIO:
                self._json(413, {"error": "That recording is too long."})
                return
            body = self.rfile.read(n)
            try:
                if route == "/phone/hear":
                    self._send(200, phonevoice.call("/stt", body, self.headers.get("Content-Type", "")),
                               "application/json")
                else:
                    self._send(200, phonevoice.call("/tts", body, "application/json"), "audio/wav")
            except RuntimeError as e:
                self._json(503, {"error": str(e)})
            return
        if route == "/remind":           # athena-server fired a reminder and rang the phone: say it here too
            if not self._authorised():
                self._json(401, {"error": "Bad or missing token."})
                return
            try:
                text = str(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                           .get("text", "")).strip()[:500]
            except ValueError:
                text = ""
            if not text:
                self._json(400, {"error": "empty reminder"})
                return
            from ..tools import scheduler
            scheduler.say_now(text)
            _log(f"reminder from athena-server: {text[:60]}")
            self._json(200, {"ok": True})
            return
        if route != "/ask":
            self._json(404, {"error": "not found"})
            return
        if not self._authorised():
            self._json(401, {"error": "Bad or missing token. Reopen the link from your PC."})
            return

        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"error": "bad request"})
            return

        text = (payload.get("text") or "").strip()
        if not text:
            self._json(400, {"error": "empty request"})
            return

        global _allow_destructive
        with _lock:
            _last_steps.clear()
            _allow_destructive = bool(payload.get("confirm"))
            blocked: list[str] = []

            def confirm(name: str, args: dict) -> bool:
                if _allow_destructive:
                    return True
                blocked.append(f"{name}({', '.join(f'{k}={v}' for k, v in args.items())})")
                return False

            from .. import share, telegram
            # Relayed by athena-server from Telegram: the same as Telegram here
            # used to be - a risky step is offered, and "yes" next does it.
            via_telegram = payload.get("via") == "telegram"
            _agent.confirm = _agent._offer_instead if via_telegram else confirm
            _log(f"{'telegram' if via_telegram else 'typed'}: {text[:70]}")
            # Typed on the phone (or the HUD): "send me X" means this device,
            # and it is already on screen, so no push is needed.
            share.from_phone = not via_telegram
            try:
                answer = _agent.ask(text)
                _log(f"replied: {answer.splitlines()[0][:70] if answer else ''}")
                if via_telegram:
                    answer = telegram._files_named(answer)   # the files are here, not on the server
            except Exception as e:
                self._json(500, {"error": f"Athena failed: {e}"})
                return
            finally:
                share.from_phone = False

            if blocked:
                self._json(200, {
                    "answer": f"This needs confirmation: {blocked[0]}",
                    "needs_confirm": True,
                    "steps": list(_last_steps),
                })
                return
            self._json(200, {"answer": answer, "steps": list(_last_steps)})


# ---------------------------------------------------------------- voice supervisor
# Voice is the process that hears you, and nothing restarted it when it died:
# on 25 Sep 2026 Windows closed its hidden console as "not responding" at
# 14:30 and the HUD said VOICE OFFLINE for twenty minutes until someone asked
# why. The web server is always running, so it keeps an eye on voice.

def _voice_pids() -> list[int]:
    import psutil
    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "python" in (p.info["name"] or "").lower() and "run.py" in cmd and " voice" in cmd:
                out.append(p.pid)
        except Exception:
            continue
    return out


def _projects() -> list[dict]:
    """Projects with their files, for the chat list."""
    from .. import goals
    out = []
    for p in chats.projects():
        g = goals.for_project(p["id"])
        out.append({**p, "files": chats.project_files(p["id"]), "goal": {
            "title": g["title"], "pct": round(100 * sum(t["done"] for t in g["tasks"]) / max(1, len(g["tasks"]))),
            "today_left": sum(1 for t in goals.today_tasks(g) if not t["done"])} if g else None})
    return out


def start_voice() -> str:
    """Launch the voice process, windowless, if it is not already running."""
    import subprocess
    import sys

    if _voice_pids():
        return "Voice is already running."
    try:
        (DATA / "live_voice.json").unlink(missing_ok=True)   # clear a "stopped" mark
    except OSError:
        pass
    pyw = Path(sys.executable).with_name("pythonw.exe")
    root = Path(__file__).resolve().parents[2]
    subprocess.Popen([str(pyw if pyw.exists() else sys.executable), "run.py", "voice"], cwd=str(root),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _log("voice: starting")
    return "Starting voice - give it about 20 seconds to load."


def _supervise_voice() -> None:
    time.sleep(40)                      # let autostart bring it up first
    while True:
        try:
            if cfg.get("voice.supervise", True) and not _voice_pids():
                if live.read_voice().get("state") != "stopped":
                    _log("voice was not running - restarting it")
                    start_voice()
                    time.sleep(60)      # give it time to load before checking again
        except Exception:
            pass
        time.sleep(20)


def run() -> None:
    global _agent

    port = int(cfg.get("web.port", 8080))
    token = get_token()

    # A question asked from the phone should not make a browser window appear
    # on the PC, possibly in another room. The page is still written, and the
    # phone gets a link to it.
    render.AUTO_OPEN = False

    threading.Thread(target=_supervise_voice, daemon=True, name="voice-supervisor").start()
    try:
        from .. import mcp             # typed chat gets the same plug-in tools as voice
        mcp.start_all()
    except Exception as e:
        print(f"[mcp: not started ({e})]")
    try:
        from ..tools import taste
        taste.start_sampler()     # what you listen to, for "play something"
        from .. import deadlines
        deadlines.start()          # assignment dates from mail + calendar into Tasks
    except Exception:
        pass
    _log("core online: web interface up")
    _log(f"everyday model: {cfg.get('llm.model', '')}")
    _agent = Agent(on_step=lambda s: _last_steps.append(
        {"tool": s.tool, "result": s.result.splitlines()[0][:70] if s.result else ""}))
    # Phone conversations are saved too, and share the same store as the
    # desktop window - a chat started on the phone is there on the PC.
    recent = chats.listing()
    _agent.memory.start_chat(recent[0]["id"] if recent else None)

    ok, msg = _agent.llm.available()
    if not ok:
        print(msg)
        return

    def telegram_ask(text: str) -> str:
        # Same agent and conversation as the phone page; a destructive step is
        # offered, and "yes" as the next message does it (offer.py).
        with _lock:
            _agent.confirm = _agent._offer_instead
            _log(f"telegram: {text[:70]}")
            return _agent.ask(text)
    try:
        from .. import server, telegram
        if server.elsewhere():
            _log("telegram: athena-server answers it and asks this laptop")
        else:
            telegram.start(telegram_ask, _log)
    except Exception as e:
        print(f"[telegram: not started ({e})]")

    from ..tools import scheduler
    scheduler.start_scheduler("web")
    from .. import skillfinder
    skillfinder.start(_log)            # new skill ideas to the phone once a week

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Athena web interface on port {port}")
    print("\nOpen this on your phone (the token is in the link, once):\n")
    https = _tailscale_https()
    if https:
        # Named first: a browser will only offer to install a site, and only
        # grant it a camera, over HTTPS.
        print(f"  installable  {https}/?t={token}")
    for ip in local_ips():
        label = "  tailscale" if ip.startswith("100.") else "  local Wi-Fi"
        print(f"{label:14} http://{ip}:{port}/?t={token}")
    print(f"\n  token: {token}")
    print("\nCtrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    run()
