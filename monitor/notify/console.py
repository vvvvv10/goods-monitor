"""终端打印通知（本地兜底，程序开着就能看到）。"""
from __future__ import annotations


class ConsoleChannel:
    def send(self, title: str, markdown: str) -> None:
        print(
            f"\n{'=' * 62}\n🔔 {title}\n{'-' * 62}\n{markdown}\n{'=' * 62}",
            flush=True,
        )
