/* Alex Hale's Board -- the order ticket form (jb-ticket), shared by the Night desk, the phone page and
 * the price chart (plus.js). var h = window.JBTicket.mount(element, {sym, compact, onChange});
 * h.setPrice(p), h.setSym(s), h.state() -> {sym, side, limit}, h.fill({broker, side, sym, qty, limit}) (jb-hedge
 * Copy To Ticket; window.JBTicket.last is the last form mounted). Limit orders only; two steps:
 * Check (nothing sent), then Send. Quick buttons: the current price +/- 0.5 / 1 / 2 %, and
 * "all I hold" for a sell.
 * Data: /api/plus/ticket (gateway/ticket.py).
 * jb-orders additions (only shown when gateway/orders.py is installed): how long the order stays,
 * ladder (split into 2-5 limits), saved sizes ($ buttons and last shares per symbol), IBKR's own
 * preview (commission, cash after), a safety stop / target / trailing stop / break-even for a buy
 * (watched by the board once the buy fills), the Flip button (TQQQ <-> SQQQ) and Chase on open orders. */
(function () {
  'use strict';
  if (window.JBTicket) return;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function api(p, data) {
    return fetch('/api/plus/ticket' + p, data ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); });
  }
  var css = '.tk{font-size:14px;line-height:1.45}.tk .tk-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0}' +
    '.tk input,.tk select{font:inherit;font-size:16px;padding:7px 8px;border-radius:6px;border:1px solid #2a3d52;background:#0b1622;color:#e6eef6;min-width:0}' +
    '.tk button{font:inherit;font-size:15px;padding:8px 14px;border-radius:6px;border:1px solid #2a3d52;background:#14202e;color:#cfe0ee;cursor:pointer}' +
    '.tk .tk-seg button.on{background:#1e5a78;border-color:#7fd6ff;color:#fff}.tk .tk-seg button.buy.on{background:#145c2c;border-color:#5dff8f}.tk .tk-seg button.sell.on{background:#7a1c1c;border-color:#ff8a80}' +
    '.tk .tk-q{color:#9fb3c8;font-size:12.5px}.tk .tk-q b{cursor:pointer;text-decoration:underline dotted;color:#cfe0ee}' +
    '.tk .tk-box{border:1px solid #7a5c00;background:#2e2400;color:#ffe9a6;border-radius:8px;padding:10px 12px;margin:8px 0}.tk .tk-box.live{border-color:#ff8a80;background:#3a0b0b;color:#ffd6d2}' +
    '.tk .tk-err{color:#ff8a80}.tk .tk-send{background:#7a1c1c!important;border-color:#ff8a80!important;color:#fff!important;font-weight:700}' +
    '.tk .tk-list div{padding:5px 0;border-bottom:1px solid rgba(127,147,168,.15);font-size:13px}.tk .tk-note{color:#7f93a8;font-size:12px}' +
    '.tk .tk-more{margin:6px 0}.tk .tk-more summary{cursor:pointer;color:#9fb3c8;font-size:13px}.tk .tk-sm{padding:4px 8px!important;font-size:12px!important}' +
    '.tk .tk-more input{width:84px}.tk .tk-more label{font-size:12.5px;color:#9fb3c8;display:inline-flex;align-items:center;gap:4px}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  function oapi(p, data) {
    return fetch('/api/plus/orders' + p, data ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); });
  }
  function mount(el, opts) {
    opts = opts || {};
    var s = { broker: localStorage.getItem('tk_broker') || 'ibkr', side: 'buy' }, q = null, pos = [];
    el.innerHTML = '<div class="tk"><div class="tk-row tk-seg" data-g="broker"><button data-v="ibkr">IBKR</button><button data-v="webull">Webull</button></div>' +
      '<div class="tk-row tk-seg" data-g="side"><button class="buy" data-v="buy">Buy</button><button class="sell" data-v="sell">Sell</button></div>' +
      '<div class="tk-row"><input class="tk-sym" placeholder="TQQQ" size="7" autocapitalize="characters" autocomplete="off"><input class="tk-qty" type="number" inputmode="numeric" placeholder="shares" style="width:90px">' +
      '<input class="tk-lim" type="number" inputmode="decimal" step="0.01" placeholder="limit $" style="width:100px"></div><div class="tk-q"></div><div class="tk-row tk-quick"></div>' +
      '<div class="tk-row tk-sizes"></div>' +
      '<details class="tk-more" style="display:none"><summary>More: how long, ladder, safety stop</summary>' +
      '<div class="tk-row"><label>Good for <select class="tk-tif"><option value="day">this session</option><option value="ond">overnight + tomorrow (IBKR overnight)</option><option value="gtc">until I cancel</option></select></label></div>' +
      '<div class="tk-row"><label>Split into <select class="tk-lad"><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select> limits</label>' +
      '<label>$ apart <input class="tk-step" type="number" inputmode="decimal" step="0.01" value="0.02" style="width:70px"></label></div>' +
      '<div class="tk-prot"><div class="tk-note">For a buy: the board watches these once it fills, in every session, and sells if hit.</div>' +
      '<div class="tk-row tk-tplrow" style="display:none"><label>Exit plan <select class="tk-tpl"><option value="">none</option></select></label><span class="tk-note">a plan replaces the stop boxes below</span></div>' +
      '<div class="tk-row"><label>Stop $<input class="tk-stop" type="number" inputmode="decimal" step="0.01"></label><label>Target $<input class="tk-tgt" type="number" inputmode="decimal" step="0.01"></label></div>' +
      '<div class="tk-row"><label>Trailing %<input class="tk-trail" type="number" inputmode="decimal" step="0.5"></label><label>Break-even after +%<input class="tk-be" type="number" inputmode="decimal" step="0.5"></label></div></div>' +
      '</details><div class="tk-row tk-flip"></div>' +
      '<div class="tk-row"><button class="tk-check">Check order</button><span class="tk-msg"></span></div><div class="tk-confirm"></div>' +
      '<div class="tk-note">Limit orders only, any session (overnight goes to IBKR\'s OVERNIGHT venue / Webull\'s NIGHT session). Nothing is sent until you tap the red Send button. A limit more than 5% from the last price is refused.</div>' +
      (opts.compact ? '' : '<div class="tk-list"></div>') + '</div>';
    var $ = function (c) { return el.querySelector(c); };
    function seg() {
      [].forEach.call(el.querySelectorAll('.tk-seg'), function (g) { [].forEach.call(g.querySelectorAll('button'), function (b) { b.classList.toggle('on', s[g.dataset.g] === b.dataset.v); }); });
    }
    [].forEach.call(el.querySelectorAll('.tk-seg button'), function (b) {
      b.onclick = function () { s[b.parentNode.dataset.g] = b.dataset.v; if (b.parentNode.dataset.g === 'broker') try { localStorage.setItem('tk_broker', s.broker); } catch (e) {} seg(); clearConfirm(); showQuote(); changed(); };
    });
    seg();
    function clearConfirm() { $('.tk-confirm').innerHTML = ''; }
    function showQuote() {
      if (!q || q.sym !== $('.tk-sym').value.trim().toUpperCase()) { $('.tk-q').innerHTML = ''; return; }
      var parts = [];
      if (q.bid != null) parts.push('bid <b data-p="' + q.bid + '">' + q.bid + '</b> · ask <b data-p="' + q.ask + '">' + q.ask + '</b>');
      if (q.last != null) parts.push(q.src === 'yahoo' ? 'Current price of ' + esc(q.sym) + ': <b data-p="' + q.last + '">$' + round(q.last) + '</b> <span class="tk-note">(from Yahoo; can lag a minute, no overnight)</span>' : 'last <b data-p="' + q.last + '">' + q.last + '</b>');
      $('.tk-q').innerHTML = parts.length ? parts.join(' · ') + ' <span class="tk-note">(tap a price to use it)</span>' : 'Could not get a current price for ' + esc(q.sym) + ' right now. Check the symbol, or try again in a minute.';
      [].forEach.call(el.querySelectorAll('.tk-q b[data-p]'), function (b) { b.onclick = function () { setPrice(+b.dataset.p); }; });
      var nt = (window.__tkNotes || {})[q.sym];                      // your note on this symbol (idea 71)
      if (nt) $('.tk-q').insertAdjacentHTML('beforeend', '<div class="tk-note" style="color:#7fd6ff;white-space:pre-wrap">NOTE: ' + esc(nt.text.slice(0, 300)) + '</div>');
      quick();
    }
    if (!window.__tkNotes) {
      window.__tkNotes = {};
      fetch('/api/plus/tools/notes', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d && d.notes) { window.__tkNotes = d.notes; showQuote(); } }).catch(function () {});
    }
    function round(p) { return p >= 1 ? Math.round(p * 100) / 100 : Math.round(p * 10000) / 10000; }
    function quick() {
      var box = $('.tk-quick'), sym = $('.tk-sym').value.trim().toUpperCase(), ref = q && q.sym === sym ? (q.last || (q.bid != null ? (q.bid + q.ask) / 2 : null)) : null;
      var h = '';
      if (ref) [-2, -1, -0.5, 0, 0.5, 1, 2].forEach(function (pc) {
        h += '<button data-q="' + round(ref * (1 + pc / 100)) + '" title="' + round(ref * (1 + pc / 100)) + '" style="padding:4px 8px;font-size:12px">' + (pc ? (pc > 0 ? '+' : '') + pc + '%' : 'now') + '</button>';
      });
      var held = pos.filter(function (x) { return x.sym === sym && x.broker === s.broker && x.qty > 0; })[0];
      if (held && s.side === 'sell') h += '<button data-all="' + Math.floor(held.qty) + '" style="padding:4px 8px;font-size:12px">all I hold (' + held.qty + ')</button>';
      box.innerHTML = h;
      [].forEach.call(box.querySelectorAll('[data-q]'), function (b) { b.onclick = function () { setPrice(+b.dataset.q); }; });
      [].forEach.call(box.querySelectorAll('[data-all]'), function (b) { b.onclick = function () { $('.tk-qty').value = b.dataset.all; clearConfirm(); }; });
      extras(sym, ref, held);
    }
    var ord = null;               // /api/plus/orders, when jb-orders is installed
    function extras(sym, ref, held) {
      if (!ord) return;
      var sz = ord.sizes || {}, h = '';
      (sz.dollars || []).forEach(function (d) { h += '<button class="tk-sm" data-d="' + d + '">$' + d.toLocaleString() + '</button>'; });
      var last = (sz.syms || {})[sym];
      if (last) h += '<button class="tk-sm" data-n="' + last + '">' + last + ' sh (last time)</button>';
      $('.tk-sizes').innerHTML = h ? '<span class="tk-note">Size:</span> ' + h : '';
      [].forEach.call(el.querySelectorAll('.tk-sizes [data-d]'), function (b) {
        b.onclick = function () { var p = +$('.tk-lim').value || ref; if (!p) { $('.tk-msg').textContent = 'Set a limit price first.'; return; } $('.tk-qty').value = Math.max(1, Math.floor(+b.dataset.d / p)); clearConfirm(); };
      });
      [].forEach.call(el.querySelectorAll('.tk-sizes [data-n]'), function (b) { b.onclick = function () { $('.tk-qty').value = b.dataset.n; clearConfirm(); }; });
      $('.tk-prot').style.display = s.side === 'buy' ? '' : 'none';
      var fb = $('.tk-flip'); fb.innerHTML = '';
      if (held && sym) oapi('/twin?sym=' + encodeURIComponent(sym)).then(function (d) {
        if (!d.twin) return;
        fb.innerHTML = '<button class="tk-sm tk-fl">Flip ' + esc(sym) + ' → ' + esc(d.twin) + '</button> <span class="tk-note">sell all ' + esc(sym) + ', buy ' + esc(d.twin) + ' with the money</span>';
        $('.tk-fl').onclick = function () {
          var m = $('.tk-msg'); m.textContent = 'Checking the flip…'; clearConfirm();
          oapi('/flip/check', { broker: s.broker, sym: sym }).then(function (r) {
            m.textContent = '';
            $('.tk-confirm').innerHTML = '<div class="tk-box live"><b>Flip</b><br>' + esc(r.text) + (r.warnings || []).map(function (w) { return '<br>⚠ ' + esc(w); }).join('') +
              '<div class="tk-row"><button class="tk-send tk-fgo">Flip now</button><button class="tk-cancel">Don\'t</button></div><div class="tk-note">Good for 2 minutes.</div></div>';
            $('.tk-cancel').onclick = clearConfirm;
            $('.tk-fgo').onclick = function () { this.disabled = true; oapi('/flip/go', { code: r.code }).then(function (x) { clearConfirm(); m.textContent = x.text; }).catch(function (e) { clearConfirm(); m.innerHTML = '<span class="tk-err">' + esc(e.message) + '</span>'; }); };
          }).catch(function (e) { m.innerHTML = '<span class="tk-err">' + esc(e.message) + '</span>'; });
        };
      }).catch(function () {});
    }
    oapi('').then(function (d) { ord = d; $('.tk-more').style.display = ''; quick(); }).catch(function () {});
    fetch('/api/plus/tradetools/templates', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {     // jb-tools2: exit plans
      if (!d || !d.plans || !d.plans.length) return;
      $('.tk-tpl').innerHTML = '<option value="">none</option>' + d.plans.map(function (p) { return '<option value="' + esc(p.name) + '" title="' + esc(p.text) + '">' + esc(p.name) + '</option>'; }).join('');
      $('.tk-tplrow').style.display = '';
    }).catch(function () {});
    function setPrice(p) { $('.tk-lim').value = round(p); clearConfirm(); changed(); }
    function changed() { if (opts.onChange) opts.onChange({ sym: $('.tk-sym').value.trim().toUpperCase(), side: s.side, limit: +$('.tk-lim').value || null }); }
    function loadQuote() {
      var sym = $('.tk-sym').value.trim().toUpperCase(); if (!sym) { q = null; showQuote(); return; }
      api('/quote?sym=' + encodeURIComponent(sym)).then(function (d) { q = d; showQuote(); }).catch(function () {});
    }
    api('/positions').then(function (d) { pos = d.positions || []; quick(); }).catch(function () {});
    var qt = 0;
    $('.tk-sym').oninput = function () {
      clearConfirm(); clearTimeout(qt);
      var sym = this.value.trim().toUpperCase(); if (!sym) { q = null; showQuote(); return; }
      qt = setTimeout(loadQuote, 400); changed();
    };
    $('.tk-qty').oninput = clearConfirm;
    $('.tk-lim').oninput = function () { clearConfirm(); changed(); };
    $('.tk-check').onclick = function () {
      var m = $('.tk-msg'); m.textContent = 'Checking with ' + s.broker.toUpperCase() + '…'; clearConfirm();
      var more = ord ? { tif: $('.tk-tif').value, ladder: $('.tk-lad').value, step: $('.tk-step').value } : {};
      var prot = ord && s.side === 'buy' ? { stop: $('.tk-stop').value, target: $('.tk-tgt').value, trail: $('.tk-trail').value, be: $('.tk-be').value } : {};
      var tpl = s.side === 'buy' && $('.tk-tpl') ? $('.tk-tpl').value : '';      // jb-tools2: an exit plan replaces the stop boxes
      var hasProt = !tpl && (prot.stop || prot.target || prot.trail);
      api('/check', Object.assign({ broker: s.broker, side: s.side, sym: $('.tk-sym').value, qty: $('.tk-qty').value, limit: $('.tk-lim').value }, more)).then(function (d) {
        var pv = d.preview, pvh = '';
        if (pv && !pv.error) pvh = '<br><span class="tk-note">IBKR preview: commission about $' + (pv.commission != null ? pv.commission : '?') +
          (pv.equity_after != null ? ' · account value after $' + Math.round(pv.equity_after).toLocaleString() : '') + (pv.warning ? ' · ' + esc(pv.warning) : '') + '</span>';
        var ph = hasProt ? '<br>🛡 Then the board watches: ' + esc([prot.stop ? 'stop ' + prot.stop : '', prot.target ? 'target ' + prot.target : '', prot.trail ? 'trailing ' + prot.trail + '%' : '', prot.be ? 'break-even after +' + prot.be + '%' : ''].filter(Boolean).join(', ')) : '';
        if (tpl) ph = '<br>🎯 Exit plan: ' + esc(tpl + ' (' + ((($('.tk-tpl').selectedOptions || [])[0] || {}).title || '') + ')') + ', starts once the buy fills.';
        m.textContent = '';
        $('.tk-confirm').innerHTML = '<div class="tk-box' + (d.kind === 'LIVE' ? ' live' : '') + '"><b>' + (d.kind === 'LIVE' ? 'REAL MONEY · ' : 'Paper account · ') + esc(d.broker.toUpperCase()) + ' ' + esc(d.account) + '</b><br>' + esc(d.text) +
          (d.side === 'sell' ? '<br>You hold ' + d.held + '.' : '') + pvh + ph + (d.warnings || []).map(function (w) { return '<br>⚠ ' + esc(w); }).join('') +
          '<div class="tk-row"><button class="tk-send">Send ' + esc(d.side.toUpperCase() + ' ' + d.qty + ' ' + d.sym) + '</button><button class="tk-cancel">Don\'t send</button></div><div class="tk-note">This check is good for 2 minutes.</div></div>';
        $('.tk-cancel').onclick = clearConfirm;
        $('.tk-send').onclick = function () {
          this.disabled = true; this.textContent = 'Sending…';
          api('/send', { code: d.code }).then(function (r) {
            clearConfirm(); m.innerHTML = esc(r.ticket.status === 'Refused' ? 'Refused: ' + (r.ticket.note || '') : 'Sent: ' + r.ticket.status + ' (see the Night desk or phone page to follow or cancel it)'); if (!opts.compact) list();
            if (ord && r.ticket.status !== 'Refused') {
              oapi('/sizes', { sym: d.sym, qty: d.qty }).then(function (x) { ord = x; }).catch(function () {});
              if (tpl) fetch('/api/plus/tradetools/plan', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ broker: d.broker, sym: d.sym, template: tpl, after: (r.tickets || [r.ticket]).map(function (x) { return x.id; }) }) })
                .then(function (x) { return x.json(); }).then(function (x) { m.innerHTML += '<br>🎯 ' + esc(x.text || x.error || ''); })
                .catch(function () { m.innerHTML += '<br><span class="tk-err">Exit plan NOT set: set a stop now.</span>'; });
              if (hasProt) oapi('/guard', Object.assign({ broker: d.broker, sym: d.sym, after: r.ticket.id }, prot)).then(function () { m.innerHTML += '<br>🛡 Safety stop set: it starts once the buy fills.'; })
                .catch(function (e) { m.innerHTML += '<br><span class="tk-err">Safety stop NOT set: ' + esc(e.message) + '</span>'; });
            }
          })
            .catch(function (e) { clearConfirm(); m.innerHTML = '<span class="tk-err">' + esc(e.message) + '</span>'; });
        };
      }).catch(function (e) { m.innerHTML = '<span class="tk-err">' + esc(e.message) + '</span>'; });
    };
    function list() {
      api('').then(function (d) {
        var t = d.tickets || [];
        $('.tk-list').innerHTML = t.length ? '<div class="tk-note" style="margin-top:8px">Your recent tickets (max $' + Math.round(d.max).toLocaleString() + ' per order):</div>' + t.slice(0, 8).map(function (x) {
          var open = /^(Sending|Submitted|PreSubmitted|PendingSubmit|Sent|Working|Pending|Partial)/.test(x.status || '');
          return '<div>' + esc(new Date(x.at).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })) + ' · <b>' + esc(x.side.toUpperCase() + ' ' + x.qty + ' ' + x.sym) + '</b> @ ' + x.limit + ' ' + esc(x.broker.toUpperCase()) +
            ' · ' + esc(x.status) + (x.filled ? ' (' + x.filled + (x.avg ? ' @ ' + Math.round(x.avg * 100) / 100 : '') + ')' : '') + (x.note && !open ? ' <span class="tk-note">' + esc(x.note) + '</span>' : '') +
            (open ? ' <button data-c="' + esc(x.id) + '" style="padding:2px 8px;font-size:12px">Cancel</button>' + (ord ? ' <button data-ch="' + esc(x.id) + '" style="padding:2px 8px;font-size:12px" title="Move the limit toward the market every 20 s, up to 6 times, at most 1% past your price">Chase</button>' : '') : '') + '</div>';
        }).join('') : '';
        [].forEach.call(el.querySelectorAll('[data-c]'), function (b) { b.onclick = function () { b.textContent = '…'; api('/cancel', { id: b.dataset.c }).then(list).catch(function (e) { b.textContent = e.message; }); }; });
        [].forEach.call(el.querySelectorAll('[data-ch]'), function (b) { b.onclick = function () { b.textContent = '…'; oapi('/chase', { id: b.dataset.ch }).then(function (r) { b.textContent = 'Chasing'; b.title = r.text; }).catch(function (e) { b.textContent = e.message; }); }; });
      }).catch(function () {});
    }
    if (!opts.compact) { list(); setInterval(function () { if (!document.hidden) list(); }, 30000); }
    if (opts.sym) { $('.tk-sym').value = opts.sym; loadQuote(); }
    var handle = {
      setPrice: setPrice,
      setSym: function (sym) { if ($('.tk-sym').value.trim().toUpperCase() !== sym) { $('.tk-sym').value = sym; $('.tk-lim').value = ''; clearConfirm(); loadQuote(); changed(); } },
      refresh: loadQuote,
      state: function () { return { sym: $('.tk-sym').value.trim().toUpperCase(), side: s.side, limit: +$('.tk-lim').value || null }; },
      // jb-hedge: Copy To Ticket fills the form (broker, side, symbol, shares, limit); nothing is sent until Check, then Send
      fill: function (o) {
        if (o.broker === 'ibkr' || o.broker === 'webull') { s.broker = o.broker; try { localStorage.setItem('tk_broker', s.broker); } catch (e) {} }
        if (o.side === 'buy' || o.side === 'sell') s.side = o.side;
        seg(); clearConfirm();
        $('.tk-sym').value = o.sym || ''; $('.tk-qty').value = o.qty || ''; $('.tk-lim').value = o.limit ? round(o.limit) : '';
        $('.tk-msg').textContent = o.note || '';
        loadQuote(); changed();
      },
      el: el
    };
    window.JBTicket.last = handle;
    return handle;
  }
  window.JBTicket = { mount: mount };
})();
