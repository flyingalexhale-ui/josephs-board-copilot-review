#!/usr/bin/env python3
"""Start the mover scanner: loads watchlist.yaml (ETFs) or stocklist.yaml (stocks, when the
Scanner tab is switched to Stocks -- see scan_mode.txt), starts the enabled feeds, serves the
live page (web/movers.html, through the gateway at http://<spare-mac>:8090/movers.html).

    python run_scanner.py --config watchlist.yaml          (normally ./install.sh runs this)

Webull needs WEBULL_APP_KEY / WEBULL_APP_SECRET (settings.env). IBKR needs IB Gateway running
and logged in on this Mac, same as the IBKR board bridge. Either feed alone is fine.
"""
import argparse
import asyncio
import errno
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from scanner.config import Config, ConfigError, read_mode, write_mode  # noqa: E402
from scanner.scanner import Scanner  # noqa: E402
from scanner.picks import PicksEngine  # noqa: E402
from scanner.intraday import MinutePicks  # noqa: E402


async def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('websockets').setLevel(logging.WARNING)   # no line per page view / health check
    logging.getLogger('ib_async').setLevel(logging.WARNING)
    logging.getLogger('webull.core.client').setLevel(logging.CRITICAL)   # its errors print the login keys
    log = logging.getLogger('run_scanner')
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default=os.path.join(HERE, 'watchlist.yaml'))
    ap.add_argument('--log-dir', default=os.path.join(HERE, 'logs'))
    ap.add_argument('--ws-host', default=os.environ.get('JB_BIND', '127.0.0.1'),
                    help='127.0.0.1 = reachable through the gateway only (recommended)')
    ap.add_argument('--ws-port', type=int, default=8767)
    ap.add_argument('--token', default=os.environ.get('MOVER_SCANNER_TOKEN', ''))
    ap.add_argument('--webull-app-key', default=os.environ.get('WEBULL_APP_KEY'))
    ap.add_argument('--webull-app-secret', default=os.environ.get('WEBULL_APP_SECRET'))
    ap.add_argument('--webull-region', default=(os.environ.get('WEBULL_REGION_ID') or 'us'))
    ap.add_argument('--ibkr-host', default='127.0.0.1')
    ap.add_argument('--ibkr-port', type=int, default=int(os.environ.get('IBKR_GATEWAY_PORT') or 0) or None)
    ap.add_argument('--ibkr-client-id', type=int, default=37)
    args = ap.parse_args()

    mode, notice = read_mode(args.config), ''
    if mode == 'stocks':
        try:
            config = Config.from_yaml(args.config, mode)
        except (ConfigError, OSError) as e:
            # a typo in stocklist.yaml must not leave the scanner down: scan ETFs and say why
            notice = f'Could not switch to stocks: {e}'.replace(HERE + os.sep, '')
            log.error('%s -- scanning ETFs instead', notice)
            mode = 'etfs'
            write_mode(args.config, mode)
    if mode == 'etfs':
        try:
            config = Config.from_yaml(args.config, mode)
        except (ConfigError, OSError) as e:
            raise SystemExit(f'watchlist problem: {e}')
    scanner = Scanner(config, log_dir=args.log_dir, config_path=args.config, notice=notice)
    log.info('scanning %s: %d symbols, %g-min windows, detectors: %s, preferred feed: %s', mode.upper(),
             len(config.symbols), config.window_ms / 60000,
             [k for k, v in config.detectors.items() if v is not False], config.prefer)

    tasks = [asyncio.create_task(scanner.run_server(args.ws_host, args.ws_port, args.token))]
    await asyncio.sleep(0.2)   # server (and scanner.loop) up before feeds deliver ticks
    history = {}               # feed name -> its daily-history fetch, for the research picks
    minute_seed = None         # IBKR's 1-minute history, so the minute picks start with history
    if config.use_webull:
        if not args.webull_app_key or not args.webull_app_secret:
            log.error('feeds.webull is on but WEBULL_APP_KEY/SECRET are not set -- Webull feed skipped')
        else:
            try:
                from scanner.feeds.webull_feed import WebullFeed
                wb = WebullFeed(config.symbols, scanner.on_tick, args.webull_app_key,
                                args.webull_app_secret, args.webull_region)
                history['webull'] = wb.daily_bars
                tasks.append(asyncio.create_task(wb.run()))
            except ImportError as e:
                log.error('Webull SDK not installed (%r) -- Webull feed skipped', e)
    if config.use_ibkr:
        try:
            from scanner.feeds.ibkr_feed import IBKRFeed
            ib = IBKRFeed(config.symbols, scanner.on_tick, host=args.ibkr_host,
                          port=args.ibkr_port, client_id=args.ibkr_client_id)
            history['ibkr'] = ib.daily_bars
            minute_seed = ib.minute_bars
            tasks.append(asyncio.create_task(ib.run()))
        except ImportError as e:
            log.error('ib_async not installed (%r) -- IBKR feed skipped', e)
    if len(tasks) == 1:
        raise SystemExit('No feed could be started (see above) -- nothing to scan.')
    # research picks from daily prices: the preferred feed first, the other as a fallback
    sources = sorted(history.items(), key=lambda kv: kv[0] != config.prefer)
    picks = PicksEngine(config.symbols, mode, sources, scanner.set_picks, live=scanner.rth.get, ext=scanner.ext.get)
    scanner.daily_close = picks.last_close      # 'vs close' right after a restart
    tasks.append(asyncio.create_task(picks.run()))
    # minute picks (1-min and 5-min bars from every tick, all sessions incl. overnight)
    mp = MinutePicks(config.symbols, scanner.set_minute_picks, vs_close=scanner.vs_close, seed=minute_seed)
    scanner.minute_hook = mp.add_tick
    tasks.append(asyncio.create_task(mp.run()))
    await asyncio.gather(*tasks)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        print('the scanner\'s port on this Mac is already in use -- is another copy already running? '
              '(./jb status shows what is running; ./jb restart fixes most cases)', flush=True)
        raise SystemExit(1)
