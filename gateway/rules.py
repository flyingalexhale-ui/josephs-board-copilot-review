"""Alex Hale's Board -- if-this-then-that rules (jb-lab, deep search idea 54).

WHEN something the board can see happens:
  price   a symbol crosses above / below a price
  move    a symbol is up / down X% since the 4 pm close (once per session)
  tag     a why-tag shows up on a symbol (OWN MOVE, HEAVY VOL, BREAKOUT... from the scanner and the tiles)
  mood    the Futures mood (since the 4 pm close) turns Risk-on or Risk-off
  time    a time of day, New York time, weekdays
THEN one of:
  push    a phone push
  ticket  a push that opens the Phone page's order ticket filled in (you check it and tap Send yourself)
  paper   a paper order at the board's price, tracked here with its profit or loss (nothing is sent to a broker)
There is no "send a live order by itself": a live order always goes through the ticket and your own tap, so the
ticket's checks (5% limit band, duplicate check, cool-down notes, loss limit) still apply. Every new rule starts as paper.

GET  /api/plus/rules                -> {rules, log, paper, tags}
POST /api/plus/rules/save {rule}    add or change one (id kept)       POST /api/plus/rules/delete {id}
POST /api/plus/rules/test {id}      run its action once now           POST /api/plus/rules/paper/close {id}
"""
import os
import secrets
import threading
import time
import urllib.request

import watch

DATA = watch.DATA
FILE = os.path.join(DATA, 'rules.json')
MIN = 60 * 1000
TAGS = ['OWN MOVE', 'MARKET-LED', 'SECTOR-LED', 'HEAVY VOL', 'THIN', 'BREAKOUT', 'BREAKDOWN', 'STRETCHED', 'TRENDING UP', 'TRENDING DOWN',
        'LAGS INDEX', 'AHEAD OF INDEX', 'HOT']
_lock = threading.Lock()
_st = {'started': False, 'prev': {}, 'mood': None}


def _now():
    return int(time.time() * 1000)


def _load():
    d = watch._read(FILE, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault('rules', [])
    d.setdefault('log', [])
    d.setdefault('paper', [])
    return d


def _save(d):
    d['log'] = d['log'][:200]
    d['paper'] = d['paper'][-300:]
    watch._write(FILE, d)


def _last(sym):
    with watch._lock:
        h = watch._px.get(sym)
        return (h[-1][1], h[-1][0]) if h else (None, None)


def _vs_close(sym, now):
    with watch._lock:
        h = list(watch._px.get(sym) or [])
    if not h or now - h[-1][0] > 15 * MIN:
        return None
    ref = watch.price_at(h, watch.last_close_ms(now))
    return watch.pct(h[-1][1], ref)


def _session_key(now):
    d = watch.et(now)
    m = d.hour * 60 + d.minute
    s = 'pre' if 240 <= m < 570 else 'reg' if 570 <= m < 960 else 'ah' if 960 <= m < 1200 else 'on'
    day = d.date().isoformat() if m >= 240 else (d.date().toordinal() - 1)
    return f'{day}-{s}'


def _tags(sym, now):
    out = set()
    try:
        import scanwhy
        t = (scanwhy.build(now).get('tags') or {}).get(sym) or {}
        out |= set(t.get('tags') or [])
    except Exception:
        pass
    try:
        import why
        t = why.tile(sym, now) or {}
        if (t.get('vol') or {}).get('label') == 'heavy':
            out.add('HEAVY VOL')
        if (t.get('stretch') or '').startswith('running hot'):
            out.add('HOT')
        if (t.get('stretch') or '').startswith('stretched'):
            out.add('STRETCHED')
        bar = t.get('bar') or {}
        mv = t.get('move')
        if bar and mv and abs(mv) >= 0.3:
            lead = max(bar, key=lambda k: (bar[k] or 0) * (1 if mv > 0 else -1))
            out.add({'market': 'MARKET-LED', 'sector': 'SECTOR-LED', 'own': 'OWN MOVE'}[lead])
    except Exception:
        pass
    return out


def _mood():
    try:
        import meters
        return ((meters.snapshot().get('futures') or {}).get('close') or {}).get('label')
    except Exception:
        return None


def describe(r):
    w, t = r.get('when') or {}, r.get('then') or {}
    k = w.get('type')
    s = (f"{w.get('sym')} crosses {w.get('dir', 'above')} {w.get('value')}" if k == 'price' else
         f"{w.get('sym')} is {'up' if w.get('dir') != 'below' else 'down'} {w.get('value')}% since the 4 pm close" if k == 'move' else
         f"{w.get('sym')} gets the {w.get('tag')} tag" if k == 'tag' else
         f"the Futures mood turns {w.get('mood')}" if k == 'mood' else
         f"it is {w.get('time')} ET (weekdays)" if k == 'time' else '?')
    a = t.get('type')
    d = ('send a push' if a == 'push' else
         f"open the ticket: {t.get('side', 'buy')} {t.get('qty') or ''} {t.get('sym') or w.get('sym') or ''} on {t.get('broker', 'ibkr').upper()}" if a == 'ticket' else
         f"paper {t.get('side', 'buy')} {t.get('qty') or ''} {t.get('sym') or w.get('sym') or ''}" if a == 'paper' else '?')
    return f'When {s}, {d}.'


def _check(r, now):
    """-> a short reason text when the rule fires now, else ''. Edge-based: it fires when the condition becomes true."""
    w = r.get('when') or {}
    k, sym = w.get('type'), (w.get('sym') or '').upper()
    key = r['id']
    if k == 'price':
        p, _ = _last(sym)
        try:
            v = float(w.get('value'))
        except (TypeError, ValueError):
            return ''
        if p is None:
            return ''
        state = p > v if w.get('dir', 'above') == 'above' else p < v
        prev = _st['prev'].get(key)
        _st['prev'][key] = state
        return f'{sym} {p:g} crossed {w.get("dir", "above")} {v:g}' if state and prev is False else ''
    if k == 'move':
        m = _vs_close(sym, now)
        try:
            v = abs(float(w.get('value')))
        except (TypeError, ValueError):
            return ''
        if m is None:
            return ''
        hit = m >= v if w.get('dir') != 'below' else m <= -v
        sk = _session_key(now)
        if hit and r.get('fired_key') != sk:
            r['fired_key'] = sk
            return f'{sym} is {m:+.1f}% since the 4 pm close'
        return ''
    if k == 'tag':
        has = (w.get('tag') or '') in _tags(sym, now)
        prev = _st['prev'].get(key)
        _st['prev'][key] = has
        return f'{sym} now shows {w.get("tag")}' if has and prev is False else ''
    if k == 'mood':
        m = _st['mood']
        return f'Futures mood is now {m}' if m and m == w.get('mood') and _st.get('mood_prev') not in (None, m) else ''
    if k == 'time':
        d = watch.et(now)
        if d.weekday() >= 5:
            return ''
        hhmm = d.strftime('%H:%M')
        dk = d.date().isoformat()
        if hhmm == (w.get('time') or '') and r.get('fired_key') != dk:
            r['fired_key'] = dk
            return f'It is {hhmm} ET'
    return ''


def _push(title, text, tags, click=''):
    t = watch.topic()
    if not t:
        return 'no JB_NTFY_TOPIC in settings.env'
    h = {'Title': title.encode('ascii', 'ignore').decode(), 'Tags': tags, 'Priority': watch._priority(tags)}
    if click:
        h['Click'] = click
    try:
        urllib.request.urlopen(urllib.request.Request('https://ntfy.sh/' + t, data=text.encode('utf-8'), method='POST', headers=h),
                               timeout=15, context=watch._ctx()).read()
        return ''
    except Exception as e:
        return str(e)[:200]


def _phone_url():
    try:
        import alerts2
        return alerts2._find_phone_url() or ''
    except Exception:
        return ''


def act(d, r, reason, now, test=False):
    t, w = r.get('then') or {}, r.get('when') or {}
    a = t.get('type') or 'push'
    sym = (t.get('sym') or w.get('sym') or '').upper()
    name = r.get('name') or 'Rule'
    note = ' (test)' if test else ''
    res = ''
    if a == 'push':
        res = _push(f'Rule: {name}{note}', f'{reason}. {describe(r)}', 'dart')
    elif a == 'ticket':
        side, qty, br = t.get('side') or 'buy', int(t.get('qty') or 0), t.get('broker') or 'ibkr'
        link = f"{_phone_url()}/phone.html#tk={sym}:{side}:{qty}:{br}" if _phone_url() else ''
        res = _push(f'Rule: {name}{note}', f'{reason}. Tap to open the ticket: {side} {qty or ""} {sym} on {br.upper()}. '
                    'Check it, then tap Send yourself.', 'dart', link)
    elif a == 'paper':
        p, _ = _last(sym)
        side, qty = t.get('side') or 'buy', int(t.get('qty') or 0) or 1
        if p is None:
            res = f'no board price for {sym}'
        else:
            d['paper'].append({'id': secrets.token_hex(4), 'rule': r['id'], 'name': name, 'sym': sym, 'side': side, 'qty': qty,
                               'price': p, 'at': now, 'open': True, 'test': test})
            res = _push(f'Rule: {name} (paper){note}', f'{reason}. Paper {side} {qty} {sym} at {p:g}. Nothing was sent to a broker.', 'memo')
    d['log'].insert(0, {'at': now, 'rule': r['id'], 'name': name, 'reason': reason, 'action': a, 'test': test, 'push_error': res})
    r['last_fired'] = now
    return res


def paper_view(d):
    out = []
    for o in d['paper']:
        p, _ = _last(o['sym'])
        cur = o.get('exit') or p
        pl = None if cur is None else (cur - o['price']) * o['qty'] * (1 if o['side'] == 'buy' else -1)
        out.append(dict(o, now=cur, pl=None if pl is None else round(pl, 2)))
    return list(reversed(out))


def step():
    now = _now()
    m = _mood()
    _st['mood_prev'], _st['mood'] = _st['mood'], m
    with _lock:
        d = _load()
        changed = False
        for r in d['rules']:
            if not r.get('on'):
                continue
            try:
                why_ = _check(r, now)
            except Exception as e:
                why_ = ''
                r['error'] = str(e)[:100]
            if why_:
                if now - (r.get('last_fired') or 0) < int(r.get('quiet_min') or 30) * MIN:
                    continue
                act(d, r, why_, now)
                changed = True
            elif r.get('when', {}).get('type') in ('move', 'time') and r.get('fired_key'):
                changed = True
        if changed:
            _save(d)


def _loop():
    time.sleep(60)
    while True:
        try:
            step()
        except Exception as e:
            print(f'rules: {e!r}', flush=True)
        time.sleep(30)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_loop, name='rules', daemon=True).start()


def _clean(r):
    w, t = r.get('when') or {}, r.get('then') or {}
    k = w.get('type')
    if k not in ('price', 'move', 'tag', 'mood', 'time'):
        raise ValueError('pick what to watch')
    sym = ''.join(ch for ch in str(w.get('sym') or '').upper() if ch.isalnum() or ch in '.=-')[:12]
    if k in ('price', 'move', 'tag') and not sym:
        raise ValueError('which symbol?')
    if k in ('price', 'move'):
        float(w.get('value'))
    if k == 'tag' and w.get('tag') not in TAGS:
        raise ValueError('pick a tag')
    if k == 'mood' and w.get('mood') not in ('Risk-on', 'Risk-off', 'Mixed'):
        raise ValueError('pick a mood')
    if k == 'time' and not (len(str(w.get('time') or '')) == 5 and str(w['time'])[2] == ':'):
        raise ValueError('time like 19:30')
    a = t.get('type')
    if a not in ('push', 'ticket', 'paper'):
        raise ValueError('pick what to do')
    tsym = ''.join(ch for ch in str(t.get('sym') or sym).upper() if ch.isalnum() or ch in '.=-')[:12]
    if a in ('ticket', 'paper') and not tsym:
        raise ValueError('which symbol to trade?')
    return {'id': r.get('id') or secrets.token_hex(4), 'name': str(r.get('name') or '')[:60] or 'Rule', 'on': bool(r.get('on', True)),
            'quiet_min': max(1, min(1440, int(r.get('quiet_min') or 30))),
            'when': {'type': k, 'sym': sym, 'dir': w.get('dir') if w.get('dir') in ('above', 'below') else 'above', 'value': w.get('value'),
                     'tag': w.get('tag'), 'mood': w.get('mood'), 'time': w.get('time')},
            'then': {'type': a, 'sym': tsym, 'side': 'sell' if t.get('side') == 'sell' else 'buy', 'qty': max(0, min(100000, int(t.get('qty') or 0))),
                     'broker': 'webull' if t.get('broker') == 'webull' else 'ibkr'}}


def get(sub, query):
    start()
    with _lock:
        d = _load()
        rules = [dict(r, text=describe(r)) for r in d['rules']]
        return 200, {'rules': rules, 'log': d['log'][:60], 'paper': paper_view(d)[:100], 'tags': TAGS, 'mood': _st['mood'],
                     'phone_url': bool(_phone_url()), 'push': bool(watch.topic())}


def post(sub, data):
    start()
    data = data or {}
    with _lock:
        d = _load()
        if sub == 'save':
            try:
                r = _clean(data.get('rule') or {})
            except (ValueError, TypeError) as e:
                return 400, {'error': str(e) or 'check the boxes'}
            old = [x for x in d['rules'] if x['id'] == r['id']]
            if old:
                r['last_fired'] = old[0].get('last_fired')
                d['rules'] = [r if x['id'] == r['id'] else x for x in d['rules']]
            else:
                d['rules'].append(r)
            _st['prev'].pop(r['id'], None)
            _save(d)
            return 200, {'ok': True, 'rule': dict(r, text=describe(r))}
        if sub == 'delete':
            d['rules'] = [x for x in d['rules'] if x['id'] != data.get('id')]
            _save(d)
            return 200, {'ok': True}
        if sub == 'test':
            r = [x for x in d['rules'] if x['id'] == data.get('id')]
            if not r:
                return 404, {'error': 'no such rule'}
            err = act(d, r[0], 'Test run', _now(), test=True)
            _save(d)
            return 200, {'ok': not err, 'error': err}
        if sub == 'paper/close':
            for o in d['paper']:
                if o['id'] == data.get('id') and o.get('open'):
                    p, _ = _last(o['sym'])
                    o['open'], o['exit'], o['closed_at'] = False, p or o['price'], _now()
            _save(d)
            return 200, {'ok': True}
    return 404, {'error': 'unknown'}
