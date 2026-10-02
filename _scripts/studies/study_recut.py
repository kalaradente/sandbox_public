"""Study step 2b: cut detection v2. Same candidates as study_compose.py, then a structure test:
if the edges before and after a candidate still line up, it's a light change (strobe/flash), not a cut.
Writes cuts2.jsonl {id, cuts, light_changes}. Usage: study_recut.py [id ...]"""
import json, os, sys
import numpy as np, cv2
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from study_compose import small_frames, hist, find_file, HERE

def edges(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = (g - g.mean()) / (g.std() + 1e-3)  # light-invariant
    e = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return cv2.GaussianBlur(e, (5, 5), 0)

def ncc(a, b):
    a = a - a.mean(); b = b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-6))

def cuts2(fr, ts):
    hs = [hist(x) for x in fr]
    d = np.array([0] + [cv2.compareHist(hs[i - 1], hs[i], cv2.HISTCMP_BHATTACHARYYA) for i in range(1, len(hs))])
    g = fr.mean(axis=3)
    pd = np.array([0] + [np.abs(g[i] - g[i - 1]).mean() for i in range(1, len(g))])
    cuts, light = [], []
    for i in range(1, len(d)):
        if not (d[i] > 0.33 and pd[i] > 18 and d[i] == d[max(0, i - 2):i + 3].max()): continue
        a, b = max(0, i - 4), min(len(hs) - 1, i + 5)
        persist = cv2.compareHist(hs[a], hs[b], cv2.HISTCMP_BHATTACHARYYA)
        # structure before vs after (best of a few frame pairs, so a one-frame flash can't fool it)
        s = max(ncc(edges(fr[x]), edges(fr[y])) for x in (max(0, i - 2), i - 1) for y in (i, min(len(fr) - 1, i + 2)))
        if persist > 0.25 and s < 0.45: cuts.append(round(float(ts[i]), 3))
        else: light.append(round(float(ts[i]), 3))
    out = []
    for c in cuts:
        if not out or c - out[-1] > 0.12: out.append(c)
    return out, light

def one(v):
    f = find_file(v['id'], v.get('file'))
    if not f: return {'id': v['id'], 'has_file': False}
    fr, ts = small_frames(f)
    c, l = cuts2(fr, ts) if len(fr) > 5 else ([], [])
    return {'id': v['id'], 'cuts': c, 'light_changes': len(l)}

if __name__ == '__main__':
    vids = {}
    for l in open(os.path.join(HERE, 'meta.jsonl')):
        j = json.loads(l); vids[j['id']] = j
    ids = sys.argv[1:]
    if ids:
        for i in ids:
            r = one(vids[i]); print(i, r)
    else:
        out = os.path.join(HERE, 'cuts2.jsonl')
        done = {json.loads(l)['id'] for l in open(out)} if os.path.exists(out) else set()
        with open(out, 'a') as o, Pool(3) as p:
            for r in p.imap_unordered(one, [v for k, v in vids.items() if k not in done]):
                o.write(json.dumps(r) + '\n'); o.flush()
