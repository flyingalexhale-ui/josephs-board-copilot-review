"""Alex's channel (jb-channel, deeper show and natural voices from jb-anchor): a personal market news
channel, made from the board's own numbers.

The Mac writes a "rundown": a list of segments. Each segment has the pictures to show and the words
the anchor says; a voice on the Mac reads those words into a small sound file, and the players show
each segment while its sound plays, so the voice always matches the screen:

    Roku TV app (press the right arrow)    GET /api/tvchannel?key=<TV key>            (rundown)
                                           GET /api/tvchannel?key=<TV key>&f=<file>   (sound)
    Phone and Mac: web/channel.html        GET /api/channel, /api/channel/audio?f=<file> (board login)
    Voices page: web/channel-voices.html   GET/POST /api/channel/voices                  (board login)

What a rundown holds (jb-anchor):
  * the headlines: BREAKING (a new board alert), the open with the mood meters, futures, top movers,
    picks, news, sectors, coming up and the market weather. Each says what CHANGED since the anchor's
    last update ("since my last update 9 minutes ago, Nasdaq futures slipped another half percent"),
    or "still ..." when nothing moved, and the wording is varied so it never repeats word for word.
  * 2 or 3 deep dives, taken in turn from a pool (one of your 3x funds in depth, futures in depth,
    rates / dollar / oil, Asia and Europe, the calendar, crypto, sector rotation, Fed odds, big
    traders' positions, and the desk view with the board's own track record), so a full cycle runs
    about 10 to 15 minutes before anything repeats. Each says why, compared to normal, and what next.
  * specials before and after big reports (CPI, jobs, the Fed, earnings of your symbols).
  * a schedule of shows: Headline Hour at the top of each hour, Quick Updates at :15 / :30 / :45,
    the Closing Bell Wrap at 4 pm, the Overnight Open at 8 pm, the Asia and Europe Check at
    midnight, QUIET HOURS from 1 to 3 am (explainers made from the board's own numbers), the
    Pre-market Preview at 4 am, the Opening Bell Preview at 9:15 and the Sunday Futures Open.
  * two anchors: one voice for markets, a second for news and what's coming up. The voices are Piper
    voices (free, natural, made on the Mac with no internet) when installed, else the Mac's own
    voices (Samantha). A short chime starts each story (a longer one for BREAKING), made on the Mac.

READ ONLY: it only reads what the board already shows. No account money or positions are spoken or
shown (a TV can be seen and heard by anyone in the room). The rundown is only made while someone is
watching (asked in the last 10 minutes), so it costs nothing otherwise.
"""
import array
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
OUT = os.path.join(DATA, 'channel')
VOICE_CFG = os.path.join(DATA, 'channel_voices.json')     # the two anchors' voices (Voices page)
MEMORY = os.path.join(DATA, 'channel_memory.json')        # what the anchor last said (for "since my last update")
PIPER_DIR = os.path.join(ROOT, 'voices')                  # Piper voices: voices/<name>/<name>.onnx, tokens.txt, espeak-ng-data
PIPER_RUN = os.path.join(ROOT, 'bin', 'jb-piper.py')
REBUILD_S = 150                    # a new rundown at most every 2.5 minutes ...
WATCHED_S = 600                    # ... and only while someone asked in the last 10 minutes
NAME = re.compile(r'^[0-9a-f]{16}\.(m4a|wav)$')

_lock = threading.Lock()
_st = {'rundown': None, 'built': 0.0, 'asked': 0.0, 'building': False, 'said': set(), 'error': '', 'thread': None,
       'pool': 0, 'expl': 0, 'etf': 0, 'tpl': {}, 'cache': {}}

FUT_SAY = {'ES': 'S and P futures', 'NQ': 'Nasdaq futures', 'YM': 'Dow futures', 'RTY': 'Russell futures',
           'CL': 'oil', 'GC': 'gold', 'SI': 'silver', 'NG': 'natural gas', 'ZB': 'bonds', 'ZN': 'ten year notes',
           'BTC': 'bitcoin futures', 'MES': 'micro S and P futures', 'MNQ': 'micro Nasdaq futures', 'HG': 'copper'}
FUT_NAME = {'ES': 'S&P 500 futures', 'NQ': 'Nasdaq 100 futures', 'YM': 'Dow futures', 'RTY': 'Russell 2000 futures',
            'CL': 'Crude oil', 'GC': 'Gold', 'SI': 'Silver', 'NG': 'Natural gas'}
FUT_CC = {'ES': 'S&P futures', 'NQ': 'Nasdaq futures', 'YM': 'Dow futures', 'RTY': 'Russell futures', 'CL': 'oil',
          'GC': 'gold', 'SI': 'silver', 'NG': 'natural gas', 'HG': 'copper'}
# companies are said by name ("Nvidia", not "N V D A")
NAMES = {'NVDA': 'Nvidia', 'AAPL': 'Apple', 'MSFT': 'Microsoft', 'AMZN': 'Amazon', 'GOOGL': 'Alphabet', 'GOOG': 'Alphabet',
         'META': 'Meta', 'TSLA': 'Tesla', 'AVGO': 'Broadcom', 'AMD': 'A M D', 'LLY': 'Eli Lilly', 'UBER': 'Uber',
         'UNH': 'UnitedHealth', 'XOM': 'Exxon', 'NFLX': 'Netflix', 'COST': 'Costco', 'JPM': 'JPMorgan', 'MU': 'Micron',
         'TSM': 'Taiwan Semi', 'INTC': 'Intel', 'QCOM': 'Qualcomm', 'ORCL': 'Oracle', 'CRM': 'Salesforce', 'ADBE': 'Adobe',
         'PLTR': 'Palantir', 'COIN': 'Coinbase', 'MSTR': 'MicroStrategy', 'SMCI': 'Super Micro', 'ARM': 'Arm', 'BA': 'Boeing',
         'WMT': 'Walmart', 'V': 'Visa', 'MA': 'Mastercard', 'HD': 'Home Depot', 'CVX': 'Chevron', 'PFE': 'Pfizer',
         'DIS': 'Disney', 'NKE': 'Nike', 'SBUX': 'Starbucks', 'GS': 'Goldman Sachs', 'BAC': 'Bank of America',
         'ASML': 'A S M L', 'SPY': 'spy', 'QQQ': 'triple Q', 'DIA': 'the Dow fund', 'IWM': 'the Russell fund',
         'SOXX': 'the chip fund', 'SMH': 'the chip fund'}
# fund tickers said the way traders say them
SAY_AS = {'TQQQ': 'T Q Q Q', 'SQQQ': 'S Q Q Q', 'SOXL': 'socks L', 'SOXS': 'socks S', 'UPRO': 'you pro', 'SPXL': 'S P X L',
          'SPXS': 'S P X S', 'SPXU': 'S P X U', 'TNA': 'T N A', 'TZA': 'T Z A', 'LABU': 'lab U', 'LABD': 'lab D',
          'TECL': 'tech L', 'TECS': 'tech S', 'FNGU': 'fang U', 'FNGD': 'fang D', 'UDOW': 'you dow', 'SDOW': 'S dow',
          'NVDL': 'N V D L', 'TSLL': 'T S L L', 'UVXY': 'U V X Y', 'YINN': 'yin', 'YANG': 'yang', 'NUGT': 'nugget',
          'DUST': 'dust', 'FAS': 'F A S', 'FAZ': 'F A Z', 'ERX': 'E R X', 'ERY': 'E R Y', 'BOIL': 'boil', 'KOLD': 'cold'}
LEV3 = ['TQQQ', 'SQQQ', 'SOXL', 'SOXS', 'UPRO', 'SPXL', 'SPXS', 'SPXU', 'TNA', 'TZA', 'TECL', 'TECS', 'FNGU', 'FNGD',
        'LABU', 'LABD', 'UDOW', 'SDOW', 'FAS', 'FAZ', 'YINN', 'YANG', 'NUGT', 'DUST', 'ERX', 'ERY', 'NVDL', 'TSLL']
# what each 3x fund follows (for "compared to normal" and the fund deep dive)
BASE = {'TQQQ': ('QQQ', 'NQ=F', 3), 'SQQQ': ('QQQ', 'NQ=F', -3), 'SOXL': ('SOXX', 'NQ=F', 3), 'SOXS': ('SOXX', 'NQ=F', -3),
        'UPRO': ('SPY', 'ES=F', 3), 'SPXL': ('SPY', 'ES=F', 3), 'SPXS': ('SPY', 'ES=F', -3), 'SPXU': ('SPY', 'ES=F', -3),
        'TNA': ('IWM', 'RTY=F', 3), 'TZA': ('IWM', 'RTY=F', -3), 'TECL': ('XLK', 'NQ=F', 3), 'TECS': ('XLK', 'NQ=F', -3),
        'UDOW': ('DIA', 'YM=F', 3), 'SDOW': ('DIA', 'YM=F', -3)}
WORLD_SAY = {'^VIX': 'the fear index', '^TNX': 'the ten year yield', 'DX-Y.NYB': 'the dollar', 'JPY=X': 'the dollar against the yen',
             'CL=F': 'oil', 'GC=F': 'gold', 'BTC-USD': 'bitcoin', 'ETH-USD': 'ether', '^N225': 'Tokyo', '^HSI': 'Hong Kong',
             '000001.SS': 'Shanghai', '^KS11': 'Seoul', '^TWII': 'Taiwan', '^FTSE': 'London', '^GDAXI': 'Frankfurt',
             '^FCHI': 'Paris', '^STOXX50E': 'European stocks', 'HG=F': 'copper', 'NG=F': 'natural gas', 'SI=F': 'silver'}
ANCHOR_B = {'news', 'coming', 'calendar', 'special', 'explainer', 'breaking'}   # the second anchor's stories
ONES = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']


# ------------------------------------------------------------------ words
# The anchor's script uses three small marks: {SYM} for a symbol (said by name, written as the ticker in
# captions), *word* for a little stress on the key word, and " | " for a short pause between stories.
def _num(v, dec=2):
    if v is None:
        return '--'
    try:
        v = float(v)
    except (TypeError, ValueError):
        return '--'
    if abs(v) >= 10000:
        return f'{v:,.0f}'
    return f'{v:,.{dec}f}'


def _pct(v):
    return '--' if v is None else f'{v:+.2f}%'


def _dir(v):
    return 0 if v is None or abs(v) < 0.005 else (1 if v > 0 else -1)


def _ext(t):
    """Text from outside (headlines, alerts): without the script's marks."""
    return re.sub(r'[{}*|\[\]]', '', str(t or ''))


def S(sym):
    return '{' + str(sym) + '}'


def say_sym(sym):
    s = sym[:-2] if sym.endswith('=F') else sym
    if sym.endswith('=F') or s in FUT_SAY:
        return FUT_SAY.get(s, ' '.join(s))
    if sym in WORLD_SAY:
        return WORLD_SAY[sym]
    if s in NAMES:
        return NAMES[s]
    if s in SAY_AS:
        return SAY_AS[s]
    return ' '.join(s.replace('.', ' ').replace('-', ' '))


def cc_sym(sym):
    s = sym[:-2] if sym.endswith('=F') else sym
    if sym.endswith('=F') or s in FUT_SAY:
        return FUT_CC.get(s, s + ' futures')
    if sym in WORLD_SAY:
        return WORLD_SAY[sym]
    if s in NAMES and s not in ('SPY', 'QQQ', 'AMD', 'ASML'):
        return NAMES[s]
    return s


def _amt(a):
    """How big a % move is, said like a person: 'about half a percent', 'about one and a half percent'."""
    a = abs(a)
    if a < 0.15:
        return 'a touch'
    if a < 0.35:
        return 'about a quarter of a percent'
    if a < 0.6:
        return 'about half a percent'
    if a < 0.85:
        return 'about three quarters of a percent'
    if a < 9.75:
        h = round(a * 2) / 2
        w = int(h)
        if h - w >= 0.5:
            return f'about {ONES[w]} and a half percent'
        return f'about {w} percent'
    return f'about {round(a)} percent'


def say_pct(v, stress=False):
    if v is None:
        return 'with no change yet'
    if abs(v) < 0.05:
        return 'about flat'
    w = 'up' if v > 0 else 'down'
    return f"{'*' + w + '*' if stress else w} {_amt(v)}"


def say_price(p):
    if p is None:
        return ''
    return f'{p:,.0f}' if p >= 100 else f'{p:,.1f}' if p >= 10 else f'{p:,.2f}'


def _low(t):
    return t[:1].lower() + t[1:] if t[:2] != t[:2].upper() else t


def _moved(v):
    return 'was about flat' if abs(v) < 0.05 else f"{'gained' if v > 0 else 'lost'} {_amt(v)}"


def _cap(t):
    return t[:1].upper() + t[1:]


def _and(items):
    items = [i for i in items if i]
    if not items:
        return ''
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def _clock(ms):
    return watch.et(ms).strftime('%I:%M %p').lstrip('0')


def _say_clock(ms):
    d = watch.et(ms)
    h = d.hour % 12 or 12
    ap = 'in the morning' if d.hour < 12 else 'in the afternoon' if d.hour < 17 else 'in the evening'
    if d.hour == 0:
        ap = 'past midnight' if d.minute else ''
        return ('12' if not d.minute else f'12:{d.minute:02d}') + (' ' + ap if ap else '')
    return (f'{h} o\'clock' if d.minute == 0 else f'{h}:{d.minute:02d}') + ' ' + ap


def _ago(ms, now):
    m = max(0, int((now - ms) / 60000))
    return 'just now' if m < 2 else f'{m} min ago' if m < 90 else f'{m // 60} h ago'


def _clean(text, n=110):
    t = re.sub(r'\s+', ' ', _ext(text)).strip()
    return t if len(t) <= n else t[:n - 1].rsplit(' ', 1)[0] + '…'


def _v(key, options):
    """One of several ways to say the same thing, never the same one twice in a row."""
    options = [o for o in options if o]
    if len(options) <= 1:
        return options[0] if options else ''
    last = _st['tpl'].get(key)
    pick = random.choice([o for i, o in enumerate(options) if i != last])
    _st['tpl'][key] = options.index(pick)
    return pick


def _speakable(text):
    t = text.replace('&', ' and ').replace('%', ' percent').replace('…', '.')
    t = re.sub(r'(?<![\w.])\+(\d)', r'plus \1', t)
    t = re.sub(r'\bO/N\b', 'overnight', t)
    t = re.sub(r'\b(\d+) min\b', r'\1 minutes', t)
    t = re.sub(r'\bET\b', 'Eastern', t)
    return re.sub(r'[ \t]+', ' ', t).strip()


def render(text, how):
    """The script -> words for the Mac voice ('say'), for Piper ('piper') or for captions ('cc')."""
    t = re.sub(r'\{([^{}]{1,24})\}', lambda m: cc_sym(m.group(1)) if how == 'cc' else say_sym(m.group(1)), text)
    if how == 'say':          # the Mac voice's own hidden marks: [[emph +]] stresses the next word, [[slnc 450]] pauses
        t = re.sub(r'\*([^*]{1,40})\*', r'[[emph +]]\1', t)
        t = t.replace(' | ', ' [[slnc 450]] ')
    elif how == 'piper':      # Piper: a blank line is a breath between stories
        t = t.replace('*', '').replace(' | ', '\n\n')
    else:
        t = t.replace('*', '').replace(' | ', ' ')
    t = re.sub(r'(^|[.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), t)   # a capital at each sentence start
    if how != 'cc':
        t = _speakable(t)
    return re.sub(r'[ \t]+', ' ', t).strip()


# ------------------------------------------------------------------ memory: what the anchor said last time
_mem = {'v': {}, 'news': [], 'loaded': False}
_now_vals = {}


def _mem_load():
    if _mem['loaded']:
        return
    _mem['loaded'] = True
    try:
        d = json.load(open(MEMORY, encoding='utf-8'))
        _mem['v'] = d.get('v') or {}
        _mem['news'] = d.get('news') or []
    except Exception:
        pass


def _mem_save():
    try:
        tmp = MEMORY + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'v': _mem['v'], 'news': _mem['news'][-60:]}, f)
        os.replace(tmp, MEMORY)
    except OSError:
        pass


def _was(key, now, within_min=45):
    r = _mem['v'].get(key)
    if r and r[0] is not None and 0 < now - r[1] < within_min * 60000:
        return r[0], max(1, int(round((now - r[1]) / 60000)))
    return None, None


def _note(key, v, now):
    if v is not None:
        _now_vals[key] = [v, now]


def _since(key, v, now, name, thr=0.2):
    """'' (nothing to compare), ('still', words) or ('moved', words) for a % that the anchor said before."""
    prev, mins = _was(key, now)
    _note(key, v, now)
    if prev is None or v is None:
        return None, ''
    d = v - prev
    if abs(d) < thr:
        return 'still', f"{name} {_v('still', ['are still', 'are holding', 'remain'])} {say_pct(v)}"
    if d > 0:
        verb = 'climbed another' if v > 0 and prev > 0 else 'bounced'
    else:
        verb = 'slipped another' if v < 0 and prev < 0 else 'gave back'
    lead = _v('since', [f'Since my last update {mins} minutes ago, ', f'In the last {mins} minutes, ',
                        f'Since I checked {mins} minutes ago, '])
    return 'moved', f"{lead}{name} {verb} {_amt(d).replace('about ', '')}, and {'are' if name.endswith('s') else 'is'} now {say_pct(v, True)}"


# ------------------------------------------------------------------ the board's numbers
def _hist(sym):
    with watch._lock:
        return list(watch._px.get(sym) or [])


def _series(hist, start, now, n=96):
    """n+1 points from start to now, as heights 0..1 (plus the low and high)."""
    if not hist:
        return [], None, None
    vals = []
    j = 0
    for k in range(n + 1):
        t = start + (now - start) * k // n
        while j + 1 < len(hist) and hist[j + 1][0] <= t:
            j += 1
        vals.append(hist[j][1] if hist[j][0] <= t else None)
    real = [v for v in vals if v is not None]
    if not real:
        return [], None, None
    lo, hi = min(real), max(real)
    pad = (hi - lo) * 0.08 or hi * 0.001 or 1
    lo, hi = lo - pad, hi + pad
    return [-1 if v is None else round((v - lo) / (hi - lo), 3) for v in vals], lo, hi


def _cached(key, ttl, fn):
    hit = _st['cache'].get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        v = fn()
    except Exception as e:
        print(f'channel: {key}: {e!r}', flush=True)
        v = None
    _st['cache'][key] = (time.time(), v)
    return v


def _world():
    def f():
        import world
        return {r['sym']: r for r in json.loads(world.snapshot()).get('rows') or []}
    return _cached('world', 60, f) or {}


def _wchg(sym):
    r = _world().get(sym) or {}
    return r.get('since_close')


def _daily(sym):
    """Daily bars [day, open, high, low, close] the Analysis tab already keeps (none fetched here)."""
    try:
        import analysis
        return analysis._cached_daily(sym) or []
    except Exception:
        return []


def _usual_gap(sym):
    """Average size of the close-to-open move over the last 20 days, in %."""
    rows = _daily(sym)[-21:]
    g = [abs(rows[i][1] / rows[i - 1][4] - 1) * 100 for i in range(1, len(rows)) if rows[i - 1][4] and rows[i][1]]
    return sum(g) / len(g) if len(g) >= 10 else None


def _usual_day(sym):
    rows = _daily(sym)[-21:]
    g = [abs(rows[i][4] / rows[i - 1][4] - 1) * 100 for i in range(1, len(rows)) if rows[i - 1][4]]
    return sum(g) / len(g) if len(g) >= 10 else None


def _streak(sym):
    """How many days in a row it closed the same way (+3 = up three days running)."""
    rows = _daily(sym)[-12:]
    n = 0
    for i in range(len(rows) - 1, 0, -1):
        d = rows[i][4] - rows[i - 1][4]
        if d == 0 or (n and (d > 0) != (n > 0)):
            break
        n += 1 if d > 0 else -1
    return n


def _ranges(sym, last):
    """Where the price sits in its 1-week and 52-week ranges (0 = at the low, 100 = at the high)."""
    rows = _daily(sym)
    out = {}
    for k, n in (('1W', 5), ('52W', 252)):
        rs = rows[-n:]
        if len(rs) < min(n, 5) or last is None:
            continue
        hi = max([r[2] for r in rs] + [last])
        lo = min([r[3] for r in rs] + [last])
        if hi > lo:
            out[k] = {'pos': round((last - lo) / (hi - lo) * 100), 'hi': hi, 'lo': lo}
    return out


def _sessions(sym, now):
    try:
        import analysis
        return analysis.sessions(sym, now)
    except Exception:
        return None


def _session_story(sym, now):
    """'after-hours added half a percent, overnight gave back a quarter, and the pre-market is flat'."""
    s = _sessions(sym, now)
    if not s:
        return '', []
    parts, chips = [], []
    for k, name in (('ah', 'after-hours'), ('on', 'overnight'), ('pre', 'the pre-market'), ('reg', 'regular hours')):
        v = s.get(k)
        if v is None:
            continue
        chips.append({'k': {'ah': 'AFTER-HOURS', 'on': 'OVERNIGHT', 'pre': 'PRE-MARKET', 'reg': 'REGULAR'}[k], 'v': _pct(v), 'dir': _dir(v)})
        if abs(v) < 0.1:
            parts.append(f'{name} was flat')
        else:
            parts.append(f"{name} {'added' if v > 0 else 'took off'} {_amt(v).replace('about ', '')}")
    return (_and(parts) if len(parts) >= 2 else ''), chips


def _levels(sym, now, hist=None):
    """The next levels to watch: the overnight high and low, yesterday's close, the options max-pain magnet."""
    hist = hist if hist is not None else _hist(sym)
    st = watch.stats(sym, now) or {}
    lv = []
    if st.get('hi') is not None:
        lv.append(('overnight high', st['hi']))
    if st.get('lo') is not None:
        lv.append(('overnight low', st['lo']))
    try:
        c = watch.price_at(hist, watch.last_close_ms(now))
        if c:
            lv.append(('the 4 pm close', c))
    except Exception:
        pass
    try:
        import analysis
        with analysis._lock:
            o = dict(analysis._c['options'].get(sym) or {})
        if o.get('max_pain') and time.time() - (o.get('at') or 0) < 86400:
            lv.append(('the options magnet', o['max_pain']))
    except Exception:
        pass
    return lv


def _next_event(p, now, hours=24):
    for e in p.get('events') or []:
        if e.get('t') and now < e['t'] < now + hours * 3600000 and (str(e.get('impact')) == 'High' or e.get('kind') == 'fed'):
            return e
    return None


def _causes(now, fut_v=None):
    """Linked causes the board can see right now: 'yields are up, which is pressuring tech'."""
    out = []
    tnx, dx, vix, oil = _wchg('^TNX'), _wchg('DX-Y.NYB'), _wchg('^VIX'), _wchg('CL=F')
    nq = fut_v if fut_v is not None else _wchg('NQ=F')
    if tnx is not None and abs(tnx) >= 1.0:
        if tnx > 0 and (nq or 0) < 0:
            out.append('the ten year yield is *up*, which is pressuring tech')
        elif tnx < 0 and (nq or 0) > 0:
            out.append('the ten year yield is *down*, which helps tech')
        else:
            out.append(f"the ten year yield is {'up' if tnx > 0 else 'down'} {_amt(tnx).replace('about ', '')}")
    if vix is not None and abs(vix) >= 4:
        out.append('the fear index is ' + ('*rising*' if vix > 0 else 'easing'))
    if dx is not None and abs(dx) >= 0.4:
        out.append('the dollar is ' + ('strong' if dx > 0 else 'weak'))
    if oil is not None and abs(oil) >= 2:
        out.append('oil is ' + ('jumping' if oil > 0 else 'sliding') + ', which moves energy')
    asia = [x for x in (_wchg(s) for s in ('^N225', '^HSI', '^KS11', '^TWII')) if x is not None]
    if asia and nq is not None and abs(sum(asia) / len(asia)) >= 0.5 and (sum(asia) > 0) == (nq > 0):
        out.append('Asia is ' + ('higher' if sum(asia) > 0 else 'lower') + ' too')
    return out


def _board_why(sym, now):
    """The board's own reason for a move (batch C, gateway/why.py: market / sector / own move, volume vs normal,
    levels crossed), when that part of the board is installed; '' if not."""
    try:
        import why
    except Exception:
        return ''
    for args in ((sym,), (sym, now)):
        try:
            t = why.line(*args)
        except TypeError:
            continue
        except Exception:
            return ''
        if isinstance(t, dict):
            t = t.get('line') or ''
        return _clean(t, 160).rstrip('.') if t else ''
    return ''


def _compare(sym, v, kind='gap'):
    """'that's about twice its usual overnight move' / 'a normal-sized move'."""
    if v is None:
        return ''
    u = _usual_gap(sym) if kind == 'gap' else _usual_day(sym)
    if not u or u < 0.05:
        return ''
    r = abs(v) / u
    what = 'overnight move' if kind == 'gap' else 'daily move'
    if r >= 2.6:
        return f"that's about {ONES[min(10, round(r))]} times its usual {what}"
    if r >= 1.6:
        return f"that's about twice its usual {what}"
    if r <= 0.4:
        return f'that is smaller than its usual {what}'
    return f'that is about a normal-sized {what}'


# ------------------------------------------------------------------ the shows (a schedule like a real channel)
def show_for(now, p):
    d = watch.et(now)
    hm = d.hour * 60 + d.minute
    closed = p.get('session') in ('Weekend', 'Holiday')
    if d.weekday() == 6 and 18 * 60 <= hm < 19 * 60:
        return {'key': 'sunday', 'name': 'SUNDAY FUTURES OPEN', 'say': 'the Sunday Futures Open', 'dives': 3,
                'focus': ['futdive', 'global', 'calendar']}
    if closed:
        return {'key': 'weekend', 'name': 'WEEKEND DESK', 'say': 'the Weekend Desk', 'dives': 2, 'explain': 1,
                'focus': ['crypto', 'calendar', 'desk', 'fed', 'positioning']}
    if 60 <= hm < 180:
        return {'key': 'quiet', 'name': 'QUIET HOURS', 'say': 'Quiet Hours', 'dives': 0, 'explain': 2, 'quiet': True}
    if 16 * 60 <= hm < 16 * 60 + 40:
        return {'key': 'close', 'name': 'CLOSING BELL WRAP', 'say': 'the Closing Bell Wrap', 'dives': 3,
                'focus': ['rotation', 'desk', 'etf']}
    if 20 * 60 <= hm < 20 * 60 + 40:
        return {'key': 'overnight', 'name': 'OVERNIGHT OPEN', 'say': 'the Overnight Open', 'dives': 3,
                'focus': ['futdive', 'etf', 'global']}
    if hm < 40:
        return {'key': 'asia', 'name': 'ASIA & EUROPE CHECK', 'say': 'the Asia and Europe Check', 'dives': 3,
                'focus': ['global', 'macro', 'futdive']}
    if 4 * 60 <= hm < 4 * 60 + 40:
        return {'key': 'premarket', 'name': 'PRE-MARKET PREVIEW', 'say': 'the Pre-market Preview', 'dives': 3,
                'focus': ['futdive', 'etf', 'calendar']}
    if 9 * 60 + 10 <= hm < 9 * 60 + 30:
        return {'key': 'bell', 'name': 'OPENING BELL PREVIEW', 'say': 'the Opening Bell Preview', 'dives': 3,
                'focus': ['etf', 'calendar', 'desk']}
    if d.minute < 5:
        return {'key': 'hour', 'name': 'HEADLINE HOUR', 'say': 'the Headline Hour', 'dives': 3}
    if d.minute % 15 < 4:
        return {'key': 'quick', 'name': 'QUICK UPDATE', 'say': 'a Quick Update', 'dives': 1, 'quick': True}
    return {'key': 'live', 'name': "ALEX'S CHANNEL", 'say': '', 'dives': 2}


# ------------------------------------------------------------------ the headline segments
def _story(p, now, fut):
    """Lead with the story before the numbers: 'Chips are carrying the market tonight.'"""
    when = 'tonight' if watch.et(now).hour >= 16 or watch.et(now).hour < 4 else 'this morning' if watch.et(now).hour < 12 else 'today'
    secs = _sector_rows(now)
    v = fut.get('v')
    if secs and len(secs) >= 3:
        top, bot = secs[0], secs[-1]
        if v is not None and v > 0.2 and top['pct'] is not None and top['pct'] > 0.3:
            return _v('story_up', [f"{_cap(top['short'])} {'are' if top['short'].endswith('s') else 'is'} carrying the market {when}.",
                                   f"{_cap(top['short'])} {'lead' if top['short'].endswith('s') else 'leads'} the way {when}."])
        if v is not None and v < -0.2 and bot['pct'] is not None and bot['pct'] < -0.3:
            return _v('story_dn', [f"{_cap(bot['short'])} {'are' if bot['short'].endswith('s') else 'is'} dragging the market {when}.",
                                   f"The selling is led by {bot['short']} {when}."])
    if v is not None:
        if abs(v) < 0.15:
            return _v('story_flat', [f'A quiet tape {when}.', f'Not much moving {when}.'])
        return _v('story_dir', [f"Stocks are leaning {'higher' if v > 0 else 'lower'} {when}.",
                                f"A {'risk-on' if v > 0 else 'risk-off'} feel {when}."])
    return ''


def _open(p, now, greet, show, fut):
    meters = []
    regular = p.get('session') == 'Regular'
    try:
        import meters as mt
        snap = mt.snapshot(now)
        k = 'open' if regular else 'close'
        for title, m in (('Market mood', p.get('mood')), ('Futures mood', (snap.get('futures') or {}).get(k)),
                         ('Picks mood', (snap.get('picks') or {}).get(k))):
            if m:
                sc = int(m.get('score') or 0)
                meters.append({'title': title, 'score': f'{sc:+d}', 'label': m.get('label') or '', 'value': sc,
                               'dir': 1 if sc >= 25 else -1 if sc <= -25 else 0})
    except Exception:
        m = p.get('mood')
        if m:
            sc = int(m.get('score') or 0)
            meters.append({'title': 'Market mood', 'score': f'{sc:+d}', 'label': m.get('label') or '', 'value': sc,
                           'dir': 1 if sc >= 25 else -1 if sc <= -25 else 0})
    where = {'Pre-market': 'in the pre-market', 'Regular': 'in regular trading', 'After-hours': 'in after-hours trading',
             'Overnight': 'in the overnight session', 'Quiet': 'before the pre-market opens'}.get(p.get('session'), '')
    showw = f"this is {show['say']} on your channel" if show.get('say') else 'this is your board'
    first = _v('hello', [f"{greet}, Alex. It's {_say_clock(now)}{', ' + where if where else ''}, and {showw}.",
                         f"{greet}, Alex, {showw}. It's {_say_clock(now)} Eastern{', ' + where if where else ''}.",
                         f"{greet}, Alex. {_cap(showw)}, at {_say_clock(now)}{', ' + where if where else ''}."])
    if p.get('session') in ('Weekend', 'Holiday'):
        first = f"{greet}, Alex, {showw}. {_ext(p.get('session_note') or 'The market is closed')}."
    words = [first]
    if show.get('quiet'):
        words.append(_v('quiet', ["It's *quiet hours* on the desk, between one and three in the morning, so little is moving. "
                                  "After a quick look at futures, I've got a couple of explainers built from your board's own numbers.",
                                  "We're in *quiet hours*, one to three a.m., when the market barely moves. "
                                  "So tonight, a quick check of futures, and then something to learn from your board's history."]))
    else:
        st = _story(p, now, fut)
        if st:
            words.append(st)
    moods = []
    for m in meters:
        key = 'meter:' + m['title']
        prev, mins = _was(key, now)
        _note(key, m['value'], now)
        if prev is not None and abs(m['value'] - prev) >= 15:
            moods.append(f"the {m['title'].lower()} {'jumped' if m['value'] > prev else 'dropped'} to {m['value']}, {m['label'].lower()}")
        elif prev is not None and not show.get('quiet'):
            continue                                 # unchanged: not said again
        else:
            moods.append(f"the {m['title'].lower()} is {m['label'].lower()} at {m['value']}")
    if moods:
        words.append(_cap(_and(moods)) + '.')
    elif meters:
        words.append(_v('moods_same', ['The mood meters are about where they were.', 'No big change in the mood meters.']))
    return {'kind': 'open', 'title': 'ALEX HALE\'S BOARD', 'color': 'cyan',
            'sub': f"{p.get('session') or ''}  ·  {_ext(p.get('session_note') or '')}", 'meters': meters,
            'headline': _clean(words[1] if len(words) > 1 and not show.get('quiet') else first.split(',')[0] + '.', 70)
            if not show.get('quiet') else 'Quiet hours: explainers from your board',
            'say': ' '.join(words)}


def _fut_lead(watchlist):
    futs = [s for s in watchlist if s.endswith('=F')]
    lead = next((s for s in ('NQ=F', 'ES=F', 'YM=F') if s in futs), futs[0] if futs else None)
    return futs, lead


def _futures(now, watchlist, p, show):
    futs, lead = _fut_lead(watchlist)
    start = now - 24 * 3600000
    rows = []
    for s in futs[:6]:
        st = watch.stats(s, now) or {}
        rows.append({'sym': s[:-2], 'val': _pct(st.get('close')), 'dir': _dir(st.get('close')), 'pct': st.get('close')})
    if not rows:                                   # no futures on the IBKR board: the World tab's
        for f in (p.get('futures') or [])[:6]:
            rows.append({'sym': f.get('name') or f.get('sym'), 'val': _pct(f.get('chg')), 'dir': _dir(f.get('chg')),
                         'pct': f.get('chg')})
    seg = {'kind': 'futures', 'title': 'FUTURES', 'color': 'cyan', 'sub': 'last 24 hours  ·  % since the 4 pm close',
           'name': '', 'big': '--', 'dir': 0, 'price': '', 'series': [], 'labels': [], 'others': []}
    words = []
    if lead:
        hist = _hist(lead)
        st = watch.stats(lead, now) or {}
        seg['series'], _, _ = _series(hist, start, now)
        seg['name'] = FUT_NAME.get(lead[:-2], lead[:-2] + ' futures').upper()
        seg['big'], seg['dir'] = _pct(st.get('close')), _dir(st.get('close'))
        seg['bigv'] = st.get('close')
        seg['price'] = _num(hist[-1][1]) if hist else ''
        seg['labels'] = [_clock(start + k * 6 * 3600000).replace(':00', '') for k in range(4)] + ['now']
        v = st.get('close')
        if v is not None:
            how, line = _since('fut:' + lead, v, now, S(lead))
            if how == 'moved':
                words.append(line + f", at {say_price(hist[-1][1] if hist else None)}.")
            elif how == 'still' and show.get('quick'):
                words.append(_cap(line) + '.')
            else:
                words.append(_cap(f"{S(lead)} are {say_pct(v, True)} since the four o'clock close, at {say_price(hist[-1][1] if hist else None)}."))
            if how != 'still':
                why = _causes(now, v)
                if why:
                    words.append(_cap(_v('why', ['That comes as ', 'Behind it: ', 'For context, ']) + _and(why[:2])) + '.')
                story, _ = _session_story(lead, now)
                if story and not show.get('quick'):
                    words.append(_cap(_v('sess', ['Session by session, ', 'The story so far: ', '']) + story) + '.')
    seg['others'] = [r for r in rows if not lead or r['sym'] != lead[:-2]][:5]
    others = []
    for r in seg['others']:
        if r['pct'] is None:
            continue
        nm = S(r['sym'] + '=F') if lead else r['sym']
        prev, _ = _was('fut:' + r['sym'] + '=F', now)
        _note('fut:' + r['sym'] + '=F', r['pct'], now)
        if prev is not None and abs(r['pct'] - prev) < 0.2 and show.get('quick'):
            continue
        others.append(f"{nm} {say_pct(r['pct'])}")
    if others:
        words.append(_cap(_and(others[:4])) + '.')
    seg['say'] = ' '.join(words) or 'Futures prices are still coming in.'
    return seg


def _movers(p, now, show):
    mv = (p.get('movers') or [])[:6]
    top = max([abs(r.get('chg') or 0) for r in mv] + [0.01])
    rows = [{'sym': r['sym'][:-2] if r['sym'].endswith('=F') else r['sym'], 'last': _num(r.get('last')),
             'val': _pct(r.get('chg')), 'dir': _dir(r.get('chg')), 'bar': round(abs(r.get('chg') or 0) / top, 3),
             'on': ('O/N ' + _pct(r.get('on'))) if r.get('on') is not None else ''} for r in mv]
    words, fresh, same = [], [], []
    for r in mv[:4]:
        how, line = _since('mv:' + r['sym'], r.get('chg'), now, S(r['sym']), thr=0.5)
        if how == 'moved':
            fresh.append(line)
        elif how == 'still':
            same.append(S(r['sym']))
        else:
            fresh.append(f"{S(r['sym'])} {say_pct(r.get('chg'))}")
    if mv:
        lead = mv[0]
        words.append(_v('movers', ['The biggest movers on your board. ', 'Your top movers. ', 'On the movers list. ']))
        if fresh:
            words.append(_cap(_and(fresh)) + '.')
        if same:
            words.append(_cap(_and(same)) + (' are' if len(same) > 1 else ' is') + ' about where ' + ('they were.' if len(same) > 1 else 'it was.'))
        cmp_ = _compare(lead['sym'], lead.get('chg'), 'gap' if p.get('session') != 'Regular' else 'day')
        if cmp_ and not show.get('quick'):
            words.append(f"For {S(lead['sym'])}, {cmp_}.")
        bw = _board_why(lead['sym'], now)
        if bw and not show.get('quick'):
            words.append(f"Why: {bw}.")
    return {'kind': 'movers', 'title': 'TOP MOVERS', 'color': 'amber', 'sub': 'on your board  ·  since the 4 pm close',
            'rows': rows, 'say': ' '.join(words) if words else 'No big movers on your board right now.'}


def _picks(now, p, show):
    cards, words, new = [], [], []
    try:
        import pick
        for c in pick.candidates(now)[:3]:
            why = [_ext(w) for w in c.get('why') or []][:3]
            v = None
            try:
                import mdata
                v = (mdata.price(c['sym'], now) or {}).get('since_close')
            except Exception:
                pass
            cards.append({'sym': c['sym'], 'val': _pct(v), 'dir': _dir(v), 'tag': f"{c.get('group', '')} {c.get('side', '')} 3x".strip(),
                          'score': f"score {c.get('score', 0)}/100", 'why': [_clean(w, 60) for w in why]})
            was = c['sym'] in (_mem['v'].get('picks', [[]])[0] or [])
            if not was and _mem['v'].get('picks'):
                new.append(c['sym'])
            if why:
                words.append(_v('pk' + str(len(words)), [f"{S(c['sym'])}, scoring {c.get('score', 0)}, because {_low(_clean(why[0], 90))}",
                                                          f"{S(c['sym'])} at {c.get('score', 0)} points: {_low(_clean(why[0], 90))}"]))
            else:
                words.append(S(c['sym']))
    except Exception:
        pass
    if not cards:
        try:
            import meters as mt
            pm = (mt.snapshot(now).get('picks') or {}).get('open' if p.get('session') == 'Regular' else 'close') or {}
            for r in (pm.get('parts') or [])[:3]:
                cards.append({'sym': r['sym'], 'val': _pct(r.get('pct')), 'dir': _dir(r.get('pct')), 'tag': r.get('lean') or '',
                              'score': '', 'why': ['from the scanner\'s Picks list']})
                words.append(f"{S(r['sym'])} {say_pct(r.get('pct'))}")
        except Exception:
            pass
    _now_vals['picks'] = [[c['sym'] for c in cards], now]
    if words:
        lead = 'New on the picks list: ' + _and([S(s) for s in new]) + '. ' if new else ''
        same = (not new) and _mem['v'].get('picks') and [c['sym'] for c in cards] == (_mem['v']['picks'][0] or [])
        if same and show.get('quick'):
            say = _v('pk_same', ['Your picks have not changed: ', 'Same picks as before: ']) + _and([S(c['sym']) for c in cards]) + '.'
        else:
            say = lead + _v('pk_head', ['Your top picks right now. ', 'Here is what the rules like. ', 'On the picks board. ']) + \
                ' | '.join(w + '.' for w in words) + ' ' + _v('pk_tail', ['Rules only, not advice.', 'As always, rules, not advice.'])
    else:
        say = 'No picks yet.'
    return {'kind': 'picks', 'title': "TODAY'S PICKS", 'color': 'amber', 'sub': '3x funds the rules like best right now  ·  not advice',
            'cards': cards, 'say': say}


def _news(now, show):
    try:
        import news
        items = news.snapshot().get('items') or []
    except Exception:
        items = []
    out = []
    for i in items[:4]:
        out.append({'title': _clean(i.get('title'), 120), 'source': _clean(i.get('source') or '', 30),
                    'syms': ', '.join((i.get('syms') or [])[:3]), 'ago': _ago(int(i.get('ts') or 0) * 1000, now)})
    seen = set(_mem['news'])
    fresh = [n for n in out if n['title'] not in seen]
    _now_vals['news'] = [n['title'] for n in out]
    if not out:
        say = 'No new headlines for your symbols.'
    elif not fresh and seen:
        if show.get('quick'):
            return None
        say = _v('news_same', ['No new headlines since my last update. The top story is still: ',
                               'Nothing new on the wires for your symbols. Still leading: ']) + out[0]['title'] + '.'
    else:
        say = _v('news', ['In the news. ', 'Headlines for your symbols. ', 'On the news desk. ']) + \
            ' | '.join(f"{n['title']}." for n in (fresh or out)[:3])
    return {'kind': 'news', 'title': 'IN THE NEWS', 'color': 'cyan', 'sub': 'headlines for the symbols on your board',
            'items': out, 'say': say}


def _sector_rows(now):
    def f():
        import analysis
        out = []
        for fd in analysis.heatmap(now).get('funds') or []:
            v = (fd.get('s') or {}).get('total')
            nm = fd.get('sector') or fd.get('sym')
            short = {'Technology': 'tech', 'Communication': 'communication stocks', 'Consumer (discretionary)': 'consumer stocks',
                     'Consumer (staples)': 'consumer staples', 'Financials': 'banks', 'Health care': 'health care',
                     'Energy': 'energy', 'Industrials': 'industrials', 'Materials': 'materials', 'Utilities': 'utilities',
                     'Real estate': 'real estate'}.get(nm, nm.split(' (')[0].lower())
            out.append({'name': nm, 'sym': fd.get('sym'), 'pct': v, 'short': short, 's': fd.get('s') or {}})
        return sorted([r for r in out if r['pct'] is not None], key=lambda r: -r['pct']) + [r for r in out if r['pct'] is None]
    return _cached('sectors', 60, f) or []


def _sectors(now, show):
    rows = _sector_rows(now)
    tiles = [{'name': r['name'], 'sym': r['sym'], 'val': _pct(r['pct']), 'dir': _dir(r['pct']),
              'heat': round(max(-1.0, min(1.0, (r['pct'] or 0) / 2.0)), 3)} for r in rows]
    known = [r for r in rows if r['pct'] is not None]
    say = 'Sectors are still loading.'
    if len(known) >= 2:
        lead, lag = known[0], known[-1]
        prev, mins = _was('sec:lead', now)
        _now_vals['sec:lead'] = [lead['sym'], now]
        changed = prev is not None and prev != lead['sym']
        say = _v('sec', [f"Across the sectors, {lead['short']} {'lead' if lead['short'].endswith('s') else 'leads'}, {say_pct(lead['pct'])}, and {lag['short']} {'lag' if lag['short'].endswith('s') else 'lags'}, {say_pct(lag['pct'])}.",
                         f"Sector check: {lead['short']} on top, {say_pct(lead['pct'])}; {lag['short']} at the bottom, {say_pct(lag['pct'])}."])
        if changed:
            say = f"A change at the top since my last update: {lead['short']} now {'lead' if lead['short'].endswith('s') else 'leads'}. " + say
    return {'kind': 'sectors', 'title': 'SECTORS', 'color': 'cyan', 'sub': 'sector funds since the 4 pm close',
            'tiles': tiles, 'say': say}


def _coming(p, now):
    ev = [{'when': _clock(e['t']) + ('' if watch.et(e['t']).date() == watch.et(now).date() else ' ' + watch.et(e['t']).strftime('%a')),
           'title': _clean(e.get('title'), 60), 't': e['t']} for e in (p.get('events') or []) if e.get('t') and e['t'] > now - 600000][:3]
    al = [{'when': _clock(a['at']) if a.get('at') else '', 'text': _clean(((a.get('sym') + ': ') if a.get('sym') else '') + str(a.get('text') or ''), 90)}
          for a in (p.get('alerts') or [])][:3]
    words = []
    for e in ev:
        mins = int((e['t'] - now) / 60000)
        if 0 < mins < 90:
            words.append(f"{e['title']} in {mins} minutes")
        else:
            words.append(f"{e['title']} at {_say_clock(e['t'])}" + ('' if watch.et(e['t']).date() == watch.et(now).date()
                                                                   else ' ' + watch.et(e['t']).strftime('%A')))
    say = (_v('coming', ['Coming up: ', 'On the calendar: ', 'Still to come: ']) + _and(words) + '.') if words else 'No big reports due.'
    a0 = (p.get('alerts') or [None])[0]
    if a0:
        say += ' | ' + _v('alert', ['Your latest alert: ', 'And the last alert on your board: ']) + \
            ((S(a0.get('sym')) + ', ') if a0.get('sym') else '') + _clean(a0.get('text'), 90) + '.'
    for e in ev:
        e.pop('t', None)
    return {'kind': 'coming', 'title': 'COMING UP', 'color': 'amber', 'sub': 'big reports and your latest alerts',
            'events': ev, 'alerts': al, 'say': say}


def _weather(p):
    w = p.get('weather')
    line = (w.get('line') or '') if isinstance(w, dict) else (w if isinstance(w, str) else '')
    line = _clean(line, 300)
    return {'kind': 'weather', 'title': 'MARKET WEATHER', 'color': 'cyan', 'sub': 'the board\'s one-line forecast',
            'line': line, 'say': line or 'No market weather yet.'}


def _breaking(p, now):
    """Board alerts from the last 20 minutes that the channel has not read out yet."""
    out = []
    for a in p.get('alerts') or []:
        at = a.get('at') or 0
        key = f"{at}:{a.get('text')}"
        if now - at < 20 * 60000 and key not in _st['said']:
            out.append({'text': _clean(((a.get('sym') + ': ') if a.get('sym') else '') + str(a.get('text') or ''), 100), 'key': key,
                        'say': ((S(a.get('sym')) + ', ') if a.get('sym') else '') + _clean(a.get('text'), 100)})
    return out[:2]


# ------------------------------------------------------------------ deep dives (one generic picture: 'dive')
def _dive(key, title, color, sub, say, name='', big=None, bigdir=0, chips=None, points=None, chart=None, banner='', kind='dive'):
    return {'kind': kind, 'pool': key, 'title': title, 'color': color, 'sub': sub, 'name': name,
            'big': big or '', 'bigdir': bigdir, 'chips': (chips or [])[:6], 'points': [p for p in (points or []) if p][:4],
            'chart': chart, 'banner': banner, 'say': say}


def _chart(sym, now, hours=24):
    hist = _hist(sym)
    if not hist:
        return None
    start = now - hours * 3600000
    ser, lo, hi = _series(hist, start, now)
    if not ser:
        return None
    lines = []
    for nm, v in _levels(sym, now, hist)[:3]:
        if lo is not None and hi is not None and lo < v < hi:
            lines.append({'y': round((v - lo) / (hi - lo), 3), 'label': f'{nm} {_num(v)}'})
    st = watch.stats(sym, now) or {}
    return {'series': ser, 'dir': _dir(st.get('close')), 'lines': lines,
            'labels': [_clock(start + k * (hours // 4) * 3600000).replace(':00', '') for k in range(4)] + ['now']}


def _lev3(watchlist, p):
    have = [s for s in watchlist if s in LEV3] + [r['sym'] for r in (p.get('movers') or []) if r.get('sym') in LEV3]
    out = []
    for s in have + ['TQQQ', 'SQQQ', 'SOXL']:
        if s not in out:
            out.append(s)
    return out


def _nth(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _day(iso, now):
    d = watch.et(now).date()
    try:
        from datetime import date
        x = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return 'today' if x == d else 'tomorrow' if (x - d).days == 1 else x.strftime('%A')


def _rng(pos):
    return ('near high ' if pos >= 80 else 'near low ' if pos <= 20 else 'middle ') + f'{pos}%'


def dive_etf(now, p, wl):
    funds = _lev3(wl, p)
    sym = funds[_st['etf'] % len(funds)]
    _st['etf'] += 1
    hist = _hist(sym)
    st = watch.stats(sym, now) or {}
    v = st.get('close')
    if not hist or v is None:
        return None
    last = hist[-1][1]
    words = [_v('etf_in', [f"Let's take a closer look at {S(sym)}.", f"Now a deep dive on {S(sym)}.", f"Spotlight on {S(sym)}."])]
    words.append(f"It's {say_pct(v, True)} since the four o'clock close, at {say_price(last)}.")
    pts = []
    base = BASE.get(sym)
    if base:
        bv = (watch.stats(base[1], now) or {}).get('close')
        if bv is not None:
            exp = base[2] * bv
            gap = v - exp
            words.append(f"Its index futures are {say_pct(bv)}, so three times that would be {say_pct(exp)}" +
                         (f"; it's trading {'richer' if gap > 0 else 'cheaper'} than that by {_amt(gap).replace('about ', '')}." if abs(gap) >= 0.4 else ', and it is right in line.'))
            pts.append(f"Follows {cc_sym(base[1])}: {_pct(bv)} x{base[2]} = {_pct(exp)}")
    story, chips = _session_story(sym, now)
    if story:
        words.append(_cap(story) + '.')
        pts.append('Sessions: ' + ', '.join(f"{c['k'].lower()} {c['v']}" for c in chips))
    cmp_ = _compare(sym, v)
    if cmp_:
        words.append(_cap(cmp_) + '.')
        pts.append('Vs normal: ' + cmp_.replace("that's ", '').replace('that is ', ''))
    stk = _streak(sym)
    if abs(stk) >= 3:
        words.append(f"It has closed {'higher' if stk > 0 else 'lower'} {abs(stk)} days in a row.")
        pts.append(f"{abs(stk)} {'up' if stk > 0 else 'down'} days in a row")
    rg = _ranges(sym, last)
    if '1W' in rg:
        pos = rg['1W']['pos']
        where = 'near the top of' if pos >= 80 else 'near the bottom of' if pos <= 20 else 'in the middle of'
        words.append(f"It sits {where} its one week range" + (f", and {'near its 52 week high' if rg['52W']['pos'] >= 90 else 'near its 52 week low' if rg['52W']['pos'] <= 10 else 'mid way in its 52 week range'}" if '52W' in rg else '') + '.')
        chips.append({'k': '1W RANGE', 'v': _rng(pos), 'dir': 0})
        if '52W' in rg:
            chips.append({'k': '52W RANGE', 'v': _rng(rg['52W']['pos']), 'dir': 0})
    bw = _board_why(sym, now)
    if bw:
        words.append(f'Why it is moving: {bw}.')
        pts.insert(0, 'Why: ' + bw)
    lv = _levels(sym, now, hist)
    above = sorted([x for x in lv if x[1] > last], key=lambda x: x[1])[:1]
    below = sorted([x for x in lv if x[1] < last], key=lambda x: -x[1])[:1]
    watchw = [f"the {nm} at {say_price(v_)} above" for nm, v_ in above] + [f"the {nm} at {say_price(v_)} below" for nm, v_ in below]
    if watchw:
        words.append(_v('watch', ['What to watch: ', 'Levels to watch: ']) + _and(watchw).replace('the the', 'the') + '.')
        pts.append('Watch: ' + ' · '.join(f'{nm} {_num(v_)}' for nm, v_ in above + below))
    ev = _next_event(p, now)
    if ev:
        words.append(f"Next big event: {_clean(ev.get('title'), 60)} at {_say_clock(ev['t'])}.")
    return _dive('etf', f'DEEP DIVE: {sym}', 'amber', 'one of your 3x funds in depth  ·  board numbers only', ' '.join(words),
                 name=sym, big=_pct(v), bigdir=_dir(v), chips=chips, points=pts, chart=_chart(sym, now))


def dive_fut(now, p, wl):
    futs, lead = _fut_lead(wl)
    lead = lead or 'ES=F'
    hist = _hist(lead)
    st = watch.stats(lead, now) or {}
    v = st.get('close')
    if not hist or v is None:
        return None
    last = hist[-1][1]
    words = [_v('fd_in', [f"Futures in depth, starting with {S(lead)}.", f"A closer look at {S(lead)}."])]
    words.append(f"They're {say_pct(v, True)} since the close, at {say_price(last)}.")
    pts, chips = [], []
    story, chips = _session_story(lead, now)
    if story:
        words.append(_cap(story) + '.')
    if st.get('hi') is not None and st.get('lo') is not None and st['hi'] > st['lo']:
        rng = (st['hi'] - st['lo']) / st['lo'] * 100
        pos = (last - st['lo']) / (st['hi'] - st['lo']) * 100
        words.append(f"The overnight range is {_amt(rng).replace('about ', 'about ')} wide, and price is "
                     f"{'near the top' if pos >= 75 else 'near the bottom' if pos <= 25 else 'in the middle'} of it.")
        pts.append(f"Overnight range {_num(st['lo'])} to {_num(st['hi'])}")
        if st.get('broke_hi') or st.get('broke_lo'):
            words.append('It has broken ' + ('above the overnight high.' if st.get('broke_hi') else 'below the overnight low.'))
    base = {'NQ=F': 'QQQ', 'ES=F': 'SPY', 'YM=F': 'DIA', 'RTY=F': 'IWM'}.get(lead)
    if base:
        cmp_ = _compare(base, v)
        if cmp_:
            words.append(_cap(cmp_) + '.')
            pts.append('Vs normal: ' + cmp_.replace("that's ", '').replace('that is ', ''))
    why = _causes(now, v)
    if why:
        words.append(_cap(_and(why[:3])) + '.')
        pts.insert(0, 'Why: ' + _and(why[:3]).replace('*', ''))
    other = [s for s in ('ES=F', 'NQ=F', 'YM=F', 'RTY=F') if s != lead and (watch.stats(s, now) or {}).get('close') is not None]
    if len(other) >= 2:
        vals = {s: watch.stats(s, now)['close'] for s in other}
        best = max(vals, key=vals.get)
        worst = min(vals, key=vals.get)
        words.append(f"{_cap(S(best))} are the strongest of the rest, {say_pct(vals[best])}, and {S(worst)} the weakest, {say_pct(vals[worst])}.")
        for s in other:
            chips.append({'k': s[:-2], 'v': _pct(vals[s]), 'dir': _dir(vals[s])})
    lv = _levels(lead, now, hist)
    up = sorted([x for x in lv if x[1] > last], key=lambda x: x[1])[:1]
    dn = sorted([x for x in lv if x[1] < last], key=lambda x: -x[1])[:1]
    if up or dn:
        words.append('Levels to watch: ' + _and([f"the {nm} at {say_price(x)}" for nm, x in up + dn]).replace('the the', 'the') + '.')
        pts.append('Watch: ' + ' · '.join(f'{nm} {_num(x)}' for nm, x in up + dn))
    return _dive('futdive', 'FUTURES IN DEPTH', 'cyan', 'session story, range and levels  ·  board numbers', ' '.join(words),
                 name=FUT_NAME.get(lead[:-2], lead).upper(), big=_pct(v), bigdir=_dir(v), chips=chips, points=pts, chart=_chart(lead, now))


def dive_macro(now, p, wl):
    rows = [('^TNX', '10Y YIELD'), ('DX-Y.NYB', 'DOLLAR'), ('JPY=X', 'USD/YEN'), ('CL=F', 'OIL'), ('GC=F', 'GOLD'), ('^VIX', 'VIX')]
    chips, said = [], []
    for sym, k in rows:
        v = _wchg(sym)
        if v is None:
            continue
        chips.append({'k': k, 'v': _pct(v), 'dir': _dir(v) * (-1 if sym in ('^VIX',) else 1)})
        if abs(v) >= {'^TNX': 0.8, 'DX-Y.NYB': 0.25, 'JPY=X': 0.4, 'CL=F': 1.0, 'GC=F': 0.6, '^VIX': 3}[sym]:
            said.append(f"{S(sym)} {say_pct(v)}")
    if len(chips) < 3:
        return None
    words = [_v('mac_in', ['Rates, the dollar and oil.', 'The macro board: rates, currencies and commodities.'])]
    if said:
        words.append(_v('mac_mv', ['The moves that matter: ', 'What stands out: ']) + _and(said) + '.')
    else:
        words.append('All quiet: no big moves in yields, the dollar, the yen, oil or gold.')
    why = _causes(now)
    if why:
        words.append(_cap(_and(why[:3])) + '.')
    y = _wchg('JPY=X')
    if y is not None and y <= -0.8:
        words.append('A fast stronger yen has shaken markets before, so that is one to watch.')
    pts = ['Why: ' + _and(why[:3]).replace('*', '')] if why else []
    return _dive('macro', 'RATES · DOLLAR · OIL', 'cyan', 'since the 4 pm close  ·  from the World tab', ' '.join(words),
                 chips=chips, points=pts)


def dive_global(now, p, wl):
    asia = [('^N225', 'TOKYO'), ('^HSI', 'HONG KONG'), ('^KS11', 'SEOUL'), ('^TWII', 'TAIWAN'), ('000001.SS', 'SHANGHAI')]
    eu = [('^GDAXI', 'FRANKFURT'), ('^FTSE', 'LONDON'), ('^FCHI', 'PARIS'), ('^STOXX50E', 'EURO STOXX')]
    chips, words, a_v, e_v = [], [], [], []
    for sym, k in asia + eu:
        v = _wchg(sym)
        if v is None:
            continue
        chips.append({'k': k, 'v': _pct(v), 'dir': _dir(v)})
        (a_v if (sym, k) in asia else e_v).append((sym, v))
    if len(chips) < 3:
        return None
    words.append(_v('gl_in', ['Around the world.', 'A trip around the globe.', 'The global picture.']))
    for grp, nm in ((a_v, 'Asia'), (e_v, 'Europe')):
        if not grp:
            continue
        avg = sum(v for _, v in grp) / len(grp)
        best = max(grp, key=lambda x: x[1])
        words.append(f"{nm} is {'mostly higher' if avg > 0.2 else 'mostly lower' if avg < -0.2 else 'mixed'}, with {S(best[0])} {say_pct(best[1])}.")
    tw, ks = _wchg('^TWII'), _wchg('^KS11')
    if tw is not None and ks is not None and abs(tw + ks) / 2 >= 0.8:
        words.append(f"Taiwan and Seoul, home of the big chip makers, are {'up' if tw + ks > 0 else 'down'}, which often carries over to {S('SOXL')}.")
    nq = _wchg('NQ=F')
    if nq is not None and a_v:
        same = (sum(v for _, v in a_v) > 0) == (nq > 0)
        words.append(f"US futures are {'moving with' if same else 'going against'} Asia, {say_pct(nq)} for {S('NQ=F')}.")
    return _dive('global', 'ASIA & EUROPE', 'cyan', 'stock markets abroad  ·  since their last close', ' '.join(words), chips=chips)


def dive_calendar(now, p, wl):
    ev = [e for e in (p.get('events') or []) if e.get('t') and now < e['t'] < now + 36 * 3600000][:5]
    earn = []
    try:
        import earn as em
        earn = [r for r in (em.snapshot().get('earnings') or []) if r.get('mine') or r.get('held')][:4]
    except Exception:
        pass
    if not ev and not earn:
        return None
    words = [_v('cal_in', ['The calendar for the next day and a half.', 'What is coming on the calendar.'])]
    pts = []
    hi = [e for e in ev if str(e.get('impact')) == 'High' or e.get('kind') == 'fed']
    for e in (hi or ev)[:3]:
        fc = f", forecast {_clean(e.get('forecast'), 12)}" if e.get('forecast') else ''
        pv = f", last time {_clean(e.get('previous'), 12)}" if e.get('previous') else ''
        words.append(f"{_clean(e.get('title'), 60)} at {_say_clock(e['t'])}{fc}{pv}.")
        pts.append(f"{_clock(e['t'])} {watch.et(e['t']).strftime('%a')}  {_clean(e.get('title'), 50)}")
    for r in earn[:3]:
        em_ = (r.get('em') or {}).get('pct')
        t = (r.get('typical') or {}).get('avg')
        line = f"{S(r['sym'])} reports {_day(r.get('date'), now)} {r.get('when') or ''}"
        if em_:
            line += f", with options pricing a move of {_amt(em_)}"
        if t:
            line += f"; its usual earnings move is {_amt(t)}"
        words.append(line + '.')
        pts.append(f"{r['sym']} earnings {r.get('date')} {r.get('when') or ''}" + (f"  ±{em_:.1f}%" if em_ else ''))
    return _dive('calendar', 'THE CALENDAR', 'amber', 'big reports and earnings for your symbols', ' '.join(words), points=pts)


def dive_crypto(now, p, wl):
    b, e = _wchg('BTC-USD'), _wchg('ETH-USD')
    if b is None:
        return None
    wk = p.get('session') in ('Weekend', 'Holiday')
    words = [_v('cr_in', ['Crypto check.', 'A look at crypto.'])]
    words.append(f"{_cap(S('BTC-USD'))} is {say_pct(b, True)} since the last stock market close" + (f", and {S('ETH-USD')} {say_pct(e)}" if e is not None else '') + '.')
    if wk:
        words.append('Crypto trades all weekend, so it is the canary: big weekend moves often show up in Sunday night futures.')
    elif abs(b) >= 3:
        words.append(f"That's a big move, and bitcoin often leads risk appetite, so watch {S('TQQQ')} and the Nasdaq.")
    chips = [{'k': 'BITCOIN', 'v': _pct(b), 'dir': _dir(b)}] + ([{'k': 'ETHER', 'v': _pct(e), 'dir': _dir(e)}] if e is not None else [])
    return _dive('crypto', 'CRYPTO', 'amber', 'trades 24/7  ·  the weekend canary', ' '.join(words), chips=chips,
                 big=_pct(b), bigdir=_dir(b), name='BITCOIN')


def dive_rotation(now, p, wl):
    rows = [r for r in _sector_rows(now) if r['pct'] is not None]
    if len(rows) < 4:
        return None
    words = [_v('rot_in', ['Sector rotation: where the money is going.', 'Following the money across sectors.'])]
    words.append(f"Leaders: {_and([r['short'] for r in rows[:2]])}. Laggards: {_and([r['short'] for r in rows[-2:]])}.")
    defensive = [r['pct'] for r in rows if r['sym'] in ('XLU', 'XLP', 'XLV')]
    growth = [r['pct'] for r in rows if r['sym'] in ('XLK', 'XLC', 'XLY')]
    if defensive and growth:
        d = sum(growth) / len(growth) - sum(defensive) / len(defensive)
        if abs(d) >= 0.4:
            words.append('Money is leaning ' + ('*into* growth and out of the safe sectors, a risk-on sign.' if d > 0
                                                  else '*into* the safe sectors like utilities and staples, a risk-off sign.'))
        else:
            words.append('Growth and defensive sectors are moving together, so no clear rotation.')
    tech = next((r for r in rows if r['sym'] == 'XLK'), None)
    if tech and abs(tech['pct']) >= 0.5:
        words.append(f"With tech {say_pct(tech['pct'])}, {S('TQQQ')} and {S('SOXL')} feel it three times over.")
    chips = [{'k': r['sym'], 'v': _pct(r['pct']), 'dir': _dir(r['pct'])} for r in rows[:3] + rows[-3:]]
    return _dive('rotation', 'SECTOR ROTATION', 'cyan', 'sector funds since the 4 pm close', ' '.join(words), chips=chips)


def dive_fed(now, p, wl):
    try:
        import edge
        o = edge._c.get('odds') or {}
    except Exception:
        return None
    meets = (o.get('meetings') or [])[:2]
    if not meets:
        return None
    words = [_v('fed_in', ['Fed odds, from the betting markets.', 'What traders expect from the Fed.'])]
    chips = []
    for m in meets:
        b = m.get('bucket') or {}
        top = max(b, key=b.get) if b else None
        if not top:
            continue
        nm = _clean(m.get('title'), 50) or 'the next meeting'
        words.append(f"For {nm}: a {top} is priced at {b[top]} percent.")
        wk = m.get('week') or {}
        if wk.get(top) and abs(wk[top]) >= 5:
            words.append(f"That is {'up' if wk[top] > 0 else 'down'} {abs(wk[top])} points in a week.")
        chips += [{'k': k.upper(), 'v': f"{b.get(k, 0)}%", 'dir': 1 if k == 'cut' else -1 if k == 'hike' else 0} for k in ('cut', 'hold', 'hike')][:3]
    return _dive('fed', 'FED ODDS', 'amber', 'from Kalshi\'s public prices  ·  on the board', ' '.join(words), chips=chips[:6])


def dive_positioning(now, p, wl):
    try:
        import edge
        rows = (edge._c.get('cftc') or {}).get('rows') or []
    except Exception:
        return None
    if not rows:
        return None
    words = [_v('pos_in', ['How the big traders are positioned, from the weekly C F T C report.',
                           'Positioning: what the big speculators hold.'])]
    ext = [r for r in rows if r.get('extreme')]
    for r in (ext or rows)[:3]:
        words.append(f"In {_clean(r.get('name'), 30)}, fast money is {r.get('extreme') or ('net long' if (r.get('lev') or 0) > 0 else 'net short')}, "
                     f"at the {_nth(r.get('rank'))} percentile of the last three years.")
    if ext:
        words.append('Crowded trades can unwind fast, which is when 3x funds jump.')
    chips = [{'k': _clean(r.get('name'), 14).upper(), 'v': f"{r.get('rank')}%", 'dir': 1 if (r.get('rank') or 50) >= 50 else -1} for r in rows[:6]]
    return _dive('positioning', 'BIG TRADERS', 'cyan', 'CFTC weekly report  ·  percentile vs 3 years', ' '.join(words), chips=chips)


def _record():
    """The board's own track record from the Report Card: [(name, pct right, calls)]."""
    def f():
        import reportcard
        b = reportcard.badges()
        out = []
        for key, nm in (('fut_mood', 'Futures mood'), ('mood', 'Overnight mood'), ('picks_mood', 'Picks mood')):
            per = b.get(key) or {}
            best = None
            for s, r in per.items():
                if r.get('n') and (best is None or r['n'] > best['n']):
                    best = r
            if best and best.get('pct') is not None:
                out.append({'name': nm, 'pct': best['pct'], 'n': best['n'], 'h': best.get('h') or '', 'src': best.get('src')})
        last = None
        try:
            fe = reportcard.feature('fut_mood')
            main = fe.get('main')
            for r in fe.get('last') or []:
                g = (r.get('g') or {}).get(main)
                if g and g.get('ok') is not None:
                    last = {'dir': r.get('dir'), 'ok': g['ok'], 'at': r.get('at')}
                    break
        except Exception:
            pass
        return {'rows': out, 'last': last}
    return _cached('record', 600, f) or {'rows': [], 'last': None}


def dive_desk(now, p, wl):
    """The desk view (the board's rules) with its track record."""
    score, parts = 0, []
    m = p.get('mood') or {}
    try:
        import meters as mt
        snap = mt.snapshot(now)
        fm = (snap.get('futures') or {}).get('open' if p.get('session') == 'Regular' else 'close') or {}
        pm = (snap.get('picks') or {}).get('open' if p.get('session') == 'Regular' else 'close') or {}
    except Exception:
        fm, pm = {}, {}
    for nm, r in (('the overnight mood', m), ('the futures mood', fm), ('the picks mood', pm)):
        if r and r.get('score') is not None:
            score += int(r['score'])
            parts.append((nm, int(r['score']), r.get('label') or ''))
    if not parts:
        return None
    avg = score / len(parts)
    view = 'leaning risk-on' if avg >= 25 else 'leaning risk-off' if avg <= -25 else 'neutral, with mixed signals'
    agree = len({1 if s >= 25 else -1 if s <= -25 else 0 for _, s, _ in parts}) == 1
    words = [_v('desk_in', ["Now the desk view, from your board's own rules.", 'The board\'s view, by the rules.'])]
    words.append(f"The desk is {view}" + (', and all three meters agree.' if agree and len(parts) == 3 else
                                          ', but the meters disagree, so confidence is lower.' if not agree else '.'))
    rec = _record()
    pts = [f"{nm.replace('the ', '').capitalize()}: {s:+d} {lb}" for nm, s, lb in parts]
    chips = []
    for r in rec['rows']:
        chips.append({'k': r['name'].upper(), 'v': f"{r['pct']}% right", 'dir': 1 if r['pct'] >= 55 else -1 if r['pct'] < 45 else 0})
    if rec['rows']:
        r0 = rec['rows'][0]
        words.append(f"How much to trust it: the {r0['name'].lower()} has been right {r0['pct']} percent of the time, over {r0['n']} graded calls"
                     + (' replayed from history.' if r0.get('src') == 'replay' else '.'))
    last = rec.get('last')
    if last:
        words.append(f"Its last graded call said {'risk-on' if (last.get('dir') or 0) > 0 else 'risk-off'}, and it was {'*right*' if last['ok'] else '*wrong*'}.")
        pts.append(f"Last graded futures-mood call: {'risk-on' if (last.get('dir') or 0) > 0 else 'risk-off'}, {'right' if last['ok'] else 'wrong'}")
    words.append('Rules only, not advice.')
    return _dive('desk', 'THE DESK VIEW', 'amber', 'the board\'s rules  ·  with its Report Card record', ' '.join(words),
                 big=view.split(',')[0].upper(), bigdir=1 if avg >= 25 else -1 if avg <= -25 else 0, chips=chips, points=pts, kind='desk')


POOL = [('etf', dive_etf), ('futdive', dive_fut), ('rotation', dive_rotation), ('macro', dive_macro), ('calendar', dive_calendar),
        ('desk', dive_desk), ('global', dive_global), ('positioning', dive_positioning), ('crypto', dive_crypto), ('fed', dive_fed),
        ('etf', dive_etf)]
POOL_FN = dict(POOL)


def _dives(now, p, wl, show):
    """2 or 3 deep dives, taken in turn from the pool (the show's own picks first), so the next loop is different."""
    out, used = [], set()
    want = show.get('dives', 2)
    for key in show.get('focus') or []:
        if len(out) >= want:
            break
        if key in used:
            continue
        d = _try(POOL_FN[key], now, p, wl)
        used.add(key)
        if d:
            out.append(d)
    tries = 0
    while len(out) < want and tries < len(POOL):
        key, fn = POOL[_st['pool'] % len(POOL)]
        _st['pool'] += 1
        tries += 1
        if key in used:
            continue
        used.add(key)
        d = _try(fn, now, p, wl)
        if d:
            out.append(d)
    return out


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        print(f'channel: {getattr(fn, "__name__", "segment")} failed: {e!r}', flush=True)
        return None


# ------------------------------------------------------------------ specials: big reports and earnings
BIG = re.compile(r'\b(CPI|PPI|PCE|Non-?Farm|Payrolls|Employment|Unemployment|FOMC|Fed|Federal Funds|Powell|GDP|Retail Sales)\b', re.I)


def specials(now, p, wl):
    out = []
    for e in p.get('events') or []:
        t = e.get('t') or 0
        if not (str(e.get('impact')) == 'High' or e.get('kind') == 'fed' or BIG.search(e.get('title') or '')):
            continue
        title = _clean(e.get('title'), 60)
        if 0 < t - now <= 3 * 3600000:
            mins = int((t - now) / 60000)
            cd = f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f'{mins} min'
            hrs = mins // 60
            words = [f"Special report: {title} is due in {(str(hrs) + (' hour' if hrs == 1 else ' hours') + ' and ') if hrs else ''}{mins % 60} minutes, at {_say_clock(t)}."]
            if e.get('forecast'):
                words.append(f"The forecast is {_clean(e.get('forecast'), 14)}" + (f", against {_clean(e.get('previous'), 14)} last time." if e.get('previous') else '.'))
            elif e.get('previous'):
                words.append(f"Last time it came in at {_clean(e.get('previous'), 14)}.")
            words.append('Reports like this can move 3x funds fast, both ways, so spreads may widen around the release.')
            nq = (watch.stats('NQ=F', now) or {}).get('close')
            pts = [f"Due {_clock(t)} ET ({watch.et(t).strftime('%a')})"]
            if e.get('forecast') or e.get('previous'):
                pts.append(f"Forecast {_clean(e.get('forecast'), 12) or '--'}  ·  last time {_clean(e.get('previous'), 12) or '--'}")
            if nq is not None:
                words.append(f"Going in, {S('NQ=F')} are {say_pct(nq)}.")
                pts.append(f"Going in: Nasdaq futures {_pct(nq)}")
            pts.append('3x funds can swing hard both ways; spreads may widen')
            out.append(_dive('special', 'SPECIAL REPORT', 'amber', f'{title}  ·  {_clock(t)} ET', ' '.join(words), name=title.upper(),
                             big=cd, chips=[c for c in ([{'k': 'FORECAST', 'v': _clean(e.get('forecast'), 10), 'dir': 0}] if e.get('forecast') else []) +
                                            ([{'k': 'LAST TIME', 'v': _clean(e.get('previous'), 10), 'dir': 0}] if e.get('previous') else [])],
                             points=pts, kind='special'))
        elif 0 <= now - t <= 60 * 60000:
            moves, chips = [], []
            for s in ('NQ=F', 'ES=F'):
                h = _hist(s)
                a, b = watch.price_at(h, t - 60000) if h else None, (h[-1][1] if h else None)
                if a and b:
                    v = (b / a - 1) * 100
                    moves.append(f"{S(s)} {say_pct(v, True)}")
                    chips.append({'k': s[:-2] + ' SINCE', 'v': _pct(v), 'dir': _dir(v)})
            if not moves:
                continue
            words = [f"What just happened: {title} came out at {_say_clock(t)}.", 'Since then, ' + _and(moves) + '.']
            if e.get('forecast') or e.get('previous'):
                words.append(f"It was expected at {_clean(e.get('forecast'), 12) or 'no forecast'}, after {_clean(e.get('previous'), 12) or 'no number'} last time.")
            pts = [f"Out at {_clock(t)} ET", f"Forecast {_clean(e.get('forecast'), 12) or '--'}  ·  last time {_clean(e.get('previous'), 12) or '--'}"] + \
                  [f"{c['k'].replace(' SINCE', '')} since the release {c['v']}" for c in chips]
            out.append(_dive('special', 'WHAT JUST HAPPENED', 'red', f'{title}  ·  {_clock(t)} ET', ' '.join(words), name=title.upper(),
                             chips=chips, points=pts, kind='special'))
        if len(out) >= 2:
            break
    try:
        import earn as em
        d = watch.et(now)
        for r in em.snapshot().get('earnings') or []:
            if not (r.get('mine') or r.get('held')) or len(out) >= 2:
                continue
            day = r.get('date') or ''
            today = day == d.date().isoformat()
            tomorrow = day == (d.date().fromordinal(d.date().toordinal() + 1)).isoformat()
            when = (r.get('when') or '').lower()
            em_ = (r.get('em') or {}).get('pct')
            if (today and 'after' in when and d.hour < 16) or (tomorrow and 'before' in when and d.hour >= 16):
                words = [f"Earnings special: {S(r['sym'])} reports {r.get('when') or ''}."]
                if em_:
                    words.append(f"Options are pricing a move of {_amt(em_)} either way.")
                t = (r.get('typical') or {}).get('avg')
                if t:
                    words.append(f"Over its last reports it moved {_amt(t)} on average.")
                words.append('Funds that hold it, like the 3x tech funds, can swing with it.')
                pts = [f"{r['sym']} reports {r.get('when') or ''} ({day})"] + ([f'Options expect ±{em_:.1f}%'] if em_ else []) + \
                      ([f'Usual earnings move {t:.1f}%'] if t else [])
                out.append(_dive('special', 'EARNINGS SPECIAL', 'amber', f"{r['sym']} reports {r.get('when') or ''}", ' '.join(words),
                                 name=r['sym'], big=(f'±{em_:.1f}%' if em_ else ''), points=pts, chart=_chart(r['sym'], now), kind='special'))
            elif (today and 'before' in when and 9 <= d.hour < 12) or (today and 'after' in when and d.hour >= 16):
                v = (watch.stats(r['sym'], now) or {}).get('close')
                if v is None:
                    continue
                words = [f"What just happened: {S(r['sym'])} reported, and it's {say_pct(v, True)} since the close."]
                if em_:
                    words.append(f"Options had priced {_amt(em_)}, so the move came in {'bigger' if abs(v) > em_ else 'smaller'} than expected.")
                out.append(_dive('special', 'WHAT JUST HAPPENED', 'red', f"{r['sym']} earnings", ' '.join(words), name=r['sym'],
                                 big=_pct(v), bigdir=_dir(v), kind='special'))
    except Exception as e:
        print(f'channel: earnings special failed: {e!r}', flush=True)
    return out[:2]


# ------------------------------------------------------------------ quiet-hours explainers (from the board's own numbers)
def _ex_decay(now, p, wl, banner):
    try:
        import edge
        rows = edge.decay_rows()
    except Exception:
        return None
    pick = next((r for s in _lev3(wl, p) for r in rows if r.get('sym') == s and r.get('d20')), None)
    if not pick:
        return None
    d = pick['d20']
    words = [f"Explainer: why 3x funds lose value in choppy weeks. Take {S(pick['sym'])}.",
             f"Over the last 20 trading days, {S(pick['base'])} {_moved(d['naive'] / pick['lev'] if pick['lev'] else 0)}.",
             f"Three times that would be {say_pct(d['naive'])}, but {S(pick['sym'])} actually {_moved(d['actual'])}.",
             f"The gap, {_amt(d['cost'])}, is what resetting to 3x every day costs when the market goes back and forth.",
             'In a steady trend it can work for you; in chop it quietly eats away, which is why these funds suit short holds.']
    pts = [f"{pick['base']} x{pick['lev']} would be {_pct(d['naive'])}", f"{pick['sym']} actually {_pct(d['actual'])}",
           f"Daily-reset cost {_pct(d['cost'])} in 20 days"]
    return _dive('ex_decay', 'EXPLAINER: 3X DECAY', 'purple', 'from your board\'s daily prices', ' '.join(words), points=pts,
                 big=_pct(d['cost']), bigdir=_dir(d['cost']), name=pick['sym'], banner=banner, kind='explainer')


def _ex_gaps(now, p, wl, banner):
    try:
        import analysis
        for sym in _lev3(wl, p)[:3] + ['QQQ', 'SPY']:
            sc = analysis.scorecard(sym, now)
            up, dn = (sc.get('year') or {}).get('up') or {}, (sc.get('year') or {}).get('down') or {}
            if up.get('n', 0) >= 20 and dn.get('n', 0) >= 20:
                break
        else:
            return None
    except Exception:
        return None
    hu, fu = round(100 * up['held'] / up['n']), round(100 * up['filled'] / up['n'])
    hd, fd = round(100 * dn['held'] / dn['n']), round(100 * dn['filled'] / dn['n'])
    words = [f"Explainer: how often overnight gaps hold into the open. Over the last year of {S(sym)}:",
             f"when it opened higher, it held that gap to the close {hu} percent of the time, and fell back to the old close {fu} percent of the time.",
             f"When it opened lower, the gap held {hd} percent of the time and filled {fd} percent.",
             'So a gap is a starting point, not a promise, and the first hour often decides it.']
    pts = [f"Gap up: held {hu}%, filled {fu}% ({up['n']} days)", f"Gap down: held {hd}%, filled {fd}% ({dn['n']} days)"]
    return _dive('ex_gaps', 'EXPLAINER: GAPS', 'purple', f'{sym}  ·  gap scorecard, last year', ' '.join(words), points=pts,
                 name=sym, chips=[{'k': 'GAP UP HELD', 'v': f'{hu}%', 'dir': 0}, {'k': 'GAP DOWN HELD', 'v': f'{hd}%', 'dir': 0}],
                 banner=banner, kind='explainer')


def _ex_night(now, p, wl, banner):
    rows = _daily('QQQ')[-61:]
    if len(rows) < 40:
        return None
    night = day = 0.0
    for i in range(1, len(rows)):
        if rows[i - 1][4] and rows[i][1]:
            night += math.log(rows[i][1] / rows[i - 1][4])
            day += math.log(rows[i][4] / rows[i][1])
    n_p, d_p = (math.exp(night) - 1) * 100, (math.exp(day) - 1) * 100
    words = [f"Explainer: when does the market really move? Over the last {len(rows) - 1} trading days of {S('QQQ')},",
             f"the moves between the close and the next open, added together, {_moved(n_p)},",
             f"while the regular sessions, open to close, {_moved(d_p)}.",
             'That is why the overnight and pre-market sessions you trade can matter as much as the day itself.']
    return _dive('ex_night', 'EXPLAINER: NIGHT VS DAY', 'purple', 'QQQ  ·  from daily prices on your board', ' '.join(words),
                 chips=[{'k': 'CLOSE TO OPEN', 'v': _pct(n_p), 'dir': _dir(n_p)}, {'k': 'OPEN TO CLOSE', 'v': _pct(d_p), 'dir': _dir(d_p)}],
                 banner=banner, kind='explainer')


def _ex_bigdays(now, p, wl, banner):
    rows = _daily('SPY')[-253:]
    if len(rows) < 120:
        return None
    ch = [(rows[i][4] / rows[i - 1][4] - 1) * 100 for i in range(1, len(rows)) if rows[i - 1][4]]
    big = sum(1 for c in ch if abs(c) >= 1)
    huge = sum(1 for c in ch if abs(c) >= 2)
    every = round(len(ch) / big) if big else 0
    words = [f"Explainer: how often are there big days? Over the last {len(ch)} trading days, {S('SPY')} moved more than 1 percent on {big} days"
             + (f', about one day in {every}.' if every else '.'),
             f"It moved more than 2 percent on {huge} days.",
             'For a 3x fund, a 1 percent day is about a 3 percent day, and a 2 percent day is about 6 percent.']
    return _dive('ex_bigdays', 'EXPLAINER: BIG DAYS', 'purple', 'SPY  ·  last year of daily prices', ' '.join(words),
                 chips=[{'k': 'OVER 1% DAYS', 'v': str(big), 'dir': 0}, {'k': 'OVER 2% DAYS', 'v': str(huge), 'dir': 0}],
                 banner=banner, kind='explainer')


EXPLAINERS = [_ex_decay, _ex_gaps, _ex_night, _ex_bigdays]


def _explainers(now, p, wl, n, banner):
    out, tries = [], 0
    while len(out) < n and tries < len(EXPLAINERS):
        fn = EXPLAINERS[_st['expl'] % len(EXPLAINERS)]
        _st['expl'] += 1
        tries += 1
        d = _try(fn, now, p, wl, banner)
        if d:
            out.append(d)
    return out


# ------------------------------------------------------------------ the voices
def _say_list():
    def f():
        if not shutil.which('say'):
            return []
        r = subprocess.run(['say', '-v', '?'], capture_output=True, text=True, timeout=20)
        out = []
        for line in r.stdout.splitlines():
            m = re.match(r'^(.+?)\s{2,}(en[_-]\w+)', line)
            if m:
                out.append(m.group(1).strip())
        return out
    return _cached('saylist', 3600, f) or []


def piper_ok():
    try:
        import importlib.util
        return importlib.util.find_spec('sherpa_onnx') is not None and os.path.isfile(PIPER_RUN)
    except Exception:
        return False


def _piper_list():
    out = []
    try:
        for n in sorted(os.listdir(PIPER_DIR)):
            d = os.path.join(PIPER_DIR, n)
            if os.path.isdir(d) and any(x.endswith('.onnx') for x in os.listdir(d)) and os.path.isfile(os.path.join(d, 'tokens.txt')):
                out.append(n)
    except OSError:
        pass
    return out


def _nice(v):
    """'piper:vits-piper-en_US-lessac-medium' -> 'Lessac (natural)'."""
    if v.startswith('piper:'):
        m = re.search(r'en_(US|GB)-([a-z_]+?)-(low|medium|high)', v)
        return (m.group(2).replace('_', ' ').title() + (' (British)' if m.group(1) == 'GB' else '') + ' · natural') if m else v[6:]
    return v[4:] + ' · Mac voice'


def voice_cfg():
    """{'a': voice id, 'b': voice id, 'chime': bool, 'speed': float}. Ids: 'piper:<folder>' or 'say:<name>'."""
    try:
        c = json.load(open(VOICE_CFG, encoding='utf-8'))
    except Exception:
        c = {}
    base = os.environ.get('JB_CHANNEL_VOICE', '').strip() or os.environ.get('JB_RECAP_VOICE', '').strip() or 'Samantha'
    pipers = _piper_list() if piper_ok() else []
    good = c.get('piper_rtf') is None or (c.get('piper_rtf') or 9) <= 0.8      # install measured it fast enough
    def_a = ('piper:' + next((v for v in pipers if 'lessac' in v), pipers[0])) if pipers and good else 'say:' + base
    def_b = ('piper:' + next((v for v in pipers if 'ryan' in v), pipers[-1])) if len(pipers) > 1 and good else 'say:' + base
    out = {'a': c.get('a') or def_a, 'b': c.get('b') or def_b, 'chime': c.get('chime', True), 'speed': float(c.get('speed') or 1.0)}
    for k in ('a', 'b'):
        v = out[k]
        if v.startswith('piper:') and (v[6:] not in pipers):
            out[k] = 'say:' + base
    return out


def _rate():
    try:
        return int(os.environ.get('JB_CHANNEL_RATE', '') or os.environ.get('JB_RECAP_RATE', '') or 167)
    except ValueError:
        return 167


def _tone(rate, freqs, each=0.16, gap=0.02, tail=0.3, vol=0.33):
    pcm = array.array('h')
    for f in freqs:
        n = int(rate * each)
        for i in range(n):
            t = i / rate
            env = math.exp(-t * 9) * min(1.0, i / (rate * 0.004))
            s = math.sin(2 * math.pi * f * t) * 0.8 + math.sin(2 * math.pi * f * 2 * t) * 0.2
            pcm.append(int(32000 * vol * env * s))
        pcm.extend([0] * int(rate * gap))
    pcm.extend([0] * int(rate * tail))
    return pcm


def chime(rate, kind):
    """A short news-show chime made on the Mac (two notes; three rising notes for BREAKING)."""
    if kind == 'breaking':
        return _tone(rate, [880, 1175, 1568, 1568], each=0.14, tail=0.35)
    if kind == 'explainer':
        return _tone(rate, [659, 880], each=0.2, tail=0.3, vol=0.25)
    return _tone(rate, [1319, 1047], tail=0.28, vol=0.28)


def _make_wav(text_script, voice, speed, wav):
    """Make the anchor's words into wav (22050 Hz, 16 bit, mono). Piper first, the Mac voice if that fails."""
    if voice.startswith('piper:'):
        d = os.path.join(PIPER_DIR, voice[6:])
        cmd = ([shutil.which('nice'), '-n', '10'] if shutil.which('nice') else []) + [sys.executable, PIPER_RUN, d, wav, f'{speed:.2f}']
        try:
            r = subprocess.run(cmd, input=render(text_script, 'piper'), text=True, timeout=240, capture_output=True)
            if r.returncode == 0 and os.path.getsize(wav) > 1000:
                return 'piper'
            print(f"channel: Piper voice failed, using the Mac voice: {(r.stderr or '')[-200:]}", flush=True)
        except Exception as e:
            print(f'channel: Piper voice failed, using the Mac voice: {e!r}', flush=True)
        voice = 'say:' + (os.environ.get('JB_CHANNEL_VOICE', '').strip() or os.environ.get('JB_RECAP_VOICE', '').strip() or 'Samantha')
    if not shutil.which('say'):
        return ''
    name = voice[4:] if voice.startswith('say:') else voice
    rate = int(_rate() * speed)
    cmd = ['say', '-o', wav, '--file-format=WAVE', '--data-format=LEI16@22050', '-r', str(rate)]
    txt = render(text_script, 'say')
    r = subprocess.run(cmd + ['-v', name, txt], timeout=120, capture_output=True)
    if r.returncode != 0:                                      # that voice isn't installed: the default one
        subprocess.run(cmd + [txt], timeout=120, capture_output=True)
    return 'say'


def _speak(text_script, anchor='a', kind=''):
    """-> (file name, seconds). The same words are made into sound only once."""
    cfg = voice_cfg()
    voice = cfg[anchor]
    ch = cfg['chime'] and kind != ''
    h = hashlib.sha1(f"{voice}|{_rate()}|{cfg['speed']}|{ch and kind}|{text_script}".encode()).hexdigest()[:16]
    os.makedirs(OUT, exist_ok=True)
    for ext in ('m4a', 'wav'):
        f = os.path.join(OUT, f'{h}.{ext}')
        if os.path.exists(f) and os.path.exists(f + '.len'):
            try:
                os.utime(f)
                os.utime(f + '.len')
                return f'{h}.{ext}', float(open(f + '.len').read())
            except (ValueError, OSError):
                pass
    if not shutil.which('say') and not (voice.startswith('piper:') and piper_ok()):
        return '', max(5.0, len(text_script.split()) / 2.6)    # not a Mac: no voice, timed by word count
    tmp = tempfile.mkdtemp(prefix='jb-channel-')
    try:
        wav = os.path.join(tmp, 'v.wav')
        if not _make_wav(text_script, voice, cfg['speed'], wav) or not os.path.exists(wav):
            return '', max(5.0, len(text_script.split()) / 2.6)
        with wave.open(wav, 'rb') as w:
            rate, frames, sw, nch = w.getframerate(), w.readframes(w.getnframes()), w.getsampwidth(), w.getnchannels()
        if ch and sw == 2 and nch == 1:                        # the chime first, then the anchor
            out = os.path.join(tmp, 'c.wav')
            with wave.open(out, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                w.writeframes(chime(rate, kind).tobytes() + frames)
            wav = out
        with wave.open(wav, 'rb') as w:
            secs = w.getnframes() / float(w.getframerate())
        name = f'{h}.wav'
        if shutil.which('afconvert'):                          # small AAC file (built into macOS)
            m4a = os.path.join(OUT, f'{h}.m4a')
            if subprocess.run(['afconvert', '-f', 'm4af', '-d', 'aac', '-b', '64000', wav, m4a],
                              timeout=60, capture_output=True).returncode == 0:
                name = f'{h}.m4a'
        if name.endswith('.wav'):
            shutil.copy(wav, os.path.join(OUT, name))
        with open(os.path.join(OUT, name + '.len'), 'w') as f:
            f.write(f'{secs:.2f}')
        return name, secs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tidy(keep):
    """Delete sound files that no longer belong to the current rundown (and are over 30 minutes old)."""
    try:
        for n in os.listdir(OUT):
            base = n[:-4] if n.endswith('.len') else n
            if not NAME.match(base):
                continue
            p = os.path.join(OUT, n)
            if base not in keep and time.time() - os.path.getmtime(p) > 1800:
                os.remove(p)
    except OSError:
        pass


# ------------------------------------------------------------------ the rundown
def _watchlist():
    try:
        syms = json.load(open(os.path.join(DATA, 'ibkr_watchlist.json'), encoding='utf-8'))
        return [str(x) for x in syms if isinstance(x, str)]
    except Exception:
        return []


def build():
    now = int(time.time() * 1000)
    _mem_load()
    _now_vals.clear()
    _st['cache'] = {k: v for k, v in _st['cache'].items() if k in ('record', 'saylist')}
    try:
        import extras
        p = json.loads(extras.phone())
    except Exception:
        p = {}
    h = watch.et(now).hour
    greet = 'Good morning' if 4 <= h < 12 else 'Good afternoon' if 12 <= h < 17 else 'Good evening'
    wl = _watchlist()
    show = show_for(now, p)
    banner = 'QUIET HOURS  ·  1–3 AM ET  ·  EXPLAINERS' if show.get('quiet') else 'WEEKEND DESK  ·  EXPLAINER' if show['key'] == 'weekend' else ''
    segs = []
    brk = _breaking(p, now)
    for b in brk:
        segs.append({'kind': 'breaking', 'title': 'BREAKING', 'color': 'red', 'sub': 'a board alert just fired',
                     'line': b['text'], 'say': _v('brk', ['Breaking news on your board. ', 'This just in. ', 'Breaking. ']) + b['say'] + '.'})
    futs, lead = _fut_lead(wl)
    fut = {'v': (watch.stats(lead, now) or {}).get('close') if lead else _wchg('NQ=F')}
    segs.append(_try(_open, p, now, greet, show, fut))
    if show.get('quiet'):
        segs.append(_try(_futures, now, wl, p, dict(show, quick=True)))
        segs += _explainers(now, p, wl, show.get('explain', 2), banner)
    else:
        segs += _try(specials, now, p, wl) or []
        dives = _dives(now, p, wl, show)
        heads = [lambda: _futures(now, wl, p, show), lambda: _movers(p, now, show), lambda: _picks(now, p, show)]
        if not show.get('quick'):
            heads += [lambda: _news(now, show), lambda: _sectors(now, show), lambda: _coming(p, now), lambda: _weather(p)]
        else:
            heads += [lambda: _news(now, show)]
        for i, mk in enumerate(heads):
            segs.append(_try(mk))
            if i in (2, 4) and dives:              # deep dives between the headlines: after picks and after sectors ...
                segs.append(dives.pop(0))
        segs += dives                              # ... and the rest at the end
        if show.get('explain'):
            segs += _explainers(now, p, wl, show['explain'], banner)
    segs = [s for s in segs if s]
    for s in segs:
        script = s['say']
        s['say'] = render(script, 'say').replace('[[emph +]]', '').replace(' [[slnc 450]] ', ' ')   # plain words (players show nothing of it)
        s['cc'] = render(script, 'cc')            # captions: symbols written as tickers, no marks
        s['_script'] = script
        s['anchor'] = 'b' if s['kind'] in ANCHOR_B else 'a'
        s['show'] = show['name']
    names = []
    for i, s in enumerate(segs):
        try:
            s['audio'], secs = _speak(s.pop('_script'), s['anchor'], s['kind'])
        except Exception as e:
            print(f'channel: voice failed: {e!r}', flush=True)
            s.pop('_script', None)
            s['audio'], secs = '', max(5.0, len(s['say'].split()) / 2.6)
        s['dur'] = round(max(6.0, secs + 1.2), 1)              # a short pause after the anchor finishes
        if s['audio']:
            names.append(s['audio'])
        if i == 2 and _st['rundown'] is None:                  # the very first show: start playing while the rest is made
            with _lock:
                _st['rundown'] = _pack(now, p, show, banner, [x for x in segs[:3]], names, partial=True)
                _st['built'] = time.time() - REBUILD_S + 20
    for b in brk:
        _st['said'].add(b['key'])
    if len(_st['said']) > 200:
        _st['said'] = set(list(_st['said'])[-100:])
    _mem['v'].update(_now_vals)
    if 'news' in _now_vals:
        _mem['news'] = (_mem['news'] + [t for t in _now_vals['news'] if t not in _mem['news']])[-60:]
        _mem['v'].pop('news', None)
    _mem_save()
    _tidy(set(names))
    return _pack(now, p, show, banner, segs, names)


def _pack(now, p, show, banner, segs, names, partial=False):
    ticker = []
    for r in (p.get('movers') or [])[:10]:
        sym = r['sym'][:-2] if r['sym'].endswith('=F') else r['sym']
        ticker.append({'t': f"{sym} {_pct(r.get('chg'))}", 'dir': _dir(r.get('chg'))})
    for s in segs:
        if s['kind'] == 'news':
            ticker += [{'t': n['title'], 'dir': 0} for n in s['items'][:3]]
    total = sum(s.get('dur') or 0 for s in segs)
    return {'ok': True, 'built': _clock(now) + ' ET', 'built_ms': now, 'session': p.get('session') or '',
            'show': show['name'], 'quiet': bool(show.get('quiet')), 'banner': banner, 'total': round(total),
            'segments': [dict(s) for s in segs], 'ticker': ticker, 'voice': bool(names), 'partial': partial}


def _builder():
    while True:
        try:
            with _lock:
                r = _st['rundown']
                every = max(REBUILD_S, ((r or {}).get('total') or 0) - 75)    # a fresh show is ready as the last one ends
                want = (time.time() - _st['asked'] < WATCHED_S and time.time() - _st['built'] > every)
            if want:
                with _lock:
                    _st['building'] = True
                try:
                    r = build()
                    with _lock:
                        _st['rundown'], _st['built'], _st['error'] = r, time.time(), ''
                except Exception as e:
                    print(f'channel: build failed: {e!r}', flush=True)
                    with _lock:
                        _st['error'] = f'{e}'[:160]
                        _st['built'] = time.time()
                finally:
                    with _lock:
                        _st['building'] = False
        except Exception:
            pass
        time.sleep(3)


def _start():
    with _lock:
        if _st['thread'] is None:
            _st['thread'] = threading.Thread(target=_builder, name='channel', daemon=True)
            _st['thread'].start()


def corner():
    """The live corner box: two futures (S&P and Nasdaq first) and the clock."""
    now = int(time.time() * 1000)
    futs = [s for s in _watchlist() if s.endswith('=F')]
    futs = [s for s in ('ES=F', 'NQ=F') if s in futs] + [s for s in futs if s not in ('ES=F', 'NQ=F')]
    out = []
    for s in futs[:2]:
        st = watch.stats(s, now) or {}
        out.append({'sym': s[:-2], 'val': _pct(st.get('close')), 'dir': _dir(st.get('close'))})
    return {'futs': out, 'clock': _clock(now).replace(' AM', '').replace(' PM', '')}


def rundown(only_corner=False):
    """The newest rundown (made in the background); 'wait' while the first one is being made."""
    _start()
    try:
        c = corner()
    except Exception:
        c = {'futs': [], 'clock': ''}
    if only_corner:
        return json.dumps({'ok': True, 'corner': c})
    with _lock:
        _st['asked'] = time.time()
        r = _st['rundown']
        if r is None:
            return json.dumps({'ok': False, 'wait': True, 'corner': c,
                               'error': _st['error'] or 'Making the first show (under a minute)…'})
        return json.dumps(dict(r, age=int(time.time() - _st['built']), corner=c))


def audio(name, range_header):
    """(status, body, content type, extra headers) for one sound file; honours 'Range: bytes=a-b'."""
    path = os.path.join(OUT, name or '')
    if not NAME.match(name or '') or not os.path.isfile(path):
        return 404, b'not found', 'text/plain; charset=utf-8', {}
    ctype = 'audio/mp4' if name.endswith('.m4a') else 'audio/wav'
    size = os.path.getsize(path)
    extra = {'Accept-Ranges': 'bytes', 'Cache-Control': 'private, max-age=3600'}
    m = re.match(r'^bytes=(\d*)-(\d*)$', (range_header or '').strip())
    if not m or (m.group(1) == '' and m.group(2) == ''):
        with open(path, 'rb') as f:
            return 200, f.read(), ctype, extra
    if m.group(1) == '':
        a, b = max(0, size - int(m.group(2))), size - 1
    else:
        a = int(m.group(1))
        b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if a >= size or a > b:
        extra['Content-Range'] = f'bytes */{size}'
        return 416, b'', 'text/plain; charset=utf-8', extra
    with open(path, 'rb') as f:
        f.seek(a)
        body = f.read(b - a + 1)
    extra['Content-Range'] = f'bytes {a}-{b}/{size}'
    return 206, body, ctype, extra


def tv(method, query, headers):
    """/api/tvchannel for the Roku: the TV key (as for /api/tv), read only."""
    if method not in ('GET', 'HEAD'):
        return 405, json.dumps({'ok': False, 'error': 'read only'}), 'application/json', {}
    import tvview
    import hmac
    given = ((query or {}).get('key') or [''])[0]
    if not given or not hmac.compare_digest(given.encode(), tvview.key().encode()):
        return 403, json.dumps({'ok': False, 'error': 'wrong TV key: make the TV app again'}), 'application/json', {}
    f = ((query or {}).get('f') or [''])[0]
    if f:
        return audio(f, headers.get('range', ''))
    return 200, rundown(), 'application/json', {}


def web(method, path, query, headers):
    """/api/channel and /api/channel/audio for the Channel page (after the board login)."""
    if method not in ('GET', 'HEAD'):
        return 405, 'method not allowed', 'text/plain; charset=utf-8', {}
    if path == '/api/channel/audio':
        return audio(((query or {}).get('f') or [''])[0], headers.get('range', ''))
    return 200, rundown(bool(((query or {}).get('corner') or [''])[0])), 'application/json', {}


SAMPLE = ("Good evening, Alex. {NQ=F} are *up* about one and a half percent tonight, and {NVDA} is leading. | "
          "Since my last update, {SQQQ} slipped another half percent.")
SAY_PICKS = ['Samantha', 'Ava', 'Zoe', 'Allison', 'Susan', 'Tom', 'Evan', 'Nathan', 'Alex', 'Daniel', 'Karen', 'Moira', 'Serena']


def voices(method, query, data):
    """/api/channel/voices (board login): GET lists the voices and the two anchors' picks; ?sample=<id> makes a
    short sample (then play /api/channel/audio?f=<file>); POST {a, b, chime, speed} saves the picks."""
    if method == 'POST':
        cfg = {}
        try:
            cfg = json.load(open(VOICE_CFG, encoding='utf-8'))
        except Exception:
            pass
        ok_ids = set(['piper:' + v for v in _piper_list()] + ['say:' + v for v in _say_list()] + ['say:Samantha'])
        for k in ('a', 'b'):
            if k in data:
                if str(data[k]) not in ok_ids:
                    return 400, json.dumps({'ok': False, 'error': 'unknown voice'}), 'application/json', {}
                cfg[k] = str(data[k])
        if 'chime' in data:
            cfg['chime'] = bool(data['chime'])
        if 'speed' in data:
            try:
                cfg['speed'] = max(0.75, min(1.3, float(data['speed'])))
            except (TypeError, ValueError):
                pass
        tmp = VOICE_CFG + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f)
        os.replace(tmp, VOICE_CFG)
        with _lock:
            _st['built'] = 0.0                     # the next show uses the new voices
        return 200, json.dumps({'ok': True, 'cfg': voice_cfg()}), 'application/json', {}
    if method not in ('GET', 'HEAD'):
        return 405, json.dumps({'ok': False}), 'application/json', {}
    sample = ((query or {}).get('sample') or [''])[0]
    if sample:
        if sample.startswith('piper:') and sample[6:] not in _piper_list():
            return 404, json.dumps({'ok': False, 'error': 'not installed'}), 'application/json', {}
        try:
            os.makedirs(OUT, exist_ok=True)
            name, secs = _speak_with(sample, SAMPLE)
            return 200, json.dumps({'ok': bool(name), 'f': name, 'secs': secs}), 'application/json', {}
        except Exception as e:
            return 200, json.dumps({'ok': False, 'error': str(e)[:160]}), 'application/json', {}
    pipers = _piper_list() if piper_ok() else []
    have = set(_say_list())
    says = [v for v in SAY_PICKS if v in have] or (['Samantha'] if shutil.which('say') else [])
    lst = [{'id': 'piper:' + v, 'name': _nice('piper:' + v), 'kind': 'piper'} for v in pipers] + \
          [{'id': 'say:' + v, 'name': _nice('say:' + v), 'kind': 'say'} for v in says]
    try:
        rtf = json.load(open(VOICE_CFG, encoding='utf-8')).get('piper_rtf')
    except Exception:
        rtf = None
    return 200, json.dumps({'ok': True, 'voices': lst, 'cfg': voice_cfg(), 'piper': piper_ok(), 'piper_rtf': rtf}), 'application/json', {}


def _speak_with(voice, script):
    """A sample clip in one voice (kept like any other clip, tidied later)."""
    h = hashlib.sha1(f'sample|{voice}|{script}'.encode()).hexdigest()[:16]
    for ext in ('m4a', 'wav'):
        f = os.path.join(OUT, f'{h}.{ext}')
        if os.path.exists(f) and os.path.exists(f + '.len'):
            return f'{h}.{ext}', float(open(f + '.len').read())
    tmp = tempfile.mkdtemp(prefix='jb-sample-')
    try:
        wav = os.path.join(tmp, 'v.wav')
        if not _make_wav(script, voice, voice_cfg()['speed'], wav) or not os.path.exists(wav):
            return '', 0
        with wave.open(wav, 'rb') as w:
            secs = w.getnframes() / float(w.getframerate())
        name = f'{h}.wav'
        if shutil.which('afconvert'):
            if subprocess.run(['afconvert', '-f', 'm4af', '-d', 'aac', '-b', '64000', wav, os.path.join(OUT, f'{h}.m4a')],
                              timeout=60, capture_output=True).returncode == 0:
                name = f'{h}.m4a'
        if name.endswith('.wav'):
            shutil.copy(wav, os.path.join(OUT, name))
        with open(os.path.join(OUT, name + '.len'), 'w') as f:
            f.write(f'{secs:.2f}')
        return name, secs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
