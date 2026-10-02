#!/usr/bin/env python3
"""A sound to cut to, taken from a TikTok: one already downloaded, or a link.
  python3 _scripts/Sound_Pull.py <clip key | file in library | tiktok link> [--name short_name]
Saves the audio to library/_audio/sounds/<name>.m4a (no re-encode when it can), estimates its tempo, draws its
waveform (…_map.png: big sections look big, drops turn dense), and records it in library/_audio/sounds.json
{name: {file, from, url, title, duration, bpm}}. Use it in a recipe's song block:
  "song": {"file": "_audio/sounds/<name>.m4a", "start": <seconds>}     (plus "lyrics_timing" if you time its words)
beat_map.py --audio library/_audio/sounds/<name>.m4a <start> <end> --cuts … shows its kicks and snares."""
import json, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, edit_json  # noqa: E402

OUT = os.path.join(LIB, "_audio", "sounds"); INDEX = os.path.join(LIB, "_audio", "sounds.json")


def find_clip(ref):
    """A clip key from the indexes, or a path (absolute or inside library/)."""
    if os.path.exists(ref): return os.path.abspath(ref), None
    if os.path.exists(os.path.join(LIB, ref)): return os.path.join(LIB, ref), None
    for rel in ("_reference/clip_index.json", "_lab/lab_index.json"):
        p = os.path.join(LIB, rel)
        if not os.path.exists(p): continue
        clips = json.load(open(p))["clips"]
        keys = [k for k in clips if k.lstrip(".").startswith(ref.lstrip("."))]
        if len(keys) == 1: return os.path.join(LIB, clips[keys[0]]["file"]), clips[keys[0]]
    sys.exit("can't find %r: give a clip key from library_view.py, a file path, or a TikTok link" % ref)


def main():
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    name = a[a.index("--name") + 1] if "--name" in a else None
    ref = a[0]; os.makedirs(OUT, exist_ok=True); info = {}; entry = None
    if ref.startswith("http"):
        # saved straight under its name, and its path is the one yt-dlp prints once the file is in place (already there: the
        # same path): never "the newest .m4a in the folder", which renamed and claimed another sound's file
        tmpl = os.path.join(OUT, (name.replace("%", "%%") if name else "%(uploader)s_%(id)s") + ".%(ext)s")
        r = subprocess.run(["python3", "-m", "yt_dlp", "--impersonate", "chrome", "-q", "--no-warnings", "--write-info-json", "-x",
                            "--audio-format", "m4a", "--no-simulate", "--print", "after_move:filepath", "-o", tmpl, ref], capture_output=True, text=True, timeout=300)
        got = [l.strip() for l in r.stdout.splitlines() if l.strip().lower().endswith(".m4a")]
        if r.returncode or not got or not os.path.exists(got[-1]): sys.exit("download failed: " + ((r.stderr or r.stdout).strip()[-300:] or "the downloader named no .m4a file"))
        dst = os.path.abspath(got[-1]); j = dst[:-4] + ".info.json"; info = json.load(open(j)) if os.path.exists(j) else {}
        url = info.get("webpage_url") or ref; frm = "tiktok link"
    else:
        path, entry = find_clip(ref)
        base = name or re.sub(r"\W+", "_", os.path.splitext(os.path.basename(path))[0]).strip("_")
        dst = os.path.join(OUT, base + ".m4a")
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-c:a", "copy", dst], capture_output=True, text=True)
        if r.returncode:   # not AAC inside: re-encode
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-c:a", "aac", "-b:a", "256k", dst], capture_output=True, text=True)
        if r.returncode or not os.path.exists(dst): sys.exit("no audio in %s" % path)
        j = os.path.splitext(path)[0] + ".info.json"; info = json.load(open(j)) if os.path.exists(j) else {}
        url = (entry or {}).get("catalog", {}).get("url") or info.get("webpage_url") or ""; frm = os.path.relpath(path, LIB)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", dst],
                               capture_output=True, text=True).stdout.strip() or 0)
    from beat_map import estimate_bpm
    bpm = estimate_bpm(dst)
    subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "beat_map.py"), "--song", "--audio", dst,
                    "--out", os.path.splitext(dst)[0] + "_map.png"], capture_output=True)
    key = os.path.splitext(os.path.basename(dst))[0]
    title = " - ".join(x for x in (info.get("track"), info.get("artist")) if x) or ((entry or {}).get("catalog") or {}).get("sound") or (info.get("description") or "")[:80]
    e = dict(file=os.path.relpath(dst, LIB), map=os.path.relpath(os.path.splitext(dst)[0] + "_map.png", LIB), **{"from": frm}, url=url,
             title=title, duration=round(dur, 2), bpm=bpm)
    with edit_json(INDEX) as idx: idx[key] = e   # only this sound's entry
    print("%s: %.1f s, ~%s BPM (an estimate: slow songs can read double or half; check by ear) -> %s" % (key, dur, bpm, os.path.relpath(dst, LIB)))
    print('use it: "song": {"file": "%s", "start": <seconds>}; its waveform: %s' % (os.path.relpath(dst, LIB), e["map"]))


if __name__ == "__main__": main()
