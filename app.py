import os
import json
import threading
from flask import Flask
from groq import Groq
import httpx
from bs4 import BeautifulSoup
from urllib.parse import unquote
import telebot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    print("Error: missing tokens")
    exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = Groq(api_key=GROQ_API_KEY)

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

def search_web(query):
    try:
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = httpx.post(url, data=data, headers=headers, timeout=20)
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for res in soup.select(".result__body")[:3]:
            title_tag = res.select_one(".result__title .result__a")
            snippet_tag = res.select_one(".result__snippet")
            if title_tag:
                title = title_tag.get_text(strip=True)
                raw_link = title_tag.get("href", "")
                if "uddg=" in raw_link:
                    link = unquote(raw_link.split("uddg=")[1].split("&")[0])
                else:
                    link = raw_link
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                results.append(f"- {title}: {snippet} ({link})")
        return "\n".join(results) if results else "لا توجد نتائج."
    except Exception as e:
        return f"خطأ: {e}"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "أهلاً! أنا مساعدك الذكي. كيف يمكنني مساعدتك؟")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = str(message.chat.id)
    user_message = message.text

    history_file = f"history_{user_id}.json"
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            messages = json.load(f)
    else:
        messages = [{"role": "system", "content": "أنت مساعد ذكي. استعمل search_web كي تحتاج معلومات حديثة."}]

    messages.append({"role": "user", "content": user_message})

    try:
        tools = [{
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

        reply = client.chat.completions.create(
            messages=messages,
            model="openai/gpt-oss-120b",
            tools=tools,
        )

        if reply.choices[0].message.tool_calls:
            tc = reply.choices[0].message.tool_calls[0]
            args = json.loads(tc.function.arguments)
            bot.send_message(message.chat.id, f"🔍 نبحث على: {args['query']}")
            result = search_web(args["query"])
            messages.append(reply.choices[0].message)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            reply = client.chat.completions.create(messages=messages, model="openai/gpt-oss-120b", tools=tools)

        answer = reply.choices[0].message.content

        for i in range(0, len(answer), 4000):
            bot.send_message(message.chat.id, answer[i:i+4000])

        messages.append({"role": "assistant", "content": answer})

        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)

    except Exception as e:
        bot.reply_to(message, f"حدث خطأ: {e}")

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    print("Bot running...")
    bot.infinity_polling()
