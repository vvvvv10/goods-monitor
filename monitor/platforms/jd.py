"""京东适配器：浏览器 + 登录态。

京东网页端对未登录访客会隐藏价格（显示成 ¥1??9 这种打码形式），
登录后价格完整；部分场景价格数字会用私有区字符混淆，用页面里的
spiderFont 字体自动解码（见 jdfont.py）。
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote

from ..models import ItemRule, Snapshot
from .base import BasePlatform, clean_text, parse_price
from .jdfont import SpiderFontDecoder, extract_font_url, has_pua

_JS_READ_PRICE = """
() => {
  const els = document.querySelectorAll('.price, .price em');
  for (const el of els) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 16) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && /[0-9?]/.test(t)) return t;
  }
  return '';
}
"""

_JS_SEARCH_ITEMS = """
() => {
  const out = [];
  for (const li of document.querySelectorAll('li.gl-item, .gl-item')) {
    const sku = li.getAttribute('data-sku');
    if (!sku) continue;
    const a = li.querySelector('.p-name a');
    const pe = li.querySelector('.p-price');
    const iEl = li.querySelector('.p-price i');
    let priceText = '';
    if (iEl) priceText = (iEl.getAttribute('data-price') || iEl.innerText || '').trim();
    if (!priceText && pe) priceText = (pe.innerText || '').trim();
    out.push({
      sku,
      url: a ? (a.href || '') : '',
      name: a ? (a.getAttribute('title') || a.innerText || '') : '',
      priceText,
      text: (li.innerText || '').replace(/\\s+/g, ' ').slice(0, 300),
    });
    if (out.length >= 60) break;
  }
  return out;
}
"""


def _parse_price_text(raw: str) -> Optional[float]:
    """解析价格文本（自动补 ¥ 号 / 处理裸数字）。"""
    if not raw:
        return None
    text = raw if any(ch in raw for ch in "¥￥") else "¥" + raw
    price = parse_price(text)
    if price is None:
        m = re.search(r"([0-9]+(?:\.[0-9]{1,2})?)", text)
        if m:
            try:
                price = float(m.group(1))
            except ValueError:
                price = None
    return price


class JDPlatform(BasePlatform):
    name = "jd"
    display = "京东"
    needs_browser = True

    def __init__(self, ctx):
        super().__init__(ctx)
        from ..config import resolve_path
        self.fonts = SpiderFontDecoder(resolve_path("data/fonts"))

    @classmethod
    def extract_item_id(cls, url: str) -> str:
        m = re.search(r"item\.(?:m\.)?jd\.com/(?:product/)?(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"(\d{6,})", url)
        if m:
            return m.group(1)
        raise SystemExit(f"无法从链接解析京东商品ID: {url}")

    # ---------- 指定商品 ----------

    def fetch_item(self, rule: ItemRule) -> Optional[Snapshot]:
        item_id = self.extract_item_id(rule.url)
        url = f"https://item.m.jd.com/product/{item_id}.html"
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            page.wait_for_timeout(4000)

            html = ""
            try:
                html = page.content()
            except Exception:
                pass

            raw = ""
            try:
                raw = (page.evaluate(_JS_READ_PRICE) or "").strip()
            except Exception:
                pass

            price: Optional[float] = None
            note = ""
            if not raw:
                note = "页面未找到价格元素"
            elif has_pua(raw):
                decoded = self.fonts.decode(raw, extract_font_url(html))
                if not has_pua(decoded):
                    price = _parse_price_text(decoded)
                    note = f"字体解码: {raw!r} -> {decoded!r}"
                else:
                    note = "价格含特殊字符，字体解码失败"
            elif "?" in raw:
                note = "价格被打码（京东对未登录访客隐藏价格），请运行: python run.py login jd"
            else:
                price = _parse_price_text(raw)

            title = ""
            m = re.search(r"<title>([^<]*)</title>", html or "")
            if m:
                title = re.sub(r"【[^】]*】", " ", m.group(1))
                title = re.sub(r"[-_]*(京\s*东|JD\.?COM|京东商城).*$", "", title).strip()
            if not title:
                try:
                    title = page.title().strip()
                except Exception:
                    pass

            stock = None
            try:
                body = page.inner_text("body")
                if any(k in body for k in ("无货", "缺货", "已下柜")):
                    stock = False
                elif "现货" in body:
                    stock = True
            except Exception:
                pass

            if note:
                self.log.warning("「%s」%s", rule.name, note)
            return Snapshot(platform=self.name, item_id=item_id, title=title,
                            price=price, url=url, stock=stock,
                            extra={"raw_price": raw})
        finally:
            page.close()

    # ---------- 关键词搜索（需要登录） ----------

    def search(self, rule: ItemRule) -> list:
        url = f"https://search.jd.com/Search?keyword={quote(rule.keyword)}&enc=utf-8"
        page = self.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.nav_timeout)
            page.wait_for_timeout(3500)
            if "passport.jd.com" in page.url:
                self.log.error("京东搜索需要登录：请运行  python run.py login jd")
                return []
            try:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(1200)
            except Exception:
                pass
            items = page.evaluate(_JS_SEARCH_ITEMS) or []
            snaps = []
            for it in items:
                price = _parse_price_text(it.get("priceText") or "")
                snaps.append(Snapshot(
                    platform=self.name,
                    item_id=str(it.get("sku")),
                    title=(it.get("name") or "").strip()[:120],
                    price=price,
                    url=it.get("url") or "",
                    extra={"text": clean_text(it.get("text") or "", 300)},
                ))
            priced = [s for s in snaps if s.price is not None]
            if snaps and not priced:
                self.log.warning("京东搜索有结果但没解析到价格（可能是全网段对 p.3.cn 价格接口不通），"
                                 "京东关键词监控可能不可用，建议用指定商品监控")
            return priced
        finally:
            page.close()
