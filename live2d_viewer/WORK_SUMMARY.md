# 工作摘要

## Objective
- 讓 3D 檢視器正確演出表情與動作，並修復「動作執行後卡在最後一個表情」「執行動作後歷史不再動」問題。程式碼修復已全數驗證；目前使用者端仍回報「只會嘴巴對……」，判定為瀏覽器頁面仍跑舊 bundle，已加版本標記待使用者重新整理確認。

## Important Details
- `viewer_main.js` 內建 11 表情（`EXPRESSIONS`）與 11 動作（`MOTIONS`），不讀模型自帶 .motion3/.exp3；`build.bat` 在 `live2d_viewer\app_files` 以 `.\build.bat` 重建，直接寫入 `web\viewer.bundle.js`。
- 模型共 7 個：ariu / fuxuan / Hiyori / Mao / March 7th / 神宫白子(面饼0) / 魔女。**全部模型**在新 bundle 下皆已個別驗證（見 probe24）：blush+shake 均正常、結束自動回中性。
- 「神宫白子」='3D\神宫白子\神宫白子模型\面饼0.model3.json'：有 `ParamBodyAngleZ`/`ParamBrowLY`，缺 `ParamBodyAngleX/Y`（身體用自訂 `Param49/50/51`）；零件開關 `Param91`（脸红）/`Param92`（生气）/`Param93`（傻饼）。
- 3D 指令以獨立 `3d` 欄位儲存於 assistant 訊息；live 路徑 `AI_voice_chat_ui.py` L2200-2206 抽出 `[3d]` 區塊並 `send_3d_command`（`lipsync` 同佇列 L2286/2321）；歷史由 `_replay_3d_history()` 重播。
- session 檔驗證（`chats\chat_20260827_203458_502.json`，enable_3d=True，18 則）：送出並儲存的指令俱為「expression（blush/worried/smug）＋motion（look_*/shake）」——live 命令生成端正常。
- 診斷伺服器：PID 13472 綁 8767（自啟、留給使用者頁面，勿殺），工作目錄=live2d_viewer，唯讀佐證：`/api/models` 正常、8727 提供之 bundle 即為磁碟新檔。
- **重要**：server.py 的 `/api/poll`（L244-251）會「整批取走並清空命令佇列」；頁面關閉時殘留的 dead long-poll 會吃走「下一筆」命令（多頁面共用佇列是設計、且有此競態）。測試前須重啟 8768 且每次只有一頁。
- 中文字串不可經 PowerShell pipeline 傳給 node（會變亂碼）；一律用 Write/Edit 工具直接寫 .js。

## Work State
### Completed
- 根因：`_applyPending` 原在 `physics.evaluate` 前，物理把表情/指令還原→改為物理後套用（指令層覆蓋）。
- `SWITCH_RESET`＝`Param91/92/93/94`；`FACIAL_RESET`＝眉/笑紋/嘴型；切表情先清開關、再歸零未覆寫者。
- 動作結束自動還原中性：`exprSeq`/`exprSeqAtMotion`（於 **ViewerModel** 建構子初始化，先前誤放 ViewerApp 導致 NaN 使之失效）、`applyMotionCmd` 記錄 `exprSeqAtMotion`、`foldMotion` 結束設 `autoNeutralPending`、`updateFrame` **幀開頭**（在 `foldExpression` 前）判 `exprSeq===exprSeqAtMotion` 才 `applyExpressionCmd({name:'neutral'})`。延幀是關鍵：直接於 foldMotion 內執行會同幀被 `_applyPending` 以舊值寫回。
- 加「版本標記」：`viewer_main.js` 啟動區 `BUILD_TAG='build-20260829-1125'` 寫入 `#bundle-ver`；`index.html` topbar 加 `<span id="bundle-ver"></span>`。bundle 重建 567,052 bytes（11:44）。
- 驗證（8758 乾淨重啟、單頁）：
  - probe21 三場景全過：A blush+shake→結束回中性(expression+參數全 0)；B 動作中途改 happy 不覆寫；C reset 歸零。
  - probe24 **7 模型全數**：blush 生效（EyeLSmile=0.4/Brow 0.2/AngleY 位移）+ 結束自動回中性。
  - probe25：`#bundle-ver` = build-20260829-1125、blush during=0.4/motion=true、after=0/false。
- 修復 `AI_voice_chat_ui.py`（前輪）：`_replay_3d_history` 尾端舊 `_save_session` 殘渣已移除、重建 `write_session_file()`（11 呼叫點），`py_compile` 通過。
- 測試伺服器 8768 已關閉；診斷伺服器 8767 未動。

### Active
- 判定使用者仍卡動作＝**viewer 頁面未載入新 bundle**（今晨 06:35 自動開啟、11:44 重建後未重新整理）：新程式碼在乾淨頁面對 7 模型全部正確；session 檔亦證明 live 命令有正常產生與送出。
- 待使用者：關閉多餘 viewer 分頁→僅留一個→Ctrl+F5 重新整理→確認 topbar 顯示 `build-20260829-1125`→再測試一次。

### Blocked
- (none)

## Next Move
1. 請使用者完成下方操作後回報：
   - 關閉「所有」舊的 viewer 分頁／視窗，只留一個。
   - 在該分頁按 `Ctrl+F5` 重新整理。
   - 確認 topbar 出現 `build-20260829-1125`（未出現=仍舊版）。
   - 再跟 AI 對話一次：表情會出現、動作結束後回原本姿勢、歷史對話重播有動作。
2. 若確認已載入新 bundle 仍「只有嘴巴動、動作卡住」→ 進入下階段：請使用者提供：(a) 目前顯示哪個模型；(b) 是否 enable_3d 開關開啟；(c) 該頁面抓到的 topbar 版號。再依此深查（可能方向：頁面 JS 例外中斷 rAF、`_report_viewer_status` 或 polling 干擾、blur 後 rAF 節流）。

## Relevant Files
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\web\viewer_main.js`：`EXPRESSIONS`/`MOTIONS`、`SWITCH_RESET`/`FACIAL_RESET`（L35 附近）、`ViewerModel` constructor（L290+，含 exprSeq/exprSeqAtMotion/autoNeutralPending）、`updateFrame`（L402 幀開頭消費旗標）、`applyExpressionCmd`（L342）、`applyMotionCmd`、`foldMotion` 結束（L479）、啟動區 `BUILD_TAG`（L1134+）。
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\web\index.html`：topbar 含 `#bundle-ver`。
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\web\viewer.bundle.js`：567,052 bytes（11:44）。
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\server.py`：`_poll`（L244-251）會清空佇列；無唯讀狀態端點（只有 /api/ping、/api/models）。
- `D:\claudeFile\20260826-VOICEVOX\AI_voice_chat_ui.py`：live 3d（L2200-2206）、lipsync（L2284-2321）、`_replay_3d_history`（L1630，重播前 `reset`、段間 1.6s、結束 `neutral`、`_replay_lock`）、`write_session_file`（L1664）、`VIEWER_URL="http://127.0.0.1:8767"`（L59）。
- `C:\Users\ayore\AppData\Local\Temp\opencode\`：`probe20/21/23/24/25.js` 及各 *_out.txt、`diag8768.pid`。