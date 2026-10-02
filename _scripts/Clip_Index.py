#!/usr/bin/env python3
"""Clip index builder.

Usage (from the sandbox folder; clip paths are relative to library/):
  python3 _scripts/Clip_Index.py            # add any new clip in the source folders (layout.json; .mp4 .mov .m4v, subfolders too), except _external content
  python3 _scripts/Clip_Index.py --refresh  # recompute machine fields for every clip, keep the hand-written ones
  python3 _scripts/Clip_Index.py "_internal content" ...   # only these folders (library-relative); nothing elsewhere is marked removed. It saves after every clip.
  python3 _scripts/Clip_Index.py "_external content/<shoot>"   # the person's own footage: ONLY when they said yes, and only the folder the ask is about. A plain run skips it and says
                                                            # how many files arrived there since they were last asked (those asked about: _reference/external_asked.txt).
  python3 _scripts/Clip_Index.py "_external content" --estimate   # what indexing would cost, folder by folder (files, GB, already indexed, time); indexes nothing
     Own footage over 20 GB or 30 minutes in one run, or the whole of _external content, stops before starting. Then either a part: --most 40 (files) or --most 10GB, the newest first (the newest shoot is
     likeliest what they're promoting; run it again for the next part), or all of it once the person said yes to all of it: --yes-all.
     Indexing runs one file at a time, at full speed when nothing else is running; while a render or other video work runs
     (an ffmpeg or Lab_Render.py of anyone's), each pass gives way to it (a Mac's utility class), so the render keeps its speed.
  python3 _scripts/Clip_Index.py --sheet library/_inbox/a.mp4 ...   # (or _inbox/a.mp4) contact sheets only (for sorting new downloads); written to _inbox/_sheets/

Writes _reference/clip_index.json and a contact sheet per clip in _reference/_index_sheets/
(timestamps on each thumbnail are true seconds in the source clip).
Machine fields: duration, size, fps, vfr, internal cuts, strobe span, active picture area, brightness, colour.
Hand fields (filled by Claude after looking at the sheet): tier, who, description, colour, feel, hooks, best, avoid, flags, use.
New clips get needs_review=true until those are filled in, except _external content: machine fields only; its hand fields
(the review) only when the person asks.
If ffmpeg hangs on a clip, the clip is marked analyze_failed; retry it once with a longer timeout. (Each pass's time limit grows with
the clip's length and size, and a pass stopped by it fails the clip: saved half-read, its cuts and light would be only the first part's.)
A clip whose file is gone is marked use "removed" (missing_on_disk); what its use was is kept (use_before_removed), and when
the file is back (moved out and back, a folder renamed mid-run) it gets that use back.
"""
import json, subprocess, sys, os, re, math, glob, csv, shutil, statistics as st, datetime, copy
from fractions import Fraction
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from sandbox_paths import LIB, VIDEO_EXT, layout, edit_json, locked  # library/: footage, indexes, lab
SOURCES = layout().get("source_folders") or []   # layout.json: the folders that hold footage (searched recursively)
INDEX = os.path.join(LIB, "_reference/clip_index.json")
ASKED = os.path.join(LIB, "_reference/external_asked.txt")   # _external content files the person was already asked about (one per line)
EXTERNAL = "_external content"   # the person's own footage: indexed only when they say so (big dumps take hours; only useful for edits from it or Resolve smart tags)
EST_FILE, EST_GB = 13.4, 23.2   # seconds a file + seconds a GB: fitted to the first own-footage run's contact-sheet times (Sep 28, M1 Pro:
                                # 260 files, 36 GB, 4 h of footage in 71 min, at normal priority); a render beside it slows it
LIMIT_GB, LIMIT_MIN = 20, 30    # own footage over either in one run stops before starting (a part with --most, or --yes-all)
SHEETS = os.path.join(LIB, "_reference/_index_sheets")
FONT = next((f for f in ["/usr/share/fonts/truetype/lato/Lato-Medium.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"] if os.path.exists(f)), None)
HAND = ["tier", "who", "description", "colour", "feel", "hooks", "best", "avoid", "flags", "use", "notes",
        "scene_match", "stills", "catalog_reason", "reviewed", "cfr30_copies", "cuts_note"]
sys.dont_write_bytecode = True

GENTLE = False   # main() sets it: an index run's passes give way to other video work (Lab_Pull's use of machine() is unchanged)

def busy():
    """Another video job on this Mac right now (a render, another session's ffmpeg). Ours run one at a time, so none is running."""
    return subprocess.run(["pgrep", "-f", r"(^|/)ffmpeg |Lab_Render\.py"], capture_output=True).returncode == 0

def run(cmd, t=90):
    # Measured Sep 29 (two encodes side by side): nice 0, 10 and 20 all finish together on this Mac; a utility-clamped one gives way
    # (the background class too, but ~18x slower, E-cores only). Clamped always, the index ran ~3x slower with nothing else running.
    pre = ["taskpolicy", "-c", "utility"] if GENTLE and busy() else []
    r = subprocess.run(pre + ["timeout", "-s", "KILL", str(int(t))] + cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode in (-9, 137): raise RuntimeError("%s stopped after %d s" % (cmd[0], t))   # killed half way (timeout KILLs its own group): its output is only the first part
    return r

def scd(path, vf_pre="", t=90):
    r = run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-an", "-vf", f"{vf_pre}scale=160:-2,scdet=t=6:s=0,metadata=print:key=lavfi.scd.score:file=-", "-f", "null", "-"], t)
    ev, cur = [], None
    for line in r.stdout.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m: cur = float(m.group(1))
        m = re.search(r"lavfi\.scd\.score=([\d.]+)", line)
        if m and cur is not None: ev.append((round(cur, 3), round(float(m.group(1)), 1)))
    return ev

def is_cut(path, t):
    """True if the picture changes at t (a cut), False if it's the same picture brighter/darker (a strobe or flash).
    Frames too dark or flat to judge count as lighting."""
    import cv2
    from shot_check import frame_at
    cap = cv2.VideoCapture(path); rs = []
    def g(x):
        fr = frame_at(path, max(0, x), cap)   # an accurate frame: a plain seek can land both sides of the cut on one keyframe
        return None if fr is None else cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (64, 36), interpolation=cv2.INTER_AREA).astype(float)
    for d in (0.12, 0.3):
        a, b = g(t - d), g(t + d)
        if a is None or b is None: continue
        if a.std() < 12 or b.std() < 12: rs.append(1.0); continue
        za, zb = (a - a.mean()) / (a.std() + 1e-6), (b - b.mean()) / (b.std() + 1e-6)
        rs.append(float((za * zb).mean()))
    return bool(rs) and max(rs) <= 0.65

def make_sheet(f, dur, out, n=None, cols=6):
    """Contact sheet with TRUE timestamps: each tile is the actual frame read at that time and is labelled with that
    frame's own timestamp. (The old ffmpeg fps+drawtext sheet labelled each tile with its slot time while showing a frame
    about half a slot later, so every time read off the old sheets was 0.5-1.1s early.)"""
    import cv2, numpy as np
    n = n or int(min(24, max(12, round(dur)))); cap = cv2.VideoCapture(f); tiles = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (i * dur / n) * 1000); ok, fr = cap.read()
        if not ok: continue
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000  # timestamp of the frame just read
        h, w = fr.shape[:2]; fr = cv2.resize(fr, (200, int(round(h * 200 / w))), interpolation=cv2.INTER_AREA)
        lab = "%.3f" % t; (tw, th), _ = cv2.getTextSize(lab, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(fr, (0, 0), (tw + 6, th + 8), (0, 0, 0), -1); cv2.putText(fr, lab, (3, th + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        tiles.append(fr)
    if not tiles: return False
    H = max(t.shape[0] for t in tiles); rows = math.ceil(len(tiles) / cols)
    can = np.full((rows * (H + 2), cols * 202, 3), 255, np.uint8)
    for i, t in enumerate(tiles):
        y, x = (i // cols) * (H + 2), (i % cols) * 202; can[y:y + t.shape[0], x:x + 200] = t
    return cv2.imwrite(out, can, [cv2.IMWRITE_JPEG_QUALITY, 88])

def moving_area(f, dur, n=60):
    """The picture inside letterbox bars that carry a logo, credit line or title, or that an end card fills (cropdetect counts
    all of those as picture). Over n frames across the clip a picture row changes and a bar row doesn't, even with text in it:
    per pixel, the median distance from its median over time (so a card in a few frames doesn't count); per row, the median of
    that along the row (so text across part of a bar doesn't). The longest run of changing rows is the picture's height, the
    same over those rows its width. Bars only count if they're dark and the picture's edge against them is a sharp line in at
    least 1 frame in 5 (the dark, still sky of a night shot is neither). Returns [w, h, x, y] like cropdetect, or None."""
    import cv2, numpy as np
    cap = cv2.VideoCapture(f); R, C = [], []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (i + 0.5) * dur / n * 1000); ok, fr = cap.read()
        if not ok: continue
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY); H, W = g.shape; sx, sy = max(1, W // 480), max(1, H // 270)
        R.append(g[:, ::sx]); C.append(g[::sy, :].T)   # every row (sampled along it), every column (sampled down it)
    if len(R) < n // 2: return None
    def lines(s):   # s: (frame, line, along the line) -> how much each line changes, and each line's brightness per frame
        return np.median(np.median(np.abs(s.astype(np.int16) - np.median(s, 0)), 0), 1), np.median(s, 2)
    def longest(m):
        e = np.diff(np.r_[0, m.astype(int), 0]); a, b = np.where(e == 1)[0], np.where(e == -1)[0]
        return (0, 0) if not len(a) else (int(a[np.argmax(b - a)]), int(b[np.argmax(b - a)]))
    rmove, rlit = lines(np.stack(R)); y0, y1 = longest(rmove > 2)
    if y1 - y0 < 8 * sy: return None
    cmove, clit = lines(np.stack(C)[:, :, -(-y0 // sy):-(-y1 // sy)]); x0, x1 = longest(cmove > 2)
    if x1 - x0 < 8: return None
    for lit, a, b, N in ((rlit, y0, y1, H), (clit, x0, x1, W)):
        for lo, hi, inside, out in ((0, a, a + 2, a - 3), (b, N, b - 3, b + 2)):   # the bar before the picture, the bar after it
            if lo == hi: continue
            out = min(max(out, lo), hi - 1)
            if np.median(lit[:, lo:hi]) > 16 or ((lit[:, inside].astype(int) - lit[:, out]) >= 12).mean() < 0.2: return None
    x, y = x0 + (x0 & 1), y0 + (y0 & 1)   # even, like cropdetect's round=2
    return [(x1 - x) & ~1, (y1 - y) & ~1, x, y]

def machine(rel, old=None, key=None):
    """Machine fields for one clip. old = its index entry: a picture area measured by hand (letterbox_note) is kept.
    key = its index key (names the contact sheet; default: the file name)."""
    f = os.path.join(LIB, rel); name = key or os.path.splitext(os.path.basename(f))[0]
    p = json.loads(run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate,avg_frame_rate,codec_name:stream_side_data=rotation:format=duration", "-of", "json", f], 30).stdout or "{}")
    v = [s for s in p.get("streams", []) if s["codec_type"] == "video"][0]
    dur = float(p["format"]["duration"]); w, h = v["width"], v["height"]
    rot = next((abs(int(float(d["rotation"]))) for d in v.get("side_data_list") or [] if "rotation" in d), 0)
    if rot % 180 == 90: w, h = h, w   # a phone's vertical video: landscape pixels + a rotation flag; ffmpeg (renders, sheets, stills) shows it upright
    T = 90 + 4 * dur   # the passes below read every frame: a long own file (a whole show, a 4K master) giving way to a render needs more than 90 s
    ts = sorted(float(x) for x in run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts_time", "-of", "csv=p=0", f], 30 + 20 * os.path.getsize(f) / 1e9).stdout.split() if x.strip() and x != "N/A")
    d = [b - a for a, b in zip(ts, ts[1:]) if b > a]; med = st.median(d)
    vfr = sum(1 for x in d if abs(x - med) > med * 0.25) / len(d) > 0.02
    if (old or {}).get("letterbox_note"):
        letterbox = bool(old.get("letterboxed") and old.get("active_area")); crop = list(old["active_area"]) if letterbox else [w, h, 0, 0]
    else:
        r = run(["ffmpeg", "-nostdin", "-v", "info", "-i", f, "-an", "-vf", "fps=2,cropdetect=limit=16:round=2:reset=0", "-f", "null", "-"], T)
        c = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr); crop = list(map(int, c[-1])) if c else [w, h, 0, 0]
        letterbox = crop[1] < h * 0.8 and crop[0] > w * 0.9
        if not letterbox:   # no black bars found: bars with text or an end card in them read as picture, so look at what moves
            m = moving_area(f, dur)
            if m and m[1] < h * 0.8 and m[0] > w * 0.5: crop, letterbox = m, True
    ev = scd(f, f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]}," if letterbox else "", T)
    strong = [t for t, s in ev if s >= 14]; cuts, flick = [], []
    for t in strong: (flick if sum(1 for u in strong if abs(u - t) <= 0.6) >= 3 else cuts).append(t)
    # a burst of changes is either a strobe or a fast-cut edit (0.4-0.5s shots): compare the picture on each side
    for t in [t for t in flick if is_cut(f, t)]:
        if all(abs(t - u) >= 0.1 for u in cuts): cuts.append(t)
    cuts.sort(); flick = [t for t in flick if t not in cuts]
    r = run(["ffmpeg", "-nostdin", "-v", "error", "-i", f, "-an", "-vf", "fps=4,scale=160:-2,signalstats,metadata=print:file=-", "-f", "null", "-"], T)
    g = lambda k: [float(x) for x in re.findall(rf"lavfi\.signalstats\.{k}=([\d.]+)", r.stdout)]
    Y, S = g("YAVG"), g("SATAVG")
    n = int(min(24, max(12, round(dur)))); cols = 6
    make_sheet(f, dur, os.path.join(SHEETS, name.lstrip(".") + ".jpg"), n, cols)
    aw, ah = (crop[0], crop[1]) if letterbox else (w, h)
    orient = "vertical" if ah > aw * 1.3 else "horizontal" if aw > ah * 1.3 else "square-ish"
    lm = round(st.mean(Y), 1) if Y else None
    return dict(file=rel, account=rel.split("/")[0], duration=round(dur, 2), width=w, height=h,
                orientation=orient, letterboxed=letterbox, active_area=crop if letterbox else None, codec=v.get("codec_name"),
                fps=round(1 / med, 2), fps_nominal=round(float(Fraction(v["r_frame_rate"])) if v.get("r_frame_rate", "0/0") != "0/0" else 0, 3), vfr=vfr, internal_cuts=cuts, strobe_span=[min(flick), max(flick)] if flick else None,
                brightness=dict(mean_luma=lm, label=None if lm is None else "dark" if lm < 35 else "mid" if lm < 70 else "bright",
                                curve_2hz=[round(y) for y in Y[::2]]),
                saturation=round(st.mean(S), 1) if S else None, sheet=f"_reference/_index_sheets/{name.lstrip('.')}.jpg")

def sheet_only(paths):
    out = os.path.join(LIB, "_inbox/_sheets"); os.makedirs(out, exist_ok=True)
    for rel in paths:
        # any of: _inbox/a.mp4 (relative to library/), library/_inbox/a.mp4 (from the sandbox folder), an absolute path
        f = next((c for c in (rel, os.path.join(LIB, rel), os.path.join(os.path.dirname(LIB), rel)) if os.path.isfile(c)), os.path.join(LIB, rel))
        name = os.path.splitext(os.path.basename(f))[0].lstrip(".")
        if not os.path.isfile(f): print("sheet_failed", name, "no such file:", rel); continue
        try:
            dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], 30).stdout.strip())
            n = int(min(24, max(12, round(dur))))
            ok = make_sheet(f, dur, os.path.join(out, name + ".jpg"), n)
            print("sheet" if ok else "sheet_failed", name)
        except Exception as e: print("sheet_failed", name, e)

def key_for(rel, clips, taken, on_disk):
    """A clip's key: its file name. Another clip already using that name (a different file still on disk) → name (folder),
    so same-named files in different folders never overwrite each other. A key whose file is gone was moved: it follows."""
    base = os.path.splitext(os.path.basename(rel))[0]; folder = os.path.basename(os.path.dirname(rel)) or "library"
    for k in [base] + ["%s (%s)" % (base, folder)] + ["%s (%s %d)" % (base, folder, i) for i in range(2, 99)]:
        if k in taken: continue
        f = (clips.get(k) or {}).get("file")
        if not f or f == rel or f not in on_disk: return k
    raise ValueError("no free key for " + rel)

def size(rel):
    try: return os.path.getsize(os.path.join(LIB, rel))
    except OSError: return 0   # deleted while this runs

def cost(rels):
    """(GB, minutes) to index these files at the measured rate."""
    gb = sum(size(r) for r in rels) / 1e9
    return gb, (len(rels) * EST_FILE + gb * EST_GB) / 60

def took(mins): return "%d min" % max(1, round(mins)) if mins < 90 else "%.1f h" % (mins / 60)

def newest(rels, most):
    """--most <n files | n GB>: the newest files first (the newest shoot is likeliest what they're promoting now; largest first would
    take the long masters and visualizers, the slowest to index and the least ready to cut)."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(gb)?", most.strip().lower())
    if not m: sys.exit("--most takes a number of files (--most 40) or of GB (--most 10GB)")
    def mt(r):
        try: return os.path.getmtime(os.path.join(LIB, r))
        except OSError: return 0
    rels = sorted(rels, key=mt, reverse=True); n = float(m.group(1))
    if not m.group(2): return rels[:int(n)]
    out, gb = [], 0.0
    for r in rels:   # the newest that fit in n GB: a file bigger than what's left is passed over, the older ones still come
        if gb + size(r) / 1e9 <= n: out.append(r); gb += size(r) / 1e9
    return out

def estimate(only, files, by_file, todo):
    """What indexing these folders costs, and folder by folder one level down (to pick the shoot the ask is about). Reads only."""
    for o in only:
        mine = [r for r in files if r.startswith(o)]; left = [r for r in todo if r.startswith(o)]; gb, mins = cost(left)
        print("%s: %d video files, %.1f GB; %d indexed; to index: %d (%.1f GB), about %s" % (
            o.rstrip("/"), len(mine), sum(map(size, mine)) / 1e9, sum(r in by_file for r in mine), len(left), gb, took(mins)))
        by = {}
        for r in left: by.setdefault(r[len(o):].split("/")[0] if "/" in r[len(o):] else "(in the folder itself)", []).append(r)
        if len(by) > 1:
            for g, rs in sorted(by.items(), key=lambda kv: -cost(kv[1])[1]):
                gb, mins = cost(rs); print("  %-44s %4d files %6.1f GB  about %s" % (g[:44], len(rs), gb, took(mins)))
    print("(%g s a file + %g s a GB, measured; slower while a render runs. Own footage over %d GB or %d min in one run stops before starting.)"
          % (EST_FILE, EST_GB, LIMIT_GB, LIMIT_MIN))

def folder(o):
    """A named folder, library-relative ("_external content/tour"; also accepted: library/..., a full path). Must be a source folder's."""
    for c in (os.path.join(LIB, o), os.path.join(os.path.dirname(LIB), o), o):
        if os.path.isdir(c):
            r, cur = [], LIB
            for x in os.path.relpath(os.path.abspath(c), os.path.abspath(LIB)).split(os.sep):   # its own spelling: the drive ignores case, the index doesn't
                x = next((y for y in (os.listdir(cur) if os.path.isdir(cur) else []) if y.lower() == x.lower()), x); r.append(x); cur = os.path.join(cur, x)
            r = "/".join(r).rstrip("/") + "/"
            if any(r.startswith(s.rstrip("/") + "/") for s in SOURCES): return r
            sys.exit("%s isn't inside a footage folder (layout.json source_folders: %s)" % (o, ", ".join(SOURCES)))
    sys.exit("no such folder in library/: %s" % o)

def main():
    if "--sheet" in sys.argv: return sheet_only([a for a in sys.argv[1:] if a != "--sheet"])
    a = sys.argv[1:]; most = None
    if "--most" in a:
        i = a.index("--most"); most = a[i + 1] if i + 1 < len(a) else ""; del a[i:i + 2]
    refresh, yes_all = "--refresh" in a, "--yes-all" in a
    only = [folder(x) for x in a if not x.startswith("--")]   # e.g. "_internal content": just these folders (library-relative)
    if not only: only = [s.rstrip("/") + "/" for s in SOURCES if s != EXTERNAL]   # the person's own footage only when named: ask them first
    idx = json.load(open(INDEX)) if os.path.exists(INDEX) else {"clips": {}}
    clips = idx["clips"]
    files = sorted(os.path.relpath(os.path.join(dp, f), LIB) for s in SOURCES for dp, dn, fn in os.walk(os.path.join(LIB, s))
                   if not any(part.startswith("_sheets") for part in dp.split(os.sep)) for f in fn if f.lower().endswith(VIDEO_EXT))
    names = set(); on_disk = set(files); by_file = {v.get("file"): k for k, v in clips.items()}
    todo = [r for r in files if any(r.startswith(o) for o in only) and (refresh or r not in by_file)]
    own = [r for r in todo if r.startswith(EXTERNAL + "/")]
    pick = newest(own, most) if most is not None else own
    if "--estimate" in a:   # nothing indexed, nothing written
        estimate(only, files, by_file, todo)
        if most is not None:
            gb, mins = cost(pick); print("--most %s would take %d of the %d own files to index: %.1f GB, about %s" % (most, len(pick), len(own), gb, took(mins)))
        return
    if pick and not yes_all:   # a dump of own footage (hundreds of GB) must never grind things to a halt
        gb, mins = cost(pick); whole = EXTERNAL + "/" in only and most is None
        if whole or gb > LIMIT_GB or mins > LIMIT_MIN:
            estimate(only, files, by_file, todo)
            sys.exit("stopped before starting, nothing indexed: %s. Ways on: name one folder (above), or a part: --most 40 (files) or --most 10GB, "
                     "the newest first (run it again for the next part); or, once the person said yes to all of it, --yes-all"
                     % ("that's all of %s" % EXTERNAL if whole else "%d own files, %.1f GB, about %s, is over %d GB or %d min for one run"
                        % (len(pick), gb, took(mins), LIMIT_GB, LIMIT_MIN)))
    later = set(own) - set(pick)   # own files a --most run leaves for the next
    global GENTLE   # one file at a time; each pass gives way while other video work runs (run()); elsewhere than a Mac, nice 10
    if sys.platform == "darwin" and shutil.which("taskpolicy"): GENTLE = True
    elif hasattr(os, "nice"): os.nice(10)
    os.makedirs(SHEETS, exist_ok=True)
    cpath = os.path.join(LIB, "_reference/saves_catalog.csv")
    catalog = {os.path.splitext(os.path.basename(r["file"]))[0]: r for r in csv.DictReader(open(cpath))} if os.path.exists(cpath) else {}
    touched = set(); seen = copy.deepcopy(clips)   # each entry as this run last read it from disk or wrote it there
    FLAGS = ("missing_on_disk", "use_before_removed", "analyze_failed")   # this run's say only: when it clears them (the file is back, the clip reads this time), they stay cleared
    def save():   # written as it goes (after every clip: an own file can take minutes), locked and atomically, so a long run never loses its work.
        # Only this run's entries are written into what's on disk now. A field on disk that differs from what this run last saw there
        # was changed meanwhile (another session retired the clip, wrote its one-liner): the disk's value, or its removal, stays, in
        # this run's copy too; every other field is this run's (its machine fields win over what it read), as before.
        with edit_json(INDEX, {"clips": {}}) as disk:
            for k in touched:
                d, c = disk["clips"].get(k, {}), clips[k]; b = seen.get(k, {}) if k in disk["clips"] else {}
                for f in list(d) + [f for f in b if f not in d]:
                    if f not in d: c.pop(f, None)
                    elif f not in b or d[f] != b[f]: c[f] = d[f]
                disk["clips"][k] = {**c, **{f: v for f, v in d.items() if f not in c and f not in FLAGS}}; seen[k] = copy.deepcopy(disk["clips"][k])
            disk["updated"] = datetime.date.today().isoformat()
    done = 0
    for rel in files:
        if (only and not any(rel.startswith(o) for o in only)) or rel in later: names.add(by_file.get(rel) or key_for(rel, clips, names, on_disk)); continue
        name = by_file.get(rel) or key_for(rel, clips, names, on_disk); names.add(name)
        old = clips.get(name)
        if old and not refresh and old.get("file") == rel: continue
        try: m = machine(rel, old, name)
        except Exception as e:
            print("analyze_failed", rel, e); m = dict(file=rel, analyze_failed=str(e))
        keep = {k: v for k, v in (old or {}).items() if k not in m and k not in ("missing_on_disk", "needs_review", "analyze_failed")}   # everything written by hand (line, fits, source_type, ...), not just HAND
        clips[name] = {**m, **keep}
        row = catalog.get(name)
        if row: clips[name]["catalog"] = dict(url=row.get("url"), views=row.get("views"), permission=row.get("permission") or None, sound=row.get("sound"))
        if not keep and not rel.startswith(EXTERNAL + "/"): clips[name]["needs_review"] = True   # the person's own footage: no review unless they ask
        print("indexed", name, flush=True); done += 1; touched.add(name)
        save()
    for name in list(clips):
        c = clips[name]
        if name not in names and c.get("use") != "pasture":
            if not c.get("missing_on_disk"): c["use_before_removed"] = c.get("use")   # what it was, in case the file comes back
            c["missing_on_disk"] = True; c["use"] = "removed"; touched.add(name)  # they deleted it: never use or re-download
        elif name in names and "use_before_removed" in c:   # it's back (moved out and back, a folder renamed mid-run): as it was
            c["use"] = c.pop("use_before_removed"); c.pop("missing_on_disk", None); touched.add(name)
    save()
    if later: print("%d own file(s) left for the next part: the same command again takes the next %s" % (len(later), most))
    indexed = {c.get("file") for c in clips.values()}   # this run's included
    waiting = [r for r in files if r.startswith(EXTERNAL + "/") and r not in indexed]
    named = [o for o in only if o.startswith(EXTERNAL + "/")]   # own folders named in this run: the person was asked about those
    asked = set(open(ASKED, encoding="utf-8").read().splitlines()) if waiting and os.path.exists(ASKED) else set()
    new = [r for r in waiting if r not in asked]; fresh = [r for r in new if not any(r.startswith(o) for o in named)]
    if fresh:   # only what arrived since they were last asked; the rest they already said no (or not yet) to
        gb, mins = cost(fresh)
        print("%d new file(s) in %s since they were last asked (%.1f GB, about %s to index; %d waiting in all) not indexed: ask the person "
              "before indexing them (which folder the ask is about: --estimate \"%s\" lists them with what each costs)" % (len(fresh), EXTERNAL, gb, took(mins), len(waiting), EXTERNAL))
    if new:
        with locked(ASKED), open(ASKED, "a", encoding="utf-8") as f: f.write("".join(r + "\n" for r in new))

if __name__ == "__main__": main()
