import sys
import json
import os
import pathlib
import subprocess
import zipfile
import py7zr
import tempfile   
import time
import requests
import shutil
import urllib.request
import socket
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from queue import Queue



CURRENT_VERSION = "v1.0.1"  #版本号

BASE_DIR = Path(os.getenv("LOCALAPPDATA")) / "Bento"
BASE_DIR.mkdir(parents=True, exist_ok=True)
KERNEL_DIR = BASE_DIR / "kernel"
KERNEL_DIR.mkdir(exist_ok=True)
PROFILES_DIR = BASE_DIR / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)

PRESETS = [
    ("Temu前端 美国", "", "https://www.temu.com/us"),
    ("Temu前端 英国", "", "https://www.temu.com/uk"),
    ("Temu前端 法国", "", "https://www.temu.com/fr-en"),
    ("Temu前端 德国", "", "https://www.temu.com/de-en"),
    ("Temu前端 日本", "", "https://www.temu.com/jp-en"),
    ("Temu前端 加拿大", "", "https://www.temu.com/ca"),
    ("Temu前端 澳大利亚", "", "https://www.temu.com/au"),
    ("Temu前端 西班牙", "", "https://www.temu.com/es-en"),
    ("Temu前端 马来西亚", "", "https://www.temu.com/my"),
]

#下载更新包线程
class DownloadThread(QThread):
    download_progress = pyqtSignal(int, int, str)  # 进度, 已下载大小, 网速
    download_finished = pyqtSignal(str)
    download_failed = pyqtSignal(str)
    message = pyqtSignal(str)
    
    def __init__(self, download_url):
        super().__init__()
        self.download_url = download_url
        self.total_size = 0
        self._is_running = True
        self._start_time = None
        self._last_update_time = None
        self._last_size = 0
        self._speed_history = []# 网速计算历史记录

    def run(self):
        try:
            tmp_dir = tempfile.mkdtemp()
            local_path = os.path.join(tmp_dir, os.path.basename(self.download_url))
            
            self.message.emit(f"开始下载: {os.path.basename(self.download_url)}")
            self._start_time = time.time()
            self._last_update_time = self._start_time
            self._last_size = 0
            
            with requests.get(self.download_url, stream=True, timeout=30) as r:
                r.raise_for_status()
                self.total_size = int(r.headers.get('content-length', 0))
                downloaded_size = 0
                
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if not self._is_running:
                            os.remove(local_path)
                            self.message.emit("下载已取消")
                            return
                            
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        
                        # 计算实时速度（每100ms更新一次）
                        current_time = time.time()
                        if current_time - self._last_update_time >= 0.1:
                            elapsed = current_time - self._last_update_time
                            speed = (downloaded_size - self._last_size) / elapsed
                            
                            # 平滑处理（最后 3 个样本的平均值）
                            self._speed_history.append(speed)
                            if len(self._speed_history) > 3:
                                self._speed_history.pop(0)
                            avg_speed = sum(self._speed_history) / len(self._speed_history)
                            
                            speed_str = self.format_speed(avg_speed)
                            
                            progress = int(downloaded_size * 100 / self.total_size) if self.total_size > 0 else 0
                            self.download_progress.emit(progress, downloaded_size, speed_str)
                            
                            self._last_update_time = current_time
                            self._last_size = downloaded_size
                
            self.download_finished.emit(local_path)
            
        except Exception as e:
            self.download_failed.emit(str(e))
    
    def format_speed(self, speed_bps):
        """Format speed display"""
        if speed_bps < 1024:
            return f"{speed_bps:.0f} B/s"
        elif speed_bps < 1024 * 1024:
            return f"{speed_bps/1024:.1f} KB/s"
        else:
            return f"{speed_bps/(1024 * 1024):.1f} MB/s"

    def stop(self):
        """Stop the download"""
        self._is_running = False


class CheckUpdateThread(QThread):
    update_checked = pyqtSignal(dict, str)  

    def __init__(self, current_version):
        super().__init__()
        self.current_version = current_version
        self.api_url = "https://api.github.com/repos/lemon-o/Bento/releases/latest"

    def run(self):
        try:
            response = requests.get(self.api_url, timeout=10)
            response.raise_for_status()
            self.update_checked.emit(response.json(), "")
        except Exception as e:
            self.update_checked.emit({}, str(e))


class UpdateDialog(QDialog):
    def __init__(self, parent=None, current_version=""):
        super().__init__(parent)
        self.current_version = current_version
        self.latest_version = ""
        self.download_url = ""
        self.setup_ui()
        self.show() 
        self.start_check_update()  
        
    def setup_ui(self):
        self.setWindowTitle("检查更新")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.resize(370, 160)
        
        layout = QVBoxLayout()
        
        # Status label
        self.status_label = QLabel("正在检查更新...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("""
            QLabel {
                font-size: 14px;
                padding: 10px;
                border: 1px solid #ccc;
                border-radius: 5px;
                background-color: #f8f9fa;
                min-height: 40px;
            }
        """)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        
        # 按钮
        button_height = self.height() // 5
        self.button_layout = QHBoxLayout()
        
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setFixedHeight(button_height)
        self.cancel_button.clicked.connect(self.close)
        self.button_layout.addWidget(self.cancel_button)

        self.update_button = QPushButton("更新")
        self.update_button.setObjectName("primaryButton")
        self.update_button.setFixedHeight(button_height)
        self.update_button.clicked.connect(self.start_update)
        self.update_button.setEnabled(False)
        self.button_layout.addWidget(self.update_button)
        
        layout.addLayout(self.button_layout)
        self.setLayout(layout)
        
    def start_check_update(self):
        """Start async update check"""
        self.status_label.setText("正在检查更新...")
        self.check_thread = CheckUpdateThread(self.current_version)
        self.check_thread.update_checked.connect(self.handle_update_result)
        self.check_thread.start()

    def handle_update_result(self, release_info, error):
        """Handle update check result"""
        if error:
            self.status_label.setText(f"检查失败: {error}")
            self.cancel_button.setText("关闭")
            return

        self.latest_version = release_info.get("tag_name", "")
        if not self.latest_version:
            self.status_label.setText("无法获取版本号")
            self.cancel_button.setText("关闭")
            return

        self.status_label.setText(f"当前版本: {self.current_version}\n最新版本: {self.latest_version}")

        if self.latest_version == self.current_version:
            self.status_label.setText("已经是最新版本")
            self.cancel_button.setText("关闭")
            return

        # 下载
        assets = release_info.get("assets", [])
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith((".exe", ".zip")):
                self.download_url = asset.get("browser_download_url")
                break

        if not self.download_url:
            self.status_label.setText("未找到可下载的安装文件")
            self.cancel_button.setText("关闭")
            return

        self.status_label.setText(f"发现新版本 {self.latest_version}，当前版本{self.current_version}")
        self.update_button.setEnabled(True)

    def start_update(self):
        """Start downloading update"""
        if hasattr(self, 'download_url') and self.download_url:
            # 更新UI
            self.update_button.hide()
            self.cancel_button.hide()
            self.progress_bar.show()
            self.progress_bar.setValue(0)
            self.status_label.setText("准备下载更新...")
            
            QApplication.processEvents()
            
            # 创建下载线程
            self.download_thread = DownloadThread(self.download_url)
            self.download_thread.download_progress.connect(self.handle_download_progress)
            self.download_thread.download_finished.connect(self.on_download_finished)
            self.download_thread.download_failed.connect(self.on_download_failed)
            self.download_thread.message.connect(self.status_label.setText)
            
            self.download_thread.start()

    def handle_download_progress(self, progress, downloaded_size, speed_str):
        """Update download progress"""
        def format_size(size):
            if size < 1024:
                return f"{size}B"
            elif size < 1024 * 1024:
                return f"{size/1024:.1f}KB"
            else:
                return f"{size/(1024 * 1024):.1f}MB"
        
        total_size = self.download_thread.total_size
        total_str = format_size(total_size) if total_size > 0 else "未知大小"
        
        self.progress_bar.setValue(progress + 5 if progress + 5 <= 100 else 100)
        self.status_label.setText(
            f"正在下载更新({format_size(downloaded_size)}/{total_str}) | 速度: {speed_str}"
        )
        QApplication.processEvents()

    def on_download_failed(self, error_msg):
        self.progress_bar.hide()
        self.status_label.setText(f"下载失败: {error_msg}")
        self.cancel_button.setText("关闭")
        self.cancel_button.show()

    def on_download_finished(self, local_path):
        self.status_label.setText("下载完成，准备安装...")
        self.progress_bar.setValue(100)
        
        try:
            if local_path.endswith(".exe"):
                self.minimize_all_windows()
                subprocess.Popen(
                    [local_path], 
                    shell=True,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                )         
                
            elif local_path.endswith(".zip"):
                self.status_label.setText(f"更新包已下载到: {local_path}")
                self.cancel_button.setText("关闭")
                self.cancel_button.show()
                
        except Exception as e:
            self.status_label.setText(f"安装失败: {e}")
            self.cancel_button.setText("关闭")
            self.cancel_button.show()

    def minimize_all_windows(self):
        if self.parent():
            self.parent().showMinimized()
        
        for window in QApplication.topLevelWidgets():
            if window.isWindow() and window.isVisible():
                window.showMinimized()

        QApplication.quit()

class KernelDownloader(QDialog):
    """浏览器内核下载器对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("下载浏览器内核")
        self.setMinimumWidth(500)
        self.setModal(True)
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        
        self.label = QLabel("正在下载浏览器内核，请稍候...")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #495057; ")
        
        layout.addWidget(self.label)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        
        self.setLayout(layout)

class KernelManager:
    """浏览器内核管理器 - Windows专用"""

    @staticmethod
    def get_chrome_path():
        """在 KERNEL_DIR 下递归查找 chrome.exe"""
        if not KERNEL_DIR.exists():
            return None
        for path in KERNEL_DIR.rglob("chrome.exe"):
            if path.is_file():
                return str(path)
        return None

    @staticmethod
    def is_chrome_installed():
        installed = KernelManager.get_chrome_path() is not None
        return installed

    @staticmethod
    def get_download_url():
        return [
            "https://github.com/Hibbiki/chromium-win64/releases/download/v141.0.7390.77-r1509326/chrome.7z",
            "https://github.com/lemon-o/chromium-win64/releases/download/v141.0.7390.77-r1509326/chrome.7z",
        ]

    @staticmethod
    def encode_url(url):
        """对 URL 的 path 部分进行中文编码"""
        parts = urlsplit(url)
        path = quote(parts.path)
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

    @staticmethod
    def download_chrome(progress_callback=None):
        """下载并解压浏览器内核"""
        urls = KernelManager.get_download_url()
        zip_path = KERNEL_DIR / "chrome_download.7z"
        KERNEL_DIR.mkdir(parents=True, exist_ok=True)

        for idx, url in enumerate(urls):
            try:
                if progress_callback:
                    progress_callback(0, f"尝试下载源 {idx + 1}/{len(urls)}...")

                # 删除旧文件
                if zip_path.exists():
                    zip_path.unlink()

                # 中文 URL 编码
                safe_url = KernelManager.encode_url(url)

                # 下载
                def report_progress(block_num, block_size, total_size):
                    if total_size > 0:
                        downloaded = block_num * block_size
                        percent = min(int(downloaded * 100 / total_size) + 5, 100)
                        msg = f"已下载: {downloaded / 1024 / 1024:.2f}MB / {total_size / 1024 / 1024:.2f}MB"
                        if progress_callback:
                            progress_callback(percent, msg)

                urllib.request.urlretrieve(safe_url, str(zip_path), reporthook=report_progress)

                if progress_callback:
                    progress_callback(100, "下载完成，正在解压...")

                # 解压
                if zip_path.suffix == ".zip":
                    with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                        zip_ref.extractall(str(KERNEL_DIR))
                elif zip_path.suffix == ".7z":
                    with py7zr.SevenZipFile(str(zip_path), mode='r') as archive:
                        archive.extractall(path=str(KERNEL_DIR))
                else:
                    if progress_callback:
                        progress_callback(-1, f"未知压缩格式: {zip_path.suffix}")
                    return False

                # 删除压缩文件
                if zip_path.exists():
                    zip_path.unlink()

                if progress_callback:
                    progress_callback(100, "浏览器内核安装完成！")
                return True

            except Exception as e:
                if zip_path.exists():
                    zip_path.unlink()
                if idx < len(urls) - 1:
                    continue
                else:
                    if progress_callback:
                        progress_callback(-1, "所有下载源均失败，请检查网络连接")
                    return False

        return False

class BrowserProfile:
    def __init__(self, name="", start_url="", user_agent="", proxy=None, proxy_user="", proxy_pass="", profile_path=""):
        self.name = name
        self.start_url = start_url
        self.user_agent = user_agent
        self.proxy = proxy or {"type": "http", "ip": "", "port": ""}  # 默认结构
        self.proxy_user = proxy_user
        self.proxy_pass = proxy_pass
        self.profile_path = profile_path

    def to_dict(self):
        return {
            "name": self.name,
            "start_url": self.start_url,
            "user_agent": self.user_agent,
            "proxy": self.proxy,
            "proxy_user": self.proxy_user,
            "proxy_pass": self.proxy_pass,
            "profile_path": self.profile_path
        }

    @staticmethod
    def from_dict(data):
        proxy_data = data.get("proxy", {})
        return BrowserProfile(
            name=data.get("name", ""),
            start_url=data.get("start_url", ""),
            user_agent=data.get("user_agent", ""),
            proxy={
                "type": proxy_data.get("type", "http"),
                "ip": proxy_data.get("ip", ""),
                "port": proxy_data.get("port", "")
            },
            proxy_user=data.get("proxy_user", ""),
            proxy_pass=data.get("proxy_pass", ""),
            profile_path=data.get("profile_path", "")
        )


class EditProfileDialog(QDialog):
    def __init__(self, profile=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑浏览器" if profile else "新建浏览器")
        self.profile = profile

        layout = QVBoxLayout(self)

        # --- 基础设置 ---
        basic_group = QGroupBox("基础设置")
        basic_layout = QFormLayout()
        basic_layout.setSpacing(12)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("名称，例如: 工作账号（必填）")
        basic_layout.addRow(self.name_edit)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("首页网址")
        basic_layout.addRow(self.url_edit)

        self.ua_edit = QLineEdit()
        self.ua_edit.setPlaceholderText("UA标识")
        basic_layout.addRow(self.ua_edit)

        basic_group.setLayout(basic_layout)
        layout.addWidget(basic_group)

        # --- 高级设置 ---
        self.advanced_group = QGroupBox("高级设置")
        advanced_layout = QFormLayout()
        advanced_layout.setSpacing(12)

        # 创建水平布局
        proxy_layout = QHBoxLayout()
        proxy_layout.setContentsMargins(0, 0, 0, 0)  # 去掉内部边距
        proxy_layout.setSpacing(4)  # 下拉框和按钮间距

        # 代理类型下拉框
        self.proxy_type_combo = QComboBox()
        self.proxy_type_combo.addItems(["socks5", "http"])
        self.proxy_type_combo.setStyleSheet("""
            QComboBox {
                padding: 2px;
                color: #495057;
            }
        """)
        proxy_layout.addWidget(self.proxy_type_combo)

        # 检查代理按钮
        self.check_proxy_btn = QPushButton("检查代理")
        self.check_proxy_btn.setStyleSheet("""
            QPushButton{
                background-color: #3656DA;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 2px;
                margin: 0px 0px 0px 6px;
            }
            QPushButton:hover{
                background-color: #193ED5;
            }
            QPushButton:pressed{
                background-color: #0A1E6D;
            }
        """)
        proxy_layout.addWidget(self.check_proxy_btn)

        # 将水平布局添加到表单布局
        label = QLabel("代理类型")
        advanced_layout.setHorizontalSpacing(4)  # 标签与控件间距
        advanced_layout.addRow(label, proxy_layout)

        # 点击事件
        self.check_proxy_btn.clicked.connect(self.on_check_proxy_clicked)

        self.proxy_ip_edit = QLineEdit()
        self.proxy_ip_edit.setPlaceholderText("代理IP或域名")
        advanced_layout.addRow(self.proxy_ip_edit)

        self.proxy_port_edit = QLineEdit()
        self.proxy_port_edit.setPlaceholderText("代理端口")
        advanced_layout.addRow(self.proxy_port_edit)

        self.proxy_user_edit = QLineEdit()
        self.proxy_user_edit.setPlaceholderText("代理用户名")
        advanced_layout.addRow(self.proxy_user_edit)

        self.proxy_pass_edit = QLineEdit()
        self.proxy_pass_edit.setPlaceholderText("代理密码")
        self.proxy_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        advanced_layout.addRow(self.proxy_pass_edit)

        self.advanced_group.setLayout(advanced_layout)
        self.advanced_group.setVisible(False)
        layout.addWidget(self.advanced_group)

        # --- 展开/收起按钮 ---
        self.toggle_advanced_btn = QPushButton("展开高级设置 ▼")
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.toggle_advanced_btn)
        btn_layout.addStretch()

        self.toggle_advanced_btn.setCheckable(True)
        self.toggle_advanced_btn.setChecked(False)
        self.toggle_advanced_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #333;
                border: none;
                padding: 5px;
                margin: 0;
                font-weight: bold;
            }
            QPushButton:hover, QPushButton:checked:hover {
                background-color: rgba(0,0,0,0.05);
            }
            QPushButton:pressed, QPushButton:checked:pressed {
                background-color: rgba(0,0,0,0.1);
            }
        """)

        def toggle_advanced(checked):
            self.advanced_group.setVisible(checked)
            self.toggle_advanced_btn.setText("收起高级设置 ▲" if checked else "展开高级设置 ▼")
            self.adjustSize()

        self.toggle_advanced_btn.toggled.connect(toggle_advanced)
        layout.insertLayout(1, btn_layout)

        layout.addStretch()

        # 基础设置Tooltip
        self.name_edit.setToolTip("浏览器名称，例如：工作账号（必填）")
        self.url_edit.setToolTip("浏览器首页网址，例如：https://www.google.com")
        self.ua_edit.setToolTip("自定义 User-Agent 标识")

        # 高级设置Tooltip
        self.proxy_ip_edit.setToolTip("代理服务器，IP地址或域名")
        self.proxy_port_edit.setToolTip("代理端口号，例如：1080")
        self.proxy_user_edit.setToolTip("代理用户名")
        self.proxy_pass_edit.setToolTip("代理密码")

        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        self.preset_btn = QPushButton("预设")
        self.preset_btn.clicked.connect(lambda: self.parent().show_presets())

        self.cancel_btn = QPushButton("取消")

        self.ok_btn = QPushButton("保存" if self.profile else "创建")
        self.ok_btn.setObjectName("primaryButton")

        btn_layout.addWidget(self.preset_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)

        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

        if profile:
            self.load_profile(profile)

    def on_check_proxy_clicked(self):
        # 获取当前代理信息
        proxy_type = self.proxy_type_combo.currentText()
        ip = self.proxy_ip_edit.text().strip()
        port = self.proxy_port_edit.text().strip()
        user = self.proxy_user_edit.text().strip()
        passwd = self.proxy_pass_edit.text().strip()

        if not ip or not port:
            QMessageBox.warning(self, "提示", "请先填写代理 IP 和端口")
            return

        # 弹出检测窗口
        dialog = ProxyCheckDialog(proxy_type, ip, port, user, passwd, parent=self)
        dialog.exec()  # 阻塞，检测完成后关闭窗口

    def load_profile(self, profile: BrowserProfile):
        self.name_edit.setText(profile.name)
        self.url_edit.setText(profile.start_url)
        self.ua_edit.setText(profile.user_agent)
        self.proxy_type_combo.setCurrentText(profile.proxy.get("type", "http"))
        self.proxy_ip_edit.setText(profile.proxy.get("ip", ""))
        self.proxy_port_edit.setText(profile.proxy.get("port", ""))
        self.proxy_user_edit.setText(profile.proxy_user)
        self.proxy_pass_edit.setText(profile.proxy_pass)

    def get_profile(self):
        """获取当前表单数据为 BrowserProfile 对象"""
        return BrowserProfile(
            name=self.name_edit.text().strip(),
            start_url=self.url_edit.text().strip(),
            user_agent=self.ua_edit.text().strip(),
            proxy={
                "type": self.proxy_type_combo.currentText(),
                "ip": self.proxy_ip_edit.text().strip(),
                "port": self.proxy_port_edit.text().strip()
            },
            proxy_user=self.proxy_user_edit.text().strip(),
            proxy_pass=self.proxy_pass_edit.text().strip(),
        )
    
    def accept(self) -> None:
        parent = self.parent()
        profiles = getattr(parent, "profiles", []) if parent else []

        # 浏览器名称验证
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "警告", "浏览器名称不能为空")
            return

        if profiles:
            if (not getattr(self, "profile", None) or name != self.profile.name) and any(p.name == name for p in profiles):
                QMessageBox.warning(self, "警告", "浏览器名称已存在")
                return

        # 所有验证通过，调用父类 accept() 关闭窗口
        super().accept()

# ---------------- 代理检测线程 ----------------
class ProxyCheckThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, proxy_type, host, port, user="", passwd=""):
        super().__init__()
        self.proxy_type = proxy_type.lower()
        self.host = host.strip()
        self.port = port.strip()
        self.user = user.strip()
        self.passwd = passwd.strip()
        self._stop_event = False
        self.geo_queue = Queue()

    def run(self):
        # ---- 1. host 验证 ----
        if not ProxyCheckThread.validate_host(self.host):
            self.progress.emit("代理地址格式不正确")
            self.finished.emit(False)
            return

        # ---- 2. port 验证 ----
        if not self.port.isdigit() or not (1 <= int(self.port) <= 65535):
            self.progress.emit("代理端口格式不正确")
            self.finished.emit(False)
            return

        # ---- 3. TCP 检测 ----
        try:
            self.progress.emit(f"测试 TCP 连接 {self.host}:{self.port} ...")
            socket.create_connection((self.host, int(self.port)), timeout=3)
            self.progress.emit("TCP 连接成功")
        except Exception as e:
            self.progress.emit(f"TCP 连接失败 {e}")
            self.finished.emit(False)
            return

        if self._stop_event:
            return

        # ---- 4. URL 检测 ----
        proxy_type = self.proxy_type
        if proxy_type.startswith("socks5"):
            proxy_type += "h"
        proxy_auth = f"{self.user}:{self.passwd}@" if self.user and self.passwd else ""
        proxy_url = f"{proxy_type}://{proxy_auth}{self.host}:{self.port}"
        proxies = {"http": proxy_url, "https": proxy_url}

        url_success = False
        ip_info = None
        test_urls = [
            "https://api.ipify.org?format=json",
            "https://ifconfig.me/ip",
            "https://icanhazip.com",
            "https://httpbin.org/ip"
        ]

        for url in test_urls:
            if self._stop_event:
                return
            
            self.progress.emit(f"测试 {url} ...")
            result, ip = self.check_url(url, proxies)
            
            if result:
                self.progress.emit(f"{url} 访问成功")
                url_success = True
                ip_info = ip
                break
            else:
                self.progress.emit(f"{url} 访问失败")

        if not url_success:
            self.finished.emit(False)
            self.progress.emit(f"检测失败")
            return

        # 显示成功信息和 IP
        self.progress.emit(f"检测通过\n\nIP: {ip_info}")

        # ---- TCP + URL 成功，立即更新状态 ----
        self.finished.emit(True)

        # ---- 5. 异步地理位置查询（不影响 UI） ----
        def geo_task():
            geo_info = None
            try:
                ip_to_query = self.host
                if not ProxyCheckThread.is_ip(self.host):
                    ip_to_query = socket.gethostbyname(self.host)
                geo_info = ProxyCheckThread.get_ip_geolocation(ip_to_query)
            except:
                pass
            if geo_info:
                self.progress.emit(f"地理位置: {geo_info}")

        QThreadPool.globalInstance().start(geo_task)

    def stop(self):
        self._stop_event = True

    def check_url(self, url, proxies, retries=1):
        headers = {"User-Agent": "Mozilla/5.0"}
        for _ in range(retries + 1):
            if self._stop_event:
                return False, None
            try:
                r = requests.get(url, proxies=proxies, headers=headers, timeout=5)
                if r.status_code == 200:
                    try:
                        data = r.json()
                        ip_info = data.get('ip') or data.get('origin') or r.text
                    except:
                        ip_info = r.text
                    return True, ip_info
            except:
                continue
        return False, None

    # ---------------- 静态方法 ----------------
    @staticmethod
    def get_ip_geolocation(ip_address):
        import re
        ip_match = re.search(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', str(ip_address))
        if not ip_match:
            return None
        ip = ip_match.group()
        geo_apis = [
            {"url": f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,isp,as&lang=zh-CN",
             "parser": ProxyCheckThread._parse_ipapi, "timeout": 8},
            {"url": f"https://ipapi.co/{ip}/json/", "parser": ProxyCheckThread._parse_ipapico, "timeout": 8},
            {"url": f"https://freeipapi.com/api/json/{ip}", "parser": ProxyCheckThread._parse_freeipapi, "timeout": 8},
            {"url": f"http://www.geoplugin.net/json.gp?ip={ip}", "parser": ProxyCheckThread._parse_geoplugin, "timeout": 8}
        ]
        import requests
        for api in geo_apis:
            try:
                response = requests.get(api["url"], timeout=api.get("timeout", 5))
                if response.status_code == 200:
                    data = response.json()
                    geo_info = api["parser"](data)
                    if geo_info:
                        return geo_info
            except:
                continue
        return None

    @staticmethod
    def _parse_ipapi(data):
        if data.get('status') == 'success':
            parts = [data[k] for k in ['country', 'regionName', 'city'] if data.get(k)]
            if data.get('isp'):
                parts.append(f"ISP: {data['isp']}")
            return ' | '.join(parts) if parts else None
        return None

    @staticmethod
    def _parse_ipapico(data):
        if data.get('error'):
            return None
        parts = [data[k] for k in ['country_name', 'region', 'city'] if data.get(k)]
        if data.get('org'):
            parts.append(f"ISP: {data['org']}")
        return ' | '.join(parts) if parts else None

    @staticmethod
    def _parse_geoplugin(data):
        parts = [data[k] for k in ['geoplugin_countryName', 'geoplugin_region', 'geoplugin_city'] if data.get(k)]
        return ' | '.join(parts) if parts else None

    @staticmethod
    def _parse_freeipapi(data):
        parts = [data[k] for k in ['countryName', 'regionName', 'cityName'] if data.get(k)]
        if data.get('zipCode'):
            parts.append(f"邮编: {data['zipCode']}")
        return ' | '.join(parts) if parts else None

    @staticmethod
    def validate_host(host: str) -> bool:
        import re
        ip_pattern = re.compile(r"^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$")
        domain_pattern = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.[A-Za-z]{2,}$")
        return bool(ip_pattern.match(host) or domain_pattern.match(host))

    @staticmethod
    def is_ip(host: str) -> bool:
        import re
        return bool(re.match(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$", host))


# ---------------- 检测窗口 ----------------
class ProxyCheckDialog(QDialog):
    def __init__(self, proxy_type, host, port, user="", passwd="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("代理通过性测试")
        self.setModal(True)
        if parent:
            self.setWindowIcon(parent.windowIcon())
            self.setMinimumWidth(int(parent.width() * 1.5))

        self.result = False  # 最终检测结果
        self.thread = None
        self.geo_done = False
        self.geo_cursor = None

        layout = QVBoxLayout(self)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        # 状态栏
        h_layout = QHBoxLayout()
        self.status_label = QLabel("<span style='color: #ffce47;'>●</span> 检测进行中")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h_layout.addWidget(self.status_label)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.on_close_clicked)
        self.close_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        h_layout.addWidget(self.close_btn)

        layout.addLayout(h_layout)

        # 启动线程检测代理
        self.thread = ProxyCheckThread(proxy_type, host, port, user, passwd)
        self.thread.progress.connect(self.on_progress)
        self.thread.finished.connect(self.on_finished)
        self.thread.start()

    def on_progress(self, message):
        # TCP+URL 成功后显示等待地理信息
        if "检测通过\n\nIP:" in message and not self.geo_done:
            self.log.append(message)  # 这里会显示到 IP 行
            self.status_label.setText("<span style='color: #00d26a;'>●</span> 通过")
            self.close_btn.setEnabled(True)
            self.show_geo_wait_msg()  # 这会另起一行显示等待提示
            return
        
        # 地理位置检测完成
        if message.startswith("地理位置:") or message.startswith("无法获取地理位置信息"):
            self.geo_done = True
            self.update_geo_result(message)
            return
        
        # 其他消息正常追加
        self.log.append(message)

    def on_finished(self, success):
        """线程结束时，如果 TCP/URL 已成功，不再修改状态"""
        self.result = success
        if not self.geo_done and not success:
            # 如果 TCP/URL 失败，显示失败状态
            self.status_label.setText("<span style='color: #F00101;'>●</span> 失败")
            self.close_btn.setEnabled(True)

    def show_geo_wait_msg(self):
        """显示等待地理位置的提示，并保存光标"""
        self.log.append("正在检测地理位置，可等待检测完成或关闭窗口")
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)  # 移到文档末尾
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)  # 移到当前行开头
        self.geo_cursor = cursor

    def update_geo_result(self, geo_info):
        """替换提示行为实际地理信息"""
        if self.geo_cursor:
            cursor = self.geo_cursor
            cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)  # 选中整行
            cursor.removeSelectedText()  # 删除提示文本
            cursor.insertText(geo_info or "无法获取地理位置信息")  # 插入地理信息
            self.geo_cursor = None
        else:
            # 如果光标丢失，就直接追加
            self.log.append(geo_info or "无法获取地理位置信息")

    def on_close_clicked(self):
        """点击关闭按钮时，无论地理检测是否完成，都立即终止线程"""
        if self.thread and self.thread.isRunning():
            self.thread.terminate()
            self.thread.wait()
        self.accept()

# ---------------- 支持多选、Ctrl+A 全选、拖拽排序，并根据拖动位置自动滚动的 QListWidget ---------------
class DraggableButton(QPushButton):
    """可拖拽的按钮 - PyQt6版本（完全重写修复版）"""
    def __init__(self, text, button_id, parent=None):
        super().__init__(text, parent)
        self.button_id = button_id
        self.is_draggable = True
        self.drag_start_position = None
        
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.MouseButton.LeftButton and self.is_draggable:
            self.drag_start_position = event.position().toPoint()
        # 先调用父类方法，确保按钮状态正确
        super().mousePressEvent(event)
        
    def mouseMoveEvent(self, event):
        """鼠标移动事件 - 简化版"""
        # 不在可拖拽状态时，直接调用父类方法
        if not self.is_draggable or not self.drag_start_position:
            super().mouseMoveEvent(event)
            return
        
        # 检查移动距离
        current_pos = event.position().toPoint()
        distance = (current_pos - self.drag_start_position).manhattanLength()
        
        if distance < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        
        # 立即清除起始位置，防止重复触发
        self.drag_start_position = None
        
        # 开始拖拽 - 完全简化流程
        self._start_drag(event)
        
    def _start_drag(self, event):
        """启动拖拽操作 - 独立方法便于调试"""
        try:
            # 创建 MIME 数据
            mime_data = QMimeData()
            mime_data.setText(self.button_id)
            
            # 创建拖拽对象
            drag = QDrag(self)
            drag.setMimeData(mime_data)
            
            # 创建拖拽预览图
            pixmap = self.grab()
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.fillRect(pixmap.rect(), QColor(0, 0, 0, 180))
            painter.end()
            
            drag.setPixmap(pixmap)
            drag.setHotSpot(event.position().toPoint())
            
            # ⚠️ 关键：通知父组件（简化版，不循环查找）
            if self.parent() and hasattr(self.parent().parent(), '_on_drag_started'):
                try:
                    self.parent().parent()._on_drag_started()
                except:
                    pass
            
            # 执行拖拽
            result = drag.exec(Qt.DropAction.MoveAction)
            
            # ⚠️ 关键：拖拽结束通知
            if self.parent() and hasattr(self.parent().parent(), '_on_drag_finished'):
                try:
                    self.parent().parent()._on_drag_finished()
                except:
                    pass
                    
        except Exception as e:
            print(f"拖拽异常: {e}")
            import traceback
            traceback.print_exc()
    
    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_position = None
        super().mouseReleaseEvent(event)


class MultiSelectListWidget(QListWidget):
    """支持多选、Ctrl+A 全选、拖拽排序、自动滚动，并带分组按钮"""
    orderChanged = pyqtSignal()

    def __init__(self, parent=None, groups_file: Path = None, widget_id: str = "default"):
        """
        :param groups_file: 保存分组的 json 文件路径，如果为 None 则不持久化
        :param widget_id: 在同一个文件中区分不同窗口的分组
        """
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)

        # 自动滚动参数
        self._auto_scroll_margin = 40
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.timeout.connect(self._auto_scroll)
        self._scroll_speed = 0

        # 拖拽参数
        self.is_dragging = False
        self._is_handling_drag = False
        # 拖拽方向追踪
        self._last_drag_pos = None  # 上一次拖拽位置
        self._drag_direction_x = 0  # 1=向右, -1=向左, 0=未知
        self._drag_direction_y = 0  # 1=向下, -1=向上, 0=未知

        # 分组管理
        self.groups: dict[str, list[str]] = {}  # {组名: [item文本,...]}
        self.current_group = "所有"
        self.all_items: list[str] = []

        # 顶部按钮容器
        self._setup_top_buttons()

        # 配置文件相关
        self.groups_file: Path | None = groups_file
        self.widget_id: str = widget_id
        if self.groups_file:
            self.load_groups()

    # ------------------- 顶部按钮 -------------------
    def _setup_top_buttons(self):
        """设置顶部按钮栏 - Chrome书签栏风格"""
        self.top_bar = QWidget(self)
        self.top_bar.setFixedHeight(50)
        
        main_layout = QHBoxLayout(self.top_bar)
        main_layout.setContentsMargins(6, 10, 25, 10)
        main_layout.setSpacing(6)
        
        # 存储按钮顺序和可见性
        self.button_order = []
        self.visible_buttons = []
        self.overflow_buttons = []
        
        # "所有"按钮（固定，不可拖拽）
        self.btn_all = QPushButton("所有", self.top_bar)
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)
        self.btn_all.clicked.connect(lambda: self._switch_group("所有"))
        main_layout.addWidget(self.btn_all)
        
        # 动态按钮容器 - 关键修复：不设置父对象，让layout管理
        self.dynamic_scroll = QWidget()
        self.dynamic_layout = QHBoxLayout(self.dynamic_scroll)
        self.dynamic_layout.setContentsMargins(0, 0, 0, 0)
        self.dynamic_layout.setSpacing(6)
        main_layout.addWidget(self.dynamic_scroll, 1)
        
        # ">>" 溢出按钮
        self.btn_overflow = QPushButton(self.top_bar)
        self.btn_overflow.setIcon(QIcon("icon/more.png"))
        self.btn_overflow.setToolTip("更多分组")
        self.btn_overflow.setCheckable(True)
        self.btn_overflow.setChecked(False)
        self.btn_overflow.hide()
        main_layout.addWidget(self.btn_overflow)

        # 溢出菜单实例
        self.overflow_menu = None
        self.is_dragging = False

        # 连接信号
        self.btn_overflow.clicked.connect(self._toggle_overflow_menu)
        
        # "添加分组"按钮
        self.btn_add_group = QPushButton(self.top_bar)
        self.btn_add_group.setToolTip("新建分组")
        self.btn_add_group.setIcon(QIcon("icon/add.png"))
        self.btn_add_group.clicked.connect(lambda: self._group_dialog(None))
        main_layout.addWidget(self.btn_add_group)
        
        # "管理分组"按钮
        self.btn_manage_groups = QPushButton(self.top_bar)
        self.btn_manage_groups.setToolTip("管理分组")
        self.btn_manage_groups.setIcon(QIcon("icon/edit.png"))
        self.btn_manage_groups.clicked.connect(self._manage_groups_dialog)
        main_layout.addWidget(self.btn_manage_groups)

        main_layout.addStretch()
        
        # 统一样式
        button_style = """
            QPushButton {
                background-color: #f0f0f0;
                border: none;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #ced4da;
                color: #495057;
            }
            QPushButton:checked {
                background-color: #e8f0fe;
                color: #1967d2;
                border: none;
            }
        """
        self.btn_all.setStyleSheet(button_style)
        self.btn_overflow.setStyleSheet(button_style)
        self.btn_add_group.setStyleSheet(button_style)
        self.btn_manage_groups.setStyleSheet(button_style)
        
        self.setViewportMargins(0, 50, 0, 0)
        self.top_bar.setGeometry(0, 0, self.width(), 50)
        self.group_buttons = {"所有": self.btn_all}
        
        # 设置拖放
        self.top_bar.setAcceptDrops(True)
        self.top_bar.dragEnterEvent = self._top_bar_dragEnterEvent
        self.top_bar.dragMoveEvent = self._top_bar_dragMoveEvent
        self.top_bar.dropEvent = self._top_bar_dropEvent

    # ==================== 拖拽插入指示器 ====================
    def _init_drop_indicator(self):
        """初始化拖拽插入指示器"""
        if not hasattr(self, '_drop_indicator'):
            self._drop_indicator = QFrame(None)
            self._drop_indicator.setWindowFlags(
                Qt.WindowType.FramelessWindowHint |
                Qt.WindowType.WindowStaysOnTopHint |
                Qt.WindowType.ToolTip  # 保持浮在最上层
            )
            self._drop_indicator.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            # ✅ 核心：完全不接收输入事件
            self._drop_indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            # Qt 6.6+ 可以直接使用这个
            self._drop_indicator.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)

            self._drop_indicator.setStyleSheet("""
                background-color: rgba(41, 44, 47, 0.8);
                border: none;
            """)
            self._drop_indicator.hide()

    def _show_drop_indicator(self, pos=None, in_overflow=False, target_button=None, insert_before=True):
        """显示拖拽插入指示器（横竖统一逻辑）"""
        if not hasattr(self, '_drop_indicator'):
            self._init_drop_indicator()
        ind = self._drop_indicator

        if not target_button:
            ind.hide()
            return

        btn_pos = target_button.mapToGlobal(QPoint(0, 0))
        btn_w, btn_h = target_button.width(), target_button.height()
        line_thickness = 2

        # 横竖线计算
        if in_overflow:
            y = btn_pos.y() - line_thickness//2 if insert_before else btn_pos.y() + btn_h - line_thickness//2
            ind.setGeometry(btn_pos.x(), y, btn_w, line_thickness)
        else:
            x = btn_pos.x() - line_thickness//2 if insert_before else btn_pos.x() + btn_w - line_thickness//2
            ind.setGeometry(x, btn_pos.y(), line_thickness, btn_h)

        ind.show()
        ind.raise_()

    def _hide_drop_indicator(self):
        if hasattr(self, '_drop_indicator'):
            self._drop_indicator.hide()

    # ==================== 拖拽开始/结束 ====================
    def _update_drag_direction(self, current_pos: QPoint):
        """更新拖拽方向 - 优化版：立即更新，不需要阈值"""
        if self._last_drag_pos is None:
            self._last_drag_pos = current_pos
            return
        
        delta_x = current_pos.x() - self._last_drag_pos.x()
        delta_y = current_pos.y() - self._last_drag_pos.y()
        
        if delta_x != 0:
            self._drag_direction_x = 1 if delta_x > 0 else -1
        if delta_y != 0:
            self._drag_direction_y = 1 if delta_y > 0 else -1
        
        self._last_drag_pos = current_pos


    def _on_drag_started(self):
        """拖拽开始时重置方向"""
        if hasattr(self, '_is_handling_drag') and self._is_handling_drag:
            return
        self._is_handling_drag = True
        try:
            self.is_dragging = True
            self._last_drag_pos = None
            self._drag_direction_x = 0
            self._drag_direction_y = 0
            if self.overflow_buttons and not self.btn_overflow.isChecked():
                self.btn_overflow.setChecked(True)
                self._toggle_overflow_menu()
            self._init_drop_indicator()
        finally:
            self._is_handling_drag = False

    def _on_drag_finished(self):
        """拖拽结束时的处理"""
        self.is_dragging = False
        self._hide_drop_indicator()
        if self.overflow_menu:
            self.overflow_menu.removeEventFilter(self)
        
    def _on_menu_item_clicked(self, button_id):
        """溢出菜单项点击事件"""
        self._switch_group(button_id)
        if self.overflow_menu:
            self.overflow_menu.hide()
        self.btn_overflow.setChecked(False)


    # ==================== 通用的按钮检测逻辑 ====================
    def _find_target_button_in_area(self, global_pos: QPoint, is_overflow: bool):
        """
        在指定区域查找目标按钮（支持缝隙容错）
        返回: (target_button, button_id, insert_before) 或 (None, None, None)
        """
        target_button = None
        target_btn_id = None
        insert_before = True
        min_distance = float('inf')  # 🆕 记录最小距离

        if is_overflow and self.overflow_menu and self.overflow_menu.isVisible():
            scroll_area = self.overflow_menu.findChild(QScrollArea)
            if scroll_area and scroll_area.widget():
                container = scroll_area.widget()
                for i in range(container.layout().count()):
                    item = container.layout().itemAt(i)
                    if not item or not item.widget():
                        continue
                    btn = item.widget()
                    btn_rect = QRect(btn.mapToGlobal(QPoint(0, 0)), btn.size())
                    
                    # 🆕 扩展检测区域（增加容错边距）
                    expanded_rect = btn_rect.adjusted(-10, -10, 10, 10)
                    
                    if expanded_rect.contains(global_pos):
                        # 计算距离按钮中心的距离
                        distance = (global_pos - btn_rect.center()).manhattanLength()
                        
                        if distance < min_distance:
                            min_distance = distance
                            target_button = btn
                            target_btn_id = btn.property("button_id")
                            
                            if self._drag_direction_y > 0:
                                insert_before = False
                            elif self._drag_direction_y < 0:
                                insert_before = True
                            else:
                                insert_before = global_pos.y() < btn_rect.center().y()
        else:
            # 可见区域
            if self.visible_buttons:
                for btn_id in self.visible_buttons:
                    btn = self.group_buttons[btn_id]
                    btn_rect = QRect(btn.mapToGlobal(QPoint(0, 0)), btn.size())
                    
                    # 🆕 扩展检测区域
                    expanded_rect = btn_rect.adjusted(-5, -5, 5, 5)
                    
                    if expanded_rect.contains(global_pos):
                        distance = (global_pos - btn_rect.center()).manhattanLength()
                        
                        if distance < min_distance:
                            min_distance = distance
                            target_button = btn
                            target_btn_id = btn_id
                            
                            if self._drag_direction_x > 0:
                                insert_before = False
                            elif self._drag_direction_x < 0:
                                insert_before = True
                            else:
                                insert_before = global_pos.x() < btn_rect.center().x()
        
        return target_button, target_btn_id, insert_before


    def _calculate_insert_index(self, target_btn_id: str, insert_before: bool):
        """计算插入索引"""
        if not target_btn_id or target_btn_id not in self.button_order:
            return None
        
        btn_idx = self.button_order.index(target_btn_id)
        return btn_idx if insert_before else btn_idx + 1


    def _perform_drop(self, button_id: str, target_btn_id: str, insert_before: bool):
        """执行拖拽放置操作"""
        if button_id not in self.group_buttons or button_id == "所有":
            return False
        
        insert_index = self._calculate_insert_index(target_btn_id, insert_before)
        if insert_index is None:
            return False
        
        # 移除原位置
        if button_id in self.button_order:
            old_idx = self.button_order.index(button_id)
            self.button_order.remove(button_id)
            if old_idx < insert_index:
                insert_index -= 1
        
        # 插入新位置
        self.button_order.insert(insert_index, button_id)
        return True


    # ==================== 顶部栏拖拽事件 ====================
    def _top_bar_dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def _top_bar_dragMoveEvent(self, event):
        """顶部栏拖拽移动事件"""
        button_id = event.mimeData().text()
        if button_id not in self.group_buttons:
            event.ignore()
            self._hide_drop_indicator()
            return

        global_pos = QCursor.pos()
        self._update_drag_direction(global_pos)

        # 自动展开溢出菜单
        try:
            local_pos = event.position().toPoint()
        except Exception:
            local_pos = event.pos()
        
        if self.overflow_buttons and self.btn_overflow.isVisible():
            if self.btn_overflow.geometry().contains(local_pos) and not self.btn_overflow.isChecked():
                self.btn_overflow.setChecked(True)
                self._toggle_overflow_menu()

        # 先检测可见区
        target_button, _, insert_before = self._find_target_button_in_area(global_pos, is_overflow=False)
        in_overflow = False
        
        # 再检测溢出区
        if not target_button:
            target_button, _, insert_before = self._find_target_button_in_area(global_pos, is_overflow=True)
            in_overflow = True
  
        event.acceptProposedAction()
        self._show_drop_indicator(None, in_overflow=in_overflow, target_button=target_button, insert_before=insert_before)

    def _top_bar_dropEvent(self, event):
        """顶部栏放置事件"""
        button_id = event.mimeData().text()
        if button_id not in self.group_buttons or button_id == "所有":
            self._hide_drop_indicator()
            event.ignore()
            return

        try:
            global_pos = QCursor.pos()
            
            # 先检测可见区
            _, target_btn_id, insert_before = self._find_target_button_in_area(global_pos, is_overflow=False)
            
            # 再检测溢出区
            if not target_btn_id:
                _, target_btn_id, insert_before = self._find_target_button_in_area(global_pos, is_overflow=True)

            if self._perform_drop(button_id, target_btn_id, insert_before):
                QTimer.singleShot(0, lambda: self._delayed_update_after_drop())
                QTimer.singleShot(0, self._refresh_overflow_menu)
                event.acceptProposedAction()
            else:
                event.ignore()
        except Exception as e:
            print(f"放置事件处理出错: {e}")
            import traceback
            traceback.print_exc()
            event.ignore()
        finally:
            self._hide_drop_indicator()
            self._last_drag_pos = None
            self._drag_direction_x = 0
            self._drag_direction_y = 0

    def _delayed_update_after_drop(self):
        """延迟更新 UI（在拖拽完成后）"""
        try:
            self._update_button_visibility()
            self._save_button_order()
            self._hide_drop_indicator()
        except Exception as e:
            print(f"延迟更新出错: {e}")


    # ==================== 溢出菜单拖拽 ====================
    def _overflow_menu_dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def _overflow_menu_dragMoveEvent(self, event):
        button_id = event.mimeData().text()
        if button_id not in self.group_buttons:
            event.ignore()
            return
        
        event.acceptProposedAction()
        global_pos = QCursor.pos()
        
        target_button, _, insert_before = self._find_target_button_in_area(global_pos, is_overflow=True)
        
        if target_button:
            self._show_drop_indicator(None, in_overflow=True, target_button=target_button, insert_before=insert_before)
        else:
            self._hide_drop_indicator()

    def _overflow_menu_dropEvent(self, event):
        button_id = event.mimeData().text()
        if button_id not in self.group_buttons or button_id == "所有":
            event.ignore()
            return

        try:
            global_pos = QCursor.pos()
            _, target_btn_id, insert_before = self._find_target_button_in_area(global_pos, is_overflow=True)

            if self._perform_drop(button_id, target_btn_id, insert_before):
                QTimer.singleShot(0, lambda: self._delayed_update_after_drop())
                QTimer.singleShot(0, self._refresh_overflow_menu)
                event.acceptProposedAction()
            else:
                event.ignore()
        except Exception as e:
            print(f"溢出菜单放置事件处理出错: {e}")
            import traceback
            traceback.print_exc()
            event.ignore()
        finally:
            self._hide_drop_indicator()

    def _toggle_overflow_menu(self):
        """点击按钮显示/隐藏溢出菜单"""
        # 如果正在拖拽且菜单已显示，不要隐藏
        if self.is_dragging and self.overflow_menu and self.overflow_menu.isVisible():
            return
            
        # 如果菜单已显示，则隐藏
        if self.overflow_menu and self.overflow_menu.isVisible():
            self.overflow_menu.hide()
            self.btn_overflow.setChecked(False)
            return
        
        # 每次都重新创建菜单以确保内容是最新的
        if self.overflow_menu:
            self.overflow_menu.deleteLater()
        
        self.overflow_menu = QMenu(self)
        self.overflow_menu.setAcceptDrops(True)
        
        # 拖拽时不要自动隐藏菜单
        def on_about_to_hide():
            if not self.is_dragging:
                self.btn_overflow.setChecked(False)
        
        self.overflow_menu.aboutToHide.connect(on_about_to_hide)
        
        # ⚠️ 关键：为菜单添加拖拽事件处理
        self.overflow_menu.dragEnterEvent = self._overflow_menu_dragEnterEvent
        self.overflow_menu.dragMoveEvent = self._overflow_menu_dragMoveEvent
        self.overflow_menu.dropEvent = self._overflow_menu_dropEvent

        scroll_area = QScrollArea()
        scroll_area.setObjectName("scroll_area")
        scroll_area.setContentsMargins(0, 0, 0, 0)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setAcceptDrops(True)
        
        # ⚠️ 关键：为滚动区域添加拖拽事件处理
        scroll_area.dragEnterEvent = self._overflow_menu_dragEnterEvent
        scroll_area.dragMoveEvent = self._overflow_menu_dragMoveEvent
        scroll_area.dropEvent = self._overflow_menu_dropEvent

        container = QWidget()
        container.setObjectName("scroll_widget")
        container.setAcceptDrops(True)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        
        # ⚠️ 关键：为容器添加拖拽事件处理
        container.dragEnterEvent = self._overflow_menu_dragEnterEvent
        container.dragMoveEvent = self._overflow_menu_dragMoveEvent
        container.dropEvent = self._overflow_menu_dropEvent

        btn_style = """
            QPushButton {
                background-color: #f0f0f0;
                border: none;
                border-radius: 6px;
                padding: 4px 10px;
                text-align: center;
                margin: 5px 20px 5px 5px;
            }
            QPushButton:hover {
                background-color: #ced4da;
                color: #495057;
            }
            QPushButton:checked {
                background-color: #e8f0fe;
                color: #1967d2;
                border: none;
            }
        """

        # 添加所有溢出按钮，无数量限制
        for button_id in self.overflow_buttons:
            if button_id in self.group_buttons:
                btn = self.group_buttons[button_id]

                # 使用 DraggableButton 以支持拖拽
                item_btn = DraggableButton(btn.text(), button_id, container)
                item_btn.setStyleSheet(btn_style)
                item_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                item_btn.setCheckable(True)
                item_btn.setChecked(btn.isChecked())
                item_btn.setProperty("button_id", button_id)
                item_btn.clicked.connect(lambda checked, bid=button_id: self._on_menu_item_clicked(bid))
                item_btn.mouseDoubleClickEvent = lambda e, b=btn: self._group_dialog(b.button_id)
                container_layout.addWidget(item_btn)

        scroll_area.setWidget(container)
        widget_action = QWidgetAction(self.overflow_menu)
        widget_action.setDefaultWidget(scroll_area)
        self.overflow_menu.addAction(widget_action)

        max_height = int(self.height() * 0.7)
        scroll_area.setMaximumHeight(max_height)

        # 显示菜单
        pos = self.btn_overflow.mapToGlobal(QPoint(0, self.btn_overflow.height()))
        pos.setX(pos.x() - 5)  # 向左偏移5像素
        self.overflow_menu.popup(pos)
        self.btn_overflow.setChecked(True)

    def _refresh_overflow_menu(self):
        """更平滑地刷新溢出菜单内容"""
        if not self.overflow_menu or not self.overflow_menu.isVisible():
            return

        # 获取滚动区域的容器
        scroll_area = self.overflow_menu.findChild(QScrollArea, "scroll_area")
        if not scroll_area:
            return

        container = scroll_area.widget()
        if not container:
            return

        layout = container.layout()

        # 清除旧按钮
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # 重新添加更新后的溢出按钮
        for button_id in self.overflow_buttons:
            if button_id in self.group_buttons:
                btn = self.group_buttons[button_id]
                item_btn = DraggableButton(btn.text(), button_id, container)
                item_btn.setStyleSheet(btn.styleSheet())
                item_btn.setCheckable(True)
                item_btn.setChecked(btn.isChecked())
                item_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                item_btn.setProperty("button_id", button_id)
                item_btn.clicked.connect(lambda checked, bid=button_id: self._on_menu_item_clicked(bid))
                layout.addWidget(item_btn)

    def _on_menu_item_clicked(self, button_id):
        """菜单项被点击 - 切换分组但不关闭菜单"""
        self._switch_group(button_id)
        
        if self.overflow_menu and self.overflow_menu.isVisible():
            for action in self.overflow_menu.actions():
                if isinstance(action, QWidgetAction):
                    scroll_area = action.defaultWidget()
                    if scroll_area:
                        container = scroll_area.widget()
                        if container:
                            for i in range(container.layout().count()):
                                item = container.layout().itemAt(i)
                                if item and item.widget():
                                    btn = item.widget()
                                    bid = btn.property("button_id")
                                    if bid:
                                        btn.setChecked(bid == self.current_group)
                    break

    def _add_group_button(self, name):
        """添加分组按钮"""
        if name in self.group_buttons:
            return

        btn = DraggableButton(name, name, None)
        btn.setCheckable(True)

        # 单击事件：切换分组
        btn.clicked.connect(lambda checked, n=name: self._on_group_button_clicked(n))

        # 双击事件：打开编辑对话框
        if name != "所有":
            btn.mouseDoubleClickEvent = lambda e, b=btn: self._group_dialog(b.button_id)

        btn.setStyleSheet("""
            QPushButton {
                background-color: #f0f0f0;
                border: none;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #ced4da;
                color: #495057;
            }
            QPushButton:checked {
                background-color: #e8f0fe;
                color: #1967d2;
                border: none;
            }
        """)

        # 动态自适应宽度
        fm = btn.fontMetrics()
        text_width = fm.horizontalAdvance(name)
        padding = 20  # 左右 padding 4+10 约等于 14，可根据样式微调
        btn.setMinimumWidth(text_width + padding)
        btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self.group_buttons[name] = btn
        if name not in self.button_order:
            self.button_order.append(name)

    def _update_button_visibility(self):
        """根据可用空间更新按钮显示，按钮宽度自适应文本"""
        if hasattr(self, '_updating_visibility') and self._updating_visibility:
            return

        self._updating_visibility = True
        try:
            BUTTON_SPACING = 6  # 固定间距

            # 清空布局（删除stretch除外）
            while self.dynamic_layout.count() > 0:
                item = self.dynamic_layout.itemAt(0)
                if item.widget():
                    widget = item.widget()
                    self.dynamic_layout.removeWidget(widget)
                    widget.hide()
                elif item.spacerItem():
                    self.dynamic_layout.removeItem(item)

            # 固定按钮宽度
            btn_all_width = self.btn_all.sizeHint().width()
            btn_add_width = self.btn_add_group.sizeHint().width()
            btn_manage_width = self.btn_manage_groups.sizeHint().width()
            overflow_btn_width = 30  # 溢出按钮宽度

            main_layout_margins = 6 + 25  # 左右边距
            main_layout_spacing = 6 * 5  # 间距

            # 计算每个按钮的动态宽度
            button_widths = {}
            for button_id in self.button_order:
                if button_id not in self.group_buttons or button_id == "所有":
                    continue
                btn = self.group_buttons[button_id]
                fm = btn.fontMetrics()
                text_width = fm.horizontalAdvance(btn.text())
                padding = 20  # 左右 padding，根据样式调整
                btn_width = text_width + padding
                btn.setFixedWidth(btn_width)
                button_widths[button_id] = btn_width

            # 判断是否需要溢出按钮
            total_button_count = len(button_widths)
            need_overflow_button = total_button_count > 1

            # 可用宽度计算
            if need_overflow_button:
                fixed_width = btn_all_width + btn_add_width + btn_manage_width + overflow_btn_width + main_layout_margins + main_layout_spacing
            else:
                fixed_width = btn_all_width + btn_add_width + btn_manage_width + main_layout_margins + main_layout_spacing

            available_width = max(0, self.top_bar.width() - fixed_width)

            # 排布按钮
            self.visible_buttons = []
            self.overflow_buttons = []
            accumulated_width = 0

            for button_id in self.button_order:
                if button_id not in button_widths:
                    continue
                btn_width = button_widths[button_id]
                needed_width = accumulated_width + BUTTON_SPACING + btn_width if self.visible_buttons else btn_width
                if needed_width <= available_width:
                    self.visible_buttons.append(button_id)
                    accumulated_width = needed_width
                else:
                    self.overflow_buttons.append(button_id)

            # 添加可见按钮到布局
            for button_id in self.visible_buttons:
                btn = self.group_buttons[button_id]
                self.dynamic_layout.addWidget(btn)
                btn.show()

            # 布局属性
            self.dynamic_layout.setSpacing(BUTTON_SPACING)
            self.dynamic_layout.setContentsMargins(0, 0, 0, 0)
            self.dynamic_layout.addStretch(1)  # 左对齐

            # 溢出按钮显示逻辑
            if need_overflow_button and self.overflow_buttons:
                self.btn_overflow.show()
            else:
                self.btn_overflow.hide()
                if hasattr(self, 'overflow_menu') and self.overflow_menu and self.overflow_menu.isVisible():
                    if not hasattr(self, 'is_dragging') or not self.is_dragging:
                        self.overflow_menu.close()

            # 强制刷新布局
            self.dynamic_layout.invalidate()
            self.dynamic_layout.activate()
            self.top_bar.update()

        finally:
            self._updating_visibility = False


    def _on_group_button_clicked(self, name):
        """分组按钮点击处理"""
        if hasattr(self, '_switching') and self._switching:
            return
        self._switch_group(name)

    def _switch_group(self, name):
        """切换到指定分组"""
        if hasattr(self, '_switching') and self._switching:
            return
            
        self._switching = True
        try:
            self.current_group = name
            self._highlight_group(name)
            
            self.blockSignals(True)
            self.clear()
            self.blockSignals(False)

            if name not in self.groups:
                self.groups[name] = []
            for text in self.groups[name]:
                super().addItem(text)
        finally:
            self._switching = False

    def _highlight_group(self, name):
        """高亮选中的分组"""
        for group, btn in self.group_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(group == name)
            btn.blockSignals(False)

    def resizeEvent(self, event):
        """窗口大小改变时的处理"""
        super().resizeEvent(event)
        self.top_bar.setGeometry(0, 0, self.width(), 50)
        if hasattr(self, '_update_button_visibility'):
            self._update_button_visibility()

    # ------------------- 持久化 -------------------
    def _save_button_order(self):
        """保存按钮顺序"""
        if not self.groups_file:
            return
        
        try:
            # 读取现有数据
            if self.groups_file.exists():
                data = json.loads(self.groups_file.read_text(encoding="utf-8"))
            else:
                data = {}
            
            # 确保 widget_id 数据结构存在
            if self.widget_id not in data:
                data[self.widget_id] = {"groups": {}, "button_order": []}
            
            # ⚠️ 确保使用嵌套结构
            if "groups" not in data[self.widget_id]:
                data[self.widget_id]["groups"] = {}
            
            # ⚠️ 去重并保存 button_order
            unique_order = []
            seen = set()
            for btn in self.button_order:
                if btn not in seen:
                    unique_order.append(btn)
                    seen.add(btn)
            
            data[self.widget_id]["button_order"] = unique_order
            
            # ⚠️ 清理旧的扁平结构数据
            keys_to_remove = []
            for key in data[self.widget_id].keys():
                if key not in ["groups", "button_order"]:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del data[self.widget_id][key]
            
            # 保存
            self.groups_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), 
                encoding="utf-8"
            )
        except Exception as e:
            print(f"保存按钮顺序失败: {e}")

    def save_groups(self):
        """保存分组数据"""
        if not self.groups_file:
            return
        
        try:
            # 读取现有数据
            if self.groups_file.exists():
                data = json.loads(self.groups_file.read_text(encoding="utf-8"))
            else:
                data = {}
            
            # ⚠️ 确保使用统一的嵌套结构
            if self.widget_id not in data:
                data[self.widget_id] = {}
            
            # ⚠️ 去重 button_order
            unique_order = []
            seen = set()
            for btn in self.button_order:
                if btn not in seen and btn in self.groups:  # 只保存存在的分组
                    unique_order.append(btn)
                    seen.add(btn)
            
            # 使用统一的数据结构
            data[self.widget_id] = {
                "groups": self.groups,
                "button_order": unique_order
            }
            
            # 保存
            self.groups_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), 
                encoding="utf-8"
            )
        except Exception as e:
            print(f"保存分组失败: {e}")

    def load_groups(self):
        """加载分组数据"""
        # ⚠️ 如果没有文件，直接跳过，不做任何初始化
        if not self.groups_file or not self.groups_file.exists():
            return

        try:
            data = json.loads(self.groups_file.read_text(encoding="utf-8"))
            widget_data = data.get(self.widget_id, {})

            # ⚠️ 如果当前 widget_id 没有数据，也跳过
            if not widget_data:
                return

            # 优先使用新的嵌套结构
            if "groups" in widget_data:
                self.groups = {str(k): v for k, v in widget_data["groups"].items()}
                self.button_order = [str(g) for g in widget_data.get("button_order", [])]
            else:
                # 兼容旧的扁平结构（迁移数据）
                self.groups = {}
                self.button_order = []
                for key, value in widget_data.items():
                    if isinstance(value, list):
                        self.groups[str(key)] = value
                        self.button_order.append(str(key))
                
                # 迁移后立即保存为新格式
                if self.groups:
                    self.save_groups()

        except Exception as e:
            print(f"加载分组失败: {e}")
            # ⚠️ 加载失败也直接返回，不做初始化
            return

        # 确保"所有"分组存在
        if "所有" not in self.groups:
            self.groups["所有"] = self.all_items.copy()
        else:
            self.all_items = self.groups["所有"].copy()

        # 去重 button_order
        unique_order = []
        seen = set()
        for group in self.button_order:
            if group not in seen and group in self.groups:
                unique_order.append(group)
                seen.add(group)
        self.button_order = unique_order

        # 补充 button_order 中缺失的分组
        for group in self.groups:
            if group not in self.button_order:
                self.button_order.append(group)

        # 初始化按钮
        for group in self.groups:
            if group not in self.group_buttons:
                self._add_group_button(group)

        self._update_button_visibility()
        self._switch_group("所有")

        
# ------------------- 分组逻辑 -------------------
    def addItem(self, *args):
        super().addItem(*args)
        text = args[0] if isinstance(args[0], str) else args[0].text()

        if text not in self.all_items:
            self.all_items.append(text)

        if "所有" not in self.groups:
            self.groups["所有"] = []
        if text not in self.groups["所有"]:
            self.groups["所有"].append(text)

        self.save_groups()

    def removeItemsByNames(self, names: list):
        """根据名称列表删除项目，同时从所有组别中移除"""
        for name in names:
            if name in self.all_items:
                self.all_items.remove(name)
            for group_name in self.groups:
                if name in self.groups[group_name]:
                    self.groups[group_name].remove(name)

        self._switch_group(self.current_group)
        self.save_groups()

    def add_selected_to_group(self, group_name: str):
        """把当前选中的项目批量加入指定分组"""
        if group_name not in self.groups:
            return

        selected_texts = [item.text() for item in self.selectedItems()]
        for text in selected_texts:
            if text not in self.groups[group_name]:
                self.groups[group_name].insert(0, text)

        if self.current_group == group_name:
            self._switch_group(group_name)

        self.save_groups()

    def remove_selected_from_group(self, group_name: str):
        """把当前选中的项目从指定分组中移除"""
        if group_name not in self.groups:
            return

        selected_texts = [item.text() for item in self.selectedItems()]
        for text in selected_texts:
            if text in self.groups[group_name]:
                self.groups[group_name].remove(text)

        if self.current_group == group_name:
            self._switch_group(group_name)

        self.save_groups()

    def _group_dialog(self, group_name: str = None):
        """创建新分组或修改已有分组"""
        is_edit = group_name is not None
        dialog = QDialog(self)
        dialog.setWindowTitle("修改分组" if is_edit else "新建分组")
        layout = QVBoxLayout(dialog)

        #使用最新按钮名初始化
        name_edit = QLineEdit(group_name if is_edit else "")
        if is_edit:
            name_edit.selectAll()
        name_edit.setPlaceholderText("分组名称")
        layout.addWidget(name_edit)

        # 按钮布局
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_button = QPushButton("修改" if is_edit else "创建")
        ok_button.setObjectName("primaryButton")
        cancel_button = QPushButton("取消")
        btn_layout.addWidget(cancel_button)
        btn_layout.addWidget(ok_button)
        layout.addLayout(btn_layout)

        cancel_button.clicked.connect(dialog.reject)

        def on_confirm():
            new_name = name_edit.text().strip()
            if not new_name:
                QMessageBox.warning(dialog, "警告", "分组名称不能为空")
                name_edit.setFocus()
                return
            if new_name == "所有":
                QMessageBox.warning(dialog, "警告", "“所有”是保留名称，不能使用")
                name_edit.setFocus()
                return
            if new_name in self.groups and (not is_edit or new_name != group_name):
                QMessageBox.warning(dialog, "警告", f"分组“{new_name}”已存在")
                name_edit.setFocus()
                return

            # 验证通过
            dialog.accept()

            if is_edit and group_name in self.groups and group_name != new_name:
                # 修改已有分组
                self.groups[new_name] = self.groups.pop(group_name)

                # 更新按钮
                if group_name in self.group_buttons:
                    btn = self.group_buttons.pop(group_name)
                    btn.setText(new_name)
                    btn.button_id = new_name  # ⚠️ 更新按钮ID
                    # 保留双击事件
                    btn.mouseDoubleClickEvent = lambda e, b=btn: self._group_dialog(b.button_id)
                    # 更新单击事件
                    try:
                        btn.clicked.disconnect()
                    except Exception:
                        pass
                    btn.clicked.connect(lambda _, n=new_name: self._on_group_button_clicked(n))
                    self.group_buttons[new_name] = btn

                # 更新 button_order
                if group_name in self.button_order:
                    idx = self.button_order.index(group_name)
                    self.button_order[idx] = new_name

                # 如果当前分组是修改的分组，切换到新分组
                if self.current_group == group_name:
                    self._switch_group(new_name)

            elif not is_edit:
                # 创建新分组
                self.groups[new_name] = []
                self._add_group_button(new_name)
                if new_name not in self.button_order:
                    self.button_order.append(new_name)

            self.save_groups()
            self._update_button_visibility()

        ok_button.clicked.connect(on_confirm)
        name_edit.returnPressed.connect(on_confirm)
        dialog.exec()

    def _manage_groups_dialog(self):
        """管理分组窗口（删除/修改）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("管理分组")
        layout = QVBoxLayout(dialog)

        target_width = int(self.width() * 0.5)
        target_height = int(self.height() * 0.7)
        dialog.resize(target_width, target_height)

        group_box = QGroupBox("可用分组")
        group_layout = QVBoxLayout(group_box)
        layout.addWidget(group_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        group_layout.addWidget(scroll)

        scroll_content = QWidget()
        scroll_content.setObjectName("scroll_content")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(4)
        scroll.setWidget(scroll_content)
        scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)  # 确保从顶部开始排列

        checkboxes = []

        def update_edit_state():
            checked = [cb for cb in checkboxes if cb.isChecked()]
            edit_button.setEnabled(len(checked) == 1)

        def refresh_checkboxes():
            while scroll_layout.count():
                item = scroll_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            checkboxes.clear()

            for g in self.groups.keys():
                if g == "所有":
                    continue
                cb = QCheckBox(g)
                scroll_layout.addWidget(cb)
                checkboxes.append(cb)
                cb.stateChanged.connect(update_edit_state)

        refresh_checkboxes()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        delete_button = QPushButton("删除")
        edit_button = QPushButton("修改")
        add_button = QPushButton("新建")
        btn_layout.addWidget(delete_button)
        btn_layout.addWidget(edit_button)
        btn_layout.addWidget(add_button)
        layout.addLayout(btn_layout)

        def delete_selected():
            checked = [cb.text() for cb in checkboxes if cb.isChecked()]
            if not checked:
                return

            # 二次确认
            reply = QMessageBox.question(
                self, 
                "确认删除", 
                "确定要删除选中的分组吗？\n" + "\n\n".join(checked),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

            # 删除操作
            for g in checked:
                if g in self.groups:
                    self.groups.pop(g)
                if g in self.group_buttons:
                    btn = self.group_buttons.pop(g)
                    btn.deleteLater()
                if g in self.button_order:
                    self.button_order.remove(g)
            self.save_groups()
            self._switch_group("所有")
            self._update_button_visibility()
            refresh_checkboxes()
            update_edit_state()

        delete_button.clicked.connect(delete_selected)

        def edit_selected():
            checked = [cb.text() for cb in checkboxes if cb.isChecked()]
            if len(checked) != 1:
                return
            old_name = checked[0]
            self._group_dialog(old_name)
            refresh_checkboxes()
            update_edit_state()

        edit_button.clicked.connect(edit_selected)

        def add_selected():
            self._group_dialog(None)
            refresh_checkboxes()
            update_edit_state()

        add_button.clicked.connect(add_selected)

        update_edit_state()
        dialog.exec()

    # ------------------- 键盘逻辑 -------------------
    def keyPressEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_A:
            self.selectAll()
        else:
            super().keyPressEvent(event)

    # ------------------- 拖拽滚动逻辑 -------------------
    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)
        pos = event.position().toPoint()
        rect = self.viewport().rect()
        margin = getattr(self, "_auto_scroll_margin", 30)

        if pos.y() < rect.top() + margin:
            self._scroll_speed = -max(1, int((margin - (pos.y() - rect.top())) / 2))
            if not getattr(self, "_auto_scroll_timer", None).isActive():
                self._auto_scroll_timer.start(30)
        elif pos.y() > rect.bottom() - margin:
            self._scroll_speed = max(1, int((pos.y() - (rect.bottom() - margin)) / 2))
            if not getattr(self, "_auto_scroll_timer", None).isActive():
                self._auto_scroll_timer.start(30)
        else:
            self._scroll_speed = 0
            self._auto_scroll_timer.stop()

    def dropEvent(self, event):
        super().dropEvent(event)
        self._auto_scroll_timer.stop()
        # ------------------ 保存当前分组顺序 ------------------
        if self.current_group:
            self.groups[self.current_group] = [self.item(i).text() for i in range(self.count())]
            self.save_groups()
            self.orderChanged.emit()

    def _auto_scroll(self):
        if getattr(self, "_scroll_speed", 0) != 0:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + self._scroll_speed)

       

#主程序 主窗口 主线程
class MainWindow(QMainWindow):
    """主窗口"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bento")

        # 设置窗口图标
        BASE_DIR = Path(os.getcwd()).resolve()
        icon_path = BASE_DIR / "icon" / "Bento.ico"

        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            print(f"图标文件不存在: {icon_path}")

        # 获取屏幕尺寸
        screen = QApplication.primaryScreen()
        screen_geometry = screen.geometry()
        screen_width = screen_geometry.width()
        screen_height = screen_geometry.height()

        # 计算窗口大小
        window_width = int(screen_width / 4)
        window_height = int(screen_height / 2)

        # 设置窗口大小
        self.resize(window_width, window_height)

        # 设置最小宽度为屏幕宽度的 0.1
        self.setMinimumWidth(int(screen_width * 0.2))
                
        self.profiles = []
        self.browser_processes = []
        self.config_file = PROFILES_DIR / "browser_profiles.json"
        self.group_file = PROFILES_DIR / "groups.json"
        self.edit_dialog = None 
        
        self.init_ui()
        self.load_profiles()
    
    def download_chrome(self):
        """下载浏览器内核的界面处理"""
        dialog = KernelDownloader(self)
        
        def update_progress(percent, status):
            dialog.progress.setValue(percent)
            dialog.status_label.setText(status)
            QApplication.processEvents()
        
        dialog.show()
        QApplication.processEvents()
        
        # 在线程中下载
        success = KernelManager.download_chrome(update_progress)
        
        if success:
            QTimer.singleShot(1000, dialog.accept)
        else:
            QMessageBox.critical(self, "错误", "浏览器内核下载失败，请检查网络连接")
            dialog.reject()

        
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
    
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
    
        # 创建一个分组框
        group_box = QGroupBox("浏览器列表")
        # 分组框内部布局
        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(8)
        
        # 搜索框和按钮的水平布局
        search_btn_layout = QHBoxLayout()
        search_btn_layout.setSpacing(8)
        
        # 搜索框
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索浏览器")
        icon_path = os.path.join(os.getcwd(), "icon", "search.png")
        # 添加图标到右侧（纯装饰，不绑定事件）
        self.search_edit.addAction(
            QIcon(icon_path),
            QLineEdit.ActionPosition.TrailingPosition
        )
        self.search_edit.textChanged.connect(self.filter_profiles)
        search_btn_layout.addWidget(self.search_edit)
        
        # 新建浏览器按钮
        self.add_btn = QPushButton("新建浏览器")
        self.add_btn.setObjectName("primaryButton")
        self.add_btn.setMinimumHeight(36)
        self.add_btn.setMinimumWidth(36)
        self.add_btn.clicked.connect(self.add_profile)
        search_btn_layout.addWidget(self.add_btn)

        # 菜单按钮
        self.setup_menu_button()

        # 添加到布局
        search_btn_layout.addWidget(self.menu_button)
        
        group_layout.addLayout(search_btn_layout)
        
        # 配置列表
        self.profile_list = MultiSelectListWidget(
            parent=self,  # 父窗口
            groups_file=PROFILES_DIR / "groups.json",  # 分组配置文件
            widget_id="main_window"  # 可选，用于区分不同窗口
        )
        self.profile_list.itemDoubleClicked.connect(self.open_browser)
        self.profile_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.profile_list.customContextMenuRequested.connect(self.show_context_menu)
        self.profile_list.orderChanged.connect(self.save_profiles)
        group_layout.addWidget(self.profile_list)
        group_box.setLayout(group_layout)
        # 将分组框添加到主布局
        layout.addWidget(group_box, 1)
    
        # 状态信息
        self.status_label = QLabel("<span style='color: #00d26a;'>●</span> 就绪")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        central_widget.setLayout(layout)

    def filter_profiles(self, text):
        """根据搜索框内容过滤列表"""
        text = text.lower()
        for i in range(self.profile_list.count()):
            item = self.profile_list.item(i)
            item.setHidden(text not in item.text().lower())
        
    def show_context_menu(self, position):
        """显示右键菜单"""
        selected_items = self.profile_list.selectedItems()
        if not selected_items:
            return

        menu = QMenu(self)

        if len(selected_items) == 1:
            open_action = QAction("打开", self)
            edit_action = QAction("编辑", self)
            open_action.triggered.connect(self.open_browser)
            edit_action.triggered.connect(self.edit_profile)
            menu.addAction(open_action)
            menu.addAction(edit_action)
            menu.addSeparator()

        delete_action = QAction("删除", self)
        delete_action.triggered.connect(self.delete_profile)
        menu.addAction(delete_action)
        menu.addSeparator()

        # 添加至分组 → 弹窗选择
        if self.profile_list.groups:
            add_to_group_action = QAction("添加至分组", self)
            add_to_group_action.triggered.connect(self._add_to_group_dialog)
            menu.addAction(add_to_group_action)

        #移出此分组 ------------------
        current_group = self.profile_list.current_group
        if current_group != "所有":
            remove_from_group_action = QAction("移出此分组", self)

            def remove_from_group():
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("确认移出")
                msg_box.setText(f"确定要将选中的项目从分组 '{current_group}' 移出吗？")
                msg_box.setIcon(QMessageBox.Icon.Question)

                # 移除默认按钮
                msg_box.setStandardButtons(QMessageBox.StandardButton.NoButton)

                # 添加自定义按钮
                yes_button = QPushButton("移出")
                no_button = QPushButton("取消")
                no_button.setObjectName("primaryButton")
                msg_box.addButton(yes_button, QMessageBox.ButtonRole.YesRole)
                msg_box.addButton(no_button, QMessageBox.ButtonRole.NoRole)

                # 显示弹窗并等待用户点击
                msg_box.exec()

                if msg_box.clickedButton() == yes_button:
                    self.profile_list.remove_selected_from_group(current_group)
                # 点击“否”则什么都不做

            remove_from_group_action.triggered.connect(remove_from_group)
            menu.addAction(remove_from_group_action)

        menu.exec(QCursor.pos())

    def _add_to_group_dialog(self):
        """弹出窗口，让用户选择要添加/移除到哪些分组"""
        groups = [g for g in self.profile_list.groups.keys() if g != "所有"]
        if not groups:
            QMessageBox.information(self, "提示", "没有可用分组")
            return

        selected_texts = [item.text() for item in self.profile_list.selectedItems()]
        if not selected_texts:
            QMessageBox.warning(self, "提示", "请先选择至少一个项目")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("添加/取消分组")
        layout = QVBoxLayout(dialog)

        # 获取主窗口尺寸
        main_width = self.width()
        main_height = self.height()

        # 计算目标尺寸
        target_width = int(main_width * 0.5)
        target_height = int(main_height * 0.5)

        # 设置窗口大小
        dialog.resize(target_width, target_height)

        # 创建一个统一的 QGroupBox 放所有复选框
        group_box = QGroupBox("可用分组")
        group_layout = QVBoxLayout(group_box)
        group_layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(group_box)

        # 在 QGroupBox 内创建滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)  # 内容自适应宽度
        group_layout.addWidget(scroll)

        # 创建一个 QWidget 作为滚动内容容器
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_content.setObjectName("scroll_content")
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(4)
        scroll.setWidget(scroll_content)

        # 设置布局默认从顶部开始排列
        scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        checkboxes = []
        for g in groups:
            cb = QCheckBox(g)
            if all(text in self.profile_list.groups[g] for text in selected_texts):
                cb.setChecked(True)
            scroll_layout.addWidget(cb)
            checkboxes.append(cb)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        ok_button = QPushButton("确定")
        ok_button.setObjectName("primaryButton")
        ok_button.clicked.connect(dialog.accept)

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(dialog.reject)

        btn_layout.addWidget(cancel_button)
        btn_layout.addWidget(ok_button)
        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            for i, group_name in enumerate(groups):
                cb = checkboxes[i]
                if cb.isChecked():
                    self.profile_list.add_selected_to_group(group_name)
                else:
                    for text in selected_texts:
                        if text in self.profile_list.groups[group_name]:
                            self.profile_list.groups[group_name].remove(text)
                    if self.profile_list.current_group == group_name:
                        self.profile_list._switch_group(group_name)
            self.profile_list.save_groups()


    def close_edit_dialog(self):
        """关闭 EditProfileDialog 对话框，相当于点击取消或关闭按钮"""
        if self.edit_dialog and self.edit_dialog.isVisible():
            self.edit_dialog.reject()  
            self.edit_dialog = None

    def show_presets(self):
        """批量选择预设并创建浏览器"""
        dialog = QDialog(self)
        dialog.setWindowTitle("预设")

        # 获取主窗口尺寸
        main_width = self.width()
        main_height = self.height()

        # 计算目标尺寸
        target_width = int(main_width * 0.6)
        target_height = int(main_height * 0.8)

        # 设置窗口大小
        dialog.resize(target_width, target_height)
        layout = QVBoxLayout(dialog)

        # 搜索框
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("搜索预设")
        icon_path = os.path.join(os.getcwd(), "icon", "search.png")
        search_edit.addAction(QIcon(icon_path), QLineEdit.ActionPosition.TrailingPosition)
        layout.addWidget(search_edit)

        # 列表
        list_widget = MultiSelectListWidget(
            parent=self,
            groups_file=PROFILES_DIR / "groups.json",
            widget_id="preset_window"
        )
        list_widget.clear()

        groups_path = PROFILES_DIR / "groups.json"

        # 读取 groups.json
        if groups_path.exists():
            with open(groups_path, "r", encoding="utf-8") as f_json:
                json_data = json.load(f_json)
        else:
            json_data = {}

        preset_window = json_data.setdefault("preset_window", {})
        preset_order = preset_window.get("所有", [])

        added_names = set()

        # 先按保存顺序添加
        for name in preset_order:
            if any(p[0] == name for p in PRESETS) and name not in added_names:
                list_widget.addItem(name)
                added_names.add(name)

        # 检查 PRESETS 中是否有新项目（没在 preset_order 里）
        new_added = False
        for preset_name, *_ in PRESETS:
            if preset_name not in added_names:
                list_widget.addItem(preset_name)
                preset_order.append(preset_name)  # 同步加入到顺序列表
                added_names.add(preset_name)
                new_added = True

        # 3如果有新项目，写回 groups.json
        if new_added:
            preset_window["所有"] = preset_order
            with open(groups_path, "w", encoding="utf-8") as f_json:
                json.dump(json_data, f_json, ensure_ascii=False, indent=2)

        layout.addWidget(list_widget, 1)

        layout.addWidget(list_widget, 1)

        # 搜索过滤
        def filter_presets(text):
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                item.setHidden(text.lower() not in item.text().lower())
        search_edit.textChanged.connect(filter_presets)

        # 列表右键菜单绑定
        list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def show_context_menu(position):
            """显示右键菜单"""
            selected_items = list_widget.selectedItems()
            if not selected_items:
                return

            menu = QMenu(self)
            if list_widget.groups:
                add_to_group_action = QAction("添加至分组", self)
                add_to_group_action.triggered.connect(_add_to_group_dialog)
                menu.addAction(add_to_group_action)

            current_group = list_widget.current_group

            if current_group != "所有":
                remove_from_group_action = QAction("移出此分组", self)

                def remove_from_group():
                    msg_box = QMessageBox(self)
                    msg_box.setWindowTitle("确认移出")
                    msg_box.setText(f"确定要将选中的项目从分组 '{current_group}' 移出吗？")
                    msg_box.setIcon(QMessageBox.Icon.Question)

                    # 移除默认按钮
                    msg_box.setStandardButtons(QMessageBox.StandardButton.NoButton)

                    # 添加自定义按钮
                    yes_button = QPushButton("移出")
                    no_button = QPushButton("取消")
                    no_button.setObjectName("primaryButton")
                    msg_box.addButton(yes_button, QMessageBox.ButtonRole.YesRole)
                    msg_box.addButton(no_button, QMessageBox.ButtonRole.NoRole)

                    # 显示弹窗并等待用户点击
                    msg_box.exec()

                    if msg_box.clickedButton() == yes_button:
                        list_widget.remove_selected_from_group(current_group)
                    # 点击“否”则什么都不做

                remove_from_group_action.triggered.connect(remove_from_group)
                menu.addAction(remove_from_group_action)

            menu.exec(QCursor.pos())

        list_widget.customContextMenuRequested.connect(show_context_menu)

        # 添加/移除分组弹窗
        def _add_to_group_dialog():
            """弹出窗口，让用户选择要添加/移除到哪些分组"""
            groups = [g for g in list_widget.groups.keys() if g != "所有"]
            if not groups:
                QMessageBox.information(self, "提示", "没有可用分组")
                return

            selected_texts = [item.text() for item in list_widget.selectedItems()]
            if not selected_texts:
                QMessageBox.warning(self, "提示", "请先选择至少一个项目")
                return

            dialog2 = QDialog(self)
            dialog2.setWindowTitle("添加/取消分组")

            target_width = int(self.width() * 0.5)
            target_height = int(self.height() * 0.5)
            dialog2.resize(target_width, target_height)

            layout2 = QVBoxLayout(dialog2)

            # 创建 QGroupBox
            group_box = QGroupBox("可用分组")
            group_layout = QVBoxLayout(group_box)
            group_layout.setContentsMargins(10, 10, 10, 10)
            layout2.addWidget(group_box)

            # 创建滚动区域
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            group_layout.addWidget(scroll)

            # 滚动内容容器
            scroll_content = QWidget()
            scroll_content.setObjectName("scroll_content")
            scroll_layout = QVBoxLayout(scroll_content)
            scroll_layout.setContentsMargins(0, 0, 0, 0)
            scroll_layout.setSpacing(4)
            scroll_layout.setAlignment(Qt.AlignmentFlag.AlignTop)  # 内容顶部对齐
            scroll.setWidget(scroll_content)

            checkboxes = []
            for g in groups:
                cb = QCheckBox(g)
                if all(text in list_widget.groups[g] for text in selected_texts):
                    cb.setChecked(True)
                scroll_layout.addWidget(cb)
                checkboxes.append(cb)

            # 添加底部弹性空间
            scroll_layout.addStretch()

            # 把 group_box 添加到主布局
            layout2.addWidget(group_box)

            btn_layout = QHBoxLayout()
            btn_layout.addStretch()

            ok_button = QPushButton("确定")
            ok_button.setObjectName("primaryButton")
            ok_button.clicked.connect(dialog2.accept)

            cancel_button = QPushButton("取消")
            cancel_button.clicked.connect(dialog2.reject)

            btn_layout.addWidget(cancel_button)
            btn_layout.addWidget(ok_button)
            layout2.addLayout(btn_layout)

            if dialog2.exec() == QDialog.DialogCode.Accepted:
                for i, group_name in enumerate(groups):
                    cb = checkboxes[i]
                    if cb.isChecked():
                        list_widget.add_selected_to_group(group_name)
                    else:
                        for text in selected_texts:
                            if text in list_widget.groups[group_name]:
                                list_widget.groups[group_name].remove(text)
                        if list_widget.current_group == group_name:
                            list_widget._switch_group(group_name)
                list_widget.save_groups()
            
        # 应用选择
        def apply_multiple():
            selected_items = list_widget.selectedItems()
            if not selected_items:
                QMessageBox.warning(dialog, "警告", "请至少选择一个预设")
                return

            import uuid

            # 构建一个名字到 PRESETS 的映射，方便验证和查找
            presets_dict = {p[0]: p for p in PRESETS}

            added_count = 0
            for item in selected_items:
                name_text = item.text()

                # 验证是否在 PRESETS 中
                if name_text not in presets_dict:
                    QMessageBox.warning(dialog, "警告", f"预设 [{name_text}] 不存在于 PRESETS 中，已跳过")
                    continue

                name, path, url = presets_dict[name_text]

                # 自动生成唯一名称
                base_name = name
                counter = 1
                while any(p.name == name for p in self.profiles):
                    name = f"{base_name}_{counter}"
                    counter += 1

                # 生成独立用户数据目录，保证独立 Chromium 内核
                profile_dir = PROFILES_DIR / f"{name}_{uuid.uuid4().hex[:8]}"
                profile_dir.mkdir(parents=True, exist_ok=True)

                # 创建浏览器实例
                profile = BrowserProfile(name=name, start_url=url)
                profile.profile_path = str(profile_dir)  # 关键：每个 profile 独立路径
                self.profiles.append(profile)
                self.profile_list.addItem(profile.name)
                added_count += 1

            self.save_profiles()
            self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已批量添加 {added_count} 个浏览器")
            QTimer.singleShot(2000, lambda: self.status_label.setText(f"<span style='color: #00d26a;'>●</span> 就绪"))

            # 关闭窗口
            dialog.accept()
            self.close_edit_dialog()

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dialog.reject)
        ok_btn = QPushButton("应用")
        ok_btn.setObjectName("primaryButton")
        ok_btn.clicked.connect(apply_multiple)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        dialog.exec()

    
    # ---------- 添加浏览器 ----------
    def add_profile(self):
        dialog = EditProfileDialog(parent=self)
        self.edit_dialog = dialog
        if dialog.exec() == QDialog.DialogCode.Accepted:
            profile = dialog.get_profile()

            # 创建配置文件夹
            folder = PROFILES_DIR / profile.name
            folder.mkdir(exist_ok=True)
            profile.profile_path = str(folder)

            # 插入到 self.profiles（全局）
            self.profiles.insert(0, profile)

            # 插入到 QListWidget 顶部
            self.profile_list.insertItem(0, profile.name)

            # ------------------- 更新分组 -------------------
            # 确保 "所有" 分组存在
            if "所有" not in self.profile_list.groups:
                self.profile_list.groups["所有"] = []

            if profile.name not in self.profile_list.groups["所有"]:
                self.profile_list.groups["所有"].insert(0, profile.name)  # 插入最顶部

            # 如果当前分组不是 "所有"，也加入当前分组
            current_group = self.profile_list.current_group
            if current_group != "所有":
                if current_group not in self.profile_list.groups:
                    self.profile_list.groups[current_group] = []
                if profile.name not in self.profile_list.groups[current_group]:
                    self.profile_list.groups[current_group].insert(0, profile.name)

            # 持久化分组顺序
            self.profile_list.save_groups()
            # 持久化全局浏览器数据
            self.save_profiles()

            # 选中新添加的项目
            self.profile_list.setCurrentRow(0)

            self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已添加浏览器: {profile.name}")
            QTimer.singleShot(2000, lambda: self.status_label.setText("<span style='color: #00d26a;'>●</span> 就绪"))

    # ---------- 编辑浏览器 ----------
    def edit_profile(self):
        current_item = self.profile_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "警告", "请先选择一个浏览器")
            return

        profile = next((p for p in self.profiles if p.name == current_item.text()), None)
        if not profile:
            return

        dialog = EditProfileDialog(profile=profile, parent=self)
        self.edit_dialog = dialog
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.get_profile()
            new_name = updated.name.strip()

            # 文件夹重命名
            if new_name != profile.name:
                old_folder = PROFILES_DIR / profile.name
                new_folder = PROFILES_DIR / new_name
                try:
                    if old_folder.exists():
                        old_folder.rename(new_folder)
                    profile.profile_path = str(new_folder)
                    profile.name = new_name
                    self.profile_list.currentItem().setText(new_name)
                except Exception as e:
                    QMessageBox.warning(self, "错误", f"重命名失败: {e}")

            # 更新信息
            profile.start_url = updated.start_url
            profile.user_agent = updated.user_agent
            profile.proxy = updated.proxy
            profile.proxy_user = updated.proxy_user
            profile.proxy_pass = updated.proxy_pass

            self.save_profiles()
            self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已更新浏览器: {profile.name}")
            QTimer.singleShot(2000, lambda: self.status_label.setText("<span style='color: #00d26a;'>●</span> 就绪"))

#-----------以下是浏览器内核逻辑------------------------------------
    def create_proxy_extension(self, proxy_type, ip, port, user, password):
        """
        创建代理认证扩展
        :return: 扩展文件路径
        """
        # 创建临时目录
        temp_dir = tempfile.mkdtemp(prefix='proxy_auth_')
        
        # manifest.json
        manifest = {
            "version": "1.0.0",
            "manifest_version": 3,
            "name": "Proxy Auth",
            "permissions": [
                "webRequest",
                "webRequestAuthProvider"
            ],
            "host_permissions": ["<all_urls>"],
            "background": {
                "service_worker": "background.js"
            }
        }
        
        # background.js
        background_js = f"""
    chrome.webRequest.onAuthRequired.addListener(
        (details, callbackFn) => {{
            callbackFn({{
                authCredentials: {{
                    username: "{user}",
                    password: "{password}"
                }}
            }});
        }},
        {{ urls: ["<all_urls>"] }},
        ['asyncBlocking']
    );
    """
        
        # 写入文件
        manifest_path = pathlib.Path(temp_dir) / "manifest.json"
        background_path = pathlib.Path(temp_dir) / "background.js"
        
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)
        
        with open(background_path, 'w', encoding='utf-8') as f:
            f.write(background_js)
        
        return temp_dir


    def open_browser(self):
        """打开浏览器（支持代理认证）"""
        # --- 检查内核 ---
        if not KernelManager.is_chrome_installed():
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("警告")
            msg_box.setText("浏览器内核未安装，无法打开浏览器，是否现在安装？")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

            yes_button = msg_box.button(QMessageBox.StandardButton.Yes)
            yes_button.setObjectName("yesButton")

            msg_box.setStyleSheet("""
                QPushButton#yesButton {
                    background-color: #3656DA;
                    color: white;
                    border: none;
                }
                QPushButton#yesButton:hover {
                    background-color: #193ED5;
                }
                QPushButton#yesButton:pressed {
                    background-color: #0A1E6D;
                }
            """)

            reply = msg_box.exec()
            if reply != QMessageBox.StandardButton.Yes:
                return
            self.download_chrome()
            return

        # --- 获取选中配置 ---
        current_item = self.profile_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "警告", "请先选择一个浏览器")
            return

        profile_name = current_item.text()
        profile = next((p for p in self.profiles if p.name == profile_name), None)
        if not profile:
            return

        # --- 构建 Chrome 启动参数 ---
        chrome_path = KernelManager.get_chrome_path()
        profile_dir = pathlib.Path(profile.profile_path)
        profile_dir.mkdir(parents=True, exist_ok=True)

        args = [
            chrome_path,
            f"--user-data-dir={profile.profile_path}",
            "--no-first-run",
            "--no-default-browser-check"
        ]

        # 添加 User-Agent
        if profile.user_agent:
            args.insert(1, f"--user-agent={profile.user_agent}")

        # 添加代理
        extension_path = None
        if getattr(profile, "proxy", None) and profile.proxy:
            ip = profile.proxy.get("ip", "").strip()
            port = profile.proxy.get("port", "").strip()
            proxy_type = profile.proxy.get("type", "socks5").strip().lower()

            if ip and port:
                proxy_user = profile.proxy.get("user", "").strip()
                proxy_pass = profile.proxy.get("pass", "").strip()
                
                # 构建代理字符串（Chrome 格式）
                if proxy_type == "socks5":
                    proxy_scheme = "socks5"
                elif proxy_type == "http":
                    proxy_scheme = "http"
                elif proxy_type == "https":
                    proxy_scheme = "https"
                else:
                    proxy_scheme = "socks5"
                
                # Chrome 代理参数不包含用户名密码
                proxy_str = f"{proxy_scheme}://{ip}:{port}"
                args.insert(1, f"--proxy-server={proxy_str}")
                print(f"使用代理: {proxy_str}")
                
                # 如果有认证信息，创建扩展
                if proxy_user and proxy_pass:
                    extension_path = self.create_proxy_extension(
                        proxy_type, ip, port, proxy_user, proxy_pass
                    )
                    args.insert(1, f"--load-extension={extension_path}")
                    print(f"加载代理认证扩展: {extension_path}")

        # 添加启动 URL
        if profile.start_url:
            args.append(profile.start_url)

        # --- 启动浏览器 ---
        try:
            process = subprocess.Popen(args)
            self.browser_processes.append(process)
            
            # 保存扩展路径，以便后续清理
            if extension_path:
                if not hasattr(self, 'temp_extensions'):
                    self.temp_extensions = []
                self.temp_extensions.append(extension_path)
            
            self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已打开: {profile_name}")
            QTimer.singleShot(2000, lambda: self.status_label.setText(f"<span style='color: #00d26a;'>●</span> 就绪"))
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动浏览器失败: {str(e)}")


    def cleanup_extensions(self):
        """清理临时扩展目录（在 closeEvent 中调用）"""
        if hasattr(self, 'temp_extensions'):
            import shutil
            for ext_path in self.temp_extensions:
                try:
                    shutil.rmtree(ext_path, ignore_errors=True)
                except:
                    pass
            self.temp_extensions.clear()
#-----------以上是浏览器内核逻辑------------------------------------

    def delete_profile(self):
        """支持多选删除"""
        selected_items = self.profile_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择一个或多个浏览器")
            return

        names = [item.text() for item in selected_items]

        dialog = QDialog(self)
        dialog.setWindowTitle("确认删除")
        layout = QVBoxLayout(dialog)

        label = QLabel("确定要删除以下浏览器吗？（本地缓存数据将清空）\n\n" + "\n".join(names))
        layout.addWidget(label)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("primaryButton")
        cancel_btn.clicked.connect(dialog.reject)
        yes_btn = QPushButton("删除")
        yes_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(yes_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # 先删除本地文件夹
        for name in names:
            profile = next((p for p in self.profiles if p.name == name), None)
            if profile and profile.profile_path:
                try:
                    profile_path = pathlib.Path(profile.profile_path)
                    if profile_path.exists():
                        shutil.rmtree(profile_path)
                except Exception as e:
                    QMessageBox.warning(self, "警告", f"删除浏览器文件夹失败: {str(e)}")

        # 从 self.profiles 移除
        self.profiles = [p for p in self.profiles if p.name not in names]

        # 从 QListWidget 和各分组移除
        self.profile_list.removeItemsByNames(names)

        # 保存配置
        self.save_profiles()

        self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已删除 {len(names)} 个浏览器")
        QTimer.singleShot(2000, lambda: self.status_label.setText(f"<span style='color: #00d26a;'>●</span> 就绪"))


#---------以下是菜单项逻辑------------------------------------------------
    def setup_menu_button(self):
        """设置菜单按钮和相关功能"""
        # 创建菜单按钮
        self.menu_button = QPushButton()
        self.menu_button.setObjectName("menuButton")
        self.menu_button.setMinimumWidth(36)
        self.menu_button.setMinimumHeight(36)
        self.menu_button.setIcon(QIcon("icon/menu.png"))
        self.menu_button.setIconSize(QSize(16, 16))
        
        # 按钮样式定义
        self.normal_style = """
            QPushButton#menuButton {
                background-color: #f0f0f0;
                border: none;
                padding: 0px 0px 0px 0px;
                margin: 0px 0px 0px 0px;
                border-radius: 8px;
            }
        """
        
        self.hover_style = """
            QPushButton#menuButton {
                background-color: #292c2f;
                border: none;
                padding: 0px 0px 0px 0px;
                margin: 0px 0px 0px 0px;
                border-radius: 8px;
            }
        """
        
        # 设置初始样式
        self.menu_button.setStyleSheet(self.normal_style)
        
        # 安装事件过滤器并开启鼠标跟踪
        self.menu_button.setMouseTracking(True)
        self.menu_button.installEventFilter(self)
        
        # 创建菜单
        self.menu = QMenu(self)
        self.menu.setWindowFlags(self.menu.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # 添加菜单项     
        kernel_management = self.menu.addAction("内核管理")
        kernel_management.triggered.connect(self.kernel_management)
        self.menu.addSeparator()
        check_action = self.menu.addAction("检查更新")
        check_action.triggered.connect(self.check_update)
        
        # 安装事件过滤器并开启鼠标跟踪
        self.menu.setMouseTracking(True)
        self.menu.installEventFilter(self)
        
        # 连接菜单项点击信号
        self.menu.triggered.connect(self._on_menu_triggered)
        
        # leave_timer：当鼠标完全移出时，延迟隐藏菜单
        self.leave_timer = QTimer(self)
        self.leave_timer.setSingleShot(True)
        self.leave_timer.timeout.connect(self._try_hide)
        
        # click_block_timer：短暂屏蔽菜单重现的定时器
        self.click_block_timer = QTimer(self)
        self.click_block_timer.setSingleShot(True)
        self.click_block_timer.timeout.connect(self._reset_just_clicked)
        
        # 状态标记
        self.just_clicked = False
        self.ignore_menu_area = False
        
    def eventFilter(self, obj, event):
        """
        事件过滤器：处理菜单按钮和菜单的鼠标事件
        hover图标跟随菜单的显示隐藏，而不是实时跟随鼠标
        """
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.MouseMove, QEvent.Type.HoverMove):
            cursor_pos = QCursor.pos()
            
            # 计算按钮在屏幕上的全局矩形
            btn_top_left = self.menu_button.mapToGlobal(QPoint(0, 0))
            btn_rect_global = self.menu_button.rect().translated(btn_top_left)
            
            # 菜单的全局矩形
            menu_rect_global = self.menu.geometry()
            
            # 判断是否在相关区域内
            if self.ignore_menu_area:
                # 限制模式：只识别按钮区域
                in_relevant_area = btn_rect_global.contains(cursor_pos)
            else:
                # 正常模式：识别按钮或菜单区域
                in_relevant_area = btn_rect_global.contains(cursor_pos) or menu_rect_global.contains(cursor_pos)
            
            if in_relevant_area:
                if self.ignore_menu_area and btn_rect_global.contains(cursor_pos):
                    # 限制模式下鼠标在按钮上
                    if not self.just_clicked:
                        self.show_menu()  # 这里会处理图标显示
                    self.just_clicked = False
                    return super().eventFilter(obj, event)
                elif not self.ignore_menu_area:
                    # 正常模式
                    if self.just_clicked:
                        # 如果刚点击过菜单项，只停止隐藏定时器
                        if self.leave_timer.isActive():
                            self.leave_timer.stop()
                        return super().eventFilter(obj, event)
                    # 显示菜单（会处理图标显示）
                    self.show_menu()
            else:
                # 鼠标不在相关区域：启动延迟隐藏菜单（会处理图标隐藏）
                if self.menu.isVisible() and not self.leave_timer.isActive():
                    self.leave_timer.start(300)
        
        return super().eventFilter(obj, event)

    def show_menu(self):
        """显示菜单时同时显示hover图标"""
        # 菜单已经可见时，只停止隐藏定时器，不重复弹出
        if self.menu.isVisible():
            self.leave_timer.stop()
            return
        
        # 从按钮重新打开菜单时，恢复识别菜单区域
        self.ignore_menu_area = False
        # 取消任何待执行的隐藏操作
        self.leave_timer.stop()
        
        # 显示菜单时：设置hover样式和hover图标
        self.menu_button.setStyleSheet(self.hover_style)
        self.menu_button.setIcon(QIcon("icon/menu_h.png"))
        
        # 计算菜单位置
        button_rect = self.menu_button.rect()
        button_pos = self.menu_button.mapToGlobal(QPoint(0, 0))
        
        # 确保菜单已经布局完成，能获取正确尺寸
        self.menu.adjustSize()
        
        # 计算位置：在按钮下方显示
        menu_x = button_pos.x() - 5
        menu_y = button_pos.y() + button_rect.height() + 2
        
        # 显示菜单
        self.menu.popup(QPoint(int(menu_x), int(menu_y)))

    def _try_hide(self):
        """
        定时器超时后检查是否隐藏菜单
        只有真正隐藏菜单时才隐藏hover图标
        """
        cursor_pos = QCursor.pos()
        
        btn_top_left = self.menu_button.mapToGlobal(QPoint(0, 0))
        btn_rect_global = self.menu_button.rect().translated(btn_top_left)
        menu_rect_global = self.menu.geometry()
        
        # 根据当前模式判断相关区域
        if self.ignore_menu_area:
            in_relevant_area = btn_rect_global.contains(cursor_pos)
        else:
            in_relevant_area = btn_rect_global.contains(cursor_pos) or menu_rect_global.contains(cursor_pos)
        
        # 如果光标仍在相关区域，不做任何操作（保持菜单和hover图标显示）
        if in_relevant_area:
            return
        
        # 否则，隐藏菜单和hover图标
        self._hide_menu_and_reset()

    def _hide_menu_and_reset(self):
        """隐藏菜单时同时隐藏hover图标"""
        # 隐藏菜单
        self.menu.hide()
        # 恢复按钮正常状态：普通样式和普通图标
        self.menu_button.setStyleSheet(self.normal_style)
        self.menu_button.setIcon(QIcon("icon/menu.png"))

    def _on_menu_triggered(self, action):
        """
        菜单项被点击时：立即隐藏菜单和hover图标
        """
        # 屏蔽短时重新弹出
        self.just_clicked = True
        if self.click_block_timer.isActive():
            self.click_block_timer.stop()
        self.click_block_timer.start(200)
        
        # 去除对菜单区域的识别，直到下一次从按钮触发
        self.ignore_menu_area = True
        # 立即隐藏菜单和hover图标
        self._hide_menu_and_reset()

    def _reset_just_clicked(self):
        """200ms 后自动重置 just_clicked 标记"""
        self.just_clicked = False

    def kernel_management(self):
        """内核管理功能"""
        class KernelDialog(QDialog):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setWindowTitle("内核管理")
                self.resize(300, 250)
                
                self.layout = QVBoxLayout(self)
                
                # 内核列表
                self.kernel_list = QListWidget()
                self.layout.addWidget(self.kernel_list)

                # 按钮布局
                btn_layout = QHBoxLayout()
                self.update_btn = QPushButton("更新内核")
                self.delete_btn = QPushButton("删除内核")
                btn_layout.addWidget(self.update_btn)
                btn_layout.addWidget(self.delete_btn)
                self.layout.addLayout(btn_layout)

                # 加载内核列表
                self.load_kernels()

                # 按钮事件
                self.update_btn.clicked.connect(self.update_kernel)
                self.delete_btn.clicked.connect(self.delete_kernel)

                # 右键菜单（继承主窗口的QMenu样式）
                self.kernel_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                self.kernel_list.customContextMenuRequested.connect(self.show_context_menu)

            def load_kernels(self):
                """加载内核列表"""
                self.kernel_list.clear()
                if KERNEL_DIR.exists():
                    for exe_path in KERNEL_DIR.rglob("*.exe"):
                        if exe_path.name.lower() == "chrome.exe":
                            item = QListWidgetItem("Chromium内核")
                            item.setData(Qt.ItemDataRole.UserRole, exe_path)
                            self.kernel_list.addItem(item)

                # 默认选中第一个内核
                if self.kernel_list.count() > 0:
                    self.kernel_list.setCurrentRow(0)


            def confirm_action(self, title, text):
                """统一的确认弹窗"""
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle(title)
                msg_box.setText(text)
                msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

                no_button = msg_box.button(QMessageBox.StandardButton.No)
                no_button.setObjectName("noButton")

                msg_box.setStyleSheet("""
                    QPushButton#noButton {
                        background-color: #3656DA;
                        color: white;
                        border: none;
                    }
                    QPushButton#noButton:hover {
                        background-color: #193ED5;
                    }
                    QPushButton#noButton:pressed {
                       background-color: #0A1E6D;
                    }
                """)
                reply = msg_box.exec()
                return reply == QMessageBox.StandardButton.Yes

            def update_kernel(self):
                """更新内核（带确认）"""
                selected_items = self.kernel_list.selectedItems()
                if not selected_items:
                    QMessageBox.warning(self, "提示", "请先选择一个内核")
                    return

                item = selected_items[0]
                kernel_path = item.data(Qt.ItemDataRole.UserRole)
                if not kernel_path or not Path(kernel_path).exists():
                    QMessageBox.warning(self, "提示", "选中的内核不存在")
                    return

                # 二次确认
                if not self.confirm_action("确认更新", "确定要更新此内核吗？旧内核将被删除并重新下载。"):
                    return

                try:
                    parent_dir = kernel_path.parent
                    if parent_dir.parent == KERNEL_DIR:
                        shutil.rmtree(parent_dir)
                        print(f"已删除旧内核目录: {parent_dir}")
                except Exception as e:
                    QMessageBox.critical(self, "错误", f"删除旧内核失败: {e}")
                    return

                # 调用父窗口的下载逻辑
                self.parent().download_chrome()
                self.load_kernels()


            def delete_kernel(self):
                """删除选中的内核（带确认）"""
                selected_items = self.kernel_list.selectedItems()
                if not selected_items:
                    QMessageBox.warning(self, "提示", "请先选择一个内核")
                    return

                item = selected_items[0]
                kernel_path = item.data(Qt.ItemDataRole.UserRole)
                if not kernel_path or not Path(kernel_path).exists():
                    QMessageBox.warning(self, "提示", "选中的内核不存在")
                    return

                # 二次确认
                if not self.confirm_action("确认删除", "确定要删除此内核吗？操作不可恢复。"):
                    return

                try:
                    parent_dir = kernel_path.parent
                    if parent_dir.parent == KERNEL_DIR:
                        shutil.rmtree(parent_dir)
                        QMessageBox.information(self, "删除成功", f"已删除内核: {parent_dir}")
                        self.load_kernels()
                except Exception as e:
                    QMessageBox.critical(self, "删除失败", str(e))

            def show_context_menu(self, pos):
                """右键菜单（继承主窗口样式）"""
                item = self.kernel_list.itemAt(pos)
                if not item:
                    return
                kernel_path = item.data(Qt.ItemDataRole.UserRole)
                if not kernel_path:
                    return

                menu = QMenu(self)
                menu.setStyleSheet("")  # 继承主窗口样式
                open_folder_action = menu.addAction("打开内核所在位置")

                action = menu.exec(self.kernel_list.mapToGlobal(pos))
                if action == open_folder_action:
                    folder = kernel_path.parent
                    if folder.exists():
                        os.startfile(str(folder))

        dlg = KernelDialog(self)
        dlg.exec()


    #检查更新
    def check_update(self):
        """显示更新对话框"""
        dialog = UpdateDialog(self, CURRENT_VERSION)
        dialog.exec()

#---------以上是菜单项逻辑------------------------------------------------

    def closeEvent(self, event):
        """主窗口关闭时清理所有浏览器进程"""
        for process in self.browser_processes:
            try:
                process.terminate()
            except:
                pass
        event.accept()
    
    # ---------- 保存 ----------
    def save_profiles(self):
        try:
            # 写入文件
            data = [p.to_dict() for p in self.profiles]
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存配置失败: {e}")


    # ---------- 加载 ----------
    def load_profiles(self):
        if not os.path.exists(self.config_file):
            return
        try:
            # 1. 先加载配置文件到 self.profiles
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.profiles = [BrowserProfile.from_dict(p) for p in data]

            # 2. 加载分组及顺序
            self.profile_list.clear()
            if not os.path.exists(self.group_file):
                # 如果 group_file 不存在，就按 config_file 的顺序加载
                for p in self.profiles:
                    self.profile_list.addItem(p.name)
            else:
                # 否则按 groups.json 中“所有”分组顺序加载
                self.profile_list.load_groups()
                with open(os.path.join(PROFILES_DIR, "groups.json"), "r", encoding="utf-8") as f_json:
                    group_data = json.load(f_json)
                all_group_order = group_data.get("main_window", {}).get("所有", [])
                for name in all_group_order:
                    if any(p.name == name for p in self.profiles):
                        self.profile_list.addItem(name)

            # 3. 状态显示
            self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已加载 {len(self.profiles)} 个浏览器")
            QTimer.singleShot(2000, lambda: self.status_label.setText("<span style='color: #00d26a;'>●</span> 就绪"))

        except Exception as e:
            QMessageBox.warning(self, "错误", f"加载配置失败: {e}")


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        /* 全局字体设置 */
        * {
            font-family: 'Microsoft YaHei UI', 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', sans-serif;
        }
        
        /* 主窗口样式 */
        QMainWindow {
            background-color: #f8f9fa;
        }
                        
        QLabel#titleLabel {
            color: #495057;
        }

        /* 右键菜单样式 */
        QMenu {
            background-color: white;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 4px 0px;
            margin: 5px;
        }
        
        QMenu::item {
            padding: 8px 40px 8px 24px;  /* 上 右 下 左 */
            margin: 2px 4px;
            border-radius: 8px;
            color: #495057;
        }
        
        QMenu::item:selected {
            background-color: #f5f5f5;
            color: #1967d2;
        }
        
        QMenu::item:pressed {
            background-color: #bbdefb;
        }
        
        QMenu::separator {
            height: 1px;
            background-color: #dee2e6;
            margin: 4px 8px;
        }

        /* 输入框样式 */
        QLineEdit {
            border: 1px solid #dadce0;
            border-radius: 8px;
            padding: 8px 12px;
            background-color: white;
            selection-background-color: #007bff;
        }
        
        QLineEdit:focus {
            border-color: #007bff;
            outline: none;
        }

        QLineEdit:read-only {
            background-color: #f8f9fa;
            color: #6c757d;
        }                 

         /* 输出框样式 */
        QTextEdit {
            border: 1px solid #dadce0;
            border-radius: 8px;
            padding: 8px 12px;
            background-color: white;
            selection-background-color: #007bff;
        }
        
        QTextEdit:focus {
            border-color: #007bff;
            outline: none;
        }

        QTextEdit:read-only {
            background-color: #f8f9fa;
            color: #6c757d;
        }                     

        /* 下拉列表样式 */
        QComboBox QAbstractItemView {
            border: 1px solid #dadce0;
            border-radius: 6px;
            background: white;
            color: #495057;
            padding: 2px;  /* 列表整体内边距 */
            selection-background-color: #007bff;
            outline: none;
        }
        QComboBox QAbstractItemView::item {
            padding: 8px 2px;  /* 上下8px, 左右2px */
            color: #495057;
        }
                                                        
        /* 状态标签样式 */
        QLabel#statusLabel {
            border: 1px solid #dadce0;
            border-radius: 8px;
            padding: 4px;
            border-radius: 8px;
            padding: 6px 12px;
            color: #495057;
        }
                        
        /* 列表样式 */
        QListWidget {
            border: 1px solid #dadce0;
            border-radius: 8px;
            padding: 4px;
            background-color: #ffffff;
            outline: none;
        }
        QListWidget::item {
            padding: 12px 16px;
            border-radius: 8px;
            margin: 2px;
            color: #495057;
        }
        QListWidget::item:hover {
            background-color: #f5f5f5;
        }
        QListWidget::item:selected {
            background-color: #e8f0fe;
            color: #1967d2;
        }
                      
        /* 勾选框样式 */  
        QCheckBox {
            color: #495057;
        }
                        
        /* 按钮样式 */           
        QPushButton {
            padding: 10px 24px;
            border: none;
            border-radius: 8px;
            font-weight: 500;
            color: #495057;
            background-color: #ced4da;
        }

        QPushButton:hover {
            background-color: #292c2f;
            color: #ffffff;
            border: none;
        }

        QPushButton:pressed {
            background-color: #000000;
        }

        QPushButton:disabled {
            background-color: #e0e0e0; /* 灰色背景 */
            color: #a0a0a0;            /* 灰色文字 */
            border: none;
        }
                 
        QPushButton#primaryButton {
            background-color: #3656DA;
            color: white;
            border: none;
        }
        QPushButton#primaryButton:hover {
            background-color: #193ED5;
        }
        QPushButton#primaryButton:pressed {
            background-color: #0A1E6D;
        }
                        
        /* 滚动条样式 */
        QScrollBar:vertical {
            border: none;
            background-color: #f5f5f5;
            width: 12px;
            border-radius: 6px;
            margin: 0px; /* 防止滑块碰到边缘 */
        }

        /* 滑块 */
        QScrollBar::handle:vertical {
            background-color: #ced4da;
            border: none; /* 去掉边框 */
            border-radius: 6px;
            min-height: 20px;
            margin: 2px 0; /* 保留上下空隙，避免顶部底部黑边 */
        }

        /* 去掉顶部/底部箭头按钮 */
        QScrollBar::sub-line:vertical,
        QScrollBar::add-line:vertical {
            height: 0px;
            subcontrol-origin: margin;
        }

        /* 去掉顶部/底部点击区域的视觉影响 */
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {
            background: #f5f5f5;
            border-radius: 6px;
        }

         /* 滚动区域样式 */
        QScrollArea {
            border: 1px solid #dadce0;
            border-radius: 8px;
            padding: 8px 12px;
            background-color: white;
            selection-background-color: #007bff;
        }    
        QScrollArea#scroll_area {
            background: transparent;
            border: none;
            padding: 5px;
        }

         /* QWidget样式 */
        QWidget#scroll_content {
            background-color: white;
        }    
        QWidget#scroll_content > QWidget:hover {
            background-color: #f2f2f2;
            border-radius: 4px;
        }
                          
        QWidget#scroll_widget {
            background-color: white;
            border: none;
            margin: 0px
        }

        QWidget#scroll_widget > QWidget {
            background-color: white;
        }

        QWidget#scroll_widget > QWidget:hover {
            background-color: #f2f2f2;
            border-radius: 4px;
        }

        /* 标签样式 */               
        QLabel {
            color: #495057;
        }                       
                        
        /* 分组框样式 */
        QGroupBox {
            border: 1px solid #dadce0;
            border-radius: 8px;
            padding: 4px;
            font-weight: 600;
            color: #343a40;
            margin-top: 12px;
            padding-top: 10px;
            background-color: white;
        }
        
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 15px;
            padding: 2px 8px 2px 8px;
            background-color: white;
            border-radius: 8px;   /* 添加圆角 */
        }

        /* 进度条样式 */
        QProgressBar {
            border: 1px solid #dee2e6;
            border-radius: 8px;
            background-color: #ced4da;   
            height: 12px;
            text-align: center;
            color: transparent;
        }
        QProgressBar::chunk {
            border: none;
            border-radius: 5px;  /* 比外框小1px */
            background-color: #3656da;
            margin: 1px;
        }   

    """)
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()