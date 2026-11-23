"""Offline browser regression tests. Run with python -m unittest discover -s tests -v."""
import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://portfolio.test"
BOOK = [
    dict(id="lot1", symbol="DEMO", action="buy", price=10, qty=2, fee=2,
         date="2026-01-01", cur="USD", type="stock"),
    dict(id="lot2", symbol="DEMO", action="buy", price=20, qty=2,
         date="2026-01-01", cur="USD", type="stock"),
    dict(id="sale", symbol="DEMO", action="sell", price=30, qty=1, fee=1,
         date="2026-01-02", cur="USD", type="stock", lots=["lot2"]),
]


class PortfolioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = getattr(cls.pw, os.getenv("BROWSER", "chromium")).launch()
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        width, height = map(int, os.getenv("VIEWPORT", "390x844").split("x"))
        self.ctx = self.browser.new_context(viewport=dict(width=width, height=height),
                                            is_mobile=width < 700, has_touch=width < 700,
                                            timezone_id="Asia/Taipei", locale="zh-TW")
        self.addCleanup(self.ctx.close)
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda err: self.errors.append(str(err)))
        self.page.set_default_timeout(6000)
        self.page.clock.set_fixed_time("2026-11-20T01:23:45+08:00")
        self.ctx.route("**/*", self.route)
        seed = {"pt_fk": "test-only", "pt_gs": ORIGIN + "/gas", "pt_t9": BOOK,
                "pt_p9": {"DEMO": {"p": 22, "pc": 21}}, "pt_ytd_2026": {"DEMO": 10}}
        self.page.add_init_script("""if(!sessionStorage.seeded){
            for(const [k,v] of Object.entries(%s))localStorage.setItem(k,JSON.stringify(v));
            sessionStorage.seeded=1;
        }""" % json.dumps(seed))
        self.page.goto(ORIGIN + "/")

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.assertNotIn("Error:", self.page.locator("#app").inner_text())

    def route(self, route):
        url = urlsplit(route.request.url)
        if url.hostname == "portfolio.test" and url.path == "/":
            return route.fulfill(content_type="text/html; charset=utf-8", body=self.html)
        if url.path == "/gas" and route.request.method == "POST":
            return route.fulfill(json=dict(ok=True, merged=True, **route.request.post_data_json))
        if url.path == "/gas" and "action=read" in url.query:
            return route.fulfill(json={"ok": True, "version": "v12", "trades": "[]"})
        return route.fulfill(json={"ok": True, "q": {}})

    def menu(self, text):
        self.page.get_by_title("選單").click()
        self.page.locator(".dd-item").filter(has_text=text).click()

    def test_mobile_trade_edit_delete_reopen_undo(self):
        p = self.page
        p.locator(".fab").click()
        p.locator("#f_s").fill("2330")
        p.locator("#f_p").fill("100")
        p.locator("#f_q").fill("0.5")
        p.locator("#f_fe").fill("20")
        p.locator("#f_fe").blur()
        p.locator("#sbtn").click()
        row = p.locator(".card").filter(has_text="2330").last
        row.get_by_role("button", name="✏️").click()
        p.locator("#f_p").fill("110")
        p.locator("#sbtn").click()
        self.assertAlmostEqual(p.evaluate("calc().op.find(x=>x.s==='2330').c"), 55022)
        row.get_by_role("button", name="🗑").click()
        p.locator(".modal").get_by_role("button", name="刪除", exact=True).click()
        old = p.evaluate("Object.keys(ld(DK,{}))[0]")
        p.wait_for_function("!S._syT && !S.syncing && !S.pBusy")
        p.reload()
        self.menu("備份與復原")
        p.get_by_role("button", name="檢視並復原").click()
        p.get_by_role("button", name="補入 1 筆").click()
        self.assertEqual(p.evaluate("S.tr.filter(t=>t.symbol==='2330').length"), 1)
        self.assertNotEqual(p.evaluate("S.tr.find(t=>t.symbol==='2330').id"), old)
        expect(p.get_by_text("目前沒有可復原的刪除紀錄。")).to_be_visible()

    def test_whole_position_after_sync_and_fifo(self):
        p = self.page
        p.locator(".hold").filter(has_text="DEMO").first.click()
        p.get_by_role("button", name="刪除持倉").click()
        p.get_by_role("button", name="刪除整檔交易").click()
        p.evaluate("syncNow()")
        p.wait_for_function("!S.syncing")
        self.menu("備份與復原")
        p.get_by_role("button", name="檢視並復原").click()
        p.get_by_role("button", name="補入 3 筆").click()
        self.assertEqual(p.evaluate("calc().cl[0].pnl"), 9)
        self.assertEqual(p.evaluate("calc().op[0].c"), 42)
        self.assertEqual(p.evaluate("calc().cl[0].lm"), None)
        p.evaluate("S.tr.reverse(); S.tr.find(t=>t.action==='sell').lots=[]")
        self.assertEqual(p.evaluate("calc().cl[0].pnl"), 18)

    def test_trade_search(self):
        p = self.page
        p.evaluate("""storeTrades(S.tr.concat({...S.tr[0],id:'tw',symbol:'2330',cur:'TWD'}));render()""")
        p.locator(".hold").filter(has_text="DEMO").first.click()
        link = p.get_by_role("link", name="查看 TradingView 夜盤")
        expect(link).to_have_count(0)
        p.evaluate("set({v:'home',det:null})")
        p.locator(".hold").filter(has_text="2330").first.click()
        expect(link).to_have_count(0)
        p.evaluate("set({v:'home',det:null})")
        p.locator(".nb").filter(has_text="交易").click()
        search = p.get_by_role("searchbox", name="搜尋交易代號")
        symbols = p.locator("#tradeSymbol")
        rows = p.locator("#tradeResults .card")
        symbols.select_option("2330")
        expect(rows).to_have_count(1)
        search.press_sequentially("em")
        expect(search).to_be_focused()
        expect(rows).to_have_count(3)
        expect(symbols).to_have_value("all")
        search.fill(" zz ")
        expect(p.get_by_text("目前條件找不到交易")).to_be_visible()
        search.fill("")
        expect(rows).to_have_count(4)
        search.fill("33")
        expect(rows).to_have_count(1)
        p.locator("select").first.select_option("today")
        expect(rows).to_have_count(0)
        expect(search).to_have_value("33")
        symbols.select_option("DEMO")
        expect(search).to_have_value("")

    def test_japan_trade_price_currency_and_fifo(self):
        p = self.page
        p.evaluate("S.jpyRate=150; S.rate=30")
        p.locator(".fab").click()
        p.get_by_role("button", name="日股").click()
        p.locator("#f_s").fill("7203")
        p.locator("#f_p").fill("3000")
        p.locator("#f_q").fill("100")
        p.locator("#f_fe").fill("150")
        expect(p.locator("#ftot")).to_contain_text("¥300,000")
        p.locator("#sbtn").click()
        self.assertEqual(p.evaluate("calc().op.find(x=>x.cur==='JPY').c"), 300150)
        self.assertEqual(p.evaluate("S.tr.at(-1).symbol"), "7203.T")
        self.assertTrue(p.evaluate("validImportTrade(S.tr.at(-1)) && isJP('130A.T')"))
        p.evaluate("""storeTrades(S.tr.concat({...S.tr.at(-1),symbol:'7203',id:'tw2',cur:'TWD'},
            {...S.tr.at(-1),action:'sell',qty:50,price:3100,fee:80,fr:0,id:'jsale'}));
            S.pr['7203.T']={p:3200,pc:3000};snapHistory();""")
        self.assertEqual(p.evaluate("calc().cl.find(x=>x.id==='jsale').pnl"), 4845)
        self.assertEqual(p.evaluate("calc().op.find(x=>x.s==='7203.T').q"), 50)
        self.assertEqual(p.evaluate("toDisp(15000,'JPY')"), 100)
        self.assertAlmostEqual(p.evaluate("ld(HK,[]).at(-1).u"), 3200*50/150+66)
        self.assertEqual(p.evaluate("S.cur='TWD';toDisp(15000,'JPY')"), 3000)
        p.evaluate("quickTrade('7203.T','sell')")
        expect(p.locator("#fHint")).to_contain_text("可賣 50")
        expect(p.locator("#fPL")).to_contain_text("¥")

    def test_overnight_price_updates_position(self):
        p = self.page
        p.clock.set_fixed_time("2026-11-24T10:00:00+08:00")
        p.wait_for_function("!S.pBusy")
        p.evaluate("""batchQuotes=async (syms,night)=>{
            if(!night)throw Error('night session missing');
            return {DEMO:{p:32,prev:25,rp:25,ts:Date.now()/1000-120,src:'y-night',sess:'overnight'}};
        };fetchPrices(false)""")
        p.wait_for_function("!S.pBusy && gp('DEMO')===32")
        expect(p.locator(".hold").filter(has_text="DEMO").first).to_contain_text("夜盤")
        p.locator(".hold").filter(has_text="DEMO").first.click()
        expect(p.get_by_text("Yahoo 夜盤成交", exact=True)).to_be_visible()
        expect(p.get_by_text("$96.00", exact=True).first).to_be_visible()
        p.evaluate("""batchQuotes=async()=>({DEMO:{p:20,ts:Date.now()/1000-4000,sess:'closed'}});
            fetchPrices(false)""")
        p.wait_for_function("!S.pBusy")
        self.assertEqual(p.evaluate("gp('DEMO')"),32)
        self.assertEqual(p.evaluate("overnightStart(Date.parse('2026-11-21T02:00:00Z'))"),0)

    def test_ibkr_short_call_entry_mark_buyback_and_statement(self):
        p = self.page
        p.locator(".fab").click()
        p.get_by_role("button", name="IBKR 期權", exact=True).click()
        expect(p.locator("[name=mode]")).to_have_value("STO")
        expect(p.locator("[name=right]")).to_have_value("C")
        p.locator("#opt_root").fill("demo")
        p.locator("#opt_expiry").fill("2026-12-18")
        p.locator("#opt_strike").fill("25")
        p.locator("#opt_qty").fill("2")
        p.locator("#opt_price").fill("1.5")
        p.locator("#opt_fee").fill("2")
        p.locator("#opt_date").fill("2026-11-17")
        expect(p.locator("#opt_preview")).to_have_text("淨收 US$298.00")
        if os.getenv("SCREENSHOT_DIR"):
            out = Path(os.environ["SCREENSHOT_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            p.locator("#opt_root").scroll_into_view_if_needed()
            p.screenshot(path=out / f"options-{self.browser.browser_type.name}-{p.viewport_size['width']}.png")
        self.assertTrue(p.evaluate("""[...document.querySelectorAll('#optionForm input')].every(e=>{
            const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1})"""))
        p.get_by_role("button", name="儲存期權交易").click()
        expect(p.locator("#tradeResults")).to_contain_text("賣出開倉")
        self.assertEqual(p.evaluate("calc().op.map(x=>[x.s,x.q])"), [["DEMO", 3]])
        self.assertEqual(p.evaluate("calc().options.unrealized"), -2)
        self.assertEqual(p.evaluate("healthReport().iss.filter(x=>x.lv===0).length"), 0)
        p.locator(".nb").filter(has_text="總覽").click()
        p.locator(".opt-row").click()
        p.locator("input[name=mark]").fill("0.90")
        p.get_by_role("button", name="儲存權利金現價").click()
        self.assertEqual(p.evaluate("calc().options.unrealized"), 118)
        p.get_by_role("button", name="買回平倉", exact=True).click()
        expect(p.locator("[name=mode]")).to_have_value("BTC")
        expect(p.locator("#opt_qty")).to_have_value("2")
        p.locator("#opt_qty").fill("1")
        p.locator("#opt_price").fill("0.50")
        p.locator("#opt_fee").fill("1")
        expect(p.locator("#opt_preview")).to_have_text("淨付 US$51.00")
        p.get_by_role("button", name="儲存期權交易").click()
        self.assertEqual(p.evaluate("""() => {const b=calc().options,g=[...b.groups.values()][0];
            return [g.qty,g.basis,g.value,g.unrealized,g.realized,g.cash]}"""), [-1, -149, -90, 59, 98, 247])
        self.assertEqual(p.evaluate("ld(HK,[]).at(-1).u"), 66 - 90)
        self.assertEqual(p.evaluate("calc().op[0].q"), 3)
        p.locator(".nb").filter(has_text="平倉").click()
        p.locator(".hold").filter(has_text="Call").click()
        expect(p.locator("#app")).to_contain_text("US$98.00")
        self.assertEqual(p.evaluate("parseImport(JSON.stringify(backupData())).tr.length"), 5)
        p.evaluate("syncNow()")
        p.wait_for_function("!S.syncing && !S._syT")
        p.reload()
        expect(p.locator(".opt-row")).to_contain_text("賣方")
        self.assertEqual(p.evaluate("calc().options.closed[0].pnl"), 98)

    def test_option_fifo_contract_identity_fees_and_expiry(self):
        p = self.page
        result = p.evaluate("""() => {
          const symbol='OPT:DEMO1:2026-11-20:P:25.5:10';
          const base={type:'option',symbol,action:'buy',effect:'open',cur:'USD',date:'2026-11-10',id:'a',price:2,qty:2,fee:2};
          const b={...base,id:'b',date:'2026-11-11',price:4,qty:2};
          const sale={...b,id:'c',date:'2026-11-18',action:'sell',effect:'close',price:5,qty:3,fee:3};
          const expire={...sale,id:'d',date:'2026-11-20',effect:'expire',qty:1,price:0,fee:0};
          const other=['OPT:DEMO1:2026-11-20:C:25.5:10','OPT:DEMO1:2026-11-20:P:26:10',
            'OPT:DEMO1:2026-11-27:P:25.5:10','OPT:DEMO1:2026-11-20:P:25.5:100'].map(
            (s,i)=>({...base,symbol:s,id:'ind'+i,action:'sell',qty:1,fee:0}));
          const rows=[sale,b,expire,base,...other],book=optionBook(rows,{});
          return [book.groups.size,book.groups.get(symbol).qty,book.closed.map(c=>c.pnl),
            book.closed[0].matches.map(m=>m.qty),book.issues.size,
            parseImport(JSON.stringify(rows)).tr.length,
            validImportTrade({...expire,effect:'open'}),validImportTrade({...base,qty:0.5}),
            optionBook([sale],{}).issues.has('c'),optionBook([base,{...b,action:'sell'}],{}).issues.has('b')];
        }""")
        self.assertEqual(result, [5, 0, [64, -41], [2, 1], 0, 8, False, False, True, True])

    def test_option_edit_delete_recover_and_reject_excess_close(self):
        p = self.page
        p.evaluate("""storeTrades(S.tr.concat({type:'option',id:'opt-open',broker:'IBKR',action:'sell',
            effect:'open',symbol:'OPT:DEMO:2026-11-19:C:25:100',cur:'USD',date:'2026-11-10',qty:2,price:1,fee:2}));
            set({v:'trades'})""")
        p.get_by_role("button", name="編輯期權").click()
        p.locator("#opt_price").fill("1.25")
        p.get_by_role("button", name="儲存期權交易").click()
        p.get_by_role("button", name="刪除期權").click()
        p.locator(".modal").get_by_role("button", name="刪除", exact=True).click()
        p.locator(".nb").filter(has_text="總覽").click()
        self.menu("備份與復原")
        p.get_by_role("button", name="檢視並復原").click()
        expect(p.locator(".modal")).to_contain_text("賣出開倉")
        p.get_by_role("button", name="補入 1 筆").click()
        p.get_by_role("button", name="返回總覽").click()
        p.locator(".opt-row").click()
        p.get_by_role("button", name="登記到期歸零").click()
        p.locator("#opt_qty").fill("3")
        seen = []
        def dismiss(dialog):
            seen.append(dialog.message)
            dialog.dismiss()
        p.once("dialog", dismiss)
        p.get_by_role("button", name="儲存期權交易").click()
        self.assertIn("平倉口數超過", seen[0])
        p.locator("#opt_qty").fill("2")
        p.get_by_role("button", name="儲存期權交易").click()
        self.assertEqual(p.evaluate("calc().options.closed[0].pnl"), 248)
        self.assertEqual(p.evaluate("calc().op[0].q"), 3)
        expect(p.locator("#tradeResults")).to_contain_text("到期歸零")

    def test_import_only_previewed_rows(self):
        p = self.page
        self.menu("匯入合併")
        src = {"trades": [dict(BOOK[0], price=999, u=9999999999999),
                          dict(BOOK[0], id="new", symbol="TEST")], "del": {"sale": 1}}
        p.locator("#imp_file").set_input_files(dict(
            name="sample.json", mimeType="application/json", buffer=json.dumps(src).encode()))
        p.get_by_role("button", name="補入 1 筆").click()
        self.assertEqual(p.evaluate("[findTr('lot1').price,findTr('sale').qty,S.tr.length]"), [10, 1, 4])
        self.assertEqual(p.evaluate("ld(DK,{})"), {})

    def test_daily_retention_recovery_and_export(self):
        p = self.page
        self.assertEqual(p.evaluate("ld(backupDays()[0],{}).trades.length"), 3)
        p.evaluate("""for(let i=1;i<=8;i++){let day='2026-11-'+String(i).padStart(2,'0');
            sv(DAY_PREFIX+day,backupData())}S._backupDay=null;backupToday();
            delTrades(['lot2']);delTrades(['lot1'])""")
        self.assertEqual(len(p.evaluate("backupDays()")), 7)
        self.menu("備份與復原")
        p.get_by_role("button", name="2026-11-20", exact=True).click()
        p.get_by_role("button", name="補入 2 筆").click()
        self.assertEqual(p.evaluate("calc().cl[0].pnl"), 9)
        p.get_by_role("button", name="2026-11-20", exact=True).click()
        expect(p.get_by_role("button", name="補入 0 筆")).to_be_disabled()
        p.get_by_role("button", name="取消").click()
        p.evaluate("Object.defineProperty(navigator,'canShare',{value:()=>false})")
        with p.expect_download() as result:
            p.get_by_role("button", name="匯出 2026-11-20", exact=True).click()
        payload = json.loads(result.value.path().read_text(encoding="utf-8"))
        self.assertEqual(payload["trades"], BOOK)
        self.assertNotIn("pt_fk", payload)
        self.assertIn("prices", payload)

    def test_pending_sync_keeps_concurrent_changes(self):
        p = self.page
        p.wait_for_function("!S.syncing")
        p.evaluate("""window.sent=[];window.pending=null;
            gasFetch=async function(url,opts){
              if(opts.method!=='POST')return {ok:true,trades:'[]',version:'v12'};
              const data=JSON.parse(opts.body); sent.push(data);
              if(sent.length===1) await new Promise(resolve=>window.pending=resolve);
              return Object.assign({ok:true,merged:true},data);
            };syncNow()""")
        p.wait_for_function("typeof pending==='function'")
        p.evaluate("""storeTrades(S.tr.concat(Object.assign({},S.tr[0],{id:'late'})));
            fixTradeDate('sale','2026-01-03');delTrades(['lot1']);pending()""")
        p.wait_for_function("!S.syncing")
        self.assertEqual(p.evaluate("[!!findTr('late'),!!findTr('lot1'),findTr('sale').date]"),
                         [True, False, "2026-01-03"])
        p.wait_for_function("sent.length>=2")
        self.assertIn("lot1", p.evaluate("JSON.parse(sent[1].del)"))
        self.assertIn("late", p.evaluate("JSON.parse(sent[1].trades).map(t=>t.id)"))

    def test_phone_layout_local_day_and_private_mode(self):
        p = self.page
        p.evaluate("snapHistory()")
        self.assertEqual(p.evaluate("ld(HK,[]).slice(-1)[0].d"), "2026-11-20")
        p.get_by_title("隱藏金額").click()
        expect(p.locator(".blur").first).to_be_visible()
        self.menu("資料健檢")
        expect(p.get_by_text("逐檔對帳", exact=False)).to_be_visible()
        p.evaluate("set({v:'home'})")
        self.menu("備份與復原")
        if os.getenv("SCREENSHOT_DIR"):
            out = Path(os.environ["SCREENSHOT_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            p.screenshot(path=out / f"recovery-{self.browser.browser_type.name}-{p.viewport_size['width']}.png")
        self.assertTrue(p.evaluate("""[...document.querySelectorAll('.bak-row button,.hdr button')].every(b=>{
            const r=b.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.width>0
        })"""))
        self.assertFalse(p.evaluate("[...document.querySelectorAll('#app *')].some(e=>getComputedStyle(e).position==='fixed')"))
        p.get_by_role("button",name="返回總覽").click()
        p.locator(".fab").click()
        p.locator("#f_s").fill("DEMO")
        p.locator("#f_q").focus()
        p.locator("#f_q").blur()
        p.get_by_role("button", name="取消", exact=True).click()
        expect(p.get_by_title("選單")).to_be_visible()

    def test_bad_cloud_data_leaves_ledger_intact(self):
        p = self.page
        p.wait_for_function("!S.syncing")
        p.evaluate("""window.writes=0;gasFetch=async function(url,opts){
            if(opts.method==='POST')writes++;
            return {ok:true,trades:'[]',del:'unreadable',version:'v12'};
        };syncNow()""")
        p.wait_for_function("!S.syncing")
        self.assertEqual(p.evaluate("[S.syncErr.kind,writes,S.tr.length,ld(TK,[]).length]"),
                         ["data", 0, 3, 3])

    def test_storage_failure_and_clear_scope(self):
        p = self.page
        self.assertTrue(p.evaluate("""() => {
            const write=Storage.prototype.setItem;const before=JSON.stringify(S.tr);
            Storage.prototype.setItem=function(k,v){if(k===TK)throw Error();write.call(this,k,v)};
            const ok=storeTrades([]);Storage.prototype.setItem=write;
            return !ok&&JSON.stringify(S.tr)===before&&JSON.stringify(ld(TK,[]))===before;
        }"""))
        p.evaluate("localStorage.setItem('other-app','keep')")
        p.once("dialog", lambda d: d.accept())
        with p.expect_event("load"):
            p.evaluate("setTimeout(clearCfg,0)")
        expect(p.get_by_text("首次設定", exact=True)).to_be_visible()
        self.assertEqual(p.evaluate("localStorage.getItem('other-app')"), "keep")
        self.assertIsNone(p.evaluate("localStorage.getItem(TK)"))


if __name__ == "__main__":
    unittest.main()
