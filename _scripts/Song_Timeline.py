"""A song's markers, set in Resolve. Runs from the song's menu entry (Workspace > Scripts > <songs menu> > <Song Name>), which
songs.py makes; don't run this file directly.
First run: builds the "<Song Name> markers" timeline with the song alone at the start (and any markers saved before, so they
can be moved). Press M on each moment an edit could start on; drag a marker longer (or set its Duration) to make a window an
edit stays inside. Name or colour them however you like.
Run the SAME entry AGAIN when you're done: that saves the markers to the song's markers file (the only copy; the previous
version is kept in _old/). Then tell Claude.
A song whose markers live on a timeline you already have ("timeline" in song.json) skips the building and saves from that one.
"Copy markers to every timeline" (ACTION = "copy"): puts the song's markers onto every other timeline in the open project that
uses the song (the song's file is on it, or its name contains the song's "copy_to" text), at the same seconds, replacing the
markers they had (those are backed up next to the markers file, in _old/) except yellow notes that aren't the song's (a
piece's notes on what its render did: never removed; the song's own yellow markers are replaced like the rest). A timeline
whose song doesn't play from its own start at 0:00 (a piece cut from the middle of the song, a trimmed edit) is left alone
and named: song seconds would land in the wrong places there, and its markers are its own.
Needed once per new project.
"Make a timeline per account" (ACTION = "timelines"): a vertical timeline per account ("<account> - <song>") with the song and
its markers, in the account's bin; skips any that exist."""
import os, sys

_rf = os.path.expanduser("~/.sandbox_root")   # the sandbox folder, written by setup.sh
_ROOT = open(_rf, encoding="utf-8").read().strip() if os.path.exists(_rf) else os.path.expanduser("~/Desktop/sandbox")
sys.path.insert(0, os.path.join(_ROOT, "_scripts"))
import songs  # noqa: E402
import song_markers  # noqa: E402


def _resolve():
    g = globals()
    if "resolve" in g: return g["resolve"]
    try: return bmd.scriptapp("Resolve")  # noqa: F821
    except NameError:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")


def _clips(folder):
    out = list(folder.GetClipList() or [])
    for sub in folder.GetSubFolderList() or []: out += _clips(sub)
    return out


def _read(tl):
    fps = float(tl.GetSetting("timelineFrameRate") or 30)
    return [dict(sec=round(f / fps, 3), dur_sec=round(m.get("duration", 1) / fps, 3), color=m.get("color"), name=m.get("name") or "",
                 note=m.get("note") or "", custom=m.get("customData") or "") for f, m in sorted((tl.GetMarkers() or {}).items())]


def _known(e):
    """Every marker the song has had, as (name, seconds): its saved set now and each earlier one (save_markers keeps them in
    _old/). An earlier copy put one of these sets on a timeline, so its markers are found even after the song's own marker
    was renamed, moved or removed."""
    import glob
    p = e["markers_file"]; old = glob.glob(os.path.join(os.path.dirname(p), "_old", glob.escape(os.path.splitext(os.path.basename(p))[0]) + "_*.json"))
    return {(m["name"], m["sec"]) for f in [p] + old for m in song_markers.load_file(f)}


def _put(target, marks, fps, e, known=()):
    """Replace a timeline's (or clip's) song markers with the song's current ones. The song's: a marker a copy wrote (customData
    "song:<id>", from now on), or one with the name and second of a marker the song has had (copies made before the tag).
    Every other colour goes too, as it always did; a yellow note that isn't the song's stays (what a piece's render did)."""
    tag = "song:%s" % e["id"]
    for f, m in (target.GetMarkers() or {}).items():
        song = m.get("customData") == tag or any(n == (m.get("name") or "") and abs(s - f / fps) <= 1.5 / fps for n, s in known)
        if m.get("color") != "Yellow" or song: target.DeleteMarkerAtFrame(f)
    add = lambda f, m: target.AddMarker(f, m.get("color") or "Blue", m.get("name") or "", m.get("note") or "",
                                        max(1, int(round((m.get("dur_sec") or 0) * fps))), tag)
    return sum(1 for m in marks if any(add(int(round(m["sec"] * fps)) + d, m) for d in range(int(fps))))   # one marker a frame: a taken one, the next free


def _song_items(tl, master):
    out = []
    for kind in ("audio", "video"):
        for i in range(1, (tl.GetTrackCount(kind) or 0) + 1):
            for it in tl.GetItemListInTrack(kind, i) or []:
                mpi = it.GetMediaPoolItem()
                if mpi and mpi.GetClipProperty("File Path") == master: out.append(it)
    return out


def _from_zero(t, master):
    """Why t is left out of a copy: where its song plays, when that isn't from the song's own start at 0:00. Empty = copy onto
    it (a timeline picked by its name, without the song's file on it, is copied onto as before)."""
    fps = float(t.GetSetting("timelineFrameRate") or 30); t0 = t.GetStartFrame(); where = []
    for it in _song_items(t, master):
        at, off = it.GetStart() - t0, it.GetLeftOffset()
        if at <= 0 and off <= 0: return ""
        where.append("the song from %.2fs of itself, at %.2fs" % (off / fps, at / fps))
    return "; ".join(where)


def copy(e, project, source):
    """The song's markers onto every other timeline in the project that uses it."""
    import datetime, json
    if source is not None and _read(source):
        songs.save_markers(e["id"], _read(source), "%s / %s" % (project.GetName(), source.GetName()))   # its markers timeline is the latest word
    marks = song_markers.load(song=e["id"])
    if not marks: return print("No markers saved for %s yet: run 'Mark the song' first (twice: once to set up, once to save)." % e["name"])
    try: clipm = json.load(open(e["markers_file"], encoding="utf-8")).get("song_clip_markers") or []
    except (OSError, ValueError): clipm = []
    key = (e.get("copy_to") or "").lower()
    tls = [project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)]
    targets = [t for t in tls if (source is None or t.GetName() != source.GetName())
               and ((key and key in t.GetName().lower()) or _song_items(t, e["master"]))]
    why = [(t, _from_zero(t, e["master"])) for t in targets]; alone = [(t, w) for t, w in why if w]; targets = [t for t, w in why if not w]
    if alone: print("Left alone (the song doesn't play from its own start at 0:00 there, so its markers are its own):\n" + "\n".join("  %s: %s" % (t.GetName(), w) for t, w in alone))
    if not targets: return print("No other timeline in this project uses %s from its start (its file isn't on any%s)." % (e["name"], ", and none has '%s' in its name" % key if key else ""))
    b = os.path.join(os.path.dirname(e["markers_file"]), "_old"); os.makedirs(b, exist_ok=True)
    bp = os.path.join(b, "copy_backup_%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    json.dump({"project": project.GetName(), "timelines": {t.GetName(): {"timeline": {str(k): v for k, v in (t.GetMarkers() or {}).items()},
               "song_clips": [{str(k): v for k, v in (it.GetMarkers() or {}).items()} for it in _song_items(t, e["master"])]} for t in targets}},
              open(bp, "w", encoding="utf-8"), indent=2)
    known = _known(e)
    for t in targets:
        fps = float(t.GetSetting("timelineFrameRate") or 30); n = _put(t, marks, fps, e, known); c = 0
        if clipm:
            for it in _song_items(t, e["master"]): c += _put(it, clipm, fps, e)
        print("%-34s %d markers%s" % (t.GetName(), n, ", %d on the song clip" % c if clipm else ""))
    print("Copied %s's markers onto %d timeline%s. Their old markers are backed up in %s" % (e["name"], len(targets), "" if len(targets) == 1 else "s", bp))


def _song_clip(mp, root, e):
    """The song in the Media Pool (imported into a "Songs" bin the first time)."""
    clip = next((c for c in _clips(root) if c.GetClipProperty("File Path") == e["master"]), None)
    if clip: return clip
    songs_bin = next((f for f in root.GetSubFolderList() or [] if f.GetName() == "Songs"), None) or mp.AddSubFolder(root, "Songs")
    mp.SetCurrentFolder(songs_bin)
    return (mp.ImportMedia([e["master"]]) or [None])[0]


def per_account(e, project):
    """A vertical 1080x1920 timeline per account in library/profile.json ("<account> - <song>"), in that account's bin,
    with the song at the start and its saved markers on it. Skips any that already exist."""
    import sandbox_paths
    accts = sandbox_paths.accounts()
    if not accts: return print("No accounts in library/profile.json yet: tell Claude which accounts you make videos for, then run this again.")
    mp = project.GetMediaPool(); root = mp.GetRootFolder()
    clip = _song_clip(mp, root, e)
    if not clip: return print("Couldn't import the song: %s" % e["master"])
    marks = song_markers.load(song=e["id"]); names = {project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)}
    made, there = [], []
    for a in accts:
        acct = a.get("name") or a.get("id"); name = "%s - %s" % (acct, e.get("menu") or e["name"])
        if name in names: there.append(name); continue
        b = next((f for f in root.GetSubFolderList() or [] if f.GetName() == acct), None) or mp.AddSubFolder(root, acct)
        mp.SetCurrentFolder(b); tl = mp.CreateEmptyTimeline(name)
        if not tl: print("Couldn't make '%s'." % name); continue
        project.SetCurrentTimeline(tl)
        for k, v in (("useCustomSettings", "1"), ("timelineFrameRate", "30"), ("timelineResolutionWidth", "1080"), ("timelineResolutionHeight", "1920")): tl.SetSetting(k, v)
        mp.AppendToTimeline([clip]); _put(tl, marks, float(tl.GetSetting("timelineFrameRate") or 30), e); made.append(name)
    print("Made %d timeline%s (%s) with %s and its %d markers.%s" % (len(made), "" if len(made) == 1 else "s", ", ".join(made) or "none new", e["name"], len(marks),
          " Already there: %s." % ", ".join(there) if there else ""))
    if not marks: print("No markers saved for %s yet: 'Mark the song' first (run it twice), then 'Copy markers to every timeline' puts them on these." % e["name"])


def main(song, action="mark"):
    e = songs.get(song)
    project = _resolve().GetProjectManager().GetCurrentProject()
    if not project: return print("Open a project first (any project), then run this again.")
    name = e.get("timeline") or "%s markers" % e["name"]
    tl = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1) if project.GetTimelineByIndex(i).GetName() == name), None)
    if action == "copy": return copy(e, project, tl)
    if action == "timelines": return per_account(e, project)
    if tl is None:
        if e.get("timeline"): return print("No timeline called '%s' in this project. Open the project that has it, then run this again." % name)
        mp = project.GetMediaPool(); root = mp.GetRootFolder()
        clip = _song_clip(mp, root, e)
        songs_bin = next((f for f in root.GetSubFolderList() or [] if f.GetName() == "Songs"), None) or mp.AddSubFolder(root, "Songs")
        mp.SetCurrentFolder(songs_bin)
        if not clip: return print("Couldn't import the song: %s" % e["master"])
        tl = mp.CreateEmptyTimeline(name)
        if not tl: return print("Couldn't make the timeline '%s'." % name)
        tl.SetSetting("useCustomSettings", "1"); tl.SetSetting("timelineFrameRate", "30")
        mp.AppendToTimeline([clip])
        project.SetCurrentTimeline(tl)
        fps = float(tl.GetSetting("timelineFrameRate") or 30); old = song_markers.load(song=e["id"])
        for m in old:
            tl.AddMarker(int(round(m["sec"] * fps)), m["color"] or "Blue", m["name"], m["note"], max(1, int(round(m["dur_sec"] * fps))), m.get("custom") or "")
        print("Made the timeline '%s' with %s at the start%s.\n"
              "Press M on each moment an edit could start on (the drop, a chorus, a line you love). Drag a marker longer (or set its\n"
              "Duration) to make a window an edit stays inside.\n"
              "When you're done, run this same script AGAIN to save your markers, then tell Claude."
              % (name, e["name"], " and the %d markers saved before" % len(old) if old else ""))
        return
    marks = _read(tl)
    if not marks: return print("No markers on '%s' yet. Press M on the moments that matter, then run this again." % name)
    p = songs.save_markers(e["id"], marks, "%s / %s" % (project.GetName(), tl.GetName()))
    n = len(song_markers.windows(song_markers.load_file(p)))
    print("Saved %d markers (%d window%s) for %s. Tell Claude you're done." % (len(marks), n, "" if n == 1 else "s", e["name"]))


main(globals().get("SONG"), globals().get("ACTION") or "mark")
