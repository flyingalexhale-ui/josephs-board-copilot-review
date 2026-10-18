/* SELL ALL buttons (jb-kill). Self-contained: injects its own CSS.
   IBKR / Webull boards: a red "SELL ALL" button next to Connection, for that broker.
   Phone page: an "Emergency" card with one button per broker.
   Pressing one only opens a window showing the account (paper or LIVE), every position and open
   order, and how it would sell right now. Nothing is sent until SELL ALL is typed and the red
   button pressed. Then the window shows each step live (gateway/killswitch.py does the work). */
(function(){
  'use strict';
  var NAMES = {ibkr: 'IBKR', webull: 'Webull'};
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function api(b, body){
    var o = body ? {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}
                 : {credentials: 'same-origin', cache: 'no-store'};
    return fetch('/api/kill/' + b, o).then(function(r){
      return r.json().catch(function(){ return {error: 'the Mac answered ' + r.status}; });
    });
  }
  var css = document.createElement('style');
  css.textContent =
    '.kill-btn{background:#7a0d0d;color:#fff;border:1px solid #ff3b30;border-radius:7px;font:inherit;font-size:12px;font-weight:700;' +
      'letter-spacing:.04em;padding:8px 12px;cursor:pointer;white-space:nowrap;min-height:36px;}' +
    '.kill-btn:active{background:#ff3b30;}' +
    '.kill-card{border-color:#ff3b30 !important;}.kill-card .kill-row{display:flex;gap:8px;}.kill-card .kill-btn{flex:1;min-height:44px;font-size:13px;}' +
    '.kill-ov{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.7);display:flex;align-items:flex-start;justify-content:center;' +
      'padding:max(12px,env(safe-area-inset-top)) 10px 12px;overflow-y:auto;-webkit-overflow-scrolling:touch;}' +
    '.kill-box{background:#071a30;border:2px solid #ff3b30;border-radius:12px;width:100%;max-width:460px;padding:14px;color:#f2f2f2;' +
      'font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:13px;line-height:1.45;}' +
    '.kill-box h3{margin:0 0 8px;font-size:16px;color:#ff6b61;display:flex;justify-content:space-between;align-items:center;}' +
    '.kill-box h3 button{background:none;border:0;color:#9fb3c8;font-size:26px;line-height:1;cursor:pointer;padding:0 4px;min-width:44px;min-height:44px;}' +
    '.kill-box table{width:100%;border-collapse:collapse;margin:4px 0 8px;}.kill-box td{padding:3px 2px;border-bottom:1px solid #0d2740;}' +
    '.kill-box .r{text-align:right;white-space:nowrap;}.kill-box .dim{color:#6f8aa3;font-size:12px;}' +
    '.kill-acct{font-size:14px;margin-bottom:8px;}.kill-live{background:#ff3b30;color:#fff;font-weight:700;padding:1px 6px;border-radius:4px;}' +
    '.kill-paper{background:#0b2a44;color:#00e5ff;font-weight:700;padding:1px 6px;border-radius:4px;}' +
    '.kill-box input{width:100%;font:inherit;font-size:16px;padding:10px;border-radius:7px;border:1px solid #164060;background:#04101f;color:#fff;margin:6px 0;text-transform:uppercase;}' +
    '.kill-go{width:100%;min-height:48px;font-size:15px;}.kill-go[disabled]{opacity:.35;}' +
    '.kill-log{background:#04101f;border:1px solid #164060;border-radius:7px;padding:8px;max-height:45vh;overflow-y:auto;font-size:12px;white-space:pre-wrap;}' +
    '.kill-err{color:#ffb3ad;margin:6px 0;}';
  document.head.appendChild(css);

  var ov = null, timer = null;
  function close(){ if(timer){ clearInterval(timer); timer = null; } if(ov){ ov.remove(); ov = null; } }
  function open(b){
    close();
    ov = document.createElement('div'); ov.className = 'kill-ov';
    ov.innerHTML = '<div class="kill-box"><h3>SELL ALL · ' + NAMES[b] + '<button aria-label="Close">×</button></h3><div class="kill-body">Asking ' + NAMES[b] + ' what you hold…</div></div>';
    ov.querySelector('h3 button').onclick = close;
    ov.addEventListener('click', function(e){ if(e.target === ov) close(); });
    document.body.appendChild(ov);
    api(b).then(function(d){ if(ov) show(b, d); }).catch(function(){ body('<div class="kill-err">The Mac did not answer. Try again, or use the ' + NAMES[b] + ' app.</div>'); });
  }
  function body(html){ if(ov) ov.querySelector('.kill-body').innerHTML = html; }
  function show(b, d){
    if(d.run && d.run.running) return follow(b);
    var h = '';
    if(d.error){
      body('<div class="kill-err">' + esc(d.error) + '</div><div class="dim">Nothing was sent. Use the ' + NAMES[b] + ' app if you need to sell now.</div>' + last(d));
      return;
    }
    h += '<div class="kill-acct">Account ' + esc(d.account) + ' <span class="' + (d.kind === 'LIVE' ? 'kill-live">LIVE · REAL MONEY' : 'kill-paper">paper') + '</span></div>';
    var pos = d.positions || [], ord = d.orders || [];
    h += '<div class="dim">Positions</div>' + (pos.length ? '<table>' + pos.map(function(p){
      return '<tr><td>' + esc(p.sym) + '</td><td class="r">' + (p.qty > 0 ? 'long ' : 'short ') + Math.abs(p.qty).toLocaleString('en-US') + '</td><td class="r dim">' +
        (p.closes ? (p.qty > 0 ? 'sell' : 'buy back') : 'left alone (' + esc(String(p.type).toLowerCase()) + ')') + '</td></tr>';
    }).join('') + '</table>' : '<p>None.</p>');
    h += '<div class="dim">Open orders (all cancelled)</div>' + (ord.length ? '<table>' + ord.map(function(o){
      return '<tr><td>' + esc(o.sym) + '</td><td class="r">' + esc(String(o.side).toLowerCase()) + ' ' + (+o.qty || '') + '</td><td class="r dim">' + esc(o.status) + '</td></tr>';
    }).join('') + '</table>' : '<p>None.</p>');
    h += '<p>Now: <b>' + esc(d.session) + '</b>, so it uses ' + esc(d.how) + '. It also pauses the ' + NAMES[b] + ' algo.</p>';
    h += '<label class="dim" for="killTxt">Type SELL ALL to confirm</label><input id="killTxt" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="SELL ALL">' +
      '<button class="kill-btn kill-go" id="killGo" disabled>SELL EVERYTHING ON ' + NAMES[b].toUpperCase() + '</button><div class="kill-err" id="killErr"></div>' + last(d);
    body(h);
    var t = ov.querySelector('#killTxt'), go = ov.querySelector('#killGo');
    t.oninput = function(){ go.disabled = t.value.trim().toUpperCase() !== 'SELL ALL'; };
    go.onclick = function(){
      go.disabled = true; go.textContent = 'Sending…';
      api(b, {confirm: t.value}).then(function(r){
        if(r.error){ ov.querySelector('#killErr').textContent = r.error; go.disabled = false; go.textContent = 'SELL EVERYTHING ON ' + NAMES[b].toUpperCase(); return; }
        follow(b);
      }).catch(function(){ ov.querySelector('#killErr').textContent = 'The Mac did not answer: it may or may not have started. Close and reopen this to see.'; });
    };
  }
  function last(d){
    var r = d.run;
    return r && r.ended ? '<p class="dim" style="margin-top:10px">Last run ' + new Date(r.started).toLocaleString() + ': ' + esc(r.result) + '</p>' : '';
  }
  function follow(b){
    body('<div class="kill-log" id="killLog">Starting…</div><p class="dim" id="killSt">Working… you can close this window: it carries on, and a phone push comes when it is done.</p>');
    function poll(){
      api(b).then(function(d){
        var r = d.run; if(!r || !ov) return;
        var el = ov.querySelector('#killLog');
        el.textContent = r.lines.map(function(l){ return new Date(l.t).toLocaleTimeString() + '  ' + l.text; }).join('\n');
        el.scrollTop = el.scrollHeight;
        if(r.ended){ clearInterval(timer); timer = null; ov.querySelector('#killSt').innerHTML = '<b>Done: ' + esc(r.result) + '.</b> Check the ' + NAMES[b] + ' app to be sure.'; }
      });
    }
    poll(); timer = setInterval(poll, 2000);
  }
  function button(b){
    var x = document.createElement('button'); x.className = 'kill-btn'; x.type = 'button';
    x.textContent = 'SELL ALL' + (document.getElementById('jb-kill-card') ? ' ' + NAMES[b] : '');
    x.title = 'Close every ' + NAMES[b] + ' position and cancel every order (asks first)';
    x.onclick = function(){ open(b); };
    return x;
  }
  window.jbKill = open;

  var cfg = window.BOARD_CONFIG;
  if(cfg && NAMES[cfg.broker]){            // IBKR / Webull board: next to the Connection button
    var tries = 0, iv = setInterval(function(){
      var adv = document.getElementById('advBtn');
      if(adv || ++tries > 40){
        clearInterval(iv);
        if(adv && !document.getElementById('jb-kill-btn')){ var x = button(cfg.broker); x.id = 'jb-kill-btn'; adv.parentNode.insertBefore(x, adv.nextSibling); }
      }
    }, 250);
  } else if(document.querySelector('.links')){   // phone page: an Emergency card above the links
    var card = document.createElement('div'); card.className = 'card kill-card'; card.id = 'jb-kill-card';
    card.innerHTML = '<h2>Emergency</h2><div class="kill-row"></div><div class="note" style="margin-top:6px">Closes every position and cancels every order on that broker, and pauses its algo. Shows what it will do and asks you to confirm first.</div>';
    document.querySelector('.links').parentNode.insertBefore(card, document.querySelector('.links'));
    card.querySelector('.kill-row').appendChild(button('ibkr'));
    card.querySelector('.kill-row').appendChild(button('webull'));
  }
})();
