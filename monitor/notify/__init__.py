"""通知渠道管理。"""
from __future__ import annotations

import logging

from .console import ConsoleChannel
from .dingtalk import DingTalkChannel
from .wecom import WeComChannel
from .webhook import WebhookChannel

log = logging.getLogger("notify")

CHANNELS = {
    "dingtalk": DingTalkChannel,
    "wecom": WeComChannel,
    "webhook": WebhookChannel,
}

# 配置里还是占位文本时直接跳过，避免每次提醒都去请求无效地址
PLACEHOLDER_HINTS = ("在这里粘贴", "你的TOKEN", "你的KEY", "example.com")


class Notifier:
    def __init__(self, channels_cfg: list | None = None, console: bool = True):
        self.channels = []
        if console:
            self.channels.append(ConsoleChannel())
        for cfg in channels_cfg or []:
            data = dict(cfg)
            kind = data.pop("type", None)
            cls = CHANNELS.get(kind)
            if cls is None:
                log.warning("未知通知渠道类型: %r（已跳过）", kind)
                continue
            if any(isinstance(v, str) and any(h in v for h in PLACEHOLDER_HINTS)
                   for v in data.values()):
                log.warning("通知渠道 %s 还是占位配置，已跳过（请编辑 config.yaml 填真实地址）", kind)
                continue
            try:
                self.channels.append(cls(**data))
            except Exception as exc:
                log.warning("通知渠道 %s 初始化失败: %s", kind, exc)

    def send(self, title: str, markdown: str) -> None:
        for ch in self.channels:
            name = type(ch).__name__
            try:
                ch.send(title, markdown)
                log.info("已推送 [%s] %s", name, title)
            except Exception as exc:
                log.error("推送失败 [%s]: %s", name, exc)
