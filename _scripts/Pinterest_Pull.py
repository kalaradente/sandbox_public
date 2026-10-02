#!/usr/bin/env python3
"""Pinterest boards -> stills for collages and photo slideshows.
  python3 _scripts/Pinterest_Pull.py [board_url ...]      default: the boards in library/profile.json "pinterest_boards"
Takes a board link or a single pin's link (pin.it short links too; public or shared by link; single pins go to pins/), downloads every pin it doesn't
have yet at full resolution into library/_stills/pinterest/<board>/ (gallery-dl; a download archive means nothing comes
down twice), and adds each new image to library/_stills/pinterest/index.json with its size, orientation, sharpness and
the pin's own title/description, marked needs_review. Then Claude looks at each image (a contact mosaic is fastest) and
writes its one-liner ("line": what you see — the vibe), like every clip. Collages and slideshows can use these files
directly: a still's "file" works in a collage recipe as it is.
A run stopped part way loses nothing: every image in the board's folder that the index doesn't have counts as new (the
download archive won't bring a pin down twice, so one downloaded before a stop is indexed from the folder), and the index
is saved after each pin. A pin cut off part way is never indexed: gallery-dl writes it to .part and renames it when done."""
import glob, json, os, re, subprocess, sys
import cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import LIB, IMAGE_EXT, profile, edit_json  # noqa: E402

OUT = os.path.join(LIB, "_stills", "pinterest")
INDEX = os.path.join(OUT, "index.json")


def resolve(url):
    """pin.it short links -> the board URL (without the invite query)."""
    r = subprocess.run(["curl", "-sL", "-o", "/dev/null", "-w", "%{url_effective}", url], capture_output=True, text=True, timeout=60)
    return (r.stdout.strip() or url).split("?")[0]


def board_name(url):
    if re.search(r"pinterest\.[a-z.]+/pin/", url): return "pins"   # single pins (e.g. emailed to drafts) share one folder
    m = re.search(r"pinterest\.[a-z.]+/([^/]+)/([^/?#]+)", url)
    return "%s_%s" % (m.group(1), m.group(2)) if m else re.sub(r"\W+", "_", url)[-40:]


def pull(url):
    url = resolve(url); name = board_name(url); d = os.path.join(OUT, name); os.makedirs(d, exist_ok=True)
    try:
        r = subprocess.run(["gallery-dl", "--download-archive", os.path.join(OUT, "archive.sqlite3"), "--write-metadata",
                            "-D", d, "-f", "{id}.{extension}", url], capture_output=True, text=True, timeout=1800)
        if r.returncode not in (0, 4): print("!! gallery-dl:", (r.stderr or r.stdout).strip()[-300:])
    except subprocess.TimeoutExpired:
        print("!! gallery-dl: still going after 30 min; indexing what came down (run again for the rest)")
    idx = json.load(open(INDEX)) if os.path.exists(INDEX) else {}   # new = any image here the index doesn't have (.part = still coming)
    new = sorted(f for f in os.listdir(d) if f.lower().endswith(IMAGE_EXT) and not f.startswith(".") and "%s/%s" % (name, f) not in idx)
    return name, url, [os.path.join(d, f) for f in new]


def facts(path):
    im = cv2.imread(path)
    if im is None: return {}
    h, w = im.shape[:2]; g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    s = 480 / min(h, w); g = cv2.resize(g, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    return dict(width=w, height=h, orientation="vertical" if h > w * 1.15 else "horizontal" if w > h * 1.15 else "square",
                sharpness=round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1))


def main():
    boards = sys.argv[1:] or profile().get("pinterest_boards") or []
    if not boards: return print('no board: pass a link, or add it to "pinterest_boards" in library/profile.json')
    for b in boards:
        name, url, new = pull(b)
        for p in new:
            meta = {}
            for m in glob.glob(p + ".json") + glob.glob(os.path.splitext(p)[0] + ".json"):
                try: meta = json.load(open(m)); break
                except ValueError: pass
            key = "%s/%s" % (name, os.path.basename(p))
            e = dict(file=os.path.relpath(p, LIB), board=url, pin_id=os.path.splitext(os.path.basename(p))[0],
                     title=(meta.get("title") or meta.get("grid_title") or "")[:120],
                     description=(meta.get("description") or meta.get("closeup_unified_description") or "")[:200],
                     **facts(p), needs_review=True)
            with edit_json(INDEX) as idx: idx.setdefault(key, e)   # saved pin by pin; another session's edits stay
        print("%s: %d new pins (%s)" % (name, len(new), url))
    idx = json.load(open(INDEX)) if os.path.exists(INDEX) else {}
    print("pinterest index: %d images, %d need a one-liner" % (len(idx), sum(1 for v in idx.values() if v.get("needs_review"))))


if __name__ == "__main__": main()
