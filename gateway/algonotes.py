"""Plain-English boxes on the algo tabs (jb-desk).

Two boxes per algo (Webull Algo, IBKR Algo): "Algorithm themes" and "Backtesting ideas", in your own
words, saved on the Mac. The tab copies them for Claude together with the algo's strategy file, the
strategy format and the last backtest, so Claude can (1) write them up as a strategy file and
(2) review the algo before it runs. A strategy file Claude writes can be pasted back on the tab:
the board checks it with the algo's own code, forces it to paper only (confirm_live: false) and saves
it as strategies/claude-<name>.yaml. It never switches what the running algo trades; it just shows up
in the Run backtest list.

  GET  /api/plus/algonotes/<webull|ibkr>          {themes, backtest, saved, current, strategies, yaml, format}
  POST /api/plus/algonotes/<webull|ibkr>/save     {themes, backtest}
  POST /api/plus/algonotes/<webull|ibkr>/paste    {yaml, backtest: bool}  -> {ok, file}
"""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
DIRS = {'webull': os.path.join(ROOT, 'webull-algo'), 'ibkr': os.path.join(ROOT, 'ibkr-algo')}
MAXLEN = 6000


def _path(algo):
    return os.path.join(DATA, f'algo_notes_{algo}.json')


def _read(algo):
    try:
        return json.load(open(_path(algo)))
    except (OSError, ValueError):
        return {}


def _text(path, limit=20000):
    try:
        return open(path, encoding='utf-8', errors='replace').read()[:limit]
    except OSError:
        return ''


def get(sub, query):
    algo = sub.strip('/')
    if algo not in DIRS:
        return 404, {'error': 'unknown algo'}
    import algoctl
    cur = algoctl._current(algo)
    d = _read(algo)
    return 200, {'algo': algo, 'themes': d.get('themes', ''), 'backtest': d.get('backtest', ''), 'saved': d.get('saved'),
                 'current': cur, 'strategies': algoctl._strategies(algo),
                 'yaml': _text(os.path.join(DIRS[algo], 'strategies', cur)) if cur else '',
                 'format': _text(os.path.join(DIRS[algo], 'STRATEGY_FORMAT.md'))}


def post(sub, data):
    parts = sub.strip('/').split('/')
    algo, act = parts[0], (parts[1] if len(parts) > 1 else '')
    if algo not in DIRS:
        return 404, {'error': 'unknown algo'}
    data = data or {}
    if act == 'save':
        d = {'themes': str(data.get('themes') or '')[:MAXLEN], 'backtest': str(data.get('backtest') or '')[:MAXLEN],
             'saved': int(time.time() * 1000)}
        os.makedirs(DATA, exist_ok=True)
        tmp = _path(algo) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        os.replace(tmp, _path(algo))
        return 200, {'ok': True, 'saved': d['saved']}
    if act == 'paste':
        txt = str(data.get('yaml') or '').replace('\r\n', '\n')
        txt = re.sub(r'^\s*```[a-zA-Z]*\s*\n|\n\s*```\s*$', '', txt.strip()) + '\n'      # Claude's code fences
        if len(txt) > 20000 or len(txt) < 20:
            return 400, {'error': 'Paste the whole strategy file Claude wrote (it starts with name: and symbol:).'}
        import yaml
        try:
            cfg = yaml.safe_load(txt)
        except yaml.YAMLError as e:
            return 400, {'error': 'That is not a strategy file: ' + str(e).splitlines()[0][:200]}
        if not isinstance(cfg, dict) or not cfg.get('symbol') or not (cfg.get('entry') or {}).get('when'):
            return 400, {'error': 'A strategy file needs at least symbol:, entry: when: and exit: when:.'}
        # paper only: whatever was pasted, it can never place a live order without you editing the file on the Mac
        if re.search(r'^confirm_live\s*:', txt, re.M):
            txt = re.sub(r'^confirm_live\s*:.*$', 'confirm_live: false   # pasted from Claude on the algo tab: paper only', txt, flags=re.M)
        else:
            txt += 'confirm_live: false   # pasted from Claude on the algo tab: paper only\n'
        slug = re.sub(r'[^a-z0-9]+', '_', str(cfg.get('name') or cfg.get('symbol')).lower()).strip('_')[:40] or 'strategy'
        fname = f'claude-{slug}.yaml'
        path = os.path.join(DIRS[algo], 'strategies', fname)
        tmp = path + '.check.yaml'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(txt)
        try:
            import stratlab
            err = stratlab._check(algo, tmp)
        except Exception as e:
            err = f'could not check it ({e})'
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if err:
            return 400, {'error': 'The algo could not run it: ' + err}
        with open(path, 'w', encoding='utf-8') as f:
            f.write(txt)
        out = {'ok': True, 'file': fname}
        if data.get('backtest'):
            import algoctl
            code, body = algoctl.post(algo, {'action': 'backtest', 'strategy': fname})
            out['backtest'] = 'started' if code == 200 else (json.loads(body).get('error') if isinstance(body, str) else body.get('error'))
        return 200, out
    return 404, {'error': 'unknown'}
