"""Alex Hale's Board -- why the scanner's movers are moving (jb-scanwhy, deep search ideas 50 and 52).

Board math only, from the scanner's own 5-minute windows (mover-scanner/logs/movers.jsonl) and the board's price
record (why.py). No outside commentary. For each symbol on the scanner it works out short tags:
  MARKET-LED / SECTOR-LED / OWN MOVE   how much of the move since the 4 pm close the futures (times how much this
                                       symbol usually moves with them) or its sector/index explain
  LAGS INDEX / AHEAD OF INDEX          a 2x/3x fund (or a stock) moving less or more than its index says it should
  HEAVY VOL / THIN                     the last 15 minutes' volume vs the same session's normal (scanner's own count)
  BREAKOUT / BREAKDOWN                 the price went past this session's high or low so far
  STRETCHED                            far from this session's average price paid (VWAP) for this symbol
  TRENDING UP / DOWN                   a steady one-way hour (little back-and-forth)
plus a one-line why, and one summary line for the whole list (idea 52).

GET /api/plus/scanwhy -> {at, mode, session, tags: {SYM: {tags: [...], move, line}}, summary}
"""
import json
import os
import threading
import time

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, '..', 'mover-scanner', 'logs', 'movers.jsonl')
TAIL = 3 * 1024 * 1024          # read only the end of the log (it can be 50 MB)
MIN = 60 * 1000
_lock = threading.Lock()
_c = {'at': 0, 'out': None}

SESS_WORD = {'overnight': 'tonight', 'post': 'this evening', 'pre': 'this morning', 'regular': 'today', 'closed': 'right now'}
MKT_NAME = {'NQ=F': 'Nasdaq futures', 'ES=F': 'S&P futures'}


def _now():
    return int(time.time() * 1000)


def _read_tail(now):
    """-> {sym: [windows oldest first]} for the newest scanner mode, last 30 hours."""
    try:
        size = os.path.getsize(LOG)
        with open(LOG, 'rb') as f:
            f.seek(max(0, size - TAIL))
            raw = f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return '', {}
    if size > TAIL:
        raw = raw[1:]                       # the first line is probably cut in half
    recs = []
    for ln in raw:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get('symbol') and r.get('t_end', 0) >= now - 30 * 60 * MIN and r.get('close'):
            recs.append(r)
    if not recs:
        return '', {}
    mode = recs[-1].get('mode') or ''
    by = {}
    for r in recs:
        if (r.get('mode') or '') == mode:
            by.setdefault(r['symbol'], []).append(r)
    for v in by.values():
        v.sort(key=lambda r: r['t_start'])
    return mode, by


def _session_run(ws):
    """The windows of the latest session, back to the first gap of over an hour or a session change."""
    out = [ws[-1]]
    for w in reversed(ws[:-1]):
        if w.get('session') != out[-1].get('session') or out[-1]['t_start'] - w['t_end'] > 60 * MIN:
            break
        out.append(w)
    return list(reversed(out))


def _pct(a, b):
    return None if a is None or not b else (a - b) / b * 100


def _move_since_close(sym, ws, now):
    lc = watch.last_close_ms(now)
    before = [w for w in ws if w['t_end'] <= lc + MIN]
    if before and lc - before[-1]['t_end'] < 6 * 60 * MIN:
        return _pct(ws[-1]['close'], before[-1]['close'])
    try:
        import why
        return why.since_close(sym, now)
    except Exception:
        return None


def _sector_of(sym):
    try:
        import signals
        sec = ((watch._read(signals.STATE, {}).get('sectors') or {}).get(sym) or {}).get('sector') or ''
        return sec, signals.SECTOR_ETF.get(sec)
    except Exception:
        return '', None


def analyse(sym, ws, now, mm, mkt, by):
    """Tags and a why line for one symbol from its scanner windows."""
    import why
    out = {'tags': [], 'move': None, 'line': ''}
    run = _session_run(ws)
    last = ws[-1]
    if now - last['t_end'] > 45 * MIN:
        return out                          # nothing fresh (the scanner has not seen it lately)
    move = _move_since_close(sym, ws, now)
    out['move'] = None if move is None else round(move, 2)
    bits = []

    def other_move(s):
        o = by.get(s)
        if o and now - o[-1]['t_end'] < 45 * MIN:
            m = _move_since_close(s, o, now)
            if m is not None:
                return m
        return why.since_close(s, now)

    # who explains the move: market, sector/index, or itself
    if move is not None and abs(move) >= 0.3 and mm is not None:
        f = why.fund_of(sym)
        st = why.stats(sym)
        sec_name, sec_part, idx_move, lev = '', 0.0, None, None
        if f:
            name, base, lev = f
            beta = st.get('beta') if st.get('beta') is not None else lev * ((why.stats(base).get('beta')) or 1.0)
            mpart = beta * mm
            bm = other_move(base)
            if bm is not None:
                idx_move = bm
                sec_part = lev * bm - mpart
                sec_name = why.SHORT.get(name, name) if hasattr(why, 'SHORT') else name
        else:
            beta = st.get('beta') if st.get('beta') is not None else 1.0
            mpart = beta * mm
            if sym in ('QQQ', 'SPY', 'DIA', 'IWM'):
                mpart = move
            else:
                sec_name, etf = _sector_of(sym)
                if etf and etf != sym:
                    sm = other_move(etf)
                    if sm is not None:
                        sec_part = sm - mpart
                        idx_move = sm
        own = move - mpart - sec_part
        parts = sorted((('MARKET-LED', mpart), ('SECTOR-LED', sec_part), ('OWN MOVE', own)), key=lambda x: -abs(x[1]))
        top, val = parts[0]
        if val * move > 0 and abs(val) >= 0.5 * abs(move):
            out['tags'].append(top)
            if top == 'MARKET-LED':
                bits.append(f"moving with {MKT_NAME.get(mkt, mkt)} ({mm:+.1f}%)")
            elif top == 'SECTOR-LED':
                bits.append(f"moving with its {'index' if f else 'sector'}{' (' + sec_name.lower() + ')' if sec_name else ''}")
            else:
                bits.append('a move of its own (not the market or its sector)')
        else:
            bits.append('a mix of market, sector and its own move')
        # 2x/3x funds: keeping up with the index?  stocks: keeping up with the market?
        if f and idx_move is not None and abs(idx_move) >= 0.2:
            ratio = move / idx_move
            if abs(lev) * 0.75 > ratio * (1 if lev > 0 else -1) >= 0:
                out['tags'].append('LAGS INDEX')
                bits.append(f"moving {abs(ratio):.1f}x its index, less than its {abs(lev)}x")
            elif ratio * (1 if lev > 0 else -1) > abs(lev) * 1.25:
                out['tags'].append('AHEAD OF INDEX')
                bits.append(f"moving {abs(ratio):.1f}x its index, more than its {abs(lev)}x")
        elif not f and abs(mm) >= 0.3 and abs(mpart) >= 0.2 and sym not in ('QQQ', 'SPY', 'DIA', 'IWM'):
            ratio = move / mpart
            if 0 <= ratio < 0.4:
                out['tags'].append('LAGS INDEX')
            elif ratio > 2.5 and top != 'OWN MOVE':
                out['tags'].append('AHEAD OF INDEX')
    # volume: the last three windows vs this session's normal
    vv = [w.get('vol_vs_session') for w in ws[-3:] if w.get('vol_vs_session') is not None and now - w['t_end'] < 30 * MIN]
    if vv:
        x = sum(vv) / len(vv)
        if x >= 2:
            out['tags'].append('HEAVY VOL')
            bits.append(f"on heavy volume ({x:.1f}x the session's normal)")
        elif x <= 0.5:
            out['tags'].append('THIN')
            bits.append('on thin volume')
    # breakout / breakdown vs this session's range so far (or a fired breakout check)
    prior = run[:-1]
    fired = [r.get('reason') for w in ws[-3:] for r in (w.get('fired') or []) if now - w['t_end'] < 20 * MIN]
    if len(prior) >= 4:
        hi, lo = max(w['high'] for w in prior), min(w['low'] for w in prior)
        if last['close'] > hi:
            out['tags'].append('BREAKOUT')
            bits.append(f"above this session's high ({hi:.2f})")
        elif last['close'] < lo:
            out['tags'].append('BREAKDOWN')
            bits.append(f"below this session's low ({lo:.2f})")
    if 'breakout_volume' in fired and not ({'BREAKOUT', 'BREAKDOWN'} & set(out['tags'])):
        up = [r.get('up') for w in ws[-3:] for r in (w.get('fired') or []) if r.get('reason') == 'breakout_volume']
        out['tags'].append('BREAKOUT' if up and up[-1] else 'BREAKDOWN')
    # stretched vs this session's VWAP (judged against this symbol's own typical window size)
    vol = sum(w['volume'] for w in run if w.get('volume'))
    if len(run) >= 4 and vol > 0:
        vw = sum((w['high'] + w['low'] + w['close']) / 3 * w['volume'] for w in run if w.get('volume')) / vol
        rng = sum((w['high'] - w['low']) / w['close'] * 100 for w in run) / len(run)
        d = _pct(last['close'], vw)
        if d is not None and rng > 0 and abs(d) >= max(0.8, 4 * rng):
            out['tags'].append('STRETCHED')
            bits.append(f"stretched {abs(d):.1f}% {'above' if d > 0 else 'below'} the session's VWAP")
    # trending: a steady one-way hour (efficiency: net move / the sum of every step)
    hr = [w for w in ws if w['t_end'] > now - 65 * MIN]
    if len(hr) >= 8:
        steps = sum(abs(b['close'] - a['close']) for a, b in zip(hr, hr[1:]))
        net = hr[-1]['close'] - hr[0]['open']
        if steps > 0 and abs(net) / steps >= 0.6 and abs(_pct(hr[-1]['close'], hr[0]['open']) or 0) >= 0.5:
            out['tags'].append('TRENDING UP' if net > 0 else 'TRENDING DOWN')
            bits.append('a steady trend for the last hour')
    if move is not None and abs(move) >= 0.3:
        out['line'] = f"{move:+.1f}% since the close: " + ', '.join(bits[:4]) if bits else ''
    elif bits:
        out['line'] = ', '.join(bits[:3]).capitalize()
    return out


def summary(tags, sess, mkt):
    """One line for the whole list (idea 52): what most of the movers have in common."""
    thr = 1
    movers = {s: t for s, t in tags.items() if t['move'] is not None and abs(t['move']) >= 1.0}
    if len(movers) < 3:
        thr = 0.5
        movers = {s: t for s, t in tags.items() if t['move'] is not None and abs(t['move']) >= 0.5}
    word = SESS_WORD.get(sess, 'right now')
    n = len(movers)
    if n < 3:
        if not n:
            return f"Quiet {word}: nothing on the scanner has moved 0.5% or more since the close."
        kind = {'MARKET-LED': 'with the market', 'SECTOR-LED': 'with its sector', 'OWN MOVE': 'its own move'}
        each = [f"{s} ({t['move']:+.1f}%" + ''.join(', ' + kind[g] for g in t['tags'] if g in kind) + ')' for s, t in movers.items()]
        return f"Quiet {word}: only {' and '.join(each)} {'has' if n == 1 else 'have'} moved {thr}% or more since the close."

    cnt = {}
    for t in movers.values():
        for g in t['tags']:
            cnt[g] = cnt.get(g, 0) + 1
    ups = sum(1 for t in movers.values() if t['move'] > 0)
    dirn = 'up' if ups >= 0.7 * n else 'down' if ups <= 0.3 * n else ''
    # what kind of symbols (sector, or index for 2x/3x funds)
    groups = {}
    try:
        import why
        for s in movers:
            f = why.fund_of(s)
            g = f[0] if f else _sector_of(s)[0]
            if g:
                groups[g] = groups.get(g, 0) + 1
    except Exception:
        pass
    g, gn = max(groups.items(), key=lambda x: x[1]) if groups else ('', 0)
    who = f"{g.lower()} names" if g and gn >= max(2, 0.4 * n) else 'movers'
    lead, ln = max(((k, cnt.get(k, 0)) for k in ('MARKET-LED', 'SECTOR-LED', 'OWN MOVE')), key=lambda x: x[1])
    how = {'MARKET-LED': f"moving with {MKT_NAME.get(mkt, 'the futures')}", 'SECTOR-LED': 'moving with their sector or index',
           'OWN MOVE': 'moving on their own (not the market or their sector)'}[lead]
    q = 'Most' if ln >= 0.5 * n else 'Some' if ln else ''
    s = f"{q} {who} {word} are {how}" if q else f"The {who} {word} are a mix of market, sector and own moves"
    if dirn:
        s += f", mostly {dirn}"
    heavy, thin = cnt.get('HEAVY VOL', 0), cnt.get('THIN', 0)
    if thin >= 0.5 * n:
        s += ', on light volume'
    elif heavy >= 0.5 * n:
        s += ', on heavy volume'
    br = cnt.get('BREAKOUT', 0) + cnt.get('BREAKDOWN', 0)
    if br >= 2:
        s += f"; {br} are past this session's high or low"
    return s + f". ({n} symbols have moved {thr}% or more since the close.)"


def build(now=None):
    now = now or _now()
    with _lock:
        if _c['out'] and now - _c['at'] < 60 * 1000:
            return _c['out']
    import why
    mode, by = _read_tail(now)
    mkt = 'NQ=F' if why.hist('NQ=F') else 'ES=F'
    mm = why.since_close(mkt, now)
    tags = {}
    for sym, ws in by.items():
        try:
            tags[sym] = analyse(sym, ws, now, mm, mkt, by)
        except Exception as e:
            tags[sym] = {'tags': [], 'move': None, 'line': '', 'error': str(e)[:80]}
    sess = ''
    if by:
        sess = max((ws[-1] for ws in by.values()), key=lambda w: w['t_end']).get('session') or ''
    out = {'at': now, 'mode': mode, 'session': sess, 'tags': tags,
           'summary': summary(tags, sess, mkt) if by else 'The scanner has not logged any windows yet.'}
    with _lock:
        _c['at'], _c['out'] = now, out
    return out


def start():
    pass


def get(sub, query):
    if sub in ('', 'tags'):
        return 200, build()
    return 404, {'error': 'unknown'}


def post(sub, data):
    return 404, {'error': 'unknown'}
