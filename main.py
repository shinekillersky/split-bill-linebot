from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError
import os
from dotenv import load_dotenv
import gspread
from google.oauth2 import service_account
from datetime import datetime
import json

load_dotenv()

app = FastAPI()

# LINE Bot 設定
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# GOOGLE_CREDENTIALS 驗證與初始化
if "GOOGLE_CREDENTIALS" not in os.environ:
    raise EnvironmentError("❌ GOOGLE_CREDENTIALS 環境變數不存在")

try:
    credentials_info = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    if "private_key" not in credentials_info:
        raise ValueError("❌ private_key 不存在，或格式錯誤")
except Exception as e:
    raise RuntimeError(f"❌ GOOGLE_CREDENTIALS 格式錯誤: {str(e)}")

# Google Sheets 認證與初始化
credentials = service_account.Credentials.from_service_account_info(
    credentials_info,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
client = gspread.authorize(credentials)
sheet = client.open_by_key(os.getenv("SPREADSHEET_ID")).sheet1

@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get('X-Line-Signature')
    body = await request.body()
    try:
        handler.handle(body.decode(), signature)
    except InvalidSignatureError:
        return 'Invalid signature'
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    user_id = event.source.user_id
    reply_token = event.reply_token

    if msg.startswith("新增"):
        try:
            _, group, activity, username, name, amount, note = msg.split()
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([group, activity, username, name, amount, note, timestamp])
            reply = TextSendMessage(text="資料已新增。")
        except:
            reply = TextSendMessage(text="格式錯誤，請使用：新增 群組 活動 username 名稱 金額 備註")
        line_bot_api.reply_message(reply_token, reply)

    elif msg == "查詢":
        records = sheet.get_all_records()
        user_records = [r for r in records if r['username'] == user_id]
        if user_records:
            messages = [TextSendMessage(text=json.dumps(r, ensure_ascii=False)) for r in user_records]
        else:
            messages = [TextSendMessage(text="查無資料。")]
        line_bot_api.reply_message(reply_token, messages)

    elif msg == "結算":
        records = sheet.get_all_records()
        summary = {}
        for r in records:
            key = r['username']
            summary[key] = summary.get(key, 0) + float(r['金額'])
        summary_text = "\n".join([f"{k}: {v}" for k, v in summary.items()])
        reply = TextSendMessage(text=summary_text)
        line_bot_api.reply_message(reply_token, reply)

    elif msg == "重置":
        sheet.clear()
        sheet.append_row(['群組名稱', '活動', 'username', '名稱', '金額', '備註', '時間'])
        reply = TextSendMessage(text="資料已重置。")
        line_bot_api.reply_message(reply_token, reply)

    else:
        reply = TextSendMessage(text="請輸入有效的指令。")
        line_bot_api.reply_message(reply_token, reply)
