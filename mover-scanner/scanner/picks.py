"""Research picks: ETFs/stocks whose daily chart shows well-known technical signals, each
explained in plain English -- ideas to look into, never buy/sell advice.

Live: about a year of DAILY bars per symbol is fetched from IBKR (Webull for any symbol IBKR
can't supply) every 5 minutes while the market is open, hourly otherwise. In between, today's
bar follows the scanner's own live regular-session prices and every signal is recomputed every
few seconds, so the picks move with the market.

Plain rules computed here -- no outside AI service, no API key. The rules come from what
popular scanners use (StockCharts' predefined scans and ChartSchool, Finviz's signals,
TradingView's technical ratings, Mark Minervini's trend template, Bollinger/TTM squeezes).
They are grouped in five families; picks are ranked by how many FAMILIES agree, so three
momentum oscillators saying the same thing count once:
  price       20-day breakout/breakdown, 52-week high/low, unfilled opening gap,
              unusually big move (2.5+ standard deviations)
  trend       20/50-day SMA cross, 50/200-day golden/death cross, price crossing its 200-day
              SMA, ADX(14) rising through 20, newly passing Minervini's trend template
  momentum    RSI(14) leaving oversold/overbought (inside the zone = heads-up only),
              MACD(12,26,9) signal-line cross, Stochastic(14,3,3) cross in its zones,
              Stochastic RSI(14,14,3,3) leaving its zones, CCI(20) through +/-100
  volatility  close outside the Bollinger Bands (20-day, 2 standard deviations), Bollinger
              squeeze (narrowest bands in ~6 months = heads-up), squeeze breakout
  volume      unusual volume (1.5x the 20-day average), Chaikin Money Flow(20) through +/-0.20

    analyze(symbol, bars, ...)  -> one pick (dict) or None   (pure function, easy to test)
    PicksEngine                 -> fetches bars, overlays live prices, publishes to the page
"""
import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo('America/New_York')
except Exception:  # pragma: no cover
    class _NewYork(__import__('datetime').tzinfo):
        # US rule: daylight saving (UTC-4) from 2 am on the 2nd Sunday of March to 2 am on the 1st Sunday of November, else UTC-5
        @staticmethod
        def _bounds(y):
            mar = datetime(y, 3, 8); nov = datetime(y, 11, 1)
            return mar + timedelta(days=(6 - mar.weekday()) % 7, hours=2), nov + timedelta(days=(6 - nov.weekday()) % 7, hours=2)
        def _dst(self, dt):      # dt = New York wall-clock time
            if dt is None: return False
            a, b = self._bounds(dt.year); w = dt.replace(tzinfo=None)
            return a <= w < b - timedelta(hours=1)
        def utcoffset(self, dt): return timedelta(hours=-4 if self._dst(dt) else -5)
        def dst(self, dt): return timedelta(hours=1 if self._dst(dt) else 0)
        def tzname(self, dt): return 'EDT' if self._dst(dt) else 'EST'
        def fromutc(self, dt):
            a, b = self._bounds(dt.year); u = dt.replace(tzinfo=None)
            on = a + timedelta(hours=5) <= u < b + timedelta(hours=4)
            return dt + timedelta(hours=-4 if on else -5)
    _ET = _NewYork()

log = logging.getLogger('picks')

from . import earnings, track  # noqa: E402

BREAKOUT_DAYS = 20
YEAR_DAYS = 250
VOLUME_MULT = 1.5
GAP_PCT = {'etfs': 1.0, 'stocks': 2.0}
SIGMA_MOVE = 2.5
SQUEEZE_LOOKBACK = 120          # ~6 months of trading days
REF = 'SPY'                     # the market, for relative strength

FAMILIES = {'price': 'price action', 'trend': 'trend', 'momentum': 'momentum',
            'volatility': 'volatility', 'volume': 'volume', 'overnight': 'overnight'}

# What each signal means, shown under it on the page (the "teach me" part).
LEARN = {
    'breakout': "When a price pushes above a level it couldn't get past for weeks, it can mean buyers "
                "are taking control. Breakouts sometimes fail and slip back, so traders watch whether it holds.",
    'breakdown': "Falling below a recent floor can mean sellers are in control. Some people watch these as "
                 "a warning; others wait for them to settle and research them as possible bargains.",
    'high52': "A price at a one-year high has strong momentum, and everyone who bought in the last year is "
              "in profit, so there are fewer sellers waiting to get their money back.",
    'low52': "A one-year low means the trend has been down for a while. It can be a bargain or a warning; "
             "the news usually explains which.",
    'gap': "A gap is when the price opens well away from the previous close, usually because of news or "
           "earnings overnight (an ETF can also gap down slightly on the day it pays a dividend). A gap that "
           "holds during the day shows conviction; one that fills back in doesn't.",
    'bigmove': "A standard deviation (sigma) measures how much something usually varies. Here it is the size "
               "of this symbol's typical daily move over the last 3 months. A move of 2.5 sigma or more is "
               "unusually large for it, often driven by news. It can start a new trend or be an overreaction.",
    'sma': "A moving average (SMA) is the average closing price over the last N days. It smooths out "
           "day-to-day noise. When the shorter average crosses above the longer one, the recent trend has "
           "turned up; crossing below means it has turned down.",
    'golden': "The \"golden cross\" (50-day average rising above the 200-day) is one of the best-known "
              "long-term signals; the \"death cross\" is the opposite. They are slow, so they confirm a trend "
              "rather than catch its start.",
    'price200': "The 200-day average is the most-watched long-term trend line. Many investors treat a price "
                "above it as a long-term uptrend and below it as a downtrend, so crossing it gets attention.",
    'adx': "ADX (Average Directional Index) measures how strong a trend is on a 0-100 scale, without saying "
           "which way. Rising above 20 suggests a real trend is starting; its +DI and -DI lines show the direction.",
    'template': "Mark Minervini's trend template is a checklist for a healthy long-term uptrend: price above "
                "its 50-, 150- and 200-day averages, those averages stacked in order with the 200-day rising, "
                "at least 30% above the 52-week low and within 25% of the high. (His relative-strength rating "
                "is left out here.)",
    'rsi': "RSI (Relative Strength Index) compares recent up-days with down-days on a 0-100 scale. Under 30 is "
           "called oversold and over 70 overbought. Climbing back above 30 suggests selling is easing; dropping "
           "back under 70 suggests buying is fading.",
    'rsizone': "RSI (Relative Strength Index) compares recent up-days with down-days on a 0-100 scale. Under 30 "
               "(oversold) the drop may be overdone; over 70 (overbought) the rise is stretched. Strong trends can "
               "stay in these zones for a while, so this is a heads-up, not a direction.",
    'macd': "MACD is the gap between a fast (12-day) and a slow (26-day) exponential moving average; its signal "
            "line is a 9-day average of that gap. MACD crossing above the signal line means momentum is turning "
            "up, below means turning down. Crossovers are common, so they matter more when other signals agree.",
    'stoch': "The Stochastic Oscillator (0-100) shows where the close sits within the last 14 days' high-low "
             "range. Under 20 is oversold, over 80 overbought. Its %K line crossing its %D (signal) line inside "
             "those zones is a classic turning-point signal. It works best when prices move sideways.",
    'stochrsi': "Stochastic RSI applies the Stochastic formula to RSI instead of price: where RSI sits within its "
                "own 14-day range (0-100). It reacts faster than RSI, so its signals come earlier but are "
                "noisier. Rising out of the bottom 20 hints momentum is turning up; falling out of the top 80, down.",
    'cci': "CCI (Commodity Channel Index) measures how far the typical price is from its 20-day average, scaled "
           "by how much it usually strays. Moving above +100 signals a strong push up; below -100 a strong push down.",
    'bb': "Bollinger Bands sit 2 standard deviations above and below the 20-day average price, so roughly 9 in "
          "10 closes stay inside them. A close outside is unusual: strong momentum, but often followed by a "
          "snap back toward the average. A 'z-score' is how many standard deviations the price is from the average.",
    'squeeze': "A Bollinger squeeze is when the bands are the narrowest they have been in about 6 months: the "
               "price has been unusually calm. Calm periods tend to end with a big move, but the squeeze doesn't "
               "say which way.",
    'squeezebreak': "A squeeze breakout is a calm period (narrow Bollinger Bands) ending with a close outside the "
                    "bands. It is a classic start of a bigger move, but watch for 'head fakes' that reverse.",
    'volume': "Volume is how many shares changed hands. A big jump means many people are suddenly paying "
              "attention, often because of news. Heavy volume behind a price move makes the move more believable.",
    'cmf': "Chaikin Money Flow (CMF) combines price and volume over 20 days to estimate whether money is flowing "
           "in (closes near the day's highs on heavy volume) or out. Above +0.20 is strong buying pressure; "
           "below -0.20 strong selling pressure.",
    'ext_gap': "Outside regular hours (after-hours, overnight, pre-market) the price can already be past "
               "the last day's high or low. If it is still there at 9:30, the stock opens with a gap. Trading "
               "outside regular hours is thin, so these prices can move fast and spreads are wider.",
    'on_hold': "The overnight session (8 pm to 4 am ET) moved the price, and pre-market trading has kept "
               "that move instead of giving it back. Moves that hold into the open tend to show real interest.",
    'on_fade': "The overnight move is being given back in pre-market trading. A fading move often means "
               "the overnight buyers (or sellers) were few, and the price may drift back toward the close.",
    'earnings': "Earnings reports come out before the open or after the close, so the biggest overnight "
                "moves usually happen right after them. Prices can jump either way."

}

# A few tickers that behave differently from what their signals suggest at first sight.
NOTES = {
    'SQQQ': 'SQQQ rises when the Nasdaq-100 falls (3x, daily). Up signals here mean the market is weak.',
    'SPXU': 'SPXU rises when the S&P 500 falls (3x, daily). Up signals here mean the market is weak.',
    'SH': 'SH rises when the S&P 500 falls. Up signals here mean the market is weak.',
    'PSQ': 'PSQ rises when the Nasdaq-100 falls. Up signals here mean the market is weak.',
    'UVXY': 'UVXY tracks expected market fear (volatility) and loses value over time. It is a short-term tool.',
    'VXX': 'VXX tracks expected market fear (volatility) and loses value over time. It is a short-term tool.',
    'TQQQ': 'TQQQ moves 3x the Nasdaq-100 each day and can lose value in choppy markets. It is a short-term tool.',
    'SOXL': 'SOXL moves 3x the chip index each day and can lose value in choppy markets.',
    'SOXS': 'SOXS rises when chip stocks fall (3x, daily).',
}


# ------------------------------------------------------------------ time helpers
def et_now():
    return datetime.now(_ET)


def regular_day(ts_ms):
    """The ET date ('YYYY-MM-DD') if ts_ms is in the regular session (Mon-Fri 9:30-16:00 ET), else None."""
    d = datetime.fromtimestamp(ts_ms / 1000, _ET)
    m = d.hour * 60 + d.minute
    return d.strftime('%Y-%m-%d') if d.weekday() < 5 and 570 <= m < 960 else None


def overnight_key(ts_ms):
    """Name of the overnight session (8 pm -> 4 am ET) a time belongs to: the date it started."""
    d = datetime.fromtimestamp(ts_ms / 1000, _ET)
    return (d.date() if d.hour >= 20 else d.date() - timedelta(days=1)).isoformat()


def ext_signals(cur, ext, mode, still_open):
    """Overnight-aware signals from the latest price outside regular hours (`ext`, the scanner's
    record) vs. the last regular day (`cur`). Only while the regular session is closed."""
    out = []
    if still_open or not ext or not ext.get('last') or time.time() * 1000 - ext.get('last_ts', 0) > 3 * 3600e3:
        return out
    if ext.get('session') not in ('pre', 'post', 'overnight'):
        return out
    last, where = ext['last'], {'pre': 'pre-market', 'post': 'after-hours', 'overnight': 'the overnight session'}[ext['session']]
    if last > cur['high']:
        out.append(('ext_gap', 1, 'Trading above the last day\'s high',
                    f"At {_money(last)} in {where}, it is above the last day's high of {_money(cur['high'])}: on course to gap up at the open.", 1.2))
    elif last < cur['low']:
        out.append(('ext_gap', -1, 'Trading below the last day\'s low',
                    f"At {_money(last)} in {where}, it is below the last day's low of {_money(cur['low'])}: on course to gap down at the open.", 1.2))
    s0, s1 = ext.get('on_start'), ext.get('on_end')
    if ext['session'] == 'pre' and s0 and s1 and ext.get('on_key') == overnight_key(ext['last_ts'] - 6 * 3600e3):
        move = (s1 - s0) / s0 * 100
        need = 0.5 if mode == 'etfs' else 1.0
        if abs(move) >= need:
            up = move > 0
            kept = (last - s0) / (s1 - s0) if s1 != s0 else 1
            if kept >= 0.9:
                out.append(('on_hold', 1 if up else -1, f"Holding its overnight {'gain' if up else 'drop'} ({move:+.1f}%)",
                            f"Overnight it went from {_money(s0)} to {_money(s1)}, and in pre-market it is still at {_money(last)}.", 1.0))
            elif kept <= 0.5:
                out.append(('on_fade', -1 if up else 1, f"Fading its overnight {'gain' if up else 'drop'} ({move:+.1f}%)",
                            f"Overnight it went from {_money(s0)} to {_money(s1)}, but pre-market has given back more than half (now {_money(last)}).", 1.0))
    return out


def market_open(now=None):
    d = now or et_now()
    m = d.hour * 60 + d.minute
    return d.weekday() < 5 and 570 <= m < 960


def bar_date_from_ms(t_ms):
    """A daily bar's ET date from its timestamp. +6h makes any convention (UTC midnight, ET
    midnight, the 9:30 open, the 4pm close) land on the right day."""
    return datetime.fromtimestamp(t_ms / 1000 + 6 * 3600, _ET).strftime('%Y-%m-%d')


# ------------------------------------------------------------------ indicators
def sma(values, n, end=None):
    """Average of the n values ending at index `end` (exclusive; default: the last one)."""
    end = len(values) if end is None else end
    if end < n or n <= 0:
        return None
    return sum(values[end - n:end]) / n


def _sma_series(values, n):
    out = [None] * len(values)
    for i in range(n - 1, len(values)):
        w = values[i - n + 1:i + 1]
        if None not in w:
            out[i] = sum(w) / n
    return out


def ema_series(values, n):
    """Standard EMA (multiplier 2/(n+1)), seeded with the SMA of the first n values; Nones
    at the start are skipped."""
    out = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), None)
    if start is None or len(values) - start < n:
        return out
    k = 2 / (n + 1)
    e = sum(values[start:start + n]) / n
    out[start + n - 1] = e
    for i in range(start + n, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def macd_series(closes, fast=12, slow=26, sig=9):
    ef, es = ema_series(closes, fast), ema_series(closes, slow)
    macd = [None if a is None or b is None else a - b for a, b in zip(ef, es)]
    return macd, ema_series(macd, sig)


def _rsi_value(ag, al):
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - 100 / (1 + ag / al)


def rsi_series(closes, n=14):
    """Wilder's RSI for every bar (None until there is enough history)."""
    out = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = _rsi_value(ag, al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = _rsi_value(ag, al)
    return out


def rsi(closes, n=14):
    return rsi_series(closes, n)[-1] if len(closes) > n else None


def _stoch_raw(vals_hi, vals_lo, vals_close, n):
    out = [None] * len(vals_close)
    for i in range(n - 1, len(vals_close)):
        hw, lw = vals_hi[i - n + 1:i + 1], vals_lo[i - n + 1:i + 1]
        if None in hw or None in lw or vals_close[i] is None:
            continue
        hh, ll = max(hw), min(lw)
        out[i] = 50.0 if hh == ll else (vals_close[i] - ll) / (hh - ll) * 100
    return out


def stoch_series(highs, lows, closes, n=14, ks=3, ds=3):
    """Slow Stochastic: %K = 3-day SMA of fast %K, %D = 3-day SMA of %K."""
    k = _sma_series(_stoch_raw(highs, lows, closes, n), ks)
    return k, _sma_series(k, ds)


def stochrsi_series(closes, rsi_n=14, n=14, ks=3, ds=3):
    r = rsi_series(closes, rsi_n)
    k = _sma_series(_stoch_raw(r, r, r, n), ks)
    return k, _sma_series(k, ds)


def bollinger(closes, end=None, n=20, mult=2.0):
    """(middle, standard deviation, upper, lower) for the n closes ending at `end`."""
    end = len(closes) if end is None else end
    if end < n:
        return None
    w = closes[end - n:end]
    m = sum(w) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return m, sd, m + mult * sd, m - mult * sd


def bandwidth_series(closes, n=20):
    """Bollinger BandWidth ((upper - lower) / middle) for every bar, via rolling sums."""
    out = [None] * len(closes)
    s = s2 = 0.0
    for i, c in enumerate(closes):
        s += c
        s2 += c * c
        if i >= n:
            o = closes[i - n]
            s -= o
            s2 -= o * o
        if i >= n - 1:
            m = s / n
            var = max(s2 / n - m * m, 0.0)
            out[i] = 4 * math.sqrt(var) / m if m else None
    return out


def adx_series(highs, lows, closes, n=14):
    """Wilder's ADX, +DI and -DI for every bar."""
    L = len(closes)
    adx, pdi, mdi = [None] * L, [None] * L, [None] * L
    tr_s = pdm_s = mdm_s = 0.0
    dxs = []
    for i in range(1, L):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm = up if up > dn and up > 0 else 0.0
        mdm = dn if dn > up and dn > 0 else 0.0
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        if i <= n:
            tr_s += tr
            pdm_s += pdm
            mdm_s += mdm
            if i < n:
                continue
        else:
            tr_s += tr - tr_s / n
            pdm_s += pdm - pdm_s / n
            mdm_s += mdm - mdm_s / n
        p = 100 * pdm_s / tr_s if tr_s else 0.0
        m = 100 * mdm_s / tr_s if tr_s else 0.0
        pdi[i], mdi[i] = p, m
        dxs.append((i, 100 * abs(p - m) / (p + m) if p + m else 0.0))
    if len(dxs) >= n:
        a = sum(d for _, d in dxs[:n]) / n
        adx[dxs[n - 1][0]] = a
        for i, dx in dxs[n:]:
            a = (a * (n - 1) + dx) / n
            adx[i] = a
    return adx, pdi, mdi


def cci_at(highs, lows, closes, end, n=20):
    if end < n:
        return None
    tps = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(end - n, end)]
    m = sum(tps) / n
    md = sum(abs(t - m) for t in tps) / n
    return 0.0 if md == 0 else (tps[-1] - m) / (0.015 * md)


def cmf_at(highs, lows, closes, vols, end, n=20):
    if end < n:
        return None
    num = den = 0.0
    for i in range(end - n, end):
        v = vols[i]
        if v is None:
            return None
        h, l, c = highs[i], lows[i], closes[i]
        num += (0.0 if h == l else ((c - l) - (h - c)) / (h - l)) * v
        den += v
    return num / den if den > 0 else None


def crossed(series, level, within=3):
    """+1 if the series rose through `level` in the last `within` bars and is still above it,
    -1 if it fell through and is still below, else 0. Returns (direction, days_ago)."""
    if not series or series[-1] is None:
        return 0, None
    now = series[-1]
    for ago in range(0, within):
        i = len(series) - 1 - ago
        if i < 1 or series[i] is None or series[i - 1] is None:
            break
        if series[i - 1] <= level < series[i] and now > level:
            return 1, ago
        if series[i - 1] >= level > series[i] and now < level:
            return -1, ago
    return 0, None


def line_cross(a, b, within=3):
    """+1/-1 if line a crossed above/below line b in the last `within` bars and is still on that
    side. Returns (direction, days_ago, index of the cross)."""
    if not a or a[-1] is None or b[-1] is None or a[-1] == b[-1]:
        return 0, None, None
    side_now = 1 if a[-1] > b[-1] else -1
    for ago in range(0, within):
        i = len(a) - 1 - ago
        if i < 1 or None in (a[i], b[i], a[i - 1], b[i - 1]):
            break
        before = a[i - 1] - b[i - 1]
        if (side_now > 0 and before <= 0 < a[i] - b[i]) or (side_now < 0 and before >= 0 > a[i] - b[i]):
            return side_now, ago, i
    return 0, None, None


# ------------------------------------------------------------------ text helpers
def _money(x):
    return f'${x:,.2f}'


def _pair(a, b):
    """Two prices that never print the same when they differ."""
    return (f'${a:,.3f}', f'${b:,.3f}') if round(a, 2) == round(b, 2) else (_money(a), _money(b))


def _vol(x):
    if x >= 0.9995e9:
        return f'{x / 1e9:.1f}B'
    if x >= 0.9995e6:
        return f'{x / 1e6:.1f}M'
    if x >= 1e3:
        return f'{x / 1e3:.0f}K'
    return f'{x:.0f}'


def _num(x, nd=1):
    return f'{x:.{nd}f}'


def _ago(n, today_word):
    if n == 0:
        return today_word
    return '1 trading day ago' if n == 1 else f'{n} trading days ago'


# ------------------------------------------------------------------ analysis
def analyze(symbol, bars, mode='etfs', today=None, ref=None, still_open=False, ext=None, earn=None):
    """bars: daily [{'date','open','high','low','close','volume'}] oldest first; the last one is
    the latest trading day (today's, still forming, while the market is open). `ref`: the
    market's (SPY) bars, for relative strength. Returns a pick or None."""
    bars = [b for b in bars or [] if b.get('close') and b['close'] > 0 and b.get('high') and b.get('low')]
    if len(bars) < BREAKOUT_DAYS + 2:
        return None
    cur, prev = bars[-1], bars[:-1]
    is_today = today is not None and cur.get('date') == today
    day = ('so far today' if still_open else 'today') if is_today else f"on {cur.get('date') or 'the latest day'}"
    day0 = 'today' if is_today else 'on the latest trading day'
    closes = [b['close'] for b in bars]
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    vols = [b.get('volume') for b in bars]
    price, prev_close = cur['close'], prev[-1]['close']
    change = (price - prev_close) / prev_close * 100
    L = len(bars)
    sigs = []

    def add(kind, family, direction, title, why, strength=1.0, learn=None):
        sigs.append({'kind': kind, 'family': family, 'dir': direction, 'title': title, 'why': why,
                     'learn': learn or kind, 'strength': round(strength, 2)})

    # ---- price action
    year = prev[-YEAR_DAYS:]
    hi20 = max(highs[-BREAKOUT_DAYS - 1:-1])
    lo20 = min(lows[-BREAKOUT_DAYS - 1:-1])
    if len(prev) >= 200 and price > max(b['high'] for b in year):
        add('high52', 'price', 1, 'New 52-week high',
            f"At {_money(price)} it is above every price of the past year (the old high was "
            f"{_money(max(b['high'] for b in year))}).", 1.5)
    elif len(prev) >= 200 and price < min(b['low'] for b in year):
        add('low52', 'price', -1, 'New 52-week low',
            f"At {_money(price)} it is below every price of the past year (the old low was "
            f"{_money(min(b['low'] for b in year))}).", 1.5)
    elif price > hi20:
        add('breakout', 'price', 1, 'Breakout to a 20-day high',
            f"At {_money(price)} it is above its highest price of the last 20 trading days ({_money(hi20)}).",
            1 + (price - hi20) / hi20 * 20)
    elif price < lo20:
        add('breakdown', 'price', -1, 'Breakdown to a 20-day low',
            f"At {_money(price)} it is below its lowest price of the last 20 trading days ({_money(lo20)}).",
            1 + (lo20 - price) / lo20 * 20)

    o = cur.get('open')
    gap_min = GAP_PCT.get(mode, 1.0)
    if o and o > 0:
        g = (o - prev_close) / prev_close * 100
        if abs(g) >= gap_min and ((g > 0 and price > prev_close) or (g < 0 and price < prev_close)):
            d = 1 if g > 0 else -1
            add('gap', 'price', d, f"Gap {'up' if d > 0 else 'down'} {abs(g):.1f}%",
                f"It opened {day0} at {_money(o)}, {abs(g):.1f}% {'above' if d > 0 else 'below'} the previous "
                f"close of {_money(prev_close)}, and is still {'above' if d > 0 else 'below'} it (the gap hasn't filled).",
                abs(g) / gap_min)

    if L >= 62:
        rets = [closes[i] / closes[i - 1] - 1 for i in range(L - 61, L - 1)]
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
        if sd > 0:
            z = (change / 100) / sd
            if abs(z) >= SIGMA_MOVE:
                d = 1 if z > 0 else -1
                add('bigmove', 'price', d, f"Unusually big move ({abs(z):.1f} sigma)",
                    f"Its {abs(change):.1f}% {'rise' if d > 0 else 'drop'} {day0} is {abs(z):.1f} times its typical daily "
                    f"move of {sd * 100:.1f}% (one standard deviation over the last 3 months). Moves this big are rare for it.",
                    abs(z) / SIGMA_MOVE)

    # ---- trend
    for fast, slow, within, kind in ((20, 50, 5, 'sma'), (50, 200, 10, 'golden')):
        if L < slow + within + 1:
            continue
        fa = [sma(closes, fast, e) for e in range(L - within - 1, L + 1)][1:]
        sl = [sma(closes, slow, e) for e in range(L - within - 1, L + 1)][1:]
        d, ago, _ = line_cross(fa, sl, within)
        if not d:
            continue
        f_now, s_now = _pair(fa[-1], sl[-1])
        if kind == 'golden':
            title = 'Golden cross (50-day above 200-day)' if d > 0 else 'Death cross (50-day below 200-day)'
        else:
            title = f"20-day average crossed {'above' if d > 0 else 'below'} the 50-day"
        add(kind, 'trend', d, title,
            f"Its {fast}-day average price ({f_now}) moved {'above' if d > 0 else 'below'} its {slow}-day average "
            f"({s_now}) {_ago(ago, 'today')}.", 1.3 if slow == 200 else 1.0)

    if L >= 204:
        p_rel = [closes[e - 1] - sma(closes, 200, e) for e in range(L - 3, L + 1)]
        d, ago = crossed(p_rel, 0, 3)
        if d:
            add('price200', 'trend', d, f"Crossed {'above' if d > 0 else 'below'} its 200-day average",
                f"The price ({_money(price)}) moved {'above' if d > 0 else 'below'} its 200-day average "
                f"({_money(sma(closes, 200))}) {_ago(ago, 'today')}.", 1.2)

    adx, pdi, mdi = adx_series(highs, lows, closes)
    d, ago = crossed(adx[-4:], 20, 3)
    if d > 0 and pdi[-1] is not None and pdi[-1] != mdi[-1]:
        up = pdi[-1] > mdi[-1]
        add('adx', 'trend', 1 if up else -1, f"New {'up' if up else 'down'}trend forming (ADX {adx[-1]:.0f})",
            f"ADX rose through 20 {_ago(ago, 'today')} (now {adx[-1]:.1f}), with {'+DI' if up else '-DI'} "
            f"({max(pdi[-1], mdi[-1]):.0f}) above {'-DI' if up else '+DI'} ({min(pdi[-1], mdi[-1]):.0f}).", 1.0)

    template_now = _template(closes, highs, lows, L)
    if template_now and L >= 232 and not any(_template(closes, highs, lows, e) for e in range(L - 10, L - 4)):
        add('template', 'trend', 1, 'Now passes the Minervini trend template',
            'It passes all 7 checks of the trend template, and it did not a week or two ago.', 1.2)

    # ---- momentum
    r = rsi_series(closes)
    if r[-1] is not None:
        d, ago = crossed(r[-4:], 30, 3)
        d2, ago2 = crossed(r[-4:], 70, 3)
        if d > 0:
            add('rsi_up', 'momentum', 1, f'RSI back above 30 (now {r[-1]:.1f})',
                f"Its 14-day RSI climbed back above 30 {_ago(ago, 'today')} after being oversold.", 1.0, 'rsi')
        elif d2 < 0:
            add('rsi_down', 'momentum', -1, f'RSI back below 70 (now {r[-1]:.1f})',
                f"Its 14-day RSI dropped back under 70 {_ago(ago2, 'today')} after being overbought.", 1.0, 'rsi')
        elif r[-1] < 30:
            add('oversold', 'momentum', 0, f'Oversold (RSI {r[-1]:.1f})',
                f'Its 14-day RSI is {r[-1]:.1f}. Under 30 means it has dropped a lot, quickly.', 1 + (30 - r[-1]) / 10, 'rsizone')
        elif r[-1] > 70:
            add('overbought', 'momentum', 0, f'Overbought (RSI {r[-1]:.1f})',
                f'Its 14-day RSI is {r[-1]:.1f}. Over 70 means it has risen a lot, quickly.', 1 + (r[-1] - 70) / 10, 'rsizone')

    m, s = macd_series(closes)
    d, ago, i = line_cross(m[-5:], s[-5:], 3)
    if d:
        zone = 'below zero (from a downtrend)' if m[-1] < 0 else 'above zero (within an uptrend)'
        add('macd', 'momentum', d, f"MACD crossed {'above' if d > 0 else 'below'} its signal line",
            f"MACD ({m[-1]:.2f}) crossed {'above' if d > 0 else 'below'} its signal line ({s[-1]:.2f}) "
            f"{_ago(ago, 'today')}, {zone}.", 1.0)

    k, dd = stoch_series(highs[-40:], lows[-40:], closes[-40:])
    d, ago, i = line_cross(k[-4:], dd[-4:], 2)
    if d:
        kb, db = k[-4:][i - 1], dd[-4:][i - 1]
        if (d > 0 and kb < 20 and db < 20) or (d < 0 and kb > 80 and db > 80):
            add('stoch', 'momentum', d, f"Stochastic turned {'up from oversold' if d > 0 else 'down from overbought'}",
                f"Its Stochastic %K ({k[-1]:.0f}) crossed {'above' if d > 0 else 'below'} %D ({dd[-1]:.0f}) "
                f"{_ago(ago, 'today')} while {'under 20' if d > 0 else 'over 80'}.", 1.0)

    k, _ = stochrsi_series(closes[-80:])
    d, ago = crossed(k[-4:], 20, 2)
    d2, ago2 = crossed(k[-4:], 80, 2)
    if d > 0:
        add('stochrsi', 'momentum', 1, 'Stochastic RSI turned up from oversold',
            f"Its Stochastic RSI rose above 20 {_ago(ago, 'today')} (now {k[-1]:.0f}).", 0.8)
    elif d2 < 0:
        add('stochrsi', 'momentum', -1, 'Stochastic RSI turned down from overbought',
            f"Its Stochastic RSI fell below 80 {_ago(ago2, 'today')} (now {k[-1]:.0f}).", 0.8)

    cci = [cci_at(highs, lows, closes, e) for e in range(L - 2, L + 1)]
    d, ago = crossed(cci, 100, 2)
    d2, ago2 = crossed(cci, -100, 2)
    if d > 0:
        add('cci', 'momentum', 1, f'CCI above +100 (now {cci[-1]:.0f})',
            f"Its 20-day CCI rose above +100 {_ago(ago, 'today')}: a strong push up.", 0.9)
    elif d2 < 0:
        add('cci', 'momentum', -1, f'CCI below -100 (now {cci[-1]:.0f})',
            f"Its 20-day CCI fell below -100 {_ago(ago2, 'today')}: a strong push down.", 0.9)

    # ---- volatility (standard deviation)
    bb = bollinger(closes)
    bw = bandwidth_series(closes)
    squeeze_days = []
    if L >= SQUEEZE_LOOKBACK + 26:
        for e in range(L - 6, L):        # was any of the last 6 days a 6-month low in BandWidth?
            past = [x for x in bw[e - SQUEEZE_LOOKBACK:e] if x is not None]
            if bw[e] is not None and past and bw[e] <= min(past):
                squeeze_days.append(e)
    if bb and bb[1] > 0:
        mid, sd, upper, lower = bb
        z = (price - mid) / sd
        recent_squeeze = any(e < L - 1 for e in squeeze_days)
        if abs(z) >= 2:
            d = 1 if z > 0 else -1
            band = f"{'upper' if d > 0 else 'lower'} Bollinger Band ({_money(upper if d > 0 else lower)})"
            if recent_squeeze:
                add('squeezebreak', 'volatility', d, f"Squeeze breakout {'up' if d > 0 else 'down'}",
                    f"After its Bollinger Bands were the narrowest in about 6 months, it closed outside the {band}, "
                    f"{abs(z):.1f} standard deviations from its 20-day average ({_money(mid)}).", 1.4)
            else:
                add('bb', 'volatility', d, f"Outside the {'upper' if d > 0 else 'lower'} Bollinger Band (z = {z:+.1f})",
                    f"At {_money(price)} it is {abs(z):.1f} standard deviations {'above' if d > 0 else 'below'} its 20-day "
                    f"average ({_money(mid)}), beyond the {band}.", 0.9 + (abs(z) - 2) / 2)
        elif squeeze_days:
            add('squeeze', 'volatility', 0, 'Bollinger squeeze (very calm)',
                f"Its Bollinger Bands are only {bw[-1] * 100:.1f}% of the price apart, about the narrowest in 6 months.", 1.0)

    # ---- volume
    vwin = [v for v in vols[-BREAKOUT_DAYS - 1:-1] if v]
    avg_vol = sum(vwin) / len(vwin) if len(vwin) >= 10 else 0
    v = cur.get('volume') or 0
    if avg_vol > 0 and v >= VOLUME_MULT * avg_vol:
        d = 1 if change > 0.1 else -1 if change < -0.1 else 0
        move = f"while the price is {'up' if change >= 0 else 'down'} {abs(change):.1f}%" if d else 'with the price about flat'
        add('volume', 'volume', d, f'Unusual volume ({v / avg_vol:.1f}x normal)',
            f"{_vol(v)} shares traded {day}, {v / avg_vol:.1f} times its 20-day average of {_vol(avg_vol)}, {move}.",
            v / avg_vol / VOLUME_MULT)

    cmf = [cmf_at(highs, lows, closes, vols, e) for e in range(L - 3, L + 1)]
    if None not in cmf:
        d, ago = crossed(cmf, 0.2, 3)
        d2, ago2 = crossed(cmf, -0.2, 3)
        if d > 0:
            add('cmf', 'volume', 1, f'Money flowing in (CMF {cmf[-1]:+.2f})',
                f"Its 20-day Chaikin Money Flow rose above +0.20 {_ago(ago, 'today')}: strong buying pressure.", 1.0)
        elif d2 < 0:
            add('cmf', 'volume', -1, f'Money flowing out (CMF {cmf[-1]:+.2f})',
                f"Its 20-day Chaikin Money Flow fell below -0.20 {_ago(ago2, 'today')}: strong selling pressure.", 1.0)

    # ---- outside regular hours: where the price is now vs. the last regular day
    for kind, d_, title, why, strength in ext_signals(cur, ext, mode, still_open):
        add(kind, 'overnight', d_, title, why, strength)
    if earn and earn.get('days') is not None and 0 <= earn['days'] <= 1:
        add('earnings', 'overnight', 0, f"Earnings {earn['label']}", f"Reports {earn['label']} ({earn['date']}{', ' + earn['when'] if earn.get('when') else ''}). Expect a big overnight move either way.", 0.8)

    if not sigs:
        return None

    # ---- how many signal FAMILIES agree
    fam_vote = {}
    for sg in sigs:
        fam_vote.setdefault(sg['family'], 0)
        fam_vote[sg['family']] += sg['dir']
    up_f = [f for f, x in fam_vote.items() if x > 0]
    dn_f = [f for f, x in fam_vote.items() if x < 0]
    lean = 'up' if len(up_f) > len(dn_f) else 'down' if len(dn_f) > len(up_f) else ('mixed' if up_f else 'watch')
    agree, conflict = max(len(up_f), len(dn_f)), min(len(up_f), len(dn_f))
    main = 1 if lean == 'up' else -1 if lean == 'down' else None
    sigs.sort(key=lambda s_: (s_['dir'] != main, s_['dir'] == 0, -s_['strength']))

    def names(fs):
        return ', '.join(FAMILIES[f] for f in fs)
    if lean == 'watch':
        summary = 'No direction yet, just a heads-up worth a look.'
    elif lean == 'mixed':
        summary = f'Up signals in {names(up_f)}, down signals in {names(dn_f)}, so the picture is mixed.'
    else:
        fs = up_f if lean == 'up' else dn_f
        word = 'up (bullish)' if lean == 'up' else 'down (bearish)'
        if agree == 1:
            summary = f'Signals point {word}, all from one kind ({names(fs)}).'
        else:
            summary = (f'{agree} different kinds of signal point {word}: {names(fs)}'
                       f'{". Others point the other way" if conflict else ""}. When different kinds agree, the pattern is stronger.')

    # ---- the bigger picture (context only, never counted)
    context = []
    s50, s200 = sma(closes, 50), sma(closes, 200)
    if s50 and s200:
        if price > s50 and price > s200:
            context.append(f'Bigger picture: uptrend. The price is above its 50-day ({_money(s50)}) and 200-day ({_money(s200)}) averages.')
        elif price < s50 and price < s200:
            context.append(f'Bigger picture: downtrend. The price is below its 50-day ({_money(s50)}) and 200-day ({_money(s200)}) averages.')
        else:
            context.append(f'Bigger picture: mixed. The price is {"above" if price > s50 else "below"} its 50-day average '
                           f'({_money(s50)}) but {"above" if price > s200 else "below"} its 200-day ({_money(s200)}).')
    elif s50:
        context.append(f'Bigger picture: the price is {"above" if price > s50 else "below"} its 50-day average ({_money(s50)}).')
    if template_now and not any(sg['kind'] == 'template' for sg in sigs):
        context.append('It passes the Minervini trend template (a healthy long-term uptrend).')
    if ref and symbol != REF:
        rc = [b['close'] for b in ref if b.get('close')]
        if len(rc) > 63 and L > 63:
            mine, mkt = (price / closes[-64] - 1) * 100, (rc[-1] / rc[-64] - 1) * 100
            context.append(f"Last 3 months: {'up' if mine >= 0 else 'down'} {abs(mine):.1f}% vs "
                           f"{'up' if mkt >= 0 else 'down'} {abs(mkt):.1f}% for the S&P 500 (SPY), so "
                           f"{'stronger' if mine > mkt else 'weaker'} than the market.")

    return {'symbol': symbol, 'price': round(price, 4), 'change_pct': round(change, 2), 'lean': lean,
            'agree': agree, 'conflict': conflict, 'up_families': up_f, 'down_families': dn_f,
            'summary': summary, 'signals': sigs, 'context': context, 'note': NOTES.get(symbol, ''),
            'score': round(sum(sg['strength'] for sg in sigs), 2), 'date': cur.get('date'),
            'ext': ({'price': ext['last'], 'session': ext['session'], 'vs_close': round((ext['last'] - price) / price * 100, 2)}
                    if ext and not still_open and ext.get('last') and ext.get('session') in ('pre', 'post', 'overnight') else None),
            'earnings': earn}


def _template(closes, highs, lows, end):
    """Minervini's trend template (minus his RS rating) at bar index end-1."""
    if end < 221:
        return False
    p = closes[end - 1]
    s50, s150, s200 = sma(closes, 50, end), sma(closes, 150, end), sma(closes, 200, end)
    s200_then = sma(closes, 200, end - 21)
    hi, lo = max(highs[max(0, end - 252):end]), min(lows[max(0, end - 252):end])
    return (p > s150 and p > s200 and s150 > s200 and s200 > s200_then and s50 > s150 and s50 > s200
            and p > s50 and p >= 0.75 * hi and p >= 1.30 * lo)


def rank(picks):
    """Most signal kinds in agreement first, then cleanest (fewest disagreeing), then strongest."""
    return sorted(picks, key=lambda p: (-p['agree'], p['conflict'], -len(p['signals']), -p['score'], p['symbol']))


# ------------------------------------------------------------------ engine
def _describe(e):
    """A short, secret-free description of a fetch error."""
    text = ' '.join(str(e).split())[:120]
    return f'{type(e).__name__}: {text}' if text else type(e).__name__


class PicksEngine:
    """Fetches daily bars (`sources` = [(name, async fetch(symbol))], preferred first), keeps
    today's bar in step with live prices (`live(symbol)` -> the scanner's regular-session
    record for the symbol) and hands ranked picks to `publish` whenever they change."""
    FETCH_OPEN_S = 5 * 60
    FETCH_CLOSED_S = 60 * 60
    LIVE_S = 3

    def __init__(self, symbols, mode, sources, publish, live=None, ext=None):
        self.ext = ext or (lambda sym: None)
        self.symbols = list(symbols)
        self.mode = mode
        self.sources = list(sources)
        self.publish = publish
        self.live = live or (lambda sym: None)
        self.fetch_list = self.symbols + ([] if REF in self.symbols else [REF])
        self.bars, self.used, self.fetched_at = {}, {}, {}
        self.history_at = None
        self.fail_streak = 0
        self._last = None
        self.state = {'picks': [], 'updated': None, 'n_symbols': len(self.symbols), 'n_history': 0,
                      'sources': {}, 'live': False, 'history_at': None, 'learn': LEARN, 'families': FAMILIES,
                      'status': 'Working out picks: fetching a year of daily prices for each symbol…'}

    async def _fetch(self, sym):
        problems = []
        order = sorted(self.sources, key=lambda s: s[0] != self.used.get(sym))   # keep a symbol on one source
        for name, fetch in order:
            try:
                rows = await asyncio.wait_for(fetch(sym), 60)
            except Exception as e:
                problems.append(f'{name}: {_describe(e)}')
                continue
            if rows and len(rows) >= BREAKOUT_DAYS + 2:
                return name, rows
            problems.append(f'{name}: no data')
        log.info('picks: no daily history for %s (%s)', sym, '; '.join(problems) or 'no source available')
        return None, None

    async def fetch_all(self):
        fresh = 0
        for sym in self.fetch_list:
            name, rows = await self._fetch(sym)
            if rows:
                self.bars[sym], self.used[sym], self.fetched_at[sym] = rows, name, time.time()
                fresh += sym in self.symbols
            await asyncio.sleep(0.3)        # gentle on IBKR's / Webull's request limits
        self.history_at = time.time()
        counts = {}
        for s in self.symbols:
            if s in self.used:
                counts[self.used[s]] = counts.get(self.used[s], 0) + 1
        log.info('picks: daily history refreshed for %d/%d symbols %s', fresh, len(self.symbols), counts)
        return fresh

    def _effective(self, sym, today):
        """The fetched bars with today's bar following the live regular-session price."""
        bars = self.bars.get(sym)
        rec = self.live(sym)
        if not bars or not rec or rec.get('day') != today:
            return bars
        if rec['last_ts'] / 1000 <= self.fetched_at.get(sym, 0) and bars[-1].get('date') == today:
            return bars                     # the fetched bar is newer than the last live price
        last = bars[-1]
        if last.get('date') == today:
            b = dict(last, close=rec['last'], high=max(last['high'], rec['last']), low=min(last['low'], rec['last']))
            return bars[:-1] + [b]
        if last.get('date') and last['date'] < today:
            near_open = (datetime.fromtimestamp(rec['open_ts'] / 1000, _ET).hour * 60
                         + datetime.fromtimestamp(rec['open_ts'] / 1000, _ET).minute) < 572
            b = {'date': today, 'open': rec['open'] if near_open else None, 'high': rec['high'],
                 'low': rec['low'], 'close': rec['last'], 'volume': None}
            return bars + [b]
        return bars

    def compute(self):
        now = et_now()
        today, is_open = now.strftime('%Y-%m-%d'), market_open(now)
        ref = self._effective(REF, today)
        picks, live_n = [], 0
        for sym in self.symbols:
            rec = self.live(sym)
            if is_open and rec and rec.get('day') == today and time.time() - rec['last_ts'] / 1000 < 120:
                live_n += 1
            try:
                p = analyze(sym, self._effective(sym, today), self.mode, today, ref, is_open, self.ext(sym), earnings.get(sym))
            except Exception as e:          # one odd symbol must never break the rest
                log.error('picks: could not analyse %s: %r', sym, e)
                p = None
            if p:
                p['source'] = self.used.get(sym)
                picks.append(p)
        n = sum(1 for s in self.symbols if s in self.bars)
        stale_s = 20 * 60 if is_open else 3 * 3600
        stale = sum(1 for s in self.symbols if s in self.bars and time.time() - self.fetched_at[s] > stale_s)
        counts = {}
        for s in self.symbols:
            if s in self.used:
                counts[self.used[s]] = counts.get(self.used[s], 0) + 1
        status = ''
        if not n:
            status = ("Couldn't get daily price history from IBKR or Webull yet, so there are no picks. "
                      "It keeps trying. Is IB Gateway logged in on the Mac?")
        elif stale:
            status = f"Daily history couldn't be refreshed lately for {stale} of them, so their picks may lag."
        try:
            track.save_if_due(now, picks)
            record = track.score(self.bars)
        except Exception as e:
            log.error('picks: track record failed: %r', e)
            record = None
        upcoming = []
        for sym in self.symbols:
            e = earnings.get(sym)
            if e and e.get('days') is not None:
                upcoming.append({'symbol': sym, 'date': e['date'], 'when': e.get('when', ''), 'days': e['days'], 'label': e['label']})
        upcoming.sort(key=lambda x: (x['days'], x['symbol']))
        return {'picks': rank(picks), 'track': record, 'upcoming': upcoming, 'earnings_status': earnings.status(), 'n_symbols': len(self.symbols), 'n_history': n, 'sources': counts,
                'live': bool(is_open and live_n), 'history_at': int(self.history_at * 1000) if self.history_at else None,
                'learn': LEARN, 'families': FAMILIES, 'status': status}

    def last_close(self, sym):
        """The last completed day's close from the daily bars (for 'vs close' after a restart)."""
        bars = self.bars.get(sym)
        if not bars:
            return None
        today = et_now().strftime('%Y-%m-%d')
        done = [b for b in bars if b.get('date') != today or not market_open()]
        if market_open() and bars[-1].get('date') == today and len(bars) > 1:
            return bars[-2]['close']
        return done[-1]['close'] if done else None

    def _fetch_wait(self, fresh):
        d = et_now()
        m = d.hour * 60 + d.minute
        if d.weekday() < 5 and 565 <= m <= 995:            # 9:25 am - 4:35 pm ET
            wait = self.FETCH_OPEN_S
        else:
            nxt = d.replace(hour=9, minute=25, second=0, microsecond=0)
            if nxt <= d:
                nxt += timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            wait = max(60, min(self.FETCH_CLOSED_S, (nxt - d).total_seconds()))
        if not fresh:
            self.fail_streak += 1
            return min(wait, 60 * min(2 ** (self.fail_streak - 1), 15))   # 1, 2, 4, 8, 15 min
        self.fail_streak = 0
        return min(wait, 120) if fresh < len(self.symbols) else wait

    async def _fetch_loop(self):
        await asyncio.sleep(15)            # let the feeds connect first
        while True:
            try:
                fresh = await self.fetch_all()
            except Exception as e:
                log.error('picks: history refresh failed: %r', e)
                fresh = 0
            try:
                await self._publish_if_changed()
            except Exception as e:
                log.error('picks: update failed: %r', e)
            await asyncio.sleep(self._fetch_wait(fresh))

    async def _publish_if_changed(self):
        state = self.compute()
        key = repr((state['picks'], state['n_history'], state['status'], state['live'], state['history_at'], state['upcoming'], state['track']))
        if key != self._last:
            self._last = key
            state['updated'] = int(time.time() * 1000)
            self.state = state
            self.publish(state)

    async def _live_loop(self):
        while True:
            await asyncio.sleep(self.LIVE_S if market_open() else 30)
            if not self.bars:
                continue
            try:
                await self._publish_if_changed()
            except Exception as e:
                log.error('picks: live update failed: %r', e)

    async def run(self):
        earnings.start(self.symbols)
        await asyncio.gather(self._fetch_loop(), self._live_loop())
