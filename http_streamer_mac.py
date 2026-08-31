import customtkinter as ctk
from tkinter import messagebox
import threading
import time
import cv2
from PIL import Image, ImageTk
from flask import Flask, Response
import logging
import warnings
import socket

# 屏蔽警告
warnings.filterwarnings("ignore", category=UserWarning, module="customtkinter")
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")


class MultiHttpStreamerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("多路摄像头 HTTP 直播流转发服务")
        self.root.geometry("640x700")
        self.root.resizable(True, True)

        # 存储所有摄像头数据
        self.cameras = []          # 每个元素为 dict
        self.camera_counter = 0    # 用于分配唯一 ID
        self.is_running = False    # 服务运行状态标志

        # Flask 服务
        self.flask_app = Flask(__name__)
        self.flask_thread = None
        self.setup_flask_routes()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # ---------- UI 布局 ----------
        self.main_frame = ctk.CTkFrame(root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # 顶部控制行
        control_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        control_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(control_frame, text="HTTP 端口:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0,5))
        self.port_entry = ctk.CTkEntry(control_frame, width=80)
        self.port_entry.pack(side="left", padx=(0, 15))
        self.port_entry.insert(0, "8080")

        self.start_btn = ctk.CTkButton(control_frame, text="▶ 启动所有流", width=100, command=self.start_all)
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = ctk.CTkButton(control_frame, text="⏹ 停止所有流", width=100, fg_color="#F44336", hover_color="#D32F2F", state="disabled", command=self.stop_all)
        self.stop_btn.pack(side="left", padx=5)

        self.add_btn = ctk.CTkButton(control_frame, text="➕ 添加摄像头", width=120, command=self.add_camera)
        self.add_btn.pack(side="right", padx=5)

        # 滚动区域用于放置摄像头卡片
        self.scrollable_frame = ctk.CTkScrollableFrame(self.main_frame, label_text="摄像头列表")
        self.scrollable_frame.pack(fill="both", expand=True, pady=10)

        # 状态显示
        self.status_var = ctk.StringVar(value="状态: 准备就绪")
        self.status_label = ctk.CTkLabel(self.main_frame, textvariable=self.status_var, font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", pady=(5, 0))

        # 默认添加一个摄像头
        self.add_camera()

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

    # ---------- 添加/删除摄像头 ----------
    def add_camera(self):
        """添加一个新的摄像头卡片"""
        cam_id = self.camera_counter
        self.camera_counter += 1

        # 卡片容器
        card = ctk.CTkFrame(self.scrollable_frame, corner_radius=8, fg_color=("gray90", "gray20"))
        card.pack(fill="x", pady=5, padx=5, ipady=5)

        # 地址输入框
        url_entry = ctk.CTkEntry(card, placeholder_text="输入 RTSP 地址")
        url_entry.pack(fill="x", padx=10, pady=(5, 0))
        url_entry.insert(0, f"rtsp://admin:password@192.168.1.100:554/stream{cam_id+1}")

        # 预览标签 + 控制行
        preview_frame = ctk.CTkFrame(card, fg_color="transparent")
        preview_frame.pack(fill="x", padx=10, pady=5)

        preview_label = ctk.CTkLabel(preview_frame, text=f"摄像头 {cam_id+1} 预览", width=200, height=120, corner_radius=6)
        preview_label.pack(side="left", padx=(0, 10))
        preview_label.configure(fg_color=("gray75", "gray25"))

        # 右侧按钮
        btn_frame = ctk.CTkFrame(preview_frame, fg_color="transparent")
        btn_frame.pack(side="right", fill="y")
        del_btn = ctk.CTkButton(btn_frame, text="删除", width=60, fg_color="#F44336", hover_color="#D32F2F",
                                command=lambda c=card, id=cam_id: self.remove_camera(c, id))
        del_btn.pack(pady=2)

        # URL 显示（只读）
        url_display = ctk.CTkEntry(card, width=300, state="readonly", font=ctk.CTkFont(size=10))
        url_display.pack(padx=10, pady=(0,5))
        url_display.insert(0, "启动服务后显示访问地址")

        # 存储摄像头数据
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
            '_img': None,          # 用于存储预览图片引用，防止被回收
        }
        self.cameras.append(cam_data)

        # 如果当前已经处于启动状态，则新加的摄像头自动启动
        if self.is_running:
            self._start_single_camera(cam_data)
            # 更新该卡的 URL 显示
            self.update_camera_url_display(cam_data)

    def remove_camera(self, card, cam_id):
        """删除一个摄像头卡片"""
        for cam in self.cameras:
            if cam['camera_id'] == cam_id:
                cam['running'] = False
                if cam['cap']:
                    cam['cap'].release()
                break
        self.cameras = [cam for cam in self.cameras if cam['camera_id'] != cam_id]
        card.destroy()
        if not self.cameras and self.is_running:
            self.stop_all()

    # ---------- 更新 URL 显示 ----------
    def update_camera_url_display(self, cam_data=None):
        """更新指定摄像头或所有摄像头的 URL 显示"""
        port = self.port_entry.get().strip()
        if not port.isdigit():
            return
        # 使用 localhost 保证本机可访问
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

    # ---------- 单个摄像头预览线程 ----------
    def _preview_loop(self, cam_data):
        url = cam_data['url_entry'].get().strip()
        if not url:
            cam_data['running'] = False
            return

        try:
            cap = cv2.VideoCapture(url)
            if url.lower().startswith('rtsp://'):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cam_data['cap'] = cap
            cam_data['running'] = True

            frame_interval = 1.0 / 10  # 10 fps
            while cam_data['running'] and cap.isOpened():
                start = time.time()
                ret, frame = cap.read()
                if not ret:
                    time.sleep(1)
                    continue

                # 编码为 JPEG 供 HTTP 流
                ret_encode, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ret_encode:
                    cam_data['frame_bytes'] = buffer.tobytes()

                # 更新预览 UI
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                img.thumbnail((200, 120), Image.Resampling.LANCZOS)
                imgtk = ImageTk.PhotoImage(img)
                # 在字典中存储引用，防止垃圾回收
                cam_data['_img'] = imgtk
                # 更新标签
                label = cam_data['preview_label']
                self.root.after(0, lambda lbl=label, img=imgtk: lbl.configure(image=img, text=""))

                elapsed = time.time() - start
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
        except Exception as e:
            print(f"摄像头 {cam_data['camera_id']} 预览异常: {e}")
        finally:
            if cam_data['cap']:
                cam_data['cap'].release()
                cam_data['cap'] = None
            cam_data['running'] = False
            self.root.after(0, lambda lbl=cam_data['preview_label']: lbl.configure(image=None, text="已停止"))

    # ---------- 启动/停止单个摄像头 ----------
    def _start_single_camera(self, cam_data):
        if not cam_data['running']:
            thread = threading.Thread(target=self._preview_loop, args=(cam_data,), daemon=True)
            cam_data['thread'] = thread
            thread.start()

    def _stop_single_camera(self, cam_data):
        cam_data['running'] = False
        if cam_data['cap']:
            cam_data['cap'].release()
            cam_data['cap'] = None

    # ---------- 启动所有流 ----------
    def start_all(self):
        port = self.port_entry.get().strip()
        if not port.isdigit():
            messagebox.showwarning("提示", "端口必须是数字")
            return
        port = int(port)

        # 启动所有摄像头线程
        for cam in self.cameras:
            self._start_single_camera(cam)

        # 启动 Flask 服务（如果尚未运行）
        if self.flask_thread is None or not self.flask_thread.is_alive():
            self.flask_thread = threading.Thread(target=self._start_flask_server, args=(port,), daemon=True)
            self.flask_thread.start()
            time.sleep(0.5)

        self.is_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status_var.set(f"状态: 已启动，端口 {port}，共 {len(self.cameras)} 路流")

        # 更新所有摄像头 URL 显示
        self.update_camera_url_display()

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
            # 清空 URL 显示或保留，这里保留原样

    # ---------- 窗口关闭 ----------
    def on_closing(self):
        for cam in self.cameras:
            cam['running'] = False
            if cam['cap']:
                cam['cap'].release()
        self.root.destroy()


if __name__ == "__main__":
    root = ctk.CTk()
    app = MultiHttpStreamerApp(root)
    root.mainloop()