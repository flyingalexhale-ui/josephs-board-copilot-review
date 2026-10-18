"""Go-live checklist (final idea 56): checks before an algo trades real money.

Used two ways:
  * the Night desk / My trading page shows it (GET /api/plus/tools/golive?broker=ibkr)
  * ./jb live <broker> <strategy> runs it first:  python bin/golive.py <broker>
    It prints each check, then asks you to type LIVE. Anything else cancels (exit code 1).
Nothing here places orders or changes settings; it only reads the board's own files.
"""
import json
import os
import sys
import time

MIN_DAYS = 10          # paper trading days with the algo
MIN_WIN = 45           # % winning trades on paper
MAX_DAY_LOSS = 500.0   # worst paper day should be smaller than this


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def checklist(broker, root):
    broker = 'webull' if str(broker).lower().startswith('w') else 'ibkr'
    data = os.environ.get('JB_DATA_DIR') or os.path.join(root, 'data')
    items = []

    def add(name, ok, detail, fix=''):
        items.append({'name': name, 'ok': bool(ok), 'detail': detail, 'fix': '' if ok else fix})

    lb = _read(os.path.join(data, 'leaderboard.json'), {})
    days = {}
    for k, e in (lb.items() if isinstance(lb, dict) else []):
        if k.startswith('_') or not isinstance(e, dict) or e.get('algo') != broker:
            continue
        for d, v in (e.get('days') or {}).items():
            x = days.setdefault(d, {'pnl': 0.0, 'n': 0, 'wins': 0})
            x['pnl'] += v.get('pnl', 0)
            x['n'] += v.get('n', 0)
            x['wins'] += v.get('wins', 0)
    traded = {d: v for d, v in days.items() if v['n']}
    add('Paper trading days', len(traded) >= MIN_DAYS, f'{len(traded)} days with paper trades (want {MIN_DAYS}+)',
        'Let the paper algo run longer first. The paper leaderboard (My trading) counts the days.')
    n = sum(v['n'] for v in traded.values())
    w = sum(v['wins'] for v in traded.values())
    wr = round(w / n * 100) if n else 0
    add('Paper win rate', n and wr >= MIN_WIN, f'{wr}% of {n} paper trades won (want {MIN_WIN}%+)',
        'The strategy loses more often than it wins on paper. Try another strategy or settings first.')
    total = sum(v['pnl'] for v in traded.values())
    add('Paper profit', total > 0, f'${total:,.2f} total on paper', 'The algo lost money on paper overall.')
    worst = min((v['pnl'] for v in traded.values()), default=0.0)
    add('Worst paper day', worst > -MAX_DAY_LOSS, f'worst day ${worst:,.2f} (want smaller than -${MAX_DAY_LOSS:,.0f})',
        'One bad day on paper was bigger than your daily loss limit. Lower the position size first.')
    kl = _read(os.path.join(data, 'kill_log.json'), [])
    tested = [r for r in kl if isinstance(r, dict) and r.get('broker') == broker and str(r.get('result') or '').strip()
              and not str(r.get('result')).startswith('stopped')]
    add('SELL ALL tested', tested, f'{len(tested)} finished SELL ALL run(s) on {broker.upper()}' if tested else f'no SELL ALL run on {broker.upper()} yet',
        'Press SELL ALL once with a tiny test position so you know the panic button works.')
    s = _read(os.path.join(data, 'trading_settings.json'), {})
    on = s.get('loss_on', True)
    lim = float(s.get('loss_limit', 500) or 0)
    add('Daily loss limit on', on and lim > 0, f'on, ${lim:,.0f}' if on and lim > 0 else 'off',
        'Turn the daily loss limit on (My trading tab) before trading real money.')
    add('Pause algos at the loss limit', s.get('pause_algos', False), 'on' if s.get('pause_algos') else 'off',
        'Tick "also pause the algos" next to the daily loss limit on My trading.')
    hb = _read(os.path.join(root, broker + '-algo', 'logs', 'heartbeat.json'), {})
    age = time.time() - float(hb.get('ts') or 0)
    add('Algo heartbeat', hb and age < 300, 'the paper algo checked in ' + (f'{int(age // 60)} min ago' if hb else 'never'),
        'The algo is not running or is stuck. Check the Health tab.')
    return {'broker': broker, 'items': items, 'passed': sum(i['ok'] for i in items), 'total': len(items),
            'ready': all(i['ok'] for i in items)}


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    broker = sys.argv[1] if len(sys.argv) > 1 else 'ibkr'
    c = checklist(broker, root)
    print(f"Go-live checklist for {c['broker'].upper()}: {c['passed']} of {c['total']} passed")
    for i in c['items']:
        print(f"  [{'OK' if i['ok'] else '!!'}] {i['name']}: {i['detail']}")
        if i['fix']:
            print(f"        {i['fix']}")
    if not c['ready']:
        print('Not every check passed. You can still go live, but it is safer to fix the !! lines first.')
    try:
        ans = input('Type  LIVE  to keep going (anything else cancels): ').strip()
    except EOFError:
        ans = ''
    if ans != 'LIVE':
        print('Cancelled.')
        sys.exit(1)


if __name__ == '__main__':
    main()
