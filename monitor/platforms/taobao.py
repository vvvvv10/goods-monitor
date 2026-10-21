"""淘宝/天猫适配器：浏览器 + 登录态 + DOM 解析。"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from ..models import ItemRule, Snapshot
from .base import (BasePlatform, clean_text, collect_anchor_items, parse_price,
                   snapshot_from_card)

LOGIN_HINTS = ("login.taobao", "login.tmall", "havana", "passport")


class TaobaoPlatform(BasePlatform):
    name = "taobao"
    display = "淘宝"
    needs_browser = True

    @classmethod
    def extract_item_id(cls, url: str) -> str:
        m = re.search(r"[?&]id=(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/(\d{9,})", url)
        if m:
            return m.group(1)
        raise SystemExit(f"无法从链接解析淘宝商品ID: {url}")

    def _login_wall(self, page) -> bool:
        if any(h in page.url.lower() for h in LOGIN_HINTS):
            self.log.error("淘宝未登录（或登录已过期）：请运行  python run.py login taobao")
            return True
        return False

    def fetch_item(self, rule: ItemRule) -> Optional[Snapshot]:
        item_id = self.extract_item_id(rule.url)
        url = f"https://item.taobao.com/item.htm?id={item_id}"
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            page.wait_for_timeout(3000)
            if self._login_wall(page):
                return None

            price = None
            for sel in ('[class*="priceText"]', '[class*="Price--priceText"]',
                        '[class*="Price--price"]', 'span[class*="price"]'):
                try:
                    el = page.query_selector(sel)
                    if el:
                        price = parse_price(el.inner_text())
                        if price:
                            break
                except Exception:
                    continue

            title = ""
            for sel in ('meta[property="og:title"]', "h1"):
                try:
                    el = page.query_selector(sel)
                    if el:
                        title = el.get_attribute("content") or el.inner_text()
                        if title:
                            break
                except Exception:
                    continue
            title = (title or page.title()).strip()

            body = ""
            try:
                body = page.inner_text("body")
            except Exception:
                pass
            if price is None:
                price = parse_price(body)

            stock = None
            if any(k in body for k in ("缺货", "补货中", "已售罄", "此商品已下架")):
                stock = False

            return Snapshot(platform=self.name, item_id=item_id, title=title,
                            price=price, url=page.url, stock=stock,
                            extra={"raw": clean_text(body[:200])})
        finally:
            page.close()

    def search(self, rule: ItemRule) -> list:
        url = f"https://s.taobao.com/search?q={quote(rule.keyword)}"
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            page.wait_for_timeout(3500)
            if self._login_wall(page):
                return []
            try:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(1500)
            except Exception:
                pass
            cards = collect_anchor_items(page, "item.htm", r"[?&]id=(\d+)")
            if not cards:
                cards = collect_anchor_items(page, "detail.tmall.com/item", r"[?&]id=(\d+)")
            snaps = [snapshot_from_card(self.name, c) for c in cards]
            return [s for s in snaps if s and s.price is not None]
        finally:
            page.close()
