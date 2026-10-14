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
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.isdigit()]

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

# Хранилище данных
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
        data = json.load(f)
        # Авто-фикс: если у товара нет поля stock, ставим 0
        for p in data.get("products", []):
            if "stock" not in p:
                p["stock"] = 0
        return data

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

db = load_data()

# ---------------- FSM (Состояния) ----------------
class AdminStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_price = State()
    waiting_for_stock = State()
    waiting_for_image = State()
    waiting_for_room = State()
    waiting_for_custom_stock = State()
    waiting_for_new_price = State()

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
        await message.answer("❌ **Вы заблокированы в системе** за неявку за заказом.\nОбратитесь в поддержку с помощью команды `/teh`.")
        return

    if user_id not in db.get("accepted_rules", []):
        rules_text = (
            "📜 **Правила школьного буфета:**\n\n"
            "1. Бот предназначен для предзаказа продукции.\n"
            "2. Оформляя заказ, вы берете на себя **ответственность** за своевременный самовывоз в назначенную дату и перемену.\n"
            "3. За неявку без предупреждения следует занесение в **черный список (ЧС)**.\n\n"
            "Нажмите кнопку ниже, чтобы принять правила."
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
    await message.answer("Привет! Добро пожаловать в школьный буфет 🥪\n\nИспользуйте меню ниже:", reply_markup=kb)

@dp.callback_query(F.data == "accept_rules")
async def process_accept_rules(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if "accepted_rules" not in db:
        db["accepted_rules"] = []
    if user_id not in db["accepted_rules"]:
        db["accepted_rules"].append(user_id)
        save_data(db)
    await callback.message.delete()
    await callback.message.answer("🎉 Спасибо за согласие с правилами!")
    await show_main_menu(callback.message)

@dp.callback_query(F.data == "open_teh")
async def callback_open_teh(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Напишите ваше обращение/жалобу одним сообщением:")
    await state.set_state(SupportStates.waiting_for_message)

# --- Прием заказа из WebApp ---
@dp.message(F.web_app_data)
async def process_web_app_data(message: types.Message):
    user_id = message.from_user.id
    if user_id in db.get("blacklist", []):
        await message.answer("❌ Вы заблокированы. Обратитесь в `/teh`.")
        return

    data = json.loads(message.web_app_data.data)

    # Проверка и списание остатков
    for item in data["items"]:
        prod = next((p for p in db["products"] if p["id"] == item["id"]), None)
        if not prod or prod.get("stock", 0) < item["quantity"]:
            await message.answer(f"❌ Ошибка! Товара «{item['title']}» осталось недостаточно на складе.")
            return

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
        f"⚠️ *Обязательно приходите вовремя!*"
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

# --- АДМИН ПАНЕЛЬ (/admin) ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Наличие и склад", callback_data="admin_stocks")],
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add"), InlineKeyboardButton(text="🗑 Удалить товар", callback_data="admin_del")],
        [InlineKeyboardButton(text="📍 Изменить раздевалку", callback_data="admin_room")],
    ])
    room = db.get("room_number", "не указан")
    await message.answer(f"⚙️ **Админ-панель буфета**\nРаздевалка выдачи: **№ {room}**\n\nВыберите действие:", reply_markup=kb)

# Меню управления наличием товаров
@dp.callback_query(F.data == "admin_stocks")
async def admin_stocks_list(callback: types.CallbackQuery):
    if not db["products"]:
        await callback.answer("Товаров пока нет. Добавьте первый товар!", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{p['title']} (Остаток: {p.get('stock',0)} шт)", callback_data=f"manage_p_{p['id']}")]
        for p in db["products"]
    ])
    await callback.message.answer("📦 **Выберите товар для управления наличием:**", reply_markup=kb)

@dp.callback_query(F.data.startswith("manage_p_"))
async def manage_product(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[2])
    prod = next((p for p in db["products"] if p["id"] == prod_id), None)
    if not prod:
        await callback.answer("Товар не найден", show_alert=True)
        return

    text = (
        f"🍕 **Товар:** {prod['title']}\n"
        f"💰 **Цена:** {prod['price']} ₽\n"
        f"📦 **В наличии:** {prod.get('stock', 0)} шт."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="+1", callback_data=f"add_st_{prod_id}_1"),
            InlineKeyboardButton(text="+5", callback_data=f"add_st_{prod_id}_5"),
            InlineKeyboardButton(text="+10", callback_data=f"add_st_{prod_id}_10")
        ],
        [
            InlineKeyboardButton(text="-1", callback_data=f"add_st_{prod_id}_-1"),
            InlineKeyboardButton(text="-5", callback_data=f"add_st_{prod_id}_-5"),
            InlineKeyboardButton(text="Обнулить (0)", callback_data=f"set_st_{prod_id}_0")
        ],
        [InlineKeyboardButton(text="✏️ Ввести точное число", callback_data=f"custom_st_{prod_id}")],
        [InlineKeyboardButton(text="🔙 К списку товаров", callback_data="admin_stocks")]
    ])
    await callback.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("add_st_"))
async def change_stock_delta(callback: types.CallbackQuery):
    _, _, prod_id, delta = callback.data.split("_")
    prod_id, delta = int(prod_id), int(delta)

    for p in db["products"]:
        if p["id"] == prod_id:
            p["stock"] = max(0, p.get("stock", 0) + delta)
            break
    save_data(db)
    await manage_product(callback)

@dp.callback_query(F.data.startswith("set_st_"))
async def set_stock_exact(callback: types.CallbackQuery):
    _, _, prod_id, val = callback.data.split("_")
    prod_id, val = int(prod_id), int(val)

    for p in db["products"]:
        if p["id"] == prod_id:
            p["stock"] = val
            break
    save_data(db)
    await manage_product(callback)

@dp.callback_query(F.data.startswith("custom_st_"))
async def custom_stock_start(callback: types.CallbackQuery, state: FSMContext):
    prod_id = int(callback.data.split("_")[2])
    await state.update_data(edit_prod_id=prod_id)
    await callback.message.answer("Введите точное количество товара на складе (числом):")
    await state.set_state(AdminStates.waiting_for_custom_stock)

@dp.message(AdminStates.waiting_for_custom_stock)
async def custom_stock_save(message: types.Message, state: FSMContext):
    try:
        val = int(message.text.strip())
        data = await state.get_data()
        prod_id = data.get("edit_prod_id")

        for p in db["products"]:
            if p["id"] == prod_id:
                p["stock"] = max(0, val)
                break
        save_data(db)
        await message.answer("✅ Наличие товара успешно обновлено!")
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное целое число.")

# Изменение раздевалки
@dp.callback_query(F.data == "admin_room")
async def process_admin_room(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите новый номер раздевалки:")
    await state.set_state(AdminStates.waiting_for_room)

@dp.message(AdminStates.waiting_for_room)
async def save_room(message: types.Message, state: FSMContext):
    db["room_number"] = message.text.strip()
    save_data(db)
    await message.answer(f"✅ Номер раздевалки обновлен на: **№ {message.text}**")
    await state.clear()

# Добавление товара
@dp.callback_query(F.data == "admin_add")
async def admin_add_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите название товара:")
    await state.set_state(AdminStates.waiting_for_title)

@dp.message(AdminStates.waiting_for_title)
async def admin_add_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("Введите цену товара в рублях (например: `60`):")
    await state.set_state(AdminStates.waiting_for_price)

@dp.message(AdminStates.waiting_for_price)
async def admin_add_price(message: types.Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        await state.update_data(price=price)
        await message.answer("Введите количество товара на складе (например: `10`):")
        await state.set_state(AdminStates.waiting_for_stock)
    except ValueError:
        await message.answer("❌ Введите корректное число для цены.")

@dp.message(AdminStates.waiting_for_stock)
async def admin_add_stock(message: types.Message, state: FSMContext):
    try:
        stock = int(message.text.strip())
        await state.update_data(stock=stock)
        await message.answer("Отправьте ссылку на картинку товара (или напишите `нет`):")
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
    if ADMIN_IDS and message.from_user.id not in ADMIN_IDS:
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
        if "blacklist" not in db:
            db["blacklist"] = []
        if target_user_id not in db["blacklist"]:
            db["blacklist"].append(target_user_id)
        save_data(db)
        try:
            await bot.send_message(target_user_id, "❌ Вы заблокированы за неявку за заказом. Обратитесь в `/teh`.")
        except:
            pass

    await callback.message.edit_text(callback.message.text + "\n\n❌ **ПОЛЬЗОВАТЕЛЬ ОТПРАВЛЕН В ЧС**")

# --- ТЕХПОДДЕРЖКА (/teh) ---
@dp.message(Command("teh"))
async def cmd_support(message: types.Message, state: FSMContext):
    await message.answer("📝 Напишите ваше обращение/жалобу одним сообщением:")
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
    if "support_tickets" not in db:
        db["support_tickets"] = []
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

# --- RUNNER ---
@app.on_event("startup")
async def on_startup():
    asyncio.create_task(dp.start_polling(bot))
