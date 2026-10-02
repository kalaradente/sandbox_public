#!/usr/bin/env python3
"""The song's markers and windows, from ONE file: the markers file saved from Resolve (or written for someone without it).
  python3 _scripts/song_markers.py [--song <song>]                   every marker and window (default: the current song)
  python3 _scripts/song_markers.py --check <batch | recipe.json> … [--part <start>-<end>]
                                                                     each song edit starts on a marker (or a line) and stays inside one window
  python3 _scripts/song_markers.py --lines [--song <song>]           where each sung line starts (from the song's word timings)
--check takes a round by its folder, its name or its number (R08), or recipe files; a name that is neither is an error, not a pass.
--part 89.35-122.3 (seconds of the song): for a round cut to a part of the song that was named for it, where the windows are
not a limit. With it each edit starts on a marker or a line and lies inside that part, and the windows aren't
checked. Without it the check is the one above.
A song edit may also start on a sung line instead of a marker: on the kick at or just before the line's first word (beat_map.py shows it), at
most LEAD s early, so the word is never clipped. A line start may sit up to LEAD s before its window's own start.
Each song has its own markers file (songs.py: library/_audio/song.json "markers_file"). Resolve users make it with the song's
menu entry (Workspace > Scripts > <songs menu> > <song>: run it, add markers, run it AGAIN to save). No Resolve: write it
yourself in the same shape, from beat_map.py and the moments they name:  {"markers": [{"sec": 42.5, "dur_sec": 12, "color": "Green",
"name": "first chorus", "note": ""}]}. A window = a marker stretched to a length (2 s or more).
Everything reads markers through here (the lab, beat_map.py, the Resolve engine). Never copy marker or window times into a
doc, a prompt or another file: they go stale the moment the markers move."""
import glob, json, os, sys

WINDOW_MIN = 2.0          # a marker this long or longer is a window (Song_Timeline saves windows the same way)
FPS = 30
TOL = 1.0 / FPS + 1e-3    # "on a marker" = within one frame
LEAD = 0.5                # a line start: on the kick at most one beat (at 120 BPM) before the line's first word


def _lib():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sandbox_paths import LIB
    return LIB


def markers_path(lib=None, song=None):
    """A song's markers file (songs.py; None = the current song)."""
    lib = lib or _lib()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import songs
    e = songs.get(song, lib)
    return e["markers_file"] if e else os.path.join(lib, "_audio", "markers.json")


def load(lib=None, song=None):
    """Every marker on a song's timeline (None = the current song), in order: [{sec, dur_sec, color, name, note}]. [] if none yet."""
    return load_file(markers_path(lib, song))


def load_file(p):
    if not os.path.exists(p): return []
    d = json.load(open(p, encoding="utf-8"))
    ms = d.get("timeline_markers") or d.get("markers") or []
    return sorted([dict(sec=float(m["sec"]), dur_sec=float(m.get("dur_sec") or 0), color=m.get("color") or "Blue",
                        name=m.get("name") or "", note=m.get("note") or "", custom=m.get("custom") or "") for m in ms],
                  key=lambda m: m["sec"])


def windows(ms=None):
    """The windows: markers stretched to 2 s or more. [{name, start, end, color}]"""
    ms = load() if ms is None else ms
    return [dict(name=m["name"], start=round(m["sec"], 3), end=round(m["sec"] + m["dur_sec"], 3), color=m["color"])
            for m in ms if m["dur_sec"] >= WINDOW_MIN]


def lines(e):
    """A song's sung lines from its word timings: [{start, text}] (start = the first word's time). [] if not timed."""
    p = e.get("lyrics_timing") if e else None
    if not p or not os.path.exists(p): return []
    d = json.load(open(p, encoding="utf-8"))
    return [dict(start=float((l.get("words") or [{}])[0].get("t", l["start"])), text=l.get("text", "")) for l in d.get("lines") or []]


def length(r):
    """A recipe's length exactly as Lab_Render makes it (whole frames per shot; a collage's from the render's own count of
    each still's frames, Lab_Render.still_frames: with "pixels" the stills aren't all on for the same time)."""
    if r.get("kind") == "video": return sum(int(round(s["dur"] * FPS)) for s in r.get("shots") or []) / FPS
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import Lab_Render   # here, not at the top: Resolve's own Python reads the markers through this file, and has no cv2
    return sum(Lab_Render.still_frames(r)) / Lab_Render.FPS


def check(r, ms=None, ws=None, part=None):
    """Problems with a recipe's song placement ([] = fine). Only for the song itself: another sound has no markers.
    part = (start, end) in seconds of the song: the part named for the round (--part). The edit lies inside it; the windows
    aren't asked."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import songs
    song = songs.block(r)
    if not song or song.get("file"): return []
    e = songs.of_recipe(r)
    if not e: return ["no song in library/_audio/song.json"]
    if ms is None: ms = load_file(e["markers_file"])
    ws = windows(ms) if ws is None else ws
    if not ms: return ["%s has no markers yet (%s): nothing to check against" % (e["name"], os.path.relpath(e["markers_file"], _lib()))]
    a = float(song["start"]); out = []
    try: b = a + length(r)
    except ValueError as x: return ["the render refuses it: %s" % x]   # a collage whose "pixels" Lab_Render won't take: its line here, not a traceback for the round
    near = min(ms, key=lambda m: abs(m["sec"] - a))
    line = next((l for l in lines(e) if -TOL <= l["start"] - a <= LEAD), None) if abs(near["sec"] - a) > TOL else None
    if abs(near["sec"] - a) > TOL and not line:
        out.append("starts at %.3f, not on a marker (nearest: %s at %.3f) or a line (on the kick up to %.1f s before its first word)"
                   % (a, near["name"] or near["color"], near["sec"], LEAD))
    lead = LEAD + TOL if line else TOL
    if part:   # the part of the song named for the round: the windows are not a limit there
        if not (part[0] - lead <= a and b <= part[1] + TOL): out.append("runs %.3f-%.3f, not inside the part named (%.3f-%.3f)" % (a, b, part[0], part[1]))
        return out
    if not any(w["start"] - lead <= a and b <= w["end"] + TOL for w in ws):
        inside = [w for w in ws if w["start"] - TOL <= a < w["end"]]
        out.append("runs %.3f-%.3f, past the end of %s (%.3f)" % (a, b, inside[0]["name"], inside[0]["end"]) if inside else
                   "runs %.3f-%.3f, not inside any window" % (a, b))
    return out


def _fmt(t): return "%d:%06.3f" % (t // 60, t % 60)


def main():
    a = sys.argv[1:]
    song = a[a.index("--song") + 1] if "--song" in a else None
    ms = load(song=song); ws = windows(ms); lib = _lib()
    if "--lines" in a:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import songs
        ls = lines(songs.get(song, lib))
        if not ls: sys.exit("No word timings for this song (lyrics_timing.py <song>).")
        for l in ls:
            w = next((w["name"] for w in ws if w["start"] - LEAD - TOL <= l["start"] < w["end"]), "")
            print("  %s  %7.3f  %-45s %s" % (_fmt(l["start"]), l["start"], l["text"][:45], w))
        return
    if "--check" in a:
        paths = []; part = None; rest = a[a.index("--check") + 1:]
        if "--part" in a:
            try: part = tuple(float(v) for v in a[a.index("--part") + 1].split("-"))
            except (IndexError, ValueError): part = ()
            if len(part) != 2 or not 0 <= part[0] < part[1]: sys.exit("--part takes <start>-<end> in seconds of the song, like --part 89.35-122.3")
        for x in [x for i, x in enumerate(rest) if x not in ("--part", "--song") and (i == 0 or rest[i - 1] not in ("--part", "--song"))]:
            from sandbox_paths import round_files   # a round by path, name or number: its recipes, wherever it sits in _lab/rounds
            x = x if os.path.exists(x) or not os.path.exists(os.path.join(lib, x)) else os.path.join(lib, x)
            got = [x] if os.path.isfile(x) else round_files(x)
            if not got: sys.exit("Nothing to check for %r: it isn't a recipe file, or a round with recipes (its folder, its name or its number, like R08)" % x)   # never "0 checked, all fine" for a name that isn't there
            paths += got
        bad = 0; n = 0
        for p in paths:
            try: r = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError): continue
            if not isinstance(r, dict): continue
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import songs
            if not (songs.block(r) and not songs.block(r).get("file")): continue
            n += 1
            for msg in check(r, part=part): print("!! %s: %s" % (r.get("id", os.path.basename(p)), msg)); bad += 1
        print("%d song edits checked, %s" % (n, "%d problem(s)" % bad if bad else "all start on a marker or a line and stay inside %s" % ("the part named (%g-%g)" % part if part else "a window")))
        sys.exit(1 if bad else 0)
    if not ms: sys.exit("No markers yet (%s). In Resolve: Workspace > Scripts > <songs menu> > the song (run it, add markers, run it again); "
                        "otherwise write it (see --help)." % os.path.relpath(markers_path(song=song), lib))
    print("Markers (%s):" % os.path.relpath(markers_path(song=song), lib))
    for m in ms:
        print("  %s  %7.3f  %-7s %s%s" % (_fmt(m["sec"]), m["sec"], m["color"], m["name"], "  [window %.1f s]" % m["dur_sec"] if m["dur_sec"] >= WINDOW_MIN else ""))
    print("Windows (a song edit starts on a marker and stays inside one):")
    for w in ws: print("  %7.3f-%7.3f  (%4.1f s)  %s" % (w["start"], w["end"], w["end"] - w["start"], w["name"]))


if __name__ == "__main__":
    if "-h" in sys.argv or "--help" in sys.argv: print(__doc__.strip()); sys.exit(0)   # the usage and nothing else, as every script (the others get it from sandbox_paths)
    main()
