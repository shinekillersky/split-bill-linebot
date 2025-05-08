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

# 主選單
def get_main_menu():
    return {
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

# 選單
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
                    {"type": "text", "text": f"📝 第 {row - 1} 筆"},
                    {"type": "text", "text": f"📅 {r['日期']}"},
                    {"type": "text", "text": f"📝 {r['項目']}"},
                    {"type": "text", "text": f"💰 {r['金額']}"},
                    {"type": "text", "text": f"🗒️ {r['備註']}"}
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

    # ✅ 新增功能：可直接記帳或進入引導輸入模式
    if text.startswith("新增"):
        parts = text.split(maxsplit=3)

        # ✅ 格式：新增 項目 金額 [備註]
        if len(parts) >= 3:
            _, item, amount_str = parts[:3]
            note = parts[3] if len(parts) == 4 else ""
            try:
                amount = int(amount_str)
                date = record_expense(item, amount, note)
                msg = f"✅ 記帳成功"

                all_rows = sheet.get_all_values()
                last_row = all_rows[-1]
                real_row_number = len(all_rows)

                record = {
                    "日期": last_row[0],
                    "項目": last_row[1],
                    "金額": last_row[2],
                    "備註": last_row[3]
                }
                flex = create_flex_list([record], start_row=real_row_number)

                line_bot_api.reply_message(event.reply_token, [
                    TextSendMessage(text=msg),
                    FlexSendMessage(alt_text="新增記錄", contents=flex["contents"][0])
                ])
            except:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="❌ 金額格式錯誤，請輸入：新增 項目 金額 [備註]，例如：\n新增 早餐 80 QBurger"
                ))
                return

        # ✅ 若只有輸入「新增」兩字 → 進入引導模式
        if len(parts) == 1:
            user_state[user_id] = {"step": "wait_detail"}
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="請輸入：項目 金額 [備註]，例如：\n早餐 80 QBurger"
            ))
            return
        
    if user_id in user_state and user_state[user_id].get("step") == "wait_detail":
        try:
            parts = text.split(maxsplit=2)
            if len(parts) < 2:
                raise ValueError("請至少輸入：項目 金額，例如：\n早餐 80")
            item = parts[0]
            amount = int(parts[1])
            note = parts[2] if len(parts) == 3 else ""
            date = record_expense(item, amount, note)

            msg = f"✅ 記帳成功"
            all_rows = sheet.get_all_values()
            last_row = all_rows[-1]
            real_row_number = len(all_rows)

            record = {
                "日期": last_row[0],
                "項目": last_row[1],
                "金額": last_row[2],
                "備註": last_row[3]
            }
            flex = create_flex_list([record], start_row=real_row_number)

            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=msg),
                FlexSendMessage(alt_text="新增記錄", contents=flex["contents"][0])
            ])
            user_state.pop(user_id)
        except Exception as e:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="❌ 格式錯誤，請重新輸入：項目 金額 [備註]，例如：\n早餐 80 QBurger")
            )
            return

    # 使用者輸入「查詢」 → 顯示 quick reply 日期選擇
    if text == "查詢":
        today = now.strftime("%Y%m%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請選擇要查詢的日期",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="今天", text=f"查詢 {today}")),
                QuickReplyButton(action=MessageAction(label="昨天", text=f"查詢 {yesterday}")),
                QuickReplyButton(action=MessageAction(label="自訂日期", text="查詢 自訂"))
            ])
        ))
        return

    # ✅ 查詢 [日期] 的格式處理（如：查詢 20250510）
    if text.startswith("查詢 "):
        try:
            target = text.split()[1]
            if target == "自訂":
                user_state[user_id] = {"step": "wait_custom_query_date"}
                line_bot_api.reply_message(event.reply_token, TextSendMessage(
                    text="請輸入要查詢的日期（格式：20250510）"
                ))
                return

            date_str = to_dash_date(target)
            matched = filter_by_date(records, date_str)
            if not matched:
                raise ValueError(f"{date_str} 沒有紀錄")
            start_row = 2 + records.index(matched[0])  # 從哪一列開始
            flex = create_flex_list(matched, start_row=start_row)
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="查詢結果", contents=flex))
            user_state.pop(user_id)  # 成功查到才移除狀態
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ {e}"))
        return
    
    # ✅ 處理自訂日期的輸入
    if user_id in user_state and user_state[user_id].get("step") == "wait_custom_query_date":
        try:
            date_str = to_dash_date(text.strip())
            matched = filter_by_date(records, date_str)
            if not matched:
                raise ValueError(f"❌ {date_str} 查無資料，請重新輸入 8 碼日期（如 20250510）")
            
            start_row = 2 + records.index(matched[0])
            flex = create_flex_list(matched, start_row=start_row)
            line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="查詢結果", contents=flex))
            user_state.pop(user_id)  # 成功查到才移除狀態
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ {e}"))
            return
        return
    
    # 使用者輸入「修改」→ 進入引導模式
    if text == "修改":
        user_state[user_id] = {"step": "wait_modify_row"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請輸入要修改第幾筆（例如：2）"
        ))
        return

    # 等使用者輸入要修改哪一筆
    if user_id in user_state and user_state[user_id].get("step") == "wait_modify_row":
        try:
            row = int(text.strip())
            all_rows = sheet.get_all_values()

            if row < 1 or row >= len(all_rows):
                raise ValueError(f"❌ 第 {row} 筆不存在，請重新輸入")

            user_state[user_id]["row"] = row
            user_state[user_id]["step"] = "wait_modify_values"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text="請輸入修改後的資料（格式：項目 金額 [備註]）例如：午餐 130 麥當勞"
            ))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"❌ {e}"
            ))
        return

    # 使用者輸入了項目 金額 [備註] → 執行修改
    if user_id in user_state and user_state[user_id].get("step") == "wait_modify_values":
        try:
            all_rows = sheet.get_all_values()
            row = user_state[user_id]["row"]  # 使用者輸入的是「第幾筆資料」（不含標題列）
            sheet_row = row + 1               # 所以實際在 Google Sheet 中是 row+1
            
            parts = text.split(maxsplit=2)
            if len(parts) < 2:
                raise ValueError("請輸入至少兩個欄位：項目 金額（備註可選）")

            item = parts[0]
            amount = int(parts[1])
            note = parts[2] if len(parts) == 3 else ""

            sheet.update_cell(sheet_row, 2, item)
            sheet.update_cell(sheet_row, 3, amount)
            sheet.update_cell(sheet_row, 4, note)

            row_data = sheet.row_values(sheet_row)
            while len(row_data) < 4:
                row_data.append("")

            record = {
                "日期": row_data[0],
                "項目": row_data[1],
                "金額": row_data[2],
                "備註": row_data[3]
            }
            flex = create_flex_list([record], start_row=row + 1)
            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=f"✅ 第 {row} 筆已修改成功"),
                FlexSendMessage(alt_text="更新後資料", contents=flex["contents"][0])
            ])
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"❌ 修改失敗：{e}"
            ))
        user_state.pop(user_id)
        return

    # ➖ 使用者輸入「刪除」
    if text == "刪除":
        user_state[user_id] = {"step": "wait_delete_row"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請輸入你要刪除的第幾筆資料（例如：2）"
        ))
        return

    # ➖ 使用者輸入欲刪除的行數
    if user_id in user_state and user_state[user_id].get("step") == "wait_delete_row":
        try:
            row = int(text.strip())
            all_rows = sheet.get_all_values()
            sheet_row = row + 1
            if row < 1 or sheet_row > len(all_rows):
                raise ValueError(f"❌ 第 {row} 筆資料不存在，請重新輸入有效的編號")

            row_data = all_rows[sheet_row - 1]
            while len(row_data) < 4:
                row_data.append("")
            record = {
                "日期": row_data[0],
                "項目": row_data[1],
                "金額": row_data[2],
                "備註": row_data[3]
            }
            flex = create_flex_list([record], start_row=row + 1)
            user_state[user_id] = {"step": "confirm_delete", "row": row}
            line_bot_api.reply_message(event.reply_token, [
                FlexSendMessage(alt_text="確認刪除", contents=flex["contents"][0]),
                TextSendMessage(
                    text=f"⚠️ 確定要刪除第 {row} 筆資料嗎？",
                    quick_reply=QuickReply(items=[
                        QuickReplyButton(action=MessageAction(label="✅ 確認刪除", text="刪除 確認")),
                        QuickReplyButton(action=MessageAction(label="❌ 取消刪除", text="刪除 取消"))
                    ])
                )
            ])
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=str(e)))
        return
    
    # ➖ 確認刪除
    if text == "刪除 確認" and user_state.get(user_id, {}).get("step") == "confirm_delete":
        row = user_state[user_id]["row"]
        sheet.delete_rows(row + 1)
        user_state.pop(user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已刪除第 {row} 筆資料"))
        return
    
    # ➖ 取消刪除
    if text == "刪除 取消" and user_state.get(user_id, {}).get("step") == "confirm_delete":
        user_state.pop(user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❎ 已取消刪除"))
        return
    
    # ✅ 統計：第一階段 QuickReply 日期選擇
    if text == "統計":
        today = now.strftime("%Y%m%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
        this_month = now.strftime("%Y%m")
        last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")

        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請選擇要統計的範圍：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="今天", text=f"統計 {today}")),
                QuickReplyButton(action=MessageAction(label="昨天", text=f"統計 {yesterday}")),
                QuickReplyButton(action=MessageAction(label="本月", text=f"統計月 {this_month}")),
                QuickReplyButton(action=MessageAction(label="上個月", text=f"統計月 {last_month}")),
                QuickReplyButton(action=MessageAction(label="自訂日期", text="統計 自訂")),
                QuickReplyButton(action=MessageAction(label="自訂年月", text="統計月 自訂"))
            ])
        ))
        return

    # ✅ 統計：自訂 8 碼日期
    if text == "統計 自訂":
        user_state[user_id] = {"step": "wait_custom_stat_date"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請輸入 8 碼日期（如 20250507）"
        ))
        return

    # ✅ 統計：接收 8 碼日期後執行
    if user_id in user_state and user_state[user_id].get("step") == "wait_custom_stat_date":
        try:
            date_str = to_dash_date(text.strip())
            matched = filter_by_date(get_all_records(), date_str)
            if not matched:
                raise ValueError(f"{date_str} 查無資料，請重新輸入 8 碼日期（如 20250510）")
            total = sum(int(r["金額"]) for r in matched)
            per_item = {}
            for r in matched:
                name = r["項目"]
                per_item[name] = per_item.get(name, 0) + int(r["金額"])
            detail = "\n".join([f"{k}: {v}" for k, v in per_item.items()])
            msg = f"📊 統計日期：{date_str}\n總金額：{total} 元\n\n明細：\n{detail}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            user_state.pop(user_id)
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"❌ {e}\n請重新輸入 8 碼日期（如 20250510）"
            ))
        return
    
    # ✅ 統計：自訂 6 碼年月
    if text == "統計月 自訂":
        user_state[user_id] = {"step": "wait_custom_stat_month"}
        line_bot_api.reply_message(event.reply_token, TextSendMessage(
            text="請輸入 6 碼年月（如 202505）"
        ))
        return

    # ✅ 統計：接收 6 碼年月後執行
    if user_id in user_state and user_state[user_id].get("step") == "wait_custom_stat_month":
        try:
            month_str = text.strip()
            if len(month_str) != 6 or not month_str.isdigit():
                raise ValueError("請輸入正確格式：202505")
            prefix = f"{month_str[:4]}-{month_str[4:]}"
            matched = [r for r in get_all_records() if r["日期"].startswith(prefix)]
            if not matched:
                raise ValueError(f"{prefix} 查無資料，請重新輸入 6 碼年月（如 202505）")
            total = sum(int(r["金額"]) for r in matched)
            per_item = {}
            for r in matched:
                name = r["項目"]
                per_item[name] = per_item.get(name, 0) + int(r["金額"])
            detail = "\n".join([f"{k}: {v}" for k, v in per_item.items()])
            msg = f"📊 統計月份：{prefix}\n總金額：{total} 元\n\n明細：\n{detail}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            user_state.pop(user_id)
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"❌ {e}\n請重新輸入 6 碼年月（如 202505）"
            ))
        return

    # ✅ 統計：快速日期（今天/昨天）
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
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"❌ {e}\n請重新選擇要統計的日期：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="今天", text=f"統計 {today}")),
                    QuickReplyButton(action=MessageAction(label="昨天", text=f"統計 {yesterday}")),
                    QuickReplyButton(action=MessageAction(label="本月", text=f"統計月 {this_month}")),
                    QuickReplyButton(action=MessageAction(label="上個月", text=f"統計月 {last_month}")),
                    QuickReplyButton(action=MessageAction(label="自訂日期", text="統計 自訂")),
                    QuickReplyButton(action=MessageAction(label="自訂年月", text="統計月 自訂"))
                ])
            ))
        return
    
    # ✅ 統計月：快速月份（本月/上月）
    if text.startswith("統計月 "):
        try:
            month_str = text.split()[1]
            if len(month_str) != 6 or not month_str.isdigit():
                raise ValueError("格式錯誤，請輸入 6 碼年月（如 202505）")

            year, month = month_str[:4], month_str[4:]
            prefix = f"{year}-{month.zfill(2)}"
            matched = [r for r in get_all_records() if r["日期"].startswith(prefix)]

            if not matched:
                raise ValueError(f"{prefix} 查無資料")

            total = sum(int(r["金額"]) for r in matched)
            per_item = {}
            for r in matched:
                name = r["項目"]
                per_item[name] = per_item.get(name, 0) + int(r["金額"])
            detail = "\n".join([f"{k}: {v}" for k, v in per_item.items()])
            msg = f"📊 統計月份：{prefix}\n總金額：{total} 元\n\n明細：\n{detail}"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except Exception as e:
            # ⬇️ QuickReply 選單重新出現
            today = now.strftime("%Y%m%d")
            yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
            this_month = now.strftime("%Y%m")
            last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y%m")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(
                text=f"❌ {e}\n請重新選擇要統計的月份：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="今天", text=f"統計 {today}")),
                    QuickReplyButton(action=MessageAction(label="昨天", text=f"統計 {yesterday}")),
                    QuickReplyButton(action=MessageAction(label="本月", text=f"統計月 {this_month}")),
                    QuickReplyButton(action=MessageAction(label="上個月", text=f"統計月 {last_month}")),
                    QuickReplyButton(action=MessageAction(label="自訂日期", text="統計 自訂")),
                    QuickReplyButton(action=MessageAction(label="自訂年月", text="統計月 自訂"))
                ])
            ))
        return

    if text == "選單":
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="請選擇操作功能", contents=get_main_menu()))

@app.get("/health")
async def health_check():
    return {"status": "ok"}