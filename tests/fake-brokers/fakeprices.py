"""Made-up prices shared by the fake IB Gateway and the fake Webull SDK: the same symbol has the same price at
the same moment on both (so the board's IBKR-vs-Webull price check stays quiet), and it moves in slow and fast
waves so the strategies see crossovers within minutes."""
import hashlib
import math

ACCOUNT = 'DU1234567'          # an IBKR paper account (DU...), so every screen says "paper"
BASE = {'TQQQ': 72.0, 'SQQQ': 18.0, 'SOXL': 31.0, 'SOXS': 6.5, 'SPY': 660.0, 'QQQ': 590.0, 'IWM': 240.0, 'NVDA': 180.0,
        'AAPL': 245.0, 'TSLA': 420.0, 'ES': 6700.0, 'NQ': 24500.0, 'YM': 46000.0, 'RTY': 2450.0, 'CL': 64.0, 'GC': 3900.0}


def _h(sym):
    return int(hashlib.md5(sym.encode()).hexdigest()[:8], 16)


def root(sym):
    s = sym.upper().replace(' ', '.')
    if s.endswith('=F'):
        return s[:-2]
    for r in ('RTY', 'ES', 'NQ', 'YM', 'CL', 'GC'):        # Webull futures contracts: ESZ6 -> ES
        if s.startswith(r) and len(s) in (len(r) + 2, len(r) + 3) and s[len(r)] in 'FGHJKMNQUVXZ':
            return r
    return s


def price_at(sym, t):
    s = root(sym)
    b = BASE.get(s) or 15 + _h(s) % 300
    h = _h(s)
    x = (0.02 * math.sin(t / 10800 * 2 * math.pi + h % 7) + 0.006 * math.sin(t / 600 * 2 * math.pi + h % 5)
         + 0.0015 * math.sin(t / 37 + h % 11))
    return round(b * (1 + x), 2)


def con_id(sym):
    return 100000 + _h(root(sym)) % 900000
