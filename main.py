import os
import json
import time
import threading
import sys
from datetime import datetime, timedelta
import requests
import feedparser
from bs4 import BeautifulSoup
from openai import OpenAI
from flask import Flask, request, jsonify

# Desactivar buffering para ver logs en tiempo real en Render
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
MORNING_HOUR_ARG = 8   # 08:00 AM -> 3 noticias de alto impacto
EVENING_HOUR_ARG = 21  # 21:00 PM -> 2 noticias de cierre

# ------------------------------------------------------------------------------
# FUENTES DE ALTA SEÑAL (HIGH-SIGNAL RSS FEEDS)
# ------------------------------------------------------------------------------
RSS_FEEDS = {
    "🤖 <b>IA & ESTRATEGIA TECNOLÓGICA</b>": [
        "https://www.technologyreview.com/feed/",
        "https://venturebeat.com/category/ai/feed/",
        "https://arstechnica.com/technology/feed/"
    ],
    "💼 <b>NEGOCIOS & MACROECONOMÍA</b>": [
        "https://techcrunch.com/category/startups/feed/",
        "https://restofworld.org/feed/latest",
        "https://feeds.feedburner.com/entrepreneur/latest"
    ],
    "📈 <b>FINANZAS & CRIPTO ESTRUCTURAL</b>": [
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
# TELEGRAM HELPERS (FORMATO HTML)
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
# PROCESAMIENTO ANALÍTICO E INTELIGENCIA DE NEGOCIOS
# ------------------------------------------------------------------------------
def generate_executive_digest_ai(category_html, title, description, raw_link):
    if not client:
        return None

    prompt = f"""
    Eres un Analista Estratégico Senior de Tecnología, Macroeconomía y Negocios Globales.
    Tu tarea es evaluar, traducir y sintetizar la siguiente noticia con rigor técnico y criterio analítico.

    Título original: {title}
    Texto original: {description}
    Enlace: {raw_link}

    PASO 1: FILTRO EDITORIAL CRÍTICO
    - Si la noticia es sobre deportes, farándula, notas de opinión personal, comunicados de prensa vacíos o no tiene un impacto tecnológico/financiero/empresarial real, responde ÚNICAMENTE con la palabra: DESCARTAR

    PASO 2: REDACCIÓN ANALÍTICA DE ALTO NIVEL (Si pasa el filtro)
    - Prohibido usar frases de relleno como "los ejecutivos deben considerar", "se abren oportunidades" o "es relevante para líderes".
    - Enfócate en datos concretos, implicaciones técnicas, arquitectura de software, movimientos de capital o cambios normativos.
    - Formato estrictamente en HTML de Telegram (<b>, <i>, <a href="...">). Sin bloques de código ```.

    Estructura requerida:
    {category_html}
    📌 <b>Acontecimiento Clave:</b> [Síntesis precisa del hecho técnico o financiero en 1-2 oraciones]
    🔍 <b>Contexto e Impacto Estratégico:</b> [Por qué altera las reglas del juego. Mencioná cifras, arquitecturas, competidores o impacto en costos si aplica]
    💡 <b>Perspectiva Futura:</b> [Análisis contemplativo sobre la tendencia a mediano/largo plazo que esto genera]

    🔗 <a href="{raw_link}">👉 LEER INFORME ORIGINAL</a>
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        content = response.choices[0].message.content.strip()

        # Si la IA determina que la noticia no tiene nivel ejecutivo, se descarta
        if "DESCARTAR" in content.upper():
            return "DESCARTAR"

        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        return content
    except Exception as e:
        print(f"⚠️ Error OpenAI Digest: {e}", flush=True)
        return "DESCARTAR"

def execute_news_burst(count_to_send, header_title):
    posted_data = load_json_file(POSTED_NEWS_FILE, {})
    if isinstance(posted_data, list):
        posted_data = {nid: datetime.now().strftime("%Y-%m-%d %H:%M:%S") for nid in posted_data}

    send_telegram_message(TELEGRAM_VIP_CHANNEL_ID, f"🌅 <b>{header_title}</b>\n<i>Análisis estratégico de novedades globales seleccionadas por IA:</i>")
    time.sleep(3)

    sent_count = 0
    for category, feeds in RSS_FEEDS.items():
        if sent_count >= count_to_send:
            break
        for feed_url in feeds:
            if sent_count >= count_to_send:
                break
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:8]: # Revisa hasta 8 noticias por feed buscando calidad
                    news_id = entry.id if 'id' in entry else entry.link
                    if news_id not in posted_data:
                        summary_raw = BeautifulSoup(entry.summary, "html.parser").get_text() if hasattr(entry, 'summary') else ""
                        
                        # Generación con Filtro Editorial
                        post_html = generate_executive_digest_ai(category, entry.title, summary_raw, entry.link)
                        
                        # Si fue descartada por falta de relevancia, pasa a la siguiente noticia
                        if not post_html or post_html == "DESCARTAR":
                            print(f"⏩ Noticia descartada por bajo valor ejecutivo: {entry.title}", flush=True)
                            posted_data[news_id] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            save_json_file(POSTED_NEWS_FILE, posted_data)
                            continue

                        res = send_telegram_message(TELEGRAM_VIP_CHANNEL_ID, post_html)
                        if res and res.get("ok"):
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            posted_data[news_id] = now_str
                            save_json_file(POSTED_NEWS_FILE, posted_data)
                            sent_count += 1
                            print(f"✅ Noticia de alto valor enviada ({sent_count}/{count_to_send}): {entry.title}", flush=True)
                            time.sleep(4)
                            break
            except Exception as e:
                print(f"❌ Error feed {feed_url}: {e}", flush=True)

def run_digest_scheduler():
    print("🚀 Hilo iniciado: Programador de Ráfagas con Filtro Editorial", flush=True)
    last_morning_date = ""
    last_evening_date = ""

    while True:
        now_arg = datetime.utcnow() - timedelta(hours=3)
        today_str = now_arg.strftime("%Y-%m-%d")

        if now_arg.hour == MORNING_HOUR_ARG and last_morning_date != today_str:
            print("🌅 Ejecutando Ráfaga de la Mañana (08:00 HS)...", flush=True)
            execute_news_burst(3, "EDICIÓN MAÑANA — EXECUTIVE AI DIGEST")
            last_morning_date = today_str

        elif now_arg.hour == EVENING_HOUR_ARG and last_evening_date != today_str:
            print("🌙 Ejecutando Ráfaga de la Noche (21:00 HS)...", flush=True)
            execute_news_burst(2, "EDICIÓN CIERRE — EXECUTIVE AI DIGEST")
            last_evening_date = today_str

        time.sleep(60)

# ------------------------------------------------------------------------------
# LISTENER TELEGRAM Y CONTROL DE VENCIMIENTOS
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
                            f"Accedé a 2 entregas diarias (08:00 y 21:00 HS) con análisis estratégicos de alto valor sobre IA, Negocios y Finanzas.\n\n"
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
                        send_telegram_message(chat_id, "🚀 Ejecutando ráfaga analítica con filtro editorial...")
                        execute_news_burst(3, "EDICIÓN ANALÍTICA — EXECUTIVE AI DIGEST")

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
