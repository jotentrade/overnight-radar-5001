# 隔日沖分點強勢股偵測器（PWA）

零第三方套件的 Python 原型，將券商分點買賣超 CSV 與行情條件合併，輸出排序後的 CSV 與 HTML 報告。

## 啟動 Web APP

```powershell
python server.py
```

瀏覽器開啟 `http://127.0.0.1:5001`。介面直接使用證交所公開行情、成交量及三大法人資料，不需要手動匯入；支援條件調整、結果匯出及 PWA 安裝。

「抓取證交所資料」不需要 Token：取得當日與前一交易日行情、量能及三大法人買賣超，再用價格強度 50%、量能放大 30%、法人籌碼 20% 評分。券商分點資料仍可透過 CSV 匯入作為另一種研究模式。

## 執行

```powershell
python app.py sample_trades.csv --branches branches.txt
```

開啟 `output/report.html` 查看結果。若輸入 CSV 沒有行情欄位，可指定交易日，從證交所公開介面補上市股票收盤行情：

```powershell
python app.py your_trades.csv --branches branches.txt --date 2026-07-10
```

可調整條件：

```powershell
python app.py your_trades.csv --min-ratio 5 --min-change 8.5
```

## CSV 欄位

必要欄位：`股票代號`、`股票名稱`、`券商分點`、`買進張數`、`賣出張數`。若要使用量能條件，另提供 `前日成交量`，或在 Web APP 查詢證交所行情自動補齊。

若不使用 `--date`，還需提供：`收盤價`、`漲跌幅`、`成交張數`。同時也支援英文欄名：`stock_id`、`stock_name`、`branch`、`buy`、`sell`、`close`、`change_pct`、`volume`。

## 判定方式

- 指定分點的淨買超必須為正。
- 預設淨買超占全市場成交張數至少 3%。
- 預設當日漲幅至少 7%。
- 預設成交量較前一交易日增加至少 20%。
- 綜合分數由股價強度 50%、買超集中度 25%、量能放大 15%、符合分點數 10% 組成。

分點名單與規則只是策略參數，並不保證分點身分或後續報酬。本工具僅供研究，不構成投資建議。
