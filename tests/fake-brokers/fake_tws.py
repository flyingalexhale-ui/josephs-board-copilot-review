"""A fake IB Gateway for live runs without IBKR: speaks the real TWS API wire protocol (so ib_async
and every board service run unchanged), streams made-up prices for any stock or future it is asked
about, answers history, positions, open orders, executions and account requests -- and REFUSES
every order (a live run must never place one: a placeOrder here is logged loudly as a bug).

usage:  python3 tests/fake-brokers/fake_tws.py [--port 4002]
Touch <logdir>/fake_tws.drop to drop every connection (like IB Gateway's nightly restart) and
refuse new ones until the file is removed.
"""
import argparse
import asyncio
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakeprices import price_at, con_id, ACCOUNT  # noqa: E402

SERVER_VERSION = 178
FUT_MONTH = '20261218'

def log(*a):
    print(time.strftime('%H:%M:%S'), 'fake-tws', *a, flush=True)


def msg(*fields):
    body = ''.join(f'{f}\0' for f in fields).encode()
    return struct.pack('>I', len(body)) + body


class Conn:
    def __init__(self, server, reader, writer):
        self.s, self.r, self.w = server, reader, writer
        self.subs = {}            # reqId -> (symbol, kind)  kind: 'mkt' | 'tbt'
        self.client_id = None
        self.alive = True

    def send(self, *fields):
        if self.alive:
            self.w.write(msg(*fields))

    async def read_msg(self):
        head = await self.r.readexactly(4)
        n = struct.unpack('>I', head)[0]
        body = await self.r.readexactly(n)
        f = body.decode(errors='replace').split('\0')
        if f and f[-1] == '':
            f.pop()
        return f

    async def run(self):
        try:
            start = await self.r.readexactly(4)
            if start != b'API\0':
                return
            await self.read_msg()                                # "v157..178"
            self.w.write(msg(SERVER_VERSION, time.strftime('%Y%m%d %H:%M:%S') + ' EST'))
            while self.alive:
                f = await self.read_msg()
                self.handle(f)
                await self.w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.alive = False
            self.s.conns.discard(self)
            try:
                self.w.close()
            except Exception:
                pass

    @staticmethod
    def contract(f, i):
        """The 12 contract fields the client sends: conId, symbol, secType, lastTradeDate, strike, right,
        multiplier, exchange, primaryExchange, currency, localSymbol, tradingClass."""
        keys = ('conId', 'symbol', 'secType', 'last', 'strike', 'right', 'mult', 'exchange', 'primary', 'currency',
                'local', 'tclass')
        return dict(zip(keys, f[i:i + 12]))

    def symbol_of(self, c):
        sym = c['symbol'] or self.s.by_con.get(int(c['conId'] or 0), '')
        return sym.replace(' ', '.')

    def handle(self, f):
        mid = int(f[0])
        if mid == 71:                                            # startApi
            self.client_id = f[2]
            self.send(9, 1, self.s.next_id)
            self.send(15, 1, ACCOUNT)
        elif mid == 61:                                          # reqPositions
            self.send(62, 1)
        elif mid in (5, 16):                                     # reqOpenOrders / reqAllOpenOrders
            self.send(53, 1)
        elif mid == 99:                                          # reqCompletedOrders
            self.send(102)
        elif mid == 6:                                           # reqAccountUpdates
            self.send(6, 2, 'NetLiquidation', '100000', 'USD', ACCOUNT)
            self.send(54, 1, ACCOUNT)
        elif mid == 76:                                          # reqAccountUpdatesMulti
            self.send(74, 1, f[2])
        elif mid == 7:                                           # reqExecutions
            self.send(55, 1, f[2])
        elif mid == 62:                                          # reqAccountSummary
            req = f[2]
            self.send(63, 1, req, ACCOUNT, 'BuyingPower', '400000', 'USD')
            self.send(64, 1, req)
        elif mid == 85:                                          # reqNewsProviders
            self.send(85, 0)
        elif mid == 86:                                          # reqHistoricalNews
            self.send(87, f[1], 0)
        elif mid == 49:                                          # reqCurrentTime
            self.send(49, 1, int(time.time()))
        elif mid == 8:                                           # reqIds
            self.send(9, 1, self.s.next_id)
        elif mid == 9:                                           # reqContractDetails
            self.contract_details(f[2], self.contract(f, 3))
        elif mid == 1:                                           # reqMktData
            c = self.contract(f, 3)
            sym = self.symbol_of(c)
            self.subs[int(f[2])] = (sym, 'mkt')
            self.tick(int(f[2]), sym, first=True)
        elif mid == 2:                                           # cancelMktData
            self.subs.pop(int(f[2]), None)
        elif mid == 97:                                          # reqTickByTickData
            c = self.contract(f, 2)
            self.subs[int(f[1])] = (self.symbol_of(c), 'tbt')
        elif mid == 98:                                          # cancelTickByTickData
            self.subs.pop(int(f[1]), None)
        elif mid == 20:                                          # reqHistoricalData
            self.history(f)
        elif mid == 59:                                          # reqMarketDataType
            pass
        elif mid == 3:                                           # placeOrder: never in a live run
            oid = f[1]                                           # (the order ticket's IBKR preview is a what-if order)
            log('!!! ORDER (or what-if preview) from client', self.client_id, 'fields', f[:20], '-- refused')
            self.s.orders += 1
            self.send(4, 2, oid, 201, 'Order rejected - reason: fake IB Gateway takes no orders', '')
            self.send(3, oid, 'Cancelled', 0, 0, 0, 0, 0, 0, self.client_id or 0, '', 0)
        elif mid in (4, 58):                                     # cancelOrder / reqGlobalCancel
            pass
        else:
            log('unhandled message', mid, f[:6])

    def contract_details(self, req, c):
        sym = self.symbol_of(c)
        sec = c['secType'] or 'STK'
        if not sym:
            self.send(4, 2, req, 200, 'No security definition has been found for the request', '')
            return
        if sec in ('CONTFUT', 'FUT'):
            root = sym
            cid = con_id(root + '=F')
            self.s.by_con[cid] = root
            exch = c['exchange'] or 'CME'
            self.send(10, req, root, 'FUT', FUT_MONTH + '-16:00:00-US/Central', '0', '', exch, 'USD', root + 'Z6', root,
                      root, cid, '0.25', '50', 'LMT,MKT,STP', exch, '1', '0', root + ' Dec26', '', '202612', '', '', '',
                      'US/Central', '', '', '', '', 0, '', '', '', '', FUT_MONTH, '', '1', '1', '1')
        else:
            exch = 'OVERNIGHT' if c['exchange'] == 'OVERNIGHT' else (c['exchange'] or 'SMART')
            cid = con_id(sym)              # one contract id per stock, whatever the route (as at IBKR)
            self.s.by_con[cid] = sym
            self.send(10, req, sym.replace('.', ' '), 'STK', '', '0', '', exch, 'USD', sym, 'NMS', sym, cid, '0.01', '',
                      'LMT,MKT,STP', 'SMART,NASDAQ,OVERNIGHT', '1', '0', sym + ' FAKE INC', 'NASDAQ', '', 'Fake', 'Fake',
                      'Fake', 'US/Eastern', '', '', '', '', 0, '', '', '', '', '', 'COMMON', '1', '1', '100')
        self.send(52, 1, req)

    def history(self, f):
        req = f[1]
        c = self.contract(f, 2)
        sym = self.symbol_of(c)
        bar, dur, fmt = f[16], f[17], f[20]          # (end time, regular hours only, what to show: ignored)
        n_d = int(dur.split()[0]) if dur.split()[0].isdigit() else 1
        unit = dur.split()[1] if len(dur.split()) > 1 else 'D'
        days = n_d * {'S': 1 / 86400, 'D': 1, 'W': 7, 'M': 30, 'Y': 365}.get(unit, 1)
        step = {'1 min': 60, '5 mins': 300, '15 mins': 900, '1 hour': 3600, '1 day': 86400}.get(bar, 60)
        now = int(time.time()) // step * step
        start = now - int(days * 86400)
        rows = []
        for t in range(start, now, step):
            p = price_at(sym, t)
            if step >= 86400:
                d = time.strftime('%Y%m%d', time.gmtime(t))
            else:
                d = str(t) if fmt == '2' else time.strftime('%Y%m%d %H:%M:%S', time.gmtime(t)) + ' UTC'
            rows.append((d, p, p * 1.001, p * 0.999, price_at(sym, t + step - 1), 1000, p, 10))
        flat = [x for r in rows for x in r]
        self.send(17, req, time.strftime('%Y%m%d %H:%M:%S', time.gmtime(start)), time.strftime('%Y%m%d %H:%M:%S', time.gmtime(now)),
                  len(rows), *flat)

    def tick(self, req, sym, first=False):
        p = price_at(sym, time.time())
        if not p:
            return
        spread = max(0.01, round(p * 0.0004, 2))
        self.send(1, 6, req, 1, round(p - spread / 2, 2), 300, 0)          # bid
        self.send(1, 6, req, 2, round(p + spread / 2, 2), 200, 0)          # ask
        self.send(1, 6, req, 4, round(p, 2), 100, 0)                        # last
        self.send(2, 6, req, 8, 125000)                                     # volume
        if not first:
            self.send(46, 6, req, 48, f'{round(p, 2)};100;{int(time.time() * 1000)};125000;{round(p, 2)};true')

    def tbt(self, req, sym):
        p = price_at(sym, time.time())
        self.send(99, req, 1, int(time.time()), round(p, 2), 100, 0, 'NASDAQ', '')


class Server:
    def mkt_subs(self, client_id):
        """Market-data requests (reqMktData) still active for a client id: what IBKR counts as data lines."""
        return sum(1 for c in self.conns if c.client_id == str(client_id) for k in c.subs.values() if k[1] == 'mkt')

    def __init__(self, port, logdir):
        self.port, self.logdir = port, logdir
        self.conns = set()
        self.by_con = {}
        self.next_id = 1000
        self.orders = 0

    async def handle(self, r, w):
        if os.path.exists(os.path.join(self.logdir, 'fake_tws.drop')):
            w.close()
            return
        c = Conn(self, r, w)
        self.conns.add(c)
        await c.run()

    async def pump(self):
        k = 0
        while True:
            await asyncio.sleep(0.5)
            k += 1
            if os.path.exists(os.path.join(self.logdir, 'fake_tws.drop')):
                if self.conns:
                    log(f'DROP: closing {len(self.conns)} connections (simulated IB Gateway restart)')
                for c in list(self.conns):
                    c.alive = False
                    try:
                        c.w.close()
                    except Exception:
                        pass
                self.conns.clear()
                continue
            for c in list(self.conns):
                for req, (sym, kind) in list(c.subs.items()):
                    if (hash(sym) + k) % 2:                 # every symbol about once a second
                        continue
                    try:
                        if kind == 'tbt':
                            c.tbt(req, sym)
                        else:
                            c.tick(req, sym)
                    except Exception as e:
                        log('tick failed', e)
                try:
                    await c.w.drain()
                except Exception:
                    c.alive = False
            if k % 120 == 0:
                log(f'{len(self.conns)} connections, {sum(len(c.subs) for c in self.conns)} subscriptions, '
                    f'orders refused so far: {self.orders}')

    async def main(self):
        srv = await asyncio.start_server(self.handle, '127.0.0.1', self.port)
        log(f'listening on 127.0.0.1:{self.port} (server version {SERVER_VERSION}, account {ACCOUNT})')
        asyncio.ensure_future(self.pump())
        async with srv:
            await srv.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=4002)
    ap.add_argument('--logdir', default='.')
    a = ap.parse_args()
    asyncio.run(Server(a.port, a.logdir).main())
