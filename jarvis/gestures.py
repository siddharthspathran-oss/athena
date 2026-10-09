"""Hand gestures through the webcam (gesture mode), after an "Ultron" demo (6 Oct 2026).

    point                    the mouse pointer follows your fingertip
    pinch while pointing     click there
    open palm, held 1.5 s    play / pause (once per palm)
    peace sign, held         Athena listens - same as the HUD's mic button
    both hands               apart / together: bigger / smaller core

Off unless switched on ("gesture mode on"): the camera then stays open, and
the presence check (presence.py) simply finds it busy and goes by idle time.
Frames are looked at and dropped - never saved, never sent. MediaPipe's hand
landmarker (data/hand_landmarker.task) runs on the CPU.
"""
from __future__ import annotations

import threading
import time

from .config import DATA, cfg
from .registry import tool

MODEL = DATA / "hand_landmarker.task"
_state = {"on": False, "thread": None}


def _fingers(lm) -> list[bool]:
    """Which of thumb, index, middle, ring, little are stretched out."""
    up = [abs(lm[4].x - lm[2].x) > abs(lm[3].x - lm[2].x) * 1.3 and abs(lm[4].x - lm[17].x) > 0.12]
    up += [lm[t].y < lm[t - 2].y for t in (8, 12, 16, 20)]
    return up


_shown = {"g": None}


def _show(g: str) -> None:
    """What the hand is doing, for the HUD's gesture chip - only when it changes."""
    if g != _shown["g"]:
        _shown["g"] = g
        from . import live
        live.set_value("gesture", g)


def _loop() -> None:
    """Your hand as the controller (redesigned 6 Oct 2026 - the first version
    fired play/pause every 1.5 s while a palm was up, and clicked wherever the
    mouse happened to be):

      point (index up)        the mouse pointer follows your fingertip
      pinch while pointing    click there, once per pinch
      open palm, held 1.5 s   play / pause, once - lower the hand to re-arm
      peace sign, held        Athena listens (with her tone)
      both hands              apart = bigger core, together = smaller
    """
    import cv2
    import mediapipe as mp
    import win32api
    from mediapipe.tasks.python import BaseOptions, vision

    from . import live, winapi
    det = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL)), num_hands=2,
        running_mode=vision.RunningMode.VIDEO))
    cap = cv2.VideoCapture(int(cfg.get("presence.camera_index", 0)), cv2.CAP_DSHOW)
    sw, sh = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
    cur = None                       # smoothed pointer position
    armed = {"palm": True, "peace": True}
    held = {"g": "", "t": 0.0}
    pinched, two = False, None       # two: (start distance, start scale)
    scale = float(live._state.get("core_scale") or 1.0)
    from .store import load_json
    checked = 0.0
    try:
        while _state["on"]:
            # "Off" said anywhere - voice, typed chat, the phone - lands in
            # gestures.json; the loop watches it, so the camera goes off even
            # when another part of Athena heard it (6 Oct 2026).
            if time.time() - checked > 1.0:
                checked = time.time()
                if not load_json(STATE, {}).get("on", False):
                    _state["on"] = False
                    break
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.2)
                continue
            now = time.time()
            res = det.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), int(now * 1000))
            # Every hand counts. A "raised hands only" rule (wrist above 0.78 of
            # the frame) threw away every gesture: the laptop camera looks down,
            # and measured wrists sat at 0.78-1.03 while gesturing - while hands
            # resting on the keyboard are not in view at all (guided test, 6 Oct 2026).
            hands = list(res.hand_landmarks or [])
            open_ = [h for h in hands if all(_fingers(h)[1:])]
            if len(open_) >= 2:                                 # both palms open: size the core
                hands = open_
                a, b = hands[0][8], hands[1][8]
                d = ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5
                if two is None:
                    two = (max(d, 0.05), scale)
                scale = max(0.4, min(4.0, two[1] * d / two[0]))
                live.set_value("core_scale", round(scale, 2))
                _show("resize")
                held["two"] = now              # palms that are part of a resize are not play/pause
                continue
            if two is not None:
                print(f"[gesture] core size {scale:.1f}x")
                two = None
                held["resized"] = now          # lowering one hand is not a palm gesture
            if not hands:
                held["g"], pinched, cur = "", False, None
                armed.update(palm=True, peace=True)
                _show("")
                continue
            lm = hands[0]
            f = _fingers(lm)
            size = max(1e-3, ((lm[0].x - lm[9].x) ** 2 + (lm[0].y - lm[9].y) ** 2) ** 0.5)
            gesture = ("palm" if all(f[1:]) else "peace" if f[1] and f[2] and not f[3] and not f[4]
                       else "point" if f[1] and not any(f[2:]) else "")
            pinch = ((lm[4].x - lm[8].x) ** 2 + (lm[4].y - lm[8].y) ** 2) ** 0.5 / size
            if gesture != held["g"]:
                held.update(g=gesture, t=now)
                if gesture != "palm":
                    armed["palm"] = True
                if gesture != "peace":
                    armed["peace"] = True
            if gesture == "point":
                held["point"] = now
            # The index finger curls as it meets the thumb, so a pinch no longer
            # looks like pointing; a pinch counts within a second of pointing.
            held["pin"] = held.get("pin", 0) + 1 if pinch < 0.25 else 0          # frames pinched
            held["open"] = held.get("open", 0) + 1 if pinch > 0.6 else 0         # frames open
            # Measured pinches (6 Oct 2026) are short dips - 1-4 frames, down to
            # 0.00-0.20 - so the first frame under 0.25 clicks; 3 frames clearly
            # open re-arm it, so one pinch can't flicker into two clicks.
            pinching = (held["pin"] >= 1 and now - held.get("point", 0) < 1.0 and gesture != "palm"
                        and now - held.get("clicked", 0) > 1.2)
            if gesture == "point" or pinching or (pinched and pinch < 0.4):
                # the camera image is mirrored; the middle of the frame covers the whole screen
                tx = min(1, max(0, (1 - lm[8].x - 0.2) / 0.6)) * sw
                ty = min(1, max(0, (lm[8].y - 0.15) / 0.55)) * sh
                if not pinched:                                 # hold still while pinching
                    cur = (tx, ty) if cur is None else (cur[0] * 0.6 + tx * 0.4, cur[1] * 0.6 + ty * 0.4)
                    win32api.SetCursorPos((int(cur[0]), int(cur[1])))
                _show("click" if pinched else "point")
                if pinching and not pinched:
                    pinched = True
                    held["clicked"] = now
                    print("[gesture] pinch - click")
                    winapi.click(*win32api.GetCursorPos())
                elif held["open"] >= 3:                        # opened properly: ready for the next
                    pinched = False
            elif gesture in ("palm", "peace"):
                _show(gesture)
            else:
                _show("")
            if len(hands) >= 2:
                held["two"] = now
            if (gesture == "palm" and armed["palm"] and now - held["t"] > 1.5 and len(hands) == 1
                    and now - held.get("two", 0) > 3.0):
                armed["palm"] = False
                print("[gesture] open palm - play/pause")
                winapi.media_key("playpause")
            elif gesture == "peace" and armed["peace"] and now - held["t"] > 0.6:
                armed["peace"] = False
                print("[gesture] peace sign - listening")
                live.request_wake()
                try:
                    from .ui.voice import earcon
                    earcon()
                except Exception:
                    pass
    finally:
        cap.release()
        det.close()
        _show("")


STATE = DATA / "gestures.json"


def _camera_problem() -> str | None:
    """Why gestures would see nothing, or None if the camera gives a picture."""
    try:
        import cv2
        cap = cv2.VideoCapture(int(cfg.get("presence.camera_index", 0)), cv2.CAP_DSHOW)
        frames = [f for ok, f in (cap.read() for _ in range(4)) if ok]
        cap.release()
    except Exception as e:
        return f"I can't use the camera ({e})."
    if not frames:
        return "I can't open the camera - another app may be using it."
    if max(float(f.mean()) for f in frames) < 3:
        # Lenovo's shutter or camera-off key: a black picture (6 Oct 2026)
        return "Your camera is only showing black - the privacy shutter is closed or the camera-off key is on."
    return None


def _running() -> bool:
    t = _state.get("thread")
    return bool(t and t.is_alive())


def start() -> None:
    """In the voice process: follow data/gestures.json, whoever changes it.

    One switch for everything. "Gesture mode off" typed in the chat or said on
    the phone runs in another process; it used to switch off only there while
    the camera loop here ran on (6 Oct 2026). Now any of them just sets the
    file, and this watcher starts or stops the camera within a second.
    """
    from . import live
    from .store import load_json, save_json

    def watch():
        while True:
            try:
                want = bool(load_json(STATE, {}).get("on", False))
                if want and not _running():
                    problem = _camera_problem() if MODEL.exists() else "The hand model is missing."
                    if problem:
                        save_json(STATE, {"on": False, "problem": problem})
                        live.note(f"gestures: {problem}")
                    else:
                        _state["on"] = True
                        _state["thread"] = threading.Thread(target=_loop, daemon=True, name="athena-gestures")
                        _state["thread"].start()
                elif not want and _running():
                    _state["on"] = False
                live.set_value("gestures_on", _running() or want)
            except Exception:
                pass
            time.sleep(1)
    threading.Thread(target=watch, daemon=True, name="athena-gestures-switch").start()


@tool(tags=["apps"])
def gesture_mode(action: str = "on") -> str:
    """Hand-gesture control through the webcam: switch it on or off, or say whether it is on.

    action: on, off, or status
    """
    from . import live
    from .store import load_json, save_json
    a = (action or "on").strip().lower()
    on_now = bool(live.read_voice().get("gestures_on")) and bool(load_json(STATE, {}).get("on"))
    if a.startswith("stat") or a in ("?", "is it on"):
        # the truth from the running Athena - never a guess
        return "Gesture mode is on." if on_now else "Gesture mode is off."
    if a in ("off", "false", "stop", "no", "0"):
        save_json(STATE, {"on": False})
        return "Gesture mode is off - the camera is released."
    if on_now:
        return "Gesture mode is already on."
    problem = _camera_problem()
    if problem:
        return problem + " Open it and ask again."
    save_json(STATE, {"on": True})
    return ("Gesture mode is on: point to move the mouse, pinch to click, hold an open palm to play or pause, "
            "peace sign to talk to me, and both hands apart or together to resize the core.")
