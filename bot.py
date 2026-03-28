import os
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict
import hashlib
import hmac

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, CallbackQuery, Message,
    InputMediaPhoto, LabeledPrice, PreCheckoutQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import asyncpg
from asyncpg import Pool
import aiohttp

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7973988177"))
CRYPTO_BOT_TOKEN = "487637:AAoEj9pbhDufWvzMcEuRsaTi0pCsaYpfwH2"  # Токен Crypto Bot

# PostgreSQL подключение
DATABASE_URL = "postgresql://bothost_db_f42fe8891149:CZkWUKfQqRoNmu63JY65Rg8ewcGG4CKESZViHW6Pm6E@node1.pghost.ru:32852/bothost_db_f42fe8891149"

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ (POSTGRESQL) ==========
class Database:
    def __init__(self, pool: Pool):
        self.pool = pool

    async def init_db(self):
        """Создание таблиц если их нет"""
        async with self.pool.acquire() as conn:
            # Таблица приветствия
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS welcome (
                    id SERIAL PRIMARY KEY,
                    text TEXT,
                    photo_file_id TEXT
                )
            ''')
            
            # Таблица кнопок
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS buttons (
                    id SERIAL PRIMARY KEY,
                    text TEXT,
                    url TEXT,
                    is_permanent BOOLEAN DEFAULT FALSE
                )
            ''')
            
            # Таблица статистики (пользователи)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_blocked BOOLEAN DEFAULT FALSE
                )
            ''')
            
            # Таблица статистики (нажатия на кнопки)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS button_clicks (
                    id SERIAL PRIMARY KEY,
                    button_id INTEGER REFERENCES buttons(id) ON DELETE CASCADE,
                    user_id BIGINT,
                    clicked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица анонимных сообщений
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS anonymous_messages (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    message_id INTEGER,
                    admin_reply_to INTEGER,
                    content_type TEXT,
                    content_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_answered BOOLEAN DEFAULT FALSE
                )
            ''')
            
            # Таблица для бан-листа
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id BIGINT PRIMARY KEY,
                    reason TEXT,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    banned_by BIGINT
                )
            ''')
            
            # Таблица для администраторов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY,
                    added_by BIGINT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица для платежей
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    invoice_id TEXT,
                    asset TEXT,
                    amount DECIMAL,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
            ''')
            
            # Добавляем главного админа
            await conn.execute('''
                INSERT INTO admins (user_id, added_by)
                VALUES ($1, $1)
                ON CONFLICT (user_id) DO NOTHING
            ''', ADMIN_ID)
            
            # Добавляем приветствие по умолчанию, если пусто
            result = await conn.fetchval("SELECT COUNT(*) FROM welcome")
            if result == 0:
                await conn.execute(
                    "INSERT INTO welcome (text, photo_file_id) VALUES ($1, $2)",
                    "Добро пожаловать! 👋\n\nНажмите на кнопки ниже:", None
                )
            
            # Добавляем постоянную кнопку анонимных сообщений, если её нет
            result = await conn.fetchval("SELECT COUNT(*) FROM buttons WHERE text = '📝 Анонимные сообщения'")
            if result == 0:
                await conn.execute(
                    "INSERT INTO buttons (text, url, is_permanent) VALUES ($1, $2, $3)",
                    "📝 Анонимные сообщения", "callback://anonymous", True
                )
            
            # Добавляем кнопку "На пропитание"
            result = await conn.fetchval("SELECT COUNT(*) FROM buttons WHERE text = '💰 На пропитание'")
            if result == 0:
                await conn.execute(
                    "INSERT INTO buttons (text, url, is_permanent) VALUES ($1, $2, $3)",
                    "💰 На пропитание", "callback://donation", False
                )
            
            # Добавляем пример кнопки, если пусто
            result = await conn.fetchval("SELECT COUNT(*) FROM buttons WHERE is_permanent = FALSE AND text != '💰 На пропитание'")
            if result == 0:
                await conn.execute(
                    "INSERT INTO buttons (text, url, is_permanent) VALUES ($1, $2, $3)",
                    "Пример ссылки", "https://example.com", False
                )

    # Методы для работы с приветствием
    async def get_welcome(self):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT text, photo_file_id FROM welcome LIMIT 1")
            return row["text"], row["photo_file_id"] if row else (None, None)

    async def update_welcome(self, text, photo_file_id=None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE welcome SET text = $1, photo_file_id = $2",
                text, photo_file_id
            )

    # Методы для работы с кнопками
    async def get_buttons(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, text, url, is_permanent FROM buttons ORDER BY id")
            return [(row["id"], row["text"], row["url"], row["is_permanent"]) for row in rows]

    async def add_button(self, text, url, is_permanent=False):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO buttons (text, url, is_permanent) VALUES ($1, $2, $3) RETURNING id",
                text, url, is_permanent
            )
            return row["id"]

    async def delete_button(self, button_id):
        async with self.pool.acquire() as conn:
            is_permanent = await conn.fetchval("SELECT is_permanent FROM buttons WHERE id = $1", button_id)
            if is_permanent:
                return False
            await conn.execute("DELETE FROM buttons WHERE id = $1", button_id)
            return True

    # Методы для статистики
    async def add_user(self, user_id, username, first_name, last_name):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, created_at, last_activity)
                VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                last_activity = CURRENT_TIMESTAMP
            ''', user_id, username, first_name, last_name)

    async def update_activity(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = $1",
                user_id
            )

    async def add_click(self, button_id, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO button_clicks (button_id, user_id) VALUES ($1, $2)",
                button_id, user_id
            )

    async def get_stats(self):
        async with self.pool.acquire() as conn:
            # Всего пользователей
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = FALSE")
            
            # Заблокированных пользователей
            blocked_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = TRUE")
            
            # Пользователей за последние 24 часа
            yesterday = datetime.now() - timedelta(days=1)
            new_users_24h = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at > $1 AND is_blocked = FALSE", yesterday
            )
            
            # Активных за последние 7 дней
            week_ago = datetime.now() - timedelta(days=7)
            active_users = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE last_activity > $1 AND is_blocked = FALSE", week_ago
            )
            
            # Всего нажатий
            total_clicks = await conn.fetchval("SELECT COUNT(*) FROM button_clicks")
            
            # Всего анонимных сообщений
            total_anonymous = await conn.fetchval("SELECT COUNT(*) FROM anonymous_messages")
            
            # Неотвеченных анонимных сообщений
            unanswered_anonymous = await conn.fetchval("SELECT COUNT(*) FROM anonymous_messages WHERE is_answered = FALSE")
            
            # Статистика по платежам
            total_payments = await conn.fetchval("SELECT COUNT(*) FROM payments WHERE status = 'paid'")
            total_amount = await conn.fetchval("SELECT SUM(amount) FROM payments WHERE status = 'paid'")
            
            # Статистика по каждой кнопке
            buttons = await self.get_buttons()
            button_stats = []
            for btn_id, text, url, is_permanent in buttons:
                clicks = await conn.fetchval(
                    "SELECT COUNT(*) FROM button_clicks WHERE button_id = $1", btn_id
                )
                button_stats.append({"id": btn_id, "text": text, "clicks": clicks, "is_permanent": is_permanent})
            
            return {
                "total_users": total_users,
                "blocked_users": blocked_users,
                "new_users_24h": new_users_24h,
                "active_users_7d": active_users,
                "total_clicks": total_clicks,
                "total_anonymous": total_anonymous,
                "unanswered_anonymous": unanswered_anonymous,
                "total_payments": total_payments,
                "total_amount": float(total_amount) if total_amount else 0,
                "button_stats": button_stats
            }

    async def get_all_users(self, include_blocked=False):
        async with self.pool.acquire() as conn:
            if include_blocked:
                rows = await conn.fetch("SELECT user_id FROM users")
            else:
                rows = await conn.fetch("SELECT user_id FROM users WHERE is_blocked = FALSE")
            return [row["user_id"] for row in rows]

    async def get_users_list(self, limit=50, offset=0):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT user_id, username, first_name, last_name, created_at, last_activity, is_blocked
                FROM users
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
            ''', limit, offset)
            return rows

    async def get_total_users_count(self):
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM users")

    # Методы для банов
    async def ban_user(self, user_id, reason, banned_by):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET is_blocked = TRUE WHERE user_id = $1
            ''', user_id)
            await conn.execute('''
                INSERT INTO banned_users (user_id, reason, banned_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET
                reason = EXCLUDED.reason,
                banned_at = CURRENT_TIMESTAMP,
                banned_by = EXCLUDED.banned_by
            ''', user_id, reason, banned_by)

    async def unban_user(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET is_blocked = FALSE WHERE user_id = $1
            ''', user_id)
            await conn.execute('''
                DELETE FROM banned_users WHERE user_id = $1
            ''', user_id)

    async def is_user_banned(self, user_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT is_blocked FROM users WHERE user_id = $1", user_id
            ) or False

    async def get_banned_users(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT u.user_id, u.username, u.first_name, u.last_name, b.reason, b.banned_at
                FROM banned_users b
                JOIN users u ON u.user_id = b.user_id
                ORDER BY b.banned_at DESC
            ''')
            return rows

    # Методы для администраторов
    async def add_admin(self, user_id, added_by):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO admins (user_id, added_by)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id, added_by)

    async def remove_admin(self, user_id):
        if user_id == ADMIN_ID:
            return False
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM admins WHERE user_id = $1", user_id)
            return True

    async def is_admin(self, user_id):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM admins WHERE user_id = $1", user_id
            ) > 0

    async def get_admins(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT a.user_id, u.username, u.first_name, u.last_name, a.added_at
                FROM admins a
                LEFT JOIN users u ON u.user_id = a.user_id
                ORDER BY a.added_at
            ''')
            return rows

    # Методы для анонимных сообщений
    async def save_anonymous_message(self, user_id, message_id, content_type, content_data, reply_to=None):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                INSERT INTO anonymous_messages (user_id, message_id, admin_reply_to, content_type, content_data)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            ''', user_id, message_id, reply_to, content_type, content_data)
            return row["id"]

    async def get_unanswered_messages(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT id, user_id, content_type, content_data, created_at
                FROM anonymous_messages
                WHERE is_answered = FALSE AND admin_reply_to IS NULL
                ORDER BY created_at DESC
            ''')
            return rows

    async def get_message_by_id(self, msg_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM anonymous_messages WHERE id = $1
            ''', msg_id)
            return row

    async def mark_as_answered(self, msg_id):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE anonymous_messages SET is_answered = TRUE WHERE id = $1
            ''', msg_id)

    async def save_admin_reply(self, original_msg_id, user_id, reply_message_id, content_type, content_data):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                INSERT INTO anonymous_messages (user_id, message_id, admin_reply_to, content_type, content_data)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            ''', user_id, reply_message_id, original_msg_id, content_type, content_data)
            return row["id"]

    # Методы для платежей
    async def save_payment(self, user_id, invoice_id, asset, amount, status="pending"):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO payments (user_id, invoice_id, asset, amount, status)
                VALUES ($1, $2, $3, $4, $5)
            ''', user_id, invoice_id, asset, amount, status)

    async def update_payment_status(self, invoice_id, status):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE payments 
                SET status = $1, completed_at = CURRENT_TIMESTAMP
                WHERE invoice_id = $2
            ''', status, invoice_id)

    async def get_payment_by_invoice(self, invoice_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM payments WHERE invoice_id = $1
            ''', invoice_id)
            return row


# ========== СОСТОЯНИЯ ДЛЯ FSM ==========
class AdminStates(StatesGroup):
    waiting_for_welcome_text = State()
    waiting_for_welcome_photo = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()
    waiting_for_broadcast = State()
    waiting_for_anonymous_reply = State()
    waiting_for_ban_reason = State()
    waiting_for_user_id = State()
    waiting_for_add_admin = State()


class AnonymousStates(StatesGroup):
    waiting_for_message = State()


class DonationStates(StatesGroup):
    waiting_for_asset = State()
    waiting_for_amount = State()


# ========== КЛАВИАТУРЫ ==========
async def get_user_keyboard():
    """Клавиатура для пользователя"""
    buttons = await db.get_buttons()
    
    keyboard = InlineKeyboardBuilder()
    for btn_id, text, url, is_permanent in buttons:
        if url == "callback://anonymous":
            keyboard.add(InlineKeyboardButton(text=text, callback_data=f"anonymous"))
        elif url == "callback://donation":
            keyboard.add(InlineKeyboardButton(text=text, callback_data=f"donation"))
        else:
            keyboard.add(InlineKeyboardButton(text=text, url=url, callback_data=f"click_{btn_id}"))
    
    return keyboard.adjust(1).as_markup()

def get_admin_keyboard():
    """Клавиатура админ-панели"""
    buttons = [
        [KeyboardButton(text="✏️ Изменить приветствие")],
        [KeyboardButton(text="➕ Добавить кнопку"), KeyboardButton(text="❌ Удалить кнопку")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👥 Управление пользователями")],
        [KeyboardButton(text="💬 Анонимные сообщения"), KeyboardButton(text="👮 Управление админами")],
        [KeyboardButton(text="📢 Рассылка"), KeyboardButton(text="💰 Платежи")],
        [KeyboardButton(text="🔙 В главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_user_management_keyboard():
    """Клавиатура управления пользователями"""
    buttons = [
        [KeyboardButton(text="📋 Список пользователей")],
        [KeyboardButton(text="🔨 Забанить пользователя"), KeyboardButton(text="🔓 Разбанить пользователя")],
        [KeyboardButton(text="🚫 Список забаненных")],
        [KeyboardButton(text="🔙 Назад в админку")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_admin_management_keyboard():
    """Клавиатура управления админами"""
    buttons = [
        [KeyboardButton(text="📋 Список админов")],
        [KeyboardButton(text="➕ Добавить админа"), KeyboardButton(text="❌ Удалить админа")],
        [KeyboardButton(text="🔙 Назад в админку")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cancel_keyboard():
    """Клавиатура отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

def get_donation_asset_keyboard():
    """Клавиатура выбора валюты для доната"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐️ TG STARS", callback_data="donate_stars")],
        [InlineKeyboardButton(text="💎 USDT (TRC20)", callback_data="donate_usdt")],
        [InlineKeyboardButton(text="💎 TON", callback_data="donate_ton")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="donate_cancel")]
    ])
    return keyboard


# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db_pool = None
db = None


# ========== CRYPTO BOT API ФУНКЦИИ ==========
class CryptoBotAPI:
    def __init__(self, token):
        self.token = token
        self.base_url = "https://pay.crypt.bot/api"
    
    async def create_invoice(self, asset, amount, description=None):
        """Создание инвойса в Crypto Bot"""
        url = f"{self.base_url}/createInvoice"
        headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json"
        }
        data = {
            "asset": asset,
            "amount": str(amount),
            "description": description or "Donation"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("result")
                else:
                    logger.error(f"Crypto Bot API error: {await response.text()}")
                    return None
    
    async def get_invoice_status(self, invoice_id):
        """Получение статуса инвойса"""
        url = f"{self.base_url}/getInvoices"
        headers = {
            "Crypto-Pay-API-Token": self.token
        }
        params = {
            "invoice_ids": invoice_id
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("result") and result["result"].get("items"):
                        return result["result"]["items"][0]
        return None


crypto_bot = CryptoBotAPI(CRYPTO_BOT_TOKEN)


# ========== ПОЛЬЗОВАТЕЛЬСКИЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def start_command(message: Message):
    user = message.from_user
    
    if await db.is_user_banned(user.id):
        await message.answer("⛔ Вы забанены и не можете использовать бота!")
        return
    
    await db.add_user(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text, photo_id = await db.get_welcome()
    
    if photo_id:
        await message.answer_photo(
            photo=photo_id,
            caption=welcome_text,
            reply_markup=await get_user_keyboard()
        )
    else:
        await message.answer(
            welcome_text,
            reply_markup=await get_user_keyboard()
        )

@dp.callback_query(F.data.startswith("click_"))
async def button_click(callback: CallbackQuery):
    if await db.is_user_banned(callback.from_user.id):
        await callback.answer("⛔ Вы забанены!", show_alert=True)
        return
    
    button_id = int(callback.data.split("_")[1])
    await db.add_click(button_id, callback.from_user.id)
    await db.update_activity(callback.from_user.id)
    
    # Получаем URL кнопки для перехода
    async with db_pool.acquire() as conn:
        result = await conn.fetchval("SELECT url FROM buttons WHERE id = $1", button_id)
    
    if result and result not in ["callback://anonymous", "callback://donation"]:
        url = result
        await callback.answer("Переход по ссылке...")
        await callback.message.answer(f"🔗 Перейдите по ссылке:\n{url}")
    else:
        await callback.answer("Кнопка не найдена")

@dp.callback_query(F.data == "anonymous")
async def anonymous_button(callback: CallbackQuery, state: FSMContext):
    if await db.is_user_banned(callback.from_user.id):
        await callback.answer("⛔ Вы забанены!", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.answer(
        "📝 *Анонимное сообщение админу*\n\n"
        "Вы можете отправить текстовое сообщение или фото.\n"
        "Админ сможет ответить вам анонимно.\n\n"
        "Напишите ваше сообщение:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AnonymousStates.waiting_for_message)

# ========== ДОНАТ (НА ПРОПИТАНИЕ) ==========
@dp.callback_query(F.data == "donation")
async def donation_start(callback: CallbackQuery, state: FSMContext):
    if await db.is_user_banned(callback.from_user.id):
        await callback.answer("⛔ Вы забанены!", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.answer(
        "💰 *Поддержать проект*\n\n"
        "Выберите способ оплаты:",
        parse_mode="Markdown",
        reply_markup=get_donation_asset_keyboard()
    )
    await state.set_state(DonationStates.waiting_for_asset)

@dp.callback_query(F.data.startswith("donate_"))
async def donation_asset_selected(callback: CallbackQuery, state: FSMContext):
    asset = callback.data.split("_")[1]
    
    if asset == "cancel":
        await state.clear()
        await callback.message.delete()
        await callback.answer("❌ Оплата отменена")
        return
    
    # Сохраняем выбранную валюту
    await state.update_data(asset=asset)
    
    # Определяем минимальную сумму
    min_amount = {
        "stars": 1,
        "usdt": 0.01,
        "ton": 0.01
    }.get(asset, 0.01)
    
    asset_names = {
        "stars": "⭐️ TG STARS",
        "usdt": "💎 USDT",
        "ton": "💎 TON"
    }
    
    await callback.message.answer(
        f"💰 *Создание счета*\n\n"
        f"Валюта: {asset_names[asset]}\n"
        f"Минимальная сумма: {min_amount}\n\n"
        f"Введите сумму (число):",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(DonationStates.waiting_for_amount)
    await callback.answer()

@dp.message(DonationStates.waiting_for_amount, F.text == "❌ Отмена")
async def cancel_donation(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Оплата отменена", reply_markup=await get_user_keyboard())

@dp.message(DonationStates.waiting_for_amount)
async def donation_amount_input(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительной!")
            return
        
        data = await state.get_data()
        asset = data["asset"]
        
        # Проверяем минимальную сумму
        min_amount = {
            "stars": 1,
            "usdt": 0.01,
            "ton": 0.01
        }.get(asset, 0.01)
        
        if amount < min_amount:
            await message.answer(f"❌ Минимальная сумма: {min_amount}")
            return
        
        # Создаем инвойс через Crypto Bot
        if asset == "stars":
            # TG STARS создаем через Telegram Stars API
            await create_stars_invoice(message, amount, state)
        else:
            # USDT и TON через Crypto Bot
            await create_crypto_invoice(message, asset, amount, state)
            
    except ValueError:
        await message.answer("❌ Введите корректное число!")

async def create_stars_invoice(message: Message, amount: int, state: FSMContext):
    """Создание счета через Telegram Stars"""
    from aiogram.types import LabeledPrice, PreCheckoutQuery
    
    # Сохраняем информацию о платеже
    invoice_id = f"stars_{message.from_user.id}_{datetime.now().timestamp()}"
    await db.save_payment(message.from_user.id, invoice_id, "STARS", amount)
    
    # Создаем инвойс в Telegram Stars
    prices = [LabeledPrice(label="Поддержка проекта", amount=int(amount * 100))]  # Stars в копейках
    
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="💰 Поддержка проекта",
        description=f"Сумма: {amount} ⭐️",
        payload=invoice_id,
        provider_token="",  # Для Stars не нужен
        currency="XTR",  # XTR = Telegram Stars
        prices=prices,
        start_parameter="donation",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_payment")]
        ])
    )
    
    await state.clear()

async def create_crypto_invoice(message: Message, asset: str, amount: float, state: FSMContext):
    """Создание счета через Crypto Bot"""
    asset_map = {
        "usdt": "USDT",
        "ton": "TON"
    }
    
    asset_code = asset_map.get(asset, asset.upper())
    
    # Создаем инвойс
    invoice = await crypto_bot.create_invoice(
        asset=asset_code,
        amount=amount,
        description=f"Donation from user {message.from_user.id}"
    )
    
    if invoice and invoice.get("pay_url"):
        invoice_id = invoice["invoice_id"]
        
        # Сохраняем в базу
        await db.save_payment(message.from_user.id, invoice_id, asset_code, amount)
        
        # Отправляем ссылку на оплату
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=invoice["pay_url"])],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_payment_{invoice_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_payment")]
        ])
        
        await message.answer(
            f"💰 *Счет создан!*\n\n"
            f"Валюта: {asset_code}\n"
            f"Сумма: {amount}\n\n"
            f"Нажмите кнопку ниже для оплаты.\n"
            f"После оплаты нажмите 'Проверить оплату'.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        
        # Запускаем проверку оплаты через 30 секунд
        asyncio.create_task(check_payment_delayed(invoice_id, message.chat.id, message.message_id))
        
        await state.clear()
    else:
        await message.answer(
            "❌ Ошибка при создании счета. Попробуйте позже.",
            reply_markup=await get_user_keyboard()
        )
        await state.clear()

@dp.callback_query(F.data.startswith("check_payment_"))
async def check_payment(callback: CallbackQuery):
    invoice_id = callback.data.split("_")[2]
    
    # Получаем платеж из базы
    payment = await db.get_payment_by_invoice(invoice_id)
    if not payment:
        await callback.answer("❌ Платеж не найден")
        return
    
    # Проверяем статус в Crypto Bot
    invoice_status = await crypto_bot.get_invoice_status(invoice_id)
    
    if invoice_status and invoice_status.get("status") == "paid":
        # Обновляем статус в базе
        await db.update_payment_status(invoice_id, "paid")
        
        # Отправляем подтверждение
        await callback.message.edit_text(
            f"✅ *Оплата получена!*\n\n"
            f"Спасибо за поддержку! 🙏\n"
            f"Сумма: {payment['amount']} {payment['asset']}",
            parse_mode="Markdown"
        )
        
        # Уведомляем админов
        await notify_admins_about_payment(payment['user_id'], payment['amount'], payment['asset'])
    else:
        await callback.answer("❌ Оплата не найдена. Попробуйте позже.", show_alert=True)

@dp.callback_query(F.data == "cancel_payment")
async def cancel_payment(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("❌ Оплата отменена", reply_markup=await get_user_keyboard())
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery):
    """Обработка предварительной проверки для Telegram Stars"""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    """Обработка успешной оплаты Telegram Stars"""
    payment = message.successful_payment
    
    # Сохраняем информацию о платеже
    await db.update_payment_status(payment.invoice_payload, "paid")
    
    await message.answer(
        f"✅ *Оплата получена!*\n\n"
        f"Спасибо за поддержку! 🙏\n"
        f"Сумма: {payment.total_amount // 100} ⭐️",
        parse_mode="Markdown",
        reply_markup=await get_user_keyboard()
    )
    
    # Уведомляем админов
    await notify_admins_about_payment(message.from_user.id, payment.total_amount // 100, "STARS")

async def check_payment_delayed(invoice_id: str, chat_id: int, message_id: int):
    """Отложенная проверка оплаты"""
    await asyncio.sleep(30)  # Ждем 30 секунд
    
    payment = await db.get_payment_by_invoice(invoice_id)
    if payment and payment["status"] == "pending":
        try:
            await bot.send_message(
                chat_id,
                "ℹ️ *Напоминание:* Если вы оплатили, нажмите кнопку 'Проверить оплату'.",
                parse_mode="Markdown",
                reply_to_message_id=message_id
            )
        except:
            pass

async def notify_admins_about_payment(user_id: int, amount: float, asset: str):
    """Уведомление админов о новом донате"""
    admins = await db.get_admins()
    
    # Получаем информацию о пользователе
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT username, first_name FROM users WHERE user_id = $1", user_id)
    
    user_name = user["first_name"] if user else str(user_id)
    username = f"@{user['username']}" if user and user["username"] else f"ID: {user_id}"
    
    for admin in admins:
        try:
            await bot.send_message(
                admin["user_id"],
                f"🎉 *Новый донат!*\n\n"
                f"Пользователь: {user_name}\n"
                f"Юзернейм: {username}\n"
                f"Сумма: {amount} {asset}\n\n"
                f"Спасибо за поддержку проекта! 🙏",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления админа {admin['user_id']}: {e}")


# ========== ОСТАЛЬНЫЕ АДМИН-ФУНКЦИИ (сохраняем из предыдущего кода) ==========
# ... (весь остальной код из предыдущей версии с админ-панелью остается здесь)

# ========== ЗАПУСК БОТА ==========
async def on_startup():
    global db_pool, db
    logger.info("Подключение к PostgreSQL...")
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    db = Database(db_pool)
    await db.init_db()
    logger.info("База данных готова")
    logger.info("Бот запущен!")

async def on_shutdown():
    if db_pool:
        await db_pool.close()
        logger.info("Подключение к PostgreSQL закрыто")

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
