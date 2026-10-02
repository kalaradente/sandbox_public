"""Overview mosaics for typing videos by eye: 16 videos per image, 5 frames each (0,1,2,3,5s) from sheets/.
Usage: study_mosaic.py out_prefix id1 id2 ..."""
import sys, os, json, cv2, numpy as np
SCRIPT = os.path.dirname(os.path.abspath(__file__))
import sys; sys.path.insert(0, os.path.dirname(SCRIPT)); from sandbox_paths import LIB  # library/
HERE = os.environ.get('STUDY_DIR') or os.path.join(LIB, '_lab', 'studies', '01_content_library')  # the study's folder (set STUDY_DIR for other studies)
meta = {}
for l in open(os.path.join(HERE, 'meta.jsonl')):
    j = json.loads(l); meta[j['id']] = j
comp = {}
for l in open(os.path.join(HERE, 'compose.jsonl')):
    j = json.loads(l)
    if j.get('has_file'): comp[j['id']] = j
out, ids = sys.argv[1], [i for i in sys.argv[2:] if i in comp]
CW, CH, SEL = 100, 178, [0, 2, 4, 5, 7]  # cell indexes in the sheet: 0,1,2,3,5s
tiles = []
for i in ids:
    p = os.path.join(HERE, 'sheets', f'{i}.jpg')
    im = cv2.imread(p)
    if im is None: continue
    cw = im.shape[1] // 8
    n_rows = -(-comp[i]['n_samples'] // 8)
    cell_h = im.shape[0] // n_rows
    cells = []
    for k in SEL:
        r, c = divmod(k, 8)
        if r >= n_rows: cells.append(np.zeros((CH, CW, 3), np.uint8)); continue
        cell = im[r * cell_h:(r + 1) * cell_h, c * cw:(c + 1) * cw]
        s = min(CW / cell.shape[1], CH / cell.shape[0])
        cell = cv2.resize(cell, (max(1, int(cell.shape[1] * s)), max(1, int(cell.shape[0] * s))))
        pad = np.zeros((CH, CW, 3), np.uint8)
        y, x = (CH - cell.shape[0]) // 2, (CW - cell.shape[1]) // 2
        pad[y:y + cell.shape[0], x:x + cell.shape[1]] = cell
        cells.append(pad)
    row = np.hstack(cells)
    lab = np.zeros((20, row.shape[1], 3), np.uint8)
    cv2.putText(lab, f"{i[-6:]} {meta[i]['creator'][:18]}", (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    tiles.append(np.vstack([lab, row]))
blank = np.zeros_like(tiles[0])
for n in range(0, len(tiles), 16):
    grp = tiles[n:n + 16]; grp += [blank] * (16 - len(grp))
    left = np.vstack(grp[0:8]); right = np.vstack(grp[8:16])
    sep = np.full((left.shape[0], 8, 3), 60, np.uint8)
    cv2.imwrite(f'{out}_{n // 16:02d}.jpg', np.hstack([left, sep, right]), [cv2.IMWRITE_JPEG_QUALITY, 80])
    print(f'{out}_{n // 16:02d}.jpg')
