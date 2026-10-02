#!/usr/bin/env python3
"""The whole library, one line per clip: the fastest way to plan from ALL of it. To make content, the plan comes first and narrow.py searches this list for what each piece needs: the whole
of it is read only when a search can't say what is wanted. Then shortlist and look at the shortlist's contact sheets; never open videos to plan.
  python3 _scripts/library_view.py [--account <id>] [--all] [--type film|real|graphic] [--orient vertical|horizontal] [--grep word]
                                   [--tag night,couple] [--short]
--tag: only clips/pins carrying every one of these concept tags (_scripts/concept_tags.json; concept_tags.py fills them).
--short: key and one-liner only (about 60% of the text: the way to read the whole library; shortlist_check.py adds the rest for the shortlist).
--account: only what suits that account ("fits" in the index: the accounts a clip or pin suits, judged from its one-liner
against each account's "what" in library/profile.json; one pool, tagged, never sorted into per-account folders).
Default: every usable clip (use yes/careful) in the main library and the lab, then the Pinterest stills. Columns: key · where · use · tier · type ·
orientation · seconds · used (edits in the last 2 rounds / all rounds: sandbox_paths.use_counts, the same count narrow.py and
shortlist_check.py show; pins have it too) · fits (accounts) · the clip's one-liner (`line` in the index:
what you see — the vibe). Clips still marked needs_review are listed at the end: review them before planning.
A line that starts [PERSONAL] is a clip flagged `personal` (someone filming herself for her own followers): it stays out of
edits unless it was asked for. [PHONE] is a clip flagged `phone_look` (it looks shot on a modern phone in ordinary light:
every clip has to feel cinematic, rules §8): it stays out unless there is a reason.
A line that starts [POSTED <id>] was in a video (a pin: a collage) posted on that account, and is retired there only (rules
§11; "pastured_<id>" in the index): --account <that id> leaves it out and names it at the end; the other accounts still use it.
The pathway: an ask ("make me sad ones") → read every line here → shortlist the clips whose line fits →
review ONLY those comprehensively (contact sheet, then frames around the candidate moments) for their best moments → build.
Since Oct 2, the read is the lines narrow.py finds for the plan.
--own [<folder>]: instead, the person's own footage (_external content, or that folder in it) that's indexed, reviewed or not: key · orientation · seconds · picture · cuts · light · used · folder | one-liner
where one is written. For "make me something from my footage": index the folder they mean (Clip_Index.py --estimate first), find
moments by how they look (visual_search.py), then sheets and one-liners only for the shortlist."""
import collections, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, VIDEO_EXT, posted_on, use_counts  # noqa: E402

a = sys.argv[1:]; arg = lambda k: a[a.index(k) + 1].lower() if k in a else None
load = lambda rel: json.load(open(os.path.join(LIB, rel)))["clips"] if os.path.exists(os.path.join(LIB, rel)) else {}
clips = {**{k: dict(v, _where="lab") for k, v in load("_lab/lab_index.json").items()},
         **{k: dict(v, _where="pasture" if v.get("use") == "pasture" else "main") for k, v in load("_reference/clip_index.json").items()}}
pins = json.load(open(os.path.join(LIB, "_stills/pinterest/index.json"))) if os.path.exists(os.path.join(LIB, "_stills/pinterest/index.json")) else {}
rounds, per = use_counts(clips, pins)   # by number: R06, R06.5, R06.9.8, R07; pins are counted as "pin:<key>"
used_all, used_recent = sum(per.values(), collections.Counter()), sum((per[r] for r in rounds[-2:]), collections.Counter())
rows, todo, untagged, posted = [], [], [], []
acct = (arg("--account") or "").upper()
fits = lambda v: ",".join(v.get("fits") or []) or "-"
TAGS = {t.strip() for t in (arg("--tag") or "").split(",") if t.strip()}; SHORT = "--short" in a
tagged = lambda v: TAGS <= {str(t).lower() for t in (v.get("tags") or [])}
EXTERNAL, OWN = "_external content", None   # --own [<folder>]: the person's own footage, listed without a review
if "--own" in a:
    i = a.index("--own"); f = a[i + 1] if i + 1 < len(a) and not a[i + 1].startswith("--") else ""
    f = f.replace(os.sep, "/").strip("/"); f = f[len("library/"):] if f.startswith("library/") else f
    SHOWN = f if f.lower().startswith(EXTERNAL.lower()) else "/".join(x for x in (EXTERNAL, f) if x); OWN = SHOWN.lower() + "/"   # the drive ignores case
def keep(k, v):
    if v.get("fits") is None and OWN is None: untagged.append(k)
    ok = not acct or acct in [str(x).upper() for x in (v.get("fits") or [])]
    if ok and acct in posted_on(v):   # posted on this account: retired there (rules §11), named at the end so it's seen why
        if tagged(v): posted.append(k)
        return False
    return ok
mark = lambda v: "".join("[POSTED %s] " % x for x in posted_on(v))
for k, v in sorted(clips.items(), key=(lambda kv: kv[1].get("file") or "") if OWN is not None else (lambda kv: (kv[1]["_where"], kv[0].lstrip(".")))):
    if OWN is not None:   # indexed own clips, reviewed or not: machine facts, and the one-liner where there is one
        if not (v.get("file") or "").lower().startswith(OWN) or ("--all" not in a and v.get("use") in ("no", "removed")): continue
    else:
        if v.get("needs_review"): todo.append(k); continue
        if "--all" not in a and v.get("use") not in ("yes", "careful"): continue
    if arg("--type") and (v.get("source_type") or "").lower() != arg("--type"): continue
    if arg("--orient") and (v.get("orientation") or "") != arg("--orient"): continue
    line = v.get("line") or ((v.get("description") or "?") + " — [vibe not written yet]")
    if OWN is not None: line = v.get("line") or ("(analyze failed: %s)" % v["analyze_failed"] if v.get("analyze_failed") else "-")
    elif "personal" in (v.get("flags") or []): line = "[PERSONAL] " + line
    elif "phone_look" in (v.get("flags") or []): line = "[PHONE] " + line
    if OWN is None: line = mark(v) + line
    if arg("--grep") and arg("--grep") not in (k + " " + line + (" " + v.get("file", "") if OWN is not None else "")).lower(): continue
    if not keep(k, v) or not tagged(v): continue
    if OWN is not None:
        w, h = (v.get("active_area") or [v.get("width") or 0, v.get("height") or 0])[:2]; where = os.path.dirname(v.get("file") or "")[len(EXTERNAL) + 1:]
        if SHORT: rows.append("%s | %s | %s" % (k.lstrip("."), line[:200], where[-60:])); continue
        rows.append("%-34s %-10s %4.0fs %-9s cuts %-3d %-6s used %d/%-2d %-38s | %s" % (k.lstrip(".")[:34], v.get("orientation") or "", v.get("duration") or 0,
                    "%dx%d" % (w, h), len(v.get("internal_cuts") or []), (v.get("brightness") or {}).get("label") or "", used_recent[k], used_all[k], where[-38:], line[:200]))
        continue
    if SHORT: rows.append("%s | %s" % (k.lstrip("."), line[:200])); continue
    rows.append("%-34s %-7s %-7s %-8s %-7s %-10s %4.0fs used %d/%-2d %-8s | %s" % (
        k.lstrip(".")[:34], v["_where"], v.get("use") or "", v.get("tier") or "", v.get("source_type") or "", v.get("orientation") or "",
        v.get("duration") or 0, used_recent[k], used_all[k], fits(v), line[:200]))
for k, v in sorted(pins.items()) if OWN is None else []:   # Pinterest stills (for collages and slideshows): one line each, like clips
    if v.get("needs_review"): todo.append("pin:" + k); continue
    if "--all" not in a and v.get("use") not in ("yes", "careful"): continue
    if arg("--orient") and (v.get("orientation") or "") != arg("--orient"): continue
    if arg("--grep") and arg("--grep") not in (k + " " + (v.get("line") or "")).lower(): continue
    if not keep("pin:" + k, v) or not tagged(v): continue
    if SHORT: rows.append("pin:%s | %s" % (k, (mark(v) + (v.get("line") or ""))[:200])); continue
    rows.append("%-34s %-7s %-7s %-8s %-7s %-10s %5s used %d/%-2d %-8s | %s" % (("pin " + v.get("pin_id", ""))[:34], "pin", v.get("use") or "", "",
                v.get("source_type") or "", v.get("orientation") or "", "still", used_recent["pin:" + k], used_all["pin:" + k], fits(v), (mark(v) + (v.get("line") or ""))[:200]))
print("\n".join(rows))
if OWN is not None:   # and what's in that folder but not indexed yet
    d = os.path.join(LIB, SHOWN); known = {(v.get("file") or "").lower() for v in clips.values()}
    if not os.path.isdir(d) and not rows: sys.exit("no such folder in library/: %s" % SHOWN)
    left = [f for dp, _, fn in os.walk(d) for f in fn if f.lower().endswith(VIDEO_EXT) and os.path.relpath(os.path.join(dp, f), LIB).replace(os.sep, "/").lower() not in known]
    print("\n%d own clips listed (indexed; own footage needs no review: machine facts, and the one-liner where one is written)" % len(rows))
    if left: print("%d file(s) there not indexed yet: python3 _scripts/Clip_Index.py --estimate \"%s\" (what indexing them costs)" % (len(left), SHOWN))
    sys.exit(0)
print("\n%d listed (%d clips in the library and lab, %d Pinterest stills)" % (len(rows), len(clips), len(pins)))
if posted: print("LEFT OUT (%d): posted on %s, so retired there (rules §11); the other accounts still use them:\n  %s" % (len(posted), acct, " ".join(posted)))
if untagged: print("NO ACCOUNT TAGS YET (%d): give each a \"fits\" list (the accounts its one-liner suits, from each account's \"what\" in the profile):\n  %s" % (len(untagged), " ".join(untagged[:60]) + (" ..." if len(untagged) > 60 else "")))
if todo: print("NOT REVIEWED YET (%d): fill their hand fields from their sheets before planning:\n  %s" % (len(todo), " ".join(todo)))
