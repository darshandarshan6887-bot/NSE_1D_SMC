/* Shared by the dashboard and the stock page. No build step, no dependencies. */
const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const PART_LABELS = [['pattern','Pattern strength',30],['smc','SMC alignment',25],['fresh','Freshness',20],['volume','Volume',15],['squeeze','Squeeze',10]];

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const store = {
  get(k){ try { return localStorage.getItem(k); } catch(e){ return null; } },
  set(k,v){ try { localStorage.setItem(k,v); } catch(e){} }
};
/* ---------- NSE adapter: turns the NSE scanner's flat stocks.json (a list, daily fields plain, hourly fields with _1h)
   into the {meta, stocks} shape this page was written for. The full signal list, indicators and candles of one stock
   live in shared-data/detail/<SYMBOL>.json and are merged in by the stock page (mergeDetail). ---------- */
const NSE_GROUP = {"Dragonfly Doji":"1-candle","Gravestone Doji":"1-candle","Hammer":"1-candle","Hanging Man":"1-candle","Inverted Hammer":"1-candle","Shooting Star":"1-candle","Bullish Marubozu":"1-candle","Bearish Marubozu":"1-candle","Bullish Spinning Top":"1-candle","Bearish Spinning Top":"1-candle","Bullish Belt Hold":"1-candle","Bearish Belt Hold":"1-candle","Doji":"1-candle","Long-Legged Doji":"1-candle","High Wave Candle":"1-candle","Bullish Engulfing":"2-candle","Bearish Engulfing":"2-candle","Piercing Line":"2-candle","Dark Cloud Cover":"2-candle","Bullish Harami":"2-candle","Bearish Harami":"2-candle","Tweezer Bottom":"2-candle","Tweezer Top":"2-candle","Bullish Kicker":"2-candle","Bearish Kicker":"2-candle","Rising Window":"2-candle","Falling Window":"2-candle","Bullish Harami Cross":"2-candle","Bearish Harami Cross":"2-candle","Morning Star":"3-candle","Evening Star":"3-candle","Bullish Abandoned Baby":"3-candle","Bearish Abandoned Baby":"3-candle","Three White Soldiers":"3-candle","Three Black Crows":"3-candle","Three Inside Up":"3-candle","Three Inside Down":"3-candle","Three Outside Up":"3-candle","Three Outside Down":"3-candle","Bullish Tri-Star":"3-candle","Bearish Tri-Star":"3-candle","Stick Sandwich":"3-candle","Rising Three Methods":"extended","Falling Three Methods":"extended","Mat Hold":"extended","Ladder Bottom":"extended","Ladder Top":"extended","Concealing Baby Swallow":"extended","Bullish Meeting Lines":"extended","Bearish Meeting Lines":"extended","On-Neck Line":"extended","In-Neck Line":"extended","Thrusting Line":"extended","Bullish Doji Star":"extended","Bearish Doji Star":"extended","Homing Pigeon":"extended","Matching Low":"extended","Bullish Separating Lines":"extended","Bearish Separating Lines":"extended","Two Black Gapping":"extended","Inside Bar":"range","NR4":"range","NR7":"range","Bollinger Squeeze Breakout":"squeeze","TTM Squeeze Fired":"squeeze","ATR Expansion Breakout":"squeeze","Donchian Squeeze Breakout":"squeeze","Volume Spike Breakout":"volume","Pocket Pivot":"volume","ADX Trend Start":"trend","MA Ribbon Fan-Out":"trend","Fair Value Gap":"structure","Turtle Soup":"structure","Undercut & Rally":"structure","Ichimoku Cloud Twist":"structure"};
const NSE_TF = (() => { const q = new URLSearchParams(location.search).get('tf'); const t = q || store.get('nse-tf'); return t === '1h' ? '1h' : '1d'; })();
const NSE_SFX = NSE_TF === '1h' ? '_1h' : '';
function setTf(tf){ store.set('nse-tf', tf); const u = new URL(location.href); u.searchParams.set('tf', tf); location.href = u.toString(); }
const nnum = v => (v === null || v === undefined || v === '' || v === 'NA') ? null : (isFinite(Number(v)) ? Number(v) : null);
function safeName(sym){ return String(sym).replace(/[^A-Za-z0-9._-]/g, c => '~' + ('0' + c.charCodeAt(0).toString(16)).slice(-2)); }
function adaptStock(r){
  const x = NSE_SFX, g = k => r[k + x];
  const status = g('scanner_status');
  const top = g('sig_top') || [];
  const lastBar = g('last_bar_time') || null;
  const sigs = top.map(t => ({name: t.name, group: NSE_GROUP[t.name] || '', dir: t.dir, len: 1, stop: null, since: t.since,
    time: t.since === 0 ? lastBar : null, price: null, vol: null, trend_before: null, confirm: t.confirm || null, score: t.score, parts: []}));
  const nLayers = nnum(g('compression_layers')) || 0;
  const smc = {trend: g('trend'), structure_event: g('structure_event'), last_structure_time: g('last_structure_time'),
    top_signal: g('top_signal'), bottom_signal: g('bottom_signal'),
    top_signal_time: g('top_signal_time'), top_signal_price: nnum(g('top_signal_price')),
    bottom_signal_time: g('bottom_signal_time'), bottom_signal_price: nnum(g('bottom_signal_price')),
    top_bars_since: nnum(g('top_bars_since')), bottom_bars_since: nnum(g('bottom_bars_since')),
    active_swing_high: nnum(g('active_swing_high')), active_swing_low: nnum(g('active_swing_low')),
    signal: g('signal'), signal_strength: g('signal_strength'), bars_since_signal: nnum(g('bars_since_signal')),
    last_signal_time: g('last_signal_time'), bearish_rank_score: nnum(g('bearish_rank_score')) || 0,
    bullish_rank_score: nnum(g('bullish_rank_score')) || 0, signal_rank_score: nnum(g('signal_rank_score')) || 0,
    structure_bars_since: -1, status: status};
  return {
    symbol: r.symbol, yahoo_symbol: r.symbol + '.NS', name: r.company_name || r.symbol,
    type: r.instrument_group || r.series || 'Stock', active: r.listed !== false, notes: r.sector || '',
    tradingview_url: r.tradingview_url, tz: 'Asia/Kolkata', scanned_at: r['scanned_at' + x] || r.scanned_at || '',
    bars: nnum(g('bars')), last_bar_time: lastBar, scanner_status: status === 'OK' || status === 'STALE' ? status : (status || 'PENDING_SCAN'),
    scan_error: r['scan_error' + x] || '', price: nnum(g('current_price')) ?? nnum(r.current_price), change_pct: null,
    smc: smc, signals: sigs,
    context: {squeeze_state: g('squeeze_state'), compression_layers: new Array(nLayers).fill('layer')},
    scores: {bull: nnum(g('sig_bull_score')) || 0, bear: nnum(g('sig_bear_score')) || 0, best: nnum(g('sig_best_score')) || 0},
    candles: [], sector: r.sector, cap_category: r.cap_category, index_memberships: r.index_memberships
  };
}
async function loadData(){
  const r = await fetch('../shared-data/stocks.json?t=' + Math.floor(Date.now() / 60000), {cache: 'no-store'});
  if (!r.ok) throw new Error('stocks.json returned HTTP ' + r.status);
  const raw = await r.json();
  if (!Array.isArray(raw)) throw new Error('stocks.json has an unexpected shape');
  const stocks = raw.map(adaptStock);
  await mergeBoard(stocks);
  const live = stocks.filter(s => s.scanner_status === 'OK' || s.scanner_status === 'STALE');
  const counts = {}; stocks.forEach(s => counts[s.scanner_status] = (counts[s.scanner_status] || 0) + 1);
  const latest = live.map(s => s.last_bar_time).filter(Boolean).sort().pop() || null;
  const scanned = raw.map(s => s['scanned_at' + NSE_SFX]).filter(Boolean).sort().pop() || '';
  const meta = {schema: 1, generated_ist: scanned, generated_et: '', generated_at: null, run_type: 'run',
    total_symbols: stocks.length, active_symbols: stocks.length, counts: counts, latest_candle: latest, csv_problems: [],
    window_options: NSE_TF === '1h' ? [3, 6, 7, 10, 14, 20, 35] : [1, 2, 3, 5, 7, 10, 20],
    top_board_window: NSE_TF === '1h' ? 20 : 10, top_board_size: 20, store_bars: 35, tf: NSE_TF};
  setRef(latest);
  return {meta: meta, stocks: stocks};
}
async function mergeBoard(stocks){      // signals, ATR, relative volume and recent candles per stock (built by build_board.py)
  try {
    const r = await fetch('../shared-data/board_' + NSE_TF + '.json?t=' + Math.floor(Date.now() / 60000), {cache: 'no-store'});
    if (!r.ok) return false;
    const b = (await r.json()).stocks || {};
    stocks.forEach(s => {
      const x = b[s.symbol]; if (!x) return;
      s.signals = x.signals || s.signals;
      s.context = Object.assign({}, s.context, x.context || {});
      s.candles = x.candles || [];
    });
    return true;
  } catch (e) { return false; }
}
async function mergeDetail(s){          // stock page: add the full signal list, indicators and candles for this timeframe
  try {
    const r = await fetch('../shared-data/detail/' + safeName(s.symbol) + '.json?t=' + Math.floor(Date.now() / 60000), {cache: 'no-store'});
    if (!r.ok) return false;
    const d = (await r.json())[NSE_TF]; if (!d) return false;
    s.signals = d.signals || s.signals; s.context = d.context || s.context; s.candles = d.candles || s.candles;
    if (d.scores) s.scores = d.scores;
    if (d.levels) { s.levels = d.levels; s.levels_meta = d.levels_meta || {}; }
    return true;
  } catch (e) { return false; }
}
function safeUrl(u){ return typeof u === 'string' && /^https:\/\/www\.tradingview\.com\//.test(u) ? u : '#'; }
function dirLabel(d){ return d === 'bull' ? 'Bullish' : d === 'bear' ? 'Bearish' : 'Neutral'; }
function money(v){ return v == null ? '–' : Number(v).toFixed(v >= 1 ? 2 : 4); }
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
let REF_DAY = null;            // the day of the latest candle; older times are shown with their date
function setRef(t){ const m = /^(\d{4})-(\d\d)-(\d\d)/.exec(t || ''); if (m) REF_DAY = Date.UTC(+m[1], +m[2]-1, +m[3]); }
function fmtTime(t){            // "2026-10-02 14:30" (exchange-local candle start) -> "Fri 2:30 PM", or "18 Sep, 12:30 PM" if older than 5 days
  if (!t) return '–';
  const dm = /^(\d{4})-(\d\d)-(\d\d)$/.exec(t);
  if (dm) { const d0 = Date.UTC(+dm[1], +dm[2]-1, +dm[3]); return DAYS[new Date(d0).getUTCDay()] + ' ' + (+dm[3]) + ' ' + MONTHS[+dm[2]-1]; }
  const m = /^(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)$/.exec(t); if (!m) return t;
  const day = Date.UTC(+m[1], +m[2]-1, +m[3]), d = new Date(day); let h = +m[4]; const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
  const clock = h + ':' + m[5] + ' ' + ap;
  if (REF_DAY != null && (REF_DAY - day) / 864e5 >= 6) return (+m[3]) + ' ' + MONTHS[+m[2]-1] + (REF_DAY - day > 300 * 864e5 ? ' ' + m[1] : '') + ', ' + clock;
  return DAYS[d.getUTCDay()] + ' ' + clock;
}
function tzShort(tz){ return tz === 'Asia/Kolkata' ? 'IST' : (tz || '').split('/').pop().replace('_',' '); }
function agoText(n){ return n === 0 ? 'latest candle' : n + ' candle' + (n === 1 ? '' : 's') + ' ago'; }
function isLive(s){ return s.scanner_status === 'OK' || s.scanner_status === 'STALE'; }
function signalsIn(stock, win){ return (stock.signals || []).filter(g => g.since <= win); }
function bestOf(sigs){ return sigs.slice().sort((a,b) => b.score - a.score || a.since - b.since)[0] || null; }
function colorVar(d){ return d === 'bear' ? 'bear' : d === 'bull' ? 'bull' : 'amber'; }

function strip(stock, sig, n){
  n = n || 14;
  const cs = (stock.candles || []).slice(-n); if (!cs.length) return '';
  const W = 8, H = 30, hi = Math.max(...cs.map(c => c[2])), lo = Math.min(...cs.map(c => c[3]));
  const y = v => 2 + (hi - v) / (hi - lo || 1) * (H - 4);
  const width = cs.length * W;
  let s = '<svg class="strip" width="' + width + '" height="' + H + '" viewBox="0 0 ' + width + ' ' + H + '" role="img" aria-label="Last ' + cs.length + ' candles">';
  if (sig){
    const end = cs.length - 1 - sig.since, a = end - (sig.len - 1);
    if (end >= 0) { const x0 = Math.max(0, a); s += '<rect x="' + (x0*W) + '" y="0" width="' + ((end - x0 + 1)*W) + '" height="' + H + '" rx="2" fill="var(--' + colorVar(sig.dir) + '-soft)"/>'; }
  }
  cs.forEach((c,i) => {
    const col = c[4] >= c[1] ? 'var(--bull)' : 'var(--bear)', x = i*W + W/2;
    s += '<line x1="' + x + '" x2="' + x + '" y1="' + y(c[2]) + '" y2="' + y(c[3]) + '" stroke="' + col + '"/>';
    s += '<rect x="' + (i*W+1.5) + '" y="' + y(Math.max(c[1],c[4])) + '" width="' + (W-3) + '" height="' + Math.max(1.2, Math.abs(y(c[1]) - y(c[4]))) + '" fill="' + col + '"/>';
  });
  return s + '</svg>';
}

/* theme: follows the device until the person picks one */
(function(){
  const saved = store.get('scanner-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
})();
function bindTheme(btn){
  btn.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next); store.set('scanner-theme', next);
  });
}
/* watchlist lives in this browser only */
function getWatch(){ try { const a = JSON.parse(store.get('scanner-watch') || '[]'); return Array.isArray(a) ? a : []; } catch(e){ return []; } }
function setWatch(a){ store.set('scanner-watch', JSON.stringify(a)); }

/* ---------- confirmation rule for the pattern tables ----------
   Same rule as the SMC + pattern tables (Candlestick Handbook): a candle pattern only counts once a LATER candle has closed
   beyond it. Returns 'confirmed', 'pending', 'failed' or 'trigger'.
   - candlestick patterns: the scanner already decided (g.confirm)
   - Turtle Soup / Undercut & Rally: decided here from the saved candles. Bullish: a later close above the signal candle's
     high confirms it, a later close below its low kills it (bearish is the mirror image). Whichever comes first wins.
   - squeeze / volume / trend / structure events: the close beyond a level IS the trigger, so there is nothing left to confirm */
function patStatus(s, g){
  if (g.confirm) return g.confirm;
  if (g.name === 'Turtle Soup' || g.name === 'Undercut & Rally') {
    const cs = s.candles || [], n = cs.length, end = n - 1 - g.since;
    if (end < 0 || end >= n) return 'pending';
    const bull = g.dir === 'bull';
    for (let i = end + 1; i < n; i++) {
      if (bull ? cs[i][4] > cs[end][2] : cs[i][4] < cs[end][3]) return 'confirmed';
      if (bull ? cs[i][4] < cs[end][3] : cs[i][4] > cs[end][2]) return 'failed';
    }
    return 'pending';
  }
  return 'trigger';
}
