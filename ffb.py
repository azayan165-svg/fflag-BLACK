import sys
import os
import json
import threading
import time
import requests
import re
import ctypes
from ctypes import sizeof, byref, c_void_p, c_size_t
import pymem
from pathlib import Path
from PyQt6 import QtWidgets, QtGui, QtCore
import subprocess
import random
import math

APP_DIR = Path(os.path.expanduser("~")) / ".Zenstrap"
APP_DIR.mkdir(parents=True, exist_ok=True)
USER_FLAGS_FILE = APP_DIR / "fflags.json"
SHORTCUT_CREATED_FILE = APP_DIR / "shortcut_created"

if not USER_FLAGS_FILE.exists():
    USER_FLAGS_FILE.write_text(json.dumps({}, indent=4))

user_flags = {}
all_offsets = {}
pm = None
base_address = None
stop_inject = False

FFLAGS_URL = "https://raw.githubusercontent.com/azayan165-svg/fflags.hpp/refs/heads/main/fflags.hpp"

def normalize_flag_name(flag_name):
    prefixes = ['FFlag', 'FInt', 'FString', 'FLog', 'DFFlag', 'DFInt', 'DFString', 'DFLog']
    for prefix in prefixes:
        if flag_name.startswith(prefix):
            return flag_name[len(prefix):]
    return flag_name

def process_imported_flags(imported_dict):
    processed = {}
    for key, value in imported_dict.items():
        clean_name = normalize_flag_name(key)
        processed[clean_name] = str(value)
    return processed

def save_user_flags():
    global user_flags
    try:
        with open(USER_FLAGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_flags, f, indent=4)
        return True
    except:
        return False

def load_user_flags():
    try:
        with open(USER_FLAGS_FILE, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
            if isinstance(loaded, list):
                converted = {}
                for item in loaded:
                    if isinstance(item, dict) and 'name' in item:
                        clean_name = normalize_flag_name(item['name'])
                        converted[clean_name] = str(item.get('value', ''))
                return converted
            return loaded
    except:
        return {}

def find_roblox_process():
    try:
        for p in pymem.process.list_processes():
            if p.th32ProcessID and "RobloxPlayerBeta.exe" in p.szExeFile.decode('utf-8', 'ignore'):
                return p.th32ProcessID
    except:
        pass
    return None

def get_module_base(pid):
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    hProcess = kernel32.OpenProcess(0x0410, False, pid)
    if not hProcess:
        return None
    try:
        hModules = (c_void_p * 1)()
        cbNeeded = c_size_t()
        if psapi.EnumProcessModules(hProcess, byref(hModules), sizeof(hModules), byref(cbNeeded)):
            return int(hModules[0])
    finally:
        kernel32.CloseHandle(hProcess)
    return None

def fetch_fflag_offsets():
    try:
        text = requests.get(FFLAGS_URL, timeout=20).text
        namespace = re.search(r'namespace FFlags\s*\{([^}]+)\}', text, re.DOTALL)
        if not namespace:
            return None
        matches = re.findall(r'uintptr_t\s+(\w+)\s*=\s*(0x[0-9A-Fa-f]+);', namespace.group(1))
        return {name: int(offset, 16) for name, offset in matches}
    except:
        return None

def infer_type(value):
    lower_value = str(value).lower().strip()
    if lower_value in ['true', 'false']:
        return 'bool'
    try:
        if '.' in lower_value:
            float(lower_value)
            return 'float'
        int(lower_value)
        return 'int'
    except:
        return 'string'

def apply_all_fflags():
    global pm, base_address, all_offsets, user_flags
    
    if not user_flags:
        return 0, 0, 0
    
    pid = find_roblox_process()
    if not pid:
        return 0, 0, 0
    
    try:
        try:
            pm = pymem.Pymem(pid)
        except:
            return 0, 0, 0
        
        base_address = get_module_base(pid)
        if not base_address:
            if hasattr(pm, 'close_process'):
                pm.close_process()
            pm = None
            return 0, 0, 0
    except:
        return 0, 0, 0
    
    if not all_offsets:
        if hasattr(pm, 'close_process'):
            pm.close_process()
        pm = None
        return 0, 0, 0
    
    success = fail = skipped = 0
    for flag_name, value in user_flags.items():
        if flag_name not in all_offsets:
            fail += 1
            continue
        
        addr = base_address + all_offsets[flag_name]
        flag_type = infer_type(value)
        value_str = str(value).strip()
        
        try:
            if flag_type == "bool":
                bool_value = value_str.lower() == "true"
                try:
                    current_value = pm.read_bool(addr)
                    if current_value != bool_value:
                        pm.write_bool(addr, bool_value)
                        success += 1
                    else:
                        skipped += 1
                except:
                    pm.write_bool(addr, bool_value)
                    success += 1
                    
            elif flag_type == "int":
                int_value = int(value_str)
                try:
                    current_value = pm.read_int(addr)
                    if current_value != int_value:
                        pm.write_int(addr, int_value)
                        success += 1
                    else:
                        skipped += 1
                except:
                    pm.write_int(addr, int_value)
                    success += 1
                    
            elif flag_type == "float":
                float_value = float(value_str)
                try:
                    current_value = pm.read_double(addr)
                    if abs(current_value - float_value) > 0.000001:
                        pm.write_double(addr, float_value)
                        success += 1
                    else:
                        skipped += 1
                except:
                    pm.write_double(addr, float_value)
                    success += 1
                    
            else:
                pm.write_string(addr, value_str)
                success += 1
                
        except Exception as e:
            print(f"Failed to set {flag_name}: {e}")
            fail += 1
    
    try:
        if hasattr(pm, 'close_process'):
            pm.close_process()
    except:
        pass
    pm = None
    
    print(f"Updated {success} FFlags, {fail} failed, {skipped} already correct")
    return success, fail, skipped

def find_roblox_exe():
    search_paths = [
        os.path.join(os.getenv("LOCALAPPDATA"), "Roblox", "Versions"),
        os.path.join(os.getenv("PROGRAMFILES"), "Roblox"),
        os.path.join(os.getenv("PROGRAMFILES(X86)"), "Roblox"),
    ]
    
    for path in search_paths:
        if os.path.exists(path):
            for root, dirs, files in os.walk(path):
                for file in files:
                    if file.lower() == "robloxplayerbeta.exe":
                        return os.path.join(root, file)
    return None

def launch_roblox():
    try:
        exe_path = find_roblox_exe()
        if exe_path:
            CREATE_NO_WINDOW = 0x08000000
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
            subprocess.Popen(
                [exe_path],
                startupinfo=startupinfo,
                creationflags=CREATE_NO_WINDOW,
                shell=False
            )
            return True
        else:
            return False
    except:
        return False

def create_desktop_shortcut():
    """Create a desktop shortcut that automatically loads FFlags and launches Roblox"""
    try:
        import winshell
        from win32com.client import Dispatch
        
        desktop = winshell.desktop()
        shortcut_path = os.path.join(desktop, "Roblox.lnk")
        
        # Check if shortcut already exists
        if os.path.exists(shortcut_path):
            print("Desktop shortcut already exists")
            return True
        
        # Get the path to this executable
        exe_path = sys.executable if getattr(sys, 'frozen', False) else sys.argv[0]
        
        # Create the shortcut
        shell = Dispatch('WScript.Shell')
        shortcut = shell.CreateShortCut(shortcut_path)
        shortcut.Targetpath = exe_path
        shortcut.Arguments = "--auto-launch"
        shortcut.WorkingDirectory = os.path.dirname(exe_path)
        shortcut.IconLocation = exe_path  # Use the executable as icon
        shortcut.Description = "Zenstrap - Roblox with FFlags"
        shortcut.save()
        
        # Mark that we've created the shortcut
        SHORTCUT_CREATED_FILE.write_text("1")
        
        print(f"Created desktop shortcut at: {shortcut_path}")
        return True
        
    except ImportError:
        print("Required modules for shortcut creation not available.")
        print("Please install: pip install pywin32 winshell")
        return False
    except Exception as e:
        print(f"Failed to create shortcut: {e}")
        return False

def format_flags_for_display(flags_dict):
    if not flags_dict:
        return ""
    
    formatted = "{\n"
    items = list(flags_dict.items())
    for i, (key, value) in enumerate(items):
        formatted += f'  "{key}": "{value}"'
        if i < len(items) - 1:
            formatted += ","
        formatted += "\n"
    formatted += "}"
    return formatted

class FastBlackWhiteTitleLabel(QtWidgets.QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.animation_offset = 0
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(30)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    
    def update_animation(self):
        self.animation_offset += 2
        
        if self.animation_offset > 100000:
            self.animation_offset = 0
        self.update()
    
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        
        font = QtGui.QFont("Arial", 28, QtGui.QFont.Weight.ExtraBold)
        painter.setFont(font)
        text_rect = painter.boundingRect(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "Zenstrap")
        
        gradient = QtGui.QLinearGradient(
            self.animation_offset - 500, text_rect.center().y(),
            self.animation_offset + 500, text_rect.center().y()
        )
        
        gradient.setColorAt(0.0, QtGui.QColor("#000000"))
        gradient.setColorAt(0.1, QtGui.QColor("#FFFFFF"))
        gradient.setColorAt(0.2, QtGui.QColor("#000000"))
        gradient.setColorAt(0.3, QtGui.QColor("#FFFFFF"))
        gradient.setColorAt(0.4, QtGui.QColor("#000000"))
        gradient.setColorAt(0.5, QtGui.QColor("#FFFFFF"))
        gradient.setColorAt(0.6, QtGui.QColor("#000000"))
        gradient.setColorAt(0.7, QtGui.QColor("#FFFFFF"))
        gradient.setColorAt(0.8, QtGui.QColor("#000000"))
        gradient.setColorAt(0.9, QtGui.QColor("#FFFFFF"))
        gradient.setColorAt(1.0, QtGui.QColor("#000000"))
        
        gradient.setSpread(QtGui.QGradient.Spread.RepeatSpread)
        
        shadow_color = QtGui.QColor(0, 0, 0, 100)
        painter.setPen(QtGui.QPen(shadow_color, 2))
        painter.drawText(text_rect.translated(1, 1), QtCore.Qt.AlignmentFlag.AlignCenter, "Zenstrap")
        
        painter.setPen(QtGui.QPen(gradient, 2))
        painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "Zenstrap")

class GradientBackgroundWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.animation_offset = 0
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(50)
        
        self.particles = []
        self.init_particles(150)
    
    def init_particles(self, count):
        """Initialize particle positions and properties"""
        self.particles = []
        for _ in range(count):
            self.particles.append({
                'x': random.randint(0, 1000),
                'y': random.randint(0, 1000),
                'size': random.uniform(0.5, 2.5),
                'speed': random.uniform(0.1, 0.5),
                'alpha': random.randint(10, 50),
                'direction': random.choice([-1, 1]),
                'wave_offset': random.uniform(0, 6.28),
            })
    
    def update_animation(self):
        self.animation_offset = (self.animation_offset + 1) % 360
        
        if hasattr(self, 'width') and hasattr(self, 'height'):
            for particle in self.particles:
                # Gentle floating motion
                particle['x'] += particle['speed'] * particle['direction']
                particle['y'] += math.sin(self.animation_offset * 0.1 + particle['wave_offset']) * 0.2
                
                if particle['x'] > self.width():
                    particle['x'] = 0
                elif particle['x'] < 0:
                    particle['x'] = self.width()
                    
                if particle['y'] > self.height():
                    particle['y'] = 0
                elif particle['y'] < 0:
                    particle['y'] = self.height()
        
        self.update()
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.particles and hasattr(self, 'width') and hasattr(self, 'height'):
            for particle in self.particles:
                particle['x'] = particle['x'] * self.width() / 1000
                particle['y'] = particle['y'] * self.height() / 1000
    
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        
        radial_gradient = QtGui.QRadialGradient(
            self.width() * 0.7 + self.animation_offset * 0.1,
            self.height() * 0.7 + self.animation_offset * 0.05,
            self.width() * 0.8
        )
        
        anim_factor = (self.animation_offset / 360.0) * 0.1
        
        gray_color = QtGui.QColor("#3a3a3a")
        gray_color.setAlphaF(0.7 + anim_factor * 0.3)
        radial_gradient.setColorAt(0.0, gray_color)
        
        mid_color = QtGui.QColor("#2a2a2a")
        mid_color.setAlphaF(0.5 + anim_factor * 0.2)
        radial_gradient.setColorAt(0.4, mid_color)
        
        black_color = QtGui.QColor("#000000")
        black_color.setAlphaF(0.9 + anim_factor * 0.1)
        radial_gradient.setColorAt(1.0, black_color)
        
        painter.fillRect(self.rect(), radial_gradient)
        
        linear_gradient = QtGui.QLinearGradient(
            self.width(), self.height(),
            0, 0
        )
        
        linear_gradient.setColorAt(0.0, QtGui.QColor("#404040"))
        linear_gradient.setColorAt(0.5, QtGui.QColor("#202020"))
        linear_gradient.setColorAt(1.0, QtGui.QColor("#000000"))
        
        painter.fillRect(self.rect(), linear_gradient)
        
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        for particle in self.particles:
            gray_shade = random.randint(180, 230)
            particle_color = QtGui.QColor(gray_shade, gray_shade, gray_shade, particle['alpha'])
            painter.setBrush(particle_color)
            
            x_pos = int(particle['x'])
            y_pos = int(particle['y'])
            size = int(particle['size'])
            painter.drawEllipse(x_pos, y_pos, size, size)
        
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        for i in range(15):
            x = (self.animation_offset + i * 50) % self.width()
            y = (self.animation_offset + i * 30) % self.height()
            size = 1 + (i % 2)
            
            particle_color = QtGui.QColor(255, 255, 255, 15 + (i % 20))
            painter.setBrush(particle_color)
            painter.drawEllipse(int(x), int(y), size, size)

class TransparentTextEdit(QtWidgets.QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        
    def paintEvent(self, event):
        painter = QtGui.QPainter(self.viewport())
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        
        background_color = QtGui.QColor(25, 25, 25, 180)
        painter.setBrush(background_color)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.viewport().rect(), 8, 8)
        
        super().paintEvent(event)

class SimpleFFlagInjector(QtWidgets.QMainWindow):
    def __init__(self, auto_launch=False, start_in_tray=False):
        super().__init__()
        self.setWindowTitle("Zenstrap")
        
        self.auto_launch = auto_launch
        self.start_in_tray = start_in_tray
        
        self.setup_tray()
        
        global user_flags
        user_flags = load_user_flags()
        
        # Load offsets in background
        threading.Thread(target=self.load_offsets, daemon=True).start()
        
        # Start injection monitor in background
        threading.Thread(target=self.injection_monitor, daemon=True).start()
        
        # Create desktop shortcut on first run
        if not SHORTCUT_CREATED_FILE.exists():
            threading.Thread(target=self.create_shortcut_thread, daemon=True).start()
        
        # If auto-launch mode, hide the window and launch Roblox
        if self.auto_launch:
            self.hide()
            self.save_and_launch()
        elif not self.start_in_tray:
            self.setup_ui()
            self.center_window()
            self.show()
        else:
            # Start in system tray
            self.hide()
        
        self.title_animation_offset = 0
        self.bg_animation_offset = 0
    
    def create_shortcut_thread(self):
        """Create shortcut in a separate thread"""
        try:
            create_desktop_shortcut()
        except Exception as e:
            print(f"Failed to create shortcut: {e}")
    
    def center_window(self):
        screen_geometry = QtWidgets.QApplication.primaryScreen().availableGeometry()
        window_width = 600
        window_height = 600
        
        x = (screen_geometry.width() - window_width) // 2
        y = (screen_geometry.height() - window_height) // 2
        
        self.setGeometry(x, y, window_width, window_height)
    
    def setup_tray(self):
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        icon = QtGui.QIcon()
        pixmap = QtGui.QPixmap(32, 32)
        pixmap.fill(QtGui.QColor("#000000"))
        icon.addPixmap(pixmap)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("Zenstrap")
        
        tray_menu = QtWidgets.QMenu()
        show_action = tray_menu.addAction("Show Zenstrap")
        show_action.triggered.connect(self.show_window)
        
        launch_action = tray_menu.addAction("Launch Roblox")
        launch_action.triggered.connect(self.save_and_launch)
        
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_activated)
        self.tray_icon.show()
    
    def setup_ui(self):
        self.background_widget = GradientBackgroundWidget()
        self.setCentralWidget(self.background_widget)
        
        self.central_container = QtWidgets.QWidget(self.background_widget)
        self.central_container.setStyleSheet("background-color: transparent;")
        
        self.main_layout = QtWidgets.QVBoxLayout(self.central_container)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(10)
        
        self.title_label = FastBlackWhiteTitleLabel()
        self.title_label.setFixedHeight(60)
        self.main_layout.addWidget(self.title_label)
        
        self.json_input = TransparentTextEdit()
        
        global user_flags
        if user_flags:
            formatted = format_flags_for_display(user_flags)
            self.json_input.setPlainText(formatted)
        
        self.json_input.setStyleSheet("""
            QTextEdit {
                color: #FFFFFF;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 12px;
                selection-background-color: #555555;
                border: none;
                padding: 15px;
            }
            QScrollBar:vertical {
                background: rgba(40, 40, 40, 150);
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: rgba(100, 100, 100, 180);
                border-radius: 6px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(120, 120, 120, 200);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        
        self.main_layout.addWidget(self.json_input, 1)
        
        button_container = QtWidgets.QWidget()
        button_container.setStyleSheet("background-color: transparent;")
        button_container.setFixedHeight(45)
        
        button_layout = QtWidgets.QHBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        
        self.launch_btn = QtWidgets.QPushButton("Save and Launch")
        self.launch_btn.setFixedSize(120, 36)
        self.launch_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(40, 40, 40, 200);
                color: white;
                border: 1px solid rgba(100, 100, 100, 150);
                border-radius: 12px;
                font-weight: bold;
                font-size: 12px;
                padding: 3px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 220);
                border: 1px solid rgba(140, 140, 140, 180);
            }
            QPushButton:pressed {
                background-color: rgba(30, 30, 30, 200);
                border: 1px solid rgba(120, 120, 120, 160);
            }
        """)
        self.launch_btn.clicked.connect(self.save_and_launch)
        self.launch_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        
        button_layout.addWidget(self.launch_btn)
        
        self.main_layout.addWidget(button_container, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        
        self.save_timer = QtCore.QTimer()
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_current_text)
        self.json_input.textChanged.connect(self.schedule_save)
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        
        if hasattr(self, 'central_container'):
            container_width = self.width() - 40
            container_height = self.height() - 40
            
            x = 20
            y = 20
            
            self.central_container.setGeometry(x, y, container_width, container_height)
            
            if hasattr(self, 'title_label'):
                self.title_label.update()
    
    def schedule_save(self):
        self.save_timer.start(500)
    
    def save_current_text(self):
        global user_flags
        
        json_text = self.json_input.toPlainText().strip()
        
        if not json_text:
            user_flags = {}
            save_user_flags()
            print("Cleared all FFlags")
            return
        
        try:
            imported = json.loads(json_text)
            
            if not isinstance(imported, dict):
                print("Invalid JSON: Not a dictionary")
                return
            
            processed = process_imported_flags(imported)
            
            user_flags = processed
            
            if save_user_flags():
                print(f"Saved {len(user_flags)} FFlags to {USER_FLAGS_FILE}")
            else:
                print("Failed to save FFlags")
            
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}")
        except Exception as e:
            print(f"Error saving FFlags: {e}")
    
    def load_offsets(self):
        global all_offsets
        offsets = fetch_fflag_offsets()
        if offsets:
            all_offsets = offsets
    
    def save_and_launch(self):
        # Only save if we have the UI setup
        if hasattr(self, 'json_input'):
            self.save_current_text()
        
        global user_flags
        if not user_flags:
            print("No FFlags to inject")
            return
        
        print(f"Launching with {len(user_flags)} FFlags")
        
        def launch_and_inject():
            if launch_roblox():
                for i in range(30):
                    time.sleep(1)
                    pid = find_roblox_process()
                    if pid:
                        print("Roblox detected, injecting FFlags...")
                        success, fail, skipped = apply_all_fflags()
                        print(f"Injected {success} FFlags, {fail} failed, {skipped} already correct")
                        break
                else:
                    print("Roblox not detected within 30 seconds")
        
        threading.Thread(target=launch_and_inject, daemon=True).start()
    
    def injection_monitor(self):
        global stop_inject
        last_inject = 0
        
        while not stop_inject:
            current_time = time.time()
            
            if current_time - last_inject >= 100 and user_flags:
                pid = find_roblox_process()
                if pid:
                    print("Auto-injecting FFlags...")
                    success, fail, skipped = apply_all_fflags()
                    print(f"Auto-injected {success} FFlags, {fail} failed, {skipped} already correct")
                    last_inject = current_time
            
            time.sleep(10)
    
    def minimize_to_tray(self):
        self.hide()
    
    def show_window(self):
        # Setup UI if it hasn't been setup yet
        if not hasattr(self, 'json_input'):
            self.setup_ui()
            self.center_window()
        
        self.showNormal()
        self.raise_()
        self.activateWindow()
    
    def tray_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()
    
    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            if self.windowState() & QtCore.Qt.WindowState.WindowFullScreen:
                QtCore.QTimer.singleShot(0, self.force_fullscreen_update)
        super().changeEvent(event)
    
    def force_fullscreen_update(self):
        """Force update of all animations in fullscreen mode"""
        if hasattr(self, 'title_label'):
            if not self.title_label.timer.isActive():
                self.title_label.timer.start(30)
            self.title_label.update()
            
        if hasattr(self, 'background_widget'):
            if not self.background_widget.timer.isActive():
                self.background_widget.timer.start(50)
            self.background_widget.update()
        
        if hasattr(self, 'central_container'):
            self.resizeEvent(None)
    
    def update_layout_fullscreen(self):
        """Update layout after fullscreen transition - FIXED VERSION"""
        if hasattr(self, 'central_container'):
            container_width = self.width() - 20
            container_height = self.height() - 15
            
            x = 10
            y = 10
            
            self.central_container.setGeometry(x, y, container_width, container_height)
            
            self.force_fullscreen_update()
    
    def closeEvent(self, event):
        event.ignore()
        self.minimize_to_tray()
    
    def quit_app(self):
        global stop_inject
        stop_inject = True
        self.tray_icon.hide()
        QtWidgets.QApplication.quit()

def main():
    # Check for command line arguments
    auto_launch = "--auto-launch" in sys.argv
    start_in_tray = "--tray" in sys.argv or auto_launch
    
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    injector = SimpleFFlagInjector(auto_launch=auto_launch, start_in_tray=start_in_tray)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
