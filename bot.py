import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")

DATA_FILE = "user_data.json"
CHILD_BOTS_DIR = "child_bots"

# Создаем папку для дочерних ботов
Path(CHILD_BOTS_DIR).mkdir(exist_ok=True)

# ================= ХРАНЕНИЕ ДАННЫХ =================
def load_data() -> Dict:
    """Загружает данные пользователей из файла"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_data(data: Dict):
    """Сохраняет данные пользователей в файл"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================= СОСТОЯНИЯ FSM =================
class AddBotStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_admin_id = State()

class EditBotStates(StatesGroup):
    selecting_bot = State()
    editing_welcome = State()
    adding_button = State()
    selecting_button_to_delete = State()

# ================= КЛАВИАТУРЫ =================
def get_main_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура с кнопками Мои боты и Профиль"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои боты", callback_data="my_bots")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])

def get_bot_management_keyboard(bot_token: str) -> InlineKeyboardMarkup:
    """Клавиатура управления конкретным ботом"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить приветствие", callback_data=f"edit_welcome_{bot_token}")],
        [InlineKeyboardButton(text="➕ Добавить кнопку", callback_data=f"add_button_{bot_token}")],
        [InlineKeyboardButton(text="🗑 Удалить кнопку", callback_data=f"delete_button_{bot_token}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="my_bots")]
    ])

# ================= ЗАПУСК ДОЧЕРНЕГО БОТА =================
def start_child_bot(token: str, admin_id: int, welcome_text: str, buttons: list):
    """Запускает дочернего бота в отдельном процессе"""
    child_bot_script = f"""
import asyncio
import sys
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

BOT_TOKEN = "{token}"
ADMIN_ID = {admin_id}
WELCOME_TEXT = \"\"\"{welcome_text}\"\"\"
BUTTONS = {buttons}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn["text"], url=btn["url"])] for btn in BUTTONS
    ])
    # Добавляем кнопку "Хочу такого бота" (неудаляемую)
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🤖 Хочу такого бота", url="https://t.me/VestPerehodBot")])
    
    await message.answer(WELCOME_TEXT, reply_markup=keyboard)

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("🔧 Админ-панель\nИспользуйте команды:\\n/edit_welcome - изменить приветствие\\n/add_button - добавить кнопку\\n/delete_button - удалить кнопку")
    else:
        await message.answer("⛔ У вас нет доступа к админ-панели")

@dp.message(Command("edit_welcome"))
async def edit_welcome(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Отправьте новое приветственное сообщение:")
        
        @dp.message(F.text)
        async def get_new_welcome(msg: types.Message):
            if msg.from_user.id == ADMIN_ID:
                # Здесь нужно обновить WELCOME_TEXT в файле
                await msg.answer("✅ Приветствие обновлено!")
    
    await message.answer("⛔ Нет доступа")

@dp.message(Command("add_button"))
async def add_button(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Отправьте кнопку в формате: текст,url")
    else:
        await message.answer("⛔ Нет доступа")

@dp.message(Command("delete_button"))
async def delete_button(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Отправьте текст кнопки для удаления:")
    else:
        await message.answer("⛔ Нет доступа")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
"""
    
    script_path = Path(CHILD_BOTS_DIR) / f"bot_{token[-10:]}.py"
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(child_bot_script)
    
    # Запускаем дочернего бота
    subprocess.Popen([sys.executable, str(script_path)], 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL)

# ================= ОБРАБОТЧИКИ ГЛАВНОГО БОТА =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    await message.answer(
        "👋 Добро пожаловать в Vest Perehod!\n\n"
        "Я помогу вам создать ботов-переходников.\n"
        "Вы можете создать до 2 ботов.",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "my_bots")
async def show_my_bots(callback: CallbackQuery):
    """Показывает список ботов пользователя"""
    user_id = str(callback.from_user.id)
    data = load_data()
    user_bots = data.get(user_id, {}).get("bots", [])
    
    if not user_bots:
        await callback.message.edit_text(
            "📭 У вас пока нет ботов.\n\n"
            "Чтобы добавить бота, используйте команду /add_bot",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить бота", callback_data="add_bot")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
        )
    else:
        keyboard = []
        for bot in user_bots:
            bot_token = bot["token"]
            keyboard.append([InlineKeyboardButton(
                text=f"🤖 Бот {bot_token[-10:]}...", 
                callback_data=f"manage_bot_{bot_token}"
            )])
        keyboard.append([InlineKeyboardButton(text="➕ Добавить бота", callback_data="add_bot")])
        keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")])
        
        await callback.message.edit_text(
            f"📋 Ваши боты ({len(user_bots)}/2):\n\n"
            "Нажмите на бота для управления",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )

@dp.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    """Показывает профиль пользователя"""
    user_id = str(callback.from_user.id)
    data = load_data()
    user_bots = data.get(user_id, {}).get("bots", [])
    
    profile_text = (
        f"👤 **Профиль**\n\n"
        f"🆔 ID: {callback.from_user.id}\n"
        f"👤 Username: @{callback.from_user.username or 'Не указан'}\n"
        f"🤖 Создано ботов: {len(user_bots)}/2\n"
    )
    
    await callback.message.edit_text(
        profile_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
        ])
    )

@dp.callback_query(F.data == "add_bot")
async def add_bot_start(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс добавления бота"""
    user_id = str(callback.from_user.id)
    data = load_data()
    user_bots = data.get(user_id, {}).get("bots", [])
    
    if len(user_bots) >= 2:
        await callback.message.edit_text(
            "❌ Вы достигли лимита ботов (2).\n"
            "Удалите одного из ботов, чтобы добавить нового.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="my_bots")]
            ])
        )
        return
    
    await callback.message.edit_text(
        "🔑 Отправьте токен вашего бота.\n\n"
        "Как получить токен:\n"
        "1. Напишите @BotFather\n"
        "2. Создайте бота командой /newbot\n"
        "3. Скопируйте токен\n\n"
        "❗ Токен должен быть в формате: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
    )
    await state.set_state(AddBotStates.waiting_for_token)

@dp.message(AddBotStates.waiting_for_token)
async def get_bot_token(message: Message, state: FSMContext):
    """Получает токен бота"""
    token = message.text.strip()
    
    # Простая проверка формата токена
    if not token or ":" not in token:
        await message.answer(
            "❌ Неверный формат токена.\n"
            "Токен должен выглядеть так: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz\n"
            "Попробуйте еще раз:"
        )
        return
    
    await state.update_data(bot_token=token)
    await message.answer(
        "👑 Отправьте ID администратора бота.\n\n"
        "Этот пользователь сможет управлять ботом через админ-панель.\n\n"
        "Как получить ID:\n"
        "1. Напишите @userinfobot\n"
        "2. Перешлите сообщение боту\n"
        "3. Скопируйте ваш ID"
    )
    await state.set_state(AddBotStates.waiting_for_admin_id)

@dp.message(AddBotStates.waiting_for_admin_id)
async def get_admin_id(message: Message, state: FSMContext):
    """Получает ID администратора и создает бота"""
    try:
        admin_id = int(message.text.strip())
    except ValueError:
        await message.answer(
            "❌ ID должен быть числом.\n"
            "Попробуйте еще раз:"
        )
        return
    
    data = await state.get_data()
    bot_token = data["bot_token"]
    user_id = str(message.from_user.id)
    
    # Сохраняем данные
    all_data = load_data()
    if user_id not in all_data:
        all_data[user_id] = {"bots": []}
    
    # Создаем нового бота с настройками по умолчанию
    new_bot = {
        "token": bot_token,
        "admin_id": admin_id,
        "welcome_text": "👋 Добро пожаловать!\n\nНажмите на кнопку ниже, чтобы связаться с создателем бота.",
        "buttons": []
    }
    
    all_data[user_id]["bots"].append(new_bot)
    save_data(all_data)
    
    # Запускаем дочернего бота
    start_child_bot(bot_token, admin_id, new_bot["welcome_text"], new_bot["buttons"])
    
    await message.answer(
        "✅ Бот успешно создан и запущен!\n\n"
        f"🔑 Токен: {bot_token}\n"
        f"👑 ID админа: {admin_id}\n\n"
        "Теперь вы можете настроить приветствие и кнопки в разделе 'Мои боты'.\n\n"
        "❗ Важно: Администратор бота может управлять им через команду /admin"
    )
    
    await state.clear()

@dp.callback_query(F.data.startswith("manage_bot_"))
async def manage_bot(callback: CallbackQuery):
    """Управление конкретным ботом"""
    bot_token = callback.data.replace("manage_bot_", "")
    user_id = str(callback.from_user.id)
    data = load_data()
    
    # Находим бота
    bot_data = None
    for bot in data.get(user_id, {}).get("bots", []):
        if bot["token"] == bot_token:
            bot_data = bot
            break
    
    if not bot_data:
        await callback.answer("Бот не найден")
        return
    
    await callback.message.edit_text(
        f"🤖 **Управление ботом**\n\n"
        f"Токен: `{bot_data['token']}`\n"
        f"Админ ID: {bot_data['admin_id']}\n\n"
        f"📝 Приветствие:\n{bot_data['welcome_text'][:100]}...\n\n"
        f"🔘 Кнопок: {len(bot_data['buttons'])}",
        parse_mode="Markdown",
        reply_markup=get_bot_management_keyboard(bot_token)
    )

@dp.callback_query(F.data.startswith("edit_welcome_"))
async def edit_welcome_start(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование приветствия"""
    bot_token = callback.data.replace("edit_welcome_", "")
    await state.update_data(editing_bot_token=bot_token)
    await callback.message.edit_text(
        "✏️ Отправьте новое приветственное сообщение.\n\n"
        "Вы можете использовать обычный текст, эмодзи и Markdown разметку.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"manage_bot_{bot_token}")]
        ])
    )
    await state.set_state(EditBotStates.editing_welcome)

@dp.message(EditBotStates.editing_welcome)
async def save_new_welcome(message: Message, state: FSMContext):
    """Сохраняет новое приветствие"""
    data = await state.get_data()
    bot_token = data["editing_bot_token"]
    user_id = str(message.from_user.id)
    new_welcome = message.text
    
    # Обновляем данные
    all_data = load_data()
    for bot in all_data.get(user_id, {}).get("bots", []):
        if bot["token"] == bot_token:
            bot["welcome_text"] = new_welcome
            break
    
    save_data(all_data)
    
    # Перезапускаем дочернего бота с новыми настройками
    # Находим бота для перезапуска
    for bot in all_data[user_id]["bots"]:
        if bot["token"] == bot_token:
            start_child_bot(bot["token"], bot["admin_id"], bot["welcome_text"], bot["buttons"])
            break
    
    await message.answer("✅ Приветствие успешно обновлено!")
    await state.clear()

@dp.callback_query(F.data.startswith("add_button_"))
async def add_button_start(callback: CallbackQuery, state: FSMContext):
    """Начинает добавление кнопки"""
    bot_token = callback.data.replace("add_button_", "")
    await state.update_data(adding_bot_token=bot_token)
    await callback.message.edit_text(
        "➕ **Добавление кнопки**\n\n"
        "Отправьте кнопку в формате:\n"
        "`Текст кнопки, https://ссылка`\n\n"
        "Пример: `Написать админу, https://t.me/username`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"manage_bot_{bot_token}")]
        ])
    )
    await state.set_state(EditBotStates.adding_button)

@dp.message(EditBotStates.adding_button)
async def save_new_button(message: Message, state: FSMContext):
    """Сохраняет новую кнопку"""
    data = await state.get_data()
    bot_token = data["adding_bot_token"]
    user_id = str(message.from_user.id)
    
    try:
        # Парсим кнопку
        button_text, button_url = message.text.split(",", 1)
        button_text = button_text.strip()
        button_url = button_url.strip()
        
        if not button_url.startswith(("http://", "https://")):
            button_url = "https://" + button_url
        
        # Обновляем данные
        all_data = load_data()
        for bot in all_data.get(user_id, {}).get("bots", []):
            if bot["token"] == bot_token:
                bot["buttons"].append({"text": button_text, "url": button_url})
                break
        
        save_data(all_data)
        
        # Перезапускаем дочернего бота
        for bot in all_data[user_id]["bots"]:
            if bot["token"] == bot_token:
                start_child_bot(bot["token"], bot["admin_id"], bot["welcome_text"], bot["buttons"])
                break
        
        await message.answer(f"✅ Кнопка \"{button_text}\" успешно добавлена!")
        
    except Exception as e:
        await message.answer(
            f"❌ Ошибка: {e}\n\n"
            "Правильный формат: Текст кнопки, https://ссылка"
        )
        return
    
    await state.clear()

@dp.callback_query(F.data.startswith("delete_button_"))
async def delete_button_start(callback: CallbackQuery, state: FSMContext):
    """Показывает кнопки для удаления"""
    bot_token = callback.data.replace("delete_button_", "")
    user_id = str(callback.from_user.id)
    data = load_data()
    
    # Находим кнопки бота
    buttons = []
    for bot in data.get(user_id, {}).get("bots", []):
        if bot["token"] == bot_token:
            buttons = bot["buttons"]
            break
    
    if not buttons:
        await callback.message.edit_text(
            "❌ У этого бота нет кнопок для удаления",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data=f"manage_bot_{bot_token}")]
            ])
        )
        return
    
    keyboard = []
    for i, button in enumerate(buttons):
        keyboard.append([InlineKeyboardButton(
            text=f"🗑 {button['text']}",
            callback_data=f"delete_this_button_{bot_token}_{i}"
        )])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"manage_bot_{bot_token}")])
    
    await callback.message.edit_text(
        "🗑 **Выберите кнопку для удаления:**\n\n"
        "Кнопку 'Хочу такого бота' удалить нельзя.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(F.data.startswith("delete_this_button_"))
async def delete_button_confirm(callback: CallbackQuery):
    """Удаляет выбранную кнопку"""
    data_parts = callback.data.split("_")
    bot_token = "_".join(data_parts[3:-1])  # Восстанавливаем токен
    button_index = int(data_parts[-1])
    
    user_id = str(callback.from_user.id)
    all_data = load_data()
    
    # Удаляем кнопку
    for bot in all_data.get(user_id, {}).get("bots", []):
        if bot["token"] == bot_token:
            if button_index < len(bot["buttons"]):
                deleted_text = bot["buttons"][button_index]["text"]
                del bot["buttons"][button_index]
                save_data(all_data)
                
                # Перезапускаем дочернего бота
                start_child_bot(bot["token"], bot["admin_id"], bot["welcome_text"], bot["buttons"])
                
                await callback.message.edit_text(
                    f"✅ Кнопка \"{deleted_text}\" успешно удалена!",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"manage_bot_{bot_token}")]
                    ])
                )
                return
    
    await callback.answer("Кнопка не найдена")

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    """Возвращает в главное меню"""
    await callback.message.edit_text(
        "👋 Добро пожаловать в Vest Perehod!\n\n"
        "Я помогу вам создать ботов-переходников.\n"
        "Вы можете создать до 2 ботов.",
        reply_markup=get_main_keyboard()
    )

# ================= ЗАПУСК БОТА =================
async def main():
    """Запуск бота"""
    print("🤖 Бот Vest Perehod запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
