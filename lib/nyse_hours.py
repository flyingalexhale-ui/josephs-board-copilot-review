"""When US stocks trade on Webull without the overnight session: trading days (no weekends,
no NYSE holidays) from 4:00 am until 4 hours after the close (8:00 pm, or 5:00 pm on the
1:00 pm early-close days). The Webull "no prices" watchdogs use this so they don't keep
reconnecting on market holidays. Same NYSE rules as the algos' runstate.py (standard library only)."""
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


def stocks_trade(d):
    """d: a datetime in New York time."""
    day = d.date()
    if not trading_day(day):
        return False
    end = (13 if day in nyse_early_closes(day.year) else 16) + 4
    return 4 <= d.hour < end
