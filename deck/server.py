"""Athena Deck's local server (aiohttp, 127.0.0.1 only): the board UI, a JSON
API for it and for Athena (deck_link.py), a websocket per console for its
terminal, and one for board events (status changes, new nodes, focus).

Everything but /api/ping and the static UI needs the Deck's token (X-Token
header or ?t=), kept in deck/data/token.txt.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
import threading
import urllib.request
from pathlib import Path

from aiohttp import WSMsgType, web

from . import flows, gitcard, handoff, studio, usage
from .board import FIELDS, SIZES, Board
from .consoles import DATA, HERE

PORT = 8430
UI = HERE / "ui"
MEDIA = DATA / "media"                         # images dropped or pasted on the board
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}   # no SVG: it can carry script
NODE_KEYS = ("kind", "cwd", "cmd", "prompt", "model", "profile") + FIELDS
# What xterm sends back unasked: device attributes, cursor position, status, colour reports.
REPLY = re.compile(r"^(?:\x1b\[[?>]?[\d;]*[cRn]|\x1b\][\d;]+;[^\x07\x1b]*(?:\x07|\x1b\\))+$")
JARVIS_DATA = HERE.parent / "data"              # Athena's own data: the orb key for "Talk to Athena"


def token() -> str:
    f = DATA / "token.txt"
    DATA.mkdir(parents=True, exist_ok=True)
    if not f.exists() or not f.read_text(encoding="utf-8").strip():
        f.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    return f.read_text(encoding="utf-8").strip()


class DeckServer:
    def __init__(self, port: int = PORT):
        self.port = port
        self.loop: asyncio.AbstractEventLoop | None = None
        self.events: set[web.WebSocketResponse] = set()      # every open board page
        self.windows: set[web.WebSocketResponse] = set()     # ...of which the Deck's own window(s)
        self.board = Board(self.emit)
        self.jobs = studio.Jobs(self.emit)
        self.flows = flows.Flows(self.board)
        self.ready = threading.Event()
        self.snaps: dict[str, asyncio.Future] = {}
        self.replies: dict[str, str] = {}            # last terminal-query answer per console, to drop the second window's
        self.reply_at: dict[str, float] = {}
        self.tok = token()

    # ---- events: from any thread to every open board ----
    def emit(self, ev: dict) -> None:
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._broadcast, ev)

    def _broadcast(self, ev: dict) -> None:
        for ws in list(self.events):
            if ws.closed:
                self.events.discard(ws)
            else:
                asyncio.ensure_future(ws.send_str(json.dumps(ev)))

    # ---- app ----
    @web.middleware
    async def auth(self, request: web.Request, handler):
        p = request.path
        if (p.startswith("/api/") and p != "/api/ping") or p.startswith("/ws/"):
            got = request.headers.get("X-Token") or request.query.get("t", "")
            if not secrets.compare_digest(got, self.tok):
                return web.json_response({"error": "bad or missing token"}, status=401)
        resp = await handler(request)
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    def app(self) -> web.Application:
        a = web.Application(middlewares=[self.auth], client_max_size=4 * 1024 * 1024)
        b = self.board
        r = a.router
        r.add_get("/", self.index)
        r.add_static("/ui/", UI)
        MEDIA.mkdir(parents=True, exist_ok=True)
        r.add_static("/media/", MEDIA)
        r.add_post("/api/media", self.media)
        r.add_post("/api/workspaces/{id}/ink", self.ink_add)
        r.add_delete("/api/workspaces/{id}/ink", self.ink_remove)
        r.add_get("/api/git", self.git_status)
        r.add_post("/api/git/{act}", self.git_action)
        r.add_post("/api/consoles/{id}/loops", self.loop_add)
        r.add_patch("/api/consoles/{id}/loops/{lid}", self.loop_set)
        r.add_delete("/api/consoles/{id}/loops/{lid}", self.loop_set)
        r.add_post("/api/handoff", self.handoff)
        r.add_post("/api/wires", self.wire_add)
        r.add_delete("/api/workspaces/{id}/wires/{wire}", self.wire_remove)
        r.add_get("/api/flows", lambda _r: web.json_response({"flows": flows.catalog(b.state), "busy": sorted(self.flows.busy)}))
        r.add_post("/api/flows/run", self.flow_run)
        r.add_post("/api/flows/test", self.flow_test)
        r.add_get("/api/fs/tree", self.fs("tree"))
        r.add_get("/api/fs/file", self.fs("read"))
        r.add_put("/api/fs/file", self.fs("write"))
        r.add_get("/api/fs/notes", self.fs("notes"))
        r.add_get("/api/usage", self.usage)
        r.add_post("/api/profiles", self.profile_add)
        r.add_delete("/api/profiles/{id}", self.profile_remove)
        r.add_put("/api/memory/{scope}", self.memory)
        r.add_post("/api/dictate", self.dictate)
        r.add_post("/api/studio/run", self.studio_run)
        r.add_get("/api/studio/jobs/{id}", self.studio_job)
        r.add_get("/api/ping", lambda _r: web.json_response({"app": "athena-deck", "ok": True, "windows": len(self.windows),
                                                              "pages": len(self.events)}))
        r.add_post("/api/quit", self.quit)
        r.add_get("/api/consoles/{id}/screen", self.screen)
        r.add_post("/api/snap/{req}", self.snap)
        r.add_get("/api/state", lambda _r: web.json_response(b.snapshot()))
        r.add_post("/api/workspaces", self.add_workspace)
        r.add_patch("/api/workspaces/{id}", self.patch_workspace)
        r.add_delete("/api/workspaces/{id}", self.delete_workspace)
        r.add_post("/api/nodes", self.add_node)
        r.add_patch("/api/nodes/{id}", self.patch_node)
        r.add_delete("/api/nodes/{id}", self.delete_node)
        r.add_post("/api/nodes/{id}/restart", self.restart_node)
        r.add_post("/api/consoles/{id}/send", self.send)
        r.add_get("/api/consoles/{id}/tail", self.tail)
        r.add_post("/api/composer", self.composer)
        r.add_post("/api/hook", self.hook)
        r.add_post("/api/focus", self.focus)
        r.add_post("/api/athena/listen", self.athena_listen)
        r.add_get("/ws/term/{id}", self.ws_term)
        r.add_get("/ws/events", self.ws_events)
        return a

    async def index(self, _request):
        page = UI / "dist" / "index.html"             # the React board, built from deck/web (npm run build)
        if not page.exists():
            return web.Response(text="The board isn't built yet: run `npm run build` in deck/web.", status=503)
        return web.FileResponse(page)

    async def _body(self, request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    async def add_workspace(self, request):
        d = await self._body(request)
        folder = str(d.get("folder") or "").strip()
        if not folder or not Path(folder).is_dir():
            return web.json_response({"error": "That folder doesn't exist."}, status=400)
        return web.json_response(self.board.add_workspace(str(d.get("name") or Path(folder).name), folder))

    async def patch_workspace(self, request):
        d = await self._body(request)
        w = self.board.update_workspace(request.match_info["id"], **d)
        return web.json_response(w or {"error": "no such workspace"}, status=200 if w else 404)

    async def delete_workspace(self, request):
        self.board.remove_workspace(request.match_info["id"])
        return web.json_response({"ok": True})

    async def add_node(self, request):
        d = await self._body(request)
        type_ = d.get("type", "console")
        if type_ not in SIZES:
            return web.json_response({"error": "unknown type"}, status=400)
        kw = {k: d[k] for k in NODE_KEYS if k in d and k != "ws"}
        n = await asyncio.to_thread(self.board.add_node, d.get("ws") or self.board.state["active"], type_, **kw)
        if d.get("focus"):
            self.emit({"type": "focus", "id": n["id"]})
        return web.json_response(n)

    async def media(self, request):
        """An image dropped or pasted on the board: stored under its own hash, served from /media/."""
        ext = IMAGE_TYPES.get((request.content_type or "").lower())
        if not ext:
            return web.json_response({"error": "Only PNG, JPEG, GIF or WebP images."}, status=415)
        data = await request.read()
        if not data or len(data) > 20 * 1024 * 1024:
            return web.json_response({"error": "That image is empty or bigger than 20 MB."}, status=413)
        name = f"{hashlib.sha256(data).hexdigest()[:20]}.{ext}"
        (MEDIA / name).write_bytes(data)
        return web.json_response({"src": f"/media/{name}"})

    async def ink_add(self, request):
        s = self.board.add_stroke(request.match_info["id"], await self._body(request))
        return web.json_response(s or {"error": "bad stroke"}, status=200 if s else 400)

    async def ink_remove(self, request):
        self.board.remove_stroke(request.match_info["id"], request.query.get("stroke", ""))
        return web.json_response({"ok": True})

    async def git_status(self, request):
        return web.json_response(await asyncio.to_thread(gitcard.status, request.query.get("path", "")))

    async def git_action(self, request):
        d = await self._body(request)
        r = await asyncio.to_thread(gitcard.action, str(d.get("path", "")), request.match_info["act"], str(d.get("arg", "")))
        return web.json_response(r)

    async def loop_add(self, request):
        try:
            return web.json_response(self.board.add_loop(request.match_info["id"], await self._body(request)))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def loop_set(self, request):
        d = await self._body(request) if request.method == "PATCH" else {}
        ok = self.board.set_loop(request.match_info["id"], request.match_info["lid"], on=d.get("on"),
                                 remove=request.method == "DELETE")
        return web.json_response({"ok": ok}, status=200 if ok else 404)

    async def handoff(self, request):
        d = await self._body(request)
        r = handoff.start(self.board, str(d.get("from", "")), str(d.get("task", "")), str(d.get("to", "")),
                          str(d.get("kind", "")), bool(d.get("summarize", True)))
        return web.json_response(r, status=200 if r["ok"] else 400)

    async def wire_add(self, request):
        d = await self._body(request)
        try:
            return web.json_response(self.board.add_wire(str(d.get("from", "")), str(d.get("to", ""))))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def wire_remove(self, request):
        self.board.remove_wire(request.match_info["id"], request.match_info["wire"])
        return web.json_response({"ok": True})

    async def flow_run(self, request):
        """Run a box with what feeds it and on to where its output lands - or, with whole, its entire flow."""
        d = await self._body(request)
        try:
            nid = str(d.get("node", ""))
            steps = self.flows.run_flow(nid) if d.get("whole") else self.flows.run_from(nid)
            return web.json_response({"ok": True, "steps": steps})
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def flow_test(self, request):
        d = await self._body(request)
        try:
            return web.json_response({"ok": True, "tests": self.flows.test(str(d.get("node", "")))})
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    def fs(self, op: str):
        """The studios' file access - only inside the board's workspace folders (deck/studio.py)."""
        async def handler(request):
            q = dict(request.query)
            if request.method == "PUT":
                q.update(await self._body(request))
            roots = [w["folder"] for w in self.board.state["workspaces"]]
            try:
                if op == "tree":
                    r = studio.tree(roots, q.get("root", ""), q.get("path", ""))
                elif op == "read":
                    r = studio.read(roots, q.get("root", ""), q.get("path", ""))
                elif op == "notes":
                    r = await asyncio.to_thread(studio.notes, roots, q.get("root", ""))
                else:
                    r = studio.write(roots, q.get("root", ""), q.get("path", ""), str(q.get("text", "")))
                return web.json_response(r)
            except (studio.Denied, OSError) as e:
                return web.json_response({"error": str(e)}, status=403 if isinstance(e, studio.Denied) else 500)
        return handler

    async def profile_add(self, request):
        d = await self._body(request)
        try:
            return web.json_response(self.board.add_profile(str(d.get("cli", "")), str(d.get("name", ""))))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def profile_remove(self, request):
        self.board.remove_profile(request.match_info["id"])
        return web.json_response({"ok": True})

    async def memory(self, request):
        try:
            self.board.set_memory(request.match_info["scope"], str((await self._body(request)).get("text", "")))
            return web.json_response({"ok": True})
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def dictate(self, request):
        """Speech to text for a console, on this PC: Athena's voice process already has Whisper warm
        (jarvis/phonevoice.py, 127.0.0.1:8421). The text is typed, never sent - you press Enter."""
        data = await request.read()
        if not data or len(data) > 8 * 1024 * 1024:
            return web.json_response({"error": "That recording is empty or too long."}, status=413)
        try:
            tok = (JARVIS_DATA / "web_token.txt").read_text(encoding="utf-8").strip()
            req = urllib.request.Request("http://127.0.0.1:8421/stt", data=data, method="POST",
                                         headers={"X-Token": tok, "Content-Type": request.content_type or "audio/webm"})
            body = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=60).read())
            return web.json_response({"text": str(json.loads(body).get("text", "")).strip()})
        except Exception as e:
            return web.json_response({"error": f"Athena's voice isn't running, so dictation can't hear you ({e})"}, status=502)

    async def usage(self, _request):
        """Tokens per day per CLI from their own logs (deck/usage.py) - re-read only what changed."""
        return web.json_response(await asyncio.to_thread(usage.report))

    async def studio_run(self, request):
        d = await self._body(request)
        roots = [w["folder"] for w in self.board.state["workspaces"]]
        try:
            root, _ = studio.resolve(roots, str(d.get("root", "")))
            make = studio.doc_prompt if d.get("kind") == "doc" else studio.diagram_prompt
            prompt = make(str(d.get("type", "")), str(d.get("custom", "")))
            job = self.jobs.start("doc" if d.get("kind") == "doc" else "diagram", str(d.get("agent", "claude")), str(root), prompt)
            return web.json_response(job)
        except (studio.Denied, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)

    async def studio_job(self, request):
        job = self.jobs.jobs.get(request.match_info["id"])
        return web.json_response(job or {"error": "no such job"}, status=200 if job else 404)

    async def patch_node(self, request):
        d = await self._body(request)
        n = self.board.update_node(request.match_info["id"], **d)
        if n and {"team", "role"} & d.keys():                    # others show it (chips, mission map); typing doesn't reload
            self.emit({"type": "board"})
        return web.json_response(n or {"error": "no such node"}, status=200 if n else 404)

    async def delete_node(self, request):
        await asyncio.to_thread(self.board.remove_node, request.match_info["id"])
        return web.json_response({"ok": True})

    async def restart_node(self, request):
        n = self.board.node(request.match_info["id"])
        if not n or n["type"] != "console":
            return web.json_response({"error": "no such console"}, status=404)
        fresh = (await self._body(request)).get("fresh", False)
        await asyncio.to_thread(self.board.start_console, n, not fresh)
        self.emit({"type": "restarted", "id": n["id"]})
        return web.json_response({"ok": True})

    async def send(self, request):
        c = self.board.consoles.get(request.match_info["id"])
        d = await self._body(request)
        if not c or not c.proc or not c.proc.isalive():
            return web.json_response({"error": "that console isn't running"}, status=409)
        await asyncio.to_thread(c.send, str(d.get("text", "")), bool(d.get("submit", True)))
        return web.json_response({"ok": True})

    async def tail(self, request):
        c = self.board.consoles.get(request.match_info["id"])
        if not c:
            return web.json_response({"error": "no such console"}, status=404)
        return web.json_response({"text": c.screen_tail(int(request.query.get("n", 2000))), **c.info()})

    async def composer(self, request):
        d = await self._body(request)
        made = await asyncio.to_thread(self.board.composer, d.get("ws") or self.board.state["active"],
                                       d.get("slots") or [], str(d.get("cwd") or ""), str(d.get("prompt") or ""),
                                       tuple(d["origin"]) if d.get("origin") else None, str(d.get("team") or ""))
        if made:
            self.emit({"type": "focus", "id": made[0]["id"], "fit": [m["id"] for m in made]})
        return web.json_response({"made": made})

    async def hook(self, request):
        d = await self._body(request)
        self.board.hook(str(d.get("console", "")), str(d.get("event", "")), str(d.get("message", "")),
                        str(d.get("session", "")))
        return web.json_response({"ok": True})

    async def focus(self, request):
        d = await self._body(request)
        # Off the server's thread: pywebview waits for the page to finish loading, and the page loads
        # from this server - calling it here froze the Deck (20 Oct 2026).
        threading.Thread(target=self.raise_window, daemon=True).start()
        if d.get("id"):
            n = self.board.node(d["id"])
            if n:
                self.board.update_workspace(n["ws"], activate=True)
            self.emit({"type": "focus", "id": d["id"]})
        return web.json_response({"ok": True})

    @staticmethod
    def raise_window() -> None:
        """Bring the Deck's window forward - plain Windows calls, so it works from this process."""
        import ctypes
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, "Athena Deck")
        if hwnd:
            u.ShowWindow(hwnd, 9)                                  # SW_RESTORE
            for top in (-1, -2):                                   # HWND_TOPMOST then NOTOPMOST: to the front
                u.SetWindowPos(hwnd, top, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
            u.SetForegroundWindow(hwnd)

    async def quit(self, _request):
        """The window closed: save, stop every console, and exit."""
        await asyncio.to_thread(self.board.shutdown)
        self.loop.call_later(0.3, self.loop.stop)
        return web.json_response({"ok": True})

    async def screen(self, request):
        """What a console shows right now, as text. The window's xterm knows the real screen
        (the raw stream is full of redraws); without a window, the raw tail."""
        cid = request.match_info["id"]
        c = self.board.consoles.get(cid)
        if not c:
            return web.json_response({"error": "no such console"}, status=404)
        text = ""
        if self.events:
            req = secrets.token_hex(6)
            fut = self.snaps[req] = asyncio.get_running_loop().create_future()
            self._broadcast({"type": "snap", "id": cid, "req": req})
            try:
                text = await asyncio.wait_for(fut, 3)
            except asyncio.TimeoutError:
                text = ""
            finally:
                self.snaps.pop(req, None)
        return web.json_response({"text": text or c.screen_tail(2500), "exact": bool(text), **c.info()})

    async def snap(self, request):
        fut = self.snaps.get(request.match_info["req"])
        if fut and not fut.done():
            fut.set_result(str((await self._body(request)).get("text", "")))
        return web.json_response({"ok": True})

    async def athena_listen(self, _request):
        """The board's mic button: Athena starts listening (her orb endpoint, orb key - nothing more)."""
        try:
            key = (JARVIS_DATA / "orb_key.txt").read_text(encoding="utf-8").strip()
            req = urllib.request.Request("http://127.0.0.1:8420/orb/listen", data=b"{}", method="POST",
                                         headers={"X-Orb-Key": key, "Content-Type": "application/json"})
            await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=4).read())
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"error": f"Athena didn't answer ({e})"}, status=502)

    async def ws_term(self, request):
        c = self.board.consoles.get(request.match_info["id"])
        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        if not c:
            await ws.close()
            return ws
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        push = lambda data: loop.call_soon_threadsafe(q.put_nowait, data)     # noqa: E731
        await ws.send_str(json.dumps({"t": "o", "d": c.subscribe(push)}))

        async def pump():
            while True:
                data = [await q.get()]
                while not q.empty():                     # batch a burst into one frame
                    data.append(q.get_nowait())
                await ws.send_str(json.dumps({"t": "o", "d": "".join(data)}))

        task = asyncio.ensure_future(pump())
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                m = json.loads(msg.data)
                if m.get("t") == "i":
                    d = m.get("d", "")
                    if REPLY.match(d):                   # every open window answers a terminal query: pass one on
                        if self.replies.get(c.id) == d and time.time() - self.reply_at.get(c.id, 0) < 1.5:
                            continue
                        self.replies[c.id], self.reply_at[c.id] = d, time.time()
                    c.write(d)
                elif m.get("t") == "r":
                    c.resize(m.get("c", 100), m.get("r", 30))
        finally:
            task.cancel()
            c.unsubscribe(push)
        return ws

    async def ws_events(self, request):
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        self.events.add(ws)
        if request.query.get("w") == "1":                  # the Deck's own window, not a browser tab
            self.windows.add(ws)
        try:
            async for _ in ws:
                pass
        finally:
            self.events.discard(ws)
            self.windows.discard(ws)
        return ws

    # ---- running ----
    async def _ticker(self):
        n = 0
        while True:
            await asyncio.sleep(1)
            n += 1
            try:
                await asyncio.to_thread(self.board.tick)             # a loop firing types into a console: not on this loop
                if n % 15 == 1:
                    await asyncio.to_thread(self.board.read_costs)
            except Exception as e:
                print(f"[deck tick: {e}]")

    def run(self) -> None:
        """Serve until /api/quit; the board's consoles come back first."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        (DATA / "port.txt").write_text(str(self.port), encoding="utf-8")
        runner = web.AppRunner(self.app(), access_log=None)
        self.loop.run_until_complete(runner.setup())
        self.loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", self.port).start())
        self.board.restore()
        self.loop.create_task(self._ticker())
        self.ready.set()
        self.loop.run_forever()
        self.loop.run_until_complete(runner.cleanup())
