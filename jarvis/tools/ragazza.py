"""RAGAZZA store: orders, revenue and stock, through the shop's private Athena API.

The website decides what is allowed - every order change goes through the same
rules as its admin panel, and refunds are refused outright - so a confused
model can at worst be told no. Anything that changes the shop is destructive
and asks first.

Key: saved once with `python run.py store-key`, which asks for it (data/keys.json,
same file as the cloud model keys). Address: `store.url` in config.yaml.
"""
from __future__ import annotations

import functools

import httpx

from ..cloud import get_key
from ..config import cfg
from ..registry import tool

TAG = ["store"]


class StoreError(Exception):
    pass


def _call(method: str, path: str, body: dict | None = None) -> dict:
    key = get_key("ragazza")
    if not key:
        raise StoreError("No RAGAZZA key saved. Run: python run.py store-key")
    url = cfg.get("store.url", "http://localhost:3000").rstrip("/") + "/api/athena" + path
    try:
        r = httpx.request(method, url, json=body, timeout=20,
                          headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError:
        raise StoreError(f"Can't reach the store at {url.split('/api')[0]}. Is the website running?")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code == 401:
        raise StoreError("The store rejected Athena's key. Save the right one with: python run.py store-key")
    if r.status_code >= 400:
        raise StoreError(data.get("error") or f"Store error {r.status_code}")
    return data


def rupees(n: int | float) -> str:
    """3598 -> ₹3,598 ; 125000 -> ₹1,25,000 (Indian grouping)."""
    s = str(int(round(n)))
    head, tail = s[:-3], s[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    return "₹" + (head + "," + tail if head else tail)


STATUS = {"CONFIRMED": "to ship", "SHIPPED": "shipped", "OUT_FOR_DELIVERY": "out for delivery",
          "DELIVERED": "delivered", "PENDING": "awaiting payment", "CANCELLED": "cancelled"}


def overview() -> dict:
    """Raw figures for the Command Center's RAGAZZA page."""
    return _call("GET", "/overview")


def _product(query: str) -> dict:
    """One product from a name or part of one; asks back when it is ambiguous."""
    q = (query or "").strip()
    if not q:
        raise StoreError("Which product?")
    found = _call("GET", "/products?" + str(httpx.QueryParams({"q": q}))).get("products", [])
    exact = [p for p in found if p["name"].lower() == q.lower() or p["slug"] == q.lower()]
    if len(exact) == 1 or len(found) == 1:
        return (exact or found)[0]
    if not found:
        raise StoreError(f"No product matches '{q}'.")
    raise StoreError("Which one? " + "; ".join(p["name"] for p in found[:8]))


def _safe(fn):
    """Turn StoreError into the 'ERROR: ...' strings the other tools return."""
    @functools.wraps(fn)  # keeps the signature and docstring the registry builds the schema from
    def wrap(*a, **kw):
        try:
            return fn(*a, **kw)
        except StoreError as e:
            return f"ERROR: {e}"
    return wrap


@tool(tags=TAG)
@_safe
def store_summary() -> str:
    """RAGAZZA shop at a glance: today's and this month's sales, orders waiting to ship, cash on delivery still to collect, low stock."""
    o = overview()
    lines = [
        f"Today: {o['today']['orders']} orders, {rupees(o['today']['revenue'])}.",
        f"Last 7 days: {o['week']['orders']} orders, {rupees(o['week']['revenue'])}. "
        f"Last 30 days: {o['month']['orders']} orders, {rupees(o['month']['revenue'])} "
        f"({rupees(o['collected30d'])} actually received).",
    ]
    ship = o["toShip"]
    lines.append(f"To ship: {len(ship)}" + (" - " + ", ".join(f"{s['order']} ({s['shipName']}, {s['shipCity']})" for s in ship[:5]) if ship else "."))
    lines.append(f"In transit: {o['inTransit']}.")
    if o["codToCollect"]["orders"]:
        n = o["codToCollect"]["orders"]
        lines.append(f"Cash on delivery to collect: {rupees(o['codToCollect']['amount'])} over {n} order{'s' if n != 1 else ''}.")
    if o["refundsDue"]:
        lines.append(f"Refunds still to issue in Razorpay: {o['refundsDue']}.")
    for r in o["needsReview"]:
        lines.append(f"Needs attention: {r['order']} - {r['reason']}")
    low = o["lowStock"]
    lines.append("Low stock: " + (", ".join(f"{x['product']} ({x['size']}): {x['stock']}" for x in low[:8]) if low else "none."))
    lines.append(f"New customers this week: {o['newCustomers7d']}.")
    return "\n".join(lines)


@tool(tags=TAG)
@_safe
def store_orders(status: str = "to-ship", search: str = "") -> str:
    """List RAGAZZA orders.

    status: to-ship, shipped, out-for-delivery, delivered, pending, cancelled, or all
    search: optional order number, customer name or email
    """
    st = (status or "").strip().lower().replace(" ", "-")
    params = {} if st in ("", "all") else {"status": st}
    if search.strip():
        params["q"] = search.strip()
    orders = _call("GET", "/orders" + ("?" + str(httpx.QueryParams(params)) if params else "")).get("orders", [])
    if not orders:
        return "No orders match."
    return "\n".join(
        f"{o['order']} · {STATUS.get(o['status'], o['status'])} · {rupees(o['total'])} "
        f"{'COD' if o['paymentMethod'] == 'COD' else 'online'} ({o['paymentStatus'].lower()}) · "
        f"{o['shipName']}, {o['shipCity']} · {o['items']} item(s) · {o['createdAt'][:10]}"
        for o in orders)


@tool(tags=TAG)
@_safe
def store_order(order: str) -> str:
    """Full details of one RAGAZZA order: items, customer, address, payment, tracking and timeline.

    order: the order number, e.g. RGZ1004 or 1004
    """
    o = _call("GET", "/orders/" + order.strip().lstrip("#"))
    items = "; ".join(f"{i['quantity']} x {i['name']} ({i['size']}) at {rupees(i['price'])}" for i in o["items"])
    pay = o["payment"]
    out = [
        f"{o['order']} - {STATUS.get(o['status'], o['status'])}",
        f"Items: {items}",
        f"Total {rupees(o['totals']['total'])} (shipping {rupees(o['totals']['shipping'])}) · "
        f"{'Cash on delivery' if pay['method'] == 'COD' else 'Online'} · {pay['status'].lower()}",
        f"Customer: {o['customer']['name']}, {o['customer']['phone']}, {o['customer']['email']}",
        f"Address: {o['address']}",
    ]
    if o["tracking"]:
        t = o["tracking"]
        out.append(f"Tracking: {t['courier']} {t['number']}" + (f" - {t['url']}" if t["url"] else ""))
    if o["needsReview"]:
        out.append(f"NEEDS ATTENTION: {o['needsReview']}")
    step = {**STATUS, "CONFIRMED": "confirmed"}  # in a history, "to ship" reads oddly
    out.append("Timeline: " + " -> ".join(step.get(e["status"], e["status"]) + (f" ({e['note']})" if e["note"] else "") for e in o["timeline"]))
    return "\n".join(out)


@tool(tags=TAG, destructive=True)
@_safe
def ship_store_order(order: str, courier: str, tracking_number: str, tracking_link: str = "") -> str:
    """Mark a RAGAZZA order as shipped and email the customer their tracking details.

    order: the order number, e.g. RGZ1004
    courier: courier company, e.g. Delhivery, Blue Dart, DTDC, India Post
    tracking_number: the AWB / tracking number
    tracking_link: optional https:// tracking page
    """
    body = {"type": "ship", "courier": courier, "trackingNumber": tracking_number}
    if tracking_link.strip():
        body["trackingUrl"] = tracking_link.strip()
    r = _call("POST", "/orders/" + order.strip().lstrip("#"), body)
    return f"{r['order']} marked shipped with {courier} ({tracking_number}). The customer has been emailed."


@tool(tags=TAG, destructive=True)
@_safe
def update_store_order(order: str, change: str) -> str:
    """Move a RAGAZZA order along after shipping, or cancel it before it ships. The customer is emailed.

    order: the order number, e.g. RGZ1004
    change: "out for delivery", "delivered" or "cancel"
    """
    types = {"out for delivery": "outForDelivery", "out-for-delivery": "outForDelivery", "delivered": "deliver",
             "deliver": "deliver", "cancel": "cancel", "cancelled": "cancel"}
    t = types.get((change or "").strip().lower())
    if not t:
        return "ERROR: change must be 'out for delivery', 'delivered' or 'cancel'. Refunds are only done in the admin panel."
    r = _call("POST", "/orders/" + order.strip().lstrip("#"), {"type": t})
    done = {"outForDelivery": "is out for delivery", "deliver": "is delivered", "cancel": "is cancelled and its stock returned"}[t]
    extra = " If it was paid online, refund it in the Razorpay dashboard, then mark it refunded in the admin panel." if t == "cancel" else ""
    return f"{r['order']} {done}. The customer has been emailed.{extra}"


@tool(tags=TAG)
@_safe
def find_store_product(query: str) -> str:
    """Look up RAGAZZA products with their price, stock and whether they are live on the site.

    query: product name, part of it, or fabric
    """
    found = _call("GET", "/products?" + str(httpx.QueryParams({"q": query.strip()}))).get("products", [])
    if not found:
        return f"No product matches '{query}'."
    return "\n".join(
        f"{p['name']} ({p['category']}) · {rupees(p['price'])}" + (f" (MRP {rupees(p['mrp'])})" if p["mrp"] > p["price"] else "")
        + " · stock " + ", ".join(f"{v['size']}: {v['stock']}" for v in p["variants"])
        + ("" if p["isActive"] else " · HIDDEN")
        for p in found)


@tool(tags=TAG, destructive=True)
@_safe
def set_store_stock(product: str, stock: int, size: str = "") -> str:
    """Set how many pieces of a RAGAZZA product are in stock.

    product: product name
    stock: the new number in stock (0 or more)
    size: only for bedsheets (Double or King); leave empty for suits and sarees
    """
    p = _product(product)
    body = {"stock": int(stock)}
    if size.strip():
        body["size"] = size.strip()
    _call("PATCH", f"/products/{p['slug']}", body)
    return f"{p['name']}{' (' + size.strip() + ')' if size.strip() else ''} stock is now {int(stock)}."


@tool(tags=TAG, destructive=True)
@_safe
def set_store_price(product: str, price: int, mrp: int = 0) -> str:
    """Change a RAGAZZA product's selling price, and optionally its MRP (the struck-through price).

    product: product name
    price: new selling price in rupees
    mrp: new MRP in rupees, or 0 to keep the current one
    """
    p = _product(product)
    body = {"price": int(price)}
    if int(mrp) > 0:
        body["mrp"] = int(mrp)
    _call("PATCH", f"/products/{p['slug']}", body)
    return f"{p['name']} now sells at {rupees(price)}" + (f" (MRP {rupees(mrp)})." if int(mrp) > 0 else ".")


@tool(tags=TAG, destructive=True)
@_safe
def show_store_product(product: str, visible: bool) -> str:
    """Show or hide a RAGAZZA product on the website without deleting it.

    product: product name
    visible: true to show it, false to hide it
    """
    p = _product(product)
    _call("PATCH", f"/products/{p['slug']}", {"active": bool(visible)})
    return f"{p['name']} is now {'live on' if visible else 'hidden from'} the website."


# ---------------------------------------------------------------- routing
# Clear store requests skip the model. On 27 Sep 2026 qwen2.5:7b answered
# "how is my store doing" with list_tasks eight times, and "how many Noor in
# stock" by trying to *set* the stock. These phrasings are regular enough to
# match; anything vaguer still goes to the model (and changes still ask first).
import re  # noqa: E402

_NO = r"#?(?:rgz\s*)?(?P<no>\d{4,})"
_SHOP = r"(?:ragazza(?:\s+(?:store|shop))?|store|shop)"
_ROUTES: list[tuple[re.Pattern, object]] = [
    (re.compile(rf"^ship\s+(?:order\s+)?{_NO}\s+(?:with|via|by|through)\s+(?P<courier>.+?)\s+(?:tracking\s+(?:number\s+|no\.?\s+)?|awb\s+)?(?P<track>[a-z0-9-]{{4,}})$", re.I),
     lambda m: ("ship_store_order", {"order": m["no"], "courier": m["courier"].strip(), "tracking_number": m["track"].upper()})),
    (re.compile(rf"^(?:mark\s+)?(?:order\s+)?{_NO}\s+(?:as\s+)?(?P<c>delivered|out\s+for\s+delivery)$", re.I),
     lambda m: ("update_store_order", {"order": m["no"], "change": re.sub(r"\s+", " ", m["c"].lower())})),
    (re.compile(r"^set\s+(?:the\s+)?stock\s+(?:of|for)\s+(?P<p>.+?)\s+to\s+(?P<n>\d+)$", re.I),
     lambda m: ("set_store_stock", {"product": m["p"], "stock": int(m["n"])})),
    (re.compile(rf"^(?:show|open|details?\s+(?:of|for)|status\s+of|where\s+is|what'?s\s+(?:the\s+)?status\s+of|check)\s+(?:my\s+|the\s+)?(?:order\s+){_NO}$", re.I),
     lambda m: ("store_order", {"order": m["no"]})),
    (re.compile(rf"^(?:any\s+|which\s+|what\s+)?(?:{_SHOP}\s+)?orders?\s+(?:are\s+|do\s+i\s+have\s+)?(?:to|waiting\s+to|left\s+to|pending\s+to)\s+ship$|^what\s+(?:do\s+i\s+|should\s+i\s+)?(?:need\s+to\s+|have\s+to\s+)?ship(?:\s+today)?$", re.I),
     lambda m: ("store_orders", {"status": "to-ship"})),
    (re.compile(rf"^(?:list|show)\s+(?:me\s+)?(?:my\s+|all\s+|the\s+)*(?:{_SHOP}\s+)?orders$", re.I),
     lambda m: ("store_orders", {"status": "all"})),
    (re.compile(r"^(?:how\s+many\s+(?P<a>.+?)\s+(?:are\s+|do\s+(?:we|i)\s+have\s+)?(?:in\s+stock|left)|(?:what'?s\s+the\s+)?(?:stock|inventory)\s+(?:of|for)\s+(?P<b>.+?)|check\s+stock\s+(?:of|for)\s+(?P<c>.+?))$", re.I),
     lambda m: ("find_store_product", {"query": m["a"] or m["b"] or m["c"]})),
    (re.compile(rf"^(?:how'?s|how\s+is)\s+(?:my\s+|the\s+)?{_SHOP}(?:\s+doing)?(?:\s+today)?$|^(?:my\s+|the\s+)?{_SHOP}\s+(?:summary|update|status|overview|report)$|^(?:{_SHOP}\s+)?(?:sales|revenue|low\s+stock)(?:\s+(?:today|this\s+week|this\s+month))?$", re.I),
     lambda m: ("store_summary", {})),
]


def route(said: str) -> tuple[str, dict] | None:
    """Tool call for a clearly worded store request, or None."""
    s = re.sub(r"\s+", " ", said.strip().rstrip(".?!"))
    s = re.sub(r"\s+(?:on|in|at|for)\s+(?:the\s+|my\s+)?(?:ragazza|store|shop)(?:\s+(?:store|shop|website|site))?$", "", s, flags=re.I)
    s = re.sub(r"^(?:(?:athena|jarvis|hey|ok|okay|please|can\s+you|could\s+you)\W+)+", "", s, flags=re.I)
    for pattern, make in _ROUTES:
        m = pattern.match(s)
        if m:
            return make(m)
    return None
