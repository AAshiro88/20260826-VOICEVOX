# -*- coding: utf-8 -*-
"""VOICEVOX 引擎 API 測試腳本。

測試前需先啟動 VOICEVOX（產品版或 ENGINE），引擎預設監聽 http://127.0.0.1:50021。
本腳本僅使用 Python 標準庫，不需安裝第三方套件。

測項：
1. GET  /version   確認引擎可連線
2. GET  /speakers  取得音聲清單
3. POST /audio_query 產生語音合成查詢
4. POST /synthesis   合成 WAV 音檔並存檔

執行時會從 /speakers 中比對偏好的 3 個聲音（nekotsuka_bi、sayo、mochikosan）
列出選單供使用者選擇，再以選定的聲音進行合成測試。
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 引擎位址，可用命令列第一個參數覆蓋
BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:50021"

# 輸出音檔位置與測試用的朗讀文字
OUTPUT_WAV = Path(__file__).with_name("test_output.wav")
TEST_TEXT = "こんにちは、VOICEVOXのテストです。"

# 可挑選的 3 個聲音：顯示名稱與比對關鍵字（不分大小寫、對說話者名稱部分符合即可）
PREFERRED_SPEAKERS = [
    ("nekotsuka_bi", ["nekotsuka_bi", "nekotsuka", "ねこつか", "猫使"]),
    ("sayo", ["sayo", "さよ", "小夜"]),
    ("mochikosan", ["mochikosan", "mochiko", "もち子"]),
]


def request_json(path, params=None):
    """發送 GET 請求並回傳 JSON 解析結果。"""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as res:
        return json.loads(res.read().decode("utf-8"))


def post_json(path, params, payload):
    """發送 POST 請求（JSON 內容）並回傳回應內容。"""
    url = BASE_URL + path + "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


results = []


def record(name, ok, detail=""):
    """記錄單一測項結果。"""
    results.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


def select_speaker(speakers):
    """列出偏好的 3 個聲音讓使用者選擇，回傳選定的 styleId。

    若引擎中找不到任一偏好聲音，改列出全部說話者；
    引擎沒有任何聲音時回傳 None。
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


def main():
    print(f"目標引擎：{BASE_URL}\n")

    # 測項 1：版本查詢
    try:
        version = request_json("/version")
        record("GET /version", True, f"引擎版本 {version}")
    except Exception as e:
        record("GET /version", False, str(e))
        print(
            "\n無法連線引擎。連線被拒絕通常代表 VOICEVOX 未啟動，"
            "或引擎埠不是預設的 50021。\n"
            "請先啟動 VOICEVOX（或 ENGINE）後重新執行本腳本。其餘測項略過。"
        )
        summarize()
        return

    # 測項 2：取得音聲清單
    speakers_ok = False
    try:
        speakers = request_json("/speakers")
        names = [s["name"] for s in speakers]
        record("GET /speakers", True, f"共 {len(speakers)} 位說話者：{', '.join(names[:5])}...")
        speakers_ok = True
    except Exception as e:
        record("GET /speakers", False, str(e))

    # 讓使用者從偏好的 3 個聲音中選擇
    speaker_id = None
    if speakers_ok:
        speaker_id = select_speaker(speakers)
        if speaker_id is None:
            print("引擎沒有回傳任何可用聲音，其餘測項略過。")
            summarize()
            return
    else:
        # 無法取得清單時，允許手動輸入 styleId 繼續測試
        raw = input("\n無法取得音聲清單，請手動輸入 styleId（直接按 Enter 結束）：").strip()
        if raw == "" or not raw.isdigit():
            summarize()
            return
        speaker_id = int(raw)

    # 測項 3：產生 audio_query
    query = None
    try:
        query = request_json(
            "/audio_query",
            {"text": TEST_TEXT, "speaker": speaker_id},
        )
        accent_phrases = len(query.get("accent_phrases", []))
        record("POST /audio_query", True, f"取得 {accent_phrases} 個句子韻律區塊")
    except Exception as e:
        record("POST /audio_query", False, str(e))

    # 測項 4：合成 WAV
    if query is not None:
        try:
            wav_data = post_json("/synthesis", {"speaker": speaker_id}, query)
            OUTPUT_WAV.write_bytes(wav_data)
            is_wav = wav_data[:4] == b"RIFF" and wav_data[8:12] == b"WAVE"
            record(
                "POST /synthesis",
                is_wav,
                f"輸出 {OUTPUT_WAV.name}（{len(wav_data)} bytes，WAV 格式 {'正確' if is_wav else '異常'}）",
            )
        except Exception as e:
            record("POST /synthesis", False, str(e))
    else:
        record("POST /synthesis", False, "缺少 audio_query，略過")

    summarize()


def summarize():
    """印出測試摘要。"""
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n結果：{passed}/{total} 通過")
    if passed < total:
        print("存在失敗的測項，請檢查上方 FAIL 訊息。")


if __name__ == "__main__":
    main()
