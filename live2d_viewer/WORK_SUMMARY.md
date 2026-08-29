# 工作摘要

## Objective
- 讓 3D 檢視器正確演出表情與動作，並修復「動作執行後卡在最後一個表情」「執行動作後手動切回原表情、歷史不再動」問題。目前目標已達標（本輪修復全部驗證通過）。

## Important Details
- `viewer_main.js` 內建 11 表情（`EXPRESSIONS`）與 11 動作（`MOTIONS`），不讀模型自帶 .motion3/.exp3；`build.bat` 在 `live2d_viewer\app_files` 以 `.\build.bat` 重建。
- 「神宫白子」=`3D\神宫白子\神宫白子模型\面饼0.model3.json`：**全套標準參數幾乎都有**（含 `ParamBodyAngleZ`、`ParamBrowLY`），但缺 `ParamBodyAngleX/Y`（身體用自訂 `Param49/50/51`）；有零件開關 `Param91`（脸红）、`Param92`（生气）、`Param93`（傻饼）。
- 3D 指令以獨立 `3d` 欄位儲存於 assistant 訊息；載入歷史時 `_replay_3d_history()` 依序重播。
- 診斷伺服器：PID 13472 綁 8767（開機時自啟、留給使用者頁面，勿殺）；測試一律用獨立 8768（用完即關），由 `server.py --no-open --port 8768` 啟動。使用者自己開的 Chrome 開著 8767 頁面長期 poll、會吃走同一 server 指令——多頁面共用 long-poll 佇列是設計行為；**測試注意**：一支 probe 的頁面關閉時若殘留未完成的 poll，伺服器會把「下一筆命令」丟給已死 waiter，造成第一筆命令遺失。測試前先重啟 8768、且每次只有一頁，即可避免。
- 中文字串不可經 PowerShell pipeline 傳給 node（會變亂碼）；一律用 Write/Edit 工具直接寫 .js。

## Work State
### Completed
- 診斷根因：指令層在 `physics.evaluate` 之前套用，物理把表情全部還原（Hiyori blush 關 physics 前 0.4→後 0 實測證實）。
- 修復 `viewer_main.js`（bundle 已重建 566,834 bytes，11:22）：
  1. `_applyPending` 移到 `physics.evaluate` 之後（指令層覆蓋物理）。
  2. `SWITCH_RESET`＝`Param91/92/93/94` 全歸 0；blush→P91、angry→P92、smug→P93；`applyExpressionCmd`/`resetAll` 先清開關再套用。
  3. `FACIAL_RESET`＝眉毛/笑紋/嘴型參數；切表情時一次性歸零未被新表情覆寫者（不碰眼皮/角度，保護眨眼與視線）。
  4. **動作結束自動還原中性表情**：`(a)` `applyExpressionCmd` 開頭 `exprSeq += 1`、`applyMotionCmd` 記錄 `exprSeqAtMotion`、constructor 初始化 `exprSeq/exprSeqAtMotion = 0`（**注意：首次修正時把它們加錯到 ViewerApp 的 constructor，probe22 抓出 `NaN===NaN` 才真正修好，必須在 ViewerModel 建構子**）；`(b)` `foldMotion` 結束（`t>=m.dur`）時若 `exprSeq===exprSeqAtMotion && expression 非空` 就設 `autoNeutralPending=true`；`(c)` `updateFrame` 幀開頭消費旗標並重判條件後才 `applyExpressionCmd({name:'neutral'})`。延到幀開頭執行是關鍵：`foldExpression` 在 `foldMotion` 前先建好舊表情的 pending，若在 foldMotion 內立刻還原會在同幀 `_applyPending` 被舊值寫回——probe23 連續三次取樣參數仍 0.4 抓到，延後一幀後全歸 0。
- 修復 `AI_voice_chat_ui.py`（前輪）：`_replay_3d_history` 尾端黏著舊 `_save_session` 殘渣（每次重播誤存檔）；已移除並重建 `write_session_file()`（現行 11 個呼叫點），`py_compile` 通過。
- 驗證（8768、乾淨的重啟、單頁）：
  - probe21 三場景**全過**：A blush+shake→結束自動還原 neutral（expression=SWITCH_RESET 且 EyeLSmile/BrowLY/MouthForm 全 0）；A_during 動作中 blush 保持（0.4/0.2/13.6, motion=true）；B 動作中途改 happy→結束不覆寫新表情；C reset 全歸 0。
  - probe20 全模型回歸**全過**（與既有基線一致）：Hiyori blush=0.4/0.2/0.3、neutral 歸 0、shake=13.5；白子 blush→P91=1、angry→P92=1+ParamBrowLY -0.4、neutral 全關、body_left→ParamBodyAngleZ=10 且 P49/P50 位移。
- 測試伺服器 8768 已關閉（最後 PID 24048），診斷伺服器 8767 未動。

### Active
- (none)

### Blocked
- (none)

## Next Move
1. 請使用者重開 chat app＋viewer，驗證真實體驗：動作結束回中性表情、表情不再卡住、歷史對話重播有動作。
2. 確認無誤後，任務收尾。
3. 若之後再開 probe：先重啟 8768 伺服器再測，單一頁面，避免 dead-poll 吃掉第一筆命令（詳見 Important Details）。

## Relevant Files
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\web\viewer_main.js`：
  - `EXPRESSIONS`/`MOTIONS`、`SWITCH_RESET`/`FACIAL_RESET`（L35 附近）。
  - `ViewerModel` constructor：`expression:{}`、`exprSeq=0`、`exprSeqAtMotion=0`、`autoNeutralPending=false`（L290-306 附近）。
  - `updateFrame`（L402）：幀開頭消費 `autoNeutralPending`；`foldExpression→foldHeld→foldMotion→foldLipsync` 後 `physics.evaluate`，最後 `_applyPending`。
  - `applyExpressionCmd`（L342，`exprSeq+=1`）、`applyMotionCmd`（L358，記錄 `exprSeqAtMotion`）、`foldMotion` 結束設 `autoNeutralPending`（L479 附近）。
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\web\viewer.bundle.js`：已重建（566,834 bytes, 11:22）。
- `D:\claudeFile\20260826-VOICEVOX\AI_voice_chat_ui.py`：`_replay_3d_history`（L1630，重播前先 `reset`、段間暫停 1.6s、結束送 `neutral`、`_replay_lock` 防重入）、`write_session_file`（L1664）、呼叫點 11 處（L1490-2204）、`_report_viewer_status`（L1923）、`VIEWER_URL = "http://127.0.0.1:8767"`（L59）。
- `C:\Users\ayore\AppData\Local\Temp\opencode\`：`probe20.js`（全模型回歸）、`probe21.js`(含插樁)、`probe23.js`（自動還原多點取樣）、`diag8768.pid`（測試伺服器 PID，已關閉）。