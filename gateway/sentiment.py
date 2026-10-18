"""Market sentiment report (jb-sentiment), on the MACRO tab, running inside the gateway.

Press "Generate report" on the MACRO tab: one combined report on the stock market's mood over three
time frames, made by fixed rules from free data the board already collects (nothing new is fetched,
except the S&P / Nasdaq daily prices if no other tab has loaded them yet):
    short term  (next 1-3 days)    overnight mood meter, VIX and its move, VIX curve, last 5 days,
                                   today's breadth, the last 2 days of macro headlines
    mid term    (next 2-8 weeks)   S&P / Nasdaq vs 50-day average, last 20 days, Nasdaq-100 breadth,
                                   Fed meeting odds (Kalshi), futures positioning (CFTC), a week of headlines,
                                   net liquidity over 4 weeks
    long term   (next 3-12 months) the MACRO score (FRED), S&P vs 200-day average, 50-day vs 200-day,
                                   where the S&P sits in its 52-week range, last 12 months, net liquidity over 6 months
Each item scores -1 (bearish) .. +1 (bullish); a time frame's score is their average x 100, and the
combined score is the three time frames averaged with equal weight. Rules, not advice.
    /api/plus/sentiment            GET: the last report and the list of earlier ones
    /api/plus/sentiment/generate   POST: makes a new report now (kept in data/sentiment.json, last 60)
Nothing here places trades or sends pushes.
"""
import math
import os
import threading
import time

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
STORE = os.path.join(DATA, 'sentiment.json')
KEEP = 60

_lock = threading.Lock()

HORIZONS = [('short', 'Short term', 'next 1-3 days'), ('mid', 'Mid term', 'next 2-8 weeks'), ('long', 'Long term', 'next 3-12 months')]


def _now():
    return int(time.time() * 1000)


def _clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _f(v, d=1, plus=True):
    if v is None:
        return '-'
    s = f'{v:.{d}f}'
    return ('+' + s) if plus and v > 0 else s


def label(score):
    if score is None:
        return 'Not enough data'
    return ('Bullish' if score >= 40 else 'Leaning bullish' if score >= 15 else 'Neutral' if score > -15
            else 'Leaning bearish' if score > -40 else 'Bearish')


# ------------------------------------------------------------------ data the board already has
def _mod(name):
    try:
        return __import__(name)
    except Exception:
        return None


def _closes(sym):
    """Daily closes, oldest first: the Night desk's copy, else the Analysis tab's (fetched once if neither has it)."""
    rows = []
    e = _mod('edge')
    if e:
        try:
            rows = e.daily(sym) or []
        except Exception:
            rows = []
    a = _mod('analysis')
    if not rows and a:
        try:
            rows = a._cached_daily(sym) or []
            if not rows:
                rows = a.daily(sym) or []
        except Exception:
            rows = []
    return [(r[0], r[4]) for r in rows if r and r[4]]


def _quote(sym):
    w = _mod('world')
    if not w:
        return {}
    try:
        q = w._quotes.get(sym) or {}
    except Exception:
        return {}
    if q and _now() - (q.get('last_ms') or 0) > 4 * 86400000:
        return {}
    return q


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


def _ma(c, n):
    return _avg([x for _, x in c[-n:]]) if len(c) >= n else None


def _chg(c, n):
    return (c[-1][1] / c[-1 - n][1] - 1) * 100 if len(c) > n else None


def _edge():
    e = _mod('edge')
    if not e:
        return None, {}
    try:
        if not e._c:
            e._load()
        return e, e._c
    except Exception:
        return e, {}


def _macro_snapshot():
    m = _mod('macro')
    if not m:
        return None
    try:
        return m.snapshot()
    except Exception:
        return None


# ------------------------------------------------------------------ the three time frames
def _item(name, s, value, reading):
    return {'name': name, 's': round(_clamp(s), 2), 'value': value, 'reading': reading}


def short_term(spy, qqq, msnap):
    out, missing = [], []
    w = _mod('world')
    mood = None
    try:
        mood = w.mood() if w else None
    except Exception:
        mood = None
    if mood:
        out.append(_item('Overnight mood meter', mood['score'] / 100, f"{mood['score']:+d} ({mood['label']})",
                         'Futures, Asia, Europe, bitcoin, the VIX, the dollar and gold since the 4 pm close, from the World tab. ' +
                         ('Money is leaning into risk.' if mood['score'] >= 25 else 'Money is leaning away from risk.' if mood['score'] <= -25 else 'No clear lean.')))
    else:
        missing.append('Overnight mood meter (World tab prices not loaded yet)')
    vq = _quote('^VIX')
    v = vq.get('last')
    if v:
        out.append(_item('VIX (fear index) level', (20 - v) / 8, f'{v:.1f}',
                         'Calm: swings should stay small.' if v < 16 else 'Nervous: expect bigger swings, which hit 3x funds hardest.' if v > 25 else 'Normal nerves.'))
        sc = vq.get('since_close')
        if sc is not None:
            out.append(_item('VIX since the 4 pm close', -math.tanh(sc / 6), _f(sc) + '%',
                             'Fear is rising.' if sc > 3 else 'Fear is easing.' if sc < -3 else 'Little change in fear.'))
    else:
        missing.append('VIX (World tab prices not loaded yet)')
    e, c = _edge()
    curve = None
    try:
        curve = e.vix_curve() if e else None
    except Exception:
        curve = None
    if curve:
        r = curve['ratio']
        s = 0.5 if r < 0.9 else 0.2 if r < 0.95 else -0.4 if r < 1 else -1.0
        out.append(_item('VIX curve (now vs 3 months)', s, f"{r:.2f}", curve['text']))
    for sym, cl in (('S&P 500 (SPY)', spy), ('Nasdaq 100 (QQQ)', qqq)):
        ch = _chg(cl, 5)
        if ch is not None:
            out.append(_item(f'{sym} last 5 days', math.tanh(ch / 2.5), _f(ch) + '%',
                             'Short-term push is up.' if ch > 0.5 else 'Short-term push is down.' if ch < -0.5 else 'Going sideways.'))
    b = None
    try:
        b = e.breadth() if e else None
    except Exception:
        b = None
    if b and b.get('n'):
        s = (b['up'] - b['down']) / b['n']
        out.append(_item('Breadth today (Nasdaq-100)', s * 1.5, f"{b['up']} up / {b['down']} down",
                         'Most stocks rose: a broad move.' if s > 0.2 else 'Most stocks fell.' if s < -0.2 else 'Split: about as many up as down.'))
    if msnap:
        cut = _now() / 1000 - 2 * 86400
        recent = [i['s'] for i in (msnap.get('news') or []) if (i.get('ts') or 0) >= cut]
        if len(recent) >= 3:
            a = sum(recent) / len(recent)
            out.append(_item('Macro headlines, last 2 days', a / 0.5, f'{len(recent)} headlines, average {a:+.2f}',
                             'Mostly upbeat words.' if a > 0.15 else 'Mostly worried words.' if a < -0.15 else 'Mixed words.'))
    return out, missing


def mid_term(spy, qqq, msnap):
    out, missing = [], []
    for sym, cl in (('S&P 500', spy), ('Nasdaq 100', qqq)):
        m50 = _ma(cl, 50)
        if m50:
            p = (cl[-1][1] / m50 - 1) * 100
            out.append(_item(f'{sym} vs its 50-day average', p / 5, _f(p) + '%',
                             'Above: the trend of recent weeks is up.' if p > 0 else 'Below: the trend of recent weeks is down.'))
        else:
            missing.append(f'{sym} daily prices (Yahoo, loads in the background)')
    ch = _chg(spy, 20)
    if ch is not None:
        out.append(_item('S&P 500 last 20 trading days', math.tanh(ch / 5), _f(ch) + '%',
                         'A good month.' if ch > 2 else 'A poor month.' if ch < -2 else 'A flat month.'))
    if len(spy) >= 61:
        m_now = _ma(spy, 50)
        m_then = _avg([x for _, x in spy[-60:-10]])
        if m_now and m_then:
            sl = (m_now / m_then - 1) * 100
            out.append(_item('50-day average slope (2 weeks)', sl / 1.5, _f(sl, 2) + '%',
                             'The average itself is rising.' if sl > 0 else 'The average itself is falling.'))
    e, c = _edge()
    b = None
    try:
        b = e.breadth() if e else None
    except Exception:
        b = None
    if b and b.get('n50'):
        pc = b['above50'] / b['n50'] * 100
        out.append(_item('Nasdaq-100 stocks above their 50-day average', (pc - 50) / 30, f'{pc:.0f}%',
                         'Most stocks are in uptrends: a healthy market.' if pc >= 60 else 'Most stocks are in downtrends: a weak market under the surface.' if pc <= 40 else 'About half: mixed.'))
    odds = (c or {}).get('odds') or {}
    mt = (odds.get('meetings') or [None])[0] if odds.get('meetings') else None
    if mt and mt.get('bucket'):
        bk = mt['bucket']
        s = (bk.get('cut', 0) - bk.get('hike', 0)) / 100
        out.append(_item('Fed odds, next meeting (Kalshi)', s * 0.8, f"cut {bk.get('cut', 0)}% / hold {bk.get('hold', 0)}% / hike {bk.get('hike', 0)}%",
                         'Traders expect a rate cut: easier money.' if s > 0.5 else 'Traders see a hike risk: tighter money.' if s < -0.1 else 'Traders expect rates to stay put.'))
    for r in ((c or {}).get('cftc') or {}).get('rows') or []:
        if r.get('name') in ('Nasdaq-100 futures', 'S&P 500 futures') and r.get('rank') is not None:
            rk = r['rank']
            s = -0.5 if rk >= 90 else 0.5 if rk <= 10 else 0.0
            out.append(_item(f"Hedge funds in {r['name']} (CFTC)", s, f'{rk}{"th" if 10 <= rk % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(rk % 10, "th")} percentile (3 years)',
                             'Very crowded long: little fuel left, pullbacks can be sharp.' if rk >= 90 else
                             'Very crowded short: room for a squeeze higher.' if rk <= 10 else 'Not crowded either way.'))
    if msnap and (msnap.get('news_mood') or {}).get('n'):
        nm = msnap['news_mood']
        out.append(_item('Macro headlines, last 7 days', nm['avg'] / 0.5, f"{nm['label']} ({nm['n']} headlines)",
                         'Fed, inflation, jobs and growth news has been ' + nm['label'].lower() + '.'))
    nl = ((c or {}).get('netliq') or {}).get('rows') or []
    if len(nl) >= 5:
        a, z = nl[-5][1], nl[-1][1]
        if a:
            p = (z / a - 1) * 100
            out.append(_item('Net liquidity, last 4 weeks (FRED)', p / 2, _f(p) + '%',
                             'More money sloshing in the system: supports stocks.' if p > 0.3 else 'Money draining from the system.' if p < -0.3 else 'Steady.'))
    return out, missing


def long_term(spy, msnap):
    out, missing = [], []
    o = (msnap or {}).get('overall')
    if o:
        out.append(_item('MACRO score (FRED, this tab)', o['score'] / 60, f"{o['score']:+d} ({o['label']})",
                         'Rates, inflation, jobs, growth and credit together are a ' + o['label'].lower() + ' for stocks.' if o['label'] != 'Mixed'
                         else 'Rates, inflation, jobs, growth and credit are mixed for stocks.'))
    else:
        missing.append('MACRO score (FRED data still loading)')
    m200, m50 = _ma(spy, 200), _ma(spy, 50)
    if m200:
        p = (spy[-1][1] / m200 - 1) * 100
        out.append(_item('S&P 500 vs its 200-day average', p / 8, _f(p) + '%',
                         'Above: the long-term trend is up.' if p > 0 else 'Below: the long-term trend is down.'))
        if m50:
            g = (m50 / m200 - 1) * 100
            out.append(_item('50-day vs 200-day average', (0.5 if g > 0 else -0.5) + g / 10, _f(g) + '%',
                             '50-day above the 200-day ("golden cross" side): a bull-market setup.' if g > 0 else
                             '50-day below the 200-day ("death cross" side): a bear-market setup.'))
    else:
        missing.append('S&P 500 one year of daily prices (Yahoo)')
    if len(spy) >= 200:
        yr = [x for _, x in spy[-252:]]
        lo, hi = min(yr), max(yr)
        if hi > lo:
            pos = (spy[-1][1] - lo) / (hi - lo)
            out.append(_item('Where the S&P sits in its 52-week range', (pos - 0.5) * 1.6, f'{pos * 100:.0f}% of the way up',
                             'Near its highs.' if pos > 0.85 else 'Near its lows.' if pos < 0.15 else 'In the middle of its year.'))
        ch = (spy[-1][1] / spy[-min(len(spy), 252)][1] - 1) * 100
        out.append(_item('S&P 500 last 12 months', math.tanh(ch / 15), _f(ch) + '%',
                         'A strong year.' if ch > 10 else 'A losing year.' if ch < 0 else 'A modest year.'))
    e, c = _edge()
    nl = ((c or {}).get('netliq') or {}).get('rows') or []
    if len(nl) >= 27:
        a, z = nl[-27][1], nl[-1][1]
        if a:
            p = (z / a - 1) * 100
            out.append(_item('Net liquidity, last 6 months (FRED)', p / 5, _f(p) + '%',
                             'The Fed and Treasury have been adding money overall.' if p > 0.5 else 'Money has been draining overall.' if p < -0.5 else 'Roughly unchanged.'))
    return out, missing


# ------------------------------------------------------------------ the words
def _score(items):
    return int(round(sum(i['s'] for i in items) / len(items) * 100)) if len(items) >= 2 else None


def _story(sc):
    s, m, l = sc.get('short'), sc.get('mid'), sc.get('long')
    sign = lambda v: None if v is None else 1 if v >= 15 else -1 if v <= -15 else 0
    S, M, L = sign(s), sign(m), sign(l)
    if None in (S, M, L):
        return 'Some time frames are missing data, so read the combined score with care.'
    if S == M == L == 1:
        return 'All three time frames point up: the market is in a healthy uptrend with nothing obvious pushing against it.'
    if S == M == L == -1:
        return 'All three time frames point down: a weak market across the board. Rallies may not last.'
    if L == 1 and S == -1:
        return 'A short-term dip inside a longer uptrend: the kind of pullback that has often been bought, unless the mid term turns down too.'
    if L == -1 and S == 1:
        return 'A short-term bounce inside a longer downtrend: bounces like this often fade, so be careful chasing it.'
    if L == 1 and M == -1:
        return 'The long-term trend is still up, but the last few weeks have weakened: a correction is under way.'
    if L == -1 and M == 1:
        return 'Recent weeks have improved against a weak long-term backdrop: a possible turn, not yet confirmed.'
    if S == 0 and M == 0 and L == 0:
        return 'No clear direction in any time frame: a choppy market.'
    lead = max((('short', s), ('mid', m), ('long', l)), key=lambda x: abs(x[1]))
    return f'Mixed picture; the strongest lean is the {dict(short="short term", mid="mid term", long="long term")[lead[0]]} ({lead[1]:+d}).'


def _letf(sc, vix):
    s = sc.get('short') or 0
    comb = sc.get('combined') or 0
    calm = vix is not None and vix < 18
    wild = vix is not None and vix > 25
    if wild:
        t = 'The VIX is high: 3x funds lose value fastest in big daily swings (decay), whichever way they point. Smaller sizes and tighter plans suit this.'
    elif comb >= 15 and s >= 0:
        t = 'The backdrop favours the 3x bull side (TQQQ, SOXL, SPXL)' + (' and calm markets keep decay low.' if calm else '.')
    elif comb <= -15 and s <= 0:
        t = 'The backdrop favours the 3x inverse side (SQQQ, SOXS, SPXS), but inverse funds decay too, so they suit short holds.'
    else:
        t = 'No strong edge for either side: choppy markets eat into 3x funds from both directions (decay).'
    return t + ' Rules, not advice.'


def _events():
    w = _mod('world')
    if not w:
        return []
    now = _now()
    try:
        with w._lock:
            evs = list(w._cal['events'])
    except Exception:
        return []
    out = []
    for e in evs:
        if now - 3600000 <= e['t'] <= now + 3 * 86400000 and (e.get('impact') == 'High' or e.get('kind') == 'fed'):
            out.append({'t': e['t'], 'title': e['title'], 'when': watch.et(e['t']).strftime('%a %-I:%M %p ET')})
    return out[:8]


def generate():
    now = _now()
    spy, qqq = _closes('SPY'), _closes('QQQ')
    msnap = _macro_snapshot()
    parts = {}
    a, ma = short_term(spy, qqq, msnap)
    b, mb = mid_term(spy, qqq, msnap)
    c, mc = long_term(spy, msnap)
    sc = {}
    for key, items, miss in (('short', a, ma), ('mid', b, mb), ('long', c, mc)):
        s = _score(items)
        sc[key] = s
        parts[key] = {'score': s, 'label': label(s), 'items': items, 'missing': miss}
    have = [sc[k] for k in ('short', 'mid', 'long') if sc[k] is not None]
    sc['combined'] = int(round(sum(have) / len(have))) if len(have) >= 2 else None
    vix = _quote('^VIX').get('last')
    d = watch.et(now)
    rep = {'at': now, 'when': d.strftime('%a %b %-d, %-I:%M %p ET'), 'combined': sc['combined'], 'label': label(sc['combined']),
           'horizons': parts, 'story': _story(sc), 'letf': _letf(sc, vix), 'events': _events(),
           'prices_as_of': spy[-1][0] if spy else None}
    with _lock:
        st = watch._read(STORE, {'reports': []})
        prev = st['reports'][-1] if st.get('reports') else None
        if prev:
            rep['change'] = {k: (None if (sc.get(k) is None or (prev.get('scores') or {}).get(k) is None) else sc[k] - prev['scores'][k])
                             for k in ('short', 'mid', 'long', 'combined')}
            rep['change_from'] = prev.get('when')
        st['last'] = rep
        st['reports'] = (st.get('reports') or []) + [{'at': now, 'when': rep['when'], 'scores': {k: sc[k] for k in ('short', 'mid', 'long', 'combined')}}]
        st['reports'] = st['reports'][-KEEP:]
        watch._write(STORE, st)
    rep['text'] = text(rep)
    return rep


def text(rep):
    L = [f"MARKET SENTIMENT REPORT - {rep['when']}",
         f"Combined: {_f(rep['combined'], 0) if rep['combined'] is not None else '-'} ({rep['label']})", rep['story'], '']
    for key, name, span in HORIZONS:
        h = rep['horizons'][key]
        L.append(f"{name.upper()} ({span}): {_f(h['score'], 0) if h['score'] is not None else '-'} ({h['label']})")
        for i in h['items']:
            L.append(f"  {i['s']:+.2f}  {i['name']}: {i['value']}. {i['reading']}")
        for m in h['missing']:
            L.append(f'  (not available: {m})')
        L.append('')
    L.append('3x funds: ' + rep['letf'])
    if rep.get('events'):
        L.append('Big events next 3 days: ' + '; '.join(f"{e['when']} {e['title']}" for e in rep['events']))
    L.append('Scores: each item -1 (bearish) to +1 (bullish) by fixed rules; a time frame = their average x 100; combined = the three averaged.')
    return '\n'.join(L)


def summary_text():
    """For the Ask Claude page."""
    st = watch._read(STORE, {})
    rep = st.get('last')
    return text(rep) if rep else 'No sentiment report made yet (MACRO tab, Generate report).'


# ------------------------------------------------------------------ doorway
def get(sub, query):
    if sub in ('', 'status'):
        st = watch._read(STORE, {})
        rep = st.get('last')
        if rep:
            rep['text'] = text(rep)
        return 200, {'last': rep, 'reports': list(reversed(st.get('reports') or []))[:20]}
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'generate':
        try:
            return 200, generate()
        except Exception as e:
            return 200, {'error': f'Could not make the report: {str(e)[:200]}'}
    return 404, {'error': 'unknown'}
