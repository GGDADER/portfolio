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
        p.evaluate("setTimeout(clearCfg,0)")
        expect(p.get_by_text("首次設定", exact=True)).to_be_visible()
        self.assertEqual(p.evaluate("localStorage.getItem('other-app')"), "keep")
        self.assertIsNone(p.evaluate("localStorage.getItem(TK)"))


if __name__ == "__main__":
    unittest.main()
