from __future__ import annotations

import asyncio
import html
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


SHELL_META_RE = re.compile(r"[;&|><`$\\\n]")
CODEX_TOKENS_RE = re.compile(r"(?m)^tokens used\s*\n\s*([0-9,]+)\s*$")
PROJECT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    allowed_user_ids: set[int]
    bot_workdir: Path
    command_timeout_seconds: int
    max_output_chars: int
    allow_unsafe_commands: bool
    allowed_command_prefixes: list[list[str]]
    ai_api_base_url: str
    ai_chat_completions_path: str
    ai_api_key: str
    ai_model: str
    ai_timeout_seconds: int
    ai_system_prompt: str
    codex_command_template: str
    codex_workdir: Path
    codex_timeout_seconds: int
    codex_session_chars: int


def getenv_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def getenv_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


def parse_user_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            ids.add(int(item))
    return ids


def parse_prefixes(raw: str) -> list[list[str]]:
    prefixes: list[list[str]] = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            prefixes.append(shlex.split(item, posix=True))
    return prefixes


def resolve_path(raw: str | None, default: Path) -> Path:
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def load_settings() -> Settings:
    load_dotenv()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    allowed_user_ids = parse_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))
    if not allowed_user_ids:
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS is required")

    bot_workdir = resolve_path(os.getenv("BOT_WORKDIR"), PROJECT_DIR / "workdir")
    codex_workdir = resolve_path(os.getenv("CODEX_WORKDIR"), bot_workdir)

    return Settings(
        telegram_token=telegram_token,
        allowed_user_ids=allowed_user_ids,
        bot_workdir=bot_workdir,
        command_timeout_seconds=getenv_int("COMMAND_TIMEOUT_SECONDS", 60),
        max_output_chars=getenv_int("MAX_OUTPUT_CHARS", 3500),
        allow_unsafe_commands=getenv_bool("ALLOW_UNSAFE_COMMANDS", False),
        allowed_command_prefixes=parse_prefixes(
            os.getenv(
                "ALLOWED_COMMAND_PREFIXES",
                "uptime,whoami,pwd,ls,df -h,free -h,systemctl status,docker ps,journalctl -n",
            )
        ),
        ai_api_base_url=os.getenv("AI_API_BASE_URL", "https://api.openai.com").rstrip("/"),
        ai_chat_completions_path=os.getenv("AI_CHAT_COMPLETIONS_PATH", "/v1/chat/completions"),
        ai_api_key=os.getenv("AI_API_KEY", "").strip(),
        ai_model=os.getenv("AI_MODEL", "gpt-4o-mini").strip(),
        ai_timeout_seconds=getenv_int("AI_TIMEOUT_SECONDS", 90),
        ai_system_prompt=os.getenv("AI_SYSTEM_PROMPT", "You are a concise assistant inside a Telegram bot."),
        codex_command_template=os.getenv("CODEX_COMMAND_TEMPLATE", "codex exec {prompt}"),
        codex_workdir=codex_workdir,
        codex_timeout_seconds=getenv_int("CODEX_TIMEOUT_SECONDS", 300),
        codex_session_chars=getenv_int("CODEX_SESSION_CHARS", 12000),
    )


SETTINGS = load_settings()
DATA_DIR = PROJECT_DIR / "data"
CODEX_SESSIONS_DIR = DATA_DIR / "codex_sessions"


def ensure_dirs() -> None:
    SETTINGS.bot_workdir.mkdir(parents=True, exist_ok=True)
    SETTINGS.codex_workdir.mkdir(parents=True, exist_ok=True)
    CODEX_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def user_is_allowed(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in SETTINGS.allowed_user_ids)


async def reject_if_unauthorized(update: Update) -> bool:
    if user_is_allowed(update):
        return False
    user = update.effective_user
    user_id = user.id if user else "unknown"
    if update.effective_message:
        await update.effective_message.reply_text(f"Not authorized. Your Telegram user id is: {user_id}")
    return True


async def send_text(update: Update, text: str, *, as_pre: bool = False) -> None:
    if not update.effective_message:
        return
    if len(text) > SETTINGS.max_output_chars:
        text = text[: SETTINGS.max_output_chars] + "\n\n...[truncated]"

    chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [""]
    for chunk in chunks:
        if as_pre:
            escaped = html.escape(chunk)
            await update.effective_message.reply_text(
                f"<pre>{escaped}</pre>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        else:
            await update.effective_message.reply_text(chunk, disable_web_page_preview=True)


def command_allowed(argv: list[str]) -> bool:
    for prefix in SETTINGS.allowed_command_prefixes:
        if argv[: len(prefix)] == prefix:
            return True
    return False


async def run_exec(argv: list[str], cwd: Path, timeout: int) -> tuple[int | None, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return None, f"Timed out after {timeout} seconds."
    return process.returncode, stdout.decode(errors="replace")


async def run_shell(command: str, cwd: Path, timeout: int) -> tuple[int | None, str]:
    process = await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return None, f"Timed out after {timeout} seconds."
    return process.returncode, stdout.decode(errors="replace")


def format_process_result(returncode: int | None, output: str) -> str:
    status = "timeout" if returncode is None else f"exit={returncode}"
    body = output.strip() or "(no output)"
    return f"{status}\n\n{body}"


def parse_codex_output(output: str) -> tuple[str | None, str]:
    text = output.strip()
    matches = list(CODEX_TOKENS_RE.finditer(text))
    if not matches:
        return None, text

    match = matches[-1]
    tokens_used = match.group(1).strip()
    final_reply = text[match.end() :].strip()
    return tokens_used, final_reply


def format_codex_result(returncode: int | None, output: str) -> str:
    if returncode is None:
        return format_process_result(returncode, output)

    tokens_used, final_reply = parse_codex_output(output)
    if returncode != 0:
        body = final_reply or output.strip() or "(no output)"
        return f"exit={returncode}\n\n{body}"

    if tokens_used and final_reply:
        return f"tokens used: {tokens_used}\n\n{final_reply}"
    if final_reply:
        return final_reply
    return output.strip() or "(no output)"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_unauthorized(update):
        return
    await send_text(update, help_text())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_unauthorized(update):
        return
    await send_text(update, help_text())


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await send_text(update, f"Your Telegram user id is: {user.id if user else 'unknown'}")


async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_unauthorized(update):
        return
    command = " ".join(context.args).strip()
    if not command:
        await send_text(update, "Usage: /cmd uptime")
        return

    if SETTINGS.allow_unsafe_commands:
        returncode, output = await run_shell(command, SETTINGS.bot_workdir, SETTINGS.command_timeout_seconds)
    else:
        if SHELL_META_RE.search(command):
            await send_text(update, "Rejected: shell metacharacters are disabled in safe mode.")
            return
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            await send_text(update, f"Rejected: cannot parse command: {exc}")
            return
        if not argv or not command_allowed(argv):
            allowed = ", ".join(" ".join(prefix) for prefix in SETTINGS.allowed_command_prefixes)
            await send_text(update, f"Rejected: command is not in ALLOWED_COMMAND_PREFIXES.\nAllowed: {allowed}")
            return
        returncode, output = await run_exec(argv, SETTINGS.bot_workdir, SETTINGS.command_timeout_seconds)

    await send_text(update, format_process_result(returncode, output), as_pre=True)


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_unauthorized(update):
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await send_text(update, "Usage: /ai summarize this error: ...")
        return
    if not SETTINGS.ai_api_key:
        await send_text(update, "AI_API_KEY is not configured.")
        return

    url = f"{SETTINGS.ai_api_base_url}{SETTINGS.ai_chat_completions_path}"
    payload = {
        "model": SETTINGS.ai_model,
        "messages": [
            {"role": "system", "content": SETTINGS.ai_system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {"Authorization": f"Bearer {SETTINGS.ai_api_key}"}

    try:
        async with httpx.AsyncClient(timeout=SETTINGS.ai_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        await send_text(update, f"AI API error: {exc}")
        return

    content = extract_chat_completion_text(data)
    await send_text(update, content or "AI API returned no text.")


def extract_chat_completion_text(data: dict) -> str:
    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        return str(data)


def session_path(chat_id: int) -> Path:
    return CODEX_SESSIONS_DIR / f"{chat_id}.md"


def read_session(chat_id: int) -> str:
    path = session_path(chat_id)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-SETTINGS.codex_session_chars :]


def append_session(chat_id: int, user_prompt: str, codex_output: str) -> None:
    path = session_path(chat_id)
    with path.open("a", encoding="utf-8") as file:
        file.write("\n\n## User\n")
        file.write(user_prompt.strip())
        file.write("\n\n## Codex\n")
        file.write(codex_output.strip())


def render_codex_command(prompt: str) -> str:
    quoted_prompt = shlex.quote(prompt)
    template = SETTINGS.codex_command_template
    if "{prompt}" in template:
        return template.replace("{prompt}", quoted_prompt)
    return f"{template} {quoted_prompt}"


async def codex_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_unauthorized(update):
        return
    if not update.effective_chat:
        return
    prompt = " ".join(context.args).strip()
    if not prompt:
        await send_text(update, "Usage: /codex inspect this repo and suggest the next step")
        return

    history = read_session(update.effective_chat.id)
    wrapped_prompt = (
        f"Previous conversation:\n{history or '(empty)'}\n\n"
        f"User request:\n{prompt}"
    )
    command = render_codex_command(wrapped_prompt)
    returncode, output = await run_shell(command, SETTINGS.codex_workdir, SETTINGS.codex_timeout_seconds)
    result = format_codex_result(returncode, output)
    _, final_reply = parse_codex_output(output)
    append_session(update.effective_chat.id, prompt, final_reply or output)
    await send_text(update, result, as_pre=True)


async def codex_reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_unauthorized(update):
        return
    if not update.effective_chat:
        return
    path = session_path(update.effective_chat.id)
    if path.exists():
        path.unlink()
    await send_text(update, "Codex session reset.")


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_unauthorized(update):
        return
    await send_text(update, "Use /help to see available commands.")


def help_text() -> str:
    return (
        "Available commands:\n"
        "/id - show your Telegram user id\n"
        "/cmd <linux command> - run an allowed server command\n"
        "/ai <prompt> - call the configured AI API\n"
        "/codex <prompt> - ask Codex CLI in CODEX_WORKDIR\n"
        "/codex_reset - clear this chat's Codex transcript\n"
        "/help - show this help"
    )


def main() -> None:
    ensure_dirs()
    app = Application.builder().token(SETTINGS.telegram_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("cmd", cmd_command))
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("codex", codex_command))
    app.add_handler(CommandHandler("codex_reset", codex_reset_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
