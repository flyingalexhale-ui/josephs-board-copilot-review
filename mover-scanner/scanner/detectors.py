"""Pluggable "is this ETF doing something worth a look" checks, each one a plain function
of a completed window plus the windows that preceded it -- add a new one the same shape and
register it, same pattern as webull-algo's indicators.py registry.

A "window" here is one completed ~5-minute OHLCV bucket for one symbol (see
scanner/aggregator.py's WindowTracker). Every detector gets:
    window   -- {'open','high','low','close','volume','t_start','t_end','_history_before'}
                 for the window that just closed. '_history_before' is the list of PRECEDING
                 completed windows only, most-recent-last -- a snapshot WindowTracker took
                 before this window was appended to its own history, so a detector computing
                 a trailing average from it can never have this window's own data leak into
                 its own baseline (see aggregator.py's add_tick docstring for why that
                 ordering matters and how it's guaranteed).
    config   -- this detector's own settings from watchlist.yaml (thresholds), already
                 merged with its defaults.

Returns None if it didn't fire, or a dict {'reason': <short id>, 'detail': <human string>,
'severity': <float, roughly comparable across detectors, higher = more notable>} if it did.
Multiple detectors can all fire on the same window -- the scanner reports every one that did,
not just the first.
"""


def _baseline(window, config):
    """Windows to compare against: by default only earlier windows from the SAME session
    (pre-market / regular / after-hours / overnight), so the opening bell isn't compared
    with thin pre-market trading. Set same_session: false in watchlist.yaml to compare with
    every earlier window regardless of session."""
    if config.get('same_session', True) and '_same_session' in window:
        return window['_same_session']
    return window['_history_before']


def price_delta_pct(window: dict, config: dict):
    """Flags a window whose own open->close move (in percent) is at least
    config['threshold_pct'] in either direction. This is the plainest, most literal reading
    of "moving up or down every 5 minutes" -- no history needed, just this window's own
    move."""
    threshold = config.get('threshold_pct', 0.5)
    o, c = window['open'], window['close']
    if not o:
        return None
    pct = (c - o) / o * 100
    if abs(pct) < threshold:
        return None
    direction = 'up' if pct > 0 else 'down'
    return {
        'reason': 'price_delta',
        'detail': f"{direction} {abs(pct):.2f}% in this window (>= {threshold}% threshold)",
        'severity': abs(pct) / threshold,
        'pct': round(pct, 3),
    }


def volume_surge(window: dict, config: dict):
    """Flags a window whose volume is at least config['multiplier'] times the trailing
    average volume of the preceding windows. Needs at least
    config.get('min_baseline_windows', 3) prior windows to have a real baseline; returns
    None (not a false "surge") before that."""
    multiplier = config.get('multiplier', 3.0)
    min_windows = config.get('min_baseline_windows', 3)
    hist = _baseline(window, config)
    if len(hist) < min_windows:
        return None
    avg = sum(w['volume'] for w in hist) / len(hist)
    if avg <= 0:
        return None      # no volume data for this feed/session -- never a false "surge"
    vol = window['volume']
    ratio = vol / avg
    if ratio < multiplier:
        return None
    return {
        'reason': 'volume_surge',
        'detail': f"volume {vol:,.0f} is {ratio:.1f}x the trailing {len(hist)}-window "
                  f"average ({avg:,.0f}) -- threshold {multiplier}x",
        'severity': ratio / multiplier,
        'ratio': round(ratio, 2),
    }


def range_expansion(window: dict, config: dict):
    """Flags a window whose own high-low range is at least config['multiplier'] times the
    trailing average range of the preceding windows -- a volatility-spike check,
    independent of which direction price actually settled: a window that whipped both ways
    and closed flat still shows up here even though price_delta_pct wouldn't catch it."""
    multiplier = config.get('multiplier', 3.0)
    min_windows = config.get('min_baseline_windows', 3)
    hist = _baseline(window, config)
    if len(hist) < min_windows:
        return None
    avg = sum(w['high'] - w['low'] for w in hist) / len(hist)
    if avg <= 0:
        return None
    rng = window['high'] - window['low']
    ratio = rng / avg
    if ratio < multiplier:
        return None
    return {
        'reason': 'range_expansion',
        'detail': f"high-low range ${rng:.3f} is {ratio:.1f}x the trailing "
                  f"{len(hist)}-window average (${avg:.3f}) -- threshold {multiplier}x",
        'severity': ratio / multiplier,
        'ratio': round(ratio, 2),
    }


def cumulative_delta_pct(window: dict, config: dict):
    """Flags on a LONGER move than a single window would show: the percent change from
    config['lookback_windows'] windows ago to now. Catches a steady grind that never has
    one dramatic single window but adds up over, say, 15-20 minutes -- something
    price_delta_pct alone would miss entirely."""
    threshold = config.get('threshold_pct', 1.0)
    lookback = config.get('lookback_windows', 3)
    hist = window['_history_before']
    if len(hist) < lookback:
        return None
    close_n_ago = hist[-lookback]['close']
    if not close_n_ago:
        return None
    span = window['t_end'] - window['t_start']
    if window['t_start'] - hist[-lookback]['t_start'] > lookback * span:
        return None       # not back-to-back windows (a weekend, a quiet spell): not "the last N windows"
    c = window['close']
    pct = (c - close_n_ago) / close_n_ago * 100
    if abs(pct) < threshold:
        return None
    direction = 'up' if pct > 0 else 'down'
    return {
        'reason': 'cumulative_delta',
        'detail': f"{direction} {abs(pct):.2f}% over the last {lookback} windows "
                  f"(>= {threshold}% threshold)",
        'severity': abs(pct) / threshold,
        'pct': round(pct, 3),
    }


# Registry so scanner.py can run "every configured detector" without an if/elif chain, same
# idea as webull-algo's indicators.py REGISTRY. Add a new check above, then one line here.
REGISTRY = {
    'price_delta_pct': price_delta_pct,
    'volume_surge': volume_surge,
    'range_expansion': range_expansion,
    'cumulative_delta_pct': cumulative_delta_pct,
}


def run_all(window: dict, detector_configs: dict) -> list:
    """detector_configs: {'price_delta_pct': {...}, 'volume_surge': {...}, ...} -- only
    detectors present here run; the others are simply not evaluated for this watchlist.
    Set a detector's value to `false` in watchlist.yaml to disable it for one symbol
    without removing the line."""
    fired = []
    for name, cfg in detector_configs.items():
        fn = REGISTRY.get(name)
        if fn is None:
            continue
        if cfg is False:
            continue
        result = fn(window, cfg if isinstance(cfg, dict) else {})
        if result is not None:
            fired.append(result)
    return fired




# ---------------------------------------------------------------------------------------------
# jb-scanwhy (deep search idea 51): more move detectors, same shape as the ones above.
def _same_day_session(window):
    """Earlier windows of the same session and the same night/day, oldest first."""
    out = []
    for w in reversed(window.get('_same_session') or []):
        if out and out[-1]['t_start'] - w['t_end'] > 60 * 60 * 1000:
            break                                   # a gap over an hour: an earlier day's session
        if (out and out[-1]['t_start'] - w['t_end'] < 0):
            continue
        if not out and window['t_start'] - w['t_end'] > 60 * 60 * 1000:
            break
        out.append(w)
    return list(reversed(out))


def breakout_volume(window: dict, config: dict):
    """The close goes past the session's high (or low) so far, with config['volume_x'] (2x) the session's
    normal volume behind it: a breakout (or breakdown) with real buying (or selling)."""
    vx = config.get('volume_x', 2.0)
    need = config.get('min_windows', 4)
    prior = _same_day_session(window)
    if len(prior) < need:
        return None
    base = [w['volume'] for w in _baseline(window, config) if w.get('volume')]
    avg = sum(base) / len(base) if len(base) >= 3 else 0
    if avg <= 0 or window['volume'] < vx * avg:
        return None
    hi, lo = max(w['high'] for w in prior), min(w['low'] for w in prior)
    c = window['close']
    if c > hi:
        kind, lvl = 'breakout above the session high', hi
    elif c < lo:
        kind, lvl = 'breakdown below the session low', lo
    else:
        return None
    ratio = window['volume'] / avg
    return {'reason': 'breakout_volume', 'detail': f"{kind} ({lvl:.2f}) on {ratio:.1f}x normal volume",
            'severity': ratio / vx + 0.5, 'up': c > hi}


def vwap_cross(window: dict, config: dict):
    """The price crossed the session's average price weighted by volume (VWAP): reclaimed (back above) or lost, by at least
    config['min_pct'] (0.1%) after config['hold_windows'] (3) windows on the other side (not chop around it)."""
    prior = _same_day_session(window)
    need = config.get('min_windows', 4)
    if len(prior) < need:
        return None
    vol = sum(w['volume'] for w in prior if w.get('volume'))
    if vol <= 0:
        return None
    vw = sum(w['close'] * w['volume'] for w in prior if w.get('volume')) / vol
    a, b = prior[-1]['close'], window['close']
    side = [w['close'] for w in prior[-config.get('hold_windows', 3):]]
    if abs(b - vw) / vw * 100 < config.get('min_pct', 0.1):
        return None                                 # just touching it: not a real cross
    if b > vw and not all(c < vw for c in side) or b < vw and not all(c > vw for c in side):
        return None                                 # it was already chopping around the VWAP
    if a < vw <= b:
        return {'reason': 'vwap_cross', 'detail': f"reclaimed the session VWAP ({vw:.2f}): back above the average price paid", 'severity': 1.0, 'up': True}
    if a > vw >= b:
        return {'reason': 'vwap_cross', 'detail': f"lost the session VWAP ({vw:.2f}): now below the average price paid", 'severity': 1.0, 'up': False}
    return None


def squeeze_release(window: dict, config: dict):
    """After config['tight_windows'] (6) unusually narrow windows in a row, this one breaks out of that box with
    a range config['multiplier'] (2x) the tight average: a quiet spell ending."""
    n = config.get('tight_windows', 6)
    mult = config.get('multiplier', 2.0)
    hist = window['_history_before']
    base = _baseline(window, config)
    if len(hist) < n or len(base) < n:
        return None
    avg_all = sum(w['high'] - w['low'] for w in base) / len(base)
    tight = hist[-n:]
    t_avg = sum(w['high'] - w['low'] for w in tight) / n
    if avg_all <= 0 or t_avg <= 0 or t_avg > 0.6 * avg_all:
        return None
    rng = window['high'] - window['low']
    box_hi, box_lo = max(w['high'] for w in tight), min(w['low'] for w in tight)
    c = window['close']
    if rng < mult * t_avg or box_lo <= c <= box_hi:
        return None
    return {'reason': 'squeeze_release', 'detail': f"broke {'up' if c > box_hi else 'down'} out of a {n * 5}-minute tight range "
            f"({box_lo:.2f}-{box_hi:.2f})", 'severity': rng / t_avg / mult, 'up': c > box_hi}


def new_high_low_volume(window: dict, config: dict):
    """A new high (or low) of the last config['lookback'] (12) windows while volume keeps falling: the move is
    running out of buyers (sellers). A warning, not a signal to chase."""
    lb = config.get('lookback', 12)
    hist = window['_history_before']
    if len(hist) < lb:
        return None
    last = hist[-lb:]
    v3 = [w['volume'] for w in hist[-3:]]
    if not all(v3) or window['volume'] >= 0.7 * (sum(v3) / 3):
        return None
    c = window['close']
    if c > max(w['high'] for w in last):
        return {'reason': 'high_on_falling_volume', 'detail': f"new {lb * 5}-minute high on falling volume ({window['volume']:,.0f} vs {sum(v3) / 3:,.0f}): buyers thinning",
                'severity': 1.0, 'up': True}
    if c < min(w['low'] for w in last):
        return {'reason': 'high_on_falling_volume', 'detail': f"new {lb * 5}-minute low on falling volume ({window['volume']:,.0f} vs {sum(v3) / 3:,.0f}): sellers thinning",
                'severity': 1.0, 'up': False}
    return None


def three_strong(window: dict, config: dict):
    """Three windows in a row moving the same way, each at least config['min_pct'] (0.3%): a steady push."""
    m = config.get('min_pct', 0.3)
    hist = window['_history_before']
    if len(hist) < 2:
        return None
    ws = hist[-2:] + [window]
    span = window['t_end'] - window['t_start']
    if window['t_start'] - ws[0]['t_start'] > 2 * span:
        return None
    mv = [(w['close'] - w['open']) / w['open'] * 100 for w in ws if w['open']]
    if len(mv) < 3 or not (all(x >= m for x in mv) or all(x <= -m for x in mv)):
        return None
    tot = (window['close'] - ws[0]['open']) / ws[0]['open'] * 100
    return {'reason': 'three_strong', 'detail': f"3 strong {'up' if tot > 0 else 'down'} windows in a row ({tot:+.2f}% in 15 minutes)",
            'severity': abs(tot) / (3 * m), 'up': tot > 0}


def fade(window: dict, config: dict):
    """A move of at least config['move_pct'] (1%) over the last config['lookback'] (6) windows that has given back
    more than half of it: a fade."""
    mp = config.get('move_pct', 1.0)
    lb = config.get('lookback', 6)
    hist = window['_history_before']
    if len(hist) < lb:
        return None
    ws = hist[-lb:]
    start = ws[0]['open']
    if not start:
        return None
    hi, lo = max(w['high'] for w in ws), min(w['low'] for w in ws)
    up_run, dn_run = (hi - start) / start * 100, (start - lo) / start * 100
    c = window['close']
    if up_run >= mp and up_run >= dn_run and (hi - c) > 0.5 * (hi - start) and hist[-1]['close'] - start > 0.5 * (hi - start):
        return {'reason': 'fade', 'detail': f"gave back over half of a {up_run:.1f}% run up (high {hi:.2f})", 'severity': up_run / mp, 'up': False}
    if dn_run >= mp and dn_run > up_run and (c - lo) > 0.5 * (start - lo) and start - hist[-1]['close'] > 0.5 * (start - lo):
        return {'reason': 'fade', 'detail': f"won back over half of a {dn_run:.1f}% drop (low {lo:.2f})", 'severity': dn_run / mp, 'up': True}
    return None


REGISTRY.update({
    'breakout_volume': breakout_volume,
    'vwap_cross': vwap_cross,
    'squeeze_release': squeeze_release,
    'new_high_low_volume': new_high_low_volume,
    'three_strong': three_strong,
    'fade': fade,
})
