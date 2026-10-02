"""Study step 3: merge meta + compose + cuts2 + types into study_results.csv, then compare
top vs bottom quarter within each content type. Prints the tables the findings are written from."""
import json, os, re, csv, math, time, collections, statistics as st

SCRIPT = os.path.dirname(os.path.abspath(__file__))
import sys; sys.path.insert(0, os.path.dirname(SCRIPT)); from sandbox_paths import LIB  # library/
HERE = os.environ.get('STUDY_DIR') or os.path.join(LIB, '_lab', 'studies', '01_content_library')  # the study's folder (set STUDY_DIR for other studies)
NOW = time.time()

def jl(name):
    d = {}
    p = os.path.join(HERE, name)
    if not os.path.exists(p): return d
    for l in open(p):
        j = json.loads(l)
        if j['id'] not in d or j.get('ok', True): d[j['id']] = j
    return d

meta, comp, cut2 = jl('meta.jsonl'), jl('compose.jsonl'), jl('cuts2.jsonl')
types = {}
for f in ['types_saves.txt', 'types_lab.txt']:
    p = os.path.join(HERE, f)
    if os.path.exists(p):
        for l in open(p):
            if l.strip() and not l.startswith('#'):
                parts = l.split()
                types[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else '')
subjects = {}
for l in (open(os.path.join(HERE, 'draft_subjects.tsv')) if os.path.exists(os.path.join(HERE, 'draft_subjects.tsv')) else []):
    p = l.rstrip('\n').split('\t')
    if len(p) > 2 and p[2]: subjects[p[2]] = p[1]
overlay = {}
for l in (open(os.path.join(HERE, 'overlays_hand.txt')) if os.path.exists(os.path.join(HERE, 'overlays_hand.txt')) else []):
    if l.startswith('#') or not l.strip(): continue
    s, job, txt = l.rstrip('\n').split('|', 2)
    overlay[s] = (job, txt)
ci = json.load(open(os.path.join(LIB, '_reference/clip_index.json')))['clips']
use = {}
for k, v in ci.items():
    m = re.search(r'_(\d{15,})', k)
    if m: use[m.group(1)] = v.get('use')
refedits = {m.group(1) for f in os.listdir(os.path.join(LIB, '_reference/edits'))   # <creator>_<id>.mp4 only: the folder also
            for m in [re.search(r'_(\d{15,})\.\w+$', f)] if m}                           # holds Instagram files and reference folders

GENERIC = {'fyp', 'foryou', 'foryoupage', 'viral', 'fy', 'fypシ', 'fypviralシ', 'xyzbca', 'xyzabc', 'xybca', 'xybcafyp', 'fypage',
           'foruyou', 'trending', 'explore', 'recomendation', 'viralvideo', 'viralfyp', 'fyyy', 'fypgakni', 'xyzbcafypシ', 'xyczba',
           'capcut', 'edit', 'edits', 'repost', 'global', 'fypp', 'foryourpage', 'on', 'targetaudience', 'creatorsearchinsights',
           'fyppppppppppppppppppppppp', 'trend', 'grhfedd', 'grhf', 'elbruso', 'fypviral'}
EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF❤✨\U0001F90C-\U0001F9FF]')
INSTR = re.compile(r"\b(follow|watch (till|until|to) the end|until the end|don'?t read|link in bio|wait for|save this|comment|tag (a|your))\b", re.I)

def cap_text(desc):
    t = re.sub(r'#\S+', '', desc or '')
    t = re.sub(r'@\S+', '', t)
    t = re.sub(r'\s*\|\s*', ' ', t).strip(' .|')
    return re.sub(r'\s+', ' ', t).strip()

def caption_style(t, desc):
    if not t: return 'none'
    words = t.split()
    if '?' in t: return 'question'
    if INSTR.search(t): return 'instruction'
    if re.match(r'^(song|🎬)', t, re.I) or re.search(r'\((19|20)\d\d\)', t): return 'credit/info'
    if len(words) == 1: return 'one_word'
    if re.search(r'\b(i|me|my|we|us|our|you|your|him|her)\b|\b(i\'m|im)\b', t, re.I):
        return 'story/declaration' if len(words) >= 4 else 'short_personal'
    if len(words) <= 5: return 'mood_phrase'
    return 'statement'

def ocr_summary(o):
    if not o: return {}
    items = sorted((float(t), l) for t, l in o.items())
    keep = []
    for t, lines in items:
        txt = ' / '.join(x['text'] for x in lines if len(x['text']) >= 3)
        if txt: keep.append((t, txt, lines))
    if not keep: return {}
    first3 = [k for k in keep if k[0] <= 3]
    bands = collections.Counter(x['band'] for _, _, ls in keep for x in ls)
    uniq = []
    for _, txt, _ in keep:
        if txt not in uniq: uniq.append(txt)
    return {'text_frames': len(keep), 'text_first_t': keep[0][0], 'text_in_first3s': bool(first3),
            'text_band': bands.most_common(1)[0][0], 'text_max_h': max(x['h'] for _, _, ls in keep for x in ls),
            'text_all': ' || '.join(uniq)[:400]}

rows = []
for i, m in meta.items():
    if not m.get('ok'): continue
    c = comp.get(i, {}); c2 = cut2.get(i, {})
    src = set(m['sources'])
    if i in refedits: src.add('gmail')
    tp, tags = types.get(i, ('', ''))
    if i in refedits: tags = ','.join(x for x in [tags, 'ref_edit'] if x)
    plays = m['plays'] or 0
    age = max(1, (NOW - m['create_time']) / 86400) if m.get('create_time') else None
    desc = m.get('desc') or ''
    tagsl = [h.lower() for h in m['hashtags']]
    gen = sum(1 for h in tagsl if h in GENERIC or h.startswith('fyp') or h.startswith('xyz'))
    t = cap_text(desc)
    ttl = (m.get('music_title') or '').lower()
    orig = bool(m.get('music_original')) or 'original sound' in ttl or 'sonido original' in ttl or 'son original' in ttl or 'оригинальный' in ttl
    dur = c.get('dur') or m.get('duration') or 0
    cuts = [x for x in c2.get('cuts', c.get('cuts', [])) if x < dur]
    shots = [b - a for a, b in zip([0] + cuts, cuts + [dur])] if dur else []
    w, h = c.get('w') or m.get('width'), c.get('h') or m.get('height')
    o = ocr_summary(c.get('ocr'))
    r = {'id': i, 'creator': m['creator'].lstrip('@'), 'source': '+'.join(sorted(src)), 'content_type': tp, 'tags': tags,
         'draft_subject': subjects.get(i, ''), 'use': use.get(i, ''), 'url': m['url'],
         'plays': plays, 'likes': m['likes'], 'comments': m['comments'], 'shares': m['shares'], 'saves': m['saves'],
         'followers': m.get('followers'), 'age_days': round(age, 1) if age else '',
         'plays_per_day': round(plays / age) if age else '',
         'save_rate': round(m['saves'] / plays, 4) if plays else '', 'share_rate': round(m['shares'] / plays, 4) if plays else '',
         'like_rate': round(m['likes'] / plays, 4) if plays else '',
         'resonance': round((m['saves'] + m['shares']) / plays, 4) if plays else '',
         'reach_multiple': round(plays / m['followers'], 2) if m.get('followers') else '',
         'caption': desc.replace('\n', ' '), 'caption_text': t, 'caption_words': len(t.split()) if t else 0,
         'caption_style': caption_style(t, desc), 'caption_lowercase': (t == t.lower()) if t else '',
         'caption_emoji': bool(EMOJI.search(desc)), 'n_hashtags': len(tagsl), 'n_generic_tags': gen,
         'n_niche_tags': len(tagsl) - gen, 'hashtags': ' '.join('#' + x for x in tagsl), 'mentions': len(m.get('mentions') or []),
         'lang': m.get('lang'), 'music': m.get('music_title'), 'original_sound': orig, 'is_photo': m.get('is_photo'),
         'has_file': c.get('has_file'), 'dur': round(dur, 2) if dur else '', 'w': w, 'h': h,
         'aspect': ('vertical' if h and w and h / w > 1.3 else 'square' if h and w and h / w > 0.9 else 'horizontal') if w and h else '',
         'bars_tb': c.get('bars_tb'), 'bars_lr': c.get('bars_lr'),
         'n_cuts': len(cuts) if c.get('has_file') else '', 'avg_shot': round(st.mean(shots), 2) if shots and c.get('has_file') else '',
         'first_cut': cuts[0] if cuts else '', 'cuts_in_first_3s': sum(1 for x in cuts if x < 3) if c.get('has_file') else '',
         'light_changes': c2.get('light_changes'),
         'f0_luma': c.get('f0_luma'), 'f0_contrast': c.get('f0_contrast'), 'f0_sat': c.get('f0_sat'),
         'f0_faces': c.get('f0_faces'), 'f0_face_area': c.get('f0_face_area'),
         'face_frames_first3s': c.get('face_frames_first3s'), 'face_share': round(c['face_frames'] / c['n_samples'], 2) if c.get('n_samples') else '',
         'motion_0_5': c.get('motion_0_5'), 'motion_1': c.get('motion_1'), 'end_luma': c.get('end_luma'), 'loop_dist': c.get('loop_dist'),
         'text_on_screen': bool(o), 'text_in_first3s': o.get('text_in_first3s', False), 'text_first_t': o.get('text_first_t', ''),
         'text_frames': o.get('text_frames', 0), 'text_band': o.get('text_band', ''), 'text_max_h': o.get('text_max_h', ''),
         'overlay_job': overlay.get(i[-7:], ('', ''))[0], 'overlay_text': overlay.get(i[-7:], ('', ''))[1],
         'text_all': o.get('text_all', '')}
    rows.append(r)

with open(os.path.join(HERE, 'study_results.csv'), 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(len(rows), 'rows ->', 'study_results.csv')
