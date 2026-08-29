# -*- coding: utf-8 -*-
"""Live2D 檢視器後端伺服器（Python 標準庫，無第三方套件）。

用途：
- 提供 Live2D 模型檔案（/model/...）與 SDK 核心（/sdk/core/live2dcubismcore.min.js）給瀏覽器網頁載入
- 提供 HTTP API：
    GET  /api/ping                健康檢查
    GET  /api/models              掃描 3D/ 下的 .model3.json 清單
    POST /api/command             接收 3D 控制指令（送進佇列）
    GET  /api/poll                長時間輪詢取回佇列中的指令（瀏覽器每約 1 秒呼叫一次）

只綁定 127.0.0.1（本機迴圈）。指令佇列大小有限（maxlen=64）。
"""

import argparse
import json
import os
import threading
import time
import urllib.parse
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8767
MAX_BODY = 16 * 1024  # /api/command 最大 16KB
POLL_TIMEOUT = 25     # 長時間輪詢等待秒數

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
WEB_DIR = BASE_DIR / "web"
MODELS_ROOT = ROOT_DIR / "3D"
SDK_CORE_FILE = (
    ROOT_DIR / "CubismSdkForWeb-5-r.5" / "Core" / "live2dcubismcore.min.js"
)
SDK_FRAMEWORK_DIR = ROOT_DIR / "CubismSdkForWeb-5-r.5" / "Framework"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".moc3": "application/octet-stream",
    ".moc": "application/octet-stream",
    ".mtn": "application/octet-stream",
    ".vert": "text/plain; charset=utf-8",
    ".frag": "text/plain; charset=utf-8",
}

ALLOWED_ACTIONS = {
    "expression",
    "motion",
    "parameter",
    "stop",
    "reset",
    "lipsync",
}

# 指令佇列：threading.Condition 保護，新增指令時通知等待中的輪詢端
cmds = deque(maxlen=64)
cond = threading.Condition()


def scan_models():
    """掃描 3D/ 底下所有 .model3.json，回傳清單。

    每筆：{"rel": 相對於 3D/ 的路徑（用 / 分隔）,
           "name": 模型資料夾名稱,
           "id": 去除副檔名的 rel}
    """
    if not MODELS_ROOT.is_dir():
        return []
    result = []
    try:
        for p in sorted(MODELS_ROOT.rglob("*.model3.json")):
            rel = p.relative_to(MODELS_ROOT).as_posix()
            result.append(
                {
                    "rel": rel,
                    "name": p.parent.name,
                    "id": rel[: -len(".model3.json")],
                }
            )
    except Exception:
        return []
    return result


def mime_of(path):
    """依副檔名回傳 Content-Type；無法辨識時回傳八位元流。"""
    return MIME.get(Path(path).suffix.lower(), "application/octet-stream")


def resolve_model_path(rel):
    """解析 /model/<rel> 的請求路徑。

    以 resolve() + commonpath 防止路徑穿越；檔名不存在時回傳 None。
    """
    try:
        target = (MODELS_ROOT / rel).resolve()
        root = MODELS_ROOT.resolve()
        if os.path.commonpath([str(target), str(root)]) != str(root):
            return None
        if not target.is_file():
            return None
        return target
    except Exception:
        return None


def resolve_framework_path(rel):
    """解析 /Framework/<rel>（SDK Framework 內的資源）的請求路徑。

    以 resolve() + commonpath 防止路徑穿越；檔名不存在時回傳 None。
    """
    try:
        target = (SDK_FRAMEWORK_DIR / rel).resolve()
        root = SDK_FRAMEWORK_DIR.resolve()
        if os.path.commonpath([str(target), str(root)]) != str(root):
            return None
        if not target.is_file():
            return None
        return target
    except Exception:
        return None


def json_bytes(obj):
    """將物件轉成 UTF-8 JSON 位元組。"""
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "Live2DViewer/1.0"
    protocol_version = "HTTP/1.1"

    # -- 工具方法 ----------------------------------------------------------

    def _log_request(self, code):
        pass  # 不輸出每筆請求，本工具維持安靜

    def _send_json(self, obj, status=200):
        body = json_bytes(obj)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass  # 瀏覽器中途關閉連線時靜默，不噴 traceback

    def _send_file(self, path, status=200):
        try:
            data = path.read_bytes()
        except Exception:
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            self.send_response(status)
            self.send_header("Content-Type", mime_of(path))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionError, OSError):
            pass  # 瀏覽器中途關閉連線時靜默，不噴 traceback

    def _send_text(self, text, status=200):
        body = text.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass  # 瀏覽器中途關閉連線時靜默，不噴 traceback

    # -- 路由 --------------------------------------------------------------

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            path = urllib.parse.unquote(path)
        except Exception:
            path = path

        if path == "/api/ping":
            self._send_json({"status": "ok"})
            return
        if path == "/api/models":
            self._send_json({"models": scan_models()})
            return
        if path == "/api/poll":
            self._poll()
            return
        if path == "/sdk/core/live2dcubismcore.min.js":
            if SDK_CORE_FILE.is_file():
                self._send_file(SDK_CORE_FILE)
            else:
                self._send_json(
                    {"error": "SDK Core 不存在（請確認 CubismSdkForWeb-5-r.5/Core 位置）"},
                    status=404,
                )
            return
        if path == "/Framework" or path.startswith("/Framework/"):
            rel = path[len("/Framework/"):]
            target = resolve_framework_path(rel)
            if target is None:
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_file(target)
            return
        if path == "/model/" or path.startswith("/model/"):
            rel = path[len("/model/"):]
            self._model_file(rel)
            return
        if path == "/":
            self._send_file(WEB_DIR / "index.html")
            return
        if path in ("/style.css", "/viewer.bundle.js"):
            self._send_file(WEB_DIR / path.lstrip("/"))
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path != "/api/command":
            self._send_json({"error": "not found"}, status=404)
            return
        self._command()

    # -- API 實作 ----------------------------------------------------------

    def _poll(self):
        """長時間輪詢：有指令立刻回傳，否則最多等待 POLL_TIMEOUT 秒。"""
        with cond:
            if not cmds:
                cond.wait(POLL_TIMEOUT)
            batch = list(cmds)
            cmds.clear()
        self._send_json({"commands": batch, "ts": time.time()})

    def _command(self):
        """接收一則控制指令並放入佇列。"""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "bad content length"}, status=400)
            return
        if length <= 0 or length > MAX_BODY:
            self._send_json({"error": "body too large"}, status=413)
            return
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json({"error": "invalid json"}, status=400)
            return
        if not isinstance(data, dict):
            self._send_json({"error": "expected object"}, status=400)
            return
        action = data.get("action")
        if action not in ALLOWED_ACTIONS:
            self._send_json(
                {"error": f"unknown action '{action}'", "allowed": sorted(ALLOWED_ACTIONS)},
                status=400,
            )
            return
        item = {"action": action}
        params = data.get("params")
        if isinstance(params, dict):
            item["params"] = params
        else:
            item.setdefault("params", {})
        with cond:
            cmds.append(item)
            cond.notify_all()
        self._send_json({"status": "ok", "queued": len(cmds)})

    def _model_file(self, rel):
        rel = rel.lstrip("/")
        target = resolve_model_path(rel)
        if target is None:
            self._send_json({"error": "not found"}, status=404)
            return
        self._send_file(target)

    # -- 其他 --------------------------------------------------------------

    def log_message(self, format, *args):
        pass  # 關閉預設請求日誌，避免主控台訊息過多


def main():
    parser = argparse.ArgumentParser(description="Live2D 檢視器後端伺服器")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="監聽埠（預設 8767）")
    parser.add_argument("--no-open", action="store_true", help="啟動後不自動開啟瀏覽器")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((HOST, args.port), ViewerHandler)
    httpd.daemon_threads = True

    url = f"http://{HOST}:{args.port}/"
    print("=" * 52)
    print("  Live2D 檢視器已啟動")
    print(f"  網頁位址：{url}")
    print("  按 Ctrl+C 結束。")
    print("=" * 52)

    if not args.no_open:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()