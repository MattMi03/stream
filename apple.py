import customtkinter as ctk
from tkinter import filedialog, messagebox
import subprocess
import threading
import sys
import os
import time
import shutil
import cv2
from PIL import Image, ImageTk
from flask import Flask, Response
import logging
import warnings
import requests
import urllib3
import datetime

# ==========================================
# 屏蔽各种警告
warnings.filterwarnings("ignore", category=UserWarning, module="customtkinter")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning) # 屏蔽 HTTPS IP 访问时的证书警告
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
# ==========================================

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

def get_ffmpeg_path():
    """跨平台自动获取 ffmpeg 路径 (兼容 Mac/Win)"""
    exe_name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, exe_name)
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), exe_name)
    if os.path.exists(local_path):
        return local_path
    sys_path = shutil.which(exe_name)
    if sys_path:
        return sys_path
    return ""

class StreamRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("流媒体录像与云端分发工具 - 跨平台业务版")
        self.root.geometry("540x800")
        self.root.resizable(False, False)

        # ---- 核心状态 ----
        self.process = None
        self.is_recording = False
        self.start_time = 0
        self.timer_id = None
        self.preview_running = False
        self.cap = None
        self.latest_frame_bytes = None
        self.cloud_token = ""

        # 初始化 Flask 服务器
        self.flask_app = Flask(__name__)
        self.flask_thread = None
        self.setup_flask_routes()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # ================= 界面布局 =================
        self.main_frame = ctk.CTkFrame(root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # ---- 地址配置 ----
        ctk.CTkLabel(self.main_frame, text="摄像头流媒体地址", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0,2))
        self.url_entry = ctk.CTkEntry(self.main_frame, width=500)
        self.url_entry.pack(fill="x", pady=(0,5))
        self.url_entry.insert(0, "rtsp://admin:password@192.168.1.100:554/stream") 

        # ---- 保存目录 ----
        ctk.CTkLabel(self.main_frame, text="本地保存文件夹 (自动分段录像)", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0,2))
        path_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        path_frame.pack(fill="x", pady=(0,5))
        self.dir_entry = ctk.CTkEntry(path_frame, width=400)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop", "24小时监控")
        self.dir_entry.insert(0, desktop_dir)
        ctk.CTkButton(path_frame, text="浏览", width=80, command=self.browse_dir).pack(side="right", padx=(5,0))
        
        # ---- HTTP 转发 ----
        ctk.CTkLabel(self.main_frame, text="HTTP 直播流转发端口", font=ctk.CTkFont(size=12, weight="bold"), text_color="#1976d2").pack(anchor="w", pady=(0,2))
        port_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        port_frame.pack(fill="x", pady=(0,10))
        self.port_entry = ctk.CTkEntry(port_frame, width=150)
        self.port_entry.pack(side="left")
        self.port_entry.insert(0, "8080")
        
        self.http_url_var = ctk.StringVar(value="外部访问地址: 未启动")
        self.http_url_label = ctk.CTkLabel(port_frame, textvariable=self.http_url_var, font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        self.http_url_label.pack(side="left", padx=15)

        # ================= 云端回放同步模块 =================
        cloud_frame = ctk.CTkFrame(self.main_frame, corner_radius=8, fg_color=("gray90", "gray15"))
        cloud_frame.pack(fill="x", pady=(0, 10), ipadx=10, ipady=10)
        
        ctk.CTkLabel(cloud_frame, text="☁️ 云端回放平台同步", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0,10), padx=5)
        
        # API 地址 & 账号
        ctk.CTkLabel(cloud_frame, text="API地址:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.api_url_entry = ctk.CTkEntry(cloud_frame, width=140)
        self.api_url_entry.grid(row=1, column=1, sticky="w")
        self.api_url_entry.insert(0, "https://111.12.149.164")

        ctk.CTkLabel(cloud_frame, text="账号:").grid(row=1, column=2, sticky="e", padx=5)
        self.account_entry = ctk.CTkEntry(cloud_frame, width=100)
        self.account_entry.grid(row=1, column=3, sticky="w")
        self.account_entry.insert(0, "admin")

        # 密码 & 设备ID
        ctk.CTkLabel(cloud_frame, text="密码:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.pwd_entry = ctk.CTkEntry(cloud_frame, width=140, show="*")
        self.pwd_entry.grid(row=2, column=1, sticky="w")
        self.pwd_entry.insert(0, "123456") 

        ctk.CTkLabel(cloud_frame, text="设备ID:").grid(row=2, column=2, sticky="e", padx=5)
        self.device_id_entry = ctk.CTkEntry(cloud_frame, width=100)
        self.device_id_entry.grid(row=2, column=3, sticky="w")
        self.device_id_entry.insert(0, "1")

        # 按钮行
        self.login_btn = ctk.CTkButton(cloud_frame, text="🔑 登录取Token", width=120, command=self.do_login)
        self.login_btn.grid(row=3, column=0, columnspan=2, pady=10, padx=10)
        
        self.upload_btn = ctk.CTkButton(cloud_frame, text="⬆️ 上传视频并绑定", width=120, command=self.do_upload_and_bind, fg_color="#2e7d32", hover_color="#1b5e20", state="disabled")
        self.upload_btn.grid(row=3, column=2, columnspan=2, pady=10)

        # ================= 预览与控制区 =================
        ctk.CTkLabel(self.main_frame, text="实时预览", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0,2))
        self.preview_label = ctk.CTkLabel(self.main_frame, text="等待开始...", width=480, height=270, corner_radius=10)
        self.preview_label.pack(pady=(0,5))
        self.preview_label.configure(fg_color=("gray75", "gray25")) 

        self.status_var = ctk.StringVar(value="状态: 准备就绪")
        self.status_label = ctk.CTkLabel(self.main_frame, textvariable=self.status_var, font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", pady=(0,5))

        btn_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        btn_frame.pack(pady=5)
        self.start_btn = ctk.CTkButton(btn_frame, text="▶ 启动录制与推流", width=140, command=self.start_recording)
        self.start_btn.pack(side="left", padx=20)
        self.stop_btn = ctk.CTkButton(btn_frame, text="⏹ 停止运行", width=140, fg_color="#F44336", hover_color="#D32F2F", state="disabled", command=self.stop_recording)
        self.stop_btn.pack(side="right", padx=20)

    # ================= 核心业务：云端上传与绑定 =================
    def do_login(self):
        base_url = self.api_url_entry.get().strip().rstrip("/")
        username = self.account_entry.get().strip()
        password = self.pwd_entry.get().strip()
        
        if not base_url or not username or not password:
            messagebox.showwarning("提示", "请完整填写 API地址、账号和密码")
            return
            
        self.status_var.set("状态: 正在登录...")
        
        def login_thread():
            try:
                login_url = f"{base_url}/admin/apiauth/auth/login"
                
                # 【精确匹配后端 JSON 结构】
                payload = {
                    "identifier": username,
                    "password": password,
                    "loginType": "WORK_CODE",
                    "userType": 1
                }
                
                resp = requests.post(
                    login_url, 
                    json=payload, 
                    verify=False, 
                    timeout=5
                )
                result = resp.json()
                
                if resp.status_code == 200 and result.get("code") == 200:
                    self.cloud_token = result["data"]["token"]
                    self.root.after(0, lambda: self.status_var.set("状态: ☁️ 登录成功，Token 已获取"))
                    self.root.after(0, lambda: self.upload_btn.configure(state="normal"))
                    self.root.after(0, lambda: messagebox.showinfo("成功", f"登录成功！\n获取到账号: {result['data']['name']} 的 Token"))
                else:
                    self.root.after(0, lambda: messagebox.showerror("登录失败", result.get("msg", "未知错误")))
                    self.root.after(0, lambda: self.status_var.set("状态: 登录失败"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("网络错误", str(e)))
                self.root.after(0, lambda: self.status_var.set("状态: 网络请求异常"))
                
        threading.Thread(target=login_thread, daemon=True).start()

    def do_upload_and_bind(self):
        filepath = filedialog.askopenfilename(title="选择要上传的录像文件", filetypes=[("MP4 视频", "*.mp4"), ("所有文件", "*.*")])
        if not filepath:
            return
            
        device_id = self.device_id_entry.get().strip()
        base_url = self.api_url_entry.get().strip().rstrip("/")
        
        if not device_id.isdigit():
            messagebox.showerror("错误", "设备ID必须是数字")
            return

        self.status_var.set(f"状态: 正在向云端上传并绑定 设备 {device_id}...")
        self.upload_btn.configure(state="disabled")

        def upload_task():
            try:
                headers = {
                    "Authorization": f"Bearer {self.cloud_token}",
                    "Content-Type": "application/json"
                }
                
                # 1. 获取上传凭证
                file_size = os.path.getsize(filepath)
                file_name = os.path.basename(filepath)
                token_payload = {
                    "studentId": "temp",
                    "fileName": file_name,
                    "contentType": "video/mp4",
                    "fileSize": file_size
                }
                
                token_url = f"{base_url}/admin/apifile/photos/upload-token"
                resp_token = requests.post(token_url, json=token_payload, headers=headers, verify=False, timeout=10)
                if resp_token.status_code != 200 or resp_token.json().get("code") != 200:
                    raise Exception(f"获取上传凭证失败: {resp_token.text}")
                    
                upload_data = resp_token.json()["data"]
                object_key = upload_data["objectKey"]
                upload_url = upload_data["uploadUrl"]

                # 2. 上传文件到 OSS
                self.root.after(0, lambda: self.status_var.set("状态: 凭证获取成功，正在推送大文件至 OSS..."))
                with open(filepath, 'rb') as f:
                    put_headers = {"Content-Type": "video/mp4"}
                    resp_oss = requests.put(upload_url, headers=put_headers, data=f, verify=False)
                    if resp_oss.status_code not in (200, 201):
                        raise Exception(f"OSS直传失败，HTTP状态码: {resp_oss.status_code}")

                # 3. 绑定设备ID保存到录像回放表
                self.root.after(0, lambda: self.status_var.set("状态: 文件上传成功，正在绑定录像回放记录..."))
                
                now = datetime.datetime.now()
                save_payload = {
                    "deviceId": int(device_id),
                    "videoUrl": object_key,
                    "recordDate": now.strftime("%Y-%m-%d"),
                    "startTime": "00:00:00",
                    "endTime": "23:59:59"
                }
                
                # 【最终修正】：带上正确的网关前缀 /admin/apistudentaffair
                save_url = f"{base_url}/admin/apistudentaffair/admin/videocheck/domain/playback/save"
                
                # 确认是标准 POST 请求
                resp_save = requests.post(save_url, json=save_payload, headers=headers, verify=False, timeout=5)
                if resp_save.status_code == 200 and resp_save.json().get("code") == 200:
                    self.root.after(0, lambda: messagebox.showinfo("完美结束", f"✅ 视频上传成功！\n设备ID: {device_id}\n云端标识: {object_key}\n记录已成功保存到录像回放列表中！"))
                else:
                    raise Exception(f"业务入库失败: {resp_save.text}")
                    
            except Exception as e:
                err_msg = str(e)  # 先转成普通字符串
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("云端同步失败", msg))
            finally:
                self.root.after(0, lambda: self.status_var.set("状态: 云端操作结束"))
                self.root.after(0, lambda: self.upload_btn.configure(state="normal"))

        threading.Thread(target=upload_task, daemon=True).start()

    # ================= 基础录制与推流逻辑 =================
    def setup_flask_routes(self):
        @self.flask_app.route('/stream')
        def video_feed():
            def generate_frames():
                while True:
                    if self.latest_frame_bytes is not None and self.is_recording:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + self.latest_frame_bytes + b'\r\n')
                    else:
                        time.sleep(0.1)
            return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    def start_flask_server(self, port):
        try:
            self.flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
        except Exception as e:
            print(f"Flask 服务器异常: {e}")

    def browse_dir(self):
        dirname = filedialog.askdirectory(title="选择保存文件夹")
        if dirname:
            self.dir_entry.delete(0, ctk.END)
            self.dir_entry.insert(0, dirname)

    def update_timer(self):
        if self.is_recording:
            elapsed = int(time.time() - self.start_time)
            mins, secs = divmod(elapsed, 60)
            hours, mins = divmod(mins, 60)
            self.status_var.set(f"状态: 录制&推流中 (已运行 {hours:02d}:{mins:02d}:{secs:02d})")
            self.status_label.configure(text_color="#2e7d32")
            self.timer_id = self.root.after(1000, self.update_timer)

    def _preview_loop(self, url):
        self.preview_running = True
        try:
            self.cap = cv2.VideoCapture(url)
            if url.lower().startswith('rtsp://'):
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not self.cap.isOpened():
                self.root.after(0, lambda: self.preview_label.configure(text="无法打开视频流"))
                return

            target_fps = 15
            frame_interval = 1.0 / target_fps

            while self.preview_running and self.cap is not None and self.cap.isOpened():
                start = time.time()
                ret, frame = self.cap.read()
                if not ret:
                    time.sleep(1)
                    continue

                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70] 
                ret_encode, buffer = cv2.imencode('.jpg', frame, encode_param)
                if ret_encode:
                    self.latest_frame_bytes = buffer.tobytes()

                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                
                label_width = self.preview_label.winfo_width() if self.preview_label.winfo_width() > 1 else 480
                label_height = self.preview_label.winfo_height() if self.preview_label.winfo_height() > 1 else 270
                img.thumbnail((label_width, label_height), Image.Resampling.LANCZOS)
                
                imgtk = ImageTk.PhotoImage(img)
                self.root.after(0, lambda img=imgtk: self.preview_label.configure(image=img, text=""))
                self.root.after(0, lambda: setattr(self, '_preview_img', imgtk))

                elapsed = time.time() - start
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
        except Exception:
            pass
        finally:
            if self.cap is not None:
                self.cap.release()
                self.cap = None
            self.preview_running = False
            self.latest_frame_bytes = None
            self.root.after(0, lambda: self.preview_label.configure(image=None, text="已停止"))

    def start_recording(self):
        url = self.url_entry.get().strip()
        out_dir = self.dir_entry.get().strip()
        
        try:
            port = int(self.port_entry.get().strip())
        except ValueError:
            messagebox.showwarning("提示", "端口必须是纯数字！")
            return

        if not url or not out_dir:
            messagebox.showwarning("提示", "地址和保存文件夹不能为空！")
            return

        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path:
            messagebox.showerror("环境缺失", "找不到 ffmpeg，请确保系统已安装。")
            return

        if not self.preview_running:
            self.preview_thread = threading.Thread(target=self._preview_loop, args=(url,), daemon=True)
            self.preview_thread.start()

        if self.flask_thread is None or not self.flask_thread.is_alive():
            self.flask_thread = threading.Thread(target=self.start_flask_server, args=(port,), daemon=True)
            self.flask_thread.start()

        self.is_recording = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        
        self.http_url_var.set(f"外部访问地址: http://你的电脑IP:{port}/stream")
        self.http_url_label.configure(text_color="#ed6c02")

        threading.Thread(target=self._record_thread, args=(ffmpeg_path, url, out_dir), daemon=True).start()

    def _record_thread(self, ffmpeg_path, url, out_dir):
        segment_filename = os.path.join(out_dir, "录像_%Y-%m-%d_%H-%M-%S.mp4")
        log_file_path = os.path.join(out_dir, "ffmpeg_debug.log")
        
        command = [ffmpeg_path, '-y']
        if url.lower().startswith('rtsp://'):
            command.extend(['-rtsp_transport', 'tcp'])
            
        command.extend(['-i', url])
        command.extend([
            '-c:v', 'copy',       
            '-c:a', 'aac',        
            '-f', 'segment',
            '-segment_atclocktime', '1',  
            '-segment_time', '86400',     
            '-reset_timestamps', '1',
            '-strftime', '1',
            segment_filename
        ])

        try:
            with open(log_file_path, "w", encoding="utf-8") as log_file:
                log_file.write(f"=== 24H 录像日志 ===\n执行命令: {' '.join(command)}\n\n")
                log_file.flush()
                # 跨平台隐藏窗口处理
                kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log_file, stderr=subprocess.STDOUT, **kwargs)

                try:
                    self.process.wait(timeout=4.0)
                    self.is_recording = False
                    self.root.after(0, lambda: self._connection_failed(log_file_path))
                    return
                except subprocess.TimeoutExpired:
                    self.root.after(0, self._connection_success)

                self.process.wait()
        except Exception as e:
            print(f"录制异常: {e}")

        self.is_recording = False
        self.root.after(0, self._reset_ui)

    def _connection_failed(self, log_path=""):
        messagebox.showerror("连接失败", f"流连接失败或地址无效！")
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
            self.status_var.set("状态: 正在结束运行...")
            self.stop_btn.configure(state="disabled")
            self._stop_preview()
            try:
                self.process.stdin.write(b'q\n')
                self.process.stdin.flush()
            except Exception:
                self.process.terminate()

    def _stop_preview(self):
        self.preview_running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        self.latest_frame_bytes = None
        self.root.after(0, lambda: self.preview_label.configure(image=None, text="预览已停止"))

    def _reset_ui(self):
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None
        self.status_var.set("状态: 已停止")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.http_url_var.set("外部访问地址: 未启动")
        self.http_url_label.configure(text_color="gray")
        if self.preview_running:
            self._stop_preview()

    def on_closing(self):
        if self.is_recording:
            if messagebox.askokcancel("确认", "确定要退出并打断录制吗？"):
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