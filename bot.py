import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

# ---------- Конфигуратсияи вақт ----------
UTC = ZoneInfo("UTC")
DEFAULT_TZ = ZoneInfo("Asia/Dushanbe")  # Минтақаи вақтии пешфарз

# ---------- Конфигуратсия ----------
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
CAPSULES_FILE = DATA_DIR / "capsules.json"
USER_SETTINGS_FILE = DATA_DIR / "user_settings.json"
LOCALES_DIR = Path(__file__).parent / "locales"

MAX_CAPSULES = 10
MAX_FILES_PER_CAPSULE = 10

MAX_CAPSULE_SIZE_MB = 100  # Максимум андозаи умумии файлҳо барои як капсула (МБ)
MAX_CAPSULE_SIZE_BYTES = MAX_CAPSULE_SIZE_MB * 1024 * 1024  # 100 MB ба байт

def get_total_files_size(files) -> int:
    """Ҳисоб кардани андозаи умумии файлҳо"""
    total = 0
    for file in files:
        path = Path(file.file_path)
        if path.exists():
            total += path.stat().st_size
    return total

def format_size(size_bytes: int) -> str:
    """Формат кардани андоза ба намуди хондашаванда"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

logging.basicConfig(level=logging.INFO)

# ---------- Бот ва Dispatcher ----------
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
scheduler = AsyncIOScheduler()

# ---------- Системаи тарҷума ----------
class TranslationManager:
    def __init__(self):
        self.translations = {}
        self.load_all_translations()
    
    def load_all_translations(self):
        """Бор кардани ҳамаи тарҷумаҳо аз файлҳои .po"""
        for lang in ['en', 'ru', 'tj']:
            po_file = LOCALES_DIR / lang / "bot.po"
            if po_file.exists():
                self.translations[lang] = self.parse_po_file(po_file)
            else:
                logging.warning(f"Translation file not found: {po_file}")
                self.translations[lang] = {}

    def parse_po_file(self, file_path: Path) -> dict:
        """Парси оддии файли .po"""
        translations = {}
        current_msgid = None
        current_msgstr = None
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('msgid '):
                    current_msgid = line[6:].strip('"')
                elif line.startswith('msgstr '):
                    current_msgstr = line[7:].strip('"')
                    if current_msgid is not None and current_msgstr is not None:
                        current_msgstr = current_msgstr.replace('\\n', '\n')
                        translations[current_msgid] = current_msgstr
                        current_msgid = None
                        current_msgstr = None
                elif line.startswith('"') and current_msgstr is not None:
                    additional = line.strip('"')
                    current_msgstr += additional
        
        return translations

    def get_text(self, key: str, locale: str = "en", **kwargs) -> str:
        """Гирифтани матн аз тарҷума"""
        if locale not in self.translations:
            locale = "en"
        
        text = self.translations.get(locale, {}).get(key)
        if text is None:
            text = self.translations.get("en", {}).get(key, key)
        
        for k, v in kwargs.items():
            text = text.replace(f"{{{k}}}", str(v))
        
        return text

i18n = TranslationManager()

# ---------- Тарҷумаи номҳои минтақаҳои вақтӣ ----------
def get_timezone_display_name(tz_key: str, locale: str) -> str:
    """Гирифтани номи минтақаи вақтӣ бо забони интихобшуда"""
    timezone_names = {
        "Asia/Dushanbe": {
            "tj": "🇹🇯 Тоҷикистон (UTC+5)",
            "ru": "🇹🇯 Таджикистан (UTC+5)",
            "en": "🇹🇯 Tajikistan (UTC+5)",
        },
        "Asia/Tashkent": {
            "tj": "🇺🇿 Ӯзбекистон (UTC+5)",
            "ru": "🇺🇿 Узбекистан (UTC+5)",
            "en": "🇺🇿 Uzbekistan (UTC+5)",
        },
        "Asia/Almaty": {
            "tj": "🇰🇿 Қазоқистон (UTC+5)",
            "ru": "🇰🇿 Казахстан (UTC+5)",
            "en": "🇰🇿 Kazakhstan (UTC+5)",
        },
        "Asia/Bishkek": {
            "tj": "🇰🇬 Қирғизистон (UTC+6)",
            "ru": "🇰🇬 Кыргызстан (UTC+6)",
            "en": "🇰🇬 Kyrgyzstan (UTC+6)",
        },
        "Asia/Ashgabat": {
            "tj": "🇹🇲 Туркманистон (UTC+5)",
            "ru": "🇹🇲 Туркменистан (UTC+5)",
            "en": "🇹🇲 Turkmenistan (UTC+5)",
        },
        "Asia/Kabul": {
            "tj": "🇦🇫 Афғонистон (UTC+4:30)",
            "ru": "🇦🇫 Афганистан (UTC+4:30)",
            "en": "🇦🇫 Afghanistan (UTC+4:30)",
        },
        "Europe/Moscow": {
            "tj": "🇷🇺 Маскав (UTC+3)",
            "ru": "🇷🇺 Москва (UTC+3)",
            "en": "🇷🇺 Moscow (UTC+3)",
        },
        "Europe/London": {
            "tj": "🇬🇧 Лондон (UTC+0)",
            "ru": "🇬🇧 Лондон (UTC+0)",
            "en": "🇬🇧 London (UTC+0)",
        },
        "America/New_York": {
            "tj": "🇺🇸 Ню-Йорк (UTC-5)",
            "ru": "🇺🇸 Нью-Йорк (UTC-5)",
            "en": "🇺🇸 New York (UTC-5)",
        },
        "Asia/Dubai": {
            "tj": "🇦🇪 Дубай (UTC+4)",
            "ru": "🇦🇪 Дубай (UTC+4)",
            "en": "🇦🇪 Dubai (UTC+4)",
        },
        "Asia/Tehran": {
            "tj": "🇮🇷 Теҳрон (UTC+3:30)",
            "ru": "🇮🇷 Тегеран (UTC+3:30)",
            "en": "🇮🇷 Tehran (UTC+3:30)",
        },
        "Europe/Berlin": {
            "tj": "🇩🇪 Берлин (UTC+1)",
            "ru": "🇩🇪 Берлин (UTC+1)",
            "en": "🇩🇪 Berlin (UTC+1)",
        },
        "Asia/Shanghai": {
            "tj": "🇨🇳 Хитой (UTC+8)",
            "ru": "🇨🇳 Китай (UTC+8)",
            "en": "🇨🇳 China (UTC+8)",
        },
        "Asia/Tokyo": {
            "tj": "🇯🇵 Ҷопон (UTC+9)",
            "ru": "🇯🇵 Япония (UTC+9)",
            "en": "🇯🇵 Japan (UTC+9)",
        },
        "Asia/Seoul": {
            "tj": "🇰🇷 Корея (UTC+9)",
            "ru": "🇰🇷 Корея (UTC+9)",
            "en": "🇰🇷 Korea (UTC+9)",
        },
        "Asia/Kolkata": {
            "tj": "🇮🇳 Ҳиндустон (UTC+5:30)",
            "ru": "🇮🇳 Индия (UTC+5:30)",
            "en": "🇮🇳 India (UTC+5:30)",
        },
        "Europe/Paris": {
            "tj": "🇫🇷 Фаронса (UTC+1)",
            "ru": "🇫🇷 Франция (UTC+1)",
            "en": "🇫🇷 France (UTC+1)",
        },
        "America/Chicago": {
            "tj": "🇺🇸 Чикаго (UTC-6)",
            "ru": "🇺🇸 Чикаго (UTC-6)",
            "en": "🇺🇸 Chicago (UTC-6)",
        },
        "America/Los_Angeles": {
            "tj": "🇺🇸 Лос-Анҷелес (UTC-8)",
            "ru": "🇺🇸 Лос-Анджелес (UTC-8)",
            "en": "🇺🇸 Los Angeles (UTC-8)",
        },
        "Pacific/Auckland": {
            "tj": "🇳🇿 Зеландияи Нав (UTC+12)",
            "ru": "🇳🇿 Новая Зеландия (UTC+12)",
            "en": "🇳🇿 New Zealand (UTC+12)",
        },
    }
    
    return timezone_names.get(tz_key, {}).get(locale, tz_key)

# Рӯйхати минтақаҳои вақтии дастрас (калидҳо)
AVAILABLE_TIMEZONES_KEYS = [
    "Asia/Dushanbe",
    "Asia/Tashkent",
    "Asia/Almaty",
    "Asia/Bishkek",
    "Asia/Ashgabat",
    "Asia/Kabul",
    "Europe/Moscow",
    "Europe/London",
    "America/New_York",
    "Asia/Dubai",
    "Asia/Tehran",
    "Europe/Berlin",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Kolkata",
    "Europe/Paris",
    "America/Chicago",
    "America/Los_Angeles",
    "Pacific/Auckland",
]

# ---------- Идораи танзимоти корбар ----------
def load_user_settings() -> dict:
    """Бор кардани танзимоти корбарон"""
    if USER_SETTINGS_FILE.exists():
        with open(USER_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_user_settings(settings: dict):
    """Захира кардани танзимоти корбарон"""
    with open(USER_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def get_user_settings_dict() -> dict:
    """Гирифтани танзимоти ҳамаи корбарон"""
    return load_user_settings()

def get_user_locale(user_id: int) -> str:
    """Гирифтани забони корбар"""
    settings = load_user_settings()
    user_key = str(user_id)
    if user_key in settings:
        return settings[user_key].get("locale", "en")
    return "en"

def set_user_locale(user_id: int, locale: str):
    """Танзим кардани забони корбар"""
    settings = load_user_settings()
    user_key = str(user_id)
    if user_key not in settings:
        settings[user_key] = {}
    settings[user_key]["locale"] = locale
    save_user_settings(settings)

def get_user_timezone(user_id: int) -> ZoneInfo:
    """Гирифтани минтақаи вақтии корбар"""
    settings = load_user_settings()
    user_key = str(user_id)
    if user_key in settings and "timezone" in settings[user_key]:
        try:
            return ZoneInfo(settings[user_key]["timezone"])
        except Exception:
            pass
    return DEFAULT_TZ

def set_user_timezone(user_id: int, timezone_str: str):
    """Танзим кардани минтақаи вақтии корбар"""
    settings = load_user_settings()
    user_key = str(user_id)
    if user_key not in settings:
        settings[user_key] = {}
    settings[user_key]["timezone"] = timezone_str
    save_user_settings(settings)

def has_user_timezone(user_id: int) -> bool:
    """Санҷидани он ки оё корбар минтақаи вақтӣ дорад"""
    settings = load_user_settings()
    user_key = str(user_id)
    return user_key in settings and "timezone" in settings[user_key]

def get_current_time_utc() -> datetime:
    """Гирифтани вақти ҷорӣ дар UTC"""
    return datetime.now(UTC)

def get_current_time_user(user_id: int) -> datetime:
    """Гирифтани вақти ҷорӣ дар минтақаи вақтии корбар"""
    return get_current_time_utc().astimezone(get_user_timezone(user_id))

def format_datetime_for_user(dt: datetime, user_id: int, format_str: str = '%d.%m.%Y, %H:%M:%S') -> str:
    """Формат кардани сана барои намоиш ба корбар"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(get_user_timezone(user_id)).strftime(format_str)

# ---------- Менюи доимӣ бо се забон ----------
def get_main_menu(locale: str = "en") -> ReplyKeyboardMarkup:
    menus = {
        "tj": [
            [KeyboardButton(text="🆕 Капсулаи нав"), KeyboardButton(text="📋 Листи капсулаҳо")],
            [KeyboardButton(text="🗑 Ҳазфи капсула"), KeyboardButton(text="ℹ️ Роҳнамо")],
            [KeyboardButton(text="🌍 Тағйири забон"), KeyboardButton(text="🕐 Минтақаи вақтӣ")],
        ],
        "ru": [
            [KeyboardButton(text="🆕 Новая капсула"), KeyboardButton(text="📋 Список капсул")],
            [KeyboardButton(text="🗑 Удалить капсулу"), KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="🌍 Сменить язык"), KeyboardButton(text="🕐 Часовой пояс")],
        ],
        "en": [
            [KeyboardButton(text="🆕 New Capsule"), KeyboardButton(text="📋 My Capsules")],
            [KeyboardButton(text="🗑 Delete Capsule"), KeyboardButton(text="ℹ️ Help")],
            [KeyboardButton(text="🌍 Change Language"), KeyboardButton(text="🕐 Timezone")],
        ],
    }
    
    return ReplyKeyboardMarkup(
        keyboard=menus.get(locale, menus["en"]),
        resize_keyboard=True,
        input_field_placeholder={
            "tj": "👆 Аз меню интихоб кунед...",
            "ru": "👆 Выберите из меню...",
            "en": "👆 Select from menu...",
        }.get(locale, "👆 Select from menu..."),
    )

def get_cancel_kb(locale: str = "en") -> ReplyKeyboardMarkup:
    cancel_texts = {
        "tj": "❌ Лағв кардан",
        "ru": "❌ Отмена",
        "en": "❌ Cancel",
    }
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=cancel_texts.get(locale, cancel_texts["en"]))]],
        resize_keyboard=True,
    )

def get_done_add_kb(locale: str = "en") -> ReplyKeyboardMarkup:
    texts = {
        "tj": ["✅ Тайёр! Фиристодан", "📎 Боз файл илова кардан", "❌ Лағв кардан"],
        "ru": ["✅ Готово! Отправить", "📎 Добавить ещё файл", "❌ Отмена"],
        "en": ["✅ Done! Send", "📎 Add more files", "❌ Cancel"],
    }
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t)] for t in texts.get(locale, texts["en"])],
        resize_keyboard=True,
    )

# ---------- Модели файл ----------
class CapsuleFile:
    def __init__(self, file_path: str, file_type: str, file_name: str = ""):
        self.file_path = file_path
        self.file_type = file_type
        self.file_name = file_name
    
    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "file_type": self.file_type,
            "file_name": self.file_name,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "CapsuleFile":
        return cls(
            file_path=data["file_path"],
            file_type=data["file_type"],
            file_name=data.get("file_name", ""),
        )

# ---------- Модели капсула ----------
class Capsule:
    def __init__(self, user_id: int, delivery_time: datetime, 
                 message_text: Optional[str] = None,
                 files: Optional[List[CapsuleFile]] = None):
        self.user_id = user_id
        self.delivery_time = delivery_time  # UTC
        self.message_text = message_text
        self.files = files or []
        self.created_at = get_current_time_utc().isoformat()  # UTC
        self.delivered = False

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "delivery_time": self.delivery_time.isoformat(),
            "message_text": self.message_text,
            "files": [f.to_dict() for f in self.files],
            "created_at": self.created_at,
            "delivered": self.delivered,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Capsule":
        delivery_time = datetime.fromisoformat(data["delivery_time"])
        if delivery_time.tzinfo is None:
            delivery_time = delivery_time.replace(tzinfo=UTC)
        return cls(
            user_id=data["user_id"],
            delivery_time=delivery_time,
            message_text=data.get("message_text"),
            files=[CapsuleFile.from_dict(f) for f in data.get("files", [])],
        )

# ---------- State машина ----------
class CapsuleStates(StatesGroup):
    waiting_for_language = State()
    waiting_for_timezone = State()
    waiting_for_time = State()
    waiting_for_content = State()

# ---------- Идораи JSON файл ----------
def load_capsules() -> List[dict]:
    if CAPSULES_FILE.exists():
        with open(CAPSULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_capsules(capsules: List[dict]):
    with open(CAPSULES_FILE, "w", encoding="utf-8") as f:
        json.dump(capsules, f, ensure_ascii=False, indent=2)

def get_user_capsules_count(user_id: int) -> int:
    capsules = load_capsules()
    return len([c for c in capsules if c["user_id"] == user_id and not c["delivered"]])

def is_time_taken(user_id: int, delivery_time: datetime) -> bool:
    """Санҷиши он ки оё ин вақт аллакай гирифта шудааст"""
    capsules = load_capsules()
    if delivery_time.tzinfo is None:
        delivery_time = delivery_time.replace(tzinfo=UTC)
    target_str = delivery_time.isoformat()
    for c in capsules:
        if c["user_id"] == user_id and not c.get("delivered"):
            if c["delivery_time"] == target_str:
                return True
    return False

# ---------- Тоза кардани капсулаҳои кӯҳна ----------
def schedule_capsule_delivery(capsule_data: dict):
    """Ба нақша гирифтани расонидани капсула"""
    delivery_time = datetime.fromisoformat(capsule_data["delivery_time"])
    if delivery_time.tzinfo is None:
        delivery_time = delivery_time.replace(tzinfo=UTC)
    
    now_utc = get_current_time_utc()
    
    if delivery_time > now_utc:
        scheduler.add_job(
            deliver_capsule,
            trigger=DateTrigger(run_date=delivery_time),
            args=[capsule_data],
            id=f"capsule_{capsule_data['user_id']}_{delivery_time.timestamp()}",
            misfire_grace_time=3600,
        )

async def deliver_capsule(capsule_data: dict):
    """Расонидани капсула ба корбар"""
    try:
        user_id = capsule_data["user_id"]
        locale = get_user_locale(user_id)
        
        created_at = datetime.fromisoformat(capsule_data['created_at'])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        created_date_str = format_datetime_for_user(created_at, user_id, '%d.%m.%Y')
        
        if capsule_data.get("message_text"):
            await bot.send_message(
                chat_id=user_id,
                text=i18n.get_text("capsule_opened", locale,
                    created_date=created_date_str,
                    message=capsule_data['message_text']),
                reply_markup=get_main_menu(locale),
            )
        
        files_sent = 0
        for file_data in capsule_data.get("files", []):
            file_path = Path(file_data["file_path"])
            if file_path.exists():
                try:
                    if file_data["file_type"] == "photo":
                        await bot.send_photo(
                            chat_id=user_id,
                            photo=FSInputFile(file_path),
                            caption=i18n.get_text("photo_from_past", locale) if files_sent == 0 else None,
                        )
                    elif file_data["file_type"] == "video":
                        await bot.send_video(
                            chat_id=user_id,
                            video=FSInputFile(file_path),
                            caption=i18n.get_text("video_from_past", locale) if files_sent == 0 else None,
                        )
                    elif file_data["file_type"] == "video_note":
                        await bot.send_video_note(
                            chat_id=user_id,
                            video_note=FSInputFile(file_path),
                        )
                    elif file_data["file_type"] == "audio":
                        await bot.send_audio(
                            chat_id=user_id,
                            audio=FSInputFile(file_path),
                            title=i18n.get_text("recording_from", locale, date=created_date_str),
                        )
                    elif file_data["file_type"] == "voice":
                        await bot.send_voice(
                            chat_id=user_id,
                            voice=FSInputFile(file_path),
                        )
                    else:
                        await bot.send_document(
                            chat_id=user_id,
                            document=FSInputFile(file_path),
                            caption=i18n.get_text("file_from_past", locale) if files_sent == 0 else None,
                        )
                    files_sent += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logging.error(f"Error sending file: {e}")
                
                file_path.unlink(missing_ok=True)
        
        if not capsule_data.get("message_text") and files_sent > 0:
            await bot.send_message(
                chat_id=user_id,
                text=i18n.get_text("files_opened", locale,
                    created_date=created_date_str,
                    files_count=files_sent),
                reply_markup=get_main_menu(locale),
            )
        
        capsules = load_capsules()
        for c in capsules:
            if (c["user_id"] == capsule_data["user_id"] and 
                c["delivery_time"] == capsule_data["delivery_time"] and
                c["created_at"] == capsule_data["created_at"]):
                c["delivered"] = True
                break
        save_capsules(capsules)
        
    except Exception as e:
        logging.error(f"Error delivering capsule: {e}")

def schedule_all_capsules():
    """Ба нақша гирифтани ҳамаи капсулаҳои расониданашуда"""
    capsules = load_capsules()
    for capsule in capsules:
        if not capsule.get("delivered", False):
            schedule_capsule_delivery(capsule)
    logging.info("All capsules scheduled")

# ---------- Handlers ----------
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Оғози бот - санҷиши танзимот"""
    user_id = message.from_user.id
    
    # Санҷидани он ки оё корбар аллакай забон ва минтақаи вақтӣ дорад
    has_locale = bool(get_user_locale(user_id) != "en" or 
                      str(user_id) in get_user_settings_dict())
    
    if not has_user_timezone(user_id) or not has_locale:
        # Агар танзимот набошад, аз аввал
        lang_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇹🇯 Тоҷикӣ", callback_data="lang:tj")],
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru")],
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")],
        ])
        
        await message.answer(
            "🌍 Please choose your language / Лутфан забони худро интихоб кунед / Пожалуйста, выберите язык:",
            reply_markup=lang_keyboard,
        )
        await state.set_state(CapsuleStates.waiting_for_language)
    else:
        # Агар ҳама чиз танзим шуда бошад, менюи асосиро нишон диҳад
        locale = get_user_locale(user_id)
        menu = get_main_menu(locale)
        await message.answer(
            i18n.get_text("welcome_back", locale),
            reply_markup=menu,
        )

@router.callback_query(CapsuleStates.waiting_for_language, F.data.startswith("lang:"))
async def process_language_selection(callback: CallbackQuery, state: FSMContext):
    """Пас аз интихоби забон, пешниҳоди интихоби минтақаи вақтӣ"""
    locale = callback.data.split(":")[1]
    user_id = callback.from_user.id
    
    # Захира кардани забон
    set_user_locale(user_id, locale)
    
    await callback.answer()
    
    # Акнун интихоби минтақаи вақтӣ
    await show_timezone_selection(callback.message, state, locale)

async def show_timezone_selection(message: Message, state: FSMContext, locale: str):
    """Намоиши рӯйхати минтақаҳои вақтӣ"""
    tz_buttons = []
    for tz_key in AVAILABLE_TIMEZONES_KEYS:
        tz_name = get_timezone_display_name(tz_key, locale)
        tz_buttons.append([InlineKeyboardButton(
            text=tz_name,
            callback_data=f"tz:{tz_key}"
        )])
    
    # Тугмаи гузаштан (танзими пешфарз)
    skip_text = {
        "tj": "⏩ Гузариш (Тоҷикистон UTC+5)",
        "ru": "⏩ Пропустить (Таджикистан UTC+5)",
        "en": "⏩ Skip (Tajikistan UTC+5)",
    }
    
    tz_buttons.append([InlineKeyboardButton(
        text=skip_text.get(locale, skip_text["en"]),
        callback_data="tz:Asia/Dushanbe"
    )])
    
    tz_keyboard = InlineKeyboardMarkup(inline_keyboard=tz_buttons)
    
    timezone_prompt = {
        "tj": (
            "🕐 <b>Лутфан минтақаи вақтии худро интихоб кунед:</b>\n\n"
            "Шумо метавонед баъдтар аз менюи асосӣ минтақаи вақтиро тағйир диҳед."
        ),
        "ru": (
            "🕐 <b>Пожалуйста, выберите ваш часовой пояс:</b>\n\n"
            "Вы сможете изменить часовой пояс позже из главного меню."
        ),
        "en": (
            "🕐 <b>Please select your timezone:</b>\n\n"
            "You can change your timezone later from the main menu."
        ),
    }
    
    await message.answer(
        timezone_prompt.get(locale, timezone_prompt["en"]),
        reply_markup=tz_keyboard,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(CapsuleStates.waiting_for_timezone)

@router.message(F.text.in_(["🌍 Тағйири забон", "🌍 Сменить язык", "🌍 Change Language"]))
async def change_language(message: Message, state: FSMContext):
    """Тағйир додани забон - бе пурсиши минтақаи вақтӣ"""
    user_id = message.from_user.id
    locale = get_user_locale(user_id)
    
    lang_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇹🇯 Тоҷикӣ", callback_data="change_lang:tj")],
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="change_lang:ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="change_lang:en")],
    ])
    
    change_lang_prompt = {
        "tj": "🌍 Лутфан забони навро интихоб кунед:",
        "ru": "🌍 Пожалуйста, выберите новый язык:",
        "en": "🌍 Please choose a new language:",
    }
    
    await message.answer(
        change_lang_prompt.get(locale, change_lang_prompt["en"]),
        reply_markup=lang_keyboard,
    )

@router.callback_query(F.data.startswith("change_lang:"))
async def process_change_language(callback: CallbackQuery):
    """Пас аз тағйири забон - танҳо забонро иваз мекунад"""
    locale = callback.data.split(":")[1]
    user_id = callback.from_user.id
    
    # Танҳо забонро иваз мекунем
    set_user_locale(user_id, locale)
    
    await callback.answer()
    
    menu = get_main_menu(locale)
    
    lang_changed = {
        "tj": "✅ Забон ба тоҷикӣ иваз шуд!",
        "ru": "✅ Язык изменён на русский!",
        "en": "✅ Language changed to English!",
    }
    
    await callback.message.edit_text(lang_changed.get(locale, lang_changed["en"]))
    await bot.send_message(
        chat_id=user_id,
        text=i18n.get_text("main_menu_returned", locale),
        reply_markup=menu,
    )

@router.message(F.text.in_(["🕐 Минтақаи вақтӣ", "🕐 Часовой пояс", "🕐 Timezone"]))
async def change_timezone(message: Message, state: FSMContext):
    """Тағйир додани минтақаи вақтӣ"""
    locale = get_user_locale(message.from_user.id)
    await show_timezone_selection(message, state, locale)

@router.callback_query(CapsuleStates.waiting_for_timezone, F.data.startswith("tz:"))
async def process_timezone_selection(callback: CallbackQuery, state: FSMContext):
    """Пас аз интихоби минтақаи вақтӣ"""
    tz_key = callback.data.split(":")[1]
    user_id = callback.from_user.id
    locale = get_user_locale(user_id)
    
    await callback.answer()
    
    # Интихоби дастӣ
    set_user_timezone(user_id, tz_key)
    
    menu = get_main_menu(locale)
    
    tz_display = get_timezone_display_name(tz_key, locale)
    
    tz_set = {
        "tj": f"✅ Минтақаи вақтии шумо танзим шуд: {tz_display}",
        "ru": f"✅ Ваш часовой пояс установлен: {tz_display}",
        "en": f"✅ Your timezone has been set: {tz_display}",
    }
    
    await callback.message.edit_text(tz_set.get(locale, tz_set["en"]))
    await bot.send_message(
        chat_id=user_id,
        text=i18n.get_text("start", locale),
        reply_markup=menu,
    )
    await state.clear()

@router.message(F.text.in_(["ℹ️ Роҳнамо", "ℹ️ Помощь", "ℹ️ Help"]))
@router.message(Command("help"))
async def cmd_help(message: Message):
    locale = get_user_locale(message.from_user.id)
    menu = get_main_menu(locale)
    help_text = i18n.get_text("help_text", locale)
    await message.answer(help_text, reply_markup=menu)

@router.message(F.text.in_(["🆕 Капсулаи нав", "🆕 Новая капсула", "🆕 New Capsule"]))
@router.message(Command("new"))
async def cmd_new_capsule(message: Message, state: FSMContext):
    user_id = message.from_user.id
    locale = get_user_locale(user_id)
    count = get_user_capsules_count(user_id)
    
    if count >= MAX_CAPSULES:
        await message.answer(
            i18n.get_text("max_capsules_error", locale, count=MAX_CAPSULES),
            reply_markup=get_main_menu(locale),
        )
        return
    
    await message.answer(
        i18n.get_text("enter_time", locale, count=count, max_count=MAX_CAPSULES),
        reply_markup=get_cancel_kb(locale),
    )
    await state.set_state(CapsuleStates.waiting_for_time)

@router.message(F.text.in_(["❌ Лағв кардан", "❌ Отмена", "❌ Cancel"]))
async def cancel_action(message: Message, state: FSMContext):
    locale = get_user_locale(message.from_user.id)
    await state.clear()
    await message.answer(i18n.get_text("operation_cancelled", locale), reply_markup=get_main_menu(locale))

@router.message(CapsuleStates.waiting_for_time)
async def process_time(message: Message, state: FSMContext):
    user_id = message.from_user.id
    locale = get_user_locale(user_id)
    user_tz = get_user_timezone(user_id)
    
    if message.text in ["❌ Лағв кардан", "❌ Отмена", "❌ Cancel"]:
        await cancel_action(message, state)
        return
    
    try:
        delivery_time_local = parse_time(message.text.strip().lower(), locale, user_id)
        delivery_time_utc = delivery_time_local.astimezone(UTC)
        
        now_utc = get_current_time_utc()
        
        if delivery_time_utc <= now_utc:
            await message.answer(i18n.get_text("future_time_error", locale))
            return
        
        if delivery_time_utc > now_utc + timedelta(days=365 * 3):
            await message.answer(i18n.get_text("max_time_error", locale))
            return
        
        if is_time_taken(user_id, delivery_time_utc):
            time_str = format_datetime_for_user(delivery_time_utc, user_id)
            time_plus_1s = format_datetime_for_user(delivery_time_utc + timedelta(seconds=1), user_id)
            time_plus_1m = format_datetime_for_user(delivery_time_utc + timedelta(minutes=1), user_id)
            
            await message.answer(
                i18n.get_text("taken_time_error", locale,
                    time=time_str,
                    time_plus_1s=time_plus_1s,
                    time_plus_1m=time_plus_1m)
            )
            return
        
        await state.update_data(delivery_time=delivery_time_utc, files=[], message_text=None)
        
        time_display = format_datetime_for_user(delivery_time_utc, user_id)
        await message.answer(
            i18n.get_text("time_set", locale, time=time_display),
            reply_markup=get_done_add_kb(locale),
        )
        await state.set_state(CapsuleStates.waiting_for_content)
        
    except ValueError as e:
        await message.answer(i18n.get_text("time_format_error", locale, error=str(e)))

@router.message(CapsuleStates.waiting_for_content, F.text.in_([
    "📎 Боз файл илова кардан", "📎 Добавить ещё файл", "📎 Add more files"
]))
async def add_more_files(message: Message, state: FSMContext):
    locale = get_user_locale(message.from_user.id)
    data = await state.get_data()
    files_count = len(data.get("files", []))
    
    if files_count >= MAX_FILES_PER_CAPSULE:
        await message.answer(
            i18n.get_text("max_files_error", locale, max_files=MAX_FILES_PER_CAPSULE),
        )
        return
    
    total_size = get_total_files_size(data.get("files", []))
    await message.answer(
        i18n.get_text("send_next_file", locale, 
            current=files_count + 1, 
            max_files=MAX_FILES_PER_CAPSULE,
            current_size=format_size(total_size),
            max_size=format_size(MAX_CAPSULE_SIZE_BYTES)),
        reply_markup=get_done_add_kb(locale),
    )

@router.message(CapsuleStates.waiting_for_content, F.text.in_([
    "✅ Тайёр! Фиристодан", "✅ Готово! Отправить", "✅ Done! Send"
]))
async def finish_capsule(message: Message, state: FSMContext):
    user_id = message.from_user.id
    locale = get_user_locale(user_id)
    data = await state.get_data()
    delivery_time = data["delivery_time"]  # UTC
    message_text = data.get("message_text")
    files = data.get("files", [])
    
    if not message_text and not files:
        await message.answer(i18n.get_text("empty_capsule_error", locale))
        return
    
    total_size = get_total_files_size(files)
    if total_size > MAX_CAPSULE_SIZE_BYTES:
        await message.answer(
            i18n.get_text("size_limit_final_error", locale,
                total_size=format_size(total_size),
                max_size=format_size(MAX_CAPSULE_SIZE_BYTES),
                excess=format_size(total_size - MAX_CAPSULE_SIZE_BYTES)),
            reply_markup=get_done_add_kb(locale),
        )
        return
    
    capsule = Capsule(
        user_id=user_id,
        delivery_time=delivery_time,
        message_text=message_text,
        files=files,
    )
    
    capsules = load_capsules()
    capsules.append(capsule.to_dict())
    save_capsules(capsules)
    
    schedule_capsule_delivery(capsule.to_dict())
    
    parts = []
    if message_text:
        parts.append(i18n.get_text("text_type", locale))
    if files:
        file_types = {
            "photo": i18n.get_text("photo_type", locale),
            "video": i18n.get_text("video_type", locale),
            "video_note": i18n.get_text("video_note_type", locale),
            "audio": i18n.get_text("audio_type", locale),
            "voice": i18n.get_text("voice_type", locale),
            "document": i18n.get_text("document_type", locale),
        }
        for f in files:
            parts.append(file_types.get(f.file_type, i18n.get_text("document_type", locale)))
    
    content_type = " + ".join(parts)
    
    delivery_time_display = format_datetime_for_user(delivery_time, user_id)
    created_at_display = format_datetime_for_user(get_current_time_utc(), user_id)
    
    await message.answer(
        i18n.get_text("capsule_created", locale,
            content_type=content_type,
            files_count=len(files),
            delivery_time=delivery_time_display,
            created_at=created_at_display,
            total_size=format_size(total_size)),
        reply_markup=get_main_menu(locale),
    )
    
    await state.clear()

@router.message(CapsuleStates.waiting_for_content)
async def process_content(message: Message, state: FSMContext):
    user_id = message.from_user.id
    locale = get_user_locale(user_id)
    
    if message.text in ["❌ Лағв кардан", "❌ Отмена", "❌ Cancel"]:
        await cancel_action(message, state)
        return
    
    data = await state.get_data()
    files: List[CapsuleFile] = data.get("files", [])
    
    if len(files) >= MAX_FILES_PER_CAPSULE:
        await message.answer(
            i18n.get_text("max_files_error", locale, max_files=MAX_FILES_PER_CAPSULE),
            reply_markup=get_done_add_kb(locale),
        )
        return
    
    new_file = await create_capsule_file(message)
    
    if new_file:
        new_file_path = Path(new_file.file_path)
        if new_file_path.exists():
            new_file_size = new_file_path.stat().st_size
            current_total_size = get_total_files_size(files)
            total_after_add = current_total_size + new_file_size
            
            if total_after_add > MAX_CAPSULE_SIZE_BYTES:
                new_file_path.unlink(missing_ok=True)
                
                remaining = MAX_CAPSULE_SIZE_BYTES - current_total_size
                await message.answer(
                    i18n.get_text("size_limit_exceeded", locale,
                        max_size=format_size(MAX_CAPSULE_SIZE_BYTES),
                        current_size=format_size(current_total_size),
                        remaining=format_size(remaining),
                        file_size=format_size(new_file_size)),
                    reply_markup=get_done_add_kb(locale),
                )
                return
        
        files.append(new_file)
        await state.update_data(files=files)
        
        file_emoji = {
            "photo": "🖼",
            "video": "🎬",
            "video_note": "🎥",
            "audio": "🎵",
            "voice": "🎤",
            "document": "📁",
        }.get(new_file.file_type, "📁")
        
        total_size = get_total_files_size(files)
        await message.answer(
            f"{file_emoji} " + i18n.get_text("file_added", locale, 
                current=len(files), 
                max_files=MAX_FILES_PER_CAPSULE,
                total_size=format_size(total_size),
                max_size=format_size(MAX_CAPSULE_SIZE_BYTES)),
            reply_markup=get_done_add_kb(locale),
        )
    
    elif message.text and not any([message.photo, message.video, message.video_note,
                                     message.audio, message.voice, message.document]):
        if data.get("message_text"):
            await message.answer(i18n.get_text("text_updated", locale))
        await state.update_data(message_text=message.text)
        
        if not files:
            await message.answer(
                i18n.get_text("text_saved", locale),
                reply_markup=get_done_add_kb(locale),
            )

async def create_capsule_file(message: Message) -> Optional[CapsuleFile]:
    user_id = message.from_user.id
    timestamp = datetime.now().timestamp()
    
    try:
        if message.photo:
            file_id = message.photo[-1].file_id
            file = await bot.get_file(file_id)
            file_path = str(UPLOADS_DIR / f"{user_id}_{timestamp}_photo.jpg")
            await bot.download_file(file.file_path, file_path)
            return CapsuleFile(file_path=file_path, file_type="photo", file_name="photo.jpg")
        
        elif message.video:
            file_id = message.video.file_id
            file = await bot.get_file(file_id)
            ext = message.video.file_name.split(".")[-1] if message.video.file_name else "mp4"
            file_path = str(UPLOADS_DIR / f"{user_id}_{timestamp}_video.{ext}")
            await bot.download_file(file.file_path, file_path)
            return CapsuleFile(file_path=file_path, file_type="video", file_name=message.video.file_name or f"video.{ext}")
        
        elif message.video_note:
            file_id = message.video_note.file_id
            file = await bot.get_file(file_id)
            file_path = str(UPLOADS_DIR / f"{user_id}_{timestamp}_videonote.mp4")
            await bot.download_file(file.file_path, file_path)
            return CapsuleFile(file_path=file_path, file_type="video_note", file_name="video_note.mp4")
        
        elif message.audio:
            file_id = message.audio.file_id
            file = await bot.get_file(file_id)
            file_path = str(UPLOADS_DIR / f"{user_id}_{timestamp}_audio.mp3")
            await bot.download_file(file.file_path, file_path)
            return CapsuleFile(file_path=file_path, file_type="audio", file_name=message.audio.file_name or "audio.mp3")
        
        elif message.voice:
            file_id = message.voice.file_id
            file = await bot.get_file(file_id)
            file_path = str(UPLOADS_DIR / f"{user_id}_{timestamp}_voice.ogg")
            await bot.download_file(file.file_path, file_path)
            return CapsuleFile(file_path=file_path, file_type="voice", file_name="voice.ogg")
        
        elif message.document:
            file_id = message.document.file_id
            file = await bot.get_file(file_id)
            safe_name = message.document.file_name or "document"
            file_path = str(UPLOADS_DIR / f"{user_id}_{timestamp}_{safe_name}")
            await bot.download_file(file.file_path, file_path)
            return CapsuleFile(file_path=file_path, file_type="document", file_name=safe_name)
    
    except Exception as e:
        logging.error(f"Error downloading file: {e}")
        return None
    
    return None

# ---------- Командаҳои дигар ----------
@router.message(F.text.in_(["📋 Листи капсулаҳо", "📋 Список капсул", "📋 My Capsules"]))
@router.message(Command("list"))
async def cmd_list_capsules(message: Message):
    user_id = message.from_user.id
    locale = get_user_locale(user_id)
    capsules = load_capsules()
    my_capsules = [c for c in capsules if c["user_id"] == user_id]
    
    if not my_capsules:
        await message.answer(i18n.get_text("list_empty", locale), reply_markup=get_main_menu(locale))
        return
    
    text = i18n.get_text("your_capsules", locale) + "\n\n"
    now_utc = get_current_time_utc()
    
    for i, c in enumerate(my_capsules, 1):
        status = i18n.get_text("status_opened", locale) if c.get("delivered") else i18n.get_text("status_closed", locale)
        delivery_time = datetime.fromisoformat(c["delivery_time"])
        if delivery_time.tzinfo is None:
            delivery_time = delivery_time.replace(tzinfo=UTC)
        
        delivery_display = format_datetime_for_user(delivery_time, user_id, '%d.%m.%Y, %H:%M')
        
        has_text = bool(c.get("message_text"))
        files_count = len(c.get("files", []))
        
        if has_text and files_count > 0:
            content_type = i18n.get_text("content_text_files", locale, count=files_count)
        elif has_text:
            content_type = i18n.get_text("content_text", locale)
        elif files_count > 0:
            content_type = i18n.get_text("content_files", locale, count=files_count)
        else:
            content_type = i18n.get_text("content_empty", locale)
        
        text += f"{i}. {status} | {content_type}\n"
        text += f"   ⏰ {delivery_display}\n"
        if not c.get("delivered"):
            time_left = delivery_time - now_utc
            if time_left.total_seconds() > 0:
                days = time_left.days
                hours = time_left.seconds // 3600
                minutes = (time_left.seconds % 3600) // 60
                text += f"   ⏳ " + i18n.get_text("time_remaining", locale, 
                    days=days, hours=hours, minutes=minutes) + "\n"
        text += "\n"
    
    await message.answer(text, reply_markup=get_main_menu(locale))

@router.message(F.text.in_(["🗑 Ҳазфи капсула", "🗑 Удалить капсулу", "🗑 Delete Capsule"]))
@router.message(Command("delete"))
async def cmd_delete_capsule(message: Message):
    user_id = message.from_user.id
    locale = get_user_locale(user_id)
    capsules = load_capsules()
    my_capsules = [(i, c) for i, c in enumerate(capsules) 
                   if c["user_id"] == user_id and not c.get("delivered")]
    
    if not my_capsules:
        await message.answer(i18n.get_text("no_unopened_capsules", locale), reply_markup=get_main_menu(locale))
        return
    
    buttons = []
    for idx, (_, c) in enumerate(my_capsules[:10], 1):
        delivery_time = datetime.fromisoformat(c["delivery_time"])
        if delivery_time.tzinfo is None:
            delivery_time = delivery_time.replace(tzinfo=UTC)
        
        delivery_display = format_datetime_for_user(delivery_time, user_id, '%d.%m.%y %H:%M')
        
        files_count = len(c.get("files", []))
        has_text = bool(c.get("message_text"))
        info = ""
        if has_text:
            info += "📝"
        if files_count > 0:
            info += f"📎{files_count}"
        
        btn_text = f"{idx}. {delivery_display} {info}"
        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"del:{my_capsules[idx-1][0]}"
        )])
    
    buttons.append([InlineKeyboardButton(
        text=i18n.get_text("cancel_delete", locale),
        callback_data="del:cancel"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        i18n.get_text("delete_confirm", locale),
        reply_markup=keyboard,
    )

@router.callback_query(F.data.startswith("del:"))
async def process_delete(callback: CallbackQuery):
    locale = get_user_locale(callback.from_user.id)
    await callback.answer()
    
    if callback.data == "del:cancel":
        await callback.message.delete()
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=i18n.get_text("delete_cancelled", locale),
            reply_markup=get_main_menu(locale),
        )
        return
    
    capsule_index = int(callback.data.split(":")[1])
    
    capsules = load_capsules()
    if capsule_index < len(capsules):
        capsule = capsules[capsule_index]
        
        for file_data in capsule.get("files", []):
            if file_data.get("file_path"):
                Path(file_data["file_path"]).unlink(missing_ok=True)
        
        capsules.pop(capsule_index)
        save_capsules(capsules)
        
        await callback.message.edit_text(i18n.get_text("delete_success", locale))
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=i18n.get_text("main_menu_returned", locale),
            reply_markup=get_main_menu(locale),
        )
    else:
        await callback.message.edit_text(i18n.get_text("capsule_not_found", locale))

# ---------- Парси вақт ----------
def parse_time(time_str: str, locale: str = "en", user_id: int = None) -> datetime:
    """
    Парси вақти воридкардаи корбар.
    Вақти бозгаштӣ дар минтақаи вақтии корбар аст.
    """
    time_str = time_str.strip()
    
    try:
        if "-" in time_str and ":" in time_str:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
            if user_id:
                return dt.replace(tzinfo=get_user_timezone(user_id))
            return dt.replace(tzinfo=DEFAULT_TZ)
    except ValueError:
        pass
    
    try:
        if "-" in time_str and len(time_str) == 10:
            dt = datetime.strptime(time_str, "%Y-%m-%d")
            if user_id:
                return dt.replace(tzinfo=get_user_timezone(user_id))
            return dt.replace(tzinfo=DEFAULT_TZ)
    except ValueError:
        pass
    
    if user_id:
        now = get_current_time_user(user_id)
    else:
        now = get_current_time_utc().astimezone(DEFAULT_TZ)
    
    parts = time_str.split()
    
    if len(parts) % 2 != 0:
        raise ValueError(i18n.get_text("time_format_help", locale))
    
    delta_kwargs = {}
    valid_units = {
        "сол": "days", "соли": "days",
        "моҳ": "days", "моҳи": "days",
        "ҳафта": "weeks",
        "рӯз": "days", "рӯзи": "days",
        "соат": "hours",
        "дақиқа": "minutes",
        "сония": "seconds", "соня": "seconds",
        "year": "days", "years": "days",
        "month": "days", "months": "days",
        "week": "weeks", "weeks": "weeks",
        "day": "days", "days": "days",
        "hour": "hours", "hours": "hours",
        "minute": "minutes", "minutes": "minutes",
        "second": "seconds", "seconds": "seconds",
        "год": "days", "года": "days", "лет": "days",
        "месяц": "days", "месяца": "days", "месяцев": "days",
        "неделя": "weeks", "недели": "weeks", "недель": "weeks",
        "день": "days", "дня": "days", "дней": "days",
        "час": "hours", "часа": "hours", "часов": "hours",
        "минута": "minutes", "минуты": "minutes", "минут": "minutes",
        "секунда": "seconds", "секунды": "seconds", "секунд": "seconds",
    }
    
    for i in range(0, len(parts), 2):
        try:
            amount = float(parts[i])
        except ValueError:
            raise ValueError(i18n.get_text("invalid_number", locale, number=parts[i]))
        
        unit = parts[i+1].lower()
        if unit not in valid_units:
            raise ValueError(i18n.get_text("invalid_unit", locale, unit=unit))
        
        key = valid_units[unit]
        
        if unit in ["сол", "соли", "year", "years", "год", "года", "лет"]:
            amount *= 365
            key = "days"
        elif unit in ["моҳ", "моҳи", "month", "months", "месяц", "месяца", "месяцев"]:
            amount *= 30
            key = "days"
        
        delta_kwargs[key] = delta_kwargs.get(key, 0) + amount
    
    return now + timedelta(**delta_kwargs)

# ---------- Оғоз ----------
async def main():
    scheduler.start()
    schedule_all_capsules()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())