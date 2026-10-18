"""Things every paper/live trader needs regardless of broker (identical file in webull-algo
and ibkr-algo):

  * restart-safe state: the paper portfolio (cash, open position, trade log, equity curve) is
    saved after every bar and every trade and restored when the trader starts again -- so a
    crash, a launchd restart or a reboot no longer silently wipes weeks of paper trading, and
    in live mode a restart doesn't forget that a real position is open
  * JSON that browsers can read (no NaN / Infinity, which Python writes but JSON forbids)
  * US market sessions (pre-market, regular, after-hours, overnight) and the NYSE calendar
"""
import json
import math
import os
import tempfile
from datetime import datetime, time as dtime

import pandas as pd

from .portfolio import Position, Trade


# ------------------------------------------------------------------ JSON helpers
def jsonsafe(o):
    if isinstance(o, float):
        if math.isnan(o):
            return None
        if math.isinf(o):
            return 'inf' if o > 0 else '-inf'
        return o
    if isinstance(o, dict):
        return {k: jsonsafe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonsafe(v) for v in o]
    if hasattr(o, 'item') and not isinstance(o, (str, bytes)):      # numpy scalars
        try:
            return jsonsafe(o.item())
        except Exception:
            return str(o)
    if hasattr(o, 'isoformat'):
        return o.isoformat()
    return o


def atomic_write_json(path, obj):
    d = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.' + os.path.basename(path) + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(jsonsafe(obj), f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _ts(v):
    return pd.Timestamp(v) if v is not None else None


# ------------------------------------------------------------------ portfolio persistence
def portfolio_to_dict(pf):
    pos = pf.position
    return {
        'starting_cash': pf.starting_cash, 'cash': pf.cash,
        'position': None if pos is None else {'side': pos.side, 'shares': pos.shares,
                                               'entry_price': pos.entry_price, 'entry_time': pos.entry_time,
                                               'peak': pos.peak},
        'trades': [{'time': t.time, 'side': t.side, 'shares': t.shares, 'price': t.price,
                    'reason': t.reason, 'commission': t.commission} for t in pf.trades],
        'equity_curve': [[t, e] for t, e in pf.equity_curve[-5000:]],
    }


def restore_portfolio(pf, d):
    pf.cash = float(d['cash'])
    p = d.get('position')
    pf.position = None if not p else Position(side=p['side'], shares=int(p['shares']),
                                              entry_price=float(p['entry_price']), entry_time=_ts(p['entry_time']),
                                              peak=float(p['peak']) if p.get('peak') is not None else None)
    pf.trades = [Trade(time=_ts(t['time']), side=t['side'], shares=int(t['shares']), price=float(t['price']),
                       reason=t['reason'], commission=float(t.get('commission') or 0)) for t in d.get('trades', [])]
    pf.equity_curve = [(_ts(t), float(e)) for t, e in d.get('equity_curve', [])]


class StateFile:
    """logs/state_<strategy file name>_<paper|live>.json"""

    def __init__(self, logs_dir, strategy, mode):
        stem = os.path.splitext(os.path.basename(strategy.source_path))[0]
        self.path = os.path.join(logs_dir, f'state_{stem}_{mode}.json')
        self.strategy = strategy
        self.mode = mode

    def load_into(self, pf):
        """Restores the portfolio if a matching saved state exists. Returns a short
        description of what was restored, or None."""
        try:
            with open(self.path) as f:
                d = json.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:
            bad = self.path + '.unreadable'
            os.replace(self.path, bad)
            return f'saved state was unreadable ({e!r}); moved to {os.path.basename(bad)} and started fresh'
        if d.get('symbol') != self.strategy.symbol or abs(float(d.get('starting_cash', -1)) - pf.starting_cash) > 1e-6:
            return None           # a different symbol or starting cash: treat as a new run
        restore_portfolio(pf, d['portfolio'])
        pos = pf.position
        return (f"{len(pf.trades)} trades, cash {pf.cash:,.2f}" +
                (f", open {pos.side} {pos.shares} @ {pos.entry_price:.2f}" if pos else ', flat') +
                f", saved {d.get('saved_at', '?')}")

    def save(self, pf):
        atomic_write_json(self.path, {'symbol': self.strategy.symbol, 'strategy_name': self.strategy.name,
                                      'mode': self.mode, 'starting_cash': pf.starting_cash,
                                      'saved_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                                      'portfolio': portfolio_to_dict(pf)})


# ------------------------------------------------------------------ market hours
# The NYSE calendar, worked out from its rules (so it never runs out): full closures and the
# 1:00 pm early closes (the day after Thanksgiving, Christmas Eve, and July 3 when the 4th is
# a weekday).
from datetime import date, timedelta
from functools import lru_cache


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _nth(y, m, weekday, n):
    """n-th given weekday (Mon=0) of a month; n=-1 for the last one."""
    if n > 0:
        d = date(y, m, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


@lru_cache(maxsize=16)
def nyse_holidays(y):
    days = set()
    ny = date(y, 1, 1)
    if ny.weekday() == 6:
        days.add(date(y, 1, 2))
    elif ny.weekday() < 5:
        days.add(ny)                           # (a Saturday New Year's Day is not made up)
    days.add(_nth(y, 1, 0, 3))                 # Martin Luther King Jr. Day
    days.add(_nth(y, 2, 0, 3))                 # Washington's Birthday
    days.add(_easter(y) - timedelta(days=2))   # Good Friday
    days.add(_nth(y, 5, 0, -1))                # Memorial Day
    if y >= 2022:
        days.add(_observed(date(y, 6, 19)))    # Juneteenth
    days.add(_observed(date(y, 7, 4)))         # Independence Day
    days.add(_nth(y, 9, 0, 1))                 # Labor Day
    days.add(_nth(y, 11, 3, 4))                # Thanksgiving
    days.add(_observed(date(y, 12, 25)))       # Christmas
    return frozenset(days)


@lru_cache(maxsize=16)
def nyse_early_closes(y):
    days = {_nth(y, 11, 3, 4) + timedelta(days=1)}          # day after Thanksgiving
    j4 = date(y, 7, 4)
    if 1 <= j4.weekday() <= 4:                               # 4th on Tue-Fri -> 3rd closes early
        days.add(date(y, 7, 3))
    c24 = date(y, 12, 24)
    if c24.weekday() <= 3:                                   # Christmas Eve Mon-Thu
        days.add(c24)
    return frozenset(days - nyse_holidays(y))


def trading_day(day):
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


_ET = None


def _et():
    global _ET
    if _ET is None:
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo('America/New_York')
    return _ET


def session_at(now=None):
    """Which US stock session is running at `now` (default: right now), US/Eastern:
         'premarket'   4:00-9:30
         'regular'     9:30-16:00   (13:00 on early-close days)
         'afterhours'  16:00-20:00  (13:00-17:00 on early-close days)
         'overnight'   20:00-3:50, the night before each trading day (so Sunday evening to
                       Friday morning; none before a holiday or a weekend)
         None          closed (weekends, holidays, 3:50-4:00, 17:00-20:00 on early-close days)
    NYSE holidays and early closes come from the rules above. (IBKR's overnight venue closes
    at 3:50; Webull's at 4:00 -- 3:50 is used for both.)"""
    d = now.astimezone(_et()) if now is not None else datetime.now(_et())
    day, t = d.date(), d.time()
    if t >= dtime(20, 0):
        return 'overnight' if trading_day(day + timedelta(days=1)) else None
    if t < dtime(3, 50):
        return 'overnight' if trading_day(day) else None
    if not trading_day(day) or t < dtime(4, 0):
        return None
    if t < dtime(9, 30):
        return 'premarket'
    early = day in nyse_early_closes(day.year)
    if t < (dtime(13, 0) if early else dtime(16, 0)):
        return 'regular'
    if t < (dtime(17, 0) if early else dtime(20, 0)):
        return 'afterhours'
    return None


def regular_hours(now=None):
    """True during the US regular session: Mon-Fri 9:30-16:00 US/Eastern (13:00 on early-close
    days), NYSE holidays excluded."""
    return session_at(now) == 'regular'
