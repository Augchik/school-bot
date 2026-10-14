import os
import json
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТОКЕН_БОТА")
ADMIN_IDS = [123456789]  # Вставь свой Telegram ID

web_app_url = "https://aufchik-school-shop.hf.space"

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

# Хранилища в памяти
DATA_FILE = "data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "products": [],
            "orders": [],
            "blacklist": [],
            "room_number": "не указан",
            "support_tickets": [],
            "accepted_rules": []
        }
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

db = load_data()

# ---------------- FSM ----------------
class AdminStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_price = State()
    waiting_for_stock = State()
    waiting_for_image = State()
    waiting_for_room = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_reply = State()

# ---------------- FASTAPI API ----------------
@app.get("/api/products")
def get_products():
    return db["products"]

# ---------------- BOT HANDLERS ----------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    if user_id in db.get("blacklist", []):
        await message.answer("❌ **Вы заблокированы в системе** за неявку за заказом.\nЕсли это ошибка, обратитесь в поддержку с помощью команды `/teh`.")
        return

    # Проверяем, принял ли пользователь правила
    if user_id not in db.get("accepted_rules", []):
        rules_text = (
            "📜 **Пользовательское соглашение и правила школьного буфета:**\n\n"
            "1. Настоящий бот является информационной системой для предварительного заказа продукции в школьном буфете.\n"
            "2. Оформляя заказ, пользователь подтверждает свое согласие на покупку выбранных позиций и берет на себя **полную личную ответственность** за своевременный самовывоз заказа в назначенное время (дата и номер перемены) и место (раздевалка).\n"
            "3. Заказы, оставленные без забора (неявка), фиксируются администрацией. При повторных нарушениях пользователь автоматически вносится в **черный список (ЧС)** с правом обжалования через техническую поддержку (`/teh`).\n"
            "4. Администрация не несет ответственности за технические сбои связи или задержки учебного процесса.\n\n"
            "Нажимая кнопку ниже, вы подтверждаете ознакомление и полное согласие с правилами."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Я согласен с правилами", callback_data="accept_rules")
        ]])
        await message.answer(rules_text, reply_markup=kb)
        return

    await show_main_menu(message)

async def show_main_menu(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍕 Открыть каталог буфета", web_app=WebAppInfo(url=web_app_url))],
        [InlineKeyboardButton(text="📦 Мои заказы", callback_data="my_orders"), InlineKeyboardButton(text="🆘 Техподдержка", callback_data="open_teh")]
    ])
    await message.answer("Привет! Добро пожаловать в школьный буфет 🥪\n\nИспользуйте меню ниже для работы с системой:", reply_markup=kb)

@dp.callback_query(F.data == "accept_rules")
async def process_accept_rules(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in db["accepted_rules"]:
        db["accepted_rules"].append(user_id)
        save_data(db)
    await callback.message.delete()
    await callback.message.answer("🎉 Спасибо за согласие с правилами!")
    await show_main_menu(callback.message)

@dp.callback_query(F.data == "open_teh")
async def callback_open_teh(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Напишите ваше обращение/жалобу одним сообщением. Поддержка ответит вам в ближайшее время:")
    await state.set_state(SupportStates.waiting_for_message)

# --- Прием заказа из WebApp ---
@dp.message(F.web_app_data)
async def process_web_app_data(message: types.Message):
    user_id = message.from_user.id
    if user_id in db.get("blacklist", []):
        await message.answer("❌ Вы не можете оформлять заказы, так как находитесь в черном списке. Обратитесь в `/teh`.")
        return

    data = json.loads(message.web_app_data.data)

    # Проверка и списание остатков на складе
    for item in data["items"]:
        prod = next((p for p in db["products"] if p["id"] == item["id"]), None)
        if not prod or prod["stock"] < item["quantity"]:
            await message.answer(f"❌ Ошибка! Товара «{item['title']}» осталось недостаточно на складе.")
            return

    # Уменьшаем остатки
    for item in data["items"]:
        for prod in db["products"]:
            if prod["id"] == item["id"]:
                prod["stock"] -= item["quantity"]
    
    order_id = len(db["orders"]) + 1
    order_info = {
        "id": order_id,
        "user_id": user_id,
        "username": message.from_user.username or "без username",
        "full_name": message.from_user.full_name,
        "items": data["items"],
        "total": data["total"],
        "date": data["date"],
        "break_number": data["break_number"],
        "status": "Новый"
    }
    db["orders"].append(order_info)
    save_data(db)

    room = db.get("room_number", "не указан")
    items_text = "\n".join([f"• {i['title']} x{i['quantity']} ({i['price']*i['quantity']} ₽)" for i in data["items"]])

    await message.answer(
        f"✅ **Заказ #{order_id} принят!**\n\n"
        f"📋 **Состав:**\n{items_text}\n\n"
        f"💰 **Сумма:** {data['total']} ₽\n"
        f"📅 **Дата:** {data['date']}\n"
        f"⏰ **Перемена:** {data['break_number']}-я перемена\n"
        f"📍 **Место выдачи:** Раздевалка № {room}\n\n"
        f"⚠️ *Обязательно приходите вовремя! Неявка ведет к внесению в ЧС.*"
    )

    admin_msg = (
        f"📥 **НОВЫЙ ЗАКАЗ #{order_id}**\n"
        f"Покупатель: {message.from_user.full_name} (@{order_info['username']})\n"
        f"Дата: {data['date']} | Перемена: {data['break_number']}\n"
        f"Сумма: {data['total']} ₽\n\n"
        f"Состав:\n{items_text}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_msg)
        except:
            pass

# --- ИСТОРИЯ ЗАКАЗОВ (/history) ---
@dp.message(Command("history"))
async def cmd_history(message: types.Message):
    user_id = message.from_user.id
    user_orders = [o for o in db["orders"] if o["user_id"] == user_id]

    if not user_orders:
        await message.answer("📦 У вас пока нет оформленных заказов.")
        return

    text = "📜 **Ваша история заказов:**\n\n"
    for o in user_orders[-5:]:  включая последние 5 заказов
        items_str = ", ".join([f"{i['title']} x{i['quantity']}" for i in o['items']])
        text += f"• **Заказ #{o['id']}** ({o['status']})\n  📅 {o['date']} | {o['break_number']} перемена\n  🛒 {items_str}\n  💰 Итого: {o['total']} ₽\n\n"

    await message.answer(text)

@dp.callback_query(F.data == "my_orders")
async def callback_my_orders(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_orders = [o for o in db["orders"] if o["user_id"] == user_id]

    if not user_orders:
        await callback.message.answer("📦 У вас пока нет оформленных заказов.")
        return

    text = "📜 **Ваша история заказов:**\n\n"
    for o in user_orders[-5:]:
        items_str = ", ".join([f"{i['title']} x{i['quantity']}" for i in o['items']])
        text += f"• **Заказ #{o['id']}** ({o['status']})\n  📅 {o['date']} | {o['break_number']} перемена\n  🛒 {items_str}\n  💰 Итого: {o['total']} ₽\n\n"

    await callback.message.answer(text)

# --- АДМИН ПАНЕЛЬ (/admin) ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data="admin_del")],
        [InlineKeyboardButton(text="📍 Изменить раздевалку", callback_data="admin_room")],
        [InlineKeyboardButton(text="🚫 Управление ЧС", callback_data="admin_blacklist")]
    ])
    room = db.get("room_number", "не указан")
    await message.answer(f"⚙️ **Админ-панель**\nТекущая раздевалка: **№ {room}**", reply_markup=kb)

@dp.callback_query(F.data == "admin_room")
async def process_admin_room(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите новый номер или описание раздевалки (например: `3 на 1 этаже`):")
    await state.set_state(AdminStates.waiting_for_room)

@dp.message(AdminStates.waiting_for_room)
async def save_room(message: types.Message, state: FSMContext):
    db["room_number"] = message.text.strip()
    save_data(db)
    await message.answer(f"✅ Номер раздевалки обновлен на: **№ {message.text}**")
    await state.clear()

# Добавление товара с количеством (складом)
@dp.callback_query(F.data == "admin_add")
async def admin_add_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите название товара:")
    await state.set_state(AdminStates.waiting_for_title)

@dp.message(AdminStates.waiting_for_title)
async def admin_add_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("Введите цену товара в рублях (например: `50`):")
    await state.set_state(AdminStates.waiting_for_price)

@dp.message(AdminStates.waiting_for_price)
async def admin_add_price(message: types.Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        await state.update_data(price=price)
        await message.answer("Введите количество товара на складе (например: `15`):")
        await state.set_state(AdminStates.waiting_for_stock)
    except ValueError:
        await message.answer("❌ Введите корректное число для цены.")

@dp.message(AdminStates.waiting_for_stock)
async def admin_add_stock(message: types.Message, state: FSMContext):
    try:
        stock = int(message.text.strip())
        await state.update_data(stock=stock)
        await message.answer("Отправьте ссылку на картинку товара (или отправьте текст `нет`):")
        await state.set_state(AdminStates.waiting_for_image)
    except ValueError:
        await message.answer("❌ Введите корректное число для количества.")

@dp.message(AdminStates.waiting_for_image)
async def admin_add_image(message: types.Message, state: FSMContext):
    data = await state.get_data()
    image = message.text.strip()
    if image.lower() == "нет":
        image = "https://via.placeholder.com/150"

    new_id = len(db["products"]) + 1
    product = {
        "id": new_id,
        "title": data["title"],
        "price": data["price"],
        "stock": data["stock"],
        "image": image
    }
    db["products"].append(product)
    save_data(db)

    await message.answer(f"✅ Товар «{data['title']}» успешно добавлен! Остаток: {data['stock']} шт.")
    await state.clear()

@dp.callback_query(F.data == "admin_del")
async def admin_del_start(callback: types.CallbackQuery):
    if not db["products"]:
        await callback.answer("Товаров нет.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🗑 Удалить: {p['title']}", callback_data=f"del_prod_{p['id']}")]
        for p in db["products"]
    ])
    await callback.message.answer("Выберите товар для удаления:", reply_markup=kb)

@dp.callback_query(F.data.startswith("del_prod_"))
async def admin_del_finish(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[2])
    db["products"] = [p for p in db["products"] if p["id"] != prod_id]
    save_data(db)
    await callback.message.edit_text("✅ Товар успешно удален!")

# --- Управление заказами (/zakaz) ---
@dp.message(Command("zakaz"))
async def cmd_zakaz(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    active_orders = [o for o in db["orders"] if o["status"] == "Новый"]
    if not active_orders:
        await message.answer("🎉 Активных заказов нет!")
        return

    for order in active_orders:
        items_str = "\n".join([f"• {i['title']} x{i['quantity']}" for i in order['items']])
        text = (
            f"📦 **Заказ #{order['id']}**\n"
            f"👤 Клиент: {order['full_name']} (@{order['username']})\n"
            f"📅 Дата: {order['date']} | Перемена: {order['break_number']}\n"
            f"💰 Итого: {order['total']} ₽\n\n"
            f"Состав:\n{items_str}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Выдан", callback_data=f"done_{order['id']}")],
            [InlineKeyboardButton(text="❌ Не явился (В ЧС)", callback_data=f"ban_{order['id']}")]
        ])
        await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("done_"))
async def order_done(callback: types.CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    for o in db["orders"]:
        if o["id"] == order_id:
            o["status"] = "Выдан"
            break
    save_data(db)
    await callback.message.edit_text(callback.message.text + "\n\n✅ **СТАТУС: ВЫДАН**")

@dp.callback_query(F.data.startswith("ban_"))
async def order_ban(callback: types.CallbackQuery):
    order_id = int(callback.data.split("_")[1])
    target_user_id = None
    for o in db["orders"]:
        if o["id"] == order_id:
            o["status"] = "Не явился"
            target_user_id = o["user_id"]
            break

    if target_user_id:
        if target_user_id not in db["blacklist"]:
            db["blacklist"].append(target_user_id)
        save_data(db)
        try:
            await bot.send_message(target_user_id, "❌ Вы заблокированы за неявку за заказом. Напишите в поддержку: `/teh`.")
        except:
            pass

    await callback.message.edit_text(callback.message.text + "\n\n❌ **ПОЛЬЗОВАТЕЛЬ ОТПРАВЛЕН В ЧС**")

# --- ТЕХПОДДЕРЖКА (/teh) ---
@dp.message(Command("teh"))
async def cmd_support(message: types.Message, state: FSMContext):
    await message.answer("📝 Напишите ваше обращение/жалобу одним сообщением. Поддержка ответит вам в ближайшее время:")
    await state.set_state(SupportStates.waiting_for_message)

@dp.message(SupportStates.waiting_for_message)
async def process_support_msg(message: types.Message, state: FSMContext):
    ticket_id = len(db.get("support_tickets", [])) + 1
    ticket = {
        "id": ticket_id,
        "user_id": message.from_user.id,
        "username": message.from_user.username or "без username",
        "full_name": message.from_user.full_name,
        "text": message.text,
        "assigned_admin": None
    }
    db["support_tickets"].append(ticket)
    save_data(db)

    await message.answer("✅ Ваше сообщение отправлено администрации. Ожидайте ответа!")
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👨‍💻 Взять в работу", callback_data=f"take_ticket_{ticket_id}")
    ]])
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆘 **ОБРАЩЕНИЕ В ПОДДЕРЖКУ #{ticket_id}**\n"
                f"От: {message.from_user.full_name} (@{ticket['username']})\n"
                f"ID: `{message.from_user.id}`\n\n"
                f"Текст:\n{message.text}",
                reply_markup=kb
            )
        except:
            pass

@dp.callback_query(F.data.startswith("take_ticket_"))
async def take_ticket(callback: types.CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[2])
    admin_name = callback.from_user.full_name

    ticket = next((t for t in db["support_tickets"] if t["id"] == ticket_id), None)
    if not ticket:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return

    if ticket["assigned_admin"] and ticket["assigned_admin"] != admin_name:
        await callback.answer(f"Уже в работе у {ticket['assigned_admin']}!", show_alert=True)
        return

    ticket["assigned_admin"] = admin_name
    save_data(db)

    await callback.message.edit_text(
        callback.message.text + f"\n\n👨‍💻 **Взял в работу:** {admin_name}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✉️ Ответить пользователю", callback_data=f"reply_ticket_{ticket_id}")
        ]])
    )

@dp.callback_query(F.data.startswith("reply_ticket_"))
async def reply_ticket_start(callback: types.CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[2])
    await state.update_data(reply_ticket_id=ticket_id)
    await callback.message.answer("Введите ответ для пользователя:")
    await state.set_state(SupportStates.waiting_for_reply)

@dp.message(SupportStates.waiting_for_reply)
async def send_reply_to_user(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("reply_ticket_id")
    ticket = next((t for t in db["support_tickets"] if t["id"] == ticket_id), None)

    if ticket:
        try:
            await bot.send_message(
                ticket["user_id"],
                f"👨‍💻 **Ответ от администрации (Техподдержка):**\n\n{message.text}"
            )
            await message.answer("✅ Ответ успешно отправлен пользователю!")
        except Exception as e:
            await message.answer(f"❌ Не удалось отправить ответ: {e}")

    await state.clear()
