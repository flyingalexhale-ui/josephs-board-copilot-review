"""Night desk alerts (jb-alerts2), running inside the gateway. All free.

  * Trading halts: Nasdaq's public halts feed (every US-listed stock, checked every minute).
    A phone push when a stock on your boards or in your holdings is halted or resumes, with the
    reason in plain words; the full list of today's halts is on the Night desk.
  * Before-bed card: at 7:45 pm New York time on evenings before a trading day (JB_BEDTIME in
    settings.env), what you hold and what could hit it before the next open: earnings tonight or
    before the open, 8:30 am data, Fed events, holidays / early closes.
  * Tap to open: every phone push from the board now opens the right page when tapped (the stock's
    chart on the IBKR board, or the Night desk). The Mac's Tailscale address is found by itself;
    JB_PHONE_URL in settings.env overrides it.
  * Combo alerts: two conditions that must both be true, e.g. "NVDA above its overnight high AND
    ES=F up 0.5% vs the 4 pm close". Once per stock per session.
  * Mac heartbeat: pings a free healthchecks.io check every 5 minutes (JB_HEARTBEAT_URL). If the
    Mac loses power or internet, the pings stop and healthchecks.io pushes your phone.
  * 8:30 am data reaction: when a big US number comes out (jobs, CPI...), how S&P and Nasdaq
    futures moved in the first 1 and 5 minutes, next to expected vs actual. Push after 5 minutes.
  * Fed and CPI odds: what Kalshi's public prediction markets say for the next Fed decision and
    the next CPI number (read only; nothing touches any account).
Nothing here places trades. Files: data/alerts2_settings.json, alerts2_state.json, alerts2_log.json.
"""
import html
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
SETTINGS = os.path.join(DATA, 'alerts2_settings.json')
STATE = os.path.join(DATA, 'alerts2_state.json')
LOG = os.path.join(DATA, 'alerts2_log.json')
COMBOS = os.path.join(DATA, 'combo_alerts.json')
sys.path.insert(0, os.path.join(ROOT, 'lib'))
try:
    from nyse_hours import trading_day, nyse_early_closes
except Exception:
    def trading_day(d):
        return d.weekday() < 5

    def nyse_early_closes(y):
        return set()

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
HALTS_URL = 'https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts'
KALSHI = 'https://api.elections.kalshi.com/trade-api/v2/events?series_ticker={}&status=open&with_nested_markets=true&limit=3'
DEFAULTS = {'halts': True, 'halts_all': False, 'bedtime': True, 'combos': True, 'data_830': True, 'tap': True}
REASONS = {
    'T1': 'news pending (the company is about to announce something)', 'T2': 'news released',
    'T3': 'news and trading resumption times announced', 'T5': 'single-stock trading pause (big price swing)',
    'T6': 'extraordinary market activity', 'T7': 'quotation-only period', 'T8': 'exchange-traded fund issue',
    'T12': 'more information requested by Nasdaq', 'H4': 'not meeting listing requirements',
    'H9': 'not current in its SEC filings', 'H10': 'SEC trading suspension', 'H11': 'regulatory concern',
    'O1': 'operations halt', 'IPO1': 'IPO not yet trading', 'M1': 'corporate action', 'M2': 'quotation not available',
    'LUDP': 'volatility pause (limit up / limit down)', 'LUDS': 'volatility pause, straddle condition',
    'MWC1': 'market-wide circuit breaker level 1', 'MWC2': 'market-wide circuit breaker level 2',
    'MWC3': 'market-wide circuit breaker level 3', 'MWC0': 'market-wide circuit breaker carried over',
    'D': 'security deleted from Nasdaq', 'R4': 'qualifications issues reviewed and resolved',
    'R9': 'filing requirements satisfied', 'C3': 'issuer news not forthcoming, trading resumes',
    'C4': 'qualifications halt ended, maintenance requirements met', 'C9': 'qualifications halt concluded',
    'C11': 'trading halt concluded by another regulator', 'MWCQ': 'market-wide circuit breaker resumption',
}
COND_KINDS = {
    'above': 'price above', 'below': 'price below', 'up_pct': 'up at least % vs 4 pm close',
    'down_pct': 'down at least % vs 4 pm close', 'above_onh': 'above its overnight high', 'below_onl': 'below its overnight low',
    'above_pmh': 'above its pre-market high', 'below_pml': 'below its pre-market low', 'vol_x': 'volume this hour at least x normal',
}

_lock = threading.Lock()
_st = {'started': False, 'halts': [], 'halts_at': None, 'halts_err': '', 'odds': {}, 'odds_at': None, 'odds_err': '',
       'heartbeat': {'at': None, 'ok': None, 'err': ''}, 'phone_url': '', 'card': None}
_CTX = None


# ------------------------------------------------------------------ small helpers
def _now():
    return int(time.time() * 1000)


def _ctx():
    global _CTX
    if _CTX is None:
        try:
            import certifi
            _CTX = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _CTX = ssl.create_default_context()
    return _CTX


def _http(url, timeout=20, headers=None, data=None, method=None):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return r.read()


def _chrome(url, timeout=20):
    import world
    if world._browser is None:
        raise RuntimeError('no Chrome-style connection available')
    r = world._browser.Session(impersonate='chrome').get(url, timeout=timeout)
    if r.status_code >= 400:
        raise OSError(f'HTTP Error {r.status_code}')
    return r.content


def settings():
    s = watch._read(SETTINGS, {})
    return {k: bool(s.get(k, d)) for k, d in DEFAULTS.items()}


def _state():
    s = watch._read(STATE, {})
    s.setdefault('sent', {})
    s.setdefault('reactions', [])
    return s


def _log(kind, sym, text):
    log = watch._read(LOG, [])
    log.insert(0, {'at': _now(), 'kind': kind, 'sym': sym, 'text': text})
    watch._write(LOG, log[:200])


def _send(kind, sym, title, text, tags='bell'):
    _log(kind, sym, text)
    print(f'alerts2: {kind} {sym}: {text}', flush=True)
    watch.push_later(title, text, tags)


def _symbols():
    out = set()
    try:
        import news
        out = {s for s, e in news.tracked().items() if e['kind'] in ('stock', 'etf')}
    except Exception:
        pass
    return out | held()


def held():
    """Stocks you hold: the Portfolio tab's ledger plus open positions in your My trading journal."""
    out = set()
    try:
        import portfolio
        out |= {p['sym'] for p in portfolio.positions() if (p.get('shares') or 0) > 0}
    except Exception:
        pass
    try:
        import trading
        with trading._lock:
            fills = list(trading._journal()['fills'].values())
        _, open_ = trading.round_trips(fills)
        out |= {o['sym'] for o in open_ if abs(o.get('qty') or 0) > 0 and '=' not in o['sym']}
    except Exception:
        pass
    return out


def session_name(ms):
    try:
        import night
        return night.session_at(ms)
    except Exception:
        return 'regular'


# ------------------------------------------------------------------ tap to open (ntfy "Click")
def _find_phone_url():
    u = os.environ.get('JB_PHONE_URL', '').strip().rstrip('/')
    if u:
        return u
    port = os.environ.get('JB_GATEWAY_PORT', '8090')
    for t in ('tailscale', '/Applications/Tailscale.app/Contents/MacOS/Tailscale'):
        exe = shutil.which(t) or (t if os.path.isfile(t) else None)
        if not exe:
            continue
        try:
            ip = subprocess.run([exe, 'ip', '-4'], capture_output=True, text=True, timeout=10).stdout.split()
            if ip:
                return f'http://{ip[0]}:{port}'
        except Exception:
            pass
    return ''


_SYM_RE = re.compile(r'^([A-Z][A-Z0-9.\-]{0,9})\b')


def click_url(title):
    base = _st['phone_url']
    if not base or not settings()['tap']:
        return ''
    m = _SYM_RE.match((title or '').replace('Alert: ', ''))
    if m and m.group(1) in (_symbols() | set(watch._px)):
        return f'{base}/ibkr.html#chart={urllib.parse.quote(m.group(1))}'
    return f'{base}/#night'


def push(title, text, tags='chart_with_upwards_trend'):
    """watch.push with a tap link (replaces it while this module runs)."""
    t = watch.topic()
    if not t:
        return 'no JB_NTFY_TOPIC in settings.env'
    try:
        h = {'Title': title.encode('ascii', 'ignore').decode(), 'Tags': tags}
        if hasattr(watch, '_priority'):
            h['Priority'] = watch._priority(tags)          # urgent pushes at top priority (jb-ideas)
        c = click_url(title)
        if c:
            h['Click'] = c
        req = urllib.request.Request('https://ntfy.sh/' + t, data=text.encode('utf-8'), method='POST', headers=h)
        urllib.request.urlopen(req, timeout=15, context=watch._ctx()).read()
        return ''
    except Exception as e:
        print(f'ntfy push failed: {e!r}', flush=True)
        return str(e)[:200]


# ------------------------------------------------------------------ halts
def _halt_items(body):
    """Each <item> as {field: text}. Nasdaq's feed is sometimes not clean XML ("mismatched tag"), so if the
    strict reader refuses it, read the fields with a forgiving pattern instead."""
    try:
        root = ET.fromstring(body)
        return [{el.tag.split('}', 1)[-1]: (el.text or '').strip() for el in it} for it in root.iter('item')]
    except ET.ParseError:
        text = body.decode('utf-8', 'replace') if isinstance(body, bytes) else body
        items = re.findall(r'<item\b.*?</item>', text, re.S | re.I)
        if not items and '<html' in text[:2000].lower():
            raise RuntimeError('Nasdaq sent a web page instead of the halts list')
        out = []
        for it in items:
            f = {}
            for tag, val in re.findall(r'<(?:\w+:)?(\w+)[^>]*>([^<]*)</(?:\w+:)?\1>', it):
                f[tag] = html.unescape(val).strip()
            out.append(f)
        return out


def parse_halts(body):
    out = []
    for f in _halt_items(body):
        sym = f.get('IssueSymbol') or f.get('title') or ''
        if not sym:
            continue
        code = f.get('ReasonCode', '')

        def stamp(day, tm):
            try:
                d = datetime.strptime(day + ' ' + tm.split('.')[0], '%m/%d/%Y %H:%M:%S')
                return watch.at_et(d.date(), d.hour, d.minute) + d.second * 1000
            except Exception:
                return None
        out.append({'sym': sym.upper(), 'name': f.get('IssueName', ''), 'market': f.get('Market', ''), 'code': code,
                    'why': REASONS.get(code, code or 'reason not given'),
                    'halted': stamp(f.get('HaltDate', ''), f.get('HaltTime', '')),
                    'resumes': stamp(f.get('ResumptionDate', ''), f.get('ResumptionTradeTime', '')) if f.get('ResumptionTradeTime') else None,
                    'quotes': stamp(f.get('ResumptionDate', ''), f.get('ResumptionQuoteTime', '')) if f.get('ResumptionQuoteTime') else None})
    return out


def check_halts(cfg, state, now_ms):
    try:
        try:
            rows = parse_halts(_http(HALTS_URL))
        except Exception:
            rows = parse_halts(_chrome(HALTS_URL))     # second try the Chrome way (sites that block plain Python)
        _st['halts_err'] = ''
    except Exception as e:
        # Nasdaq now and then answers one request with a web page; only report it if it keeps happening
        if not _st['halts_at'] or now_ms - _st['halts_at'] > 10 * 60000:
            _st['halts_err'] = str(e)[:160]
        return
    _st['halts'], _st['halts_at'] = rows, now_ms
    if not cfg['halts']:
        return
    mine = _symbols()
    hd = held()
    sent = state['sent']
    first = 'halts_ready' not in state
    state['halts_ready'] = True
    for h in rows:
        if not (cfg['halts_all'] or h['sym'] in mine):
            continue
        key = f"halt {h['sym']} {h['halted']}"
        if key not in sent:
            sent[key] = now_ms
            if not first and h['halted'] and now_ms - h['halted'] < 6 * 3600000:
                tag = ' (YOU HOLD IT)' if h['sym'] in hd else ''
                _send('halt', h['sym'], f"{h['sym']} HALTED{tag}",
                      f"{h['sym']} was halted at {watch.et(h['halted']).strftime('%-I:%M %p')} ET: {h['why']} ({h['code']}).", 'no_entry')
        if h['resumes'] and f'{key} resumed' not in sent:
            sent[f'{key} resumed'] = now_ms
            if not first and now_ms - h['resumes'] < 6 * 3600000:
                _send('resumed', h['sym'], f"{h['sym']} resumes trading",
                      f"{h['sym']} trading resumes {watch.et(h['resumes']).strftime('%-I:%M %p')} ET (halted for {h['why']}).", 'arrow_forward')


# ------------------------------------------------------------------ before-bed card
def bedtime():
    v = os.environ.get('JB_BEDTIME', '19:45').strip()
    m = re.match(r'^(\d{1,2})[:.](\d{2})$', v)
    return (int(m.group(1)), int(m.group(2))) if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else (19, 45)


def _next_trading_day(d):
    n = d + timedelta(days=1)
    while not trading_day(n):
        n += timedelta(days=1)
    return n


def build_card(now_ms):
    d = watch.et(now_ms).date()
    nxt = _next_trading_day(d)
    hd = sorted(held())
    lines, items = [], []
    early = nxt in set(nyse_early_closes(nxt.year) or [])
    if nxt != d + timedelta(days=1):
        items.append(f"Next trading day is {nxt.strftime('%A %b %-d')} (market closed in between).")
    if early:
        items.append(f"{nxt.strftime('%A')} is an early close (1 pm).")
    earn = (watch._read(os.path.join(DATA, 'earnings.json'), {}).get('dates') or {})
    watchset = _symbols()
    tonight = [s for s, e in earn.items() if s in watchset and e.get('date') == d.isoformat() and e.get('when') != 'before the open']
    morning = [s for s, e in earn.items() if s in watchset and e.get('date') == nxt.isoformat() and e.get('when') == 'before the open']
    held_earn = [s for s in tonight + morning if s in hd]
    if held_earn:
        items.append('YOU HOLD ' + ', '.join(held_earn) + ': earnings ' + ('tonight / ' if any(s in tonight for s in held_earn) else '') + 'before the open.')
    if tonight:
        items.append('Reported / reporting tonight: ' + ', '.join(sorted(tonight)[:10]) + '.')
    if morning:
        items.append(f"Reporting before the open {nxt.strftime('%a')}: " + ', '.join(sorted(morning)[:10]) + '.')
    try:
        import world
        snap = json.loads(world.snapshot())
        a, b = now_ms, watch.at_et(nxt, 10, 30)
        ev = [e for e in snap.get('calendar') or [] if a <= e['t'] <= b and e.get('impact') in ('High', 'Medium')]
        if ev:
            items.append('Before 10:30 am: ' + ', '.join(f"{e['title']} {watch.et(e['t']).strftime('%-I:%M %p')}" for e in ev[:6]) + '.')
        fed = [e for e in snap.get('calendar') or [] if e.get('kind') == 'fed' and a <= e['t'] <= watch.at_et(nxt, 16, 0)]
        if fed:
            items.append('Fed: ' + ', '.join(f"{e['title']} {watch.et(e['t']).strftime('%a %-I:%M %p')}" for e in fed[:3]) + '.')
        fut = {r['sym']: r for r in snap.get('rows') or []}
        f = [f"{n} {fut[s]['since_close']:+.2f}%" for s, n in (('ES=F', 'S&P'), ('NQ=F', 'Nasdaq')) if fut.get(s, {}).get('since_close') is not None]
        if f:
            items.append('Futures vs 4 pm: ' + ', '.join(f) + '.')
    except Exception:
        pass
    risky = bool(held_earn) or early
    head = ('Holding: ' + ', '.join(hd[:12]) + '.') if hd else 'You hold nothing (per your Portfolio tab and journal).'
    verdict = 'Busy night for what you hold.' if held_earn else ('Quiet night: nothing scheduled for what you hold.' if hd else 'Nothing held.')
    lines = [verdict, head] + items
    return {'at': now_ms, 'for': nxt.isoformat(), 'held': hd, 'risky': risky, 'text': '\n'.join(lines)}


def check_bedtime(cfg, state, now_ms):
    if not cfg['bedtime']:
        return
    d = watch.et(now_ms).date()
    # only on evenings that lead straight into a trading day (Sunday evening counts, Friday / Saturday don't)
    if _next_trading_day(d) != d + timedelta(days=1):
        return
    hh, mm = bedtime()
    t = watch.at_et(d, hh, mm)
    key = f'bed {d}'
    if t <= now_ms < t + 45 * 60000 and key not in state['sent']:
        state['sent'][key] = now_ms
        card = build_card(now_ms)
        _st['card'] = card
        state['card'] = card
        _send('before bed', '', 'Before bed: ' + ('heads-up' if card['risky'] else 'quiet night'), card['text'], 'crescent_moon')


# ------------------------------------------------------------------ combo alerts
def combos():
    return watch._read(COMBOS, [])


def _ratio_now(sym, now_ms):
    try:
        import signals
        v, normal = signals._volume_ratio(signals._volumes(), sym, now_ms)
        return v / normal if v and normal else None
    except Exception:
        return None


def cond_true(c, now_ms):
    sym = c['sym']
    with watch._lock:
        hist = list(watch._px.get(sym) or [])
    if not hist or now_ms - hist[-1][0] > 15 * 60000:
        return False
    last = hist[-1][1]
    k, v = c['kind'], c.get('value')
    if k == 'above':
        return last > v
    if k == 'below':
        return last < v
    if k in ('up_pct', 'down_pct'):
        ch = watch.pct(last, watch.price_at(hist, watch.last_close_ms(now_ms)))
        return ch is not None and (ch >= v if k == 'up_pct' else ch <= -abs(v))
    if k in ('above_onh', 'below_onl'):
        s, e = watch.overnight_window(now_ms)
        on = [p for t, p in hist if s <= t < e]
        if not on or now_ms < e:
            return False
        return last > max(on) if k == 'above_onh' else last < min(on)
    if k in ('above_pmh', 'below_pml'):
        d = watch.et(now_ms).date()
        a, b = watch.at_et(d, 4, 0), watch.at_et(d, 9, 30)
        pm = [p for t, p in hist if a <= t < b]
        if not pm or now_ms < b:
            return False
        return last > max(pm) if k == 'above_pmh' else last < min(pm)
    if k == 'vol_x':
        r = _ratio_now(sym, now_ms)
        return r is not None and r >= v
    return False


def describe_cond(c):
    k, v = c['kind'], c.get('value')
    if k in ('above', 'below'):
        return f"{c['sym']} {'above' if k == 'above' else 'below'} {v:g}"
    if k == 'up_pct':
        return f"{c['sym']} up {v:g}%+ vs close"
    if k == 'down_pct':
        return f"{c['sym']} down {abs(v):g}%+ vs close"
    if k == 'vol_x':
        return f"{c['sym']} volume {v:g}x normal"
    return f"{c['sym']} {COND_KINDS.get(k, k)}"


def check_combos(cfg, state, now_ms):
    if not cfg['combos']:
        return
    sess = session_name(now_ms)
    if sess == 'closed':
        return
    try:
        import signals
        label = signals._session_key(now_ms)
    except Exception:
        label = f'{watch.et(now_ms).date()} {sess}'
    for a in combos():
        if not a.get('on', True):
            continue
        key = f"combo {a['id']} {label}"
        if key in state['sent']:
            continue
        if all(cond_true(c, now_ms) for c in a['conds']):
            state['sent'][key] = now_ms
            text = ' AND '.join(describe_cond(c) for c in a['conds'])
            _send('combo', a['conds'][0]['sym'], f"{a['conds'][0]['sym']} combo alert", f'{text} ({sess}).', 'dart')


def add_combo(conds):
    clean = []
    for c in conds[:3]:
        sym = str(c.get('sym') or '').strip().upper()
        k = c.get('kind')
        if not sym or len(sym) > 12 or k not in COND_KINDS:
            raise ValueError('each condition needs a symbol and a choice')
        v = None
        if k in ('above', 'below', 'up_pct', 'down_pct', 'vol_x'):
            v = float(c.get('value'))
            if not v > 0:
                raise ValueError('numbers must be above 0')
        clean.append({'sym': sym, 'kind': k, 'value': v})
    if len(clean) < 2:
        raise ValueError('a combo alert needs two conditions')
    rows = combos()
    rows.append({'id': uuid.uuid4().hex[:8], 'conds': clean, 'on': True, 'created': _now()})
    watch._write(COMBOS, rows[-40:])


# ------------------------------------------------------------------ Mac heartbeat (healthchecks.io)
def save_heartbeat_url(url):
    """Write JB_HEARTBEAT_URL into settings.env (the Night desk's paste box) and use it straight away."""
    import re
    url = url.strip()
    if url and not re.fullmatch(r'https://hc-ping\.com/[A-Za-z0-9_/\-]{8,120}', url):
        raise ValueError('That is not a healthchecks.io ping link. It starts with https://hc-ping.com/')
    path = os.path.join(ROOT, 'settings.env')
    lines = open(path, encoding='utf-8').read().split('\n') if os.path.exists(path) else []
    for i, l in enumerate(lines):
        if l.strip().startswith('JB_HEARTBEAT_URL='):
            lines[i] = 'JB_HEARTBEAT_URL=' + url
            break
    else:
        if lines and lines[-1] == '':
            lines.pop()
        lines += ['JB_HEARTBEAT_URL=' + url, '']
    tmp = path + '.tmp'
    open(tmp, 'w', encoding='utf-8').write('\n'.join(lines))
    try:
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
    except OSError:
        pass
    os.replace(tmp, path)
    os.environ['JB_HEARTBEAT_URL'] = url
    _st['heartbeat'].update(ok=None, err='', at=None)


def heartbeat():
    url = os.environ.get('JB_HEARTBEAT_URL', '').strip()
    hb = _st['heartbeat']
    if not url:
        hb.update(ok=None, err='JB_HEARTBEAT_URL is not set in settings.env')
        return
    try:
        _http(url, timeout=15, data=f'board alive {datetime.now().isoformat(timespec="seconds")}'.encode(), method='POST')
        hb.update(at=_now(), ok=True, err='')
    except Exception as e:
        hb.update(ok=False, err=str(e)[:160])


# ------------------------------------------------------------------ 8:30 am data reaction
def check_reactions(cfg, state, now_ms):
    try:
        import world
        events = [e for e in json.loads(world.snapshot()).get('calendar') or []
                  if e.get('impact') == 'High' and e.get('kind') in ('data', 'fed')]
    except Exception:
        return
    done = {r['key'] for r in state['reactions']}
    for e in events:
        key = f"{e['t']} {e['title']}"
        if key in done or not (e['t'] + 5 * 60000 <= now_ms < e['t'] + 60 * 60000):
            continue
        row = {'key': key, 't': e['t'], 'title': e['title'], 'forecast': e.get('forecast', ''), 'actual': e.get('actual', ''),
               'previous': e.get('previous', ''), 'moves': {}}
        for sym, name in (('ES=F', 'S&P'), ('NQ=F', 'Nasdaq'), ('YM=F', 'Dow')):
            with watch._lock:
                hist = list(watch._px.get(sym) or [])
            before = watch.price_at(hist, e['t'] - 60000)
            m1, m5 = watch.price_at(hist, e['t'] + 60000), watch.price_at(hist, e['t'] + 5 * 60000)
            if before and m5:
                row['moves'][name] = {'m1': watch.pct(m1, before), 'm5': watch.pct(m5, before)}
        state['reactions'].insert(0, row)
        state['reactions'] = state['reactions'][:60]
        if cfg['data_830'] and row['moves']:
            exp = f" (actual {row['actual']} vs {row['forecast']} expected)" if row['actual'] and row['forecast'] else ''
            mv = ', '.join(f"{n} {m['m5']:+.2f}%" for n, m in row['moves'].items() if m['m5'] is not None)
            _send('data reaction', '', f"{e['title']}: futures {mv.split(',')[0]}", f"{e['title']}{exp}. Futures 5 minutes after: {mv}.", 'bar_chart')


# ------------------------------------------------------------------ Fed / CPI odds (Kalshi, read only)
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _price(m):
    for k in ('last_price_dollars', 'yes_bid_dollars'):
        v = _num(m.get(k))
        if v:
            return v
    v = _num(m.get('last_price'))
    return v / 100 if v else None


def refresh_odds():
    out = {}
    for key, series in (('fed', 'KXFEDDECISION'), ('cpi', 'KXCPI'), ('jobs', 'KXPAYROLLS')):
        try:
            body = json.loads(_http(KALSHI.format(series), headers={'Accept': 'application/json'}))
        except Exception as e:
            _st['odds_err'] = f'{series}: {str(e)[:120]}'
            continue
        evs = sorted(body.get('events') or [], key=lambda e: e.get('strike_date') or e.get('event_ticker') or '')
        if not evs:
            continue
        ev = evs[0]
        rows = []
        for m in ev.get('markets') or []:
            p = _price(m)
            if p is None:
                continue
            rows.append({'label': m.get('yes_sub_title') or m.get('subtitle') or m.get('title') or m.get('ticker'),
                         'p': round(p * 100), 'strike': _num(m.get('floor_strike'))})
        if key == 'fed':
            rows.sort(key=lambda r: -r['p'])
        else:
            rows.sort(key=lambda r: (r['strike'] is None, r['strike'] or 0))
        out[key] = {'title': ev.get('title') or series, 'sub': ev.get('sub_title') or '', 'when': ev.get('strike_date'),
                    'rows': rows[:14], 'link': 'https://kalshi.com/markets/' + series.lower()}
    if out:
        _st['odds'], _st['odds_at'] = out, _now()
        _st['odds_err'] = '' if len(out) >= 2 else _st['odds_err']


# ------------------------------------------------------------------ thread + API
def _loop():
    _st['phone_url'] = _find_phone_url()
    watch.push = push                      # every push from the board gets its tap link
    last = {'halts': 0, 'odds': 0, 'hb': 0}
    while True:
        now = _now()
        try:
            cfg = settings()
            state = _state()
            if now - last['halts'] >= 60000:
                last['halts'] = now
                check_halts(cfg, state, now)
            check_bedtime(cfg, state, now)
            check_combos(cfg, state, now)
            check_reactions(cfg, state, now)
            for k in [k for k, t in state['sent'].items() if now - t > 4 * 86400000]:
                del state['sent'][k]
            watch._write(STATE, state)
            if now - last['hb'] >= 5 * 60000:
                last['hb'] = now
                heartbeat()
            if now - last['odds'] >= 30 * 60000:
                last['odds'] = now
                refresh_odds()
        except Exception as e:
            print(f'alerts2 error: {e!r}', flush=True)
        time.sleep(10)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_loop, name='alerts2', daemon=True).start()


def snapshot():
    now = _now()
    state = watch._read(STATE, {})
    mine, hd = _symbols(), held()
    halts = sorted(_st['halts'], key=lambda h: -(h['halted'] or 0))
    today = [dict(h, mine=h['sym'] in mine, held=h['sym'] in hd) for h in halts if h['halted'] and now - h['halted'] < 36 * 3600000]
    return {'server_ms': now, 'settings': settings(), 'halts': today[:150], 'halts_at': _st['halts_at'], 'halts_err': _st['halts_err'],
            'card': _st['card'] or state.get('card'), 'bedtime': '%d:%02d' % bedtime(),
            'combos': [dict(a, text=' AND '.join(describe_cond(c) for c in a['conds'])) for a in combos()],
            'kinds': COND_KINDS, 'heartbeat': _st['heartbeat'], 'heartbeat_set': bool(os.environ.get('JB_HEARTBEAT_URL', '').strip()),
            'phone_url': _st['phone_url'], 'topic_set': bool(watch.topic()),
            'reactions': (state.get('reactions') or [])[:20], 'odds': _st['odds'], 'odds_at': _st['odds_at'], 'odds_err': _st['odds_err'],
            'log': watch._read(LOG, [])[:30]}


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    return 404, {'error': 'unknown'}


def post(sub, data):
    try:
        if sub == 'settings':
            s = settings()
            for k in DEFAULTS:
                if k in (data.get('settings') or {}):
                    s[k] = bool(data['settings'][k])
            watch._write(SETTINGS, s)
        elif sub == 'combo':
            add_combo(data.get('conds') or [])
        elif sub == 'combo/remove':
            watch._write(COMBOS, [a for a in combos() if a['id'] != str(data.get('id'))])
        elif sub == 'card':                          # make the before-bed card now (button)
            _st['card'] = build_card(_now())
        elif sub == 'heartbeat':                     # send one ping now (button); with a url: save it first (jb-ndtabs)
            if data.get('url') is not None:
                save_heartbeat_url(str(data.get('url')))
            heartbeat()
        elif sub == 'test-tap':
            err = push('Night desk', 'Test: tap this notification to open the Night desk.', 'white_check_mark')
            if err:
                return 400, {'error': 'Could not send: ' + err}
        else:
            return 404, {'error': 'unknown'}
    except (ValueError, TypeError) as e:
        return 400, {'error': str(e)}
    return 200, snapshot()
