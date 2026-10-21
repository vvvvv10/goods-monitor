"""平台适配器工厂（按需懒加载，避免未启用的平台也初始化浏览器）。"""
from __future__ import annotations

from .base import BasePlatform, PlatformContext


def build_platform(name: str, ctx: PlatformContext) -> BasePlatform:
    if name == "jd":
        from .jd import JDPlatform
        return JDPlatform(ctx)
    if name == "taobao":
        from .taobao import TaobaoPlatform
        return TaobaoPlatform(ctx)
    if name == "pdd":
        from .pdd import PDDPlatform
        return PDDPlatform(ctx)
    if name == "xianyu":
        from .xianyu import XianyuPlatform
        return XianyuPlatform(ctx)
    if name == "mock":
        from .mock import MockPlatform
        return MockPlatform(ctx)
    raise SystemExit(f"未知平台: {name}")
