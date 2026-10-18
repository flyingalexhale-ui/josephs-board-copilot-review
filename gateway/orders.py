"""Order helpers from the final-ideas list (jb-orders), running inside the gateway.

  Safety guards, watched by the board in EVERY session (overnight too, and on Webull):
    stop          sell if the price falls to your stop                              (idea 1)
    bracket       a profit target and a stop together: whichever comes first sells (idea 3)
    trailing      the stop follows the price up by a set % and never moves down     (idea 59)
    break-even    once up a set %, the stop moves up to what you paid               (idea 62)
  A guard fires only after two price readings in a row past its level (one odd tick can't
  trigger it) and only on a fresh price. It then sells what you still hold (never more) with a
  limit a little under the last price, 0.5% / 1% / 2% / 3%, waiting 20 s before each step down,
  like the SELL ALL button. The result is pushed to your phone.
  Flip (idea 2): sell a 3x ETF and buy its opposite twin (TQQQ <-> SQQQ) with the same money.
  Chase (idea 6): an order that doesn't fill is moved toward the market every 20 s (6 tries,
  never more than 1% past your first limit).
  Stale orders (idea 54): today-only orders not filled after N minutes are cancelled (off unless set).
  Scheduled orders (idea 33) and sell at the open (idea 63): paper (just recorded) unless you tick
  "real". News heads-up push (idea 34, on) before high-impact news; the algo pause is a separate
  switch (off unless set) that pauses the algos a few minutes before high-impact
  news (Fed, CPI, jobs) and resumes them after. Sunday IB sign-in reminder (idea 35).
  Fill push (idea 58): a phone push for every fill (IBKR within ~30 s, Webull within ~3 min), with
  an automatic trade note (idea 52): session, move since the close, QQQ/SPY, VIX.
Every order goes through the order ticket (gateway/ticket.py): same checks, same log.

  GET  /api/plus/orders                 guards, schedules, settings, recent events, saved sizes
  POST /api/plus/orders/guard           {broker, sym, qty?, stop?, target?, trail?, be?, after?}
  POST /api/plus/orders/guard/off       {id}
  POST /api/plus/orders/flip/check      {broker, sym}   -> plan + code (nothing sent)
  POST /api/plus/orders/flip/go         {code}
  POST /api/plus/orders/chase           {id}            (a ticket id)
  POST /api/plus/orders/schedule        {broker, sym, side, qty|"all", when, limit?, real?}
  POST /api/plus/orders/schedule/off    {id}
  POST /api/plus/orders/settings        {stale_min?, stale_session?, event_push?, event_guard?, event_before?, event_after?, ib_reminder?, fill_push?}
  POST /api/plus/orders/sizes           {dollars: [..]} or {sym, qty}
Files: data/orders.json, data/orders_log.json, data/fills_seen.json, data/trade_notes.json.
"""
import asyncio
import json
import math
import os
import threading
import time
import uuid
from datetime import timedelta

import watch
import killswitch as ks
import ticket

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
FILE = os.path.join(DATA, 'orders.json')
LOG = os.path.join(DATA, 'orders_log.json')
EVFILE = os.path.join(DATA, 'event_pause.json')    # news pause survives a gateway restart
SEEN = os.path.join(DATA, 'fills_seen.json')
NOTES = os.path.join(DATA, 'trade_notes.json')
STEPS = (0.5, 1.0, 2.0, 3.0)
WAIT_S = 20
FRESH_MS = 90 * 1000
DEFAULT_CFG = {'stale_min': 0, 'stale_session': True, 'event_push': True, 'event_guard': False, 'event_before': 10, 'event_after': 15,
               'ib_reminder': True, 'fill_push': True}
_lock = threading.Lock()
_st = {'started': False, 'firing': set(), 'flips': {}, 'chasing': set(), 'ev': {}, 'wb_at': 0, 'ib_at': 0}


def _now():
    return int(time.time() * 1000)


def _clean(s):
    return ticket._clean_sym(s)


def _load():
    v = watch._read(FILE, {})
    v = v if isinstance(v, dict) else {}
    v.setdefault('guards', [])
    v.setdefault('sched', [])
    v['cfg'] = dict(DEFAULT_CFG, **(v.get('cfg') or {}))
    v.setdefault('sizes', {'dollars': [500, 1000, 2500], 'syms': {}})
    return v


def _save(v):
    v['guards'] = v['guards'][-100:]
    v['sched'] = v['sched'][-100:]
    watch._write(FILE, v)


def _change(fn):
    """Read, change and save orders.json in one go (so two threads can't undo each other)."""
    with _lock:
        v = _load()
        out = fn(v)
        _save(v)
        return out


def log(kind, text, **extra):
    with _lock:
        lst = watch._read(LOG, [])
        lst = lst if isinstance(lst, list) else []
        lst.insert(0, dict(extra, kind=kind, text=text, at=_now()))
        watch._write(LOG, lst[:150])
    print(f'orders: {kind}: {text}', flush=True)


def _push(title, text, tags='bell'):
    watch.push_later(title, text, tags)


def price(sym):
    """(price, time ms) of the board's latest reading, or (None, 0)."""
    with watch._lock:
        h = watch._px.get(sym)
        return (float(h[-1][1]), int(h[-1][0])) if h else (None, 0)


def any_price(sym):
    p, t = price(sym)
    if p and _now() - t < 15 * 60000:
        return p
    q = ticket.quote(sym)
    return q.get('last') or ((q['bid'] + q['ask']) / 2 if q.get('bid') else None)


def held(broker, sym):
    """Shares held right now, asked from the broker itself."""
    if not ticket._busy.acquire(timeout=60):
        raise RuntimeError('busy with another order')
    try:
        if broker == 'ibkr':
            return float(ks._in_loop(ticket._ib_check({'sym': sym}))['held'])
        return float(ticket._wb_check({'sym': sym})['held'])
    finally:
        ticket._busy.release()


def _wait_fill(tid, qty, wait_s=WAIT_S):
    """Wait for a ticket, then (filled, avg, still open?)."""
    end = time.time() + wait_s
    t = ticket.ticket_by_id(tid) or {}
    while time.time() < end:
        time.sleep(5)
        ticket._last_refresh[0] = 0
        try:
            ticket.refresh()
        except Exception:
            pass
        t = ticket.ticket_by_id(tid) or t
        if float(t.get('filled') or 0) >= qty or t.get('status') not in ticket.OPEN:
            break
    return float(t.get('filled') or 0), t.get('avg'), t.get('status') in ticket.OPEN


def _cancel_open_sells(broker, sym):
    """The board's own sell orders for this symbol that are still open at the broker (a stop's sell from before
    a restart, a sell ticket): cancelled first, since a new sell on top of them could sell the same shares twice.
    Returns (how many were open, '' or why nothing new may be sent)."""
    def mine():
        ticket._last_refresh[0] = 0
        try:
            ticket.refresh()
        except Exception:
            pass
        return [t for t in ticket.open_tickets() if t.get('broker') == broker and t.get('sym') == sym and t.get('side') == 'sell']
    found = mine()
    for t in found:
        try:
            ticket.cancel({'id': t['id']})
        except Exception:
            pass
    if not found:
        return 0, ''
    time.sleep(2)
    left = mine()
    return len(found), (f'an earlier sell order for {sym} could not be cancelled, so no second sell was sent' if left else '')


async def _ib_cancel_safety_stops(ib, sym):
    trades = [t for t in ib.openTrades() if getattr(t.order, 'orderRef', '') == 'jb-safety-stop'
              and getattr(t.contract, 'symbol', '') == sym and not t.isDone()]
    for t in trades:
        ib.cancelOrder(t.order)
    for _ in range(16):
        if all(t.isDone() for t in trades):
            break
        await asyncio.sleep(0.5)
    return len(trades), sum(1 for t in trades if not t.isDone())


def _cancel_safety_stops(sym):
    """A real stop the board placed at IBKR for this stock (the protection check's "Place at IBKR": good until
    cancelled, works from 4 am) would sell the same shares AGAIN after this sale, leaving you short: it is
    cancelled first, through the link that placed it. Returns '' or a warning for the result message."""
    try:
        import iblink
        if not iblink.connected():
            return f'could not reach IB Gateway to cancel a stop the board placed at IBKR for {sym}: check the IBKR app'
        n, left = iblink.run(lambda ib: _ib_cancel_safety_stops(ib, sym), timeout=20)
    except Exception as e:
        return f'could not check for a stop the board placed at IBKR for {sym} ({str(e)[:60]}): check the IBKR app'
    if left:
        return f'your stop at IBKR for {sym} could not be cancelled: cancel it in the IBKR app, or it can sell again'
    return f'cancelled the stop the board had placed at IBKR for {sym}' if n else ''


async def _ib_other_sells(ib, sym):
    await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), 15)
    return sum(1 for t in ib.openTrades() if getattr(t.contract, 'symbol', '') == sym and not t.isDone()
               and str(getattr(t.order, 'action', '')).upper() == 'SELL')


def _other_sells_note(broker, sym):
    """Sell or stop orders YOU placed in the broker app for this stock are left alone (the board never cancels
    your own orders), but after the board sold, one of them could sell again and leave you short: say so in
    the push (review 2026-10-11, second check)."""
    try:
        if broker == 'ibkr':
            import iblink
            if not iblink.connected():
                return ''
            n = iblink.run(lambda ib: _ib_other_sells(ib, sym), timeout=25)
        else:
            tc = ks._wb_client()
            account, _ = ks._wb_account(tc)
            n = sum(1 for o in ks._wb_open_orders(tc, account) if o['sym'] == sym and o['side'].upper() == 'SELL')
    except Exception:
        return ''
    return (f'you still have {n} open sell/stop order{"s" if n > 1 else ""} for {sym} in the {broker.upper()} app: '
            'cancel it if you no longer hold the shares, or it can sell again') if n else ''


def sell_now(broker, sym, qty, why, source='guard'):
    """Sell `qty` (at most what is held) with marketable limits, stepping down like SELL ALL.
    Returns (sold, avg price, message)."""
    n_open, stop = _cancel_open_sells(broker, sym)
    if stop:
        return 0, None, stop
    ib_note = _cancel_safety_stops(sym) if broker == 'ibkr' else ''
    try:
        have = held(broker, sym)
    except Exception as e:
        return 0, None, f'could not ask {broker.upper()} what you hold: {str(e)[:120]}'
    left = int(min(qty, have))
    if left <= 0:
        return 0, None, f'you no longer hold {sym} on {broker.upper()}, nothing to sell'
    sold, money, note = 0, 0.0, ''
    for pct in STEPS:
        last = any_price(sym)
        if not last:
            note = f'no price for {sym}'
            break
        ok, t = ticket.place({'broker': broker, 'sym': sym, 'side': 'sell', 'qty': left,
                              'limit': ks.limit_price('SELL', last, pct), 'tif': 'day'}, source)
        if not ok:
            note = t
            if 'closed' in t or 'hold' in t:
                break
            continue
        filled, avg, still = _wait_fill(t['id'], left)
        if still:
            try:
                ticket.cancel({'id': t['id']})
            except Exception:
                pass
            time.sleep(2)
            ticket._last_refresh[0] = 0
            ticket.refresh()
            t2 = ticket.ticket_by_id(t['id']) or {}
            filled, avg = float(t2.get('filled') or filled), t2.get('avg') or avg
            still = t2.get('status') in ticket.OPEN
        if filled:
            sold += int(filled)
            money += filled * float(avg or last)
            left -= int(filled)
        if left <= 0:
            break
        if still:
            note = 'the broker had not confirmed the cancel, so no second sell was sent'
            break
    avg = money / sold if sold else None
    msg = f'sold {sold} {sym}' + (f' at about {avg:.2f}' if avg else '') + (f', {left} not sold ({note or "no fill"})' if left > 0 else '')
    if n_open:
        msg = f'cancelled {n_open} open sell order{"s" if n_open > 1 else ""} for {sym} first; ' + msg
    if ib_note:
        msg += f' ({ib_note})'
    if sold:
        other = _other_sells_note(broker, sym)
        if other:
            msg += f' ({other})'
    return sold, avg, msg


# ------------------------------------------------------------------ guards (ideas 1, 3, 59, 62)
def _num(v):
    try:
        v = float(v)
        return v if math.isfinite(v) and v > 0 else None
    except (TypeError, ValueError):
        return None


def add_guard(data):
    broker = str(data.get('broker') or '').lower()
    sym = _clean(data.get('sym'))
    if broker not in ('ibkr', 'webull') or not sym:
        return 400, {'error': 'Pick IBKR or Webull and a symbol.'}
    stop, target, trail, be = _num(data.get('stop')), _num(data.get('target')), _num(data.get('trail')), _num(data.get('be'))
    if not (stop or target or trail):
        return 400, {'error': 'Set a stop price, a target price or a trailing %.'}
    if trail and trail > 30:
        return 400, {'error': 'A trailing stop of more than 30% is too wide.'}
    last = any_price(sym)
    if last:
        if stop and stop >= last:
            return 400, {'error': f'The stop {stop:g} is at or above the price now ({last:g}); it would sell at once.'}
        if target and target <= last:
            return 400, {'error': f'The target {target:g} is at or below the price now ({last:g}); it would sell at once.'}
    after = str(data.get('after') or '')          # a buy ticket: the guard waits until it fills
    pos = next((p for p in ticket.positions() if p['broker'] == broker and p['sym'] == sym), None)
    qty = _num(data.get('qty'))
    if not qty and not after:
        qty = pos['qty'] if pos else None
    if not qty and not after:
        return 400, {'error': f'How many shares? (No {sym} position found in your journal on {broker.upper()}.)'}
    entry = _num(data.get('entry')) or (pos['price'] if pos else None) or last
    hi = last or 0
    if trail and last:
        stop = max(stop or 0, round(last * (1 - trail / 100), 4))
    g = {'id': uuid.uuid4().hex[:10], 'broker': broker, 'sym': sym, 'qty': int(qty or 0), 'stop': stop, 'target': target,
         'trail': trail, 'be': be, 'be_done': False, 'entry': entry, 'high': hi, 'after': after, 'status': 'waiting' if after else 'on',
         'created': _now(), 'hits': 0, 'note': ''}
    _change(lambda v: v['guards'].append(g))
    log('guard', f'{describe(g)} added', sym=sym)
    return 200, snapshot()


def describe(g):
    bits = []
    if g.get('target'):
        bits.append(f"target {g['target']:g}")
    if g.get('trail'):
        bits.append(f"trailing {g['trail']:g}% (stop now {g['stop']:g})" if g.get('stop') else f"trailing {g['trail']:g}%")
    elif g.get('stop'):
        bits.append(f"stop {g['stop']:g}")
    if g.get('be'):
        bits.append(f"break-even after +{g['be']:g}%" + (' (done)' if g.get('be_done') else ''))
    q = g['qty'] or 'the filled'
    return f"{g['broker'].upper()} {g['sym']} {q} shares: " + ', '.join(bits)


def guard_off(data):
    gid = str(data.get('id') or '')

    def f(v):
        for g in v['guards']:
            if g['id'] == gid and g['status'] in ('on', 'waiting'):
                g['status'], g['note'] = 'off', 'switched off by you'
    _change(f)
    return 200, snapshot()


def _activate_waiting(v):
    """A guard set with a buy order starts once that order has filled (for the shares filled)."""
    for g in v['guards']:
        if g['status'] != 'waiting':
            continue
        t = ticket.ticket_by_id(g.get('after'))
        if not t:
            g['status'], g['note'] = 'off', 'its buy order was not found'
            continue
        filled = int(float(t.get('filled') or 0))
        if filled > 0 and (filled >= int(t.get('qty') or 0) or t.get('status') not in ticket.OPEN):
            g['qty'], g['status'] = filled, 'on'
            g['entry'] = float(t.get('avg') or g.get('entry') or 0) or g.get('entry')
            log('guard', f"{describe(g)} is now on (buy filled)", sym=g['sym'])
        elif t.get('status') not in ticket.OPEN and not filled:
            g['status'], g['note'] = 'off', 'its buy order ended without a fill'


def _step_guards(now):
    fire = []

    def f(v):
        _activate_waiting(v)
        for g in v['guards']:
            if g['status'] != 'on' or g['id'] in _st['firing']:
                continue
            p, t = price(g['sym'])
            g['fresh'] = bool(p and now - t < FRESH_MS)
            if not g['fresh'] or t == g.get('_t'):
                continue                                   # no new reading since the last look
            g['_t'], g['last'] = t, p
            if g.get('trail'):
                if p > (g.get('high') or 0):
                    g['high'] = p
                new = round(g['high'] * (1 - g['trail'] / 100), 4)
                if new > (g.get('stop') or 0):
                    g['stop'] = new
            if g.get('be') and not g.get('be_done') and g.get('entry') and p >= g['entry'] * (1 + g['be'] / 100):
                g['be_done'] = True
                if (g.get('stop') or 0) < g['entry']:
                    g['stop'] = round(g['entry'], 4)
                    _push(f"{g['sym']} stop moved to break-even", f"{g['sym']} is up {g['be']:g}% from {g['entry']:g}, so its stop is now {g['stop']:g} (what you paid).", 'shield')
                    log('guard', f"{g['sym']} stop moved to break-even {g['stop']:g}", sym=g['sym'])
            hit = 'stop' if g.get('stop') and p <= g['stop'] else 'target' if g.get('target') and p >= g['target'] else ''
            g['hits'] = g.get('hits', 0) + 1 if hit else 0
            if hit and g['hits'] >= 2:
                g['status'] = 'firing'
                _st['firing'].add(g['id'])
                fire.append((dict(g), hit, p))
    _change(f)
    for g, hit, p in fire:
        threading.Thread(target=_fire, args=(g, hit, p), name='guard', daemon=True).start()


def _fire(g, hit, p):
    level = g['stop'] if hit == 'stop' else g['target']
    _push(f"{g['sym']} {hit} hit: selling", f"{g['sym']} is {p:g}, {'at or under your stop' if hit == 'stop' else 'at or over your target'} {level:g}. Selling {g['qty']} on {g['broker'].upper()}.", 'rotating_light')
    try:
        sold, avg, msg = sell_now(g['broker'], g['sym'], g['qty'], hit)
    except Exception as e:
        sold, avg, msg = 0, None, f'error: {str(e)[:150]}'
    ok = sold >= g['qty'] or 'no longer hold' in msg

    def f(v):
        for x in v['guards']:
            if x['id'] == g['id']:
                x['status'] = 'done' if ok else 'failed'
                x['note'] = f'{hit} at {p:g}: {msg}'
                x['done_at'] = _now()
    _change(f)
    _st['firing'].discard(g['id'])
    log('guard', f"{g['sym']} {hit} at {p:g}: {msg}", sym=g['sym'])
    _push(f"{g['sym']} {hit}: {'done' if ok else 'CHECK NOW'}", f"{g['broker'].upper()}: {msg}." + ('' if ok else ' Some shares may still be held: check the broker app.'),
          'white_check_mark' if ok else 'rotating_light')


# ------------------------------------------------------------------ flip (idea 2)
def twin(sym):
    try:
        import mdata
        pairs = mdata.pairs() if hasattr(mdata, 'pairs') else mdata.DEFAULT_PAIRS
    except Exception:
        pairs = []
    for row in pairs:
        try:
            bull, bear = row[3], row[4]
        except Exception:
            continue
        if sym == bull and bear:
            return bear
        if sym == bear and bull:
            return bull
    return None


def flip_check(data):
    broker = str(data.get('broker') or '').lower()
    sym = _clean(data.get('sym'))
    other = _clean(data.get('to')) or twin(sym)
    if broker not in ('ibkr', 'webull') or not sym:
        return 400, {'error': 'Pick IBKR or Webull and a symbol.'}
    if not other:
        return 400, {'error': f'No opposite fund known for {sym}. Use a bull/bear pair like TQQQ / SQQQ.'}
    if ks.session_now() is None:
        return 400, {'error': 'Markets are closed right now.'}
    try:
        have = int(held(broker, sym))
    except Exception as e:
        return 400, {'error': f'Could not reach {broker.upper()}: {str(e)[:150]}'}
    if have <= 0:
        return 400, {'error': f'You hold no {sym} on {broker.upper()}.'}
    pa, pb = any_price(sym), any_price(other)
    if not pa or not pb:
        return 400, {'error': f'No price for {sym if not pa else other} right now.'}
    money = have * pa
    qty_b = int(money * 0.99 // ks.limit_price('BUY', pb, 0.5))
    if qty_b < 1:
        return 400, {'error': f'{have} {sym} is not enough to buy one {other}.'}
    cost_b = qty_b * ks.limit_price('BUY', pb, 0.5)
    if cost_b > ticket.max_dollars():
        # the buy half goes through the ticket's per-order cap (JB_TICKET_MAX): say so now, before anything is sold,
        # rather than sell and then have the buy refused (half a flip: no position at all)
        return 400, {'error': f'Buying about {qty_b} {other} would be about ${cost_b:,.0f}, over your limit of '
                              f'${ticket.max_dollars():,.0f} per buy order (JB_TICKET_MAX in settings.env), so nothing was '
                              f'sold. Raise JB_TICKET_MAX to flip this position, or sell {sym} with the order ticket.'}
    code = uuid.uuid4().hex[:16]
    _st['flips'] = {k: x for k, x in _st['flips'].items() if time.time() - x['at'] < 120}
    _st['flips'][code] = {'at': time.time(), 'broker': broker, 'a': sym, 'b': other, 'qty_a': have}
    warn = ['Both accounts are margin accounts, so the new shares can be bought right away. Watch your buying power: '
            'a big move against a 3x position can bring an intraday margin call (see Accounts and protection on the Phone page).']
    if broker == 'webull' and ks.session_now() == 'overnight' and not _webull_overnight():
        warn.append('Your Webull plan has no overnight prices, so the prices used here may be from another source.')
    return 200, {'code': code, 'text': f"Sell all {have} {sym} (about ${money:,.0f}), then buy about {qty_b} {other} at up to "
                                       f"{ks.limit_price('BUY', pb, 0.5):g} on {broker.upper()}, {ks.SESSION_NAME[ks.session_now()]}.",
                 'warnings': warn}


def flip_go(data):
    f = _st['flips'].pop(str(data.get('code') or ''), None)
    if not f or time.time() - f['at'] > 120:
        return 400, {'error': 'That flip check has run out (2 minutes). Check it again.'}
    threading.Thread(target=_flip_run, args=(f,), name='flip', daemon=True).start()
    log('flip', f"flip {f['a']} -> {f['b']} started", sym=f['a'])
    return 200, {'started': True, 'text': f"Selling {f['a']}, then buying {f['b']}. You'll get a push with the result."}


def _flip_run(f):
    sold, avg, msg = sell_now(f['broker'], f['a'], f['qty_a'], 'flip', source='flip')
    if not sold:
        log('flip', f"flip stopped: {msg}", sym=f['a'])
        _push(f"Flip {f['a']} -> {f['b']} stopped", f"Nothing was sold: {msg}. Nothing was bought.", 'warning')
        return
    money = sold * (avg or 0)
    got, note = 0, ''
    for pct in (0.5, 1.0):
        pb = any_price(f['b'])
        if not pb:
            note = f"no price for {f['b']}"
            break
        lim = ks.limit_price('BUY', pb, pct)
        qty = int((money * 0.995) // lim) - got
        if qty < 1:
            break
        ok, t = ticket.place({'broker': f['broker'], 'sym': f['b'], 'side': 'buy', 'qty': qty, 'limit': lim, 'tif': 'day'}, 'flip')
        if not ok:
            note = t
            break
        filled, _, still = _wait_fill(t['id'], qty)
        if still:
            try:
                ticket.cancel({'id': t['id']})
            except Exception:
                pass
            time.sleep(2)
            ticket._last_refresh[0] = 0
            ticket.refresh()
            filled = float((ticket.ticket_by_id(t['id']) or {}).get('filled') or filled)
        got += int(filled)
        if filled >= qty:
            break
    text = f"{msg}; bought {got} {f['b']}" + (f" ({note})" if note else '')
    log('flip', text, sym=f['a'])
    _push(f"Flip {f['a']} -> {f['b']}", text + '.', 'arrows_counterclockwise' if got else 'warning')


# ------------------------------------------------------------------ chase (idea 6)
def chase(data):
    tid = str(data.get('id') or '')
    t = ticket.ticket_by_id(tid)
    if not t or t.get('status') not in ticket.OPEN:
        return 400, {'error': 'That order is not open.'}
    if tid in _st['chasing']:
        return 200, {'text': 'Already chasing this order.'}
    _st['chasing'].add(tid)
    threading.Thread(target=_chase_run, args=(t,), name='chase', daemon=True).start()
    return 200, {'text': f"Chasing {t['side']} {t['qty']} {t['sym']}: every 20 s it moves toward the market, up to 6 times and at most 1% past {t['limit']:g}."}


def _chase_run(t):
    first, cur, total = float(t['limit']), t, int(t['qty'])
    done = 0                                               # shares filled by earlier, finished orders
    left = total
    cap = first * (1.01 if t['side'] == 'buy' else 0.99)
    try:
        for _ in range(6):
            filled, _, still = _wait_fill(cur['id'], left)
            left = total - done - int(filled)
            if not still or left <= 0:
                break
            last = any_price(t['sym'])
            if not last:
                break
            new = ks.limit_price(t['side'].upper(), last, 0.2)
            new = min(new, cap) if t['side'] == 'buy' else max(new, cap)
            if abs(new - float(cur['limit'])) < 0.005:
                continue
            ticket.cancel({'id': cur['id']})
            time.sleep(2)
            ticket._last_refresh[0] = 0
            ticket.refresh()
            c2 = ticket.ticket_by_id(cur['id']) or cur
            if c2.get('status') in ticket.OPEN:
                break                                      # cancel not confirmed: never stack a second order
            done += int(float(c2.get('filled') or 0))
            left = total - done
            if left <= 0:
                break
            ok, nt = ticket.place({'broker': t['broker'], 'sym': t['sym'], 'side': t['side'], 'qty': left, 'limit': new, 'tif': t.get('tif') or 'day'}, 'chase')
            if not ok:
                log('chase', f"{t['sym']} chase stopped: {nt}", sym=t['sym'])
                return
            cur = nt
            log('chase', f"{t['sym']} moved to {new:g} ({left} shares)", sym=t['sym'])
    finally:
        _st['chasing'].discard(t['id'])


# ------------------------------------------------------------------ stale orders (idea 54)
def _stale(cfg, now):
    m = int(cfg.get('stale_min') or 0)
    by_sess = bool(cfg.get('stale_session'))
    if m <= 0 and not by_sess:
        return
    sess = ks.session_now(watch.et(now))
    for t in ticket.open_tickets():
        if t.get('tif', 'day') != 'day' or t.get('source', 'ticket') != 'ticket':
            continue
        old = m > 0 and now - t['at'] >= m * 60000
        moved = by_sess and sess and t.get('sess') and t['sess'] != sess
        if not old and not moved:
            continue
        why = f'did not fill in {m} minutes' if old else f"was still open when {ks.SESSION_NAME.get(sess, 'the next session')} started"
        code, res = ticket.cancel({'id': t['id']})
        if code == 200:
            log('stale', f"cancelled {t['side']} {t['qty']} {t['sym']} at {t['limit']:g}: {why}", sym=t['sym'])
            _push(f"Cancelled {t['sym']} order", f"{t['side'].upper()} {t['qty']} {t['sym']} at {t['limit']:g} {why}, so it was cancelled.", 'wastebasket')


# ------------------------------------------------------------------ scheduled orders (ideas 33, 63)
WHEN = {'open': (9, 30, 'regular'), 'pre': (4, 0, 'premarket'), 'night': (20, 0, 'overnight'), 'after': (16, 0, 'afterhours')}


def next_time(when, now=None):
    now = now or _now()
    if when in WHEN:
        hh, mm, sess = WHEN[when]
        d = watch.et(now).date()
        for i in range(10):
            t = watch.at_et(d + timedelta(days=i), hh, mm)
            if t > now and ks.session_now(watch.et(t + 60000)) == sess:
                return t
        return None
    try:
        from datetime import datetime
        x = datetime.fromisoformat(str(when))
        return watch.at_et(x.date(), x.hour, x.minute)
    except Exception:
        return None


def schedule(data):
    broker = str(data.get('broker') or '').lower()
    sym, side = _clean(data.get('sym')), str(data.get('side') or '').lower()
    if broker not in ('ibkr', 'webull') or not sym or side not in ('buy', 'sell'):
        return 400, {'error': 'Pick the broker, symbol and buy or sell.'}
    qty = 'all' if str(data.get('qty')).lower() == 'all' else _num(data.get('qty'))
    if not qty or (qty != 'all' and qty != math.floor(qty)) or (qty == 'all' and side != 'sell'):
        return 400, {'error': 'Whole shares (or "all" for a sell).'}
    when = str(data.get('when') or '')
    at = next_time(when)
    if not at or at <= _now():
        return 400, {'error': 'Pick when: the open, pre-market, overnight, after-hours, or a date and time (New York) in the future.'}
    if ks.session_now(watch.et(at + 1000)) is None:
        return 400, {'error': 'Markets are closed at that time.'}
    conds = []
    for c in (data.get('conds') or [])[:3]:
        cs, op, val = ''.join(ch for ch in str((c or {}).get('sym') or '').upper() if ch.isalnum() or ch in '.=^-')[:12], str((c or {}).get('op') or ''), _num((c or {}).get('val'))
        if not cs or op not in COND_OPS or val is None:
            return 400, {'error': 'Each condition needs a symbol, a test (up / down / above / below) and a number.'}
        conds.append({'sym': cs, 'op': op, 'val': val})
    s = {'id': uuid.uuid4().hex[:10], 'broker': broker, 'sym': sym, 'side': side, 'qty': qty if qty == 'all' else int(qty),
         'when': when, 'at': at, 'limit': _num(data.get('limit')), 'real': bool(data.get('real')), 'status': 'waiting', 'created': _now(),
         'conds': conds}
    _change(lambda v: v['sched'].append(s))
    log('schedule', f"{'REAL' if s['real'] else 'paper'} {side} {s['qty']} {sym} at {watch.et(at):%a %b %d %I:%M %p} ET" +
        (' if ' + ' and '.join(cond_text(c) for c in conds) if conds else ''), sym=sym)
    return 200, snapshot()


COND_OPS = ('up', 'down', 'above', 'below')


def cond_text(c):
    return f"{c['sym']} " + {'up': f"is up {c['val']:g}%+ vs the 4 pm close", 'down': f"is down {c['val']:g}%+ vs the 4 pm close",
                             'above': f"is above {c['val']:g}", 'below': f"is below {c['val']:g}"}[c['op']]


def cond_check(c, now=None):
    """(ok, what it is now) for one scheduled-order condition."""
    now = now or _now()
    if c['op'] in ('up', 'down'):
        st = watch.stats(c['sym'], now)
        v = st.get('close') if st else None
        if v is None:
            return False, f"no price vs the close for {c['sym']}"
        ok = v >= c['val'] if c['op'] == 'up' else v <= -c['val']
        return ok, f"{c['sym']} {v:+.2f}% vs close"
    p = any_price(c['sym'])
    if not p:
        return False, f"no price for {c['sym']}"
    return (p > c['val'] if c['op'] == 'above' else p < c['val']), f"{c['sym']} at {p:g}"


def sched_off(data):
    sid = str(data.get('id') or '')

    def f(v):
        for s in v['sched']:
            if s['id'] == sid and s['status'] == 'waiting':
                s['status'] = 'cancelled'
    _change(f)
    return 200, snapshot()


class _Skip(Exception):
    pass


def _run_sched(s):
    last = any_price(s['sym'])
    res, status = '', 'done'
    try:
        if not last:
            raise RuntimeError(f"no price for {s['sym']}")
        for c in s.get('conds') or []:
            ok, now_is = cond_check(c)
            if not ok:
                raise _Skip(f"skipped: wanted {cond_text(c)}, but {now_is}")
        qty = s['qty']
        if qty == 'all':
            qty = int(held(s['broker'], s['sym']))
            if qty <= 0:
                raise RuntimeError(f"you hold no {s['sym']}")
        lim = s.get('limit') or ks.limit_price(s['side'].upper(), last, 0.5)
        if not s['real']:
            status, res = 'paper', f"paper: would {s['side']} {qty} {s['sym']} at {lim:g} (price then {last:g})"
        elif s['side'] == 'sell' and not s.get('limit'):
            sold, avg, msg = sell_now(s['broker'], s['sym'], qty, 'scheduled', source='schedule')
            res, status = msg, 'done' if sold >= qty else 'failed'
        else:
            ok, t = ticket.place({'broker': s['broker'], 'sym': s['sym'], 'side': s['side'], 'qty': qty, 'limit': lim, 'tif': 'day'}, 'schedule')
            res, status = (f"sent {s['side']} {qty} {s['sym']} at {lim:g}: {t['status']}", 'done') if ok else (f'refused: {t}', 'failed')
    except _Skip as e:
        res, status = str(e)[:200], 'skipped'
    except Exception as e:
        res, status = str(e)[:200], 'failed'

    def f(v):
        for x in v['sched']:
            if x['id'] == s['id']:
                x.update(status=status, result=res, ran=_now())
    _change(f)
    log('schedule', f"{s['sym']}: {res}", sym=s['sym'])
    _push(f"Scheduled {s['side']} {s['sym']}" + ('' if s['real'] else ' (paper)'), res, 'alarm_clock' if status not in ('failed', 'skipped') else 'warning')


def _step_sched(now):
    due = []

    def f(v):
        for s in v['sched']:
            if s['status'] != 'waiting' or now < s['at']:
                continue
            if now - s['at'] > 10 * 60000:
                s.update(status='missed', result='the board was not running at that time')
                continue
            s['status'] = 'running'
            due.append(dict(s))
    _change(f)
    for s in due:
        threading.Thread(target=_run_sched, args=(s,), name='sched', daemon=True).start()


# ------------------------------------------------------------------ event guard (idea 34)
def _kill_at(name):
    import algoctl
    try:
        return json.load(open(algoctl._kill_path(name))).get('at')
    except Exception:
        return None


def _open_positions():
    try:
        import trading
        with trading._lock:
            j = trading._journal()
        _, open_ = trading.round_trips(list(j['fills'].values()))
        return ', '.join(f"{o['qty']:g} {o['sym']}" for o in open_ if o.get('qty'))[:200]
    except Exception:
        return ''


def _ev_save(v):
    try:
        if v:
            tmp = EVFILE + '.tmp'
            json.dump(v, open(tmp, 'w'))
            os.replace(tmp, EVFILE)
        elif os.path.exists(EVFILE):
            os.remove(EVFILE)
    except Exception:
        pass


def _step_events(cfg, now):
    if not (cfg.get('event_guard') or cfg.get('event_push')):
        return
    import algoctl
    try:
        import world
        events = [e for e in world._cal.get('events', []) if str(e.get('impact')) == 'High']
    except Exception:
        return
    before, after = int(cfg.get('event_before') or 10) * 60000, int(cfg.get('event_after') or 15) * 60000
    live = [e for e in events if e['t'] - before <= now <= e['t'] + after]
    if not _st['ev'] and os.path.exists(EVFILE):
        try:
            _st['ev'] = json.load(open(EVFILE)) or {}
        except Exception:
            _st['ev'] = {}
    ev = _st['ev']
    if live and not ev:
        e = live[0]
        paused = {}
        for name in (algoctl.DIRS if cfg.get('event_guard') else ()):     # pausing is its own switch (off at first)
            if os.path.isdir(algoctl.DIRS[name]) and not os.path.exists(algoctl._kill_path(name)):
                algoctl.post(name, {'action': 'stop'})
                paused[name] = _kill_at(name)
        _st['ev'] = {'t': e['t'], 'until': e['t'] + after, 'paused': paused, 'title': e['title']}
        _ev_save(_st['ev'])
        if paused:
            log('event', f"paused {', '.join(paused)} algo for {e['title']}")
        mine = _open_positions()
        _push(('Algos paused for news' if paused else 'Big news soon'), f"{e['title']} at {watch.et(e['t']):%I:%M %p} ET. " +
              (f"Paused the {' and '.join(p.upper() for p in paused)} algo (its position is closed) until {int(cfg.get('event_after') or 15)} minutes after. " if paused else '') +
              (f"You hold {mine}: these releases can gap 3x funds fast." if mine else 'You hold no positions.'), 'pause_button')
    elif ev and now > ev['until'] and not live:
        back = []
        for name, at in ev['paused'].items():
            if at and _kill_at(name) == at:               # still our pause, not SELL ALL or yours
                algoctl.post(name, {'action': 'resume'})
                back.append(name)
        _st['ev'] = {}
        _ev_save({})
        if back:
            log('event', f"resumed {', '.join(back)} algo after {ev['title']}")
            _push('Algos resumed', f"{ev['title']} is past: resumed the {' and '.join(b.upper() for b in back)} algo.", 'arrow_forward')


# ------------------------------------------------------------------ Sunday IB sign-in reminder (idea 35)
def _step_reminder(cfg, now, state):
    d = watch.et(now)
    if not cfg.get('ib_reminder') or d.weekday() != 6 or d.hour < 17:
        return
    wk = d.strftime('%Y-%m-%d')
    if state.get('reminded') == wk:
        return
    state['reminded'] = wk
    extra = ''
    try:
        import ibauto
        last = ibauto._st.get('last_2fa') or 0
        if last:
            extra = f" Last IB Key approval: {(now - last) / 86400000:.0f} days ago."
    except Exception:
        pass
    _push('IB Gateway: weekly sign-in', 'IBKR asks for a fresh sign-in once a week. If your phone gets an IB Key request tonight, tap Approve, '
          'so the board and the IBKR algo are ready when futures open at 6 pm.' + extra, 'key')


# ------------------------------------------------------------------ fill push + auto notes (ideas 58, 52)
def _context(sym, t):
    c = {'session': ks.SESSION_NAME.get(ks.session_now(watch.et(t)), '')}
    s = watch.stats(sym, t)
    if s and s.get('close') is not None:
        c['vs_close'] = round(s['close'], 2)
    for k in ('QQQ', 'SPY'):
        x = watch.stats(k, t)
        if x and x.get('close') is not None:
            c[k.lower()] = round(x['close'], 2)
    try:
        import world
        v = world._quotes.get('^VIX')
        if isinstance(v, dict) and v.get('last'):
            c['vix'] = round(float(v['last']), 1)
    except Exception:
        pass
    return c


def note_text(c):
    bits = [c.get('session') or '']
    if 'vs_close' in c:
        bits.append(f"{c['vs_close']:+.1f}% vs close")
    if 'qqq' in c:
        bits.append(f"QQQ {c['qqq']:+.1f}%")
    if 'spy' in c:
        bits.append(f"SPY {c['spy']:+.1f}%")
    if 'vix' in c:
        bits.append(f"VIX {c['vix']:g}")
    if c.get('by'):
        bits.append(c['by'])
    return ', '.join(b for b in bits if b)


def _ib_fills():
    import iblink

    async def go(ib):
        import asyncio
        out = []
        for f in await asyncio.wait_for(ib.reqExecutionsAsync(), 15):
            c, e = f.contract, f.execution
            sym = c.symbol if c.secType == 'STK' else (c.localSymbol or c.symbol)
            out.append({'id': 'ibkr:' + e.execId, 'broker': 'ibkr', 'sym': sym, 'side': 'buy' if e.side == 'BOT' else 'sell',
                        'qty': float(e.shares), 'price': float(e.price), 't': int(e.time.timestamp() * 1000),
                        'ref': getattr(e, 'orderRef', '') or ''})
        return out
    return iblink.run(go, timeout=25)


def _wb_fills():
    import trading
    return [dict(f, ref='') for f in trading.fetch_webull()]


def _by(f):
    ref = f.get('ref') or ''
    if ref.startswith('jb-ticket-'):
        t = ticket.ticket_by_id(ref[10:])
        if t and t.get('source') and t['source'] != 'ticket':
            return {'guard': 'a safety stop / target', 'flip': 'the flip button', 'chase': 'chasing', 'schedule': 'a scheduled order'}.get(t['source'], t['source'])
        return 'your order ticket'
    if 'algo' in ref.lower():
        return 'an algo'
    return ''


def fill_gap(f):
    """' (0.04 better than your 52.10 limit)' or ' (0.3% above the last price 52.00)' or ''."""
    ref = f.get('ref') or ''
    t = ticket.ticket_by_id(ref[10:]) if ref.startswith('jb-ticket-') else None
    buy = f['side'] == 'buy'
    if t and t.get('limit'):
        d = (t['limit'] - f['price']) if buy else (f['price'] - t['limit'])
        if abs(d) < 0.005:
            return f" (right at your {t['limit']:g} limit)"
        return f" ({abs(d):.2f} {'better' if d > 0 else 'worse'} than your {t['limit']:g} limit)"
    with watch._lock:
        h = list(watch._px.get(f['sym']) or [])
    last = watch.price_at(h, f['t'] - 1000) if h else None
    if not last:
        return ''
    p = (f['price'] / last - 1) * 100
    return f" ({abs(p):.2f}% {'above' if p >= 0 else 'below'} the last price {last:g})"


def _step_fills(cfg, now):
    if not cfg.get('fill_push') and not os.path.exists(SEEN):
        return
    got = []
    if now - _st['ib_at'] >= 30000:
        _st['ib_at'] = now
        try:
            got += _ib_fills()
        except Exception:
            pass
    sess = ks.session_now()
    if now - _st['wb_at'] >= 180000 and sess:
        _st['wb_at'] = now
        if os.environ.get('WEBULL_APP_KEY'):
            try:
                got += _wb_fills()
            except Exception as e:
                print(f'orders: webull fills: {e!r}', flush=True)
    if not got:
        return
    seen = watch._read(SEEN, {})
    seen = seen if isinstance(seen, dict) else {}
    ids = set(seen.get('ids') or [])
    first = {b: not seen.get('primed_' + b) for b in ('ibkr', 'webull')}
    new = [f for f in got if f['id'] not in ids]
    if not new:
        return
    notes = watch._read(NOTES, {})
    notes = notes if isinstance(notes, dict) else {}
    for f in sorted(new, key=lambda x: x['t']):
        ids.add(f['id'])
        if first.get(f['broker']) or now - f['t'] > 6 * 3600000:
            continue                                       # the first look only learns what is already there
        c = _context(f['sym'], f['t'])
        c['by'] = _by(f)
        notes[f['id']] = {'sym': f['sym'], 'side': f['side'], 'qty': f['qty'], 'price': f['price'], 't': f['t'], 'ctx': c, 'text': note_text(c)}
        if cfg.get('fill_push'):
            _push(f"Filled: {f['side'].upper()} {f['qty']:g} {f['sym']}",
                  f"{f['broker'].upper()}: {f['side']} {f['qty']:g} {f['sym']} at {f['price']:g}{fill_gap(f)}. {note_text(c)}.", 'moneybag')
    for b in ('ibkr', 'webull'):
        if any(f['broker'] == b for f in got):
            seen['primed_' + b] = True
    seen['ids'] = list(ids)[-2000:]
    watch._write(SEEN, seen)
    if len(notes) > 3000:
        notes = dict(sorted(notes.items(), key=lambda kv: kv[1].get('t', 0))[-3000:])
    watch._write(NOTES, notes)


def notes():
    v = watch._read(NOTES, {})
    return v if isinstance(v, dict) else {}


# ------------------------------------------------------------------ loop
def _loop():
    state = {}
    n = 0
    while True:
        now = _now()
        try:
            if ks.session_now() is not None:
                _step_guards(now)
        except Exception as e:
            print(f'orders: guards: {e!r}', flush=True)
        if n % 5 == 0:                                     # every ~15 s
            cfg = _load()['cfg']
            for fn in (lambda: _step_sched(now), lambda: _stale(cfg, now), lambda: _step_events(cfg, now),
                       lambda: _step_reminder(cfg, now, state), lambda: _step_fills(cfg, now)):
                try:
                    fn()
                except Exception as e:
                    print(f'orders: {e!r}', flush=True)
        n += 1
        time.sleep(3)


def _unstick_guards():
    """review 2026-10-11 #10: a stop that was selling when the board restarted is left 'firing' and would never
    watch again. Put it back on: it fires again if the price is still past the level, and the sale only ever
    sells what you still hold."""
    def f(v):
        back = []
        for g in v['guards']:
            if g.get('status') == 'firing':
                g['status'], g['hits'] = 'on', 0
                g['note'] = 'the board restarted while this was selling; watching again (check the broker app)'
                back.append(g['sym'])
        return back
    try:
        back = _change(f)
    except Exception as e:
        print(f'orders: could not check stuck stops: {e!r}', flush=True)
        return
    if back:
        _push('Stops watching again', f"The board restarted while selling {', '.join(back)}. Those stops are on again and "
              'sell only what you still hold. Check the broker app.', 'warning')


def start():
    if _st['started']:
        return
    _st['started'] = True
    _unstick_guards()
    try:
        import iblink
        iblink.start()
    except Exception as e:
        print(f'orders: iblink: {e!r}', flush=True)
    threading.Thread(target=_loop, name='orders', daemon=True).start()


def snapshot():
    v = _load()
    lst = watch._read(LOG, [])
    for g in v['guards']:
        g['text'] = describe(g)
    for s in v['sched']:
        s['at_text'] = watch.et(s['at']).strftime('%a %b %d %I:%M %p') + ' ET'
        if s.get('conds'):
            s['if_text'] = ' and '.join(cond_text(c) for c in s['conds'])
    try:
        import iblink
        link = iblink.status()
    except Exception:
        link = {'connected': False}
    return {'guards': [g for g in v['guards'] if g['status'] in ('on', 'waiting', 'firing') or _now() - g.get('done_at', g['created']) < 3 * 86400000][-30:],
            'sched': [s for s in v['sched'] if s['status'] in ('waiting', 'running') or _now() - s['at'] < 3 * 86400000][-30:],
            'cfg': v['cfg'], 'sizes': v['sizes'], 'log': lst[:40] if isinstance(lst, list) else [], 'iblink': link,
            'session': ks.SESSION_NAME[ks.session_now()], 'open': ticket.open_tickets()[:20]}


def settings(data):
    def f(v):
        c = v['cfg']
        if 'stale_min' in data:
            c['stale_min'] = max(0, min(600, int(_num(data.get('stale_min')) or 0)))
        for k in ('event_guard', 'event_push', 'ib_reminder', 'fill_push', 'stale_session'):
            if k in data:
                c[k] = bool(data[k])
        for k in ('event_before', 'event_after'):
            if k in data and _num(data.get(k)):
                c[k] = max(1, min(120, int(_num(data[k]))))
    _change(f)
    return 200, snapshot()


def sizes(data):
    def f(v):
        s = v['sizes']
        if isinstance(data.get('dollars'), list):
            s['dollars'] = [int(x) for x in (_num(y) for y in data['dollars'][:6]) if x][:6]
        sym, qty = _clean(data.get('sym')), _num(data.get('qty'))
        if sym and qty:
            s['syms'][sym] = int(qty)
            if len(s['syms']) > 60:
                s['syms'].pop(next(iter(s['syms'])))
    _change(f)
    return 200, snapshot()


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    if sub == 'notes':
        return 200, notes()
    if sub == 'twin':
        return 200, {'twin': twin(_clean(ticket._q(query, 'sym')))}
    return 404, {'error': 'unknown'}


POSTS = {'guard': add_guard, 'guard/off': guard_off, 'flip/check': flip_check, 'flip/go': flip_go, 'chase': chase,
         'schedule': schedule, 'schedule/off': sched_off, 'settings': settings, 'sizes': sizes}


def post(sub, data):
    fn = POSTS.get(sub)
    return fn(data) if fn else (404, {'error': 'unknown'})


def _webull_overnight():
    """Does the Webull plan stream the overnight session right now (lib/jb_webull.py, hedge-2026-10-12)?"""
    try:
        import jb_webull
        return jb_webull.overnight_state().get('on') is True
    except Exception:
        return False
