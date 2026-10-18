"""Pick track record: each weekday at the close, the day's picks are saved (data/picks_history.json),
and later scored against what the price actually did by the next day's close.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
PATH = os.path.join(DATA, 'picks_history.json')
KEEP_DAYS = 120

_cache = {'hist': None, 'scored_key': None, 'record': None}


def _load():
    if _cache['hist'] is None:
        try:
            with open(PATH) as f:
                _cache['hist'] = json.load(f)
        except Exception:
            _cache['hist'] = []
    return _cache['hist']


def save_if_due(now, picks):
    """At 3:55-4:00 pm ET on a weekday, save today's picks once (lean + price + signal titles)."""
    m = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (955 <= m < 960) or not picks:
        return
    day = now.strftime('%Y-%m-%d')
    hist = _load()
    if hist and hist[-1]['date'] == day:
        return
    hist.append({'date': day, 'picks': [{'sym': p['symbol'], 'lean': p['lean'], 'price': p['price'],
                                         'signals': [s['title'] for s in p['signals']],
                                         'kinds': sorted({s['learn'] for s in p['signals']})} for p in picks]})
    del hist[:-KEEP_DAYS]
    os.makedirs(DATA, exist_ok=True)
    with open(PATH + '.tmp', 'w') as f:
        json.dump(hist, f)
    os.replace(PATH + '.tmp', PATH)
    _cache['scored_key'] = None


def score(bars):
    """How saved up/down picks did by the NEXT trading day's close. Mixed/heads-up picks aren't scored."""
    hist = _load()
    key = (len(hist), tuple(sorted((s, b[-1].get('date')) for s, b in bars.items() if b)))
    if key == _cache['scored_key']:
        return _cache['record']
    total, right, moves, kinds = 0, 0, [], {}
    for day in hist:
        for p in day['picks']:
            if p['lean'] not in ('up', 'down'):
                continue
            b = bars.get(p['sym']) or []
            nxt = [x for x in b if x.get('date', '') > day['date']]
            if not nxt:
                continue
            mv = (nxt[0]['close'] - p['price']) / p['price'] * 100 * (1 if p['lean'] == 'up' else -1)
            total += 1
            right += mv > 0
            moves.append(mv)
            for k in p.get('kinds', []):
                kk = kinds.setdefault(k, [0, 0, 0.0])
                kk[0] += 1; kk[1] += mv > 0; kk[2] += mv
    rec = {'days': len(hist), 'scored': total, 'right': right,
           'avg': round(sum(moves) / len(moves), 2) if moves else None,
           'kinds': {k: {'n': v[0], 'right': v[1], 'avg': round(v[2] / v[0], 2)} for k, v in kinds.items() if v[0]}}
    _cache['scored_key'], _cache['record'] = key, rec
    return rec
