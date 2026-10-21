"""OKX 公开市场 API 客户端（不需要 API Key）。

- 接口文档：https://www.okx.com/docs-v5/
- 大陆网络无法直连 OKX（DNS 污染 + SNI 阻断），需本机代理；
  proxy 留空时自动探测常见代理端口，也可用环境变量 HTTPS_PROXY。
"""
from __future__ import annotations

import logging
import socket
import threading
import time

import requests

log = logging.getLogger("crypto.okx")

# 官方域名（走代理时用；大陆直连会被 SNI 阻断）
OFFICIAL_BASES = ("https://www.okx.com", "https://aws.okx.com")

# 国内直连镜像：OKX 为大陆线路维护的轮换域名，实测完整转发官方 /api/v5 行情接口。
# 域名会不定期更换；失效时可在 config.yaml 的 crypto.base_urls 覆写。
MIRROR_BASES = ("https://www.ouyiawotkc.com", "https://www.oyigwcn.systems")

DEFAULT_BASES = MIRROR_BASES + OFFICIAL_BASES

# 常见本机代理端口：FlClash/Clash(7890/7897/7891)、v2rayU(1087)、SOCKS(1080)
COMMON_PROXY_PORTS = (7890, 7897, 7891, 1087, 1080, 8889)


class OkxError(RuntimeError):
    pass


def detect_local_proxy() -> str:
    """探测本机常见代理端口，返回 http://127.0.0.1:port；未发现返回空串。"""
    for port in COMMON_PROXY_PORTS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    log.info("检测到本机代理端口 %d，将走代理访问 OKX", port)
                    return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return ""


class OkxClient:
    """OKX v5 公开行情接口的最小封装。"""

    def __init__(self, proxy: str = "", timeout: float = 12.0, retries: int = 2,
                 bases: "tuple | list | None" = None):
        self.timeout = timeout
        self.retries = retries
        self.bases = tuple(bases) if bases else DEFAULT_BASES
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"),
            "Accept": "application/json",
        })
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
            log.info("OKX 请求经代理: %s", proxy)
        self._lock = threading.Lock()
        self._last_ts = 0.0
        self.last_base = self.bases[0]

    # ---------- 底层请求 ----------

    def _throttle(self, min_interval: float = 0.08) -> None:
        """全局限速（多线程共享），满足 OKX 约 40 次/2 秒的限频。"""
        with self._lock:
            wait = self._last_ts + min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_ts = time.monotonic()

    def _get(self, path: str, params: dict | None = None) -> list:
        last_err: Exception | None = None
        for base in self.bases:
            for attempt in range(self.retries + 1):
                self._throttle()
                try:
                    resp = self.session.get(base + path, params=params, timeout=self.timeout)
                    if resp.status_code == 429:
                        raise OkxError("请求过于频繁（429）")
                    resp.raise_for_status()
                    body = resp.json()
                except Exception as exc:
                    last_err = exc
                    time.sleep(0.6 * (attempt + 1))
                    continue
                if str(body.get("code")) != "0":
                    raise OkxError(f"OKX 接口错误 {body.get('code')}: {body.get('msg')}")
                self.last_base = base
                return body.get("data") or []
        raise OkxError(f"OKX 请求失败 {path}：{last_err}")

    # ---------- 行情 ----------

    def spot_tickers(self) -> list:
        """全部现货行情（一次返回所有币对）。"""
        return self._get("/api/v5/market/tickers", {"instType": "SPOT"})

    def spot_instruments(self) -> list:
        """全部现货交易对信息（含 listTime 上市时间、instCategory 分类）。

        instCategory: "1"=加密货币；"3"=代币化股票（X 开头，不属于新币）。
        """
        return self._get("/api/v5/public/instruments", {"instType": "SPOT"})

    def daily_candles(self, inst_id: str, limit: int = 5) -> list:
        """日线 K 线，最新在前：[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]。

        confirm == "1" 表示该日线已收盘。
        """
        return self._get("/api/v5/market/candles",
                         {"instId": inst_id, "bar": "1D", "limit": limit})
