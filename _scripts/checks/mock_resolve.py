"""A stand-in for DaVinci Resolve's scripting API: records what the timeline engine asks for, so a recipe can be checked
without Resolve. Only what the engine uses is here. run_engine(recipe, engine) -> (calls, timeline, printed text); Mock() for scripts run in a row."""
import contextlib, io, json, os, subprocess

START = 86400   # Resolve timelines start at 01:00:00:00


def _probe(p):
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
                                   "-of", "json", p], capture_output=True, text=True).stdout or "{}")
    ss = j.get("streams") or []; v = next((s for s in ss if s.get("codec_type") == "video"), None)
    d = float((j.get("format") or {}).get("duration") or 0)
    if v:
        a, b = v["r_frame_rate"].split("/"); fps = float(a) / float(b or 1)
    else:
        fps = 48000.0
    return dict(fps=fps, frames=int(d * fps), res="%sx%s" % (v["width"], v["height"]) if v else "")


def run_engine(recipe, engine, fit_mode="scaleToFit"):
    r = Mock(fit_mode)
    out = r.run(engine, RECIPE=recipe)
    return r.calls, r.timeline(), out


def Mock(fit_mode="scaleToFit"):
    """A fresh stand-in Resolve with one open project. .run(script, **globals) runs a script in it (the project persists
    between runs, like running two menu entries in a row); .timeline() is the newest timeline; .calls is what was asked."""
    calls = []

    class Item:
        def __init__(s, clip, n): s.clip, s.n, s.props, s.markers = clip, n, {}, {}
        def GetDuration(s): return s.n
        def GetStart(s): return s.start                     # where it sits on the timeline (timeline frames, from START)
        def GetLeftOffset(s): return s.left                 # how far into its own media it starts (timeline frames)
        def DeleteMarkerAtFrame(s, f): return s.markers.pop(f, None) is not None
        def GetMediaPoolItem(s): return s.clip
        def GetMarkers(s): return dict(s.markers)
        def AddMarker(s, f, color, name, note, dur, custom=""):   # like Resolve: one marker a frame
            if f in s.markers: return False
            s.markers[f] = {"color": color, "name": name, "note": note, "duration": dur, "customData": custom}; return True
        def DeleteMarkersByColor(s, c): [s.markers.pop(f) for f, m in list(s.markers.items()) if c == "All" or m["color"] == c]; return True
        def SetProperty(s, k, v): s.props[k] = v; calls.append(("prop", os.path.basename(s.clip.path), k, v)); return True

    class Clip:
        def __init__(s, path): s.path = path; s.p = _probe(path); s.meta = {}
        def GetMetadata(s, k=None): return s.meta.get(k, "") if k else dict(s.meta)
        def SetMetadata(s, k, v=None): s.meta.update(k if isinstance(k, dict) else {k: v}); calls.append(("meta", os.path.basename(s.path))); return True
        def GetClipProperty(s, k):
            return {"File Path": s.path, "FPS": str(s.p["fps"]), "Frames": str(s.p["frames"]), "Resolution": s.p["res"]}.get(k, "")

    class Folder:
        def __init__(s, name): s.name, s.clips, s.subs = name, [], []
        def GetClipList(s): return s.clips
        def GetSubFolderList(s): return s.subs
        def GetName(s): return s.name

    class TL:
        def __init__(s, name): s.name, s.settings, s.tracks, s.markers, s.items = name, {}, 1, [], []
        def GetName(s): return s.name
        def SetSetting(s, k, v): s.settings[k] = v; return True
        def GetSetting(s, k): return s.settings.get(k, "30")
        def GetStartFrame(s): return START
        def GetTrackCount(s, t): return max([s.tracks] + [it.track[1] for it in s.items if it.track[0] == ("V" if t == "video" else "A")])
        def AddTrack(s, t): s.tracks += 1; calls.append(("addtrack", t)); return True
        def DeleteClips(s, items): calls.append(("delete", len(items))); [s.items.remove(i) for i in items if i in s.items]; return True
        def AddMarker(s, f, color, name, note, dur, custom=""):   # like Resolve: one marker a frame
            if any(m[0] == f for m in s.markers): return False
            s.markers.append((f, color, name, note, dur, custom)); return True
        def GetMarkers(s): return {f: {"color": c, "name": n, "note": no, "duration": d, "customData": cu} for (f, c, n, no, d, cu) in s.markers}
        def DeleteMarkersByColor(s, c): s.markers[:] = [m for m in s.markers if not (c == "All" or m[1] == c)]; return True
        def DeleteMarkerAtFrame(s, f): n = len(s.markers); s.markers[:] = [m for m in s.markers if m[0] != f]; return len(s.markers) < n
        def GetItemListInTrack(s, kind, i): return [it for it in s.items if it.track == ("V" if kind == "video" else "A", i)]

    class MP:
        def __init__(s): s.root = Folder("Master"); s.cur = s.root; s.tl = None
        def GetRootFolder(s): return s.root
        def AddSubFolder(s, parent, name): f = Folder(name); parent.subs.append(f); return f
        def SetCurrentFolder(s, f): s.cur = f
        def ImportMedia(s, paths): cs = [Clip(p) for p in paths if os.path.exists(p)]; s.cur.clips += cs; return cs
        def CreateEmptyTimeline(s, name): s.tl = TL(name); proj.tls.append(s.tl); return s.tl
        def _folders(s, f=None):
            f = f or s.root; out = [f]
            for sub in f.subs: out += s._folders(sub)
            return out
        def RelinkClips(s, items, folder):
            ok = False
            for c in items:
                p = os.path.join(folder, os.path.basename(c.path))
                if os.path.exists(p): c.path = p; ok = True
            calls.append(("relink", len(items), folder)); return ok
        def MoveClips(s, clips, target):
            for c in clips:
                for f in s._folders():
                    if c in f.clips: f.clips.remove(c)
                target.clips.append(c)
            calls.append(("move", len(clips), target.name)); return True
        def DeleteFolders(s, folders):
            for d in folders:
                for f in s._folders():
                    if d in f.subs: f.subs.remove(d)
            calls.append(("delete_bins", [d.name for d in folders])); return True
        def AppendToTimeline(s, infos):
            out = []
            infos = [i if isinstance(i, dict) else {"mediaPoolItem": i, "startFrame": 0, "endFrame": max(0, i.p["frames"] - 1),
                                                    "mediaType": 1 if i.p["res"] else 2, "trackIndex": 1, "recordFrame": START} for i in infos]
            for i in infos:
                n = i["endFrame"] - i["startFrame"] + 1
                calls.append(("append", "V" if i["mediaType"] == 1 else "A", i["trackIndex"], i["mediaPoolItem"].path, i["startFrame"], n, i["recordFrame"] - START))
                it = Item(i["mediaPoolItem"], n); it.track = ("V" if i["mediaType"] == 1 else "A", i["trackIndex"]); it.rec = i["recordFrame"] - START
                it.start = i["recordFrame"]; it.left = round(i["startFrame"] / i["mediaPoolItem"].p["fps"] * float(s.tl.GetSetting("timelineFrameRate")))
                s.tl.items.append(it); out.append(it)
            return out

    class Project:
        def __init__(s): s.mp = MP(); s.tls = []
        def GetMediaPool(s): return s.mp
        def GetTimelineCount(s): return len(s.tls)
        def GetTimelineByIndex(s, i): return s.tls[i - 1]
        def SetCurrentTimeline(s, t): return True
        def GetSetting(s, k): return fit_mode if k == "timelineInputResMismatchBehavior" else ""
        def GetName(s): return "check"

    class PM:
        def GetCurrentProject(s): return proj

    class R:
        def GetProjectManager(s): return PM()

    class Runner:
        def __init__(s): s.calls, s.project = calls, proj
        def timeline(s): return proj.tls[-1] if proj.tls else None
        def run(s, script, **g):
            """Like Resolve's own Python, text files open as ASCII unless the script names an encoding (Resolve does this;
            a Mac's normal Python reads UTF-8, which hid the bug until the person ran Import_Library, Sep 27)."""
            import builtins
            real = builtins.open
            def ascii_open(f, mode="r", *a, **k):
                if "b" not in mode and "encoding" not in k and len(a) < 2: k["encoding"] = "ascii"
                return real(f, mode, *a, **k)
            buf = io.StringIO(); builtins.open = ascii_open
            try:
                with contextlib.redirect_stdout(buf):
                    exec(compile(real(script, encoding="utf-8").read(), script, "exec"), dict(g, resolve=R(), __name__="__resolve__"))
            finally:
                builtins.open = real
            return buf.getvalue()

    proj = Project()
    return Runner()
