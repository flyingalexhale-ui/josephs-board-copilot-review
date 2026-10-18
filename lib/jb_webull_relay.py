"""One Webull price connection for the whole board (jb-onestream, 2026-10-12).

Webull gives stock prices to only ONE streaming connection per App Key at a time: a second one takes them over
("Permission grabbed by other session", notice 1002) and the first goes quiet. The board's Webull bridge, the scanner
and the Webull algo each had their own connection, so from October 12 they kept knocking each other off.

Now only the bridge (bridges/webull_bridge.py, port 8766 on this Mac) talks to Webull's stream. The scanner and the
Webull algo get their prices from it through RelayStreamingClient, which looks like the Webull SDK's
DataStreamingClient (on_connect_success, on_quotes_message, on_disconnect, subscribe, unsubscribe,
connect_and_loop_forever, disconnect), so their code is unchanged:
  * subscribe() asks the bridge to stream those symbols for this client too ("follow"); they are NOT added to the
    board's watchlist. With overnight_required=True it is refused, as Webull refuses it, while the bridge's Webull plan
    has no overnight session -- so the overnight fallback and re-check code keeps working.
  * every trade the bridge receives for a followed symbol comes back as an SDK-like tick (.basic.symbol,
    .basic.trading_session, .time, .price, .volume).
  * the bridge down or restarting = the link dropped: on_disconnect(client, None, 1), then the caller reconnects.
JB_WEBULL_DIRECT=1 goes back to a direct Webull connection (tests of the fake SDK only).
"""
import json
import os
import threading
import types

HOST = os.environ.get('JB_WEBULL_RELAY_HOST', '127.0.0.1')
PORT = int(os.environ.get('JB_WEBULL_RELAY_PORT', '8766') or 8766)
TOKEN = os.environ.get('WEBULL_BRIDGE_TOKEN', '')
ANSWER_S = 20                     # how long subscribe() waits for the bridge's answer


class RelayRefused(Exception):
    """The bridge said no; the text is Webull's own wording when it is about the plan (overnight)."""

    def __init__(self, msg):
        super().__init__(msg)
        self.error_code = 'MARKET_DATA_NOT_SUBSCRIBED' if 'NOT_SUBSCRIBED' in msg.upper() else 'RELAY'
        self.error_msg = msg


def direct():
    return os.environ.get('JB_WEBULL_DIRECT', '') == '1'


def streaming_client_class():
    """The class the scanner and the algo stream with: the bridge relay, or Webull itself (JB_WEBULL_DIRECT=1)."""
    if direct():
        from webull.data.data_streaming_client import DataStreamingClient
        return DataStreamingClient
    return RelayStreamingClient


class RelayStreamingClient:
    def __init__(self, app_key=None, app_secret=None, region_id=None, session_id='relay', http_host=None,
                 mqtt_host=None, *a, **k):
        self.session_id = session_id
        self.on_connect_success = None
        self.on_quotes_message = None
        self.on_disconnect = None
        self._thread_terminate = False
        self._ws = None
        self._send_lock = threading.Lock()
        self._subs = set()
        self._answers = {}            # request id -> [event, reply]
        self._n = 0
        self._stop = threading.Event()

    # ---------- the SDK's interface ----------
    def connect_and_loop_forever(self, logger_enable=True, customer_logger=None):
        from websockets.sync.client import connect
        url = f'ws://{HOST}:{PORT}/'
        try:
            ws = connect(url, open_timeout=10, close_timeout=2, max_size=2 ** 22)
        except Exception as e:
            raise ConnectionError(f'the board\'s Webull bridge is not answering on port {PORT} ({e!r}) -- '
                                  f'is the webull service running?') from None
        self._ws = ws
        try:
            if TOKEN:
                self._send({'type': 'auth', 'token': TOKEN})
            self._send({'type': 'hello', 'who': self.session_id})
            if self.on_connect_success:
                self.on_connect_success(self, None, self.session_id)
            while not self._stop.is_set() and not self._thread_terminate:
                try:
                    raw = ws.recv(timeout=1)
                except TimeoutError:
                    continue
                self._handle(raw)
        except Exception as e:
            if not (self._stop.is_set() or self._thread_terminate):
                if type(e).__name__.startswith('ConnectionClosed'):
                    e = 'the bridge closed the link'
                if self.on_disconnect:
                    try:
                        self.on_disconnect(self, None, 1)
                    except Exception:
                        pass
                raise ConnectionError(f'Webull bridge link lost ({e})') from None
        finally:
            self._ws = None
            for ev, box in list(self._answers.values()):
                ev.set()
            try:
                ws.close()
            except Exception:
                pass

    def subscribe(self, symbols, category=None, sub_types=None, depth=None, overnight_required=None):
        syms = [str(s).upper() for s in ([symbols] if isinstance(symbols, str) else symbols)]
        r = self._ask({'type': 'follow', 'add': syms, 'overnight': bool(overnight_required)})
        if not r.get('ok'):
            raise RelayRefused(r.get('error') or 'the bridge refused the subscription')
        self._subs.update(syms)
        return r

    def unsubscribe(self, symbols=None, category=None, sub_types=None, unsubscribe_all=False):
        syms = list(self._subs) if unsubscribe_all else \
            [str(s).upper() for s in ([symbols] if isinstance(symbols, str) else (symbols or []))]
        r = self._ask({'type': 'follow', 'remove': syms})
        self._subs.difference_update(syms)
        return r

    def disconnect(self):
        self._stop.set()

    # ---------- internals ----------
    def _send(self, obj):
        ws = self._ws
        if ws is None:
            raise ConnectionError('not connected to the Webull bridge')
        with self._send_lock:
            ws.send(json.dumps(obj, separators=(',', ':')))

    def _ask(self, obj):
        with self._send_lock:
            self._n += 1
            rid = f'{self.session_id}-{self._n}'
        ev, box = threading.Event(), {}
        self._answers[rid] = (ev, box)
        try:
            self._send(dict(obj, id=rid))
            if not ev.wait(ANSWER_S) or not box:
                raise ConnectionError('the Webull bridge did not answer')
            return box
        finally:
            self._answers.pop(rid, None)

    def _handle(self, raw):
        try:
            m = json.loads(raw)
        except ValueError:
            return
        kind = m.get('type') if isinstance(m, dict) else None
        if kind == 'fticks':
            cb = self.on_quotes_message
            for t in m.get('ticks') or []:
                sym = t.get('symbol')
                q = types.SimpleNamespace(
                    basic=types.SimpleNamespace(symbol=sym, trading_session=t.get('session'),
                                                timestamp=str(t.get('ts'))),
                    price=str(t.get('price')), time=str(t.get('ts')), volume=str(t.get('vol') or 0))
                if cb:
                    try:
                        cb(self, f'{sym}-TICK', q)
                    except Exception:
                        pass
        elif kind == 'follow_ok' or kind == 'follow_err':
            a = self._answers.get(m.get('id'))
            if a:
                a[1].update(m, ok=(kind == 'follow_ok'))
                a[0].set()

