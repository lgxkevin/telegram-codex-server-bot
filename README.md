# Telegram Codex Server Bot

这是一个可以部署在 Linux server 上的 Telegram bot，支持三类能力：

- `/cmd` 执行服务器命令
- `/ai` 调用 OpenAI-compatible Chat Completions API
- `/codex` 和服务器上的 Codex CLI 交流

## 安全模型

强烈建议只把 bot 加给你自己使用，并设置：

- `TELEGRAM_ALLOWED_USER_IDS`
- `ALLOW_UNSAFE_COMMANDS=false`
- `ALLOWED_COMMAND_PREFIXES`

默认安全模式下，`/cmd` 不走 shell，会拒绝 `;`、`|`、`>` 等 shell 元字符，并且只允许白名单命令前缀。若你设置 `ALLOW_UNSAFE_COMMANDS=true`，Telegram 就等于获得了服务器 shell 权限，请只在你完全信任账号、网络和服务器环境时使用。

## 本地/服务器安装

```bash
cd /opt/telegram-codex-bot
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
nano .env
python bot.py
```

先用 `/id` 获取自己的 Telegram user id，再写入 `.env` 的 `TELEGRAM_ALLOWED_USER_IDS`。

## 配置 Telegram

1. 找 Telegram 的 `@BotFather`
2. 创建 bot，拿到 token
3. 写入 `.env`：

```bash
TELEGRAM_BOT_TOKEN=你的_token
TELEGRAM_ALLOWED_USER_IDS=你的数字用户ID
```

## 配置 AI API

默认调用 OpenAI-compatible `/v1/chat/completions`：

```bash
AI_API_BASE_URL=https://api.openai.com
AI_CHAT_COMPLETIONS_PATH=/v1/chat/completions
AI_API_KEY=你的_api_key
AI_MODEL=gpt-4o-mini
```

如果你用的是其他兼容服务，把 `AI_API_BASE_URL` 和 `AI_MODEL` 换掉即可。

## 配置 Codex CLI

先在 Linux server 上安装并登录 Codex CLI，确认运行 systemd 服务的用户也可以执行这个命令：

```bash
codex exec "hello"
```

如果你用 `telegrambot` 这个 systemd 用户运行服务，就需要让 Codex CLI 的认证、配置和目标项目目录对这个用户可用。更简单的做法是把 `systemd/telegram-codex-bot.service` 里的 `User=` 改成你平时已经登录 Codex CLI 的 Linux 用户。

然后配置：

```bash
CODEX_COMMAND_TEMPLATE=codex exec {prompt}
CODEX_WORKDIR=/opt/telegram-codex-bot/workdir
CODEX_TIMEOUT_SECONDS=300
```

`{prompt}` 会被程序自动 shell-quote。`/codex` 会为每个 Telegram chat 保存一份简短 transcript，可用 `/codex_reset` 清空。

## systemd 部署

创建运行用户：

```bash
sudo useradd --system --home /opt/telegram-codex-bot --shell /usr/sbin/nologin telegrambot
sudo chown -R telegrambot:telegrambot /opt/telegram-codex-bot
```

安装 service：

```bash
sudo cp systemd/telegram-codex-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-codex-bot
sudo systemctl status telegram-codex-bot
```

查看日志：

```bash
sudo journalctl -u telegram-codex-bot -f
```

## Telegram 命令

```text
/id
/cmd uptime
/cmd df -h
/ai 帮我解释这个报错...
/codex 看一下当前项目结构，然后告诉我下一步
/codex_reset
```
