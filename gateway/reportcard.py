"""Report Card (jb-reportcard): the board grades its own calls.

Every time a board feature makes a call (a meter reading, a scanner pick, an alert, a report),
it is written down here with the time, the symbol and which way it pointed. Later the grader
looks up what really happened, with free Yahoo prices, and marks it right or wrong at fixed
points: 30 and 60 minutes later, the next 9:30 am open, the next 4 pm close, and the close
1, 5, 20 and 60 trading days later.

  "right" for a call that points up or down: the price moved that way (for picks, filings,
  relative strength and politicians' trades: moved that way compared with SPY).
  "right" for an earnings expected move: the real move stayed inside the expected range.
  Event days (Fed, CPI, jobs...) aren't right or wrong: the card shows how much bigger the
  move was than on a normal day.

Features that use only prices are also replayed on up to 2 years of past prices (rc_replay.py),
so they have a grade from day one. The rest are graded from the day this was installed.

  GET  /api/plus/reportcard                  every feature with its grades
  GET  /api/plus/reportcard/feature?key=     one feature: each part, each time point, weekly line, last 60 calls
  GET  /api/plus/reportcard/badges           the small "right 61%" tags on the meters
  GET  /api/plus/reportcard/trades           your journal trades: did they agree with the board?
  POST /api/plus/reportcard/settings         {weekly_push: true|false}
  POST /api/plus/reportcard/replay           run the 2-year replay again now
Files: data/reportcard.db (every call and its grades), data/reportcard_replay.json,
data/reportcard_settings.json. Pushes: Friday 8:30 pm New York, the week's best and worst feature.
"""
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
from datetime import date, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
DB = os.path.join(DATA, 'reportcard.db')
SETTINGS = os.path.join(DATA, 'reportcard_settings.json')
sys.path.insert(0, os.path.join(ROOT, 'lib')) if os.path.join(ROOT, 'lib') not in sys.path else None

HORIZONS = [('m30', '30 min'), ('m60', '1 hour'), ('open', 'Next open'), ('close', 'Next close'),
            ('d1', '1 day'), ('d5', '5 days'), ('d20', '20 days'), ('d60', '60 days')]
HKEYS = [h for h, _ in HORIZONS]
HNAME = dict(HORIZONS)

# key, name, what counts as right, which time points, the main one, replayed on past prices?
FEATURES = [
    ('fut_mood', 'Futures mood meter', 'S&P futures keep moving the way the meter points (Risk-on = up, Risk-off = down). Mixed readings are not graded.',
     ['m60', 'open', 'close', 'd1'], 'close', True),
    ('picks_mood', 'Picks mood meter', 'The Picks list as a whole keeps moving the way the meter points.', ['m60', 'close', 'd1'], 'close', False),
    ('mood', 'Overnight mood (World tab and phone "since the 4 pm close")', 'S&P futures move the way the mood points by the next open and close.',
     ['open', 'close', 'd1'], 'open', True),
    ('macro', 'MACRO score', 'Tailwind = SPY up over the next weeks, Headwind = down.', ['d5', 'd20', 'd60'], 'd20', False),
    ('sent_short', 'Sentiment report: short term', 'SPY moves the way the short-term (1-3 day) score points.', ['d1', 'd5'], 'd1', False),
    ('sent_mid', 'Sentiment report: mid term', 'SPY moves the way the mid-term (2-8 week) score points.', ['d5', 'd20'], 'd20', False),
    ('sent_long', 'Sentiment report: long term', 'SPY moves the way the long-term (3-12 month) score points. Needs months to grade.', ['d20', 'd60'], 'd60', False),
    ('sent_comb', 'Sentiment report: combined', 'SPY moves the way the combined score points.', ['d5', 'd20'], 'd20', False),
    ('picks', 'Scanner Picks', 'An up pick beats SPY (a down pick trails SPY) afterwards.', ['d1', 'd5', 'd20'], 'd5', True),
    ('pick_signal', 'Scanner Picks: each signal', 'Each of the signals behind the Picks, on its own: does the stock beat (or trail) SPY the way the signal points?',
     ['d1', 'd5', 'd20'], 'd5', True),
    ('etf_pick', 'ETF pick of the hour', 'The picked ETF is higher an hour later and at the close.', ['m60', 'close', 'd1'], 'm60', False),
    ('gap_score', 'Gap scorecard', 'The scorecard\'s best guess ("gaps like this usually hold" or "usually fade") came true that day.', ['close'], 'close', True),
    ('gaps4am', '4 am gap list', 'A stock gapping at 4 am keeps going the same way by the 9:30 open and the close.', ['open', 'close'], 'open', True),
    ('alert_move', 'Big and fast move alerts', 'The move keeps going 30 and 60 minutes after the alert.', ['m30', 'm60', 'close'], 'm60', False),
    ('alert_hl', 'Crossed yesterday\'s high / low alerts', 'The price stays above yesterday\'s high (or below the low) until the close.', ['m60', 'close', 'd1'], 'close', True),
    ('alert_volume', 'Heavy volume alerts', 'The day\'s move keeps going after the heavy volume.', ['m30', 'm60', 'close'], 'm60', False),
    ('buzz', 'StockTwits buzz', 'Not up or down: shows how the stock did vs SPY after chatter jumped.', ['d1', 'd5'], 'd1', False),
    ('filing', 'SEC filings (8-K, insider buy, 13D)', 'Insider buys: the stock beats SPY afterwards. Other filings: how it moved vs SPY.', ['d1', 'd5', 'd20'], 'd5', False),
    ('earn_em', 'Earnings expected move', 'The real earnings move stayed inside the expected range.', ['close'], 'close', False),
    ('maxpain', 'Options max pain', 'The price moves toward max pain by the end of the week.', ['d1', 'd5'], 'd5', False),
    ('putcall', 'Put/call ratio', 'High put/call (over 1.0) = down, low (under 0.7) = up, over the next week.', ['d1', 'd5'], 'd5', False),
    ('bigmoney', 'Big money: politicians\' trades', 'A politician\'s buy beats SPY (a sale trails it) over the following weeks.', ['d5', 'd20', 'd60'], 'd20', False),
    ('relstr', 'Relative strength', 'The 3 strongest stocks vs SPY keep beating it; the 3 weakest keep trailing.', ['d5', 'd20'], 'd5', True),
    ('weather', 'Market weather report', 'Sectors keep moving the way the morning weather report says.', ['close', 'd1'], 'close', True),
    ('econ', 'Economic calendar events', 'Not right or wrong: how big the S&P move was after Fed / CPI / jobs news compared with a normal day.', ['m60', 'close'], 'close', True),
]
FMAP = {f[0]: f for f in FEATURES}
INVERSE = {'SQQQ', 'SPXS', 'SPXU', 'SDOW', 'TZA', 'SOXS', 'TECS', 'FAZ', 'LABD', 'TMV', 'SDS', 'QID', 'SH', 'PSQ', 'UVXY', 'VXX', 'SVXY'}

_lock = threading.RLock()
_st = {'started': False, 'last': {}, 'errors': {}, 'graded_at': 0, 'recorded_at': 0, 'replay_running': False}
_bars = {}        # (sym, interval) -> (fetched, [(t_ms, open, close)])


def _now():
    return int(time.time() * 1000)


def _err(key, e):
    _st['errors'][key] = f'{type(e).__name__}: {e}'[:200]


# ------------------------------------------------------------------ the call book (SQLite)
def _db():
    c = sqlite3.connect(DB, timeout=30)
    c.execute('''CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY, k TEXT UNIQUE, feature TEXT, sub TEXT, sym TEXT, at INTEGER,
        dir INTEGER, score REAL, p0 REAL, bench TEXT, kind TEXT, info TEXT, src TEXT,
        m30 REAL, m60 REAL, open REAL, close REAL, d1 REAL, d5 REAL, d20 REAL, d60 REAL, done INTEGER DEFAULT 0)''')
    c.execute('CREATE INDEX IF NOT EXISTS calls_f ON calls(feature, at)')
    c.execute('CREATE INDEX IF NOT EXISTS calls_d ON calls(done, at)')
    return c


def add(feature, sub, sym, at, dir_, k, score=None, p0=None, bench='', kind='dir', info=None):
    """Write one call down (once: k is its unique key). Returns True when it was new."""
    try:
        with _lock:
            c = _db()
            try:
                cur = c.execute('INSERT OR IGNORE INTO calls (k, feature, sub, sym, at, dir, score, p0, bench, kind, info, src) '
                                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                (f'{feature}|{k}', feature, sub or '', sym, int(at), int(dir_ or 0), score, p0, bench or '', kind,
                                 json.dumps(info or {}), 'live'))
                c.commit()
                return cur.rowcount > 0
            finally:
                c.close()
    except Exception as e:
        _err('save', e)
        return False


# ------------------------------------------------------------------ New York calendar
def _nh():
    import nyse_hours
    return nyse_hours


def trading_day(d):
    try:
        return _nh().trading_day(d)
    except Exception:
        return d.weekday() < 5


def close_hm(d):
    try:
        return (13, 0) if d in _nh().nyse_early_closes(d.year) else (16, 0)
    except Exception:
        return (16, 0)


def next_tday(d, n=1):
    while n > 0:
        d += timedelta(days=1)
        if trading_day(d):
            n -= 1
    return d


def close_ms(d):
    hh, mm = close_hm(d)
    return watch.at_et(d, hh, mm)


def target(h, at):
    """When horizon h ends for a call made at `at` (ms)."""
    if h == 'm30':
        return at + 30 * 60000
    if h == 'm60':
        return at + 60 * 60000
    d = watch.et(at).date()
    if h == 'open':
        while not (trading_day(d) and watch.at_et(d, 9, 30) > at):
            d += timedelta(days=1)
        return watch.at_et(d, 9, 30)
    while not (trading_day(d) and close_ms(d) > at):
        d += timedelta(days=1)
    if h == 'close':
        return close_ms(d)
    n = int(h[1:])
    return close_ms(next_tday(d, n))


def session_of(ms):
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    if not trading_day(d.date()) and not (m >= 1200 and trading_day(d.date() + timedelta(days=1))):
        return 'closed'
    if 570 <= m < 960:
        return 'regular'
    if 240 <= m < 570:
        return 'pre-market'
    if 960 <= m < 1200:
        return 'after-hours'
    return 'overnight'


# ------------------------------------------------------------------ prices (free Yahoo)
IV = {'5m': ('60d', 300000, 20 * 60), '1h': ('730d', 3600000, 6 * 3600), '1d': ('5y', 86400000, 12 * 3600)}


def _ysym(s):
    return s if ('=' in s or '^' in s or '-USD' in s) else s.replace('.', '-')


def yahoo_bars(sym, iv):
    """[(t_ms, open, close)] oldest first, cached. Daily bars are stamped at 9:30 am New York."""
    key = (sym, iv)
    rng, _, ttl = IV[iv]
    with _lock:
        hit = _bars.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    import world
    if time.time() < (world._status.get('wait_until') or 0):
        return hit[1] if hit else []
    url = (f'https://{world.HOSTS[0]}/v8/finance/chart/{urllib.parse.quote(_ysym(sym))}?range={rng}&interval={iv}'
           + ('&includePrePost=true' if iv != '1d' else ''))
    body = world._get(url, timeout=20)
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    q = ((res.get('indicators') or {}).get('quote') or [{}])[0]
    out = []
    for t, o, c in zip(res.get('timestamp') or [], q.get('open') or [], q.get('close') or []):
        if c is None:
            continue
        t = int(t) * 1000
        if iv == '1d':
            t = watch.at_et(watch.et(t).date(), 9, 30)
        out.append((t, float(o if o is not None else c), float(c)))
    with _lock:
        _bars[key] = (time.time(), out)
        if len(_bars) > 400:
            for k in sorted(_bars, key=lambda k: _bars[k][0])[:100]:
                del _bars[k]
    time.sleep(1.2)                       # spaced out: Yahoo limits how often we may ask
    return out


def bar_price(bars, ms, span):
    """Price at a moment: the opening price of a bar starting then, else the last close before it."""
    before, bt = None, None
    for t, o, c in bars:
        if t >= ms:
            if t - ms <= span:
                return o
            break
        before, bt = c, t
    if bt is None or ms - bt > 4 * 86400000:     # nothing near that moment
        return None
    return before


def daily_price(bars, ms):
    d = watch.et(ms)
    day = d.date()
    m = d.hour * 60 + d.minute
    hh, mm = close_hm(day)
    prev = None
    for t, o, c in bars:
        bd = watch.et(t).date()
        if bd < day:
            prev = c
            continue
        if bd == day:
            if m < 570:
                return prev
            if m < hh * 60 + mm:
                return o
            return c
        break
    return prev


def price_at(sym, ms):
    age = _now() - ms
    tries = ['5m', '1h', '1d'] if age < 55 * 86400000 else ['1h', '1d'] if age < 700 * 86400000 else ['1d']
    for iv in tries:
        try:
            bars = yahoo_bars(sym, iv)
        except Exception as e:
            _err('yahoo ' + sym, e)
            continue
        if not bars or bars[0][0] > ms + 3600000:
            continue
        p = daily_price(bars, ms) if iv == '1d' else bar_price(bars, ms, IV[iv][1])
        if p:
            return p
    return None


def _live(sym, ms):
    """The board's own price for sym near ms (IBKR / Webull stream, includes overnight)."""
    try:
        with watch._lock:
            hist = list(watch._px.get(sym) or [])
        if hist and hist[0][0] <= ms <= hist[-1][0] + 15 * 60000:
            return watch.price_at(hist, ms)
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ grading
def _move(sym, p0, at, t):
    a = p0 or _live(sym, at) or price_at(sym, at)
    b = _live(sym, t) if _now() - t < 30 * 60000 else None
    b = b or price_at(sym, t)
    if not a or not b:
        return None
    return (b / a - 1) * 100


def _due(r, now):
    """jb-safety: True when one of this call's time points has passed and is still empty (or it is too old to wait)."""
    have = dict(zip(HKEYS, r[7:]))
    want = (FMAP.get(r[1]) or (0, 0, 0, HKEYS))[3]
    if now - r[3] > 120 * 86400000 or all(have.get(h) is not None for h in want):
        return True
    return any(have.get(h) is None and target(h, r[3]) <= now - 20 * 60000 for h in want)


def grade(limit=400):
    """Fill in every time point that has passed. Returns how many were filled."""
    now = _now()
    with _lock:
        c = _db()
        rows = c.execute('SELECT id, feature, sym, at, p0, bench, info, ' + ', '.join(HKEYS) +
                         ' FROM calls WHERE done = 0 ORDER BY at').fetchall()
        c.close()
    rows = [r for r in rows if _due(r, now)][:limit]     # jb-safety: calls with a grade due now, oldest first
    filled = 0
    import world
    for r in rows:
        if time.time() < (world._status.get('wait_until') or 0):
            break
        cid, feat, sym, at, p0, bench, info = r[:7]
        have = dict(zip(HKEYS, r[7:]))
        want = (FMAP.get(feat) or (0, 0, 0, HKEYS))[3]
        upd, all_done = {}, True
        for h in want:
            if have.get(h) is not None:
                continue
            t = target(h, at)
            if t > now - 20 * 60000:          # not yet (and give Yahoo 20 minutes to catch up)
                all_done = False
                continue
            try:
                inf = json.loads(info or '{}')
                syms = inf.get('syms') or [sym]
                moves = [m for m in (_move(s, p0 if s == sym else None, at, t) for s in syms) if m is not None]
                mv = sum(moves) / len(moves) if moves else None
                if mv is not None and bench:
                    bm = _move(bench, None, at, t)
                    mv = None if bm is None else mv - bm
            except Exception as e:
                _err('grade', e)
                mv = None
            if mv is None:
                if now - t > 10 * 86400000:   # prices never came: give up on this point
                    upd[h] = -99999.0
                else:
                    all_done = False
                continue
            upd[h] = round(mv, 3)
        if upd or all_done or now - at > 120 * 86400000:
            with _lock:
                c = _db()
                sets = ', '.join(f'{k} = ?' for k in upd)
                c.execute('UPDATE calls SET ' + (sets + ', ' if sets else '') + 'done = ? WHERE id = ?',
                          list(upd.values()) + [1 if (all_done or now - at > 120 * 86400000) else 0, cid])
                c.commit()
                c.close()
            filled += len(upd)
    _st['graded_at'] = time.time()
    return filled


# ------------------------------------------------------------------ the recorders (one per feature)
def _hour_key(now):
    return now // 3600000


def _dir_score(score, cut=25):
    if score is None:
        return 0
    return 1 if score >= cut else -1 if score <= -cut else 0


def rec_meters(now):
    import meters
    snap = meters.snapshot(now)
    hk = _hour_key(now)
    for key, r in (snap.get('futures') or {}).items():
        add('fut_mood', key, 'ES=F', now, _dir_score(r.get('score')), f'{key}|{hk}', score=r.get('score'),
            p0=_live('ES=F', now), info={'label': r.get('label')})
    for key, r in (snap.get('picks') or {}).items():
        syms = [p['sym'] for p in r.get('parts') or []]
        if syms:
            add('picks_mood', key, 'PICKS', now, _dir_score(r.get('score')), f'{key}|{hk}', score=r.get('score'),
                info={'label': r.get('label'), 'syms': syms})


def rec_mood(now):
    import world
    m = world.mood()
    if m:
        add('mood', '', 'ES=F', now, _dir_score(m['score']), str(_hour_key(now)), score=m['score'], p0=_live('ES=F', now),
            info={'label': m['label']})


def rec_macro(now):
    d = watch.et(now)
    if d.hour < 16:
        return
    import macro
    o = macro.overall(macro.indicators())
    if o:
        add('macro', '', 'SPY', now, _dir_score(o['score'], 20), d.date().isoformat(), score=o['score'], info={'label': o['label']})


def rec_sentiment(now):
    import sentiment
    st = watch._read(sentiment.STORE, {'reports': []})
    for rep in st.get('reports') or []:
        sc = rep.get('scores') or {}
        for key, feat in (('short', 'sent_short'), ('mid', 'sent_mid'), ('long', 'sent_long'), ('combined', 'sent_comb')):
            v = sc.get(key)
            if v is not None:
                add(feat, '', 'SPY', rep['at'], _dir_score(v, 15), str(rep['at']), score=v, info={'label': sentiment.label(v)})


def rec_picks(now):
    d = watch.et(now)
    if not trading_day(d.date()) or d.hour * 60 + d.minute < 945:      # once a day, from 3:45 pm
        return
    day = d.date().isoformat()
    if _st['last'].get('picks_day') == day:
        return
    import meters
    lst, mode = meters._read_picks()
    for p in lst:
        s = p['symbol']
        lean = p.get('lean') or ''
        add('picks', lean, s, now, 1 if lean == 'up' else -1 if lean == 'down' else 0, f'{day}|{s}', score=p.get('score'),
            p0=p.get('price'), bench='SPY', info={'mode': mode})
        for sg in p.get('signals') or []:
            add('pick_signal', sg.get('kind') or '', s, now, sg.get('dir') or 0, f'{day}|{s}|{sg.get("kind")}', score=sg.get('strength'),
                p0=p.get('price'), bench='SPY', info={'title': sg.get('title'), 'family': sg.get('family')})
    _st['last']['picks_day'] = day


def rec_pick(now):
    import pick
    for r in pick.history()[-50:]:
        p = r.get('pick')
        if p and p.get('sym'):
            add('etf_pick', p.get('side') or '', p['sym'], r['at'], 1, str(r['at']), score=p.get('score'), p0=p.get('price'),
                info={'group': p.get('group')})


PCT = re.compile(r'([A-Z][A-Z0-9.\-=^]{0,9}) ([+-]\d+(?:\.\d+)?)%')


def rec_signals(now):
    import signals
    for e in watch._read(signals.LOG, [])[:200]:
        kind, sym, text, at = e.get('kind') or '', e.get('sym') or '', e.get('text') or '', e.get('at') or 0
        if not at or now - at > 7 * 86400000:
            continue
        m = re.search(r'([+-]\d+(?:\.\d+)?)%', text)
        sign = (1 if float(m.group(1)) > 0 else -1) if m else 0
        if kind == '4 am gaps':
            for s, v in PCT.findall(text):
                add('gaps4am', '', s, at, 1 if float(v) > 0 else -1, f'{at}|{s}', score=float(v))
        elif kind == 'big move':
            add('alert_move', 'big move', sym, at, 1 if ' is up ' in text else -1 if ' is down ' in text else sign, f'{at}|{sym}|b')
        elif kind == 'fast move':
            add('alert_move', 'fast move', sym, at, sign, f'{at}|{sym}|f')
        elif kind in ('yesterday high', 'yesterday low'):
            add('alert_hl', kind, sym, at, 1 if kind == 'yesterday high' else -1, f'{at}|{sym}|{kind}')
        elif kind == 'volume':
            add('alert_volume', '', sym, at, sign, f'{at}|{sym}')
        elif kind == 'buzz':
            add('buzz', '', sym, at, 0, f'{at}|{sym}', bench='SPY')
        elif kind == 'filing':
            low = text.lower()
            sub = 'insider buy' if 'insider' in low and 'buy' in low else '13D' if '13d' in low else '8-K' if '8-k' in low else 'other'
            add('filing', sub, sym, at, 1 if sub == 'insider buy' else 0, f'{at}|{sym}', bench='SPY')


def rec_earn(now):
    import earn
    with earn._lock:
        em = dict(earn._st.get('em') or {})
    for u in earn.upcoming(now, days=3):
        e = em.get(u['sym']) or {}
        if not e.get('cost') or not e.get('price') or e.get('date') != u['date']:
            continue
        day = date.fromisoformat(u['date'])
        start = close_ms(day) if u['when'] != 'before the open' else close_ms(_prev_tday(day))
        add('earn_em', u['when'], u['sym'], start, 0, f'{u["sym"]}|{u["date"]}', kind='range',
            info={'em': round(e['cost'] / e['price'] * 100, 2), 'lo': e.get('lo'), 'hi': e.get('hi'), 'date': u['date']})


def _prev_tday(d):
    d -= timedelta(days=1)
    while not trading_day(d):
        d -= timedelta(days=1)
    return d


def rec_options(now):
    d = watch.et(now)
    if not trading_day(d.date()) or d.hour * 60 + d.minute < 930:     # once a day, from 3:30 pm
        return
    day = d.date().isoformat()
    import analysis
    with analysis._lock:
        opts = dict(analysis._c.get('options') or {})
    for s, o in opts.items():
        if not isinstance(o, dict) or o.get('none') or time.time() - (o.get('at') or 0) > 6 * 3600:
            continue
        price = o.get('price') or _live(s, now)
        mp = o.get('max_pain')
        if mp and price and abs(mp / price - 1) > 0.005:
            add('maxpain', '', s, now, 1 if mp > price else -1, f'{day}|{s}', score=mp, p0=price, info={'max_pain': mp})
        pcr = o.get('pc_oi')
        if pcr:
            dr = -1 if pcr > 1.0 else 1 if pcr < 0.7 else 0
            add('putcall', '', s, now, dr, f'{day}|{s}', score=pcr, p0=price)


def rec_bigmoney(now):
    import bigmoney
    snap = watch._read(bigmoney.CACHE, {})
    pol = snap.get('politicians') or []
    if not pol:
        try:
            pol = bigmoney.politicians()
        except Exception:
            pol = []
    for t in pol[:400]:
        if t.get('kind') not in ('buy', 'sell') or not t.get('sym') or not t.get('date'):
            continue
        try:
            d = date.fromisoformat(t['date'])
        except Exception:
            continue
        if (watch.et(now).date() - d).days > 400:
            continue
        add('bigmoney', t['kind'], t['sym'], close_ms(d), 1 if t['kind'] == 'buy' else -1,
            f"{t['sym']}|{t['date']}|{t['kind']}|{t.get('who') or t.get('name') or ''}|{t.get('amount') or ''}", bench='SPY',
            info={'who': t.get('who') or t.get('name') or '', 'amount': t.get('amount') or ''})


def rec_relstr(now):
    d = watch.et(now)
    if not trading_day(d.date()) or d.hour * 60 + d.minute < 955:
        return
    import analysis
    rows = [r for r in analysis.relative(now).get('rows') or [] if r.get('vs_SPY_d21') is not None]
    rows.sort(key=lambda r: -r['vs_SPY_d21'])
    day = d.date().isoformat()
    for r in rows[:3]:
        add('relstr', 'strongest', r['sym'], now, 1, f'{day}|{r["sym"]}', score=r['vs_SPY_d21'], bench='SPY')
    for r in rows[-3:] if len(rows) > 6 else []:
        add('relstr', 'weakest', r['sym'], now, -1, f'{day}|{r["sym"]}', score=r['vs_SPY_d21'], bench='SPY')


def rec_weather(now):
    import extras
    w = extras._read(extras.WEATHER, {})
    at = w.get('at')
    if not at or now - at > 6 * 3600000:
        return
    d = watch.et(at)
    if d.hour >= 16 or not trading_day(d.date()):
        return
    for r in (w.get('day') or {}).get('rows') or []:
        chg = r.get('chg')
        if chg is not None and abs(chg) >= 0.3:
            add('weather', r.get('word') or '', r['sym'], at, 1 if chg > 0 else -1, f'{d.date()}|{r["sym"]}', score=chg)


def rec_econ(now):
    import world
    with world._lock:
        ev = list(world._cal.get('events') or [])
    for e in ev:
        if str(e.get('impact')).lower() != 'high' or not e.get('t') or e['t'] > now or now - e['t'] > 3 * 86400000:
            continue
        if e.get('kind') == 'auction':
            continue
        add('econ', e.get('kind') or 'data', 'ES=F', e['t'] - 15 * 60000, 0, f"{e['t']}|{e.get('title')}", kind='size',
            info={'title': e.get('title')})


RECORDERS = [('meters', rec_meters, 3600), ('mood', rec_mood, 3600), ('macro', rec_macro, 3600), ('sentiment', rec_sentiment, 1800),
             ('picks', rec_picks, 300), ('etf pick', rec_pick, 900), ('alerts', rec_signals, 300), ('earnings', rec_earn, 3600),
             ('options', rec_options, 1800), ('big money', rec_bigmoney, 6 * 3600), ('relative strength', rec_relstr, 1800),
             ('weather', rec_weather, 1800), ('events', rec_econ, 1800)]


def record_all():
    now = _now()
    for name, fn, every in RECORDERS:
        last = _st['last'].get(name, 0)
        if name in ('meters', 'mood'):
            due = _hour_key(now) != _hour_key(last)
        else:
            due = now - last >= every * 1000
        if not due:
            continue
        _st['last'][name] = now
        try:
            fn(now)
            _st['errors'].pop(name, None)
        except Exception as e:
            _err(name, e)
    _st['recorded_at'] = time.time()


# ------------------------------------------------------------------ grades
def _right(kind, dir_, mv, info):
    if mv is None or mv <= -99998:
        return None
    if kind == 'range':
        em = (info or {}).get('em')
        return None if not em else abs(mv) <= em
    if kind == 'size' or not dir_:
        return None
    return mv * dir_ > 0


def _tally(rows, h):
    """rows: (dir, kind, info, move) -> {n, right, pct, avg_right, avg_wrong, avg, up_pct}"""
    n = r_ = 0
    ar, aw, alls, ups, nd = [], [], [], 0, 0
    for dir_, kind, info, mv in rows:
        if mv is None or mv <= -99998:
            continue
        ok = _right(kind, dir_, mv, info)
        if ok is None:
            nd += 1
            alls.append(abs(mv) if kind == 'size' else mv)
            ups += mv > 0
            continue
        n += 1
        r_ += ok
        (ar if ok else aw).append(abs(mv))
    out = {'n': n, 'right': r_, 'pct': round(100 * r_ / n) if n else None,
           'avg_right': round(sum(ar) / len(ar), 2) if ar else None, 'avg_wrong': round(sum(aw) / len(aw), 2) if aw else None}
    if nd:
        out.update(n_other=nd, avg=round(sum(alls) / len(alls), 2), up_pct=round(100 * ups / nd))
    return out


def _rows(feature, since=0, sub=None):
    with _lock:
        c = _db()
        q = 'SELECT sub, sym, at, dir, kind, info, score, ' + ', '.join(HKEYS) + ' FROM calls WHERE feature = ? AND at >= ?'
        args = [feature, since]
        if sub is not None:
            q += ' AND sub = ?'
            args.append(sub)
        out = c.execute(q + ' ORDER BY at', args).fetchall()
        c.close()
    return out


def _info(s):
    try:
        return json.loads(s or '{}')
    except Exception:
        return {}


def replay():
    return watch._read(os.path.join(DATA, 'reportcard_replay.json'), {})


def summary():
    rp = replay()
    feats = []
    with _lock:
        c = _db()
        counts = dict(c.execute('SELECT feature, COUNT(*) FROM calls GROUP BY feature').fetchall())
        first = c.execute('SELECT MIN(at) FROM calls').fetchone()[0]
        c.close()
    for key, name, what, hs, main, can_replay in FEATURES:
        rows = _rows(key)
        live = {}
        for h in hs:
            i = 7 + HKEYS.index(h)
            live[h] = _tally([(r[3], r[4], _info(r[5]), r[i]) for r in rows], h)
        trend = _weekly(rows, main)
        feats.append({'key': key, 'name': name, 'what': what, 'horizons': [[h, HNAME[h]] for h in hs], 'main': main,
                      'calls': counts.get(key, 0), 'live': live, 'trend': trend[-12:],
                      'replay': (rp.get('features') or {}).get(key), 'can_replay': can_replay})
    return {'features': feats, 'since': first, 'graded_at': int(_st['graded_at'] * 1000) or None,
            'recorded_at': int(_st['recorded_at'] * 1000) or None, 'replay_at': rp.get('at'), 'replay_running': _st['replay_running'],
            'replay_note': rp.get('note') or '', 'errors': dict(_st['errors']), 'settings': settings()}


def _weekly(rows, h):
    i = 7 + HKEYS.index(h)
    wk = {}
    for r in rows:
        ok = _right(r[4], r[3], r[i], _info(r[5]))
        if ok is None:
            continue
        d = watch.et(r[2]).date()
        monday = (d - timedelta(days=d.weekday())).isoformat()
        a = wk.setdefault(monday, [0, 0])
        a[0] += 1
        a[1] += ok
    return [{'week': w, 'n': a[0], 'pct': round(100 * a[1] / a[0])} for w, a in sorted(wk.items())]


def feature(key, sub=None):
    f = FMAP.get(key)
    if not f:
        return {'error': 'unknown feature'}
    _, name, what, hs, main, _ = f
    rows = _rows(key)
    subs = {}
    for r in rows:
        subs.setdefault(r[0], []).append(r)
    by_sub = []
    for s, rs in sorted(subs.items(), key=lambda kv: -len(kv[1])):
        by_sub.append({'sub': s, 'calls': len(rs),
                       'h': {h: _tally([(r[3], r[4], _info(r[5]), r[7 + HKEYS.index(h)]) for r in rs], h) for h in hs}})
    sess = {}
    for r in rows:
        sess.setdefault(session_of(r[2]), []).append(r)
    by_sess = [{'session': s, 'h': _tally([(r[3], r[4], _info(r[5]), r[7 + HKEYS.index(main)]) for r in rs], main)}
               for s, rs in sess.items()]
    pick = [r for r in rows if sub is None or r[0] == sub]
    last = []
    for r in pick[-60:][::-1]:
        inf = _info(r[5])
        g = {}
        for h in hs:
            mv = r[7 + HKEYS.index(h)]
            g[h] = None if mv is None else {'move': None if mv <= -99998 else round(mv, 2), 'ok': _right(r[4], r[3], mv, inf)}
        last.append({'sub': r[0], 'sym': r[1], 'at': r[2], 'dir': r[3], 'score': r[6], 'info': inf, 'g': g})
    rp = (replay().get('features') or {}).get(key)
    return {'key': key, 'name': name, 'what': what, 'horizons': [[h, HNAME[h]] for h in hs], 'main': main,
            'subs': by_sub, 'sessions': by_sess, 'weekly': _weekly(pick, main), 'last': last, 'replay': rp}


def badges():
    out = {}
    for key in ('fut_mood', 'picks_mood', 'mood'):
        f = FMAP[key]
        rows = _rows(key)
        rp = ((replay().get('features') or {}).get(key) or {})
        per = {}
        subs = sorted({r[0] for r in rows} | set((rp.get('subs') or {}).keys())) or ['']
        for s in subs:
            rs = [r for r in rows if r[0] == s]
            t = _tally([(r[3], r[4], _info(r[5]), r[7 + HKEYS.index(f[4])]) for r in rs], f[4])
            rsub = ((rp.get('subs') or {}).get(s) or {}).get(f[4]) or (rp.get('h') or {}).get(f[4])
            if t['n'] < 10 and rsub and rsub.get('n'):
                per[s] = {'pct': rsub['pct'], 'n': rsub['n'], 'src': 'replay', 'h': HNAME[f[4]]}
            elif t['n']:
                per[s] = {'pct': t['pct'], 'n': t['n'], 'src': 'live', 'h': HNAME[f[4]]}
        out[key] = per
    return out


def trades():
    """Your journal round trips: was the Futures mood (since the 4 pm close) on your side when you got in?"""
    import trading
    j = trading._journal()
    trips, _ = trading.round_trips(list((j.get('fills') or {}).values()))
    rows = _rows('fut_mood', sub='close')
    times = [r[2] for r in rows]
    import bisect
    agree, against, nocall = [], [], []
    for t in trips:
        et_ = t.get('entry_t')
        if not et_ or t.get('pnl_pct') is None:
            continue
        i = bisect.bisect_right(times, et_) - 1
        if i < 0 or et_ - times[i] > 2 * 3600000 or not rows[i][3]:
            nocall.append(t)
            continue
        want = 1 if t.get('side') == 'long' else -1
        if t.get('sym') in INVERSE:
            want = -want
        (agree if want == rows[i][3] else against).append(t)

    def st(ts):
        if not ts:
            return {'n': 0}
        w = [x for x in ts if (x.get('pnl') or 0) > 0]
        return {'n': len(ts), 'win_pct': round(100 * len(w) / len(ts)), 'avg_pct': round(sum(x['pnl_pct'] for x in ts) / len(ts), 2),
                'pnl': round(sum(x.get('pnl') or 0 for x in ts), 2)}
    return {'agree': st(agree), 'against': st(against), 'no_call': st(nocall),
            'note': 'Agree = the Futures mood (since the 4 pm close) pointed your way within 2 hours before you got in. '
                    'Inverse ETFs (SQQQ, SOXS...) count the other way round.'}


# ------------------------------------------------------------------ settings + weekly push
def settings():
    s = watch._read(SETTINGS, {})
    return {'weekly_push': s.get('weekly_push', True)}


def save_settings(data):
    s = settings()
    if 'weekly_push' in data:
        s['weekly_push'] = bool(data['weekly_push'])
    watch._write(SETTINGS, s)
    return s


def week_text(now):
    since = now - 7 * 86400000
    res = []
    for key, name, _, hs, main, _ in FEATURES:
        rows = _rows(key, since=since - 60 * 86400000)
        rows = [r for r in rows if target(main, r[2]) >= since]
        t = _tally([(r[3], r[4], _info(r[5]), r[7 + HKEYS.index(main)]) for r in rows], main)
        if t['n'] >= 5:
            res.append((t['pct'], t['n'], name, HNAME[main]))
    if not res:
        return None
    res.sort()
    best, worst = res[-1], res[0]
    lines = [f'Best: {best[2]}, right {best[0]}% of {best[1]} calls ({best[3]}).']
    if worst is not best:
        lines.append(f'Worst: {worst[2]}, right {worst[0]}% of {worst[1]} calls ({worst[3]}).')
    lines.append('50% = a coin flip. Full Report Card on the board.')
    return ' '.join(lines)


def _weekly_push(now):
    d = watch.et(now)
    if d.weekday() != 4 or d.hour != 20 or d.minute < 30 or not settings()['weekly_push']:
        return
    k = d.date().isoformat()
    s = watch._read(SETTINGS, {})
    if s.get('pushed') == k:
        return
    txt = week_text(now)
    if txt:
        watch.push('Board Report Card: this week', txt, tags='bar_chart')
    s['pushed'] = k
    watch._write(SETTINGS, s)


# ------------------------------------------------------------------ loop
def _replay_now():
    if _st['replay_running']:
        return
    _st['replay_running'] = True
    try:
        import rc_replay
        rc_replay.run()
        _st['errors'].pop('replay', None)
    except Exception as e:
        traceback.print_exc()
        _err('replay', e)
    finally:
        _st['replay_running'] = False


def _loop():
    time.sleep(90)
    while True:
        now = _now()
        try:
            record_all()
        except Exception as e:
            _err('record', e)
        if time.time() - _st['graded_at'] > 25 * 60:
            try:
                grade()
            except Exception as e:
                _err('grade', e)
                _st['graded_at'] = time.time()
        try:
            _weekly_push(now)
        except Exception as e:
            _err('push', e)
        rp = replay()
        d = watch.et(now)
        if not _st['replay_running'] and (not rp.get('at') or (now - rp['at'] > 20 * 3600000 and d.hour >= 17 and d.hour < 23)):
            threading.Thread(target=_replay_now, name='rc-replay', daemon=True).start()
        time.sleep(300)


def start():
    if _st['started']:
        return
    _st['started'] = True
    try:
        _db().close()
    except Exception as e:
        _err('save', e)
    threading.Thread(target=_loop, name='reportcard', daemon=True).start()


# ------------------------------------------------------------------ doorway
def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return v or ''


def get(sub, query):
    if sub in ('', 'status'):
        return 200, summary()
    if sub == 'feature':
        s = _q(query, 'sub')
        return 200, feature(_q(query, 'key'), s if (query or {}).get('sub') is not None and s != '' else None)
    if sub == 'badges':
        return 200, badges()
    if sub == 'trades':
        try:
            return 200, trades()
        except Exception as e:
            return 200, {'error': f'Journal not readable: {e}'[:200]}
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'settings':
        return 200, save_settings(data)
    if sub == 'replay':
        threading.Thread(target=_replay_now, name='rc-replay', daemon=True).start()
        return 200, {'started': True}
    return 404, {'error': 'unknown'}
