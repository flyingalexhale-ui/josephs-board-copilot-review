#!/usr/bin/env python3
"""./jb webull-login -- one-time (and occasional) approval of Webull API access.

Webull's SDK may require API access to be approved in the Webull app before it will serve
data. This creates the Webull client in the foreground so you can approve the request on your
phone while watching; the approved token is saved in data/webull-token/ and reused by the
Webull bridge, the scanner and the Webull algo (no separate approvals).
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
import jb_webull  # noqa: E402


def main():
    key, secret = os.environ.get('WEBULL_APP_KEY'), os.environ.get('WEBULL_APP_SECRET')
    if not key or not secret:
        print('WEBULL_APP_KEY / WEBULL_APP_SECRET are empty in settings.env -- add them first.')
        return 1
    print('Contacting Webull... if Webull asks for approval, a request will appear in the Webull app on your')
    print('phone within a few seconds -- approve it there. This window waits up to 5 minutes.\n')
    t0 = time.time()
    try:
        _, data_client, _ = jb_webull.make_clients(key, secret, (os.environ.get('WEBULL_REGION_ID') or 'us'), interactive=True)
        rows = jb_webull.fetch_bars(data_client, 'SPY', 'D', 5, sessions=None)
    except Exception as e:
        print(f'\nNot approved / not working yet: {jb_webull.describe_error(e)}')
        print('Check the App Key/Secret in settings.env, that API access is enabled for your Webull account at '
              'developer.webull.com, then run ./jb webull-login again.')
        return 1
    tok = os.path.join(os.environ.get('WEBULL_OPENAPI_TOKEN_DIR', ''), 'token.txt')
    print(f'\nWebull API access works ({time.time() - t0:.0f}s). Test request: SPY daily bars -> {len(rows)} bars.')
    if os.path.exists(tok):
        print('Approval token saved -- the Webull bridge, scanner and algo will reuse it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
