import asyncio
import datetime
import json
import logging
import os
import aiosqlite
import pytz
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

BOT_TOKEN = "8613597120:AAHcGm6MKyNZq5m1hVwIPf6G6ENSe_Q7uRU"
ADMIN_IDS = [5114807748, 5545174715, 6319212519, 5248254181]
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "shop.db"


async def init_db():
  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                price INTEGER,
                image TEXT
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                items TEXT,
                total INTEGER,
                status TEXT DEFAULT 'Новый',
                taken_by TEXT DEFAULT NULL,
                created_at TEXT
            )
        """)
    await db.commit()

    async with db.execute(
        "SELECT value FROM settings WHERE key='work_hours'"
    ) as cursor:
      row = await cursor.fetchone()
      if not row:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES ('work_hours', '16:00-18:00')"
        )
        await db.commit()


async def get_work_hours():
  async with aiosqlite.connect(DB_FILE) as db:
    async with db.execute(
        "SELECT value FROM settings WHERE key='work_hours'"
    ) as cursor:
      row = await cursor.fetchone()
      return row[0] if row else "16:00-18:00"


async def is_shop_open() -> bool:
  hours_str = await get_work_hours()
  try:
    start_str, end_str = hours_str.split("-")
    tz = pytz.timezone("Europe/Moscow")
    now = datetime.datetime.now(tz)
    current_time = now.strftime("%H:%M")
    return start_str <= current_time <= end_str
  except Exception:
    return True


@app.get("/api/products")
async def api_get_products():
  async with aiosqlite.connect(DB_FILE) as db:
    db.row_factory = aiosqlite.Row
    async with db.execute("SELECT * FROM products") as cursor:
      products = [dict(row) async for row in cursor]
  return products


@app.get("/api/settings")
async def api_get_settings():
  hours = await get_work_hours()
  open_status = await is_shop_open()
  return {"work_hours": hours, "is_open": open_status}


class AdminStates(StatesGroup):
  waiting_for_title = State()
  waiting_for_price = State()
  waiting_for_image = State()
  waiting_for_hours = State()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
  # Сюда позже вставишь ссылку на свою витрину с Hugging Face Static
  web_app_url = "https://huggingface.co/spaces/Aufchik/school-shop"
  kb = InlineKeyboardMarkup(
      inline_keyboard=[[
          InlineKeyboardButton(
              text="🍕 Открыть каталог буфета",
              web_app=WebAppInfo(url=web_app_url),
          )
      ]]
  )
  await message.answer(
      "Привет! 👋 Добро пожаловать в школьный буфет.\n"
      "Нажми кнопку ниже, чтобы открыть каталог товаров:",
      reply_markup=kb,
  )


@dp.message(F.web_app_data)
async def process_web_app_order(message: types.Message):
  if not await is_shop_open():
    hours = await get_work_hours()
    await message.answer(
        f"❌ Извините, магазин сейчас закрыт!\nПрием заказов по графику: {hours}"
    )
    return

  try:
    cart = json.loads(message.web_app_data.data)
    total = sum(item["price"] * item["count"] for item in cart)
    items_text = "\n".join(
        [
            f"• {i['title']} x{i['count']} — {i['price'] * i['count']} ₽"
            for i in cart
        ]
    )

    tz = pytz.timezone("Europe/Moscow")
    created_at = datetime.datetime.now(tz).strftime("%H:%M %d.%m.%Y")

    async with aiosqlite.connect(DB_FILE) as db:
      await db.execute(
          "INSERT INTO orders (user_id, username, full_name, items, total,"
          " created_at) VALUES (?, ?, ?, ?, ?, ?)",
          (
              message.from_user.id,
              message.from_user.username or "нет",
              message.from_user.full_name,
              items_text,
              total,
              created_at,
          ),
      )
      await db.commit()

    await message.answer(
        f"✅ **Заказ успешно оформлен!**\n\n{items_text}\n\n💰 **Итого:** {total}"
        f" ₽\n\nМы скоро свяжемся с вами для выдачи!"
    )

    for admin_id in ADMIN_IDS:
      try:
        await bot.send_message(
            admin_id,
            f"🚨 **Новый заказ!**\n\nОт: {message.from_user.full_name}"
            f" (@{message.from_user.username or 'нет'})\n{items_text}\n\n💰"
            f" Сумма: {total} ₽\n\nИспользуйте /zakaz чтобы взять заказ в работу.",
            parse_mode="Markdown",
        )
      except:
        pass

  except Exception as e:
    logging.error(f"Order error: {e}")
    await message.answer("Произошла ошибка при обработке заказа.")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
  if message.from_user.id not in ADMIN_IDS:
    return
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="➕ Добавить товар", callback_data="adm_add"
              ),
              InlineKeyboardButton(
                  text="🗑 Удалить товар", callback_data="adm_del_list"
              ),
          ],
          [
              InlineKeyboardButton(
                  text="⏰ Изменить время работы", callback_data="adm_hours"
              )
          ],
      ]
  )
  await message.answer(
      "👑 **Панель администратора буфета:**",
      reply_markup=kb,
      parse_mode="Markdown",
  )


@dp.callback_query(F.data == "adm_hours")
async def admin_change_hours(callback: types.CallbackQuery, state: FSMContext):
  if callback.from_user.id not in ADMIN_IDS:
    return
  current = await get_work_hours()
  await callback.message.answer(
      f"Текущее время приема заказов: `{current}`\n\nВведите новый интервал"
      " в формате `ЧЧ:ММ-ЧЧ:ММ` (например: `15:30-17:30`):"
  )
  await state.set_state(AdminStates.waiting_for_hours)
  await callback.answer()


@dp.message(AdminStates.waiting_for_hours)
async def save_new_hours(message: types.Message, state: FSMContext):
  if message.from_user.id not in ADMIN_IDS:
    return
  new_hours = message.text.strip()
  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('work_hours',"
        " ?)",
        (new_hours,),
    )
    await db.commit()
  await state.clear()
  await message.answer(f"✅ Время приема заказов успешно изменено на: {new_hours}")


@dp.callback_query(F.data == "adm_add")
async def admin_add_product(callback: types.CallbackQuery, state: FSMContext):
  if callback.from_user.id not in ADMIN_IDS:
    return
  await callback.message.answer("Введите название нового товара:")
  await state.set_state(AdminStates.waiting_for_title)
  await callback.answer()


@dp.message(AdminStates.waiting_for_title)
async def admin_got_title(message: types.Message, state: FSMContext):
  await state.update_data(title=message.text)
  await message.answer("Введите цену товара (числом, например: 120):")
  await state.set_state(AdminStates.waiting_for_price)


@dp.message(AdminStates.waiting_for_price)
async def admin_got_price(message: types.Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("Цена должна быть числом! Попробуйте снова:")
    return
  await state.update_data(price=int(message.text))
  await message.answer(
      "Отправьте прямую ссылку на картинку товара (или `-` если без картинки):"
  )
  await state.set_state(AdminStates.waiting_for_image)


@dp.message(AdminStates.waiting_for_image)
async def admin_got_image(message: types.Message, state: FSMContext):
  data = await state.get_data()
  title = data["title"]
  price = data["price"]
  image = "" if message.text.strip() == "-" else message.text.strip()

  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute(
        "INSERT INTO products (title, price, image) VALUES (?, ?, ?)",
        (title, price, image),
    )
    await db.commit()

  await state.clear()
  await message.answer(f"✅ Товар **{title}** ({price} ₽) успешно добавлен!")


@dp.callback_query(F.data == "adm_del_list")
async def admin_del_list(callback: types.CallbackQuery):
  if callback.from_user.id not in ADMIN_IDS:
    return
  async with aiosqlite.connect(DB_FILE) as db:
    db.row_factory = aiosqlite.Row
    async with db.execute("SELECT * FROM products") as cursor:
      products = [dict(row) async for row in cursor]

  if not products:
    await callback.message.answer("Список товаров пуст.")
    await callback.answer()
    return

  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text=f"❌ Удалить: {p['title']} ({p['price']}₽)",
                  callback_data=f"del_{p['id']}",
              )
          ]
          for p in products
      ]
  )
  await callback.message.answer(
      "Выберите товар для удаления:", reply_markup=kb
  )
  await callback.answer()


@dp.callback_query(F.data.startswith("del_"))
async def admin_delete_item(callback: types.CallbackQuery):
  if callback.from_user.id not in ADMIN_IDS:
    return
  product_id = int(callback.data.split("_")[1])
  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    await db.commit()
  await callback.message.edit_text("✅ Товар удален из каталога.")
  await callback.answer()


@dp.message(Command("zakaz"))
async def cmd_zakaz(message: types.Message):
  if message.from_user.id not in ADMIN_IDS:
    return

  async with aiosqlite.connect(DB_FILE) as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT * FROM orders WHERE status != 'Выполнен' ORDER BY id DESC LIMIT 10"
    ) as cursor:
      orders = [dict(row) async for row in cursor]

  if not orders:
    await message.answer("📭 Активных заказов пока нет.")
    return

  for o in orders:
    taken_text = (
        f"👤 **Взял в работу:** {o['taken_by']}"
        if o["taken_by"]
        else "⚠️ Никем не взят"
    )

    buttons = []
    if not o["taken_by"]:
      buttons.append([
          InlineKeyboardButton(
              text="🙋‍♂️ Взять себе", callback_data=f"take_{o['id']}"
          )
      ])
    else:
      buttons.append([
          InlineKeyboardButton(
              text="✅ Завершить заказ", callback_data=f"done_{o['id']}"
          )
      ])

    if o["username"] and o["username"] != "нет":
      buttons.append([
          InlineKeyboardButton(
              text="💬 Написать покупателю",
              url=f"https://t.me/{o['username']}",
          )
      ])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = (
        f"📦 **Заказ #{o['id']}** ({o['created_at']})\n"
        f"👤 Покупатель: {o['full_name']} (@{o['username']})\n\n"
        f"{o['items']}\n\n"
        f"💰 **Сумма:** {o['total']} ₽\n"
        f"📌 **Статус:** {o['status']} | {taken_text}"
    )
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("take_"))
async def take_order(callback: types.CallbackQuery):
  if callback.from_user.id not in ADMIN_IDS:
    return
  order_id = int(callback.data.split("_")[1])
  admin_name = callback.from_user.full_name

  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute(
        "UPDATE orders SET status = 'В работе', taken_by = ? WHERE id = ?",
        (admin_name, order_id),
    )
    await db.commit()

  await callback.message.edit_text(
      f"{callback.message.text}\n\n📌 **Статус:** В работе 👤 **Взял:**"
      f" {admin_name}",
      reply_markup=callback.message.reply_markup,
      parse_mode="Markdown",
  )
  await callback.answer(f"Вы взяли заказ #{order_id} в работу!")


@dp.callback_query(F.data.startswith("done_"))
async def finish_order(callback: types.CallbackQuery):
  if callback.from_user.id not in ADMIN_IDS:
    return
  order_id = int(callback.data.split("_")[1])

  async with aiosqlite.connect(DB_FILE) as db:
    await db.execute(
        "UPDATE orders SET status = 'Выполнен' WHERE id = ?", (order_id,)
    )
    await db.commit()

  await callback.message.edit_text(
      f"✅ **Заказ #{order_id} выполнен и закрыт.**", parse_mode="Markdown"
  )
  await callback.answer("Заказ закрыт!")


async def main():
  await init_db()
  config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
  server = uvicorn.Server(config)
  await asyncio.gather(server.serve(), dp.start_polling(bot))


if __name__ == "__main__":
  asyncio.run(main())
