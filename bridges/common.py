"""Shared plumbing for both board bridges (ibkr_bridge.py, webull_bridge.py).

Everything here is broker-agnostic and lives in ONE place so a fix applies to both bridges:

  * TickStore   - the bridge's own minute-by-minute price record for every watched symbol,
                  kept 35 days in small append-only daily files (a crash or a launchd restart
                  can never corrupt it, and saving costs next to nothing). This record is what makes overnight moves
                  visible: brokers' own history bars don't cover the overnight session, but
                  every tick the bridge sees is kept here.
  * Watchlist   - the board's symbol list lives HERE on the always-on Mac (not in each
                  browser), so the phone, the work desktop and the dashboard all show the same
                  symbols, and the bridge keeps streaming + recording them 24/7 whether or not
                  any page is open.
  * BridgeServer - the browser-facing WebSocket server (protocol v2, below). Thread-safe:
                  broker SDKs that call back from their own threads go through post(), never
                  touching asyncio objects directly. Ticks are batched to at most 10 messages
                  per second per browser (latest price per symbol) so a burst of trades can't
                  flood a phone.
  * HistoryCache - the broker's own history bars, kept on disk, so a page load (or a
                  restart) shows every change window at once instead of waiting on the broker.
                  Pages always get the bridge's own record immediately; broker bars only fill
                  in where the record has nothing (before recording began, or while the Mac was
                  off), and are refreshed in the background.

Protocol v2 (browser <-> bridge, JSON over one WebSocket):
  browser -> bridge: {type:'auth', token}            (only if a token is configured)
                     {type:'hello'}                  -> bridge replies hello + watchlist + status
                     {type:'add'|'remove', symbol}   -> watchlist changes, broadcast to everyone
                     {type:'history', symbol}        -> a 'history' reply (a second one follows
                                                        if broker bars had to be fetched first)
                     {type:'subscribe'|'unsubscribe', symbol}   (v1 pages: treated as add/remove)
  bridge -> browser: {type:'hello', bridge, v:2}
                     {type:'watchlist', symbols:[...]}
                     {type:'ticks', ticks:[{symbol, price, ts, source}, ...]}
                     {type:'history', symbol, intraday:[{t,p}], daily:[{t,p}]}
                     {type:'status', symbol|null, kind:'live'|'idle'|'stale'|'error', msg}
                     {type:'adjust', symbol, before, delta}   (futures roll: add delta to points before)
"""
import asyncio
import bisect
from array import array
import json
import os
import re
import tempfile
import threading
import time

KEEP_MS = 35 * 24 * 3600 * 1000
FINE_MS = 26 * 3600 * 1000          # history sent to pages: 1-min points for the last 26 h,
MID_MS = 8 * 86400 * 1000           # 5-min points back to 8 days,
COARSE_BUCKET_MS = 5 * 60 * 1000
OLD_BUCKET_MS = 30 * 60 * 1000      # 30-min points before that (plenty for the 1W / 1MO windows)
HIST_FRESH_S = 15 * 60              # broker history older than this is re-fetched in the background
GAP_TOL_MS = 10 * 60 * 1000         # broker bars are used only where our record has no point this close


def now_ms():
    return int(time.time() * 1000)


def atomic_write_json(path, obj):
    """Write JSON so the file on disk is always either the old or the new complete version,
    never a half-written one: write a temp file in the same folder, flush+fsync, then rename
    over the original (rename is atomic on macOS/Linux)."""
    d = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.' + os.path.basename(path) + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(obj, f, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        # keep the unreadable file for inspection rather than silently overwriting it
        bad = path + '.unreadable-%d' % int(time.time())
        try:
            os.replace(path, bad)
        except OSError:
            pass
        print(f'warning: {path} could not be read ({e!r}); moved it to {bad} and started fresh',
              flush=True)
        return default


_SYM_RE = re.compile(r'^[A-Z0-9][A-Z0-9.\-/^]{0,11}(=F)?$')


def normalize_symbol(s):
    """Upper-case, trimmed, sane ticker (AAPL, BRK.B, ES=F) or None."""
    if not isinstance(s, str):
        return None
    s = s.strip().upper()
    return s if _SYM_RE.match(s) else None


def is_futures(sym):
    return sym.endswith('=F')


def _day(minute_ms):
    return time.strftime('%Y-%m-%d', time.gmtime(minute_ms / 1000))


class TickStore:
    """The bridge's own price record: the last trade price of every minute, per symbol, kept
    35 days. On disk it is one small append-only file per day (data/<broker>_ticks/<date>.csv,
    lines "SYMBOL,minute_ms,price"): each minute only the newly finished minutes are appended,
    so saving is instant and a crash can at worst lose the last line. Files older than the
    35 days are deleted automatically. In memory each symbol is two compact arrays (16 bytes
    a minute, under 1 MB per symbol for the full 35 days).

    Futures rolls: when a future switches to the next contract month, the price gap between
    the two contracts is saved in adjustments.json and applied to everything recorded before
    the roll, so the 1D..1MO changes don't show the gap as a market move."""

    def __init__(self, path, legacy_paths=()):
        self.path = path                                   # e.g. data/ibkr_ticks.json (old format)
        self.dir = os.path.splitext(path)[0]               # -> data/ibkr_ticks/
        self.data = {}                                     # sym -> (array of minute_ms, array of prices)
        self.saved = {}                                    # sym -> newest minute written to disk
        self.redo = set()                                  # (sym, minute) changed after being written
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()
        self._pruned_at = 0
        os.makedirs(self.dir, exist_ok=True)
        self.adj_path = os.path.join(self.dir, 'adjustments.json')
        adj = load_json(self.adj_path, {})
        self.adjustments = adj if isinstance(adj, dict) else {}   # sym -> [[roll_ms, delta], ...]
        self._load()
        if not self.data:
            self._migrate([path] + [os.path.expanduser(p) for p in legacy_paths])

    # ---- disk ----
    def _load(self):
        cutoff = _day(now_ms() - KEEP_MS - 86400000)
        merged = {}
        try:
            names = sorted(n for n in os.listdir(self.dir) if n.endswith('.csv'))
        except OSError:
            names = []
        for name in names:
            if name[:-4] < cutoff:
                continue
            try:
                with open(os.path.join(self.dir, name), encoding='utf-8', errors='replace') as f:
                    for line in f:
                        parts = line.split(',')
                        if len(parts) != 3:
                            continue                   # e.g. a line cut short by a power cut
                        try:
                            merged.setdefault(parts[0], {})[int(parts[1])] = float(parts[2])
                        except ValueError:
                            continue
            except OSError as e:
                print(f'warning: could not read {name}: {e!r}', flush=True)
        self.data = {}
        for sym, d in merged.items():
            ts = sorted(d)
            self.data[sym] = (array('q', ts), array('d', (d[t] for t in ts)))
        self.saved = {sym: s[0][-1] for sym, s in self.data.items() if len(s[0])}

    def _migrate(self, paths):
        """Carry over a record kept by an earlier version (one big JSON file)."""
        for lp in paths:
            if not os.path.exists(lp):
                continue
            data = load_json(lp, None)
            if not isinstance(data, dict):
                continue
            clean = {}
            for sym, lst in data.items():
                try:
                    pts = sorted((int(m), float(p)) for m, p in lst)
                except (TypeError, ValueError):
                    continue
                if pts and normalize_symbol(sym):
                    clean[sym] = (array('q', (m for m, _ in pts)), array('d', (p for _, p in pts)))
            self.data = clean
            self.saved = {}
            self.save(final=True)
            try:
                os.replace(lp, lp + '.migrated')
            except OSError:
                pass
            print(f'imported earlier price record from {lp}', flush=True)
            return

    def save(self, final=False):
        """Append every finished minute not yet on disk (final=True: the current one too)."""
        with self._save_lock:
            cur = (now_ms() // 60000) * 60000
            out = {}
            with self._lock:
                for sym, (ts, px) in self.data.items():
                    last = self.saved.get(sym, -1)
                    i = bisect.bisect_right(ts, last)
                    for j in range(i, len(ts)):
                        m = ts[j]
                        if m >= cur and not final:
                            break
                        out.setdefault(_day(m), []).append(f'{sym},{m},{px[j]!r}\n')
                        self.saved[sym] = m
                for sym, m in self.redo:
                    s = self.data.get(sym)
                    if s:
                        j = bisect.bisect_left(s[0], m)
                        if j < len(s[0]) and s[0][j] == m:
                            out.setdefault(_day(m), []).append(f'{sym},{m},{s[1][j]!r}\n')
                self.redo = set()
            try:
                for day, lines in out.items():
                    with open(os.path.join(self.dir, day + '.csv'), 'a', encoding='utf-8') as f:
                        f.write(''.join(lines))
            except OSError as e:
                print(f'warning: could not save the price record: {e!r}', flush=True)
            if time.time() - self._pruned_at > 3600:
                self._prune()

    def _prune(self):
        self._pruned_at = time.time()
        cutoff_ms = now_ms() - KEEP_MS
        with self._lock:
            for sym in list(self.data):
                ts, px = self.data[sym]
                i = bisect.bisect_left(ts, cutoff_ms)
                if i:
                    del ts[:i]
                    del px[:i]
                if not len(ts):
                    del self.data[sym]
            for sym in list(self.adjustments):
                keep = [a for a in self.adjustments[sym] if a[0] >= cutoff_ms]
                if keep:
                    self.adjustments[sym] = keep
                else:
                    del self.adjustments[sym]
        cutoff_day = _day(cutoff_ms - 86400000)
        try:
            for name in os.listdir(self.dir):
                if name.endswith('.csv') and name[:-4] < cutoff_day:
                    os.remove(os.path.join(self.dir, name))
        except OSError:
            pass

    # ---- recording ----
    def record(self, sym, price, ts):
        minute = (int(ts) // 60000) * 60000
        with self._lock:
            s = self.data.get(sym)
            if s is None:
                s = self.data[sym] = (array('q'), array('d'))
            tsa, px = s
            n = len(tsa)
            if n and tsa[-1] == minute:
                px[-1] = price
                if minute <= self.saved.get(sym, -1):
                    self.redo.add((sym, minute))
            elif n and minute < tsa[-1]:
                # late tick for an earlier minute (rare): update that minute if present
                for j in range(n - 1, max(-1, n - 6), -1):
                    if tsa[j] == minute:
                        px[j] = price
                        if minute <= self.saved.get(sym, -1):
                            self.redo.add((sym, minute))
                        break
            else:
                tsa.append(minute)
                px.append(price)

    def last(self, sym):
        """(minute_ms, price) of the newest recorded minute, or None."""
        with self._lock:
            s = self.data.get(sym)
            return (s[0][-1], s[1][-1]) if s and len(s[0]) else None

    def earliest(self, sym):
        s = self.data.get(sym)
        return s[0][0] if s and len(s[0]) else None

    def add_adjustment(self, sym, roll_ms, delta):
        with self._lock:
            self.adjustments.setdefault(sym, []).append([int(roll_ms), float(delta)])
            self.adjustments[sym].sort()
            snap = {k: list(v) for k, v in self.adjustments.items()}
        try:
            atomic_write_json(self.adj_path, snap)
        except Exception as e:
            print(f'warning: could not save {self.adj_path}: {e!r}', flush=True)

    def adjust_points(self, sym, points):
        """Apply saved roll adjustments to [{t, p}] (sorted by t), in place."""
        adj = self.adjustments.get(sym)
        if not adj or not points:
            return points
        total = sum(d for _, d in adj)
        k = 0
        for pt in points:
            while k < len(adj) and pt['t'] >= adj[k][0]:
                total -= adj[k][1]
                k += 1
            if total:
                pt['p'] = round(pt['p'] + total, 6)
        return points

    def for_page(self, sym):
        """Minute points for the last 26 h, 5-min points back to 8 days, 30-min points back
        to 35 days: every change window stays exact while a page load stays small. Each point
        is stamped at the END of its minute (it holds that minute's last price), and futures
        roll adjustments are applied."""
        with self._lock:
            s = self.data.get(sym)
            if not s:
                return []
            tsa, pxa = s[0][:], s[1][:]
        now = now_ms()
        fine_cut, mid_cut = now - FINE_MS, now - MID_MS
        out, last_bucket = [], None
        for t, p in zip(tsa, pxa):
            te = min(t + 59999, now)
            if t >= fine_cut:
                out.append({'t': te, 'p': p})
                last_bucket = None
                continue
            b = ('m', t // COARSE_BUCKET_MS) if t >= mid_cut else ('o', t // OLD_BUCKET_MS)
            if b == last_bucket:
                out[-1] = {'t': te, 'p': p}
            else:
                out.append({'t': te, 'p': p})
                last_bucket = b
        return self.adjust_points(sym, out)


def merge_history(own, broker):
    """Our own recorded points always win; broker bars are added only where the record has
    no point within GAP_TOL_MS (before recording started, or while this Mac was off)."""
    if not broker:
        return own
    times = [p['t'] for p in own]
    out = list(own)
    for b in broker:
        i = bisect.bisect_left(times, b['t'] - GAP_TOL_MS)
        if i < len(times) and times[i] <= b['t'] + GAP_TOL_MS:
            continue
        if times and b['t'] > times[0]:     # inside the record: a stretch filled in after downtime (jb-ideas)
            b = dict(b, f=1)
        out.append(b)
    out.sort(key=lambda p: p['t'])
    return out


class HistoryCache:
    """Broker history per symbol ({at, boot, daily}), saved to disk with the tick record."""

    def __init__(self, path):
        self.path = path
        data = load_json(path, {})
        self.data = data if isinstance(data, dict) else {}
        self.dirty = False

    def get(self, sym):
        e = self.data.get(sym)
        return e if isinstance(e, dict) else None

    def put(self, sym, boot, daily):
        self.data[sym] = {'at': time.time(), 'boot': boot, 'daily': daily}
        self.dirty = True

    def drop(self, sym):
        if self.data.pop(sym, None) is not None:
            self.dirty = True

    def snapshot(self):
        """Call on the event loop; the returned copy can be written from another thread."""
        self.dirty = False
        return dict(self.data)

    def write(self, snap):
        try:
            atomic_write_json(self.path, snap)
        except Exception as e:
            self.dirty = True
            print(f'warning: could not save {self.path}: {e!r}', flush=True)


class Watchlist:
    def __init__(self, path, defaults, legacy_path=None):
        self.path = path
        data = load_json(path, None)
        if data is None and legacy_path and os.path.exists(os.path.expanduser(legacy_path)):
            data = load_json(os.path.expanduser(legacy_path), None)
        if not isinstance(data, list):
            data = list(defaults)
        self.symbols = []
        for s in data:
            s = normalize_symbol(s)
            if s and s not in self.symbols:
                self.symbols.append(s)
        self.save()

    def __contains__(self, sym):
        return sym in self.symbols

    def add(self, sym):
        if sym in self.symbols:
            return False
        self.symbols.append(sym)
        self.save()
        return True

    def remove(self, sym):
        if sym not in self.symbols:
            return False
        self.symbols.remove(sym)
        self.save()
        return True

    def save(self):
        try:
            atomic_write_json(self.path, self.symbols)
        except Exception as e:
            print(f'warning: could not save {self.path}: {e!r}', flush=True)


class VolumeStore:
    """Traded volume per symbol per minute (shares; contracts for futures), kept 7 days in
    data/<bridge>_volume.json. Only trades that report their size are counted (IBKR's RT-volume
    trades, Webull's quote volume), so it is a record of what this Mac saw, not an official total."""
    KEEP_MS = 7 * 24 * 3600 * 1000

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.dirty = False
        self._lock = threading.Lock()
        try:
            with open(path) as f:
                for sym, rows in (json.load(f) or {}).items():
                    self.data[sym] = {int(m): float(v) for m, v in rows}
        except Exception:
            pass

    def add(self, sym, ts, size):
        try:
            size = float(size)
        except (TypeError, ValueError):
            return
        if not (size > 0) or size != size:
            return
        minute = (int(ts) // 60000) * 60000
        with self._lock:
            d = self.data.setdefault(sym, {})
            d[minute] = d.get(minute, 0.0) + size
            self.dirty = True

    def save(self):
        if not self.dirty:
            return
        cut = now_ms() - self.KEEP_MS
        with self._lock:
            for sym in list(self.data):
                d = self.data[sym]
                for m in [m for m in d if m < cut]:
                    del d[m]
                if not d:
                    del self.data[sym]
            snap = {s: sorted([m, round(v, 4)] for m, v in d.items()) for s, d in self.data.items()}
            self.dirty = False
        try:
            atomic_write_json(self.path, snap)
        except OSError as e:
            print(f'could not save volume: {e!r}', flush=True)


class BridgeServer:
    """Browser-facing side of a bridge. The broker side plugs in through three callbacks:
         on_add(sym) / on_remove(sym)   called on the event loop after the watchlist changed
         history(sym) -> awaitable of (intraday_bars, daily_bars), each a list of {t, p};
                         ([], []) if the broker can't answer right now
    """

    def __init__(self, name, store: TickStore, watchlist: Watchlist, token='',
                 on_add=None, on_remove=None, history=None, history_cache_path=None):
        self.name = name
        self.store = store
        self.hcache = HistoryCache(history_cache_path or (os.path.splitext(store.path)[0] + '_history.json'))
        self.volume = VolumeStore(os.path.join(os.path.dirname(store.path), name + '_volume.json'))
        self._rolls = {}            # symbol -> pending futures-roll adjustment (see begin_roll)
        self._fetching = {}         # symbol -> in-flight broker history fetch (shared by all pages)
        self.watchlist = watchlist
        self.token = token or ''
        self.on_add = on_add
        self.on_remove = on_remove
        self.history = history
        self.loop = None
        self.clients = set()
        self._pending = {}          # symbol -> latest tick msg, flushed every 100 ms
        self._last_key = {}
        self._last_status = {}      # symbol|None -> status msg (replayed to new browsers)
        self.last_tick_ms = 0
        # jb-onestream: other parts of the board (scanner, Webull algo) that get their prices through this bridge
        # instead of a Webull connection of their own (lib/jb_webull_relay.py). ws -> followed symbols.
        self.followers = {}
        self._fpending = {}         # ws -> trades not yet sent (every trade, with its size and session)
        self.on_follow = None       # async (symbols, overnight) -> None, or an error text the follower gets back
        self.on_unfollow = None     # called after a follower dropped symbols or went away

    # ---- thread-safe entry points ----
    def post(self, fn, *args):
        """Run fn(*args) on the event loop, from ANY thread (broker SDK callbacks)."""
        loop = self.loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn(*args)
        else:
            try:
                loop.call_soon_threadsafe(fn, *args)
            except RuntimeError:
                pass  # loop closed during shutdown

    def status(self, sym, kind, msg):
        self.post(self._status, sym, kind, msg)

    def tick(self, sym, price, ts, source, size=0):
        self.post(self._tick, sym, price, ts, source, size)

    # ---- loop-thread internals ----
    def _status(self, sym, kind, msg):
        m = {'type': 'status', 'symbol': sym, 'kind': kind, 'msg': msg}
        self._last_status[sym] = m
        self._broadcast(m)

    def followed(self):
        out = set()
        for s in self.followers.values():
            out |= s
        return out

    def relay(self, sym, price, ts, session=None, size=0):
        """Every trade for a followed symbol goes to the parts that follow it (loop thread; jb-onestream)."""
        if not self.followers:
            return
        t = None
        for ws, syms in self.followers.items():
            if sym in syms:
                if t is None:
                    try:
                        t = {'symbol': sym, 'price': float(price), 'ts': int(ts or now_ms()), 'vol': float(size or 0),
                             'session': session if isinstance(session, str) else None}
                    except (TypeError, ValueError):
                        return
                q = self._fpending.setdefault(ws, [])
                if len(q) < 20000:
                    q.append(t)

    async def _follow(self, ws, m):
        rid = m.get('id')
        add = [s for s in (normalize_symbol(x) for x in (m.get('add') or [])) if s]
        drop = [s for s in (normalize_symbol(x) for x in (m.get('remove') or [])) if s]
        mine = self.followers.setdefault(ws, set())
        if add:
            err = await self.on_follow(add, bool(m.get('overnight'))) if self.on_follow else None
            if err:
                await self._send(ws, {'type': 'follow_err', 'id': rid, 'error': err})
                return
            mine.update(add)
        if drop:
            mine.difference_update(drop)
            if self.on_unfollow:
                self.on_unfollow()
        await self._send(ws, {'type': 'follow_ok', 'id': rid, 'symbols': sorted(mine)})

    def _tick(self, sym, price, ts, source, size=0):
        try:
            price = float(price)
        except (TypeError, ValueError):
            return
        if not (price > 0) or sym not in self.watchlist:
            return
        now = now_ms()
        ts = int(ts) if ts else now
        if ts > now + 5000:
            ts = now
        if size:
            self.volume.add(sym, ts, size)
        key = (price, ts)
        if self._last_key.get(sym) == key:
            return
        self._last_key[sym] = key
        self.last_tick_ms = now
        if sym in self._rolls:
            self._finish_roll(sym, price, ts)
        self.store.record(sym, price, ts)
        self._pending[sym] = {'symbol': sym, 'price': price, 'ts': ts, 'source': source}

    # ---- futures rolls ----
    def begin_roll(self, sym, new_contract):
        """Call on the loop right after a future switched to its next contract month (old
        stream stopped, new one started). The first price from the new contract is compared
        with the old contract's last price and the difference applied to the history."""
        self.hcache.drop(sym)
        last = self.store.last(sym)
        if last is None:
            return
        self._rolls[sym] = {'at': now_ms(), 'old_t': last[0], 'old_p': last[1], 'contract': new_contract}

    def _finish_roll(self, sym, price, ts):
        r = self._rolls.pop(sym)
        fresh = r['at'] - r['old_t'] <= 10 * 60000 and ts - r['at'] <= 10 * 60000
        delta = price - r['old_p']
        if not fresh or not r['old_p'] or abs(delta) / r['old_p'] > 0.2:
            print(f"{sym}: rolled to {r['contract']}; the market was quiet around the roll, so the "
                  f"history before it is left unadjusted", flush=True)
            return
        roll_at = r['at']                                     # everything recorded before the switch
        self.store.add_adjustment(sym, roll_at, delta)
        self.hcache.drop(sym)
        self._broadcast({'type': 'adjust', 'symbol': sym, 'before': roll_at, 'delta': delta})
        print(f"{sym}: rolled to {r['contract']}; earlier history shifted by {delta:+.6g} "
              f"(the gap between the two contracts) so it isn't shown as a price move", flush=True)

    async def _flusher(self):
        while True:
            await asyncio.sleep(0.1)
            if self._pending and self.clients:
                batch = list(self._pending.values())
                self._pending = {}
                self._broadcast({'type': 'ticks', 'ticks': batch})
            elif not self.clients:
                self._pending = {}
            if self._fpending:
                out, self._fpending = self._fpending, {}
                for ws, ticks in out.items():
                    if ws in self.followers:
                        asyncio.ensure_future(self._send(ws, {'type': 'fticks', 'ticks': ticks}))

    def _broadcast(self, obj):
        if not self.clients:
            return
        data = json.dumps(obj, separators=(',', ':'))
        for ws in list(self.clients):
            asyncio.ensure_future(self._send_raw(ws, data))

    async def _send_raw(self, ws, data):
        try:
            await ws.send(data)
        except Exception:
            pass

    async def _send(self, ws, obj):
        await self._send_raw(ws, json.dumps(obj, separators=(',', ':')))

    def _watchlist_msg(self):
        return {'type': 'watchlist', 'symbols': list(self.watchlist.symbols)}

    def add_symbol(self, sym):
        if self.watchlist.add(sym):
            self._broadcast(self._watchlist_msg())
            if self.on_add:
                self.on_add(sym)

    def remove_symbol(self, sym):
        if self.watchlist.remove(sym):
            self._pending.pop(sym, None)
            self._last_status.pop(sym, None)
            self.hcache.drop(sym)
            self._broadcast(self._watchlist_msg())
            if self.on_remove:
                self.on_remove(sym)

    def _history_msg(self, sym, entry):
        own = self.store.for_page(sym)
        boot = entry.get('boot') or [] if entry else []
        daily = entry.get('daily') or [] if entry else []
        return {'type': 'history', 'symbol': sym, 'intraday': merge_history(own, boot), 'daily': daily}

    def fetch_history(self, sym):
        """Start (or join) one broker history fetch for sym; the result lands in the cache."""
        fut = self._fetching.get(sym)
        if fut is None:
            fut = asyncio.ensure_future(self._do_fetch(sym))
            self._fetching[sym] = fut
            fut.add_done_callback(lambda f, s=sym: self._fetching.pop(s, None))
        return fut

    async def _do_fetch(self, sym):
        if not self.history:
            return None
        try:
            boot, daily = await self.history(sym)
        except Exception as e:
            print(f'history for {sym} failed: {e!r}', flush=True)
            return None
        if not boot and not daily:
            return None
        if sym in self.watchlist:
            self.hcache.put(sym, sorted(boot, key=lambda b: b['t']), daily)
        return {'boot': boot, 'daily': daily}

    async def _send_history(self, ws, sym):
        entry = self.hcache.get(sym)
        if entry:                                   # the usual case: instant
            await self._send(ws, self._history_msg(sym, entry))
            if time.time() - entry.get('at', 0) > HIST_FRESH_S:
                self.fetch_history(sym)             # refresh quietly for next time
            return
        # never fetched: send our own record now, the broker's bars as soon as they arrive
        await self._send(ws, self._history_msg(sym, None))
        for _ in range(20):
            entry = await asyncio.shield(self.fetch_history(sym))
            if ws not in self.clients or sym not in self.watchlist:
                return
            if entry:
                await self._send(ws, self._history_msg(sym, entry))
                return
            await asyncio.sleep(30)                 # broker not ready (e.g. IB Gateway starting)

    async def _saver(self, every=60):
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(every)
            await loop.run_in_executor(None, self.store.save)
            await loop.run_in_executor(None, self.volume.save)
            if self.hcache.dirty:
                await loop.run_in_executor(None, self.hcache.write, self.hcache.snapshot())

    def save_now(self):
        """Final save when launchd stops/restarts the service, so no recorded minute is lost."""
        self.store.save(final=True)
        self.volume.save()
        if self.hcache.dirty:
            self.hcache.write(self.hcache.snapshot())

    async def handler(self, ws):
        if self.token:
            try:
                first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            except Exception:
                await ws.close(code=4001, reason='auth required')
                return
            if not isinstance(first, dict) or first.get('type') != 'auth' or first.get('token') != self.token:
                await ws.close(code=4001, reason='bad token')
                return
        self.clients.add(ws)
        try:
            await self._send(ws, {'type': 'hello', 'bridge': self.name, 'v': 2})
            await self._send(ws, self._watchlist_msg())
            for m in list(self._last_status.values()):
                await self._send(ws, m)
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                kind = m.get('type')
                if kind in ('hello', 'auth'):
                    continue
                if kind == 'follow':
                    try:
                        await self._follow(ws, m)
                    except Exception as e:
                        await self._send(ws, {'type': 'follow_err', 'id': m.get('id'), 'error': repr(e)})
                    continue
                sym = normalize_symbol(m.get('symbol'))
                if not sym:
                    if kind in ('add', 'subscribe'):
                        await self._send(ws, {'type': 'status', 'symbol': None, 'kind': 'error',
                                              'msg': f"{m.get('symbol')!r} doesn't look like a ticker"})
                    continue
                if kind in ('add', 'subscribe'):
                    self.add_symbol(sym)
                elif kind in ('remove', 'unsubscribe'):
                    self.remove_symbol(sym)
                elif kind == 'history':
                    asyncio.ensure_future(self._send_history(ws, sym))
        except Exception as e:
            # a phone going to sleep drops the connection without a goodbye -- normal, not an error
            if type(e).__name__ not in ('ConnectionClosedError', 'ConnectionClosedOK', 'ConnectionClosed'):
                print(f'browser connection error: {e!r}', flush=True)
        finally:
            self.clients.discard(ws)
            self._fpending.pop(ws, None)
            if self.followers.pop(ws, None) and self.on_unfollow:
                self.on_unfollow()

    async def start(self, host, port):
        import websockets
        self.loop = asyncio.get_running_loop()
        # compression: history replies shrink ~5x (matters on a phone); ticks are tiny either way
        server = await websockets.serve(self.handler, host, port, max_size=2 ** 20)
        asyncio.ensure_future(self._flusher())
        asyncio.ensure_future(self._saver())
        try:
            import signal
            self.loop.add_signal_handler(signal.SIGTERM, self._on_sigterm)
        except (NotImplementedError, RuntimeError):
            pass
        return server

    def _on_sigterm(self):
        print('stopping: saving the price record', flush=True)
        try:
            self.save_now()
        finally:
            os._exit(0)
