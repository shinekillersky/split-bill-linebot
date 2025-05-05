from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
from linebot.exceptions import InvalidSignatureError
import gspread
from datetime import datetime
import pytz
import os

app = FastAPI()
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))
gc = gspread.service_account(filename="gspread-service-account.json")
sheet = gc.open("記帳表單").sheet1

def validate_category(category: str) -> str:
    allowed_categories = ['食', '衣', '住', '行', '育', '樂']
    if category not in allowed_categories:
        raise ValueError(f"類別「{category}」錯誤，請使用：{', '.join(allowed_categories)}")
    return category

def record_expense(sheet, category, item, amount, note):
    now = datetime.now(pytz.timezone("Asia/Taipei"))
    date = now.strftime("%Y-%m-%d")
    category = validate_category(category)
    row = [date, category, item, amount, note]
    sheet.append_row(row)

def create_flex_response(date, category, item, amount, note):
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "✅ 記帳成功", "weight": "bold", "size": "xl"},
                {"type": "separator"},
                {"type": "text", "text": f"📅 日期：{date}"},
                {"type": "text", "text": f"📂 類別：{category}"},
                {"type": "text", "text": f"📝 項目：{item}"},
                {"type": "text", "text": f"💰 金額：{amount}"},
                {"type": "text", "text": f"🗒️ 備註：{note}"},
            ]
        }
    }

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers["X-Line-Signature"]
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    if text.startswith("記帳"):
        try:
            _, category, item, amount_str, note = text.split(maxsplit=4)
            amount = int(amount_str)
            record_expense(sheet, category, item, amount, note)
            now = datetime.now(pytz.timezone("Asia/Taipei"))
            date = now.strftime("%Y-%m-%d")
            flex_msg = create_flex_response(date, category, item, amount, note)
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text="記帳成功", contents=flex_msg)
            )
        except ValueError as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"⚠️ 記帳失敗：{e}")
            )
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"❌ 發生錯誤：{e}")
            )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請使用格式：\n記帳 類別 項目 金額 備註")
        )