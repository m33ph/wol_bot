#!/usr/bin/env python3
"""
Telegram WOL Bot + OpenWrt Router Control + Traffic Stats via conntrack
----------------------------------------------------------------------------

Функции:
 - Wake-on-LAN
 - Выключение сервера по SSH
 - Перезагрузка роутера OpenWrt
 - Сбор интернет-трафика всех устройств через conntrack
 - Авто-добавление новых устройств в БД
 - История трафика до 2 лет
 - Просмотр статистики: сегодня, вчера, месяц, год
 - Просмотр предыдущих месяцев (с разбивкой по устройствам)
 - Очистка статистики
 - Кнопочное меню Telegram
 - Авто-удаление старых сообщений (кроме 3–4 последних)
"""

import asyncio
import os
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Tuple, List

import paramiko
import aiosqlite
from wakeonlan import send_magic_packet
from dotenv import load_dotenv
from dateutil.relativedelta import relativedelta

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------------------
# Загружаем .env
# ---------------------------------------------------------------------

load_dotenv()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
ADMIN_USER_IDS = [
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()
]

SERVER_MAC = os.getenv("SERVER_MAC", "")
SERVER_IP = os.getenv("SERVER_IP", "")

SSH_USER_OMV = os.getenv("SSH_USER_OMV", "")
SSH_KEY_OMV = os.getenv("SSH_KEY_OMV", "")

ROUTER_IP = os.getenv("ROUTER_IP", "")
ROUTER_SSH_USER = os.getenv("ROUTER_SSH_USER", "")
ROUTER_SSH_KEY = os.getenv("ROUTER_SSH_KEY", "")

TRAFFIC_LAN_SUBNET = os.getenv("TRAFFIC_LAN_SUBNET", "192.168.1.")
TRAFFIC_GREP_PATTERN = os.getenv("TRAFFIC_GREP_PATTERN", TRAFFIC_LAN_SUBNET)
TRAFFIC_COLLECTION_ENABLED = os.getenv("TRAFFIC_COLLECTION_ENABLED", "true").lower() == "true"
TRAFFIC_COLLECTION_INTERVAL = int(os.getenv("TRAFFIC_COLLECTION_INTERVAL", "600"))
TRAFFIC_DB_PATH = os.getenv("TRAFFIC_DB_PATH", "/home/user/wol_bot_data/traffic_stats.db")
TRAFFIC_RETENTION_DAYS = int(os.getenv("TRAFFIC_RETENTION_DAYS", "730"))

LOG_PATH = os.getenv("LOG_PATH", "/home/user/wol_bot_data/wol_bot_conntrack.log")
KEEP_CHAT_MESSAGES = int(os.getenv("KEEP_CHAT_MESSAGES", "4"))

# ---------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------

if not TG_BOT_TOKEN:
    print("ERROR: TG_BOT_TOKEN not set")
    sys.exit(1)

IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


# ---------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------

def is_allowed(uid: int) -> bool:
    return (not ADMIN_USER_IDS) or (uid in ADMIN_USER_IDS)


def scrub(text: str) -> str:
    """Удаление приватных путей из логов."""
    text = re.sub(r"/home/[^\s]+", "[PATH]", text)
    text = re.sub(r"[A-Fa-f0-9]{30,}", "[HEX]", text)
    return text


async def run_ssh(host: str, user: str, key: str, cmd: str) -> Tuple[bool, str]:
    """SSH-выполнение команды (OMV или OpenWrt)."""

    def _run():
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            pkey = None
            if os.path.exists(key):
                # Пробуем Ed25519 → RSA
                for loader in (paramiko.Ed25519Key, paramiko.RSAKey):
                    try:
                        pkey = loader.from_private_key_file(key)
                        break
                    except Exception:
                        pass

            client.connect(hostname=host, username=user, pkey=pkey, timeout=10)

            stdin, stdout, stderr = client.exec_command(cmd, timeout=60)
            out = stdout.read().decode(errors="ignore")
            err = stderr.read().decode(errors="ignore")

            client.close()

            if err and not out:
                return False, err
            return True, out
        except Exception as e:
            return False, str(e)

    return await asyncio.to_thread(_run)


async def send_wol(mac: str):
    try:
        await asyncio.to_thread(send_magic_packet, mac)
        return True, "WOL пакет отправлен."
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------
# Работа с базой данных SQLite
# ---------------------------------------------------------------------

async def init_db():
    Path(TRAFFIC_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                ip TEXT PRIMARY KEY,
                name TEXT,
                mac TEXT,
                last_seen TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS traffic_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collected_at TEXT,
                device_ip TEXT,
                rx_bytes INTEGER,
                tx_bytes INTEGER
            )
        """)

        await db.execute("CREATE INDEX IF NOT EXISTS idx_ts_ip_date ON traffic_stats(device_ip, collected_at)")
        await db.commit()


async def add_device(ip: str):
    now = datetime.utcnow().isoformat()

    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        await db.execute("""
            INSERT INTO devices (ip, name, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET last_seen = excluded.last_seen
        """, (ip, f"Device_{ip.replace('.', '_')}", now))
        await db.commit()


async def save_sample(ip: str, rx: int, tx: int):
    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        await db.execute("""
            INSERT INTO traffic_stats (collected_at, device_ip, rx_bytes, tx_bytes)
            VALUES (?, ?, ?, ?)
        """, (datetime.utcnow().isoformat(), ip, rx, tx))
        await db.commit()


async def cleanup_old():
    cutoff = (datetime.utcnow() - timedelta(days=TRAFFIC_RETENTION_DAYS)).date().isoformat()

    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        await db.execute("DELETE FROM traffic_stats WHERE date(collected_at) < ?", (cutoff,))
        await db.commit()


# ---------------------------------------------------------------------
# Парсер conntrack
# ---------------------------------------------------------------------

RE_SRC = re.compile(r"src=(\d+\.\d+\.\d+\.\d+)")
RE_DST = re.compile(r"dst=(\d+\.\d+\.\d+\.\d+)")
RE_BYTES = re.compile(r"bytes=(\d+)")

def parse_conntrack(output: str) -> Dict[str, Dict[str, int]]:
    """
    Возвращает:
    {
        "192.168.1.50": {"in": 12345, "out": 54321},
        ...
    }
    """
    result = {}

    for line in output.splitlines():
        if TRAFFIC_LAN_SUBNET not in line:
            continue

        m_bytes = RE_BYTES.search(line)
        if not m_bytes:
            continue

        size = int(m_bytes.group(1))

        m_src = RE_SRC.search(line)
        m_dst = RE_DST.search(line)

        # если src = LAN → исходящий трафик
        if m_src:
            ip = m_src.group(1)
            if ip.startswith(TRAFFIC_LAN_SUBNET):
                result.setdefault(ip, {"in": 0, "out": 0})
                result[ip]["out"] += size

        # если dst = LAN → входящий трафик
        if m_dst:
            ip = m_dst.group(1)
            if ip.startswith(TRAFFIC_LAN_SUBNET):
                result.setdefault(ip, {"in": 0, "out": 0})
                result[ip]["in"] += size

    return result
# ---------------------------------------------------------------------
# Задача: собрать трафик через conntrack
# ---------------------------------------------------------------------

async def collect_conntrack(context):
    """
    Запускается каждые TRAFFIC_COLLECTION_INTERVAL секунд.
    На роутере выполняем:

        conntrack -L -o extended | grep "192.168.1."

    Потом парсим и записываем трафик в БД.
    """

    cmd = f"conntrack -L -o extended | grep '{TRAFFIC_GREP_PATTERN}' || true"

    ok, out = await run_ssh(
        ROUTER_IP,
        ROUTER_SSH_USER,
        ROUTER_SSH_KEY,
        cmd
    )

    if not ok:
        print("Ошибка conntrack:", scrub(out))
        return

    parsed = parse_conntrack(out)
    if not parsed:
        return

    for ip, values in parsed.items():
        await add_device(ip)
        await save_sample(ip, values["in"], values["out"])

    await cleanup_old()


# ---------------------------------------------------------------------
# Функции агрегирования статистики
# ---------------------------------------------------------------------

async def today_per_device() -> List[tuple]:
    q = """
    SELECT device_ip, SUM(rx_bytes + tx_bytes)
    FROM traffic_stats
    WHERE date(collected_at) = date('now')
    GROUP BY device_ip
    ORDER BY SUM(rx_bytes + tx_bytes) DESC
    """
    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        rows = await db.execute(q)
        rows = await rows.fetchall()
    return [(ip, s or 0) for ip, s in rows]


async def yesterday_total() -> int:
    q = "SELECT SUM(rx_bytes + tx_bytes) FROM traffic_stats WHERE date(collected_at)=date('now','-1 day')"
    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        r = await db.execute(q)
        r = await r.fetchone()
    return r[0] or 0


async def month_total(year, month) -> int:
    ym = f"{year:04d}-{month:02d}"
    q = """
    SELECT SUM(rx_bytes + tx_bytes)
    FROM traffic_stats
    WHERE strftime('%Y-%m', collected_at)=?
    """
    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        r = await db.execute(q, (ym,))
        r = await r.fetchone()
    return r[0] or 0


async def month_per_device(year, month) -> dict:
    ym = f"{year:04d}-{month:02d}"
    q = """
    SELECT device_ip, SUM(rx_bytes + tx_bytes)
    FROM traffic_stats
    WHERE strftime('%Y-%m', collected_at)=?
    GROUP BY device_ip
    ORDER BY SUM(rx_bytes + tx_bytes) DESC
    """
    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        r = await db.execute(q, (ym,))
        rows = await r.fetchall()
    return {ip: s or 0 for ip, s in rows}


async def year_total() -> int:
    q = """
    SELECT SUM(rx_bytes + tx_bytes)
    FROM traffic_stats
    WHERE strftime('%Y', collected_at)=strftime('%Y','now')
    """
    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        r = await db.execute(q)
        r = await r.fetchone()
    return r[0] or 0


# ---------------------------------------------------------------------
# Форматирование объёма данных
# ---------------------------------------------------------------------

def fmt(x: int) -> str:
    if x < 1024:
        return f"{x} B"
    for unit in ["KB", "MB", "GB", "TB", "PB"]:
        x /= 1024
        if x < 1024:
            return f"{x:.2f} {unit}"
    return f"{x:.2f} EB"


# ---------------------------------------------------------------------
# Telegram UI — клавиатуры
# ---------------------------------------------------------------------

MAIN_KB = ReplyKeyboardMarkup(
    [
        ["🖥 Включить сервер", "⏹ Выключить сервер"],
        ["🔄 Перезагрузить роутер"],
        ["📊 Трафик"],
        ["📋 Устройства", "📜 Логи"]
    ],
    resize_keyboard=True
)


def kb_traffic(offset):
    """
    Кнопки для просмотра статистики
       offset = 0 → текущий месяц
       offset = -1 → прошлый месяц
       offset = -2 → позапрошлый
    """
    buttons = [[InlineKeyboardButton("⬅ Назад", callback_data=f"traffic_prev:{offset-1}")]]
    if offset < 0:
        buttons[0].append(InlineKeyboardButton("➡ Вперёд", callback_data=f"traffic_prev:{offset+1}"))

    buttons.append([
        InlineKeyboardButton("🔄 Обновить", callback_data=f"traffic_refresh:{offset}"),
        InlineKeyboardButton("🧹 Очистить", callback_data=f"traffic_clear:confirm"),
        InlineKeyboardButton("🏠 Меню", callback_data="menu:home")
    ])

    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------
# Автоудаление старых сообщений
# ---------------------------------------------------------------------

async def record(context: ContextTypes.DEFAULT_TYPE, message):
    """
    Храним ID последних N сообщений.
    Всё старше — удаляется.
    """
    hist = context.chat_data.setdefault("hist", [])
    hist.append(message.message_id)

    if len(hist) > KEEP_CHAT_MESSAGES:
        old = hist[:-KEEP_CHAT_MESSAGES]
        for mid in old:
            try:
                await context.bot.delete_message(message.chat_id, mid)
            except:
                pass
        context.chat_data["hist"] = hist[-KEEP_CHAT_MESSAGES:]


# ---------------------------------------------------------------------
# Команды Telegram
# ---------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return await update.message.reply_text("Доступ запрещён.")

    msg = await update.message.reply_text("Готов к работе!", reply_markup=MAIN_KB)
    await record(context, msg)


async def wol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Отправляю WOL...")
    ok, info = await send_wol(SERVER_MAC)
    out = "OK: " + info if ok else "Ошибка: " + info
    msg2 = await update.message.reply_text(out)
    await record(context, msg)
    await record(context, msg2)


async def shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Выключаю сервер...")
    ok, out = await run_ssh(SERVER_IP, SSH_USER_OMV, SSH_KEY_OMV, "sudo shutdown -h now")
    msg2 = await update.message.reply_text(out if ok else "Ошибка:\n" + scrub(out))
    await record(context, msg)
    await record(context, msg2)


async def reboot_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Перезагружаю роутер...")
    ok, out = await run_ssh(ROUTER_IP, ROUTER_SSH_USER, ROUTER_SSH_KEY, "reboot")
    msg2 = await update.message.reply_text(out if ok else "Ошибка:\n" + scrub(out))
    await record(context, msg)
    await record(context, msg2)
# ---------------------------------------------------------------------
# Устройства и логи
# ---------------------------------------------------------------------

async def list_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return await update.message.reply_text("Доступ запрещён.")
    async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
        cur = await db.execute("SELECT ip, name, mac, last_seen FROM devices ORDER BY ip")
        rows = await cur.fetchall()

    if not rows:
        msg = await update.message.reply_text("Устройства не найдены.")
        await record(context, msg)
        return

    lines = []
    for ip, name, mac, last in rows:
        lines.append(f"{ip} — {name}  MAC:{mac or '-'}  last:{last or '-'}")

    # Разбиваем на сообщения по 4000 символов (telegram limit)
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3900:
            m = await update.message.reply_text(chunk)
            await record(context, m)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        m = await update.message.reply_text(chunk)
        await record(context, m)


async def show_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return await update.message.reply_text("Доступ запрещён.")

    if not os.path.exists(LOG_PATH):
        m = await update.message.reply_text("Лог-файла не найден.")
        await record(context, m)
        return

    with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()[-6000:]
    m = await update.message.reply_text(scrub(text))
    await record(context, m)


# ---------------------------------------------------------------------
# Показ статистики (кнопка / рендер)
# ---------------------------------------------------------------------

async def show_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE, offset: int = 0):
    """
    offset == 0 -> текущий месяц (спец-правила: показать сегодня, вчера, месяц, год)
    offset < 0  -> показать итог за соответствующий прошлый месяц (разбивка по устройствам)
    """
    if not is_allowed(update.effective_user.id):
        return await update.message.reply_text("Доступ запрещён.")

    target = datetime.now() + relativedelta(months=offset)
    month_title = target.strftime("%B %Y")

    if offset == 0:
        # Сегодня по устройствам
        today = await today_per_device()
        total_today = sum(t for _, t in today)
        y_total = await yesterday_total()
        m_total = await month_total(target.year, target.month)
        yrtotal = await year_total()

        lines = [f"📊 Трафик — {month_title}", ""]
        lines.append("Сегодня (на данный момент):")
        if today:
            for ip, t in today:
                lines.append(f"• {ip} — {fmt(t)}")
        else:
            lines.append("(нет данных за сегодня)")
        lines += ["", f"Всего сегодня: {fmt(total_today)}", f"Вчера: {fmt(y_total)}", f"Месяц (нарастающим итогом): {fmt(m_total)}", f"Год: {fmt(yrtotal)}"]
    else:
        per_dev = await month_per_device(target.year, target.month)
        total = sum(per_dev.values())
        lines = [f"📊 Трафик за {month_title} (итог):", ""]
        if per_dev:
            idx = 1
            for ip, val in sorted(per_dev.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"{idx}. {ip} — {fmt(val)}")
                idx += 1
            lines += ["", f"Всего: {fmt(total)}"]
        else:
            lines.append("(нет данных)")

    # Отправляем текст с Inline-клавиатурой
    text = "\n".join(lines)
    m = await update.message.reply_text(text, reply_markup=kb_traffic(offset))
    await record(context, m)


# ---------------------------------------------------------------------
# CallbackQuery handler
# ---------------------------------------------------------------------

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data.startswith("traffic_prev:"):
        try:
            offset = int(data.split(":", 1)[1])
        except:
            offset = 0
        # редактируем сообщение — рендерим для offset
        target = datetime.now() + relativedelta(months=offset)
        month_title = target.strftime("%B %Y")
        if offset == 0:
            today = await today_per_device()
            total_today = sum(t for _, t in today)
            y_total = await yesterday_total()
            m_total = await month_total(target.year, target.month)
            yrtotal = await year_total()
            lines = [f"📊 Трафик — {month_title}", ""]
            lines.append("Сегодня (на данный момент):")
            if today:
                for ip, t in today:
                    lines.append(f"• {ip} — {fmt(t)}")
            else:
                lines.append("(нет данных за сегодня)")
            lines += ["", f"Всего сегодня: {fmt(total_today)}", f"Вчера: {fmt(y_total)}", f"Месяц (нарастающим итогом): {fmt(m_total)}", f"Год: {fmt(yrtotal)}"]
        else:
            per_dev = await month_per_device(target.year, target.month)
            total = sum(per_dev.values())
            lines = [f"📊 Трафик за {month_title} (итог):", ""]
            if per_dev:
                idx = 1
                for ip, val in sorted(per_dev.items(), key=lambda x: x[1], reverse=True):
                    lines.append(f"{idx}. {ip} — {fmt(val)}")
                    idx += 1
                lines += ["", f"Всего: {fmt(total)}"]
            else:
                lines.append("(нет данных)")
        try:
            await q.edit_message_text("\n".join(lines), reply_markup=kb_traffic(offset))
        except Exception:
            # Возможно сообщение было удалено/изменино — просто отправим новое
            await q.message.reply_text("\n".join(lines), reply_markup=kb_traffic(offset))

    elif data.startswith("traffic_refresh:"):
        try:
            offset = int(data.split(":", 1)[1])
        except:
            offset = 0
        if offset == 0 and TRAFFIC_COLLECTION_ENABLED:
            # принудительный сбор
            await collect_conntrack(context)
        # перерендерить текущее окно:
        await callback_handler(update, context)  # рекурсивно обработаем traffic_prev:0

    elif data == "traffic_clear:confirm":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да — удалить", callback_data="traffic_clear:do"),
            InlineKeyboardButton("❌ Отмена", callback_data="traffic_prev:0")
        ]])
        await q.edit_message_text("Подтвердите удаление всей статистики (это необратимо).", reply_markup=kb)

    elif data == "traffic_clear:do":
        async with aiosqlite.connect(TRAFFIC_DB_PATH) as db:
            await db.execute("DELETE FROM traffic_stats")
            await db.commit()
        await q.edit_message_text("Статистика удалена.", reply_markup=kb_traffic(0))

    elif data == "menu:home":
        try:
            await q.edit_message_text("Возвращаем в главное меню.", reply_markup=None)
        except:
            pass
        m = await context.bot.send_message(chat_id=q.message.chat.id, text="Меню", reply_markup=MAIN_KB)
        await record(context, m)


# ---------------------------------------------------------------------
# Текстовые сообщения (reply keyboard)
# ---------------------------------------------------------------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "🖥 Включить сервер":
        return await wol(update, context)

    if text == "⏹ Выключить сервер":
        return await shutdown(update, context)

    if text == "🔄 Перезагрузить роутер":
        return await reboot_router(update, context)

    if text == "📊 Трафик":
        return await show_traffic(update, context, 0)

    if text == "📋 Устройства":
        return await list_devices(update, context)

    if text == "📜 Логи":
        return await show_logs(update, context)

    return await update.message.reply_text("Неизвестная команда. Нажми /start для меню.")


# ---------------------------------------------------------------------
# Запуск бота и периодические задачи
# ---------------------------------------------------------------------

async def periodic_setup(app):
    await init_db()
    if TRAFFIC_COLLECTION_ENABLED:
        app.job_queue.run_repeating(lambda ctx: asyncio.create_task(collect_conntrack(ctx)), interval=TRAFFIC_COLLECTION_INTERVAL, first=10)


async def main():
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    app.post_init.append(periodic_setup)

    print("Запуск бота...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        await app.idle()
    finally:
        await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Завершение работы.")
