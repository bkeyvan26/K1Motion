# -*- coding: utf-8 -*-
"""
K1Motion v10.5
- Fixed shutdown warnings (VideoWriter + cap.read race)
- Sensitivity 0-400 mapped to threshold 0-255
- Nonlinear volume
- All previous features
"""

import os
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|max_delay;500000|fflags;nobuffer"
)

import sys, json, time, wave, struct, math, tempfile, threading, urllib.parse, datetime, subprocess
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List

import cv2
import numpy as np

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QRect
from PySide6.QtGui import (
    QImage, QPainter, QPen, QColor, QFont, QIcon, QPixmap, QAction
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QPushButton, QDialog, QFormLayout, QLineEdit, QSpinBox, QListWidget,
    QListWidgetItem, QDialogButtonBox, QMessageBox, QSlider, QCheckBox,
    QProgressDialog, QStatusBar, QGroupBox, QGridLayout, QComboBox,
    QStackedWidget, QFileDialog, QSystemTrayIcon, QMenu
)

IS_WIN = sys.platform.startswith("win")
if IS_WIN:
    import winsound

APP_DIR = Path.home() / ".k1motion"
APP_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = APP_DIR / "settings.json"
ALARM_WAV = Path(tempfile.gettempdir()) / "k1motion_alarm.wav"
DEFAULT_RECORDINGS_DIR = APP_DIR / "recordings"
DEFAULT_RECORDINGS_DIR.mkdir(exist_ok=True)

AUTOSTART_KEY = "K1Motion"
AUTOSTART_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

SENSITIVITY_MAX = 400
SENSITIVITY_DEFAULT = 100

_rec_dir_override: Optional[str] = None

def get_recordings_dir() -> Path:
    p = Path(_rec_dir_override) if _rec_dir_override else DEFAULT_RECORDINGS_DIR
    try: p.mkdir(parents=True, exist_ok=True)
    except Exception: pass
    return p

def set_recordings_dir(path: str):
    global _rec_dir_override
    _rec_dir_override = path.strip() if path and path.strip() else None

def sensitivity_to_threshold(s: int) -> int:
    s = max(0, min(SENSITIVITY_MAX, int(s)))
    return int(round(s * 255.0 / SENSITIVITY_MAX))


# ============================================================================
# Auto-start
# ============================================================================
def _get_launch_command() -> Optional[str]:
    try:
        if getattr(sys, 'frozen', False):
            return f'"{sys.executable}"'
        py = Path(sys.executable); pyw = py.with_name("pythonw.exe")
        interp = str(pyw) if pyw.exists() else str(py)
        script = Path(__file__).resolve()
        return f'"{interp}" "{script}"'
    except Exception as e:
        print("launch command failed:", e); return None

def is_autostart_enabled() -> bool:
    if not IS_WIN: return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, AUTOSTART_KEY); winreg.CloseKey(key); return True
        except FileNotFoundError:
            winreg.CloseKey(key); return False
    except Exception as e:
        print("read autostart failed:", e); return False

def set_autostart(enabled: bool) -> bool:
    if not IS_WIN: return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_ALL_ACCESS)
        if enabled:
            cmd = _get_launch_command()
            if not cmd: winreg.CloseKey(key); return False
            winreg.SetValueEx(key, AUTOSTART_KEY, 0, winreg.REG_SZ, cmd)
            print(f"✅ autostart enabled: {cmd}")
        else:
            try: winreg.DeleteValue(key, AUTOSTART_KEY); print("✅ autostart disabled.")
            except FileNotFoundError: pass
        winreg.CloseKey(key); return True
    except Exception as e:
        print("write autostart failed:", e); return False


# ============================================================================
# i18n
# ============================================================================
TRANSLATIONS = {
    "fa": {
        "app_title":"K1Motion","lang_btn":"🌐 English","cameras":"دوربین‌ها",
        "add":"➕ افزودن","edit":"✎ ویرایش","delete":"🗑 حذف",
        "connect":"🔌 اتصال","disconnect":"⏏ قطع",
        "connect_all":"🔗 اتصال به همه","disconnect_all":"⛔ قطع همه",
        "quick_params":"پارامترهای سریع",
        "sensitivity_quick":"حساسیت (۰=فوق‌حساس، ۴۰۰=بی‌تفاوت)",
        "min_area_quick":"حداقل ناحیه حرکت (px)",
        "volume_title":"🔊 بلندی صدا",
        "volume_hint":"منحنی غیرخطی: ۵۰=۲۵٪، ۱۰=۱٪، ۲=تقریباً بی‌صدا",
        "pause_sending":"⏸  توقف ارسال به بله","resume_sending":"▶  فعال‌سازی ارسال به بله",
        "hide_tray":"🔽  مخفی کردن در سینی","settings":"⚙  تنظیمات عمومی",
        "recordings_folder":"📁 پوشه فیلم‌ها","reset_settings":"🧹  پاک کردن تنظیمات",
        "quit_app":"✖  بستن برنامه","set_roi":"▭ تعیین محدوده","clear_roi":"✕ پاک کردن ROI",
        "test_alarm":"▶ تست آلارم","back_grid":"⊟ بازگشت به شبکه",
        "layout":"چیدمان:","fullscreen":"⛶ تمام‌صفحه",
        "ready":"آماده","connecting":"در حال اتصال…","connected":"متصل",
        "conn_failed_retry":"اتصال ناموفق — تلاش مجدد…","disconnected_reconnect":"قطع شد — اتصال مجدد…",
        "disconnected":"قطع","all_disconnected":"همه قطع شدند",
        "no_cam_selected":"«{name}» متصل نیست — «اتصال» را بزن",
        "no_cam_selected_short":"«{name}» قطع است",
        "motion_detected":"⚠ حرکت تشخیص داده شد","monitoring":"در حال پایش…","cooldown":"Cooldown…",
        "hidden_tray":"🔽 برنامه در سینی مخفی است",
        "tray_hint":"برنامه در سینی سیستم مخفی شد. برای برگشت، روی آیکون کنار ساعت دابل‌کلیک کن.",
        "tray_tooltip":"K1Motion — در حال اجرا","tray_show":"👁  نمایش پنجره",
        "tray_hide":"🔽  مخفی کردن","tray_settings":"⚙  تنظیمات","tray_exit":"✖  خروج",
        "grid_hint":"نمای شبکه — برای بزرگ‌نمایی روی دوربین دابل‌کلیک کن",
        "single_hint":"نمای تک — برای بازگشت به شبکه دابل‌کلیک کن",
        "roi_status":"ROI: x={x} y={y} w={w} h={h}","roi_cleared":"ROI پاک شد",
        "settings_saved":"تنظیمات ذخیره شد",
        "error":"خطا","info":"اطلاع","ok":"تأیید","yes":"بله","no":"خیر",
        "cam_settings_title":"تنظیمات دوربین","cam_name":"نام دوربین:",
        "ip_host":"IP / Host:","rtsp_port":"RTSP Port:","username":"Username:",
        "password":"Password:","rtsp_path":"RTSP Path:",
        "rtsp_path_placeholder":"مثلا /Streaming/Channels/101 — خالی = تشخیص خودکار",
        "auto_detect":"🔍  تشخیص خودکار مسیر RTSP","motion_params":"پارامترهای تشخیص حرکت",
        "sensitivity":"حساسیت (۰=فوق‌حساس، ۴۰۰=بی‌تفاوت):",
        "min_area_px":"حداقل ناحیه حرکت (پیکسل):","process_every":"پردازش هر N فریم:",
        "sensitivity_help_btn":"❔  راهنمای حساسیت و حداقل ناحیه",
        "save":"ذخیره","cancel":"انصراف","detecting":"در حال تست مسیرهای RTSP…",
        "rtsp_detect_title":"تشخیص خودکار RTSP","testing":"تست {i}/{n}:\n{path}",
        "found":"پیدا شد","found_msg":"مسیر کارا:\n{path}","not_found":"پیدا نشد",
        "not_found_msg":"هیچ مسیر رایجی جواب نداد. لطفاً دستی وارد کن.",
        "need_ip_first":"اول IP را وارد کن.",
        "sensitivity_help":(
            "🔹 حساسیت (۰ تا ۴۰۰): آستانه تغییر روشنایی پیکسل‌ها\n"
            "  ۰=فوق‌حساس | ۲۵=حساس | ۱۰۰=متعادل | ۲۰۰=بی‌تفاوت | ۳۰۰+=فقط تغییرات بزرگ\n\n"
            "🔹 Min Area (پیکسل):\n"
            "  ۱۰۰=پرنده | ۴۰۰=دست | ۲۰۰۰=آدم | ۸۰۰۰=ماشین\n\n"
            "🔹 نکته: اگر با برگ آلارم داد حساسیت را زیاد کن یا Min Area را بیشتر کن.\n"
            "   اگر آدم رد شد و نگرفت حساسیت را کم کن."
        ),
        "default_cam_name":"دوربین {n}","camera":"دوربین",
        "general_settings_title":"تنظیمات عمومی",
        "alarm_duration":"مدت آلارم (ثانیه):","cooldown_label":"Cooldown (ثانیه):",
        "play_sound":"پخش صدای هشدار","volume_setting":"بلندی صدا:",
        "always_on_top":"هنگام آلارم، پنجره روی همه برنامه‌ها بیاید",
        "autostart_windows":"اجرای خودکار هنگام بالا آمدن ویندوز",
        "autostart_hint":"برنامه در استارتاپ ویندوز ثبت می‌شود",
        "record_on_motion":"ضبط فیلم هنگام حرکت","record_duration":"مدت ضبط (ثانیه):",
        "recordings_dir_label":"پوشه ذخیره فیلم:","default_rec_dir":"پیش‌فرض: {dir}",
        "send_to_bale_section":"📨 ارسال به پیام‌رسان بله",
        "send_to_bale":"ارسال فیلم به پیام‌رسان بله",
        "bot_token":"توکن ربات:","bot_token_placeholder":"توکن ربات بله (از BotFather)",
        "chat_id":"Chat ID:","chat_id_placeholder":"Chat ID شما",
        "message_text":"متن پیام:","message_text_placeholder":"متن پیام همراه فیلم",
        "send_test":"📤 ارسال پیام تست به بله",
        "test_success":"موفق","test_success_msg":"پیام تست ارسال شد. بله را چک کن.",
        "test_failed":"ناموفق","test_failed_msg":"پاسخ بله:\n{code}\n{body}",
        "test_error_msg":"ارسال تست ناموفق:\n{err}",
        "need_token_chat":"اول توکن و Chat ID را وارد کن.",
        "pick_rec_dir":"انتخاب پوشه ذخیره فیلم","pick_rec_dir_btn":"…",
        "reset_confirm_title":"پاک کردن تنظیمات",
        "reset_confirm":"همه تنظیمات پاک شوند؟\nبرنامه بسته می‌شود.",
        "reset_done":"پاک شد","reset_done_msg":"تنظیمات پاک شد.",
        "min_one_cam":"حداقل یک دوربین باید باقی بماند.",
        "delete_cam_title":"حذف","delete_cam_confirm":"«{name}» حذف شود؟",
        "need_ip":"اول IP و مشخصات دوربین را وارد کن.",
        "needs_roi":"ROI لازم است","needs_roi_msg":"اول با «تعیین محدوده» یک کادر بکش.",
        "sending_paused":"⏸ ارسال متوقف — فیلم ذخیره شد",
        "sending_processing":"⏳ در حال تبدیل و ارسال…",
        "sending_ok":"✅ فیلم به بله ارسال شد",
        "sending_fail":"❌ ارسال به بله ناموفق (کنسول را ببین)",
        "motion_overlay":"⚠  MOTION DETECTED  ⚠",
        "default_caption":"⚠ حرکت مشکوک\nدوربین: {name}\nتاریخ: {date}\nساعت: {time}",
        "autostart_enabled_msg":"✅ اجرای خودکار با ویندوز فعال شد",
        "autostart_disabled_msg":"اجرای خودکار با ویندوز غیرفعال شد",
        "autostart_failed_msg":"⚠ ثبت اجرای خودکار ناموفق بود",
    },
    "en": {
        "app_title":"K1Motion","lang_btn":"🌐 فارسی","cameras":"Cameras",
        "add":"➕ Add","edit":"✎ Edit","delete":"🗑 Delete",
        "connect":"🔌 Connect","disconnect":"⏏ Disconnect",
        "connect_all":"🔗 Connect All","disconnect_all":"⛔ Disconnect All",
        "quick_params":"Quick Params",
        "sensitivity_quick":"Sensitivity (0 = hyper, 400 = lenient)",
        "min_area_quick":"Min Motion Area (px)",
        "volume_title":"🔊 Volume",
        "volume_hint":"Nonlinear curve: 50=25%, 10=1%, 2≈silent",
        "pause_sending":"⏸  Pause sending to Bale","resume_sending":"▶  Resume sending to Bale",
        "hide_tray":"🔽  Hide to tray","settings":"⚙  General Settings",
        "recordings_folder":"📁 Recordings Folder","reset_settings":"🧹  Reset Settings",
        "quit_app":"✖  Quit","set_roi":"▭ Set ROI","clear_roi":"✕ Clear ROI",
        "test_alarm":"▶ Test Alarm","back_grid":"⊟ Back to Grid",
        "layout":"Layout:","fullscreen":"⛶ Fullscreen",
        "ready":"Ready","connecting":"Connecting…","connected":"Connected",
        "conn_failed_retry":"Connection failed — retrying…",
        "disconnected_reconnect":"Disconnected — reconnecting…",
        "disconnected":"Disconnected","all_disconnected":"All disconnected",
        "no_cam_selected":"\"{name}\" not connected — click \"Connect\"",
        "no_cam_selected_short":"\"{name}\" is disconnected",
        "motion_detected":"⚠ Motion detected","monitoring":"Monitoring…","cooldown":"Cooldown…",
        "hidden_tray":"🔽 App hidden to tray",
        "tray_hint":"App minimized to system tray. Double-click the tray icon to restore.",
        "tray_tooltip":"K1Motion — Running","tray_show":"👁  Show window",
        "tray_hide":"🔽  Hide","tray_settings":"⚙  Settings","tray_exit":"✖  Exit",
        "grid_hint":"Grid view — double-click a camera to zoom in",
        "single_hint":"Single view — double-click to go back to grid",
        "roi_status":"ROI: x={x} y={y} w={w} h={h}","roi_cleared":"ROI cleared",
        "settings_saved":"Settings saved",
        "error":"Error","info":"Info","ok":"OK","yes":"Yes","no":"No",
        "cam_settings_title":"Camera Settings","cam_name":"Camera Name:",
        "ip_host":"IP / Host:","rtsp_port":"RTSP Port:","username":"Username:",
        "password":"Password:","rtsp_path":"RTSP Path:",
        "rtsp_path_placeholder":"e.g. /Streaming/Channels/101 — empty = auto-detect",
        "auto_detect":"🔍  Auto-detect RTSP path","motion_params":"Motion Detection Params",
        "sensitivity":"Sensitivity (0 = hyper, 400 = lenient):",
        "min_area_px":"Min Motion Area (pixels):","process_every":"Process every N frames:",
        "sensitivity_help_btn":"❔  Sensitivity / Min Area help",
        "save":"Save","cancel":"Cancel","detecting":"Testing RTSP paths…",
        "rtsp_detect_title":"RTSP Auto-detect","testing":"Testing {i}/{n}:\n{path}",
        "found":"Found","found_msg":"Working path:\n{path}","not_found":"Not found",
        "not_found_msg":"No common path worked. Please enter it manually.",
        "need_ip_first":"Please enter IP first.",
        "sensitivity_help":(
            "🔹 Sensitivity (0-400): threshold\n"
            "  0=hyper | 25=sensitive | 100=balanced | 200=lenient | 300+=only big\n\n"
            "🔹 Min Area (pixels):\n"
            "  100=bird | 400=hand | 2000=person | 8000=car\n\n"
            "🔹 Tip: if foliage triggers, increase Sensitivity or Min Area.\n"
            "   If people are missed, decrease Sensitivity."
        ),
        "default_cam_name":"Camera {n}","camera":"Camera",
        "general_settings_title":"General Settings",
        "alarm_duration":"Alarm duration (sec):","cooldown_label":"Cooldown (sec):",
        "play_sound":"Play alarm sound","volume_setting":"Volume:",
        "always_on_top":"Bring window to front on alarm (over all apps)",
        "autostart_windows":"Auto-start with Windows",
        "autostart_hint":"Register the app in Windows startup",
        "record_on_motion":"Record video on motion","record_duration":"Recording duration (sec):",
        "recordings_dir_label":"Recordings folder:","default_rec_dir":"Default: {dir}",
        "send_to_bale_section":"📨 Send to Bale messenger",
        "send_to_bale":"Send video to Bale",
        "bot_token":"Bot token:","bot_token_placeholder":"Bale bot token (from BotFather)",
        "chat_id":"Chat ID:","chat_id_placeholder":"Your Chat ID",
        "message_text":"Message text:","message_text_placeholder":"Message caption",
        "send_test":"📤 Send test message to Bale",
        "test_success":"Success","test_success_msg":"Test message sent. Check Bale.",
        "test_failed":"Failed","test_failed_msg":"Bale response:\n{code}\n{body}",
        "test_error_msg":"Test failed:\n{err}",
        "need_token_chat":"Please enter token and Chat ID first.",
        "pick_rec_dir":"Select recordings folder","pick_rec_dir_btn":"…",
        "reset_confirm_title":"Reset Settings",
        "reset_confirm":"Delete all settings?\nThe app will close.",
        "reset_done":"Reset","reset_done_msg":"Settings cleared.",
        "min_one_cam":"At least one camera must remain.",
        "delete_cam_title":"Delete","delete_cam_confirm":"Delete \"{name}\"?",
        "need_ip":"Enter IP and camera details first.",
        "needs_roi":"ROI required","needs_roi_msg":"First draw a box using \"Set ROI\".",
        "sending_paused":"⏸ Sending paused — video saved",
        "sending_processing":"⏳ Converting and sending…",
        "sending_ok":"✅ Video sent to Bale",
        "sending_fail":"❌ Sending to Bale failed (check console)",
        "motion_overlay":"⚠  MOTION DETECTED  ⚠",
        "default_caption":"⚠ Motion detected\nCamera: {name}\nDate: {date}\nTime: {time}",
        "autostart_enabled_msg":"✅ Auto-start with Windows enabled",
        "autostart_disabled_msg":"Auto-start with Windows disabled",
        "autostart_failed_msg":"⚠ Failed to register auto-start",
    },
}

_current_lang = "fa"

def set_language(lang: str):
    global _current_lang
    _current_lang = lang if lang in TRANSLATIONS else "fa"
    try:
        app = QApplication.instance()
        if app is not None:
            app.setLayoutDirection(Qt.RightToLeft if _current_lang == "fa" else Qt.LeftToRight)
    except Exception: pass

def get_language() -> str: return _current_lang

def T(key: str, **kwargs) -> str:
    d = TRANSLATIONS.get(_current_lang) or TRANSLATIONS["fa"]
    text = d.get(key) or TRANSLATIONS["fa"].get(key, key)
    if kwargs:
        try: return text.format(**kwargs)
        except Exception: return text
    return text


def get_app_icon() -> QIcon:
    if getattr(sys, 'frozen', False):
        try:
            base = Path(sys._MEIPASS)
            for pat in ("icon.ico", "*.ico", "icon.png", "*.png"):
                found = list(base.rglob(pat))
                if found: return QIcon(str(found[0]))
        except Exception: pass
    for name in ("icon.ico", "icon.png"):
        p = Path(__file__).parent / name
        if p.exists(): return QIcon(str(p))
    pix = QPixmap(64, 64); pix.fill(QColor(30, 30, 30))
    painter = QPainter(pix); painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(QColor(0, 230, 100), 5)); painter.drawEllipse(8, 8, 48, 48)
    painter.setPen(QPen(QColor(255, 255, 255), 4))
    painter.drawLine(20, 32, 44, 32); painter.drawLine(32, 20, 32, 44)
    painter.end(); return QIcon(pix)


# ============================================================================
# مدل داده
# ============================================================================
@dataclass
class CameraConfig:
    name: str = ""
    ip: str = ""
    port: int = 554
    user: str = "admin"
    password: str = ""
    rtsp_path: str = ""
    enabled: bool = True
    roi: Optional[List[int]] = None
    sensitivity: int = SENSITIVITY_DEFAULT
    min_area: int = 400
    detect_every: int = 3

    @staticmethod
    def from_dict(d):
        c = CameraConfig()
        for k, v in d.items():
            if hasattr(c, k): setattr(c, k, v)
        if c.roi is not None: c.roi = list(c.roi)
        c.sensitivity = int(c.sensitivity)
        return c


@dataclass
class GlobalSettings:
    cameras: List[CameraConfig] = field(default_factory=list)
    alarm_duration: int = 8
    cooldown: int = 5
    sound: bool = True
    volume: int = 80
    always_on_top: bool = True
    autostart: bool = False
    record_enabled: bool = True
    record_duration: int = 5
    grid_cols: int = 3
    grid_rows: int = 3
    recordings_dir: str = ""
    language: str = "fa"
    messenger_enabled: bool = False
    messenger_paused: bool = False
    messenger_token: str = ""
    messenger_chat_id: str = ""
    messenger_caption: str = ""

    @staticmethod
    def from_dict(d):
        g = GlobalSettings()
        for k in ("alarm_duration","cooldown","sound","volume","always_on_top",
                  "autostart","record_enabled","record_duration","grid_cols","grid_rows",
                  "recordings_dir","language","messenger_enabled","messenger_paused",
                  "messenger_token","messenger_chat_id","messenger_caption"):
            if k in d: setattr(g, k, d[k])
        g.cameras = [CameraConfig.from_dict(c) for c in d.get("cameras", [])]
        return g


def load_settings() -> GlobalSettings:
    if SETTINGS_FILE.exists():
        try: return GlobalSettings.from_dict(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception as e: print("load settings failed:", e)
    g = GlobalSettings(); g.cameras.append(CameraConfig(name="")); return g


def save_settings(g: GlobalSettings):
    try:
        data = {
            "cameras": [asdict(c) for c in g.cameras],
            "alarm_duration": g.alarm_duration, "cooldown": g.cooldown,
            "sound": g.sound, "volume": g.volume, "always_on_top": g.always_on_top,
            "autostart": g.autostart,
            "record_enabled": g.record_enabled, "record_duration": g.record_duration,
            "grid_cols": g.grid_cols, "grid_rows": g.grid_rows,
            "recordings_dir": g.recordings_dir, "language": g.language,
            "messenger_enabled": g.messenger_enabled, "messenger_paused": g.messenger_paused,
            "messenger_token": g.messenger_token, "messenger_chat_id": g.messenger_chat_id,
            "messenger_caption": g.messenger_caption,
        }
        SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e: print("save settings failed:", e)


def build_rtsp_url(c: CameraConfig) -> str:
    if c.rtsp_path:
        p = c.rtsp_path
        if p.startswith("rtsp://"): return p
        if not p.startswith("/"): p = "/" + p
    else: p = "/"
    u = urllib.parse.quote(c.user or "", safe="")
    pw = urllib.parse.quote(c.password or "", safe="")
    return f"rtsp://{u}:{pw}@{c.ip}:{c.port}{p}"


COMMON_PATHS = [
    "/Streaming/Channels/101", "/Streaming/Channels/102",
    "/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1",
    "/h264/ch1/main/av_stream", "/h264/ch1/sub/av_stream",
    "/stream1", "/stream2", "/h264", "/live", "/live/ch0",
    "/video1", "/video2", "/11", "/12", "/av0_0",
    "/onvif1", "/onvif2", "/media/video1", "/ch01/0",
]


# ============================================================================
# صدا
# ============================================================================
_ALARM_WAV_LAST_VOLUME = -1

def volume_to_scale(volume: int) -> float:
    v = max(0, min(100, int(volume)))
    if v <= 0: return 0.0
    return (v / 100.0) ** 2


def ensure_alarm_wav(volume: int = 80) -> str:
    global _ALARM_WAV_LAST_VOLUME
    volume = max(0, min(100, int(volume)))
    if ALARM_WAV.exists() and _ALARM_WAV_LAST_VOLUME == volume:
        return str(ALARM_WAV)
    sr = 44100; dur = 0.6; n = int(sr * dur)
    frames = bytearray(); attack, release = 0.02, 0.06
    scale = volume_to_scale(volume)
    if scale <= 1e-7:
        with wave.open(str(ALARM_WAV), "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(b"\x00" * (n * 2))
        _ALARM_WAV_LAST_VOLUME = volume
        print(f"🔊 alarm wav: mute"); return str(ALARM_WAV)
    for i in range(n):
        t = i / sr; env = 1.0
        if t < attack: env = t / attack
        elif t > dur - release: env = (dur - t) / release
        v = 0.35 * math.sin(2*math.pi*880*t) + 0.35 * math.sin(2*math.pi*1318*t)
        s = int(32767 * env * v * scale); s = max(-32767, min(32767, s))
        frames += struct.pack("<h", s)
    with wave.open(str(ALARM_WAV), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(bytes(frames))
    _ALARM_WAV_LAST_VOLUME = volume
    print(f"🔊 alarm wav: vol={volume}% scale={scale:.6f}")
    return str(ALARM_WAV)


_sound_effect = None

def play_alarm_sound(volume: int = 80):
    global _sound_effect
    if int(volume) <= 0: print("▶ alarm muted"); return
    path = ensure_alarm_wav(volume)
    try:
        from PySide6.QtMultimedia import QSoundEffect
        if _sound_effect is None: _sound_effect = QSoundEffect()
        _sound_effect.setSource(QUrl.fromLocalFile(path))
        _sound_effect.setVolume(1.0)
        _sound_effect.setLoopCount(QSoundEffect.Infinite)
        _sound_effect.play()
        print(f"▶ alarm via QSoundEffect (vol={volume}%)"); return
    except Exception as e: print("QSoundEffect failed:", e)
    if IS_WIN:
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
            print(f"▶ alarm via winsound (vol={volume}%)")
        except Exception as e: print("winsound failed:", e)


def stop_alarm_sound():
    global _sound_effect
    try:
        if _sound_effect is not None: _sound_effect.stop()
    except Exception: pass
    if IS_WIN:
        try: winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception: pass


def open_recordings_folder():
    p = str(get_recordings_dir())
    try:
        if IS_WIN: os.startfile(p)
        elif sys.platform == "darwin": subprocess.Popen(["open", p])
        else: subprocess.Popen(["xdg-open", p])
    except Exception as e: print("open folder failed:", e)


# ============================================================================
# ffmpeg + bale
# ============================================================================
def get_ffmpeg_exe() -> Optional[str]:
    if getattr(sys, 'frozen', False):
        try:
            base = Path(sys._MEIPASS)
            for pat in ("ffmpeg*.exe", "ffmpeg*"):
                found = list(base.rglob(pat))
                if found: return str(found[0])
        except Exception: pass
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception: pass
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        if r.returncode == 0: return "ffmpeg"
    except Exception: pass
    return None


def convert_for_web(src_path: str, dst_path: Optional[str] = None) -> Optional[str]:
    ff = get_ffmpeg_exe()
    if not ff: return None
    if dst_path is None:
        p = Path(src_path); dst_path = str(p.with_name(p.stem + "_web.mp4"))
    if os.path.exists(dst_path) and os.path.getmtime(dst_path) > os.path.getmtime(src_path):
        return dst_path
    cmd = [ff,"-y","-loglevel","error","-i",src_path,
           "-c:v","libx264","-preset","veryfast","-profile:v","baseline",
           "-level","3.1","-pix_fmt","yuv420p","-movflags","+faststart",
           "-an",dst_path]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 1000:
            print(f"✅ web convert: {os.path.basename(dst_path)}"); return dst_path
    except Exception as e: print(f"❌ convert error: {e}")
    return None


def send_video_to_bale(token: str, chat_id: str, video_path: str, caption: str) -> bool:
    if not token or not chat_id or not os.path.exists(video_path): return False
    web_path = convert_for_web(video_path)
    send_path = web_path if web_path else video_path
    try:
        import asyncio
        Client = None
        try: from pyrobale.client import Client
        except Exception:
            try: from pyrobale import Client
            except Exception: pass
        if Client is not None:
            async def _send():
                bot = Client(token)
                with open(send_path, "rb") as f:
                    await bot.send_video(chat_id, video=f, caption=caption)
            asyncio.run(_send()); print("✅ sent via pyrobale."); return True
    except Exception as e: print(f"⚠ pyrobale failed: {e}")
    try:
        import requests
        url = f"https://tapi.bale.ai/bot{token}/sendVideo"
        with open(send_path, "rb") as f:
            files = {"video": f}; data = {"chat_id": chat_id, "caption": caption}
            r = requests.post(url, files=files, data=data, timeout=180)
        if r.status_code == 200 and '"ok":true' in r.text.replace(" ", ""):
            print("✅ sent via HTTP."); return True
    except Exception as e: print(f"❌ sending failed: {e}")
    return False


# ============================================================================
# Video widgets
# ============================================================================
class VideoWidget(QWidget):
    roi_selected = Signal(object); double_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.setStyleSheet("background-color:#0a0a0a;")
        self.setMouseTracking(True); self.setFocusPolicy(Qt.StrongFocus)
        self._frame=None; self._roi=None; self._motion_box=None
        self._select_mode=False; self._alarm=False
        self._drag_start=None; self._drag_end=None
        self._placeholder=""; self._title=""

    def set_frame(self, f): self._frame=f; self.update()
    def set_roi(self, roi):
        self._roi = list(roi) if roi else None
        self.roi_selected.emit(self._roi); self.update()
    def get_roi(self): return list(self._roi) if self._roi else None
    def set_motion_box(self, box): self._motion_box = tuple(box) if box else None; self.update()
    def set_select_mode(self, on):
        self._select_mode=on
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        if not on: self._drag_start=self._drag_end=None
        self.update()
    def set_alarm(self, on):
        self._alarm=on
        if not on: self._motion_box=None
        self.update()
    def set_placeholder(self, t): self._placeholder=t; self.update()
    def set_title(self, t): self._title=t; self.update()

    def _fit(self, sw, sh):
        ww, wh = max(1, self.width()), max(1, self.height())
        s = min(ww/sw, wh/sh); dw, dh = int(sw*s), int(sh*s)
        return s, (ww-dw)//2, (wh-dh)//2, dw, dh

    def _widget_to_video(self, wx, wy):
        if self._frame is None: return 0, 0
        vh, vw = self._frame.shape[:2]
        s, ox, oy, _, _ = self._fit(vw, vh)
        return int((wx-ox)/s), int((wy-oy)/s)

    def paintEvent(self, e):
        p = QPainter(self); p.fillRect(self.rect(), QColor("#0a0a0a"))
        if self._frame is None:
            p.setPen(QColor("#888")); p.setFont(QFont("Sans", 13))
            p.drawText(self.rect(), Qt.AlignCenter, self._placeholder); p.end(); return
        vh, vw = self._frame.shape[:2]
        s, ox, oy, dw, dh = self._fit(vw, vh)
        rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)
        qimg = QImage(rgb.data, vw, vh, vw*3, QImage.Format_RGB888)
        p.drawImage(QRect(ox, oy, dw, dh), qimg)
        if self._roi is not None:
            x,y,w,h = self._roi
            p.setPen(QPen(QColor(0,230,100),2,Qt.DashLine))
            p.drawRect(QRect(int(ox+x*s),int(oy+y*s),int(w*s),int(h*s)))
            p.setPen(QColor(0,230,100)); p.setFont(QFont("Sans",9,QFont.Bold))
            p.drawText(int(ox+x*s), int(oy+y*s)-4, "ROI")
        if self._select_mode and self._drag_start and self._drag_end:
            x0,y0 = self._drag_start; x1,y1 = self._drag_end
            p.setPen(QPen(QColor(255,200,0),2,Qt.DashLine))
            p.drawRect(QRect(min(x0,x1),min(y0,y1),abs(x1-x0),abs(y1-y0)))
        if self._alarm and self._motion_box is not None:
            mx,my,mw,mh = self._motion_box; pad=12
            rx=int(ox+(mx-pad)*s); ry=int(oy+(my-pad)*s)
            rw=int((mw+2*pad)*s); rh=int((mh+2*pad)*s)
            p.setPen(QPen(QColor(255,40,40),4)); p.drawRect(QRect(rx,ry,rw,rh))
            p.setPen(QPen(QColor(255,255,0),6)); L=22
            for a,b,c,d in [(rx,ry,rx+L,ry),(rx,ry,rx,ry+L),
                            (rx+rw,ry,rx+rw-L,ry),(rx+rw,ry,rx+rw,ry+L),
                            (rx,ry+rh,rx+L,ry+rh),(rx,ry+rh,rx,ry+rh-L),
                            (rx+rw,ry+rh,rx+rw-L,ry+rh),(rx+rw,ry+rh,rx+rw,ry+rh-L)]:
                p.drawLine(a,b,c,d)
        if self._alarm:
            p.setPen(QPen(QColor(255,40,40),8))
            p.drawRect(self.rect().adjusted(4,4,-4,-4))
            bh=66
            p.fillRect(QRect(0,0,self.width(),bh), QColor(200,0,0,225))
            p.setPen(QColor("white")); p.setFont(QFont("Sans",22,QFont.Bold))
            txt = T("motion_overlay")
            if self._title: txt += f"   —   {self._title}"
            p.drawText(QRect(0,0,self.width(),bh), Qt.AlignCenter, txt)
        p.end()

    def mousePressEvent(self, e):
        if not self._select_mode or e.button() != Qt.LeftButton: return
        pos = e.position(); self._drag_start=(pos.x(),pos.y())
        self._drag_end=self._drag_start; self.update()

    def mouseMoveEvent(self, e):
        if not self._select_mode or self._drag_start is None: return
        pos = e.position(); self._drag_end=(pos.x(),pos.y()); self.update()

    def mouseReleaseEvent(self, e):
        if not self._select_mode or self._drag_start is None: return
        x0,y0=self._drag_start; x1,y1=self._drag_end
        self._drag_start=self._drag_end=None
        if abs(x1-x0)<10 or abs(y1-y0)<10: self.update(); return
        vx0,vy0 = self._widget_to_video(min(x0,x1),min(y0,y1))
        vx1,vy1 = self._widget_to_video(max(x0,x1),max(y0,y1))
        if self._frame is not None:
            vh,vw = self._frame.shape[:2]
            vx0=max(0,min(vw-1,vx0)); vy0=max(0,min(vh-1,vy0))
            vx1=max(0,min(vw,vx1)); vy1=max(0,min(vh,vy1))
            if vx1-vx0>8 and vy1-vy0>8:
                self._roi=[vx0,vy0,vx1-vx0,vy1-vy0]
                self.roi_selected.emit(list(self._roi))
        self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and not self._select_mode:
            self.double_clicked.emit()


class VideoTile(QWidget):
    clicked = Signal(int); double_clicked = Signal(int)
    def __init__(self, idx: int, parent=None):
        super().__init__(parent)
        self.idx=idx; self._frame=None; self._title=""
        self._status=""; self._motion=False
        self.setStyleSheet("background-color:#0a0a0a;")
        self.setMinimumSize(120,70); self.setCursor(Qt.PointingHandCursor)
    def set_frame(self,f): self._frame=f; self.update()
    def set_title(self,t): self._title=t; self.update()
    def set_status(self,s): self._status=s; self.update()
    def set_motion(self,on): self._motion=on; self.update()
    def mousePressEvent(self,e):
        if e.button()==Qt.LeftButton: self.clicked.emit(self.idx)
    def mouseDoubleClickEvent(self,e):
        if e.button()==Qt.LeftButton: self.double_clicked.emit(self.idx)
    def paintEvent(self,e):
        p = QPainter(self); p.fillRect(self.rect(), QColor("#0a0a0a"))
        if self._frame is None:
            p.setPen(QColor("#666")); p.setFont(QFont("Sans",10))
            p.drawText(self.rect(), Qt.AlignCenter, self._title or "—")
            p.setPen(QPen(QColor(60,60,60),1))
            p.drawRect(self.rect().adjusted(1,1,-2,-2)); p.end(); return
        vh,vw = self._frame.shape[:2]
        ww,wh = max(1,self.width()), max(1,self.height())
        s=min(ww/vw,wh/vh); dw,dh = int(vw*s),int(vh*s)
        ox,oy = (ww-dw)//2,(wh-dh)//2
        rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)
        qimg = QImage(rgb.data,vw,vh,vw*3,QImage.Format_RGB888)
        p.drawImage(QRect(ox,oy,dw,dh), qimg)
        bh=20
        p.fillRect(QRect(0,self.height()-bh,self.width(),bh), QColor(0,0,0,180))
        p.setPen(QColor("white")); p.setFont(QFont("Sans",9,QFont.Bold))
        title = self._title or "—"
        if self._status and self._status != T("connected"):
            title += f"  ({self._status})"
        p.drawText(QRect(4,self.height()-bh,self.width()-8,bh),
                   Qt.AlignVCenter|Qt.AlignLeft, title)
        if self._motion:
            p.setPen(QPen(QColor(255,40,40),3)); p.drawRect(self.rect().adjusted(2,2,-3,-3))
        else:
            p.setPen(QPen(QColor(60,60,60),1)); p.drawRect(self.rect().adjusted(1,1,-2,-2))
        p.end()


class GridView(QWidget):
    tile_clicked = Signal(int); tile_double_clicked = Signal(int)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color:#000;")
        self._layout = QGridLayout(self)
        self._layout.setSpacing(2); self._layout.setContentsMargins(2,2,2,2)
        self._tiles=[]; self._cols=3; self._rows=3
    def set_grid_size(self, cols, rows):
        while self._layout.count():
            item = self._layout.takeAt(0); w = item.widget()
            if w: w.setParent(None); w.deleteLater()
        self._tiles=[]; self._cols, self._rows = cols, rows
        for i in range(cols*rows):
            t = VideoTile(i)
            t.clicked.connect(self.tile_clicked.emit)
            t.double_clicked.connect(self.tile_double_clicked.emit)
            self._layout.addWidget(t, i//cols, i%cols); self._tiles.append(t)
        for r in range(rows): self._layout.setRowStretch(r,1)
        for c in range(cols): self._layout.setColumnStretch(c,1)
    def set_frame(self, idx, frame):
        if 0 <= idx < len(self._tiles): self._tiles[idx].set_frame(frame)
    def set_title(self, idx, title):
        if 0 <= idx < len(self._tiles): self._tiles[idx].set_title(title)
    def set_status(self, idx, status):
        if 0 <= idx < len(self._tiles): self._tiles[idx].set_status(status)
    def set_motion(self, idx, on):
        if 0 <= idx < len(self._tiles): self._tiles[idx].set_motion(on)


# ============================================================================
# CaptureThread (نسخه اصلاح‌شده — بدون هشدار در خروج)
# ============================================================================
class CaptureThread(QThread):
    frame_ready = Signal(int, object)
    status = Signal(int, str)
    motion = Signal(int, bool, object)
    recording_finished = Signal(int, str)

    def __init__(self, idx, cam, parent=None):
        super().__init__(parent)
        self.idx=idx; self.cam=cam
        self.url = build_rtsp_url(cam)
        self._running=True
        self._frame_lock = threading.Lock()
        self._latest=None
        self.roi = list(cam.roi) if cam.roi else None
        self.sensitivity = int(cam.sensitivity)
        self.min_area = int(cam.min_area)
        self.detect_every = int(cam.detect_every)
        self._cnt=0; self._bg=None
        self._last_motion=False; self._last_emit=0.0
        self._record_until=0.0; self._writer=None
        self._rec_path=None; self._fps=20
        self._cap=None
        self._writer_lock = threading.Lock()

    def get_frame(self):
        with self._frame_lock: return self._latest

    def stop(self):
        """اول writer را می‌بندیم، بعد cap را آزاد می‌کنیم."""
        if not self._running and self._cap is None and self._writer is None:
            return
        self._running = False
        try: self._close_writer()
        except Exception: pass
        try:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
        except Exception: pass

    def reset_bg(self): self._bg=None

    def start_recording(self, duration):
        if not self._running: return
        if self._writer is None:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = "".join(ch for ch in (self.cam.name or "cam") if ch.isalnum() or ch in "-_")[:24] or "cam"
            self._rec_path = get_recordings_dir() / f"{ts}_{safe}.mp4"
        self._record_until = time.time() + max(1, int(duration))

    def _open_writer(self, path, fps, size):
        for codec, ext in [("avc1",".mp4"),("H264",".mp4"),("h264",".mp4"),
                           ("mp4v",".mp4"),("MJPG",".avi")]:
            try:
                p = path.with_suffix(ext)
                fourcc = cv2.VideoWriter_fourcc(*codec)
                w = cv2.VideoWriter(str(p), fourcc, fps, size)
                if w.isOpened():
                    self._rec_path = p
                    print(f"🎥 codec: {codec} → {p.name}")
                    return w
                try: w.release()
                except Exception: pass
            except Exception as e: print(f"codec {codec} failed: {e}")
        return None

    def _close_writer(self):
        with self._writer_lock:
            writer = self._writer
            rec_path = self._rec_path
            self._writer = None
            self._rec_path = None
            self._record_until = 0.0
            if writer is not None:
                try: writer.release()
                except Exception: pass
            if rec_path is not None:
                self.status.emit(self.idx, f"🎬 {rec_path.name}")
                try:
                    if rec_path.exists() and rec_path.stat().st_size > 2000:
                        self.recording_finished.emit(self.idx, str(rec_path))
                except Exception as e: print("emit recording_finished failed:", e)

    def run(self):
        cap = None
        try:
            while self._running:
                if cap is None:
                    self.status.emit(self.idx, T("connecting"))
                    try:
                        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    except Exception:
                        try: cap = cv2.VideoCapture(self.url)
                        except Exception: cap = None
                    if cap is None:
                        self.msleep(3000); continue
                    self._cap = cap
                    try:
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        fps = cap.get(cv2.CAP_PROP_FPS)
                        if 1 < fps < 120: self._fps = int(round(fps))
                    except Exception: pass
                    if not cap.isOpened():
                        self.status.emit(self.idx, T("conn_failed_retry"))
                        try: cap.release()
                        except Exception: pass
                        self._cap = None; cap = None
                        self.msleep(3000); continue
                    self.status.emit(self.idx, T("connected"))

                if not self._running: break

                # read محافظت‌شده
                ret = False; frame = None
                try:
                    ret, frame = cap.read()
                except Exception as e:
                    if not self._running: break
                    print(f"⚠ cap.read exception (cam {self.idx}): {e}")
                    ret, frame = False, None

                if not self._running: break

                if not ret or frame is None:
                    self.status.emit(self.idx, T("disconnected_reconnect"))
                    try: cap.release()
                    except Exception: pass
                    self._cap = None; cap = None
                    self._close_writer()
                    self.msleep(500); continue

                with self._frame_lock:
                    self._latest = frame

                now = time.time()
                if now - self._last_emit >= 0.033:
                    self._last_emit = now
                    try: self.frame_ready.emit(self.idx, frame)
                    except Exception: pass

                # ضبط
                if self._running and self._record_until > 0:
                    if now < self._record_until:
                        with self._writer_lock:
                            if not self._running: break
                            if self._writer is None and self._rec_path is not None:
                                h, w = frame.shape[:2]
                                self._writer = self._open_writer(self._rec_path, self._fps, (w, h))
                            if self._writer is not None and self._writer.isOpened():
                                try: self._writer.write(frame)
                                except Exception: pass
                    else:
                        self._close_writer()

                self._cnt += 1
                if self.roi is not None and (self._cnt % max(1, self.detect_every) == 0):
                    m, box = self._detect(frame)
                    if m != self._last_motion:
                        self._last_motion=m
                        try: self.motion.emit(self.idx, m, box)
                        except Exception: pass
                    elif m:
                        try: self.motion.emit(self.idx, True, box)
                        except Exception: pass
        except Exception as e:
            print(f"⚠ CaptureThread[{self.idx}] run() error: {e}")
        finally:
            try: self._close_writer()
            except Exception: pass
            if cap is not None:
                try: cap.release()
                except Exception: pass
            self._cap = None

    def _detect(self, frame):
        try:
            x,y,w,h = self.roi
            fh,fw = frame.shape[:2]
            x = max(0, min(fw-1, int(x))); y = max(0, min(fh-1, int(y)))
            w = min(int(w), fw-x); h = min(int(h), fh-y)
            if w < 8 or h < 8: return False, None
            roi = frame[y:y+h, x:x+w]
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (11,11), 0)
            if self._bg is None or self._bg.shape != gray.shape:
                self._bg = gray.astype(np.float32); return False, None
            cv2.accumulateWeighted(gray, self._bg, 0.08)
            bg = cv2.convertScaleAbs(self._bg)
            diff = cv2.absdiff(gray, bg)
            th_val = sensitivity_to_threshold(int(self.sensitivity))
            _, th = cv2.threshold(diff, th_val, 255, cv2.THRESH_BINARY)
            th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3,3), np.uint8))
            th = cv2.dilate(th, np.ones((5,5), np.uint8), iterations=2)
            if int(cv2.countNonZero(th)) < int(self.min_area):
                return False, None
            contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours: return True, None
            c = max(contours, key=cv2.contourArea)
            bx, by, bw, bh = cv2.boundingRect(c)
            return True, (x+bx, y+by, bw, bh)
        except Exception:
            return False, None


class ProbeThread(QThread):
    found = Signal(str); done = Signal(); progress = Signal(int, int, str)
    def __init__(self, ip, port, user, password, paths):
        super().__init__()
        self.ip,self.port,self.user,self.password = ip,port,user,password
        self.paths = paths; self._stop = False
    def stop(self): self._stop=True
    def run(self):
        u = urllib.parse.quote(self.user or "", safe="")
        pw = urllib.parse.quote(self.password or "", safe="")
        prev = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|stimeout;2500000|timeout;2500000|max_delay;300000")
        try:
            for i, path in enumerate(self.paths):
                if self._stop: break
                self.progress.emit(i+1, len(self.paths), path)
                url = f"rtsp://{u}:{pw}@{self.ip}:{self.port}{path}"
                cap = None
                try:
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                    ok = cap.isOpened()
                    if ok:
                        ret, fr = cap.read(); ok = bool(ret and fr is not None)
                    if ok: self.found.emit(path); return
                except Exception: pass
                finally:
                    if cap is not None:
                        try: cap.release()
                        except Exception: pass
        finally:
            if prev: os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = prev
            self.done.emit()


# ============================================================================
# Camera dialog
# ============================================================================
class CameraEditDialog(QDialog):
    def __init__(self, cam, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("cam_settings_title"))
        self.resize(520, 480)
        self.cam = CameraConfig.from_dict(asdict(cam))
        self._build()

    def _build(self):
        form = QFormLayout(self)
        self.name_edit = QLineEdit(self.cam.name)
        self.ip_edit = QLineEdit(self.cam.ip)
        self.port_spin = QSpinBox(); self.port_spin.setRange(1, 65535); self.port_spin.setValue(self.cam.port)
        self.user_edit = QLineEdit(self.cam.user)
        self.pass_edit = QLineEdit(self.cam.password); self.pass_edit.setEchoMode(QLineEdit.Password)
        self.path_edit = QLineEdit(self.cam.rtsp_path)
        self.path_edit.setPlaceholderText(T("rtsp_path_placeholder"))
        form.addRow(T("cam_name"), self.name_edit)
        form.addRow(T("ip_host"), self.ip_edit)
        form.addRow(T("rtsp_port"), self.port_spin)
        form.addRow(T("username"), self.user_edit)
        form.addRow(T("password"), self.pass_edit)
        form.addRow(T("rtsp_path"), self.path_edit)
        self.auto_btn = QPushButton(T("auto_detect"))
        self.auto_btn.clicked.connect(self._auto_detect)
        form.addRow("", self.auto_btn)

        grp = QGroupBox(T("motion_params"))
        gf = QFormLayout(grp)
        sens_row = QHBoxLayout()
        self.sens_slider = QSlider(Qt.Horizontal)
        self.sens_slider.setRange(0, SENSITIVITY_MAX)
        self.sens_slider.setValue(int(self.cam.sensitivity))
        self.sens_slider.setToolTip(T("sensitivity_quick"))
        self.sens_val = QLabel(str(self.cam.sensitivity)); self.sens_val.setFixedWidth(42)
        self.sens_val.setStyleSheet("font-weight:bold;")
        self.sens_slider.valueChanged.connect(lambda v: self.sens_val.setText(str(v)))
        sens_row.addWidget(self.sens_slider); sens_row.addWidget(self.sens_val)
        sw = QWidget(); sw.setLayout(sens_row)
        gf.addRow(T("sensitivity"), sw)
        self.area_spin = QSpinBox()
        self.area_spin.setRange(20, 200000); self.area_spin.setValue(int(self.cam.min_area))
        gf.addRow(T("min_area_px"), self.area_spin)
        self.detect_spin = QSpinBox()
        self.detect_spin.setRange(1, 30); self.detect_spin.setValue(int(self.cam.detect_every))
        gf.addRow(T("process_every"), self.detect_spin)
        help_btn = QPushButton(T("sensitivity_help_btn"))
        help_btn.clicked.connect(self._show_help)
        gf.addRow("", help_btn)
        form.addRow(grp)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(T("save"))
        bb.button(QDialogButtonBox.Cancel).setText(T("cancel"))
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _show_help(self):
        QMessageBox.information(self, T("info"), T("sensitivity_help"))

    def _auto_detect(self):
        ip = self.ip_edit.text().strip()
        if not ip:
            QMessageBox.warning(self, T("error"), T("need_ip_first")); return
        dlg = QProgressDialog(T("detecting"), T("cancel"), 0, len(COMMON_PATHS), self)
        dlg.setWindowTitle(T("rtsp_detect_title")); dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        probe = ProbeThread(ip, self.port_spin.value(), self.user_edit.text().strip(),
                            self.pass_edit.text(), COMMON_PATHS)
        self._probe = probe
        def on_prog(i, n, path):
            dlg.setValue(i-1); dlg.setLabelText(T("testing", i=i, n=n, path=path))
        def on_found(path):
            self.path_edit.setText(path); dlg.close()
            QMessageBox.information(self, T("found"), T("found_msg", path=path))
        def on_done():
            dlg.close()
            if not self.path_edit.text().strip():
                QMessageBox.warning(self, T("not_found"), T("not_found_msg"))
        probe.progress.connect(on_prog); probe.found.connect(on_found); probe.done.connect(on_done)
        dlg.canceled.connect(probe.stop); probe.start()

    def get_camera(self):
        c = CameraConfig.from_dict(asdict(self.cam))
        c.name = self.name_edit.text().strip() or T("camera")
        c.ip = self.ip_edit.text().strip()
        c.port = self.port_spin.value()
        c.user = self.user_edit.text().strip()
        c.password = self.pass_edit.text()
        c.rtsp_path = self.path_edit.text().strip()
        c.sensitivity = int(self.sens_slider.value())
        c.min_area = int(self.area_spin.value())
        c.detect_every = int(self.detect_spin.value())
        return c


# ============================================================================
# Global settings dialog
# ============================================================================
class GlobalSettingsDialog(QDialog):
    def __init__(self, g, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("general_settings_title"))
        form = QFormLayout(self)
        self.alarm_spin = QSpinBox(); self.alarm_spin.setRange(1,120); self.alarm_spin.setValue(g.alarm_duration)
        form.addRow(T("alarm_duration"), self.alarm_spin)
        self.cool_spin = QSpinBox(); self.cool_spin.setRange(0,300); self.cool_spin.setValue(g.cooldown)
        form.addRow(T("cooldown_label"), self.cool_spin)
        self.sound_chk = QCheckBox(T("play_sound")); self.sound_chk.setChecked(g.sound)
        form.addRow("", self.sound_chk)
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0,100); self.vol_slider.setValue(int(g.volume))
        self.vol_slider.setToolTip(T("volume_hint"))
        self.vol_lbl = QLabel(self._vol_label(g.volume)); self.vol_lbl.setFixedWidth(140)
        self.vol_slider.valueChanged.connect(lambda v: self.vol_lbl.setText(self._vol_label(v)))
        vrow = QHBoxLayout(); vrow.addWidget(self.vol_slider); vrow.addWidget(self.vol_lbl)
        vw = QWidget(); vw.setLayout(vrow)
        form.addRow(T("volume_setting"), vw)
        self.top_chk = QCheckBox(T("always_on_top")); self.top_chk.setChecked(g.always_on_top)
        form.addRow("", self.top_chk)
        self.autostart_chk = QCheckBox(T("autostart_windows")); self.autostart_chk.setChecked(g.autostart)
        self.autostart_chk.setToolTip(T("autostart_hint"))
        form.addRow("", self.autostart_chk)
        self.rec_chk = QCheckBox(T("record_on_motion")); self.rec_chk.setChecked(g.record_enabled)
        form.addRow("", self.rec_chk)
        self.rec_spin = QSpinBox(); self.rec_spin.setRange(1,120); self.rec_spin.setValue(g.record_duration)
        form.addRow(T("record_duration"), self.rec_spin)
        dir_row = QHBoxLayout()
        self.rec_dir_edit = QLineEdit(g.recordings_dir)
        self.rec_dir_edit.setPlaceholderText(T("default_rec_dir", dir=DEFAULT_RECORDINGS_DIR))
        self.rec_dir_btn = QPushButton(T("pick_rec_dir_btn")); self.rec_dir_btn.setFixedWidth(32)
        self.rec_dir_btn.clicked.connect(self._pick_rec_dir)
        dir_row.addWidget(self.rec_dir_edit); dir_row.addWidget(self.rec_dir_btn)
        dw = QWidget(); dw.setLayout(dir_row)
        form.addRow(T("recordings_dir_label"), dw)
        form.addRow(QLabel(f"<b>{T('send_to_bale_section')}</b>"))
        self.msg_chk = QCheckBox(T("send_to_bale")); self.msg_chk.setChecked(g.messenger_enabled)
        form.addRow("", self.msg_chk)
        self.msg_token = QLineEdit(g.messenger_token); self.msg_token.setEchoMode(QLineEdit.Password)
        self.msg_token.setPlaceholderText(T("bot_token_placeholder"))
        form.addRow(T("bot_token"), self.msg_token)
        self.msg_chat_id = QLineEdit(g.messenger_chat_id)
        self.msg_chat_id.setPlaceholderText(T("chat_id_placeholder"))
        form.addRow(T("chat_id"), self.msg_chat_id)
        self.msg_caption = QLineEdit(g.messenger_caption)
        self.msg_caption.setPlaceholderText(T("message_text_placeholder"))
        form.addRow(T("message_text"), self.msg_caption)
        test_btn = QPushButton(T("send_test")); test_btn.clicked.connect(self._test_bale)
        form.addRow("", test_btn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(T("save"))
        bb.button(QDialogButtonBox.Cancel).setText(T("cancel"))
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _vol_label(self, v):
        scale = volume_to_scale(int(v)); pct = scale*100.0
        if v <= 0: return "mute"
        if pct < 0.01: return f"{v}%  ({pct:.3f}%)"
        return f"{v}%  ({pct:.1f}%)"

    def _pick_rec_dir(self):
        start = self.rec_dir_edit.text().strip() or str(DEFAULT_RECORDINGS_DIR)
        p = QFileDialog.getExistingDirectory(self, T("pick_rec_dir"), start)
        if p: self.rec_dir_edit.setText(p)

    def _test_bale(self):
        token = self.msg_token.text().strip(); chat_id = self.msg_chat_id.text().strip()
        if not token or not chat_id:
            QMessageBox.warning(self, T("error"), T("need_token_chat")); return
        try:
            import requests
            url = f"https://tapi.bale.ai/bot{token}/sendMessage"
            r = requests.post(url, data={"chat_id": chat_id, "text":"✅ K1Motion test"}, timeout=15)
            if r.status_code == 200 and '"ok":true' in r.text.replace(" ", ""):
                QMessageBox.information(self, T("test_success"), T("test_success_msg"))
            else:
                QMessageBox.warning(self, T("test_failed"),
                                    T("test_failed_msg", code=r.status_code, body=r.text[:300]))
        except Exception as e:
            QMessageBox.critical(self, T("error"), T("test_error_msg", err=e))

    def apply_to(self, g):
        g.alarm_duration = self.alarm_spin.value()
        g.cooldown = self.cool_spin.value()
        g.sound = self.sound_chk.isChecked()
        g.volume = self.vol_slider.value()
        g.always_on_top = self.top_chk.isChecked()
        g.autostart = self.autostart_chk.isChecked()
        g.record_enabled = self.rec_chk.isChecked()
        g.record_duration = self.rec_spin.value()
        g.recordings_dir = self.rec_dir_edit.text().strip()
        g.messenger_enabled = self.msg_chk.isChecked()
        g.messenger_token = self.msg_token.text().strip()
        g.messenger_chat_id = self.msg_chat_id.text().strip()
        g.messenger_caption = self.msg_caption.text().strip()


# ============================================================================
# Main window
# ============================================================================
class MainWindow(QMainWindow):
    STATE_IDLE="IDLE"; STATE_NORMAL="NORMAL"; STATE_ALARM="ALARM"; STATE_COOLDOWN="COOLDOWN"

    def __init__(self):
        super().__init__()
        self.setWindowTitle(T("app_title"))
        self.resize(1250, 800)
        self.setWindowIcon(get_app_icon())

        self.settings = load_settings()
        set_recordings_dir(self.settings.recordings_dir)
        set_language(self.settings.language)

        self.capture_threads = {}
        self.camera_status = {}
        self.current_idx = 0
        self.state = self.STATE_IDLE
        self._last_motion_box = None
        self._pre_alarm_view = 1
        self._first_show = True
        self._quitting = False

        self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)

        if IS_WIN and self.settings.autostart:
            try:
                if not is_autostart_enabled(): set_autostart(True)
            except Exception as e: print("autostart sync failed:", e)

        ensure_alarm_wav(self.settings.volume)

        self._build_ui()
        self._setup_tray()
        self._refresh_camera_list()
        self._apply_grid_size()
        self._go_to_grid_view()
        self._update_pause_btn_style()
        self._retranslate_ui()

        self.alarm_timer = QTimer(self); self.alarm_timer.setSingleShot(True)
        self.alarm_timer.timeout.connect(self._exit_alarm)
        self.cooldown_timer = QTimer(self); self.cooldown_timer.setSingleShot(True)
        self.cooldown_timer.timeout.connect(self._end_cooldown)

    def _set_status(self, text):
        sb = self.statusBar()
        if sb is not None: sb.showMessage(text)

    def _apply_on_top(self):
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.show(); self.raise_(); self.activateWindow()

    def _remove_on_top(self):
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint); self.show()

    def _vol_label_short(self, v):
        scale = volume_to_scale(int(v)); pct = scale*100.0
        if v <= 0: return "mute"
        if pct < 0.01: return f"{v}%  (<0.01%)"
        return f"{v}%  ({pct:.1f}%)"

    def _build_ui(self):
        central = QWidget(); root = QHBoxLayout(central)
        root.setContentsMargins(8,8,8,8); root.setSpacing(8)

        left = QWidget(); left.setFixedWidth(300)
        lv = QVBoxLayout(left); lv.setContentsMargins(0,0,0,0)
        self.lbl_cameras = QLabel(); lv.addWidget(self.lbl_cameras)
        self.cam_list = QListWidget()
        self.cam_list.currentRowChanged.connect(self._on_camera_selected)
        lv.addWidget(self.cam_list, 1)
        row1 = QHBoxLayout()
        self.add_btn = QPushButton(); self.edit_btn = QPushButton(); self.del_btn = QPushButton()
        row1.addWidget(self.add_btn); row1.addWidget(self.edit_btn); row1.addWidget(self.del_btn)
        lv.addLayout(row1)
        row2 = QHBoxLayout()
        self.connect_btn = QPushButton(); self.disconnect_btn = QPushButton()
        row2.addWidget(self.connect_btn); row2.addWidget(self.disconnect_btn)
        lv.addLayout(row2)
        self.connect_all_btn = QPushButton(); self.disconnect_all_btn = QPushButton()
        lv.addWidget(self.connect_all_btn); lv.addWidget(self.disconnect_all_btn)
        lv.addSpacing(8)
        self.lbl_quick_params = QLabel(); lv.addWidget(self.lbl_quick_params)
        self.lbl_sens_quick = QLabel(); lv.addWidget(self.lbl_sens_quick)
        sens_row = QHBoxLayout()
        self.sens_quick = QSlider(Qt.Horizontal)
        self.sens_quick.setRange(0, SENSITIVITY_MAX)
        self.sens_quick.setValue(SENSITIVITY_DEFAULT)
        self.sens_quick.valueChanged.connect(self._on_quick_sens)
        self.sens_quick_val = QLabel(str(SENSITIVITY_DEFAULT)); self.sens_quick_val.setFixedWidth(42)
        self.sens_quick_val.setStyleSheet("font-weight:bold;")
        self.sens_quick.valueChanged.connect(lambda v: self.sens_quick_val.setText(str(v)))
        sens_row.addWidget(self.sens_quick); sens_row.addWidget(self.sens_quick_val)
        lv.addLayout(sens_row)
        self.lbl_area_quick = QLabel(); lv.addWidget(self.lbl_area_quick)
        self.area_quick = QSpinBox(); self.area_quick.setRange(20, 200000)
        self.area_quick.valueChanged.connect(self._on_quick_area)
        lv.addWidget(self.area_quick)
        lv.addSpacing(8)
        self.lbl_volume_title = QLabel(); lv.addWidget(self.lbl_volume_title)
        vol_row = QHBoxLayout()
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0,100); self.vol_slider.setValue(self.settings.volume)
        self.vol_slider.setToolTip(T("volume_hint"))
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.vol_lbl = QLabel(self._vol_label_short(self.settings.volume))
        self.vol_lbl.setFixedWidth(110); self.vol_lbl.setStyleSheet("font-weight:bold;")
        vol_row.addWidget(self.vol_slider); vol_row.addWidget(self.vol_lbl)
        lv.addLayout(vol_row)
        lv.addSpacing(8)
        self.pause_send_btn = QPushButton(); self.pause_send_btn.setCheckable(True)
        self.pause_send_btn.setChecked(self.settings.messenger_paused)
        self.pause_send_btn.toggled.connect(self._on_pause_send_toggled)
        lv.addWidget(self.pause_send_btn)
        lv.addSpacing(4)
        self.hide_btn = QPushButton(); self.hide_btn.setStyleSheet("padding:6px;")
        self.hide_btn.clicked.connect(self._hide_to_tray)
        lv.addWidget(self.hide_btn)
        lv.addSpacing(4)
        self.settings_btn = QPushButton(); lv.addWidget(self.settings_btn)
        self.open_rec_btn = QPushButton(); lv.addWidget(self.open_rec_btn)
        self.reset_btn = QPushButton(); self.reset_btn.clicked.connect(self._reset_settings)
        lv.addWidget(self.reset_btn)
        self.close_btn = QPushButton()
        self.close_btn.setStyleSheet("background-color:#a02020;color:white;font-weight:bold;padding:6px;")
        lv.addWidget(self.close_btn)
        root.addWidget(left)

        right = QWidget(); rv = QVBoxLayout(right)
        rv.setContentsMargins(0,0,0,0); rv.setSpacing(6)
        top = QHBoxLayout()
        self.title_lbl = QLabel("<b>—</b>"); self.title_lbl.setStyleSheet("font-size:14px;")
        top.addWidget(self.title_lbl, 1)
        self.roi_btn = QPushButton(); self.roi_btn.setCheckable(True)
        self.clear_roi_btn = QPushButton(); self.test_btn = QPushButton()
        top.addWidget(self.roi_btn); top.addWidget(self.clear_roi_btn); top.addWidget(self.test_btn)
        self.back_to_grid_btn = QPushButton()
        self.back_to_grid_btn.setVisible(False)
        self.back_to_grid_btn.clicked.connect(self._go_to_grid_view)
        top.addWidget(self.back_to_grid_btn)
        self.lbl_layout = QLabel(); top.addWidget(self.lbl_layout)
        self.grid_combo = QComboBox()
        for c in ("2×2","3×3","4×4","5×5"): self.grid_combo.addItem(c)
        idx = self.grid_combo.findText(f"{self.settings.grid_cols}×{self.settings.grid_rows}")
        if idx >= 0: self.grid_combo.setCurrentIndex(idx)
        self.grid_combo.currentIndexChanged.connect(self._on_grid_size_changed)
        top.addWidget(self.grid_combo)
        self.fs_btn = QPushButton(); top.addWidget(self.fs_btn)
        self.lang_btn = QPushButton()
        self.lang_btn.setToolTip("تغییر زبان / Change language")
        self.lang_btn.clicked.connect(self._toggle_language)
        top.addWidget(self.lang_btn)
        rv.addLayout(top)
        self.view_stack = QStackedWidget()
        self.video = VideoWidget()
        self.video.roi_selected.connect(self._on_roi_changed)
        self.video.double_clicked.connect(self._go_to_grid_view)
        self.view_stack.addWidget(self.video)
        self.grid_view = GridView()
        self.grid_view.tile_clicked.connect(self._on_grid_tile_clicked)
        self.grid_view.tile_double_clicked.connect(self._on_grid_tile_double_clicked)
        self.view_stack.addWidget(self.grid_view)
        rv.addWidget(self.view_stack, 1)
        root.addWidget(right, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        self.add_btn.clicked.connect(self._add_camera)
        self.edit_btn.clicked.connect(self._edit_camera)
        self.del_btn.clicked.connect(self._delete_camera)
        self.connect_btn.clicked.connect(self._connect_current)
        self.disconnect_btn.clicked.connect(self._disconnect_current)
        self.connect_all_btn.clicked.connect(self._connect_all)
        self.disconnect_all_btn.clicked.connect(self._disconnect_all)
        self.settings_btn.clicked.connect(self._open_settings)
        self.open_rec_btn.clicked.connect(open_recordings_folder)
        self.close_btn.clicked.connect(self._quit_app)
        self.roi_btn.toggled.connect(lambda on: self.video.set_select_mode(on))
        self.clear_roi_btn.clicked.connect(self._clear_roi)
        self.test_btn.clicked.connect(self._test_alarm)
        self.fs_btn.clicked.connect(self._toggle_fullscreen)

    def _retranslate_ui(self):
        self.lbl_cameras.setText(f"<b>{T('cameras')}</b>")
        self.add_btn.setText(T("add")); self.edit_btn.setText(T("edit")); self.del_btn.setText(T("delete"))
        self.connect_btn.setText(T("connect")); self.disconnect_btn.setText(T("disconnect"))
        self.connect_all_btn.setText(T("connect_all")); self.disconnect_all_btn.setText(T("disconnect_all"))
        self.lbl_quick_params.setText(f"<b>{T('quick_params')}</b>")
        self.lbl_sens_quick.setText(T("sensitivity_quick"))
        self.lbl_area_quick.setText(T("min_area_quick"))
        self.lbl_volume_title.setText(f"<b>{T('volume_title')}</b>")
        self.hide_btn.setText(T("hide_tray"))
        self.settings_btn.setText(T("settings"))
        self.open_rec_btn.setText(T("recordings_folder"))
        self.reset_btn.setText(T("reset_settings"))
        self.close_btn.setText(T("quit_app"))
        self.roi_btn.setText(T("set_roi")); self.clear_roi_btn.setText(T("clear_roi"))
        self.test_btn.setText(T("test_alarm"))
        self.back_to_grid_btn.setText(T("back_grid"))
        self.lbl_layout.setText(T("layout")); self.fs_btn.setText(T("fullscreen"))
        self.lang_btn.setText(T("lang_btn"))
        self.vol_slider.setToolTip(T("volume_hint"))
        self.sens_quick.setToolTip(T("sensitivity_quick"))
        self._update_pause_btn_style()
        if hasattr(self, "tray"):
            self.tray.setToolTip(T("tray_tooltip"))
            if hasattr(self, "_tray_actions"):
                a_show, a_hide, a_set, a_exit = self._tray_actions
                a_show.setText(T("tray_show")); a_hide.setText(T("tray_hide"))
                a_set.setText(T("tray_settings")); a_exit.setText(T("tray_exit"))
        if not self.settings.cameras:
            self.video.set_placeholder(T("ready"))
        else:
            idx = self.current_idx
            if idx not in self.capture_threads and idx < len(self.settings.cameras):
                self.video.set_placeholder(T("no_cam_selected", name=self.settings.cameras[idx].name))
        self._apply_grid_size()
        self.setWindowTitle(T("app_title"))

    def _toggle_language(self):
        new_lang = "en" if get_language() == "fa" else "fa"
        set_language(new_lang)
        self.settings.language = new_lang
        save_settings(self.settings); self._retranslate_ui()
        self._set_status(T("settings_saved"))

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(get_app_icon(), self)
        self.tray.setToolTip(T("tray_tooltip"))
        menu = QMenu()
        act_show = QAction(T("tray_show"), self); act_show.triggered.connect(self._restore_from_tray)
        act_hide = QAction(T("tray_hide"), self); act_hide.triggered.connect(self._hide_to_tray)
        act_settings = QAction(T("tray_settings"), self); act_settings.triggered.connect(self._tray_open_settings)
        act_exit = QAction(T("tray_exit"), self); act_exit.triggered.connect(self._quit_app)
        menu.addAction(act_show); menu.addAction(act_hide)
        menu.addSeparator(); menu.addAction(act_settings)
        menu.addSeparator(); menu.addAction(act_exit)
        self.tray.setContextMenu(menu); self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._tray_actions = (act_show, act_hide, act_settings, act_exit)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            if self.isVisible(): self._hide_to_tray()
            else: self._restore_from_tray()

    def _tray_open_settings(self):
        self._restore_from_tray(); self._open_settings()

    def _hide_to_tray(self):
        self.hide()
        try:
            self.tray.showMessage(T("app_title"), T("tray_hint"),
                                  QSystemTrayIcon.Information, 2500)
        except Exception: pass
        self._set_status(T("hidden_tray"))

    def _restore_from_tray(self):
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.raise_(); self.activateWindow()

    def _quit_app(self):
        self._quitting = True
        try: self.tray.hide()
        except Exception: pass
        self.close()

    def _reset_settings(self):
        r = QMessageBox.question(self, T("reset_confirm_title"), T("reset_confirm"))
        if r != QMessageBox.Yes: return
        try: self._disconnect_all()
        except Exception: pass
        try:
            if SETTINGS_FILE.exists(): SETTINGS_FILE.unlink()
        except Exception as e: print("reset failed:", e)
        QMessageBox.information(self, T("reset_done"), T("reset_done_msg"))
        self._quit_app()

    def _update_pause_btn_style(self):
        if self.settings.messenger_paused:
            self.pause_send_btn.setText(T("resume_sending"))
            self.pause_send_btn.setStyleSheet("background-color:#c08000;color:white;font-weight:bold;padding:6px;")
        else:
            self.pause_send_btn.setText(T("pause_sending"))
            self.pause_send_btn.setStyleSheet("padding:6px;")

    def _on_pause_send_toggled(self, checked):
        self.settings.messenger_paused = checked
        save_settings(self.settings); self._update_pause_btn_style()
        self._set_status(T("sending_paused") if checked else T("settings_saved"))

    def _apply_grid_size(self):
        cols, rows = self.settings.grid_cols, self.settings.grid_rows
        self.grid_view.set_grid_size(cols, rows)
        for i, c in enumerate(self.settings.cameras):
            if i >= cols*rows: break
            self.grid_view.set_title(i, c.name or T("camera"))
            self.grid_view.set_status(i, self.camera_status.get(i, T("disconnected")))
        for i in range(len(self.settings.cameras), cols*rows):
            self.grid_view.set_title(i, ""); self.grid_view.set_status(i, "")

    def _on_grid_size_changed(self, _):
        txt = self.grid_combo.currentText()
        try:
            c, r = txt.split("×")
            self.settings.grid_cols = int(c); self.settings.grid_rows = int(r)
            save_settings(self.settings); self._apply_grid_size()
        except Exception as e: print("grid parse:", e)

    def _go_to_grid_view(self):
        self.view_stack.setCurrentIndex(1)
        self.roi_btn.setEnabled(False); self.clear_roi_btn.setEnabled(False)
        self.test_btn.setEnabled(False); self.back_to_grid_btn.setVisible(False)
        self._apply_grid_size()
        self._set_status(T("grid_hint"))

    def _go_to_single_view(self):
        self.view_stack.setCurrentIndex(0)
        self.roi_btn.setEnabled(True); self.clear_roi_btn.setEnabled(True)
        self.test_btn.setEnabled(True); self.back_to_grid_btn.setVisible(True)
        self._set_status(T("single_hint"))

    def _on_grid_tile_clicked(self, idx):
        if idx >= len(self.settings.cameras): return
        self._switch_to_camera(idx)

    def _on_grid_tile_double_clicked(self, idx):
        if idx >= len(self.settings.cameras): return
        self._switch_to_camera(idx); self._go_to_single_view()

    def _refresh_camera_list(self):
        self.cam_list.blockSignals(True); self.cam_list.clear()
        for c in self.settings.cameras:
            name = c.name or T("camera"); ip = c.ip or "-"
            self.cam_list.addItem(QListWidgetItem(f"{name}  —  {ip}"))
        self.cam_list.blockSignals(False)
        if self.settings.cameras:
            idx = min(self.current_idx, len(self.settings.cameras)-1)
            self.cam_list.setCurrentRow(idx)
        self._apply_grid_size()

    def _on_camera_selected(self, row):
        if row < 0 or row >= len(self.settings.cameras): return
        self.current_idx = row
        c = self.settings.cameras[row]
        self.title_lbl.setText(f"<b>{c.name or T('camera')}</b> — {c.ip}:{c.port}")
        self.video.set_roi(c.roi)
        self.sens_quick.blockSignals(True); self.sens_quick.setValue(int(c.sensitivity)); self.sens_quick.blockSignals(False)
        self.sens_quick_val.setText(str(int(c.sensitivity)))
        self.area_quick.blockSignals(True); self.area_quick.setValue(int(c.min_area)); self.area_quick.blockSignals(False)
        t = self.capture_threads.get(row)
        if t is not None:
            f = t.get_frame()
            if f is not None: self.video.set_frame(f)
            else: self.video.set_frame(None)
        else:
            self.video.set_frame(None)
            self.video.set_placeholder(T("no_cam_selected", name=c.name or T("camera")))

    def _add_camera(self):
        cam = CameraConfig(name=T("default_cam_name", n=len(self.settings.cameras)+1))
        dlg = CameraEditDialog(cam, self)
        if dlg.exec() == QDialog.Accepted:
            self.settings.cameras.append(dlg.get_camera())
            save_settings(self.settings); self._refresh_camera_list()
            self.cam_list.setCurrentRow(len(self.settings.cameras)-1)

    def _edit_camera(self):
        if not self.settings.cameras: return
        c = self.settings.cameras[self.current_idx]
        dlg = CameraEditDialog(c, self)
        if dlg.exec() == QDialog.Accepted:
            newc = dlg.get_camera(); newc.roi = c.roi
            self.settings.cameras[self.current_idx] = newc
            save_settings(self.settings); self._refresh_camera_list()
            self._on_camera_selected(self.current_idx)
            self._apply_current_camera_params()
            print(f"⚙ cam {self.current_idx}: sens={newc.sensitivity} "
                  f"th={sensitivity_to_threshold(newc.sensitivity)} "
                  f"min_area={newc.min_area} every={newc.detect_every}")

    def _delete_camera(self):
        if not self.settings.cameras: return
        if len(self.settings.cameras) == 1:
            QMessageBox.warning(self, T("error"), T("min_one_cam")); return
        r = QMessageBox.question(self, T("delete_cam_title"),
            T("delete_cam_confirm", name=self.settings.cameras[self.current_idx].name or T("camera")))
        if r != QMessageBox.Yes: return
        if self.current_idx in self.capture_threads: self._disconnect_current()
        del self.settings.cameras[self.current_idx]
        save_settings(self.settings)
        self.current_idx = max(0, self.current_idx-1)
        self._refresh_camera_list()

    def _apply_current_camera_params(self):
        if not self.settings.cameras: return
        idx = self.current_idx
        if idx < 0 or idx >= len(self.settings.cameras): return
        c = self.settings.cameras[idx]
        t = self.capture_threads.get(idx)
        if t is None: return
        t.sensitivity = int(c.sensitivity)
        t.min_area = int(c.min_area)
        t.detect_every = max(1, int(c.detect_every))
        t.roi = list(c.roi) if c.roi else None
        print(f"🔧 thread[{idx}]: sens={t.sensitivity}→th={sensitivity_to_threshold(t.sensitivity)} "
              f"min_area={t.min_area} every={t.detect_every}")

    def _connect_current(self):
        if not self.settings.cameras: return
        idx = self.current_idx
        if idx in self.capture_threads: return
        c = self.settings.cameras[idx]
        if not c.ip and not c.rtsp_path:
            QMessageBox.warning(self, T("error"), T("need_ip")); return
        t = CaptureThread(idx, c, self)
        t.roi = list(c.roi) if c.roi else None
        t.sensitivity = int(c.sensitivity); t.min_area = int(c.min_area)
        t.detect_every = max(1, int(c.detect_every))
        t.frame_ready.connect(self._on_frame); t.status.connect(self._on_status)
        t.motion.connect(self._on_motion); t.recording_finished.connect(self._on_recording_finished)
        t.start(); self.capture_threads[idx] = t
        self._set_status(f"{c.name or T('camera')} — {T('connecting')}")

    def _disconnect_current(self):
        idx = self.current_idx
        if idx not in self.capture_threads: return
        t = self.capture_threads.pop(idx); t.stop()
        try: t.wait(1500)
        except Exception: pass
        if self.current_idx == idx:
            self.video.set_frame(None)
            if idx < len(self.settings.cameras):
                self.video.set_placeholder(T("no_cam_selected_short",
                                             name=self.settings.cameras[idx].name or T("camera")))
        self.grid_view.set_status(idx, T("disconnected"))
        if self.state == self.STATE_ALARM: self._exit_alarm()

    def _connect_all(self):
        old = self.current_idx
        for i in range(len(self.settings.cameras)):
            if i not in self.capture_threads:
                self.current_idx = i; self._connect_current()
        self.current_idx = old; self._on_camera_selected(self.current_idx)

    def _disconnect_all(self):
        """قطع همه threadها با انتظار کنترل‌شده."""
        threads = list(self.capture_threads.values())
        self.capture_threads.clear()
        for t in threads:
            try: t.stop()
            except Exception: pass
        # انتظار کوتاه
        for _ in range(20):  # حداکثر ۲ ثانیه
            alive = [t for t in threads if t.isRunning()]
            if not alive: break
            time.sleep(0.1)
        # برای باقی‌مانده‌ها بیشتر صبر کن
        for t in threads:
            if t.isRunning():
                try: t.wait(800)
                except Exception: pass
        self.video.set_frame(None)
        self._set_status(T("all_disconnected"))

    def _on_frame(self, idx, frame):
        if self._quitting: return
        if idx == self.current_idx: self.video.set_frame(frame)
        self.grid_view.set_frame(idx, frame)

    def _on_status(self, idx, txt):
        if self._quitting: return
        self.camera_status[idx] = txt
        self.grid_view.set_status(idx, txt)
        if idx == self.current_idx and idx < len(self.settings.cameras):
            self._set_status(f"{self.settings.cameras[idx].name or T('camera')}: {txt}")

    def _on_motion(self, idx, motion, box):
        if self._quitting: return
        if not motion: return
        if self.state == self.STATE_COOLDOWN: return
        self.grid_view.set_motion(idx, True)
        if self.settings.record_enabled:
            t = self.capture_threads.get(idx)
            if t is not None: t.start_recording(self.settings.record_duration)
        if idx != self.current_idx: self._switch_to_camera(idx)
        self._last_motion_box = box
        if self.state != self.STATE_ALARM: self._enter_alarm()
        else: self.video.set_motion_box(box)
        self.alarm_timer.start(self.settings.alarm_duration * 1000)

    def _on_recording_finished(self, cam_idx, video_path):
        if self._quitting: return
        if not self.settings.messenger_enabled: return
        if self.settings.messenger_paused:
            self._set_status(T("sending_paused")); return
        if not self.settings.messenger_token or not self.settings.messenger_chat_id: return
        self._send_video_to_bale(cam_idx, video_path)

    def _send_video_to_bale(self, cam_idx, video_path):
        rec_path = Path(video_path)
        if not rec_path.exists(): return
        if cam_idx >= len(self.settings.cameras): return
        cam_name = self.settings.cameras[cam_idx].name or T("camera")
        now = datetime.datetime.now()
        template = self.settings.messenger_caption.strip() or T("default_caption")
        try:
            caption = template.format(name=cam_name, date=now.strftime("%Y-%m-%d"),
                                      time=now.strftime("%H:%M:%S"))
        except Exception:
            caption = T("default_caption").format(name=cam_name,
                date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M:%S"))
        token = self.settings.messenger_token; chat_id = self.settings.messenger_chat_id
        video_path = str(rec_path)
        def _worker():
            self._set_status(T("sending_processing"))
            ok = send_video_to_bale(token, chat_id, video_path, caption)
            self._set_status(T("sending_ok") if ok else T("sending_fail"))
        threading.Thread(target=_worker, daemon=True).start()

    def _switch_to_camera(self, idx):
        if idx < 0 or idx >= len(self.settings.cameras): return
        self.current_idx = idx
        self.cam_list.blockSignals(True); self.cam_list.setCurrentRow(idx); self.cam_list.blockSignals(False)
        c = self.settings.cameras[idx]
        self.title_lbl.setText(f"<b>{c.name or T('camera')}</b> — {c.ip}:{c.port}")
        self.video.set_roi(c.roi); self.video.set_title(c.name or T("camera"))
        t = self.capture_threads.get(idx)
        if t is not None:
            f = t.get_frame()
            if f is not None: self.video.set_frame(f)

    def _enter_alarm(self):
        self._pre_alarm_view = self.view_stack.currentIndex()
        self.state = self.STATE_ALARM
        if not self.isVisible(): self._restore_from_tray()
        if self.isMinimized():
            self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.view_stack.setCurrentIndex(0)
        self.roi_btn.setEnabled(True); self.clear_roi_btn.setEnabled(True); self.test_btn.setEnabled(True)
        self.back_to_grid_btn.setVisible(True)
        self.video.set_alarm(True); self.video.set_motion_box(self._last_motion_box)
        if self.current_idx < len(self.settings.cameras):
            c = self.settings.cameras[self.current_idx]
            self.video.set_title(c.name or T("camera"))
        if self.settings.always_on_top: self._apply_on_top()
        if not self.isFullScreen(): self.showFullScreen()
        self.raise_(); self.activateWindow()
        if self.settings.sound: play_alarm_sound(self.settings.volume)
        self._set_status(T("motion_detected"))

    def _exit_alarm(self):
        self.state = self.STATE_COOLDOWN
        self.video.set_alarm(False); self.video.set_motion_box(None)
        self._last_motion_box = None; stop_alarm_sound()
        if self.isFullScreen(): self.showNormal()
        self._remove_on_top()
        if self._pre_alarm_view == 1: self._go_to_grid_view()
        else: self._go_to_single_view()
        for i in range(len(self.settings.cameras)): self.grid_view.set_motion(i, False)
        self._set_status(T("cooldown"))
        self.cooldown_timer.start(self.settings.cooldown * 1000)

    def _end_cooldown(self):
        self.state = self.STATE_NORMAL; self._set_status(T("monitoring"))

    def _on_roi_changed(self, roi):
        if not self.settings.cameras: return
        c = self.settings.cameras[self.current_idx]
        c.roi = list(roi) if roi else None
        save_settings(self.settings)
        t = self.capture_threads.get(self.current_idx)
        if t is not None:
            t.roi = list(roi) if roi else None; t.reset_bg()
        if roi: self._set_status(T("roi_status", x=roi[0], y=roi[1], w=roi[2], h=roi[3]))
        else: self._set_status(T("roi_cleared"))

    def _clear_roi(self):
        self.video.set_roi(None); self.roi_btn.setChecked(False)

    def _on_quick_sens(self, v):
        if not self.settings.cameras: return
        idx = self.current_idx
        if idx < 0 or idx >= len(self.settings.cameras): return
        v = int(v); self.sens_quick_val.setText(str(v))
        c = self.settings.cameras[idx]; c.sensitivity = v
        t = self.capture_threads.get(idx)
        if t is not None: t.sensitivity = v
        save_settings(self.settings)

    def _on_quick_area(self, v):
        if not self.settings.cameras: return
        idx = self.current_idx
        if idx < 0 or idx >= len(self.settings.cameras): return
        v = int(v); c = self.settings.cameras[idx]; c.min_area = v
        t = self.capture_threads.get(idx)
        if t is not None: t.min_area = v
        save_settings(self.settings)

    def _on_volume_changed(self, v):
        v = int(v); self.settings.volume = v
        self.vol_lbl.setText(self._vol_label_short(v)); save_settings(self.settings)
        ensure_alarm_wav(v)

    def _test_alarm(self):
        roi = self.video.get_roi()
        if roi is None:
            QMessageBox.information(self, T("needs_roi"), T("needs_roi_msg")); return
        x,y,w,h = roi
        self._last_motion_box = (x+w//4, y+h//4, w//2, h//2)
        self._enter_alarm()
        self.alarm_timer.start(self.settings.alarm_duration * 1000)

    def _toggle_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.isFullScreen() and self.state != self.STATE_ALARM:
            self.showNormal()
        elif e.key() == Qt.Key_H and (e.modifiers() & Qt.ControlModifier):
            self._hide_to_tray()
        elif e.key() == Qt.Key_D and (e.modifiers() & Qt.ControlModifier):
            self._debug_current_values()
        else:
            super().keyPressEvent(e)

    def _debug_current_values(self):
        if not self.settings.cameras: print("⚠ no cameras"); return
        idx = self.current_idx; c = self.settings.cameras[idx]
        t = self.capture_threads.get(idx)
        print("="*55)
        print(f"DEBUG cam[{idx}] = {c.name!r}")
        print(f"  settings: sens={c.sensitivity} th={sensitivity_to_threshold(c.sensitivity)} "
              f"min_area={c.min_area} every={c.detect_every}")
        if t is not None:
            print(f"  live:     sens={t.sensitivity} th={sensitivity_to_threshold(t.sensitivity)} "
                  f"min_area={t.min_area} every={t.detect_every}")
        else: print("  live:     (not connected)")
        print(f"  volume:   {self.settings.volume}% (scale={volume_to_scale(self.settings.volume):.6f})")
        print("="*55)

    def showEvent(self, e):
        super().showEvent(e)
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(400, _close_pyinstaller_splash)

    def _open_settings(self):
        dlg = GlobalSettingsDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            old_autostart = self.settings.autostart
            dlg.apply_to(self.settings)
            save_settings(self.settings); set_recordings_dir(self.settings.recordings_dir)
            self.vol_slider.blockSignals(True); self.vol_slider.setValue(int(self.settings.volume))
            self.vol_lbl.setText(self._vol_label_short(self.settings.volume))
            self.vol_slider.blockSignals(False)
            ensure_alarm_wav(self.settings.volume)
            if IS_WIN and self.settings.autostart != old_autostart:
                ok = set_autostart(self.settings.autostart)
                if self.settings.autostart and not ok:
                    QMessageBox.warning(self, T("error"), T("autostart_failed_msg"))
                elif ok:
                    self._set_status(T("autostart_enabled_msg") if self.settings.autostart
                                     else T("autostart_disabled_msg"))
            self._apply_current_camera_params()
            self._set_status(T("settings_saved"))

    def closeEvent(self, e):
        print("🛑 closeEvent...")
        self._quitting = True
        # تایمرها را متوقف کن
        try: self.alarm_timer.stop()
        except Exception: pass
        try: self.cooldown_timer.stop()
        except Exception: pass
        # صدا را قطع کن
        try: stop_alarm_sound()
        except Exception: pass
        # thread ها را قطع کن (با انتظار کنترل‌شده)
        try: self._disconnect_all()
        except Exception as ex: print("disconnect_all error:", ex)
        # tray را پنهان کن
        try: self.tray.hide()
        except Exception: pass
        try: e.accept()
        except Exception: pass
        super().closeEvent(e)
        # درخواست quit
        try:
            app = QApplication.instance()
            if app is not None: app.quit()
        except Exception as ex: print("app.quit failed:", ex)
        # fallback قطعی — حداکثر ۲ ثانیه دیگر پروسه را می‌کشیم
        def _hard_exit():
            time.sleep(2.0)
            print("⚠ force exit fallback")
            try: os._exit(0)
            except Exception: pass
        threading.Thread(target=_hard_exit, daemon=True).start()


def _close_pyinstaller_splash():
    try:
        import pyi_splash  # type: ignore
        pyi_splash.close()
    except Exception: pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("K1Motion")
    app.setQuitOnLastWindowClosed(False)
    w = MainWindow()
    w.show()
    QTimer.singleShot(1500, _close_pyinstaller_splash)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()