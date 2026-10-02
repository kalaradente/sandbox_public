#!/usr/bin/env python3
"""Narrow the search: the one-liners a plan needs, instead of every line in the library. Reading a whole library of well over a thousand lines before every
round costs more with each pull, so the plan comes first and only what it asks for is read.
  python3 _scripts/narrow.py [--format 9:16|16:9] [--account <id>] [--pins | --pins-only] [--top 40] [--beats 8] [--thin 12] [--fresh] "<search>" ["<search>" ...]
One search per piece or idea of the plan, written "name: part; part" (the name may be left out; a search with no part is refused):
  tag night,couple          concept tags, all of them (_scripts/concept_tags.json): filters everything in that search
  words rain|storm|wet      a pattern looked for in the one-liners
  look a couple dancing in the rain under a streetlight      by picture (visual_search.py's index): the --top closest
  beat a girl standing up through a sunroof at night         one beat of the piece, by picture: the --beats closest, whatever their tags
A search finds what its words match plus what its look finds, inside its tags, plus its beats; with tags alone, everything carrying them.
Film and TV: the film's name in words is enough (back-tested on two rounds: every source found). Real footage and
collages get the wider search: a tag only
when every shot of the piece must carry it, and a beat for each beat of the story (one search per piece with a tag on it
missed one source in six of what those rounds used: a girl alone in a couple piece, a shot the words didn't name).
  python3 _scripts/narrow.py --format 9:16 "rain kiss: tag couple; words rain|storm; look a couple kissing in the rain at night" \\
                                           "drive home: tag couple; words car|drive|sunroof; look a couple in a car at night; beat a girl standing up through a sunroof at night; beat a girl in the headlights of a stopped car"
Prints each search with how many it found (!! thin: under --thin, default 12: pull more for that piece, in the background,
before planning it), then every line found, ONCE, with the searches that found it and how often it has been in an edit
(last 2 rounds / all rounds: sandbox_paths.use_counts, the count library_view.py and shortlist_check.py show): read every
one of these lines, shortlist 2-3x what each piece needs, then shortlist_check.py --moments on the shortlist. The whole
list is still there when a search can't say what is wanted (library_view.py --short).
What the thin mark means: it counts lines, so it speaks for tags and words. It cannot judge a look or a beat: a look always
brings its --top closest and a beat its --beats closest, however far off they are. For those the closeness is printed
beside the count, "by picture: look 0.357/0.324": the picture score of its closest match and of its 12th closest (--thin;
a beat: its last). No floor on that number tells "it is there" from "nothing like it" (measured on the visual index, Oct 2:
at the 12th closest, subjects a dozen one-liners name scored 0.25 to 0.32 and subjects no line names 0.21 to 0.28), so it
doesn't set the mark: over about 0.29 the thing was always there, under about 0.25 seldom; in between, the lines say. When
what a look or a beat found doesn't show what was asked for, pull more for it, mark or no mark.
--format: clips of that orientation (and square-ish) only; pins are not filtered. --account: only what is tagged for it,
without what was posted on it (a clip in a video posted on an account is retired there only, rules §11: named under the
searches; for the other accounts its line starts [POSTED <id>]).
--pins: pins as well as clips (collages); --pins-only: pins alone. Usable clips and pins only (use yes/careful, reviewed);
[PERSONAL] and [PHONE] lines are marked as in library_view.py and stay out unless asked for.
--fresh: never-used first, then the least used. Only for a swap: when the person says swap
something out, the replacement is looked for among what has never been in an edit first."""
import collections, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, accounts, posted_on, use_counts  # noqa: E402

a = sys.argv[1:]
if not a or a[0] in ("-h", "--help"): sys.exit(__doc__)
def opt(k, d=None):
    if k in a:
        i = a.index(k)
        if i + 1 >= len(a): sys.exit("%s needs a value after it (--help has the usage)" % k)
        v = a[i + 1]; del a[i:i + 2]; return v
    return d
def num(k, d):
    v = opt(k, d)
    try: return int(v)
    except ValueError: sys.exit("%s takes a whole number, not \"%s\"" % (k, v))
def flag(k):
    if k in a: a.remove(k); return True
    return False
FMT = opt("--format"); ACCT = (opt("--account") or "").upper(); TOP = num("--top", 40); BEATS = num("--beats", 8); THIN = num("--thin", 12)
PINS_ONLY = flag("--pins-only"); WITH_PINS = flag("--pins") or PINS_ONLY; FRESH = flag("--fresh")
if FMT not in (None, "9:16", "16:9") or not a: sys.exit(__doc__)
WANT = {"9:16": "vertical", "16:9": "horizontal"}.get(FMT)

load = lambda rel: json.load(open(os.path.join(LIB, rel)))["clips"] if os.path.exists(os.path.join(LIB, rel)) else {}
CLIPS = {**load("_lab/lab_index.json"), **load("_reference/clip_index.json")}
_p = os.path.join(LIB, "_stills/pinterest/index.json")
PINS = json.load(open(_p)) if os.path.exists(_p) else {}
PINS = PINS.get("pins", PINS)

# the pool: usable and reviewed, the same lines library_view.py lists; pins keyed "pin:<file>" as in the visual index
POOL = {}
for k, v in ([] if PINS_ONLY else CLIPS.items()):
    if v.get("needs_review") or v.get("use") not in ("yes", "careful"): continue
    if WANT and v.get("orientation") not in (WANT, "square-ish"): continue
    POOL[k] = v
for k, v in (PINS.items() if WITH_PINS else []):
    if isinstance(v, dict) and not v.get("needs_review") and v.get("use") in ("yes", "careful"): POOL["pin:" + k] = v
EVERY = list(CLIPS.values()) + [v for v in PINS.values() if isinstance(v, dict)]; POSTED = []
if ACCT:
    if ACCT not in {str(x.get("id")).upper() for x in accounts()} | {str(x).upper() for v in EVERY for x in (v.get("fits") or [])}:
        sys.exit("--account %s: no such account (the accounts: %s)" % (ACCT, ", ".join(str(x.get("id")) for x in accounts()) or "none in library/profile.json"))
    POOL = {k: v for k, v in POOL.items() if ACCT in [str(x).upper() for x in (v.get("fits") or [])]}
    POSTED = sorted(k for k, v in POOL.items() if ACCT in posted_on(v)); POOL = {k: v for k, v in POOL.items() if k not in POSTED}   # posted on this account: retired there (rules §11)
tags_of = lambda v: {str(t).lower() for t in (v.get("tags") or [])}
_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "concept_tags.json")   # a tag is known when the vocabulary has it or anything in the library carries it
TAGS = {t for g, d in (json.load(open(_p)) if os.path.exists(_p) else {}).items() if not g.startswith("_") for t in d} | {t for v in EVERY for t in tags_of(v)}
def line_of(v):
    l = v.get("line") or ((v.get("description") or "?") + " — [vibe not written yet]")
    return ("[PERSONAL] " if "personal" in (v.get("flags") or []) else "[PHONE] " if "phone_look" in (v.get("flags") or []) else "") + "".join("[POSTED %s] " % x for x in posted_on(v)) + l

# how often each has been in an edit: the one count (sandbox_paths.use_counts), keyed like the pool
rounds, per = use_counts(CLIPS, PINS)
used_all, used_recent = sum(per.values(), collections.Counter()), sum((per[r] for r in rounds[-2:]), collections.Counter())
uses = lambda k: (used_recent[k], used_all[k])

_scores = {}
def by_picture(q):
    """{key: score} of every clip and pin in the visual index for these words (its best frame)."""
    if q not in _scores:
        import numpy as np, visual_search as vs
        if "store" not in _scores:
            _scores["store"] = vs.read_store()
            if not _scores["store"]["keys"]: sys.exit("no visual index yet: python3 _scripts/visual_search.py --build")
        s = _scores["store"]; sc = s["emb"].astype(np.float32) @ vs.embed_text(q); best = {}
        for k, v in zip(s["keys"], sc):
            if k not in best or v > best[k]: best[k] = float(v)
        _scores[q] = best
    return _scores[q]

KINDS = ("tag", "tags", "words", "look", "beat")
found, heads, unseen = collections.OrderedDict(), [], set()
for n, spec in enumerate(a, 1):
    name, _, body = spec.partition(":"); kind_first = lambda t: t.strip().split(" ")[0].lower() in KINDS
    if not _ or ";" in name or (kind_first(name) and not kind_first(body)): name, body = "", spec   # no name given ("look a couple at 3:00 am"); "beat drop: tag night" is a name
    parts = collections.defaultdict(list); near = []
    for part in body.split(";"):
        kind, _, val = part.strip().partition(" ")
        if not part.strip(): continue
        if kind.lower() not in KINDS or not val.strip():
            sys.exit("search %d: \"%s\" is not a part (tag a,b · words <pattern> · look <what it should look like> · beat <one beat, by picture>)" % (n, part.strip()))
        parts["tag" if kind.lower() == "tags" else kind.lower()].append(val.strip())
    if not parts: sys.exit("search %d: \"%s\" has nothing to look for (tag a,b · words <pattern> · look <what it should look like> · beat <one beat, by picture>)" % (n, spec))
    tags = {t.strip().lower() for v in parts["tag"] for t in v.split(",") if t.strip()}
    if tags - TAGS: sys.exit("search %d: no such tag: %s (the tags: _scripts/concept_tags.json, or python3 _scripts/concept_tags.py --list)" % (n, ", ".join(sorted(tags - TAGS))))
    inside = {k: v for k, v in POOL.items() if tags <= tags_of(v)}
    hit = set()
    for w in parts["words"]:
        try: rx = re.compile(w, re.I)
        except re.error as e: sys.exit("search %d: \"%s\" is not a pattern (%s)" % (n, w, e))
        hit |= {k for k, v in inside.items() if rx.search(v.get("line") or "")}
    def closest(q, among, most, kind):   # the `most` closest by picture, and how close: the closest and the --thin-th (or the last taken)
        sc = by_picture(q); unseen.update(k for k in among if k not in sc); top = sorted((k for k in among if k in sc), key=lambda k: -sc[k])[:most]
        if top: near.append("%s %.3f/%.3f" % (kind, sc[top[0]], sc[top[:max(THIN, 1)][-1]]))
        return set(top)
    for q in parts["look"]: hit |= closest(q, inside, TOP, "look")
    if not parts["words"] and not parts["look"] and (tags or not parts["beat"]): hit = set(inside)
    for q in parts["beat"]: hit |= closest(q, POOL, BEATS, "beat")   # a beat is one shot of the story: found by picture alone, whatever the clip is tagged
    for k in hit: found.setdefault(k, []).append(n)
    heads.append("%s %2d  %-28s %3d found%s%s%s" % ("!!" if len(hit) < THIN else "  ", n, (name.strip() or body.strip())[:28], len(hit),
                 "  (%d carry its tags)" % len(inside) if tags else "", "   by picture: " + ", ".join(near) if near else "", "   thin: pull more for it" if len(hit) < THIN else ""))

print("\n".join(heads) + "\n")
if POSTED: print("%d left out of these searches: posted on %s, so retired there (rules §11): %s\n" % (len(POSTED), ACCT, " ".join(k.lstrip(".") for k in POSTED)))
order = sorted(found, key=(lambda k: (uses(k)[1], k.startswith("pin:"), k.lstrip("."))) if FRESH else (lambda k: (k.startswith("pin:"), k.lstrip("."))))
for k in order:
    r, al = uses(k)
    print("%s | %s | used %d/%d | %s" % (k.lstrip("."), ",".join(map(str, found[k])), r, al, line_of(POOL[k])[:200]))
whole = sum(1 for v in CLIPS.values() if not v.get("needs_review") and v.get("use") in ("yes", "careful")) + \
        sum(1 for v in PINS.values() if isinstance(v, dict) and not v.get("needs_review") and v.get("use") in ("yes", "careful"))
print("\n%d lines to read, of %d in the library (clips and pins)%s. Columns: key | the searches that found it | used in the last 2 rounds / ever | one-liner"
      % (len(found), whole, "; never used first" if FRESH else ""))
if unseen: print("%d in these searches are not in the visual index yet (found by tags and words only): python3 _scripts/visual_search.py --build" % len(unseen))
todo = [k for k, v in CLIPS.items() if v.get("needs_review")] + ["pin:" + k for k, v in PINS.items() if isinstance(v, dict) and v.get("needs_review")]
if todo: print("NOT REVIEWED YET (%d), so not searched: fill their hand fields from their sheets first:\n  %s" % (len(todo), " ".join(todo[:40])))
