# deal-monitor · 购物 + 虚拟币监控

监控 **京东 / 淘宝 / 拼多多 / 闲鱼** 的商品价格：指定商品降价、关键词捡漏（含百亿补贴）；
并附带 **OKX 虚拟币日报**：每天自动筛选成交额达标、连续下跌的币种。
全部通过 **钉钉 / 企业微信** 推送到手机。

## 功能一览

- 📌 **指定商品监控**：贴商品链接，价格达到目标值 / 比上次降 X% / 重新有货时提醒
- 🔍 **关键词监控**：定时搜索，出现符合价格区间的新商品就提醒（闲鱼捡漏、百亿补贴蹲货）
- 🏷️ **百亿补贴过滤**：可要求商品卡片带「百亿补贴」角标才提醒
- 🔔 钉钉 / 企业微信 / 通用 webhook 推送，终端也会打印
- 🗂️ SQLite 记录价格历史；同一提醒默认 6 小时不重复（可调）
- 🤖 Playwright 驱动本机 Chrome 保留登录态；检查间隔带随机浮动，降低风控概率
- 📈 **OKX 虚拟币日报**：每天 00:10 推送「24h 成交额 ≥ 200 万美元 + 连续 2 日下跌」币种（详见下文）
- 🖥️ **本地 Web 面板**：浏览器查看 OKX 触发榜、监控规则与价格走势，可一键刷新行情 / 发送日报

## 平台支持情况（本机实测结论）

| 平台 | 指定商品 | 关键词 | 需要登录 | 说明 |
|------|:---:|:---:|:---:|------|
| 京东 | ✅ | ⚠️ | ✅ | 网页端对未登录访客隐藏价格（打码成 `¥1??9`）；价格特殊字符会用页面自带字体自动解码 |
| 淘宝 | ✅ | ✅ | ✅ | 商品页与搜索都需要登录 |
| 拼多多 | ✅ | ✅ | ✅ | 走移动版网页（mobile.yangkeduo.com） |
| 闲鱼 | ✅ | ✅ | ✅ | 风控最严，建议间隔 ≥5 分钟；提示"非法访问"时把 `browser.headless` 改为 `false` |

> ⚠️ 京东关键词搜索依赖的价格接口（p.3.cn）在部分网络下不通（比如本机网络），
> 京东关键词监控可能拿不到价格，建议京东以"指定商品监控"为主。
> 用 `python run.py check <链接>` 可随时验证抓取是否正常。

## 快速开始

准备工作：本机已装 Chrome（脚本直接用系统 Chrome，无需额外下载浏览器内核）。

所有命令在项目目录下用 `.venv/bin/python` 运行，也可以先 `source .venv/bin/activate` 之后直接用 `python`。

### 第 1 步：配置通知（以钉钉为例）

1. 钉钉群 → 群设置 → 机器人 → 添加机器人 → 「自定义」
2. 安全设置建议选 **「加签」**，复制 webhook 地址和密钥
3. 编辑 `config.yaml`：
   ```yaml
   notify:
     channels:
       - type: dingtalk
         webhook: "https://oapi.dingtalk.com/robot/send?access_token=..."
         secret: "SEC开头的那串密钥"
   ```
4. 测试：`.venv/bin/python run.py test-notify` —— 手机上应立刻收到消息

> 企业微信：群设置 → 群机器人 → 添加，把 webhook 填到 `channels` 里 `type: wecom`。
> 什么都不配也能用：提醒会打印在终端。

### 第 2 步：登录各平台（只需一次）

```bash
.venv/bin/python run.py login jd       # 京东（必须，否则看不到价格）
.venv/bin/python run.py login taobao   # 淘宝
.venv/bin/python run.py login pdd      # 拼多多
.venv/bin/python run.py login xianyu   # 闲鱼
.venv/bin/python run.py status         # 查看登录状态
```

弹出的浏览器窗口里**扫码登录**，成功后在终端按回车保存。
会话保存在 `data/profiles/` 下，长期有效；过期或风控时重新登录即可。
（登录时若监控正在后台跑，先 `./scripts/daemon.sh stop`。）

### 第 3 步：添加监控规则

编辑 `config.yaml` 的 `rules:` 部分（示例见 `config.example.yaml`），改好把 `enabled` 设为 `true`：

**指定商品（item）**

```yaml
- name: "京东-茅台飞天"
  platform: jd
  type: item
  url: "https://item.jd.com/100012043978.html"
  target_price: 2299     # 现价 ≤ 2299 时提醒（可省略）
  drop_percent: 3        # 或：比上次记录的价格降 ≥3% 时提醒（可省略）
  enabled: true
```

**关键词（keyword）**

```yaml
- name: "闲鱼-iPhone 15 Pro 低价"
  platform: xianyu
  type: keyword
  keyword: "iPhone 15 Pro 256G"
  min_price: 3000        # 可选：过滤太便宜的可疑结果
  max_price: 5200
  exclude_keywords: ["求购", "回收", "配件"]   # 可选
  require_badge: "百亿补贴"    # 可选：卡片必须带该字样（拼多多/淘宝常用）
  max_results: 15        # 每条规则单轮最多提醒多少条新商品
  enabled: true
```

### 第 4 步：运行

```bash
.venv/bin/python run.py list     # 查看生效中的规则
.venv/bin/python run.py once     # 立刻检查一轮（调试用，输出详细日志）
.venv/bin/python run.py run      # 前台运行（Ctrl+C 停止）

# 或者后台常驻：
./scripts/daemon.sh start        # 启动
./scripts/daemon.sh status       # 状态
./scripts/daemon.sh log          # 实时日志
./scripts/daemon.sh stop         # 停止
```

不配任何规则也可以先体验完整流程（不联网）：
```bash
.venv/bin/python run.py demo
```

## 虚拟币监控（OKX 日报）

监控 OKX 现货市场：每天 **00:10** 自动筛选「24h 成交额 ≥ 200 万美元」的 USDT 币对，
检测其中**连续 2 日收盘下跌**的币种，生成日报推送到钉钉 / 企业微信。

- 候选过滤：自动排除稳定币（USDC/USDT/DAI…）和杠杆代币（3L/3S/5L/5S）
- 🆕 **新币重点关注**：上市 ≤ 5 天且连续 2 日下跌的新币单独置顶展示，
  不受成交额门槛限制；自动排除 OKX 的代币化股票（`instCategory=3`）
- 日线按**北京时间 0 点**切分（与每日 00:10 的统计时间正好对齐）
- 测试阶段默认 `send_always: true`（无论是否触发都发日报）；
  正式启用后改成 `false`，只在有币种触发时才推送（新币触发也算）

> ✅ **免翻墙直连**：程序内置 OKX 国内线路镜像域名（实测直连可用，完整转发官方 /api/v5 行情接口）。
> 这些域名会不定期轮换，若某天失效，把 `config.yaml` 里 `crypto.base_urls` 换成新的即可；
> 如果你开着代理，程序会自动探测（7890/7897 等）并改走官方域名，双保险。

常用命令：

```bash
.venv/bin/python run.py crypto --mock --dry-run   # 离线示例数据，预览日报长什么样（不联网）
.venv/bin/python run.py crypto --dry-run          # 拉真实行情但只打印，不发送
.venv/bin/python run.py crypto                    # 立即执行并发送日报
```

配置（`config.yaml` 的 `crypto` 段）：

```yaml
crypto:
  enabled: true
  min_volume_usd: 2000000   # 24h 成交额下限（美元）
  decline_days: 2           # 连续下跌天数触发
  new_coin_days: 5          # 新币关注：上市 ≤N 天（0=关闭）
  report_time: "00:10"      # 每日发送时间
  send_always: true         # 测试阶段：始终发送；正式后 false
  max_items: 30             # 触发列表最多展示条数
  base_urls: []             # 数据源留空=内置镜像+官方自动选择；可覆写
  proxy: ""                 # 留空=自动探测；也可填 http://127.0.0.1:7890
```

`run.py run` / daemon 启动后会同时跑购物监控与虚拟币日报，无需额外操作；
发送失败会自动每 15 分钟重试（最多 4 次），不会漏掉当天日报。

## 本地 Web 面板

在浏览器里查看监控状态（只监听本机 `127.0.0.1`，不会暴露到局域网）：

```bash
./scripts/web.sh start      # 后台启动，默认 http://127.0.0.1:8765
./scripts/web.sh status     # 运行状态
./scripts/web.sh stop       # 停止
```

也可以前台运行（Ctrl+C 停止）：`.venv/bin/python run.py web [--port 8765]`。

面板内容：

- **OKX 日报看板**：新币重点关注、达标币对、连续下跌触发列表（支持搜索 / 按列排序）、
  数据源与更新时间；「刷新行情」立即抓取最新数据，「发送钉钉」立即执行一次日报推送
- **监控规则**：所有购物规则与触发条件、启用状态
- **最近抓取**：商品最新价格；点击任意商品查看价格历史走势图
- 行情缓存 15 分钟自动刷新，页面数据每分钟自动更新

端口在 `config.yaml` 的 `web` 段修改（默认 8765）。

## Docker 部署（服务器 / 7×24 运行）

适合放在一台 Linux 服务器上长期运行。镜像基于 Python 3.11 + Playwright Chromium，
容器内用 supervisor 同时托管监控循环和 Web 面板；`data/` 与 `config.yaml` 挂在宿主机。

**服务器要求**：Linux x86_64/arm64、Docker 24+（含 compose 插件）、≥2 GB 内存、≥5 GB 磁盘。

一键部署（在本机执行，需要能 SSH 到服务器）：

```bash
./deploy/deploy.sh root@1.2.3.4
# 非默认端口/密钥：
DEPLOY_SSH_OPTS='-p 2222 -i ~/.ssh/id_ed25519' ./deploy/deploy.sh ubuntu@example.com
```

脚本会：同步代码 → 把 `config.yaml` 的浏览器内核改为容器自带 Chromium（`chromium` 新无头模式）→ 上传配置 →
远程执行 `docker compose up -d --build`。

手动部署：

```bash
git clone git@github.com:vvvvv10/goods-monitor.git
cd goods-monitor
cp config.example.yaml config.yaml   # 填钉钉 webhook
# 把 config.yaml 里的 browser.channel 改成 "chromium"（容器内没有 Google Chrome）
mkdir -p data
docker compose up -d --build
```

访问面板（推荐 SSH 隧道，端口不对公网开放）：

```bash
ssh -N -L 8765:127.0.0.1:8765 root@1.2.3.4
# 本机浏览器打开 http://127.0.0.1:8765
```

**服务器上扫码登录**（无图形界面）：

```bash
# 1. 暂停监控，避免 profile 冲突
docker compose exec app supervisorctl -c /app/deploy/supervisord.conf stop monitor
# 2. 无头登录，登录页每 3 秒截图到 data/login/jd.png
docker compose exec app python run.py login jd --headless --timeout 600
# 3. 另开终端下载截图并扫码
scp root@1.2.3.4:/opt/goods-monitor/data/login/jd.png .
# 4. 登录成功后恢复监控
docker compose exec app supervisorctl -c /app/deploy/supervisord.conf start monitor
docker compose exec app python run.py status
```

淘宝/拼多多/闲鱼把 `jd` 换成对应平台即可。登录态保存在 `data/profiles/`，
重新部署不会丢失（`data/` 不参与镜像构建和同步）。

**运维命令**：

```bash
docker compose ps
docker compose logs -f --tail=100
docker compose restart
docker compose down            # 停止（data/ 保留）
docker compose up -d --build   # 更新代码后重建
```

时区固定为 `Asia/Shanghai`，OKX 日报仍为每天 00:10 推送。

## 常用命令

| 命令 | 作用 |
|------|------|
| `run.py run` | 启动监控循环 |
| `run.py once [平台]` | 立刻执行一轮检查 |
| `run.py check <链接>` | 单独检查一个商品（调试抓取是否正常） |
| `run.py login <平台>` | 扫码登录并保存会话 |
| `run.py status` | 查看各平台登录状态 |
| `run.py test-notify` | 发送测试通知 |
| `run.py list` | 查看规则与最近抓取记录 |
| `run.py demo` | mock 自测（不联网，验证全链路） |
| `run.py crypto [--dry-run] [--mock]` | OKX 虚拟币日报：立即执行 / 只打印 / 离线演示 |
| `run.py web [--port 8765]` | 启动本地 Web 面板（`scripts/web.sh` 可后台管理） |

## 目录说明

```
deal-monitor/
├── run.py                  # 命令行入口
├── config.yaml             # 你的配置（改这个）
├── config.example.yaml     # 配置模板
├── monitor/                # 程序主体
│   ├── engine.py           # 调度、规则判断、提醒
│   ├── db.py               # SQLite 存储
│   ├── config.py           # 配置加载
│   ├── models.py           # 数据结构
│   ├── notify/             # 钉钉/企业微信/webhook/终端 通知
│   ├── browser/            # Playwright 会话与登录
│   ├── crypto/             # OKX 虚拟币日报（行情客户端 + 每日任务）
│   ├── web/                # 本地 Web 面板（标准库 HTTP 服务 + 单页前端）
│   └── platforms/          # 京东/淘宝/拼多多/闲鱼 适配器
├── scripts/
│   ├── daemon.sh           # 后台启停脚本
│   ├── web.sh              # Web 面板启停脚本
│   └── probe_platforms.py  # 平台连通性诊断工具
└── data/                   # 运行数据（数据库/日志/登录会话/字体缓存）
```

## 调参建议

- **频率**：在 `config.yaml` 的 `platforms.<平台>.interval` 调整（秒），`jitter` 是随机浮动比例。
  闲鱼建议 ≥300 秒，淘宝/拼多多 ≥120 秒，京东 ≥60 秒。频率越激进，风控概率越高。
- **提醒冷却**：`alert_cooldown_hours`（默认 6 小时内的同一条提醒不重复发）。
- **无头模式**：`browser.headless: true`（默认）。如果某平台频繁提示风控/非法访问，
  改成 `false`（会弹浏览器窗口但更像真人，登录态也保持）。

## 常见问题

**Q: 京东显示"价格被打码"？**
登录过期了。运行 `.venv/bin/python run.py login jd` 重新登录。必须登录才能看到京东价格。

**Q: 闲鱼/淘宝提示"非法访问"或要求滑块验证？**
1. `browser.headless` 改 `false` 再跑；2. 降低频率；3. 等十几分钟再试；
4. 重新 `run.py login xianyu` 刷一下登录态。

**Q: 电脑关机还监控吗？**
不监控。本方案跑在你本机。想 7x24 可以之后迁移到云服务器（需把 `data/profiles/`
里的登录会话拷过去，或服务器上用 VNC 重新登录）。

**Q: 会不会被平台封号？**
程序已内置随机间隔、低频访问、浏览器指纹规避等措施，但任何自动化都不能 100% 避免风控。
建议：只监控少量商品、别用主账号高频跑、观察几天没问题再加量。

**Q: 价格历史在哪看？**
启动 `./scripts/web.sh start` 打开面板，点击「最近抓取」里的商品即可看走势图；
也可以直接查 `data/monitor.db`（SQLite），或运行 `run.py list` 看最近记录。

**Q: OKX 日报提示请求失败？**
程序默认走内置国内镜像域名（免翻墙直连）。镜像域名会轮换，失效时在
`config.yaml` 的 `crypto.base_urls` 里填上新的镜像域名（或启动本机代理并在
`crypto.proxy` 填 `http://127.0.0.1:7890` 走官方域名）。
想先离线预览日报效果，运行 `run.py crypto --mock`。

## 免责声明

本项目仅供个人学习与自用。请遵守各平台的用户协议与服务条款，保持合理访问频率，
不要用于商业抓取或大规模爬取。因违反平台规则造成的账号限制等后果由使用者自行承担。
