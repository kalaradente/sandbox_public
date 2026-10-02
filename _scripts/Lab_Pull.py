#!/usr/bin/env python3
"""Lab footage puller: more posts from the creators already saved from (every creator in
_reference/saves_catalog.csv), downloaded into _lab/source/ and machine-indexed into _lab/lab_index.json.
Lab footage never enters the main library; the person promotes what they like.
Usage: python3 _scripts/Lab_Pull.py [--max 30] [--per-creator 3] [--scan 12] [--creators a,b,c]
  --max          new clips to download this run (default 30)
  --per-creator  new clips per creator (default 3)
  --scan         newest posts to look at per creator (default 12)
Skips every ID already in the main catalog, the lab catalog, the lab index, _reference/edits, or the posts; skips photo
posts, posts over 60 s, and anything that won't download (age-restricted). New clips get needs_review=true: fill their hand
fields from the sheets in _lab/_sheets/ before planning with them.
A run stopped part way (a command killed at its time limit, a download that hangs) loses nothing: each clip goes into the
catalog and the index as soon as it's down, and every run first indexes any clip in _lab/source/ the index doesn't have yet.
A download cut off part way is never indexed: yt-dlp writes to .part and renames only when done (a .part is resumed next
time), and a clip must also be whole (its boxes run to the end of the file) or it's moved to _to_delete/lab_pull_cut_off/."""
import os, re, sys, csv, json, subprocess, random, datetime, importlib.util
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from sandbox_paths import LIB, VIDEO_EXT, edit_json, append_csv  # library/: footage, indexes, lab
LAB = os.path.join(LIB, "_lab")
SRC = os.path.join(LAB, "source"); SHEETS = os.path.join(LAB, "_sheets"); IDX = os.path.join(LAB, "lab_index.json")
LCAT = os.path.join(LAB, "lab_catalog.csv")
for d in (SRC, SHEETS): os.makedirs(d, exist_ok=True)
arg = lambda k, d: int(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
MAX, PER, SCAN = arg("--max", 30), arg("--per-creator", 3), arg("--scan", 12)
ONLY = sys.argv[sys.argv.index("--creators") + 1].split(",") if "--creators" in sys.argv else None   # e.g. --creators some_creator,another_creator
vid_of = lambda url: (url or "").rstrip("/").split("/")[-1].split("?")[0]


def lab_index():
    return (json.load(open(IDX)) if os.path.exists(IDX) else {}).get("clips", {})


def known_ids():
    s = set()
    for f in [os.path.join(LIB, "_reference/saves_catalog.csv"), LCAT]:
        if os.path.exists(f): s |= {vid_of(r["url"]) for r in csv.DictReader(open(f)) if r.get("url")}
    for d in ["_reference/edits", "_reference/posted"]:
        for f in os.listdir(os.path.join(LIB, d)) if os.path.isdir(os.path.join(LIB, d)) else []:
            s.add(os.path.splitext(f)[0].split(".")[0].rsplit("_", 1)[-1])
    for f in os.listdir(SRC):   # whole clips only: a download cut off is just its .part and .info.json, and is tried again
        if f.lower().endswith(VIDEO_EXT): s.add(os.path.splitext(f)[0].rsplit("_", 1)[-1])
    return s | {k.rsplit("_", 1)[-1] for k in lab_index()}   # indexed once = known, even after its file is deleted


def creators():
    c = {}
    cat = os.path.join(LIB, "_reference/saves_catalog.csv")
    for r in (csv.DictReader(open(cat)) if os.path.exists(cat) else []):   # no saves yet = no creators to pull from
        u = r.get("url") or ""
        if "tiktok.com/@" in u and r.get("account") != "captions": c[u.split("/@")[1].split("/")[0]] = r.get("account")   # TikTok creators only (the catalog also holds other sites' links)
    return c


def whole(path):
    """A whole MP4/MOV: its top-level boxes run exactly to the end of the file, and one is the index (moov). A copy cut off part
    way ends inside a box (moov at the end: it's missing; moov first: the picture data runs past the end)."""
    try:
        n = os.path.getsize(path); i = 0; moov = False
        with open(path, "rb") as f:
            while i < n:
                f.seek(i); h = f.read(16)
                if len(h) < 8: return False
                size, typ = int.from_bytes(h[:4], "big"), h[4:8]
                if size == 1: size = int.from_bytes(h[8:16], "big") if len(h) == 16 else 0   # a 64-bit size
                elif size == 0: size = n - i   # the last box runs to the end of the file
                if size < 8: return False
                moov |= typ == b"moov"; i += size
        return moov and i == n
    except OSError:
        return False


def take(ci, rel, user, acct, pulled=None):
    """One clip that's down: into the catalog, then the index, each saved at once (a run stopped after this keeps it).
    Returns its catalog row; None when the file is cut off (moved aside, so it's pulled again)."""
    f = os.path.join(LIB, rel); name = os.path.splitext(os.path.basename(rel))[0]; vid = name.rsplit("_", 1)[-1]
    if not whole(f):
        aside = os.path.join(LIB, "_to_delete", "lab_pull_cut_off", datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
        os.makedirs(aside, exist_ok=True); os.replace(f, os.path.join(aside, os.path.basename(f)))
        print("  cut off, not indexed (moved to %s; pulled again next time): %s" % (os.path.relpath(aside, LIB), rel)); return None
    j = os.path.splitext(f)[0] + ".info.json"
    try: info = json.load(open(j))
    except (OSError, ValueError): info = {}
    url = info.get("webpage_url") or ("https://www.tiktok.com/@%s/video/%s" % (user, vid) if user and vid.isdigit() else "")
    row = dict(pulled=pulled or datetime.date.today().isoformat(), creator=user, like_account=acct, url=url,
               posted=info.get("upload_date"), duration_s=info.get("duration"), views=info.get("view_count"),
               likes=info.get("like_count"), caption=(info.get("description") or "").replace("\n", " ")[:200], file=rel)
    if url: append_csv(LCAT, [row], unique=lambda r: vid_of(r.get("url")))   # never twice (a re-run picking up the same clip)
    # machine-index it with the main indexer's functions (true-timestamp sheets into _lab/_sheets); slow, so outside the lock
    try:
        m = ci.machine(rel); m["sheet"] = "_lab/_sheets/%s.jpg" % name.lstrip("."); m["account"] = "lab"
    except Exception as e:
        m = dict(file=rel, analyze_failed=str(e))
    m["needs_review"] = True; m["lab"] = True; m["catalog"] = dict(url=url, views=row["views"])
    with edit_json(IDX, {"clips": {}}) as idx:   # only this entry: another session's edits to the index stay
        idx.setdefault("clips", {}).setdefault(name, m)
    return row


def main():
    spec = importlib.util.spec_from_file_location("ci", os.path.join(os.path.dirname(os.path.abspath(__file__)), "Clip_Index.py")); ci = importlib.util.module_from_spec(spec); spec.loader.exec_module(ci)
    ci.SHEETS = SHEETS
    os.makedirs(SRC, exist_ok=True)   # a library that has never pulled: nothing to pick up, and the listing below needs the folder
    cmap = creators()
    # first what a stopped run left: every clip in _lab/source/ the index doesn't have (not yt-dlp's own pieces: .fNNN, .temp)
    have = lab_index(); files = {v.get("file") for v in have.values()}; back = []
    for f in sorted(os.listdir(SRC)):
        rel = "_lab/source/" + f; name = os.path.splitext(f)[0]
        if f.startswith(".") or not f.lower().endswith(VIDEO_EXT) or re.search(r"\.(f\d+|temp)$", name) or name in have or rel in files: continue
        user = name.rsplit("_", 1)[0] if "_" in name else ""
        r = take(ci, rel, user, cmap.get(user) or "lab", datetime.date.fromtimestamp(os.path.getmtime(os.path.join(SRC, f))).isoformat())
        if r: back.append(r)
    if back: print("picked up", len(back), "clips a stopped run left:", ", ".join(r["file"] for r in back))
    seen = known_ids(); cr = list(cmap.items()); random.shuffle(cr); got = []
    if ONLY: cr = [(u, a) for u, a in cr if u in ONLY] or [(u, "lab") for u in ONLY]
    for user, acct in cr:
        if len(got) >= MAX: break
        try:
            r = subprocess.run(["python3", "-m", "yt_dlp", "--impersonate", "chrome", "--flat-playlist", "--playlist-end", str(SCAN),
                                "--print", "%(id)s|%(duration)s", "https://www.tiktok.com/@" + user], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            print("  @%s: listing its posts timed out, skipped" % user); continue
        n = 0
        for line in r.stdout.split():
            vid, _, dur = line.partition("|")
            if not vid.isdigit() or vid in seen: continue
            try:
                if dur not in ("", "NA", "None") and float(dur) > 60: continue
            except ValueError: pass
            out = os.path.join(SRC, "%s_%s" % (user, vid))
            seen.add(vid)
            try:
                d = subprocess.run(["python3", "-m", "yt_dlp", "--impersonate", "chrome", "-q", "--no-warnings", "-o", out + ".%(ext)s",
                                    "--write-info-json", "https://www.tiktok.com/@%s/video/%s" % (user, vid)], capture_output=True, text=True, timeout=150)
            except subprocess.TimeoutExpired:   # its .part stays: yt-dlp resumes it when the clip comes up again
                print("  %s_%s: download timed out, skipped" % (user, vid)); continue
            if d.returncode or not os.path.exists(out + ".mp4"):
                for ext in (".info.json", ".jpg", ".webp"):
                    if os.path.exists(out + ext): os.remove(out + ext)
                continue
            g = take(ci, "_lab/source/%s_%s.mp4" % (user, vid), user, acct)
            if not g: continue
            got.append(g); n += 1
            if n >= PER or len(got) >= MAX: break
    print("pulled", len(got), "new clips from", len({g['creator'] for g in got}), "creators")
    for g in got: print("  ", g["file"], g["duration_s"], "s", g["views"], "views")

if __name__ == "__main__": main()
