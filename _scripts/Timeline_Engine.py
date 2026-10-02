#!/usr/bin/env python
"""
Timeline Engine - builds a Resolve timeline from an edit recipe (JSON) that Claude writes.
Don't run this file directly: each edit Claude makes shows up under
Workspace > Scripts > Utility > sandbox > <account> > <With song|No song> > <edit name> (menu files live in Fusion/Scripts/Utility/<account>/<content type>/), and that entry runs this engine with its recipe.

Recipe format (library/_recipes/*.json), all times in seconds:
  name, account_bin, fps, width, height, window_start,
  audio:   {file, start, end}                       # song, placed on audio track 1 at 0
  shots:   [{file, src_in, rec, dur, fill, fill_area, zoom_extra, cx, cy, rotate, label}]   # V1
           rotate: degrees for sideways-shot footage (90 = turn a clip filmed sideways upright; if it lands upside down, flip the sign in the Inspector)
           fill_area: [w, h] of the picture inside letterbox bars (source pixels): fill the frame with that, not the bars
           file can also be a still image (.jpg/.png) for photo flips: it is held for dur seconds
  overlays: [{file, rec, dur}]                      # V2, placed as they are (e.g. the caption/lyrics layer with alpha)
  markers: [{sec, color, name, note}]               # relative to the edit
  markers_file: "<song's markers, relative to library/>"   # add that song's markers inside the window (shifted)
                                                    # older recipes: song_markers = the first song's (songs.py)
Song markers are read through _scripts/song_markers.py; each song's are set in Resolve from its own menu entry (songs.py).
_scripts/Resolve_Export.py writes these recipes (and their menu entries) from any finished lab/batch piece.
"""
import os, json, math, sys
if "RECIPE" not in globals() and {"-h", "--help"} & set((getattr(sys, "argv", None) or [])[1:]):   # from the shell: the usage and nothing else, as every script
    print(__doc__.strip()); sys.exit(0)                                                           # (a menu entry in Resolve always names its RECIPE, so this never stops one)

def _sandbox_lib():
    """library/ inside the sandbox folder named in ~/.sandbox_root (written by setup.sh). Resolve runs this file outside the repo, so it can't look next to itself."""
    p = os.path.expanduser("~/.sandbox_root")
    root = open(p, encoding="utf-8").read().strip() if os.path.exists(p) else os.path.expanduser("~/Desktop/sandbox")
    return os.path.join(root, "library")


LIB = _sandbox_lib()


def _song_markers(rc):
    """The song's markers, through _scripts/song_markers.py (the one reader everything uses): the recipe's "markers_file", or
    (older recipes: song_markers) the first song's (songs.py)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(LIB), "_scripts"))
    import song_markers, songs
    if rc.get("markers_file"): return song_markers.load_file(os.path.join(LIB, rc["markers_file"]))
    e = songs.of_recipe({}, lib=LIB)
    return song_markers.load_file(e["markers_file"]) if e else []


def _resolve():
    g = globals()
    if "resolve" in g:
        return g["resolve"]
    try:
        return bmd.scriptapp("Resolve")  # noqa: F821
    except NameError:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")


def _num(x, d):
    try:
        return float(str(x).split()[0])
    except Exception:
        return d


def _all_clips(folder):
    out = list(folder.GetClipList() or [])
    for sub in folder.GetSubFolderList() or []:
        out += _all_clips(sub)
    return out


def _bin(mp, parent, name):
    for f in parent.GetSubFolderList() or []:
        if f.GetName() == name:
            return f
    return mp.AddSubFolder(parent, name)


def _latest_recipe():
    d = os.path.join(LIB, "_recipes")
    js = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")] if os.path.isdir(d) else []
    return max(js, key=os.path.getmtime) if js else None


def run(recipe_path):
    rc = json.load(open(recipe_path, encoding="utf-8"))
    r = _resolve()
    project = r.GetProjectManager().GetCurrentProject()
    if not project:
        print("!! Open a project first."); return
    mp = project.GetMediaPool(); root = mp.GetRootFolder()
    abin = _bin(mp, root, rc.get("account_bin", "sandbox Edits"))
    try:
        import sys; sys.path.insert(0, os.path.join(os.path.dirname(LIB), "_scripts")); import sandbox_paths
        lay = sandbox_paths.layout(); content = (lay.get("resolve_import") or lay.get("source_folders") or []) + ["_lab/source", "_pasture"]
    except Exception:
        content = ["_internal content", "_external content", "_lab/source", "_pasture"]

    # media: find by file path anywhere in the pool, import if missing
    by_path = {c.GetClipProperty("File Path"): c for c in _all_clips(root) if c.GetClipProperty("File Path")}
    def media(rel):
        p = os.path.join(LIB, rel)
        if p in by_path:
            return by_path[p]
        if not os.path.exists(p):
            print("!! Missing file: %s" % p); return None
        # footage goes in the bin that mirrors its library folder (like Import_Library), a song in "Songs", and the piece's own
        # files (caption layer, timed stills) in the account's bin with its timelines
        rel = os.path.relpath(os.path.dirname(p), LIB); b = abin
        if not rel.startswith("..") and any(rel == t or rel.startswith(t + os.sep) for t in content):
            b = root
            for part in rel.split(os.sep): b = _bin(mp, b, part)
        elif rel == "_audio" or rel.startswith("_audio" + os.sep):
            b = _bin(mp, root, "Songs")
        mp.SetCurrentFolder(b)
        got = mp.ImportMedia([p]) or []
        if got:
            by_path[p] = got[0]
            return got[0]
        print("!! Couldn't import %s" % p); return None

    # timeline
    names = {project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)}
    n = 1
    while "%s v%d" % (rc["name"], n) in names:
        n += 1
    mp.SetCurrentFolder(abin)
    tl = mp.CreateEmptyTimeline("%s v%d" % (rc["name"], n))
    if not tl:
        print("!! Couldn't create the timeline."); return
    project.SetCurrentTimeline(tl)
    tl.SetSetting("useCustomSettings", "1")
    tl.SetSetting("timelineFrameRate", str(rc.get("fps", 30)))
    tl.SetSetting("timelineResolutionWidth", str(rc.get("width", 1080)))
    tl.SetSetting("timelineResolutionHeight", str(rc.get("height", 1920)))
    fps = _num(tl.GetSetting("timelineFrameRate"), 30.0)
    t0 = tl.GetStartFrame()
    W, H = float(rc.get("width", 1080)), float(rc.get("height", 1920))
    fit_mode = project.GetSetting("timelineInputResMismatchBehavior") or "scaleToFit"

    # song
    a = rc.get("audio")
    if a:
        m = media(a["file"])
        if m:
            af = _num(m.GetClipProperty("FPS"), fps)
            s = int(round(a["start"] * af)); e = int(round(a["end"] * af))
            mp.AppendToTimeline([{"mediaPoolItem": m, "startFrame": s, "endFrame": e - 1,
                                  "mediaType": 2, "trackIndex": 1, "recordFrame": t0}])

    # shots (V1), then overlays (V2: the caption/lyrics layer, placed as it is)
    state = {"end_fix": None}
    def place(sh, track):
        m = media(sh["file"])
        if not m:
            return False
        cf = _num(m.GetClipProperty("FPS"), fps)
        frames = int(_num(m.GetClipProperty("Frames"), 0))
        rec = t0 + int(round(sh["rec"] * fps))
        rec_end = t0 + int(round((sh["rec"] + sh["dur"]) * fps))
        want = rec_end - rec
        if want < 1:
            return False
        need = max(1, int(round(want * cf / fps)))
        s = min(int(round(sh.get("src_in", 0) * cf)), max(0, frames - need))
        fix = -1 if state["end_fix"] is None else state["end_fix"]
        items = mp.AppendToTimeline([{"mediaPoolItem": m, "startFrame": s, "endFrame": s + need + fix,
                                      "mediaType": 1, "trackIndex": track, "recordFrame": rec}])
        it = items[0] if items else None
        if it and state["end_fix"] is None:          # learn if endFrame is inclusive on this Resolve
            state["end_fix"] = -1
            if it.GetDuration() < want:
                state["end_fix"] = 0
                tl.DeleteClips([it])
                items = mp.AppendToTimeline([{"mediaPoolItem": m, "startFrame": s, "endFrame": s + need,
                                              "mediaType": 1, "trackIndex": track, "recordFrame": rec}])
                it = items[0] if items else None
        if not it:
            print("!! Couldn't place %s at %.2fs" % (sh.get("label", sh["file"]), sh["rec"])); return False
        if track > 1:
            return True
        # framing: zoom to fill the frame (or the picture inside letterbox bars), extra punch-in, keep (cx, cy) centered
        res = (m.GetClipProperty("Resolution") or "").lower().split("x")
        rot = float(sh.get("rotate", 0) or 0)
        if rot:
            it.SetProperty("RotationAngle", rot)
        if len(res) == 2:
            cw, ch = _num(res[0], W), _num(res[1], H)
            # Resolve sizes the clip to the timeline BEFORE rotating it, so the input scale comes from the unrotated size
            sc = {"scaleToFit": min(W / cw, H / ch), "scaleToCrop": max(W / cw, H / ch)}.get(fit_mode, 1.0 if fit_mode == "centerCrop" else min(W / cw, H / ch))
            if abs(rot) % 180 == 90:
                cw, ch = ch, cw
            aw, ah = sh.get("fill_area") or (cw, ch)
            z = max(W / (aw * sc), H / (ah * sc)) if sh.get("fill", True) else 1.0
            z *= sh.get("zoom_extra", 1.0)
            if abs(z - 1) > 0.001:
                it.SetProperty("ZoomX", z); it.SetProperty("ZoomY", z)
            dw, dh = cw * sc * z, ch * sc * z
            cx, cy = sh.get("cx", 0.5), sh.get("cy", 0.5)
            px = max(-(dw - W) / 2, min((dw - W) / 2, (0.5 - cx) * dw))
            py = max(-(dh - H) / 2, min((dh - H) / 2, (cy - 0.5) * dh))
            if abs(px) > 0.5: it.SetProperty("Pan", px)
            if abs(py) > 0.5: it.SetProperty("Tilt", py)
        return True

    placed = sum(1 for sh in rc["shots"] if place(sh, 1))
    if rc.get("overlays"):
        while tl.GetTrackCount("video") < 2:
            if not tl.AddTrack("video"): break
        for ov in rc["overlays"]:
            place(dict(ov, fill=False), 2)

    # markers
    w0 = rc.get("window_start", 0.0); dur = rc.get("audio", {}).get("end", 0) - rc.get("audio", {}).get("start", 0)
    if rc.get("markers_file") or rc.get("song_markers"):
        for mk in _song_markers(rc):
            if w0 <= mk["sec"] < w0 + dur:
                tl.AddMarker(int(round((mk["sec"] - w0) * fps)), mk.get("color") or "Blue", mk.get("name") or "", mk.get("note") or "",
                             max(1, int(round((mk.get("dur_sec") or 0) * fps))), mk.get("custom") or "")
    for mk in rc.get("markers", []):   # Resolve keeps one marker a frame: a taken frame moves it to the next free one (several notes share 0:00)
        f = int(round(mk["sec"] * fps))
        if not any(tl.AddMarker(f + d, mk.get("color", "Cream"), mk.get("name", ""), mk.get("note", ""), 1) for d in range(int(fps))):
            print("!! Couldn't place the marker '%s' near %.2fs" % (mk.get("name", ""), mk["sec"]))

    print("%s v%d: %d/%d shots placed." % (rc["name"], n, placed, len(rc["shots"])))
    if rc.get("notes"):
        print(rc["notes"])


_rp = globals().get("RECIPE") or _latest_recipe()
if _rp:
    run(_rp)
else:
    print("!! No recipe found in library/_recipes.")
