# VOICEVOX × Ollama／OpenRouter 語音對話工具

以本地語音合成（VOICEVOX）搭配大型語言模型（本機 Ollama 或 OpenRouter 雲端 API）的圖形介面對話程式。AI 回覆會自動朗讀，支援多重對話管理、角色設定，對話語言可選日本語／中文／English，介面文字亦提供三語切換。

全部程式僅使用 **Python 標準庫**，不需安裝任何第三方套件（因此沒有 requirements.txt）。

## 功能特色

- **語音朗讀**：AI 回覆逐句送 VOICEVOX 合成並播放，可隨時停止；**雙擊任何一則有底線的朗讀句子可重新播放**；只列出指定的 3 個聲音（猫使ビィ、小夜/SAYO、もち子さん）；中文回覆無日文行時自動跳過朗讀（避免 VOICEVOX 硬唸中文）
- **語言偵測與自動修復**：AI 回覆缺少 `日:`/`中:`/`英:` 前綴時，自動偵測語言並補上標籤，確保朗讀與模型記憶正確；載入舊對話檔時同步修復壞標籤並寫回磁碟（一次性）
- **語言一鍵切換**：啟動時先彈出語言選擇視窗（日本語／中文／English，預設帶入**最新聊天紀錄的語言**）；每個對話綁定自己的語言，**對話清單只顯示相同語言的對話**；主畫面「語言」下拉切換後自動重啟套用
  - 日本語：回覆為純日文單行，送模型、顯示與朗讀都用同一份原文，不需翻譯
  - 中文／English：回覆含「日:」行供 VOICEVOX 合成；**送模型時只送該語言行，日文朗讀行不佔 token**
  - 朗讀一律使用日文行（VOICEVOX 只有日文發音正確）
- **雙來源 LLM**：一鍵切換本機 Ollama 或 OpenRouter（模型清單只保留免費模型與 ox-alpha）
- **多重對話管理**：每個對話存成獨立 JSON，可開新對話、載入、改名、刪除；記錄當下使用的聲音／服務／模型／語言，載入時自動還原
- **自動命名**：新對話先以時間戳命名，第一則回覆後由 AI 自動取標題（標題語言跟隨對話語言），之後可手動改名；允許多個對話同名（清單自動加編號區分）
- **角色設定**：自訂 AI 人設（例如「傲嬌的妹妹」），跟著對話一起存在 chats/*.json，載入對話即還原人設
- **歷史自動摘要**：對話過長時自動呼叫目前模型整理成重點摘要＋保留最近數則原文（摘要輸入同樣只取對話語言）；整理中暫停接受新訊息
- **雙語顯示**：中文／English 模式下，AI 以「對話語言（主要行）＋日文朗讀行（灰色底線）」顯示，看得到也聽得到
- **重新生成**：AI 回覆不满意或格式壞掉時，按「重新生成」按鈕移除最後一則回覆並重新呼叫模型生成

## 檔案結構

```
20260826-VOICEVOX/
├─ AI_voice_chat_ui.py      主程式（Tkinter 圖形介面）
├─ ollama_voice_chat.py     舊版純命令列介面（僅支援 Ollama，保留備用）
├─ voicevox_api_test.py     VOICEVOX 引擎 API 連通測試腳本
├─ .env                     OPENROUTER_API_KEY（金鑰，不上 GIT）
└─ chats/                   對話紀錄（chat_日期_時間_毫秒.json，含角色設定）
```

## 環境需求

| 項目 | 說明 |
|------|------|
| 作業系統 | Windows 10 以上（使用 winsound 播放音訊） |
| Python | 3.8 以上（開發環境為 Anaconda） |
| VOICEVOX | [產品版 0.25.2](https://voicevox.hiroshiba.jp/)（`voicevox-windows-directml-0.25.2.zip`），啟動後引擎自動監聽 `127.0.0.1:50021` |
| 對話來源 | Ollama（本機）或 OpenRouter API 金鑰（擇一即可） |

## 安裝

不需安裝任何套件。確認已裝好 Python 與 VOICEVOX 即可：

```bat
pip install -r requirements.txt
```

> 本專案無第三方相依套件，專案內未提供 requirements.txt，此步驟可略過。

## 事前準備

### 1. 啟動 VOICEVOX 引擎

啟動 VOICEVOX.exe（本目錄下的 `VOICEVOX/` 或自行安裝的版本），等它載入完成。可用瀏覽器開 `http://127.0.0.1:50021/docs` 確認引擎已就緒。

若清單中找不到那 3 個聲音，請確認 VOICEVOX 已更新到包含猫使ビィ、小夜/SAYO、もち子さん 的版本。

### 2A. 使用本機 Ollama（離線）

```powershell
ollama serve          # 若尚未啟動伺服器
ollama pull qwen3.5   # 至少下載一個聊天模型
```

### 2B. 使用 OpenRouter（線上）

1. 到 https://openrouter.ai/settings/keys 建立 API 金鑰
2. 編輯 `.env`：

   ```ini
   OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxx
   ```

   語言不存放於 `.env`：每次啟動時在語言選擇視窗選擇（預設帶入最新聊天紀錄的語言）。

3. 重新啟動程式

## 使用方法

```powershell
C:\ProgramData\Anaconda3\python.exe AI_voice_chat_ui.py
```

啟動時若已有對話紀錄，會自動以上次對話的語言直接進入主畫面；第一次啟動（無對話紀錄）才會彈出語言選擇視窗。帶 `--lang=xx` 參數重啟時（語言切換），同樣跳過選擇視窗直接套用。

介面操作：

| 區塊 | 說明 |
|------|------|
| 狀態列 | 即時顯示引擎／Ollama／OpenRouter 連線狀態（綠＝正常、紅＝未連線） |
| 服務／聲音／模型／語言 | 切換對話來源、朗讀聲音、使用的模型與語言；「語言」切換後自動重啟，介面與對話皆套用新語言 |
| 停止朗讀 | 中斷目前與後續句子的播放 |
| 重新生成 | 移除最後一則 AI 回覆並重新呼叫模型生成（忙碌中或無回覆時 disabled） |
| 重播 | 雙擊對話區任何一則有底線的朗讀句子即可重新合成播放 |
| 角色 | 輸入人設後按「套用角色」，下一則訊息起生效 |
| 對話列 | 開新對話（綁定目前語言）／載入／改名／刪除；**清單只顯示目前語言的對話** |
| 輸入區 | Enter 送出、Shift+Enter 換行 |

其他腳本：

```powershell
# 引擎連通測試（含選聲音與合成一段 WAV）
python voicevox_api_test.py

# 舊版純文字介面（僅 Ollama）
python ollama_voice_chat.py
```

### 懶人啟動與打包成 exe

| 檔案 | 用途 |
|------|------|
| `start_ai_voice_chat.bat` | 雙擊直接啟動主程式（自動找 Anaconda Python） |
| `build_exe.bat` | 以 PyInstaller 打包成單一執行檔 `dist\AI_VoiceChat_UI.exe` |

打包注意：**`.env` 與 `chats/` 不會被封裝進 exe**。程式在打包模式（frozen）下會改以 exe 所在資料夾作為基底目錄，因此使用 exe 前請手動把 `.env` 複製到 `dist\` 旁；`chats\` 會在首次存檔時自動建立在 exe 旁。

## 對話檔案格式

`chats/chat_YYYYMMDD_HHMMSS_mmm.json`（檔名不含文字，同名對話互不衝突）：

```json
{
  "name": "傲嬌妹妹初體驗",
  "created_at": "2026-08-26 15:30:59",
  "updated_at": "2026-08-26 16:02:11",
  "speaker_id": 46,
  "provider": "openrouter",
  "model": "stealth/ox-alpha",
  "lang": "zh",
  "persona": "傲嬌的妹妹",
  "history": [
    { "role": "user", "content": "你好" },
    { "role": "assistant", "content": "日: ...\n中: ..." },
    { "role": "system", "content": "以下是更早對話的重點摘要：..." }
  ]
}
```

`history` 中 role 為 system 的項目是自動摘要產物，載入重播時不顯示。`persona` 是該對話專屬的角色設定，載入時自動還原到介面輸入框。`lang` 是該對話綁定的語言（`ja`／`zh`／`en`），同時決定它在對話清單中的歸屬——**清單只顯示目前語言的對話**；舊檔案沒有此欄位時視為 `zh`，不需遷移。

載入對話時，程式會自動檢查所有 assistant 回覆是否有語言前綴（`日:`/`中:`/`英:`），缺少時依 `lang` 補上並寫回磁碟。此修復為一次性，不影響對話內容。

各語言的回覆格式：

| 語言 | 回覆內容 | 送模型的 assistant 內容 | 朗讀 |
|------|---------|------------------------|------|
| `ja` | 純日文單行 | 原文整段 | 原文（VOICEVOX 合成） |
| `zh` | 「日:」＋「中:」兩行 | 只送「中:」的內容 | 取「日:」行；無日文行時跳過朗讀 |
| `en` | 「英:」＋「日:」兩行 | 只送「英:」的內容 | 取「日:」行；無日文行時跳過朗讀 |

AI 回覆缺少前綴時，程式自動偵測語言並補上標籤。朗讀一律取「日:」行（`ja` 模式為原文本身）；若回覆只有中文行（無日文翻譯），則不送 VOICEVOX（避免硬唸中文產生怪音）。

## 常見問題

| 現象 | 原因與處理 |
|------|-----------|
| `[WinError 10061] 無法連線` | VOICEVOX 未啟動或埠不是 50021，先啟動 VOICEVOX |
| `HTTP Error 405` | `/audio_query` 必須用 POST（現行程式碼已正確處理） |
| OpenRouter `HTTP Error 429` | 免費模型有頻率限制，稍候再試或換一個免費模型 |
| 找不到偏好的 3 個聲音 | VOICEVOX 版本未含這些角色，請更新或改用其他聲音測試 |
| 中文／英文朗讀發音怪怪的 | AI 回覆缺少語言前綴時，程式已自動偵測語言並補上標籤；若仍有問題可按「重新生成」重來一次 |
| 對話出現「整理歷史中」 | 歷史超過 5000 字元觸發自動摘要，完成前無法送出新訊息 |
| 切換語言沒有生效 | 「語言」下拉切換會自動重啟並直接套用（跳過啟動選單）；啟動時也可在語言視窗重新選擇 |
| 對話清單找不到某個對話 | 該對話綁定的是其他語言；把「語言」下拉切到該語言即會出現（舊檔案視為中文） |

摘要門檻與保留則數可在 `AI_voice_chat_ui.py` 頂部的 `HISTORY_CHAR_LIMIT`、`KEEP_RECENT_MESSAGES` 調整。

## GIT 注意事項

`.gitignore` 已排除：

- `.env`——含 API 金鑰，**絕對不要提交或分享**
- `VOICEVOX/`、`voicevox_engine-master/`、各壓縮檔——龐大的二進位資產
- `__pycache__/`

`chats/` 屬一般資料，預設會納入版本控制；若對話內容涉及隱私，請自行將 `chats/` 加入 `.gitignore`。
