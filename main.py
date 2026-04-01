"""
main.py — Trackie: персональний бот для відстеження здоров'я.

Команди:
  /start      — привітання, реєстрація користувача
  /log        — записати прийом їжі
  /addproduct — додати власний продукт до бази
  /weight     — записати вагу
  /stats      — тижнева статистика
  /reminders  — керування нагадуваннями
  /plan       — нагадування і харчування за сьогодні
  /today      — лог їжі за сьогодні
  /help       — список команд
  /cancel     — скасувати поточну дію
"""

import html as html_lib
import logging
import os
import re
from datetime import datetime, time

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from database import Database

# ── Налаштування ───────────────────────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database("trackie.db")

# ── Стани ConversationHandler ──────────────────────────────────────────────────
# /log
WAITING_PRODUCT_SEARCH = 1
WAITING_PRODUCT_GRAMS  = 2
# /addproduct
WAITING_ADDPROD_NAME     = 10
WAITING_ADDPROD_CALORIES = 11
WAITING_ADDPROD_PROTEIN  = 12
# /weight
WAITING_WEIGHT = 20
# /reminders
WAITING_REMINDER_SELECT = 30  # очікування натискання кнопки меню
WAITING_REMINDER_TIME   = 31  # очікування введення часу

# ── Конфіг нагадувань ──────────────────────────────────────────────────────────
REMINDER_TYPES = {
    "water":    "💧 Вода",
    "gym":      "🏋️ Зал",
    "creatine": "💊 Креатин",
}

# ── Текст допомоги (shared між /start і /help) ────────────────────────────────
HELP_TEXT = (
    "Ось що я вмію:\n\n"
    "  /log — записати їжу\n"
    "  /addproduct — додати свій продукт\n"
    "  /weight — записати вагу\n"
    "  /today — лог їжі за сьогодні\n"
    "  /plan — нагадування і харчування на сьогодні\n"
    "  /stats — тижнева статистика\n"
    "  /reminders — налаштувати нагадування\n"
    "  /help — показати цей список\n\n"
    "Скасувати будь-яку дію — /cancel"
)


# ── Хелпери нагадувань ─────────────────────────────────────────────────────────

def _reminder_job_name(user_id: int, rtype: str) -> str:
    return f"reminder_{user_id}_{rtype}"


def _schedule_reminder(app: Application, user_id: int, rtype: str, time_str: str):
    """Реєструє (або замінює) щоденне нагадування у черзі завдань."""
    hour, minute = map(int, time_str.split(":"))
    job_name = _reminder_job_name(user_id, rtype)

    for job in app.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    app.job_queue.run_daily(
        callback=_send_reminder,
        time=time(hour=hour, minute=minute),
        chat_id=user_id,
        name=job_name,
        data={"label": REMINDER_TYPES.get(rtype, rtype)},
    )


async def _send_reminder(context: ContextTypes.DEFAULT_TYPE):
    label = context.job.data["label"]
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"⏰ Нагадування: {label}",
    )


def _parse_time(text: str) -> tuple[int, int] | None:
    """
    Парсить час у форматах: 10:00, 10.00, 1000, 830.
    Повертає (year, minute) або None якщо формат невірний.
    """
    text = text.strip().replace(".", ":")
    # Формат без двокрапки: 930 → 09:30, 1000 → 10:00
    if re.match(r"^\d{3,4}$", text):
        text = text.zfill(4)
        text = f"{text[:2]}:{text[2:]}"
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        return None
    hour, minute = map(int, text.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


# ── /start ─────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username or user.first_name)
    # html.escape безпечно обробляє будь-який нікнейм (з _ або &)
    raw_name = f"@{user.username}" if user.username else user.first_name
    name = html_lib.escape(raw_name)
    await update.message.reply_text(
        f"Привіт, {name}! 👋\n\n"
        "Мене звати <b>Trackie</b> — я допоможу тобі не забувати слідкувати за здоров'ям: "
        "їжа, вага, вода, зал — все в одному місці.\n\n"
        + HELP_TEXT,
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


# ── /log conversation ──────────────────────────────────────────────────────────

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 Введи назву продукту для пошуку (напр. *курка*, *вівсянка*, *яйце*):",
        parse_mode="Markdown",
    )
    return WAITING_PRODUCT_SEARCH


async def handle_product_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шукає продукти в БД і показує результати як inline-кнопки."""
    query = update.message.text.strip()
    user_id = update.effective_user.id
    results = db.search_products(user_id, query)

    if not results:
        await update.message.reply_text(
            "❌ Продукт не знайдено.\n\n"
            "Спробуй іншу назву або додай продукт через /addproduct",
        )
        return WAITING_PRODUCT_SEARCH

    keyboard = [
        [InlineKeyboardButton(
            f"{r['name']} ({r['calories_per_100g']:.0f} ккал / {r['protein_per_100g']:.0f}г білка на 100г)",
            callback_data=f"prod:{r['id']}",
        )]
        for r in results
    ]
    keyboard.append([InlineKeyboardButton("🔍 Пошукати ще раз", callback_data="log_search_again")])
    keyboard.append([InlineKeyboardButton("❌ Скасувати", callback_data="log_cancel")])

    await update.message.reply_text(
        f"Знайдено *{len(results)}* результат(ів). Обери продукт:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return WAITING_PRODUCT_SEARCH


async def handle_product_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Користувач натиснув кнопку продукту — питаємо кількість грамів."""
    query = update.callback_query
    await query.answer()

    if query.data == "log_cancel":
        await query.edit_message_text("Скасовано.")
        return ConversationHandler.END

    if query.data == "log_search_again":
        await query.edit_message_text("🔍 Введи нову назву продукту:")
        return WAITING_PRODUCT_SEARCH

    product_id = int(query.data.split(":", 1)[1])
    product = db.get_product_by_id(product_id)
    if not product:
        await query.edit_message_text("⚠️ Продукт не знайдено. Спробуй /log ще раз.")
        return ConversationHandler.END

    context.user_data["selected_product"] = product
    await query.edit_message_text(
        f"✅ *{product['name']}*\n"
        f"На 100г: {product['calories_per_100g']:.0f} ккал, {product['protein_per_100g']:.0f}г білка\n\n"
        f"Скільки грамів з'їв?",
        parse_mode="Markdown",
    )
    return WAITING_PRODUCT_GRAMS


async def handle_grams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рахує калорії/білок за вагою і зберігає запис."""
    text = update.message.text.strip().replace(",", ".")
    user_id = update.effective_user.id
    product = context.user_data.get("selected_product")

    if not product:
        await update.message.reply_text("⚠️ Щось пішло не так. Почни знову з /log.")
        return ConversationHandler.END

    try:
        grams = float(text)
        if grams <= 0 or grams > 5000:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введи коректну кількість грамів, напр. *150*",
            parse_mode="Markdown",
        )
        return WAITING_PRODUCT_GRAMS

    calories = round(product["calories_per_100g"] * grams / 100, 1)
    protein  = round(product["protein_per_100g"]  * grams / 100, 1)
    description = f"{product['name']} {grams:.0f}г"

    db.add_food(user_id, description, calories, protein)
    context.user_data.pop("selected_product", None)

    await update.message.reply_text(
        f"✅ *Записано!*\n\n"
        f"📋 {description}\n"
        f"🔥 Калорії: *{calories} ккал*\n"
        f"💪 Білок: *{protein} г*",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /addproduct conversation ───────────────────────────────────────────────────

async def cmd_addproduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *Додати власний продукт*\n\nКрок 1/3 — Введи назву продукту:",
        parse_mode="Markdown",
    )
    return WAITING_ADDPROD_NAME


async def receive_addprod_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("⚠️ Назва занадто коротка. Спробуй ще раз:")
        return WAITING_ADDPROD_NAME

    context.user_data["new_product_name"] = name
    await update.message.reply_text(
        f"Крок 2/3 — *Калорії на 100г* для _{name}_?\n(напр. `165`)",
        parse_mode="Markdown",
    )
    return WAITING_ADDPROD_CALORIES


async def receive_addprod_calories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")
    try:
        calories = float(text)
        if calories < 0 or calories > 1000:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введи число від 0 до 1000:")
        return WAITING_ADDPROD_CALORIES

    context.user_data["new_product_calories"] = calories
    await update.message.reply_text(
        "Крок 3/3 — *Білок на 100г* (у грамах)?\n(напр. `31`)",
        parse_mode="Markdown",
    )
    return WAITING_ADDPROD_PROTEIN


async def receive_addprod_protein(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")
    user_id = update.effective_user.id

    try:
        protein = float(text)
        if protein < 0 or protein > 100:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введи число від 0 до 100:")
        return WAITING_ADDPROD_PROTEIN

    name     = context.user_data.pop("new_product_name")
    calories = context.user_data.pop("new_product_calories")
    db.add_product(user_id, name, calories, protein)

    await update.message.reply_text(
        f"✅ Продукт додано!\n\n"
        f"*{name}*\n"
        f"🔥 {calories:.0f} ккал | 💪 {protein:.0f}г білка — на 100г\n\n"
        f"Тепер можеш знайти його через /log.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /weight conversation ───────────────────────────────────────────────────────

async def cmd_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚖️ Введи свою поточну вагу в кг (напр. *74.5*):",
        parse_mode="Markdown",
    )
    return WAITING_WEIGHT


async def receive_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")
    user_id = update.effective_user.id
    try:
        weight = float(text)
        if weight <= 0 or weight > 500:
            raise ValueError
        db.add_weight(user_id, weight)
        await update.message.reply_text(
            f"✅ Вага записана: *{weight} кг*\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y, %H:%M')}",
            parse_mode="Markdown",
        )
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введи коректне число, напр. *74.5*",
            parse_mode="Markdown",
        )
    return ConversationHandler.END


# ── /today ─────────────────────────────────────────────────────────────────────

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = db.get_today_food(user_id)

    if not entries:
        await update.message.reply_text(
            "📭 Сьогодні ще нічого не записано. Використай /log щоб додати їжу!"
        )
        return

    total_cal  = sum(e["calories"] or 0 for e in entries)
    total_prot = sum(e["protein"]  or 0 for e in entries)

    lines = []
    for i, e in enumerate(entries, 1):
        ts = e["timestamp"][11:16]
        lines.append(
            f"{i}. _{e['description']}_ — {e['calories']:.0f} ккал, {e['protein']:.1f}г білка ({ts})"
        )

    await update.message.reply_text(
        f"📅 *Їжа за сьогодні*\n\n" + "\n".join(lines) +
        f"\n\n━━━━━━━━━━━━\n"
        f"🔥 Разом: *{total_cal:.0f} ккал* | 💪 *{total_prot:.1f} г білка*",
        parse_mode="Markdown",
    )


# ── /plan ──────────────────────────────────────────────────────────────────────

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Дашборд: нагадування + харчування за сьогодні."""
    user_id = update.effective_user.id

    # Нагадування
    reminders = db.get_user_reminders(user_id)
    if reminders:
        reminder_lines = []
        for r in reminders:
            label = REMINDER_TYPES.get(r["reminder_type"], r["reminder_type"])
            reminder_lines.append(f"  {label} — {r['time']}")
        reminder_section = "\n".join(reminder_lines)
    else:
        reminder_section = "  Немає нагадувань. Налаштуй через /reminders"

    # Харчування за сьогодні
    entries = db.get_today_food(user_id)
    total_cal  = sum(e["calories"] or 0 for e in entries)
    total_prot = sum(e["protein"]  or 0 for e in entries)

    if entries:
        food_section = (
            f"  🔥 Калорії: *{total_cal:.0f} ккал*\n"
            f"  💪 Білок: *{total_prot:.1f} г*\n"
            f"  🍽 Прийомів їжі: {len(entries)}"
        )
    else:
        food_section = "  Ще нічого не записано сьогодні"

    # Вага (остання запис)
    weights = db.get_week_weights(user_id)
    if weights:
        last_w = weights[-1]["weight"]
        weight_line = f"\n⚖️ *Остання вага:* {last_w} кг"
    else:
        weight_line = ""

    await update.message.reply_text(
        f"📋 *План на сьогодні*\n\n"
        f"⏰ *Нагадування:*\n{reminder_section}\n\n"
        f"🥗 *Харчування:*\n{food_section}"
        f"{weight_line}",
        parse_mode="Markdown",
    )


# ── /stats ─────────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    foods   = db.get_week_food(user_id)
    weights = db.get_week_weights(user_id)

    total_cal  = sum(e["calories"] or 0 for e in foods)
    total_prot = sum(e["protein"]  or 0 for e in foods)
    avg_cal    = total_cal / 7
    avg_prot   = total_prot / 7

    if weights:
        first_w = weights[0]["weight"]
        last_w  = weights[-1]["weight"]
        delta   = last_w - first_w
        sign    = "+" if delta >= 0 else ""
        weight_section = (
            f"\n⚖️ *Вага (останні 7 днів)*\n"
            f"  Початок: {first_w} кг → Зараз: {last_w} кг\n"
            f"  Зміна: *{sign}{delta:.1f} кг*\n"
        )
    else:
        weight_section = "\n⚖️ Немає записів ваги цього тижня.\n"

    await update.message.reply_text(
        f"📊 *Тижнева статистика*\n\n"
        f"🍽 *Харчування (останні 7 днів)*\n"
        f"  Прийомів їжі: {len(foods)}\n"
        f"  Всього калорій: *{total_cal:.0f} ккал*\n"
        f"  Середньо/день: *{avg_cal:.0f} ккал*\n"
        f"  Всього білка: *{total_prot:.1f} г*\n"
        f"  Середньо/день: *{avg_prot:.1f} г*\n"
        f"{weight_section}",
        parse_mode="Markdown",
    )


# ── /reminders conversation ────────────────────────────────────────────────────

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує меню нагадувань і входить у ConversationHandler."""
    await _show_reminders_menu(update.message, update.effective_user.id)
    return WAITING_REMINDER_SELECT


async def _show_reminders_menu(message, user_id: int):
    """Будує і надсилає inline-клавіатуру меню нагадувань."""
    existing = {r["reminder_type"]: r["time"] for r in db.get_user_reminders(user_id)}

    keyboard = []
    for rtype, label in REMINDER_TYPES.items():
        t = existing.get(rtype)
        btn = f"{label} ✅ {t}" if t else f"{label} — натисни щоб встановити"
        keyboard.append([InlineKeyboardButton(btn, callback_data=f"set_reminder:{rtype}")])

    if existing:
        keyboard.append([InlineKeyboardButton("🗑 Видалити нагадування", callback_data="delete_reminder_menu")])
    keyboard.append([InlineKeyboardButton("❌ Закрити", callback_data="close")])

    await message.reply_text(
        "⏰ *Нагадування*\nОбери нагадування щоб встановити або змінити:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натискання кнопок меню нагадувань."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "close":
        await query.message.delete()
        return ConversationHandler.END

    if query.data == "delete_reminder_menu":
        existing = db.get_user_reminders(user_id)
        if not existing:
            await query.edit_message_text("У тебе немає активних нагадувань.")
            return ConversationHandler.END
        keyboard = [
            [InlineKeyboardButton(
                f"{REMINDER_TYPES.get(r['reminder_type'], r['reminder_type'])} ({r['time']})",
                callback_data=f"del_reminder:{r['reminder_type']}",
            )]
            for r in existing
        ]
        keyboard.append([InlineKeyboardButton("« Назад", callback_data="back_reminders")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
        return WAITING_REMINDER_SELECT

    if query.data == "back_reminders":
        existing = {r["reminder_type"]: r["time"] for r in db.get_user_reminders(user_id)}
        keyboard = []
        for rtype, label in REMINDER_TYPES.items():
            t = existing.get(rtype)
            btn = f"{label} ✅ {t}" if t else f"{label} — натисни щоб встановити"
            keyboard.append([InlineKeyboardButton(btn, callback_data=f"set_reminder:{rtype}")])
        if existing:
            keyboard.append([InlineKeyboardButton("🗑 Видалити нагадування", callback_data="delete_reminder_menu")])
        keyboard.append([InlineKeyboardButton("❌ Закрити", callback_data="close")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
        return WAITING_REMINDER_SELECT

    if query.data.startswith("del_reminder:"):
        rtype = query.data.split(":", 1)[1]
        db.delete_reminder(user_id, rtype)
        for job in context.application.job_queue.get_jobs_by_name(_reminder_job_name(user_id, rtype)):
            job.schedule_removal()
        label = REMINDER_TYPES.get(rtype, rtype)
        await query.edit_message_text(
            f"🗑 Нагадування *{label}* видалено.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    if query.data.startswith("set_reminder:"):
        rtype = query.data.split(":", 1)[1]
        context.user_data["pending_reminder_type"] = rtype
        label = REMINDER_TYPES.get(rtype, rtype)
        await query.edit_message_text(
            f"⏰ Встанови час для *{label}*.\n\n"
            f"Надішли час у форматі *HH:MM* або *HHMM* (24г), напр. `08:30` або `0830`",
            parse_mode="Markdown",
        )
        return WAITING_REMINDER_TIME

    return WAITING_REMINDER_SELECT


async def receive_reminder_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Парсить введений час і зберігає нагадування."""
    text    = update.message.text.strip()
    user_id = update.effective_user.id
    rtype   = context.user_data.get("pending_reminder_type")

    parsed = _parse_time(text)
    if parsed is None:
        await update.message.reply_text(
            "⚠️ Невірний формат. Введи час як *HH:MM* або *HHMM*, напр. `07:30` або `0730`",
            parse_mode="Markdown",
        )
        return WAITING_REMINDER_TIME

    hour, minute = parsed
    time_str = f"{hour:02d}:{minute:02d}"
    db.set_reminder(user_id, rtype, time_str)
    _schedule_reminder(context.application, user_id, rtype, time_str)

    label = REMINDER_TYPES.get(rtype, rtype)
    await update.message.reply_text(
        f"✅ Нагадування встановлено!\n{label} — щодня о *{time_str}*",
        parse_mode="Markdown",
    )
    context.user_data.pop("pending_reminder_type", None)
    return ConversationHandler.END


# ── Скасування ─────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Скасовано.")
    return ConversationHandler.END


# ── Старт: відновлення нагадувань з БД ────────────────────────────────────────

async def on_startup(app: Application):
    reminders = db.get_all_reminders()
    for r in reminders:
        try:
            _schedule_reminder(app, r["user_id"], r["reminder_type"], r["time"])
        except Exception as e:
            logger.warning("Не вдалось відновити нагадування %s: %s", r, e)
    logger.info("Відновлено %d нагадувань з бази даних.", len(reminders))


# ── Збирання бота ──────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # /log: пошук → вибір продукту (inline) → введення грамів
    log_conv = ConversationHandler(
        entry_points=[CommandHandler("log", cmd_log)],
        states={
            WAITING_PRODUCT_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_search),
                CallbackQueryHandler(handle_product_select, pattern=r"^(prod:\d+|log_search_again|log_cancel)$"),
            ],
            WAITING_PRODUCT_GRAMS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_grams),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # /addproduct: назва → калорії → білок
    addproduct_conv = ConversationHandler(
        entry_points=[CommandHandler("addproduct", cmd_addproduct)],
        states={
            WAITING_ADDPROD_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_addprod_name)],
            WAITING_ADDPROD_CALORIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_addprod_calories)],
            WAITING_ADDPROD_PROTEIN:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_addprod_protein)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # /weight
    weight_conv = ConversationHandler(
        entry_points=[CommandHandler("weight", cmd_weight)],
        states={WAITING_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weight)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # /reminders: меню (inline) → введення часу
    # ВИПРАВЛЕНО: cmd_reminders тепер повертає WAITING_REMINDER_SELECT,
    # тому ConversationHandler правильно перехоплює наступні натискання кнопок.
    reminder_conv = ConversationHandler(
        entry_points=[CommandHandler("reminders", cmd_reminders)],
        states={
            WAITING_REMINDER_SELECT: [
                CallbackQueryHandler(reminder_callback),
            ],
            WAITING_REMINDER_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_time),
                CallbackQueryHandler(reminder_callback),  # кнопка «Назад» зсередини
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("today",  cmd_today))
    app.add_handler(CommandHandler("plan",   cmd_plan))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(log_conv)
    app.add_handler(addproduct_conv)
    app.add_handler(weight_conv)
    app.add_handler(reminder_conv)

    logger.info("Trackie бот запущено...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
