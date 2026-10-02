#!/usr/bin/env python3
"""Perfect-children study, the measured half: a reviewed round's perfect edits against the rest, from the renders.
  python3 _scripts/studies/perfect_measure.py <round folder, e.g. R06_2026-09-27>
Runs after every round's notes are logged.
Reads the round's review.json (decisions) and recipes (shot and still lengths), finds each render wherever the review page
moved it (perfect children, keep, the round folder, _to_delete/lab_deleted), samples 3 frames per shot (25/50/75 %), and prints
one markdown table per account type (video, collage): shot length, evenness, saturation, brightness, the light jump from shot
to shot, motion inside a shot, sources, song. The read of what the perfects share is written by hand into
library/_lab/perfect children/_study/STUDY.md (numbers alone don't say why)."""
import glob, json, os, statistics as st, subprocess, sys
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sandbox_paths import LIB, merge_own, round_files, round_parts  # noqa: E402
import songs  # noqa: E402  (a recipe's song block: "song")
import Lab_Render  # noqa: E402  (how long each still of a collage is on: the render's own count)

if len(sys.argv) < 2: sys.exit(__doc__)
PARTS = round_parts(sys.argv[1])   # the round's folders: one, or several when its pieces are sorted between folders
if not PARTS: sys.exit("No such round: %s" % sys.argv[1])
RND = PARTS[0]
review = merge_own(PARTS, {d: json.load(open(os.path.join(d, "review.json"))) for d in PARTS if os.path.exists(os.path.join(d, "review.json"))})   # a split round: each piece's own folder wins
if not review: sys.exit("%s isn't reviewed yet (no review.json): run this after its notes are in." % os.path.basename(RND))
PLACES = PARTS + [os.path.join(LIB, "_lab/perfect children"), os.path.join(LIB, "_lab/keep"),
          os.path.join(LIB, "_to_delete/lab_deleted", os.path.basename(RND))]

def render(i):
    for d in PLACES:   # the id whole, then ".mp4" or a space and the title, as the review page and shot_verify find it: a piece whose id only starts the same (a "b" version) is another piece
        m = sorted(glob.glob(os.path.join(d, glob.escape(i) + ".mp4")) + glob.glob(os.path.join(d, glob.escape(i) + " *.mp4")))
        if m: return m[0]

def frame(f, t):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", f, "-frames:v", "1", "-vf", "scale=270:-2",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    import io
    return Image.open(io.BytesIO(raw)).convert("RGB") if raw else None

def measure(i, r):
    f = render(i)
    if not f: return None
    if r.get("kind") == "video": durs = [s["dur"] for s in r["shots"]]; srcs = len({s["clip"] for s in r["shots"]})
    else: durs = [n / Lab_Render.FPS for n in Lab_Render.still_frames(r)]; srcs = len(r.get("stills") or [])   # with "pixels" the first still is on for less: sampled where the render put each
    t = 0; sat = []; bri = []; mot = []; shot_bri = []
    for d in durs:
        fr = [frame(f, t + d * k) for k in (0.25, 0.5, 0.75)]; t += d
        fr = [x for x in fr if x]
        if not fr: continue
        hsv = [np.asarray(x.convert("HSV"), dtype=float) for x in fr]
        sat.append(np.mean([h[..., 1].mean() for h in hsv])); b = np.mean([h[..., 2].mean() for h in hsv]); bri.append(b); shot_bri.append(b)
        g = [np.asarray(x.convert("L"), dtype=float) for x in fr]
        mot.append(np.mean([abs(g[k + 1] - g[k]).mean() for k in range(len(g) - 1)]) if len(g) > 1 else 0)
    jump = st.mean(abs(shot_bri[k + 1] - shot_bri[k]) for k in range(len(shot_bri) - 1)) if len(shot_bri) > 1 else 0
    return dict(len=st.mean(durs), even=(st.pstdev(durs) / st.mean(durs)) if durs else 0, sat=st.mean(sat), bri=st.mean(bri),
                jump=jump, mot=st.mean(mot), srcs=srcs, song=bool(songs.block(r)))

from concurrent.futures import ThreadPoolExecutor
todo = []
for p in round_files(os.path.basename(RND)):
    r = json.load(open(p)); i = r.get("id") or os.path.basename(p)[:-5]
    if i in review: todo.append((i, r))
with ThreadPoolExecutor(max(2, (os.cpu_count() or 4) // 2)) as ex:   # one render per worker: ffmpeg does the heavy part
    rows = {i: (review[i]["decision"], r.get("kind"), m) for (i, r), m in zip(todo, ex.map(lambda t: measure(*t), todo)) if m}

KEYS = [("len", "shot length avg (s)", "%.2f"), ("even", "evenness (lower = steadier)", "%.2f"), ("sat", "saturation", "%.0f"),
        ("bri", "brightness", "%.0f"), ("jump", "light jump shot to shot", "±%.0f"), ("mot", "motion inside a shot", "%.0f"),
        ("srcs", "sources", "%.1f")]
for kind in ("video", "collage"):
    grp = {g: [m for d, k, m in rows.values() if (k == "video") == (kind == "video") and (d == "perfect") == (g == "perfect")]
           for g in ("perfect", "others")}
    if not any(grp.values()): continue
    print("\n%s: perfect (%d) vs others (%d)" % (kind, len(grp["perfect"]), len(grp["others"])))
    print("| | perfect | others |\n|---|---|---|")
    for k, name, fmt in KEYS:
        print("| %s | %s | %s |" % (name, *[(fmt % st.mean(m[k] for m in grp[g])) if grp[g] else "-" for g in ("perfect", "others")]))
    print("| with the song | %s | %s |" % tuple("%d/%d" % (sum(m["song"] for m in grp[g]), len(grp[g])) for g in ("perfect", "others")))
missing = [i for i in review if i not in rows]
if missing: print("\nno render found for: " + ", ".join(missing))
