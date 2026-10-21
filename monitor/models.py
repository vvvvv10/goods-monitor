"""数据模型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PLATFORM_NAMES = {
    "jd": "京东",
    "taobao": "淘宝",
    "pdd": "拼多多",
    "xianyu": "闲鱼",
    "mock": "自测(mock)",
}


@dataclass
class ItemRule:
    """监控规则。

    type=item    监控指定商品：按 target_price / drop_percent 触发提醒
    type=keyword 监控关键词搜索：新出现的、符合价格区间的商品触发提醒
    """

    name: str
    platform: str
    type: str = "item"
    enabled: bool = True

    # ---- type=item ----
    url: str = ""
    target_price: Optional[float] = None   # 现价 <= target_price 时提醒
    drop_percent: Optional[float] = None   # 相对上次记录价格降幅 >= drop_percent 时提醒

    # ---- type=keyword ----
    keyword: str = ""
    max_price: Optional[float] = None      # 只关注价格 <= max_price 的商品
    min_price: Optional[float] = None      # 过滤掉低于此价的可疑结果（可选）
    exclude_keywords: list = field(default_factory=list)  # 标题包含即忽略，如 ["求购", "回收"]
    require_badge: Optional[str] = None    # 卡片文字必须包含该字样，如 "百亿补贴"
    max_results: int = 15                  # 每次最多上报多少条新商品

    def __post_init__(self) -> None:
        if self.type not in ("item", "keyword"):
            raise ValueError(f"规则「{self.name}」: type 必须是 item 或 keyword")
        if self.platform not in PLATFORM_NAMES:
            raise ValueError(f"规则「{self.name}」: platform 必须是 {list(PLATFORM_NAMES)} 之一")
        if self.type == "item" and not self.url:
            raise ValueError(f"规则「{self.name}」: type=item 时缺少 url")
        if self.type == "keyword" and not self.keyword:
            raise ValueError(f"规则「{self.name}」: type=keyword 时缺少 keyword")


@dataclass
class Snapshot:
    """一次抓取得到的商品快照。"""

    platform: str
    item_id: str
    title: str
    price: Optional[float]
    url: str
    stock: Optional[bool] = None
    extra: dict = field(default_factory=dict)

    @property
    def price_text(self) -> str:
        if self.price is None:
            return "无报价"
        return f"¥{self.price:,.2f}"
