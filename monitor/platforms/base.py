"""平台适配器基类与公共工具。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from ..models import ItemRule, Snapshot

if TYPE_CHECKING:
    from ..browser.session import BrowserManager


@dataclass
class PlatformContext:
    browser: "BrowserManager"
    config: dict


class BasePlatform:
    name = ""
    display = ""
    needs_browser = False

    def __init__(self, ctx: PlatformContext):
        self.ctx = ctx
        self.cfg = ctx.config
        self.log = logging.getLogger(f"platform.{self.name}")

    def new_page(self):
        return self.ctx.browser.new_page(self.name)

    @property
    def nav_timeout(self) -> int:
        return int(self.cfg["browser"].get("navigation_timeout_ms", 30000))

    @classmethod
    def extract_item_id(cls, url: str) -> str:
        raise NotImplementedError

    def fetch_item(self, rule: ItemRule) -> Optional[Snapshot]:
        """抓取指定商品当前快照；失败返回 None。"""
        raise NotImplementedError

    def search(self, rule: ItemRule) -> list:
        """按关键词搜索，返回 Snapshot 列表。"""
        return []

    def close(self) -> None:
        pass


# ---------- 公共解析工具 ----------

PRICE_RE = re.compile(r"[¥￥]\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(万)?")


def parse_price(text: str) -> Optional[float]:
    """提取文本中第一个价格（支持 ¥1,234.56 / ¥1.2万）。"""
    if not text:
        return None
    m = PRICE_RE.search(text)
    if m:
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            return None
        return val * 10000 if m.group(2) else val
    m2 = re.search(r"(?:价格|售价|现价|到手价|券后)\s*[:：]?\s*([0-9]+(?:\.[0-9]{1,2})?)", text)
    return float(m2.group(1)) if m2 else None


def clean_text(text: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()[:limit]


_TITLE_BLACK = re.compile(
    r"^[¥￥\d.,+万千百\s\t·\-–—人个件次支付年月日天小时分钟秒评价已消费关注粉丝购买想要包邮面交]+$"
)


def pick_title(lines: list) -> str:
    """从卡片文本行里猜商品标题：取第一个像标题的行。"""
    for line in lines:
        if len(line) >= 6 and not _TITLE_BLACK.match(line):
            return line[:120]
    return lines[0][:120] if lines else ""


_JS_COLLECT = """
([hrefMarker, idRe, cap]) => {
  const re = new RegExp(idRe);
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="' + hrefMarker + '"]')) {
    const m = (a.href || '').match(re);
    if (!m) continue;
    const id = m[1];
    if (seen.has(id)) continue;
    seen.add(id);
    let box = a;
    if ((a.innerText || '').trim().length < 8 && a.parentElement) {
      let p = a.parentElement;
      for (let i = 0; i < 4 && p && (p.innerText || '').trim().length < 8; i++) {
        p = p.parentElement;
      }
      if (p) box = p;
    }
    out.push({ id, url: a.href, text: (box.innerText || a.innerText || '').slice(0, 400) });
    if (out.length >= cap) break;
  }
  return out;
}
"""


def collect_anchor_items(page, href_marker: str, id_regex: str, cap: int = 60) -> list:
    """收集页面中指向商品的锚点（id / 链接 / 所在卡片文本）。"""
    try:
        return page.evaluate(_JS_COLLECT, [href_marker, id_regex, cap]) or []
    except Exception:
        return []


def snapshot_from_card(platform: str, item: dict) -> Optional[Snapshot]:
    """从 collect_anchor_items 的结果构造 Snapshot（价格可能为 None）。"""
    text = item.get("text") or ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return Snapshot(
        platform=platform,
        item_id=str(item.get("id")),
        title=pick_title(lines),
        price=parse_price(text),
        url=item.get("url") or "",
        extra={"text": clean_text(text, 300)},
    )
