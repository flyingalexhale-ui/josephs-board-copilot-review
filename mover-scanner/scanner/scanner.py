"""The orchestrator: config + feeds + window trackers + detectors + event log + the page's
WebSocket server. Runs forever.

How ticks from two feeds are combined (changed from the first version, which added BOTH feeds'
copies of every trade together and so doubled volume whenever both were up):
  * each symbol listens to ONE feed at a time -- the preferred one (feeds.prefer in
    watchlist.yaml, IBKR by default) -- and falls over to the other feed automatically if the
    preferred one goes quiet for that symbol for more than 30 s, switching back when it returns.
  * feeds may call on_tick from their own threads (the Webull SDK does); everything is handed
    to the asyncio loop first, so all state is touched from one thread only.

Switching between ETFs and stocks: the page sends {type:'set_mode', mode:'etfs'|'stocks'}. The
scanner saves the choice (scan_mode.txt) and quits; the Mac's service manager starts it again
within ~10 s on the new list. Restarting is deliberately the only way to switch -- it reuses
the scanner's normal start-up path instead of re-subscribing feeds on the fly.
"""
import asyncio
import json
import logging
import os
import time

from .aggregator import WindowTracker
from . import detectors
from .config import MODES, write_mode
from .picks import regular_day, overnight_key
from .aggregator import session_of

log = logging.getLogger('scanner')
FAILOVER_MS = 30_000


class Scanner:
    def __init__(self, config, log_dir='logs', config_path=None, notice=''):
        self.config = config
        self.config_path = config_path
        self.notice = notice          # shown on the page, e.g. why a switch to stocks failed
        self.switching = False
        self.trackers = {s: WindowTracker(s, config.window_ms, config.baseline_windows) for s in config.symbols}
        self.clients = set()
        self.latest = {}
        self.recent_events = []
        self.picks = None         # latest research picks (scanner/picks.py), sent to the page
        self.picks_min = {}       # '1' / '5' -> latest minute picks (scanner/intraday.py)
        self.minute_hook = None   # fn(symbol, price, ts_ms, volume): every tick, for the minute picks
        self.rth = {}             # symbol -> today's regular-session open/high/low/last (for the picks)
        self.prev_close = {}      # symbol -> the regular session's last price on the day before self.rth's day
        self.ext = {}             # symbol -> overnight-session record + latest price in any session (picks, vs close)
        self.daily_close = None   # set by run_scanner: symbol -> last completed daily close (after a restart)
        self.loop = None
        self.prefer = config.prefer
        self.active = {}          # symbol -> feed currently used
        self.last_by_feed = {}    # (symbol, feed) -> last tick wall ms
        self._dirty = set()
        os.makedirs(log_dir, exist_ok=True)
        self.jsonl_path = os.path.join(log_dir, 'movers.jsonl')
        self.text_log_path = os.path.join(log_dir, 'movers.log')

    # ---------------- tick intake (any thread) ----------------
    def on_tick(self, symbol, price, ts_ms, volume, source):
        loop = self.loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._on_tick(symbol, price, ts_ms, volume, source)
        else:
            loop.call_soon_threadsafe(self._on_tick, symbol, price, ts_ms, volume, source)

    def _on_tick(self, symbol, price, ts_ms, volume, source):
        tr = self.trackers.get(symbol)
        if tr is None or not (price and price > 0):
            return
        now = int(time.time() * 1000)
        self.last_by_feed[(symbol, source)] = now
        cur = self.active.get(symbol)
        if cur != source:
            cur_fresh = cur is not None and now - self.last_by_feed.get((symbol, cur), 0) < FAILOVER_MS
            if cur is None or (source == self.prefer) or not cur_fresh:
                if cur is not None:
                    log.info('%s: now using %s ticks (was %s)', symbol, source, cur)
                self.active[symbol] = source
            else:
                return            # the other feed is live for this symbol: ignore this copy
        ts_ms = int(ts_ms or now)
        if ts_ms > now + 5000:
            ts_ms = now
        day = regular_day(ts_ms)
        if day:
            r = self.rth.get(symbol)
            p = float(price)
            if r is None or r['day'] != day:
                if r is not None:
                    self.prev_close[symbol] = r['last']
                self.rth[symbol] = {'day': day, 'open': p, 'open_ts': ts_ms, 'high': p, 'low': p,
                                    'last': p, 'last_ts': ts_ms}
            else:
                if ts_ms >= r['last_ts']:
                    r['last'], r['last_ts'] = p, ts_ms
                r['high'], r['low'] = max(r['high'], p), min(r['low'], p)
        self._track_ext(symbol, float(price), ts_ms)
        if self.minute_hook:
            self.minute_hook(symbol, float(price), ts_ms, float(volume or 0))
        closed = tr.add_tick(float(price), ts_ms, float(volume or 0))
        snap = tr.current_snapshot() or {}
        self.latest[symbol] = {'symbol': symbol, 'last_price': float(price), 'last_ts': ts_ms, 'last_source': source,
                               'window_open': snap.get('open'), 'window_volume': snap.get('volume', 0),
                               'n_baseline_windows': len(tr.history),
                               'session': self.ext[symbol]['session'], 'vs_close': self.vs_close(symbol)}
        self._dirty.add(symbol)
        if closed:
            self._on_window_closed(closed)

    def _on_window_closed(self, window):
        fired = detectors.run_all(window, self.config.detectors)
        rec = {k: window[k] for k in ('symbol', 't_start', 't_end', 'open', 'high', 'low', 'close', 'volume', 'session')}
        rec['n_baseline_windows'] = len(window['_same_session'])
        rec['feed'] = self.active.get(window['symbol'])
        rec['mode'] = self.config.mode
        rec['fired'] = fired
        # volume compared two ways (shown on the page with what each one means)
        def avg_vol(ws):
            ws = [w['volume'] for w in ws if w.get('volume')]
            return sum(ws) / len(ws) if len(ws) >= 3 else None
        a_sess, a_hour = avg_vol(window['_same_session']), avg_vol(window['_history_before'])
        rec['vol_vs_session'] = round(window['volume'] / a_sess, 2) if a_sess and window['volume'] else None
        rec['vol_vs_hour'] = round(window['volume'] / a_hour, 2) if a_hour and window['volume'] else None
        self._append_log(rec)
        if fired:
            self.recent_events.insert(0, rec)
            del self.recent_events[200:]
            self._broadcast({'type': 'alert', **rec})

    # ---- overnight record + change since the 4 pm close ----
    def _track_ext(self, symbol, p, ts_ms):
        e = self.ext.setdefault(symbol, {})
        sess = session_of(ts_ms)
        if sess == 'overnight':
            key = overnight_key(ts_ms)
            if e.get('on_key') != key:          # a new overnight session: starts from the last price before it
                e.update(on_key=key, on_start=e.get('last', p), on_hi=p, on_lo=p)
            e['on_end'], e['on_end_ts'] = p, ts_ms
            e['on_hi'], e['on_lo'] = max(e['on_hi'], p), min(e['on_lo'], p)
        e['last'], e['last_ts'], e['session'] = p, ts_ms, sess

    def close_ref(self, symbol):
        """The last 4:00 pm close: yesterday's while the regular session runs, else the latest one."""
        r = self.rth.get(symbol)
        now = int(time.time() * 1000)
        if r and r['day'] == regular_day(now):
            ref = self.prev_close.get(symbol)
        elif r:
            ref = r['last']
        else:
            ref = None
        if ref is None and self.daily_close:
            try:
                ref = self.daily_close(symbol)
            except Exception:
                ref = None
        return ref

    def vs_close(self, symbol):
        e, ref = self.ext.get(symbol), self.close_ref(symbol)
        return round((e['last'] - ref) / ref * 100, 3) if e and ref else None

    @staticmethod
    def _rotate(path, max_bytes=50_000_000):
        """Keep a log from growing forever: past max_bytes it becomes <name>.old (replacing
        the previous .old) and a fresh file starts."""
        try:
            if os.path.getsize(path) > max_bytes:
                os.replace(path, path + '.old')
        except OSError:
            pass

    def _append_log(self, rec):
        self._n_logged = getattr(self, '_n_logged', 0) + 1
        if self._n_logged % 500 == 1:
            self._rotate(self.jsonl_path)
            self._rotate(self.text_log_path, 5_000_000)
        try:
            with open(self.jsonl_path, 'a') as f:
                f.write(json.dumps(rec) + '\n')
        except Exception as e:
            log.error('failed writing %s: %r', self.jsonl_path, e)
        if rec['fired']:
            ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(rec['t_end'] / 1000))
            reasons = '; '.join(f"{r['reason']}: {r['detail']}" for r in rec['fired'])
            line = (f"[{ts}] {rec['symbol']:6s} O:{rec['open']:.2f} H:{rec['high']:.2f} L:{rec['low']:.2f} "
                    f"C:{rec['close']:.2f} V:{rec['volume']:,.0f} ({rec['session']}) -- {reasons}\n")
            try:
                with open(self.text_log_path, 'a') as f:
                    f.write(line)
            except Exception as e:
                log.error('failed writing %s: %r', self.text_log_path, e)
            log.warning(line.strip())

    # ---------------- periodic work ----------------
    async def _flush_quotes(self):
        while True:
            await asyncio.sleep(0.25)
            if self._dirty and self.clients:
                self._broadcast({'type': 'quotes', 'quotes': [self.latest[s] for s in self._dirty if s in self.latest]})
            self._dirty.clear()

    async def _close_idle_windows(self):
        while True:
            await asyncio.sleep(1)
            now = int(time.time() * 1000)
            for tr in self.trackers.values():
                closed = tr.close_if_due(now)
                if closed:
                    self._on_window_closed(closed)
                    if tr.symbol in self.latest:
                        self.latest[tr.symbol].update(window_open=None, window_volume=0,
                                                      n_baseline_windows=len(tr.history))
                        self._dirty.add(tr.symbol)

    # ---------------- page server ----------------
    def _broadcast(self, obj):
        if not self.clients:
            return
        data = json.dumps(obj)
        for ws in list(self.clients):
            asyncio.ensure_future(self._send(ws, data))

    @staticmethod
    async def _send(ws, data):
        try:
            await ws.send(data)
        except Exception:
            pass

    def set_picks(self, state):
        self.picks = state
        self._broadcast({'type': 'picks', **state})

    def set_minute_picks(self, tf, state):
        self.picks_min[str(tf)] = state
        self._broadcast({'type': 'picks_min', **state})

    def _set_mode(self, mode):
        if mode not in MODES or mode == self.config.mode or self.switching or not self.config_path:
            return
        try:
            write_mode(self.config_path, mode)
        except OSError as e:
            log.error('could not save the scanner mode: %r', e)
            return
        self.switching = True
        log.warning('switching the scanner to %s -- restarting (back in about 10 seconds)', mode.upper())
        self._broadcast({'type': 'switching', 'mode': mode})
        # quit hard (the Webull SDK's thread would otherwise keep the process alive); the
        # service manager starts the scanner again on the new list
        self.loop.call_later(0.5, os._exit, 0)

    async def run_server(self, host='127.0.0.1', port=8767, token=''):
        import websockets
        self.loop = asyncio.get_running_loop()

        async def handler(ws):
            if token:
                try:
                    first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                except Exception:
                    await ws.close(code=4001, reason='auth required')
                    return
                if not isinstance(first, dict) or first.get('type') != 'auth' or first.get('token') != token:
                    await ws.close(code=4001, reason='bad token')
                    return
            self.clients.add(ws)
            try:
                await self._send(ws, json.dumps({'type': 'snapshot', 'symbols': self.config.symbols,
                                                 'mode': self.config.mode, 'notice': self.notice,
                                                 'switching': self.switching,
                                                 'window_minutes': self.config.window_ms / 60000,
                                                 'latest': self.latest, 'recent_events': self.recent_events[:60],
                                                 'picks': self.picks, 'picks_min': self.picks_min}))
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if isinstance(msg, dict) and msg.get('type') == 'set_mode':
                        self._set_mode(msg.get('mode'))
            except Exception as e:
                # a phone going to sleep drops the connection without a goodbye -- normal
                if not type(e).__name__.startswith('ConnectionClosed'):
                    log.warning('page connection error: %r', e)
            finally:
                self.clients.discard(ws)

        async with websockets.serve(handler, host, port, compression=None):
            log.info('scanner page server on %s:%s', host, port)
            asyncio.ensure_future(self._flush_quotes())
            asyncio.ensure_future(self._close_idle_windows())
            await asyncio.Future()
