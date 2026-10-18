"""Shared Webull OpenAPI helpers used by every Webull piece (board bridge, mover scanner,
Webull trading algo), so they behave as ONE Webull client toward Webull:

  * One token folder. Webull's SDK (3.x) may require a one-time approval of API access in the
    Webull app (a 2FA-style token). The approved token is saved to WEBULL_OPENAPI_TOKEN_DIR,
    which ./bin/run-service.sh points at data/webull-token for every service, so a single
    approval (./jb webull-login) covers the bridge, the scanner and the algo.
  * A cross-process lock around client creation, so two services starting at once can never
    each fire their own approval request at your phone -- the second one waits, then reuses
    the token the first one saved.
  * The SDK's own logging kept to warnings on stdout (its default also writes an ever-growing
    webull_data_streaming_sdk.log into whatever folder the process runs in).
"""
import contextlib
import logging
import os
import sys
import time

try:
    import fcntl
except ImportError:  # pragma: no cover  (not on macOS/Linux)
    fcntl = None

PROD_HTTP = 'api.webull.com'
PROD_MQTT = 'data-api.webull.com'          # host only -- the SDK passes the port (1883) separately
UAT_HTTP = 'api.sandbox.webull.com'
UAT_MQTT = 'data-api.sandbox.webull.com'


def hosts(environment='prod'):
    return (PROD_HTTP, PROD_MQTT) if environment == 'prod' else (UAT_HTTP, UAT_MQTT)


def token_dir():
    d = os.environ.get('WEBULL_OPENAPI_TOKEN_DIR')
    if d:
        os.makedirs(d, exist_ok=True)
    return d


@contextlib.contextmanager
def _file_lock(path):
    if fcntl is None:
        yield
        return
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def sdk_logger():
    lg = logging.getLogger('jb.webull.sdk')
    if not lg.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter('%(asctime)s webull-sdk %(levelname)s %(message)s'))
        lg.addHandler(h)
        lg.setLevel(logging.WARNING)
    return lg


class NeedsApproval(Exception):
    """Webull wants API access approved in the app, and this is a background service: it must
    not fire approval requests at the phone by itself -- ./jb webull-login does that."""


_TOKEN_REQUIRED = None      # learnt from Webull at the first client creation in this process


def token_state():
    """'ok' | 'missing' | 'pending' | 'expired' for the saved approval token (read locally --
    nothing is sent to Webull)."""
    d = os.environ.get('WEBULL_OPENAPI_TOKEN_DIR') or 'conf'
    path = os.path.join(os.path.abspath(os.path.expanduser(d)), 'token.txt')
    try:
        with open(path, encoding='utf-8') as f:
            lines = [ln.strip() for ln in f.read().splitlines()]
    except OSError:
        return 'missing'
    if len(lines) < 3 or not lines[0]:
        return 'missing'
    if lines[2] != 'NORMAL':
        return 'pending'
    try:
        exp = int(lines[1])
        exp_s = exp / 1000 if exp > 1e11 else exp
        if 0 < exp_s < time.time():
            return 'expired'
    except ValueError:
        pass
    return 'ok'


def _token_check_enabled(api):
    """Does Webull require the approval token for this App Key? (A plain config request --
    it does NOT send anything to the phone.)"""
    try:
        from webull.core.http.initializer.client_initializer import ClientInitializer
        return bool(ClientInitializer._check_token_enable(api))
    except (ImportError, AttributeError):
        return True             # SDK changed: assume it's required rather than risk prompts


def would_prompt():
    """True when connecting now would certainly send an approval request to the phone (no
    token saved yet, or one still waiting for approval). An 'expired'-looking token is still
    tried: Webull may have extended it, and if not, that one attempt is the reminder push."""
    return token_state() in ('missing', 'pending')


def stream_allowed():
    """May a background process (re)connect the streaming client? Each connect re-checks the
    token with Webull, which would send a new approval request if it's not valid."""
    return _TOKEN_REQUIRED is False or not would_prompt()


def _quiet_sdk_logging(api, level):
    """Without this, every DataClient/TradeClient the SDK creates also writes an ever-growing
    webull_data_sdk.log (INFO level) into the current folder and chats on stdout."""
    lg = logging.getLogger('webull.core')
    if not lg.handlers and hasattr(api, 'set_stream_logger'):
        api.set_stream_logger(log_level=level, logger_name='webull.core', stream=sys.stdout,
                              format_string='%(asctime)s webull-sdk %(levelname)s %(message)s')
    else:
        lg.setLevel(level)
    for attr in ('_stream_logger_set',):
        try:
            setattr(api, attr, True)
        except Exception:
            pass


def make_clients(app_key, app_secret, region='us', environment='prod', trade=False, interactive=False):
    """(api_client, data_client, trade_client_or_None), created under the shared lock.

    interactive=False (every background service): if Webull needs an approval that hasn't been
    given yet, raise NeedsApproval instead of sending a request to the phone. Only
    ./jb webull-login (and foreground commands like ./jb backtest) use interactive=True."""
    global _TOKEN_REQUIRED
    from webull.core.client import ApiClient
    from webull.data.data_client import DataClient
    http_host, _ = hosts(environment)
    d = token_dir()
    lock = os.path.join(d or os.getcwd(), '.webull-init.lock')
    with _file_lock(lock):
        api = ApiClient(app_key, app_secret, region)
        api.add_endpoint(region, http_host)
        _quiet_sdk_logging(api, logging.INFO if interactive else logging.WARNING)
        if not interactive and would_prompt():
            _TOKEN_REQUIRED = _token_check_enabled(api)
            if _TOKEN_REQUIRED:
                raise NeedsApproval(f'Webull API access needs approving (token {token_state()})')
        data = DataClient(api)
        if _TOKEN_REQUIRED is None:
            _TOKEN_REQUIRED = token_state() != 'missing'
        tc = None
        if trade:
            from webull.trade.trade_client import TradeClient
            tc = TradeClient(api)
    return api, data, tc


def describe_error(e):
    """Readable text for any SDK error (ServerException's repr() is just 'ServerException()')."""
    text = str(e).strip()
    return f'{type(e).__name__}: {text}' if text else repr(e)


def is_auth_problem(exc):
    if isinstance(exc, NeedsApproval):
        return True
    s = ' '.join(str(x) for x in (describe_error(exc), getattr(exc, 'error_code', ''),
                                   getattr(exc, 'error_msg', ''))).upper()
    return any(k in s for k in ('TOKEN', 'UNAUTHORIZED', '401', '403', 'SIGNATURE', 'APP_KEY', 'APPKEY'))


AUTH_HELP = ('Webull API access needs approving -- on the spare Mac run:  ./jb webull-login  '
             '(and approve the request in the Webull app). Also check WEBULL_APP_KEY/SECRET in settings.env.')
APPROVAL_POLL_S = 30        # background services re-check for the approval this often


def wait_for_approval(stop=None):
    """Block until ./jb webull-login has saved an approval, checking the token file (locally,
    nothing sent to Webull) every 30 s. Returns False if `stop` (a threading.Event) was set."""
    while would_prompt():
        if stop is not None:
            if stop.wait(APPROVAL_POLL_S):
                return False
        else:
            time.sleep(APPROVAL_POLL_S)
    return True


# ------------------------------------------------------------------ history bars
TIMESPAN_SECONDS = {'M1': 60, 'M5': 300, 'M15': 900, 'M30': 1800, 'M60': 3600, 'M120': 7200,
                    'M240': 14400, 'D': 86400, 'W': 7 * 86400, 'M': 30 * 86400, 'Y': 365 * 86400}
TIMESPAN_ALIASES = {'D1': 'D', '1D': 'D', 'DAY': 'D', 'DAILY': 'D', 'H1': 'M60', '1H': 'M60', 'H4': 'M240',
                    '1M': 'M1', '5M': 'M5', '15M': 'M15', '30M': 'M30', 'W1': 'W', 'WEEK': 'W'}


def normalize_timespan(ts):
    t = str(ts).strip().upper()
    t = TIMESPAN_ALIASES.get(t, t)
    if t not in TIMESPAN_SECONDS:
        raise ValueError(f"unknown Webull timespan {ts!r}; use one of {', '.join(TIMESPAN_SECONDS)} "
                         f"(M1 = 1-minute bars, M60 = hourly, D = daily)")
    return t


def unwrap_bars(data, symbol=None):
    """Webull returns bars as a flat list, as {'result': [...]}, or -- what get_history_bar()
    returns in SDK 3.x -- grouped [{'symbol':..., 'result': [...]}]. Returns the bar list."""
    if isinstance(data, dict):
        data = data.get('result', data.get('data', []))
    if not isinstance(data, list):
        return []
    if data and isinstance(data[0], dict) and 'result' in data[0]:
        for g in data:
            if symbol is None or str(g.get('symbol', '')).upper() == str(symbol).upper():
                return g.get('result') or []
        return data[0].get('result') or []
    return data


_ET = None
_seen_formats = set()


def bar_start_ms(v, span_s):
    """A bar's time field -> epoch ms of the bar START. Handles epoch seconds/ms (number or
    text), ISO-8601 with a zone, a bare date (daily bars -> that day, 9:30am ET) and a
    zone-less 'YYYY-MM-DD HH:MM[:SS]' (read as US/Eastern, the market's clock). Prints one
    sample per format the first time it appears, so the log shows what Webull really sent."""
    global _ET
    import datetime as dt
    if _ET is None:
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo('America/New_York')
    if v is None:
        return None
    s = str(v).strip()
    kind = None
    try:
        if isinstance(v, (int, float)) or s.replace('.', '', 1).isdigit():
            kind = 'epoch'
            n = float(s)
            return int(n * 1000 if n < 1e11 else n)
        if len(s) == 10 and s[4] == '-' and s[7] == '-':
            kind = 'date'
            d = dt.date.fromisoformat(s)
            return int(dt.datetime(d.year, d.month, d.day, 9, 30, tzinfo=_ET).timestamp() * 1000)
        iso = s.replace('Z', '+00:00')
        if len(iso) > 5 and iso[-5] in '+-' and iso[-4:].isdigit() and iso[-3] != ':':
            iso = iso[:-2] + ':' + iso[-2:]
        d = dt.datetime.fromisoformat(iso if 'T' in iso else iso.replace(' ', 'T', 1))
        kind = 'iso'
        if d.tzinfo is None:
            kind = 'naive (read as US/Eastern)'
            d = d.replace(tzinfo=_ET)
        return int(d.timestamp() * 1000)
    except Exception:
        kind = 'UNPARSEABLE'
        return None
    finally:
        if kind not in _seen_formats:
            _seen_formats.add(kind)
            print(f'[webull bar time] {kind}: e.g. {s!r}', flush=True)


def ohlcv_rows(data, symbol, timespan):
    """Response JSON -> [{'t': start_ms, 'open','high','low','close','volume'}] oldest first."""
    span = TIMESPAN_SECONDS[normalize_timespan(timespan)]
    rows = []
    for b in unwrap_bars(data, symbol):
        if not isinstance(b, dict):
            continue
        t = bar_start_ms(b.get('time', b.get('timestamp', b.get('t'))), span)
        try:
            o, h, l, c = (float(b[k]) for k in ('open', 'high', 'low', 'close'))
        except (KeyError, TypeError, ValueError):
            continue
        try:
            v = float(b.get('volume') or 0)
        except (TypeError, ValueError):
            v = 0.0
        if t is not None and c > 0:
            rows.append({'t': t, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v})
    rows.sort(key=lambda r: r['t'])
    out = []
    for r in rows:                        # drop duplicate timestamps, keep the last
        if out and out[-1]['t'] == r['t']:
            out[-1] = r
        else:
            out.append(r)
    return out


def _as_list(data):
    if isinstance(data, dict):
        for k in ('data', 'result', 'list', 'items', 'instruments', 'contracts'):
            if isinstance(data.get(k), list):
                return data[k]
        return [data] if data.get('symbol') else []
    return data if isinstance(data, list) else []


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


MONTH_CODES = 'FGHJKMNQUVXZ'


def _contract_month(code, root):
    """'ESZ6' -> (2026, 12); None if the code isn't ROOT + month letter + year digits."""
    suf = code[len(root):]
    if len(suf) < 2 or suf[0] not in MONTH_CODES or not suf[1:].isdigit():
        return None
    now_y = time.gmtime().tm_year
    y = int(suf[1:])
    year = 2000 + y if len(suf) > 2 else now_y - now_y % 10 + y
    if year < now_y - 1:
        year += 10
    return year, MONTH_CODES.index(suf[0]) + 1


def front_month(data_client, root):
    """Webull names futures by contract (ESZ6, not ES). Returns the contract of `root` that is
    trading most (open interest, then volume) -- the one a futures board should follow --
    or the nearest unexpired month if Webull gives no activity figures. None if unknown."""
    root = root.upper()
    inst = data_client.instrument
    items = []
    for call in (lambda: inst.get_futures_instrument(category='US_FUTURES', code=root),
                 lambda: inst.get_futures_instrument_by_code(root, 'US_FUTURES', 'MONTHLY')):
        try:
            items = _as_list(call().json())
        except Exception:
            items = []
        if items:
            break
    codes = sorted({str(it.get('symbol') or '').upper() for it in items if isinstance(it, dict)}
                   - {'', root, root + 'MAIN'})
    codes = [c for c in codes if c.startswith(root)]
    if not codes:
        return None
    if len(codes) > 1:
        try:
            snap = _as_list(data_client.futures_market_data.get_futures_snapshot(
                ','.join(codes[:100]), 'US_FUTURES').json())
            rows = [r for r in snap if isinstance(r, dict) and str(r.get('symbol', '')).upper() in codes]
            best = max(rows, key=lambda r: (_num(r.get('open_interest')), _num(r.get('volume'))), default=None)
            if best is not None and (_num(best.get('open_interest')) or _num(best.get('volume'))):
                return str(best['symbol']).upper()
        except Exception:
            pass
        now = time.gmtime()
        dated = sorted((m, c) for c in codes if (m := _contract_month(c, root)) and m >= (now.tm_year, now.tm_mon))
        if dated:
            return dated[0][1]
    return codes[0]


def fetch_bars(data_client, symbol, timespan, count=1200, sessions=('PRE', 'RTH', 'ATH', 'OVN'), contract=None):
    """History for a stock ('AAPL') or a future ('ES=F' -> its current contract, or `contract`),
    via the right Webull endpoint."""
    ts = normalize_timespan(timespan)
    count = str(max(1, min(int(count), 1200)))
    if symbol.endswith('=F'):
        code = contract or front_month(data_client, symbol[:-2])
        if not code:
            return []
        # (a plain string: the SDK would send a Python list as the text "['ESZ6']")
        res = data_client.futures_market_data.get_futures_history_bars(code, 'US_FUTURES', ts, count=count)
        return ohlcv_rows(res.json(), code, ts)
    md = data_client.market_data
    if sessions and ts in ('M1', 'M5', 'M15', 'M30', 'M60', 'M120', 'M240'):
        tries = [list(sessions)]
        if 'OVN' in sessions:                 # a plan without the overnight session: keep pre-market and after-hours
            tries.append([x for x in sessions if x != 'OVN'])
        for sess in tries:
            try:
                res = md.get_history_bar(symbol, 'US_STOCK', ts, count=count, trading_sessions=sess)
                return ohlcv_rows(res.json(), symbol, ts)
            except Exception as e:
                print(f'[webull history] {symbol} {ts} with sessions {",".join(sess)} failed ({describe_error(e)}); '
                      + ('trying without overnight' if 'OVN' in sess and len(tries) > 1 else 'retrying regular hours only'), flush=True)
    res = md.get_history_bar(symbol, 'US_STOCK', ts, count=count)
    return ohlcv_rows(res.json(), symbol, ts)



# ------------------------------------------------------------------ overnight data (hedge-2026-10-12, Webull overnight ready)
# Webull's overnight session (8 pm - 4 am ET) needs its own data plan. Without it, Webull refuses a subscription that
# asks for overnight prices; the board bridge, the scanner feed and the Webull algo then stream without them. They
# check again every OVERNIGHT_RECHECK_S and once just before each overnight session (OVERNIGHT_RECHECK_ET), so a plan
# added during the day works that evening -- no ./jb restart needed (a restart checks at once). What each one found is
# written to <data>/webull_overnight.json, which the algo tab and the ? Legend show.
OVERNIGHT_RECHECK_S = int(os.environ.get('JB_OVERNIGHT_RECHECK_S') or 3 * 3600)    # (tests set it shorter)
OVERNIGHT_RECHECK_ET = (19, 45)          # 7:45 pm New York, on the evening before a trading day


def _ny(ts):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo('America/New_York'))


def overnight_recheck_due(last_check, now=None):
    """True when the plan should be checked again for overnight prices: OVERNIGHT_RECHECK_S since the last check, or
    7:45 pm New York (on an evening before a trading day) has passed since it."""
    now = now or time.time()
    if not last_check or now - last_check >= OVERNIGHT_RECHECK_S:
        return True
    try:
        from datetime import timedelta
        import nyse_hours
        d = _ny(now)
        mark = d.replace(hour=OVERNIGHT_RECHECK_ET[0], minute=OVERNIGHT_RECHECK_ET[1], second=0, microsecond=0)
        return (last_check < mark.timestamp() <= now) and nyse_hours.trading_day(d.date() + timedelta(days=1))
    except Exception:
        return False


def overnight_path():
    d = os.environ.get('JB_DATA_DIR') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    return os.path.join(d, 'webull_overnight.json')


def note_overnight(part, on, why=''):
    """Records what `part` ('board', 'scanner', 'algo') found about Webull's overnight prices: on = True (Webull
    streams them), False (the plan has no overnight session). Never raises."""
    import json
    path = overnight_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _file_lock(path + '.lock'):
            try:
                with open(path, encoding='utf-8') as f:
                    d = json.load(f)
            except Exception:
                d = {}
            if not isinstance(d, dict):
                d = {}
            old = d.get(part) or {}
            now = int(time.time() * 1000)
            d[part] = {'on': bool(on), 'checked_at': now, 'why': why or '',
                       'since': old.get('since') if old.get('on') == bool(on) and old.get('since') else now}
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(d, f)
            os.replace(tmp, path)
    except Exception as e:
        print(f'could not note the overnight state: {e!r}', flush=True)


def overnight_state():
    """{part: {'on', 'checked_at', 'since', 'why'}} as last noted, plus 'on' overall: what the newest check found
    (True / False), or None when nothing has been checked yet."""
    import json
    try:
        with open(overnight_path(), encoding='utf-8') as f:
            d = json.load(f)
    except Exception:
        d = {}
    parts = {k: v for k, v in (d if isinstance(d, dict) else {}).items() if isinstance(v, dict)}
    latest = max(parts.values(), key=lambda v: v.get('checked_at') or 0, default=None)   # the newest answer from Webull wins
    return {'parts': parts, 'on': None if latest is None else bool(latest.get('on')),
            'recheck_hours': OVERNIGHT_RECHECK_S // 3600, 'recheck_et': '%d:%02d pm' % (OVERNIGHT_RECHECK_ET[0] - 12, OVERNIGHT_RECHECK_ET[1])}


def overnight_refused(e):
    """Webull said no only because this plan lacks the overnight session
    (403 MARKET_DATA_NOT_SUBSCRIBED ... please subscribe to US_STOCK OVERNIGHT)."""
    s = ' '.join(str(x) for x in (describe_error(e), getattr(e, 'error_code', ''), getattr(e, 'error_msg', ''))).upper()
    return ('MARKET_DATA_NOT_SUBSCRIBED' in s or 'INSUFFICIENT PERMISSION' in s) and 'OVERNIGHT' in s
