"""Per-symbol rolling windows: buckets ticks into fixed, clock-aligned windows (default 5 min)
and keeps the completed windows that detectors compare against.

Changes from the first version:
  * windows also close on a TIMER (Scanner.flush_idle -> close_if_due), not only when the next
    trade arrives -- so a window is evaluated right after it ends even in a quiet market
  * every window is tagged with its US/Eastern session (pre / regular / post / overnight), so
    volume and range can be compared against windows from the SAME session: the 9:30 open is
    no longer judged against pre-market volume (which used to flag nearly every ETF at 9:35)
"""
from collections import deque
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo('America/New_York')
except Exception:  # pragma: no cover
    _ET = None


def session_of(ts_ms):
    if _ET is None:
        return 'regular'
    d = datetime.fromtimestamp(ts_ms / 1000, _ET)
    m = d.hour * 60 + d.minute
    wd = d.weekday()
    if wd == 5 or (wd == 6 and m < 20 * 60) or (wd == 4 and m >= 20 * 60):
        return 'closed'          # Fri 8pm -> Sun 8pm ET: no US equity session
    if 4 * 60 <= m < 9 * 60 + 30:
        return 'pre'
    if 9 * 60 + 30 <= m < 16 * 60:
        return 'regular'
    if 16 * 60 <= m < 20 * 60:
        return 'post'
    return 'overnight'


class WindowTracker:
    def __init__(self, symbol, window_ms=5 * 60 * 1000, baseline_windows=12):
        self.symbol = symbol
        self.window_ms = window_ms
        self.baseline_windows = baseline_windows
        self._cur_start = None
        self._closed_until = None       # end of the last closed window: nothing may land before it
        self._o = self._h = self._l = self._c = None
        self._v = 0.0
        self._n = 0
        # completed windows, most recent last; a longer tail than baseline_windows so there are
        # enough same-session windows to compare against
        self.history = deque(maxlen=max(baseline_windows * 4, 24))

    def _bucket(self, ts_ms):
        return (ts_ms // self.window_ms) * self.window_ms

    def _close(self):
        """Close the open window. The returned dict carries the windows that came BEFORE it
        ('_history_before', and '_same_session' for the same session only), snapshotted
        before this window joins the history -- so no detector can ever compare a window
        against itself."""
        if self._cur_start is None or self._o is None:
            return None
        before = list(self.history)
        sess = session_of(self._cur_start)
        closed = {'symbol': self.symbol, 't_start': self._cur_start, 't_end': self._cur_start + self.window_ms,
                  'open': self._o, 'high': self._h, 'low': self._l, 'close': self._c, 'volume': self._v,
                  'trades': self._n, 'session': sess,
                  '_history_before': before[-self.baseline_windows:],
                  '_same_session': [w for w in before if w['session'] == sess][-self.baseline_windows:]}
        self.history.append({k: v for k, v in closed.items() if not k.startswith('_')})
        self._closed_until = closed['t_end']
        self._o = self._h = self._l = self._c = None
        self._v = 0.0
        self._n = 0
        return closed

    def add_tick(self, price, ts_ms, volume=0):
        bucket = self._bucket(ts_ms)
        if self._closed_until is not None and bucket < self._closed_until:
            bucket = self._closed_until      # late tick for a window already closed: never reopen it
        closed = None
        if self._cur_start is not None and bucket > self._cur_start:
            closed = self._close()
            self._cur_start = bucket
        elif self._cur_start is None:
            self._cur_start = bucket
        # a late tick for an already-closed window is folded into the current one, never
        # rewriting history after the fact
        if self._o is None:
            self._o = self._h = self._l = self._c = price
        else:
            self._h = max(self._h, price)
            self._l = min(self._l, price)
            self._c = price
        self._v += volume or 0
        self._n += 1
        return closed

    def close_if_due(self, now_ms, grace_ms=3000):
        """Timer path: close the open window once wall-clock time is past its end."""
        if self._cur_start is not None and self._o is not None and now_ms >= self._cur_start + self.window_ms + grace_ms:
            closed = self._close()
            self._cur_start = None
            return closed
        return None

    def current_snapshot(self):
        if self._o is None:
            return None
        return {'t_start': self._cur_start, 'open': self._o, 'high': self._h, 'low': self._l,
                'close': self._c, 'volume': self._v}
