"""Checks the RAGAZZA store router: clear store requests reach the right tool
without the model, and ordinary commands are left alone.

    python tests/storeroutetest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.agent import route  # noqa: E402

HITS = {
    "how is my store doing": ("store_summary", {}),
    "Athena, how's the store doing today?": ("store_summary", {}),
    "store summary": ("store_summary", {}),
    "How is my RAGAZZA store doing today?": ("store_summary", {}),
    "ragazza store summary": ("store_summary", {}),
    "any ragazza store orders to ship": ("store_orders", {"status": "to-ship"}),
    "ragazza sales today": ("store_summary", {}),
    "low stock": ("store_summary", {}),
    "any orders to ship": ("store_orders", {"status": "to-ship"}),
    "orders waiting to ship": ("store_orders", {"status": "to-ship"}),
    "what do I need to ship today": ("store_orders", {"status": "to-ship"}),
    "list my orders": ("store_orders", {"status": "all"}),
    "show all ragazza orders": ("store_orders", {"status": "all"}),
    "show order RGZ1004": ("store_order", {"order": "1004"}),
    "where is order #1004": ("store_order", {"order": "1004"}),
    "status of order rgz 1004": ("store_order", {"order": "1004"}),
    "how many Noor Anarkali Set are in stock": ("find_store_product", {"query": "Noor Anarkali Set"}),
    "stock of jaipur bloom bedsheet": ("find_store_product", {"query": "jaipur bloom bedsheet"}),
    "ship RGZ1004 with Delhivery DLV123": ("ship_store_order", {"order": "1004", "courier": "Delhivery", "tracking_number": "DLV123"}),
    "ship order 1004 via Blue Dart tracking number bd-99812": ("ship_store_order", {"order": "1004", "courier": "Blue Dart", "tracking_number": "BD-99812"}),
    "mark 1004 as delivered": ("update_store_order", {"order": "1004", "change": "delivered"}),
    "order RGZ1004 out for delivery": ("update_store_order", {"order": "1004", "change": "out for delivery"}),
    "set the stock of Noor Anarkali Set to 10": ("set_store_stock", {"product": "Noor Anarkali Set", "stock": 10}),
    "Set the stock of Noor Anarkali Set to 50 on the RAGAZZA store": ("set_store_stock", {"product": "Noor Anarkali Set", "stock": 50}),
}

# Must not be taken by the store router (whatever else handles them is fine)
MISSES = [
    "set volume to 50", "open chrome", "how many tabs are open", "show my files", "list my tasks",
    "what's the weather", "remind me to ship the parcel at 5", "how many days until friday",
    "show me the order of operations", "set a timer for 10 minutes", "check my email",
]

bad = 0
for text, want in HITS.items():
    got = route(text)
    if got != want:
        bad += 1
        print(f"FAIL  {text!r}\n      want {want}\n      got  {got}")
for text in MISSES:
    got = route(text)
    if got and got[0] in {"store_summary", "store_orders", "store_order", "find_store_product",
                         "ship_store_order", "update_store_order", "set_store_stock"}:
        bad += 1
        print(f"FAIL  {text!r} was taken by the store router: {got}")

print(f"{len(HITS) + len(MISSES) - bad}/{len(HITS) + len(MISSES)} passed")
sys.exit(1 if bad else 0)
