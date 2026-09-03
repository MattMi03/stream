import customtkinter as ctk
from tkinter import messagebox
import threading
import time
import cv2
from PIL import Image, ImageTk
from flask import Flask, Response, send_from_directory, render_template_string
from flask_cors import CORS  # 新增 CORS
import logging
import warnings
import os
import json
import sys
import shutil
import subprocess

# ========== 强制 RTSP over TCP ==========
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

warnings.filterwarnings("ignore", category=UserWarning, module="customtkinter")
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

CONFIG_FILE = "camera_config.json"
HLS_TEMP_DIR = "hls_temp"  # 存放 HLS 切片的临时目录

def get_cert_files():
    cert_path = "cert.pem"
    key_path = "key.pem"
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path
    if hasattr(sys, '_MEIPASS'):
        bundled_cert = os.path.join(sys._MEIPASS, "cert.pem")
        bundled_key = os.path.join(sys._MEIPASS, "key.pem")
        if os.path.exists(bundled_cert) and os.path.exists(bundled_key):
            try:
                shutil.copy2(bundled_cert, cert_path)
                shutil.copy2(bundled_key, key_path)
                return cert_path, key_path
            except Exception as e:
                print(f"复制证书失败: {e}")
    return None, None

def get_ffmpeg_path():
    """获取打包或当前目录的 ffmpeg.exe 路径"""
    if os.path.exists("ffmpeg.exe"):
        return "ffmpeg.exe"
    if hasattr(sys, '_MEIPASS'):
        bundled = os.path.join(sys._MEIPASS, "ffmpeg.exe")
        if os.path.exists(bundled):
            return bundled
    return "ffmpeg"  # 依赖系统环境变量兜底

class MultiHttpStreamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("多路摄像头 音视频直播流服务 (HLS)")
        self.root.geometry("680x780")
        self.root.resizable(True, True)

        self.cameras = []
        self.camera_counter = 0
        self.is_running = False

        # 初始化 HLS 目录
        if os.path.exists(HLS_TEMP_DIR):
            shutil.rmtree(HLS_TEMP_DIR, ignore_errors=True)
        os.makedirs(HLS_TEMP_DIR, exist_ok=True)

        self.flask_app = Flask(__name__)
        # 启用全站 CORS 跨域
        CORS(self.flask_app) 
        
        self.flask_thread = None
        self.setup_flask_routes()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # ---------- UI ----------
        self.main_frame = ctk.CTkFrame(root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        control_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        control_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(control_frame, text="端口:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 5))
        self.port_entry = ctk.CTkEntry(control_frame, width=60)
        self.port_entry.pack(side="left", padx=(0, 15))
        self.port_entry.insert(0, "8080")

        self.use_https_var = ctk.BooleanVar(value=True)
        self.https_check = ctk.CTkCheckBox(control_frame, text="启用 HTTPS", variable=self.use_https_var,
                                           command=self.on_https_toggle)
        self.https_check.pack(side="left", padx=5)

        self.start_btn = ctk.CTkButton(control_frame, text="▶ 启动所有流", width=100, command=self.start_all)
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = ctk.CTkButton(control_frame, text="⏹ 停止所有流", width=100, fg_color="#F44336",
                                      hover_color="#D32F2F", state="disabled", command=self.stop_all)
        self.stop_btn.pack(side="left", padx=5)

        self.add_btn = ctk.CTkButton(control_frame, text="➕ 添加摄像头", width=110, command=self.add_camera)
        self.add_btn.pack(side="left", padx=5)

        self.save_btn = ctk.CTkButton(control_frame, text="💾 保存", width=60, command=self.save_config,
                                      fg_color="#2e7d32", hover_color="#1b5e20")
        self.save_btn.pack(side="right", padx=5)

        self.scrollable_frame = ctk.CTkScrollableFrame(self.main_frame, label_text="摄像头列表 (带声音输出)")
        self.scrollable_frame.pack(fill="both", expand=True, pady=10)

        self.status_var = ctk.StringVar(value="状态: 准备就绪")
        self.status_label = ctk.CTkLabel(self.main_frame, textvariable=self.status_var, font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", pady=(5, 0))

        self.hint_var = ctk.StringVar(value="提示：启用 HTTPS 时首次访问若有警告，请点击“高级”->“继续访问”")
        self.hint_label = ctk.CTkLabel(self.main_frame, textvariable=self.hint_var, font=ctk.CTkFont(size=10),
                                       text_color="orange")
        self.hint_label.pack(anchor="w", pady=(5, 0))

        self.load_config()

    def on_https_toggle(self):
        self.update_camera_url_display()

    # ---------- Flask 路由 (服务 HLS 和 播放器) ----------
    def setup_flask_routes(self):
        # 服务 M3U8 和 TS 切片文件
        @self.flask_app.route('/stream/<int:camera_id>/<path:filename>')
        def serve_hls(camera_id, filename):
            cam_dir = os.path.join(HLS_TEMP_DIR, str(camera_id))
            response = send_from_directory(cam_dir, filename)
            # 防止浏览器缓存 M3U8 文件
            if filename.endswith(".m3u8"):
                response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response.headers['Pragma'] = 'no-cache'
                response.headers['Expires'] = '0'
            return response

        # 提供一个内置的 Web 播放器页面，方便直接测试
        @self.flask_app.route('/player/<int:camera_id>')
        def video_player(camera_id):
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Camera {{ camera_id }} 播放器</title>
                <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
                <style>body { background: #121212; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }</style>
            </head>
            <body>
                <video id="video" controls autoplay muted style="width: 80%; max-width: 1200px; border: 2px solid #333;"></video>
                <script>
                  var video = document.getElementById('video');
                  var videoSrc = '/stream/{{ camera_id }}/index.m3u8';
                  if (Hls.isSupported()) {
                    var hls = new Hls();
                    hls.loadSource(videoSrc);
                    hls.attachMedia(video);
                    hls.on(Hls.Events.MANIFEST_PARSED, function() { video.play(); });
                  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                    video.src = videoSrc;
                    video.addEventListener('loadedmetadata', function() { video.play(); });
                  }
                </script>
            </body>
            </html>
            """
            return render_template_string(html, camera_id=camera_id)

    # ---------- 配置持久化 ----------
    def save_config(self):
        config = {
            "port": self.port_entry.get().strip(),
            "use_https": self.use_https_var.get(),
            "cameras": [cam['url_entry'].get().strip() for cam in self.cameras if cam['url_entry'].get().strip()]
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            self.status_var.set("状态: 配置已保存")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            self.add_camera()
            return
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            if 'port' in config and config['port'].isdigit():
                self.port_entry.delete(0, ctk.END)
                self.port_entry.insert(0, config['port'])
            if 'use_https' in config:
                self.use_https_var.set(config['use_https'])
                self.on_https_toggle()
            urls = config.get('cameras', [])
            if urls:
                for cam in self.cameras[:]:
                    if cam['card']:
                        cam['card'].destroy()
                    self.cameras.remove(cam)
                self.camera_counter = 0
                for url in urls:
                    self.add_camera_with_url(url)
            else:
                self.add_camera()
        except Exception as e:
            self.add_camera()

    # ---------- 摄像头管理 ----------
    def add_camera_with_url(self, url):
        cam_id = self.camera_counter
        self.camera_counter += 1

        card = ctk.CTkFrame(self.scrollable_frame, corner_radius=8, fg_color=("gray90", "gray20"))
        card.pack(fill="x", pady=5, padx=5, ipady=5)

        url_entry = ctk.CTkEntry(card, placeholder_text="输入 RTSP 地址")
        url_entry.pack(fill="x", padx=10, pady=(5, 0))
        url_entry.insert(0, url)

        preview_frame = ctk.CTkFrame(card, fg_color="transparent")
        preview_frame.pack(fill="x", padx=10, pady=5)

        preview_label = ctk.CTkLabel(preview_frame, text=f"摄像头 {cam_id+1} 预览", width=200, height=120,
                                     corner_radius=6)
        preview_label.pack(side="left", padx=(0, 10))
        preview_label.configure(fg_color=("gray75", "gray25"))

        btn_frame = ctk.CTkFrame(preview_frame, fg_color="transparent")
        btn_frame.pack(side="right", fill="y")
        del_btn = ctk.CTkButton(btn_frame, text="删除", width=60, fg_color="#F44336", hover_color="#D32F2F",
                                command=lambda c=card, id=cam_id: self.remove_camera(c, id))
        del_btn.pack(pady=2)

        # 增加流地址和播放器地址的显示
        url_display = ctk.CTkEntry(card, width=400, state="readonly", font=ctk.CTkFont(size=10))
        url_display.pack(padx=10, pady=(0, 2), fill="x")
        url_display.insert(0, "M3U8 直播地址 (用于前端调用)")

        player_display = ctk.CTkEntry(card, width=400, state="readonly", font=ctk.CTkFont(size=10))
        player_display.pack(padx=10, pady=(0, 5), fill="x")
        player_display.insert(0, "内置网页播放器地址 (复制到浏览器)")

        cam_data = {
            'camera_id': cam_id,
            'url_entry': url_entry,
            'preview_label': preview_label,
            'url_display': url_display,
            'player_display': player_display,
            'card': card,
            'running': False,
            'cap': None,
            'thread': None,
            'ffmpeg_proc': None, # 新增 ffmpeg 进程对象
            '_img': None,
        }
        self.cameras.append(cam_data)

        if self.is_running:
            self._start_single_camera(cam_data)
            self.update_camera_url_display(cam_data)

    def add_camera(self):
        self.add_camera_with_url("rtsp://admin:password@192.168.1.100:554/stream")
        self.save_config()

    def remove_camera(self, card, cam_id):
        for cam in self.cameras:
            if cam['camera_id'] == cam_id:
                self._stop_single_camera(cam)
                break
        self.cameras = [cam for cam in self.cameras if cam['camera_id'] != cam_id]
        card.destroy()
        if not self.cameras and self.is_running:
            self.stop_all()
        self.save_config()

    def update_camera_url_display(self, cam_data=None):
        port = self.port_entry.get().strip()
        if not port.isdigit():
            return
        protocol = "https" if self.use_https_var.get() else "http"
        base_url = f"{protocol}://127.0.0.1:{port}"
        
        target_cams = [cam_data] if cam_data else self.cameras
        for cam in target_cams:
            m3u8_url = f"{base_url}/stream/{cam['camera_id']}/index.m3u8"
            player_url = f"{base_url}/player/{cam['camera_id']}"
            
            cam['url_display'].configure(state="normal")
            cam['url_display'].delete(0, ctk.END)
            cam['url_display'].insert(0, f"HLS流: {m3u8_url}")
            cam['url_display'].configure(state="readonly")

            cam['player_display'].configure(state="normal")
            cam['player_display'].delete(0, ctk.END)
            cam['player_display'].insert(0, f"播放器: {player_url}")
            cam['player_display'].configure(state="readonly")

    # ---------- FFmpeg HLS 后台转码 ----------
    def _start_ffmpeg_hls(self, cam_data):
        url = cam_data['url_entry'].get().strip()
        cam_dir = os.path.join(HLS_TEMP_DIR, str(cam_data['camera_id']))
        os.makedirs(cam_dir, exist_ok=True)
        m3u8_path = os.path.join(cam_dir, "index.m3u8")

        # FFmpeg 核心命令：视频原画拷贝 (节约CPU)，音频强制转 AAC (浏览器兼容)
        ffmpeg_cmd = [
            get_ffmpeg_path(),
            "-y",
            "-rtsp_transport", "tcp",
            "-i", url,
            "-c:v", "copy",
            "-c:a", "aac", 
            "-b:a", "128k",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "3",
            "-hls_flags", "delete_segments",
            m3u8_path
        ]
        try:
            cam_data['ffmpeg_proc'] = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"启动 FFmpeg 失败: {e}")

    # ---------- 仅用于 UI 的轻量预览线程 ----------
    def _ui_preview_loop(self, cam_data):
        url = cam_data['url_entry'].get().strip()
        if not url: return

        while cam_data['running']:
            try:
                cap = cv2.VideoCapture(url)
                if url.lower().startswith('rtsp://'):
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cam_data['cap'] = cap

                if not cap.isOpened():
                    cap.release()
                    cam_data['cap'] = None
                    self.root.after(0, lambda lbl=cam_data['preview_label']: lbl.configure(image=None, text="连接失败..."))
                    time.sleep(2)
                    continue

                self.root.after(0, lambda lbl=cam_data['preview_label']: lbl.configure(image=None, text=""))

                # 预览抽帧 (无需太快，1秒 5帧即可节省 UI 性能)
                frame_interval = 1.0 / 5
                fail_count = 0

                while cam_data['running'] and cap.isOpened():
                    start = time.time()
                    ret, frame = cap.read()
                    if not ret:
                        fail_count += 1
                        if fail_count >= 5: break
                        time.sleep(0.5)
                        continue
                    fail_count = 0

                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame_rgb)
                    img.thumbnail((200, 120), Image.Resampling.LANCZOS)
                    cam_data['_img'] = ImageTk.PhotoImage(img)
                    
                    label = cam_data['preview_label']
                    self.root.after(0, lambda lbl=label, cd=cam_data: lbl.configure(image=cd['_img'], text=""))

                    elapsed = time.time() - start
                    if elapsed < frame_interval:
                        time.sleep(frame_interval - elapsed)

                if cam_data['running']:
                    if cap: cap.release()
                    cam_data['cap'] = None
                    time.sleep(1)
                    continue

            except Exception:
                time.sleep(2)

        if cam_data['cap']:
            cam_data['cap'].release()
            cam_data['cap'] = None
        self.root.after(0, lambda lbl=cam_data['preview_label']: lbl.configure(image=None, text="已停止"))

    # ---------- 启动/停止单路 ----------
    def _start_single_camera(self, cam_data):
        if not cam_data['running']:
            cam_data['running'] = True
            
            # 1. 启动 FFmpeg 转码服务给前端
            self._start_ffmpeg_hls(cam_data)
            
            # 2. 启动 OpenCV 抽取给 UI 预览
            thread = threading.Thread(target=self._ui_preview_loop, args=(cam_data,), daemon=True)
            cam_data['thread'] = thread
            thread.start()

    def _stop_single_camera(self, cam_data):
        cam_data['running'] = False
        if cam_data['cap']:
            cam_data['cap'].release()
            cam_data['cap'] = None
        cam_data['_img'] = None
        
        # 杀死 ffmpeg 进程
        if cam_data.get('ffmpeg_proc'):
            try:
                cam_data['ffmpeg_proc'].terminate()
                cam_data['ffmpeg_proc'].wait(timeout=2)
            except Exception:
                pass
            cam_data['ffmpeg_proc'] = None

    # ---------- 全局启动/停止 ----------
    def start_all(self):
        port = self.port_entry.get().strip()
        if not port.isdigit():
            messagebox.showwarning("提示", "端口必须是数字")
            return
        port = int(port)

        ssl_context = None
        if self.use_https_var.get():
            cert, key = get_cert_files()
            if cert and key:
                ssl_context = (cert, key)
            else:
                self.use_https_var.set(False)
                self.on_https_toggle()
                messagebox.showwarning("证书缺失", "未找到证书文件，将使用 HTTP 模式")

        for cam in self.cameras:
            self._start_single_camera(cam)

        if self.flask_thread is None or not self.flask_thread.is_alive():
            self.flask_thread = threading.Thread(target=self._start_flask_server,
                                                 args=(port, ssl_context), daemon=True)
            self.flask_thread.start()
            time.sleep(0.5)

        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        protocol = "HTTPS" if self.use_https_var.get() else "HTTP"
        self.status_var.set(f"状态: 已启动 {protocol}，端口 {port}，共 {len(self.cameras)} 路流")
        self.update_camera_url_display()
        self.save_config()

    def _start_flask_server(self, port, ssl_context):
        try:
            self.flask_app.run(host='0.0.0.0', port=port, debug=False,
                               use_reloader=False, ssl_context=ssl_context)
        except Exception as e:
            print(f"Flask 异常: {e}")

    def stop_all(self):
        for cam in self.cameras:
            self._stop_single_camera(cam)
        self.is_running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_var.set("状态: 已停止所有流")

    def on_closing(self):
        self.save_config()
        self.stop_all()
        # 清理临时切片
        if os.path.exists(HLS_TEMP_DIR):
            shutil.rmtree(HLS_TEMP_DIR, ignore_errors=True)
        self.root.destroy()

if __name__ == "__main__":
    root = ctk.CTk()
    app = MultiHttpStreamerApp(root)
    root.mainloop()
