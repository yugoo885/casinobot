import asyncio
import html
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiohttp import web
from aiosqlite import connect
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("dipcasino")

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
ADMIN_HANDLE = os.getenv("ADMIN_HANDLE", "@YUGO_DZ")
PORT = int(os.getenv("PORT", 10000))
DB_PATH = os.getenv("DB_PATH", "casino_stats.db")

if not API_TOKEN or not ADMIN_ID:
    raise ValueError("BOT_TOKEN و ADMIN_ID مطلوبان")

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ---------- إعدادات اللعبة ----------
MIN_BET = 500_000
COMMISSION_RATE = 0.05
COOLDOWN_SECONDS = 10
CHALLENGE_TIMEOUT = 180
MAX_REROLLS = 5  # حماية من حلقة إعادة رمي لا نهائية

# ---------- الحالة داخل الذاكرة (محمية بقفل) ----------
active_challenges: dict[str, dict] = {}
state_lock = asyncio.Lock()
user_last_cmd_time: dict[int, float] = {}


def esc(text: str | None) -> str:
    """تأمين أي نص قادم من المستخدم (الاسم، اليوزرنيم...) قبل إرساله بصيغة HTML."""
    return html.escape(text or "")


def fmt_money(n: int) -> str:
    return f"{n:,}"


# ---------- طبقة قاعدة البيانات ----------
async def init_db():
    async with connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_spent INTEGER DEFAULT 0,
                net_profit INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0
            )
        """)
        await db.commit()


@asynccontextmanager
async def get_db():
    async with connect(DB_PATH) as db:
        yield db


async def register_user_if_new(user: types.User):
    async with get_db() as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id = ?", (user.id,))
        if await cur.fetchone() is None:
            await db.execute(
                """INSERT INTO users
                   (user_id, username, full_name, wins, losses, total_spent, net_profit, is_banned)
                   VALUES (?, ?, ?, 0, 0, 0, 0, 0)""",
                (user.id, user.username or "", user.full_name),
            )
            await db.commit()
        else:
            # تحديث الاسم/اليوزرنيم في حال تغيّر
            await db.execute(
                "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
                (user.username or "", user.full_name, user.id),
            )
            await db.commit()


async def is_user_banned(user_id: int) -> bool:
    async with get_db() as db:
        cur = await db.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return bool(row and row[0] == 1)


async def update_user_result(user_id: int, username: str, full_name: str, bet: int, win_amount: int, is_win: bool):
    async with get_db() as db:
        cur = await db.execute(
            "SELECT wins, losses, total_spent, net_profit FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            wins = 1 if is_win else 0
            losses = 0 if is_win else 1
            total_spent = bet
            net_profit = (win_amount - bet) if is_win else -bet
            await db.execute(
                """INSERT INTO users
                   (user_id, username, full_name, wins, losses, total_spent, net_profit, is_banned)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                (user_id, username, full_name, wins, losses, total_spent, net_profit),
            )
        else:
            wins = row[0] + (1 if is_win else 0)
            losses = row[1] + (0 if is_win else 1)
            total_spent = row[2] + bet
            net_profit = row[3] + ((win_amount - bet) if is_win else -bet)
            await db.execute(
                """UPDATE users SET username = ?, full_name = ?, wins = ?, losses = ?,
                   total_spent = ?, net_profit = ? WHERE user_id = ?""",
                (username, full_name, wins, losses, total_spent, net_profit, user_id),
            )
        await db.commit()


# ---------- أدوات مساعدة للحالة ----------
async def find_user_active_challenge(user_id: int) -> str | None:
    """يبحث هل للمستخدم تحدٍ قائم بالفعل (كمنشئ أو كمنضم). يجب استدعاؤها داخل state_lock."""
    for ch_id, game in active_challenges.items():
        if game["p1_id"] == user_id or game.get("p2_id") == user_id:
            return ch_id
    return None


async def cancel_auto_timeout(challenge_id: str, chat_id: int, msg_id: int):
    await asyncio.sleep(CHALLENGE_TIMEOUT)
    async with state_lock:
        game = active_challenges.get(challenge_id)
        if game is None:
            return
        del active_challenges[challenge_id]
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text="⏰ تم إلغاء التحدي تلقائياً لعدم اكتماله.",
        )
    except Exception:
        pass


# ---------- الأوامر الأساسية ----------
@dp.message(Command("start", "help"))
async def help_cmd(message: types.Message):
    if await is_user_banned(message.from_user.id):
        await message.reply("❌ أنت محظور.")
        return
    await register_user_if_new(message.from_user)
    text = (
        "🎲 <b>مرحباً بك في DIPCASINO</b> 🎲\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "اختر الرهان المناسب لك وابدأ التحدي فوراً:\n\n"
        "🔹 <code>/dice 500000</code>\n"
        "🔹 <code>/dice 1000000</code>\n"
        "🔹 <code>/dice 2000000</code>\n"
        "🔹 <code>/dice 5000000</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📜 <b>القوانين:</b>\n"
        f"• يجب إرسال العملة داخل اللعبة إلى الوسيط {esc(ADMIN_HANDLE)} قبل بدء التحدي.\n"
        f"• سيتم إخطار {esc(ADMIN_HANDLE)} تلقائياً عند اكتمال اختيار الأرقام.\n"
        f"• عمولة البنك: <b>{int(COMMISSION_RATE*100)}%</b> من الجائزة.\n\n"
        "📊 <b>الأوامر:</b>\n"
        "• /top — المتصدرين\n"
        "• /stats — إحصائياتك"
    )
    await message.reply(text)


@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with get_db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cur.fetchone())[0]
        cur = await db.execute("SELECT SUM(total_spent) FROM users")
        total_volume = (await cur.fetchone())[0] or 0
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_count = (await cur.fetchone())[0]
    bank_profit = int(total_volume * COMMISSION_RATE)
    async with state_lock:
        active_count = len(active_challenges)
    text = (
        "⚙️ <b>لوحة الأدمن | DIPCASINO</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👥 المستخدمين: <code>{total_users}</code>\n"
        f"🚫 المحظورين: <code>{banned_count}</code>\n"
        f"💰 حجم الرهانات: <code>{fmt_money(total_volume)}</code>\n"
        f"🏦 أرباح البنك: <code>{fmt_money(bank_profit)}</code>\n"
        f"⚔️ تحديات نشطة: <code>{active_count}</code>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🛠 الأوامر:\n"
        "• /broadcast [الرسالة]\n"
        "• /ban [ID]\n"
        "• /unban [ID]"
    )
    await message.reply(text)


@dp.message(Command("broadcast"))
async def broadcast_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text_to_send = message.text.replace("/broadcast", "", 1).strip()
    if not text_to_send:
        await message.reply("⚠️ اكتب الرسالة بعد الأمر.")
        return
    async with get_db() as db:
        cur = await db.execute("SELECT user_id FROM users WHERE is_banned = 0")
        users = await cur.fetchall()
    success = failed = 0
    for (u_id,) in users:
        try:
            await bot.send_message(u_id, f"📢 تنويه:\n\n{esc(text_to_send)}")
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await message.reply(f"✅ أُرسلت إلى {success}، فشل {failed}")


@dp.message(Command("ban"))
async def ban_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("⚠️ الصيغة: /ban 123456789")
        return
    target = int(args[1])
    async with get_db() as db:
        await db.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target,))
        await db.commit()
    await message.reply(f"🚫 تم حظر {target}.")


@dp.message(Command("unban"))
async def unban_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("⚠️ الصيغة: /unban 123456789")
        return
    target = int(args[1])
    async with get_db() as db:
        await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target,))
        await db.commit()
    await message.reply(f"✅ تم فك الحظر عن {target}.")


# ---------- منطق التحدي ----------
@dp.message(Command("dice"))
async def dice_challenge(message: types.Message):
    user = message.from_user
    if await is_user_banned(user.id):
        await message.reply("❌ أنت محظور.")
        return
    await register_user_if_new(user)

    now = time.time()
    last = user_last_cmd_time.get(user.id)
    if last is not None and now - last < COOLDOWN_SECONDS:
        rem = int(COOLDOWN_SECONDS - (now - last))
        await message.reply(f"⏳ انتظر {rem} ثانية.")
        return

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply(f"❌ التنسيق الصحيح: /dice {MIN_BET}")
        return
    bet = int(args[1])
    if bet < MIN_BET:
        await message.reply(f"⚠️ الحد الأدنى للرهان هو {fmt_money(MIN_BET)}.")
        return

    async with state_lock:
        if await find_user_active_challenge(user.id) is not None:
            await message.reply("⚠️ لديك تحدٍ قائم بالفعل!")
            return

        user_last_cmd_time[user.id] = now
        challenge_id = str(uuid.uuid4())
        active_challenges[challenge_id] = {
            "p1_id": user.id,
            "p1_name": user.full_name,
            "p1_username": user.username or "",
            "p1_choice": None,
            "p2_id": None,
            "p2_name": None,
            "p2_username": None,
            "p2_choice": None,
            "bet": bet,
            "chat_id": message.chat.id,
            "msg_id": None,
            "started": False,   # يمنع ضغط "تأكيد" أكثر من مرة
            "rerolls": 0,
        }

    win_pot = int(bet * 2 * (1 - COMMISSION_RATE))
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚔️ انضم ({fmt_money(bet)})", callback_data=f"join_{challenge_id}")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data=f"cancel_{challenge_id}")],
    ])

    sent = await message.reply(
        "🎲 <b>تحدي مطابقة الأرقام!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👤 المتحدي: {esc(user.full_name)}\n"
        f"💰 الرهان: {fmt_money(bet)} لكل لاعب\n"
        f"🏆 الجائزة: {fmt_money(win_pot)}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📌 اضغط للانضمام!",
        reply_markup=keyboard,
    )
    async with state_lock:
        if challenge_id in active_challenges:
            active_challenges[challenge_id]["msg_id"] = sent.message_id
    asyncio.create_task(cancel_auto_timeout(challenge_id, message.chat.id, sent.message_id))


@dp.callback_query(lambda c: c.data and c.data.startswith("cancel_"))
async def cancel_challenge(callback: types.CallbackQuery):
    challenge_id = callback.data.removeprefix("cancel_")
    async with state_lock:
        game = active_challenges.get(challenge_id)
        if not game:
            await callback.answer("التحدي منتهي.", show_alert=True)
            return
        if callback.from_user.id not in (game["p1_id"], ADMIN_ID):
            await callback.answer("❌ ليس لديك صلاحية.", show_alert=True)
            return
        del active_challenges[challenge_id]
    await callback.message.edit_text("🚫 تم إلغاء التحدي.")
    await callback.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("join_"))
async def join_challenge(callback: types.CallbackQuery):
    user = callback.from_user
    if await is_user_banned(user.id):
        await callback.answer("❌ أنت محظور.", show_alert=True)
        return
    await register_user_if_new(user)

    challenge_id = callback.data.removeprefix("join_")

    async with state_lock:
        game = active_challenges.get(challenge_id)
        if not game:
            await callback.answer("التحدي غير موجود.", show_alert=True)
            return
        if user.id == game["p1_id"]:
            await callback.answer("❌ لا يمكنك الانضمام إلى تحديك الخاص.", show_alert=True)
            return
        if game["p2_id"] is not None:
            msg = "أنت بالفعل في هذا التحدي." if game["p2_id"] == user.id else "⚠️ التحدي مكتمل بالفعل!"
            await callback.answer(msg, show_alert=True)
            return
        existing = await find_user_active_challenge(user.id)
        if existing is not None and existing != challenge_id:
            await callback.answer("⚠️ لديك تحدٍ قائم بالفعل!", show_alert=True)
            return

        game["p2_id"] = user.id
        game["p2_name"] = user.full_name
        game["p2_username"] = user.username or ""
        p1_name, p2_name = game["p1_name"], game["p2_name"]

    buttons, row = [], []
    for num in range(1, 7):
        row.append(InlineKeyboardButton(text=str(num), callback_data=f"num_{challenge_id}_{num}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "🎮 <b>اختيار الأرقام:</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {esc(p1_name)}: لم يختر بعد\n"
        f"👤 {esc(p2_name)}: لم يختر بعد\n\n"
        "🎯 يجب أن يختار كل لاعب رقماً مختلفاً عن الآخر!",
        reply_markup=keyboard,
    )
    await callback.answer("✅ انضممت إلى التحدي! اختر رقمك.")


@dp.callback_query(lambda c: c.data and c.data.startswith("num_"))
async def select_number(callback: types.CallbackQuery):
    # الصيغة: num_<uuid>_<digit> — نفصل من اليمين لتفادي أي التباس مع الشرطات السفلية
    _, rest = callback.data.split("num_", 1)
    challenge_id, num_str = rest.rsplit("_", 1)
    num = int(num_str)
    user_id = callback.from_user.id

    async with state_lock:
        game = active_challenges.get(challenge_id)
        if not game:
            await callback.answer("التحدي منتهي.", show_alert=True)
            return
        if user_id not in (game["p1_id"], game["p2_id"]):
            await callback.answer("❌ لست طرفاً في هذا التحدي.", show_alert=True)
            return

        is_p1 = user_id == game["p1_id"]
        my_choice = game["p1_choice"] if is_p1 else game["p2_choice"]
        other_choice = game["p2_choice"] if is_p1 else game["p1_choice"]

        if my_choice is not None:
            await callback.answer("⚠️ اخترت مسبقاً!", show_alert=True)
            return
        if other_choice == num:
            await callback.answer("❌ هذا الرقم اختاره الخصم!", show_alert=True)
            return

        if is_p1:
            game["p1_choice"] = num
        else:
            game["p2_choice"] = num
        await callback.answer(f"✅ اخترت {num}")

        both_chosen = game["p1_choice"] is not None and game["p2_choice"] is not None
        game_snapshot = dict(game)

    chosen = [game_snapshot["p1_choice"], game_snapshot["p2_choice"]]
    buttons, row = [], []
    for n in range(1, 7):
        if n in chosen:
            continue
        row.append(InlineKeyboardButton(text=str(n), callback_data=f"num_{challenge_id}_{n}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    p1_status = f"✅ اختار {game_snapshot['p1_choice']}" if game_snapshot["p1_choice"] else "لم يختر بعد"
    p2_status = f"✅ اختار {game_snapshot['p2_choice']}" if game_snapshot["p2_choice"] else "لم يختر بعد"

    if not both_chosen:
        await callback.message.edit_text(
            "🎮 <b>اختيار الأرقام:</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {esc(game_snapshot['p1_name'])}: {p1_status}\n"
            f"👤 {esc(game_snapshot['p2_name'])}: {p2_status}\n\n"
            "🎯 اختر رقماً مختلفاً عن الخصم!",
            reply_markup=keyboard,
        )
        return

    win_pot = int(game_snapshot["bet"] * 2 * (1 - COMMISSION_RATE))
    await callback.message.edit_text(
        "⏳ <b>تم اختيار الأرقام!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {esc(game_snapshot['p1_name'])}: {game_snapshot['p1_choice']}\n"
        f"👤 {esc(game_snapshot['p2_name'])}: {game_snapshot['p2_choice']}\n"
        f"💰 الرهان لكل: {fmt_money(game_snapshot['bet'])}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📨 تم إخطار الأدمن {esc(ADMIN_HANDLE)} لتأكيد استلام العملة.\n"
        "سيتم بدء اللعبة فور تأكيده."
    )

    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تأكيد وبدء اللعبة", callback_data=f"startgame_{challenge_id}")]
    ])
    await bot.send_message(
        ADMIN_ID,
        "🔔 <b>تحدي جاهز للتأكيد!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {esc(game_snapshot['p1_name'])} (رقم {game_snapshot['p1_choice']}) "
        f"vs {esc(game_snapshot['p2_name'])} (رقم {game_snapshot['p2_choice']})\n"
        f"💰 الرهان: {fmt_money(game_snapshot['bet'])} لكل لاعب\n"
        f"🏆 الجائزة الصافية: {fmt_money(win_pot)}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "اضغط الزر لتأكيد استلام العملة وبدء النرد.",
        reply_markup=admin_keyboard,
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("startgame_"))
async def start_game_by_admin(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ هذا الزر للأدمن فقط.", show_alert=True)
        return
    challenge_id = callback.data.removeprefix("startgame_")

    async with state_lock:
        game = active_challenges.get(challenge_id)
        if not game:
            await callback.answer("⚠️ التحدي غير موجود أو انتهى بالفعل.", show_alert=True)
            return
        if game.get("started"):
            await callback.answer("⚠️ تم تأكيد هذا التحدي مسبقاً.", show_alert=True)
            return
        game["started"] = True

    await callback.answer("✅ تم التأكيد! جارٍ الرمي...")
    try:
        await callback.message.delete()
    except Exception:
        pass
    asyncio.create_task(run_dice_roll(challenge_id))


async def run_dice_roll(challenge_id: str, attempt: int = 1):
    async with state_lock:
        game = active_challenges.get(challenge_id)
        if not game:
            return
        game_snapshot = dict(game)

    p1_name, p2_name = game_snapshot["p1_name"], game_snapshot["p2_name"]
    c1, c2 = game_snapshot["p1_choice"], game_snapshot["p2_choice"]
    bet = game_snapshot["bet"]
    chat_id = game_snapshot["chat_id"]

    if attempt == 1:
        await bot.send_message(
            chat_id,
            "🎲 <b>رمية النرد!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 {esc(p1_name)} توقع {c1}\n"
            f"🎯 {esc(p2_name)} توقع {c2}\n"
            "⏳ جارٍ الرمي..."
        )
    else:
        await bot.send_message(chat_id, f"🔄 إعادة رمي (#{attempt}) بسبب عدم تطابق الرقم. جارٍ الرمي مجدداً...")

    dice = await bot.send_dice(chat_id=chat_id, emoji="🎲")
    actual_val = dice.dice.value
    await asyncio.sleep(3)  # وقت كافٍ لعرض حركة النرد قبل إعلان النتيجة

    if actual_val not in (c1, c2) and attempt >= MAX_REROLLS:
        # لتفادي حلقة إعادة رمي بلا نهاية، تُحسم الجولة عشوائياً بين الاختيارين بعد عدد محاولات معقول
        actual_val = random.choice([c1, c2])

    win_pot = int(bet * 2 * (1 - COMMISSION_RATE))

    if actual_val in (c1, c2):
        winner_is_p1 = actual_val == c1
        winner_id = game_snapshot["p1_id"] if winner_is_p1 else game_snapshot["p2_id"]
        winner_username = game_snapshot["p1_username"] if winner_is_p1 else game_snapshot["p2_username"]
        winner_name = p1_name if winner_is_p1 else p2_name
        loser_id = game_snapshot["p2_id"] if winner_is_p1 else game_snapshot["p1_id"]
        loser_username = game_snapshot["p2_username"] if winner_is_p1 else game_snapshot["p1_username"]
        loser_name = p2_name if winner_is_p1 else p1_name
        winner_choice = c1 if winner_is_p1 else c2

        async with state_lock:
            active_challenges.pop(challenge_id, None)

        await update_user_result(winner_id, winner_username, winner_name, bet, win_pot, True)
        await update_user_result(loser_id, loser_username, loser_name, bet, 0, False)

        await bot.send_message(
            chat_id,
            "🏆 <b>النتيجة النهائية</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"🎲 النرد: {actual_val}\n"
            f"👑 الفائز: {esc(winner_name)}\n"
            f"🎯 رقمه: {winner_choice}\n"
            f"💰 الجائزة: {fmt_money(win_pot)} عملة"
        )
        return

    # لا تطابق بعد — إعادة المحاولة
    await bot.send_message(
        chat_id,
        f"❌ ظهر الرقم {actual_val} ولا يطابق {c1} أو {c2}. سيتم إعادة الرمي تلقائياً..."
    )
    await asyncio.sleep(1.5)
    await run_dice_roll(challenge_id, attempt + 1)


# ---------- الإحصائيات ----------
@dp.message(Command("top"))
async def top_cmd(message: types.Message):
    if await is_user_banned(message.from_user.id):
        return
    async with get_db() as db:
        cur = await db.execute(
            "SELECT full_name, net_profit, wins FROM users WHERE is_banned = 0 "
            "ORDER BY net_profit DESC LIMIT 10"
        )
        rows = await cur.fetchall()
    if not rows:
        await message.reply("🏆 لا توجد إحصائيات بعد.")
        return
    lines = ["🏆 <b>لوحة المتصدرين</b>", "━━━━━━━━━━━━━━━━━━━"]
    for i, (name, profit, wins) in enumerate(rows, 1):
        lines.append(f"🔹 {i}. {esc(name)} | أرباح: {fmt_money(profit)} | فوز: {wins}")
    await message.reply("\n".join(lines))


@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    if await is_user_banned(message.from_user.id):
        return
    user = message.from_user
    async with get_db() as db:
        cur = await db.execute(
            "SELECT wins, losses, total_spent, net_profit FROM users WHERE user_id = ?",
            (user.id,),
        )
        row = await cur.fetchone()
    if not row:
        await message.reply("⚠️ ليس لديك إحصائيات بعد.")
        return
    wins, losses, spent, profit = row
    total = wins + losses
    rate = (wins / total * 100) if total else 0
    text = (
        f"📊 <b>إحصائيات {esc(user.full_name)}:</b>\n"
        f"🎮 مواجهات: {total}\n"
        f"✅ فوز: {wins} | ❌ خسارة: {losses}\n"
        f"📈 نسبة النجاح: {rate:.1f}%\n"
        f"💰 الأرباح: {fmt_money(profit)} عملة"
    )
    await message.reply(text)


# ---------- سيرفر ويب بسيط لإبقاء الخدمة حية (Render/Railway...) ----------
async def handle_ping(request):
    return web.Response(text="Bot is alive!")


async def start_dummy_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("السيرفر يعمل على المنفذ %s", PORT)


# ---------- التشغيل ----------
async def main():
    log.info("🟢 DIPCASINO يعمل...")
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await start_dummy_server()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
