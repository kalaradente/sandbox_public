#!/usr/bin/env python3
"""Phone review: review a round from the phone through a private claude.ai page (review/phone.html), then apply the verdicts
on this Mac through the same code the review page uses (serve.decide), so files, review.json, lab_log.csv, note tags and
exports come out exactly as if the person had clicked on the Mac.

  python3 review/phone.py prep <round> [--ids ID ...]   phone copies (long side 1280, H.264, faststart) of each edit's current
                                                        video -> library/_lab/phone/<round>/<id>.mp4; prints their paths
  python3 review/phone.py doc <round> <uploads.json>    uploads.json = {"<id>": "<asset id>"} from the upload; writes
                                                        library/_lab/phone/<round>/round_doc.json (db: rounds/<round>)
  python3 review/phone.py apply <verdicts.json | folder> verdicts.json = the page's verdicts collection read back (or the folder
                                                        ArtifactData's out_dir wrote: one <id>.json per verdict):
                                                        [{"round", "id", "decision", "note", "topics", "applied"}...] (or {"docs": [...]},
                                                        rows may wrap the body in "data"; or one verdict on its own); applies
                                                        each one not applied yet and writes <verdicts>.applied.json (id -> time)
                                                        to mark them in db. A verdict older than the Mac's own decision for that
                                                        edit (review.json "at": decided at the desk after the phone) is skipped
                                                        and reported, never applied over it (it stays unapplied in db).

The page stores: rounds/<round> {round, date, notes, pushed, topic_names:[[key, label]...], edits:[{id, asset, account_name,
kind, format, caption, story, why, window, lyrics, decision, note, topics}]} and verdicts/<id> {round, id, decision, note,
topics, at, applied}. Decisions are serve.py's: kept, perfect, deleted (moved to library/_to_delete, never erased). topics =
the reviewer's taps (serve.TOPICS keys; the page offers the round doc's topic_names, so a round sent before taps has none);
a verdict without topics (sent before taps) leaves the Mac's as they are. The round doc is built from serve.state(), the
page's allow-list: a recipe's sealed forecast never reaches the phone."""
import datetime, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import serve  # noqa: E402  (also puts _scripts on the path)
from sandbox_paths import LIB, locked, save_json  # noqa: E402

OUT = os.path.join(LIB, "_lab", "phone")


def proxy(src, dst):
    if os.path.exists(dst) and os.path.getmtime(dst) >= os.path.getmtime(src): return dst
    tmp = dst + ".tmp.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0?",
                        "-vf", "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))'",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", tmp], capture_output=True, text=True)
    if r.returncode: raise RuntimeError("ffmpeg failed on %s: %s" % (src, r.stderr.strip()[-300:]))
    os.replace(tmp, dst); return dst


def prep(rnd, ids=None):
    st = serve.state(rnd); d = os.path.join(OUT, rnd); os.makedirs(d, exist_ok=True)
    for e in st["edits"]:
        if ids and e["id"] not in ids: continue
        src, _ = serve.find(rnd, e["id"])
        if not src: print("  (no video) %s" % e["id"], file=sys.stderr); continue
        p = proxy(src, os.path.join(d, e["id"] + ".mp4"))
        print(p)


def doc(rnd, uploads):
    up = json.load(open(uploads)); st = serve.state(rnd); edits = []
    for e in st["edits"]:
        if e["id"] not in up: continue
        song = e.get("song") if isinstance(e.get("song"), dict) else {}
        edits.append(dict(id=e["id"], asset=up[e["id"]], account_name=e.get("account_name") or e.get("account") or "",
                          kind=e.get("kind") or "", format=e.get("format") or "", caption=e.get("caption") or "",
                          story=e.get("story") or "", why=e.get("why") or "",
                          window=song.get("window") or (os.path.basename(song["file"]) if song.get("file") else ""),
                          lyrics=bool(e.get("lyrics")), decision=e.get("decision") or "", note=e.get("note") or "",
                          topics=e.get("topics") or []))
    missing = sorted(set(up) - {e["id"] for e in edits})
    if missing: sys.exit("not in %s: %s" % (rnd, ", ".join(missing)))
    body = dict(round=rnd, date=st.get("date") or "", notes=st.get("notes") or "", topic_names=st["topic_names"],
                pushed=datetime.datetime.now().isoformat(timespec="seconds"), edits=edits)
    p = os.path.join(OUT, rnd, "round_doc.json"); save_json(p, body)
    print(p)


def rows(path):
    if os.path.isdir(path):   # ArtifactData out_dir: <path>/verdicts/<id>.json (or the files straight in it)
        sub = os.path.join(path, "verdicts"); d = sub if os.path.isdir(sub) else path
        d = [json.load(open(os.path.join(d, f))) for f in sorted(os.listdir(d)) if f.endswith(".json") and not f.endswith(".applied.json")]
    else:
        d = json.load(open(path))
    if isinstance(d, dict):   # one verdict on its own (plain or wrapped in "data"), or a collection read back
        d = [d] if (d.get("id") and d.get("round")) or isinstance(d.get("data"), dict) else d.get("docs") or d.get("documents") or d.get("results") or list(d.values())
    for r in d:
        body = r.get("data") if isinstance(r.get("data"), dict) else r
        if body.get("id") and body.get("round"): yield body


def when(s):
    """An ISO time as an aware datetime (the phone's is UTC with a Z; review.json's is this Mac's local time), or None."""
    try: t = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError: return None
    return t if t.tzinfo else t.astimezone()


def apply(path):
    done = {}; skipped = 0; now = datetime.datetime.now().isoformat(timespec="seconds")
    for v in rows(path):
        if v.get("applied"): continue
        dec = {"keep": "kept", "delete": "deleted"}.get(v.get("decision"), v.get("decision") or "")
        try:
            with locked(serve.DECIDING):   # the check and the verdict as one decision: the desk page (another process) may be deciding this edit now
                desk = serve.load_review(v["round"]).get(v["id"]) if v.get("round") in serve.rounds() else None
                if desk and when(v.get("at")) and when(desk.get("at")) and when(v.get("at")) < when(desk.get("at")):   # decided on the Mac after the phone
                    skipped += 1; print("  skipped  %s  the phone's %s (%s) is older than the Mac's %s (%s): not applied" % (v["id"], dec or "clear", v.get("at"), desk.get("decision"), desk.get("at"))); continue
                serve.decide(v["round"], v["id"], dec, (v.get("note") or "").strip(), v.get("topics"))
            done[v["id"]] = dict(applied=now, decision=dec, note=(v.get("note") or "").strip())
            print("  %-8s %s  %s%s" % (dec or "cleared", v["id"], "[%s] " % " ".join(serve.clean_topics(v["topics"])) if v.get("topics") else "", v.get("note") or ""))
        except Exception as e:
            print("!! %s: %s: %s" % (v["id"], type(e).__name__, e))
    out = (path.rstrip("/") + ".applied.json") if os.path.isdir(path) else os.path.splitext(path)[0] + ".applied.json"; save_json(out, done)
    print("%d applied -> %s%s" % (len(done), out, "; %d skipped (decided on the Mac after the phone): tap again on the phone to change them" % skipped if skipped else ""))


def main():
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "prep": return prep(a[1], a[a.index("--ids") + 1:] if "--ids" in a else None)
    if len(a) == 3 and a[0] == "doc": return doc(a[1], a[2])
    if len(a) == 2 and a[0] == "apply": return apply(a[1])
    sys.exit(__doc__)


if __name__ == "__main__": main()
