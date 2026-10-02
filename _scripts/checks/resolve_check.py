#!/usr/bin/env python3
"""Checks that a Resolve timeline made from a piece matches its render, without Resolve (mock_resolve.py records what the
engine asks for).
  python3 _scripts/checks/resolve_check.py           the cases below + every Resolve recipe in library/_recipes
  python3 _scripts/checks/resolve_check.py --quick   the cases only (setup's smoke test runs this)
Cases: made-up clips (plain, letterboxed, sideways, both; 16:9 and 9:16 frames; each also framed off-centre by a shot's
"center"), a caption layer, a sped-up shot, a punch on an off-centre shot, a collage and "pixels" on a video (the thread ripper
effect, the style when none is named) and on a collage (the pixel sort) go through the real Resolve_Export.py
and engine. For each shot, the frame Resolve would show is rebuilt from the engine's settings
and compared with the render's own frame. Resolve's rules, as modelled here: a clip is fitted to the timeline BEFORE it's
turned, a positive rotation turns it counter-clockwise, Pan moves it right, Tilt moves it up. If real Resolve ever disagrees,
fix the rule here and in the engine together.
New song: a made-up song goes through songs.py add, its Resolve menu entry (run, add markers, run again) and the markers check;
a made-up lyric layer of it (white on green, shorter than the edit) is laid by name and read back from the text layer.
Recipes: each builds on the stand-in with every shot placed (a missing source file is reported, not failed: clips get deleted).
Drift: forecast.py's own copies of CARRIED's effects and notes lists (Resolve_Export.EFFECTS, NOTES, SHOT_EFFECTS, SHOT_NOTES) must match.
Exit 1 on any failure. export_public.py runs it before building the public version."""
import contextlib, glob, io, json, math, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, SCRIPTS)
import numpy as np, cv2  # noqa: E402
import mock_resolve  # noqa: E402
import Lab_Render as LR  # noqa: E402
import Resolve_Export as RX  # noqa: E402
from sandbox_paths import LIB  # noqa: E402

ENGINE = os.path.join(SCRIPTS, RX.ENGINE)
FAIL = []


def ff(*a): subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)


def frame(src, t, vf=None):
    cmd = ["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", src] + (["-vf", vf] if vf else []) + ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    return cv2.imdecode(np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.uint8), 1)


def absolute(rc_path):
    """Recipe paths are relative to library/; make them absolute so the engine finds the test files wherever it thinks library/ is."""
    rc = json.load(open(rc_path))
    for k in ("shots", "overlays"):
        for s in rc.get(k) or []: s["file"] = os.path.normpath(os.path.join(LIB, s["file"]))
    if rc.get("audio"): rc["audio"]["file"] = os.path.normpath(os.path.join(LIB, rc["audio"]["file"]))
    if rc.get("markers_file"): rc["markers_file"] = os.path.normpath(os.path.join(LIB, rc["markers_file"]))
    json.dump(rc, open(rc_path, "w")); return rc


def resolve_frame(f, w, h, W, H, props):
    """What Resolve shows for a source frame with these settings (the rules in the docstring)."""
    sc = min(W / w, H / h) * props.get("ZoomX", 1.0); th = math.radians(props.get("RotationAngle", 0.0)); co, si = math.cos(th), math.sin(th)
    M = np.array([[sc * co, sc * si, 0], [-sc * si, sc * co, 0]], float)
    M[:, 2] = np.array([W / 2 + props.get("Pan", 0.0), H / 2 - props.get("Tilt", 0.0)]) - M[:, :2] @ np.array([w / 2, h / 2])
    return cv2.warpAffine(f, M, (W, H), flags=cv2.INTER_LINEAR)


def build(r, idx, tmp):
    rp = os.path.join(tmp, r["id"] + "_recipe.json"); json.dump(r, open(rp, "w"))
    LR.load_index = lambda: idx
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): rc, erp, _, _ = RX.convert(rp, tmp)   # its warnings count ("isn't carried")
    absolute(erp)
    calls, tl, out = mock_resolve.run_engine(erp, ENGINE)
    return rc, calls, tl, buf.getvalue() + out


def cases(tmp):
    specs = {  # name: (source w, h, letterbox crop or None, sideways, frame, the shot's "center" or None)
        "plain 16:9 clip in 9:16": (1920, 1080, None, False, "9:16", None),
        "sideways clip": (720, 1280, None, True, "16:9", None),
        "letterboxed clip": (1280, 720, (1100, 460, 40, 170), False, "9:16", None),
        "letterboxed + sideways": (720, 1280, (640, 900, 20, 250), True, "16:9", None),
        "letterboxed + sideways, 9:16": (1080, 1920, (1000, 1200, 60, 500), True, "9:16", None),
        "off-centre: plain clip": (1920, 1080, None, False, "9:16", [0.22, 0.5]),
        "off-centre: 4:3 clip in 16:9, up": (960, 720, None, False, "16:9", [0.5, 0.2]),
        "off-centre: letterboxed clip": (1280, 720, (1100, 460, 40, 170), False, "9:16", [0.83, 0.5]),
        "off-centre: sideways clip": (720, 1280, None, True, "9:16", [0.3, 0.5]),
        "off-centre: letterboxed + sideways": (1080, 1920, (1000, 1200, 60, 500), True, "9:16", [0.7, 0.4]),
        "off-centre past the edge: held in the picture": (1920, 1080, None, False, "9:16", [0.02, 0.5]),
    }
    for name, (w, h, crop, side, fmt, at) in specs.items():
        src = os.path.join(tmp, "c%d.mp4" % len(os.listdir(tmp)))
        if crop:
            aw, ah, ax, ay = crop
            ff("-f", "lavfi", "-i", "color=black:s=%dx%d:d=1:r=30" % (w, h), "-f", "lavfi", "-i", "testsrc2=s=%dx%d:d=1:r=30" % (aw, ah),
               "-filter_complex", "[1:v]scale=%d:%d[p];[0:v][p]overlay=%d:%d" % (aw, ah, ax, ay), "-pix_fmt", "yuv420p", src)
        else:
            ff("-f", "lavfi", "-i", "testsrc2=s=%dx%d:d=1:r=30" % (w, h), "-pix_fmt", "yuv420p", src)
        c = dict(file=src, width=w, height=h, letterboxed=bool(crop), active_area=list(crop) if crop else None, flags=["sideways_footage"] if side else [])
        shot = dict({"clip": "x", "in": 0.4, "dur": 0.3}, **({"center": at} if at else {}))
        r = dict(id="check", account="", kind="video", format=fmt, song=None, caption="", shots=[shot])
        rc, calls, tl, out = build(r, {"x": c}, tmp)
        props = {k: v for (t, _, k, v) in [x for x in calls if x[0] == "prop"]}
        W, H = LR.size(fmt); fr = LR.framing(c, shot, W, H, 0.3)
        ref = frame(src, 0.5, LR.vf_for(c, W, H, 1.0, fr=fr)); sim = resolve_frame(frame(src, 0.5), w, h, W, H, props)
        psnr = cv2.PSNR(ref, sim); bars = float((sim.max(axis=2) < 8).mean())
        ok = psnr > 24 and bars < 0.01 and "!!" not in out and (not at or fr["fill"] is not None)   # off-centre really is (not quietly the middle)
        print("  %s  %-44s %.1f dB%s%s" % ("ok " if ok else "!! ", name, psnr, ", %.0f%% bars" % (bars * 100) if bars >= 0.01 else "",
                                         ", frame's middle at %.3f, %.3f of the picture" % fr["center"] if fr["center"] else ""))
        if not ok: FAIL.append(name)

    # a caption (the ProRes 4444 layer on V2), a sped-up shot (a marker saying what the render did), exact shot lengths
    src = os.path.join(tmp, "plain.mp4"); ff("-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=3:r=30", "-pix_fmt", "yuv420p", src)
    r = dict(id="check2", account="", kind="video", format="9:16", song=None, caption="check",
             shots=[{"clip": "x", "in": 0.2, "dur": 0.55}, {"clip": "x", "in": 1.0, "dur": 1.0, "speed": 1.5}])
    rc, calls, tl, out = build(r, {"x": dict(file=src, width=1080, height=1920)}, tmp)
    v2 = [c for c in calls if c[0] == "append" and c[1] == "V" and c[2] == 2]
    lens = [c[5] for c in calls if c[0] == "append" and c[1] == "V" and c[2] == 1]
    alpha = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt", "-of", "csv=p=0",
                            os.path.join(tmp, "check2_text.mov")], capture_output=True, text=True).stdout.strip()
    checks = [("caption layer on V2, with transparency", bool(v2) and "a" in alpha.replace("yuv", "")),
              ("shot lengths = the render's frames", lens == [int(round(s["dur"] * 30)) for s in r["shots"]]),
              ("speed change marked with what the render did", any("speed 1.5x" in m[2] and "1.00-2.50s" in m[3] for m in tl.markers))]
    # effects: each one marked with what the render did
    r = dict(r, id="check2fx", grade="warm", grain=6, shots=[dict(r["shots"][0], flash=3), dict(r["shots"][1], punch=1.15)])
    rc, calls, tl, out = build(r, {"x": dict(file=src, width=1080, height=1920)}, tmp)
    names = [m[2] for m in tl.markers]
    checks.append(("effects marked (flash, punch, grade, grain), none unaccounted",
                   all(any(n.startswith(k) for n in names) for k in ("flash 3", "punch 1.15", "grade: warm", "grain 6")) and "isn't carried" not in out))
    wide = os.path.join(tmp, "wide.mp4"); ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=2:r=30", "-pix_fmt", "yuv420p", wide)
    rc, calls, tl, out = build(dict(r, id="check2z", grade=None, grain=None, shots=[{"clip": "w", "in": 0.2, "dur": 0.6, "punch": 1.2}]), {"w": dict(file=wide, width=1920, height=1080)}, tmp)
    z = next((c[3] for c in calls if c[0] == "prop" and c[2] == "ZoomX"), 1.0); note = next((m[3] for m in tl.markers if m[2].startswith("punch")), "")
    checks.append(("punch marker keyframes the shot's own zoom (%.3f) x the punch" % z, "keyframe %.3f at the cut and %.3f" % (z * 1.2, z) in note))
    rc, calls, tl, out = build(dict(r, id="check2p", grade=None, grain=None, shots=[{"clip": "w", "in": 0.2, "dur": 0.6, "punch": 1.2, "center": [0.3, 0.5]}]),
                               {"w": dict(file=wide, width=1920, height=1080)}, tmp)   # off-centre: the render zooms round its frame's middle, so Pan scales with the zoom
    pan = next((c[3] for c in calls if c[0] == "prop" and c[2] == "Pan"), 0.0); note = next((m[3] for m in tl.markers if m[2].startswith("punch")), "")
    pp = {k: v for (t, _, k, v) in [x for x in calls if x[0] == "prop"]}; s_ = {"clip": "w", "in": 0.2, "dur": 0.6, "punch": 1.2, "center": [0.3, 0.5]}
    ref = frame(wide, 0.2, LR.vf_for(dict(file=wide, width=1920, height=1080), 1080, 1920, 1.0, s_, {}, 0.6))   # the render's first frame: zoomed 1.2 times
    sim = resolve_frame(frame(wide, 0.2), 1920, 1080, 1080, 1920, dict(pp, ZoomX=pp.get("ZoomX", 1.0) * 1.2, Pan=pan * 1.2))   # Resolve at the marker's first keyframe
    checks.append(("punch on an off-centre shot keyframes its Pan (%.1f) x the punch too, and that frame matches the render's (%.1f dB)" % (pan, cv2.PSNR(ref, sim)),
                   abs(pan) > 1 and "Pan %.1f and Tilt 0.0 at the zoomed end, %.1f" % (pan * 1.2, pan) in note and "isn't carried" not in out and cv2.PSNR(ref, sim) > 24))
    # a collage: the stills as one timed clip
    stills = []
    for i in range(3):
        p = os.path.join(tmp, "s%d.jpg" % i); ff("-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=1", "-frames:v", "1", p); stills.append({"file": p, "rotate": False})
    rc, calls, tl, out = build(dict(id="check3", account="", kind="collage", format="9:16", song=None, caption="", stills=stills, sec_per_still=0.5,
                                    fx={"pulse": 0.01}, sparkles={"style": "dust"}), {}, tmp)
    placed = [c for c in calls if c[0] == "append" and c[1] == "V" and c[2] == 1]
    checks.append(("collage placed as one clip of exact length", len(placed) == 1 and placed[0][5] == 45))
    checks.append(("collage effects marked (fx, sparkles)", {"fx pass", "sparkles"} <= {m[2] for m in tl.markers}))
    # "pixels": free Resolve can't, so the timeline says what the render did: a video with no style named (the thread ripper effect: mosh),
    # its shots at the render's lengths, and a collage in the pixel sort, its stills timed as the render times them (the first a flight shorter)
    r = dict(id="check5", account="", kind="video", format="9:16", song=None, caption="", pixels=True,
             shots=[{"clip": "x", "in": 0.2, "dur": 0.6}, {"clip": "x", "in": 0.3, "dur": 2.6}])
    rc, calls, tl, out = build(r, {"x": dict(file=src, width=1080, height=1920)}, tmp)
    note = next((m[3] for m in tl.markers if m[2] == "pixels"), ""); lens = [c[5] for c in calls if c[0] == "append" and c[1] == "V" and c[2] == 1]
    checks.append(("pixels on a video marked as the render does it (no style named: mosh), shots at the render's lengths",
                   LR.pixels_of(r)["style"] == "mosh" and "mosh" in note and lens == [18, 78] and "isn't carried" not in out))
    r = dict(id="check6", account="", kind="collage", format="9:16", song=None, caption="", stills=stills, sec_per_still=1.2, pixels={"style": "sort", "fly": 0.5})
    rc, calls, tl, out = build(r, {}, tmp)
    note = next((m[3] for m in tl.markers if m[2] == "pixels"), ""); placed = [c for c in calls if c[0] == "append" and c[1] == "V" and c[2] == 1]
    checks.append(("pixels on a collage marked (sort), the stills one clip as long as the render makes them",
                   "streaks" in note and len(placed) == 1 and placed[0][5] == sum(LR.still_frames(r)) == 21 + 36 + 36 and "isn't carried" not in out))
    for name, ok in checks:
        print("  %s  %s" % ("ok " if ok else "!! ", name))
        if not ok: FAIL.append(name)


def song_case():
    """A new song end to end, in a throwaway library: songs.py add -> its menu entry run once (builds the markers timeline) ->
    markers added -> run AGAIN (saves them) -> a piece cut to it -> its Resolve timeline carries those markers."""
    tmp = tempfile.mkdtemp(prefix="song_check_")
    try:
        lib, ut = os.path.join(tmp, "library"), os.path.join(tmp, "Utility"); os.makedirs(os.path.join(lib, "_audio")); os.makedirs(ut)
        ff("-f", "lavfi", "-i", "sine=frequency=220:duration=20", "-f", "lavfi", "-i", "sine=frequency=60:duration=20:beep_factor=8",
           "-filter_complex", "amix=inputs=2", os.path.join(tmp, "Check Song.wav"))
        code = r'''
import json, os, sys
sys.path.insert(0, %r); sys.path.insert(0, %r)
import songs, song_markers, mock_resolve, Resolve_Export as RX, Lab_Render as LR
lib, tmp = %r, %r
songs.add(os.path.join(tmp, "Check Song.wav"), "Check Song")
entry = os.path.join(%r, *songs.menu_folder().split("/"), "Check Song", "Mark the song.py")
copy_entry = os.path.join(os.path.dirname(entry), "Copy markers to every timeline.py")
tl_entry = os.path.join(os.path.dirname(entry), "Make a timeline per account.py")
json.dump({"accounts": [{"id": "mainacct", "name": "mainacct"}, {"id": "slides", "name": "slides"}]}, open(os.path.join(lib, "profile.json"), "w"))
assert os.path.exists(entry), "no menu entry"
r = mock_resolve.Mock()
out1 = r.run(entry)
tl = r.timeline(); assert tl and tl.name == "Check Song markers", "no markers timeline"
assert "AGAIN" in out1, "doesn't tell them to run it again"
tl.AddMarker(60, "Green", "drop", "", 150); tl.AddMarker(120, "Yellow", "hook", "", 1); tl.AddMarker(240, "Blue", "line", "", 1)   # a song may mark in yellow too
out2 = r.run(entry)
ms = song_markers.load(song="check-song")
assert [m["name"] for m in ms] == ["drop", "hook", "line"] and len(song_markers.windows(ms)) == 1, "markers not saved: " + out2
# a second timeline with the song on it (an edit in the same project): Copy puts the markers on it, backing up what it had
mp = r.project.GetMediaPool(); other = mp.CreateEmptyTimeline("my edit v1"); mp.AppendToTimeline([mp.cur.clips[0]]); other.AddMarker(15, "Red", "old", "", 1)
other.AddMarker(30, "Yellow", "flash 3 frames", "", 1)   # a render's note: stays
out3 = r.run(copy_entry)
assert sorted(m[2] for m in other.markers) == ["drop", "flash 3 frames", "hook", "line"], "copy didn't reach the other timeline, or took its yellow note: " + out3
out3 = r.run(copy_entry)   # again: the song's markers (the yellow one too) are replaced, not added to
assert sorted(m[2] for m in other.markers) == ["drop", "flash 3 frames", "hook", "line"], "a second copy changed the count: %%s" %% sorted(other.markers)
tl.DeleteMarkerAtFrame(120); out3 = r.run(copy_entry)   # the yellow song marker taken off the song: it leaves the copies, the render's note stays
assert sorted(m[2] for m in other.markers) == ["drop", "flash 3 frames", "line"], "a removed yellow song marker stayed: %%s" %% sorted(other.markers)
assert any(f.startswith("copy_backup_") for f in os.listdir(os.path.join(lib, "_audio", "songs", "check-song", "_old"))), "no backup of the old markers"
out5 = r.run(tl_entry); out6 = r.run(tl_entry)
made = [t for t in r.project.tls if t.name in ("mainacct - Check Song", "slides - Check Song")]
assert len(made) == 2 and all(sorted(m[2] for m in t.markers) == ["drop", "line"] for t in made) and all(t.settings.get("timelineResolutionHeight") == "1920" for t in made), out5
assert "Made 0 timelines" in out6 and "Already there" in out6, out6
import subprocess
v = os.path.join(tmp, "v.mp4"); subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=4:r=30", "-pix_fmt", "yuv420p", v], check=True)
LR.load_index = lambda: {"x": dict(file=v, width=1080, height=1920)}
rp = os.path.join(tmp, "r.json")
json.dump(dict(id="check4", account="", kind="video", format="9:16", song=dict(name="check-song", start=2.0), caption="",
               shots=[{"clip": "x", "in": 0.5, "dur": 3.0}]), open(rp, "w"))
rc, erp, _, _ = RX.convert(rp, tmp)
assert rc["audio"]["file"].endswith("Check Song.wav"), "piece isn't cut to its song"
from resolve_check import absolute; absolute(erp)
calls, tl2, out4 = mock_resolve.run_engine(erp, os.path.join(%r, RX.ENGINE))
assert any(m[2] == "drop" and m[0] == 0 for m in tl2.markers), "the song's markers didn't reach the piece's timeline: %%s" %% tl2.markers
# the same piece in this project (the song from 2 s into itself): Copy leaves it and its own markers alone, and says so
r.run(os.path.join(%r, RX.ENGINE), RECIPE=erp); piece = r.timeline(); before = sorted(piece.markers)
out7 = r.run(copy_entry)
assert sorted(piece.markers) == before and piece.name in out7 and "Left alone" in out7, "copy changed a piece cut from the middle of the song: " + out7
assert not song_markers.check(dict(song=dict(name="check-song", start=2.0), kind="video", shots=[{"dur": 3.0}])), "start on a marker flagged"
assert song_markers.check(dict(song=dict(name="check-song", start=2.5), kind="video", shots=[{"dur": 3.0}])), "off-marker start not flagged"
# a lyric layer of the song's, laid by name ("lyrics": "<name>"): white on green, keyed as the render keys it, carried in the text layer
# on V2. Its file ends 0.6 s into the edit: the words stop there (they used to stay on to the end)
import numpy as np
from sandbox_paths import locked
subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x00ff00:s=640x360:d=2.6:r=30,drawbox=x=220:y=140:w=200:h=80:color=white:t=fill",
                "-pix_fmt", "yuv420p", os.path.join(lib, "_audio", "layer.mp4")], check=True)
with locked(songs._cfg_path()):
    cfg = songs.config(); cfg["songs"]["check-song"]["lyric_layers"] = {"words": {"file": "_audio/layer.mp4", "key": "green"}}; songs.save(cfg)
json.dump(dict(id="check7", account="", kind="video", format="9:16", song=dict(name="check-song", start=2.0), caption="", lyrics="words",
               shots=[{"clip": "x", "in": 0.5, "dur": 1.0}]), open(rp, "w"))
rc, erp, _, _ = RX.convert(rp, tmp); absolute(erp)
calls, tl3, out8 = mock_resolve.run_engine(erp, os.path.join(%r, RX.ENGINE))
assert any(c[0] == "append" and c[1] == "V" and c[2] == 2 and c[3].endswith("check7_text.mov") for c in calls), "the lyric layer's text layer isn't on V2"
def text_at(t):   # the text layer's frame at t, with its transparency
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%%.3f" %% t, "-i", os.path.join(tmp, "check7_text.mov"), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 4)
on, off = text_at(0.3), text_at(0.8)
assert on[960, 540].min() > 240 and on[400, 540, 3] < 8, "the lyric layer isn't keyed into the text layer: %%s on the words, %%s on the green" %% (on[960, 540], on[400, 540])
assert off[..., 3].max() < 8, "the lyric layer's words stay on after its file has ended"
print("ok")
''' % (SCRIPTS, HERE, lib, tmp, ut, SCRIPTS, SCRIPTS, SCRIPTS)
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, SANDBOX_LIBRARY=lib, SANDBOX_RESOLVE_UTILITY=ut))
        ok = p.stdout.strip().endswith("ok")
        print("  %s  new song: markers timeline, run again to save, copy to other timelines (twice: the song's own yellow marker replaced, then gone when the song drops it; a render's note kept; a piece cut from the middle left alone), a timeline per account, markers checked, a piece carries them; a lyric layer laid by name is keyed into the text layer and stops where its file ends" % ("ok " if ok else "!! "))
        if not ok: print("       " + (p.stderr.strip().splitlines() or ["?"])[-1]); FAIL.append("new song")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def import_case():
    """Import_Library on a throwaway library: bins mirror the folders, clips get their accounts as Keywords and their
    one-liner as Description, and a second run imports and changes nothing. In another project, a clip on a card that isn't
    plugged in, a name that is in the library twice and the person's own bins are left alone."""
    tmp = tempfile.mkdtemp(prefix="import_check_")
    try:
        lib = os.path.join(tmp, "library"); d = os.path.join(lib, "_external content", "trip"); os.makedirs(d); os.makedirs(os.path.join(lib, "_reference"))
        ff("-f", "lavfi", "-i", "testsrc2=s=720x1280:d=1", "-pix_fmt", "yuv420p", os.path.join(d, "IMG_1.mov"))
        os.makedirs(os.path.join(lib, "_internal content")); shutil.copy(os.path.join(d, "IMG_1.mov"), os.path.join(lib, "_internal content", "a_1.mp4"))
        json.dump({"clips": {"IMG_1": {"file": "_external content/trip/IMG_1.mov", "fits": ["main", "slides"], "source_type": "real", "line": "golden hour walk"},
                             "a_1": {"file": "_internal content/a_1.mp4", "fits": ["main"], "line": "a moved clip"}}},
                  open(os.path.join(lib, "_reference", "clip_index.json"), "w"))
        json.dump({"accounts": [{"id": "main", "name": "mainaccount"}, {"id": "slides", "name": "slideshows"}]}, open(os.path.join(lib, "profile.json"), "w"))
        code = r'''
import sys; sys.path.insert(0, %r); import mock_resolve
import os
r = mock_resolve.Mock(); mp = r.project.GetMediaPool(); root = mp.GetRootFolder()
lib = os.environ["SANDBOX_LIBRARY"]
old = mp.AddSubFolder(root, "01_x"); src = mp.AddSubFolder(old, "source"); mp.AddSubFolder(root, "already empty")
mp.SetCurrentFolder(src); moved_clip = mp.ImportMedia([os.path.join(lib, "_internal content", "a_1.mp4")])[0]
moved_clip.path = os.path.join(lib, "01_x", "source", "a_1.mp4")   # imported before the move: offline now
o1 = r.run(%r); o2 = r.run(%r)
assert moved_clip.path == os.path.join(lib, "_internal content", "a_1.mp4"), "not relinked: " + moved_clip.path
names = [f.GetName() for f in root.GetSubFolderList()]
assert "01_x" not in names and "already empty" in names and "_internal content" in names, names
assert "1 relinked" in o1 and "1 moved into the folder bins" in o1 and "2 emptied bins removed" in o1, o1
root = r.project.GetMediaPool().GetRootFolder()
ext = next(f for f in root.GetSubFolderList() if f.GetName() == "_external content"); trip = next(f for f in ext.GetSubFolderList() if f.GetName() == "trip")
c = trip.GetClipList()[0]
assert c.GetMetadata("Keywords") == "mainaccount,slideshows,real" and c.GetMetadata("Description") == "golden hour walk", c.meta
assert "1 new clip imported, 2 tagged" in o1 and "0 new clips imported, 0 tagged" in o2 and "relinked" not in o2, (o1, o2)
# another project: the person's own bins, a card that isn't plugged in, a clip whose name is in the library twice, and one with a
# copy in _to_delete (that copy doesn't count: the clip is relinked to the file in the footage folders)
import shutil
for d, n in (("_internal content/t1", "twin.mp4"), ("_external content/t2", "twin.mp4"), ("_internal content/t3", "aside.mp4"), ("_to_delete/x", "aside.mp4")):
    os.makedirs(os.path.join(lib, d), exist_ok=True); shutil.copy(os.path.join(lib, "_internal content", "a_1.mp4"), os.path.join(lib, d, n))
r2 = mock_resolve.Mock(); mp2 = r2.project.GetMediaPool(); root2 = mp2.GetRootFolder()
sel = mp2.AddSubFolder(root2, "selects"); mp2.SetCurrentFolder(sel); pick = mp2.ImportMedia([os.path.join(lib, "_internal content", "a_1.mp4")])[0]
twin = mp2.ImportMedia([os.path.join(lib, "_internal content", "t1", "twin.mp4")])[0]; twin.path = os.path.join(lib, "_internal content", "old", "twin.mp4")
aside = mp2.ImportMedia([os.path.join(lib, "_internal content", "t3", "aside.mp4")])[0]; aside.path = os.path.join(lib, "_internal content", "old", "aside.mp4")
shoot = mp2.AddSubFolder(root2, "my shoot"); mp2.SetCurrentFolder(shoot); card = mp2.ImportMedia([os.path.join(lib, "_external content", "trip", "IMG_1.mov")])[0]
card.path = "/Volumes/CARD/DCIM/IMG_1.mov"   # its card isn't plugged in; the library has another shoot's IMG_1.mov
o3 = r2.run(%r)
assert card.path == "/Volumes/CARD/DCIM/IMG_1.mov", "a clip from outside the library was relinked: " + card.path
assert twin.path == os.path.join(lib, "_internal content", "old", "twin.mp4") and "twin.mp4" in o3, "a name twice in the library was relinked or not named: " + o3
assert aside.path == os.path.join(lib, "_internal content", "t3", "aside.mp4") and "aside.mp4" not in o3.split("Left offline")[-1], "a copy in _to_delete counted as a second file: " + aside.path + " | " + o3
assert [f.GetName() for f in root2.GetSubFolderList()][:2] == ["selects", "my shoot"] and pick in sel.GetClipList() and card in shoot.GetClipList(), "a bin the person made was emptied or removed: " + o3
print("ok")
''' % ((HERE,) + (os.path.join(SCRIPTS, "resolve", "Import_Library.py"),) * 3)
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ, SANDBOX_LIBRARY=lib))
        ok = p.stdout.strip().endswith("ok")
        print("  %s  import library: bins mirror the folders, relinks and re-files moved clips, removes only emptied bins, account names as Keywords, one-liner as Description, re-run changes nothing; a card that isn't plugged in, a name twice in the library and the person's own bins are left alone; a copy in _to_delete doesn't count" % ("ok " if ok else "!! "))
        if not ok: print("       " + (p.stderr.strip().splitlines() or ["?"])[-1]); FAIL.append("import library")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def drift():
    """forecast.py keeps its own copies of CARRIED's groups (Resolve_Export.EFFECTS, NOTES, SHOT_EFFECTS, SHOT_NOTES): a key one of them
    has and the other lacks would make a forecast count a note as a re-cut, or an effect as nothing. Each such key is named."""
    try:
        import forecast as FC
    except Exception as e:
        print("  !!  forecast.py doesn't load (%s)" % e); FAIL.append("forecast's lists"); return
    every = RX.CARRIED["recipe"] | RX.CARRIED["shot"]; bad = []
    for name, ours, theirs in (("effects", RX.EFFECTS, FC.EFFECTS), ("notes", RX.NOTES, FC.NOTES), ("shot effects", RX.SHOT_EFFECTS, FC.SHOT_EFFECTS), ("shot notes", RX.SHOT_NOTES, FC.SHOT_NOTES)):
        bad += ["%s is in forecast.py's %s but not Resolve_Export.py's%s" % (k, name, "" if k in every else " (nor anywhere in CARRIED)") for k in sorted(theirs - ours)]
        bad += ["%s is in Resolve_Export.py's %s but not forecast.py's" % (k, name) for k in sorted(ours - theirs)]
    print("  %s  forecast.py's effects and notes lists match CARRIED's%s" % ("ok " if not bad else "!! ", ": " + "; ".join(bad) if bad else ""))
    if bad: FAIL.append("forecast's lists")


def library():
    rs = [p for p in glob.glob(os.path.join(LIB, "_recipes", "**", "*.json"), recursive=True) if "/_archive/" not in p]   # _archive: retired, media gone
    n = missing = 0
    for p in sorted(rs):
        try: rc = json.load(open(p))
        except (OSError, ValueError): continue
        if not isinstance(rc, dict) or not rc.get("shots"): continue
        n += 1
        try:
            calls, tl, out = mock_resolve.run_engine(p, ENGINE)
        except Exception as e:
            print("  !!  %s: the engine crashed (%s)" % (os.path.relpath(p, LIB), e)); FAIL.append(p); continue
        lines = [l for l in out.splitlines() if l.startswith("!!")]
        gone = [l for l in lines if "Missing file" in l]; missing += len(gone)
        if len(lines) > len(gone): print("  !!  %s: %s" % (os.path.relpath(p, LIB), "; ".join(l for l in lines if l not in gone))); FAIL.append(p)
    print("  %d Resolve recipes in library/_recipes build%s" % (n, " (%d source files since deleted, reported by the engine)" % missing if missing else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="resolve_check_")
    try:
        print("Resolve version vs the render:"); cases(tmp); song_case(); import_case(); drift()
        if "--quick" not in sys.argv: library()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + ", ".join(os.path.basename(f) for f in FAIL) if FAIL else "all good")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__": main()
