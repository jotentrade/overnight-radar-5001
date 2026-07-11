from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import (Trade, analyze, analyze_public_signals, fetch_twse_institutional,
                 fetch_twse_latest_date, fetch_twse_prices_with_volume_change)

ROOT = Path(__file__).parent.resolve()
STATIC = ROOT / "static"


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self.send_json({"status": "ok"})
        if parsed.path == "/api/twse":
            day = parse_qs(parsed.query).get("date", [""])[0]
            if not day:
                return self.send_json({"error": "缺少 date 參數"}, 400)
            try:
                prices, previous_day = fetch_twse_prices_with_volume_change(day)
                return self.send_json({"prices": prices, "previous_date": previous_day})
            except Exception as exc:
                return self.send_json({"error": f"行情查詢失敗：{exc}"}, 502)
        relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
        target = ROOT / relative if relative == "sample_trades.csv" else (STATIC / relative).resolve()
        if not target.is_file() or target.parent not in {STATIC, ROOT}:
            self.send_error(404)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if target.name == "index.html" else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/online-scan":
            return self.online_scan()
        if self.path != "/api/analyze":
            return self.send_error(404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            trades = [Trade(**row) for row in payload.get("trades", [])]
            rows = analyze(trades, set(payload.get("branches", [])), float(payload.get("min_ratio", 3)),
                           float(payload.get("min_change", 7)), float(payload.get("min_volume_change", 20)))
            self.send_json({"results": rows, "total": len(rows)})
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": f"資料格式錯誤：{exc}"}, 400)

    def online_scan(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            requested_day = payload["date"]
            day = requested_day
            min_ratio = float(payload.get("min_ratio", 3))
            min_change = float(payload.get("min_change", 7))
            min_volume_change = float(payload.get("min_volume_change", 20))
            fallback = False
            try:
                prices, previous_day = fetch_twse_prices_with_volume_change(day)
                institutions = fetch_twse_institutional(day)
            except Exception:
                day = fetch_twse_latest_date()
                fallback = day != requested_day
                prices, previous_day = fetch_twse_prices_with_volume_change(day)
                institutions = fetch_twse_institutional("")
            rows = analyze_public_signals(prices, institutions, min_ratio, min_change, min_volume_change)
            candidates = sum(1 for values in prices.values()
                             if values["change_pct"] >= min_change and values["volume_change_pct"] >= min_volume_change)
            self.send_json({"results": rows, "total": len(rows), "candidate_count": candidates,
                            "date": day, "requested_date": requested_day, "fallback": fallback,
                            "previous_date": previous_day, "source": "TWSE 三大法人"})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": f"查詢參數錯誤：{exc}"}, 400)
        except Exception as exc:
            self.send_json({"error": f"網路資料查詢失敗：{exc}"}, 502)

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    print("隔日沖雷達已啟動：http://127.0.0.1:5001")
    ThreadingHTTPServer(("127.0.0.1", 5001), Handler).serve_forever()
