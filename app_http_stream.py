import customtkinter as ctk
from tkinter import messagebox
import threading
import time
import cv2
from PIL import Image, ImageTk
from flask import Flask, Response
import logging
import warnings
import os
import json

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

warnings.filterwarnings("ignore", category=UserWarning, module="customtkinter")
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

CONFIG_FILE = "camera_config.json"


class MultiHttpStreamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("多路摄像头 HTTP 直播流转发服务（配置持久化）")
        self.root.geometry("640x750")
        self.root.resizable(True, True)

        self.cameras = []
        self.camera_counter = 0
        self.is_running = False

        self.flask_app = Flask(__name__)
        self.flask_thread = None
        self.setup_flask_routes()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # ---------- UI ----------
        self.main_frame = ctk.CTkFrame(root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        control_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        control_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(control_frame, text="HTTP 端口:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0,5))
        self.port_entry = ctk.CTkEntry(control_frame, width=80)
        self.port_entry.pack(side="left", padx=(0, 15))
        self.port_entry.insert(0, "8080")

        self.start_btn = ctk.CTkButton(control_frame, text="▶ 启动所有流", width=100, command=self.start_all)
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = ctk.CTkButton(control_frame, text="⏹ 停止所有流", width=100, fg_color="#F44336",
                                      hover_color="#D32F2F", state="disabled", command=self.stop_all)
        self.stop_btn.pack(side="left", padx=5)

        self.add_btn = ctk.CTkButton(control_frame, text="➕ 添加摄像头", width=120, command=self.add_camera)
        self.add_btn.pack(side="left", padx=5)

        self.save_btn = ctk.CTkButton(control_frame, text="💾 保存配置", width=100, command=self.save_config, fg_color="#2e7d32", hover_color="#1b5e20")
        self.save_btn.pack(side="right", padx=5)

        self.scrollable_frame = ctk.CTkScrollableFrame(self.main_frame, label_text="摄像头列表")
        self.scrollable_frame.pack(fill="both", expand=True, pady=10)

        self.status_var = ctk.StringVar(value="状态: 准备就绪")
        self.status_label = ctk.CTkLabel(self.main_frame, textvariable=self.status_var, font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", pady=(5, 0))

        # 加载配置文件
        self.load_config()

    # ---------- Flask 路由 ----------
    def setup_flask_routes(self):
        @self.flask_app.route('/stream/<int:camera_id>')
        def video_feed(camera_id):
            def generate_frames():
                while True:
                    frame_bytes = None
                    for cam in self.cameras:
                        if cam['camera_id'] == camera_id and cam['running']:
                            frame_bytes = cam['frame_bytes']
                            break
                    if frame_bytes is not None:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                    else:
                        time.sleep(0.1)
            return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    # ---------- 配置持久化 ----------
    def save_config(self):
        """保存当前摄像头地址列表和端口到 JSON 文件"""
        config = {
            "port": self.port_entry.get().strip(),
            "cameras": [cam['url_entry'].get().strip() for cam in self.cameras if cam['url_entry'].get().strip()]
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            self.status_var.set("状态: 配置已保存")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def load_config(self):
        """加载配置文件，恢复摄像头列表和端口"""
        if not os.path.exists(CONFIG_FILE):
            # 默认添加一个示例摄像头
            self.add_camera()
            return

        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            # 恢复端口
            if 'port' in config and config['port'].isdigit():
                self.port_entry.delete(0, ctk.END)
                self.port_entry.insert(0, config['port'])

            # 恢复摄像头列表
            urls = config.get('cameras', [])
            if urls:
                # 先移除默认添加的摄像头（如果有）
                for cam in self.cameras[:]:
                    if cam['card']:  # 销毁卡片
                        cam['card'].destroy()
                    self.cameras.remove(cam)
                self.camera_counter = 0
                for url in urls:
                    self.add_camera_with_url(url)
            else:
                # 如果没有摄像头，添加一个默认示例
                self.add_camera()
            self.status_var.set("状态: 配置已加载")
        except Exception as e:
            messagebox.showerror("加载配置失败", str(e))
            self.add_camera()  # 出错则添加默认示例

    def add_camera_with_url(self, url):
        """内部方法：直接添加带指定 URL 的摄像头（不保存配置）"""
        cam_id = self.camera_counter
        self.camera_counter += 1

        card = ctk.CTkFrame(self.scrollable_frame, corner_radius=8, fg_color=("gray90", "gray20"))
        card.pack(fill="x", pady=5, padx=5, ipady=5)

        url_entry = ctk.CTkEntry(card, placeholder_text="输入 RTSP 地址")
        url_entry.pack(fill="x", padx=10, pady=(5, 0))
        url_entry.insert(0, url)

        preview_frame = ctk.CTkFrame(card, fg_color="transparent")
        preview_frame.pack(fill="x", padx=10, pady=5)

        preview_label = ctk.CTkLabel(preview_frame, text=f"摄像头 {cam_id+1} 预览", width=200, height=120, corner_radius=6)
        preview_label.pack(side="left", padx=(0, 10))
        preview_label.configure(fg_color=("gray75", "gray25"))

        btn_frame = ctk.CTkFrame(preview_frame, fg_color="transparent")
        btn_frame.pack(side="right", fill="y")
        del_btn = ctk.CTkButton(btn_frame, text="删除", width=60, fg_color="#F44336", hover_color="#D32F2F",
                                command=lambda c=card, id=cam_id: self.remove_camera(c, id))
        del_btn.pack(pady=2)

        url_display = ctk.CTkEntry(card, width=300, state="readonly", font=ctk.CTkFont(size=10))
        url_display.pack(padx=10, pady=(0,5))
        url_display.insert(0, "启动服务后显示访问地址")

        cam_data = {
            'camera_id': cam_id,
            'url_entry': url_entry,
            'preview_label': preview_label,
            'url_display': url_display,
            'card': card,
            'running': False,
            'cap': None,
            'frame_bytes': None,
            'thread': None,
            '_img': None,
        }
        self.cameras.append(cam_data)

        # 如果服务已运行，自动启动此路
        if self.is_running:
            self._start_single_camera(cam_data)
            self.update_camera_url_display(cam_data)

    # ---------- 添加/删除摄像头 ----------
    def add_camera(self):
        """添加摄像头（UI 操作），并自动保存配置"""
        self.add_camera_with_url("rtsp://admin:password@192.168.1.100:554/stream")
        self.save_config()  # 保存配置

    def remove_camera(self, card, cam_id):
        for cam in self.cameras:
            if cam['camera_id'] == cam_id:
                cam['running'] = False
                if cam['cap']:
                    cam['cap'].release()
                cam['_img'] = None
                break
        self.cameras = [cam for cam in self.cameras if cam['camera_id'] != cam_id]
        card.destroy()
        if not self.cameras and self.is_running:
            self.stop_all()
        self.save_config()  # 保存配置

    # ---------- 更新 URL 显示 ----------
    def update_camera_url_display(self, cam_data=None):
        port = self.port_entry.get().strip()
        if not port.isdigit():
            return
        base_url = f"http://localhost:{port}/stream/"
        if cam_data:
            url = base_url + str(cam_data['camera_id'])
            cam_data['url_display'].configure(state="normal")
            cam_data['url_display'].delete(0, ctk.END)
            cam_data['url_display'].insert(0, url)
            cam_data['url_display'].configure(state="readonly")
        else:
            for cam in self.cameras:
                url = base_url + str(cam['camera_id'])
                cam['url_display'].configure(state="normal")
                cam['url_display'].delete(0, ctk.END)
                cam['url_display'].insert(0, url)
                cam['url_display'].configure(state="readonly")

    # ---------- 核心预览线程（带自动重连） ----------
    def _preview_loop(self, cam_data):
        url = cam_data['url_entry'].get().strip()
        if not url:
            cam_data['running'] = False
            return

        while cam_data['running']:
            try:
                cap = cv2.VideoCapture(url)
                if url.lower().startswith('rtsp://'):
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cam_data['cap'] = cap

                if not cap.isOpened():
                    cap.release()
                    cam_data['cap'] = None
                    self.root.after(0, lambda lbl=cam_data['preview_label']: lbl.configure(image=None, text="连接失败，重试中..."))
                    time.sleep(2)
                    continue

                self.root.after(0, lambda lbl=cam_data['preview_label']: lbl.configure(image=None, text="预览中..."))

                frame_interval = 1.0 / 8
                fail_count = 0
                max_fail = 5

                while cam_data['running'] and cap.isOpened():
                    start = time.time()
                    ret, frame = cap.read()
                    if not ret:
                        fail_count += 1
                        if fail_count >= max_fail:
                            print(f"摄像头 {cam_data['camera_id']} 读取失败，触发重连")
                            break
                        time.sleep(0.5)
                        continue
                    else:
                        fail_count = 0

                    ret_encode, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    if ret_encode:
                        cam_data['frame_bytes'] = buffer.tobytes()

                    # 更新预览
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame_rgb)
                    img.thumbnail((200, 120), Image.Resampling.LANCZOS)
                    imgtk = ImageTk.PhotoImage(img)
                    cam_data['_img'] = imgtk
                    label = cam_data['preview_label']
                    self.root.after(0, lambda lbl=label, cd=cam_data: lbl.configure(image=cd['_img'], text=""))

                    elapsed = time.time() - start
                    if elapsed < frame_interval:
                        time.sleep(frame_interval - elapsed)

                if cam_data['running']:
                    print(f"摄像头 {cam_data['camera_id']} 断开，正在重连...")
                    self.root.after(0, lambda lbl=cam_data['preview_label']: lbl.configure(image=None, text="重连中..."))
                    if cap:
                        cap.release()
                        cam_data['cap'] = None
                    time.sleep(1)
                    continue
                else:
                    break

            except Exception as e:
                print(f"摄像头 {cam_data['camera_id']} 异常: {e}")
                time.sleep(2)
                continue

        if cam_data['cap']:
            cam_data['cap'].release()
            cam_data['cap'] = None
        cam_data['running'] = False
        cam_data['frame_bytes'] = None
        cam_data['_img'] = None
        self.root.after(0, lambda lbl=cam_data['preview_label']: lbl.configure(image=None, text="已停止"))

    # ---------- 启动/停止单个摄像头 ----------
    def _start_single_camera(self, cam_data):
        if not cam_data['running']:
            cam_data['running'] = True
            thread = threading.Thread(target=self._preview_loop, args=(cam_data,), daemon=True)
            cam_data['thread'] = thread
            thread.start()

    def _stop_single_camera(self, cam_data):
        cam_data['running'] = False
        if cam_data['cap']:
            cam_data['cap'].release()
            cam_data['cap'] = None
        cam_data['_img'] = None

    # ---------- 启动所有流 ----------
    def start_all(self):
        port = self.port_entry.get().strip()
        if not port.isdigit():
            messagebox.showwarning("提示", "端口必须是数字")
            return
        port = int(port)

        for cam in self.cameras:
            self._start_single_camera(cam)

        if self.flask_thread is None or not self.flask_thread.is_alive():
            self.flask_thread = threading.Thread(target=self._start_flask_server, args=(port,), daemon=True)
            self.flask_thread.start()
            time.sleep(0.5)

        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_var.set(f"状态: 已启动，端口 {port}，共 {len(self.cameras)} 路流")
        self.update_camera_url_display()
        self.save_config()  # 保存端口

    def _start_flask_server(self, port):
        try:
            self.flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
        except Exception as e:
            print(f"Flask 异常: {e}")

    # ---------- 停止所有流 ----------
    def stop_all(self):
        for cam in self.cameras:
            self._stop_single_camera(cam)
        self.is_running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status_var.set("状态: 已停止所有流")
        for cam in self.cameras:
            cam['preview_label'].configure(image=None, text="已停止")

    # ---------- 窗口关闭 ----------
    def on_closing(self):
        self.save_config()  # 关闭前保存
        for cam in self.cameras:
            cam['running'] = False
            if cam['cap']:
                cam['cap'].release()
        self.root.destroy()


if __name__ == "__main__":
    root = ctk.CTk()
    app = MultiHttpStreamerApp(root)
    root.mainloop()