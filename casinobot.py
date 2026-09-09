import asyncio
import random
import sqlite3
import sys
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# ضبط الترميز لطباعة اللغة العربية بوضوح في موجه الأوامر
sys.stdout.reconfigure(encoding='utf-8')

# --- الإعدادات الأساسية المحدثة ---
API_TOKEN = "8987676069:AAHAKyeUghOdsfJZWBnO7CflLr-u9ovzXrY"
ADMIN_HANDLE = "@YUGO_DZ"
MIN_BET = 500000

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- إدارة قاعدة البيانات (SQLite) ---
conn = sqlite3.connect("casino_stats.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    total_spent INTEGER DEFAULT 0,
    net_profit INTEGER DEFAULT 0
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
        INSERT INTO users (user_id, username, full_name, wins, losses, total_spent, net_profit)
        VALUES (?, ?, ?, ?, ?, ?, ?)
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


# --- الأوامر الرئيسية والترحيب ---
@dp.message(Command("start", "help"))
async def help_cmd(message: types.Message):
  text = (
      f"🎰 DIPCASINO VIP | قائمة الألعاب المتاحة 🎰\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"📜 القوانين والأحكام:\n"
      f"• الحد الأدنى للرهانات: {MIN_BET:,} عملة\n"
      f"• العمولة المقتطعة: 5% لصندوق البنك\n"
      f"• الوسيط والضامن المعتمد: {ADMIN_HANDLE}\n\n"
      f"🎮 الأوامر المتاحة:\n"
      f"1️⃣ آلة الحظ (🎰 Slot Machine): /slot 500000\n"
      f"2️⃣ روليت الكرة الـ 36 (🎡 Roulette): /roulette 500000\n"
      f"🏆 لوحة المتصدرين الشرفية: /top"
  )
  await message.reply(text)


# --- 1️⃣ لعبة آلة الحظ (Slot Machine) ---
@dp.message(Command("slot"))
async def slot_game(message: types.Message):
  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.reply(f"❌ التنسيق غير صحيح!\nاستخدم الأمر هكذا: /slot {MIN_BET}")
    return

  bet = int(args[1])
  if bet < MIN_BET:
    await message.reply(f"⚠️ الحد الأدنى للرهان هو {MIN_BET:,} عملة.")
    return

  user = message.from_user
  dice_msg = await message.answer_dice(emoji="🎰")
  value = dice_msg.dice.value
  await asyncio.sleep(2.5)

  if value in [1, 22, 43]:
    win_amount = int(bet * 2 * 0.95)
    update_user_db(
        user.id,
        user.username or "",
        user.full_name,
        bet,
        win_amount,
        is_win=True,
    )
    res = (
        f"🎉 فوز متوسط!\nحصلت على رمزين متشابهين.\nالمبلغ المستحق بعد اقتطاع"
        f" 5%: {win_amount:,} عملة."
    )
  elif value == 64:
    win_amount = int(bet * 10 * 0.95)
    update_user_db(
        user.id,
        user.username or "",
        user.full_name,
        bet,
        win_amount,
        is_win=True,
    )
    res = (
        f"🔥 بالجائزة الكبرى (777)! 🔥\nفوز ساحق x10!\nالمبلغ المستحق بعد اقتطاع"
        f" 5%: {win_amount:,} عملة."
    )
  else:
    update_user_db(
        user.id, user.username or "", user.full_name, bet, 0, is_win=False
    )
    res = (
        f"❌ حظاً أوفر!\nخسرت الرهان بقيمة {bet:,} عملة.\nتم تحويل المبلغ"
        f" لصندوق الكازينو."
    )

  await message.reply(
      f"👤 اللاعب: {user.full_name}\n💰 الرهان: {bet:,} عملة\n\n{res}"
  )


# --- 2️⃣ لعبة روليت الكرة (Roulette) ---
@dp.message(Command("roulette"))
async def roulette_game(message: types.Message):
  args = message.text.split()
  if len(args) < 2 or not args[1].isdigit():
    await message.reply(
        f"❌ التنسيق غير صحيح!\nاستخدم الأمر هكذا: /roulette {MIN_BET}"
    )
    return

  bet = int(args[1])
  if bet < MIN_BET:
    await message.reply(f"⚠️ الحد الأدنى للرهان هو {MIN_BET:,} عملة.")
    return

  user = message.from_user
  winning_number = random.randint(0, 36)

  if winning_number == 0:
    color = "🟢 أخضر (صفر)"
  elif winning_number % 2 == 0:
    color = "أسود 🖤"
  else:
    color = "أحمر 🔴"

  if winning_number != 0 and winning_number % 2 == 0:
    win_amount = int(bet * 2 * 0.95)
    update_user_db(
        user.id,
        user.username or "",
        user.full_name,
        bet,
        win_amount,
        is_win=True,
    )
    status = f"🎉 فوز!\nاستقرت الكرة على الرقم والرقم زوجي.\nالربح الصافي (خصم 5%): {win_amount:,} عملة."
  else:
    update_user_db(
        user.id, user.username or "", user.full_name, bet, 0, is_win=False
    )
    status = (
        f"❌ خسارة!\nاستقرت الكرة على الرقم الذي لم يطابق توقعك.\nالخسارة:"
        f" {bet:,} عملة."
    )

  text = (
      f"🎡 نتيجة دوران عجلة الروليت:\n"
      f"🎯 الرقم الفائز: {winning_number} ({color})\n"
      f"👤 اللاعب: {user.full_name}\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"{status}"
  )
  await message.reply(text)


# --- 🏆 لوحة المتصدرين الشرفية (Top Players) ---
@dp.message(Command("top"))
async def top_cmd(message: types.Message):
  cursor.execute(
      "SELECT full_name, net_profit, wins FROM users ORDER BY net_profit DESC"
      " LIMIT 10"
  )
  rows = cursor.fetchall()

  if not rows:
    await message.reply("🏆 لا توجد إحصائيات للمتصدرين حتى الآن.")
    return

  text = "🏆 قائمة أعلى 10 أرباح في الكازينو 🏆\n━━━━━━━━━━━━━━━━━━━\n"
  for idx, (name, profit, wins) in enumerate(rows, 1):
    symbol = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "🔹"
    text += f"{symbol} {idx}. {name} | صافي الأرباح: {profit:,} عملة ({wins} فوز)\n"

  await message.reply(text)


# --- تشغيل البوت ---
async def main():
  print("🟢 البوت يعمل الآن بنجاح...")
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())