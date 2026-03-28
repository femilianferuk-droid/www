import os
import telebot
import google.generativeai as genai
import logging
from telebot.types import Message
from dotenv import load_dotenv

# Загрузка переменных окружения из файла .env
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyBJ4yGFDp33ly8LaJgk3JPrVC-6YQKmwWE")

# Проверка наличия токена
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

# Инициализация Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Настройка модели Gemini
generation_config = {
    "temperature": 0.9,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 2048,
}

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",  # Можно использовать "gemini-1.5-pro" для более мощной модели
    generation_config=generation_config,
    safety_settings=safety_settings
)

# Инициализация Telegram бота
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Словарь для хранения истории диалогов пользователей
user_conversations = {}

def get_or_create_chat(user_id):
    """Получить или создать чат для пользователя"""
    if user_id not in user_conversations:
        user_conversations[user_id] = model.start_chat(history=[])
    return user_conversations[user_id]

@bot.message_handler(commands=['start'])
def send_welcome(message: Message):
    """Обработчик команды /start"""
    welcome_text = """
🤖 Привет! Я бот на основе Google Gemini AI.

Я могу:
• Отвечать на вопросы
• Помогать с задачами
• Генерировать текст
• Вести диалог

Просто отправь мне сообщение, и я отвечу!

Команды:
/start - Показать это сообщение
/clear - Очистить историю диалога
/help - Показать помощь
    """
    bot.reply_to(message, welcome_text)

@bot.message_handler(commands=['help'])
def send_help(message: Message):
    """Обработчик команды /help"""
    help_text = """
📚 Доступные команды:

/start - Начать работу с ботом
/clear - Очистить историю диалога
/help - Показать это сообщение

💡 Просто напишите любое сообщение, и я отвечу с помощью Gemini AI!
    """
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['clear'])
def clear_history(message: Message):
    """Очистить историю диалога пользователя"""
    user_id = message.from_user.id
    if user_id in user_conversations:
        user_conversations[user_id] = model.start_chat(history=[])
        bot.reply_to(message, "✅ История диалога очищена!")
    else:
        bot.reply_to(message, "📝 У вас еще нет истории диалога для очистки.")

@bot.message_handler(func=lambda message: True)
def handle_message(message: Message):
    """Обработчик всех текстовых сообщений"""
    try:
        user_id = message.from_user.id
        user_message = message.text
        
        # Отправляем уведомление о наборе текста
        bot.send_chat_action(message.chat.id, 'typing')
        
        # Получаем или создаем чат для пользователя
        chat = get_or_create_chat(user_id)
        
        # Отправляем запрос к Gemini
        response = chat.send_message(user_message)
        
        # Отправляем ответ пользователю
        bot.reply_to(message, response.text)
        
        logger.info(f"User {user_id}: {user_message[:50]}... -> Response sent")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        error_message = "😔 Извините, произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже."
        bot.reply_to(message, error_message)

if __name__ == '__main__':
    logger.info("Бот запущен...")
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Ошибка при работе бота: {e}")
