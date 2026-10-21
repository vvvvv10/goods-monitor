"""京东价格字体混淆解码器。

京东会把部分价格数字替换成私有区(PUA)字符，并用动态生成的字体
(如 spiderFontFormal_*.otf) 渲染成数字。该字体的 cmap 把每个 PUA
码点映射到以英文数字命名的 glyph（zero/nine/...），因此可以离线解码。
字体文件按 URL 缓存到 data/fonts/。
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from pathlib import Path
from typing import Optional

import requests

try:
    from fontTools.ttLib import TTFont
except ImportError:  # pragma: no cover
    TTFont = None

log = logging.getLogger("platform.jdfont")

DIGIT_NAMES = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def has_pua(text: str) -> bool:
    return any(0xE000 <= ord(c) <= 0xF8FF for c in text or "")


def extract_font_url(html: str) -> Optional[str]:
    """从页面 HTML 里找 spiderFont 字体 URL。"""
    m = re.search(r'"spiderFont"\s*:\s*"([^"]+)"', html or "")
    if m:
        return m.group(1).replace("\\u002F", "/").replace("\\/", "/")
    m = re.search(r"https?://[^\"'\s]*spiderFont[^\"'\s]*\.(?:otf|ttf|woff2?)", html or "")
    return m.group(0) if m else None


class SpiderFontDecoder:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._maps: dict = {}

    def get_map(self, url: str) -> dict:
        """返回 {PUA码点: 数字字符} 映射。"""
        if url in self._maps:
            return self._maps[url]
        mapping: dict = {}
        if TTFont is None:
            log.error("缺少 fonttools 依赖，无法解码京东价格字体："
                      ".venv/bin/pip install fonttools brotli")
        else:
            try:
                data = self._fetch_bytes(url)
                font = TTFont(io.BytesIO(data))
                for cp, gname in font.getBestCmap().items():
                    digit = DIGIT_NAMES.get(str(gname).lower())
                    if digit:
                        mapping[cp] = digit
                log.info("已加载京东价格字体，PUA映射 %d 条 (缓存: %s)",
                         len(mapping), url.split("/")[-1][:40])
            except Exception as exc:
                log.warning("解析京东价格字体失败: %s", exc)
        self._maps[url] = mapping
        return mapping

    def _fetch_bytes(self, url: str) -> bytes:
        key = hashlib.md5(url.encode()).hexdigest()[:16]
        cached = self.cache_dir / f"jd_spider_{key}.bin"
        if cached.exists():
            return cached.read_bytes()
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        cached.write_bytes(resp.content)
        return resp.content

    def decode(self, text: str, font_url: Optional[str]) -> str:
        """把价格文本里的 PUA 字符替换为真实数字。"""
        if not has_pua(text):
            return text
        if not font_url:
            log.warning("价格含特殊字符但未在页面中找到字体 URL，无法解码")
            return text
        mapping = self.get_map(font_url)
        if not mapping:
            return text
        return "".join(mapping.get(ord(c), c) for c in text)
