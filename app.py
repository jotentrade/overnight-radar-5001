from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


DEFAULT_BRANCHES = {
    "凱基-台北",
    "美林",
    "摩根大通",
    "港商野村",
    "新加坡商瑞銀",
    "元大-土城永寧",
    "富邦-嘉義",
}


@dataclass(frozen=True)
class Trade:
    stock_id: str
    stock_name: str
    branch: str
    buy: int
    sell: int
    close: float
    change_pct: float
    volume: int
    previous_volume: int = 0
    volume_change_pct: float = 0

    @property
    def net_buy(self) -> int:
        return self.buy - self.sell


def number(value: str, kind=float):
    cleaned = str(value or "0").replace(",", "").replace("%", "").strip()
    if cleaned in {"", "--", "---", "X"}:
        return kind(0)
    return kind(float(cleaned))


def load_trades(path: Path) -> list[Trade]:
    aliases = {
        "stock_id": ("stock_id", "證券代號", "股票代號"),
        "stock_name": ("stock_name", "證券名稱", "股票名稱"),
        "branch": ("branch", "券商分點", "分點"),
        "buy": ("buy", "買進張數", "買進"),
        "sell": ("sell", "賣出張數", "賣出"),
        "close": ("close", "收盤價"),
        "change_pct": ("change_pct", "漲跌幅"),
        "volume": ("volume", "成交量", "成交張數"),
        "previous_volume": ("previous_volume", "前日成交量", "前一日成交量", "昨日成交量"),
        "volume_change_pct": ("volume_change_pct", "成交量變化", "量增幅", "量變化率"),
    }
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        selected = {}
        for target, names in aliases.items():
            selected[target] = next((name for name in names if name in fields), None)
        required = ("stock_id", "stock_name", "branch", "buy", "sell")
        missing = [name for name in required if not selected[name]]
        if missing:
            raise ValueError(f"CSV 缺少必要欄位: {', '.join(missing)}")

        rows = []
        for row in reader:
            get = lambda key: row.get(selected[key], "") if selected[key] else ""
            rows.append(Trade(
                get("stock_id").strip(), get("stock_name").strip(), get("branch").strip(),
                number(get("buy"), int), number(get("sell"), int),
                number(get("close")), number(get("change_pct")), number(get("volume"), int),
                number(get("previous_volume"), int), number(get("volume_change_pct")),
            ))
    return rows


def load_branches(path: Path | None) -> set[str]:
    if not path:
        return DEFAULT_BRANCHES
    return {
        line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def normalize_branch(name: str) -> str:
    return name.replace("-", "").replace("－", "").replace(" ", "").strip()


def analyze(trades: list[Trade], branches: set[str], min_ratio: float, min_change: float, min_volume_change: float = 0):
    stocks: dict[str, dict] = {}
    branch_keys = {normalize_branch(branch) for branch in branches}
    for trade in trades:
        item = stocks.setdefault(trade.stock_id, {
            "stock_id": trade.stock_id, "stock_name": trade.stock_name,
            "close": trade.close, "change_pct": trade.change_pct, "volume": trade.volume,
            "previous_volume": trade.previous_volume, "volume_change_pct": trade.volume_change_pct,
            "known_net_buy": 0, "known_buy": 0, "known_branches": set(),
        })
        if trade.close:
            volume_change = trade.volume_change_pct
            if not volume_change and trade.previous_volume:
                volume_change = (trade.volume - trade.previous_volume) / trade.previous_volume * 100
            item.update(close=trade.close, change_pct=trade.change_pct, volume=trade.volume,
                        previous_volume=trade.previous_volume, volume_change_pct=volume_change)
        if normalize_branch(trade.branch) in branch_keys and trade.net_buy > 0:
            item["known_net_buy"] += trade.net_buy
            item["known_buy"] += trade.buy
            item["known_branches"].add(trade.branch)

    results = []
    for item in stocks.values():
        volume = item["volume"]
        ratio = item["known_net_buy"] / volume * 100 if volume else 0
        if ratio < min_ratio or item["change_pct"] < min_change or item["volume_change_pct"] < min_volume_change:
            continue
        strength = min(max(item["change_pct"], 0) / 9.9, 1) * 50
        concentration = min(ratio / max(min_ratio, 0.1), 2) / 2 * 25
        volume_score = min(max(item["volume_change_pct"], 0) / 100, 1) * 15
        breadth = min(len(item["known_branches"]), 3) / 3 * 10
        item["buy_ratio_pct"] = round(ratio, 2)
        item["volume_change_pct"] = round(item["volume_change_pct"], 2)
        item["score"] = round(strength + concentration + volume_score + breadth, 1)
        item["known_branches"] = "、".join(sorted(item["known_branches"]))
        results.append(item)
    return sorted(results, key=lambda row: (row["score"], row["known_net_buy"]), reverse=True)


def fetch_twse_prices(day: str) -> dict[str, dict]:
    query = urllib.parse.urlencode({"response": "json", "date": day.replace("-", ""), "type": "ALLBUT0999"})
    request = urllib.request.Request(
        f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?{query}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    table = next((table for table in payload.get("tables", []) if "證券代號" in table.get("fields", [])), None)
    if not table:
        raise RuntimeError(payload.get("stat", "證交所未回傳個股行情"))
    indexes = {name: i for i, name in enumerate(table["fields"])}
    prices = {}
    for row in table.get("data", []):
        stock_id = row[indexes["證券代號"]].strip()
        close = number(row[indexes["收盤價"]])
        sign = -1 if "-" in row[indexes["漲跌(+/-)"]] else 1
        change = sign * number(row[indexes["漲跌價差"]])
        previous = close - change
        prices[stock_id] = {
            "stock_name": row[indexes["證券名稱"]].strip(),
            "close": close,
            "change_pct": change / previous * 100 if previous else 0,
            "volume": number(row[indexes["成交股數"]], int) // 1000,
        }
    return prices


def fetch_twse_latest_date() -> str:
    query = urllib.parse.urlencode({"response": "json", "type": "ALLBUT0999"})
    request = urllib.request.Request(
        f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?{query}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    raw = str(payload.get("date", ""))
    if len(raw) != 8 or not raw.isdigit():
        raise RuntimeError("證交所未回傳最新交易日期")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def fetch_twse_prices_with_volume_change(day: str) -> tuple[dict[str, dict], str | None]:
    current = fetch_twse_prices(day)
    cursor = datetime.strptime(day, "%Y-%m-%d").date()
    previous, previous_day = {}, None
    for offset in range(1, 11):
        candidate = (cursor - timedelta(days=offset)).isoformat()
        try:
            previous = fetch_twse_prices(candidate)
        except (OSError, RuntimeError, urllib.error.URLError):
            continue
        if previous:
            previous_day = candidate
            break
    for stock_id, values in current.items():
        prior_volume = previous.get(stock_id, {}).get("volume", 0)
        values["previous_volume"] = prior_volume
        values["volume_change_pct"] = ((values["volume"] - prior_volume) / prior_volume * 100) if prior_volume else 0
    return current, previous_day


def fetch_twse_institutional(day: str) -> dict[str, int]:
    params = {"selectType": "ALL", "response": "json"}
    if day:
        params["date"] = day.replace("-", "")
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"https://www.twse.com.tw/rwd/zh/fund/T86?{query}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("stat") != "OK":
        raise RuntimeError(payload.get("stat", "證交所未回傳法人資料"))
    fields = payload.get("fields", [])
    code_index = fields.index("證券代號")
    total_index = fields.index("三大法人買賣超股數")
    return {row[code_index].strip(): number(row[total_index], int) // 1000
            for row in payload.get("data", []) if len(row) > total_index and row[code_index].strip()}


def analyze_public_signals(prices: dict[str, dict], institutions: dict[str, int],
                           min_ratio: float, min_change: float, min_volume_change: float) -> list[dict]:
    results = []
    for stock_id, values in prices.items():
        if len(stock_id) != 4 or not stock_id.isdigit() or stock_id.startswith("0"):
            continue
        volume = values.get("volume", 0)
        net_buy = institutions.get(stock_id, 0)
        ratio = net_buy / volume * 100 if volume else 0
        if (values.get("change_pct", 0) < min_change or
                values.get("volume_change_pct", 0) < min_volume_change or ratio < min_ratio):
            continue
        price_score = min(max(values["change_pct"], 0) / 9.9, 1) * 50
        volume_score = min(max(values["volume_change_pct"], 0) / 100, 1) * 30
        chip_score = min(max(ratio, 0) / max(min_ratio * 2, 0.1), 1) * 20
        results.append({
            "stock_id": stock_id, "stock_name": values.get("stock_name", ""),
            "close": values.get("close", 0), "change_pct": round(values.get("change_pct", 0), 2),
            "volume": volume, "previous_volume": values.get("previous_volume", 0),
            "volume_change_pct": round(values.get("volume_change_pct", 0), 2),
            "known_net_buy": net_buy, "buy_ratio_pct": round(ratio, 2),
            "known_branches": "三大法人", "score": round(price_score + volume_score + chip_score, 1),
        })
    return sorted(results, key=lambda row: (row["score"], row["known_net_buy"]), reverse=True)


def fetch_finmind_trades(stock_id: str, stock_name: str, day: str, token: str = "") -> list[Trade]:
    query = urllib.parse.urlencode({"data_id": stock_id, "date": day})
    headers = {"User-Agent": "DayTradeRadar/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report?{query}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("msg", str(exc))
        except (ValueError, OSError):
            detail = str(exc)
        if exc.code in {400, 401, 403} and ("level" in detail.lower() or "token" in detail.lower()):
            raise RuntimeError("FinMind 分點資料需要有效的 Sponsor Token") from exc
        raise RuntimeError(f"FinMind 回應錯誤：{detail}") from exc
    if payload.get("status") not in {None, 200}:
        raise RuntimeError(payload.get("msg", "FinMind 查詢失敗"))
    grouped: dict[str, list[int]] = {}
    for row in payload.get("data", []):
        branch = str(row.get("securities_trader", "")).strip()
        totals = grouped.setdefault(branch, [0, 0])
        totals[0] += int(row.get("buy", 0))
        totals[1] += int(row.get("sell", 0))
    return [Trade(stock_id, stock_name, branch, buy // 1000, sell // 1000, 0, 0, 0)
            for branch, (buy, sell) in grouped.items()]


def merge_prices(trades: list[Trade], prices: dict[str, dict]) -> list[Trade]:
    return [Trade(**{**trade.__dict__, **prices.get(trade.stock_id, {})}) for trade in trades]


FIELDS = ["stock_id", "stock_name", "close", "change_pct", "volume", "previous_volume", "volume_change_pct", "known_net_buy", "buy_ratio_pct", "known_branches", "score"]
LABELS = ["股票代號", "股票名稱", "收盤價", "漲跌幅(%)", "成交張數", "前日成交張數", "成交量變化(%)", "隔日沖分點淨買超", "買超比重(%)", "符合分點", "綜合分數"]


def write_reports(rows: list[dict], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    csv_path, html_path = output / "signals.csv", output / "report.html"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writerow(dict(zip(FIELDS, LABELS)))
        writer.writerows({key: row[key] for key in FIELDS} for row in rows)
    trs = "".join("<tr>" + "".join(f"<td>{html.escape(str(row[key]))}</td>" for key in FIELDS) + "</tr>" for row in rows)
    document = f"""<!doctype html><html lang=\"zh-Hant\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>隔日沖偵測報告</title><style>body{{font-family:system-ui;margin:32px;color:#17202a}}table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid #d8dee4;text-align:right}}th:nth-child(1),th:nth-child(2),td:nth-child(1),td:nth-child(2),th:nth-child(8),td:nth-child(8){{text-align:left}}th{{background:#f3f5f7;position:sticky;top:0}}h1{{font-size:24px}}.note{{color:#667085}}</style><h1>隔日沖分點潛在強勢股</h1><p class=\"note\">產生日期：{date.today().isoformat()}｜僅供研究，不構成投資建議。</p><table><thead><tr>{''.join(f'<th>{x}</th>' for x in LABELS)}</tr></thead><tbody>{trs}</tbody></table></html>"""
    html_path.write_text(document, encoding="utf-8")
    return csv_path, html_path


def main() -> int:
    parser = argparse.ArgumentParser(description="隔日沖分點與強勢股票偵測器")
    parser.add_argument("csv", type=Path, help="券商分點買賣超 CSV")
    parser.add_argument("--branches", type=Path, help="自訂分點清單，一行一個")
    parser.add_argument("--date", help="從證交所補行情，格式 YYYY-MM-DD")
    parser.add_argument("--min-ratio", type=float, default=3, help="最低分點淨買超占成交量百分比")
    parser.add_argument("--min-change", type=float, default=7, help="最低漲幅百分比")
    parser.add_argument("--min-volume-change", type=float, default=20, help="最低成交量增幅百分比")
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()
    try:
        trades = load_trades(args.csv)
        if args.date:
            trades = merge_prices(trades, fetch_twse_prices(args.date))
        rows = analyze(trades, load_branches(args.branches), args.min_ratio, args.min_change, args.min_volume_change)
        csv_path, html_path = write_reports(rows, args.output)
        print(f"找到 {len(rows)} 檔，報告：{html_path}，資料：{csv_path}")
        return 0
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
