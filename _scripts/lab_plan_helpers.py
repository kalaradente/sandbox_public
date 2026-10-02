"""Helpers for writing lab recipes: shots that stay inside one camera shot, on the beat grid.
  fit(prefix, t, dur)                       a shot of exactly dur near t that crosses no cut, or an error: never shorter
  video(rnd, rid, account, fmt, shots, cuts_at, ...)   shots [(prefix, t, {options}...)] cut at the edit times in cuts_at
  silent(rnd, rid, account, fmt, shots, ...)        shots [(prefix, t, dur, {options}...)], one after another
  collage(rnd, rid, account, fmt, files, ...)       stills (paths inside library/), sec each: pins and photos; a frame from
                                            a video only when it is named in approved=[...] (each one approved by name)
  song_window(window, song=None)            a recipe's song block for one of the song's windows, read live from its markers
Each writes <rnd>/<rid>.json and returns the edit's length. A clip's cuts are remembered (in memory and in a small cache
file in the temp folder, keyed on the file's size and date), so planning again after one fix decodes nothing it has seen.
A video shot whose picture loses more than LOSES of its width or height to the fill crop (a wide clip in a tall frame) gets
"center": "face": the render frames on the people in it instead of cutting from the middle (R07: faces the shots were chosen
for came out cut). A shot's own {"center": ...} option wins; with no face in it, it's framed from the middle as before.
What the planner refuses: a clip flagged personal (unless allow_personal=True), in a video or as a collage frame; a clip
already in a video posted on the piece's account, or a pin already in a collage posted on it ("pastured_<account id>" in
the index: retired on that account only, rules §11); a collage frame from a video that isn't in approved."""
import json, os, sys, math, tempfile
sys.path.insert(0, os.path.dirname(__file__)); from shot_check import cuts_in
from sandbox_paths import LIB, find_account, posted_on, still_key  # library/: footage, indexes, lab
_load = lambda rel: json.load(open(os.path.join(LIB, rel)))["clips"] if os.path.exists(os.path.join(LIB, rel)) else {}   # a fresh library has neither yet
MAIN = _load("_reference/clip_index.json")
IDX = {**_load("_lab/lab_index.json"), **MAIN}
_p = os.path.join(LIB, "_stills/pinterest/index.json"); PINS = json.load(open(_p)) if os.path.exists(_p) else {}; PINS = PINS.get("pins", PINS)
def _posted(account, entry):
    """The account's id when this clip or pin was already posted on it (retired there only, rules §11), else None."""
    a = str((find_account(account) or {}).get("id") or account or "").upper()
    return a if a in posted_on(entry) else None
CUT_CACHE = os.path.join(tempfile.gettempdir(), "sandbox_cut_cache.json")
try: _CUTS = json.load(open(CUT_CACHE))
except (OSError, ValueError): _CUTS = {}
def cuts(f, a, b):
    """cuts_in(f, a, b), remembered: a range inside one already scanned (this run or an earlier one) decodes nothing."""
    st = os.stat(f); key = "%s|%d|%d|%d" % (f, st.st_size, int(st.st_mtime), int(os.path.getmtime(cuts_in.__code__.co_filename)))   # a new cut finder starts afresh
    for a0, b0, cs in _CUTS.get(key, []):
        if a0 <= a and b <= b0: return [c for c in cs if a <= c <= b + 0.05]
    cs = cuts_in(f, a, b); _CUTS.setdefault(key, []).append((a, b, cs))
    try: json.dump(_CUTS, open(CUT_CACHE + ".tmp", "w")); os.replace(CUT_CACHE + ".tmp", CUT_CACHE)
    except OSError: pass
    return cs
def K(prefix):
    m = [k for k in IDX if k.lstrip(".").startswith(prefix.lstrip("."))]
    if len(m) > 1: m = [k for k in m if k in MAIN] or m   # prefer the main library when a lab clip shares the prefix
    if len(m) != 1: raise KeyError((prefix, m[:5]))
    return m[0]
def fit(prefix, t, dur, slack=3.0):
    """Place a `dur`-long shot as close to time t as possible without crossing a cut (2-frame margin). Never shorter than
    asked: with no clean dur near t it stops with an error, so the planner picks another moment."""
    k = K(prefix); v = IDX[k]; f = os.path.join(LIB, v["file"]); D = v.get("duration") or 999
    if not cuts(f, t, t + dur) and t + dur <= D - 0.05: return dict(clip=k, dur=dur, **{"in": round(t, 3)})
    lo, hi = max(0, t - slack), min(D - 0.05, t + dur + slack); cs = [lo] + cuts(f, lo, hi) + [hi]; M = 2 / 30; best = None
    for a, b in zip(cs, cs[1:]):
        a2, b2 = a + M, b - M
        if b2 - a2 < dur: continue
        st = min(max(t, a2), b2 - dur); sc = abs(st - t)
        if best is None or sc < best[0]: best = (sc, st)
    if best is None:
        a, b = max(zip(cs, cs[1:]), key=lambda ab: ab[1] - ab[0])
        raise ValueError("%s @%.2f: no clean %.2f s within %.0f s of it (the longest clean span near it is %.2f s, %.2f-%.2f): "
                         "pick another moment or pull more footage, never a shorter shot" % (k, t, dur, slack, max(0, b - a - 2 * M), a, b))
    if abs(best[1] - t) > 0.5: print("  moved %s @%.2f -> %.2f (look: may be a different shot)" % (k[:24], t, best[1]))
    return dict(clip=k, dur=dur, **{"in": round(best[1], 3)})
LOSES = 0.1
def loses(k, fmt):
    """The share of clip k's picture (inside any bars, turned upright) the fill crop cuts off in a fmt frame."""
    import Lab_Render as LR
    c = IDX[k]; w, h = LR.picture_size(c, LR.framing(c)); W, H = LR.size(fmt)
    return 1 - min(w / h / (W / H), (W / H) / (w / h))
def recipe(path, rid, account, fmt, shots, song=None, caption="", lyrics=False, story="", why="", allow_personal=False, **extra):
    """allow_personal=True only when a personal clip was asked for (flag `personal` in the index: someone filming herself for her
    own followers; using it reads as taking her content)."""
    mine = sorted({s["clip"] for s in shots if "personal" in ((IDX.get(s["clip"]) or {}).get("flags") or [])})
    if mine and not allow_personal: raise ValueError("%s uses a personal clip (%s): leave it out, or allow_personal=True when it was asked for" % (rid, ", ".join(k[:30] for k in mine)))
    ph = sorted({s["clip"] for s in shots if "phone_look" in ((IDX.get(s["clip"]) or {}).get("flags") or [])})
    if ph: print("  %s uses a clip flagged phone_look (%s): every clip has to feel cinematic (rules §8): keep it only with a reason" % (rid, ", ".join(k[:30] for k in ph)))
    gone = sorted({s["clip"] for s in shots if _posted(account, IDX.get(s["clip"]) or {})})   # a clip in a video posted on an account is retired there
    if gone: raise ValueError("%s uses a clip already in a video posted on %s (%s): it is retired on that account (rules §11); the other accounts still use it" % (rid, _posted(account, IDX[gone[0]]), ", ".join(k[:30] for k in gone)))
    r = dict(id=rid, account=account, kind="video", format=fmt, song=song, caption=caption, lyrics=lyrics, story=story, why=why, shots=shots,
             sources=sorted({s["clip"] for s in shots}), **extra)
    assert len(r["sources"]) <= 15, (rid, "over 15 sources")
    for i, x in enumerate(shots):   # no repeated picture: two shots from the same clip within 1.5 s of each other
        for y in shots[i + 1:]:
            if x["clip"] == y["clip"] and abs(x["in"] - y["in"]) < 1.5: raise ValueError("%s repeats %s @%.2f/%.2f" % (rid, x["clip"][:20], x["in"], y["in"]))
    json.dump(r, open(os.path.join(path, rid + ".json"), "w"), indent=1)
    return round(sum(s["dur"] for s in shots), 3)
def song_window(window, song=None):
    """A recipe's song block for the song's window named `window` (or the start of its name), read from its markers file
    (song_markers.py; song None = the current song): window times are never copied into a planner, they go stale."""
    import songs, song_markers as SM
    e = songs.get(song); ws = SM.windows(SM.load(song=e["id"]))
    w = next((w for w in ws if w["name"] == window), None) or next((w for w in ws if w["name"].lower().startswith(window.lower())), None)
    if not w: raise KeyError("%s has no window %r (its windows: %s)" % (e["name"], window, ", ".join(w["name"] for w in ws) or "none yet"))
    return dict(name=e["id"], window=w["name"], start=w["start"])
def video(rnd, rid, account, fmt, shots, cuts_at, song=None, caption="", lyrics=False, story="", why="", **extra):
    """shots [(prefix, t, {options}...)]: shot i fills cuts_at[i]..cuts_at[i + 1] (edit seconds: the cuts on the song's kicks),
    each fitted at exactly that length; options (e.g. {"reverse": True}) go on the shot. extra: any other recipe keys (tags, fx ...)."""
    if len(shots) != len(cuts_at) - 1: raise ValueError("%s: %d shots need %d cut times, got %d" % (rid, len(shots), len(shots) + 1, len(cuts_at)))
    ss = []
    for (pre, t, *opt), a, b in zip(shots, cuts_at, cuts_at[1:]):
        s = fit(pre, t, round(b - a, 3))
        if loses(s["clip"], fmt) > LOSES: s["center"] = "face"   # framed on the people, not from the middle
        for o in opt: s.update(o)
        ss.append(s)
    return recipe(rnd, rid, account, fmt, ss, song=song, caption=caption, lyrics=lyrics, story=story, why=why, **extra)
def silent(rnd, rid, account, fmt, shots, caption="", story="", why="", **extra):
    """shots [(prefix, t, dur, {options}...)], one after another, no song."""
    at = [0.0]
    for sh in shots: at.append(round(at[-1] + sh[2], 3))
    return video(rnd, rid, account, fmt, [(p, t, *o) for p, t, d, *o in shots], at, caption=caption, story=story, why=why, **extra)
def collage(rnd, rid, account, fmt, files, caption="", sec=0.5, story="", why="", approved=(), allow_personal=False, **extra):
    """files: stills as paths inside library/, sec each, no song. Pins: a frame from a video (a lab_still.grab(...)["file"], a frame of a clip) must
    be in approved, the stills that were each approved by name, by the same path. A photo (the person's own, a photo post) is
    not a frame: the house rule keeps house collages to pins (rules §13), the public version builds slideshows from photos. A frame from a clip flagged personal is refused
    like a personal shot (allow_personal=True only when it was asked for); one flagged phone_look is named, as in recipe()."""
    for f in files:
        if not os.path.exists(os.path.join(LIB, f)): raise ValueError("%s: no such still %s" % (rid, f))
    key = still_key(IDX, PINS); src = {f: key({"file": f}) for f in files}; of = lambda f: PINS.get(src[f][4:]) if src[f].startswith("pin:") else IDX.get(src[f])
    flagged = lambda flag: sorted({f for f in files if flag in ((of(f) or {}).get("flags") or [])})
    frame = lambda f: src[f] in IDX or f.replace(os.sep, "/").startswith(("_lab/stills/", "_stills/candidates/"))   # a frame taken from a video (lab_still.grab, the old stills step): photos and pins aren't
    own = [f for f in files if not src[f].startswith("pin:") and frame(f) and f not in approved]
    if own: raise ValueError("%s: %d frame(s) from a video (%s): a collage is made from pins and photos; a frame goes in only when it was approved by name, in approved=[...]" % (rid, len(own), ", ".join(own)))
    if flagged("personal") and not allow_personal: raise ValueError("%s uses a still from a personal clip (%s): leave it out, or allow_personal=True when it was asked for" % (rid, ", ".join(flagged("personal"))))
    if flagged("phone_look"): print("  %s uses a still flagged phone_look (%s): every clip and photo has to feel cinematic (rules §8): keep it only with a reason" % (rid, ", ".join(flagged("phone_look"))))
    gone = [f for f in files if src[f].startswith("pin:") and _posted(account, of(f) or {})]   # a pin in a collage posted on this account; a clip posted in a video doesn't cross over
    if gone: raise ValueError("%s uses a pin already in a collage posted on %s (%s): it is retired on that account (rules §11)" % (rid, _posted(account, of(gone[0])), ", ".join(gone)))
    r = dict(id=rid, account=account, kind="collage", format=fmt, song=None, caption=caption, lyrics=False, story=story, why=why,
             sec_per_still=sec, stills=[{"file": f} for f in files], sources=[os.path.basename(f) for f in files], **extra)
    json.dump(r, open(os.path.join(rnd, rid + ".json"), "w"), indent=1)
    return round(sec * len(files), 2)
