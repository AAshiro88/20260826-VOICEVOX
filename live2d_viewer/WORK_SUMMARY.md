# 工作摘要

## Objective
- 讓 3D 檢視器正確演出表情與動作，並修復「動作後卡最後表情」「載入歷史不重播」「啟動未操作即自行動作」「點歷史訊息無動作」等問題。前端與 Python 端修復皆已完成（後兩項待使用者驗證）。

## 目前結論
- 前端程式碼（`viewer_main.js`＋bundle）已全部正確：用乾淨頁面對**全部 7 個模型**（ariu/fuxuan/Hiyori/Mao/March 7th/神宫白子/魔女）實測，表情生效、動作會動、結束自動回中性。
- 使用者回報「只會嘴巴跟著聲音、動作卡住」判定為 viewer 分頁仍舊 bundle（今晨自動開啟、11:44 重建後未重新整理）；已加版本標記 `build-20260829-1125` 供確認。
- 「啟動沒操作 3D 就開始動」根因已修：`handle_restore` 啟動還原後會自動重播 3D 歷史。

## Important Details
- `viewer_main.js` 內建 11 表情（`EXPRESSIONS`）與 11 動作（`MOTIONS`）；`build.bat` 在 `live2d_viewer\app_files` 執行直接寫入 `web\viewer.bundle.js`。
- 模型共 7 個；「神宫白子」有 `ParamBodyAngleZ`/`ParamBrowLY`，缺 `ParamBodyAngleX/Y`（身體用 `Param49/50/51`）；零件開關 `Param91`（脸红）/`Param92`（生气）/`Param93`（傻饼）。
- 3D 指令以 `3d` 欄位存於 assistant 訊息；live 路徑 `AI_voice_chat_ui.py` L2200-2206 送出、lipsync L2286/2321 同佇列；session 檔證實 live 正常產生 `expression+look_*/shake`。
- 呼叫鏈：啟動→`_emit("restore", ...)`(L1223)→`_poll_ui` L1100 `handle_restore(..., replay_3d=False)`；使用者手動切換 `load_selected_session`(L1738)→`handle_restore(..., replay_3d=True)`(L1753)。`handle_restore` 定義於 L1499，`_replay_3d_history` L1630+（reset→逐則重播 0.4s/1.6s→`neutral`，`_replay_lock` 防重入）。
- 診斷伺服器 PID 13472 綁 8767（自啟，勿殺）；測試一律 8768 用完即關。`server.py` `/api/poll`(L244-251) 整批取走並清空佇列，**多頁面共用、dead poll 會吃走下一筆命令**——測試前重啟 8768、每次單頁。server 無唯讀狀態端點；`_report_viewer_status` 僅 ping 不送指令。
- 中文字串不可經 PowerShell pipeline 傳給 node（會變亂碼）；一律用 Write/Edit 工具寫 .js。

## Work State
### Completed
- 前端：指令層改在 `physics.evaluate` 後套用；`SWITCH_RESET=P91/92/93/94`、`FACIAL_RESET` 切表情歸零未覆寫者；動作結束**延幀**自動回中性（`exprSeq`/`exprSeqAtMotion`/`autoNeutralPending`，於 ViewerModel 建構子初始化）。
- 驗證：probe21 三場景、probe24 7 模型、probe20 全模型回歸、probe25 版本標記──全過。
- Python 三項：
  1. `handle_restore` 加 `replay_3d` 旗標（啟動 False、手動 True）──「啟動即自動作」已修、使用者確認。
  2. `replay_3d` dict：`_register_ai_message(conv, voice, cmds=None)` 記錄每則回覆的 3D 指令；三個呼叫點（`ai_msg` 事件 L1065、`handle_restore` L1626、retry 重繪 L~2024）都帶入。`_emit("ai_msg")` 改帶 `commands`（L2249）。
  3. `replay_message(tag)` 雙擊重播：enable_3d 開啟時先依序送該則 3D 指令（0.3s 間隔）再 `speak(voice)`；`finally` 收 busy。
- `py_compile` 通過；bundle 567,052 bytes（11:44）；8768 測試伺服器已關閉；8767 未動。

### Active
- 待使用者驗證「雙擊歷史訊息會連同表情/動作一起重播」。

### Blocked
- (none)

## Next Move
1. 使用者雙擊某則歷史回覆的朗讀行：模型應先演出該則動作、再播聲音。
2. 確認後收尾。

## Next Move
1. 使用者重開 app；確認啟動靜止＋版本標記。
2. 確認後收尾。

## Relevant Files
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\web\viewer_main.js`：EXPRESSIONS/MOTIONS、SWITCH_RESET/FACIAL_RESET（L35 附近）、ViewerModel constructor（L290+）、updateFrame（L402）、applyExpressionCmd（L342）、applyMotionCmd、foldMotion 結束（L479）、啟動區 BUILD_TAG（L1134+）。
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\web\index.html`：topbar 含 `#bundle-ver`。
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\web\viewer.bundle.js`：567,052 bytes（11:44）。
- `D:\claudeFile\20260826-VOICEVOX\live2d_viewer\server.py`：`_poll`（L244-251）。
- `D:\claudeFile\20260826-VOICEVOX\AI_voice_chat_ui.py`：`handle_restore`（L1499）、`_replay_3d_history`（L1630）、`load_selected_session`（L1738）、restore 事件（L1100）、live 3d（L2200-2206）、lipsync（L2284-2321）、`write_session_file`（L1664）、`VIEWER_URL="http://127.0.0.1:8767"`（L59）。
- `C:\Users\ayore\AppData\Local\Temp\opencode\`：probe20/21/23/24/25.js、diag8768.pid。