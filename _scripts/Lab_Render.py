#!/usr/bin/env python3
"""Lab renderer: turns a lab recipe into a finished low-res mp4 to watch on a phone.
Usage: python3 _scripts/Lab_Render.py <recipe.json> [...]      (from the sandbox folder; paths are relative to library/)
Recipe:
  {"id": "R01_<account>_03", "account": "<account id>", "kind": "video"|"collage", "format": "9:16"|"16:9",
   "song": null | {"name": "<song>", "window": "first chorus", "start": 42.5},   # song under it (songs.py; no name = the first song;)
             optional "file": "_audio/sounds/x.m4a" (another sound, e.g. one pulled from a TikTok by Sound_Pull.py) and
             "lyrics_timing": "_audio/sounds/x_words.json" (word timings for that sound); default: the song in _audio/song.json
   "caption": "on-screen text or empty",
   "lyrics": true | "<layer>",                    # the song's words as they are sung: word by word (lyric_frames), or one of the
                                                  # song's own lyric layers by name, laid over as it is (lyric_layer; only when asked for)
   "shots":  [{"clip": "<clip_index key>", "in": 3.2, "dur": 1.0, "speed": 1.0,      # kind=video
               "center": "face",              # optional: where the fill crop sits (framing(): "face" | "subject" | [x, y])
               "flash": 3, "punch": 1.12}],   # optional effects on a shot (FX below)
   "grade": "warm"|"cool"|"punch"|"matte", "grain": 8,                                 # optional effects on the whole edit
   "blend": 2.0, "blend_style": "light"|"fade"|"white"|…,                             # gradual blends between shots (BLENDS; a shot's own "blend" overrides)
   "layers": [{"clip": k, "mode": "screen", "opacity": 0.5}], "sparkles": {...},       # other clips blended over it; animated stars (layer_pass, sparkles_pass)
   "sparkles": {"style": "motes", "look": "air", "count": 16000, "opacity": 1.0, "haze": 0.5}   # or dust, by "style": "dust" | "gold" | "motes" (dust_pass, gold_pass,
                                                  # motes_pass); the motes' "look": "air" is dust that fills the air and lets the light through ("opacity": each speck's, "haze": the veil in the light)
   shot "reverse": true                                                               # the shot plays backwards
   "doodles": [{"fx": "trace", "from": 2.0, "to": 3.0}, {"fx": "hearts", "from": 9.88, "to": 10.3}]   # hand-drawn animation on the people (doodles.py)
   "pixels": true | 2.0 | "sort" | {"style": "mosh", "fly": 3.4, "decay": 1.7, "streak": 1.0}   # at every cut the picture's own pixels carry it into the next one (pixels_of, pixel_flight; video or collage),
                                                  # for "fly" seconds. "style": "mosh" (a video: the old picture dragged by the new shot's motion, "streak" how much harder,
                                                  # settling into it over "decay" seconds; a video's style when none is named), "sort" (the picture runs into streaks of its own
                                                  # light, "size" pixels across, "wind": "down" to run downward; a collage's when none is named) or "sand" (flying grains: only by name)
   "stills": [{"file": "_stills/candidates/x.jpg", "rotate": false}], "sec_per_still": 0.5}   # kind=collage
Clips are looked up in _reference/clip_index.json, then _lab/lab_index.json. Sideways clips are turned upright,
letterbox bars are cropped off, and every shot is cropped to fill the frame, from the middle or around its "center" (the
people in it, found with Apple Vision: "face"; faces, else people, else what the eye goes to: "subject"; or [x, y] as
fractions of the picture) (framing(): shared with Resolve_Export.py, so anything the render does to a shot must be
carried into the Resolve version too). Output: _lab/rounds/<round>/<id>.mp4
(1080x1920 or 1920x1080, 30 fps). A song's tempo, markers and windows are its own (songs.py, song_markers.py); cuts land on its
beats (beat_map.py).
FX (effects; the render may do what free Resolve can't.
    The Resolve version marks each one, saying what the render did; for colour or effects there, import the rendered mp4):
  shot "flash": n    the shot opens on white and clears over n frames (a flash on the cut: put it on a kick)
  shot "punch": z    the shot opens zoomed in z times (1.05-1.25) and settles to normal over 0.3 s (a hit)
  edit "grade": one of GRADES (a colour look over every shot); edit "grain": 1-20 (film grain, same on every shot)
  edit "doodles": hand-drawn animation on and around the people, Lyrical Lemonade style (doodles.py)
Use them for effect, sparingly: a flash or punch lands on a kick where the song hits; a whole edit of them is noise."""
import os, sys, json, subprocess, tempfile, shutil, hashlib, threading
from PIL import Image, ImageDraw, ImageFont
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); from sandbox_paths import ROOT, LIB  # library/: footage, indexes, lab
import songs as SONGS  # noqa: E402  (the songs being promoted: library/_audio/song.json)
def _song():
    """The current song's master and word timings (what beat_map and setup look at). Recipes use audio_of()."""
    e = SONGS.get()
    return (e["master"], e.get("lyrics_timing") or "") if e else ("", "")
MASTER, LYRICS = _song()


def audio_of(r):
    """(audio file, lyrics timing file) for this recipe: its own sound if it names one ("file"), else its song ("name";
    no name = the first song, so an old edit keeps its song when the current song changes)."""
    m = SONGS.block(r) or {}
    if m.get("file"):
        return os.path.join(LIB, m["file"]), os.path.join(LIB, m["lyrics_timing"]) if m.get("lyrics_timing") else ""
    e = SONGS.of_recipe(r) or {}
    if not e.get("master"): sys.exit("%s is cut to a song, but no song is set up yet (songs.py add, or drop one into the chat)" % r.get("id", "this piece"))
    return e.get("master", ""), os.path.join(LIB, m["lyrics_timing"]) if m.get("lyrics_timing") else e.get("lyrics_timing", "")
FONT = next((p for p in (os.path.join(d, "_assets/fonts/TikTokSans-Variable.ttf") for d in (LIB, ROOT)) if os.path.exists(p)), "")  # TikTok's own typeface (OFL; a copy ships in the repo), set to the in-app "Classic" look
FPS = 30
FADE_OUT = 0.3   # the song fades out over the last 0.3 s (Resolve_Export marks it for the person)
TB = 15360       # the time base of our 30 fps temp mp4s (ffmpeg doubles 30 until past 10000); xfade works out each blend in it

def enc(final=False):
    """Encode settings. Files between passes are lossless and only the finished file is compressed, once: every pass used to
    re-encode the whole edit (2-7 lossy generations; the motes' specks lost 9 dB) and those encodes were most of the render time. Sep 29: a plain edit 10.6 -> 6.5 s, and every kind closer to a lossless render."""
    return ["-c:v", "libx264"] + (["-crf", "20", "-preset", "faster"] if final else ["-qp", "0", "-preset", "ultrafast"]) + ["-pix_fmt", "yuv420p"]

def load_index():
    main = os.path.join(LIB, "_reference/clip_index.json")
    idx = json.load(open(main))["clips"] if os.path.exists(main) else {}
    lab = os.path.join(LIB, "_lab/lab_index.json")
    if os.path.exists(lab): idx = {**json.load(open(lab))["clips"], **idx}
    return idx

def size(fmt): return (1080, 1920) if fmt == "9:16" else (1920, 1080)   # full 1080

def caption_png(text, W, H, path):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        d = ImageDraw.Draw(im); f = ImageFont.truetype(FONT, max(16, int(min(W, H) / 21)))
        try: f.set_variation_by_axes([36, 100, 500, 0])   # opsz, width, weight 500 = Medium, slant
        except Exception: pass
        lines, cur = [], ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if d.textlength(t, font=f) > W * 0.8 and cur: lines.append(cur); cur = w
            else: cur = t
        lines.append(cur); lh = f.size * 1.25; y = H * 0.60 - lh * len(lines) / 2   # 15% lower than centre-ish
        for ln in lines:
            x = (W - d.textlength(ln, font=f)) / 2
            d.text((x, y + 1), ln, font=f, fill=(0, 0, 0, 90)); d.text((x, y), ln, font=f, fill=(255, 255, 255, 255)); y += lh
    im.save(path)

def font(W, H, weight):
    f = ImageFont.truetype(FONT, max(16, int(min(W, H) / 21)))
    try: f.set_variation_by_axes([36, 100, weight, 0])
    except Exception: pass
    return f

LYRIC_Y = 0.60   # where the lyric line sits (its middle, as a fraction of the height): doodles keep clear of it

LAYER_Y = 0.50   # a song's lyric layer sits where its maker put it: the middle
TEXT_W = 0.8     # the share of the width on-screen text keeps to (captions and lyrics wrap there; a lyric layer is fitted into it)

def lyric_layer(r):
    """The lyric layer a recipe asks for by name ("lyrics": "<name>"), or None (true = the word-by-word lyrics below). A layer is
    a lyric video someone made for the whole song, laid over the edit from the same place in the song, only when asked for. It belongs to
    the song: song.json, the song's "lyric_layers": {"<name>": {"file": in library/, "offset": seconds into the file where the
    song starts, "key": "green" (white text on a green ground; without it the file's own transparency is used),
    "text_width": the widest the text gets, in the file's pixels}}."""
    name = r.get("lyrics")
    if not isinstance(name, str): return None
    m = SONGS.block(r) or {}; e = None if m.get("file") else SONGS.of_recipe(r)
    lay = ((e or {}).get("lyric_layers") or {}).get(name)
    if not lay: raise ValueError('%s: "lyrics": "%s", but %s has no lyric layer called that (song.json, its "lyric_layers")'
                                 % (r.get("id", "this piece"), name, (e or {}).get("name") or "its sound"))
    if not os.path.exists(os.path.join(LIB, lay["file"])): raise ValueError("%s: the lyric layer's file is gone: %s" % (r.get("id", "this piece"), lay["file"]))
    if lay.get("key") not in (None, "green"): raise ValueError('lyric layer "%s": "key" is "green" or left out, not %r' % (name, lay["key"]))
    return lay

def layer_frames(r, lay, W, H, dur, tmp):
    """The layer's frames for this edit's stretch of the song, as lyric_frames writes them. It keeps its own size (text made
    for the song isn't blown up), scaled down only to keep its text inside TEXT_W of the width, and sits in the middle.
    key "green": the text is white on a flat green, so red is exactly how much text is at each pixel (white has all of it, the
    ground none): red becomes the transparency and the letters are drawn white, soft edges and glow kept, no green rim.
    Where the file has no picture there is nothing to lay, and a line says so: an edit that starts before the file does gets empty
    frames until it starts, one that runs past its end gets no frames after it (the text stops there), and one that lies wholly
    outside it gets None."""
    k = min(1.0, TEXT_W * W / float(lay["text_width"])) if lay.get("text_width") else 1.0
    fit = (["scale=trunc(iw*%.5f/2)*2:trunc(ih*%.5f/2)*2:flags=lanczos" % (k, k)] if k < 1 else []) + ["crop=min(iw\\,%d):min(ih\\,%d)" % (W, H)]
    pad = "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black" % (W, H)
    if lay.get("key") == "green":
        # the white the letters are drawn in is made from the layer's own frames, so it is only there where the file has a picture (a white source
        # of its own never ends: the edit came out white wherever the file had no frame for it, all of it outside the file and its first frame when
        # the start fell between two of the file's frames, and past the file's end the last words stayed on). start_time: the first frame the seek
        # hands over stands from the edit's first frame; format=gray: the key as it was read (converted on the way, some pixels came out a step off)
        fc = ("[0:v]fps=%d:start_time=0,format=gbrp,extractplanes=r,lut=y=clip((val-8)*255/247\\,0\\,255),%s,%s,format=gray,split[a][b];[b]format=rgba,lutrgb=r=255:g=255:b=255[w];[w][a]alphamerge,format=rgba"
              % (FPS, ",".join(fit), pad))
    else:
        fc = "[0:v]fps=%d,format=rgba,%s,%s@0" % (FPS, ",".join(fit), pad)
    st = SONGS.block(r)["start"]; at = st + float(lay.get("offset", 0)); n = int(round(dur * FPS)); pat = os.path.join(tmp, "lyr_%05d.png")
    lead = min(n, max(0, int(round(-at * FPS))))   # the edit's frames before the file's first one (sought before its start, a green key gave white up to there and the other kind laid its words that much early)
    if lead < n: subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % max(0.0, at), "-i", os.path.join(LIB, lay["file"]),
                                 "-filter_complex", fc, "-frames:v", str(n - lead), "-start_number", str(lead), "-compression_level", "1", pat], check=True, timeout=600)
    end = next((i for i in range(lead, n) if not os.path.exists(pat % i)), n)   # the first frame the file had no picture for
    if end == lead:
        print('  %s: the lyric layer "%s" has no picture for this stretch of the song (%.2f-%.2fs): no lyrics on it' % (r.get("id", "this piece"), r["lyrics"], st, st + dur))
        return None
    if lead or end < n:
        print('  %s: the lyric layer "%s" has a picture only for %.2f-%.2fs of this %.2fs edit (its file starts or ends there): no words on the rest' % (r.get("id", "this piece"), r["lyrics"], lead / FPS, end / FPS, dur))
        for i in range(lead):   # empty frames up to the file's start
            if i: os.link(pat % 0, pat % i)
            else: Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(pat % 0, compress_level=1)
    return pat

def lyric_frames(r, W, H, dur, tmp):
    """Word-by-word lyrics: each word flashes into place (no fade, no bounce) as it is sung; the line's one impact word is bold.
    Writes an RGBA frame per video frame (lyr_00000.png ...) and returns the pattern, or None. "lyrics": "<name>" lays the
    song's lyric layer of that name instead (lyric_layer)."""
    lay = lyric_layer(r)
    if lay: return layer_frames(r, lay, W, H, dur, tmp)
    m0 = SONGS.block(r)["start"]; fr, fb = font(W, H, 500), font(W, H, 800)
    lines = [l for l in json.load(open(audio_of(r)[1]))["lines"]
             if l["end"] - m0 > 0.3 and l["start"] - m0 < dur]
    if not lines: return None
    d0 = ImageDraw.Draw(Image.new("RGBA", (W, H)))
    lay = []
    for l in lines:   # lay out each full line once (wrapped at 80% width), so words never shift as they appear
        sp = d0.textlength(" ", font=fr); rows, cur, cw = [], [], 0
        for w in l["words"]:
            f = fb if w.get("bold") else fr; ww = d0.textlength(w["w"], font=f)
            if cur and cw + sp + ww > W * 0.8: rows.append((cur, cw)); cur, cw = [], 0
            cw += (sp if cur else 0) + ww; cur.append((w, f, ww))
        rows.append((cur, cw)); lh = fr.size * 1.25; y = H * LYRIC_Y - lh * len(rows) / 2; items = []
        for row, rw in rows:
            x = (W - rw) / 2
            for w, f, ww in row: items.append((w["t"] - m0, x, y, w["w"], f)); x += ww + sp
            y += lh
        lay.append((l["start"] - m0, l["end"] - m0, items))
    n = int(round(dur * FPS)); last = None
    for i in range(n):
        t = i / FPS; p = os.path.join(tmp, "lyr_%05d.png" % i)
        on = [(a, sum(t >= wt for wt, *_ in items)) for a, b, items in lay if a - 0.01 <= t < b]   # the lines up and how many words of each
        if on == last: os.link(prev, p); continue   # nothing new since the frame before: the same picture, linked (a word lands ~30 times in 280 frames)
        last = on; im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        for a, b, items in lay:
            if not (a - 0.01 <= t < b): continue
            for wt, x, y, txt, f in items:                                   # each word flashes into place on its sung frame (no fade, no bounce)
                if t < wt: continue
                d.text((x, y + 1), txt, font=f, fill=(0, 0, 0, 90)); d.text((x, y), txt, font=f, fill=(255, 255, 255, 255))
        im.save(p, compress_level=1); prev = p
    return os.path.join(tmp, "lyr_%05d.png")

def framing(c, s=None, W=0, H=0, span=None):
    """How a clip is framed. The ONE place for it: the render (vf_for) and the Resolve export (Resolve_Export.py) both
    read this, so a Resolve timeline always matches the render. A new framing option goes here, never in only one of them.
    crop: the picture inside letterbox bars (w, h, x, y) or None; turn: 90 = turn sideways footage upright (counter-clockwise).
    Then both fill the W x H frame with what's left: from its middle, or, for a shot (or layer) s with a "center", around
    that (the centre crop cut the faces a shot was chosen for: R07, "an arm and the sea"): [x, y] as fractions of the picture
    after the crop and the turn, "face" or "subject" (subject_center: the frames the shot shows over span source seconds;
    nothing found = from the middle, as ever). The frame never leaves the picture. center: None (the middle: today's crop,
    the same frames), or the middle of the frame the render shows, as fractions of that picture; fill: (x, y) where the
    render's fill crop starts in the scaled picture."""
    crop = tuple(int(v) for v in c["active_area"]) if c.get("letterboxed") and c.get("active_area") else None
    fr = dict(crop=crop, turn=90 if "sideways_footage" in (c.get("flags") or []) else 0, center=None, fill=None)
    want = (s or {}).get("center")
    if want is None or not (W and H): return fr
    pw, ph = picture_size(c, fr)
    sw, sh = max(W, (2 * H * pw + ph) // (2 * ph)), max(H, (2 * W * ph + pw) // (2 * pw))   # the picture scaled to fill, rounded as ffmpeg's scale does
    if want not in ("face", "subject") and not (isinstance(want, (list, tuple)) and len(want) == 2):
        raise ValueError('shot "center": "face", "subject" or [x, y], not %r' % (want,))
    if isinstance(want, str):
        want = subject_center(c, s, span if span is not None else float(s["dur"]) * float(s.get("speed", 1.0)), (W / sw, H / sh), want)
        if not want: return fr
    x, y = [min(max(0, int(round(float(v) * n - m / 2))), n - m) // 2 * 2 for v, n, m in zip(want, (sw, sh), (W, H))]   # even: ffmpeg's crop rounds 4:2:0 so
    if (x, y) == (round((sw - W) / 2) // 2 * 2, round((sh - H) / 2) // 2 * 2): return fr   # where the centre crop is anyway: today's filter
    fr.update(center=((x + W / 2) / sw, (y + H / 2) / sh), fill=(x, y))
    return fr

def picture_size(c, fr):
    """(w, h) of clip c's picture after framing fr's crop and turn (the index's size is the upright one, as ffmpeg shows it)."""
    w, h = fr["crop"][:2] if fr["crop"] else (c.get("width"), c.get("height"))
    if not (w and h):
        v = (json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_side_data=rotation",
                                        "-of", "json", os.path.join(LIB, c["file"])], capture_output=True, text=True).stdout or "{}").get("streams") or [{}])[0]
        w, h = v["width"], v["height"]
        if any(abs(int(float(d.get("rotation", 0)))) % 180 == 90 for d in v.get("side_data_list") or []): w, h = h, w
    return (h, w) if fr["turn"] else (w, h)

def span_of(r, i):
    """Source seconds shot i shows: its length plus the tail it runs on under the next shot (run_on), times its speed."""
    s = r["shots"][i]; return (float(s["dur"]) + run_on(r, i)) * float(s.get("speed", 1.0))

def run_on(r, i):
    """Seconds shot i plays on after its cut, under the shot that follows: that shot's blend, or the flight of a pixel morph
    (the outgoing shot keeps playing while its pixels leave: pixel_shots). 0 for the last shot and across a hard cut."""
    if i + 1 >= len(r["shots"]): return 0
    px = pixels_of(r); return px["tail"] if px else blend_in(r, i + 1)

# ---- where the people are, for a shot's "center" (Apple Vision, through vision_track.swift: the doodles' helper) ----
VISION_V = 1        # what's kept per frame; a new number starts the cache afresh
FACE_MIN = 0.05     # a face under this share of the picture's height is someone in the background, not who the shot is about
BEHIND = 0.5        # a face under half as tall as the biggest in its look is someone behind (about twice as far from the lens); from
                    # half up it's two people, and the planner says who the shot is about. R06 + R07, 81 looks with two faces: the
                    # five under half were all someone in the background; at 0.51 one of each; from 0.6 up all two people
_VLOCK, _VBUILD, _VRUN, _NO_VISION = threading.Lock(), threading.Lock(), threading.BoundedSemaphore(2), []   # one cache writer, one build; two Vision runs at most (renders run three at once)

def look_times(s, span):
    """The source times Vision looks at: the shot's in, middle and out frames (a reversed shot shows the same ones; one that runs
    on shows the stretch before its "in" for that: render_video)."""
    a = float(s.get("in", 0)); back = span - float(s["dur"]) * float(s.get("speed", 1.0)) if s.get("reverse") else 0
    if back > 1e-9: b = a + span - back; a = max(0.0, a - back); span = b - a
    return (round(a + min(0.02, span / 2), 3), round(a + span / 2, 3), round(a + max(span / 2, span - 0.04), 3))

def looks(c, ts):
    """What Vision sees in clip c's picture (after the crop and the turn) at each source time in ts: {"faces": [[x, y, w, h,
    share of the face on a person], ...], "people": [x, y, w, h] | None, "salient": [[x, y, w, h], ...]} as fractions of the
    picture, or None (no frame there, or no Vision on this machine). Worked out once per frame and remembered in
    library/_lab/_vision/framing/ (keyed on the file, its size and date, crop and turn): the render, the Resolve export and
    shot_verify.py read the same answer, and a second render costs nothing."""
    fr = framing(c); src = os.path.join(LIB, c["file"])
    if not os.path.exists(src): return [None] * len(ts)   # gone (clips get deleted while sessions run): nothing to frame on
    st = os.stat(src); key = hashlib.sha1(("%s|%d|%d|%s|%d|%d" % (c["file"], st.st_size, int(st.st_mtime), fr["crop"], fr["turn"], VISION_V)).encode()).hexdigest()[:16]
    path = os.path.join(LIB, "_lab", "_vision", "framing", key + ".json"); ms = ["%d" % round(t * 1000) for t in ts]
    def load():
        try: return json.load(open(path))["frames"]
        except (OSError, ValueError, KeyError): return {}
    got = load(); need = sorted({t for t, m in zip(ts, ms) if m not in got})
    if need and not _NO_VISION:
        new = _vision(src, fr, picture_size(c, fr), need)
        if new:
            with _VLOCK:
                got = {**load(), **new}; os.makedirs(os.path.dirname(path), exist_ok=True); tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
                json.dump({"clip": c["file"], "frames": got}, open(tmp, "w")); os.replace(tmp, path)
    return [got.get(m) for m in ms]

def _vision(src, fr, pic, ts):
    """vision_track on the frames at ts, cropped and turned like the render: {ms: look}. One run for all of them (each run
    costs about half a second to start)."""
    try:
        with _VBUILD: import doodles; exe = doodles.helper()   # builds it the first time (swiftc; macOS only), once when three renders ask together
    except Exception as e:
        _NO_VISION.append(1); print("  no Apple Vision here (%s): shots with a \"center\" are framed from the middle" % e); return {}
    k = 960 / max(pic); vw, vh = max(2, int(pic[0] * k / 2) * 2), max(2, int(pic[1] * k / 2) * 2)
    vf = ",".join((["crop=%d:%d:%d:%d" % fr["crop"]] if fr["crop"] else []) + (["transpose=2"] if fr["turn"] else []) + ["scale=%d:%d" % (vw, vh)])
    out, frames, at = {"%d" % round(t * 1000): None for t in ts}, [], []
    for t in ts:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t, "-i", src, "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, timeout=120).stdout
        if len(raw) == vw * vh * 3: frames.append(raw); at.append(t)
    if not frames: return out
    d = tempfile.mkdtemp(prefix="vis_")
    try:
        # each frame coded on its own at a fixed quality, so Vision sees the same pixels whatever frames it's batched with (with ordinary
        # compression a frame came out a little different beside other frames, and so did its faces: a shot framed a few pixels apart)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (vw, vh), "-framerate", str(FPS), "-i", "-",
                        "-c:v", "libx264", "-qp", "10", "-g", "1", "-bf", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p", os.path.join(d, "f.mp4")],
                       input=b"".join(frames), check=True, timeout=120)
        with _VRUN: subprocess.run([exe, os.path.join(d, "f.mp4"), d, "--mask-width", "160", "--saliency"], check=True, capture_output=True, timeout=600)
        for line in open(os.path.join(d, "track.jsonl")):
            row = json.loads(line); n = row["n"]
            if n >= len(at): continue
            m = cv2.imread(os.path.join(d, "mask_%06d.png" % n), 0); m = (m > 127) if m is not None else np.zeros((1, 1), bool); mh, mw = m.shape
            def on(b):   # how much of the face's middle the person mask covers: a face on a poster, a screen or a wall isn't a person there
                p = m[max(0, int((b[1] + b[3] / 4) * mh)):max(0, int(np.ceil((b[1] + b[3] * 3 / 4) * mh))), max(0, int((b[0] + b[2] / 4) * mw)):max(0, int(np.ceil((b[0] + b[2] * 3 / 4) * mw)))]
                return round(float(p.mean()), 3) if p.size else 0.0
            ys, xs = np.nonzero(m)
            out["%d" % round(at[n] * 1000)] = dict(faces=sorted(f["box"] + [on(f["box"])] for f in row.get("faces") or []), salient=row.get("salient") or [],   # (Vision lists them in any order)
                                                   people=[round(float(v), 4) for v in (xs.min() / mw, ys.min() / mh, (xs.max() + 1 - xs.min()) / mw,
                                                                                         (ys.max() + 1 - ys.min()) / mh)] if len(xs) > 0.005 * m.size else None)
    except (subprocess.SubprocessError, OSError) as e:   # remembered as nothing found, so the render and the export still agree
        print("  Vision couldn't read %s (%s): framed from the middle (library/_lab/_vision/framing/ keeps that; delete its file there to try again)"
              % (os.path.basename(src), e)); return {m: None for m in out}
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out

def main_faces(look, seen=()):
    """The faces a shot is about, in one look: on a person (Vision's person mask covers it: not a poster, a screen or a false
    face in a blur), or where a face that is on a person sits in another look of the shot (seen: the mask misses some dark
    close-ups; a false face doesn't come back), at least FACE_MIN of the picture's height, and at least BEHIND as tall as the
    biggest (not the people far behind). [x, y, w, h] each, cut to the picture."""
    over = lambda f, g: max(0, min(f[0] + f[2], g[0] + g[2]) - max(f[0], g[0])) * max(0, min(f[1] + f[3], g[1] + g[3]) - max(f[1], g[1]))
    fs = [f for f in (look or {}).get("faces") or [] if f[3] >= FACE_MIN and (f[4] >= 0.3 or any(over(f, g) > 0.3 * (f[2] * f[3] + g[2] * g[3] - over(f, g)) for g in seen))]
    top = max((f[3] for f in fs), default=0); out = []
    for f in (f for f in fs if f[3] >= BEHIND * top):
        x0, y0, x1, y1 = max(0, f[0]), max(0, f[1]), min(1, f[0] + f[2]), min(1, f[1] + f[3])
        if x1 > x0 and y1 > y0: out.append([x0, y0, x1 - x0, y1 - y0])
    return out

def shot_faces(L):
    """main_faces in each look of a shot (looks(c, look_times(...)))."""
    on = [[f for f in (l or {}).get("faces") or [] if f[4] >= 0.3] for l in L]
    return [main_faces(l, [g for j, fs in enumerate(on) if j != i for g in fs]) for i, l in enumerate(L)]

def union(bs):
    """The box round boxes [x, y, w, h], as (x0, y0, x1, y1); None for none."""
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[0] + b[2] for b in bs), max(b[1] + b[3] for b in bs)) if bs else None

def held(spans, a, win):
    """How well a frame starting at a (win long; fractions of the picture along one side; a may be an array) holds spans
    [(lo, hi)]: the worst one's share inside it, out of what any frame that size could hold of it (a face bigger than the
    frame is held when it fills it)."""
    a = np.asarray(a, float)[..., None]; lo, hi = np.array(spans, float).T; w = np.maximum(hi - lo, 1e-9)
    return (np.clip(np.minimum(hi, a + win) - np.maximum(lo, a), 0, None) / w / np.minimum(1, win / w)).min(-1)

def hold(spans, win):
    """The middle (along one side) of the frame that holds spans best (held): the middle of the positions that do it equally
    well, so a face that fits is centred and one that moves further than the frame is cut the least at its worst."""
    if win >= 1 - 1e-9: return 0.5
    g = np.linspace(0, 1 - win, 801); sc = held(spans, g, win); ok = g[sc >= sc.max() - 1e-9]
    return float((ok.min() + ok.max()) / 2 + win / 2)

def subject_center(c, s, span, win, mode="face"):
    """The middle (x, y, fractions of the picture) of the frame for shot s, or None (framed from the middle). win: the frame's
    size as fractions of the picture. face: the main faces over its in, middle and out frames: all of them when they fit
    together; one person (never two main faces in one look) held as well as a fixed frame can when they move. Two people
    whose faces don't fit together: None. Vision frames on people, it never decides between them: who the shot is about is
    a taste call (R07_1_02: the girl grinning in sunglasses, not the bigger face beside her), so the planner says it with
    "center": [x, y] (shot_verify.py gives each person's). subject: faces, else the people (Vision's person mask), else
    where the eye goes (saliency)."""
    L = looks(c, look_times(s, span)); F = shot_faces(L); bs = [b for fs in F for b in fs]; u = union(bs)
    if u:
        if (u[2] - u[0] > win[0] + 1e-6 or u[3] - u[1] > win[1] + 1e-6) and any(len(fs) > 1 for fs in F): return None
        return tuple(hold([(b[k], b[k] + b[k + 2]) for b in bs], win[k]) for k in (0, 1))
    if mode != "subject": return None
    u = union([l["people"] for l in L if l and l.get("people")]) or union([b for l in L if l for b in l.get("salient") or []])
    return ((u[0] + u[2]) / 2, (u[1] + u[3]) / 2) if u else None

def look_ahead(r, idx):
    """Vision on every "face" / "subject" shot of r before the render asks one at a time: one run per clip, not per shot."""
    by = {}
    for i, s in enumerate(r.get("shots") or []):
        if isinstance(s.get("center"), str) and s["clip"] in idx: by.setdefault(s["clip"], []).extend(look_times(s, span_of(r, i)))
    for k, ts in by.items(): looks(idx[k], ts)

GRADES = {   # name: (ffmpeg chain, what it does in words: the Resolve marker). None may darken the picture: each keeps the plain cut's average brightness.
    "warm": ("colorbalance=rs=0.06:gs=0.01:bs=-0.06:rm=0.04:bm=-0.04,eq=saturation=1.08", "warmer: shadows and mids pushed to orange, saturation +8%"),
    "cool": ("colorbalance=rs=-0.05:bs=0.06:rm=-0.03:bm=0.05,eq=brightness=0.015", "cooler: shadows and mids pushed to blue"),
    "punch": ("eq=contrast=1.12:brightness=0.04:saturation=1.18", "contrast +12%, saturation +18%, brightness kept"),
    "matte": ("curves=all='0/0.06 1/0.94',eq=saturation=0.9", "lifted blacks and lowered whites (a matte film look), saturation -10%"),
}
PUNCH_S = 0.3

def fx_for(s, r):
    """The effect filters for one shot of recipe r (after framing): punch, grade, grain, flash."""
    f = []
    z = float(s.get("punch") or 0)
    if z > 1:   # zoom in z times at the cut, easing back to the frame over PUNCH_S (the frame is already W x H here)
        f += ["scale=w='iw*(1+%g*pow(max(0\\,1-t/%g)\\,2))':h=-2:eval=frame" % (z - 1, PUNCH_S), "crop=%s:%s" % ("{W}", "{H}")]
    if r.get("grade"):
        if r["grade"] not in GRADES: raise ValueError("grade %r: one of %s" % (r["grade"], ", ".join(GRADES)))
        f.append(GRADES[r["grade"]][0])
    if r.get("grain"): f.append("noise=alls=%d:allf=t" % grain_of(r))
    if s.get("flash"): f.append("fade=t=in:st=0:n=%d:color=white" % int(s["flash"]))
    return f

def grain_of(r):
    """The ffmpeg noise strength for r["grain"]: any grain asked for shows, at least 1, the lightest (0.3 used to round to none).
    Resolve_Export's grain marker should say this number too."""
    return max(1, int(r["grain"]))

def vf_for(c, W, H, speed, s=None, r=None, span=None, fr=None):
    fr = fr or framing(c, s, W, H, span); f = []   # span: the source seconds the shot shows (span_of), for a "center" found by Vision
    if fr["crop"]: f.append("crop=%d:%d:%d:%d" % fr["crop"])
    if fr["turn"]: f.append("transpose=2")
    f += ["scale=%d:%d:force_original_aspect_ratio=increase" % (W, H), "crop=%d:%d:%d:%d" % ((W, H) + fr["fill"]) if fr["fill"] else "crop=%d:%d" % (W, H), "setsar=1"]
    if abs(speed - 1) > 1e-3: f.append("setpts=PTS/%g" % speed)
    f.append("fps=%d" % FPS)
    if s is not None: f += [x.replace("{W}", str(W)).replace("{H}", str(H)) for x in fx_for(s, r or {})]
    return ",".join(f)

BLENDS = {   # how one shot melts into the next (ffmpeg xfade). "light" = a double exposure (the two screened together) that changes
            # evenly across the WHOLE blend and holds brightness mid-way (the first version reached the new shot halfway, so every
            # "light" blend looked half as long as asked; measured since)
    "fade": "fade", "light": "custom:expr='255-(255-A*pow(P\\,0.75))*(255-B*pow(1-P\\,0.75))/255'", "dissolve": "dissolve", "blur": "hblur", "zoom": "zoomin",
    "white": "fadewhite", "black": "fadeblack"}   # white = a burn-out through white between shots

_BLEND = {}
def blend_in(r, i):
    """_blend_in, remembered: a "long" blend reads the indexes and decodes its clip, and the render asked 7 times per edit."""
    k = (json.dumps([r["shots"], r.get("blend"), SONGS.block(r)], sort_keys=True), i)
    if k not in _BLEND: _BLEND[k] = _blend_in(r, i)
    return _BLEND[k]

def _blend_in(r, i):
    """Seconds shot i (i >= 1) blends in from the shot before: its own "blend", else the edit's (0 = a hard cut).
    "long": at least a whole note (4 beats at the song's tempo; 2 s when silent), or 80% of the incoming shot
    if that's longer, never past the incoming shot's own length, and capped so the outgoing shot never runs past a cut in its
    own clip (a warning says when a cut makes it shorter than a whole note: move that shot)."""
    s = r["shots"][i]; b = s.get("blend", r.get("blend") or 0)
    if b != "long": return float(b)
    e = SONGS.of_recipe(r) if SONGS.block(r) else None; whole = 4 * 60.0 / float((e or {}).get("bpm") or 120)
    prev = r["shots"][i - 1]; want = min(max(whole, 0.8 * float(s["dur"])), float(s["dur"]))
    try:
        c = load_index()[prev["clip"]]; end = prev["in"] + prev["dur"] * prev.get("speed", 1.0)
        from shot_check import cuts_in
        if prev.get("reverse"):   # it runs on backwards, into the stretch before its "in" (render_video): the cut in its way is the last one there
            cut = cuts_in(os.path.join(LIB, c["file"]), max(0.0, prev["in"] - want - 0.2), prev["in"])
            room = min((prev["in"] - max(cut) - 2 / FPS) if cut else want, prev["in"] - 0.1)
        else:
            cut = cuts_in(os.path.join(LIB, c["file"]), end, end + want + 0.2)
            room = (min(cut) - end - 2 / FPS) if cut else want
            if c.get("duration"): room = min(room, float(c["duration"]) - end - 0.1)
        got = round(max(0.3, min(want, room)), 3)
        if got < min(whole, float(s["dur"])) - 0.05: print("  blend into shot %d of %s: only %.2fs before a cut in %s (a whole note is %.2fs): move that shot" % (i + 1, r.get("id"), got, prev["clip"][:28], whole))
        return got
    except Exception:
        return round(want, 3)

def render_video(r, W, H, tmp, idx):
    parts = []; look_ahead(r, idx)
    for i, s in enumerate(r["shots"]):
        c = idx[s["clip"]]; src = os.path.join(LIB, c["file"]); sp = s.get("speed", 1.0)
        extra = run_on(r, i)   # a shot that blends out (or whose pixels fly into the next) runs on under the next one
        n = int(round((s["dur"] + extra) * FPS)); out = os.path.join(tmp, "s%02d.mp4" % i); a = s["in"]; held = 0
        if s.get("reverse") and extra:   # backwards, the shot's own time is still in..in+dur (what its Resolve timeline places) and the run-on carries on
            a = max(0.0, s["in"] - extra * sp)   # past "in": read from that much earlier. A clip with nothing there holds its first frame
            held = int(round((extra * sp - (s["in"] - a)) / sp * FPS))
        # -t counts the output's seconds, and the output is dur + extra long whatever the speed: at (dur + extra) x speed + 0.5 a slow shot
        # longer than 0.5 / (1 - speed) s came out short and the render was refused
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % a, "-i", src, "-t", "%.3f" % ((s["dur"] + extra) * max(sp, 1) + 0.5),
                        "-vf", vf_for(c, W, H, sp, s, r, span_of(r, i)), "-frames:v", str(n - held), "-an"] + enc() + [out], check=True, timeout=120)   # read-ahead on reversed shots too (without it one came out a frame short when "in" fell between frames)
        if s.get("reverse"):   # the shot plays backwards: reversed from the rendered copy (reversing straight from some
            rev = out[:-4] + "_rev.mp4"   # sources, e.g. HEVC phone video, gives black frames)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", out, "-vf", "reverse" + (",tpad=stop_mode=clone:stop=%d" % held if held else ""), "-an"] + enc() + [rev], check=True, timeout=180)
            out = rev
        parts.append(out)
    body = os.path.join(tmp, "body.mp4"); total = sum(int(round(s["dur"] * FPS)) for s in r["shots"]) / FPS
    if pixels_of(r): return pixel_shots(r, parts, W, H, total, body), total
    if not any(blend_in(r, i) for i in range(1, len(r["shots"]))):
        lst = os.path.join(tmp, "list.txt"); open(lst, "w").write("".join("file '%s'\n" % p for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", body], check=True, timeout=120)
        return body, total
    # gradual blends: each shot
    # still starts at its own cut time; the one before runs on under it and melts away over the blend
    style = BLENDS.get(r.get("blend_style") or "fade", "fade"); g = []; last = "[0:v]"; t = 0.0
    if style == BLENDS["light"]: return light_blends(r, parts, W, H, total, body), total
    for i in range(1, len(parts)):
        t += int(round(r["shots"][i - 1]["dur"] * FPS)) / FPS; b = blend_in(r, i)
        if b > 0:
            tr = ("transition=custom:expr=" + style.split("expr=", 1)[1]) if style.startswith("custom:") else "transition=" + style
            g.append("%s[%d:v]xfade=%s:duration=%g:offset=%.4f[x%d]" % (last, i, tr, b, t, i))
        else:   # a hard cut inside a blended edit: joined (a 1-frame xfade had no frame to fade from, so the picture stopped at that cut)
            g.append("%s[%d:v]concat=n=2:v=1:a=0,settb=1/%d[x%d]" % (last, i, TB, i))   # (concat hands frames on in microseconds)
        last = "[x%d]" % i
    cmd = ["ffmpeg", "-v", "error", "-y"] + sum([["-i", p] for p in parts], []) + ["-filter_complex", ";".join(g), "-map", last,
           "-t", "%.3f" % total] + enc() + [body]
    subprocess.run(cmd, check=True, timeout=600)
    return body, total

def color_tags(p):
    """A file's colour tags as ffmpeg options, for a pass that hands its frames on raw (yuv444p) and writes them back as they were."""
    tags = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_space,color_primaries,color_transfer,color_range",
                                      "-of", "json", p], capture_output=True, text=True).stdout or "{}").get("streams", [{}])[0]
    return sum([[o, tags[k]] for o, k in (("-colorspace", "color_space"), ("-color_primaries", "color_primaries"), ("-color_trc", "color_transfer"),
                                          ("-color_range", "color_range")) if tags.get(k, "unknown") != "unknown"], [])

def light_blends(r, parts, W, H, total, body):
    """The "light" chain exactly as ffmpeg's xfade makes it (in yuv444p; P from 1 to 0 over the blend, in float32 from each
    frame's time; each result cut to 8 bits), as one 256 x 256 table per frame instead of ffmpeg working the expression out for
    every pixel: the same frames bit for bit, in half the time (Sep 29: a 10 s edit 53 -> 25 s). A hard cut is a plain join."""
    n = W * H * 3; ST = TB // FPS; A_ = np.arange(256.0)[:, None]; B_ = np.arange(256.0)[None, :]
    def frames(p):
        pr = subprocess.Popen(["ffmpeg", "-v", "error", "-i", p, "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"], stdout=subprocess.PIPE)
        try:
            while len(x := pr.stdout.read(n)) == n: yield np.frombuffer(x, np.uint8)
        finally: pr.stdout.close(); pr.kill(); pr.wait()   # also when a blend stops reading a shot early
    def xfade(A, B, off, d):   # off, d: seconds, as the filter string would give them to ffmpeg
        if not d: yield from A; yield from B; return
        off, d, k = int(round(off * 1e6) * TB / 1e6 + 0.5), int(round(d * 1e6) * TB / 1e6 + 0.5), 0   # microseconds -> TB, rounded as ffmpeg does
        for x in A:
            if k * ST < off: yield x; k += 1; continue
            y = next(B, None)
            if y is None: yield x; k += 1; continue
            j = k * ST - off; P = float(min(1, max(0, np.float32(1) - np.float32(j) / np.float32(d)))); k += 1
            yield np.floor(255 - (255 - A_ * P ** 0.75) * (255 - B_ * (1 - P) ** 0.75) / 255).astype(np.uint8).ravel()[x.astype(np.intp) * 256 + y]
            if j > d: break
        yield from B
    st, t = frames(parts[0]), 0.0
    for i in range(1, len(parts)):
        t += int(round(r["shots"][i - 1]["dur"] * FPS)) / FPS; st = xfade(st, frames(parts[i]), float("%.4f" % t), float("%g" % blend_in(r, i)))
    tags = color_tags(parts[0])   # the tags xfade would have carried
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv444p", "-s", "%dx%d" % (W, H), "-framerate", str(FPS)] + tags +
                         ["-i", "-", "-t", "%.3f" % total] + enc() + tags + [body], stdin=subprocess.PIPE)
    try:
        for f in st: p.stdin.write(f.tobytes())
    finally:
        p.stdin.close()
    if p.wait(timeout=600): raise RuntimeError("light blend: ffmpeg failed")
    return body

def fit_still(path, W, H, rotate=False):
    im = cv2.imread(path)
    if rotate: im = cv2.rotate(im, cv2.ROTATE_90_COUNTERCLOCKWISE)
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY); h, w = g.shape
    rr = np.where(g.mean(1) > 10)[0]; cc = np.where(g.mean(0) > 10)[0]
    y0, y1 = (rr[0], rr[-1] + 1) if len(rr) and rr[-1] - rr[0] < 0.9 * h else (0, h)
    x0, x1 = (cc[0], cc[-1] + 1) if len(cc) and cc[-1] - cc[0] < 0.9 * w else (0, w)
    rw = np.where(g.mean(1) < 245)[0]; cw = np.where(g.mean(0) < 245)[0]   # white bars (slideshows on white)
    if len(rw) and rw[-1] - rw[0] < 0.9 * h: y0, y1 = max(y0, rw[0]), min(y1, rw[-1] + 1)
    if len(cw) and cw[-1] - cw[0] < 0.9 * w: x0, x1 = max(x0, cw[0]), min(x1, cw[-1] + 1)
    im = im[y0:y1, x0:x1]; h, w = im.shape[:2]; s = max(W / w, H / h)
    im = cv2.resize(im, (int(w * s + .5), int(h * s + .5)), interpolation=cv2.INTER_AREA)
    y = (im.shape[0] - H) // 2; x = (im.shape[1] - W) // 2
    return im[y:y + H, x:x + W]

def render_collage(r, W, H, tmp, lossless=False):
    k = 0
    for s, n in zip(r["stills"], still_frames(r)):
        fr = fit_still(os.path.join(LIB, s["file"]), W, H, s.get("rotate", False))
        for j in range(n):   # the same frame n times: written once, the rest linked to it (18 full-size PNGs a still took 3/4 of the render)
            p = os.path.join(tmp, "f%05d.png" % k); k += 1
            if j: os.link(p0, p)
            else: cv2.imwrite(p, fr); p0 = p
    body = os.path.join(tmp, "body.mp4")   # lossless for the render; Resolve_Export imports it as a clip, so it keeps the old settings there
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", os.path.join(tmp, "f%05d.png")] +
                   (enc() if lossless else ["-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p"]) + [body], check=True, timeout=180)
    return body, k / FPS

# ---- the FX pass: effects that fire on the song's hits across the whole edit (after the cut, before the text) ----
FX_KEYS = {"hits", "pulse", "shake", "flash", "rgb", "strobe", "whip", "glow", "trails", "cut_flash", "cut_pulse", "blur_in", "vignette"}
# Nothing may darken the picture. The vignette stays, only for effect and very light,
# capped at VIGNETTE_MAX (about -5% overall, -13% in the corners). Pulses stay subtle.
VIGNETTE_MAX = 0.3   # ffmpeg vignette angle at strength 1

def still_frames(r):
    """How many frames each still of a collage is on for: sec_per_still each. With "pixels" a still spends its first `fly`
    seconds forming out of the one before, so the first, which forms out of nothing, is that much shorter: every still is
    whole for the same time. One still alone has nothing to turn into:
    it keeps its full time (pixel_pass says so)."""
    n = int(round(r.get("sec_per_still", 0.5) * FPS)); px = pixels_of(r); m = [n] * len(r.get("stills") or [])
    if px and len(m) > 1: m[0] = n - int(round(px["fly"] * FPS))
    return m

def cut_times(r):
    if r["kind"] != "video" and pixels_of(r): return [round(c / FPS, 4) for c in np.cumsum(still_frames(r))[:-1]]
    if r["kind"] != "video": return [i * r.get("sec_per_still", 0.5) for i in range(1, len(r.get("stills") or []))]
    out, t = [], 0
    for s in r["shots"][:-1]: t += int(round(s["dur"] * FPS)) / FPS; out.append(round(t, 4))
    return out

def kick_hits(r, dur):
    """Edit-relative times of the song's kicks: its tempo grid (the song's bpm; 120 if unknown), phased onto the kick drum
    (35-130 Hz) in this stretch, keeping only the beats where the kick actually hits."""
    m = SONGS.block(r) or {}
    if not m: return []
    audio = audio_of(r)[0]; e = SONGS.of_recipe(r) or {}; beat = 60.0 / float(e.get("bpm") or 120)
    sr = 11025; raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % m["start"], "-t", "%.3f" % dur, "-i", audio, "-ac", "1", "-ar", str(sr),
                                      "-af", "lowpass=f=130,highpass=f=35", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    if len(x) < sr // 2: return []
    hop = sr // 200; env = np.sqrt(np.convolve(x ** 2, np.ones(hop * 2) / (hop * 2), "same"))[::hop]   # 5 ms steps
    onset = np.maximum(0, np.diff(env, prepend=env[0]))
    def at(t): i = int(round(t * 200)); return onset[max(0, i - 6):i + 7].max() if i < len(onset) else 0.0
    best = max(np.arange(0, beat, 0.005), key=lambda ph: sum(at(t) for t in np.arange(ph, dur, beat)))
    grid = [(t, at(t)) for t in np.arange(best, dur, beat)]
    top = np.percentile([v for _, v in grid], 75) if grid else 0
    return [round(float(t), 3) for t, v in grid if v >= 0.35 * top]

def env_expr(times, tau, win=4):
    """An ffmpeg expression that jumps to 1 at each time and dies away (tau seconds)."""
    return "+".join("between(t\\,%.3f\\,%.3f)*exp(-(t-%.3f)/%g)" % (a, a + tau * win, a, tau) for a in times) or "0"

def on_expr(times, d):
    return "+".join("between(t\\,%.3f\\,%.3f)" % (a, a + d) for a in times) or "0"

def fx_pass(r, body, W, H, dur, tmp):
    """Everything in r["fx"], on the cut body: the text goes on after, so it stays clean. Keys (all optional):
      hits: "kicks" (default: the song's kicks; cuts when there's no song) | "cuts" | "both" | [edit-relative seconds]
      pulse z: zoom in z (0.005-0.02: subtle) on every hit, easing back  ·  cut_pulse z: a bigger one on every cut (~0.025)
      shake px: the frame jolts px pixels on every hit  ·  rgb px: red/blue split px pixels for 3 frames on every hit
      flash b: brightness +b (0.1-0.6) on every hit, dying fast  ·  cut_flash b: the same on every cut
      strobe n: every n-th hit inverts the picture for 2 frames  ·  whip: a motion-blur smear across every cut
      blur_in s: the edit opens out of focus and sharpens over s seconds
      glow a: bloom (0.2-0.6)  ·  trails d: light trails (decay 0.8-0.97; night and neon)
      vignette v: edges a touch darker, only for effect (0-1, true = 0.5; capped very light: at 1, -5% overall)
    Free Resolve can't match most of this."""
    f = r["fx"]; bad = set(f) - FX_KEYS
    if bad: raise ValueError("fx: unknown %s (known: %s)" % (", ".join(sorted(bad)), ", ".join(sorted(FX_KEYS))))
    cuts = cut_times(r); h = f.get("hits", "kicks")
    if isinstance(h, list): hits = [float(x) for x in h]
    elif h == "cuts": hits = cuts
    else:
        hits = kick_hits(r, dur) or cuts
        if h == "both": hits = sorted(set(hits) | set(cuts))
    g = []
    if f.get("trails"): g.append("lagfun=decay=%g" % float(f["trails"]))
    z, cz, sh = float(f.get("pulse") or 0), float(f.get("cut_pulse") or 0), float(f.get("shake") or 0)
    if z or cz or sh:   # zoom pulses and shake share one scale + crop; a little overscan gives the shake room
        over = (sh * 2.5) / min(W, H) if sh else 0
        zoom = "1+%g" % over + ("+%g*(%s)" % (z, env_expr(hits, 0.16)) if z else "") + ("+%g*(%s)" % (cz, env_expr(cuts, 0.22)) if cz else "")
        g.append("scale=w='2*trunc(iw*(%s)/2)':h='2*trunc(ih*(%s)/2)':eval=frame" % (zoom, zoom))
        jx = "%g*(%s)*sin(t*83)" % (sh, env_expr(hits, 0.12)) if sh else "0"; jy = "%g*(%s)*cos(t*71)" % (sh, env_expr(hits, 0.12)) if sh else "0"
        g.append("crop=%d:%d:x='(iw-%d)/2+%s':y='(ih-%d)/2+%s'" % (W, H, W, jx, H, jy))
    if f.get("whip"): g.append("gblur=sigma=%d:sigmaV=2:enable='%s'" % (40, "+".join("between(t\\,%.3f\\,%.3f)" % (c - 0.05, c + 0.05) for c in cuts) or "0"))
    if f.get("blur_in"): g.append("gblur=sigma='30*max(0\\,1-t/%g)':enable='lt(t\\,%g)'" % (float(f["blur_in"]), float(f["blur_in"])))
    if f.get("rgb"): g.append("rgbashift=rh=%d:bh=%d:rv=%d:enable='%s'" % (int(f["rgb"]), -int(f["rgb"]), int(f["rgb"]) // 3, on_expr(hits, 3 / FPS)))
    # flashes are sharp and short (a 2-3 frame pop), so the edit's overall brightness stays the plain cut's
    fl = "+".join(x for x in ("%g*(%s)" % (float(f["flash"]), env_expr(hits, 0.04, 3)) if f.get("flash") else "",
                                "%g*(%s)" % (float(f["cut_flash"]), env_expr(cuts, 0.05, 3)) if f.get("cut_flash") else "") if x)
    if fl: g.append("eq=brightness='min(0.8\\,%s)':eval=frame" % fl)
    if f.get("strobe"): g.append("negate=enable='%s'" % on_expr(hits[::int(f["strobe"])], 2 / FPS))
    if f.get("vignette"):
        v = 0.5 if f["vignette"] is True else min(1.0, max(0.0, float(f["vignette"])))
        if v: g.append("vignette=angle=%g" % (v * VIGNETTE_MAX))
    chain = ",".join(g) or "null"
    if f.get("glow"):   # bloom: a blurred copy screened over the picture, in RGB (screening YUV planes turns everything magenta)
        chain = "%s,format=gbrp,split[a][b];[b]gblur=sigma=25[gl];[a][gl]blend=all_mode=screen:all_opacity=%g,format=yuv420p" % (chain, float(f["glow"]))
    out = os.path.join(tmp, "fx.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", body, "-filter_complex", chain, "-t", "%.3f" % dur] + enc() + [out], check=True, timeout=600)
    return out, hits

def layer_pass(r, body, W, H, dur, tmp, idx):
    """r["layers"]: other clips blended over the whole edit or part of it (a permanent double exposure, a silhouette
    inside a face). Each: {"clip": key, "in": s, "mode": "screen"|"multiply"|"normal" (a plain mix: the one for a bright picture)|"lighten"|"overlay", "opacity": 0-1,
    "from": s, "to": s (edit time; default the whole edit), "matte": 0.3 (optional: the layer becomes a black silhouette on white,
    cut at that brightness, for "multiply"), "crop": [x, y, w, h] (optional: only that part of the layer, as fractions),
    "center" (as on a shot; not with "crop")}. Framed like a shot (bars cropped, filled), blended in RGB."""
    inputs, g, last = ["-i", body], [], "[0:v]"
    for i, L in enumerate(r["layers"], 1):
        c = idx[L["clip"]]; a, b = float(L.get("from", 0)), float(L.get("to", dur))
        # looped, so an overlay always covers its whole span
        inputs += ["-stream_loop", "-1", "-ss", "%.3f" % L.get("in", 0), "-t", "%.3f" % (b - a + 0.5), "-i", os.path.join(LIB, c["file"])]
        # "matte": the layer as a pure silhouette (dark subject -> black, everything lighter than the level -> white), so a
        # multiply cuts the figure into the picture and leaves the rest untouched (a figure inside a face)
        mt = ",format=gray,curves=all='0/0 %.2f/0 %.2f/1 1/1'" % (max(0.02, float(L["matte"]) - 0.08), min(0.98, float(L["matte"]) + 0.08)) if L.get("matte") else ""
        cr = ""
        if L.get("crop"):   # use only part of the layer's picture: [x, y, w, h] as fractions (e.g. just the sky above a silhouette)
            x0, y0, w0, h0 = [float(v) for v in L["crop"]]
            cr = "crop=iw*%g:ih*%g:iw*%g:ih*%g," % (w0, h0, x0, y0)
        fr = framing(c, None if L.get("crop") else L, W, H, b - a)   # (a layer cut to part of its picture is filled from that part's middle)
        g.append("[%d:v]%s%s%s,setpts=PTS-STARTPTS+%g/TB,format=gbrp[l%d]" % (i, cr, vf_for(c, W, H, 1.0, fr=fr), mt, a, i))
        g.append("%sformat=gbrp[b%d]" % (last, i))
        op = float(L.get("opacity", 0.5))
        how = ("all_expr='A*(1-%g)+B*%g'" % (op, op)) if L.get("mode") == "normal" else "all_mode=%s:all_opacity=%g" % (L.get("mode", "screen"), op)   # normal: a plain mix (ffmpeg's own ignores the opacity)
        g.append("[b%d][l%d]blend=%s:eof_action=pass:enable='between(t\\,%g\\,%g)'[o%d]" % (i, i, how, a, b, i))
        last = "[o%d]" % i
    out = os.path.join(tmp, "layers.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y"] + inputs + ["-filter_complex", ";".join(g) + ";%sformat=yuv420p[v]" % last, "-map", "[v]",
                    "-t", "%.3f" % dur] + enc() + [out], check=True, timeout=600)
    return out

def sparkles_pass(r, body, W, H, dur, tmp):
    """r["sparkles"]: animated rainbow stars over the picture (a star animation laid over a pretty
    video). {"count": 9, "path": "orbit"|"spiral"|"rise"|"drift", "size": 0.03 (of the width), "speed": 1.0,
    "center": [0.5, 0.45], "radius": 0.28, "trail": 0.86 (0 = none), "reflect": 0.62 (the water line as a fraction of the
    height, or null), "from": s, "to": s}. Drawn frame by frame (PIL), with glow and trails, then laid over the picture."""
    import math, colorsys
    from PIL import ImageFilter
    sp = r["sparkles"]
    if sp.get("style") == "dust": return dust_pass(r, body, W, H, dur, tmp)
    if sp.get("style") == "gold": return gold_pass(r, body, W, H, dur, tmp)
    if sp.get("style") == "motes": return motes_pass(r, body, W, H, dur, tmp)
    n = int(sp.get("count", 9)); size = float(sp.get("size", 0.03)) * W; speed = float(sp.get("speed", 1.0))
    cx, cy = [float(v) for v in sp.get("center", [0.5, 0.45])]; rad = float(sp.get("radius", 0.28)); path = sp.get("path", "orbit")
    trail = float(sp.get("trail", 0.86)); refl = sp.get("reflect"); a0, a1 = float(sp.get("from", 0)), float(sp.get("to", dur))
    sw, sh = W // 2, H // 2; acc = np.zeros((sh, sw, 4), np.float32); o = os.path.join(tmp, "sparkles.mp4")
    put, done = drawn_onto(body, W, H, dur, "rgba", OVER, o)
    def star(dr, x, y, rr, col):
        pts = [(x + (rr if k % 2 == 0 else rr * 0.42) * math.cos(math.pi / 2 + k * math.pi / 4), y - (rr if k % 2 == 0 else rr * 0.42) * math.sin(math.pi / 2 + k * math.pi / 4)) for k in range(8)]
        dr.polygon(pts, fill=col)
    frames = int(round(dur * FPS))
    for fi in range(frames):
        t = fi / FPS; im = Image.new("RGBA", (sw, sh), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
        if a0 <= t <= a1:
            for k in range(n):
                ph = 2 * math.pi * k / n + t * speed * 1.6
                if path == "spiral": rk = rad * (0.25 + 0.75 * ((t * 0.25 * speed + k / n) % 1)); x, y = cx + rk * math.cos(ph), cy + rk * 0.45 * math.sin(ph)
                elif path == "rise": x, y = cx + rad * math.sin(ph * 0.7 + k), 1.0 - ((t * 0.12 * speed + k / n) % 1)
                elif path == "drift": x, y = (k / n + 0.05 * math.sin(t * speed + k)) % 1, cy + 0.15 * math.sin(t * 0.8 * speed + k * 1.7)
                else: x, y = cx + rad * math.cos(ph), cy + rad * 0.45 * math.sin(ph)   # orbit: an ellipse, as if seen at an angle
                rgb = colorsys.hsv_to_rgb((k / n + t * 0.15) % 1, 0.75, 1.0)
                star(dr, x * sw, y * sh, size / 2 * (0.8 + 0.3 * math.sin(t * 6 + k)), tuple(int(c * 255) for c in rgb) + (255,))
        cur = np.asarray(im, np.float32)
        acc = np.maximum(acc * trail, cur) if trail else cur   # light trails behind each star
        out = Image.fromarray(acc.astype(np.uint8), "RGBA")
        glow = out.filter(ImageFilter.GaussianBlur(size / 6)); out = Image.alpha_composite(glow, out)
        if refl:   # the water's reflection: the stars mirrored below the line, fainter and softer
            wl = int(float(refl) * sh); top = out.crop((0, max(0, 2 * wl - sh), sw, wl)).transpose(Image.FLIP_TOP_BOTTOM).filter(ImageFilter.GaussianBlur(3))
            rf = np.asarray(top, np.float32); rf[..., 3] *= 0.45; out.alpha_composite(Image.fromarray(rf.astype(np.uint8), "RGBA"), (0, wl))
        put(out.resize((W, H), Image.BILINEAR))
    return done()

def drawn_onto(body, W, H, dur, fmt, graph, out):
    """An ffmpeg that lays drawn frames (raw, fmt) over body as they're drawn: put(frame) each, then done(). Writing a full-size
    PNG per frame and reading it back took up to a third of a drawn effect's render (Sep 29: dust 33.5 -> 20.3 s, same file)."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-i", body, "-f", "rawvideo", "-pix_fmt", fmt, "-s", "%dx%d" % (W, H), "-framerate", str(FPS),
                          "-i", "-", "-filter_complex", graph, "-map", "[v]", "-t", "%.3f" % dur] + enc() + [out], stdin=subprocess.PIPE)
    def done():
        p.stdin.close()
        if p.wait(timeout=1200): raise RuntimeError("drawn effect: ffmpeg failed")
        return out
    return (lambda f: p.stdin.write(f.tobytes())), done
OVER, SCREEN = "[0:v][1:v]overlay=0:0:eof_action=pass[v]", "[0:v]format=gbrp[a];[1:v]format=gbrp[b];[a][b]blend=all_mode=screen[m];[m]format=yuv420p[v]"   # pasted on / added as light

def highlight_tint(body, dur):
    """The colour of the picture's light (its brightest 3% of pixels, averaged over a few frames), so an effect drawn on top
    matches the grade."""
    cols = []
    for t in np.linspace(0.2, max(0.3, dur - 0.2), 5):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.2f" % t, "-i", body, "-frames:v", "1", "-vf", "scale=160:-2", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        if not raw: continue
        px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.float32); lum = px @ [0.3, 0.59, 0.11]
        cols.append(px[lum >= np.percentile(lum, 97)].mean(0))
    c = np.mean(cols, 0) if cols else np.array([255, 240, 200.0])
    return tuple(int(v) for v in np.clip(c / max(c.max(), 1) * 255, 0, 255))   # the hue of the light, at full brightness

def dust_pass(r, body, W, H, dur, tmp):
    """Pixie dust (r["sparkles"] with "style": "dust"): a stream of tiny glittering specks drifting along a curving path,
    each twinkling, with depth (near specks bigger, brighter, faster, softer), tinted with the picture's own light.
    Keys: "count" (150), "path": "arc"|"swirl"|"fall", "center": [x, y], "spread" (0.25), "speed" (1.0), "trail" (0.8),
    "from", "to", "tint": [r, g, b] (default: matched to the video)."""
    import math, random
    from PIL import ImageFilter
    sp = r["sparkles"]; rnd = random.Random(7); n = int(sp.get("count", 150)); speed = float(sp.get("speed", 1.0)); path = sp.get("path", "arc")
    cx, cy = [float(v) for v in sp.get("center", [0.5, 0.5])]; spread = float(sp.get("spread", 0.25)); trail = float(sp.get("trail", 0.8))
    a0, a1 = float(sp.get("from", 0)), float(sp.get("to", dur)); tint = tuple(sp.get("tint") or highlight_tint(body, dur))
    sw, sh = W // 2, H // 2; acc = np.zeros((sh, sw, 4), np.float32); put, done = drawn_onto(body, W, H, dur, "rgba", OVER, os.path.join(tmp, "dust.mp4"))
    parts = [dict(u=rnd.random(), z=rnd.random() ** 1.5, ox=rnd.gauss(0, 1), oy=rnd.gauss(0, 1), tw=rnd.random() * 6.28, f=2 + rnd.random() * 5) for _ in range(n)]
    white = tuple(int(0.55 * c + 0.45 * 255) for c in tint)   # speck cores lean white, their glow carries the tint
    for fi in range(int(round(dur * FPS))):
        t = fi / FPS; im = Image.new("RGBA", (sw, sh), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
        if a0 <= t <= a1:
            for p in parts:
                z = p["z"]; u = (p["u"] + t * 0.07 * speed * (0.6 + z)) % 1.0          # nearer specks travel faster
                if path == "swirl": ang = u * 4 * math.pi; x, y = cx + spread * u * math.cos(ang), cy + spread * 0.6 * u * math.sin(ang)
                elif path == "fall": x, y = cx + spread * 1.6 * (p["ox"] * 0.4 + 0.15 * math.sin(t + p["tw"])), u
                else: x, y = cx - 0.45 + 0.9 * u, cy + 0.18 * math.sin(u * math.pi * 1.3 + 0.4) - spread * 0.3   # arc: a stream across the frame
                x += 0.02 * spread * p["ox"] * (1.5 - z); y += 0.03 * spread * p["oy"] * (1.5 - z)
                tw = 0.35 + 0.65 * max(0.0, math.sin(t * p["f"] + p["tw"])) ** 3                # twinkle
                rr = (0.6 + 2.6 * z) * sw / 540; a = int(255 * tw * (0.35 + 0.65 * z))
                dr.ellipse([x * sw - rr, y * sh - rr, x * sw + rr, y * sh + rr], fill=white + (a,))
        cur = np.asarray(im, np.float32); acc = np.maximum(acc * trail, cur) if trail else cur
        out = Image.fromarray(acc.astype(np.uint8), "RGBA")
        g = out.filter(ImageFilter.GaussianBlur(2 * sw / 540)); ga = np.asarray(g, np.float32).copy()
        ga[..., :3] = tint; ga[..., 3] = np.clip(ga[..., 3] * 1.6, 0, 255)                   # a soft tinted glow around every speck
        out = Image.alpha_composite(Image.fromarray(ga.astype(np.uint8), "RGBA"), out)
        put(out.resize((W, H), Image.BILINEAR))
    return done()

def camera_path(body, dur, W, H):
    """How the camera moves, frame by frame (phase correlation between frames), so drawn particles can sit in the scene."""
    import cv2 as _cv
    cap = _cv.VideoCapture(body); prev = None; pos = [(0.0, 0.0)]; x = y = 0.0
    while True:
        ok, fr = cap.read()
        if not ok: break
        g = _cv.resize(_cv.cvtColor(fr, _cv.COLOR_BGR2GRAY), (W // 8, H // 8)).astype(np.float32)
        if prev is not None:
            (dx, dy), resp = _cv.phaseCorrelate(prev, g)
            if resp > 0.05 and abs(dx) < W / 40 and abs(dy) < H / 40: x += dx * 8; y += dy * 8
            pos.append((x, y))
        prev = g
    return pos

def gold_pass(r, body, W, H, dur, tmp):
    """Gold pixie dust that lives in the scene: specks at different depths follow the camera's own movement (near ones move more: parallax), near
    ones are soft out-of-focus bokeh, far ones tiny sharp glints with a star flare when they catch the light, all floating
    and twinkling, gathered around the brightest part of the picture, and ADDED as light (screen), not pasted on top.
    Keys: "count" (260), "near" (0.25: share of big bokeh specks), "drift" (1.0), "from", "to", "tint" (default: the
    picture's own light pushed towards gold)."""
    import math, random
    from PIL import ImageFilter
    sp = r["sparkles"]; rnd = random.Random(11); n = int(sp.get("count", 260)); drift = float(sp.get("drift", 1.0))
    a0, a1 = float(sp.get("from", 0)), float(sp.get("to", dur))
    lt = np.array(sp.get("tint") or highlight_tint(body, dur), np.float32); gold = np.array([255, 196, 92], np.float32)
    tint = tuple(int(v) for v in np.clip(0.55 * gold + 0.45 * lt, 0, 255)); hot = tuple(int(0.5 * v + 127) for v in tint)
    cam = camera_path(body, dur, W, H)
    # where the light is: particles gather around the brightest region of the first frame
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.3", "-i", body, "-frames:v", "1", "-vf", "scale=64:-2", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    gy = np.frombuffer(raw, np.uint8).reshape(-1, 64).astype(np.float32) if raw else np.ones((114, 64), np.float32)
    yy, xx = np.nonzero(gy >= np.percentile(gy, 92)); lx, ly = (xx.mean() / 64, yy.mean() / gy.shape[0]) if len(xx) else (0.5, 0.4)
    parts = []
    for _ in range(n):
        z = 0.35 + 2.6 * rnd.random() ** 1.3                      # depth: small z = near the lens
        near = rnd.random() < float(sp.get("near", 0.25)) and z < 1.0
        parts.append(dict(x=lx + rnd.gauss(0, 0.32), y=ly + rnd.gauss(0, 0.25), z=z, near=near, ph=rnd.random() * 6.3, f=1.5 + rnd.random() * 4,
                          vx=rnd.gauss(0, 0.012), vy=-0.01 - rnd.random() * 0.02))
    sw, sh = W // 2, H // 2; put, done = drawn_onto(body, W, H, dur, "rgb24", SCREEN, os.path.join(tmp, "gold.mp4"))
    for fi in range(int(round(dur * FPS))):
        t = fi / FPS; cx, cy = cam[min(fi, len(cam) - 1)]
        sharp = Image.new("RGB", (sw, sh)); soft = Image.new("RGB", (sw, sh)); ds, dso = ImageDraw.Draw(sharp), ImageDraw.Draw(soft)
        if a0 <= t <= a1:
            fade = min(1.0, (t - a0) / 0.6, (a1 - t) / 0.6)
            for p in parts:
                px = p["x"] + (p["vx"] * t + 0.01 * math.sin(t * 0.7 + p["ph"])) * drift + (cx / W) / p["z"] * 1.0   # parallax: the camera moves near specks more
                py = p["y"] + (p["vy"] * t + 0.008 * math.cos(t * 0.9 + p["ph"])) * drift + (cy / H) / p["z"] * 1.0
                if not (-0.1 < px < 1.1 and -0.1 < py < 1.1): continue
                tw = max(0.0, math.sin(t * p["f"] + p["ph"])) ** 4                                     # sharp twinkle
                X, Y = px * sw, py * sh
                if p["near"]:   # out-of-focus bokeh disc near the lens
                    rr = (14 + 18 * (1 - p["z"])) * sw / 540; c = tuple(int(v * (0.18 + 0.12 * tw) * fade) for v in tint)
                    dso.ellipse([X - rr, Y - rr, X + rr, Y + rr], fill=c)
                else:
                    rr = max(0.6, 2.2 / p["z"]) * sw / 540; b = (0.35 + 0.65 * tw) * fade
                    ds.ellipse([X - rr, Y - rr, X + rr, Y + rr], fill=tuple(int(v * b) for v in hot))
                    if tw > 0.7 and p["z"] < 1.8:   # a star glint when it catches the light
                        L = rr * 7 * tw; c = tuple(int(v * 0.7 * b) for v in hot)
                        ds.line([X - L, Y, X + L, Y], fill=c, width=1); ds.line([X, Y - L, X, Y + L], fill=c, width=1)
        glow = sharp.filter(ImageFilter.GaussianBlur(3 * sw / 540))
        g = np.asarray(glow, np.float32) * 1.8 * (np.array(tint, np.float32) / 255)
        img = np.clip(np.asarray(sharp, np.float32) + g + np.asarray(soft.filter(ImageFilter.GaussianBlur(4 * sw / 540)), np.float32), 0, 255)
        put(Image.fromarray(img.astype(np.uint8)).resize((W, H), Image.BILINEAR))
    return done()

AIR_TIERS = (0.7, 1.4, 2.8, 5.6, 11.2)   # how soft a speck is drawn (px across at 1080 wide): in focus far away, a faint blur up close

def air_setup(sp, n, z, far, W, H, dur):
    """The motes' "look": "air". What each speck is, decided once: how much light it throws back (most hardly any, a few a
    lot), whether it is a flake that glints as it turns or a grain that only shimmers, a fibre's length and which way it lies,
    and how far out of focus it is (the lens is on the street: dust near the camera is a wide faint blur). Plus the air's own
    slow unevenness (a soft noise field that changes every 1.6 s): dust hangs in drifts, never evenly."""
    rnd = np.random.default_rng(11)
    sig = np.where(far, 0.7, 0.7 + 3.0 / z ** 1.3) * float(sp.get("size", 1.0))
    keys = [cv2.GaussianBlur(cv2.resize(rnd.random((10, 6)).astype(np.float32), (W // 8, H // 8), interpolation=cv2.INTER_CUBIC), (0, 0), 3)
            for _ in range(int(dur / 1.6) + 3)]
    return {"bright": np.clip(rnd.lognormal(-1.0, 0.8, n), 0.04, 1.8), "flake": rnd.random(n) < 0.3, "gw": 1.0 + 3.0 * rnd.random(n),
            "gp": rnd.random(n) * 6.28, "ang": rnd.random(n) * np.pi, "spin": rnd.normal(0, 1.0, n), "tum": 0.6 + 2.0 * rnd.random(n),
            "len": np.where(rnd.random(n) < 0.5, 0.7 + 3.0 * rnd.random(n) ** 2, 0.0) * np.where(far, 0.35, 1.0) * W / 1080,
            "sig": sig, "tier": np.clip(np.round(np.log2(sig / 0.7)), 0, len(AIR_TIERS) - 1).astype(int), "keys": keys,
            "dither": np.random.default_rng(3)}

def air_paths(body, cam, W, H):
    """The camera's move and the sun's place for the "air" look, frame by frame. Two things made the far dust jump: the sun (where far specks gather,
    and what lights them) was found to the nearest 8 px, a step in most frames; and far specks hardly moved with a handheld
    picture's shake, so they jittered against the street. Here the camera's move is split: the slow part (walking: near things
    slide past faster, so it is shared out by depth) and the quick shake (the hand turning the lens: the whole picture moves as
    one, so every speck gets all of it). The sun is the centre of the brightest light, between pixels, steadied over time once
    the shake is taken out, then given the shake back: it moves exactly as the picture does and never in steps.
    Returns slow, shake (px) and sun (fractions of the frame), one row a frame."""
    def smooth(a, sg):
        k = np.exp(-0.5 * (np.arange(-3 * sg, 3 * sg + 1) / sg) ** 2); k /= k.sum()
        return np.stack([np.convolve(np.pad(a[:, i], 3 * sg, mode="edge"), k, mode="valid") for i in range(a.shape[1])], 1)
    cap = cv2.VideoCapture(body); sun = []
    while True:
        ok, fr = cap.read()
        if not ok: break
        b = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(fr, (W // 4, H // 4), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 8)
        w = np.clip(b - 0.85 * b.max(), 0, None); yy, xx = np.mgrid[0:b.shape[0], 0:b.shape[1]]; tot = max(float(w.sum()), 1e-6)
        sun.append((float((w * xx).sum()) / tot * 4, float((w * yy).sum()) / tot * 4))
    n = min(len(sun), len(cam)); cam = np.asarray(cam[:n], np.float64); sun = np.asarray(sun[:n], np.float64)
    slow = smooth(cam, 4); shake = cam - slow
    return slow, shake, (smooth(sun - shake, 6) + shake) / (W, H)

def air_light(a, x, y):
    """The picture's light at the points x, y (fractions of the frame), read between its cells, so a drifting speck's brightness
    changes evenly instead of in steps."""
    h, w = a.shape[:2]; X = np.clip(x * w - 0.5, 0, w - 1.001); Y = np.clip(y * h - 0.5, 0, h - 1.001)
    xi = X.astype(int); yi = Y.astype(int); fx = X - xi; fy = Y - yi
    return a[yi, xi] * (1 - fx) * (1 - fy) + a[yi, xi + 1] * fx * (1 - fy) + a[yi + 1, xi] * (1 - fx) * fy + a[yi + 1, xi + 1] * fx * fy

def air_splat(L, x, y, c, dx=None):
    """Adds light c (m, 3) at the points x, y (pixels, fractions kept: each point is shared between the four pixels round it,
    so a speck drifts smoothly instead of stepping from pixel to pixel). dx: a streak instead, that many pixels along x from each
    point (over a pixel only), its light spread evenly along it (the motion smear: air_draw)."""
    if dx is not None and (np.abs(dx) > 1).any():   # k points along each streak, laid in one go
        k = int(np.ceil(np.abs(dx).max())) + 1; dx = np.where(np.abs(dx) > 1, dx, 0.0); u = np.repeat(np.arange(k) / (k - 1), len(x))
        return air_splat(L, np.tile(x, k) + np.tile(dx, k) * u, np.tile(y, k), np.tile(c, (k, 1)) / k)
    h, w = L.shape[:2]; xi = np.floor(x).astype(int); yi = np.floor(y).astype(int); fx = x - xi; fy = y - yi
    for dx, dy, wt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        X = xi + dx; Y = yi + dy; ok = (X >= 0) & (X < w) & (Y >= 0) & (Y < h); idx = Y[ok] * w + X[ok]
        for ch in range(3): L[..., ch] += np.bincount(idx, weights=c[ok, ch] * wt[ok], minlength=h * w).reshape(h, w)

def air_draw(A, sp, W, H, t, px, py, v, col, lum, sunx, suny, sc, gl, smear=None):
    """One frame of the "air" look, as light to add. No discs: every speck is one to three points of light laid down between
    pixels and blurred, so it is a soft uneven fleck (a fibre lies along its own direction and shortens as it tumbles); smear
    (pixels along x, each speck's: "flow": "ground") draws each as a streak along the motion, as the plain look does. Near
    specks are out of focus: wider and much fainter (a blur spreads the same light over more picture), so thousands of them
    read as air with dust in it, not as dots. Flakes flash for a moment as they turn into the sun. Under it all, the dust too
    fine to see as specks: a thin haze where the light is, strongest toward the sun, uneven like the air."""
    s = W / 1080; img = np.zeros((H, W, 3), np.float32)
    k = t / 1.6; i = min(int(k), len(A["keys"]) - 2); f = (1 - np.cos((k - i) * np.pi)) / 2; nz = np.clip(A["keys"][i] * (1 - f) + A["keys"][i + 1] * f, 0, 1)
    hh, ww = nz.shape; drift = nz[np.clip((py * hh).astype(int), 0, hh - 1), np.clip((px * ww).astype(int), 0, ww - 1)]
    turn = np.abs(np.sin(A["gw"] * t + A["gp"]))
    shim = np.where(A["flake"], np.where(A["tier"] == 0, 0.5 + 1.6 * turn ** 6, 0.3 + 3.2 * turn ** 12),    # far flakes glint more gently: a crowd of
                    0.75 + 0.25 * np.sin(0.8 * A["gw"] * t + A["gp"]))                                       # pin-points flashing reads as flicker
    e = v * A["bright"] * (0.4 + 1.2 * drift) * shim * float(sp.get("opacity", 1.0)) * 255
    on = (e > 1.0) & (px > -0.02) & (px < 1.02) & (py > -0.02) & (py < 1.02)
    for ti, sg in enumerate(AIR_TIERS):
        m = on & (A["tier"] == ti)
        if not m.any(): continue
        d = 2 ** max(0, ti - 1); bl = 0.7 if ti == 0 else 1.4                 # the wide tiers are drawn small and enlarged: the same blur, cheaply
        L = np.zeros((H // d, W // d, 3), np.float32); x = px[m] * W / d; y = py[m] * H / d; sm = None if smear is None else smear[m] / d
        c = col[m] / 255 * (e[m] * (0.7 / sg) ** 1.3 * 6.283 * bl * bl)[:, None]   # the light it lays down: a blurred speck's peak is that much lower
        if d == 1:   # near enough in focus to have a shape: a fibre is three points along its direction, the tumble foreshortening it
            ln = (A["len"][m] * np.abs(np.cos(A["tum"][m] * t + A["gp"][m])))[:, None]; a = A["ang"][m] + A["spin"][m] * t
            ux, uy = np.cos(a), np.sin(a); c = c * (1 + ln / 2.5)
            for o, wgt in ((-1, 0.3), (0, 0.4), (1, 0.3)): air_splat(L, x + o * ln[:, 0] * ux, y + o * ln[:, 0] * uy, c * wgt, sm)
        else: air_splat(L, x, y, c, sm)
        L = cv2.GaussianBlur(L, (0, 0), bl * s)
        img += L if d == 1 else cv2.resize(L, (W, H), interpolation=cv2.INTER_CUBIC)
    img += cv2.GaussianBlur(img, (0, 0), 3.0 * s) * 0.5 * gl                                 # the light each speck spills into the air round it
    hz = float(sp.get("haze", 0.5))
    if hz > 0:
        wide = cv2.GaussianBlur(lum, (0, 0), 6); yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32); xx /= ww; yy /= hh
        sun = np.exp(-(((xx - sunx) ** 2 + ((yy - suny) * 0.8) ** 2) / 0.10)) * min(1.0, float(wide.max()) * 1.3)
        veil = (0.35 * wide ** 1.2 + 0.9 * sun) * (0.3 + 0.9 * nz) * (0.45 + 0.55 * yy) * hz * 20
        img += cv2.resize(veil[..., None] * (np.asarray(sc, np.float32) / 255), (W, H), interpolation=cv2.INTER_CUBIC)
    img += (A["dither"].random(img.shape, dtype=np.float32) - 0.5) * (img > 0.3)                # no steps in the faint light once it's 8 bits
    return np.clip(img, 0, 255).astype(np.uint8)

def motes_pass(r, body, W, H, dur, tmp):
    """Real dust in the air. Thousands of tiny
    specks at different depths are kicked up, then settle (gravity, drag, a little random wander) and move with the camera
    (parallax). Each speck is lit by the video itself: it only shows where the picture is bright, in the picture's own
    colour at that spot, and glows more near the sun (light scattering), so it can only match the grade. Added as light.
    Keys: "count" (1500), "kick" (1.0: how hard it's thrown up), "settle" (1.0), "size" (1.0), "glow" (1.0), "far" (0.5: share of
    far-away specks), "even": 0-1 (0 = gathered in the light, like a sunbeam; 1 = spread across the whole scene, far and near balanced), "flow": "ground" (shot from a moving car/train: the dust streams past with the landscape; "stream": [vx, vy] sets the speed
    by hand, in frame widths a second for a speck 1 m away, when the ground is too dark to track), "from", "to".
    "look": "air" draws the same dust as air_setup / air_draw do (soft uneven flecks, far more and far fainter, a haze in the light;
    "count" then wants about 16000; "opacity" (1.0), "haze" (0.5))."""
    import cv2 as _cv, math, random
    sp = r["sparkles"]; rnd = np.random.default_rng(5)
    # "flow": "ground": from a moving vehicle the dust is OUTSIDE: it streams past with the landscape, measured from the lower half's motion
    ground = sp.get("flow") == "ground"; n = int(sp.get("count", 1500)); kick = float(sp.get("kick", 1.0))
    settle = float(sp.get("settle", 1.0)); size = float(sp.get("size", 1.0)); gl = float(sp.get("glow", 1.0))
    a0, a1 = float(sp.get("from", 0)), float(sp.get("to", dur)); cam = camera_path(body, dur, W, H)
    # depth tiers: half near, the rest far
    # away, where specks are tiny and crowd toward the horizon (perspective), still catching the light in the beam
    far = rnd.random(n) < float(sp.get("far", 0.5))
    z = np.where(far, 3.0 + 60.0 * rnd.random(n) ** 2.0, 0.4 + 2.6 * rnd.random(n) ** 1.2)
    x0 = rnd.uniform(-0.1, 1.1, n); y0 = 1.05 - 0.8 * rnd.random(n) ** 0.7  # denser low down: dust rises from the ground
    vx = rnd.normal(0, 0.03, n) * kick; vy = -(0.04 + 0.12 * rnd.random(n)) * kick   # thrown up gently...
    born = rnd.uniform(-5.0, dur, n); life = 5.0 + 3.0 * rnd.random(n)     # ...at staggered moments, so there's always dust in the air
    ph = rnd.random(n) * 6.28; fq = 0.5 + rnd.random(n) * 2.0
    rad = np.where(far, 0.5 + 0.4 * float(sp.get("even", 0.0)), 0.55 + 1.6 * (1 / z) ** 1.2) * size * W / 1080  # tiny: ~0.5-2.5 px; far specks a fine point
    hz = float(sp.get("horizon", 0)) or None                                # the horizon's height (fraction); found from the picture if not given
    air = air_setup(sp, n, z, far, W, H, dur) if sp.get("look") == "air" else None
    if air: air["slow"], air["shake"], air["sun"] = air_paths(body, cam, W, H)
    gx = gy = None
    if ground:   # how far the near landscape has moved, frame by frame (optical flow in the bottom half, median)
        cp = _cv.VideoCapture(body); prev = None; gx, gy = [0.0], [0.0]
        while True:
            ok, fr = cp.read()
            if not ok: break
            g = _cv.cvtColor(_cv.resize(fr, (W // 6, H // 6)), _cv.COLOR_BGR2GRAY); g = g[g.shape[0] // 2:]
            if prev is not None:
                fl = _cv.calcOpticalFlowFarneback(prev, g, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                gx.append(gx[-1] + float(np.median(fl[..., 0])) * 6 / W); gy.append(gy[-1] + float(np.median(fl[..., 1])) * 6 / H)
            prev = g
        if sp.get("stream"):   # a dark or featureless ground can't be tracked: the speed given (frame widths a second at 1 m)
            vx_, vy_ = [float(v) for v in sp["stream"]]; gx = [vx_ * f / FPS for f in range(len(gx))]; gy = [vy_ * f / FPS for f in range(len(gy))]
        vel = (gx[-1] - gx[0]) / max(1, len(gx) - 1)                           # per frame, for the motion smear
    cap = _cv.VideoCapture(body); put, done = drawn_onto(body, W, H, dur, "bgr24", SCREEN, os.path.join(tmp, "motes.mp4"))
    for fi in range(int(round(dur * FPS))):
        ok, fr = cap.read()
        if not ok: break
        t = fi / FPS
        # the light: a soft version of the frame (colour and brightness at every spot) and where the sun is
        small = _cv.resize(fr, (W // 8, H // 8), interpolation=_cv.INTER_AREA).astype(np.float32)
        soft = _cv.GaussianBlur(small, (0, 0), 2.5); lum = soft @ np.array([0.114, 0.587, 0.299], np.float32) / 255
        sy, sx = np.unravel_index(np.argmax(_cv.GaussianBlur(lum, (0, 0), 4)), lum.shape); sunx, suny = sx / lum.shape[1], sy / lum.shape[0]
        if air: sunx, suny = air["sun"][min(fi, len(air["sun"]) - 1)]
        # motion: thrown up, dragged, pulled down, wandering; the camera's move shifts near specks more (parallax)
        age = t - born; alive = (age > 0) & (age < life)
        drag = np.exp(-0.9 * np.maximum(age, 0)); fall = 0.006 * settle * np.maximum(age, 0) ** 1.6   # settles slowly
        if ground:   # world-fixed: specks move with the landscape, near ones faster than the ground (parallax), wrapping round
            sx_ = gx[min(fi, len(gx) - 1)] * 1.2 / z; sy_ = gy[min(fi, len(gy) - 1)] * 1.2 / z
            px = (x0 + vx * (1 - drag) / 0.9 + 0.008 * np.sin(age * fq + ph) + sx_ + 0.1) % 1.2 - 0.1
            py = y0 + vy * (1 - drag) / 0.9 + fall + 0.008 * np.cos(age * fq * 1.3 + ph) + sy_
        elif air:   # the walk shared out by depth, the hand's shake given whole to every speck (air_paths)
            j = min(fi, len(air["slow"]) - 1)
            px = x0 + vx * (1 - drag) / 0.9 + 0.008 * np.sin(age * fq + ph) + (air["slow"][j][0] / W) / z + air["shake"][j][0] / W
            py = y0 + vy * (1 - drag) / 0.9 + fall + 0.008 * np.cos(age * fq * 1.3 + ph) + (air["slow"][j][1] / H) / z + air["shake"][j][1] / H
        else:
            px = x0 + vx * (1 - drag) / 0.9 + 0.008 * np.sin(age * fq + ph) + (cam[min(fi, len(cam) - 1)][0] / W) / z
            py = y0 + vy * (1 - drag) / 0.9 + fall + 0.008 * np.cos(age * fq * 1.3 + ph) + (cam[min(fi, len(cam) - 1)][1] / H) / z
        agefade = np.clip(np.minimum(age / 0.5, (life - age) / 1.2), 0, 1) * alive
        # far specks sit in perspective: pulled toward the vanishing point on the horizon (under the sun), smaller and slower
        vpx, vpy = sunx, (hz if hz else min(0.85, suny + 0.05))
        k = np.where(far, 1.0 / np.sqrt(z / 3.0), 1.0)
        px = vpx + (px - vpx) * k; py = vpy + (py - vpy) * k
        img = np.zeros((H, W, 3), np.float32)
        if a0 <= t <= a1:
            fade = min(1.0, (t - a0) / 0.4, (a1 - t) / 0.5)
            if air: fade = min(1.0, (t - a0) / 0.4 if "from" in sp else 1.0, (a1 - t) / 0.5 if "to" in sp else 1.0)   # there from the first frame to the last
            ix = np.clip((px * lum.shape[1]).astype(int), 0, lum.shape[1] - 1); iy = np.clip((py * lum.shape[0]).astype(int), 0, lum.shape[0] - 1)
            lit = (air_light(lum, px, py) if air else lum[iy, ix]) ** 1.3                    # the light around the speck
            sun = np.exp(-(((px - sunx) ** 2 + ((py - suny) * 0.8) ** 2) / 0.12))          # backlit dust glows toward the sun (forward scatter), a wide glow
            lit = np.maximum(lit, 0.18 * lum.mean())                                         # some dust shows anywhere there's light, not only by the sun
            tw = 1.0 if air else 0.55 + 0.45 * np.sin(t * 5 * fq + ph) ** 2                 # air: each speck's own shimmer and glints (air_draw)
            spread = float(sp.get("even", 0.0))   # 0 = gather in the light (a sunbeam); 1 = everywhere (low sun across a whole scene)
            v = np.clip(((lit * 0.9 + sun * 1.3) * (1 - spread) + (0.45 + 0.3 * lit + 0.35 * sun) * spread) * tw * fade * agefade
                        * np.where(far, 0.55 + 0.35 * spread, (0.45 + 0.55 / z) * (1 - 0.1 * spread)), 0, 1.5)   # even: far specks up, near just a touch down
            v = v * (0.3 + 0.7 * np.clip(py, 0, 1))                                          # thinner higher up (dust rises from the ground)
            hi = soft.reshape(-1, 3)[lum.reshape(-1) >= np.percentile(lum, 90)].mean(0)          # the colour of the light (the brightest tenth)
            m = hi.mean(); sc = np.clip(m + (hi - m) * 1.6, 0, 255); sc = sc / max(sc.max(), 1) * 255   # its saturation kept (a near-white sun turns specks white)
            lc = soft[iy, ix] / np.maximum(soft[iy, ix].max(1, keepdims=True), 1) * 255
            w = np.clip(lum[iy, ix] * 1.5, 0, 1)[:, None]; col = w * lc + (1 - w) * sc          # lit specks take the local light, dark ones the sun's
            if air:   # (with ground flow, near specks smeared along the motion as below: the air look had none)
                put(air_draw(air, sp, W, H, t, px, py, v, col, lum, sunx, suny, sc, gl, np.where(far, 0.0, -vel * 1.2 / z * W * 0.5) if ground else None)); continue
            for k in np.nonzero((v > 0.04) & (px > -0.02) & (px < 1.02) & (py > -0.02) & (py < 1.02))[0]:
                c = tuple(float(q) * v[k] for q in col[k]); X, Y = int(px[k] * W), int(py[k] * H)
                if ground and not far[k]:   # a short smear along the motion (a moving camera's shutter), longer for near specks
                    L = int(abs(vel) * 1.2 / z[k] * W * 0.5)
                    if L > 1: _cv.line(img, (X - int(np.sign(vel) * L), Y), (X, Y), c, max(1, int(round(rad[k]))), lineType=_cv.LINE_AA); continue
                _cv.circle(img, (X, Y), max(1, int(round(rad[k]))), c, -1, lineType=_cv.LINE_AA)
            img = img + _cv.GaussianBlur(img, (0, 0), 2.2 * W / 1080) * 1.3 * gl                # a soft glow round each speck
        put(np.clip(img, 0, 255).astype(np.uint8))
    return done()

def doodle_pass(r, body, W, H, dur, tmp):
    """r["doodles"]: hand-drawn animation on and around the people (doodles.py: the effects, Vision for where people are)."""
    import doodles
    specs = [dict(sp, fx=sp.get("fx") or sp.get("shape")) for sp in r["doodles"]]   # "shape": the first doodle recipes' name for it
    got = doodles.render_overlay(specs, body, W, H, FPS, os.path.join(tmp, "dd"), cuts=cut_times(r), cache=os.path.join(tmp, "vision"),
                                 lyric_y=(LAYER_Y if isinstance(r.get("lyrics"), str) else LYRIC_Y) if r.get("lyrics") else None)
    if not got: return body
    pat, t0 = got; o = os.path.join(tmp, "doodles.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", body, "-itsoffset", "%.6f" % t0, "-framerate", str(FPS), "-i", pat, "-filter_complex",
                    "[0:v][1:v]overlay=0:0:eof_action=pass:format=auto[v]", "-map", "[v]", "-t", "%.3f" % dur] + enc() + [o], check=True, timeout=1800)
    return o

# ---- the pixel morph: at every cut the picture's own pixels fly to where they fit the next picture ----
PIXEL_SIZES = (1, 2, 3, 4, 5, 6, 8)   # how many pixels across a flying grain is: each divides 1080 and 1920
PIXEL_BINS = 64                      # brightness steps a picture is cut into: what "fits" means
PIXEL_WIDE = 0.75                    # how much of the light round a place counts in its brightness (pixel_plan)
PIXEL_STAGGER = 0.35                 # the grains leave over the first 35% of the flight, each at its own moment, and land the same way
PIXEL_SWEEP = 0.45                   # with "wind": the lift-off crosses the frame over the first 45% of the flight, the landing over the last 45%
PIXEL_WINDS = ("up", "down", "left", "right", "out", "in")
PIXEL_STYLES = ("sand", "sort", "mosh")   # the ways a picture can turn into the next

def pixels_of(r):
    """r["pixels"] as its options, or None: true or {} (the defaults), a number (the flight's seconds), a style's name, or {"style",
    "fly", "size", "wind", "streak", "decay"}. style: "sort" (the pixel sort: the picture runs into streaks of its own light: sort_flight), "mosh" (the thread ripper effect: the old picture
    dragged by the new shot's motion: mosh_flight; "streak" there is how much harder it is dragged, "decay" the seconds at the
    end of the flight it takes to settle into the new shot: half the flight unless given), or "sand" (grains flying
    to where they fit: pixel_plan; with "wind" and "streak"). With no style named it is the one that was kept: mosh on a video,
    sort on a collage (mosh needs a moving picture). Sand was turned down as it was: it is the style only when a recipe names it, so the recipes made with it render.
    A flight is 2 s unless told otherwise. "tail" is worked out here: the seconds the outgoing shot plays on
    past its cut (run_on): the whole flight for sand, 0.6 of it for sort, and for mosh until the incoming shot's movement has
    taken the picture over."""
    p = r.get("pixels")
    if not p and p != {}: return None   # ({} is true with nothing changed: it used to render plain cuts without a word)
    p = {} if p is True else dict(p) if isinstance(p, dict) else {"style": p} if isinstance(p, str) else {"fly": float(p)}
    bad = set(p) - {"fly", "size", "wind", "streak", "style", "decay"}
    if bad: raise ValueError('pixels: unknown %s (known: "fly", "size", "wind", "streak", "style", "decay")' % ", ".join(sorted(bad)))
    own = p.get("decay") is not None   # the recipe set a decay itself
    p = dict(fly=float(p.get("fly", 2.0)), size=float(p.get("size", 3)), wind=p.get("wind") or None, streak=float(p.get("streak") or 0),
             style=p.get("style") or ("sort" if r.get("kind") == "collage" else "mosh"),
             decay=float(p["decay"]) if own else 0.5 * float(p.get("fly", 2.0)))
    if p["style"] not in PIXEL_STYLES: raise ValueError("pixels style %r: one of %s" % (p["style"], ", ".join(PIXEL_STYLES)))
    if p["style"] == "mosh" and r.get("kind") == "collage": raise ValueError('pixels style "mosh" needs a moving picture to carry the old one: not on a collage')
    if p["size"] not in PIXEL_SIZES: raise ValueError("pixels size %g: one of %s" % (p["size"], ", ".join(map(str, PIXEL_SIZES))))   # (3.9 is refused: it used to be cut to 3 without a word)
    p["size"] = int(p["size"])
    if p["wind"] not in (None,) + PIXEL_WINDS: raise ValueError("pixels wind %r: one of %s" % (p["wind"], ", ".join(PIXEL_WINDS)))
    if not 0 <= p["streak"] <= 4: raise ValueError("pixels streak %g: 0 (none) to 4 frames of travel" % p["streak"])
    if p["fly"] * FPS < 2: raise ValueError("pixels fly %g: too short to see" % p["fly"])
    if r.get("blend") or any(s.get("blend") for s in r.get("shots") or []): raise ValueError('"pixels" and "blend" both change how a shot becomes the next: one or the other')
    if p["style"] == "mosh" and not 0.2 <= p["decay"] <= p["fly"] - 0.5:
        raise ValueError("pixels decay %g: from 0.2 s to half a second less than the flight (%g)" % (p["decay"], p["fly"]) if own else
                         'pixels fly %g: a "mosh" flight is 1 s or longer (its second half is the settling into the new shot; or give "decay" too, from 0.2 s to half a second less than "fly")' % p["fly"])
    p["tail"] = {"sand": p["fly"], "sort": 0.6 * p["fly"], "mosh": (p["fly"] - p["decay"]) * MOSH_OVER / MOSH_SETTLE}[p["style"]]
    return p

def pixel_plan(A, B, px, seed=0, step=0.0):
    """Where every grain of picture A goes so that together they make picture B, and when (frames as H x W x 3, Y U V; px:
    pixels_of's options; step: how much of the flight one frame is, for "streak"). A grain is a size x size block of A, and
    each lands on one block of B: the brightest go to B's brightest places and the darkest to its darkest, and within one
    brightness the warm go to the warm and the cool to the cool (so a sunset's orange finds the next sky's horizon): a grain
    changes as little as it can on the way. B's brightness is read with the light round each place added in (PIXEL_WIDE)
    and a little noise: where B is flat (a night sky, the black round a pool) the grains that fill it are laid as a soft
    falloff toward the light with a fine grain, not in the blocks its compression left there (matching ranks stretches a
    flat stretch's tiny differences into patches). Nothing is added or lost: every grain of A lands on one place of B.
    When: each grain leaves at its own moment in the first PIXEL_STAGGER of the flight and travels for the rest of it. With
    "wind" the moments follow the picture instead: the lift-off sweeps across the outgoing picture that way ("up": from the
    bottom to the top; "out": from the middle outward) and the landing sweeps across the incoming one the same way, so the
    change crosses the frame like wind over sand instead of happening everywhere at once."""
    H, W = A.shape[:2]; g = px["size"]; gw, gh = W // g, H // g; n = gw * gh; rnd = np.random.default_rng(seed)
    a = cv2.resize(A, (gw, gh), interpolation=cv2.INTER_AREA).reshape(n, 3).astype(np.float32)
    b = cv2.resize(B, (gw, gh), interpolation=cv2.INTER_AREA).reshape(n, 3).astype(np.float32)
    def by_light(c):   # darkest to brightest
        y = c[:, 0].reshape(gh, gw); return np.argsort((y + PIXEL_WIDE * cv2.GaussianBlur(y, (0, 0), gw / 8)).ravel() + rnd.uniform(-2, 2, n), kind="stable")
    oa, ob = by_light(a), by_light(b); wa, wb = a[:, 2] - a[:, 1], b[:, 2] - b[:, 1]   # how warm: Cr minus Cb
    src, dst = np.empty(n, np.int64), np.empty(n, np.int64); per = -(-n // PIXEL_BINS)
    for k in range(0, n, per):
        ia, ib = oa[k:k + per], ob[k:k + per]; src[k:k + per], dst[k:k + per] = ia[np.argsort(wa[ia], kind="stable")], ib[np.argsort(wb[ib], kind="stable")]
    sx, sy, dx, dy = [v.astype(np.float32) for v in (src % gw, src // gw, dst % gw, dst // gw)]
    def along(x, y):   # how far along the wind a place is, 0 (where it starts) to 1
        x, y = x / max(1, gw - 1), y / max(1, gh - 1); far = np.hypot(x - 0.5, (y - 0.5) * gh / gw) / np.hypot(0.5, 0.5 * gh / gw)
        return {"up": 1 - y, "down": y, "right": x, "left": 1 - x, "out": far, "in": 1 - far}[px["wind"]]
    if px["wind"]:
        t0 = PIXEL_SWEEP * (0.85 * along(sx, sy) + 0.15 * rnd.random(n)); t1 = 1 - PIXEL_SWEEP * (1 - 0.85 * along(dx, dy) - 0.15 * rnd.random(n))
    else:
        t0 = PIXEL_STAGGER * rnd.random(n); t1 = t0 + (1 - PIXEL_STAGGER)
    return dict(g=g, gw=gw, gh=gh, W=W, H=H, src=src, dst=dst, sx=sx, sy=sy, dx=dx, dy=dy, t0=t0.astype(np.float32), t1=t1.astype(np.float32),
                mean=(a.mean(0) + b.mean(0)) / 2, streak=px["streak"], step=step)

def pixel_frame(P, p, A, B):
    """The picture p of the way through a flight (0 to 1) from A, the outgoing picture, to B, the incoming one, both as
    they are playing at that moment. Each grain eases along a straight line from its place in A to its place in B,
    and it is a window on both: it shows A as A is now at the place it left, turning on the way into B as B is now at the
    place it is flying to, so it lands already right and nothing is left to change after. A grain that hasn't left is A's own sharp
    pixels; a place whose grain has landed is B's. In the air a grain is drawn where it is, and with "streak" also where it
    was over the last so many frames, fainter the further back (a long exposure: lines instead of specks); where several
    cross they mix. Where every grain has gone, none has landed and none is passing, the cloud itself shows through, soft:
    never black (nothing may darken the picture)."""
    if p <= 0: return A
    if p >= 1: return B
    g, W, H, gw, gh = P["g"], P["W"], P["H"], P["gw"], P["gh"]
    ease = lambda q: (lambda u: u * u * (3 - 2 * u))(np.clip((q - P["t0"]) / (P["t1"] - P["t0"]), 0, 1))
    e = ease(p); mv = (e > 0) & (e < 1); em = e[mv][:, None]   # in the air
    a = cv2.resize(A, (gw, gh), interpolation=cv2.INTER_AREA).reshape(-1, 3)[P["src"][mv]].astype(np.float32)
    b = cv2.resize(B, (gw, gh), interpolation=cv2.INTER_AREA).reshape(-1, 3)[P["dst"][mv]].astype(np.float32)
    c = a + (b - a) * em; sx, sy, vx, vy = P["sx"][mv], P["sy"][mv], (P["dx"] - P["sx"])[mv], (P["dy"] - P["sy"])[mv]
    w = np.zeros(W * H, np.float32); acc = np.zeros((3, W * H), np.float32)
    m = max(1, int(np.ceil(6 * P["streak"])))   # with a streak: the grain at m moments over its last `streak` frames of travel
    for k in range(m):
        ek = em[:, 0] if not k else ease(p - P["streak"] * P["step"] * k / m)[mv]; wk = 1 - k / m
        at = np.rint((sy + vy * ek) * g).astype(np.int64) * W + np.rint((sx + vx * ek) * g).astype(np.int64)
        w += np.bincount(at, minlength=W * H) * wk
        for ch in range(3): acc[ch] += np.bincount(at, weights=c[:, ch], minlength=W * H) * wk
    w = w.reshape(H, W); acc = np.ascontiguousarray(acc.reshape(3, H, W).transpose(1, 2, 0))
    if g > 1:   # each grain a g x g square from the point it was laid at
        acc = cv2.boxFilter(acc, -1, (g, g), anchor=(g - 1, g - 1), normalize=False, borderType=cv2.BORDER_CONSTANT)
        w = cv2.boxFilter(w, -1, (g, g), anchor=(g - 1, g - 1), normalize=False, borderType=cv2.BORDER_CONSTANT)
    def cells(ix):   # those grid places, as a mask over the frame
        k = np.zeros(gw * gh, np.float32); k[ix] = 1; return cv2.resize(k.reshape(gh, gw), (W, H), interpolation=cv2.INTER_NEAREST)[..., None]
    landed = cells(P["dst"][e >= 1]); held = np.maximum(landed, cells(P["src"][e <= 0]))   # a place with a grain on it: landed (B's) or not yet left (A's)
    sharp = np.where(landed > 0, B, A).astype(np.float32)
    def soft(k, sg, under):   # the picture's colours spread wide at 1/k size; where hardly anything is near, what's under shows instead
        s = lambda v: cv2.GaussianBlur(cv2.resize(v, (W // k, H // k), interpolation=cv2.INTER_AREA), (0, 0), sg)
        sw = s(w + held[..., 0])[..., None]; return (s(acc + sharp * held) + 0.02 * under) / (sw + 0.02)
    far = soft(40, 4, P["mean"]); cloud = cv2.resize(soft(8, 2.5, cv2.resize(far, (W // 8, H // 8), interpolation=cv2.INTER_LINEAR)), (W, H), interpolation=cv2.INTER_LINEAR)
    out = np.where(w[..., None] > 0, acc / np.maximum(w, 1e-6)[..., None], np.where(held > 0, sharp, cloud))
    return np.rint(out).clip(0, 255).astype(np.uint8)

def yuv_frames(path, W, H, at=None):
    """A file's frames as H x W x 3 arrays (Y, U, V at full size), one after another; at=k: only frame k."""
    n = W * H * 3; pr = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path] + (["-vf", "select='eq(n\\,%d)'" % at, "-frames:v", "1"] if at is not None else []) +
                                         ["-f", "rawvideo", "-pix_fmt", "yuv444p", "-"], stdout=subprocess.PIPE)
    try:
        while len(x := pr.stdout.read(n)) == n: yield np.ascontiguousarray(np.frombuffer(x, np.uint8).reshape(3, H, W).transpose(1, 2, 0))
    finally: pr.stdout.close(); pr.kill(); pr.wait()   # also when a flight stops reading a shot early

def yuv_write(frames, like, W, H, dur, out):
    """Those frames written as a file between passes, with the colour tags of `like` (the file they came from)."""
    tags = color_tags(like)
    wr = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv444p", "-s", "%dx%d" % (W, H), "-framerate", str(FPS)] + tags +
                          ["-i", "-", "-t", "%.3f" % dur] + enc() + tags + [out], stdin=subprocess.PIPE)
    try:
        for f in frames: wr.stdin.write(np.ascontiguousarray(f.transpose(2, 0, 1)).tobytes())
    finally: wr.stdin.close()
    if wr.wait(timeout=600): raise RuntimeError("pixels: ffmpeg failed")
    return out

SORT_MOST = 0.85   # at the height of a "sort" flight this share of the picture has run into streaks
MOSH_BLOCK = 20    # pixels across a block that moves as one, as a video codec's blocks do (it divides 1080 and 1920)
MOSH_SETTLE, MOSH_OVER = 0.6, 0.65   # on the mosh's own clock (1 = the flight less its decay, over 0.6): the settling starts at 0.6, and by 0.65 the incoming
                                     # shot's movement alone carries the picture (so the settling begins just before the outgoing shot lets go)

def sort_flight(a0, land, px, seed, nb):
    """ "style": "sort": the picture runs into streaks of its own light, and the streaks gather into the next picture (pixel
    sorting, the glitch-art look). Nothing scatters and nothing fades at random. In every column the stretches brighter than
    a level are put in order, brightest on top (with "wind": "down", at the bottom), and the level drops as the flight goes:
    first only the highlights run, then the stretches grow and join until most of the picture is lines of light (SORT_MOST).
    Columns run a little ahead of or behind each other, so it goes unevenly, like wax. The incoming picture, as it will stand
    when the flight ends, is taken the same way and shown backward: its streaks fall back into place. Between the two, when
    both are streaks, one set turns into the other. Each picture keeps playing inside its blocks the whole time: only where
    a block sits changes."""
    H, W = a0.shape[:2]; g = px["size"]; gw, gh = W // g, H // g; rnd = np.random.default_rng(seed); cols = np.arange(gw)[None, :]
    n1 = int(round(0.45 * nb)); nc = max(2, int(round(0.12 * nb))); n2 = nb - n1 - nc; up = px["wind"] != "down"
    ys = np.arange(gh, dtype=np.int32)[:, None]; ease = lambda u: u * u * (3 - 2 * u)
    def read(f):   # a picture's brightness on the grid, the level each share of it lies above, and how far each column runs ahead or behind
        K = cv2.resize(f[..., 0], (gw, gh), interpolation=cv2.INTER_AREA).astype(np.int32)
        k = sum(wt * cv2.resize(rnd.random((1, m)).astype(np.float32), (gw, 1), interpolation=cv2.INTER_CUBIC)[0] for m, wt in ((7, 0.5), (19, 0.3), (53, 0.2)))   # wide and narrow at once: no two streaks alike
        S = cv2.GaussianBlur(K.astype(np.float32), (0, 0), 1.5)   # what decides which stretches run: the brightness without its grain, or noise would break them up
        return K, S, np.percentile(S, np.linspace(100, 0, 101)), 1 + 0.6 * (k - k.mean()) / max(1e-6, float(np.abs(k - k.mean()).max()))
    def state(P, q):   # where each block comes from (the row, in its own column) when the brightest share q of the picture has run
        K, S, level, lead = P; T = np.interp(np.clip(q * lead, 0, 1) * 100, np.arange(101), level)
        m = S > T[None, :]; run = np.concatenate([np.zeros((1, gw), np.int32), np.cumsum(m[1:] != m[:-1], axis=0, dtype=np.int32)])
        return np.argsort(run * 4096 + np.where(m, 255 - K if up else K, ys), axis=0, kind="stable")   # stretches stay where they are; inside a bright one, by brightness
    def gather(f, idx):   # the frame with each block taken from where idx says
        b = f.reshape(gh, g, gw, g, 3).transpose(0, 2, 1, 3, 4); return np.ascontiguousarray(b[idx, cols].transpose(0, 2, 1, 3, 4)).reshape(H, W, 3)
    Pa, Pb = read(a0), read(land)
    def frame(j, A, B):
        if j < n1: return gather(A, state(Pa, SORT_MOST * ease((j + 1) / n1)))
        if j < n1 + nc:
            m = ease((j - n1 + 1) / (nc + 1)); return np.rint(gather(A, state(Pa, SORT_MOST)) * (1 - m) + gather(B, state(Pb, SORT_MOST)) * m).astype(np.uint8)
        return gather(B, state(Pb, SORT_MOST * ease(1 - (j - n1 - nc + 1) / (n2 + 1))))
    return frame

def _motion(cur, prev, W, H, k=4, win=25):
    """Where each place of picture `cur` was in picture `prev`, as (dx, dy) in pixels at full size (dense optical flow on the
    Y planes at 1/k size)."""
    s = lambda f: cv2.resize(f[..., 0], (W // k, H // k), interpolation=cv2.INTER_AREA)
    return cv2.calcOpticalFlowFarneback(s(cur), s(prev), None, 0.5, 4, win, 3, 7, 1.5, 0) * k

def mosh_flight(a0, land, px, seed, nb):
    """ "style": "mosh": the thread ripper effect. Datamoshing, as when a video's key frame is dropped and the player keeps moving the old picture with
    motion that isn't its own. Nothing stops and nothing fades. The picture on screen is carried from frame to
    frame, block by block (MOSH_BLOCK): each frame it is moved by a motion and given only what that motion doesn't explain.
    At first both come from the outgoing shot, which so keeps playing, its movement more and more overdone ("streak": how
    much), so it smears on its own motion. Then the incoming shot's movement takes the picture over and what changes in the
    incoming shot paints itself in. Last, over the flight's "decay" seconds and still moving, the picture is pulled into the
    incoming shot, gently at first and never in a step, while the overdoing eases off, so it ends as that shot exactly. It needs movement: over a still nothing would happen (a collage
    can't use it), and it is at its best into a shot that really moves."""
    H, W = a0.shape[:2]; gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32)); amp = 1.0 + px["streak"]
    st = dict(c=a0.astype(np.float32), a=a0, b=None); ramp = lambda u: (lambda v: v * v * (3 - 2 * v))(min(1.0, max(0.0, u)))
    def carried(cur, prev):   # how the picture moved from prev to cur (one motion a block, as a codec keeps it), and what that motion doesn't explain
        fl = _motion(cur, prev, W, H)
        fl = cv2.resize(cv2.resize(fl, (W // MOSH_BLOCK, H // MOSH_BLOCK), interpolation=cv2.INTER_AREA), (W, H), interpolation=cv2.INTER_NEAREST)
        return fl, cur.astype(np.float32) - cv2.remap(prev.astype(np.float32), gx + fl[..., 0], gy + fl[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    nd = int(round(px["decay"] * FPS)); clock = (nb - nd) / MOSH_SETTLE          # frames of settling at the end; the frames the mosh's own clock counts as 1
    left = lambda t: (1 - ramp((t - (nb - nd)) / (nd + 1))) ** 1.5               # how much of the mosh is still on the picture at frame t: all of it until the decay, none at its end
    def frame(j, A, B):
        t = j + 1; u = t / clock; w = ramp((u - 0.15) / (MOSH_OVER - 0.15))      # whose movement carries the picture: 0 the outgoing shot's, 1 the incoming one's
        over = 1 + (amp - 1) * ramp(u / 0.2) * (1 - ramp((t - (nb - nd)) / (nd + 1)))   # how overdone the movement is: none at the start, easing off all through the decay
        fa, ra = carried(A, st["a"]) if w < 1 else (0.0, 0.0); fb, rb = carried(B, st["b"] if st["b"] is not None else B) if w > 0 else (0.0, 0.0)
        fl = (1 - w) * fa + w * fb
        c = cv2.remap(st["c"], gx + over * fl[..., 0], gy + over * fl[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE) + (1 - w) * ra + w * rb
        c += (B.astype(np.float32) - c) * (1 - left(t) / left(t - 1) if left(t - 1) > 0 else 1.0)   # pulled toward the incoming shot by just what the decay's curve asks of this frame
        st.update(c=np.clip(c, 0, 255), a=A, b=B)
        return np.rint(st["c"]).astype(np.uint8)
    return frame

def pixel_flight(a0, land, px, seed, nb):
    """One flight from the outgoing picture (a0: as it stands at the cut) to the incoming one (land: as it will stand when the
    flight ends), in the recipe's "style": a function (j, A, B) -> frame j of nb, asked for in order, A and B being the two
    shots as they are playing at that frame (the outgoing one held once its run-on is used up)."""
    if px["style"] == "sort": return sort_flight(a0, land, px, seed, nb)
    if px["style"] == "mosh": return mosh_flight(a0, land, px, seed, nb)
    P = pixel_plan(a0, land, px, seed, 1 / (nb + 1)); return lambda j, A, B: pixel_frame(P, (j + 1) / (nb + 1), A, B)

def pixel_room(r, cuts, total, nb):
    """Every picture after the first is whole for at least half a second once its pixels have landed, or the recipe is refused,
    with which picture it is and what to change (cuts, total, nb: in frames)."""
    for i, (a, b) in enumerate(zip(cuts, cuts[1:] + [total]), 2):
        if b - a - nb < FPS // 2:
            raise ValueError('%s: "pixels": %s %d is on for %.2fs and the flight that forms it takes %.2fs, so it is never whole for the half second it needs: %s %.2f or more%s'
                             % (r.get("id"), "shot" if r.get("kind") == "video" else "still", i, (b - a) / FPS, nb / FPS, 'its "dur"' if r.get("kind") == "video" else '"sec_per_still"',
                                (nb + FPS // 2) / FPS, ', or "fly" %.2f or less' % ((b - a - FPS // 2) / FPS) if b - a - FPS // 2 >= 2 else ""))

def pixel_shots(r, parts, W, H, total, body):
    """A video's r["pixels"]: each shot turns into the next by its own pixels. From each cut the outgoing
    shot's pixels lift off as grains (`size` pixels across) and fly for `fly` seconds to where they fit the incoming shot
    (pixel_plan: as it will stand when the last one lands). Nothing stops for it: the outgoing shot plays on under its
    flying pixels (parts carry that run-on: run_on) and the incoming one is playing from its cut (pixel_frame). So a shot is
    seen forming over its first `fly` seconds, whole from then to its cut, and coming apart for `fly` seconds after: the
    moment a shot is for goes after `fly`, and its source runs clean for `fly` past its cut. On a song, `fly` in whole beats
    puts the lift-off and the landing both on the beat. Free Resolve can't do it: its version gets a marker."""
    px = pixels_of(r); nb = int(round(px["fly"] * FPS)); n = [int(round(s["dur"] * FPS)) for s in r["shots"]]
    cuts = [int(c) for c in np.cumsum(n)[:-1]]; pixel_room(r, cuts, int(round(total * FPS)), nb)
    def join(A, part, off, seed):   # the edit so far (its last shot running on past its cut at frame `off`), then the next shot
        B = yuv_frames(part, W, H); k = 0; a = None
        for x in A:
            if k >= off: a = x; break   # the outgoing shot's frame on the cut: from here it runs on under the flight
            yield x; a = x; k += 1
        land = next(yuv_frames(part, W, H, at=nb), None)
        if a is None or land is None: yield from B; return
        fl = pixel_flight(a, land, px, seed, nb)
        for j in range(nb):
            b = next(B, None)
            if b is None: return
            yield fl(j, a, b); a = next(A, a)   # (its last frame held once its run-on is used up)
        yield from B
    st = yuv_frames(parts[0], W, H)
    for i in range(1, len(parts)): st = join(st, parts[i], cuts[i - 1], i)
    return yuv_write(st, parts[0], W, H, total, body)

def pixel_pass(r, body, W, H, dur, tmp):
    """A collage's r["pixels"] (a video's: pixel_shots): the same flight between its stills, over the cut body. From each cut
    the outgoing still's pixels fly for `fly` seconds into the incoming one, which is whole from then to its own cut."""
    px = pixels_of(r); nb = int(round(px["fly"] * FPS)); cuts = [int(round(c * FPS)) for c in cut_times(r)]   # in frames, as the body was made
    if not cuts: print('  %s: "pixels" on one still: it has nothing to turn into, so it keeps its full time and is rendered plain' % r.get("id")); return body
    pixel_room(r, cuts, int(round(dur * FPS)), nb)
    def frames():
        start = {c: i for i, c in enumerate(cuts)}; fl = last = None; f0 = 0
        for f, fr in enumerate(yuv_frames(body, W, H)):
            if f in start: A = last; fl = pixel_flight(A, fr, px, start[f], nb); f0 = f   # `last`: the outgoing still
            yield fl(f - f0, A, fr) if fl is not None and f - f0 < nb else fr
            last = fr
    return yuv_write(frames(), body, W, H, dur, os.path.join(tmp, "pixels.mp4"))

def frames_and_sound(p):
    """(video frames, seconds of sound) in a finished file."""
    o = json.loads(subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-show_entries", "stream=codec_type,nb_read_packets,duration", "-of", "json", p],
                                  capture_output=True, text=True).stdout or "{}").get("streams", [])
    v, a = [next((x for x in o if x.get("codec_type") == k), {}) for k in ("video", "audio")]
    return int(v.get("nb_read_packets") or 0), float(a.get("duration") or 0)

def render(path):
    r = json.load(open(path)); W, H = size(r["format"]); idx = load_index()
    if r["kind"] == "collage":
        for i, st in enumerate(r.get("stills") or []):
            if not (isinstance(st, dict) and isinstance(st.get("file"), str)):
                raise ValueError('stills[%d] needs "file": an image path inside library/ (lab_still.grab(...)["file"], a pin, a photo)' % i)
            if not os.path.exists(os.path.join(LIB, st["file"])): raise ValueError("stills[%d]: no such file %s" % (i, st["file"]))
    tmp = tempfile.mkdtemp(prefix="lab_"); part = None
    try:
        body, dur = render_video(r, W, H, tmp, idx) if r["kind"] == "video" else render_collage(r, W, H, tmp, True)
        if r["kind"] != "video" and pixels_of(r): body = pixel_pass(r, body, W, H, dur, tmp)   # (a video's: in render_video, where each shot's run-on is)
        if r.get("layers"): body = layer_pass(r, body, W, H, dur, tmp, idx)
        if r.get("fx"): body, _ = fx_pass(r, body, W, H, dur, tmp)
        if r.get("sparkles"): body = sparkles_pass(r, body, W, H, dur, tmp)
        if r.get("doodles"): body = doodle_pass(r, body, W, H, dur, tmp)
        cap = os.path.join(tmp, "cap.png"); caption_png(r.get("caption", ""), W, H, cap)
        lyr = lyric_frames(r, W, H, dur, tmp) if (r.get("lyrics") and SONGS.block(r)) else None
        out = os.path.join(os.path.dirname(os.path.abspath(path)), r["id"] + ".mp4"); part = out[:-4] + ".part.mp4"
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", body]
        if SONGS.block(r): cmd += ["-ss", "%.3f" % SONGS.block(r)["start"], "-t", "%.3f" % dur, "-i", audio_of(r)[0]]
        # the caption and lyric frames are read inside the graph (movie=), not as inputs beside the video: an image input racing the
        # video dropped frames or paired them with the wrong lyric frame, differently every run (R07_1_07 went out with 279 of 281)
        def mv(p):
            if "'" in p or ":" in p: raise ValueError("the temp folder's path has a quote or a colon: %s" % p)
            return "movie='%s'" % p
        fc = mv(cap) + "[c];" + ("%s:f=image2,settb=1/%d,setpts=N[l];" % (mv(lyr), FPS) if lyr else "") + "[0:v][c]overlay=0:0[v0]" + (";[v0][l]overlay=0:0:eof_action=pass[v1]" if lyr else "")
        cmd += ["-filter_complex", fc, "-map", "[v1]" if lyr else "[v0]"]
        if SONGS.block(r): cmd += ["-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-af", "afade=t=out:st=%.3f:d=%g" % (max(0, dur - FADE_OUT), FADE_OUT)]
        cmd += enc(True) + ["-t", "%.3f" % dur, "-movflags", "+faststart", part]
        want = int(round(dur * FPS))
        for n in range(4):   # every frame and all the sound, or it's made again (3 more tries); a bad file is never left looking finished
            subprocess.run(cmd, check=True, timeout=180); got, snd = frames_and_sound(part)
            if got == want and (not SONGS.block(r) or abs(snd - dur) < 0.05): break
            if n < 3: print("  %s: %d of %d frames, sound %.2f of %.2fs: making it again" % (r["id"], got, want, snd, dur))
        else:
            raise RuntimeError("%s came out with %d of %d frames, sound %.2f of %.2fs, 4 times: not saved" % (r["id"], got, want, snd, dur))
        os.replace(part, out)
        print("rendered", out, "%.2fs" % dur)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if part and os.path.exists(part): os.remove(part)   # a failed or stopped render leaves nothing that looks made

if __name__ == "__main__":
    # several recipes at once (a round renders in about a third of the time); each failure is reported, the rest still render
    from concurrent.futures import ThreadPoolExecutor
    def one(p):
        try:
            r = json.load(open(p))
            if not isinstance(r, dict) or "kind" not in r or "id" not in r: return None   # the round's manifest.json / review.json: not a recipe
            render(p); return None
        except Exception as e: return "%s: %s" % (os.path.basename(p), e)
    with ThreadPoolExecutor(max(1, min(4, (os.cpu_count() or 4) // 3))) as ex:
        bad = [b for b in ex.map(one, sys.argv[1:]) if b]
    if bad: sys.exit("render failed:\n  " + "\n  ".join(bad))
