var SHEET_NAME = 'Data';
var GAS_VERSION = 'v13';

function doGet(e) {
  var action = (e.parameter.action || '').toString();
  if (action === 'ping') return jsonOut({ok:true, msg:'pong', version:GAS_VERSION});
  if (action === 'read') return readData();
  if (action === 'twprice') return getTWPrice(e.parameter.sym);
  if (action === 'ytd') return getYtd(e.parameter.sym, e.parameter.mkt, e.parameter.year);
  if (action === 'quotes') return getQuotes(e.parameter.syms, e.parameter.mkt, e.parameter.night === '1');
  if (action === 'diag') return getDiag(e.parameter.sym, e.parameter.mkt);
  if (action === 'probe') return getProbe(e.parameter.sym);
  return jsonOut({ok:false});
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  try { lock.waitLock(25000); } catch (le) { return jsonOut({ok:false, e:'雲端忙碌中，請稍後再試'}); }
  try {
    var d = JSON.parse(e.postData.contents);
    var sh = dataSheet();
    var R = readColsN(sh), cur = R.s;
    var curTr = pj(cur[0], []);
    if (curTr === null) return jsonOut({ok:false, e:'雲端交易資料無法解析，已中止寫入以免覆蓋'});
    var A = {tr: curTr, pr: pj(cur[1], {}) || {}, hist: pj(cur[2], []) || [], del: pj(cur[3], {}) || {}};
    var B = {tr: pj(d.trades, []) || [], pr: pj(d.prices, {}) || {}, hist: pj(d.history, []) || [], del: pj(d.del, {}) || {}};
    var M = mergeCore(A, B);
    var out = [JSON.stringify(M.tr), JSON.stringify(M.pr), JSON.stringify(M.hist), JSON.stringify(M.del)];
    /* v12: only columns whose content changed are written, and history only when something other
       than today's snapshot changed (or today has no row yet) -- about once a day. Rewriting all
       four columns on every sync is what made the old spreadsheet slow to open. */
    var skip = {};
    if (sameHistoryButToday(A.hist, M.hist)) skip[2] = 1;
    var wrote = writeChanged(sh, out, cur, R.n, skip);
    return jsonOut({ok:true, merged:true, version:GAS_VERSION, wrote:wrote, n:M.tr.length, trades:out[0], prices:out[1], history:out[2], del:out[3]});
  } catch(err) { return jsonOut({ok:false, e:err.message}); }
  finally { lock.releaseLock(); }
}

/* Storage: column A = trades, B = prices, C = history, D = deletions. Each value is split
   into 40k-char chunks down the column (a Sheets cell holds at most 50k), every chunk
   prefixed with ~ so Sheets never reads one as a number, date or formula. The old
   single-cell layout (A1/B1/C1) reads back unchanged. */
function dataSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
}
function pj(v, f) {
  if (v === undefined || v === null || v === '') return f;
  if (typeof v !== 'string') return v;
  try { return JSON.parse(v); } catch (x) { return null; }
}
function readColsN(sh) {
  var out = ['', '', '', ''], n = [0, 0, 0, 0];
  if (!sh) return {s: out, n: n};
  var lr = sh.getLastRow();
  if (lr < 1) return {s: out, n: n};
  var v = sh.getRange(1, 1, lr, 4).getValues();
  for (var c = 0; c < 4; c++) {
    var acc = '';
    for (var r = 0; r < v.length; r++) {
      var x = v[r][c];
      if (x === '' || x === null) break;
      x = String(x);
      if (x.charAt(0) === '~') x = x.substring(1);
      acc += x;
      n[c] = r + 1;
    }
    out[c] = acc;
  }
  return {s: out, n: n};
}
function readCols(sh) { return readColsN(sh).s; }
/* Writes only the columns whose text differs from what is stored, each split into 40k-char
   chunks down its own column (a cell holds at most 50k), every chunk prefixed with ~ so Sheets
   never reads it as a number, date or formula. Rows a shorter column no longer uses are cleared
   in that column only. Nothing changed = nothing written. Returns the letters written, e.g. "AB". */
function writeChanged(sh, cols, old, oldN, skip) {
  var CH = 40000, wrote = '', c, i;
  for (c = 0; c < cols.length; c++) {
    if (skip[c] || String(cols[c]) === String(old[c])) continue;
    var str = String(cols[c]), p = [];
    for (i = 0; i < str.length; i += CH) p.push(['~' + str.substring(i, i + CH)]);
    if (!p.length) p.push(['~']);
    sh.getRange(1, c + 1, p.length, 1).setValues(p);
    if (oldN[c] > p.length) sh.getRange(p.length + 1, c + 1, oldN[c] - p.length, 1).clearContent();
    wrote += 'ABCD'.charAt(c);
  }
  if (wrote) SpreadsheetApp.flush();
  return wrote;
}
/* true when the two histories differ at most in today's entry AND the stored one already has
   today -- then the history column is left alone until something else in it changes. */
function sameHistoryButToday(stored, merged) {
  var today = new Date().toISOString().slice(0, 10), a = {}, b = {}, has = false, i, k;
  for (i = 0; i < stored.length; i++) { var x = stored[i]; if (!x || !x.d) continue; if (x.d === today) has = true; else a[x.d] = JSON.stringify(x); }
  if (!has) return false;
  for (i = 0; i < merged.length; i++) { var y = merged[i]; if (y && y.d && y.d !== today) b[y.d] = JSON.stringify(y); }
  for (k in a) if (a[k] !== b[k]) return false;
  for (k in b) if (a[k] !== b[k]) return false;
  return true;
}

function readData() {
  try {
    var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
    if (!sh) return jsonOut({ok:true, empty:true, version:GAS_VERSION, trades:'[]', prices:'{}', history:'[]', del:'{}'});
    var c = readCols(sh);
    return jsonOut({ok:true, version:GAS_VERSION, trades:c[0] || '[]', prices:c[1] || '{}', history:c[2] || '[]', del:c[3] || '{}'});
  } catch(err) { return jsonOut({ok:false,e:err.message}); }
}

function getTWPrice(sym) {
  try {
    var url = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_' + sym + '.tw';
    var r = JSON.parse(UrlFetchApp.fetch(url).getContentText());
    var d = r.msgArray && r.msgArray[0];
    var p = d ? parseFloat(d.z) || parseFloat(d.y) : 0;
    var y = d ? parseFloat(d.y) : 0;
    return jsonOut({ok:true,price:p,yesterday:y});
  } catch(err) { return jsonOut({ok:false,price:0}); }
}

/* YTD baseline = previous year's LAST trading day CLOSE price.
   v6: removed Stooq (was hanging GAS via UrlFetchApp). Now Yahoo first, TWSE backup.
   Each branch wrapped in independent try-catch so one slow source can't kill the others. */
function getYtd(sym, mkt, year) {
  var y = parseInt(year) || (new Date()).getFullYear();
  var prevY = y - 1;
  var tried = [];

  // ---------- 1. Yahoo Finance (works for both US and TW with .TW suffix) ----------
  try {
    var yhSym = (mkt === 'tw' && sym.indexOf('.') < 0) ? (sym + '.TW') : sym;
    var p1 = Math.floor(Date.UTC(prevY, 11, 20) / 1000);
    var p2 = Math.floor(Date.UTC(y, 0, 5) / 1000);
    var yurl = 'https://query1.finance.yahoo.com/v8/finance/chart/' + encodeURIComponent(yhSym)
      + '?period1=' + p1 + '&period2=' + p2 + '&interval=1d';
    var opts = {
      muteHttpExceptions: true,
      headers: {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    };
    var yresp = UrlFetchApp.fetch(yurl, opts);
    var ycode = yresp.getResponseCode();
    if (ycode === 200) {
      var body = JSON.parse(yresp.getContentText());
      var result = body && body.chart && body.chart.result && body.chart.result[0];
      var ts = result && result.timestamp;
      var quote = result && result.indicators && result.indicators.quote && result.indicators.quote[0];
      var closes = quote && quote.close;
      var cutoff = Math.floor(Date.UTC(y, 0, 1) / 1000);
      var bestPrice = 0, bestTs = 0;
      if (ts && closes) {
        for (var i = 0; i < ts.length; i++) {
          if (ts[i] < cutoff && closes[i] != null && closes[i] > 0) {
            if (ts[i] > bestTs) { bestTs = ts[i]; bestPrice = closes[i]; }
          }
        }
      }
      if (bestPrice > 0) {
        var dateStr = new Date(bestTs * 1000).toISOString().substring(0, 10);
        return jsonOut({ok:true, price:bestPrice, source:'yahoo', baselineDate:dateStr, ticker:yhSym, tried:tried});
      }
      tried.push({src:'yahoo', reason:'no close before Jan 1'});
    } else {
      tried.push({src:'yahoo', reason:'http '+ycode});
    }
  } catch(err) { tried.push({src:'yahoo', reason:'err: '+err.message}); }

  // ---------- 2. TWSE STOCK_DAY (TW only) ----------
  if (mkt === 'tw') {
    try {
      var turl = 'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=' + prevY + '1201&stockNo=' + sym;
      var tresp = UrlFetchApp.fetch(turl, {muteHttpExceptions:true});
      var tcode = tresp.getResponseCode();
      if (tcode === 200) {
        var r = JSON.parse(tresp.getContentText());
        if (r.stat === 'OK' && r.data && r.data.length) {
          var lastRow = r.data[r.data.length - 1];
          var closeP = parseFloat(String(lastRow[6]).replace(/,/g,''));
          if (closeP > 0) return jsonOut({ok:true, price:closeP, source:'twse', baselineDate:String(lastRow[0]), ticker:sym, tried:tried});
        }
        tried.push({src:'twse', reason:'no data: '+(r.stat||'unknown')});
      } else {
        tried.push({src:'twse', reason:'http '+tcode});
      }
    } catch(err) { tried.push({src:'twse', reason:'err: '+err.message}); }
  }

  return jsonOut({ok:false, price:0, tried:tried});
}

/* Batch live quotes incl. pre-market / post-market, via Yahoo chart API.
   fetchAll() runs every symbol in parallel so one client request covers the whole portfolio. */
function getQuotes(syms, mkt, night) {
  try {
    var list = String(syms || '').split(',');
    var reqs = [], names = [];
    var ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
    for (var i = 0; i < list.length; i++) {
      var s = String(list[i]).replace(/\s/g, '');
      if (!s) continue;
      var yhSym = (mkt === 'tw' && s.indexOf('.') < 0) ? (s + '.TW') : s;
      reqs.push({
        url: 'https://query1.finance.yahoo.com/v8/finance/chart/' + encodeURIComponent(yhSym) + '?includePrePost=true&interval=1m&range=1d',
        muteHttpExceptions: true,
        headers: {'User-Agent': ua}
      });
      names.push(s);
    }
    if (!reqs.length) return jsonOut({ok:true, q:{}});
    var resps = UrlFetchApp.fetchAll(reqs);
    var out = {};
    for (var j = 0; j < resps.length; j++) {
      try {
        if (resps[j].getResponseCode() !== 200) continue;
        var parsed = parseChart(resps[j].getContentText());
        if (parsed) out[names[j]] = parsed;
      } catch (e2) {}
    }
    if (night) addNightPrices(names, out);
    return jsonOut({ok:true, q:out});
  } catch(err) { return jsonOut({ok:false, e:err.message}); }
}

/* Only the explicitly timestamped overnight trade is eligible. A missing
   overnight field leaves the chart result intact. Credentials here are Yahoo's
   anonymous visitor session, shared with the existing Yahoo diagnostic. */
function addNightPrices(names, out) {
  var now = new Date(), time = Utilities.formatDate(now, 'America/New_York', 'HH:mm:ss').split(':');
  var hours = +time[0], seconds = hours * 3600 + (+time[1]) * 60 + (+time[2]);
  var weekday = +Utilities.formatDate(now, 'America/New_York', 'u');
  if (!(hours >= 20 && (weekday === 7 || weekday <= 4)) && !(hours < 4 && weekday <= 5)) return;
  var start = Math.floor(now.getTime() / 1000) - (seconds + 4 * 3600) % 86400;
  var eligible = names.filter(function(s){return /^[A-Z][A-Z0-9.-]*$/.test(s);});
  if (!eligible.length) return;
  try {
    var auth = yahooAuth();
    if (!auth) return;
    for (var i = 0; i < eligible.length; i += 75) {
      var group = eligible.slice(i, i + 75);
      var url = 'https://query1.finance.yahoo.com/v7/finance/quote?overnightPrice=true&symbols='
        + encodeURIComponent(group.join(',')) + '&crumb=' + encodeURIComponent(auth.crumb);
      var response = UrlFetchApp.fetch(url, {muteHttpExceptions:true,headers:{'User-Agent':YH_UA,'Cookie':auth.cookie}});
      if (response.getResponseCode() === 401 || response.getResponseCode() === 403) {
        CacheService.getScriptCache().removeAll(['yh_crumb','yh_cookie']);
        return;
      }
      if (response.getResponseCode() !== 200) continue;
      var data = JSON.parse(response.getContentText()).quoteResponse;
      (data && data.result || []).forEach(function(q) {
        var price = +q.overnightMarketPrice, ts = +q.overnightMarketTime, now = Date.now();
        if (group.indexOf(q.symbol) < 0 || !(price > 0 && isFinite(price) && ts > 0) ||
            ts < start || ts * 1000 > now) return;
        var old = out[q.symbol];
        if (old && old.ts > ts) return;
        out[q.symbol] = {p:price,ts:ts,sess:'overnight',src:'y-night',
          prev:q.regularMarketPrice || null,rp:q.regularMarketPrice || null,delay:+q.exchangeDataDelayedBy || 0};
      });
    }
  } catch (err) { /* Fall back to chart quotes if the optional feed is unavailable. */ }
}

function parseChart(txt) {
  var body = JSON.parse(txt);
  var r = body && body.chart && body.chart.result && body.chart.result[0];
  if (!r) return null;
  var meta = r.meta || {};
  var ts = r.timestamp || [];
  var q = r.indicators && r.indicators.quote && r.indicators.quote[0];
  var closes = (q && q.close) || [];
  var last = 0, lastTs = 0;
  for (var i = closes.length - 1; i >= 0; i--) {
    if (closes[i] != null && closes[i] > 0) { last = closes[i]; lastTs = ts[i] || 0; break; }
  }
  var reg = meta.regularMarketPrice || 0;
  var regTs = meta.regularMarketTime || 0;
  var prev = meta.chartPreviousClose || meta.previousClose || 0;
  var sess = 'closed';
  var cp = meta.currentTradingPeriod || {};
  var nowS = Math.floor(new Date().getTime() / 1000);
  if (cp.pre && nowS >= cp.pre.start && nowS < cp.pre.end) sess = 'pre';
  else if (cp.regular && nowS >= cp.regular.start && nowS < cp.regular.end) sess = 'regular';
  else if (cp.post && nowS >= cp.post.start && nowS < cp.post.end) sess = 'post';
  else if (cp.pre && cp.post) {
    /* Outside pre/regular/post = the 8pm-4am ET overnight window (Blue Ocean ATS).
       Only call it live if the newest bar is genuinely recent; otherwise it is just
       a stale post-market close and we must not pretend it is an overnight quote. */
    sess = 'closed';
  }

  /* v7 FRESHNESS FIX -- this was the bug that made the app show 15-minute-old prices
     while the header claimed the data was live. Yahoo's 1-minute bar array lags
     meta.regularMarketPrice, routinely by 10-15 minutes, and the old code always read
     the last bar. meta is the freshest field in the payload, so prefer it whenever its
     timestamp is at least as new as the newest bar. Never do this in pre/post/overnight:
     there meta.regularMarketPrice is the STALE regular close while the bars carry the
     live extended-hours print, so overriding would make things worse, not better. */
  var src = 'bar';
  if (sess === 'regular' && reg > 0 && regTs >= lastTs) { last = reg; lastTs = regTs; src = 'meta'; }
  if (!last) { last = reg; lastTs = regTs; src = 'meta'; }
  if (!last) return null;
  return {p:last, rp:reg, prev:prev, sess:sess, ts:lastTs, src:src, age:(nowS - lastTs)};
}

/* Diagnostic: shows exactly what Yahoo returns for one symbol, with the newest bars
   labelled in both US Eastern and Taipei time. Use it to confirm whether overnight
   (Blue Ocean) bars are present: ?action=diag&sym=TSLA */
function getDiag(sym, mkt) {
  try {
    var yhSym = (mkt === 'tw' && sym.indexOf('.') < 0) ? (sym + '.TW') : sym;
    var url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + encodeURIComponent(yhSym) + '?includePrePost=true&interval=1m&range=1d';
    var ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
    var resp = UrlFetchApp.fetch(url, {muteHttpExceptions:true, headers:{'User-Agent': ua}});
    var code = resp.getResponseCode();
    if (code !== 200) return jsonOut({ok:false, http:code});
    var txt = resp.getContentText();
    var body = JSON.parse(txt);
    var r = body && body.chart && body.chart.result && body.chart.result[0];
    if (!r) return jsonOut({ok:false, e:'no result'});
    var meta = r.meta || {}, ts = r.timestamp || [];
    var q = r.indicators && r.indicators.quote && r.indicators.quote[0];
    var closes = (q && q.close) || [];
    var tail = [];
    for (var i = closes.length - 1; i >= 0 && tail.length < 10; i--) {
      if (closes[i] == null) continue;
      var d = new Date(ts[i] * 1000);
      tail.push({
        et: Utilities.formatDate(d, 'America/New_York', 'MM-dd HH:mm'),
        tw: Utilities.formatDate(d, 'Asia/Taipei', 'MM-dd HH:mm'),
        p: closes[i]
      });
    }
    var cp = meta.currentTradingPeriod || {};
    function per(x) {
      if (!x) return '-';
      return Utilities.formatDate(new Date(x.start*1000), 'Asia/Taipei', 'MM-dd HH:mm')
        + ' ~ ' + Utilities.formatDate(new Date(x.end*1000), 'Asia/Taipei', 'MM-dd HH:mm') + ' (TW)';
    }
    var now = new Date();
    return jsonOut({
      ok: true,
      symbol: yhSym,
      nowTW: Utilities.formatDate(now, 'Asia/Taipei', 'MM-dd HH:mm'),
      nowET: Utilities.formatDate(now, 'America/New_York', 'MM-dd HH:mm'),
      regularMarketPrice: meta.regularMarketPrice,
      chartPreviousClose: meta.chartPreviousClose,
      periods: {pre: per(cp.pre), regular: per(cp.regular), post: per(cp.post)},
      totalBars: closes.length,
      newestBars: tail,
      parsed: parseChart(txt)
    });
  } catch(err) { return jsonOut({ok:false, e:err.message}); }
}

/* --- Yahoo v7 quote probe ---
   The chart endpoint carries no Blue Ocean overnight bars, but yahoo.com itself shows an
   Overnight price, so the data may sit on the quote endpoint instead. That endpoint needs
   a cookie + crumb. This probe fetches it and dumps the field names so we can see whether an
   overnight field actually exists, before wiring anything into the live price path.
   Usage: ?action=probe&sym=TSLA */
var YH_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

function yahooAuth() {
  var cache = CacheService.getScriptCache();
  var cr = cache.get('yh_crumb'), ck = cache.get('yh_cookie');
  if (cr && ck) return {crumb: cr, cookie: ck};
  var r1 = UrlFetchApp.fetch('https://fc.yahoo.com', {muteHttpExceptions:true, followRedirects:false, headers:{'User-Agent': YH_UA}});
  var h = r1.getAllHeaders();
  var sc = h['Set-Cookie'] || h['set-cookie'];
  if (!sc) return null;
  if (Array.isArray(sc)) sc = sc[0];
  var cookie = String(sc).split(';')[0];
  var r2 = UrlFetchApp.fetch('https://query1.finance.yahoo.com/v1/test/getcrumb', {muteHttpExceptions:true, headers:{'User-Agent': YH_UA, 'Cookie': cookie}});
  if (r2.getResponseCode() !== 200) return null;
  var crumb = r2.getContentText().replace(/\s/g, '');
  if (!crumb || crumb.length > 40) return null;
  cache.put('yh_crumb', crumb, 3600);
  cache.put('yh_cookie', cookie, 3600);
  return {crumb: crumb, cookie: cookie};
}

function getProbe(sym) {
  try {
    sym = sym || 'TSLA';
    var a = yahooAuth();
    if (!a) return jsonOut({ok:false, step:'auth', e:'could not obtain cookie/crumb'});
    var url = 'https://query1.finance.yahoo.com/v7/finance/quote?overnightPrice=true&symbols=' + encodeURIComponent(sym) + '&crumb=' + encodeURIComponent(a.crumb);
    var r = UrlFetchApp.fetch(url, {muteHttpExceptions:true, headers:{'User-Agent': YH_UA, 'Cookie': a.cookie}});
    var code = r.getResponseCode();
    var txt = r.getContentText();
    if (code !== 200) return jsonOut({ok:false, step:'quote', http:code, body:txt.substring(0, 400)});
    var body = JSON.parse(txt);
    var q = body && body.quoteResponse && body.quoteResponse.result && body.quoteResponse.result[0];
    if (!q) return jsonOut({ok:false, step:'parse', body:txt.substring(0, 400)});
    var keys = [], matched = {};
    for (var k in q) {
      keys.push(k);
      if (/night|pre|post|extend/i.test(k)) matched[k] = q[k];
    }
    keys.sort();
    var now = new Date();
    return jsonOut({
      ok: true,
      symbol: sym,
      nowTW: Utilities.formatDate(now, 'Asia/Taipei', 'MM-dd HH:mm'),
      nowET: Utilities.formatDate(now, 'America/New_York', 'MM-dd HH:mm'),
      marketState: q.marketState,
      regularMarketPrice: q.regularMarketPrice,
      regularMarketPreviousClose: q.regularMarketPreviousClose,
      sessionFields: matched,
      allKeys: keys
    });
  } catch(err) { return jsonOut({ok:false, e:err.message}); }
}

function jsonOut(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function mergeCore(a,b){
  a=a||{};b=b||{};
  var del={},k,i,m={},order=[],da=a.del||{},db=b.del||{};
  for(k in da){if(da.hasOwnProperty(k))del[k]=+da[k]||1}
  for(k in db){if(db.hasOwnProperty(k)){var x=+db[k]||1;if(!del[k]||x>del[k])del[k]=x}}
  function put(t,inc){if(!t||!t.id||del[t.id])return;var c=m[t.id];if(!c){m[t.id]=t;order.push(t.id);return}
    var cu=+c.u||0,tu=+t.u||0;if(tu>cu||(tu===cu&&inc))m[t.id]=t}
  var ta=a.tr||[],tb=b.tr||[];
  for(i=0;i<ta.length;i++)put(ta[i],false);
  for(i=0;i<tb.length;i++)put(tb[i],true);
  var tr=[];for(i=0;i<order.length;i++)tr.push(m[order[i]]);
  var pr={},pa=a.pr||{},pb=b.pr||{};
  for(k in pa){if(pa.hasOwnProperty(k)&&pa[k])pr[k]=pa[k]}
  for(k in pb){if(pb.hasOwnProperty(k)&&pb[k]){var o=pr[k];if(!o||!o.at||!pb[k].at||pb[k].at>=o.at)pr[k]=pb[k]}}
  var hm={},hk=[],ha=a.hist||[],hb=b.hist||[],e;
  for(i=0;i<ha.length;i++){e=ha[i];if(e&&e.d){if(!hm[e.d])hk.push(e.d);hm[e.d]=e}}
  for(i=0;i<hb.length;i++){e=hb[i];if(e&&e.d){if(!hm[e.d])hk.push(e.d);hm[e.d]=e}}
  hk.sort();if(hk.length>400)hk=hk.slice(hk.length-400);
  var hist=[];for(i=0;i<hk.length;i++)hist.push(hm[hk[i]]);
  return{tr:tr,del:del,pr:pr,hist:hist}}
