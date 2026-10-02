#!/usr/bin/env python3
"""Post sync: find what they posted, download it, and work out which library clips it used.
Usage (from the sandbox folder; written paths are inside library/):
  python3 _scripts/Post_Sync.py [--external]    # the whole sync: --fetch, then --pending
                                                # (--external: against _external content too; a plain run skips it)
  python3 _scripts/Post_Sync.py --fetch         # list the accounts, download new posts, snapshot every post's stats (no matching)
  python3 _scripts/Post_Sync.py --pending [--external] [--redo]   # match every downloaded post not in the post log yet (no network);
                                                # a post matched before shows its saved match (--redo: match it again)
  python3 _scripts/Post_Sync.py --match <mp4> [--no-external]   # only match one video against the library and the lab pulls (--no-external: skip _external content)
A post is new until it's in _reference/post_log.csv (its id, or its link, in its row), not just until its video is downloaded:
a run stopped while matching (or a post never logged) comes up again on the next run.
Writes:
  _reference/posted/<account>_<id>.mp4 (+ .info.json)
  _reference/post_stats.csv   one row per post per run (date, account, id, views, likes, comments, shares, saves)
  _reference/_fingerprints/   cached 4 fps thumbnails of every library clip (built on first use)
  _reference/_post_matches/<account>_<id>.json   a new post's match, as printed
  _reference/_post_matches/<account>_<id>.jpg    to confirm it by eye: per matched stretch, the post's frame next to the source
                                                 clip's frame at the matched time; a stretch with no match, the post's frame alone
Prints, for each new post, the library clips it most likely uses with the matched time ranges.
Matching is a suggestion: confirm by looking at the frames before retiring anything."""
import os, io, re, sys, json, glob, subprocess, datetime
import cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from sandbox_paths import LIB, accounts, append_csv, save_json, write_atomic  # library/: footage, indexes, lab
# each person's own TikTok accounts (library/profile.json "accounts")
ACCOUNTS = [str(a.get("handle") or a.get("name") or "").lstrip("@") for a in accounts() if str(a.get("platform") or "tiktok").lower() == "tiktok"]
ACCOUNTS = [a for a in ACCOUNTS if a]
POSTED = os.path.join(LIB, "_reference/posted"); FP = os.path.join(LIB, "_reference/_fingerprints")
MATCHES = os.path.join(LIB, "_reference/_post_matches")   # not in _reference/posted: the starter pack takes that folder whole
STATS = os.path.join(LIB, "_reference/post_stats.csv"); LOG = os.path.join(LIB, "_reference/post_log.csv")

def trim(g):
    rows = np.where(g.mean(1) > 12)[0]; cols = np.where(g.mean(0) > 12)[0]
    if len(rows) < 4 or len(cols) < 4: return g
    return g[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]

def frames(path, step=0.25, size=160):
    cap = cv2.VideoCapture(path); out = []; t = 0.0
    dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30)
    while True:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, fr = cap.read()
        if not ok: break
        real = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY); s = size / max(g.shape)
        out.append((real, cv2.resize(g, (max(1, int(g.shape[1] * s)), max(1, int(g.shape[0] * s))), interpolation=cv2.INTER_AREA)))
        t += step
        if dur and t > dur: break
    return out

def fingerprint(key, path):
    f = os.path.join(FP, key.lstrip(".") + ".npz")
    if os.path.exists(f) and os.path.getmtime(f) > os.path.getmtime(path):
        try:
            d = np.load(f, allow_pickle=True); return [(t, np.asarray(g, dtype=np.uint8)) for t, g in zip(d["t"], d["g"])]
        except Exception: pass   # a half file (from before the safe writer): made again
    fr = frames(path); os.makedirs(FP, exist_ok=True); buf = io.BytesIO()   # written whole or not at all: a cut-off cache would be trusted next run
    np.savez_compressed(buf, t=np.array([x[0] for x in fr]), g=np.array([x[1] for x in fr], dtype=object), allow_pickle=True); write_atomic(f, buf.getvalue())
    return fr

def crop_to(g, aspect):  # centre-crop g to width/height = aspect
    h, w = g.shape
    if w / h > aspect: nw = int(h * aspect); x = (w - nw) // 2; return g[:, x:x + nw]
    nh = int(w / aspect); y = (h - nh) // 2; return g[y:y + nh, :]

def sig(g):
    g = cv2.resize(g, (24, 24), interpolation=cv2.INTER_AREA).astype(np.float32)
    return ((g - g.mean()) / (g.std() + 1e-6)).ravel()

def library():
    """Every indexed clip (main index and lab pulls): {key: entry}."""
    idx = {}
    for rel in ("_lab/lab_index.json", "_reference/clip_index.json"):   # lab pulls too: rounds use them, so posts do
        ip = os.path.join(LIB, rel)
        if os.path.exists(ip): idx.update(json.load(open(ip))["clips"])
    return idx

def match(video, external=True):
    """The library clips (main index and lab pulls) a posted video most likely uses. external=False skips _external content
    (someone's own shoot, often 100+ GB of camera files: slow to fingerprint the first time)."""
    idx = library()
    if not idx: return []   # nothing indexed yet, so nothing to match against
    lib = {k: v for k, v in idx.items() if v.get("use") != "removed" and v.get("file") and os.path.exists(os.path.join(LIB, v["file"]))
           and (external or not v["file"].startswith("_external content/"))}
    post = [(t, trim(g)) for t, g in frames(video)]
    post = [(t, g) for t, g in post if g.std() > 6]
    if not post: return []
    aspect = np.median([g.shape[1] / g.shape[0] for _, g in post])
    P = np.array([sig(g) for _, g in post])
    best = np.full(len(post), -1.0); who = [None] * len(post); when = [None] * len(post)
    for k, v in lib.items():
        fr = fingerprint(k, os.path.join(LIB, v["file"]))
        if not fr: continue
        L = np.array([sig(crop_to(trim(g), aspect)) for _, g in fr]); ts = [t for t, _ in fr]
        sc = P @ L.T / P.shape[1]
        j = sc.argmax(1); m = sc.max(1)
        for i in range(len(post)):
            if m[i] > best[i]: best[i], who[i], when[i] = m[i], k, ts[j[i]]
    # group consecutive frames by source
    res = []
    for i, (t, _) in enumerate(post):
        if best[i] < 0.6: continue
        if res and res[-1]["clip"] == who[i] and t - res[-1]["post_out"] <= 0.6:
            r = res[-1]; r["post_out"] = t; r["src_out"] = when[i]; r["scores"].append(best[i])
        else:
            res.append(dict(clip=who[i], post_in=t, post_out=t, src_in=when[i], src_out=when[i], scores=[best[i]], pairs=[]))
        res[-1]["pairs"].append((t, when[i], best[i]))
    def shown(ps):   # the frame pairs (post time @ source time) to confirm a stretch by eye: its best one, and its first and last
        top = max(ps, key=lambda p: p[2]); out = [top]   # when their source time doesn't follow it: a source that is itself an
        for p in (ps[0], ps[-1]):                        # edit matches out of order ("src 7.10-3.03"), and one stretch can hold two shots
            if p not in out and abs((p[1] - top[1]) - (p[0] - top[0])) > 0.5: out.append(p)
        return ["%.2f@%.2f" % p[:2] for p in sorted(out)]
    out = [dict(clip=r["clip"], post="%.2f-%.2f" % (r["post_in"], r["post_out"]), src="%.2f-%.2f" % (r["src_in"], r["src_out"]),
                score=round(float(np.mean(r["scores"])), 2), frames=len(r["scores"]),
                sure=bool(np.mean(r["scores"]) >= 0.75), at=shown(r["pairs"])) for r in res if len(r["scores"]) >= 2]
    return out

def gaps(video, found, least=1.0):
    """The stretches of a post that no match covers (each at least `least` s): they must be checked too; a stretch may come
    from a clip that isn't in the library."""
    cap = cv2.VideoCapture(video); dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / (cap.get(cv2.CAP_PROP_FPS) or 30); cap.release()
    spans = sorted(tuple(float(x) for x in m["post"].split("-")) for m in found); out = []; t = 0.0
    for a, b in spans + [(dur + 0.25, dur + 0.25)]:
        if a - 0.25 - t >= least: out.append("%.2f-%.2f" % (t, min(a - 0.25, dur)))
        t = max(t, b + 0.25)   # a match's frames are 0.25 s apart: each covers a quarter second either side
    return out

def sheet(video, found, holes, out):
    """One picture to confirm a post by eye: a row per matched stretch (the post's frame | the source clip's frame it matched, for
    each pair in "at"), then a row per stretch with no match (the post's frame alone)."""
    from shot_check import frame_at   # the exact frame at a time (a plain OpenCV seek can land up to a second off)
    idx = library(); H = 360; rows = []
    def fit(fr): return None if fr is None else cv2.resize(fr, (max(1, round(fr.shape[1] * H / fr.shape[0])), H), interpolation=cv2.INTER_AREA)
    mid = lambda span: sum(float(x) for x in span.split("-")) / 2
    for m in found:
        c = idx.get(m["clip"]) or {}; src = os.path.join(LIB, c["file"]) if c.get("file") else ""
        at = [tuple(float(x) for x in a.split("@")) for a in m.get("at") or []] or [(mid(m["post"]), mid(m["src"]))]
        rows.append(("post %s s  |  %s %s s  score %.2f%s  |  shown, post = source: %s" % (m["post"], m["clip"], m["src"], m["score"], "" if m["sure"] else "  NOT SURE",
                                                                                         ", ".join("%.2f = %.2f" % a for a in at)),
                     [im for pt, stt in at for im in (fit(frame_at(video, pt)), fit(frame_at(src, stt) if os.path.exists(src) else None))]))
    for g in holes: rows.append(("post %s s  |  no match in the library" % g, [fit(frame_at(video, mid(g))), None]))
    if not rows: return None
    rows = [(label.encode("ascii", "replace").decode(), ims) for label, ims in rows]   # the drawn font has no accents
    gap = lambda j: 24 if j % 2 else 8   # post | source, then a wider gap before the next pair
    W = max(max(sum((im.shape[1] if im is not None else H) + gap(j) for j, im in enumerate(ims)), cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)[0][0] + 12)
            for label, ims in rows); can = []
    for label, ims in rows:
        strip = np.full((30, W, 3), 255, np.uint8); cv2.putText(strip, label, (6, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
        row = np.full((H, W, 3), 40, np.uint8); x = 0
        for j, im in enumerate(ims):
            w = im.shape[1] if im is not None else H
            if im is not None: row[:, x:x + w] = im
            x += w + gap(j)
        can += [strip, row, np.full((6, W, 3), 255, np.uint8)]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    write_atomic(out, cv2.imencode(".jpg", np.vstack(can), [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
    return out

def list_posts(acct):
    r = subprocess.run(["python3", "-m", "yt_dlp", "--impersonate", "chrome", "--flat-playlist", "--print", "%(id)s",
                        "https://www.tiktok.com/@" + acct], capture_output=True, text=True, timeout=180)
    return [x.strip() for x in r.stdout.split() if x.strip().isdigit()], (r.stderr.strip().splitlines() or [""])[-1]

def logged():
    """Post ids already in the post log: any 15+ digit number in it (an id column, or a link to the post)."""
    return set(re.findall(r"\d{15,}", open(LOG, encoding="utf-8").read())) if os.path.exists(LOG) else set()

def pending():
    """Downloaded posts of the profile's accounts that aren't in the post log yet: [(account, id, mp4)]."""
    done = logged(); out = []
    for acct in ACCOUNTS:
        for p in sorted(glob.glob(os.path.join(POSTED, glob.escape(acct) + "_*.mp4"))):
            vid = os.path.basename(p)[len(acct) + 1:-4]
            if vid.isdigit() and vid not in done: out.append((acct, vid, p))
    return out

def saved(acct, vid, mp4, external=False):
    """A post's match from an earlier run, if it's still good: made after the post was downloaded, and against _external
    content too when that's asked for now. None otherwise."""
    f = os.path.join(MATCHES, "%s_%s.json" % (acct, vid))
    try:
        d = json.load(open(f))
        if os.path.getmtime(f) >= os.path.getmtime(mp4) and (d.get("external") or not external): return d
    except (OSError, ValueError): pass
    return None

def fetch():
    """List each account's posts, download the new ones, snapshot every post's stats. Network only: no matching."""
    os.makedirs(POSTED, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y-%m-%d %H:%M"); rows = []
    for acct in ACCOUNTS:
        try: ids, err = list_posts(acct)
        except subprocess.TimeoutExpired: ids, err = [], "timed out"
        if not ids: print("%s: no public posts listed (%s)" % (acct, err[:120])); continue
        for vid in ids:
            base = os.path.join(POSTED, "%s_%s" % (acct, vid)); had = os.path.exists(base + ".mp4")
            try:
                if not had:
                    subprocess.run(["python3", "-m", "yt_dlp", "--impersonate", "chrome", "-q", "-o", base + ".%(ext)s", "--write-info-json",
                                    "https://www.tiktok.com/@%s/video/%s" % (acct, vid)], timeout=180)
                else:  # refresh stats
                    subprocess.run(["python3", "-m", "yt_dlp", "--impersonate", "chrome", "-q", "--skip-download", "--write-info-json", "-o", base + ".%(ext)s",
                                    "https://www.tiktok.com/@%s/video/%s" % (acct, vid)], timeout=120)
            except subprocess.TimeoutExpired: print("%s %s: timed out (the next sync tries again)" % (acct, vid))   # one slow post doesn't stop the rest
            if not had and not os.path.exists(base + ".mp4"): print("%s %s: couldn't download it" % (acct, vid))
            try: d = json.load(open(base + ".info.json"))
            except Exception: continue
            rows.append([today, acct, vid, d.get("upload_date"), d.get("view_count"), d.get("like_count"), d.get("comment_count"),
                         d.get("repost_count"), d.get("save_count"), (d.get("track") or "") + " - " + (d.get("artist") or ""), (d.get("description") or "")[:120]])
    head = ["checked", "account", "id", "posted", "views", "likes", "comments", "shares", "saves", "sound", "caption"]
    append_csv(STATS, [dict(zip(head, r)) for r in rows], fields=head)
    for r in rows: print("stats", r[1], r[2], "views", r[4], "likes", r[5], "saves", r[8], "shares", r[7])

def match_pending(external=False, redo=False):
    """Match every post not in the post log yet (not only the ones this run downloaded), saving each match as it's made."""
    new = pending()
    for acct, vid, path in new:
        print("NEW POST", acct, vid, flush=True)
        d = None if redo else saved(acct, vid, path, external)
        if d is None:
            found = match(path, external)
            d = dict(account=acct, id=vid, post=os.path.relpath(path, LIB), external=external, matched=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                     matches=found, unmatched=gaps(path, found))
            os.makedirs(MATCHES, exist_ok=True); save_json(os.path.join(MATCHES, "%s_%s.json" % (acct, vid)), d)
        jpg = os.path.join(MATCHES, "%s_%s.jpg" % (acct, vid))
        if not os.path.exists(jpg) or os.path.getmtime(jpg) < os.path.getmtime(os.path.join(MATCHES, "%s_%s.json" % (acct, vid))):
            try: sheet(path, d["matches"], d["unmatched"], jpg)
            except Exception as e: print("    couldn't make the picture to confirm it: %s" % e)
        for m in d["matches"]: print("   ", m)
        for g in d["unmatched"]: print("    no match: post %s s" % g)
        if os.path.exists(jpg): print("    to confirm by eye:", os.path.relpath(jpg, LIB))
    print("%d new post(s): confirm each by eye before retiring its clips (a picture each, in _reference/_post_matches/)" % len(new) if new else "no new posts")

def main():
    if "--match" in sys.argv:
        for m in match(sys.argv[sys.argv.index("--match") + 1], "--no-external" not in sys.argv): print(m)
        return
    if not ACCOUNTS: return print("no TikTok accounts in library/profile.json: nothing to sync")
    whole = not ({"--fetch", "--pending"} & set(sys.argv))
    if whole or "--fetch" in sys.argv: fetch()
    if whole or "--pending" in sys.argv: match_pending("--external" in sys.argv, "--redo" in sys.argv)
    else: print("%d post(s) not in the post log yet (match them: --pending)" % len(pending()))

if __name__ == "__main__": main()
