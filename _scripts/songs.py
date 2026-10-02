#!/usr/bin/env python3
"""The songs being promoted: library/_audio/song.json. Each song keeps its own master, lyric sheet, word timings and markers.
  python3 _scripts/songs.py                                         list them (* = the one new edits are cut to)
  python3 _scripts/songs.py add <audio file> --name "Song Title" [--lyrics <sheet>]      (the real title: ask if it isn't clear)
        a song they dropped into the chat: copied into library/_audio/songs/<song>/ with its lyric sheet (.txt .rtf .doc
        .docx; pasted lyrics: save them as a .txt first, every sung line in order, choruses written out each time), the tempo
        estimated, the waveform drawn (song_map.png), a Resolve menu entry made, and it becomes the current song
  python3 _scripts/songs.py use <song>                              new edits are cut to this song
  python3 _scripts/songs.py menu                                    (re)make every song's Resolve menu entry (setup runs this)
Markers, in Resolve: Workspace > Scripts > <songs menu> > <Song Name> > Mark the song. The first run builds a "<Song Name> markers" timeline
with the song alone at the start. Press M on each moment an edit could start on; drag a marker longer (or set its Duration) to
make a window an edit stays inside. Then run the SAME entry AGAIN: that saves the markers. Then tell Claude. In each new
Resolve project that uses the song: <Song Name> > Copy markers to every timeline; <Song Name> > Make a timeline per account
builds "<account> - <song>" timelines with the song and markers (skips existing ones).
song.json: {"current": "<song>", "songs": {"<song>": {"name", "master", "lyrics", "lyrics_timing", "markers_file", "timeline",
"bpm"}}}, paths relative to library/; "timeline" = an existing Resolve timeline that holds the markers (default "<name> markers"); "copy_to" = text in the names of
timelines "Copy markers to every timeline" should reach (besides any timeline the song's file is on); "menu" = its folder name
for pieces in Resolve (default: the title); "lyric_layers" = lyric videos made for the whole song that a recipe can lay over
an edit by name ("lyrics": "<name>"; Lab_Render.lyric_layer says what each entry holds).
The older one-song form {"master", "lyrics_timing", "markers_file"} still reads (master and lyrics_timing are in _audio/).
A recipe's song block ("song": {"name": "<song>", "window", "start"}) names its song. One without a name uses the FIRST song (what edits used before there
were several), never the current one, so an old edit keeps its song when the current song changes."""
import filecmp, json, os, re, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, ROOT, layout, resolve_menu, RESOLVE_UTILITY, locked, save_json  # noqa: E402

AUDIO_EXT = (".wav", ".aif", ".aiff", ".mp3", ".m4a", ".flac")


def _cfg_path(lib=None): return os.path.join(lib or LIB, "_audio", "song.json")


def slug(name): return re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’]", "", name.lower())).strip("-") or "song"   # "That's" -> thats


def config(lib=None):
    """song.json in its current form: {"current", "songs": {slug: entry}} (an older one-song file is read as one song)."""
    lib = lib or LIB
    try: d = json.load(open(_cfg_path(lib), encoding="utf-8"))
    except (OSError, ValueError): d = {}
    if "songs" in d: return d
    a = os.path.join(lib, "_audio"); fs = sorted(os.listdir(a)) if os.path.isdir(a) else []
    master = d.get("master") or next((f for f in fs if f.lower().endswith((".wav", ".aif", ".aiff")) and "_clip_" not in f.lower()), "")
    if not master: return {"current": None, "songs": {}}
    lt = d.get("lyrics_timing") or next((f for f in fs if f.startswith("lyrics") and "timing" in f and f.endswith(".json")), "")
    s = slug(d.get("name") or "song")
    e = dict(name=d.get("name") or os.path.splitext(master)[0], master="_audio/" + master, markers_file=d.get("markers_file") or "_audio/markers.json")
    if lt: e["lyrics_timing"] = "_audio/" + lt
    if d.get("bpm"): e["bpm"] = d["bpm"]
    return {"current": s, "songs": {s: e}, "_one_song_file": True}


def save(cfg, lib=None):
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}; cfg["_help"] = ("The songs being promoted (python3 _scripts/songs.py). current = the one new edits are cut to; paths are "
                                   "relative to library/; markers live only in each song's markers_file (read them with song_markers.py).")
    save_json(_cfg_path(lib), cfg)   # atomic; a change reads and saves inside one `with locked(_cfg_path()):` (other sessions add and time songs too)


def get(name=None, lib=None):
    """A song's entry with absolute paths (+ "id"), by id or name; None = the current song. None if there are no songs."""
    lib = lib or LIB; cfg = config(lib); ss = cfg["songs"]
    if not ss: return None
    key = name if name in ss else next((k for k, e in ss.items() if name and slug(e.get("name", "")) == slug(name)), None) if name else cfg.get("current")
    if key not in ss and cfg.get("_one_song_file"): key = next(iter(ss))   # an older one-song file: every name means its one song
    if key not in ss: raise KeyError("no song called %r (songs: %s)" % (name, ", ".join(ss)))
    e = dict(ss[key], id=key)
    for k in ("master", "lyrics", "lyrics_timing", "markers_file"):
        if e.get(k): e[k] = os.path.join(lib, e[k])
    e.setdefault("markers_file", os.path.join(lib, "_audio", "markers.json"))
    return e


def block(r):
    """A recipe's song block: "song". None = silent."""
    return r.get("song") or r.get("song") or None


def of_recipe(r, lib=None):
    """The song a recipe is cut to: its song block's "name", else the FIRST song (see the top of this file)."""
    m = block(r) or {}
    if m.get("name"): return get(m["name"], lib)
    ss = config(lib)["songs"]
    return get(next(iter(ss)), lib) if ss else None


def rollout(e):
    """The song's rollout doc (everything specific to it): the house's _docs/<song>-rollout.md, else library/_audio/songs/<song>/rollout.md."""
    for p in (os.path.join(ROOT, "_docs", "%s-rollout.md" % e["id"]), os.path.join(LIB, "_audio", "songs", e["id"], "rollout.md")):
        if os.path.exists(p): return p
    return None


def menu_folder(): return (layout().get("resolve_menu") or {}).get("songs") or "sandbox/Songs"


ENTRIES = (("Mark the song", "mark", '''# First run: builds a "%(tl)s" timeline with the song at the start. Press M on the moments that matter;
# drag a marker longer to make a window. Then run THIS AGAIN to save the markers, and tell Claude.'''),
           ("Copy markers to every timeline", "copy", '''# Puts the song's saved markers onto every other timeline in this project that uses the song (once per new
# project). Their old markers are backed up first.'''),
           ("Make a timeline per account", "timelines", '''# A vertical timeline per account ("<account> - <song>") with the song at the start and its markers, in each
# account's bin. Skips any that already exist.'''))


def menu(e):
    """Make the song's Resolve menu entries (Songs > <name> > Mark the song / Copy markers to every timeline).
    Returns (path of the folder's first entry, installed in Resolve?)."""
    old = os.path.join(RESOLVE_UTILITY, *menu_folder().split("/"), re.sub(r'[/:\\"]+', " ", e["name"]).strip() + ".py")
    if os.path.isfile(old):   # the one-entry layout of Sep 27 (before Copy): out of the menu, kept
        d = os.path.join(LIB, "_to_delete", "menu_old"); os.makedirs(d, exist_ok=True); shutil.move(old, os.path.join(d, os.path.basename(old)))
    first = None
    for label, action, how in ENTRIES:
        r = resolve_menu(menu_folder() + "/" + e["name"], label, '''# %s > %s (made by sandbox).
%s
import os
_r = os.path.expanduser("~/.sandbox_root")   # the sandbox folder, written by setup.sh
_root = open(_r, encoding="utf-8").read().strip() if os.path.exists(_r) else os.path.expanduser("~/Desktop/sandbox")
SONG, ACTION = %r, %r
exec(open(os.path.join(_root, "_scripts", "Song_Timeline.py"), encoding="utf-8").read())
''' % (e["name"], label, how % {"tl": e.get("timeline") or "%s markers" % e["name"]}, e["id"], action))
        first = first or r
    return first


def where(p, live): return ("Workspace > Scripts > " + " > ".join(os.path.relpath(os.path.dirname(p), RESOLVE_UTILITY).split(os.sep)) + " > Mark the song") if live else \
    "waiting for Resolve (./setup.sh installs it once Resolve is on this Mac)"


def save_markers(song, marks, source):
    """Write a song's markers (the only copy), keeping the file's own shape; the previous version goes to <its folder>/_old/."""
    import datetime
    e = get(song); p = e["markers_file"]; old = {}
    if os.path.exists(p):
        old = json.load(open(p, encoding="utf-8")); b = os.path.join(os.path.dirname(p), "_old"); os.makedirs(b, exist_ok=True)
        shutil.copy2(p, os.path.join(b, "%s_%s.json" % (os.path.splitext(os.path.basename(p))[0], datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))))
    d = {"from": source, "saved": datetime.datetime.now().isoformat(timespec="seconds"), "song": e["id"]}
    if "timeline_markers" in old: d.update(timeline_markers=marks, song_clip_markers=[])   # the house file's older shape (its Resolve scripts read it)
    else: d["markers"] = marks
    os.makedirs(os.path.dirname(p), exist_ok=True); save_json(p, d, indent=2, ensure_ascii=True)   # the only copy: never left half-written
    return p


def lyrics_text(src, dst):
    """A lyric sheet as plain text (.txt as is; .rtf .doc .docx .html through macOS textutil)."""
    if src.lower().endswith(".txt"): shutil.copyfile(src, dst)
    else: subprocess.run(["textutil", "-convert", "txt", "-output", dst, src], check=True, capture_output=True)


def add(src, name, lyrics=None, make_current=True):
    s = slug(name)
    if not os.path.isfile(src): sys.exit("no such file: %s" % src)
    d = os.path.join(LIB, "_audio", "songs", s); os.makedirs(d, exist_ok=True)
    master = os.path.join(d, os.path.basename(src)); rel = lambda p: os.path.relpath(p, LIB); audio = "new"
    if os.path.exists(master):   # the same file name again: the same audio changes nothing; a new mix replaces it, the old one kept (never deleted)
        if os.path.samefile(src, master) or filecmp.cmp(src, master, shallow=False): audio = "same"
        else:
            import datetime
            old = os.path.join(LIB, "_to_delete", "replaced_songs", "%s_%s" % (s, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
            os.makedirs(old, exist_ok=True); shutil.move(master, os.path.join(old, os.path.basename(master))); audio = "replaced: the old version is in library/%s" % rel(old)
    if not os.path.exists(master):
        if subprocess.run(["cp", "-c", src, master], capture_output=True).returncode: shutil.copy2(src, master)   # APFS clone when it can
    e = dict(name=name, master=rel(master), markers_file=rel(os.path.join(d, "markers.json")))   # what this sets: onto the entry as it is when saved
    if lyrics:
        lyrics_text(lyrics, os.path.join(d, "lyrics.txt")); e["lyrics"] = rel(os.path.join(d, "lyrics.txt"))
    import beat_map
    try:
        bpm = beat_map.estimate_bpm(master, start=30)
        if bpm: e["bpm"] = bpm
    except Exception: pass
    with locked(_cfg_path()):   # read again here: another session may have changed song.json while the tempo was measured
        cfg = config(); e = cfg["songs"][s] = dict(cfg["songs"].get(s) or {}, **e)
        if make_current or not cfg.get("current"): cfg["current"] = s
        save(cfg)
    subprocess.run([sys.executable, os.path.join(ROOT, "_scripts", "beat_map.py"), "--name", s, "--song", "--out", os.path.join(d, "song_map.png")], capture_output=True)
    p, live = menu(get(s))
    print("Added %s (%s): library/%s%s, %s, waveform %s"
          % (name, s, rel(master), ", lyrics" if lyrics else "", "tempo about %s BPM (an estimate: check by ear)" % e["bpm"] if e.get("bpm") else
             "no clear beat found (find the moments on the waveform)", rel(os.path.join(d, "song_map.png"))))
    print("Markers: %s. Run it once to build the timeline, add markers (M), then run it AGAIN to save them." % where(p, live))
    if audio == "same": print("Audio: the same file as before, nothing changed.")
    elif audio != "new": print("Audio %s. Its markers and word timings were made on the old version: check them against this one (song_markers.py, lyrics_timing.py)." % audio)
    if lyrics: print("Word timings for on-screen lyrics: python3 _scripts/lyrics_timing.py %s" % s)
    if os.path.exists(os.path.join(ROOT, "_docs", "song-rollout-template.md")):
        print("Its rollout doc: _docs/%s-rollout.md, written with them from _docs/song-rollout-template.md." % s)
    else:
        print("Notes for this song (what it's about, what each marker means, lyric fixes), once there's something to write down: "
              "library/_audio/songs/%s/rollout.md" % s)
    return s


def main():
    a = sys.argv[1:]
    if a[:1] == ["add"] and len(a) >= 2:
        arg = lambda k: a[a.index(k) + 1] if k in a else None
        if not arg("--name"): sys.exit("--name is required: the song's real title. Ask them if it isn't clear (never take it from the file name).")
        add(a[1], arg("--name"), arg("--lyrics"), "--keep-current" not in a)
    elif a[:1] == ["use"] and len(a) == 2:
        with locked(_cfg_path()): cfg = config(); e = get(a[1]); cfg["current"] = e["id"]; save(cfg)
        print("New edits are cut to %s." % e["name"])
    elif a[:1] == ["menu"]:
        for k in config()["songs"]:
            p, live = menu(get(k)); print("  %s: %s" % (get(k)["name"], where(p, live)))
    elif not a:
        cfg = config()
        if not cfg["songs"]: return print("No songs yet. Drop one into the chat (and its lyrics, if you want them on screen).")
        import song_markers as SM
        for k in cfg["songs"]:
            e = get(k); ms = SM.load(song=k); ws = SM.windows(ms)
            ro = rollout(e)
            print("%s %-22s %s | %s | %s | %s | %s" % ("*" if k == cfg["current"] else " ", k, e["name"],
                  "master ok" if os.path.exists(e["master"]) else "MASTER MISSING",
                  "%d markers, %d windows" % (len(ms), len(ws)) if ms else "no markers yet",
                  "lyrics timed" if e.get("lyrics_timing") and os.path.exists(e["lyrics_timing"]) else ("lyrics, not timed" if e.get("lyrics") else "no lyrics"),
                  "rollout: " + os.path.relpath(ro, ROOT) if ro else "no rollout doc yet"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__": main()
