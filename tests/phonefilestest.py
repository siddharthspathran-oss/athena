"""Checks files between the PC and the phone (jarvis/share.py, the /get,
/shares and /upload routes in jarvis/ui/web.py, and the send_to_phone tool).

Runs against a scratch folder and a real web server on a free port, with its
own token, so it needs neither Ollama nor your real data.

    python tests/phonefilestest.py
"""
import ctypes
import http.client
import json
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not hasattr(ctypes, "WinDLL"):
    # Not Windows: stand in for the Win32 modules the tool package imports;
    # nothing checked here calls them.
    from unittest import mock

    ctypes.WinDLL = lambda *a, **k: mock.MagicMock()
    ctypes.windll = mock.MagicMock()
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    for name in ("win32gui", "win32con", "win32api", "win32process", "win32clipboard",
                 "pycaw", "pycaw.pycaw", "comtypes", "comtypes.client", "winsound",
                 "keyboard", "send2trash", "playwright", "playwright.sync_api", "ddgs",
                 "pythoncom", "winreg", "msvcrt"):
        try:
            __import__(name)
        except Exception:
            sys.modules[name] = mock.MagicMock()

from jarvis import offer, share  # noqa: E402
from jarvis.config import DATA, cfg  # noqa: E402

failures = 0


def check(name: str, ok: bool, got=None) -> None:
    global failures
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"   got: {got!r}"))
    if not ok:
        failures += 1


def names() -> None:
    check("path parts dropped", share.safe_name("../../evil.txt") == "evil.txt", share.safe_name("../../evil.txt"))
    check("windows path dropped", share.safe_name("C:\\Windows\\x.dll") == "x.dll")
    check("bad characters", share.safe_name('a<b>:c?.pdf') == "a_b__c_.pdf", share.safe_name('a<b>:c?.pdf'))
    check("reserved device name", share.safe_name("CON.txt") == "_CON.txt", share.safe_name("CON.txt"))
    check("empty name", share.safe_name("..") == "file", share.safe_name(".."))
    check("long name capped", len(share.safe_name("x" * 400 + ".pdf")) <= 160)


def shares(tmp: Path) -> None:
    doc = tmp / "lab manual.pdf"
    doc.write_bytes(b"%PDF lab")
    (tmp / "taken.txt").write_text("1")
    check("unique path", share.unique_path(tmp, "taken.txt").name == "taken (1).txt")

    s = share.create(doc)
    check("share made", s["name"] == "lab manual.pdf" and len(s["id"]) >= 20, s)
    check("share resolves", share.resolve(s["id"]) == doc.resolve(), share.resolve(s["id"]))
    check("share listed", any(p["id"] == s["id"] for p in share.pending()), share.pending())
    check("bad id refused", share.resolve("../../etc/passwd") is None)
    check("unknown id refused", share.resolve("A" * 24) is None)

    # expiry
    data = json.loads(share.SHARES.read_text())
    data[s["id"]]["expires"] = time.time() - 1
    share.SHARES.write_text(json.dumps(data))
    check("expired share gone", share.resolve(s["id"]) is None)
    check("expired share not listed", not any(p["id"] == s["id"] for p in share.pending()))

    # deleted after sharing
    gone = tmp / "gone.txt"
    gone.write_text("x")
    g = share.create(gone)
    gone.unlink()
    check("deleted file refused", share.resolve(g["id"]) is None)

    # refusals
    for bad in ("secrets.txt", "api_keys.json", "id_rsa", "prod.env", "server.pem"):
        f = tmp / bad
        f.write_text("x")
        check(f"secret-looking {bad} refused", bool(share.refusal(f)), share.refusal(f))
    check("missing file refused", bool(share.refusal(tmp / "nope.pdf")))
    check("folder refused", bool(share.refusal(tmp)))
    inside = DATA / "phonefilestest-probe.txt"
    inside.write_text("x")
    try:
        why = share.refusal(inside)
        check("data folder refused", bool(why) and "data folder" in why, why)
    finally:
        inside.unlink(missing_ok=True)
    try:
        share.create(tmp / "secrets.txt")
        check("create raises on refusal", False)
    except ValueError:
        check("create raises on refusal", True)


def tool(tmp: Path) -> None:
    from jarvis.tools import files, finder, phonefiles

    doc = tmp / "signals notes.pdf"
    doc.write_bytes(b"notes")
    saved = finder.find_all, files._resolve, share.notify
    pushed = []
    share.notify = lambda name: pushed.append(name)
    try:
        finder.find_all = lambda q, limit=8, **k: []

        def fake_resolve(q, want_dir=None):
            files._offer_files([str(doc)])
            return None, "I couldn't find a file called x. Closest: signals notes. Want me to open signals notes?"
        files._resolve = fake_resolve
        said = phonefiles.send_to_phone("signal note")
        c = offer.pending()
        check("offer says send", "Want me to send" in said, said)
        check("offer remapped to send_to_phone",
              len(c) == 1 and c[0].tool == "send_to_phone" and c[0].args == {"file": str(doc)}, c)

        said = phonefiles.send_to_phone(str(doc))
        check("full path shared", said.startswith("Sent to your phone [share:"), said)
        check("push names the file", pushed == ["signals notes.pdf"], pushed)

        files.last_file = None
        check("'it' with nothing found", "Which file" in phonefiles.send_to_phone("it"))
        files.last_file = str(doc)
        check("'it' uses the last file", "[share:" in phonefiles.send_to_phone("it"))

        (tmp / "api_keys.txt").write_text("x")
        said = phonefiles.send_to_phone(str(tmp / "api_keys.txt"))
        check("secret file refused by the tool", said.startswith("I can't send that"), said)
    finally:
        finder.find_all, files._resolve, share.notify = saved
        offer.clear()

    # the push itself: the file name only
    from jarvis import phone
    sent = []
    real_push = phone.push
    phone.push = lambda msg, **k: sent.append((msg, k))
    try:
        share.from_phone = True
        share.notify("a.pdf")
        check("no push when asked from the phone", sent == [], sent)
        share.from_phone = False
        share.notify("a.pdf")
        check("push has no link or token", len(sent) == 1 and "http" not in sent[0][0] and "/get/" not in sent[0][0], sent)
    finally:
        phone.push = real_push
        share.from_phone = False


def server(tmp: Path) -> None:
    from jarvis.ui import web, web_files

    # The handler checks the token in web.py, the share/upload routes in web_files.py
    # (split out 31 Oct 2026) - both read it from their own module.
    web.get_token = web_files.get_token = lambda: "tok-123"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def req(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        out = r.status, dict(r.getheaders()), r.read()
        c.close()
        return out

    doc = tmp / "résumé final.pdf"
    doc.write_bytes(b"x" * 3_000_000)
    s = share.create(doc)

    st, _, _ = req("GET", f"/get/{s['id']}")
    check("download without token refused", st == 401, st)
    st, _, _ = req("GET", f"/get/{s['id']}?t=wrong")
    check("download with a wrong token refused", st == 401, st)
    st, h, body = req("GET", f"/get/{s['id']}?t=tok-123")
    check("download works", st == 200 and body == doc.read_bytes(), (st, len(body)))
    check("download is an attachment",
          "attachment" in h.get("Content-Disposition", "") and "UTF-8''r%C3%A9sum%C3%A9" in h.get("Content-Disposition", ""),
          h.get("Content-Disposition"))
    st, _, _ = req("GET", "/get/AAAAAAAAAAAAAAAAAAAAAAAA?t=tok-123")
    check("unknown share 404", st == 404, st)

    st, _, body = req("GET", "/shares")
    check("share list needs the token", st == 401, st)
    st, _, body = req("GET", "/shares", headers={"X-Token": "tok-123"})
    listed = json.loads(body).get("shares", [])
    check("share list", st == 200 and any(x["id"] == s["id"] for x in listed), body[:200])
    check("share list hides the path", all("path" not in x for x in listed), listed)

    data = json.loads(share.SHARES.read_text())
    data[s["id"]]["expires"] = time.time() - 1
    share.SHARES.write_text(json.dumps(data))
    st, _, _ = req("GET", f"/get/{s['id']}?t=tok-123")
    check("expired share 404", st == 404, st)

    inbox = share.receive_folder()
    st, _, _ = req("POST", "/upload?name=a.txt", body=b"hello")
    check("upload without token refused", st == 401, st)
    check("nothing written without token", not inbox.exists() or not any(inbox.iterdir()))

    st, _, body = req("POST", "/upload?name=..%2F..%2Fevil.txt", body=b"hello",
                      headers={"X-Token": "tok-123"})
    j = json.loads(body)
    check("upload saved", st == 200 and (inbox / "evil.txt").read_bytes() == b"hello", (st, j))
    check("upload stays in the folder", not (inbox.parent.parent / "evil.txt").exists())

    st, _, body = req("POST", "/upload?name=evil.txt", body=b"again",
                      headers={"X-Token": "tok-123"})
    check("same name kept apart", st == 200 and json.loads(body)["saved"] == "evil (1).txt"
          and (inbox / "evil.txt").read_bytes() == b"hello", body)

    cfg._d["phone_files"]["max_upload_mb"] = 0.001          # about 1 KB
    st, _, body = req("POST", "/upload?name=big.bin", body=b"x" * 5000,
                      headers={"X-Token": "tok-123"})
    check("too large refused", st == 413 and not (inbox / "big.bin").exists(), (st, body))
    cfg._d["phone_files"]["max_upload_mb"] = 2048

    st, _, body = req("POST", "/upload?name=empty.txt", body=b"", headers={"X-Token": "tok-123"})
    check("empty upload refused", st == 411, (st, body))
    check("no .part files left", not list(inbox.glob("*.part")), list(inbox.iterdir()))

    srv.shutdown()


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t).resolve()
        share.SHARES = tmp / "shares.json"
        cfg._d.setdefault("safety", {})["allowed_roots"] = [str(tmp)]
        cfg._d["safety"]["protected_paths"] = []
        cfg._d["phone_files"] = {"receive_folder": str(tmp / "From phone"),
                                 "max_upload_mb": 2048, "share_minutes": 15, "notify": True}
        names()
        shares(tmp)
        tool(tmp)
        server(tmp)
    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
