from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, FlexSendMessage, QuickReply, QuickReplyButton, MessageAction
)
from linebot.exceptions import InvalidSignatureError
import json
from oauth2client.service_account import ServiceAccountCredentials
import gspread
from datetime import datetime, timedelta
import pytz
import os
import base64

app = FastAPI()
line_bot_api = LineBotApi(os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))
user_state = {}

def get_gspread_client_from_env():
    encoded = os.getenv("GOOGLE_CREDENTIALS_BASE64")
    decoded = base64.b64decode(encoded)
    credentials_dict = json.loads(decoded)
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
    return gspread.authorize(creds)

gc = get_gspread_client_from_env()
sheet = gc.open("記帳表單").sheet1

def get_all_records():
    return sheet.get_all_records()

def to_dash_date(s):
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s

def filter_by_date(records, date_str):
    return [r for r in records if r["日期"] == date_str]

def record_expense(category, item, amount, note):
    now = datetime.now(pytz.timezone("Asia/Taipei"))
    date = now.strftime("%Y-%m-%d")
    row = [date, category, item, amount, note]
    sheet.append_row(row)
    return date

def create_flex_list(records):
    bubbles = []
    for idx, r in enumerate(records[:10]):
        row = idx + 2
        b = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": f"📝 編號：{row}"},
                    {"type": "text", "text": f"📅 {r['日期']}"},
                    {"type": "text", "text": f"📂 {r['類別']}"},
                    {"type": "text", "text": f"📝 {r['項目']}"},
                    {"type": "text", "text": f"💰 {r['金額']}"},
                    {"type": "text", "text": f"🗒️ {r['備註']}"}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#FF4444",
                        "action": {"type": "message", "label": "🗑 刪除", "text": f"刪除 {row}"}
                    },
                    *[{
                        "type": "button",
                        "style": "secondary",
                        "action": {"type": "message", "label": f"✏️ 修改{field}", "text": f"修改 {row} {field} "}
                    } for field in ["類別", "項目", "金額", "備註"]]
                ]
            }
        }
        bubbles.append(b)
    return {"type": "carousel", "contents": bubbles}

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
    user_id = event.source.user_id
    now = datetime.now(pytz.timezone("Asia/Taipei"))
    records = get_all_records()

    # ➕ 引導式新增
    if text == "記帳 ":
        user_state[user_id] = {"step": "wait_category"}
        reply = TextSendMessage(
            text="請選擇類別（食 / 衣 / 住 / 行 / 育 / 樂）",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label=cat, text=cat)) for cat in ["食", "衣", "住", "行", "育", "樂"]
            ])
        )
        line_bot_api.reply_message(event.reply_token, reply)
        return

    if user_id in user_state and user_state[user_id].get("step") == "wait_category":
        user_state[user_id]["category"] = text
        user_state[user_id]["step"] = "wait_detail"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入：項目 金額 備註 例如：早餐 50 早餐店"))
        return

    if user_id in user_state and user_state[user_id].get("step") == "wait_detail":
        try:
            item, amount_str, note = text.split(maxsplit=2)
            amount = int(amount_str)
            category = user_state[user_id]["category"]
            date = record_expense(category, item, amount, note)
            msg = f"✅ 記帳成功 📅{date} 📂{category} 📝{item} 💰{amount} 🗒️{note}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 記帳失敗：{e}"))
        user_state.pop(user_id)
        return

    # 查詢功能
    if text.startswith("查詢"):
        try:
            target = text.split()[1] if len(text.split()) > 1 else now.strftime("%Y-%m-%d")
            date_str = to_dash_date(target)
            matched = filter_by_date(records, date_str)
            if not matched:
                raise ValueError(f"{date_str} 沒有紀錄")
            flex = create_flex_list(matched)
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="查詢結果", contents=flex))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ {e}"))
        return

    # 刪除
    if text.startswith("刪除"):
        try:
            row = int(text.split()[1])
            sheet.delete_rows(row)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已刪除第 {row} 列"))
        except:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請輸入格式：刪除 3"))
        return

    # 修改
    if text.startswith("修改"):
        try:
            _, row_str, field, *val_parts = text.split()
            value = " ".join(val_parts)
            col_map = {"日期": 1, "類別": 2, "項目": 3, "金額": 4, "備註": 5}
            col = col_map.get(field)
            if not col:
                raise ValueError("欄位名稱錯誤，請用：日期 類別 項目 金額 備註")
            sheet.update_cell(int(row_str), col, value)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"✅ 第 {row_str} 列已更新 {field} → {value}"
            ))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 修改失敗：{e}"))
        return

    # 預設選單
    menu = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "📌 請選擇操作功能", "weight": "bold", "size": "lg", "align": "center"},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "➕ 新增記帳", "text": "記帳 "}},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "📋 查詢紀錄", "text": "查詢"}},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "📊 統計分析", "text": "統計"}},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "🗑️ 刪除紀錄", "text": "刪除 2"}},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "✏️ 修改紀錄", "text": "修改 2 金額 999"}}
            ]
        }
    }
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="請選擇操作功能", contents=menu))