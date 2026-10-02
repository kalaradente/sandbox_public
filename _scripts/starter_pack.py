#!/usr/bin/env python3
"""Starter pack: the footage + indexes as a zip on a public Box link (starter_pack.json holds the link and version).
setup.sh runs this; nobody needs to by hand.
  python3 _scripts/starter_pack.py status          installed version vs the one in starter_pack.json
  python3 _scripts/starter_pack.py url             the direct download URL for the Box share link
  python3 _scripts/starter_pack.py merge <zip>     add the pack to library/ (add-only, see below)
Merging never overwrites or deletes anything in library/. A clip already known to your indexes (including ones you
deleted = use "removed", or retired to the pasture) is never copied again; new clips are copied and their index
entries, stills and catalog rows are added, and clips they already have gain any index fields they were missing (never a
changed value). So a newer pack can be merged into a library someone is working in.
A merge stopped part way (Ctrl-C, a full disk, a killed command) leaves no cut-off file: each file is unpacked to .part and
renamed when whole. A cut-off copy an older merge left (the start of the pack's file, shorter) is the one thing it replaces;
anything else already there stays as it is. The pack is stamped installed only when every file of it on disk is whole."""
import csv, io, json, os, re, shutil, subprocess, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox_paths import ROOT, LIB, edit_json, append_csv, locked, write_atomic  # noqa: E402

CONF = os.path.join(ROOT, "starter_pack.json")
STAMP = os.path.join(LIB, ".starter_pack_version")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) sandbox-setup"
JSON_INDEXES = {"_reference/clip_index.json": "clips", "_lab/lab_index.json": "clips", "_stills/candidates/index.json": None}
CSV_CATALOGS = {"_reference/saves_catalog.csv": "url", "_lab/lab_catalog.csv": "url"}
SONGS = "_audio/song.json"   # merged by song: the pack's songs are added, theirs and their current song stay
CLIP_DIRS = ("_internal content/", "_external content/", "01_videos/source/", "02_collages/source/", "_pasture/", "_lab/source/")   # the old per-account folders: older packs
TEXT = (".txt", ".md", ".csv", ".json", ".srt", ".lrc")   # files a person may edit by hand (see cut_off)


def conf(): return json.load(open(CONF))


def installed(): return open(STAMP).read().strip() if os.path.exists(STAMP) else ""


def curl(args, timeout=60):
    """HTTP through macOS's own curl: it trusts the system certificates (python.org Python has none until
    'Install Certificates' is run, so urllib fails on fresh Macs)."""
    return subprocess.run(["curl", "-sSL", "--max-time", str(timeout), "-A", UA] + args, capture_output=True)


def direct_url(url):
    """A Box share link (https://<x>.box.com/s/<id>) -> a URL that returns the file itself. Other URLs pass through."""
    m = re.match(r"^(https://[\w.-]*box\.com)/s/(\w+)", url or "")
    if not m: return url
    host, sid = m.groups()
    for cand in (host + "/shared/static/" + sid, host + "/shared/static/" + sid + ".zip"):
        if looks_like_zip(cand): return cand
    html = curl([url]).stdout.decode("utf-8", "ignore")
    for fid in dict.fromkeys(re.findall(r'"typedID"\s*:\s*"f_(\d+)"', html) + re.findall(r'"itemID"\s*:\s*"?(\d+)', html) + re.findall(r"/file/(\d+)", html)):
        cand = "%s/index.php?rm=box_download_shared_file&shared_name=%s&file_id=f_%s" % (host, sid, fid)
        if looks_like_zip(cand): return cand
    raise SystemExit("Couldn't turn the Box link into a download. Check that it's a public link to the .zip file (anyone with the link can view and download).")


def looks_like_zip(url):
    r = curl(["-r", "0-3", url])
    return r.returncode == 0 and r.stdout[:4] == b"PK\x03\x04"


def cut_off(dst, z, info):
    """True when dst is a cut-off copy of the pack's file: shorter, and byte for byte its start. A file of the person's own
    (anything else) is never one. A text file someone shortened by hand is also the start of the pack's, so of those only an
    empty one, or JSON that doesn't load, counts (a small file is written in one go: cut off, it's empty)."""
    n = os.path.getsize(dst)
    if n >= info.file_size: return False
    if n and dst.lower().endswith(TEXT):
        if not dst.lower().endswith(".json"): return False
        try: json.load(open(dst, encoding="utf-8")); return False
        except ValueError: pass
    with open(dst, "rb") as a, z.open(info) as b:
        while n > 0:
            x = a.read(min(n, 1 << 20)); y = b.read(len(x))
            if not x or x != y: return False
            n -= len(x)
    return True


def unpack(z, n, dst):
    """The pack's file n to dst, whole or not at all: written to .part (zipfile checks its CRC at the end), then renamed."""
    os.makedirs(os.path.dirname(dst), exist_ok=True); part = dst + ".part"
    try:
        with z.open(n) as src, open(part, "wb") as out: shutil.copyfileobj(src, out, 1 << 20)
        os.replace(part, dst)
    except BaseException:
        try: os.remove(part)
        except OSError: pass
        raise


def merge(zpath):
    z = zipfile.ZipFile(zpath)
    names = [n for n in z.namelist() if n.startswith("library/") and not n.endswith("/")]
    pack = lambda rel: z.read("library/" + rel)
    have = lambda rel: os.path.exists(os.path.join(LIB, rel))
    load = lambda rel, d: json.load(open(os.path.join(LIB, rel))) if have(rel) else d
    ci, li, si = load("_reference/clip_index.json", {"clips": {}})["clips"], load("_lab/lab_index.json", {"clips": {}})["clips"], load("_stills/candidates/index.json", {})
    known = set(ci) | set(li)
    known_files = {os.path.basename(v.get("file", "")) for v in list(ci.values()) + list(li.values())}
    stills_known = {os.path.basename(r["file"]) for v in si.values() for r in v}
    own = set(JSON_INDEXES) | set(CSV_CATALOGS) | {".starter_pack_version", SONGS}   # merged by entry, below
    files = [(n, n[len("library/"):]) for n in names if n[len("library/"):] not in own]
    added = skipped = fixed = 0
    for n, rel in files:
        dst = os.path.join(LIB, rel)
        if have(rel):   # never overwritten, except a cut-off copy an older merge left (it may be in the index as usable already)
            if cut_off(dst, z, z.getinfo(n)): unpack(z, n, dst); fixed += 1; print("  replaced a cut-off copy: %s" % rel)
            continue
        base = os.path.basename(rel)
        key = os.path.splitext(base)[0]
        if rel.startswith(CLIP_DIRS) and (key in known or base in known_files): skipped += 1; continue   # known clip (maybe deleted or pastured): never re-add
        if rel.startswith("_stills/candidates/") and not rel.startswith("_stills/candidates/_culled/") and si and base not in stills_known and any(base.startswith(k.lstrip(".") + "_") for k in si): skipped += 1; continue
        unpack(z, n, dst)
        added += 1
    filled = 0
    for rel, key in JSON_INDEXES.items():   # add entries the library doesn't have yet (locked from the read to the write)
        if "library/" + rel not in z.namelist(): continue
        new = json.loads(pack(rel))
        with edit_json(os.path.join(LIB, rel)) as cur:
            if not cur: cur.update(new); n_new = len(new[key] if key else new)
            else:
                a, b = (cur[key], new[key]) if key else (cur, new)
                ks = [k for k in b if k not in a]; a.update({k: b[k] for k in ks}); n_new = len(ks)
                if key:   # clips they already have: add fields they're missing (e.g. a new one-liner), never change one they have
                    for k in b:
                        if k not in ks and isinstance(a.get(k), dict) and isinstance(b[k], dict):
                            miss = {f: v for f, v in b[k].items() if f not in a[k] and f != "needs_review"}
                            if a[k].get("needs_review") and not b[k].get("needs_review"):   # unreviewed here, reviewed in the pack: it counts as reviewed
                                if "source_type" in b[k]: miss["source_type"] = b[k]["source_type"]   # replaces the puller's guess; nothing else of theirs changes
                                a[k].pop("needs_review", None)
                            if miss: a[k].update(miss); filled += 1
        print("  %-32s +%d entries" % (rel, n_new))
    if "library/" + SONGS in z.namelist():   # songs: add the pack's songs they don't have; never change theirs or which is current
        new = json.loads(pack(SONGS))
        with edit_json(os.path.join(LIB, SONGS)) as cur:
            if not cur: cur.update(new); n_new = len(new.get("songs") or {})
            else:
                ks = [k for k in (new.get("songs") or {}) if k not in (cur.get("songs") or {})]
                cur.setdefault("songs", {}).update({k: new["songs"][k] for k in ks}); n_new = len(ks)
                for k, e in cur["songs"].items():   # a song they have whose file isn't on disk (e.g. an older pack left the audio out): take the pack's
                    pe = (new.get("songs") or {}).get(k) or {}
                    for f, v in pe.items():
                        if isinstance(v, str) and v.startswith("_") and os.path.exists(os.path.join(LIB, v)) and not (
                                isinstance(e.get(f), str) and os.path.exists(os.path.join(LIB, e[f]))): e[f] = v
                if not cur.get("current") or cur["current"] not in cur["songs"]: cur["current"] = new.get("current")
        print("  %-32s +%d songs" % (SONGS, n_new))
    for rel, key in CSV_CATALOGS.items():
        if "library/" + rel not in z.namelist(): continue
        rows = list(csv.DictReader(io.StringIO(pack(rel).decode("utf-8")))); p = os.path.join(LIB, rel)
        with locked(p):
            if have(rel): n_new = append_csv(p, rows, unique=lambda r: r.get(key))
            else: write_atomic(p, pack(rel)); n_new = len(rows)
        print("  %-32s +%d rows" % (rel, n_new))
    # stamped installed only when every file of the pack that's on disk is whole (one still cut off: this merge runs again)
    bad = [rel for n, rel in files if have(rel) and cut_off(os.path.join(LIB, rel), z, z.getinfo(n))]
    if bad: raise SystemExit("  %d file(s) still cut off (e.g. %s): the pack isn't stamped installed; run it again" % (len(bad), bad[0]))
    # stamp the version starter_pack.json asked for, so setup doesn't download again (the zip's own stamp is the fallback)
    ver = conf().get("version") or (z.read("library/.starter_pack_version").decode().strip() if "library/.starter_pack_version" in z.namelist() else "")
    write_atomic(STAMP, ver + "\n")
    print("  files: +%d new, %d cut-off copies replaced, %d known clips left alone; %d existing index entries got fields they were missing; starter pack %s installed" % (added, fixed, skipped, filled, ver))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        c = conf(); print("installed=%s available=%s url=%s" % (installed() or "none", c.get("version") or "-", "set" if c.get("url") else "missing"))
    elif cmd == "url": print(direct_url(conf().get("url", "")))
    elif cmd == "merge": merge(sys.argv[2])
    else: raise SystemExit(__doc__)
