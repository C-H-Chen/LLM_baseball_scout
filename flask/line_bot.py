import os
import threading
import traceback
import uuid
import time
from typing import List, Optional
import importlib

from flask import Flask, request, jsonify, send_file, abort
from linebot import LineBotApi
from linebot.models import TextSendMessage
from linebot.exceptions import LineBotApiError

app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN")
RENDER_BASE_URL = os.environ.get("RENDER_BASE_URL", "").rstrip("/") 
DOWNLOAD_DIR = "/tmp/line_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)

# Helpers
def utf16_len(s: str) -> int:

    # 計算 LINE 真正計算的長度：UTF-16 code units。

    if not s:
        return 0
    return len(s.encode("utf-16-le")) // 2

def chunk_text_by_chars(text: str, max_len: int = 4999) -> List[str]:
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_len
        chunks.append(text[start:end])
        start = end
    return chunks

def _extract_target_id(ev: dict) -> Optional[str]:
    src = ev.get("source") or {}
    return src.get("userId") or src.get("groupId") or src.get("roomId")

def safe_push_single(to_id: str, text: str, max_retries: int = 6, wait_s: float = 2.5):
    if not to_id:
        print("⚠️ skip push: empty to_id")
        return False
    for attempt in range(1, max_retries + 1):
        try:
            line_bot_api.push_message(to_id, TextSendMessage(text=text))
            print(f"✅ push OK -> {to_id} (len={len(text)})")
            return True
        except LineBotApiError as e:
            status = getattr(e, "status_code", None)
            print(f"push LineBotApiError #{attempt}/{max_retries} status={status} resp={getattr(e,'error_response',None)}")
            if status and 400 <= status < 500:
                print("🛑 client error，停止重試此段")
                return False
        except Exception as e:
            print(f"push exception #{attempt}/{max_retries}: {e}")
            traceback.print_exc()
        time.sleep(wait_s)
    print("❌ push 最後仍失敗")
    return False

# 刪除檔案（由 timer 呼叫）
def _delete_download_file(file_id: str):
    try:
        txt_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.txt")
        if os.path.exists(txt_path):
            os.remove(txt_path)
            print(f"🗑️ 已自動刪除檔案 {txt_path}")
    except Exception as e:
        print("delete_download_file exception:", e)
        traceback.print_exc()

def save_text_and_get_url(text: str, lifetime_seconds: int = 600) -> str:

    # 儲存檔案並排程在 lifetime_seconds 後自動刪除（使用 threading.Timer）
    # 回傳可由 /download/<id> 存取的 URL。

    file_id = str(uuid.uuid4())
    filename = f"{file_id}.txt"
    path = os.path.join(DOWNLOAD_DIR, filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        print("save_text_and_get_url write fail:", e)
        traceback.print_exc()
        raise

    # 啟動單次 Timer，在到期時刪除檔案
    try:
        timer = threading.Timer(lifetime_seconds, _delete_download_file, args=(file_id,))
        timer.daemon = True
        timer.start()
        print(f"🕒 已排程檔案 {file_id} 在 {lifetime_seconds}s 後自動刪除")
    except Exception as e:
        print("save_text_and_get_url timer start failed:", e)
        traceback.print_exc()

    if not RENDER_BASE_URL:
        print("⚠️ RENDER_BASE_URL not set; download URL will be local path (not accessible externally)")
        return f"/download/{file_id}"
    return f"{RENDER_BASE_URL}/download/{file_id}"

# 背景處理：運算並以 single-message or download-link 回傳
def background_process_and_push(question: str, target_id: str):
    try:
        print("背景：lazy import main ...")
        main = importlib.import_module("main")
    except Exception as e:
        print("無法 import main:", e)
        traceback.print_exc()
        if target_id:
            safe_push_single(target_id, f"❌ 系統無法啟動：{e}")
        return

    try:
        print(f"背景開始處理（to={target_id}）：{question}")
        answer = main.get_answer(question, user_id=target_id or "default")
        if not answer:
            answer = "❌ 系統在產生回覆時發生錯誤，請稍後再試。"

        ulen = utf16_len(answer)
        print(f"回答 UTF-16 長度：{ulen}")

        if ulen <= 5000:
            ok = safe_push_single(target_id, answer)
            if not ok:
                parts = chunk_text_by_chars(answer, 4000)
                for p in parts:
                    safe_push_single(target_id, p)
        else:
            download_url = save_text_and_get_url(answer, lifetime_seconds=600)  # 10 分鐘
            snippet = answer[:1500]
            msg = f"📄 回答內容太長，請點此下載完整回答（連結 10 分鐘後失效）：\n{download_url}\n\n（預覽）\n{snippet}...\n"
            safe_push_single(target_id, msg)

    except Exception as e:
        print("background_process_and_push 例外：", e)
        traceback.print_exc()
        try:
            if target_id:
                line_bot_api.push_message(target_id, TextSendMessage(text=f"❌ 內部錯誤：{e}"))
        except Exception:
            pass

# Webhook route (由 Worker/DO 轉送)
@app.route("/callback", methods=["POST"])
def callback():
    proxy_from = request.headers.get("x-proxy-from", "")
    thinking_sent = request.headers.get("x-thinking-sent", "0")
    thinking_method = request.headers.get("x-thinking-method", "none")
    print(f"/callback headers: x-proxy-from={proxy_from}, x-thinking-sent={thinking_sent}, x-thinking-method={thinking_method}")

    body = request.get_json(silent=True, force=True)
    if not body:
        print("/callback: no JSON body")
        return jsonify({"status": "no body"}), 400

    events = body.get("events", [])
    for ev in events:
        try:
            if ev.get("type") != "message":
                continue
            msg = ev.get("message") or {}
            if msg.get("type") != "text":
                continue

            question = (msg.get("text") or "").strip()
            if question == "名單":
                continue

            to_id = _extract_target_id(ev)
            if not to_id:
                print("⚠️ 無有效 target id，跳過")
                continue

            if thinking_sent != "1":
                try:
                    safe_push_single(to_id, "📊 思考分析中，請稍候...")
                except Exception as e:
                    print("後端補發 thinking 失敗：", e)
                    traceback.print_exc()

            t = threading.Thread(target=background_process_and_push, args=(question, to_id))
            t.daemon = False
            t.start()

        except Exception as e:
            print("處理 event 發生錯誤：", e)
            traceback.print_exc()

    return "OK", 200


# 下載 endpoint（使用者點連結時由 Render 提供檔案）
@app.route("/download/<file_id>", methods=["GET"])
def download_file(file_id):
    txt_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.txt")
    if not os.path.exists(txt_path):
        return abort(404)
    # 直接回傳檔案（若檔案已被 timer 刪除則 404）
    return send_file(txt_path, mimetype="text/plain", as_attachment=True, download_name=f"answer_{file_id}.txt")

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Line bot is running."}), 200
