# -*- coding: utf-8 -*-
"""本地 Ollama 對話 + VOICEVOX 朗讀腳本。

流程：
1. 啟動時檢查 VOICEVOX 引擎（預設 http://127.0.0.1:50021）與 Ollama（http://127.0.0.1:11434）
2. 從 /speakers 比對偏好的 3 個聲音（nekotsuka_bi、sayo、mochikosan）供選擇
3. 使用者輸入文字，送給 Ollama 產生回覆，再交由 VOICEVOX 合成並逐句播放

注意：VOICEVOX 只能正確朗讀日文，因此系統提示要求模型以兩行格式回覆：
  日: 供朗讀的日文句子
  中: 繁體中文翻譯（僅顯示在畫面上）

僅使用 Python 標準庫，不需安裝第三方套件。
用法：
  python ollama_voice_chat.py [引擎URL] [模型名稱]
"""

import atexit
import json
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import winsound
from pathlib import Path

# 引擎與 Ollama 位址，可用命令列參數覆蓋
ENGINE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:50021"
OLLAMA_URL = "http://127.0.0.1:11434"

# 指定使用的模型；留空則自動從偏好清單挑選第一個可用的聊天模型
MODEL_NAME = ""
PREFERRED_MODELS = ["qwen3.5:4b","qwen3.6", "gemma4"]

# 可挑選的 3 個聲音：顯示名稱與比對關鍵字（不分大小寫、部分符合即可）
PREFERRED_SPEAKERS = [
    ("nekotsuka_bi", ["nekotsuka_bi", "nekotsuka", "ねこつか", "猫使"]),
    ("sayo", ["sayo", "さよ", "小夜"]),
    ("mochikosan", ["mochikosan", "mochiko", "もち子"]),
]

# 系統提示：要求兩行格式，日文供朗讀、中文供閱讀
SYSTEM_PROMPT = (
    "你是透過 VOICEVOX 語音合成與使用者對話的夥伴。VOICEVOX 只能朗讀日文，"
    "所以你的每一次回覆都必須嚴格使用下列兩行格式，不得加入其他內容：\n"
    "日: <自然口語的日文回覆，將被朗讀>\n"
    "中: <前述日文的繁體中文翻譯>\n"
    "不要使用 Markdown、表情符號或條列式，回覆保持簡短、口語化。"
)


def http_json(path, base_url, params=None):
    """發送 GET 並回傳 JSON 解析結果。"""
    url = base_url + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))


def post_json(path, base_url, params, payload=None, timeout=60):
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


def select_speaker(speakers):
    """列出偏好的 3 個聲音讓使用者選擇，回傳選定的 styleId。

    找不到偏好聲音時改列出全部；沒有任何聲音時回傳 None。
    """
    options = []  # (顯示標籤, styleId)

    def collect(matched_only):
        for speaker in speakers:
            name = speaker.get("name", "")
            if matched_only:
                hit = any(
                    alias.lower() in name.lower()
                    for _, aliases in PREFERRED_SPEAKERS
                    for alias in aliases
                )
                if not hit:
                    continue
            for style in speaker.get("styles", []):
                options.append((f"{name}（{style.get('name', '')}）", style["id"]))

    collect(True)
    if not options:
        print("找不到偏好的 3 個聲音，改列出全部說話者。")
        collect(False)
    if not options:
        return None

    print("\n請選擇聲音：")
    for i, (label, style_id) in enumerate(options, start=1):
        print(f"  {i}. {label} [styleId={style_id}]")

    while True:
        raw = input(f"輸入編號（1-{len(options)}，直接按 Enter 使用 1）：").strip()
        if raw == "":
            chosen = options[0]
        elif raw.isdigit() and 1 <= int(raw) <= len(options):
            chosen = options[int(raw) - 1]
        else:
            print("輸入無效，請重新輸入。")
            continue
        print(f"已選擇：{chosen[0]}")
        return chosen[1]


def pick_model(models):
    """依偏好順序挑選聊天模型，排除 embedding 模型。"""
    if MODEL_NAME:
        return MODEL_NAME
    for key in PREFERRED_MODELS:
        for m in models:
            if key in m:
                return m
    for m in models:
        if "embed" not in m.lower():
            return m
    return None


def parse_reply(text):
    """解析兩行格式回覆，回傳（朗讀文字, 中文翻譯或 None）。

    格式不符時整段視為朗讀文字。
    """
    jp, zh = None, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("日:") or line.startswith("日："):
            jp = line.split(":", 1)[-1].split("：", 1)[-1].strip()
        elif line.startswith("中:") or line.startswith("中："):
            zh = line.split(":", 1)[-1].split("：", 1)[-1].strip()
    if not jp:
        jp = text.strip()
    return jp, zh


def split_sentences(text):
    """把長回覆切成句子，逐句合成播放。"""
    parts = re.split(r"(?<=[。！？!?…])\s*|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


TMP_DIR = Path(tempfile.mkdtemp(prefix="vv_chat_"))
atexit.register(shutil.rmtree, TMP_DIR, ignore_errors=True)
_wav_seq = 0


def speak(text, speaker_id):
    """合成一段文字並同步播放；失敗只印警告不中斷對話。"""
    global _wav_seq
    for sentence in split_sentences(text):
        try:
            # /audio_query 為 POST，參數在網址、內容為空
            query = json.loads(
                post_json(
                    "/audio_query",
                    ENGINE_URL,
                    {"text": sentence, "speaker": speaker_id},
                    None,
                ).decode("utf-8")
            )
            wav_data = post_json(
                "/synthesis",
                ENGINE_URL,
                {"speaker": speaker_id},
                query,
                timeout=120,
            )
        except Exception as e:
            print(f"（語音合成失敗：{e}）")
            return
        _wav_seq += 1
        wav_path = TMP_DIR / f"reply_{_wav_seq}.wav"
        wav_path.write_bytes(wav_data)
        winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)


def main():
    # 檢查 VOICEVOX 引擎
    try:
        version = http_json("/version", ENGINE_URL)
        print(f"VOICEVOX 引擎：{ENGINE_URL}（版本 {version}）")
    except Exception:
        print(f"無法連線 VOICEVOX 引擎（{ENGINE_URL}）。")
        print("請先啟動 VOICEVOX.exe，等它載入完成後重新執行本腳本。")
        return 1

    # 選擇聲音
    speakers = http_json("/speakers", ENGINE_URL)
    speaker_id = select_speaker(speakers)
    if speaker_id is None:
        print("引擎沒有回傳任何可用聲音。")
        return 1

    # 檢查 Ollama 並挑選模型
    try:
        tags = http_json("/api/tags", OLLAMA_URL)
    except Exception:
        print(f"無法連線 Ollama（{OLLAMA_URL}）。請執行 ollama serve 或啟動 Ollama 應用程式。")
        return 1
    model = pick_model([m["name"] for m in tags.get("models", [])])
    if model is None:
        print("Ollama 沒有可用模型，請先用 ollama pull 下載模型。")
        return 1
    print(f"使用模型：{model}")

    # 對話迴圈
    history = []
    print("\n開始對話（輸入 exit 結束、輸入 /voice 重新選聲音）\n")
    while True:
        try:
            user_text = input("你＞ ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n結束對話。")
            break
        if not user_text:
            continue
        if user_text.lower() in ("exit", "quit", "離開"):
            print("結束對話。")
            break
        if user_text == "/voice":
            new_id = select_speaker(http_json("/speakers", ENGINE_URL))
            if new_id is not None:
                speaker_id = new_id
            continue

        history.append({"role": "user", "content": user_text})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
        try:
            resp = post_json(
                "/api/chat",
                OLLAMA_URL,
                None,
                {"model": model, "messages": messages, "stream": False},
                timeout=300,
            )
            reply = json.loads(resp.decode("utf-8"))["message"]["content"]
        except Exception as e:
            print(f"（呼叫 Ollama 失敗：{e}）")
            history.pop()
            continue

        history.append({"role": "assistant", "content": reply})
        jp, zh = parse_reply(reply)
        print(f"AI（日）：{jp}")
        if zh:
            print(f"AI（中）：{zh}")
        speak(jp, speaker_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
