"""监控引擎：调度循环、规则判断、提醒发送。"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta
from typing import Optional

from .browser.session import BrowserManager
from .config import parse_rules, resolve_path
from .crypto.job import CryptoDailyJob
from .db import DB
from .models import PLATFORM_NAMES, ItemRule, Snapshot
from .notify import Notifier
from .platforms import build_platform
from .platforms.base import BasePlatform, PlatformContext

log = logging.getLogger("engine")

# 消息统一后缀；钉钉「自定义关键词」安全设置填「监控」时靠它满足关键词要求
FOOTER = "\n\n— deal-monitor 价格监控"


class Engine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.db = DB(resolve_path(cfg["storage"]["db_path"]))
        self.notifier = Notifier(
            channels_cfg=cfg["notify"].get("channels"),
            console=cfg["notify"].get("console", True),
        )
        self.rules = parse_rules(cfg)
        bcfg = cfg["browser"]
        self.browser = BrowserManager(
            profiles_dir=resolve_path(bcfg["profiles_dir"]),
            headless=bcfg.get("headless", True),
            channel=bcfg.get("channel", "chrome"),
            nav_timeout_ms=bcfg.get("navigation_timeout_ms", 30000),
        )
        self._platforms: dict[str, BasePlatform] = {}
        self._running = True
        self.cooldown = float(cfg.get("alert_cooldown_hours", 6)) * 3600
        # 虚拟币日报（OKX）：enabled 后由 run_forever 每天 report_time 触发
        self.crypto: Optional[CryptoDailyJob] = None
        if (cfg.get("crypto") or {}).get("enabled"):
            self.crypto = CryptoDailyJob(cfg, self.notifier)

    # ---------- 平台管理 ----------

    def platform(self, name: str) -> BasePlatform:
        if name not in self._platforms:
            ctx = PlatformContext(browser=self.browser, config=self.cfg)
            self._platforms[name] = build_platform(name, ctx)
        return self._platforms[name]

    def active_platforms(self) -> list:
        names = sorted({r.platform for r in self.rules})
        out = []
        for name in names:
            if self.cfg["platforms"].get(name, {}).get("enabled", True):
                out.append(name)
            else:
                log.info("平台 %s 已在配置中禁用，跳过", name)
        return out

    # ---------- 单次执行 ----------

    def run_platform(self, name: str) -> None:
        rule_list = [r for r in self.rules if r.platform == name]
        if not rule_list:
            return
        adapter = self.platform(name)
        for rule in rule_list:
            try:
                if rule.type == "item":
                    self.check_item_rule(adapter, rule)
                else:
                    self.check_keyword_rule(adapter, rule)
            except Exception:
                log.exception("规则「%s」执行出错", rule.name)

    def run_once(self, platforms: Optional[list] = None) -> None:
        for name in (platforms or self.active_platforms()):
            log.info("===== 执行 %s =====", PLATFORM_NAMES.get(name, name))
            self.run_platform(name)

    def run_forever(self) -> None:
        active = self.active_platforms()
        if not active and not self.crypto:
            raise SystemExit("没有任何启用的规则，请编辑 config.yaml 添加规则（可参考示例）")
        if active:
            log.info("开始监控: %s",
                     "、".join(PLATFORM_NAMES.get(p, p) for p in active))
        next_run = {p: time.monotonic() + random.uniform(0, 5) for p in active}
        next_crypto = self.crypto.next_run_time() if self.crypto else None
        crypto_fails = 0
        if self.crypto and next_crypto:
            log.info("虚拟币日报已启用：每天 %02d:%02d 发送 OKX 监控日报（下次 %s）",
                     self.crypto.hh, self.crypto.mm, next_crypto.strftime("%m-%d %H:%M"))
        try:
            while self._running:
                now = time.monotonic()
                for p in active:
                    if now < next_run[p]:
                        continue
                    try:
                        self.run_platform(p)
                    except Exception:
                        log.exception("平台 %s 本轮执行出错", p)
                    pcfg = self.cfg["platforms"].get(p, {})
                    interval = float(pcfg.get("interval", 180))
                    jitter = float(pcfg.get("jitter", 0.3))
                    interval *= random.uniform(1 - jitter, 1 + jitter)
                    interval = max(interval, 30.0)
                    next_run[p] = time.monotonic() + interval
                    log.debug("%s 下次检查在 %.0f 秒后", p, interval)
                if self.crypto and datetime.now() >= next_crypto:
                    try:
                        self.crypto.run()
                        crypto_fails = 0
                        next_crypto = self.crypto.next_run_time()
                        log.info("下次虚拟币日报: %s", next_crypto.strftime("%m-%d %H:%M"))
                    except Exception:
                        crypto_fails += 1
                        log.exception("OKX 日报执行失败（第 %d/4 次）", crypto_fails)
                        if crypto_fails >= 4:
                            crypto_fails = 0
                            next_crypto = self.crypto.next_run_time()
                            log.warning("连续失败，跳过今日日报，下次: %s",
                                        next_crypto.strftime("%m-%d %H:%M"))
                        else:
                            next_crypto = datetime.now() + timedelta(minutes=15)
                            log.info("15 分钟后自动重试虚拟币日报")
                time.sleep(1.0)
        except KeyboardInterrupt:
            log.info("收到退出信号，正在关闭…")
        finally:
            self.close()

    def close(self) -> None:
        for adapter in self._platforms.values():
            try:
                adapter.close()
            except Exception:
                pass
        self.browser.close()
        self.db.close()

    # ---------- 指定商品规则 ----------

    def check_item_rule(self, adapter: BasePlatform, rule: ItemRule) -> None:
        snap = adapter.fetch_item(rule)
        if snap is None:
            log.warning("「%s」本次抓取失败", rule.name)
            return

        prev = self.db.last_record(rule.platform, snap.item_id)
        prev_price = prev["price"] if prev else None
        prev_stock = None if prev is None or prev["stock"] is None else bool(prev["stock"])
        self.db.record_price(
            rule.platform, snap.item_id, snap.price, snap.title, snap.url, snap.stock,
        )

        if snap.price is None:
            log.info("「%s」%s 当前无报价", rule.name, snap.title[:40])
            return

        reasons: list = []
        if rule.target_price is not None and snap.price <= rule.target_price:
            reasons.append(("price_below", f"已达到目标价 ¥{rule.target_price:,.2f}"))
        if rule.drop_percent and prev_price and prev_price > 0:
            drop = (prev_price - snap.price) / prev_price * 100
            if drop >= rule.drop_percent:
                reasons.append(("drop", f"较上次价格下降 {drop:.1f}%"))
        if prev_stock is False and snap.stock is True:
            reasons.append(("restock", "重新有货"))

        if not reasons:
            log.info("「%s」%s | %s（未触发）", rule.name, snap.title[:34], snap.price_text)
            return

        for cond, desc in reasons:
            dedup_key = f"item|{rule.name}|{snap.item_id}|{cond}"
            if self.db.alert_sent_recently(dedup_key, self.cooldown):
                log.info("「%s」%s 提醒冷却中，跳过", rule.name, desc)
                continue
            title, md = self.format_item_alert(rule, snap, prev_price, desc)
            self.notifier.send(title, md)
            self.db.log_alert(dedup_key)

    def format_item_alert(self, rule: ItemRule, snap: Snapshot,
                          prev_price: Optional[float], desc: str) -> tuple:
        pname = PLATFORM_NAMES.get(snap.platform, snap.platform)
        title = f"{pname}监控提醒 | {rule.name}"
        lines = [
            f"**🔔 {pname} · 价格提醒**",
            f"**{clean_title(snap.title)}**",
            "",
            f"- 现价：**{snap.price_text}**",
        ]
        if prev_price:
            lines.append(f"- 上次记录价：¥{prev_price:,.2f}")
        if rule.target_price is not None:
            lines.append(f"- 目标价：¥{rule.target_price:,.2f}")
        lines.append(f"- 触发：{desc}")
        lines.append(f"- [点击查看商品]({snap.url})")
        return title, "\n".join(lines) + FOOTER

    # ---------- 关键词规则 ----------

    def check_keyword_rule(self, adapter: BasePlatform, rule: ItemRule) -> None:
        items = adapter.search(rule)
        log.info("「%s」搜索到 %d 条结果", rule.name, len(items))

        new_items: list = []
        for snap in items:
            if snap.price is None:
                continue
            if rule.max_price is not None and snap.price > rule.max_price:
                continue
            if rule.min_price is not None and snap.price < rule.min_price:
                continue
            if snap.title and any(kw and kw in snap.title for kw in (rule.exclude_keywords or [])):
                continue
            if rule.require_badge:
                card_text = snap.extra.get("text") or snap.title
                if rule.require_badge not in card_text:
                    continue
            if self.db.is_seen(rule.platform, snap.item_id, rule.name):
                continue
            new_items.append(snap)

        new_items = new_items[: rule.max_results]
        if not new_items:
            return

        to_send = []
        for snap in new_items:
            self.db.mark_seen(rule.platform, snap.item_id, rule.name,
                              snap.title, snap.price, snap.url)
            dedup_key = f"kw|{rule.name}|{snap.item_id}"
            if self.db.alert_sent_recently(dedup_key, self.cooldown):
                continue
            self.db.log_alert(dedup_key)
            to_send.append(snap)

        if not to_send:
            return
        if len(to_send) == 1:
            title, md = self.format_keyword_alert(rule, to_send[0])
        else:
            title, md = self.format_digest(rule, to_send)
        self.notifier.send(title, md)

    def format_keyword_alert(self, rule: ItemRule, snap: Snapshot) -> tuple:
        pname = PLATFORM_NAMES.get(snap.platform, snap.platform)
        title = f"{pname}监控·新商品 | {rule.name}"
        lines = [
            f"**🆕 {pname} · 发现新商品**",
            f"**{clean_title(snap.title)}**",
            f"- 价格：**{snap.price_text}**"
            + (f"（上限 ¥{rule.max_price:,.2f}）" if rule.max_price else ""),
            f"- [点击查看]({snap.url})",
        ]
        return title, "\n".join(lines) + FOOTER

    def format_digest(self, rule: ItemRule, snaps: list) -> tuple:
        pname = PLATFORM_NAMES.get(snaps[0].platform, snaps[0].platform)
        title = f"{pname}监控·发现{len(snaps)}条新商品 | {rule.name}"
        lines = [f"**🆕 {pname} · 「{rule.name}」发现 {len(snaps)} 条新商品**", ""]
        for i, snap in enumerate(snaps, 1):
            lines.append(
                f"{i}. [{clean_title(snap.title, 60)}]({snap.url}) —— **{snap.price_text}**"
            )
        return title, "\n".join(lines) + FOOTER


def clean_title(text: str, limit: int = 80) -> str:
    text = (text or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")
