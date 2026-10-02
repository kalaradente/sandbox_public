#!/usr/bin/env python3
"""Sealed forecasts and the scoreboard: is taste getting better, round by round. Nothing else predicts a verdict, so nothing else can say whether the picks are improving.
  python3 _scripts/forecast.py check <round>          before rendering: every recipe has a forecast, and it adds up
  python3 _scripts/forecast.py seal <round>           before the review: each forecast fixed with its time and a checksum
  python3 _scripts/forecast.py score <round> [--csv <file>] [--base <round>,<round>...]      after the review
<round>: a round folder in library/_lab/rounds/ (its full name, its number like R08, or a path), wherever it sits there: straight
in it, or in the folders rounds are sorted into (layout.json "round_folders"; a round whose pieces sit in several of them is
read as one round, and each piece's seal is kept in its own folder).
Each recipe carries its forecast, written while planning and never shown to the reviewer (the review pages show only the
caption, story and why):
  "forecast": {"perfect": 0.2, "kept": 0.5, "deleted": 0.3, "note": "<the note the reviewer is most likely to write>",
               "origin": "note" | "kept" | "fresh", "from": "<the note or edit the idea came from; empty when fresh>"}
  origin: note = built on something the reviewer said; kept = re-made from an edit they kept or made perfect;
          fresh = Claude's own idea.
check  names every recipe whose forecast is missing or broken (the three chances add up to 1; origin is one of the three;
       "from" unless fresh) and exits 1. It also says when an account has no fresh long shot (a fresh idea forecast at LOW
       or under): ideas built on the reviewer's notes go perfect far more often than fresh ones, so a round of safe bets
       scores well and says nothing about taste. The fresh-idea hit rate is the number that does.
seal   writes <round>/forecast_seal.json: per edit its forecast, the time, a checksum of the forecast and one of the rest of
       the recipe. An edit's first seal never moves (sealing again only adds new edits); a forecast changed later shows up
       as changed; an edit already judged isn't sealed.
score  against <round>/review.json: the Brier score (the three chances against what came: 0 = foresaw it, 2 = sure and wrong)
       next to the base rate's (each account's verdict shares in the logged rounds before this one that were judged on the
       review page, leaving out rounds where every edit carries an effect: there "deleted" meant "try again"; --base names the
       rounds instead); how often the most likely verdict came; expected against actual counts; each likely note beside the
       note that came (judged by hand, not here); where each idea came from, and the fresh-idea hit rate per account.
       Unsealed forecasts and edits re-cut between forecast and verdict are named, and the scores are given again without
       them. --csv scores forecasts held outside the recipes (columns id, p_perfect, p_kept, p_deleted, likely_note; origin
       and from when tagged); the file's own time stands in for the seal.
       Then it adds the round's line to library/_lab/forecast_scoreboard.csv (scoring a round again replaces its line).
"noted" counts as kept everywhere, as in the review page's log."""
import csv, datetime, hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, find_account, merge_own, round_dirs, round_order, round_parts  # noqa: E402  (--help stops there, before anything runs)

LAB = os.path.join(LIB, "_lab"); ROUNDS = os.path.join(LAB, "rounds"); LOG = os.path.join(LAB, "lab_log.csv")
BOARD = os.path.join(LAB, "forecast_scoreboard.csv")
SEAL = "forecast_seal.json"   # not R*.json: every tool that reads a round folder takes only R*.json as recipes
CLS = ("perfect", "kept", "deleted"); ORIGINS = ("note", "kept", "fresh")
LOW = 0.2      # a fresh idea at this chance of perfect or under is a long shot (the accounts' perfect rates have run about 0.1-0.3)
TOL = 0.005    # the chances add up to 1 within this: 0.333 three times passes, 0.33 three times doesn't
EFFECTS = {"fx", "grade", "grain", "blend", "blend_style", "layers", "sparkles", "doodles", "pixels"}   # a recipe's effect keys: the effects line of CARRIED in Resolve_Export.py
SHOT_EFFECTS = {"flash", "punch", "blend", "reverse"}
NOTES = {"forecast", "story", "why", "sources", "sharpness", "notes", "tags", "title"}   # not picture or sound (CARRIED's notes line): changing them isn't a re-cut
SHOT_NOTES = {"what", "why", "note", "scene", "snare", "looked", "why_in"}   # the same on a shot (CARRIED's shot notes: Resolve_Export.SHOT_NOTES)
NOTES_BEFORE = {"forecast", "story", "why", "sources", "sharpness", "notes", "tags"}   # the notes as seals took them until Oct 2 (recut reads those seals with it: never add to it)
BOARD_COLS = ["round", "scored_at", "forecasts", "judged", "brier", "base_brier", "clean", "clean_brier", "clean_base_brier",
              "most_likely_right", "expected_pkd", "actual_pkd", "fresh_perfect", "base_rounds"]


def round_dir(a):
    """The round's folders (one, or several when its pieces are sorted between folders): every function below takes this.
    Looked up where everything else looks a round up (sandbox_paths.round_parts). A bare name or number is the round, whatever
    folder of that name sits in the working directory; a path to a folder that isn't one of the round's own is a copy."""
    ds = round_parts(a); n = os.path.basename(os.path.normpath(a))
    if os.path.isdir(a) and not (ds and a == n) and os.path.realpath(a) not in [os.path.realpath(p) for p in ds]: return [os.path.abspath(a)]   # a copy somewhere else: read by itself
    if ds: return ds
    hit = sorted(d for d in round_dirs() if d.split("_")[0] == n)
    sys.exit("No round %r in %s%s" % (a, ROUNDS, " (it matches %s)" % ", ".join(hit) if hit else ""))


def parts(rd): return [rd] if isinstance(rd, str) else list(rd or [])   # a round: one folder, or the folders its pieces are sorted between


def short(rd): return os.path.basename(os.path.normpath(parts(rd)[0])).split("_")[0]


def recipes(rd):
    """{id: (recipe, path)} for the round's recipes (R*.json with an id and a kind; manifest and review aren't recipes)."""
    out = {}
    for d, f in sorted(((d, f) for d in parts(rd) if os.path.isdir(d) for f in os.listdir(d)), key=lambda x: x[1]):
        if not (f.startswith("R") and f.endswith(".json")): continue
        try: r = json.load(open(os.path.join(d, f), encoding="utf-8"))
        except (OSError, ValueError): continue
        if isinstance(r, dict) and "id" in r and "kind" in r: out[r["id"]] = (r, os.path.join(d, f))
    return out


def load(p, default):
    try: return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError): return default


def load_review(rd): return merge_own(parts(rd), {d: load(os.path.join(d, "review.json"), {}) for d in parts(rd)})   # a split round: each piece's own folder wins
def load_seal(rd): return merge_own(parts(rd), {d: load(os.path.join(d, SEAL), {}).get("edits", {}) for d in parts(rd)})
def read_log(): return list(csv.DictReader(open(LOG, encoding="utf-8"))) if os.path.exists(LOG) else []
def verdict(d): return {"noted": "kept"}.get(d, d)
def digest(x): return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
def now(): return datetime.datetime.now().isoformat(timespec="seconds")   # local time, like review.json's "at"
def mtime(p): return datetime.datetime.fromtimestamp(os.path.getmtime(p))


def body(r):
    """The edit itself: a change here is a re-cut. Notes aren't in it, the recipe's or a shot's: the title is the suggested
    caption, so changing
    it changes nothing that was forecast."""
    return {k: [{j: w for j, w in s.items() if j not in SHOT_NOTES} if isinstance(s, dict) else s for s in v] if k == "shots" and isinstance(v, list) else v
            for k, v in r.items() if k not in NOTES}


def recut(r, s):
    """Whether recipe r was re-cut after its seal s. A seal made before the title and the shots' notes were left out of the
    edit (Oct 2) took its checksum with them in: it is read that way too (NOTES_BEFORE), so it verifies exactly as it did.
    For those seals a changed title or shot note still reads as a re-cut (what they said then isn't in the seal); seals made
    since don't mind one."""
    return s.get("recipe_sha256") not in (digest(body(r)), digest({k: v for k, v in r.items() if k not in NOTES_BEFORE}))


def when(s):
    """An ISO time as local naive time (review.json's is local; a phone's may end in Z), or None."""
    try: t = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError: return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def label(acc):
    a = find_account(acc)
    return "%s (%s)" % (acc, a["name"]) if a and a.get("name") and str(a["name"]) != str(acc) else str(acc)


def chances(fc):
    """The three chances as floats, or None when they aren't three numbers from 0 to 1 adding up to 1."""
    try: p = [float(fc[k]) for k in CLS]
    except (KeyError, TypeError, ValueError): return None
    if any(isinstance(fc[k], bool) for k in CLS) or any(not 0 <= x <= 1 for x in p) or abs(sum(p) - 1) > TOL: return None
    return p


def problems(fc):
    if not isinstance(fc, dict): return ["no forecast"]
    bad = []
    if chances(fc) is None:
        try: bad.append("the chances add up to %.3g, not 1" % sum(float(fc[k]) for k in CLS))
        except (KeyError, TypeError, ValueError): bad.append("perfect, kept and deleted must each be a chance from 0 to 1")
    o = fc.get("origin"); frm = str(fc.get("from") or "").strip()
    if o not in ORIGINS: bad.append("origin %r isn't note, kept or fresh" % (o,))
    elif o != "fresh" and not frm: bad.append('no "from": say which note or edit this %s idea came from' % o)
    elif o == "fresh" and frm: bad.append('fresh, but "from" names %r: that makes it note or kept' % frm[:60])
    if not isinstance(fc.get("note", ""), str): bad.append("note must be text")
    return bad


def check(rd):
    rs = recipes(rd)
    if not rs: sys.exit("No recipes in %s" % rd)
    bad = 0; acc = {}
    for i, (r, _) in rs.items():
        fc = r.get("forecast"); pb = problems(fc)
        a = acc.setdefault(str(r.get("account") or "?"), dict(n=0, fresh=0, low=0, exp=[0.0, 0.0, 0.0]))
        a["n"] += 1
        if pb: bad += 1; print("!! %s: %s" % (i, "; ".join(pb))); continue
        a["exp"] = [e + float(fc[k]) for e, k in zip(a["exp"], CLS)]
        if fc["origin"] == "fresh": a["fresh"] += 1; a["low"] += float(fc["perfect"]) <= LOW
    for k, a in sorted(acc.items()):
        print("%s: %d edits; expected %.1f perfect, %.1f kept, %.1f deleted; %d fresh, %d of them long shots (perfect at %g or under)"
              % (label(k), a["n"], a["exp"][0], a["exp"][1], a["exp"][2], a["fresh"], a["low"], LOW))
        if not a["low"]: print("   no fresh long shot for %s: a round of safe bets says nothing about taste. Add a fresh idea you don't expect to go perfect." % k)
    print("%s: %d of %d forecasts ready%s" % (short(rd), len(rs) - bad, len(rs), "" if bad else "; seal them before the review: forecast.py seal " + short(rd)))
    return 1 if bad else 0


def seal(rd):
    rs = recipes(rd); sealed = load_seal(rd); rv = load_review(rd); new = old = bad = 0
    for i, (r, _) in rs.items():
        fc = r.get("forecast"); s = sealed.get(i)
        if s:
            old += 1
            if fc is not None and digest(fc) != s.get("sha256"): print("!! %s: its forecast changed after its seal (%s); the seal keeps the first" % (i, s.get("at")))
            if recut(r, s): print("   %s: re-cut after its seal (%s); the score says whether that was before its verdict" % (i, s.get("at")))
            continue
        if (rv.get(i) or {}).get("decision"):
            bad += 1; print("!! %s: already judged (%s): a forecast sealed now isn't a forecast; not sealed" % (i, rv[i]["decision"])); continue
        pb = problems(fc)
        if pb: bad += 1; print("!! %s: not sealed: %s" % (i, "; ".join(pb))); continue
        sealed[i] = dict(at=now(), forecast=fc, sha256=digest(fc), recipe_sha256=digest(body(r))); new += 1
    for d in parts(rd) if new else []:   # each folder's seal file holds its own pieces' seals
        mine = dict(load(os.path.join(d, SEAL), {}).get("edits", {}), **{i: s for i, s in sealed.items() if i in rs and os.path.dirname(rs[i][1]) == d})
        if not mine: continue
        p = os.path.join(d, SEAL); tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(dict(round=short(rd), edits=dict(sorted(mine.items()))), f, indent=1, ensure_ascii=False)
        os.replace(tmp, p)
    print("%s: %d sealed now, %d sealed before, %d not sealed (%s)" % (short(rd), new, old, bad, os.path.join(short(rd), SEAL)))
    return 1 if bad else 0


def effects_round(rd):
    """Every edit carries an effect: an effects experiment (R06.6-R06.9.8 were), where "deleted" meant "try again"."""
    rs = [r for r, _ in recipes(rd).values()]
    return bool(rs) and all(EFFECTS & set(r) or any(SHOT_EFFECTS & set(s) for s in r.get("shots") or []) for r in rs)


def on_page(rd):
    """Judged on the review page (it writes review.json). Before it, "perfect" wasn't always a choice (early perfects were
    upgraded later, by hand) and "noted" wasn't a verdict, so those rounds' shares don't compare."""
    return any(os.path.exists(os.path.join(d, "review.json")) for d in parts(rd))


def folders(): return {short(d): ps for d, ps in round_dirs().items()}


def base_rates(rnd, log, names=None):
    """Each account's verdict shares in the logged rounds before rnd that compare (or the rounds named). Returns (rates, used,
    left out {why: rounds}, counts); rates[None] pools every account, for an account with no history of its own."""
    fd = folders(); logged = sorted({r["round"] for r in log}, key=round_order); out = {}
    if names: use = [x for x in logged if x in names]
    else:
        before = [x for x in logged if round_order(x)[0] < round_order(rnd)[0]]
        out = {"before the review page": [x for x in before if not on_page(fd.get(x))],
               "every edit an effect": [x for x in before if on_page(fd.get(x)) and effects_round(fd.get(x))]}
        use = [x for x in before if x not in out["before the review page"] + out["every edit an effect"]]
    counts = {}
    for r in log:
        y = verdict(r["decision"])
        if r["round"] in use and y in CLS:
            for k in (r["account"], None): counts.setdefault(k, [0, 0, 0])[CLS.index(y)] += 1
    return {k: [c / sum(v) for c in v] for k, v in counts.items() if sum(v)}, use, out, counts


def brier(p, y): return sum((p[k] - (CLS[k] == y)) ** 2 for k in range(3))


def top_right(p, y):
    t = [CLS[k] for k in range(3) if p[k] == max(p)]
    return (y in t) / len(t)   # a tie between two verdicts counts half when either came


def summary(rows):
    n = len(rows)
    if not n: return None
    exp = [sum(r["p"][k] for r in rows) for k in range(3)]; act = [sum(r["y"] == c for r in rows) for c in CLS]
    return dict(n=n, brier=sum(brier(r["p"], r["y"]) for r in rows) / n, base=sum(brier(r["b"], r["y"]) for r in rows) / n,
                right=sum(top_right(r["p"], r["y"]) for r in rows), exp=exp, act=act)


def fmt(p): return "/".join("%.2f" % x for x in p)


def score(rd, csv_path=None, names=None):
    rv = load_review(rd); rnd = short(rd)
    if not rv: sys.exit("%s has no review.json yet: score it after the review" % rnd)
    rs = recipes(rd); log = read_log(); fcs = {}   # id: (forecast, [(flag, spoils the forecast)])
    if csv_path:
        t_csv = mtime(csv_path); src = os.path.basename(csv_path)
        for row in csv.DictReader(open(csv_path, encoding="utf-8")):
            i = (row.get("id") or "").strip()
            if not i: continue
            fc = {k: row.get("p_" + k, row.get(k)) for k in CLS}
            fc.update(note=row.get("likely_note", row.get("note")) or "", origin=(row.get("origin") or "").strip() or None, **{"from": row.get("from") or ""})
            flags = []; vt = when((rv.get(i) or {}).get("at")); rp = (rs.get(i) or (None, None))[1]
            if vt and vt < t_csv: flags.append(("judged before the forecast file last changed", True))
            elif rp and vt and t_csv < mtime(rp) < vt: flags.append(("re-cut after the forecast, before its verdict (file times): the forecast was for another cut", True))
            fcs[i] = (fc, flags)
        first = min((when(v.get("at")) for v in rv.values() if when(v.get("at"))), default=None)
        print("%s: forecasts from %s (last changed %s; first verdict %s)" % (rnd, src, t_csv.isoformat(timespec="seconds"), first.isoformat(timespec="seconds") if first else "?"))
    else:
        src = "recipes"; sealed = load_seal(rd)
        for i, (r, rp) in rs.items():
            s = sealed.get(i); cur = r.get("forecast"); flags = []
            if not s and cur is None: continue
            vt = when((rv.get(i) or {}).get("at"))
            if not s: fc = cur; flags.append(("not sealed", True))
            else:
                fc = s.get("forecast")
                if digest(fc) != s.get("sha256"): flags.append(("the seal file's own entry was edited", True))
                if cur is not None and digest(cur) != s.get("sha256"): flags.append(("forecast changed after its seal (the sealed one is scored)", False))
                st = when(s.get("at"))
                if vt and st and st > vt: flags.append(("sealed after its verdict", True))
                if recut(r, s):
                    if not vt or mtime(rp) < vt: flags.append(("re-cut after its seal, before its verdict: the forecast was for another cut", True))
                    else: flags.append(("re-cut after its verdict", False))
            fcs[i] = (fc, flags)
        print("%s: forecasts from the recipes (%d sealed)" % (rnd, len(sealed)))
    rates, used, out, counts = base_rates(rnd, log, names)
    acct = {r["id"]: r["account"] for r in log}
    rows = []; missing = []; broken = []
    for i in sorted(rv):
        y = verdict((rv.get(i) or {}).get("decision"))
        if y not in CLS: continue   # not judged yet
        if i not in fcs: missing.append(i); continue
        fc, flags = fcs[i]; p = chances(fc) if isinstance(fc, dict) else None
        if p is None: broken.append(i); continue
        a = str((rs.get(i) or ({},))[0].get("account") or acct.get(i) or "?")
        b = rates.get(a) or rates.get(None) or [1 / 3.0] * 3
        rows.append(dict(id=i, acc=a, y=y, p=p, b=b, fc=fc, flags=flags, note=(rv[i].get("note") or "").strip()))
    if not rows: sys.exit("Nothing to score in %s: no judged edit has a forecast" % rnd)
    print("base rate (%s): %s" % ("the rounds named" if names else "the rounds before %s that compare" % rnd, " ".join(used) or "none"))
    for why, xs in out.items():
        if xs: print("   left out, %s: %s" % (why, " ".join(xs)))
    for k in sorted({r["acc"] for r in rows}):
        c = counts.get(k) or counts.get(None)
        print("   %s: %s perfect/kept/deleted from %d verdicts%s" % (label(k), fmt(rates.get(k) or rates.get(None) or [1 / 3.0] * 3), sum(c) if c else 0,
                                                                 "" if counts.get(k) else " (no history of its own: all accounts pooled)" if c else " (no history: a third each)"))
    print("\n%-14s %-8s %-15s %-9s %6s %6s" % ("edit", "came", "forecast p/k/d", "likeliest", "Brier", "base"))
    for r in rows:
        t = [CLS[k] for k in range(3) if r["p"][k] == max(r["p"])]
        print("%-14s %-8s %-15s %-9s %6.3f %6.3f%s" % (r["id"], r["y"], fmt(r["p"]), "/".join(t), brier(r["p"], r["y"]), brier(r["b"], r["y"]),
                                                    ("  <- " + "; ".join(f for f, _ in r["flags"])) if r["flags"] else ""))
    clean = [r for r in rows if not any(sp for _, sp in r["flags"])]
    S = summary(rows); C = summary(clean) if len(clean) < len(rows) else S
    for name, s in (("all %d" % S["n"], S),) + ((("without the %d flagged" % (S["n"] - C["n"]), C),) if C and C is not S else ()):
        print("%s: Brier %.3f against the base rate's %.3f (lower is better); the likeliest verdict came %s of %d; expected %.1f perfect, %.1f kept, %.1f deleted; came %d, %d, %d"
              % (name, s["brier"], s["base"], ("%g" % s["right"]), s["n"], s["exp"][0], s["exp"][1], s["exp"][2], *s["act"]))
    if missing: print("judged with no forecast: %s" % " ".join(missing))
    if broken: print("forecast unusable (the chances don't add up): %s" % " ".join(broken))
    print("\nThe likely note beside the note that came (whether it named what the note was about is judged by hand):")
    for r in rows:
        ln = str(r["fc"].get("note") or "").strip()
        print("%s  %s\n   likely: %s\n   came:   %s" % (r["id"], r["y"], ln or "(no note)", r["note"] or "(no note)"))
    tagged = [r for r in rows if r["fc"].get("origin") in ORIGINS]
    fresh = {}
    if tagged:
        print("\nWhere the ideas came from (%d of %d tagged): perfect/kept/deleted" % (len(tagged), len(rows)))
        for k in sorted({r["acc"] for r in tagged}):
            parts = []
            for o in ORIGINS:
                g = [r for r in tagged if r["acc"] == k and r["fc"]["origin"] == o]
                parts.append("%s %d: %s" % (o, len(g), "/".join(str(sum(r["y"] == c for r in g)) for c in CLS)))
                if o == "fresh": fresh[k] = (sum(r["y"] == "perfect" for r in g), len(g), sum(r["y"] == "deleted" for r in g))
            print("   %s: %s" % (label(k), " | ".join(parts)))
        print("fresh-idea hit rate (fresh ideas that went perfect): %s" % "; ".join("%s %d/%d (%d deleted)" % (k, *v) for k, v in sorted(fresh.items())))
    else: print("\nfresh-idea hit rate: the forecasts carry no origin, so it can't be told")
    line = dict(round=rnd, scored_at=now(), forecasts=src, judged=S["n"], brier="%.3f" % S["brier"], base_brier="%.3f" % S["base"],
                clean=C["n"] if C else 0, clean_brier="%.3f" % C["brier"] if C else "", clean_base_brier="%.3f" % C["base"] if C else "",
                most_likely_right="%g/%d" % (S["right"], S["n"]), expected_pkd="%.1f/%.1f/%.1f" % tuple(S["exp"]), actual_pkd="%d/%d/%d" % tuple(S["act"]),
                fresh_perfect="; ".join("%s %d/%d" % (k, v[0], v[1]) for k, v in sorted(fresh.items())) if fresh else "not tagged",
                base_rounds=" ".join(used))
    os.makedirs(LAB, exist_ok=True)
    board = [r for r in (list(csv.DictReader(open(BOARD, encoding="utf-8"))) if os.path.exists(BOARD) else []) if r.get("round") != rnd] + [line]
    board.sort(key=lambda r: round_order(r["round"]))
    tmp = BOARD + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BOARD_COLS, extrasaction="ignore"); w.writeheader(); w.writerows(board)
    os.replace(tmp, BOARD)
    print("\nscoreboard: %s (%s's line %s)" % (os.path.relpath(BOARD, LIB), rnd, "written"))
    return 0


def main():
    a = sys.argv[1:]
    opt = lambda k: a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else None
    if len(a) < 2 or a[0] not in ("check", "seal", "score"): sys.exit(__doc__)
    rd = round_dir(a[1])
    if a[0] == "check": return check(rd)
    if a[0] == "seal": return seal(rd)
    cp, base = opt("--csv"), opt("--base")
    if "--csv" in a and not (cp and os.path.isfile(cp)): sys.exit("--csv needs a forecasts file: %r isn't one" % cp)
    return score(rd, cp, [x.strip() for x in base.split(",") if x.strip()] if base else None)


if __name__ == "__main__": sys.exit(main())
