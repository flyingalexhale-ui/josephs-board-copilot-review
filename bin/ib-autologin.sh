#!/bin/bash
# Automatic IB Gateway login (jb-ibc), using IBC: free, open-source (github.com/IbcAlpha/IBC).
# IBC starts IB Gateway, types your IBKR username and password, accepts the usual pop-ups and
# restarts IB Gateway every night. You only tap Approve in the IBKR Mobile app (IB Key) when IBKR
# asks, about once a week. The board pushes you when it is waiting for that tap.
#   ./jb ibkr-autologin           set it up (asks your IBKR username and password) and switch it on
#   ./jb ibkr-autologin off       switch it off (you log in to IB Gateway by hand again)
#   ./jb ibkr-autologin on        switch it back on with the saved login
#   bin/ib-autologin.sh run       what the "ibgateway" background service runs (not by hand)
# Your password is saved only in ibc/config.ini inside this folder, readable only by your Mac
# account (permission 600). It is never printed and never sent anywhere except to IBKR's login.
JB="$(cd "$(dirname "$0")/.." && pwd)"
IBC="$JB/ibc"
INI="$IBC/config.ini"
PY="$JB/.venv/bin/python"

find_gateway() {   # prints "<folder that holds it>|<version like 1030>" for the newest IB Gateway 10.x
  local best="" bestv=0 d v
  for d in "$HOME/Applications"/IB\ Gateway\ 10.* "/Applications"/IB\ Gateway\ 10.*; do
    [ -d "$d/jars" ] || continue        # only the offline (stand-alone) version has its own jars folder; IBC needs it
    v="$(basename "$d" | sed -n 's/^IB Gateway \([0-9]*\)\.\([0-9]*\)$/\1\2/p')"
    [ -n "$v" ] || continue
    if [ "$v" -gt "$bestv" ]; then bestv="$v"; best="$(dirname "$d")"; fi
  done
  [ -n "$best" ] && echo "$best|$bestv"
}

set_setting() {   # set_setting KEY VALUE in settings.env (adds the line if missing)
  "$PY" - "$JB/settings.env" "$1" "$2" <<'EOF'
import re, sys
f, k, v = sys.argv[1:]
try:
    s = open(f).read()
except FileNotFoundError:
    s = ''
if re.search(rf'^{k}=.*$', s, re.M):
    s = re.sub(rf'^{k}=.*$', f'{k}={v}', s, flags=re.M)
else:
    s = s.rstrip('\n') + f'\n\n# ---- Automatic IB Gateway login (./jb ibkr-autologin to set up, ./jb ibkr-autologin off to stop)\n{k}={v}\n'
open(f, 'w').write(s)
EOF
}

get_ibc() {
  [ -x "$IBC/scripts/ibcstart.sh" ] && return 0
  echo "Downloading IBC (free, open source) from GitHub..."
  local url
  url="$(curl -fsSL https://api.github.com/repos/IbcAlpha/IBC/releases/latest | grep -o 'https://[^"]*IBCMacos[^"]*\.zip' | head -n 1)"
  if [ -z "$url" ]; then echo "Could not find the IBC download on GitHub. Check the internet connection and try again."; return 1; fi
  local tmp; tmp="$(mktemp -d)"
  if ! curl -fsSL -o "$tmp/ibc.zip" "$url"; then echo "The IBC download failed. Try again in a minute."; rm -rf "$tmp"; return 1; fi
  mkdir -p "$IBC"
  unzip -q -o "$tmp/ibc.zip" -d "$IBC" && rm -rf "$tmp"
  chmod +x "$IBC"/*.sh "$IBC"/scripts/*.sh 2>/dev/null
  [ -f "$IBC/config.ini" ] && [ ! -f "$IBC/config.ini.original" ] && cp "$IBC/config.ini" "$IBC/config.ini.original"
  if [ ! -x "$IBC/scripts/ibcstart.sh" ]; then echo "IBC downloaded but its start script is missing ($url)."; return 1; fi
  echo "IBC $(basename "$url" .zip | sed 's/IBCMacos-//') downloaded."
}

cmd_setup() {
  if [ ! -x "$PY" ]; then echo "Not installed yet -- run ./install.sh first."; exit 1; fi
  local g; g="$(find_gateway)"
  if [ -z "$g" ]; then
    echo "The offline (stand-alone) IB Gateway 10.x was not found in Applications."
    echo "(The auto-updating IB Gateway can't be logged in automatically; IBC needs the offline one.)"
    echo "Download the OFFLINE IB Gateway installer, \"Stable\", for macOS from interactivebrokers.com, install it, then run this again."
    exit 1
  fi
  local v="${g#*|}"; echo "Found IB Gateway ${v:0:2}.${v:2} in ${g%|*}"
  get_ibc || exit 1
  [ -f "$INI" ] || cp "$IBC/config.ini.original" "$INI" 2>/dev/null || touch "$INI"
  chmod 600 "$INI"   # before the password goes in
  echo
  printf 'IBKR username: '; read -r user
  [ -n "$user" ] || { echo "Cancelled (no username)."; exit 1; }
  local pw pw2
  printf 'IBKR password (it will not show as you type): '; IFS= read -rs pw; echo
  printf 'Type the password again: '; IFS= read -rs pw2; echo
  if [ -z "$pw" ] || [ "$pw" != "$pw2" ]; then echo "The two passwords did not match. Nothing was changed; run it again."; exit 1; fi
  printf 'Live or paper account? [live]: '; read -r mode
  case "$(printf '%s' "$mode" | tr 'A-Z' 'a-z')" in paper) mode=paper ;; *) mode=live ;; esac
  # write config.ini with Python, reading the password from the environment (never on a command line)
  IBU="$user" IBP="$pw" IBM="$mode" "$PY" - "$INI" <<'EOF'
import os, re, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
f = sys.argv[1]
# the nightly restart at 11:50 pm New York time (IBKR's own nightly reset), in this Mac's clock
ny = datetime.now(ZoneInfo('America/New_York')).replace(hour=23, minute=50, second=0, microsecond=0)
local = ny.astimezone().strftime('%I:%M %p').lstrip('0')
want = {
    'IbLoginId': os.environ['IBU'], 'IbPassword': os.environ['IBP'], 'TradingMode': os.environ['IBM'],
    'AcceptIncomingConnectionAction': 'accept', 'ExistingSessionDetectedAction': 'primary',
    'ReloginAfterSecondFactorAuthenticationTimeout': 'yes', 'SecondFactorAuthenticationTimeout': '180',
    'AutoRestartTime': local, 'AcceptNonBrokerageAccountWarning': 'yes', 'AllowBlindTrading': 'no',
    'MinimizeMainWindow': 'yes', 'StoreSettingsOnServer': 'no',
}
s = open(f).read() if os.path.exists(f) else ''
for k, v in want.items():
    if re.search(rf'^\s*{k}\s*=.*$', s, re.M):
        s = re.sub(rf'^\s*{k}\s*=.*$', lambda m: f'{k}={v}', s, flags=re.M)
    else:
        s += f'\n{k}={v}\n'
os.chmod(f, 0o600) if os.path.exists(f) else None
old = os.umask(0o077)
with open(f, 'w') as fh:
    fh.write(s)
os.umask(old)
os.chmod(f, 0o600)
print(f'Saved. IB Gateway will restart itself every night at {local} on this Mac\'s clock (11:50 pm New York time).')
EOF
  unset pw pw2
  chmod 600 "$INI"
  set_setting RUN_IB_AUTOLOGIN on
  if pgrep -f "IB Gateway|ibgateway" >/dev/null 2>&1 && ! pgrep -f "ibcstart|IBC.jar" >/dev/null 2>&1; then
    echo
    echo "IB Gateway is open right now. Quit it first: click its window, then press Command+Q (choose Exit / Yes)."
    printf 'Press Return once it is closed... '; read -r _
  fi
  "$JB/jb" restart ibgateway
  echo
  echo "Starting IB Gateway with automatic login. Within about a minute IBKR Mobile on your phone"
  echo "should ask you to approve the login: tap Approve. After that the IBKR board connects by itself."
  echo "Watch it with:  ./jb logs ibgateway   (Control+C to stop watching)"
}

cmd_off() {
  set_setting RUN_IB_AUTOLOGIN off
  "$JB/jb" restart
  echo "Automatic login is off. Open IB Gateway and log in by hand as before."
  echo "(Your saved login is still in ibc/config.ini; delete that file to remove it.)"
}

cmd_on() {   # switch it back on with the login already saved (no retyping)
  if [ ! -f "$INI" ] || ! grep -q '^IbLoginId=.\+' "$INI"; then cmd_setup; return; fi
  set_setting RUN_IB_AUTOLOGIN on
  if pgrep -f "IB Gateway|ibgateway" >/dev/null 2>&1 && ! pgrep -f "ibcstart|IBC.jar" >/dev/null 2>&1; then
    echo "IB Gateway is open right now. Quit it first: click its window, then press Command+Q (choose Exit / Yes)."
    printf 'Press Return once it is closed... '; read -r _
  fi
  "$JB/jb" restart ibgateway
  echo
  echo "Starting IB Gateway with automatic login. Within about a minute IBKR Mobile on your phone"
  echo "should ask you to approve the login: tap Approve. After that the IBKR board connects by itself."
  echo "Watch it with:  ./jb logs ibgateway   (Control+C to stop watching)"
}

cmd_run() {
  if [ ! -f "$INI" ] || ! grep -q '^IbLoginId=.\+' "$INI"; then
    echo "Automatic IB Gateway login is not set up yet -- run  ./jb ibkr-autologin"; sleep 300; exit 1
  fi
  local g; g="$(find_gateway)"
  if [ -z "$g" ]; then echo "IB Gateway 10.x not found in Applications -- reinstall it, then ./jb restart ibgateway"; sleep 300; exit 1; fi
  local mode; mode="$(sed -n 's/^TradingMode=\(.*\)$/\1/p' "$INI" | head -n 1)"
  mkdir -p "$IBC/logs"
  local v="${g#*|}"; v="${v:0:2}.${v:2}"      # on a Mac IBC wants the folder's own version, "10.51"
  local settings=(); [ -d "$HOME/Jts" ] && settings=("--tws-settings-path=$HOME/Jts")   # keep IB Gateway's own settings
  echo "starting IB Gateway $v with IBC (mode ${mode:-live})"
  exec /bin/bash "$IBC/scripts/ibcstart.sh" "$v" -g "--tws-path=${g%|*}" "--ibc-path=$IBC" "--ibc-ini=$INI" "${settings[@]}" "--mode=${mode:-live}" --on2fatimeout=restart
}

case "$1" in
  run) cmd_run ;;
  off) cmd_off ;;
  on) cmd_on ;;
  setup|"") cmd_setup ;;
  *) echo "usage: ./jb ibkr-autologin [on|off]"; exit 1 ;;
esac
