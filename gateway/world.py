"""The World tab (jb-world), running inside the gateway:
  * every few minutes fetches free, delayed prices from Yahoo Finance for overnight futures
    (S&P, Nasdaq, Dow, Russell, oil, gold, ...), the VIX, the 10-year yield, the dollar,
    Asia and Europe stock indexes, and Bitcoin / Ether
  * works out each one's move since the last 4 pm New York close, and an "overnight mood"
    score (risk-on vs risk-off) from them
  * an economic calendar for the next 7 days: US data releases with forecasts (Forex Factory's
    free feed), Fed meetings and speeches, and US Treasury auctions (TreasuryDirect). No phone push.
  * Sunday evening, once futures have opened, sends one phone push (ntfy, like the alerts):
    where S&P / Nasdaq / Dow futures opened vs Friday's 4 pm close
  * /api/world   GET: everything above as JSON (web/world.html draws it)
Nothing here places trades or touches the bridges. Data is cached in data/world.json."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import watch                     # New York time helpers + phone push (gateway/watch.py)

try:                             # talks to Yahoo the way Chrome does (Yahoo refuses plain Python since 2025)
    import sys as _sys
    if _sys.platform == 'darwin':   # its Mac build forgets to load this macOS part itself
        import ctypes
        ctypes.CDLL('/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration', mode=ctypes.RTLD_GLOBAL)
    from curl_cffi import requests as _browser
except Exception:
    _browser = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'world.json')
STATE = os.path.join(DATA, 'world_state.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
QUOTE_EVERY_S = 300              # all quotes refreshed every 5 minutes (Yahoo limits how often we may ask)
CAL_EVERY_S = 3600               # calendar refreshed every hour

# (Yahoo symbol, name, group, short explanation)
SYMBOLS = [
    ('ES=F', 'S&P 500 futures', 'futures', 'Where the S&P 500 is heading before the open.'),
    ('NQ=F', 'Nasdaq 100 futures', 'futures', 'Big tech.'),
    ('YM=F', 'Dow futures', 'futures', '30 large US companies.'),
    ('RTY=F', 'Russell 2000 futures', 'futures', 'Small US companies.'),
    ('CL=F', 'Crude oil', 'futures', 'Oil, per barrel. Moves energy stocks and inflation worries.'),
    ('NG=F', 'Natural gas', 'futures', ''),
    ('GC=F', 'Gold', 'futures', 'A "safe place" for money when people are nervous.'),
    ('SI=F', 'Silver', 'futures', ''),
    ('HG=F', 'Copper', 'futures', 'Used in everything; a hint about the world economy.'),
    ('^VIX', 'VIX (fear index)', 'rates', 'Expected S&P swings. Up = more nervous.'),
    ('^TNX', '10-year yield', 'rates', 'US 10-year interest rate. Fast rises often hurt tech stocks.'),
    ('DX-Y.NYB', 'US dollar index', 'rates', 'The dollar vs other big currencies.'),
    ('EURUSD=X', 'Euro (in $)', 'rates', ''),
    ('JPY=X', 'Dollar in yen', 'rates', 'Big yen moves can shake markets worldwide.'),
    ('^N225', 'Tokyo (Nikkei 225)', 'asia', ''),
    ('^HSI', 'Hong Kong (Hang Seng)', 'asia', ''),
    ('000001.SS', 'Shanghai Composite', 'asia', ''),
    ('^KS11', 'Seoul (KOSPI)', 'asia', 'Home of Samsung; chip stocks.'),
    ('^TWII', 'Taiwan (TAIEX)', 'asia', 'Home of TSMC; chip stocks.'),
    ('^AXJO', 'Sydney (ASX 200)', 'asia', ''),
    ('^BSESN', 'India (Sensex)', 'asia', ''),
    ('^FTSE', 'London (FTSE 100)', 'europe', ''),
    ('^GDAXI', 'Frankfurt (DAX)', 'europe', ''),
    ('^FCHI', 'Paris (CAC 40)', 'europe', ''),
    ('^STOXX50E', 'Euro Stoxx 50', 'europe', '50 biggest companies in the euro area.'),
    ('BTC-USD', 'Bitcoin', 'crypto', 'Trades 24/7, so it shows the mood even on weekends.'),
    ('ETH-USD', 'Ether', 'crypto', ''),
]
GROUPS = [('futures', 'Overnight futures'), ('rates', 'Rates, fear index and currencies'),
          ('asia', 'Asia'), ('europe', 'Europe'), ('crypto', 'Crypto (24/7)')]

# Mood meter: (symbol or group, weight, how many % counts as a "full" move, direction)
MOOD = [('ES=F', 2.0, 0.8, 1), ('NQ=F', 2.0, 1.0, 1), ('asia', 1.5, 1.2, 1), ('europe', 1.5, 1.0, 1),
        ('BTC-USD', 1.0, 3.0, 1), ('^VIX', 1.0, 6.0, -1), ('DX-Y.NYB', 0.5, 0.5, -1), ('GC=F', 0.5, 1.0, -1)]
MOOD_WHY = {'ES=F': 'S&P futures', 'NQ=F': 'Nasdaq futures', 'asia': 'Asia', 'europe': 'Europe',
            'BTC-USD': 'Bitcoin', '^VIX': 'Fear index (VIX)', 'DX-Y.NYB': 'US dollar', 'GC=F': 'Gold'}

# FOMC (Fed interest-rate) decisions, 2:00 pm New York time (second day of each meeting)
FOMC = ['2026-01-28', '2026-03-18', '2026-04-29', '2026-06-17', '2026-07-29', '2026-09-16',
        '2026-10-28', '2026-12-09']

_lock = threading.Lock()
_quotes = {}                     # symbol -> dict (see _quote)
_cal = {'events': [], 'at': 0, 'errors': []}
_status = {'started': False, 'quotes_at': 0, 'errors': {}, 'wait_until': 0, 'wait_s': 0, 'note': ''}
HOSTS = ('query1.finance.yahoo.com', 'query2.finance.yahoo.com')


def _ctx():
    return watch._ctx()


_yh = {'cookie': '', 'crumb': '', 'at': 0, 'session': None}   # Yahoo visitor cookie + "crumb", like a web browser has
_yl = threading.RLock()            # one thread at a time renews the cookie + crumb (World, Analysis, Market data share them)


def _browser_get(url, timeout=20):
    """GET through curl_cffi pretending to be Chrome; keeps Yahoo's cookies between calls."""
    with _yl:
        if _yh['session'] is None:
            _yh['session'] = _browser.Session(impersonate='chrome')
        s = _yh['session']
    r = s.get(url, timeout=timeout)
    if r.status_code >= 400:
        raise OSError(f'HTTP Error {r.status_code}' + (': Too Many Requests' if r.status_code == 429 else ''))
    return r


def _yahoo_session():
    """Yahoo answers "too many requests" to visitors without its cookie, so get one the way a
    browser does (fc.yahoo.com sets it), plus the matching crumb. Renewed every 6 hours."""
    if _yh['crumb'] and time.time() - _yh['at'] < 6 * 3600:
        return
    with _yl:
        if _yh['crumb'] and time.time() - _yh['at'] < 6 * 3600:
            return                              # another thread just renewed it
        _yahoo_renew()


def _yahoo_renew():
    _yh.update(cookie='', crumb='', at=time.time())
    if _browser is not None:
        _yh['session'] = None                   # fresh cookies
        try:
            _browser_get('https://fc.yahoo.com/')
        except Exception:
            pass                                # answers 404 but sets the cookie
        try:
            c = _browser_get('https://query1.finance.yahoo.com/v1/test/getcrumb', 15).text.strip()
            _yh['crumb'] = c if c and '<' not in c and len(c) < 40 else ''
            _yh['cookie'] = 'browser'
        except Exception as e:
            print(f'world: no Yahoo crumb: {e!r}', flush=True)
        return
    hdrs = None
    try:
        with urllib.request.urlopen(urllib.request.Request('https://fc.yahoo.com/', headers={'User-Agent': UA}),
                                    timeout=15, context=_ctx()) as r:
            hdrs = r.headers
    except urllib.error.HTTPError as e:         # it answers 404, but still sets the cookie
        hdrs = e.headers
    except Exception as e:
        print(f'world: no Yahoo cookie: {e!r}', flush=True)
    cookies = [c.split(';', 1)[0] for c in (hdrs.get_all('Set-Cookie') or [])] if hdrs else []
    _yh['cookie'] = '; '.join(c for c in cookies if '=' in c)
    if not _yh['cookie']:
        return
    try:
        req = urllib.request.Request('https://query1.finance.yahoo.com/v1/test/getcrumb',
                                     headers={'User-Agent': UA, 'Cookie': _yh['cookie']})
        with urllib.request.urlopen(req, timeout=15, context=_ctx()) as r:
            c = r.read().decode('utf-8').strip()
        _yh['crumb'] = c if c and '<' not in c and len(c) < 40 else ''
    except Exception as e:
        print(f'world: no Yahoo crumb: {e!r}', flush=True)


def _get(url, timeout=20):
    try:
        return _get_once(url, timeout)
    except Exception as e:
        if '.yahoo.com/' not in url or 'HTTP Error 401' not in str(e) or time.time() - _yh.get('renewed', 0) < 600:
            raise
        # Yahoo refused our key ("crumb"), e.g. one fetched while the Mac was still getting
        # online after a restart: get a fresh cookie + crumb and try once more
        with _yl:
            if time.time() - _yh.get('renewed', 0) >= 600:
                _yh.update(crumb='', at=0, renewed=time.time())     # at most once every 10 minutes
        return _get_once(url, timeout)


def _get_once(url, timeout=20):
    headers = {'User-Agent': UA, 'Accept': 'application/json', 'Accept-Language': 'en-US,en;q=0.9'}
    if '.yahoo.com/' in url:
        _yahoo_session()
        if _browser is not None:
            if _yh['crumb']:
                url += ('&' if '?' in url else '?') + 'crumb=' + urllib.parse.quote(_yh['crumb'])
            return _browser_get(url, timeout).json()
        if _yh['cookie']:
            headers['Cookie'] = _yh['cookie']
        if _yh['crumb']:
            url += ('&' if '?' in url else '?') + 'crumb=' + urllib.parse.quote(_yh['crumb'])
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return json.loads(r.read().decode('utf-8'))


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception as e:
        print(f'world: could not save {path}: {e!r}', flush=True)


def _pct(a, b):
    return round((a / b - 1) * 100, 2) if a and b else None


# ------------------------------------------------------------------ quotes
def _quote(sym, now_ms, host=HOSTS[0]):
    body = _get(f'https://{host}/v8/finance/chart/' + urllib.parse.quote(sym) +
                '?range=5d&interval=15m&includePrePost=true')
    return _parse(((body.get('chart') or {}).get('result') or [None])[0] or {}, sym, now_ms)


def _spark(syms, now_ms, host):
    """Up to 10 symbols in ONE request (far fewer requests than one per symbol)."""
    body = _get(f'https://{host}/v8/finance/spark?symbols=' + ','.join(urllib.parse.quote(s) for s in syms) +
                '&range=5d&interval=15m&includePrePost=true')
    out = {}
    if isinstance(body, dict) and isinstance(body.get('spark'), dict):          # chart-style answer
        for r in body['spark'].get('result') or []:
            res = (r.get('response') or [None])[0]
            if r.get('symbol') in syms and res:
                try:
                    out[r['symbol']] = _parse(res, r['symbol'], now_ms)
                except Exception:
                    pass
    elif isinstance(body, dict):                                                # {symbol: {timestamp, close}}
        for sym in syms:
            r = body.get(sym)
            if isinstance(r, dict) and r.get('timestamp'):
                res = {'meta': {k: v for k, v in r.items() if k not in ('timestamp', 'close')},
                       'timestamp': r['timestamp'], 'indicators': {'quote': [{'close': r.get('close') or []}]}}
                try:
                    out[sym] = _parse(res, sym, now_ms)
                except Exception:
                    pass
    return out


def _parse(res, sym, now_ms):
    meta = res.get('meta') or {}
    ts = res.get('timestamp') or []
    closes = (((res.get('indicators') or {}).get('quote') or [{}])[0].get('close')) or []
    bars = [(int(t) * 1000, float(c)) for t, c in zip(ts, closes) if c]
    if not bars:
        raise ValueError('no prices')
    last_t, last = bars[-1]
    if meta.get('regularMarketPrice') and (meta.get('regularMarketTime') or 0) * 1000 >= last_t:
        last, last_t = float(meta['regularMarketPrice']), int(meta['regularMarketTime']) * 1000
    close_ms = watch.last_close_ms(now_ms)
    ref = watch.price_at(bars, close_ms)
    # the exchange's own previous day close (by the exchange's local date)
    off = int(meta.get('gmtoffset') or 0) * 1000
    day = lambda t: (t + off) // 86400000
    prev = None
    for t, c in reversed(bars):
        if day(t) < day(last_t):
            prev = c
            break
    period = (meta.get('currentTradingPeriod') or {}).get('regular') or {}
    start, end = (period.get('start') or 0) * 1000, (period.get('end') or 0) * 1000
    group = next((g for s, _, g, _ in SYMBOLS if s == sym), '')
    if group in ('asia', 'europe') and start and end:
        is_open = start <= now_ms < end
    else:
        is_open = now_ms - last_t < 45 * 60000
    spark = [round(c, 6) for t, c in bars if t >= now_ms - 24 * 3600000]
    step = max(1, len(spark) // 60)
    spark = spark[::step][-60:]
    return {'last': last, 'last_ms': last_t, 'since_close': _pct(last, ref), 'close_ref': ref,
            'day': _pct(last, prev), 'open': is_open, 'spark': spark,
            'tz': meta.get('exchangeTimezoneName') or '', 'open_ms': start, 'close_ms': end}


def _limited(e):
    return '429' in str(e) or 'Too Many' in str(e)


def _back_off():
    """Yahoo said "too many requests": wait 5, 10, 20, then 30 minutes before asking again."""
    _yh['at'] = 0                                   # get a fresh cookie next time
    with _lock:
        _status['wait_s'] = min(1800, (_status['wait_s'] or 150) * 2)
        _status['wait_until'] = time.time() + _status['wait_s']
        _status['note'] = ('Yahoo is asking us to slow down; trying again at ' +
                           watch.et(_status['wait_until'] * 1000).strftime('%-I:%M %p') + ' NY time. Last prices shown.')
    print(f"world: Yahoo rate limit, waiting {_status['wait_s']} s", flush=True)


def refresh_quotes():
    if time.time() < _status['wait_until']:
        return
    now = int(time.time() * 1000)
    syms = [s for s, *_ in SYMBOLS]
    got = {}
    for i in range(0, len(syms), 10):                    # batches of 10 first
        try:
            got.update(_spark(syms[i:i + 10], now, HOSTS[(i // 10) % 2]))
        except Exception as e:
            if _limited(e):
                _back_off()
                break
        time.sleep(3)
    else:
        n = 0
        for sym in syms:                                 # anything the batches missed, one by one, slowly
            if sym in got:
                continue
            try:
                got[sym] = _quote(sym, now, HOSTS[n % 2])
            except Exception as e:
                if _limited(e):
                    _back_off()
                    break
                with _lock:
                    _status['errors'][sym] = str(e)[:70]
            n += 1
            time.sleep(2.5)
    with _lock:
        for sym, q in got.items():
            _quotes[sym] = q
            _status['errors'].pop(sym, None)
        if got:
            _status['quotes_at'] = now
        if got and time.time() >= _status['wait_until']:
            _status['wait_s'] = 0
            _status['note'] = ''


def _group_avg(group):
    vals = [q['since_close'] for s, _, g, _ in SYMBOLS if g == group
            for q in [_quotes.get(s)] if q and q.get('since_close') is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def mood():
    """Score -100 (risk-off) .. +100 (risk-on) from moves since the 4 pm close."""
    import math
    parts, tot, wsum = [], 0.0, 0.0
    for key, w, full, sign in MOOD:
        v = _group_avg(key) if key in ('asia', 'europe') else (_quotes.get(key) or {}).get('since_close')
        if v is None:
            continue
        s = math.tanh(sign * v / full)          # -1 .. +1
        tot += w * s
        wsum += w
        parts.append({'name': MOOD_WHY[key], 'pct': v, 'push': round(w * s, 2),
                      'note': ('up' if v > 0 else 'down' if v < 0 else 'flat') + (' (counts the other way)' if sign < 0 else '')})
    if not wsum:
        return None
    score = int(round(100 * tot / wsum))
    label = 'Risk-on' if score >= 25 else 'Risk-off' if score <= -25 else 'Mixed'
    parts.sort(key=lambda p: -abs(p['push']))
    return {'score': score, 'label': label, 'parts': parts}


# ------------------------------------------------------------------ calendar
def _ff_events():
    out = []
    for name in ('thisweek', 'nextweek'):
        try:
            rows = _get(f'https://nfs.faireconomy.media/ff_calendar_{name}.json')
        except Exception as e:
            if name == 'thisweek':
                raise
            continue
        for r in rows if isinstance(rows, list) else []:
            if (r.get('country') or '').upper() != 'USD':
                continue
            try:
                t = int(datetime.fromisoformat(str(r.get('date'))).timestamp() * 1000)
            except Exception:
                continue
            title = str(r.get('title') or '').strip()
            low = title.lower()
            kind = 'fed' if ('fomc' in low or 'fed ' in low or 'federal funds' in low or 'powell' in low) else \
                   'auction' if 'auction' in low else 'data'
            out.append({'t': t, 'title': title, 'kind': kind, 'impact': str(r.get('impact') or ''),
                        'forecast': str(r.get('forecast') or ''), 'previous': str(r.get('previous') or ''),
                        'actual': str(r.get('actual') or ''), 'src': 'Forex Factory'})
    return out


def _treasury_events():
    rows = _get('https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json')
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            d = datetime.fromisoformat(str(r.get('auctionDate'))[:10])
        except Exception:
            continue
        hh, mm = 13, 0
        tm = str(r.get('closingTimeCompetitive') or '').strip().upper()   # e.g. "01:00 PM"
        try:
            x = datetime.strptime(tm, '%I:%M %p')
            hh, mm = x.hour, x.minute
        except Exception:
            pass
        amt = r.get('offeringAmount')
        try:
            amt = f'${float(amt) / 1e9:.0f}B'
        except Exception:
            amt = ''
        term = str(r.get('securityTerm') or '').strip()
        typ = str(r.get('securityType') or '').strip()
        big = typ in ('Note', 'Bond') and any(k in term for k in ('10-Year', '20-Year', '30-Year'))
        out.append({'t': watch.at_et(d.date(), hh, mm), 'title': f'Treasury auction: {term} {typ}'.strip(),
                    'kind': 'auction', 'impact': 'Medium' if big else 'Low', 'forecast': amt, 'previous': '',
                    'actual': '', 'src': 'TreasuryDirect'})
    return out


def _fomc_events():
    out = []
    for d in FOMC:
        day = datetime.fromisoformat(d).date()
        out.append({'t': watch.at_et(day, 14, 0), 'title': 'Fed interest-rate decision (FOMC)', 'kind': 'fed',
                    'impact': 'High', 'forecast': '', 'previous': '', 'actual': '', 'src': 'Federal Reserve'})
    return out


def refresh_calendar():
    events, errors = [], []
    for name, fn in (('economic data', _ff_events), ('Treasury auctions', _treasury_events), ('Fed', _fomc_events)):
        try:
            events += fn()
        except Exception as e:
            errors.append(f'{name}: {str(e)[:100]}')
    # the Fed decision is in both lists: keep one per hour
    seen, uniq = set(), []
    for e in sorted(events, key=lambda e: (e['t'], e['src'] != 'Forex Factory')):
        low = e['title'].lower()
        decision = e['kind'] == 'fed' and ('decision' in low or 'federal funds' in low)
        key = ('fed', e['t'] // 3600000) if decision else (e['title'], e['t'])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    with _lock:
        if uniq or not _cal['events']:
            _cal['events'] = uniq
        _cal['at'] = int(time.time() * 1000)
        _cal['errors'] = errors


# ------------------------------------------------------------------ Sunday futures push
def _sunday_push(now_ms):
    d = watch.et(now_ms)
    if d.weekday() != 6 or not (18 * 60 + 5 <= d.hour * 60 + d.minute < 21 * 60):
        return
    st = _read(STATE, {})
    if st.get('sunday_push') == str(d.date()):
        return
    open_ms = watch.at_et(d.date(), 18, 0)
    lines, fresh = [], False
    for sym, name in (('ES=F', 'S&P'), ('NQ=F', 'Nasdaq'), ('YM=F', 'Dow')):
        q = _quotes.get(sym)
        if not q or q.get('since_close') is None:
            continue
        fresh = fresh or q['last_ms'] >= open_ms
        lines.append(f"{name} {q['since_close']:+.2f}%")
    if not fresh or not lines:
        return
    st['sunday_push'] = str(d.date())
    _write(STATE, st)
    m = mood()
    text = 'Futures opened vs Friday 4 pm: ' + ', '.join(lines) + '.'
    if m:
        text += f" Overnight mood: {m['label']} ({m['score']:+d})."
    watch.push_later('Sunday futures open', text, 'crescent_moon')
    print('world: Sunday futures push sent', flush=True)


# ------------------------------------------------------------------ thread + API
def _loop():
    c = _read(CACHE, {})
    with _lock:
        _quotes.update(c.get('quotes') or {})
        _status['quotes_at'] = c.get('quotes_at') or 0
        if c.get('calendar'):
            _cal['events'] = c['calendar']
    last_q = last_c = 0
    while True:
        try:
            now = time.time()
            if now - last_c >= CAL_EVERY_S:
                last_c = now
                refresh_calendar()
            if now - last_q >= QUOTE_EVERY_S:
                last_q = now
                refresh_quotes()
                with _lock:
                    _write(CACHE, {'quotes': _quotes, 'quotes_at': _status['quotes_at'], 'calendar': _cal['events']})
            _sunday_push(int(time.time() * 1000))
        except Exception as e:           # never let the thread die
            print(f'world error: {e!r}', flush=True)
        time.sleep(15)


def start():
    with _lock:
        if _status['started']:
            return
        _status['started'] = True
    threading.Thread(target=_loop, name='world', daemon=True).start()


def snapshot():
    now = int(time.time() * 1000)
    with _lock:
        rows = []
        for sym, name, group, note in SYMBOLS:
            q = _quotes.get(sym)
            if q and now - (q.get('last_ms') or 0) > 4 * 86400000:
                q = None                         # too old to show
            rows.append(dict(q or {}, sym=sym, name=name, group=group, note=note,
                             error=_status['errors'].get(sym, '') if not q else ''))
        events = [e for e in _cal['events'] if e['t'] >= now - 6 * 3600000 and e['t'] <= now + 8 * 86400000]
        out = {'server_ms': now, 'close_ms': watch.last_close_ms(now), 'quotes_at': _status['quotes_at'],
               'groups': GROUPS, 'rows': rows, 'mood': mood(), 'calendar': events,
               'calendar_at': _cal['at'], 'calendar_errors': _cal['errors'], 'note': _status['note']}
    return json.dumps(out)
