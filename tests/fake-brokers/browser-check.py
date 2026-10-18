"""Loads every page in web/ from a running gateway in headless Chromium, logged in, at 1440x900 and at
iPhone size. Reports JavaScript errors, failed requests to the board itself, sideways scrolling or
content wider than the phone screen, and buttons whose click throws a JavaScript error (buttons that
place orders, sell, restart or delete are not clicked). Saves a screenshot of each page.
(JB_CHROMIUM=/path/to/chrome to use an already installed Chromium)
usage: python3 tests/fake-brokers/browser-check.py http://127.0.0.1:8090 testpw <screenshot folder> [page.html ...]"""
import json
import os
import re
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

BASE, PW, SHOTS = sys.argv[1].rstrip('/'), sys.argv[2], sys.argv[3]
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'web')
PAGES = sys.argv[4:] or sorted(f for f in os.listdir(WEB) if f.endswith('.html'))
SKIP_CLICK = re.compile(r'sell|buy|send|flip|restart|stop|resume|kill|backup|delete|remove|reset|log ?out|clear|cancel|chase|'
                        r'place|go live|confirm|save|turn off|run now|make one|test push|push|refresh now|switch', re.I)
os.makedirs(SHOTS, exist_ok=True)

OVERFLOW_JS = """() => {
  const vw = document.documentElement.clientWidth;
  const out = [];
  const scrollable = el => { for (let p = el.parentElement; p; p = p.parentElement) {
      const s = getComputedStyle(p); if (/(auto|scroll|hidden)/.test(s.overflowX) && p.scrollWidth > p.clientWidth + 1) return true; }
    return false; };
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const s = getComputedStyle(el);
    if (s.position === 'fixed' && r.left >= vw) continue;
    if (r.right > vw + 2 && !scrollable(el)) {
      out.push({tag: el.tagName.toLowerCase(), id: el.id, cls: (el.className && el.className.baseVal === undefined ? el.className : '').toString().slice(0, 40),
                right: Math.round(r.right), text: (el.innerText || '').trim().slice(0, 50)});
    }
  }
  const leaf = out.filter(a => !out.some(b => b !== a && b.right >= a.right && false));
  return {vw, sw: document.documentElement.scrollWidth, bw: document.body ? document.body.scrollWidth : 0, wide: leaf.slice(-8)};
}"""


def run(pw, mode):
    browser = pw.chromium.launch(executable_path=os.environ.get('JB_CHROMIUM') or None)
    ctx_args = dict(viewport={'width': 1440, 'height': 900}) if mode == 'desktop' else dict(pw.devices['iPhone 13'])
    ctx = browser.new_context(**ctx_args)
    page = ctx.new_page()
    page.goto(BASE + '/login')
    page.fill('#pw', PW)
    page.click('button[type=submit]')
    page.wait_for_load_state('networkidle')
    results = {}
    for name in PAGES:
        errs, bad = [], []
        page = ctx.new_page()
        page.on('pageerror', lambda e, errs=errs: errs.append('pageerror: ' + str(e)[:300]))
        page.on('console', lambda m, errs=errs: errs.append('console.' + m.type + ': ' + m.text[:300]) if m.type == 'error' else None)
        page.on('dialog', lambda d: d.dismiss())

        def resp(r, bad=bad):
            u = urllib.parse.urlsplit(r.url)
            if u.netloc == urllib.parse.urlsplit(BASE).netloc and r.status >= 400 and not u.path.startswith(('/api/tv', '/api/widget')):
                bad.append(f'{r.status} {u.path}')
        page.on('response', resp)
        try:
            page.goto(f'{BASE}/{name}', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(6000)
        except Exception as e:
            errs.append('load: ' + str(e)[:200])
        ov = page.evaluate(OVERFLOW_JS) if mode == 'phone' else None
        page.screenshot(path=os.path.join(SHOTS, f'{mode}-{name}.png'), full_page=False)
        clicked, broke = 0, []
        if mode == 'desktop':
            buttons = page.query_selector_all('button:visible, [role=button]:visible, .btn:visible')
            for b in buttons[:60]:
                try:
                    label = (b.inner_text() or b.get_attribute('title') or '').strip()
                except Exception:
                    continue
                if not label or SKIP_CLICK.search(label):
                    continue
                n0 = len(errs)
                try:
                    b.click(timeout=2000, no_wait_after=True)
                    page.wait_for_timeout(400)
                    clicked += 1
                except Exception:
                    continue
                if len(errs) > n0:
                    broke.append(f'{label[:40]}: {errs[-1][:150]}')
                if page.url.split('?')[0] != f'{BASE}/{name}':
                    try:
                        page.goto(f'{BASE}/{name}', wait_until='domcontentloaded', timeout=30000)
                        page.wait_for_timeout(1500)
                    except Exception:
                        pass
        results[name] = {'errors': errs, 'bad_requests': sorted(set(bad)), 'overflow': ov, 'clicked': clicked, 'broken_buttons': broke}
        page.close()
    browser.close()
    return results


with sync_playwright() as pw:
    out = {m: run(pw, m) for m in ('desktop', 'phone')}
json.dump(out, open(os.path.join(SHOTS, 'results.json'), 'w'), indent=1)
for m, res in out.items():
    print(f'===== {m}')
    for name, r in res.items():
        ov = r['overflow']
        side = ov and ov['sw'] > ov['vw'] + 1
        flags = []
        if r['errors']:
            flags.append(f"{len(r['errors'])} JS errors")
        if r['bad_requests']:
            flags.append('failed: ' + ', '.join(r['bad_requests'][:5]))
        if side:
            flags.append(f"SCROLLS SIDEWAYS (page {ov['sw']}px on a {ov['vw']}px screen)")
        if ov and ov['wide']:
            flags.append(f"{len(ov['wide'])}+ elements wider than the screen")
        if r['broken_buttons']:
            flags.append(f"{len(r['broken_buttons'])} buttons threw")
        print(f"{name:22s} {'OK' if not flags else '; '.join(flags)}" + (f"  (clicked {r['clicked']})" if m == 'desktop' else ''))
        for e in r['errors'][:4]:
            print('      ', e[:220])
        for b in r['broken_buttons'][:4]:
            print('       button', b[:220])
        if ov and ov['wide']:
            for w in ov['wide'][:4]:
                print('       wide:', w)
