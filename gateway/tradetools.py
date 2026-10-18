"""Trading tools (jb-tools2, deep search batch B), running inside the gateway. All free, all from data the
board already has (your journal, the board's prices, Yahoo options the Night desk already reads).

  Real exposure (idea 3)     everything you hold on both brokers in index terms: $10,000 of TQQQ counts as
                             $30,000 of Nasdaq-100, SQQQ counts as short. Amber when you hold both sides of
                             the same index, or more than the size you set.
  Your trades on the chart (idea 4)   your journal fills as arrows on the Chart page (GET fills?sym=).
  Your own price record (idea 5)      every finished day, the minutes outside regular hours (after-hours,
                             overnight, pre-market) from the price record are copied, compressed, into
                             data/history/<broker>/<date>.csv.gz and kept for good (the record itself is
                             still trimmed to 35 days). About 10 MB a month.
  Earnings move for your ETFs (idea 6)  when a big holding of a 2x/3x fund on your boards (or held) reports
                             today after the close or tomorrow before the open: the options' expected move
                             x its weight x the leverage. A push at 3:30 pm ET.
  Cool-down (idea 7)         after 2 losing trades in a row, or at half the daily loss limit: a push, and an
                             amber note on the order ticket. It never blocks an order.
  Wash-sale watch (idea 11)  a loss sale bought back within 30 days postpones that loss for taxes. A note on
                             the ticket, and a running total for the year. Information only (it checks buys
                             after the sale; the tax rule also counts buys in the 30 days before it).
  Exit plans (idea 57)       named plans such as "Overnight scalp": sell a third at +2%, a third at +4%,
                             trail the rest 2%, stop -2%, stop to break-even after +1.5%. Picked on the
                             ticket (it starts once the buy fills) or put on what you already hold. The
                             board's own safety guards (gateway/orders.py) do the watching and selling.

  GET  /api/plus/tradetools               everything above for the phone and My trading
  GET  /api/plus/tradetools/fills?sym=    your fills of one symbol (chart arrows)
  GET  /api/plus/tradetools/templates     exit plans (for the ticket)
  GET  /api/plus/tradetools/history?sym=&days=   your kept price record outside regular hours
  POST /api/plus/tradetools/settings      {cool_on, cool_losses, cool_half, expo_max, wash_on, earn_push, archive_on}
  POST /api/plus/tradetools/template      {name, stop, be, parts: [{pct, target} | {pct, trail}]}
  POST /api/plus/tradetools/template/off  {name}
  POST /api/plus/tradetools/plan          {broker, sym, template, after: [ticket ids]} or {broker, sym, template} for what you hold
Files: data/tradetools.json (settings, exit plans), data/tradetools_state.json, data/history/.
"""
import glob
import gzip
import math
import os
import threading
import time
from datetime import timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
FILE = os.path.join(DATA, 'tradetools.json')
STATE = os.path.join(DATA, 'tradetools_state.json')
HIST = os.path.join(DATA, 'history')
DAY_MS = 86400000

DEFAULTS = {'cool_on': True, 'cool_losses': 2, 'cool_half': True, 'cool_min': 30, 'expo_max': 0.0,
            'wash_on': True, 'earn_push': True, 'archive_on': True}
PLANS = [
    {'name': 'Overnight scalp', 'stop': 2.0, 'be': 1.5,
     'parts': [{'pct': 33, 'target': 2.0}, {'pct': 33, 'target': 4.0}, {'pct': 34, 'trail': 2.0}]},
    {'name': 'Swing', 'stop': 4.0, 'be': 3.0, 'parts': [{'pct': 50, 'target': 6.0}, {'pct': 50, 'trail': 4.0}]},
    {'name': 'Quick in and out', 'stop': 1.5, 'be': 0, 'parts': [{'pct': 100, 'target': 2.0}]},
]
_lock = threading.Lock()
_st = {'started': False, 'em': {}, 'cal': {}, 'earn': [], 'earn_at': 0, 'wait_until': 0, 'err': {}, 'arch': {'at': 0, 'days': 0, 'mb': 0.0}}


def _now():
    return int(time.time() * 1000)


def _cfg():
    v = watch._read(FILE, {})
    return v if isinstance(v, dict) else {}


def settings():
    s = dict(DEFAULTS)
    s.update({k: v for k, v in (_cfg().get('settings') or {}).items() if k in DEFAULTS})
    return s


def plans():
    p = _cfg().get('plans')
    return p if isinstance(p, list) else [dict(x) for x in PLANS]


def _save(**kw):
    with _lock:
        v = _cfg()
        v.update(kw)
        watch._write(FILE, v)


def _state():
    v = watch._read(STATE, {})
    return v if isinstance(v, dict) else {}


def _usd(v):
    return ('-' if v < 0 else '') + '$' + f'{abs(v):,.0f}'


def _k(v):
    v = abs(v)
    return f'${v / 1000:,.1f}k' if v >= 1000 else f'${v:,.0f}'


def last(sym):
    with watch._lock:
        h = watch._px.get(sym)
        return float(h[-1][1]) if h else None


def _journal():
    import trading
    with trading._lock:
        j = trading._journal()
    fills = list(j['fills'].values())
    trips, open_ = trading.round_trips(fills)
    return fills, trips, open_


# ------------------------------------------------------------------ real exposure (idea 3)
def _pairs():
    try:
        import mdata
        return mdata.pairs()
    except Exception:
        return []


def index_of(sym):
    """-> (index name, factor): TQQQ -> ('Nasdaq-100', 3), SQQQ -> ('Nasdaq-100', -3), QQQ -> ('Nasdaq-100', 1)."""
    for name, base, lev, bull, bear in _pairs():
        if sym == bull:
            return name, lev
        if sym == bear:
            return name, -lev
    for name, base, lev, bull, bear in _pairs():
        if sym == base:
            return name, 1
    return sym, 1


def exposure(extra=None):
    """extra: (sym, dollars) of a buy being checked on the ticket, added as if it filled."""
    import ticket
    cfg = settings()
    rows = {}
    for p in ticket.positions() + ([{'broker': 'this order', 'sym': extra[0], 'qty': 1, 'price': extra[1], 'dollars': extra[1]}] if extra else []):
        if not p.get('qty') or p['qty'] <= 0:
            continue
        val = p.get('dollars') or p['qty'] * (last(p['sym']) or p['price'])
        idx, f = index_of(p['sym'])
        r = rows.setdefault(idx, {'index': idx, 'long': 0.0, 'short': 0.0, 'held': []})
        if f >= 0:
            r['long'] += val * f
        else:
            r['short'] += val * -f
        r['held'].append({'sym': p['sym'], 'broker': p['broker'], 'value': round(val), 'x': f})
    out, gross = [], 0.0
    for r in rows.values():
        r['net'] = round(r['long'] - r['short'])
        r['long'], r['short'] = round(r['long']), round(r['short'])
        r['both'] = r['long'] > 0 and r['short'] > 0
        gross += r['long'] + r['short']
        out.append(r)
    out.sort(key=lambda r: -abs(r['net']))
    words = [('long ' if r['net'] >= 0 else 'short ') + _k(r['net']) + ' ' + r['index'] for r in out if abs(r['net']) >= 1]
    warn = [f"You hold both sides of {r['index']} ({', '.join(dict.fromkeys(h['sym'] for h in r['held']))}): they partly cancel out, and both lose value in a choppy market."
            for r in out if r['both']]
    if cfg['expo_max'] and gross > cfg['expo_max']:
        warn.append(f"All together you hold {_k(gross)} in index terms, over the {_k(cfg['expo_max'])} you set.")
    return {'rows': out, 'gross': round(gross), 'text': ('Net: ' + ', '.join(words)) if words else 'Nothing held in your journal.',
            'warn': warn, 'max': cfg['expo_max']}


# ------------------------------------------------------------------ cool-down (idea 7)
def cooldown(trips=None):
    import trading
    cfg = settings()
    if trips is None:
        _, trips, _ = _journal()
    today = trading.trading_day(_now())
    td = sorted((t for t in trips if t['day'] == today), key=lambda t: t['exit_t'])
    streak, last_loss = 0, None
    for t in reversed(td):
        if t['pnl'] < 0:
            streak += 1
            last_loss = last_loss or t
        else:
            break
    realized = sum(t['pnl'] for t in td)
    lim = float(trading.settings().get('loss_limit') or 0)
    half = cfg['cool_half'] and lim > 0 and realized <= -lim / 2
    until = (last_loss['exit_t'] + cfg['cool_min'] * 60000) if last_loss and streak >= cfg['cool_losses'] else 0
    on = cfg['cool_on'] and (until > _now() or half)
    why = []
    if until > _now():
        why.append(f"{streak} losing trades in a row (last one {watch.et(last_loss['exit_t']).strftime('%-I:%M %p')})")
    if half:
        why.append(f"down {_usd(-realized)} on closed trades today, half your {_usd(lim)} daily loss limit")
    return {'on': bool(on), 'streak': streak, 'realized': round(realized, 2), 'half': bool(half), 'until': until,
            'text': ('Cool-down: ' + ' and '.join(why) + f'. Consider a {cfg["cool_min"]}-minute break before the next trade.') if on else ''}


def _cool_push(state):
    c = cooldown()
    if not c['on']:
        return
    import trading
    key = f"cool|{trading.trading_day(_now())}|{'half' if c['half'] else c['streak']}"
    sent = state.setdefault('sent', {})
    if key in sent:
        return
    sent[key] = _now()
    t = c['text'][len('Cool-down: '):]
    watch.push_later('Consider a short break', t[:1].upper() + t[1:] + ' Nothing is blocked: this is just a heads-up.', 'hourglass')


# ------------------------------------------------------------------ wash-sale watch (idea 11)
def wash(fills=None, trips=None):
    if fills is None:
        fills, trips, _ = _journal()
    now = _now()
    year = watch.et(now).year
    buys = sorted((f for f in fills if f['side'] == 'buy'), key=lambda f: f['t'])
    left = {f['id']: f['qty'] for f in buys}
    rows, open_ = [], []
    for t in sorted((t for t in trips if t['pnl'] < 0 and t['side'] == 'long'), key=lambda t: t['exit_t']):
        own = t['id'].split(':', 1)[1] if ':' in t['id'] else ''
        need, got, when = t['qty'], 0.0, None
        for b in buys:
            if b['sym'] != t['sym'] or b['id'] == own or not left.get(b['id']):
                continue
            if t['exit_t'] < b['t'] <= t['exit_t'] + 30 * DAY_MS:          # bought back after the loss sale
                n = min(left[b['id']], need - got)
                left[b['id']] -= n
                got += n
                when = when or b['t']
                if got >= need:
                    break
        post = round(-t['pnl'] * min(1.0, got / need), 2) if need else 0
        end = t['exit_t'] + 30 * DAY_MS
        r = {'sym': t['sym'], 'sold': t['exit_t'], 'loss': round(-t['pnl'], 2), 'qty': t['qty'], 'postponed': post,
             'bought_back': when, 'until': end}
        if post > 0:
            rows.append(r)
        elif end > now:
            open_.append(r)
    ytd = sum(r['postponed'] for r in rows if watch.et(r['sold']).year == year)
    return {'year': year, 'postponed': round(ytd, 2), 'rows': sorted(rows, key=lambda r: -r['sold'])[:50],
            'open': sorted(open_, key=lambda r: r['until'])}


# ------------------------------------------------------------------ ticket notes (ideas 3, 7, 11)
def ticket_notes(sym, side, qty, limit):
    """Extra warning lines for the order ticket. Never blocks anything."""
    cfg = settings()
    out = []
    try:
        fills, trips, _ = _journal()
        c = cooldown(trips)
        if c['on']:
            out.append(c['text'] + ' (Never blocks an order.)')
        if side == 'buy' and cfg['wash_on']:
            for r in wash(fills, trips)['open']:
                if r['sym'] == sym:
                    out.append(f"Wash sale: you sold {sym} at a {_usd(r['loss'])} loss on {watch.et(r['sold']).strftime('%b %-d')}. Buying it back before "
                               f"{watch.et(r['until']).strftime('%b %-d')} postpones that loss for taxes (it is added to the cost of the new shares). Information only.")
                    break
        if side == 'buy':
            e = exposure((sym, qty * limit))
            if e['warn']:
                out.append('Exposure after this buy: ' + ' '.join(e['warn']))
    except Exception as ex:
        print(f'tradetools: ticket notes: {ex!r}', flush=True)
    return out


# ------------------------------------------------------------------ chart arrows (idea 4)
def fills_of(sym):
    fills, trips, _ = _journal()
    pnl = {}
    for t in trips:
        fid = t['id'].split(':', 1)[0]
        pnl[fid] = pnl.get(fid, 0) + t['pnl']
    out = [{'t': f['t'], 'side': f['side'], 'qty': f['qty'], 'price': f['price'], 'broker': f['broker'],
            'pnl': round(pnl[f['id']], 2) if f['id'] in pnl else None} for f in fills if f['sym'] == sym]
    return sorted(out, key=lambda f: f['t'])[-300:]


# ------------------------------------------------------------------ own price record (idea 5)
def _regular(ms):
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    return d.weekday() < 5 and 570 <= m < 960


def archive_step():
    if not settings()['archive_on']:
        return 0
    today = time.strftime('%Y-%m-%d', time.gmtime())
    done = 0
    for broker in ('ibkr', 'webull'):
        src = os.path.join(DATA, broker + '_ticks')
        dst = os.path.join(HIST, broker)
        for p in sorted(glob.glob(os.path.join(src, '*.csv'))):
            day = os.path.basename(p)[:-4]
            out = os.path.join(dst, day + '.csv.gz')
            if day >= today or os.path.exists(out):
                continue
            keep = []
            try:
                with open(p, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        parts = line.strip().split(',')
                        if len(parts) == 3 and parts[1].isdigit() and not _regular(int(parts[1])):
                            keep.append(line if line.endswith('\n') else line + '\n')
                os.makedirs(dst, exist_ok=True)
                with gzip.open(out + '.tmp', 'wt', encoding='utf-8') as g:
                    g.write('symbol,minute_ms,price\n' + ''.join(keep))
                os.replace(out + '.tmp', out)
                done += 1
            except OSError as e:
                print(f'tradetools: archive {p}: {e!r}', flush=True)
                return done
    files = glob.glob(os.path.join(HIST, '*', '*.csv.gz'))
    _st['arch'] = {'at': _now(), 'days': len({os.path.basename(f) for f in files}),
                   'mb': round(sum(os.path.getsize(f) for f in files) / 1e6, 1),
                   'first': min((os.path.basename(f)[:10] for f in files), default='')}
    return done


def history(sym, days=60):
    """Minutes outside regular hours for one symbol from the kept record (IBKR first, Webull where IBKR has none)."""
    sym = sym.upper()
    start = time.strftime('%Y-%m-%d', time.gmtime(time.time() - days * 86400))
    rows = {}
    for broker in ('webull', 'ibkr'):
        for p in sorted(glob.glob(os.path.join(HIST, broker, '*.csv.gz'))):
            if os.path.basename(p)[:10] < start:
                continue
            try:
                with gzip.open(p, 'rt', encoding='utf-8', errors='replace') as g:
                    for line in g:
                        if line.startswith(sym + ','):
                            a = line.strip().split(',')
                            try:
                                rows[int(a[1])] = (float(a[2]), broker)
                            except (ValueError, IndexError):
                                pass
            except OSError:
                pass
    import trading
    return [[t, p, b, trading.session_of(t)] for t, (p, b) in sorted(rows.items())]


# ------------------------------------------------------------------ earnings move for your ETFs (idea 6)
def _watched_funds():
    import ticket
    held = {p['sym'] for p in ticket.positions()}
    with watch._lock:
        board = set(watch._px)
    out = []
    for name, base, lev, bull, bear in _pairs():
        for s, f in ((bull, lev), (bear, -lev)):
            if s and (s in held or s in board):
                out.append((s, base, f, s in held))
    return out


def _report_window(e, now):
    """True for a report today after the close (or time unknown) or on the next trading day before the open."""
    d = watch.et(now).date()
    if not e.get('date'):
        return ''
    if e['date'] == d.isoformat() and e.get('when') != 'before the open' and watch.et(now).hour < 17:
        return 'today after the close'
    nxt = d + timedelta(days=1)
    try:
        import trading
        while not trading._market_day(nxt):
            nxt += timedelta(days=1)
    except Exception:
        pass
    if e['date'] == nxt.isoformat() and e.get('when') == 'before the open':
        return ('tomorrow' if (nxt - d).days == 1 else nxt.strftime('%A')) + ' before the open'
    if e['date'] == d.isoformat() and e.get('when') == 'before the open' and watch.et(now).hour < 9:
        return 'this morning before the open'
    return ''


def earn_step():
    """One Yahoo lookup at most per call; builds the list of ETF earnings moves."""
    now = _now()
    try:
        import earn
        import edge
    except Exception:
        return
    dates = earn._earn_dates()
    rows, fetched = [], False
    for etf, base, lev, held in _watched_funds():
        hold = edge.holdings(base)
        if not hold and not fetched and time.time() > _st['wait_until']:
            fetched = True
            try:
                edge.holdings_view(base)
                hold = edge.holdings(base)
            except Exception as e:
                _st['err']['holdings ' + base] = str(e)[:120]
        for h in hold[:15]:
            e = dates.get(h['sym']) or {}
            if not e.get('date') and h['w'] >= 3:              # not in the scanner's calendar: Yahoo's date, checked once a day
                c = _st['cal'].get(h['sym'])
                if (not c or time.time() - c['at'] > 86400) and not fetched and time.time() > _st['wait_until']:
                    fetched = True
                    try:
                        c = earn.fetch_calendar(h['sym'])
                    except Exception as ex:
                        c = {'at': time.time(), 'release': None}
                        if '429' in str(ex):
                            _st['wait_until'] = time.time() + 600
                    _st['cal'][h['sym']] = c
                if c and c.get('release'):
                    r = watch.et(c['release'])
                    e = {'date': r.date().isoformat(), 'when': 'before the open' if r.hour < 12 else 'after the close'}
            when = _report_window(e, now)
            if not when:
                continue
            em = (earn._st.get('em') or {}).get(h['sym'])
            if not (em and em.get('pct') and em.get('date') == e['date']):
                em = _st['em'].get(h['sym'])
            if (not em or em.get('date') != e['date'] or time.time() - em.get('at', 0) > 3 * 3600) and not fetched and time.time() > _st['wait_until']:
                fetched = True
                try:
                    em = earn.fetch_expected(h['sym'], e['date'], e.get('when') or '', now)
                    em['date'] = e['date']
                except Exception as ex:
                    em = {'at': time.time() - 2.5 * 3600, 'date': e['date']}
                    _st['err']['options ' + h['sym']] = str(ex)[:120]
                    if '429' in str(ex):
                        _st['wait_until'] = time.time() + 600
                _st['em'][h['sym']] = em
            if not em or not em.get('pct'):
                continue
            move = round(h['w'] / 100 * em['pct'] * abs(lev), 1)
            rows.append({'etf': etf, 'base': base, 'lev': lev, 'held': held, 'sym': h['sym'], 'w': h['w'], 'when': when,
                         'em': em['pct'], 'move': move,
                         'text': f"{h['sym']} reports {when}, expected ±{em['pct']:g}%. It is about {h['w']:.0f}% of {base}, so "
                                 f"{etf}'s expected move from it is about ±{move:g}%."})
    rows.sort(key=lambda r: (not r['held'], -r['move']))
    _st['earn'], _st['earn_at'] = rows, now


def _earn_push(state):
    if not settings()['earn_push'] or not _st['earn']:
        return
    d = watch.et(_now())
    if not (d.hour == 15 and 30 <= d.minute < 50):
        return
    key = 'earn|' + d.date().isoformat()
    if key in state.setdefault('sent', {}):
        return
    state['sent'][key] = _now()
    best = {}
    for r in _st['earn']:
        best.setdefault(r['etf'], r)
    lines = [r['text'] for r in list(best.values())[:4]]
    watch.push_later('Earnings moves for your ETFs', ' '.join(lines), 'calendar')


# ------------------------------------------------------------------ exit plans (idea 57)
def _clean_plan(d):
    name = str(d.get('name') or '').strip()[:40]
    if not name:
        raise ValueError('Give the plan a name.')

    def num(v, lo, hi):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.0
        return round(v, 2) if math.isfinite(v) and lo <= v <= hi else 0.0
    stop, be = num(d.get('stop'), 0, 30), num(d.get('be'), 0, 50)
    parts = []
    for p in (d.get('parts') or [])[:4]:
        pct = num(p.get('pct'), 1, 100)
        tgt, tr = num(p.get('target'), 0.1, 200), num(p.get('trail'), 0.1, 30)
        if pct and (tgt or tr):
            parts.append({'pct': pct, 'target': tgt} if tgt else {'pct': pct, 'trail': tr})
    if not parts:
        raise ValueError('Add at least one part with a target % or a trailing %.')
    tot = sum(p['pct'] for p in parts)
    if abs(tot - 100) > 1:
        raise ValueError(f'The parts add up to {tot:g}%; they need to add up to 100%.')
    if not stop and not any('trail' in p for p in parts):
        raise ValueError('Set a stop % (or a trailing part), so every share is protected.')
    return {'name': name, 'stop': stop, 'be': be, 'parts': parts}


def plan_text(p):
    bits = []
    for x in p['parts']:
        bits.append(f"{x['pct']:g}% at +{x['target']:g}%" if x.get('target') else f"{x['pct']:g}% trails {x['trail']:g}%")
    if p.get('stop'):
        bits.append(f"stop -{p['stop']:g}%")
    if p.get('be'):
        bits.append(f"stop to break-even after +{p['be']:g}%")
    return ', '.join(bits)


def _split(qty, parts):
    qty = int(qty)
    qs = [int(qty * p['pct'] / 100) for p in parts]
    qs[-1] = qty - sum(qs[:-1])
    out = [(q, p) for q, p in zip(qs, parts) if q > 0]
    return out


def _make_guards(broker, sym, qty, entry, plan):
    import orders
    msgs, made = [], 0
    for q, part in _split(qty, plan['parts']):
        g = {'broker': broker, 'sym': sym, 'qty': q, 'entry': entry,
             'stop': round(entry * (1 - plan['stop'] / 100), 4) if plan.get('stop') else None,
             'be': plan.get('be') or None}
        if part.get('target'):
            g['target'] = round(entry * (1 + part['target'] / 100), 4)
        else:
            g['trail'] = part['trail']
        code, body = orders.add_guard(g)
        if code == 200:
            made += 1
        else:
            msgs.append((body or {}).get('error', 'not set') if isinstance(body, dict) else 'not set')
    return made, msgs


def apply_plan(data):
    name = str(data.get('template') or '')
    plan = next((p for p in plans() if p['name'] == name), None)
    broker = str(data.get('broker') or '').lower()
    sym = ''.join(ch for ch in str(data.get('sym') or '').upper() if ch.isalnum() or ch == '.')[:12]
    if not plan or broker not in ('ibkr', 'webull') or not sym:
        return 400, {'error': 'Pick an exit plan, a broker and a symbol.'}
    after = data.get('after') or []
    if isinstance(after, str):
        after = [after]
    after = [str(a)[:40] for a in after if a][:5]
    if after:
        with _lock:
            st = _state()
            st.setdefault('pending', []).append({'broker': broker, 'sym': sym, 'plan': name, 'after': after, 'at': _now()})
            watch._write(STATE, st)
        return 200, {'text': f"Exit plan “{name}” ({plan_text(plan)}) starts once the buy fills."}
    import ticket
    pos = next((p for p in ticket.positions() if p['broker'] == broker and p['sym'] == sym), None)
    if not pos:
        return 400, {'error': f'No {sym} position on {broker.upper()} in your journal.'}
    made, msgs = _make_guards(broker, sym, pos['qty'], pos['price'], plan)
    if not made:
        return 400, {'error': 'Not set: ' + '; '.join(msgs)}
    return 200, {'text': f"Exit plan “{name}” is on for {int(pos['qty'])} {sym} from {pos['price']:g}: {plan_text(plan)}." +
                 (' Some parts were not set: ' + '; '.join(msgs) if msgs else '')}


def _pending_step():
    import ticket
    with _lock:
        st = _state()
        pend = st.get('pending') or []
    if not pend:
        return
    keep, ready = [], []
    for p in pend:
        ts = [ticket.ticket_by_id(a) for a in p['after']]
        if any(t is None for t in ts) or _now() - p['at'] > 4 * DAY_MS:
            continue
        if any(t.get('status') in ticket.OPEN for t in ts):
            keep.append(p)
            continue
        filled = sum(float(t.get('filled') or 0) for t in ts)
        if filled > 0:
            avg = sum(float(t.get('filled') or 0) * float(t.get('avg') or t.get('limit') or 0) for t in ts) / filled
            ready.append((p, int(filled), avg))
    with _lock:
        st = _state()
        st['pending'] = keep
        watch._write(STATE, st)
    for p, qty, avg in ready:
        plan = next((x for x in plans() if x['name'] == p['plan']), None)
        if not plan:
            continue
        made, msgs = _make_guards(p['broker'], p['sym'], qty, avg, plan)
        watch.push_later(f"{p['sym']} exit plan on" if made else f"{p['sym']} exit plan NOT set",
                         f"{qty} {p['sym']} filled at {avg:.2f}. “{p['plan']}”: {plan_text(plan)}." if made else
                         f"{qty} {p['sym']} filled, but the exit plan could not be set: {'; '.join(msgs)}. Set a stop now.",
                         'shield' if made else 'rotating_light')


# ------------------------------------------------------------------ loop + API
def _loop():
    time.sleep(45)
    n = 0
    while True:
        try:
            _pending_step()
        except Exception as e:
            print(f'tradetools: plans: {e!r}', flush=True)
        if n % 4 == 0:
            try:
                state = _state()
                if settings()['cool_on']:
                    _cool_push(state)
                _earn_push(state)
                for k in [k for k, t in (state.get('sent') or {}).items() if _now() - t > 3 * DAY_MS]:
                    del state['sent'][k]
                with _lock:
                    cur = _state()
                    cur['sent'] = state.get('sent', {})
                    watch._write(STATE, cur)
            except Exception as e:
                print(f'tradetools: pushes: {e!r}', flush=True)
        if n % 8 == 0:
            try:
                earn_step()
            except Exception as e:
                print(f'tradetools: earnings: {e!r}', flush=True)
        if n % 240 == 0:
            try:
                archive_step()
            except Exception as e:
                print(f'tradetools: archive: {e!r}', flush=True)
        n += 1
        time.sleep(15)


def start():
    if _st['started']:
        return
    _st['started'] = True
    threading.Thread(target=_loop, name='tradetools', daemon=True).start()


def snapshot():
    out = {'at': _now(), 'settings': settings(), 'plans': [dict(p, text=plan_text(p)) for p in plans()],
           'earn': _st['earn'], 'archive': _st['arch']}
    try:
        fills, trips, _ = _journal()
        out['cooldown'] = cooldown(trips)
        out['wash'] = wash(fills, trips)
    except Exception as e:
        out['error'] = str(e)[:200]
    try:
        out['exposure'] = exposure()
    except Exception as e:
        out['exposure'] = {'rows': [], 'warn': [], 'text': 'Could not read positions: ' + str(e)[:100]}
    with _lock:
        out['pending'] = _state().get('pending') or []
    return out


def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return str(v or '')


def get(sub, query):
    if sub == 'fills':
        sym = ''.join(ch for ch in _q(query, 'sym').upper() if ch.isalnum() or ch in '.=^-')[:12]
        return 200, {'sym': sym, 'fills': fills_of(sym)}
    if sub == 'templates':
        return 200, {'plans': [dict(p, text=plan_text(p)) for p in plans()]}
    if sub == 'history':
        sym = ''.join(ch for ch in _q(query, 'sym').upper() if ch.isalnum() or ch in '.=^-')[:12]
        try:
            days = max(1, min(3650, int(_q(query, 'days') or 60)))
        except ValueError:
            days = 60
        if not sym:
            return 400, {'error': 'Pick a symbol.'}
        return 200, {'sym': sym, 'rows': history(sym, days), 'archive': _st['arch']}
    if sub == '':
        return 200, snapshot()
    return 404, {'error': 'not found'}


def post(sub, data):
    if sub == 'settings':
        cur = settings()
        for k, v in (data or {}).items():
            if k not in DEFAULTS:
                continue
            if isinstance(DEFAULTS[k], bool):
                cur[k] = bool(v)
            else:
                try:
                    n = float(v)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(n) and n >= 0:
                    cur[k] = int(n) if isinstance(DEFAULTS[k], int) else n
        cur['cool_losses'] = max(1, min(10, int(cur['cool_losses'])))
        cur['cool_min'] = max(5, min(240, int(cur['cool_min'])))
        _save(settings=cur)
        return 200, snapshot()
    if sub == 'template':
        try:
            p = _clean_plan(data or {})
        except ValueError as e:
            return 400, {'error': str(e)}
        ps = [x for x in plans() if x['name'] != p['name']] + [p]
        _save(plans=ps[-12:])
        return 200, snapshot()
    if sub == 'template/off':
        name = str((data or {}).get('name') or '')
        _save(plans=[x for x in plans() if x['name'] != name])
        return 200, snapshot()
    if sub == 'plan':
        return apply_plan(data or {})
    if sub == 'pending/off':
        with _lock:
            st = _state()
            i = int((data or {}).get('i', -1)) if str((data or {}).get('i', '')).lstrip('-').isdigit() else -1
            if 0 <= i < len(st.get('pending') or []):
                st['pending'].pop(i)
                watch._write(STATE, st)
        return 200, snapshot()
    return 404, {'error': 'not found'}
