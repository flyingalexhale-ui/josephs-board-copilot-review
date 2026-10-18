"""Phone layout check for the board tiles' chip row (web/plus.js): at iPhone width, long chips (SPREAD ... 300x200,
AH PEAK ... ended ...) must stay inside their tile instead of running past it and scrolling the page sideways.
Needs Playwright with Chromium (pip install playwright; set JB_CHROMIUM=/path/to/chrome to use an installed one).
usage: python3 tests/phone-layout-test.py      (exit code 1 if a check fails)"""
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;font-family:monospace;font-size:16px"><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:8px">
<div class="tile" style="border:1px solid #444;padding:6px;min-width:0"><div class="plus">
<span class="pl-chip pl-g">SPREAD 10¢ (0.04%) · 300×200</span><span class="pl-chip pl-a">AH PEAK -4.8% · ended -3.3%</span>
<span class="pl-chip pl-d">NO TRADES SINCE 11:42 pm</span></div></div>
<div class="tile" style="border:1px solid #444;padding:6px;min-width:0"><div class="plus"><span class="pl-chip pl-b">WHY: NO NEWS</span></div></div>
</div></body></html>"""
bad = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get('JB_CHROMIUM') or None)
    pg = b.new_page(**p.devices['iPhone 13'])
    pg.set_content(PAGE)
    pg.add_script_tag(content=open(os.path.join(ROOT, 'web', 'plus.js'), encoding='utf-8').read())
    pg.wait_for_timeout(300)
    r = pg.evaluate("""() => { const out = [];
      document.querySelectorAll('.tile').forEach(t => { const tr = t.getBoundingClientRect();
        t.querySelectorAll('.pl-chip').forEach(c => { const r = c.getBoundingClientRect();
          if (r.right > tr.right + 0.5) out.push(c.textContent + ' ends at ' + Math.round(r.right) + ' px, tile at ' + Math.round(tr.right)); }); });
      return {over: out, sw: document.documentElement.scrollWidth, vw: document.documentElement.clientWidth}; }""")
    b.close()
ok1 = not r['over']
ok2 = r['sw'] <= r['vw']
print(('PASS ' if ok1 else 'FAIL ') + 'phone: every tile chip stays inside its tile' + ('' if ok1 else '  -- ' + '; '.join(r['over'])))
print(('PASS ' if ok2 else 'FAIL ') + f"phone: the page does not scroll sideways ({r['sw']} px wide on a {r['vw']} px screen)")
sys.exit(0 if ok1 and ok2 else 1)
