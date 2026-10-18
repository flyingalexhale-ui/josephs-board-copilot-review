"""Minute picks: the same idea as the daily picks (scanner/picks.py), on 1-minute and 5-minute
bars, rechecked every few seconds in EVERY session -- pre-market, regular hours, after-hours
and overnight. Shown on the Scanner's Picks view under "1 min" and "5 min".

Bars are built from the scanner's own live prices (every tick it receives), and at start-up
the last two days of 1-minute bars (4 am - 8 pm ET) are loaded from IBKR when it is connected,
so the signals have history right away. Overnight bars come from the live overnight prices.

Signals, by family (counted the same way as the daily picks: different families agreeing
make a stronger pick):
  price       new 20-bar high / low, unusually big bar (3+ standard deviations)
  trend       EMA 9 / 21 cross, price crossing the session VWAP
  momentum    RSI(14) crossing 50, RSI under 25 / over 75 (heads-up), MACD(12,26,9) cross
  volatility  close outside the Bollinger Bands (20 bars, 2 sd), squeeze (narrowest in 100 bars)
  volume      a bar with 3x the average volume of the last 30 bars
"""
import asyncio
import logging
import math
import time
from datetime import datetime

from .picks import (_ET, FAMILIES, NOTES, _money, bandwidth_series, bollinger, crossed, ema_series,
                    line_cross, macd_series, rank, rsi_series)
from . import earnings

log = logging.getLogger('picks')
KEEP_MIN = 2 * 24 * 60            # keep two days of 1-minute bars
TFS = (1, 5)

LEARN = {
    'm_breakout': "The price just went above the highest price of the last 20 bars. On minute charts this "
                  "often marks a burst of buying; it can also fail and slip straight back, so watch if it holds.",
    'm_breakdown': "The price just went below the lowest price of the last 20 bars: sellers pushing. "
                   "It can keep going or snap back quickly.",
    'm_bigmove': "This bar moved at least 3 standard deviations, i.e. 3 times its normal bar-to-bar wobble "
                 "over the last hour or so. Something just happened: often news.",
    'm_ema': "An EMA is an average that leans toward the most recent prices. When the fast one (9 bars) "
             "crosses the slow one (21 bars), the short-term direction has changed.",
    'm_vwap': "VWAP is the average price paid in this session, weighted by volume. Many traders treat price "
              "above VWAP as buyers in control and below as sellers in control, so crossing it gets noticed.",
    'm_rsi': "RSI compares recent up-bars with down-bars on a 0-100 scale. Crossing 50 means momentum has "
             "flipped. Under 25 or over 75 is stretched: a heads-up, not a direction.",
    'm_macd': "MACD is the gap between a fast and a slow average; crossing its signal line means momentum "
              "is turning. On minute bars it fires often, so it matters most when other signals agree.",
    'm_bb': "Bollinger Bands sit 2 standard deviations around the 20-bar average, so most bars close inside. "
            "A close outside is a strong push that often snaps back toward the average.",
    'm_squeeze': "The bands are the narrowest in 100 bars: the price has gone unusually quiet. Quiet spells "
                 "often end with a sharp move, in either direction.",
    'm_volume': "Far more shares traded in this bar than usual. Volume behind a move makes it more believable; "
                "outside regular hours even a modest burst stands out.",
}

SESSION_WORD = {'pre': 'pre-market', 'regular': 'regular hours', 'post': 'after-hours', 'overnight': 'overnight'}


def session_of(ts_ms):
    d = datetime.fromtimestamp(ts_ms / 1000, _ET)
    m = d.hour * 60 + d.minute
    if m >= 1200 or m < 240:
        return 'overnight'
    if m < 570:
        return 'pre'
    return 'regular' if m < 960 else 'post'


def session_start(ts_ms):
    """Start (ms) of the session ts_ms belongs to, for the VWAP."""
    d = datetime.fromtimestamp(ts_ms / 1000, _ET)
    m = d.hour * 60 + d.minute
    for start in (1200, 960, 570, 240):
        if m >= start:
            return int(d.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0).timestamp() * 1000)
    # 0:00 - 4:00 belongs to the overnight session that started at 8 pm the day before
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000) - 4 * 3600 * 1000


def resample(bars, tf):
    """1-minute bars [t, o, h, l, c, v] -> tf-minute bars (the last one may still be forming)."""
    if tf == 1:
        return bars
    out, step = [], tf * 60000
    for b in bars:
        t = b[0] // step * step
        if out and out[-1][0] == t:
            o = out[-1]
            o[2], o[3], o[4], o[5] = max(o[2], b[2]), min(o[3], b[3]), b[4], o[5] + b[5]
        else:
            out.append([t, b[1], b[2], b[3], b[4], b[5]])
    return out


def analyze(symbol, bars, tf, vs_close=None, earn=None):
    """bars: [t_ms, open, high, low, close, volume] oldest first, tf-minute; the last may still be
    forming. Returns a pick in the same shape as the daily picks, or None."""
    if len(bars) < 40:
        return None
    unit = f'{tf}-minute'
    span = lambda n: f'{n * tf} minutes' if n * tf < 120 else f'{n * tf / 60:g} hours'
    closes = [b[4] for b in bars]
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    vols = [b[5] for b in bars]
    price, t_last = closes[-1], bars[-1][0]
    where = SESSION_WORD[session_of(t_last)]
    sigs = []

    def add(kind, family, direction, title, why, strength=1.0):
        sigs.append({'kind': kind, 'family': family, 'dir': direction, 'title': title, 'why': why,
                     'learn': kind, 'strength': round(strength, 2)})

    # price
    hi20, lo20 = max(highs[-21:-1]), min(lows[-21:-1])
    if price > hi20:
        add('m_breakout', 'price', 1, f'New 20-bar high ({where})',
            f'{_money(price)} is above the highest price of the last 20 {unit} bars ({span(20)}), {_money(hi20)}.')
    elif price < lo20:
        add('m_breakdown', 'price', -1, f'New 20-bar low ({where})',
            f'{_money(price)} is below the lowest price of the last 20 {unit} bars ({span(20)}), {_money(lo20)}.')
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(max(1, len(closes) - 61), len(closes) - 1)]
    if len(rets) >= 30:
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
        last = (closes[-1] - closes[-2]) / closes[-2]
        if sd > 0 and abs(last) >= 3 * sd and abs(last) >= 0.002:
            add('m_bigmove', 'price', 1 if last > 0 else -1, f'Big {unit} move ({last * 100:+.2f}%)',
                f'This bar moved {abs(last) / sd:.1f} standard deviations, far more than its usual {sd * 100:.2f}% per bar.', 1.2)
    # trend
    d, ago, _ = line_cross(ema_series(closes, 9), ema_series(closes, 21), within=2)
    if d:
        add('m_ema', 'trend', d, f"EMA 9 crossed {'above' if d > 0 else 'below'} EMA 21",
            f"The fast average turned {'up' if d > 0 else 'down'} through the slow one on the {unit} chart"
            f"{' just now' if ago == 0 else f', {ago} bar ago'}.")
    s0 = session_start(t_last)
    idx = [i for i, b in enumerate(bars) if b[0] >= s0]
    if len(idx) >= 3:
        pv = v = 0.0
        vw = []
        for i in idx:
            tp = (highs[i] + lows[i] + closes[i]) / 3
            pv += tp * vols[i]
            v += vols[i]
            vw.append(pv / v if v > 0 else None)
        sub = closes[idx[0]:]
        if vw[-1] is not None:
            d, ago, _ = line_cross(sub, vw, within=2)
            if d:
                add('m_vwap', 'trend', d, f"Crossed {'above' if d > 0 else 'below'} VWAP ({where})",
                    f"{_money(price)} vs. this session's VWAP of {_money(vw[-1])}: "
                    f"{'buyers' if d > 0 else 'sellers'} have the upper hand for now.")
    # momentum
    r = rsi_series(closes, 14)
    d, ago = crossed(r, 50, within=2)
    if d:
        add('m_rsi', 'momentum', d, f"RSI crossed {'above' if d > 0 else 'below'} 50",
            f'RSI(14) on the {unit} chart is {r[-1]:.0f}: momentum has turned {"up" if d > 0 else "down"}.', 0.8)
    elif r[-1] is not None and (r[-1] < 25 or r[-1] > 75):
        add('m_rsi', 'momentum', 0, f"RSI {'oversold' if r[-1] < 25 else 'overbought'} ({r[-1]:.0f})",
            f"RSI(14) on the {unit} chart is {r[-1]:.0f}: the {'drop' if r[-1] < 25 else 'rise'} is stretched.", 0.6)
    mac, sig = macd_series(closes)
    d, ago, _ = line_cross(mac, sig, within=2)
    if d:
        add('m_macd', 'momentum', d, f"MACD crossed {'above' if d > 0 else 'below'} its signal",
            f"Momentum on the {unit} chart is turning {'up' if d > 0 else 'down'}.", 0.8)
    # volatility
    bb = bollinger(closes)
    if bb:
        mid, sd, up, lo = bb
        if price > up or price < lo:
            d = 1 if price > up else -1
            add('m_bb', 'volatility', d, f"Closed {'above' if d > 0 else 'below'} the Bollinger Band",
                f'{_money(price)} is {abs(price - mid) / sd:.1f} standard deviations from the 20-bar average {_money(mid)}.', 0.9)
    bw = [x for x in bandwidth_series(closes)[-100:] if x is not None]
    if len(bw) >= 60 and bw[-1] <= min(bw) * 1.0001:
        add('m_squeeze', 'volatility', 0, 'Squeeze: unusually quiet',
            f'The Bollinger Bands are the narrowest in the last {span(len(bw))}. A sharp move often follows.', 0.6)
    # volume
    prev = [x for x in vols[-31:-1] if x]
    if len(prev) >= 15 and vols[-1]:
        avg = sum(prev) / len(prev)
        if avg > 0 and vols[-1] >= 3 * avg:
            mv = closes[-1] - bars[-1][1]
            add('m_volume', 'volume', 1 if mv > 0 else -1 if mv < 0 else 0, f'Volume burst ({vols[-1] / avg:.1f}x)',
                f'{vols[-1]:,.0f} shares in this {unit} bar vs. about {avg:,.0f} usually ({where}).', 1.0)
    if not sigs:
        return None

    vote = {}
    for s in sigs:
        vote[s['family']] = vote.get(s['family'], 0) + s['dir']
    up_f = [f for f, x in vote.items() if x > 0]
    dn_f = [f for f, x in vote.items() if x < 0]
    lean = 'up' if len(up_f) > len(dn_f) else 'down' if len(dn_f) > len(up_f) else ('mixed' if up_f else 'watch')
    agree, conflict = max(len(up_f), len(dn_f)), min(len(up_f), len(dn_f))
    main = 1 if lean == 'up' else -1 if lean == 'down' else None
    sigs.sort(key=lambda s_: (s_['dir'] != main, s_['dir'] == 0, -s_['strength']))
    names = lambda fs: ', '.join(FAMILIES[f] for f in fs)
    if lean == 'watch':
        summary = f'No direction yet on the {unit} chart, just a heads-up.'
    elif lean == 'mixed':
        summary = f'Up signals in {names(up_f)}, down signals in {names(dn_f)}: mixed on the {unit} chart.'
    else:
        fs = up_f if lean == 'up' else dn_f
        word = 'up' if lean == 'up' else 'down'
        summary = (f'Signals point {word} on the {unit} chart, all from one kind ({names(fs)}).' if agree == 1 else
                   f'{agree} different kinds of signal point {word} on the {unit} chart: {names(fs)}'
                   f'{". Others point the other way" if conflict else ""}.')
    context = [f'Session: {where}. Last {unit} bar at '
               f"{datetime.fromtimestamp(t_last / 1000, _ET).strftime('%-I:%M %p').lower()} ET."]
    return {'symbol': symbol, 'price': round(price, 4), 'change_pct': round(vs_close, 2) if vs_close is not None else 0.0,
            'lean': lean, 'agree': agree, 'conflict': conflict, 'up_families': up_f, 'down_families': dn_f,
            'summary': summary, 'signals': sigs, 'context': context, 'note': NOTES.get(symbol, ''),
            'score': round(sum(s['strength'] for s in sigs), 2), 'ext': None, 'earnings': earn}


class MinutePicks:
    """Builds 1-minute bars from every tick and publishes 1-min and 5-min picks every few seconds."""
    LIVE_S = 3
    FRESH_MIN = {1: 10, 5: 20}     # a symbol whose last trade is older than this is left out

    def __init__(self, symbols, publish, vs_close=None, seed=None):
        self.symbols = list(symbols)
        self.publish = publish               # publish(tf, state)
        self.vs_close = vs_close or (lambda s: None)
        self.seed_fetch = seed               # async fetch(symbol) -> [[t, o, h, l, c, v], ...] or None
        self.bars = {s: [] for s in self.symbols}
        self._last = {}
        self.seeded = 0

    def add_tick(self, sym, price, ts_ms, volume=0.0):
        bars = self.bars.get(sym)
        if bars is None or not price or price <= 0:
            return
        t = int(ts_ms) // 60000 * 60000
        if bars and t < bars[-1][0]:
            return
        if bars and bars[-1][0] == t:
            b = bars[-1]
            b[2], b[3], b[4], b[5] = max(b[2], price), min(b[3], price), price, b[5] + (volume or 0)
        else:
            bars.append([t, price, price, price, price, volume or 0.0])
            if len(bars) > KEEP_MIN + 60:
                del bars[:-KEEP_MIN]

    async def _seed(self):
        if not self.seed_fetch:
            return
        await asyncio.sleep(20)                  # let the feed connect first
        for sym in self.symbols:
            try:
                rows = await asyncio.wait_for(self.seed_fetch(sym), 60)
            except Exception as e:
                log.info('minute picks: no history for %s (%r)', sym, e)
                rows = None
            if rows:
                live = self.bars[sym]
                first = live[0][0] if live else None
                old = [r for r in rows if first is None or r[0] < first]
                self.bars[sym] = old + live
                self.seeded += 1
            await asyncio.sleep(0.5)
        log.info('minute picks: 1-minute history loaded for %d/%d symbols', self.seeded, len(self.symbols))

    def compute(self, tf):
        now = time.time() * 1000
        picks, fresh = [], 0
        for sym in self.symbols:
            bars = self.bars.get(sym) or []
            if not bars or now - bars[-1][0] > self.FRESH_MIN[tf] * 60000:
                continue
            fresh += 1
            try:
                p = analyze(sym, resample(bars, tf)[-400:], tf, self.vs_close(sym), earnings.get(sym))
            except Exception as e:
                log.error('minute picks: could not analyse %s: %r', sym, e)
                p = None
            if p:
                picks.append(p)
        sess = SESSION_WORD[session_of(now)]
        status = '' if fresh else ('No live prices right now (market closed, or the feeds are reconnecting), '
                                   'so there are no minute picks.')
        return {'tf': tf, 'picks': rank(picks), 'n_symbols': len(self.symbols), 'n_live': fresh, 'session': sess,
                'learn': LEARN, 'families': FAMILIES, 'status': status, 'live': bool(fresh)}

    async def run(self):
        asyncio.ensure_future(self._seed())
        while True:
            await asyncio.sleep(self.LIVE_S)
            for tf in TFS:
                try:
                    st = self.compute(tf)
                    key = repr((st['picks'], st['status'], st['n_live']))
                    if key != self._last.get(tf):
                        self._last[tf] = key
                        st['updated'] = int(time.time() * 1000)
                        self.publish(tf, st)
                except Exception as e:
                    log.error('minute picks failed: %r', e)
