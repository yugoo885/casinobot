import asyncio
import os
import random
import sqlite3
import sys
import time
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web

# ضبط الترميز
sys.stdout.reconfigure(encoding='utf-8')

# --- الإعدادات الأساسية ---
API_TOKEN = "8987676069:AAHAKyeUghOdsfJZWBnO7CflLr-u9ovzXrY"
ADMIN_HANDLE = "@YUGO_DZ"
ADMIN_ID = 677447724  # تم تحديث الآيدي الخاص بك بنجاح
MIN_BET = 500000
COOLDOWN_SECONDS = 10

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- قاعدة البيانات ---
conn = sqlite3.connect("casino_stats.db")
cursor = conn.cursor()

# جدول المستخدمين الإحصائي
cursor.execute("""
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
conn.commit()


def update_user_db(
    user_id: int,
    username: str,
    full_name: str,
    bet: int,
    win_amount: int,
    is_win: bool,
):
  cursor.execute(
      "SELECT wins, losses, total_spent, net_profit FROM users WHERE user_id"
      " = ?",
      (user_id,),
  )
  row = cursor.fetchone()

  if row is None:
    wins = 1 if is_win else 0
    losses = 0 if is_win else 1
    total_spent = bet
    net_profit = (win_amount - bet) if is_win else -bet
    cursor.execute(
        """
        INSERT INTO users (user_id, username, full_name, wins, losses, total_spent, net_profit, is_banned)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    """,
        (user_id, username, full_name, wins, losses, total_spent, net_profit),
    )
  else:
    wins = row[0] + (1 if is_win else 0)
    losses = row[1] + (0 if is_win else 1)
    total_spent = row[2] + bet
    net_profit = row[3] + ((win_amount - bet) if is_win else -bet)
    cursor.execute(
        """
        UPDATE users
        SET username = ?, full_name = ?, wins = ?, losses = ?, total_spent = ?, net_profit = ?
        WHERE user_id = ?
    """,
        (username, full_name, wins, losses, total_spent, net_profit, user_id),
    )

  conn.commit()


def is_user_banned(user_id: int) -> bool:
  cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
  row = cursor.fetchone()
  return bool(row and row[0] == 1)


def register_user_if_new(user: types.User):
  cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
  if cursor.fetchone() is None:
    cursor.execute(
        """
        INSERT INTO users (user_id, username, full_name, wins, losses, total_spent, net_profit, is_banned)
        VALUES (?, ?, ?, 0, 0, 0, 0, 0)
    """,
        (user.id, user.username or "", user.full_name),
    )
    conn.commit()


active_challenges = {}
user_last_cmd_time = {}


# --- الأوامر والترحيب ---
@dp.message(Command("start", "help"))
async def help_cmd(message: types.Message):
  if is_user_banned(message.from_user.id):
    await message.reply("❌ أنت محظور من استخدام البوت.")
    return

  register_user_if_new(message.from_user)

  text = (
      f"🎲 **DIPCASINO | كازينو النرد الذكي** 🎲\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"اختر رقمك واترك البوت يحسم الجولة بمطابقة رقمك تماماً!\n\n"
      f"📜 **القوانين:**\n"
      f"• الحد الأدنى للرهان: `{MIN_BET:,}` عملة.\n"
      f"• عمولة البنك: **5%** تُقتطع من الفائز.\n"
      f"• الضامن والوسيط: {ADMIN_HANDLE}\n\n"
      f"🎮 **الأوامر:**\n"
      f"🔹 `/dice [المبلغ]` — إنشاء تحدي اختيار أرقام.\n"
      f"🔹 `/top` — لوحة المتصدرين.\n"
      f"🔹 `/stats` — إحصائياتك الشخصية."
  )
  await message.reply(text, parse_mode="Markdown")


# --- ⚙️ لوحة تحكم الأدمن ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
  if message.from_user.id != ADMIN_ID:
    return

  cursor.execute("SELECT COUNT(*), SUM(total_spent) FROM users")
  total_users, total_volume = cursor.fetchone()
  total_volume = total_volume or 0
  bank_profit = int(total_volume * 0.05)

  cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
  banned_count = cursor.fetchone()[0]

  text = (
      f"⚙️ **لوحة تحكم الأدمن | DIPCASINO**\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"👥 **إجمالي المستخدمين:** `{total_users}`\n"
      f"🚫 **المستخدمين المحظورين:** `{banned_count}`\n"
      f"💰 **إجمالي حجم الرهانات:** `{total_volume:,}` عملة\n"
      f"🏦 **أرباح البنك التقريبية (5%):** `{bank_profit:,}` عملة\n"
      f"⚔️ **التحديات النشطة حالياً:** `{len(active_challenges)}`\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"🛠 **الأوامر المتاحة:**\n"
      f"• `/broadcast [الرسالة]` — إذاعة رسالة للجميع.\n"
      f"• `/ban [ID]` — حظر مستخدم.\n"
      f"• `/unban [ID]` — فك حظر مستخدم."
  )
  await message.reply(text, parse_mode="Markdown")


# --- إذاعة للجميع ---
@dp.message(Command("broadcast"))
async def broadcast_cmd(message: types.Message):
  if message.from_user.id != ADMIN_ID:
    return

  text_to_send = message.text.replace("/broadcast", "").strip()
  if not text_to_send:
    await message.reply(
        "⚠️ يرجى كتابة الرسالة بعد الأمر.\nمثال: `/broadcast أهلاً بكم!`"
    )
    return

  cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
  users = cursor.fetchall()

  success = 0
  failed = 0

  msg = await message.reply("🔄 **جارٍ إرسال الإذاعة...**")

  for (u_id,) in users:
    try:
      await bot.send_message(
          u_id, f"📢 **تنويه من الإدارة:**\n\n{text_to_send}", parse_mode="Markdown"
      )
      success += 1
      await asyncio.sleep(0.05)
    except Exception:
      failed += 1

  await msg.edit_text(
      f"✅ **تمت الإذاعة بنجاح!**\n"
      f"• أُرسلت إلى: `{success}` مستخدم\n"
      f"• فشل الإرسال إلى: `{failed}` مستخدم",
      parse_mode="Markdown",
  )


# --- حظر مستخدم ---
@dp.message(Command("ban"))
async def ban_cmd(message: types.Message):
  if message.from_user.id != ADMIN_ID:
    return

  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.reply(
        "⚠️ استخدم الأمر هكذا: `/ban 123456789`", parse_mode="Markdown"
    )
    return

  target_id = int(args[1])
  cursor.execute(
      "UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_id,)
  )
  conn.commit()
  await message.reply(
      f"🚫 تم حظر المستخدم `{target_id}` بنجاح.", parse_mode="Markdown"
  )


# --- فك حظر مستخدم ---
@dp.message(Command("unban"))
async def unban_cmd(message: types.Message):
  if message.from_user.id != ADMIN_ID:
    return

  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.reply(
        "⚠️ استخدم الأمر هكذا: `/unban 123456789`", parse_mode="Markdown"
    )
    return

  target_id = int(args[1])
  cursor.execute(
      "UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_id,)
  )
  conn.commit()
  await message.reply(
      f"✅ تم فك الحظر عن المستخدم `{target_id}` بنجاح.", parse_mode="Markdown"
  )


# --- مؤقت إلغاء التحدي المعلق تلقائياً ---
async def auto_cancel_challenge(
    chat_id: int, message_id: int, challenge_id: str
):
  await asyncio.sleep(180)
  if challenge_id in active_challenges:
    del active_challenges[challenge_id]
    try:
      await bot.edit_message_text(
          chat_id=chat_id,
          message_id=message_id,
          text="⏰ **تم إلغاء التحدي تلقائياً بسبب عدم انضمام منافس خلال 3 دقائق.**",
      )
    except Exception:
      pass


# --- 1. إنشاء التحدي ---
@dp.message(Command("dice"))
async def dice_challenge(message: types.Message):
  user_id = message.from_user.id

  if is_user_banned(user_id):
    await message.reply("❌ أنت محظور من استخدام البوت.")
    return

  register_user_if_new(message.from_user)
  now = time.time()

  if user_id in user_last_cmd_time:
    elapsed = now - user_last_cmd_time[user_id]
    if elapsed < COOLDOWN_SECONDS:
      remaining = int(COOLDOWN_SECONDS - elapsed)
      await message.reply(
          f"⏳ **مهلاً!** يرجى الانتظار `{remaining}` ثوانٍ قبل إنشاء تحدٍّ جديد.",
          parse_mode="Markdown",
      )
      return

  for ch_id, ch_data in active_challenges.items():
    if ch_data["p1_id"] == user_id:
      await message.reply(
          "⚠️ **لديك تحدٍّ قائم بالفعل!** قم بإلغائه أو انتظره حتى ينتهي.",
          parse_mode="Markdown",
      )
      return

  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.reply(
        f"❌ **التنسيق غير صحيح!**\nارسل الأمر هكذا:\n`/dice {MIN_BET}`",
        parse_mode="Markdown",
    )
    return

  bet = int(args[1])
  if bet < MIN_BET:
    await message.reply(
        f"⚠️ **الحد الأدنى للرهان هو `{MIN_BET:,}` عملة.**", parse_mode="Markdown"
    )
    return

  user_last_cmd_time[user_id] = now
  challenger = message.from_user
  challenge_id = f"{message.chat.id}_{message.message_id}"

  active_challenges[challenge_id] = {
      "p1_id": challenger.id,
      "p1_name": challenger.full_name,
      "p1_username": challenger.username or "",
      "p1_choice": None,
      "p2_id": None,
      "p2_name": None,
      "p2_username": None,
      "p2_choice": None,
      "bet": bet,
  }

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text=f"⚔️ دخول التحدي ({bet:,} عملة)",
                  callback_data=f"join_{challenge_id}",
              )
          ],
          [
              InlineKeyboardButton(
                  text="❌ إلغاء التحدي", callback_data=f"cancel_{challenge_id}"
              )
          ],
      ]
  )

  sent_msg = await message.reply(
      f"🎲 **تحدي المطابقة الذكي!**\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"👤 **المتحدي:** {challenger.full_name}\n"
      f"💰 **الرهان:** `{bet:,}` عملة\n"
      f"🏆 **الجائزة الصافية:** `{int(bet * 2 * 0.95):,}` عملة\n"
      f"🎯 **الشرط:** المطابقة التامة لرقم النرد!\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"اضغط على الزر للانضمام واختيار الرقم!",
      reply_markup=keyboard,
      parse_mode="Markdown",
  )

  asyncio.create_task(
      auto_cancel_challenge(
          message.chat.id, sent_msg.message_id, challenge_id
      )
  )


# --- إلغاء التحدي ---
@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_challenge(callback: types.CallbackQuery):
  challenge_id = callback.data.replace("cancel_", "")
  if challenge_id in active_challenges:
    if callback.from_user.id == active_challenges[challenge_id]["p1_id"]:
      del active_challenges[challenge_id]
      await callback.message.edit_text("🚫 **تم إلغاء التحدي.**")
    else:
      await callback.answer("❌ يمكن لمنشئ التحدي فقط إلغاؤه!", show_alert=True)


# --- 2. انضمام المنافس ---
@dp.callback_query(F.data.startswith("join_"))
async def join_challenge(callback: types.CallbackQuery):
  if is_user_banned(callback.from_user.id):
    await callback.answer("❌ أنت محظور من استخدام البوت.", show_alert=True)
    return

  register_user_if_new(callback.from_user)

  challenge_id = callback.data.replace("join_", "")
  if challenge_id not in active_challenges:
    await callback.answer("⚠️ هذا التحدي غير موجود أو انتهى!", show_alert=True)
    return

  game = active_challenges[challenge_id]
  p2 = callback.from_user

  if p2.id == game["p1_id"]:
    await callback.answer("❌ لا يمكنك التحدي ضد نفسك!", show_alert=True)
    return

  game["p2_id"] = p2.id
  game["p2_name"] = p2.full_name
  game["p2_username"] = p2.username or ""

  buttons = []
  row = []
  for num in range(1, 7):
    row.append(
        InlineKeyboardButton(
            text=f"️⃣ {num}", callback_data=f"num_{challenge_id}_{num}"
        )
    )
    if len(row) == 3:
      buttons.append(row)
      row = []

  keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

  await callback.message.edit_text(
      f"🎮 **اختر رقمك المتوقع (1-6):**\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"👤 **{game['p1_name']}:** لم يسيطر بعد\n"
      f"👤 **{game['p2_name']}:** لم يسيطر بعد\n\n"
      f"⚠️ يجب على كل لاعب اختيار رقم مختلف عن الآخر!",
      reply_markup=keyboard,
      parse_mode="Markdown",
  )


# --- 3. اختيار الأرقام ---
@dp.callback_query(F.data.startswith("num_"))
async def select_number(callback: types.CallbackQuery):
  _, challenge_id, num_str = callback.data.split("_")
  num = int(num_str)

  if challenge_id not in active_challenges:
    await callback.answer("⚠️ التحدي منتهي!", show_alert=True)
    return

  game = active_challenges[challenge_id]
  user_id = callback.from_user.id

  if user_id not in [game["p1_id"], game["p2_id"]]:
    await callback.answer("❌ أنت لست طرفاً في هذا التحدي!", show_alert=True)
    return

  if user_id == game["p1_id"]:
    if game["p1_choice"] is not None:
      await callback.answer("⚠️ اخترت رقمك مسبقاً!", show_alert=True)
      return
    if game["p2_choice"] == num:
      await callback.answer("❌ اختار خصمك هذا الرقم!", show_alert=True)
      return
    game["p1_choice"] = num
    await callback.answer(f"✅ تم اختيار الرقم {num}")

  elif user_id == game["p2_id"]:
    if game["p2_choice"] is not None:
      await callback.answer("⚠️ اخترت رقمك مسبقاً!", show_alert=True)
      return
    if game["p1_choice"] == num:
      await callback.answer("❌ اختار خصمك هذا الرقم!", show_alert=True)
      return
    game["p2_choice"] = num
    await callback.answer(f"✅ تم اختيار الرقم {num}")

  if game["p1_choice"] is not None and game["p2_choice"] is not None:
    await start_dice_roll(callback.message, challenge_id)


# --- 4. خوارزمية الرمي الذكية والتنفيذ ---
async def start_dice_roll(
    message: types.Message, challenge_id: str, attempt: int = 1
):
  if challenge_id not in active_challenges:
    return

  game = active_challenges[challenge_id]

  p1_name = game["p1_name"]
  p2_name = game["p2_name"]
  c1 = game["p1_choice"]
  c2 = game["p2_choice"]
  bet = game["bet"]

  if attempt == 1:
    await message.edit_text(
        f"🔥 **تم اختيار الأرقام!**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 **توقع {p1_name}:** الرقم `{c1}`\n"
        f"🎯 **توقع {p2_name}:** الرقم `{c2}`\n\n"
        f"🎲 **رمية النرد (المحاولة {attempt})...**",
        parse_mode="Markdown",
    )
  else:
    await message.answer(
        f"🔄 **إعادة رمي تلقائية (المحاولة {attempt})...**\n"
        f"جارٍ الرمي مجدداً لحسم النتيجة بين `{c1}` و `{c2}`!",
        parse_mode="Markdown",
    )

  dice_msg = await bot.send_dice(chat_id=message.chat.id, emoji="🎲")
  actual_val = dice_msg.dice.value

  if attempt >= 2 and actual_val not in [c1, c2]:
    actual_val = random.choice([c1, c2])

  await asyncio.sleep(3.5)

  total_pot = bet * 2
  win_pot = int(total_pot * 0.95)

  if actual_val == c1:
    del active_challenges[challenge_id]
    update_user_db(
        game["p1_id"], game["p1_username"], p1_name, bet, win_pot, is_win=True
    )
    update_user_db(
        game["p2_id"], game["p2_username"], p2_name, bet, 0, is_win=False
    )
    await message.answer(
        f"📊 **النتيجة النهائية للمواجهة:**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎲 **رقم النرد:** `{actual_val}`\n\n"
        f"👑 **الفائز:** {p1_name}\n"
        f"🎯 **طابق رقمه المختار:** `{c1}`\n"
        f"💰 **الجائزة:** `{win_pot:,}` عملة",
        parse_mode="Markdown",
    )

  elif actual_val == c2:
    del active_challenges[challenge_id]
    update_user_db(
        game["p2_id"], game["p2_username"], p2_name, bet, win_pot, is_win=True
    )
    update_user_db(
        game["p1_id"], game["p1_username"], p1_name, bet, 0, is_win=False
    )
    await message.answer(
        f"📊 **النتيجة النهائية للمواجهة:**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎲 **رقم النرد:** `{actual_val}`\n\n"
        f"👑 **الفائز:** {p2_name}\n"
        f"🎯 **طابق رقمه المختار:** `{c2}`\n"
        f"💰 **الجائزة:** `{win_pot:,}` عملة",
        parse_mode="Markdown",
    )

  else:
    await message.answer(
        f"❌ **ظهر الرقم `{actual_val}`!**\n"
        f"لم يطابق أي من الرقمين (`{c1}` و `{c2}`).\n"
        f"⚡ **إعادة الرمي تلقائياً...**"
    )
    await asyncio.sleep(1.5)
    await start_dice_roll(message, challenge_id, attempt=attempt + 1)


# --- 🏆 المتصدرين ---
@dp.message(Command("top"))
async def top_cmd(message: types.Message):
  if is_user_banned(message.from_user.id):
    return
  cursor.execute(
      "SELECT full_name, net_profit, wins FROM users WHERE is_banned = 0 ORDER"
      " BY net_profit DESC LIMIT 10"
  )
  rows = cursor.fetchall()
  if not rows:
    await message.reply("🏆 لا توجد إحصائيات بعد.")
    return
  text = "🏆 **لوحة أبطال المطابقة** 🏆\n━━━━━━━━━━━━━━━━━━━\n"
  for idx, (name, profit, wins) in enumerate(rows, 1):
    text += f"🔹 **{idx}. {name}** | أرباح: `{profit:,}` | فوز: `{wins}`\n"
  await message.reply(text, parse_mode="Markdown")


# --- 📈 الإحصائيات ---
@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
  if is_user_banned(message.from_user.id):
    return
  user = message.from_user
  cursor.execute(
      "SELECT wins, losses, total_spent, net_profit FROM users WHERE user_id"
      " = ?",
      (user.id,),
  )
  row = cursor.fetchone()
  if row is None:
    await message.reply("⚠️ ليس لديك إحصائيات بعد.")
    return
  wins, losses, total_spent, net_profit = row
  total = wins + losses
  rate = (wins / total * 100) if total > 0 else 0
  text = (
      f"📊 **إحصائيات {user.full_name}:**\n"
      f"🎮 المواجهات: `{total}` | ✅ الفوز: `{wins}` | 📈 النجاح: `{rate:.1f}%`\n"
      f"💰 الأرباح: `{net_profit:,}` عملة"
  )
  await message.reply(text, parse_mode="Markdown")


# --- سيرفر Render ---
async def handle_ping(request):
  return web.Response(text="Admin Panel Enabled Dice Bot Running!")


async def start_dummy_server():
  app = web.Application()
  app.router.add_get("/", handle_ping)
  runner = web.AppRunner(app)
  await runner.setup()
  port = int(os.environ.get("PORT", 10000))
  site = web.TCPSite(runner, "0.0.0.0", port)
  await site.start()


async def main():
  print("🟢 البوت يعمل ومربوط بالآيدي الخاص بك الآن...")
  await start_dummy_server()
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
