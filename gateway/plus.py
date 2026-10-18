"""Night desk add-ons (jb-night and the installs after it), running inside the gateway.

One small doorway for every feature added from the October 2026 ideas list, so gateway.py only
needed one new line for all of them:
    /api/plus/<name>        GET (?query) or POST {json}
Each feature lives in its own file next to this one and is used only if that file is there:
    night.py     overnight venue watch, spreads, overnight / pre-market high-low alerts (jb-night)
    alerts2.py   halts, before-bed card, combo alerts, Mac heartbeat, 8:30 data, Fed / CPI odds (jb-alerts2)
    mdata.py     short interest, short volume, unusual options, ratings, float, catalysts, gappers, ETFs (jb-mdata)
    pick.py      ETF pick of the hour: fixed rules, graded an hour later (jb-mdata)
    bigmoney.py  big money: institutions, insiders, block trades, dark pools, politicians (jb-mdata)
    webwatch.py  web watch: press releases, Reddit, SEC, Wikipedia, Google Trends, unexplained moves (jb-webwatch)
    earn.py      expected move, earnings clock, night vs day, stress test, fill quality, game plan (jb-earn)
    ticket.py    phone order ticket, IBKR and Webull, limit orders only (jb-ticket)
    levels.py    your price levels on the charts, with optional alerts (jb-ticket)
    ibauto.py    automatic IB Gateway login: phone push when IB Key approval is needed (jb-ibc)
    macro.py     the MACRO tab: economic backdrop and macro news (jb-macro)
    claude.py    the Ask Claude page: your question plus the board's numbers (jb-macro)
    orders.py    safety stops, targets, trailing stops, flip, chase, scheduled orders, fill pushes (jb-ideas)
    edge.py      3x ETF fair value, decay, drift, opex, VIX curve, Fed odds, options levels, breadth (jb-ideas)
    feeds.py     buy / sell pressure, price cross-check, Bitcoin book, Reddit, Polymarket, policy, news (jb-ideas)
    guardian.py  Mac checks, backups, internet drops, crash loops, algo heartbeats, Saturday refresh (jb-ideas)
    tools.py     any-symbol chart, notes, widget / Siri, alert manager, scorecard, reports (jb-ideas)
    hedge.py     delta hedging: Net Exposure, hedge calculator, hedge partners, hedge ideas, auto-hedge settings (jb-hedge)
A module that is missing or fails to load is simply left out; the rest keep working.
"""
import importlib
import json
import threading
import traceback

MODULES = ('night', 'alerts2', 'mdata', 'pick', 'bigmoney', 'webwatch', 'earn', 'ticket', 'levels', 'ibauto', 'macro', 'claude', 'syscheck',
           'orders', 'edge', 'feeds', 'guardian', 'tools', 'sentiment', 'meters', 'chartpage', 'reportcard', 'safety', 'tradetools', 'why', 'scanwhy', 'insight', 'rc_explore', 'rules', 'stratlab', 'top5', 'algonotes', 'brokerpos', 'hedge')     # final ideas (jb-ideas)
_mods = {}
_lock = threading.Lock()
_started = {'done': False}
_errors = {}


def _load():
    for name in MODULES:
        if name in _mods or name in _errors:
            continue
        try:
            _mods[name] = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name != name:              # the file is there but something it needs is not
                _errors[name] = f'could not load: {e}'
                print(f'plus: {name} could not load: {e!r}', flush=True)
        except Exception as e:
            _errors[name] = f'could not load: {e}'
            print(f'plus: {name} could not load: {e!r}', flush=True)


def start():
    with _lock:
        if _started['done']:
            return
        _started['done'] = True
        _load()
    for name, m in list(_mods.items()):
        if hasattr(m, 'start'):
            try:
                m.start()
            except Exception as e:
                _errors[name] = f'did not start: {e}'
                print(f'plus: {name} did not start: {e!r}', flush=True)


def installed():
    return {'modules': sorted(_mods), 'errors': dict(_errors)}


def handle(method, rest, query, data):
    """-> (status, body, content type). rest = what follows /api/plus/ (e.g. 'night' or 'night/settings')."""
    with _lock:
        _load()
    parts = [p for p in rest.strip('/').split('/') if p]
    if not parts:
        return 200, json.dumps(installed()), 'application/json'
    name, sub = parts[0], '/'.join(parts[1:])
    m = _mods.get(name)
    if m is None:
        return 404, json.dumps({'error': f'{name} is not installed'}), 'application/json'
    try:
        if method == 'POST':
            if not hasattr(m, 'post'):
                return 405, json.dumps({'error': 'method not allowed'}), 'application/json'
            code, body = m.post(sub, data if isinstance(data, dict) else {})
        else:
            code, body = m.get(sub, query or {})
    except Exception as e:
        traceback.print_exc()
        return 500, json.dumps({'error': f'{name}: {e}'[:300]}), 'application/json'
    return code, body if isinstance(body, (str, bytes)) else json.dumps(body), 'application/json'
