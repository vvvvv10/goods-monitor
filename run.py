#!/usr/bin/env python3
"""deal-monitor 命令行入口。

常用命令：
  python run.py run                    启动监控（Ctrl+C 停止，含 OKX 每日日报）
  python run.py once [平台]            立刻执行一轮检查（调试用）
  python run.py check <商品链接>       单独检查一个商品
  python run.py crypto [--dry-run] [--mock]   OKX 虚拟币日报（--mock 离线演示）
  python run.py web [--port 8765]      启动本地 Web 面板（浏览器看行情与规则）
  python run.py login <平台>           扫码登录（jd / taobao / pdd / xianyu）
  python run.py status                 查看各平台登录状态
  python run.py test-notify            发送测试通知（验证推送配置）
  python run.py list                   查看规则与最近抓取记录
  python run.py demo                   mock 自测（不联网验证整条链路）
"""
from __future__ import annotations

import argparse
import copy
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from monitor.config import load_config, resolve_path
from monitor.models import PLATFORM_NAMES


def setup_logging(cfg: dict) -> None:
    lcfg = cfg.get("logging") or {}
    logfile = resolve_path(lcfg.get("file", "data/monitor.log"))
    logfile.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, str(lcfg.get("level", "INFO")).upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def detect_platform(url: str) -> str:
    u = url.lower()
    if "jd.com" in u or "3.cn/" in u:
        return "jd"
    if "yangkeduo.com" in u or "pinduoduo.com" in u:
        return "pdd"
    if "goofish.com" in u or "2.taobao.com" in u:
        return "xianyu"
    if "taobao.com" in u or "tmall.com" in u or "tb.cn" in u:
        return "taobao"
    raise SystemExit(f"无法从链接识别平台: {url}\n可用 --platform 手动指定 (jd/taobao/pdd/xianyu)")


# ---------------- 子命令 ----------------

def cmd_run(args, cfg: dict) -> None:
    setup_logging(cfg)
    from monitor.engine import Engine
    Engine(cfg).run_forever()


def cmd_once(args, cfg: dict) -> None:
    setup_logging(cfg)
    from monitor.engine import Engine
    eng = Engine(cfg)
    try:
        eng.run_once([args.platform] if args.platform else None)
    finally:
        eng.close()


def cmd_check(args, cfg: dict) -> None:
    setup_logging(cfg)
    from monitor.engine import Engine
    from monitor.models import ItemRule
    platform = args.platform or detect_platform(args.url)
    rule = ItemRule(name="手动检查", platform=platform, type="item", url=args.url)
    eng = Engine(cfg)
    try:
        adapter = eng.platform(platform)
        print(f"平台: {PLATFORM_NAMES[platform]}   商品ID: {adapter.extract_item_id(args.url)}")
        snap = adapter.fetch_item(rule)
        if snap is None:
            print("❌ 抓取失败（可能未登录、商品下架或页面结构变化，请看日志）")
            return
        print(f"标题: {snap.title}")
        print(f"价格: {snap.price_text}")
        if snap.stock is not None:
            print(f"有货: {'是' if snap.stock else '否'}")
        print(f"链接: {snap.url}")
    finally:
        eng.close()


def cmd_login(args, cfg: dict) -> None:
    from monitor.browser.login import login
    login(args.platform, resolve_path(cfg["browser"]["profiles_dir"]))


def cmd_status(args, cfg: dict) -> None:
    from monitor.browser.login import check_login
    profiles = resolve_path(cfg["browser"]["profiles_dir"])
    print("各平台登录状态：")
    for plat in ("jd", "taobao", "pdd", "xianyu"):
        try:
            ok, detail = check_login(plat, profiles)
        except Exception as exc:
            ok, detail = False, f"检测出错: {exc}"
        print(f"  {PLATFORM_NAMES[plat]:<4} {'✅ 已登录' if ok else '❌ 未登录'}  {detail}")


def cmd_test_notify(args, cfg: dict) -> None:
    setup_logging(cfg)
    from monitor.notify import Notifier
    n = Notifier(channels_cfg=cfg["notify"].get("channels"),
                 console=cfg["notify"].get("console", True))
    n.send("deal-monitor 监控测试通知",
           "**✅ 监控通知测试成功**\n如果你在手机上看到这条消息，说明推送配置正确。\n\n— deal-monitor 价格监控")


def cmd_crypto(args, cfg: dict) -> None:
    """OKX 虚拟币日报：--dry-run 只打印不发送；--mock 用离线示例数据。"""
    setup_logging(cfg)
    from monitor.crypto.job import CryptoDailyJob
    from monitor.notify import Notifier
    n = Notifier(channels_cfg=cfg["notify"].get("channels"),
                 console=cfg["notify"].get("console", True))
    job = CryptoDailyJob(cfg, n)
    try:
        job.run(dry_run=args.dry_run, mock=args.mock)
    except Exception as exc:
        raise SystemExit(
            f"❌ OKX 日报执行失败: {exc}\n"
            "提示：程序默认走国内直连镜像；若持续失败，可在 config.yaml 的\n"
            "crypto.base_urls 换上新的镜像域名，或启动代理后设置 crypto.proxy。"
        )


def cmd_web(args, cfg: dict) -> None:
    """本地 Web 面板：OKX 日报 + 购物监控状态。"""
    import copy
    web_cfg = copy.deepcopy(cfg)
    web_cfg.setdefault("logging", {})["file"] = "data/web.log"
    setup_logging(web_cfg)
    from monitor.web.server import serve
    wcfg = cfg.get("web") or {}
    host = args.host or wcfg.get("host", "127.0.0.1")
    port = int(args.port or wcfg.get("port", 8765))
    serve(cfg, host=host, port=port)


def cmd_list(args, cfg: dict) -> None:
    from monitor.config import parse_rules
    from monitor.db import DB
    rules = parse_rules(cfg)
    print(f"\n启用中的规则（{len(rules)} 条）：")
    for r in rules:
        if r.type == "item":
            extra = f"url={r.url[:58]}  target={r.target_price}  drop={r.drop_percent}"
        else:
            extra = f"keyword={r.keyword!r}  max={r.max_price}  min={r.min_price}"
        print(f"  [{PLATFORM_NAMES.get(r.platform, r.platform)}] {r.name} ({r.type})  {extra}")
    db = DB(resolve_path(cfg["storage"]["db_path"]))
    try:
        items = db.latest_items(15)
        if items:
            print(f"\n最近抓取记录（最新在前）：")
            for it in items:
                price = f"¥{it['price']:,.2f}" if it["price"] is not None else "无报价"
                title = (it["title"] or it["item_id"])[:40]
                print(f"  {PLATFORM_NAMES.get(it['platform'], it['platform']):<4} {price:>12}  {title}")
        print(f"\n24 小时内提醒次数: {db.alerts_count()}")
        print(f"关键词已去重商品数: {db.seen_count()}")
    finally:
        db.close()


def cmd_demo(args, cfg: dict) -> None:
    """不联网自测：mock 平台 → 规则判断 → 终端通知 → 数据库记录。"""
    import time
    setup_logging(cfg)
    from monitor.engine import Engine
    demo_cfg = copy.deepcopy(cfg)
    demo_cfg["storage"] = {"db_path": "data/demo.db"}
    demo_cfg["notify"] = {"console": True, "channels": []}
    demo_cfg["platforms"] = {"mock": {"enabled": True, "interval": 5, "jitter": 0.1}}
    demo_cfg["alert_cooldown_hours"] = 0.0001  # 演示时几乎不冷却
    demo_cfg["rules"] = [
        {"name": "自测-指定商品", "platform": "mock", "type": "item",
         "url": "mock://item/1", "target_price": 900, "drop_percent": 3},
        {"name": "自测-关键词", "platform": "mock", "type": "keyword",
         "keyword": "游戏本", "max_price": 980},
    ]
    print("=== 自测模式：不联网，模拟完整流程 ===\n"
          "第 1 轮：抓取模拟商品价格 & 搜索模拟新商品\n"
          "第 2 轮：再次检查（验证价格变化提醒与去重逻辑）\n")
    eng = Engine(demo_cfg)
    try:
        eng.run_once(["mock"])
        print("\n---- 等待 3 秒后执行第 2 轮 ----\n")
        time.sleep(3)
        eng.run_once(["mock"])
        print("\n=== 自测完成。上面的 🔔 输出就是提醒消息的样子 ===")
        print("真实推送内容会同时发到钉钉/企业微信（配置后），并在 data/demo.db 留下记录。")
    finally:
        eng.close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="deal-monitor · 京东/淘宝/拼多多/闲鱼 价格监控",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--config", default=None, help="配置文件路径（默认 ./config.yaml）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("run", help="启动监控循环")
    p_once = sub.add_parser("once", help="立刻执行一轮检查（调试用）")
    p_once.add_argument("platform", nargs="?", choices=["jd", "taobao", "pdd", "xianyu", "mock"])
    p_check = sub.add_parser("check", help="单独检查一个商品链接")
    p_check.add_argument("url")
    p_check.add_argument("--platform", choices=["jd", "taobao", "pdd", "xianyu"], default=None)
    p_login = sub.add_parser("login", help="扫码登录并保存会话")
    p_login.add_argument("platform", choices=["jd", "taobao", "pdd", "xianyu"])
    sub.add_parser("status", help="查看各平台登录状态")
    sub.add_parser("test-notify", help="发送测试通知")
    p_crypto = sub.add_parser("crypto", help="立即执行一次 OKX 虚拟币日报")
    p_crypto.add_argument("--dry-run", action="store_true", help="只打印日报，不发送")
    p_crypto.add_argument("--mock", action="store_true", help="离线示例数据演示（不联网）")
    p_web = sub.add_parser("web", help="启动本地 Web 面板")
    p_web.add_argument("--host", default=None, help="监听地址（默认取 config.yaml web.host）")
    p_web.add_argument("--port", type=int, default=None, help="监听端口（默认 8765）")
    sub.add_parser("list", help="查看规则与最近记录")
    sub.add_parser("demo", help="mock 自测（不联网）")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return

    cfg = load_config(args.config)
    handlers = {
        "run": cmd_run, "once": cmd_once, "check": cmd_check,
        "login": cmd_login, "status": cmd_status, "test-notify": cmd_test_notify,
        "crypto": cmd_crypto, "web": cmd_web, "list": cmd_list, "demo": cmd_demo,
    }
    handlers[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
