"""探针脚本：检查各平台页面在真实 Chrome 下的可达性、登录墙、价格数据来源。

用法: .venv/bin/python scripts/probe_platforms.py
仅用于开发调试。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

TMP = Path("/private/var/folders/38/x3wls7j13zb1ltc5fgv9np_40000gn/T/opencode")

TARGETS = [
    ("jd_item", "https://item.m.jd.com/product/100012043978.html"),
    ("jd_search", "https://search.jd.com/Search?keyword=%E8%8C%85%E5%8F%B0&enc=utf-8"),
    ("goofish_search", "https://www.goofish.com/search?q=iPhone"),
    ("pdd_goods", "https://mobile.yangkeduo.com/goods.html?goods_id=432867921015"),
    ("taobao_item", "https://item.taobao.com/item.htm?id=654941758134"),
]

PRICE_HINTS = ("price", "Price", "wareBusiness", "prices", "warebusiness")


def probe(name: str, url: str, browser) -> None:
    page = browser.new_page()
    captured: list = []

    def on_response(resp):
        u = resp.url
        if any(h in u for h in PRICE_HINTS) and len(captured) < 4:
            try:
                body = resp.text()
                if body and len(body) < 300000:
                    captured.append((u[:170], body[:500]))
            except Exception:
                pass

    page.on("response", on_response)
    try:
        resp = page.goto(url, timeout=35000, wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        info = page.evaluate(
            """() => ({
                title: document.title,
                url: location.href,
                text: document.body ? document.body.innerText.slice(0, 260) : '',
                glitem: document.querySelectorAll('li.gl-item').length,
                jdskus: document.querySelectorAll('[data-sku]').length,
                goofishItems: document.querySelectorAll('a[href*="item?id"], a[href*="item.htm"]').length,
                pddGoods: document.querySelectorAll('a[href*="goods_id"], a[href*="goods.html"]').length,
                priceEls: Array.from(document.querySelectorAll('[class*=price i]'))
                    .slice(0, 8)
                    .map(e => (e.className || '').slice(0, 60) + ' :: ' + (e.innerText || '').slice(0, 50)),
            })"""
        )
        print(f"\n### {name}")
        print("status:", resp.status if resp else None)
        print("final url:", info["url"][:150])
        print("title:", info["title"][:80])
        print("text:", repr(info["text"][:220]))
        print("counts: glitem=%s jdskus=%s goofish=%s pdd=%s" % (
            info["glitem"], info["jdskus"], info["goofishItems"], info["pddGoods"]))
        for pe in info["priceEls"][:4]:
            print("  priceEl:", pe[:110])
        for u, b in captured[:3]:
            print("NET:", u)
            print("    ", repr(b[:260]))
    except Exception as exc:
        print(f"\n### {name} ERROR: {exc}")
    finally:
        page.close()


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(TMP / "probe_profile"),
            headless=True,
            channel="chrome",
            locale="zh-CN",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
        )
        for name, url in TARGETS:
            probe(name, url, browser)
        browser.close()


if __name__ == "__main__":
    main()
