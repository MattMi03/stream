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
import warnings
import requests
import urllib3
import datetime

warnings.filterwarnings("ignore", category=UserWarning, module="customtkinter")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

def get_ffmpeg_path():
    exe_name = 'ffmpeg.exe'
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, exe_name)
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), exe_name)
    if os.path.exists(local_path):
        return local_path
    sys_path = shutil.which(exe_name)
    return sys_path if sys_path else ""

class RecorderAndCloudApp:
    def __init__(self, root):
        self.root = root
        self.root.title("监控录制与云端同步工具 - 业务专版")
        self.root.geometry("540x740")
        self.root.resizable(False, False)

        self.process = None
        self.is_recording = False
        self.start_time = 0
        self.timer_id = None
        self.preview_running = False
        self.cap = None
        self.cloud_token = ""

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.main_frame = ctk.CTkFrame(root, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # ---- 基础录制配置 ----
        ctk.CTkLabel(self.main_frame, text="摄像头流媒体地址", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0,2))
        self.url_entry = ctk.CTkEntry(self.main_frame, width=500)
        self.url_entry.pack(fill="x", pady=(0,5))
        self.url_entry.insert(0, "rtsp://admin:password@192.168.1.100:554/stream") 

        ctk.CTkLabel(self.main_frame, text="本地保存文件夹 (自动分段录像)", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(0,2))
        path_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        path_frame.pack(fill="x", pady=(0,10))
        self.dir_entry = ctk.CTkEntry(path_frame, width=400)
        self.dir_entry.pack(side="left", fill="x", expand=True)
        self.dir_entry.insert(0, os.path.join(os.path.expanduser("~"), "Desktop", "24小时监控"))
        ctk.CTkButton(path_frame, text="浏览", width=80, command=self.browse_dir).pack(side="right", padx=(5,0))
        
        # ---- 云端同步区 ----
        cloud_frame = ctk.CTkFrame(self.main_frame, corner_radius=8, fg_color=("gray90", "gray15"))
        cloud_frame.pack(fill="x", pady=(5, 10), ipadx=10, ipady=10)
        
        ctk.CTkLabel(cloud_frame, text="☁️ 云端回放平台同步", font=ctk.CTkFont(size=13, weight="bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0,10), padx=5)
        
        ctk.CTkLabel(cloud_frame, text="API地址:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.api_url_entry = ctk.CTkEntry(cloud_frame, width=140)
        self.api_url_entry.grid(row=1, column=1, sticky="w")
        self.api_url_entry.insert(0, "https://111.12.149.164")

        ctk.CTkLabel(cloud_frame, text="账号:").grid(row=1, column=2, sticky="e", padx=5)
        self.account_entry = ctk.CTkEntry(cloud_frame, width=100)
        self.account_entry.grid(row=1, column=3, sticky="w")
        self.account_entry.insert(0, "admin")

        ctk.CTkLabel(cloud_frame, text="密码:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.pwd_entry = ctk.CTkEntry(cloud_frame, width=140, show="*")
        self.pwd_entry.grid(row=2, column=1, sticky="w")
        self.pwd_entry.insert(0, "12345678") 

        ctk.CTkLabel(cloud_frame, text="设备ID:").grid(row=2, column=2, sticky="e", padx=5)
        self.device_id_entry = ctk.CTkEntry(cloud_frame, width=100)
        self.device_id_entry.grid(row=2, column=3, sticky="w")
        self.device_id_entry.insert(0, "1")

        self.login_btn = ctk.CTkButton(cloud_frame, text="🔑 登录取Token", width=120, command=self.do_login)
        self.login_btn.grid(row=3, column=0, columnspan=2, pady=10, padx=10)
        
        self.upload_btn = ctk.CTkButton(cloud_frame, text="⬆️ 上传视频并入库", width=120, command=self.do_upload_and_bind, fg_color="#2e7d32", hover_color="#1b5e20", state="disabled")
        self.upload_btn.grid(row=3, column=2, columnspan=2, pady=10)

        # ---- 状态与预览区 ----
        ctk.CTkLabel(self.main_frame, text="本地监控状态预览", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(5,2))
        self.preview_label = ctk.CTkLabel(self.main_frame, text="等待开始...", width=480, height=270, corner_radius=10)
        self.preview_label.pack(pady=(0,5))
        self.preview_label.configure(fg_color=("gray75", "gray25")) 

        self.status_var = ctk.StringVar(value="状态: 准备就绪")
        self.status_label = ctk.CTkLabel(self.main_frame, textvariable=self.status_var, font=ctk.CTkFont(size=11))
        self.status_label.pack(anchor="w", pady=(0,5))

        btn_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        btn_frame.pack(pady=5)
        self.start_btn = ctk.CTkButton(btn_frame, text="▶ 启动24H录制", width=140, command=self.start_recording)
        self.start_btn.pack(side="left", padx=20)
        self.stop_btn = ctk.CTkButton(btn_frame, text="⏹ 停止录制", width=140, fg_color="#F44336", hover_color="#D32F2F", state="disabled", command=self.stop_recording)
        self.stop_btn.pack(side="right", padx=20)

    # -------- 云端业务逻辑 --------
    def do_login(self):
        base_url = self.api_url_entry.get().strip().rstrip("/")
        username = self.account_entry.get().strip()
        password = self.pwd_entry.get().strip()
        if not base_url or not username or not password:
            return messagebox.showwarning("提示", "信息不全")
            
        self.status_var.set("状态: 正在向云端发起登录认证...")
        
        def login_thread():
            try:
                payload = {"identifier": username, "password": password, "loginType": "WORK_CODE", "userType": 1}
                resp = requests.post(f"{base_url}/admin/apiauth/auth/login", json=payload, verify=False, timeout=5)
                result = resp.json()
                if resp.status_code == 200 and result.get("code") == 200:
                    self.cloud_token = result["data"]["token"]
                    self.root.after(0, lambda: self.status_var.set("状态: ☁️ 登录成功"))
                    self.root.after(0, lambda: self.upload_btn.configure(state="normal"))
                else:
                    self.root.after(0, lambda msg=result.get("msg", "未知"): messagebox.showerror("登录失败", msg))
            except Exception as e:
                self.root.after(0, lambda msg=str(e): messagebox.showerror("网络错误", msg))
        threading.Thread(target=login_thread, daemon=True).start()

    def do_upload_and_bind(self):
        filepath = filedialog.askopenfilename(title="选择要上传的录像", filetypes=[("MP4", "*.mp4"), ("All", "*.*")])
        if not filepath: return
        device_id = self.device_id_entry.get().strip()
        base_url = self.api_url_entry.get().strip().rstrip("/")
        
        self.status_var.set("状态: 获取凭证中...")
        self.upload_btn.configure(state="disabled")

        def upload_task():
            try:
                headers = {"Authorization": f"Bearer {self.cloud_token}", "Content-Type": "application/json"}
                # 1. 获取凭证
                token_payload = {"studentId": "temp", "fileName": os.path.basename(filepath), "contentType": "video/mp4", "fileSize": os.path.getsize(filepath)}
                resp_token = requests.post(f"{base_url}/admin/apifile/photos/upload-token", json=token_payload, headers=headers, verify=False)
                if resp_token.status_code != 200 or resp_token.json().get("code") != 200:
                    raise Exception(f"凭证失败: {resp_token.text}")
                    
                obj_key = resp_token.json()["data"]["objectKey"]
                upl_url = resp_token.json()["data"]["uploadUrl"]

                # 2. 上传 OSS
                self.root.after(0, lambda: self.status_var.set("状态: 正在大文件直传 OSS..."))
                with open(filepath, 'rb') as f:
                    resp_oss = requests.put(upl_url, headers={"Content-Type": "video/mp4"}, data=f, verify=False)
                    if resp_oss.status_code not in (200, 201): raise Exception("OSS直传失败")

                # 3. 入库绑定
                self.root.after(0, lambda: self.status_var.set("状态: 正在绑定录像记录..."))
                save_payload = {
                    "deviceId": int(device_id), "videoUrl": obj_key, 
                    "recordDate": datetime.datetime.now().strftime("%Y-%m-%d"), 
                    "startTime": "00:00:00", "endTime": "23:59:59"
                }
                resp_save = requests.post(f"{base_url}/admin/apistudentaffair/admin/videocheck/domain/playback/save", json=save_payload, headers=headers, verify=False)
                
                if resp_save.status_code == 200 and resp_save.json().get("code") == 200:
                    self.root.after(0, lambda: messagebox.showinfo("完成", f"上传并入库成功！\n云标识: {obj_key}"))
                else:
                    raise Exception(f"入库失败: {resp_save.text}")
            except Exception as e:
                self.root.after(0, lambda msg=str(e): messagebox.showerror("错误", msg))
            finally:
                self.root.after(0, lambda: self.status_var.set("状态: 同步结束"))
                self.root.after(0, lambda: self.upload_btn.configure(state="normal"))
        threading.Thread(target=upload_task, daemon=True).start()

    # -------- 录制逻辑 --------
    def browse_dir(self):
        dirname = filedialog.askdirectory()
        if dirname:
            self.dir_entry.delete(0, ctk.END)
            self.dir_entry.insert(0, dirname)

    def _preview_loop(self, url):
        self.preview_running = True
        try:
            self.cap = cv2.VideoCapture(url)
            if url.lower().startswith('rtsp://'): self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            frame_int = 1.0 / 10
            while self.preview_running and self.cap and self.cap.isOpened():
                start = time.time()
                ret, frame = self.cap.read()
                if not ret: 
                    time.sleep(1)
                    continue
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                img.thumbnail((480, 270), Image.Resampling.LANCZOS)
                imgtk = ImageTk.PhotoImage(img)
                self.root.after(0, lambda img=imgtk: self.preview_label.configure(image=img, text=""))
                self.root.after(0, lambda: setattr(self, '_preview_img', imgtk))
                elapsed = time.time() - start
                if elapsed < frame_int: time.sleep(frame_int - elapsed)
        except Exception: pass
        finally:
            if self.cap: self.cap.release(); self.cap = None
            self.root.after(0, lambda: self.preview_label.configure(image=None, text="预览停止"))

    def start_recording(self):
        url, out_dir = self.url_entry.get().strip(), self.dir_entry.get().strip()
        if not url or not out_dir: return messagebox.showwarning("提示", "地址/目录为空")
        os.makedirs(out_dir, exist_ok=True)
        ffmpeg_path = get_ffmpeg_path()
        if not ffmpeg_path: return messagebox.showerror("缺失", "找不到 ffmpeg.exe")

        self.preview_running = True
        threading.Thread(target=self._preview_loop, args=(url,), daemon=True).start()
        
        self.is_recording = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._record_thread, args=(ffmpeg_path, url, out_dir), daemon=True).start()

    def _record_thread(self, ffmpeg_path, url, out_dir):
        seg_file = os.path.join(out_dir, "录像_%Y-%m-%d_%H-%M-%S.mp4")
        cmd = [ffmpeg_path, '-y']
        if url.lower().startswith('rtsp://'): cmd.extend(['-rtsp_transport', 'tcp'])
        cmd.extend(['-i', url, '-c:v', 'copy', '-c:a', 'aac', '-f', 'segment', '-segment_atclocktime', '1', '-segment_time', '86400', '-reset_timestamps', '1', '-strftime', '1', seg_file])

        try:
            kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            try:
                self.process.wait(timeout=4.0)
                self.is_recording = False
                self.root.after(0, lambda: messagebox.showerror("失败", "FFmpeg启动失败或流断开"))
                return
            except subprocess.TimeoutExpired:
                self.start_time = time.time()
                self.update_timer()
            self.process.wait()
        except Exception as e: print(e)
        self.is_recording = False
        self.root.after(0, self._reset_ui)

    def update_timer(self):
        if self.is_recording:
            elapsed = int(time.time() - self.start_time)
            m, s = divmod(elapsed, 60)
            h, m = divmod(m, 60)
            self.status_var.set(f"状态: 稳定录像中 ({h:02d}:{m:02d}:{s:02d})")
            self.timer_id = self.root.after(1000, self.update_timer)

    def stop_recording(self):
        if self.process and self.is_recording:
            self.preview_running = False
            try:
                self.process.stdin.write(b'q\n')
                self.process.stdin.flush()
            except: self.process.terminate()

    def _reset_ui(self):
        if self.timer_id: self.root.after_cancel(self.timer_id); self.timer_id = None
        self.status_var.set("状态: 已停止")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def on_closing(self):
        self.preview_running = False
        if self.is_recording: self.stop_recording()
        self.root.destroy()

if __name__ == "__main__":
    root = ctk.CTk()
    app = RecorderAndCloudApp(root)
    root.mainloop()