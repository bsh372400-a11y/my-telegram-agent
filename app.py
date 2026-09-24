import os
import io
import json
import base64
import threading
from flask import Flask
from groq import Groq
import httpx
from bs4 import BeautifulSoup
from urllib.parse import unquote
import telebot
from pypdf import PdfReader
from google import genai
from google.genai import types

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    print("Error: missing tokens")
    exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

gemini_client = None
if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

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

def hfile(uid):
    return f"history_{uid}.json"

def load_history(uid):
    f = hfile(uid)
    if os.path.exists(f):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except:
            pass
    return [{"role": "system", "content": "أنت مساعد ذكي ودود. تجاوب بالعربية. استعمل search_web كي تحتاج معلومات حديثة."}]

def save_history(uid, msgs):
    with open(hfile(uid), "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False, indent=2)

def send_long(chat_id, text):
    for i in range(0, len(text), 4000):
        bot.send_message(chat_id, text[i:i+4000])

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

def chat_with_tools(uid, user_text, with_history=True):
    if with_history:
        msgs = load_history(uid)
    else:
        msgs = [{"role": "system", "content": "أنت مساعد ذكي ودود. تجاوب بالعربية."}]
    msgs.append({"role": "user", "content": user_text})

    reply = client.chat.completions.create(messages=msgs, model="openai/gpt-oss-120b", tools=TOOLS)

    if reply.choices[0].message.tool_calls:
        tc = reply.choices[0].message.tool_calls[0]
        args = json.loads(tc.function.arguments)
        bot.send_message(uid, f"🔍 نبحث على: {args['query']}")
        res = search_web(args["query"])
        msgs.append(reply.choices[0].message)
        msgs.append({"role": "tool", "tool_call_id": tc.id, "content": res})
        reply = client.chat.completions.create(messages=msgs, model="openai/gpt-oss-120b", tools=TOOLS)

    answer = reply.choices[0].message.content
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

@bot.message_handler(commands=['start'])
def cmd_start(m):
    bot.reply_to(m, "أهلاً! 👋\n\nأنا مساعدك الذكي. نجم:\n💬 تجاوب معايا بالكتابة\n🎤 تبعثلي voice\n🖼️ تبعثلي صورة\n📄 تبعثلي PDF\n🔍 نبحثلك في الإنترنت\n\nأكتب /help للتفاصيل.")

@bot.message_handler(commands=['help'])
def cmd_help(m):
    txt = ("🤖 الأوامر:\n"
           "/start - بداية\n"
           "/help - المساعدة\n"
           "/clear - امسح الذاكرة\n\n"
           "📝 المميزات:\n"
           "• كتابة عادية\n"
           "• Voice message (نحوّلو لنص)\n"
           "• صورة (نوصفلك فيها)\n"
           "• PDF (نقراه ونجاوبك)\n"
           "• بحث في الإنترنت تلقائي")
    bot.reply_to(m, txt)

@bot.message_handler(commands=['clear'])
def cmd_clear(m):
    uid = m.chat.id
    if os.path.exists(hfile(uid)):
        os.remove(hfile(uid))
    bot.reply_to(m, "✅ الذاكرة تمسحت.")

@bot.message_handler(content_types=['voice'])
def handle_voice(m):
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        fi = bot.get_file(m.voice.file_id)
        audio = bot.download_file(fi.file_path)
        text = transcribe(audio)
        bot.send_message(m.chat.id, f"🎤 سمعتك تقول: {text}")
        answer = chat_with_tools(m.chat.id, text)
        send_long(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ في الصوت: {str(e)[:200]}")

@bot.message_handler(content_types=['photo'])
def handle_photo(m):
    if not gemini_client:
        bot.reply_to(m, "ميزة الصور غير مفعلة.")
        return
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        fi = bot.get_file(m.photo[-1].file_id)
        img = bot.download_file(fi.file_path)
        caption = m.caption or "شنوّة في هذه الصورة؟ وصفلي بالتفصيل."

        import time
        last_error = None
        for attempt in range(3):
            try:
                response = gemini_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=[
                        types.Part.from_text(text=caption),
                        types.Part.from_bytes(data=img, mime_type="image/jpeg")
                    ]
                )
                send_long(m.chat.id, response.text)
                return
            except Exception as e:
                last_error = e
                if "503" in str(e) or "UNAVAILABLE" in str(e):
                    time.sleep(3)
                    continue
                else:
                    break
        bot.reply_to(m, f"الصورة ما خدمتش توّا. عاود جرّب بعد شوية.\n\n({str(last_error)[:200]})")
    except Exception as e:
        bot.reply_to(m, f"خطأ في الصورة: {str(e)[:200]}")

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
        send_long(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"خطأ في الملف: {str(e)[:200]}")

@bot.message_handler(func=lambda m: True)
def handle_text(m):
    if not m.text:
        return
    try:
        bot.send_chat_action(m.chat.id, 'typing')
        answer = chat_with_tools(m.chat.id, m.text)
        send_long(m.chat.id, answer)
    except Exception as e:
        bot.reply_to(m, f"حدث خطأ: {str(e)[:200]}")

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Bot running...")
    bot.infinity_polling()
