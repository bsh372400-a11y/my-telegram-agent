import os
import io
import json
import base64
import threading
import random
import time
from flask import Flask
from groq import Groq
import httpx
from bs4 import BeautifulSoup
from urllib.parse import unquote
import telebot
from telebot import types
from pypdf import PdfReader
from openai import OpenAI

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
AGNES_API_KEY = os.environ.get("AGNES_API_KEY")
MAGIC_HOUR_API_KEY = os.environ.get("MAGIC_HOUR_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    print("Error: missing tokens")
    exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

openrouter_client = None
if OPENROUTER_API_KEY:
    openrouter_client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ============ الذاكرة ============
def hfile(uid):
    return f"history_{uid}.json"

def clean_msg(m):
    if not isinstance(m, dict):
        return None
    role = m.get("role")
    if role not in ("system", "user", "assistant", "tool"):
        return None
    out = {"role": role}
    if "content" in m and m["content"] is not None:
        out["content"] = m["content"]
    if "tool_calls" in m and m["tool_calls"]:
        out["tool_calls"] = m["tool_calls"]
    if "tool_call_id" in m:
        out["tool_call_id"] = m["tool_call_id"]
    return out

def load_history(uid):
    f = hfile(uid)
    if os.path.exists(f):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                raw = json.load(fp)
            cleaned = []
            for m in raw:
                c = clean_msg(m)
                if c:
                    cleaned.append(c)
            if cleaned:
                return cleaned
        except:
            pass
    return [{"role": "system", "content": "أنت مساعد ذكي ودود. تجاوب بالعربية. استعمل search_web كي تحتاج معلومات حديثة."}]

def save_history(uid, msgs):
    clean = []
    for m in msgs:
        c = clean_msg(m)
        if c:
            clean.append(c)
    with open(hfile(uid), "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)

# ============ توليد الفيديو ============
def generate_video_agnes(prompt, seconds=5, aspect_ratio="16:9"):
    if not AGNES_API_KEY:
        return None, "Agnes API key missing"
    try:
        create_url = "https://apihub.agnes-ai.com/v1/videos"
        headers = {
            "Authorization": f"Bearer {AGNES_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "agnes-video-2.5-flash",
            "prompt": prompt,
            "seconds": str(seconds),
            "mode": "text",
            "size": "720P",
            "aspect_ratio": aspect_ratio
        }
        r = httpx.post(create_url, json=payload, headers=headers, timeout=60)
        if r.status_code != 200:
            return None, f"Agnes create: {r.text[:150]}"
        data = r.json()
        vid = data.get("video_id")
        if not vid:
            return None, f"Agnes no id: {str(data)[:150]}"

        poll = f"https://apihub.agnes-ai.com/agnesapi?video_id={vid}&model_name=agnes-video-2.5-flash"
        for _ in range(60):
            time.sleep(3)
            try:
                rr = httpx.get(poll, headers={"Authorization": f"Bearer {AGNES_API_KEY}"}, timeout=30)
                if rr.status_code != 200:
                    continue
                d = rr.json()
                st = d.get("status")
                if st == "completed":
                    return d.get("url"), None
                if st == "failed":
                    return None, f"Agnes failed: {d.get('error', 'unknown')}"
            except:
                continue
        return None, "Agnes timeout"
    except Exception as e:
        return None, f"Agnes: {str(e)[:150]}"

def generate_video_magic(prompt, seconds=5, aspect_ratio="16:9"):
    if not MAGIC_HOUR_API_KEY:
        return None, "Magic Hour key missing"
    try:
        url = "https://api.magichour.ai/v1/text-to-video"
        headers = {
            "Authorization": f"Bearer {MAGIC_HOUR_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "name": "Telegram Bot",
            "end_seconds": seconds,
            "aspect_ratio": aspect_ratio,
            "resolution": "480p",
            "style": {"prompt": prompt}
        }
        r = httpx.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code != 200:
            return None, f"MagicHour create: {r.text[:150]}"
        data = r.json()
        job = data.get("id")
        if not job:
            return None, f"MagicHour no id: {str(data)[:150]}"

        poll = f"https://api.magichour.ai/v1/video-projects/{job}"
        for _ in range(80):
            time.sleep(3)
            try:
                rr = httpx.get(poll, headers={"Authorization": f"Bearer {MAGIC_HOUR_API_KEY}"}, timeout=30)
                if rr.status_code != 200:
                    continue
                d = rr.json()
                st = d.get("status")
                if st == "complete":
                    downloads = d.get("downloads", [])
                    if downloads:
                        return downloads[0].get("url"), None
                    return None, "MagicHour no url"
                if st == "error":
                    return None, f"MagicHour: {d.get('error', 'unknown')}"
            except:
                continue
        return None, "MagicHour timeout"
    except Exception as e:
        return None, f"MagicHour: {str(e)[:150]}"

def generate_video(prompt, seconds=5, aspect_ratio="16:9"):
    url, err = generate_video_agnes(prompt, seconds, aspect_ratio)
    if url:
        return url, None, "Agnes AI"
    print(f"Agnes failed: {err} - trying Magic Hour")
    url, err = generate_video_magic(prompt, seconds, aspect_ratio)
    if url:
        return url, None, "Magic Hour"
    return None, err, None

# ============ الأزرار ============
def get_after_reply_buttons():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("🔄 أعيد", callback_data="act_retry"),
        types.InlineKeyboardButton("📝 لخّص", callback_data="act_summary"),
        types.InlineKeyboardButton("🌐 ترجم", callback_data="act_translate"),
    )
    kb.add(
        types.InlineKeyboardButton("🎯 القائمة", callback_data="menu_main"),
        types.InlineKeyboardButton("🗑️ امسح الذاكرة", callback_data="menu_clear"),
    )
    return kb

def send_answer(chat_id, text, with_buttons=True):
    if not text or not text.strip():
        text = "ما لقيتش جواب. عاود جرّب."
    parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for i, part in enumerate(parts):
        if i == len(parts) - 1 and with_buttons:
            bot.send_message(chat_id, part, reply_markup=get_after_reply_buttons())
        else:
            bot.send_message(chat_id, part)

def get_main_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔍 ابحث", callback_data="menu_search"),
        types.InlineKeyboardButton("🌐 ترجم", callback_data="menu_translate"),
        types.InlineKeyboardButton("📝 لخّص", callback_data="menu_summary"),
        types.InlineKeyboardButton("💡 أفكار", callback_data="menu_ideas"),
        types.InlineKeyboardButton("✍️ اكتب", callback_data="menu_write"),
        types.InlineKeyboardButton("🔢 احسب", callback_data="menu_math"),
        types.InlineKeyboardButton("📖 اشرحلي", callback_data="menu_explain"),
        types.InlineKeyboardButton("💬 شات", callback_data="menu_chat"),
    )
    kb.add(
        types.InlineKeyboardButton("🎬 فيديو", callback_data="menu_video"),
        types.InlineKeyboardButton("🎲 نصيحة", callback_data="menu_quote"),
        types.InlineKeyboardButton("🌤️ الطقس", callback_data="menu_weather"),
        types.InlineKeyboardButton("📊 إحصائيات", callback_data="menu_stats"),
    )
    kb.add(
        types.InlineKeyboardButton("❓ مساعدة", callback_data="menu_help"),
        types.InlineKeyboardButton("🗑️ امسح الذاكرة", callback_data="menu_clear"),
    )
    return kb

# ============ البحث ============
def search_web(query):
    try:
        r = httpx.post("https://html.duckduckgo.com/html/", data={"q": query},
                       headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for res in soup.select(".result__body")[:3]:
            t = res.select_one(".result__title .result__a")
            s = res.select_one(".result__snippet")
            if t:
                title = t.get_text(strip=True)
                link = t.get("href", "")
                if "uddg=" in link:
                    link = unquote(link.split("uddg=")[1].split("&")[0])
                snip = s.get_text(strip=True) if s else ""
                out.append(f"- {title}: {snip} ({link})")
        return "\n".join(out) if out else "لا توجد نتائج."
    except Exception as e:
        return f"خطأ: {e}"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "ابحث في الإنترنت للحصول على معلومات حديثة",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"]
        }
    }
}]

# ============ المحادثة ============
def chat_with_tools(uid, user_text, with_history=True):
    if with_history:
        msgs = load_history(uid)
    else:
        msgs = [{"role": "system", "content": "أنت مساعد ذكي ودود. تجاوب بالعربية."}]
    msgs.append({"role": "user", "content": user_text})

    try:
        reply = client.chat.completions.create(
            messages=msgs, model="openai/gpt-oss-120b",
            tools=TOOLS, tool_choice="auto"
        )
        msg = reply.choices[0].message
    except Exception as e:
        return f"خطأ في الاتصال: {str(e)[:200]}"

    if msg.tool_calls:
        tc = msg.tool_calls[0]
        if tc.function.name != "search_web":
            answer = msg.content or "سمعتك، بصح ما فهمتش. عاود صياغة."
            if with_history:
                msgs.append({"role": "assistant", "content": answer})
                save_history(uid, msgs)
            return answer
        try:
            args = json.loads(tc.function.arguments)
        except:
            args = {}
        query = args.get("query", user_text)
        bot.send_message(uid, f"🔍 نبحث على: {query}")
        res = search_web(query)

        msgs.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
            }]
        })
        msgs.append({"role": "tool", "tool_call_id": tc.id, "content": res})

        try:
            summary_msgs = [
                {"role": "system", "content": "أنت مساعد ذكي. جاوب على سؤال المستخدم بناء على نتائج البحث. جاوب بالعربية."},
                {"role": "user", "content": f"السؤال: {user_text}\n\nنتائج البحث:\n{res}\n\nجاوب."}
            ]
            r2 = client.chat.completions.create(messages=summary_msgs, model="openai/gpt-oss-120b")
            answer = r2.choices[0].message.content
        except Exception as e:
            answer = f"لقيت النتائج بصح ما قدرتش نلخّصها. ({str(e)[:100]})"
    else:
        answer = msg.content

    if not answer or not answer.strip():
        answer = "ما لقيتش جواب. عاود جرّب."

    if with_history:
        msgs.append({"role": "assistant", "content": answer})
        save_history(uid, msgs)
    return answer

def transcribe(audio_bytes):
    r = client.audio.transcriptions.create(
        file=("voice.ogg", audio_bytes),
        model="whisper-large-v3-turbo",
        language="ar",
        prompt="هذا تسجيل بالدارجة التونسية."
    )
    return r.text

# ============ الأوامر ============
@bot.message_handler(commands=['start'])
def cmd_start(m):
    txt = ("أهلاً! 👋\n\nأنا مساعدك الذكي:\n"
           "💬 كتابة | 🎤 صوت | 🖼️ صور | 📄 PDF\n"
           "🔍 بحث | 🌐 ترجمة | 📝 تلخيص\n"
           "🎬 توليد فيديو بالذكاء الاصطناعي!\n\n"
           "اكتب /menu باش تشوف كل شيء!")
    bot.reply_to(m, txt, reply_markup=get_main_menu())

@bot.message_handler(commands=['help'])
def cmd_help(m):
    txt = ("🤖 الأوامر:\n"
           "/start - بداية\n"
           "/menu - القائمة\n"
           "/video - توليد فيديو\n"
           "/help - المساعدة\n"
           "/clear - امسح الذاكرة")
    bot.reply_to(m, txt)

@bot.message_handler(commands=['menu'])
def cmd_menu(m):
    bot.reply_to(m, "🎯 اختار شنوّة تحب:", reply_markup=get_main_menu())

@bot.message_handler(commands=['clear'])
def cmd_clear(m):
    uid = m.chat.id
    if os.path.exists(hfile(uid)):
        os.remove(hfile(uid))
    bot.reply_to(m, "✅ الذاكرة تمسحت.")

@bot.message_handler(commands=['video'])
def cmd_video(m):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📱 عمودي (9:16)", callback_data="vid_v_9_16"),
        types.InlineKeyboardButton("🖥️ أفقي (16:9)", callback_data="vid_v_16_9"),
        types.InlineKeyboardButton("⬛ مربع (1:1)", callback_data="vid_v_1_1"),
    )
    bot.reply_to(m, "🎬 اختار مقاس الفيديو:", reply_markup=kb)

@bot.message_handler(commands=['stats'])
def cmd_stats(m):
    uid = m.chat.id
    if os.path.exists(hfile(uid)):
        with open(hfile(uid), "r", encoding="utf-8") as f:
            msgs = json.load(f)
        total = len(msgs)
        um = sum(1 for x in msgs if isinstance(x, dict) and x.get("role") == "user")
        bm = sum(1 for x in msgs if isinstance(x, dict) and x.get("role") == "assistant")
        bot.reply_to(m, f"📊 إحصائياتك:\n\n💬 المجموع: {total}\n👤 رسائلك: {um}\n🤖 ردود: {bm}")
    else:
        bot.reply_to(m, "📊 ما عندك حتى محادثة.")

# ============ معالج فيديو ============
def process_video_format(m, aspect_ratio):
    prompt = m.text.strip() if m.text else ""
    if not prompt:
        bot.reply_to(m, "❌ ما كتبتش وصف.")
        return
    bot.send_chat_action(m.chat.id, 'upload_video')
    wait = bot.reply_to(m, f"⏳ نولّد الفيديو ({aspect_ratio})... ياخذ 1-3 دقائق.")
    url, err, provider = generate_video(prompt, seconds=5, aspect_ratio=aspect_ratio)
    try:
        bot.delete_message(m.chat.id, wait.message_id)
    except:
        pass
    if url:
        try:
            bot.send_video(m.chat.id, url, caption=f"🎬 جاهز!\n📝 {prompt}\n📐 {aspect_ratio}\n🔧 {provider}")
        except Exception as e:
            bot.send_message(m.chat.id, f"🎬 الفيديو:\n{url}\n\n({str(e)[:100]})")
    else:
        bot.send_message(m.chat.id, f"❌ فشل.\n\n{err}")

# ============ الأزرار ============
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    uid = call.message.chat.id
    data = call.data
    bot.answer_callback_query(call.id)

    if data.startswith("vid_v_"):
        ar_map = {"vid_v_9_16": "9:16", "vid_v_16_9": "16:9", "vid_v_1_1": "1:1"}
        ar = ar_map.get(data, "16:9")
        msg = bot.send_message(uid, f"🎬 مقاس {ar}. اكتبلي وصف الفيديو (بالإنجليزية):")
        bot.register_next_step_handler(msg, process_video_format, ar)
    elif data == "menu_main":
        bot.send_message(uid, "🎯 القائمة:", reply_markup=get_main_menu())
    elif data == "menu_help":
        cmd_help(call.message)
    elif data == "menu_clear":
        if os.path.exists(hfile(uid)):
            os.remove(hfile(uid))
        bot.send_message(uid, "✅ الذاكرة تمسحت.")
    elif data == "menu_video":
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("📱 عمودي (9:16)", callback_data="vid_v_9_16"),
            types.InlineKeyboardButton("🖥️ أفقي (16:9)", callback_data="vid_v_16_9"),
            types.InlineKeyboardButton("⬛ مربع (1:1)", callback_data="vid_v_1_1"),
        )
        bot.send_message(uid, "🎬 اختار مقاس الفيديو:", reply_markup=kb)
    elif data == "menu_search":
        msg = bot.send_message(uid, "🔍 اكتب شنوّة تحب نبحث عليه:")
        bot.register_next_step_handler(msg, process_search)
    elif data == "menu_translate":
        msg = bot.send_message(uid, "🌐 اكتب النص (عربي↔إنجليزي):")
        bot.register_next_step_handler(msg, process_translate)
    elif data == "menu_summary":
        msg = bot.send_message(uid, "📝 ابعثلي النص:")
        bot.register_next_step_handler(msg, process_summary)
    elif data == "menu_ideas":
        msg = bot.send_message(uid, "💡 على شنوّة تحب أفكار؟")
        bot.register_next_step_handler(msg, process_ideas)
    elif data == "menu_write":
        msg = bot.send_message(uid, "✍️ اكتب شنوّة تحب نكتبلك:")
        bot.register_next_step_handler(msg, process_write)
    elif data == "menu_math":
        msg = bot.send_message(uid, "🔢 اكتب المسألة:")
        bot.register_next_step_handler(msg, process_math)
    elif data == "menu_explain":
        msg = bot.send_message(uid, "📖 اكتب الحاجة:")
        bot.register_next_step_handler(msg, process_explain)
    elif data == "menu_chat":
        bot.send_message(uid, "💬 تفضل 🙂")
    elif data == "menu_quote":
        quotes = [
            "💡 النجاح هو مجموع جهود صغيرة تتعاود كل يوم.",
            "💡 ما تخافش من البطء، خاف من الوقوف.",
            "💡 اللي يتعلّم من أخطائه، يربح.",
            "💡 ابدا من وين راك، استعمل اللي عندك.",
            "💡 الصبر مفتاح الفرج.",
        ]
        bot.send_message(uid, random.choice(quotes))
    elif data == "menu_weather":
        msg = bot.send_message(uid, "🌤️ اكتب اسم المدينة:")
        bot.register_next_step_handler(msg, process_weather)
    elif data == "menu_stats":
        cmd_stats(call.message)
    elif data == "act_retry":
        bot.send_message(uid, "🔄 اكتب سؤالك.")
    elif data == "act_summary":
        msg = bot.send_message(uid, "📝 ابعثلي النص:")
        bot.register_next_step_handler(msg, process_summary)
    elif data == "act_translate":
        msg = bot.send_message(uid, "🌐 اكتب النص:")
        bot.register_next_step_handler(msg, process_translate)

# ============ معالجات ============
def process_search(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        send_answer(m.chat.id, chat_with_tools(m.chat.id, f"ابحثلي على: {m.text}"))
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_translate(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        send_answer(m.chat.id, chat_with_tools(m.chat.id, f"ترجم (عربي↔إنجليزي):\n\n{m.text}", with_history=False))
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_summary(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        send_answer(m.chat.id, chat_with_tools(m.chat.id, f"لخّص في نقاط:\n\n{m.text}", with_history=False))
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_ideas(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        send_answer(m.chat.id, chat_with_tools(m.chat.id, f"عطيني 5 أفكار على: {m.text}", with_history=False))
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_write(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        send_answer(m.chat.id, chat_with_tools(m.chat.id, f"اكتبلي: {m.text}", with_history=False))
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_math(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        send_answer(m.chat.id, chat_with
