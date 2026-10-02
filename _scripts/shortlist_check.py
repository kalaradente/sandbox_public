#!/usr/bin/env python3
"""Shortlist check: one table of everything that can sink a clip or pin BEFORE moments are picked. Run it on each piece's shortlist right after reading the one-liners, then plan only from the OK rows.
  python3 _scripts/shortlist_check.py --format 9:16|16:9 [--account <id>] [--round <round folder>] <clip or pin> ...
  python3 _scripts/shortlist_check.py --format 16:9 --find "blue hour|dusk"      every usable clip/pin whose line matches
  python3 _scripts/shortlist_check.py --format 9:16 --tag night,couple --find ""  every usable clip/pin with those tags
  python3 _scripts/shortlist_check.py --format 9:16 --own ["<folder>"] [--find "tour|stage"]   the person's own footage (_external content,
        or that folder in it), indexed, reviewed or not; --find matches the one-liner or the file's path. Own clips can also be named.
        One not looked at yet says so (its text or logos aren't known until its sheet is read).
  add --moments to list each usable row's described moments (hooks, best ranges, stills: time + what): plan from these,
  and open a contact sheet only for a clip with no moments yet or none that fits (then write the new one into the index,
  so no clip is looked at twice for the same thing). add --tag <tag>[,<tag>] to keep only clips/pins with those concept tags.
Names can be any unique start of a clip key, or a pin's file name (or its number). Columns:
  ok · key · orientation once upright and bars trimmed (V/H/S) · picture size · upscale to fill the frame · tier · type ·
  use · fits · pieces in this round / in the 2 rounds before (sandbox_paths.use_counts, the count library_view.py and
  narrow.py show; --round: the round being planned, by name or number, default the newest: one that isn't in _lab/rounds
  yet counts nothing and the last line says so) · issues
Issues (!! = don't use, ~ = look first): wrong orientation for the format; upscale over 1.8x (fine only when the whole
piece is one lo-fi world); use no/removed; pastured (a posted clip: never in a video edit for the account it was posted
to); posted on an account ("pastured_<id>" in the index, rules §11: a clip in a video posted there is retired from that
account's videos, a pin from its collages: !! with --account <that id>, ~ with no --account, a plain remark for another
account, which still uses it); already in 2 pieces this round; used in the last 2 rounds; text, logos, watermarks, subtitles, black-and-white, very
dark, strobe, someone else's edit; letterboxed with no measured picture area; personal (flag `personal`: someone filming
herself for her own followers: front camera, her own room, her face filling the frame, eyes on the lens: using it reads as
taking her content, so it stays out unless it was asked for); selfie (flag `selfie`: someone holding their own camera: look twice); phone look (flag `phone_look`: a real clip that looks
shot on a modern phone in ordinary light: everything sharp, everything lit, true colours, clean). The footer says whether the shortlist
mixes film with real footage or sharpness classes (like with like)."""
import collections, glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, posted_on, round_order, round_parts, use_counts  # noqa: E402

a = sys.argv[1:]
def opt(k, d=None):
    if k in a:
        i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return v
    return d
FMT = opt("--format"); ACCT = (opt("--account") or "").upper(); RND = opt("--round"); FIND = opt("--find")
MOMENTS = "--moments" in a; a = [x for x in a if x != "--moments"]
TAGS = [t.strip().lower() for t in (opt("--tag") or "").split(",") if t.strip()]
EXTERNAL, OWN = "_external content", None   # --own [<folder>]: the person's own footage, reviewed or not (no review unless they ask)
if "--own" in a:
    i = a.index("--own"); del a[i]; OWN = EXTERNAL.lower() + "/"
    if i < len(a) and not a[i].startswith("--"):   # a folder after it (else it's a clip name)
        f = a[i].replace(os.sep, "/").strip("/"); f = f[len("library/"):] if f.startswith("library/") else f
        f = f if f.lower().startswith(EXTERNAL.lower()) else EXTERNAL + "/" + f
        if os.path.isdir(os.path.join(LIB, f)): OWN = f.lower() + "/"; del a[i]   # the drive ignores case
if FMT not in ("9:16", "16:9"): sys.exit(__doc__)
W, H = (1080, 1920) if FMT == "9:16" else (1920, 1080); WANT = "V" if FMT == "9:16" else "H"

load = lambda rel: json.load(open(os.path.join(LIB, rel)))["clips"] if os.path.exists(os.path.join(LIB, rel)) else {}
CLIPS = {**load("_lab/lab_index.json"), **load("_reference/clip_index.json")}
_p = os.path.join(LIB, "_stills/pinterest/index.json")
PINS = json.load(open(_p)) if os.path.exists(_p) else {}
PINS = PINS.get("pins", PINS)

# use per round: the one count (sandbox_paths.use_counts: shots, and collage stills by the clip or pin they came from)
rounds, per = use_counts(CLIPS, PINS)   # by number: R06, R06.5, R06.9.8, R07
_r = round_parts(RND) if RND else []   # rounds by name or number: a round's recipes are read wherever it sits in _lab/rounds
cur = os.path.basename(_r[0]) if _r else os.path.basename(os.path.normpath(RND)) if RND else (rounds[-1] if rounds else None)
NO_ROUND = bool(RND) and cur not in per   # --round names no round in _lab/rounds (not made yet, or mistyped): said in the last line
before = (rounds[:rounds.index(cur)] if cur in per else [d for d in rounds if cur and round_order(d) < round_order(cur)])[-2:]
now = per.get(cur) or collections.Counter()
past = sum((per[d] for d in before), collections.Counter())
def uses(k, pin_file=None):
    return now[("pin:" + k) if pin_file else k], past[("pin:" + k) if pin_file else k]

BAD_FLAGS = {"text_overlay": "!!", "subtitles": "!!", "graphics": "!!", "watermark": "~", "tv_logo_watermark": "~",
             "watermark_corner": "~", "small_title_text": "~", "timestamp_text": "~", "title_card_end": "~", "watermark_end": "~",
             "black_and_white": "!!", "very_dark": "~", "dark": "~", "strobe": "~", "someone_elses_edit": "~", "already_an_edit": "~",
             "low_res": "~", "audio_only": "!!", "glitch": "~"}
def picture(v):
    w, h = v.get("width") or 0, v.get("height") or 0
    if v.get("letterboxed") and v.get("active_area"): w, h = v["active_area"][:2]
    if "sideways_footage" in (v.get("flags") or []): w, h = h, w
    return w, h
orient = lambda w, h: "V" if h > w * 1.2 else ("H" if w > h * 1.2 else "S")

def row(k, v, pin=None):
    w, h = picture(v); o = orient(w, h); up = max(W / max(w, 1), H / max(h, 1)); issues = []
    if o not in (WANT, "S"): issues.append("!! %s footage in a %s piece" % ({"V": "vertical", "H": "horizontal"}[o], FMT))
    if up > 1.8: issues.append("~ upscale %.1fx (only in an all lo-fi piece)" % up)
    use = v.get("use") or "?"
    if use in ("no", "removed"): issues.append("!! use: " + use)
    if use == "pasture": issues.append("~ pastured: never in a video edit for the account it was posted to")
    if v.get("needs_review"): issues.append("!! not reviewed yet")
    if (v.get("file") or "").startswith(EXTERNAL + "/") and not v.get("line"): issues.append("~ own footage not looked at yet: text or logos unknown (read its sheet)")
    if v.get("letterboxed") and not v.get("active_area"): issues.append("~ letterboxed, picture area not measured")
    for f in v.get("flags") or []:
        if f in BAD_FLAGS: issues.append("%s %s" % (BAD_FLAGS[f], f))
    if "personal" in (v.get("flags") or []): issues.append("!! personal: someone filming herself for her own followers (only when it was asked for)")
    elif "selfie" in (v.get("flags") or []): issues.append("~ selfie: someone holding their own camera (be wary: look twice)")
    if "phone_look" in (v.get("flags") or []): issues.append("!! phone look: looks shot on a modern phone in ordinary light (every clip has to feel cinematic, rules §8)")
    if ACCT and ACCT not in [str(x).upper() for x in (v.get("fits") or [])]: issues.append("~ not tagged for " + ACCT)
    n, p = uses(k, pin); po = posted_on(v); kind = "a collage" if pin else "a video edit"
    if ACCT in po: issues.append("!! posted on %s: never in %s for it again (rules §11)" % (ACCT, kind))
    elif po and not ACCT: issues.append("~ posted on %s: never in %s for that account (rules §11)" % (", ".join(po), kind))
    if n >= 2: issues.append("!! already in %d pieces this round" % n)
    elif n: issues.append("~ in 1 piece this round")
    if p: issues.append("~ used in %d piece(s) in the last 2 rounds" % p)
    ok = "!!" if any(i.startswith("!!") for i in issues) else ("~ " if issues else "OK")
    if ACCT and po and ACCT not in po: issues.append("(posted on %s: retired there only)" % ", ".join(po))   # a remark, not an issue: this account still uses it
    return ok, dict(v=v, key=("pin " + os.path.splitext(os.path.basename(k))[0][-16:]) if pin else k[:44], o=o, size="%dx%d" % (w, h), up=up, tier=v.get("tier") or "-", type=v.get("source_type") or ("pin" if pin else "-"),
                    use=use, fits=",".join(v.get("fits") or []) or "-", n=n, p=p, issues="; ".join(issues))

def resolve(name):
    m = [k for k in CLIPS if k.lstrip(".").startswith(name.lstrip("."))]
    if len(m) > 1: m = [k for k in m if k == name] or m
    if len(m) == 1: return m[0], CLIPS[m[0]], None
    pm = [k for k in PINS if k.endswith(name) or os.path.splitext(os.path.basename(k))[0].endswith(name)]
    if len(pm) == 1: return pm[0], PINS[pm[0]], os.path.join("_stills/pinterest", pm[0])
    sys.exit("%s: %s" % (name, "no match" if not m and not pm else "matches several: " + ", ".join((m + pm)[:5])))

def moments(v):
    """A clip's described moments, one line each: what planning reads instead of the contact sheet."""
    out = []
    for h in v.get("hooks") or []:
        if isinstance(h, dict): out.append("hook %5.2f      %s" % (h.get("t", 0), h.get("what", "")))
    for b in v.get("best") or []:
        if isinstance(b, dict): out.append("best %5.2f-%5.2f %s%s" % (b.get("in", 0), b.get("out", 0), b.get("what", ""), "  (check: %s)" % b["check"][:60] if b.get("check") else ""))
    for x in v.get("stills") or []:
        if isinstance(x, dict): out.append("still %5.2f     %s" % (x.get("t", 0), x.get("what", "")))
    for x in v.get("avoid") or []:
        if isinstance(x, dict): out.append("avoid %5.2f-%5.2f %s" % (x.get("in", 0), x.get("out", 0), x.get("what", x.get("why", ""))))
    return out

SEARCH = FIND is not None or (OWN is not None and not a)   # --own with no names: every own clip there, like a search
if OWN is not None and SEARCH:
    rx = re.compile(FIND or ".", re.I)
    items = [(k, v, None) for k, v in sorted(CLIPS.items()) if (v.get("file") or "").lower().startswith(OWN) and v.get("use") not in ("no", "removed")
             and rx.search((v.get("line") or "") + " " + v["file"])]
elif FIND is not None:
    rx = re.compile(FIND or ".", re.I)
    items = [(k, v, None) for k, v in sorted(CLIPS.items()) if v.get("use") in ("yes", "careful", "pasture") and rx.search(v.get("line") or "")]
    items += [(k, v, os.path.join("_stills/pinterest", k)) for k, v in sorted(PINS.items()) if v.get("use") in ("yes", "careful") and rx.search(v.get("line") or "")]
else:
    items = [resolve(n) for n in a]
if TAGS: items = [it for it in items if set(TAGS) <= {str(t).lower() for t in (it[1].get("tags") or [])}]
if not items: sys.exit("nothing to check")
rows = [row(*it) for it in items]; skipped = 0
if SEARCH:   # a search lists only what fits the frame; the rest is counted
    keep = [r for r in rows if "footage in a" not in r[1]["issues"]]; skipped = len(rows) - len(keep)
    rows = sorted(keep, key=lambda r: (r[0] == "!!", r[0] != "OK"))
    if not rows: sys.exit("nothing in this orientation matches (%d in the other)" % skipped)
print("%-2s %-44s %s %-9s %5s %-8s %-7s %-7s %-6s %s  %s" % ("", "key", "o", "picture", "up", "tier", "type", "use", "fits", "now/2r", "issues"))
for ok, r in rows:
    print("%-2s %-44s %s %-9s %4.1fx %-8s %-7s %-7s %-6s %d/%d   %s" % (ok, r["key"], r["o"], r["size"], r["up"], r["tier"][:8], r["type"][:7], r["use"][:7], r["fits"][:6], r["n"], r["p"], r["issues"]))
    if MOMENTS and ok != "!!":
        ms = moments(r["v"])
        if ms or not r["key"].startswith("pin "): print("\n".join("      " + m for m in ms) if ms else "      (no moments described yet: read its sheet once, then write them into the index)")
good = [r for ok, r in rows if ok != "!!"]
types = collections.Counter(r["type"] for r in good if r["type"] not in ("-", "pin")); tiers = collections.Counter(r["tier"] for r in good if r["tier"] != "-")
print("\n%d checked: %d OK, %d look first, %d don't use%s.  This round: %s; before it: %s" % (
    len(rows), sum(ok == "OK" for ok, _ in rows), sum(ok == "~ " for ok, _ in rows), sum(ok == "!!" for ok, _ in rows),
    " (%d more match in the other orientation)" % skipped if skipped else "",
    (os.path.basename(cur) if cur else "-") + (" (no such round in _lab/rounds: nothing counted for it)" if NO_ROUND else ""), ", ".join(os.path.basename(d) for d in before) or "-"))
if len(types) > 1: print("Like with like: the usable rows mix %s (film only with film, real only with real)." % dict(types))
if len(tiers) > 1: print("Sharpness classes among the usable rows: %s (one class per piece unless it's all lo-fi)." % dict(tiers))
