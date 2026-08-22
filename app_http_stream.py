import customtkinter as ctk
from tkinter import messagebox
import threading
import time
import cv2
from PIL import Image, ImageTk
from flask import Flask, Response
import logging
import warnings

# 屏蔽警告
warnings.filterwarnings("ignore", category=UserWarning, module="customtkinter")
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

class HttpStreamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("摄像头 HTTP 直播流转发服务 (纯净版)")
        self.root.geometry("540x500")
        self.root.resizable(False, False)

        self.preview_running = False
        self.cap = None
        self.latest_frame_bytes = None

        # Flask 服务
        self.flask_app = Flask(__name__)
        self.flask_thread = None
        self.setup_flask_routes()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # UI 布局
        self.main_frame = ctk.CTkFrame(root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        ctk.CTkLabel(self.main_frame, text="摄像头流媒体地址", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0,2))
        self.url_entry = ctk.CTkEntry(self.main_frame, width=500)
        self.url_entry.pack(fill="x", pady=(0,10))
        self.url_entry.insert(0, "rtsp://admin:password@192.168.1.100:554/stream") 

        ctk.CTkLabel(self.main_frame, text="HTTP 直播转发端口", font=ctk.CTkFont(size=12, weight="bold"), text_color="#1976d2").pack(anchor="w", pady=(0,2))
        port_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        port_frame.pack(fill="x", pady=(0,10))
        self.port_entry = ctk.CTkEntry(port_frame, width=150)
        self.port_entry.pack(side="left")
        self.port_entry.insert(0, "8080")
        
        self.http_url_var = ctk.StringVar(value="外部访问地址: 未启动")
        self.http_url_label = ctk.CTkLabel(port_frame, textvariable=self.http_url_var, font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        self.http_url_label.pack(side="left", padx=15)

        ctk.CTkLabel(self.main_frame, text="实时预览", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(5,2))
        self.preview_label = ctk.CTkLabel(self.main_frame, text="等待开始...", width=480, height=270, corner_radius=10)
        self.preview_label.pack(pady=(0,10))
        self.preview_label.configure(fg_color=("gray75", "gray25")) 

        btn_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        btn_frame.pack(pady=10)
        self.start_btn = ctk.CTkButton(btn_frame, text="▶ 启动转发服务", width=140, command=self.start_stream)
        self.start_btn.pack(side="left", padx=20)
        self.stop_btn = ctk.CTkButton(btn_frame, text="⏹ 停止运行", width=140, fg_color="#F44336", hover_color="#D32F2F", state="disabled", command=self.stop_stream)
        self.stop_btn.pack(side="right", padx=20)

    def setup_flask_routes(self):
        @self.flask_app.route('/stream')
        def video_feed():
            def generate_frames():
                while True:
                    if self.latest_frame_bytes is not None and self.preview_running:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + self.latest_frame_bytes + b'\r\n')
                    else:
                        time.sleep(0.1)
            return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    def start_flask_server(self, port):
        try:
            self.flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
        except Exception as e:
            print(f"Flask 异常: {e}")

    def _preview_loop(self, url):
        self.preview_running = True
        try:
            self.cap = cv2.VideoCapture(url)
            if url.lower().startswith('rtsp://'):
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            frame_interval = 1.0 / 15 # 15 fps
            while self.preview_running and self.cap is not None and self.cap.isOpened():
                start = time.time()
                ret, frame = self.cap.read()
                if not ret:
                    time.sleep(1)
                    continue

                # 编码给 Flask 使用
                ret_encode, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ret_encode:
                    self.latest_frame_bytes = buffer.tobytes()

                # UI 预览
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                img.thumbnail((480, 270), Image.Resampling.LANCZOS)
                imgtk = ImageTk.PhotoImage(img)
                self.root.after(0, lambda img=imgtk: self.preview_label.configure(image=img, text=""))
                self.root.after(0, lambda: setattr(self, '_preview_img', imgtk))

                elapsed = time.time() - start
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
        except Exception:
            pass
        finally:
            if self.cap:
                self.cap.release()
                self.cap = None
            self.preview_running = False
            self.root.after(0, lambda: self.preview_label.configure(image=None, text="已停止"))

    def start_stream(self):
        url = self.url_entry.get().strip()
        try:
            port = int(self.port_entry.get().strip())
        except ValueError:
            messagebox.showwarning("提示", "端口必须是纯数字！")
            return
        
        if not url:
            return

        if not self.preview_running:
            threading.Thread(target=self._preview_loop, args=(url,), daemon=True).start()

        if self.flask_thread is None or not self.flask_thread.is_alive():
            self.flask_thread = threading.Thread(target=self.start_flask_server, args=(port,), daemon=True)
            self.flask_thread.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.http_url_var.set(f"外部访问地址: http://本机IP:{port}/stream")
        self.http_url_label.configure(text_color="#ed6c02")

    def stop_stream(self):
        self.preview_running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.http_url_var.set("外部访问地址: 未启动")
        self.http_url_label.configure(text_color="gray")

    def on_closing(self):
        self.preview_running = False
        self.root.destroy()

if __name__ == "__main__":
    root = ctk.CTk()
    app = HttpStreamerApp(root)
    root.mainloop()