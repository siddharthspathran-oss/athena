"""Inbox autopilot: what came in, and replies drafted in your style - never sent.

Every morning (inbox.at) and on request, unread mail addressed to you (not
promotions or social) is summarised, and for the messages that need an answer
a reply is drafted in your writing style (profile "Writing style"). Drafts go
into Gmail's Drafts folder once you have granted "create drafts" with
connect_gmail_drafts - a separate token from the read-only one, and a scope
that cannot send. Until then they are kept in data/reply_drafts.json.

Nothing here sends mail. You open the draft, read it, and press send.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import threading
import time
from email.mime.text import MIMEText

from .config import DATA, cfg
from .registry import tool
from .store import load_json, save_json

DRAFTS = DATA / "reply_drafts.json"
COMPOSE_TOKEN = DATA / "google_token_compose.json"
COMPOSE_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
_NOREPLY = re.compile(r"no-?reply|do-?not-?reply|notification|alerts?@|mailer|newsletter|updates@", re.I)


def _read_service():
    from googleapiclient.discovery import build
    from .tools.brief import _credentials
    creds = _credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False) if creds else None


def _compose_service():
    if not COMPOSE_TOKEN.exists():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(COMPOSE_TOKEN), COMPOSE_SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        COMPOSE_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _text(payload: dict) -> str:
    """The plain-text body of a message, however it is nested."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        t = _text(part)
        if t:
            return t
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def unread(limit: int = 8) -> list[dict]:
    svc = _read_service()
    if svc is None:
        return []
    ids = svc.users().messages().list(userId="me", maxResults=limit, q=cfg.get(
        "inbox.query", "is:unread to:me -category:promotions -category:social newer_than:3d")).execute().get("messages", [])
    out = []
    for m in ids:
        msg = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        h = {x["name"].lower(): x["value"] for x in msg["payload"].get("headers", [])}
        body = re.sub(r"\s+", " ", _text(msg["payload"]))
        body = re.split(r"\bOn .{5,80} wrote:", body)[0][:2000]          # not the quoted thread
        out.append({"id": m["id"], "thread": msg.get("threadId"), "from": h.get("from", ""),
                    "subject": h.get("subject", "(no subject)"), "msgid": h.get("message-id", ""),
                    "body": body.strip(), "snippet": msg.get("snippet", "")})
    return out


def _who(sender: str) -> str:
    return sender.split("<")[0].strip().strip('"') or sender


def _ask(prompt: str) -> str:
    from . import cloud
    from .llm import LLM
    for model in ("gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b"):
        try:
            if cloud.get_key(model.split(":")[0]):
                return cloud.chat(model, [{"role": "user", "content": prompt}], temperature=0.3, max_tokens=900)
        except Exception:
            continue
    return (LLM().chat([{"role": "user", "content": prompt}], temperature=0.3).content or "")


def _style() -> str:
    try:
        from . import profile
        return " ".join(i.text for i in profile.load() if i.section == "style")
    except Exception:
        return ""


def triage(mails: list[dict]) -> list[dict]:
    """Each mail with a one-line summary and whether it needs a reply."""
    if not mails:
        return []
    listing = "\n\n".join(f"[{i}] From: {m['from']}\nSubject: {m['subject']}\n{m['body'][:900]}" for i, m in enumerate(mails))
    raw = _ask("For each email below, give a one-line summary of what the sender wants, and whether the "
               "recipient (a university student) personally needs to reply. Reply with JSON only: "
               '[{"i": 0, "summary": "...", "reply": true}]\n\n' + listing)
    try:
        items = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
    except Exception:
        items = [{"i": i, "summary": m["subject"], "reply": False} for i, m in enumerate(mails)]
    for it in items:
        if 0 <= int(it.get("i", -1)) < len(mails):
            m = mails[int(it["i"])]
            m["summary"] = it.get("summary", m["subject"])
            m["needs_reply"] = bool(it.get("reply")) and not _NOREPLY.search(m["from"])
    return mails


def draft(m: dict) -> str:
    style = _style()
    return _ask(
        "Draft a reply to this email for the recipient to review and send himself. Write only the reply body - "
        "no subject line, no placeholders in brackets unless a fact is genuinely unknown, then use [CHECK: ...]. "
        "Keep it short and natural." + (f" Match the recipient's writing style: {style}" if style else "")
        + f"\n\nFrom: {m['from']}\nSubject: {m['subject']}\n\n{m['body']}").strip()


def _save_draft(m: dict, text: str) -> str:
    svc = _compose_service()
    if svc is None:
        drafts = load_json(DRAFTS, [])
        drafts = [d for d in drafts if d["id"] != m["id"]] + [{"id": m["id"], "to": m["from"], "subject": m["subject"],
                                                               "body": text, "t": time.time()}]
        save_json(DRAFTS, drafts[-30:])
        return "local"
    to = re.search(r"<([^>]+)>", m["from"])
    msg = MIMEText(text)
    msg["To"] = to.group(1) if to else m["from"]
    msg["Subject"] = m["subject"] if m["subject"].lower().startswith("re:") else "Re: " + m["subject"]
    if m.get("msgid"):
        msg["In-Reply-To"] = msg["References"] = m["msgid"]
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().drafts().create(userId="me", body={"message": {"raw": raw, "threadId": m["thread"]}}).execute()
    return "gmail"


def run(drafts_too: bool = True) -> str:
    mails = triage(unread())
    if not mails:
        return "No unread mail that needs you."
    done = load_json(DATA / "inbox_seen.json", [])
    lines, made, where = [], 0, ""
    for m in mails:
        lines.append(f"{_who(m['from'])}: {m.get('summary', m['subject'])}")
        if drafts_too and m.get("needs_reply") and m["id"] not in done:
            try:
                where = _save_draft(m, draft(m))
                made += 1
                done.append(m["id"])
            except Exception as e:
                lines.append(f"  (couldn't draft: {e})")
    save_json(DATA / "inbox_seen.json", done[-300:])
    out = f"{len(mails)} unread for you. " + " | ".join(lines)
    if made:
        out += (f" I drafted {made} repl{'y' if made == 1 else 'ies'} - they're in your Gmail drafts; read and send them yourself."
                if where == "gmail" else
                f" I drafted {made} repl{'y' if made == 1 else 'ies'} (kept on this PC - say 'connect gmail drafts' to have them put in Gmail).")
    try:
        from . import rules
        rules.log(f"inbox: {len(mails)} unread, {made} replies drafted", "inbox")
    except Exception:
        pass
    return out


@tool(tags=["email"], timeout=240)
def inbox_summary(draft_replies: bool = True) -> str:
    """Summarise unread email addressed to the user and draft replies to the
    ones that need one, in the user's style. Drafts are never sent.

    draft_replies: also draft replies
    """
    try:
        return run(draft_replies)
    except Exception as e:
        return f"ERROR: could not read mail ({e})."


@tool(tags=["email"], timeout=300)
def connect_gmail_drafts() -> str:
    """Let Athena put drafted replies into Gmail's Drafts folder (asks Google
    for the "create drafts" permission once, in the browser). It cannot send."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    from .tools.brief import CREDENTIALS_FILE
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), COMPOSE_SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    COMPOSE_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    moved = 0
    for d in load_json(DRAFTS, []):
        try:
            _save_draft({"id": d["id"], "from": d["to"], "subject": d["subject"], "thread": None, "msgid": ""}, d["body"])
            moved += 1
        except Exception:
            pass
    save_json(DRAFTS, [])
    return "Gmail drafts connected." + (f" Moved {moved} waiting draft(s) into Gmail." if moved else "")


def start() -> None:
    def loop():
        done = ""
        while True:
            now = dt.datetime.now()
            # Until 10 pm, not noon: a PC first switched on in the afternoon still gets it.
            if now.strftime("%H:%M") >= cfg.get("inbox.at", "08:00") and done != now.date().isoformat() and now.hour < 22:
                done = now.date().isoformat()
                try:
                    from . import rules
                    if not rules.paused():
                        run(True)
                except Exception:
                    pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="athena-inbox").start()
