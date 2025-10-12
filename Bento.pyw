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
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit


CURRENT_VERSION = "v1.0.0"  #版本号

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
    ("Temu前端  德国", "", "https://www.temu.com/de-en"),
    ("Temu前端  日本", "", "https://www.temu.com/jp-en"),
    ("Temu前端  加拿大", "", "https://www.temu.com/ca"),
    ("Temu前端  澳大利亚", "", "https://www.temu.com/au"),
    ("Temu前端  西班牙", "", "https://www.temu.com/es-en"),
    ("Temu前端  马来西亚", "", "https://www.temu.com/my"),
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
        self.status_label.setStyleSheet("color: #5f6368; ")
        
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
    """浏览器文件类"""
    def __init__(self, name, user_agent="", profile_path="", start_url=""):
        self.name = name
        self.user_agent = user_agent
        self.profile_path = profile_path or str(PROFILES_DIR / name)
        self.start_url = start_url or "https://www.google.com"

    def to_dict(self):
        return {
            "name": self.name,
            "user_agent": self.user_agent,
            "profile_path": self.profile_path,
            "start_url": self.start_url
        }

    @staticmethod
    def from_dict(data):
        return BrowserProfile(
            data["name"],
            data.get("user_agent", ""),
            data.get("profile_path", ""),
            data.get("start_url", "")
        )

class MultiSelectListWidget(QListWidget):
    """支持 Ctrl/Shift 多选和 Ctrl+A 全选的 QListWidget"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # 启用多选模式
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def keyPressEvent(self, event):
        """增加 Ctrl+A 全选"""
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_A:
            self.selectAll()
        else:
            super().keyPressEvent(event)

class EditProfileDialog(QDialog):
    """编辑或新建浏览器对话框"""
    def __init__(self, profile=None, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setWindowTitle("编辑浏览器" if profile else "新建浏览器")
        self.setMinimumWidth(200)
        self.init_ui()

        if profile:
            self.load_profile(profile)

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(24, 24, 24, 24)

        # 表单区域
        form_layout = QFormLayout()
        form_layout.setSpacing(16)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("名称，例如: 工作账号")

        self.start_url_edit = QLineEdit()
        self.start_url_edit.setPlaceholderText("首页网址，默认: https://www.google.com")

        self.user_agent_edit = QLineEdit()
        self.user_agent_edit.setPlaceholderText("UA标识，默认：User-Agent")

        form_layout.addRow(self.name_edit)
        form_layout.addRow(self.start_url_edit)
        form_layout.addRow(self.user_agent_edit)
        layout.addLayout(form_layout)

        # 按钮区域
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        self.preset_btn = QPushButton("预设")
        self.preset_btn.setObjectName("secondaryButton")
        self.preset_btn.clicked.connect(lambda: self.parent().show_presets())

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("secondaryButton")
        self.ok_btn = QPushButton("保存" if self.profile else "创建")
        self.ok_btn.setObjectName("primaryButton")

        btn_layout.addWidget(self.preset_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.ok_btn)

        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def load_profile(self, profile):
        """加载现有浏览器信息"""
        self.name_edit.setText(profile.name)
        self.start_url_edit.setText(profile.start_url)
        self.user_agent_edit.setText(profile.user_agent)
        # 允许修改名称，所以不禁用 name_edit

    def get_profile(self):
        """返回编辑后的浏览器对象"""
        name = self.name_edit.text().strip()
        return BrowserProfile(
            name,
            self.user_agent_edit.text(),
            self.profile.profile_path if self.profile else "",
            self.start_url_edit.text()
        )

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
        
        self.profiles = []
        self.browser_processes = []
        self.config_file = PROFILES_DIR / "browser_profiles.json"
        self.edit_dialog = None 
        
        self.apply_style()
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
    
    def apply_style(self):
        self.setStyleSheet("""
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
            }
            
            QMenu::item {
                padding: 8px 40px 8px 24px;  /* 上 右 下 左 */
                margin: 2px 4px;
                border-radius: 8px;
                color: #495057;
            }
            
            QMenu::item:selected {
                background-color: #e3f2fd;
                color: #1976d2;
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
                color: #202124;
            }
            QListWidget::item:hover {
                background-color: #f5f5f5;
            }
            QListWidget::item:selected {
                background-color: #e8f0fe;
                color: #1967d2;
            }
                           
            /* 按钮样式 */           
            QPushButton {
                padding: 10px 24px;
                border: none;
                border-radius: 8px;
                font-weight: 500;
                color: #5f6368;
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
        self.profile_list = MultiSelectListWidget()
        self.profile_list.itemDoubleClicked.connect(self.open_browser)
        self.profile_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.profile_list.customContextMenuRequested.connect(self.show_context_menu)
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

        # 如果只选中一个才显示打开和编辑
        if len(selected_items) == 1:
            open_action = QAction("打开", self)
            edit_action = QAction("编辑", self)
            open_action.triggered.connect(self.open_browser)
            edit_action.triggered.connect(self.edit_profile)
            menu.addAction(open_action)
            menu.addAction(edit_action)
            menu.addSeparator()

        # 删除动作，始终显示
        delete_action = QAction("删除", self)
        delete_action.triggered.connect(self.delete_profile)
        menu.addAction(delete_action)

        menu.exec(QCursor.pos())

    def close_edit_dialog(self):
        """关闭 EditProfileDialog 对话框，相当于点击取消或关闭按钮"""
        if self.edit_dialog and self.edit_dialog.isVisible():
            self.edit_dialog.reject()  
            self.edit_dialog = None
    def show_presets(self):
        """批量选择预设并创建浏览器"""
        dialog = QDialog(self)
        dialog.setWindowTitle("预设")
        dialog.setMinimumWidth(300)
        layout = QVBoxLayout(dialog)

        # 搜索框
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("搜索预设")
        icon_path = os.path.join(os.getcwd(), "icon", "search.png")
        search_edit.addAction(QIcon(icon_path), QLineEdit.ActionPosition.TrailingPosition)
        layout.addWidget(search_edit)

        # 列表
        list_widget = MultiSelectListWidget()
        for name, path, url in PRESETS:
            list_widget.addItem(name)
        layout.addWidget(list_widget, 1)

        # 搜索过滤函数
        def filter_presets(text):
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                item.setHidden(text.lower() not in item.text().lower())
        
        search_edit.textChanged.connect(filter_presets)

        def apply_multiple():
            selected_items = list_widget.selectedItems()
            if not selected_items:
                QMessageBox.warning(dialog, "警告", "请至少选择一个预设")
                return

            for item in selected_items:
                index = list_widget.row(item)
                name, path, url = PRESETS[index]

                # 自动生成唯一名称
                base_name = name
                counter = 1
                while any(p.name == name for p in self.profiles):
                    name = f"{base_name}_{counter}"
                    counter += 1

                profile = BrowserProfile(name=name, start_url=url)
                self.profiles.append(profile)
                (PROFILES_DIR / profile.name).mkdir(exist_ok=True)
                self.profile_list.addItem(profile.name)

            self.save_profiles()
            self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已批量添加 {len(selected_items)} 个浏览器")
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
    
    def add_profile(self):
        dialog = EditProfileDialog(parent=self)
        self.edit_dialog = dialog
        if dialog.exec() == QDialog.DialogCode.Accepted:
            profile = dialog.get_profile()
            if not profile.name:
                QMessageBox.warning(self, "警告", "浏览器名称不能为空")
                return
            if any(p.name == profile.name for p in self.profiles):
                QMessageBox.warning(self, "警告", "浏览器名称已存在")
                return

            profile_folder = PROFILES_DIR / profile.name
            profile_folder.mkdir(exist_ok=True)
            profile.profile_path = str(profile_folder)

            self.profiles.append(profile)
            self.profile_list.addItem(profile.name)
            self.save_profiles()
            self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已添加浏览器: {profile.name}")
            QTimer.singleShot(2000, lambda: self.status_label.setText(f"<span style='color: #00d26a;'>●</span> 就绪"))

    def edit_profile(self):
        current_item = self.profile_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "警告", "请先选择一个浏览器")
            return

        current_index = self.profile_list.currentRow()
        old_name = current_item.text()
        profile = next((p for p in self.profiles if p.name == old_name), None)
        if not profile:
            return

        dialog = EditProfileDialog(profile=profile, parent=self)
        self.edit_dialog = dialog
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated_profile = dialog.get_profile()
            new_name = updated_profile.name.strip()

            if not new_name:
                QMessageBox.warning(self, "警告", "浏览器名称不能为空")
                return

            if new_name != old_name and any(p.name == new_name for p in self.profiles):
                QMessageBox.warning(self, "警告", "浏览器名称已存在")
                return

            # 修改文件夹和对象
            if new_name != old_name:
                old_folder = PROFILES_DIR / old_name
                new_folder = PROFILES_DIR / new_name
                try:
                    if old_folder.exists():
                        old_folder.rename(new_folder)
                    profile.profile_path = str(new_folder)
                    profile.name = new_name
                    self.profile_list.item(current_index).setText(new_name)
                except Exception as e:
                    QMessageBox.warning(self, "警告", f"重命名浏览器文件夹失败: {e}")

            # 更新其他属性
            profile.start_url = updated_profile.start_url
            profile.user_agent = updated_profile.user_agent
            self.save_profiles()
            self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已更新浏览器: {profile.name}")
            QTimer.singleShot(2000, lambda: self.status_label.setText(f"<span style='color: #00d26a;'>●</span> 就绪"))
    
    def open_browser(self):
        """打开浏览器"""
        if not KernelManager.is_chrome_installed():
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("警告")
            msg_box.setText("浏览器内核未安装，无法打开浏览器，是否现在安装？")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

            # 设置按钮对象名用于样式
            yes_button = msg_box.button(QMessageBox.StandardButton.Yes)
            yes_button.setObjectName("yesButton")
            no_button = msg_box.button(QMessageBox.StandardButton.No)

            # 设置样式
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
                # 用户选择了 No 或关闭窗口
                return

            # 用户选择 Yes，开始下载
            self.download_chrome()
            return

        
        current_item = self.profile_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "警告", "请先选择一个浏览器")
            return
        
        profile_name = current_item.text()
        profile = next((p for p in self.profiles if p.name == profile_name), None)
        
        if profile:
            chrome_path = KernelManager.get_chrome_path()
            profile_dir = pathlib.Path(profile.profile_path)
            profile_dir.mkdir(parents=True, exist_ok=True)
            
            # 构建浏览器内核启动参数
            args = [
                chrome_path,
                f"--user-data-dir={profile.profile_path}",
                "--no-first-run",
                "--no-default-browser-check",
                profile.start_url
            ]
            
            # 如果设置了User-Agent，添加到参数列表
            if profile.user_agent:
                args.insert(1, f"--user-agent={profile.user_agent}")
            
            try:
                # 启动浏览器内核进程
                process = subprocess.Popen(args)
                self.browser_processes.append(process)
                self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已打开: {profile_name}")
                QTimer.singleShot(2000, lambda: self.status_label.setText(f"<span style='color: #00d26a;'>●</span> 就绪"))
            except Exception as e:
                QMessageBox.critical(self, "错误", f"启动浏览器失败: {str(e)}")

    def delete_profile(self):
        """支持多选删除"""
        selected_items = self.profile_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择一个或多个浏览器")
            return

        names = [item.text() for item in selected_items]

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("确认删除")
        msg_box.setText("确定要删除以下浏览器吗？（本地缓存数据将清空）\n\n" + "\n".join(names))
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        # 给 No 按钮添加样式
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
        if reply != QMessageBox.StandardButton.Yes:
            return

        for item in selected_items:
            profile_name = item.text()
            profile = next((p for p in self.profiles if p.name == profile_name), None)
            # 删除文件夹
            if profile and profile.profile_path:
                try:
                    profile_path = pathlib.Path(profile.profile_path)
                    if profile_path.exists():
                        shutil.rmtree(profile_path)
                except Exception as e:
                    QMessageBox.warning(self, "警告", f"删除浏览器文件夹失败: {str(e)}")
            # 从列表中移除
            row = self.profile_list.row(item)
            self.profile_list.takeItem(row)
            self.profiles = [p for p in self.profiles if p.name != profile_name]

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
        menu_x = button_pos.x()
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

                # ✅ 默认选中第一个内核
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
    
    def save_profiles(self):
        data = [p.to_dict() for p in self.profiles]
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def load_profiles(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.profiles = [BrowserProfile.from_dict(p) for p in data]
                    for profile in self.profiles:
                        self.profile_list.addItem(profile.name)
                self.status_label.setText(f"<span style='color: #ffce47;'>●</span> 已加载 {len(self.profiles)} 个浏览器")
                QTimer.singleShot(2000, lambda: self.status_label.setText(f"<span style='color: #00d26a;'>●</span> 就绪"))
            except Exception as e:
                QMessageBox.warning(self, "错误", f"加载浏览器失败: {str(e)}")


def main():
    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()