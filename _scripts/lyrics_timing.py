#!/usr/bin/env python3
"""Word timings for on-screen lyrics (each word appears as it's sung): the song's lyric sheet laid onto the audio.
  python3 _scripts/lyrics_timing.py <song> [--model small.en] [--language en] [--out file.json]
  python3 _scripts/lyrics_timing.py <song> --heard      what Whisper hears, line by line with times: use it to write the sheet
                                                        out in sung order (repeats included) before timing (needs no sheet)
The words always come from the lyric sheet, never from a transcription (Whisper mishears, and hears a song differently every
time: it drops whole choruses). So the sheet's own words are laid onto the audio by Whisper's alignment, 30 s at a time, in the
sheet's order, which is why a chorus lands on its own pass. Whisper listens freely first, twice: where neither listening heard
anything for 4 s or more (an intro, an instrumental break) no words are laid, unless they sit better there than after it (sung,
not heard). The listenings are also the check (a sheet word
heard within 2 s of where it was laid, in order) and for the gaps: the alignment stretches a word over the silence before it,
so a stretched word takes the time Whisper heard it at, and a line's first word stretched over a gap (> 0.45 s) starts where
Whisper heard a phrase begin, else 0.3 s before its end. The last word of each line
is bold (change it to the line's impact word by hand). Writes <song folder>/words.json and records it in song.json as the
song's lyrics_timing.
The sheet must have every sung line in order, choruses written out each time they're sung (section labels like [Chorus] and
lines in [square brackets] are skipped; a line or words in (parentheses) are sung: the parentheses come off, the words stay,
timed and shown like any other). Then CHECK what it flags against the audio (beat_map.py <start> <end>): lines that don't seem
to be sung where the sheet has them (squeezed into no time, nothing heard), words heard somewhere else than they were laid,
words bunched together, words it is unsure of, and stretches where it heard other words than the sheet's (a repeat that isn't
written out, a line sung differently, or Whisper mishearing). Timings are never done until checked."""
import difflib, json, os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import songs  # noqa: E402
from sandbox_paths import LIB, locked, save_json  # noqa: E402

HEADER = re.compile(r"^\s*(\[.*\]|(verse|chorus|pre-?chorus|bridge|intro|outro|hook|refrain|post-?chorus)\b[^a-z]*\d*\s*:?)\s*$", re.I)


def norm(w): return re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'")).strip("'")


def sheet_lines(path):
    if not path.lower().endswith(".txt"):
        t = tempfile.mktemp(suffix=".txt"); songs.lyrics_text(path, t); path = t
    ls = [" ".join(l.replace("(", " ").replace(")", " ").split()) for l in open(path, encoding="utf-8", errors="replace").read().splitlines()]   # (echoes) are sung
    return [l for l in ls if l and not HEADER.match(l)]


SUNG = {"u": "you", "ur": "your", "imma": "I'ma", "im": "I'm", "youre": "you're", "thats": "that's", "dont": "don't", "aint": "ain't",
        "its": "it's", "cuz": "'cause", "whats": "what's", "gimmie": "gimme"}
WINDOW, KEEP, FEED = 30.0, 22.0, 110   # seconds Whisper takes at once; how far into them words are kept; tokens laid at once
QUIET = 4.0   # seconds with nothing heard in either listening: nothing is laid there


def sung(w):   # a word as it sounds, for the alignment only (the sheet's spelling is what goes on screen)
    return SUNG.get(norm(w), w.replace("’", "'").replace("‘", "'"))


def whisper(model):
    import ctranslate2
    from faster_whisper import WhisperModel
    ctranslate2.set_random_seed(0)   # the same listening every run (it guesses again, at random, where it isn't sure)
    return WhisperModel(model, device="cpu", compute_type="int8")


def heard(m, master, language, **how):
    segs, _ = m.transcribe(master, word_timestamps=True, language=language, **how)
    return [dict(w=w.word.strip(), n=norm(w.word), t=w.start, e=w.end, p=w.probability, first=k == 0)
            for s in segs for k, w in enumerate(x for x in (s.words or []) if norm(x.word))]


def quiet(hws, dur):
    """The stretches of 4 s or more where neither listening heard a word: [(from, to)]."""
    out = []; end = 0.0
    for t, e in sorted((h["t"], h["e"]) for hw in hws for h in hw) + [(dur, dur)]:
        if t - end >= QUIET: out.append((end, t))
        end = max(end, e)
    return out


def lay(m, master, lines, language, hws):
    """The sheet's words onto the audio, in order: [(start, end, how sure)] per word. Each window takes the next words, keeps the
    ones that end in its first 22 s (the rest pile up at its end: they belong to the next window), back to a line end if one is
    near, and the next window opens where the last kept word ends. Words are not laid where nothing was heard (hws: the free
    listenings): a window keeps only the words that start before a quiet stretch, and the next one opens at its end, unless the
    words sit better in the quiet itself (sung, not heard). Also returns what it did at each quiet stretch."""
    import numpy as np
    from faster_whisper.audio import decode_audio, pad_or_trim
    from faster_whisper.tokenizer import Tokenizer
    sr = 16000; audio = decode_audio(master, sampling_rate=sr); dur = len(audio) / sr
    if not language: language = m.detect_language(audio[:int(WINDOW * sr)])[0] if m.model.is_multilingual else "en"
    tok = Tokenizer(m.hf_tokenizer, m.model.is_multilingual, task="transcribe", language=language)
    sw = [(li, w) for li, l in enumerate(lines) for w in l.split() if norm(w)]
    toks = [tok.encode(" " + sung(w)) for _, w in sw]
    gaps = quiet(hws, dur); notes = []

    def window(pos, w):   # the next words laid onto the 30 s from pos: [(start, end, how sure)] from pos, and the word after them
        k = w; n = 0
        while k < len(sw) and (k == w or n + len(toks[k]) <= FEED): n += len(toks[k]); k += 1
        feats = m.feature_extractor(audio[int(pos * sr): int((pos + WINDOW) * sr)])[..., :-1]; frames = feats.shape[-1]
        r = m.model.align(m.encode(pad_or_trim(feats)), tok.sot_sequence, [[t for x in toks[w:k] for t in x]], frames, median_filter_width=7)[0]
        ti = np.array([x[0] for x in r.alignments]); fi = np.array([x[1] for x in r.alignments])
        at = np.append(fi[np.pad(np.diff(ti), (1, 0), constant_values=1).astype(bool)] / m.tokens_per_second, frames * m.feature_extractor.time_per_frame)
        b = np.pad(np.cumsum([len(x) for x in toks[w:k]]), (1, 0))
        return [(float(at[min(b0, len(at) - 1)]), float(at[min(b1, len(at) - 1)]), float(np.mean(r.text_token_probs[b0:b1]))) for b0, b1 in zip(b[:-1], b[1:])], k

    def fit(got):   # how well words sit: how sure, less the share of them squeezed into no time
        return sum(x[2] for x in got) / len(got) - 0.5 * sum(1 for x in got if x[1] - x[0] < 0.12) / len(got) if got else -1.0

    times = [None] * len(sw); pos = 0.0; w = 0
    while w < len(sw) and pos < dur - 0.5:
        got, k = window(pos, w)
        g = next(((a, b) for a, b in gaps if a <= pos + 1.5 and b > pos + 2.0), None)
        if g:   # the window opens where nothing was heard: are the next lines sung in it after all? Each line laid here against
            gaps.remove(g); there = max(pos, g[1] - 1.0); got2, k2 = window(there, w); keep = 0   # the same line laid past it
            for i in range(1, min(len(got), len(got2)) + 1):
                if pos + got[i - 1][0] > g[1] - 0.5: break
                if w + i == len(sw) or sw[w + i][0] != sw[w + i - 1][0]:
                    if fit(got[keep:i]) <= fit(got2[keep:i]): break
                    keep = i
            notes.append("nothing heard %.1f-%.1f: %s" % (g[0], g[1], "the words after it are laid past it" if not keep else
                                                            "%d words sit better in it, laid there to %.1f (sung, not heard? check)" % (keep, pos + got[keep - 1][1])))
            if keep:
                for i in range(keep): times[w + i] = (pos + got[i][0], pos + got[i][1], got[i][2])
                pos += got[keep - 1][1]; w += keep
                if g[1] - pos >= QUIET: gaps.append((pos, g[1]))
                continue
            pos, got, k = there, got2, k2
        stop = min([a - pos + 0.3 for a, b in gaps if pos + 1.5 < a < pos + KEEP] + [KEEP])   # a quiet stretch starts in this window
        if k >= len(sw) and pos + WINDOW >= dur and stop == KEEP: take = len(got)
        else:
            if stop == KEEP: take = max(1, sum(1 for x in got if x[1] <= KEEP))
            else:   # the words that start before the quiet are sung before it; the ones squeezed against it are the next words
                take = next((i for i, x in enumerate(got) if x[0] > stop), len(got))
                while take and got[take - 1][1] - got[take - 1][0] < 0.04: take -= 1
            for back in range(take, max(take - 8, 0), -1):
                if w + back == len(sw) or sw[w + back][0] != sw[w + back - 1][0]: take = back; break
        for i in range(take): times[w + i] = (pos + got[i][0], pos + got[i][1], got[i][2])
        if stop < KEEP: pos = pos + stop - 0.3   # to the quiet stretch: the next window decides whether to go past it
        elif got[take - 1][1] > 0.5: pos = pos + got[take - 1][1]
        else: pos += 5.0
        w += take
    for i in range(w, len(sw)): times[i] = (dur, dur, 0.0)   # the song ended before the sheet did
    return sw, times, notes


def alike(x, y):
    """1 the same word, 0.6 the same word misheard or spelled another way (lovin / loving, gimmie / gimme), 0 another word."""
    x, y = x.replace("'", ""), y.replace("'", "")
    if x == y: return 1.0
    return 0.6 if min(len(x), len(y)) >= 3 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.72 else 0.0


def pairs(sw, laid, hw):
    """Sheet words onto heard words, in order, each within 2 s of where it was laid: the pairing with the most words alike
    (neighbours count extra). {sheet word: heard word}."""
    n, m = len(sw), len(hw); a = [norm(w) for _, w in sw]
    best = [[[0.0, -1e9] for _ in range(m + 1)] for _ in range(n + 1)]; back = [[[None, None] for _ in range(m + 1)] for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c = [(best[i - 1][j][0], (i - 1, j, 0)), (best[i - 1][j][1], (i - 1, j, 1)), (best[i][j - 1][0], (i, j - 1, 0)), (best[i][j - 1][1], (i, j - 1, 1))]
            best[i][j][0], back[i][j][0] = max(c, key=lambda x: x[0])
            v = alike(a[i - 1], hw[j - 1]["n"]) if abs(hw[j - 1]["e"] - laid[i - 1][1]) <= 2.0 else 0
            if v: best[i][j][1], back[i][j][1] = max([(best[i - 1][j - 1][0] + v, (i - 1, j - 1, 0)), (best[i - 1][j - 1][1] + v + 0.5, (i - 1, j - 1, 1))], key=lambda x: x[0])
    i, j, s = n, m, int(best[n][m][1] > best[n][m][0]); out = {}
    while i > 0 and j > 0:
        if s: out[i - 1] = j - 1
        i, j, s = back[i][j][s]
    for i in sorted(out):   # a lone word heard somewhere else than it was laid: as likely another "you" or "me"
        if (i - 1) not in out and (i + 1) not in out and abs(hw[out[i]]["e"] - laid[i][1]) > 0.35: del out[i]
    return out


def merge(sw, laid, hws):
    """Every sheet word's time: where it was laid, unless it was stretched over a gap (longer than 1 s, or a line's first word
    longer than 0.45 s, or the next word of the line after one of those) and Whisper heard it start somewhere else: then where it was heard. how: "heard" (heard within 0.35 s
    of where it was laid), "moved" (stretched, so it took the time it was heard at), "differs" (heard somewhere else, left
    where it was laid), "laid" (not heard). Plus the heard word behind each, where phrases were heard to begin, and the
    stretches where other words were heard."""
    times = list(laid); how = ["laid"] * len(sw); src = [None] * len(sw); other = []
    for hw in hws:
        got = pairs(sw, laid, hw)
        for i in sorted(got):
            if how[i] in ("heard", "moved"): continue
            h = hw[got[i]]; (t, e, p) = laid[i]; src[i] = h
            after = i and how[i - 1] == "moved" and sw[i - 1][0] == sw[i][0]   # the next word of the line: it starts where a stretched word was laid to end
            if abs(h["t"] - t) <= 0.35 or (abs(h["e"] - e) <= 0.35 and not e - t > 1.0 and not after): how[i] = "heard"
            elif e - t > 1.0 or (e - t > 0.45 and (i == 0 or sw[i - 1][0] != sw[i][0])) or after: how[i] = "moved"; times[i] = (h["t"], h["e"], h["p"])
            else: how[i] = "differs"
        if hw is hws[0]:
            run = []
            for j, h in enumerate(hw + [None]):
                if h is not None and j not in got.values(): run.append(h); continue
                if len(run) >= 6: other.append((run[0]["t"], run[-1]["e"], " ".join(x["w"] for x in run)))
                run = []
    for i in range(1, len(sw)):   # in order, whatever was moved
        if times[i][0] < times[i - 1][0]: times[i] = (times[i - 1][0], max(times[i][1], times[i - 1][0]), times[i][2])
    begins = sorted({round(h["t"], 2) for hw in hws for h in hw if h["first"]})
    return times, how, src, begins, other


def build(lines, sw, times, how, src, begins):
    out, flags = [], []
    for li, text in enumerate(lines):
        idx = [n for n, (l, _) in enumerate(sw) if l == li]
        if not idx: continue
        ws = []
        t0, e1 = times[idx[0]][0], times[idx[-1]][1]
        gone = all(how[n] == "laid" for n in idx) and sum(times[n][2] for n in idx) / len(idx) < 0.25 and (e1 - t0) / len(idx) < 0.2
        if gone: flags.append((t0, "not sung?", "the whole line", text))
        for k, n in enumerate(idx):
            t, e, p = times[n]
            if k == 0 and e - t > 0.45:   # stretched over the gap before the line: where a phrase was heard to begin there
                b = [x for x in begins if t - 0.3 <= x <= e - 0.1]
                t = b[-1] if b else e - 0.3
            ws.append(dict(w=sw[n][1], t=round(t, 2), bold=k == len(idx) - 1))
            if gone: continue
            if how[n] in ("moved", "differs"): flags.append((t, how[n], sw[n][1], text + (" | heard at %.2f" % src[n]["t"] if how[n] == "differs" else "")))
            elif how[n] == "laid" and p < 0.3: flags.append((t, "unsure", sw[n][1], text))
            if k and ws[-1]["t"] - ws[-2]["t"] < 0.12: flags.append((t, "bunched", "%s / %s" % (ws[-2]["w"], ws[-1]["w"]), text))
        out.append(dict(start=ws[0]["t"], end=round(e1 + 0.4, 2), text=text, words=ws))
    ws = [w for l in out for w in l["words"]]
    for a, b in zip(ws[-2::-1], ws[:0:-1]): a["t"] = min(a["t"], b["t"])   # in order, whatever a first word was moved to
    for l in out: l["start"] = l["words"][0]["t"]
    for a, b in zip(out, out[1:]): a["end"] = max(a["start"], min(a["end"], b["start"]))
    return out, flags


def main():
    a = sys.argv[1:]
    if not a or a[0].startswith("-"): sys.exit(__doc__)
    arg = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    e = songs.get(a[0])
    model = arg("--model", "small.en"); lang = arg("--language", "en" if model.endswith(".en") else None)
    if "--heard" in a:   # needs no sheet: it's what the sheet gets written from
        segs, _ = whisper(model).transcribe(e["master"], language=lang, word_timestamps=True)
        for sg in segs:   # times from the words (segment times drift)
            ws = sg.words or []
            print("%6.2f-%6.2f  %s" % (ws[0].start if ws else sg.start, ws[-1].end if ws else sg.end, sg.text.strip()))
        return
    if not e.get("lyrics") or not os.path.exists(e["lyrics"]): sys.exit("%s has no lyric sheet yet (songs.py add ... --lyrics <sheet>)" % e["name"])
    lines = sheet_lines(e["lyrics"])
    print("Listening to %s with Whisper %s (a few minutes the first time: the model downloads)..." % (e["name"], model))
    m = whisper(model)
    hws = [heard(m, e["master"], lang), heard(m, e["master"], lang, condition_on_previous_text=False)]
    sw, laid, notes = lay(m, e["master"], lines, lang, hws)
    times, how, src, begins, other = merge(sw, laid, hws)
    out, flags = build(lines, sw, times, how, src, begins)
    dst = arg("--out") or os.path.join(os.path.dirname(e["master"]), "words.json")
    save_json(dst, dict(source="The lyric sheet %s laid onto %s by Whisper %s's alignment, checked against what it hears (sheet "
                               "words only; bold = last word of each line). Made by lyrics_timing.py; check the flagged words against the audio."
                               % (os.path.basename(e["lyrics"]), os.path.basename(e["master"]), model), lines=out))
    if not arg("--out"):   # song.json read and saved under its lock: another session may be adding or timing another song
        with locked(songs._cfg_path()): cfg = songs.config(); cfg["songs"][e["id"]]["lyrics_timing"] = os.path.relpath(dst, LIB); songs.save(cfg)
    print("%d lines, %d words: %d heard where they were laid, %d not heard, %d stretched and moved to where they were heard, %d heard "
          "somewhere else -> %s" % (len(out), len(how), how.count("heard"), how.count("laid"), how.count("moved"), how.count("differs"), os.path.relpath(dst, LIB)))
    for n in notes: print("  " + n)
    for t, kind, w, text in flags[:40]: print("  check %6.2f  %-9s %-18s  (%s)" % (t, kind, w, text[:50]))
    if len(flags) > 40: print("  ... and %d more" % (len(flags) - 40))
    for t, e1, text in other[:12]: print("  heard other words %6.2f-%6.2f: %s" % (t, e1, text[:90]))


if __name__ == "__main__": main()
