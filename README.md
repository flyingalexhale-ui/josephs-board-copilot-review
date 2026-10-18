# Alex Hale's Board (Joseph's Board)

Market dashboard plus Webull/IBKR paper and live trading algos that run 24/7 on a Mac in `~/josephs-board`, controlled with `./jb`.

This repo is a snapshot rebuilt from the original JOSEPHS_BOARD_v3 package plus every install package through jb-ready (2026-10-11), made for code review. It is not pulled by the Mac. Left out on purpose: `settings.env` (passwords and broker keys), `data/`, `logs/`, `voices/` (downloaded voice models), `backup-*/` and `.venv/`. One personal hostname in `roku/make-tv-app.sh` is replaced with a placeholder.

Layout: `gateway/` (web server on 8090 and most features), `bridges/` (IBKR 8765, Webull 8766 price feeds), `mover-scanner/` (8767), `webull-algo/`, `ibkr-algo/` (trading bots), `lib/`, `bin/`, `web/` (pages), `roku/` (TV app), `jb` (control script).
