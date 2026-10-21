"""本地 Web 面板服务：OKX 行情日报 + 购物监控状态。

使用标准库 http.server 实现，无额外依赖：
- GET  /                     面板页面
- GET  /api/overview         总览数据（规则 / 抓取记录 / 行情缓存 / 进程状态）
- GET  /api/history          单个商品的价格历史
- POST /api/crypto/refresh   立即抓取行情并刷新缓存（不发送）
- POST /api/crypto/send      立即抓取行情并发送一次钉钉日报
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import resolve_path
from ..crypto.job import CryptoDailyJob
from ..db import DB
from ..models import PLATFORM_NAMES
from ..notify import Notifier

log = logging.getLogger("web")

INDEX_PATH = Path(__file__).resolve().parent / "index.html"


def _money(value) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num == int(num):
        return f"¥{num:,.0f}"
    return f"¥{num:,.2f}"


class Dashboard:
    """面板数据聚合：读取配置/数据库/缓存，并处理手动触发动作。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.db_path = resolve_path(cfg["storage"]["db_path"])
        self.cache_path = resolve_path("data/crypto_cache.json")
        self.send_path = resolve_path("data/crypto_last_send.json")
        self.pid_path = resolve_path("data/monitor.pid")
        self.notifier = Notifier(
            channels_cfg=cfg["notify"].get("channels"),
            console=cfg["notify"].get("console", True),
        )
        self.crypto_cfg = dict(cfg.get("crypto") or {})
        self.crypto_enabled = bool(self.crypto_cfg.get("enabled"))
        self.crypto = CryptoDailyJob(cfg, self.notifier)
        # 行情抓取耗时较长，且 requests.Session 不保证线程安全：串行执行
        self._crypto_lock = threading.Lock()

    # ---------- 文件与进程状态 ----------

    @staticmethod
    def _read_json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _daemon_state(self) -> dict:
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return {"running": False, "pid": None}
        try:
            os.kill(pid, 0)
        except OSError:
            return {"running": False, "pid": None}
        return {"running": True, "pid": pid}

    # ---------- 概览 ----------

    @staticmethod
    def _condition(rule: dict) -> str:
        if rule.get("type") == "keyword":
            parts = [f"关键词「{rule.get('keyword', '')}」"]
            if rule.get("max_price") is not None:
                parts.append(f"价格 ≤ {_money(rule['max_price'])}")
            if rule.get("min_price") is not None:
                parts.append(f"≥ {_money(rule['min_price'])}")
            if rule.get("require_badge"):
                parts.append(f"含「{rule['require_badge']}」")
            return "，".join(parts)
        parts = []
        if rule.get("target_price") is not None:
            parts.append(f"价格 ≤ {_money(rule['target_price'])}")
        if rule.get("drop_percent"):
            parts.append(f"较上次降 ≥ {rule['drop_percent']}%")
        return " 或 ".join(parts) or "—"

    def _shop_snapshot(self) -> dict:
        db = DB(self.db_path)
        try:
            return {
                "items": db.latest_items(60),
                "alerts_24h": db.alerts_count(86400),
                "seen": db.seen_count(),
            }
        finally:
            db.close()

    def overview(self) -> dict:
        raw_rules = self.cfg.get("rules") or []
        rules = [
            {
                "name": r.get("name", "未命名规则"),
                "platform": PLATFORM_NAMES.get(r.get("platform"), r.get("platform", "")),
                "type": "指定商品" if r.get("type") == "item" else "关键词",
                "condition": self._condition(r),
                "enabled": bool(r.get("enabled", True)),
            }
            for r in raw_rules
        ]
        shop = self._shop_snapshot()
        items = [
            {**it,
             "platform_name": PLATFORM_NAMES.get(it["platform"], it["platform"]),
             "stock": None if it.get("stock") is None else bool(it["stock"])}
            for it in shop["items"]
        ]
        enabled_platforms = [
            PLATFORM_NAMES.get(name, name)
            for name, conf in (self.cfg.get("platforms") or {}).items()
            if (conf or {}).get("enabled")
        ]
        last_send = self._read_json(self.send_path) or {}
        return {
            "now": time.time(),
            "daemon": self._daemon_state(),
            "stats": {
                "rules_enabled": sum(1 for r in rules if r["enabled"]),
                "rules_total": len(rules),
                "alerts_24h": shop["alerts_24h"],
                "seen": shop["seen"],
                "items": len(items),
                "platforms": enabled_platforms,
            },
            "rules": rules,
            "items": items,
            "crypto": {
                "enabled": self.crypto_enabled,
                "report_time": self.crypto_cfg.get("report_time", "00:10"),
                "send_always": bool(self.crypto_cfg.get("send_always", True)),
                "new_coin_days": int(self.crypto_cfg.get("new_coin_days", 5)),
                "next_run": (self.crypto.next_run_time().strftime("%Y-%m-%d %H:%M")
                             if self.crypto_enabled else None),
                "last_send_at": last_send.get("ts"),
                "result": self._read_json(self.cache_path),
            },
        }

    # ---------- 动作 ----------

    def refresh_crypto(self) -> dict:
        """抓取一轮行情并写入缓存（不发送通知）。"""
        with self._crypto_lock:
            result = self.crypto.compute()
            self._write_json(self.cache_path, result)
            return result

    def send_crypto(self) -> dict:
        """抓取一轮行情并立即推送日报。"""
        with self._crypto_lock:
            result = self.crypto.compute()
            title, body = self.crypto.format_report(
                result["candidates"], result["triggered"],
                new_coins=result.get("new_coins"),
                new_listing_count=result.get("new_listing_count"))
            self.notifier.send(title, body)
            self._write_json(self.cache_path, result)
            self._write_json(self.send_path, {
                "ts": time.time(),
                "title": title,
                "triggered": len(result["triggered"]),
                "candidates": len(result["candidates"]),
            })
            return {
                "title": title,
                "candidates": len(result["candidates"]),
                "triggered": len(result["triggered"]),
            }

    def history(self, platform: str, item_id: str, limit: int = 200) -> dict:
        db = DB(self.db_path)
        try:
            rows = db.history(platform, item_id, limit=limit)
        finally:
            db.close()
        points = [
            {"ts": row["ts"], "price": row["price"]}
            for row in reversed(rows) if row["price"] is not None
        ]
        return {
            "platform": platform,
            "item_id": item_id,
            "title": (rows[0]["title"] if rows else "") or item_id,
            "points": points,
        }


class DashboardHandler(BaseHTTPRequestHandler):
    dashboard: Dashboard  # 由 serve() 注入
    server_version = "deal-monitor-web/1.0"

    # ---------- 响应工具 ----------

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self) -> None:
        try:
            body = INDEX_PATH.read_bytes()
        except OSError:
            self._send_json({"ok": False, "error": "index.html 缺失"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ---------- 路由 ----------

    def do_GET(self) -> None:  # noqa: N802 (http.server 约定)
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._send_index()
            elif path == "/api/overview":
                self._send_json(self.dashboard.overview())
            elif path == "/api/history":
                query = parse_qs(parsed.query)
                platform = (query.get("platform") or [""])[0]
                item_id = (query.get("item_id") or [""])[0]
                if not platform or not item_id:
                    self._send_json({"ok": False, "error": "缺少 platform 或 item_id"}, 400)
                    return
                try:
                    limit = min(1000, max(2, int((query.get("limit") or ["200"])[0])))
                except ValueError:
                    limit = 200
                self._send_json(self.dashboard.history(platform, item_id, limit))
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self._send_json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            log.exception("GET %s 失败", path)
            self._send_json({"ok": False, "error": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)  # 目前无请求体，读掉以免连接复用出错
        try:
            if path == "/api/crypto/refresh":
                self._send_json({"ok": True, "result": self.dashboard.refresh_crypto()})
            elif path == "/api/crypto/send":
                self._send_json({"ok": True, **self.dashboard.send_crypto()})
            else:
                self._send_json({"ok": False, "error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            log.exception("POST %s 失败", path)
            self._send_json({"ok": False, "error": str(exc)}, 500)


def serve(cfg: dict, host: str = "127.0.0.1", port: int = 8765) -> None:
    dashboard = Dashboard(cfg)
    handler = type("BoundDashboardHandler", (DashboardHandler,),
                   {"dashboard": dashboard})
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        raise SystemExit(
            f"无法启动 Web 面板（{host}:{port}）：{exc}\n"
            f"端口可能被占用，可用 --port 换一个端口，例如：\n"
            f"  python run.py web --port 8766"
        ) from exc
    httpd.daemon_threads = True
    log.info("Web 面板已启动: http://%s:%d", host, port)
    local_only = host in ("127.0.0.1", "localhost", "::1")
    print(f"\n  deal-monitor 监控面板: http://{host}:{port}\n"
          f"  {'仅监听本机' if local_only else '监听所有网卡'}，按 Ctrl+C 停止\n", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Web 面板收到退出信号")
    finally:
        httpd.server_close()
