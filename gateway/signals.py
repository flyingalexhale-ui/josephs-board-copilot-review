"""The Signals tab (jb-signals), running inside the gateway. Everything here is free:
  * SEC filings: a phone push when a stock on the board files an 8-K (big news), an insider BUYS
    shares (Form 4), or an activist investor files a 13D. From SEC EDGAR, checked every 15 min.
  * Social buzz: how fast people are posting about each stock on StockTwits, and a push when it
    suddenly jumps to several times its normal pace.
  * Smarter alerts (phone push, each can be turned off on the Signals tab):
      - big move: a stock moves 3% or more vs the 4 pm close (once per session: after-hours,
        overnight, pre-market, regular)
      - fast move: 1.5% or more within 15 minutes
      - volume: this hour's volume so far is 3x the normal for this hour (last 5 trading days)
      - crossed yesterday's high or low (regular-session high / low)
      - 4 am gap list: one push at 4 am with every stock 2%+ away from the 4 pm close
  * Earnings night: on a report day, each stock's typical earnings move (its last 4 reports) next
    to how much it has moved so far, on the board tiles; a push if it moves more than typical.
  * Overnight summary: one push at 7:30 am on trading days (JB_SUMMARY_TIME in settings.env):
    the overnight session's biggest movers, futures, the overnight mood and today's big events.
  * "Why is it moving?": /api/why?sym=X pulls headlines, filings, its sector, the market and buzz
    together, for the WHY button on the board tiles (web/signals.js).
  /api/signals  GET: the Signals tab's data; POST {settings: {...}}: save the switches.
Uses the board's own price record (gateway/watch.py). Nothing here places trades."""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import watch                     # price record, New York time, phone push
import world                     # Yahoo (browser-style), futures, mood, calendar

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
SETTINGS = os.path.join(DATA, 'signals_settings.json')
STATE = os.path.join(DATA, 'signals_state.json')        # what was already sent, buzz history, filings
LOG = os.path.join(DATA, 'signals_log.json')
EARN = os.path.join(DATA, 'earnings.json')               # the scanner's earnings calendar
sys.path.insert(0, os.path.join(ROOT, 'lib'))
try:
    from nyse_hours import trading_day, nyse_early_closes
except Exception:
    def trading_day(d):
        return d.weekday() < 5

    def nyse_early_closes(y):
        return frozenset()

DEFAULTS = {
    'big_move': {'on': True, 'pct': 3.0},
    'fast_move': {'on': True, 'pct': 1.5, 'mins': 15},
    'volume': {'on': True, 'x': 3.0},
    'yday': {'on': True},
    'gap4': {'on': True, 'pct': 2.0},
    'buzz': {'on': True, 'x': 3.0},
    'sec': {'on': True},
    'earnings': {'on': True},
    'summary': {'on': True},
}
SECTOR_ETF = {'Technology': 'XLK', 'Financial Services': 'XLF', 'Healthcare': 'XLV', 'Energy': 'XLE',
              'Consumer Cyclical': 'XLY', 'Consumer Defensive': 'XLP', 'Industrials': 'XLI', 'Utilities': 'XLU',
              'Real Estate': 'XLRE', 'Basic Materials': 'XLB', 'Communication Services': 'XLC'}
ITEMS_8K = {'1.01': 'signed a major agreement', '1.02': 'ended a major agreement', '1.03': 'bankruptcy',
            '1.05': 'cybersecurity incident', '2.01': 'bought or sold a business', '2.02': 'quarterly results',
            '2.03': 'took on new debt', '2.05': 'layoffs or restructuring', '2.06': 'write-down',
            '3.01': 'stock listing problem', '3.02': 'sold new shares', '4.01': 'changed auditor',
            '4.02': 'past financials unreliable', '5.01': 'change of control', '5.02': 'executive or board change',
            '5.03': 'bylaw change', '5.07': 'shareholder vote results', '7.01': 'investor update',
            '8.01': 'other news'}
UA = world.UA

_lock = threading.Lock()
_st = {'started': False, 'errors': {}, 'sec_note': '', 'buzz': {}, 'moves': {}}
_last = {}                       # sym -> last price seen by the alert checker


# ------------------------------------------------------------------ small helpers
def _read(path, default):
    return world._read(path, default)


def _write(path, obj):
    world._write(path, obj)


def settings():
    s = _read(SETTINGS, {})
    out = {}
    for k, d in DEFAULTS.items():
        v = dict(d)
        if isinstance(s.get(k), dict):
            for kk in d:
                if kk not in s[k]:
                    continue
                if isinstance(d[kk], bool):
                    v[kk] = bool(s[k][kk])
                elif isinstance(s[k][kk], (int, float)) and not isinstance(s[k][kk], bool):
                    v[kk] = type(d[kk])(s[k][kk])
        out[k] = v
    return out


def _now():
    return int(time.time() * 1000)


def session_at(ms):
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    day = d.date()
    nxt = day + timedelta(days=1)
    if m < 4 * 60:
        return 'overnight' if trading_day(day) else 'closed'
    if not trading_day(day):
        return 'overnight' if m >= 20 * 60 and trading_day(nxt) else 'closed'
    if m < 9 * 60 + 30:
        return 'pre-market'
    close = 13 * 60 if day in nyse_early_closes(day.year) else 16 * 60     # holidays and early closes known
    if m < close:
        return 'regular'
    if m < close + 4 * 60:
        return 'after-hours'
    if m < 20 * 60:
        return 'closed'
    return 'overnight' if trading_day(nxt) else 'closed'


def _session_key(ms):
    """Which session this is, as a label that changes every session (the overnight one is
    named after the evening it started)."""
    d = watch.et(ms)
    s = session_at(ms)
    day = d.date() - timedelta(days=1) if s == 'overnight' and d.hour < 12 else d.date()
    return f'{day} {s}'


def _http(url, headers=None, browser=False, timeout=20):
    """GET -> bytes. browser=True goes through the Chrome-like helper when it is installed."""
    if browser and world._browser is not None:
        with _lock:
            if _st.get('session') is None:
                _st['session'] = world._browser.Session(impersonate='chrome')
            sess = _st['session']
        r = sess.get(url, headers=headers or {}, timeout=timeout)
        if r.status_code >= 400:
            raise OSError(f'HTTP Error {r.status_code}')
        return r.content
    h = {'User-Agent': UA, 'Accept': 'application/json'}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout, context=world._ctx()) as r:
        return r.read()


def _log(kind, sym, text, link=''):
    log = _read(LOG, [])
    log.insert(0, {'at': _now(), 'kind': kind, 'sym': sym, 'text': text, 'link': link})
    _write(LOG, log[:200])


def _send(kind, sym, title, text, link='', tags='bell'):
    if sym and kind in ('big move', 'fast move', 'yesterday high', 'yesterday low', 'volume'):   # jb-why: the board's own reason
        try:
            import why
            w = why.line(sym)
            if w:
                text = text + ' Why: ' + w + '.'
        except Exception:
            pass
    _log(kind, sym, text, link)
    print(f'signals: {kind} {sym}: {text}', flush=True)
    watch.push_later(title, text, tags)


def _symbols():
    """Stocks and ETFs on the board / scanner: {sym: 'stock'|'etf'}."""
    try:
        import news
        return {s: e['kind'] for s, e in news.tracked().items() if e['kind'] in ('stock', 'etf')}
    except Exception:
        return {}


# ------------------------------------------------------------------ price-based alerts
def _yday_range(hist, now_ms):
    """High and low of the last finished regular session (9:30-4 pm) in the price record."""
    d = watch.et(now_ms).date()
    if watch.et(now_ms).hour * 60 + watch.et(now_ms).minute < 16 * 60:
        d -= timedelta(days=1)
    for _ in range(6):
        if trading_day(d):
            break
        d -= timedelta(days=1)
    a, b = watch.at_et(d, 9, 30), watch.at_et(d, 16, 0)
    px = [p for t, p in hist if a <= t <= b]
    return (max(px), min(px), d) if len(px) >= 30 else (None, None, d)


def _volume_ratio(vol, sym, now_ms):
    """(volume this clock hour so far, normal for the same minutes over the last 5 days)."""
    rows = vol.get(sym)
    if not rows:
        return None, None
    start = now_ms - now_ms % 3600000
    mins = (now_ms - start) // 60000
    if mins < 10:
        return None, None
    d = {int(m): v for m, v in rows}
    now_v = sum(v for m, v in d.items() if start <= m < now_ms)
    past = []
    for k in range(1, 8):
        s = start - k * 86400000
        if not trading_day(watch.et(s).date()):
            continue
        past.append(sum(v for m, v in d.items() if s <= m < s + mins * 60000))
        if len(past) == 5:
            break
    past = [p for p in past if p > 0]
    if len(past) < 3:
        return now_v, None
    return now_v, sum(past) / len(past)


_vol_cache = {'mt': 0, 'data': {}}


def _volumes():
    best = {}
    for b in ('webull', 'ibkr'):              # IBKR read last so it wins
        p = os.path.join(DATA, b + '_volume.json')
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        key = (b, mt)
        if _vol_cache.get(b, (None,))[0] != key:
            _vol_cache[b] = (key, _read(p, {}))
        best.update(_vol_cache[b][1])
    return best


def check_prices(cfg, state, now_ms):
    syms = _symbols()
    sent = state.setdefault('sent', {})
    sess = _session_key(now_ms)
    s_name = session_at(now_ms)
    close_ms = watch.last_close_ms(now_ms)
    vol = _volumes() if cfg['volume']['on'] and s_name != 'closed' else {}
    for sym in syms:
        with watch._lock:
            hist = list(watch._px.get(sym) or [])
        if len(hist) < 2 or now_ms - hist[-1][0] > 10 * 60000:
            continue
        last = hist[-1][1]
        ref = watch.price_at(hist, close_ms)
        vs = watch.pct(last, ref)
        # big move vs the 4 pm close, once per session
        if cfg['big_move']['on'] and vs is not None and abs(vs) >= cfg['big_move']['pct']:
            k = f'big {sym} {sess}'
            if k not in sent:
                sent[k] = now_ms
                _send('big move', sym, f"{sym} {'up' if vs > 0 else 'down'} {abs(vs):.1f}% since the close",
                      f"Heads up: {sym} is {'up' if vs > 0 else 'down'} {abs(vs):.1f}% since the 4 pm close, now {last:g} ({s_name}).", tags='rotating_light')
        # fast move within N minutes
        fm = cfg['fast_move']
        if fm['on']:
            then = watch.price_at(hist, now_ms - int(fm['mins']) * 60000)
            ch = watch.pct(last, then)
            if ch is not None and abs(ch) >= fm['pct'] and hist[0][0] <= now_ms - int(fm['mins']) * 60000:
                k = f'fast {sym}'
                if now_ms - sent.get(k, 0) > 60 * 60000:          # at most once an hour per stock
                    sent[k] = now_ms
                    _send('fast move', sym, f"{sym} {'jumped' if ch > 0 else 'dropped'} {abs(ch):.1f}% in {int(fm['mins'])} min",
                          f"Heads up: {sym} just {'jumped' if ch > 0 else 'dropped'} in the last {int(fm['mins'])} minutes ({ch:+.1f}%), now {last:g} ({s_name}).", tags='zap')
        # yesterday's high / low
        if cfg['yday']['on'] and s_name != 'closed':
            hi, lo, d = _yday_range(hist, now_ms)
            prev = _last.get(sym)
            if hi and prev is not None:
                if prev <= hi < last and f'yhi {sym} {d}' not in sent:
                    sent[f'yhi {sym} {d}'] = now_ms
                    _send('yesterday high', sym, f"{sym} broke yesterday's high", f"{sym} just moved above yesterday's high of {hi:g}, now {last:g} ({s_name}).", tags='arrow_up')
                if prev >= lo > last and f'ylo {sym} {d}' not in sent:
                    sent[f'ylo {sym} {d}'] = now_ms
                    _send('yesterday low', sym, f"{sym} broke yesterday's low", f"{sym} just slipped below yesterday's low of {lo:g}, now {last:g} ({s_name}).", tags='arrow_down')
        _last[sym] = last
        # volume vs normal for this hour
        if vol:
            v, normal = _volume_ratio(vol, sym, now_ms)
            if v and normal and v >= cfg['volume']['x'] * normal and v >= 2000:
                k = f'vol {sym} {now_ms // 3600000}'
                if k not in sent:
                    sent[k] = now_ms
                    _send('volume', sym, f'Busy trading in {sym}', f"Busy trading in {sym} ({vs:+.1f}% since the close): {v / normal:.1f}x its normal volume for this hour, {int(v):,} shares vs about {int(normal):,}." if vs is not None
                          else f"Busy trading in {sym}: {v / normal:.1f}x its normal volume for this hour.", tags='bar_chart')
    # 4 am gap list
    d = watch.et(now_ms)
    if cfg['gap4']['on'] and trading_day(d.date()) and d.hour == 4 and d.minute < 30 and f'gap4 {d.date()}' not in sent:
        sent[f'gap4 {d.date()}'] = now_ms
        rows = []
        for sym in syms:
            st = watch.stats(sym, now_ms)
            if st and st.get('close') is not None and abs(st['close']) >= cfg['gap4']['pct']:
                rows.append((st['close'], sym))
        rows.sort(key=lambda r: -abs(r[0]))
        if rows:
            _send('4 am gaps', '', '4 am gaps', 'Gapping vs the 4 pm close at 4 am: ' + ', '.join(f'{s} {v:+.1f}%' for v, s in rows[:12]) + '.', tags='sunrise')
    for k in [k for k, t in sent.items() if now_ms - t > 4 * 86400000]:
        del sent[k]


# ------------------------------------------------------------------ overnight summary (7:30 am)
def summary_time():
    v = os.environ.get('JB_SUMMARY_TIME', '7:30').strip().lower()
    m = re.match(r'^(\d{1,2})[:.](\d{2})$', v)
    return (int(m.group(1)), int(m.group(2))) if m and int(m.group(1)) < 24 and int(m.group(2)) < 60 else (7, 30)


def summary_text(now_ms):
    rows = []
    for sym in _symbols():
        st = watch.stats(sym, now_ms)
        if st and st.get('on') is not None:
            rows.append((st['on'], sym))
    rows.sort()
    up = [f'{s} {v:+.1f}%' for v, s in reversed(rows) if v > 0][:4]
    dn = [f'{s} {v:+.1f}%' for v, s in rows if v < 0][:4]
    parts = ['Overnight (8 pm-4 am): ' + ('up ' + ', '.join(up) if up else 'nothing up') + ('; down ' + ', '.join(dn) if dn else '') + '.']
    try:
        snap = json.loads(world.snapshot())
        fut = {r['sym']: r for r in snap['rows']}
        f = [f"{n} {fut[s]['since_close']:+.2f}%" for s, n in (('ES=F', 'S&P'), ('NQ=F', 'Nasdaq'), ('YM=F', 'Dow'))
             if fut.get(s, {}).get('since_close') is not None]
        if f:
            parts.append('Futures vs 4 pm: ' + ', '.join(f) + '.')
        if snap.get('mood'):
            parts.append(f"Mood: {snap['mood']['label']} {snap['mood']['score']:+d}.")
        d = watch.et(now_ms).date()
        ev = [e for e in snap.get('calendar') or [] if watch.et(e['t']).date() == d and e['impact'] in ('High', 'Medium')]
        if ev:
            parts.append('Today: ' + ', '.join(f"{e['title']} {watch.et(e['t']).strftime('%-I:%M %p')}" for e in ev[:5]) + '.')
    except Exception:
        pass
    er = [s for s, e in _earnings_today(now_ms).items()]
    if er:
        parts.append('Earnings: ' + ', '.join(er[:8]) + '.')
    return ' '.join(parts)


def check_summary(cfg, state, now_ms):
    if not cfg['summary']['on']:
        return
    d = watch.et(now_ms)
    hh, mm = summary_time()
    t = watch.at_et(d.date(), hh, mm)
    k = f'summary {d.date()}'
    if trading_day(d.date()) and t <= now_ms < t + 45 * 60000 and k not in state['sent']:
        state['sent'][k] = now_ms
        _send('summary', '', 'Overnight summary', summary_text(now_ms), tags='crescent_moon')


# ------------------------------------------------------------------ SEC filings (EDGAR)
def _sec_headers():
    who = os.environ.get('JB_SEC_EMAIL', '').strip()
    return {'User-Agent': f'JosephsBoard {who}' if who else 'JosephsBoard personal-dashboard',
            'Accept-Encoding': 'identity'}


def _sec_get(url):
    try:
        return _http(url, _sec_headers())
    except Exception as e:
        if '403' in str(e) and not os.environ.get('JB_SEC_EMAIL'):
            _st['sec_note'] = 'The SEC wants a contact email: add JB_SEC_EMAIL=you@example.com to settings.env, then restart the gateway.'
        raise


def _ciks(state):
    c = state.get('ciks') or {}
    if c and time.time() - state.get('ciks_at', 0) < 7 * 86400:
        return c
    body = json.loads(_sec_get('https://www.sec.gov/files/company_tickers.json'))
    c = {str(v['ticker']).upper(): int(v['cik_str']) for v in body.values()}
    state['ciks'], state['ciks_at'] = c, time.time()
    return c


def _form4_buy(cik, acc, doc):
    """'Name (title) bought N shares at $P' if this Form 4 has an open-market purchase, else ''."""
    doc = doc.split('/')[-1]
    url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace("-", "")}/{doc}'
    root = ET.fromstring(_sec_get(url))
    name = (root.findtext('.//reportingOwner/reportingOwnerId/rptOwnerName') or '').strip()
    title = (root.findtext('.//reportingOwner/reportingOwnerRelationship/officerTitle') or '').strip()
    if not title and (root.findtext('.//reportingOwner/reportingOwnerRelationship/isDirector') or '').strip() in ('1', 'true'):
        title = 'director'
    sh = val = 0.0
    for t in root.findall('.//nonDerivativeTable/nonDerivativeTransaction'):
        if (t.findtext('transactionCoding/transactionCode') or '').strip() != 'P':
            continue
        try:
            n = float(t.findtext('transactionAmounts/transactionShares/value') or 0)
            p = float(t.findtext('transactionAmounts/transactionPricePerShare/value') or 0)
        except ValueError:
            continue
        sh += n
        val += n * p
    if sh <= 0:
        return ''
    return f"{name.title()}{' (' + title + ')' if title else ''} bought {int(sh):,} shares" + (f' at about ${val / sh:,.2f} (${val:,.0f})' if val else '')


def check_sec(cfg, state):
    syms = [s for s, k in _symbols().items() if k == 'stock']
    if not syms:
        return
    ciks = _ciks(state)
    seen = state.setdefault('sec_seen', {})
    feed = state.setdefault('filings', [])
    first_run = not state.get('sec_ready')
    for sym in syms:
        cik = ciks.get(sym) or ciks.get(sym.replace('.', '-'))
        if not cik:
            continue
        try:
            body = json.loads(_sec_get(f'https://data.sec.gov/submissions/CIK{cik:010d}.json'))
        except Exception as e:
            _st['errors'][f'SEC {sym}'] = str(e)[:80]
            time.sleep(1)
            continue
        _st['errors'].pop(f'SEC {sym}', None)
        r = ((body.get('filings') or {}).get('recent')) or {}
        forms, accs = r.get('form') or [], r.get('accessionNumber') or []
        for i in range(min(len(forms), 40)):
            form, acc = forms[i], accs[i]
            if form not in ('8-K', '4', 'SC 13D', 'SC 13D/A', 'SCHEDULE 13D', 'SCHEDULE 13D/A'):
                continue
            if acc in seen.setdefault(sym, []):
                continue
            seen[sym].append(acc)
            doc = (r.get('primaryDocument') or [''] * 40)[i]
            when = (r.get('acceptanceDateTime') or [''] * 40)[i] or (r.get('filingDate') or [''] * 40)[i]
            link = f'https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace("-", "")}/{doc}'
            if first_run:
                continue                    # don't push what was filed before you installed this
            text = ''
            if form == '8-K':
                items = [x.strip() for x in str((r.get('items') or [''] * 40)[i]).split(',') if x.strip()]
                what = [ITEMS_8K[x] for x in items if x in ITEMS_8K] or ['company news']
                text = f'{sym} filed an 8-K (big news): ' + ', '.join(what) + '.'
            elif form == '4':
                try:
                    buy = _form4_buy(cik, acc, doc)
                except Exception:
                    buy = ''
                time.sleep(0.5)
                if not buy:
                    continue                # sales, option exercises, grants: not pushed
                text = f'Insider BUY at {sym}: {buy}.'
            else:
                text = f'{sym}: an investor filed a 13D (owns 5%+ and may push for changes).'
            feed.insert(0, {'sym': sym, 'form': form, 'text': text, 'at': _now(), 'when': when, 'link': link})
            if cfg['sec']['on']:
                _send('filing', sym, f'{sym} SEC filing', text, link, tags='page_facing_up')
        seen[sym] = seen[sym][-80:]
        time.sleep(1)
    del feed[60:]
    state['sec_ready'] = True


# ------------------------------------------------------------------ social buzz (StockTwits)
def _buzz_one(sym):
    body = json.loads(_http(f'https://api.stocktwits.com/api/2/streams/symbol/{urllib.parse.quote(sym)}.json', browser=True))
    msgs = body.get('messages') or []
    now = time.time()
    ts, bull, bear = [], 0, 0
    for m in msgs:
        try:
            t = (datetime.strptime(m['created_at'], '%Y-%m-%dT%H:%M:%SZ') - datetime(1970, 1, 1)).total_seconds()
        except Exception:
            continue
        ts.append(t)
        s = (((m.get('entities') or {}).get('sentiment') or {}) or {}).get('basic')
        bull += s == 'Bullish'
        bear += s == 'Bearish'
    if not ts:
        return {'rate': 0.0, 'bull': 0, 'bear': 0, 'at': now}
    last_hour = sum(1 for t in ts if now - t <= 3600)
    if len(ts) >= 30 and last_hour >= len(ts):                     # all 30 inside the hour: estimate
        span = max(60.0, now - min(ts))
        rate = len(ts) * 3600 / span
    else:
        rate = float(last_hour)
    return {'rate': round(rate, 1), 'bull': bull, 'bear': bear, 'at': now}


def check_buzz(cfg, state, syms):
    hist = state.setdefault('buzz', {})
    for sym in syms:
        try:
            b = _buzz_one(sym)
            _st['errors'].pop(f'buzz {sym}', None)
        except Exception as e:
            _st['errors'][f'buzz {sym}'] = str(e)[:80]
            time.sleep(4)
            continue
        h = hist.setdefault(sym, [])
        past = sorted(x[1] for x in h)
        normal = past[len(past) // 2] if len(past) >= 12 else None          # median of the last week
        b['normal'] = normal
        b['x'] = round(b['rate'] / normal, 1) if normal else None
        with _lock:
            _st['buzz'][sym] = b
        h.append([int(b['at']), b['rate']])
        del h[:-7 * 24 * 2]
        if cfg['buzz']['on'] and normal and b['rate'] >= 10 and b['rate'] >= cfg['buzz']['x'] * max(normal, 1):
            k = f'buzz {sym} {int(b["at"]) // (6 * 3600)}'
            if k not in state['sent']:
                state['sent'][k] = _now()
                mood = f" ({b['bull']} bullish / {b['bear']} bearish of the last 30)" if b['bull'] or b['bear'] else ''
                _send('buzz', sym, f'{sym} buzz spike', f"People are posting about {sym} {b['x']:.0f}x faster than normal on StockTwits: about {b['rate']:.0f} posts/hour{mood}.", tags='speech_balloon')
        time.sleep(4)


# ------------------------------------------------------------------ earnings night
def _earnings_today(now_ms):
    """{sym: {'date', 'when'}} reporting today, or last evening (reaction still going)."""
    d = watch.et(now_ms).date()
    prev = d - timedelta(days=1)
    while not trading_day(prev):
        prev -= timedelta(days=1)
    syms = _symbols()
    out = {}
    for sym, e in (_read(EARN, {}).get('dates') or {}).items():
        if sym not in syms:
            continue
        if e.get('date') == d.isoformat() or (e.get('date') == prev.isoformat() and e.get('when') != 'before the open'):
            out[sym] = e
    return out


def typical_move(sym, state):
    """Average size of the stock's move around its last 4 earnings reports (close the day before
    to close the day after), cached for a week."""
    c = state.setdefault('typical', {}).get(sym)
    if c and time.time() - c['at'] < 7 * 86400:
        return c
    body = json.loads(_http(f'https://api.nasdaq.com/api/company/{urllib.parse.quote(sym)}/earnings-surprise',
                            {'Accept': 'application/json', 'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/'}, browser=True))
    rows = ((((body or {}).get('data') or {}).get('earningsSurpriseTable') or {}).get('rows')) or []
    dates = []
    for r in rows:
        try:
            dates.append(datetime.strptime(str(r.get('dateReported')), '%m/%d/%Y').date())
        except ValueError:
            pass
    chart = world._get('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(sym.replace('.', '-')) +
                       '?range=2y&interval=1d&includePrePost=false')
    res = ((chart.get('chart') or {}).get('result') or [None])[0] or {}
    off = int((res.get('meta') or {}).get('gmtoffset') or -14400)
    bars = [(datetime.utcfromtimestamp(t + off).date(), c) for t, c in
            zip(res.get('timestamp') or [], ((res.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []) if c]
    moves = []
    for d in dates:
        before = [c for bd, c in bars if bd < d]
        after = [c for bd, c in bars if bd > d]
        if before and after:
            moves.append(abs(after[0] / before[-1] - 1) * 100)
    avg = sum(moves) / len(moves) if moves else 0
    c = {'at': time.time(), 'avg': round(avg, 1) if avg >= 0.1 else None, 'n': len(moves),
         'moves': [round(m, 1) for m in moves]}
    state['typical'][sym] = c
    return c


def check_earnings(cfg, state, now_ms):
    today = _earnings_today(now_ms)
    out = {}
    for sym, e in today.items():
        try:
            t = typical_move(sym, state)
        except Exception as ex:
            _st['errors'][f'earnings {sym}'] = str(ex)[:80]
            t = {'avg': None, 'n': 0}
        st = watch.stats(sym, now_ms) or {}
        out[sym] = {'date': e.get('date'), 'when': e.get('when') or '', 'typical': t.get('avg'), 'n': t.get('n', 0),
                    'now': st.get('close')}
        v, typ = st.get('close'), t.get('avg')
        if cfg['earnings']['on'] and v is not None and typ and abs(v) >= typ and session_at(now_ms) != 'closed':
            k = f'earn {sym} {e.get("date")}'
            if k not in state['sent']:
                state['sent'][k] = now_ms
                _send('earnings', sym, f'{sym} earnings move', f'{sym} is {v:+.1f}% vs the 4 pm close after earnings, more than its typical {typ:.1f}% move (last {t.get("n")} reports).', tags='moneybag')
    with _lock:
        _st['earnings'] = out


# ------------------------------------------------------------------ why is it moving
def _sector(sym, state):
    c = state.setdefault('sectors', {}).get(sym)
    if c and time.time() - c['at'] < 30 * 86400:
        return c['sector']
    body = world._get('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + urllib.parse.quote(sym.replace('.', '-')) + '?modules=assetProfile')
    prof = ((((body.get('quoteSummary') or {}).get('result') or [{}])[0]) or {}).get('assetProfile') or {}
    sec = prof.get('sector') or ''
    state['sectors'][sym] = {'sector': sec, 'at': time.time()}
    return sec


def _move_since_close(sym, now_ms):
    st = watch.stats(sym, now_ms)
    if st and st.get('close') is not None:
        return st['close']
    try:
        return world._quote(sym, now_ms).get('since_close')
    except Exception:
        return None


def why(query):
    q = query if isinstance(query, dict) else urllib.parse.parse_qs(query or '')
    sym = (q.get('sym') or [''])[0].upper().strip()[:12]
    if not sym:
        return json.dumps({'error': 'no symbol'})
    now = _now()
    state = _read(STATE, {})
    st = watch.stats(sym, now) or {}
    out = {'sym': sym, 'session': session_at(now), 'move': st.get('close'), 'overnight': st.get('on'), 'reasons': []}
    # market
    mk = {}
    for s, name in (('SPY', 'S&P 500 (SPY)'), ('QQQ', 'Nasdaq 100 (QQQ)')):
        v = (watch.stats(s, now) or {}).get('close')
        if v is not None:
            mk[name] = v
    if not mk:
        try:
            snap = json.loads(world.snapshot())
            for r in snap['rows']:
                if r['sym'] in ('ES=F', 'NQ=F') and r.get('since_close') is not None:
                    mk[r['name']] = r['since_close']
        except Exception:
            pass
    out['market'] = mk
    # sector
    try:
        sec = _sector(sym, state)
        _write(STATE, dict(_read(STATE, {}), sectors=state.get('sectors', {})))
    except Exception:
        sec = ''
    etf = SECTOR_ETF.get(sec)
    out['sector'] = {'name': sec, 'etf': etf, 'move': _move_since_close(etf, now) if etf and etf != sym else None}
    # news
    try:
        import news
        with news._lock:
            items = list(news._state['items'].get(sym) or [])[:6]
    except Exception:
        items = []
    out['news'] = [{'title': i['title'], 'link': i.get('link', ''), 'source': i.get('source', ''), 'ts': i['ts']} for i in items]
    out['filings'] = [f for f in state.get('filings', []) if f['sym'] == sym][:4]
    with _lock:
        out['buzz'] = _st['buzz'].get(sym)
        out['earnings'] = (_st.get('earnings') or {}).get(sym)
    # the short answer
    mv, sm = out['move'], out['sector']['move']
    mkt = next(iter(mk.values()), None)
    r = out['reasons']
    if out['earnings']:
        r.append(f"Earnings ({out['earnings']['when'] or out['earnings']['date']}); typical move about {out['earnings']['typical']}%." if out['earnings'].get('typical') else 'Earnings report.')
    if out['filings'] and now - out['filings'][0]['at'] < 2 * 86400000:
        r.append('New SEC filing: ' + out['filings'][0]['text'])
    if out['news'] and now / 1000 - out['news'][0]['ts'] < 12 * 3600:
        r.append('Fresh headline: ' + out['news'][0]['title'])
    if mv is not None and sm is not None and abs(sm) >= 0.4 and abs(mv - sm) < max(0.5, abs(sm) * 0.5):
        r.append(f"Moving with its sector ({sec}: {etf} {sm:+.2f}%).")
    elif mv is not None and mkt is not None and abs(mkt) >= 0.4 and abs(mv - mkt) < max(0.5, abs(mkt) * 0.5):
        r.append(f'Moving with the whole market ({mkt:+.2f}%).')
    elif mv is not None and abs(mv) >= 1 and (sm is None or abs(mv - sm) >= 1):
        r.append('Moving on its own, not with its sector or the market.')
    b = out['buzz']
    if b and b.get('x') and b['x'] >= 2:
        r.append(f"Social chatter is {b['x']:.0f}x normal on StockTwits.")
    if not r:
        r.append('No clear reason found in the free sources. Check the News tab.')
    return json.dumps(out)


# ------------------------------------------------------------------ thread + API
def _loop():
    state = _read(STATE, {})
    state.setdefault('sent', {})
    last_sec = last_buzz = last_earn = 0
    buzz_i = 0
    while True:
        try:
            cfg = settings()
            now = _now()
            check_prices(cfg, state, now)
            check_summary(cfg, state, now)
            if time.time() - last_earn > 600:
                last_earn = time.time()
                check_earnings(cfg, state, now)
            if time.time() - last_sec > 15 * 60:
                last_sec = time.time()
                try:
                    check_sec(cfg, state)
                except Exception as e:
                    _st['errors']['SEC'] = str(e)[:120]
            if time.time() - last_buzz > 120:      # a few symbols every 2 minutes: each about every 20-30 min
                last_buzz = time.time()
                syms = sorted(_symbols())
                if syms:
                    part = [syms[(buzz_i + k) % len(syms)] for k in range(min(5, len(syms)))]
                    buzz_i += len(part)
                    check_buzz(cfg, state, part)
            st = _read(STATE, {})
            state['sectors'] = dict(st.get('sectors') or {}, **state.get('sectors', {}))
            _write(STATE, state)
        except Exception as e:                 # never let the thread die
            print(f'signals error: {e!r}', flush=True)
        time.sleep(30)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_loop, name='signals', daemon=True).start()


def snapshot():
    state = _read(STATE, {})
    with _lock:
        buzz = dict(_st['buzz'])
        earn = dict(_st.get('earnings') or {})
        errors = dict(_st['errors'])
    return json.dumps({'server_ms': _now(), 'settings': settings(), 'log': _read(LOG, [])[:60],
                       'filings': state.get('filings', [])[:40], 'buzz': buzz, 'earnings': earn,
                       'sec_note': _st['sec_note'], 'errors': errors, 'summary_time': '%d:%02d' % summary_time(),
                       'topic': bool(watch.topic())})


def post(data):
    if isinstance(data.get('settings'), dict):
        cur = settings()
        for k, v in data['settings'].items():
            if k in cur and isinstance(v, dict):
                for kk, vv in v.items():
                    if kk in cur[k]:
                        if isinstance(cur[k][kk], bool):
                            cur[k][kk] = bool(vv)
                        elif isinstance(vv, (int, float)) and 0 < float(vv) < 1000:
                            cur[k][kk] = type(cur[k][kk])(vv)
        _write(SETTINGS, cur)
        return 200, json.dumps({'ok': True, 'settings': cur})
    if data.get('action') == 'test-summary':
        text = summary_text(_now())
        watch.push_later('Overnight summary (test)', text, 'crescent_moon')
        return 200, json.dumps({'ok': True, 'text': text})
    return 400, json.dumps({'ok': False, 'error': 'nothing to do'})
