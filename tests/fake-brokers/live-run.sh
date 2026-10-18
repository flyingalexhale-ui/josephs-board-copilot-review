#!/bin/bash
# Live run of the whole board with FAKE brokers (no IBKR, no Webull, no phone pushes):
#   * a copy of this folder in $RUN (the real folder is never written to)
#   * a fake IB Gateway on port 4002 (tests/fake-brokers/fake_tws.py) and the fake Webull SDK
#     (tests/fake-brokers/pkgs/webull, copied into the copy's lib/ so every service imports it)
#   * every service started through bin/run-service.sh, exactly as launchd does on the Mac
#   * optional: a shifted wall clock (tests/fake-brokers/clockshift, built with gcc here) so the run
#     happens at a chosen New York time
# usage: tests/fake-brokers/live-run.sh <run folder> ["2026-10-13 19:53:00"  (New York time; default: real time)]
#        (FAKE_NO_OVERNIGHT=1 JB_OVERNIGHT_RECHECK_S=90 tests/fake-brokers/live-run.sh ...: Webull without overnight data)
# Stop everything with:  tests/fake-brokers/live-run.sh <run folder> stop
set -u
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="$1"; WHEN="${2:-}"
if [ "$WHEN" = "stop" ]; then
  for f in "$RUN"/logs/*.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null; done
  pkill -f "$RUN/" 2>/dev/null; echo "stopped"; exit 0
fi
rm -rf "$RUN"; mkdir -p "$RUN"
tar -C "$SRC" --exclude=.git --exclude=.venv --exclude=__pycache__ -cf - . | tar -C "$RUN" -xf -
ln -s "$SRC/.venv" "$RUN/.venv"
cp -r "$SRC/tests/fake-brokers/pkgs/webull" "$RUN/lib/webull"
mkdir -p "$RUN/logs" "$RUN/data/webull-token"
printf 'faketoken\n%s\nNORMAL\n' "$(( ($(date +%s) + 30 * 86400) * 1000 ))" > "$RUN/data/webull-token/token.txt"

export JB_PASSWORD=testpw JB_NTFY_TOPIC= JB_GATEWAY_PORT=8090
export RUN_IBKR_BRIDGE=on RUN_WEBULL_BRIDGE=on RUN_SCANNER=on RUN_WEBULL_ALGO=on RUN_IBKR_ALGO=on KEEP_AWAKE=off RUN_IB_AUTOLOGIN=off
export WEBULL_APP_KEY=FAKEKEY WEBULL_APP_SECRET=FAKESECRET WEBULL_REGION_ID=us IBKR_GATEWAY_PORT=4002
export WEBULL_ALGO_STRATEGY=strategies/sma_crossover.yaml IBKR_ALGO_STRATEGY=strategies/sma_crossover.yaml
export JB_FAKE_BROKERS="$RUN/tests/fake-brokers" JB_FAKE_LOGDIR="$RUN/logs"
# FAKE_NO_OVERNIGHT=1: the fake Webull starts as a plan without the overnight session (delete
# $RUN/logs/fake_webull.no_overnight to "buy the plan"; JB_OVERNIGHT_RECHECK_S=90 makes the parts check again sooner)
[ -n "${FAKE_NO_OVERNIGHT:-}" ] && touch "$RUN/logs/fake_webull.no_overnight"
if [ -n "$WHEN" ]; then
  want=$(TZ=America/New_York date -d "$WHEN" +%s)
  gcc -shared -fPIC -O2 -o "$RUN/logs/clockshift.so" "$SRC/tests/fake-brokers/clockshift/clockshift.c" -ldl || exit 1
  export LD_PRELOAD="$RUN/logs/clockshift.so" JB_CLOCK_SHIFT="$(( want - $(date +%s) ))"
  echo "clock shifted by $JB_CLOCK_SHIFT s: it is now $(TZ=America/New_York date) in New York for the services"
fi
"$RUN/.venv/bin/python" "$RUN/tests/fake-brokers/fake_tws.py" --port 4002 --logdir "$RUN/logs" > "$RUN/logs/fake_tws.log" 2>&1 &
echo $! > "$RUN/logs/fake_tws.pid"
sleep 1
for s in gateway ibkr webull scanner webull-algo ibkr-algo; do
  bash "$RUN/bin/run-service.sh" "$s" &
  echo $! > "$RUN/logs/$s.pid"
done
echo "started in $RUN (gateway http://127.0.0.1:8090/, password testpw)"
