import os
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile, CallbackQuery, Message
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import asyncpg
from asyncpg import Pool

# ========== ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7973988177"))

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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            
            # Добавляем приветствие по умолчанию, если пусто
            result = await conn.fetchval("SELECT COUNT(*) FROM welcome")
            if result == 0:
                await conn.execute(
                    "INSERT INTO welcome (text, photo_file_id) VALUES ($1, $2)",
                    "Добро пожаловать! 👋\n\nНажмите на кнопки ниже:", None
                )
            
            # Добавляем пример кнопки, если пусто
            result = await conn.fetchval("SELECT COUNT(*) FROM buttons")
            if result == 0:
                await conn.execute(
                    "INSERT INTO buttons (text, url) VALUES ($1, $2)",
                    "Пример ссылки", "https://example.com"
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
            rows = await conn.fetch("SELECT id, text, url FROM buttons ORDER BY id")
            return [(row["id"], row["text"], row["url"]) for row in rows]

    async def add_button(self, text, url):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO buttons (text, url) VALUES ($1, $2) RETURNING id",
                text, url
            )
            return row["id"]

    async def delete_button(self, button_id):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM buttons WHERE id = $1", button_id)

    # Методы для статистики
    async def add_user(self, user_id, username, first_name, last_name):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (user_id, username, first_name, last_name, created_at)
                VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id, username, first_name, last_name)

    async def add_click(self, button_id, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO button_clicks (button_id, user_id) VALUES ($1, $2)",
                button_id, user_id
            )

    async def get_stats(self):
        async with self.pool.acquire() as conn:
            # Всего пользователей
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            
            # Пользователей за последние 24 часа
            yesterday = datetime.now() - timedelta(days=1)
            new_users_24h = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE created_at > $1", yesterday
            )
            
            # Всего нажатий
            total_clicks = await conn.fetchval("SELECT COUNT(*) FROM button_clicks")
            
            # Статистика по каждой кнопке
            buttons = await self.get_buttons()
            button_stats = []
            for btn_id, text, url in buttons:
                clicks = await conn.fetchval(
                    "SELECT COUNT(*) FROM button_clicks WHERE button_id = $1", btn_id
                )
                button_stats.append({"id": btn_id, "text": text, "clicks": clicks})
            
            return {
                "total_users": total_users,
                "new_users_24h": new_users_24h,
                "total_clicks": total_clicks,
                "button_stats": button_stats
            }

    async def get_all_users(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT user_id FROM users")
            return [row["user_id"] for row in rows]


# ========== СОСТОЯНИЯ ДЛЯ FSM ==========
class AdminStates(StatesGroup):
    waiting_for_welcome_text = State()
    waiting_for_welcome_photo = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()
    waiting_for_broadcast = State()


# ========== КЛАВИАТУРЫ ==========
async def get_user_keyboard():
    """Клавиатура для пользователя"""
    buttons = await db.get_buttons()
    
    keyboard = InlineKeyboardBuilder()
    for btn_id, text, url in buttons:
        keyboard.add(InlineKeyboardButton(text=text, url=url, callback_data=f"click_{btn_id}"))
    
    return keyboard.adjust(1).as_markup()

def get_admin_keyboard():
    """Клавиатура админ-панели"""
    buttons = [
        [KeyboardButton(text="✏️ Изменить приветствие")],
        [KeyboardButton(text="➕ Добавить кнопку"), KeyboardButton(text="❌ Удалить кнопку")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📢 Рассылка")],
        [KeyboardButton(text="🔙 В главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cancel_keyboard():
    """Клавиатура отмены"""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db_pool = None
db = None


# ========== ПОЛЬЗОВАТЕЛЬСКИЕ ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def start_command(message: Message):
    user = message.from_user
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
    button_id = int(callback.data.split("_")[1])
    await db.add_click(button_id, callback.from_user.id)
    
    # Получаем URL кнопки для перехода
    async with db_pool.acquire() as conn:
        result = await conn.fetchval("SELECT url FROM buttons WHERE id = $1", button_id)
    
    if result:
        url = result
        await callback.answer("Переход по ссылке...")
        await callback.message.answer(f"🔗 Перейдите по ссылке:\n{url}")
    else:
        await callback.answer("Кнопка не найдена")


# ========== АДМИН-ПАНЕЛЬ ==========
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return
    
    await message.answer(
        "👋 Добро пожаловать в админ-панель!\n\n"
        "Выберите действие:",
        reply_markup=get_admin_keyboard()
    )

@dp.message(F.text == "🔙 В главное меню")
async def back_to_main(message: Message):
    if message.from_user.id != ADMIN_ID:
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

# Изменение приветствия
@dp.message(F.text == "✏️ Изменить приветствие")
async def change_welcome(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
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

# Добавление кнопки
@dp.message(F.text == "➕ Добавить кнопку")
async def add_button_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
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
    
    await db.add_button(button_text, button_url)
    await state.clear()
    await message.answer(f"✅ Кнопка \"{button_text}\" добавлена!", reply_markup=get_admin_keyboard())

# Удаление кнопки
@dp.message(F.text == "❌ Удалить кнопку")
async def delete_button_menu(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    buttons = await db.get_buttons()
    if not buttons:
        await message.answer("📭 Нет кнопок для удаления", reply_markup=get_admin_keyboard())
        return
    
    keyboard = InlineKeyboardBuilder()
    for btn_id, text, url in buttons:
        keyboard.add(InlineKeyboardButton(text=f"❌ {text}", callback_data=f"del_{btn_id}"))
    
    keyboard.add(InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_del"))
    
    await message.answer(
        "Выберите кнопку для удаления:",
        reply_markup=keyboard.adjust(1).as_markup()
    )

@dp.callback_query(F.data.startswith("del_"))
async def confirm_delete_button(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа")
        return
    
    button_id = int(callback.data.split("_")[1])
    await db.delete_button(button_id)
    await callback.message.edit_text("✅ Кнопка удалена!")
    await callback.answer()

@dp.callback_query(F.data == "cancel_del")
async def cancel_delete(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Нет доступа")
        return
    
    await callback.message.delete()
    await callback.message.answer("❌ Удаление отменено", reply_markup=get_admin_keyboard())
    await callback.answer()

# Статистика
@dp.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    stats = await db.get_stats()
    
    stats_text = (
        "📊 *Статистика бота*\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"🆕 Новых за 24ч: {stats['new_users_24h']}\n"
        f"🔘 Всего нажатий: {stats['total_clicks']}\n\n"
        "*Статистика по кнопкам:*\n"
    )
    
    for btn in stats['button_stats']:
        stats_text += f"• {btn['text']}: {btn['clicks']} нажатий\n"
    
    await message.answer(stats_text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

# Рассылка
@dp.message(F.text == "📢 Рассылка")
async def start_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
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
    
    users = await db.get_all_users()
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
