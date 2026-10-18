"""Night desk earnings and trading tools (jb-earn), running inside the gateway. All free.

  * Expected move: before each earnings report on your boards, the move the options market is
    pricing in (the at-the-money straddle on the first expiry after the report), next to how much
    the stock really moved on its last reports.
  * Earnings call clock: when the results and the conference call come out (Yahoo's calendar),
    with a countdown, and a push 10 minutes before the call for stocks you hold or watch.
  * Night vs day: for any symbol, a year of "hold only overnight" (4 pm close -> 9:30 open) vs
    "hold only in the day" (open -> close), as a line chart (Yahoo daily bars).
  * Stress test: what your holdings would gain or lose if the S&P 500 fell 1%, 3% or 5%, or
    moved as much as the futures have since 4 pm (each holding's beta: 3x funds move ~3x).
  * Fill quality: each of your recent fills vs the middle of the bid and ask at that minute
    (IBKR quotes saved by the Night desk since jb-night), in cents per share.
  * Limit price helper: bid, ask, a fair limit price for a buy or sell, and the exchanges' 20%
    overnight price band.
  * Game plan: a note you write in the evening; it is pushed to your phone at 9:00 am New York
    time before a trading day (JB_PLAN_TIME in settings.env) and shown on the phone page.
Nothing here places trades. Files: data/earn_settings.json, earn_cache.json, earn_log.json,
gameplan.json.
"""
import json
import os
import sys
import threading
import time
import urllib.parse
from datetime import date, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
SETTINGS = os.path.join(DATA, 'earn_settings.json')
CACHE = os.path.join(DATA, 'earn_cache.json')
LOG = os.path.join(DATA, 'earn_log.json')
PLAN = os.path.join(DATA, 'gameplan.json')
sys.path.insert(0, os.path.join(ROOT, 'lib'))
try:
    from nyse_hours import trading_day
except Exception:
    def trading_day(d):
        return d.weekday() < 5

DEFAULTS = {'call_push': True, 'plan_push': True}
_lock = threading.Lock()
_st = {'started': False, 'em': {}, 'cal': {}, 'nd': {}, 'errors': {}, 'wait_until': 0}


def _now():
    return int(time.time() * 1000)


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
    print(f'earn: {kind} {sym}: {text}', flush=True)
    watch.push_later(title, text, tags)


def _err(key, e):
    with _lock:
        _st['errors'][key] = str(e)[:160]
    if '429' in str(e) or 'Too Many' in str(e):
        _st['wait_until'] = time.time() + 600


def _yahoo(url):
    import world
    return world._get(url)


def _ysym(s):
    return urllib.parse.quote(s.replace('.', '-'))


def tracked():
    try:
        import mdata
        return mdata.tracked()
    except Exception:
        pass
    out = {}
    try:
        import news
        out = {s: dict(e) for s, e in news.tracked().items() if e['kind'] in ('stock', 'etf')}
    except Exception:
        pass
    return out


def held_positions():
    """[{sym, shares, last, value}] from the Portfolio tab, plus open journal positions."""
    out = {}
    try:
        import portfolio
        for p in portfolio.positions():
            if (p.get('shares') or 0) > 0:
                out[p['sym']] = {'sym': p['sym'], 'shares': p['shares'], 'last': p.get('last'), 'value': p.get('value'), 'from': 'Portfolio'}
    except Exception:
        pass
    try:
        import trading
        with trading._lock:
            fills = list(trading._journal()['fills'].values())
        _, open_ = trading.round_trips(fills)
        for o in open_:
            if '=' in o['sym'] or not o.get('qty') or o['sym'] in out:
                continue
            last = _last(o['sym'])
            out[o['sym']] = {'sym': o['sym'], 'shares': o['qty'], 'last': last,
                             'value': round(o['qty'] * last * (o.get('mult') or 1), 2) if last else None, 'from': 'journal (' + o['broker'] + ')'}
    except Exception:
        pass
    return list(out.values())


def _last(sym):
    with watch._lock:
        h = watch._px.get(sym)
        return h[-1][1] if h else None


# ------------------------------------------------------------------ earnings dates + call times (Yahoo calendarEvents)
def _earn_dates():
    return (watch._read(os.path.join(DATA, 'earnings.json'), {}).get('dates') or {})


def upcoming(now_ms, days=8):
    """Stocks on your boards / scanner / holdings reporting in the next `days` days."""
    d = watch.et(now_ms).date()
    info = tracked()
    out = []
    for s, e in _earn_dates().items():
        if s not in info or not e.get('date'):
            continue
        if d.isoformat() <= e['date'] <= (d + timedelta(days=days)).isoformat():
            out.append({'sym': s, 'date': e['date'], 'when': e.get('when') or '', 'mine': any(not w.startswith('Scanner') for w in info[s].get('where') or []),
                        'held': 'Held' in (info[s].get('where') or [])})
    out.sort(key=lambda x: (x['date'], x['when'] != 'before the open', x['sym']))
    return out


def fetch_calendar(sym):
    body = _yahoo('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + _ysym(sym) + '?modules=calendarEvents')
    res = ((body.get('quoteSummary') or {}).get('result') or [None])[0] or {}
    e = (res.get('calendarEvents') or {}).get('earnings') or {}
    raw = lambda lst: [x.get('raw') if isinstance(x, dict) else x for x in (lst or [])]
    dates, calls = raw(e.get('earningsDate')), raw(e.get('earningsCallDate'))
    return {'at': time.time(), 'release': (dates[0] * 1000) if dates else None, 'call': (calls[0] * 1000) if calls else None,
            'estimate': bool(e.get('isEarningsDateEstimate'))}


# ------------------------------------------------------------------ expected move (options straddle)
def straddle(calls, puts, price):
    """At-the-money call + put (mid of bid/ask when there is one, else the last trade)."""
    def mid(o):
        b, a = o.get('bid') or 0, o.get('ask') or 0
        return (b + a) / 2 if b > 0 and a > 0 else (o.get('lastPrice') or 0)
    ks = sorted({o['strike'] for o in calls} & {o['strike'] for o in puts})
    if not ks or not price:
        return None
    k = min(ks, key=lambda x: abs(x - price))
    c = next(o for o in calls if o['strike'] == k)
    p = next(o for o in puts if o['strike'] == k)
    v = mid(c) + mid(p)
    if v <= 0:
        return None
    return {'strike': k, 'cost': round(v, 2), 'pct': round(v / price * 100, 1)}


def fetch_expected(sym, date_iso, when, now_ms):
    base = 'https://query2.finance.yahoo.com/v7/finance/options/' + _ysym(sym)
    body = _yahoo(base)
    res = ((body.get('optionChain') or {}).get('result') or [None])[0]
    if not res:
        return {'at': time.time(), 'none': True}
    price = (res.get('quote') or {}).get('regularMarketPrice')
    # first expiry that lets the report be priced in: on/after the report day (after the next day for after-close reports)
    need = date_iso
    if when != 'before the open':
        d = date.fromisoformat(date_iso) + timedelta(days=1)
        while not trading_day(d):
            d += timedelta(days=1)
        need = d.isoformat()
    exps = [e for e in res.get('expirationDates') or [] if time.strftime('%Y-%m-%d', time.gmtime(e)) >= need]
    if not exps:
        return {'at': time.time(), 'none': True}
    exp = exps[0]
    chain = (res.get('options') or [{}])[0]
    if chain.get('expirationDate') != exp:
        time.sleep(1)
        b2 = _yahoo(base + '?date=' + str(exp))
        chain = ((((b2.get('optionChain') or {}).get('result') or [{}])[0]) or {}).get('options', [{}])[0]
    s = straddle(chain.get('calls') or [], chain.get('puts') or [], price)
    out = {'at': time.time(), 'price': price, 'expiry': time.strftime('%a %b %d', time.gmtime(exp))}
    if s:
        out.update(s)
        out['lo'], out['hi'] = round(price - s['cost'], 2), round(price + s['cost'], 2)
    return out


def typical(sym):
    try:
        import signals
        t = (watch._read(signals.STATE, {}).get('typical') or {}).get(sym)
        if t and t.get('avg'):
            return t
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ night vs day (Yahoo daily bars)
def night_day(sym):
    with _lock:
        hit = _st['nd'].get(sym)
    if hit and time.time() - hit['at'] < 6 * 3600:
        return hit
    import analysis
    rows = analysis.daily(sym)
    pts, n, d = [], 1.0, 1.0
    nights, days = [], []
    for prev, r in zip(rows, rows[1:]):
        _, o, _, _, c = r
        pc = prev[4]
        if not (o and c and pc):
            continue
        rn, rd = o / pc - 1, c / o - 1
        n *= 1 + rn
        d *= 1 + rd
        nights.append(rn)
        days.append(rd)
        pts.append([r[0], round((n - 1) * 100, 2), round((d - 1) * 100, 2)])
    win = lambda xs: round(sum(1 for x in xs if x > 0) / len(xs) * 100) if xs else None
    out = {'at': time.time(), 'sym': sym, 'pts': pts, 'night': round((n - 1) * 100, 1), 'day': round((d - 1) * 100, 1),
           'night_win': win(nights), 'day_win': win(days), 'n': len(pts)}
    with _lock:
        _st['nd'][sym] = out
    return out


# ------------------------------------------------------------------ stress test
def beta(sym):
    try:
        import analysis
        b = analysis._beta(sym)
        if b is not None:
            return b, 'last 90 days vs SPY'
    except Exception:
        pass
    return 1.0, 'not known yet, using 1'


def stress(now_ms):
    rows, fut = [], None
    try:
        import world
        snap = json.loads(world.snapshot())
        fut = next((r.get('since_close') for r in snap.get('rows') or [] if r.get('sym') == 'ES=F'), None)
    except Exception:
        pass
    scen = [-1.0, -3.0, -5.0] + ([round(fut, 2)] if fut is not None else [])
    for p in held_positions():
        if not p.get('value'):
            continue
        b, why = beta(p['sym'])
        rows.append({'sym': p['sym'], 'value': p['value'], 'beta': b, 'beta_why': why, 'from': p['from'],
                     'moves': [round(p['value'] * b * s / 100, 2) for s in scen]})
    total = [round(sum(r['moves'][i] for r in rows), 2) for i in range(len(scen))]
    return {'scenarios': scen, 'futures': fut, 'rows': rows, 'total': total, 'value': round(sum(r['value'] for r in rows), 2)}


# ------------------------------------------------------------------ fill quality vs the mid
_fq_lock = threading.Lock()


def fill_quality(limit=40):
    with _fq_lock:      # the loop and the page both call this; one at a time so fill_quotes.json is never written twice at once
        return _fill_quality(limit)


def _fill_quality(limit=40):
    try:
        import night
        import trading
    except Exception:
        return {'rows': [], 'note': 'Needs the Night desk (jb-night) and the My trading tab.'}
    with trading._lock:
        fills = sorted(trading._journal()['fills'].values(), key=lambda f: -f['t'])
    path = os.path.join(DATA, 'fill_quotes.json')     # the bid / ask at each fill, kept (the quote record only covers ~26 h)
    saved, changed = watch._read(path, {}), False
    rows = []
    for f in fills[:300]:
        q = saved.get(f['id'])
        if not q:
            ba = night.quote_at(f['sym'], f['t'])
            if not ba:
                continue
            q = saved[f['id']] = {'bid': ba[0], 'ask': ba[1]}
            changed = True
        mid = (q['bid'] + q['ask']) / 2
        # positive = better than the mid (bought below it / sold above it)
        c = (mid - f['price']) if f['side'] == 'buy' else (f['price'] - mid)
        rows.append({'t': f['t'], 'sym': f['sym'], 'side': f['side'], 'qty': f['qty'], 'price': f['price'], 'bid': q['bid'], 'ask': q['ask'],
                     'cents': round(c * 100, 1), 'dollars': round(c * f['qty'] * (f.get('mult') or 1), 2), 'broker': f['broker']})
        if len(rows) >= limit:
            break
    if changed:
        keep = {f['id'] for f in fills[:2000]}
        watch._write(path, {k: v for k, v in saved.items() if k in keep})
    avg = round(sum(r['cents'] for r in rows) / len(rows), 1) if rows else None
    return {'rows': rows, 'avg_cents': avg, 'total': round(sum(r['dollars'] for r in rows), 2) if rows else None}


# ------------------------------------------------------------------ limit price helper
def _r2(x):
    return round(x + 1e-9, 2) if x >= 1 else round(x, 4)


def limit_help(sym, side, now_ms):
    sym = ''.join(ch for ch in (sym or '').upper() if ch.isalnum() or ch in '.-')[:10]
    if not sym:
        return {'error': 'Type a symbol.'}
    q = None
    try:
        import night
        q = night._q.get(sym)
        if q and now_ms - q['ts'] > 5 * 60000:
            q = None
    except Exception:
        q = None
    last = _last(sym)
    out = {'sym': sym, 'side': side, 'last': last}
    if q:
        bid, ask = q['bid'], q['ask']
        mid, sp = (bid + ask) / 2, ask - bid
        out.update(bid=bid, ask=ask, mid=_r2(mid), spread=round(sp, 4), spread_pct=round(sp / mid * 100, 2) if mid else None)
        if side == 'sell':
            out['fair'], out['patient'], out['now'] = _r2(mid - sp * 0.25), _r2(mid), _r2(bid)
        else:
            out['fair'], out['patient'], out['now'] = _r2(mid + sp * 0.25), _r2(mid), _r2(ask)
    elif last:
        out['note'] = 'No live bid / ask for this symbol (only symbols on the IBKR board have them). Showing the last trade.'
    else:
        return {'error': f'No price for {sym} on the boards. Add it to your IBKR board first.'}
    ref = last or out.get('mid')
    if ref:
        out['band'] = [_r2(ref * 0.8), _r2(ref * 1.2)]
    return out


# ------------------------------------------------------------------ game plan
def plan():
    p = watch._read(PLAN, {})
    return p if isinstance(p, dict) else {}


def plan_time():
    v = os.environ.get('JB_PLAN_TIME', '9:00').strip()
    try:
        h, m = v.split(':')
        return int(h) % 24, int(m) % 60
    except Exception:
        return 9, 0


def check_plan(cfg, state, now_ms):
    d = watch.et(now_ms)
    h, m = plan_time()
    t = watch.at_et(d.date(), h, m)
    k = 'plan|' + d.date().isoformat()
    p = plan()
    if not (trading_day(d.date()) and t <= now_ms < t + 30 * 60000) or k in state['sent']:
        return
    state['sent'][k] = now_ms
    if cfg['plan_push'] and (p.get('text') or '').strip() and now_ms - (p.get('at') or 0) < 36 * 3600000:
        _send('plan', '', 'Your game plan', p['text'].strip()[:900], 'clipboard')


# ------------------------------------------------------------------ background loop
def check_calls(cfg, state, now_ms):
    with _lock:
        cal = dict(_st['cal'])
    info = tracked()
    for s, c in cal.items():
        t = c.get('call')
        if not t or s not in info:
            continue
        k = f'call|{s}|{t}'
        if 0 < t - now_ms <= 10 * 60000 and k not in state['sent']:
            state['sent'][k] = now_ms
            if cfg['call_push'] and any(not w.startswith('Scanner') for w in info[s].get('where') or []):
                _send('call', s, f'{s} earnings call soon', f"{s}'s earnings call starts at {watch.et(t).strftime('%-I:%M %p')} New York time. Prices can jump while it runs.", 'telephone_receiver')


def _loop():
    last = {'save': 0}
    while True:
        now = _now()
        try:
            cfg = settings()
            state = watch._read(os.path.join(DATA, 'earn_state.json'), {})
            state.setdefault('sent', {})
            check_plan(cfg, state, now)
            check_calls(cfg, state, now)
            if now - last.get('fq', 0) > 10 * 60000:
                last['fq'] = now
                fill_quality()
            if time.time() < _st['wait_until']:     # Yahoo asked us to back off: the plan / call pushes above still run
                watch._write(os.path.join(DATA, 'earn_state.json'), state)
                time.sleep(30)
                continue
            # one Yahoo lookup per round: call times (every 6 h), then expected moves (every 30 min in the day, 3 h otherwise)
            up = upcoming(now)
            did = False
            for u in up:
                s = u['sym']
                with _lock:
                    c = _st['cal'].get(s)
                if not c or time.time() - c['at'] > 6 * 3600:
                    try:
                        r = fetch_calendar(s)
                        with _lock:
                            _st['cal'][s] = r
                            _st['errors'].pop('calendar ' + s, None)
                    except Exception as e:
                        _err('calendar ' + s, e)
                        with _lock:
                            _st['cal'][s] = {'at': time.time() - 5 * 3600}
                    did = True
                    break
            if not did:
                sess_day = 570 <= watch.et(now).hour * 60 + watch.et(now).minute < 975
                for u in up:
                    s = u['sym']
                    with _lock:
                        e = _st['em'].get(s)
                    if not e or time.time() - e['at'] > (30 * 60 if sess_day else 3 * 3600) or e.get('date') != u['date']:
                        try:
                            r = fetch_expected(s, u['date'], u['when'], now)
                            r['date'] = u['date']
                            with _lock:
                                _st['em'][s] = r
                                _st['errors'].pop('options ' + s, None)
                        except Exception as ex:
                            _err('options ' + s, ex)
                            with _lock:
                                _st['em'][s] = {'at': time.time() - 20 * 60, 'date': u['date']}
                        did = True
                        break
            for k in [k for k, t in state['sent'].items() if now - t > 4 * 86400000]:
                del state['sent'][k]
            watch._write(os.path.join(DATA, 'earn_state.json'), state)
            if now - last['save'] > 10 * 60000:
                last['save'] = now
                with _lock:
                    watch._write(CACHE, {'em': _st['em'], 'cal': _st['cal']})
            time.sleep(4 if did else 30)
        except Exception as e:
            print(f'earn error: {e!r}', flush=True)
            time.sleep(30)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
        old = watch._read(CACHE, {})
        for k in ('em', 'cal'):
            if isinstance(old.get(k), dict):
                _st[k] = old[k]
    threading.Thread(target=_loop, name='earn', daemon=True).start()


# ------------------------------------------------------------------ what the pages get
def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return (v or '').strip()


def snapshot():
    now = _now()
    rows = []
    for u in upcoming(now):
        s = u['sym']
        with _lock:
            em = dict(_st['em'].get(s) or {})
            cal = dict(_st['cal'].get(s) or {})
        rows.append(dict(u, em=em if em.get('pct') else None, typical=typical(s), release=cal.get('release'), call=cal.get('call'),
                         estimate=cal.get('estimate')))
    with _lock:
        errors = dict(_st['errors'])
    syms = sorted(s for s, i in tracked().items())
    return {'at': now, 'settings': settings(), 'earnings': rows, 'stress': stress(now), 'fills': fill_quality(),
            'plan': plan(), 'plan_time': '%d:%02d' % plan_time(), 'symbols': syms, 'log': watch._read(LOG, [])[:30],
            'errors': errors, 'slow': time.time() < _st['wait_until']}


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    if sub == 'nightday':
        s = ''.join(ch for ch in _q(query, 'sym').upper() if ch.isalnum() or ch in '.-')[:10]
        if not s:
            return 400, {'error': 'Pick a symbol.'}
        try:
            return 200, night_day(s)
        except Exception as e:
            return 200, {'sym': s, 'error': 'Could not get daily prices from Yahoo right now (' + str(e)[:80] + ').'}
    if sub == 'limit':
        return 200, limit_help(_q(query, 'sym'), 'sell' if _q(query, 'side') == 'sell' else 'buy', _now())
    if sub == 'plan':
        return 200, dict(plan(), plan_time='%d:%02d' % plan_time())
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'settings':
        s = settings()
        for k in DEFAULTS:
            if k in (data.get('settings') or {}):
                s[k] = bool(data['settings'][k])
        watch._write(SETTINGS, s)
        return 200, snapshot()
    if sub == 'plan':
        text = str(data.get('text') or '')[:2000]
        watch._write(PLAN, {'text': text, 'at': _now()})
        return 200, dict(plan(), plan_time='%d:%02d' % plan_time())
    return 404, {'error': 'unknown'}
