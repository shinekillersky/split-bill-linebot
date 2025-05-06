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

def record_expense(item, amount, note):
    now = datetime.now(pytz.timezone("Asia/Taipei"))
    date = now.strftime("%Y-%m-%d")
    row = [date, item, amount, note]
    sheet.append_row(row)
    return date

# ✅ 讓 create_flex_list 支援指定 row 編號
def create_flex_list(records, start_row=2):
    bubbles = []
    for idx, r in enumerate(records):
        row = start_row + idx
        b = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": f"📝 第 {row - 1} 筆資料"},
                    {"type": "text", "text": f"📅 {r['日期']}"},
                    {"type": "text", "text": f"📝 {r['項目']}"},
                    {"type": "text", "text": f"💰 {r['金額']}"},
                    {"type": "text", "text": f"🗒️ {r['備註']}"}
                ]
            }
        }
        bubbles.append(b)
    return {"type": "carousel", "contents": bubbles}

    # ✅ 呼叫時帶入真實 row
    flex = create_flex_list([record], start_row=real_row_number)

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

    if text == "新增":
        user_state[user_id] = {"step": "wait_detail"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請輸入：項目 金額 備註，例如：\n早餐 80 QBurger"
        ))
        return

    if user_id in user_state and user_state[user_id].get("step") == "wait_detail":
        try:
            item, amount_str, note = text.split(maxsplit=2)
            amount = int(amount_str)
            date = record_expense(item, amount, note)
            msg = f"✅ 記帳成功 📅{date} 📝{item} 💰{amount} 🗒️{note}"

            # 🔽 新增一筆後，馬上查出最後一筆資料
            all_rows = sheet.get_all_values()
            last_row = all_rows[-1]
            real_row_number = len(all_rows) # ✅ 真實的行數

            record = {
                "日期": last_row[0],
                "項目": last_row[1],
                "金額": last_row[2],
                "備註": last_row[3]
            }
            flex = create_flex_list([record])

            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=msg),
                FlexSendMessage(alt_text="新增記錄", contents=flex["contents"][0])
            ])
            user_state.pop(user_id)
        except Exception:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 格式錯誤，請重新輸入：項目 金額 備註，例如：\n早餐 80 QBurger")
            )
        return

        # 使用者輸入「查詢」 → 顯示 quick reply 日期選擇
        if text == "查詢":
            today = now.strftime("%Y%m%d")
            yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="請選擇要查詢的日期：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="今天", text=f"查詢 {today}")),
                    QuickReplyButton(action=MessageAction(label="昨天", text=f"查詢 {yesterday}")),
                    QuickReplyButton(action=MessageAction(label="自訂日期", text="查詢 自訂"))
                ])
            ))
            return

        # 如果是查詢 自訂 → 要求輸入日期
        if text.strip() == "查詢 自訂":
            user_state[user_id] = {"step": "wait_custom_query_date"}
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="請輸入要查詢的日期（格式：20240510）"
            ))
            return

        # 處理自訂日期的輸入
        if user_id in user_state and user_state[user_id].get("step") == "wait_custom_query_date":
            try:
                date_str = to_dash_date(text.strip())
                matched = filter_by_date(records, date_str)
                if not matched:
                    raise ValueError(f"{date_str} 沒有紀錄")
                flex = create_flex_list(matched)
                line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="查詢結果", contents=flex))
            except Exception as e:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ {e}"))
            user_state.pop(user_id)
            return    

    # 使用者輸入「修改」→ 進入引導模式
    if text == "修改":
        user_state[user_id] = {"step": "wait_modify_row"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請輸入你要修改的第幾筆資料（例如：2）"
        ))
        return

    # 使用者輸入了要修改的 row
    if user_id in user_state and user_state[user_id].get("step") == "wait_modify_row":
        try:
            row = int(text.strip())
            user_state[user_id]["row"] = row
            user_state[user_id]["step"] = "wait_modify_field"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="請輸入你要修改的欄位（可選：項目、金額、備註）"
            ))
        except:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="❌ 請輸入有效的數字，例如：2"
            ))
        return

    # 使用者輸入了欄位名稱
    if user_id in user_state and user_state[user_id].get("step") == "wait_modify_field":
        field = text.strip()
        col_map = {"項目": 3, "金額": 4, "備註": 5}
        if field not in col_map:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="❌ 欄位名稱錯誤，請輸入：項目、金額 或 備註"
            ))
            return
        user_state[user_id]["field"] = field
        user_state[user_id]["step"] = "wait_modify_value"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text=f"請輸入新的「{field}」內容："
        ))
        return

    # 使用者輸入了新值 → 執行修改 + 回傳結果
    if user_id in user_state and user_state[user_id].get("step") == "wait_modify_value":
        try:
            row = user_state[user_id]["row"]
            field = user_state[user_id]["field"]
            value = text.strip()
            col_map = {"項目": 3, "金額": 4, "備註": 5}
            col = col_map[field]
            sheet.update_cell(row, col, value)

            # 查出更新後資料
            row_data = sheet.row_values(row)
            record = {
                "日期": row_data[0],
                "項目": row_data[1],
                "金額": row_data[2],
                "備註": row_data[3]

            }
            bubble = create_flex_list([record])
            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=f"✅ 第 {row} 筆資料的「{field}」已更新為：{value}"),
                FlexSendMessage(alt_text="更新後資料", contents=bubble["contents"][0])
            ])
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 修改失敗：{e}"))
        user_state.pop(user_id)
        return

    # ➖ 引導式刪除第一步：輸入「刪除」
    if text == "刪除":
        user_state[user_id] = {"step": "wait_delete_row"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請輸入你要刪除的第幾筆資料（例如：2）"
        ))
        return

    # ➖ 第二步：輸入要刪除的 row 編號
    if user_id in user_state and user_state[user_id].get("step") == "wait_delete_row":
        try:
            row = int(text.strip())
            sheet.delete_rows(row)
            user_state.pop(user_id)

            # 🔁 回覆刪除成功並跳出主選單
            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=f"✅ 已成功刪除第 {row} 筆資料"),
                FlexSendMessage(alt_text="選單", contents={
                    "type": "bubble",
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "md",
                        "contents": [
                            {"type": "text", "text": "📌 請選擇操作功能", "weight": "bold", "size": "lg", "align": "center"},
                            {"type": "button", "style": "primary", "action": {"type": "message", "label": "➕ 新增", "text": "新增"}},
                            {"type": "button", "style": "primary", "action": {"type": "message", "label": "📋 查詢", "text": "查詢"}},
                            {"type": "button", "style": "primary", "action": {"type": "message", "label": "✏️ 修改", "text": "修改"}},
                            {"type": "button", "style": "primary", "action": {"type": "message", "label": "🗑️ 刪除", "text": "刪除"}},
                            {"type": "button", "style": "primary", "action": {"type": "message", "label": "📊 統計", "text": "統計"}}
                        ]
                    }
                })
            ])
        except:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請輸入有效數字，例如：2"))
        return
    
    # ✅ 統計：第一階段 QuickReply 日期選擇
    if text == "統計":
        today = now.strftime("%Y%m%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請選擇要統計的日期：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="今天", text=f"統計 {today}")),
                QuickReplyButton(action=MessageAction(label="昨天", text=f"統計 {yesterday}")),
                QuickReplyButton(action=MessageAction(label="自訂日期", text="統計 自訂"))
            ])
        ))
        return

    # ✅ 統計：進入自訂日期狀態
    if text.strip() == "統計 自訂":
        user_state[user_id] = {"step": "wait_custom_stat_date"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請輸入日期（格式：20240510）"
        ))
        return

    # ✅ 統計：接收自訂日期後執行
    if user_id in user_state and user_state[user_id].get("step") == "wait_custom_stat_date":
        try:
            target_date = to_dash_date(text.strip())
            matched = filter_by_date(get_all_records(), target_date)
            if not matched:
                raise ValueError(f"{target_date} 沒有資料")

            total = sum(int(r["金額"]) for r in matched)
            per_item = {}
            for r in matched:
                name = r["項目"]
                per_item[name] = per_item.get(name, 0) + int(r["金額"])

            detail = "\n".join([f"{k}: {v}" for k, v in per_item.items()])
            msg = f"📊 統計日期：{target_date}\n總金額：{total} 元\n\n明細：\n{detail}"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ {e}"))
        user_state.pop(user_id)
        return

    # ✅ 統計：快速查詢今天 / 昨天
    if text.startswith("統計 "):
        try:
            date_str = to_dash_date(text.split()[1])
            matched = filter_by_date(get_all_records(), date_str)
            if not matched:
                raise ValueError(f"{date_str} 沒有資料")

            total = sum(int(r["金額"]) for r in matched)
            per_item = {}
            for r in matched:
                name = r["項目"]
                per_item[name] = per_item.get(name, 0) + int(r["金額"])

            detail = "\n".join([f"{k}: {v}" for k, v in per_item.items()])
            msg = f"📊 統計日期：{date_str}\n總金額：{total} 元\n\n明細：\n{detail}"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ {e}"))
        return

    menu = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "📌 請選擇操作功能", "weight": "bold", "size": "lg", "align": "center"},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "➕ 新增", "text": "新增"}},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "📋 查詢", "text": "查詢"}},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "✏️ 修改", "text": "修改"}},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "🗑️ 刪除", "text": "刪除"}},
                {"type": "button", "style": "primary", "action": {"type": "message", "label": "📊 統計", "text": "統計"}}
            ]
        }            
    }    

    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="請選擇操作功能", contents=menu))

@app.get("/health")
async def health_check():
    return {"status": "ok"}