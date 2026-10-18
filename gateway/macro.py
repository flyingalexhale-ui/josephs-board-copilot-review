"""MACRO tab (jb-macro), running inside the gateway: the long-term economic backdrop for stocks, and
macro news with a simple mood score. All free, no keys.

  * Indicators: about 16 US numbers from FRED (the St. Louis Fed's public data, downloaded as CSV):
    Fed funds rate, inflation (CPI, core PCE), jobs (payrolls, unemployment, jobless claims), the
    yield curve, 10-year yield, credit spreads, financial conditions, consumer sentiment,
    industrial production, GDP, money supply, VIX, plus the S&P 500 vs its 200-day average (Yahoo).
    Each gets a plain-words reading and a score from -1 (headwind for stocks) to +1 (tailwind),
    by simple fixed rules (written below), and a small line chart.
  * Macro score: the average of those scores, -100 .. +100, kept once a day so its own history
    can be drawn (data/macro_history.json).
  * Macro news: headlines about the Fed, inflation, jobs, growth and rates (Google News and the
    Fed's own press releases), each scored by counting positive / negative words. Rough by design.
Refreshed slowly: FRED every 6 hours (one series every 3 s), news every 30 minutes.
Files: data/macro_cache.json, macro_history.json.
"""
import csv
import io
import math
import os
import threading
import time
import urllib.request
from datetime import date, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'macro_cache.json')
HIST = os.path.join(DATA, 'macro_history.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
FRED = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={}&cosd={}'

# id, name, group, how the raw series is turned into the number shown ('level', 'yoy', 'chg', 'ann'),
# unit, how often FRED updates it
SERIES = [
    ('DFF', 'Fed funds rate', 'Rates', 'level', '%', 'daily'),
    ('DGS10', '10-year Treasury yield', 'Rates', 'level', '%', 'daily'),
    ('T10Y2Y', 'Yield curve (10-year minus 2-year)', 'Rates', 'level', 'pts', 'daily'),
    ('CPIAUCSL', 'Inflation (CPI, vs a year ago)', 'Inflation', 'yoy', '%', 'monthly'),
    ('PCEPILFE', 'Core PCE inflation (the Fed\'s gauge)', 'Inflation', 'yoy', '%', 'monthly'),
    ('T10YIE', 'Expected inflation (10-year)', 'Inflation', 'level', '%', 'daily'),
    ('PAYEMS', 'Jobs added per month (3-month average)', 'Jobs', 'chg', 'k', 'monthly'),
    ('UNRATE', 'Unemployment rate', 'Jobs', 'level', '%', 'monthly'),
    ('ICSA', 'New jobless claims (weekly)', 'Jobs', 'level', 'k', 'weekly'),
    ('GDPC1', 'Economic growth (GDP, yearly pace)', 'Growth', 'ann', '%', 'quarterly'),
    ('INDPRO', 'Industrial production (vs a year ago)', 'Growth', 'yoy', '%', 'monthly'),
    ('UMCSENT', 'Consumer sentiment (U. Michigan)', 'Growth', 'level', '', 'monthly'),
    ('BAMLH0A0HYM2', 'Junk-bond spread (credit stress)', 'Markets', 'level', '%', 'daily'),
    ('NFCI', 'Financial conditions (Chicago Fed)', 'Markets', 'level', '', 'weekly'),
    ('M2SL', 'Money supply M2 (vs a year ago)', 'Markets', 'yoy', '%', 'monthly'),
    ('VIXCLS', 'VIX (fear gauge)', 'Markets', 'level', '', 'daily'),
]
NEWS_QUERIES = ['Federal Reserve interest rates', 'US inflation CPI', 'US jobs report unemployment',
                'US economy recession', 'Treasury yields bond market']
FED_RSS = 'https://www.federalreserve.gov/feeds/press_all.xml'
POS = ('cool', 'cooling', 'ease', 'eases', 'easing', 'cut', 'cuts', 'beat', 'beats', 'strong', 'stronger', 'growth', 'grows',
       'rally', 'rallies', 'gain', 'gains', 'surge', 'surges', 'rebound', 'optimism', 'upbeat', 'record high', 'soft landing',
       'slows inflation', 'improve', 'improves', 'boost', 'expands', 'resilient', 'robust', 'eased', 'calm')
NEG = ('recession', 'slowdown', 'slump', 'layoff', 'layoffs', 'hike', 'hikes', 'sticky', 'hot inflation', 'hotter', 'miss', 'misses',
       'weak', 'weaker', 'fall', 'falls', 'drop', 'drops', 'plunge', 'plunges', 'selloff', 'sell-off', 'fear', 'fears', 'warn',
       'warns', 'crisis', 'default', 'shutdown', 'tariff', 'tariffs', 'contraction', 'shrinks', 'stagflation', 'downgrade', 'worst', 'jump in unemployment')

_lock = threading.Lock()
_st = {'started': False, 'raw': {}, 'news': [], 'news_at': 0, 'errors': {}, 'spy': None}


def _now():
    return int(time.time() * 1000)


def _clamp(x):
    return max(-1.0, min(1.0, x))


def _http(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/csv,application/xml,*/*'})
    with urllib.request.urlopen(req, timeout=timeout, context=watch._ctx()) as r:
        return r.read(3_000_000).decode('utf-8', 'replace')


def parse_fred(text):
    out = []
    rows = csv.reader(io.StringIO(text))
    next(rows, None)
    for r in rows:
        if len(r) >= 2:
            try:
                out.append([r[0], float(r[1])])
            except ValueError:
                pass                          # "." = no value that day
    return out


def fetch_series(sid):
    start = (date.today() - timedelta(days=4 * 365)).isoformat()
    url = FRED.format(sid, start)
    try:                                   # FRED never answers plain Python (times out): ask like Chrome does
        import world
        if world._browser is not None:
            r = world._browser.Session(impersonate='chrome').get(url, timeout=25)
            if r.status_code < 400:
                return parse_fred(r.text)
    except Exception:
        pass
    return parse_fred(_http(url))


# ------------------------------------------------------------------ turning raw series into readings
def transform(rows, how, freq):
    """-> [[date, value]] of the number shown."""
    if how == 'level':
        if freq == 'daily':                   # one point a week is plenty for the chart
            out, seen = [], set()
            for d, v in rows:
                wk = date.fromisoformat(d).isocalendar()[:2]
                if wk in seen:
                    out[-1] = [d, v]
                else:
                    seen.add(wk)
                    out.append([d, v])
            return out
        if freq == 'weekly' and rows and rows[-1][1] > 1000:     # jobless claims: thousands
            return [[d, round(v / 1000, 1)] for d, v in rows]
        return rows
    if how == 'yoy':
        step = 12 if freq == 'monthly' else 4
        return [[rows[i][0], round((rows[i][1] / rows[i - step][1] - 1) * 100, 2)] for i in range(step, len(rows)) if rows[i - step][1]]
    if how == 'chg':                          # payrolls: monthly change in thousands, 3-month average
        ch = [rows[i][1] - rows[i - 1][1] for i in range(1, len(rows))]
        return [[rows[i + 1][0], round(sum(ch[i - 2:i + 1]) / 3, 0)] for i in range(2, len(ch))]
    if how == 'ann':                          # GDP: quarter vs quarter, at a yearly pace
        return [[rows[i][0], round(((rows[i][1] / rows[i - 1][1]) ** 4 - 1) * 100, 1)] for i in range(1, len(rows)) if rows[i - 1][1]]
    return rows


def _ago(pts, days):
    """Value about `days` days before the last point."""
    if not pts:
        return None
    cut = (date.fromisoformat(pts[-1][0]) - timedelta(days=days)).isoformat()
    older = [v for d, v in pts if d <= cut]
    return older[-1] if older else None


def score(sid, pts):
    """(score -1..+1, reading in plain words). Fixed rules; + = good for stocks."""
    v = pts[-1][1]
    m3, m6, y1 = _ago(pts, 91), _ago(pts, 182), _ago(pts, 365)
    d6 = v - m6 if m6 is not None else 0
    d3 = v - m3 if m3 is not None else 0
    if sid == 'DFF':
        s = _clamp(-d6 / 1.0)
        return s, ('The Fed has been cutting: easier money helps stocks.' if d6 < -0.2 else 'The Fed has been raising rates: tighter money is a headwind.' if d6 > 0.2 else 'Rates are on hold.')
    if sid == 'DGS10':
        s = _clamp(-d3 / 0.75)
        return s, ('Long-term rates have risen fast, which pressures stock prices (tech most).' if d3 > 0.3 else 'Long-term rates have fallen, which helps stock prices.' if d3 < -0.3 else 'Long-term rates are steady.')
    if sid == 'T10Y2Y':
        s = _clamp(v / 1.0)
        return s, ('Inverted (short rates above long): historically a recession warning, often a year or more ahead.' if v < 0 else
                   'Normal, upward sloping.' if v > 0.5 else 'Nearly flat. Note: recessions have often started soon after an inversion ends.')
    if sid == 'CPIAUCSL':
        s = _clamp(0.5 * (3 - v) / 1.5 + 0.5 * (-d6) / 1.0)
        return s, f"Inflation is {v:.1f}% a year, {'falling' if d6 < -0.2 else 'rising' if d6 > 0.2 else 'flat'} over 6 months (the Fed targets 2%)."
    if sid == 'PCEPILFE':
        s = _clamp(0.5 * (2.6 - v) / 1.0 + 0.5 * (-d6) / 0.8)
        return s, f"The Fed's preferred inflation measure is {v:.1f}% ({'cooling' if d6 < -0.15 else 'heating up' if d6 > 0.15 else 'steady'}); 2% is the goal."
    if sid == 'T10YIE':
        s = _clamp((2.5 - v) / 0.6)
        return s, ('Bond traders expect inflation near the Fed\'s goal.' if v < 2.5 else 'Bond traders expect inflation above the Fed\'s goal.')
    if sid == 'PAYEMS':
        s = _clamp((v - 100) / 150)
        return s, ('Hiring is strong.' if v > 200 else 'Hiring is healthy.' if v > 100 else 'Hiring is slow.' if v > 0 else 'The economy is losing jobs.')
    if sid == 'UNRATE':
        lo = min(x for d, x in pts if d >= (date.fromisoformat(pts[-1][0]) - timedelta(days=365)).isoformat())
        rise = v - lo
        s = _clamp((0.25 - rise) / 0.5)
        return s, (f'Unemployment is {rise:.1f} points above its 12-month low: a rise of 0.5 or more has come with every recession since 1970 (the "Sahm rule").' if rise >= 0.3
                   else f'Unemployment is {v:.1f}%, near its recent low.')
    if sid == 'ICSA':
        ch = (v / m6 - 1) * 100 if m6 else 0
        s = _clamp(-ch / 20)
        return s, ('More people are filing for unemployment than 6 months ago.' if ch > 10 else 'Layoffs are low and steady.' if ch < 10 else '')
    if sid == 'GDPC1':
        s = _clamp((v - 1) / 2)
        return s, (f'The economy grew at a {v:.1f}% yearly pace last quarter.' if v >= 0 else f'The economy shrank ({v:.1f}% yearly pace) last quarter.')
    if sid == 'INDPRO':
        s = _clamp(v / 3)
        return s, ('Factories are producing more than a year ago.' if v > 0 else 'Factory output is below a year ago.')
    if sid == 'UMCSENT':
        s = _clamp(d6 / 10)
        return s, ('Consumers feel better than 6 months ago.' if d6 > 3 else 'Consumers feel worse than 6 months ago.' if d6 < -3 else 'Consumer mood is steady.') + ' (Long-run average about 85.)'
    if sid == 'BAMLH0A0HYM2':
        s = _clamp(0.5 * (4.5 - v) / 2 + 0.5 * (-d3) / 1.0)
        return s, ('Lenders are relaxed about risky companies.' if v < 4 else 'Lenders are nervous: risky companies pay a lot more to borrow.' if v > 5.5 else 'Credit stress is moderate.')
    if sid == 'NFCI':
        s = _clamp(-v / 0.5)
        return s, ('Money is easy to get (loose conditions).' if v < 0 else 'Money is harder to get than usual (tight conditions).')
    if sid == 'M2SL':
        s = _clamp(v / 6)
        return s, ('The money supply is growing, adding fuel to markets.' if v > 2 else 'The money supply is shrinking, a drain on markets.' if v < 0 else 'The money supply is barely growing.')
    if sid == 'VIXCLS':
        s = _clamp((20 - v) / 10)
        return s, ('Markets are calm.' if v < 16 else 'Markets are nervous.' if v > 25 else 'Normal nerves.')
    return 0.0, ''


def spy_trend():
    """S&P 500 (SPY) vs its 200-day average, from Yahoo daily bars."""
    try:
        import analysis
        rows = analysis.daily('SPY')
    except Exception:
        return None
    closes = [r[4] for r in rows if r[4]]
    if len(closes) < 200:
        return None
    ma = sum(closes[-200:]) / 200
    pct = (closes[-1] / ma - 1) * 100
    pts = []
    for i in range(200, len(closes), 5):
        m = sum(closes[i - 200:i]) / 200
        pts.append([rows[i][0], round((closes[i] / m - 1) * 100, 1)])
    s = _clamp(pct / 8)
    return {'id': 'SPY200', 'name': 'S&P 500 vs its 200-day average', 'group': 'Markets', 'unit': '%', 'freq': 'daily',
            'value': round(pct, 1), 'date': rows[-1][0], 's': round(s, 2), 'spark': pts[-40:],
            'reading': ('Above its long-term average: the long-term trend is up.' if pct > 0 else 'Below its long-term average: the long-term trend is down.')}


def indicators():
    out = []
    with _lock:
        raw = dict(_st['raw'])
    for sid, name, group, how, unit, freq in SERIES:
        rows = (raw.get(sid) or {}).get('rows') or []
        pts = transform(rows, how, freq) if rows else []
        if not pts:
            out.append({'id': sid, 'name': name, 'group': group, 'unit': unit, 'freq': freq, 'value': None, 'missing': True})
            continue
        s, reading = score(sid, pts)
        out.append({'id': sid, 'name': name, 'group': group, 'unit': unit, 'freq': freq, 'value': pts[-1][1], 'date': pts[-1][0],
                    's': round(s, 2), 'reading': reading, 'spark': pts[-40:] if freq != 'monthly' else pts[-36:]})
    t = _st.get('spy')
    if t:
        out.append(t)
    return out


def overall(inds):
    sc = [i['s'] for i in inds if i.get('s') is not None]
    if len(sc) < 4:
        return None
    v = int(round(sum(sc) / len(sc) * 100))
    groups = {}
    for i in inds:
        if i.get('s') is not None:
            groups.setdefault(i['group'], []).append(i['s'])
    return {'score': v, 'label': 'Tailwind' if v >= 20 else 'Headwind' if v <= -20 else 'Mixed', 'n': len(sc),
            'groups': {g: int(round(sum(x) / len(x) * 100)) for g, x in groups.items()}}


# ------------------------------------------------------------------ news
def headline_score(t):
    t = ' ' + t.lower() + ' '
    p = sum(1 for w in POS if ' ' + w + ' ' in t or ' ' + w + ',' in t)
    n = sum(1 for w in NEG if ' ' + w + ' ' in t or ' ' + w + ',' in t)
    return p - n


def refresh_news():
    import news
    items, seen = [], set()
    for q in NEWS_QUERIES:
        try:
            for i in news._google(q):
                k = news._norm(i['title'])
                if k not in seen:
                    seen.add(k)
                    items.append(dict(i, topic=q.split(' ')[1] if q.startswith('US ') else q.split(' ')[0]))
        except Exception as e:
            with _lock:
                _st['errors']['news ' + q] = str(e)[:120]
        time.sleep(3)
    try:
        for i in news._parse_rss(news._get(FED_RSS), 'Federal Reserve'):
            items.append(dict(i, topic='Fed', source='Federal Reserve'))
    except Exception as e:
        with _lock:
            _st['errors']['fed'] = str(e)[:120]
    cut = time.time() - 7 * 86400
    items = [dict(i, s=headline_score(i['title'])) for i in items if i['ts'] >= cut]
    items.sort(key=lambda i: -i['ts'])
    with _lock:
        _st['news'], _st['news_at'] = items[:150], _now()


def news_mood(items):
    days = {}
    for i in items:
        d = watch.et(i['ts'] * 1000).date().isoformat()
        days.setdefault(d, []).append(i['s'])
    out = [{'day': d, 'avg': round(sum(v) / len(v), 2), 'n': len(v), 'pos': sum(1 for x in v if x > 0), 'neg': sum(1 for x in v if x < 0)}
           for d, v in sorted(days.items())]
    all_ = [i['s'] for i in items]
    avg = sum(all_) / len(all_) if all_ else 0
    return {'days': out, 'avg': round(avg, 2), 'label': 'Upbeat' if avg > 0.15 else 'Gloomy' if avg < -0.15 else 'Mixed', 'n': len(all_)}


# ------------------------------------------------------------------ background
def _save():
    with _lock:
        watch._write(CACHE, {'raw': _st['raw'], 'news': _st['news'], 'news_at': _st['news_at'], 'spy': _st['spy']})


def _keep_history():
    o = overall(indicators())
    if not o:
        return
    h = watch._read(HIST, [])
    today = watch.et(_now()).date().isoformat()
    h = [x for x in h if x.get('day') != today] + [{'day': today, 'score': o['score']}]
    watch._write(HIST, h[-800:])


def _loop():
    last = {'news': 0, 'spy': 0, 'hist': ''}
    while True:
        try:
            did = False
            for sid, *_ in SERIES:
                with _lock:
                    hit = _st['raw'].get(sid)
                if not hit or time.time() - hit.get('at', 0) > 6 * 3600:
                    try:
                        rows = fetch_series(sid)
                        with _lock:
                            _st['raw'][sid] = {'at': time.time(), 'rows': rows[-1200:]}
                            _st['errors'].pop(sid, None)
                    except Exception as e:
                        with _lock:
                            _st['raw'][sid] = dict(hit or {}, at=time.time() - 5 * 3600)    # try again in an hour
                            _st['errors'][sid] = str(e)[:120]
                    did = True
                    time.sleep(3)
            if time.time() - last['spy'] > 6 * 3600:
                last['spy'] = time.time()
                t = spy_trend()
                if t:
                    _st['spy'] = t
            if time.time() - last['news'] > 30 * 60:
                last['news'] = time.time()
                refresh_news()
                did = True
            today = watch.et(_now()).date().isoformat()
            if last['hist'] != today:
                last['hist'] = today
                _keep_history()
            if did:
                _save()
            time.sleep(60)
        except Exception as e:
            print(f'macro error: {e!r}', flush=True)
            time.sleep(60)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
        old = watch._read(CACHE, {})
        if isinstance(old.get('raw'), dict):
            _st['raw'] = old['raw']
        _st['news'] = old.get('news') or []
        _st['news_at'] = old.get('news_at') or 0
        _st['spy'] = old.get('spy')
    threading.Thread(target=_loop, name='macro', daemon=True).start()


def snapshot():
    inds = indicators()
    with _lock:
        items = list(_st['news'])
        errors = dict(_st['errors'])
        at = _st['news_at']
    return {'at': _now(), 'indicators': inds, 'overall': overall(inds), 'history': watch._read(HIST, [])[-400:],
            'news': items[:80], 'news_mood': news_mood(items), 'news_at': at, 'errors': errors}


def summary_text():
    """A few lines for the Claude question page."""
    s = snapshot()
    o = s['overall']
    lines = []
    if o:
        lines.append(f"Macro score {o['score']:+d} ({o['label']}) from {o['n']} indicators; by group: " + ', '.join(f'{g} {v:+d}' for g, v in o['groups'].items()) + '.')
    for i in s['indicators']:
        if i.get('value') is not None:
            lines.append(f"- {i['name']}: {i['value']:.2f}{i['unit'] if i['unit'] in ('%', 'k') else ''} ({i['date']}). {i['reading']}")
    nm = s['news_mood']
    if nm['n']:
        lines.append(f"Macro headlines this week: {nm['label']} ({nm['n']} headlines). Latest: " + ' | '.join(i['title'] for i in s['news'][:6]))
    return '\n'.join(lines)


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    return 404, {'error': 'unknown'}
