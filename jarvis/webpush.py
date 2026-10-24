"""Notifications from the Athena phone app itself (Web Push), alongside ntfy.

The phone app subscribes once (the browser hands it an endpoint on Google's
push service plus two keys); the PC encrypts each alert for that phone and
posts it there. The service worker (ui/web.py SERVICE_WORKER) shows it, and a
tap opens the app at the given page.

Keys: one VAPID key pair, made on first use, in data/ (never committed).
phone.push() sends this and ntfy both: Google accepting a push doesn't mean
the phone showed it (Chrome's notifications can be off).
"""
from __future__ import annotations

import base64
import json
import threading
import time

from .config import DATA
from .store import load_json, save_json

KEY = DATA / "vapid_private.pem"
SUBS = DATA / "push_subs.json"
CONTACT = "mailto:athena@localhost.invalid"   # VAPID wants a contact; nothing personal goes to Google
_lock = threading.Lock()


def _vapid():
    from py_vapid import Vapid
    if not KEY.exists():
        v = Vapid()
        v.generate_keys()
        v.save_key(str(KEY))
    return Vapid.from_file(str(KEY))


def public_key() -> str:
    """The key the phone subscribes with (base64url, uncompressed P-256 point)."""
    from cryptography.hazmat.primitives import serialization
    raw = _vapid().public_key.public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def subscribe(sub: dict, label: str = "") -> int:
    if not (isinstance(sub, dict) and str(sub.get("endpoint", "")).startswith("https://")
            and (sub.get("keys") or {}).get("p256dh") and (sub.get("keys") or {}).get("auth")):
        raise ValueError("not a push subscription")
    with _lock:
        subs = [s for s in load_json(SUBS, []) if s["sub"]["endpoint"] != sub["endpoint"]]
        subs.append({"sub": {"endpoint": sub["endpoint"], "keys": sub["keys"]}, "label": label[:80], "added": time.time()})
        save_json(SUBS, subs)
        return len(subs)


def subscribers() -> int:
    return len(load_json(SUBS, []))


def send(title: str, body: str, url: str = "/", urgent: bool = False, tag: str = "") -> int:
    """Push to every subscribed phone. Returns how many took it; a phone that
    unsubscribed (404/410) is forgotten."""
    from pywebpush import WebPushException, webpush
    data = json.dumps({"title": title, "body": body[:400], "url": url or "/", "urgent": urgent, "tag": tag})
    ok, gone = 0, []
    key = _vapid()
    for s in load_json(SUBS, []):
        try:
            webpush(s["sub"], data, vapid_private_key=key, vapid_claims={"sub": CONTACT},
                    ttl=3600 if not urgent else 600, headers={"Urgency": "high" if urgent else "normal"}, timeout=15)
            ok += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (404, 410):
                gone.append(s["sub"]["endpoint"])
            print(f"[web push failed ({code}): {str(e)[:120]}]")
        except Exception as e:
            print(f"[web push failed: {str(e)[:120]}]")
    if gone:
        with _lock:
            save_json(SUBS, [s for s in load_json(SUBS, []) if s["sub"]["endpoint"] not in gone])
    return ok


if __name__ == "__main__":
    k = public_key()
    assert len(base64.urlsafe_b64decode(k + "=" * (-len(k) % 4))) == 65, "uncompressed P-256 point"
    try:
        subscribe({"endpoint": "http://not-https", "keys": {}})
        raise AssertionError("accepted a bad subscription")
    except ValueError:
        pass
    print("webpush: checks pass")
