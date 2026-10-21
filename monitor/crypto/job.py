"""OKX 虚拟币每日监控：成交额筛选 → 连续下跌检测 → 钉钉日报。

规则（config.yaml 的 crypto 段可调）：
- 候选：USDT 现货，24h 成交额 ≥ min_volume_usd（默认 200 万美元）；
  排除稳定币与杠杆代币（3L/3S/5L/5S）。
- 触发：最近 decline_days（默认 2）个已收盘日线（按北京时间 0 点切分）连续下跌。
- 新币关注：上市 ≤ new_coin_days（默认 5）天的加密货币，连续下跌时单独置顶，
  不受成交额门槛限制；排除代币化股票（instCategory=3）。
- 发送：每天 report_time（默认 00:10）推送到已配置的通知渠道；
  测试阶段 send_always=true，无论是否触发都会发日报。
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

from .okx import MIRROR_BASES, OFFICIAL_BASES, OkxClient, detect_local_proxy

log = logging.getLogger("crypto.job")

# 与 engine.FOOTER 保持一致；钉钉「自定义关键词」安全设置靠「监控」二字通过校验
FOOTER = "\n\n— deal-monitor 价格监控"

# 稳定币 / 法币不参与监控
STABLECOINS = {
    "USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDE", "USDD", "PYUSD", "USDP",
    "GUSD", "FRAX", "LUSD", "USTC", "USDS", "SUSDE", "EUR", "EURT",
}
# 杠杆代币
LEVERAGED_RE = re.compile(r"(3L|3S|5L|5S)$")


def fmt_usd(value: float) -> str:
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    if value >= 1e3:
        return f"${value / 1e3:.1f}K"
    return f"${value:.0f}"


def fmt_price(value: float) -> str:
    if value >= 100:
        return f"${value:,.2f}"
    if value >= 1:
        return f"${value:,.4f}".rstrip("0").rstrip(".")
    if value >= 0.001:
        return f"${value:.6f}".rstrip("0").rstrip(".")
    return f"${value:.10f}".rstrip("0").rstrip(".")


def spot_usd_volume(ticker: dict) -> float:
    """把行情里的成交量字段统一折算成 USDT 金额。

    OKX 现货 vol24h / volCcy24h 两个字段在不同版本的文档中表述不一，
    这里利用 vol24h×last ≈ volCcy24h 的关系自动判别哪个字段是币的数量。
    """
    try:
        v1 = float(ticker.get("vol24h") or 0)     # 可能是币的数量
        v2 = float(ticker.get("volCcy24h") or 0)  # 可能是计价货币数量
        last = float(ticker.get("last") or 0)
    except (TypeError, ValueError):
        return 0.0
    if v1 <= 0:
        return v2
    if v2 <= 0:
        return v1 * last
    err_if_v1_is_base = abs(v1 * last - v2) / max(v2, 1e-12)
    err_if_v2_is_base = abs(v2 * last - v1) / max(v1, 1e-12)
    return v2 if err_if_v1_is_base <= err_if_v2_is_base else v1


class CryptoDailyJob:
    def __init__(self, cfg: dict, notifier):
        ccfg = dict(cfg.get("crypto") or {})
        self.notifier = notifier
        self.min_volume = float(ccfg.get("min_volume_usd", 2_000_000))
        self.decline_days = max(1, int(ccfg.get("decline_days", 2)))
        self.new_coin_days = max(0, int(ccfg.get("new_coin_days", 5)))
        self.send_always = bool(ccfg.get("send_always", True))
        self.max_items = max(1, int(ccfg.get("max_items", 30)))
        self.hh, self.mm = self._parse_time(ccfg.get("report_time", "00:10"))
        self._explicit_proxy = str(ccfg.get("proxy") or "").strip()
        self._custom_bases = tuple(ccfg.get("base_urls") or ())
        proxy = self._explicit_proxy or detect_local_proxy()
        self.client = OkxClient(
            proxy=proxy, timeout=float(ccfg.get("timeout", 12)),
            bases=self._pick_bases(proxy),
        )

    def _pick_bases(self, proxy: str) -> tuple:
        """选数据源：有代理走官方域名；直连则优先国内镜像（免翻墙）。"""
        if self._custom_bases:
            return self._custom_bases
        if proxy:
            return OFFICIAL_BASES + MIRROR_BASES
        return MIRROR_BASES + OFFICIAL_BASES

    # ---------- 调度 ----------

    @staticmethod
    def _parse_time(text) -> tuple[int, int]:
        try:
            hh, mm = str(text).split(":", 1)
            return int(hh) % 24, int(mm) % 60
        except (ValueError, TypeError):
            log.warning("crypto.report_time 格式应为 HH:MM，使用默认 00:10")
            return 0, 10

    def next_run_time(self, now: Optional[datetime] = None) -> datetime:
        now = now or datetime.now()
        target = now.replace(hour=self.hh, minute=self.mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # ---------- 数据获取 ----------

    def collect_candidates(self, tickers: list) -> list:
        out = []
        for t in tickers:
            inst = t.get("instId") or ""
            if not inst.endswith("-USDT"):
                continue
            base = inst.split("-")[0].upper()
            if base in STABLECOINS or LEVERAGED_RE.search(base):
                continue
            try:
                last = float(t.get("last") or 0)
            except (TypeError, ValueError):
                continue
            vol = spot_usd_volume(t)
            if vol < self.min_volume or last <= 0:
                continue
            out.append({
                "instId": inst, "base": base, "last": last,
                "volume": vol, "open24h": t.get("open24h"),
            })
        out.sort(key=lambda x: x["volume"], reverse=True)
        return out

    def fetch_candles(self, candidates: list) -> dict:
        """并发拉取候选币对日线；返回 {instId: rows}，失败的不包含。"""
        limit = self.decline_days + 2  # 多取一根，当天未收盘的会被 confirm 字段过滤
        result: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self.client.daily_candles, c["instId"], limit): c["instId"]
                       for c in candidates}
            for fut in as_completed(futures):
                inst = futures[fut]
                try:
                    result[inst] = fut.result()
                except Exception as exc:
                    log.warning("%s 日线获取失败：%s", inst, exc)
        return result

    @staticmethod
    def analyze(rows: list, decline_days: int = 2) -> Optional[dict]:
        """检测最近 decline_days 个已收盘日线是否连续下跌；返回摘要或 None。

        rows 为 OKX 日线数组（最新在前）：
        [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        """
        closed = [r for r in rows if len(r) > 8 and str(r[8]) == "1"]
        if len(closed) < decline_days + 1:
            return None
        try:
            closes = [float(r[4]) for r in closed[: decline_days + 1]]  # 最近收盘日在前
        except (TypeError, ValueError):
            return None
        if any(c <= 0 for c in closes):
            return None
        for i in range(decline_days):
            if closes[i] >= closes[i + 1]:
                return None
        newest, oldest = closes[0], closes[-1]
        return {
            "closes": closes,
            "cum_drop": (oldest - newest) / oldest * 100,           # 区间累计跌幅
            "last_drop": (closes[1] - closes[0]) / closes[1] * 100,  # 最近一天跌幅
        }

    def collect_new_coins(self, instruments: list, tickers: list,
                          candle_map: dict) -> tuple[int, list]:
        """筛选上市 ≤ new_coin_days 天且连续下跌的新币。

        - 只统计真正的加密货币：instCategory == "1"；
          OKX 的代币化股票（instCategory == "3"，代码 X 开头）不算新币。
        - 新币不受成交额门槛限制，单独列入「重点关注」。
        返回 (上市新币总数, 触发连续下跌的新币列表)。
        """
        if self.new_coin_days <= 0:
            return 0, []
        now_ms = time.time() * 1000
        cutoff = now_ms - self.new_coin_days * 86_400_000
        ticker_map = {t.get("instId"): t for t in tickers}
        new_items = []

        for ins in instruments:
            inst = ins.get("instId") or ""
            if not inst.endswith("-USDT") or ins.get("state") != "live":
                continue
            # 1=加密货币；3=代币化股票。缺少该字段时按普通币处理
            if str(ins.get("instCategory") or "1") != "1":
                continue
            try:
                list_ts = float(ins.get("listTime") or 0)
            except (TypeError, ValueError):
                continue
            if not list_ts or list_ts < cutoff or list_ts > now_ms:
                continue
            base = (ins.get("baseCcy") or inst.split("-")[0]).upper()
            if base in STABLECOINS or LEVERAGED_RE.search(base):
                continue
            ticker = ticker_map.get(inst)
            if not ticker:
                continue
            try:
                last = float(ticker.get("last") or 0)
            except (TypeError, ValueError):
                continue
            if last <= 0:
                continue
            new_items.append({
                "instId": inst,
                "base": base,
                "last": last,
                "volume": spot_usd_volume(ticker),
                "list_ts": list_ts,
                "days_listed": (now_ms - list_ts) / 86_400_000,
            })

        if not new_items:
            return 0, []

        # 成交额达标的币可能已经取过日线，只补取缺的
        missing = [c for c in new_items if c["instId"] not in candle_map]
        if missing:
            candle_map.update(self.fetch_candles(missing))

        out = []
        for c in new_items:
            info = self.analyze(candle_map.get(c["instId"]) or [], self.decline_days)
            if info:
                out.append({**c, **info})
        out.sort(key=lambda x: x["cum_drop"], reverse=True)
        return len(new_items), out

    # ---------- 报告 ----------

    def format_report(self, candidates: list, triggered: list,
                      new_coins: Optional[list] = None,
                      new_listing_count: Optional[int] = None,
                      when: Optional[datetime] = None) -> tuple:
        when = when or datetime.now()
        date_str = when.strftime("%Y-%m-%d")
        title = f"OKX 监控日报 | {date_str}"
        new_coins = new_coins or []
        new_ids = {c["instId"] for c in new_coins}
        # 新币单独置顶展示，普通列表里不重复出现
        regular = [c for c in triggered if c["instId"] not in new_ids]

        lines = [
            f"**📊 OKX 虚拟币监控日报 · {date_str}**",
            "",
            f"- 筛选：24h 成交额 ≥ {fmt_usd(self.min_volume)} 的 USDT 现货",
            f"- 规则：连续 {self.decline_days} 日收盘下跌（日线按北京时间 0 点切分）",
        ]
        if self.new_coin_days:
            lines.append(f"- 新币关注：上市 ≤ {self.new_coin_days} 天且连续 "
                         f"{self.decline_days} 日下跌（不受成交额门槛限制）")
        summary = (f"- 达标币对 **{len(candidates)}** 个 ｜ "
                   f"触发下跌 **{len(triggered)}** 个")
        if self.new_coin_days:
            summary += f" ｜ 新币关注 **{len(new_coins)}** 个"
        lines.extend([summary, ""])

        if self.new_coin_days and new_coins:
            lines.append(f"**🆕 新币重点关注（上市 ≤ {self.new_coin_days} 天）**")
            for i, c in enumerate(new_coins[: self.max_items], 1):
                url = f"https://www.okx.com/trade-spot/{c['base'].lower()}-usdt"
                last_txt = (f"-{c['last_drop']:.1f}%"
                            if c["last_drop"] >= 0.05 else "0.0%")
                days_txt = f"{float(c.get('days_listed') or 0):.1f}"
                lines.append(
                    f"{i}. [{c['instId']}]({url}) —— {fmt_price(c['last'])}"
                    f" ｜ 上市 {days_txt} 天"
                    f" ｜ {self.decline_days}日 -{c['cum_drop']:.1f}%"
                    f" ｜ 昨日 {last_txt}"
                    f" ｜ 24h额 {fmt_usd(c['volume'])}"
                )
            if len(new_coins) > self.max_items:
                lines.append(f"…… 其余 {len(new_coins) - self.max_items} 个新币已省略")
            lines.append("")
        elif self.new_coin_days:
            if new_listing_count is None:
                lines.append("🆕 新币关注：上市时间数据暂不可用，本轮未统计。")
            elif new_listing_count > 0:
                lines.append(f"🆕 新币关注：近 {self.new_coin_days} 天上市的 "
                             f"{new_listing_count} 个新币中，暂无连续 "
                             f"{self.decline_days} 日下跌。")
            else:
                lines.append(f"🆕 新币关注：近 {self.new_coin_days} 天暂无新币上市。")
            lines.append("")

        shown = regular[: self.max_items]
        if shown:
            heading = f"**🔻 连续 {self.decline_days} 日下跌（按累计跌幅排序）**"
            if new_coins:
                heading = f"**🔻 连续 {self.decline_days} 日下跌（不含新币）**"
            lines.append(heading)
            for i, c in enumerate(shown, 1):
                url = f"https://www.okx.com/trade-spot/{c['base'].lower()}-usdt"
                last_txt = (f"-{c['last_drop']:.1f}%"
                            if c["last_drop"] >= 0.05 else "0.0%")
                lines.append(
                    f"{i}. [{c['instId']}]({url}) —— {fmt_price(c['last'])}"
                    f" ｜ {self.decline_days}日 -{c['cum_drop']:.1f}%"
                    f" ｜ 昨日 {last_txt}"
                    f" ｜ 24h额 {fmt_usd(c['volume'])}"
                )
            if len(regular) > len(shown):
                lines.append(f"…… 其余 {len(regular) - len(shown)} 个已省略"
                             f"（可调大 crypto.max_items）")
            lines.append("")
        elif not triggered:
            lines.append(f"✅ 今日没有币种满足「连续 {self.decline_days} 日下跌」，无需操作。")
            lines.append("")
        if self.send_always:
            lines.append("ℹ️ 测试阶段：每日固定推送；正式启用后可设 "
                         "crypto.send_always=false，仅在触发时推送。")
        return title, "\n".join(lines) + FOOTER

    # ---------- 执行 ----------

    def compute(self, mock: bool = False) -> dict:
        """抓取并分析一轮行情，返回结构化结果（不发送通知）。

        Web 面板与 CLI 共用这套结果，字段可直接 JSON 序列化。
        """
        source = "mock"
        instruments: list = []
        if mock:
            tickers, candle_map, instruments = _mock_data()
        else:
            if not self._explicit_proxy:
                # 代理可能是在程序启动之后才打开的，每次运行前重新探测
                proxy = detect_local_proxy()
                self.client.session.proxies = (
                    {"http": proxy, "https": proxy} if proxy else {})
                self.client.bases = self._pick_bases(proxy)
            tickers = self.client.spot_tickers()
            source = self.client.last_base
            log.info("数据源: %s", source)
            candle_map = {}
            if self.new_coin_days:
                try:
                    instruments = self.client.spot_instruments()
                except Exception as exc:  # noqa: BLE001
                    log.warning("获取上市时间失败，本轮跳过新币关注：%s", exc)

        candidates = self.collect_candidates(tickers)
        log.info("成交额达标币对：%d 个", len(candidates))

        if not mock:
            candle_map = self.fetch_candles(candidates) if candidates else {}

        triggered = []
        for c in candidates:
            info = self.analyze(candle_map.get(c["instId"]) or [], self.decline_days)
            if info:
                triggered.append({**c, **info})
        triggered.sort(key=lambda x: x["cum_drop"], reverse=True)
        log.info("连续 %d 日下跌：%d 个", self.decline_days, len(triggered))

        new_listing_count: Optional[int] = 0 if not self.new_coin_days else None
        new_coins: list = []
        if self.new_coin_days and instruments:
            new_listing_count, new_coins = self.collect_new_coins(
                instruments, tickers, candle_map)
            new_ids = {c["instId"] for c in new_coins}
            for c in triggered:
                if c["instId"] in new_ids:
                    c["is_new"] = True
        if self.new_coin_days:
            log.info("新币关注（上市 ≤ %d 天）：上市 %s 个，连续下跌 %d 个",
                     self.new_coin_days,
                     "未知" if new_listing_count is None else new_listing_count,
                     len(new_coins))

        return {
            "generated_at": time.time(),
            "source": source,
            "min_volume": self.min_volume,
            "decline_days": self.decline_days,
            "new_coin_days": self.new_coin_days,
            "new_listing_count": new_listing_count,
            "candidates": candidates,
            "triggered": triggered,
            "new_coins": new_coins,
        }

    def run(self, dry_run: bool = False, mock: bool = False) -> str:
        log.info("OKX 日报开始（成交额 ≥ %s ｜ 连续 %d 日下跌 ｜ %s）",
                 fmt_usd(self.min_volume), self.decline_days,
                 "测试模式：始终发送" if self.send_always else "仅触发时发送")
        result = self.compute(mock=mock)
        title, body = self.format_report(
            result["candidates"], result["triggered"],
            new_coins=result["new_coins"],
            new_listing_count=result["new_listing_count"])
        should_send = (self.send_always or bool(result["triggered"])
                       or bool(result["new_coins"]))
        if dry_run:
            print("\n===== 钉钉消息标题 =====")
            print(title)
            print("\n===== 钉钉消息正文 =====")
            print(body)
        elif should_send:
            self.notifier.send(title, body)
            log.info("OKX 日报已发送")
        else:
            log.info("今日无币种触发（send_always=false），跳过推送")
        return body


def _mock_data() -> tuple[list, dict, list]:
    """离线演示数据：不联网即可看到完整日报效果（run.py crypto --mock）。"""
    now_ms = int(datetime.now().timestamp() * 1000)
    day = 86_400_000

    def row(i: int, close: float, confirm: str = "1") -> list:
        return [str(now_ms - i * day), str(close), str(close),
                str(close), str(close), "1000", "1000", "1000", confirm]

    def t(inst: str, last: float, vol_usdt: float) -> dict:
        return {"instId": inst, "last": str(last), "open24h": str(last),
                "vol24h": str(vol_usdt / last), "volCcy24h": str(vol_usdt)}

    def ins(inst: str, days_listed: float, category: str = "1") -> dict:
        return {"instId": inst, "baseCcy": inst.split("-")[0],
                "instCategory": category, "state": "live",
                "listTime": str(now_ms - int(days_listed * day))}

    tickers = [
        t("BTC-USDT", 67000, 1.2e9),
        t("ETH-USDT", 3200, 5e8),
        t("SOL-USDT", 146.5, 2e8),
        t("DOGE-USDT", 0.1205, 8e7),
        t("PEPE-USDT", 0.0000072, 1.2e7),
        t("USDC-USDT", 1.0, 9e7),      # 稳定币，应被排除
        t("BTC3L-USDT", 2.31, 6e6),    # 杠杆代币，应被排除
        t("TINY-USDT", 0.5, 1e5),      # 成交额不足，应被排除
        t("NEW-USDT", 0.42, 3.5e6),    # 新币：上市 4 天、连续下跌
    ]
    candles = {
        "BTC-USDT": [row(0, 67000), row(1, 68200), row(2, 69500)],
        "ETH-USDT": [row(0, 3200), row(1, 3180), row(2, 3300)],
        "SOL-USDT": [row(0, 148), row(1, 145), row(2, 150)],
        "DOGE-USDT": [row(0, 0.1205), row(1, 0.129), row(2, 0.1399)],
        "PEPE-USDT": [row(0, 0.000008, confirm="0"),  # 未收盘，应被忽略
                      row(1, 0.0000072), row(2, 0.0000089), row(3, 0.000011)],
        "NEW-USDT": [row(0, 0.42, confirm="0"),       # 今日未收盘，应被忽略
                     row(1, 0.42), row(2, 0.50), row(3, 0.60)],
    }
    instruments = [
        ins("BTC-USDT", 400), ins("ETH-USDT", 300), ins("SOL-USDT", 200),
        ins("DOGE-USDT", 150), ins("PEPE-USDT", 100), ins("USDC-USDT", 90),
        ins("BTC3L-USDT", 80), ins("TINY-USDT", 60), ins("NEW-USDT", 4),
    ]
    return tickers, candles, instruments
