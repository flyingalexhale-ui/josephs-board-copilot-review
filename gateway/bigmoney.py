"""Night desk "Big money" (jb-mdata), running inside the gateway. All free, read only.

  * Who owns it: % held by institutions and insiders, the biggest institutional holders and funds,
    and how much each added or cut in its latest report (Yahoo, from the quarterly 13F filings,
    which are up to 45 days old when published). Checked every 12 hours per stock.
  * Insider trades: company officers and directors buying or selling their own stock (Yahoo, from
    SEC Form 4), last 90 days.
  * Block trades: single trades of $1 million or 20,000 shares or more, as they print on the IBKR
    feed (needs jb-night's bridge update; thresholds JB_BLOCK_DOLLARS / JB_BLOCK_SHARES).
  * Dark pools: shares traded off-exchange on dark pools (ATS) per week, from FINRA's free weekly
    data (published 2-4 weeks late).
  * Politicians: stock trades reported by members of Congress (official House Clerk filings, and
    the Senate's eFD site), usually 2-6 weeks after the trade. Needs the pypdf library for the
    House PDFs (installed with this update).
Files: data/bigmoney_cache.json.
"""
import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'bigmoney_cache.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
HOUSE_INDEX = 'https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{y}FD.zip'
HOUSE_PDF = 'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{y}/{doc}.pdf'
SENATE = 'https://efdsearch.senate.gov'
FINRA_ATS = 'https://api.finra.org/data/group/otcMarket/name/weeklySummary'

_lock = threading.Lock()
_st = {'started': False, 'own': {}, 'ats': {}, 'house': {}, 'senate': [], 'senate_at': 0, 'house_at': 0, 'errors': {}, 'wait_until': 0}


def _now():
    return int(time.time() * 1000)


def _err(key, e):
    with _lock:
        _st['errors'][key] = str(e)[:160]
    if '429' in str(e) or 'Too Many' in str(e):
        _st['wait_until'] = time.time() + 600


def _http(url, data=None, headers=None, timeout=30, raw=False):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=watch._ctx()) as r:
        b = r.read(20_000_000)
    return b if raw else b.decode('utf-8', 'replace')


def _raw(v):
    return v.get('raw') if isinstance(v, dict) else v


def tracked():
    try:
        import mdata
        return mdata.tracked()
    except Exception:
        return {}


def _mine(info):
    return any(not w.startswith('Scanner') for w in info.get('where') or [])


# ------------------------------------------------------------------ ownership + insiders (Yahoo)
def fetch_own(sym):
    import world
    mods = 'majorHoldersBreakdown,institutionOwnership,fundOwnership,insiderTransactions'
    try:
        body = world._get('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + urllib.parse.quote(sym.replace('.', '-')) + '?modules=' + mods)
    except Exception as e:                            # jb-ownfix: funds have no ownership list (Yahoo says 404): not a problem
        if '404' not in str(e):
            raise
        return {'at': time.time(), 'none': True, 'inst_pct': None, 'inst_float_pct': None, 'insider_pct': None, 'inst_count': None,
                'inst': [], 'funds': [], 'insiders': []}
    res = ((body.get('quoteSummary') or {}).get('result') or [None])[0] or {}
    mh = res.get('majorHoldersBreakdown') or {}

    def holders(key):
        out = []
        for h in ((res.get(key) or {}).get('ownershipList') or [])[:8]:
            pct = _raw(h.get('pctHeld'))
            ch = _raw(h.get('pctChange'))
            out.append({'name': h.get('organization') or '', 'pct': round(pct * 100, 2) if pct is not None else None,
                        'chg': round(ch * 100, 1) if ch is not None else None, 'shares': _raw(h.get('position')),
                        'value': _raw(h.get('value')), 'date': (h.get('reportDate') or {}).get('fmt') or ''})
        return out
    ins = []
    cut = time.time() - 90 * 86400
    for t in ((res.get('insiderTransactions') or {}).get('transactions') or []):
        d = _raw(t.get('startDate')) or 0
        if d < cut:
            continue
        text = t.get('transactionText') or ''
        kind = 'buy' if re.search(r'purchase|buy', text, re.I) else 'sell' if re.search(r'sale|sold|sell', text, re.I) else 'other'
        ins.append({'name': (t.get('filerName') or '').title(), 'role': t.get('filerRelation') or '', 'text': text, 'kind': kind,
                    'shares': _raw(t.get('shares')), 'value': _raw(t.get('value')), 't': int(d) * 1000})
    pct = lambda k: round(_raw(mh.get(k)) * 100, 1) if _raw(mh.get(k)) is not None else None
    return {'at': time.time(), 'inst_pct': pct('institutionsPercentHeld'), 'inst_float_pct': pct('institutionsFloatPercentHeld'),
            'insider_pct': pct('insidersPercentHeld'), 'inst_count': _raw(mh.get('institutionsCount')),
            'inst': holders('institutionOwnership'), 'funds': holders('fundOwnership'), 'insiders': ins[:15]}


# ------------------------------------------------------------------ dark pools (FINRA weekly ATS data)
_ats_ok = [0]                    # which way of asking FINRA worked last


def fetch_ats(sym):
    filters = [{'compareType': 'EQUAL', 'fieldName': 'issueSymbolIdentifier', 'fieldValue': sym},
               {'compareType': 'EQUAL', 'fieldName': 'summaryTypeCode', 'fieldValue': 'ATS_W_SMBL'}]
    since = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
    tries = [{'compareFilters': filters, 'limit': 12, 'sortFields': ['-weekStartDate']},
             {'compareFilters': filters, 'limit': 500,
              'dateRangeFilters': [{'fieldName': 'weekStartDate', 'startDate': since, 'endDate': datetime.now().strftime('%Y-%m-%d')}]},
             {'compareFilters': filters, 'limit': 5000}]
    body, last = None, None
    for i in [_ats_ok[0]] + [j for j in range(len(tries)) if j != _ats_ok[0]]:   # FINRA refused the first way (HTTP 400)
        try:
            body = _http(FINRA_ATS, json.dumps(tries[i]).encode(), {'Content-Type': 'application/json', 'Accept': 'application/json'})
            _ats_ok[0] = i
            break
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 400:
                raise
    if body is None:
        raise last
    rows = json.loads(body) if body.strip().startswith('[') else []
    weeks = {}
    for r in rows:
        w = r.get('weekStartDate')
        if w:
            weeks[w] = weeks.get(w, 0) + int(r.get('totalWeeklyShareQuantity') or 0)
    out = [{'week': w, 'shares': v} for w, v in sorted(weeks.items())]
    return {'at': time.time(), 'weeks': out[-8:]}


# ------------------------------------------------------------------ politicians: House (official Clerk filings)
TX_RE = re.compile(r'\(([A-Z][A-Z.]{0,5})\)\s*\[(?:ST|OP|ET|EF)\][^\n]{0,120}?\s(P|S|S \(partial\)|E)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(\$[\d,]+(?:\s*-\s*\$[\d,]+)?|Over \$[\d,]+)', re.S)


def parse_ptr_text(text):
    """Stock trades from a House periodic transaction report's text."""
    flat = re.sub(r'[ \t]+', ' ', text.replace('\x00', ''))
    out = []
    for m in TX_RE.finditer(flat):
        sym, kind, tdate, ndate, amt = m.groups()
        out.append({'sym': sym, 'kind': {'P': 'buy', 'E': 'exchange'}.get(kind, 'sell'), 'date': _iso(tdate), 'reported': _iso(ndate), 'amount': amt.strip()})
    return out


def _iso(mdy):
    try:
        return datetime.strptime(mdy, '%m/%d/%Y').date().isoformat()
    except ValueError:
        return mdy


def pdf_text(b):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(b))
    return '\n'.join((p.extract_text() or '') for p in r.pages[:12])


def refresh_house():
    y = date.today().year
    z = zipfile.ZipFile(io.BytesIO(_http(HOUSE_INDEX.format(y=y), raw=True)))
    name = next(n for n in z.namelist() if n.lower().endswith('.xml'))
    root = ET.fromstring(z.read(name))
    filings = []
    for m in root.iter('Member'):
        g = lambda k: (m.findtext(k) or '').strip()
        if g('FilingType') != 'P':
            continue
        try:
            fd = datetime.strptime(g('FilingDate'), '%m/%d/%Y').date()
        except ValueError:
            continue
        if fd < date.today() - timedelta(days=60):
            continue
        filings.append({'doc': g('DocID'), 'name': f"{g('First')} {g('Last')}".strip(), 'where': g('StateDst'), 'filed': fd.isoformat()})
    filings.sort(key=lambda f: f['filed'], reverse=True)
    with _lock:
        have = dict(_st['house'])
    n = 0
    for f in filings:
        if f['doc'] in have or n >= 25:           # at most 25 new PDFs per round
            continue
        n += 1
        try:
            trades = parse_ptr_text(pdf_text(_http(HOUSE_PDF.format(y=y, doc=f['doc']), raw=True)))
        except ModuleNotFoundError:
            _err('house', 'the pypdf library is missing: run  cd ~/josephs-board && .venv/bin/python -m pip install pypdf')
            return
        except Exception as e:
            trades = []
            _err('house ' + f['doc'], e)
        with _lock:
            _st['house'][f['doc']] = dict(f, trades=trades, chamber='House')
        time.sleep(2)
    with _lock:
        keep = {f['doc'] for f in filings}
        _st['house'] = {k: v for k, v in _st['house'].items() if k in keep}
        _st['house_at'] = _now()
        _st['errors'].pop('house', None)


# ------------------------------------------------------------------ politicians: Senate (eFD search)
def refresh_senate():
    """The Senate site asks visitors to accept its terms first (a form), then lists periodic
    transaction reports; each electronic report is a web page with a table of trades."""
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=watch._ctx()))
    op.addheaders = [('User-Agent', UA)]
    home = op.open(SENATE + '/search/home/', timeout=30).read().decode('utf-8', 'replace')
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', home)
    if not tok:
        raise RuntimeError('Senate site layout changed')
    op.open(urllib.request.Request(SENATE + '/search/home/', data=urllib.parse.urlencode({'prohibition_agreement': '1', 'csrfmiddlewaretoken': tok.group(1)}).encode(),
                                   headers={'Referer': SENATE + '/search/home/'}), timeout=30).read()
    csrf = next((c.value for c in jar if c.name == 'csrftoken'), '')
    start = (date.today() - timedelta(days=45)).strftime('%m/%d/%Y 00:00:00')
    form = {'start': '0', 'length': '60', 'report_types': '[11]', 'filer_types': '[]', 'submitted_start_date': start,
            'submitted_end_date': '', 'candidate_state': '', 'senator_state': '', 'office_id': '', 'first_name': '', 'last_name': '',
            'csrfmiddlewaretoken': csrf}
    r = op.open(urllib.request.Request(SENATE + '/search/report/data/', data=urllib.parse.urlencode(form).encode(),
                                       headers={'Referer': SENATE + '/search/', 'X-CSRFToken': csrf}), timeout=30).read()
    rows = (json.loads(r).get('data') or [])
    out = []
    for row in rows[:40]:
        first, last, link_html, filed = row[0], row[1], row[3], row[4]
        m = re.search(r'href="([^"]+)"', link_html or '')
        if not m or '/ptr/' not in m.group(1):
            continue                                 # paper filings are scanned images: skipped
        page = op.open(SENATE + m.group(1), timeout=30).read().decode('utf-8', 'replace')
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S)[1:]:
            cells = [re.sub(r'<[^>]+>', ' ', c) for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
            cells = [re.sub(r'\s+', ' ', c).strip() for c in cells]
            if len(cells) < 8:
                continue
            sym = cells[3] if re.fullmatch(r'[A-Z.]{1,6}', cells[3] or '') else ''
            if not sym:
                continue
            kind = 'buy' if 'Purchase' in cells[6] else 'sell' if 'Sale' in cells[6] else 'exchange'
            out.append({'name': f'{first} {last}'.strip(), 'where': 'Senate', 'chamber': 'Senate', 'sym': sym, 'kind': kind,
                        'date': _iso(cells[1]), 'reported': _iso(filed) if '/' in filed else filed, 'amount': cells[7]})
        time.sleep(1.5)
    with _lock:
        _st['senate'], _st['senate_at'] = out, _now()
        _st['errors'].pop('senate', None)


def politicians():
    with _lock:
        house = list(_st['house'].values())
        senate = list(_st['senate'])
    trades = []
    for f in house:
        for t in f['trades']:
            trades.append(dict(t, name=f['name'], where=f['where'], chamber='House'))
    trades += senate
    trades.sort(key=lambda t: (t.get('reported') or '', t.get('date') or ''), reverse=True)
    return trades


# ------------------------------------------------------------------ background
def _save():
    with _lock:
        watch._write(CACHE, {k: _st[k] for k in ('own', 'ats', 'house', 'senate', 'senate_at', 'house_at')})


def _loop():
    last = {'house': 0, 'senate': 0, 'save': 0}
    while True:
        try:
            if time.time() < _st['wait_until']:
                time.sleep(30)
                continue
            info = tracked()
            did = False
            for sym, i in sorted(info.items(), key=lambda x: not _mine(x[1])):
                if not _mine(i):
                    continue
                with _lock:
                    o = _st['own'].get(sym)
                if not o or time.time() - o.get('at', 0) > 12 * 3600:
                    try:
                        r = fetch_own(sym)
                        with _lock:
                            _st['own'][sym] = r
                            _st['errors'].pop('own ' + sym, None)
                    except Exception as e:
                        _err('own ' + sym, e)
                        with _lock:
                            _st['own'][sym] = dict(o or {}, at=time.time() - 11 * 3600)
                    did = True
                    break
            if not did:
                for sym, i in sorted(info.items()):
                    if not _mine(i) or '=' in sym:
                        continue
                    with _lock:
                        a = _st['ats'].get(sym)
                    if not a or time.time() - a.get('at', 0) > 24 * 3600:
                        try:
                            r = fetch_ats(sym)
                            with _lock:
                                _st['ats'][sym] = r
                                _st['errors'].pop('dark pools', None)
                        except Exception as e:
                            _err('dark pools', e)
                            with _lock:
                                _st['ats'][sym] = dict(a or {}, at=time.time() - 18 * 3600)
                        did = True
                        break
            if time.time() - last['house'] > 6 * 3600:
                last['house'] = time.time()
                try:
                    refresh_house()
                except Exception as e:
                    _err('house', e)
                    last['house'] = time.time() - 6 * 3600 + 900   # try again in 15 min after a hiccup (jb-power)
                did = True
            if time.time() - last['senate'] > 6 * 3600:
                last['senate'] = time.time()
                try:
                    refresh_senate()
                except Exception as e:
                    _err('senate', e)
                    last['senate'] = time.time() - 6 * 3600 + 900   # try again in 15 min after a hiccup (jb-power)
                did = True
            if did and time.time() - last['save'] > 120:
                last['save'] = time.time()
                _save()
            time.sleep(5 if did else 60)
        except Exception as e:
            print(f'bigmoney error: {e!r}', flush=True)
            time.sleep(60)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
        old = watch._read(CACHE, {})
        for k in ('own', 'ats', 'house'):
            if isinstance(old.get(k), dict):
                _st[k] = old[k]
        _st['senate'] = old.get('senate') or []
        _st['senate_at'] = old.get('senate_at') or 0
        _st['house_at'] = old.get('house_at') or 0
    threading.Thread(target=_loop, name='bigmoney', daemon=True).start()


def blocks(info):
    try:
        import night
        lst = night.prints()
    except Exception:
        return []
    return [dict(p, value=round(p['price'] * p['size'])) for p in lst[:200] if p['sym'] in info][:60]


def snapshot():
    info = tracked()
    rows = []
    with _lock:
        own = dict(_st['own'])
        ats = dict(_st['ats'])
        errors = dict(_st['errors'])
        h_at, s_at = _st['house_at'], _st['senate_at']
    for s, i in sorted(info.items()):
        if not _mine(i):
            continue
        o, a = own.get(s) or {}, ats.get(s) or {}
        rows.append({'sym': s, 'kind': i['kind'], 'held': 'Held' in (i.get('where') or []),
                     'inst_pct': o.get('inst_pct'), 'insider_pct': o.get('insider_pct'), 'inst_count': o.get('inst_count'),
                     'inst': o.get('inst') or [], 'funds': o.get('funds') or [], 'insiders': o.get('insiders') or [],
                     'ats': a.get('weeks') or []})
    pol = politicians()
    mine = set(info)
    return {'at': _now(), 'rows': rows, 'blocks': blocks(info), 'politicians': pol[:120],
            'politicians_mine': [t for t in pol if t['sym'] in mine][:40], 'house_at': h_at, 'senate_at': s_at, 'errors': errors}


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    return 404, {'error': 'unknown'}
