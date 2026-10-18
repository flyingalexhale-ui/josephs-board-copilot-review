"""News for the board's News tab: headlines about the symbols Joseph's Board tracks.

Which symbols: the IBKR and Webull board watchlists (data/*_watchlist.json), the scanner's ETF
and stock lists (mover-scanner/watchlist.yaml, stocklist.yaml) and the two algos' symbols
(WEBULL_ALGO_STRATEGY / IBKR_ALGO_STRATEGY in settings.env). Re-read every 10 minutes, so
symbols added on the board show up here by themselves.

Where from: free public RSS feeds, no account or key needed.
  * stocks and ETFs -> Yahoo Finance's headline feed for that ticker
                       (Google News is used instead if Yahoo doesn't answer)
  * futures (ES=F..) -> a Google News search for the market behind it ("S&P 500 futures",
                       "crude oil", "gold prices", ...)

One background thread fetches one feed every few seconds and refreshes each symbol about every
15 minutes, so the sites are never hammered. Results are kept in data/news_cache.json so the
page isn't empty after a restart. Used by gateway.py at /api/news; standard library only
(plus certifi, already installed, for secure connections).
"""
import email.utils
import html
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'news_cache.json')

REFRESH_S = 15 * 60          # each symbol's feed is re-read this often
GAP_S = 4                    # pause between two feed requests
KEEP_S = 3 * 24 * 3600       # forget headlines older than 3 days
PER_SYMBOL = 15              # newest headlines kept per symbol
SYMBOLS_EVERY_S = 10 * 60    # how often the list of tracked symbols is re-read
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'

# what to search Google News for, per futures root (micros share their big brother's news)
FUTURES = {
    'ES': ('S&P 500 futures', '"S&P 500 futures" OR "stock futures"'),
    'NQ': ('Nasdaq 100 futures', '"Nasdaq futures" OR "Nasdaq 100 futures"'),
    'YM': ('Dow futures', '"Dow futures" OR "Dow Jones futures"'),
    'RTY': ('Russell 2000 futures', '"Russell 2000" futures OR "small caps"'),
    'CL': ('Crude oil', '"crude oil" OR "oil prices"'),
    'NG': ('Natural gas', '"natural gas prices" OR "natural gas futures"'),
    'RB': ('Gasoline', '"gasoline futures" OR "gasoline prices"'),
    'HO': ('Heating oil', '"heating oil" OR "diesel prices"'),
    'GC': ('Gold', '"gold prices" OR "gold futures"'),
    'SI': ('Silver', '"silver prices" OR "silver futures"'),
    'HG': ('Copper', '"copper prices" OR "copper futures"'),
    'ZB': ('30-year Treasury', '"Treasury yields" OR "bond market"'),
    'ZN': ('10-year Treasury', '"10-year Treasury" OR "Treasury yields"'),
    'ZC': ('Corn', '"corn futures" OR "corn prices"'),
    'ZS': ('Soybeans', '"soybean futures" OR "soybean prices"'),
    '6E': ('Euro', '"euro" dollar exchange rate'),
    '6J': ('Japanese yen', '"yen" dollar exchange rate'),
    '6B': ('British pound', '"pound sterling" OR "British pound"'),
    'BTC': ('Bitcoin', '"bitcoin price" OR "bitcoin futures"'),
}
MICRO = {'MES': 'ES', 'MNQ': 'NQ', 'MYM': 'YM', 'M2K': 'RTY', 'MCL': 'CL', 'MGC': 'GC', 'MBT': 'BTC'}

_lock = threading.Lock()
_state = {'items': {}, 'fetched': {}, 'errors': {}, 'symbols': {}, 'symbols_ts': 0, 'started': False}


# ------------------------------------------------------------------ which symbols
def _json_list(p):
    try:
        with open(p) as f:
            v = json.load(f)
        return [str(s).strip().upper() for s in v if str(s).strip()] if isinstance(v, list) else []
    except Exception:
        return []


def _yaml_symbols(p):
    """The `symbols:` list of a scanner yaml file, read without needing a yaml library."""
    out, inside = [], False
    try:
        with open(p) as f:
            for line in f:
                if re.match(r'^symbols\s*:', line):
                    inside = True
                    continue
                if inside:
                    m = re.match(r'^\s+-\s*["\']?([A-Za-z0-9.\-=^/]+)', line)
                    if m:
                        out.append(m.group(1).upper())
                    elif line.strip() and not line.lstrip().startswith('#'):
                        break
    except OSError:
        pass
    return out


def _algo_symbol(folder, env_key):
    rel = os.environ.get(env_key) or 'strategies/sma_crossover.yaml'
    p = rel if os.path.isabs(rel) else os.path.join(ROOT, folder, rel)
    try:
        with open(p) as f:
            for line in f:
                m = re.match(r'^symbol\s*:\s*["\']?([A-Za-z0-9.\-=^/]+)', line)
                if m:
                    return m.group(1).upper()
    except OSError:
        pass
    return None


def tracked():
    """{symbol: {'kind': 'future'|'etf'|'stock', 'where': [...]}} for everything on the board."""
    etfs = _yaml_symbols(os.path.join(ROOT, 'mover-scanner', 'watchlist.yaml'))
    groups = [('IBKR board', _json_list(os.path.join(DATA, 'ibkr_watchlist.json'))),
              ('Webull board', _json_list(os.path.join(DATA, 'webull_watchlist.json'))),
              ('Scanner ETFs', etfs),
              ('Scanner stocks', _yaml_symbols(os.path.join(ROOT, 'mover-scanner', 'stocklist.yaml'))),
              ('Webull Algo', [s for s in [_algo_symbol('webull-algo', 'WEBULL_ALGO_STRATEGY')] if s]),
              ('IBKR Algo', [s for s in [_algo_symbol('ibkr-algo', 'IBKR_ALGO_STRATEGY')] if s])]
    out = {}
    for where, syms in groups:
        for s in syms:
            if s.endswith('=F'):
                kind = 'future'
            elif s in etfs:
                kind = 'etf'
            else:
                kind = 'stock'
            e = out.setdefault(s, {'kind': kind, 'where': []})
            if where not in e['where']:
                e['where'].append(where)
    for s, e in out.items():
        if e['kind'] == 'future':
            root = s[:-2]
            e['name'] = FUTURES.get(MICRO.get(root, root), (root + ' futures',))[0]
    return out


# ------------------------------------------------------------------ fetching
def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_CTX = None


def _get(url):
    global _CTX
    if _CTX is None:
        _CTX = _ssl_context()
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/rss+xml, application/xml, text/xml'})
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
        return r.read(2_000_000)


def _text(el, tag):
    x = el.find(tag)
    return (x.text or '').strip() if x is not None and x.text else ''


def _parse_rss(body, default_source):
    items = []
    root = ET.fromstring(body)
    for it in root.iter('item'):
        title = html.unescape(_text(it, 'title'))
        link = _text(it, 'link')
        if not title or not link.startswith('http'):
            continue
        source = html.unescape(_text(it, 'source')) or default_source
        if source != default_source and title.endswith(' - ' + source):
            title = title[:-len(' - ' + source)]      # Google News adds " - Publisher" to titles
        try:
            ts = email.utils.parsedate_to_datetime(_text(it, 'pubDate')).timestamp()
        except Exception:
            ts = time.time()
        items.append({'title': title, 'link': link, 'source': source, 'ts': int(ts)})
    return items


def _google(query):
    q = urllib.parse.quote(query + ' when:3d')
    url = 'https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en' % q
    return _parse_rss(_get(url), 'Google News')


def _yahoo(sym):
    url = 'https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US' % \
          urllib.parse.quote(sym.replace('.', '-'))
    return _parse_rss(_get(url), 'Yahoo Finance')


def _fetch(sym, info):
    if info['kind'] == 'future':
        root = sym[:-2]
        root = MICRO.get(root, root)
        return _google(FUTURES.get(root, (None, '"%s futures"' % root))[1])
    try:
        got = _yahoo(sym)
        if got:
            return got
    except Exception:
        pass
    return _google('"%s" stock' % sym)


def _norm(title):
    return re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip()


def _store(sym, got):
    now = time.time()
    with _lock:
        mine = [i for i in got if now - i['ts'] < KEEP_S]
        mine.sort(key=lambda i: -i['ts'])
        _state['items'][sym] = mine[:PER_SYMBOL]
        _state['fetched'][sym] = int(now)
        _state['errors'].pop(sym, None)


def _save():
    with _lock:
        snap = {'items': _state['items'], 'fetched': _state['fetched']}
    try:
        tmp = CACHE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(snap, f)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def _load():
    try:
        with open(CACHE) as f:
            snap = json.load(f)
        _state['items'] = snap.get('items') or {}
        _state['fetched'] = snap.get('fetched') or {}
    except Exception:
        pass


def _worker():
    _load()
    n = 0
    while True:
        try:
            now = time.time()
            if now - _state['symbols_ts'] > SYMBOLS_EVERY_S:
                syms = tracked()
                with _lock:
                    _state['symbols'], _state['symbols_ts'] = syms, now
            syms = _state['symbols']
            due = [s for s in syms if now - _state['fetched'].get(s, 0) > REFRESH_S]
            due.sort(key=lambda s: _state['fetched'].get(s, 0))       # oldest first
            if not due:
                time.sleep(15)
                continue
            sym = due[0]
            try:
                _store(sym, _fetch(sym, syms[sym]))
            except Exception as e:
                with _lock:
                    _state['errors'][sym] = str(e)[:120]
                    _state['fetched'][sym] = int(now - REFRESH_S + 120)     # try again in 2 minutes
            n += 1
            if n % 10 == 0 or len(due) == 1:
                _save()
            time.sleep(GAP_S)
        except Exception as e:                                   # never let the thread die
            print('news: %r' % e, flush=True)
            time.sleep(30)


def start():
    with _lock:
        if _state['started']:
            return
        _state['started'] = True
    threading.Thread(target=_worker, name='news', daemon=True).start()


# ------------------------------------------------------------------ what the page gets
def snapshot():
    """Headlines for every tracked symbol, newest first; the same story found for several
    symbols is listed once with all of them."""
    start()
    if not _state['symbols_ts']:                  # first visit right after a restart
        found = tracked()
        with _lock:
            if not _state['symbols_ts']:
                _state['symbols'], _state['symbols_ts'] = found, time.time()
    with _lock:
        syms = dict(_state['symbols'])
        items = {s: list(v) for s, v in _state['items'].items() if s in syms}
        fetched = dict(_state['fetched'])
        errors = dict(_state['errors'])
    merged = {}
    for sym, lst in items.items():
        for i in lst:
            k = _norm(i['title'])
            m = merged.get(k)
            if m is None:
                m = merged[k] = dict(i, syms=[])
            if sym not in m['syms']:
                m['syms'].append(sym)
    out = sorted(merged.values(), key=lambda i: -i['ts'])[:400]
    try:                                          # jb-insight: analysis articles (Nasdaq.com), tagged ANALYSIS
        import insight
        out = sorted(out + [a for a in insight.articles() if any(s in syms for s in a['syms'])], key=lambda i: -i['ts'])
    except Exception:
        pass
    done = [s for s in syms if s in fetched]
    return {'server_ts': int(time.time()), 'symbols': syms, 'items': out,
            'loaded': len(done), 'total': len(syms),
            'last_fetch': max(fetched.values()) if fetched else None,
            'failed': sorted(s for s in errors if s in syms)}


def response(headers):
    """(body, extra headers) for gateway.py: the snapshot as JSON, gzipped when the browser
    accepts it (about a fifth of the size, which matters on a phone)."""
    import gzip
    body = json.dumps(snapshot(), separators=(',', ':')).encode()
    if 'gzip' in headers.get('accept-encoding', ''):
        return gzip.compress(body, 6), {'Content-Encoding': 'gzip', 'Vary': 'Accept-Encoding'}
    return body, {'Vary': 'Accept-Encoding'}
