"""知源 · 本地启动窗口.

双击 `启动.bat`（或运行 ``python launcher.py``）即可启动 FastAPI 服务并打开浏览器。
窗口关闭时会停止由它启动的本地服务，不会影响已经存在的其他实例。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - headless environments
    tk = None  # type: ignore[assignment]

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8000
HEALTH_PATH = "/api/health"

# 开发环境可能把依赖放在项目同级的 .pylibs（而不是项目内），
# 这里把两种位置都加入 sys.path，双击启动时也能直接找到 fastapi/uvicorn。
for _libs_dir in (BASE_DIR / ".pylibs", BASE_DIR.parent / ".pylibs"):
    if _libs_dir.is_dir():
        _libs_path = str(_libs_dir)
        if _libs_path not in sys.path:
            sys.path.insert(0, _libs_path)

BG = "#F7F8FA"
SURFACE = "#FFFFFF"
INK = "#101828"
MUTED = "#5B6B7F"
FAINT = "#8492A6"
LINE = "#DDE3EA"
BLUE = "#2457F5"
BLUE_DARK = "#163AA6"
GREEN = "#0B6B57"
RED = "#B42318"
AMBER = "#8A5A00"
LOG_BG = "#0E1A2B"
LOG_FG = "#DDE3EA"


def _font(size: int, weight: str = "normal") -> tuple[str, int, str]:
    return ("Microsoft YaHei UI", size, weight)


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _find_port(preferred: int) -> int:
    for port in range(preferred, preferred + 30):
        if _port_is_free(port):
            return port
    return preferred


def _health(port: int, timeout: float = 0.8) -> dict | None:
    url = f"http://127.0.0.1:{port}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if isinstance(payload, dict) and payload.get("status") == "ok":
        return payload
    return None


class Launcher:
    def __init__(self, root, preferred_port: int = DEFAULT_PORT, open_browser: bool = True):
        self.root = root
        self.preferred_port = preferred_port
        self.open_when_ready = open_browser
        self.port = preferred_port
        self.proc: subprocess.Popen | None = None
        self.external = False
        self.running = False
        self.log_queue: queue.Queue[str | None] = queue.Queue()
        self.log_lines: list[str] = []

        self.root.title("知源 · 启动器")
        self.root.geometry("680x520")
        self.root.minsize(640, 470)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_ui()
        self.root.after(120, self._poll_log)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=20)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        mark = tk.Label(
            header,
            text="知源",
            bg=BLUE,
            fg="#FFFFFF",
            font=_font(18, "bold"),
            width=4,
            height=2,
        )
        mark.pack(side="left", padx=(0, 14))
        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box,
            text="企业知识库问答",
            bg=BG,
            fg=INK,
            font=_font(20, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_box,
            text="先检索，再回答；每个结论都有出处。",
            bg=BG,
            fg=MUTED,
            font=_font(10),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        card = tk.Frame(
            outer,
            bg=SURFACE,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        card.pack(fill="x", pady=(20, 14))

        status_row = tk.Frame(card, bg=SURFACE)
        status_row.pack(fill="x", padx=16, pady=(14, 6))
        self.dot = tk.Label(status_row, text="●", bg=SURFACE, fg=FAINT, font=_font(12))
        self.dot.pack(side="left")
        self.status_label = tk.Label(
            status_row,
            text="准备启动",
            bg=SURFACE,
            fg=INK,
            font=_font(11, "bold"),
        )
        self.status_label.pack(side="left", padx=(6, 0))
        self.detail_label = tk.Label(
            status_row,
            text="",
            bg=SURFACE,
            fg=MUTED,
            font=_font(9),
        )
        self.detail_label.pack(side="right")

        url_row = tk.Frame(card, bg=SURFACE)
        url_row.pack(fill="x", padx=16, pady=(2, 14))
        tk.Label(
            url_row,
            text="访问地址",
            bg=SURFACE,
            fg=MUTED,
            font=_font(9),
        ).pack(side="left", padx=(0, 10))
        self.url_var = tk.StringVar(value=f"http://127.0.0.1:{self.port}")
        self.url_entry = tk.Entry(
            url_row,
            textvariable=self.url_var,
            state="readonly",
            readonlybackground="#F3F5F8",
            fg=INK,
            relief="flat",
            font=("Consolas", 10),
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=LINE,
        )
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.open_btn = tk.Button(
            url_row,
            text="打开浏览器",
            command=self.open_browser,
            state="disabled",
            bg=SURFACE,
            fg=BLUE,
            activebackground="#EAF0FF",
            activeforeground=BLUE_DARK,
            relief="flat",
            font=_font(9, "bold"),
            cursor="hand2",
            padx=10,
        )
        self.open_btn.pack(side="left", padx=(8, 0))

        actions = tk.Frame(outer, bg=BG)
        actions.pack(fill="x")
        self.start_btn = tk.Button(
            actions,
            text="启动服务",
            command=self.start,
            bg=BLUE,
            fg="#FFFFFF",
            activebackground=BLUE_DARK,
            activeforeground="#FFFFFF",
            disabledforeground="#EEF2F8",
            relief="flat",
            font=_font(10, "bold"),
            cursor="hand2",
            padx=22,
            pady=8,
        )
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(
            actions,
            text="停止服务",
            command=self.stop,
            state="disabled",
            bg=SURFACE,
            fg=INK,
            activebackground="#EEF2F8",
            relief="flat",
            font=_font(10),
            cursor="hand2",
            padx=18,
            pady=8,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.stop_btn.pack(side="left", padx=(10, 0))
        tk.Label(
            actions,
            text="关闭窗口会停止本地服务",
            bg=BG,
            fg=FAINT,
            font=_font(9),
        ).pack(side="right")

        log_head = tk.Frame(outer, bg=BG)
        log_head.pack(fill="x", pady=(18, 6))
        tk.Label(
            log_head,
            text="运行日志",
            bg=BG,
            fg=INK,
            font=_font(10, "bold"),
        ).pack(side="left")
        tk.Label(
            log_head,
            text="首次启动会自动创建演示知识库",
            bg=BG,
            fg=FAINT,
            font=_font(9),
        ).pack(side="right")

        log_frame = tk.Frame(outer, bg=LOG_BG, highlightthickness=0)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_frame,
            height=8,
            bg=LOG_BG,
            fg=LOG_FG,
            insertbackground=LOG_FG,
            relief="flat",
            font=("Consolas", 9),
            wrap="word",
            padx=12,
            pady=10,
            state="disabled",
        )
        scroll = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    def _append_log(self, line: str) -> None:
        self.log_lines.append(line)
        if len(self.log_lines) > 400:
            self.log_lines = self.log_lines[-400:]
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", "\n".join(self.log_lines) + "\n")
            self.log_text.configure(state="disabled")
            self.log_text.see("end")
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def _poll_log(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                if line is None:
                    continue
                self._append_log(line)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_log)

    def _set_status(self, text: str, detail: str = "", color: str = FAINT) -> None:
        self.dot.configure(fg=color)
        self.status_label.configure(text=text)
        self.detail_label.configure(text=detail)

    def _set_running(self, running: bool) -> None:
        self.running = running
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.open_btn.configure(state="normal" if running else "disabled")

    # -------------------------------------------------------------- process
    def start(self) -> None:
        if self.running:
            return

        existing = _health(self.preferred_port)
        if existing:
            self.external = True
            self.port = self.preferred_port
            self.url_var.set(self.url(f"127.0.0.1", self.port))
            self._set_status("已有实例在运行", f"v{existing.get('version', '')}", GREEN)
            self._set_running(True)
            self.open_browser()
            return

        self.port = _find_port(self.preferred_port)
        self.url_var.set(f"http://127.0.0.1:{self.port}")
        if self.port != self.preferred_port:
            self._append_log(f"[启动器] 端口 {self.preferred_port} 被占用，改用 {self.port}")

        missing = [
            name
            for name in ("fastapi", "uvicorn", "numpy", "httpx")
            if importlib.util.find_spec(name) is None
        ]
        if missing:
            self._set_status("依赖未安装", "请先安装 requirements.txt", RED)
            self._append_log(
                "[启动器] 缺少依赖：" + ", ".join(missing) + "\n"
                "请在本目录运行：python -m pip install -r requirements.txt"
            )
            return

        env = os.environ.copy()
        env["ZHIYUAN_PORT"] = str(self.port)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        python_path = [str(BASE_DIR), str(BASE_DIR / ".pylibs")]
        parent_libs = BASE_DIR.parent / ".pylibs"
        if parent_libs.is_dir():
            python_path.append(str(parent_libs))
        if env.get("PYTHONPATH"):
            python_path.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_path)

        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(
                [sys.executable, str(BASE_DIR / "run.py")],
                cwd=str(BASE_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
        except OSError as exc:
            self._set_status("启动失败", str(exc), RED)
            self._append_log(f"[启动器] 无法启动 Python 进程：{exc}")
            return

        self._set_status("正在启动…", "等待健康检查", AMBER)
        self._set_running(True)
        self._append_log(f"[启动器] python run.py  (port {self.port})")
        threading.Thread(target=self._read_output, daemon=True).start()
        self.root.after(250, self._wait_health, 0)

    def _read_output(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            self.log_queue.put(line.rstrip())
        self.log_queue.put(None)

    def _wait_health(self, attempt: int) -> None:
        if not self.proc:
            return
        if self.proc.poll() is not None:
            self._set_status("启动失败", f"退出码 {self.proc.returncode}", RED)
            self._set_running(False)
            self._append_log("[启动器] 服务进程已退出，请查看上方日志。")
            return
        payload = _health(self.port)
        if payload:
            self._set_status("运行中", f"v{payload.get('version', '')} · {payload.get('mode', '')}", GREEN)
            self._append_log(f"[启动器] 服务就绪：http://127.0.0.1:{self.port}")
            if self.open_when_ready:
                self.open_browser()
            return
        if attempt >= 48:
            self._set_status("启动超时", "请查看日志或换一个端口", RED)
            self._append_log("[启动器] 等待健康检查超时。")
            return
        self.root.after(250, self._wait_health, attempt + 1)

    def stop(self) -> None:
        if self.external:
            self.external = False
            self._set_running(False)
            self._set_status("已断开", "外部实例仍在运行", FAINT)
            return
        proc = self.proc
        if not proc:
            self._set_running(False)
            self._set_status("已停止", "", FAINT)
            return
        self._set_status("正在停止…", "", AMBER)
        threading.Thread(target=self._stop_worker, args=(proc,), daemon=True).start()
        self.root.after(100, self._check_stopped)

    def _stop_worker(self, proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except OSError:
            pass
        finally:
            self.proc = None

    def _check_stopped(self) -> None:
        if self.proc is not None:
            self.root.after(100, self._check_stopped)
            return
        self._after_stop()

    def _after_stop(self) -> None:
        self._set_running(False)
        self._set_status("已停止", "可以再次启动", FAINT)
        self._append_log("[启动器] 服务已停止。")

    # ---------------------------------------------------------------- misc
    @staticmethod
    def url(host: str, port: int) -> str:
        return f"http://{host}:{port}"

    def open_browser(self) -> None:
        if not self.running:
            return
        webbrowser.open(f"http://127.0.0.1:{self.port}")

    def on_close(self) -> None:
        if self.running and not self.external:
            from tkinter import messagebox

            if not messagebox.askokcancel("退出知源", "关闭窗口会停止本地服务，确定退出吗？"):
                return
            proc = self.proc
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="知源本地启动器")
    parser.add_argument("--port", type=int, default=int(os.getenv("ZHIYUAN_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    if tk is None:
        print("当前 Python 未包含 tkinter，无法打开启动窗口。")
        print("请直接运行：python run.py")
        return 1

    root = tk.Tk()
    Launcher(root, preferred_port=args.port, open_browser=not args.no_browser)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
