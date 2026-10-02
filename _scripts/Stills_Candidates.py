#!/usr/bin/env python3
"""Stills for collages: usable still moments in a moving video are RARE, about 3-4 max in 15s,
and many clips have none. So this script no longer samples hooks and best ranges. It only extracts the moments
Claude picked by eye from the contact sheet and wrote into the clip index as `stills: [{t, what}]`
(0-4 per clip; an empty list means the clip has no usable still). For each picked moment it compares every frame
within +-0.25s (Laplacian variance at 480p) and saves only the sharpest one, skipping frames near a source cut or in
an 'avoid' range. Clips with no `stills` field yet are skipped (pick them first). Clips already in the index are
not redone. Output: _stills/candidates/<clip>_<t>.jpg (a letterboxed clip's: <clip>_<t>_aa.jpg, the picture without its bars)
+ _stills/candidates/index.json.
--redo-picked: for a clip that now has `stills` but still carries old-style candidates, move those frames to
_stills/candidates/_culled/ and extract only the picks.
Resumable. Usage: python3 _scripts/Stills_Candidates.py [--include-pasture] [--redo-picked] [--max-seconds 150]"""
import os, sys, json, subprocess, glob, shutil, time
import cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from sandbox_paths import LIB, edit_json  # library/: footage, indexes, lab
from lab_still import picture  # a letterboxed clip's still keeps only its picture (the render's crop), saved as <clip>_<t>_aa.jpg
OUT = os.path.join(LIB, "_stills", "candidates"); os.makedirs(OUT, exist_ok=True)
IX = os.path.join(OUT, "index.json"); ix = json.load(open(IX)) if os.path.exists(IX) else {}
budget = float(sys.argv[sys.argv.index("--max-seconds")+1]) if "--max-seconds" in sys.argv else 150
REDO = "--redo-picked" in sys.argv
t0 = time.time(); TMP = os.path.expanduser("~/cand_tmp")
_ip = os.path.join(LIB, "_reference", "clip_index.json")
if not os.path.exists(_ip): sys.exit("no clip index yet: index some clips first (say \"check\")")
cl = json.load(open(_ip))["clips"]
for k, c in cl.items():
    if k in ix and REDO and "stills" in c and not all(r.get("picked") for r in ix[k]):
        # old-style candidates (sampled from hooks/best ranges): move their frames to _culled and redo from the picks
        os.makedirs(os.path.join(OUT, "_culled"), exist_ok=True)
        for r in ix.pop(k):
            f = os.path.join(LIB, r["file"])
            if os.path.exists(f): subprocess.run(["mv", "-n", f, os.path.join(OUT, "_culled", "")])
        with edit_json(IX, indent=1, ensure_ascii=True) as d: d.pop(k, None)   # only this clip's entry, locked and atomic (as before: ascii, indent 1)
    # very_dark / ffmpeg_hangs_on_mac_vm clips are no longer skipped: the picks were made by eye, and ffmpeg runs with a timeout
    if k in ix or c.get("use") in (("ask", "removed") if "--include-pasture" in sys.argv else ("pasture", "ask", "removed")): continue
    if time.time() - t0 > budget: print("budget used; run again"); break
    src = os.path.join(LIB, c["file"]); dur = c["duration"]
    if "stills" not in c: print("no stills picked yet:", k); continue
    targets = [(x["t"], x.get("what", "")) for x in c["stills"]][:4]
    cuts = c.get("internal_cuts") or []; avoid = [a for a in (c.get("avoid") or []) if isinstance(a, dict)]
    res = []
    for t, what in targets:
        # stay inside the shot the pick belongs to (never slide across a cut into the neighbouring shot)
        lo = max([x for x in cuts if x <= t] or [0]); hi = min([x for x in cuts if x > t] or [dur])
        a = max(0, t - 0.25, lo)
        # read full-res frames with their REAL timestamps (frame-count / fps maths is wrong in VFR files); the winner is saved as-is
        cap = cv2.VideoCapture(src); cap.set(cv2.CAP_PROP_POS_MSEC, max(0, a - 1.0) * 1000); best = None  # seek early, read forward (seeks overshoot in VFR files)
        while True:
            ok, fr = cap.read()
            if not ok: break
            tt = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if tt < a: continue
            if tt > min(a + 0.5, hi): break
            if tt > dur - 0.1 or tt < lo or any(abs(tt - x) < 0.12 for x in cuts) or any(v["in"] <= tt <= v["out"] for v in avoid): continue
            fr, cropped = picture(fr, c)
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY); g = cv2.resize(g, (max(1, round(g.shape[1] * 480 / g.shape[0])), 480), interpolation=cv2.INTER_AREA)
            s = float(cv2.Laplacian(g, cv2.CV_64F).var())
            rows = g.mean(axis=1) > 8  # brightness of the picture only, not letterbox bars
            l = float(g[rows].mean()) if rows.any() else 0.0
            if l < 18: continue  # a black frame, not a dark-but-real night moment
            if not best or s > best[1]: best = (tt, s, l, fr.copy(), cropped)
        if not best: continue
        tt, s, l, bf, cropped = best
        if any(abs(tt - r["t"]) < 0.3 for r in res): continue
        fn = "%s_%05.2f%s.jpg" % (k.lstrip("."), tt, "_aa" if cropped else ""); p = os.path.join(OUT, fn)
        cv2.imwrite(p, bf, [cv2.IMWRITE_JPEG_QUALITY, 95])
        res.append({"file": "_stills/candidates/" + fn, "t": round(tt, 3), "what": what, "sharpness": round(s, 1), "luma": round(l, 1), "picked": True})
    ix[k] = res
    with edit_json(IX, indent=1, ensure_ascii=True) as d: d[k] = res   # this clip's entry only, into the index as it is now
    print(k, len(res))
shutil.rmtree(TMP, ignore_errors=True)
print("clips", len(ix), "candidates", sum(len(v) for v in ix.values()))
