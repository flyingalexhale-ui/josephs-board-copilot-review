"""Technical indicators, computed on a pandas DataFrame of OHLCV bars.

Every function takes a DataFrame with at least a 'close' column (some also need
'high'/'low'/'volume') and returns a pandas Series aligned to the same index -- so they
drop straight into a DataFrame as a new column: df['rsi_14'] = rsi(df, 14).

These are the deliberately "boring," well-known formulas (the same ones any charting
platform uses) -- nothing exotic, so there's nothing here to verify against a live account.
Add your own indicator by writing one more function in this same shape; the rule engine
(rules.py) can then reference it by whatever column name you give it.
"""
import numpy as np
import pandas as pd


def sma(df: pd.DataFrame, period: int, column: str = 'close') -> pd.Series:
    """Simple moving average."""
    return df[column].rolling(window=period, min_periods=period).mean()


def ema(df: pd.DataFrame, period: int, column: str = 'close') -> pd.Series:
    """Exponential moving average."""
    return df[column].ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(df: pd.DataFrame, period: int = 14, column: str = 'close') -> pd.Series:
    """Relative Strength Index, 0-100. Proper Wilder smoothing: the first average gain/loss
    is a plain mean of the first `period` deltas (Wilder's own seed), and every value after
    that recursively blends in the new delta at weight 1/period -- NOT a plain EWM from the
    very first row, which understates the seed and gives a visibly wrong number for the
    first many bars after the window fills (verified against the standard Investopedia/
    Wilder worked example: this produces RSI=70.46 at the 15th close on that example's
    data, matching the textbook value; a naive ewm(alpha=1/period, adjust=False) from bar
    zero gives 43.2 there instead -- a large, silent error worth avoiding)."""
    delta = df[column].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = pd.Series(np.nan, index=df.index)
    avg_loss = pd.Series(np.nan, index=df.index)
    n = len(df)
    if n > period:
        seed_gain = gain.iloc[1:period + 1].mean()
        seed_loss = loss.iloc[1:period + 1].mean()
        avg_gain.iloc[period] = seed_gain
        avg_loss.iloc[period] = seed_loss
        g = gain.to_numpy(copy=True)
        l = loss.to_numpy(copy=True)
        ag = avg_gain.to_numpy(copy=True)
        al = avg_loss.to_numpy(copy=True)
        for i in range(period + 1, n):
            ag[i] = (ag[i - 1] * (period - 1) + g[i]) / period
            al[i] = (al[i - 1] * (period - 1) + l[i]) / period
        avg_gain = pd.Series(ag, index=df.index)
        avg_loss = pd.Series(al, index=df.index)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.where(avg_loss != 0, 100.0)  # avg_loss==0 -> no losses -> RSI 100, not NaN


def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9,
         column: str = 'close') -> pd.DataFrame:
    """Returns a DataFrame with columns 'macd', 'signal', 'hist'."""
    fast_ema = df[column].ewm(span=fast, adjust=False, min_periods=fast).mean()
    slow_ema = df[column].ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({
        'macd': macd_line,
        'signal': signal_line,
        'hist': macd_line - signal_line,
    })


def bollinger(df: pd.DataFrame, period: int = 20, num_std: float = 2.0,
              column: str = 'close') -> pd.DataFrame:
    """Returns a DataFrame with columns 'mid', 'upper', 'lower'."""
    mid = sma(df, period, column)
    std = df[column].rolling(window=period, min_periods=period).std()
    return pd.DataFrame({
        'mid': mid,
        'upper': mid + num_std * std,
        'lower': mid - num_std * std,
    })


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range. Needs 'high', 'low', 'close' columns."""
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session-cumulative Volume-Weighted Average Price. Needs 'high','low','close','volume'.
    Resets at the start of each calendar day found in the index (the index must be a
    DatetimeIndex) -- a standard intraday VWAP, not a running-forever one."""
    typical = (df['high'] + df['low'] + df['close']) / 3
    day = df.index.normalize()
    pv = (typical * df['volume']).groupby(day).cumsum()
    vol = df['volume'].groupby(day).cumsum()
    return pv / vol.replace(0, np.nan)


def roc(df: pd.DataFrame, period: int = 10, column: str = 'close') -> pd.Series:
    """Rate of change, as a percent: (close_now - close_n_bars_ago) / close_n_bars_ago * 100."""
    return df[column].pct_change(periods=period) * 100


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    """Returns a DataFrame with columns '%k', '%d'. Needs 'high','low','close'."""
    low_min = df['low'].rolling(window=k_period, min_periods=k_period).min()
    high_max = df['high'].rolling(window=k_period, min_periods=k_period).max()
    k = 100 * (df['close'] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(window=d_period, min_periods=d_period).mean()
    return pd.DataFrame({'%k': k, '%d': d})


# Registry so strategy.py can build a requested indicator by name from a config file
# without a big if/elif chain -- add your function above, then register it here with the
# config keys it needs (used to build the column name and to call the function).
REGISTRY = {
    'sma': lambda df, params: sma(df, params.get('period', 20)),
    'ema': lambda df, params: ema(df, params.get('period', 20)),
    'rsi': lambda df, params: rsi(df, params.get('period', 14)),
    'atr': lambda df, params: atr(df, params.get('period', 14)),
    'vwap': lambda df, params: vwap(df),
    'roc': lambda df, params: roc(df, params.get('period', 10)),
}

# Multi-column indicators: added as several DataFrame columns at once, prefixed by name.
REGISTRY_MULTI = {
    'macd': lambda df, params: macd(df, params.get('fast', 12), params.get('slow', 26),
                                     params.get('signal', 9)),
    'bollinger': lambda df, params: bollinger(df, params.get('period', 20),
                                               params.get('num_std', 2.0)),
    'stochastic': lambda df, params: stochastic(df, params.get('k_period', 14),
                                                 params.get('d_period', 3)),
    'clock': lambda df, params: clock(df),
    'vs_close': lambda df, params: vs_close(df),
    'session_range': lambda df, params: session_range(df),
}


# ---- time of day and "vs the 4 pm close" (for overnight / extended-hours strategies) ----
SESSION_CODES = {None: 0, 'overnight': 1, 'premarket': 2, 'regular': 3, 'afterhours': 4}


def _et_index(df):
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:                      # backtest data without a timezone is New York time
        return idx.tz_localize('America/New_York', ambiguous='NaT', nonexistent='NaT')
    return idx.tz_convert('America/New_York')


def _sessions(et):
    """Session code of each bar, from the same calendar the trader uses (algo/runstate.py:
    NYSE holidays and early closes included)."""
    from .runstate import session_at
    out = np.full(len(et), np.nan)
    for i, t in enumerate(et):
        if not pd.isna(t):
            out[i] = SESSION_CODES[session_at(t.to_pydatetime())]
    return out


def clock(df: pd.DataFrame) -> pd.DataFrame:
    """Columns 'minute_et' (minutes after midnight New York time at the bar's start, e.g.
    19:30 = 1170), 'session' (0 closed, 1 overnight, 2 pre-market, 3 regular, 4 after-hours),
    'weekday' (0 Monday ... 4 Friday) and 'next_day_open' (1 if the NEXT calendar day is a
    trading day, 0 if it is a weekend or NYSE holiday -- e.g. 0 on the evening before
    Thanksgiving or Good Friday)."""
    from datetime import timedelta
    from .runstate import trading_day
    et = _et_index(df)
    seen = {}
    nxt = np.full(len(et), np.nan)
    for i, t in enumerate(et):
        if not pd.isna(t):
            d = t.date()
            if d not in seen:
                seen[d] = 1.0 if trading_day(d + timedelta(days=1)) else 0.0
            nxt[i] = seen[d]
    return pd.DataFrame({'minute_et': np.asarray(et.hour * 60 + et.minute, dtype='float64'),
                         'session': _sessions(et),
                         'weekday': np.asarray(et.weekday, dtype='float64'),
                         'next_day_open': nxt}, index=df.index)


def vs_close(df: pd.DataFrame) -> pd.DataFrame:
    """Columns 'prev' (the last regular-hours close: the 4 pm close once the day's regular
    session has ended, until the next one ends) and 'pct' (this bar's close vs that, in %).
    Empty until the data holds one finished regular session."""
    et = _et_index(df)
    sess = _sessions(et)
    cl = df['close'].to_numpy(dtype='float64')
    prev = np.full(len(df), np.nan)
    last_close, pending, pending_day = np.nan, np.nan, None
    for i, t in enumerate(et):
        if pd.isna(t):
            prev[i] = last_close
            continue
        day = t.date()
        reg = sess[i] == 3
        # the tracked regular session has ended at its first later non-regular bar (or a later day)
        if pending_day is not None and (day != pending_day or not reg):
            last_close, pending_day = pending, None
        if reg:
            pending, pending_day = cl[i], day
        prev[i] = last_close
    prev = pd.Series(prev, index=df.index)
    return pd.DataFrame({'prev': prev, 'pct': (df['close'] / prev - 1) * 100})


def session_range(df: pd.DataFrame) -> pd.DataFrame:
    """Columns 'high' and 'low': the highest high and lowest low of the CURRENT session (overnight,
    pre-market, regular or after-hours) up to the bar BEFORE this one, so close > session_range_high
    means a breakout. Starts again at each new session or after a gap of more than 2 hours.
    Empty on a session's first bar. Added by jb-lab (Strategy lab)."""
    et = _et_index(df)
    sess = _sessions(et)
    hi_col = df['high'].to_numpy(dtype='float64')
    lo_col = df['low'].to_numpy(dtype='float64')
    hi = np.full(len(df), np.nan)
    lo = np.full(len(df), np.nan)
    run_h, run_l, prev_s, prev_t = np.nan, np.nan, None, None
    for i, t in enumerate(et):
        s = sess[i]
        new = (prev_s is None or s != prev_s or pd.isna(t) or prev_t is None or pd.isna(prev_t)
               or (t - prev_t).total_seconds() > 7200)
        if new:
            run_h, run_l = np.nan, np.nan
        hi[i], lo[i] = run_h, run_l
        run_h = hi_col[i] if np.isnan(run_h) else max(run_h, hi_col[i])
        run_l = lo_col[i] if np.isnan(run_l) else min(run_l, lo_col[i])
        prev_s, prev_t = s, t
    return pd.DataFrame({'high': hi, 'low': lo}, index=df.index)
