"""Night desk market data (jb-mdata), running inside the gateway. All free.

  * Leveraged ETF twins: each 3x / 2x bull fund next to its inverse twin and the index or stock
    behind it, with the move since the 4 pm close and whether it is tracking (3 x the index).
    The list is yours to edit in data/etf_pairs.json. Board prices are live (overnight too on the
    IBKR board); the rest come from Yahoo every 2 minutes (4 am - 8 pm only).
  * Short interest and float: Yahoo's numbers for each stock, checked every 3 hours. Short interest
    itself is only published twice a month (by FINRA); a push tells you when a new number lands.
  * Daily short volume: FINRA's free daily file (every trading day, about 6 pm), the share of
    each day's trades that were short sales, vs its own 10-day average.
  * Unusual options: contracts trading far more than their open interest (new bets), checked
    every 30 minutes in market hours, with a push once per stock per day.
  * Analyst rating changes: upgrades, downgrades and new coverage, with a push.
  * Relative volume: volume so far this session vs the same time on the last ~9 days (Yahoo,
    5-minute bars, pre-market and after-hours included).
  * Catalyst tags: why something is moving (EARNINGS, ANALYST, 8-K, FDA, GUIDANCE, DEAL,
    OFFERING, SECTOR, NEWS or NO NEWS), from data the board already has.
  * Pre-market gapper checklist: five checks for each stock gapping 2%+ before the open.
Nothing here places trades. Files: data/mdata_settings.json, mdata_cache.json, mdata_log.json,
etf_pairs.json.
"""
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
SETTINGS = os.path.join(DATA, 'mdata_settings.json')
CACHE = os.path.join(DATA, 'mdata_cache.json')
LOG = os.path.join(DATA, 'mdata_log.json')
PAIRS = os.path.join(DATA, 'etf_pairs.json')
sys.path.insert(0, os.path.join(ROOT, 'lib'))
try:
    from nyse_hours import trading_day, nyse_early_closes
except Exception:
    def trading_day(d):
        return d.weekday() < 5

    def nyse_early_closes(y):
        return frozenset()

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
FINRA = 'https://cdn.finra.org/equity/regsho/daily/CNMSshvol{}.txt'
DEFAULTS = {'ratings': True, 'options': True, 'short': True}
SECTOR_ETFS = ('XLK', 'XLC', 'XLY', 'XLP', 'XLF', 'XLV', 'XLE', 'XLI', 'XLB', 'XLU', 'XLRE', 'SOXX', 'XBI')
# group, the fund it follows, leverage, bull fund, bear (inverse) fund. Blank = no such fund.
DEFAULT_PAIRS = [
    ['Nasdaq-100', 'QQQ', 3, 'TQQQ', 'SQQQ'], ['S&P 500', 'SPY', 3, 'SPXL', 'SPXS'], ['S&P 500', 'SPY', 3, 'UPRO', 'SPXU'],
    ['Dow', 'DIA', 3, 'UDOW', 'SDOW'], ['Russell 2000', 'IWM', 3, 'TNA', 'TZA'], ['Semiconductors', 'SOXX', 3, 'SOXL', 'SOXS'],
    ['Technology', 'XLK', 3, 'TECL', 'TECS'], ['Financials', 'XLF', 3, 'FAS', 'FAZ'], ['Biotech', 'XBI', 3, 'LABU', 'LABD'],
    ['20+ yr Treasuries', 'TLT', 3, 'TMF', 'TMV'],
    ['Nasdaq-100', 'QQQ', 2, 'QLD', 'QID'], ['S&P 500', 'SPY', 2, 'SSO', 'SDS'], ['Dow', 'DIA', 2, 'DDM', 'DXD'],
    ['Russell 2000', 'IWM', 2, 'UWM', 'TWM'], ['Semiconductors', 'SOXX', 2, 'USD', 'SSG'], ['Technology', 'XLK', 2, 'ROM', 'REW'],
    ['Financials', 'XLF', 2, 'UYG', 'SKF'], ['Energy', 'XLE', 2, 'ERX', 'ERY'], ['Gold miners', 'GDX', 2, 'NUGT', 'DUST'],
    ['NVIDIA', 'NVDA', 2, 'NVDL', 'NVDQ'], ['Tesla', 'TSLA', 2, 'TSLL', 'TSLZ'], ['MicroStrategy', 'MSTR', 2, 'MSTU', 'MSTZ'],
    ['AMD', 'AMD', 2, 'AMDL', ''], ['Apple', 'AAPL', 2, 'AAPU', ''], ['Palantir', 'PLTR', 2, 'PLTU', ''],
    ['Coinbase', 'COIN', 2, 'CONL', ''], ['Amazon', 'AMZN', 2, 'AMZU', ''], ['Meta', 'META', 2, 'METU', ''],
    ['Alphabet', 'GOOGL', 2, 'GGLL', ''], ['Bitcoin', 'IBIT', 2, 'BITX', 'SBIT'],
]
# headline words -> catalyst tag (first match wins, so the more specific ones come first)
NEWS_TAGS = [
    ('FDA', ('fda', 'phase 3', 'phase iii', 'phase 2', 'clinical trial', 'approval for', 'approves', 'pdufa')),
    ('GUIDANCE', ('guidance', 'outlook', 'forecast', 'raises full-year', 'cuts full-year', 'preannounce')),
    ('DEAL', ('to acquire', 'acquisition', 'merger', 'buyout', 'takeover', 'to buy ', 'deal to')),
    ('OFFERING', ('offering', 'private placement', 'share sale', 'dilut')),
    ('EARNINGS', ('earnings', 'quarterly results', 'q1 ', 'q2 ', 'q3 ', 'q4 ', 'revenue beat', 'eps')),
    ('ANALYST', ('upgrade', 'downgrade', 'price target', 'initiates coverage', 'initiated at')),
]
ACTIONS = {'up': 'upgrade', 'down': 'downgrade', 'init': 'new coverage', 'main': 'kept rating', 'reit': 'kept rating'}

_lock = threading.Lock()
_st = {'started': False, 'stats': {}, 'sv': {}, 'rv': {}, 'ratings': {}, 'opts': {}, 'opt_hist': {}, 'px': {},
       'errors': {}, 'wait_until': 0, 'sv_days': []}


# ------------------------------------------------------------------ small helpers
def _now():
    return int(time.time() * 1000)


def _ctx():
    try:
        return watch._ctx()
    except Exception:
        return ssl.create_default_context()


def settings():
    s = dict(DEFAULTS)
    s.update({k: v for k, v in (watch._read(SETTINGS, {}) or {}).items() if k in DEFAULTS})
    return s


def _log(kind, sym, text):
    log = watch._read(LOG, [])
    log.insert(0, {'at': _now(), 'kind': kind, 'sym': sym, 'text': text})
    watch._write(LOG, log[:200])


def _send(kind, sym, title, text, tags='bell'):
    _log(kind, sym, text)
    print(f'mdata: {kind} {sym}: {text}', flush=True)
    watch.push_later(title, text, tags)


def _err(key, e):
    with _lock:
        _st['errors'][key] = str(e)[:160]
    if '429' in str(e) or 'Too Many' in str(e):
        _st['wait_until'] = time.time() + 600          # Yahoo asked us to slow down: rest 10 minutes


def _yahoo(url):
    import world
    return world._get(url)


def tracked():
    """{sym: {'kind', 'where'}} for stocks and ETFs on the boards, scanner, algos, plus holdings."""
    out = {}
    try:
        import news
        out = {s: dict(e) for s, e in news.tracked().items() if e['kind'] in ('stock', 'etf')}
    except Exception:
        pass
    for s in held():
        out.setdefault(s, {'kind': 'stock', 'where': []})['where'] = out.get(s, {}).get('where', []) + ['Held']
    return out


def held():
    try:
        import alerts2
        return alerts2.held()
    except Exception:
        pass
    try:
        import portfolio
        return {p['sym'] for p in portfolio.positions() if (p.get('shares') or 0) > 0}
    except Exception:
        return set()


def mine(info):
    """On a board, an algo or held (not scanner-only)."""
    return any(not w.startswith('Scanner') for w in info.get('where') or [])


def pairs():
    p = watch._read(PAIRS, None)
    if not isinstance(p, list) or not p:
        p = [list(x) for x in DEFAULT_PAIRS]
        try:
            watch._write(PAIRS, p)
        except Exception:
            pass
    out = []
    for r in p:
        try:
            out.append([str(r[0]), str(r[1]).upper(), int(r[2]), str(r[3] or '').upper(), str(r[4] or '').upper()])
        except Exception:
            pass
    return out


def session_at(ms):
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    if not trading_day(d.date()):
        return 'closed' if not (m >= 20 * 60 and trading_day((d + timedelta(days=1)).date())) else 'overnight'
    if 240 <= m < 570:
        return 'pre'
    close = 780 if d.date() in nyse_early_closes(d.year) else 960      # 1 pm on early-close days
    if 570 <= m < close:
        return 'regular'
    if close <= m < close + 240:
        return 'after'
    if close + 240 <= m < 1200:
        return 'closed'
    if m >= 1200 and not trading_day((d + timedelta(days=1)).date()):
        return 'closed'                              # Friday / day before a holiday: no overnight
    return 'overnight'


# ------------------------------------------------------------------ live prices (board first, then Yahoo)
def price(sym, now_ms):
    """{'last', 'since_close', 'src', 'at'} from the board's own record if fresh, else Yahoo."""
    with watch._lock:
        hist = list(watch._px.get(sym) or [])
    if hist and now_ms - hist[-1][0] < 10 * 60000:
        ref = watch.price_at(hist, watch.last_close_ms(now_ms))
        if ref:
            return {'last': hist[-1][1], 'since_close': watch.pct(hist[-1][1], ref), 'src': 'board', 'at': hist[-1][0]}
    with _lock:
        y = _st['px'].get(sym)
    if y:
        return {'last': y['last'], 'since_close': y.get('since_close'), 'src': 'yahoo', 'at': y.get('last_ms') or 0}
    return None


def refresh_prices(now_ms):
    import world
    want = set()
    for r in pairs():
        want |= {r[1], r[3], r[4]}
    want |= set(SECTOR_ETFS)
    want.discard('')
    syms = sorted(want)
    one_by_one = time.time() < _st.get('spark_off_until', 0)
    for i in range(0, len(syms), 10):
        if time.time() < _st['wait_until']:
            return
        batch, got = syms[i:i + 10], {}
        if not one_by_one:
            try:
                got = world._spark(batch, now_ms, world.HOSTS[(i // 10) % 2])
            except Exception as e:
                if '429' in str(e):
                    _err('prices', e)
                    return
                one_by_one = True              # this round: one by one, like the World tab
                if 'HTTP Error 400' in str(e) or 'HTTP 400' in str(e):
                    _st['spark_off_until'] = time.time() + 6 * 3600   # Yahoo refuses its batch address: skip it for 6 hours
        if one_by_one:
            for n, s in enumerate(batch):
                try:
                    got[s] = world._quote(s, now_ms, world.HOSTS[n % 2])
                except Exception as e:
                    if '429' in str(e):
                        _err('prices', e)
                        return
                time.sleep(1.2)
        with _lock:
            for s, v in got.items():
                _st['px'][s] = {k: v.get(k) for k in ('last', 'since_close', 'last_ms', 'close_ref')}
            for s in batch:
                if s not in got:
                    _st['px'].setdefault(s, None)
            if got:
                _st['errors'].pop('prices', None)
        time.sleep(1.5)


def twins(now_ms):
    rows = []
    for g, base, lev, bull, bear in pairs():
        b = price(base, now_ms)
        row = {'group': g, 'lev': lev, 'base': {'sym': base, 'p': b}}
        for k, s in (('bull', bull), ('bear', bear)):
            if not s:
                row[k] = None
                continue
            p = price(s, now_ms)
            with _lock:
                missing = s in _st['px'] and _st['px'][s] is None
            exp = round(b['since_close'] * lev * (1 if k == 'bull' else -1), 2) if b and b.get('since_close') is not None else None
            row[k] = {'sym': s, 'p': p, 'expected': exp, 'missing': bool(missing and not p)}
        rows.append(row)
    return rows


# ------------------------------------------------------------------ Yahoo stats: float, short interest, ratings
def _raw(v):
    return v.get('raw') if isinstance(v, dict) else v


def fetch_stats(sym, ratings=True):
    mods = 'defaultKeyStatistics,summaryDetail,price' + (',upgradeDowngradeHistory' if ratings else '')
    body = _yahoo('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + urllib.parse.quote(sym.replace('.', '-')) + '?modules=' + mods)
    res = ((body.get('quoteSummary') or {}).get('result') or [None])[0] or {}
    ks, sd, pr = res.get('defaultKeyStatistics') or {}, res.get('summaryDetail') or {}, res.get('price') or {}
    out = {'at': time.time(), 'float': _raw(ks.get('floatShares')), 'short_pct': _raw(ks.get('shortPercentOfFloat')),
           'short_ratio': _raw(ks.get('shortRatio')), 'shares_short': _raw(ks.get('sharesShort')),
           'short_prev': _raw(ks.get('sharesShortPriorMonth')), 'short_date': _raw(ks.get('dateShortInterest')),
           'avg10': _raw(sd.get('averageVolume10days')) or _raw(sd.get('averageDailyVolume10Day')),
           'name': pr.get('shortName') or ''}
    if out['short_pct'] is not None:
        out['short_pct'] = round(out['short_pct'] * 100, 2)
    hist = []
    for h in ((res.get('upgradeDowngradeHistory') or {}).get('history') or [])[:30]:
        try:
            hist.append({'t': int(h['epochGradeDate']) * 1000, 'firm': h.get('firm') or '', 'action': h.get('action') or '',
                         'to': h.get('toGrade') or '', 'from': h.get('fromGrade') or ''})
        except Exception:
            pass
    return out, hist


def rating_text(sym, r):
    a = ACTIONS.get(r['action'], r['action'])
    if r['action'] in ('up', 'down') and r['from']:
        return f"{sym}: {r['firm']} {a} to {r['to']} (from {r['from']})"
    return f"{sym}: {r['firm']} {a}" + (f" ({r['to']})" if r['to'] else '')


def check_stats(cfg, state, info_all, now_ms):
    """One symbol per call, oldest first: board / held stocks every 3 h, scanner-only every 12 h."""
    due = []
    for s, info in info_all.items():
        with _lock:
            hit = _st['stats'].get(s)
        age = time.time() - (hit or {}).get('at', 0)
        if age > (3 * 3600 if mine(info) else 12 * 3600):
            due.append((age, s))
    if not due:
        return False
    s = max(due)[1]
    info = info_all[s]
    try:
        out, hist = fetch_stats(s, ratings=info['kind'] == 'stock')
    except Exception as e:
        _err('stats ' + s, e)
        with _lock:
            _st['stats'].setdefault(s, {})['at'] = time.time() - 2 * 3600     # try again in about an hour
        return True
    with _lock:
        old = _st['stats'].get(s) or {}
        _st['stats'][s] = out
        _st['errors'].pop('stats ' + s, None)
        if hist:
            _st['ratings'][s] = hist
    first = s not in state['seen_ratings']
    seen = set(state['seen_ratings'].get(s) or [])
    new = [r for r in hist if f"{r['t']}|{r['firm']}" not in seen]
    state['seen_ratings'][s] = [f"{r['t']}|{r['firm']}" for r in hist]
    if not first and cfg['ratings'] and mine(info):
        for r in new:
            if r['action'] in ('up', 'down', 'init') and now_ms - r['t'] < 3 * 86400000:
                _send('rating', s, f"{s} {ACTIONS[r['action']]}", rating_text(s, r) + '.', 'arrow_up' if r['action'] == 'up' else 'arrow_down' if r['action'] == 'down' else 'mag')
    # new short interest number published
    if old.get('short_date') and out.get('short_date') and out['short_date'] != old['short_date'] and cfg['short'] and mine(info):
        ch = watch.pct(out.get('shares_short'), out.get('short_prev'))
        _send('short', s, f'{s} new short interest', f"{s}: {out['short_pct'] or '?'}% of the float is sold short" +
              (f" ({ch:+.0f}% vs the last report)" if ch is not None else '') +
              (f", {out['short_ratio']} days to cover." if out.get('short_ratio') else '.'), 'chart_with_downwards_trend')
    return True


# ------------------------------------------------------------------ FINRA daily short volume
def parse_finra(text, want):
    out = {}
    for line in text.splitlines()[1:]:
        p = line.split('|')
        if len(p) < 5 or p[1] not in want:
            continue
        try:
            short, total = float(p[2]), float(p[4])
        except ValueError:
            continue
        if total > 0:
            out[p[1]] = {'short': int(short), 'total': int(total), 'pct': round(short / total * 100, 1)}
    return out


def check_finra(info_all, now_ms):
    """Fetch any of the last 10 trading days not yet read (today's file appears about 6 pm ET)."""
    d = watch.et(now_ms)
    days, day = [], d.date()
    if d.hour * 60 + d.minute < 18 * 60 + 15:
        day -= timedelta(days=1)
    while len(days) < 10:
        if trading_day(day):
            days.append(day.isoformat())
        day -= timedelta(days=1)
    have = set(_st['sv_days'])
    todo = [x for x in days if x not in have and x not in _st.get('sv_missing', {})]
    if not todo:
        return
    want = set(info_all)
    for iso in sorted(todo)[-3:]:                   # at most 3 files per round
        url = FINRA.format(iso.replace('-', ''))
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
                text = r.read().decode('utf-8', 'replace')
        except Exception as e:
            if '403' in str(e) or '404' in str(e):  # not published (yet): try again in an hour
                _st.setdefault('sv_missing', {})[iso] = time.time()
            else:
                _err('short volume', e)
            continue
        got = parse_finra(text, want)
        with _lock:
            for s, v in got.items():
                lst = [x for x in _st['sv'].get(s, []) if x['date'] != iso] + [dict(v, date=iso)]
                _st['sv'][s] = sorted(lst, key=lambda x: x['date'])[-10:]
            _st['sv_days'] = sorted(set(_st['sv_days']) | {iso})[-12:]
            _st['errors'].pop('short volume', None)
        time.sleep(2)
    _st['sv_missing'] = {k: t for k, t in _st.get('sv_missing', {}).items() if time.time() - t < 3600}


def short_volume(sym):
    with _lock:
        lst = list(_st['sv'].get(sym) or [])
    if not lst:
        return None
    last = lst[-1]
    prev = [x['pct'] for x in lst[:-1]]
    avg = round(sum(prev) / len(prev), 1) if prev else None
    return {'date': last['date'], 'pct': last['pct'], 'avg': avg, 'total': last['total']}


# ------------------------------------------------------------------ relative volume + pre-market range (Yahoo 5-min bars)
def _sess_of(m):
    return 'pre' if 240 <= m < 570 else 'regular' if 570 <= m < 960 else 'after' if 960 <= m < 1200 else ''


def relvol_from_bars(ts, vols, highs, lows, closes, now_ms):
    days = {}
    for t, v, h, lo, c in zip(ts, vols, highs, lows, closes):
        if c is None:
            continue
        d = watch.et(t * 1000)
        m = d.hour * 60 + d.minute
        s = _sess_of(m)
        if not s:
            continue
        days.setdefault(d.date().isoformat(), []).append((m, s, v or 0, h or c, lo or c, c))
    if not days:
        return None
    order = sorted(days)
    now = watch.et(now_ms)
    today = now.date().isoformat()
    m_now = now.hour * 60 + now.minute
    s_now = _sess_of(m_now)
    out = {'at': time.time()}
    # previous regular close
    prev_days = [x for x in order if x < today]
    if prev_days:
        reg = [b for b in days[prev_days[-1]] if b[1] == 'regular']
        if reg:
            out['prev_close'] = reg[-1][5]
    if today in days:
        out['last'] = days[today][-1][5]
        pm = [b for b in days[today] if b[1] == 'pre']
        if pm:
            out['pm_hi'], out['pm_lo'], out['pm_vol'] = max(b[3] for b in pm), min(b[4] for b in pm), sum(b[2] for b in pm)
    if today in days and 240 <= m_now:              # today's pre-market so far vs the same stretch on earlier days
        cut_m = min(m_now, 570)
        pre = lambda x: sum(b[2] for b in days[x] if b[1] == 'pre' and b[0] < cut_m)
        pv = [pre(x) for x in prev_days[-9:]]
        pv = [v for v in pv if v > 0]
        if pv and pre(today):
            out['pm_rv'] = round(pre(today) / (sum(pv) / len(pv)), 1)
    if s_now and today in days:
        cut = lambda bars: sum(b[2] for b in bars if b[1] == s_now and b[0] < m_now)
        mine_v = cut(days[today])
        past = [cut(days[x]) for x in prev_days[-9:]]
        past = [v for v in past if v > 0]
        if past and mine_v:
            avg = sum(past) / len(past)
            out.update(rv=round(mine_v / avg, 1), rv_sess=s_now, vol=int(mine_v))
    else:                                            # overnight / closed: the last full regular day
        last_day = order[-1]
        tot = lambda x: sum(b[2] for b in days[x] if b[1] == 'regular')
        past = [tot(x) for x in order[:-1][-9:] if tot(x) > 0]
        if past and tot(last_day):
            out.update(rv=round(tot(last_day) / (sum(past) / len(past)), 1), rv_sess='day', vol=int(tot(last_day)))
    return out


def fetch_relvol(sym, now_ms):
    body = _yahoo(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym.replace(".", "-"))}?range=10d&interval=5m&includePrePost=true')
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    q = (((res.get('indicators') or {}).get('quote')) or [{}])[0]
    ts = res.get('timestamp') or []
    return relvol_from_bars(ts, q.get('volume') or [], q.get('high') or [], q.get('low') or [], q.get('close') or [], now_ms)


def check_relvol(info_all, now_ms):
    s_now = session_at(now_ms)
    due = []
    for s in info_all:
        with _lock:
            hit = _st['rv'].get(s)
        age = time.time() - (hit or {}).get('at', 0)
        p = price(s, now_ms)
        gapper = p and p.get('since_close') is not None and abs(p['since_close']) >= 2
        limit = 30 * 60 if s_now in ('overnight', 'closed') else (4 * 60 if gapper and s_now == 'pre' else 8 * 60)
        if age > limit:
            due.append((age, s))
    if not due:
        return False
    s = max(due)[1]
    try:
        out = fetch_relvol(s, now_ms)
        with _lock:
            _st['rv'][s] = out or {'at': time.time()}
            _st['errors'].pop('volume ' + s, None)
    except Exception as e:
        _err('volume ' + s, e)
        with _lock:
            _st['rv'].setdefault(s, {})['at'] = time.time()
    return True


# ------------------------------------------------------------------ unusual options
def unusual(calls, puts, min_vol=500):
    out = []
    for kind, lst in (('call', calls), ('put', puts)):
        for o in lst:
            v, oi = o.get('volume') or 0, o.get('openInterest') or 0
            if v >= min_vol and v >= 2 * max(oi, 1):
                out.append({'type': kind, 'strike': o.get('strike'), 'vol': int(v), 'oi': int(oi),
                            'last': o.get('lastPrice'), 'x': round(v / oi, 1) if oi else None})
    out.sort(key=lambda u: -u['vol'])
    return out[:8]


def fetch_options(sym, now_ms):
    base = 'https://query2.finance.yahoo.com/v7/finance/options/' + urllib.parse.quote(sym.replace('.', '-'))
    body = _yahoo(base)
    res = ((body.get('optionChain') or {}).get('result') or [None])[0]
    if not res:
        return {'at': time.time(), 'none': True}
    exps = res.get('expirationDates') or []
    chains = [(res.get('options') or [{}])[0]]
    today = watch.et(now_ms).strftime('%Y-%m-%d')
    if exps and time.strftime('%Y-%m-%d', time.gmtime(exps[0])) <= today and len(exps) > 1:     # also the next expiry after 0-day
        time.sleep(1)
        b2 = _yahoo(base + '?date=' + str(exps[1]))
        r2 = ((b2.get('optionChain') or {}).get('result') or [None])[0] or {}
        chains.append((r2.get('options') or [{}])[0])
    calls = [dict(o, exp=c.get('expirationDate')) for c in chains for o in c.get('calls') or []]
    puts = [dict(o, exp=c.get('expirationDate')) for c in chains for o in c.get('puts') or []]
    u = unusual(calls, puts)
    exp_of = {(('call', o.get('strike'), o.get('volume'))): o.get('exp') for o in calls}
    exp_of.update({(('put', o.get('strike'), o.get('volume'))): o.get('exp') for o in puts})
    for x in u:
        e = exp_of.get((x['type'], x['strike'], x['vol']))
        x['exp'] = time.strftime('%b %d', time.gmtime(e)) if e else ''
    cv, pv = sum(o.get('volume') or 0 for o in calls), sum(o.get('volume') or 0 for o in puts)
    return {'at': time.time(), 'unusual': u, 'calls_vol': int(cv), 'puts_vol': int(pv),
            'pc': round(pv / cv, 2) if cv else None, 'date': today}


def check_options(cfg, state, info_all, now_ms):
    s_now = session_at(now_ms)
    d = watch.et(now_ms)
    m = d.hour * 60 + d.minute
    if s_now != 'regular' and not (s_now == 'after' and m < 990):   # options trade 9:30 - 4:15
        return False
    due = []
    for s, info in info_all.items():
        if not mine(info):
            continue
        with _lock:
            hit = _st['opts'].get(s)
        age = time.time() - (hit or {}).get('at', 0)
        if age > 30 * 60:
            due.append((age, s))
    if not due:
        return False
    s = max(due)[1]
    try:
        out = fetch_options(s, now_ms)
    except Exception as e:
        _err('options ' + s, e)
        with _lock:
            _st['opts'].setdefault(s, {})['at'] = time.time() - 20 * 60
        return True
    with _lock:
        _st['opts'][s] = out
        _st['errors'].pop('options ' + s, None)
        if out.get('date'):
            h = _st['opt_hist'].setdefault(s, {})
            h[out['date']] = out['calls_vol'] + out['puts_vol']
            for k in sorted(h)[:-12]:
                del h[k]
    k = f"opt|{s}|{out.get('date')}"
    if cfg['options'] and out.get('unusual') and k not in state['sent']:
        state['sent'][k] = now_ms
        u = out['unusual'][0]
        _send('options', s, f'{s} unusual options', f"{s}: {u['vol']:,} {u['type']}s at ${u['strike']:g} ({u['exp']}) traded vs {u['oi']:,} open before today: someone is making a new bet." +
              (f" {len(out['unusual']) - 1} more on the Night desk." if len(out['unusual']) > 1 else ''), 'dart')
    return True


def options_avg(sym, date):
    with _lock:
        h = dict(_st['opt_hist'].get(sym) or {})
    past = [v for d, v in h.items() if d != date and v]
    return int(sum(past) / len(past)) if past else None


# ------------------------------------------------------------------ catalyst tags
def tags(sym, now_ms, move=None):
    out = []
    d = watch.et(now_ms).date()
    e = ((watch._read(os.path.join(DATA, 'earnings.json'), {}).get('dates') or {}).get(sym) or {})
    if e.get('date'):
        yday = d - timedelta(days=1)
        while not trading_day(yday):
            yday -= timedelta(days=1)
        if e['date'] == d.isoformat() or (e['date'] == yday.isoformat() and e.get('when') != 'before the open'):
            out.append('EARNINGS')
    with _lock:
        rs = list(_st['ratings'].get(sym) or [])
    if any(r['action'] in ('up', 'down', 'init') and now_ms - r['t'] < 36 * 3600000 for r in rs):
        out.append('ANALYST')
    try:
        import signals
        st = watch._read(signals.STATE, {})
        for f in st.get('filings') or []:
            if f.get('sym') == sym and now_ms - (f.get('at') or 0) < 36 * 3600000:
                t = '8-K' if f.get('form') == '8-K' else 'INSIDER' if f.get('form') == '4' else '13D'
                if t not in out:
                    out.append(t)
    except Exception:
        pass
    heads = []
    try:
        import news
        with news._lock:
            heads = [i for i in (news._state['items'].get(sym) or []) if now_ms / 1000 - (i.get('ts') or 0) < 24 * 3600]
    except Exception:
        pass
    for i in heads:
        t = (i.get('title') or '').lower()
        for tag, words in NEWS_TAGS:
            if any(w in t for w in words) and tag not in out:
                out.append(tag)
                break
    sec = None
    try:
        import analysis
        with analysis._lock:
            name = (analysis._c['sectors'].get(sym) or {}).get('sector')
        sec = {v: k for k, v in analysis.SECTOR_ETF.items()}.get(name)
    except Exception:
        pass
    if sec and move is not None:
        p = price(sec, now_ms)
        if p and p.get('since_close') is not None and abs(p['since_close']) >= 1 and (p['since_close'] > 0) == (move > 0):
            out.append('SECTOR')
    if not out and heads:
        out.append('NEWS')
    if not out and move is not None and abs(move) >= 2:
        out.append('NO NEWS')
    return out


# ------------------------------------------------------------------ pre-market gapper checklist
def gappers(info_all, now_ms):
    rows = []
    for s, info in info_all.items():
        if info['kind'] != 'stock':
            continue
        p = price(s, now_ms)
        with _lock:
            rv = dict(_st['rv'].get(s) or {})
        last = (p or {}).get('last') or rv.get('last')
        gap = (p or {}).get('since_close')
        if gap is None and rv.get('last') and rv.get('prev_close'):
            gap = watch.pct(rv['last'], rv['prev_close'])
        if gap is None or abs(gap) < 2 or not last:
            continue
        tg = tags(s, now_ms, gap)
        hold = None
        if rv.get('pm_hi') and rv.get('pm_lo') and rv['pm_hi'] > rv['pm_lo']:
            pos = (last - rv['pm_lo']) / (rv['pm_hi'] - rv['pm_lo'])
            hold = pos >= 0.5 if gap > 0 else pos <= 0.5
        checks = [
            ['Gap 2-10%', 2 <= abs(gap) <= 10, f'{gap:+.1f}%'],
            ['Has a catalyst', bool(tg) and tg != ['NO NEWS'], ', '.join(tg) or 'none found'],
            ['Pre-market volume 5x+', None if rv.get('pm_rv') is None else rv['pm_rv'] >= 5, (f"{rv['pm_rv']}x normal" if rv.get('pm_rv') is not None else 'not known yet')],
            ['Price $5-100', 5 <= last <= 100, f'${last:.2f}'],
            ['Holding its pre-market range', hold, ('in the ' + ('top' if (last - rv['pm_lo']) / (rv['pm_hi'] - rv['pm_lo']) >= 0.5 else 'bottom') + ' half') if hold is not None else 'not known yet'],
        ]
        rows.append({'sym': s, 'gap': round(gap, 2), 'last': last, 'tags': tg, 'checks': checks,
                     'score': sum(1 for c in checks if c[1]), 'mine': mine(info)})
    rows.sort(key=lambda r: (-r['score'], -abs(r['gap'])))
    return rows[:30]


# ------------------------------------------------------------------ background loop
def _state():
    st = watch._read(os.path.join(DATA, 'mdata_state.json'), {})
    st.setdefault('sent', {})
    st.setdefault('seen_ratings', {})
    return st


def _save():
    with _lock:
        snap = {k: _st[k] for k in ('stats', 'sv', 'rv', 'ratings', 'opts', 'opt_hist', 'px', 'sv_days')}
    try:
        watch._write(CACHE, snap)
    except Exception as e:
        print(f'mdata: could not save: {e!r}', flush=True)


def _loop():
    last = {'px': 0, 'finra': 0, 'save': 0, 'syms': 0}
    info_all = {}
    while True:
        now = _now()
        try:
            if time.time() < _st['wait_until']:
                time.sleep(20)
                continue
            if now - last['syms'] > 10 * 60000 or not info_all:
                last['syms'] = now
                info_all = tracked()
            cfg = settings()
            state = _state()
            if now - last['px'] >= (5 if time.time() < _st.get('spark_off_until', 0) else 2) * 60000:
                last['px'] = now
                refresh_prices(now)
            if now - last['finra'] >= 30 * 60000:
                last['finra'] = now
                check_finra(info_all, now)
            busy = check_stats(cfg, state, info_all, now)
            time.sleep(2)
            busy = check_relvol(info_all, now) or busy
            time.sleep(2)
            busy = check_options(cfg, state, info_all, now) or busy
            for k in [k for k, t in state['sent'].items() if now - t > 4 * 86400000]:
                del state['sent'][k]
            watch._write(os.path.join(DATA, 'mdata_state.json'), state)
            if now - last['save'] >= 10 * 60000:
                last['save'] = now
                _save()
            time.sleep(1 if busy else 20)
        except Exception as e:
            print(f'mdata error: {e!r}', flush=True)
            time.sleep(30)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
        old = watch._read(CACHE, {})
        for k in ('stats', 'sv', 'rv', 'ratings', 'opts', 'opt_hist', 'px'):
            if isinstance(old.get(k), dict):
                _st[k] = old[k]
        if isinstance(old.get('sv_days'), list):
            _st['sv_days'] = old['sv_days']
    pairs()
    threading.Thread(target=_loop, name='mdata', daemon=True).start()


# ------------------------------------------------------------------ what the pages get
def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return v or ''


def row(sym, info, now_ms):
    with _lock:
        st = dict(_st['stats'].get(sym) or {})
        rv = dict(_st['rv'].get(sym) or {})
        op = dict(_st['opts'].get(sym) or {})
        rs = list(_st['ratings'].get(sym) or [])[:3]
    p = price(sym, now_ms)
    move = (p or {}).get('since_close')
    return {'sym': sym, 'kind': info.get('kind'), 'mine': mine(info), 'held': 'Held' in (info.get('where') or []),
            'float': st.get('float'), 'short_pct': st.get('short_pct'), 'short_ratio': st.get('short_ratio'),
            'short_ch': watch.pct(st.get('shares_short'), st.get('short_prev')), 'short_date': st.get('short_date'),
            'sv': short_volume(sym), 'rv': rv.get('rv'), 'rv_sess': rv.get('rv_sess'), 'move': move,
            'tags': tags(sym, now_ms, move), 'ratings': [dict(r, text=rating_text(sym, r)) for r in rs],
            'opts': {'unusual': op.get('unusual') or [], 'pc': op.get('pc'), 'vol': (op.get('calls_vol') or 0) + (op.get('puts_vol') or 0),
                     'avg': options_avg(sym, op.get('date')), 'at': op.get('at')} if op.get('at') and not op.get('none') else None}


def snapshot():
    now = _now()
    info_all = tracked()
    rows = [row(s, i, now) for s, i in sorted(info_all.items())]
    ratings = []
    with _lock:
        for s, lst in _st['ratings'].items():
            if s in info_all:
                ratings += [dict(r, sym=s, text=rating_text(s, r)) for r in lst if now - r['t'] < 14 * 86400000 and r['action'] in ('up', 'down', 'init')]
        errors = dict(_st['errors'])
        n_stats = sum(1 for s in info_all if (_st['stats'].get(s) or {}).get('float') is not None or (_st['stats'].get(s) or {}).get('short_pct') is not None)
        sv_last = max(_st['sv_days']) if _st['sv_days'] else None
    ratings.sort(key=lambda r: -r['t'])
    return {'at': now, 'session': session_at(now), 'settings': settings(), 'rows': rows, 'twins': twins(now),
            'gappers': gappers(info_all, now), 'ratings': ratings[:40], 'log': watch._read(LOG, [])[:40],
            'errors': errors, 'n_stats': n_stats, 'sv_last': sv_last,
            'slow': time.time() < _st['wait_until']}


def scan(query):
    """For the scanner table and the board chips: a few numbers per symbol."""
    now = _now()
    syms = [s.strip().upper() for s in _q(query, 'syms').split(',') if s.strip()][:200]
    info_all = tracked()
    out = {}
    for s in syms:
        with _lock:
            st = dict(_st['stats'].get(s) or {})
            rv = dict(_st['rv'].get(s) or {})
            op = _st['opts'].get(s) or {}
            rs = list(_st['ratings'].get(s) or [])
        p = price(s, now)
        move = (p or {}).get('since_close')
        rec = [r for r in rs if r['action'] in ('up', 'down', 'init') and now - r['t'] < 2 * 86400000]
        g = None
        if s in info_all and info_all[s]['kind'] == 'stock' and session_at(now) in ('pre', 'overnight', 'after') and move is not None and abs(move) >= 2:
            for r in gappers({s: info_all[s]}, now):
                g = r['score']
        out[s] = {'float': st.get('float'), 'short_pct': st.get('short_pct'), 'short_ratio': st.get('short_ratio'),
                  'rv': rv.get('rv'), 'rv_sess': rv.get('rv_sess'), 'tags': tags(s, now, move), 'gap_score': g,
                  'rating': dict(rec[0], text=rating_text(s, rec[0])) if rec else None,
                  'unusual': len(op.get('unusual') or []) if op.get('date') == watch.et(now).strftime('%Y-%m-%d') else 0,
                  'sv': short_volume(s)}
    return {'at': now, 'syms': out}


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    if sub == 'scan':
        return 200, scan(query)
    if sub == 'twins':
        return 200, {'at': _now(), 'twins': twins(_now())}
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'settings':
        s = settings()
        for k in DEFAULTS:
            if k in (data.get('settings') or {}):
                s[k] = bool(data['settings'][k])
        watch._write(SETTINGS, s)
        return 200, snapshot()
    if sub == 'pairs':                       # add or remove a twin row from the Night desk
        p = pairs()
        if data.get('remove') is not None:
            i = int(data['remove'])
            if 0 <= i < len(p):
                p.pop(i)
        elif data.get('add'):
            a = data['add']
            clean = lambda x: ''.join(ch for ch in str(x or '').upper() if ch.isalnum() or ch in '.-')[:10]
            lev = int(a.get('lev') or 3)
            if lev not in (2, 3) or not clean(a.get('base')) or not (clean(a.get('bull')) or clean(a.get('bear'))):
                return 400, {'error': 'Fill in the fund it follows and at least one of bull / bear (leverage 2 or 3).'}
            p.append([str(a.get('group') or clean(a.get('base')))[:30], clean(a.get('base')), lev, clean(a.get('bull')), clean(a.get('bear'))])
        watch._write(PAIRS, p)
        with _lock:
            _st['px'] = {k: v for k, v in _st['px'].items() if v is not None}   # look up new symbols again
        threading.Thread(target=refresh_prices, args=(_now(),), daemon=True).start()
        return 200, {'twins': twins(_now()), 'pairs': p}
    return 404, {'error': 'unknown'}
