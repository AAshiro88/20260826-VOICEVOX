# VOICEVOX × Ollama／OpenRouter 語音對話工具

以本地語音合成（VOICEVOX）搭配大型語言模型（本機 Ollama 或 OpenRouter 雲端 API）的圖形介面對話程式。AI 回覆會自動朗讀，支援多重對話管理、角色設定，對話語言可選日本語／中文／English，介面文字亦提供三語切換。已內建 **Live2D 3D 演出**：開啟「啟用 3D 演出」後，AI 會在回覆時輸出 `[3d]` 指令，驅動另一視窗中的 3D 角色做表情、動作與口型同步。

主程式使用 Python 標準庫，另使用 **deep-translator** 將非日文回覆翻成日文朗讀；相依套件列於 `requirements.txt`。

## 功能特色

- **語音朗讀**：AI 回覆逐句送 VOICEVOX 合成並播放，可隨時停止；**雙擊任何一則有底線的朗讀句子可重新播放**；聲選擇採兩段式（角色→風格），偏好 3 個角色（猫使ビィ、小夜/SAYO、もち子さん）優先顯示
- **單語 LLM 回覆 + 本機翻譯流程**：模型只需以使用者選擇的語言回答純文字；中文／English 回覆會由 `deep-translator` 翻成日文供 VOICEVOX 朗讀，日語回覆則直接朗讀
- **介面與對話語言分離**：主畫面的語言下拉可切換介面文字；每個 chat 建立後即綁定自己的語言，切換介面不會改寫既有對話檔的 `lang`
  - 日本語：模型回覆、顯示與朗讀都使用同一份日文原文，不需翻譯
  - 中文／English：模型只回覆該語言；程式使用 `deep-translator` 翻譯成日文，日文譯文不會送回模型、不佔 LLM token
- **雙來源 LLM**：一鍵切換本機 Ollama 或 OpenRouter（顯示全部模型，模型 Combobox 可編輯即時篩選關鍵字）
- **多重對話管理**：每個對話存成獨立 JSON，可開新對話、載入、改名、刪除；記錄當下使用的聲音／服務／模型／語言，載入時自動還原
- **自動命名**：新對話先以時間戳命名，第一則回覆後由 AI 自動取標題（標題語言跟隨對話語言），之後可手動改名；允許多個對話同名（清單自動加編號區分）
- **角色設定**：自訂 AI 人設（例如「傲嬌的妹妹」），可透過「編輯」按鈕開啟多行對話框輸入完整角色描述（含範本載入），跟著對話一起存在 chats/*.json，載入對話即還原人設
- **歷史自動摘要**：對話過長時自動呼叫目前模型整理成重點摘要＋保留最近數則原文（摘要輸入同樣只取對話語言）；整理中暫停接受新訊息
- **長期記憶（chroma_db）**：將對話中的重要事實以向量庫儲存（本機 ONNX embedding，不需 API），分「自動」與「手動」兩種寫入：「存入記憶」按鈕可隨時把目前對話抽成長期記憶；歷史觸發自動摘要時，被壓縮掉的舊對話也會順手抽成記憶。以「對話檔」為隔離單位，未來回覆前會檢索該對話自己的過往記憶並附在系統提示中供模型參考（屬參考資訊，與話題無關時一律忽略）
- **雙語顯示**：中文／English 模式下，AI 以「對話語言（主要行）＋日文朗讀行（灰色底線）」顯示，看得到也聽得到
- **重新生成**：AI 回覆不滿意或格式壞掉時，按「重新生成」按鈕移除最後一則回覆並重新呼叫模型生成
- **3D 角色演出（Live2D）**：勾選「啟用 3D 演出」後，系統提示會注入 3D 指令格式說明，模型可依對話內容輸出 `[3d]{json}[/3d]` 指令，驅動 `start_viewer.bat` 開啟的 3D 檢視器（表情／動作／參數控制／口型同步）；指令不會顯示在對話、不會翻譯、也不會存入歷史。檢視器另支援滑鼠互動（視線跟隨、點擊部位反應）與呼吸／自然搖擺的幅度微調
- **介面文字外部化**：`locales/{zh,ja,en}.json` 維護三語介面字串，方便增刪與在地化

## 檔案結構

```
20260826-VOICEVOX/
├─ AI_voice_chat_ui.py      主程式（Tkinter 圖形介面）
├─ memory_store.py          長期記憶庫（chroma_db 向量儲存封裝）
├─ ollama_voice_chat.py     舊版純命令列介面（僅支援 Ollama，保留備用）
├─ voicevox_api_test.py     VOICEVOX 引擎 API 連通測試腳本
├─ locales/                 介面多國語系
│  ├─ zh.json               繁體中文（88 鍵）
│  ├─ ja.json               日本語（88 鍵）
│  └─ en.json               English（88 鍵）
├─ live2d_viewer/           3D 檢視器（Live2D Web）
│  ├─ server.py             本機伺服器（Python 標準庫，127.0.0.1:8767）
│  ├─ web/                  前端（index.html / style.css / viewer_main.js，經 esbuild 打包成 viewer.bundle.js）
│  └─ app_files/            esbuild 建置（package.json / build.bat）
├─ start_viewer.bat         雙擊啟動 3D 檢視器伺服器（自動開瀏覽器）
├─ 3D/                      Live2D 模型資料夾（掃描 *.model3.json；不入版控，clone 後自行放入）
├─ CubismSdkForWeb-5-r.5/   Live2D Cubism Web SDK r.5（版控僅收 Framework/ 與 Core/，官方 Samples/ 不納入）
├─ .env                     OPENROUTER_API_KEY（金鑰，不上 GIT）
├─ chats/                   對話紀錄（chat_日期_時間_毫秒.json，含角色設定；屬個人隱私，不入版控）
└─ chroma_db/               長期記憶庫（含 chroma.sqlite3 與向量索引，首次寫入時自動建立；不入版控）
```

## 環境需求

| 項目 | 說明 |
|------|------|
| 作業系統 | Windows 10 以上（使用 winsound 播放音訊） |
| Python | 3.9 以上（開發環境為 Anaconda Python 3.13.9；`chromadb` 需要較新版本） |
| VOICEVOX | [產品版 0.25.2](https://voicevox.hiroshiba.jp/)（`voicevox-windows-directml-0.25.2.zip`），啟動後引擎自動監聽 `127.0.0.1:50021` |
| 對話來源 | Ollama（本機）或 OpenRouter API 金鑰（擇一即可） |
| 3D 演出（選用） | Live2D 模型放 `3D/`（資料夾未入版控）；建置前端需 Node.js；通常使用 Chrome／Edge／Firefox 開啟檢視器 |

## 安裝

安裝 Python 相依套件，並確認 VOICEVOX 已啟動：

```bat
pip install -r requirements.txt
```

若使用 Anaconda，請以實際啟動程式的同一個 Python 執行上述指令，確保能找到 `deep-translator` 與 `chromadb`。

> 長期記憶使用 chroma 內建的 ONNX embedding 模型（`all-MiniLM-L6-v2`），**首次抽取記憶時**會在系統使用者快取目錄（`%USERPROFILE%\.cache\chroma\onnx_models\`）自動下載約 80MB 的模型，之後離線使用；下載失敗時該次記憶寫入會自動略過，下次對話重試，不影響語音對話。

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

啟動時若已有對話紀錄，會自動以上次對話的語言直接進入主畫面；第一次啟動（無對話紀錄）才會彈出語言選擇視窗。沒有 chat 時程式不會自動建立檔案，請按「開新對話」。切換介面語言不會改變已建立 chat 的語言。

介面操作：

| 區塊 | 說明 |
|------|------|
| 狀態列 | 即時顯示引擎／Ollama／OpenRouter 連線狀態（綠＝正常、紅＝未連線） |
| 聲音列 | Provider 切換 → 角色 Combobox（偏好 3 個角色優先顯示，取消勾選「偏好」展開全部）→ 風格 Combobox（對應角色的朗讀風格）→ 偏好篩選 checkbox |
| 模型列 | 模型 Combobox（可編輯+即時篩選，右側有文字輸入框可過濾關鍵字）→ 介面語言切換（保留目前 chat 的語言）→ 停止朗讀 |
| 角色列 | 單行摘要 Entry（width=18）→「編輯」按鈕開啟多行對話框 →「套用」按鈕或 Enter 套用角色；右側為「啟用 3D 演出」開關 |
| 對話列 | 開新對話（綁定目前語言）／載入／改名／刪除／存入記憶；**清單只顯示目前語言的對話** |
| 輸入區 | Enter 送出、Shift+Enter 換行 |

其他腳本：

```powershell
# 引擎連通測試（含選聲音與合成一段 WAV）
python voicevox_api_test.py

# 舊版純文字介面（僅 Ollama）
python ollama_voice_chat.py
```

### 3D 角色演出（Live2D）

3D 是「獨立視窗」模式，與主程式分開啟動：

1. **建置前端（僅首次／改過 `viewer_main.js` 後）**：需要 Node.js，執行 `live2d_viewer\app_files\build.bat` 產生 `viewer.bundle.js`
2. **安裝模型**：`3D/` 未入版控，clone 後請先把想用的 Live2D 模型資料夾放進去（每個模型至少需 `*.model3.json` 與 `.moc3`、紋理）
3. **啟動檢視器**：雙擊 `start_viewer.bat`，會開啟本機伺服器並自動在瀏覽器打開檢視器視窗（網址 `http://127.0.0.1:8767/`），用頂部下拉切換模型
4. **主程式**：照常啟動 `start_ai_voice_chat.bat`，勾選「啟用 3D 演出」後開始對話

勾選後 AI 可依對話內容輸出 `[3d]` JSON 指令，支援：`expression`（11 種表情）、`motion`（11 種動作）、`parameter`（指定參數過渡）、`stop`、`reset`；朗讀期間角色嘴型會跟著說話起伏（`speak` 開始／結束自動送 `lipsync` 事件）。關閉開關即恢復原本純文字行為，指令也會被忽略。若檢視器未執行，勾選時會在對話區提示一次，對話流程不受影響。

檢視器網頁右下角有「動作測試」面板（可整段收合），分群組管理：

- **表情／動作／參數測試**：點按即可送出，指令走與 AI 相同的 `/api/command` 鏈路
- **控制**：「呼吸晃動」「自動眨眼」「口型同步」開關，以及停止動作、全部還原
- **互動**（可收合）：滑鼠與角色的互動開關
  - 「視線跟隨」：游標移到哪，角色就看向哪；「水平反轉」「垂直反轉」可調整視線方向與游標的一致性
  - 「點擊反應」：點擊角色不同部位觸發對應動作——頭部＝點頭、胸部＝短暫害羞表情、腰部＝指向／受擊（有原生 TapBody 的模型會播放）、左右臂＝左／右傾（右臂上緣為揮手）、下緣＝搖頭
  - 注意：游標移出瀏覽器視窗後視線會停在最後方向，屬瀏覽器事件限制
- **微調（呼吸與搖擺）**（可收合）：自然搖擺開關，以及呼吸幅度／呼吸頻率／搖擺幅度三個滑桿（切換模型後沿用）

角色上下還有「呼吸晃動」「自動眨眼」控制：關閉後角色不會自動扭動或眨眼。

### 懶人啟動與打包成 exe

| 檔案 | 用途 |
|------|------|
| `start_ai_voice_chat.bat` | 雙擊直接啟動主程式（自動找 Anaconda Python） |
| `start_viewer.bat` | 雙擊啟動 3D 檢視器伺服器（自動找 Anaconda Python，自動開瀏覽器） |
| `build_exe.bat` | 以 PyInstaller 打包成單一執行檔 `dist\AI_VoiceChat_UI.exe` |

打包注意：**`.env`、`chats/`、`locales/` 不會被封裝進 exe**。程式在打包模式（frozen）下會改以 exe 所在資料夾作為基底目錄，因此使用 exe 前請手動把 `.env` 與 `locales/` 複製到 `dist\` 旁；`chats\` 與 `chroma_db\` 會在首次存檔時自動建立在 exe 旁。ONNX embedding 模型快取於系統使用者目錄（`%USERPROFILE%\.cache\chroma\`），不隨 exe 分發。

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
  "enable_3d": false,
  "history": [
    { "role": "user", "content": "你好" },
    { "role": "assistant", "content": "你好呀！", "voice": "やあ！" },
    { "role": "system", "content": "以下是更早對話的重點摘要：..." }
  ]
}
```

`history` 中 role 為 system 的項目是自動摘要產物，載入重播時不顯示。assistant 的 `content` 永遠是模型以使用者語言回答的單語文字；非日語對話的 `voice` 則是 `deep-translator` 產生的日文朗讀稿，**不會送回 LLM**。`persona` 是該對話專屬的角色設定，載入時自動還原到介面輸入框。`enable_3d` 是該對話的 3D 演出開關（`true`／`false`，舊檔案沒有此欄位時視為關閉）。`lang` 是該對話綁定的語言（`ja`／`zh`／`en`），同時決定它在對話清單中的歸屬——**清單只顯示目前語言的對話**；舊檔案沒有此欄位時視為 `zh`，不需遷移。

> 舊版 assistant JSON（含 `zh`／`en`／`jp` 欄位）仍可正常載入、顯示與重播；新回覆會在下次存檔時使用單語 `content` 與獨立 `voice` 欄位。更早的 `日:`／`中:`／`英:` 前綴格式則視為一般文字，建議重新生成以取得正確朗讀稿。

各語言的回覆格式：

| 語言 | LLM 輸出 | 送模型的 assistant 歷史 | 朗讀 |
|------|----------|----------------------------|------|
| `ja` | 純日文 | 日文原文 | 日文原文（VOICEVOX 合成） |
| `zh` | 純繁體中文 | 中文原文 | `deep-translator` 翻成日文 |
| `en` | 純 English | English 原文 | `deep-translator` 翻成日文 |

`llm_view()` 送模型時只保留 `content`，不會送出 `voice` 日文朗讀稿；`reply_parts()` 同時相容讀取新單語格式與舊 JSON 格式。

## 程式架構

`AI_voice_chat_ui.py` 是主程式，使用 Python 標準庫加上 `deep-translator`、`chromadb`（長期記憶），`memory_store.py` 封裝 chroma_db 的讀寫；主要區塊如下：

```text
AI_voice_chat_ui.py
├─ 環境與路徑        ENGINE_URL / OLLAMA_URL / OPENROUTER_URL / ENV_PATH / CHATS_DIR / LOCALES_DIR
├─ .env 載入         load_env() → OPENROUTER_API_KEY（金鑰存於記憶體，不寫入輸出）
├─ 介面文字載入      load_locale() / get_locale_dict() / set_ui_lang() / tr()
│                    （自 locales/{zh,ja,en}.json 動態載入，缺漏退回繁體中文）
├─ 語言與提示詞      CONVO_LANGS / SYSTEM_PROMPTS / _3D_PROMPTS / SUMMARY_ASKS / SUMMARY_HEADERS / TITLE_ASKS
├─ HTTP 輔助         http_json() / post_json()（urllib 標準庫）
├─ 3D 檢視器整合     viewer_alive() / send_3d_command() / split_3d()（[3d] JSON 指令抽取與發送）
├─ 回覆與翻譯        reply_parts() / assistant_conv_text() / llm_view()
│                    translate_to_japanese()（deep-translator）
├─ 長期記憶          memory_store.py（MemoryStore：add_facts / retrieve / facts_for / delete_chat）
│                    ─ chroma_db 向量儲存，以對話檔名為隔離鍵，首次寫入時下載 ONNX embedding 模型
├─ 其他純函式        split_sentences() / sanitize_filename() / clean_title()
│                    new_chat_filename() / scan_chat_files()
├─ PREFERRED_SPEAKERS / _PERSONA_TEMPLATE
├─ class VoiceChatApp（Tkinter 主程式）
│   ├─ UI 建置        _build_widgets()（4 列佈局：狀態/聲/模型/角色）/ _clear_chat_display()
│   │                 / _append() / _register_ai_message()
│   ├─ 佇列輪詢       _poll_queue() / _emit()（背景執行緒 → 主執行緒更新畫面）
│   ├─ 後端初始化     init_backend() / _collect_voices() / _load_voices() / _apply_models()
│   ├─ 事件處理       on_provider_selected() / on_speaker_selected() / on_voice_style_selected()
│   │                 / on_toggle_preferred_voices() / on_model_selected()
│   │                 / _on_model_filter() / _on_model_filter_entry() / on_lang_selected()
│   ├─ 對話管理       new_session() / load_selected_session() / rename / delete
│   │                 handle_restore() / write_session_file() / refresh_session_list()
│   ├─ 角色設定       apply_persona() / build_system_prompt() / open_persona_editor()
│   │                 / _on_toggle_3d() / _check_viewer_3d()
│   ├─ 訊息流程       send_message() / _on_return() / retry_last() / chat_worker()
│   ├─ 長期記憶       extract_memories() / _run_memory_extract() / memory_block_for()
│   │                 / save_memory_manual() / _memory_worker()
│   ├─ 後台工作       call_llm() / maybe_summarize() / auto_title_worker() / speak()
│   └─ 停止與重播     stop_speaking() / replay_message()
└─ main() + 啟動錯誤記錄（startup_error.log）
```

### 執行緒模型

- **主執行緒**：Tkinter 事件迴圈，並以 `_poll_queue()` 每 100ms 輪詢 `ui_queue`。
- **背景執行緒**：`chat_worker()`、`speak()`、`init_backend()`、`auto_title_worker()`、`maybe_summarize()`、`_memory_worker()`（手動存記憶）都在工作執行緒執行。
- **關鍵規則**：背景執行緒不直接操作 Tk 元件，一律透過 `self._emit()` 把訊息塞進 `ui_queue`，由主執行緒的 `_poll_queue()` 依型別處理，避免跨執行緒 UI 存取造成的競態與崩潰。

### 佇列訊息型別（ui_queue）

| 型別 | 內容 |
|------|------|
| `text` | `(文字, tag)` 附加到對話區 |
| `ai_msg` | `(conv, voice)` 顯示一則 AI 回覆（含朗讀） |
| `busy` | `(bool)` 切換忙碌鎖與按鈕可用狀態 |
| `engine_ok/ng`、`ollama_ok/ng`、`or_ok/ng` | 狀態列紅綠燈 |
| `voices`、`ollama_models`、`or_models` | 載入聲音／模型清單 |
| `sessions` | 更新對話清單下拉 |
| `restore` | 還原一個對話工作階段 |

### 資料流程

```
使用者在輸入框送出訊息（send_message）
  → 背景執行緒 chat_worker()
      → 以使用者訊息檢索該對話的長期記憶，命中時附加到系統提示（僅供參考）
      → 歷史過長先 maybe_summarize()（壓縮舊訊息；被壓縮的部分順手抽成長期記憶）
      → call_llm() 呼叫 Ollama 或 OpenRouter
      → （3D 開啟時）split_3d() 抽取 [3d] 指令 → send_3d_command() 送往檢視器，
         指令區塊從正文移除（不顯示、不翻譯、不入歷史）
      → 回覆以使用者語言存入 history
      → 中文／English 經 deep-translator 轉為日文 voice（日本語則直接使用原文）
      → write_session_file() 存檔
      → _emit("ai_msg", conv, voice) 更新畫面
      → speak(voice) 逐句送往 VOICEVOX 合成並播放
          （3D 開啟：speak 開始／結束自動送 lipsync 口型事件，中途停止也收尾）
```

### 語言與回覆格式

- 對話語言 `lang`（`ja`/`zh`/`en`）綁定在各對話檔。
- AI 回覆一律是使用者選定語言的純文字；模型不負責產生日文翻譯。
- `llm_view()` 只傳遞 `content`，獨立的 `voice` 日文朗讀稿不佔 token。
- `translate_to_japanese()` 只在中文／English 回覆後呼叫；日文對話跳過翻譯。

### 重新生成邏輯（retry_last）

依歷史最後一則的角色分兩路：

- **最後是 `user`**（上次呼叫失敗）：`chat_worker()` 失敗時不會移除 user 訊息，因此直接重送即可，不需刪除任何內容。
- **最後是 `assistant`**（對回覆不滿意）：移除該 assistant 回覆，重送其後的 user 訊息。

`chat_worker()` 透過 `append_user` 參數避免重新生成時重複加入 user 訊息。

## 常見問題

| 現象 | 原因與處理 |
|------|-----------|
| `[WinError 10061] 無法連線` | VOICEVOX 未啟動或埠不是 50021，先啟動 VOICEVOX |
| `HTTP Error 405` | `/audio_query` 必須用 POST（現行程式碼已正確處理） |
| OpenRouter `HTTP Error 429` | 免費模型有頻率限制，稍候再試或換一個模型 |
| 找不到偏好的 3 個聲音 | VOICEVOX 版本未含這些角色，請更新或改用其他聲音測試 |
| 中文／英文沒有朗讀 | `deep-translator` 未安裝、網路無法連至翻譯服務，或翻譯服務暫時失敗；確認 `pip install -r requirements.txt` 已在啟動程式的 Python 環境執行，之後可按「重新生成」重試 |
| 對話出現「整理歷史中」 | 歷史超過 5000 字元觸發自動摘要，完成前無法送出新訊息 |
| 想改對話語言 | 既有 chat 的語言不可改，以避免混合語言或誤覆寫 `lang`；切換介面語言後按「開新對話」，新 chat 才會採用該語言 |
| 對話清單找不到某個對話 | 該對話綁定的是其他語言；既有 chat 不可改語言（舊檔案無 `lang` 時視為中文） |
| 介面顯示為英文／日文 | 使用主畫面的語言下拉切換介面；目前 chat 的對話語言與資料不會改變 |
| 介面翻譯缺漏 | `locales/{zh,ja,en}.json` 找不到對應鍵時，介面會退回繁體中文，若仍無則顯示原 key；可自行編輯 JSON 補上 |
| 模型清單太多找不到 | 在模型列右側的篩選輸入框打關鍵字即可即時過濾；Ollama 與 OpenRouter 皆適用 |
| 角色設定太長沒地方寫 | 按「編輯」按鈕開啟多行對話框，可輸入完整角色描述（含範本），確定後自動更新摘要 |
| 勾選 3D 後提示「檢視器尚未連線」 | 先執行 `start_viewer.bat` 開啟檢視器再對話；若已開啟仍提示，確認瀏覽器頁面停在 `127.0.0.1:8767` 且伺服器輸出沒有啟動失敗 |
| 3D 視窗開了但角色沒動作 | 確認已勾選「啟用 3D 演出」；模型回覆若沒有 `[3d]` 區塊可能是模型能力或回覆太短，可換較強模型再試；角色沒有該表情參數時自然不會動 |
| `viewer.bundle.js` 不存在 | 尚未建置前端，先執行 `live2d_viewer\app_files\build.bat`（需 Node.js）；`start_viewer.bat` 啟動時也會提醒 |

摘要門檻與保留則數可在 `AI_voice_chat_ui.py` 頂部的 `HISTORY_CHAR_LIMIT`、`KEEP_RECENT_MESSAGES` 調整。

## GIT 注意事項

`.gitignore` 已排除（不進入版本控制）：

- `.env`——含 API 金鑰，**絕對不要提交或分享**
- `VOICEVOX/`、`voicevox_engine-master/`、各壓縮檔——龐大的二進位資產（`VOICEVOX/` 為語音引擎執行檔資料夾）
- `3D/`——模型資產過大且含第三方版權模型（Felis、Gothic、March 7th 等）；clone 後請自行放入要用的 Live2D 模型
- `chats/`——對話紀錄含人設與逐字稿，屬個人隱私
- `chroma_db/`——長期記憶庫（含向量索引與 chroma.sqlite3），屬個人隱私
- `CubismSdkForWeb-5-r.5/Samples/`——官方 Demo 範例，檢視器不需要
- `CubismSdkForWeb-5-r.5.zip`、`live2d_viewer/app_files/node_modules/`、`live2d_viewer/web/viewer.bundle.js.map`——壓縮檔、npm 依賴與建置 sourcemap
- `__pycache__/`、`*.pyc`

Live2D Cubism SDK for Web **r.5** 只收錄檢視器執行所需的部分：`Framework/`（TS 原始碼與 shader）與 `Core/`（`live2dcubismcore.min.js`），約 1.4 MB。官方完整 SDK（含 Samples，約 25 MB）需自行至官方下載頁 https://www.live2d.com/sdk/download/web/ 取得；更新 SDK 時更換 `CubismSdkForWeb-5-r.5/` 內容後重建前端（`build.bat`）即可。
