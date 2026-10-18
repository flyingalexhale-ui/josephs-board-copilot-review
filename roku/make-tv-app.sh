#!/bin/bash
# Makes the Roku TV app (Board-TV.zip in Downloads) with this Mac's home Wi-Fi address, its ts.net
# link and the TV key. Run again any time the Mac's Wi-Fi address changes:
#   bash ~/josephs-board/roku/make-tv-app.sh
cd ~/josephs-board || exit 1
KEYF=data/tv_key.txt
if [ ! -s "$KEYF" ]; then
  mkdir -p data
  .venv/bin/python -c "import secrets; print(secrets.token_hex(16))" > "$KEYF" && chmod 600 "$KEYF"
fi
KEY=$(head -1 "$KEYF" | tr -d '[:space:]')
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
[ -n "$IP" ] || { echo "PROBLEM: this Mac has no Wi-Fi address right now. Is Wi-Fi on?"; exit 1; }
PORT="${JB_GATEWAY_PORT:-8090}"
TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
NAME=$("$TS" status --json 2>/dev/null | .venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null)
[ -n "$NAME" ] || NAME=YOUR-MAC.YOUR-TAILNET.ts.net
AWAY="https://$NAME"
T=$(mktemp -d)
cp -R roku/app/. "$T/"
sed -i '' -e "s#http://MAC_ADDRESS:8090#http://$IP:$PORT#" -e "s#TS_NET_URL#$AWAY#" -e "s#TV_KEY#$KEY#g" "$T/components/config.brs"
grep -q "$IP" "$T/components/config.brs" || { echo "PROBLEM: could not write the address into the app."; exit 1; }
rm -f ~/Downloads/Board-TV.zip
(cd "$T" && zip -qr ~/Downloads/Board-TV.zip . -x '.*') || { echo "PROBLEM: could not make the zip."; exit 1; }
rm -rf "$T"
for U in "http://$IP:$PORT" "$AWAY"; do
  if curl -s -m 20 "$U/api/tv?key=$KEY" | grep -q '"ok": true'; then
    echo "TV feed check $U: OK"
  else
    echo "TV feed check $U: no answer yet (fine if the gateway just restarted; the TV keeps trying)."
  fi
done
echo "Made Board-TV.zip in your Downloads folder."
