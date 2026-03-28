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
        """Создание таблиц если их нет и миграция существующих"""
        async with self.pool.acquire() as conn:
            # Таблица приветствия
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS welcome (
                    id SERIAL PRIMARY KEY,
                    text TEXT,
                    photo_file_id TEXT
                )
            ''')
            
            # Проверяем и добавляем колонку is_permanent в таблицу buttons
            try:
                await conn.execute('''
                    ALTER TABLE buttons ADD COLUMN IF NOT EXISTS is_permanent BOOLEAN DEFAULT FALSE
                ''')
                logger.info("Колонка is_permanent добавлена в таблицу buttons")
            except Exception as e:
                logger.warning(f"Колонка is_permanent уже существует или ошибка: {e}")
            
            # Создаем таблицу buttons если её нет
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS buttons (
                    id SERIAL PRIMARY KEY,
                    text TEXT,
                    url TEXT
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
            
            # Добавляем колонки в users если их нет
            try:
                await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
                await conn.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN DEFAULT FALSE')
            except Exception as e:
                logger.warning(f"Колонки в users уже существуют: {e}")
            
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
            
            # Проверяем и добавляем постоянные кнопки
            # Добавляем кнопку анонимных сообщений
            result = await conn.fetchval("SELECT COUNT(*) FROM buttons WHERE text = '📝 Анонимные сообщения'")
            if result == 0:
                await conn.execute(
                    "INSERT INTO buttons (text, url, is_permanent) VALUES ($1, $2, $3)",
                    "📝 Анонимные сообщения", "callback://anonymous", True
                )
            else:
                # Обновляем существующую кнопку
                await conn.execute(
                    "UPDATE buttons SET is_permanent = TRUE WHERE text = '📝 Анонимные сообщения'"
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
            if result == 0 and await conn.fetchval("SELECT COUNT(*) FROM buttons") <= 2:
                await conn.execute(
                    "INSERT INTO buttons (text, url, is_permanent) VALUES ($1, $2, $3)",
                    "Пример ссылки", "https://example.com", False
                )
            
            logger.info("База данных успешно инициализирована")

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
            try:
                rows = await conn.fetch("SELECT id, text, url, is_permanent FROM buttons ORDER BY id")
            except:
                # Если колонки нет, получаем без is_permanent
                rows = await conn.fetch("SELECT id, text, url FROM buttons ORDER BY id")
                return [(row["id"], row["text"], row["url"], False) for row in rows]
            return [(row["id"], row["text"], row["url"], row["is_permanent"]) for row in rows]

    async def add_button(self, text, url, is_permanent=False):
        async with self.pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO buttons (text, url, is_permanent) VALUES ($1, $2, $3) RETURNING id",
                    text, url, is_permanent
                )
            except:
                row = await conn.fetchrow(
                    "INSERT INTO buttons (text, url) VALUES ($1, $2) RETURNING id",
                    text, url
                )
            return row["id"]

    async def delete_button(self, button_id):
        async with self.pool.acquire() as conn:
            try:
                is_permanent = await conn.fetchval("SELECT is_permanent FROM buttons WHERE id = $1", button_id)
                if is_permanent:
                    return False
            except:
                pass
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

@dp.message(AnonymousStates.waiting_for_message, F.text == "❌ Отмена")
async def cancel_anonymous(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отправка сообщения отменена", reply_markup=await get_user_keyboard())

@dp.message(AnonymousStates.waiting_for_message)
async def process_anonymous_text(message: Message, state: FSMContext):
    if await db.is_user_banned(message.from_user.id):
        await message.answer("⛔ Вы забанены!")
        await state.clear()
        return
    
    user_id = message.from_user.id
    content_type = "text"
    content_data = message.text
    
    # Сохраняем сообщение
    msg_id = await db.save_anonymous_message(user_id, message.message_id, content_type, content_data)
    
    # Отправляем админам
    await notify_admins_about_message(msg_id, user_id, content_type, content_data)
    
    await state.clear()
    await message.answer(
        "✅ Ваше сообщение отправлено админам!\n"
        "Когда вам ответят, вы получите уведомление.",
        reply_markup=await get_user_keyboard()
    )

@dp.message(AnonymousStates.waiting_for_message, F.photo)
async def process_anonymous_photo(message: Message, state: FSMContext):
    if await db.is_user_banned(message.from_user.id):
        await message.answer("⛔ Вы забанены!")
        await state.clear()
        return
    
    user_id = message.from_user.id
    content_type = "photo"
    photo_file_id = message.photo[-1].file_id
    caption = message.caption or ""
    content_data = json.dumps({"file_id": photo_file_id, "caption": caption})
    
    # Сохраняем сообщение
    msg_id = await db.save_anonymous_message(user_id, message.message_id, content_type, content_data)
    
    # Отправляем админам
    await notify_admins_about_message(msg_id, user_id, content_type, content_data)
    
    await state.clear()
    await message.answer(
        "✅ Ваше сообщение отправлено админам!\n"
        "Когда вам ответят, вы получите уведомление.",
        reply_markup=await get_user_keyboard()
    )


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

async def notify_admins_about_message(msg_id, user_id, content_type, content_data):
    """Отправляет уведомление всем админам о новом сообщении"""
    admins = await db.get_admins()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"reply_anon_{msg_id}")]
    ])
    
    for admin in admins:
        admin_id = admin["user_id"]
        try:
            if content_type == "text":
                await bot.send_message(
                    admin_id,
                    f"📬 *Новое анонимное сообщение!*\n\n"
                    f"Сообщение: {content_data}\n\n"
                    f"ID сообщения: {msg_id}",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            elif content_type == "photo":
                data = json.loads(content_data)
                await bot.send_photo(
                    admin_id,
                    data["file_id"],
                    caption=f"📬 *Новое анонимное сообщение!*\n\n"
                            f"Подпись: {data['caption'] or 'Нет подписи'}\n\n"
                            f"ID сообщения: {msg_id}",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")


# ========== АДМИН-ПАНЕЛЬ ==========
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not await db.is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return
    
    await message.answer(
        "👋 Добро пожаловать в админ-панель!\n\n"
        "Выберите действие:",
        reply_markup=get_admin_keyboard()
    )

@dp.message(F.text == "🔙 Назад в админку")
@dp.message(F.text == "🔙 В главное меню")
async def back_to_main(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
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

# ========== УПРАВЛЕНИЕ ПРИВЕТСТВИЕМ ==========
@dp.message(F.text == "✏️ Изменить приветствие")
async def change_welcome(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        return
    
    await message.answer(
        "✏️ Введите новый текст приветствия:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_welcome_text)

@dp.message(AdminStates.waiting_for_welcome_text, F.text == "❌ Отмена")
async def cancel_welcome_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_welcome_text)
async def save_welcome_text(message: Message, state: FSMContext):
    await state.update_data(welcome_text=message.text)
    await message.answer(
        "📸 Теперь отправьте фото (или отправьте /skip, чтобы оставить без фото):",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_welcome_photo)

@dp.message(AdminStates.waiting_for_welcome_photo, F.text == "❌ Отмена")
async def cancel_welcome_photo(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_welcome_photo, Command("skip"))
async def skip_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.update_welcome(data["welcome_text"], None)
    await state.clear()
    await message.answer("✅ Приветствие обновлено без фото!", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_welcome_photo, F.photo)
async def save_welcome_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photo_file_id = message.photo[-1].file_id
    await db.update_welcome(data["welcome_text"], photo_file_id)
    await state.clear()
    await message.answer("✅ Приветствие с фото обновлено!", reply_markup=get_admin_keyboard())

# ========== УПРАВЛЕНИЕ КНОПКАМИ ==========
@dp.message(F.text == "➕ Добавить кнопку")
async def add_button_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        return
    
    await message.answer(
        "➕ Введите текст для новой кнопки:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_button_text)

@dp.message(AdminStates.waiting_for_button_text, F.text == "❌ Отмена")
async def cancel_button_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_button_text)
async def save_button_text(message: Message, state: FSMContext):
    await state.update_data(button_text=message.text)
    await message.answer(
        "🔗 Введите URL для кнопки (например, https://example.com):",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_button_url)

@dp.message(AdminStates.waiting_for_button_url, F.text == "❌ Отмена")
async def cancel_button_url(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_button_url)
async def save_button_url(message: Message, state: FSMContext):
    data = await state.get_data()
    button_text = data["button_text"]
    button_url = message.text
    
    await db.add_button(button_text, button_url, False)
    await state.clear()
    await message.answer(f"✅ Кнопка \"{button_text}\" добавлена!", reply_markup=get_admin_keyboard())

@dp.message(F.text == "❌ Удалить кнопку")
async def delete_button_menu(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    buttons = await db.get_buttons()
    deletable_buttons = [b for b in buttons if not b[3] and b[1] not in ["📝 Анонимные сообщения"]]
    
    if not deletable_buttons:
        await message.answer("📭 Нет кнопок для удаления (постоянные кнопки удалить нельзя)", reply_markup=get_admin_keyboard())
        return
    
    keyboard = InlineKeyboardBuilder()
    for btn_id, text, url, is_permanent in deletable_buttons:
        keyboard.add(InlineKeyboardButton(text=f"❌ {text}", callback_data=f"del_{btn_id}"))
    
    keyboard.add(InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_del"))
    
    await message.answer(
        "Выберите кнопку для удаления:\n\n"
        "⚠️ Постоянные кнопки (Анонимные сообщения) удалить нельзя",
        reply_markup=keyboard.adjust(1).as_markup()
    )

@dp.callback_query(F.data.startswith("del_"))
async def confirm_delete_button(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    button_id = int(callback.data.split("_")[1])
    success = await db.delete_button(button_id)
    
    if success:
        await callback.message.edit_text("✅ Кнопка удалена!")
    else:
        await callback.message.edit_text("❌ Нельзя удалить постоянную кнопку!")
    
    await callback.answer()

@dp.callback_query(F.data == "cancel_del")
async def cancel_delete(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    await callback.message.delete()
    await callback.message.answer("❌ Удаление отменено", reply_markup=get_admin_keyboard())
    await callback.answer()

# ========== СТАТИСТИКА ==========
@dp.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    stats = await db.get_stats()
    
    stats_text = (
        "📊 *Статистика бота*\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🚫 Заблокировано: {stats['blocked_users']}\n"
        f"🆕 Новых за 24ч: {stats['new_users_24h']}\n"
        f"📈 Активных за 7д: {stats['active_users_7d']}\n"
        f"🔘 Всего нажатий: {stats['total_clicks']}\n"
        f"💬 Анонимных сообщений: {stats['total_anonymous']}\n"
        f"⏳ Неотвеченных: {stats['unanswered_anonymous']}\n"
        f"💰 Всего платежей: {stats['total_payments']}\n"
        f"💵 Собрано: {stats['total_amount']:.2f} $\n\n"
        "*Статистика по кнопкам:*\n"
    )
    
    for btn in stats['button_stats']:
        permanent_mark = " 🔒" if btn['is_permanent'] else ""
        stats_text += f"• {btn['text']}{permanent_mark}: {btn['clicks']} нажатий\n"
    
    await message.answer(stats_text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

# ========== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ==========
@dp.message(F.text == "👥 Управление пользователями")
async def user_management(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    await message.answer(
        "👥 *Управление пользователями*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_user_management_keyboard()
    )

@dp.message(F.text == "📋 Список пользователей")
async def list_users(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    users = await db.get_users_list(limit=20)
    
    if not users:
        await message.answer("📭 Нет пользователей")
        return
    
    text = "📋 *Последние пользователи:*\n\n"
    for user in users:
        status = "🚫" if user["is_blocked"] else "✅"
        text += f"{status} ID: `{user['user_id']}`\n"
        text += f"   👤 {user['first_name'] or 'Нет имени'} {user['last_name'] or ''}\n"
        text += f"   🆔 @{user['username'] or 'нет юзернейма'}\n"
        text += f"   📅 {user['created_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
    
    total = await db.get_total_users_count()
    text += f"\n*Всего пользователей: {total}*"
    
    await message.answer(text, parse_mode="Markdown", reply_markup=get_user_management_keyboard())

@dp.message(F.text == "🔨 Забанить пользователя")
async def ban_user_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        return
    
    await message.answer(
        "🔨 Введите ID пользователя для бана:\n\n"
        "Можно отправить /cancel для отмены",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_user_id)

@dp.message(AdminStates.waiting_for_user_id, F.text == "❌ Отмена")
async def cancel_ban(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено", reply_markup=get_user_management_keyboard())

@dp.message(AdminStates.waiting_for_user_id)
async def get_ban_user_id(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await state.update_data(user_id=user_id)
        
        await message.answer(
            "📝 Введите причину бана:",
            reply_markup=get_cancel_keyboard()
        )
        await state.set_state(AdminStates.waiting_for_ban_reason)
    except ValueError:
        await message.answer("❌ Неверный ID. Введите число.")

@dp.message(AdminStates.waiting_for_ban_reason, F.text == "❌ Отмена")
async def cancel_ban_reason(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Бан отменен", reply_markup=get_user_management_keyboard())

@dp.message(AdminStates.waiting_for_ban_reason)
async def execute_ban(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data["user_id"]
    reason = message.text
    
    await db.ban_user(user_id, reason, message.from_user.id)
    await state.clear()
    
    await message.answer(
        f"✅ Пользователь {user_id} забанен!\n"
        f"Причина: {reason}",
        reply_markup=get_user_management_keyboard()
    )
    
    # Уведомляем пользователя о бане
    try:
        await bot.send_message(
            user_id,
            f"⛔ Вы были забанены в боте!\n\nПричина: {reason}"
        )
    except:
        pass

@dp.message(F.text == "🔓 Разбанить пользователя")
async def unban_user_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        return
    
    await message.answer(
        "🔓 Введите ID пользователя для разбана:\n\n"
        "Можно отправить /cancel для отмены",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_user_id)

@dp.message(AdminStates.waiting_for_user_id)
async def execute_unban(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Действие отменено", reply_markup=get_user_management_keyboard())
        return
    
    try:
        user_id = int(message.text.strip())
        await db.unban_user(user_id)
        await state.clear()
        
        await message.answer(
            f"✅ Пользователь {user_id} разбанен!",
            reply_markup=get_user_management_keyboard()
        )
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                "✅ Вы были разбанены в боте!"
            )
        except:
            pass
    except ValueError:
        await message.answer("❌ Неверный ID. Введите число.")

@dp.message(F.text == "🚫 Список забаненных")
async def list_banned(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    banned = await db.get_banned_users()
    
    if not banned:
        await message.answer("📭 Нет забаненных пользователей", reply_markup=get_user_management_keyboard())
        return
    
    text = "🚫 *Забаненные пользователи:*\n\n"
    for user in banned:
        text += f"ID: `{user['user_id']}`\n"
        text += f"👤 {user['first_name'] or 'Нет имени'}\n"
        text += f"🆔 @{user['username'] or 'нет юзернейма'}\n"
        text += f"📝 Причина: {user['reason']}\n"
        text += f"📅 Забанен: {user['banned_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
    
    await message.answer(text, parse_mode="Markdown", reply_markup=get_user_management_keyboard())

# ========== УПРАВЛЕНИЕ АДМИНАМИ ==========
@dp.message(F.text == "👮 Управление админами")
async def admin_management(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    await message.answer(
        "👮 *Управление администраторами*\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_admin_management_keyboard()
    )

@dp.message(F.text == "📋 Список админов")
async def list_admins(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    admins = await db.get_admins()
    
    text = "👮 *Список администраторов:*\n\n"
    for admin in admins:
        is_main = "⭐ " if admin["user_id"] == ADMIN_ID else ""
        text += f"{is_main}ID: `{admin['user_id']}`\n"
        text += f"👤 {admin['first_name'] or 'Нет имени'} {admin['last_name'] or ''}\n"
        text += f"🆔 @{admin['username'] or 'нет юзернейма'}\n"
        text += f"📅 Добавлен: {admin['added_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
    
    await message.answer(text, parse_mode="Markdown", reply_markup=get_admin_management_keyboard())

@dp.message(F.text == "➕ Добавить админа")
async def add_admin_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        return
    
    await message.answer(
        "➕ Введите ID пользователя, которого хотите сделать администратором:",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_add_admin)

@dp.message(AdminStates.waiting_for_add_admin, F.text == "❌ Отмена")
async def cancel_add_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено", reply_markup=get_admin_management_keyboard())

@dp.message(AdminStates.waiting_for_add_admin)
async def execute_add_admin(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await db.add_admin(user_id, message.from_user.id)
        await state.clear()
        
        await message.answer(
            f"✅ Пользователь {user_id} добавлен в администраторы!",
            reply_markup=get_admin_management_keyboard()
        )
        
        # Уведомляем нового админа
        try:
            await bot.send_message(
                user_id,
                "🎉 Вы были назначены администратором бота!\n"
                "Используйте команду /admin для доступа к админ-панели."
            )
        except:
            pass
    except ValueError:
        await message.answer("❌ Неверный ID. Введите число.")

@dp.message(F.text == "❌ Удалить админа")
async def remove_admin_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        return
    
    admins = await db.get_admins()
    deletable = [a for a in admins if a["user_id"] != ADMIN_ID]
    
    if not deletable:
        await message.answer("❌ Нет администраторов для удаления", reply_markup=get_admin_management_keyboard())
        return
    
    keyboard = InlineKeyboardBuilder()
    for admin in deletable:
        keyboard.add(InlineKeyboardButton(
            text=f"❌ {admin['user_id']} - {admin['first_name'] or 'без имени'}",
            callback_data=f"remove_admin_{admin['user_id']}"
        ))
    
    keyboard.add(InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_remove_admin"))
    
    await message.answer(
        "Выберите администратора для удаления:",
        reply_markup=keyboard.adjust(1).as_markup()
    )

@dp.callback_query(F.data.startswith("remove_admin_"))
async def execute_remove_admin(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    user_id = int(callback.data.split("_")[2])
    success = await db.remove_admin(user_id)
    
    if success:
        await callback.message.edit_text(f"✅ Администратор {user_id} удален!")
        await bot.send_message(user_id, "⛔ Вы были лишены прав администратора.")
    else:
        await callback.message.edit_text("❌ Нельзя удалить главного администратора!")
    
    await callback.answer()

@dp.callback_query(F.data == "cancel_remove_admin")
async def cancel_remove_admin(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    await callback.message.delete()
    await callback.message.answer("❌ Удаление отменено", reply_markup=get_admin_management_keyboard())
    await callback.answer()

# ========== РАССЫЛКА ==========
@dp.message(F.text == "📢 Рассылка")
async def start_broadcast(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id):
        return
    
    await message.answer(
        "📢 Введите сообщение для рассылки.\n\n"
        "Можно отправлять текст, фото, видео, документы и т.д.\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_broadcast)

@dp.message(AdminStates.waiting_for_broadcast, F.text == "❌ Отмена")
async def cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Рассылка отменена", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_broadcast)
async def send_broadcast(message: Message, state: FSMContext):
    await state.clear()
    
    users = await db.get_all_users(include_blocked=False)
    total = len(users)
    success = 0
    failed = 0
    
    status_msg = await message.answer(f"📢 Начинаю рассылку для {total} пользователей...")
    
    for i, user_id in enumerate(users):
        try:
            if message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption)
            elif message.document:
                await bot.send_document(user_id, message.document.file_id, caption=message.caption)
            else:
                await bot.send_message(user_id, message.text or message.caption)
            success += 1
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
        
        # Показываем прогресс каждые 10 сообщений
        if (i + 1) % 10 == 0:
            await status_msg.edit_text(
                f"📢 Рассылка в процессе...\n"
                f"Отправлено: {i + 1}/{total}\n"
                f"✅ Успешно: {success}\n"
                f"❌ Ошибок: {failed}"
            )
    
    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📊 Статистика:\n"
        f"Всего: {total}\n"
        f"✅ Успешно: {success}\n"
        f"❌ Ошибок: {failed}"
    )
    await message.answer("✅ Рассылка завершена!", reply_markup=get_admin_keyboard())

# ========== ПЛАТЕЖИ ==========
@dp.message(F.text == "💰 Платежи")
async def show_payments_stats(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    async with db_pool.acquire() as conn:
        # Получаем общую статистику платежей
        total_paid = await conn.fetchval("SELECT COUNT(*) FROM payments WHERE status = 'paid'")
        total_amount = await conn.fetchval("SELECT SUM(amount) FROM payments WHERE status = 'paid'")
        
        # Последние 10 платежей
        recent_payments = await conn.fetch('''
            SELECT p.*, u.username, u.first_name
            FROM payments p
            LEFT JOIN users u ON u.user_id = p.user_id
            WHERE p.status = 'paid'
            ORDER BY p.completed_at DESC
            LIMIT 10
        ''')
        
        text = f"💰 *Статистика платежей*\n\n"
        text += f"✅ Успешных платежей: {total_paid}\n"
        text += f"💵 Общая сумма: {float(total_amount) if total_amount else 0:.2f} $\n\n"
        text += f"📋 *Последние 10 платежей:*\n\n"
        
        for payment in recent_payments:
            user_name = payment['first_name'] or str(payment['user_id'])
            if payment['username']:
                user_name += f" (@{payment['username']})"
            
            text += f"👤 {user_name}\n"
            text += f"💰 {payment['amount']} {payment['asset']}\n"
            text += f"📅 {payment['completed_at'].strftime('%d.%m.%Y %H:%M')}\n"
            text += f"🆔 {payment['invoice_id']}\n\n"
        
        await message.answer(text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

# ========== АНОНИМНЫЕ СООБЩЕНИЯ ДЛЯ АДМИНА ==========
@dp.message(F.text == "💬 Анонимные сообщения")
async def view_anonymous_messages(message: Message):
    if not await db.is_admin(message.from_user.id):
        return
    
    unanswered = await db.get_unanswered_messages()
    
    if not unanswered:
        await message.answer("📭 Нет непрочитанных анонимных сообщений", reply_markup=get_admin_keyboard())
        return
    
    keyboard = InlineKeyboardBuilder()
    for msg in unanswered:
        preview = msg["content_data"][:30] + "..." if len(msg["content_data"]) > 30 else msg["content_data"]
        keyboard.add(InlineKeyboardButton(
            text=f"📨 #{msg['id']} - {preview}",
            callback_data=f"view_anon_{msg['id']}"
        ))
    
    await message.answer(
        "💬 *Непрочитанные анонимные сообщения:*\n\n"
        "Нажмите на сообщение, чтобы ответить",
        parse_mode="Markdown",
        reply_markup=keyboard.adjust(1).as_markup()
    )

@dp.callback_query(F.data.startswith("view_anon_"))
async def view_anonymous_message(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    msg_id = int(callback.data.split("_")[2])
    msg = await db.get_message_by_id(msg_id)
    
    if not msg:
        await callback.message.edit_text("❌ Сообщение не найдено")
        return
    
    # Формируем клавиатуру для ответа
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Ответить", callback_data=f"reply_anon_{msg_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_anonymous")]
    ])
    
    # Отображаем сообщение
    if msg["content_type"] == "text":
        await callback.message.edit_text(
            f"📬 *Анонимное сообщение #{msg['id']}*\n\n"
            f"Текст: {msg['content_data']}\n\n"
            f"Получено: {msg['created_at'].strftime('%d.%m.%Y %H:%M')}",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    elif msg["content_type"] == "photo":
        data = json.loads(msg["content_data"])
        await callback.message.delete()
        await callback.message.answer_photo(
            data["file_id"],
            caption=f"📬 *Анонимное сообщение #{msg['id']}*\n\n"
                    f"Подпись: {data['caption'] or 'Нет подписи'}\n\n"
                    f"Получено: {msg['created_at'].strftime('%d.%m.%Y %H:%M')}",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    await callback.answer()

@dp.callback_query(F.data.startswith("reply_anon_"))
async def reply_to_anonymous(callback: CallbackQuery, state: FSMContext):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    msg_id = int(callback.data.split("_")[2])
    await state.update_data(reply_to_msg_id=msg_id)
    
    await callback.message.answer(
        "✉️ Введите ваш ответ на анонимное сообщение.\n\n"
        "Вы можете отправить текст или фото.\n"
        "Для отмены нажмите ❌ Отмена",
        reply_markup=get_cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_anonymous_reply)
    await callback.answer()

@dp.message(AdminStates.waiting_for_anonymous_reply, F.text == "❌ Отмена")
async def cancel_anonymous_reply(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Ответ отменен", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_anonymous_reply)
async def process_anonymous_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    original_msg_id = data["reply_to_msg_id"]
    
    await send_anonymous_reply_to_user(original_msg_id, message, message.from_user.id)
    await state.clear()
    await message.answer("✅ Ответ отправлен!", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_anonymous_reply, F.photo)
async def process_anonymous_reply_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    original_msg_id = data["reply_to_msg_id"]
    
    await send_anonymous_reply_to_user(original_msg_id, message, message.from_user.id)
    await state.clear()
    await message.answer("✅ Ответ отправлен!", reply_markup=get_admin_keyboard())

@dp.callback_query(F.data == "back_to_anonymous")
async def back_to_anonymous_list(callback: CallbackQuery):
    if not await db.is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа")
        return
    
    await view_anonymous_messages(callback.message)
    await callback.answer()

async def send_anonymous_reply_to_user(original_msg_id, admin_message, admin_id):
    """Отправляет ответ пользователю"""
    original_msg = await db.get_message_by_id(original_msg_id)
    if not original_msg:
        return False
    
    user_id = original_msg["user_id"]
    
    # Проверяем, не забанен ли пользователь
    if await db.is_user_banned(user_id):
        await bot.send_message(
            admin_id,
            f"❌ Не удалось отправить ответ: пользователь забанен"
        )
        return False
    
    try:
        if admin_message.photo:
            # Сохраняем ответ
            content_type = "photo"
            photo_file_id = admin_message.photo[-1].file_id
            caption = admin_message.caption or ""
            content_data = json.dumps({"file_id": photo_file_id, "caption": caption})
            
            await db.save_admin_reply(original_msg_id, user_id, admin_message.message_id, content_type, content_data)
            await db.mark_as_answered(original_msg_id)
            
            # Отправляем пользователю
            await bot.send_photo(
                user_id,
                photo_file_id,
                caption=f"📨 *Ответ от администратора:*\n\n{caption}",
                parse_mode="Markdown"
            )
        else:
            # Текстовый ответ
            content_type = "text"
            content_data = admin_message.text or admin_message.caption
            
            await db.save_admin_reply(original_msg_id, user_id, admin_message.message_id, content_type, content_data)
            await db.mark_as_answered(original_msg_id)
            
            await bot.send_message(
                user_id,
                f"📨 *Ответ от администратора:*\n\n{content_data}",
                parse_mode="Markdown"
            )
        
        await bot.send_message(
            admin_id,
            "✅ Ответ отправлен пользователю!"
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки ответа: {e}")
        await bot.send_message(
            admin_id,
            f"❌ Ошибка при отправке ответа: {e}"
        )
        return False


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
