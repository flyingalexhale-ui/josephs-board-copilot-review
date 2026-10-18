"""News, crowd and tape feeds from the final-ideas list (jb-feeds), running inside the gateway.

  IBKR's free newswire through your IB Gateway: Briefing.com analyst actions and market columns,
     Dow Jones (needs the news boxes ticked in IB Gateway, see below)                            (idea 14)
  Reddit mention rank from ApeWisdom (WallStreetBets, stocks, options...), with 24 h change      (idea 15)
  Polymarket odds (Fed, recession, tariffs...) next to Kalshi's                                   (idea 16)
  big policy posts: the President's Truth Social posts and White House news, best effort         (idea 17)
  Bluesky $TICKER buzz (only with a free Bluesky account in settings.env)                        (idea 18)
  buy vs sell pressure from the IBKR bridge's free trades and best bid / ask                      (idea 20)
  Bitcoin order book from Gemini (Yahoo price as the backup), for the weekend                     (idea 22)
  price cross-check: IBKR vs Webull on the same symbol; the algos skip new entries while
     they disagree (data/price_disagree.json)                                                      (idea 24)
  top news, ranked and merged from every feed on the board, repeats removed                       (idea 46)

  GET  /api/plus/feeds              everything for the Night desk / World tab
  GET  /api/plus/feeds/tiles        flow, cross-check and Reddit rank per symbol (board tiles)
  GET  /api/plus/feeds/top          the ranked news list
  GET  /api/plus/feeds/btc          the Bitcoin order book
  POST /api/plus/feeds/settings     {news_push, reddit_push, policy_push, poly_push, check_push}
IBKR news: the three free feeds must be on your IBKR account (Client Portal: Settings > Research
Subscriptions); without them the IBKR news row says what to do.
Bluesky: add JB_BSKY_HANDLE=yourname.bsky.social and JB_BSKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
(Bluesky app: Settings > Privacy and security > App passwords) to settings.env.
Files: data/feeds.json, data/feeds_settings.json, data/price_disagree.json.
"""
import asyncio
import html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'feeds.json')
SETTINGS = os.path.join(DATA, 'feeds_settings.json')
DISAGREE = os.path.join(DATA, 'price_disagree.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
DEFAULTS = {'news_push': True, 'reddit_push': True, 'policy_push': True, 'poly_push': False, 'check_push': True}
IB_NEWS = 'BRFUPDN+BRFG+DJNL'
IB_NEWS_NAMES = {'BRFUPDN': 'Briefing.com analyst actions', 'BRFG': 'Briefing.com', 'DJNL': 'Dow Jones'}
POLICY_WORDS = ('tariff', 'fed', 'powell', 'interest rate', 'rates', 'china', 'trade deal', 'sanction', 'executive order',
                'chip', 'semiconductor', 'oil', 'stock market', 'dow', 'nasdaq', 'crypto', 'bitcoin', 'japan', 'mexico', 'canada', 'europe', 'tax')
POLY_WORDS = ('fed', 'rate', 'recession', 'tariff', 'inflation', 'cpi', 'shutdown', 'powell', 'gdp', 'unemployment', 'election', 'bitcoin', 's&p', 'nasdaq')
HOT_WORDS = {'halt': 3, 'halted': 3, 'guidance': 2, 'fda': 2, 'tariff': 2, 'downgrade': 2, 'upgrade': 2, 'beats': 2, 'misses': 2, 'miss': 1,
             'investigation': 2, 'sec ': 1, 'acquire': 2, 'acquisition': 2, 'merger': 2, 'bankruptcy': 3, 'recall': 2, 'lawsuit': 1,
             'fed': 1, 'powell': 2, 'cpi': 2, 'jobs report': 2, 'payrolls': 2, 'rate cut': 2, 'rate hike': 2, 'offering': 2, 'buyback': 1,
             'raises': 1, 'cuts': 1, 'plunge': 1, 'soar': 1, 'surge': 1, 'crash': 2, 'record': 1}
_lock = threading.Lock()
_c = {}
_st = {'started': False, 'errors': {}, 'last': {}, 'flow': {}, 'dis': {}, 'bsky': {}, 'ib_news_ok': None}



def _web(u):
    """Only http/https links reach the pages (review 2026-10-11, second check): anything else becomes no link."""
    u = str(u or '').strip()
    return u if u.lower().startswith(('http://', 'https://')) else ''

def _now():
    return int(time.time() * 1000)


def settings():
    v = watch._read(SETTINGS, {})
    return dict(DEFAULTS, **(v if isinstance(v, dict) else {}))


def _load():
    global _c
    v = watch._read(CACHE, {})
    _c = v if isinstance(v, dict) else {}
    for k in ('ape', 'poly', 'policy', 'ibnews', 'btc', 'seen', 'poly_hist'):
        _c.setdefault(k, {} if k in ('seen', 'poly_hist', 'ape', 'btc') else [])


def _save():
    with _lock:
        watch._write(CACHE, _c)


def _err(k, e):
    _st['errors'][k] = f'{str(e)[:140]} ({watch.et(_now()):%a %I:%M %p} ET)'
    print(f'feeds: {k}: {e!r}', flush=True)


def _ok(k):
    _st['errors'].pop(k, None)


def _http(url, timeout=20, headers=None, chrome=False, data=None):
    if chrome:
        try:
            import world
            if world._browser is not None and data is None:
                r = world._browser.Session(impersonate='chrome').get(url, timeout=timeout, headers=headers or {})
                if r.status_code >= 400:
                    raise OSError(f'HTTP Error {r.status_code}')
                return r.content.decode('utf-8', 'replace')
        except OSError:
            raise
        except Exception:
            pass
    h = {'User-Agent': UA, 'Accept': '*/*'}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data, method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout, context=watch._ctx()) as r:
        return r.read(6_000_000).decode('utf-8', 'replace')


def _json(url, **k):
    return json.loads(_http(url, **k))


def mine():
    try:
        import webwatch
        return webwatch.mine()
    except Exception:
        return {}


def held():
    try:
        import mdata
        return set(mdata.held())
    except Exception:
        return set()


def _seen(key):
    """True the first time a key is seen (and remembers it for 3 days)."""
    s = _c['seen']
    if key in s:
        return False
    s[key] = _now()
    if len(s) > 3000:
        cut = _now() - 3 * 86400000
        for k in [k for k, v in s.items() if v < cut]:
            del s[k]
    return True


# ------------------------------------------------------------------ bridges: flow (20) and cross-check (24)
def _flow(m):
    sym = m.get('symbol')
    if not sym:
        return
    now = _now()
    with _lock:
        lst = _st['flow'].setdefault(sym, [])
        lst.append([int(m.get('ts') or now), float(m.get('buy') or 0), float(m.get('sell') or 0), float(m.get('mid') or 0)])
        cut = now - 30 * 60000
        while lst and lst[0][0] < cut:
            lst.pop(0)


def flow(sym, minutes=15):
    """{'buy', 'sell', 'mid', 'pct'}: shares traded at the ask (buyers) vs at the bid (sellers)."""
    cut = _now() - minutes * 60000
    with _lock:
        lst = [x for x in (_st['flow'].get(sym) or []) if x[0] >= cut]
    b, s, m = sum(x[1] for x in lst), sum(x[2] for x in lst), sum(x[3] for x in lst)
    if b + s < 1:
        return None
    out = {'buy': int(b), 'sell': int(s), 'mid': int(m), 'pct': round(b / (b + s) * 100)}
    try:
        import night
        q = night._q.get(sym)
        if q and _now() - q['ts'] < 5 * 60000 and (q.get('bs') or q.get('as')):
            out['book'] = round(q['bs'] / ((q['bs'] or 0) + (q['as'] or 0) or 1) * 100)
    except Exception:
        pass
    return out


def _tick(src, sym, p, ts, source):
    if not sym or not isinstance(p, (int, float)) or p <= 0 or 'delay' in str(source or '').lower():
        return
    _st['last'][(src, sym)] = (int(ts or _now()), float(p))


async def _listen(name, port, token_env):
    import websockets
    delay = 2
    while True:
        try:
            async with websockets.connect(f'ws://127.0.0.1:{port}', max_size=2 ** 22) as ws:
                tok = os.environ.get(token_env, '')
                if tok:
                    await ws.send(json.dumps({'type': 'auth', 'token': tok}))
                delay = 2
                async for raw in ws:
                    head = raw[:40]
                    if '"tick' not in head and '"flow"' not in head:
                        continue
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    k = m.get('type')
                    if k == 'ticks':
                        for t in m.get('ticks', []):
                            _tick(name, t.get('symbol'), t.get('price'), t.get('ts'), t.get('source'))
                    elif k == 'tick':
                        _tick(name, m.get('symbol'), m.get('price'), m.get('ts'), m.get('source'))
                    elif k == 'flow':
                        _flow(m)
        except Exception:
            pass
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


def _bridges():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(asyncio.gather(_listen('ibkr', 8765, 'IBKR_BRIDGE_TOKEN'), _listen('webull', 8766, 'WEBULL_BRIDGE_TOKEN')))


def crosscheck():
    """Symbols where IBKR and Webull both have a fresh price that differs by more than 0.75%
    (twice in a row, 20 s apart). Written to data/price_disagree.json for the algos."""
    now = _now()
    syms = {s for (src, s) in list(_st['last']) if src == 'ibkr'} & {s for (src, s) in list(_st['last']) if src == 'webull'}
    out = {}
    for s in syms:
        a, b = _st['last'].get(('ibkr', s)), _st['last'].get(('webull', s))
        if not a or not b or now - a[0] > 60000 or now - b[0] > 60000 or abs(a[0] - b[0]) > 30000:
            _st['dis'].pop(s, None)
            continue
        diff = abs(a[1] / b[1] - 1) * 100
        if diff > 0.75:
            d = _st['dis'].setdefault(s, {'since': now, 'n': 0})
            d['n'] += 1
            d.update(ibkr=a[1], webull=b[1], diff=round(diff, 2))
            if d['n'] >= 2:
                out[s] = d
        else:
            _st['dis'].pop(s, None)
    old = watch._read(DISAGREE, {})
    if out or old:
        watch._write(DISAGREE, {s: dict(d, at=now) for s, d in out.items()})
    cfg = settings()
    for s, d in out.items():
        if d['n'] == 2 and cfg['check_push'] and _seen(f'dis {s} {now // 3600000}'):
            watch.push_later(f'{s}: prices disagree', f"IBKR says {d['ibkr']:g}, Webull says {d['webull']:g} ({d['diff']}% apart). One feed may be stuck or wrong; "
                             'the algos skip new entries in it until they agree again.', 'warning')
    return out


# ------------------------------------------------------------------ Gemini Bitcoin book (22)
def fetch_btc():
    try:
        body = _json('https://api.gemini.com/v1/book/btcusd?limit_bids=500&limit_asks=500', timeout=15)
        bids = [(float(x['price']), float(x['amount'])) for x in body.get('bids') or []]
        asks = [(float(x['price']), float(x['amount'])) for x in body.get('asks') or []]
        if not bids or not asks:
            raise ValueError('empty book')
        bb, ba = bids[0][0], asks[0][0]
        mid = (bb + ba) / 2

        def within(side, pct):
            return round(sum(a for p, a in side if abs(p / mid - 1) * 100 <= pct), 2)

        def walls(side):
            b = {}
            for p, a in side:
                if abs(p / mid - 1) <= 0.05:
                    k = round(p / 250) * 250                      # $250 buckets
                    b[k] = b.get(k, 0) + a
            return [{'p': k, 'btc': round(v, 2)} for k, v in sorted(b.items(), key=lambda kv: -kv[1])[:3]]
        b1, a1 = within(bids, 1), within(asks, 1)
        _c['btc'] = {'at': _now(), 'src': 'Gemini', 'bid': bb, 'ask': ba, 'mid': round(mid, 2), 'bid1': b1, 'ask1': a1, 'bid2': within(bids, 2), 'ask2': within(asks, 2),
                     'tilt': round(b1 / (b1 + a1) * 100) if b1 + a1 else None, 'bid_walls': walls(bids), 'ask_walls': walls(asks)}
        _ok('btc')
    except Exception as e:
        _err('btc', e)
        try:
            import world
            q = world._quotes.get('BTC-USD') or {}
            if q.get('last'):
                _c['btc'] = {'at': _now(), 'src': 'Yahoo (Gemini did not answer: price only)', 'mid': q['last'], 'since_close': q.get('since_close')}
        except Exception:
            pass
    try:
        import world
        q = world._quotes.get('BTC-USD') or {}
        if q.get('since_close') is not None:
            _c['btc']['since_close'] = q['since_close']
    except Exception:
        pass


# ------------------------------------------------------------------ ApeWisdom (15)
def fetch_ape():
    rows = {}
    for page in (1, 2, 3):
        body = _json(f'https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}', timeout=20)
        for r in body.get('results') or []:
            t = str(r.get('ticker') or '').upper()
            if not t:
                continue
            def n(k):
                try:
                    return int(float(r.get(k)))
                except (TypeError, ValueError):
                    return None
            rows[t] = {'rank': n('rank'), 'mentions': n('mentions'), 'up': n('upvotes'), 'rank_24h': n('rank_24h_ago'), 'mentions_24h': n('mentions_24h_ago'),
                       'name': html.unescape(str(r.get('name') or ''))[:40]}
        if page >= int(body.get('pages') or 1):
            break
        time.sleep(2)
    if not rows:
        raise ValueError('no results')
    _c['ape'] = {'at': _now(), 'rows': rows}
    cfg, my = settings(), mine()
    for t, r in rows.items():
        if t not in my or not r['mentions'] or r['mentions'] < 15:
            continue
        before = r['mentions_24h'] or 0
        jump = r['mentions'] >= max(2 * before, before + 15)
        if jump and cfg['reddit_push'] and _seen(f'ape {t} {watch.et(_now()).date()}'):
            watch.push_later(f'{t}: Reddit buzz jump', f"{t} has {r['mentions']} Reddit mentions in 24 h (was {before}), rank {r['rank']} (was {r['rank_24h'] or '?'}). Source: ApeWisdom.", 'speech_balloon')


# ------------------------------------------------------------------ Polymarket (16)
def fetch_poly():
    body = _json('https://gamma-api.polymarket.com/events?closed=false&active=true&limit=200&order=volume24hr&ascending=false', timeout=25)
    out = []
    for ev in body if isinstance(body, list) else []:
        title = str(ev.get('title') or '')
        low = title.lower()
        if not any(w in low for w in POLY_WORDS):
            continue
        mk = []
        for m in ev.get('markets') or []:
            try:
                outs = json.loads(m.get('outcomes') or '[]')
                prices = [float(x) for x in json.loads(m.get('outcomePrices') or '[]')]
            except (ValueError, TypeError):
                continue
            if not prices or m.get('closed'):
                continue
            yes = prices[0] if outs and str(outs[0]).lower() == 'yes' else max(prices)
            mk.append({'q': str(m.get('groupItemTitle') or m.get('question') or '')[:80], 'p': round(yes * 100), 'id': str(m.get('id') or '')})
        if not mk:
            continue
        mk.sort(key=lambda x: -x['p'])
        out.append({'title': title[:120], 'slug': ev.get('slug') or '', 'vol24': ev.get('volume24hr'), 'markets': mk[:5], 'end': (ev.get('endDate') or '')[:10]})
    out = out[:12]
    hist = _c['poly_hist']
    hour = str(_now() // 3600000)
    hist[hour] = {m['id']: m['p'] for e in out for m in e['markets']}
    for k in sorted(hist, key=int)[:-30]:
        del hist[k]
    old = hist.get(str(_now() // 3600000 - 6)) or {}
    cfg = settings()
    for e in out:
        for m in e['markets']:
            if m['id'] in old:
                m['ch6h'] = m['p'] - old[m['id']]
                if abs(m['ch6h']) >= 10 and cfg['poly_push'] and _seen(f"poly {m['id']} {hour}"):
                    watch.push_later('Polymarket odds jump', f"{e['title']}: {m['q']} now {m['p']}% ({m['ch6h']:+d} in 6 h).", 'game_die')
    _c['poly'] = {'at': _now(), 'events': out}


# ------------------------------------------------------------------ policy posts (17)
def _strip(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def fetch_policy():
    items, errs = [], []
    try:                                       # Truth Social's public API (may be blocked: best effort)
        body = _json('https://truthsocial.com/api/v1/accounts/107780257626128497/statuses?exclude_replies=true&limit=20', chrome=True,
                     headers={'Accept': 'application/json'})
        for s in body if isinstance(body, list) else []:
            txt = _strip(s.get('content'))
            if txt:
                items.append({'src': 'Truth Social', 'text': txt[:400], 'url': _web(s.get('url')), 'ts': _iso_ms(s.get('created_at'))})
    except Exception as e:
        errs.append(f'Truth Social: {str(e)[:80]}')
        try:                                   # a public mirror of the same posts
            for it in _rss(_http('https://www.trumpstruth.org/feed', chrome=True)):
                items.append(dict(it, src='Truth Social (via trumpstruth.org)'))
        except Exception as e2:
            errs.append(f'mirror: {str(e2)[:80]}')
    try:
        for it in _rss(_http('https://www.whitehouse.gov/news/feed/', chrome=True))[:20]:
            items.append(dict(it, src='White House'))
    except Exception as e:
        errs.append(f'White House: {str(e)[:80]}')
    if not items:
        raise ValueError('; '.join(errs) or 'nothing')
    my = mine()
    cfg = settings()
    out = []
    for it in sorted(items, key=lambda x: -(x['ts'] or 0))[:40]:
        low = it['text'].lower()
        hits = [w for w in POLICY_WORDS if re.search(r'\b' + re.escape(w) + r'\b', low)]
        hits += [s for s in my if len(s) >= 3 and re.search(r'(\$|\b)' + re.escape(s) + r'\b', it['text'])]
        it['hits'] = hits[:6]
        out.append(it)
        if hits and cfg['policy_push'] and it['ts'] and _now() - it['ts'] < 3 * 3600000 and _seen('policy ' + (it['url'] or it['text'][:80])):
            watch.push_later(f"{it['src'].split(' (')[0]} post: {', '.join(hits[:3])}", it['text'][:300], 'loudspeaker')
    _c['policy'] = {'at': _now(), 'items': out, 'errors': errs}


def _iso_ms(s):
    try:
        return int(datetime.fromisoformat(str(s).replace('Z', '+00:00')).timestamp() * 1000)
    except Exception:
        return None


def _rss(body):
    import news
    return [{'text': i['title'], 'url': i['link'], 'ts': i['ts'] * 1000} for i in news._parse_rss(body, '')]


# ------------------------------------------------------------------ Bluesky (18)
def _bsky_token():
    h, pw = os.environ.get('JB_BSKY_HANDLE', '').strip(), os.environ.get('JB_BSKY_APP_PASSWORD', '').strip()
    if not h or not pw:
        return None
    tok = _st['bsky'].get('tok')
    if tok and time.time() - _st['bsky'].get('at', 0) < 3600:
        return tok
    body = json.loads(_http('https://bsky.social/xrpc/com.atproto.server.createSession', headers={'Content-Type': 'application/json'},
                            data=json.dumps({'identifier': h, 'password': pw}).encode()))
    _st['bsky'].update(tok=body.get('accessJwt'), at=time.time())
    return _st['bsky']['tok']


def fetch_bsky():
    tok = _bsky_token()
    if not tok:
        _c['bsky'] = {'at': _now(), 'off': 'Add JB_BSKY_HANDLE and JB_BSKY_APP_PASSWORD to settings.env to switch this on (a free Bluesky account).'}
        return
    since = datetime.fromtimestamp(time.time() - 86400, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    rows = dict((_c.get('bsky') or {}).get('rows') or {})
    syms = sorted(mine())[:40]
    start = int(_c.get('bsky_i') or 0) % max(1, len(syms))
    for s in (syms[start:] + syms[:start])[:12]:                   # 12 symbols per round
        n, cursor = 0, ''
        for _ in range(3):
            url = 'https://bsky.social/xrpc/app.bsky.feed.searchPosts?q=' + urllib.parse.quote('$' + s) + '&since=' + since + '&limit=100' + ('&cursor=' + cursor if cursor else '')
            body = json.loads(_http(url, headers={'Authorization': 'Bearer ' + tok}))
            n += len(body.get('posts') or [])
            cursor = body.get('cursor') or ''
            if not cursor or len(body.get('posts') or []) < 100:
                break
            time.sleep(1)
        rows[s] = {'n': n, 'at': _now()}
        time.sleep(1.5)
    _c['bsky_i'] = start + 12
    _c['bsky'] = {'at': _now(), 'rows': rows}


# ------------------------------------------------------------------ IBKR newswire (14)
def fetch_ibnews():
    import iblink
    syms = sorted(mine())[:30]
    hot = held()

    async def go(ib):
        provs = await asyncio.wait_for(ib.reqNewsProvidersAsync(), 10)
        have = [p.code for p in provs or []]
        use = '+'.join(c for c in IB_NEWS.split('+') if c in have)
        if not use:
            return have, []
        out = []
        start = datetime.fromtimestamp(time.time() - 2 * 86400, timezone.utc)
        for s in syms:
            try:
                c = await iblink.stock(ib, s)
                lst = await asyncio.wait_for(ib.reqHistoricalNewsAsync(c.conId, use, start, '', 10), 12)
                for n in lst or []:
                    out.append({'sym': s, 'src': IB_NEWS_NAMES.get(n.providerCode, n.providerCode), 'title': _strip(n.headline)[:300],
                                'id': n.articleId, 'ts': int(n.time.timestamp() * 1000) if hasattr(n.time, 'timestamp') else _now()})
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return have, out
    have, items = iblink.run(go, timeout=150)
    _st['ib_news_ok'] = bool(set(have) & set(IB_NEWS.split('+')))
    if not _st['ib_news_ok']:
        _c['ibnews'] = {'at': _now(), 'items': [], 'have': have,
                        'off': 'IB Gateway is not offering the free news feeds yet. On the IBKR website (Client Portal): Settings > Research Subscriptions, add the free '
                               '"Briefing.com Analyst Actions", "Briefing.com General Market Columns" and "Dow Jones Newsletters" if they are not there; '
                               'in IB Gateway, if Configure > Settings shows a News section, tick them there too. Then restart IB Gateway.'}
        return
    items.sort(key=lambda x: -x['ts'])
    cfg, primed = settings(), _c.get('ib_primed')
    _c['ib_primed'] = True                       # the first look only learns what is already there
    for it in items[:60]:
        new = _seen('ibn ' + str(it['id']))
        if new and primed and cfg['news_push'] and it['sym'] in hot and _now() - it['ts'] < 2 * 3600000:
            watch.push_later(f"{it['sym']}: {it['src']}", it['title'], 'newspaper')
    _c['ibnews'] = {'at': _now(), 'items': items[:120]}


# ------------------------------------------------------------------ ranked news (46)
def _words(t):
    return set(w for w in re.findall(r'[a-z0-9$]+', t.lower()) if len(w) > 2)


def top_news(limit=60):
    my, hot = mine(), held()
    try:
        import edge
        inside = {h['sym'] for b in ('QQQ', 'SOXX', 'SPY') for h in edge.holdings(b)}
    except Exception:
        inside = set()
    items = []
    try:
        import news
        with news._lock:
            for sym, lst in news._state['items'].items():
                for i in lst:
                    items.append({'title': i['title'], 'url': i['link'], 'src': i.get('source') or 'News', 'ts': i['ts'] * 1000, 'sym': sym})
    except Exception:
        pass
    try:
        import webwatch
        with webwatch._lock:
            for i in webwatch._st['items'][-300:]:
                items.append({'title': i['title'], 'url': _web(i.get('url')), 'src': i.get('src') or i.get('kind'), 'ts': i['at'], 'sym': i.get('sym') or ''})
    except Exception:
        pass
    try:
        import macro
        for i in list(macro._st.get('news') or [])[:80]:
            items.append({'title': i['title'], 'url': _web(i.get('link')), 'src': i.get('source') or 'Macro', 'ts': i['ts'] * 1000, 'sym': ''})
    except Exception:
        pass
    for i in (_c.get('ibnews') or {}).get('items') or []:
        items.append({'title': i['title'], 'url': '', 'src': i['src'], 'ts': i['ts'], 'sym': i['sym']})
    for i in (_c.get('policy') or {}).get('items') or []:
        if i.get('hits'):
            items.append({'title': i['text'][:200], 'url': _web(i.get('url')), 'src': i['src'], 'ts': i['ts'] or 0, 'sym': ''})
    now = _now()
    items = [i for i in items if i['title'] and now - (i['ts'] or 0) < 2 * 86400000]
    for i in items:
        low, s = i['title'].lower(), 0.0
        if i['sym'] in hot:
            s += 4
        elif i['sym'] in my:
            s += 2
        elif i['sym'] in inside:
            s += 1.5
        for t in list(hot) + list(my):
            if t != i['sym'] and len(t) >= 3 and re.search(r'\b' + re.escape(t) + r'\b', i['title']):
                s += 1.5
                break
        s += sum(v for w, v in HOT_WORDS.items() if w in low)
        if any(k in i['src'] for k in ('Briefing', 'Dow Jones', 'Federal Reserve', 'SEC')):
            s += 1
        age_h = (now - i['ts']) / 3600000
        i['score'] = round(s - age_h * 0.35, 2)
    items.sort(key=lambda i: -i['score'])
    out, kept = [], []
    for i in items:
        w = _words(i['title'])
        dup = next((k for k in kept if w and len(w & k[0]) / max(1, len(w | k[0])) >= 0.55), None)
        if dup:
            dup[1]['also'] = dup[1].get('also', 0) + 1
            continue
        kept.append((w, i))
        out.append(i)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ loop + doorway
EVERY = {'btc': 60, 'ape': 900, 'poly': 900, 'policy': 180, 'bsky': 3600, 'ibnews': 300}
JOBS = {'btc': fetch_btc, 'ape': fetch_ape, 'poly': fetch_poly, 'policy': fetch_policy, 'bsky': fetch_bsky, 'ibnews': fetch_ibnews}


def _loop():
    time.sleep(30)
    last = {}
    while True:
        for k, fn in JOBS.items():
            if time.time() - last.get(k, 0) < EVERY[k]:
                continue
            last[k] = time.time()
            try:
                fn()
                _ok(k)
            except Exception as e:
                _err(k, e)
            _save()
        for _ in range(3):
            try:
                crosscheck()
            except Exception as e:
                print(f'feeds: crosscheck: {e!r}', flush=True)
            time.sleep(20)


def start():
    if _st['started']:
        return
    _st['started'] = True
    _load()
    threading.Thread(target=_bridges, name='feeds-bridges', daemon=True).start()
    threading.Thread(target=_loop, name='feeds', daemon=True).start()


def tiles():
    out = {}
    syms = {s for (_, s) in list(_st['last'])} | set(_st['flow'])
    ape = (_c.get('ape') or {}).get('rows') or {}
    dis = watch._read(DISAGREE, {})
    for s in syms | set(mine()):
        r = {}
        f = flow(s)
        if f:
            r['flow'] = f
        if s in dis and _now() - dis[s].get('at', 0) < 90000:
            r['disagree'] = dis[s]
        a = ape.get(s)
        if a and a.get('rank') and a['rank'] <= 100:
            r['reddit'] = a
        if r:
            out[s] = r
    return out


def snapshot():
    if not _c:
        _load()
    ape = (_c.get('ape') or {}).get('rows') or {}
    my = mine()
    reddit = sorted([dict(v, sym=k, mine=k in my) for k, v in ape.items() if k in my or (v.get('rank') or 999) <= 15], key=lambda x: x['rank'] or 999)[:30]
    return {'btc': _c.get('btc'), 'reddit': reddit, 'reddit_at': (_c.get('ape') or {}).get('at'), 'poly': _c.get('poly'), 'policy': _c.get('policy'),
            'bsky': _c.get('bsky'), 'ibnews': {k: v for k, v in (_c.get('ibnews') or {}).items() if k != 'items'},
            'disagree': watch._read(DISAGREE, {}), 'flow': {s: flow(s) for s in list(_st['flow'])[:60] if flow(s)},
            'settings': settings(), 'errors': _st['errors']}


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    if sub == 'tiles':
        return 200, tiles()
    if sub == 'top':
        return 200, {'items': top_news()}
    if sub == 'btc':
        return 200, _c.get('btc') or {}
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'settings':
        cur = settings()
        for k in DEFAULTS:
            if k in data:
                cur[k] = bool(data[k])
        watch._write(SETTINGS, cur)
        return 200, {'settings': cur}
    return 404, {'error': 'unknown'}
