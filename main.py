"""
main.py — Trackie: personal health tracking Telegram bot.

Commands:
  /start      — welcome message, register user
  /log        — search product, enter grams, log meal
  /addproduct — add a custom product to the database
  /weight     — record today's weight
  /stats      — weekly summary (calories, protein, weight)
  /reminders  — manage daily reminders
  /today      — today's food log + totals
  /help       — show all commands
  /cancel     — cancel current operation
"""

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

# ── Setup ──────────────────────────────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database("trackie.db")

# ── ConversationHandler states ─────────────────────────────────────────────────
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
WAITING_REMINDER_TIME = 30

# ── Reminder config ────────────────────────────────────────────────────────────
REMINDER_TYPES = {
    "water":    "💧 Water",
    "gym":      "🏋️ Gym",
    "creatine": "💊 Creatine",
}


# ── Reminder helpers ───────────────────────────────────────────────────────────

def _reminder_job_name(user_id: int, rtype: str) -> str:
    return f"reminder_{user_id}_{rtype}"


def _schedule_reminder(app: Application, user_id: int, rtype: str, time_str: str):
    """Register (or replace) a daily reminder job in the job queue."""
    hour, minute = map(int, time_str.split(":"))
    job_name = _reminder_job_name(user_id, rtype)

    for job in app.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    app.job_queue.run_daily(
        callback=_send_reminder,
        time=time(hour=hour, minute=minute),
        chat_id=user_id,
        name=job_name,
        data={"label": REMINDER_TYPES.get(rtype, rtype.capitalize())},
    )


async def _send_reminder(context: ContextTypes.DEFAULT_TYPE):
    label = context.job.data["label"]
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"⏰ Reminder: {label}")


# ── /start ─────────────────────────────────────────────────────────────────────

HELP_TEXT = (
    "Ось що я вмію:\n\n"
    "  /log — записати їжу з бази продуктів\n"
    "  /addproduct — додати свій продукт\n"
    "  /weight — записати вагу\n"
    "  /today — лог їжі за сьогодні\n"
    "  /stats — тижнева статистика\n"
    "  /reminders — нагадування (вода, зал, креатин)\n"
    "  /help — показати цей список\n\n"
    "Щоб скасувати будь-яку дію — /cancel"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import html as html_lib
    user = update.effective_user
    db.upsert_user(user.id, user.username or user.first_name)
    # html.escape handles &, <, > — safe for any username/first_name
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
        "🔍 Enter a product name to search (e.g. *chicken*, *oat*, *egg*):",
        parse_mode="Markdown",
    )
    return WAITING_PRODUCT_SEARCH


async def handle_product_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search DB and show matching products as inline buttons."""
    query = update.message.text.strip()
    user_id = update.effective_user.id
    results = db.search_products(user_id, query)

    if not results:
        await update.message.reply_text(
            "❌ No products found for that query.\n\n"
            "Try a different name, or use /addproduct to add it to your database.",
        )
        return WAITING_PRODUCT_SEARCH  # let them try again

    keyboard = [
        [InlineKeyboardButton(
            f"{r['name']} ({r['calories_per_100g']:.0f} kcal / {r['protein_per_100g']:.0f}g P per 100g)",
            callback_data=f"prod:{r['id']}",
        )]
        for r in results
    ]
    keyboard.append([InlineKeyboardButton("🔍 Search again", callback_data="log_search_again")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="log_cancel")])

    await update.message.reply_text(
        f"Found *{len(results)}* result(s). Select a product:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return WAITING_PRODUCT_SEARCH  # stay here until user taps a button


async def handle_product_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped a product button — ask how many grams."""
    query = update.callback_query
    await query.answer()

    if query.data == "log_cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    if query.data == "log_search_again":
        await query.edit_message_text("🔍 Enter a new product name:")
        return WAITING_PRODUCT_SEARCH

    product_id = int(query.data.split(":", 1)[1])
    product = db.get_product_by_id(product_id)
    if not product:
        await query.edit_message_text("⚠️ Product not found. Please try again with /log.")
        return ConversationHandler.END

    context.user_data["selected_product"] = product
    await query.edit_message_text(
        f"✅ *{product['name']}*\n"
        f"Per 100g: {product['calories_per_100g']:.0f} kcal, {product['protein_per_100g']:.0f}g protein\n\n"
        f"How many grams did you eat?",
        parse_mode="Markdown",
    )
    return WAITING_PRODUCT_GRAMS


async def handle_grams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calculate nutrition based on grams and save to food_log."""
    text = update.message.text.strip().replace(",", ".")
    user_id = update.effective_user.id
    product = context.user_data.get("selected_product")

    if not product:
        await update.message.reply_text("⚠️ Something went wrong. Please start over with /log.")
        return ConversationHandler.END

    try:
        grams = float(text)
        if grams <= 0 or grams > 5000:
            raise ValueError("Unrealistic amount")
    except ValueError:
        await update.message.reply_text("⚠️ Enter a valid number of grams, e.g. *150*", parse_mode="Markdown")
        return WAITING_PRODUCT_GRAMS

    calories = round(product["calories_per_100g"] * grams / 100, 1)
    protein  = round(product["protein_per_100g"]  * grams / 100, 1)
    description = f"{product['name']} {grams:.0f}g"

    db.add_food(user_id, description, calories, protein)
    context.user_data.pop("selected_product", None)

    await update.message.reply_text(
        f"✅ *Logged!*\n\n"
        f"📋 {description}\n"
        f"🔥 Calories: *{calories} kcal*\n"
        f"💪 Protein: *{protein} g*",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /addproduct conversation ───────────────────────────────────────────────────

async def cmd_addproduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ *Add a custom product*\n\nStep 1/3 — Enter the product name:",
        parse_mode="Markdown",
    )
    return WAITING_ADDPROD_NAME


async def receive_addprod_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("⚠️ Name is too short. Try again:")
        return WAITING_ADDPROD_NAME

    context.user_data["new_product_name"] = name
    await update.message.reply_text(
        f"Step 2/3 — *Calories per 100g* for _{name}_?\n(e.g. `165`)",
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
        await update.message.reply_text("⚠️ Enter a valid number between 0 and 1000:")
        return WAITING_ADDPROD_CALORIES

    context.user_data["new_product_calories"] = calories
    await update.message.reply_text(
        "Step 3/3 — *Protein per 100g* (grams)?\n(e.g. `31`)",
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
        await update.message.reply_text("⚠️ Enter a valid number between 0 and 100:")
        return WAITING_ADDPROD_PROTEIN

    name     = context.user_data.pop("new_product_name")
    calories = context.user_data.pop("new_product_calories")

    db.add_product(user_id, name, calories, protein)

    await update.message.reply_text(
        f"✅ Product added!\n\n"
        f"*{name}*\n"
        f"🔥 {calories:.0f} kcal | 💪 {protein:.0f}g protein — per 100g\n\n"
        f"You can now find it when using /log.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── /weight conversation ───────────────────────────────────────────────────────

async def cmd_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚖️ Enter your current weight in kg (e.g. *74.5*):",
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
            f"✅ Weight recorded: *{weight} kg*\n"
            f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}",
            parse_mode="Markdown",
        )
    except ValueError:
        await update.message.reply_text(
            "⚠️ Please send a valid weight number, e.g. *74.5*",
            parse_mode="Markdown",
        )
    return ConversationHandler.END


# ── /today ─────────────────────────────────────────────────────────────────────

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = db.get_today_food(user_id)

    if not entries:
        await update.message.reply_text("📭 No food logged today yet. Use /log to add a meal!")
        return

    total_cal  = sum(e["calories"] or 0 for e in entries)
    total_prot = sum(e["protein"]  or 0 for e in entries)

    lines = []
    for i, e in enumerate(entries, 1):
        ts = e["timestamp"][11:16]  # HH:MM
        lines.append(
            f"{i}. _{e['description']}_ — {e['calories']:.0f} kcal, {e['protein']:.1f}g protein ({ts})"
        )

    await update.message.reply_text(
        f"📅 *Today's food log*\n\n" + "\n".join(lines) +
        f"\n\n━━━━━━━━━━━━\n"
        f"🔥 Total: *{total_cal:.0f} kcal* | 💪 *{total_prot:.1f} g protein*",
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
            f"\n⚖️ *Weight (last 7 days)*\n"
            f"  Start: {first_w} kg → Latest: {last_w} kg\n"
            f"  Change: *{sign}{delta:.1f} kg*\n"
        )
    else:
        weight_section = "\n⚖️ No weight entries this week.\n"

    await update.message.reply_text(
        f"📊 *Weekly Summary*\n\n"
        f"🍽 *Nutrition (last 7 days)*\n"
        f"  Meals logged: {len(foods)}\n"
        f"  Total calories: *{total_cal:.0f} kcal*\n"
        f"  Avg/day: *{avg_cal:.0f} kcal*\n"
        f"  Total protein: *{total_prot:.1f} g*\n"
        f"  Avg/day: *{avg_prot:.1f} g*\n"
        f"{weight_section}",
        parse_mode="Markdown",
    )


# ── /reminders conversation ────────────────────────────────────────────────────

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    existing = {r["reminder_type"]: r["time"] for r in db.get_user_reminders(user_id)}

    keyboard = []
    for rtype, label in REMINDER_TYPES.items():
        t = existing.get(rtype)
        btn = f"{label} ✅ {t}" if t else f"{label} — tap to set"
        keyboard.append([InlineKeyboardButton(btn, callback_data=f"set_reminder:{rtype}")])

    if existing:
        keyboard.append([InlineKeyboardButton("🗑 Delete a reminder", callback_data="delete_reminder_menu")])
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="close")])

    await update.message.reply_text(
        "⏰ *Reminders*\nSelect a reminder to set or update:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "close":
        await query.message.delete()
        return ConversationHandler.END

    if query.data == "delete_reminder_menu":
        user_id  = query.from_user.id
        existing = db.get_user_reminders(user_id)
        if not existing:
            await query.edit_message_text("You have no reminders set.")
            return ConversationHandler.END
        keyboard = [
            [InlineKeyboardButton(
                f"{REMINDER_TYPES.get(r['reminder_type'], r['reminder_type'])} ({r['time']})",
                callback_data=f"del_reminder:{r['reminder_type']}",
            )]
            for r in existing
        ]
        keyboard.append([InlineKeyboardButton("« Back", callback_data="back_reminders")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    if query.data.startswith("del_reminder:"):
        rtype   = query.data.split(":", 1)[1]
        user_id = query.from_user.id
        db.delete_reminder(user_id, rtype)
        for job in context.application.job_queue.get_jobs_by_name(_reminder_job_name(user_id, rtype)):
            job.schedule_removal()
        label = REMINDER_TYPES.get(rtype, rtype)
        await query.edit_message_text(f"🗑 Reminder *{label}* deleted.", parse_mode="Markdown")
        return ConversationHandler.END

    if query.data.startswith("set_reminder:"):
        rtype = query.data.split(":", 1)[1]
        context.user_data["pending_reminder_type"] = rtype
        label = REMINDER_TYPES.get(rtype, rtype)
        await query.edit_message_text(
            f"⏰ Set time for *{label}* reminder.\n\nSend time in *HH:MM* format (24h), e.g. `08:30`",
            parse_mode="Markdown",
        )
        return WAITING_REMINDER_TIME

    return ConversationHandler.END


async def receive_reminder_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text    = update.message.text.strip()
    user_id = update.effective_user.id
    rtype   = context.user_data.get("pending_reminder_type")

    if not re.match(r"^\d{1,2}:\d{2}$", text):
        await update.message.reply_text(
            "⚠️ Invalid format. Please send time as *HH:MM*, e.g. `07:30`",
            parse_mode="Markdown",
        )
        return WAITING_REMINDER_TIME

    hour, minute = map(int, text.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await update.message.reply_text("⚠️ Invalid time. Hour 0-23, minute 0-59.")
        return WAITING_REMINDER_TIME

    time_str = f"{hour:02d}:{minute:02d}"
    db.set_reminder(user_id, rtype, time_str)
    _schedule_reminder(context.application, user_id, rtype, time_str)

    label = REMINDER_TYPES.get(rtype, rtype)
    await update.message.reply_text(
        f"✅ Reminder set!\n{label} — every day at *{time_str}*",
        parse_mode="Markdown",
    )
    context.user_data.pop("pending_reminder_type", None)
    return ConversationHandler.END


# ── Shared cancel ──────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Startup: restore reminders from DB ────────────────────────────────────────

async def on_startup(app: Application):
    reminders = db.get_all_reminders()
    for r in reminders:
        try:
            _schedule_reminder(app, r["user_id"], r["reminder_type"], r["time"])
        except Exception as e:
            logger.warning("Failed to restore reminder %s: %s", r, e)
    logger.info("Restored %d reminder(s) from database.", len(reminders))


# ── Bot assembly ───────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # /log: search → select product (inline) → enter grams
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

    # /addproduct: name → calories → protein
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

    # /reminders: inline menu → time input
    reminder_conv = ConversationHandler(
        entry_points=[CommandHandler("reminders", cmd_reminders)],
        states={
            WAITING_REMINDER_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_time),
            ],
            ConversationHandler.WAITING: [
                CallbackQueryHandler(reminder_callback),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(log_conv)
    app.add_handler(addproduct_conv)
    app.add_handler(weight_conv)
    app.add_handler(reminder_conv)
    app.add_handler(CallbackQueryHandler(reminder_callback))  # catch-all for reminder buttons

    logger.info("Trackie bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
