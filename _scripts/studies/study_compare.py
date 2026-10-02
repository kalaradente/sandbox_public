"""Study step 4: top vs bottom quarter within each content type (and, with STUDY_SOLO=<creator>, inside that one account:
a creator with so many videos in the sample that they'd swamp their type is looked at on their own and left out of it).
Ranks on resonance = (saves+shares)/plays and separately on reach (plays_per_day). Videos under
MIN_PLAYS are left out of rate rankings (too noisy). Max 2 videos per creator in any quarter."""
import csv, os, sys, statistics as st, collections, math
SCRIPT = os.path.dirname(os.path.abspath(__file__))
import sys; sys.path.insert(0, os.path.dirname(SCRIPT)); from sandbox_paths import LIB  # library/
HERE = os.environ.get('STUDY_DIR') or os.path.join(LIB, '_lab', 'studies', '01_content_library')  # the study's folder (set STUDY_DIR for other studies)
R = list(csv.DictReader(open(os.path.join(HERE, 'study_results.csv'))))
MIN_PLAYS = 5000
SOLO = (os.environ.get('STUDY_SOLO') or '').strip().lstrip('@').lower()   # one creator looked at on their own (and left out of their type); none by default

def f(x):
    try: return float(x)
    except: return None

def quarters(rows, key, cap=2):
    rows = sorted(rows, key=lambda r: f(r[key]), reverse=True)
    n = max(3, len(rows) // 4)
    def pick(seq):
        out, c = [], collections.Counter()
        for r in seq:
            if c[r['creator']] < cap: out.append(r); c[r['creator']] += 1
            if len(out) == n: break
        return out
    return pick(rows), pick(rows[::-1])

NUM = ['plays', 'resonance', 'save_rate', 'share_rate', 'like_rate', 'reach_multiple', 'age_days', 'followers', 'dur', 'n_cuts',
       'avg_shot', 'cuts_in_first_3s', 'f0_luma', 'f0_contrast', 'f0_sat', 'f0_face_area', 'face_share', 'motion_0_5',
       'caption_words', 'n_hashtags', 'n_generic_tags', 'n_niche_tags', 'bars_tb']
BOOL = ['original_sound', 'text_on_screen', 'caption_emoji', 'caption_lowercase']

def med(rows, k):
    v = [f(r[k]) for r in rows if f(r[k]) is not None]
    return round(st.median(v), 3) if v else None

def share(rows, cond):
    return f"{sum(1 for r in rows if cond(r))}/{len(rows)}"

def report(name, rows, key, cap=2):
    top, bot = quarters(rows, key, cap)
    print(f"\n=== {name}: {len(rows)} videos, ranked by {key}; top/bottom {len(top)} (creator cap {cap})")
    for k in NUM:
        print(f"  {k:18s} top {med(top, k)!s:>10}  bottom {med(bot, k)!s:>10}")
    for lab, c in [('any caption text', lambda r: r['caption_style'] != 'none'),
                   ('no caption, only tags/none', lambda r: r['caption_style'] == 'none'),
                   ('original sound', lambda r: r['original_sound'] == 'True'),
                   ('deliberate on-screen text', lambda r: bool(r['overlay_job'])),
                   ('face in frame 1', lambda r: (f(r['f0_faces']) or 0) > 0),
                   ('vertical', lambda r: r['aspect'] == 'vertical'),
                   ('horizontal/square source', lambda r: r['aspect'] in ('horizontal', 'square')),
                   ('0 cuts (one take)', lambda r: r['n_cuts'] == '0'),
                   ('cut in first 3s', lambda r: (f(r['cuts_in_first_3s']) or 0) > 0),
                   ('bars (letterbox)', lambda r: (f(r['bars_tb']) or 0) > 0.08),
                   ('emoji', lambda r: r['caption_emoji'] == 'True'),
                   ('>=5 hashtags', lambda r: (f(r['n_hashtags']) or 0) >= 5),
                   ('any generic tag', lambda r: (f(r['n_generic_tags']) or 0) > 0)]:
        print(f"  {lab:28s} top {share(top, c):>6}  bottom {share(bot, c):>6}")
    cs = lambda rs: dict(collections.Counter(r['caption_style'] for r in rs).most_common())
    print('  caption styles top   ', cs(top)); print('  caption styles bottom', cs(bot))
    print('  TOP:')
    for r in top: print(f"    {r['creator'][:18]:18s} {int(f(r['plays'])):>9} res {f(r['resonance']):.3f} {r['dur']:>5}s cuts {r['n_cuts']:>3} | {r['caption_text'][:55]!r} {r['overlay_text'][:40]!r}")
    print('  BOTTOM:')
    for r in bot: print(f"    {r['creator'][:18]:18s} {int(f(r['plays'])):>9} res {f(r['resonance']):.3f} {r['dur']:>5}s cuts {r['n_cuts']:>3} | {r['caption_text'][:55]!r} {r['overlay_text'][:40]!r}")

if __name__ == '__main__':
    key = sys.argv[1] if len(sys.argv) > 1 else 'resonance'
    ok = [r for r in R if f(r['plays']) and f(r['plays']) >= MIN_PLAYS]
    for t in ['film_celeb', 'couple', 'people', 'aesthetic', 'party']:
        rows = [r for r in ok if r['content_type'] == t and r['creator'].lower() != SOLO]
        report(t + (' (without %s)' % SOLO if SOLO and len(rows) < sum(r['content_type'] == t for r in ok) else ''), rows, key)
    if SOLO: report('%s only (same account, same footage)' % SOLO, [r for r in ok if r['creator'].lower() == SOLO], key, cap=99)

    # rank correlations within type (Spearman), creator-balanced by using each creator's median per bin is overkill; plain rho + n
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
        for k, i in enumerate(o): r[i] = k
        return r
    def rho(a, b):
        ra, rb = rank(a), rank(b); n = len(a)
        ma, mb = sum(ra) / n, sum(rb) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
        den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
        return num / den if den else 0
    print(f"\n=== Spearman rho vs {key}, within type ({SOLO + ' on their own, left out of their type; ' if SOLO else ''}n in brackets)")
    feats = ['dur', 'n_cuts', 'avg_shot', 'caption_words', 'n_hashtags', 'n_generic_tags', 'f0_luma', 'f0_sat', 'f0_contrast', 'motion_0_5', 'face_share', 'bars_tb']
    print('  ' + ' '.join(f"{x[:9]:>9}" for x in ['type'] + feats))
    for t in ['film_celeb', 'couple', 'people', 'aesthetic', 'party'] + ([SOLO.upper()] if SOLO else []):
        rows = [r for r in ok if (r['creator'].lower() == SOLO if t == SOLO.upper() and SOLO else r['content_type'] == t and r['creator'].lower() != SOLO)]
        out = []
        for ft in feats:
            pr = [(f(r[ft]), f(r[key])) for r in rows if f(r[ft]) is not None and f(r[key]) is not None]
            out.append(f"{rho([a for a, b in pr], [b for a, b in pr]):+.2f}({len(pr)})" if len(pr) > 5 else '-')
        print(f"  {t[:9]:>9} " + ' '.join(f"{x:>9}" for x in out))
