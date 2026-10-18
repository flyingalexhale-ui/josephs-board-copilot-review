#!/bin/bash
# Joseph's Board installer for the spare Mac. Safe to run again any time (e.g. after moving the
# folder or updating files) -- it only (re)does what's needed.
#
#   1. finds Python 3.10+ (the built-in /usr/bin/python3 on macOS 12 is 3.9, too old for IBKR)
#   2. builds a private Python environment in .venv with everything from requirements.txt
#   3. creates settings.env the first time (then asks you to fill it in and run this again)
#   4. installs + starts the background jobs chosen in settings.env (via ./jb restart)
set -u
JB="$(cd "$(dirname "$0")" && pwd)"
cd "$JB" || exit 1
say() { printf '\n==> %s\n' "$*"; }

if [ "$(uname)" != "Darwin" ]; then echo "This installer is for macOS."; exit 1; fi
if [ "$(id -u)" = "0" ]; then echo "Run this as your normal user, not with sudo."; exit 1; fi
# macOS privacy rules stop background jobs from reading Desktop, Documents, Downloads and iCloud
# folders -- everything would install fine and then never run. Catch that now.
case "$JB/" in
  "$HOME/Desktop/"*|"$HOME/Documents/"*|"$HOME/Downloads/"*|"$HOME/Library/Mobile Documents/"*)
    cat <<EOF

  This folder is in a place macOS won't let background jobs read:
      $JB
  Move it into your home folder first: in Finder choose  Go > Home  and drag the
  josephs-board folder into that window. Then in Terminal:
      cd ~/josephs-board
      ./install.sh
EOF
    exit 1 ;;
esac
case "$JB" in *" "*) say "Note: this folder's path contains a space ($JB). That works, but ~/josephs-board is simplest.";; esac

# ---------------------------------------------------------------- 1. Python
say "Looking for Python 3.10 or newer"
PYBIN=""
for c in /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
         /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13 /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if "$c" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,15) else 1)' 2>/dev/null; then PYBIN="$c"; break; fi
done
if [ -z "$PYBIN" ]; then
  cat <<'EOF'

  No suitable Python found. The Python built into macOS 12 is 3.9, and the IBKR library needs 3.10+.

  Install Python 3.13 (free, 2 minutes):
    1. Go to  https://www.python.org/downloads/macos/
    2. Under "Stable Releases", find the newest "Python 3.13.x" that shows
       "Download macOS 64-bit universal2 installer", download it and run it.
    3. When it finishes, run  ./install.sh  again.
EOF
  exit 1
fi
echo "Using $PYBIN ($("$PYBIN" -c 'import sys; print(sys.version.split()[0])'))"

# ---------------------------------------------------------------- 2. private environment
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  say "Creating the private Python environment (.venv)"
  "$PYBIN" -m venv .venv || { echo "Could not create .venv"; exit 1; }
fi
say "Installing/updating Python packages (first time: a few minutes)"
.venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
# --prefer-binary: take a ready-built package over a newer one that would need compiling
# (this Mac has no compilers, and doesn't need them)
if ! .venv/bin/python -m pip install --prefer-binary -r requirements.txt; then
  cat <<'EOF'

  Package installation failed (see the messages above). The usual causes:
   * no internet connection, or
   * Python's security certificates not installed: open Finder > Applications > Python 3.x and
     double-click "Install Certificates.command", then run ./install.sh again.
   * a package that had to be built from source: send the last 20 lines above to Claude.
EOF
  exit 1
fi
.venv/bin/python -c 'import ib_async, websockets, pandas, yaml, certifi; import webull.data.data_client' || { echo "A package failed to import (see above)."; exit 1; }

# ---------------------------------------------------------------- 3. settings
chmod +x jb bin/run-service.sh 2>/dev/null
mkdir -p logs data/webull-token
if [ ! -f settings.env ]; then
  cp settings.env.example settings.env
  chmod 600 settings.env
  cat <<'EOF'

==> Created settings.env -- fill it in now:
      open -e settings.env          (opens it in TextEdit)
    At minimum: pick a JB_PASSWORD, and add WEBULL_APP_KEY / WEBULL_APP_SECRET if you use Webull
    (or set RUN_WEBULL_BRIDGE=off). Save it, then run  ./install.sh  again.
EOF
  exit 0
fi
chmod 600 settings.env

# ---------------------------------------------------------------- 4. background jobs
say "Installing and starting the background jobs"
./jb restart all
cat <<'EOF'

==> Done. Next steps:
    * IB Gateway must be running and logged in on this Mac for the IBKR parts.
    * Using Webull? Run  ./jb webull-login  once and approve the request in the Webull app.
    * Check on things any time with  ./jb status   (and ./jb logs <service> if something's red).
EOF
