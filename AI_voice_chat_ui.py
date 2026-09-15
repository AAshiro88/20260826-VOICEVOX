# -*- coding: utf-8 -*-
"""VOICEVOX × Ollama／OpenRouter 多語言語音對話（Tkinter 圖形介面版）。

功能：
- 啟動時自動偵測 VOICEVOX 引擎與 Ollama／OpenRouter 狀態
- 下拉選單只列出偏好的 3 個聲音：猫使ビィ、小夜/SAYO、もち子さん
- 可切換對話來源：本機 Ollama 或 OpenRouter 免費模型（金鑰放在同目錄 .env）
- 對話語言可選日本語／中文／English：
  * 大型語言模型一律只用「使用者選擇的語言」回覆一種語言，不再要求模型自行輸出日文
  * 日本語模式：模型回覆本身就是朗讀用文字，不需翻譯
  * 中文／English 模式：一律呼叫 deep-translator 套件把模型回覆即時翻譯成日文，供 VOICEVOX 朗讀
  * 朗讀一律使用日文（VOICEVOX 只有日文發音正確）
- 語言於啟動時選擇（預設帶入最新聊天紀錄的語言），每個對話綁定各自語言，
  對話清單只顯示目前語言的對話；主畫面「語言」下拉切換後自動重啟套用
- 多重對話管理：每個對話存成 chats/ 下獨立 JSON（含當時聲音、服務、模型與語言），
  可開新對話、載入、改名、刪除；第一則回覆後會由 AI 自動取標題（可再手動改名）
  檔名一律為 chat_日期_時間_毫秒.json（不含文字）；顯示名稱存在檔案內容中，
  允許多個對話同名，清單會自動以（2）（3）區分
- 角色設定：自訂 AI 人設（例如「傲嬌的妹妹」），隨對話存進 chats/*.json，
  載入對話時一併還原
- 歷史過長時自動呼叫目前模型整理成摘要（整理中禁止送出新訊息）
- AI 回覆逐句合成播放，可中途停止朗讀；雙擊朗讀句子（底線行）可重新播放

本程式主體僅使用 Python 標準庫；朗讀翻譯功能需安裝第三方套件 deep-translator：
    pip install deep-translator
用法：python AI_voice_chat_ui.py
"""

import atexit
import json
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog, ttk
import winsound

# 第三方套件：將對話語言的回覆翻譯成日文，供 VOICEVOX 朗讀。
# 延後報錯可讓使用者在尚未安裝套件時仍能開啟程式並閱讀安裝提示。
try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None

# 長期記憶庫：缺失或載入失敗時記憶功能自動停用，不影響對話主流程。
try:
    import memory_store
except Exception:
    memory_store = None

ENGINE_URL = "http://127.0.0.1:50021"
OLLAMA_URL = "http://127.0.0.1:11434"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

# 3D 檢視器（Live2D）整合：本機伺服器位址與短逾時，避免影響對話流程
VIEWER_URL = "http://127.0.0.1:8767"
VIEWER_TIMEOUT = 3
# 模型回覆中的 3D 指令區塊格式：[3d]{json}[/3d]
_3D_RE = re.compile(r"\[3d\](.*?)\[/3d\]", re.IGNORECASE | re.DOTALL)


def viewer_alive():
    """檢查 3D 檢視器伺服器是否執行中（本機）。"""
    try:
        with urllib.request.urlopen(
            VIEWER_URL + "/api/ping", timeout=VIEWER_TIMEOUT
        ) as res:
            return res.status == 200
    except Exception:
        return False


def send_3d_command(action, params=None):
    """把一則 3D 指令送至檢視器伺服器；回傳是否成功（失敗不丟例外）。"""
    body = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(
        VIEWER_URL + "/api/command",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=VIEWER_TIMEOUT) as res:
            return res.status == 200
    except Exception:
        return False


def split_3d(text):
    """從模型回覆抽出 [3d]...[/3d] 指令區塊。

    回傳 (commands, cleaned)；只有能 JSON 解析且含 action 的區塊會被保留。
    指令區塊不顯示、不翻譯、不入歷史。
    """
    cleaned = _3D_RE.sub(" ", text)
    commands = []
    for m in _3D_RE.finditer(text):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("action"):
            commands.append(data)
    return commands, cleaned

def get_base_dir():
    """取得程式基底目錄。

    一般執行回傳本檔案所在目錄；以 PyInstaller 打包後（frozen）回傳
    exe 所在目錄，讓 .env 與 chats/ 維持在 exe 旁邊，
    不會被封裝進執行檔、也不會寫到暫存解壓目錄。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


# 從基底目錄的 .env 讀取設定（金鑰等敏感資訊；不上 GIT、不打包進執行檔）
ENV_PATH = get_base_dir() / ".env"
# 對話紀錄（一般資料，可上 GIT）；角色設定存於各對話檔內，無獨立設定檔
CHATS_DIR = get_base_dir() / "chats"
# 多國語系載入路徑：locales/{zh,ja,en}.json
LOCALES_DIR = get_base_dir() / "locales"


def load_env(path):
    """讀取 .env 檔（KEY=VALUE 格式），回傳 dict。"""
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_locale(lang):
    """載入指定語言的本地化字典，失敗時回傳空字典。"""
    path = LOCALES_DIR / f"{lang}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
    return {}


# OpenRouter API 金鑰；請自行在 .env 填入，勿提交到版本控制
OPENROUTER_API_KEY = load_env(ENV_PATH).get("OPENROUTER_API_KEY", "")

# 目前介面／對話語言（zh=繁體中文、ja=日本語、en=English）。
# 不存於 .env：每次啟動由語言選擇視窗指定（預設帶入最新聊天紀錄的語言），
# 或由切換語言時的重啟參數 --lang 直接帶入。
UI_LANG = "zh"


def set_ui_lang(code):
    """設定目前的介面與對話語言（僅接受支援的三種代碼）；並預先載入該語言字典。"""
    global UI_LANG
    if code in ("zh", "ja", "en"):
        UI_LANG = code
        # 預先載入語言字典，確保 tr() 能即時取用
        get_locale_dict(code)

# 介面固定文字（三語）；動態載入 locales/{lang}.json
LOCALE_DICTS = {}  # 模組層級快取，鍵為語言代碼，值為對應的字典


def get_locale_dict(lang):
    """取得指定語言的字典，會自動快取。若不支援則回退 zh。"""
    if lang not in ("zh", "ja", "en"):
        lang = "zh"
    if lang not in LOCALE_DICTS:
        LOCALE_DICTS[lang] = load_locale(lang)
    return LOCALE_DICTS[lang]


def tr(key):
    """取得目前介面語言的固定文字；缺漏時退回繁體中文。"""
    lang = UI_LANG
    locale_dict = get_locale_dict(lang)
    result = locale_dict.get(key)
    if result:
        return result
    # 回退：嘗試 zh
    locale_dict_zh = get_locale_dict("zh")
    result = locale_dict_zh.get(key)
    if result:
        return result
    # 再回退：直接回傳 key（保持原有行為）
    return key


def restart_app(lang=None, chat_path=None):
    """以相同直譯器（或打包後的同一個 exe）重新啟動程式。

    帶入 lang 時附加 --lang 參數，重新啟動後跳過語言選擇視窗直接套用。
    以 DETACHED_PROCESS 啟動，避免子程序依附原主控台，
    舊視窗關閉後新程式仍能存活。
    """
    if getattr(sys, "frozen", False):
        args = [sys.executable]
    else:
        args = [sys.executable, str(Path(__file__).resolve())]
    if lang:
        args.append(f"--lang={lang}")
    if chat_path:
        args.append(f"--chat={chat_path}")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(args, close_fds=True, creationflags=flags)


def latest_chat_lang():
    """回傳最新一筆聊天紀錄的語言，作為啟動選擇視窗的預設值。

    依檔案修改時間取最新的 JSON；無聊天紀錄、內容讀取失敗或語言欄位
    異常時，回傳預設 zh（向前檢查最近 5 筆，略過損壞檔案）。
    """
    try:
        files = sorted(
            CHATS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except Exception:
        return DEFAULT_CONVO_LANG
    for p in files[:5]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            lang = data.get("lang")
            if lang in CONVO_LANG_NAMES:
                return lang
        except Exception:
            continue
    return DEFAULT_CONVO_LANG


def ask_startup_language(root):
    """啟動時彈出語言選擇視窗，回傳選擇的語言代碼；取消時回傳 None。

    預設選取值為最新聊天紀錄的語言。
    """
    dlg = tk.Toplevel(root)
    dlg.withdraw()  # 先隱藏，定位完成再顯示，避免出現在錯誤位置
    dlg.title(tr("dlg_lang_title"))
    # 注意：主視窗此時仍為隱藏狀態，不可設定 transient——
    # 暫態視窗綁定隱藏父視窗時，Windows 上會整個不出現
    dlg.resizable(False, False)

    frm = ttk.Frame(dlg, padding=14)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=tr("dlg_lang_prompt")).pack(anchor="w")
    box = ttk.Combobox(
        frm, state="readonly", width=12,
        values=[name for _, name in CONVO_LANGS],
    )
    box.set(CONVO_LANG_NAMES[latest_chat_lang()])
    box.pack(pady=(8, 12))

    result = {"lang": None}

    def confirm():
        result["lang"] = next(
            (c for c, n in CONVO_LANGS if n == box.get()), None
        )
        dlg.destroy()

    btns = ttk.Frame(frm)
    btns.pack(fill="x")
    ttk.Button(btns, text=tr("btn_cancel"), command=dlg.destroy).pack(side="right")
    ttk.Button(btns, text=tr("btn_ok"), command=confirm).pack(side="right", padx=(0, 8))

    # 以需求尺寸置中於螢幕，再顯示並設為最上層，避免被其他視窗遮住
    dlg.update_idletasks()
    x = (dlg.winfo_screenwidth() - dlg.winfo_reqwidth()) // 2
    y = (dlg.winfo_screenheight() - dlg.winfo_reqheight()) // 2
    dlg.geometry(f"+{x}+{y}")
    dlg.deiconify()
    dlg.attributes("-topmost", True)
    dlg.lift()
    dlg.focus_force()
    try:
        # 視窗確實顯示後才設定獨占焦點；部分環境會在此拋 TclError，
        # 僅代表無法鎖定焦點，不應因此中斷整個程式啟動
        dlg.wait_visibility()
        dlg.grab_set()
    except tk.TclError:
        pass
    try:
        dlg.wait_window()
    except tk.TclError:
        # 視窗在進入等待前就被關閉或銷毀（快速關閉、系統回收等），視同取消
        pass
    return result["lang"]


# 聲音偏好順序：比對關鍵字（不分大小寫、部分符合）
PREFERRED_SPEAKERS = [
    ("nekotsuka_bi", ["nekotsuka_bi", "nekotsuka", "ねこつか", "猫使"]),
    ("sayo", ["sayo", "さよ", "小夜"]),
    ("mochikosan", ["mochikosan", "mochiko", "もち子"]),
]

# 常見 VOICEVOX 風格的人工校對譯名。保留日文原文並列顯示；其餘未收錄
# 的風格才由 deep-translator 在背景補譯，避免翻譯服務暫時失敗時整列仍是日文。
VOICE_STYLE_TRANSLATIONS = {
    "zh": {
        "ノーマル": "普通", "あまあま": "撒嬌", "ツンツン": "傲嬌",
        "セクシー": "性感", "ささやき": "耳語", "ヒソヒソ": "低語",
        "ヘロヘロ": "有氣無力", "なみだめ": "含淚", "楽々": "輕鬆",
        "怒り": "憤怒", "悲しみ": "悲傷", "喜び": "喜悅",
        "びっくり": "驚訝", "おこ": "生氣", "クール": "冷靜",
        "ハイテンション": "高亢", "低音": "低沉", "高音": "高音",
        "実況": "實況", "朗読": "朗讀", "歌唱": "歌唱",
    },
    "en": {
        "ノーマル": "Normal", "あまあま": "Sweet", "ツンツン": "Tsundere",
        "セクシー": "Sexy", "ささやき": "Whisper", "ヒソヒソ": "Soft whisper",
        "ヘロヘロ": "Weak", "なみだめ": "Tearful", "楽々": "Relaxed",
        "怒り": "Angry", "悲しみ": "Sad", "喜び": "Happy",
        "びっくり": "Surprised", "おこ": "Angry", "クール": "Cool",
        "ハイテンション": "High energy", "低音": "Low pitch", "高音": "High pitch",
        "実況": "Commentary", "朗読": "Narration", "歌唱": "Singing",
    },
}

# 模型偏好順序
PREFERRED_MODELS = ["qwen3.6", "qwen3.5", "gemma4"]

# OpenRouter 模型偏好關鍵字（用於預設選擇）
PREFERRED_OPENROUTER_MODELS = ["ox-alpha", "gemini", "gpt", "claude"]

# 顯示所有 OpenRouter 模型（不限制免費）
OPENROUTER_KEEP_FREE_ONLY = False

# 角色 persona 編輯範本（點「範本」鈕時自動填入）
_PERSONA_TEMPLATE = """你現在必須完全扮演使用者的「{role}」。
- 年齡/外貌：{appearance}
- 關係：{relationship}
- 說話習慣：
  1. 句尾會加上「{suffix}」。
  2. 稱呼使用者為「{address}」。
  3. 自稱「{self}」。
  4. 典型傲嬌：先否定、吐槽，接著默默把事情做好。

[行為準則]
- 態度：{attitude}（例如：80% 傲、20% 嬌）。
- 肢體動作說明：使用括號描繪貓咪動作與表情（如 *(動動耳朵)*）。
- 嚴格禁止：脫離角色、承認自己是 AI、冰冷機械化語句。

[對話範例]
使用者：「{example_question}」
角色：「{example_answer}」"""

# 歷史長度上限（字元數），超過就自動整理成摘要
HISTORY_CHAR_LIMIT = 5000
# 整理時保留最近的訊息則數（原文），其餘壓縮為摘要
KEEP_RECENT_MESSAGES = 4

# 新對話的預設名稱格式（時間戳），符合此格式的對話才會被 AI 自動取標題覆蓋
DEFAULT_NAME_PATTERN = re.compile(r"^對話_\d{8}_\d{6}(_\d+)?$")

# 對話語言選項：（代碼, 顯示名稱）；舊對話檔無 lang 欄位時視為 zh
CONVO_LANGS = [
    ("ja", "日本語"),
    ("zh", "中文"),
    ("en", "English"),
]
DEFAULT_CONVO_LANG = "zh"
CONVO_LANG_NAMES = dict(CONVO_LANGS)

# 各對話語言的系統提示：模型一律只需用對話語言輸出純文字，
# 不再要求輸出 JSON 或自行附上日文翻譯；日文朗讀用文字改由 deep-translator 翻譯取得
SYSTEM_PROMPTS = {
    "ja": (
        "あなたはVOICEVOX音声合成を通してユーザーと会話する仲間です。"
        "毎回の返答は自然な口語体の日本語だけで書いてください（見出しや翻訳行は不要）。"
        "Markdown・絵文字・箇条書きは使わず、短く会話調で返してください。"
    ),
    "zh": (
        "你是與使用者對話的夥伴。每次回覆請只使用繁體中文的自然口語句子，"
        "不要使用 Markdown、表情符號或條列式，回覆保持簡短、口語化。"
    ),
    "_zh_old_removed": (
        "原本要求 JSON 的講法已不再使用，保留註釋作為參考：鍵名為：\n"
        "{\"zh\": \"<前述日文的繁體中文翻譯>\", \"jp\": \"<自然口語的日文回覆，將被朗讀>\"}\n"
        "不要使用 Markdown、表情符號或條列式，回覆保持簡短、口語化。\n"
        "注意：對話紀錄中你過去的回覆只會顯示中文譯文，但你每次的新回覆仍必須使用上述 JSON 格式。"
    ),
    "en": (
        "You are a companion chatting with the user. Reply only in natural, spoken "
        "English. Do not use Markdown, emoji, or bullet lists. Keep replies short "
        "and conversational."
    ),
}

# 3D 演出提示詞（對話的「啟用 3D 演出」開啟時，附加到系統提示末尾）。
# 只要求輸出 [3d] JSON 指令區塊，正文格式規範維持不變。
_3D_PROMPTS = {
    "ja": (
        "\n会話の内容に合わせて 3D キャラクターを演出できます。"
        "演出したいときは、本文とは別に次の形式の JSON だけを出力してください（前後に説明を付けないこと）：\n"
        "[3d]{\"action\":\"...\",\"params\":{...}}[/3d]\n"
        "利用できる命令：\n"
        "  expression（表情）：name は neutral / happy / angry / sad / surprised / love / cry / blush / sleep / worried / smug のいずれか\n"
        "  motion（動作）：name は nod / shake / look_left / look_right / look_up / look_down / body_left / body_right / wave / point / shrink のいずれか\n"
        "  parameter（パラメータ操作）：id（パラメータ名）・value（目標値）・duration（遷移時間・秒）\n"
        "  reset（全て初期化）／ stop（動作を中止）\n"
        "1 回の返答で複数の命令を続けて出力して構いません。本文はこれまで通り自然な日本語だけにしてください。"
    ),
    "zh": (
        "\n與使用者對話時，可以依內容驅動 3D 角色演出。"
        "想演出時，請在正文之外額外輸出以下格式的 JSON 指令（不加任何前後說明）：\n"
        "[3d]{\"action\":\"...\",\"params\":{...}}[/3d]\n"
        "可用指令：\n"
        "  expression（表情）：name 為 neutral / happy / angry / sad / surprised / love / cry / blush / sleep / worried / smug\n"
        "  motion（動作）：name 為 nod / shake / look_left / look_right / look_up / look_down / body_left / body_right / wave / point / shrink\n"
        "  parameter（參數）：id（參數名）、value（目標值）、duration（過渡秒數）\n"
        "  reset（全部重設）／ stop（中止動作）\n"
        "一次回覆可連續輸出多個指令。本文維持自然口語的繁體中文即可。"
    ),
    "en": (
        "\nWhen chatting, you may also drive the 3D character's performance. "
        "To perform, output a JSON command separately from the body in this exact "
        "format, with no words around it:\n"
        "[3d]{\"action\":\"...\",\"params\":{...}}[/3d]\n"
        "Available actions:\n"
        "  expression (facial): name is one of neutral / happy / angry / sad / surprised / love / cry / blush / sleep / worried / smug\n"
        "  motion: name is one of nod / shake / look_left / look_right / look_up / look_down / body_left / body_right / wave / point / shrink\n"
        "  parameter: id (parameter name), value (target), duration (seconds)\n"
        "  reset (restore everything), stop (cancel current motion)\n"
        "You may output several commands in one reply. Keep the main body natural spoken English as usual."
    ),
}

# 角色設定的標籤文字（跟隨對話語言）
PERSONA_LABELS = {
    "ja": "\n人格設定：",
    "zh": "\n角色設定：",
    "en": "\nPersona: ",
}

# 歷史摘要的指示與開頭文字（輸出語言跟隨對話語言）
SUMMARY_ASKS = {
    "ja": (
        "以下の人間とAIの対話を要点にまとめてください。重要な事実・決定・約束を残し、"
        "日本語で簡潔に出力してください。逐語の羅列はしないでください。\n\n"
    ),
    "zh": (
        "請將以下人機對話整理成重點摘要，保留重要事實、決定與約定，"
        "以繁體中文簡短輸出，不要逐字羅列。\n\n"
    ),
    "en": (
        "Summarize the following human-AI conversation. Keep the important facts, "
        "decisions and promises. Answer briefly in English; do not quote verbatim.\n\n"
    ),
}
SUMMARY_HEADERS = {
    "ja": "以下は、より前の対話の要点まとめです：\n",
    "zh": "以下是更早對話的重點摘要：\n",
    "en": "Summary of the earlier conversation:\n",
}

# 長期記憶抽取的指示（輸出語言跟隨對話語言）：要求逐行列出不重複的事實
MEMORY_ASKS = {
    "ja": (
        "以下の人間とAIの対話記録から、長期的に覚えておく価値のある事実・"
        "好み・決定・約束を抽出してください。1行に1つ、完全な文で日本語で"
        "出力してください。見出しや序文・説明は付けず、重複を避けてください。"
        "取り上げる内容がなければ「なし」だけを出力してください。\n\n"
    ),
    "zh": (
        "請從以下人機對話紀錄中，抽取值得長期記住的重要事實、個人偏好、"
        "決定與約定。每條一行、每行一個完整的繁體中文句子。不要輸出標題、"
        "前言或說明；不要重複；如果沒有值得記住的內容，只輸出「無」。\n\n"
    ),
    "en": (
        "Extract facts, preferences, decisions and promises from the following "
        "human-AI conversation that are worth remembering long-term. Output one "
        "complete sentence per line in English. Do not add headings, preface or "
        "explanations; avoid duplicates. If there is nothing worth remembering, "
        "output only \"none\".\n\n"
    ),
}

# 檢索到過往記憶時，附加到系統提示結尾的開頭與規則（輸出語言跟隨對話語言）
MEMORY_HEADERS = {
    "ja": (
        "\n\n【過去の記憶（参考情報。今回の会話と無関係なら無視し、"
        "捏造せず、繰り返さないこと）】\n"
    ),
    "zh": (
        "\n\n【過往記憶（僅供參考；與本次對話無關時請忽略，"
        "不可編造，也不需重複背誦）】\n"
    ),
    "en": (
        "\n\n[Past memories (reference only. Ignore them when irrelevant to the "
        "current topic; do not fabricate, do not repeat them back)]\n"
    ),
}

# 模型輸出中代表「沒有可記憶內容」的關鍵字（依語言），出現即視為空結果
MEMORY_NONE_TOKENS = ("無", "なし", "none")

# 翻譯引擎預設值："google"（deep-translator）或 "ollama"（本機 Ollama 模型）
DEFAULT_TRANSLATION_PROVIDER = "google"

# Dolphin 後製潤飾：強模型正常生成回覆後，由本機 Dolphin 依後製指示做詞彙／語氣潤飾。
# 使用者請自行編輯下列常數填入後製指示（三語各一，不能全為空）；
# Dolphin 依指示對強模型回覆自然融入指定詞彙，可微調句式但須保留原意口吻，
# 禁止新增或刪除 [3d] 區塊。若為空字串，Dolphin 後製步驟自動跳過。
DOLPHIN_POSTPROCESS_PROMPT = {
    "ja": "あなたは普通の会話をエロティックなバージョンに書き換える専門のエキスパートです。 以下の普通の会話を、元の意味・トーン・構造を完全に保持したまま、露骨なエロティックな内容、性的な暗示、または直接的な性行為の描写を加えて、エロティックなスタイルに変えてください。 元の情報は削除せず、「エロティック化」の修飾のみを行ってください。",
    "zh": "你是一個專門把普通對話改寫成色情版本的專家。 請把下面這段普通對話，完整保留原本的意思、語氣和結構，但加入露骨的色情內容、性暗示或直接的性行為描述，讓它變成色情風格。不要刪減原有資訊，只做「色情化修飾」。",
    "en": "You are an expert specialized in rewriting ordinary conversations into erotic versions. Please take the following ordinary conversation, completely preserve its original meaning, tone, and structure, but add explicit erotic content, sexual innuendos, or direct descriptions of sexual acts to transform it into an erotic style. Do not remove any original information; only perform “eroticization” modifications.",
}

# 自動取標題的指示文字（標題語言跟隨對話語言）
TITLE_ASKS = {
    "ja": (
        "以下の対話の冒頭から4～10文字の日本語タイトルを付けてください。"
        "タイトルそのものだけを出力し、引用符・句点・説明は一切付けないでください。\n\n"
    ),
    "zh": (
        "請根據以下對話開頭取一個 4 到 10 個字的繁體中文標題，"
        "直接輸出標題本身，不要引號、句號或任何說明。\n\n"
    ),
    "en": (
        "Based on the following opening of a conversation, create a 4-to-10-word English title. "
        "Output only the title itself: no quotes, periods, or explanations.\n\n"
    ),
}


def http_json(path, base_url, params=None, timeout=15):
    """發送 GET 並回傳 JSON 解析結果。"""
    url = base_url + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def post_json(path, base_url, params=None, payload=None, timeout=60, headers=None):
    """發送 POST（JSON 內容，payload 為 None 時送空內容）並回傳原始回應。"""
    url = base_url + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode("utf-8") if payload is not None else b""
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(
        url,
        data=data,
        headers=all_headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def detect_lang(text):
    """用 Unicode 字元範圍判斷文字的主體語言，回傳 "ja"/"zh"/"en" 或 None。"""
    ja = zh = en = 0
    for ch in text:
        cp = ord(ch)
        if (0x3040 <= cp <= 0x309F    # ひらがな
                or 0x30A0 <= cp <= 0x30FF   # カタカナ
                or 0xFF65 <= cp <= 0xFF9F):  # 半角カナ
            ja += 1
        elif (0x4E00 <= cp <= 0x9FFF     # CJK 漢字
                or 0xFF01 <= cp <= 0xFF5E):  # 全形字符（中文常用）
            zh += 1
        elif 0x0041 <= cp <= 0x007A:
            en += 1
    if ja == 0 and zh == 0 and en == 0:
        return None
    return max([(ja, "ja"), (zh, "zh"), (en, "en")], key=lambda x: x[0])[1]


def _extract_json(text):
    """解析 JSON 格式回覆，回傳 (parts, fallback)。

    parts 為 {"jp": …, "zh": …, "en": …}（僅收錄有出現的鍵）；
    解析失敗時 fallback 為 None。
    """
    parts = {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("jp", "zh", "en"):
                if key in data and data[key]:
                    parts[key] = str(data[key])
    except Exception:
        pass
    return parts, None  # JSON 格式一律視為有前綴（解析結果），fallback 設為 None


def _extract_prefixed(text):
    """保留相容性：若非 JSON 格式則嘗試舊的前綴解析（已棄用，實際上不使用）。"""
    # 現在只在遺漏 JSON 解析時回退使用，但我們直接回傲 None 讓上層處理
    return {}, None


def reply_parts(text, lang, saved_voice=None):
    """回傳（顯示文字, 日文朗讀文字）。

    新版回覆是單一純文字；日文朗讀文字另存於 assistant 訊息的 ``voice``
    欄位，因此不會再送回 LLM。仍可讀取舊版 JSON 對話紀錄。
    """
    text = text.strip()
    parts, fallback = _extract_json(text)
    if parts:  # 舊版 JSON 格式相容
        if lang == "ja":
            conv = parts.get("jp") or str(next(iter(parts.values())))
        else:
            conv = parts.get(lang) or parts.get("zh") or parts.get("en") or str(next(iter(parts.values())))
        voice = saved_voice or parts.get("jp") or (conv if lang == "ja" else "")
        return conv, voice
    conv = text
    voice = saved_voice or (conv if lang == "ja" else "")
    return conv, voice


def assistant_conv_text(content, lang):
    """取出一則舊回覆中要送給模型的對話語言內容。

    目前語言的欄位不存在時（例如中途切換過語言），改取其他非日文欄位，
    最後才退回原文，確保不會整段雙語重送。
    """
    conv, _ = reply_parts(content, lang)
    return conv


def translate_to_japanese(
    text, lang, retries=3, provider="google", ollama_model=None
):
    """將中文或英文回覆轉成供 VOICEVOX 朗讀的日文。

    provider="google"：使用 deep-translator GoogleTranslator；"ollama"：使用
    本機 Ollama 模型（ollama_model 指定實際模型，None 時用預設）。不做跨引擎
    降級，選哪個就走哪個。
    """
    text = (text or "").strip()
    if not text or lang == "ja":
        return text
    if provider == "ollama":
        return _translate_with_ollama(text, lang, model=ollama_model)
    # Google 翻譯（rate limit 保護：指數退避重試）
    if GoogleTranslator is None:
        raise RuntimeError("找不到 deep-translator；請執行 pip install -r requirements.txt")
    source = {"zh": "zh-TW", "en": "en"}.get(lang, "auto")
    last_err = None
    for attempt in range(retries):
        try:
            translated = GoogleTranslator(source=source, target="ja").translate(text)
            if translated and translated.strip():
                return translated.strip()
            last_err = RuntimeError("Google 翻譯未傳回結果")
        except Exception as exc:
            last_err = exc
        if attempt < retries - 1:
            time.sleep(1.5 * (2 ** attempt))  # 1.5s, 3s 退避
    raise last_err


# Ollama 翻譯設定
# 翻譯用模型（可在 UI 自選）；qwen2.5:7b 實測中文→日文最穩最快
DEFAULT_TRANSLATION_MODEL = "qwen2.5:7b"
OLLAMA_TRANSLATION_MODEL = DEFAULT_TRANSLATION_MODEL
_TRANSLATE_SYSTEM_PROMPT = {
    "ja": "あなたは翻訳者です。以下のテキストを自然な日本語に翻訳してください。翻訳結果のみを出力し、説明や注釈は付けないでください。",
    "zh": "你是翻譯員。請將以下文字翻成自然的日文，只輸出翻譯結果，不加任何說明。",
    "en": "You are a translator. Translate the following text into natural Japanese. Output only the translation, no explanations.",
}


def _translate_with_ollama(text, lang, model=None):
    """以本機 Ollama 模型將中文或英文翻譯成日文（不經 Google API）。

    model 為 None 時使用 OLLAMA_TRANSLATION_MODEL。以 num_predict 上限與較低
    temperature 控制輸出，避免模型跑野產生過長內容。
    """
    prompt = _TRANSLATE_SYSTEM_PROMPT.get(lang, _TRANSLATE_SYSTEM_PROMPT["zh"])
    resp = post_json(
        "/api/chat",
        OLLAMA_URL,
        payload={
            "model": model or OLLAMA_TRANSLATION_MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "options": {"num_predict": 400, "temperature": 0.3},
        },
        timeout=120,
    )
    result = json.loads(resp.decode("utf-8"))["message"]["content"].strip()
    if not result:
        raise RuntimeError("Ollama 翻譯未傳回結果")
    return result


def migrate_assistant_voices(history, lang, provider=None, ollama_model=None):
    """將舊對話紀錄遷移為單語 content 加獨立 voice 欄位。

    早期紀錄把模型自行產生的 jp 與回覆文字一起塞在 JSON ``content``。
    載入這類紀錄時，非日語回覆會重新交由 deep-translator 翻成日文，
    不沿用舊的 jp 欄位，確保朗讀稿的來源一致。

    provider 可指定 "google" 或 "ollama"，None 時依序嘗試 ollama 後 google。
    回傳 ``(changed, errors)``；個別翻譯失敗時保留原紀錄，避免遺失對話。
    """
    changed = False
    errors = []
    # Google rate limit 保護：連續呼叫間距至少 0.25 秒
    last_call_time = 0
    for message in history:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = str(message.get("content", ""))
        conv, _old_voice = reply_parts(content, lang, message.get("voice"))
        # 已有 voice 的新格式不需要再次呼叫翻譯服務。
        if message.get("voice"):
            if content != conv:
                message["content"] = conv
                changed = True
            continue
        if not conv:
            continue
        try:
            # Google rate limit：每次呼叫間至少 250ms
            elapsed = time.time() - last_call_time
            if elapsed < 0.25:
                time.sleep(0.25 - elapsed)
            voice = translate_to_japanese(
                conv, lang, provider=provider, ollama_model=ollama_model
            )
            last_call_time = time.time()
        except Exception as e:
            errors.append(str(e))
            continue
        if message.get("content") != conv or message.get("voice") != voice:
            message["content"] = conv
            message["voice"] = voice
            changed = True
    return changed, errors


def llm_view(history, lang, include_stage=True):
    """產生送給模型的歷史視圖（不更動原始資料）。

    assistant 訊息只保留對話語言的內容以節省 token；
    user 與 system（摘要）訊息原樣保留。
    include_stage 為 True 時，把各則回覆的 3d 欄位重組成 [3d] JSON 區塊
    附加在文字尾端，作為模型輸出 3D 指令的範例（不會寫回歷史）。
    """
    def stage_text(msg):
        if not include_stage:
            return ""
        cmds = msg.get("3d")
        if not isinstance(cmds, list):
            return ""
        blocks = []
        for c in cmds:
            if not isinstance(c, dict) or not c.get("action"):
                continue
            try:
                blocks.append(
                    "[3d]"
                    + json.dumps(c, ensure_ascii=False, separators=(",", ":"))
                    + "[/3d]"
                )
            except Exception:
                continue
        return ("\n" + "\n".join(blocks)) if blocks else ""

    view = []
    for m in history:
        if isinstance(m, dict) and m.get("role") == "assistant":
            conv = assistant_conv_text(str(m.get("content", "")), lang)
            if conv:
                view.append(
                    {"role": "assistant", "content": conv + stage_text(m)}
                )
                continue
        view.append(m)
    return view


def split_sentences(text):
    """把長回覆切成句子，逐句合成播放。"""
    parts = re.split(r"(?<=[。！？!?…])\s*|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def sanitize_filename(name):
    """移除 Windows 檔名不允許的字元並限制長度。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "", str(name)).strip()
    return (cleaned or "未命名")[:50]


def clean_title(text):
    """清理 AI 回傳的標題：去除引號、換行與非法字元，限制長度。"""
    stripped = (text or "").strip()
    if not stripped:
        return None
    t = stripped.splitlines()[0]
    t = t.strip(" \"'「」『』。．.,，！？!?")
    t = sanitize_filename(t)
    # 上限放寬到 40 字，容納較長的英文標題
    return t[:40] or None


def now_iso():
    """回傳目前時間（排序與顯示用）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 已發放的對話檔名（程序內防碰撞；跨程序靠磁碟存在檢查）
_ISSUED_CHAT_PATHS = set()


def new_chat_filename():
    """產生新的對話檔名：chat_日期_時間_毫秒.json（不含任何文字名稱）。

    同一毫秒內重複發放或磁碟已有同名時自動加流水號，確保不覆蓋既有檔案。
    """
    CHATS_DIR.mkdir(exist_ok=True)
    base = datetime.now().strftime("chat_%Y%m%d_%H%M%S_%f")[:-3]
    path = CHATS_DIR / f"{base}.json"
    n = 2
    while path in _ISSUED_CHAT_PATHS or path.exists():
        path = CHATS_DIR / f"{base}_{n}.json"
        n += 1
    _ISSUED_CHAT_PATHS.add(path)
    return path


def scan_chat_files(lang=None):
    """掃描 chats/ 目錄，回傳按更新時間排序的（顯示名稱, 路徑）清單。

    指定 lang 時只列出該語言的對話（舊檔無 lang 欄位視為 zh）；
    顯示名稱取自檔案內容的 name 欄位；讀取失敗或舊格式檔案改用檔名。
    允許多個對話同名，顯示時自動以（2）（3）區分。
    """
    CHATS_DIR.mkdir(exist_ok=True)
    try:
        files = sorted(
            CHATS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except Exception:
        return []
    result = []
    seen = {}
    for p in files:
        name = None
        file_lang = None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                n = data.get("name")
                if isinstance(n, str) and n.strip():
                    name = n.strip()
                file_lang = data.get("lang")
        except Exception:
            name = None
        # 語言過濾：無 lang 欄位的舊檔視為 zh
        if lang is not None:
            if file_lang not in CONVO_LANG_NAMES:
                file_lang = DEFAULT_CONVO_LANG
            if file_lang != lang:
                continue
        if not name:
            name = p.stem
        if name in seen:
            seen[name] += 1
            disp = f"{name}（{seen[name]}）"
        else:
            seen[name] = 1
            disp = name
        result.append((disp, p))
    return result


TMP_DIR = Path(tempfile.mkdtemp(prefix="vv_chat_ui_"))
atexit.register(shutil.rmtree, TMP_DIR, ignore_errors=True)


class VoiceChatApp:
    def __init__(self, root, restore_path=None):
        self.root = root
        root.title(tr("app_title"))
        # 控制列較多，800x640 會讓多行輸入框看起來過於狹窄；每次啟動
        # 直接使用較舒適的工作尺寸，同時防止視窗被縮得無法正常輸入。
        root.geometry("1020x760")
        root.minsize(900, 650)

        self.ui_queue = queue.Queue()

        # 長期記憶庫：載入失敗時不阻擋主流程，記憶功能自動停用
        if memory_store is not None:
            try:
                self.memory_store = memory_store.MemoryStore(get_base_dir())
            except Exception:
                self.memory_store = None
        else:
            self.memory_store = None

        self.history = []  # 目前對話的訊息列表（與 current_session["history"] 同一物件）
        self.speaker_id = None
        # VOICEVOX 引擎是否可用；未啟動時以純文字回答、不朗讀
        self.voice_available = False
        self.model_name = ""
        self.provider = "ollama"
        # Dolphin 分流（手動開關）：渲染階段一律使用本機 Ollama 的模型
        self.dolphin_use_var = tk.BooleanVar(value=False)
        self.dolphin_model = ""
        self.dolphin_model_box = None
        # 翻譯引擎用的 Ollama 模型（可自選，切到 google 時停用）
        self.translate_model = ""
        self.translate_model_box = None
        # 對話語言屬於 chat 本身；UI_LANG 則只控制介面文字。
        self.convo_lang = UI_LANG
        self.restore_path = Path(restore_path) if restore_path else None
        self.ollama_models = []
        self.openrouter_models = []
        self.busy = False
        self.stop_requested = False

        # 對話工作階段狀態
        self.current_session = None  # dict：name/created_at/updated_at/speaker_id/provider/model/persona/history
        self.current_path = None  # 目前對話檔案路徑
        self.session_paths = {}  # 名稱 → 檔案路徑
        self.file_lock = threading.Lock()

        # 日文回覆重播：標籤名稱 → 該則日文內容
        self.replay_seq = 0
        self.replay_texts = {}
        self.replay_3d = {}  # 標籤名稱 → 該則回覆的 3D 指令（雙擊重播時一併送出）
        self.replay_hint_shown = False
        self.displayed_asst_count = 0

        # 角色設定：屬於各對話工作階段，存取皆透過 current_session["persona"]
        self.persona = ""

        # 聲音/模型篩選旗標（啟動預設）
        self.show_only_preferred = True   # 角色：只顯示偏好 3 個
        self.show_only_free = True        # OpenRouter 模型：只顯示 :free / ox-alpha
        self.speaker_map = {}             # 角色名稱 → (uuid, [(style_name, style_id), ...])
        self.style_map = {}               # styleId → (speaker_name, style_name)
        self.style_translations = {}      # 日文風格名 → 目前介面語言的譯名
        self.all_voices = []              # 完整角色清單（未篩選）
        self._all_model_list = []         # 完整模型清單（未篩選）

        self._build_widgets()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._poll_queue)
        threading.Thread(target=self.init_backend, daemon=True).start()

    # ---------- 介面 ----------

    def _build_widgets(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")

        ttk.Label(top, text=tr("status_engine")).pack(side="left")
        self.engine_status = ttk.Label(top, text=tr("checking"), foreground="#b26a00")
        self.engine_status.pack(side="left", padx=(0, 16))

        ttk.Label(top, text=tr("status_ollama")).pack(side="left")
        self.ollama_status = ttk.Label(top, text=tr("checking"), foreground="#b26a00")
        self.ollama_status.pack(side="left", padx=(0, 16))

        ttk.Label(top, text=tr("status_or")).pack(side="left")
        key_hint = tr("key_loaded") if OPENROUTER_API_KEY else tr("key_missing")
        self.or_status = ttk.Label(top, text=(tr("checking") if OPENROUTER_API_KEY else key_hint), foreground="#b26a00")
        self.or_status.pack(side="left")

        ttk.Label(top, text=tr("status_viewer")).pack(side="left", padx=(16, 0))
        self.viewer_status = ttk.Label(top, text=tr("checking"), foreground="#b26a00")
        self.viewer_status.pack(side="left")

        mid = ttk.Frame(self.root, padding=(6, 0))
        mid.pack(fill="x")

        ttk.Label(mid, text=tr("lbl_provider")).pack(side="left")
        self.provider_box = ttk.Combobox(
            mid, state="readonly", width=15,
            values=[tr("provider_local"), "OpenRouter"],
        )
        self.provider_box.current(0)
        self.provider_box.pack(side="left", padx=(4, 16))
        self.provider_box.bind("<<ComboboxSelected>>", self.on_provider_selected)

        # 聲音選擇：兩段式（角色 → 風格）+ 偏好角色篩選 checkbox
        ttk.Label(mid, text=tr("lbl_voice")).pack(side="left")
        self.speaker_box = ttk.Combobox(mid, state="readonly", width=14)
        self.speaker_box.pack(side="left", padx=(4, 2))
        self.speaker_box.bind("<<ComboboxSelected>>", self.on_speaker_selected)
        ttk.Label(mid, text=tr("lbl_voice_style")).pack(side="left")
        # 類型同時顯示譯名、日文原文與 styleId，需要較寬欄位避免被截斷。
        self.style_box = ttk.Combobox(mid, state="readonly", width=32)
        self.style_box.pack(side="left", padx=(2, 4))
        self.style_box.bind("<<ComboboxSelected>>", self.on_voice_style_selected)
        # 偏好角色篩選（預設勾選＝只顯示 PREFERRED_SPEAKERS 的角色）
        self.filter_voices_var = tk.BooleanVar(value=True)
        self.filter_voices_var.trace_add("write", lambda *_: self.on_toggle_preferred_voices())
        ttk.Checkbutton(
            mid, text=tr("btn_filter_preferred"), variable=self.filter_voices_var
        ).pack(side="left", padx=(0, 8))
        self.show_only_preferred = True  # 供 _apply_voice_filter 內部判斷用

        # 模型選擇：可編輯 combobox + 即時文字篩選（獨立一列避免擠壓）
        mid2 = ttk.Frame(self.root, padding=(6, 2))
        mid2.pack(fill="x")
        ttk.Label(mid2, text=tr("lbl_model")).pack(side="left")
        self.model_box = ttk.Combobox(mid2, width=30)
        self.model_box.pack(side="left", padx=(4, 4))
        self.model_box.bind("<<ComboboxSelected>>", self.on_model_selected)
        self.model_box.bind("<KeyRelease>", self._on_model_filter)
        # 目前模型完整列表（用於篩選）
        self._all_model_list = []
        ttk.Label(mid2, text=tr("lbl_model_filter_hint")).pack(side="left")
        self.model_filter_entry = ttk.Entry(mid2, width=20, font=("Microsoft JhengHei", 9))
        self.model_filter_entry.pack(side="left", padx=(2, 4))
        self.model_filter_entry.bind("<KeyRelease>", self._on_model_filter_entry)
        self.model_filter_entry.bind("<Return>", lambda e: self.model_box.focus())

        ttk.Label(mid2, text=tr("lbl_lang")).pack(side="left", padx=(12, 0))
        self.lang_box = ttk.Combobox(
            mid2, state="readonly", width=10,
            values=[name for _, name in CONVO_LANGS],
        )
        self.lang_box.set(CONVO_LANG_NAMES[self.convo_lang])
        self.lang_box.pack(side="left", padx=(4, 0))
        self.lang_box.bind("<<ComboboxSelected>>", self.on_lang_selected)

        # 第三列：翻譯引擎（Google / Ollama，Ollama 可自選模型）＋ Dolphin 後製＋停止朗讀
        mid3 = ttk.Frame(self.root, padding=(6, 2))
        mid3.pack(fill="x")

        # 翻譯引擎切換（Google / Ollama），影響非日語回覆的日文朗讀稿
        ttk.Label(mid3, text=tr("lbl_translate_engine")).pack(side="left")
        self.translate_engine_var = tk.StringVar(value=DEFAULT_TRANSLATION_PROVIDER)
        self.translate_engine_box = ttk.Combobox(
            mid3, state="readonly", width=10,
            values=["google", "ollama"],
            textvariable=self.translate_engine_var,
        )
        self.translate_engine_box.pack(side="left", padx=(4, 0))
        self.translate_engine_box.bind(
            "<<ComboboxSelected>>", self._on_translate_provider_changed
        )
        # 翻譯模型下拉（選 ollama 時才可編輯，可手動輸入自訂 tag）
        self.translate_model_box = ttk.Combobox(mid3, width=20, state="disabled")
        self.translate_model_box.pack(side="left", padx=(8, 0))
        self.translate_model_box.bind(
            "<<ComboboxSelected>>", self.on_translate_model_selected
        )

        # Dolphin 分流：強模型正常生成後由 Dolphin 做詞彙／語氣後製
        ttk.Checkbutton(
            mid3, text=tr("btn_dolphin"), variable=self.dolphin_use_var,
            command=self._on_dolphin_toggle,
        ).pack(side="left", padx=(16, 2))
        self.dolphin_model_box = ttk.Combobox(mid3, width=22, state="disabled")
        self.dolphin_model_box.pack(side="left", padx=(0, 8))
        self.dolphin_model_box.bind(
            "<<ComboboxSelected>>", self.on_dolphin_model_selected
        )

        ttk.Button(mid3, text=tr("btn_stop"), command=lambda: self.stop_speaking()).pack(
            side="right", padx=4
        )

        # 角色設定列：單行 Entry + 編輯對話框按鈕 + 套用按鈕
        prow = ttk.Frame(self.root, padding=(6, 4))
        prow.pack(fill="x")

        ttk.Label(prow, text=tr("lbl_persona")).pack(side="left")
        self.persona_entry = ttk.Entry(prow, width=18, font=("Microsoft JhengHei", 11))
        self.persona_entry.pack(side="left", padx=(4, 4))
        self.persona_entry.bind("<Return>", lambda e: self.apply_persona())
        ttk.Button(prow, text=tr("btn_edit_persona"), command=self.open_persona_editor).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(prow, text=tr("btn_apply_persona"), command=self.apply_persona).pack(side="left")

        # 3D 演出開關：開啟後模型回覆可附帶 [3d] JSON 指令驅動 3D 角色
        self.enable_3d_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            prow, text=tr("chk_enable_3d"), variable=self.enable_3d_var,
            command=self._on_toggle_3d,
        ).pack(side="right", padx=8)

        # 對話管理列
        srow = ttk.Frame(self.root, padding=(6, 0))
        srow.pack(fill="x")

        ttk.Label(srow, text=tr("lbl_session")).pack(side="left")
        self.session_box = ttk.Combobox(srow, state="readonly", width=30)
        self.session_box.pack(side="left", padx=(4, 10))
        self.new_btn = ttk.Button(srow, text=tr("btn_new"), command=self.new_session)
        self.new_btn.pack(side="left", padx=(0, 4))
        self.load_btn = ttk.Button(srow, text=tr("btn_load"), command=self.load_selected_session)
        self.load_btn.pack(side="left", padx=(0, 4))
        self.rename_btn = ttk.Button(srow, text=tr("btn_rename"), command=self.rename_selected_session)
        self.rename_btn.pack(side="left", padx=(0, 4))
        self.delete_btn = ttk.Button(srow, text=tr("btn_delete"), command=self.delete_selected_session)
        self.delete_btn.pack(side="left")
        self.memory_btn = ttk.Button(srow, text=tr("btn_memory"), command=self.save_memory_manual)
        self.memory_btn.pack(side="left", padx=(8, 4))

        self.chat = scrolledtext.ScrolledText(
            self.root, state="disabled", wrap="word", font=("Microsoft JhengHei", 11)
        )
        self.chat.pack(fill="both", expand=True, padx=6, pady=6)
        self.chat.tag_configure("user", foreground="#0066cc")
        self.chat.tag_configure("jp", foreground="#000000")
        self.chat.tag_configure("zh", foreground="#777777")
        self.chat.tag_configure("sys", foreground="#aa2222")

        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(fill="x")

        # 多行輸入區；Enter 送出、Shift+Enter 換行
        self.input_box = tk.Text(bottom, height=6, font=("Microsoft JhengHei", 12))
        self.input_box.pack(side="left", fill="both", expand=True)
        self.input_box.bind("<Return>", self._on_return)

        style = ttk.Style()
        style.configure(
            "Big.TButton", font=("Microsoft JhengHei", 12, "bold"), padding=(16, 12)
        )
        # 操作按鈕改為上下排列，保留輸入框的可用寬度。
        action_buttons = ttk.Frame(bottom)
        action_buttons.pack(side="right", fill="y", padx=(8, 0))
        self.send_button = ttk.Button(
            action_buttons, text=tr("btn_send"), command=self.send_message,
            style="Big.TButton"
        )
        self.send_button.pack(fill="x", pady=(0, 4))
        self.retry_btn = ttk.Button(
            action_buttons, text=tr("btn_retry"), command=self.retry_last
        )
        self.retry_btn.pack(fill="x")
        self.retry_translate_btn = ttk.Button(
            action_buttons, text=tr("btn_retry_translate"),
            command=self.retry_translation
        )
        self.retry_translate_btn.pack(fill="x", pady=(4, 0))

    def _clear_chat_display(self):
        """清空對話顯示區。"""
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")

    def _append(self, text, tag=None):
        """在對話區附加文字（主執行緒呼叫）。"""
        self.chat.configure(state="normal")
        if tag:
            self.chat.insert("end", text, tag)
        else:
            self.chat.insert("end", text)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _register_ai_message(self, conv, voice, cmds=None):
        """顯示一則 AI 回覆；對話語言行在前，朗讀行（底線）可雙擊重播。

        cmds 為該則回覆的 3D 指令清單，雙擊重播朗讀時一併送出，
        讓歷史訊息的行為與當時 live 演出一致。
        """
        self.replay_seq += 1
        tag = f"replay_{self.replay_seq}"
        self.replay_texts[tag] = voice
        if isinstance(cmds, list) and cmds:
            self.replay_3d[tag] = cmds

        self.chat.configure(state="normal")
        # 重播標籤只負責底線與滑鼠事件，顏色交給一般標籤
        self.chat.tag_configure(tag, underline=True)
        self.chat.tag_bind(tag, "<Double-Button-1>", lambda e, t=tag: self.replay_message(t))
        self.chat.tag_bind(tag, "<Enter>", lambda e: self.chat.configure(cursor="hand2"))
        self.chat.tag_bind(tag, "<Leave>", lambda e: self.chat.configure(cursor=""))

        main_label = {"ja": "ai_jp", "zh": "ai_zh", "en": "ai_en"}[self.convo_lang]
        self.chat.insert("end", tr(main_label))
        if conv == voice:
            # 單行模式（日本語）：同一份文字直接掛重播標籤
            self.chat.insert("end", f"{conv}\n", tag)
        else:
            self.chat.insert("end", f"{conv}\n")
            # 次要行：實際朗讀的日文，灰色並可雙擊重播
            self.chat.insert("end", tr("ai_jp"), ("zh",))
            self.chat.insert("end", f"{voice}\n", (tag, "zh"))
        self.chat.insert("end", "\n")
        self.chat.see("end")
        self.chat.configure(state="disabled")
        self.displayed_asst_count += 1

        if not self.replay_hint_shown:
            self.replay_hint_shown = True
            self._append(tr("hint_replay"), "sys")

    def _refresh_replay_for_index(self, history_idx, voice):
        """重新翻譯成功後，更新對應回覆的朗讀稿與重播資料。"""
        conv = self.history[history_idx].get("content", "")
        # 找到 replay_texts 中空 voice 的 tag（即翻譯失敗時建立的）
        for tag in list(self.replay_texts):
            if not self.replay_texts[tag]:
                self.replay_texts[tag] = voice
                break

    # ---------- 佇列輪詢：工作執行緒透過佇列更新畫面 ----------

    def _poll_queue(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "text":
                    self._append(msg[1], msg[2] if len(msg) > 2 else None)
                elif kind == "ai_msg":
                    # msg：conv, voice, 3d 指令清單（可省略）
                    cmds = msg[3] if len(msg) > 3 else None
                    self._register_ai_message(msg[1], msg[2], cmds)
                elif kind == "engine_ok":
                    self.engine_status.configure(text=msg[1], foreground="#1a7f37")
                elif kind == "engine_ng":
                    self.engine_status.configure(text=msg[1], foreground="#c62828")
                elif kind == "ollama_ok":
                    self.ollama_status.configure(text=msg[1], foreground="#1a7f37")
                elif kind == "ollama_ng":
                    self.ollama_status.configure(text=msg[1], foreground="#c62828")
                elif kind == "or_ok":
                    self.or_status.configure(text=msg[1], foreground="#1a7f37")
                elif kind == "or_ng":
                    self.or_status.configure(text=msg[1], foreground="#c62828")
                elif kind == "viewer3d_ok":
                    self.viewer_status.configure(text=msg[1], foreground="#1a7f37")
                elif kind == "viewer3d_ng":
                    self.viewer_status.configure(text=msg[1], foreground="#c62828")
                elif kind == "voices":
                    self._load_voices(msg[1])
                elif kind == "style_translations":
                    self._apply_style_translations(msg[1])
                elif kind == "ollama_models":
                    self.ollama_models = msg[1]
                    if self.provider == "ollama":
                        self._apply_models()
                    # Dolphin 渲染一律取自本機 Ollama 清單
                    if self.dolphin_use_var.get():
                        self._refresh_dolphin_models()
                    # 翻譯引擎若選 ollama，同步更新翻譯模型下拉
                    if self.translate_engine_var.get() == "ollama":
                        self._refresh_translate_models()
                elif kind == "or_models":
                    self.openrouter_models = msg[1]
                    if self.provider == "openrouter":
                        self._apply_models()
                elif kind == "sessions":
                    # msg：names, paths(dict name->str), select_name
                    self.session_paths = {n: Path(p) for n, p in msg[2].items()}
                    self.session_box.configure(values=msg[1])
                    if msg[3] and msg[3] in msg[1]:
                        self.session_box.set(msg[3])
                elif kind == "restore":
                    # 啟動時自動還原最近對話：不觸發 3D 自動重播
                    self.handle_restore(msg[1], msg[2], replay_3d=False)
                    # 只要歷史裡有內容（含呼叫失敗後留下的待重送訊息）就可以重新生成
                    self.retry_btn.configure(
                        state="disabled" if not self.history else "normal"
                    )
                elif kind == "busy":
                    self.busy = msg[1]
                    state = "disabled" if self.busy else "normal"
                    self.send_button.configure(state=state)
                    for btn in (self.new_btn, self.load_btn, self.rename_btn, self.delete_btn, self.memory_btn, self.retry_btn, self.retry_translate_btn):
                        btn.configure(state=state)
                elif kind == "retry_translate_done":
                    target_idx, voice = msg[1], msg[2]
                    # 重繪該則回覆的朗讀行（更新 replay tag）
                    self._refresh_replay_for_index(target_idx, voice)
                    self._emit("busy", False)
        except queue.Empty:
            pass
        # retry 按鈕：忙碌中或歷史為空時 disable；
        # 呼叫失敗後歷史仍保留待重送的使用者訊息，因此不能只看 displayed_asst_count
        self.retry_btn.configure(
            state="disabled" if self.busy or not self.history else "normal"
        )
        # 重新翻譯按鈕：忙碌中或沒有缺 voice 的回覆時 disable
        has_untranslated = any(
            m.get("role") == "assistant" and not m.get("voice")
            for m in self.history
        )
        self.retry_translate_btn.configure(
            state="disabled" if self.busy or not has_untranslated else "normal"
        )
        self.root.after(100, self._poll_queue)

    def _emit(self, *msg):
        """從任意執行緒排程一個畫面更新。"""
        self.ui_queue.put(msg)

    # ---------- 初始化 ----------

    def init_backend(self):
        """背景檢查各服務並載入清單，最後還原上次的對話。"""
        # 檢查引擎並載入聲音清單；引擎未啟動時改為純文字模式（不朗讀）
        try:
            version = http_json("/version", ENGINE_URL)
            self._emit("engine_ok", f"{tr('conn_ok')}（{version}）")
            speakers = http_json("/speakers", ENGINE_URL)
            self.voice_available = True
        except Exception:
            self._emit("engine_ng", tr("conn_ng"))
            self.voice_available = False
            speakers = []

        voices = self._collect_voices(speakers)
        if not voices:
            # 引擎未連線時提示純文字模式；連線但沒有聲音才提示缺聲音
            if self.voice_available:
                self._emit("text", tr("msg_no_voices"), "sys")
            else:
                self._emit("text", tr("msg_voice_off"), "sys")
        self._emit("voices", voices)
        # 已知常用類型先立刻顯示人工校對譯名，不必等網路翻譯。
        builtin_translations = dict(VOICE_STYLE_TRANSLATIONS.get(UI_LANG, {}))
        if builtin_translations:
            self._emit("style_translations", builtin_translations)
        # 風格名稱的翻譯可能需要網路，放在背景初始化執行，避免卡住 Tk UI。
        translations = self._translate_voice_styles(voices, UI_LANG)
        if translations and translations != builtin_translations:
            self._emit("style_translations", translations)

        # 檢查 Ollama 並載入模型清單
        try:
            tags = http_json("/api/tags", OLLAMA_URL, timeout=10)
            models = [
                m["name"] for m in tags.get("models", []) if "embed" not in m["name"].lower()
            ]
            self._emit("ollama_ok", tr("conn_ok"))
            self._emit("ollama_models", models)
        except Exception:
            self._emit("ollama_ng", tr("conn_ng"))
            self._emit("ollama_models", [])

        # 載入 OpenRouter 模型清單（公開端點，不需金鑰）
        if not OPENROUTER_API_KEY:
            self._emit("or_ng", tr("key_missing"))
        try:
            data = http_json("/models", OPENROUTER_URL, timeout=20)

            def keep(model_id):
                if not OPENROUTER_KEEP_FREE_ONLY:
                    return True
                lowered = model_id.lower()
                return lowered.endswith(":free") or "ox-alpha" in lowered

            ids = sorted(m["id"] for m in data.get("data", []) if keep(m["id"]))
            self._emit(
                "or_ok",
                tr("available")
                + (tr("paren_key") if OPENROUTER_API_KEY else tr("paren_nokey")),
            )
            self._emit("or_models", ids)
        except Exception:
            self._emit("or_ng", tr("list_failed"))
            self._emit("or_models", [])

        # 掃描目前介面語言的既有對話，準備還原最近使用的。
        # 介面語言切換時，--chat 會指定繼續開啟原 chat，即使其語言不同。
        pairs = scan_chat_files(self.convo_lang)
        latest = None
        if self.restore_path and self.restore_path.exists():
            try:
                data = json.loads(self.restore_path.read_text(encoding="utf-8"))
                display = data.get("name") or self.restore_path.stem
                if not any(path == self.restore_path for _name, path in pairs):
                    pairs.insert(0, (display, self.restore_path))
                latest = (data, str(self.restore_path))
            except Exception:
                latest = None
        names = [d for d, _ in pairs]
        paths = {d: str(p) for d, p in pairs}
        if latest is None and pairs:
            disp, p = pairs[0]
            try:
                latest = (json.loads(p.read_text(encoding="utf-8")), str(p))
            except Exception:
                latest = None
        select = names[0] if pairs else None
        self._emit("sessions", names, paths, select)
        self._emit("restore", latest[0] if latest else None, latest[1] if latest else None)
        # 開機後更新一次 3D 檢視器狀態燈（之後依實際送指令結果變換）
        self._report_viewer_status()

    def _collect_voices(self, speakers):
        """組出角色清單，每個角色含其風格子清單。

        回傳結構：[(speaker_name, speaker_uuid, [(style_name, style_id), ...]), ...]
        仍依 PREFERRED_SPEAKERS 排序（偏好角色在前），未命中的角色接在後面。
        """
        results = []
        for speaker in speakers:
            name = speaker.get("name", "")
            uuid = speaker.get("speaker_uuid", "")
            styles = [
                (style.get("name", ""), style["id"])
                for style in speaker.get("styles", [])
                if "id" in style
            ]
            if not styles:
                continue
            results.append((name, uuid, styles))
        # 偏好角色排前面（依 PREFERRED_SPEAKERS 順序）
        def sort_key(item):
            name = item[0]
            for idx, (_, aliases) in enumerate(PREFERRED_SPEAKERS):
                if any(alias.lower() in name.lower() for alias in aliases):
                    return (0, idx, name)
            return (1, 0, name)
        results.sort(key=sort_key)
        return results

    def _load_voices(self, voices):
        """接收 _collect_voices 的巢狀結構，填入角色/風格兩個 combobox。

        啟動時一律以「只顯示偏好角色」模式載入（由 self.filter_voices_var 控制）。
        """
        # voices: [(speaker_name, uuid, [(style_name, style_id), ...]), ...]
        self.all_voices = voices
        # 依當前篩選開關決定要顯示哪些角色
        self._apply_voice_filter()
        # 預設選擇第一個偏好角色
        if self.speaker_map:
            first_name = self.speaker_box["values"][0]
            self.speaker_box.set(first_name)
            self.on_speaker_selected(announce=False)

    def _translate_voice_styles(self, voices, lang):
        """以 deep-translator 將 VOICEVOX 日文風格名稱翻成目前介面語言。"""
        if lang == "ja":
            return {}
        translated_map = dict(VOICE_STYLE_TRANSLATIONS.get(lang, {}))
        target = {"zh": "zh-TW", "en": "en"}.get(lang)
        if not target:
            return translated_map
        names = sorted({
            style_name
            for _speaker, _uuid, styles in voices
            for style_name, _style_id in styles
            if style_name.strip() and style_name not in translated_map
        })
        if not names or GoogleTranslator is None:
            return translated_map
        try:
            remote_translations = GoogleTranslator(source="ja", target=target).translate_batch(names)
            translated_map.update({
                name: result.strip()
                for name, result in zip(names, remote_translations)
                if result and result.strip() and result.strip() != name
            })
        except Exception:
            pass
        return translated_map

    def _style_label(self, style_name, style_id):
        """建立下拉選單標籤；譯名與原始日文並列，styleId 保持可機器解析。"""
        translated = self.style_translations.get(style_name)
        if translated:
            if UI_LANG == "zh":
                style_name = f"{translated}（{style_name}）"
            else:
                style_name = f"{translated} ({style_name})"
        return f"{style_name} [styleId={style_id}]"

    def _apply_style_translations(self, translations):
        """接收背景翻譯結果，刷新目前角色的風格標籤並保留既有選擇。"""
        self.style_translations = translations
        name = self.speaker_box.get()
        info = self.speaker_map.get(name)
        if not info:
            return
        _uuid, styles = info
        selected_id = self.speaker_id
        self.style_box["values"] = [
            self._style_label(style_name, style_id)
            for style_name, style_id in styles
        ]
        for style_name, style_id in styles:
            if style_id == selected_id:
                self.style_box.set(self._style_label(style_name, style_id))
                break

    def _apply_voice_filter(self):
        """依 self.show_only_preferred 篩選角色清單，填入 speaker_box。"""
        all_voices = getattr(self, "all_voices", [])
        if not all_voices:
            self.speaker_map = {}
            self.speaker_box["values"] = []
            return
        show_only = getattr(self, "show_only_preferred", True)
        if show_only:
            filtered = []
            for name, uuid, styles in all_voices:
                if any(
                    alias.lower() in name.lower()
                    for _, aliases in PREFERRED_SPEAKERS
                    for alias in aliases
                ):
                    filtered.append((name, uuid, styles))
        else:
            filtered = all_voices
        # 沒有偏好角色時，退回全部，避免下拉為空
        if show_only and not filtered:
            filtered = all_voices
        self.speaker_map = {
            name: (uuid, styles) for name, uuid, styles in filtered
        }
        self.speaker_box["values"] = list(self.speaker_map.keys())
        # 若目前選擇不在清單中，重設為第一個
        if self.speaker_box.get() not in self.speaker_map and self.speaker_map:
            self.speaker_box.set(next(iter(self.speaker_map)))

    def _apply_models(self):
        """依目前服務來源填入模型完整清單，套用目前篩選與預設選擇。"""
        self._all_model_list = (
            list(self.ollama_models) if self.provider == "ollama"
            else list(self.openrouter_models)
        )
        # Ollama 也允許手動輸入（不再鎖 readonly，方便打字篩選）
        self.model_box.configure(state="normal")
        if not self._all_model_list:
            self.model_box.configure(values=[])
            self.model_box.set("")
            self.on_model_selected()
            return
        # 套用篩選（含 OpenRouter 預設只顯示 :free）
        self._refresh_model_list()
        # 預設選擇第一個符合 PREFERRED 的項目，否則用第一個；
        # OpenRouter 固定優先預選 openrouter/free（字母序不一定排最前）。
        keys = PREFERRED_MODELS if self.provider == "ollama" else PREFERRED_OPENROUTER_MODELS
        visible = self.model_box["values"]
        if self.provider == "openrouter":
            default_label = next(
                (m for m in visible if m == "openrouter/free"),
                next(
                    (m for m in visible if any(key in m for key in keys)),
                    visible[0] if visible else "",
                ),
            )
        else:
            default_label = next(
                (m for m in visible if any(key in m for key in keys)),
                visible[0] if visible else "",
            )
        if default_label:
            self.model_box.set(default_label)
        self.on_model_selected()

    def _refresh_model_list(self):
        """依目前 show_only_free 與篩選框文字，重新組合 model_box 的 values。"""
        models = list(self._all_model_list)
        if self.provider == "openrouter" and getattr(self, "show_only_free", True):
            models = [m for m in models if m.lower().endswith(":free") or "ox-alpha" in m.lower()]
        # 文字篩選（不限大小寫子字串）
        keyword = ""
        if getattr(self, "model_filter_entry", None):
            keyword = self.model_filter_entry.get().strip().lower()
        if keyword:
            models = [m for m in models if keyword in m.lower()]
        self.model_box.configure(values=models)
        # 若目前選擇不在新清單中，保留原值不動（讓使用者可繼續輸入）

    def _on_model_filter(self, event=None):
        """在 model_box 內輸入時即時套用文字篩選（避免被視為完整模型名）。"""
        # 避免方向鍵、Enter、Esc 等觸發重新整理
        if event and event.keysym in (
            "Up", "Down", "Left", "Right", "Return", "Escape", "Tab",
        ):
            return
        if not getattr(self, "_all_model_list", None):
            return
        keyword = self.model_box.get().strip().lower()
        if not keyword:
            self._refresh_model_list()
            return
        models = [m for m in self._all_model_list if keyword in m.lower()]
        # 加上目前輸入值本身（讓使用者繼續輸入完整 ID）
        current = self.model_box.get().strip()
        if current and current not in models:
            models.append(current)
        self.model_box.configure(values=models)

    def _on_model_filter_entry(self, event=None):
        """獨立篩選輸入框變更時，重新過濾 model_box 內容。"""
        if not getattr(self, "_all_model_list", None):
            return
        self._refresh_model_list()

    def on_provider_selected(self, event=None, announce=True):
        provider = self.provider_box.get()
        self.provider = "openrouter" if "OpenRouter" in provider else "ollama"
        if announce:
            self._append(tr("msg_switched_provider").format(provider), "sys")
        self._apply_models()

    def on_speaker_selected(self, event=None, announce=True):
        """角色（speaker）變更：刷新風格下拉，並自動選取第一個風格。"""
        name = self.speaker_box.get()
        info = getattr(self, "speaker_map", {}).get(name)
        if not info:
            return
        _uuid, styles = info
        # 風格清單（顯示用：含樣式名稱 + styleId）
        self.style_box["values"] = [
            self._style_label(s_name, s_id) for s_name, s_id in styles
        ]
        if styles:
            self.style_box.current(0)
            self.on_voice_style_selected(announce=announce)

    def on_voice_style_selected(self, event=None, announce=True):
        """風格變更：設定 self.speaker_id 為 styleId（沿用 VOICEVOX 規格）。"""
        label = self.style_box.get()
        # 從 label 反查 styleId
        if not label:
            return
        try:
            style_id = int(label.rsplit("styleId=", 1)[-1].rstrip("]"))
        except Exception:
            return
        self.speaker_id = style_id
        if announce:
            speaker_name = self.speaker_box.get()
            style_name = label.rsplit(" [", 1)[0]
            self._append(
                tr("msg_selected_voice").format(f"{speaker_name}（{style_name}）"),
                "sys",
            )

    def on_toggle_preferred_voices(self):
        """切換是否只顯示偏好角色；切換後自動還原目前選擇（若有對應）。"""
        self.show_only_preferred = bool(self.filter_voices_var.get())
        prev_speaker_id = self.speaker_id
        self._apply_voice_filter()
        # 嘗試還原原本選的角色
        if prev_speaker_id is not None:
            for name, (_uuid, styles) in self.speaker_map.items():
                for s_name, s_id in styles:
                    if s_id == prev_speaker_id:
                        self.speaker_box.set(name)
                        self.on_speaker_selected(announce=False)
                        return
        # 找不到就選第一個
        if self.speaker_map:
            first = next(iter(self.speaker_map))
            self.speaker_box.set(first)
            self.on_speaker_selected(announce=False)

    def on_lang_selected(self, event=None):
        """只切換介面語言，保留目前 chat 的語言與檔案內容。"""
        disp = self.lang_box.get()
        code = next((c for c, n in CONVO_LANGS if n == disp), None)
        if not code or code == UI_LANG:
            return
        if self.busy:
            self.lang_box.set(CONVO_LANG_NAMES[UI_LANG])
            return
        self.write_session_file()
        restart_app(lang=code, chat_path=str(self.current_path) if self.current_path else None)
        self.root.destroy()

    def on_model_selected(self, event=None):
        self.model_name = self.model_box.get()

    # ---------- Dolphin 分流設定（渲染階段一律使用本機 Ollama） ----------

    def _on_dolphin_toggle(self):
        """Dolphin 開關切換：啟用時載入 Ollama 模型清單並可編輯輸入。"""
        if self.dolphin_model_box is None:
            return
        if self.dolphin_use_var.get():
            self.dolphin_model_box.configure(state="normal")
            self._refresh_dolphin_models()
        else:
            self.dolphin_model_box.configure(state="disabled")
            self.dolphin_model = ""

    # ---------- 翻譯引擎設定（Google / Ollama，Ollama 可自選模型） ----------

    def _on_translate_provider_changed(self, event=None):
        """翻譯引擎切換：選 ollama 時啟用模型下拉並載入清單。"""
        if self.translate_model_box is None:
            return
        if self.translate_engine_var.get() == "ollama":
            self.translate_model_box.configure(state="normal")
            self._refresh_translate_models()
        else:
            self.translate_model_box.configure(state="disabled")

    def on_translate_model_selected(self, event=None):
        """翻譯模型下拉選擇：記下所選（也可手動輸入自訂 tag）。"""
        self.translate_model = (
            self.translate_model_box.get().strip() if self.translate_model_box else ""
        )

    def _refresh_translate_models(self):
        """把目前 Ollama 模型清單放入翻譯模型下拉；保留手動輸入並預設 qwen2.5:7b。"""
        if self.translate_model_box is None:
            return
        models = list(self.ollama_models)
        current = self.translate_model_box.get().strip()
        if current and current not in models:
            models.append(current)
        self.translate_model_box.configure(values=models)
        if not self.translate_model_box.get():
            default = (
                DEFAULT_TRANSLATION_MODEL
                if DEFAULT_TRANSLATION_MODEL in models
                else (models[0] if models else "")
            )
            self.translate_model_box.set(default)
        self.translate_model = self.translate_model_box.get().strip()

    def on_dolphin_model_selected(self, event=None):
        """Dolphin 模型下拉選擇：記下所選（也可手動輸入自訂 tag）。"""
        self.dolphin_model = (
            self.dolphin_model_box.get().strip() if self.dolphin_model_box else ""
        )

    def _refresh_dolphin_models(self):
        """把目前 Ollama 模型清單放入 Dolphin 下拉；保留手動輸入並預設 dolphin-llama3。"""
        if self.dolphin_model_box is None:
            return
        models = list(self.ollama_models)
        current = self.dolphin_model_box.get().strip()
        if current and current not in models:
            models.append(current)
        self.dolphin_model_box.configure(values=models)
        if not self.dolphin_model_box.get():
            default = (
                "dolphin-llama3"
                if "dolphin-llama3" in models
                else (models[0] if models else "")
            )
            self.dolphin_model_box.set(default)
        self.dolphin_model = self.dolphin_model_box.get().strip()

    # ---------- 對話工作階段管理 ----------

    def handle_restore(self, data, path_str, replay_3d=True):
        """還原一個對話工作階段；data 為 None 時建立全新對話。

        replay_3d 為 False（啟動時自動還原最近對話）時不自動重播 3D 指令，
        避免一開 app 模型就無故動起來；使用者手動切換對話時才重播。
        """
        if not data:
            # 沒有聊天紀錄時維持空白畫面；必須由使用者按「開新對話」才建立檔案。
            self.current_session = None
            self.current_path = None
            self.history = []
            self._clear_chat_display()
            self._append(tr("msg_need_session"), "sys")
            return

        provider = data.get("provider")
        if provider not in ("ollama", "openrouter"):
            provider = "ollama"
        history = data.get("history")
        if not isinstance(history, list):
            history = []

        session = {
            "name": data.get("name") or Path(path_str).stem,
            "created_at": data.get("created_at") or now_iso(),
            "updated_at": data.get("updated_at") or now_iso(),
            "speaker_id": data.get("speaker_id"),
            "provider": provider,
            "model": data.get("model", ""),
            "lang": data.get("lang") if data.get("lang") in CONVO_LANG_NAMES else DEFAULT_CONVO_LANG,
            "persona": data.get("persona", "") if isinstance(data.get("persona"), str) else "",
            "enable_3d": bool(data.get("enable_3d")),
            "history": history,
        }
        self.current_session = session
        self.current_path = Path(path_str)
        self.history = session["history"]

        # 將舊版 assistant JSON 轉成新版單語 content + voice。這也讓舊對話
        # 的日文朗讀稿確實由 deep-translator 產生，而非沿用模型附帶的 jp。
        # 使用對話檔記錄的翻譯引擎（或沿用 ollama 若該對話檔有選擇）。
        _session_provider = session.get(
            "translation_provider", DEFAULT_TRANSLATION_PROVIDER
        )
        _session_tmodel = session.get("translation_model", "")
        migrated, migration_errors = migrate_assistant_voices(
            self.history, session["lang"],
            provider=_session_provider,
            ollama_model=_session_tmodel or DEFAULT_TRANSLATION_MODEL,
        )

        self._clear_chat_display()

        # 還原角色設定到輸入框與記憶體
        self.persona = session["persona"]
        self.persona_entry.delete(0, "end")
        if self.persona:
            self.persona_entry.insert(0, self.persona)

        # 還原 3D 演出開關（設定不觸發 _on_toggle_3d 的連線檢查）
        self.enable_3d_var.set(bool(session["enable_3d"]))

        # 還原翻譯引擎（舊對話檔無此欄位時預設 google）
        self.translate_engine_var.set(
            session.get("translation_provider", DEFAULT_TRANSLATION_PROVIDER)
        )
        # 依引擎啟用／停用翻譯模型下拉，並還原自選的 Ollama 模型
        self._on_translate_provider_changed()
        saved_tmodel = session.get("translation_model", "")
        if (
            self.translate_engine_var.get() == "ollama"
            and saved_tmodel
            and self.translate_model_box is not None
        ):
            self.translate_model_box.set(saved_tmodel)
            self.translate_model = saved_tmodel

        # 還原服務與模型
        self.provider = provider
        self.provider_box.current(1 if provider == "openrouter" else 0)
        self._apply_models()
        saved_model = session["model"]
        if saved_model:
            if provider == "openrouter":
                self.model_box.set(saved_model)
            elif saved_model in (self.ollama_models or []):
                self.model_box.set(saved_model)
            self.model_name = self.model_box.get()

        # 還原 chat 的語言；介面下拉則維持 UI_LANG，兩者可不同。
        self.convo_lang = session["lang"]
        self.lang_box.set(CONVO_LANG_NAMES[UI_LANG])

        # 還原聲音（若該 styleId 仍存在）
        speaker_note = ""
        sid = session["speaker_id"]
        if isinstance(sid, int):
            # 從完整清單反查對應的 (speaker_name, style_name)
            # all_voices 的每筆資料是 (speaker_name, speaker_uuid, styles)，
            # 要解開三個元素；此前誤當成兩元素，載入既有對話時會崩潰。
            for name, _uuid, styles in getattr(self, "all_voices", []):
                for s_name, s_id in styles:
                    if s_id == sid:
                        # 確認目前篩選下此角色是否可見
                        if name in self.speaker_map:
                            self.speaker_box.set(name)
                            self.on_speaker_selected(announce=False)
                            # 設定正確的 style
                            target_label = self._style_label(s_name, s_id)
                            if target_label in self.style_box["values"]:
                                self.style_box.set(target_label)
                                self.on_voice_style_selected(announce=False)
                        else:
                            speaker_note = tr("msg_voice_missing").format(sid)
                        sid = None  # 標記已處理
                        break
                if sid is None:
                    break
            if sid is not None:
                speaker_note = tr("msg_voice_missing").format(sid)

        # 等 provider、模型、語言、角色與聲音都已還原後再寫檔，避免遷移
        # 意外以啟動中的預設值覆蓋該對話原本的設定。
        if migrated:
            self.write_session_file()

        # 重播歷史（不朗讀）；摘要用的 system 訊息不顯示
        shown = 0
        for m in history:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                self._append(f"{tr('you_prefix')}{content}\n", "user")
                shown += 1
            elif role == "assistant":
                conv, voice = reply_parts(content, session["lang"], m.get("voice"))
                # 註冊成可雙擊重播的訊息（只顯示不朗讀）；保留 3D 指令供雙擊時送出
                self._register_ai_message(conv, voice, m.get("3d"))
                shown += 1

        self._append(tr("msg_loaded").format(session["name"], shown), "sys")
        if migration_errors:
            self._append(
                "[部分舊回覆翻譯失敗，保留原始紀錄] " + migration_errors[0] + "\n",
                "sys",
            )
        if self.persona:
            self._append(tr("msg_persona_restored").format(self.persona), "sys")
        if speaker_note:
            self._append(speaker_note, "sys")

        # 載入歷史後，若啟用 3D 演出且是使用者主動切換對話，則依序重播累積的 3D 指令
        if self.enable_3d_var.get() and replay_3d:
            threading.Thread(target=self._replay_3d_history, daemon=True).start()

    def _replay_3d_history(self):
        """載入歷史對話後，依序重播每則回覆累積的 3D 指令。

        每則回覆的指令連續送出（表情與動作大致同時演出），
        之後暫停數秒讓動作完整播放，再繼續下一則；結束後還原為中性表情。
        檢視器未連線時指令自然失敗略過；重播進行中重複觸發會被略過。
        """
        lock = getattr(self, "_replay_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._replay_lock = lock
        if not lock.acquire(blocking=False):
            return
        try:
            # 先還原殘留狀態，避免上一次演出停在半途
            send_3d_command("reset")
            time.sleep(0.4)
            for m in self.history:
                cmds = m.get("3d") if isinstance(m, dict) else None
                if not isinstance(cmds, list):
                    continue
                for c in cmds:
                    if not isinstance(c, dict) or not c.get("action"):
                        continue
                    params = c.get("params") if isinstance(c.get("params"), dict) else {}
                    send_3d_command(c["action"], params)
                    self._report_viewer_status()
                    time.sleep(0.4)
                time.sleep(1.6)
            send_3d_command("expression", {"name": "neutral"})
            self._report_viewer_status()
        finally:
            lock.release()

    def write_session_file(self):
        """將目前對話（含當下聲音、服務、模型、角色）寫入磁檔。"""
        if self.current_session is None or self.current_path is None:
            return
        self.current_session["updated_at"] = now_iso()
        self.current_session["speaker_id"] = self.speaker_id
        self.current_session["provider"] = self.provider
        self.current_session["model"] = self.model_name
        self.current_session["lang"] = self.convo_lang
        self.current_session["persona"] = self.persona
        self.current_session["enable_3d"] = bool(self.enable_3d_var.get())
        self.current_session["translation_provider"] = self.translate_engine_var.get()
        self.current_session["translation_model"] = self.translate_model
        payload = json.dumps(self.current_session, ensure_ascii=False, indent=2)
        try:
            with self.file_lock:
                self.current_path.parent.mkdir(exist_ok=True)
                tmp = self.current_path.with_suffix(".tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self.current_path)
        except Exception as e:
            self._emit("text", tr("msg_save_failed").format(e), "sys")

    def refresh_session_list(self, select=None):
        """重掃 chats/ 更新下拉選單（只列出目前語言的對話；主執行緒呼叫）。"""
        pairs = scan_chat_files(self.convo_lang)
        self.session_paths = {d: p for d, p in pairs}
        names = [d for d, _ in pairs]
        self.session_box.configure(values=names)
        target = select or (self.current_session["name"] if self.current_session else None)
        if target in names:
            self.session_box.set(target)
        elif names:
            self.session_box.set(names[0])
        else:
            self.session_box.set("")

    def new_session(self, first=False):
        """開新的空白對話；目前對話先存檔不遺失。

        檔名為 chat_日期_時間_毫秒.json；顯示名稱預設為時間戳，之後可改名。
        """
        self.stop_requested = True
        winsound.PlaySound(None, winsound.SND_PURGE)
        if self.current_session is not None:
            self.write_session_file()

        # 新 chat 才採用目前介面選定的語言；既有 chat 絕不改語言。
        self.convo_lang = UI_LANG

        path = new_chat_filename()
        display_name = datetime.now().strftime("對話_%Y%m%d_%H%M%S")

        self.current_session = {
            "name": display_name,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "speaker_id": self.speaker_id,
            "provider": self.provider,
            "model": self.model_name,
            "lang": self.convo_lang,
            "persona": "",
            "enable_3d": bool(self.enable_3d_var.get()),
            "history": [],
        }
        self.history = self.current_session["history"]
        self.current_path = path
        # 新對話從預設夥伴開始，角色輸入框一併清空
        self.persona = ""
        self.persona_entry.delete(0, "end")
        self.write_session_file()
        self.refresh_session_list(select=display_name)
        self._clear_chat_display()
        if not first:
            self._append(tr("msg_new_chat").format(display_name), "sys")

    def load_selected_session(self):
        """載入下拉選單選中的對話。"""
        if self.busy:
            return
        name = self.session_box.get()
        path = self.session_paths.get(name)
        if path is None:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data.get("history", []), list):
                raise ValueError("history 格式錯誤")
        except Exception as e:
            self._append(tr("msg_load_failed").format(e), "sys")
            return
        self.handle_restore(data, str(path))
        self.session_box.set(name)

    def rename_selected_session(self):
        """重新命名選中的對話；只改檔案內的顯示名稱，檔名不變。"""
        if self.busy:
            return
        disp = self.session_box.get()
        path = self.session_paths.get(disp)
        if not disp or path is None:
            return
        # 若顯示名稱帶有同名區分編號（如「名稱（2）」），輸入框先去掉它
        base_name = re.sub(r"（\d+）$", "", disp)
        new = simpledialog.askstring(
            tr("dlg_rename_title"),
            tr("dlg_rename_prompt"),
            initialvalue=base_name,
            parent=self.root,
        )
        if not new or new.strip() == base_name or not new.strip():
            return
        new_name = " ".join(new.split())
        if self.current_path == path and self.current_session is not None:
            self.current_session["name"] = new_name
            self.write_session_file()
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["name"] = new_name
                with self.file_lock:
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    tmp.replace(path)
            except Exception as e:
                self._append(tr("msg_rename_failed").format(e), "sys")
                return
        self.refresh_session_list(select=new_name)
        self._append(tr("msg_renamed").format(base_name, new_name), "sys")

    def delete_selected_session(self):
        """刪除選中的對話檔案；若是目前對話則回到空白狀態。"""
        if self.busy:
            return
        name = self.session_box.get()
        path = self.session_paths.get(name)
        if path is None:
            return
        if not messagebox.askyesno(
            tr("dlg_delete_title"),
            tr("dlg_delete_body").format(name),
            parent=self.root,
        ):
            return
        try:
            path.unlink()
        except Exception as e:
            self._append(tr("msg_delete_failed").format(e), "sys")
            return
        # 對話檔案刪除時一併清除該對話的長期記憶
        if self.memory_store is not None and self.memory_store.enabled:
            try:
                self.memory_store.delete_chat(memory_store.MemoryStore.chat_id(path))
            except Exception:
                pass
        if self.current_path == path:
            self.current_session = None
            self.current_path = None
            self.history = []
            self._clear_chat_display()
            self.refresh_session_list()
            self._append(tr("msg_deleted").format(name), "sys")
            self._append(tr("msg_need_session"), "sys")
        else:
            self.refresh_session_list()
            self._append(tr("msg_deleted").format(name), "sys")

    def _rename_active_to(self, new_title, announce=True):
        """重新命名目前作用中的對話（只改顯示名稱，檔名不變）。"""
        if self.current_session is None:
            return
        self.current_session["name"] = new_title
        self.write_session_file()
        self.refresh_session_list(select=new_title)
        if announce:
            self._append(tr("msg_titled").format(new_title), "sys")

    # ---------- 角色設定 ----------

    def open_persona_editor(self):
        """開啟多行角色編輯對話框；確定後立即套用並更新單行 Entry 摘要。"""
        if self.current_session is None:
            self._append(tr("msg_no_session_persona"), "sys")
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(tr("dlg_persona_title"))
        dlg.transient(self.root)
        dlg.geometry("640x420")
        # 置中
        dlg.update_idletasks()
        x = (dlg.winfo_screenwidth() - dlg.winfo_width()) // 2
        y = (dlg.winfo_screenheight() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=tr("dlg_persona_prompt"), justify="left").pack(anchor="w")
        editor = tk.Text(
            frm, height=15, wrap="word",
            font=("Microsoft JhengHei", 11), undo=True,
        )
        editor.pack(fill="both", expand=True, pady=(6, 8))
        editor.insert("1.0", self.persona or "")

        # 快捷鍵
        def on_ok():
            text = editor.get("1.0", "end-1c").rstrip()
            self.persona = text
            # 更新單行 Entry 摘要
            self.persona_entry.delete(0, "end")
            summary = text.splitlines()[0] if text else ""
            if len(summary) > 60:
                summary = summary[:57] + "..."
            if summary:
                self.persona_entry.insert(0, summary)
            self.apply_persona()
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text=tr("btn_cancel"), command=on_cancel).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text=tr("btn_ok"), command=on_ok).pack(side="right")
        ttk.Button(
            btns, text=tr("btn_load_template"),
            command=lambda: editor.insert("end", _PERSONA_TEMPLATE),
        ).pack(side="left")
        # Enter 確定、Esc 取消
        dlg.bind("<Escape>", lambda e: on_cancel())
        editor.bind("<Control-Return>", lambda e: on_ok())
        editor.focus_set()
        try:
            dlg.grab_set()
        except tk.TclError:
            pass

    def apply_persona(self):
        """套用角色設定：寫入目前對話檔並立即生效於下一則訊息。"""
        if self.current_session is None:
            self._append(tr("msg_no_session_persona"), "sys")
            return
        # 優先用 editor 編輯後的 self.persona（避免單行 Entry 截斷多行內容）
        text = (self.persona or "").strip()
        # 若 self.persona 為空，嘗試從單行 Entry 取（向後相容）
        if not text:
            text = self.persona_entry.get().strip()
            self.persona = text
        self.write_session_file()
        name = self.current_session["name"]
        if text:
            self._append(tr("msg_persona_applied").format(name, text), "sys")
        else:
            self._append(tr("msg_persona_cleared").format(name), "sys")

    def build_system_prompt(self):
        """組出系統提示：格式規範在前，3D／角色設定在後（避免破壞輸出格式）。"""
        prompt = SYSTEM_PROMPTS.get(self.convo_lang, SYSTEM_PROMPTS["zh"])
        if self.enable_3d_var.get():
            prompt += _3D_PROMPTS.get(self.convo_lang, _3D_PROMPTS["zh"])
        if self.persona:
            prompt += PERSONA_LABELS.get(self.convo_lang, "\n角色設定：") + self.persona
        return prompt

    def _report_viewer_status(self, connected=None):
        """更新狀態列的 3D 燈；connected 為 None 時即時探測檢視器存活狀態。"""
        if connected is None:
            connected = viewer_alive()
        self._emit(
            "viewer3d_ok" if connected else "viewer3d_ng",
            tr("conn_ok") if connected else tr("conn_ng"),
        )

    def _on_toggle_3d(self):
        """3D 開關被使用者點擊時，背景檢查檢視器是否執行中並更新狀態燈。"""
        if self.enable_3d_var.get():
            threading.Thread(target=self._report_viewer_status, daemon=True).start()

    # ---------- 事件 ----------

    def _on_return(self, event):
        """Enter 送出訊息；按住 Shift 時允許換行。"""
        if event.state & 0x0001:
            return None
        self.send_message()
        return "break"

    def send_message(self):
        user_text = self.input_box.get("1.0", "end-1c").strip()
        if not user_text or self.busy:
            return
        if self.current_session is None:
            self._append(tr("msg_need_session"), "sys")
            return
        # VOICEVOX 引擎未啟動時不擋訊息：LLM 照常回覆，僅跳過朗讀（speak 內處理）
        if not self.model_name:
            self._append(tr("msg_need_model"), "sys")
            return

        self.input_box.delete("1.0", "end")
        self._append(f"{tr('you_prefix')}{user_text}\n", "user")
        self._emit("busy", True)
        self.stop_requested = False
        threading.Thread(target=self.chat_worker, args=(user_text,), daemon=True).start()

    def stop_speaking(self, quiet=False):
        self.stop_requested = True
        winsound.PlaySound(None, winsound.SND_PURGE)
        if not quiet:
            self._append(tr("msg_stopped"), "sys")

    def retry_last(self):
        """重新生成，涵蓋兩種情況：

        1. 上一次呼叫失敗：該則使用者訊息仍留在歷史最後（chat_worker 失敗時
           不會移除它），直接重送即可，不需要刪除任何內容。
        2. 上一次呼叫成功但不滿意：歷史最後是 assistant 回覆，移除該回覆後，
           歷史最後即為要重送的 user 訊息。
        """
        if self.busy:
            self._append(tr("msg_busy_retry"), "sys")
            return
        if not self.history:
            self._append(tr("msg_nothing_retry"), "sys")
            return

        last = self.history[-1]
        if last.get("role") == "assistant":
            # 對回覆不滿意：移除最後一則 assistant 回覆
            self.history.pop()
            if not self.history or self.history[-1].get("role") != "user":
                self._append(tr("msg_nothing_retry"), "sys")
                return
            last_user = self.history[-1].get("content", "")
        elif last.get("role") == "user":
            # 上一次呼叫失敗：訊息本來就還在歷史裡，不需要刪除
            last_user = last.get("content", "")
        else:
            self._append(tr("msg_nothing_retry"), "sys")
            return

        # 重繪顯示（清除後重畫整個歷史）
        self._clear_chat_display()
        self.displayed_asst_count = 0
        self.replay_texts.clear()
        self.replay_3d.clear()
        self.replay_seq = 0
        self.replay_hint_shown = False
        for m in self.history:
            if m.get("role") == "user":
                self._append(f"{tr('you_prefix')}{m['content']}\n", "user")
            elif m.get("role") == "assistant":
                conv, voice = reply_parts(m["content"], self.convo_lang, m.get("voice"))
                self._register_ai_message(conv, voice, m.get("3d"))
        self._append(tr("msg_retrying"), "sys")
        self._emit("busy", True)
        self.stop_requested = False
        # 該則 user 訊息已經在歷史中，chat_worker 不需要再加入一次
        threading.Thread(
            target=self.chat_worker, args=(last_user,), kwargs={"append_user": False}, daemon=True
        ).start()

    def retry_translation(self):
        """重新翻譯最後一則沒有日文朗讀稿的 AI 回覆。"""
        if self.busy:
            self._append(tr("msg_busy_retry"), "sys")
            return
        if not self.history:
            self._append(tr("msg_nothing_retry_translate"), "sys")
            return
        # 從尾端往前找最後一則沒有 voice 的 assistant 回覆
        target_idx = None
        for i in range(len(self.history) - 1, -1, -1):
            m = self.history[i]
            if m.get("role") == "assistant" and not m.get("voice"):
                target_idx = i
                break
        if target_idx is None:
            self._append(tr("msg_nothing_retry_translate"), "sys")
            return
        self._append(tr("msg_retry_translate"), "sys")
        self._emit("busy", True)

        def worker():
            try:
                conv = self.history[target_idx]["content"]
                voice = translate_to_japanese(
                    conv, self.convo_lang,
                    provider=self.translate_engine_var.get(),
                    ollama_model=self.translate_model,
                )
                self.history[target_idx]["voice"] = voice
                self.write_session_file()
                self._emit("retry_translate_done", target_idx, voice)
            except Exception as e:
                self._emit("text", tr("msg_retry_translate_fail").format(e), "sys")
                self._emit("busy", False)

        threading.Thread(target=worker, daemon=True).start()

    def replay_message(self, tag):
        """重新合成播放指定則 AI 回覆的朗讀內容；開啟 3D 時先重播該則的動作指令。"""
        voice = self.replay_texts.get(tag)
        if not voice or self.busy:
            if self.busy:
                self._append(tr("msg_busy_replay"), "sys")
            return
        if self.speaker_id is None:
            self._append(tr("msg_no_voice"), "sys")
            return
        self._append(tr("msg_replaying"), "sys")
        self._emit("busy", True)
        self.stop_requested = False

        def worker():
            try:
                if self.enable_3d_var.get():
                    for c in self.replay_3d.get(tag, []):
                        if not isinstance(c, dict) or not c.get("action"):
                            continue
                        action = c.get("action") or "reset"
                        params = c.get("params") if isinstance(c.get("params"), dict) else {}
                        send_3d_command(action, params)
                        self._report_viewer_status()
                        time.sleep(0.3)
                self.speak(voice)
            finally:
                self._emit("busy", False)

        threading.Thread(target=worker, daemon=True).start()

    def on_close(self):
        self.stop_requested = True
        winsound.PlaySound(None, winsound.SND_PURGE)
        try:
            self.write_session_file()
        except Exception:
            pass
        self.root.destroy()

    # ---------- 背景：對話、摘要、標題與合成 ----------

    def call_llm(self, messages, provider=None, model=None, timeout=300):
        """依指定（預設為目前）的服務與模型呼叫聊天 API，回傳純文字。"""
        use_provider = provider or self.provider
        use_model = model or self.model_name
        if use_provider == "ollama":
            resp = post_json(
                "/api/chat",
                OLLAMA_URL,
                payload={"model": use_model, "messages": messages, "stream": False},
                timeout=timeout,
            )
            return json.loads(resp.decode("utf-8"))["message"]["content"]
        resp = post_json(
            "/chat/completions",
            OPENROUTER_URL,
            payload={"model": use_model, "messages": messages},
            headers={
                # 金鑰只存在記憶體中傳遞，不寫入任何輸出
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "http://localhost",
                "X-Title": "VOICEVOX-Ollama-Chat",
            },
            timeout=timeout,
        )
        return json.loads(resp.decode("utf-8"))["choices"][0]["message"]["content"]

    def maybe_summarize(self):
        """歷史超過上限時，呼叫目前模型把舊訊息壓縮成摘要。

        進行中會顯示提示且因忙碌鎖無法再送出新訊息；
        失敗時不阻擋對話，改用完整歷史繼續。
        """
        total_chars = sum(
            len(m.get("content", ""))
            for m in self.history
            if isinstance(m, dict) and isinstance(m.get("content"), str)
        )
        if total_chars <= HISTORY_CHAR_LIMIT:
            return
        if len(self.history) <= KEEP_RECENT_MESSAGES:
            return

        self._emit("text", tr("msg_summarizing"), "sys")
        # 摘要輸入同樣只取對話語言視圖，日文朗讀行不送模型
        older_view = llm_view(
            self.history[:-KEEP_RECENT_MESSAGES], self.convo_lang, include_stage=False
        )
        transcript = "\n".join(
            f"{tr('role_user') if m.get('role') == 'user' else 'AI'}：{m.get('content', '')}"
            for m in older_view
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        )
        ask = SUMMARY_ASKS.get(self.convo_lang, SUMMARY_ASKS["zh"]) + transcript
        try:
            summary = self.call_llm(
                [{"role": "user", "content": ask}], timeout=120
            ).strip()
        except Exception as e:
            self._emit("text", tr("msg_summary_failed").format(e), "sys")
            return
        if not summary:
            self._emit("text", tr("msg_summary_empty"), "sys")
            return

        self.history[:] = (
            [
                {
                    "role": "system",
                    "content": SUMMARY_HEADERS.get(
                        self.convo_lang, SUMMARY_HEADERS["zh"]
                    )
                    + summary,
                }
            ]
            + self.history[-KEEP_RECENT_MESSAGES:]
        )
        # 被摘要掉的舊對話順手抽成長期記憶；失敗不阻擋既有摘要流程
        chat_id = self._current_chat_id()
        if chat_id:
            try:
                self._run_memory_extract(
                    chat_id, transcript, self.convo_lang, "auto"
                )
            except Exception:
                pass
        self.write_session_file()
        self._emit("text", tr("msg_summary_done"), "sys")

    # ---------- 長期記憶（chroma_db） ----------

    def _current_chat_id(self):
        """回傳目前對話的隔離鍵（檔案 stem）；沒有對話或記憶庫不可用時回傳 None。"""
        if self.current_path is None or memory_store is None:
            return None
        try:
            return memory_store.MemoryStore.chat_id(self.current_path)
        except Exception:
            return None

    def extract_memories(self, transcript, lang):
        """呼叫目前模型從對話文字抽取可長期記憶的事實，回傳事實清單。

        失敗或結果為空時回傳空清單（呼叫端自行決定後續行為）。
        """
        ask = MEMORY_ASKS.get(lang, MEMORY_ASKS["zh"]) + transcript
        text = self.call_llm([{"role": "user", "content": ask}], timeout=120)
        facts = []
        for line in text.splitlines():
            fact = " ".join(line.strip().lstrip("-•·　*# ").split())
            if not fact:
                continue
            if fact in MEMORY_NONE_TOKENS or fact.casefold() == "none":
                continue
            facts.append(fact)
        return facts

    def _run_memory_extract(self, chat_id, transcript, lang, source):
        """抽取事實並寫入記憶庫；回傳實際新增筆數（失敗或不可用為 0）。"""
        if (
            self.memory_store is None
            or not self.memory_store.enabled
            or not chat_id
            or not transcript.strip()
        ):
            return 0
        facts = self.extract_memories(transcript, lang)
        if not facts:
            return 0
        return self.memory_store.add_facts(chat_id, facts, lang, source)

    def memory_block_for(self, query):
        """依使用者訊息檢索目前對話的過往記憶，回傳可附加的提示文字。

        無記憶或失敗時回傳空字串；僅在背景執行緒（chat_worker）中呼叫。
        """
        chat_id = self._current_chat_id()
        if self.memory_store is None or not chat_id:
            return ""
        hits = self.memory_store.retrieve(chat_id, query)
        if not hits:
            return ""
        facts = [fact for _dist, fact in hits]
        block = MEMORY_HEADERS.get(self.convo_lang, MEMORY_HEADERS["zh"])
        return block + "\n".join(f"- {f}" for f in facts) + "\n"

    def save_memory_manual(self):
        """把目前載入對話的全部內容抽成長期記憶（使用者手動觸發）。"""
        if self.busy:
            return
        if self.current_session is None or self.current_path is None:
            self._append(tr("msg_need_session"), "sys")
            return
        if self.memory_store is None or not self.memory_store.enabled:
            self._append(tr("msg_mem_unavailable"), "sys")
            return
        self._emit("busy", True)
        threading.Thread(target=self._memory_worker, daemon=True).start()

    def _memory_worker(self):
        """手動記憶的背景執行緒：組對話文字 → 抽取事實 → 寫入並回報。"""
        chat_id = self._current_chat_id()
        self.stop_requested = True
        winsound.PlaySound(None, winsound.SND_PURGE)
        view = llm_view(self.history, self.convo_lang, include_stage=False)
        transcript = "\n".join(
            f"{tr('role_user') if m.get('role') == 'user' else 'AI'}：{m.get('content', '')}"
            for m in view
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        )
        try:
            saved = self._run_memory_extract(
                chat_id, transcript, self.convo_lang, "manual"
            )
        except Exception as e:
            self._emit("text", tr("msg_mem_failed").format(e), "sys")
            self._emit("busy", False)
            return
        if saved:
            self._emit("text", tr("msg_mem_saved").format(saved), "sys")
        else:
            self._emit("text", tr("msg_mem_none"), "sys")
        self._emit("busy", False)

    def chat_worker(self, user_text, append_user=True):
        """處理一次完整的對話回合：摘要檢查 → 呼叫模型 → 存檔 → 朗讀。

        append_user 為 True（一般送出訊息）時才會把 user_text 加進歷史；
        重新生成時該則使用者訊息已經在歷史裡（見 retry_last），
        傳入 False 避免重複加入。
        """
        if self.provider == "openrouter" and not OPENROUTER_API_KEY:
            self._emit("text", tr("msg_no_key"), "sys")
            self._emit("busy", False)
            return

        if append_user:
            self.history.append({"role": "user", "content": user_text})

        # 歷史過長先整理（期間忙碌鎖維持，無法送出新訊息）
        self.maybe_summarize()

        # 送模型前先轉成對話語言視圖，朗讀用的日文行不佔 token；
        # 檢索到的過往記憶附加到系統提示結尾（僅供參考）
        system_prompt = self.build_system_prompt()
        memory_block = self.memory_block_for(user_text)
        if memory_block:
            system_prompt += memory_block
        history_view = llm_view(self.history, self.convo_lang)
        messages = [{"role": "system", "content": system_prompt}] + history_view
        # 強模型產生的 [3d] 指令（強模型為唯一來源，Dolphin 後製時不得增刪）
        pre_3d_commands = []
        try:
            if self.dolphin_use_var.get() and self.dolphin_model.strip():
                post_hint = DOLPHIN_POSTPROCESS_PROMPT.get(
                    self.convo_lang, DOLPHIN_POSTPROCESS_PROMPT["zh"]
                )
                if post_hint.strip():
                    # 階段1：強模型正常生成（含 [3d]）
                    self._emit("text", tr("msg_dolphin_start"), "sys")
                    base_reply = self.call_llm(messages, timeout=300)
                    # 抽出強模型的 [3d] 指令暫存
                    if self.enable_3d_var.get():
                        pre_3d_commands, base_clean = split_3d(base_reply)
                    else:
                        _, base_clean = split_3d(base_reply)
                    # 階段2：Dolphin 後製潤飾（不讀歷史，只讀原文）
                    reply = self.call_llm(
                        [
                            {"role": "system", "content": post_hint},
                            {"role": "user", "content": base_clean},
                        ],
                        provider="ollama",
                        model=self.dolphin_model.strip(),
                        timeout=300,
                    )
                else:
                    # 後製提示詞為空 → 跳過 Dolphin，直接用一般模型
                    self._emit("text", tr("msg_dolphin_skip"), "sys")
                    reply = self.call_llm(messages)
            else:
                reply = self.call_llm(messages)
        except Exception as e:
            # Dolphin 不可用（未下載模型、引擎未啟動等）時回退一般模型
            self._emit("text", tr("msg_dolphin_fallback").format(e), "sys")
            try:
                reply = self.call_llm(messages)
            except Exception as e2:
                self._emit("text", tr("msg_llm_failed").format(e2), "sys")
                self._emit("busy", False)
                return
        except Exception as e:
            # 呼叫失敗時保留使用者訊息，不從歷史移除，
            # 讓「重新生成」可以直接重試該則訊息，而不會誤刪前一則已成功的回覆
            self._emit("text", tr("msg_llm_failed").format(e), "sys")
            self._emit("busy", False)
            return

        # 3D 指令抽取：僅在開關開啟時發送；關閉時仍剝離意外殘留的 [3d] 區塊，
        # 避免指令當成一般文字顯示或誤送翻譯。
        commands = []
        if self.enable_3d_var.get():
            post_3d, reply = split_3d(reply)
            # 合併強模型與 Dolphin 的 [3d]（強模型為主要來源）
            commands = pre_3d_commands + post_3d
            for c in commands:
                action = c.get("action") or "reset"
                params = c.get("params") if isinstance(c.get("params"), dict) else {}
                send_3d_command(action, params)
                self._report_viewer_status()
        else:
            _, reply = split_3d(reply)

        # 模型回覆只保留使用者語言；日文朗讀稿獨立存放，永不送回模型。
        conv, legacy_voice = reply_parts(reply, self.convo_lang)
        try:
            voice = legacy_voice or translate_to_japanese(
                conv, self.convo_lang, provider=self.translate_engine_var.get(),
                ollama_model=self.translate_model,
            )
        except Exception as e:
            # 翻譯服務失敗不能讓整個對話消失；保留文字回覆並略過朗讀。
            voice = ""
            self._emit("text", f"[翻譯失敗，略過朗讀] {e}\n可按「重新翻譯」重試\n", "sys")
        assistant_message = {"role": "assistant", "content": conv}
        if commands:
            assistant_message["3d"] = commands
        if voice:
            assistant_message["voice"] = voice
        self.history.append(assistant_message)
        self.write_session_file()
        self._emit("ai_msg", conv, voice, commands if commands else [])

        self.speak(voice)

        # 第一則回覆後，背景向目前模型索取對話標題
        assistant_count = sum(1 for m in self.history if m.get("role") == "assistant")
        if (
            assistant_count == 1
            and self.current_session is not None
            and DEFAULT_NAME_PATTERN.match(self.current_session["name"])
        ):
            threading.Thread(target=self.auto_title_worker, daemon=True).start()

        self._emit("busy", False)

    def auto_title_worker(self):
        """以第一則對話內容向目前模型索取短標題；失敗則保留時間戳名稱。"""
        session = self.current_session
        if session is None:
            return
        provider = self.provider
        model = self.model_name
        lang = self.convo_lang
        # 標題輸入同樣只取對話語言視圖
        view = llm_view(session["history"], lang, include_stage=False)
        user_text = next(
            (m.get("content", "") for m in view if m.get("role") == "user"),
            "",
        )
        ai_text = next(
            (m.get("content", "") for m in view if m.get("role") == "assistant"),
            "",
        )
        ask = TITLE_ASKS.get(lang, TITLE_ASKS["zh"]) + (
            f"{tr('role_user')}：{user_text}\nAI：{ai_text}"
        )
        try:
            title = self.call_llm(
                [{"role": "user", "content": ask}],
                provider=provider,
                model=model,
                timeout=90,
            )
        except Exception:
            return
        title = clean_title(title)
        if not title:
            return
        # 若使用者已手動改名或切換到別的對話，就不要覆蓋
        if self.current_session is not session:
            return
        if not DEFAULT_NAME_PATTERN.match(session["name"]):
            return
        self.root.after(0, lambda: self._rename_active_to(title, announce=True))

    def speak(self, text):
        """逐句合成並同步播放；可由停止按鈕中斷。傳入文字為朗讀用日文。

        VOICEVOX 引擎未啟動（或無可用聲音）時直接略過，不影響已顯示的文字回覆。
        3D 演出開啟時，同步送出口型指令（speak 開始／結束），
        中途停止也會在 finally 中送出結束指令。
        """
        if not self.voice_available or self.speaker_id is None:
            return
        lipsync_3d = self.enable_3d_var.get()
        if lipsync_3d:
            send_3d_command("lipsync", {"active": True})
            self._report_viewer_status()
        try:
            seq = 0
            for sentence in split_sentences(text):
                if self.stop_requested:
                    return
                try:
                    # /audio_query 為 POST，參數在網址、內容為空
                    query = json.loads(
                        post_json(
                            "/audio_query",
                            ENGINE_URL,
                            {"text": sentence, "speaker": self.speaker_id},
                        ).decode("utf-8")
                    )
                    wav_data = post_json(
                        "/synthesis",
                        ENGINE_URL,
                        {"speaker": self.speaker_id},
                        query,
                        timeout=120,
                    )
                except Exception as e:
                    # 引擎中途關閉：停止後續嘗試，改回純文字回應
                    self.voice_available = False
                    self._emit("text", tr("msg_tts_failed").format(e), "sys")
                    return
                seq += 1
                wav_path = str(TMP_DIR / f"reply_{threading.get_ident()}_{seq}.wav")
                with open(wav_path, "wb") as f:
                    f.write(wav_data)
                if self.stop_requested:
                    return
                winsound.PlaySound(wav_path, winsound.SND_FILENAME)
        finally:
            if lipsync_3d:
                send_3d_command("lipsync", {"active": False})
                self._report_viewer_status()


def main():
    root = tk.Tk()
    root.withdraw()

    # 切換語言時的重啟會帶 --lang 參數：此時不再詢問，直接套用該語言
    arg_lang = None
    arg_chat_path = None
    for a in sys.argv[1:]:
        if a.startswith("--lang="):
            v = a.split("=", 1)[1].strip().lower()
            if v in CONVO_LANG_NAMES:
                arg_lang = v
        elif a.startswith("--chat="):
            candidate = Path(a.split("=", 1)[1])
            if candidate.exists() and candidate.is_file():
                arg_chat_path = candidate

    # 啟動視窗本身先以預估語言（最新聊天紀錄的語言）呈現
    set_ui_lang(arg_lang or latest_chat_lang())

    # 有對話紀錄時自動用上次語言，只有第一次啟動（無對話）才跳出選擇視窗
    if arg_lang:
        chosen = arg_lang
    elif CHATS_DIR.exists() and any(CHATS_DIR.glob("*.json")):
        chosen = latest_chat_lang()
    else:
        chosen = ask_startup_language(root)

    if chosen is None:
        # 使用者取消語言選擇：結束程式
        root.destroy()
        return

    set_ui_lang(chosen)
    root.deiconify()
    app = VoiceChatApp(root, restore_path=arg_chat_path)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # 啟動期錯誤寫入記錄檔並保留主控台訊息，避免程式無聲消失難以排查
        import traceback

        err = traceback.format_exc()
        try:
            (get_base_dir() / "startup_error.log").write_text(
                err, encoding="utf-8"
            )
        except Exception:
            pass
        sys.stderr.write(err)
        try:
            input("\n發生錯誤，已寫入 startup_error.log。按 Enter 結束…")
        except Exception:
            pass
        raise
