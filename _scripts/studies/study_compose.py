"""Study step 2: composition + on-screen text for every study video that has a local file.
Cuts (flashes filtered), frame-1 look, bars, ending, OCR (macOS Vision) on frames at
0, .5, 1, 1.5, 2, 3s then every 1s. Contact sheet per video in sheets/. Output compose.jsonl (resumable)."""
import json, os, glob, subprocess, sys, tempfile, shutil, re
import numpy as np, cv2

SCRIPT = os.path.dirname(os.path.abspath(__file__))
import sys; sys.path.insert(0, os.path.dirname(SCRIPT)); from sandbox_paths import LIB, layout  # library/
HERE = os.environ.get('STUDY_DIR') or os.path.join(LIB, '_lab', 'studies', '01_content_library')  # the study's folder (set STUDY_DIR for other studies)
OUT = os.path.join(HERE, 'compose.jsonl')
SHEETS = os.path.join(HERE, 'sheets'); os.makedirs(SHEETS, exist_ok=True)
OCR = os.path.join(SCRIPT, 'ocr')

def ensure_ocr():   # built on first use, not in setup (swiftc takes minutes on a fresh Mac and prints nothing)
    if os.access(OCR, os.X_OK): return
    print('building the OCR helper (first time only, a few minutes)...', flush=True)
    subprocess.run(['swiftc', '-O', '-o', OCR, OCR + '.swift'], check=True)

def find_file(vid, hint):
    if hint and os.path.exists(os.path.join(LIB, hint)): return os.path.join(LIB, hint)
    for d in [*(layout().get('source_folders') or []), '01_videos', '02_collages', '03_own', '04_alt', '_external content', '_pasture', '_lab/source',
              '_captions', '_inbox', '_reference/edits', '_lab/studies/03_fyp/feed/videos', os.path.relpath(os.path.join(HERE, 'feed', 'videos'), LIB), os.path.relpath(os.path.join(HERE, '_tmp_vids'), LIB)]:
        for f in sorted(glob.glob(f'{LIB}/{d}/**/*{vid}*.mp4', recursive=True)):
            if '_cfr30' not in f: return f
    return None

def probe(f):
    j = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'stream=width,height,r_frame_rate:stream_side_data=rotation:format=duration', '-of', 'json', f],
        capture_output=True, text=True).stdout)
    s = j['streams'][0]; w, h = s['width'], s['height']
    rot = abs(int(float((s.get('side_data_list') or [{}])[0].get('rotation', 0) or 0)))
    if rot in (90, 270): w, h = h, w
    return float(j['format']['duration']), w, h

def small_frames(f, W=96, H=170, FPS=30):
    # constant 30fps sampling in real time (handles VFR); frame i sits at i/FPS
    p = subprocess.run(['ffmpeg', '-v', 'error', '-i', f, '-vf', f'fps={FPS},scale={W}:{H}', '-f', 'rawvideo',
                        '-pix_fmt', 'bgr24', '-'], capture_output=True)
    a = np.frombuffer(p.stdout, np.uint8)
    n = len(a) // (W * H * 3)
    return a[:n * W * H * 3].reshape(n, H, W, 3), np.arange(n) / FPS

def hist(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hh = cv2.calcHist([hsv], [0, 1, 2], None, [12, 6, 6], [0, 180, 0, 256, 0, 256])
    return cv2.normalize(hh, hh).flatten()

def cuts_of(fr, ts):
    hs = [hist(x) for x in fr]
    d = np.array([0] + [cv2.compareHist(hs[i - 1], hs[i], cv2.HISTCMP_BHATTACHARYYA) for i in range(1, len(hs))])
    g = fr.mean(axis=3)
    pd = np.array([0] + [np.abs(g[i] - g[i - 1]).mean() for i in range(1, len(g))])
    cuts, flashes = [], []
    for i in range(1, len(d)):
        if not (d[i] > 0.33 and pd[i] > 18 and d[i] == d[max(0, i - 2):i + 3].max()): continue
        a, b = max(0, i - 4), min(len(hs) - 1, i + 5)
        persist = cv2.compareHist(hs[a], hs[b], cv2.HISTCMP_BHATTACHARYYA)
        (cuts if persist > 0.25 else flashes).append(round(float(ts[i]), 3))
    out = []
    for c in cuts:
        if not out or c - out[-1] > 0.12: out.append(c)
    return out, flashes

def bars(frames):
    g = np.mean([cv2.cvtColor(x, cv2.COLOR_BGR2GRAY) for x in frames], axis=0)
    rows, cols = g.mean(axis=1), g.mean(axis=0)
    H, W = g.shape
    top = next((i for i in range(H) if rows[i] > 14), 0); bot = next((i for i in range(H) if rows[H - 1 - i] > 14), 0)
    lef = next((i for i in range(W) if cols[i] > 14), 0); rig = next((i for i in range(W) if cols[W - 1 - i] > 14), 0)
    return round((top + bot) / H, 3), round((lef + rig) / W, 3)

def band(y):
    return 'top' if y < 0.33 else ('mid' if y < 0.66 else 'bottom')

def run(v):
    f = find_file(v['id'], v.get('file'))
    if not f: return {'id': v['id'], 'has_file': False}
    dur, w, h = probe(f)
    fr, ts = small_frames(f)
    cuts, flashes = cuts_of(fr, ts) if len(fr) > 5 else ([], [])
    times = [t for t in [0, 0.5, 1, 1.5, 2, 3] if t < dur] + [float(t) for t in range(4, int(dur) + 1) if t < dur - 0.05]
    tmp = tempfile.mkdtemp()
    paths = []
    for t in times:
        p = os.path.join(tmp, f'{t:06.2f}.jpg')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(t + (0.04 if t == 0 else 0)), '-i', f, '-frames:v', '1',
                        '-vf', 'scale=720:-2', '-q:v', '3', p])
        if os.path.exists(p): paths.append((t, p))
    big = [(t, cv2.imread(p)) for t, p in paths]
    big = [(t, im) for t, im in big if im is not None]
    # OCR
    ocr = {}; faces = {}
    if big:
        r = subprocess.run([OCR] + [p for t, p in paths if os.path.exists(p)], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            j = json.loads(line); t = float(os.path.basename(j['img'])[:-4])
            keep = [dict(text=x['text'], band=band(x['y'] + x['h'] / 2), h=round(x['h'], 3)) for x in j['lines']
                    if x['conf'] >= 0.5 and len(x['text'].strip()) >= 2 and not re.search(r'tiktok|@\w', x['text'], re.I)]
            if keep: ocr[t] = keep
            faces[t] = [x for x in j.get('faces', []) if x['conf'] > 0.6]
    # frame-1 look
    f0 = big[0][1] if big else None
    look = {}
    if f0 is not None:
        g0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(f0, cv2.COLOR_BGR2HSV)
        fc = faces.get(big[0][0], [])
        look = dict(f0_luma=round(float(g0.mean()), 1), f0_contrast=round(float(g0.std()), 1),
                    f0_sat=round(float(hsv[..., 1].mean()), 1), f0_faces=len(fc), f0_face_area=round(max([x['w'] * x['h'] for x in fc], default=0), 3),
                    face_frames_first3s=sum(1 for t in faces if t <= 3 and faces[t]), face_frames=sum(1 for t in faces if faces[t]), n_samples=len(big))
    # motion in first 0.5s and 1s (mean abs diff of small frames)
    def mot(t1):
        i1 = int(np.searchsorted(ts, t1)); i1 = min(i1, len(fr) - 1)
        return round(float(np.abs(fr[i1].astype(float) - fr[0].astype(float)).mean()), 1) if len(fr) else None
    tb, lr = bars([fr[i] for i in np.linspace(0, len(fr) - 1, min(12, len(fr))).astype(int)]) if len(fr) else (0, 0)
    last = fr[-3] if len(fr) > 3 else None
    end_luma = round(float(last.mean()), 1) if last is not None else None
    loop = round(float(cv2.compareHist(hist(fr[1]), hist(last), cv2.HISTCMP_BHATTACHARYYA)), 3) if last is not None else None
    # sheet
    if big:
        tw = 180; cells = []
        for t, im in big:
            c = cv2.resize(im, (tw, int(im.shape[0] * tw / im.shape[1])))
            cv2.rectangle(c, (0, 0), (52, 18), (0, 0, 0), -1)
            cv2.putText(c, f'{t:.1f}s', (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            cells.append(c)
        ch = max(c.shape[0] for c in cells); cells = [cv2.copyMakeBorder(c, 0, ch - c.shape[0], 0, 0, cv2.BORDER_CONSTANT) for c in cells]
        per = 8; rows = []
        for i in range(0, len(cells), per):
            row = cells[i:i + per]; row += [np.zeros_like(cells[0])] * (per - len(row)); rows.append(np.hstack(row))
        cv2.imwrite(os.path.join(SHEETS, f"{v['id']}.jpg"), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 80])
    shutil.rmtree(tmp, ignore_errors=True)
    shots = np.diff([0] + [c for c in cuts if c < dur] + [dur])
    return {'id': v['id'], 'has_file': True, 'file': os.path.relpath(f, LIB), 'dur': round(dur, 2), 'w': w, 'h': h,
            'cuts': cuts, 'n_cuts': len(cuts), 'flashes': len(flashes),
            'avg_shot': round(float(shots.mean()), 2), 'first_cut': cuts[0] if cuts else None,
            'cuts_in_first_3s': sum(1 for c in cuts if c < 3),
            'bars_tb': tb, 'bars_lr': lr, **look, 'motion_0_5': mot(0.5), 'motion_1': mot(1.0),
            'end_luma': end_luma, 'loop_dist': loop, 'ocr': ocr}

def safe_run(v):
    try: return run(v)
    except Exception as ex: return {'id': v['id'], 'has_file': None, 'err': repr(ex)}

if __name__ == '__main__':
    ensure_ocr()
    vids = {}
    for l in open(os.path.join(HERE, 'meta.jsonl')):
        j = json.loads(l); vids[j['id']] = j
    done = set()
    if os.path.exists(OUT):
        done = {json.loads(l)['id'] for l in open(OUT)}
    todo = [v for k, v in vids.items() if k not in done]
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else len(todo)
    from multiprocessing import Pool
    with open(OUT, 'a') as o, Pool(4) as pool:
        for r in pool.imap_unordered(safe_run, todo[:lim]):
            v = r
            o.write(json.dumps(r) + '\n'); o.flush()
            print(v['id'], r.get('has_file'), r.get('n_cuts'), len(r.get('ocr', {})), flush=True)
