import asyncio
import json
import os
import subprocess
import sys
import signal
from pathlib import Path
from typing import Dict

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
CHILD_BOTS_PID_FILE = "child_bots_pids.json"

# Создаем папку для дочерних ботов
Path(CHILD_BOTS_DIR).mkdir(exist_ok=True)

# Хранилище процессов дочерних ботов
child_processes = {}

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

def save_child_pid(token: str, pid: int):
    """Сохраняет PID дочернего процесса"""
    pids_data = {}
    if os.path.exists(CHILD_BOTS_PID_FILE):
        with open(CHILD_BOTS_PID_FILE, 'r') as f:
            pids_data = json.load(f)
    
    pids_data[token] = pid
    
    with open(CHILD_BOTS_PID_FILE, 'w') as f:
        json.dump(pids_data, f)

def load_child_pids() -> Dict:
    """Загружает сохраненные PIDы"""
    if os.path.exists(CHILD_BOTS_PID_FILE):
        with open(CHILD_BOTS_PID_FILE, 'r') as f:
            return json.load(f)
    return {}

# ================= СОСТОЯНИЯ FSM =================
class AddBotStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_admin_id = State()

class EditBotStates(StatesGroup):
    editing_welcome = State()
    adding_button = State()

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
        [InlineKeyboardButton(text="🔄 Перезапустить бота", callback_data=f"restart_bot_{bot_token}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="my_bots")]
    ])

# ================= ЗАПУСК ДОЧЕРНЕГО БОТА =================
def create_child_bot_script(token: str, admin_id: int, welcome_text: str, buttons: list) -> str:
    """Создает скрипт для дочернего бота"""
    # Экранируем кавычки в тексте
    welcome_text_escaped = welcome_text.replace('"', '\\"').replace('\n', '\\n')
    
    script_content = f'''import asyncio
import json
import os
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

# Конфигурация
BOT_TOKEN = "{token}"
ADMIN_ID = {admin_id}
DATA_FILE = f"child_bot_data_{{BOT_TOKEN[-10:]}}.json"

def load_bot_data():
    """Загружает данные бота из файла"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {{"welcome_text": "{welcome_text_escaped}", "buttons": {json.dumps(buttons)}}}

def save_bot_data(data):
    """Сохраняет данные бота в файл"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def start_command(message: types.Message):
    """Обработчик команды /start"""
    data = load_bot_data()
    
    # Создаем клавиатуру
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    # Добавляем пользовательские кнопки
    for btn in data["buttons"]:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text=btn["text"], url=btn["url"])])
    
    # Добавляем кнопку "Хочу такого бота" (неудаляемую)
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🤖 Хочу такого бота", url="https://t.me/VestPerehodBot")])
    
    await message.answer(data["welcome_text"], reply_markup=keyboard)

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    """Админ-панель"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return
    
    await message.answer(
        "🔧 **Админ-панель**\\n\\n"
        "Доступные команды:\\n"
        "`/edit_welcome` \\- изменить приветствие\\n"
        "`/add_button` \\- добавить кнопку\\n"
        "`/delete_button` \\- удалить кнопку\\n"
        "`/list_buttons` \\- список кнопок",
        parse_mode="MarkdownV2"
    )

@dp.message(Command("edit_welcome"))
async def edit_welcome(message: types.Message):
    """Изменение приветствия"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа")
        return
    
    # Ждем новое приветствие
    await message.answer("✏️ Отправьте новое приветственное сообщение:")
    
    @dp.message(F.text)
    async def get_new_welcome(msg: types.Message):
        if msg.from_user.id != ADMIN_ID:
            return
        
        data = load_bot_data()
        data["welcome_text"] = msg.text
        save_bot_data(data)
        
        await msg.answer("✅ Приветствие успешно обновлено!")
        
        # Удаляем временный обработчик
        dp.message.handlers.remove(get_new_welcome)

@dp.message(Command("add_button"))
async def add_button(message: types.Message):
    """Добавление кнопки"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа")
        return
    
    await message.answer(
        "➕ **Добавление кнопки**\\n\\n"
        "Отправьте кнопку в формате:\\n"
        "`Текст кнопки, https://ссылка`\\n\\n"
        "Пример: `Написать админу, https://t.me/username`",
        parse_mode="MarkdownV2"
    )
    
    @dp.message(F.text)
    async def get_new_button(msg: types.Message):
        if msg.from_user.id != ADMIN_ID:
            return
        
        try:
            button_text, button_url = msg.text.split(",", 1)
            button_text = button_text.strip()
            button_url = button_url.strip()
            
            if not button_url.startswith(("http://", "https://")):
                button_url = "https://" + button_url
            
            data = load_bot_data()
            data["buttons"].append({{"text": button_text, "url": button_url}})
            save_bot_data(data)
            
            await msg.answer(f"✅ Кнопка \"{button_text}\" успешно добавлена!")
            
            # Удаляем временный обработчик
            dp.message.handlers.remove(get_new_button)
            
        except Exception as e:
            await msg.answer(f"❌ Ошибка: {e}\\nПравильный формат: Текст кнопки, https://ссылка")

@dp.message(Command("delete_button"))
async def delete_button(message: types.Message):
    """Удаление кнопки"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа")
        return
    
    data = load_bot_data()
    
    if not data["buttons"]:
        await message.answer("❌ У бота нет кнопок для удаления")
        return
    
    # Показываем список кнопок
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for i, btn in enumerate(data["buttons"]):
        keyboard.inline_keyboard.append([InlineKeyboardButton(
            text=f"🗑 {btn['text']}",
            callback_data=f"del_btn_{{i}}"
        )])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    
    await message.answer("🗑 Выберите кнопку для удаления:", reply_markup=keyboard)
    
    @dp.callback_query(F.data.startswith("del_btn_"))
    async def delete_button_callback(callback: types.CallbackQuery):
        if callback.from_user.id != ADMIN_ID:
            await callback.answer("Нет доступа")
            return
        
        btn_index = int(callback.data.split("_")[2])
        data = load_bot_data()
        deleted_text = data["buttons"][btn_index]["text"]
        del data["buttons"][btn_index]
        save_bot_data(data)
        
        await callback.message.edit_text(f"✅ Кнопка \"{deleted_text}\" удалена!")
        
        # Удаляем обработчик
        dp.callback_query.handlers.remove(delete_button_callback)

@dp.message(Command("list_buttons"))
async def list_buttons(message: types.Message):
    """Список кнопок"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Нет доступа")
        return
    
    data = load_bot_data()
    
    if not data["buttons"]:
        await message.answer("📭 У бота нет кнопок")
        return
    
    buttons_text = "🔘 **Список кнопок:**\\n\\n"
    for i, btn in enumerate(data["buttons"], 1):
        buttons_text += f"{i}\\. {btn['text']} → {btn['url']}\\n"
    
    await message.answer(buttons_text, parse_mode="MarkdownV2")

async def main():
    """Запуск бота"""
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
'''
    
    return script_content

def start_child_bot(token: str, admin_id: int, welcome_text: str, buttons: list):
    """Запускает дочернего бота в отдельном процессе"""
    try:
        # Создаем уникальное имя файла для скрипта
        script_filename = f"bot_{token[-10:]}.py"
        script_path = Path(CHILD_BOTS_DIR) / script_filename
        
        # Создаем файл скрипта
        script_content = create_child_bot_script(token, admin_id, welcome_text, buttons)
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script_content)
        
        # Создаем файл данных для дочернего бота
        child_data_file = Path(CHILD_BOTS_DIR) / f"child_bot_data_{token[-10:]}.json"
        with open(child_data_file, 'w', encoding='utf-8') as f:
            json.dump({
                "welcome_text": welcome_text,
                "buttons": buttons
            }, f, ensure_ascii=False, indent=2)
        
        # Запускаем дочерний процесс
        process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path.cwd()),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        
        # Сохраняем PID процесса
        child_processes[token] = process
        save_child_pid(token, process.pid)
        
        print(f"✅ Дочерний бот запущен: {token} (PID: {process.pid})")
        
        # Небольшая задержка для проверки
        asyncio.create_task(check_child_bot_startup(token, process))
        
    except Exception as e:
        print(f"❌ Ошибка при запуске дочернего бота: {e}")

async def check_child_bot_startup(token: str, process: subprocess.Popen):
    """Проверяет успешность запуска дочернего бота"""
    await asyncio.sleep(3)
    
    if process.poll() is not None:
        stdout, stderr = process.communicate()
        print(f"❌ Дочерний бот {token} завершился с ошибкой:")
        print(f"STDOUT: {stdout.decode('utf-8', errors='ignore')}")
        print(f"STDERR: {stderr.decode('utf-8', errors='ignore')}")

def stop_child_bot(token: str):
    """Останавливает дочернего бота"""
    if token in child_processes:
        try:
            process = child_processes[token]
            process.terminate()
            process.wait(timeout=5)
            print(f"✅ Дочерний бот остановлен: {token}")
        except:
            process.kill()
        finally:
            del child_processes[token]

def restart_child_bot(token: str, admin_id: int, welcome_text: str, buttons: list):
    """Перезапускает дочернего бота"""
    stop_child_bot(token)
    start_child_bot(token, admin_id, welcome_text, buttons)

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
            "Чтобы добавить бота, нажмите кнопку ниже",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить бота", callback_data="add_bot")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
            ])
        )
    else:
        keyboard = []
        for bot in user_bots:
            bot_token = bot["token"]
            # Проверяем, запущен ли бот
            status = "🟢" if bot_token in child_processes else "🔴"
            keyboard.append([InlineKeyboardButton(
                text=f"{status} Бот {bot_token[-10:]}...", 
                callback_data=f"manage_bot_{bot_token}"
            )])
        keyboard.append([InlineKeyboardButton(text="➕ Добавить бота", callback_data="add_bot")])
        keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")])
        
        await callback.message.edit_text(
            f"📋 Ваши боты ({len(user_bots)}/2):\n\n"
            "🟢 - бот запущен\n"
            "🔴 - бот остановлен\n\n"
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
        f"🆔 ID: `{callback.from_user.id}`\n"
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
        "🔑 **Отправьте токен вашего бота**\n\n"
        "Как получить токен:\n"
        "1️⃣ Напишите @BotFather\n"
        "2️⃣ Создайте бота командой /newbot\n"
        "3️⃣ Скопируйте токен\n\n"
        "❗ Токен должен быть в формате:\n"
        "`1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`",
        parse_mode="Markdown"
    )
    await state.set_state(AddBotStates.waiting_for_token)

@dp.message(AddBotStates.waiting_for_token)
async def get_bot_token(message: Message, state: FSMContext):
    """Получает токен бота"""
    token = message.text.strip()
    
    # Простая проверка формата токена
    if not token or ":" not in token or len(token) < 30:
        await message.answer(
            "❌ Неверный формат токена.\n"
            "Токен должен выглядеть так:\n"
            "`1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`\n\n"
            "Попробуйте еще раз:",
            parse_mode="Markdown"
        )
        return
    
    await state.update_data(bot_token=token)
    await message.answer(
        "👑 **Отправьте ID администратора бота**\n\n"
        "Этот пользователь сможет управлять ботом через админ-панель.\n\n"
        "Как получить ID:\n"
        "1️⃣ Напишите @userinfobot\n"
        "2️⃣ Отправьте любое сообщение боту\n"
        "3️⃣ Скопируйте ваш ID\n\n"
        "Пример: `123456789`",
        parse_mode="Markdown"
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
        "✅ **Бот успешно создан и запущен!**\n\n"
        f"🔑 Токен: `{bot_token}`\n"
        f"👑 ID админа: `{admin_id}`\n\n"
        "Теперь вы можете настроить приветствие и кнопки в разделе **Мои боты**.\n\n"
        "❗ **Важно**: Администратор бота может управлять им через команду `/admin` в дочернем боте.\n\n"
        "📌 Ссылка на вашего бота:\n"
        f"https://t.me/{(await get_bot_info(bot_token))[0]}",
        parse_mode="Markdown"
    )
    
    await state.clear()

async def get_bot_info(token: str):
    """Получает информацию о боте по токену"""
    try:
        temp_bot = Bot(token=token)
        me = await temp_bot.get_me()
        await temp_bot.session.close()
        return me.username, me.first_name
    except:
        return "unknown", "Unknown"

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
    
    # Получаем статус бота
    status = "🟢 Запущен" if bot_token in child_processes else "🔴 Остановлен"
    
    await callback.message.edit_text(
        f"🤖 **Управление ботом**\n\n"
        f"Статус: {status}\n"
        f"🔑 Токен: `{bot_data['token']}`\n"
        f"👑 Админ ID: `{bot_data['admin_id']}`\n\n"
        f"📝 **Приветствие:**\n{bot_data['welcome_text'][:100]}{'...' if len(bot_data['welcome_text']) > 100 else ''}\n\n"
        f"🔘 **Кнопок:** {len(bot_data['buttons'])}",
        parse_mode="Markdown",
        reply_markup=get_bot_management_keyboard(bot_token)
    )

@dp.callback_query(F.data.startswith("edit_welcome_"))
async def edit_welcome_start(callback: CallbackQuery, state: FSMContext):
    """Начинает редактирование приветствия"""
    bot_token = callback.data.replace("edit_welcome_", "")
    await state.update_data(editing_bot_token=bot_token)
    await callback.message.edit_text(
        "✏️ **Редактирование приветствия**\n\n"
        "Отправьте новое приветственное сообщение.\n\n"
        "Вы можете использовать обычный текст, эмодзи и Markdown разметку.\n\n"
        "Пример: `👋 Привет! Я бот-переходник`",
        parse_mode="Markdown",
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
    bot_data = None
    for bot in all_data.get(user_id, {}).get("bots", []):
        if bot["token"] == bot_token:
            bot["welcome_text"] = new_welcome
            bot_data = bot
            break
    
    save_data(all_data)
    
    # Перезапускаем дочернего бота с новыми настройками
    if bot_data:
        restart_child_bot(bot_data["token"], bot_data["admin_id"], bot_data["welcome_text"], bot_data["buttons"])
    
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
        "**Примеры:**\n"
        "`Написать админу, https://t.me/username`\n"
        "`Канал, https://t.me/channel`\n"
        "`Сайт, https://example.com`\n\n"
        "❗ Ссылка должна начинаться с http:// или https://",
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
        if "," not in message.text:
            raise ValueError("Не найден разделитель ','")
        
        button_text, button_url = message.text.split(",", 1)
        button_text = button_text.strip()
        button_url = button_url.strip()
        
        if not button_text:
            raise ValueError("Текст кнопки не может быть пустым")
        
        if not button_url:
            raise ValueError("Ссылка не может быть пустой")
        
        if not button_url.startswith(("http://", "https://")):
            button_url = "https://" + button_url
        
        # Обновляем данные
        all_data = load_data()
        bot_data = None
        for bot in all_data.get(user_id, {}).get("bots", []):
            if bot["token"] == bot_token:
                bot["buttons"].append({"text": button_text, "url": button_url})
                bot_data = bot
                break
        
        save_data(all_data)
        
        # Перезапускаем дочернего бота
        if bot_data:
            restart_child_bot(bot_data["token"], bot_data["admin_id"], bot_data["welcome_text"], bot_data["buttons"])
        
        await message.answer(f"✅ Кнопка \"{button_text}\" успешно добавлена!")
        
    except Exception as e:
        await message.answer(
            f"❌ Ошибка: {str(e)}\n\n"
            "Правильный формат:\n"
            "`Текст кнопки, https://ссылка`\n\n"
            "Попробуйте еще раз или нажмите Отмена:",
            parse_mode="Markdown"
        )
        return
    
    await state.clear()

@dp.callback_query(F.data.startswith("delete_button_"))
async def delete_button_start(callback: CallbackQuery):
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
        # Экранируем текст для безопасного использования в callback_data
        keyboard.append([InlineKeyboardButton(
            text=f"🗑 {button['text'][:30]}",
            callback_data=f"delete_btn_{bot_token}_{i}"
        )])
    keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"manage_bot_{bot_token}")])
    
    await callback.message.edit_text(
        "🗑 **Выберите кнопку для удаления:**\n\n"
        "Кнопку '🤖 Хочу такого бота' удалить нельзя.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(F.data.startswith("delete_btn_"))
async def delete_button_confirm(callback: CallbackQuery):
    """Удаляет выбранную кнопку"""
    data_parts = callback.data.split("_")
    bot_token = "_".join(data_parts[2:-1]) if len(data_parts) > 3 else data_parts[2]
    button_index = int(data_parts[-1])
    
    user_id = str(callback.from_user.id)
    all_data = load_data()
    
    # Удаляем кнопку
    bot_data = None
    for bot in all_data.get(user_id, {}).get("bots", []):
        if bot["token"] == bot_token:
            if button_index < len(bot["buttons"]):
                deleted_text = bot["buttons"][button_index]["text"]
                del bot["buttons"][button_index]
                bot_data = bot
                save_data(all_data)
                
                # Перезапускаем дочернего бота
                if bot_data:
                    restart_child_bot(bot_data["token"], bot_data["admin_id"], bot_data["welcome_text"], bot_data["buttons"])
                
                await callback.message.edit_text(
                    f"✅ Кнопка \"{deleted_text}\" успешно удалена!",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"manage_bot_{bot_token}")]
                    ])
                )
                return
    
    await callback.answer("Кнопка не найдена")

@dp.callback_query(F.data.startswith("restart_bot_"))
async def restart_bot(callback: CallbackQuery):
    """Перезапускает дочернего бота"""
    bot_token = callback.data.replace("restart_bot_", "")
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
        "🔄 **Перезапуск бота...**\n\n"
        "Пожалуйста, подождите...",
        parse_mode="Markdown"
    )
    
    # Перезапускаем бота
    restart_child_bot(bot_data["token"], bot_data["admin_id"], bot_data["welcome_text"], bot_data["buttons"])
    
    await asyncio.sleep(2)
    
    await callback.message.edit_text(
        f"✅ **Бот успешно перезапущен!**\n\n"
        f"Статус: 🟢 Запущен",
        parse_mode="Markdown",
        reply_markup=get_bot_management_keyboard(bot_token)
    )

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
    print(f"📁 Папка для дочерних ботов: {CHILD_BOTS_DIR}")
    
    # Загружаем сохраненные PIDы и восстанавливаем процессы
    saved_pids = load_child_pids()
    if saved_pids:
        print(f"🔄 Найдено {len(saved_pids)} сохраненных ботов")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
