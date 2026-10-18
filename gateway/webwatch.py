"""Night desk "Web watch" (jb-webwatch), running inside the gateway. A free crawler that fills the
gaps between the news feeds, for the stocks on your boards and the ones you hold. No keys, no cost.

  Press releases   GlobeNewswire, PR Newswire and Business Wire feeds, every 2 minutes. Companies
                   publish here first; news sites pick it up later. Matched by "(NASDAQ: XYZ)" tags.
  Reddit           new posts in wallstreetbets, stocks, options, StockMarket, every 3 minutes;
                   a spike = a ticker mentioned far more this hour than its usual hour.
  SEC filings      every 5 minutes: filings BY your companies other than the 8-K / Form 4 / 13D the
                   Signals tab already covers (offerings like S-3 and 424B, 13G, 10-Q, 6-K ...), and
                   other companies' 8-Ks that MENTION your company (deals, partners), from EDGAR's
                   full-text search.
  Wikipedia        daily page views per company, checked every 15 minutes (Wikipedia updates its
                   numbers once a day); a spike = the public suddenly paying attention.
  Google Trends    the US "trending now" list, every 30 minutes (unofficial, best effort).
  Unexplained move a stock of yours moving 3% or more since the 4 pm close with nothing found
                   anywhere: no headline, filing, rating, earnings or anything above.
Phone pushes for each kind are off until you switch them on.
  GET  /api/plus/webwatch             {items, spikes, unexplained, sources, settings}
  POST /api/plus/webwatch/settings    {wires, reddit, sec, attention, unexplained: true|false}
Files: data/webwatch_cache.json, data/webwatch_settings.json.
"""
import html
import json
import os
import re
import statistics
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'webwatch_cache.json')
SETTINGS = os.path.join(DATA, 'webwatch_settings.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
WIRES = [
    ('GlobeNewswire', 'https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies'),
    ('PR Newswire', 'https://www.prnewswire.com/rss/news-releases-list.rss'),
    ('Business Wire', 'https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGVtRWA=='),
]
REDDIT = 'https://www.reddit.com/r/wallstreetbets+stocks+options+StockMarket/new/.rss?limit=100'   # the JSON answer is blocked (403)
TRENDS = 'https://trends.google.com/trending/rss?geo=US'
EVERY = {'wires': 120, 'reddit': 300, 'sec': 300, 'wiki': 900, 'trends': 1800, 'unexplained': 120}
KINDS = {'wires': 'Press releases', 'reddit': 'Reddit spikes', 'sec': 'SEC filings', 'attention': 'Wikipedia / Google Trends spikes',
         'unexplained': 'Unexplained moves'}
COVERED = {'8-K', '8-K/A', '4', '4/A', 'SC 13D', 'SC 13D/A', 'SCHEDULE 13D', 'SCHEDULE 13D/A'}   # already on the Signals tab
TAG_RE = re.compile(r'\((?:NASDAQ|Nasdaq|NYSE|NYSE American|NYSE Arca|Cboe|CBOE|OTCQX|OTCQB|TSX|NASDAQ GS|NASDAQ GM)\s*:\s*([A-Z][A-Z.]{0,5})\)')
# words that are also tickers: only counted on Reddit when written as $XYZ
COMMON = set('A AI ALL AM AN AND ANY ARE AS AT BE BIG BY CAN CEO DD EOD EPS ETF FOR GO HAS HE IF IN IPO IS IT ITM LOL ME MY NEW NO NOT NOW OF OK ON ONE OR OTM OUT PM PT SO TA THE TO UP US USA WE YOLO ATH IMO FYI EV OPEN REAL SEE TV CASH FUN LOVE BEST CAR NICE'.split())

_lock = threading.Lock()
_st = {'started': False, 'items': [], 'seen': {}, 'reddit_hours': {}, 'wiki_titles': {}, 'wiki': {}, 'sec_seen': {},
       'sources': {}, 'names': {}, 'ciks': {}, 'ciks_at': 0, 'fts_i': 0, 'unexplained': {}, 'spikes': {}}


def _now():
    return int(time.time() * 1000)


def settings():
    s = watch._read(SETTINGS, {})
    s = s if isinstance(s, dict) else {}
    return {k: bool(s.get(k, False)) for k in KINDS}


def _http(url, headers=None, timeout=25):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout, context=watch._ctx()) as r:
        return r.read(8_000_000).decode('utf-8', 'replace')


def _http_chrome(url, timeout=25):
    """Some sites (GlobeNewswire) never answer plain Python: ask like Chrome does (curl_cffi, as the World tab
    does for Yahoo), and fall back to the plain way if that isn't available."""
    try:
        import world
        if world._browser is not None:
            r = world._browser.Session(impersonate='chrome').get(url, timeout=timeout)
            if r.status_code >= 400:
                raise OSError(f'HTTP Error {r.status_code}')
            return r.content[:8_000_000].decode('utf-8', 'replace')
    except OSError:
        raise
    except Exception:
        pass
    return _http(url, timeout=timeout)


def _ok(src, n=0, note=''):
    with _lock:
        _st['sources'][src] = {'at': _now(), 'ok': True, 'n': n, 'note': note, 'good': _now()}   # good = last success (jb-power)


def _fail(src, e):
    with _lock:
        prev = _st['sources'].get(src) or {}
        _st['sources'][src] = dict(prev, at=_now(), ok=False, err=str(e)[:160])


def mine():
    """{sym: kind} for stocks and ETFs on your boards, algos and holdings (not scanner-only)."""
    try:
        import mdata
        return {s: i['kind'] for s, i in mdata.tracked().items() if mdata.mine(i)}
    except Exception:
        return {}


def held():
    try:
        import mdata
        return mdata.held()
    except Exception:
        return set()


def _add(kind, sym, title, url='', src='', extra=None, push=None, key=None):
    """Store a found item once (by kind + key, or url/title); push if that kind's switch is on."""
    key = kind + '|' + (key or url or title)[:300]
    with _lock:
        if key in _st['seen']:
            return False
        _st['seen'][key] = _now()
        # only http/https links reach the pages (review 2026-10-11, second check)
        url = url if str(url or '').strip().lower().startswith(('http://', 'https://')) else ''
        it = {'at': _now(), 'kind': kind, 'sym': sym, 'title': title[:300], 'url': url, 'src': src}
        it.update(extra or {})
        _st['items'].append(it)
        _st['items'] = _st['items'][-600:]
    if push and settings().get(push):
        watch.push_later(f'{sym}: {KINDS[push].rstrip("s")}', f'{title[:220]} ({src})', 'mag')
    return True


# ------------------------------------------------------------------ names (from the SEC's ticker list)
def _sec_headers():
    who = os.environ.get('JB_SEC_EMAIL', '').strip()
    return {'User-Agent': f'JosephsBoard {who}' if who else 'JosephsBoard personal-dashboard', 'Accept-Encoding': 'identity'}


def clean_name(n):
    n = re.sub(r'[,.]?\s+(inc|corp|corporation|co|company|ltd|plc|holdings?|group|sa|nv|ag|se|n\.v|s\.a|class [a-c]|/[a-z]+/?)\.?$', '', n.strip(), flags=re.I)
    n = re.sub(r'[,.]?\s+(inc|corp|corporation|ltd|plc|holdings?)\.?$', '', n, flags=re.I)
    return n.strip(' ,.')


def names():
    if _st['ciks'] and time.time() - _st['ciks_at'] < 7 * 86400:
        return _st['ciks'], _st['names']
    body = json.loads(_http('https://www.sec.gov/files/company_tickers.json', _sec_headers()))
    ciks, nm = {}, {}
    for v in body.values():
        t = str(v['ticker']).upper()
        ciks[t] = int(v['cik_str'])
        nm[t] = clean_name(str(v.get('title') or ''))
    _st['ciks'], _st['names'], _st['ciks_at'] = ciks, nm, time.time()
    return ciks, nm


# ------------------------------------------------------------------ press releases
def parse_rss(text):
    root = ET.fromstring(text.encode('utf-8') if isinstance(text, str) else text)
    out = []
    for it in root.iter('item'):
        g = lambda k: (it.findtext(k) or '').strip()
        desc = re.sub(r'<[^>]+>', ' ', g('description'))
        cats = ' '.join((c.text or '') for c in it.findall('category'))
        out.append({'title': g('title'), 'url': g('link'), 'text': desc + ' ' + cats, 'when': g('pubDate')})
    return out


def wire_symbols(item, want):
    found = set(TAG_RE.findall(item['title'] + ' ' + item['text']))
    for m in re.findall(r'\b(?:NASDAQ|NYSE)\s*:\s*([A-Z][A-Z.]{0,5})\b', item['title'] + ' ' + item['text']):
        found.add(m)
    return [s for s in found if s in want]


def check_wires(want):
    for name, url in WIRES:
        try:
            items = parse_rss(_http_chrome(url))
            n = 0
            for it in items:
                for s in wire_symbols(it, want):
                    n += _add('wire', s, it['title'], it['url'], name, push='wires')
            _ok(name, len(items), f'{n} new for you' if n else '')
        except Exception as e:
            _fail(name, e)
        time.sleep(1)


# ------------------------------------------------------------------ Reddit
def mentions(text, want):
    out = set()
    for m in re.findall(r'\$([A-Za-z]{1,5})\b', text):
        if m.upper() in want:
            out.add(m.upper())
    for m in re.findall(r'\b([A-Z]{3,5})\b', text):
        if m in want and m not in COMMON:
            out.add(m)
    return out


def parse_reddit_rss(text):
    """Reddit's Atom feed -> posts shaped like its old JSON answer (no scores in the feed)."""
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    root = ET.fromstring(text.encode('utf-8'))
    out = []
    for e in root.findall('a:entry', ns):
        link = (e.find('a:link', ns).get('href') if e.find('a:link', ns) is not None else '') or ''
        cat = e.find('a:category', ns)
        when = (e.findtext('a:published', '', ns) or e.findtext('a:updated', '', ns)).strip()
        try:
            ts = datetime.fromisoformat(when.replace('Z', '+00:00')).timestamp()
        except ValueError:
            ts = time.time()
        body = re.sub(r'<[^>]+>', ' ', html.unescape(e.findtext('a:content', '', ns) or ''))
        out.append({'id': (e.findtext('a:id', '', ns) or link).strip(), 'title': (e.findtext('a:title', '', ns) or '').strip(),
                    'selftext': html.unescape(body), 'created_utc': ts, 'permalink': re.sub(r'^https?://[^/]+', '', link),
                    'subreddit': cat.get('term') if cat is not None else '', 'score': 0})
    return out


def check_reddit(want):
    posts = parse_reddit_rss(_http(REDDIT, {'Accept': 'application/atom+xml, application/xml'}))
    hour = time.strftime('%Y%m%d%H', time.gmtime())
    with _lock:
        hrs = _st['reddit_hours']
        seen = _st['seen']
        for p in posts:
            k = 'rd|' + str(p.get('id'))
            if k in seen:
                continue
            seen[k] = _now()
            created = time.strftime('%Y%m%d%H', time.gmtime(p.get('created_utc') or time.time()))
            for s in mentions((p.get('title') or '') + ' ' + (p.get('selftext') or '')[:4000], want):
                h = hrs.setdefault(s, {})
                h[created] = h.get(created, 0) + 1
                lst = h.setdefault('_posts', [])
                lst.append({'t': int((p.get('created_utc') or 0) * 1000), 'title': (p.get('title') or '')[:200],
                            'url': 'https://www.reddit.com' + (p.get('permalink') or ''), 'sub': p.get('subreddit') or '', 'score': p.get('score') or 0})
                h['_posts'] = lst[-10:]
        cut = time.strftime('%Y%m%d%H', time.gmtime(time.time() - 48 * 3600))
        for s in list(hrs):
            for k in [k for k in hrs[s] if not k.startswith('_') and k < cut]:
                del hrs[s][k]
    _ok('Reddit', len(posts))
    for s in want:
        sp = reddit_spike(s, hour)
        if sp:
            with _lock:
                prev = _st['spikes'].get('reddit ' + s)
                _st['spikes']['reddit ' + s] = dict(sp, at=_now(), sym=s, kind='reddit')
            if not prev or _now() - prev['at'] > 6 * 3600000:
                _add('reddit', s, f"{s} mentioned {sp['now']} times on Reddit this hour (usually {sp['usual']:.1f})", '', 'Reddit',
                     {'posts': sp['posts']}, push='reddit')


def reddit_spike(sym, hour):
    with _lock:
        h = dict(_st['reddit_hours'].get(sym) or {})
    now = h.get(hour, 0)
    past = [v for k, v in h.items() if not k.startswith('_') and k != hour]
    usual = sum(past) / 47 if past else 0.0          # spread over the other 47 hours
    if now >= 4 and now >= 3 * max(usual, 0.5):
        return {'now': now, 'usual': round(usual, 1), 'posts': (h.get('_posts') or [])[-3:]}
    return None


# ------------------------------------------------------------------ SEC
def check_sec(want):
    ciks, nm = names()
    stocks = [s for s, k in want.items() if k == 'stock' and s in ciks]
    bad = None
    for s in stocks:                              # filings BY your companies
        try:
            body = json.loads(_http(f'https://data.sec.gov/submissions/CIK{ciks[s]:010d}.json', _sec_headers()))
            r = (body.get('filings') or {}).get('recent') or {}
            cut = (date.today() - timedelta(days=3)).isoformat()
            for i, form in enumerate((r.get('form') or [])[:30]):
                fd = r['filingDate'][i]
                if fd < cut or form in COVERED:
                    continue
                acc = r['accessionNumber'][i]
                doc = r['primaryDocument'][i]
                url = f'https://www.sec.gov/Archives/edgar/data/{ciks[s]}/{acc.replace("-", "")}/{doc}'
                _add('sec', s, f"{s} filed a {form}{': ' + FORM_WHY[form] if form in FORM_WHY else ''}", url, 'SEC', {'form': form, 'date': fd}, push='sec')
        except Exception as e:
            bad = e                               # one stock failing doesn't stop the others
        time.sleep(0.3)
    if bad is not None:
        _fail('SEC filings', bad)
    else:
        _ok('SEC filings', len(stocks))
    # other companies' 8-Ks that mention one of yours (one company per round)
    if not stocks:
        return
    s = stocks[_st['fts_i'] % len(stocks)]
    _st['fts_i'] += 1
    name = nm.get(s) or ''
    if len(name) < 4:
        return
    try:
        q = urllib.parse.urlencode({'q': f'"{name}"', 'dateRange': 'custom', 'forms': '8-K,6-K',
                                    'startdt': (date.today() - timedelta(days=3)).isoformat(), 'enddt': date.today().isoformat()})
        body = json.loads(_http('https://efts.sec.gov/LATEST/search-index?' + q, _sec_headers()))
        n = 0
        for h in ((body.get('hits') or {}).get('hits') or [])[:20]:
            src = h.get('_source') or {}
            if str(ciks[s]) in [str(int(c)) for c in src.get('ciks') or [] if str(c).isdigit()]:
                continue                          # its own filing
            who = re.sub(r'\s*\(CIK.*$', '', (src.get('display_names') or [''])[0])
            adsh, _, fn = str(h.get('_id') or '').partition(':')
            cik = (src.get('ciks') or ['0'])[0]
            url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace("-", "")}/{fn}' if adsh and fn and str(cik).isdigit() else ''
            n += _add('sec', s, f'{who} mentions {name} in a {src.get("form") or "filing"} ({src.get("file_date") or ""})', url, 'SEC full-text search',
                      {'form': src.get('form'), 'mention': True}, push='sec')
        _ok('SEC full-text search', n, f'last: {name}')
    except Exception as e:
        _fail('SEC full-text search', e)


FORM_WHY = {'S-3': 'may sell new shares (shelf offering)', 'S-1': 'registering shares to sell', '424B5': 'selling shares or notes now (offering)',
            '424B3': 'prospectus (often shares being sold)', '424B2': 'notes / debt offering', 'SC 13G': 'a big holder owns 5%+',
            'SC 13G/A': 'a 5%+ holder changed its stake', 'SCHEDULE 13G': 'a big holder owns 5%+', 'SCHEDULE 13G/A': 'a 5%+ holder changed its stake',
            '10-Q': 'quarterly report', '10-K': 'annual report', '6-K': 'foreign company news', 'DEF 14A': 'shareholder vote coming',
            'SC TO-T': 'tender offer (someone bidding for shares)', 'SC 14D9': 'response to a tender offer', '144': 'an insider plans to sell',
            'S-8': 'shares for employee pay', 'NT 10-Q': 'quarterly report will be LATE', 'NT 10-K': 'annual report will be LATE'}


# ------------------------------------------------------------------ attention: Wikipedia + Google Trends
def wiki_title(sym, name):
    with _lock:
        if sym in _st['wiki_titles']:
            return _st['wiki_titles'][sym]
    q = urllib.parse.urlencode({'action': 'opensearch', 'search': name, 'limit': 1, 'namespace': 0, 'format': 'json'})
    body = json.loads(_http('https://en.wikipedia.org/w/api.php?' + q, {'User-Agent': 'JosephsBoard/1.0 (personal dashboard)'}))
    t = (body[1] or [None])[0] if len(body) > 1 else None
    with _lock:
        _st['wiki_titles'][sym] = t
    return t


def wiki_spike(views):
    """views: daily counts oldest first. A spike = the latest day 3x the usual (median) and 2,000+."""
    if len(views) < 10:
        return None
    usual = statistics.median(views[:-1][-30:])
    last = views[-1]
    if last >= 2000 and last >= 3 * max(usual, 1):
        return {'last': last, 'usual': int(usual)}
    return None


def check_wiki(want):
    _, nm = names()
    end = date.today()
    start = end - timedelta(days=35)
    n, bad = 0, None
    for s, k in want.items():
        if k != 'stock' or not nm.get(s):
            continue
        with _lock:
            w = _st['wiki'].get(s) or {}
        if w and time.time() - w.get('at', 0) < 6 * 3600:
            continue
        try:
            t = wiki_title(s, nm[s])
            if not t:
                continue
            url = ('https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/'
                   + urllib.parse.quote(t.replace(' ', '_'), safe='') + f'/daily/{start:%Y%m%d}/{end:%Y%m%d}')
            body = json.loads(_http(url, {'User-Agent': 'JosephsBoard/1.0 (personal dashboard)'}))
            views = [i.get('views') or 0 for i in body.get('items') or []]
            sp = wiki_spike(views)
            with _lock:
                _st['wiki'][s] = {'at': time.time(), 'title': t, 'views': views[-14:], 'spike': sp}
            if sp:
                with _lock:
                    _st['spikes']['wiki ' + s] = dict(sp, at=_now(), sym=s, kind='wiki', title=t)
                _add('wiki', s, f"{t} Wikipedia page: {sp['last']:,} views yesterday vs usual {sp['usual']:,}",
                     'https://en.wikipedia.org/wiki/' + urllib.parse.quote(t.replace(' ', '_')), 'Wikipedia', {'day': str(end)}, push='attention', key=f'{s}|{end}')
            n += 1
        except Exception as e:
            bad = e                               # try this stock again in 6 h; carry on with the others
            with _lock:
                _st['wiki'][s] = {'at': time.time(), 'title': '', 'views': [], 'spike': None}
        time.sleep(0.5)
    if bad is not None:
        _fail('Wikipedia', bad)
    else:
        _ok('Wikipedia', n)


def check_trends(want):
    _, nm = names()
    text = _http(TRENDS)
    root = ET.fromstring(text.encode('utf-8'))
    items = list(root.iter('item'))
    keys = {s: [s] + ([nm[s]] if len(nm.get(s) or '') >= 4 else []) for s, k in want.items() if k == 'stock'}
    n = 0
    for it in items:
        title = (it.findtext('title') or '').strip()
        blob = title + ' ' + ' '.join((e.text or '') for e in it.iter() if e.tag.endswith('news_item_title'))
        for s, ks in keys.items():
            if any(re.search(r'\b' + re.escape(k) + r'\b', blob, re.I if len(k) > 5 else 0) for k in ks):
                n += _add('trends', s, f'"{title}" is trending on Google in the US', 'https://trends.google.com/trending?geo=US', 'Google Trends',
                          {'day': str(date.today())}, push='attention', key=f'{s}|{title}|{date.today()}')
    _ok('Google Trends', len(items))


# ------------------------------------------------------------------ unexplained moves
def check_unexplained(want):
    import mdata
    now = _now()
    n = 0
    for s, k in want.items():
        if k != 'stock':
            continue
        p = mdata.price(s, now)
        if not p or p.get('since_close') is None or abs(p['since_close']) < 3 or now - (p.get('at') or 0) > 20 * 60000:
            with _lock:
                _st['unexplained'].pop(s, None)
            continue
        tags = mdata.tags(s, now, p['since_close'])
        with _lock:
            found = [i for i in _st['items'] if i['sym'] == s and now - i['at'] < 24 * 3600000 and i['kind'] != 'unexplained']
        if tags != ['NO NEWS'] or found:
            with _lock:
                _st['unexplained'].pop(s, None)
            continue
        with _lock:
            had = s in _st['unexplained']
            _st['unexplained'][s] = {'sym': s, 'move': p['since_close'], 'last': p['last'], 'at': now}
        if not had:
            n += 1
            _add('unexplained', s, f"{s} {p['since_close']:+.1f}% since the 4 pm close with no news found anywhere",
                 '', 'Web watch', {'move': p['since_close'], 'id': f"{s}{watch.et(now):%Y%m%d}"}, push='unexplained')
    _ok('Unexplained moves', n)


# ------------------------------------------------------------------ background
def _save():
    with _lock:
        cut = _now() - 72 * 3600000
        _st['items'] = [i for i in _st['items'] if i['at'] > cut]
        _st['seen'] = {k: v for k, v in _st['seen'].items() if v > _now() - 7 * 86400000}
        snap = {k: _st[k] for k in ('items', 'seen', 'reddit_hours', 'wiki_titles', 'wiki', 'spikes', 'sources')}
    watch._write(CACHE, snap)


def _loop():
    last = {k: 0 for k in EVERY}
    last['save'] = 0
    jobs = {'wires': check_wires, 'reddit': check_reddit, 'sec': check_sec, 'wiki': check_wiki, 'trends': check_trends,
            'unexplained': check_unexplained}
    src = {'reddit': 'Reddit', 'sec': 'SEC filings', 'wiki': 'Wikipedia', 'trends': 'Google Trends', 'unexplained': 'Unexplained moves'}
    while True:
        want = mine()
        for k, fn in jobs.items():
            if time.time() - last[k] < EVERY[k]:
                continue
            last[k] = time.time()
            if not want:
                continue
            try:
                fn(want)
            except Exception as e:
                _fail(src.get(k, k), e)
                print(f'webwatch {k} error: {e!r}', flush=True)
        if time.time() - last['save'] > 300:
            last['save'] = time.time()
            try:
                _save()
            except Exception as e:
                print(f'webwatch save error: {e!r}', flush=True)
        time.sleep(10)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
        old = watch._read(CACHE, {})
        for k in ('items', 'seen', 'reddit_hours', 'wiki_titles', 'wiki', 'spikes', 'sources'):
            if isinstance(old.get(k), type(_st[k])):
                _st[k] = old[k]
    threading.Thread(target=_loop, name='webwatch', daemon=True).start()


def snapshot():
    now = _now()
    h = held()
    with _lock:
        items = [dict(i) for i in _st['items'] if now - i['at'] < 48 * 3600000]
        spikes = [dict(v) for v in _st['spikes'].values() if now - v['at'] < 24 * 3600000]
        unexp = list(_st['unexplained'].values())
        sources = dict(_st['sources'])
        wiki = {s: {'title': w.get('title'), 'views': w.get('views'), 'spike': w.get('spike')} for s, w in _st['wiki'].items()}
    for i in items:
        i['held'] = i['sym'] in h
    items.sort(key=lambda i: -i['at'])
    return {'at': now, 'items': items[:200], 'spikes': spikes, 'unexplained': unexp, 'sources': sources, 'wiki': wiki,
            'settings': settings(), 'every': EVERY, 'watching': len(mine())}


def get(sub, query):
    return 200, snapshot()


def post(sub, data):
    if sub == 'settings':
        s = settings()
        for k in KINDS:
            if k in data:
                s[k] = bool(data[k])
        watch._write(SETTINGS, s)
        return 200, {'settings': s}
    return 404, {'error': 'unknown'}
