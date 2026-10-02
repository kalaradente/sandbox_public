#!/usr/bin/env python3
"""Shot check by numbers: what used to take ~100 frame images a round (the in / middle / out frame of every shot).
  python3 _scripts/shot_verify.py <round folder or recipe.json> ...           before rendering: the plan
  python3 _scripts/shot_verify.py --renders <round folder or recipe.json> ...  after rendering: plan + each render
Per shot, from the source file: a cut inside the shot or within 2 frames of its end (frame-by-frame on the picture inside any
bars, shot_check.py: flashes pass, real cuts don't; plus the cuts the index knows), a near-black or blown-out frame at its
in / middle / out, running past the clip's end, two shots showing the same moment, and a shot under half a second unless it's one of a run of short shots that lets go into a longer one.
Faces, where the fill crop cuts picture off (Apple Vision, Lab_Render.looks: the render's own answer, remembered): the main
face partly or wholly outside the frame the render shows ("center": "face" frames on it), two faces further apart than the
frame is wide, a face that moves across more than the frame holds (no fixed frame keeps those: another moment, or a shorter
shot). Posters, screens and people far behind don't count (Lab_Render.main_faces).
A recipe the render would refuse over its "pixels" (an option it doesn't know, or a picture that isn't whole for half a second
once its pixels have landed: Lab_Render.pixels_of and pixel_room, the render's own rules) gets a line saying so, a collage too.
With --renders, each shot's middle frame in the rendered mp4 is compared with the source frame the recipe asked for,
framed the render's way (Lab_Render.framing): a low match means the render doesn't show what was planned.
The plan's lines are remembered per recipe (a small file in the temp folder) with a fingerprint of everything they read, so
--renders after a plan check decodes only the renders. A change to any of it (the recipe, a clip's index entry, a source
file's size or date, a Vision answer, a blend, this check's code) checks that recipe in full. To check everything afresh:
delete $TMPDIR/sandbox_shot_verify.
Only flagged shots need looking at (the contact sheet or one frame); the round's board image stays the one visual pass."""
import base64, glob, hashlib, json, os, sys, tempfile
import cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, round_files, write_atomic  # noqa: E402
import shot_check  # noqa: E402
from shot_check import cuts_in, frame_at, scan  # noqa: E402

a = sys.argv[1:]; RENDERS = "--renders" in a; a = [x for x in a if x != "--renders"]
if not a: sys.exit(__doc__)
load = lambda rel: json.load(open(os.path.join(LIB, rel)))["clips"] if os.path.exists(os.path.join(LIB, rel)) else {}
IDX = {**load("_lab/lab_index.json"), **load("_reference/clip_index.json")}
import Lab_Render  # noqa: E402  (framing and sizes: the render's own)
# The plan's lines, remembered: --renders used to run the whole plan check again, ~60 s a round. In the temp folder, like lab_plan_helpers' cut cache: it's only worth keeping for the
# minutes between the plan check and the render check, and it never travels with the library, the drive or the pack.
MEMO = os.path.join(tempfile.gettempdir(), "sandbox_shot_verify")
CODE = hashlib.sha256(b"|".join([open(p, "rb").read() for p in (os.path.abspath(__file__), shot_check.__file__, Lab_Render.__file__)]
                                + [cv2.__version__.encode(), np.__version__.encode()])).hexdigest()   # a changed check, cut finder or framing starts afresh


def grab(path, t):
    return frame_at(path, max(0, t))


def framed(fr, c, W, H, f=None):
    """A source frame the way the render frames it: crop to the picture, turn sideways footage, fill W x H from the middle, or
    round the centre framing f found (Lab_Render.framing(c, shot, ...), at the render's own size)."""
    f = f or Lab_Render.framing(c)
    if f["crop"]: w, h, x, y = f["crop"]; fr = fr[y:y + h, x:x + w]
    if f["turn"]: fr = cv2.rotate(fr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    s = max(W / fr.shape[1], H / fr.shape[0]); fr = cv2.resize(fr, (max(W, round(fr.shape[1] * s)), max(H, round(fr.shape[0] * s))))
    if f.get("center"): x0, y0 = [min(max(0, round(v * n - m / 2)), n - m) for v, n, m in zip(f["center"], (fr.shape[1], fr.shape[0]), (W, H))]
    else: y0, x0 = (fr.shape[0] - H) // 2, (fr.shape[1] - W) // 2
    return fr[y0:y0 + H, x0:x0 + W]


def window(c, f, W, H):
    """The part of clip c's picture (after the crop and the turn) the render's frame shows, as fractions: (x0, y0, x1, y1)."""
    pw, ph = Lab_Render.picture_size(c, f); s = max(W / pw, H / ph); ww, wh = W / (pw * s), H / (ph * s)
    cx, cy = f.get("center") or (0.5, 0.5); x0, y0 = min(max(0, cx - ww / 2), 1 - ww), min(max(0, cy - wh / 2), 1 - wh)
    return x0, y0, x0 + ww, y0 + wh


def cropped(c, W, H):
    """Whether the fill crop cuts more than 3% off clip c's picture on a side (only then can the frame cut a face)."""
    w = window(c, Lab_Render.framing(c), W, H); return w[2] - w[0] <= 0.97 or w[3] - w[1] <= 0.97


def people(F):
    """The people in a shot's looks F (Lab_Render.shot_faces): the look with the most faces names them, the other looks'
    faces join the nearest (each face once; one found only in another look is someone of its own), left to right.
    A list of [x, y, w, h] boxes per person."""
    m = lambda b: (b[0] + b[2] / 2, b[1] + b[3] / 2); d = lambda a, b: (m(a)[0] - m(b)[0]) ** 2 + (m(a)[1] - m(b)[1]) ** 2
    i0 = max(range(len(F)), key=lambda i: (len(F[i]), i == len(F) // 2)); P = [[b] for b in F[i0]]
    for i, fs in enumerate(F):
        if i == i0: continue
        free, pairs = list(fs), sorted((d(b, p[0]), j, n) for j, p in enumerate(P[:len(F[i0])]) for n, b in enumerate(fs))
        took, used = set(), set()
        for _, j, n in pairs:
            if j not in took and n not in used: P[j].append(fs[n]); took.add(j); used.add(n)
        P += [[b] for n, b in enumerate(fs) if n not in used]
    return sorted(P, key=lambda p: sum(m(b)[0] for b in p) / len(p))


def faces_cut(c, s, span, f, W, H):
    """What the frame does to the faces of shot s (framing f): a line to print, or None. A face counts as cut when less than
    95% of it is in the frame in the shot's in, middle or out look (a face bigger than the frame: when it doesn't fill it).
    Two people whose faces don't fit the frame together: the planner says who the shot is about (Vision never picks between
    them: Lab_Render.subject_center), so the line gives each one's "center", clamped as framing() would apply it."""
    if not cropped(c, W, H): return None
    w = window(c, f, W, H); F = Lab_Render.shot_faces(Lab_Render.looks(c, Lab_Render.look_times(s, span)))
    if not any(F): return None
    k = 0 if w[2] - w[0] < w[3] - w[1] else 1; win = w[k + 2] - w[k]                        # the side the fill crop cuts
    P = people(F); allf = [b for p in P for b in p]; u = Lab_Render.union(allf); sp = lambda bs: [(b[k], b[k] + b[k + 2]) for b in bs]
    if len(P) > 1 and u[k + 2] - u[k] > win + 1e-3:
        if not isinstance(s.get("center"), (list, tuple)):   # nobody chose yet: each person's frame, ready to paste
            names = ["left", "right"] if len(P) == 2 else ["left", "middle", "right"] if len(P) == 3 else ["%d from the left" % (j + 1) for j in range(len(P))]
            at = []
            for p, name in zip(P, names):
                cx, cy = [Lab_Render.hold([(b[q], b[q] + b[q + 2]) for b in p], (w[q + 2] - w[q])) for q in (0, 1)]
                g = [round(v, 3) for v in Lab_Render.framing(c, dict(s, center=[cx, cy]), W, H, span)["center"] or (0.5, 0.5)]   # 3 places: 2 lost a few % of a moving face
                h = float(Lab_Render.held(sp(p), min(max(0, g[k] - win / 2), 1 - win), win))   # how whole that frame keeps them (they may move)
                at.append('"center": [%g, %g] (%s%s)' % (g[0], g[1], name, ", %.0f%% of the face at worst: they move" % (100 * h) if h < 0.95 else ""))
            return "%s, the frame can't hold them together: say who the shot is about: %s" % ("two people" if len(P) == 2 else "%d people" % len(P), " or ".join(at))
        ctr = (w[k] + w[k + 2]) / 2   # the planner chose: judge the person the frame is on
        allf = min(P, key=lambda p: abs(sum(b[k] + b[k + 2] / 2 for b in p) / len(p) - ctr))
    now = float(Lab_Render.held(sp(allf), w[k], win)); best = float(Lab_Render.held(sp(allf), np.linspace(0, 1 - win, 801), win).max()); why = []
    if now < 0.95: why.append("main face cut (%.0f%% of it in the frame at worst)" % (100 * now))
    if best < 0.95: why.append("the face moves further between the shot's in and out than the frame holds (%.0f%% at best): no fixed frame keeps it, pick another moment or a shorter shot" % (100 * best))
    if len(why) == 1 and now < 0.95 and not s.get("center"): why[0] += ': "center": "face" frames on it'   # a fixed frame can hold it
    return "; ".join(why) or None


def thumb(x):
    """What sim compares: a frame as a small grey thumbnail (64 x 36, 8 bits). The source frame's is remembered with the plan,
    so the render check needs no second decode of the source."""
    return cv2.resize(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY), (36, 64), interpolation=cv2.INTER_AREA)


def sim(p, q):
    """How alike two frames (or their thumbs) are (correlation of small grey thumbnails): ~1 same picture, below ~0.6 different."""
    g = [(thumb(x) if x.ndim == 3 else x).astype(np.float32) for x in (p, q)]
    z = [(x - x.mean()) / (x.std() + 1e-6) for x in g]
    return float((z[0] * z[1]).mean())


def flurry(shots, i):
    """A short shot on purpose: three or more shots under half a second in a row, then one of a second or more."""
    a = b = i
    while a > 0 and shots[a - 1]["dur"] < 0.5 - 1e-3: a -= 1
    while b + 1 < len(shots) and shots[b + 1]["dur"] < 0.5 - 1e-3: b += 1
    return b - a + 1 >= 3 and b + 1 < len(shots) and shots[b + 1]["dur"] >= 1.0 - 1e-3


def render_of(path, rid):
    """The edit's mp4 wherever the review page moved it (round, keep, perfect children, deleted), notes after the ID."""
    rnd = os.path.basename(os.path.dirname(os.path.abspath(path)))
    for d in (os.path.dirname(os.path.abspath(path)), os.path.join(LIB, "_lab/keep"), os.path.join(LIB, "_lab/perfect children"),
              os.path.join(LIB, "_to_delete/lab_deleted", rnd)):
        m = sorted(glob.glob(os.path.join(d, glob.escape(rid) + ".mp4")) + glob.glob(os.path.join(d, glob.escape(rid) + " *.mp4")))
        if m: return m[0]
    return os.path.join(os.path.dirname(os.path.abspath(path)), rid + ".mp4")


def label(s, i): return "shot %d %s @%.2f+%.2f" % (i + 1, s["clip"][:28], s["in"], s["dur"])


def stamp(p):
    """A file's size and date, or None when it's gone (clips get deleted while sessions run)."""
    try: st = os.stat(p); return [st.st_size, st.st_mtime_ns]
    except OSError: return None


def fingerprint(r, W, H, bl, spans):
    """Everything recipe r's plan lines read, hashed: the recipe, its blend lengths (a "long" blend reads the song's tempo and
    the clip's cuts), each clip's index entries (and the one the cut finder finds by file name) with its file's size and date,
    the Vision answers its shots are framed and judged on, and this check's code. Any of it changed: the recipe is checked in
    full. A stale answer is worse than a slow one."""
    d = [CODE, LIB, r, bl]
    for k in sorted({s["clip"] for s in r["shots"]}):
        c = IDX.get(k)
        d.append([k, c] + ([IDX.get(os.path.splitext(os.path.basename(c["file"]))[0]), stamp(os.path.join(LIB, c["file"]))] if c else []))
    for i, s in enumerate(r["shots"]):   # the shots whose check reads Vision (faces_cut, framing on "face" / "subject"): read it the same way
        c = IDX.get(s["clip"])
        if c and os.path.exists(os.path.join(LIB, c["file"])) and (s.get("center") in ("face", "subject") or cropped(c, W, H)):
            d.append([i, Lab_Render.looks(c, Lab_Render.look_times(s, spans[i]))])
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=repr).encode()).hexdigest()


def memo_file(path): return os.path.join(MEMO, hashlib.sha1(os.path.realpath(path).encode()).hexdigest()[:16] + ".json")   # one per recipe: a re-cut replaces its own


def remembered(path, key, n):
    """The plan of recipe `path` from an earlier run whose fingerprint was key, or None: never run, changed since, or a file that
    doesn't load (cut off, another version): then the recipe is checked in full, and says nothing about it."""
    try:
        d = json.load(open(memo_file(path)))
        if d["key"] != key or len(d["shots"]) != n: return None
        out = [dict(lines=p["lines"], skip=p["skip"], thumb=None if p["thumb"] is None else
                    np.frombuffer(base64.b64decode(p["thumb"], validate=True), np.uint8).reshape(64, 36)) for p in d["shots"]]
        return out if all(isinstance(p["lines"], list) and all(isinstance(x, str) for x in p["lines"]) and isinstance(p["skip"], bool) for p in out) else None
    except Exception:   # anything wrong with a cache file only costs the time of a full check
        return None


def remember(path, key, plans):
    try:
        write_atomic(memo_file(path), json.dumps(dict(key=key, recipe=os.path.abspath(path), shots=[
            dict(lines=p["lines"], skip=p["skip"], thumb=None if p["thumb"] is None else base64.b64encode(p["thumb"].tobytes()).decode()) for p in plans])))
    except OSError:
        pass   # no cache this time: the check itself is done


def plan_shot(r, i, W, H, span, shown):
    """Shot i's plan lines, in order; skip: not in the index or its file missing (no render comparison); thumb: the source frame
    the render is compared with (at shown), None when the shot's decode didn't reach it."""
    s = r["shots"][i]; c = IDX.get(s["clip"]); n = label(s, i); out = []
    if not c: return dict(lines=["%s: not in the index" % n], skip=True, thumb=None)
    src = os.path.join(LIB, c["file"])
    if not os.path.exists(src): return dict(lines=["%s: file missing" % n], skip=True, thumb=None)
    f = Lab_Render.framing(c, s, W, H, span)   # the frame the render shows (round the shot's "center"; span = Lab_Render.span_of)
    fc = faces_cut(c, s, span, f, W, H)
    if fc: out.append("%s: %s" % (n, fc))
    if c.get("duration") and s["in"] + span > c["duration"] - 0.03: out.append("%s: runs past the clip's end (%.2f s)" % (n, c["duration"]))
    ts = (("in", s["in"] + 0.02), ("middle", s["in"] + span / 2), ("out", s["in"] + span - 0.04))
    cut, got = scan(src, s["in"] + 1 / 30, s["in"] + span + 1 / 30, [t for _, t in ts] + [shown])   # one decode: cuts and the frames; 2 frames past the end (rules §7)
    cut += [x for x in c.get("internal_cuts") or [] if s["in"] + 1 / 30 < x < s["in"] + span + 2 / 30 and not any(abs(x - y) < 0.1 for y in cut)]   # look-alike shots can pass the frame check: the index's cuts count too
    if cut: out.append("%s: a cut inside the shot or within 2 frames of its end at %s" % (n, ", ".join("%.2f" % x for x in sorted(cut))))
    if s["dur"] < 0.5 - 1e-3 and not flurry(r["shots"], i): out.append("%s: under half a second: too fast to see, unless it's a run of short shots letting go into a longer one" % n)
    for tag, t in ts:
        fr = got.get(t)
        if fr is None: fr = grab(src, t)
        if fr is None and tag == "out": fr = grab(src, t - 0.12)   # the last frames of a file can't always be seeked
        if fr is None: out.append("%s: can't read the %s frame" % (n, tag)); continue
        fr = framed(fr, c, W // 4, H // 4, f); y = float(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).mean())
        if y < 18: out.append("%s: %s frame is very dark (%.0f): look at it" % (n, tag, y))
        elif y > 235: out.append("%s: %s frame is blown out (%.0f)" % (n, tag, y))
    for j, o in enumerate(r["shots"][:i]):   # the same moment of a clip twice (no cut between them). Look-alike shots are a judgement: similar pictures in one world are often the point
        if o["clip"] == s["clip"] and abs(o["in"] - s["in"]) < 1.5 and not cuts_in(src, min(o["in"], s["in"]), max(o["in"], s["in"])):
            out.append("%s: same moment as shot %d" % (n, j + 1))
    b_ = got.get(shown)
    return dict(lines=out, skip=False, thumb=None if b_ is None else thumb(framed(b_, c, W // 4, H // 4, f)))


def check(path):
    r = json.load(open(path)); out = []
    try: px = Lab_Render.pixels_of(r)
    except ValueError as e: return r, ["the render refuses it: %s" % e]   # its line, and the round's other recipes are still checked (nothing below can be worked out without the options: a shot's span reads them)
    if px and r.get("kind") in ("video", "collage"):   # room for every flight: the render's own rule (pixel_room), asked before rendering, in frames the way its pixel passes count them
        F = Lab_Render.FPS; total = sum(int(round(s["dur"] * F)) for s in r["shots"]) if r["kind"] == "video" else sum(Lab_Render.still_frames(r))
        try: Lab_Render.pixel_room(r, [int(round(c * F)) for c in Lab_Render.cut_times(r)], total, int(round(px["fly"] * F)))
        except ValueError as e: out.append("the render refuses it: %s" % str(e).replace("%s: " % r.get("id"), "", 1))
    if r.get("kind") != "video": return r, out
    W, H = Lab_Render.size(r["format"]); mp4 = render_of(path, r["id"]); t_out = 0.0
    rfr = {}; bl = [0.0] + [Lab_Render.blend_in(r, j) for j in range(1, len(r["shots"]))]   # bl[i]: seconds shot i blends in from the one before
    # where each shot is compared with its render: the middle of the part no blend covers (a blend-in fills the shot's start; a
    # blend-out runs on AFTER its cut, under the next shot), so the render's frame there is this shot alone. "pixels" does both
    # the same way: the shot forms out of the one before over its first `fly` seconds, and runs on under the next (run_on)
    cov = [b or (px["fly"] if px and i else 0.0) for i, b in enumerate(bl)]
    tau = [(min(cov[i], s["dur"] - 2 / 30) + s["dur"]) / 2 for i, s in enumerate(r["shots"])]
    if RENDERS and os.path.exists(mp4):   # every shot's compared frame in the render, read in one pass
        mids, t = [], 0.0
        for i, s in enumerate(r["shots"]): mids.append(t + tau[i]); t += int(round(s["dur"] * 30)) / 30
        rfr = scan(mp4, 0, -1, mids)[1]
    look = {}   # Vision on every shot the fill crop cuts, one run per clip (the render reads the same answers after)
    for i, s in enumerate(r["shots"]):
        c = IDX.get(s["clip"])
        if c and os.path.exists(os.path.join(LIB, c["file"])) and cropped(c, W, H): look.setdefault(s["clip"], []).extend(Lab_Render.look_times(s, Lab_Render.span_of(r, i)))
    for k, ts in look.items(): Lab_Render.looks(IDX[k], ts)
    spans = [Lab_Render.span_of(r, i) for i in range(len(r["shots"]))]   # a shot that blends out runs on under the next
    key = fingerprint(r, W, H, bl, spans); memo = remembered(path, key, len(r["shots"])); plans = []
    for i, s in enumerate(r["shots"]):
        sp = s.get("speed", 1.0); span = spans[i]
        shown = s["in"] + (s["dur"] * sp - tau[i] * sp if s.get("reverse") else tau[i] * sp)   # the source moment the render shows at tau (a reversed shot runs from the far end of its own in..in+dur; one that runs on carries on past "in")
        p = memo[i] if memo else plan_shot(r, i, W, H, span, shown); plans.append(p); out += p["lines"]
        if p["skip"]: continue
        if RENDERS:
            if not os.path.exists(mp4): out.append("not rendered yet: %s" % os.path.basename(mp4)); break
            c = IDX[s["clip"]]; a_ = rfr.get(t_out + tau[i]); b_ = p["thumb"]
            if b_ is None:   # the shot's own decode didn't reach that frame: read it alone
                fr = grab(os.path.join(LIB, c["file"]), shown)
                if fr is not None: b_ = thumb(framed(fr, c, W // 4, H // 4, Lab_Render.framing(c, s, W, H, span)))
            if a_ is not None and b_ is not None:
                m = sim(cv2.resize(a_, (W // 4, H // 4)), b_)
                if m < (0.25 if r.get("fx") or r.get("grade") else 0.4): out.append("%s: the render's frame doesn't match the planned one (%.2f)" % (label(s, i), m))
        t_out += int(round(s["dur"] * 30)) / 30
    if memo is None and len(plans) == len(r["shots"]): remember(path, key, plans)   # a check stopped at "not rendered yet" isn't whole
    return r, out


paths = []
for x in a:
    paths += [x] if os.path.isfile(x) else round_files(x) or [x]   # a round by path, name or number: its recipes, wherever it sits in _lab/rounds
bad = n = 0
from concurrent.futures import ThreadPoolExecutor  # noqa: E402  (edits in parallel: decoding releases the GIL)
with ThreadPoolExecutor(min(8, os.cpu_count() or 4)) as ex: results = list(ex.map(check, paths))
for r, out in results:
    n += r.get("kind") == "video"   # (a collage has no shots to check: its only lines are what the render would refuse it for)
    for o in out: print("!! %s %s" % (r["id"], o)); bad += 1
print("%d video edits, %d shots checked: %s" % (n, sum(len(json.load(open(p)).get("shots") or []) for p in paths), "%d to look at" % bad if bad else "all clean"))
sys.exit(1 if bad else 0)
