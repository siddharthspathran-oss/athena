"""Web access: search, fetch and read pages, download files, weather."""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from ..config import expand
from ..registry import tool

TAG = ["web"]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


@tool(tags=TAG)
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return titles, snippets and links.

    query: what to search for
    max_results: how many results to return
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # older package name
        except ImportError:
            return "ERROR: search backend missing. Run: pip install ddgs"

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        return f"ERROR: search failed: {e}"

    if not results:
        return f"No results for {query}."

    out = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        body = (r.get("body", "") or "")[:300]
        href = r.get("href", "")
        out.append(f"{i}. {title}\n   {body}\n   {href}")
    return "\n\n".join(out)


@tool(tags=TAG)
def read_webpage(url: str, max_chars: int = 4000) -> str:
    """Fetch a web page and return its readable text content.

    url: the page address
    max_chars: stop after this many characters
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return f"ERROR fetching {url}: {e}"

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(r.text, "html.parser")
        for junk in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            junk.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s{2,}", " ", text)

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
    if m:
        title = m.group(1).strip() + "\n\n"

    body = title + text
    if len(body) > max_chars:
        body = body[:max_chars] + f"\n... [truncated, {len(body) - max_chars} more characters]"
    return body


@tool(tags=TAG)
def download_file(url: str, save_to: str = "~/Downloads") -> str:
    """Download a file from a URL into a folder.

    url: direct link to the file
    save_to: destination folder
    """
    from ..config import is_path_allowed, path_refusal

    if not is_path_allowed(save_to, write=True):
        # A download writes a file, so it obeys protected_paths too.
        return "ERROR: " + (path_refusal(save_to, write=True) or f"{save_to} is not allowed.")
    folder = expand(save_to)
    folder.mkdir(parents=True, exist_ok=True)

    name = Path(httpx.URL(url).path).name or "download.bin"
    dest = folder / name
    n = 1
    while dest.exists():
        dest = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1

    try:
        with httpx.stream("GET", url, headers={"User-Agent": UA},
                          follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except Exception as e:
        return f"ERROR downloading {url}: {e}"
    return f"Downloaded to {dest} ({dest.stat().st_size / 1e6:.1f} MB)."


@tool(tags=TAG)
def get_weather(location: str) -> str:
    """Get the current weather and short forecast for a place.

    location: city or place name
    """
    try:
        r = httpx.get(f"https://wttr.in/{location}?format=j1", timeout=20,
                      headers={"User-Agent": "curl/8"})
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        return f"ERROR: weather lookup failed: {e}"

    cur = d["current_condition"][0]
    today = d["weather"][0]
    desc = cur["weatherDesc"][0]["value"]
    return (
        f"{location}: {desc}, {cur['temp_C']}C (feels like {cur['FeelsLikeC']}C), "
        f"humidity {cur['humidity']}%, wind {cur['windspeedKmph']} km/h.\n"
        f"Today: {today['mintempC']}C to {today['maxtempC']}C."
    )


@tool(tags=TAG)
def open_url(url: str) -> str:
    """Open a URL in the default web browser.

    url: the address to open
    """
    import webbrowser

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url} in your browser."
