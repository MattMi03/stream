import customtkinter as ctk
from tkinter import filedialog, messagebox
import subprocess
import threading
import sys
import os
import time
import cv2
from PIL import Image, ImageTk

# -------- customtkinter 全局设置 --------
ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

def get_ffmpeg_path():
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'ffmpeg.exe')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg.exe')

class StreamRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("流媒体录像工具 - 带预览")
        self.root.geometry("540x580")  # 高度增加，容纳预览框
        self.root.resizable(False, False)

        self.process = None
        self.is_recording = False
        self.start_time = 0
        self.timer_id = None
        self.preview_running = False
        self.cap = None
        self.preview_thread = None

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # ----- 主容器 -----
        self.main_frame = ctk.CTkFrame(root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # ---- 地址 ----
        ctk.CTkLabel(self.main_frame, text="摄像头/直播流地址", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0,5))
        self.url_entry = ctk.CTkEntry(self.main_frame, width=500, placeholder_text="输入 RTSP/RTMP/HTTP 地址")
        self.url_entry.pack(fill="x", pady=(0,10))
        self.url_entry.insert(0, "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8")

        # ---- 保存路径 ----
        ctk.CTkLabel(self.main_frame, text="本地保存路径", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0,5))
        path_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        path_frame.pack(fill="x", pady=(0,10))
        self.file_entry = ctk.CTkEntry(path_frame, width=400)
        self.file_entry.pack(side="left", fill="x", expand=True)
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop", "监控录像_01.mp4")
        self.file_entry.insert(0, desktop_path)
        ctk.CTkButton(path_frame, text="浏览", width=80, command=self.browse_file).pack(side="right", padx=(5,0))

        # ---- 预览区域 ----
        ctk.CTkLabel(self.main_frame, text="实时预览", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(5,5))
        self.preview_label = ctk.CTkLabel(self.main_frame, text="等待开始录制...", width=480, height=270, corner_radius=10)
        self.preview_label.pack(pady=(0,10))
        self.preview_label.configure(fg_color=("gray75", "gray25"))  # 浅色/深色适配

        # ---- 状态栏 ----
        self.status_var = ctk.StringVar(value="状态: 准备就绪")
        self.status_label = ctk.CTkLabel(self.main_frame, textvariable=self.status_var, font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", pady=(0,10))

        # ---- 按钮行 ----
        btn_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        btn_frame.pack(pady=5)
        self.start_btn = ctk.CTkButton(btn_frame, text="▶ 开始录制", width=140, command=self.start_recording)
        self.start_btn.pack(side="left", padx=20)
        self.stop_btn = ctk.CTkButton(btn_frame, text="⏹ 停止录制", width=140, state="disabled", command=self.stop_recording)
        self.stop_btn.pack(side="right", padx=20)

    def browse_file(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4 视频", "*.mp4"), ("所有文件", "*.*")],
            initialfile="监控录像_01.mp4",
            title="选择保存位置"
        )
        if filename:
            self.file_entry.delete(0, ctk.END)
            self.file_entry.insert(0, filename)

    def update_timer(self):
        if self.is_recording:
            elapsed = int(time.time() - self.start_time)
            mins, secs = divmod(elapsed, 60)
            hours, mins = divmod(mins, 60)
            self.status_var.set(f"状态: 正在录制... [{hours:02d}:{mins:02d}:{secs:02d}]")
            self.status_label.configure(text_color="#2e7d32")
            self.timer_id = self.root.after(1000, self.update_timer)

    # ---------- 预览线程 ----------
    def _preview_loop(self, url):
        """在独立线程中读取视频流，更新预览标签"""
        self.preview_running = True
        try:
            self.cap = cv2.VideoCapture(url)
            # 针对 RTSP 可能需要的设置
            if url.lower().startswith('rtsp://'):
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 减少延迟
            # 针对 HTTP 可能需要的 user-agent? 这里不做复杂处理，默认即可

            if not self.cap.isOpened():
                self.root.after(0, lambda: self.preview_label.configure(text="无法打开视频流"))
                return

            # 降低帧率到 10fps，减少资源消耗
            target_fps = 10
            frame_interval = 1.0 / target_fps

            while self.preview_running and self.cap is not None and self.cap.isOpened():
                start = time.time()
                ret, frame = self.cap.read()
                if not ret:
                    # 如果读取失败，尝试重连（简单重试一次）
                    time.sleep(1)
                    continue

                # 转换为 PIL Image 并缩放到预览框大小（保持宽高比）
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                # 预览框尺寸（label实际大小）
                label_width = self.preview_label.winfo_width() if self.preview_label.winfo_width() > 1 else 480
                label_height = self.preview_label.winfo_height() if self.preview_label.winfo_height() > 1 else 270
                img.thumbnail((label_width, label_height), Image.Resampling.LANCZOS)
                imgtk = ImageTk.PhotoImage(img)

                # 更新标签（必须在主线程）
                self.root.after(0, lambda img=imgtk: self.preview_label.configure(image=img, text=""))
                # 保持引用防止被垃圾回收
                self.root.after(0, lambda: setattr(self, '_preview_img', imgtk))

                # 控制帧率
                elapsed = time.time() - start
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)

        except Exception as e:
            print(f"预览线程异常: {e}")
        finally:
            if self.cap is not None:
                self.cap.release()
                self.cap = None
            self.preview_running = False
            # 清除预览图
            self.root.after(0, lambda: self.preview_label.configure(image=None, text="预览已停止"))

    # ---------- 录制逻辑（几乎不变，增加预览启动/停止） ----------
    def start_recording(self):
        url = self.url_entry.get().strip()
        out_file = self.file_entry.get().strip()

        if not url or not out_file:
            messagebox.showwarning("提示", "流媒体地址和保存路径不能为空！")
            return

        out_dir = os.path.dirname(out_file)
        if out_dir and not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir)
            except Exception:
                messagebox.showerror("错误", "无法创建保存文件夹，请检查路径权限！")
                return

        ffmpeg_path = get_ffmpeg_path()
        if not os.path.exists(ffmpeg_path):
            messagebox.showerror("环境缺失", f"找不到核心组件！\n请确保在此目录下有 ffmpeg.exe:\n{ffmpeg_path}")
            return

        # 启动预览线程（独立于录制）
        if not self.preview_running:
            self.preview_thread = threading.Thread(target=self._preview_loop, args=(url,), daemon=True)
            self.preview_thread.start()

        self.is_recording = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self.url_entry.configure(state="readonly")
        self.file_entry.configure(state="readonly")

        self.status_var.set("状态: 正在连接视频流，请稍候...")
        self.status_label.configure(text_color="#ed6c02")

        # 启动录制线程（与之前相同）
        threading.Thread(target=self._record_thread, args=(ffmpeg_path, url, out_file), daemon=True).start()

    def _record_thread(self, ffmpeg_path, url, out_file):
        command = [ffmpeg_path, '-y']
        if url.lower().startswith('rtsp://'):
            command.extend(['-rtsp_transport', 'tcp'])
        elif url.lower().startswith('http'):
            command.extend([
                '-user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
                '-tls_verify', '0'
            ])
        command.extend(['-i', url, '-c', 'copy', out_file])

        out_dir = os.path.dirname(out_file) or os.getcwd()
        log_file_path = os.path.join(out_dir, "ffmpeg_debug.log")

        try:
            with open(log_file_path, "w", encoding="utf-8") as log_file:
                log_file.write(f"=== FFmpeg 调试日志 ===\n执行命令: {' '.join(command)}\n\n")
                log_file.flush()

                kwargs = {}
                if os.name == 'nt':
                    kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    **kwargs
                )

                try:
                    self.process.wait(timeout=4.0)
                    self.is_recording = False
                    self.root.after(0, lambda: self._connection_failed(log_file_path))
                    return
                except subprocess.TimeoutExpired:
                    self.root.after(0, self._connection_success)

                self.process.wait()

        except Exception as e:
            print(f"进程异常: {e}")

        self.is_recording = False
        self.root.after(0, self._reset_ui)

    def _connection_failed(self, log_path=""):
        messagebox.showerror("连接失败", f"无法连接到该视频流！\n\n已生成详细诊断日志：\n{log_path}")
        self._reset_ui()

    def _connection_success(self):
        if not self.is_recording:
            return
        self.stop_btn.configure(state="normal")
        self.start_time = time.time()
        self.update_timer()

    def stop_recording(self):
        if self.process and self.is_recording:
            if self.timer_id:
                self.root.after_cancel(self.timer_id)
                self.timer_id = None

            self.status_var.set("状态: 正在保存视频信息并安全退出...")
            self.status_label.configure(text_color="#ed6c02")
            self.stop_btn.configure(state="disabled")

            # 停止预览
            self._stop_preview()

            try:
                self.process.stdin.write(b'q\n')
                self.process.stdin.flush()
            except Exception:
                self.process.terminate()

    def _stop_preview(self):
        self.preview_running = False
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        # 清空预览图像
        self.root.after(0, lambda: self.preview_label.configure(image=None, text="预览已停止"))

    def _reset_ui(self):
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None

        self.status_var.set("状态: 录制已结束或连接已断开")
        self.status_label.configure(text_color="#1976d2")

        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.url_entry.configure(state="normal")
        self.file_entry.configure(state="normal")

        # 停止预览（如果还在运行）
        if self.preview_running:
            self._stop_preview()

    def on_closing(self):
        if self.is_recording:
            if messagebox.askokcancel("确认退出", "当前正在录像，直接退出会导致录像停止。\n确定要退出吗？"):
                self._stop_preview()
                self.stop_recording()
                self.root.after(1500, self.root.destroy)
        else:
            self._stop_preview()
            self.root.destroy()


if __name__ == "__main__":
    root = ctk.CTk()
    app = StreamRecorderApp(root)
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.mainloop()