"""Everyday tools from the final-ideas list (jb-tools), running inside the gateway.

  any-symbol chart: Yahoo prices for a symbol your boards don't stream, drawn in your own chart  (idea 32)
  iPhone widget (Scriptable) and "Hey Siri, how's the board?": a read-only summary at
     /api/widget?key=...  (the key is made on the Mac; the Night desk shows the ready-made
     Scriptable script and Shortcut steps with it filled in)                                     (ideas 29, 30)
  alert manager: every alert and alert switch on one page, with on / off and a test push          (idea 50)
  journal scorecard: profit factor, average win vs loss, expectancy, losing streak, by weekday
     and by ETF, with the automatic trade notes next to each trade                                (ideas 51, 52)
  weekly trading report PDF, Friday 8:30 pm New York, with a push linking to it                    (idea 53)
  go-live checklist (also run by ./jb live before real money)                                      (idea 56)
  symbol notes, shown on the tile, chart and ticket                                                 (idea 71)
  "what changed since I last looked" for the board and phone banners                              (idea 72)
  (the command bar, idea 28, and keyboard keys, idea 31, are in web/tools-ui.js)

  GET  /api/plus/tools/bars?sym=           intraday + daily prices from Yahoo
  GET  /api/plus/tools/notes               {notes: {sym: {text, at}}}
  POST /api/plus/tools/notes               {sym, text}   (empty text removes it)
  GET  /api/plus/tools/alerts              the alert manager list
  POST /api/plus/tools/alerts              {kind, module?, key?, id?, on}  or  {test: true}
  GET  /api/plus/tools/scorecard
  GET  /api/plus/tools/golive?broker=
  GET  /api/plus/tools/changes?since=ms
  GET  /api/plus/tools/widget/setup        the Scriptable script and Siri steps (logged in)
  POST /api/plus/tools/report              make this week's PDF now
Files: data/notes.json, data/tools.json (widget key), web/reports/week-*.pdf.
"""
import hmac
import importlib
import json
import math
import os
import secrets
import threading
import time
import urllib.parse
from datetime import date, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
NOTES = os.path.join(DATA, 'notes.json')
STATE = os.path.join(DATA, 'tools.json')
REPORTS = os.path.join(ROOT, 'web', 'reports')
_lock = threading.Lock()
_st = {'started': False, 'bars': {}}


def _now():
    return int(time.time() * 1000)


def _clean(s):
    return ''.join(ch for ch in str(s or '').upper() if ch.isalnum() or ch in '.=^-')[:12]


def _state():
    v = watch._read(STATE, {})
    return v if isinstance(v, dict) else {}


def _put(**kw):
    with _lock:
        s = _state()
        s.update(kw)
        watch._write(STATE, s)


# ------------------------------------------------------------------ any-symbol chart (32)
def bars(sym):
    sym = _clean(sym)
    hit = _st['bars'].get(sym)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    import world
    q = urllib.parse.quote(sym.replace('.', '-') if '=' not in sym and '^' not in sym else sym)
    body = world._get(f'https://query1.finance.yahoo.com/v8/finance/chart/{q}?range=5d&interval=5m&includePrePost=true', timeout=15)
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    meta = res.get('meta') or {}
    pts = [{'t': int(t) * 1000 + 300000, 'p': round(c, 6)} for t, c in zip(res.get('timestamp') or [], (((res.get('indicators') or {}).get('quote') or [{}])[0].get('close') or [])) if c]
    daily = []
    try:
        time.sleep(1)
        b2 = world._get(f'https://query1.finance.yahoo.com/v8/finance/chart/{q}?range=6mo&interval=1d', timeout=15)
        r2 = ((b2.get('chart') or {}).get('result') or [None])[0] or {}
        first = pts[0]['t'] if pts else _now()
        for t, c in zip(r2.get('timestamp') or [], (((r2.get('indicators') or {}).get('quote') or [{}])[0].get('close') or [])):
            if c and watch.at_et(watch.et(int(t) * 1000).date(), 16, 0) < first:
                daily.append({'t': watch.at_et(watch.et(int(t) * 1000).date(), 16, 0), 'p': round(c, 6)})
    except Exception:
        pass
    out = {'sym': sym, 'name': meta.get('shortName') or meta.get('longName') or '', 'points': daily + pts, 'src': 'Yahoo (can lag 15 min for futures and indexes)'}
    if not pts:
        out['error'] = f'Yahoo has no prices for {sym}'
    _st['bars'][sym] = (time.time(), out)
    return out


# ------------------------------------------------------------------ notes (71)
def notes():
    v = watch._read(NOTES, {})
    return v if isinstance(v, dict) else {}


def save_note(data):
    sym, text = _clean(data.get('sym')), str(data.get('text') or '').strip()[:1000]
    if not sym:
        return 400, {'error': 'Which symbol?'}
    with _lock:
        n = notes()
        if text:
            n[sym] = {'text': text, 'at': _now()}
        else:
            n.pop(sym, None)
        watch._write(NOTES, n)
    return 200, {'notes': n}


# ------------------------------------------------------------------ widget + Siri (29, 30)
def widget_key():
    k = os.environ.get('JB_WIDGET_KEY', '').strip() or _state().get('widget_key')
    if not k:
        k = secrets.token_urlsafe(18)
        _put(widget_key=k)
    return k


def _fut(sym):
    try:
        import world
        q = world._quotes.get(sym) or {}
        return q.get('since_close')
    except Exception:
        return None


def widget(query):
    """Read-only summary for the iPhone widget and Siri (needs ?key=, no login)."""
    k = (query or {}).get('key')
    k = k[0] if isinstance(k, list) and k else (k or '')
    if not hmac.compare_digest(str(k), widget_key()):
        time.sleep(1)
        return 403, {'error': 'wrong key'}
    out = {'at': _now(), 'session': ''}
    try:
        import killswitch as ks
        out['session'] = ks.SESSION_NAME[ks.session_now()]
    except Exception:
        pass
    out['futures'] = {n: (None if v is None else round(v, 2)) for n, v in (('Nasdaq', _fut('NQ=F')), ('S&P', _fut('ES=F')), ('Dow', _fut('YM=F')))}
    out['basis'] = 'close'
    try:                                         # jb-lockfix2: in regular hours, futures since the 9:30 open
        import killswitch as ks
        if ks.session_now() == 'regular':
            import meters
            m = {p['name']: p['pct'] for p in ((meters.snapshot().get('futures') or {}).get('open') or {}).get('parts', [])}
            if all(m.get(n) is not None for n in ('Nasdaq', 'S&P', 'Dow')):
                out['futures'] = {n: round(m[n], 2) for n in ('Nasdaq', 'S&P', 'Dow')}
                out['basis'] = 'open'
    except Exception:
        pass
    try:
        import trading
        with trading._lock:
            j = trading._journal()
        trips, open_ = trading.round_trips(list(j['fills'].values()))
        s = trading.summary(trips, open_, j['notes'])
        out['pnl_today'] = round(s['today_realized'] + s['unrealized_today'], 2)
        out['positions'] = [{'sym': o['sym'], 'qty': o['qty'], 'pnl': o.get('unrealized')} for o in open_ if o.get('qty')][:6]
    except Exception:
        out['pnl_today'], out['positions'] = None, []
    try:
        import syscheck
        snap = syscheck.snapshot()
        out['health'] = snap['overall']
        out['problems'] = snap['counts']['bad'] + snap['counts']['warn']
        if (query or {}).get('checks'):          # jb-ease: the Mac menu bar's board health and data list
            out['checks'] = [{'group': g['name'], 'name': i['name'], 'state': i['state'], 'detail': (i.get('detail') or '')[:90]}
                             for g in snap.get('groups', []) for i in g.get('items', [])
                             if g['name'] in ('Board parts', 'Brokers', 'Data feeds') or i['state'] in ('bad', 'warn')]
    except Exception:
        out['health'] = 'unknown'
    try:                                         # jb-widgetchart: IBKR and Webull price feeds up / down
        rank = {'bad': 4, 'warn': 3, 'off': 2, 'info': 1, 'ok': 0}
        items = [i for g in snap.get('groups', []) if g['name'] == 'Brokers' for i in g.get('items', [])]
        feeds = {}
        for title, pre in (('IBKR', ('IBKR ', 'IB Gateway')), ('Webull', ('Webull ',))):
            sts = [i['state'] for i in items if i['name'].startswith(pre) and 'auto-login' not in i['name']]
            worst = max(sts, key=lambda x: rank.get(x, 0)) if sts else None
            feeds[title] = None if worst is None else 'off' if worst == 'off' else 'down' if worst in ('bad', 'warn') else 'up'
        out['feeds'] = feeds
    except Exception:
        out['feeds'] = {}
    try:                                         # jb-widgetchart: futures line chart since the 4 pm close / 9:30 open
        import meters
        st = meters.anchors(_now()).get('open' if out.get('basis') == 'open' else 'close')
        ser = {}
        for sym in ('ES=F', 'NQ=F', 'YM=F'):
            with meters._lock:
                bars = list(meters._bars.get(sym) or [])
            p0 = meters._bar_price(bars, st) if st else None
            pts = [round((c / p0 - 1) * 100, 3) for t, o, c in bars if st and t >= st] if p0 else []
            if len(pts) < 2 and st:              # jb-widgettime: live fallback (the board's own prices)
                with watch._lock:
                    h = [tuple(x) for x in (watch._px.get(sym) or [])]
                p0 = watch.price_at(h, st) or (h[0][1] if h and h[0][0] - st <= 30 * 60000 else None)
                pts = [round((p / p0 - 1) * 100, 3) for t, p in h if t >= st] if p0 else []
            if len(pts) > 48:
                k = len(pts) / 48.0
                pts = [pts[int(i * k)] for i in range(47)] + [pts[-1]]
            if pts:
                ser[sym] = [0.0] + pts
        out['chart'] = ser
    except Exception:
        out['chart'] = {}
    try:
        lst = watch._read(watch.LOG, [])
        cut = _now() - 12 * 3600000
        out['alerts_12h'] = sum(1 for a in lst if isinstance(a, dict) and (a.get('at') or a.get('t') or 0) >= cut)
    except Exception:
        out['alerts_12h'] = None
    f = out['futures']
    bits = [f"It's {out['session']}." if out['session'] else '']
    if f.get('Nasdaq') is not None:
        bits.append(f"Nasdaq futures {'up' if f['Nasdaq'] >= 0 else 'down'} {abs(f['Nasdaq']):.1f} percent, S&P {'up' if (f.get('S&P') or 0) >= 0 else 'down'} {abs(f.get('S&P') or 0):.1f}, "
                    f"Dow {'up' if (f.get('Dow') or 0) >= 0 else 'down'} {abs(f.get('Dow') or 0):.1f}.")
    if out.get('pnl_today') is not None:
        v = out['pnl_today']
        bits.append(f"You're {'up' if v >= 0 else 'down'} {abs(v):,.0f} dollars today" + (f" with {len(out['positions'])} positions." if out['positions'] else ', no open positions.'))
    if out.get('alerts_12h'):
        bits.append(f"{out['alerts_12h']} alerts in the last 12 hours.")
    bits.append('The board is healthy.' if out.get('health') == 'ok' else f"System check shows {out.get('problems', 'some')} things to look at.")
    out['say'] = ' '.join(b for b in bits if b)
    out['pnl'] = out['pnl_today']        # widget shows the Dow instead of the Today line; Siri still says it (jb-widget)
    out['pnl_today'] = None
    return 200, out


SCRIPTABLE = r'''// Alex Hale's Board widget for the free Scriptable app (iPhone home screen).
// Made by the board: the link and key below are yours. Keep this private.
const URL = "__URL__/api/widget?key=__KEY__";
const PHONE = "__URL__/phone.html";
let d;
try { d = await new Request(URL).loadJSON(); } catch (e) { d = null; }
const fam = config.widgetFamily || "";
const tc = x => String(x).split(" ").map(w => w.split("-").map(p => p.charAt(0).toUpperCase() + p.slice(1)).join("-")).join(" ");  // jb-widgetchart caps
if (fam.indexOf("accessory") === 0) {        // Lock Screen (jb-lockfix): board status, session, S&P, Nasdaq and Dow futures
  const a = new ListWidget();
  a.url = PHONE;
  a.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
  const f = (d && d.futures) || {};
  const s = v => v === null || v === undefined ? "--" : (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
  const ok = d && !d.error;
  const status = ok ? "Up" : "Down";
  const fd = (ok && d.feeds) || {};
  const at = new Date(ok ? d.at : Date.now()).toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit"});  // jb-widgettime: lock time
  const feedTxt = ok ? [fd.IBKR ? "IBKR " + tc(fd.IBKR) : "", at, fd.Webull ? "Webull " + tc(fd.Webull) : ""].filter(x => x).join(" · ") : "";
  const sess = ok && d.session ? d.session.charAt(0).toUpperCase() + d.session.slice(1) : "";
  const txt = (st, t, size, bold) => { const x = st.addText(t); x.font = bold ? Font.semiboldSystemFont(size) : Font.systemFont(size);
    x.lineLimit = 1; x.minimumScaleFactor = 0.6; return x; };
  if (fam === "accessoryInline") txt(a, ok ? "Board: Up  ·  NQ=F " + s(f.Nasdaq) : "⚠️ Board Down  ·  " + at, 12);  // jb-widgettime: lock down
  else if (fam === "accessoryCircular") {
    a.addAccessoryWidgetBackground = true;
    txt(a, "NQ=F", 10, true).centerAlignText(); txt(a, s(f.Nasdaq), 11).centerAlignText();
  } else if (!ok) {                          // jb-widgettime: lock down box
    const c = () => { const r = a.addStack(); r.addSpacer(); const x = r.addStack(); r.addSpacer(); return x; };
    txt(c(), "⚠️ Board Down", 16, true);
    a.addSpacer(2);
    txt(c(), "Can't Reach The Board", 11);
    txt(c(), "Tried At " + at + " · Is Tailscale On?", 10);
  } else {
    const top = a.addStack(); top.centerAlignContent();
    txt(top, "Board: " + status, 13, true);
    top.addSpacer();
    const basisTxt = ok ? (d.basis === "open" ? "Vs 9:30" : "Vs 4 PM") : "";
    const shortSess = tc(sess.replace("Markets closed", "Closed").replace("Regular hours", "Day"));
    if (ok) txt(top, (shortSess ? shortSess + " · " : "") + basisTxt, 10);
    if (feedTxt) { a.addSpacer(1); const fr = a.addStack(); fr.addSpacer(); txt(fr, feedTxt, 11); fr.addSpacer(); }  // jb-lockcenter
    a.addSpacer(2);
    const row = a.addStack();
    const cols = [["ES=F", f["S&P"]], ["NQ=F", f.Nasdaq], ["YM=F", f.Dow]];
    cols.forEach((c, i) => {
      if (i) row.addSpacer(); const one = row.addStack(); one.layoutVertically();
      txt(one, c[0], 9); txt(one, s(c[1]), 12, true);
    });
  }
  Script.setWidget(a);
} else {
const w = new ListWidget();
w.backgroundColor = new Color("#04101f");
w.url = PHONE;
w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
function line(t, c, size) { const x = w.addText(t); x.textColor = new Color(c || "#f2f2f2"); x.font = Font.mediumMonospacedSystemFont(size || 12); return x; }
if (!d || d.error) {
  w.backgroundColor = new Color("#5a0f14");  // jb-widgettime: home down
  const bd = line("⚠️ Board Down", "#ffffff", 18); bd.font = Font.boldSystemFont(18); bd.minimumScaleFactor = 0.6;
  w.addSpacer(4);
  line("Can't Reach The Board", "#ffd2d2", 12).minimumScaleFactor = 0.6;
  line("Is Tailscale On?", "#ffd2d2", 11);
  w.addSpacer(4);
  line("Tried At " + new Date().toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit"}), "#ffffff", 11);
} else {
  // jb-widgetchart: Board / feeds lines, futures rows and a mini chart (colours match the rows)
  const COLS = {"ES=F": "#5aa9ff", "NQ=F": "#c08cff", "YM=F": "#ffb648"};
  const top = w.addStack(); top.centerAlignContent();
  const t1 = top.addText("Board: Up"); t1.font = Font.semiboldSystemFont(12); t1.textColor = new Color("#f2f2f2");
  top.addSpacer();
  const ss = (d.basis === "open" ? "Vs 9:30" : tc((d.session || "").replace("markets closed", "closed")));
  const t2 = top.addText(ss); t2.font = Font.systemFont(9); t2.textColor = new Color("#9fb3c8"); t2.lineLimit = 1; t2.minimumScaleFactor = 0.6;
  const fd = d.feeds || {};
  const ft = ["IBKR", "Webull"].filter(k => fd[k]).map(k => k + " " + tc(fd[k])).join(" · ");
  if (ft) line(ft, Object.values(fd).some(v => v === "down") ? "#ff8a80" : "#9fb3c8", 10).minimumScaleFactor = 0.6;
  w.addSpacer(3);
  const map = [["ES=F", "S&P"], ["NQ=F", "Nasdaq"], ["YM=F", "Dow"]];
  for (const [sym, k] of map) {
    const v = d.futures[k];
    const r = w.addStack(); r.centerAlignContent();
    const a1 = r.addText(sym); a1.font = Font.mediumMonospacedSystemFont(12); a1.textColor = new Color(COLS[sym]);
    r.addSpacer();
    const a2 = r.addText(v === null || v === undefined ? "--" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%");
    a2.font = Font.mediumMonospacedSystemFont(12); a2.textColor = new Color(v === null || v === undefined ? "#6f8aa3" : v >= 0 ? "#5dff8f" : "#ff8a80");
  }
  const ch = d.chart || {};
  const all = Object.values(ch).flat();
  if (all.length > 2) {
    const W = 300, H = 96, pad = 4;
    const lo = Math.min(0, ...all), hi = Math.max(0, ...all), span = (hi - lo) || 0.1;
    const dc = new DrawContext(); dc.size = new Size(W, H); dc.opaque = false; dc.respectScreenScale = true;
    const y = v => pad + (H - 2 * pad) * (1 - (v - lo) / span);
    const z = new Path(); z.move(new Point(0, y(0))); z.addLine(new Point(W, y(0)));
    dc.addPath(z); dc.setStrokeColor(new Color("#6f8aa3", 0.6)); dc.setLineWidth(1); dc.strokePath();
    for (const sym of ["YM=F", "ES=F", "NQ=F"]) {
      const pts = ch[sym]; if (!pts || pts.length < 2) continue;
      const p = new Path();
      pts.forEach((v, i) => { const pt = new Point(W * i / (pts.length - 1), y(v)); if (i) p.addLine(pt); else p.move(pt); });
      dc.addPath(p); dc.setStrokeColor(new Color(COLS[sym])); dc.setLineWidth(3); dc.strokePath();
    }
    w.addSpacer(4);
    const im = w.addImage(dc.getImage()); im.imageSize = new Size(140, 45);
  } else { w.addSpacer(4); line("Chart Waiting For Prices", "#6f8aa3", 9).minimumScaleFactor = 0.6; }  // jb-widgettime: chart note
  const t = new Date(d.at); line("As Of " + t.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}), "#6f8aa3", 9);
}
if (config.runsInWidget) Script.setWidget(w); else w.presentSmall();
}
Script.complete();
'''


def widget_setup():
    try:
        import alerts2
        url = alerts2._find_phone_url()
    except Exception:
        url = ''
    url = url or 'http://YOUR-TAILSCALE-ADDRESS:8090'
    key = widget_key()
    return {'url': f'{url}/api/widget?key={key}', 'script': SCRIPTABLE.replace('__URL__', url).replace('__KEY__', key),
            'siri': [
                'Open the Shortcuts app on your iPhone and tap + (new shortcut).',
                'Add the action "Get Contents of URL" and paste this link: ' + f'{url}/api/widget?key={key}',
                'Add the action "Get Dictionary Value": Get Value for key "say" in Contents of URL.',
                'Add the action "Speak Text" (Dictionary Value).',
                'Name the shortcut "How\'s the board". Now say "Hey Siri, how\'s the board". It also runs from the Apple Watch.'],
            'lock': 'Lock Screen too: copy the new script into Scriptable (replace the old one), then touch and hold the Lock Screen, tap Customize, tap the box under the clock, add Scriptable (the small, round or one-line size) and pick this script. It shows S&P, Nasdaq and Dow futures and a green, amber or red dot for the board\'s health.',
            'note': 'Your iPhone needs Tailscale on (as for the Phone page). The key only lets someone read this summary, never trade; '
                    'to change it, delete widget_key from data/tools.json (or set JB_WIDGET_KEY in settings.env) and restart the gateway.'}


# ------------------------------------------------------------------ alert manager (50)
SWITCH_MODULES = (('signals', 'Signals tab'), ('alerts2', 'Night desk alerts'), ('night', 'Overnight watch'), ('mdata', 'Market data alerts'),
                  ('webwatch', 'Web watch'), ('earn', 'Earnings'), ('edge', '3x ETF edge'), ('feeds', 'News and crowd feeds'),
                  ('guardian', 'Reliability'), ('orders', 'Order automation'))
LABELS = {'big_move': 'Big move', 'fast_move': 'Fast move', 'volume': 'Heavy volume', 'yday': "Yesterday's high / low", 'gap4': '4 am gap list',
          'buzz': 'StockTwits buzz', 'sec': 'SEC filings', 'earnings': 'Earnings move', 'summary': '7:30 am summary',
          'halts': 'Trading halts', 'halts_all': 'Halts of any stock', 'bedtime': 'Before-bed card', 'combos': 'Combo alerts',
          'data_830': 'Data reactions', 'tap': 'Tap opens the chart', 'on_break': 'Overnight high / low break', 'pm_break': 'Pre-market high / low break',
          'venue': 'Overnight venue down', 'ratings': 'Analyst ratings', 'options': 'Unusual options', 'short': 'Short interest',
          'wires': 'Press releases', 'reddit': 'Reddit spike', 'attention': 'Wikipedia / Google Trends', 'unexplained': 'Unexplained moves',
          'call_push': 'Earnings call clock', 'plan_push': '9 am game plan', 'vix_push': 'VIX curve warning', 'yen_push': 'Yen shock',
          'auction_push': 'Weak Treasury auction', 'news_push': 'IBKR newswire', 'reddit_push': 'Reddit buzz', 'policy_push': 'Policy posts',
          'poly_push': 'Polymarket moves', 'check_push': 'Prices disagree', 'heartbeat_push': 'Algo stopped', 'early_loss': 'Early loss warnings',
          'confirm_sellall': 'Confirm SELL ALL', 'backup': 'Nightly backup', 'icloud': 'Backup to iCloud', 'sunday_test': 'Sunday self-test',
          'saturday_refresh': 'Saturday refresh', 'stale_session': 'Cancel at session change', 'event_guard': 'Pause algos for big news',
          'ib_reminder': 'Sunday IB sign-in reminder', 'fill_push': 'Fill pushes'}


def _label(k):
    return LABELS.get(k) or k.replace('_on', '').replace('_push', ' push').replace('_', ' ').strip().capitalize()


def _module_switches(name):
    try:
        m = importlib.import_module(name)
    except Exception:
        return None, None, {}
    if name == 'orders':
        return m, m.FILE, {k: v for k, v in m._load()['cfg'].items() if isinstance(v, bool)}
    path = getattr(m, 'SETTINGS', None)
    fn = getattr(m, 'settings', None)
    if not path or not callable(fn):
        return m, None, {}
    try:
        cur = fn()
    except Exception:
        return m, None, {}
    out = {}
    for k, v in (cur or {}).items():
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, dict) and isinstance(v.get('on'), bool):     # signals: {'big_move': {'on': True, 'pct': 3}}
            out[k] = v['on']
    return m, path, out


def alerts_list():
    out = {'price': [], 'levels': [], 'combos': [], 'switches': []}
    try:
        for a in watch.alerts():
            out['price'].append({'id': a.get('id'), 'text': watch.describe(a), 'on': a.get('on', True), 'fired': a.get('fired') or a.get('last')})
    except Exception:
        pass
    try:
        import levels
        for l in levels.all_levels():
            out['levels'].append({'id': l['id'], 'text': f"{l['sym']} {l['price']:g}" + (f" ({l['label']})" if l.get('label') else ''), 'on': bool(l.get('alert')), 'fired': l.get('fired')})
    except Exception:
        pass
    try:
        import alerts2
        for c in alerts2.combos():
            out['combos'].append({'id': c.get('id'), 'text': alerts2.describe_cond(c) if 'conds' not in c else ' and '.join(alerts2.describe_cond(x) for x in c['conds']), 'on': c.get('on', True), 'fired': c.get('fired')})
    except Exception:
        pass
    for name, title in SWITCH_MODULES:
        _, path, sw = _module_switches(name)
        if sw:
            out['switches'].append({'module': name, 'title': title, 'items': [{'key': k, 'label': _label(k), 'on': v} for k, v in sw.items()]})
    try:
        lst = watch._read(watch.LOG, [])
        out['recent'] = [x for x in lst if isinstance(x, dict)][:20] if isinstance(lst, list) else []
    except Exception:
        out['recent'] = []
    return out


def alerts_post(data):
    if data.get('test'):
        err = watch.push('Test push from the board', 'If you can read this, phone alerts work. Urgent alerts use top priority.', 'bell')
        return (200, {'text': 'Test push sent.'}) if not err else (400, {'error': f'Push failed: {err}'})
    kind, on = data.get('kind'), bool(data.get('on'))
    if kind == 'switch':
        name, key = str(data.get('module') or ''), str(data.get('key') or '')
        m, path, sw = _module_switches(name)
        if key not in sw:
            return 400, {'error': 'unknown switch'}
        if name == 'orders':
            return m.settings({key: on})[0], alerts_list()
        cur = watch._read(path, {})
        cur = cur if isinstance(cur, dict) else {}
        if isinstance((m.settings() or {}).get(key), dict):
            cur[key] = dict(cur.get(key) if isinstance(cur.get(key), dict) else {}, on=on)
        else:
            cur[key] = on
        watch._write(path, cur)
        return 200, alerts_list()
    if kind == 'level':
        import levels
        levels.set_alert({'id': data.get('id'), 'alert': on})
        return 200, alerts_list()
    if kind == 'price' and not on:
        watch.remove_alert(data.get('id'))
        return 200, alerts_list()
    return 400, {'error': 'Price alerts and combo alerts are switched off by removing them (Watch panel / Night desk).'}


# ------------------------------------------------------------------ scorecard + notes (51, 52)
def scorecard():
    import trading
    with trading._lock:
        j = trading._journal()
    trips, open_ = trading.round_trips(list(j['fills'].values()))
    out = {'n': len(trips)}
    if not trips:
        return out
    wins = [t['pnl'] for t in trips if t['pnl'] > 0]
    losses = [t['pnl'] for t in trips if t['pnl'] <= 0]
    gw, gl = sum(wins), -sum(losses)
    streak = worst = 0
    for t in sorted(trips, key=lambda x: x['exit_t']):
        streak = streak + 1 if t['pnl'] <= 0 else 0
        worst = max(worst, streak)
    out.update(profit_factor=round(gw / gl, 2) if gl else None, avg_win=round(gw / len(wins), 2) if wins else None,
               avg_loss=round(-gl / len(losses), 2) if losses else None, expectancy=round(sum(t['pnl'] for t in trips) / len(trips), 2),
               win_rate=round(len(wins) / len(trips) * 100), losing_streak=worst, total=round(sum(t['pnl'] for t in trips), 2))
    wd, sy = {}, {}
    for t in trips:
        k = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][date.fromisoformat(t['day']).weekday()]
        for b, key in ((wd, k), (sy, t['sym'])):
            x = b.setdefault(key, {'n': 0, 'pnl': 0.0, 'wins': 0})
            x['n'] += 1
            x['pnl'] = round(x['pnl'] + t['pnl'], 2)
            x['wins'] += t['pnl'] > 0
    out['weekday'], out['symbol'] = wd, dict(sorted(sy.items(), key=lambda kv: -kv[1]['pnl']))
    try:
        import orders
        nt = orders.notes()
    except Exception:
        nt = {}
    recent = []
    for t in sorted(trips, key=lambda x: -x['exit_t'])[:25]:
        r = {k: t[k] for k in ('sym', 'side', 'qty', 'entry', 'exit', 'pnl', 'exit_t', 'entry_session', 'exit_session')}
        for fid, n in nt.items():
            if t['id'].startswith(fid + ':'):
                r['exit_note'] = n.get('text')
            elif t['id'].endswith(':' + fid):
                r['entry_note'] = n.get('text')
        recent.append(r)
    out['recent'] = recent
    return out


# ------------------------------------------------------------------ go-live checklist (56)
def golive(broker):
    import sys
    sys.path.insert(0, os.path.join(ROOT, 'bin')) if os.path.join(ROOT, 'bin') not in sys.path else None
    import golive as g
    return g.checklist(broker, ROOT)


# ------------------------------------------------------------------ what changed (72)
def changes(since):
    now = _now()
    since = max(since or 0, now - 3 * 86400000)
    out = []
    try:
        import orders
        for n in orders.notes().values():
            if n.get('t', 0) > since:
                out.append({'t': n['t'], 'kind': 'fill', 'text': f"Filled {n['side']} {n['qty']:g} {n['sym']} at {n['price']:g}"})
        for l in watch._read(orders.LOG, [])[:60]:
            if l.get('at', 0) > since and l.get('kind') in ('guard', 'flip', 'schedule', 'stale', 'event'):
                out.append({'t': l['at'], 'kind': l['kind'], 'text': l['text']})
    except Exception:
        pass
    try:
        for a in watch._read(watch.LOG, [])[:80]:
            t = a.get('at') or a.get('t') or 0
            if isinstance(a, dict) and t > since:
                out.append({'t': t, 'kind': 'alert', 'text': a.get('text') or a.get('title') or ''})
    except Exception:
        pass
    try:
        import webwatch
        my = set(webwatch.held())
        import news
        with news._lock:
            for sym in my:
                for i in news._state['items'].get(sym, [])[:3]:
                    if i['ts'] * 1000 > since:
                        out.append({'t': i['ts'] * 1000, 'kind': 'news', 'text': f"{sym}: {i['title']}"})
        for sym in sorted(webwatch.mine())[:40]:
            s = watch.stats(sym, now)
            if s and s.get('close') is not None and abs(s['close']) >= 3:
                out.append({'t': now, 'kind': 'move', 'text': f"{sym} {s['close']:+.1f}% since the close"})
    except Exception:
        pass
    out.sort(key=lambda x: -x['t'])
    return {'since': since, 'items': out[:25]}


# ------------------------------------------------------------------ weekly report (53)
def weekly_report(push=True):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    import trading
    now = _now()
    d = watch.et(now).date()
    start = d - timedelta(days=d.weekday())               # Monday
    with trading._lock:
        j = trading._journal()
    trips, open_ = trading.round_trips(list(j['fills'].values()))
    wk = [t for t in trips if t['day'] >= start.isoformat()]
    os.makedirs(REPORTS, exist_ok=True)
    name = f'week-{start.isoformat()}.pdf'
    path = os.path.join(REPORTS, name)
    total = sum(t['pnl'] for t in wk)
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(8.5, 11))
        fig.text(0.07, 0.95, "Alex Hale's Board: weekly trading report", fontsize=16, weight='bold')
        fig.text(0.07, 0.925, f'Week of {start:%b %d, %Y} (made {watch.et(now):%a %b %d %I:%M %p} ET)', fontsize=10, color='#555')
        wins = [t for t in wk if t['pnl'] > 0]
        lines = [f'Trades closed: {len(wk)}', f'Total: ${total:,.2f}', f'Win rate: {len(wins) / len(wk) * 100:.0f}%' if wk else 'Win rate: -',
                 f'Best: ${max((t["pnl"] for t in wk), default=0):,.2f}   Worst: ${min((t["pnl"] for t in wk), default=0):,.2f}',
                 f'Open positions now: {len([o for o in open_ if o.get("qty")])}']
        for i, l in enumerate(lines):
            fig.text(0.07, 0.88 - i * 0.025, l, fontsize=11)
        by_day, by_sess, by_sym = {}, {}, {}
        for t in wk:
            by_day[t['day']] = by_day.get(t['day'], 0) + t['pnl']
            by_sess[t['entry_session']] = by_sess.get(t['entry_session'], 0) + t['pnl']
            by_sym[t['sym']] = by_sym.get(t['sym'], 0) + t['pnl']
        for k, (title, data) in enumerate((('By day', by_day), ('By session (entry)', by_sess), ('By symbol', by_sym))):
            ax = fig.add_axes([0.1, 0.55 - k * 0.2, 0.8, 0.13])
            ks = list(data)[:12]
            ax.bar(ks, [data[x] for x in ks], color=['#2e9e5b' if data[x] >= 0 else '#c0453b' for x in ks])
            ax.set_title(title, fontsize=10, loc='left')
            ax.axhline(0, color='#999', lw=0.8)
            ax.tick_params(labelsize=8)
        pdf.savefig(fig)
        plt.close(fig)
        fig = plt.figure(figsize=(8.5, 11))
        fig.text(0.07, 0.95, 'Trades this week', fontsize=14, weight='bold')
        y = 0.92
        for t in sorted(wk, key=lambda x: x['exit_t'])[:45]:
            fig.text(0.07, y, f"{watch.et(t['exit_t']):%a %I:%M %p}  {t['sym']:<6} {t['side']:<5} {t['qty']:>6g}  {t['entry']:>9.2f} -> {t['exit']:<9.2f}  ${t['pnl']:>9,.2f}  {t['entry_session']}",
                     fontsize=8, family='monospace')
            y -= 0.019
        try:
            lb = trading.leaderboard()
            fig.text(0.07, y - 0.03, 'Paper algos (leaderboard)', fontsize=12, weight='bold')
            for i, r in enumerate((lb.get('rows') or [])[:10]):
                d5, d20 = r['d5'], r['d20']
                fig.text(0.07, y - 0.06 - i * 0.02, f"{(r.get('algo') or '').upper():<6} {str(r.get('name') or r.get('file'))[:28]:<28} {r.get('symbol') or '':<6} "
                         f"5 days ${d5['pnl']:>9,.2f} ({d5['trades']} trades)   20 days ${d20['pnl']:>9,.2f}", fontsize=7, family='monospace')
        except Exception:
            pass
        pdf.savefig(fig)
        plt.close(fig)
    _put(report_at=now, report=name)
    if push:
        url = ''
        try:
            import alerts2
            url = alerts2._find_phone_url()
        except Exception:
            pass
        watch.push_later('Weekly trading report', f'Week of {start:%b %d}: {len(wk)} trades, ${total:,.0f}. ' + (f'{url}/reports/{name}' if url else f'On the Mac: web/reports/{name}'), 'page_facing_up')
    return name


def _loop():
    while True:
        try:
            d = watch.et(_now())
            s = _state()
            key = d.strftime('%Y-%m-%d')
            if d.weekday() == 4 and (d.hour > 20 or (d.hour == 20 and d.minute >= 30)) and s.get('report_day') != key:
                _put(report_day=key)
                weekly_report()
        except Exception as e:
            print(f'tools: {e!r}', flush=True)
        time.sleep(60)


def start():
    if _st['started']:
        return
    _st['started'] = True
    widget_key()
    threading.Thread(target=_loop, name='tools', daemon=True).start()


def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return v or ''


def get(sub, query):
    if sub == 'bars':
        try:
            return 200, bars(_q(query, 'sym'))
        except Exception as e:
            return 200, {'sym': _clean(_q(query, 'sym')), 'points': [], 'error': f'Yahoo did not answer: {str(e)[:120]}'}
    if sub == 'notes':
        return 200, {'notes': notes()}
    if sub == 'alerts':
        return 200, alerts_list()
    if sub == 'scorecard':
        return 200, scorecard()
    if sub == 'golive':
        return 200, golive(str(_q(query, 'broker') or 'ibkr').lower())
    if sub == 'changes':
        try:
            since = int(_q(query, 'since') or 0)
        except ValueError:
            since = 0
        return 200, changes(since)
    if sub == 'widget/setup':
        return 200, widget_setup()
    if sub in ('', 'status'):
        s = _state()
        return 200, {'report': s.get('report'), 'report_at': s.get('report_at'), 'reports': sorted(os.listdir(REPORTS))[::-1][:10] if os.path.isdir(REPORTS) else []}
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'notes':
        return save_note(data)
    if sub == 'alerts':
        return alerts_post(data)
    if sub == 'report':
        try:
            return 200, {'report': weekly_report(push=False)}
        except Exception as e:
            return 500, {'error': f'Could not make the report: {str(e)[:200]}'}
    return 404, {'error': 'unknown'}
