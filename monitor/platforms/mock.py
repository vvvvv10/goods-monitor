"""mock 平台：不联网的端到端自测，用于验证 DB/规则/通知 整条链路。"""
from __future__ import annotations

import random
from typing import Optional

from ..models import ItemRule, Snapshot
from .base import BasePlatform

# 模拟数据集（常驻内存，模拟“商品池会不断出现新商品”）
_POOL: dict = {}
_CALLS = 0


def _new_item(iid: str) -> None:
    _POOL[iid] = (
        f"【自测】九成新 游戏本 RTX4060 16G #{iid}",
        round(random.uniform(650, 980), 2),
    )


class MockPlatform(BasePlatform):
    name = "mock"
    display = "自测"

    @classmethod
    def extract_item_id(cls, url: str) -> str:
        return url.rstrip("/").split("/")[-1]

    def fetch_item(self, rule: ItemRule) -> Optional[Snapshot]:
        global _CALLS
        _CALLS += 1
        item_id = self.extract_item_id(rule.url)
        price = max(600.0, 1250.0 - _CALLS * 130.0 - random.uniform(0, 40))
        stock = _CALLS % 4 != 0
        return Snapshot(
            platform="mock",
            item_id=item_id,
            title="【自测】示例商品 · 旗舰手机 256G",
            price=round(price, 2),
            url=rule.url,
            stock=stock,
        )

    def search(self, rule: ItemRule) -> list:
        if not _POOL:
            for i in range(2):
                _new_item(str(9000 + i))
        for _ in range(random.randint(1, 2)):
            _new_item(str(9000 + len(_POOL) + random.randint(0, 1)))
        items = []
        for iid, (title, price) in list(_POOL.items())[-8:]:
            items.append(Snapshot(
                platform="mock",
                item_id=iid,
                title=title,
                price=price,
                url=f"mock://item/{iid}",
                extra={"text": "百亿补贴" if iid == "9000" else "个人闲置"},
            ))
        random.shuffle(items)
        return items
