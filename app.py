import os
import io
import json
import base64
import threading
import random
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
        types.InlineKeyboardButton("🎲 نصيحة", callback_data="menu_quote"),
        types.InlineKeyboardButton("🌤️ الطقس", callback_data="menu_weather"),
        types.InlineKeyboardButton("📊 إحصائيات", callback_data="menu_stats"),
        types.InlineKeyboardButton("❓ مساعدة", callback_data="menu_help"),
    )
    kb.add(types.InlineKeyboardButton("🗑️ امسح الذاكرة", callback_data="menu_clear"))
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
            messages=msgs,
            model="openai/gpt-oss-120b",
            tools=TOOLS,
            tool_choice="auto"
        )
        msg = reply.choices[0].message
    except Exception as e:
        return f"خطأ في الاتصال: {str(e)[:200]}"

    if msg.tool_calls:
        tc = msg.tool_calls[0]
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
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            }]
        })
        msgs.append({"role": "tool", "tool_call_id": tc.id, "content": res})

        try:
            summary_msgs = [
                {"role": "system", "content": "أنت مساعد ذكي. جاوب على سؤال المستخدم بناء على نتائج البحث. جاوب بالعربية، بشكل واضح ومفيد."},
                {"role": "user", "content": f"السؤال: {user_text}\n\nنتائج البحث:\n{res}\n\nجاوب على السؤال بناء على هالنتائج."}
            ]
            reply2 = client.chat.completions.create(
                messages=summary_msgs,
                model="openai/gpt-oss-120b"
            )
            answer = reply2.choices[0].message.content
        except Exception as e:
            answer = f"لقيت النتائج بصح ما قدرتش نلخّصها. عاود جرّب. ({str(e)[:100]})"
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
    )
    return r.text

# ============ الأوامر ============
@bot.message_handler(commands=['start'])
def cmd_start(m):
    txt = ("أهلاً! 👋\n\nأنا مساعدك الذكي:\n"
           "💬 كتابة | 🎤 صوت | 🖼️ صور | 📄 PDF\n"
           "🔍 بحث | 🌐 ترجمة | 📝 تلخيص | 💡 أفكار\n\n"
           "اكتب /menu باش تشوف كل شيء!")
    bot.reply_to(m, txt, reply_markup=get_main_menu())

@bot.message_handler(commands=['help'])
def cmd_help(m):
    txt = ("🤖 الأوامر:\n"
           "/start - بداية\n"
           "/menu - القائمة\n"
           "/help - المساعدة\n"
           "/clear - امسح الذاكرة\n"
           "/stats - إحصائيات")
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

@bot.message_handler(commands=['stats'])
def cmd_stats(m):
    uid = m.chat.id
    if os.path.exists(hfile(uid)):
        with open(hfile(uid), "r", encoding="utf-8") as f:
            msgs = json.load(f)
        total = len(msgs)
        user_msgs = sum(1 for x in msgs if isinstance(x, dict) and x.get("role") == "user")
        bot_msgs = sum(1 for x in msgs if isinstance(x, dict) and x.get("role") == "assistant")
        txt = (f"📊 إحصائياتك:\n\n"
               f"💬 مجموع الرسائل: {total}\n"
               f"👤 رسائلك: {user_msgs}\n"
               f"🤖 ردود البوت: {bot_msgs}")
    else:
        txt = "📊 ما عندك حتى محادثة محفوظة."
    bot.reply_to(m, txt)

# ============ الأزرار ============
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    uid = call.message.chat.id
    data = call.data
    bot.answer_callback_query(call.id)

    if data == "menu_main":
        bot.send_message(uid, "🎯 القائمة الرئيسية:", reply_markup=get_main_menu())

    elif data == "menu_help":
        cmd_help(call.message)

    elif data == "menu_clear":
        if os.path.exists(hfile(uid)):
            os.remove(hfile(uid))
        bot.send_message(uid, "✅ الذاكرة تمسحت.")

    elif data == "menu_stats":
        cmd_stats(call.message)

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
        msg = bot.send_message(uid, "📖 اكتب الحاجة اللي تحب نشرحها:")
        bot.register_next_step_handler(msg, process_explain)

    elif data == "menu_chat":
        bot.send_message(uid, "💬 تفضل، اكتبلي ونتكلمو 🙂")

    elif data == "menu_quote":
        quotes = [
            "💡 النجاح هو مجموع جهود صغيرة تتعاود كل يوم.",
            "💡 ما تخافش من البطء، خاف من الوقوف.",
            "💡 اللي يتعلّم من أخطائه، يربح.",
            "💡 ابدا من وين راك، استعمل اللي عندك، اعمل اللي تقدر.",
            "💡 الصبر مفتاح الفرج.",
            "💡 أحسن وقت تزرع فيه شجرة كان 20 سنة قبل. ثاني أحسن وقت هو توّا.",
        ]
        bot.send_message(uid, random.choice(quotes))

    elif data == "menu_weather":
        msg = bot.send_message(uid, "🌤️ اكتب اسم المدينة:")
        bot.register_next_step_handler(msg, process_weather)

    elif data == "act_retry":
        bot.send_message(uid, "🔄 اكتب سؤالك مرة أخرى.")

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
        answer = chat_with_tools(m.chat.id, f"ابحثلي في الإنترنت على: {m.text}")
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_translate(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, f"ترجم هذا (عربي↔إنجليزي):\n\n{m.text}", with_history=False)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_summary(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, f"لخّصلي في نقاط:\n\n{m.text}", with_history=False)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_ideas(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, f"عطيني 5 أفكار على: {m.text}", with_history=False)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_write(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, f"اكتبلي: {m.text}", with_history=False)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_math(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, f"حل المسألة: {m.text}\n\nوريني الخطوات.", with_history=False)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_explain(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, f"اشرحلي بطريقة بسيطة: {m.text}", with_history=False)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

def process_weather(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, f"شنوّة الطقس في {m.text} توّا؟ ابحثلي في الإنترنت.", with_history=False)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ: {str(e)[:200]}")

# ============ Voice ============
@bot.message_handler(content_types=['voice'])
def handle_voice(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        fi = bot.get_file(m.voice.file_id)
        audio = bot.download_file(fi.file_path)
        text = transcribe(audio)
        bot.send_message(m.chat.id, f"🎤 سمعتك تقول: {text}")
        answer = chat_with_tools(m.chat.id, text)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ في الصوت: {str(e)[:200]}")

# ============ Photo ============
@bot.message_handler(content_types=['photo'])
def handle_photo(m):
    if not openrouter_client:
        bot.reply_to(m, "ميزة الصور غير مفعلة.")
        return
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        fi = bot.get_file(m.photo[-1].file_id)
        img = bot.download_file(fi.file_path)
        b64 = base64.b64encode(img).decode()
        caption = m.caption or "شنوّة في هذه الصورة؟ وصفلي بالتفصيل."

        response = openrouter_client.chat.completions.create(
            model="openrouter/free",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": caption},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ]
            }]
        )
        send_answer(m.chat.id, response.choices[0].message.content)
    except Exception as e:
        bot.reply_to(m, f"خطأ في الصورة: {str(e)[:200]}")

# ============ Document ============
@bot.message_handler(content_types=['document'])
def handle_doc(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        fi = bot.get_file(m.document.file_id)
        doc = bot.download_file(fi.file_path)
        name = (m.document.file_name or "").lower()

        if name.endswith(".pdf"):
            reader = PdfReader(io.BytesIO(doc))
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:20])
            if not text.strip():
                bot.reply_to(m, "ما قدرتش نقرا الـ PDF.")
                return
            prompt = f"هذا محتوى PDF، لخّصلي أهم النقاط:\n\n{text[:8000]}"
        elif name.endswith((".txt", ".md", ".csv")):
            text = doc.decode("utf-8", errors="ignore")
            prompt = f"هذا محتوى ملف، لخّصلي:\n\n{text[:8000]}"
        else:
            bot.reply_to(m, "نوع الملف ما مدعومش.")
            return

        answer = chat_with_tools(m.chat.id, prompt, with_history=False)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ في الملف: {str(e)[:200]}")

# ============ Text ============
@bot.message_handler(func=lambda m: True)
def handle_text(m):
    if not m.text:
        return
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, m.text)
        send_answer(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"حدث خطأ: {str(e)[:200]}")

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Bot running...")
    bot.infinity_polling()
