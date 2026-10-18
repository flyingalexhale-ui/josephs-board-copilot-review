#!/bin/bash
# launchd runs this for every background service (see ./install.sh). It loads settings.env,
# points everything at the private Python in .venv, rotates the service's log if it's grown
# big, and starts the right program. Usage: bin/run-service.sh <service>
JB="$(cd "$(dirname "$0")/.." && pwd)"
SVC="$1"
cd "$JB" || exit 1
mkdir -p "$JB/logs" "$JB/data/webull-token"
LOG="$JB/logs/$SVC.log"

# settings.env -> environment (read as plain KEY=value lines, never run as code)
. "$JB/lib/settings.sh"
jb_load_settings "$JB/settings.env"
export PYTHONUNBUFFERED=1
export JB_DATA_DIR="$JB/data"
export WEBULL_OPENAPI_TOKEN_DIR="$JB/data/webull-token"   # one Webull login shared by every service
export PYTHONPATH="$JB/lib"

# which services are switched on (the dashboard hides tabs for the others)
is_on() { case "$(printf '%s' "$1" | tr 'A-Z' 'a-z')" in on|yes|true|1) return 0 ;; *) return 1 ;; esac; }
SV=""
is_on "$RUN_IBKR_BRIDGE" && SV="$SV,ibkr"
is_on "$RUN_WEBULL_BRIDGE" && SV="$SV,webull"
is_on "$RUN_SCANNER" && SV="$SV,scanner"
is_on "$RUN_WEBULL_ALGO" && SV="$SV,algo_webull"
is_on "$RUN_IBKR_ALGO" && SV="$SV,algo_ibkr"
export JB_SERVICES="${SV#,}"

# keep each log under ~20 MB: roll it over at start-up
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG" | tr -d ' ')" -gt 20000000 ]; then mv -f "$LOG" "$LOG.old"; fi
echo "===== $(date '+%Y-%m-%d %H:%M:%S') starting $SVC =====" >> "$LOG"

# crash-loop guard (jb-ideas): started 6+ times in 10 minutes -> wait an hour instead of
# restarting over and over, and push the phone once (System check shows it too)
ST="$JB/logs/.starts-$SVC"; NOW=$(date +%s)
{ [ -f "$ST" ] && awk -v n="$NOW" 'n-$1<600' "$ST"; echo "$NOW"; } > "$ST.tmp" 2>/dev/null && mv -f "$ST.tmp" "$ST"
if [ "$SVC" != "keepawake" ] && [ "${SVC%-live}" = "$SVC" ] && [ "$(wc -l < "$ST" | tr -d ' ')" -gt 5 ]; then
  CL="$JB/logs/.crashloop-$SVC"
  if [ ! -f "$CL" ] || [ -n "$(find "$CL" -mmin +60 2>/dev/null)" ]; then
    [ -n "$JB_NTFY_TOPIC" ] && curl -s -m 10 -H "Title: $SVC keeps crashing" -H "Tags: rotating_light" -H "Priority: 5" \
      -d "$SVC restarted 6 times in 10 minutes, so it is held back for an hour. Check System check or ./jb logs $SVC." "https://ntfy.sh/$JB_NTFY_TOPIC" >/dev/null 2>&1
  fi
  grep -v "^=====" "$LOG" | tail -n 1 > "$CL"
  echo "crash loop: held back for an hour (6+ starts in 10 minutes)" >> "$LOG"
  rm -f "$ST"; sleep 3600; exit 1
fi

PY="$JB/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Python environment missing -- run ./install.sh" >> "$LOG"; sleep 60; exit 1
fi
# certificates for secure connections (Webull's streaming), whatever state Python's own are in
CERT="$("$PY" -c 'import certifi; print(certifi.where())' 2>/dev/null)"
[ -n "$CERT" ] && export SSL_CERT_FILE="$CERT"

# while live trading runs (./jb live in a Terminal window, or the algo tab's Go Live), the matching paper trader stays off
case "$SVC" in
  webull-algo|ibkr-algo)
    LOCK="$JB/$SVC/logs/LIVE_RUNNING"
    if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
      echo "live trading is running (./jb live or Go Live) -- the paper trader waits" >> "$LOG"
      sleep 60; exit 0
    fi
    rm -f "$LOCK" "$LOCK.by" ;;
esac

# Go Live (jb-golive): live trading as a background job, started by the algo tab's Go Live button through
# ./jb live-service start (never by launchd's KeepAlive: the job is not restarted when it stops). It runs only with
# the request <algo>/logs/LIVE_SERVICE that ./jb live-service writes after you typed I_UNDERSTAND_THE_RISK on the page.
# Like ./jb live: logs/LIVE_RUNNING while it runs (the guardian watches it), the paper trader off meanwhile, and
# logs/LIVE_EXITED when it stops on its own (one loud push). Stop Live (logs/LIVE_STOP, then launchd's SIGTERM)
# stops it cleanly: orders still working are cancelled, nothing new is sent, the position stays.
live_service() {
  local BROKER="${SVC%-live}" PROJ L REQ STRAT CONF STARTED LOCK CHILD RC VAR
  PROJ="$JB/$BROKER-algo"; L="$PROJ/logs"; REQ="$L/LIVE_SERVICE"; LOCK="$L/LIVE_RUNNING"
  mkdir -p "$L"
  STRAT="$(sed -n 's/^strategy=//p' "$REQ" 2>/dev/null | head -n 1)"
  CONF="$(sed -n 's/^confirm=//p' "$REQ" 2>/dev/null | head -n 1)"
  STARTED="$(sed -n 's/^started=//p' "$REQ" 2>/dev/null | head -n 1)"
  last() { printf '{"how": "%s", "rc": %s, "at": %s, "strategy": "%s"}\n' "$1" "$2" "$(date +%s)" "$STRAT" > "$L/LIVE_LAST.json"; }
  if [ -z "$STRAT" ] || [ "$CONF" != "I_UNDERSTAND_THE_RISK" ]; then
    echo "no Go Live request (logs/LIVE_SERVICE) -- live trading not started" >> "$LOG"; last refused 3; return 0
  fi
  if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    echo "live trading for $BROKER is already running (./jb live?) -- not started twice" >> "$LOG"; last refused 3; return 0
  fi
  echo $$ > "$LOCK"; echo service > "$LOCK.by"
  rm -f "$L/LIVE_EXITED" "$L/LIVE_STOP"
  launchctl bootout "gui/$(id -u)/com.josephsboard.$BROKER-algo" >/dev/null 2>&1    # the paper trader stays off meanwhile
  VAR="$(printf '%s' "$BROKER" | tr 'a-z' 'A-Z')_LIVE_TRADING_CONFIRM"
  export "$VAR=I_UNDERSTAND_THE_RISK"           # the second switch, for this job only (you typed it on the page)
  echo "LIVE trading starts: $STRAT (Go Live)" >> "$LOG"
  cd "$PROJ" || return 1
  "$PY" -m algo.paper_trader --strategy "$STRAT" --mode live >> "$LOG" 2>&1 &
  CHILD=$!
  STOPPING=""
  trap 'STOPPING=1; kill -TERM "$CHILD" 2>/dev/null' TERM INT HUP
  wait "$CHILD"; RC=$?
  while kill -0 "$CHILD" 2>/dev/null; do wait "$CHILD"; RC=$?; done
  trap - TERM INT HUP
  rm -f "$LOCK" "$LOCK.by"
  if [ -f "$L/LIVE_STOP" ]; then                  # Stop Live: ./jb live-service stop restarts the paper trader
    echo "LIVE trading stopped by Stop Live (exit code $RC)" >> "$LOG"
    last stopped "$RC"; rm -f "$L/LIVE_STOP" "$REQ"
  elif [ -n "$STOPPING" ]; then                   # macOS stopped it (shut down, restart, log out): the request stays,
    echo "LIVE trading stopped by macOS (exit code $RC)" >> "$LOG"   # for the start-at-login option
    last shutdown "$RC"
  else                                            # it stopped on its own (a start-up refusal, an error)
    if [ "$RC" -eq 3 ]; then last refused 3; else last exited "$RC"; fi
    echo "LIVE trading stopped on its own (exit code $RC)" >> "$LOG"
    # the guardian pushes the phone -- a refusal right after you pressed Go Live shows on the page instead
    if [ "$RC" -ne 3 ] || [ -z "$STARTED" ] || [ $(( $(date +%s) - STARTED )) -gt 300 ]; then
      printf '{"rc": %d, "at": %s, "how": "service"}\n' "$RC" "$(date +%s)" > "$L/LIVE_EXITED"
    fi
    rm -f "$REQ" "$HOME/Library/LaunchAgents/com.josephsboard.$SVC.plist"
    /bin/bash "$JB/jb" restart "$BROKER-algo" >> "$LOG" 2>&1      # the paper trader again (if on in settings.env)
  fi
  return 0
}

case "$SVC" in
  gateway)     exec "$PY" gateway/gateway.py >> "$LOG" 2>&1 ;;
  ibkr)        exec "$PY" bridges/ibkr_bridge.py >> "$LOG" 2>&1 ;;
  webull)      exec "$PY" bridges/webull_bridge.py >> "$LOG" 2>&1 ;;
  scanner)     cd "$JB/mover-scanner" && exec "$PY" run_scanner.py --config watchlist.yaml >> "$LOG" 2>&1 ;;
  webull-algo) cd "$JB/webull-algo" && exec "$PY" -m algo.paper_trader --strategy "$WEBULL_ALGO_STRATEGY" --mode paper >> "$LOG" 2>&1 ;;
  ibkr-algo)   cd "$JB/ibkr-algo" && exec "$PY" -m algo.paper_trader --strategy "$IBKR_ALGO_STRATEGY" --mode paper >> "$LOG" 2>&1 ;;
  keepawake)   exec /usr/bin/caffeinate -i -s >> "$LOG" 2>&1 ;;
  ibgateway)   exec /bin/bash "$JB/bin/ib-autologin.sh" run >> "$LOG" 2>&1 ;;
  webull-live|ibkr-live) live_service; exit 0 ;;
  *) echo "unknown service '$SVC'" >> "$LOG"; exit 2 ;;
esac
