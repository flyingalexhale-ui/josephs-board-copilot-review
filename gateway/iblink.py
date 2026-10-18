"""One shared, always-on connection to IB Gateway for the October 2026 final-ideas features
(jb-orders and the parts after it), running inside the gateway.

Used for: fill alerts (today's executions, every 30 s), the order preview ("what if"), IBKR's free
newswire, and checking prices / positions. It never places orders itself: orders still go through
the order ticket's own connection (client id 69), so they can be followed and cancelled there.

Client id 71 (journal 67, SELL ALL 68, ticket 69, SELL ALL preview 70, bridge and algos others).
If IB Gateway is down or restarting (its nightly restart), calls fail quickly with a plain message
and the link reconnects by itself every 30 seconds.
"""
import asyncio
import threading
import time

IB_CLIENT_ID = 71
_st = {'ib': None, 'loop': None, 'port': None, 'connected': False, 'error': '', 'since': 0, 'started': False}
_lock = threading.Lock()


def _ports():
    import killswitch as ks
    return ks._ibkr_ports()


async def _connect():
    from ib_async import IB
    last = None
    for port in _ports():
        ib = IB()
        try:
            await ib.connectAsync('127.0.0.1', port, clientId=IB_CLIENT_ID, timeout=8, readonly=False)
            return ib, port
        except Exception as e:
            last = e
            try:
                ib.disconnect()
            except Exception:
                pass
    raise RuntimeError(f'IB Gateway not reachable ({last!r})')


async def _keeper():
    while True:
        ib = _st['ib']
        if ib is None or not ib.isConnected():
            _st['connected'] = False
            try:
                ib, port = await _connect()
                _st.update(ib=ib, port=port, connected=True, error='', since=time.time())
            except Exception as e:
                _st.update(ib=None, connected=False, error=str(e)[:200])
        await asyncio.sleep(30 if not _st['connected'] else 5)


def _run_loop(loop):
    asyncio.set_event_loop(loop)
    loop.create_task(_keeper())
    loop.run_forever()


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
        loop = asyncio.new_event_loop()
        _st['loop'] = loop
    threading.Thread(target=_run_loop, args=(loop,), name='iblink', daemon=True).start()


def connected():
    ib = _st['ib']
    return bool(ib is not None and ib.isConnected())


def status():
    return {'connected': connected(), 'error': _st['error'], 'since': int(_st['since'] * 1000) if _st['since'] else None}


def run(fn, timeout=20):
    """Run `fn(ib)` (an async function) on the link's own loop and return its result.
    Raises RuntimeError with a plain message when IB Gateway isn't connected."""
    start()
    if not connected():
        raise RuntimeError('IB Gateway is not connected right now' + (f' ({_st["error"][:80]})' if _st['error'] else ''))
    fut = asyncio.run_coroutine_threadsafe(fn(_st['ib']), _st['loop'])
    try:
        return fut.result(timeout)
    except TimeoutError:
        fut.cancel()
        raise RuntimeError('IB Gateway took too long to answer')
    except asyncio.TimeoutError:
        fut.cancel()
        raise RuntimeError('IB Gateway took too long to answer')


def account():
    """The account the board trades (IBKR_ACCOUNT_ID, or the only one), or None."""
    import killswitch as ks
    try:
        return ks._ib_account(_st['ib']) if connected() else None
    except Exception:
        return None


async def stock(ib, sym):
    from ib_async import Stock
    q = await asyncio.wait_for(ib.qualifyContractsAsync(Stock(sym, 'SMART', 'USD')), 10)
    if not q or q[0] is None:
        raise RuntimeError(f'IBKR did not recognise {sym}')
    return q[0]
