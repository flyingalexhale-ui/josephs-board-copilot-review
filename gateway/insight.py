"""Alex Hale's Board -- desk notes, analysis articles and crowd gauges (jb-insight, deep search ideas 27, 29, 30, 31).

27 Desk note: a short analyst-style note for any symbol, written by the board from what it already knows (move since
   the close and by session, where it sits in its overnight, 1-week and 52-week range, trend, market/sector/own
   move, volume, the last level crossed, earnings coming up, the newest headline). Board math only.
29 Analysis articles: Nasdaq.com's free per-symbol RSS (it carries Zacks, Motley Fool and others) for your 2x/3x
   funds, their biggest holdings and what you hold. Shown on the News tab with an ANALYSIS tag.
30 Fear & Greed: CNN's 0-100 index and its 7 parts (public data behind CNN's page, unofficial). Tiny: one line.
31 AAII weekly survey (individual investors bullish / bearish, Thursdays) and NAAIM exposure index (active managers,
   Wednesdays), with a note at extremes.

GET /api/plus/insight/note?sym=SOXL      -> {sym, note, parts}
GET /api/plus/insight/notes?syms=A,B     -> {notes: {sym: note}}   (the Channel can read these)
GET /api/plus/insight/crowd              -> {fg, aaii, naaim, line}
GET /api/plus/insight/articles           -> {items, errors}
"""
import html
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import watch

MIN = 60 * 1000
_lock = threading.Lock()
_st = {'started': False, 'arts': {}, 'fetched': {}, 'errors': {}, 'fg': None, 'fg_at': 0, 'fg_err': '',
       'aaii': None, 'naaim': None, 'weekly_at': 0, 'weekly_err': {}, 'notes': {}, 'pushed': {}}
SAVE = 'insight.json'
ART_EVERY = 3 * 3600          # each symbol's article feed every 3 hours
ART_GAP = 75                  # seconds between two feeds (be polite)


def _now():
    return int(time.time() * 1000)


def _path():
    import os
    return os.path.join(watch.DATA, SAVE)


def _http(url, timeout=20):
    import feeds
    return feeds._http(url, timeout=timeout, chrome=True,
                       headers={'Accept': 'application/json, application/rss+xml, text/html, */*', 'Accept-Language': 'en-US,en;q=0.9'})


# ------------------------------------------------------------------ 27 desk note
def _sessions_text(sym):
    try:
        import analysis
        s = analysis.sessions(sym)
    except Exception:
        return ''
    if not s:
        return ''
    parts = [(n, s.get(k)) for k, n in (('ah', 'after-hours'), ('on', 'overnight'), ('pre', 'pre-market'), ('reg', 'the regular session')) if s.get(k) is not None]
    parts = [(n, v) for n, v in parts if abs(v) >= 0.2]
    if not parts:
        return ''
    big = max(parts, key=lambda x: abs(x[1]))
    if len(parts) == 1:
        return f"the move came in {big[0]} ({big[1]:+.1f}%)"
    return 'by session: ' + ', '.join(f"{n} {v:+.1f}%" for n, v in parts) + f"; most of it in {big[0]}"


def _range_text(sym, last, session=''):
    out = []
    try:
        import analysis
        rows = analysis._cached_daily(sym)
    except Exception:
        rows = []
    if rows and last:
        def where(lo, hi):
            if hi <= lo:
                return None
            return (last - lo) / (hi - lo)
        wk = rows[-5:]
        w = where(min(r[3] for r in wk), max(r[2] for r in wk))
        y = where(min(r[3] for r in rows[-252:]), max(r[2] for r in rows[-252:]))
        if w is not None:
            out.append('above its 1-week range' if w > 1 else 'below its 1-week range' if w < 0 else
                       'near the top of its 1-week range' if w >= 0.8 else 'near the bottom of its 1-week range' if w <= 0.2 else '')
        if y is not None and (y >= 0.95 or y <= 0.05):
            out.append('at a 52-week high' if y >= 1 else 'close to its 52-week high' if y >= 0.95 else
                       'at a 52-week low' if y <= 0 else 'close to its 52-week low')
    try:
        if session not in ('overnight', 'pre-market'):
            raise ValueError('no overnight range yet')
        on_s, on_e = watch.overnight_window(_now())
        with watch._lock:
            h = [p for t, p in (watch._px.get(sym) or []) if on_s <= t <= min(on_e, _now())]
        if len(h) >= 30 and last:
            hi, lo = max(h), min(h)
            if hi > lo:
                f = (last - lo) / (hi - lo)
                out.append('at its overnight high' if f >= 0.97 else 'at its overnight low' if f <= 0.03 else '')
    except Exception:
        pass
    return [x for x in out if x]


def _earnings_text(sym, now):
    try:
        import earn
        dates = earn._earn_dates()
    except Exception:
        return ''
    d0 = watch.et(now).date()
    def soon(s):
        e = dates.get(s) or {}
        if not e.get('date'):
            return None
        try:
            d = datetime.strptime(e['date'], '%Y-%m-%d').date()
        except ValueError:
            return None
        if 0 <= (d - d0).days <= 7:
            day = 'today' if d == d0 else 'tomorrow' if (d - d0).days == 1 else d.strftime('%A')
            return f"{day}{' ' + e['when'] if e.get('when') else ''}"
        return None
    w = soon(sym)
    if w:
        return f"{sym} reports {w}"
    try:
        import edge
        for r in sorted(edge.holdings(sym) or [], key=lambda r: -r['w'])[:8]:
            w = soon(r['sym'])
            if w and r['w'] >= 3:
                return f"{r['sym']} ({r['w']:.0f}% of the fund) reports {w}"
    except Exception:
        pass
    return ''


def _headline(sym, now):
    try:
        import news
        with news._lock:
            items = list(news._state['items'].get(sym) or [])
        items = [i for i in items if now / 1000 - i['ts'] < 24 * 3600]
        if items:
            return items[0]['title']
    except Exception:
        pass
    a = [i for i in _st['arts'].get(sym) or [] if now / 1000 - i['ts'] < 48 * 3600]
    return a[0]['title'] + ' (analysis)' if a else ''


NAMES = {'NQ=F': 'Nasdaq futures', 'ES=F': 'S&P futures', 'YM=F': 'Dow futures', 'RTY=F': 'Russell futures'}


def note(sym, now=None):
    """-> {sym, note, parts}: 2 to 4 plain sentences."""
    import why
    now = now or _now()
    sym = sym.upper()
    with _lock:
        c = _st['notes'].get(sym)
        if c and now - c['at'] < 2 * MIN:
            return c['out']
    o = why.explain(sym, now, light=True)
    name = NAMES.get(sym, sym)
    s = []
    mv = o.get('move')
    if mv is None:
        s.append(o.get('note') or f"The board has no fresh price for {sym} right now.")
    else:
        size = 'flat' if abs(mv) < 0.3 else f"{'up' if mv > 0 else 'down'} {abs(mv):.1f}%"
        sess = {'after-hours': 'after the close', 'overnight': 'overnight', 'pre-market': 'in pre-market', 'regular': 'today'}.get(o.get('session'), '')
        first = f"{name} {'are' if sym in NAMES else 'is'} {size} since the 4 pm close" + (f" ({sess})" if sess and size != 'flat' else '')
        rng = _range_text(sym, o.get('last'), o.get('session'))
        if rng:
            first += ' and ' + ' and '.join(rng[:2])
        s.append(first + '.')
        st = _sessions_text(sym)
        if st and abs(mv) >= 0.5:
            s.append(st[0].upper() + st[1:] + '.')
        wl = o.get('line') or '' if abs(mv) >= 0.3 else ''
        if wl:
            s.append(wl[0].upper() + wl[1:] + '.')
        tr = o.get('trend')
        if tr and tr.get('text') and tr['text'] not in wl:
            s.append(tr['text'][0].upper() + tr['text'][1:] + '.')
        cf = (o.get('confidence') or {}).get('text')
        if cf and len(s) < 4 and abs(mv) >= 0.3:
            s.append(cf if cf.endswith('.') else cf + '.')
    e = _earnings_text(sym, now)
    if e:
        s.append(e[0].upper() + e[1:] + '.')
    hd = _headline(sym, now)
    if hd:
        s.append(f'Latest headline: "{hd}".')
    out = {'sym': sym, 'at': now, 'note': ' '.join(s[:6]), 'parts': {'move': mv, 'earnings': e, 'headline': hd}}
    with _lock:
        _st['notes'][sym] = {'at': now, 'out': out}
    return out


# ------------------------------------------------------------------ 29 analysis articles (Nasdaq.com RSS)
def _funds():
    """{symbol: [funds it relates to]}: your 2x/3x funds on the boards, their top 5 holdings, what you hold."""
    out = {}
    try:
        import news
        tracked = news.tracked()
    except Exception:
        tracked = {}
    try:
        import mdata
        pairs = mdata.pairs()
    except Exception:
        pairs = []
    lev = set()
    for _n, base, _l, bull, bear in pairs:
        for f in (bull, bear):
            if f in tracked:
                lev.add(f)
                out.setdefault(f, []).append(f)
                try:
                    import edge
                    for r in sorted(edge.holdings(base) or [], key=lambda r: -r['w'])[:5]:
                        if f not in out.setdefault(r['sym'], []):
                            out[r['sym']].append(f)
                except Exception:
                    pass
    try:
        import earn
        for p in earn.held_positions():
            out.setdefault(p['sym'], [])
    except Exception:
        pass
    return out


def _parse(body):
    items = []
    root = ET.fromstring(body)
    for it in root.iter('item'):
        def t(tag):
            x = it.find(tag)
            return (x.text or '').strip() if x is not None and x.text else ''
        title, link = html.unescape(t('title')), t('link')
        if not title or not link.startswith('http'):
            continue
        try:
            import email.utils
            ts = int(email.utils.parsedate_to_datetime(t('pubDate')).timestamp())
        except Exception:
            ts = int(time.time())
        auth = ''
        for el in it:
            if el.tag.endswith('creator') and el.text:
                auth = el.text.strip()
        items.append({'title': title, 'link': link, 'ts': ts, 'by': html.unescape(auth)})
    return items


def _articles_step():
    want = _funds()
    now = time.time()
    due = sorted((s for s in want if now - _st['fetched'].get(s, 0) > ART_EVERY), key=lambda s: _st['fetched'].get(s, 0))
    if not due:
        return False
    sym = due[0]
    _st['fetched'][sym] = now
    try:
        body = _http('https://www.nasdaq.com/feed/rssoutbound?symbol=' + urllib.parse.quote(sym))
        got = [i for i in _parse(body) if now - i['ts'] < 7 * 86400][:15]
        with _lock:
            _st['arts'][sym] = got
            _st['errors'].pop(sym, None)
    except Exception as e:
        with _lock:
            _st['errors'][sym] = str(e)[:100]
    return True


def articles():
    """For the News tab: one entry per article, with every symbol and fund it relates to."""
    want = _funds()
    try:
        import news
        tracked = news.tracked()
    except Exception:
        tracked = {}
    merged = {}
    with _lock:
        arts = {s: list(v) for s, v in _st['arts'].items()}
    for sym, lst in arts.items():
        funds = want.get(sym, [])
        for i in lst:
            m = merged.setdefault(i['link'], {'title': i['title'], 'link': i['link'], 'ts': i['ts'], 'analysis': True,
                                              'source': 'Nasdaq.com' + (' · ' + i['by'] if i.get('by') else ''), 'syms': [], 'about': [], 'funds': []})
            if sym in tracked and sym not in m['syms']:
                m['syms'].append(sym)
            elif sym not in tracked and sym not in m['about']:
                m['about'].append(sym)
            for f in funds:
                if f not in m['funds']:
                    m['funds'].append(f)
                if f in tracked and f not in m['syms']:
                    m['syms'].append(f)
    out = [m for m in merged.values() if m['syms']]
    out.sort(key=lambda m: -m['ts'])
    return out[:150]


# ------------------------------------------------------------------ 30 Fear & Greed, 31 AAII and NAAIM
FG_PARTS = (('market_momentum_sp500', 'Momentum (S&P vs its 125-day average)'), ('stock_price_strength', 'New highs vs new lows'),
            ('stock_price_breadth', 'Breadth (volume in rising vs falling stocks)'), ('put_call_options', 'Put/call options'),
            ('market_volatility_vix', 'Volatility (VIX)'), ('junk_bond_demand', 'Junk bond demand'), ('safe_haven_demand', 'Safe havens (stocks vs bonds)'))


def _fg_step():
    d = datetime.now(timezone.utc).date() - timedelta(days=40)
    import json
    j = json.loads(_http(f'https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{d.isoformat()}'))
    f = j['fear_and_greed']
    out = {'score': round(f['score']), 'rating': f.get('rating', ''), 'week': round(f.get('previous_1_week') or 0),
           'month': round(f.get('previous_1_month') or 0), 'year': round(f.get('previous_1_year') or 0), 'parts': []}
    for k, n in FG_PARTS:
        p = j.get(k) or {}
        if p.get('rating'):
            out['parts'].append({'name': n, 'rating': p['rating'], 'score': round(p.get('score') or 0)})
    return out


def _pct_cells(row):
    return [float(x) for x in re.findall(r'(\d{1,3}(?:\.\d+)?)\s*%', row)]


def _aaii_step():
    page = _http('https://www.aaii.com/sentimentsurvey/sent_results')
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S | re.I):
        v = _pct_cells(row)
        if len(v) >= 3:
            date = re.sub(r'<[^>]+>', ' ', row)
            m = re.search(r'([A-Z][a-z]{2,8}\.? \d{1,2},?(?: \d{4})?)', date)
            return {'bull': v[0], 'neutral': v[1], 'bear': v[2], 'date': m.group(1) if m else ''}
    raise ValueError('no survey table on the AAII page')


def _naaim_step():
    page = _http('https://naaim.org/programs/naaim-exposure-index/')
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S | re.I):
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)]
        if len(cells) >= 2 and re.match(r'\d{1,2}/\d{1,2}/\d{2,4}$', cells[0]):
            try:
                return {'value': round(float(cells[1].replace(',', '')), 1), 'date': cells[0]}
            except ValueError:
                continue
    raise ValueError('no exposure table on the NAAIM page')


def crowd():
    fg, a, n = _st['fg'], _st['aaii'], _st['naaim']
    bits, notes = [], []
    if fg:
        bits.append(f"Fear & Greed {fg['score']} ({fg['rating']}; a week ago {fg['week']}, a month ago {fg['month']})")
        if fg['score'] <= 25:
            notes.append('Fear & Greed shows extreme fear: often near a bottom, not a top.')
        elif fg['score'] >= 75:
            notes.append('Fear & Greed shows extreme greed: the crowd is all in.')
    if a:
        bits.append(f"AAII bulls {a['bull']:.0f}% / bears {a['bear']:.0f}%")
        if a['bear'] >= 50 or a['bull'] - a['bear'] <= -20:
            notes.append(f"AAII bears at {a['bear']:.0f}%: individual investors are very gloomy, a classic contrarian buy sign (the average is about 31%).")
        elif a['bull'] >= 50 or a['bull'] - a['bear'] >= 25:
            notes.append(f"AAII bulls at {a['bull']:.0f}%: individual investors are very upbeat, a classic contrarian caution sign (the average is about 38%).")
    if n:
        bits.append(f"NAAIM {n['value']:.0f}")
        if n['value'] >= 100:
            notes.append(f"NAAIM {n['value']:.0f}: active managers are fully invested or more (often late in a run).")
        elif n['value'] <= 30:
            notes.append(f"NAAIM {n['value']:.0f}: active managers are barely invested (often near a low).")
    return {'fg': fg, 'aaii': a, 'naaim': n, 'line': ' · '.join(bits), 'notes': notes,
            'errors': {k: v for k, v in dict(_st['weekly_err'], fg=_st['fg_err']).items() if v}}


# ------------------------------------------------------------------ loop
def _save():
    with _lock:
        snap = {'arts': _st['arts'], 'fetched': _st['fetched'], 'fg': _st['fg'], 'fg_at': _st['fg_at'],
                'aaii': _st['aaii'], 'naaim': _st['naaim'], 'weekly_at': _st['weekly_at'], 'pushed': _st['pushed']}
    watch._write(_path(), snap)


def _loop():
    v = watch._read(_path(), {})
    if isinstance(v, dict):
        for k in ('arts', 'fetched', 'fg', 'fg_at', 'aaii', 'naaim', 'weekly_at', 'pushed'):
            if v.get(k) is not None:
                _st[k] = v[k]
    time.sleep(40)
    while True:
        try:
            now = time.time()
            if now - _st['fg_at'] > 30 * 60:
                _st['fg_at'] = now
                try:
                    _st['fg'], _st['fg_err'] = _fg_step(), ''
                except Exception as e:
                    _st['fg_err'] = str(e)[:100]
            if now - _st['weekly_at'] > 6 * 3600:
                _st['weekly_at'] = now
                for k, fn in (('aaii', _aaii_step), ('naaim', _naaim_step)):
                    try:
                        _st[k] = fn()
                        _st['weekly_err'][k] = ''
                    except Exception as e:
                        _st['weekly_err'][k] = str(e)[:100]
                _extreme_push()
            _articles_step()
            _save()
        except Exception as e:
            print(f'insight: {e!r}', flush=True)
        time.sleep(ART_GAP)


def _extreme_push():
    """One push when a weekly survey reading is at an extreme (once per survey date)."""
    c = crowd()
    for k, n in (('aaii', c['aaii']), ('naaim', c['naaim'])):
        if not n:
            continue
        txt = [x for x in c['notes'] if x.startswith('AAII' if k == 'aaii' else 'NAAIM')]
        if txt and _st['pushed'].get(k) != n.get('date'):
            _st['pushed'][k] = n.get('date')
            try:
                watch.push_later('Investor sentiment at an extreme', txt[0], 'busts_in_silhouette')
            except Exception:
                pass


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_loop, name='insight', daemon=True).start()


def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return (v or '').strip()


def get(sub, query):
    start()
    if sub == 'note':
        s = _q(query, 'sym').upper()
        if not s or len(s) > 12:
            return 400, {'error': 'sym?'}
        return 200, note(s)
    if sub == 'notes':
        syms = [s for s in _q(query, 'syms').upper().split(',') if s and len(s) <= 12][:12]
        return 200, {'notes': {s: note(s)['note'] for s in syms}}
    if sub == 'crowd':
        return 200, crowd()
    if sub == 'articles':
        return 200, {'items': articles(), 'errors': dict(_st['errors'])}
    return 404, {'error': 'unknown'}


def post(sub, data):
    return 404, {'error': 'unknown'}
