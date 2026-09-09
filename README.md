# 📡 SubInfo Telegram Bot (全能订阅查询与管家机器人)

一个轻量、优雅且功能强大的 Telegram 订阅管理与节点分析机器人。支持机场订阅流量监控、到期倒计时提醒、本地配置文件深度解析、节点一键清洗导出以及多订阅聚合管理。

---

## ✨ 核心特性

- 📊 **精准流量审计**：自动解析 `subscription-userinfo` 标头，直观展示总流量、已用上传/下载、剩余流量及彩色阶梯进度条。
- ⏳ **智能到期推算**：精确计算订阅到期绝对时间与动态倒计时（天/小时/分），临期自动警示。
- 📂 **全格式文件直传**：支持拖入 `.txt`、`.yaml`、`.yml`、`.log` 文件，自动提取 Clash / V2Ray / Sing-box 节点特征。
- 🌍 **全球落地透视**：自动聚合节点所属国家与地区，辅以国旗 Emoji 直观展示分布。
- 📥 **节点一键清洗导出**：粉碎订阅杂质与广告节点，一键生成去重清洗后的干净节点文本（`clean_nodes.txt`）。
- 📦 **多订阅聚合收藏夹**：
  - `/save <名称> <链接>` 快速入库
  - `/my` 一键并发轮询所有收藏订阅的实时健康度
  - `/del <名称>` 随时管理
- 🔒 **隐私保护**：智能识别订阅链接中的 Token、UUID、密码等敏感字段并自动打码脱敏。
- ⚡ **极致轻量**：基于纯 Python 构建，常驻内存仅 ~35MB，无繁重外部依赖。

---

## 🎛️ 指令列表

| 指令 | 描述 |
| :--- | :--- |
| `/start` | 启动机器人与欢迎面板 |
| `/menu` | 呼出交互式控制中心（Inline Keyboard 按钮） |
| `/my` | 一键轮询名下所有收藏订阅的实时健康状态 |
| `/save <别名> <链接>` | 收藏一条订阅链接到个人库 |
| `/del <别名>` | 从个人库中移除指定的订阅 |
| `/sub <链接>` | 在群聊中单次查询订阅（带防刷屏与自动脱敏） |
| `/help` | 查看详细操作指南 |

> **私聊模式**：直接向机器人发送订阅链接或拖入配置文件，即可全自动触发解析。

---

## 🚀 快速部署

### 方式一：Docker Compose（推荐）

1. 克隆仓库：
   ```bash
   git clone https://github.com/nayeshiluo/subinfo-telegram-bot.git
   cd subinfo-telegram-bot
   ```

2. 配置环境变量：
   ```bash
   cp .env.example .env
   # 编辑 .env 填入你的 SUBINFO_BOT_TOKEN 与 SUBINFO_ADMIN_IDS
   nano .env
   ```

3. 一键启动：
   ```bash
   docker compose up -d
   ```

---

### 方式二：Systemd 守护进程

1. 安装 Python 依赖：
   ```bash
   pip install -r requirements.txt
   ```

2. 创建服务文件 `/etc/systemd/system/subinfo-bot.service`：
   ```ini
   [Unit]
   Description=SubInfo Telegram Bot
   After=network-online.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/path/to/subinfo-telegram-bot
   Environment=SUBINFO_BOT_TOKEN="你的_BOT_TOKEN"
   Environment=SUBINFO_ADMIN_IDS="你的_TELEGRAM_ID"
   ExecStart=/usr/bin/python3 subinfo_bot.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

3. 启动并启用开机自启：
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now subinfo-bot
   ```

---

## 📄 开源许可

本项目遵循 [MIT License](LICENSE) 开源协议。
