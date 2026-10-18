"""Webull paper/live trader: streams the strategy's symbol from Webull, builds 1-minute bars,
runs the strategy (algo/core_trader.py), and in live mode sends real orders through
live_broker.py. Paper mode never sends anything to Webull's trading API.

Run from the webull-algo folder (./jb does this for you):
    python -m algo.paper_trader --strategy strategies/rsi_reversal.yaml --mode paper
It must be started with "-m algo.paper_trader" (not "python algo/paper_trader.py"): it is
part of the algo package and imports its sibling modules as such.

Paper trading state survives restarts (logs/state_<strategy>_paper.json); the dashboard's
Webull Algo tab reads logs/live_status.json.
"""
import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

import pandas as pd

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(PROJECT), 'lib'))
import jb_webull  # noqa: E402

from . import autohedge  # noqa: E402
from . import guards
from . import options as opt  # noqa: E402
from .core_trader import TraderCore, default_options_path, default_status_path  # noqa: E402
from .strategy import Strategy  # noqa: E402

log = logging.getLogger('paper_trader')

# no price for the symbol this long in that session: prices stopped -- reconnect, and push the phone once per outage when the
# algo may trade then or holds a position (the same wait times as the IBKR algo; jb-parity)
STALL_S = {'regular': 300, 'premarket': 900, 'afterhours': 900, 'overnight': 1800}
NO_SHORT_SESSIONS = {'overnight'}     # Webull takes no short sales in its overnight session (8 pm - 4 am ET)
SHORTS_NOTE = ('Short Sales (Entry Side: Sell): Live At Webull Only On A Margin Account, Never Overnight (Webull Takes No '
               'Short Sales Then; Paper And Backtests Skip Them Too). Same Checks As Buys.')

NO_OVERNIGHT_NOTE = ('no overnight prices: your Webull plan does not include the overnight session '
                     '(8 pm - 4 am ET), so the algo does not trade overnight (checked again every 3 hours)')
OVN_ON_NOTE = ('Overnight Prices (Webull): On -- The Algo Trades 8 pm - 4 am ET When Its Trading Options Allow')
OVN_OFF_NOTE = ('Overnight Prices (Webull): Off -- Your Plan Has No Overnight Session; Checked Again Every 3 Hours And At '
                '7:45 pm ET, So It Turns On By Itself Once The Plan Has It')


BSTOP_HOURS = {'ALL': 'set for all sessions; check in the Webull app that it covers pre/after hours',
               'CORE': 'works 9:30 am - 4 pm only, not before or after hours or overnight',
               'NIGHT': 'the overnight one (8 pm - 3:50 am ET, Webull took it); a day one again from 3:50 am',
               'NIGHT_REFUSED': 'works 4 am - 8 pm; Webull refused a stop order overnight (tried again in a week)'}
NIGHT_STOP_RETRY_S = 7 * 86400   # Webull refused an overnight (NIGHT) backup stop: not asked again for a week


def overnight_refused(e):
    """Webull said no only because this plan lacks the overnight session
    (403 MARKET_DATA_NOT_SUBSCRIBED ... please subscribe to US_STOCK OVERNIGHT)."""
    s = f'{jb_webull.describe_error(e)} {e}'.upper()
    return ('MARKET_DATA_NOT_SUBSCRIBED' in s or 'INSUFFICIENT PERMISSION' in s) and 'OVERNIGHT' in s


class WebullTrader:
    def __init__(self, strategy, args):
        if strategy.symbol.endswith('=F'):
            raise SystemExit('webull-algo trades stocks/ETFs only (the strategy symbol is a future).')
        self.args = args
        self.core = TraderCore(strategy, args.mode, logs_dir=os.path.dirname(args.status_path or default_status_path(args.strategy)),
                               starting_cash=args.starting_cash, commission_per_share=args.commission_per_share,
                               slippage_pct=args.slippage_pct, max_order_quantity=args.max_order_quantity,
                               max_order_notional_usd=args.max_order_notional_usd,
                               status_path=args.status_path, broker_name='webull',
                               options_path=args.options_path)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.data_client = None
        self.broker = None
        self.session_ok = False
        self.overnight = True         # ask for overnight prices too (off if the plan lacks them)
        self._bs_session = 'ALL'      # backup stop: all sessions Webull allows, else 'CORE' (review 2026-10-11 #5)
        self.client = None            # the current streaming client
        self.alive_at = 0.0           # last price, or when the stream connected
        self.hedge_sym = None         # Auto-Hedge fund streamed (jb-hedge; Webull: funds only, no futures)
        self.stall_pushed = False     # the 'prices stopped' push went out for this outage (once per outage)
        self.preempted = False        # Webull notice 1002: another session took over this market data
        self.core.no_short_sessions = set(NO_SHORT_SESSIONS)
        self.overnight_checked = time.time()  # when the plan was last checked for overnight prices (start-up = a check)
        self.overnight_known = None           # True / False once Webull answered a subscription asking for overnight
        self.caps_path = os.path.join(os.path.dirname(self.core.kill_path), 'webull_caps.json')   # what Webull refused
        self._notes()

    def _notes(self):
        """The algo tab's note line (core.broker_notes): short sales, and whether overnight prices are on right now."""
        notes = [SHORTS_NOTE] if self.core.strategy.entry_side == 'sell' else []
        if self.overnight_known is True:
            notes.append(OVN_ON_NOTE)
        elif self.overnight_known is False:
            notes.append(OVN_OFF_NOTE)
        self.core.broker_notes = notes

    def _drop_overnight(self):
        """The plan has no overnight prices: never trade overnight (whatever the trading options
        say), since the algo would be acting on prices hours old. Checked again every 3 hours and at 7:45 pm ET
        (_overnight_probe): once the plan has it, overnight comes back by itself (hedge-2026-10-12)."""
        self.overnight = False
        self.overnight_known = False
        self.overnight_checked = time.time()
        core = self.core
        core.allowed_sessions = lambda: TraderCore.allowed_sessions(core) - {'overnight'}
        self._notes()
        jb_webull.note_overnight('algo', False, 'the Webull plan has no overnight session')
        log.warning('Webull has no overnight-session data on this plan: streaming without overnight '
                    'prices and not trading overnight (checked again every 3 hours and at 7:45 pm ET)')

    def _restore_overnight(self):
        """The plan has overnight prices now: the trading options decide about overnight again."""
        self.overnight = True
        self.overnight_known = True
        self.core.__dict__.pop('allowed_sessions', None)       # the class's own allowed_sessions again
        if self.core.last_error == NO_OVERNIGHT_NOTE:
            self.core.last_error = None
        self._notes()
        jb_webull.note_overnight('algo', True, 'the overnight session is now on the Webull plan')
        log.warning('Webull now streams the overnight session: the algo trades overnight again when its trading options allow')

    def _overnight_probe(self):
        """Webull overnight ready: one subscription of the strategy's symbol that asks for the overnight session, on the
        current stream. Taken: overnight on again, and the stream reconnects so the hedge fund gets it too. Refused:
        nothing changes (asked again in 3 hours, or at 7:45 pm ET)."""
        from webull.data.common.category import Category
        from webull.data.common.subscribe_type import SubscribeType
        c = self.client
        self.overnight_checked = time.time()
        if c is None:
            return
        try:
            c.subscribe([self.core.strategy.symbol], Category.US_STOCK.name, [SubscribeType.TICK.name], overnight_required=True)
        except Exception as e:
            if overnight_refused(e):
                log.info('overnight re-check: still no overnight session on this Webull plan')
                jb_webull.note_overnight('algo', False, 'the Webull plan has no overnight session (checked again)')
            else:
                log.warning('overnight re-check failed (%s); trying again later', jb_webull.describe_error(e))
            return
        with self.lock:
            self._restore_overnight()
            self.core.write_status()
        try:
            c._thread_terminate = True        # reconnect: every subscription again, with the overnight session
            c.disconnect()
        except Exception as e:
            log.warning('disconnect failed: %r', e)

    # ---------------- setup ----------------
    def connect_api(self):
        delay = 60
        while True:
            try:
                _, self.data_client, trade = jb_webull.make_clients(
                    self.args.app_key, self.args.app_secret, self.args.region, self.args.environment,
                    trade=self.args.mode == 'live', interactive=self.args.mode == 'live')
                break
            except jb_webull.NeedsApproval:
                log.error(jb_webull.AUTH_HELP)
                with self.lock:
                    self.core.last_error = jb_webull.AUTH_HELP
                    self.core.write_status()
                if not jb_webull.wait_for_approval(self.stop):
                    return False
                log.info('Webull approval found -- connecting')
            except Exception as e:
                msg = jb_webull.AUTH_HELP if jb_webull.is_auth_problem(e) else f'Webull client failed: {jb_webull.describe_error(e)}'
                log.error('%s -- retrying in %d min', msg, delay // 60)
                with self.lock:
                    self.core.last_error = msg
                    self.core.write_status()
                if self.stop.wait(delay):
                    return False
                delay = min(delay * 2, 3600)
        if self.args.mode == 'live':
            from .live_broker import LiveBroker
            self.broker = LiveBroker(trade)
            self.core.broker_position = lambda: self.broker.position(self.core.strategy.symbol)   # kill switch checks it first
            try:
                acct = self.broker.account_id()      # know which account BEFORE the first signal
                self.broker.verify_account()         # WEBULL_ACCOUNT_ID must be one of the key's accounts (jb-parity)
                held = self.broker.position(self.core.strategy.symbol)
            except Exception as e:
                raise SystemExit(f'\nLIVE TRADING NOT STARTED: {jb_webull.describe_error(e)}\n')
            if self.core.strategy.entry_side == 'sell':
                self._check_shorts(acct)
            listed = True
            try:
                all_open = self.broker.open_orders()
            except Exception as e:
                all_open, listed = [], False
                log.warning('could not list open Webull orders (%s) -- check the Webull app for any '
                            'open %s order', jb_webull.describe_error(e), self.core.strategy.symbol)
            sym = self.core.strategy.symbol.upper()
            still_open = [o for o in all_open if str(o.get('symbol') or '').upper() == sym]
            # the algo's own backup stop (jb-ready) is not "an order left open by someone"
            bid = (self.core.bstop or {}).get('id')
            stops = [o for o in still_open if bid and str(o.get('client_order_id') or '') == bid]
            still_open = [o for o in still_open if o not in stops]
            if stops and self.core.portfolio.position is None:
                try:
                    self.broker.trade.order_v3.cancel_order(account_id=acct, client_order_id=bid)
                    log.warning('cancelled a leftover backup stop at Webull (the algo holds nothing)')
                except Exception as e:
                    log.warning('could not cancel the leftover backup stop (%s) -- cancel it in the Webull app',
                                jb_webull.describe_error(e))
                self.core.bstop = None
                self.core._bstop_save()
            elif not stops and self.core.bstop is not None and listed:
                if not self.core.bstop_filled_while_away(held):
                    self.core.bstop = None                # gone (cancelled in the app or by SELL ALL): placed again
                    self.core._bstop_save()
            if still_open:
                raise SystemExit(f'\nLIVE TRADING NOT STARTED: an order for {self.core.strategy.symbol} is still open '
                                 f'at Webull. Cancel it in the Webull app (or let it finish), then start again.\n')
            # Auto-Hedge (jb-hedge, Webull leftover): an Auto-Hedge order still open from an earlier run stops the start, as at
            # IBKR (orderRef jb-hedge); one that filled while the algo was off is adopted below
            lookups = self._hedge_leftovers(all_open)
            log.warning('live orders go to Webull account %s', acct)
            self.core.check_matches_broker(held, 'Webull')
            log.warning('Webull position in %s matches the algo\'s record (%d shares)',
                        self.core.strategy.symbol, self.core.ledger_shares())
            self.core.place_live = self._place_live
            # Auto-Hedge (jb-hedge): its orders and the broker's count of it; at start-up its record vs the account
            h = self.core.hedge
            self.core.hedge.place = self._place_hedge
            self.core.hedge.position = lambda: (self.broker.position(h.position_symbol()) if h.position_symbol() else None)
            if h.units() or h.cfg.get('on') or lookups:
                try:
                    h.startup(lookups=lookups)
                except Exception as e:
                    log.warning('could not read the Auto-Hedge position (%s) -- check it in the Webull app', jb_webull.describe_error(e))
            self.core.stop_api = {'place': self._bs_place, 'cancel': self._bs_cancel, 'status': self._bs_status,
                                  'listed': self._bs_listed, 'fits': self._bs_fits,
                                  'hours': BSTOP_HOURS[getattr(self, '_bs_session', 'ALL')]}
        return True

    def _check_shorts(self, acct):
        """Live start of a short-selling strategy (jb-parity): Webull's API has a SHORT order side, and the algo sends it
        only for an entry while flat. Refuses unless the account is a margin account; then asks Webull to preview a
        1-share short sale (never placed) and refuses if Webull says no. A preview that can't be made is only noted:
        Webull's own check on the real order still applies, and a refusal there is handled like any rejected order."""
        sym = self.core.strategy.symbol
        try:
            kind = self.broker.account_kind()
        except Exception as e:
            raise SystemExit(f'\nLIVE TRADING NOT STARTED: this strategy sells short (entry side: sell), and the Webull account '
                             f'type could not be read ({jb_webull.describe_error(e)[:120]}). Try again.\n')
        if 'MARGIN' not in kind:
            raise SystemExit(f'\nLIVE TRADING NOT STARTED: this strategy sells short (entry side: sell), but Webull account '
                             f'{acct} is not a margin account (Webull says: {kind or "no account type"}). Short sales need a '
                             f'margin account. Paper trading and backtests still work.\n')
        price = None
        try:
            rows = jb_webull.fetch_bars(self.data_client, sym, 'M1', 5)
            price = rows[-1]['close'] if rows else None
        except Exception:
            pass
        ok, why = self.broker.preview_short(sym, price)
        if ok is False:
            raise SystemExit(f'\nLIVE TRADING NOT STARTED: Webull does not take a short sale of {sym} on account {acct} '
                             f'({why[:200]}). Paper trading and backtests still work.\n')
        log.warning('short sales of %s: margin account %s; Webull\'s preview %s', sym, acct,
                    'accepted a 1-share short sale' if ok else f'could not be made ({why[:120]})')

    def _hedge_leftovers(self, all_open):
        """Live start-up (jb-hedge, Webull leftover): refuses to start while an Auto-Hedge order is still open at Webull --
        found by its jbhedge... client order id, by the ids this algo wrote down, or as an order for a hedge fund (the
        open hedge, the chosen one, those of unanswered hedge orders). Returns what Webull says about the hedge orders the
        algo sent without a final answer ({client order id: (status, filled, avg) or None}), for hedge.startup()."""
        from .live_broker import FINAL_BAD, _not_found
        h = self.core.hedge
        mine = {o['id']: o for o in h.open_orders()}
        syms = h.watch_symbols()
        hits = []
        for o in all_open:
            coid = str(o.get('client_order_id') or '')
            osym = str(o.get('symbol') or '').upper()
            if coid.startswith(autohedge.HEDGE_TAG) or coid in mine or osym in syms:
                hits.append(f"{str(o.get('side') or '').upper()} {o.get('quantity') or o.get('total_quantity') or ''} {osym}"
                            + (f' (client order id {coid})' if coid else ''))
        lookups = {}
        for coid, rec in mine.items():
            try:
                st = self.broker.order_state(coid)
            except Exception as e:
                if not _not_found(e):
                    log.warning('could not look up the Auto-Hedge order %s (%s) -- check %s in the Webull app', coid,
                                jb_webull.describe_error(e), rec.get('symbol'))
                    continue
                st = None
            lookups[coid] = st
            if st is not None and st[0] != 'FILLED' and st[0] not in FINAL_BAD and not any(coid in x for x in hits):
                hits.append(f"{str(rec.get('side') or '').upper()} {rec.get('units')} {rec.get('symbol')} (client order id {coid})")
        if hits:
            raise SystemExit('\nLIVE TRADING NOT STARTED: an Auto-Hedge order is still open at Webull: ' + '; '.join(hits[:4]) +
                             '. Cancel it in the Webull app (or let it finish), then start again. (An order for the hedge fund '
                             'you placed yourself counts too: the algo can\'t tell it from a leftover hedge order.)\n')
        return lookups

    # ---------------- backup stop at Webull (jb-ready) ----------------
    def _night_stop_ok(self):
        """May an overnight (NIGHT session) backup stop be tried? Not within a week of Webull refusing one."""
        at = getattr(self, '_night_refused_at', 0.0)
        try:
            with open(self.caps_path) as f:
                at = max(at, float(json.load(f).get('night_stop_refused_at') or 0))
        except Exception:
            pass
        return time.time() - at >= NIGHT_STOP_RETRY_S

    def _bs_want_night(self):
        now = getattr(self.core, 'session_now', None)
        return bool(now) and now()[0] == 'overnight' and self._night_stop_ok()

    def _bs_fits(self, b):
        """Does the backup stop at Webull cover the session now (hedge-2026-10-12, Webull overnight ready)? A NIGHT stop
        only overnight (8 pm - 3:50 am); a day stop always, except overnight while Webull may take a NIGHT one. A stop
        that does not fit is cancelled by the core and placed again for the session now -- never two at once."""
        if b.get('session') == 'NIGHT':
            return self.core.session_now()[0] == 'overnight'
        return not self._bs_want_night()

    def _bs_place(self, side, shares, stop):
        import uuid
        from .live_broker import FINAL_BAD, _decimal, _not_found
        unsure = getattr(self, '_bs_unsure', None)
        if unsure:
            # an earlier stop's send errored without a clear answer: it may be at Webull, so look for it first
            try:
                st = self.broker._order_state(unsure)
            except Exception as e:
                if not _not_found(e):
                    raise RuntimeError(f'could not look up the backup stop sent earlier ({jb_webull.describe_error(e)[:80]}), '
                                       'so no other one was placed')
                st = None
            self._bs_unsure = None
            if st is not None and st[0] not in FINAL_BAD:
                log.warning('the backup stop sent earlier did reach Webull (%s) -- keeping it', st[0])
                return {'id': unsure, 'session': getattr(self, '_bs_unsure_session', None) or getattr(self, '_bs_session', 'ALL')}
        # overnight (hedge-2026-10-12): Webull's night session, if it takes stop orders then (day orders only overnight)
        night = self._bs_want_night()
        sess = 'NIGHT' if night else getattr(self, '_bs_session', 'ALL')
        coid = uuid.uuid4().hex[:32]
        order = {'combo_type': 'NORMAL', 'client_order_id': coid, 'instrument_type': 'EQUITY', 'market': 'US',
                 'symbol': self.core.strategy.symbol, 'side': side.upper(), 'quantity': str(int(shares)),
                 'time_in_force': 'DAY' if night else 'GTC', 'entrust_type': 'QTY', 'support_trading_session': sess,
                 'order_type': 'STOP_LOSS', 'stop_price': _decimal(stop)}
        log.warning('PLACING BACKUP STOP: %s', order)
        try:
            self.broker.trade.order_v3.place_order(account_id=self.broker.account_id(), new_orders=[order])
        except Exception as e:
            # review 2026-10-11 #5: ask for a stop that also works before and after hours ('ALL'); if Webull
            # refuses that kind, fall back to the regular-session stop it accepted before
            if self._bs_find_coid(coid):
                log.warning('the backup stop call errored but Webull has the order -- keeping it (review 2026-10-11 #7)')
                return {'id': coid, 'session': sess}
            try:
                status = int(getattr(e, 'http_status', None))
            except (TypeError, ValueError):
                status = None
            if status is None or not 400 <= status < 500 or status == 429:
                self._bs_unsure, self._bs_unsure_session = coid, sess   # no clear "no": it may still be at Webull
                raise
            if night:
                # Webull takes no stop order overnight: a day stop instead (it works from 4 am), and no NIGHT one for a week
                log.warning('Webull refused an overnight backup stop (%s) -- placing a day one instead', jb_webull.describe_error(e))
                self._night_refused_at = time.time()
                try:
                    from .runstate import atomic_write_json
                    atomic_write_json(self.caps_path, {'night_stop_refused_at': time.time(), 'why': jb_webull.describe_error(e)[:200]})
                except Exception as e2:
                    log.warning('could not save that: %r', e2)
                if getattr(self.core, 'stop_api', None):
                    self.core.stop_api['hours'] = BSTOP_HOURS['NIGHT_REFUSED']
                return self._bs_place(side, shares, stop)
            if getattr(self, '_bs_session', 'ALL') != 'ALL':
                raise
            log.warning('Webull refused an all-sessions backup stop (%s) -- using a regular-hours one',
                        jb_webull.describe_error(e))
            self._bs_session = 'CORE'
            if getattr(self.core, 'stop_api', None):
                self.core.stop_api['hours'] = BSTOP_HOURS['CORE']
            return self._bs_place(side, shares, stop)
        if getattr(self.core, 'stop_api', None):
            self.core.stop_api['hours'] = BSTOP_HOURS['NIGHT' if night else ('ALL' if sess == 'ALL' and self._night_stop_ok() else
                                                                            'NIGHT_REFUSED' if sess == 'ALL' else 'CORE')]
        return {'id': coid, 'session': sess}

    def _bs_find_coid(self, coid):
        """True when Webull knows this order (it was placed even though the call errored)."""
        try:
            return self.broker._order_state(coid) is not None
        except Exception:
            return False

    def _bs_status(self, b):
        from .live_broker import FINAL_BAD
        st = self.broker._order_state(b['id'])
        if st is None:
            return 'unknown', 0, None
        status, filled, avg = st
        if status == 'FILLED':
            return 'filled', filled, avg
        if status in FINAL_BAD:
            return 'cancelled', filled, avg
        return 'working', filled, avg

    def _bs_listed(self, b):
        """Is the backup stop among Webull's open orders? None if the list can't be read in full."""
        try:
            rows = self.broker.open_orders_for(self.core.strategy.symbol)
        except Exception:
            return None
        return any(str(o.get('client_order_id') or '') == str(b.get('id')) for o in rows)

    def _bs_cancel(self, b):
        self.broker.trade.order_v3.cancel_order(account_id=self.broker.account_id(), client_order_id=b['id'])

    def _place_live(self, spec, filled, failed, unconfirmed, unfilled):
        a = self.args
        if spec['side'] == 'sell' and not spec.get('closing') and spec['session'] in NO_SHORT_SESSIONS:
            raise guards.GuardRejected('Webull takes no short sales overnight')
        # returns at once; a helper thread follows the order and calls back (under self.lock)
        self.broker.place_order(self.core.strategy, spec, self.core.strategy.symbol, filled, failed, unconfirmed,
                                unfilled, lock=self.lock, max_order_quantity=a.max_order_quantity,
                                max_order_notional_usd=a.max_order_notional_usd)

    def _place_hedge(self, spec, filled, failed, unconfirmed, unfilled):
        a = self.args
        sym = spec['symbol']
        if sym in autohedge.FUTURES:
            raise guards.GuardRejected('futures hedges are IBKR only')
        self.broker.place_order(autohedge._As(self.core.strategy, sym), spec, sym, filled, failed, unconfirmed, unfilled,
                                lock=self.lock, max_order_quantity=a.max_order_quantity,
                                max_order_notional_usd=a.max_order_notional_usd)

    def _hedge_subscribe(self, client=None):
        """Stream the Auto-Hedge fund on the current client (jb-hedge); futures are never streamed from Webull."""
        c = client or self.client
        want = self.core.hedge.feed_symbol()
        if want in autohedge.FUTURES:
            want = None
        if c is None or want == self.hedge_sym:
            return
        from webull.data.common.category import Category
        from webull.data.common.subscribe_type import SubscribeType
        old, self.hedge_sym = self.hedge_sym, None
        try:
            if old:
                c.unsubscribe(symbols=[old], category=Category.US_STOCK.name, sub_types=[SubscribeType.TICK.name])
            if want:
                c.subscribe([want], Category.US_STOCK.name, [SubscribeType.TICK.name], overnight_required=self.overnight)
                log.warning('Auto-Hedge: streaming %s', want)
            self.hedge_sym = want
        except Exception as e:
            self.core.hedge.note = f'could not stream the hedge {want}: {e}'
            log.warning('Auto-Hedge: could not stream %s (%r)', want, e)

    def warm_up(self):
        try:
            rows = jb_webull.fetch_bars(self.data_client, self.core.strategy.symbol, 'M1', 1200)
            if rows:
                df = pd.DataFrame(rows)
                df.index = pd.to_datetime(df.pop('t'), unit='ms', utc=True)
                with self.lock:
                    self.core.seed_bars(df)
        except Exception as e:
            log.warning('warm-up history failed (starting with none): %r', e)

    # ---------------- streaming (SDK thread) ----------------
    def _on_disconnect(self, client, userdata, rc, *rest):
        """paho-mqtt's own callback (Webull's streaming client is an MQTT client): the stream lost its link to Webull.
        rc 0 = we asked for it (a reconnect). Otherwise safe mode, like IBKR's 1100 (jb-parity): no orders until the stream
        is subscribed again (the SDK reconnects by itself every 10 s) and, live, the position matches. Never raises."""
        try:
            if client is not self.client or not rc:
                return
            with self.lock:
                if self.core.connected:
                    log.warning('lost the connection to Webull (code %s) -- safe mode until it is back', rc)
                self.core.connected = False
                self.core.last_error = f'Webull connection lost (code {rc}): reconnecting'
                self.core.write_status()
        except Exception as e:
            log.warning('disconnect handling failed: %r', e)

    def _on_notice(self, q):
        """Webull's notice messages: type 1002 = another session took over this market data (permission preemption):
        prices may stop, so safe mode until they come back (jb-parity)."""
        kind = str(q.get('type') or '')
        if kind != '1002':
            return
        msg = str(q.get('content') or 'another session took over')[:120]
        with self.lock:
            log.warning('Webull: market data taken over by another session (%s) -- safe mode until prices come back', msg)
            self.preempted = True
            self.core.connected = False
            self.core.last_error = f'Webull: another session took over this market data ({msg})'
            self.core.write_status()

    def _on_message(self, client, topic, q):
        if topic == 'notice' or isinstance(q, dict):
            try:
                self._on_notice(q if isinstance(q, dict) else {})
            except Exception as e:
                log.warning('Webull notice not understood (%r): %r', q, e)
            return
        try:
            price = float(getattr(q, 'price', None))
            t = getattr(q, 'time', None) or getattr(getattr(q, 'basic', None), 'timestamp', None)
            ts = float(t) if t is not None else time.time() * 1000
            ts = int(ts * 1000 if ts < 1e11 else ts)
            vol = float(getattr(q, 'volume', 0) or 0)
        except (TypeError, ValueError):
            return
        sym = str(getattr(getattr(q, 'basic', None), 'symbol', '') or '').upper()
        if sym and sym != self.core.strategy.symbol.upper():
            if sym == (self.hedge_sym or ''):          # the Auto-Hedge fund (jb-hedge)
                with self.lock:
                    self.core.on_hedge_tick(price, ts)
            return
        self.alive_at = time.time()
        self.stall_pushed = False                     # prices again: a later outage gets its own push
        with self.lock:
            if self.preempted:                        # prices are back after Webull's notice 1002
                self.preempted = False
                self.core.connected, self.core.last_error = True, None
                log.warning('Webull prices are back after the market data was taken over')
            elif (self.core.last_error or '').startswith('no prices for'):
                self.core.last_error = None
            try:
                self.core.on_tick(price, ts, vol)
            except Exception as e:                    # never raise into the SDK
                log.exception('tick handling failed: %r', e)

    def _on_connect(self, client, api_client, session_id):
        self.session_ok = True
        self.alive_at = time.time()
        def sub():
            from webull.data.common.category import Category
            from webull.data.common.subscribe_type import SubscribeType
            try:
                try:
                    client.subscribe([self.core.strategy.symbol], Category.US_STOCK.name, [SubscribeType.TICK.name],
                                     overnight_required=self.overnight)
                except Exception as e:
                    if not (self.overnight and overnight_refused(e)):
                        raise
                    with self.lock:
                        self._drop_overnight()
                    client.subscribe([self.core.strategy.symbol], Category.US_STOCK.name, [SubscribeType.TICK.name],
                                     overnight_required=False)
                with self.lock:
                    self.core.connected = True
                    self.core.last_error = None if self.overnight else NO_OVERNIGHT_NOTE
                    if self.overnight and self.overnight_known is not True:
                        self.overnight_known = True       # Webull took a subscription with the overnight session
                        self._notes()
                        jb_webull.note_overnight('algo', True, 'Webull streams the overnight session')
                    self.core.write_status()
                log.info('streaming %s from Webull%s', self.core.strategy.symbol,
                         '' if self.overnight else ' (no overnight session on this Webull plan)')
                self.hedge_sym = None                 # a new session: the Auto-Hedge fund again (jb-hedge)
                self._hedge_subscribe(client)
            except Exception as e:
                log.error('subscribe failed: %r', e)
                with self.lock:
                    self.core.last_error = f'subscribe failed: {e}'
        threading.Thread(target=sub, daemon=True).start()

    def _timer(self):
        n = 0
        while not self.stop.wait(2):
            n += 1
            with self.lock:
                try:
                    self.core.on_timer(int(time.time() * 1000))
                    if n % 15 == 0:
                        self.core.write_status()      # keeps the dashboard's "running" dot fresh
                except Exception as e:
                    log.exception('timer failed: %r', e)
            self._stall_check()
            if self.broker is not None and n % 30 == 1:
                self._refresh_bp()
            if n % 5 == 0 and self.session_ok and self.core.hedge.feed_symbol() != self.hedge_sym:
                self._hedge_subscribe()               # Auto-Hedge turned on / changed (jb-hedge)
            if (n % 30 == 0 and self.session_ok and not self.overnight
                    and jb_webull.overnight_recheck_due(self.overnight_checked)):
                self.overnight_checked = time.time()
                threading.Thread(target=self._overnight_probe, daemon=True).start()   # Webull overnight ready

    def _refresh_bp(self):
        """Live: the account's buying power from Webull, for the entry check in core_trader (jb-ready)."""
        def find(obj, names):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if str(k).lower() in names:
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            pass
                for v in obj.values():
                    r = find(v, names)
                    if r is not None:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = find(v, names)
                    if r is not None:
                        return r
            return None
        try:
            acc, account = self.broker.trade.account_v2, self.broker.account_id()
            res = None
            for call in (lambda: acc.get_account_balance(account, 'USD'), lambda: acc.get_account_balance(account)):
                try:
                    res = call()
                    break
                except TypeError:
                    continue
            data = res.json() if hasattr(res, 'json') else res
            sess = self.core.session_now()[0]
            names = ['overnight_buying_power'] if sess == 'overnight' else ['day_buying_power']
            bp = None
            for n in names + ['buying_power', 'margin_power', 'cash_power']:
                bp = find(data, {n})
                if bp is not None:
                    break
            if bp is not None:
                with self.lock:
                    self.core.buying_power, self.core.buying_power_at = bp, time.time()
        except Exception as e:
            log.warning('could not read the Webull buying power: %s', jb_webull.describe_error(e))

    def _stall_check(self):
        """Prices can stop without any error (connected, but none come). No price for the symbol for STALL_S of the
        session now (the IBKR algo's wait times): reconnect the stream, and -- when the algo may trade in this session or
        holds a position -- push the phone once per outage ('Webull algo: prices stopped'), as the IBKR algo does
        (jb-parity). Overnight only when this Webull plan streams overnight prices."""
        c = self.client
        sess, ok = self.core.session_now()
        if c is None or not self.session_ok or sess is None or (sess == 'overnight' and not self.overnight):
            self.alive_at = time.time()
            return
        quiet = time.time() - self.alive_at
        if quiet < STALL_S.get(sess, 900):
            return
        mins = int(quiet // 60)
        self.alive_at = time.time()
        held = self.core.portfolio.position is not None
        log.warning('no Webull prices for %s for %d min (%s) -- reconnecting the stream', self.core.strategy.symbol, mins, sess)
        if ok or held:
            with self.lock:
                self.core.last_error = f'no prices for {mins} min: reconnected to Webull'
            if not self.stall_pushed:
                self.stall_pushed = True
                threading.Thread(target=self.core._push, daemon=True, args=(
                    'Webull algo: prices stopped',
                    f'No {self.core.strategy.symbol} prices for {mins} min, so the algo reconnected to Webull.'
                    + (' It holds a position: its stop-loss only works while prices arrive.' if held else ''),
                    'rotating_light' if held else 'warning')).start()
        try:
            c._thread_terminate = True        # stops the SDK's own retry loop on this client
            c.disconnect()                    # ...and makes connect_and_loop_forever return
        except Exception as e:
            log.warning('disconnect failed: %r', e)

    def run(self):
        if not self.connect_api():
            return
        self.warm_up()
        with self.lock:
            self.core.write_status()
        threading.Thread(target=self._timer, daemon=True).start()
        # jb-onestream: Webull streams to one connection per App Key, so the algo gets its prices through the board's
        # Webull bridge (lib/jb_webull_relay.py), not a Webull connection of its own
        import jb_webull_relay
        DataStreamingClient = jb_webull_relay.streaming_client_class()
        http_host, mqtt_host = jb_webull.hosts(self.args.environment)
        delay = 1
        log.info('trader running (%s mode) for %s', self.core.mode, self.core.strategy.symbol)
        while not self.stop.is_set():
            self.session_ok = False
            if not jb_webull.stream_allowed():        # connecting now would ping the phone
                log.error(jb_webull.AUTH_HELP)
                with self.lock:
                    self.core.last_error = jb_webull.AUTH_HELP
                    self.core.write_status()
                if not jb_webull.wait_for_approval(self.stop):
                    break
            try:
                c = DataStreamingClient(self.args.app_key, self.args.app_secret, self.args.region, self.args.session_id,
                                        http_host=http_host, mqtt_host=mqtt_host)
                c.on_connect_success = self._on_connect
                c.on_quotes_message = self._on_message
                c.on_disconnect = self._on_disconnect      # paho-mqtt: the link to Webull dropped (safe mode)
                self.client = c
                c.connect_and_loop_forever(logger_enable=True, customer_logger=jb_webull.sdk_logger())
            except Exception as e:
                log.error('stream error: %r', e)
                with self.lock:
                    self.core.last_error = jb_webull.describe_error(e)[:160]
            with self.lock:
                self.core.connected = False
                self.core.write_status()
            if self.session_ok:           # it had connected fine: reconnect straight away
                delay = 1
            if self.stop.wait(delay):
                break
            delay = min(delay * 2, 120)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    ap = argparse.ArgumentParser(description='Webull paper/live trader')
    ap.add_argument('--strategy', required=True)
    ap.add_argument('--mode', choices=['paper', 'live'], default='paper')
    ap.add_argument('--app-key', default=os.environ.get('WEBULL_APP_KEY'))
    ap.add_argument('--app-secret', default=os.environ.get('WEBULL_APP_SECRET'))
    ap.add_argument('--region', default=(os.environ.get('WEBULL_REGION_ID') or 'us'))
    ap.add_argument('--environment', default=os.environ.get('WEBULL_ENVIRONMENT', 'prod'), choices=['prod', 'uat'])
    ap.add_argument('--starting-cash', type=float, default=100_000.0)
    ap.add_argument('--commission-per-share', type=float, default=0.0)
    ap.add_argument('--slippage-pct', type=float, default=0.0)
    ap.add_argument('--max-order-quantity', type=int, default=10_000)
    ap.add_argument('--max-order-notional-usd', type=float, default=None)
    ap.add_argument('--status-path', default=None, help='default: <project>/logs/live_status.json')
    ap.add_argument('--session-id', default=None, help='Webull streaming session (default differs for paper/live)')
    ap.add_argument('--options-path', default=None, help='default: <project>/trading_options.json (the dashboard\'s)')
    args = ap.parse_args()
    if not args.app_key or not args.app_secret:
        raise SystemExit('Missing WEBULL_APP_KEY / WEBULL_APP_SECRET (settings.env).')
    args.status_path = args.status_path or default_status_path(args.strategy)
    args.options_path = args.options_path or default_options_path(args.strategy)
    args.session_id = args.session_id or ('josephs_board_live' if args.mode == 'live' else 'josephs_board_algo')
    strategy = Strategy.from_yaml(args.strategy)
    if args.mode == 'live':
        try:                      # both switches must be on BEFORE anything starts
            guards.check_live_trading_enabled(strategy)
        except guards.GuardRejected as e:
            raise SystemExit(f'\nLIVE TRADING NOT STARTED: {e}\n')
        cap = args.max_order_notional_usd or strategy.max_order_notional_usd
        opts, source, _ = opt.effective(strategy.trading_raw, args.options_path)
        log.warning('=' * 70)
        log.warning('LIVE MODE -- REAL ORDERS CAN BE PLACED ON WEBULL. Ctrl+C within 5 s to abort.')
        log.warning('strategy %r on %s; max order value %s', strategy.name, strategy.symbol,
                    f'${cap:,.0f}' if cap else 'not capped')
        log.warning('trading (%s): %s', source, opt.describe(opts, 'live'))
        log.warning('(the trading options can be changed on the dashboard while this runs)')
        log.warning('=' * 70)
        time.sleep(5)
    trader = WebullTrader(strategy, args)
    signal.signal(signal.SIGHUP, signal.default_int_handler)   # Terminal window closed: stop cleanly, like Ctrl+C
    # Stop Live on the algo tab (launchd's SIGTERM to the Go Live service) stops it the same clean way: orders still
    # working are cancelled, nothing new is sent, the position stays (jb-golive). SIGINT set too: a background
    # process may have been started with it ignored.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        trader.run()
    except KeyboardInterrupt:
        pass
    finally:
        trader.stop.set()
        if trader.broker is not None and trader.broker.working:
            ids = trader.broker.cancel_working()
            log.warning('STOPPED WITH AN ORDER STILL WORKING: asked Webull to cancel it (%s). Check the Webull app: '
                        'if part of it filled, the next start will say the position differs -- then use '
                        './jb live-reset after checking.', ', '.join(ids))
        with trader.lock:
            trader.core.save()


REFUSED_RC = 3          # live trading refused to start (LIVE TRADING NOT STARTED: ...): ./jb live and the Go Live service tell
                        # a start-up refusal (nothing ran) apart from a stop while running (jb-golive)


def run_main():
    try:
        main()
    except SystemExit as e:
        if isinstance(e.code, str) and 'LIVE TRADING NOT STARTED' in e.code:
            print(e.code, flush=True)
            sys.exit(REFUSED_RC)
        raise


if __name__ == '__main__':
    run_main()
