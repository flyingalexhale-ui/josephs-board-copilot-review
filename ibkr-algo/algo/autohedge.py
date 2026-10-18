"""Hedging for the algo (jb-hedge; identical file in webull-algo and ibkr-algo).

Auto-Hedge (AutoHedge below): the algo tab's Auto-Hedge button. While it is on, every position the algo opens gets a
hedge -- an inverse fund bought, or (IBKR only) MNQ / MES micro futures sold -- sized from the settings box (hedge
ratio and instrument, saved by the board in <algo folder>/hedge_settings.json), and the hedge is closed when the
position closes. Default OFF.
  * paper: the hedge orders are simulated (filled at the hedge's last price, plus the usual paper costs)
  * live: needs its own switch on the algo tab, confirmed by typing HEDGE LIVE, and it only counts for the ./jb live
    run it was confirmed in (logs/LIVE_RUNNING): a new ./jb live always starts with live hedging off
  * every safety rule of the position applies to the hedge too: the Stop button and the daily loss limit (KILL file)
    close it and open no new one; while SELL ALL runs (KILL by sellall) no hedge order is sent at all (SELL ALL closes
    the hedge itself, and the algo then books it as closed); after a rejected order no new hedge for 15 minutes and 3
    rejections in a row stop live trading; the speed limits and the duplicate check count hedge orders; the buying
    power check and risk.max_order_notional_usd apply to hedge orders; safe mode and an order in doubt stop them.
  * a hedge is never left open after the position is closed: it is closed even when Auto-Hedge has been turned off
    in the meantime, and it never sells more than the broker holds (the board never opens a short in a fund)
  * a hedge left over by an earlier run (jb-hedge, Webull leftover): every live hedge order is written down before it
    is sent (st['orders'], client order id jbhedge...); at the next live start an order still open stops the start, a
    fill that came in while the algo was off is adopted as the algo's hedge, and hedge-fund shares the account holds
    beyond the record are reported and block a second hedge (never sold by the algo). Paper keeps its saved hedge.

Backtests: a strategy file may have a  hedge:  section -- a second fund (or an IBKR micro future) held next to each
position, opened with the entry and closed with the exit:

    hedge:
      symbol: SQQQ          # the fund bought as the hedge (or MNQ / MES: micro futures, sold against a long position)
      ratio_pct: 100        # how much of the position's market exposure to hedge (shown on the algo tab)
      dollar_ratio: 1.0     # $ of the hedge per $1 of the position (the board's Hedge Ideas fill this in from the
                            # board's own price history; default ratio_pct / 100)

backtest_hedge() adds that leg to a finished backtest from the board's own minute record of the hedge (a micro future
uses the board's NQ=F / ES=F prices), at the same costs as the position (slippage, and the extra cost outside regular
hours), so the algo tab shows the position alone and with its hedge. Written for buy strategies.
"""
import bisect
import datetime as _dt
import json
import logging
import math
import os
import time
import uuid

import pandas as pd

from . import guards
from . import options as opt
from .runstate import atomic_write_json, session_at

log = logging.getLogger('autohedge')

# micro futures: root -> (the board symbol with its price, $ per point)
FUTURES = {'MNQ': ('NQ=F', 2.0), 'MES': ('ES=F', 5.0)}
FUT_COST_PCT = 0.02               # a futures fill costs about this % (tight spreads), instead of the stock costs
MAX_AGE = pd.Timedelta(minutes=15)


def record_symbol(sym):
    """The symbol whose recorded prices stand for the hedge (MNQ -> NQ=F)."""
    return FUTURES[sym][0] if sym in FUTURES else sym


def parse(raw, main_symbol, source='strategy'):
    """The strategy file's hedge: section -> {'symbol', 'ratio_pct', 'dollar_ratio', 'side'} or None.
    Raises ValueError with a plain reason."""
    if not raw:
        return None
    if not isinstance(raw, dict) or not str(raw.get('symbol') or '').strip():
        raise ValueError(f'{source}: hedge: needs a symbol (for example SQQQ, or MNQ for the micro Nasdaq-100 future)')
    sym = str(raw['symbol']).strip().upper()
    if sym == str(main_symbol).upper():
        raise ValueError(f'{source}: hedge: symbol must be another fund than the one traded')
    try:
        ratio = float(raw.get('ratio_pct', 100))
        dr = float(raw.get('dollar_ratio', ratio / 100.0))
    except (TypeError, ValueError):
        raise ValueError(f'{source}: hedge: ratio_pct and dollar_ratio must be numbers')
    if not 0 < ratio <= 150:
        raise ValueError(f'{source}: hedge: ratio_pct must be between 1 and 150')
    if not 0 < dr <= 10:
        raise ValueError(f'{source}: hedge: dollar_ratio must be between 0 and 10')
    side = str(raw.get('side') or ('sell' if sym in FUTURES else 'buy')).lower()
    if side not in ('buy', 'sell'):
        raise ValueError(f"{source}: hedge: side must be buy or sell")
    return {'symbol': sym, 'ratio_pct': ratio, 'dollar_ratio': dr, 'side': side}


def _price_at(idx, px, t):
    i = bisect.bisect_right(idx, t) - 1
    if i < 0 or t - idx[i] > MAX_AGE:
        return None
    return px[i]


def backtest_hedge(strategy, portfolio, hedge_bars, opts, slippage_pct=0.0):
    """The hedge leg of a finished backtest. -> dict for metrics['hedge']."""
    h = strategy.hedge
    out = {'symbol': h['symbol'], 'side': h['side'], 'ratio_pct': h['ratio_pct'], 'dollar_ratio': h['dollar_ratio'],
           'record': record_symbol(h['symbol'])}
    if hedge_bars is None or not len(hedge_bars):
        out['error'] = f"no recorded prices of {out['record']} on the board, so the hedge was left out"
        return out
    hb = hedge_bars['close'].astype('float64').sort_index()
    idx, px = list(hb.index), list(hb.values)
    ext = float(opts.get('extended_hours_cost_pct') or 0.0)
    fut = h['symbol'] in FUTURES

    def cost_pct(t):
        if fut:
            return FUT_COST_PCT
        s = session_at(pd.Timestamp(t).to_pydatetime())
        return slippage_pct + (ext if s != 'regular' else 0.0)

    sign0 = 1.0 if h['side'] == 'buy' else -1.0
    legs, open_leg, missing = [], None, 0
    for tr in portfolio.trades:
        t = pd.Timestamp(tr.time)
        if tr.reason == 'entry':
            p = _price_at(idx, px, t)
            if p is None:
                missing += 1
                open_leg = None
                continue
            sign = sign0 if tr.side == 'buy' else -sign0
            dollars = h['dollar_ratio'] * tr.shares * tr.price
            open_leg = {'t': t, 'p': p, 'dollars': dollars, 'sign': sign, 'shares': tr.shares, 'left': tr.shares,
                        'cost': dollars * cost_pct(t) / 100, 'pnl': 0.0, 'session': session_at(t.to_pydatetime())}
            continue
        if open_leg is None:
            continue
        p = _price_at(idx, px, t)
        part = min(tr.shares, open_leg['left']) / open_leg['shares']
        if p is None:                                   # no price at the exit: the leg is closed at its last price
            p = _price_at(idx, px, t + pd.Timedelta(hours=12)) or open_leg['p']
        d = open_leg['dollars'] * part
        open_leg['pnl'] += open_leg['sign'] * d * (p / open_leg['p'] - 1) - d * cost_pct(t) / 100
        open_leg['left'] -= min(tr.shares, open_leg['left'])
        if open_leg['left'] <= 0:
            open_leg['pnl'] -= open_leg['cost']
            open_leg['exit_t'], open_leg['exit_p'] = t, p
            legs.append(open_leg)
            open_leg = None
    start = portfolio.starting_cash
    pnl = sum(l['pnl'] for l in legs)
    # the combined equity: the position's equity curve plus the hedge's realized and open result at each point
    curve = []
    j, realized = 0, 0.0
    done = sorted(legs, key=lambda l: l['exit_t'])
    for t, e in portfolio.equity_curve:
        t = pd.Timestamp(t)
        while j < len(done) and done[j]['exit_t'] <= t:
            realized += done[j]['pnl']
            j += 1
        mtm = 0.0
        for l in legs:
            if l['t'] <= t < l['exit_t']:
                p = _price_at(idx, px, t)
                if p is not None:
                    mtm += l['sign'] * l['dollars'] * (p / l['p'] - 1)
        curve.append(e + realized + mtm)
    peak, mdd = None, 0.0
    for v in curve:
        peak = v if peak is None else max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v - peak) / peak * 100)
    trips = _main_trips(portfolio.trades)
    by_entry, wins = {}, 0
    for l in legs:
        main = next((m for m in trips if m['t'] == l['t']), None)
        tot = l['pnl'] + (main['pnl'] if main else 0.0)
        wins += tot > 0
        b = by_entry.setdefault(l['session'] or 'closed', {'n': 0, 'pnl': 0.0, 'with_main': 0.0})
        b['n'] += 1
        b['pnl'] = round(b['pnl'] + l['pnl'], 2)
        b['with_main'] = round(b['with_main'] + tot, 2)
    out.update(trips=len(legs), missing=missing, pnl=round(pnl, 2), pnl_pct=round(pnl / start * 100, 3) if start else None,
               combined_return_pct=round((curve[-1] / curve[0] - 1) * 100, 2) if curve and curve[0] else None,
               combined_max_drawdown_pct=round(mdd, 2),
               combined_win_rate_pct=round(wins / len(legs) * 100, 1) if legs else None,
               by_session=by_entry)
    if missing:
        out['note'] = f'{missing} entr{"y" if missing == 1 else "ies"} had no recorded {out["record"]} price within 15 minutes: no hedge on those'
    return out


def _main_trips(trades):
    """[{t: entry time, pnl}] of the position's own round trips (partial exits added up)."""
    out, cur = [], None
    for tr in trades:
        if tr.reason == 'entry':
            cur = {'t': pd.Timestamp(tr.time), 'price': tr.price, 'sign': 1 if tr.side == 'buy' else -1, 'pnl': 0.0}
            out.append(cur)
        elif cur is not None:
            cur['pnl'] += cur['sign'] * (tr.price - cur['price']) * tr.shares
    return out



# ======================================================================= Auto-Hedge in paper and live trading
SETTINGS_CHECK_S = 5
OPEN_RETRY_S = 60                 # an open that did not fill (or was skipped) is tried again after this
CLOSE_RETRY_S = 30                # a close that did not fill: tried again after this
BROKER_CHECK_S = 30               # live: how often a hedge closed outside the algo (SELL ALL, the broker app) is looked for
FUT_TICK = 0.25                   # MNQ / MES price step
FUT_MARGIN_PCT = 10.0             # buying power check for a futures hedge: about this % of what it controls (estimate)
RESIZE_PCT = 25.0                 # the hedge is resized when it is off its target by this much (and at least one unit)
HEDGE_TAG = 'jbhedge'             # client order ids of live hedge orders start with this (Webull: found again at start-up,
                                  # like IBKR's orderRef jb-hedge); 7 letters + 25 hex = Webull's 32 characters
FINAL_STATES = ('FILLED', 'CANCELLED', 'CANCELED', 'FAILED', 'REJECTED', 'EXPIRED')


def cme_paused(now=None):
    """True during CME's daily 5-6 pm ET pause, when MNQ / MES take no orders."""
    try:
        from zoneinfo import ZoneInfo
        t = _dt.datetime.fromtimestamp(now or time.time(), ZoneInfo('America/New_York'))
    except Exception:
        t = _dt.datetime.utcfromtimestamp(now or time.time()) - _dt.timedelta(hours=4)
    return t.hour == 17


class _As:
    """The strategy as seen by the order code, with the hedge's symbol (guards and logs name the hedge)."""
    def __init__(self, base, symbol):
        self._base, self.symbol = base, symbol

    def __getattr__(self, k):
        return getattr(self._base, k)


def settings_path(logs_dir):
    return os.path.join(os.path.dirname(os.path.abspath(logs_dir)), 'hedge_settings.json')


def read_settings(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


class AutoHedge:
    def __init__(self, core, logs_dir):
        self.core = core
        self.logs_dir = logs_dir
        self.path = settings_path(logs_dir)
        self.state_path = os.path.join(logs_dir, f'hedge_state_{core.mode}.json')
        self.cfg, self.cfg_at, self.cfg_stamp = {}, 0.0, None
        self.problem = None               # why the settings can't be used right now (shown on the algo tab)
        self.note = None                  # the last thing that happened / why nothing happened
        self.price, self.price_at = None, 0.0
        self.pending = None               # live: the hedge order in flight
        self.retry_at = 0.0
        self.close_retry_at = 0.0
        self.cooldown_until = 0.0
        self.checked_at = 0.0
        self.pushed = set()
        self.night_refused = False        # the broker refused a hedge order overnight: no more until the session ends
        self.place = None                 # live: fn(spec, filled, failed, unconfirmed, unfilled) -- set by the trader
        self.position = None              # live: fn() -> the broker's signed units of the hedge, or None
        self.st = {'symbol': None, 'units': 0, 'avg': None, 'opened_at': None, 'for_entry': None, 'main_shares': 0,
                   'mult': 1.0, 'realized': 0.0, 'day': None, 'day_pnl': 0.0, 'trips': []}
        try:
            with open(self.state_path) as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.st.update(d)
        except Exception:
            pass
        self.reload(force=True)
        self.outside_checked = 0.0
        if core.mode == 'paper' and self.units():
            # paper restart (jb-hedge, Webull leftover): the saved paper hedge is the algo's own -- adopted, never a second one
            self.note = (f"kept the paper hedge from before the restart: {abs(self.units())} {self.st.get('symbol')} @ "
                         f"{self.st.get('avg')} (no second hedge is opened)")
            log.warning('hedge: %s', self.note)

    # ------------------------------------------------------------ settings
    def reload(self, force=False):
        now = time.time()
        if not force and now - self.cfg_at < SETTINGS_CHECK_S:
            return
        self.cfg_at = now
        try:
            stamp = os.stat(self.path).st_mtime
        except OSError:
            stamp = None
        if not force and stamp == self.cfg_stamp:
            return
        self.cfg_stamp = stamp
        self.cfg = read_settings(self.path) if stamp else {}

    def symbol(self):
        return str(self.cfg.get('symbol') or '').upper() or None

    def feed_symbol(self):
        """The symbol whose prices the trader should stream: the open hedge's, else the chosen one while Auto-Hedge is on."""
        return (self.st.get('symbol') if self.units() else None) or (self.symbol() if self.cfg.get('on') else None)

    def position_symbol(self):
        """Whose broker position to read: the open hedge's symbol, else the chosen one."""
        return (self.st.get('symbol') if self.units() else None) or self.symbol()

    def is_future(self, sym=None):
        return (sym or self.symbol()) in FUTURES

    def active(self):
        """(may a NEW hedge be opened, why not). Closing an open hedge never needs this."""
        c, core = self.cfg, self.core
        if not c.get('on'):
            return False, 'Auto-Hedge is off'
        sym = self.symbol()
        if not sym:
            return False, 'no hedge chosen'
        if str(c.get('for_symbol') or '').upper() != core.strategy.symbol.upper():
            return False, (f"the Auto-Hedge settings were made for {c.get('for_symbol')}; this algo trades "
                           f'{core.strategy.symbol}: save them again on the algo tab')
        if sym == core.strategy.symbol.upper():
            return False, 'the hedge must be another fund than the one traded'
        if self.is_future(sym) and core.broker_name != 'ibkr':
            return False, 'futures hedges are IBKR only'
        try:
            dr = float(c.get('dollar_ratio'))
        except (TypeError, ValueError):
            return False, 'no hedge size in the settings'
        if not 0 < dr <= 10:
            return False, 'the hedge size in the settings is out of range'
        if core.strategy.entry_side != 'buy' and not self.is_future(sym):
            return False, 'a fund hedge is only made for buy strategies (use MNQ / MES at IBKR)'
        out = self.st.get('outside') or {}
        if core.mode == 'live' and out.get('units') and str(out.get('symbol') or '') == sym and not self.units():
            return False, (f"the account already holds {out['units']:g} {sym} that the algo's hedge record does not have "
                           '(found at start-up): no second hedge is opened. Sell them in the broker app (or pick another '
                           'hedge fund) to hedge again; the algo never sells them itself')
        if core.mode == 'live':
            if not c.get('live_on'):
                return False, 'live hedging is off (its own switch on the algo tab)'
            if not self._live_lock_ok(c.get('live_lock')):
                return False, ('live hedging was turned on for an earlier ./jb live run: turn it on again on the algo '
                               'tab while this one runs')
        return True, None

    def _live_lock_ok(self, lock):
        """The live switch counts only for the ./jb live run it was confirmed in (its logs/LIVE_RUNNING file)."""
        p = os.path.join(self.logs_dir, 'LIVE_RUNNING')
        try:
            pid = open(p).read().strip()
            mt = os.stat(p).st_mtime
        except OSError:
            return False
        return isinstance(lock, dict) and str(lock.get('pid')) == pid and abs(float(lock.get('mtime') or 0) - mt) < 2

    # ------------------------------------------------------------ prices
    def on_price(self, price, ts_ms=None):
        if price is not None and price > 0:
            self.price, self.price_at = float(price), time.time()

    def fresh_price(self):
        return self.price if self.price and time.time() - self.price_at < 600 else None

    # ------------------------------------------------------------ state
    def save(self):
        try:
            atomic_write_json(self.state_path, self.st)
        except Exception as e:
            log.warning('could not save the hedge state: %r', e)

    def units(self):
        return int(self.st.get('units') or 0)

    def _today(self):
        try:
            from zoneinfo import ZoneInfo
            d = _dt.datetime.now(ZoneInfo('America/New_York'))
        except Exception:
            d = _dt.datetime.utcnow() - _dt.timedelta(hours=4)
        return (d + _dt.timedelta(hours=4)).date().isoformat()          # the board's trading day starts at 8 pm

    def book(self, delta, price, reason):
        """A hedge fill of `delta` units (signed) at `price`: the open size and average change, a reduction books P&L."""
        st = self.st
        u0, avg = int(st.get('units') or 0), st.get('avg')
        mult = float(st.get('mult') or 1.0)
        u1 = u0 + delta
        pnl = 0.0
        if u0 == 0 or (u0 > 0) == (delta > 0):                       # opening / adding
            st['avg'] = price if u0 == 0 else (avg * abs(u0) + price * abs(delta)) / abs(u1)
            if u0 == 0:
                st['opened_at'] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        else:                                                         # reducing / closing
            n = min(abs(delta), abs(u0))
            pnl = (price - avg) * n * mult * (1 if u0 > 0 else -1)
            if u1 == 0:
                st['avg'] = None
        st['units'] = u1
        if pnl:
            day = self._today()
            if st.get('day') != day:
                st['day'], st['day_pnl'] = day, 0.0
            st['day_pnl'] = round(st['day_pnl'] + pnl, 2)
            st['realized'] = round(float(st.get('realized') or 0) + pnl, 2)
        st['trips'] = (st.get('trips') or [])[-49:] + [{'time': _dt.datetime.now(_dt.timezone.utc).isoformat(),
                                                         'symbol': st.get('symbol'), 'units': delta, 'price': price,
                                                         'pnl': round(pnl, 2), 'reason': reason}]
        if u1 == 0:
            st['for_entry'] = None
            st['main_shares'] = 0
        self.save()
        return pnl

    # ------------------------------------------------------------ sizing
    def target(self):
        """Signed hedge units wanted for the position now (0 = none), and a note when it is 0 for a reason."""
        core, pos = self.core, self.core.portfolio.position
        if pos is None:
            return 0, None
        st = self.st
        same_ratio = abs(float(st.get('dollar_ratio') or 0) - float(self.cfg.get('dollar_ratio') or 0)) <= \
            abs(float(self.cfg.get('dollar_ratio') or 0)) * RESIZE_PCT / 100
        if self.units() and st.get('for_entry') == pos.entry_time and st.get('main_shares') and (same_ratio or not self.cfg.get('on')):
            # follow the position's share count (a part exit makes the hedge smaller), not every price move
            want = round(abs(self.units()) * pos.shares / st['main_shares'])
            return (want if self.units() > 0 else -want), None
        sym, px = self.symbol(), self.fresh_price()
        main_px = core.last_price
        if not px or not main_px:
            return 0, f'waiting for a {sym} price' if not px else 'waiting for a price'
        dollars = float(self.cfg.get('dollar_ratio') or 0) * pos.shares * main_px
        side = str(self.cfg.get('side') or ('sell' if self.is_future(sym) else 'buy'))
        sign = 1 if side == 'buy' else -1
        if pos.side == 'short':
            sign = -sign
        if self.is_future(sym):
            mult = FUTURES[sym][1]
            n = int(round(dollars / (px * mult)))
            if n == 0:
                return 0, f'the position is smaller than one {sym} (one controls ${px * mult:,.0f})'
        else:
            n = int(round(dollars / px))
        cap = core.max_order_notional_usd or core.strategy.max_order_notional_usd
        if core.mode == 'live' and cap:
            unit_value = px * (FUTURES[sym][1] if self.is_future(sym) else 1.0)
            if unit_value > cap:
                return 0, f'one {sym} (${unit_value:,.0f}) is worth more than risk.max_order_notional_usd (${float(cap):,.0f})'
            n = min(n, int(float(cap) // unit_value))
        return sign * n, None

    # ------------------------------------------------------------ the loop (every 2 s from TraderCore.on_timer)
    def tick(self):
        self.reload()
        core = self.core
        if core.mode == 'live' and (self.st.get('outside') or {}).get('units'):
            self._check_outside()
        pos = core.portfolio.position
        held = self.units()
        ok, why = self.active()
        self.problem = None if ok or not self.cfg.get('on') else why
        if held and core.killed and core._kill_by() == 'sellall':
            # SELL ALL closes the hedge at the broker: send nothing, book it as closed once the broker shows it gone
            if core.mode == 'paper':
                self._paper_close('SELL ALL')
            else:
                self._check_gone('SELL ALL')
            return
        reason = None
        if held:
            if core.killed:
                reason = 'the Stop button or the daily loss limit'
            elif pos is None:
                reason = 'the position is closed'
            elif self.st.get('for_entry') and pos.entry_time != self.st['for_entry']:
                reason = 'a new position'
            elif not self.cfg.get('on'):
                reason = 'Auto-Hedge was turned off'
            elif self.symbol() != self.st.get('symbol'):
                reason = 'the hedge settings changed'
        if reason:
            self._close(reason)
            return
        if core.mode == 'live' and held:
            self._check_gone('closed outside the algo')
            held = self.units()
        if pos is None or core.killed:
            return
        if not held:
            if not ok:
                self.note = why if self.cfg.get('on') else None
                return
            want, wnote = self.target()
            if want == 0:
                self.note = wnote or self.note
                return
            self._send(want, closing=False, reason='hedge for the position')
            return
        want, _ = self.target()                            # follows the position: a part exit makes the hedge smaller
        diff = want - held
        if diff == 0 or abs(diff) < max(1, abs(want) * RESIZE_PCT / 100):
            return
        if abs(want) < abs(held) or (want > 0) != (held > 0):
            self._send(diff, closing=True, reason='position smaller')
        elif ok:
            self._send(diff, closing=False, reason='hedge resized')

    # ------------------------------------------------------------ orders
    def _session_ok(self, sym, closing):
        core = self.core
        sess, ok = core.session_now()
        if not ok:
            return None, f"{opt.LABELS.get(sess, 'market closed') if sess else 'market closed'} is not turned on for {core.mode} trading"
        if self.is_future(sym) and cme_paused():
            return None, 'CME futures pause 5-6 pm ET'
        return sess, None

    def _close(self, why):
        held = self.units()
        if not held:
            return
        if self.core.mode == 'paper':
            self._paper_close(why)
            return
        if time.time() < self.close_retry_at:
            return
        # never sell blind: the broker must still hold the hedge (SELL ALL or you may have closed it)
        b = self._broker_units()
        if b is None:
            self.note = f'closing the hedge ({why}): could not read the broker position, tries again in 30 s'
            self.close_retry_at = time.time() + CLOSE_RETRY_S
            return
        if b == 0 or (b > 0) != (held > 0):
            self._mark_flat(f'the broker shows no {self.st.get("symbol")} hedge')
            return
        n = min(abs(held), abs(int(round(b))))
        self._send(-n if held > 0 else n, closing=True, reason=f'close the hedge: {why}')

    def _broker_units(self):
        if self.position is None:
            return None
        try:
            v = self.position()
            return None if v is None else float(v)
        except Exception as e:
            log.warning('hedge: could not read the broker position: %r', e)
            return None

    def _check_gone(self, why):
        """Live: book the hedge as closed when the broker holds less of it (SELL ALL, or closed in the broker app)."""
        if self.pending or time.time() - self.checked_at < BROKER_CHECK_S:
            return
        self.checked_at = time.time()
        held = self.units()
        b = self._broker_units()
        if b is None or not held:
            return
        if b == 0 or (b > 0) != (held > 0):
            self._mark_flat(f'{why}: the broker shows no {self.st.get("symbol")} hedge')
        elif abs(b) < abs(held):
            px = self.fresh_price() or self.st.get('avg')
            self.book(int(round(b)) - held if held > 0 else -(abs(int(round(b))) - abs(held)), px, why)
            self.note = f'{why}: the broker holds {abs(b):g} of the hedge, booked the rest as sold'

    def _mark_flat(self, why):
        held = self.units()
        px = self.fresh_price() or self.st.get('avg') or 0.0
        if held:
            self.book(-held, px, why + ' (booked at the last price)')
        self.note = why + '; booked at the last price'
        log.warning('hedge: %s', self.note)
        self.core.write_status()

    def _paper_close(self, why):
        held = self.units()
        px = self.fresh_price() or self.st.get('avg')
        if not held or not px:
            return
        side = 'sell' if held > 0 else 'buy'
        fill = self.core.sim_price(side, px, session_at() or 'overnight')
        pnl = self.book(-held, fill, why)
        self.note = f'paper: closed the hedge ({why}), {side} {abs(held)} {self.st.get("symbol")} @ {fill:.4g}, P&L ${pnl:,.2f}'
        log.warning('hedge %s', self.note)
        self.core.write_status()

    def _send(self, delta, closing, reason):
        core, sym = self.core, (self.st.get('symbol') if closing and self.units() else self.symbol())
        side, n = ('buy' if delta > 0 else 'sell'), abs(int(delta))
        if n <= 0:
            return
        now = time.time()
        if self.pending or (core.mode == 'live' and core.pending):
            return                                         # one order at a time (the position's own first)
        if core._kill_by() == 'sellall':
            self.note = 'SELL ALL is running: no hedge order is sent'
            return
        if closing and now < self.close_retry_at:
            return
        if not closing:
            if now < self.retry_at:
                return
            if core.killed:
                return
            if core.halted:
                self.note = 'no new hedge: ' + core.halted[:160]
                return
            if now < core.cooldown_until or now < self.cooldown_until:
                self.note = 'no new hedge: waiting 15 min after a rejected order'
                return
            if core._doubt():
                self.note = 'no new hedge: IBKR and Webull disagree on the price'
                return
        sess, why = self._session_ok(sym, closing)
        if why:
            self.note = f'hedge waits: {why}'
            return
        if sess != 'overnight':
            self.night_refused = False
        elif self.night_refused:
            # (hedge-2026-10-12) the broker took no hedge order overnight -- e.g. this fund does not trade in Webull's
            # night session: wait for 4 am instead of adding rejections (3 in a row would stop live trading)
            self.note = f'hedge waits: the broker refused a {sym} hedge order overnight -- tried again from 4 am'
            return
        px = self.fresh_price()
        if not px:
            self.note = f'hedge waits: no {sym} price yet'
            return
        fut = self.is_future(sym)
        mult = FUTURES[sym][1] if fut else 1.0
        spec = core.order_spec(side, n, px, sess, closing=closing)
        spec.update(symbol=sym, hedge=True, dup_key='hedge-' + side, mult=mult)
        if fut and spec.get('limit'):
            q = math.ceil if side == 'buy' else math.floor
            spec['limit'] = round(q(spec['limit'] / FUT_TICK) * FUT_TICK, 2)
        if core.mode == 'paper':
            why = (None if closing else core.halted) or core._guardian_block(spec)
            if why:
                self.note = 'paper hedge blocked: ' + why[:160]
                if not closing:
                    self.retry_at = now + OPEN_RETRY_S
                return
            if not self.st.get('symbol') or not self.units():
                self.st['symbol'], self.st['mult'] = sym, mult
            fill = core.sim_price(side, px, sess)
            pnl = self.book(n if side == 'buy' else -n, fill, reason)
            if self.units():
                self._mark_entry()                         # the hedge now matches the position's share count
            self.note = f"paper: {side} {n} {sym} @ {fill:.4g} ({reason})" + (f', P&L ${pnl:,.2f}' if pnl else '')
            log.warning('hedge %s', self.note)
            core.write_status()
            return
        self._send_live(spec, sym, side, n, px, closing, reason, now)

    def _mark_entry(self):
        """Remember which position the hedge belongs to and its share count now (the hedge follows it)."""
        pos = self.core.portfolio.position
        if pos is not None:
            self.st['for_entry'], self.st['main_shares'] = pos.entry_time, pos.shares
            self.st['dollar_ratio'] = float(self.cfg.get('dollar_ratio') or 0)

    def _send_live(self, spec, sym, side, n, px, closing, reason, now):
        core = self.core
        cap = core.max_order_notional_usd or core.strategy.max_order_notional_usd
        if cap and not closing:                            # never above the cap (like the position's own entry)
            n = min(n, int(float(cap) // (core.spec_price(spec) * spec['mult'])))
            if n <= 0:
                self.note = f'no hedge: one {sym} is worth more than risk.max_order_notional_usd (${float(cap):,.0f})'
                self.retry_at = now + OPEN_RETRY_S
                return
            spec['shares'] = n
        if not closing:
            bp = core.buying_power if now - core.buying_power_at < 300 else None
            cost = n * px * (spec['mult'] * FUT_MARGIN_PCT / 100 if self.is_future(sym) else 1.0)
            if bp is not None and cost > bp:
                self.note = f'no hedge: it needs ${cost:,.0f} of buying power, the broker shows ${bp:,.0f}'
                self.retry_at = now + OPEN_RETRY_S
                return
        if self.place is None:
            self.note = 'no broker connected for the hedge'
            return
        try:
            guards.check_live_trading_enabled(core.strategy)
            guards.check_order(core.strategy, side, sym, n, max(spec['ref_price'], spec.get('limit') or 0.0) * spec['mult'],
                               max_order_quantity=core.max_order_quantity,
                               max_order_notional_usd=core.max_order_notional_usd, closing=closing)
        except guards.GuardRejected as e:
            self.note = 'hedge blocked by the guards: ' + str(e)[:160]
            self.retry_at = now + OPEN_RETRY_S
            return
        why = core._guardian_block(spec)                   # safe mode, speed limit, duplicate (counts as sent)
        if why:
            self.note = 'hedge blocked: ' + why[:160]
            if not closing:
                self.retry_at = now + OPEN_RETRY_S
            return
        if not self.st.get('symbol') or not self.units():
            self.st['symbol'], self.st['mult'] = sym, spec['mult']
        self.pending = {'side': side, 'units': n, 'symbol': sym, 'closing': closing, 'reason': reason, 'sent_at': now}
        # written down BEFORE it is sent (jb-hedge, Webull leftover): a run that stops with this order open finds it again
        rec = self._order_add(spec, sym, side, n, px, reason)
        self.note = f"sent: {side} {n} {sym} ({reason})"
        log.warning('LIVE HEDGE ORDER: %s %s %s (%s, id %s)', side, n, sym, reason, rec['id'])
        core.write_status()
        sign = 1 if side == 'buy' else -1

        def filled(fill_price, filled_units=None, status='filled'):
            self.pending = None
            core.rejects = 0
            k = int(filled_units if filled_units is not None else n)
            if k > 0:
                self.book(sign * k, float(fill_price or spec.get('limit') or px), reason)
                if self.units():
                    self._mark_entry()
            self._order_done(rec, k)
            self.note = f"{status}: {side} {k} {sym} @ {fill_price}"
            core.save()

        def unfilled(detail):
            self.pending = None
            self._order_done(rec)
            if closing:
                self.close_retry_at = time.time() + CLOSE_RETRY_S
            else:
                self.retry_at = time.time() + OPEN_RETRY_S
            self.note = f'hedge not filled ({detail[:120]}); tried again shortly'
            core.save()

        def failed(detail):
            self.pending = None
            self._order_done(rec)
            core.rejects += 1
            self.cooldown_until = time.time() + 15 * 60
            if closing:
                self.close_retry_at = time.time() + 60
            if spec.get('session') == 'overnight':
                self.night_refused = True          # no more hedge orders this overnight session
            self.note = f'hedge order rejected: {str(detail)[:150]}'
            log.error('LIVE HEDGE ORDER REJECTED: %s', detail)
            if core.rejects >= 3:
                core.halted = (f'3 live orders in a row were rejected (last: the hedge, {str(detail)[:100]}). '
                               'Live trading stopped -- fix the cause, then start it again.')
            if 'rejected' not in self.pushed:
                self.pushed.add('rejected')
                core._push('Hedge order rejected', f'{core.broker_name.upper()} algo: the {side} of {n} {sym} '
                           f'({reason}) was rejected: {str(detail)[:120]}. ' +
                           ('The position is not hedged right now.' if not closing else
                            'It tries again from 4 am (refused overnight).' if spec.get('session') == 'overnight' else
                            'It tries again in a minute.'))
            core.save()

        def unconfirmed(detail):
            # keep self.pending: no more hedge orders until a person has checked
            core.halted = (f'a hedge order could not be confirmed ({str(detail)[:100]}). Live trading is PAUSED: check '
                           f'{sym} in the broker app, then stop this window (Ctrl+C) and start ./jb live again.')
            self.note = 'hedge order unconfirmed: ' + str(detail)[:150]
            log.error('LIVE TRADING PAUSED (hedge): %s', detail)
            core._push('Algo paused: hedge order unconfirmed', f'{core.broker_name.upper()} algo: {core.halted}', 'rotating_light')
            core.save()

        try:
            self.place(spec, filled, failed, unconfirmed, unfilled)
        except guards.GuardRejected as e:
            self.pending = None
            self._order_done(rec)
            self.note = 'hedge blocked: ' + str(e)[:160]
            self.retry_at = time.time() + OPEN_RETRY_S
        except Exception as e:
            failed(f'{type(e).__name__}: {e}')

    # ------------------------------------------------------------ the record of live hedge orders (jb-hedge, Webull leftover)
    def _order_add(self, spec, sym, side, n, px, reason):
        pos = self.core.portfolio.position
        rec = {'id': HEDGE_TAG + uuid.uuid4().hex[:25], 'symbol': sym, 'side': side, 'units': n, 'px': px,
               'reason': reason, 'sent_at': _dt.datetime.now(_dt.timezone.utc).isoformat(), 'booked': 0, 'done': False,
               'mult': spec.get('mult') or 1.0, 'for_entry': pos.entry_time if pos is not None else None,
               'main_shares': pos.shares if pos is not None else 0, 'dollar_ratio': float(self.cfg.get('dollar_ratio') or 0)}
        spec['client_order_id'] = rec['id']               # Webull sends it as the order's client order id
        self.st['orders'] = [o for o in (self.st.get('orders') or []) if isinstance(o, dict)][-19:] + [rec]
        self.save()
        return rec

    def _order_done(self, rec, booked=None):
        if booked is not None:
            rec['booked'] = int(booked)
        rec['done'] = True
        self.save()

    def open_orders(self):
        """Live hedge orders this algo sent that never got a final answer (the run stopped, or they were unconfirmed)."""
        return [o for o in (self.st.get('orders') or []) if isinstance(o, dict) and not o.get('done') and o.get('id')]

    def watch_symbols(self):
        """The funds a leftover hedge order could be for: the open hedge, the chosen one (Auto-Hedge on) and those of
        hedge orders without a final answer."""
        out = {o.get('symbol') for o in self.open_orders()}
        if self.units():
            out.add(self.st.get('symbol'))
        if self.cfg.get('on'):
            out.add(self.symbol())
        return {str(x).upper() for x in out if x}

    def _check_outside(self):
        """Live: are the hedge-fund shares found outside the record at start-up still there? Sold in the broker app:
        Auto-Hedge hedges new positions again."""
        now = time.time()
        if self.pending or now - self.outside_checked < BROKER_CHECK_S:
            return
        self.outside_checked = now
        out = self.st.get('outside') or {}
        if self.position is None or str(out.get('symbol') or '') != (self.position_symbol() or ''):
            return
        b = self._broker_units()
        if b is None:
            return
        extra = abs(int(round(b))) - abs(self.units())
        if extra <= 0:
            self.st['outside'] = None
            self.note = f"the {out.get('symbol')} held outside the algo is gone: Auto-Hedge hedges new positions again"
            log.warning('hedge: %s', self.note)
            self.save()
        elif extra != out.get('units'):
            self.st['outside'] = dict(out, units=extra)
            self.save()

    # ------------------------------------------------------------ live start-up
    _ASK = object()

    def startup(self, broker_units=_ASK, lookups=None):
        """Live start: compare the hedge record with the broker (jb-hedge).
          * lookups ({client order id: (status, filled, avg) or None}, Webull): hedge orders this algo sent without a
            final answer. A fill found there came in while the algo was off: it is adopted (booked as the algo's
            hedge, for the position it was sent for). None = the broker does not know the order: it was never placed.
          * less (or none) at the broker than the record: booked as closed outside the algo
          * more at the broker than the record: reported (st['outside']) and no second hedge is opened while they are
            there; the algo never sells them (they may be yours)
        broker_units: the broker's signed units of the hedge; left out, it is read (after any adoption)."""
        adopted = []
        for o in self.open_orders():
            if lookups is None or o['id'] not in lookups:
                continue
            r = lookups[o['id']]
            if r is None:
                o['done'] = True                              # the broker never got it
                continue
            status, filled, avg = r
            extra = int(round(filled or 0)) - int(o.get('booked') or 0)
            sym = o.get('symbol')
            if extra > 0 and (not self.units() or self.st.get('symbol') == sym):
                if not self.units():
                    self.st['symbol'], self.st['mult'] = sym, float(o.get('mult') or 1.0)
                sign = 1 if o.get('side') == 'buy' else -1
                self.book(sign * extra, float(avg or o.get('px') or 0.0), 'filled while the algo was off (adopted)')
                o['booked'] = int(round(filled))
                if self.units():
                    self.st['for_entry'], self.st['main_shares'] = o.get('for_entry'), o.get('main_shares') or 0
                    self.st['dollar_ratio'] = o.get('dollar_ratio') or self.st.get('dollar_ratio')
                adopted.append(f"{o.get('side')} {extra} {sym} @ {avg or o.get('px')}")
            if str(status or '').upper().replace(' ', '_') in FINAL_STATES:
                o['done'] = True
        if adopted:
            self.note = 'at start-up: a hedge order filled while the algo was off, adopted as its hedge (' + '; '.join(adopted) + ')'
            log.warning('hedge: %s', self.note)
        self.save()
        if broker_units is self._ASK:
            broker_units = self._broker_units() if self.position_symbol() else None
        held = self.units()
        if broker_units is None:
            return
        b = int(round(broker_units))
        sym = self.position_symbol()
        if held:
            if b == 0 or (b > 0) != (held > 0):
                self._mark_flat(f'at start-up the broker holds no {self.st.get("symbol")} hedge')
                return
            if abs(b) < abs(held):
                self.book((abs(b) - abs(held)) * (1 if held > 0 else -1), self.fresh_price() or self.st.get('avg'),
                          'at start-up the broker holds less')
                return
        extra = abs(b) - abs(held)
        if extra > 0 and sym:
            self.st['outside'] = {'symbol': sym, 'units': extra, 'at': _dt.datetime.now(_dt.timezone.utc).isoformat()}
            self.note = (f'at start-up the account holds {extra} {sym} that the algo\'s hedge record does not have (bought '
                         'outside the algo, or by an earlier run): no second hedge is opened while they are there, and the '
                         'algo never sells them')
            log.warning('hedge: %s', self.note)
            self.save()
        elif self.st.get('outside'):
            self.st['outside'] = None
            self.save()

    # ------------------------------------------------------------ status for the algo tab
    def status(self):
        st, px = self.st, self.fresh_price()
        u = self.units()
        unreal = (px - st['avg']) * u * float(st.get('mult') or 1.0) if u and px and st.get('avg') else 0.0
        day = st.get('day_pnl', 0.0) if st.get('day') == self._today() else 0.0
        ok, why = self.active()
        return {'on': bool(self.cfg.get('on')), 'live_on': bool(self.cfg.get('live_on')), 'active': ok,
                'why': None if ok else why, 'symbol': self.symbol(), 'ratio_pct': self.cfg.get('ratio_pct'),
                'held_symbol': st.get('symbol') if u else None, 'units': u, 'avg': st.get('avg'), 'last': px,
                'unrealized': round(unreal, 2), 'realized_today': round(day, 2), 'realized': st.get('realized', 0.0),
                'pending': self.pending, 'note': self.note, 'trips': (st.get('trips') or [])[-10:],
                'outside': st.get('outside') if self.core.mode == 'live' else None, 'mode': self.core.mode}
