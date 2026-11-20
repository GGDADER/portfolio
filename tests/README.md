# 投資紀錄開發測試

給下一位維護者。一般使用者直接從主畫面開啟 App 即可。

## v10.1 交接

- 網頁入口是根目錄的 `index.html`。設定畫面的 `gasCode()` 會把 GAS 主體與 `mergeCore` 組成完整程式。本次沿用 GAS v12。
- 修改前的網頁可以從 [v10.0 的固定版本](https://github.com/GGDADER/portfolio/blob/e54d1c7375dd4d3caa06ec4c3f6e8b8f25313410/index.html) 取回。
- 每日首次有交易時留一份本機快照，依裝置日期保留七份。內容含交易、刪除紀錄、報價及歷史。介面可以匯出整份檔案，也可以單獨預覽並補回交易。當日快照保存後即保留原貌。
- GAS v12 會永久記住已刪除的交易編號。明確復原時前端分配替代編號，並接回指定的買入批次及相同時間的先後順序。一般 JSON 匯入只補入預覽中缺少的交易。
- iPhone 的導覽列、選單、提示及視窗使用容器內定位。交付前要實際點過表單欄位、離開輸入框，再點下方按鈕。

## 執行

在專案根目錄使用 Python 3.13 與 Node.js 20 以上。

```sh
python -m pip install playwright==1.58.0
python -m playwright install chromium webkit
node tests/test_gas_compat.mjs
python -m unittest discover -s tests -v
```

Linux 若缺系統套件，將安裝瀏覽器的指令改為 `python -m playwright install --with-deps chromium webkit`。以環境變數 `BROWSER` 與 `VIEWPORT` 選擇瀏覽器和尺寸。手機使用 WebKit 390×844、Chromium 375×667，桌面使用 Chromium 1280×800。例如在 PowerShell 執行下列指令。

```powershell
$env:BROWSER='webkit'
$env:VIEWPORT='390x844'
python -m unittest discover -s tests -v
```

設定 `SCREENSHOT_DIR` 可保存「備份與復原」畫面的截圖。測試使用獨立的瀏覽器空間及虛構交易；所有網路請求都在測試內攔截。GAS 相容性測試直接執行網頁內附的 GAS，以記憶體模擬試算表。正式行情服務、Google 部署權限、iOS 實體鍵盤觸控仍須在對應環境驗證。

## 交付前核對

1. 備份現行網頁與 GAS，核對線上版本。個人帳本應從使用者有資料的裝置匯出。
2. 跑完手機與桌面測試，檢查每個瀏覽器的結果。測試涵蓋台股半張與手續費、FIFO、刪除後重開、復原後同步、每日備份、匯入預覽、慢速同步、損壞的雲端資料及本機寫入失敗。
3. 更新網頁版本號。核對 `gasCode()` 的輸出是否有變動，並告訴使用者是否需要重新部署。
4. 上傳後讀取 GitHub Pages 的正式網址，確認提供的版本號與上傳的檔案一致。
