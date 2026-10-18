"""Earnings dates for the picks: when each symbol next reports, and whether it is today or tomorrow.

Free source: Nasdaq's public earnings calendar (one request per day for the next two weeks,
refreshed every 6 hours, saved in data/earnings.json so a restart doesn't refetch). If Nasdaq
can't be reached, the picks simply show no earnings dates; nothing else is affected.
Also reads the News tab's cache (data/news_cache.json) to flag symbols with news in the last hour.
"""
import json
import logging
import os
import ssl
import threading
import time
import urllib.request
from datetime import datetime, timedelta

log = logging.getLogger('picks')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'earnings.json')
NEWS = os.path.join(DATA, 'news_cache.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
REFRESH_S = 6 * 3600
DAYS_AHEAD = 21
WHEN = {'time-pre-market': 'before the open', 'time-after-hours': 'after the close'}

_lock = threading.Lock()
_state = {'dates': {}, 'fetched': 0, 'error': '', 'started': False, 'news': {}, 'news_at': 0}


def _et_today():
    from .picks import et_now
    return et_now().date()


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _fetch_day(day, ctx):
    url = 'https://api.nasdaq.com/api/calendar/earnings?date=' + day.isoformat()
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        body = json.loads(r.read().decode('utf-8'))
    rows = ((body or {}).get('data') or {}).get('rows') or []
    return [(str(x.get('symbol', '')).upper().strip(), WHEN.get(x.get('time'), '')) for x in rows if x.get('symbol')]


def _refresh():
    ctx, found, ok = _ctx(), {}, 0
    start = _et_today()
    for i in range(DAYS_AHEAD):
        day = start + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        try:
            for sym, when in _fetch_day(day, ctx):
                found.setdefault(sym, {'date': day.isoformat(), 'when': when})
            ok += 1
        except Exception as e:
            err = f'{type(e).__name__}: {" ".join(str(e).split())[:80]}'
            log.warning('earnings calendar: %s %s', day, err)
            with _lock:
                _state['error'] = err
        time.sleep(1.0)
    if ok:
        with _lock:
            _state['dates'], _state['fetched'], _state['error'] = found, time.time(), ''
        try:
            os.makedirs(DATA, exist_ok=True)
            with open(CACHE + '.tmp', 'w') as f:
                json.dump({'dates': found, 'fetched': time.time()}, f)
            os.replace(CACHE + '.tmp', CACHE)
        except OSError:
            pass
        log.info('earnings calendar: %d upcoming reports loaded', len(found))


def _worker():
    try:
        with open(CACHE) as f:
            snap = json.load(f)
        with _lock:
            _state['dates'], _state['fetched'] = snap.get('dates') or {}, snap.get('fetched') or 0
    except Exception:
        pass
    while True:
        if time.time() - _state['fetched'] > REFRESH_S:
            try:
                _refresh()
            except Exception as e:           # never let the thread die
                log.warning('earnings calendar failed: %r', e)
        time.sleep(600)


def start(symbols=None):
    with _lock:
        if _state['started']:
            return
        _state['started'] = True
    threading.Thread(target=_worker, name='earnings', daemon=True).start()


def _recent_news(sym):
    now = time.time()
    if now - _state['news_at'] > 120:          # re-read the News tab's cache every 2 minutes
        _state['news_at'] = now
        try:
            with open(NEWS) as f:
                _state['news'] = json.load(f).get('items') or {}
        except Exception:
            _state['news'] = {}
    items = [i for i in _state['news'].get(sym, []) if now - i.get('ts', 0) < 3600]
    return items[0]['title'] if items else None


def get(sym):
    """{'date', 'when', 'days', 'label', 'news'} or None. days: trading-calendar days from today."""
    with _lock:
        d = _state['dates'].get(sym)
    news = _recent_news(sym)
    if not d and not news:
        return None
    out = {'news': news}
    if d:
        day = datetime.strptime(d['date'], '%Y-%m-%d').date()
        n = (day - _et_today()).days
        if n < 0:
            return {'news': news} if news else None
        out.update(date=day.strftime('%a %b %-d'), when=d['when'], days=n,
                   label='today' if n == 0 else 'tomorrow' if n == 1 else f'in {n} days')
    return out


def status():
    with _lock:
        if _state['error'] and not _state['dates']:
            return "Earnings dates unavailable right now (Nasdaq's calendar couldn't be reached)."
        return ''
