"""The "Ask Claude" page's data (jb-macro), running inside the gateway. Free: no API key, nothing is
sent anywhere by the board. It only gathers the board's live numbers as plain text; the page puts
your question plus the parts you tick on the clipboard and opens the Claude app with them.
  GET /api/plus/claude      {at, parts: [{id, title, text}]}
Each part is built separately, so one that fails (or is not installed) is just left out.
"""
import json
import time

import watch


def _now():
    return int(time.time() * 1000)


def _pc(v):
    return '–' if v is None else f'{v:+.2f}%'


def part_market(now):
    import world
    lines = []
    m = world.mood()
    if m:
        lines.append(f"Market mood since the 4 pm close: {m['score']:+d} ({m['label']}). Biggest pushes: " +
                     ', '.join(f"{p['name']} {_pc(p['pct'])}" for p in m['parts'][:5]) + '.')
    names = {s: n for s, n, _, _ in world.SYMBOLS}
    for s in ('ES=F', 'NQ=F', 'YM=F', 'RTY=F', '^VIX', '^TNX', 'DX-Y.NYB', 'CL=F', 'GC=F', 'BTC-USD', '^N225', '^STOXX50E'):
        q = world._quotes.get(s) or {}
        if q.get('last') is not None:
            lines.append(f"- {names.get(s, s)} ({s}): {q['last']:g}, {_pc(q.get('since_close'))} since the 4 pm close")
    return '\n'.join(lines)


def part_board(now):
    import mdata
    lines = []
    info = mdata.tracked()
    held = mdata.held()
    for s in sorted(info):
        if not mdata.mine(info[s]):
            continue
        p = mdata.price(s, now)
        if p:
            lines.append(f"- {s}{' (I hold it)' if s in held else ''}: ${p['last']:.2f}, {_pc(p.get('since_close'))} since the 4 pm close"
                         + (f" ({mdata.session_at(now)} session)" if p.get('src') == 'board' else ' (Yahoo, may be delayed)'))
    return 'My board symbols right now (New York time ' + watch.et(now).strftime('%a %I:%M %p') + '):\n' + '\n'.join(lines) if lines else ''


def part_twins(now):
    import mdata
    lines = []
    for r in mdata.twins(now):
        if r['lev'] != 3 or not r['base']['p']:
            continue
        legs = []
        for k in ('bull', 'bear'):
            x = r.get(k)
            if x and x.get('p'):
                legs.append(f"{x['sym']} {_pc(x['p'].get('since_close'))}")
        lines.append(f"- {r['group']} ({r['base']['sym']} {_pc(r['base']['p'].get('since_close'))}): " + ', '.join(legs))
    return '3x leveraged ETF pairs (move since the 4 pm close):\n' + '\n'.join(lines) if lines else ''


def part_positions(now):
    import earn
    pos = earn.held_positions()
    lines = [f"- {p['sym']}: {p['shares']:g} shares" + (f" at ${p['last']:.2f} (${p['value']:,.0f})" if p.get('last') and p.get('value') else '')
             for p in pos]
    out = 'My positions:\n' + '\n'.join(lines) if lines else 'I hold no positions right now.'
    try:
        import trading
        s = json.loads(trading.snapshot()).get('summary') or {}
        if s.get('today'):
            out += f"\nMy realized profit/loss today (day starts 8 pm ET): ${s.get('today_realized') or 0:,.2f}"
    except Exception:
        pass
    return out


def part_pick(now):
    import pick
    _, d = pick.get('', {})
    r = d.get('current') or d.get('preview')
    if not r:
        return ''
    p = r.get('pick')
    rec = d.get('record') or {}
    head = (f"The board's rule-based ETF pick of the hour: {p['sym']} ({p['group']} 3x {p['side']}), score {p['score']}/100. Reasons: " + '; '.join(p['why']) + '.'
            if p else 'The board\'s rule-based ETF pick of the hour: no clear pick (stay flat).')
    if rec.get('graded'):
        head += f" Track record: {rec['win_pct']}% of {rec['graded']} picks were up an hour later, average {rec['avg']:+.2f}%."
    return head


def part_earnings(now):
    import earn
    rows = earn.snapshot()['earnings']
    lines = [f"- {r['sym']}: {r['date']} {r['when']}" + (f", options expect a move of about ±{r['em']['pct']:.1f}%" if r.get('em') else '')
             for r in rows if r.get('mine')]
    return 'Earnings coming up for my stocks:\n' + '\n'.join(lines) if lines else ''


def part_alerts(now):
    import alerts2
    d = alerts2.snapshot()
    lines = []
    for h in (d.get('halts') or [])[:20]:
        if h.get('mine') and h.get('halted'):
            lines.append(f"- {h['sym']} was halted ({h.get('why') or ''}) at {watch.et(h['halted']).strftime('%a %I:%M %p')}"
                         + (' (I hold it)' if h.get('held') else ''))
    odds = d.get('odds') if isinstance(d.get('odds'), dict) else {}
    for o in odds.values():
        top = ', '.join(f"{r['label']} {r['p']}%" for r in (o.get('rows') or [])[:4])
        if top:
            lines.append(f"- Kalshi odds, {o.get('title')}: {top}")
    return 'Alerts and odds:\n' + '\n'.join(lines) if lines else ''


def part_macro(now):
    import macro
    t = macro.summary_text()
    return 'Long-term macro backdrop (FRED data):\n' + t if t else ''


def part_bigmoney(now):
    import bigmoney
    d = bigmoney.snapshot()
    lines = []
    for r in d['rows']:
        if r.get('inst_pct') is not None:
            lines.append(f"- {r['sym']}: {r['inst_pct']:.0f}% held by institutions, {r['insider_pct'] or 0:.1f}% by insiders")
        for t in (r.get('insiders') or [])[:2]:
            lines.append(f"  insider {t['kind']}: {t['name']} ({t['role']}) ${t.get('value') or 0:,.0f}, {time.strftime('%b %d', time.localtime(t['t'] / 1000))}")
    for b in d['blocks'][:5]:
        lines.append(f"- Block trade: {b['sym']} {b['size']:,} shares at ${b['price']:.2f}")
    for t in d['politicians_mine'][:5]:
        lines.append(f"- Congress: {t['name']} ({t['chamber']}) {t['kind']} {t['sym']} {t['amount']} on {t['date']}")
    return 'Big money in my stocks:\n' + '\n'.join(lines) if lines else ''


def part_web(now):
    import webwatch
    d = webwatch.snapshot()
    lines = [f"- UNEXPLAINED: {u['sym']} {u['move']:+.1f}% with no news found" for u in d['unexplained']]
    lines += [f"- {i['sym']} [{i['kind']}] {i['title']} ({i['src']})" for i in d['items'][:12] if i['kind'] != 'unexplained']
    return 'Web watch (press releases, Reddit, SEC, attention) for my stocks, last 2 days:\n' + '\n'.join(lines) if lines else ''


def part_news(now):
    import news
    d = news.snapshot()
    cut = time.time() - 24 * 3600
    items = [i for i in d['items'] if i['ts'] >= cut][:15]
    lines = [f"- [{', '.join(i['syms'][:3])}] {i['title']} ({i.get('source') or ''})" for i in items]
    return 'Headlines for my symbols, last 24 hours:\n' + '\n'.join(lines) if lines else ''


PARTS = [('market', 'Market now (futures, rates, mood)', part_market, True),
         ('board', 'My board symbols', part_board, True),
         ('twins', '3x ETF pairs', part_twins, True),
         ('positions', 'My positions and today\'s trading', part_positions, True),
         ('pick', 'ETF pick of the hour', part_pick, False),
         ('earnings', 'Upcoming earnings', part_earnings, False),
         ('alerts', 'Halts and prediction odds', part_alerts, False),
         ('macro', 'Macro backdrop (MACRO tab)', part_macro, False),
         ('sentiment', 'Market sentiment report (MACRO tab)', lambda now: __import__('sentiment').summary_text(), False),
         ('bigmoney', 'Big money and politicians', part_bigmoney, False),
         ('web', 'Web watch', part_web, False),
         ('news', 'Headlines (24 h)', part_news, False)]


def build():
    now = _now()
    out = []
    for pid, title, fn, on in PARTS:
        try:
            t = fn(now)
        except Exception as e:
            print(f'claude part {pid}: {e!r}', flush=True)
            t = ''
        if t:
            out.append({'id': pid, 'title': title, 'text': t, 'on': on})
    return {'at': now, 'parts': out}


def start():
    pass


def get(sub, query):
    return 200, build()


def post(sub, data):
    return 404, {'error': 'unknown'}
