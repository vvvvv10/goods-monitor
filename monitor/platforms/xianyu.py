"""闲鱼适配器：浏览器 + 登录态 + DOM 解析（风控最严，频率放慢）。"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from ..models import ItemRule, Snapshot
from .base import (BasePlatform, clean_text, collect_anchor_items, parse_price,
                   snapshot_from_card)


class XianyuPlatform(BasePlatform):
    name = "xianyu"
    display = "闲鱼"
    needs_browser = True

    @classmethod
    def extract_item_id(cls, url: str) -> str:
        m = re.search(r"[?&]id=(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"(\d{9,})", url)
        if m:
            return m.group(1)
        raise SystemExit(f"无法从链接解析闲鱼商品ID: {url}")

    def _blocked(self, page) -> bool:
        u = page.url.lower()
        if "login" in u and "goofish" in u:
            self.log.error("闲鱼未登录（或登录已过期）：请运行  python run.py login xianyu")
            return True
        try:
            body = page.inner_text("body")
        except Exception:
            body = ""
        if "非法访问" in body or "请使用正常浏览器" in body:
            self.log.error(
                "闲鱼触发了风控（非法访问）。建议：稍后重试；或把 config.yaml 的 "
                "browser.headless 改为 false 再跑；并保持低频访问。")
            return True
        return False

    def fetch_item(self, rule: ItemRule) -> Optional[Snapshot]:
        item_id = self.extract_item_id(rule.url)
        url = f"https://www.goofish.com/item?id={item_id}"
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            page.wait_for_timeout(3000)
            if self._blocked(page):
                return None

            title = ""
            for sel in ('meta[property="og:title"]', '[class*="title"]', "h1"):
                try:
                    el = page.query_selector(sel)
                    if el:
                        title = el.get_attribute("content") or el.inner_text()
                        if title:
                            break
                except Exception:
                    continue
            title = (title or page.title()).replace("_闲鱼", "").strip()

            price = None
            for sel in ('[class*="price"]',):
                try:
                    el = page.query_selector(sel)
                    if el:
                        price = parse_price(el.inner_text())
                        if price:
                            break
                except Exception:
                    continue
            body = ""
            try:
                body = page.inner_text("body")
            except Exception:
                pass
            if price is None:
                price = parse_price(body)

            return Snapshot(platform=self.name, item_id=item_id, title=title,
                            price=price, url=page.url,
                            extra={"raw": clean_text(body[:200])})
        finally:
            page.close()

    def search(self, rule: ItemRule) -> list:
        q = quote(rule.keyword)
        urls = [
            f"https://www.goofish.com/search?q={q}&sortField=create&sortValue=desc",
            f"https://www.goofish.com/search?q={q}",
        ]
        page = self.new_page()
        try:
            cards = []
            for url in urls:
                page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
                page.wait_for_timeout(3500)
                if self._blocked(page):
                    return []
                for _ in range(2):
                    try:
                        page.mouse.wheel(0, 1600)
                        page.wait_for_timeout(1200)
                    except Exception:
                        break
                cards = collect_anchor_items(page, "/item", r"[?&]id=(\d+)")
                if cards:
                    break
            snaps = [snapshot_from_card(self.name, c) for c in cards]
            return [s for s in snaps if s and s.price is not None]
        finally:
            page.close()
