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
mkdir -p ~/telegram-codex-bot
cd ~/telegram-codex-bot
git clone https://github.com/lgxkevin/telegram-codex-server-bot.git
cd telegram-codex-server-bot
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

当前 service 模板默认按 user service 运行，也就是使用你自己的 Linux 用户，路径是 `~/telegram-codex-bot/telegram-codex-server-bot`。

然后配置：

```bash
CODEX_COMMAND_TEMPLATE=codex exec {prompt}
CODEX_WORKDIR=workdir
CODEX_TIMEOUT_SECONDS=300
```

`{prompt}` 会被程序自动 shell-quote。`/codex` 会为每个 Telegram chat 保存一份简短 transcript，可用 `/codex_reset` 清空。

## systemd 部署

这个仓库里的 service 模板是 user service，适合你的目录：

```bash
~/telegram-codex-bot/telegram-codex-server-bot
```

安装 service：

```bash
mkdir -p ~/.config/systemd/user
cp systemd/telegram-codex-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now telegram-codex-bot
systemctl --user status telegram-codex-bot
sudo loginctl enable-linger "$USER"
```

查看日志：

```bash
journalctl --user -u telegram-codex-bot -f
```

## 后续更新代码

如果 bot 已经部署过，之后先在服务器上 pull 最新代码：

```bash
cd ~/telegram-codex-bot/telegram-codex-server-bot
git pull
```

如果只是改了 `bot.py` 这类代码文件，不需要进入 venv，也不需要重新安装依赖，直接重启服务：

```bash
systemctl --user restart telegram-codex-bot
```

如果 `pyproject.toml` 里的依赖变了，再进入 venv 并重新安装：

```bash
. .venv/bin/activate
pip install -e .
systemctl --user restart telegram-codex-bot
```

如果这次更新改了 `systemd/telegram-codex-bot.service`，还需要重新复制 service 并 reload：

```bash
mkdir -p ~/.config/systemd/user
cp systemd/telegram-codex-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart telegram-codex-bot
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
