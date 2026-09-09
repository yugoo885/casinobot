import asyncio
import os
import random
import sys
import time
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web
from aiosqlite import connect
from dotenv import load_dotenv

load_dotenv()  # تحميل المتغيرات من .env

# ------------------- المتغيرات الأساسية -------------------
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))          # معرف الأدمن (رئيسي)
ADMIN_HANDLE = os.getenv("ADMIN_HANDLE", "@YUGO_DZ")  # يمكن تركه افتراضي
SERVER_IP = os.getenv("SERVER_IP", "0.0.0.0")     # عنوان IP للسيرفر (افتراضي 0.0.0.0)
PORT = int(os.getenv("PORT", 10000))              # المنفذ (افتراضي 10000)

MIN_BET = 500000
COOLDOWN_SECONDS = 10
CHALLENGE_TIMEOUT = 180  # 3 دقائق

if not API_TOKEN or not ADMIN_ID:
    raise ValueError("يجب تعيين BOT_TOKEN و ADMIN_ID في ملف .env")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ------------------- قاعدة البيانات غير المتزامنة -------------------
DB_PATH = "casino_stats.db"

@asynccontextmanager
async def get_db():
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
        yield db

async def update_user_db(user_id, username, full_name, bet, win_amount, is_win):
    async with get_db() as db:
        row = await db.execute(
            "SELECT wins, losses, total_spent, net_profit FROM users WHERE user_id = ?",
            (user_id,)
        )
        row = await row.fetchone()
        if row is None:
            wins = 1 if is_win else 0
            losses = 0 if is_win else 1
            total_spent = bet
            net_profit = (win_amount - bet) if is_win else -bet
            await db.execute("""
                INSERT INTO users (user_id, username, full_name, wins, losses, total_spent, net_profit, is_banned)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (user_id, username, full_name, wins, losses, total_spent, net_profit))
        else:
            wins = row[0] + (1 if is_win else 0)
            losses = row[1] + (0 if is_win else 1)
            total_spent = row[2] + bet
            net_profit = row[3] + ((win_amount - bet) if is_win else -bet)
            await db.execute("""
                UPDATE users
                SET username = ?, full_name = ?, wins = ?, losses = ?, total_spent = ?, net_profit = ?
                WHERE user_id = ?
            """, (username, full_name, wins, losses, total_spent, net_profit, user_id))
        await db.commit()

async def is_user_banned(user_id):
    async with get_db() as db:
        row = await db.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        row = await row.fetchone()
        return bool(row and row[0] == 1)

async def register_user_if_new(user: types.User):
    async with get_db() as db:
        row = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
        if await row.fetchone() is None:
            await db.execute("""
                INSERT INTO users (user_id, username, full_name, wins, losses, total_spent, net_profit, is_banned)
                VALUES (?, ?, ?, 0, 0, 0, 0, 0)
            """, (user.id, user.username or "", user.full_name))
            await db.commit()

# ------------------- التحديات النشطة -------------------
active_challenges = {}
user_last_cmd_time = {}

# ------------------- الأوامر الأساسية -------------------
@dp.message(Command("start", "help"))
async def help_cmd(message: types.Message):
    if await is_user_banned(message.from_user.id):
        await message.reply("❌ أنت محظور.")
        return
    await register_user_if_new(message.from_user)
    text = (
        f"🎲 **مرحباً بك في DIPCASINO** 🎲\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"اختر الرهان المناسب لك وابدأ التحدي فوراً:\n\n"
        f"🔹 `/dice 500000` — 500 ألف\n"
        f"🔹 `/dice 1000000` — 1 مليون\n"
        f"🔹 `/dice 1500000` — 1.5 مليون\n"
        f"🔹 `/dice 2000000` — 2 مليون\n"
        f"🔹 `/dice 5000000` — 5 ملايين\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📜 **القوانين:**\n"
        f"• يجب تحويل المبلغ للوسيط {ADMIN_HANDLE} قبل بدء التحدي.\n"
        f"• سيتم إخطار {ADMIN_HANDLE} تلقائياً عند اكتمال اختيار الأرقام.\n"
        f"• عمولة البنك: **5%** من الجائزة.\n\n"
        f"📊 **الأوامر:**\n"
        f"• `/top` — المتصدرين\n"
        f"• `/stats` — إحصائياتك"
    )
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    async with get_db() as db:
        total_users = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await total_users.fetchone())[0]
        total_volume = await db.execute("SELECT SUM(total_spent) FROM users")
        total_volume = (await total_volume.fetchone())[0] or 0
        banned_count = await db.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_count = (await banned_count.fetchone())[0]

    bank_profit = int(total_volume * 0.05)
    text = (
        f"⚙️ **لوحة الأدمن | DIPCASINO**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👥 **المستخدمين:** `{total_users}`\n"
        f"🚫 **المحظورين:** `{banned_count}`\n"
        f"💰 **حجم الرهانات:** `{total_volume:,}`\n"
        f"🏦 **أرباح البنك:** `{bank_profit:,}`\n"
        f"⚔️ **تحديات نشطة:** `{len(active_challenges)}`\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🛠 **الأوامر:**\n"
        f"• `/broadcast [الرسالة]`\n"
        f"• `/ban [ID]`\n"
        f"• `/unban [ID]`"
    )
    await message.reply(text, parse_mode="Markdown")

# أوامر الإدارة (بث، حظر، فك حظر) – مختصرة للاختصار
@dp.message(Command("broadcast"))
async def broadcast_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text_to_send = message.text.replace("/broadcast", "").strip()
    if not text_to_send:
        await message.reply("⚠️ اكتب الرسالة بعد الأمر.")
        return
    async with get_db() as db:
        users = await db.execute("SELECT user_id FROM users WHERE is_banned = 0")
        users = await users.fetchall()
    success = 0
    failed = 0
    for (u_id,) in users:
        try:
            await bot.send_message(u_id, f"📢 **تنويه:**\n\n{text_to_send}", parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    await message.reply(f"✅ أُرسلت إلى `{success}`، فشل `{failed}`")

@dp.message(Command("ban"))
async def ban_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("⚠️ `/ban 123456789`")
        return
    target = int(args[1])
    async with get_db() as db:
        await db.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target,))
        await db.commit()
    await message.reply(f"🚫 تم حظر `{target}`.")

@dp.message(Command("unban"))
async def unban_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("⚠️ `/unban 123456789`")
        return
    target = int(args[1])
    async with get_db() as db:
        await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target,))
        await db.commit()
    await message.reply(f"✅ تم فك الحظر عن `{target}`.")

# ------------------- منطق التحدي -------------------
async def auto_cancel(challenge_id, chat_id, message_id):
    await asyncio.sleep(CHALLENGE_TIMEOUT)
    if challenge_id in active_challenges:
        del active_challenges[challenge_id]
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="⏰ **تم إلغاء التحدي تلقائياً لعدم اكتماله.**"
            )
        except:
            pass

@dp.message(Command("dice"))
async def dice_challenge(message: types.Message):
    user = message.from_user
    if await is_user_banned(user.id):
        await message.reply("❌ أنت محظور.")
        return
    await register_user_if_new(user)

    now = time.time()
    if user.id in user_last_cmd_time:
        if now - user_last_cmd_time[user.id] < COOLDOWN_SECONDS:
            rem = int(COOLDOWN_SECONDS - (now - user_last_cmd_time[user.id]))
            await message.reply(f"⏳ انتظر `{rem}` ثانية.")
            return

    # منع المشاركة في أكثر من تحدٍ
    for ch_id, ch_data in active_challenges.items():
        if ch_data["p1_id"] == user.id or ch_data.get("p2_id") == user.id:
            await message.reply("⚠️ لديك تحدٍ قائم بالفعل!")
            return

    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply(f"❌ التنسيق: `/dice {MIN_BET}`")
        return
    bet = int(args[1])
    if bet < MIN_BET:
        await message.reply(f"⚠️ الحد الأدنى `{MIN_BET:,}`.")
        return

    user_last_cmd_time[user.id] = now

    challenge_id = f"{message.chat.id}_{message.message_id}"
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
        "msg_id": None
    }

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚔️ انضم ({bet:,})", callback_data=f"join_{challenge_id}")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data=f"cancel_{challenge_id}")]
    ])

    sent = await message.reply(
        f"🎲 **تحدي مطابقة الأرقام!**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **المتحدي:** {user.full_name}\n"
        f"💰 **الرهان:** `{bet:,}` لكل لاعب\n"
        f"🏆 **الجائزة:** `{int(bet*2*0.95):,}`\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 اضغط للانضمام!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    active_challenges[challenge_id]["msg_id"] = sent.message_id
    asyncio.create_task(auto_cancel(challenge_id, message.chat.id, sent.message_id))

@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_challenge(callback: types.CallbackQuery):
    challenge_id = callback.data.replace("cancel_", "")
    game = active_challenges.get(challenge_id)
    if not game:
        await callback.answer("التحدي منتهي.", show_alert=True)
        return
    if callback.from_user.id == game["p1_id"] or callback.from_user.id == ADMIN_ID:
        del active_challenges[challenge_id]
        await callback.message.edit_text("🚫 تم إلغاء التحدي.")
        await callback.answer()
    else:
        await callback.answer("❌ ليس لديك صلاحية.", show_alert=True)

@dp.callback_query(F.data.startswith("join_"))
async def join_challenge(callback: types.CallbackQuery):
    user = callback.from_user
    if await is_user_banned(user.id):
        await callback.answer("❌ أنت محظور.", show_alert=True)
        return
    await register_user_if_new(user)

    challenge_id = callback.data.replace("join_", "")
    game = active_challenges.get(challenge_id)
    if not game:
        await callback.answer("التحدي غير موجود.", show_alert=True)
        return
    if game["p2_id"] is not None:
        await callback.answer("⚠️ التحدي مكتمل بالفعل!", show_alert=True)
        return
    if user.id == game["p1_id"]:
        await callback.answer("❌ لا يمكنك التحدي ضد نفسك.", show_alert=True)
        return

    for ch_id, ch_data in active_challenges.items():
        if ch_id != challenge_id and (ch_data["p1_id"] == user.id or ch_data.get("p2_id") == user.id):
            await callback.answer("⚠️ لديك تحدٍ قائم بالفعل!", show_alert=True)
            return

    game["p2_id"] = user.id
    game["p2_name"] = user.full_name
    game["p2_username"] = user.username or ""

    buttons = []
    row = []
    for num in range(1, 7):
        row.append(InlineKeyboardButton(str(num), callback_data=f"num_{challenge_id}_{num}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        f"🎮 **اختيار الأرقام:**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **{game['p1_name']}:** لم يختار بعد\n"
        f"👤 **{game['p2_name']}:** لم يختار بعد\n\n"
        f"🎯 يجب أن يختار كل لاعب رقماً مختلفاً عن الآخر!",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    await callback.answer("✅ تم الانضمام! اختر رقمك.")

@dp.callback_query(F.data.startswith("num_"))
async def select_number(callback: types.CallbackQuery):
    _, challenge_id, num_str = callback.data.split("_")
    num = int(num_str)
    game = active_challenges.get(challenge_id)
    if not game:
        await callback.answer("التحدي منتهي.", show_alert=True)
        return

    user_id = callback.from_user.id
    if user_id not in (game["p1_id"], game["p2_id"]):
        await callback.answer("❌ لست طرفاً في هذا التحدي.", show_alert=True)
        return

    if user_id == game["p1_id"]:
        if game["p1_choice"] is not None:
            await callback.answer("⚠️ اخترت مسبقاً!", show_alert=True)
            return
        if game["p2_choice"] == num:
            await callback.answer("❌ هذا الرقم اختاره الخصم!", show_alert=True)
            return
        game["p1_choice"] = num
        await callback.answer(f"✅ اخترت {num}")
    else:
        if game["p2_choice"] is not None:
            await callback.answer("⚠️ اخترت مسبقاً!", show_alert=True)
            return
        if game["p1_choice"] == num:
            await callback.answer("❌ هذا الرقم اختاره الخصم!", show_alert=True)
            return
        game["p2_choice"] = num
        await callback.answer(f"✅ اخترت {num}")

    if game["p1_choice"] is not None and game["p2_choice"] is not None:
        await callback.message.edit_text(
            f"⏳ **تم اختيار الأرقام!**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **{game['p1_name']}:** `{game['p1_choice']}`\n"
            f"👤 **{game['p2_name']}:** `{game['p2_choice']}`\n"
            f"💰 **الرهان لكل:** `{game['bet']:,}`\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📨 **تم إخطار الأدمن {ADMIN_HANDLE} لتأكيد استلام الأموال.**\n"
            f"سيتم بدء اللعبة فور تأكيده.",
            parse_mode="Markdown"
        )

        admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton("✅ تأكيد وبدء اللعبة", callback_data=f"startgame_{challenge_id}")]
        ])
        await bot.send_message(
            ADMIN_ID,
            f"🔔 **تحدي جاهز للتأكيد!**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {game['p1_name']} (رقم {game['p1_choice']}) vs {game['p2_name']} (رقم {game['p2_choice']})\n"
            f"💰 الرهان: {game['bet']:,} لكل لاعب\n"
            f"🏆 الجائزة الصافية: {int(game['bet']*2*0.95):,}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"اضغط الزر لتأكيد استلام المال وبدء النرد.",
            reply_markup=admin_keyboard,
            parse_mode="Markdown"
        )

@dp.callback_query(F.data.startswith("startgame_"))
async def start_game_by_admin(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ هذا الزر للأدمن فقط.", show_alert=True)
        return
    challenge_id = callback.data.replace("startgame_", "")
    game = active_challenges.get(challenge_id)
    if not game:
        await callback.answer("⚠️ التحدي غير موجود.", show_alert=True)
        return
    await callback.answer("✅ تم التأكيد! جارٍ الرمي...")
    await start_dice_roll(callback.message, challenge_id)
    await callback.message.delete()

async def start_dice_roll(msg: types.Message, challenge_id: str, attempt: int = 1):
    game = active_challenges.get(challenge_id)
    if not game:
        return

    p1_name = game["p1_name"]
    p2_name = game["p2_name"]
    c1 = game["p1_choice"]
    c2 = game["p2_choice"]
    bet = game["bet"]
    chat_id = game["chat_id"]

    if attempt == 1:
        await bot.send_message(
            chat_id,
            f"🎲 **رمية النرد!**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 {p1_name} توقع `{c1}`\n"
            f"🎯 {p2_name} توقع `{c2}`\n"
            f"⏳ المحاولة #{attempt}...",
            parse_mode="Markdown"
        )
    else:
        await bot.send_message(
            chat_id,
            f"🔄 **إعادة رمي (#{attempt})** بسبب عدم تطابق الرقم.\n"
            f"جارٍ الرمي مجدداً...",
            parse_mode="Markdown"
        )

    dice = await bot.send_dice(chat_id=chat_id, emoji="🎲")
    actual_val = dice.dice.value

    if attempt >= 2 and actual_val not in (c1, c2):
        actual_val = random.choice([c1, c2])

    await asyncio.sleep(2)

    total_pot = bet * 2
    win_pot = int(total_pot * 0.95)

    if actual_val == c1:
        del active_challenges[challenge_id]
        await update_user_db(game["p1_id"], game["p1_username"], p1_name, bet, win_pot, True)
        await update_user_db(game["p2_id"], game["p2_username"], p2_name, bet, 0, False)
        await bot.send_message(
            chat_id,
            f"🏆 **النتيجة النهائية**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎲 النرد: `{actual_val}`\n"
            f"👑 **الفائز:** {p1_name}\n"
            f"🎯 رقمه: `{c1}`\n"
            f"💰 الجائزة: `{win_pot:,}` عملة",
            parse_mode="Markdown"
        )
    elif actual_val == c2:
        del active_challenges[challenge_id]
        await update_user_db(game["p2_id"], game["p2_username"], p2_name, bet, win_pot, True)
        await update_user_db(game["p1_id"], game["p1_username"], p1_name, bet, 0, False)
        await bot.send_message(
            chat_id,
            f"🏆 **النتيجة النهائية**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎲 النرد: `{actual_val}`\n"
            f"👑 **الفائز:** {p2_name}\n"
            f"🎯 رقمه: `{c2}`\n"
            f"💰 الجائزة: `{win_pot:,}` عملة",
            parse_mode="Markdown"
        )
    else:
        await bot.send_message(
            chat_id,
            f"❌ ظهر الرقم `{actual_val}` ولا يطابق `{c1}` أو `{c2}`.\n"
            f"سيتم إعادة الرمي تلقائياً...",
            parse_mode="Markdown"
        )
        await asyncio.sleep(1.5)
        await start_dice_roll(msg, challenge_id, attempt + 1)

# ------------------- الإحصائيات -------------------
@dp.message(Command("top"))
async def top_cmd(message: types.Message):
    if await is_user_banned(message.from_user.id):
        return
    async with get_db() as db:
        rows = await db.execute(
            "SELECT full_name, net_profit, wins FROM users WHERE is_banned = 0 ORDER BY net_profit DESC LIMIT 10"
        )
        rows = await rows.fetchall()
    if not rows:
        await message.reply("🏆 لا توجد إحصائيات.")
        return
    text = "🏆 **لوحة المتصدرين**\n━━━━━━━━━━━━━━━━━━━\n"
    for i, (name, profit, wins) in enumerate(rows, 1):
        text += f"🔹 **{i}. {name}** | أرباح: `{profit:,}` | فوز: `{wins}`\n"
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    if await is_user_banned(message.from_user.id):
        return
    user = message.from_user
    async with get_db() as db:
        row = await db.execute(
            "SELECT wins, losses, total_spent, net_profit FROM users WHERE user_id = ?",
            (user.id,)
        )
        row = await row.fetchone()
    if not row:
        await message.reply("⚠️ ليس لديك إحصائيات.")
        return
    wins, losses, spent, profit = row
    total = wins + losses
    rate = (wins / total * 100) if total else 0
    text = (
        f"📊 **إحصائيات {user.full_name}:**\n"
        f"🎮 مواجهات: `{total}`\n"
        f"✅ فوز: `{wins}` | ❌ خسارة: `{losses}`\n"
        f"📈 نسبة النجاح: `{rate:.1f}%`\n"
        f"💰 الأرباح: `{profit:,}` عملة"
    )
    await message.reply(text, parse_mode="Markdown")

# ------------------- سيرفر الويب -------------------
async def handle_ping(request):
    return web.Response(text="Bot is alive!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SERVER_IP, PORT)  # استخدام SERVER_IP و PORT من المتغيرات
    await site.start()
    print(f"✅ السيرفر يعمل على {SERVER_IP}:{PORT}")

# ------------------- التشغيل -------------------
async def main():
    print(f"🟢 DIPCASINO يعمل مع إشعار الأدمن المباشر (IP: {SERVER_IP}, PORT: {PORT})")
    await start_dummy_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
