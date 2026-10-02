#!/bin/bash
# sandbox: the one step. Paste into Terminal on a Mac (a brand-new Mac is fine):
#   public:     curl -fsSL https://raw.githubusercontent.com/kalaradente/sandbox_public/main/install.sh | bash
#   the team: curl -fsSL https://raw.githubusercontent.com/kalaradente/sandbox_public/main/install.sh | bash -s -- team
# It installs what's missing (Apple's command line tools, Homebrew), gets the sandbox into ~/Desktop/sandbox (set
# SANDBOX_DIR for elsewhere), then runs setup.sh: ffmpeg, Python and its packages, the team's starter pack (team only),
# the DaVinci Resolve scripts if Resolve is installed, and a test render. "team" signs in to GitHub first, because the
# team repo is private (you need to be added to it). Safe to run again: it updates an existing sandbox.
# The whole script is one function, so bash reads all of it before running any of it (safe to pipe from curl).
main() {
  set -eu
  local MODE="${1:-}" REPO DIR BREW b
  if [ "$MODE" = "team" ]; then REPO="kalaradente/sandbox"; else REPO="kalaradente/sandbox_public"; fi
  REPO="${SANDBOX_REPO:-$REPO}"; DIR="${SANDBOX_DIR:-$HOME/Desktop/sandbox}"
  say() { printf "\n\033[1m%s\033[0m\n" "$1"; }
  [ "$(uname)" = "Darwin" ] || { echo "sandbox needs a Mac."; exit 1; }
  if [ "$(df -g "$HOME" | awk 'NR==2 {print $4}')" -lt 6 ]; then echo "sandbox needs about 6 GB of free disk space. Free some up and run this again."; exit 1; fi

  if ! xcode-select -p >/dev/null 2>&1; then
    say "Installing Apple's command line tools. A window pops up: click Install. This continues by itself when it's done (5-15 minutes)."
    xcode-select --install >/dev/null 2>&1 || true
    until xcode-select -p >/dev/null 2>&1; do sleep 10; done
  fi

  BREW="$(command -v brew || true)"
  if [ -z "$BREW" ]; then
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$b" ]; then BREW="$b"; break; fi
    done
  fi
  if [ -z "$BREW" ]; then
    if ! id -Gn | tr ' ' '\n' | grep -qx admin; then
      echo "Installing needs an administrator account on this Mac (Homebrew requires one). Log in as an admin, or ask whoever owns this Mac to make you one, then run this again."; exit 1
    fi
    say "Installing Homebrew (it asks for your Mac password once)."
    sudo -v </dev/tty
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/tty
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$b" ]; then BREW="$b"; break; fi
    done
  fi
  eval "$("$BREW" shellenv)"
  grep -qs "brew shellenv" "$HOME/.zprofile" || echo "eval \"\$($BREW shellenv)\"" >> "$HOME/.zprofile"

  if [ -d "$DIR/.git" ]; then
    say "Updating the sandbox in $DIR"
    if ! git -C "$DIR" pull --ff-only; then
      # the published history was replaced (a fresh first commit): nothing local joins it, so the copy is moved onto it,
      # but only when no file the sandbox ships was changed here (library/ and everything else git ignores is never touched)
      BR="$(git -C "$DIR" rev-parse --abbrev-ref HEAD)"
      if git -C "$DIR" fetch -q origin && git -C "$DIR" rev-parse -q --verify "origin/$BR" >/dev/null \
         && [ -z "$(git -C "$DIR" merge-base HEAD "origin/$BR")" ] && [ -z "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
        say "The sandbox's history was started fresh upstream: moving this copy onto it (your library and settings stay)."
        git -C "$DIR" reset -q --hard "origin/$BR"
      elif [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
        echo "Couldn't update $DIR: files the sandbox ships were changed here (git -C \"$DIR\" status lists them). Ask Claude to look at them, or move the folder away and run this again."; exit 1
      else
        echo "Couldn't update $DIR (the reason is above). Ask Claude to look at it, or move the folder away and run this again."; exit 1
      fi
    fi
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    echo "$DIR already exists and isn't a sandbox. Move it away, or run with SANDBOX_DIR=/another/folder."; exit 1
  else
    say "Getting the sandbox into $DIR"
    case "$REPO" in /*) git clone "$REPO" "$DIR" ;; *)
      if ! GIT_TERMINAL_PROMPT=0 git clone -q "https://github.com/$REPO.git" "$DIR" 2>/dev/null; then
        say "The team sandbox is private: sign in to GitHub (a browser window opens; your GitHub account must be added to $REPO)."
        command -v gh >/dev/null || brew install gh
        # GH_BROWSER=open: the sign-in page opens in their default browser (a BROWSER setting from other software can point at Chrome)
        gh auth status >/dev/null 2>&1 || GH_BROWSER=open gh auth login --hostname github.com --git-protocol https --web </dev/tty
        gh auth setup-git
        gh repo clone "$REPO" "$DIR"
      fi ;;
    esac
  fi

  if [ "$MODE" = "team" ]; then say "Setting up: ffmpeg, Python, the team's starter pack (about 1 GB) and a test render. The first run takes a while."
  else say "Setting up: ffmpeg, Python and a test render. The first run takes a while."; fi
  cd "$DIR" || exit 1
  if (exec </dev/tty) 2>/dev/null; then ./setup.sh </dev/tty; else ./setup.sh; fi   # piped from curl: prompts read the terminal

  say "All set."
  cat <<EOF
Next: open the Claude desktop app, go to the Code tab, choose the folder $DIR, and say hi.
Claude will show you how everything works.
(No Claude app yet? Get it at https://claude.ai/download and sign in; Claude Code needs a paid Claude plan.)
EOF
}
main "$@"
