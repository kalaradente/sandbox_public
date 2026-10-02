"""Grab the sharpest real frame within +-0.25 s of time t (never across a cut) from any clip; saves to _lab/stills/.
A letterboxed clip's still keeps only its picture (picture(): the render's own crop), so a logo or credits in the bars never
reach a collage; it's saved as <clip>_<t>_aa.jpg (aa = active area), so it can't overwrite an uncropped <clip>_<t>.jpg."""
import os, sys, cv2, json
sys.path.insert(0, os.path.dirname(__file__)); from shot_check import cuts_in
from sandbox_paths import LIB; OUT = os.path.join(LIB, "_lab/stills"); os.makedirs(OUT, exist_ok=True)
import Lab_Render as LR  # noqa: E402  (the clip index + lab index, and how a clip is framed)

def picture(fr, c):
    """(frame, cropped): the frame cut down to clip c's picture inside its letterbox bars, the crop Lab_Render.framing()
    gives the render and Resolve. Uncropped if c isn't letterboxed or its active area doesn't fit this frame."""
    k = LR.framing(c or {})["crop"]
    if not k or k[0] + k[2] > fr.shape[1] or k[1] + k[3] > fr.shape[0]: return fr, False
    w, h, x, y = k; return fr[y:y + h, x:x + w], True

def grab(path, t, what=""):
    f = path if os.path.isabs(path) else os.path.join(LIB, path)
    entry = LR.load_index().get(os.path.splitext(os.path.basename(f))[0])   # keyed by file name, like the indexes
    cs = cuts_in(f, max(0, t - 0.6), t + 0.6); lo = max([c for c in cs if c <= t] or [t - 0.25]); hi = min([c for c in cs if c > t] or [t + 0.25])
    cap = cv2.VideoCapture(f); cap.set(cv2.CAP_PROP_POS_MSEC, max(0, t - 1.0) * 1000); best = None
    while True:
        ok, fr = cap.read()
        if not ok: break
        tt = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        if tt < max(t - 0.25, lo + 0.07): continue
        if tt > min(t + 0.25, hi - 0.07): break
        fr, cropped = picture(fr, entry)
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY); g2 = cv2.resize(g, (max(1, round(g.shape[1] * 480 / g.shape[0])), 480))
        rows = g2.mean(axis=1) > 8; l = float(g2[rows].mean()) if rows.any() else 0
        if l < 18: continue
        s = float(cv2.Laplacian(g2, cv2.CV_64F).var())
        if not best or s > best[0]: best = (s, tt, fr.copy(), cropped)
    if not best: return None
    name = "%s_%05.2f%s.jpg" % (os.path.splitext(os.path.basename(f))[0].lstrip("."), best[1], "_aa" if best[3] else ""); p = os.path.join(OUT, name)
    cv2.imwrite(p, best[2], [cv2.IMWRITE_JPEG_QUALITY, 95])
    return dict(file=os.path.relpath(p, LIB), t=round(best[1], 3), what=what, clip=os.path.splitext(os.path.basename(f))[0])
