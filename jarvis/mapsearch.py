"""Google Maps, read by Athena herself: "cafes near Rajpur Road" -> the places,
their ratings and addresses, and whether each has a website of its own.

Gemini's background agent can't browse (its browser tools exist only in an
interactive session), so missions that need real local places get them
here: the installed Chrome, headless, via Playwright (as upes.py does). Read
only - one search and each place's own page, nothing clicked or typed - and
a small number of places, the way a person would look.
"""
from __future__ import annotations

import re
import urllib.parse

from .config import cfg
from .registry import tool


def search(query: str, limit: int = 12, details: bool = True) -> list[dict]:
    from playwright.sync_api import sync_playwright
    limit = max(1, min(int(limit or 12), int(cfg.get("maps.max_places", 15))))
    rows: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            pg = browser.new_page(viewport={"width": 1280, "height": 900}, locale="en-IN")
            pg.goto("https://www.google.com/maps/search/" + urllib.parse.quote_plus(query) + "?hl=en",
                    wait_until="domcontentloaded", timeout=45000)
            try:
                pg.wait_for_selector('div[role="feed"], h1', timeout=20000)
            except Exception:
                return []
            feed = pg.query_selector('div[role="feed"]')
            for _ in range(3 if feed else 0):                 # a few scrolls: more than one screenful
                feed.evaluate("f => f.scrollBy(0, 2500)")
                pg.wait_for_timeout(1000)
            cards = pg.evaluate("""() => [...document.querySelectorAll('div[role="feed"] a.hfpxzc')].map(a => {
                const card = a.closest('div[jsaction]')?.parentElement || a.parentElement;
                return {name: a.getAttribute('aria-label') || '', url: a.href,
                        text: (card ? card.innerText : '').replace(/\\s+/g, ' ')};
            })""")
            for c in cards:
                if len(rows) >= limit:
                    break
                if not c["name"] or "Sponsored" in c["text"][:200]:
                    continue                                   # ads are not places that came up
                text = c["text"].replace(c["name"], " ").strip()
                rating = re.search(r"\b([1-5]\.\d)\b", text)
                kind = re.search(r"\b[1-5]\.\d\b\s*(?:\([\d,]+\))?\s*([A-Z][A-Za-z &'-]{2,40}?)\s*·", text)
                addr = re.search(r"·\s*(?:·\s*)?([^·]{4,90}?)\s*(?:Open|Closed|Closes|Opens|Temporarily|$)", text)
                rows.append({"name": c["name"].strip(), "rating": rating.group(1) if rating else "",
                             "kind": kind.group(1).strip() if kind else "",
                             "address": addr.group(1).strip(" ·") if addr else "", "maps": c["url"],
                             "website": "", "phone": ""})
            if details:
                for r in rows:
                    try:
                        pg.goto(r["maps"], wait_until="domcontentloaded", timeout=30000)
                        pg.wait_for_selector("h1", timeout=15000)
                        pg.wait_for_timeout(800)
                        info = pg.evaluate("""() => ({
                            site: document.querySelector('a[data-item-id="authority"]')?.href || '',
                            phone: (document.querySelector('button[data-item-id^="phone"]')?.getAttribute('aria-label') || '')
                                     .replace(/^Phone:\\s*/i, '')})""")
                        r["website"], r["phone"] = info["site"], info["phone"].strip()
                    except Exception:
                        r["website"] = "?"
        finally:
            browser.close()
    return rows


def to_markdown(query: str, rows: list[dict]) -> str:
    without = [r for r in rows if not r["website"]]
    lines = [f"# Google Maps: {query}", "",
             f"{len(rows)} places; {len(without)} with no website of their own.", "",
             "| Place | Rating | Type | Address | Website | Phone |", "|---|---|---|---|---|---|"]
    for r in rows:
        site = r["website"] if r["website"] not in ("", "?") else ("unknown" if r["website"] == "?" else "**none**")
        lines.append(f"| [{r['name']}]({r['maps']}) | {r['rating']} | {r['kind']} | {r['address']} | {site} | {r['phone']} |")
    return "\n".join(lines) + "\n"


@tool(tags=["places"], timeout=180)
def maps_search(query: str, limit: int = 10) -> str:
    """Look places up on Google Maps: ratings, addresses, and whether each has
    its own website. Use for "find cafes near Rajpur Road", "which restaurants
    near me have no website", "top rated gyms in Dehradun".

    query: what and where, e.g. "cafes near Rajpur Road Dehradun" (add the city if the user didn't say one)
    limit: how many places (up to 15)
    """
    if not re.search(r"\b(in|near|around|at)\b", query, re.I):
        query = f"{query} in {cfg.get('assistant.city', 'Dehradun')}"
    try:
        rows = search(query, limit)
    except Exception as e:
        return f"ERROR: couldn't read Google Maps ({str(e)[:120]})."
    if not rows:
        return f"Google Maps found nothing for '{query}'."
    without = [r["name"] for r in rows if not r["website"]]
    head = "; ".join(f"{r['name']} ({r['rating'] or '-'})" for r in rows[:8])
    return (f"{len(rows)} places for '{query}': {head}." +
            (f" No website of their own: {', '.join(without[:8])}." if without else " All have websites."))


if __name__ == "__main__":
    rows = [{"name": "A Cafe", "rating": "4.5", "kind": "Cafe", "address": "1 Rd", "maps": "u", "website": "", "phone": ""},
            {"name": "B Cafe", "rating": "4.1", "kind": "Cafe", "address": "2 Rd", "maps": "v", "website": "https://b.in",
             "phone": "1"}]
    md = to_markdown("cafes", rows)
    assert "1 with no website" in md and "**none**" in md and "https://b.in" in md
    print("mapsearch: checks pass")
