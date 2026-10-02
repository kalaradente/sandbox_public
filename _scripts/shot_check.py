"""Frame-by-frame cut finder for a range of a clip: returns times where consecutive frames stop matching
(correlation of mean-normalised thumbnails). Flashes keep their layout, so they pass; real cuts don't. Two tests:
  a hard cut: the match falls under 0.45 (a different picture);
  a jump cut between look-alike shots (the same person on the same set, a fan edit of one interview): the match dips
  under 0.8 for ONE frame while the three frames either side of it match well. Motion (a hand across the lens, a whip
  pan) and strobes drag the match down for several frames in a row, so they don't count.
Only the picture is compared: a letterboxed clip's bars are most of its frame and never change, so whole frames kept
matching across real cuts (found Sep 29: two shots of one piece ran a frame or two into the source's next shot, and
another piece held eight jump cuts inside seven shots)."""
import os
import cv2, numpy as np

_AREAS = {}


def area_of(path):
    """The picture inside a clip's letterbox bars (w, h, x, y), the render's own crop (Lab_Render.framing); None without bars
    or for a file the indexes don't know (a render)."""
    if path not in _AREAS:
        try:
            import Lab_Render   # here, not at the top: Lab_Render imports this file too
            if "idx" not in _AREAS: _AREAS["idx"] = Lab_Render.load_index()
            c = _AREAS["idx"].get(os.path.splitext(os.path.basename(path))[0])   # keyed by file name, like the indexes
            _AREAS[path] = Lab_Render.framing(c)["crop"] if c else None
        except Exception:
            _AREAS[path] = None
    return _AREAS[path]


def picture(fr, area):
    if not area: return fr
    w, h, x, y = area
    return fr[y:y + h, x:x + w] if x + w <= fr.shape[1] and y + h <= fr.shape[0] else fr


HARD, DIP, AROUND, DEPTH, LOOK = 0.45, 0.8, 0.85, 0.12, 3   # see the two tests above; LOOK = frames either side


def frame_at(path, t, cap=None):
    """The frame showing at t. OpenCV's own seek lands on a nearby keyframe (measured Sep 28: often the wrong frame, up to a
    second off), so seek a second early and read forward. Pass an open cap to reuse it."""
    own = cap is None; cap = cap or cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_MSEC, max(0, t - 1.0) * 1000); last = None
    while True:
        ok, fr = cap.read()
        if not ok: break
        last = fr
        if cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 >= t: break
    if own: cap.release()
    return last


def cuts_in(path, a, b):
    return scan(path, a, b)[0]


def scan(path, a, b, at=()):
    """One decode of a..b: the cuts, and the frame at each time in `at` (inside the range), so a check needs no extra seeks.
    Cuts are judged on the picture only (area_of); the frames in `at` come back whole."""
    cap = cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_MSEC, max(0, a - 0.6) * 1000); prev = None; m = []
    want = sorted(at); got = {}; area = area_of(path); past = 0
    while True:
        ok, fr = cap.read()
        if not ok: break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        while want and t >= want[0]: got[want.pop(0)] = fr
        if t > b + 0.05:
            past += 1
            if b < a or past > LOOK:   # past the range (b < a: only the frames in `at` were asked for)
                if not want: break
                continue
        g = cv2.resize(cv2.cvtColor(picture(fr, area), cv2.COLOR_BGR2GRAY), (48, 27), interpolation=cv2.INTER_AREA).astype(np.float32)
        z = (g - g.mean()) / (g.std() + 1e-6)
        if prev is not None: m.append((t, float((z * prev[0]).mean()) if g.std() > 8 and prev[1] > 8 else None))   # None: too dark or flat to judge
        prev = (z, g.std())
    cap.release()
    out = []
    for i, (t, c) in enumerate(m):
        if c is None or t < a or t > b + 0.05: continue
        if c < HARD: out.append(round(t, 3)); continue
        near = [x for _, x in m[max(0, i - LOOK):i] + m[i + 1:i + 1 + LOOK]]
        if c < DIP and len(near) >= LOOK + 1 and all(x is not None and x >= max(AROUND, c + DEPTH) for x in near): out.append(round(t, 3))
    return out, got
