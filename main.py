"""
main.py — Trackie: personal health tracking Telegram bot.

Commands:
  /start     — welcome message, register user
  /log       — log a meal via Gemini analysis
  /weight    — record today's weight
  /stats     — weekly summary (calories, protein, weight)
  /reminders — manage daily reminders
  /today     — today's food log + totals
"""

import logging
import os
import re
from datetime import datetime, time

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
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
from gemini_client import GeminiClient

# ── Setup ─────────────────────────────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database("trackie.db")
gemini = GeminiClient(GEMINI_API_KEY)

# ConversationHandler states
WAITING_FOOD = 1
WAITING_WEIGHT = 2
WAITING_REMINDER_TIME = 3

# Reminder types available
REMINDER_TYPES = {
    "water": "💧 Water",
    "gym": "🏋️ Gym",
    "creatine": "💊 Creatine",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reminder_job_name(user_id: int, rtype: str) -> str:
    return f"reminder_{user_id}_{rtype}"


def _schedule_reminder(app: Application, user_id: int, rtype: str, time_str: str):
    """Register a daily reminder job in the job queue."""
    hour, minute = map(int, time_str.split(":"))
    job_name = _reminder_job_name(user_id, rtype)

    # Remove existing job for this user+type if it exists
    current_jobs = app.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()

    label = REMINDER_TYPES.get(rtype, rtype.capitalize())
    app.job_queue.run_daily(
        callback=_send_reminder,
        time=time(hour=hour, minute=minute),
        chat_id=user_id,
        name=job_name,
        data={"label": label, "rtype": rtype},
    )


async def _send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Job callback — fires the reminder message."""
    label = context.job.data["label"]
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"⏰ Reminder: {label}",
    )


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username or user.first_name)

    await update.message.reply_text(
        f"👋 Hey, {user.first_name}! I'm *Trackie* — your personal health assistant.\n\n"
        "Here's what I can do:\n"
        "  /log — log a meal (I'll estimate calories & protein)\n"
        "  /weight — record your weight\n"
        "  /today — see today's food log\n"
        "  /stats — weekly summary\n"
        "  /reminders — set daily reminders\n\n"
        "Let's get started! 💪",
        parse_mode="Markdown",
    )


# ── /log conversation ─────────────────────────────────────────────────────────

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍽 What did you eat? Describe it naturally, e.g.:\n"
        "_\"2 scrambled eggs, oatmeal with banana, black coffee\"_",
        parse_mode="Markdown",
    )
    return WAITING_FOOD


async def receive_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    user_id = update.effective_user.id

    thinking_msg = await update.message.reply_text("🔍 Analyzing with Gemini...")

    try:
        result = gemini.analyze_food(description)
        calories = result["calories"]
        protein = result["protein"]

        db.add_food(user_id, description, calories, protein)

        await thinking_msg.edit_text(
            f"✅ *Meal logged!*\n\n"
            f"📋 _{description}_\n\n"
            f"🔥 Calories: *{calories:.0f} kcal*\n"
            f"💪 Protein: *{protein:.1f} g*",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Gemini error: %s", e)
        await thinking_msg.edit_text(
            "⚠️ Couldn't analyze that meal. Please try again or rephrase your description."
        )

    return ConversationHandler.END


# ── /weight conversation ──────────────────────────────────────────────────────

async def cmd_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚖️ Enter your current weight in kg (e.g. *74.5*):", parse_mode="Markdown")
    return WAITING_WEIGHT


async def receive_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".")
    user_id = update.effective_user.id

    try:
        weight = float(text)
        if weight <= 0 or weight > 500:
            raise ValueError("Unrealistic weight")

        db.add_weight(user_id, weight)
        await update.message.reply_text(
            f"✅ Weight recorded: *{weight} kg*\n"
            f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}",
            parse_mode="Markdown",
        )
    except ValueError:
        await update.message.reply_text("⚠️ Please send a valid weight number, e.g. *74.5*", parse_mode="Markdown")

    return ConversationHandler.END


# ── /today ────────────────────────────────────────────────────────────────────

async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    entries = db.get_today_food(user_id)

    if not entries:
        await update.message.reply_text("📭 No food logged today yet. Use /log to add a meal!")
        return

    total_cal = sum(e["calories"] or 0 for e in entries)
    total_prot = sum(e["protein"] or 0 for e in entries)

    lines = []
    for i, e in enumerate(entries, 1):
        ts = e["timestamp"][11:16]  # HH:MM
        lines.append(f"{i}. _{e['description']}_ — {e['calories']:.0f} kcal, {e['protein']:.1f}g protein ({ts})")

    body = "\n".join(lines)
    await update.message.reply_text(
        f"📅 *Today's food log*\n\n{body}\n\n"
        f"━━━━━━━━━━━━\n"
        f"🔥 Total: *{total_cal:.0f} kcal* | 💪 *{total_prot:.1f} g protein*",
        parse_mode="Markdown",
    )


# ── /stats ────────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    foods = db.get_week_food(user_id)
    weights = db.get_week_weights(user_id)

    # Calorie / protein totals
    total_cal = sum(e["calories"] or 0 for e in foods)
    total_prot = sum(e["protein"] or 0 for e in foods)
    meal_count = len(foods)

    # Weight dynamics
    weight_section = ""
    if weights:
        first_w = weights[0]["weight"]
        last_w = weights[-1]["weight"]
        delta = last_w - first_w
        sign = "+" if delta >= 0 else ""
        weight_section = (
            f"\n⚖️ *Weight (last 7 days)*\n"
            f"  Start: {first_w} kg → Latest: {last_w} kg\n"
            f"  Change: *{sign}{delta:.1f} kg*\n"
        )
    else:
        weight_section = "\n⚖️ No weight entries this week.\n"

    avg_cal = total_cal / 7
    avg_prot = total_prot / 7

    await update.message.reply_text(
        f"📊 *Weekly Summary*\n\n"
        f"🍽 *Nutrition (last 7 days)*\n"
        f"  Meals logged: {meal_count}\n"
        f"  Total calories: *{total_cal:.0f} kcal*\n"
        f"  Avg/day: *{avg_cal:.0f} kcal*\n"
        f"  Total protein: *{total_prot:.1f} g*\n"
        f"  Avg/day: *{avg_prot:.1f} g*\n"
        f"{weight_section}",
        parse_mode="Markdown",
    )


# ── /reminders ────────────────────────────────────────────────────────────────

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show reminder menu with inline keyboard."""
    user_id = update.effective_user.id
    existing = {r["reminder_type"]: r["time"] for r in db.get_user_reminders(user_id)}

    keyboard = []
    for rtype, label in REMINDER_TYPES.items():
        time_str = existing.get(rtype)
        btn_label = f"{label} ✅ {time_str}" if time_str else f"{label} — tap to set"
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"set_reminder:{rtype}")])

    if existing:
        keyboard.append([InlineKeyboardButton("🗑 Delete a reminder", callback_data="delete_reminder_menu")])

    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="close")])

    await update.message.reply_text(
        "⏰ *Reminders*\nSelect a reminder to set or update:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard taps for reminder setup."""
    query = update.callback_query
    await query.answer()

    if query.data == "close":
        await query.message.delete()
        return ConversationHandler.END

    if query.data == "delete_reminder_menu":
        user_id = query.from_user.id
        existing = db.get_user_reminders(user_id)
        if not existing:
            await query.edit_message_text("You have no reminders set.")
            return ConversationHandler.END

        keyboard = [
            [InlineKeyboardButton(
                f"{REMINDER_TYPES.get(r['reminder_type'], r['reminder_type'])} ({r['time']})",
                callback_data=f"del_reminder:{r['reminder_type']}"
            )]
            for r in existing
        ]
        keyboard.append([InlineKeyboardButton("« Back", callback_data="back_reminders")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    if query.data.startswith("del_reminder:"):
        rtype = query.data.split(":", 1)[1]
        user_id = query.from_user.id
        db.delete_reminder(user_id, rtype)

        # Cancel scheduled job
        job_name = _reminder_job_name(user_id, rtype)
        for job in query.get_bot().job_queue.get_jobs_by_name(job_name) if hasattr(query.get_bot(), "job_queue") else []:
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
    text = update.message.text.strip()
    user_id = update.effective_user.id
    rtype = context.user_data.get("pending_reminder_type")

    # Validate HH:MM format
    if not re.match(r"^\d{1,2}:\d{2}$", text):
        await update.message.reply_text("⚠️ Invalid format. Please send time as *HH:MM*, e.g. `07:30`", parse_mode="Markdown")
        return WAITING_REMINDER_TIME

    hour, minute = map(int, text.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await update.message.reply_text("⚠️ Invalid time. Hour must be 0-23, minute 0-59.", parse_mode="Markdown")
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


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Startup: restore reminders from DB ────────────────────────────────────────

async def on_startup(app: Application):
    """Reschedule all saved reminders when the bot starts."""
    reminders = db.get_all_reminders()
    for r in reminders:
        try:
            _schedule_reminder(app, r["user_id"], r["reminder_type"], r["time"])
            logger.info("Restored reminder: user=%s type=%s time=%s", r["user_id"], r["reminder_type"], r["time"])
        except Exception as e:
            logger.warning("Failed to restore reminder %s: %s", r, e)
    logger.info("Restored %d reminder(s) from database.", len(reminders))


# ── Bot assembly ──────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()

    # /log conversation
    log_conv = ConversationHandler(
        entry_points=[CommandHandler("log", cmd_log)],
        states={WAITING_FOOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_food)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # /weight conversation
    weight_conv = ConversationHandler(
        entry_points=[CommandHandler("weight", cmd_weight)],
        states={WAITING_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weight)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # /reminders conversation (inline keyboard + time input)
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
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(log_conv)
    app.add_handler(weight_conv)
    app.add_handler(reminder_conv)

    # Catch-all for inline buttons not inside a conversation
    app.add_handler(CallbackQueryHandler(reminder_callback))

    logger.info("Trackie bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
