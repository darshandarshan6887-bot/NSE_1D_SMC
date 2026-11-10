/* Shared helpers for the Signals dashboard. No dependencies. */
(function (global) {
  'use strict';

  // 1D and 1H differ only in field suffix and in the window sizes (the same numbers as config.PROFILES).
  var TF = {
    '1d': { key: '1d', label: '1D', suffix: '', one: 'day', many: 'days', latest: 'Latest day',
            windows: [1, 2, 3, 5, 7, 10, 20], boardWindow: 10, note: 'Scanned once a day, after the market closes.' },
    '1h': { key: '1h', label: '1H', suffix: '_1h', one: 'candle', many: 'candles', latest: 'Latest candle',
            windows: [3, 6, 7, 10, 14, 20, 35], boardWindow: 20, note: 'Scanned seven times a day, after each hourly candle closes.' }
  };
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var STRUCT = { STRONG_LOW: 'strong low', WEAK_LOW: 'weak low', STRONG_HIGH: 'strong high', WEAK_HIGH: 'weak high' };
  var PARTS = [['Pattern', 30], ['Structure', 25], ['Freshness', 20], ['Volume', 15], ['Squeeze', 10]]; // order of `parts`

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function num(v) {
    if (v === null || v === undefined || v === '' || v === 'NA') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }
  function get(r, key, tf) { return r[key + TF[tf].suffix]; }
  // Must match detail_store.safe_name in the scanner: 'M&M' -> 'M~26M'
  function safeName(sym) {
    return String(sym).replace(/[^A-Za-z0-9._-]/g, function (c) {
      var h = c.charCodeAt(0).toString(16);
      return '~' + (h.length < 2 ? '0' + h : h);
    });
  }
  function price(v) {
    var n = num(v);
    return n === null ? '\u2013' : '\u20B9' + n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function since(n, tf) {
    n = num(n);
    if (n === null) return '\u2013';
    if (n === 0) return TF[tf].latest;
    return n + ' ' + (n === 1 ? TF[tf].one : TF[tf].many) + ' ago';
  }
  // 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' -> '6 Nov 2026' / '6 Nov, 15:15' (no Date parsing: Safari rejects the space)
  function when(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})(?: (\d{2}):(\d{2}))?/.exec(String(s || ''));
    if (!m) return '\u2013';
    var d = (+m[3]) + ' ' + MONTHS[+m[2] - 1];
    return m[4] ? d + ', ' + m[4] + ':' + m[5] : d + ' ' + m[1];
  }
  function dirLabel(d) { return d === 'bull' ? 'Bullish' : d === 'bear' ? 'Bearish' : 'Neutral'; }
  function pill(d) { return '<span class="pill ' + esc(d) + '">' + dirLabel(d) + '</span>'; }
  function structure(r, tf) {
    var t = get(r, 'trend', tf), sg = STRUCT[get(r, 'signal', tf)];
    if (t !== 'BULLISH' && t !== 'BEARISH') return '<span class="muted">No clear trend</span>';
    var cls = t === 'BULLISH' ? 'up' : 'down';
    return '<span class="' + cls + '">' + (t === 'BULLISH' ? 'Bullish' : 'Bearish') + '</span>' +
           (sg ? ' <span class="muted">' + sg + '</span>' : '');
  }
  function squeeze(state) { return state === 'fired' ? 'Squeeze fired' : state === 'on' ? 'Squeeze on' : state === 'off' ? 'No squeeze' : '\u2013'; }

  function getJson(url) {
    return fetch(url, { cache: 'no-cache' }).then(function (res) {
      if (!res.ok) { var e = new Error(url + ' returned ' + res.status); e.status = res.status; throw e; }
      return res.json();
    });
  }
  function loadStocks() { return getJson('../shared-data/stocks.json'); }
  function loadDetail(symbol) { return getJson('../shared-data/detail/' + safeName(symbol) + '.json'); }

  function params() { return new URLSearchParams(global.location.search); }
  function setParams(obj) {
    var p = params();
    Object.keys(obj).forEach(function (k) { if (obj[k] === null || obj[k] === '' || obj[k] === undefined) p.delete(k); else p.set(k, obj[k]); });
    var q = p.toString();
    try { global.history.replaceState(null, '', q ? '?' + q : global.location.pathname); } catch (e) { /* file:// etc. */ }
  }
  function tfFromUrl() { var t = params().get('tf'); return t === '1h' ? '1h' : '1d'; }
  function tfSwitch(el, current, onChange) {
    el.innerHTML = '<button type="button" data-tf="1d">1D</button><button type="button" data-tf="1h">1H</button>';
    function paint(tf) {
      Array.prototype.forEach.call(el.querySelectorAll('button'), function (b) { b.setAttribute('aria-pressed', String(b.dataset.tf === tf)); });
    }
    paint(current);
    el.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      paint(b.dataset.tf); onChange(b.dataset.tf);
    });
  }

  global.SIG = { TF: TF, PARTS: PARTS, esc: esc, num: num, get: get, safeName: safeName, price: price, since: since, when: when,
                 dirLabel: dirLabel, pill: pill, structure: structure, squeeze: squeeze, getJson: getJson,
                 loadStocks: loadStocks, loadDetail: loadDetail, params: params, setParams: setParams,
                 tfFromUrl: tfFromUrl, tfSwitch: tfSwitch };
})(window);
