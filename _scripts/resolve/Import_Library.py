#!/usr/bin/env python
"""Import Library - DaVinci Resolve script (Workspace > Scripts > Import_Library). Safe to run any time, in any project.
Brings the sandbox library into the open project's Media Pool:
  1. Bins mirror the library folders (layout.json "resolve_import"; default the footage folders), subfolders included.
  2. Imports only what isn't in the project yet (matched by file path anywhere in the Media Pool). Never removes a clip.
  3. Tags every library clip, new or already there, from the clip index: Keywords = the names of the accounts it suits ("fits")
     and its type (film / real / graphic); Description = its one-liner. So you can search the Media Pool by what's in the shot,
     and Resolve makes a Smart Bin per keyword by itself (Smart Bins > Keywords > <account name>, film …): every clip that suits
     an account. Re-run after new clips come in or tags change.
  4. A clip whose file moved inside the library is relinked to where the file is now, only when its old path was in the library
     and its name is in the library once. A clip from anywhere else (a card that isn't plugged in, another drive) is never
     touched, and one whose name matches more than one file is left offline and named: a name alone can't say which file it
     was (Sep 29: an unplugged card's C0001.MP4 was pointed at another shoot's). A copy in library/_to_delete doesn't count: what's there was put aside, so the file in the footage folders is the clip's. A relinked clip that sat in the bin mirroring
     its old folder moves to the bin for its new one, and that old bin (then its parents) goes once it holds nothing and its
     folder is gone from the library. A clip in a bin someone made stays there, and those bins are never removed.
It makes no timelines (a song's markers timeline comes from Songs > <song> > Mark the song)."""
import json, os, sys

_rf = os.path.expanduser("~/.sandbox_root")   # the sandbox folder, written by setup.sh
ROOT = open(_rf, encoding="utf-8").read().strip() if os.path.exists(_rf) else os.path.expanduser("~/Desktop/sandbox")
sys.path.insert(0, os.path.join(ROOT, "_scripts"))
from sandbox_paths import LIB, layout, accounts, VIDEO_EXT, IMAGE_EXT  # noqa: E402

MEDIA_EXT = tuple(VIDEO_EXT) + tuple(IMAGE_EXT) + (".mkv", ".webm", ".tif", ".tiff")


def _resolve():
    g = globals()
    if "resolve" in g: return g["resolve"]
    try: return bmd.scriptapp("Resolve")  # noqa: F821
    except NameError:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")


def _bin(mp, parent, name):
    return next((f for f in parent.GetSubFolderList() or [] if f.GetName() == name), None) or mp.AddSubFolder(parent, name)


def _clips(folder):
    out = list(folder.GetClipList() or [])
    for sub in folder.GetSubFolderList() or []: out += _clips(sub)
    return out


def _where(folder, chain=()):
    """[(bin path as a tuple of names, bin, clip)] for every clip in the Media Pool."""
    out = [(chain, folder, c) for c in folder.GetClipList() or []]
    for sub in folder.GetSubFolderList() or []: out += _where(sub, chain + (sub.GetName(),))
    return out


def _chain(p):
    """The bin path (a tuple of names) that mirrors the library folder a file is in."""
    return tuple(os.path.relpath(os.path.dirname(p), LIB).split(os.sep))


def _empty_bins(root, emptied):
    """Bins this run emptied, deepest first: a bin that mirrored a relinked clip's old folder (only those lose clips here) and
    now holds nothing (no clips, no timelines, no bins), then a parent left with nothing because of that; never one whose
    folder is still in the library. Bins are known by their name path (Resolve hands out new objects on every call). A bin
    that was already empty, or that someone made, is never touched."""
    out = []
    def walk(f, chain):
        subs = f.GetSubFolderList() or []
        gone = [walk(sb, chain + (sb.GetName(),)) for sb in subs]
        if (chain and not (f.GetClipList() or []) and all(gone) and (chain in emptied or (subs and all(gone)))
                and not os.path.isdir(os.path.join(LIB, *chain))):
            out.append(f); return True
        return False
    walk(root, ())
    return out


def _index():
    """file path (absolute) -> index entry, from the clip index and the lab index."""
    out = {}
    for rel in ("_lab/lab_index.json", "_reference/clip_index.json"):
        p = os.path.join(LIB, rel)
        if os.path.exists(p):
            for v in json.load(open(p, encoding="utf-8")).get("clips", {}).values():
                if v.get("file"): out[os.path.join(LIB, v["file"])] = v
    return out


def main():
    project = _resolve().GetProjectManager().GetCurrentProject()
    if not project: return print("Open a project first, then run this again.")
    mp = project.GetMediaPool(); root = mp.GetRootFolder()
    folders = layout().get("resolve_import") or layout().get("source_folders") or []
    # clips whose file moved inside the library (e.g. into a folder in _internal content): relinked to where the file is now,
    # only when the old path was in the library and the name is there once. By name alone, an unplugged card's C0001.MP4 was
    # pointed at another shoot's C0001.MP4 (Sep 29). Any other offline clip is left as it is.
    lib = os.path.abspath(LIB) + os.sep; lost = {}   # name (any case) -> {old path: [clips]}
    for c in _clips(root):
        p = c.GetClipProperty("File Path") or ""
        if p and os.path.abspath(p).startswith(lib) and not os.path.exists(p): lost.setdefault(os.path.basename(p).lower(), {}).setdefault(p, []).append(c)
    found = {}   # those names wherever they are in the library now, _to_delete left out
    if lost:
        for dp, dn, fn in os.walk(LIB):
            if os.path.abspath(dp) == os.path.abspath(LIB): dn[:] = [d for d in dn if d != "_to_delete"]
            for f in fn:
                if f.lower() in lost and not f.startswith("."): found.setdefault(f.lower(), []).append(os.path.join(dp, f))
    relink, twice = {}, []
    for name, olds in sorted(lost.items()):
        now = found.get(name, [])
        if not now: continue                                                       # gone from the library: stays offline
        if len(now) > 1 or len(olds) > 1: twice.append(os.path.basename(now[0])); continue   # which file is whose can't be told
        rel = os.path.relpath(os.path.dirname(now[0]), LIB)
        if not any(rel == t or rel.startswith(t + os.sep) for t in folders): continue        # not where the bins mirror
        relink.setdefault(os.path.dirname(now[0]), []).extend(olds.items())
    relinked, n_relinked = {}, 0   # a relinked clip's path now -> the bin path that mirrored its old folder
    for d, items in relink.items():
        mp.RelinkClips([c for old, cs in items for c in cs], d)
        for old, cs in items:
            for c in cs:
                p = c.GetClipProperty("File Path") or ""
                if p != old and os.path.exists(p): relinked[p] = _chain(old); n_relinked += 1
    have = {c.GetClipProperty("File Path"): c for c in _clips(root) if c.GetClipProperty("File Path")}
    added = 0
    for top in folders:
        base = os.path.join(LIB, top)
        if not os.path.isdir(base): continue
        b = root
        for part in top.split("/"): b = _bin(mp, b, part)   # the top bins always show (_external content too, even while it's empty)
        for dp, dn, fn in os.walk(base):
            dn[:] = sorted(d for d in dn if not d.startswith(("_sheets", ".")))
            files = sorted(os.path.join(dp, f) for f in fn if f.lower().endswith(MEDIA_EXT) and not f.startswith("."))
            new = [f for f in files if f not in have]
            if not new: continue
            b = root
            for part in os.path.relpath(dp, LIB).split(os.sep): b = _bin(mp, b, part)
            mp.SetCurrentFolder(b)
            for c in mp.ImportMedia(new) or []:
                have[c.GetClipProperty("File Path")] = c; added += 1
    # a clip relinked in this run that sat in the bin mirroring its old folder moves to the bin for its new one. Only those:
    # moving every library clip to its mirror bin took picks out of the bins people made, then removed those bins (Sep 29).
    moved, emptied = 0, set()
    for chain, b, c in _where(root):
        p = c.GetClipProperty("File Path") or ""
        if relinked.get(p) != chain or _chain(p) == chain: continue
        t = root
        for part in _chain(p): t = _bin(mp, t, part)
        if mp.MoveClips([c], t): moved += 1; emptied.add(chain)
    gone = _empty_bins(root, emptied)
    if gone: mp.DeleteFolders(gone)
    names = {str(a.get("id")): a.get("name") or str(a.get("id")) for a in accounts()}
    idx = _index(); tagged = 0
    for path, c in have.items():
        v = idx.get(path)
        if not v: continue
        kw = [names.get(str(a), str(a)) for a in (v.get("fits") or [])] + ([v["source_type"]] if v.get("source_type") else [])   # account names, not ids
        # no space after the commas: Resolve keeps it, so " name" became a second keyword next to "name"
        meta = {"Keywords": ",".join(k.strip() for k in kw if k.strip()), "Description": v.get("line") or v.get("description") or ""}
        if any(c.GetMetadata(k) != val for k, val in meta.items()):
            if c.SetMetadata(meta): tagged += 1
    print("Import Library: %d new clip%s imported, %d tagged (accounts as Keywords, one-liner as Description)%s%s%s. "
          "Each account's clips: Smart Bins > Keywords > <account name>.%s" % (added, "" if added == 1 else "s", tagged,
          ", %d relinked to where they live now" % n_relinked if n_relinked else "", ", %d moved into the folder bins" % moved if moved else "",
          ", %d emptied bin%s removed" % (len(gone), "" if len(gone) == 1 else "s") if gone else "",
          " Left offline, as the name matches more than one file or clip in the library (relink by hand): %s%s." % (
              ", ".join(twice[:8]), " and %d more" % (len(twice) - 8) if len(twice) > 8 else "") if twice else ""))


main()
