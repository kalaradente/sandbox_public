#!/usr/bin/env python3
"""This or that: a taste game on the phone. Two pictures side by side (or two captions under one picture) and the reviewer
taps the one they'd use. Claude's guess for every pair is sealed on this Mac first and never reaches the page. A hundred
taps say more about someone's eye than a round's verdicts do, and the score says whether Claude's eye is getting closer.

  python3 review/pairs.py make <deck> [--pins 45] [--frames 25] [--seed 1]
        candidate pairs from the library -> library/_lab/pairs/<deck>/: candidates.json, img/<id>a.jpg and <id>b.jpg, and
        board_<k>.jpg (six pairs a board, numbered) for the eye. A pair is two pictures of one kind (a pin with a pin, a
        frame of a real clip with another clip's) that the visual index says look alike, the same subject, so the choice
        is about the look. Pins already in an earlier deck are left out.
  python3 review/pairs.py doc <deck>
        the page's doc (db: decks/<deck>) -> deck.json, and files.json (the pictures to publish beside the page: the
        Artifact tool's files list, with root library/_lab/pairs). Only pairs with a sealed guess go in: sealed.json = {"guesses": {"<id>": {"guess": "a" | "b" |
        "neither" | "both", "sure": 0.5-1.0, "why": "..."}}}, written by Claude from the boards (a candidate with no guess
        is left out). captions.json, if there = [{"id": "c01", "img": "<a file in img/>", "a": "...", "b": "..."}], each
        with its sealed guess too. The first run stamps sealed.json with the time; a guess changed after that is refused,
        here and when scoring, and so is a sealed.json that has lost its stamp once the deck's doc is made.
  python3 review/pairs.py score <deck> <picks.json | folder>
        the page's picks read back (ArtifactData's out_dir, or one file) against the sealed guesses -> results.csv in the
        deck's folder, one line in library/_lab/pairs/scoreboard.csv (scoring a deck again replaces its line), and the
        table printed.

The page (review/pairs.html, published once as a private page with the db capability; a deck's pictures are published
beside it as files, <deck>/img/<name>.jpg) stores decks/<deck> {deck, title, pushed, pairs: [{id, kind: "picture" | "caption",
a, b, img}]} (a picture pair: a and b are picture paths; a caption pair: a and b are the two lines, img the picture) and
picks/<deck>__<id> {deck, id, pick: "a" | "b" | "neither" | "both", ms, note, at}. Picks are verdicts, never lessons: what
the reviewer chose is recorded, and anything Claude reads into them is a suggestion to ask about."""
import csv, datetime, hashlib, io, json, os, random, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "_scripts"))
from sandbox_paths import LIB, locked, save_json, write_atomic  # noqa: E402

OUT = os.path.join(LIB, "_lab", "pairs")
BAD = {"personal", "text", "text_overlay", "already_an_edit", "watermark", "black_and_white", "very_dark", "low_res", "logo_cropped"}
BAND = (0.70, 0.86)   # how alike two pictures are in the visual index: under it another subject, over it nearly the same picture
SIDE = 900            # long side of a deck picture
PICKS = ("a", "b", "neither", "both")
load = lambda rel: json.load(open(os.path.join(LIB, rel)))
folder = lambda deck: os.path.join(OUT, deck)


def pool():
    """(pins, frames): everything a pair can be made of, each {key, kind, file, t, line, group, emb}."""
    import numpy as np
    z = np.load(os.path.join(LIB, "_reference", "visual_index.npz"), allow_pickle=False)
    keys = z["keys"].tolist(); times = z["times"]; emb = z["emb"]; at = {}
    for i, k in enumerate(keys): at.setdefault(k, []).append(i)
    unit = lambda i: emb[i].astype(np.float32) / np.linalg.norm(emb[i].astype(np.float32))
    pins = []
    for k, p in load("_stills/pinterest/index.json").items():
        if p.get("use") not in ("yes", "careful") or "pin:" + k not in at or p["height"] < p["width"] * 1.1: continue
        if not os.path.exists(os.path.join(LIB, p["file"])): continue
        pins.append(dict(key="pin:" + k, kind="pin", file=p["file"], t=None, line=p.get("line") or "", use=p["use"],
                         group=(p.get("people") or "", p.get("look") or ""), emb=unit(at["pin:" + k][0])))
    clips = {**load("_lab/lab_index.json")["clips"], **load("_reference/clip_index.json")["clips"]}; frames = []
    for k, c in clips.items():
        if c.get("source_type") != "real" or c.get("use") not in ("yes", "careful") or c.get("orientation") != "vertical": continue
        if c.get("letterboxed") or set(c.get("flags") or []) & BAD or str(c.get("file", "")).startswith("_external"): continue
        if k not in at or not os.path.exists(os.path.join(LIB, c["file"])): continue
        best = c.get("best") or []; d = float(c.get("duration") or 0)
        t = (float(best[0]["in"]) + float(best[0]["out"])) / 2 if best else d / 2   # the middle of its best moment
        i = min(at[k], key=lambda j: abs(float(times[j]) - t))
        frames.append(dict(key=k, kind="frame", file=c["file"], t=round(float(times[i]), 2), line=c.get("line") or "", use=c["use"],
                           group=(k.rsplit("_", 1)[0],), emb=unit(i)))   # group = the creator: never two frames of one account
    return pins, frames


def match(items, n, rng, same_group):
    """n pairs from items: alike in the visual index (BAND), each picture used once. Pins pair inside their look and
    people when they have them (same_group); frames never pair inside one creator."""
    import numpy as np
    if not items: return []
    E = np.stack([x["emb"] for x in items]); S = E @ E.T; order = list(range(len(items))); rng.shuffle(order); used = set(); out = []
    for i in order:
        if len(out) >= n: break
        if i in used: continue
        ok = [j for j in order if j != i and j not in used and BAND[0] <= S[i, j] <= BAND[1]
              and ((items[i]["group"] == items[j]["group"]) if same_group else (items[i]["group"] != items[j]["group"]))]
        if not ok: continue
        j = rng.choice(sorted(ok, key=lambda j: -S[i, j])[:4])   # among the four most alike: the same subject, not always the nearest
        used |= {i, j}; a, b = (i, j) if rng.random() < 0.5 else (j, i)
        out.append((items[a], items[b], float(S[i, j])))
    return out


def picture(x, dst):
    src = os.path.join(LIB, x["file"])
    if x["kind"] == "pin":
        from PIL import Image
        im = Image.open(src).convert("RGB"); im.thumbnail((SIDE, SIDE)); im.save(dst, "JPEG", quality=82)
    else:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % x["t"], "-i", src, "-frames:v", "1",
                            "-vf", "scale=-2:%d" % SIDE, "-q:v", "4", dst], capture_output=True, text=True)
        if r.returncode or not os.path.exists(dst): raise RuntimeError("no frame from %s at %s: %s" % (x["file"], x["t"], r.stderr.strip()[-200:]))


def boards(d, cands):
    """Six pairs a board, each numbered, a over b's left: what Claude looks at to seal its guesses."""
    from PIL import Image, ImageDraw
    W, H, G = 300, 440, 10
    for k in range(0, len(cands), 6):
        part = cands[k:k + 6]; rows = (len(part) + 2) // 3
        im = Image.new("RGB", (3 * (2 * W + 3 * G), rows * (H + 34)), (18, 18, 20)); dr = ImageDraw.Draw(im)
        for n, c in enumerate(part):
            x0 = (n % 3) * (2 * W + 3 * G) + G; y0 = (n // 3) * (H + 34)
            dr.text((x0, y0 + 8), "%s   a | b   (%s, %.2f)" % (c["id"], c["kind"], c["alike"]), fill=(240, 240, 240))
            for s, side in enumerate("ab"):
                p = Image.open(os.path.join(d, "img", c["id"] + side + ".jpg")); p.thumbnail((W, H))
                im.paste(p, (x0 + s * (W + G) + (W - p.width) // 2, y0 + 30 + (H - p.height) // 2))
        im.save(os.path.join(d, "board_%02d.jpg" % (k // 6 + 1)), quality=85)


def make(deck, n_pins, n_frames, seed):
    d = folder(deck); os.makedirs(os.path.join(d, "img"), exist_ok=True)
    if os.path.exists(os.path.join(d, "sealed.json")): sys.exit("%s has sealed guesses: make a new deck instead of re-dealing this one" % deck)
    seen = set()   # pictures an earlier deck already showed
    for other in sorted(os.listdir(OUT)):
        f = os.path.join(OUT, other, "candidates.json")
        if other != deck and os.path.exists(f): seen |= {x["key"] for c in json.load(open(f)) for x in (c["a"], c["b"])}
    pins, frames = pool(); pins = [x for x in pins if x["key"] not in seen]; frames = [x for x in frames if x["key"] not in seen]
    rng = random.Random(seed); cands = []
    for kind, pairs in (("pin", match(pins, n_pins, rng, True)), ("frame", match(frames, n_frames, rng, False))):
        for a, b, s in pairs:
            c = dict(id="%s%02d" % (kind[0], sum(1 for x in cands if x["kind"] == kind) + 1), kind=kind, alike=round(s, 3))
            for side, x in (("a", a), ("b", b)):
                picture(x, os.path.join(d, "img", c["id"] + side + ".jpg"))
                c[side] = {k: x[k] for k in ("key", "file", "t", "line", "use")}
            cands.append(c)
    save_json(os.path.join(d, "candidates.json"), cands); boards(d, cands)
    print("%d pin pairs, %d frame pairs (from %d pins, %d clips) -> %s" % (sum(c["kind"] == "pin" for c in cands),
          sum(c["kind"] == "frame" for c in cands), len(pins), len(frames), d))


def sealed(deck):
    """(file, sealed.json, the mark of its guesses), for doc and for score alike: a guess that no longer matches its mark is
    refused by both, and so is a seal that is gone once the deck's doc is made (it would be sealed again, after the picks)."""
    f = os.path.join(folder(deck), "sealed.json")
    if not os.path.exists(f): sys.exit("no sealed.json in %s: write the guesses first" % folder(deck))
    s = json.load(open(f))
    for k, g in s["guesses"].items():
        if g.get("guess") not in PICKS or not 0.5 <= float(g.get("sure", 0)) <= 1: sys.exit("sealed.json: %s needs a guess (%s) and sure 0.5-1.0" % (k, " | ".join(PICKS)))
    mark = hashlib.sha256(json.dumps(s["guesses"], sort_keys=True).encode()).hexdigest()[:16]
    if s.get("sealed_at") and s.get("mark") != mark: sys.exit("sealed.json changed after it was sealed (%s): a sealed guess stays as it was" % s["sealed_at"])
    if not s.get("sealed_at") and os.path.exists(os.path.join(folder(deck), "deck.json")):
        sys.exit("sealed.json has no seal (sealed_at, mark) and %s's doc is already made (deck.json): the guesses can't be shown to be the ones sealed. Put the seal back as it was, or deal a new deck" % deck)
    return f, s, mark


def doc(deck):
    d = folder(deck); f, s, mark = sealed(deck)
    cands = json.load(open(os.path.join(d, "candidates.json"))); pairs = []; files = set()
    pub = lambda name: files.add("%s/img/%s" % (deck, name)) or "%s/img/%s" % (deck, name)   # published where it sits under library/_lab/pairs
    for c in cands:
        if c["id"] in s["guesses"]: pairs.append(dict(id=c["id"], kind="picture", a=pub(c["id"] + "a.jpg"), b=pub(c["id"] + "b.jpg")))
    cf = os.path.join(d, "captions.json")
    for c in (json.load(open(cf)) if os.path.exists(cf) else []):
        if c["id"] not in s["guesses"]: sys.exit("captions.json: %s has no sealed guess" % c["id"])
        if not os.path.exists(os.path.join(d, "img", c["img"])): sys.exit("captions.json: no picture %s" % c["img"])
        pairs.append(dict(id=c["id"], kind="caption", img=pub(c["img"]), a=c["a"], b=c["b"]))
    extra = sorted(set(s["guesses"]) - {p["id"] for p in pairs})
    if extra: sys.exit("sealed guesses for pairs that aren't there: %s" % ", ".join(extra))
    random.Random(mark).shuffle(pairs)   # kinds mixed, the same order every run
    if not s.get("sealed_at"): s.update(sealed_at=datetime.datetime.now().isoformat(timespec="seconds"), mark=mark); save_json(f, s)
    body = dict(deck=deck, title=s.get("title") or deck, pushed=datetime.datetime.now().isoformat(timespec="seconds"), pairs=pairs)
    save_json(os.path.join(d, "deck.json"), body); save_json(os.path.join(d, "files.json"), [dict(path=f) for f in sorted(files)])
    print("%d pairs (%d pictures, %d captions), sealed %s -> %s" % (len(pairs), sum(p["kind"] == "picture" for p in pairs),
          sum(p["kind"] == "caption" for p in pairs), s["sealed_at"], os.path.join(d, "deck.json")))


def rows(path):
    if os.path.isdir(path):   # ArtifactData out_dir: <path>/picks/<id>.json (or the files straight in it)
        sub = os.path.join(path, "picks"); p = sub if os.path.isdir(sub) else path
        got = [json.load(open(os.path.join(p, f))) for f in sorted(os.listdir(p)) if f.endswith(".json")]
    else:
        got = json.load(open(path))
    if isinstance(got, dict): got = got.get("docs") or got.get("documents") or got.get("results") or ([got] if got.get("pick") or isinstance(got.get("data"), dict) else list(got.values()))
    for r in got:
        body = r.get("data") if isinstance(r, dict) and isinstance(r.get("data"), dict) else r
        if isinstance(body, dict) and body.get("deck") and body.get("id") and body.get("pick") in PICKS: yield body


def score(deck, path):
    d = folder(deck); _, s, _ = sealed(deck); g = s["guesses"]; picks = {r["id"]: r for r in rows(path) if r["deck"] == deck}
    shown = {p["id"]: p for p in json.load(open(os.path.join(d, "deck.json")))["pairs"]}; out = []
    for i, p in shown.items():
        if i not in picks: continue
        r = picks[i]; out.append(dict(id=i, kind=p["kind"], pick=r["pick"], guess=g[i]["guess"], sure=g[i]["sure"], hit=int(r["pick"] == g[i]["guess"]),
                                      ms=r.get("ms") or "", note=(r.get("note") or "").strip(), why=g[i].get("why") or "", a=p["a"], b=p["b"]))
    if not out: sys.exit("no picks for %s in %s" % (deck, path))
    with open(os.path.join(d, "results.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    rate = lambda xs: "%d/%d" % (sum(x["hit"] for x in xs), len(xs)) if xs else "-"
    common = max(PICKS, key=lambda k: sum(x["pick"] == k for x in out))   # the base rate: always guessing the commonest pick
    side = [x for x in out if x["pick"] in "ab" and x["guess"] in "ab"]; sure = [x for x in out if float(x["sure"]) >= 0.8]
    line = dict(deck=deck, scored_at=datetime.datetime.now().isoformat(timespec="seconds"), shown=len(shown), picked=len(out), right=rate(out),
                pictures=rate([x for x in out if x["kind"] == "picture"]), captions=rate([x for x in out if x["kind"] == "caption"]),
                when_sure=rate(sure), when_unsure=rate([x for x in out if x not in sure]), side_right=rate(side),
                base="%d/%d (%s)" % (sum(x["pick"] == common for x in out), len(out), common),
                picks=" ".join("%s:%d" % (k, sum(x["pick"] == k for x in out)) for k in PICKS))
    with locked(os.path.join(OUT, "scoreboard.csv")) as p:   # one line a deck: scoring it again replaces its line (forecast.py does the same for a round)
        r = csv.DictReader(open(p, newline="", encoding="utf-8")) if os.path.exists(p) else None; old = [x for x in r or [] if x.get("deck") != deck]
        t = io.StringIO(); w = csv.DictWriter(t, fieldnames=(r and r.fieldnames) or list(line), extrasaction="ignore"); w.writeheader(); w.writerows(old + [line])
        write_atomic(p, t.getvalue())
    for k, v in line.items(): print("  %-12s %s" % (k, v))
    print("missed (the pick, then the sealed guess):")
    for x in out:
        if not x["hit"]: print("  %-4s %-8s %-8s sure %.2f  %s%s" % (x["id"], x["pick"], x["guess"], float(x["sure"]), x["why"], ("  | note: " + x["note"]) if x["note"] else ""))
    notes = [x for x in out if x["note"] and x["hit"]]
    if notes: print("notes on the rest:"); [print("  %-4s %s" % (x["id"], x["note"])) for x in notes]
    print("-> %s" % os.path.join(d, "results.csv"))


def main():
    a = sys.argv[1:]; opt = lambda name, default: int(a[a.index(name) + 1]) if name in a else default
    if len(a) >= 2 and a[0] == "make" and "--help" not in a: return make(a[1], opt("--pins", 45), opt("--frames", 25), opt("--seed", 1))
    if len(a) == 2 and a[0] == "doc": return doc(a[1])
    if len(a) == 3 and a[0] == "score": return score(a[1], a[2])
    sys.exit(__doc__)


if __name__ == "__main__": main()
