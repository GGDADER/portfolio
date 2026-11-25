"""Offline browser regression tests. Run with python -m unittest discover -s tests -v."""
import json
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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

    def test_tiered_stock_fees_and_japan_annual_fees(self):
        p = self.page
        p.evaluate("sv('pt_feed',{USD:0.0035,TWD:0})")
        p.locator(".fab").click()
        rate, fee = p.locator("#f_fr"), p.locator("#f_fe")
        expect(rate).to_have_value("")
        expect(rate).to_have_attribute("placeholder", "按股估算")
        p.locator("#f_s").fill("FTEST")
        p.locator("#f_p").fill("100")
        p.locator("#f_q").fill("100")
        expect(fee).to_have_value("0.37")
        expect(p.locator("#fnet")).to_have_text("$10,000.37")
        p.get_by_role("button", name="賣出", exact=True).click()
        expect(p.locator("#fnet")).to_have_text("$9,999.42")
        p.get_by_role("button", name="買入", exact=True).click()
        if os.getenv("SCREENSHOT_DIR"):
            out = Path(os.environ["SCREENSHOT_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            p.locator("#fFeeChips").evaluate("el=>el.scrollIntoView({block:'center'})")
            p.screenshot(path=out / f"tiered-fees-{self.browser.browser_type.name}-{p.viewport_size['width']}.png")
        p.locator("#sbtn").click()
        expect(p.locator("#tradeResults")).to_contain_text("預估手續費 $0.37")
        self.assertEqual(p.evaluate("calc().op.find(x=>x.s==='FTEST').c"), 10000.37)
        p.locator(".fab").click()
        p.locator("#f_s").fill("7203.T")
        expect(rate).to_have_value("0.05302")
        expect(p.locator("#fFeeLabel")).to_have_text("整筆金額（JPY）")
        expect(p.locator("#fFeeNote")).to_contain_text("換匯費另計，年均 0.54216%")
        p.locator("#f_p").fill("1000")
        p.locator("#f_q").fill("100")
        expect(fee).to_have_value("53.02")
        expect(p.locator("#fnet")).to_have_text("¥100,053.02")
        p.locator("#sbtn").click()
        self.assertEqual(p.evaluate("S.tr.at(-1).feeEstimateRate"), 0.05302)
        self.assertAlmostEqual(p.evaluate("calc().op.find(x=>x.s==='7203.T').c"), 100053.02)
        p.evaluate("quickTrade('7203.T','sell')")
        expect(rate).to_have_value("0.05302")
        p.locator("#fcT").click()
        expect(rate).to_have_value("")
        expect(p.locator("#fFeeChips")).not_to_contain_text("IBKR")

    def test_annual_fee_preferences_and_statement_amount(self):
        p = self.page
        p.evaluate("sv('pt_feed',{USD:0.08})")
        p.locator(".fab").click()
        rate, fee = p.locator("#f_fr"), p.locator("#f_fe")
        expect(rate).to_have_value("0.08")
        p.locator("#f_s").fill("FTEST")
        p.locator("#f_p").fill("100")
        p.locator("#f_q").fill("100")
        p.get_by_role("button", name="IBKR 按股估算", exact=True).click()
        fee.fill("0.3725")
        p.locator("#f_q").fill("3")
        expect(fee).to_have_value("0.3725")
        p.locator("#sbtn").click()
        self.assertEqual(p.evaluate("[S.tr.at(-1).fee,S.tr.at(-1).feeEstimateRate??null]"), [0.3725, None])
        p.wait_for_function("!S._syT && !S.syncing && !S.pBusy")
        p.reload()
        p.locator(".fab").click()
        expect(rate).to_have_attribute("placeholder", "按股估算")
        p.get_by_role("button", name="0%", exact=True).click()
        p.locator("#f_s").fill("FTEST")
        p.locator("#f_q").fill("1")
        p.locator("#f_p").fill("10")
        p.locator("#sbtn").click()
        p.locator(".fab").click()
        expect(rate).to_have_value("")
        self.assertEqual(p.evaluate("defFee('USD','stock')"), 0)
        p.evaluate("""S.tr.push({id:'coin',symbol:'FCOIN',type:'crypto',cur:'USD',action:'buy',
            price:10,qty:1,date:'2026-01-01'});quickTrade('FCOIN','buy')""")
        expect(p.locator("#fFeeChips")).not_to_contain_text("IBKR")
        self.assertEqual(p.evaluate("F.type"), "crypto")
        p.evaluate("""const prior=S.tr.find(t=>t.id==='lot1');S.ed={...prior};initF(S.ed);set({v:'form'})""")
        expect(fee).to_have_value("2")
        p.locator("#f_d").fill("2026-01-02")
        expect(fee).to_have_value("2")
        p.locator("#sbtn").click()
        self.assertEqual(p.evaluate("findTr('lot1').fee"), 2)

    def test_option_fees_follow_premium_and_manual_override(self):
        p = self.page
        p.locator(".fab").click()
        p.get_by_role("button", name="IBKR 期權", exact=True).click()
        for name, value in [("root","FTEST"),("expiry","2026-12-18"),("strike","50"),("qty","3"),
                            ("price","2"),("date","2026-11-16")]:
            p.locator("#opt_"+name).fill(value)
        fee = p.locator("#opt_fee")
        expect(fee).to_have_value("2.04")
        expect(p.locator("#opt_preview")).to_have_text("淨收 US$597.96")
        if os.getenv("SCREENSHOT_DIR"):
            out = Path(os.environ["SCREENSHOT_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            fee.evaluate("el=>el.scrollIntoView({block:'center'})")
            p.screenshot(path=out / f"option-fees-{self.browser.browser_type.name}-{p.viewport_size['width']}.png")
        p.locator("#opt_strike").fill("55")
        expect(fee).to_have_value("2.04")
        p.locator("#opt_mult").fill("10")
        expect(fee).to_have_value("2.03")
        p.locator("#opt_mult").fill("100")
        fee.fill("0")
        p.locator("#opt_qty").fill("4")
        expect(fee).to_have_value("0")
        p.get_by_role("button", name="套用 IBKR 按口估算").click()
        expect(fee).to_have_value("2.72")
        p.get_by_role("button", name="儲存期權交易").click()
        expect(p.locator("#tradeResults")).to_contain_text("預估手續費 US$2.72")
        self.assertTrue(p.evaluate("S.tr.at(-1).feeModel===IBKR_TIERED"))
        p.get_by_role("button", name="編輯期權").click()
        fee.fill("1.2345")
        p.locator("#opt_price").fill("3")
        expect(fee).to_have_value("1.2345")
        p.get_by_role("button", name="儲存期權交易").click()
        self.assertEqual(p.evaluate("S.tr.at(-1).fee"), 1.2345)
        p.get_by_role("button", name="編輯期權").click()
        expect(fee).to_have_value("1.2345")
        p.locator("[name=mode]").select_option("XS")
        expect(fee).to_have_value("0")
        expect(p.locator("#opt_fee_note")).to_contain_text("到期歸零")

    def test_tiered_boundaries_fractional_and_venue(self):
        p = self.page
        stock = p.evaluate("""() => {
          const cost=(price,qty,day='2026-11-15')=>ibkrOrderFee('stock','buy',price,qty,1,day);
          return [cost(18,2000),cost(.02,10),cost(10,.5),cost(1,.2),cost(18,2000,'2026-09-01'),
                  cost(18,2000.2),defFee('USD','stock')===IBKR_TIERED]
        }""")
        self.assertEqual(stock, [7.41, 0, .05, .01, 7, 7.41, True])
        options = p.evaluate("""() => {
          const cost=(price,qty,venue='')=>ibkrOrderFee('option','buy',price,qty,100,'2026-11-15',venue);
          return [cost(.049,10),cost(.05,10),cost(.099,10),cost(.10,10),cost(.01,1),
                  cost(.06,10,'MERCURY'),cost(.06,10,'BOX')]
        }""")
        self.assertEqual(options, [2.75, 5.25, 5.25, 6.75, 1.03, 5.36, 5.47])
        p.evaluate("openOptionForm(null,'BTO')")
        p.locator("#opt_qty").fill("10")
        p.locator("#opt_price").fill("0.06")
        p.locator("[name=venue]").select_option("MERCURY")
        expect(p.locator("#opt_fee")).to_have_value("5.36")
        p.locator("#opt_fee").fill("1.88712")
        p.locator("#opt_qty").fill("20")
        expect(p.locator("#opt_fee")).to_have_value("1.88712")
        p.locator("#opt_fee_reset").click()
        expect(p.locator("#opt_fee")).to_have_value("10.73")

    def test_legacy_fee_and_stock_field_swap(self):
        p = self.page
        p.wait_for_function("S.synced && !S.syncing && !S.pBusy && !S._syT")
        p.evaluate("""const t={...findTr('lot1'),symbol:'SWAPTEST',id:'legacy',date:localDate(),tm:'00:10',
            price:800,qty:12.3,fr:0.00496,feeEstimateRate:0.00496,fee:.48};
            storeTrades(S.tr.concat(t));S.ed={...t};S.pr.SWAPTEST={p:12.32,at:Date.now()};sv(PK,S.pr);
            initF(S.ed);set({v:'form'})""")
        expect(p.locator("#f_fe")).to_have_value("0.48")
        expect(p.locator("#fFeeNote")).to_contain_text("平均")
        p.get_by_role("button", name="交換單價和股數").click()
        expect(p.locator("#f_p")).to_have_value("12.3")
        expect(p.locator("#f_q")).to_have_value("800")
        expect(p.get_by_role("button", name="交換單價和股數")).to_have_count(0)
        p.get_by_role("button", name="IBKR 按股估算").click()
        expect(p.locator("#f_fe")).to_have_value("2.96")
        p.locator("#sbtn").click()
        self.assertEqual(p.evaluate("[findTr('legacy').qty,findTr('legacy').fee,findTr('legacy').feeEstimateRate??null]"),
                         [800, 2.96, None])

    def test_statement_preview_apply_reload_and_undo(self):
        p = self.page
        p.evaluate("""const option={id:'os',type:'option',symbol:'OPT:FTEST:2026-12-18:C:24:100',
            action:'sell',effect:'open',qty:4,price:.2,fee:1,date:'2026-11-12',tm:'22:10',cur:'USD',seq:1};
            const close={...option,id:'oc',seq:2,action:'buy',effect:'close',qty:3,price:.074,fee:.35,
                         feeEstimateRate:1.58661,date:'2026-11-17',tm:'22:09'};
            const stock={...findTr('lot1'),id:'s',date:'2026-11-17',tm:'22:06',qty:6.8,price:400,
                         fd:'margin',fee:.13,fr:.00496,feeEstimateRate:.00496};
            storeTrades(S.tr.concat([option,close,stock]));set({v:'trades'})""")
        p.get_by_role("button", name="IBKR 成交對帳").click()
        lines = ("2026-11-17,22:05,DEMO,BUY,400,6.801,1.51746\n"
                 "2026-11-17,22:04,FTEST261218C00024000,BTC,3,0.074,1.6052")
        p.locator("#recon_text").fill(lines)
        p.locator("#recon_text").blur()
        p.get_by_role("button", name="預覽對帳").click()
        expect(p.locator("#recon_result")).to_contain_text("400 股 × US$6.801")
        self.assertEqual(p.evaluate("findTr('s').qty"), 6.8)
        if os.getenv("SCREENSHOT_DIR"):
            out = Path(os.environ["SCREENSHOT_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            p.get_by_role("button", name="套用 2 筆並備份原紀錄").scroll_into_view_if_needed()
            p.screenshot(path=out / f"fee-reconcile-{self.browser.browser_type.name}-{p.viewport_size['width']}.png")
        self.assertLessEqual(p.evaluate("document.documentElement.scrollWidth"), p.viewport_size["width"])
        p.get_by_role("button", name="套用 2 筆並備份原紀錄").click()
        expect(p.locator("#recon_result")).to_contain_text("已按成交單更新 2 筆")
        self.assertEqual(p.evaluate("""[findTr('s').qty,findTr('s').price,trFee(findTr('s')),findTr('s').fd,
            findTr('s').fr??null,findTr('oc').fee,findTr('oc').tm,findTr('oc').seq,
            optionCashText('buy',.074,3,100,trFee(findTr('oc')))]"""),
                         [400, 6.801, 1.51746, 'margin', None, 1.6052, '22:04', 2, '淨付 US$23.81'])
        p.wait_for_function("!S._syT && !S.syncing")
        p.reload()
        p.locator(".nb").filter(has_text="交易").click()
        expect(p.locator("#tradeResults")).to_contain_text("實付手續費 US$1.61")
        p.get_by_role("button", name="IBKR 成交對帳").click()
        p.locator("#recon_text").fill(lines)
        p.get_by_role("button", name="預覽對帳").click()
        expect(p.get_by_text("已與成交單一致。")).to_have_count(2)
        p.get_by_role("button", name="復原上次對帳").click()
        self.assertEqual(p.evaluate("[findTr('s').qty,findTr('s').price,findTr('s').feeEstimateRate,findTr('oc').fee]"),
                         [6.8, 400, .00496, .35])

    def test_recon_rejects_ambiguous_matches_and_changed_records(self):
        p = self.page
        p.evaluate("""storeTrades(S.tr.map(t=>({...t,tm:'12:00'})));openRecon()""")
        line = "2026-01-01,12:00,DEMO,BUY,2,10,0"
        for data, message in [(line + "\n" + line, "多行對到同一筆"), (line.replace("12:00", "12:15"), "找不到"),
                              (line.replace("10,0", "10,"), "格式有誤")]:
            p.locator("#recon_text").fill(data)
            p.get_by_role("button", name="預覽對帳").click()
            expect(p.locator("#recon_result")).to_contain_text(message)
            expect(p.get_by_role("button", name="套用", exact=False)).to_have_count(0)
        p.locator("#recon_text").fill(line)
        p.get_by_role("button", name="預覽對帳").click()
        p.evaluate("storeTrades(S.tr.concat({...findTr('lot1'),id:'duplicate'}))")
        p.get_by_role("button", name="套用 1 筆並備份原紀錄").click()
        expect(p.locator("#recon_result")).to_contain_text("符合多筆")
        self.assertEqual(p.evaluate("trFee(findTr('lot1'))"), 2)
        p.evaluate("storeTrades(S.tr.filter(t=>t.id!=='duplicate'))")
        p.get_by_role("button", name="預覽對帳").click()
        p.get_by_role("button", name="套用 1 筆並備份原紀錄").click()
        self.assertEqual(p.evaluate("[findTr('lot1').feeActual,trFee(findTr('lot1'))]"), [True, 0])
        p.evaluate("storeTrades(S.tr.map(t=>t.id==='lot1'?{...t,qty:5,u:t.u+1}:t))")
        p.get_by_role("button", name="復原上次對帳").click()
        expect(p.locator("#recon_result")).to_contain_text("已有交易被編輯")
        self.assertEqual(p.evaluate("findTr('lot1').qty"), 5)

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

    def test_option_only_auto_quote_on_open_and_manual_refresh(self):
        p = self.page
        key = "OPT:FTEST:2026-11-27:C:18:100"
        ticker = "FTEST261127C00018000"
        trade = dict(id="short", symbol=key, type="option", cur="USD", broker="IBKR",
                     effect="open", action="sell", date="2026-11-17", price=0.75, qty=4)
        # Seed the new document before its first refresh. A response from the
        # previous document must never be part of this option-only fixture.
        p.wait_for_function("S.synced && !S.syncing && !S.pBusy && !S._syT")
        p.add_init_script("localStorage.setItem('pt_t9',JSON.stringify(%s));localStorage.setItem('pt_p9','{}')" % json.dumps([trade]))
        quote_time = p.evaluate("Math.floor(Date.now()/1000)-960")
        quotes = {ticker: dict(p=0.31, ts=quote_time, src="meta", sess="regular")}
        queries = []

        def market(route):
            query = parse_qs(urlsplit(route.request.url).query)
            queries.append(query)
            route.fulfill(json=dict(ok=True, q=quotes))

        p.route("**/gas?action=quotes&**", market)
        p.reload()
        row = p.locator(".opt-row")
        expect(row).to_contain_text("Yahoo 市場成交")
        expect(row).to_contain_text("約延遲 15 分鐘")
        expect(row).to_contain_text("US$0.31")
        self.assertTrue(queries)
        self.assertTrue(all(q["syms"] == [ticker] and "night" not in q for q in queries))
        self.assertEqual(p.evaluate("s=>S.pr[s].at", key), quote_time * 1000)
        self.assertAlmostEqual(p.evaluate("calc().options.value"), -124)
        self.assertAlmostEqual(p.evaluate("calc().options.unrealized"), 176)
        row.click()
        expect(p.locator("#app")).to_contain_text("11/20")
        if os.getenv("SCREENSHOT_DIR"):
            out = Path(os.environ["SCREENSHOT_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            p.screenshot(path=out / f"option-quote-{self.browser.browser_type.name}-{p.viewport_size['width']}.png")
        self.assertTrue(p.evaluate("document.documentElement.scrollWidth<=innerWidth"))
        update = p.get_by_role("button", name="更新期權報價")
        quotes.clear()
        update.click()
        expect(p.get_by_text("行情來源暫無這張合約的成交資料，沿用上方估值。")).to_be_visible()
        self.assertEqual(p.evaluate("s=>ld(PK,{})[s].at", key), quote_time * 1000)
        p.locator("input[name=mark]").fill("0.29")
        p.get_by_role("button", name="儲存權利金現價").click()
        quotes[ticker] = dict(p=0.31, ts=quote_time, src="meta")
        update.click()
        expect(p.locator("#app")).to_contain_text("沿用上方估值")
        self.assertEqual(p.evaluate("s=>S.pr[s].src", key), "option-manual")
        p.clock.set_fixed_time("2026-11-20T02:00:00+08:00")
        quotes[ticker] = dict(p=0.21, ts=p.evaluate("Math.floor(Date.now()/1000)-901"), src="meta")
        update.click()
        p.wait_for_function("!S.pBusy && calc().options.value===-84")
        self.assertAlmostEqual(p.evaluate("calc().options.unrealized"), 216)
        self.assertEqual(p.evaluate("calc().options.groups.values().next().value.realized"), 0)

    def test_option_poll_updates_again_without_a_tap(self):
        p = self.page
        p.clock.install(time="2026-11-20T01:23:45+08:00")
        p.clock.pause_at("2026-11-20T01:24:00+08:00")
        p.evaluate("""() => {
            const open={id:'a-call',symbol:'OPT:TSLL:2026-11-27:C:10:100',type:'option',cur:'USD',
              action:'sell',effect:'open',price:0.41,qty:1,date:'2026-11-17'};
            storeTrades(S.tr.concat(open,{...open,id:'a-put',symbol:'OPT:TSLL:2026-11-27:P:10:100'}));
        }""")
        ticker = "TSLL261127C00010000"
        stamp = p.evaluate("Math.floor(Date.now()/1000)")
        call = dict(p=0.30, ts=stamp - 900, src="meta")
        stock = dict(p=25, ts=stamp, src="meta", sess="regular")
        queries = []

        def market(route):
            queries.append(parse_qs(urlsplit(route.request.url).query)["syms"][0].split(","))
            route.fulfill(json=dict(ok=True, q={ticker: call, "DEMO": stock}))

        p.route("**/gas?action=quotes&**", market)
        p.reload()
        p.clock.fast_forward(2100)
        row = p.locator(".opt-row").filter(has_text="Call")
        expect(row).to_contain_text("US$0.3")
        call.update(p=0.20, ts=stamp-840)
        stock.update(p=26, ts=stamp+60)
        p.clock.fast_forward(61000)
        expect(row).to_contain_text("US$0.2")
        self.assertEqual(p.evaluate("gp('DEMO')"), 26)
        expect(p.locator(".opt-row").filter(has_text="Put")).to_contain_text("依你的成交價估算")
        self.assertGreaterEqual(sum({ticker,"TSLL261127P00010000","DEMO"} <= set(q) for q in queries), 2)

    def test_option_contract_filter_and_night_requests(self):
        result = self.page.evaluate("""async () => {
          const now=Date.parse('2026-11-20T01:00:00+08:00'); // Still Nov 19 in New York.
          const base='OPT:TSLL:2026-11-19:C:10:100',put='OPT:TSLL:2026-11-27:P:10.125:100';
          const keys=[base,put,'OPT:TSLL:2026-11-18:C:10:100','OPT:TSLL:2026-11-19:C:10:10',
            'OPT:TSLL:2026-11-19:C:10.0004:100','OPT:TSLL:2026-11-19:C:100000:100',
            'OPT:BRK.B:2026-11-19:C:10:100','OPT:TSLL:2026-11-27:C:11:100'];
          const book={groups:new Map(keys.map((s,i)=>[s,{s,contract:optionContract(s),qty:i===7?0:-1}]))};
          const eligible=optionQuoteRequests(book,now,false,{}),calls=[];
          batchQuotes=async (syms,night)=>{calls.push([syms,!!night]);return {}};
          await portfolioQuotes(['TSLL'],eligible,true);
          return [eligible.map(x=>x.ticker),calls,
            optionQuoteRequests(book,now,true,{[base]:now-59000,[put]:now-61000}).map(x=>x.symbol),
            optionQuoteSymbol(optionContract('OPT:TSLL1:2027-01-15:P:12.5:100'))];
        }""")
        symbols = ["TSLL261119C00010000", "TSLL261127P00010125"]
        self.assertEqual(result, [symbols, [[["TSLL"], True], [symbols, False]],
                                  ["OPT:TSLL:2026-11-27:P:10.125:100"], "TSLL1270115P00012500"])

    def test_option_market_time_wins_over_download_order(self):
        result = self.page.evaluate("""async () => {
          const s='OPT:TSLL:2026-11-27:C:10:100',ticker='TSLL261127C00010000';
          const t=Math.floor(Date.now()/1000);
          const open={id:'o',symbol:s,type:'option',cur:'USD',action:'sell',effect:'open',
            date:'2026-11-17',qty:1,price:0.41};
          S.tr=[open,...S.tr];const items=[{symbol:s,ticker}];
          const check=q=>{let np={...S.pr};const count=applyOptionQuotes(items,{[ticker]:q},np);
            S.pr=np;return [count,S.pr[s]?.p??null]};
          const good={p:0.31,ts:t-1200,src:'meta'};
          const rejected=[{...good,p:0},{...good,ts:t+1},{...good,src:'y-night'},
            {...good,ts:null},{...good,ts:1}].map(check);
          const first=check(good);
          let resume;batchQuotes=async()=>({[ticker]:{...good,p:0.22,ts:t-900}});
          fetchRate=async()=>{};fetchOne=()=>new Promise(resolve=>{resume=resolve});
          fetchPrices(false);
          for(let i=0;!resume&&i<100;i++)await new Promise(resolve=>setTimeout(resolve,10));
          if(!resume)throw Error('Stock fallback did not start');
          S.pr[s]={p:0.28,src:'option-manual',at:t*1000};resume({p:23,t});
          for(let i=0;S.pBusy&&i<100;i++)await new Promise(resolve=>setTimeout(resolve,10));
          if(S.pBusy)throw Error('Quote refresh did not finish');
          const after=[S.pr[s].p,S.pr[s].src,S.pr.DEMO.p,S.optionQuoteStatus[s]];
          S.tr.push({...open,id:'c',action:'buy',effect:'close',date:'2026-11-18'});
          const closed=check(good);
          return [rejected,first,after,closed];
        }""")
        self.assertEqual(result, [[[0, None]] * 5, [1, 0.31], [0.28, "option-manual", 23, "older"], [0, 0.28]])

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
