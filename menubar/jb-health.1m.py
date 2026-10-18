#!/tmp/claude-0/-home-claude-josephs-board/f2670f6c-9349-5e02-b356-2bdbd221c48c/scratchpad/s/fake/josephs-board/.venv/bin/python
# <xbar.title>Alex Hale's Board health</xbar.title>
# <xbar.desc>The board's health and data feeds in the Mac menu bar (jb-ease, deep search idea 62). Read-only.</xbar.desc>
# <xbar.version>1.0</xbar.version>
# Works with the free menu-bar apps xbar, BitBar or SwiftBar. Refreshes every minute (the 1m in the name).
import json
import os
import urllib.request

HOME = os.path.expanduser('~/josephs-board')
BASE = 'http://127.0.0.1:8090'
DOT = {'ok': '🟢', 'warn': '🟡', 'bad': '🔴', 'off': '⚪', 'info': '⚪'}
COL = {'ok': '#2e9e5b', 'warn': '#b88a00', 'bad': '#d0453a'}


def key():
    """The widget's read-only key (never printed)."""
    try:
        for ln in open(os.path.join(HOME, 'settings.env'), encoding='utf-8'):
            ln = ln.strip()
            if ln.startswith('JB_WIDGET_KEY=') and ln.split('=', 1)[1].strip().strip('"\''):
                return ln.split('=', 1)[1].strip().strip('"\'')
    except OSError:
        pass
    try:
        return json.load(open(os.path.join(HOME, 'data', 'tools.json'), encoding='utf-8')).get('widget_key') or ''
    except (OSError, ValueError):
        return ''


def clean(s):
    return str(s or '').replace('|', '/').replace('\n', ' ')


d = None
try:
    with urllib.request.urlopen(f'{BASE}/api/widget?checks=1&key={key()}', timeout=8) as r:
        d = json.loads(r.read().decode('utf-8'))
except Exception as e:
    err = str(e)[:80]

if not d or d.get('error'):
    print('🔴 Board')
    print('---')
    print('The board is not answering | color=#d0453a')
    print(f"{clean(d.get('error') if d else err)} | size=11")
    print('---')
    print(f'Restart the board | bash={HOME}/jb param1=restart terminal=false refresh=true')
    print(f'Open Terminal at the board | bash=/usr/bin/open param1=-a param2=Terminal param3={HOME} terminal=false')
else:
    h = d.get('health') or 'unknown'
    n = d.get('problems') or 0
    print(DOT.get(h, '⚪') + (f' {n}' if h != 'ok' and n else ''))
    print('---')
    print(('Board healthy' if h == 'ok' else f'{n} things to look at') + f" | color={COL.get(h, '#888888')}")
    groups = {}
    for c in d.get('checks') or []:
        groups.setdefault(c['group'], []).append(c)
    for g in ('Board parts', 'Brokers', 'Data feeds'):
        if g in groups:
            print('---')
            print(f'{g} | size=11 color=#888888')
            for c in groups.pop(g):
                print(f"{DOT.get(c['state'], '⚪')} {clean(c['name'])} | size=12")
                if c.get('detail'):
                    print(f"--{clean(c['detail'])} | size=11")
    other = [c for cs in groups.values() for c in cs]
    if other:
        print('---')
        print('Also needs attention | size=11 color=#888888')
        for c in other:
            print(f"{DOT.get(c['state'], '⚪')} {clean(c['name'])} | size=12")
            if c.get('detail'):
                print(f"--{clean(c['detail'])} | size=11")
    print('---')
    print(f'Open System check | href={BASE}/syscheck.html')
    print(f'Open the Health tab | href={BASE}/#health')
    print(f'Restart the board | bash={HOME}/jb param1=restart terminal=false refresh=true')
