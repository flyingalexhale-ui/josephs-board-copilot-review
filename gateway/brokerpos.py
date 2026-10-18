"""Portfolio filled from your accounts (jb-desk). Plus module 'brokerpos'.

Every 5 minutes (and when you press Refresh Now) it reads the stock positions in your IBKR account
and your Webull Individual Margin account (WEBULL_ACCOUNT_ID; the Events account is left alone):
symbol, shares and average cost. Read only: nothing here places or changes orders. IBKR futures are kept
apart ('futs') for the Net Exposure line (gateway/hedge.py, jb-hedge); they are not Portfolio rows.

portfolio.positions() then shows these broker positions first (tagged IBKR or Webull), plus any
symbol you typed in with Add a Trade that neither account holds (tagged Typed). Turn it off on the
Portfolio tab to go back to only the typed-in list.

  data/broker_positions.json   last good read per broker + the on/off switch
  GET  /api/plus/brokerpos/            the saved state
  POST /api/plus/brokerpos/refresh     read both accounts now
  POST /api/plus/brokerpos/switch      {"on": true|false}
"""
import json
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
PATH = os.path.join(DATA, 'broker_positions.json')
EVERY = 300
_lock = threading.Lock()
_busy = threading.Lock()
_st = {'started': False}
COST_KEYS = ('cost_price', 'avg_cost', 'average_cost', 'avg_price', 'average_price', 'unit_cost', 'avg_cost_price')
TOTAL_KEYS = ('total_cost', 'cost')


def _read():
    try:
        with open(PATH, encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(d):
    os.makedirs(DATA, exist_ok=True)
    with open(PATH + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1)
    os.replace(PATH + '.tmp', PATH)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def is_on():
    return _read().get('on', True)


# ------------------------------------------------------------------ the two accounts
def _ibkr():
    import iblink

    async def go(ib):
        import asyncio
        acct = iblink.account()
        ps = await asyncio.wait_for(ib.reqPositionsAsync(), 15)
        out, futs = [], []
        for p in ps:
            c = p.contract
            if not float(p.position) or (acct and p.account != acct):
                continue
            if c.secType == 'FUT':          # jb-hedge: kept apart for the Net Exposure line, not shown as a Portfolio row
                mult = float(getattr(c, 'multiplier', 0) or 1)
                futs.append({'sym': c.symbol.upper(), 'local': getattr(c, 'localSymbol', '') or c.symbol, 'qty': float(p.position),
                             'mult': mult, 'avg': round(float(p.avgCost or 0) / mult, 4) or None})
                continue
            if c.secType != 'STK':
                continue
            out.append({'sym': c.symbol.upper(), 'qty': float(p.position), 'avg': round(float(p.avgCost or 0), 4) or None})
        return out, futs
    return iblink.run(go, timeout=25)


def _webull():
    import killswitch as ks
    tc = ks._wb_client()
    acct, _ = ks._wb_account(tc)
    rows = ks._wb_rows(ks._wb_json(tc.account_v2.get_account_position(acct)))
    out, keys = [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        keys = sorted(r.keys())
        sym = str(r.get('symbol') or r.get('ticker') or '').upper()
        qty = _num(r.get('quantity', r.get('qty', r.get('position'))))
        side = str(r.get('side') or r.get('position_side') or '').upper()
        if side in ('SHORT', 'SELL'):
            qty = -abs(qty)
        kind = str(r.get('instrument_type') or r.get('asset_type') or 'EQUITY').upper()
        if not sym or not qty or kind not in ('EQUITY', 'STOCK', 'STK', 'ETF'):
            continue
        avg = next((_num(r.get(k)) for k in COST_KEYS if _num(r.get(k)) > 0), 0.0)
        if not avg:
            tot = next((_num(r.get(k)) for k in TOTAL_KEYS if _num(r.get(k)) > 0), 0.0)
            avg = tot / abs(qty) if tot else 0.0
        out.append({'sym': sym, 'qty': qty, 'avg': round(avg, 4) or None})
    return out, keys


def refresh():
    """Read both accounts. A broker that fails keeps its last good list (marked with the error)."""
    if not _busy.acquire(blocking=False):
        return state()
    try:
        res = {}
        for name, fn in (('ibkr', _ibkr), ('webull', _webull)):
            try:
                r = fn()
                if name == 'webull':
                    rows, keys = r
                    res[name] = {'rows': rows, 'ok': int(time.time() * 1000), 'error': None, 'fields': keys}
                else:
                    rows, futs = r
                    res[name] = {'rows': rows, 'futs': futs, 'ok': int(time.time() * 1000), 'error': None}
            except Exception as e:
                res[name] = {'error': str(e)[:160]}
        with _lock:
            d = _read()
            for name, r in res.items():
                old = d.get(name) or {}
                if r.get('error'):
                    old['error'] = r['error']
                    old['tried'] = int(time.time() * 1000)
                    d[name] = old
                else:
                    d[name] = r
            d['at'] = int(time.time() * 1000)
            _write(d)
        return state()
    finally:
        _busy.release()


# ------------------------------------------------------------------ used by portfolio.positions()
def merge(ledger, last_price):
    """Broker positions first, then typed-in positions for symbols neither account holds."""
    d = _read()
    if not d.get('on', True):
        return ledger
    out, held = [], set()
    for name, tag in (('ibkr', 'IBKR'), ('webull', 'Webull')):
        for r in (d.get(name) or {}).get('rows') or []:
            sym, sh, avg = r['sym'], float(r['qty']), r.get('avg')
            held.add(sym)
            last = last_price(sym)
            val = sh * last if last is not None else None
            gain = (last - avg) * sh if last is not None and avg else None
            out.append({'sym': sym, 'shares': round(sh, 6), 'avg': avg, 'last': last,
                        'value': round(val, 2) if val is not None else None,
                        'gain': round(gain, 2) if gain is not None else None,
                        'gain_pct': round((last - avg) / avg * 100 * (1 if sh > 0 else -1), 2) if last is not None and avg else None,
                        'realized': 0, 'src': tag})
    if not out and not any((d.get(n) or {}).get('ok') for n in ('ibkr', 'webull')):
        return ledger                       # never read an account yet: keep the typed-in list
    out.sort(key=lambda p: (p['sym'], p['src']))
    for p in ledger:
        if p['sym'] not in held:
            q = dict(p)
            q['src'] = 'Typed'
            out.append(q)
    return out


def state():
    d = _read()
    out = {'on': d.get('on', True), 'at': d.get('at'), 'every_s': EVERY}
    for name in ('ibkr', 'webull'):
        b = d.get(name) or {}
        out[name] = {'n': len(b.get('rows') or []), 'ok': b.get('ok'), 'error': b.get('error')}
    return out


# ------------------------------------------------------------------ plus module hooks
def _loop():
    time.sleep(20)
    while True:
        try:
            if is_on():
                refresh()
        except Exception as e:
            print(f'brokerpos: {e!r}', flush=True)
        time.sleep(EVERY)


def start():
    if _st['started']:
        return
    _st['started'] = True
    threading.Thread(target=_loop, name='brokerpos', daemon=True).start()


def get(sub, query):
    return 200, json.dumps(state())


def post(sub, data):
    sub = (sub or '').strip('/')
    if sub == 'refresh':
        threading.Thread(target=refresh, daemon=True).start()
        return 200, json.dumps(dict(state(), refreshing=True))
    if sub == 'switch':
        with _lock:
            d = _read()
            d['on'] = bool((data or {}).get('on'))
            _write(d)
        if d['on']:
            threading.Thread(target=refresh, daemon=True).start()
        return 200, json.dumps(state())
    return 400, json.dumps({'error': 'unknown action'})
