"""Safety extras (jb-safety), running inside the gateway as a Night-desk add-on.

  Buying power and margin call risk (both accounts are margin accounts). Since June 4, 2026
     there is no pattern day trader rule; what matters is intraday buying power. A margin call
     that isn't covered can limit the account to closing trades for 90 days. Shown on the phone
     page and the Health tab; a push when the risk turns high.
       IBKR: account type, net liquidation, buying power, excess liquidity and "cushion"
             (excess liquidity / net liquidation): under 10% = high risk, under 25% = watch.
       Webull: account type, buying power, margin use (what Webull's account balance gives).
  Broker-side protection check (idea 64): for each position in your journal, is there a real
     stop sitting at the broker (works even if the Mac is off), only a board safety stop (works
     only while the Mac is on), or nothing? IBKR stops can be placed from here (stop-limit, good
     until cancelled, also in pre-market and after-hours; IBKR's overnight venue takes no stops,
     so the board's own safety stop still covers 8 pm - 4 am). A push when a position is
     protected by the Mac only, once per position per day.
  Backup internet through the iPhone hotspot (idea 63): if the internet is down for 90 seconds,
     the Mac joins your iPhone's Personal Hotspot (off until you type its name on the Health tab),
     pushes "on backup internet", and tries home Wi-Fi again every 15 minutes.
  Dec 6 first-night check (idea 10): Sunday Dec 6, 2026, 9:20 pm New York (and again at
     12:30 am) the board checks that overnight prices are flowing, IBKR still accepts its
     OVERNIGHT route, and pushes the result. "Run now" on the Health tab does the same any time.

  GET  /api/plus/safety                       accounts, protection, hotspot, dec6
  POST /api/plus/safety/hotspot               {ssid, password?, home?, on}
  POST /api/plus/safety/hotspot/test          switch to the hotspot now, then back
  POST /api/plus/safety/stop/check            {sym, stop}   -> plan + code (nothing sent)
  POST /api/plus/safety/stop/go               {code}
  POST /api/plus/safety/dec6                  run the Dec 6 check now
Files: data/safety.json (settings, private), data/safety_state.json.
"""
import asyncio
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import date

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
SETTINGS = os.path.join(DATA, 'safety.json')
STATE = os.path.join(DATA, 'safety_state.json')
ACCT_EVERY_S = 300
PROT_EVERY_S = 300
DEC6 = date(2026, 12, 6)

_lock = threading.Lock()
_st = {'started': False, 'accounts': {}, 'acct_at': 0, 'prot': [], 'prot_at': 0, 'stops': {}, 'pushed': {},
       'net_down': 0, 'on_hotspot': False, 'hot_at': 0, 'hot_log': [], 'dec6': None}


def _now():
    return int(time.time() * 1000)


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def _write(path, obj, private=False):
    os.makedirs(DATA, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f)
    if private:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)


def _num(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _push_once(key, title, text, tags='warning'):
    day = watch.et(_now()).date().isoformat()
    if _st['pushed'].get(key) == day:
        return
    _st['pushed'][key] = day
    watch.push_later(title, text, tags)


# ------------------------------------------------------------------ buying power and margin risk
def _risk_text(level):
    return {'ok': 'Low margin call risk', 'watch': 'Watch: margin getting thin', 'high': 'HIGH margin call risk',
            'none': 'No margin in use', '': 'Margin risk unknown'}.get(level, '')


async def _ib_summary(ib):
    import killswitch as ks
    account = ks._ib_account(ib)
    vals = await asyncio.wait_for(ib.accountSummaryAsync(account), 15)
    d = {}
    for v in vals or []:
        if v.account in (account, 'All') and (not v.currency or v.currency in ('USD', 'BASE')):
            d[v.tag] = v.value
    return account, d


def _ibkr_account():
    import iblink
    iblink.start()
    if not iblink.connected():
        return {'broker': 'ibkr', 'ok': False, 'note': 'IB Gateway is not connected right now.'}
    account, d = iblink.run(_ib_summary, timeout=25)
    net, bp = _num(d.get('NetLiquidation')), _num(d.get('BuyingPower'))
    exl, cush = _num(d.get('ExcessLiquidity')), _num(d.get('Cushion'))
    gross = _num(d.get('GrossPositionValue')) or 0
    maint = _num(d.get('FullMaintMarginReq')) or _num(d.get('MaintMarginReq')) or 0
    if cush is None and exl is not None and net:
        cush = exl / net
    level = ''
    if gross <= 0 or maint <= 0:
        level = 'none'
    elif cush is not None:
        level = 'high' if cush < 0.10 else 'watch' if cush < 0.25 else 'ok'
    return {'broker': 'ibkr', 'ok': True, 'type': str(d.get('AccountType') or '').title() or 'Unknown',
            'net': net, 'bp': bp, 'excess': exl, 'cushion': None if cush is None else round(cush * 100, 1),
            'level': level, 'risk': _risk_text(level), 'acct': account[-4:] if account else ''}


def _find(obj, names):
    """First number under any of these key names, anywhere in Webull's answer."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in names and _num(v) is not None:
                return _num(v)
        for v in obj.values():
            r = _find(v, names)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, names)
            if r is not None:
                return r
    return None


def _webull_account():
    import killswitch as ks
    tc = ks._wb_client()
    account, row = ks._wb_account(tc)
    kind = ' '.join(str(v) for k, v in (row or {}).items() if 'type' in k or 'class' in k or 'name' in k)
    typ = 'Margin' if 'MARGIN' in kind.upper() else 'Cash' if 'CASH' in kind.upper() else 'Unknown'
    data = None
    acc = tc.account_v2 if hasattr(tc, 'account_v2') else tc.account
    for call in (lambda: acc.get_account_balance(account, 'USD'), lambda: acc.get_account_balance(account_id=account),
                 lambda: acc.get_account_balance(account)):
        try:
            data = ks._wb_json(call())
            if data:
                break
        except TypeError:
            continue
    net = _find(data, {'net_liquidation_value', 'net_liquidation', 'total_asset', 'total_assets'})
    bp = _find(data, {'day_buying_power', 'buying_power', 'margin_power', 'overnight_buying_power', 'cash_power'})
    util = _find(data, {'margin_utilization_rate', 'margin_usage', 'risk_rate'})
    mv = _find(data, {'total_market_value', 'positions_market_value', 'market_value'}) or 0
    level = ''
    if util is not None:
        u = util * 100 if util <= 1.5 else util
        level = 'none' if mv <= 0 and u <= 0 else 'high' if u >= 90 else 'watch' if u >= 70 else 'ok'
        util = round(u, 1)
    elif mv <= 0:
        level = 'none'
    return {'broker': 'webull', 'ok': True, 'type': typ, 'net': net, 'bp': bp, 'util': util, 'level': level,
            'risk': _risk_text(level), 'acct': str(account)[-4:]}


def refresh_accounts():
    out = {}
    for name, fn in (('ibkr', _ibkr_account), ('webull', _webull_account)):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {'broker': name, 'ok': False, 'note': f'Could not read the account: {str(e)[:120]}'}
        if out[name].get('level') == 'high':
            a = out[name]
            _push_once('risk-' + name, f'{name.upper()}: margin call risk is HIGH',
                       f"Excess money over the margin you need is down to {a.get('cushion') or a.get('util')}%. "
                       'A margin call that isn\'t covered can limit the account to closing trades for 90 days. '
                       'Consider trimming a position.', 'rotating_light')
        if name == 'webull':
            time.sleep(2)                       # Webull limits back-to-back calls
    with _lock:
        _st['accounts'], _st['acct_at'] = out, time.time()
    return out


# ------------------------------------------------------------------ broker-side protection (64)
async def _ib_stops(ib):
    import killswitch as ks
    account = ks._ib_account(ib)
    trades = await ks._ib_open_orders(ib, account)
    out = {}
    for t in trades:
        o, c = t.order, t.contract
        if o.action == 'SELL' and o.orderType in ('STP', 'STP LMT', 'TRAIL', 'TRAIL LIMIT') and c.secType == 'STK':
            out.setdefault(c.symbol, []).append({'type': o.orderType, 'stop': _num(o.auxPrice) or _num(o.trailStopPrice),
                                                 'qty': _num(o.totalQuantity), 'rth_only': not o.outsideRth})
    return out


def _wb_stops():
    import killswitch as ks
    tc = ks._wb_client()
    account, _ = ks._wb_account(tc)
    try:
        data = tc.order_v3.get_order_open(account_id=account, page_size=100)
    except TypeError:
        data = tc.order_v3.get_order_open(account_id=account)
    out = {}
    for r in ks._wb_rows(ks._wb_json(data)):
        if not isinstance(r, dict):
            continue
        for o in (r.get('orders') if isinstance(r.get('orders'), list) else [r]):
            if not isinstance(o, dict):
                continue
            typ = str(o.get('order_type') or '').upper()
            if 'STOP' in typ and str(o.get('side') or '').upper() == 'SELL':
                out.setdefault(str(o.get('symbol') or '').upper(), []).append(
                    {'type': typ, 'stop': _num(o.get('stop_price')), 'qty': _num(o.get('quantity') or o.get('total_quantity'))})
    return out


def refresh_protection():
    import ticket
    pos = [p for p in ticket.positions() if p['qty'] > 0]
    guards = {}
    try:
        import orders
        for g in orders._load().get('guards') or []:
            if g.get('status') in ('on', 'waiting') and (g.get('stop') or g.get('trail')):
                guards.setdefault((g['broker'], g['sym']), []).append(g)
    except Exception:
        pass
    stops, notes = {}, {}
    if any(p['broker'] == 'ibkr' for p in pos):
        try:
            import iblink
            iblink.start()
            stops['ibkr'] = iblink.run(_ib_stops, timeout=25) if iblink.connected() else None
            if stops['ibkr'] is None:
                notes['ibkr'] = 'IB Gateway not connected, so IBKR stops could not be checked.'
        except Exception as e:
            stops['ibkr'], notes['ibkr'] = None, f'IBKR: {str(e)[:100]}'
    if any(p['broker'] == 'webull' for p in pos):
        try:
            stops['webull'] = _wb_stops()
        except Exception as e:
            stops['webull'], notes['webull'] = None, f'Webull: {str(e)[:100]}'
    rows = []
    for p in pos:
        b, s = p['broker'], p['sym']
        at_broker = (stops.get(b) or {}).get(s) or []
        mine = guards.get((b, s)) or []
        if stops.get(b) is None:
            status, text = 'unknown', notes.get(b) or 'Could not check.'
        elif at_broker:
            st = at_broker[0]
            status = 'broker'
            text = f"Stop at {b.upper()}: {st['type']} {st['stop'] or ''}".strip()
            if b == 'ibkr':
                text += (' (regular hours only)' if st.get('rth_only') else ' (4 am - 8 pm)')
                text += '; overnight: ' + ('board safety stop' if mine else 'no stop')
        elif mine:
            status, text = 'mac', 'Protected by the Mac only (board safety stop). If the Mac goes down, nothing protects it.'
        else:
            status, text = 'none', 'No stop anywhere.'
        rows.append({'broker': b, 'sym': s, 'qty': p['qty'], 'status': status, 'text': text,
                     'stop': (mine[0].get('stop') if mine else None), 'can_place': b == 'ibkr' and status in ('mac', 'none')})
        if status == 'mac':
            _push_once(f'prot-{b}-{s}', f'{s} is protected by the Mac only',
                       f"Your {s} position on {b.upper()} has a board safety stop but no stop at the broker. If the Mac goes down "
                       'it is unprotected. ' + ('Tap "Place at IBKR" on the phone page to add a real stop.' if b == 'ibkr'
                                                else 'Add a stop in the Webull app to be safe.'), 'warning')
    with _lock:
        _st['prot'], _st['prot_at'] = rows, time.time()
    return rows


def stop_check(data):
    sym = re.sub(r'[^A-Z.]', '', str(data.get('sym') or '').upper())[:10]
    stop = _num(data.get('stop'))
    row = next((r for r in _st['prot'] if r['broker'] == 'ibkr' and r['sym'] == sym), None)
    if not row:
        return 400, {'error': f'No IBKR {sym} position found in your journal.'}
    if not stop:
        stop = row.get('stop')
    if not stop:
        return 400, {'error': 'Type a stop price.'}
    try:
        import orders
        last = orders.any_price(sym)
    except Exception:
        last = None
    if last and stop >= last:
        return 400, {'error': f'The stop {stop:g} is at or above the price now ({last:g}).'}
    lmt = round(stop * 0.99, 2)
    code = uuid.uuid4().hex[:12]
    _st['stops'] = {k: v for k, v in _st['stops'].items() if time.time() - v['at'] < 120}
    _st['stops'][code] = {'at': time.time(), 'sym': sym, 'qty': int(row['qty']), 'stop': round(stop, 2), 'lmt': lmt}
    return 200, {'code': code, 'text': f"Place a real stop at IBKR: sell {int(row['qty'])} {sym} if it trades at {stop:.2f} or lower, "
                                       f"with a limit of {lmt:.2f} (1% under the stop). Good until you cancel it, works 4 am - 8 pm. "
                                       'Your board safety stop stays on for overnight.'}


async def _ib_place_stop(ib, s):
    import iblink
    import killswitch as ks
    from ib_async import Order
    account = ks._ib_account(ib)
    c = await iblink.stock(ib, s['sym'])
    o = Order(action='SELL', orderType='STP LMT', totalQuantity=s['qty'], auxPrice=s['stop'], lmtPrice=s['lmt'],
              tif='GTC', outsideRth=True, account=account, orderRef='jb-safety-stop')
    t = ib.placeOrder(c, o)
    await asyncio.sleep(3)
    return t.orderStatus.status or 'sent'


def stop_go(data):
    s = _st['stops'].pop(str(data.get('code') or ''), None)
    if not s:
        return 400, {'error': 'That plan expired. Check again.'}
    import iblink
    if not iblink.connected():
        return 400, {'error': 'IB Gateway is not connected right now.'}
    try:
        status = iblink.run(lambda ib: _ib_place_stop(ib, s), timeout=30)
    except Exception as e:
        return 400, {'error': f'IBKR did not take it: {str(e)[:150]}'}
    watch.push_later(f"Stop placed at IBKR: {s['sym']}", f"Sell {s['qty']} {s['sym']} at {s['stop']:.2f} (limit {s['lmt']:.2f}), "
                     f'good until cancelled. IBKR says: {status}.', 'shield')
    threading.Thread(target=refresh_protection, daemon=True).start()
    return 200, {'ok': True, 'text': f"Sent to IBKR. Status: {status}. It shows in the IBKR app under Orders."}


# ------------------------------------------------------------------ backup internet (63)
def _wifi_device():
    try:
        out = subprocess.run(['networksetup', '-listallhardwareports'], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r'Hardware Port: (?:Wi-Fi|AirPort)\s*\nDevice: (\S+)', out)
        return m.group(1) if m else 'en0'
    except Exception:
        return 'en0'


def current_ssid():
    try:
        out = subprocess.run(['networksetup', '-getairportnetwork', _wifi_device()], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r'Current Wi-Fi Network: (.+)', out)
        return m.group(1).strip() if m else ''
    except Exception:
        return ''


def _join(ssid, password=''):
    cmd = ['networksetup', '-setairportnetwork', _wifi_device(), ssid] + ([password] if password else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        err = (r.stdout + r.stderr).strip()
        return '' if r.returncode == 0 and 'error' not in err.lower() and 'could not' not in err.lower() else (err or 'failed')[:160]
    except Exception as e:
        return str(e)[:160]


def _online():
    try:
        import guardian
        return guardian._online()
    except Exception:
        return True


def _hot_log(text):
    _st['hot_log'] = ([{'at': _now(), 'text': text}] + _st['hot_log'])[:20]


def _wait_online(secs):
    end = time.time() + secs
    while time.time() < end:
        time.sleep(5)
        if _online():
            return True
    return False


def to_hotspot(cfg, why):
    home = current_ssid()
    if home and home != cfg['ssid'] and not cfg.get('home'):
        cfg['home'] = home
        _write(SETTINGS, cfg, private=True)
    err = _join(cfg['ssid'], cfg.get('password') or '')
    if err:
        _hot_log(f'Could not join the hotspot "{cfg["ssid"]}": {err}')
        return False
    ok = _wait_online(45)
    _st['on_hotspot'], _st['hot_at'] = ok, time.time()
    _hot_log(f'Joined the iPhone hotspot ({why}).' + ('' if ok else ' No internet through it yet.'))
    if ok:
        watch.push_later('Board is on backup internet', f'Home internet was down, so the Mac joined your iPhone hotspot "{cfg["ssid"]}". '
                         'It tries home Wi-Fi again every 15 minutes.', 'warning')
    return ok


def to_home(cfg, why):
    home = cfg.get('home') or ''
    if not home:
        return False
    err = _join(home, cfg.get('home_password') or '')
    if err:
        _hot_log(f'Could not rejoin home Wi-Fi "{home}": {err}')
        return False
    if _wait_online(45):
        _st['on_hotspot'] = False
        _hot_log(f'Back on home Wi-Fi ({why}).')
        watch.push_later('Board is back on home Wi-Fi', 'Home internet works again; the Mac left the iPhone hotspot.', 'white_check_mark')
        return True
    _hot_log('Home Wi-Fi still has no internet; going back to the hotspot.')
    _join(cfg['ssid'], cfg.get('password') or '')
    _st['hot_at'] = time.time()
    return False


def _step_hotspot():
    cfg = _read(SETTINGS, {})
    if not cfg.get('on') or not cfg.get('ssid'):
        return
    if _st['on_hotspot']:
        if time.time() - _st['hot_at'] > 900:
            _st['hot_at'] = time.time()
            to_home(cfg, 'every 15 minutes')
        return
    if _online():
        _st['net_down'] = 0
        return
    _st['net_down'] = _st['net_down'] or time.time()
    if time.time() - _st['net_down'] >= 90:
        _st['net_down'] = 0
        to_hotspot(cfg, 'internet down for 90 seconds')


def hotspot_settings(data):
    cfg = _read(SETTINGS, {})
    if 'ssid' in data:
        cfg['ssid'] = str(data.get('ssid') or '').strip()[:64]
    if data.get('password'):
        cfg['password'] = str(data['password'])[:128]
    if 'home' in data and data.get('home'):
        cfg['home'] = str(data['home']).strip()[:64]
    if data.get('home_password'):
        cfg['home_password'] = str(data['home_password'])[:128]
    if 'on' in data:
        cfg['on'] = bool(data.get('on')) and bool(cfg.get('ssid'))
    _write(SETTINGS, cfg, private=True)
    return 200, snapshot()


def hotspot_test():
    cfg = _read(SETTINGS, {})
    if not cfg.get('ssid'):
        return 400, {'error': 'Type your iPhone hotspot name first.'}

    def run():
        if to_hotspot(cfg, 'test'):
            time.sleep(20)
            to_home(cfg, 'end of test')
    threading.Thread(target=run, daemon=True).start()
    return 200, {'ok': True, 'text': 'Testing: the Mac joins the hotspot, waits 20 seconds, then goes back to home Wi-Fi. '
                                     'The board may go quiet for a minute. Results show here in about 2 minutes.'}


# ------------------------------------------------------------------ Dec 6 first-night check (10)
def _ib_overnight_ok():
    import iblink
    from ib_async import Stock

    async def go(ib):
        c = Stock('TQQQ', 'OVERNIGHT', 'USD', primaryExchange='NASDAQ')
        q = await asyncio.wait_for(ib.qualifyContractsAsync(c), 10)
        return bool(q and q[0] is not None and c.conId)
    iblink.start()
    if not iblink.connected():
        return None
    return iblink.run(go, timeout=20)


def dec6_check(push=True):
    now = _now()
    lines, bad = [], 0
    try:
        syms = [s for s in json.load(open(os.path.join(DATA, 'ibkr_watchlist.json'))) if not s.endswith('=F')]
    except Exception:
        syms = []
    fresh = []
    with watch._lock:
        for s in syms:
            h = watch._px.get(s) or []
            if h and now - h[-1][0] < 10 * 60000:
                fresh.append(s)
    if syms:
        ok = len(fresh) >= max(1, len(syms) // 3)
        bad += not ok
        lines.append(('OK' if ok else 'PROBLEM') + f': overnight prices in the last 10 minutes for {len(fresh)} of {len(syms)} stocks on the IBKR board.')
    try:
        r = _ib_overnight_ok()
        if r is None:
            bad += 1
            lines.append('PROBLEM: IB Gateway is not connected, so the OVERNIGHT route could not be checked.')
        else:
            bad += not r
            lines.append(('OK' if r else 'PROBLEM') + ': IBKR ' + ('still accepts' if r else 'did NOT accept') + ' its OVERNIGHT route (TQQQ).')
    except Exception as e:
        bad += 1
        lines.append(f'PROBLEM: OVERNIGHT route check failed: {str(e)[:100]}')
    d = watch.et(now)
    m = d.hour * 60 + d.minute
    if 1200 <= m < 1260:
        lines.append('Note: 8-9 pm is the exchanges\' pause; IBKR\'s side venue may still trade.')
    res = {'at': now, 'ok': not bad, 'lines': lines}
    _st['dec6'] = res
    st = _read(STATE, {})
    st['dec6'] = res
    _write(STATE, st)
    if push:
        watch.push_later('Overnight session check: ' + ('ALL OK' if not bad else 'needs a look'), '\n'.join(lines),
                         'white_check_mark' if not bad else 'warning')
    return res


def _step_dec6():
    d = watch.et(_now())
    st = _read(STATE, {})
    done = st.get('dec6_done') or []
    for key, day, hh, mm in (('first', DEC6, 21, 20), ('late', date(2026, 12, 7), 0, 30)):
        if key in done or d.date() != day or d.hour * 60 + d.minute < hh * 60 + mm or d.hour * 60 + d.minute > hh * 60 + mm + 90:
            continue
        dec6_check()
        st = _read(STATE, {})
        st['dec6_done'] = (st.get('dec6_done') or []) + [key]
        _write(STATE, st)


# ------------------------------------------------------------------ loop and doorway
def _loop():
    time.sleep(60)
    while True:
        try:
            _step_hotspot()
        except Exception as e:
            print(f'safety: hotspot: {e!r}', flush=True)
        try:
            _step_dec6()
        except Exception as e:
            print(f'safety: dec6: {e!r}', flush=True)
        if time.time() - _st['acct_at'] > ACCT_EVERY_S:
            try:
                refresh_accounts()
            except Exception as e:
                print(f'safety: accounts: {e!r}', flush=True)
                _st['acct_at'] = time.time()
        if time.time() - _st['prot_at'] > PROT_EVERY_S:
            try:
                refresh_protection()
            except Exception as e:
                print(f'safety: protection: {e!r}', flush=True)
                _st['prot_at'] = time.time()
        time.sleep(30)


def start():
    if _st['started']:
        return
    _st['started'] = True
    _st['dec6'] = _read(STATE, {}).get('dec6')
    threading.Thread(target=_loop, name='safety', daemon=True).start()


def snapshot():
    cfg = _read(SETTINGS, {})
    with _lock:
        return {'accounts': _st['accounts'], 'accounts_at': int(_st['acct_at'] * 1000), 'protection': _st['prot'],
                'protection_at': int(_st['prot_at'] * 1000),
                'hotspot': {'on': bool(cfg.get('on')), 'ssid': cfg.get('ssid') or '', 'has_password': bool(cfg.get('password')),
                            'home': cfg.get('home') or '', 'on_hotspot': _st['on_hotspot'], 'log': _st['hot_log'][:8]},
                'dec6': _st['dec6'], 'dec6_date': 'Sun Dec 6, 2026'}


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'hotspot':
        return hotspot_settings(data)
    if sub == 'hotspot/test':
        return hotspot_test()
    if sub == 'stop/check':
        return stop_check(data)
    if sub == 'stop/go':
        return stop_go(data)
    if sub == 'dec6':
        return 200, dec6_check(push=True)
    if sub == 'refresh':
        threading.Thread(target=lambda: (refresh_accounts(), refresh_protection()), daemon=True).start()
        return 200, {'ok': True}
    return 404, {'error': 'unknown'}
