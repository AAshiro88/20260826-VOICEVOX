# -*- coding: utf-8 -*-
"""VOICEVOX × Ollama／OpenRouter 多語言語音對話（Tkinter 圖形介面版）。

功能：
- 啟動時自動偵測 VOICEVOX 引擎與 Ollama／OpenRouter 狀態
- 下拉選單只列出偏好的 3 個聲音：猫使ビィ、小夜/SAYO、もち子さん
- 可切換對話來源：本機 Ollama 或 OpenRouter 免費模型（金鑰放在同目錄 .env）
- 對話語言可選日本語／中文／English：
  * 日本語：回覆為純日文，送模型、顯示與朗讀都用同一份原文，不需翻譯
  * 中文／English：回覆含「日:」行供 VOICEVOX 合成，送模型只送該語言行（省 token）
  * 朗讀一律使用日文行（VOICEVOX 只有日文發音正確）
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

僅使用 Python 標準庫，不需安裝第三方套件。
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
import urllib.parse
import urllib.request
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog, ttk
import winsound

ENGINE_URL = "http://127.0.0.1:50021"
OLLAMA_URL = "http://127.0.0.1:11434"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

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


def restart_app(lang=None):
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

# 各對話語言的系統提示
SYSTEM_PROMPTS = {
    "ja": (
        "あなたはVOICEVOX音声合成を通してユーザーと会話する仲間です。"
        "毎回の返答は自然な口語体の日本語だけで書いてください（見出しや翻訳行は不要）。"
        "Markdown・絵文字・箇条書きは使わず、短く会話調で返してください。"
    ),
    "zh": (
        "你是透過 VOICEVOX 語音合成與使用者對話的夥伴。VOICEVOX 只能朗讀日文，"
        "所以你的每一次回覆都必須嚴格輸出 JSON 格式，鍵名為：\n"
        "{\"zh\": \"<前述日文的繁體中文翻譯>\", \"jp\": \"<自然口語的日文回覆，將被朗讀>\"}\n"
        "不要使用 Markdown、表情符號或條列式，回覆保持簡短、口語化。\n"
        "注意：對話紀錄中你過去的回覆只會顯示中文譯文，但你每次的新回覆仍必須使用上述 JSON 格式。"
    ),
    "en": (
        "You are a companion chatting with the user through VOICEVOX speech synthesis. "
        "VOICEVOX can only read Japanese aloud, so every reply MUST strictly follow "
        "this JSON format with no other content:\n"
        "{\"en\": \"<your reply in natural spoken English>\", \"jp\": \"<a natural spoken Japanese version of the above, to be read aloud>\"}\n"
        "Do not use Markdown, emoji, or bullet lists. Keep replies short and conversational.\n"
        "Note: your past replies in the history show only the English line, "
        "but each new reply must still use the JSON format above."
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


def reply_parts(text, lang):
    """依對話語言從 JSON 回覆拆解，回傳（對話用文字, 朗讀用文字）。

    - 日本語模式：只取 jp 欄位
    - 中文／English 模式：取對應語言欄位 + jp (日文朗讀) 欄位
    """
    text = text.strip()
    parts, fallback = _extract_json(text)
    if lang == "ja":
        conv = parts.get("jp") or ("" if not parts else str(list(parts.values())[0]))
    else:
        # zh 或 en 模式：優先取對應語言，其次 jp (日文供 VOICEVOX 朗讀)
        conv = parts.get(lang) or parts.get("jp") or ("" if not parts else str(list(parts.values())[0]))
    voice = parts.get("jp") or ("" if not parts else "")
    return conv, voice


def assistant_conv_text(content, lang):
    """取出一則舊回覆中要送給模型的對話語言內容。

    目前語言的欄位不存在時（例如中途切換過語言），改取其他非日文欄位，
    最後才退回原文，確保不會整段雙語重送。
    """
    content = content.strip()
    parts, fallback = _extract_json(content)
    if lang == "ja":
        return parts.get("jp") or fallback or content
    conv = parts.get(lang) or parts.get("zh") or parts.get("en")
    return conv or fallback or content


def llm_view(history, lang):
    """產生送給模型的歷史視圖（不更動原始資料）。

    assistant 訊息只保留對話語言的內容以節省 token；
    user 與 system（摘要）訊息原樣保留。
    """
    view = []
    for m in history:
        if isinstance(m, dict) and m.get("role") == "assistant":
            conv = assistant_conv_text(str(m.get("content", "")), lang)
            if conv:
                view.append({"role": "assistant", "content": conv})
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
    def __init__(self, root):
        self.root = root
        root.title(tr("app_title"))
        root.geometry("800x640")

        self.ui_queue = queue.Queue()
        self.history = []  # 目前對話的訊息列表（與 current_session["history"] 同一物件）
        self.speaker_id = None
        self.model_name = ""
        self.provider = "ollama"
        # 對話語言（ja＝純日文；zh／en＝對話語言＋日文朗讀行的兩行格式），
        # 由啟動時的語言選擇（或重啟帶入的 --lang）決定
        self.convo_lang = UI_LANG
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
        self.replay_hint_shown = False
        self.displayed_asst_count = 0

        # 角色設定：屬於各對話工作階段，存取皆透過 current_session["persona"]
        self.persona = ""

        # 聲音/模型篩選旗標（啟動預設）
        self.show_only_preferred = True   # 角色：只顯示偏好 3 個
        self.show_only_free = True        # OpenRouter 模型：只顯示 :free / ox-alpha
        self.speaker_map = {}             # 角色名稱 → (uuid, [(style_name, style_id), ...])
        self.style_map = {}               # styleId → (speaker_name, style_name)
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
        self.speaker_box = ttk.Combobox(mid, state="readonly", width=18)
        self.speaker_box.pack(side="left", padx=(4, 2))
        self.speaker_box.bind("<<ComboboxSelected>>", self.on_speaker_selected)
        ttk.Label(mid, text=tr("lbl_voice_style")).pack(side="left")
        self.style_box = ttk.Combobox(mid, state="readonly", width=18)
        self.style_box.pack(side="left", padx=(2, 4))
        self.style_box.bind("<<ComboboxSelected>>", self.on_voice_style_selected)
        # 偏好角色篩選（預設勾選＝只顯示 PREFERRED_SPEAKERS 的角色）
        self.filter_voices_var = tk.BooleanVar(value=True)
        self.filter_voices_var.trace_add("write", lambda *_: self.on_toggle_preferred_voices())
        ttk.Checkbutton(
            mid, text=tr("btn_filter_preferred"), variable=self.filter_voices_var
        ).pack(side="left", padx=(0, 8))
        self.show_only_preferred = True  # 供 _apply_voice_filter 內部判斷用

        # 模型選擇：可編輯 combobox + 即時文字篩選
        ttk.Label(mid, text=tr("lbl_model")).pack(side="left")
        self.model_box = ttk.Combobox(mid, width=22)
        self.model_box.pack(side="left", padx=(4, 4))
        self.model_box.bind("<<ComboboxSelected>>", self.on_model_selected)
        self.model_box.bind("<KeyRelease>", self._on_model_filter)
        # 目前模型完整列表（用於篩選）
        self._all_model_list = []
        ttk.Label(mid, text=tr("lbl_model_filter_hint")).pack(side="left")
        self.model_filter_entry = ttk.Entry(mid, width=12, font=("Microsoft JhengHei", 9))
        self.model_filter_entry.pack(side="left", padx=(2, 4))
        self.model_filter_entry.bind("<KeyRelease>", self._on_model_filter_entry)
        self.model_filter_entry.bind("<Return>", lambda e: self.model_box.focus())

        # 語言：同時決定 AI 回覆語言與介面文字語言（介面變更時自動重啟套用）
        ttk.Label(mid, text=tr("lbl_lang")).pack(side="left")
        self.lang_box = ttk.Combobox(
            mid, state="readonly", width=10,
            values=[name for _, name in CONVO_LANGS],
        )
        self.lang_box.set(CONVO_LANG_NAMES[self.convo_lang])
        self.lang_box.pack(side="left", padx=(4, 0))
        self.lang_box.bind("<<ComboboxSelected>>", self.on_lang_selected)

        ttk.Button(mid, text=tr("btn_stop"), command=lambda: self.stop_speaking()).pack(
            side="right", padx=4
        )

        # 角色設定列：單行 Entry + 編輯對話框按鈕 + 套用按鈕
        prow = ttk.Frame(self.root, padding=(6, 4))
        prow.pack(fill="x")

        ttk.Label(prow, text=tr("lbl_persona")).pack(side="left")
        self.persona_entry = ttk.Entry(prow, font=("Microsoft JhengHei", 11))
        self.persona_entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self.persona_entry.bind("<Return>", lambda e: self.apply_persona())
        ttk.Button(prow, text=tr("btn_edit_persona"), command=self.open_persona_editor).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(prow, text=tr("btn_apply_persona"), command=self.apply_persona).pack(side="left")

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
        self.input_box = tk.Text(bottom, height=4, font=("Microsoft JhengHei", 12))
        self.input_box.pack(side="left", fill="both", expand=True)
        self.input_box.bind("<Return>", self._on_return)

        style = ttk.Style()
        style.configure(
            "Big.TButton", font=("Microsoft JhengHei", 12, "bold"), padding=(16, 12)
        )
        self.retry_btn = ttk.Button(
            bottom, text=tr("btn_retry"), command=self.retry_last
        )
        self.retry_btn.pack(side="right", padx=(0, 4), anchor="se")
        self.send_button = ttk.Button(
            bottom, text=tr("btn_send"), command=self.send_message, style="Big.TButton"
        )
        self.send_button.pack(side="right", padx=(6, 0), anchor="se")

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

    def _register_ai_message(self, conv, voice):
        """顯示一則 AI 回覆；對話語言行在前，朗讀行（底線）可雙擊重播。"""
        self.replay_seq += 1
        tag = f"replay_{self.replay_seq}"
        self.replay_texts[tag] = voice

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

    # ---------- 佇列輪詢：工作執行緒透過佇列更新畫面 ----------

    def _poll_queue(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "text":
                    self._append(msg[1], msg[2] if len(msg) > 2 else None)
                elif kind == "ai_msg":
                    self._register_ai_message(msg[1], msg[2])
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
                elif kind == "voices":
                    self._load_voices(msg[1])
                elif kind == "ollama_models":
                    self.ollama_models = msg[1]
                    if self.provider == "ollama":
                        self._apply_models()
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
                    self.handle_restore(msg[1], msg[2])
                    # 只要歷史裡有內容（含呼叫失敗後留下的待重送訊息）就可以重新生成
                    self.retry_btn.configure(
                        state="disabled" if not self.history else "normal"
                    )
                elif kind == "busy":
                    self.busy = msg[1]
                    state = "disabled" if self.busy else "normal"
                    self.send_button.configure(state=state)
                    for btn in (self.new_btn, self.load_btn, self.rename_btn, self.delete_btn, self.retry_btn):
                        btn.configure(state=state)
        except queue.Empty:
            pass
        # retry 按鈕：忙碌中或歷史為空時 disable；
        # 呼叫失敗後歷史仍保留待重送的使用者訊息，因此不能只看 displayed_asst_count
        self.retry_btn.configure(
            state="disabled" if self.busy or not self.history else "normal"
        )
        self.root.after(100, self._poll_queue)

    def _emit(self, *msg):
        """從任意執行緒排程一個畫面更新。"""
        self.ui_queue.put(msg)

    # ---------- 初始化 ----------

    def init_backend(self):
        """背景檢查各服務並載入清單，最後還原上次的對話。"""
        # 檢查引擎並載入聲音清單
        try:
            version = http_json("/version", ENGINE_URL)
            self._emit("engine_ok", f"{tr('conn_ok')}（{version}）")
            speakers = http_json("/speakers", ENGINE_URL)
        except Exception:
            self._emit("engine_ng", tr("conn_ng"))
            self._emit("text", tr("msg_engine_down"), "sys")
            speakers = []

        voices = self._collect_voices(speakers)
        if not voices:
            self._emit("text", tr("msg_no_voices"), "sys")
        self._emit("voices", voices)

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

        # 掃描目前語言的既有對話，準備還原最近使用的
        pairs = scan_chat_files(self.convo_lang)
        names = [d for d, _ in pairs]
        paths = {d: str(p) for d, p in pairs}
        latest = None
        if pairs:
            disp, p = pairs[0]
            try:
                latest = (json.loads(p.read_text(encoding="utf-8")), str(p))
            except Exception:
                latest = None
        select = names[0] if pairs else None
        self._emit("sessions", names, paths, select)
        self._emit("restore", latest[0] if latest else None, latest[1] if latest else None)

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
        # 預設選擇第一個符合 PREFERRED 的項目，否則用第一個
        keys = PREFERRED_MODELS if self.provider == "ollama" else PREFERRED_OPENROUTER_MODELS
        visible = self.model_box["values"]
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
        self.style_box["values"] = [f"{s_name} [styleId={s_id}]" for s_name, s_id in styles]
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
        """切換語言：綁定目前對話的新語言並存檔後，自動重新啟動套用。

        重啟時帶入 --lang 參數，跳過啟動語言選擇視窗，一次完成切換；
        進行中的朗讀與請求會被中斷。
        """
        disp = self.lang_box.get()
        code = next((c for c, n in CONVO_LANGS if n == disp), None)
        if not code or code == UI_LANG:
            return
        self.convo_lang = code
        self.write_session_file()
        self._append(tr("msg_switched_lang").format(disp), "sys")
        self._append(tr("msg_ui_restart"), "sys")
        restart_app(lang=code)
        self.root.destroy()

    def on_model_selected(self, event=None):
        self.model_name = self.model_box.get()

    # ---------- 對話工作階段管理 ----------

    def handle_restore(self, data, path_str):
        """還原一個對話工作階段；data 為 None 時建立全新對話。"""
        if not data:
            self.new_session(first=True)
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
            "history": history,
        }
        self.current_session = session
        self.current_path = Path(path_str)
        self.history = session["history"]

        self._clear_chat_display()

        # 還原角色設定到輸入框與記憶體
        self.persona = session["persona"]
        self.persona_entry.delete(0, "end")
        if self.persona:
            self.persona_entry.insert(0, self.persona)

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

        # 還原對話語言
        self.convo_lang = session["lang"]
        self.lang_box.set(CONVO_LANG_NAMES[self.convo_lang])

        # 還原聲音（若該 styleId 仍存在）
        speaker_note = ""
        sid = session["speaker_id"]
        if isinstance(sid, int):
            # 從完整清單反查對應的 (speaker_name, style_name)
            for name, (_uuid, styles) in getattr(self, "all_voices", []):
                for s_name, s_id in styles:
                    if s_id == sid:
                        # 確認目前篩選下此角色是否可見
                        if name in self.speaker_map:
                            self.speaker_box.set(name)
                            self.on_speaker_selected(announce=False)
                            # 設定正確的 style
                            target_label = f"{s_name} [styleId={s_id}]"
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
                conv, voice = reply_parts(content, session["lang"])
                # 註冊成可雙擊重播的訊息（只顯示不朗讀）
                self._register_ai_message(conv, voice)
                shown += 1

        self._append(tr("msg_loaded").format(session["name"], shown), "sys")
        if self.persona:
            self._append(tr("msg_persona_restored").format(self.persona), "sys")
        if speaker_note:
            self._append(speaker_note, "sys")

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
        """刪除選中的對話檔案；若是目前對話則另開新對話。"""
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
        if self.current_path == path:
            self.current_session = None
            self.current_path = None
            self.history = []
            self.new_session()
            self._append(tr("msg_deleted_new").format(name), "sys")
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
        """組出系統提示：格式規範在前，角色設定在後（避免破壞輸出格式）。"""
        prompt = SYSTEM_PROMPTS.get(self.convo_lang, SYSTEM_PROMPTS["zh"])
        if self.persona:
            prompt += PERSONA_LABELS.get(self.convo_lang, "\n角色設定：") + self.persona
        return prompt

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
        if self.speaker_id is None:
            self._append(tr("msg_need_engine"), "sys")
            return
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
        self.replay_seq = 0
        self.replay_hint_shown = False
        for m in self.history:
            if m.get("role") == "user":
                self._append(f"{tr('you_prefix')}{m['content']}\n", "user")
            elif m.get("role") == "assistant":
                conv, voice = reply_parts(m["content"], self.convo_lang)
                self._register_ai_message(conv, voice)
        self._append(tr("msg_retrying"), "sys")
        self._emit("busy", True)
        self.stop_requested = False
        # 該則 user 訊息已經在歷史中，chat_worker 不需要再加入一次
        threading.Thread(
            target=self.chat_worker, args=(last_user,), kwargs={"append_user": False}, daemon=True
        ).start()

    def replay_message(self, tag):
        """重新合成播放指定則 AI 回覆的朗讀內容。"""
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
            self.speak(voice)
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
        older_view = llm_view(self.history[:-KEEP_RECENT_MESSAGES], self.convo_lang)
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
        self.write_session_file()
        self._emit("text", tr("msg_summary_done"), "sys")

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

        # 送模型前先轉成對話語言視圖，朗讀用的日文行不佔 token
        messages = [
            {"role": "system", "content": self.build_system_prompt()}
        ] + llm_view(self.history, self.convo_lang)
        try:
            reply = self.call_llm(messages)
        except Exception as e:
            # 呼叫失敗時保留使用者訊息，不從歷史移除，
            # 讓「重新生成」可以直接重試該則訊息，而不會誤刪前一則已成功的回覆
            self._emit("text", tr("msg_llm_failed").format(e), "sys")
            self._emit("busy", False)
            return

        # 不再補前綴；直接以 JSON 解析。原始回覆原封不動存進歷史。
        self.history.append({"role": "assistant", "content": reply})
        self.write_session_file()

        conv, voice = reply_parts(reply, self.convo_lang)
        self._emit("ai_msg", conv, voice)

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
        view = llm_view(session["history"], lang)
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
        """逐句合成並同步播放；可由停止按鈕中斷。傳入文字為朗讀用日文。"""
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
                self._emit("text", tr("msg_tts_failed").format(e), "sys")
                return
            seq += 1
            wav_path = str(TMP_DIR / f"reply_{threading.get_ident()}_{seq}.wav")
            with open(wav_path, "wb") as f:
                f.write(wav_data)
            if self.stop_requested:
                return
            winsound.PlaySound(wav_path, winsound.SND_FILENAME)


def main():
    root = tk.Tk()
    root.withdraw()

    # 切換語言時的重啟會帶 --lang 參數：此時不再詢問，直接套用該語言
    arg_lang = None
    for a in sys.argv[1:]:
        if a.startswith("--lang="):
            v = a.split("=", 1)[1].strip().lower()
            if v in CONVO_LANG_NAMES:
                arg_lang = v
            break

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
    app = VoiceChatApp(root)
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
