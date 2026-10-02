#!/usr/bin/env python3
"""Film and TV scenes from public links, pulled slowly: one at a time, a pause between, only sharp ones.
  python3 _scripts/Scene_Pull.py <link or YouTube id> [...] [--from links.txt] [--gap 60] [--min 720]
Why slowly: YouTube answers a burst of requests from one machine with "Sign in to confirm you're not a bot" and lets go again
after about twenty minutes. So a round's scenes are fetched by ONE run of this, never by several at once: one video, a pause of
--gap seconds, the next. It stops at the first refusal and lists what is left (run it again later with those): the bot
wall, any rate-limit answer ("try again later", "rate-limited", HTTP 429), or a download that runs past its 25 minutes.
One video that can't be had (age-gated, private, removed) is skipped with the reason and the run goes on. It never signs
in, never uses cookies or the machine's yt-dlp config, and never works round the wall.
Only sharp ones: a video under --min lines high, or over ten minutes, is skipped before anything is downloaded.
A scene already held costs no request: its file is there, or the clip index or the saves catalog knows it (IDs stay forever:
a scene that was deleted, retired or pastured is never downloaded again; the log says why it is held). The same clip is
recognised under any of its links (watch, youtu.be, shorts, embed, live, dai.ly; with or without https://).
Only a whole download is filed: when one fails part way, its pieces (a picture-only <Site>-<id>.f616.mp4, a .part) are left
in library/_inbox/ and named, never filed and never deleted. A whole <Site>-<id>.mp4 a stopped run left there is filed as it is.
Each scene that comes down is filed in library/_internal content/ as <Site>-<id>.mp4, gets its row in the saves catalog and
its machine fields in the clip index (length, cuts, picture area, contact sheet), marked needs_review: its one-liner and
moments are still written by whoever asked for it, after looking at the sheet (a sharp file can still carry subtitles or a logo).
--from: a text file with one link or id a line (# starts a comment). Works for any public site yt-dlp reads (Dailymotion,
Vimeo ...). About two minutes a scene. Log: library/_lab/scene_pull.log."""
import csv, datetime, json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, ROOT, append_csv, edit_json  # noqa: E402

INBOX = os.path.join(LIB, "_inbox"); DEST = "_internal content"; LOG = os.path.join(LIB, "_lab", "scene_pull.log")
CATALOG = os.path.join(LIB, "_reference", "saves_catalog.csv")
COLS = ["account", "list", "creator", "url", "posted", "caption", "sound", "duration_s", "resolution", "horizontal", "views", "likes",
        "comments", "shares", "saves", "sorted_to", "permission", "file", "cataloged"]
INDEX = os.path.join(LIB, "_reference", "clip_index.json"); EDITS = os.path.join(LIB, "_reference", "edits")
REFUSED = "Sign in to confirm"   # the bot wall: stops the run
LIMITED = re.compile(r"rate.?limit|try again later|too many requests|HTTP Error 429", re.I)   # so does any answer that says to slow down
AGE = re.compile(r"confirm your age|age.?restrict|inappropriate for some users", re.I)   # one video that can't be had without a sign-in: skipped
GONE = re.compile(r"private video|video unavailable|video is unavailable|has been removed|no longer available|does not exist", re.I)
PIECE = re.compile(r"\.(f\d+|temp)$")   # yt-dlp's own pieces (one stream of a download that didn't finish), as Lab_Pull.py tells them
STOPS = {"REFUSED": "the site asked for a sign-in: stopped (never signed in).",
         "LIMITED": "the site said to slow down (rate-limited): stopped. Leave it an hour before the next run.",
         "TIMEOUT": "the download ran past its 25 minutes: stopped."}


def log(*x):
    s = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(v) for v in x); print(s, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True); open(LOG, "a").write(s + "\n")


def key_of(link):
    """(the full link, the file name it will have, its id) for a link or a bare YouTube id."""
    if not re.match(r"https?://", link): link = ("https://" if "/" in link or "." in link else "https://www.youtube.com/watch?v=") + link   # a link pasted without its https://
    m = re.search(r"(?:v=|youtu\.be/|shorts/%s)([\w-]{11})" % ("|embed/|live/" if "youtube" in link else ""), link)
    if m: return link, "Youtube-" + m.group(1), m.group(1)
    vid = link.rstrip("/").split("/")[-1].split("?")[0]
    site = "Dailymotion" if "dailymotion" in link or "//dai.ly/" in link else "Vimeo" if "vimeo" in link else None
    return link, ("%s-%s" % (site, vid)) if site else None, vid


def index_one(rel):
    """Machine-index one new clip with the indexer's own code and write only that entry, under the index's lock."""
    import Clip_Index as ci
    name = os.path.splitext(os.path.basename(rel))[0]
    cur = json.load(open(ci.INDEX))["clips"].get(name) if os.path.exists(ci.INDEX) else None
    if cur and cur.get("file") == rel and cur.get("duration") and not cur.get("analyze_failed"): return "indexed before"
    os.makedirs(ci.SHEETS, exist_ok=True)
    try: m = ci.machine(rel, cur, name)
    except Exception as e: m = dict(file=rel, analyze_failed=str(e))
    row = None
    if os.path.exists(CATALOG):
        for r in csv.DictReader(open(CATALOG)):
            if os.path.splitext(os.path.basename(r.get("file") or ""))[0] == name: row = r
    with edit_json(ci.INDEX, {"clips": {}}) as d:
        old = d["clips"].get(name) or {}
        keep = {k: v for k, v in old.items() if k not in m and k not in ("missing_on_disk", "needs_review", "analyze_failed")}
        e = {**m, **keep}
        if row: e["catalog"] = dict(url=row.get("url"), views=row.get("views"), permission=row.get("permission") or None, sound=row.get("sound"))
        if not keep: e["needs_review"] = True
        d["clips"][name] = e
    return "index failed: " + m["analyze_failed"] if m.get("analyze_failed") else "indexed"


def held(link, k, vid):
    """Why a scene is not fetched ("" when it should be): its file is there ("have"), or the clip index, the saves catalog or
    the reference edits know it. IDs stay forever, so a scene that was deleted, retired or pastured is never downloaded again
    (what round_prep.py's known() asks before its own downloads)."""
    if k and os.path.exists(os.path.join(LIB, DEST, k + ".mp4")): return "have"
    try: e = json.load(open(INDEX, encoding="utf-8")).get("clips", {}).get(k) if k else None
    except (OSError, ValueError): e = None
    if e is not None:
        why = ["use: " + e["use"]] if e.get("use") in ("removed", "pasture", "no") else []
        return "held, not fetched again: the clip index has it (%s)" % ", ".join(why + ["missing on disk"] * bool(e.get("missing_on_disk")) or ["its file: %s" % e.get("file")])
    try: rows = list(csv.DictReader(open(CATALOG, encoding="utf-8")))
    except OSError: rows = []
    bare = lambda u: re.sub(r"^https?://(www\.)?", "", u).rstrip("/")
    for r in rows:
        u, f = r.get("url") or "", r.get("file") or ""
        if (k and (os.path.splitext(os.path.basename(f))[0] == k or re.search(r"[/=]%s(?![\w-])" % re.escape(vid), u))) or (u and bare(u) == bare(link)):
            return "held, not fetched again: the saves catalog has it (%s)" % (f or u)
    if k and os.path.isdir(EDITS) and k in {os.path.splitext(f)[0] for f in os.listdir(EDITS)}: return "held, not fetched again: it is a reference edit"
    return ""


def refusal(r, low):
    """What a download that filed nothing said: "REFUSED" or "LIMITED" (they stop the run), or why this one video was skipped.
    Only yt-dlp's own errors are read for it (a film's title on the other stream can hold any word)."""
    err = (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or ["?"])[-1]
    if LIMITED.search(r.stderr): return "LIMITED"
    if AGE.search(r.stderr): return "skipped: age-gated (it asks for a sign-in to confirm an age; never signed in)"
    if REFUSED in err or REFUSED in r.stderr: return "REFUSED"
    if GONE.search(r.stderr): return "skipped: private or removed: " + err[:160]
    return "skipped (under %d lines, over 10 minutes, or no download): %s" % (low, err[:160])


def pull(link, low):
    """One scene. Returns (key, what happened, whether a request was made); what is a key of STOPS when the run has to stop
    ("REFUSED": the site asks for a sign-in; "LIMITED": it says to slow down)."""
    link, k, vid = key_of(link); why = held(link, k, vid)
    if why: return k or link, why, False
    os.makedirs(os.path.join(INBOX, "_info"), exist_ok=True); before = set(os.listdir(INBOX)); asked = not (k and k + ".mp4" in before)
    if asked:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-warnings", "--match-filter", "height>=%d & duration<=600" % low,
                            "-f", "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
                            "-o", os.path.join(INBOX, "%(extractor_key)s-%(id)s.%(ext)s"), "-o", "infojson:" + os.path.join(INBOX, "_info", "%(id)s"),
                            "--write-info-json", link], cwd=ROOT, capture_output=True, text=True, timeout=1500)
        left = sorted(f for f in os.listdir(INBOX) if vid in f)   # everything of this scene in the inbox, pieces of this download or an earlier one too
        new = [f for f in left if f not in before and f.endswith(".mp4") and not PIECE.search(f[:-4])]
        if r.returncode or not new:   # as Lab_Pull.py: a download that failed files nothing; the picture-only stream it left is not the scene
            if left: log(k or link, "| left in the inbox, not filed (a download that didn't finish; never deleted):", ", ".join(left))
            return k or link, refusal(r, low), True
    else: new = [k + ".mp4"]   # a whole file a stopped run left in the inbox: filed as it is, no request
    k = new[0][:-4]; rel = "%s/%s.mp4" % (DEST, k); os.rename(os.path.join(INBOX, new[0]), os.path.join(LIB, rel))
    try: info = json.load(open(os.path.join(INBOX, "_info", vid + ".info.json")))
    except Exception: info = {}
    w, h = info.get("width") or 0, info.get("height") or 0; d_ = str(info.get("upload_date") or "")
    append_csv(CATALOG, [{"account": "", "list": "Found", "creator": info.get("uploader") or "", "url": info.get("webpage_url") or link,
        "posted": "%s-%s-%s" % (d_[:4], d_[4:6], d_[6:]) if len(d_) == 8 else d_,
        "caption": (info.get("description") or info.get("title") or "").replace("\n", " "), "sound": "",
        "duration_s": round(info["duration"]) if info.get("duration") else "", "resolution": "%dx%d" % (w, h) if w else "",
        "horizontal": "CROP" if w > h else "", "views": info.get("view_count") or "", "likes": info.get("like_count") or "",
        "comments": info.get("comment_count") or "", "shares": "", "saves": "", "sorted_to": DEST, "permission": "",
        "file": rel, "cataloged": datetime.date.today().isoformat()}], fields=COLS, unique=lambda x: x.get("file"))
    return k, "%s %sx%s %ss | %s | %s" % ("down" if asked else "was in the inbox (a stopped run left it), filed:", w, h, info.get("duration"), (info.get("title") or "")[:70], index_one(rel)), asked


def main(a):
    gap, low, links, bad = 60, 720, [], []
    while a:
        x = a.pop(0); v = a.pop(0) if x in ("--gap", "--min", "--from") and a else None
        if x in ("--gap", "--min"):
            if not (v or "").isdigit(): bad.append("%s needs a whole number after it%s" % (x, ' (not "%s")' % v if v else ""))
            elif x == "--gap": gap = int(v)
            else: low = int(v)
        elif x == "--from":
            try: links += [l.split("#")[0].strip().split()[0] for l in open(v) if l.split("#")[0].strip()]
            except (OSError, TypeError, ValueError): bad.append("--from needs a text file of links after it%s" % (' ("%s" can\'t be read)' % v if v else ""))
        else: links.append(x)
    if bad: sys.exit("%s. Nothing was asked for. Left: %s" % ("; ".join(bad), " ".join(links) or "none"))
    if not links: sys.exit(__doc__)
    for n, link in enumerate(links):
        try: k, what, asked = pull(link, low)
        except subprocess.TimeoutExpired: k, what, asked = key_of(link)[1] or link, "TIMEOUT", True
        log(k, "|", what)
        if what in STOPS:
            log(STOPS[what], "Not tried:", " ".join(links[n + 1:]) or "none", "| and this one again:", link)
            sys.exit(2)
        if n < len(links) - 1 and asked: time.sleep(gap)
    log("done:", len(links), "asked")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"): print(__doc__); sys.exit(0)
    main(sys.argv[1:])
