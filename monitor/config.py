"""配置加载与校验。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Union

import yaml

from .models import ItemRule

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "storage": {"db_path": "data/monitor.db"},
    "logging": {"file": "data/monitor.log", "level": "INFO"},
    "web": {"host": "127.0.0.1", "port": 8765},
    "notify": {"console": True, "channels": []},
    "alert_cooldown_hours": 6,
    "browser": {
        "headless": True,
        "channel": "chrome",
        "profiles_dir": "data/profiles",
        "navigation_timeout_ms": 30000,
    },
    "platforms": {
        "jd": {"enabled": True, "interval": 120, "jitter": 0.3},
        "taobao": {"enabled": True, "interval": 180, "jitter": 0.4},
        "pdd": {"enabled": True, "interval": 180, "jitter": 0.4},
        "xianyu": {"enabled": True, "interval": 300, "jitter": 0.5},
    },
    "crypto": {
        "enabled": False,
        "min_volume_usd": 2_000_000,
        "decline_days": 2,
        "new_coin_days": 5,
        "report_time": "00:10",
        "send_always": True,
        "max_items": 30,
        "base_urls": [],
        "proxy": "",
        "timeout": 12,
    },
    "rules": [],
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Union[str, Path, None] = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        raise SystemExit(
            f"找不到配置文件: {p}\n"
            f"请先复制示例配置再修改: cp config.example.yaml config.yaml"
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return _merge(DEFAULTS, raw)


def resolve_path(path_str: str) -> Path:
    """相对路径一律相对于项目根目录。"""
    p = Path(path_str)
    return p if p.is_absolute() else ROOT / p


def parse_rules(cfg: dict) -> list[ItemRule]:
    rules: list[ItemRule] = []
    for i, item in enumerate(cfg.get("rules") or [], 1):
        data = dict(item)
        data.setdefault("name", f"规则{i}")
        try:
            rule = ItemRule(**data)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"第 {i} 条规则配置错误: {exc}")
        if rule.enabled:
            rules.append(rule)
    return rules
