import telebot
from telebot import types
import subprocess
import os
import sys
import psutil
import json
import time
import zipfile
import shutil

# ----------------- ENVIRONMENT VARIABLES -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8605535414:AAGQvmgH2Xgj0ahD_7zCIAmacn9MqkiUHBo")
OWNER_ID_ENV = os.getenv("OWNER_ID", "7609375248")

if not BOT_TOKEN: sys.exit("[CRITICAL ERROR] 'BOT_TOKEN' is missing!")
if not OWNER_ID_ENV: sys.exit("[CRITICAL ERROR] 'OWNER_ID' is missing!")

try: OWNER_ID = int(OWNER_ID_ENV)
except ValueError: sys.exit("[CRITICAL ERROR] 'OWNER_ID' must be an integer!")

bot = telebot.TeleBot(BOT_TOKEN)

CONFIG_FILE = "bot_config.json"
HOSTED_BOTS_DIR = "uploaded_bots"
MAX_CHANNELS = 10

os.makedirs(HOSTED_BOTS_DIR, exist_ok=True)

bg_processes = {}
waiting_for_channel_forward = set()
START_TIME = time.time()

# ----------------- STORAGE HELPERS -----------------
def load_config():
    default_config = {"required_channels": []}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f: return json.load(f)
        except: return default_config
    return default_config

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f: json.dump(cfg, f, indent=4)

bot_config = load_config()

# ----------------- VERIFICATION HELPERS -----------------
def is_owner(user_id): return user_id == OWNER_ID

def check_channel_subscriptions(user_id):
    if is_owner(user_id): return True, []
    channels = bot_config.get("required_channels", [])
    if not channels: return True, []
    missing = []
    for ch in channels:
        try:
            member = bot.get_chat_member(ch.get("id"), user_id)
            if member.status not in ['member', 'administrator', 'creator']: missing.append(ch)
        except: missing.append(ch)
    return (len(missing) == 0), missing

def get_join_channels_keyboard(missing_channels):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for ch in missing_channels:
        if ch.get("invite_link"): markup.add(types.InlineKeyboardButton(f"📢 Join {ch.get('title')}", url=ch["invite_link"]))
    markup.add(types.InlineKeyboardButton("🔄 Verify Membership", callback_data="verify_membership"))
    return markup

def get_uptime():
    uptime_seconds = int(time.time() - START_TIME)
    return f"{uptime_seconds // 86400}d {(uptime_seconds % 86400) // 3600}h {(uptime_seconds % 3600) // 60}m"

# ----------------- UI / KEYBOARDS -----------------
def get_main_menu_keyboard(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🟢 Hosted Bots", callback_data="btn_hosted"),
        types.InlineKeyboardButton("🧬 Vitals", callback_data="btn_sysinfo"),
        types.InlineKeyboardButton("🪪 My ID", callback_data="btn_myid")
    )
    if is_owner(user_id):
        markup.add(types.InlineKeyboardButton("📢 Channel Manager", callback_data="btn_channel_info"))
    return markup

def get_back_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_main_menu"))
    return markup

def get_help_text():
    return f"""⚡️ *ASH X BOT HOSTING* ⚡️
*═════════════════════════*
Welcome to the Cloud Panel. 🚀

📁 *HOW TO HOST YOUR BOT:*
▪️ Send your bot file directly to this chat (`.py`, `.js`, `.zip`).
▪️ The bot will extract, save, and **AUTO-START** it instantly.
▪️ Type `/hosts` to see your uploaded bots & their status.
▪️ Type `/logs <filename>` to see why a bot crashed.

⚙️ *SYSTEM VITALS:*
▪️ `/status` - Live uptime & overview ⏳
▪️ `/sysinfo` - CPU & RAM utilization 🧬

🔑 *ACCESS:*
▪️ `/myid` - View your Telegram User ID 🪪

🔒 *Active Channels:* `{len(bot_config.get('required_channels', []))} / {MAX_CHANNELS}`
*═════════════════════════*
*SYSTEM READY >_*"""

# ----------------- CHANNEL MANAGER -----------------
@bot.message_handler(commands=['channel'])
def set_channel_prompt(message):
    if not is_owner(message.from_user.id): return
    waiting_for_channel_forward.add(message.from_user.id)
    bot.reply_to(message, "📢 Forward any message from your channel to add it.", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_channel_setup")))

@bot.message_handler(func=lambda msg: msg.from_user.id in waiting_for_channel_forward and msg.forward_from_chat is not None)
def handle_channel_forward(message):
    chat = message.forward_from_chat
    waiting_for_channel_forward.discard(message.from_user.id)
    if chat.type == 'channel':
        channels = bot_config.get("required_channels", [])
        if not any(c.get("id") == chat.id for c in channels):
            channels.append({"id": chat.id, "title": chat.title, "invite_link": f"https://t.me/{chat.username}" if chat.username else None})
            bot_config["required_channels"] = channels
            save_config(bot_config)
            bot.reply_to(message, f"✅ Channel `{chat.title}` added!")

# ----------------- GENERAL COMMANDS -----------------
@bot.message_handler(commands=['start', 'help', 'menu'])
def send_menu(message):
    user_id = message.from_user.id
    is_all_joined, missing = check_channel_subscriptions(user_id)
    if not is_all_joined:
        bot.reply_to(message, f"⚠️ Join `{len(missing)}` channels first.", reply_markup=get_join_channels_keyboard(missing))
        return
    bot.reply_to(message, get_help_text(), parse_mode="Markdown", reply_markup=get_main_menu_keyboard(user_id))

@bot.message_handler(commands=['myid'])
def my_id(message):
    bot.reply_to(message, f"🪪 *YOUR ID:* `{message.from_user.id}`", parse_mode="Markdown")

@bot.message_handler(commands=['sysinfo'])
def sys_info(message):
    bot.reply_to(message, f"🧬 *CPU:* `{psutil.cpu_percent()}%` | *RAM:* `{psutil.virtual_memory().percent}%`", parse_mode="Markdown")

# ----------------- BOT HOSTING ENGINE (AUTO-START & LOGS) -----------------
def start_process(filepath, filename):
    cmd = ""
    if filename.endswith(".py"): cmd = f"python3 {filepath}"
    elif filename.endswith(".js"): cmd = f"node {filepath}"
    elif filename.endswith(".sh"): cmd = f"bash {filepath}"
    
    if cmd:
        try:
            # I-redirect ang output sa log file para makita kung bakit nag-crash
            log_file = os.path.join(HOSTED_BOTS_DIR, f"{filename}.log")
            with open(log_file, "w") as f:
                # Popen na tumatakbo sa background, naka-save ang output sa log
                proc = subprocess.Popen(
                    cmd, shell=True, 
                    stdout=f, 
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid # Para kapag kinill, pati anak na process mamatay
                )
            bg_processes[proc.pid] = {'process': proc, 'file': filename, 'cmd': cmd, 'log': log_file}
            return True, proc.pid
        except Exception as e:
            return False, str(e)
    return False, "Unsupported file type"

@bot.message_handler(content_types=['document'])
def handle_upload(message):
    user_id = message.from_user.id
    is_all_joined, missing = check_channel_subscriptions(user_id)
    if not is_all_joined:
        bot.reply_to(message, "⛔️ You must join the required channels to use this.", reply_markup=get_join_channels_keyboard(missing))
        return

    try:
        bot.send_chat_action(message.chat.id, 'upload_document')
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        filename = message.document.file_name
        filepath = os.path.join(HOSTED_BOTS_DIR, filename)
        
        with open(filepath, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        size_kb = os.path.getsize(filepath) / 1024
        extract_msg = ""
        run_msg = ""
        target_file = filepath
        target_name = filename
        
        # Auto-extract ZIP files
        if filename.endswith(".zip"):
            try:
                extract_folder = os.path.join(HOSTED_BOTS_DIR, filename.replace(".zip", ""))
                with zipfile.ZipFile(filepath, 'r') as zip_ref:
                    zip_ref.extractall(extract_folder)
                extract_msg = f"\n📦 *Extracted:* `{filename.replace('.zip', '')}/`"
                
                # Hanapin yung unang .py or .js file sa loob ng zip
                found_main = False
                for root, dirs, files in os.walk(extract_folder):
                    for f in files:
                        if f.endswith('.py') or f.endswith('.js'):
                            target_file = os.path.join(root, f)
                            target_name = f
                            found_main = True
                            break
                    if found_main: break
                
                # Kung may requirements.txt, i-install
                req_file = os.path.join(extract_folder, "requirements.txt")
                if os.path.exists(req_file):
                    extract_msg += "\n⏳ *Installing requirements...*"
                    os.system(f"pip install -r {req_file} > /dev/null 2>&1")
                    extract_msg += " (Done)"
                    
                if found_main:
                    success, res = start_process(target_file, target_name)
                    if success: run_msg = f"\n🟢 *Auto-Started:* `{target_name}` (PID: `{res}`)"
                    else: run_msg = f"\n⚠️ *Start Failed:* {res}"
                else:
                    run_msg = "\n⚠️ No .py or .js found inside zip."
                    
            except Exception as e:
                extract_msg = f"\n⚠️ Extract error: {e}"
        else:
            # Auto-start kapag direct upload
            success, res = start_process(filepath, filename)
            if success: run_msg = f"\n🟢 *Auto-Started:* (PID: `{res}`)"
            else: run_msg = f"\n⚠️ *Start Failed:* {res}"

        text = (
            f"✅ *[BOT UPLOADED SUCCESSFULLY]*\n\n"
            f"📄 *Filename:* `{filename}`\n"
            f"📦 *Size:* `{size_kb:.1f} KB`{extract_msg}{run_msg}\n\n"
            f"Use `/hosts` to monitor status.\nType `/logs {target_name}` if it stopped."
        )
        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ *Upload Failed:* {e}")

@bot.message_handler(commands=['logs'])
def get_logs(message):
    args = message.text.split(" ", 1)
    if len(args) < 2: 
        bot.reply_to(message, "⚠️ Usage: `/logs <filename>`")
        return
    filename = args[1].strip()
    log_file = os.path.join(HOSTED_BOTS_DIR, f"{filename}.log")
    
    if not os.path.exists(log_file):
        bot.reply_to(message, f"❌ No logs found for `{filename}`.")
        return
        
    with open(log_file, 'r') as f:
        logs = f.read()
        
    if not logs.strip():
        bot.reply_to(message, f"📝 *Logs for {filename}:* (Empty/Clean)")
        return
        
    if len(logs) > 4000:
        logs = logs[-4000:] # Get last 4000 chars
        
    bot.reply_to(message, f"📝 *Logs for {filename}:*\n```\n{logs}\n```", parse_mode="Markdown")

@bot.message_handler(commands=['hosts'])
def list_hosted_bots(message):
    files = os.listdir(HOSTED_BOTS_DIR)
    if not files: 
        bot.reply_to(message, "📁 *No bots uploaded yet.*")
        return
    
    text = "📁 *[HOSTED BOT FILES]*\n\n"
    for i, f in enumerate(files, 1):
        if f.endswith(".log") or f.endswith(".zip"): continue
        is_running = any(p['file'] == f for p in bg_processes.values() if p['process'].poll() is None)
        status = "🟢 RUNNING" if is_running else "🔴 STOPPED"
        text += f"{i}. `{f}` - {status}\n"
    
    text += "\nType `/logs <filename>` to check for errors."
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['stopbot'])
def stop_bot(message):
    args = message.text.split(" ", 1)
    if len(args) < 2: return
    filename = args[1].strip()
    
    for pid, pinfo in list(bg_processes.items()):
        if pinfo['file'] == filename:
            try:
                import signal
                os.killpg(os.getpgid(pinfo['process'].pid), signal.SIGTERM)
                del bg_processes[pid]
                bot.reply_to(message, f"🛑 *[BOT STOPPED]*\nFile `{filename}` terminated.", parse_mode="Markdown")
                return
            except Exception as e:
                bot.reply_to(message, f"❌ Error: {e}")
                return
    bot.reply_to(message, f"⚠️ No running process found for `{filename}`.")

# ----------------- CALLBACKS -----------------
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    if call.data == "btn_main_menu":
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=get_help_text(), parse_mode="Markdown", reply_markup=get_main_menu_keyboard(user_id))
    elif call.data == "btn_hosted":
        list_hosted_bots(call.message)
    elif call.data == "btn_sysinfo":
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=f"🧬 *CPU:* `{psutil.cpu_percent()}%` | *RAM:* `{psutil.virtual_memory().percent}%`", parse_mode="Markdown", reply_markup=get_back_keyboard())
    elif call.data == "btn_myid":
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=f"🪪 *YOUR ID:* `{user_id}`", parse_mode="Markdown", reply_markup=get_back_keyboard())
    elif call.data == "verify_membership":
        is_all_joined, missing = check_channel_subscriptions(user_id)
        if is_all_joined: send_menu(call.message)
        else: bot.answer_callback_query(call.id, "❌ Join all channels first!", show_alert=True)
    elif call.data == "cancel_channel_setup":
        waiting_for_channel_forward.discard(user_id)
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="Cancelled.")

if __name__ == "__main__":
    print("⚡️ AUTO-HOSTING is online.")
    bot.infinity_polling()
