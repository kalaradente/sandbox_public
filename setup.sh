#!/bin/bash
# sandbox setup. New people don't run this by hand: the one-step installer does (see README).
# Safe to re-run any time, and after every `git pull` (it picks up a new starter pack).
#   ./setup.sh             install tools, write the config, make library/, get the starter pack, install Resolve scripts, smoke test
#   ./setup.sh --check     only check (installs and downloads nothing)
#   ./setup.sh --no-pack   everything except the starter pack download
#   ./setup.sh --help      this, and nothing else (any other option is refused before anything runs)
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"; LIB="$ROOT/library"; CHECK=0; NOPACK=0
for a in "$@"; do case "$a" in --check) CHECK=1;; --no-pack) NOPACK=1;;
  -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0;;   # the usage above, and nothing else: before this, --help ran a full setup
  *) echo "setup.sh: no such option: $a (it takes --check or --no-pack; ./setup.sh --help)" >&2; exit 2;; esac; done   # an unknown option was ignored, so a mistyped --check installed everything
UT="${SANDBOX_RESOLVE_UTILITY:-/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility}"   # the env var: tests only
ok() { printf "  \033[32mok\033[0m  %s\n" "$1"; }; warn() { printf "  \033[33m!!\033[0m  %s\n" "$1"; }
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && { eval "$("$b" shellenv)"; break; }; done
# The status line: pinned to the bottom row of the terminal (a scroll region; everything else scrolls above it), with the
# current step, a spinner and how long the step has taken, so a quiet step (pip, unpacking) never looks frozen.
# Only in a real terminal; the scroll region is always put back on exit, Ctrl-C included.
STATUS_FILE=""; TICK=""
status() { [ -n "$STATUS_FILE" ] && printf '%s|%s\n' "$1" "$(date +%s)" > "$STATUS_FILE"; return 0; }
step() { echo "$1"; status "$1"; }
status_stop() {
  [ -n "$TICK" ] && kill "$TICK" 2>/dev/null; wait "$TICK" 2>/dev/null; TICK=""
  [ -n "$STATUS_FILE" ] || return 0; rm -f "$STATUS_FILE"; STATUS_FILE=""
  local r; r=$(stty size </dev/tty 2>/dev/null | cut -d' ' -f1)
  printf '\0337\033[r\0338'; [ -n "$r" ] && printf '\0337\033[%d;1H\033[2K\0338' "$r"; return 0
}
if [ -t 1 ] && [ "${TERM:-dumb}" != dumb ] && stty size </dev/tty >/dev/null 2>&1; then
  STATUS_FILE=$(mktemp -t sandbox_status) && status "starting"
  printf '\n\033[1A'   # keep the cursor above the bottom row
  ( rows=0; i=0; spin='|/-\'
    while [ -f "$STATUS_FILE" ]; do
      read -r r c < <(stty size </dev/tty 2>/dev/null); r=${r:-24}; c=${c:-80}
      IFS='|' read -r msg since < "$STATUS_FILE" 2>/dev/null; msg=${msg:-working}; since=${since:-$(date +%s)}
      if [ "$r" != "$rows" ]; then rows=$r; printf '\0337\033[1;%dr\0338' $((rows - 1)); fi
      el=$(( $(date +%s) - since )); line=$(printf ' %s  sandbox setup: %s  %d:%02d ' "${spin:$((i % 4)):1}" "$msg" $((el / 60)) $((el % 60)))
      printf '\0337\033[%d;1H\033[2K\033[7m%s\033[0m\0338' "$rows" "${line:0:$((c - 1))}"
      i=$((i + 1)); sleep 1
    done ) &
  TICK=$!
  trap status_stop EXIT; trap 'exit 130' INT TERM
fi
echo "sandbox $(cat "$ROOT/VERSION") at $ROOT"
cd "$ROOT"

step "1. Tools"
if [ $CHECK = 0 ]; then
  command -v brew >/dev/null || { warn "Homebrew missing: run the one-step installer (README), or install it from https://brew.sh, then re-run"; exit 1; }
  grep -qs "brew shellenv" "$HOME/.zprofile" || { echo "eval \"\$($(command -v brew) shellenv)\"" >> "$HOME/.zprofile"; ok "added Homebrew to ~/.zprofile"; }
  for p in ffmpeg coreutils deno; do brew list "$p" >/dev/null 2>&1 || { status "installing $p (Homebrew)"; brew install "$p"; }; done
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null || { brew install python; hash -r; }
  echo "     installing the Python packages (a few minutes the first time; nothing prints until it's done)..."; status "installing the Python packages (quiet, a few minutes the first time)"
  PIPFLAG=""; python3 -c 'import os, sys, sysconfig; sys.exit(not os.path.exists(os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")))' && PIPFLAG="--break-system-packages"
  # core packages first, so an optional one that can't install on this Python never blocks the rest
  python3 -m pip install -q --disable-pip-version-check $PIPFLAG opencv-python-headless numpy pillow "yt-dlp[default,curl-cffi]" || warn "pip install of the core packages failed (see above)"
  python3 -m pip install -q --disable-pip-version-check $PIPFLAG -r "$ROOT/requirements.txt" || warn "an optional package didn't install (faster-whisper / gallery-dl / onnxruntime: only lyric timing, Pinterest and visual search need them)"
  # the downloaders chase TikTok, YouTube and Pinterest, which change often: always the newest (an old one is the usual "check" failure)
  python3 -m pip install -q -U --disable-pip-version-check $PIPFLAG "yt-dlp[default,curl-cffi]" gallery-dl || warn "couldn't update yt-dlp / gallery-dl (downloads may fail until they update)"
fi
for t in ffmpeg ffprobe timeout python3 git deno; do command -v $t >/dev/null && ok "$t" || warn "$t missing"; done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' && ok "python $(python3 -c 'import platform; print(platform.python_version())')" || warn "python 3.10 or newer needed"
python3 -c "import cv2, numpy, PIL, yt_dlp" 2>/dev/null && ok "python packages" || warn "python packages missing: python3 -m pip install -r requirements.txt"
# (the studies' OCR helper is built the first time a study needs it, not here: compiling it takes minutes on a fresh Mac with no output)

step "2. Config"
if [ $CHECK = 0 ]; then echo "$ROOT" > "$HOME/.sandbox_root"; fi
[ "$(cat "$HOME/.sandbox_root" 2>/dev/null)" = "$ROOT" ] && ok "~/.sandbox_root -> $ROOT (Resolve scripts and scheduled tasks read this)" || warn "~/.sandbox_root doesn't point here (run ./setup.sh without --check)"

step "3. Library"
if [ $CHECK = 0 ]; then
  python3 - "$ROOT" "$LIB" <<'PY'
import json, os, sys
root, lib = sys.argv[1:]
lay = json.load(open(os.path.join(root, "layout.json")))
for d in lay.get("folders", []) + ["_lab/rounds/" + k for k in lay.get("round_folders", [])]: os.makedirs(os.path.join(lib, d), exist_ok=True)
PY
  [ -f "$LIB/profile.json" ] || cp "$ROOT/profile.example.json" "$LIB/profile.json"
  HL="_docs/lab-lessons.md"; [ -f "$ROOT/$HL" ] || HL="the craft doc (python3 _scripts/docs.py craft)"   # the public version has no house lessons, only the craft
  [ -f "$LIB/_lab/LESSONS.md" ] || printf "# My lab lessons\nPatterns from my own lab picks (notes to test). House lessons: %s.\n" "$HL" > "$LIB/_lab/LESSONS.md"
fi
HOUSE=$(python3 -c "import json; print(json.load(open('$LIB/profile.json')).get('house') is True)" 2>/dev/null)

step "4. Starter pack (optional)"
read -r INST AVAIL URLSET <<<"$(python3 "$ROOT/_scripts/starter_pack.py" status | sed -E 's/installed=([^ ]*) available=([^ ]*) url=([^ ]*)/\1 \2 \3/')"
if [ "$HOUSE" = "True" ]; then ok "house library: this is where the pack comes from (build one with _scripts/make_starter_pack.sh)"
elif [ "$URLSET" != "set" ] || [ "$AVAIL" = "-" ]; then ok "no starter pack set up (optional; your own footage works too)"
elif [ "$INST" = "$AVAIL" ]; then ok "starter pack $INST"
elif [ $CHECK = 1 ] || [ $NOPACK = 1 ]; then warn "starter pack $AVAIL is available (you have: $INST): run ./setup.sh"
else
  CACHE="$HOME/Library/Caches/sandbox"; ZIP="$CACHE/starter-pack-$AVAIL.zip"; mkdir -p "$CACHE"
  SIZE=$(python3 -c "import json; print(round(json.load(open('$ROOT/starter_pack.json')).get('bytes', 0) / 1e9, 1))")
  echo "     downloading starter pack $AVAIL (${SIZE} GB)..."; status "downloading the starter pack (${SIZE} GB)"
  if URL=$(python3 "$ROOT/_scripts/starter_pack.py" url) && curl -L --fail --retry 3 -C - -# -o "$ZIP" "$URL"; then
    WANT=$(python3 -c "import json; print(json.load(open('$ROOT/starter_pack.json')).get('sha256', ''))")
    if [ -n "$WANT" ] && [ "$(shasum -a 256 "$ZIP" | awk '{print $1}')" != "$WANT" ]; then
      warn "download doesn't match starter_pack.json (Box has a different file than the repo expects); deleted it, run ./setup.sh again later"; rm -f "$ZIP"
    elif echo "     unpacking it into library/ (a minute or two)..." && status "unpacking the starter pack into library/" && python3 "$ROOT/_scripts/starter_pack.py" merge "$ZIP"; then ok "starter pack $AVAIL added to library/"; rm -f "$ZIP"
    else warn "couldn't unpack the starter pack (kept at $ZIP)"; fi
  else warn "starter pack download failed; run ./setup.sh again to resume"; fi
fi
[ -f "$LIB/profile.json" ] && ok "library/profile.json" || warn "no library/profile.json"
# concept tags for any clip or pin without them (a pack from before tags, new footage): only fills what's missing
if [ $CHECK = 0 ] && [ -f "$LIB/_reference/clip_index.json" ]; then python3 "$ROOT/_scripts/concept_tags.py" >/dev/null 2>&1 && ok "concept tags on every clip and pin"; fi
python3 - "$ROOT" "$CHECK" <<'PY'
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "_scripts")); from sandbox_paths import LIB, accounts
acc = accounts(); missing = []
for a in acc:
    if not a.get("exports"): continue
    if sys.argv[2] == "1": missing += [] if os.path.isdir(os.path.join(LIB, a["exports"])) else [a["exports"]]   # --check makes nothing
    else: os.makedirs(os.path.join(LIB, a["exports"]), exist_ok=True)
print("  \033[32mok\033[0m  accounts: %s" % (", ".join("%s -> library/%s" % (a.get("name") or a.get("id"), a.get("exports", "(no exports folder)")) for a in acc) if acc else "none yet (Claude asks which accounts you make videos for on the first run)"))
if missing: print("  \033[33m!!\033[0m  no exports folder yet: %s (run ./setup.sh)" % ", ".join("library/" + m for m in missing))
PY
[ -f "$LIB/_reference/clip_index.json" ] && ok "clip index ($(python3 -c "import json;print(len(json.load(open('$LIB/_reference/clip_index.json'))['clips']))") clips)" || ok "no clips yet (drop your own into library/_external content/ and tell Claude \"index my content\", or save TikToks and say \"check\")"
python3 -c "import sys; sys.path.insert(0, '$ROOT/_scripts'); import os; from Lab_Render import MASTER; sys.exit(not os.path.isfile(MASTER))" 2>/dev/null && ok "songs: $(python3 "$ROOT/_scripts/songs.py" | tr -s ' ' | cut -c1-60 | paste -sd ';' -)" || ok "no song yet (only for edits cut to a song: drop one, and its lyrics, into the chat with Claude)"

step "5. DaVinci Resolve scripts"
# Resolve is pinned:
# the free one up to 21.0.4, or any paid one. Read from the app without opening it: the version in its Info.plist, and paid
# or free the way Blackmagic's own uninstaller tells them apart (a paid app holds Contents/Applications/Blackmagic Proxy
# Generator, the free one Blackmagic Proxy Generator Lite). It only says so: setup goes on, and never installs or removes Resolve.
RAPP="${SANDBOX_RESOLVE_APP:-/Applications/DaVinci Resolve/DaVinci Resolve.app}"   # the env var: tests only
RESOLVE_PIN="21.0.4"; RESOLVE_PIN_URL="https://www.blackmagicdesign.com/support/download/f1b3986a11634684b538ff3d1d4b55d6/Mac%20OS%20X"   # "DaVinci Resolve 21.0.4 Update", the free one
resolve_version() {
  local r; r=$(python3 - "$RAPP" "$RESOLVE_PIN" "$RESOLVE_PIN_URL" <<'PY'
import os, plistlib, re, sys
app, pin, url = sys.argv[1:]
fix = "\n      use any paid version of Resolve, or install the free %s: %s" % (pin, url)
try: v = str(plistlib.load(open(os.path.join(app, "Contents", "Info.plist"), "rb")).get("CFBundleShortVersionString") or "").strip()
except Exception: v = ""
m = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", v)
if not m:
    print("warn|%s, so setup can't tell which version it is. The scripts need the free Resolve %s or older, or any paid version (DaVinci Resolve > About DaVinci Resolve says which you have). If yours is a newer free one:%s" % (
        "Resolve's scripts folder is here but the app isn't at " + app if not os.path.isdir(app) else "DaVinci Resolve is here but its version can't be read from " + os.path.join(app, "Contents", "Info.plist"), pin, fix)); sys.exit()
newer = tuple(int(x or 0) for x in m.groups()) > tuple(int(x) for x in pin.split("."))
paid, free = (os.path.lexists(os.path.join(app, "Contents", "Applications", n)) for n in ("Blackmagic Proxy Generator", "Blackmagic Proxy Generator Lite"))
ed = "paid" if paid and not free else "free" if free and not paid else ""
name = "DaVinci Resolve %s%s" % (v, " (%s)" % ed if ed else "")
if not newer or ed == "paid": print("ok|%s: the scripts work in it" % name)
elif ed == "free": print("warn|%s is newer than %s, and the free Resolve after %s breaks the scripts here:%s" % (name, pin, pin, fix))
else: print("warn|%s is newer than %s, and setup can't tell if it's the free one or a paid one (the free Resolve after %s breaks the scripts here). If yours is free:%s" % (name, pin, pin, fix))
PY
)
  case "$r" in ok\|*) ok "${r#ok|}";; warn\|*) warn "${r#warn|}";; *) warn "couldn't check the Resolve version: the scripts need the free Resolve $RESOLVE_PIN or older, or any paid version";; esac
}
if ! ls "$ROOT/_scripts/resolve/"*.py >/dev/null 2>&1; then ok "no Resolve scripts in this sandbox"
elif [ -d "$UT" ]; then
  resolve_version
  if [ $CHECK = 0 ]; then
    if cp "$ROOT/_scripts/resolve/"*.py "$UT/" 2>/dev/null; then ok "installed the Resolve scripts ($(cd "$ROOT/_scripts/resolve" && ls *.py | tr '\n' ' '))"
    else sudo cp "$ROOT/_scripts/resolve/"*.py "$UT/" && ok "installed the Resolve scripts (admin)"; fi
    # retired scripts come out of the menu (kept in the library, never deleted)
    for f in Export_Song_Markers.py; do
      if [ -f "$UT/$f" ]; then mkdir -p "$LIB/_to_delete/retired_resolve_scripts" && { mv -n "$UT/$f" "$LIB/_to_delete/retired_resolve_scripts/" 2>/dev/null || sudo mv -n "$UT/$f" "$LIB/_to_delete/retired_resolve_scripts/"; } && ok "removed the retired $f from Resolve's menu"; fi
    done
    # each song's markers entry, and any piece or song entries made before Resolve was here (they wait in the library)
    python3 "$ROOT/_scripts/songs.py" menu >/dev/null 2>&1
    PEND="$LIB/_recipes/resolve/menu"
    if [ -d "$PEND" ] && [ -n "$(ls -A "$PEND" 2>/dev/null)" ]; then
      if cp -R "$PEND/." "$UT/" 2>/dev/null || sudo cp -R "$PEND/." "$UT/"; then
        mkdir -p "$LIB/_to_delete/menu_installed" && mv "$PEND" "$LIB/_to_delete/menu_installed/$(date +%Y%m%d-%H%M%S)" && ok "installed the menu entries that were waiting for Resolve"
      fi
    fi
  else
    miss=0; for f in "$ROOT/_scripts/resolve/"*.py; do cmp -s "$f" "$UT/$(basename "$f")" || miss=1; done
    [ $miss = 0 ] && ok "Resolve scripts installed and current" || warn "Resolve scripts missing or out of date (run ./setup.sh)"
  fi
elif [ -d "$RAPP" ]; then resolve_version; warn "Resolve's scripts folder isn't there yet (it appears once Resolve has been opened): open DaVinci Resolve once, then run ./setup.sh again"
else ok "DaVinci Resolve not installed (optional; only for finishing edits by hand)"; fi

step "6. Smoke test"
python3 -c "import sys; sys.path.insert(0, '$ROOT/_scripts'); import sandbox_paths as p; print('     library:', p.LIB)"
python3 -c "import ast,glob; [ast.parse(open(f).read(), f) for f in glob.glob('$ROOT/_scripts/**/*.py', recursive=True) + glob.glob('$ROOT/review/*.py')]" && ok "every script parses" || warn "a script doesn't parse"
# render a generated test clip, so this proves the whole toolchain even before there's any footage
T=$(mktemp -d); mkdir -p "$T/_reference" "$T/_inbox"
if ffmpeg -v error -f lavfi -i "testsrc2=size=1080x1920:rate=30:duration=4" -c:v libx264 -pix_fmt yuv420p "$T/_inbox/smoke.mp4" </dev/null; then
  SANDBOX_LIBRARY="$T" python3 "$ROOT/_scripts/Clip_Index.py" --sheet _inbox/smoke.mp4 | grep -q "^sheet smoke" && ok "contact sheet" || warn "contact sheet failed"
  python3 - "$T" "$ROOT" <<'PY' && ok "lab render (two shots, TikTok-font caption)" || warn "lab render failed"
import json, os, subprocess, sys
T, ROOT = sys.argv[1:]
json.dump({"clips": {"smoke": {"file": "_inbox/smoke.mp4", "duration": 4.0}}}, open(os.path.join(T, "_reference/clip_index.json"), "w"))
r = dict(id="smoke", account="test", kind="video", format="9:16", song=None, caption="smoke test",
         shots=[{"clip": "smoke", "in": 0.2, "dur": 1.0}, {"clip": "smoke", "in": 2.0, "dur": 1.0}])
json.dump(r, open(os.path.join(T, "smoke.json"), "w"))
subprocess.run([sys.executable, os.path.join(ROOT, "_scripts/Lab_Render.py"), os.path.join(T, "smoke.json")], check=True, capture_output=True,
               env={**os.environ, "SANDBOX_LIBRARY": T})
assert os.path.getsize(os.path.join(T, "smoke.mp4")) > 10000
PY
else warn "ffmpeg couldn't make a test clip"; fi
rm -rf "$T"
# a piece turned into a Resolve timeline (no Resolve needed: a stand-in records what the engine asks for) must match its render
if python3 "$ROOT/_scripts/checks/resolve_check.py" --quick >/dev/null 2>&1; then ok "Resolve export (framing, caption layer and lengths match the render)"
else warn "Resolve export check failed (only matters if you finish edits in Resolve): python3 _scripts/checks/resolve_check.py --quick"; fi
status_stop
echo "Done. Next: open this folder in Claude Code and say hi. Claude will show you how everything works."
