"""Where things live (v0.3+). One place, so nothing is hard-coded anywhere else.
ROOT = the sandbox folder (git: code, docs, rules). LIB = ROOT/library (each person's footage, indexes and lab; never in git).
Every data path stored in an index or recipe (e.g. "_internal content/x.mp4") is relative to LIB.
Override the library location with SANDBOX_LIBRARY=/path if it lives elsewhere.
layout() = ROOT/layout.json: the library folders setup makes, and the source folders indexing reads.
profile() = LIB/profile.json: who's working, their accounts (each with an exports folder), Gmail, Pinterest boards.
use_counts() = how often each clip and pin has been in an edit, the one count every listing shows; posted_on() = the accounts
an index entry was posted on (retired there only).
edit_json() / save_json() / append_csv() / write_atomic(): the one safe way to write an index or a catalog (see below)."""
import contextlib, copy, csv, errno, fcntl, io, json, os, sys, threading, time
if {"-h", "--help"} & set(sys.argv[1:]):   # every script: --help prints its own docstring and stops, before anything runs or writes
    print((getattr(sys.modules.get("__main__"), "__doc__", None) or "No help for this script: see CLAUDE.md, Key files.").strip()); sys.exit(0)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.environ.get("SANDBOX_LIBRARY") or os.path.join(ROOT, "library")
VIDEO_EXT = (".mp4", ".mov", ".m4v")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")


def _load(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def layout():
    return _load(os.path.join(ROOT, "layout.json"), {"source_folders": [], "folders": []})


def profile():
    return _load(os.path.join(LIB, "profile.json"), {})


def accounts():
    """[{id, name, platform, what, exports}] from the profile (older profiles: tiktok_accounts {id: handle})."""
    p = profile()
    if p.get("accounts"): return p["accounts"]
    return [dict(id=k, name=v, platform="tiktok") for k, v in (p.get("tiktok_accounts") or {}).items()]


def find_account(ref):
    """The account an edit belongs to: matched on id, name or handle (case and @ ignored)."""
    ref = str(ref or "").lower().lstrip("@")
    for a in accounts():
        if ref and ref in {str(a.get(k) or "").lower().lstrip("@") for k in ("id", "name", "handle")}: return a
    return None


def round_order(path):
    """Sort key for lab round folders, by number: R06 < R06.5 < R06.9 < R06.9.8 < R07 (as plain text, "R06_..." sorts after
    "R06.9.8_...", so the round before R07 looked like R06). library_view.py and shortlist_check.py sort with it."""
    import re
    n = os.path.basename(os.path.normpath(path)); m = re.match(r"R(\d+(?:\.\d+)*)", n)
    return (tuple(int(x) for x in m.group(1).split(".")) if m else (), n)


ROUNDS = os.path.join(LIB, "_lab", "rounds")
ROUND_KINDS = tuple(layout().get("round_folders") or ())   # layout.json: folders in _lab/rounds that rounds are sorted into, if a team sorts them (none listed = every round sits straight in _lab/rounds)


def round_dirs():
    """{round name: [its folders]} for every lab round: the ones in layout.json's "round_folders" and the ones straight in
    _lab/rounds. A round has one folder, unless its pieces were sorted between those folders: then the same name sits in
    each, and each holds its own pieces whole (recipe, render, plan) with its own manifest, review and forecast seal.
    The one place a round is looked up, so the review page and every script find a round wherever it sits. A round's
    name is its own in all of them: numbers carry on across the folders."""
    out = {}
    for base in [os.path.join(ROUNDS, k) for k in ROUND_KINDS] + [ROUNDS]:
        for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            if d not in ROUND_KINDS and not d.startswith(".") and os.path.isdir(os.path.join(base, d)): out.setdefault(d, []).append(os.path.join(base, d))
    return out


def round_parts(ref):
    """A round's folders from its full name, its number ("R08") or a path that ends in either: [] when there is no such
    round or the number fits several. Read a round through this (every folder it gives), never one path built by hand."""
    ds = round_dirs(); name = os.path.basename(os.path.normpath(str(ref)))
    hit = [d for d in ds if d == name] or [d for d in ds if d.split("_")[0] == name]
    return list(ds[hit[0]]) if len(hit) == 1 else []


def round_files(ref, pattern="R*.json"):
    """The files matching pattern in a round (default: its recipes), over every folder it sits in. ref: as round_parts; a
    folder that exists and isn't one of a round's own (a copy somewhere else) is read by itself."""
    import glob
    ref = str(ref); ds = round_parts(ref); real = os.path.realpath(ref)
    if os.path.isdir(ref) and real not in [os.path.realpath(d) for d in ds]: ds = [ref]
    return sorted((f for d in ds for f in glob.glob(os.path.join(glob.escape(d), pattern))), key=os.path.basename)


def round_kind(path):
    """Which of layout.json's "round_folders" a round sits in ("" for a round straight in _lab/rounds)."""
    k = os.path.basename(os.path.dirname(os.path.normpath(path)))
    return k if k in ROUND_KINDS else ""


def still_key(clips=(), pins=()):
    """A function: a collage still (a recipe's {"clip" / "pin" / "file"}) -> the key of what it came from, a clip's index key
    or "pin:<its key in the pins index>". By its "clip" or "pin" when the still names one, else by its file: a pin's own
    file, or a frame named as lab_still.py names them (<clip>_<t>.jpg, a letterboxed clip's <clip>_<t>_aa.jpg; a pin's
    trimmed copy pin_<pin id>_aa.jpg). A clip whose key begins with a dot has none in its frames' names. Anything else
    (a photo, a clip the indexes don't know) keeps its own name. clips, pins: the index keys."""
    import re
    bare = {k.lstrip("."): k for k in clips}; by_id = {os.path.splitext(os.path.basename(k))[0]: k for k in pins}
    def key(s):
        f = str(s.get("file") or "").replace(os.sep, "/"); b = os.path.splitext(os.path.basename(f))[0]
        if s.get("clip"): return bare.get(s["clip"].lstrip("."), s["clip"])
        if s.get("pin"): return "pin:" + s["pin"]
        if f.startswith("_stills/pinterest/"): return "pin:" + f[len("_stills/pinterest/"):]
        m = re.fullmatch(r"pin_(.+?)(?:_aa)?", b)
        if m and m.group(1) in by_id: return "pin:" + by_id[m.group(1)]
        m = re.fullmatch(r"(.+)_\d+\.\d+(?:_aa)?", b)
        return bare.get(m.group(1) if m else b, b)
    return key


def use_counts(clips=(), pins=()):
    """How often each clip and pin has been in an edit: (the rounds, oldest first; {round: Counter of key -> how many of its
    recipes use it}). The one count library_view.py, narrow.py and shortlist_check.py show: each had its own, they disagreed
    on 20 clips, and two used clips read as never used. Every recipe of every round, wherever its folder sits and whatever
    it is called (a round not named R<number> takes its place from its recipes' names). A recipe counts a key once: each
    shot's "clip", each collage still through still_key (clips, pins: the index keys)."""
    import collections
    key = still_key(clips, pins); per, at = {}, {}
    for name in round_dirs():
        fs = round_files(name); c = per[name] = collections.Counter(); o = round_order(name)
        at[name] = o if o[0] or not fs else (round_order(fs[0])[0], name)
        for f in fs:
            r = json.load(open(f))
            c.update({key({"clip": s["clip"]}) for s in r.get("shots") or [] if s.get("clip")} | {key(s) for s in r.get("stills") or []})
    return sorted(per, key=at.get), per


def merge_own(dirs, per):
    """A split round's records merged into one ({folder: {id: entry}} -> {id: entry}): an id's entry in the folder where its
    recipe sits (<id>.json) wins; one left in another folder counts only while its own folder has none (review/serve.py's
    load_review does the same: a piece moved between folders by hand never brings an old verdict or seal back)."""
    out, own = {}, set()
    for d in dirs:
        for k, v in (per.get(d) or {}).items():
            mine = len(dirs) == 1 or os.path.exists(os.path.join(d, k + ".json"))
            if mine or k not in own: out[k] = v
            if mine: own.add(k)
    return out


def posted_on(entry):
    """The accounts (their ids, upper case) an index entry was posted on: its "pastured_<account id, lower case>" fields.
    Rules §11: a clip in a video posted on one account is retired from that account's
    videos, a pin in a posted collage from that account's collages; the other accounts still use it."""
    return sorted(k[len("pastured_"):].upper() for k, v in entry.items() if k.startswith("pastured_") and v)


RESOLVE_UTILITY = os.environ.get("SANDBOX_RESOLVE_UTILITY") or "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
MENU_PENDING = os.path.join(LIB, "_recipes", "resolve", "menu")   # entries waiting for Resolve (./setup.sh installs them)


def resolve_menu(folder, name, text):
    """Write a Resolve menu entry: Workspace > Scripts > <folder> > <name>. The one writer every menu entry goes through.
    Returns (path, True) when it's in Resolve's scripts folder; (path, False) when Resolve isn't installed or that folder isn't
    writable: then it waits in library/_recipes/resolve/menu/, and ./setup.sh installs it (asking for the password if needed)."""
    import re
    fn = re.sub(r'[/:\\"]+', " ", name).strip() + ".py"
    for base, live in ((RESOLVE_UTILITY, True), (MENU_PENDING, False)):
        if live and not os.path.isdir(base): continue
        d = os.path.join(base, *[p for p in folder.split("/") if p])
        try:
            os.makedirs(d, exist_ok=True); p = os.path.join(d, fn); open(p, "w", encoding="utf-8").write(text); return p, live
        except OSError:
            continue
    raise OSError("couldn't write the menu entry %s" % fn)


# The one safe writer for indexes and catalogs. Several sessions and background pulls write the same files at once: rewritten in
# place, a reader could load half a file, and the last writer's copy won, so another script's change was lost. Now:
# - a lock between processes: flock on a hidden ".<name>.lock" beside the file. The system drops it when its holder exits or
#   is killed, so a killed run never blocks the next one; the lock file is removed on release (a killed run's is reused, then removed);
# - the new text goes to a hidden temp file in the same folder, synced, then renamed over the file: a reader sees the old
#   file or the new one, never half (a killed writer's temp file is cleared by the next write);
# - edit_json holds the lock from the read to the write, so two scripts changing different entries both keep their change.
# Keep slow work (ffmpeg, downloads) outside the with-block: others wait for the lock while it's held.
_HELD = {}; _HELD_GUARD = threading.Lock()   # real path -> [lock between threads, lock file fd, depth]: nested use in one thread is fine


def _lockfile(p):
    d, b = os.path.split(p); return os.path.join(d, "." + b + ".lock")


def _flock(p, wait):
    lp = _lockfile(p); os.makedirs(os.path.dirname(lp), exist_ok=True); end = time.time() + wait
    while True:
        fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):   # a disk that can't lock: write without one rather than not at all
                print("(no lock on this disk for %s: %s)" % (os.path.basename(p), e), file=sys.stderr); return None
            if time.time() > end: raise TimeoutError("%s: another run is still writing it after %d s; try again" % (p, wait))
            time.sleep(0.02); continue
        try: same = os.fstat(fd).st_ino == os.stat(lp).st_ino
        except OSError: same = False
        if same: return fd
        os.close(fd)   # the holder before us removed that lock file as we opened it: take the new one


@contextlib.contextmanager
def locked(path, wait=60):
    """Hold path's lock (between processes and threads). Yields the real path."""
    p = os.path.realpath(path)
    with _HELD_GUARD: h = _HELD.setdefault(p, [threading.RLock(), None, 0])
    if not h[0].acquire(timeout=wait): raise TimeoutError("%s: still being written in this process after %d s" % (p, wait))
    try:
        if not h[2]: h[1] = _flock(p, wait)
        h[2] += 1
        try: yield p
        finally:
            h[2] -= 1
            if not h[2] and h[1] is not None:
                try: os.unlink(_lockfile(p))   # removed while still held: a waiter that opened it sees it's gone and takes a new one
                except OSError: pass
                os.close(h[1]); h[1] = None
    finally: h[0].release()


def _replace(p, data):
    """Temp file in the same folder, synced, renamed over p (the caller holds p's lock)."""
    d, b = os.path.split(p)
    for f in os.listdir(d or "."):   # a killed writer's temp file: temp files are only made under this lock, so any other is stale
        if f.startswith("." + b + ".") and f.endswith(".tmp"):
            try: os.unlink(os.path.join(d, f))
            except OSError: pass
    tmp = os.path.join(d, ".%s.%d.tmp" % (b, os.getpid()))
    try:
        with open(tmp, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        try: os.chmod(tmp, os.stat(p).st_mode & 0o7777)   # keep the file's own permissions
        except OSError: pass
        os.replace(tmp, p)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def write_atomic(path, data):
    """Replace a file's whole contents (bytes or text) safely."""
    with locked(path) as p: _replace(p, data if isinstance(data, bytes) else data.encode("utf-8"))


def save_json(path, obj, **kw):
    """Replace a JSON file with obj (indent=1, ensure_ascii=False unless given). For a file this run owns whole; to change
    some entries of a shared index, use edit_json."""
    write_atomic(path, json.dumps(obj, **{"indent": 1, "ensure_ascii": False, **kw}))


@contextlib.contextmanager
def edit_json(path, default=None, **kw):
    """with edit_json(p, {"clips": {}}) as d: d["clips"][key] = entry
    Locked from the read to the write; saved (atomically, only if it changed) when the block ends without an error. A file that
    doesn't load is never replaced (it raises): nothing is lost to a bad read. default = what a missing file starts as ({})."""
    with locked(path) as p:
        old = open(p, "rb").read() if os.path.exists(p) else None
        try: d = json.loads(old.decode("utf-8")) if old is not None else copy.deepcopy(default if default is not None else {})
        except ValueError as e: raise ValueError("%s doesn't load (%s): nothing was written; restore it (or fix it) first" % (p, e)) from None
        yield d
        new = json.dumps(d, **{"indent": 1, "ensure_ascii": False, **kw}).encode("utf-8")
        if new != old: _replace(p, new)


def append_csv(path, rows, fields=None, unique=None):
    """Add rows (dicts) to a CSV catalog under its lock: the file's own header decides the columns (a new file gets fields, or
    the first row's keys). unique = a function row -> key: a row whose key is empty or already in the file isn't added.
    Returns how many rows were added."""
    rows = list(rows)
    with locked(path) as p:
        old = open(p, "rb").read() if os.path.exists(p) else b""
        r = csv.DictReader(io.StringIO(old.decode("utf-8"))); cur = list(r) if unique else []
        head = r.fieldnames or fields or (list(rows[0].keys()) if rows else [])
        seen = {unique(x) for x in cur} if unique else set()
        rows = [x for x in rows if not unique or (unique(x) and unique(x) not in seen)]
        if not rows: return 0
        if old and not old.endswith((b"\n", b"\r")): old += b"\r\n"   # a catalog saved by hand without its last line break: the new row never joins the last one
        s = io.StringIO(); w = csv.DictWriter(s, fieldnames=head, extrasaction="ignore")
        if not old: w.writeheader()
        w.writerows(rows); _replace(p, old + s.getvalue().encode("utf-8"))
        return len(rows)
