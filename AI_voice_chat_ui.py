# -*- coding: utf-8 -*-
"""VOICEVOX × Ollama／OpenRouter 語音對話（Tkinter 圖形介面版）。

功能：
- 啟動時自動偵測 VOICEVOX 引擎與 Ollama／OpenRouter 狀態
- 下拉選單只列出偏好的 3 個聲音：猫使ビィ、小夜/SAYO、もち子さん
- 可切換對話來源：本機 Ollama 或 OpenRouter 免費模型（金鑰放在同目錄 .env）
- 多重對話管理：每個對話存成 chats/ 下獨立 JSON（含當時聲音、服務與模型），
  可開新對話、載入、改名、刪除；第一則回覆後會由 AI 自動取標題（可再手動改名）
  檔名一律為 chat_日期_時間_毫秒.json（不含文字）；顯示名稱存在檔案內容中，
  允許多個對話同名，清單會自動以（2）（3）區分
- 角色設定：寫入 persona.txt，套用後加入系統提示
- 歷史過長時自動呼叫目前模型整理成摘要（整理中禁止送出新訊息）
- AI 回覆逐句合成播放，可中途停止朗讀
- VOICEVOX 只能正確朗讀日文，因此要求模型以「日:/中:」兩行格式回覆

僅使用 Python 標準庫，不需安裝第三方套件。
用法：python AI_voice_chat_ui.py
"""

import atexit
import json
import queue
import re
import shutil
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

# 從與本檔案同目錄的 .env 讀取設定（金鑰等敏感資訊，不上 GIT）
ENV_PATH = Path(__file__).with_name(".env")
# 角色設定與對話紀錄（一般資料，可上 GIT）
PERSONA_PATH = Path(__file__).with_name("persona.txt")
CHATS_DIR = Path(__file__).with_name("chats")


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


# OpenRouter API 金鑰；請自行在 .env 填入，勿提交到版本控制
OPENROUTER_API_KEY = load_env(ENV_PATH).get("OPENROUTER_API_KEY", "")

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

# 只保留免費模型（:free 結尾）與 ox-alpha
OPENROUTER_KEEP_FREE_ONLY = True

# 歷史長度上限（字元數），超過就自動整理成摘要
HISTORY_CHAR_LIMIT = 5000
# 整理時保留最近的訊息則數（原文），其餘壓縮為摘要
KEEP_RECENT_MESSAGES = 4

# 新對話的預設名稱格式（時間戳），符合此格式的對話才會被 AI 自動取標題覆蓋
DEFAULT_NAME_PATTERN = re.compile(r"^對話_\d{8}_\d{6}(_\d+)?$")

SYSTEM_PROMPT = (
    "你是透過 VOICEVOX 語音合成與使用者對話的夥伴。VOICEVOX 只能朗讀日文，"
    "所以你的每一次回覆都必須嚴格使用下列兩行格式，不得加入其他內容：\n"
    "日: <自然口語的日文回覆，將被朗讀>\n"
    "中: <前述日文的繁體中文翻譯>\n"
    "不要使用 Markdown、表情符號或條列式，回覆保持簡短、口語化。"
)


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


def parse_reply(text):
    """解析兩行格式回覆，回傳（朗讀文字, 中文翻譯或 None）。"""
    jp, zh = None, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("日:", "日：")):
            jp = line.split(":", 1)[-1].split("：", 1)[-1].strip()
        elif line.startswith(("中:", "中：")):
            zh = line.split(":", 1)[-1].split("：", 1)[-1].strip()
    if not jp:
        jp = text.strip()
    return jp, zh


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
    return t[:20] or None


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


def scan_chat_files():
    """掃描 chats/ 目錄，回傳按更新時間排序的（顯示名稱, 路徑）清單。

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
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                n = data.get("name")
                if isinstance(n, str) and n.strip():
                    name = n.strip()
        except Exception:
            name = None
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
        root.title("VOICEVOX × Ollama／OpenRouter 語音對話")
        root.geometry("800x640")

        self.ui_queue = queue.Queue()
        self.history = []  # 目前對話的訊息列表（與 current_session["history"] 同一物件）
        self.speaker_id = None
        self.model_name = ""
        self.provider = "ollama"
        self.ollama_models = []
        self.openrouter_models = []
        self.busy = False
        self.stop_requested = False

        # 對話工作階段狀態
        self.current_session = None  # dict：name/created_at/updated_at/speaker_id/provider/model/history
        self.current_path = None  # 目前對話檔案路徑
        self.session_paths = {}  # 名稱 → 檔案路徑
        self.file_lock = threading.Lock()

        # 角色設定（從 persona.txt 載入）
        self.persona = ""
        if PERSONA_PATH.exists():
            try:
                self.persona = PERSONA_PATH.read_text(encoding="utf-8").strip()
            except Exception:
                self.persona = ""

        self._build_widgets()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._poll_queue)
        threading.Thread(target=self.init_backend, daemon=True).start()

    # ---------- 介面 ----------

    def _build_widgets(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")

        ttk.Label(top, text="引擎狀態：").pack(side="left")
        self.engine_status = ttk.Label(top, text="檢查中…", foreground="#b26a00")
        self.engine_status.pack(side="left", padx=(0, 16))

        ttk.Label(top, text="Ollama：").pack(side="left")
        self.ollama_status = ttk.Label(top, text="檢查中…", foreground="#b26a00")
        self.ollama_status.pack(side="left", padx=(0, 16))

        ttk.Label(top, text="OpenRouter：").pack(side="left")
        key_hint = "金鑰已載入" if OPENROUTER_API_KEY else "未設定金鑰（.env）"
        self.or_status = ttk.Label(
            top, text=("檢查中…" if OPENROUTER_API_KEY else key_hint), foreground="#b26a00"
        )
        self.or_status.pack(side="left")

        mid = ttk.Frame(self.root, padding=(6, 0))
        mid.pack(fill="x")

        ttk.Label(mid, text="服務").pack(side="left")
        self.provider_box = ttk.Combobox(
            mid, state="readonly", width=15, values=["Ollama（本機）", "OpenRouter"]
        )
        self.provider_box.current(0)
        self.provider_box.pack(side="left", padx=(4, 16))
        self.provider_box.bind("<<ComboboxSelected>>", self.on_provider_selected)

        ttk.Label(mid, text="聲音").pack(side="left")
        self.voice_box = ttk.Combobox(mid, state="readonly", width=30)
        self.voice_box.pack(side="left", padx=(4, 16))
        self.voice_box.bind("<<ComboboxSelected>>", self.on_voice_selected)

        ttk.Label(mid, text="模型").pack(side="left")
        self.model_box = ttk.Combobox(mid, width=22)
        self.model_box.pack(side="left", padx=(4, 0))
        self.model_box.bind("<<ComboboxSelected>>", self.on_model_selected)

        ttk.Button(mid, text="停止朗讀", command=lambda: self.stop_speaking()).pack(
            side="right", padx=4
        )

        # 角色設定列
        prow = ttk.Frame(self.root, padding=(6, 4))
        prow.pack(fill="x")

        ttk.Label(prow, text="角色").pack(side="left")
        self.persona_entry = ttk.Entry(prow, font=("Microsoft JhengHei", 11))
        self.persona_entry.pack(side="left", fill="x", expand=True, padx=(4, 6))
        if self.persona:
            self.persona_entry.insert(0, self.persona)
        self.persona_entry.bind("<Return>", lambda e: self.apply_persona())
        ttk.Button(prow, text="套用角色", command=self.apply_persona).pack(side="left")

        # 對話管理列
        srow = ttk.Frame(self.root, padding=(6, 0))
        srow.pack(fill="x")

        ttk.Label(srow, text="對話").pack(side="left")
        self.session_box = ttk.Combobox(srow, state="readonly", width=30)
        self.session_box.pack(side="left", padx=(4, 10))
        self.new_btn = ttk.Button(srow, text="開新對話", command=self.new_session)
        self.new_btn.pack(side="left", padx=(0, 4))
        self.load_btn = ttk.Button(srow, text="載入", command=self.load_selected_session)
        self.load_btn.pack(side="left", padx=(0, 4))
        self.rename_btn = ttk.Button(srow, text="改名", command=self.rename_selected_session)
        self.rename_btn.pack(side="left", padx=(0, 4))
        self.delete_btn = ttk.Button(srow, text="刪除", command=self.delete_selected_session)
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
        self.send_button = ttk.Button(
            bottom, text="送出", command=self.send_message, style="Big.TButton"
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

    # ---------- 佇列輪詢：工作執行緒透過佇列更新畫面 ----------

    def _poll_queue(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "text":
                    self._append(msg[1], msg[2] if len(msg) > 2 else None)
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
                elif kind == "busy":
                    self.busy = msg[1]
                    state = "disabled" if self.busy else "normal"
                    self.send_button.configure(state=state)
                    for btn in (self.new_btn, self.load_btn, self.rename_btn, self.delete_btn):
                        btn.configure(state=state)
        except queue.Empty:
            pass
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
            self._emit("engine_ok", f"已連線（{version}）")
            speakers = http_json("/speakers", ENGINE_URL)
        except Exception:
            self._emit("engine_ng", "未連線")
            self._emit(
                "text",
                "無法連線 VOICEVOX 引擎。請先啟動 VOICEVOX.exe 後重新開啟本程式。\n\n",
                "sys",
            )
            speakers = []

        voices = self._collect_voices(speakers)
        if not voices:
            self._emit(
                "text",
                "找不到偏好的 3 個聲音（猫使ビィ、小夜/SAYO、もち子さん）。\n"
                "請確認 VOICEVOX 已安裝這些角色語音。\n\n",
                "sys",
            )
        self._emit("voices", voices)

        # 檢查 Ollama 並載入模型清單
        try:
            tags = http_json("/api/tags", OLLAMA_URL, timeout=10)
            models = [
                m["name"] for m in tags.get("models", []) if "embed" not in m["name"].lower()
            ]
            self._emit("ollama_ok", "已連線")
            self._emit("ollama_models", models)
        except Exception:
            self._emit("ollama_ng", "未連線")
            self._emit("ollama_models", [])

        # 載入 OpenRouter 模型清單（公開端點，不需金鑰），只保留免費模型與 ox-alpha
        if not OPENROUTER_API_KEY:
            self._emit("or_ng", "未設定金鑰")
        try:
            data = http_json("/models", OPENROUTER_URL, timeout=20)

            def keep(model_id):
                if not OPENROUTER_KEEP_FREE_ONLY:
                    return True
                lowered = model_id.lower()
                return lowered.endswith(":free") or "ox-alpha" in lowered

            ids = sorted(m["id"] for m in data.get("data", []) if keep(m["id"]))
            self._emit("or_ok", "可用" + ("（金鑰已載入）" if OPENROUTER_API_KEY else "（未設金鑰）"))
            self._emit("or_models", ids)
        except Exception:
            self._emit("or_ng", "清單取得失敗")
            self._emit("or_models", [])

        # 掃描既有對話，準備還原最近使用的
        pairs = scan_chat_files()
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
        """組出聲音選項，只保留符合偏好關鍵字的 3 個聲音。"""
        voices = []
        for speaker in speakers:
            name = speaker.get("name", "")
            if not any(
                alias.lower() in name.lower()
                for _, aliases in PREFERRED_SPEAKERS
                for alias in aliases
            ):
                continue
            for style in speaker.get("styles", []):
                label = f"{name}（{style.get('name', '')}）[styleId={style['id']}]"
                voices.append((label, style["id"]))
        return voices

    def _load_voices(self, voices):
        self.voice_map = dict(voices)
        self.voice_box.configure(values=[v[0] for v in voices])
        if voices:
            self.voice_box.current(0)
            self.on_voice_selected(announce=False)

    def _apply_models(self):
        """依目前服務來源填入模型下拉選單並選擇預設模型。"""
        models = self.ollama_models if self.provider == "ollama" else self.openrouter_models
        # OpenRouter 允許自行輸入模型 ID；Ollama 限定清單內項目
        self.model_box.configure(
            values=models, state="normal" if self.provider == "openrouter" else "readonly"
        )
        if not models:
            self.model_box.set("")
            self.on_model_selected()
            return
        keys = PREFERRED_MODELS if self.provider == "ollama" else PREFERRED_OPENROUTER_MODELS
        default_index = next(
            (i for i, m in enumerate(models) if any(key in m for key in keys)),
            0,
        )
        self.model_box.current(default_index)
        self.on_model_selected()

    def on_provider_selected(self, event=None, announce=True):
        provider = self.provider_box.get()
        self.provider = "openrouter" if "OpenRouter" in provider else "ollama"
        if announce:
            self._append(f"[已切換服務：{provider}]\n", "sys")
        self._apply_models()

    def on_voice_selected(self, event=None, announce=True):
        label = self.voice_box.get()
        if label in getattr(self, "voice_map", {}):
            self.speaker_id = self.voice_map[label]
            if announce:
                self._append(f"[已選擇聲音 {label}]\n", "sys")

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
            "history": history,
        }
        self.current_session = session
        self.current_path = Path(path_str)
        self.history = session["history"]

        self._clear_chat_display()

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

        # 還原聲音（若該 styleId 仍存在）
        speaker_note = ""
        sid = session["speaker_id"]
        if isinstance(sid, int):
            match = next((lbl for lbl, i in self.voice_map.items() if i == sid), None)
            if match:
                self.voice_box.set(match)
                self.speaker_id = sid
            else:
                speaker_note = f"[注意：原聲音 styleId={sid} 不存在，維持目前選擇]\n"

        # 重播歷史（不朗讀）；摘要用的 system 訊息不顯示
        shown = 0
        for m in history:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                self._append(f"你＞ {content}\n", "user")
                shown += 1
            elif role == "assistant":
                jp, zh = parse_reply(content)
                self._append(f"AI（日）：{jp}\n", "jp")
                self._append(f"AI（中）：{zh}\n\n" if zh else "\n", "zh" if zh else None)
                shown += 1

        self._append(
            f"[已載入對話「{session['name']}」，共 {shown} 則，可繼續聊]\n", "sys"
        )
        if speaker_note:
            self._append(speaker_note, "sys")

    def write_session_file(self):
        """將目前對話（含當下聲音、服務、模型）寫入磁檔。"""
        if self.current_session is None or self.current_path is None:
            return
        self.current_session["updated_at"] = now_iso()
        self.current_session["speaker_id"] = self.speaker_id
        self.current_session["provider"] = self.provider
        self.current_session["model"] = self.model_name
        payload = json.dumps(self.current_session, ensure_ascii=False, indent=2)
        try:
            with self.file_lock:
                self.current_path.parent.mkdir(exist_ok=True)
                tmp = self.current_path.with_suffix(".tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(self.current_path)
        except Exception as e:
            self._emit("text", f"（對話存檔失敗：{e}）\n", "sys")

    def refresh_session_list(self, select=None):
        """重掃 chats/ 更新下拉選單（主執行緒呼叫）。"""
        pairs = scan_chat_files()
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
            "history": [],
        }
        self.history = self.current_session["history"]
        self.current_path = path
        self.write_session_file()
        self.refresh_session_list(select=display_name)
        self._clear_chat_display()
        if not first:
            self._append(f"[已開新對話：{display_name}]\n", "sys")

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
            self._append(f"[載入失敗：{e}]\n", "sys")
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
            "重新命名對話", "新的對話名稱：", initialvalue=base_name, parent=self.root
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
                self._append(f"[改名失敗：{e}]\n", "sys")
                return
        self.refresh_session_list(select=new_name)
        self._append(f"[已改名：「{base_name}」→「{new_name}」]\n", "sys")

    def delete_selected_session(self):
        """刪除選中的對話檔案；若是目前對話則另開新對話。"""
        if self.busy:
            return
        name = self.session_box.get()
        path = self.session_paths.get(name)
        if path is None:
            return
        if not messagebox.askyesno(
            "刪除對話", f"確定要刪除「{name}」嗎？\n此動作無法復原。", parent=self.root
        ):
            return
        try:
            path.unlink()
        except Exception as e:
            self._append(f"[刪除失敗：{e}]\n", "sys")
            return
        if self.current_path == path:
            self.current_session = None
            self.current_path = None
            self.history = []
            self.new_session()
            self._append(f"[已刪除「{name}」，並自動開了新對話]\n", "sys")
        else:
            self.refresh_session_list()
            self._append(f"[已刪除「{name}」]\n", "sys")

    def _rename_active_to(self, new_title, announce=True):
        """重新命名目前作用中的對話（只改顯示名稱，檔名不變）。"""
        if self.current_session is None:
            return
        self.current_session["name"] = new_title
        self.write_session_file()
        self.refresh_session_list(select=new_title)
        if announce:
            self._append(f"[已命名為「{new_title}」]\n", "sys")

    # ---------- 角色設定 ----------

    def apply_persona(self):
        """套用角色設定：寫入 persona.txt 並立即生效於下一則訊息。"""
        text = self.persona_entry.get().strip()
        self.persona = text
        try:
            PERSONA_PATH.write_text(text, encoding="utf-8")
            saved = "（已存入 persona.txt）"
        except Exception:
            saved = "（persona.txt 寫入失敗，僅本次生效）"
        if text:
            self._append(f"[角色設定已套用：{text}]{saved}\n", "sys")
        else:
            self._append(f"[已清除角色設定，回到預設]{saved}\n", "sys")

    def build_system_prompt(self):
        """組出系統提示：格式規範在前，角色設定在後（避免破壞輸出格式）。"""
        prompt = SYSTEM_PROMPT
        if self.persona:
            prompt += f"\n角色設定：{self.persona}"
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
            self._append("[請先確認 VOICEVOX 引擎已啟動]\n", "sys")
            return
        if not self.model_name:
            self._append("[請先確認 Ollama／OpenRouter 已啟動且有可用模型]\n", "sys")
            return

        self.input_box.delete("1.0", "end")
        self._append(f"你＞ {user_text}\n", "user")
        self._emit("busy", True)
        self.stop_requested = False
        threading.Thread(target=self.chat_worker, args=(user_text,), daemon=True).start()

    def stop_speaking(self, quiet=False):
        self.stop_requested = True
        winsound.PlaySound(None, winsound.SND_PURGE)
        if not quiet:
            self._append("[已停止朗讀]\n", "sys")

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

        self._emit("text", "[對話過長，整理歷史中，請稍候…（此時無法送出訊息）]\n", "sys")
        older = self.history[:-KEEP_RECENT_MESSAGES]
        transcript = "\n".join(
            f"{'使用者' if m.get('role') == 'user' else 'AI'}：{m.get('content', '')}"
            for m in older
            if m.get("role") in ("user", "assistant")
        )
        ask = (
            "請將以下人機對話整理成重點摘要，保留重要事實、決定與約定，"
            "以繁體中文簡短輸出，不要逐字羅列。\n\n" + transcript
        )
        try:
            summary = self.call_llm(
                [{"role": "user", "content": ask}], timeout=120
            ).strip()
        except Exception as e:
            self._emit("text", f"[摘要失敗，改用完整歷史繼續（{e}）]\n", "sys")
            return
        if not summary:
            self._emit("text", "[摘要為空，改用完整歷史繼續]\n", "sys")
            return

        self.history[:] = (
            [{"role": "system", "content": f"以下是更早對話的重點摘要：\n{summary}"}]
            + self.history[-KEEP_RECENT_MESSAGES:]
        )
        self.write_session_file()
        self._emit("text", "[整理完成，舊歷史已壓縮為摘要]\n", "sys")

    def chat_worker(self, user_text):
        """處理一次完整的對話回合：摘要檢查 → 呼叫模型 → 存檔 → 朗讀。"""
        if self.provider == "openrouter" and not OPENROUTER_API_KEY:
            self._emit(
                "text",
                "（尚未設定 OpenRouter 金鑰，請在本程式同目錄的 .env 填入 OPENROUTER_API_KEY 後重新啟動）\n",
                "sys",
            )
            self._emit("busy", False)
            return

        self.history.append({"role": "user", "content": user_text})

        # 歷史過長先整理（期間忙碌鎖維持，無法送出新訊息）
        self.maybe_summarize()

        messages = [{"role": "system", "content": self.build_system_prompt()}] + self.history
        try:
            reply = self.call_llm(messages)
        except Exception as e:
            self.history.pop()
            self._emit("text", f"（呼叫對話服務失敗：{e}）\n", "sys")
            self._emit("busy", False)
            return

        self.history.append({"role": "assistant", "content": reply})
        self.write_session_file()

        jp, zh = parse_reply(reply)
        self._emit("text", f"AI（日）：{jp}\n", "jp")
        if zh:
            self._emit("text", f"AI（中）：{zh}\n\n", "zh")
        else:
            self._emit("text", "\n")

        self.speak(jp)

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
        user_text = next(
            (m.get("content", "") for m in session["history"] if m.get("role") == "user"),
            "",
        )
        ai_text = next(
            (m.get("content", "") for m in session["history"] if m.get("role") == "assistant"),
            "",
        )
        ask = (
            "請根據以下對話開頭取一個 4 到 10 個字的繁體中文標題，"
            "直接輸出標題本身，不要引號、句號或任何說明。\n\n"
            f"使用者：{user_text}\nAI：{ai_text}"
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
        """逐句合成並同步播放；可由停止按鈕中斷。"""
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
                self._emit("text", f"（語音合成失敗：{e}）\n", "sys")
                return
            seq += 1
            wav_path = str(TMP_DIR / f"reply_{threading.get_ident()}_{seq}.wav")
            with open(wav_path, "wb") as f:
                f.write(wav_data)
            if self.stop_requested:
                return
            winsound.PlaySound(wav_path, winsound.SND_FILENAME)


if __name__ == "__main__":
    root = tk.Tk()
    VoiceChatApp(root)
    root.mainloop()
