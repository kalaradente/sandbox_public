#!/usr/bin/env python3
"""Lab review page: watch a round's renders and mark each one keep / perfect / delete, with a note.
Run from the sandbox folder:  python3 review/serve.py      (opens http://127.0.0.1:8765; --port N, --no-open)

Every decision has the same effect as doing it by hand in Finder, so the lab task reads it the same way:
  kept     -> library/_lab/keep/<id> <note>.mp4
  perfect  -> library/_lab/perfect children/<id> <note>.mp4
  deleted  -> library/_to_delete/lab_deleted/<round>/<id>.mp4      (moved, never erased)
  noted    -> stays in the round folder, renamed <id> <note>.mp4   (a note or taps, no verdict; Undo leaves an edit here)
Perfect also puts a copy in the edit's account exports folder (library/profile.json "accounts": the account whose id,
name or handle matches the edit's "account"), ready to post. Moving away from perfect takes that copy back out (to
library/_to_delete/exports_removed/<date>/, never over an earlier one). Only the copy the page made comes out (it keeps a
record, library/_lab/review_exports.json): any other file in exports, e.g. a version graded in Resolve, is left alone.
--backfill-exports does the same for every perfect edit already decided.
The filename holds the first 150 characters of the note; the full note also goes into the mp4's own comment tag.
Topics: one tap each for what the verdict is about (TOPICS: opener, caption, pictures, music match, feel, idea, quality;
number keys on the desk page), any number, never required. Not the recipe's "why" (Claude's own reasoning).
It also writes <round>/review.json (decision, note, topics, time per edit) and keeps one row per edit in
library/_lab/lab_log.csv (topics space-separated; the reason column is left for Claude). The page is sent only the fields
state() names, so anything else in a recipe (its sealed "forecast") never reaches it. Localhost only; standard library only."""
import csv, datetime, io, json, os, re, shutil, subprocess, sys, threading, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_scripts"))
from sandbox_paths import LIB, find_account, edit_json, locked, round_dirs, save_json, write_atomic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.join(LIB, "_lab"); ROUNDS = os.path.join(LAB, "rounds"); LOG = os.path.join(LAB, "lab_log.csv")
PLACES = {"kept": os.path.join(LAB, "keep"), "perfect": os.path.join(LAB, "perfect children")}
TRASH = os.path.join(LIB, "_to_delete", "lab_deleted")
LOG_COLS = ["date", "round", "id", "account", "kind", "song", "caption", "sources", "decision", "note", "reason", "topics"]   # song = <song> <window>; topics last, so a row written with the older columns still lines up
MADE = os.path.join(LAB, "review_exports.json")   # {id: {file (in the library), size}}: the export copies this page made, the only ones it moves
# What a verdict is about, one tap each (taste study, idea 2: the taps say why in 2 seconds, where a verdict with no note
# left Claude guessing). Keys are stored; labels shown; the order is the number keys'. The one list: both pages get it.
TOPICS = [("opener", "opener"), ("caption", "caption"), ("pictures", "pictures"), ("music", "music match"), ("feel", "feel"),
          ("idea", "idea"), ("quality", "quality")]
LOCK = threading.Lock()
DECIDING = os.path.join(LAB, "review")   # its lock (_lab/.review.lock) is held for one whole decision: a phone apply is another process


def parts(rnd):
    """The round's folders: one straight in _lab/rounds or in one of the folders a team sorts its rounds into (layout.json
    "round_folders"), or several when its pieces were sorted between those."""
    return round_dirs().get(rnd) or [os.path.join(ROUNDS, rnd)]


def rdir(rnd, eid=None, ps=None):
    """The folder an edit's files are in (its recipe, its render, its verdict): where its recipe is, else where a manifest lists it."""
    ps = ps or parts(rnd)
    for p in ps if eid and len(ps) > 1 else []:
        if os.path.exists(os.path.join(p, eid + ".json")): return p
    for p in ps if eid and len(ps) > 1 else []:
        try:
            if any(e.get("id") == eid for e in json.load(open(os.path.join(p, "manifest.json"))).get("edits", [])): return p
        except (OSError, ValueError): pass
    return ps[0]


def rounds():
    return sorted((d for d, ps in round_dirs().items() if any(os.path.isfile(os.path.join(p, "manifest.json")) for p in ps)), reverse=True)


def manifest(rnd):
    if rnd not in rounds(): raise KeyError(rnd)
    m = {}
    for p in parts(rnd):   # a round sorted between folders has a manifest in each: one round on the page, its edits in id order
        if os.path.isfile(os.path.join(p, "manifest.json")):
            mm = json.load(open(os.path.join(p, "manifest.json")))
            for e in mm.get("edits", []): e["_dir"] = p
            if m: m["edits"] = sorted(m.get("edits", []) + mm.get("edits", []), key=lambda e: e["id"])
            else: m = mm
    for e in m.get("edits", []):   # the edit's own recipe is newer than the manifest when a caption was changed after rendering
        rp = os.path.join(e.pop("_dir"), e["id"] + ".json")
        if os.path.exists(rp):
            r = json.load(open(rp)); e.update({k: r[k] for k in ("caption", "story", "why", "lyrics", "format") if k in r}); e["song"] = r.get("song") or r.get("song")
    return m, {e["id"]: e for e in m.get("edits", [])}


def review_file(rnd, eid=None): return os.path.join(rdir(rnd, eid), "review.json")   # an edit's verdict is kept in its own folder


def load_review(rnd):
    """The round's verdicts, over its folders. An edit's verdict is the one in its own folder (rdir: where its recipe sits, where
    decide writes it): the same id left in another folder's review.json counts only while its own folder has none, never over it."""
    ps = parts(rnd); rv = {}; own = set()
    for p in ps:
        if os.path.exists(os.path.join(p, "review.json")):
            for k, v in json.load(open(os.path.join(p, "review.json"))).items():
                mine = len(ps) == 1 or rdir(rnd, k, ps) == p
                if mine or k not in own: rv[k] = v
                if mine: own.add(k)
    return rv


def find(rnd, eid):
    """Where the edit's video is now: (path, place). the Finder renames keep the ID at the front."""
    pat = re.compile(r"^%s(\s.*)?\.mp4$" % re.escape(eid))
    for place, d in [("round", p) for p in parts(rnd)] + [("kept", PLACES["kept"]), ("perfect", PLACES["perfect"]),
                     ("deleted", os.path.join(TRASH, rnd))]:
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if pat.match(f): return os.path.join(d, f), place
    return None, None


def clean_note(note):
    note = re.sub(r"\s+", " ", re.sub(r"[/:\\\n\r\t]+", " ", note or "")).strip()
    if len(note) > 150: note = note[:150].rsplit(" ", 1)[0].rstrip(",;. ") + "…"   # the full note is in the comment tag
    return note.rstrip(". ")  # Finder-safe filename part


def clean_topics(ts):
    """Taps as a list (or the log's space-separated text), known ones only, in TOPICS order: a tap renamed later drops out."""
    ts = set(ts if isinstance(ts, (list, tuple)) else str(ts or "").split())
    return [k for k, _ in TOPICS if k in ts]


def target(rnd, eid, decision, note):
    name = eid + (" " + clean_note(note) if clean_note(note) and decision != "deleted" else "") + ".mp4"
    d = {"kept": PLACES["kept"], "perfect": PLACES["perfect"], "deleted": os.path.join(TRASH, rnd)}.get(decision, rdir(rnd, eid))
    return os.path.join(d, name)


def tag_note(path, note):
    """Write the full note into the mp4's own comment tag (lossless remux, no re-encode), so it travels with the file
    when it's dragged into Resolve or sent to a phone; the filename only holds 150 characters. Read it back with
    ffprobe -v error -show_entries format_tags=comment -of csv=p=0 <file>. An empty note clears the tag."""
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".tmp")   # hidden, and not *.mp4, so find() never sees it
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0", "-c", "copy", "-metadata", "comment=" + note,
                            "-movflags", "+faststart", "-f", "mp4", tmp], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.getsize(tmp) > 0: os.replace(tmp, path); return True
        print("!! couldn't tag the note into %s: %s" % (os.path.basename(path), r.stderr.strip()[-200:]), flush=True)
    except (OSError, subprocess.SubprocessError) as e:
        print("!! couldn't tag the note into %s: %s" % (os.path.basename(path), e), flush=True)
    if os.path.exists(tmp): os.remove(tmp)
    return False


def export_dir(e):
    """The exports folder of the account this edit was made for, or None if the profile doesn't name one."""
    a = find_account(e.get("account"))
    return os.path.join(LIB, a["exports"]) if a and a.get("exports") else None


def made():
    try: return json.load(open(MADE))
    except (OSError, ValueError): return {}


def save_made(d): save_json(MADE, d)


def page_copy(e, cur=None, d=None):
    """The copy of this edit the page put in its exports folder, while it's still there as made (same size), else None.
    Anything else named <id>… there is the person's own (a version graded in Resolve and saved next to it was swept out
    on a note edit) and is never moved. Copies from before the page kept a record: the one named exactly like the edit's
    file (cur) and the same size, which is all the page ever made."""
    exp = export_dir(e)
    if not exp: return None
    r = (made() if d is None else d).get(e["id"])
    p = os.path.join(LIB, r["file"]) if r else os.path.join(exp, os.path.basename(cur)) if cur else None
    size = r.get("size") if r else os.path.getsize(cur) if cur and os.path.isfile(cur) else -1
    return p if p and os.path.isfile(p) and os.path.getsize(p) == size else None


def claim_export(e, cur):
    """Record an unrecorded copy the page made earlier, before the edit's file is renamed or re-tagged (then it wouldn't match)."""
    d = made()
    if e["id"] in d: return
    p = page_copy(e, cur, d)
    if p: d[e["id"]] = dict(file=os.path.relpath(p, LIB), size=os.path.getsize(p)); save_made(d)


def free_name(folder, name):
    """folder/name, or "<name> (2).mp4" … when one of that name is there already: a second removal never overwrites the first."""
    stem, ext = os.path.splitext(name); p = os.path.join(folder, name); n = 2
    while os.path.exists(p): p = os.path.join(folder, "%s (%d)%s" % (stem, n, ext)); n += 1
    return p


def sync_export(e, path, perfect):
    """Perfect: a copy of the final file (note tag included) in the account's exports folder. Not perfect: the page's own
    copy comes back out. Returns the copy's path when it's there, else None."""
    exp = export_dir(e)
    if not exp: return None
    want = os.path.join(exp, os.path.basename(path)) if perfect and path else None
    d = made(); mine = page_copy(e, path, d); before = dict(d)
    if mine and mine != want:
        gone = os.path.join(LIB, "_to_delete", "exports_removed", datetime.date.today().isoformat())
        os.makedirs(gone, exist_ok=True); shutil.move(mine, free_name(gone, os.path.basename(mine)))
    if not mine or mine != want: d.pop(e["id"], None)   # taken out, gone, or changed by hand since (then it's theirs)
    if want and not os.path.exists(want):
        os.makedirs(exp, exist_ok=True)
        if subprocess.run(["cp", "-c", path, want], capture_output=True).returncode: shutil.copy2(path, want)   # APFS clone: instant, no extra space
        mine = want
    elif want and mine != want:   # a file of that name was there already and isn't the page's: left as it is
        print("!! %s is in exports already and isn't the page's copy: left alone" % os.path.relpath(want, LIB), flush=True); want = None
    if want: d[e["id"]] = dict(file=os.path.relpath(want, LIB), size=os.path.getsize(want))
    if d != before: save_made(d)
    return want


def write_log(rnd, e, decision, note, topics=()):
    """The edit's row out, its new one at the end. Read and written under the log's lock (the desk page and a phone apply, another
    process, write it at the same moment); every other row keeps its own bytes: the reasons Claude writes in, their quotes, CRLF.
    Older rows have no topics: they stay as they are, and a log without the column gets it in its header. A log with other columns
    (the song column's older name) is written whole in today's, once."""
    with locked(LOG) as p:
        src = io.StringIO(open(p, "rb").read().decode("utf-8") if os.path.exists(p) else "", newline=""); got = []
        def lines():   # the csv reader takes a row's lines one at a time: what it took for a row is that row's own text
            for ln in src: got.append(ln); yield ln
        recs = []
        for x in csv.reader(lines()):
            if x: recs.append(("".join(got), x))   # a blank line isn't a row (DictReader skips it too)
            del got[:]
        head = recs[0][1] if recs else []; ours = head in (LOG_COLS, LOG_COLS[:-1])   # the columns this page writes (or all but topics)
        ended = lambda t: t if t.endswith(("\n", "\r")) else t + "\r\n"   # a last row saved by hand without its line break
        rows = [dict(zip(head, x), _text=t) for t, x in recs[1:]]
        for r in rows:
            if "song" in r: r.setdefault("song", r.pop("song") or "")
        old = next((r for r in rows if r.get("id") == e["id"]), None)
        rows = [r for r in rows if r.get("id") != e["id"]]
        if decision:
            sb = e.get("song") if isinstance(e.get("song"), dict) else {}
            mw = " ".join(x for x in ((sb.get("name") or ""), (sb.get("window") or "") if not sb.get("file") else os.path.basename(sb["file"])) if x)
            rows.append(dict(date=datetime.date.today().isoformat(), round=rnd.split("_")[0], id=e["id"],
                             account=e.get("account", ""), kind=e.get("kind", ""), song=mw, caption=e.get("caption", ""),
                             sources=len(e.get("sources") or []), decision=decision, note=note, topics=" ".join(topics),
                             reason=(old or {}).get("reason", "") if old and old.get("decision") == decision else ""))
        s = io.StringIO(); w = csv.DictWriter(s, fieldnames=LOG_COLS, extrasaction="ignore")
        if head == LOG_COLS: s.write(ended(recs[0][0]))
        else: w.writeheader()
        for r in rows:
            if ours and "_text" in r: s.write(ended(r["_text"]))
            else: w.writerow(r)
        write_atomic(p, s.getvalue())


def decide(rnd, eid, decision, note, topics=None):
    """topics None (a caller that doesn't send taps: an older phone verdict) keeps the ones review.json has."""
    if decision not in ("kept", "perfect", "deleted", "noted", ""): raise ValueError(decision)
    m, edits = manifest(rnd)
    if eid not in edits: raise KeyError(eid)
    with LOCK, locked(DECIDING):   # one decision at a time: the page's own threads, and a phone apply (another process)
        cur, _ = find(rnd, eid)
        if not cur: raise FileNotFoundError("%s.mp4 isn't in the round, keep, perfect children or the delete folder" % eid)
        rv = load_review(rnd); was = rv.get(eid) or {}
        topics = clean_topics(was.get("topics") if topics is None else topics)
        claim_export(edits[eid], cur)
        dst = target(rnd, eid, decision, note)
        if os.path.abspath(cur) != os.path.abspath(dst):
            # a note that only changes capitals names the same file on a case-insensitive drive: a rename, not a clash
            if os.path.exists(dst) and not os.path.samefile(cur, dst): raise FileExistsError(dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True); os.rename(cur, dst)
        if note != was.get("note", ""): tag_note(dst, note)
        with edit_json(review_file(rnd, eid)) as rv:   # this edit's entry, into the file as it is now (locked, atomic: the page reads it meanwhile)
            if decision or note or topics: rv[eid] = dict(decision=decision or "noted", note=note, topics=topics, at=datetime.datetime.now().isoformat(timespec="seconds"))
            else: rv.pop(eid, None)
        for p in parts(rnd):   # the same id left in the round's other folder (a piece moved between them by hand): out, so an old verdict never comes back under the new one
            f = os.path.join(p, "review.json")
            if os.path.abspath(f) != os.path.abspath(review_file(rnd, eid)) and os.path.exists(f) and eid in json.load(open(f)):
                with edit_json(f) as o: o.pop(eid, None)
        write_log(rnd, edits[eid], (decision or "noted") if (decision or note or topics) else "", note, topics)
        sync_export(edits[eid], dst, decision == "perfect")
    return state(rnd)


def song_shown(s):
    """The song block as the pages show it: name, window, the sound's file name. Nothing else of it (a forecast inside it too)."""
    if not isinstance(s, dict): return None
    return {k: v for k, v in (("name", s.get("name")), ("window", s.get("window")), ("file", os.path.basename(str(s.get("file") or "")))) if v}


def state(rnd):
    """What the page (and the phone's round doc, built from this) is sent: only the fields named here, an allow-list. A recipe's
    sealed "forecast" (Claude's guess at the verdict) would anchor the reviewer, so it, and any key added later, stays out."""
    m, edits = manifest(rnd); rv = load_review(rnd); out = []; d = made()
    logged = {r["id"]: r for r in csv.DictReader(open(LOG))} if os.path.exists(LOG) else {}
    for e in m.get("edits", []):
        p, place = find(rnd, e["id"]); lg = logged.get(e["id"])
        r = rv.get(e["id"]) or (dict(decision=lg.get("decision"), note=lg.get("note") or "", topics=lg.get("topics")) if lg else {})
        # decided in Finder before the page existed: where the file sits says what they did (gone entirely = deleted)
        guess = {"round": "", "kept": "kept", "perfect": "perfect", "deleted": "deleted", None: "deleted"}.get(place)
        note = r.get("note")
        if note is None and p: note = os.path.splitext(os.path.basename(p))[0][len(e["id"]):].strip()
        exp = export_dir(e); ex = page_copy(e, p, d)
        a = find_account(e.get("account"))
        out.append(dict(id=e["id"], account=e.get("account"), account_name=(a or {}).get("name"), exports=os.path.relpath(exp, LIB) if exp else None,
                        exported=os.path.basename(ex) if ex else None, kind=e.get("kind"), format=e.get("format"), song=song_shown(e.get("song")),
                        lyrics=bool(e.get("lyrics")), caption=e.get("caption", ""), story=e.get("story", ""), why=e.get("why", ""),
                        sources=len(e.get("sources") or []), decision=r.get("decision") or guess or ("noted" if note else ""),
                        note=note or "", topics=clean_topics(r.get("topics")), place=place, video="/media/%s/%s" % (rnd, e["id"]) if p else None))
    return dict(round=rnd, notes=m.get("notes", ""), date=m.get("date"), topic_names=[list(t) for t in TOPICS], edits=out)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def local(self):
        """Only this machine's own page: another website open in the browser can't reach it by name (DNS rebinding)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host in ("127.0.0.1", "localhost", "::1"): return True
        self.send(403, {"error": "forbidden"}); return False

    def do_GET(self):
        if not self.local(): return
        path = unquote(urlparse(self.path).path)
        try:
            if path in ("/", "/index.html"): return self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
            if path == "/api/rounds":
                out = []
                for r in rounds():
                    s = state(r)["edits"]
                    out.append(dict(round=r, total=len(s), decided=sum(1 for e in s if e["decision"])))
                return self.send(200, out)
            if path.startswith("/api/round/"): return self.send(200, state(path[len("/api/round/"):]))
            if path.startswith("/media/"):
                rnd, eid = path[len("/media/"):].split("/", 1)
                if eid not in manifest(rnd)[1]: raise KeyError(eid)
                return self.video(find(rnd, eid)[0])
            self.send(404, {"error": "not found"})
        except (KeyError, ValueError, FileNotFoundError, TypeError) as e:
            self.send(404, {"error": str(e)})

    def video(self, p):
        if not p: return self.send(404, {"error": "file missing"})
        size = os.path.getsize(p); start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and not (m.group(1) or m.group(2)): m = None   # "bytes=-": the whole file
        if m:
            if m.group(1): start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else end
            else: start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416); self.send_header("Content-Range", "bytes */%d" % size); self.send_header("Content-Length", "0"); self.end_headers(); return
        n = end - start + 1
        self.send_response(206 if m else 200); self.send_header("Content-Type", "video/mp4"); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(n))
        if m: self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        with open(p, "rb") as f:
            f.seek(start)
            while n > 0:
                chunk = f.read(min(n, 1 << 20))
                if not chunk: break
                try: self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError): return
                n -= len(chunk)

    def do_POST(self):
        if not self.local(): return
        # JSON only: a plain form or text post from another website (no preflight) can't move files
        if not (self.headers.get("Content-Type") or "").startswith("application/json"): return self.send(415, {"error": "JSON only"})
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if urlparse(self.path).path != "/api/decide": return self.send(404, {"error": "not found"})
            self.send(200, decide(d["round"], d["id"], d.get("decision", ""), (d.get("note") or "").strip(), d.get("topics")))
        except Exception as e:
            self.send(400, {"error": "%s: %s" % (type(e).__name__, e)})


def backfill_exports():
    """Copy every video in library/_lab/perfect children/ (review page or Finder) into its account's exports folder."""
    n = skipped = 0
    pc = PLACES["perfect"]
    files = sorted(f for f in os.listdir(pc) if f.endswith(".mp4")) if os.path.isdir(pc) else []
    edits = {}
    for rnd in rounds(): edits.update(manifest(rnd)[1])
    for f in files:
        eid = next((i for i in sorted(edits, key=len, reverse=True) if re.match(r"^%s(\s.*)?\.mp4$" % re.escape(i), f)), None)
        if not eid: skipped += 1; print("  (not from a lab round) %s" % f); continue
        if not export_dir(edits[eid]): skipped += 1; print("  (no exports folder for account %r) %s" % (edits[eid].get("account"), f))
        else:
            with locked(DECIDING): ok = sync_export(edits[eid], os.path.join(pc, f), True)   # as one decision: the page may be deciding now (another process)
            if ok: n += 1; print("  exports <-", f)
            else: skipped += 1   # sync_export said why (a file of that name there that isn't the page's)
    print("%d perfect edits in exports, %d skipped" % (n, skipped))


def main():
    if "--backfill-exports" in sys.argv: return backfill_exports()
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    url = "http://127.0.0.1:%d" % port
    print("Lab review page: %s   (library: %s)   Ctrl+C to stop" % (url, LIB), flush=True)
    if "--no-open" not in sys.argv:   # their default browser: macOS "open" (webbrowser follows a BROWSER setting, which can point at Chrome)
        if sys.platform != "darwin" or subprocess.run(["open", url]).returncode: webbrowser.open(url)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == "__main__": main()
