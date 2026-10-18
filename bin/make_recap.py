"""Makes the "since the close" recap video (jb-recap): a 2-3 minute narrated video of what the
board's stocks and futures did after-hours, overnight and pre-market, saved in data/recaps/ and
announced on the phone through ntfy (the same private topic as the alerts).

Runs on its own (the gateway starts it at JB_RECAP_TIME on trading days, default 8:05 am New
York time, or when you press "Make one now" on the Recap tab). By hand, from ~/josephs-board:
    .venv/bin/python bin/make_recap.py            make one now and send it to the phone
    .venv/bin/python bin/make_recap.py --no-push  make one now, don't send it
    .venv/bin/python bin/make_recap.py --weekly   the weekly recap (jb-extras): the week's best and
                                                  worst, the market weather, how the picks and your
                                                  trades did, and next week's earnings and events.
                                                  The gateway makes it every Friday at JB_WEEKLY_TIME.

What it uses, all already on the Mac:
  * prices: the board's own minute-by-minute record, data/ibkr_ticks/ (preferred, it has
    overnight) and data/webull_ticks/ (fills in symbols IBKR doesn't have; no overnight)
  * volume: data/ibkr_volume.json and data/webull_volume.json (what this Mac saw, not official)
  * your holdings: data/holdings.json (Portfolio tab)
  * the voice: the Mac's built-in "say" command; the video: ffmpeg from the imageio-ffmpeg
    package (installed by the jb-recap install). Pictures are drawn with matplotlib.

Sessions (New York time): after-hours 4-8 pm and pre-market 4-9:30 am go through IBKR's normal
SMART route; overnight 8 pm-3:50 am goes through IBKR's overnight venue (Blue Ocean)."""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timedelta, time as dtime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
OUT = os.path.join(DATA, 'recaps')
KEEP = 14                                    # recaps kept (oldest deleted)
KEEP_WEEKLY = 8                              # weekly recaps kept
W, H, DPI = 1280, 720, 100
FPS_STEPS = 24                               # picture changes per scene (the line draws itself)

sys.path.insert(0, os.path.join(ROOT, 'ibkr-algo'))
from algo.runstate import trading_day, nyse_early_closes      # the NYSE calendar (holidays, early closes)
from zoneinfo import ZoneInfo
ET = ZoneInfo('America/New_York')

# Names the voice says instead of spelling the ticker out
NAMES = {'ES=F': 'S and P futures', 'NQ=F': 'Nasdaq futures', 'YM=F': 'Dow futures', 'RTY=F': 'Russell futures',
         'CL=F': 'oil', 'GC=F': 'gold', 'SI=F': 'silver', 'NG=F': 'natural gas', 'ZB=F': 'bond futures',
         'AAPL': 'Apple', 'MSFT': 'Microsoft', 'NVDA': 'Nvidia', 'AMZN': 'Amazon', 'GOOGL': 'Google',
         'GOOG': 'Google', 'META': 'Meta', 'TSLA': 'Tesla', 'AMD': 'A M D', 'NFLX': 'Netflix', 'INTC': 'Intel',
         'AVGO': 'Broadcom', 'ORCL': 'Oracle', 'CRM': 'Salesforce', 'ADBE': 'Adobe', 'PLTR': 'Palantir',
         'UBER': 'Uber', 'LLY': 'Eli Lilly', 'UNH': 'United Health', 'XOM': 'Exxon', 'JPM': 'J P Morgan',
         'BAC': 'Bank of America', 'WMT': 'Walmart', 'COST': 'Costco', 'DIS': 'Disney', 'BA': 'Boeing',
         'COIN': 'Coinbase', 'MSTR': 'MicroStrategy', 'SMCI': 'Super Micro', 'MU': 'Micron', 'ARM': 'Arm',
         'SPY': 'the S and P E T F', 'QQQ': 'the Nasdaq E T F', 'IWM': 'the Russell E T F', 'DIA': 'the Dow E T F'}
FUT_SHORT = {'ES=F': 'S&P fut', 'NQ=F': 'Nasdaq fut', 'YM=F': 'Dow fut', 'RTY=F': 'Russell fut',
             'CL=F': 'Oil', 'GC=F': 'Gold'}

BG, PANEL, LINE, WHITE, DIM = '#04101f', '#071a30', '#164060', '#f2f2f2', '#6f8aa3'
GREEN, RED, CYAN, AMBER, PINK = '#00ff41', '#ff3b30', '#00e5ff', '#ffb648', '#ff8bd1'
SESSION_COLOR = {'afterhours': '#8a7dff', 'overnight': CYAN, 'premarket': AMBER, 'regular': '#5fc97e'}


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S ') + msg, flush=True)


def is_future(sym):
    return sym.endswith('=F')


def spoken(sym):
    return NAMES.get(sym) or ' '.join(sym.replace('=F', '').replace('.', ' '))


def say_pct(v):
    if v is None:
        return 'no change recorded'
    if abs(v) < 0.05:
        return 'about flat'
    return f"{'up' if v > 0 else 'down'} {abs(v):.1f} percent"


def fmt_pct(v):
    return '—' if v is None else f'{v:+.2f}%'


def ms(dt):
    return int(dt.timestamp() * 1000)


def at(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET)


def clock(dt):
    s = dt.strftime('%I:%M %p').lstrip('0').lower()
    return s.replace(':00 ', ' ')


# ------------------------------------------------------------------ which hours to cover
def windows(now):
    """The sessions since the last regular close, in order, each clipped to `now`:
    [(key, title, venue, start_dt, end_dt)], plus the close time and the next trading day."""
    day = now.date()
    # the last regular close that has passed
    while True:
        if trading_day(day):
            close = at(day, 13 if day in nyse_early_closes(day.year) else 16)
            if close <= now:
                break
        day -= timedelta(days=1)
    nxt = day + timedelta(days=1)
    while not trading_day(nxt):
        nxt += timedelta(days=1)
    ah_end = close + timedelta(hours=4)
    on_start, on_end = at(nxt - timedelta(days=1), 20), at(nxt, 3, 50)
    pm_start, pm_end = at(nxt, 4), at(nxt, 9, 30)
    nxt_close = at(nxt, 13 if nxt in nyse_early_closes(nxt.year) else 16)
    out = [('afterhours', 'AFTER-HOURS', "SMART · IBKR's normal route", close, ah_end),
           ('overnight', 'OVERNIGHT', "Blue Ocean · IBKR's overnight venue", on_start, on_end),
           ('premarket', 'PRE-MARKET', "SMART · IBKR's normal route", pm_start, pm_end),
           ('regular', 'REGULAR HOURS SO FAR', 'SMART · all exchanges', pm_end, nxt_close)]
    out = [(k, t, v, s, min(e, now)) for k, t, v, s, e in out if s < now]
    return out, close, nxt


# ------------------------------------------------------------------ the board's records
def load_ticks(broker, start_ms, end_ms):
    """{sym: [(minute_ms, price), ...]} from data/<broker>_ticks/<UTC date>.csv."""
    folder = os.path.join(DATA, f'{broker}_ticks')
    out = {}
    d0 = datetime.fromtimestamp(start_ms / 1000, ZoneInfo("UTC")).date()
    d1 = datetime.fromtimestamp(end_ms / 1000, ZoneInfo("UTC")).date()
    d = d0
    while d <= d1:
        p = os.path.join(folder, d.isoformat() + '.csv')
        d += timedelta(days=1)
        try:
            with open(p, encoding='utf-8', errors='replace') as f:
                for line in f:
                    parts = line.strip().split(',')
                    if len(parts) != 3:
                        continue
                    try:
                        t, px = int(parts[1]), float(parts[2])
                    except ValueError:
                        continue
                    if start_ms <= t <= end_ms and px > 0:
                        out.setdefault(parts[0], {})[t] = px
        except OSError:
            continue
    return {s: sorted(v.items()) for s, v in out.items()}


def load_volume(broker):
    try:
        with open(os.path.join(DATA, f'{broker}_volume.json')) as f:
            return {s: dict((int(m), float(v)) for m, v in rows) for s, rows in (json.load(f) or {}).items()}
    except Exception:
        return {}


def load_watchlist(broker):
    try:
        with open(os.path.join(DATA, f'{broker}_watchlist.json')) as f:
            v = json.load(f)
            return [s for s in v if isinstance(s, str)] if isinstance(v, list) else []
    except Exception:
        return []


def holdings():
    """{sym: (shares, avg cost)} from the Portfolio tab's trades."""
    try:
        with open(os.path.join(DATA, 'holdings.json')) as f:
            trades = sorted(json.load(f) or [], key=lambda t: (t['date'], t.get('created', 0)))
    except Exception:
        return {}
    pos = {}
    for t in trades:
        try:
            q, px = float(t['qty']), float(t['price'])
        except (KeyError, TypeError, ValueError):
            continue
        sh, cost = pos.get(t.get('sym'), (0.0, 0.0))
        if q > 0:
            sh, cost = sh + q, cost + q * px
        elif sh > 0:
            sell = min(-q, sh)
            cost -= sell * cost / sh
            sh -= sell
        pos[t.get('sym')] = (sh, cost)
    return {s: (sh, cost / sh) for s, (sh, cost) in pos.items() if s and sh > 1e-9}


def price_before(series, t):
    best = None
    for tt, p in series:
        if tt >= t:
            break
        best = p
    return best


def session_stats(series, vol, s_ms, e_ms):
    pts = [(t, p) for t, p in series if s_ms <= t < e_ms]
    if not pts:
        return None
    start = price_before(series, s_ms)
    if start is None:
        start = pts[0][1]
    end = pts[-1][1]
    v = sum(x for m, x in vol.items() if s_ms <= m < e_ms) if vol else 0.0
    return {'start': start, 'end': end, 'chg': (end - start) / start * 100 if start else None,
            'hi': max(p for _, p in pts), 'lo': min(p for _, p in pts), 'n': len(pts), 'vol': v, 'pts': pts}


# ------------------------------------------------------------------ the pictures
def _fig():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor=BG)
    return plt, fig


def _header(fig, title, sub, color, stamp):
    fig.text(0.035, 0.915, title, color=color, fontsize=34, fontweight='bold', va='center')
    fig.text(0.035, 0.855, sub, color=DIM, fontsize=14, va='center')
    fig.text(0.965, 0.925, "ALEX HALE'S BOARD · RECAP", color=DIM, fontsize=11, ha='right', va='center')
    fig.text(0.965, 0.885, stamp, color=DIM, fontsize=11, ha='right', va='center')


def _timeline(fig, wins, current):
    """The strip that shows where we are: after-hours → overnight → pre-market."""
    ax = fig.add_axes([0.035, 0.035, 0.93, 0.05])
    ax.set_xlim(0, len(wins)); ax.set_ylim(0, 1); ax.axis('off')
    for i, (k, t, v, s, e) in enumerate(wins):
        on = k == current
        c = SESSION_COLOR.get(k, DIM)
        ax.add_patch(__import__('matplotlib').patches.FancyBboxPatch(
            (i + 0.02, 0.1), 0.96, 0.8, boxstyle='round,pad=0,rounding_size=0.15',
            fc=c if on else PANEL, ec=c, lw=1.5, alpha=1 if on else 0.9))
        label = t if k.startswith('day') else f"{t.title()}  {clock(s)}–{clock(e)}"
        ax.text(i + 0.5, 0.5, label, ha='center', va='center',
                fontsize=11, color=BG if on else c, fontweight='bold' if on else 'normal')


def _bars(ax, rows, title):
    """Horizontal % bars, biggest up at the top."""
    ax.set_facecolor(BG)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title(title, color=DIM, fontsize=12, loc='left', pad=8)
    if not rows:
        ax.text(0.5, 0.5, 'No prices recorded', color=DIM, ha='center', va='center', fontsize=16,
                transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
        return
    rows = sorted(rows, key=lambda r: r[1])
    names = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    m = max(0.5, max(abs(v) for v in vals)) * 1.7
    ax.barh(range(len(rows)), vals, color=[GREEN if v >= 0 else RED for v in vals], height=0.62)
    ax.set_xlim(-m, m)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(names, color=WHITE, fontsize=12 if len(rows) <= 12 else 9, family='monospace')
    ax.tick_params(axis='y', length=0)
    ax.tick_params(axis='x', colors=DIM, labelsize=9)
    ax.axvline(0, color=LINE, lw=1)
    ax.xaxis.set_major_formatter(__import__('matplotlib').ticker.FuncFormatter(lambda x, _: f'{x:+.1f}%'))
    for i, v in enumerate(vals):
        ax.text(v + (m * 0.02 if v >= 0 else -m * 0.02), i, f'{v:+.2f}%', va='center',
                ha='left' if v >= 0 else 'right', color=WHITE, fontsize=10)


def _line(ax, pts, base, title, color, frac, bands=None, days=False):
    """Price line, drawn up to `frac` (0..1) of its length; dashed line at `base`. Plotted minute
    by minute (one step per recorded minute), so closed hours and weekends take no room."""
    import bisect
    import matplotlib.ticker as mticker
    ax.set_facecolor('#020a14')
    for sp in ax.spines.values():
        sp.set_color(LINE)
    ax.tick_params(colors=DIM, labelsize=9)
    ax.set_title(title, color=WHITE, fontsize=15, loc='left', pad=8, fontweight='bold')
    if not pts:
        ax.text(0.5, 0.5, 'No prices recorded', color=DIM, ha='center', va='center', fontsize=14,
                transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
        return
    ts = [t for t, _ in pts]
    ys = [p for _, p in pts]
    xs = list(range(len(ys)))
    lo, hi = min(ys + ([base] if base else [])), max(ys + ([base] if base else []))
    pad = (hi - lo) * 0.12 or hi * 0.002 or 1
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(0, max(1, len(xs) - 1))
    if bands:
        for k, s, e in bands:
            a, b = bisect.bisect_left(ts, ms(s)), bisect.bisect_left(ts, ms(e))
            if b > a:
                ax.axvspan(a, b - 1, color=SESSION_COLOR.get(k, DIM), alpha=0.12, lw=0)
    if base:
        ax.axhline(base, color=DIM, lw=1, ls='--')
    n = max(2, int(len(xs) * frac))
    ax.plot(xs[:n], ys[:n], color=color, lw=2.2)
    ax.plot([xs[n - 1]], [ys[n - 1]], 'o', color=color, ms=6)
    ticks = sorted(set(int(round(q * (len(xs) - 1))) for q in (0.08, 0.3, 0.52, 0.74, 0.96)))
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: (lambda d: d.strftime('%a ') + clock(d) if days else clock(d))(
            datetime.fromtimestamp(ts[min(len(ts) - 1, max(0, int(x)))] / 1000, ET))))
    ax.yaxis.tick_right()
    ax.grid(color=LINE, lw=0.5, alpha=0.6)


def draw_scene(path, sc, wins, stamp, frac):
    plt, fig = _fig()
    try:
        _header(fig, sc['title'], sc['sub'], sc['color'], stamp)
        if sc['kind'] == 'intro':
            ax = fig.add_axes([0.05, 0.24, 0.62, 0.55])
            _line(ax, sc['pts'], sc['base'], sc['line_title'], CYAN, frac, sc['bands'], sc.get('days', False))
            y = 0.76
            for label, v, sub in sc['big']:
                fig.text(0.71, y, label, color=DIM, fontsize=13)
                fig.text(0.71, y - 0.065, fmt_pct(v), color=GREEN if (v or 0) >= 0 else RED, fontsize=30, fontweight='bold')
                if sub:
                    fig.text(0.71, y - 0.105, sub, color=DIM, fontsize=10)
                y -= 0.17
            if sc.get('foot'):
                fig.text(0.05, 0.155, sc['foot'], color=AMBER, fontsize=11.5)
        elif sc['kind'] == 'session' or sc['kind'] == 'summary':
            ax1 = fig.add_axes([0.10, 0.20, 0.36, 0.58])
            _bars(ax1, sc['bars'], sc['bars_title'])
            ax2 = fig.add_axes([0.53, 0.30, 0.40, 0.48])
            _line(ax2, sc['pts'], sc['base'], sc['line_title'], sc['color'], frac, sc.get('bands'), sc.get('days', False))
            fig.text(0.53, 0.235, sc.get('note1', ''), color=WHITE, fontsize=12)
            fig.text(0.53, 0.195, sc.get('note2', ''), color=DIM, fontsize=10.5)
            fig.text(0.53, 0.158, sc.get('note3', ''), color=PINK, fontsize=10.5)
        elif sc['kind'] == 'list':
            # optional bars on the left, then lines of text (each (text, colour))
            x0 = 0.05
            if sc.get('bars') is not None:
                ax1 = fig.add_axes([0.12, 0.20, 0.33, 0.58])
                _bars(ax1, sc['bars'], sc['bars_title'])
                x0 = 0.52
            if sc.get('lines_title'):
                fig.text(x0, 0.775, sc['lines_title'], color=DIM, fontsize=12)
            lines = sc.get('lines') or [('Nothing to show', DIM)]
            step = min(0.052, 0.56 / max(1, len(lines)))
            size = 14 if step >= 0.05 else 11
            for i, (txt, col) in enumerate(lines):
                fig.text(x0, 0.72 - i * step, txt, color=col, fontsize=size, family='monospace')
        _timeline(fig, wins, sc.get('session'))
        fig.savefig(path, dpi=DPI, facecolor=BG)
    finally:
        plt.close(fig)


# ------------------------------------------------------------------ the voice
def speak(text, path, voice, rate):
    """Narration as a 22 kHz mono WAV; returns seconds. Without 'say' (not a Mac): silence."""
    if shutil.which('say'):
        cmd = ['say', '-o', path, '--file-format=WAVE', '--data-format=LEI16@22050', '-r', str(rate)]
        if voice:
            cmd += ['-v', voice]
        subprocess.run(cmd + [text], check=True, timeout=180)
    else:
        secs = max(3.0, len(text.split()) / 2.7)
        with wave.open(path, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(b'\0\0' * int(secs * 22050))
    with wave.open(path, 'rb') as w:
        return w.getnframes() / float(w.getframerate())


def join_wavs(parts, path, gap=0.7):
    with wave.open(parts[0], 'rb') as w0:
        params = w0.getparams()
    with wave.open(path, 'wb') as out:
        out.setparams(params)
        for p in parts:
            with wave.open(p, 'rb') as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != params[:3]:
                    raise RuntimeError('voice files differ in format')
                out.writeframes(w.readframes(w.getnframes()))
            out.writeframes(b'\0' * int(gap * params.framerate) * params.sampwidth * params.nchannels)


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which('ffmpeg')


# ------------------------------------------------------------------ the story
def build_scenes(now):
    wins, close, nxt = windows(now)
    s_ms, e_ms = ms(close) - 6 * 3600 * 1000, ms(now)
    ib, wb = load_ticks('ibkr', s_ms, e_ms), load_ticks('webull', s_ms, e_ms)
    ivol, wvol = load_volume('ibkr'), load_volume('webull')
    watch = load_watchlist('ibkr') + load_watchlist('webull')
    syms = [s for s in dict.fromkeys(watch + sorted(ib) + sorted(wb)) if s in ib or s in wb]
    held = holdings()
    close_word = 'Friday' if close.weekday() == 4 and nxt.weekday() == 0 else close.strftime('%A')

    def series(sym, s, e):
        """IBKR if it has prices in this window, else Webull."""
        for book, vol in ((ib, ivol), (wb, wvol)):
            ser = book.get(sym) or []
            if any(s <= t < e for t, _ in ser):
                return ser, vol.get(sym, {}), (book is ib)
        return [], {}, False

    stamp = now.strftime('%a %b %-d · ') + clock(now) + ' ET'
    scenes = []
    # --- intro: the S&P futures line across everything since the close
    lead = next((f for f in ('ES=F', 'NQ=F', 'SPY', 'QQQ') if f in syms), syms[0] if syms else None)
    full_s, full_e = ms(close), ms(now)
    big, intro_bits = [], []
    for f in ('ES=F', 'NQ=F', 'YM=F'):
        if f in syms:
            ser, _, _ = series(f, full_s, full_e)
            st = session_stats(ser, None, full_s, full_e)
            if st:
                big.append((FUT_SHORT.get(f, f) + ' vs 4 pm close', st['chg'], f"{st['end']:,.2f}"))
                intro_bits.append(f"{spoken(f)} are {say_pct(st['chg'])}")
    lead_ser, _, _ = series(lead, full_s, full_e) if lead else ([], {}, False)
    lead_pts = [(t, p) for t, p in lead_ser if full_s <= t <= full_e]
    gap_note = ' After the weekend,' if (nxt - close.date()).days > 1 else ''
    intro_text = (f"Here is your recap since {close_word}'s {clock(close)} close, up to {clock(now)} New York time."
                  f"{gap_note} {'; '.join(intro_bits[:3]) + '.' if intro_bits else ''} "
                  f"We'll go through {_and([SPOKEN_SESSION[k] for k, t, v, s, e in wins])}.")
    wx = weather_now(close)
    if wx:
        intro_text += ' ' + wx['line']
    scenes.append({'kind': 'intro', 'title': 'SINCE THE CLOSE', 'color': GREEN, 'session': None,
                   'sub': f"{close_word} {clock(close)} close → {clock(now)} ET",
                   'pts': lead_pts, 'base': price_before(lead_ser, full_s) if lead_ser else None,
                   'line_title': (FUT_SHORT.get(lead, lead) + ' since the close') if lead else 'No prices yet',
                   'bands': [(k, s, e) for k, t, v, s, e in wins], 'big': big[:3], 'text': intro_text,
                   'foot': weather_foot(wx['rows']) if wx else ''})

    # --- one scene per session
    per = {}
    for key, title, venue, s, e in wins:
        a, b = ms(s), ms(e)
        rows = {}
        for sym in syms:
            ser, vol, from_ib = series(sym, a, b)
            st = session_stats(ser, vol, a, b)
            if st and st['chg'] is not None:
                st['ib'] = from_ib
                rows[sym] = st
        per[key] = rows
        stocks = {k: v for k, v in rows.items() if not is_future(k)}
        futs = {k: v for k, v in rows.items() if is_future(k)}
        ranked = sorted(stocks.items(), key=lambda kv: kv[1]['chg'], reverse=True)
        top = max(stocks.items(), key=lambda kv: abs(kv[1]['chg']), default=None)
        busiest = max(stocks.items(), key=lambda kv: kv[1]['vol'], default=None)
        when = f"from {clock(s)} to {clock(e)}"
        lines = []
        if key == 'afterhours':
            lines.append(f"After-hours, {when}, on IBKR's normal SMART route.")
        elif key == 'overnight':
            night = 'Sunday night' if s.weekday() == 6 else 'overnight'
            lines.append(f"Overnight, {when}{' on ' + night if night != 'overnight' else ''}, on IBKR's overnight venue, Blue Ocean.")
            if stocks and not any(v['ib'] for v in stocks.values()):
                lines.append("Only Webull prices were recorded, and your Webull plan has no overnight data.")
        elif key == 'premarket':
            lines.append(f"Pre-market, {when}, back on the SMART route.")
        else:
            lines.append(f"Regular hours so far, {when}.")
        if not rows:
            lines.append("The board recorded no prices in this session.")
        else:
            up = [kv for kv in ranked if kv[1]['chg'] >= 0.05][:3]
            dn = [kv for kv in reversed(ranked) if kv[1]['chg'] <= -0.05][:3]
            if up:
                lines.append('Up the most: ' + ', '.join(f"{spoken(k)}, {say_pct(v['chg'])}" for k, v in up) + '.')
            if dn:
                lines.append('Down the most: ' + ', '.join(f"{spoken(k)}, {say_pct(v['chg'])}" for k, v in dn) + '.')
            if stocks and not up and not dn:
                lines.append('Stocks barely moved.')
            if busiest and busiest[1]['vol'] > 0:
                lines.append(f"Busiest by volume the board saw: {spoken(busiest[0])}, about {_round_shares(busiest[1]['vol'])} shares.")
            fl = [f"{spoken(k)} {say_pct(v['chg'])}" for k, v in futs.items() if k in ('ES=F', 'NQ=F', 'CL=F', 'GC=F')]
            if fl:
                lines.append('Futures: ' + ', '.join(fl) + '.')
            for sym, (sh, avg) in held.items():
                if sym in rows:
                    lines.append(f"Your {spoken(sym)} shares: {say_pct(rows[sym]['chg'])} in this session.")
        bars = [(k, v['chg']) for k, v in ranked]
        bars = sorted(bars, key=lambda r: abs(r[1]), reverse=True)[:14]
        tsym, tst = top if top else (None, None)
        note1 = (f"High {tst['hi']:,.2f} · Low {tst['lo']:,.2f} · {tst['n']} minutes recorded" if tst else '')
        fut_note = ' · '.join(f"{FUT_SHORT.get(k, k)} {fmt_pct(v['chg'])}" for k, v in futs.items() if k in ('ES=F', 'NQ=F', 'CL=F', 'GC=F'))
        held_note = ' · '.join(f"HELD {k} {fmt_pct(rows[k]['chg'])}" for k in list(held)[:5] if k in rows)
        scenes.append({'kind': 'session', 'title': title, 'color': SESSION_COLOR[key], 'session': key,
                       'sub': f"{s.strftime('%a')} {clock(s)} – {clock(e)} ET · {venue}",
                       'bars': bars, 'bars_title': 'Change in this session',
                       'pts': tst['pts'] if tst else [], 'base': tst['start'] if tst else None,
                       'line_title': f"Biggest mover: {tsym}  {fmt_pct(tst['chg'])}" if tst else 'Biggest mover',
                       'note1': note1, 'note2': fut_note, 'note3': held_note,
                       'text': ' '.join(lines)})

    # --- summary: everything vs the 4 pm close
    tot = {}
    for sym in syms:
        ser, _, _ = series(sym, full_s, full_e)
        base = price_before(ser, full_s)
        st = session_stats(ser, None, full_s, full_e)
        if st and base:
            st['chg'] = (st['end'] - base) / base * 100
            st['start'] = base
            tot[sym] = st
    stocks = {k: v for k, v in tot.items() if not is_future(k)}
    ranked = sorted(stocks.items(), key=lambda kv: kv[1]['chg'], reverse=True)
    lines = [f"Adding it all up, here is where things stand versus {close_word}'s close."]
    if ranked:
        n = min(3, len(ranked) // 2) or 1
        # 'Strongest' lists only stocks that are up and 'Weakest' only ones that are down; if none
        # are up (or down), name the single best (or worst) one with gentler words instead
        ups = [kv for kv in ranked if kv[1]['chg'] > 0][:n]
        downs = [kv for kv in ranked[::-1] if kv[1]['chg'] < 0][:n]
        best, top = ('Strongest', ups) if ups else ('Held up best', ranked[:1])
        worst, low = ('Weakest', downs) if downs else ('Gained the least', ranked[::-1][:1])
        lines.append(f"{best}: {', '.join(spoken(k) + ', ' + say_pct(v['chg']) for k, v in top)}.")
        if len(ranked) > 1 and low[0][0] != top[0][0]:
            lines.append(f"{worst}: {', '.join(spoken(k) + ', ' + say_pct(v['chg']) for k, v in low)}.")
    on = per.get('overnight') or {}
    pm = per.get('premarket') or {}
    broke_hi = [k for k in pm if k in on and pm[k]['hi'] > on[k]['hi'] and not is_future(k)]
    broke_lo = [k for k in pm if k in on and pm[k]['lo'] < on[k]['lo'] and not is_future(k)]
    if broke_hi:
        lines.append('Broke above their overnight high in pre-market: ' + ', '.join(spoken(k) for k in broke_hi[:4]) + '.')
    if broke_lo:
        lines.append('Broke below their overnight low in pre-market: ' + ', '.join(spoken(k) for k in broke_lo[:4]) + '.')
    gain_total, held_bits = 0.0, []
    for sym, (sh, avg) in held.items():
        if sym in tot:
            d = (tot[sym]['end'] - tot[sym]['start']) * sh
            gain_total += d
            held_bits.append(f"{spoken(sym)} {say_pct(tot[sym]['chg'])}")
    if held_bits:
        lines.append(f"Your holdings since the close: {', '.join(held_bits[:5])}; "
                     f"{'a gain' if gain_total >= 0 else 'a loss'} of about {abs(gain_total):,.0f} dollars.")
    open_at = at(nxt, 9, 30)
    if now < open_at:
        mins = int((open_at - now).total_seconds() // 60)
        lines.append(f"The regular session opens in {mins // 60} hour{'s' if mins >= 120 else ''} and {mins % 60} minutes." if mins >= 60
                     else f"The regular session opens in {mins} minutes.")
    lines.append('That is the recap.')
    tsym, tst = (ranked[0] if ranked and abs(ranked[0][1]['chg']) >= abs(ranked[-1][1]['chg']) else ranked[-1]) if ranked else (None, None)
    scenes.append({'kind': 'summary', 'title': 'WHERE THINGS STAND', 'color': GREEN, 'session': None,
                   'sub': f"Change vs {close_word}'s {clock(close)} close",
                   'bars': sorted([(k, v['chg']) for k, v in ranked], key=lambda r: abs(r[1]), reverse=True)[:14],
                   'bars_title': 'Since the close', 'pts': tst['pts'] if tst else [], 'base': tst['start'] if tst else None,
                   'line_title': f"Biggest move: {tsym}  {fmt_pct(tst['chg'])}" if tst else 'Biggest move',
                   'bands': [(k, s, e) for k, t, v, s, e in wins],
                   'note1': ('Above overnight high: ' + ', '.join(broke_hi[:6])) if broke_hi else '',
                   'note2': ('Below overnight low: ' + ', '.join(broke_lo[:6])) if broke_lo else '',
                   'text': ' '.join(lines)})
    return scenes, wins, stamp, close, syms


# ------------------------------------------------------------------ market weather (jb-extras)
def weather_now(close=None, part='day'):
    """The gateway's sector "weather" (data/weather.json, every 5 minutes), if it is fresh and,
    for today's, about the same close as this recap."""
    try:
        with open(os.path.join(DATA, 'weather.json')) as f:
            w = json.load(f)
    except Exception:
        return None
    if time.time() * 1000 - (w.get('at') or 0) > 3 * 3600 * 1000:
        return None
    if part == 'day' and close is not None and w.get('close_ms') and abs(w['close_ms'] - ms(close)) > 3600 * 1000:
        return None
    x = w.get(part) or {}
    return x if x.get('rows') and x.get('line') else None


def weather_foot(rows):
    """'Weather: XLE sunny +1.2% · XLK stormy -2.0%' for the picture (best two, worst two)."""
    if not rows:
        return ''
    pick = rows[:2] + [r for r in rows[-2:] if r not in rows[:2]]
    return 'Weather:  ' + '   '.join(f"{r['sym']} {r['word']} {fmt_pct(r['chg'])}" for r in pick)


# ------------------------------------------------------------------ the weekly recap (jb-extras)
def _read_json(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except Exception:
        return default


def close_of(day):
    return at(day, 13 if day in nyse_early_closes(day.year) else 16)


def week_bounds(now):
    """(last close that has passed, the close before that week's Monday, trading days of the week)."""
    day = now.date()
    while True:
        if trading_day(day) and close_of(day) <= now:
            break
        day -= timedelta(days=1)
    monday = day - timedelta(days=day.weekday())
    prev = monday - timedelta(days=1)
    while not trading_day(prev):
        prev -= timedelta(days=1)
    days = [monday + timedelta(days=i) for i in range(5) if trading_day(monday + timedelta(days=i))
            and monday + timedelta(days=i) <= day]
    return close_of(day), close_of(prev), days


def _daily_closes():
    """{sym: {date: close}} from the Analysis tab's Yahoo daily bars (data/analysis_cache.json)."""
    c = _read_json('analysis_cache.json', {}).get('daily') or {}
    out = {}
    for sym, hit in c.items():
        try:
            out[sym] = {r[0]: float(r[4]) for r in hit.get('rows') or [] if r[4]}
        except Exception:
            continue
    return out


def _say_day(d):
    return d.strftime('%A')


def _say_money(v):
    return f"{'a gain' if v >= 0 else 'a loss'} of {abs(v):,.0f} dollars"


def build_week(now):
    end_close, start_close, days = week_bounds(now)
    s_ms, e_ms = ms(start_close) - 6 * 3600 * 1000, ms(now)
    ib, wb = load_ticks('ibkr', s_ms, e_ms), load_ticks('webull', s_ms, e_ms)
    daily = _daily_closes()
    facts = _read_json(os.path.join('recaps', 'week_facts.json'), {})
    watch = load_watchlist('ibkr') + load_watchlist('webull')
    syms = [s for s in dict.fromkeys(watch + sorted(ib) + sorted(wb)) if s in ib or s in wb]
    held = holdings()
    a_day, b_day = start_close.date().isoformat(), end_close.date().isoformat()
    a_ms, b_ms = ms(start_close), ms(end_close)

    def series(sym):
        return ib.get(sym) or wb.get(sym) or []

    def week_move(sym):
        """(start, end, % change): the board's own prices at both closes when it has them, else
        Yahoo's daily closes (the Analysis tab's), else whatever of the two there is."""
        ser = series(sym)
        dc = daily.get(sym) or {}

        def tick_at(t):
            pts = [p for tt, p in ser if t - 30 * 60000 <= tt < t]
            return pts[-1] if pts else None
        ta, tb = tick_at(a_ms), tick_at(b_ms)
        if ta and tb:
            start, end = ta, tb
        elif dc.get(a_day) and dc.get(b_day):
            start, end = dc[a_day], dc[b_day]
        else:
            start, end = ta or dc.get(a_day), tb or dc.get(b_day)
        if not start or not end:
            return None
        return start, end, (end - start) / start * 100

    moves = {}
    for sym in dict.fromkeys(syms + list(held)):
        m = week_move(sym)
        if m:
            moves[sym] = m
    stamp = now.strftime('%a %b %-d · ') + clock(now) + ' ET'
    wk_label = 'week of ' + days[0].strftime('%b %-d') if days else 'this week'
    wins = [('day' + d.isoformat(), d.strftime('%a %b %-d'), '', None, None) for d in days]
    scenes = []
    full_s = a_ms

    def line_pts(sym):
        pts = [(t, p) for t, p in series(sym) if full_s <= t <= e_ms]
        return pts[::5] if len(pts) > 1500 else pts

    # --- intro: the week in one line
    lead = next((f for f in ('ES=F', 'SPY', 'NQ=F', 'QQQ') if f in moves and line_pts(f)), None)
    if not lead:
        lead = max((s for s in moves if line_pts(s)), key=lambda s: len(line_pts(s)), default=None)
    big, bits = [], []
    for f in ('ES=F', 'NQ=F', 'SPY', 'QQQ', 'YM=F'):
        if f in moves and len(big) < 3:
            st, en, chg = moves[f]
            big.append(((FUT_SHORT.get(f) or f) + ' this week', chg, f'{en:,.2f}'))
            bits.append(f"{spoken(f)} {'were' if is_future(f) else 'was'} {say_pct(chg)}")
    prev_word = start_close.strftime('%A')
    end_word = end_close.strftime('%A')
    if end_word == prev_word:
        end_word = 'this ' + end_word
    monday = end_close.date() - timedelta(days=end_close.date().weekday())
    week_last = max((monday + timedelta(days=i) for i in range(5) if trading_day(monday + timedelta(days=i))),
                    default=end_close.date())
    done = end_close.date() >= week_last
    short = f' A short week: {len(days)} trading days.' if done and 0 < len(days) < 5 else ''
    text = (f"Here is your weekly recap, for the {wk_label}{'' if done else ', so far'}.{short} "
            f"From last {prev_word}'s close to {end_word}'s close, "
            + ('; '.join(bits[:3]) + '.' if bits else 'the board has no prices for the whole week yet.'))
    wx = weather_now(part='week')
    if wx:
        text += ' ' + wx['line']
    scenes.append({'kind': 'intro', 'title': 'THE WEEK', 'color': GREEN, 'session': None,
                   'sub': f"{prev_word} {start_close.strftime('%b %-d')} close → {end_word.replace('this ', '')} {end_close.strftime('%b %-d')} close",
                   'pts': line_pts(lead) if lead else [], 'base': moves[lead][0] if lead else None,
                   'line_title': f"{FUT_SHORT.get(lead, lead)} this week" if lead else 'No prices yet',
                   'bands': None, 'days': True, 'big': big, 'text': text,
                   'foot': weather_foot(wx['rows']) if wx else ''})

    # --- best and worst
    stocks = {k: v for k, v in moves.items() if not is_future(k)}
    ranked = sorted(stocks.items(), key=lambda kv: kv[1][2], reverse=True)
    lines = []
    if ranked:
        n = min(3, max(1, len(ranked) // 2))
        ups = [kv for kv in ranked if kv[1][2] > 0][:n]
        downs = [kv for kv in ranked[::-1] if kv[1][2] < 0][:n]
        if ups:
            lines.append('Best of the week: ' + ', '.join(f"{spoken(k)}, {say_pct(v[2])}" for k, v in ups) + '.')
        if downs:
            lines.append('Worst: ' + ', '.join(f"{spoken(k)}, {say_pct(v[2])}" for k, v in downs) + '.')
        if not ups and not downs:
            lines.append('Nothing on the board moved much this week.')
    else:
        lines.append('The board has no prices for this week yet.')
    gain, held_bits = 0.0, []
    for sym, (sh, avg) in held.items():
        if sym in moves:
            st, en, chg = moves[sym]
            gain += (en - st) * sh
            held_bits.append(f"{spoken(sym)} {say_pct(chg)}")
    if held_bits:
        lines.append(f"Your holdings this week: {', '.join(held_bits[:5])}; {_say_money(gain)}.")
    top = max(ranked, key=lambda kv: abs(kv[1][2]), default=None)
    scenes.append({'kind': 'summary', 'title': 'BEST AND WORST', 'color': GREEN, 'session': None,
                   'sub': f"Change from {prev_word}'s close to {end_word.replace('this ', '')}'s close",
                   'bars': sorted([(k, v[2]) for k, v in ranked], key=lambda r: abs(r[1]), reverse=True)[:14],
                   'bars_title': 'This week', 'pts': line_pts(top[0]) if top else [], 'base': top[1][0] if top else None,
                   'line_title': f"Biggest move: {top[0]}  {fmt_pct(top[1][2])}" if top else 'Biggest move', 'days': True,
                   'note1': ' · '.join(f"HELD {k} {fmt_pct(moves[k][2])}" for k in list(held)[:5] if k in moves),
                   'note2': '', 'note3': '', 'text': ' '.join(lines)})

    # --- market weather for the week (#32)
    if wx:
        rows = wx['rows']
        wl = [(f"{r['sym']:<5} {r['name'][:18]:<18} {r['word'].upper():<13} {fmt_pct(r['chg'])}",
               GREEN if r['chg'] >= 0 else RED) for r in rows]
        best, worst = rows[0], rows[-1]
        scenes.append({'kind': 'list', 'title': 'MARKET WEATHER', 'color': AMBER, 'session': None,
                       'sub': 'The 11 sector funds this week: sunny = up, stormy = down',
                       'bars': [(r['sym'], r['chg']) for r in rows], 'bars_title': 'Sector funds this week',
                       'lines': wl, 'lines_title': '', 'pts': [],
                       'text': f"{wx['line']} The best sector was {best['say']}, {say_pct(best['chg'])}; "
                               f"the worst was {worst['say']}, {say_pct(worst['chg'])}."})

    # --- picks and your trading
    lines_txt, lines_say, bars = [], [], []
    hist = _read_json('picks_history.json', [])
    day_set = {d.isoformat() for d in days}
    right = total = 0
    sum_move = 0.0
    for entry in hist if isinstance(hist, list) else []:
        if entry.get('date') not in day_set:
            continue
        for p in entry.get('picks') or []:
            if p.get('lean') not in ('up', 'down') or not p.get('price'):
                continue
            dc = daily.get(p['sym']) or {}
            nxt = sorted(d for d in dc if entry['date'] < d <= b_day)
            if not nxt:
                ser = series(p['sym'])
                d1 = datetime.fromisoformat(entry['date']).date() + timedelta(days=1)
                while not trading_day(d1):
                    d1 += timedelta(days=1)
                if d1 > end_close.date():
                    continue
                later = price_before(ser, ms(close_of(d1)))
                if later is None:
                    continue
            else:
                later = dc[nxt[0]]
            mv = (later - p['price']) / p['price'] * 100 * (1 if p['lean'] == 'up' else -1)
            total += 1
            right += mv > 0
            sum_move += mv
            bars.append((f"{p['sym']}{'▲' if p['lean'] == 'up' else '▼'}{entry['date'][8:]}", mv))
    if total:
        lines_txt.append((f"Picks that went the right way: {right} of {total}", GREEN if right * 2 >= total else RED))
        lines_txt.append((f"Average move in the picked direction: {fmt_pct(sum_move / total)}", WHITE))
        lines_say.append(f"Your picks this week: {right} of {total} went the way they leaned, "
                         f"an average of {say_pct(sum_move / total)} in the picked direction, by the next day's close.")
    else:
        lines_txt.append(('No picks to score this week yet', DIM))
        lines_say.append("There were no up or down picks to score this week.")
    tdays = [d for d in facts.get('days') or [] if d.get('day') in day_set]
    if tdays:
        pnl = sum(d.get('pnl', 0) for d in tdays)
        n = sum(d.get('n', 0) for d in tdays)
        wins_n = sum(d.get('wins', 0) for d in tdays)
        trips = [t for t in facts.get('trips') or [] if t.get('day') in day_set]
        lines_txt.append((f"Your trades: {n}, {wins_n} winners, {'+' if pnl >= 0 else '-'}${abs(pnl):,.0f}", GREEN if pnl >= 0 else RED))
        if trips:
            b = max(trips, key=lambda t: t['pnl'])
            w = min(trips, key=lambda t: t['pnl'])
            lines_txt.append((f"Best {b['sym']} {b['pnl']:+,.0f} · worst {w['sym']} {w['pnl']:+,.0f}", DIM))
        lines_say.append(f"Your own trades: {n} round trips, {wins_n} winners, {_say_money(pnl)}.")
    elif 'days' in facts:
        lines_txt.append(('Your trades: none recorded this week', DIM))
    lb = facts.get('leaderboard') or []
    if lb:
        best = max(lb, key=lambda r: (r.get('d5') or {}).get('pnl', 0))
        d5 = best.get('d5') or {}
        if d5.get('trades'):
            lines_txt.append((f"Best paper strategy (5 days): {best['name'][:26]} {d5['pnl']:+,.0f}", CYAN))
            lines_say.append(f"Best paper algo strategy over the last five days: {best['name']}, "
                             f"{_say_money(d5['pnl'])} on {best.get('symbol') or 'its stock'}.")
    scenes.append({'kind': 'list', 'title': 'PICKS AND YOUR TRADING', 'color': CYAN, 'session': None,
                   'sub': 'Picks scored by the next trading day\'s close · your trades from the My trading tab',
                   'bars': sorted(bars, key=lambda r: abs(r[1]), reverse=True)[:14] if bars else None,
                   'bars_title': 'Picks: % in the picked direction', 'lines': lines_txt, 'pts': [],
                   'text': ' '.join(lines_say)})

    # --- coming up: next week's earnings and events
    nxt_mon = monday + timedelta(days=7)
    first = nxt_mon if done else now.date() + timedelta(days=1)       # made mid-week: the rest of this week too
    last = nxt_mon + timedelta(days=4)
    mine = set(syms) | set(held) | set(facts.get('symbols') or [])
    earn = []
    for sym, e in (_read_json('earnings.json', {}).get('dates') or {}).items():
        try:
            d = datetime.fromisoformat(e['date']).date()
        except Exception:
            continue
        if sym in mine and first <= d <= last:
            earn.append((d, sym, e.get('when') or ''))
    earn.sort()
    events = []
    for ev in facts.get('calendar') or []:
        d = datetime.fromtimestamp(ev['t'] / 1000, ET)
        if first <= d.date() <= last and ev.get('impact') == 'High':
            events.append((d, ev['title']))
    events.sort()
    rows = [(d, 1, f"{d.strftime('%a %b %-d')}           {sym:<6} earnings {when}", AMBER) for d, sym, when in earn[:8]] + \
           [(d.date(), 0, f"{d.strftime('%a %b %-d')} {clock(d):>8}  {title[:44]}", WHITE) for d, title in events[:8]]
    lines_txt = [(txt, col) for d, k, txt, col in sorted(rows, key=lambda r: (r[0], r[1]))]
    say = []
    if earn:
        say.append('Earnings coming up: ' + '; '.join(f"{spoken(sym)} on {_say_day(d)}{' ' + when if when else ''}"
                                                      for d, sym, when in earn[:6]) + '.')
    else:
        say.append('None of your stocks report earnings in the coming week.')
    if events:
        say.append('Big events: ' + '; '.join(f"{t} on {_say_day(d)} at {clock(d)}" for d, t in events[:5]) + '.')
    elif 'calendar' in facts:
        say.append('No big economic releases on the calendar.')
    say.append('That is the weekly recap. Have a good weekend.' if now.weekday() >= 4 else 'That is the weekly recap.')
    scenes.append({'kind': 'list', 'title': 'COMING UP', 'color': AMBER, 'session': None,
                   'sub': f"{first.strftime('%a %b %-d')} – {last.strftime('%a %b %-d')} · earnings for your stocks, high-impact events (ET)",
                   'bars': None, 'lines': lines_txt or [('Nothing big on the calendar', DIM)], 'pts': [],
                   'text': ' '.join(say)})
    return scenes, wins, stamp, end_close, list(moves)


SPOKEN_SESSION = {'afterhours': 'after-hours', 'overnight': 'overnight on Blue Ocean', 'premarket': 'pre-market',
                  'regular': 'the regular session so far'}


def _and(items):
    return items[0] if len(items) == 1 else ', then '.join(items[:-1]) + ', and then ' + items[-1] if items else 'it'


def _round_shares(v):
    if v >= 1e6:
        return f'{v / 1e6:.1f} million'
    if v >= 1e3:
        return f'{v / 1e3:.0f} thousand'
    return f'{v:.0f}'


# ------------------------------------------------------------------ phone
def board_url():
    u = os.environ.get('JB_RECAP_URL', '').strip()
    if u:
        return u
    port = os.environ.get('JB_GATEWAY_PORT', '8090')
    for t in ('tailscale', '/Applications/Tailscale.app/Contents/MacOS/Tailscale'):
        if shutil.which(t) or os.path.exists(t):
            try:
                ip = subprocess.run([t, 'ip', '-4'], capture_output=True, text=True, timeout=10).stdout.split()
                if ip:
                    return f'http://{ip[0]}:{port}/recap.html'
            except Exception:
                pass
    return f'http://localhost:{port}/recap.html'


def push(title, text, url):
    t = os.environ.get('JB_NTFY_TOPIC', '').strip()
    if not t:
        log('no JB_NTFY_TOPIC in settings.env -- not sent to the phone')
        return
    import ssl
    import urllib.request
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    req = urllib.request.Request('https://ntfy.sh/' + t, data=text.encode('utf-8'), method='POST',
                                 headers={'Title': title, 'Tags': 'movie_camera', 'Click': url,
                                          'Actions': f'view, Watch recap, {url}'})
    try:
        urllib.request.urlopen(req, timeout=20, context=ctx).read()
        log('sent to the phone')
    except Exception as e:
        log(f'ntfy push failed: {e!r}')


# ------------------------------------------------------------------ main
def make(now, do_push=True, reason='manual', weekly=False):
    os.makedirs(OUT, exist_ok=True)
    ff = ffmpeg_exe()
    if not ff:
        raise SystemExit('ffmpeg not found -- run:  .venv/bin/pip install imageio-ffmpeg')
    scenes, wins, stamp, close, syms = build_week(now) if weekly else build_scenes(now)
    if not syms:
        raise SystemExit('no prices recorded ' + ('this week' if weekly else 'since the last close') +
                         ' -- are the IBKR / Webull boards running?')
    voice = os.environ.get('JB_RECAP_VOICE', '').strip()
    try:
        rate = int(os.environ.get('JB_RECAP_RATE', '185'))
    except ValueError:
        rate = 185
    tmp = tempfile.mkdtemp(prefix='jb-recap-')
    try:
        wavs, listing, total = [], [], 0.0
        for i, sc in enumerate(scenes):
            wav = os.path.join(tmp, f'v{i}.wav')
            secs = speak(sc['text'], wav, voice, rate) + 0.7           # +0.7 s pause after each scene
            wavs.append(wav)
            steps = FPS_STEPS if sc['pts'] else 1
            for j in range(steps):
                png = os.path.join(tmp, f's{i:02d}_{j:03d}.png')
                frac = 1.0 if steps == 1 else min(1.0, (j + 1) / (steps * 0.8))   # line done at 80%
                draw_scene(png, sc, wins, stamp, frac)
                listing.append((png, secs / steps))
            total += secs
            log(f'scene {i + 1}/{len(scenes)} ready ({secs:.0f} s)')
        audio = os.path.join(tmp, 'voice.wav')
        join_wavs(wavs, audio, gap=0.7)
        with open(os.path.join(tmp, 'list.txt'), 'w') as f:
            for png, d in listing:
                f.write(f"file '{png}'\nduration {d:.3f}\n")
            f.write(f"file '{listing[-1][0]}'\n")
        stem = ('recap-week-' if weekly else 'recap-') + now.strftime('%Y-%m-%d-%H%M')
        mp4 = os.path.join(OUT, stem + '.mp4')
        cmd = [ff, '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', os.path.join(tmp, 'list.txt'),
               '-i', audio, '-vf', 'fps=12,format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast',
               '-crf', '26', '-c:a', 'aac', '-b:a', '64k', '-shortest', '-movflags', '+faststart', '-f', 'mp4', mp4 + '.part']
        subprocess.run(cmd, check=True, timeout=1200)
        os.replace(mp4 + '.part', mp4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    script = '\n\n'.join(f"{sc['title']}\n{sc['text']}" for sc in scenes)
    info = {'file': stem + '.mp4', 'at': ms(now), 'seconds': round(total), 'reason': reason,
            'title': (f"Weekly recap · week of {(close - timedelta(days=close.weekday())).strftime('%b %-d')}" if weekly
                      else f"Since the close · {now.strftime('%a %b %-d')} {clock(now)} ET"),
            'weekly': weekly, 'script': script}
    with open(os.path.join(OUT, stem + '.json'), 'w') as f:
        json.dump(info, f)
    old_ones = sorted(glob.glob(os.path.join(OUT, 'recap-2*.mp4')))[:-KEEP] + \
        sorted(glob.glob(os.path.join(OUT, 'recap-week-*.mp4')))[:-KEEP_WEEKLY]
    for old in old_ones:
        for p in (old, old[:-4] + '.json'):
            try:
                os.remove(p)
            except OSError:
                pass
    log(f'made {mp4} ({total:.0f} s)')
    if do_push:
        first = scenes[0]['text'].split('. ', 1)[-1][:300]
        push(f"{'Weekly recap' if weekly else 'Recap'} ready ({int(total // 60)}:{int(total % 60):02d})", first, board_url())
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--no-push', action='store_true', help="don't send it to the phone")
    ap.add_argument('--auto', action='store_true', help='the morning run: skip market holidays')
    ap.add_argument('--weekly', action='store_true', help='the weekly recap instead (jb-extras)')
    ap.add_argument('--at', help='pretend it is this New York time, e.g. "2026-10-06 07:00" (for testing)')
    a = ap.parse_args()
    now = datetime.now(ET).replace(second=0, microsecond=0)
    if a.at:
        now = datetime.strptime(a.at, '%Y-%m-%d %H:%M').replace(tzinfo=ET)
    if a.auto and a.weekly:
        monday = now.date() - timedelta(days=now.weekday())
        if not any(trading_day(monday + timedelta(days=i)) for i in range(5)):
            log('no trading days this week -- no weekly recap')
            return
    elif a.auto and not trading_day(now.date()):
        log('market holiday or weekend -- no recap today')
        return
    lock = os.path.join(OUT, '.making')
    os.makedirs(OUT, exist_ok=True)
    try:
        if time.time() - os.path.getmtime(lock) < 20 * 60:
            log('a recap is already being made -- skipped')
            return
    except OSError:
        pass
    with open(lock, 'w') as f:
        f.write(str(os.getpid()))
    try:
        make(now, do_push=not a.no_push, reason='auto' if a.auto else 'manual', weekly=a.weekly)
    except SystemExit as e:
        _fail(str(e))
        raise
    except Exception as e:
        _fail(repr(e))
        raise
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _fail(msg):
    log('recap failed: ' + msg)
    try:
        with open(os.path.join(OUT, 'last_error.json'), 'w') as f:
            json.dump({'at': int(time.time() * 1000), 'error': msg[:300]}, f)
    except OSError:
        pass


if __name__ == '__main__':
    main()
