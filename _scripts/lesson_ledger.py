#!/usr/bin/env python3
"""The lesson ledger: every lesson with the verdicts behind it and a status. One round used to become a law that nothing
retired, and Claude's own guessed reasons for note-less verdicts were counted as support; the ledger shows both.
  python3 _scripts/lesson_ledger.py [ledger.json]        default: library/_lab/lesson_ledger.json
One line per lesson: its status, whose words it is, the verdicts behind it (perfect/kept/deleted from library/_lab/lab_log.csv;
"noted" counts as kept) and, where the ledger says how to tell, the edits made after it that followed it against those that
didn't. Then where each lesson lives now (the line its words are on). It never writes: the ledger is written by hand (the
house's: the reviewer's own words quoted, never Claude's), and the lessons stay in their own files. Only the reviewer makes
lessons: what Claude noticed is a "suggestion" until they say yes, and what was drawn from a verdict with no note is "disregarded".
The ledger (JSON):
  {"lessons": [{
    "id": "silent-proven",                   a short name
    "words": "Silent pieces are the proven format now",
    "where": [{"file": "_docs/lab-lessons.md", "find": "Silent pieces are the proven format now"}],
                                             sandbox-relative (library/... = the library); "find" = text on the lesson's line
    "whose": "reviewer" | "claude",          the reviewer's own note, or Claude's reading or reason
    "round": "R05",                          written after this round ("" = before the first); "after it" = the rounds after
    "behind": ["R05_1_05", ...],            the verdicts it was drawn from
    "scope": {"kind": "video"}, "followed": {"song": ""}, "broke": {"song": "*"},
                                             which later edits followed it and which didn't: a filter on lab_log.csv's columns
                                             ("" empty, "*" anything, "<=3" ">=6" numbers, "~word" contains, "a|b" one of, else
                                             equal), narrowed by scope; or a list of edit ids where the log can't tell
    "effects_rounds": false,                 filters leave out rounds where every edit carries an effect (there "deleted" meant
                                             "try again"); true counts them
    "status": "open" | "held" | "contradicted" | "settled" | "suggestion" | "disregarded",
                                             suggestion: Claude's own reading, not a lesson: the reviewer is asked first; disregarded:
                                             drawn from verdicts that came with no note (or a note like "very good": they liked it)
    "settled": {"date": "2026-09-29", "words": "<the reviewer's words that settled it>"},
    "note": "<the evidence, briefly>"}]}
Flags: one round (every verdict behind it is from one round); reasons only (no verdict behind it carries the reviewer's note,
so the only why is a reason Claude wrote); Claude's words; ids not in the log; + = counts rounds judged before the review
page, where "perfect" wasn't always a choice (compare deletes there, not perfects)."""
import csv, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, ROOT, round_order  # noqa: E402  (--help stops there, before anything runs)
import forecast as F  # noqa: E402  (one rule for what an effects round is, and for "noted")

STATUS = ("open", "held", "contradicted", "settled", "suggestion", "disregarded"); WHOSE = ("reviewer", "claude")


def cond(v, c):
    v = (v or "").strip(); c = str(c)
    if c == "": return v == ""
    if c == "*": return v != ""
    m = re.match(r"^(<=|>=|<|>)\s*(-?[\d.]+)$", c)
    if m:
        try: x, y = float(v), float(m.group(2))
        except ValueError: return False
        return {"<=": x <= y, ">=": x >= y, "<": x < y, ">": x > y}[m.group(1)]
    if c.startswith("~"): return c[1:].lower() in v.lower()
    return v.lower() in [x.strip().lower() for x in c.split("|")]


def count(rows):
    c = [0, 0, 0]
    for r in rows:
        y = F.verdict(r["decision"])
        if y in F.CLS: c[F.CLS.index(y)] += 1
    return c


def where_now(w):
    """'file:line' for each place the lesson's words are found, or 'file: not found'."""
    out = []
    for p in (w if isinstance(w, list) else [w] if w else []):
        f = str(p.get("file") or ""); find = str(p.get("find") or "").lower()
        path = os.path.join(LIB, f[len("library/"):]) if f.startswith("library/") else os.path.join(ROOT, f)
        try: n = next((k for k, line in enumerate(open(path, encoding="utf-8"), 1) if find and find in line.lower()), None)
        except OSError: n = None
        out.append("%s:%s" % (f, n) if n else "%s: not found" % f)
    return out


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("-")]
    path = a[0] if a else os.path.join(F.LAB, "lesson_ledger.json")
    try: led = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e: sys.exit("Can't read the ledger %s: %s" % (path, e))
    log = F.read_log(); L = {r["id"]: r for r in log}
    rounds = sorted({r["round"] for r in log}, key=round_order); fd = F.folders()
    fx = {x for x in rounds if F.effects_round(fd.get(x))}; early = {x for x in rounds if not F.on_page(fd.get(x))}
    pkd = lambda rs: "%d/%d/%d%s" % (*count(rs), "+" if any(r["round"] in early for r in rs) else "")
    row = "%-26s %-12s %-8s %-10s %-15s %-14s %-14s %s"
    print(row % ("lesson", "status", "whose", "behind", "from", "followed", "didn't", "flags"))
    print(row % ("", "", "", "p/k/d", "", "(after it)", "(after it)", ""))
    bystatus = {}
    for les in led.get("lessons", []):
        i = str(les.get("id") or "?"); st = les.get("status") or ""; who = les.get("whose") or ""
        behind = [x for x in les.get("behind") or []]; rows = [L[x] for x in behind if x in L]; gone = [x for x in behind if x not in L]
        rs = sorted({r["round"] for r in rows}, key=round_order)
        span = rs[0] if len(rs) == 1 else "%s..%s (%d)" % (rs[0], rs[-1], len(rs)) if rs else "-"
        after = [r for r in log if not les.get("round") or round_order(r["round"])[0] > round_order(les["round"])[0]]
        after = [r for r in after if les.get("effects_rounds") or r["round"] not in fx]
        side = {}
        for k in ("followed", "broke"):
            spec = les.get(k)
            if isinstance(spec, list): side[k] = [L[x] for x in spec if x in L]; gone += [x for x in spec if x not in L]
            elif isinstance(spec, dict):
                side[k] = [r for r in after if all(cond(r.get(c), v) for c, v in {**(les.get("scope") or {}), **spec}.items())]
        flags = []
        if st not in STATUS: flags.append("no status" if not st else "status %r?" % st)
        if st == "settled" and not (les.get("settled") or {}).get("words"): flags.append("settled with no words of the reviewer's")
        if who not in WHOSE: flags.append("whose?")
        elif who == "claude": flags.append("Claude's words")
        if not behind: flags.append("no verdicts behind it")
        elif len(rs) == 1: flags.append("one round")
        if rows and not any((r.get("note") or "").strip() for r in rows): flags.append("reasons only")
        if gone: flags.append("not in the log: %s" % " ".join(sorted(set(gone))))
        bystatus.setdefault(st or "no status", []).append(i)
        print(row % (i[:26], st or "?", who or "?", pkd(rows) if rows else "-", span,
              (pkd(side["followed"]) + " (%d)" % len(side["followed"])) if "followed" in side else "-",
              (pkd(side["broke"]) + " (%d)" % len(side["broke"])) if "broke" in side else "-", "; ".join(flags)))
    print("\nWhere they live now:")
    for les in led.get("lessons", []):
        s = les.get("settled") or {}
        print("  %s: %s%s" % (les.get("id"), ", ".join(where_now(les.get("where"))) or "(no place given)",
                              ' | settled %s: "%s"' % (s.get("date", "?"), (s["words"][:110] + "...") if len(s["words"]) > 110 else s["words"]) if s.get("words") else ""))
    print("\n%s. Left out of the filters, every edit an effect: %s. + = counts rounds judged before the review page (%s): perfect wasn't"
          " always a choice there, so compare their deletes, not their perfects."
          % ("; ".join("%s %d" % (k, len(v)) for k, v in sorted(bystatus.items())), " ".join(sorted(fx, key=round_order)) or "none",
             " ".join(sorted(early, key=round_order)) or "none"))
    return 0


if __name__ == "__main__": sys.exit(main())
