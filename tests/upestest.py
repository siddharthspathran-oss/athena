"""Checks the myUPES attendance reader (jarvis/tools/upes.py) against a
stand-in portal that behaves like the real one as seen in the browser:
the page loads the course list for its dropdown, then posts ONE course to
.../studentattendancesummary with a Bearer token and portal headers.

Needs Playwright and a Chromium (set CHROMIUM to its path if Playwright
cannot find one); the maths and routing checks run without them.

    python tests/upestest.py
"""
import ctypes
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not hasattr(ctypes, "WinDLL"):
    from unittest import mock

    ctypes.WinDLL = lambda *a, **k: mock.MagicMock()
    ctypes.windll = mock.MagicMock()
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    for name in ("win32gui", "win32con", "win32api", "win32process", "win32clipboard",
                 "pycaw", "pycaw.pycaw", "comtypes", "comtypes.client", "winsound",
                 "keyboard", "send2trash", "ddgs", "pythoncom", "winreg", "msvcrt"):
        try:
            __import__(name)
        except Exception:
            sys.modules[name] = mock.MagicMock()

import jarvis.tools  # noqa: E402,F401
from jarvis.agent import route  # noqa: E402
from jarvis.config import cfg  # noqa: E402
from jarvis.tools import upes  # noqa: E402

failures = 0


def check(name, ok, got=None):
    global failures
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"   got: {got!r}"))
    failures += not ok


def maths():
    check("can miss at 24/29", upes.can_miss(24, 29, 0.75) == 3, upes.can_miss(24, 29, 0.75))
    check("24/32 is exactly 75%", upes.can_miss(24, 32, 0.75) == 0)
    check("can miss none at 75.0", upes.can_miss(3, 4, 0.75) == 0)
    check("must attend from 20/30", upes.must_attend(20, 30, 0.75) == 10, upes.must_attend(20, 30, 0.75))
    check("30/40 needs nothing", upes.must_attend(30, 40, 0.75) == 0)
    check("no classes yet", upes.can_miss(0, 0, 0.75) == 0 and upes.must_attend(0, 0, 0.75) == 0)
    cfg._d.setdefault("upes", {})["min_attendance"] = 75
    check("75 means 0.75", upes.minimum() == 0.75)
    courses = [{"name": "Data Structure"}, {"name": "Digital Signal Processing"}, {"name": "Signals and Systems"}]
    check("match by word", [c["name"] for c in upes._match(courses, "data structures")] == ["Data Structure"],
          upes._match(courses, "data structures"))
    check("match by initials", [c["name"] for c in upes._match(courses, "dsp")] == ["Digital Signal Processing"])
    check("no match", upes._match(courses, "chemistry") == [])
    found = upes._find_courses([{"x": 1}, {"data": {"items": [
        {"CourseId": 80057, "CourseName": "Data Structure"}, {"CourseId": 80058, "CourseName": "DSP"}]}}],
        80057, "Data Structure")
    check("course list found by the known course", found == [{"ID": 80057, "Name": "Data Structure"},
                                                            {"ID": 80058, "Name": "DSP"}], found)


def routes():
    cases = {
        "what's my attendance": ("attendance", {}),
        "whats my attendance in data structures": ("attendance", {"subject": "data structures"}),
        "attendance for dsp": ("attendance", {"subject": "dsp"}),
        "check my latest attendance": ("attendance", {"refresh_now": True}),
        "how many classes can i bunk in signals and systems":
            ("attendance", {"subject": "signals and systems"}),
        "how many classes can I miss": ("attendance", {}),
        "mark my attendance in the register": None,
    }
    for text, want in cases.items():
        got = route(text)
        if want is None:
            check(f"route {text!r} not attendance", not got or got[0] != "attendance", got)
        else:
            check(f"route {text!r}", got == want, got)


# ------------------------------------------------------------------ stand-in portal

TOKEN = "Bearer test-token-1"
NUMBERS = {80057: (24, 29), 80058: (15, 22), 80059: (30, 30)}
NAMES = {80057: "Data Structure", 80058: "Digital Signal Processing", 80059: "Signals and Systems"}
PAGE_HTML = """<!doctype html><title>Attendance</title><select id=c></select><script>
fetch('/apigateway/course/list').then(r=>r.json()).then(j=>{
  const s=document.getElementById('c');
  j.data.forEach(c=>{const o=document.createElement('option');o.value=c.courseId;o.textContent=c.courseName;s.appendChild(o)});
  const first=j.data[0];
  fetch('/apigateway/student-attendance/studentattendancesummary',{method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer test-token-1',
             'X-Applicationname':'connectportal','X-Appsecret':'app-key'},
    body:JSON.stringify({StudentUniqueID:'abc',CourseFamilyId:172,TermCodeId:3,
      StartDate:'2026-08-03',EndDate:'2026-12-25',CourseList:[{ID:first.courseId,Name:first.courseName}]})});
});
</script>"""


# The real page asks nothing until a course is picked from its dropdown -
# a styled list, not a <select>.
PICK_HTML = """<!doctype html><title>Attendance</title>
<div class="p-dropdown" id=dd>Select Course</div><ul id=list style="display:none"></ul><script>
let courses=[];
fetch('/apigateway/course/list').then(r=>r.json()).then(j=>{courses=j.data;
  const ul=document.getElementById('list');
  courses.forEach(c=>{const li=document.createElement('li');li.className='p-dropdown-item';
    li.setAttribute('role','option');li.textContent=c.courseName;li.onclick=()=>ask(c);ul.appendChild(li)});
});
document.getElementById('dd').onclick=()=>{document.getElementById('list').style.display='block'};
function ask(c){fetch('/apigateway/student-attendance/studentattendancesummary',{method:'POST',
  headers:{'Content-Type':'application/json','Authorization':'Bearer test-token-1',
           'X-Applicationname':'connectportal','X-Appsecret':'app-key'},
  body:JSON.stringify({StudentUniqueID:'abc',CourseFamilyId:172,TermCodeId:3,
    StartDate:'2026-08-03',EndDate:'2026-12-25',CourseList:[{ID:c.courseId,Name:c.courseName}]})})}
</script>"""


class Portal(BaseHTTPRequestHandler):
    logged_in = True
    needs_pick = False
    asked: list = []

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/connectportal/"):
            if not Portal.logged_in:
                self.send_response(302)
                self.send_header("Location", "/idp/login")
                self.end_headers()
                return
            b = (PICK_HTML if Portal.needs_pick else PAGE_HTML).encode()
        elif self.path.startswith("/apigateway/course/list"):
            return self._json(200, {"data": [{"courseId": k, "courseName": v} for k, v in NAMES.items()]})
        else:
            b = b"<title>Login</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.headers.get("Authorization") != TOKEN or self.headers.get("X-Appsecret") != "app-key":
            return self._json(401, {"error": "unauthorised"})
        cid = body["CourseList"][0]["ID"]
        Portal.asked.append((cid, body.get("TermCodeId")))
        a, t = NUMBERS[cid]
        self._json(200, {"AttendanceInfo": [{"CourseId": cid, "CourseName": NAMES[cid]}],
                         "AttendanceSummary": {"TotalAttendance": a, "TotalSessionCount": t,
                                               "TotalCondonedAttendanceCount": 0,
                                               "TotalPresentPercentage": round(100 * a / t, 2)}})


def live():
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("skip live portal checks: Playwright is not installed")
        return
    exe = os.environ.get("CHROMIUM") or next(
        (str(p) for p in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")), None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    tmp = Path(tempfile.mkdtemp())
    saved = upes.PAGE, upes.SAVED, upes.PROFILE, upes._open
    upes.PAGE = base + "/connectportal/user/student/student-attendance"
    upes.SAVED, upes.PROFILE = tmp / "att.json", tmp / "profile"

    def _open(pw, headless):
        kw = dict(user_data_dir=str(upes.PROFILE), headless=True)
        if exe:
            kw["executable_path"] = exe
        return pw.chromium.launch_persistent_context(**kw)
    upes._open = _open
    try:
        courses = upes.refresh()
        got = {c["name"]: (c["attended"], c["total"]) for c in courses}
        check("every course read", got == {NAMES[k]: v for k, v in NUMBERS.items()}, got)
        check("each course asked once", sorted(c for c, _ in Portal.asked) == [80057, 80058, 80059], Portal.asked)
        check("term kept from the page's request", all(t == 3 for _, t in Portal.asked), Portal.asked)
        check("saved", json.loads(upes.SAVED.read_text())["courses"][0]["name"] == "Data Structure")
        check("no token saved", "test-token" not in upes.SAVED.read_text())

        said = upes.attendance("data structures")
        check("answer for one subject", said.startswith("Data Structure: 82.8% (24 of 29) - you can miss 3 more"), said)
        said = upes.attendance()
        check("low subject told to attend", "attend the next" in said and "Digital Signal Processing: 68.2%" in said, said)
        check("full subject", "Signals and Systems: 100.0% (30 of 30) - you can miss 10 more" in said, said)

        Portal.needs_pick, Portal.asked = True, []
        courses = upes.refresh()
        check("course picked from a styled dropdown", len(courses) == 3 and len(Portal.asked) == 3,
              (courses, Portal.asked))
        Portal.needs_pick = False

        Portal.logged_in = False
        try:
            upes.fetch(wait=6)
            check("logged out noticed", False)
        except upes.NotLoggedIn:
            check("logged out noticed", True)
        said = upes.attendance(refresh_now=True)
        check("logged out: saved numbers still answered", "Data Structure" in said and "log in again" in said, said)
    finally:
        upes.PAGE, upes.SAVED, upes.PROFILE, upes._open = saved
        srv.shutdown()


if __name__ == "__main__":
    maths()
    routes()
    live()
    print(f"\n{'all passed' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)
