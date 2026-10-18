"""Broker-independent heart of the paper/live trader (identical file in webull-algo and
ibkr-algo). Each broker's paper_trader.py feeds it ticks and supplies one function that places
a real order; everything else lives here, once:

  ticks -> 1-minute bars -> indicators -> entry/exit rules -> simulated order (paper mode)
                                                           -> guarded real order (live mode)
  + portfolio state saved after every bar/trade and restored on restart (runstate.py)
  + logs/live_status.json for the dashboard's algo tab

Sessions and orders follow the trading options (algo/options.py: built-in defaults, the
strategy's trading: section, or the dashboard's Trading options panel -- re-read every few
seconds). Regular hours (9:30-16:00 ET) are always on; pre-market, after-hours and overnight
can each be turned on separately for paper trading and for live trading.
  * limit orders: price = last trade price +/- limit_offset_pct (at least one tick; exits are
    always priced to fill, never at a "better" price), cancelled if not filled within
    cancel_after_s ("unfilled" -- not an error; the rule simply fires again later if still true)
  * market orders: only in regular hours; outside them the order is sent as a limit order
  * trailing stop (optional): the algo watches every trade price and sends the exit order when
    the price has fallen trailing_stop_pct from its best since entry (rises, for a short).
    It is not an order resting at the broker: it only works while the algo is running.
  * paper mode fills a limit order only if a later trade price reaches it (after slippage and,
    outside regular hours, an extra cost for the wider spreads); market orders fill at once

Live-mode safety, on top of guards.py's two switches and per-order limits:
  * every order is capped by the strategy's risk.max_order_notional_usd (required for live):
    the share count is cut down to fit, never above it
  * one order at a time: while an order is working, new signals are ignored
  * the ledger books only what the broker reports as FILLED, at the broker's fill price and
    quantity (partial fills included); a rejected or unfilled order is not booked
  * after a rejection no new order for 15 minutes; after 3 rejections in a row live trading
    stops
  * the dashboard's Stop button (kill switch) writes logs/KILL: the algo closes its position
    (paper: at once at the last price; live: with an exit order, in a session turned on for
    live trading) and opens nothing new until Resume deletes the file
  * an order whose outcome can't be confirmed (no answer, connection lost) is cancelled if
    possible and live trading PAUSES -- nothing more is sent until a person has checked the
    account and restarted it (at start-up the trader compares its ledger with the broker's
    real position and refuses to run if they differ)
  * Auto-Hedge (jb-hedge, algo/autohedge.py, default off): a hedge opened and closed with each
    position, under all of the rules above
"""
import datetime as _dt
import logging
import json
import os
import time

import pandas as pd

from . import autohedge
from . import guards
from . import options as opt
from .portfolio import Portfolio
from .runstate import StateFile, atomic_write_json, session_at

log = logging.getLogger('trader')
MAX_BARS = 5000
REJECT_COOLDOWN_S = 15 * 60
MAX_REJECTS = 3
CLOSE_RETRY_S = 60            # review 2026-10-11 #3: a rejected closing order is tried again after 1 min, not 15
OPTIONS_CHECK_S = 5
# after one of these exits, no new entry until the entry rule has been false at least once (jb-ready): a rule
# such as "sma_20 > sma_50" is often still true right after a stop-loss, and would buy straight back in
RISK_EXITS = ('stop-loss', 'take-profit', 'trailing-stop', 'broker-stop')
# live: a backup stop order resting AT THE BROKER while a position is open (jb-ready). It protects the shares if
# the Mac or the algo is down. Placed a little wider than the algo's own stop-loss (risk.broker_stop_pct; default
# stop_loss_pct + 1, or 10% without a stop-loss; 0 = off), and cancelled before the algo sends its own exit, so the
# shares can never be sold twice.
BSTOP_DEFAULT_PCT = 10.0
BSTOP_CHECK_S = 20            # how often its state is read from the broker (2 s while a cancel is waiting)
BSTOP_RETRY_S = 300           # after the broker refused or dropped it: wait this long before placing it again
BSTOP_UNKNOWN_S = 60          # an exit waits this long for an answer about the stop before giving up on it
ET = 'America/New_York'
COLUMNS = ['open', 'high', 'low', 'close', 'volume']


class BarAggregator:
    """Ticks -> 1-minute OHLCV bars. A bar is handed over only once a tick from a LATER minute
    arrives (or flush_due() is called after the minute ends), i.e. once nothing more can change it."""

    def __init__(self):
        self.minute = None
        self.o = self.h = self.l = self.c = None
        self.v = 0.0

    def _emit(self):
        bar = {'t': self.minute, 'open': self.o, 'high': self.h, 'low': self.l, 'close': self.c, 'volume': self.v}
        self.o = self.h = self.l = self.c = None
        self.v = 0.0
        return bar

    def add_tick(self, price, ts_ms, volume=0.0):
        m = (int(ts_ms) // 60000) * 60000
        closed = None
        if self.minute is not None and m > self.minute and self.o is not None:
            closed = self._emit()
        if self.minute is None or m > self.minute:
            self.minute = m
        if self.o is None:
            self.o = self.h = self.l = self.c = price
        else:
            self.h, self.l, self.c = max(self.h, price), min(self.l, price), price
        self.v += volume or 0.0
        return closed

    def flush_due(self, now_ms, grace_ms=2000):
        if self.minute is not None and self.o is not None and now_ms >= self.minute + 60000 + grace_ms:
            return self._emit()
        return None


class TraderCore:
    def __init__(self, strategy, mode='paper', logs_dir='logs', starting_cash=100_000.0,
                 commission_per_share=0.0, slippage_pct=0.0, max_order_quantity=10_000,
                 max_order_notional_usd=None, status_path=None, broker_name='', options_path=None):
        if mode not in ('paper', 'live'):
            raise ValueError("mode must be 'paper' or 'live'")
        self.strategy = strategy
        self.mode = mode
        self.broker_name = broker_name
        self.portfolio = Portfolio(starting_cash=starting_cash, commission_per_share=commission_per_share,
                                   slippage_pct=slippage_pct)
        self.state = StateFile(logs_dir, strategy, mode)
        self.restored = self.state.load_into(self.portfolio)
        if self.restored:
            log.warning('restored saved state: %s', self.restored)
        self.max_order_quantity = max_order_quantity
        self.max_order_notional_usd = max_order_notional_usd
        self.status_path = status_path
        self.options_path = options_path
        self._opts_stamp = object()
        self._opts_checked = 0.0
        self._opts_good = False       # True once options were read successfully
        self.opts, self.opts_source, self.opts_problem = dict(opt.DEFAULTS), 'built-in defaults', None
        self.reload_options(force=True)
        self.halted = None            # live: why live trading is paused (None = trading)
        self.rejects = 0
        self.cooldown_until = 0.0
        self.bars = pd.DataFrame({c: pd.Series(dtype='float64') for c in COLUMNS},
                                 index=pd.DatetimeIndex([], tz='UTC'))
        self.agg = BarAggregator()
        self.last_price = None
        self.connected = False
        self.last_error = None
        self.last_order = None
        self.pending = None           # live: dict describing the order in flight, or None
        self.paper_order = None       # paper: the simulated limit order still working, or None
        self.place_live = None        # set by the broker: fn(spec, on_filled, on_failed, on_unconfirmed, on_unfilled)
        self.kill_path = os.path.join(logs_dir, 'KILL')
        self.killed = None            # ISO time the kill switch was pressed (None = trading normally)
        self._kill_try = 0.0
        self.stop_api = None          # live: {'place', 'cancel', 'status'} broker functions for the backup stop
        self.bstop = None             # live: the backup stop at the broker (dict), or None
        self.bstop_path = os.path.join(logs_dir, 'broker_stop.json')
        self._bstop_wait_exit = None  # (reason, since) an exit waiting for the backup stop to be cancelled
        self._bstop_checked = 0.0
        self._bstop_failed_at = 0.0
        self._bstop_unknown_since = None
        self._bstop_gone_at = None    # first time the broker's open-order list showed no such stop (see _bstop_gone)
        if mode == 'live':
            try:
                with open(self.bstop_path) as f:
                    d = json.load(f)
                if isinstance(d, dict) and d.get('symbol') == strategy.symbol:
                    self.bstop = d
            except Exception:
                pass
        self._rearm_at = -1           # trade count at which the entry rule was seen false after a risk exit
        self.broker_position = None   # live: a function giving the broker's real signed share count (set by the trader)
        self.buying_power = None      # live: the broker's buying power in $, refreshed every minute by the trader (jb-ready)
        self.buying_power_at = 0.0
        # Auto-Hedge (jb-hedge, algo/autohedge.py): off unless turned on on the algo tab; the trader feeds it the
        # hedge's prices (on_hedge_tick) and, live, sets self.hedge.place / self.hedge.position
        self.hedge = autohedge.AutoHedge(self, logs_dir)
        self.no_short_sessions = set()  # sessions the broker takes no short sales in (Webull: overnight), set by the trader
        self.broker_notes = []          # Title Case lines from the trader for the algo tab (e.g. Webull short sales)

    # ------------------------------------------------------------ options
    def reload_options(self, force=False):
        """Picks up a change to the dashboard's options file (checked every few seconds)."""
        now = time.time()
        if not force and now - self._opts_checked < OPTIONS_CHECK_S:
            return
        self._opts_checked = now
        st = opt.stamp(self.options_path)
        if not force and st == self._opts_stamp:
            return
        self._opts_stamp = st
        try:
            opts, source, problem = opt.effective(getattr(self.strategy, 'trading_raw', None), self.options_path)
        except opt.OptionsError as e:
            opts, source, problem = dict(opt.DEFAULTS), 'built-in defaults', str(e)
        if problem:
            # the dashboard's file exists but can't be used: never fall back to settings that may
            # trade MORE (e.g. a session the dashboard had turned off) -- keep what was in force,
            # or at start-up the safest choice, regular hours only
            if self._opts_good:
                opts, source = self.opts, self.opts_source
            else:
                opts, source = dict(opt.DEFAULTS), 'built-in defaults'
        else:
            self._opts_good = True
        changed = opts != self.opts or source != self.opts_source
        self.opts, self.opts_source, self.opts_problem = opts, source, problem
        if problem:
            log.error('trading options: %s -- keeping the %s settings', problem, source)
        if changed or force:
            log.warning('trading options (%s): %s', source, opt.describe(opts, self.mode))
        if changed and not force:
            self.write_status()

    def allowed_sessions(self):
        return {'regular'} | set(self.opts['live_sessions' if self.mode == 'live' else 'paper_sessions'])

    def session_now(self):
        """(session name or None, True if the algo may trade in it)."""
        s = session_at()
        return s, s is not None and s in self.allowed_sessions()

    def busy(self):
        return self.pending is not None or self.paper_order is not None

    # ------------------------------------------------------------ data in
    def seed_bars(self, df):
        """Warm-up history (DataFrame indexed by UTC time with OHLCV columns)."""
        if df is None or not len(df):
            return
        df = df[COLUMNS].astype('float64').sort_index()
        df = df[~df.index.duplicated(keep='last')]
        now_min = pd.Timestamp(int(time.time() // 60 * 60), unit='s', tz='UTC')
        df = df[df.index < now_min]        # the minute still forming comes from live ticks
        self.bars = df.iloc[-MAX_BARS:]
        log.info('warmed up with %d historical 1-min bars for %s', len(self.bars), self.strategy.symbol)

    def on_tick(self, price, ts_ms, volume=0.0):
        if price is None or not (price > 0):
            return
        price = float(price)
        self.last_price = price
        if self.paper_order is not None:
            self._paper_try_fill(price)
        closed = self.agg.add_tick(price, int(ts_ms), volume)
        if closed:
            self._on_bar(closed)
        self._trail(price)

    def on_timer(self, now_ms):
        """Call every few seconds: closes a finished minute even if no new trade arrived,
        expires an unfilled paper limit order, and re-reads the trading options."""
        self.reload_options()
        self.check_kill()
        self._guardian_tick()      # heartbeat, price cross-check, live safety (jb-ideas)
        self._bstop_tick()         # live: the backup stop at the broker (jb-ready)
        po = self.paper_order
        if po is not None and time.time() >= po['expires']:
            self.paper_order = None
            s = po['spec']
            self._note_order(s['side'], s['shares'], 'unfilled', price=s['limit'],
                             detail=f"{po['reason']}: limit {s['limit']:g} not reached within {s['wait_s']}s (paper)")
            log.info('paper %s %s limit %s not filled within %ss', s['side'], s['shares'], s['limit'], s['wait_s'])
            self.write_status()
        closed = self.agg.flush_due(now_ms)
        if closed:
            self._on_bar(closed)
        try:
            self.hedge.tick()          # Auto-Hedge (jb-hedge): open / follow / close the hedge with the position
        except Exception as e:
            log.exception('auto-hedge failed: %r', e)

    def on_hedge_tick(self, price, ts_ms=None):
        """A trade price of the Auto-Hedge instrument (jb-hedge)."""
        self.hedge.on_price(price, ts_ms)

    # ------------------------------------------------------------ strategy
    def _on_bar(self, bar):
        t = pd.Timestamp(bar['t'], unit='ms', tz='UTC')
        row = pd.DataFrame([{c: float(bar[c]) for c in COLUMNS}], index=pd.DatetimeIndex([t]))
        if len(self.bars) and t <= self.bars.index[-1]:
            return                                        # never rewrite an earlier bar
        self.bars = pd.concat([self.bars, row]) if len(self.bars) else row
        if len(self.bars) > MAX_BARS:
            self.bars = self.bars.iloc[-MAX_BARS:]
        work = self.bars.copy()
        self.strategy.compute_indicators(work)
        i = len(work) - 1
        close = float(bar['close'])
        pf = self.portfolio
        sess, ok = self.session_now()
        # the bar itself must belong to a session that is turned on too (a 9:29 pre-market bar
        # closes at 9:30 -- with pre-market off it must not trade, just as in a backtest)
        bar_sess = session_at((t + pd.Timedelta(seconds=59)).to_pydatetime())
        ok = ok and bar_sess in self.allowed_sessions()
        ref = self.last_price or close                    # orders are priced from the latest trade
        if ok and not self.busy():                        # orders only in the sessions turned on
            if pf.position is not None:
                hit = pf.stop_take_hit(close, self.strategy.stop_loss_pct, self.strategy.take_profit_pct)
                if hit:
                    self._exit(t, ref, hit, sess)
                elif self.strategy.check_exit(work, i):
                    self._exit(t, ref, 'exit-rule', sess)
            elif not self.killed and not self._doubt():
                trs = pf.trades
                if trs and trs[-1].reason in RISK_EXITS and self._rearm_at != len(trs):
                    if not self.strategy.check_entry(work, i):
                        self._rearm_at = len(trs)             # the signal has reset: entries allowed again
                elif self.strategy.check_entry(work, i):
                    self._enter(t, ref, sess)
        pf.mark(t, close)
        if len(pf.equity_curve) > 20000:                  # months of minutes: keep memory flat
            del pf.equity_curve[:-10000]
        log.info('%s bar close=%.2f equity=%.2f position=%s', t.tz_convert(ET).strftime('%Y-%m-%d %H:%M ET'), close,
                 pf.equity(close), pf.position.side if pf.position else 'flat')
        self.save()

    def _trail(self, price):
        """Trailing stop, checked on every trade price."""
        pct = self.opts.get('trailing_stop_pct')
        pos = self.portfolio.position
        if not pct or pos is None:
            return
        if pos.peak is None:
            pos.peak = pos.entry_price
        if pos.side == 'long':
            pos.peak = max(pos.peak, price)
            hit = price <= pos.peak * (1 - pct / 100.0)
        else:
            pos.peak = min(pos.peak, price)
            hit = price >= pos.peak * (1 + pct / 100.0)
        if not hit or self.busy() or time.time() < self.__dict__.get('_close_retry_at', 0.0):
            return                # a rejection or a halt pauses new buys only; the trailing stop still sells
        sess, ok = self.session_now()
        if not ok:
            return
        log.warning('trailing stop: %s is %.2f, %.2f%% from the best price %.2f since entry', self.strategy.symbol,
                    price, pct, pos.peak)
        self._exit(pd.Timestamp.now(tz='UTC'), price, 'trailing-stop', sess)

    # ------------------------------------------------------------ kill switch
    def check_kill(self):
        if not os.path.exists(self.kill_path):
            if self.killed:
                log.warning('kill switch released (Resume): trading again')
                self.killed = None
                self.write_status()
            return
        if not self.killed:
            self.killed = _dt.datetime.now(_dt.timezone.utc).isoformat()
            log.warning('KILL SWITCH: closing the position and stopping new trades')
            self.write_status()
        pos = self.portfolio.position
        if self.mode == 'paper':
            po = self.paper_order
            if po is not None:
                self.paper_order = None
                s = po['spec']
                self._note_order(s['side'], s['shares'], 'cancelled', price=s['limit'], detail='kill switch (paper)')
            price = self.last_price or (float(self.bars['close'].iloc[-1]) if len(self.bars) else None)
            if pos is not None and price:
                side, n = ('sell' if pos.side == 'long' else 'buy'), pos.shares
                fill = self.sim_price(side, price, session_at() or 'overnight')
                self.portfolio.close_position(pd.Timestamp.now(tz='UTC'), fill, 'kill-switch', exact=True)
                self._note_order(side, n, 'paper fill', price=fill, detail='kill switch')
                log.warning('kill switch: paper %s %s %s @ %.4f', side, n, self.strategy.symbol, fill)
            if po is not None or pos is not None:
                self.save()
            return
        if pos is None or self.busy() or time.time() - self._kill_try < 30:
            return
        self._kill_try = time.time()
        # Never sell on the algo's own record alone: SELL ALL (or you, in the broker's app) may already have
        # closed the position, and a second sell would leave the account short. Check the broker first.
        held = self._broker_shares()
        if held is None:
            self._note_order('sell' if pos.side == 'long' else 'buy', pos.shares, 'blocked',
                             detail='kill switch: could not read the real position from the broker, so nothing was sent '
                                    '-- check the broker app; it tries again in 30 s')
            self.write_status()
            return
        want = pos.shares if pos.side == 'long' else -pos.shares
        if held == 0 or (held > 0) != (want > 0):
            self._mark_flat('the broker shows no ' + pos.side + ' position (closed by SELL ALL or in the broker app)')
            return
        if self._kill_by() == 'sellall':
            return                    # SELL ALL is closing it right now; it re-checks in 30 s
        if abs(held) < abs(want):
            self._note_order('sell' if pos.side == 'long' else 'buy', pos.shares, 'blocked',
                             detail=f'kill switch: the broker holds only {abs(held):g} of the algo\'s {pos.shares} shares '
                                    '-- close the rest in the broker app')
            self.write_status()
            return
        sess, ok = self.session_now()
        price = self.last_price
        if ok and price:
            self._exit(pd.Timestamp.now(tz='UTC'), price, 'kill-switch', sess)
        else:
            self._note_order('sell' if pos.side == 'long' else 'buy', pos.shares, 'blocked',
                             detail='kill switch: cannot close now (' + (opt.LABELS.get(sess, 'market closed') if sess else 'market closed')
                                    + ' is not turned on for live trading, or no price yet) -- close it in the broker app')
            self.write_status()

    def _kill_by(self):
        """Who wrote the KILL file: 'sellall' for the SELL ALL button, '' for the Stop button."""
        try:
            with open(self.kill_path) as f:
                return str(json.load(f).get('by') or '')
        except Exception:
            return ''

    def _broker_shares(self):
        """The broker's real signed share count, or None if it can't be read right now."""
        if self.broker_position is None:
            return None
        try:
            v = self.broker_position()
            return None if v is None else float(v)
        except Exception as e:
            log.warning('kill switch: could not read the broker position: %r', e)
            return None

    def _mark_flat(self, why):
        """The position was closed outside the algo: book it at the last price so the record matches the account."""
        pos = self.portfolio.position
        price = self.last_price or (float(self.bars['close'].iloc[-1]) if len(self.bars) else None)
        if pos is None or not price:
            return
        n, side = pos.shares, ('sell' if pos.side == 'long' else 'buy')
        self.portfolio.close_position(pd.Timestamp.now(tz='UTC'), price, 'closed outside the algo', exact=True)
        self._note_order(side, n, 'closed outside', price=price, detail='kill switch: ' + why + '; booked at the last price')
        log.warning('kill switch: %s -- algo record set to flat', why)
        self.save()
        self.write_status()

    def order_spec(self, side, shares, ref_price, session, closing=False):
        """What to send: market or limit (and the limit price), for which session."""
        o = self.opts
        kind, note = o['order_type'], ''
        if kind == 'market' and session != 'regular':
            kind, note = 'limit', 'sent as a limit order: market orders only 9:30-16:00 ET'
        limit = None
        if kind == 'limit':
            limit = opt.limit_price(ref_price, side, o['limit_offset_pct'], closing=closing)
        return {'side': side, 'shares': int(shares), 'type': kind, 'limit': limit, 'session': session,
                'wait_s': int(o['cancel_after_s']) if kind == 'limit' else 60, 'ref_price': float(ref_price),
                'closing': closing, 'note': note}

    @staticmethod
    def spec_price(spec):
        """The highest price the order can cost per share (for size caps and guards)."""
        return max(spec['ref_price'], spec['limit'] or 0.0)

    def _enter(self, t, price, sess):
        side = 'long' if self.strategy.entry_side == 'buy' else 'short'
        if side == 'short' and sess in self.no_short_sessions:
            # the broker takes no short sales in this session (Webull: overnight; jb-parity): not sent, not counted as an order
            if (self.last_order or {}).get('status') != 'skipped' or 'short' not in str((self.last_order or {}).get('detail')):
                self._note_order('sell', 0, 'skipped', detail=f'{self.broker_name.title()} takes no short sales '
                                                               f'{opt.LABELS.get(sess, sess)}')
            return
        shares = self.strategy.position_size(self.portfolio.equity(price), price)
        spec = self.order_spec(self.strategy.entry_side, shares, price, sess)
        if self.mode == 'live':
            cap = self.max_order_notional_usd or self.strategy.max_order_notional_usd
            if cap:
                spec['shares'] = min(spec['shares'], int(cap // self.spec_price(spec)))   # never above the cap
        if spec['shares'] <= 0:
            if self.mode == 'live':
                self._note_order(self.strategy.entry_side, 0, 'skipped',
                                 detail='one share costs more than risk.max_order_notional_usd')
            return
        if self.mode == 'live':
            # buying power check (jb-ready): never send an entry the account can't pay for. Uses the broker's own
            # figure, read in the last 5 minutes; if it can't be read, the broker's own check still applies.
            bp = self.buying_power if time.time() - self.buying_power_at < 300 else None
            cost = spec['shares'] * self.spec_price(spec)
            if bp is not None and cost > bp:
                self._note_order(spec['side'], spec['shares'], 'skipped',
                                 detail=f'not enough buying power: this order needs ${cost:,.0f}, the broker shows '
                                        f'${bp:,.0f} available')
                log.warning('LIVE entry skipped: needs $%.0f, buying power $%.0f', cost, bp)
                return
        if self.mode == 'paper':
            if self._paper_blocked(spec):      # same checks as live (jb-ideas)
                return
            if spec['type'] == 'market':
                self.portfolio.open_position(t, side, spec['shares'], price, 'entry')
                self._note_order(spec['side'], spec['shares'], 'paper fill', price=self.portfolio.position.entry_price,
                                 detail='entry (market)')
            else:
                self._paper_submit(spec, 'entry',
                                   lambda fill, n: self.portfolio.open_position(t, side, n, fill, 'entry', exact=True))
            return
        self._live(spec, 'entry', lambda fill, n: self.portfolio.open_position(t, side, n, fill, 'entry', exact=True))

    def _book_exit(self, t, reason, fill, filled):
        pos = self.portfolio.position
        if pos is None:
            return
        if filled >= pos.shares:
            self.portfolio.close_position(t, fill, reason, exact=True)
        else:                                  # partly filled: keep the rest open
            self.portfolio.reduce_position(t, filled, fill, reason, exact=True)

    def _exit(self, t, price, reason, sess):
        pos = self.portfolio.position
        if pos is None:
            return
        side = 'sell' if pos.side == 'long' else 'buy'
        if self.mode == 'live' and self.bstop is not None:
            # the backup stop must be gone before the algo sells, or both could sell (leaving the account short)
            if self._bstop_wait_exit is None:
                self._bstop_wait_exit = (reason, time.time())
                self._note_order(side, pos.shares, 'waiting', detail=f'{reason}: cancelling the backup stop at the broker first')
                self._bstop_checked = 0.0
                self._bstop_tick()
            return
        spec = self.order_spec(side, pos.shares, price, sess, closing=True)
        if self.mode == 'paper':
            if self._paper_blocked(spec):      # same checks as live (jb-ideas)
                return
            if spec['type'] == 'market':
                n = pos.shares
                self.portfolio.close_position(t, price, reason)
                self._note_order(side, n, 'paper fill', price=self.portfolio.trades[-1].price,
                                 detail=f'{reason} (market)')
            else:
                self._paper_submit(spec, reason, lambda fill, n: self._book_exit(t, reason, fill, n))
            return
        self._live(spec, reason, lambda fill, n: self._book_exit(t, reason, fill, n))

    # ------------------------------------------------------------ paper limit orders
    def sim_price(self, side, price, session):
        """What a paper fill really costs: slippage, plus the extra spread outside regular hours."""
        pct = self.portfolio.slippage_pct + (self.opts['extended_hours_cost_pct'] if session != 'regular' else 0.0)
        adj = price * pct / 100.0
        return price + adj if side == 'buy' else price - adj

    def _paper_submit(self, spec, reason, book):
        self.paper_order = {'spec': spec, 'reason': reason, 'book': book,
                            'expires': time.time() + spec['wait_s'], 'placed_at': time.time()}
        self._note_order(spec['side'], spec['shares'], 'working', price=spec['limit'],
                         detail=f"{reason}: paper limit {spec['limit']:g} ({opt.LABELS[spec['session']]})"
                                + (f" -- {spec['note']}" if spec['note'] else ''))
        log.info('paper %s %s %s limit %s (%s)', spec['side'], spec['shares'], self.strategy.symbol, spec['limit'],
                 spec['session'])
        self.write_status()

    def _paper_try_fill(self, price):
        po = self.paper_order
        s = po['spec']
        if session_at() != s['session']:
            return                        # a limit order only works in the session it was sent for
        eff = self.sim_price(s['side'], price, s['session'])
        if (s['side'] == 'buy' and eff > s['limit']) or (s['side'] == 'sell' and eff < s['limit']):
            return
        self.paper_order = None
        po['book'](eff, s['shares'])
        self._note_order(s['side'], s['shares'], 'paper fill', price=eff,
                         detail=f"{po['reason']} (limit {s['limit']:g})")
        log.info('paper fill: %s %s %s @ %.4f (limit %s)', s['side'], s['shares'], self.strategy.symbol, eff, s['limit'])
        self.save()

    # ------------------------------------------------------------ live orders
    def _live(self, spec, reason, book):
        side, shares = spec['side'], spec['shares']
        # the Stop button must work even while live trading is paused (jb-ready): check_kill has already
        # read the broker's real position, and an order still in doubt keeps self.pending, which blocks it
        kill = reason == 'kill-switch'
        if not kill and self._kill_by() == 'sellall':
            # review 2026-10-11 #1: SELL ALL is closing this position at the broker; a stop-loss, exit rule or
            # trailing stop sent now would sell the same shares a second time and could leave the account short
            # (the file itself is read: the 2-second timer may not have seen it yet)
            self._note_order(side, shares, 'blocked', detail='SELL ALL is closing this position, so the algo sends nothing')
            return
        closing = kill or bool(spec.get('closing'))
        # review 2026-10-11 #3: after rejections (cooldown, or halted after 3 in a row) the algo stops BUYING,
        # but stop-loss / take-profit / exit-rule / trailing-stop sells still go out, so a position is never
        # left without its stop. (An order that could not be confirmed keeps self.pending, which still blocks.)
        if self.halted and not closing:
            self._note_order(side, shares, 'blocked', detail=self.halted[:200])
            return
        if self.pending:
            log.warning('signal %s %s ignored: an order is still working', side, shares)
            return
        if time.time() < self.cooldown_until and not closing:
            self._note_order(side, shares, 'skipped', detail='waiting 15 min after a rejected order')
            return
        if closing and not kill and time.time() < self.__dict__.get('_close_retry_at', 0.0):
            return                # a closing order was just rejected: tried again after CLOSE_RETRY_S
        sess, ok = self.session_now()
        if not ok or sess != spec['session']:
            self._note_order(side, shares, 'skipped', detail=f'{opt.LABELS.get(sess, "market closed")} is not '
                                                            f'turned on for live trading')
            log.warning('LIVE %s %s %s skipped: session %s not turned on', side, shares, self.strategy.symbol, sess)
            return
        if self.place_live is None:
            self._note_order(side, shares, 'error', detail='no broker connected')
            return
        try:
            guards.check_live_trading_enabled(self.strategy)
            guards.check_order(self.strategy, side, self.strategy.symbol, shares, self.spec_price(spec),
                               max_order_quantity=self.max_order_quantity,
                               max_order_notional_usd=self.max_order_notional_usd, closing=spec['closing'])
        except guards.GuardRejected as e:
            log.error('LIVE ORDER BLOCKED by guards: %s', e)
            self._note_order(side, shares, 'blocked', detail=str(e)[:200])
            return
        # safe mode, speed limit, duplicate (jb-ideas). Checked last: it counts the order as sent, so an order
        # skipped above (waiting after a rejection, session off) never counts toward the speed limits
        why = None if kill else self._guardian_block(spec)
        if why:
            self._note_order(side, shares, 'blocked', detail=why[:200])
            return
        how = 'market' if spec['type'] == 'market' else f"limit {spec['limit']:g}"
        self.pending = {'side': side, 'shares': shares, 'reason': reason, 'type': spec['type'],
                        'limit': spec['limit'], 'session': spec['session'], 'sent_at': time.time()}
        self._note_order(side, shares, 'sent', price=spec['limit'],
                         detail=f"{reason}: {how}, {opt.LABELS[spec['session']]}"
                                + (f" -- {spec['note']}" if spec['note'] else ''))

        def filled(fill_price, filled_shares=None, status='filled'):
            self.pending = None
            self.rejects = 0
            fp = float(fill_price or spec['limit'] or spec['ref_price'])
            n = int(filled_shares if filled_shares is not None else shares)
            if n > 0:
                book(fp, n)
            self._note_order(side, n, status, price=fp, detail=reason)
            self.save()

        def unfilled(detail):
            self.pending = None
            self._note_order(side, shares, 'unfilled', price=spec['limit'], detail=f'{reason}: {detail}'[:200])
            log.warning('LIVE ORDER NOT FILLED (%s %s %s): %s', side, shares, self.strategy.symbol, detail)
            self.save()

        def failed(detail):
            self.pending = None
            self.rejects += 1
            self.cooldown_until = time.time() + REJECT_COOLDOWN_S
            if spec.get('closing'):
                self._close_retry_at = time.time() + CLOSE_RETRY_S
            log.error('LIVE ORDER FAILED (%s %s %s): %s', side, shares, self.strategy.symbol, detail)
            self._note_order(side, shares, 'rejected', detail=str(detail)[:200])
            if self.rejects >= MAX_REJECTS:
                self.halted = (f'{MAX_REJECTS} live orders in a row were rejected (last: {str(detail)[:120]}). '
                               f'Live trading stopped -- fix the cause, then start it again.')
                log.error('LIVE TRADING STOPPED: %s', self.halted)
            self.save()

        def unconfirmed(detail):
            # keep self.pending: nothing else may be sent until a person has checked
            self.halted = (f'an order could not be confirmed ({str(detail)[:120]}). Live trading is '
                           f'PAUSED: check the order and your position in the broker app, then stop this '
                           f'window (Ctrl+C) and start ./jb live again.')
            log.error('LIVE TRADING PAUSED: %s', self.halted)
            self._note_order(side, shares, 'unconfirmed', detail=str(detail)[:200])
            self.save()

        try:
            self.place_live(spec, filled, failed, unconfirmed, unfilled)
        except guards.GuardRejected as e:
            self.pending = None
            log.error('LIVE ORDER BLOCKED: %s', e)
            self._note_order(side, shares, 'blocked', detail=str(e)[:200])
        except Exception as e:
            failed(_describe(e))

    # ------------------------------------------------------------ backup stop at the broker (jb-ready)
    def bstop_pct(self):
        v = getattr(self.strategy, 'broker_stop_pct', None)
        if v is None:
            sl = self.strategy.stop_loss_pct
            return float(sl) + 1.0 if sl else BSTOP_DEFAULT_PCT
        return max(0.0, float(v))

    def _bstop_save(self):
        try:
            if self.bstop is None:
                if os.path.exists(self.bstop_path):
                    os.remove(self.bstop_path)
            else:
                atomic_write_json(self.bstop_path, self.bstop)
        except Exception as e:
            log.warning('could not save the backup stop record: %r', e)

    def bstop_filled_while_away(self, broker_shares):
        """Live start-up: the broker holds fewer shares than the ledger, and the backup stop explains it (it
        filled while the algo was not running). Books that exit at the stop price and returns True."""
        b, pos = self.bstop, self.portfolio.position
        if b is None or pos is None:
            return False
        held = abs(int(round(broker_shares)))
        if held >= pos.shares or pos.shares - held > b['shares']:
            return False
        n = pos.shares - held
        self._book_exit(pd.Timestamp.now(tz='UTC'), 'broker-stop', float(b['stop']), n)
        self._note_order(b['side'], n, 'filled by broker stop', price=b['stop'],
                         detail='the backup stop at the broker sold while the algo was off (booked at the stop price; '
                                'the broker app shows the exact fill)')
        log.warning('backup stop filled while the algo was off: %s %s at about %s', b['side'], n, b['stop'])
        self.bstop = None
        self._bstop_save()
        self.save()
        return True

    def _bstop_gone(self, b, now):
        """True once the broker's open-order list (stop_api['listed']) has shown no such stop twice, 10 s apart.
        Anything else (still listed, or the list can't be read) keeps the exit waiting."""
        fn = (self.stop_api or {}).get('listed')
        try:
            listed = fn(b) if fn else None
        except Exception as e:
            log.warning('could not list the open orders: %r', e)
            listed = None
        if listed is not False:
            self._bstop_gone_at = None
            return False
        if self._bstop_gone_at is None:
            self._bstop_gone_at = now
            return False
        return now - self._bstop_gone_at >= 10

    def _bstop_tick(self):
        if self.mode != 'live' or self.stop_api is None or not self.connected:
            return
        now = time.time()
        pos, b = self.portfolio.position, self.bstop
        wait = self._bstop_wait_exit
        # 1. what the broker says about the stop that is there
        if b is not None and now - self._bstop_checked >= (2 if b.get('cancelling') or wait else BSTOP_CHECK_S):
            self._bstop_checked = now
            try:
                state, filled, avg = self.stop_api['status'](b)
            except Exception as e:
                log.warning('could not read the backup stop: %r', e)
                state, filled, avg = 'unknown', 0, None
            if state == 'unknown':
                self._bstop_unknown_since = self._bstop_unknown_since or now
                if wait and pos is not None and now - self._bstop_unknown_since >= BSTOP_UNKNOWN_S:
                    # review 2026-10-11 #7: never sell while the stop may still be at the broker (both could fill and
                    # leave the account short). If the broker holds fewer shares, the stop sold them; otherwise keep
                    # waiting (the stop itself still protects the position) and ask you to check, once.
                    held = self._broker_shares()
                    if held is not None and abs(held) < pos.shares:
                        state, filled, avg = 'filled', pos.shares - abs(held), None
                    elif held is not None and self._bstop_gone(b, now):
                        # the broker's own list of open orders has had no such stop for 10 s and it still holds all
                        # the shares: the stop is gone (e.g. cancelled while the connection was down), so the exit goes out
                        log.warning('backup stop: no answer about it, and the broker lists no such open order -- the %s '
                                    'sell goes out', wait[0])
                        state, filled, avg = 'cancelled', 0, None
                    elif self.__dict__.get('_bstop_asked') != b.get('id'):
                        self._bstop_asked = b.get('id')
                        log.error('backup stop: no answer from the broker for %ds -- the exit waits until it answers',
                                  BSTOP_UNKNOWN_S)
                        self._push('Check the backup stop', f'{self.broker_name.upper()} algo ({self.strategy.symbol}): the broker '
                                   f'gives no answer about its backup stop order, so the algo\'s {wait[0]} sell is waiting (selling '
                                   'now could sell twice). The stop still protects the shares. If you want out now, cancel the '
                                   'stop in the broker app; the algo then sells.', 'rotating_light')
            else:
                self._bstop_unknown_since = None
                self._bstop_gone_at = None
            if state in ('filled', 'cancelled'):
                n = min(int(round(filled or 0)), pos.shares) if pos is not None else 0
                if n > 0:
                    px = float(avg or b['stop'])
                    self._book_exit(pd.Timestamp.now(tz='UTC'), 'broker-stop', px, n)
                    self._note_order(b['side'], n, 'filled by broker stop', price=px, detail='backup stop at the broker')
                    log.warning('BACKUP STOP FILLED at the broker: %s %s %s @ %s', b['side'], n, self.strategy.symbol, px)
                    self._push('Backup stop sold', f'{self.broker_name.upper()} algo: the backup stop at the broker sold {n} '
                               f'{self.strategy.symbol} at {px:g}.', 'rotating_light')
                elif not b.get('cancelling'):
                    self._bstop_failed_at = now          # refused, or cancelled by SELL ALL / in the broker app
                    self._note_order(b['side'], b['shares'], 'stop gone', detail='the backup stop at the broker was cancelled '
                                     'or refused outside the algo; it is placed again in 5 min')
                    log.warning('backup stop cancelled or refused outside the algo')
                self.bstop = b = None
                self._bstop_save()
                self.save()
                pos = self.portfolio.position
        # 2. an exit waiting for the stop to go
        if wait is not None:
            if pos is None:
                self._bstop_wait_exit = None
            elif b is None:
                self._bstop_wait_exit = None
                sess, ok = self.session_now()
                price = self.last_price or (float(self.bars['close'].iloc[-1]) if len(self.bars) else None)
                if ok and price and not self.busy():
                    self._exit(pd.Timestamp.now(tz='UTC'), price, wait[0], sess)
                return
        # 3. cancel a stop that no longer fits (exit waiting, flat, or a different share count)
        if b is not None:
            if b.get('cancelling') and now - b.get('cancel_at', 0) < 30:
                return
            fits = (self.stop_api or {}).get('fits')      # Webull: a night-session stop overnight, a day one otherwise
            if wait is not None or pos is None or b['shares'] != pos.shares or (fits is not None and not fits(b)):
                try:
                    self.stop_api['cancel'](b)
                    b['cancelling'], b['cancel_at'] = True, now
                    self._bstop_checked = 0.0
                    self._bstop_save()
                except Exception as e:
                    log.warning('could not cancel the backup stop: %r', e)
            return
        # 4. place one when a position is open and none is there
        pct = self.bstop_pct()
        if (pos is None or pct <= 0 or self.killed or self.pending or getattr(self, 'safe_mode', None)
                or now - self._bstop_failed_at < BSTOP_RETRY_S):
            return
        long_ = pos.side == 'long'
        stop = pos.entry_price * (1 - pct / 100.0 if long_ else 1 + pct / 100.0)
        stop = round(stop, 2 if stop >= 1 else 4)
        if self.last_price and ((long_ and self.last_price <= stop) or (not long_ and self.last_price >= stop)):
            return                                   # already past it: the algo's own stop-loss handles this
        side = 'sell' if long_ else 'buy'
        try:
            h = self.stop_api['place'](side, pos.shares, stop)
        except Exception as e:
            self._bstop_failed_at = now
            self._note_order(side, pos.shares, 'stop not placed', price=stop, detail=f'backup stop at the broker: {_describe(e)}'[:200])
            log.error('backup stop NOT placed: %s', _describe(e))
            if self.__dict__.get('_bstop_pushed') == pos.entry_time:
                return                                   # one push per position, not one every 5 minutes
            self._bstop_pushed = pos.entry_time
            self._push('Backup stop not placed', f'{self.broker_name.upper()} algo ({self.strategy.symbol}): the broker refused '
                       f'the backup stop ({_describe(e)[:120]}). The algo\'s own stop-loss still works; it tries again in 5 min.')
            return
        self.bstop = dict(h or {}, symbol=self.strategy.symbol, side=side, shares=pos.shares, stop=stop,
                          placed_at=_dt.datetime.now(_dt.timezone.utc).isoformat())
        self._bstop_checked = now
        self._bstop_save()
        self._note_order(side, pos.shares, 'stop placed', price=stop, detail=f'backup stop at the broker, {pct:g}% below the buy'
                         + (f" ({self.stop_api['hours']})" if self.stop_api.get('hours') else ''))
        log.warning('backup stop placed at the broker: %s %s %s stop %s', side, pos.shares, self.strategy.symbol, stop)
        self.write_status()

    # ------------------------------------------------------------ final ideas 24, 25, 36-38 (jb-guardian)
    # heartbeat file for the board's "algo stopped checking in" push; skips new entries while IBKR and
    # Webull disagree on this symbol's price; safe mode while the connection is lost (resumes by itself)
    # and an order speed limit / duplicate guard, in paper too so the simulation behaves like live;
    # live mode also checks the position against the broker every minute.
    SPEED_PER_MIN = 3
    SPEED_PER_DAY = 30

    def _guardian_tick(self):
        now = time.time()
        g = self.__dict__.setdefault('_g', {'hb': 0.0, 'doubt': 0.0, 'pos': 0.0, 'miss': 0, 'was_conn': None, 'conn_since': 0.0})
        logs = os.path.dirname(self.kill_path)
        if now - g['hb'] >= 30:
            g['hb'] = now
            try:
                atomic_write_json(os.path.join(logs, 'heartbeat.json'), {'at': _dt.datetime.now(_dt.timezone.utc).isoformat(), 'ts': now,
                                                                         'mode': self.mode, 'connected': self.connected, 'symbol': self.strategy.symbol})
            except Exception:
                pass
        if now - g['doubt'] >= 15:
            g['doubt'] = now
            self.price_doubt = None
            try:
                p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(logs))), 'data', 'price_disagree.json')
                d = (json.load(open(p)) or {}).get(self.strategy.symbol) if os.path.exists(p) else None
                if d and now * 1000 - float(d.get('at') or 0) < 90000:
                    self.price_doubt = f"IBKR {d.get('ibkr')} vs Webull {d.get('webull')} ({d.get('diff')}% apart)"
            except Exception:
                pass
        if g['was_conn'] is not None and self.connected != g['was_conn']:
            if not self.connected and not getattr(self, 'safe_mode', None):
                self.safe_mode = 'connection to the broker lost at ' + _dt.datetime.now().strftime('%H:%M')
                g['lost_at'] = now
            if self.connected:
                g['conn_since'] = now
        g['was_conn'] = self.connected
        if self.mode != 'live':                # paper: no broker position to check; resume once connected a minute
            sm = getattr(self, 'safe_mode', None)
            if sm and self.connected and now - g.get('conn_since', 0) >= 60:
                self.safe_mode = None
                log.warning('paper safe mode over (%s)', sm)
                g.pop('lost_at', None)
                self.write_status()
            return
        if not self.connected or self.pending or now - g['pos'] < 60:
            return
        g['pos'] = now
        b = self._broker_shares()
        if b is None:
            return
        mine = self.ledger_shares()
        if int(round(b)) != mine:
            g['miss'] += 1
            if g['miss'] >= 2 and not (getattr(self, 'safe_mode', None) or '').startswith('position'):
                self.safe_mode = f'position check: the broker says {b:g} {self.strategy.symbol}, the algo says {mine}'
                log.error('SAFE MODE: %s', self.safe_mode)
                self._push('Algo paused: positions differ', f'{self.broker_name} algo ({self.strategy.symbol}): {self.safe_mode}. '
                           'It sends nothing until they match again (check the broker app).', 'rotating_light')
                self.write_status()
            return
        g['miss'] = 0
        sm = getattr(self, 'safe_mode', None)
        if sm and now - g.get('conn_since', 0) >= 60:
            self.safe_mode = None
            log.warning('safe mode over (%s); positions match', sm)
            down = f" (down {int((now - g['lost_at']) / 60)} min)" if g.get('lost_at') and sm.startswith('connection') else ''
            self._push('Algo resumed', f'{self.broker_name} algo ({self.strategy.symbol}): {sm}{down}. Connection is back and the '
                       f'position matches the broker ({mine} shares), so it is trading again.', 'white_check_mark')
            g.pop('lost_at', None)
            self.write_status()

    def _doubt(self):
        return bool(getattr(self, 'price_doubt', None))

    def _guardian_block(self, spec):
        """Why a live order must not go out now (safe mode, speed limit, duplicate), or None."""
        sm = getattr(self, 'safe_mode', None)
        if sm:
            return 'safe mode: ' + sm
        now = time.time()
        sent = [x for x in self.__dict__.setdefault('_sent', []) if now - x[0] < 86400]
        self._sent = sent
        key = spec.get('dup_key', spec['side'])        # hedge orders (jb-hedge) only match other hedge orders
        same = [x for x in sent if now - x[0] < 30 and x[1] == key and x[2] == spec['shares']]
        why = ''
        if same:
            why = f"the same order ({spec['side']} {spec['shares']}) was sent {int(now - same[-1][0])} s ago"
        elif sum(1 for x in sent if now - x[0] < 60) >= self.SPEED_PER_MIN:
            why = f'{self.SPEED_PER_MIN} orders in the last minute'
        elif len(sent) >= self.SPEED_PER_DAY and not spec.get('closing'):
            why = f'{self.SPEED_PER_DAY} orders in the last 24 hours'
        if why and spec.get('closing'):
            # an exit that did not fill (e.g. the trailing stop's limit was not reached) is simply sent again a
            # little later: never pause the algo while it still holds the position (jb-ready)
            return 'waiting before sending the exit again: ' + why
        if why:
            if self.mode == 'live':
                self.halted = f'order speed limit: {why}. Live trading paused -- check the algo, then restart ./jb live.'
            else:
                self.halted = f'order speed limit: {why}. Paper trading paused -- check the algo, then press Restart for it on the Health tab.'
            log.error('%s TRADING PAUSED: %s', self.mode.upper(), self.halted)
            self._push(('Paper algo' if self.mode != 'live' else 'Algo') + ' paused: too many orders', f'{self.broker_name} algo ({self.strategy.symbol}): {why}. Nothing more is sent until you restart it.',
                       'rotating_light' if self.mode == 'live' else 'warning')
            return self.halted
        sent.append((now, key, spec['shares']))
        return None

    def _paper_blocked(self, spec):
        """Paper runs the same order checks as live (paused, safe mode, speed limit, duplicate)."""
        # like live: a pause (speed limit, rejections) stops BUYS; exits still go out (review 2026-10-11, second check)
        why = (None if spec.get('closing') else self.halted) or self._guardian_block(spec)
        if why:
            self._note_order(spec['side'], spec['shares'], 'blocked', detail=('paper: ' + why)[:200])
            return True
        return False

    def _push(self, title, text, tags='warning'):
        topic = os.environ.get('JB_NTFY_TOPIC', '').strip()
        if not topic:
            return
        try:
            import urllib.request
            pri = '5' if tags == 'rotating_light' else '3'
            req = urllib.request.Request('https://ntfy.sh/' + topic, data=text.encode('utf-8'), method='POST',
                                         headers={'Title': title.encode('ascii', 'ignore').decode(), 'Tags': tags, 'Priority': pri})
            try:
                import ssl, certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = None
            urllib.request.urlopen(req, timeout=10, context=ctx).read()
        except Exception as e:
            log.warning('push failed: %r', e)

    def ledger_shares(self):
        """The ledger's position as a signed share count (+ long, - short, 0 flat)."""
        pos = self.portfolio.position
        if pos is None:
            return 0
        return pos.shares if pos.side == 'long' else -pos.shares

    def check_matches_broker(self, broker_shares, broker_name):
        """Live start-up: refuse to trade if the broker's real position differs from the ledger."""
        mine = self.ledger_shares()
        if int(round(broker_shares)) == mine:
            return
        sym = self.strategy.symbol
        raise SystemExit(
            f"\nLIVE TRADING NOT STARTED: your {broker_name} account holds {broker_shares:g} shares of {sym}, "
            f"but this algo's live record says {mine}.\n"
            f"  * If you hold {sym} yourself (outside the algo): use a different symbol or account -- the algo "
            f"would otherwise trade those shares.\n"
            f"  * If you closed or changed the algo's position yourself, or it stopped while an order was in "
            f"flight: make the account hold no {sym}, then clear the algo's live record with\n"
            f"        ./jb live-reset {broker_name.lower()} {self.strategy.source_path}\n")

    def _note_order(self, side, shares, status, price=None, detail=''):
        self.last_order = {'time': _dt.datetime.now(_dt.timezone.utc).isoformat(), 'side': side,
                           'shares': shares, 'status': status, 'price': price, 'detail': detail}

    # ------------------------------------------------------------ output
    def save(self):
        try:
            self.state.save(self.portfolio)
        except Exception as e:
            log.warning('could not save state: %r', e)
        self.write_status()

    def write_status(self):
        if not self.status_path:
            return
        pf, price = self.portfolio, self.last_price
        if price is None and len(self.bars):
            price = float(self.bars['close'].iloc[-1])      # before the first tick: last bar's close
        pos = pf.position
        sess, ok = self.session_now()
        po = self.paper_order
        snap = {
            'strategy_name': self.strategy.name, 'symbol': self.strategy.symbol, 'mode': self.mode,
            'broker': self.broker_name, 'connected': self.connected, 'last_error': self.last_error,
            'last_price': price, 'starting_cash': pf.starting_cash, 'cash': pf.cash,
            'position': None if pos is None else {'side': pos.side, 'shares': pos.shares,
                                                  'entry_price': pos.entry_price, 'entry_time': pos.entry_time,
                                                  'peak': pos.peak},
            'equity': pf.equity(price) if price is not None else pf.cash,
            'unrealized_pnl': pf.unrealized_pnl(price) if price is not None else 0.0,
            'trades': [{'time': t.time, 'side': t.side, 'shares': t.shares, 'price': t.price, 'reason': t.reason}
                       for t in pf.trades[-50:]],
            'equity_curve': [[t, e] for t, e in pf.equity_curve[-400:]],
            'last_order': self.last_order, 'pending_order': self.pending,
            'paper_order': None if po is None else {k: po['spec'][k] for k in ('side', 'shares', 'limit', 'session')},
            'restored': self.restored, 'halted': self.halted, 'killed': self.killed,
            'broker_stop': self.bstop,
            'hedge': self.hedge.status() if getattr(self, 'hedge', None) else None,
            'buying_power': self.buying_power if time.time() - self.buying_power_at < 300 else None,
            'safe_mode': getattr(self, 'safe_mode', None), 'price_doubt': getattr(self, 'price_doubt', None),
            'broker_notes': list(getattr(self, 'broker_notes', None) or []),
            'round_trips': round_trips(pf.trades)[-300:],
            'options': self.opts, 'options_source': self.opts_source, 'options_problem': self.opts_problem,
            'session_now': sess, 'trading_now': ok, 'allowed_sessions': sorted(self.allowed_sessions()),
            'updated_at': _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        try:
            atomic_write_json(self.status_path, snap)
        except Exception as e:
            log.warning('write_status failed: %r', e)


def round_trips(trades):
    """Fills -> one line per finished trade (entry to exit), for the dashboard's daily report.
    A partly closed position gives one line per exit."""
    out, open_ = [], None
    for t in trades:
        if t.reason == 'entry':
            open_ = {'side': 'long' if t.side == 'buy' else 'short', 'time': t.time, 'price': t.price}
            continue
        if open_ is None:
            continue
        sign = 1 if open_['side'] == 'long' else -1
        pnl = sign * (t.price - open_['price']) * t.shares
        ent, ext = pd.Timestamp(open_['time']), pd.Timestamp(t.time)
        out.append({'side': open_['side'], 'shares': t.shares, 'entry_time': ent.isoformat(), 'entry_price': open_['price'],
                    'exit_time': ext.isoformat(), 'exit_price': t.price, 'reason': t.reason,
                    'entry_session': _sess(ent), 'exit_session': _sess(ext), 'pnl': round(pnl, 2),
                    'pnl_pct': round(sign * (t.price / open_['price'] - 1) * 100, 3)})
    return out


def _sess(ts):
    try:
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        return session_at(ts.to_pydatetime())
    except Exception:
        return None


def _describe(e):
    """'ServerException: HTTP Status: 400, Code: ..., Msg: ...' rather than 'ServerException()'."""
    text = str(e).strip()
    return f'{type(e).__name__}: {text}' if text else repr(e)


def default_status_path(strategy_path):
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(strategy_path))), 'logs', 'live_status.json')


def default_options_path(strategy_path):
    """<algo folder>/trading_options.json -- written by the dashboard's Trading options panel."""
    return opt.path_for(os.path.dirname(os.path.dirname(os.path.abspath(strategy_path))))
