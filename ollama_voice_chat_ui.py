# -*- coding: utf-8 -*-
"""VOICEVOX × Ollama 語音對話（Tkinter 圖形介面版）。

功能：
- 啟動時自動偵測 VOICEVOX 引擎與 Ollama 狀態
- 下拉選單只列出偏好的 3 個聲音：猫使ビィ、小夜/SAYO、もち子さん
- 對話內容顯示於畫面，AI 回覆逐句合成播放，可中途停止朗讀
- VOICEVOX 只能正確朗讀日文，因此要求模型以「日:/中:」兩行格式回覆

僅使用 Python 標準庫，不需安裝第三方套件。
用法：python ollama_voice_chat_ui.py [引擎URL]
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
from pathlib import Path
from tkinter import scrolledtext, ttk
import winsound

ENGINE_URL = "http://127.0.0.1:50021"
OLLAMA_URL = "http://127.0.0.1:11434"

# 聲音偏好順序：比對關鍵字（不分大小寫、部分符合）
PREFERRED_SPEAKERS = [
    ("nekotsuka_bi", ["nekotsuka_bi", "nekotsuka", "ねこつか", "猫使"]),
    ("sayo", ["sayo", "さよ", "小夜"]),
    ("mochikosan", ["mochikosan", "mochiko", "もち子"]),
]

# 模型偏好順序
PREFERRED_MODELS = ["qwen3.6", "qwen3.5", "gemma4"]

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


def post_json(path, base_url, params=None, payload=None, timeout=60):
    """發送 POST（JSON 內容，payload 為 None 時送空內容）並回傳原始回應。"""
    url = base_url + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode("utf-8") if payload is not None else b""
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
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


TMP_DIR = Path(tempfile.mkdtemp(prefix="vv_chat_ui_"))
atexit.register(shutil.rmtree, TMP_DIR, ignore_errors=True)


class VoiceChatApp:
    def __init__(self, root):
        self.root = root
        root.title("VOICEVOX × Ollama 語音對話")
        root.geometry("800x560")

        self.ui_queue = queue.Queue()
        self.history = []
        self.speaker_id = None
        self.model_name = ""
        self.busy = False
        self.stop_requested = False

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
        self.ollama_status.pack(side="left")

        mid = ttk.Frame(self.root, padding=(6, 0))
        mid.pack(fill="x")

        ttk.Label(mid, text="聲音").pack(side="left")
        self.voice_box = ttk.Combobox(mid, state="readonly", width=38)
        self.voice_box.pack(side="left", padx=(4, 16))
        self.voice_box.bind("<<ComboboxSelected>>", self.on_voice_selected)

        ttk.Label(mid, text="模型").pack(side="left")
        self.model_box = ttk.Combobox(mid, state="readonly", width=28)
        self.model_box.pack(side="left", padx=(4, 0))
        self.model_box.bind("<<ComboboxSelected>>", self.on_model_selected)

        ttk.Button(mid, text="停止朗讀", command=self.stop_speaking).pack(
            side="right", padx=4
        )

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

        self.send_button = ttk.Button(
            bottom, text="送出", command=self.send_message
        )
        self.send_button.pack(side="right", padx=(6, 0), anchor="se")

    def _on_return(self, event):
        """Enter 送出訊息；按住 Shift 時允許換行。"""
        if event.state & 0x0001:
            return None
        self.send_message()
        return "break"

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
                elif kind == "voices":
                    self._load_voices(msg[1])
                elif kind == "models":
                    self._load_models(msg[1])
                elif kind == "busy":
                    self.busy = msg[1]
                    self.send_button.configure(state="disabled" if self.busy else "normal")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _emit(self, *msg):
        """從任意執行緒排程一個畫面更新。"""
        self.ui_queue.put(msg)

    # ---------- 初始化 ----------

    def init_backend(self):
        # 檢查引擎並載入聲音清單
        try:
            version = http_json("/version", ENGINE_URL)
            self._emit("engine_ok", f"已連線（{version}）")
            speakers = http_json("/speakers", ENGINE_URL)
        except Exception:
            self._emit("engine_ng", "未連線")
            self._emit(
                "text",
                "無法連線 VOICEVOX 引擎。請先啟動 VOICEVOX.exe 再按右上角重試或重新開啟本程式。\n\n",
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
            self._emit("models", models)
        except Exception:
            self._emit("ollama_ng", "未連線")
            self._emit(
                "text",
                "無法連線 Ollama。請執行 ollama serve 或啟動 Ollama 應用程式。\n\n",
                "sys",
            )

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
            self.on_voice_selected()

    def _load_models(self, models):
        self.model_list = models
        self.model_box.configure(values=models)
        default = next(
            (
                i
                for i, m in enumerate(models)
                if any(key in m for key in PREFERRED_MODELS)
            ),
            0 if models else -1,
        )
        if models:
            self.model_box.current(default)
            self.on_model_selected()

    def on_voice_selected(self, event=None):
        label = self.voice_box.get()
        if label in getattr(self, "voice_map", {}):
            self.speaker_id = self.voice_map[label]
            self._append(f"[已選擇聲音 {label}]\n", "sys")

    def on_model_selected(self, event=None):
        self.model_name = self.model_box.get()

    # ---------- 事件 ----------

    def send_message(self):
        user_text = self.input_box.get("1.0", "end-1c").strip()
        if not user_text or self.busy:
            return
        if self.speaker_id is None:
            self._append("[請先確認 VOICEVOX 引擎已啟動]\n", "sys")
            return
        if not self.model_name:
            self._append("[請先確認 Ollama 已啟動且有可用模型]\n", "sys")
            return

        self.input_box.delete("1.0", "end")
        self._append(f"你＞ {user_text}\n", "user")
        self._emit("busy", True)
        self.stop_requested = False
        threading.Thread(target=self.chat_worker, args=(user_text,), daemon=True).start()

    def stop_speaking(self):
        self.stop_requested = True
        winsound.PlaySound(None, winsound.SND_PURGE)
        self._append("[已停止朗讀]\n", "sys")

    def on_close(self):
        self.stop_requested = True
        winsound.PlaySound(None, winsound.SND_PURGE)
        self.root.destroy()

    # ---------- 背景：對話與合成 ----------

    def chat_worker(self, user_text):
        self.history.append({"role": "user", "content": user_text})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history
        try:
            resp = post_json(
                "/api/chat",
                OLLAMA_URL,
                payload={"model": self.model_name, "messages": messages, "stream": False},
                timeout=300,
            )
            reply = json.loads(resp.decode("utf-8"))["message"]["content"]
        except Exception as e:
            self.history.pop()
            self._emit("text", f"（呼叫 Ollama 失敗：{e}）\n", "sys")
            self._emit("busy", False)
            return

        self.history.append({"role": "assistant", "content": reply})
        jp, zh = parse_reply(reply)
        self._emit("text", f"AI（日）：{jp}\n", "jp")
        if zh:
            self._emit("text", f"AI（中）：{zh}\n\n", "zh")
        else:
            self._emit("text", "\n")

        self.speak(jp)
        self._emit("busy", False)

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
