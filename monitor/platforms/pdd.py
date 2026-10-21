"""拼多多适配器：浏览器 + 登录态（移动版页面）。"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from ..models import ItemRule, Snapshot
from .base import (BasePlatform, clean_text, collect_anchor_items, parse_price,
                   snapshot_from_card)

_JS_PRICE_CANDIDATES = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('span, div, p, b, strong')) {
    if (el.children.length > 0) continue;
    const t = (el.innerText || '').trim();
    if (/^[¥￥]\\s?\\d+(\\.\\d+)?$/.test(t) && t.length <= 12) out.push(t);
    if (out.length >= 8) break;
  }
  return out;
}
"""


class PDDPlatform(BasePlatform):
    name = "pdd"
    display = "拼多多"
    needs_browser = True

    @classmethod
    def extract_item_id(cls, url: str) -> str:
        m = re.search(r"goods_id=(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"(\d{8,})", url)
        if m:
            return m.group(1)
        raise SystemExit(f"无法从链接解析拼多多商品ID: {url}")

    def _login_wall(self, page) -> bool:
        u = page.url.lower()
        if "login" in u:
            self.log.error("拼多多未登录（或登录已过期）：请运行  python run.py login pdd")
            return True
        return False

    @staticmethod
    def _price_from_content(content: str) -> Optional[float]:
        """从页面内嵌 JSON 里取价格（单位：分）。"""
        for pat in (r'"minOnSaleGroupPrice":(\d+)', r'"minGroupPrice":(\d+)',
                    r'"minNormalPrice":(\d+)', r'"linePrice":(\d+)'):
            m = re.search(pat, content)
            if m:
                try:
                    val = float(m.group(1)) / 100.0
                except ValueError:
                    continue
                if val > 0:
                    return val
        return None

    def fetch_item(self, rule: ItemRule) -> Optional[Snapshot]:
        item_id = self.extract_item_id(rule.url)
        url = f"https://mobile.yangkeduo.com/goods.html?goods_id={item_id}"
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            page.wait_for_timeout(4000)
            if self._login_wall(page):
                return None

            content = page.content()
            price = self._price_from_content(content)
            if price is None:
                try:
                    cands = page.evaluate(_JS_PRICE_CANDIDATES) or []
                    vals = [v for v in (parse_price(t) for t in cands) if v]
                    if vals:
                        price = min(vals)
                except Exception:
                    pass

            m = re.search(r"<title>([^<]*)</title>", content)
            title = (m.group(1) if m else "").strip()
            for suffix in ("-拼多多", "_拼多多", "【拼多多】", "拼多多"):
                title = title.replace(suffix, "").strip()

            return Snapshot(platform=self.name, item_id=item_id, title=title,
                            price=price, url=page.url,
                            extra={"raw": clean_text(content[:200])})
        finally:
            page.close()

    def search(self, rule: ItemRule) -> list:
        url = f"https://mobile.yangkeduo.com/search_result.html?search_key={quote(rule.keyword)}"
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            page.wait_for_timeout(4500)
            if self._login_wall(page):
                return []
            for _ in range(2):
                try:
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(1200)
                except Exception:
                    break
            cards = collect_anchor_items(page, "goods_id=", r"goods_id=(\d+)")
            snaps = [snapshot_from_card(self.name, c) for c in cards]
            return [s for s in snaps if s and s.price is not None]
        finally:
            page.close()
