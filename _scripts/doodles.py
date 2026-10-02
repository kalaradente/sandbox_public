#!/usr/bin/env python3
"""Doodles: hand-drawn animation over a video, drawn ON the people as much as around them, in the vintage Lyrical Lemonade
style.
Animation is all or nothing: a video that gets any gets a hit every beat or two, all the way through.

Where the people are comes from Apple's Vision framework (vision_track.swift, built on first use; macOS only): each drawing's
frame gets the person's mask, face landmarks and body pose, so a doodle traces, fills or replaces what's really there and
follows it. Drawings change 12 times a second and are held in between ("on twos", like hand-drawn animation), and a cut
always gets a fresh drawing.

Specs (a recipe's "doodles" list, or render_overlay's): {"fx": <name>, "from": s, "to": s, ...options}. Times are the edit's.
  On the person:
    trace       the person's outline in thick shaky ink ("color", "width" as a fraction of the frame width, "fringe": two
                offset colour lines under it, like Famous Dex's; "draw": s to draw it on)
    fill        the person filled flat: two or three tones from the picture's own light, black ink for the darkest parts and the
                main edges, an ink outline ("colors": [dark, mid, light], "keep_face": the real face stays, a drawn body under
                a real head)
    silhouette  the person one flat colour with an ink outline ("color")
    hair        the hair scribbled a new colour ("colors": [base, scribble])
    face        a cartoon face over theirs, eyes where theirs are, mouth open when theirs is ("style": dex | tecca | monster
                | skull; "color": never black or near it: a dark one is drawn white)
    hearteyes   their eyes turned into beating hearts, pink cheeks
  On the head:  halo, horns, pin (a push-pin stuck in it), swirl (a scribble ring circling it), rays (a dashed aura),
                zzz (z's and a moon drifting up)
  Around:       hearts (a ring popping round the hands, or "at"), sparkles, bolts, marks ("text": "!!" / "?"), words ("text",
                "pops": the times each one appears, the same list on every shot so they build up), squiggles (colour
                lines swooping through), chain (a drawn chain round the neck)
  The frame:    ink (the whole picture inked over: flat colours and ink lines; "keep_person": only the background),
                border (a shaky drawn frame round the picture)
Common options: "color"/"colors" (palette names: ink, white, teal, yellow, purple, red, blue, pink, orange, green, or [r, g, b]),
"seed" (another arrangement), "at": [x, y] (a fixed point as fractions of the frame, instead of what Vision found),
"palette": {name: colour} (every colour of that name in the drawing, the ones an fx picks by itself too, becomes the other:
a video kept to two or three colours gives every spec the same one), "line": true (the shapes round and on the head as drawn
outlines, nothing filled, no ink edge: a marker on the picture instead of stickers). fill also takes "shade": "halftone" | "hatch"
(the mid tone printed as dots or lines of the mid colour on the light one, like newsprint; "pitch": their spacing, 0.0065 of the width).
Usage: python3 _scripts/doodles.py <video> <specs.json> <out.mp4> [--lyrics <lyric frame pattern> <its fps>]"""
import os, sys, json, math, random, subprocess, hashlib, shutil, tempfile, threading
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from sandbox_paths import ROOT, LIB  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PAL = {"ink": (22, 18, 26), "white": (255, 255, 255), "teal": (32, 168, 160), "yellow": (250, 212, 42), "purple": (132, 72, 236),
       "red": (236, 48, 62), "blue": (62, 112, 242), "pink": (255, 96, 176), "orange": (250, 128, 36), "green": (72, 204, 96), "wine": (150, 20, 40)}
INK = PAL["ink"]
DRAW_FPS = 12                    # drawings per second, each held ("on twos")
SS = 2                           # drawn at 2x and scaled down: smooth lines
MARKER = next((p for p in (os.path.join(d, "_assets/fonts/PermanentMarker-Regular.ttf") for d in (ROOT, LIB)) if os.path.exists(p)), "")
FX = {}
BODY = {"trace", "fill", "silhouette", "hair", "ink"}   # drawn on the body's outline: follow it every frame (the rest holds for 12ths of a second)
class _Ctx(threading.local):   # one for each thread (Lab_Render renders several recipes at once in threads: with one for all, a spec's palette and line coloured another recipe's drawing)
    def __init__(s): s.d = {"remap": {}, "line": False, "tex": {}}
    def __getitem__(s, k): return s.d[k]
    def __setitem__(s, k, v): s.d[k] = v
_CTX = _Ctx()   # the spec being drawn: its "palette" (colour names swapped) and "line" (shapes as outlines); "tex": this thread's kept drawings (_tex)

def col(c, a=255):
    if isinstance(c, str): c = _CTX["remap"].get(c, c)
    c = PAL.get(c, c) if isinstance(c, str) else c
    return tuple(int(v) for v in c[:3]) + (a,)

# ---------- Vision ----------
def helper():
    exe = os.path.join(HERE, "vision_track")
    if not os.access(exe, os.X_OK) or os.path.getmtime(exe) < os.path.getmtime(exe + ".swift"):
        print("building the Vision tracker (first time only, under a minute)...", flush=True)
        subprocess.run(["swiftc", "-O", "-o", exe, exe + ".swift"], check=True)
    return exe

def vision(video, fps, cache=None, every_frame_masks=False):
    """Run vision_track on every drawing's frame (masks, faces, pose), cached by the video's path, size and date; with
    every_frame_masks, the person mask for every frame too (outlines and fills that stay on a moving body).
    Returns (dir, every, {frame: row}, the every-frame masks' dir or None)."""
    every = max(1, int(round(fps / DRAW_FPS))); st = os.stat(video)
    key = hashlib.sha1(("%s|%d|%d|%d" % (os.path.abspath(video), st.st_size, int(st.st_mtime), every)).encode()).hexdigest()[:12]
    d = os.path.join(cache or os.path.join(LIB, "_lab", "_vision"), key)
    if not os.path.exists(os.path.join(d, "done")):
        print("finding the people (Vision, every %d frames)..." % every, flush=True)
        subprocess.run([helper(), video, d, "--every", str(every)], check=True, capture_output=True); open(os.path.join(d, "done"), "w").close()
    alld = None
    if every_frame_masks:
        alld = os.path.join(d, "all")
        if not os.path.exists(os.path.join(alld, "done")):
            print("following the body outline (Vision, every frame: a few minutes the first time)...", flush=True)
            subprocess.run([helper(), video, alld, "--masks-only"], check=True, capture_output=True); open(os.path.join(alld, "done"), "w").close()
    rows = {}
    for line in open(os.path.join(d, "track.jsonl")):
        r = json.loads(line); rows[r["n"]] = r
    return d, every, rows, alld

class Look:
    """What Vision saw on one drawing's frame, in output pixels (at SS)."""
    def __init__(self, row, vdir, W, H, mask=None):
        self.W, self.H = W * SS, H * SS; self.row = row or {"faces": [], "pose": []}
        fs = sorted(self.row.get("faces") or [], key=lambda f: -f["box"][2] * f["box"][3]); self.face = fs[0] if fs else None
        ps = self.row.get("pose") or []; self.pose = max(ps, key=len) if ps else {}
        self.mpath = os.path.join(vdir, "mask_%06d.png" % self.row.get("n", -1)); self._m = mask   # mask: this very frame's (the body layer)
    def mask(self, scale=SS):
        """The person mask, uint8 0-255, at the output size x scale (1 = output pixels)."""
        if self._m is None:
            self._m = cv2.imread(self.mpath, 0) if os.path.exists(self.mpath) else None
        w, h = self.W // SS * scale, self.H // SS * scale
        if self._m is None: return np.zeros((h, w), np.uint8)
        return cv2.resize(self._m, (w, h), interpolation=cv2.INTER_LINEAR)
    def P(self, p): return (p[0] * self.W, p[1] * self.H)
    def pts(self, region): return [self.P(p) for p in (self.face or {}).get("pts", {}).get(region, [])]
    def center(self, region):
        p = self.pts(region); return (sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p)) if p else None
    def head(self):
        """(x, y, width, height) of the head: the face box, widened for hair; None without a face (then the pose's head)."""
        if self.face:
            bx, by, bw, bh = self.face["box"]; return ((bx + bw / 2) * self.W, (by + bh / 2) * self.H, bw * self.W * 1.3, bh * self.H * 1.3)
        j = self.joint("head")
        if j:
            sh = [self.joint(k) for k in ("left_shoulder_1", "right_shoulder_1")]; w = abs(sh[0][0] - sh[1][0]) * 0.55 if all(sh) else self.W * 0.12
            return (j[0], j[1], w, w * 1.2)
        return None
    def joint(self, name):
        j = self.pose.get(name + "_joint"); return self.P(j) if j and j[2] > 0.15 else None
    def hands(self):
        h = [self.joint(k) for k in ("left_hand", "right_hand")]; h = [p for p in h if p]
        return (sum(x for x, _ in h) / len(h), sum(y for _, y in h) / len(h)) if h else None
    def body(self):
        """(x, y, width) of the person: the mask's box."""
        m = self.mask(1); ys, xs = np.nonzero(m > 127)
        if not len(xs): return None
        return ((xs.min() + xs.max()) / 2 * SS, (ys.min() + ys.max()) / 2 * SS, (xs.max() - xs.min()) * SS)
    def contours(self):
        """The person's outline(s), smoothed along the edge: a steady line, not the mask's pixel steps and flicker."""
        m = cv2.GaussianBlur(self.mask(1), (0, 0), 2); cs, _ = cv2.findContours((m > 127).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        out = []; k = np.exp(-0.5 * (np.arange(-18, 19) / 6.0) ** 2); k /= k.sum()
        for c in cs:
            if cv2.contourArea(c) <= m.size * 0.004: continue
            p = c[:, 0].astype(float)
            if len(p) > 40: p = np.stack([np.convolve(np.concatenate([p[-18:, j], p[:, j], p[:18, j]]), k, "valid") for j in (0, 1)], 1)
            out.append([(float(x) * SS, float(y) * SS) for x, y in p[::3]])
        return out

# ---------- drawing ----------
def resample(pts, step, closed=False):
    if len(pts) < 2: return pts
    q = pts + [pts[0]] if closed else pts; out = [q[0]]; carry = 0.0
    for (x0, y0), (x1, y1) in zip(q, q[1:]):
        d = math.hypot(x1 - x0, y1 - y0); s = step - carry
        while s <= d: out.append((x0 + (x1 - x0) * s / d, y0 + (y1 - y0) * s / d)); s += step
        carry = d - (s - step)
    return out

def wobble(pts, amp, rng, smooth=3):
    """Shaky hand: noise along the path, smoothed so it wanders rather than buzzes."""
    if len(pts) < 3 or amp <= 0: return pts
    n = np.array([[rng.gauss(0, amp), rng.gauss(0, amp)] for _ in pts])
    k = np.ones(smooth * 2 + 1) / (smooth * 2 + 1); n = np.stack([np.convolve(np.pad(n[:, i], smooth, mode="wrap"), k, "valid") for i in (0, 1)], 1)
    return [(x + a, y + b) for (x, y), (a, b) in zip(pts, n)]

def hand(pts, amp, rng, closed=True):
    """A drawn line's wander as a smooth function of the way along it: the same all through a drawing's hold (rng is the
    drawing's), however the line under it moves, and new with each drawing (12 a second, the boil)."""
    n = len(pts)
    if n < 3 or amp <= 0: return pts
    fr = [rng.randint(4, 7), rng.randint(9, 14), rng.randint(17, 26)]; ph = [rng.uniform(0, 6.3) for _ in range(6)]
    out = []
    for i, (x, y) in enumerate(pts):
        a = 2 * math.pi * i / (n if closed else 2 * n)
        out.append((x + amp * (0.6 * math.sin(fr[0] * a + ph[0]) + 0.3 * math.sin(fr[1] * a + ph[1]) + 0.15 * math.sin(fr[2] * a + ph[2])),
                    y + amp * (0.6 * math.sin(fr[0] * a + ph[3]) + 0.3 * math.sin(fr[1] * a + ph[4]) + 0.15 * math.sin(fr[2] * a + ph[5]))))
    return out

def brush(dr, pts, color, w, rng=None, closed=False, taper=True):
    """A marker/brush stroke: dabs along the path, the width breathing a little, thinner at the ends."""
    if len(pts) < 2: return
    p = resample(pts, max(1.5, w * 0.3), closed); n = len(p); rng = rng or random.Random(0); ph = rng.uniform(0, 6.3)
    for i, (x, y) in enumerate(p):
        r = w / 2 * (0.82 + 0.18 * math.sin(i * 0.21 + ph))
        if taper and not closed: r *= min(1.0, 0.35 + 0.65 * min(i, n - 1 - i) / max(1, min(8, n // 4)))
        dr.ellipse([x - r, y - r, x + r, y + r], fill=color)

def inked(dr, pts, color, w, rng, closed=False, ink=True):
    """A coloured stroke with a black ink edge (the Lyrical Lemonade line)."""
    if ink and not _CTX["line"]: brush(dr, pts, col("ink"), w * 1.55, random.Random(rng.random()), closed)
    brush(dr, pts, col(color), w, random.Random(rng.random()), closed)

def poly_inked(dr, pts, fill, w, rng, ink=True):
    if _CTX["line"]: brush(dr, pts, col(fill), w * 1.3, rng, closed=True); return   # "line": the shape as a drawn outline, nothing filled
    dr.polygon(pts, fill=col(fill)); brush(dr, pts, col("ink") if ink else col(fill), w, rng, closed=True)

def heart_pts(cx, cy, s, rot=0.0, rng=None, n=48):
    out = []
    for i in range(n):
        u = 2 * math.pi * i / n; x = 16 * math.sin(u) ** 3; y = -(13 * math.cos(u) - 5 * math.cos(2 * u) - 2 * math.cos(3 * u) - math.cos(4 * u))
        if rng: x += rng.gauss(0, 0.4); y += rng.gauss(0, 0.4)
        out.append((cx + (x * math.cos(rot) - y * math.sin(rot)) * s / 34, cy + (x * math.sin(rot) + y * math.cos(rot)) * s / 34))
    return out

def star_pts(cx, cy, r, rot=0.0, k=4, inner=0.3):
    return [(cx + (r if i % 2 == 0 else r * inner) * math.cos(rot + i * math.pi / k), cy + (r if i % 2 == 0 else r * inner) * math.sin(rot + i * math.pi / k)) for i in range(2 * k)]

def pop(u, d=0.12, span=1.0):
    """Scale for something that pops in at progress u (0..1 over the hit), overshooting a little; drawn in 12ths of a second."""
    x = u * span / d
    return 0.0 if x <= 0 else (1 + 2.7 * (x - 1) ** 3 + 1.7 * (x - 1) ** 2 if x < 1 else 1.0)

def lw(L, f): return max(2 * SS, L.W * f)

def fx(name):
    def reg(fn): FX[name] = fn; return fn
    return reg

# ---------- on the person ----------
@fx("trace")
def _trace(im, dr, L, frame, sp, u, rng):
    w = lw(L, float(sp.get("width", 0.0075))); draw = float(sp.get("draw", 0)); amt = min(1.0, u * float(sp.get("_dur", 1)) / draw) if draw else 1.0
    for c in L.contours():
        c = hand(resample(c, w * 0.8, True), w * 0.16, random.Random(rng.random())); c = c[:max(2, int(len(c) * amt))] if amt < 1 else c
        closed = amt >= 1
        if sp.get("fringe", True):
            for dx, dy, cc in ((w * 0.7, -w * 0.4, sp.get("fringe_colors", ["red", "blue"])[0]), (-w * 0.7, w * 0.4, sp.get("fringe_colors", ["red", "blue"])[1])):
                brush(dr, [(x + dx, y + dy) for x, y in c], col(cc), w * 0.55, random.Random(rng.random()), closed)
        brush(dr, c, col(sp.get("color", "white")), w, random.Random(rng.random()), closed)

def _region(L, keep_face=False):
    m = L.mask(SS) > 127
    if keep_face and L.face:
        cont = L.pts("contour"); bx, by, bw, bh = [v * s for v, s in zip(L.face["box"], (L.W, L.H, L.W, L.H))]
        fm = np.zeros(m.shape, np.uint8)
        cv2.ellipse(fm, (int(bx + bw / 2), int(by + bh * 0.42)), (int(bw * 0.66), int(bh * 0.95)), 0, 0, 360, 255, -1)   # the head, hair included
        if cont: cv2.fillPoly(fm, [np.array(cont, np.int32)], 255)
        m &= fm == 0
    return m

def _tex(sp, make):   # a drawing's flat colours and ink lines, made once from its frame and reused while it holds
    k = (sp.get("_key"), sp.get("_sid")); T = _CTX["tex"]   # kept for this thread only: another recipe's render emptied a shared one mid-hold
    if k not in T:
        if len(T) > 6: T.clear()
        T[k] = make()
    return T[k]

_SCREENS = {}
def _screen(w, h, kind, p):
    """A print screen the size of the drawing, fixed on the picture: True where the dots (halftone, at 45 degrees) or lines (hatch) are."""
    k = (w, h, kind, round(p, 2)); sc = _SCREENS.get(k)   # (shared between threads: handed back from here, never read again after another thread's clear)
    if sc is None:
        if len(_SCREENS) > 3: _SCREENS.clear()
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        if kind == "hatch": sc = ((xx + yy) % p) < p * 0.42
        else: a = (xx + yy) / 1.4142; b = (xx - yy) / 1.4142; sc = ((a % p - p / 2) ** 2 + (b % p - p / 2) ** 2) < (p * 0.36) ** 2
        _SCREENS[k] = sc
    return sc

def _outline(dr, L, rng, w):
    for c in L.contours(): brush(dr, hand(resample(c, 6, True), lw(L, 0.0012), random.Random(rng.random())), col("ink"), lw(L, w), rng, True)

@fx("fill")
def _fill(im, dr, L, frame, sp, u, rng):
    m = _region(L, sp.get("keep_face"))
    if not m.any(): return
    H2, W2 = m.shape
    def make():
        if frame is None: return None
        g = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, (W2, H2)), cv2.COLOR_BGR2GRAY), (0, 0), 3 * SS).astype(np.float32); v = g[m]
        cols = [col(c) for c in sp.get("colors", ["ink", "teal", "white"])]
        q = np.percentile(v, [22, 70]); tone = np.where(g < q[0], 0, np.where(g < q[1], 1, 2)); out = np.zeros((H2, W2, 4), np.uint8)
        for i, c in enumerate(cols[:3]): out[tone == i] = c
        if sp.get("shade") and len(cols) > 2: out[(tone == 1) & ~_screen(W2, H2, sp["shade"], W2 * float(sp.get("pitch", 0.0065)))] = cols[2]
        e = cv2.Canny(cv2.resize(frame, (W2 // (4 * SS) * 2, H2 // (4 * SS) * 2)), 60, 150)   # the main edges inside the person as ink
        out[cv2.resize(cv2.dilate(e, np.ones((2, 2), np.uint8)), (W2, H2), interpolation=cv2.INTER_NEAREST) > 0] = col("ink")
        return out
    tex = _tex(sp, make)
    if tex is None: return
    out = np.zeros_like(tex); out[m] = tex[m]; im.alpha_composite(Image.fromarray(out, "RGBA")); _outline(dr, L, rng, 0.007)

@fx("silhouette")
def _silhouette(im, dr, L, frame, sp, u, rng):
    m = _region(L, sp.get("keep_face"))
    out = np.zeros(m.shape + (4,), np.uint8); out[m] = col(sp.get("color", "white")); im.alpha_composite(Image.fromarray(out, "RGBA")); _outline(dr, L, rng, 0.006)

@fx("hair")
def _hair(im, dr, L, frame, sp, u, rng):
    if not L.face: return
    m = L.mask(SS) > 127; bx, by, bw, bh = [v * s for v, s in zip(L.face["box"], (L.W, L.H, L.W, L.H))]
    brows = L.pts("leftBrow") + L.pts("rightBrow"); top = min(y for _, y in brows) if brows else by + bh * 0.25
    reg = np.zeros(m.shape, bool); y0 = int(max(0, by - bh * 1.5)); y1 = int(top - bh * 0.03); x0 = int(max(0, bx - bw * 0.45)); x1 = int(min(m.shape[1], bx + bw * 1.45))
    reg[y0:max(y0, y1), x0:x1] = True; reg &= m
    if not reg.any(): return
    c1, c2 = (sp.get("colors") or ["purple", "blue"])[:2]; c3 = ((sp.get("colors") or [])[2:3] or ["pink"])[0]
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0)); d2 = ImageDraw.Draw(lay)
    ys, xs = np.nonzero(reg); d2.rectangle([xs.min(), ys.min(), xs.max(), ys.max()], fill=col(c1))
    for _ in range(26):   # scribbles across it
        x = rng.uniform(xs.min(), xs.max()); y = rng.uniform(ys.min(), ys.max()); a = rng.uniform(0, 6.3); ln = bw * rng.uniform(0.25, 0.6)
        pts = [(x + ln * t * math.cos(a) + bw * 0.05 * math.sin(t * 9), y + ln * t * math.sin(a) + bw * 0.05 * math.cos(t * 7)) for t in np.linspace(-0.5, 0.5, 12)]
        brush(d2, pts, col(c2 if rng.random() < 0.6 else c3), lw(L, 0.006), rng)
    a = np.array(lay); a[..., 3] = np.where(reg, a[..., 3], 0); im.alpha_composite(Image.fromarray(a, "RGBA"))

def _mouth(L):
    o, i = L.pts("outerLips"), L.pts("innerLips")
    if not o: return None
    cx = sum(x for x, _ in o) / len(o); cy = sum(y for _, y in o) / len(o); w = max(x for x, _ in o) - min(x for x, _ in o)
    op = (max(y for _, y in i) - min(y for _, y in i)) if i else 0.0
    return cx, cy, w, op

@fx("face")
def _face(im, dr, L, frame, sp, u, rng):
    if not L.face: return
    style = sp.get("style", "dex"); bx, by, bw, bh = [v * s for v, s in zip(L.face["box"], (L.W, L.H, L.W, L.H))]
    fc = sp.get("color", {"dex": "white", "tecca": "teal", "monster": "teal", "skull": "white"}.get(style, "white"))
    # never a black face over a person's. Decided on the colour as it will be drawn, the
    # spec's palette already applied, and handed on as numbers, so the palette can't turn it again ("palette": {"white": "ink"}
    # gave a black face). Near black: every channel under 100 (a deep blue or a dark red is a colour and stays). It is drawn white.
    fc = col(fc)[:3]
    if max(fc) < 100: fc = (255, 255, 255)
    s = pop(u, 0.08, float(sp.get("_dur", 1)))
    if s <= 0: return
    cont = L.pts("contour")
    if cont and len(cont) > 4:   # the jaw, closed over the top of the head
        (x0, y0), (x1, y1) = cont[0], cont[-1]; cxh, top = (x0 + x1) / 2, by - bh * 0.18
        arc = [(cxh + (x1 - x0) / 2 * math.cos(a) * 1.02, min(y0, y1) + (top - min(y0, y1)) * math.sin(a)) for a in np.linspace(0, math.pi, 16)]
        shape = cont + arc
    else:
        shape = [(bx + bw / 2 + bw * 0.55 * math.cos(a), by + bh * 0.45 + bh * 0.62 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 40)]
    cx, cy = sum(x for x, _ in shape) / len(shape), sum(y for _, y in shape) / len(shape)
    shape = wobble([(cx + (x - cx) * s, cy + (y - cy) * s) for x, y in resample(shape, 8 * SS, True)], bw * 0.006, rng)
    poly_inked(dr, shape, fc, lw(L, 0.0055), rng)
    if style == "dex":   # red and blue fringe lines, like his
        brush(dr, [(x + bw * 0.03, y) for x, y in shape[len(shape) // 3: 2 * len(shape) // 3]], col("red"), lw(L, 0.003), rng)
    eyes = [L.center("leftEye"), L.center("rightEye")]; pup = [L.center("leftPupil"), L.center("rightPupil")]
    ew = bw * 0.2
    for e, p in zip(eyes, pup):
        if not e: continue
        if style == "skull":
            poly_inked(dr, [(e[0] + ew * 0.7 * math.cos(a), e[1] + ew * 0.62 * math.sin(a)) for a in np.linspace(0, 6.28, 20)], "ink", lw(L, 0.003), rng)
        elif style == "dex":
            dr.ellipse([e[0] - ew * 0.28, e[1] - ew * 0.42, e[0] + ew * 0.28, e[1] + ew * 0.42], fill=col("ink"))
        else:
            r = ew * (0.62 if style == "tecca" else 0.55)
            poly_inked(dr, [(e[0] + r * math.cos(a), e[1] + r * 0.85 * math.sin(a)) for a in np.linspace(0, 6.28, 20)], "white", lw(L, 0.004), rng)
            px, py = p or e; pr = r * (0.22 if style == "monster" else 0.38); dr.ellipse([px - pr, py - pr, px + pr, py + pr], fill=col("ink"))
            if style == "tecca":   # glasses
                brush(dr, [(e[0] + r * 1.2 * math.cos(a), e[1] + r * 1.05 * math.sin(a)) for a in np.linspace(0, 6.28, 24)], col("ink"), lw(L, 0.004), rng, True)
    if all(eyes) and style == "tecca": brush(dr, [(eyes[0][0], eyes[0][1]), (eyes[1][0], eyes[1][1])], col("ink"), lw(L, 0.003), rng)
    for b in ("leftBrow", "rightBrow"):
        p = L.pts(b)
        if p and style != "skull": brush(dr, wobble([(x, y - bh * 0.03) for x, y in p[:4]], 1, rng), col("ink"), lw(L, 0.006 if style != "dex" else 0.004), rng)
    n = L.pts("nose")
    if n and style != "dex":
        if style == "skull": poly_inked(dr, heart_pts(sum(x for x, _ in n) / len(n), max(y for _, y in n) - bh * 0.03, bw * 0.14, math.pi), "ink", lw(L, 0.002), rng)
        else: brush(dr, n[len(n) // 2:], col("ink"), lw(L, 0.003), rng)
    mo = _mouth(L)
    if mo:
        mx, my, mw, op = mo; open_ = op > bh * 0.05
        if style == "monster":   # a jagged mouth full of teeth, too big for the face
            ww, hh = mw * 0.95, max(bh * 0.16, op * 1.6); top = [(mx - ww + 2 * ww * i / 12, my - hh * 0.35 + (hh * 0.35 if i % 2 else 0)) for i in range(13)]
            bot = [(mx + ww - 2 * ww * i / 12, my + hh * 0.65 - (hh * 0.35 if i % 2 else 0)) for i in range(13)]
            dr.polygon([(mx - ww, my - hh * 0.4), (mx + ww, my - hh * 0.4), (mx + ww * 0.8, my + hh * 0.7), (mx - ww * 0.8, my + hh * 0.7)], fill=col("ink"))
            poly_inked(dr, top + [(mx + ww, my - hh * 0.5), (mx - ww, my - hh * 0.5)], "white", lw(L, 0.002), rng)
            poly_inked(dr, bot + [(mx - ww, my + hh * 0.8), (mx + ww, my + hh * 0.8)], "white", lw(L, 0.002), rng)
        elif style == "skull":
            for k in range(7):
                x = mx - mw * 0.6 + mw * 1.2 * k / 6; brush(dr, [(x, my - bh * 0.05), (x, my + bh * 0.06)], col("ink"), lw(L, 0.003), rng)
            brush(dr, [(mx - mw * 0.65, my), (mx + mw * 0.65, my)], col("ink"), lw(L, 0.003), rng)
        elif open_:
            o = L.pts("outerLips"); o = [(mx + (x - mx) * 1.15, my + (y - my) * 1.25) for x, y in o]
            poly_inked(dr, o, "wine", lw(L, 0.004), rng)
            dr.rectangle([mx - mw * 0.35, min(y for _, y in o) + bh * 0.01, mx + mw * 0.35, min(y for _, y in o) + bh * 0.045], fill=col("white"))
        else:
            brush(dr, [(mx - mw * 0.6 + mw * 1.2 * t, my + bh * 0.05 * math.sin(math.pi * t)) for t in np.linspace(0, 1, 10)], col("ink"), lw(L, 0.005), rng)
    if style == "monster":   # spikes of hair
        for k in range(9):
            a = math.pi * (1.05 + 0.9 * k / 8); r0 = bw * 0.55
            x0, y0 = cx + r0 * math.cos(a), by + bh * 0.25 + r0 * math.sin(a); x1, y1 = cx + r0 * 1.7 * math.cos(a + rng.uniform(-0.1, 0.1)), by + bh * 0.25 + r0 * 1.7 * math.sin(a)
            brush(dr, [(x0, y0), (x1, y1)], col("ink"), lw(L, 0.006), rng)

@fx("hearteyes")
def _hearteyes(im, dr, L, frame, sp, u, rng):
    if not L.face: return
    bw = L.face["box"][2] * L.W; beat = 1 + 0.12 * math.sin(u * float(sp.get("_dur", 1)) * 2 * math.pi * 2)
    for e in (L.center("leftEye"), L.center("rightEye")):
        if e: poly_inked(dr, heart_pts(e[0], e[1], bw * 0.32 * beat * pop(u, 0.1, float(sp.get("_dur", 1))), 0, rng), sp.get("color", "red"), lw(L, 0.004), rng)
    mo = _mouth(L)
    for e in (L.center("leftEye"), L.center("rightEye")):
        if e and mo: dr.ellipse([e[0] - bw * 0.08, mo[1] - bw * 0.2, e[0] + bw * 0.08, mo[1] - bw * 0.13], fill=col("pink", 170))

# ---------- on the head ----------
def _head_or_at(L, sp):
    if sp.get("at"): return (sp["at"][0] * L.W, sp["at"][1] * L.H, L.W * float(sp.get("size", 0.15)), L.W * float(sp.get("size", 0.15)))
    return L.head()

@fx("halo")
def _halo(im, dr, L, frame, sp, u, rng):
    h = _head_or_at(L, sp)
    if not h: return
    x, y, w, hh = h; s = pop(u, 0.1, float(sp.get("_dur", 1))); cy = max(y - hh * 0.62, w * 0.2)   # kept in the picture
    ring = wobble([(x + w * 0.45 * s * math.cos(a), cy + w * 0.12 * s * math.sin(a)) for a in np.linspace(0, 6.28, 50)], w * 0.006, rng)
    inked(dr, ring, sp.get("color", "yellow"), lw(L, 0.007), rng, closed=True)
    for k in range(6):
        a = -math.pi / 2 + (k - 2.5) * 0.35; brush(dr, [(x + w * 0.55 * math.cos(a) * s, cy - w * 0.12 + w * 0.2 * math.sin(a)), (x + w * 0.7 * math.cos(a) * s, cy - w * 0.2 + w * 0.26 * math.sin(a))], col("yellow"), lw(L, 0.003), rng)

@fx("horns")
def _horns(im, dr, L, frame, sp, u, rng):
    h = _head_or_at(L, sp)
    if not h: return
    x, y, w, hh = h; s = pop(u, 0.1, float(sp.get("_dur", 1))); top = max(y - hh * 0.45, w * 0.48)   # kept in the picture
    for side in (-1, 1):
        bx = x + side * w * 0.26; pts = [(bx - w * 0.1, top), (bx + side * w * 0.08 * s + side * w * 0.12 * s, top - w * 0.42 * s), (bx + w * 0.1, top)]
        mid = [(pts[0][0] + pts[2][0]) / 2, top]; curve = [pts[0], (bx + side * w * 0.03, top - w * 0.2 * s), pts[1], (bx + side * w * 0.2 * s, top - w * 0.16 * s), pts[2]]
        poly_inked(dr, wobble(resample(curve, 4 * SS, True), 1.5, rng), sp.get("color", "red"), lw(L, 0.004), rng)

@fx("pin")
def _pin(im, dr, L, frame, sp, u, rng):
    h = _head_or_at(L, sp)
    if not h: return
    x, y, w, hh = h; s = pop(u, 0.08, float(sp.get("_dur", 1))); tx, ty = x + w * 0.08, max(y - hh * 0.5, w * 0.5)   # kept in the picture
    a = -1.1; ln = w * 0.45 * s
    brush(dr, [(tx, ty), (tx + ln * 0.5 * math.cos(a), ty + ln * 0.5 * math.sin(a))], col((180, 180, 190)), lw(L, 0.004), rng)
    hx, hy = tx + ln * math.cos(a), ty + ln * math.sin(a)
    poly_inked(dr, [(hx + w * 0.13 * s * math.cos(t), hy + w * 0.13 * s * math.sin(t)) for t in np.linspace(0, 6.28, 24)], sp.get("color", "red"), lw(L, 0.004), rng)
    poly_inked(dr, [(tx + ln * 0.5 * math.cos(a) + w * 0.12 * s * math.cos(t), ty + ln * 0.5 * math.sin(a) + w * 0.05 * s * math.sin(t)) for t in np.linspace(0, 6.28, 20)], sp.get("color", "red"), lw(L, 0.003), rng)

@fx("swirl")
def _swirl(im, dr, L, frame, sp, u, rng):
    h = _head_or_at(L, sp)
    if not h: return
    x, y, w, hh = h; dur = float(sp.get("_dur", 1)); prog = min(1.0, u * dur / float(sp.get("draw", 0.4))) if sp.get("draw", 0.4) else 1.0
    cy = y - hh * 0.38; spin = u * dur * 1.3; n = int(200 * prog) + 2; pts = []
    for i in range(n):
        th = 2 * math.pi * 2.3 * i / 199 + spin
        k = 1 + 0.17 * math.sin(2.7 * th) + 0.08 * math.sin(5.3 * th + 1.1) + rng.gauss(0, 0.012)
        pts.append((x + w * 0.62 * k * math.cos(th), cy + w * 0.17 * k * math.sin(th) + (i / 199 - 0.5) * w * 0.16))
    inked(dr, pts, sp.get("color", "white"), lw(L, 0.0045), rng)

@fx("rays")
def _rays(im, dr, L, frame, sp, u, rng):
    h = _head_or_at(L, sp)
    if not h: return
    x, y, w, hh = h; n = int(sp.get("count", 16)); spin = u * float(sp.get("_dur", 1)) * 0.6; cs = sp.get("colors") or ["white", "yellow", "pink"]
    for k in range(n):
        a = spin + 2 * math.pi * k / n + rng.uniform(-0.05, 0.05); r0 = w * (0.62 + 0.06 * (k % 2)); r1 = r0 + w * rng.uniform(0.14, 0.24)
        inked(dr, [(x + r0 * math.cos(a), y - hh * 0.05 + r0 * 1.05 * math.sin(a)), (x + r1 * math.cos(a), y - hh * 0.05 + r1 * 1.05 * math.sin(a))], cs[k % len(cs)], lw(L, 0.0055), rng)

@fx("zzz")
def _zzz(im, dr, L, frame, sp, u, rng):
    h = _head_or_at(L, sp)
    if not h: return
    x, y, w, hh = h; dur = float(sp.get("_dur", 1)); f = ImageFont.truetype(MARKER, int(w * 0.28))
    for k in range(3):
        v = (u * dur * 0.8 + k / 3) % 1; zx, zy = x + w * (0.35 + 0.3 * v) + w * 0.06 * math.sin(v * 6), y - hh * (0.3 + 0.9 * v)
        dr.text((zx, zy), "z", font=ImageFont.truetype(MARKER, int(w * (0.18 + 0.2 * v))), fill=col("white"), stroke_width=0 if _CTX["line"] else int(w * 0.02), stroke_fill=col("ink"), anchor="mm")
    mx, my = x - w * 0.55, y - hh * 0.75; r = w * 0.2
    moon = [(mx + r * math.cos(a), my + r * math.sin(a)) for a in np.linspace(-2.2, 2.2, 20)] + [(mx + r * 0.35 + r * 0.72 * math.cos(a), my + r * 0.72 * math.sin(a)) for a in np.linspace(1.9, -1.9, 20)]
    poly_inked(dr, moon, "yellow", lw(L, 0.004), rng)

# ---------- around ----------
@fx("hearts")
def _hearts(im, dr, L, frame, sp, u, rng):
    if sp.get("at"): x, y, w = sp["at"][0] * L.W, sp["at"][1] * L.H, L.W * float(sp.get("size", 0.14))
    else:
        c = L.hands(); hd = L.head()
        if not c: return
        w = (hd[2] * 1.1) if hd else L.W * 0.14; x, y = c
    k = int(sp.get("count", 8)); R = random.Random(int(sp.get("seed", 7))); dur = float(sp.get("_dur", 1)); cs = sp.get("colors") or ["red", "pink", "white"]
    for i in range(k):
        ang = math.radians(-170 + 160 * i / max(1, k - 1) + R.uniform(-8, 8)); r = w * R.uniform(0.7, 1.05); sz = w * R.uniform(0.25, 0.4); c = cs[i % len(cs)]
        s = pop(u - 0.03 * i / dur, 0.1, dur)
        if s <= 0.02: continue
        poly_inked(dr, heart_pts(x + r * math.cos(ang), y + r * math.sin(ang), sz * s, math.radians((math.degrees(ang) + 90) * 0.35), rng), c, lw(L, 0.0035), rng)

@fx("sparkles")
def _sparkles(im, dr, L, frame, sp, u, rng):
    b = L.body(); h = L.head() or (b and (b[0], b[1], b[2] * 0.5, b[2] * 0.5))
    if not h: return
    x, y, w, hh = h; R = random.Random(int(sp.get("seed", 3))); dur = float(sp.get("_dur", 1)); cs = sp.get("colors") or ["yellow", "white", "pink", "teal"]
    for i in range(int(sp.get("count", 10))):
        a = R.uniform(0, 6.28); r = w * R.uniform(0.7, 1.6); t0 = R.uniform(0, 0.6)
        s = pop(u - t0, 0.08, dur) * (1 - max(0, (u - t0 - 0.35)) * 1.2)
        if s <= 0.05: continue
        poly_inked(dr, star_pts(x + r * math.cos(a), y + r * 0.8 * math.sin(a), w * R.uniform(0.08, 0.16) * s, rng.uniform(-0.2, 0.2)), cs[i % len(cs)], lw(L, 0.0025), rng)

@fx("bolts")
def _bolts(im, dr, L, frame, sp, u, rng):
    h = L.head()
    if not h: return
    x, y, w, hh = h; R = random.Random(int(sp.get("seed", 5))); dur = float(sp.get("_dur", 1))
    for i in range(int(sp.get("count", 4))):
        a = -math.pi / 2 + R.uniform(-1.3, 1.3); r = w * R.uniform(0.75, 1.1); cx, cy = x + r * math.cos(a), y - hh * 0.1 + r * math.sin(a); s = w * 0.28 * pop(u - i * 0.05, 0.08, dur)
        if s <= 1: continue
        pts = [(0, -1), (0.35, -1), (0.1, -0.2), (0.45, -0.2), (-0.2, 1), (0.02, 0.1), (-0.3, 0.1)]; rot = a + math.pi / 2
        pts = [(cx + s * (px * math.cos(rot) - py * math.sin(rot)), cy + s * (px * math.sin(rot) + py * math.cos(rot))) for px, py in pts]
        poly_inked(dr, pts, sp.get("color", "yellow"), lw(L, 0.003), rng)

@fx("marks")
def _marks(im, dr, L, frame, sp, u, rng):
    h = L.head()
    if not h: return
    x, y, w, hh = h; dur = float(sp.get("_dur", 1)); t = sp.get("text", "!!")
    for i, ch in enumerate(t):
        s = pop(u - i * 0.06, 0.08, dur)
        if s <= 0.05: continue
        f = ImageFont.truetype(MARKER, max(8, int(w * 0.45 * s)))
        dr.text((x + w * (0.6 + 0.2 * i), y - hh * (0.55 + 0.05 * i)), ch, font=f, fill=col(sp.get("color", "yellow")), stroke_width=0 if _CTX["line"] else int(w * 0.025), stroke_fill=col("ink"), anchor="mm")

SPOTS = [(-0.34, -0.36), (0.36, -0.22), (-0.30, 0.16), (0.30, 0.26), (0.02, 0.46), (-0.46, 0.46), (0.44, -0.56), (-0.22, -0.66), (0.44, 0.58), (-0.02, 0.12), (-0.5, -0.1), (0.52, 0.05)]
@fx("words")
def _words(im, dr, L, frame, sp, u, rng):
    b = L.body()
    if sp.get("at"): b = (sp["at"][0] * L.W, sp["at"][1] * L.H, L.W * float(sp.get("size", 0.4)))
    if not b: return
    x, y, w = b; w = min(w, L.W * 0.6); t0 = float(sp.get("_from", 0)); dur = float(sp.get("_dur", 1)); now = t0 + u * dur
    pops = sorted(float(v) for v in sp.get("pops") or [t0]); cs = sp.get("colors") or ["yellow", "white", "pink", "teal"]; ly = sp.get("_lyric_y")
    for i, pt in enumerate(pops[:len(SPOTS)]):
        if now < pt: continue
        s = pop((now - pt) / dur, 0.1, dur) if pt >= t0 else 1.0
        dx, dy = SPOTS[i]; R = random.Random(i * 13 + 1); rot = R.uniform(-22, 22) + rng.uniform(-1.5, 1.5); fs = w * R.uniform(0.1, 0.14) * s
        if fs < 4: continue
        f = ImageFont.truetype(MARKER, int(fs)); lay = Image.new("RGBA", (int(fs * 5), int(fs * 2)), (0, 0, 0, 0))
        ImageDraw.Draw(lay).text((lay.size[0] / 2, lay.size[1] / 2), sp.get("text", "lies").upper(), font=f, fill=col(cs[i % len(cs)]), stroke_width=0 if _CTX["line"] else max(2, int(fs * 0.09)), stroke_fill=col("ink"), anchor="mm")
        lay = lay.rotate(rot, resample=Image.BICUBIC, expand=True); wx, wy = x + dx * w, y + dy * w
        if ly is not None and abs(wy - ly) < L.H * 0.05 + fs * 0.4: wy = ly + math.copysign(L.H * 0.05 + fs * 0.4, wy - ly)   # never over the lyric line
        im.alpha_composite(lay, (int(max(0, min(L.W - lay.size[0], wx - lay.size[0] / 2))), int(max(0, min(L.H - lay.size[1], wy - lay.size[1] / 2)))))

@fx("squiggles")
def _squiggles(im, dr, L, frame, sp, u, rng):
    b = L.body() or (L.W / 2, L.H / 2, L.W * 0.4); x, y, w = b; R = random.Random(int(sp.get("seed", 9))); dur = float(sp.get("_dur", 1))
    cs = sp.get("colors") or ["teal", "yellow", "purple", "pink"]
    for i in range(int(sp.get("count", 3))):
        side = R.choice((-1, 1)); y0 = y + R.uniform(-0.5, 0.5) * w; amp = w * R.uniform(0.05, 0.12); fr = R.uniform(2, 4); ph = R.uniform(0, 6.3)
        prog = min(1.0, max(0.0, (u * dur - i * 0.08) / 0.35))
        if prog <= 0: continue
        n = int(40 * prog) + 2
        pts = [(x + side * (-w * 0.9 + w * 1.8 * t), y0 + amp * math.sin(fr * 2 * math.pi * t + ph) + w * 0.25 * (t - 0.5) * side) for t in np.linspace(0, prog, n)]
        inked(dr, pts, cs[i % len(cs)], lw(L, 0.006), rng)

@fx("chain")
def _chain(im, dr, L, frame, sp, u, rng):
    ls, rs, nk = L.joint("left_shoulder_1"), L.joint("right_shoulder_1"), L.joint("neck_1")
    if not (ls and rs and nk): return
    w = abs(ls[0] - rs[0]); cx = (ls[0] + rs[0]) / 2; top = nk[1] - w * 0.08
    for k in range(22):
        t = k / 21; a = math.pi * t; px, py = cx - w * 0.28 * math.cos(a), top + w * 0.34 * math.sin(a)
        r = w * 0.028; poly_inked(dr, [(px + r * math.cos(q), py + r * 0.7 * math.sin(q)) for q in np.linspace(0, 6.28, 10)], sp.get("color", "yellow"), lw(L, 0.0018), rng)

# ---------- the frame ----------
@fx("ink")
def _ink(im, dr, L, frame, sp, u, rng):
    Wt, Ht = L.W, L.H
    out = _tex(sp, lambda: _ink_tex(frame, sp, L, rng))
    if out is None: return
    out = out.copy()
    if sp.get("keep_person"): out[L.mask(SS) > 127] = 0
    im.alpha_composite(Image.fromarray(out, "RGBA"))
    if sp.get("keep_person"): _outline(dr, L, rng, 0.006)

def _ink_tex(frame, sp, L, rng):
    if frame is None: return None
    Wt, Ht = L.W, L.H; small = cv2.resize(frame, (320, int(320 * Ht / Wt)), interpolation=cv2.INTER_AREA)
    sm = cv2.medianBlur(cv2.pyrMeanShiftFiltering(small, 10, 28), 5); g = cv2.GaussianBlur(cv2.cvtColor(sm, cv2.COLOR_BGR2GRAY), (0, 0), 1.5).astype(np.float32)   # flat areas, like paint
    cols = [col(c) for c in sp.get("colors", ["ink", "teal", "yellow", "white"])]; qs = np.percentile(g, np.linspace(0, 100, len(cols) + 1)[1:-1])
    tone = np.digitize(g, qs); out = np.zeros(g.shape + (4,), np.uint8)
    for i, c in enumerate(cols): out[tone == i] = c
    out = cv2.resize(out, (Wt, Ht), interpolation=cv2.INTER_NEAREST)
    cs, _ = cv2.findContours(cv2.Canny(sm, 60, 140), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)   # only the long lines, drawn as shaky ink
    sc = Wt / 320.0; lay = Image.fromarray(out, "RGBA"); d2 = ImageDraw.Draw(lay)
    for c in cs:
        if cv2.arcLength(c, False) < 40: continue
        brush(d2, wobble([(float(x) * sc, float(y) * sc) for x, y in c[::2, 0]], sc * 0.3, rng), col("ink"), lw(L, 0.0035), rng)
    return np.array(lay)

@fx("border")
def _border(im, dr, L, frame, sp, u, rng):
    m = L.W * 0.02; pts = [(m, m), (L.W - m, m), (L.W - m, L.H - m), (m, L.H - m)]
    inked(dr, wobble(resample(pts, 12 * SS, True), L.W * 0.002, rng), sp.get("color", "yellow"), lw(L, 0.012), rng, closed=True)

# ---------- rendering ----------
def render_overlay(specs, video, W, H, fps, d, cuts=(), cache=None, lyric_y=None):
    """Draw every spec over `video` into RGBA frames d/dd_%05d.png (one per output frame, from the first spec's start; held
    drawings are hard links). cuts: the video's cut times (a cut always gets a fresh drawing). Returns (pattern, start) or None."""
    bad = [s.get("fx") for s in specs if s.get("fx") not in FX]
    if bad: raise ValueError("doodles: unknown fx %s (known: %s)" % (", ".join(map(str, bad)), ", ".join(sorted(FX))))
    if not specs: return None
    _CTX["tex"].clear()   # (the last recipe's kept drawings: a new spec can be given a finished one's id)
    body_on = any(s["fx"] in BODY for s in specs)
    vdir, every, rows, alld = vision(video, fps, cache, every_frame_masks=body_on); os.makedirs(d, exist_ok=True)
    t0 = min(float(s["from"]) for s in specs); t1 = max(float(s["to"]) for s in specs)
    n0, n1 = int(round(t0 * fps)), int(round(t1 * fps)); cutn = sorted(int(round(c * fps)) for c in cuts)
    keys = sorted(rows)
    def shot_of(n): return max([c for c in cutn if c <= n], default=0), min([c for c in cutn if c > n], default=10 ** 9)
    def key_for(n):
        start = shot_of(n)[0]; ks = [k for k in keys if start <= k <= n]
        return ks[-1] if ks else next((k for k in keys if k >= start), keys[-1])
    small = {}
    def mask_at(n):
        """This frame's person mask, averaged with its neighbours in the same shot (steadies the edge's flicker)."""
        if not alld: return None
        a, b = shot_of(n); ms = []
        for q in (n - 1, n, n + 1):
            if not a <= q < b: continue
            if q not in small:
                if len(small) > 12: small.pop(next(iter(small)))
                f = os.path.join(alld, "mask_%06d.png" % q); small[q] = cv2.imread(f, 0) if os.path.exists(f) else None
            if small[q] is not None: ms.append(small[q].astype(np.float32))
        return np.mean(ms, 0).astype(np.uint8) if ms else None
    need_pix = {"fill", "ink"} & {s["fx"] for s in specs}
    cap = cv2.VideoCapture(video) if need_pix else None; cur = -1; img = None; kframe = {}
    def frame_for(k):
        nonlocal cur, img
        if cap is None: return None
        if k in kframe: return kframe[k]
        tk = k / fps
        if k < cur or cur < 0: cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, tk - 1.0) * 1000); cur = -1
        while cur < k:
            ok, img = cap.read()
            if not ok: break
            cur = int(round(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 * fps))
        kframe.clear(); kframe[k] = img; return img
    empty = os.path.join(d, "_empty.png"); Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(empty)
    made, top_im = {}, {}
    order = {"ink": 0, "fill": 1, "silhouette": 1, "hair": 2, "trace": 3, "face": 4, "hearteyes": 5}
    def draw(specs_, L, frame, tk, k):
        im = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
        for s in sorted(specs_, key=lambda s: order.get(s["fx"], 9)):
            a, b = float(s["from"]), float(s["to"]); u = 0.0 if tk < a else min(1.0, max(0.0, (tk - a) / max(1e-3, b - a)))
            s2 = dict(s, _dur=b - a, _from=a, _key=k, _sid=id(s), _lyric_y=None if lyric_y is None else lyric_y * H * SS)
            _CTX["remap"] = s.get("palette") or {}; _CTX["line"] = bool(s.get("line"))
            try: FX[s["fx"]](im, dr, L, frame, s2, u, random.Random(k * 7919 + int(s.get("seed", 0))))   # the same wobble all through a drawing's hold
            finally: _CTX["remap"] = {}; _CTX["line"] = False
        return im.resize((W, H), Image.LANCZOS)
    for n in range(n0, n1 + 1):
        t = n / fps; k = key_for(n); tk = k / fps
        live = [s for s in specs if float(s["from"]) <= t < float(s["to"])]
        on_body = [s for s in live if s["fx"] in BODY]; rest = [s for s in live if s["fx"] not in BODY]
        dst = os.path.join(d, "dd_%05d.png" % (n - n0))
        if not live: os.link(empty, dst); continue
        sig = (k, tuple(id(s) for s in rest))
        if sig not in made:   # everything not on the body: drawn 12 times a second and held (the jumpy, hand-drawn feel)
            im = draw(rest, Look(rows.get(k), vdir, W, H), frame_for(k) if any(s["fx"] in need_pix for s in rest) else None, tk, k) if rest else None
            p = os.path.join(d, "k_%06d_%d.png" % (k, len(made)))
            if im is not None: im.save(p, compress_level=1)
            made[sig] = p if im is not None else empty; top_im.clear(); top_im[sig] = im
        if not on_body: os.link(made[sig], dst); continue
        # on the body (outlines, fills): this very frame's outline, so it stays on a moving body; the lines' wander and the fill's
        # colours still change only with each drawing
        lay = draw(on_body, Look(rows.get(k), vdir, W, H, mask=mask_at(n)), frame_for(k) if any(s["fx"] in need_pix for s in on_body) else None, tk, k)
        if top_im.get(sig) is not None: lay = Image.alpha_composite(lay, top_im[sig])
        lay.save(dst, compress_level=1)
    return os.path.join(d, "dd_%05d.png"), n0 / fps

def composite(video, pat, t0, fps_str, out, lyrics=None, lyr_fps=30, crf=16):
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", video, "-itsoffset", "%.6f" % t0, "-framerate", fps_str, "-i", pat]
    fc = "[0:v][1:v]overlay=0:0:eof_action=pass:format=auto[a]"
    if lyrics: cmd += ["-framerate", str(lyr_fps), "-i", lyrics]; fc += ";[a][2:v]overlay=0:0:eof_action=pass:format=auto[v]"
    else: fc += ";[a]null[v]"
    subprocess.run(cmd + ["-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p",
                          "-c:a", "copy", "-movflags", "+faststart", out], check=True)

if __name__ == "__main__":
    if len(sys.argv) < 4 or "--help" in sys.argv: print(__doc__); sys.exit(0 if "--help" in sys.argv else 2)
    video, specs, out = sys.argv[1:4]; cfg = json.load(open(specs)); specs = cfg["doodles"] if isinstance(cfg, dict) else cfg
    cap = cv2.VideoCapture(video); fps = cap.get(cv2.CAP_PROP_FPS); W, H = int(cap.get(3)), int(cap.get(4)); cap.release()
    rate = (subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", video], capture_output=True, text=True).stdout.split() or [str(fps)])[0]   # first line only (a stream group repeats it)
    tmp = tempfile.mkdtemp(prefix="doodles_")
    try:
        lyr = sys.argv[sys.argv.index("--lyrics") + 1:sys.argv.index("--lyrics") + 3] if "--lyrics" in sys.argv else None
        got = render_overlay(specs, video, W, H, fps, tmp, cuts=cfg.get("cuts", []) if isinstance(cfg, dict) else [], lyric_y=cfg.get("lyric_y") if isinstance(cfg, dict) else None)
        composite(video, got[0], got[1], rate, out, lyr[0] if lyr else None, float(lyr[1]) if lyr else 30)
        print("done", out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
