"""Trading options: which market sessions the algo may trade in, and how it sends orders.
(Identical file in webull-algo and ibkr-algo; the dashboard's gateway reads it too, so it
uses only Python's standard library.)

Where the options come from -- a later one replaces an earlier one completely:
  1. built-in defaults: regular hours only; limit orders 0.05% past the last price,
     cancelled if not filled within 60 seconds
  2. the strategy file's optional  trading:  section (see STRATEGY_FORMAT.md)
  3. the dashboard's "Trading options" panel, saved in <algo folder>/trading_options.json
     ("Use the strategy file's settings" on that panel deletes the file again)

The running paper/live trader re-reads trading_options.json every few seconds, so a change
made on the dashboard takes effect without a restart.

Sessions (US/Eastern; regular hours are always on):
  premarket   4:00-9:30        afterhours  16:00-20:00 (13:00-17:00 on early-close days)
  overnight   20:00-3:50, Sunday evening to Friday morning, before each trading day
"""
import json
import os
import tempfile

SESSIONS = ('premarket', 'afterhours', 'overnight')       # regular hours are always allowed
ALIASES = {'premarket': 'premarket', 'pre-market': 'premarket', 'pre': 'premarket',
           'afterhours': 'afterhours', 'after-hours': 'afterhours', 'after': 'afterhours',
           'post': 'afterhours', 'postmarket': 'afterhours', 'post-market': 'afterhours',
           'overnight': 'overnight', 'night': 'overnight',
           'regular': None, 'rth': None, 'core': None}
LABELS = {'premarket': 'pre-market', 'regular': 'regular hours', 'afterhours': 'after-hours',
          'overnight': 'overnight'}
ORDER_TYPES = ('limit', 'market')
DEFAULTS = {
    'paper_sessions': [],             # extra sessions for paper trading and backtests
    'live_sessions': [],              # extra sessions for live trading
    'order_type': 'limit',            # 'limit' or 'market' (market only in regular hours)
    'limit_offset_pct': 0.05,         # buy limit = last x (1 + this%), sell limit = last x (1 - this%)
    'cancel_after_s': 60,             # a limit order not filled by then is cancelled
    'trailing_stop_pct': None,        # e.g. 1.5: exit when the price falls 1.5% from its best since entry
    'extended_hours_cost_pct': 0.1,   # paper/backtest only: extra cost per fill outside regular hours
}
RANGES = {'limit_offset_pct': (-5.0, 5.0), 'cancel_after_s': (5, 3600),
          'trailing_stop_pct': (0.01, 50.0), 'extended_hours_cost_pct': (0.0, 5.0)}
FILE_NAME = 'trading_options.json'


class OptionsError(ValueError):
    pass


def _sessions(v, key):
    if v is None or v is False:
        return []
    if isinstance(v, str):
        v = [x for x in v.replace(',', ' ').split() if x]
    if not isinstance(v, (list, tuple)):
        raise OptionsError(f'{key} must be a list such as [premarket, afterhours, overnight]')
    out = []
    for x in v:
        name = str(x).strip().lower()
        if name not in ALIASES:
            raise OptionsError(f'{key}: unknown session {x!r} (use premarket, afterhours, overnight)')
        if ALIASES[name] and ALIASES[name] not in out:
            out.append(ALIASES[name])
    return [s for s in SESSIONS if s in out]


def _number(v, key, integer=False):
    if isinstance(v, bool):
        raise OptionsError(f'{key} must be a number')
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise OptionsError(f'{key} must be a number, got {v!r}')
    if n != n or n in (float('inf'), float('-inf')):
        raise OptionsError(f'{key} must be a number')
    lo, hi = RANGES[key]
    if not lo <= n <= hi:
        raise OptionsError(f'{key} must be between {lo:g} and {hi:g}, got {n:g}')
    return int(round(n)) if integer else n


def validate(d, where='trading options'):
    """Checks a (partial) options dict and returns a complete one (defaults filled in).
    Raises OptionsError with a plain-English reason."""
    if d is None:
        d = {}
    if not isinstance(d, dict):
        raise OptionsError(f'{where}: expected settings such as  order_type: limit')
    unknown = sorted(set(d) - set(DEFAULTS) - {'saved_at'})
    if unknown:
        raise OptionsError(f'{where}: unknown setting(s) {", ".join(unknown)} '
                           f'(known: {", ".join(DEFAULTS)})')
    out = dict(DEFAULTS)
    try:
        for key in ('paper_sessions', 'live_sessions'):
            if key in d:
                out[key] = _sessions(d[key], key)
        if 'order_type' in d:
            ot = str(d['order_type']).strip().lower()
            if ot not in ORDER_TYPES:
                raise OptionsError(f"order_type must be 'limit' or 'market', got {d['order_type']!r}")
            out['order_type'] = ot
        for key in ('limit_offset_pct', 'extended_hours_cost_pct'):
            if key in d and d[key] is not None:
                out[key] = _number(d[key], key)
        if 'cancel_after_s' in d and d['cancel_after_s'] is not None:
            out['cancel_after_s'] = _number(d['cancel_after_s'], 'cancel_after_s', integer=True)
        if 'trailing_stop_pct' in d:
            v = d['trailing_stop_pct']
            out['trailing_stop_pct'] = None if v in (None, '', 0, False) else _number(v, 'trailing_stop_pct')
    except OptionsError as e:
        raise OptionsError(f'{where}: {e}') from None
    out['paper_sessions'] = list(out['paper_sessions'])
    out['live_sessions'] = list(out['live_sessions'])
    return out


def path_for(algo_dir):
    return os.path.join(algo_dir, FILE_NAME)


def stamp(path):
    """Changes whenever the options file is written or removed."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except (OSError, TypeError):
        return None


def load_file(path):
    """(options, None) from the dashboard's file; (None, None) if there is none;
    (None, reason) if it can't be used."""
    if not path:
        return None, None
    try:
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None, None
    except Exception as e:
        return None, f'{os.path.basename(path)} could not be read ({e})'
    try:
        return validate(raw, os.path.basename(path)), None
    except OptionsError as e:
        return None, str(e)


def effective(strategy_trading, path):
    """(options, source, problem): the options in force right now and where they came from."""
    opts, problem = load_file(path)
    if opts is not None:
        return opts, 'dashboard', None
    if strategy_trading:
        return validate(strategy_trading), 'strategy file', problem
    return dict(DEFAULTS), 'built-in defaults', problem


def save_file(path, d):
    """Validates and writes atomically (a half-written file can never be read)."""
    opts = validate(d, 'dashboard settings')
    from datetime import datetime
    opts['saved_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.' + FILE_NAME + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(opts, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return opts


def describe(opts, mode):
    """One line for logs, e.g. 'regular hours + pre-market, after-hours; limit orders 0.05% ...'."""
    extra = opts['live_sessions' if mode == 'live' else 'paper_sessions']
    sess = 'regular hours' + (' + ' + ', '.join(LABELS[s] for s in extra) if extra else ' only')
    lim = f"limit orders {opts['limit_offset_pct']:g}% from the last price, cancelled after {opts['cancel_after_s']}s"
    how = f'market orders in regular hours; outside them {lim}' if opts['order_type'] == 'market' else lim
    if opts.get('trailing_stop_pct'):
        how += f"; trailing stop {opts['trailing_stop_pct']:g}%"
    return f'{sess}; {how}'


def _tick(p):
    from decimal import Decimal
    return Decimal('0.01') if p >= 1 else Decimal('0.0001')


def round_price(p, how='nearest'):
    """US stock prices: whole cents from $1 up, 4 decimals below $1. how: nearest | up | down."""
    from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
    mode = {'nearest': ROUND_HALF_UP, 'up': ROUND_CEILING, 'down': ROUND_FLOOR}[how]
    return float(Decimal(repr(float(p))).quantize(_tick(p), rounding=mode))


def limit_price(ref, side, offset_pct, closing=False):
    """The limit price for an order at last price `ref`.
      * positive offset: buys above / sells below the last price, rounded AWAY from it and at
        least one tick away, so the order can fill (0.05% of $8 is under a cent -- without this
        it would round back to the last price)
      * negative offset (entries only): a better price than the last, rounded away from it
      * exits (closing=True) are always priced to fill: a negative or zero offset is treated as
        one tick through the last price, so a stop-loss or trailing stop never waits for a
        better price while the price runs away"""
    from decimal import Decimal
    ref = float(ref)
    buy = side == 'buy'
    off = float(offset_pct)
    if closing and off < 0:
        off = 0.0
    tick = float(_tick(ref))
    last = round_price(ref)
    raw = ref * (1 + off / 100.0) if buy else ref * (1 - off / 100.0)
    if off > 0 or closing:
        px = max(round_price(raw, 'up'), last + tick) if buy else min(round_price(raw, 'down'), last - tick)
    elif off < 0:
        px = round_price(raw, 'down') if buy else round_price(raw, 'up')
    else:
        px = last
    px = max(px, tick)
    return float(Decimal(repr(px)).quantize(_tick(px)))
