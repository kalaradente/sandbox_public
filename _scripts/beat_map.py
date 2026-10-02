#!/usr/bin/env python3
"""Draw a stretch of the song so the cuts can be placed by eye.
  python3 _scripts/beat_map.py --song [--out file.png]      the whole master: sections are visible by size (big sections are
                                                            big, breakdowns thin out, drops turn dense); the windows from
                                                            the song's markers (song_markers.py) are marked. Saved next to the song.
  python3 _scripts/beat_map.py <start> <end> [--cuts 1.5,2.5,...] [--out file.png]
  python3 _scripts/beat_map.py --bpm                         the tempo, estimated from the kick pattern
  add --audio <file> to any of these to use another sound (e.g. library/_audio/sounds/x.m4a) instead of the song,
  or --name <song> for another of the songs in songs.py (default: the current one)
Writes a PNG (default: /tmp/beat_map_<start>.png): the full-mix waveform, the kick band (35-130 Hz) and the snare band
(1.5-7 kHz) with their hits dotted and timed, a 0.5 s grid from <start>, and optional planned cuts (seconds into the edit).
Read the image: the drop is where the waveform turns dense and chaotic; put the cut on the kick just before the snare
there, at that kick's own time (it doesn't have to sit on the 0.5 s grid). The song comes from library/_audio/song.json."""
import os, subprocess, sys
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from Lab_Render import MASTER  # noqa: E402
import song_markers as SM  # noqa: E402

SR = 22050


AUDIO = MASTER
SONG = None       # --name <song>: a registered song other than the current one (songs.py)


def estimate_bpm(path=None, start=0, seconds=90):
    """Tempo: spectral-flux onsets, then a comb over the autocorrelation (beats 1-4 apart), 70-180 BPM with a mild
    preference for common tempos. Gives 120 on every stretch of the house song."""
    sr = 11025
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(seconds), "-i", path or AUDIO, "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32); n, hop = 512, 110; dt = hop / sr
    if len(x) < n * 4: return None
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    S = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 100)
    on = np.maximum(0, np.diff(S, axis=0)).sum(1); on = np.maximum(on - np.convolve(on, np.ones(50) / 50, "same"), 0)
    ac = np.correlate(on, on, "full")[len(on) - 1:]; ac = ac / (ac[0] or 1)
    best, bs = None, -1.0
    for bpm in np.arange(70, 180.5, 0.5):
        lag = 60 / bpm / dt
        sc = sum(w * ac[max(0, int(round(lag * k)) - 1):int(round(lag * k)) + 2].max() for k, w in ((1, 1), (2, .6), (3, .4), (4, .5)) if int(round(lag * k)) < len(ac))
        sc *= np.exp(-0.5 * np.log2(bpm / 120) ** 2 / 0.81)
        if sc > bs: best, bs = float(bpm), sc
    return best


def song(out):
    import json
    is_song = AUDIO == MASTER or SONG is not None
    out = out or (os.path.join(os.path.dirname(AUDIO), "song_map.png") if is_song else os.path.splitext(AUDIO)[0] + "_map.png")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", AUDIO, "-ac", "1", "-ar", "4000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32); n = len(x) / 4000; W, H = 2400, 420; img = np.full((H, W, 3), 18, np.uint8)
    col = np.array_split(np.abs(x), W)
    pk = np.array([c.max() for c in col]); rm = np.array([np.sqrt(np.mean(c ** 2)) for c in col])
    rm = (rm - rm.min()) / (rm.max() - rm.min() + 1e-9)   # masters are loud: stretch loudness to the song's own range
    for i in range(W):   # peak (grey) and loudness (white) per pixel column
        cv2.line(img, (i, 210 - int(pk[i] / pk.max() * 190)), (i, 210 + int(pk[i] / pk.max() * 190)), (70, 70, 70), 1)
        cv2.line(img, (i, 210 - int(rm[i] * 185)), (i, 210 + int(rm[i] * 185)), (225, 225, 225), 1)
    X_ = lambda t: int(t / n * (W - 1))
    for t in range(0, int(n) + 1, 10):
        cv2.line(img, (X_(t), 400), (X_(t), 420), (150, 150, 150), 1); cv2.putText(img, "%d:%02d" % (t // 60, t % 60), (X_(t) + 2, 414), 0, 0.35, (170, 170, 170), 1)
    for w in (SM.windows(SM.load(song=SONG)) if is_song else []):   # the song's windows, from its markers file (the only source)
        cv2.rectangle(img, (X_(w["start"]), 4), (X_(w["end"]), 22), (60, 200, 60), -1); cv2.putText(img, w.get("name", "")[:24], (X_(w["start"]) + 2, 17), 0, 0.35, (0, 0, 0), 1)
    cv2.imwrite(out, img); print(out)


def main():
    global AUDIO
    a = sys.argv[1:]
    global SONG
    if "--audio" in a: AUDIO = a[a.index("--audio") + 1]; del a[a.index("--audio"):a.index("--audio") + 2]
    if "--name" in a:
        import songs; SONG = a[a.index("--name") + 1]; AUDIO = songs.get(SONG)["master"]; del a[a.index("--name"):a.index("--name") + 2]
    if not AUDIO: sys.exit("No song yet: drop one into the chat (python3 _scripts/songs.py add ...), or use --audio <file>.")
    if "--bpm" in a: return print(estimate_bpm())
    if "--song" in a: return song(a[a.index("--out") + 1] if "--out" in a else None)
    if len(a) < 2: sys.exit(__doc__)
    t0, t1 = float(a[0]), float(a[1]); arg = lambda k: a[a.index(k) + 1] if k in a else None
    cuts = [t0 + float(c) for c in arg("--cuts").split(",")] if arg("--cuts") else []
    out = arg("--out") or "/tmp/beat_map_%.3f.png" % t0
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % t0, "-t", "%.3f" % (t1 - t0), "-i", AUDIO, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32); hop = int(0.005 * SR)
    def band(lo, hi):
        X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / SR); X[(f < lo) | (f > hi)] = 0; return np.fft.irfft(X, len(x))
    env = lambda s: np.sqrt(np.convolve(s ** 2, np.ones(hop * 2) / (hop * 2), "same"))[::hop]
    def hits(e, thr):
        d = np.convolve(np.maximum(0, np.diff(e, prepend=e[0])), np.ones(3), "same")
        return [i for i in range(8, len(d) - 8) if d[i] == d[i - 8:i + 9].max() and d[i] > thr * d.max()]
    A, K, S = env(x), env(band(35, 130)), env(band(1500, 7000)); t = t0 + np.arange(len(A)) * 0.005
    W, H = 1900, 560; img = np.full((H, W, 3), 18, np.uint8); X_ = lambda tt: int((tt - t0) / (t1 - t0) * (W - 1))
    for e, col, base, name in ((A, (170, 170, 170), 190, "full mix"), (K, (80, 80, 255), 365, "KICK 35-130 Hz"), (S, (255, 220, 60), 535, "SNARE 1.5-7 kHz")):
        ee = e / (e.max() + 1e-9)
        for i in range(1, len(t)): cv2.line(img, (X_(t[i - 1]), base - int(ee[i - 1] * 150)), (X_(t[i]), base - int(ee[i] * 150)), col, 1)
        cv2.putText(img, name, (5, base - 155), 0, 0.5, col, 1)
    for g in np.arange(t0, t1, 0.5): cv2.line(img, (X_(g), 0), (X_(g), H), (70, 70, 70), 1); cv2.putText(img, "%.2f" % g, (X_(g) + 2, 14), 0, 0.38, (170, 170, 170), 1)
    for i in hits(K, 0.25): cv2.circle(img, (X_(t[i]), 370), 6, (80, 80, 255), -1); cv2.putText(img, "%.2f" % t[i], (X_(t[i]) - 18, 390), 0, 0.33, (120, 120, 255), 1)
    for i in hits(S, 0.25): cv2.circle(img, (X_(t[i]), 545), 6, (255, 220, 60), -1); cv2.putText(img, "%.2f" % t[i], (X_(t[i]) - 18, 558), 0, 0.33, (255, 220, 60), 1)
    for c in cuts: cv2.line(img, (X_(c), 20), (X_(c), H), (0, 230, 255), 2); cv2.putText(img, "CUT %.2f" % c, (X_(c) + 3, 32), 0, 0.42, (0, 230, 255), 1)
    cv2.imwrite(out, img); print(out)


if __name__ == "__main__": main()
