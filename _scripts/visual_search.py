#!/usr/bin/env python3
"""Visual search: find footage by what it LOOKS like, and check that each shot looks like the concept.
  python3 _scripts/visual_search.py --build [--no-external]       embed new clips (a frame a second) and Pinterest pins (--no-external:
        not the person's own clips in _external content: a big shoot takes long; the ones embedded already stay)
  python3 _scripts/visual_search.py "golden hour sunlight" [--format 9:16] [--top 25] [--tag couple] [--pins]
        the clips (and with --pins, pins) whose frames look most like the words, with the best moment's time
  python3 _scripts/visual_search.py --check <round folder | recipe.json> [golden-hour,road | "free words"]
        concept tags (the light: golden-hour / daylight / blue-hour / night; the place: road / city / beach / home / club /
        concert / nature) check each shot like for like: it flags when another light or place fits the picture better
        (R06: "golden hour" whose first and last shots read as daylight; "the drive home" with sidewalk shots). Free words
        (default: the edit's caption) only flag outliers, the shots that look least like the words. With nothing given, a
        recipe's own "tags" (its light and place, e.g. ["golden-hour"]) are used, so a whole round checks in one go.
The model is CLIP ViT-B/16 (ONNX, ~150 MB, downloaded once into the Hugging Face cache); it runs on the Mac's CPU, no GPU.
Embeddings: library/_reference/visual_index.npz (re-run --build after new footage; only new or changed clips are done).
Scores are relative: compare clips with each other for one query, never across queries.
How far to trust it (tested on R06, Sep 28): it caught both sidewalk shots flagged in a reviewed night-drive piece and left the
perfect night drives mostly alone, but it can't reliably tell golden hour from plain daylight. Its flags are a second look,
never a verdict; the search is a way in to footage the one-liners describe differently."""
import glob, io, json, os, subprocess, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, round_files, write_atomic  # noqa: E402

REPO = "Xenova/clip-vit-base-patch16"
STORE = os.path.join(LIB, "_reference", "visual_index.npz")
EMBED_V = "1-each"   # how pictures are embedded; an index made another way is rebuilt whole on the next --build
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32); STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
load = lambda rel: json.load(open(os.path.join(LIB, rel)))["clips"] if os.path.exists(os.path.join(LIB, rel)) else {}
_m = {}


def fetch(name):
    """A model file from the Hugging Face cache; downloaded once (then no network, and no rate-limit warnings)."""
    from huggingface_hub import hf_hub_download
    try: return hf_hub_download(REPO, name, local_files_only=True)
    except Exception: return hf_hub_download(REPO, name)


def model(part):
    """The vision or text half of CLIP (downloaded on first use)."""
    if part not in _m:
        import onnxruntime as ort
        f = fetch("onnx/%s_model_quantized.onnx" % part)
        o = ort.SessionOptions(); o.log_severity_level = 3
        _m[part] = ort.InferenceSession(f, o, providers=["CPUExecutionProvider"])
    return _m[part]


def embed_images(ims):
    """RGB uint8 arrays -> unit vectors. CLIP's own preprocessing: shortest side 224, centre crop, normalise."""
    import cv2
    x = []
    for im in ims:
        h, w = im.shape[:2]; s = 224 / min(h, w)
        im = cv2.resize(im, (max(224, round(w * s)), max(224, round(h * s))), interpolation=cv2.INTER_AREA)
        y0, x0 = (im.shape[0] - 224) // 2, (im.shape[1] - 224) // 2
        x.append(((im[y0:y0 + 224, x0:x0 + 224].astype(np.float32) / 255 - MEAN) / STD).transpose(2, 0, 1))
    if not x: return np.zeros((0, 512), np.float32)
    out = []
    for xi in x:   # one at a time: the quantized model scales each batch as a whole, so a picture's vector shifted with its
        e = model("vision").run(None, {"pixel_values": xi[None]})   # batch-mates (scores ±0.012, over MARGIN; measured Sep 28)
        out.append(next(v for v in e if v.ndim == 2))
    e = np.concatenate(out); return e / np.linalg.norm(e, axis=1, keepdims=True)


_te = {}


def embed_text(t):
    """Words -> a unit vector, kept for the run: --check asks for the same few phrases for every recipe (the same words
    always give the same vector, so this changes no score)."""
    if t not in _te:
        if "tok" not in _m:
            from tokenizers import Tokenizer
            _m["tok"] = Tokenizer.from_file(fetch("tokenizer.json"))
        ids = _m["tok"].encode(t).ids[:77]
        sess = model("text"); feed = {}
        for i in sess.get_inputs():
            feed[i.name] = np.array([ids], np.int64) if "ids" in i.name else np.ones((1, len(ids)), np.int64)
        e = next(v for v in sess.run(None, feed) if v.ndim == 2)[0]
        _te[t] = e / np.linalg.norm(e)
    return _te[t]


def frames(c, every=1.0, most=30):
    """(times, RGB frames) a clip shows, upright and without letterbox bars (the render's own framing)."""
    import Lab_Render
    f = os.path.join(LIB, c["file"]); d = float(c.get("duration") or 0)
    if not os.path.exists(f) or d <= 0: return [], []
    step = max(every, d / most); fr = Lab_Render.framing(c); vf = []
    if fr["crop"]: vf.append("crop=%d:%d:%d:%d" % fr["crop"])
    if fr["turn"]: vf.append("transpose=2")
    vf += ["fps=1/%g" % step, "scale=224:224:force_original_aspect_ratio=increase", "crop=224:224"]   # CLIP sees the centre square
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % (step / 2), "-i", f, "-an", "-vf", ",".join(vf), "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=300)
    w = h = 224; n = len(p.stdout) // (w * h * 3)
    ims = [np.frombuffer(p.stdout, np.uint8, w * h * 3, i * w * h * 3).reshape(h, w, 3) for i in range(n)]
    return [round(step / 2 + i * step, 2) for i in range(n)], ims


def read_store():
    if not os.path.exists(STORE): return dict(keys=[], times=[], emb=np.zeros((0, 512), np.float16), seen={})
    z = np.load(STORE, allow_pickle=False)
    return dict(keys=list(z["keys"]), times=list(z["times"]), emb=z["emb"], seen=json.loads(str(z["seen"])),
                stale="v" not in z.files or str(z["v"]) != EMBED_V)   # made another way: search still works, --build redoes it


def build(external=True):
    import cv2
    s = read_store(); clips = {**load("_lab/lab_index.json"), **load("_reference/clip_index.json")}
    if s.get("stale"): s = dict(keys=[], times=[], emb=np.zeros((0, 512), np.float16), seen={}); print("  the index was made another way: rebuilding all of it")
    pins_p = os.path.join(LIB, "_stills/pinterest/index.json"); pins = json.load(open(pins_p)) if os.path.exists(pins_p) else {}
    todo = [(k, v) for k, v in clips.items() if v.get("use") not in ("no", "removed") and v.get("file")
            and s["seen"].get(k) != "%s|%s" % (v["file"], v.get("duration")) and (external or not v["file"].startswith("_external content/"))]
    todo += [("pin:" + k, v) for k, v in pins.items() if isinstance(v, dict) and v.get("use") not in ("no", "removed")
             and s["seen"].get("pin:" + k) != k]
    ok = {k for k, v in clips.items() if v.get("use") not in ("no", "removed") and v.get("file")} | \
         {"pin:" + k for k, v in pins.items() if isinstance(v, dict) and v.get("use") not in ("no", "removed")}
    keep = [i for i, k in enumerate(s["keys"]) if k not in {t[0] for t in todo} and k in ok]   # removed / "no" clips drop out
    for k in set(s["keys"]) - ok: s["seen"].pop(k, None)
    keys = [s["keys"][i] for i in keep]; times = [s["times"][i] for i in keep]; embs = [s["emb"][keep].astype(np.float32)]
    for n, (k, v) in enumerate(todo, 1):
        try:
            if k.startswith("pin:"):
                im = cv2.imread(os.path.join(LIB, "_stills/pinterest", k[4:]))
                if im is None: continue
                ts, ims = [0.0], [cv2.cvtColor(im, cv2.COLOR_BGR2RGB)]
            else:
                ts, ims = frames(v)
            if not ims: continue
            embs.append(embed_images(ims)); keys += [k] * len(ts); times += ts
            s["seen"][k] = k[4:] if k.startswith("pin:") else "%s|%s" % (v["file"], v.get("duration"))
        except Exception as e:
            print("  skipped %s: %s" % (k, e))
        if n % 25 == 0 or n == len(todo): print("  %d/%d" % (n, len(todo)), flush=True)
    e = np.concatenate(embs) if embs else np.zeros((0, 512), np.float32)
    buf = io.BytesIO()   # written whole: a search reading it mid-write would get half a file
    np.savez_compressed(buf, keys=np.array(keys), times=np.array(times, np.float32), emb=e.astype(np.float16), seen=json.dumps(s["seen"]), v=EMBED_V)
    write_atomic(STORE, buf.getvalue())
    print("visual index: %d frames from %d clips and pins" % (len(keys), len(set(keys))))


def search(q, fmt=None, top=25, tags=(), pins=False):
    s = read_store()
    if not s["keys"]: sys.exit("no visual index yet: python3 _scripts/visual_search.py --build")
    clips = {**load("_lab/lab_index.json"), **load("_reference/clip_index.json")}
    sc = s["emb"].astype(np.float32) @ embed_text(q); best = {}
    for k, t, v in zip(s["keys"], s["times"], sc):
        if k.startswith("pin:") and not pins: continue
        if k not in best or v > best[k][0]: best[k] = (float(v), float(t))
    want = {"9:16": "vertical", "16:9": "horizontal"}.get(fmt)
    rows = []
    for k, (v, t) in sorted(best.items(), key=lambda kv: -kv[1][0]):
        c = clips.get(k, {})
        if want and c and c.get("orientation") not in (want, "square-ish"): continue
        if tags and not set(tags) <= set(c.get("tags") or []): continue
        rows.append("%.3f  %-44s %s  %s" % (v, k[:44], "        " if k.startswith("pin:") else "@%6.1fs" % t, (c.get("line") or "")[:110]))
        if len(rows) >= top: break
    print("\n".join(rows))


# Like-for-like checks by concept tag (concept_tags.json). Each tag here is one picture of its group; a shot flags when
# another picture in the same group fits it clearly better: "golden-hour" with a shot that reads as plain daylight
# (R06_1_23), "road" with a shot that reads as a sidewalk (R06_1_18). Free words (a caption) only get the outlier test.
GROUPS = [{"golden-hour": "a photo taken at golden hour in warm low sunlight", "daylight": "a photo taken in bright midday daylight",
           "blue-hour": "a photo taken at blue hour twilight", "night": "a photo taken at night with artificial lights"},
          {"road": "a photo from inside a car driving on the road", "city": "a person walking on a city sidewalk",
           "beach": "a photo of a beach and the sea", "home": "a photo taken indoors in a room",
           "concert": "a crowd at a concert or club", "club": "a crowd at a concert or club", "nature": "a landscape of fields, hills or forest"}]
MARGIN = 0.01   # how much better another picture must fit before a shot flags


def check(paths, q=None, frames=None):
    """frames: pictures a caller already decoded, {(source file, round(t, 3)): BGR frame} (the render check reads the same
    middle frames): taken as they are; the rest are read here."""
    import cv2
    clips = {**load("_lab/lab_index.json"), **load("_reference/clip_index.json")}; flagged = 0; frames = frames or {}
    for p in paths:
        r = json.load(open(p))
        own = [t for t in r.get("tags") or [] if any(t in g for g in GROUPS)]   # the edit's light / place, if its recipe names them
        words = q or ",".join(own) or r.get("caption") or r.get("story") or ""
        if not words: continue
        tags = [t.strip() for t in words.split(",")] if all(any(t.strip() in g for g in GROUPS) for t in words.split(",")) else []
        te = embed_text(" and ".join(next(g[t] for g in GROUPS if t in g) for t in tags) if tags else words); rows = []
        pairs = []   # (the picture a tag names, the other pictures in its group)
        for t in tags:
            g = next(g for g in GROUPS if t in g)
            pairs.append(((g[t], embed_text(g[t])), [(c, embed_text(c)) for k, c in g.items() if c != g[t]]))
        def vs(ie):   # the strongest like-for-like alternative that beats the tagged picture, or None
            worst = None
            for (mc, me), rest in pairs:
                m = float(ie @ me)
                for c, e in rest:
                    d = float(ie @ e) - m
                    if d > MARGIN and (worst is None or d > worst[0]): worst = (d, c)
            return worst
        ims = []   # (label, RGB picture): read in parallel, embedded in one batch
        if r.get("kind") == "video":
            import Lab_Render
            from concurrent.futures import ThreadPoolExecutor
            from shot_check import frame_at
            def shot(i_sh):
                i, sh = i_sh; c = clips.get(sh["clip"])
                if not c: return None
                src = os.path.join(LIB, c["file"]); t = sh["in"] + sh["dur"] * sh.get("speed", 1.0) / 2
                fr = frames.get((src, round(t, 3)))
                if fr is None: fr = frame_at(src, t)
                if fr is None: return None
                f = Lab_Render.framing(c)
                if f["crop"]: w, h, x, y = f["crop"]; fr = fr[y:y + h, x:x + w]
                if f["turn"]: fr = cv2.rotate(fr, cv2.ROTATE_90_COUNTERCLOCKWISE)
                return ("shot %d %s @%.2f" % (i + 1, sh["clip"][:30], sh["in"]), cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            with ThreadPoolExecutor(min(8, os.cpu_count() or 4)) as ex: ims = [x for x in ex.map(shot, enumerate(r["shots"])) if x]
        else:
            for i, st in enumerate(r.get("stills") or []):
                im = cv2.imread(os.path.join(LIB, st["file"]))
                if im is not None: ims.append(("still %d %s" % (i + 1, os.path.basename(st["file"])[:36]), cv2.cvtColor(im, cv2.COLOR_BGR2RGB)))
        if ims:
            for (n, _), ie in zip(ims, embed_images([im for _, im in ims])): rows.append((n, float(ie @ te), vs(ie)))
        if len(rows) < 3: continue
        med = float(np.median([v for _, v, _ in rows]))
        low = [(n, v, o) for n, v, o in rows if o or v < med - 0.035]
        print("%s  \"%s\"  median %.3f%s" % (r.get("id"), words[:50], med, "" if low else "  ok"))
        for n, v, o in low:
            print("   !! %s %s" % (n, "reads more as \"%s\"" % o[1][len("a photo "):] if o else "looks least like it (%.3f vs %.3f)" % (v, med))); flagged += 1
    return flagged


if __name__ == "__main__":
    a = sys.argv[1:]
    def opt(k, d=None):
        if k in a: i = a.index(k); v = a[i + 1]; del a[i:i + 2]; return v
        return d
    if not a or a[0] in ("-h", "--help"): sys.exit(__doc__)
    if a[0] == "--build": build("--no-external" not in a)
    elif a[0] == "--check":
        x = a[1]
        ps = [x] if os.path.isfile(x) else round_files(x) or [x]   # a round by path, name or number: its recipes, wherever it sits in _lab/rounds
        check(ps, a[2] if len(a) > 2 else None)
    else:
        fmt = opt("--format"); top = int(opt("--top", 25)); tags = [t for t in (opt("--tag") or "").split(",") if t]
        pins = "--pins" in a; a = [x for x in a if x != "--pins"]
        search(" ".join(a), fmt, top, tags, pins)
