import os
import json
import time
import threading
import sys
import re
from datetime import datetime, timedelta
import requests
import feedparser
from bs4 import BeautifulSoup
from openai import OpenAI
from flask import Flask, request, jsonify

# Forzar logs en tiempo real en Render
sys.stdout.reconfigure(line_buffering=True)

app = Flask(__name__)

# ------------------------------------------------------------------------------
# CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ------------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

TELEGRAM_VIP_CHANNEL_ID = (
    os.getenv("TELEGRAM_VIP_CHANNEL_ID") or 
    os.getenv("Telegram_vip_channel") or ""
).strip()

BOT_USERNAME = (
    os.getenv("BOT_USERNAME") or 
    os.getenv("Bot_username") or ""
).strip().lstrip("@")

OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY") or 
    os.getenv("OpenAI_API_Key") or ""
).strip()

MP_FIXED_LINK = (
    os.getenv("MP_FIXED_LINK") or 
    os.getenv("Mp_fixed_link") or 
    os.getenv("MP_LINK") or ""
).strip()

ADMIN_CHAT_ID = (os.getenv("ADMIN_CHAT_ID") or "").strip()

raw_price = (
    os.getenv("SUBSCRIPTION_PRICE") or 
    os.getenv("Suscription_price") or "7000"
)
try:
    SUBSCRIPTION_PRICE = float(str(raw_price).replace(",", "").strip())
except Exception:
    SUBSCRIPTION_PRICE = 7000.0

POSTED_NEWS_FILE = "posted_news.json"
SUBSCRIBERS_FILE = "subscribers.json"

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
TG_BASE_URL = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN

# HORARIOS DE RÁFAGA (Hora Argentina UTC-3)
MORNING_HOUR_ARG = 8   # 08:00 AM
EVENING_HOUR_ARG = 21  # 21:00 PM

# ------------------------------------------------------------------------------
# FUENTES DE ALTA SEÑAL
# ------------------------------------------------------------------------------
RSS_FEEDS = {
    "🤖 <b>IA & TECNOLOGÍA</b>": [
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://www.technologyreview.com/feed/",
        "https://venturebeat.com/category/ai/feed/",
        "https://arstechnica.com/technology/feed/"
    ],
    "💼 <b>NEGOCIOS & STARTUPS</b>": [
        "https://techcrunch.com/category/startups/feed/",
        "https://feeds.feedburner.com/entrepreneur/latest",
        "https://restofworld.org/feed/latest"
    ],
    "📈 <b>FINANZAS & CRIPTO</b>": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://www.cnbc.com/id/10000664/device/rss/rss.html"
    ]
}

# ------------------------------------------------------------------------------
# MANEJO DE ARCHIVOS JSON
# ------------------------------------------------------------------------------
def load_json_file(filename, default_value):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_value

def save_json_file(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Error al guardar {filename}: {e}", flush=True)

# ------------------------------------------------------------------------------
# OBTENCIÓN ROBUSTA DE MERCADOS EN TIEMPO REAL (BINANCE + FEAR & GREED)
# ------------------------------------------------------------------------------
def fetch_live_market_data():
    headers = {"User-Agent": "Mozilla/5.0"}
    btc_str, eth_str, sol_str = "N/A", "N/A", "N/A"
    fng_val, fng_class = "50", "Neutral"

    # Precios Cripto vía Coinbase API (sin bloqueo de IP)
    try:
        r_btc = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5).json()
        btc_p = float(r_btc["data"]["amount"])
        btc_str = f"${btc_p:,.0f}"
    except Exception as e:
        print(f"⚠️ Error BTC Coinbase: {e}", flush=True)

    try:
        r_eth = requests.get("https://api.coinbase.com/v2/prices/ETH-USD/spot", timeout=5).json()
        eth_p = float(r_eth["data"]["amount"])
        eth_str = f"${eth_p:,.0f}"
    except Exception as e:
        print(f"⚠️ Error ETH Coinbase: {e}", flush=True)

    try:
        r_sol = requests.get("https://api.coinbase.com/v2/prices/SOL-USD/spot", timeout=5).json()
        sol_p = float(r_sol["data"]["amount"])
        sol_str = f"${sol_p:,.0f}"
    except Exception as e:
        print(f"⚠️ Error SOL Coinbase: {e}", flush=True)

    # Índice Fear & Greed
    try:
        r_fng = requests.get("https://api.alternative.me/fng/", headers=headers, timeout=5).json()
        fng_data = r_fng.get("data", [{}])[0]
        fng_val = fng_data.get("value", "50")
        fng_class = fng_data.get("value_classification", "Neutral")
    except Exception as e:
        print(f"⚠️ Error Fear & Greed API: {e}", flush=True)

    return (
        f"📊 <b>MÉTRICAS DE MERCADO EN VIVO</b>\n"
        f"▫️ <b>BTC:</b> {btc_str} | <b>ETH:</b> {eth_str} | <b>SOL:</b> {sol_str}\n"
        f"▫️ <b>Sentimiento (Fear & Greed):</b> {fng_val}/100 ({fng_class})\n"
        f"▫️ <b>Macro Tech:</b> Flujos orientados a infraestructura de IA y semiconductores."
    )
# ------------------------------------------------------------------------------
# LIMPIEZA AUTOMÁTICA DE MARKDOWN A HTML
# ------------------------------------------------------------------------------
def clean_markdown_to_html(text):
    if not text:
        return ""
    # Convierte **texto** a <b>texto</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    # Convierte *texto* a <i>texto</i>
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    return text

# ------------------------------------------------------------------------------
# TELEGRAM HELPERS
# ------------------------------------------------------------------------------
def send_telegram_message(chat_id, text, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return None

    url = TG_BASE_URL + "/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        res = requests.post(url, json=payload, timeout=15)
        return res.json()
    except Exception as e:
        print(f"❌ Excepción enviando mensaje Telegram: {e}", flush=True)
        return None

def create_one_time_invite_link(channel_id):
    url = TG_BASE_URL + "/createChatInviteLink"
    payload = {
        "chat_id": channel_id,
        "member_limit": 1,
        "name": "Acceso VIP Executive AI Digest"
    }
    try:
        res = requests.post(url, json=payload, timeout=10).json()
        if res.get("ok"):
            return res["result"]["invite_link"]
    except Exception as e:
        print(f"❌ Excepción creando link de invitación: {e}", flush=True)
    return None

def kick_user_from_channel(channel_id, user_id):
    ban_url = TG_BASE_URL + "/banChatMember"
    unban_url = TG_BASE_URL + "/unbanChatMember"
    try:
        requests.post(ban_url, json={"chat_id": channel_id, "user_id": user_id}, timeout=10)
        requests.post(unban_url, json={"chat_id": channel_id, "user_id": user_id, "only_if_banned": True}, timeout=10)
        print(f"🚫 Usuario {user_id} removido del canal VIP.", flush=True)
    except Exception as e:
        print(f"❌ Error al remover usuario {user_id}: {e}", flush=True)

def activate_subscriber(user_id):
    subscribers = load_json_file(SUBSCRIBERS_FILE, {})
    expiration_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    subscribers[str(user_id)] = {
        "user_id": str(user_id),
        "expiration_date": expiration_date,
        "status": "active"
    }
    save_json_file(SUBSCRIBERS_FILE, subscribers)
    invite_link = create_one_time_invite_link(TELEGRAM_VIP_CHANNEL_ID)
    
    if invite_link:
        msg = (
            f"🎉 <b>¡BIENVENIDO AL EXECUTIVE AI DIGEST VIP!</b>\n\n"
            f"Tu suscripción está activa hasta el: <code>{expiration_date}</code>\n\n"
            f"🔗 <b>Tu enlace exclusivo de ingreso al Canal VIP:</b>\n{invite_link}\n\n"
            f"⚠️ <i>Nota: Este enlace es personal y de un solo uso.</i>"
        )
    else:
        msg = "🎉 <b>¡Suscripción activada!</b> Contactá al administrador para recibir tu enlace."

    send_telegram_message(user_id, msg)
    return expiration_date

@app.route("/")
def health_check():
    return "Executive AI Digest Bot Activo 24/7", 200

# ------------------------------------------------------------------------------
# GENERACIÓN DEL BOLETÍN CONSOLIDADO CON MÉTRICAS CLAVE
# ------------------------------------------------------------------------------
def generate_consolidated_digest(raw_articles, edition_title):
    market_header = fetch_live_market_data()
    
    if not client:
        return f"🗞️ <b>{edition_title}</b>\n\n{market_header}\n\n<i>Servicio activo.</i>"

    articles_text = ""
    for i, art in enumerate(raw_articles, 1):
        articles_text += f"[{i}] Categ: {art['cat']} | Título: {art['title']} | Link: {art['link']}\nResumen: {art['desc'][:300]}\n---\n"

    prompt = f"""
    Eres el Editor Jefe de 'Executive AI Digest', un boletín premium exclusivo para ejecutivos y fundadores.
    A continuación tienes un lote de noticias internacionales recopiladas:

    {articles_text}

    TU TAREA:
    Sintetiza la información en UN ÚNICO MENSAJE CONSOLIDADO en español.

    REGLAS DE FORMATO ESTRICTAS:
    1. Usa ÚNICAMENTE etiquetas HTML (<b>, <i>, <a href="...">).
    2. JAMÁS uses asteriscos dobles ** ni bloques de código ```. Usa <b>texto</b> para resaltados en negrita.
    3. Selecciona entre 6 y 8 noticias de VERDADERO IMPACTO TECNOLÓGICO Y FINANCIERO.
    4. Cada noticia debe tener MÁXIMO 1 o 2 renglones, súper concisa, con hipervínculo HTML al final (ejemplo: <a href="LINK">[Fuente]</a>).

    ESTRUCTURA REQUERIDA:
    🗞️ <b>{edition_title}</b>
    🗓️ <i>{datetime.now().strftime('%d/%m/%Y')}</i>

    {market_header}

    ⚡ <b>RADAR GLOBAL DE NOTICIAS (EN 2 MINUTOS)</b>
    [Agrupa aquí las 6-8 noticias seleccionadas divididas por subcategorías: 🤖 IA & Tech, 💼 Negocios & Startups, 📈 Cripto & Macro]

    📈 <b>MÉTRICAS Y DATOS CLAVE DEL DÍA</b>
    • <b>[Nombre de la Métrica 1]:</b> [Cifra / Porcentaje / Cierre de ronda / Dato numérico concreto derivado de las noticias]
    • <b>[Nombre de la Métrica 2]:</b> [Dato concreto / Ciberseguridad / Inversión VC / Cómputo]

    🛠️ <b>WORKFLOW / HERRAMIENTA IA DEL DÍA</b>
    📌 <b>[Nombre de la Herramienta o Tip]:</b> [1 o 2 oraciones prácticas sobre cómo aplicar una app o prompt de IA para optimizar procesos]

    💡 <i>Executive AI Digest — Síntesis exclusiva para miembros VIP.</i>
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        content = response.choices[0].message.content.strip()

        # Limpiar bloques de código y convertir asteriscos a HTML <b>
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        content = clean_markdown_to_html(content)
        return content
    except Exception as e:
        print(f"⚠️ Error generando Digest Consolidado: {e}", flush=True)
        return None

def execute_daily_digest(edition_title):
    posted_data = load_json_file(POSTED_NEWS_FILE, {})
    if isinstance(posted_data, list):
        posted_data = {nid: datetime.now().strftime("%Y-%m-%d %H:%M:%S") for nid in posted_data}

    collected_articles = []
    
    for category, feeds in RSS_FEEDS.items():
        for feed_url in feeds:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:3]:
                    news_id = entry.id if 'id' in entry else entry.link
                    if news_id not in posted_data:
                        summary_raw = BeautifulSoup(entry.summary, "html.parser").get_text() if hasattr(entry, 'summary') else ""
                        collected_articles.append({
                            "id": news_id,
                            "cat": category,
                            "title": entry.title,
                            "desc": summary_raw,
                            "link": entry.link
                        })
            except Exception as e:
                print(f"❌ Error leyendo feed {feed_url}: {e}", flush=True)

    if collected_articles:
        print(f"📦 Procesando {len(collected_articles)} artículos para el Digest...", flush=True)
        final_post = generate_consolidated_digest(collected_articles, edition_title)
        
        if final_post:
            res = send_telegram_message(TELEGRAM_VIP_CHANNEL_ID, final_post)
            if res and res.get("ok"):
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for art in collected_articles:
                    posted_data[art["id"]] = now_str
                save_json_file(POSTED_NEWS_FILE, posted_data)
                print(f"✅ Boletín Consolidado Enviado Exitosamente a Telegram.", flush=True)
    else:
        print("⚠️ No hay noticias nuevas para compilar en este ciclo.", flush=True)

def run_digest_scheduler():
    print("🚀 Hilo iniciado: Programador de Boletín Ejecutivo Consolidado", flush=True)
    last_morning_date = ""
    last_evening_date = ""

    while True:
        now_arg = datetime.utcnow() - timedelta(hours=3)
        today_str = now_arg.strftime("%Y-%m-%d")

        if now_arg.hour == MORNING_HOUR_ARG and last_morning_date != today_str:
            print("🌅 Generando Edición Mañana...", flush=True)
            execute_daily_digest("EXECUTIVE AI DIGEST — EDICIÓN MAÑANA")
            last_morning_date = today_str

        elif now_arg.hour == EVENING_HOUR_ARG and last_evening_date != today_str:
            print("🌙 Generando Edición Cierre...", flush=True)
            execute_daily_digest("EXECUTIVE AI DIGEST — EDICIÓN CIERRE")
            last_evening_date = today_str

        time.sleep(60)

# ------------------------------------------------------------------------------
# LISTENER TELEGRAM Y VENCIMIENTOS
# ------------------------------------------------------------------------------
def run_telegram_listener():
    print("🎧 Hilo iniciado: Bot Listener de Telegram", flush=True)
    
    try:
        requests.get(TG_BASE_URL + "/deleteWebhook", timeout=5)
    except Exception:
        pass

    offset = None
    while True:
        try:
            url = TG_BASE_URL + "/getUpdates"
            params = {"timeout": 20, "offset": offset}
            response = requests.get(url, params=params, timeout=25).json()

            if not response.get("ok"):
                time.sleep(5)
                continue

            for update in response.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if message and "text" in message:
                    chat_id = message["chat"]["id"]
                    text = message["text"].strip()

                    if text.lower() in ["/start", "/suscribirse", "suscribirme"]:
                        msg = (
                            f"🗞️ <b>EXECUTIVE AI DIGEST — CANAL VIP</b>\n\n"
                            f"Accedé a la síntesis diaria consolidada sobre IA, Negocios, Startups, Cripto y Mercados en tiempo real.\n\n"
                            f"💰 <b>Precio Suscripción:</b> ${SUBSCRIPTION_PRICE:,.0f} ARS / mes.\n\n"
                            f"📌 <b>Tu ID de Usuario:</b> <code>{chat_id}</code>"
                        )
                        reply_markup = {
                            "inline_keyboard": [
                                [{"text": f"💳 SUSCRIBIRME (${SUBSCRIPTION_PRICE:,.0f} ARS)", "url": MP_FIXED_LINK}]
                            ]
                        }
                        send_telegram_message(chat_id, msg, reply_markup=reply_markup)

                    elif text.startswith("/burst"):
                        send_telegram_message(chat_id, "🚀 Compilando y generando el Boletín Consolidado VIP...")
                        execute_daily_digest("EXECUTIVE AI DIGEST — EDICIÓN ESPECIAL")

                    elif text.startswith("/activar"):
                        parts = text.split()
                        if len(parts) > 1:
                            target_id = parts[1]
                            exp = activate_subscriber(target_id)
                            send_telegram_message(chat_id, f"✅ Usuario <code>{target_id}</code> activado con éxito hasta <code>{exp}</code>.")
                        else:
                            send_telegram_message(chat_id, "⚠️ Uso: <code>/activar ID_DEL_USUARIO</code>")

                    elif text.lower() in ["/estado", "/mi_estado"]:
                        subscribers = load_json_file(SUBSCRIBERS_FILE, {})
                        sub = subscribers.get(str(chat_id))
                        if sub and sub.get("status") == "active":
                            send_telegram_message(chat_id, f"✅ Tu suscripción VIP está <b>ACTIVA</b> hasta: <code>{sub.get('expiration_date')}</code>")
                        else:
                            send_telegram_message(chat_id, "❌ No tenés una suscripción activa. Usá /suscribirse para unirte.")
        except Exception as e:
            print(f"❌ Error Listener Digest: {e}", flush=True)
            time.sleep(5)

def run_expiration_checker():
    print("🕒 Hilo iniciado: Verificador de Vencimientos", flush=True)
    while True:
        subscribers = load_json_file(SUBSCRIBERS_FILE, {})
        now = datetime.now()
        updated = False
        for user_id, data in list(subscribers.items()):
            if data.get("status") == "active":
                exp_date = datetime.strptime(data["expiration_date"], "%Y-%m-%d %H:%M:%S")
                if now > exp_date:
                    kick_user_from_channel(TELEGRAM_VIP_CHANNEL_ID, user_id)
                    send_telegram_message(user_id, "🔴 <b>TU SUSCRIPCIÓN VIP AL DIGEST HA VENCIDO</b>\n\nUsá /suscribirse para renovar tu acceso.")
                    data["status"] = "expired"
                    updated = True
        if updated:
            save_json_file(SUBSCRIBERS_FILE, subscribers)
        time.sleep(43200)

def start_background_threads():
    threading.Thread(target=run_telegram_listener, daemon=True).start()
    threading.Thread(target=run_digest_scheduler, daemon=True).start()
    threading.Thread(target=run_expiration_checker, daemon=True).start()

start_background_threads()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
