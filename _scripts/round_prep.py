#!/usr/bin/env python3
"""Round prep: a round's upkeep as one command with no model, run in the background while Claude does the rest (R07 spent
64 of its 88 minutes waiting on these steps).
  python3 _scripts/round_prep.py [<name>] [--links <file>] [--skip <step,...>] [--max 30] [--per-creator 2] [--external]
  python3 _scripts/round_prep.py [<name>] --dry      what it would run and where each step stands; runs and writes nothing
<name>: the prep's folder, library/_lab/prep/<name>/ (default: today's date; give a second round the same day its own name).
The steps, in order. One that fails is named in the to-do and the rest still run:
  links    the links already collected (Claude reads the Gmail drafts: a sign-in), from <prep>/links.txt: an id already saved
           is skipped (never downloaded twice), the rest go to _inbox, then into _internal content with a catalog row
           (a reference edit into _reference/edits); Pinterest links go to the pins step
  pulls    Lab_Pull.py --max 30 --per-creator 2: newer posts from the creators already saved
  pins     Pinterest_Pull.py: the profile's boards and any Pinterest links
  index    Clip_Index.py: new footage (never _external content: the person's own footage only when they say so)
  tags     concept_tags.py: tags from the one-liners' words
  visual   visual_search.py --build --no-external: the visual index
  posts    Post_Sync.py --fetch: the accounts' new posts and every post's numbers
  matches  Post_Sync.py --pending: the clips each post not logged yet most likely used, and a picture per post to confirm it
It ends with the to-do (printed, and saved as <prep>/todo.txt, rewritten after every step): the posts to confirm by eye (the
post's frame next to the suggested source's frame, one picture per post) and the clips to retire once they're confirmed; the
clips and pins that need a one-liner, with their contact sheets; what it couldn't do; what to ask the person. The indexes
are only written before the visual step, so the one-liners can start while the posts are still being matched.
Its last line says what is done and what is waiting. Stopped (or killed) and run again with the same name, it picks up where
it stopped: finished steps aren't repeated, a download is never made twice. Run it again after the one-liners: the tags and
the visual index catch up and the to-do is rewritten.
<prep>/links.txt, one link a line: <link> [chat] [edit] [found] [ #the draft's own words]. chat: pasted in the chat (the
catalog says Chat; else Email). edit: a reference edit, not footage. found: Claude went looking for it (a draft or a pasted
link is the person asking; a found link over 10 minutes is skipped). --links <file> adds a file's links to it.
--external: post matching and the visual index take in the person's indexed own footage too (only when they ask: a big
shoot takes long). Never: a login or cookies (a link that needs one is skipped and named).
It takes minutes: run it in the background (a foreground command is stopped after 2 minutes). Exit: 0; 1 when a step failed
(the rest ran); 130 when it was stopped."""
import csv, datetime, glob, hashlib, json, os, re, signal, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from sandbox_paths import LIB, VIDEO_EXT, append_csv, find_account, layout, profile, save_json, write_atomic  # noqa: E402  (--help stops there)

EXTERNAL = "_external content"   # the person's own footage: indexed or fingerprinted only when they say so
STEPS = ["links", "pulls", "pins", "index", "tags", "visual", "posts", "matches"]   # everything that writes the indexes comes before visual
INDEXES = ["_reference/clip_index.json", "_lab/lab_index.json", "_stills/pinterest/index.json"]
CATALOG = os.path.join(LIB, "_reference/saves_catalog.csv"); INBOX = os.path.join(LIB, "_inbox"); CRYINFO = os.path.join(INBOX, "_info")   # each download's details, on the drive next to the download (the check prompt, step 3)
CAT_COLS = ["account", "list", "creator", "url", "posted", "caption", "sound", "duration_s", "resolution", "horizontal", "views", "likes",
            "comments", "shares", "saves", "sorted_to", "permission", "file", "cataloged"]   # Check_TikTok_links_task_prompt.txt step 5a
LONG = 600   # seconds
SETTLED = ("done", "known", "failed", "pin", "pinned")   # a link in any of these isn't looked at again ("timed out" is)


def opt(a, k, d=None):
    if k in a: i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return v
    return d


A = sys.argv[1:]
LINKS_IN = opt(A, "--links"); SKIP = {s for s in (opt(A, "--skip") or "").split(",") if s}
MAX, PER = int(opt(A, "--max", 30)), int(opt(A, "--per-creator", 2))
DRY, EXT = "--dry" in A, "--external" in A
NAME = next((x for x in A if not x.startswith("--")), datetime.date.today().isoformat())
if "/" in NAME or NAME.startswith("http") or SKIP - set(STEPS): sys.exit("usage: python3 _scripts/round_prep.py [<name>] [options]: see --help")
PREP = os.path.join(LIB, "_lab", "prep"); D = os.path.join(PREP, NAME); STATE = os.path.join(D, "state.json")
LOCK = os.path.join(PREP, ".running"); LOG = os.path.join(D, "log.txt"); TODO = os.path.join(D, "todo.txt")
now = lambda: datetime.datetime.now().strftime("%H:%M:%S")
rel = lambda p: os.path.relpath(p, LIB)


class Stopped(BaseException): pass   # not an Exception: nothing that catches a step's errors swallows a stop


def stop(*_):
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT): signal.signal(sig, signal.SIG_IGN)   # one stop: the to-do still gets written
    raise Stopped()


def jload(p, d):
    try: return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError): return d


def alive(pid, what):
    """True if pid is running and is `what` (a pid can be reused by something else)."""
    try: os.kill(int(pid), 0)
    except ProcessLookupError: return False
    except (PermissionError, ValueError, TypeError): pass
    return what in subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout


def stamp():
    """The indexes as they are now (tags and the visual index catch up when this changes)."""
    h = hashlib.sha1()
    for r in INDEXES:
        try: h.update(open(os.path.join(LIB, r), "rb").read())
        except OSError: h.update(b"-")
    return h.hexdigest()


# ---- links: the ones Claude collected from the drafts or the chat
def links():
    """[(link, flags, note)] from <prep>/links.txt."""
    out = []
    try: lines = open(os.path.join(D, "links.txt"), encoding="utf-8").read().splitlines()
    except OSError: return out
    for ln in lines:
        body, _, note = ln.strip().partition(" #")
        t = body.split()
        if t and not t[0].startswith("#"): out.append((t[0], {x.lower() for x in t[1:]}, note.strip()))
    return out


def add_links(src):
    have = {l for l, _, _ in links()}; new = []
    for ln in open(src, encoding="utf-8").read().splitlines():
        t = ln.strip().split()
        if t and not t[0].startswith("#") and t[0] not in have: new.append(ln.strip()); have.add(t[0])
    if new:
        with open(os.path.join(D, "links.txt"), "a", encoding="utf-8") as f: f.write("".join(x + "\n" for x in new))
    return new


def open_links(st): return [l for l, _, _ in links() if st["links"].get(l, {}).get("status") not in SETTLED]


def pinterest(link): return bool(re.search(r"(^|//|\.)(pin\.it|pinterest\.[a-z.]+)/", link))


def known():
    """Ids already saved (the catalog's links and files, reference edits, the index): IDs stay forever, so a clip the person deleted or
    retired is never downloaded again (Check_TikTok_links_task_prompt.txt steps 2 and 4)."""
    ids, names, urls = set(), set(), []
    try: rows = list(csv.DictReader(open(CATALOG, encoding="utf-8")))
    except OSError: rows = []
    for r in rows:
        u, f = r.get("url") or "", r.get("file") or ""; urls.append(u)
        ids |= set(re.findall(r"\d{15,}", u + " " + f)); names.add(os.path.splitext(os.path.basename(f))[0])
    for n in list(jload(os.path.join(LIB, INDEXES[0]), {}).get("clips", {})) + \
             [os.path.splitext(f)[0] for f in (os.listdir(os.path.join(LIB, "_reference/edits")) if os.path.isdir(os.path.join(LIB, "_reference/edits")) else [])]:
        names.add(n); ids |= set(re.findall(r"\d{15,}", n))
    return ids, names, urls


def ytdlp(args, timeout):
    """yt-dlp, never with a login or cookies."""
    return subprocess.run(["python3", "-m", "yt_dlp", "--impersonate", "chrome", "--no-playlist", "--no-warnings"] + args,
                          capture_output=True, text=True, timeout=timeout)


def why_not(r):
    e = ((r.stderr or r.stdout).strip().splitlines() or ["no output"])[-1][:200]
    return ("needs a login: skipped (never a login)  " if re.search(r"\blog-? ?in\b|\bsign-? ?in\b|age[- ]restrict|\bcookies\b|\bprivate\b", e, re.I) else "") + e


def catalog_row(link, flags, info, name, dest):
    w, h = info.get("width") or 0, info.get("height") or 0; d = str(info.get("upload_date") or "")
    row = {"account": "", "list": "Chat" if "chat" in flags else "Found" if "found" in flags else "Email", "creator": info.get("uploader") or "",
           "url": info.get("webpage_url") or link, "posted": "%s-%s-%s" % (d[:4], d[4:6], d[6:]) if len(d) == 8 else d,
           "caption": (info.get("description") or info.get("title") or "").replace("\n", " "), "sound": info.get("track") or "",
           "duration_s": round(info["duration"]) if info.get("duration") else "", "resolution": "%dx%d" % (w, h) if w else "",
           "horizontal": "CROP" if w > h else "", "views": info.get("view_count") or "", "likes": info.get("like_count") or "",
           "comments": info.get("comment_count") or "", "shares": info.get("repost_count") or "", "saves": info.get("save_count") or "",
           "sorted_to": dest, "permission": "", "file": "%s/%s.mp4" % (dest, name), "cataloged": datetime.date.today().isoformat()}
    append_csv(CATALOG, [row], fields=CAT_COLS, unique=lambda x: x.get("file"))   # never a second row for one file (a stop right after writing it)


def step_links(st, log):
    """Each link: resolve its id, skip it if saved already, download it to _inbox, file it, write its catalog row. Its state is
    saved after every move, so a stopped run never downloads or files anything twice."""
    ids, names, urls = known(); done = n = 0
    for link, flags, note in links():
        s = st["links"].setdefault(link, {})
        if note: s["note"] = note
        if s.get("status") in SETTLED: continue
        if pinterest(link): s["status"] = "pin"; save(st); continue
        n += 1
        try:
            if "id" not in s:
                r = ytdlp(["--print", "%(extractor_key)s\t%(id)s\t%(uploader)s\t%(duration)s", link], 120)
                got = [x.split("\t") for x in r.stdout.splitlines() if x.count("\t") == 3]
                if r.returncode or not got: s.update(status="failed", why=why_not(r)); save(st); continue
                if len(got) > 1: s.update(status="failed", why="a list of %d videos, not one: take the ones wanted by hand" % len(got)); save(st); continue
                ek, vid, up, dur = got[0]
                s.update(id=vid, site=ek, dur=float(dur) if re.match(r"^[\d.]+$", dur) else None,   # file names as the check prompt makes them
                         name=re.sub(r"[/\\:]", "_", ("%s_%s" % (up, vid)) if ek == "TikTok" else "%s-%s" % (ek, vid))); save(st)
            vid, name = s["id"], s["name"]
            if s.get("status") not in ("placed",):
                if vid in ids or name in names or any(re.search(r"[/=]%s(?![\w-])" % re.escape(vid), u) for u in urls):
                    s["status"] = "known"; save(st); print("  %s: saved already (%s)" % (link, name), file=log, flush=True); continue
                if "found" in flags and (s.get("dur") is None or s["dur"] > LONG):
                    s.update(status="failed", why="over 10 minutes (%s s) and not asked for: take only the scene (--download-sections)" % s.get("dur")); save(st); continue
            dest = "_reference/edits" if "edit" in flags else "_internal content"
            src, dst = os.path.join(INBOX, name + ".mp4"), os.path.join(LIB, dest, name + ".mp4")
            if s.get("status") != "placed":
                if not os.path.exists(src):
                    os.makedirs(INBOX, exist_ok=True)
                    args = ["-o", os.path.join(INBOX, name) + ".%(ext)s", "-o", "infojson:" + os.path.join(CRYINFO, "%(id)s"), "--write-info-json"]
                    if s["site"] != "TikTok": args += ["-f", "bv*+ba/b", "--merge-output-format", "mp4"]   # TikTok's default is the clean file (never "download")
                    r = ytdlp(args + [link], int(300 + 3 * (s.get("dur") or 300)))
                    if not os.path.exists(src):
                        other = [os.path.basename(p) for p in glob.glob(os.path.join(INBOX, glob.escape(name) + ".*"))]
                        s.update(status="failed", why=("no video came down (%s): a photo post? save its pictures into _internal content/photos/%s/"
                                                       % (", ".join(other), name)) if other else why_not(r)); save(st); continue
                if os.path.exists(dst):
                    s.update(status="failed", why="%s/%s.mp4 exists already: left in _inbox" % (dest, name)); save(st); continue
                os.makedirs(os.path.dirname(dst), exist_ok=True); os.rename(src, dst); s.update(status="placed", file="%s/%s.mp4" % (dest, name)); save(st)
            if dest != "_reference/edits":
                catalog_row(link, flags, jload(os.path.join(CRYINFO, vid + ".info.json"), {}), name, dest)
                ids.add(vid); names.add(name)
            s["status"] = "done"; save(st); done += 1
            print("  %s -> %s" % (link, s["file"]), file=log, flush=True)
        except subprocess.TimeoutExpired:
            s.update(status="timed out", why="timed out (tried again on the next run)"); save(st)
        except Exception as e:   # one bad link doesn't hold up the others
            s.update(status="failed", why="%s: %s" % (type(e).__name__, e)); save(st)
    return 0, "%d new of %d links looked at" % (done, n)


# ---- the steps that are other scripts
def unindexed():
    """Footage the main index doesn't know yet, in the folders layout.json names (never _external content)."""
    have = {v.get("file") for v in jload(os.path.join(LIB, INDEXES[0]), {}).get("clips", {}).values()}; out = []
    for s in layout().get("source_folders") or []:
        if s == EXTERNAL: continue
        for dp, _, fn in os.walk(os.path.join(LIB, s)):
            if any(p.startswith("_sheets") for p in dp.split(os.sep)): continue
            out += [r for r in (rel(os.path.join(dp, f)) for f in fn if f.lower().endswith(VIDEO_EXT)) if r not in have]
    return out


def own_clips(idx=None):
    """The person's own clips that are indexed (they said yes to indexing them) and usable."""
    idx = jload(os.path.join(LIB, INDEXES[0]), {}).get("clips", {}) if idx is None else idx
    return {k for k, v in idx.items() if str(v.get("file", "")).startswith(EXTERNAL + "/") and v.get("use") not in ("no", "removed")}


def lab_files(): return sorted(os.path.basename(p) for p in glob.glob(os.path.join(LIB, "_lab/source/*.mp4")))


def command(step, st):
    """The command a step runs now, or None when there's nothing for it to do."""
    py = lambda s, *a: [sys.executable, "-u", os.path.join(HERE, s)] + list(a)
    s = st["steps"].get(step, {}); done = s.get("status") == "done"
    if step == "pulls":
        if done: return None
        left = MAX - len(set(lab_files()) - set(st.get("pulls_before", lab_files())))   # a stopped pull: only what's left of it
        return py("Lab_Pull.py", "--max", str(left), "--per-creator", str(PER)) if left > 0 else None
    if step == "pins":
        extra = [l for l, v in st["links"].items() if v.get("status") == "pin"]
        if done and not extra: return None
        boards = [] if done else list(profile().get("pinterest_boards") or [])
        return py("Pinterest_Pull.py", *(boards + extra)) if boards + extra else None
    if step == "index": return py("Clip_Index.py") if not done or unindexed() else None
    if step == "tags": return py("concept_tags.py") if not done or s.get("stamp") != stamp() else None
    if step == "visual":
        return py("visual_search.py", "--build", *([] if EXT else ["--no-external"])) if not done or s.get("stamp") != stamp() or s.get("external", False) != EXT else None
    if step == "posts": return None if done else py("Post_Sync.py", "--fetch")
    if step == "matches":
        import Post_Sync as PS
        if done and all(PS.saved(a, v, p, EXT) for a, v, p in PS.pending()): return None
        return py("Post_Sync.py", "--pending", *(["--external"] if EXT else []))
    return None


def save(st): save_json(STATE, st)


def run(step, st, cmd, log):
    """Run one step's command, its output into the log; returns (exit code, what it said)."""
    log.write("\n=== %s %s: %s\n" % (now(), step, " ".join(os.path.basename(c) if i < 3 else c for i, c in enumerate(cmd)))); log.flush()
    start = os.path.getsize(LOG)
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONUNBUFFERED="1"))
    st["running"] = dict(step=step, pid=p.pid, script=os.path.basename(cmd[2])); save(st)
    try: code = p.wait()
    except Stopped:
        p.terminate()
        try: p.wait(10)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
        raise
    finally:
        st.pop("running", None); save(st)
    log.flush()
    with open(LOG, "rb") as f: f.seek(start); raw = [x for x in f.read().decode("utf-8", "replace").splitlines() if x.strip()]
    said = [x.strip() for x in raw] + [x.strip() for x in raw if not x[0].isspace()][-1:]   # last: its last unindented line (a summary, not a list item)
    log.write("=== %s %s: exit %d\n" % (now(), step, code)); log.flush()
    notes = st.setdefault("notes", {})
    if step == "index":   # the person's own footage waiting: Clip_Index says it once, so it's kept here until the prep is done
        notes["own"] = sorted(set(notes.get("own", [])) | {x for x in said if EXTERNAL in x})
        notes["index"] = sorted(set(notes.get("index", [])) | {x for x in said if x.startswith(("analyze_failed", "sheet_failed"))})
    if step in ("pulls", "pins", "posts", "matches"):
        notes[step] = sorted(set(notes.get(step, [])) | {x for x in said if x.startswith("!!") or re.search(r"couldn't|timed out|cut off|no public posts", x)})
    if step == "pins" and code == 0:
        for v in st["links"].values():
            if v.get("status") == "pin": v["status"] = "pinned"
    return code, said


# ---- the to-do
def todo(st, left=()):
    """What's waiting on Claude (or the person), from the indexes and this prep's state; saved as <prep>/todo.txt."""
    idx = jload(os.path.join(LIB, INDEXES[0]), {}).get("clips", {}); lab = jload(os.path.join(LIB, INDEXES[1]), {}).get("clips", {})
    pins = jload(os.path.join(LIB, INDEXES[2]), {}); pins = pins.get("pins", pins) if isinstance(pins, dict) else {}
    ok = lambda v: isinstance(v, dict) and v.get("needs_review") and v.get("use") != "removed"
    mine = {v["file"]: v for v in st["links"].values() if v.get("status") == "done" and v.get("file")}   # this prep's downloads
    main = [(k, v) for k, v in idx.items() if ok(v) and not str(v.get("file", "")).startswith(EXTERNAL + "/")]
    new = [(k, v) for k, v in main if v.get("file") in mine]; main = [(k, v) for k, v in main if v.get("file") not in mine]
    labn = [(k, v) for k, v in lab.items() if ok(v)]; pinn = [(k, v) for k, v in pins.items() if ok(v)]
    L = ["Round prep %s, %s (paths are inside library/)." % (NAME, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))]
    if left: L.append("Still running: %s. %s" % (", ".join(left), "Nothing left in this run writes the indexes: the one-liners can start."
                                                           if not set(left) & {"links", "pulls", "pins", "index", "tags"} else "Wait for index and tags before writing one-liners."))
    # posts first: planning waits for the clips they retire
    try:
        import Post_Sync as PS; posts = [(a, v, PS.saved(a, v, p, EXT)) for a, v, p in PS.pending()]
    except Exception as e: posts = []; L.append("(couldn't read the posts: %s)" % e)
    retire = []
    if posts:
        L += ["", "POSTS TO CONFIRM BY EYE (%d): one picture each, the post's frame next to the suggested source's frame, a row per stretch" % len(posts)]
        for a, v, d in posts:
            acc = find_account(a) or {}
            if not d: L.append("  %s (%s) %s: not matched yet" % (a, acc.get("id", "?"), v)); continue
            L.append("  %s (%s) %s: _reference/_post_matches/%s_%s.jpg" % (a, acc.get("id", "?"), v, a, v))
            for m in d["matches"]:
                L.append("      %-44s post %s s <- %s s  score %.2f%s" % (m["clip"][:44], m["post"], m["src"], m["score"], "" if m["sure"] else "  NOT SURE"))
                c = {**lab, **idx}.get(m["clip"], {})
                if (m["clip"], a) not in [(r[0], r[3]) for r in retire]: retire.append((m["clip"], c.get("file", "?"), c.get("use"), a, v, m["sure"]))
            for g in d["unmatched"]: L.append("      no match in the library: post %s s" % g)
        if retire:
            L += ["  Clips to retire once confirmed (Post_Sync_task_prompt.txt steps 4-5: what retiring means for each account; then log each post with its id):"]
            L += ["      %-44s %s%s  (%s %s)" % (k[:44], f, "  already retired" if u == "pasture" else "" if s else "  (NOT SURE)", a, v) for k, f, u, a, v, s in retire]
    notes = {v["file"]: v.get("note") for v in mine.values()}
    got = [(l, v) for l, v in st["links"].items() if v.get("status") in ("done", "known", "pinned", "pin")]
    if got:   # the check's summary: where each link went
        L += ["", "LINKS (%d new, %d saved already)" % (sum(v["status"] == "done" for _, v in got), sum(v["status"] == "known" for _, v in got))]
        L += ["  %s -> %s%s" % (l, v.get("file") or {"pin": "Pinterest (not pulled yet)", "pinned": "Pinterest"}.get(v["status"], "saved already: " + v.get("name", "")),
                                ("   the draft said: \"%s\"" % v["note"]) if v.get("note") and str(v.get("file", "")).startswith("_reference/edits/") else "") for l, v in got]
    if new or main or labn or pinn:
        L += ["", "ONE-LINERS (%d): read each contact sheet, fill the hand fields and remove needs_review (Check_TikTok_links_task_prompt.txt step 5b)"
              % (len(new) + len(main) + len(labn) + len(pinn))]
    if new:
        L.append("  New downloads (%d), filed into _internal content: text over most of it -> a Captions clip (move it to _captions/, add a "
                 "caption_ideas.csv row, fix its catalog row); a private person's face you can identify -> permission \"ASK CREATOR\"" % len(new))
        L += ["      %-44s %s%s" % (k[:44], v.get("sheet", "?"), ("   the draft said: \"%s\" (saved_note)" % notes[v["file"]]) if notes.get(v["file"]) else "") for k, v in new]
    if main: L += ["  Main library (%d):" % len(main)] + ["      %-44s %s" % (k[:44], v.get("sheet", "?")) for k, v in main]
    if labn: L += ["  Lab pulls (%d, _lab/lab_index.json):" % len(labn)] + ["      %-44s %s" % (k[:44], v.get("sheet", "?")) for k, v in labn]
    if pinn:
        pics = mosaic([v.get("file") for _, v in pinn], st)
        L += ["  Pins (%d, _stills/pinterest/index.json), numbered as in %s:" % (len(pinn), ", ".join(pics) or "(no picture)")]
        L += ["      %2d  %s" % (i + 1, k) for i, (k, _) in enumerate(pinn)]
    if new or main or labn or pinn: L.append("  Then run round_prep again (same name): the tags and the visual index catch up.")
    bad = [(l, v) for l, v in st["links"].items() if v.get("status") in ("failed", "timed out")]
    nt = st.get("notes", {}); fails = [(s, v) for s, v in st["steps"].items() if v.get("status") == "failed"]
    cut = [s for s, v in st["steps"].items() if v.get("status") in ("stopped", "running") and s not in left]
    if cut: L.insert(1, "STOPPED during %s: run round_prep again (same name) to pick up there." % ", ".join(cut))
    if bad or any(nt.get(k) for k in ("index", "pulls", "pins", "posts", "matches")) or fails:
        L += ["", "COULDN'T DO"]
        L += ["  link %s: %s" % (l, v.get("why")) for l, v in bad]
        L += ["  %s" % x for k in ("index", "pulls", "pins", "posts", "matches") for x in nt.get(k, [])]
        L += ["  step %s %s: %s (log: _lab/prep/%s/log.txt)" % (s, v["status"], v.get("said", ""), NAME) for s, v in fails]
    inbox = [f for f in (os.listdir(INBOX) if os.path.isdir(INBOX) else []) if f.lower().endswith(VIDEO_EXT)]
    if inbox: L += ["", "In _inbox, not filed (%d): %s" % (len(inbox), ", ".join(sorted(inbox)[:12]) + (" ..." if len(inbox) > 12 else ""))]
    vis = set(st.get("visual_seen") or []) if "visual_seen" in st and not EXT else None   # the visual step leaves own footage out unless asked
    unseen = [k for k in own_clips(idx) if vis is not None and k not in vis]
    if nt.get("own") or unseen:
        L += ["", "ASK THE PERSON"] + ["  %s" % x for x in nt.get("own", [])]
        if unseen: L.append("  %d of their own clips are indexed but not in the visual index (picture search of their footage): "
                            "visual_search.py --build embeds them, slowly on a big shoot: only if they want it" % len(unseen))
    write_atomic(TODO, "\n".join(L) + "\n")
    return L, dict(posts=len(posts), retire=len(retire), liners=len(new) + len(main) + len(labn) + len(pinn), ask=len(nt.get("own", [])) + bool(unseen), failed=[s for s, _ in fails])


def mosaic(files, st):
    """The pins waiting for a one-liner in numbered pictures (40 a picture), made once per set: the pins' contact sheet."""
    key = hashlib.sha1("\n".join(map(str, files)).encode()).hexdigest()
    outs = ["_lab/prep/%s/pins%s.jpg" % (NAME, "" if i == 0 else "_%d" % (i + 1)) for i in range((len(files) + 39) // 40)]
    if st.get("mosaic") == key and all(os.path.exists(os.path.join(LIB, o)) for o in outs): return outs
    import cv2, numpy as np
    for n, o in enumerate(outs):
        tiles = []
        for i, f in enumerate(files[n * 40:(n + 1) * 40]):
            im = cv2.imread(os.path.join(LIB, f)) if f else None; t = np.full((300, 220, 3), 60, np.uint8)
            if im is not None:
                s = min(220 / im.shape[1], 270 / im.shape[0]); im = cv2.resize(im, (max(1, int(im.shape[1] * s)), max(1, int(im.shape[0] * s))), interpolation=cv2.INTER_AREA)
                y, x = 30 + (270 - im.shape[0]) // 2, (220 - im.shape[1]) // 2; t[y:y + im.shape[0], x:x + im.shape[1]] = im
            cv2.putText(t, str(n * 40 + i + 1), (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA); tiles.append(t)
        cols = min(8, len(tiles))
        while len(tiles) % cols: tiles.append(np.full((300, 220, 3), 255, np.uint8))
        rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
        cv2.imwrite(os.path.join(LIB, o), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    st["mosaic"] = key; return outs


def last_line(st, info, stopped=None):
    done = [s for s in STEPS if st["steps"].get(s, {}).get("status") == "done"]
    wait = ["%d posts to confirm (%d clips to retire)" % (info["posts"], info["retire"])] if info["posts"] else []
    wait += ["%d one-liners" % info["liners"]] if info["liners"] else []
    wait += ["%d question(s) for the person" % info["ask"]] if info["ask"] else []
    return "prep %s: %sdone: %s%s | waiting on you: %s | to-do: %s" % (
        NAME, "STOPPED during %s (run it again to pick up there); " % stopped if stopped else "", ", ".join(done) or "nothing",
        " | failed: %s" % ", ".join(info["failed"]) if info["failed"] else "", "; ".join(wait) or "nothing", rel(TODO))


def dry(st):
    print("round_prep %s (dry: nothing runs, nothing is written; state: %s)" % (NAME, rel(STATE) if os.path.exists(STATE) else "none yet"))
    if LINKS_IN: print("  --links %s would add: %s" % (LINKS_IN, ", ".join(t.split()[0] for t in open(LINKS_IN).read().splitlines() if t.strip()) or "nothing"))
    for step in STEPS:
        s = st["steps"].get(step, {}); was = " (%s %s: %s)" % (s.get("status"), s.get("at", ""), s.get("said", "")) if s else ""
        if step in SKIP: print("  %-8s skipped (--skip)%s" % (step, was)); continue
        if step == "links":
            todo_ = open_links(st)
            print("  %-8s %s%s" % (step, "%d to look at: %s" % (len(todo_), " ".join(todo_)) if todo_ else "nothing to do", was)); continue
        cmd = command(step, st)
        print("  %-8s %s%s" % (step, "would run: python3 _scripts/" + " ".join([os.path.basename(cmd[2])] + cmd[3:]) if cmd else "nothing to do", was))


def main():
    st = jload(STATE, {}); st.setdefault("steps", {}); st.setdefault("links", {}); st["name"] = NAME
    if LINKS_IN and not os.path.isfile(LINKS_IN): sys.exit("no such links file: %s" % LINKS_IN)
    if DRY: return dry(st)
    os.makedirs(D, exist_ok=True)
    lk = jload(LOCK, {})
    if lk and alive(lk.get("pid"), "round_prep"): sys.exit("round_prep is already running (pid %s, prep %s): its log is _lab/prep/%s/log.txt" % (lk["pid"], lk.get("name"), lk.get("name")))
    save_json(LOCK, dict(pid=os.getpid(), name=NAME, since=now()))
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT): signal.signal(sig, stop)
    stopped = None; log = open(LOG, "a", encoding="utf-8")
    try:
        if LINKS_IN: print("%s links: %d added to %s" % (now(), len(add_links(LINKS_IN)), rel(os.path.join(D, "links.txt"))), flush=True)
        r = st.get("running")   # a step of a run killed outright may still be going: let it finish (it's doing the work), then pick up
        if r and alive(r.get("pid"), r.get("script", "?")):
            print("%s %s from the last run is still going (pid %s): waiting for it" % (now(), r["step"], r["pid"]), flush=True)
            while alive(r["pid"], r["script"]): time.sleep(5)
        st.pop("running", None); save(st)
        for step in STEPS:
            if step in SKIP: continue
            if step == "links":
                if not open_links(st): continue
                cmd = None
            else:
                if step == "pulls": st.setdefault("pulls_before", lab_files())
                cmd = command(step, st)
                if not cmd:
                    if st["steps"].get(step, {}).get("status") != "done": st["steps"][step] = dict(status="done", at=now(), said="nothing to do")
                    continue
            print("%s %s: %s" % (now(), step, "downloading the links" if cmd is None else "running " + " ".join([os.path.basename(cmd[2])] + cmd[3:])), flush=True)
            t0 = time.time(); st["steps"][step] = dict(status="running", at=now()); save(st)
            try:
                if cmd is None: code, said = step_links(st, log); said = [said]
                else: code, said = run(step, st, cmd, log)
            except Stopped:
                st["steps"][step] = dict(status="stopped", at=now(), said="stopped"); stopped = step; raise
            except Exception as e:   # one failing step doesn't stop the rest
                code, said = 1, ["%s: %s" % (type(e).__name__, e)]
            s = dict(status="done" if code == 0 else "failed", at=now(), secs=round(time.time() - t0, 1), said=(said or [""])[-1][:300])
            if code: s["error"] = said[-3:]
            if step in ("tags", "visual") and code == 0: s.update(stamp=stamp(), external=EXT)
            if step == "visual" and code == 0:   # which own clips the visual index has, for the to-do's question
                try:
                    import numpy as np
                    z = np.load(os.path.join(LIB, "_reference/visual_index.npz"), allow_pickle=False); own = own_clips()
                    st["visual_seen"] = sorted(k for k in json.loads(str(z["seen"])) if k in own)
                except Exception: pass
            st["steps"][step] = s; save(st)
            print("%s %s: %s (%.0f s): %s" % (now(), step, s["status"], s["secs"], s["said"]), flush=True)
            todo(st, [x for x in STEPS[STEPS.index(step) + 1:] if x not in SKIP]); save(st)
    except Stopped:
        stopped = stopped or "?"; save(st)
    finally:
        log.close()
        if jload(LOCK, {}).get("pid") == os.getpid(): os.remove(LOCK)
    lines, info = todo(st); save(st)
    print("\n".join(lines)); print(last_line(st, info, stopped))
    if stopped or info["failed"]: sys.exit(130 if stopped else 1)   # the rest ran; the exit says something didn't


if __name__ == "__main__": main()
