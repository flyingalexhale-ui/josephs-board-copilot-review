# Fake brokers for live runs and tests

Nothing here talks to IBKR, Webull or ntfy.

* `fake_tws.py`: a fake IB Gateway that speaks the real TWS API wire protocol, so `ib_async` and every
  service run unchanged. It streams made-up prices, answers history, positions, open orders, executions
  and account requests, and refuses (and logs) every order. Touching `<logdir>/fake_tws.drop` drops all
  connections until the file is removed, like IB Gateway's nightly restart.
* `pkgs/webull/`: a fake Webull OpenAPI SDK (the modules the board imports). It streams prices about
  once a second, and placing an order is refused. `<logdir>/fake_webull.stall` keeps the stream
  connected with no prices (a silent stall), `fake_webull.drop` drops it (with paho's `on_disconnect`), and
  `fake_webull.no_overnight` acts like a plan without the overnight session (a subscription asking for it is
  refused). `JB_OVERNIGHT_RECHECK_S` makes the bridge, scanner and algo check the plan again sooner than 3 hours.
* `fakeprices.py`: the made-up prices. Both fakes give the same price for a symbol at the same moment.
* `clockshift/clockshift.c`: an `LD_PRELOAD` shim that moves the wall clock only, so a run can happen
  at a chosen New York time. libfaketime breaks Python's `time.sleep`.
* `live-run.sh <folder> ["2026-10-13 19:53:00"]`: copies the board to `<folder>` and starts the fake
  IB Gateway plus every service through `bin/run-service.sh`, with password `testpw`, fake Webull keys
  and `JB_NTFY_TOPIC` empty. `live-run.sh <folder> stop` stops them.
* `api-sweep.py http://127.0.0.1:8090 testpw`: calls every API route (GET, empty POSTs, malformed
  POSTs) and reports any 5xx answer, traceback or slow answer.
* `browser-check.py http://127.0.0.1:8090 testpw <shots folder>`: loads every page in headless Chromium
  at 1440x900 and iPhone 13 size. It reports JavaScript errors, failed requests, sideways scrolling and
  buttons that throw. Buttons that trade, sell, restart or delete are never clicked.
  `JB_CHROMIUM=/path/to/chrome` uses an installed Chromium.
