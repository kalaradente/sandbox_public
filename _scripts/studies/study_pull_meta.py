"""Study step 1: fresh TikTok metadata for every saved (gmail/liked) and lab video.
Writes <study folder>/meta.jsonl (one line per video id, resumable). Default folder: _lab/studies/01_content_library.
Any list of videos: STUDY_SOURCE=list STUDY_DIR=<study folder> reads <study folder>/feed/log.csv (a "url" column; other
columns kept) and feed/videos/<creator>_<id>.mp4 (optional local copies). The FYP study is the same thing (STUDY_SOURCE=fyp)."""
import csv, json, re, os, glob, requests, concurrent.futures as cf, time

SCRIPT = os.path.dirname(os.path.abspath(__file__))
import sys; sys.path.insert(0, os.path.dirname(SCRIPT)); from sandbox_paths import LIB  # library/
HERE = os.environ.get('STUDY_DIR') or os.path.join(LIB, '_lab', 'studies', '01_content_library')
OUT = os.path.join(HERE, 'meta.jsonl')
FYP = os.environ.get('STUDY_SOURCE') in ('fyp', 'list')  # a list of videos: <study folder>/feed (log.csv + videos/)
FEED = os.path.join(HERE, 'feed')
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
      'Accept-Language': 'en-US,en;q=0.9'}

def vid(url):
    m = re.search(r'tiktok\.com/.*?/(?:video|photo)/(\d+)', url)   # TikTok only: other sites' links in the catalog are skipped
    return m.group(1) if m else None

rows = {}
if FYP:
    for r in csv.DictReader(open(os.path.join(FEED, 'log.csv'))):
        i = vid(r.get('url', ''))
        if not i: continue
        e = rows.setdefault(i, {'id': i, 'url': r['url'].split('?')[0], 'sources': set(), 'file': '', 'creator': r.get('creator', ''),
                                'fyp_action': r.get('action', ''), 'fyp_why': r.get('why', '')})
        e['sources'].add('fyp')
    for f in glob.glob(os.path.join(FEED, 'videos/*.mp4')):
        cr, i = os.path.basename(f)[:-4].rsplit('_', 1)
        e = rows.setdefault(i, {'id': i, 'url': f'https://www.tiktok.com/@{cr}/video/{i}', 'sources': set(), 'creator': cr})
        e['file'] = os.path.relpath(f, LIB); e['sources'].add('fyp')
if not FYP:  # the saved library (default)
    for r in csv.DictReader(open(os.path.join(LIB, '_reference/saves_catalog.csv'))):
        i = vid(r['url'])
        if not i: continue
        e = rows.setdefault(i, {'id': i, 'url': r['url'].split('?')[0], 'sources': set(), 'file': r['file'], 'creator': r['creator']})
        e['sources'].add('gmail' if r['list'] == 'Email' else 'liked')
    for r in csv.DictReader(open(os.path.join(LIB, '_lab/lab_catalog.csv'))):
        i = vid(r['url'])
        if not i: continue
        e = rows.setdefault(i, {'id': i, 'url': r['url'].split('?')[0], 'sources': set(), 'file': r['file'], 'creator': r['creator']})
        e['sources'].add('lab')
    for f in glob.glob(os.path.join(LIB, '_lab/source/*.info.json')):
        j = json.load(open(f))
        i = str(j['id'])
        e = rows.setdefault(i, {'id': i, 'url': j['webpage_url'], 'sources': set(),
                                'file': os.path.relpath(f, LIB).replace('.info.json', '.mp4'), 'creator': '@' + (j.get('uploader') or '')})
        e['sources'].add('lab')

    for f in glob.glob(os.path.join(LIB, '_reference/edits/*.mp4')) + glob.glob(os.path.join(LIB, '_captions/*.mp4')):
        b = os.path.basename(f)[:-4]
        if not re.match(r'^[\w.]+_\d{15,}$', b): continue   # TikTok files are <creator>_<numeric id>; other sites' clips are skipped
        cr, i = b.rsplit('_', 1)
        e = rows.setdefault(i, {'id': i, 'url': f'https://www.tiktok.com/@{cr}/video/{i}', 'sources': set(),
                                'file': os.path.relpath(f, LIB), 'creator': cr})
        e['sources'].add('gmail')

done = set()
if os.path.exists(OUT):
    for l in open(OUT):
        j = json.loads(l)
        if j.get('ok'): done.add(j['id'])

def pull(e):
    for attempt in range(3):
        try:
            t = requests.get(e['url'], headers=UA, timeout=25).text
            m = re.search(r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', t, re.S)
            d = json.loads(m.group(1))['__DEFAULT_SCOPE__'].get('webapp.video-detail', {})
            it = d.get('itemInfo', {}).get('itemStruct')
            if not it:
                return {**e, 'ok': False, 'err': d.get('statusMsg') or d.get('statusCode')}
            s = it.get('statsV2') or it['stats']
            return {**e, 'ok': True, 'desc': it.get('desc', ''),
                    'hashtags': [x.get('hashtagName') for x in it.get('textExtra', []) if x.get('hashtagName')],
                    'mentions': [x.get('userUniqueId') for x in it.get('textExtra', []) if x.get('userUniqueId')],
                    'plays': int(s.get('playCount', 0)), 'likes': int(s.get('diggCount', 0)),
                    'comments': int(s.get('commentCount', 0)), 'shares': int(s.get('shareCount', 0)),
                    'saves': int(s.get('collectCount', 0)),
                    'followers': it.get('authorStats', {}).get('followerCount'),
                    'author_videos': it.get('authorStats', {}).get('videoCount'),
                    'create_time': int(it.get('createTime', 0)),
                    'duration': it.get('video', {}).get('duration'), 'width': it.get('video', {}).get('width'),
                    'height': it.get('video', {}).get('height'),
                    'music_title': it.get('music', {}).get('title'), 'music_author': it.get('music', {}).get('authorName'),
                    'music_original': it.get('music', {}).get('original'),
                    'is_photo': it.get('imagePost') is not None, 'location': it.get('locationCreated'),
                    'lang': it.get('textLanguage'), 'poi': (it.get('poi') or {}).get('name'),
                    'category': it.get('CategoryType'), 'aigc': it.get('IsAigc')}
        except Exception as ex:
            err = repr(ex); time.sleep(4 * (attempt + 1))
    return {**e, 'ok': False, 'err': err}

todo = [e for i, e in rows.items() if i not in done]
print(len(rows), 'videos,', len(todo), 'to pull')
with open(OUT, 'a') as f, cf.ThreadPoolExecutor(3) as ex:
    for res in ex.map(pull, todo):
        res['sources'] = sorted(res['sources'])
        f.write(json.dumps(res) + '\n'); f.flush()
        print(res['id'], res['ok'], res.get('plays', res.get('err')))
