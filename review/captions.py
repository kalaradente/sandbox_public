#!/usr/bin/env python3
"""Caption grades: a list of caption lines on a private page (review/captions.html), each graded Yes / Almost / No with a note
or the reviewer's own version, then read back into a bank of lines that says which ones a real person would write.

  python3 review/captions.py doc <set> <lines.csv> [--title "..."] [--intro "..."] [--group <column>] [--new]
        the page's doc (db: sets/<set>) -> library/_lab/captions/<set>/set.json. lines.csv has a column "text" (or
        "comment") and, if known, "under" (or "video": what the line was said under), "likes" (1234, "1,234", "1.2K"),
        "source" (or "url"); a file with neither "text" nor "comment" is refused. --group: a column to put headings by
        (rows keep the file's order inside each group). A line's id is made from its words, so the same line in two sets
        is one line in the bank. --new: leave out lines the bank already holds and lines another set already sent, graded
        or not (a catalog that grows, like the For You page's comments.csv, sent again after each run).
  python3 review/captions.py apply <set> <grades.json | folder>
        the page's grades read back (ArtifactData's out_dir, or one file) -> grades.csv in the set's folder, and into
        library/_lab/captions/bank.csv (one row a line: its newest grade and note), and the table printed. A grade
        cleared on the page (the lit one tapped again, no note) leaves both: the row this set put in the bank is taken out.

  python3 review/captions.py own "<line>" [--note "..."] [--on "<the line or picture it was said about>"]
        a line the reviewer wrote themselves (their version in a note, or said in the chat) -> the bank, grade "own":
        its own row, beside the graded row of a line with the same words, never over it.

The page stores sets/<set> {set, title, intro, pushed, lines: [{id, text, under, likes, group}]} and grades/<set>__<id>
{set, id, text, grade: "yes" | "almost" | "no" | "", note, at}. A grade is a verdict on that line, and a note is about that
line: nothing is drawn from them without asking."""
import csv, datetime, hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_scripts"))
from sandbox_paths import LIB, locked, save_json, write_atomic  # noqa: E402

OUT = os.path.join(LIB, "_lab", "captions")
BANK = os.path.join(OUT, "bank.csv")
FIELDS = ["id", "text", "grade", "note", "under", "likes", "source", "set", "graded_at"]
line_id = lambda text: hashlib.sha1(" ".join(text.lower().split()).encode()).hexdigest()[:10]


def likes(s):
    """A like count as a number: 1234, "1,234", "1.2K", "3M". Empty = 0; anything else that doesn't read = None."""
    m = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmM]?)", str(s or "0").strip())
    return int(round(float(m.group(1).replace(",", "")) * {"": 1, "k": 1000, "m": 1000000}[m.group(2).lower()])) if m else None


def doc(name, path, title, intro, group, new=False):
    rd = csv.DictReader(open(path, newline="", encoding="utf-8-sig")); rows = list(rd); lines = []; src = {}; odd = []   # utf-8-sig: a CSV saved with a BOM reads like any other (its first column kept its name)
    if not {"text", "comment"} & set(rd.fieldnames or []):
        sys.exit('%s has no column "text" (or "comment"): nothing to send. Its columns: %s' % (path, ", ".join(rd.fieldnames or []) or "none"))
    seen = {r["id"] for r in csv.DictReader(open(BANK, newline=""))} if new and os.path.exists(BANK) else set()
    for other in sorted(os.listdir(OUT)) if new and os.path.isdir(OUT) else []:   # sent in an earlier set and not graded yet: not asked twice
        f = os.path.join(OUT, other, "set.json")
        if other != name and os.path.exists(f): seen |= {l["id"] for l in json.load(open(f)).get("lines", [])}
    order = []   # groups in the order they first appear
    for r in rows:
        text = (r.get("text") or r.get("comment") or "").strip(); i = line_id(text)
        if not text or i in seen: continue
        seen.add(i); g = (r.get(group) or "").strip() if group else ""
        if g not in order: order.append(g)
        n = likes(r.get("likes"))
        if n is None: odd.append(r.get("likes")); n = 0
        lines.append(dict(id=i, text=text, under=(r.get("under") or r.get("video") or "").strip(), likes=n, group=g))
        src[i] = (r.get("source") or r.get("url") or "").strip()
    lines.sort(key=lambda l: order.index(l["group"]))   # stable: the file's order inside each group
    d = os.path.join(OUT, name); os.makedirs(d, exist_ok=True)
    body = dict(set=name, title=title or name, intro=intro or "", pushed=datetime.datetime.now().isoformat(timespec="seconds"), lines=lines)
    save_json(os.path.join(d, "set.json"), body); save_json(os.path.join(d, "sources.json"), src)
    print("%d lines -> %s" % (len(lines), os.path.join(d, "set.json")))
    if odd: print("!! like counts that don't read as a number, sent as none (%d): %s" % (len(odd), ", ".join(repr(x) for x in odd[:5])))


def rows(path):
    if os.path.isdir(path):   # ArtifactData out_dir: <path>/grades/<id>.json (or the files straight in it)
        sub = os.path.join(path, "grades"); p = sub if os.path.isdir(sub) else path
        got = [json.load(open(os.path.join(p, f))) for f in sorted(os.listdir(p)) if f.endswith(".json")]
    else:
        got = json.load(open(path))
    if isinstance(got, dict): got = got.get("docs") or got.get("documents") or got.get("results") or ([got] if got.get("id") or isinstance(got.get("data"), dict) else list(got.values()))
    for r in got:
        body = r.get("data") if isinstance(r, dict) and isinstance(r.get("data"), dict) else r
        if isinstance(body, dict) and body.get("set") and body.get("id"): yield body


def apply(name, path):
    d = os.path.join(OUT, name); lines = {l["id"]: l for l in json.load(open(os.path.join(d, "set.json")))["lines"]}
    src = json.load(open(os.path.join(d, "sources.json"))); out = []; got = {}
    for g in rows(path):   # one doc a line: the same one read back twice (two pages of a listing, a file and its copy) is taken once, the newest
        if g["set"] == name and g["id"] in lines and (g["id"] not in got or (g.get("at") or "") >= (got[g["id"]].get("at") or "")): got[g["id"]] = g
    for g in got.values():
        if not (g.get("grade") or (g.get("note") or "").strip()): continue
        l = lines[g["id"]]
        out.append(dict(id=l["id"], text=l["text"], grade=g.get("grade") or "", note=(g.get("note") or "").strip(), under=l.get("under") or "",
                        likes=l.get("likes") or "", source=src.get(l["id"], ""), set=name, graded_at=g.get("at") or ""))
    cleared = set(got) - {x["id"] for x in out}   # graded, then the lit grade tapped again with no note: the page holds an empty doc for the line
    if not out and not cleared: sys.exit("no grades for %s in %s" % (name, path))
    out.sort(key=lambda x: list(lines).index(x["id"]))
    text = lambda rs: "".join(_csv(r) for r in [dict(zip(FIELDS, FIELDS))] + rs)
    write_atomic(os.path.join(d, "grades.csv"), text(out))
    with locked(BANK) as p:   # one row a line: this set's grade replaces an older one of the same line; a line the reviewer wrote ("own") keeps its row beside it
        old = list(csv.DictReader(open(p, newline=""))) if os.path.exists(p) else []
        new = {x["id"] for x in out}; gone = [r for r in old if r["id"] in cleared and r.get("set") == name]   # a cleared grade leaves the bank too: only the row this set put there
        write_atomic(p, text([r for r in old if r not in gone and (r["id"] not in new or r.get("grade") == "own")] + out))
    for k in ("yes", "almost", "no", ""):
        part = [x for x in out if x["grade"] == k]
        if not part: continue
        print("%s (%d)" % (k or "a note, no grade", len(part)))
        for x in part: print("  %s%s" % (x["text"], ("   | " + x["note"]) if x["note"] else ""))
    for r in gone: print("cleared on the page, out of the bank: %s (it was %s)" % (r["text"], r.get("grade") or "a note"))
    print("%d of %d graded -> %s, and the bank %s" % (sum(1 for x in out if x["grade"]), len(lines), os.path.join(d, "grades.csv"), BANK))


def own(text, note, on):
    row = dict(id=line_id(text), text=text.strip(), grade="own", note=note or "", under=on or "", likes="", source="their own words", set="",
               graded_at=datetime.datetime.now().isoformat(timespec="seconds"))
    with locked(BANK) as p:
        old = list(csv.DictReader(open(p, newline=""))) if os.path.exists(p) else []
        # beside a graded row of the same words, never over it (its grade, note, likes and source stay); the same own line again replaces the older own row
        write_atomic(p, "".join(_csv(r) for r in [dict(zip(FIELDS, FIELDS))] + [r for r in old if r["id"] != row["id"] or r.get("grade") != "own"] + [row]))
    print("own  %s" % row["text"])


def _csv(row):
    import io
    s = io.StringIO(); csv.DictWriter(s, fieldnames=FIELDS, extrasaction="ignore").writerow(row); return s.getvalue()


def main():
    a = sys.argv[1:]; opt = lambda name: a[a.index(name) + 1] if name in a else None
    if len(a) >= 3 and a[0] == "doc": return doc(a[1], a[2], opt("--title"), opt("--intro"), opt("--group"), "--new" in a)
    if len(a) == 3 and a[0] == "apply": return apply(a[1], a[2])
    if len(a) >= 2 and a[0] == "own" and not a[1].startswith("--"): return own(a[1], opt("--note"), opt("--on"))
    sys.exit(__doc__)


if __name__ == "__main__": main()
