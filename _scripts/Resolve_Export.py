#!/usr/bin/env python3
"""Turn finished pieces into DaVinci Resolve timelines the person can refine: you curate, they refine.
  python3 _scripts/Resolve_Export.py <batch folder | recipe.json …> [--ids R07_1_01,R07_1_04]
A lab round can be named by its folder, its full name or its number (R08), wherever it sits in library/_lab/rounds: one whose
pieces are sorted between folders exports as one round, and --ids finds a piece in whichever folder holds it. A round or an
id that matches nothing is an error.
For each lab/batch recipe it writes, in library/_recipes/resolve/<batch>/:
  <id>.json          an engine recipe: every shot on V1 at its in point and the render's exact length, framed like
                     the render (Lab_Render.framing(), the same function the render uses: letterbox bars cropped, sideways
                     clips turned, filled from the middle or around the shot's "center", the same numbers as Pan and Tilt),
                     the song on audio track 1 from the same start, the song's markers
  <id>_text.mov      the caption / word-by-word lyrics (or the song's lyric layer the recipe names, keyed as the render
                     keys it) as their own layer with transparency, on V2 (delete it or restyle it; the pictures
                     underneath are clean)
  <id>_stills.mp4    for a collage: the stills already timed (Resolve ignores lengths on photos), on V1
and a menu entry in Resolve's Workspace > Scripts (folders from layout.json "resolve_menu"). Running it builds a new
"<name> v#" timeline each time. What a script can't set becomes a yellow marker saying exactly what the render did:
speed changes ("speed 1.3x", with the source range) and the song's fade-out.
The rule: the Resolve version matches the render. Anything the render does to a piece must be carried here; a fix made
to a render goes into its recipe (never only into the mp4), so this picks it up."""
import json, os, re, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, ROOT, RESOLVE_UTILITY as UTILITY, find_account, layout, resolve_menu, round_files  # noqa: E402
import Lab_Render as LR  # noqa: E402

ENGINE = "Timeline_Engine.py"
FPS = LR.FPS


def text_layer(r, W, H, dur, out):
    """Caption + lyrics as a ProRes 4444 layer with alpha, or None if the piece has neither."""
    song = LR.SONGS.block(r)
    if not r.get("caption") and not (r.get("lyrics") and song): return None
    tmp = tempfile.mkdtemp(prefix="rx_")
    try:
        cap = os.path.join(tmp, "cap.png"); LR.caption_png(r.get("caption", ""), W, H, cap)
        lyr = LR.lyric_frames(r, W, H, dur, tmp) if (r.get("lyrics") and song) else None
        cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-t", "%.3f" % dur, "-i", cap]
        if lyr: cmd += ["-framerate", str(FPS), "-i", lyr, "-filter_complex", "[0:v][1:v]overlay=0:0:eof_action=pass,format=yuva444p10le"]
        else: cmd += ["-vf", "format=yuva444p10le"]
        cmd += ["-c:v", "prores_ks", "-profile:v", "4444", "-t", "%.3f" % dur, out]
        subprocess.run(cmd, check=True, timeout=300)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# Every recipe key the render uses, and how the Resolve version carries it. A render option that isn't carried here
# would make the timeline quietly differ from the render, so unknown keys are flagged (add them here AND in the render).
# The groups are named: forecast.py keeps its own copies of EFFECTS, NOTES, SHOT_EFFECTS and SHOT_NOTES, and resolve_check.py fails when they drift.
EFFECTS = {"grade", "grain", "fx", "blend", "blend_style", "layers", "sparkles", "doodles", "pixels"}   # yellow markers saying what the render did
NOTES = {"story", "why", "sources", "sharpness", "notes", "tags", "forecast", "title"}         # notes, not picture or sound (the title: the suggested caption, never drawn)
SHOT_EFFECTS = {"flash", "punch", "blend", "reverse"}
SHOT_NOTES = {"what", "why", "note", "scene", "snare", "looked", "why_in"}   # a shot's planning notes (what it shows, why it sits there, what was looked at): the render reads none of them
CARRIED = {"recipe": {"id", "account", "kind", "format", "song", "song", "caption", "lyrics", "shots", "stills", "sec_per_still"} | EFFECTS | NOTES,
           "shot": {"clip", "in", "dur", "speed", "center"} | SHOT_EFFECTS | SHOT_NOTES}   # center: carried as cx, cy


def unknown(r):
    bad = sorted(set(r) - CARRIED["recipe"]) + sorted({k for s in r.get("shots") or [] for k in s} - CARRIED["shot"])
    if bad: print("!! %s: %s isn't carried into Resolve yet; the timeline won't match the render there (see CARRIED in Resolve_Export.py)"
                  % (r["id"], ", ".join(bad)))


def probe_size(path):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                       capture_output=True, text=True).stdout.strip().split(",")
    return int(o[0]), int(o[1])


def fill_zoom(sh, cw, ch, W, H):
    """The Zoom Timeline_Engine sets on this shot (Resolve's default input scaling, scale to fit: the clip is fitted before it's
    turned, then zoomed to fill with the picture inside any bars). What a punch keyframes from. resolve_check.py compares this
    with the zoom the engine really sets, so the two can't drift apart."""
    sc = min(W / cw, H / ch)
    if sh.get("rotate") and abs(sh["rotate"]) % 180 == 90: cw, ch = ch, cw
    aw, ah = sh.get("fill_area") or (cw, ch)
    return max(W / (aw * sc), H / (ah * sc))


def pan_tilt(sh, cw, ch, W, H):
    """The Pan and Tilt Timeline_Engine sets on this shot (it puts (cx, cy) of the turned clip in the middle of the frame), 0 where it
    sets none: a punch keyframes them with the zoom, so the frame stays on the same spot. resolve_check.py compares them with the engine's."""
    z = fill_zoom(sh, cw, ch, W, H); sc = min(W / cw, H / ch)
    if sh.get("rotate") and abs(sh["rotate"]) % 180 == 90: cw, ch = ch, cw
    dw, dh = cw * sc * z, ch * sc * z
    px = max(-(dw - W) / 2, min((dw - W) / 2, (0.5 - sh.get("cx", 0.5)) * dw)); py = max(-(dh - H) / 2, min((dh - H) / 2, (sh.get("cy", 0.5) - 0.5) * dh))
    return (px if abs(px) > 0.5 else 0.0), (py if abs(py) > 0.5 else 0.0)


def clip_center(fr, cw, ch):
    """framing()'s centre of the frame (a fraction of the picture left after the crop and the turn; its middle when None) as a
    fraction of the whole turned clip: the cx, cy the engine pans to."""
    u, v = fr["center"] or (0.5, 0.5); w, h, x, y = fr["crop"] or (cw, ch, 0, 0)
    if fr["turn"]: u, v = 1 - v, u                       # back into the clip as filmed (the turn is 90 counter-clockwise)
    X, Y = (x + u * w) / cw, (y + v * h) / ch
    return (Y, 1 - X) if fr["turn"] else (X, Y)


def convert(path, outdir):
    r = json.load(open(path, encoding="utf-8")); W, H = LR.size(r["format"]); idx = LR.load_index(); song = LR.SONGS.block(r)
    acct = find_account(r.get("account")) or {}
    name = ("%s %s" % (r["id"], r["caption"][:40].strip())) if r.get("caption") else r["id"]
    shots, markers, rec = [], [], 0.0
    unknown(r)
    if r["kind"] == "video":
        LR.look_ahead(r, idx)                                                # Vision for "face" shots, one run per clip (remembered: the render's own answer)
        for i, s in enumerate(r["shots"]):
            c = idx[s["clip"]]; sp = s.get("speed", 1.0)
            n = int(round(s["dur"] * FPS))                                   # the render's exact frame count for this shot
            sh = dict(file=c["file"], src_in=s["in"], rec=round(rec, 4), dur=round(n / FPS, 4), fill=True, label=s["clip"])
            fr = LR.framing(c, s, W, H, LR.span_of(r, i))                   # the render's own framing: crop, then turn, then fill round its centre
            cw, ch = c.get("width"), c.get("height")
            if not (cw and ch): cw, ch = probe_size(os.path.join(LIB, c["file"]))
            if fr["crop"] or fr["center"]:
                cx, cy = clip_center(fr, cw, ch)
                if fr["crop"]: sh["fill_area"] = [fr["crop"][1], fr["crop"][0]] if fr["turn"] else list(fr["crop"][:2])   # the picture inside the bars, turned
                sh.update(cx=round(cx, 4), cy=round(cy, 4))
            if fr["turn"]: sh["rotate"] = fr["turn"]
            if abs(sp - 1) > 1e-3:
                markers.append(dict(sec=round(rec, 3), color="Yellow", name="speed %gx" % sp,
                                    note="the render plays %.2f-%.2fs of this clip at %g%%: right-click > Change Clip Speed > %g%%, then drag its end out to the next cut"
                                         % (s["in"], s["in"] + s["dur"] * sp, sp * 100, sp * 100)))
            # a reversed shot is turned round AFTER its flash and punch are drawn (Lab_Render.render_video), so the render shows
            # them at the END of its run (the last frames before the next cut, or under the blend into it), not at its cut
            end = rec + (n + round(LR.run_on(r, i) * FPS)) / FPS if s.get("reverse") else None
            if s.get("flash"):
                k = int(s["flash"])
                markers.append(dict(sec=round(rec, 3), color="Yellow", name="flash %d frames" % k,
                                    note="the render opens this shot on white, clearing over %d frames: Effects > Video Transitions > Dip to Color (white), "
                                         "%d frames, on this cut, aligned to start at the cut" % (k, k)) if end is None else
                               dict(sec=round(end - k / FPS, 3), color="Yellow", name="flash %d frames (at the end: reversed)" % k,
                                    note="the render reverses this shot after drawing its flash, so it fades TO white over its last %d frames, ending "
                                         "at %.2fs: Dip to Color (white), %d frames, ending there" % (k, end, k)))
            if float(s.get("punch") or 0) > 1:
                z = fill_zoom(sh, cw, ch, W, H); p = float(s["punch"]); zp = z * p; k = round(LR.PUNCH_S * FPS); px, py = pan_tilt(sh, cw, ch, W, H)
                # the render zooms round the middle of its frame; Resolve zooms round the clip's own middle, so a panned shot keyframes its Pan and Tilt too
                pt = ("; with it Pan %.1f and Tilt %.1f at the zoomed end, %.1f and %.1f (what the Inspector shows now) at the other, so the frame stays on the same spot"
                      % (px * p, py * p, px, py)) if px or py else ""
                markers.append(dict(sec=round(rec, 3), color="Yellow", name="punch %gx" % p,
                                    note="the render opens this shot zoomed %g times, easing back over %gs: Inspector > Zoom, keyframe %.3f at the cut and "
                                         "%.3f (its framed zoom, what the Inspector shows now) %d frames later, ease in%s" % (p, LR.PUNCH_S, zp, z, k, pt)) if end is None else
                               dict(sec=round(end - LR.PUNCH_S, 3), color="Yellow", name="punch %gx (at the end: reversed)" % p,
                                    note="the render reverses this shot after drawing its punch, so it zooms IN over its last %gs: Inspector > Zoom, "
                                         "keyframe %.3f (its framed zoom) %d frames before %.2fs and %.3f at %.2fs, ease out%s" % (LR.PUNCH_S, z, k, end, zp, end, pt)))
            shots.append(sh); rec += n / FPS
        dur = rec
        if r.get("grade"):
            markers.append(dict(sec=0.0, color="Yellow", name="grade: %s" % r["grade"],
                                note="the render grades every shot %s (ffmpeg: %s): match it on the Color page" % (LR.GRADES[r["grade"]][1], LR.GRADES[r["grade"]][0])))
        if r.get("blend") or any(x.get("blend") for x in r["shots"]):
            markers.append(dict(sec=0.0, color="Yellow", name="blends",
                                note="the render melts each shot into the next (%s, %s s; a shot's own blend overrides): Resolve can do a Cross Dissolve centred "
                                     "after each cut, or import the rendered mp4" % (r.get("blend_style") or "fade", r.get("blend") or "per shot")))
        for n_, s_ in enumerate(r["shots"]):
            if s_.get("reverse"): markers.append(dict(sec=round(sum(x["dur"] for x in r["shots"][:n_]), 3), color="Yellow", name="reverse",
                                                     note="the render plays this shot backwards: right-click > Change Clip Speed > Reverse Speed"))
        if r.get("grain"):
            markers.append(dict(sec=0.0, color="Yellow", name="grain %d" % LR.grain_of(r),   # the strength the render used (a grain under 1 renders as 1)
                                note="the render adds film grain (ffmpeg noise %d, changing every frame) over every shot: Effects > Film Grain on an adjustment clip" % LR.grain_of(r)))
    else:
        tmp = tempfile.mkdtemp(prefix="rx_")
        try:
            body, dur = LR.render_collage(r, W, H, tmp)
            dst = os.path.join(outdir, r["id"] + "_stills.mp4"); shutil.copy2(body, dst)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        shots.append(dict(file=os.path.relpath(dst, LIB), src_in=0, rec=0, dur=dur, fill=False, label="the stills, timed"))
    # over the whole piece, a video's or a collage's (the render runs these passes on both)
    px = LR.pixels_of(r)   # as the render reads "pixels" ({} counts; a collage of one still is rendered plain, so no marker)
    if px and not (r["kind"] == "collage" and len(r.get("stills") or []) < 2):
        # "wind", "streak" and "decay", where the style reads them (Lab_Render: sort_flight, mosh_flight; pixel_plan and pixel_frame for the grains): the marker says what the render did
        more = {"sort": ", brightest at the bottom (wind: down)" if px["wind"] == "down" else "",
                "mosh": (", the movement that carries it overdone up to %g times (streak %g)" % (1 + px["streak"], px["streak"]) if px["streak"] else "") +
                        ", settling into that shot over the last %gs of the flight (decay)" % px["decay"]}.get(px["style"],
               (", the lift-off and the landing sweeping across the frame (wind: %s)" % px["wind"] if px["wind"] else "") +
               (", each grain in the air drawn as a line over its last %g frames of travel (streak)" % px["streak"] if px["streak"] else ""))
        markers.append(dict(sec=0.0, color="Yellow", name="pixels",
                            note="from each cut, for %gs, the render turns the outgoing picture into the incoming one by moving its own pixels (%s%s; the "
                                 "outgoing shot runs on %gs past its cut, the incoming one plays from it): free Resolve can't; import the rendered mp4 for it"
                                 % (px["fly"], {"sort": "each column's bright stretches run into streaks of light, then the incoming picture's streaks fall "
                                                        "back into place", "mosh": "the thread ripper effect, a datamosh: the outgoing shot plays on, smearing on its own motion, then the "
                                                        "incoming shot's motion takes the picture over and it is pulled into that shot"}.get(px["style"], "grains %d px across fly to where they fit, "
                                                        "turning its colours on the way" % px["size"]), more, LR.run_on(r, 0) if r["kind"] == "video" else 0)))
    for k, what in (("layers", "other clips blended over the picture"), ("sparkles", "animated stars drawn over the picture"), ("doodles", "hand-drawn doodles that follow a point in the shot")):
        if r.get(k): markers.append(dict(sec=0.0, color="Yellow", name=k, note="the render adds %s (%s): import the rendered mp4 for it" % (what, json.dumps(r[k])[:200])))
    if r.get("fx"):
        markers.append(dict(sec=0.0, color="Yellow", name="fx pass",
                            note="the render adds beat-synced effects free Resolve can't (%s): import the rendered mp4 over this timeline for them"
                                 % ", ".join("%s %s" % (k, v) for k, v in r["fx"].items())))
    rc = dict(name=name, account_bin=acct.get("name") or "sandbox Edits", fps=FPS, width=W, height=H, shots=shots, markers=markers,
              notes=" | ".join(x for x in (r.get("story"), r.get("why")) if x))
    if song:
        audio = LR.audio_of(r)[0]
        rc.update(audio=dict(file=os.path.relpath(audio, LIB), start=song["start"], end=round(song["start"] + dur, 3)), window_start=song["start"])
        e = None if song.get("file") else LR.SONGS.of_recipe(r)
        if e: rc["markers_file"] = os.path.relpath(e["markers_file"], LIB)   # the song's own markers (song_markers.py), shifted into the edit
        markers.append(dict(sec=round(max(0, dur - LR.FADE_OUT), 3), color="Yellow", name="fade out",
                            note="the render fades the song out over the last %gs: add an audio fade on the song here" % LR.FADE_OUT))
    t = text_layer(r, W, H, dur, os.path.join(outdir, r["id"] + "_text.mov"))
    if t: rc["overlays"] = [dict(file=os.path.relpath(t, LIB), rec=0, dur=dur)]
    rp = os.path.join(outdir, r["id"] + ".json"); json.dump(rc, open(rp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return rc, rp, acct, song


def menu_kind(song):
    """The menu folder for a piece: its song's (the song's "menu" name, else its title), "with_sound" for a TikTok sound, else "no_song"."""
    m = layout().get("resolve_menu") or {}
    if not song: return m.get("no_song") or "No song"
    if song.get("file"): return m.get("with_sound") or "With sound"
    e = LR.SONGS.of_recipe({"song": song}) or {}
    return (m.get("with_song") or "{song}").format(song=e.get("menu") or e.get("name") or "With song")


def menu_entry(rc, rp, acct, song, batch):
    m = layout().get("resolve_menu") or {"path": "sandbox/{name}/{kind}"}
    kind = menu_kind(song)
    folder = m["path"].format(id=acct.get("id") or "sandbox", name=acct.get("name") or "sandbox", kind=kind, batch=batch)
    return resolve_menu(folder, rc["name"], '''# %s (%dx%d, %.1fs; made by sandbox, %s)
# Each run builds a new "v#" timeline in the "%s" bin. Captions sit on V2 as their own layer.
import os
_r = os.path.expanduser("~/.sandbox_root")   # the sandbox folder, written by setup.sh
_root = open(_r, encoding="utf-8").read().strip() if os.path.exists(_r) else os.path.expanduser("~/Desktop/sandbox")
RECIPE = os.path.join(_root, "library", "%s")
exec(open(os.path.join(_root, "_scripts", "%s"), encoding="utf-8").read())
''' % (rc["name"], rc["width"], rc["height"], sum(s["dur"] for s in rc["shots"]), batch, rc["account_bin"], os.path.relpath(rp, LIB), ENGINE))


def main():
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    ids = set(a[a.index("--ids") + 1].split(",")) if "--ids" in a else None
    paths = []; bad = []
    for x in [x for i, x in enumerate(a) if x != "--ids" and (i == 0 or a[i - 1] != "--ids")]:
        x = x if os.path.exists(x) or not os.path.exists(os.path.join(LIB, x)) else os.path.join(LIB, x)
        # a round by path, name or number: its recipes, wherever it sits in _lab/rounds (every folder it is sorted between); any other folder: its own
        got = [x] if os.path.isfile(x) else [p for p in round_files(x) if re.match(r"R[\d.]+_.*\.json$", os.path.basename(p))]
        if not got: bad.append("%s: no recipe file, and no round with recipes, by that path, name or number" % x)
        paths += [p for p in got if p not in paths]   # (two folders of one round both name the whole round: each piece once)
    bad += ["%s: no piece with that id in %s" % (i, ", ".join(sorted({os.path.basename(os.path.dirname(os.path.abspath(p))) for p in paths})) or "what was named")
            for i in sorted((ids or set()) - {os.path.basename(p)[:-5] for p in paths})]   # an id that matches nothing is said, never a quiet exit 0
    paths = [p for p in paths if not ids or os.path.basename(p)[:-5] in ids]
    for p in paths:   # each failure is reported, the rest still export (as Lab_Render does)
        try:
            batch = os.path.basename(os.path.dirname(os.path.abspath(p)))
            outdir = os.path.join(LIB, "_recipes", "resolve", batch); os.makedirs(outdir, exist_ok=True)
            rc, rp, acct, song = convert(p, outdir)
            f, installed = menu_entry(rc, rp, acct, song, batch)
            where = "Workspace > Scripts > " + " > ".join(os.path.relpath(f, UTILITY)[:-3].split(os.sep)) if installed else "waiting for Resolve (./setup.sh installs it): " + os.path.relpath(f, LIB)
            print("%s: %d shots%s%s -> %s" % (rc["name"], len(rc["shots"]), ", song" if song else "", ", text layer" if rc.get("overlays") else "", where))
        except Exception as e: bad.append("%s: %s" % (os.path.basename(p), repr(e) if isinstance(e, KeyError) else e))
    if bad: sys.exit("export failed:\n  " + "\n  ".join(bad))


if __name__ == "__main__": main()
