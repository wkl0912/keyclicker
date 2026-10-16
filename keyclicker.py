# -*- coding: utf-8 -*-
"""
键鼠映射器 KeyClicker  v1.5
================================================================
作用：把键盘按键映射成「在屏幕指定坐标执行一次鼠标单击」，
      并提供一个可拖动的屏幕悬浮按钮面板（点按钮 = 在该坐标单击一次）。

v1.4 新增：界面尺寸可调 ——
      「悬浮按钮」大小 0.5~2.0 倍（面板按钮压在 HUD 密集区嫌大时缩小它），
      「拖动标记」边长 12~96 px（拖动定位的准星，同时是拖拽热区）。
      主界面底部有两组 − / ＋ 按钮，改动即时生效并写入 config.json。

v1.5 修正：拖动标记的「触发面积」以前不等于你设的尺寸 ——
      窗口 = 准星画布 + 下方的键名标签 + 2px 外框，所以设 16px 时
      真实的可拖拽区域其实有 50 多像素。现在标记就是「一个 mark_size×mark_size
      的方块」，外框画在画布内部、键名画在画布里面，看到多大 = 触发就多大。
      尺寸不足放键名时会自动省略键名，鼠标悬停可在顶部提示条看到是哪一条。
      同时：钩子对同一次物理按压做去重（避免 KEYDOWN/SYSKEYDOWN 双投递导致重复触发）。

快速上手（三步）：
  1) 点【＋ 添加映射】新增一行
  2) 点行里的【按键】按钮 -> 在键盘上按下你想用的键（例如 1）
  3) 点行里的【坐标】按钮 -> 主窗口消失 3 秒，把鼠标移到目标点停住，自动记录
  完成后：无论当前在哪个程序，按 1 就会在目标位置单击一次。

全局热键：F9 = 启用/停用全部映射（紧急停用）。

依赖：仅 Python 标准库 + Windows API（ctypes），无需 pip 安装任何东西。
说明：本程序执行的是「全局键鼠注入」，属于自动化宏行为。联机竞技游戏的
      反作弊（BattlEye / EAC / VAC）可能判定为违规，请自行评估风险。
"""

import ctypes
import json
import os
import queue
import sys
import threading
import time
import traceback
from ctypes import wintypes

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# =====================================================================
# 0. 常量与路径
# =====================================================================
APP_NAME = '键鼠映射器'
APP_VERSION = '1.5'

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, 'config.json')
LOG_PATH = os.path.join(APP_DIR, 'error.log')

# 配色（浅色主题）
C_BG = '#f4f5f7'
C_CARD = '#ffffff'
C_TEXT = '#1f2328'
C_MUTED = '#6b7280'
C_LINE = '#d9dde3'
C_ACCENT = '#2f6feb'
C_OK = '#1a9c52'
C_DANGER = '#d94b41'
C_WARN = '#b45309'
C_SOFT = '#eef2ff'

# DPI 缩放系数（在 App 初始化时按实际 DPI 计算）
SCALE = 1.0


def px(v):
    """把逻辑像素换算成当前 DPI 下的物理像素。"""
    return int(round(v * SCALE))


user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# ---------------------------------------------------------------- 消息/钩子
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
HC_ACTION = 0

# ---------------------------------------------------------------- 鼠标注入
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# ---------------------------------------------------------------- 窗口扩展样式
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000

GA_ROOT = 2

BUTTON_FLAGS = {
    'left': (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    'right': (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    'middle': (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}
BUTTON_CN = {'left': '左键', 'right': '右键', 'middle': '中键'}


# =====================================================================
# 1. Win32 结构体与函数签名
# =====================================================================
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode', wintypes.DWORD),
        ('scanCode', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('union', _INPUTUNION)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p,
                               wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
user32.FindWindowW.restype = ctypes.c_void_p
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]

kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
MUTEX_NAME = 'KeyClicker_SingleInstance_v1'
_mutex_handle = None

if hasattr(user32, 'GetWindowLongPtrW'):
    _get_style = user32.GetWindowLongPtrW
    _set_style = user32.SetWindowLongPtrW
else:  # 32 位 Python 兜底
    _get_style = user32.GetWindowLongW
    _set_style = user32.SetWindowLongW
_get_style.restype = ctypes.c_ssize_t
_get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
_set_style.restype = ctypes.c_ssize_t
_set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]


def acquire_single_instance():
    """返回 True 表示本进程是第一个实例。互斥体随进程退出自动释放。"""
    global _mutex_handle
    ctypes.set_last_error(0)
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def focus_existing():
    """已有实例在运行时，把它的主窗口拉到前台。"""
    try:
        hwnd = user32.FindWindowW(None, '%s v%s' % (APP_NAME, APP_VERSION))
        if not hwnd:
            return
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        log_error('聚焦已有实例失败')


def setup_dpi_awareness():
    """必须在创建任何窗口之前调用，否则界面会被系统位图拉伸变糊。"""
    try:
        hr = ctypes.windll.shcore.SetProcessDpiAwareness(1)  # SYSTEM_DPI_AWARE
        if hr == 0:
            return 'system'
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return 'system(legacy)'
    except Exception:
        pass
    return 'none'


def hwnd_of(widget):
    try:
        h = widget.winfo_id()
        root = user32.GetAncestor(h, GA_ROOT)
        return root or h
    except Exception:
        return None


def set_ex_style(widget, extra):
    h = hwnd_of(widget)
    if not h:
        return
    try:
        cur = _get_style(h, GWL_EXSTYLE)
        _set_style(h, GWL_EXSTYLE, cur | extra)
    except Exception:
        log_error('设置窗口扩展样式失败')


# =====================================================================
# 2. 按键名称表（VK -> 可读名）
# =====================================================================
VK_NAMES = {
    0x08: 'Backspace', 0x09: 'Tab', 0x0D: 'Enter', 0x13: 'Pause', 0x14: 'CapsLock',
    0x1B: 'Esc', 0x20: 'Space', 0x21: 'PgUp', 0x22: 'PgDn', 0x23: 'End', 0x24: 'Home',
    0x25: '←', 0x26: '↑', 0x27: '→', 0x28: '↓',
    0x2C: 'PrtSc', 0x2D: 'Insert', 0x2E: 'Delete',
    0x5B: 'Win', 0x5C: 'WinR', 0x5D: 'Menu',
    0x60: 'Num0', 0x61: 'Num1', 0x62: 'Num2', 0x63: 'Num3', 0x64: 'Num4',
    0x65: 'Num5', 0x66: 'Num6', 0x67: 'Num7', 0x68: 'Num8', 0x69: 'Num9',
    0x6A: 'Num*', 0x6B: 'Num+', 0x6D: 'Num-', 0x6E: 'Num.', 0x6F: 'Num/',
    0x90: 'NumLock', 0x91: 'ScrollLock',
    0xA0: 'LShift', 0xA1: 'RShift', 0xA2: 'LCtrl', 0xA3: 'RCtrl', 0xA4: 'LAlt', 0xA5: 'RAlt',
    0xBA: ';', 0xBB: '=', 0xBC: ',', 0xBD: '-', 0xBE: '.', 0xBF: '/', 0xC0: '`',
    0xDB: '[', 0xDC: '\\', 0xDD: ']', 0xDE: "'",
}
for _i in range(10):
    VK_NAMES[0x30 + _i] = str(_i)
for _i in range(26):
    VK_NAMES[0x41 + _i] = chr(0x41 + _i)
for _i in range(1, 25):
    VK_NAMES[0x6F + _i] = 'F%d' % _i


def vk_name(vk):
    return VK_NAMES.get(vk, 'VK_%02X' % vk)


# =====================================================================
# 3. 配置
# =====================================================================
# 尺寸调节范围：悬浮按钮整体倍率 / 拖动标记边长（逻辑像素）
BUTTON_SCALE_MIN, BUTTON_SCALE_MAX, BUTTON_SCALE_STEP = 0.5, 2.0, 0.1
MARK_SIZE_MIN, MARK_SIZE_MAX, MARK_SIZE_STEP = 12, 96, 4
BUTTON_SCALE_DEFAULT = 1.0
MARK_SIZE_DEFAULT = 38

DEFAULT_CONFIG = {
    'enabled': True,
    'toggle_vk': 0x78,          # F9
    'debounce_ms': 180,         # 同一按键最小触发间隔（防按住连发）
    'move_delay_ms': 20,        # 移动光标后、点击前的等待
    'restore_cursor': True,     # 点击后把光标还原到原位置
    'show_marker': True,        # 点击处闪一个圆环
    'use_absolute': False,      # 用绝对坐标注入（个别游戏需要）
    'swallow': False,           # 拦截已映射按键，不再传给系统
    'button_scale': BUTTON_SCALE_DEFAULT,   # 悬浮按钮大小倍率（0.5~2.0）
    'mark_size': MARK_SIZE_DEFAULT,         # 拖动定位准星边长（16~96 px）
    'first_run': True,
    'panel': {'visible': False, 'x': 120, 'y': 160},
    'mappings': [],
}


def deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return deep_merge(DEFAULT_CONFIG, json.load(f))
    except Exception:
        log_error('读取配置失败，改用默认配置')
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    try:
        tmp = CONFIG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        log_error('保存配置失败')


def log_error(msg):
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
            if sys.exc_info()[0]:
                f.write(traceback.format_exc())
            f.write('\n')
    except Exception:
        pass


# =====================================================================
# 4. 输入注入 / 读取
# =====================================================================
_mouse_lock = threading.Lock()


def _send_mouse(flags, dx=0, dy=0, data=0):
    mi = MOUSEINPUT(dx, dy, data, flags, 0, 0)
    inp = INPUT(INPUT_MOUSE, _INPUTUNION(mi=mi))
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _to_absolute(x, y):
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
    vh = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
    nx = int(round((x - vx) * 65535.0 / vw))
    ny = int(round((y - vy) * 65535.0 / vh))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def get_cursor_pos():
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def virtual_desktop():
    """返回虚拟桌面矩形 (x, y, w, h)，兼容多显示器（含负坐标）。"""
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
            max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))


def clamp_to_virtual(x, y):
    vx, vy, vw, vh = virtual_desktop()
    return (max(vx, min(vx + vw - 1, int(x))), max(vy, min(vy + vh - 1, int(y))))


def do_click(x, y, button='left', double=False, opts=None):
    """在屏幕坐标 (x, y) 执行一次（或双击）鼠标单击。"""
    opts = opts or {}
    down, up = BUTTON_FLAGS.get(button, BUTTON_FLAGS['left'])
    move_delay = max(0, int(opts.get('move_delay_ms', 20))) / 1000.0
    use_abs = bool(opts.get('use_absolute', False))
    restore = bool(opts.get('restore_cursor', False))

    with _mouse_lock:
        prev = get_cursor_pos() if restore else None

        if use_abs:
            nx, ny = _to_absolute(int(x), int(y))
            _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)
        else:
            user32.SetCursorPos(int(x), int(y))

        if move_delay:
            time.sleep(move_delay)

        clicks = 2 if double else 1
        for i in range(clicks):
            _send_mouse(down)
            _send_mouse(up)
            if i + 1 < clicks:
                time.sleep(0.06)

        if restore and prev is not None:
            time.sleep(0.02)
            user32.SetCursorPos(prev[0], prev[1])


# =====================================================================
# 5. 全局键盘钩子（独立线程 + 消息循环）
# =====================================================================
class KeyboardHook:
    """底层键盘钩子：回调里只做「入队/极快判断」，重活交给工作线程。"""

    def __init__(self, on_key):
        """on_key(vk) -> bool，返回 True 表示吞掉这次按键"""
        self._on_key = on_key
        self._thread = None
        self._thread_id = 0
        self._hook = None
        self._proc = HOOKPROC(self._hook_proc)   # 必须持引用，否则 GC 后崩溃
        self._running = False
        # 上一次处理的按键签名，用于丢弃同一次物理按压的重复投递
        self._last_sig = None

    def _hook_proc(self, n_code, w_param, l_param):
        if n_code == HC_ACTION:
            try:
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = int(kb.vkCode)
                    # Windows 对同一次物理按压可能同时投递 KEYDOWN 与 SYSKEYDOWN，
                    # 两者的 (vk, 时间戳, 标志位) 完全相同 —— 只认一次，
                    # 否则一次按压会触发两次映射。自动重复的时间戳不同，不受影响。
                    sig = (vk, int(kb.time), int(kb.flags))
                    if sig == self._last_sig:
                        return user32.CallNextHookEx(None, n_code, w_param, l_param)
                    self._last_sig = sig
                    if self._on_key(vk):
                        return 1
            except Exception:
                log_error('钩子回调异常')
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            log_error('安装键盘钩子失败，错误码 %d' % ctypes.get_last_error())
            return
        self._running = True
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._running = False

    def start(self):
        self._thread = threading.Thread(target=self._run, name='kbd-hook', daemon=True)
        self._thread.start()
        for _ in range(60):
            if self._running:
                return True
            time.sleep(0.02)
        return False

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


# =====================================================================
# 6. 主界面
# =====================================================================
class App:
    def __init__(self):
        self.cfg = load_config()
        self._clamp_sizes()
        self.ui_queue = queue.Queue()
        self.capture_vk_target = None
        self.last_fire = {}
        self.panel_win = None
        self.panel_body = None
        self.marker_win = None
        self.marker_canvas = None
        self.row_widgets = []
        self.pick_row = None
        self.hint_win = None
        self.drag_mode = False          # 坐标拖动调整模式
        self.drag_marks = []            # [{'win':..,'cv':..,'lbl':..,'cw':..}]
        self.drag_hint = None
        self.drag_hint_lbl = None
        self.drag_focus = None          # 正在拖动第几条（用于高亮提示）

        self.root = tk.Tk()
        self.root.title('%s v%s' % (APP_NAME, APP_VERSION))
        self.root.configure(bg=C_BG)
        self.root.resizable(False, False)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._setup_scale()
        self._apply_fonts()
        self._build_ui()
        self._center_window()
        self.refresh_rows()
        self.refresh_status()
        self.refresh_panel()

        self.hook = KeyboardHook(self.on_key_event)
        if not self.hook.start():
            messagebox.showwarning(APP_NAME, '全局键盘钩子安装失败。\n'
                                             '按键触发不可用，悬浮按钮仍可使用。')

        self.root.after(40, self._pump)
        if self.cfg['first_run']:
            self.root.after(400, self.show_first_run)

    # ---------------------------------------------------------------- DPI / 字体
    def _setup_scale(self):
        global SCALE
        try:
            dpi = float(self.root.winfo_fpixels('1i'))
        except Exception:
            dpi = 96.0
        SCALE = max(1.0, min(3.0, dpi / 96.0))

    def _sf(self, base, s):
        """按倍率 s 生成同族字体（字号随之缩放，最小 6pt）。"""
        try:
            fam, sz = base[0], base[1]
            style = base[2] if len(base) > 2 else ''
            nsz = max(6, int(round(sz * s)))
            return (fam, nsz, style) if style else (fam, nsz)
        except Exception:
            return base

    def _clamp_sizes(self):
        """把配置里的尺寸值收进合法区间（配置可能被手改过）。"""
        self.cfg['button_scale'] = self._clamp_button_scale(
            self.cfg.get('button_scale', BUTTON_SCALE_DEFAULT))
        self.cfg['mark_size'] = self._clamp_mark_size(
            self.cfg.get('mark_size', MARK_SIZE_DEFAULT))

    @staticmethod
    def _clamp_button_scale(v):
        try:
            return max(BUTTON_SCALE_MIN, min(BUTTON_SCALE_MAX, round(float(v), 2)))
        except Exception:
            return BUTTON_SCALE_DEFAULT

    @staticmethod
    def _clamp_mark_size(v):
        try:
            return int(max(MARK_SIZE_MIN, min(MARK_SIZE_MAX, int(round(float(v))))))
        except Exception:
            return MARK_SIZE_DEFAULT

    def _apply_fonts(self):
        fam = 'Microsoft YaHei UI'
        self.F = (fam, 9)
        self.F_B = (fam, 9, 'bold')
        self.F_S = (fam, 8)
        self.F_BIG = (fam, 11, 'bold')
        try:
            self.root.option_add('*Font', self.F)
        except Exception:
            pass
        try:
            st = ttk.Style()
            st.configure('TCombobox', font=self.F)
            st.configure('Vertical.TScrollbar', arrowsize=px(12))
        except Exception:
            pass

    def _center_window(self):
        self.root.update_idletasks()
        w, h = px(560), px(620)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.root.geometry('%dx%d+%d+%d' % (w, h, x, y))

    # ---------------------------------------------------------------- 布局
    def _build_ui(self):
        # ---- 顶部标题栏
        head = tk.Frame(self.root, bg=C_CARD, height=px(52))
        head.pack(fill='x')
        head.pack_propagate(False)
        tk.Label(head, text='键鼠映射器', bg=C_CARD, fg=C_TEXT,
                 font=self.F_BIG).pack(side='left', padx=(px(14), px(8)))
        self.lbl_status = tk.Label(head, text='', bg=C_CARD, fg=C_MUTED, font=self.F_B)
        self.lbl_status.pack(side='left')
        tk.Button(head, text='启用/禁用 (F9)', command=self.toggle_enabled,
                  bg=C_CARD, fg=C_ACCENT, activebackground=C_SOFT, relief='flat',
                  font=self.F, cursor='hand2', bd=0).pack(side='right', padx=px(12))

        tk.Frame(self.root, bg=C_LINE, height=1).pack(fill='x')

        # ---- 列表表头
        hdr = tk.Frame(self.root, bg=C_BG)
        hdr.pack(fill='x', padx=px(12), pady=(px(8), px(2)))
        for txt, w in (('按键', 8), ('目标坐标', 12), ('方式', 6), ('备注', 10)):
            tk.Label(hdr, text=txt, bg=C_BG, fg=C_MUTED, font=self.F_S,
                     width=w, anchor='w').pack(side='left')
        tk.Label(hdr, text='操作', bg=C_BG, fg=C_MUTED, font=self.F_S,
                 anchor='w').pack(side='left', padx=(px(6), 0))

        # ---- 可滚动列表区
        wrap = tk.Frame(self.root, bg=C_BG)
        wrap.pack(fill='both', expand=True, padx=px(12))
        self.canvas = tk.Canvas(wrap, bg=C_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.list_frame = tk.Frame(self.canvas, bg=C_BG)
        self.list_win = self.canvas.create_window((0, 0), window=self.list_frame, anchor='nw')
        self.list_frame.bind('<Configure>',
                             lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',
                         lambda e: self.canvas.itemconfigure(self.list_win, width=e.width))
        self.root.bind_all('<MouseWheel>', self._on_wheel)

        # ---- 底部工具区
        bottom = tk.Frame(self.root, bg=C_BG)
        bottom.pack(fill='x', padx=px(12), pady=(px(6), px(4)))

        row1 = tk.Frame(bottom, bg=C_BG)
        row1.pack(fill='x', pady=(0, px(6)))
        self._btn(row1, '＋ 添加映射', self.add_row, C_ACCENT, fg='white').pack(side='left')
        self._btn(row1, '拖动调整坐标', self.toggle_drag_mode).pack(side='left', padx=px(6))
        self._btn(row1, '显示/隐藏悬浮按钮', self.toggle_panel).pack(side='left', padx=(0, px(6)))
        self._btn(row1, '清空', self.clear_rows).pack(side='left')

        row2 = tk.Frame(bottom, bg=C_BG)
        row2.pack(fill='x', pady=(0, px(6)))
        tk.Label(row2, text='防连发(ms)', bg=C_BG, fg=C_TEXT, font=self.F_S).pack(side='left')
        self.var_debounce = tk.StringVar(value=str(self.cfg['debounce_ms']))
        ent = tk.Entry(row2, textvariable=self.var_debounce, width=5, font=self.F_S,
                       justify='center', relief='solid', bd=1)
        ent.pack(side='left', padx=(px(4), px(14)))
        ent.bind('<FocusOut>', lambda ev: self.apply_opts())
        ent.bind('<Return>', lambda ev: self.apply_opts())

        self.var_marker = tk.BooleanVar(value=self.cfg['show_marker'])
        self.var_restore = tk.BooleanVar(value=self.cfg['restore_cursor'])
        self.var_swallow = tk.BooleanVar(value=self.cfg['swallow'])
        for txt, var in (('点击标记', self.var_marker),
                         ('还原光标', self.var_restore),
                         ('吞掉按键', self.var_swallow)):
            tk.Checkbutton(row2, text=txt, variable=var, command=self.apply_opts,
                           bg=C_BG, fg=C_TEXT, font=self.F_S, activebackground=C_BG,
                           selectcolor=C_CARD, bd=0, highlightthickness=0
                           ).pack(side='left', padx=(0, px(10)))

        row3 = tk.Frame(bottom, bg=C_BG)
        row3.pack(fill='x', pady=(0, px(4)))
        tk.Label(row3, text='启用键', bg=C_BG, fg=C_TEXT, font=self.F_S).pack(side='left')
        self.btn_toggle_key = tk.Button(row3, text=vk_name(self.cfg['toggle_vk']),
                                        command=lambda: self.begin_capture('toggle'),
                                        bg=C_CARD, fg=C_TEXT, font=self.F_S, width=8,
                                        relief='solid', bd=1, cursor='hand2')
        self.btn_toggle_key.pack(side='left', padx=(px(4), px(14)))
        self._btn(row3, '导出配置', self.export_cfg).pack(side='left')
        self._btn(row3, '导入配置', self.import_cfg).pack(side='left', padx=px(6))
        self._btn(row3, '打开目录', self.open_dir).pack(side='left')

        # ---- 尺寸调节（v1.4 新增）
        row4 = tk.Frame(bottom, bg=C_BG)
        row4.pack(fill='x', pady=(0, px(4)))
        tk.Label(row4, text='悬浮按钮', bg=C_BG, fg=C_TEXT, font=self.F_S).pack(side='left')
        self._btn(row4, '−', lambda: self.nudge_button_scale(-BUTTON_SCALE_STEP),
                  padx=px(5), takefocus=0).pack(side='left', padx=(px(4), 0))
        self.lbl_button_scale = tk.Label(row4, text='100%', bg=C_BG, fg=C_ACCENT,
                                         font=self.F_B, width=5)
        self.lbl_button_scale.pack(side='left')
        self._btn(row4, '＋', lambda: self.nudge_button_scale(+BUTTON_SCALE_STEP),
                  padx=px(5), takefocus=0).pack(side='left')
        tk.Label(row4, text='拖动标记', bg=C_BG, fg=C_TEXT, font=self.F_S
                 ).pack(side='left', padx=(px(16), 0))
        self._btn(row4, '−', lambda: self.nudge_mark_size(-MARK_SIZE_STEP),
                  padx=px(5), takefocus=0).pack(side='left', padx=(px(4), 0))
        self.lbl_mark_size = tk.Label(row4, text=str(MARK_SIZE_DEFAULT), bg=C_BG,
                                      fg=C_ACCENT, font=self.F_B, width=3)
        self.lbl_mark_size.pack(side='left')
        self._btn(row4, '＋', lambda: self.nudge_mark_size(+MARK_SIZE_STEP),
                  padx=px(5), takefocus=0).pack(side='left')
        self._btn(row4, '重置', self.reset_sizes).pack(side='left', padx=(px(10), 0))
        self._refresh_size_labels()

        tk.Label(self.root, bg=C_BG, fg=C_WARN, font=self.F_S, justify='left',
                 wraplength=px(530), anchor='w',
                 text='⚠ 本工具执行全局键鼠注入。联机竞技游戏（BattlEye / EAC / VAC）'
                      '可能判定为违规，请自行评估风险。'
                 ).pack(fill='x', padx=px(12), pady=(0, px(8)))

    def _btn(self, parent, text, cmd, bg=C_CARD, fg=C_TEXT, **kw):
        kw.setdefault('font', self.F)
        kw.setdefault('padx', px(8))
        kw.setdefault('pady', px(2))
        kw.setdefault('takefocus', 0)
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         relief='solid', bd=1, cursor='hand2',
                         activebackground=C_SOFT, **kw)

    def _on_wheel(self, event):
        try:
            mx, my = self.root.winfo_pointerxy()
            cx, cy = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
            if cx <= mx < cx + self.canvas.winfo_width() and \
               cy <= my < cy + self.canvas.winfo_height():
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        except Exception:
            pass

    # ---------------------------------------------------------------- 状态
    def refresh_status(self):
        if self.drag_mode:
            self.lbl_status.config(text='✛ 拖动调整中（映射已暂停，Esc 结束）', fg=C_ACCENT)
        elif self.cfg['enabled']:
            self.lbl_status.config(
                text='● 已启用  (按 %s 停用)' % vk_name(self.cfg['toggle_vk']), fg=C_OK)
        else:
            self.lbl_status.config(
                text='○ 已停用  (按 %s 启用)' % vk_name(self.cfg['toggle_vk']), fg=C_DANGER)

    def apply_opts(self):
        try:
            self.cfg['debounce_ms'] = max(20, min(3000, int(self.var_debounce.get())))
        except Exception:
            pass
        self.cfg['show_marker'] = bool(self.var_marker.get())
        self.cfg['restore_cursor'] = bool(self.var_restore.get())
        self.cfg['swallow'] = bool(self.var_swallow.get())
        self.var_debounce.set(str(self.cfg['debounce_ms']))
        save_config(self.cfg)

    # ---------------------------------------------------------------- 尺寸调节
    def _refresh_size_labels(self):
        try:
            self.lbl_button_scale.config(
                text='%d%%' % int(round(self.cfg['button_scale'] * 100)))
            self.lbl_mark_size.config(text=str(int(self.cfg['mark_size'])))
        except Exception:
            pass

    def nudge_button_scale(self, delta):
        """调节悬浮按钮大小倍率，到边界后不动。"""
        v = self._clamp_button_scale(self.cfg['button_scale'] + delta)
        if abs(v - self.cfg['button_scale']) < 1e-9:
            return
        self.cfg['button_scale'] = v
        self.apply_sizes()

    def nudge_mark_size(self, delta):
        """调节拖动标记边长（像素），到边界后不动。"""
        v = self._clamp_mark_size(self.cfg['mark_size'] + delta)
        if v == self.cfg['mark_size']:
            return
        self.cfg['mark_size'] = v
        self.apply_sizes()

    def reset_sizes(self):
        if (self.cfg['button_scale'] == BUTTON_SCALE_DEFAULT
                and self.cfg['mark_size'] == MARK_SIZE_DEFAULT):
            return
        self.cfg['button_scale'] = BUTTON_SCALE_DEFAULT
        self.cfg['mark_size'] = MARK_SIZE_DEFAULT
        self.apply_sizes()

    def apply_sizes(self):
        """尺寸变更后：存盘 + 刷新数值 + 重建悬浮面板（立即生效）。"""
        self._clamp_sizes()
        self._refresh_size_labels()
        save_config(self.cfg)
        self.rebuild_panel()

    def rebuild_panel(self):
        """面板是按旧尺寸布局出来的，尺寸变了直接重建最省心。"""
        if self.panel_win is not None:
            try:
                self.panel_win.destroy()
            except Exception:
                pass
            self.panel_win = None
            self.panel_body = None
        self.refresh_panel()

    def toggle_enabled(self):
        self.cfg['enabled'] = not self.cfg['enabled']
        self.refresh_status()
        self.refresh_panel()
        save_config(self.cfg)

    # ---------------------------------------------------------------- 列表
    def refresh_rows(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.row_widgets = []

        if not self.cfg['mappings']:
            tk.Label(self.list_frame,
                     text='还没有任何映射。\n点下方【＋ 添加映射】开始 —— 先设按键，'
                          '再点坐标按钮拖动到目标位置。',
                     bg=C_BG, fg=C_MUTED, font=self.F, justify='center'
                     ).pack(pady=px(40))
            return

        for idx, mp in enumerate(self.cfg['mappings']):
            self.row_widgets.append(self._make_row(idx, mp))

    def _make_row(self, idx, mp):
        rf = tk.Frame(self.list_frame, bg=C_CARD,
                      highlightbackground=C_LINE, highlightthickness=1)
        rf.pack(fill='x', pady=px(2))

        b_key = tk.Button(rf, text=vk_name(mp['vk']), width=8, font=self.F_B,
                          bg=C_SOFT, fg=C_ACCENT, relief='flat', cursor='hand2',
                          command=lambda i=idx: self.begin_capture(i))
        b_key.pack(side='left', padx=(px(6), px(6)), pady=px(5))

        b_xy = tk.Button(rf, text='%d, %d' % (mp['x'], mp['y']), width=12, font=self.F_S,
                         bg=C_CARD, fg=C_ACCENT, relief='flat', cursor='fleur',
                         command=lambda i=idx: self.enter_drag_mode(i))
        b_xy.pack(side='left', padx=(0, px(6)))
        b_xy.bind('<Button-3>', lambda ev, i=idx: self.pick_coord(i))

        b_btn = tk.Button(rf, text=self._btn_label(mp), width=6, font=self.F_S,
                          bg=C_CARD, fg=C_TEXT, relief='flat', cursor='hand2',
                          command=lambda i=idx: self.cycle_button(i))
        b_btn.pack(side='left', padx=(0, px(6)))

        var_lbl = tk.StringVar(value=mp.get('label', ''))
        e_lbl = tk.Entry(rf, textvariable=var_lbl, width=9, font=self.F_S,
                         relief='solid', bd=1)
        e_lbl.pack(side='left', padx=(0, px(6)))
        e_lbl.bind('<FocusOut>', lambda ev, i=idx, v=var_lbl: self.set_label(i, v.get()))
        e_lbl.bind('<Return>', lambda ev, i=idx, v=var_lbl: self.set_label(i, v.get()))

        tk.Button(rf, text='▶', width=2, font=self.F_S, bg=C_CARD, fg=C_OK,
                  relief='flat', cursor='hand2',
                  command=lambda i=idx: self.test_row(i)).pack(side='left')
        tk.Button(rf, text='✕', width=2, font=self.F_S, bg=C_CARD, fg=C_DANGER,
                  relief='flat', cursor='hand2',
                  command=lambda i=idx: self.del_row(i)).pack(side='left', padx=(px(2), px(6)))

        return {'frame': rf, 'key': b_key, 'xy': b_xy, 'btn': b_btn, 'label': e_lbl}

    def _btn_label(self, mp):
        s = BUTTON_CN.get(mp.get('button', 'left'), '左键')
        return s + '双击' if mp.get('double') else s

    def add_row(self):
        if self.drag_mode:
            self.exit_drag_mode()
        cx, cy = clamp_to_virtual(self.root.winfo_screenwidth() // 2,
                                  self.root.winfo_screenheight() // 2)
        self.cfg['mappings'].append({'vk': 0x31, 'x': cx, 'y': cy,
                                     'button': 'left', 'double': False, 'label': ''})
        self.refresh_rows()
        self.refresh_panel()
        save_config(self.cfg)

    def del_row(self, idx):
        if self.drag_mode:
            self.exit_drag_mode()
        if 0 <= idx < len(self.cfg['mappings']):
            del self.cfg['mappings'][idx]
            self.refresh_rows()
            self.refresh_panel()
            save_config(self.cfg)

    def clear_rows(self):
        if not self.cfg['mappings']:
            return
        if messagebox.askyesno(APP_NAME, '确定清空全部 %d 条映射？' % len(self.cfg['mappings'])):
            if self.drag_mode:
                self.exit_drag_mode()
            self.cfg['mappings'] = []
            self.refresh_rows()
            self.refresh_panel()
            save_config(self.cfg)

    def cycle_button(self, idx):
        mp = self.cfg['mappings'][idx]
        cur = mp.get('button', 'left')
        if mp.get('double'):
            mp['double'] = False
            mp['button'] = 'left'
        elif cur == 'left':
            mp['button'] = 'right'
        elif cur == 'right':
            mp['button'] = 'middle'
        else:
            mp['button'] = 'left'
            mp['double'] = True
        self.row_widgets[idx]['btn'].config(text=self._btn_label(mp))
        self.refresh_panel()
        save_config(self.cfg)

    def set_label(self, idx, text):
        if 0 <= idx < len(self.cfg['mappings']):
            self.cfg['mappings'][idx]['label'] = text.strip()
            self.refresh_panel()
            save_config(self.cfg)

    # ---------------------------------------------------------------- 捕获按键
    def begin_capture(self, target):
        self.capture_vk_target = target
        try:
            self.root.focus_force()
        except Exception:
            pass
        if target == 'toggle':
            self.btn_toggle_key.config(text='按下按键…', fg=C_ACCENT)
        elif 0 <= target < len(self.row_widgets):
            self.row_widgets[target]['key'].config(text='按下按键…', fg=C_ACCENT)

    def end_capture(self, vk=None, cancel=False):
        target = self.capture_vk_target
        self.capture_vk_target = None
        if target is None:
            return
        if target == 'toggle':
            self.btn_toggle_key.config(text=vk_name(self.cfg['toggle_vk']), fg=C_TEXT)
            if vk is not None and not cancel:
                self.cfg['toggle_vk'] = vk
                self.btn_toggle_key.config(text=vk_name(vk))
                self.refresh_status()
                save_config(self.cfg)
        else:
            if 0 <= target < len(self.row_widgets):
                self.row_widgets[target]['key'].config(
                    text=vk_name(self.cfg['mappings'][target]['vk']), fg=C_ACCENT)
            if vk is not None and not cancel and 0 <= target < len(self.cfg['mappings']):
                self.cfg['mappings'][target]['vk'] = vk
                self.row_widgets[target]['key'].config(text=vk_name(vk))
                self.refresh_panel()
                save_config(self.cfg)

    # ---------------------------------------------------------------- 拖动调整坐标
    def toggle_drag_mode(self):
        if self.drag_mode:
            self.exit_drag_mode()
        else:
            self.enter_drag_mode()

    def enter_drag_mode(self, only_index=None):
        """在屏幕上生成可拖动的坐标标记。only_index 不为 None 时只显示那一条。"""
        if not self.cfg['mappings']:
            messagebox.showinfo(APP_NAME, '还没有映射，先点【＋ 添加映射】。')
            return
        if self.drag_mode:
            self.exit_drag_mode()

        idxs = ([only_index] if only_index is not None
                else list(range(len(self.cfg['mappings']))))
        idxs = [i for i in idxs if 0 <= i < len(self.cfg['mappings'])]
        if not idxs:
            return

        self.drag_mode = True
        self.drag_marks = []
        self.drag_focus = None
        self.root.withdraw()          # 让开目标区域，避免挡住要拖的位置
        for i in idxs:
            self._create_mark(i)
        self._show_drag_hint()
        self.refresh_status()

    def exit_drag_mode(self):
        self.drag_mode = False
        self.drag_focus = None
        for m in self.drag_marks:
            try:
                m['win'].destroy()
            except Exception:
                pass
        self.drag_marks = []
        if self.drag_hint is not None:
            try:
                self.drag_hint.destroy()
            except Exception:
                pass
            self.drag_hint = None
            self.drag_hint_lbl = None
        save_config(self.cfg)
        for i in range(len(self.row_widgets)):
            self._sync_row_coord(i)
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        self.refresh_status()

    def _create_mark(self, idx):
        mp = self.cfg['mappings'][idx]
        # 标记边长 = 真实触发面积。外框和键名都画在画布「内部」，
        # 所以「看到多大 = 能拖多大」，不会再被键名标签和外框额外撑大。
        cw = max(px(10), px(int(self.cfg['mark_size'])))
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes('-topmost', True)
        w.configure(bg=C_CARD)
        cv = tk.Canvas(w, width=cw, height=cw, bg='#eaf0ff', highlightthickness=0,
                       cursor='fleur')
        cv.pack()

        name = vk_name(mp['vk'])
        show_name = cw >= px(30)          # 太小就只留准星，键名走悬停提示

        def draw(bg, brd):
            cv.configure(bg=bg)
            cv.delete('all')
            cv.create_rectangle(0, 0, cw - 1, cw - 1, outline=brd, width=max(1, px(1)))
            top = cw * 0.58 if show_name else cw
            if show_name:
                pt = max(6, min(16, int(round(self.cfg['mark_size'] * 0.24))))
                cv.create_text(cw / 2.0, cw * 0.74, text=name, fill=brd,
                               font=(self.F_S[0], pt))
            cx, cy = cw / 2.0, top / 2.0
            r = max(3.0, top * 0.35)
            arm = r * 0.55
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=brd,
                           width=max(1, px(2)))
            cv.create_line(cx, cy - arm, cx, cy + arm, fill=brd, width=max(1, px(2)))
            cv.create_line(cx - arm, cy, cx + arm, cy, fill=brd, width=max(1, px(2)))

        draw('#eaf0ff', C_ACCENT)
        w.update_idletasks()

        x, y = clamp_to_virtual(mp['x'], mp['y'])
        w.geometry('+%d+%d' % (x - cw // 2, y - cw // 2))

        def on_press(e, win=w):
            win._dx = e.x_root - win.winfo_x()
            win._dy = e.y_root - win.winfo_y()
            self.drag_focus = idx
            draw('#d7f0e0', C_OK)
            self._update_drag_hint(idx, self.cfg['mappings'][idx]['x'],
                                   self.cfg['mappings'][idx]['y'])
            win.lift()

        def on_motion(e, win=w):
            nx = e.x_root - win._dx
            ny = e.y_root - win._dy
            win.geometry('+%d+%d' % (nx, ny))
            cx, cy = clamp_to_virtual(nx + cw // 2, ny + cw // 2)
            self.cfg['mappings'][idx]['x'] = cx
            self.cfg['mappings'][idx]['y'] = cy
            self._update_drag_hint(idx, cx, cy)

        def on_release(e):
            draw('#eaf0ff', C_ACCENT)
            self._sync_row_coord(idx)
            save_config(self.cfg)

        # 只绑画布：触发面积 == 标记的可见面积，一个像素都不多
        cv.bind('<Button-1>', on_press)
        cv.bind('<B1-Motion>', on_motion)
        cv.bind('<ButtonRelease-1>', on_release)
        # 标记太小时不画键名，靠悬停辨认是哪一条
        cv.bind('<Enter>', lambda e, i=idx: self._update_drag_hint(
            i, self.cfg['mappings'][i]['x'], self.cfg['mappings'][i]['y']))
        cv.bind('<Leave>', lambda e: self._reset_drag_hint())

        # 不抢焦点：拖动时不会打断你正在用的程序
        set_ex_style(w, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.drag_marks.append({'win': w, 'cv': cv, 'lbl': None, 'cw': cw, 'idx': idx})

    def _show_drag_hint(self):
        h = tk.Toplevel(self.root)
        h.overrideredirect(True)
        h.attributes('-topmost', True)
        f = tk.Frame(h, bg='#111827')
        f.pack()
        tk.Label(f, text='拖动标记到目标位置，松手即生效', bg='#111827', fg='white',
                 font=self.F_B, padx=px(12), pady=px(8)).pack(side='left')
        self.drag_hint_lbl = tk.Label(f, text='', bg='#111827', fg='#93c5fd',
                                      font=self.F_S, padx=px(8))
        self.drag_hint_lbl.pack(side='left')
        tk.Button(f, text='完成 (Esc)', command=self.exit_drag_mode, bg=C_ACCENT,
                  fg='white', relief='flat', font=self.F_S, cursor='hand2',
                  padx=px(10), pady=px(4)).pack(side='left', padx=px(8))
        h.update_idletasks()
        vx, vy, vw, _vh = virtual_desktop()
        h.geometry('+%d+%d' % (vx + (vw - h.winfo_reqwidth()) // 2, vy + px(16)))
        set_ex_style(h, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.drag_hint = h
        self._reset_drag_hint()

    def _reset_drag_hint(self):
        """顶部提示条恢复成「标记总数 + 当前尺寸」。"""
        if self.drag_hint_lbl is None:
            return
        try:
            self.drag_hint_lbl.config(
                text='%d 个标记 · 标记 %dpx'
                     % (len(self.drag_marks), int(self.cfg['mark_size'])))
        except Exception:
            pass

    def _update_drag_hint(self, idx, x, y):
        if self.drag_hint_lbl is None:
            return
        try:
            name = vk_name(self.cfg['mappings'][idx]['vk'])
            self.drag_hint_lbl.config(text='%s → %d, %d' % (name, int(x), int(y)))
        except Exception:
            pass

    def _sync_row_coord(self, idx):
        try:
            if 0 <= idx < len(self.row_widgets):
                mp = self.cfg['mappings'][idx]
                self.row_widgets[idx]['xy'].config(text='%d, %d' % (mp['x'], mp['y']))
        except Exception:
            pass

    # ---------------------------------------------------------------- 光标拾取（慢速，适合全屏游戏）
    def pick_coord(self, idx):
        self.pick_row = idx
        self.root.withdraw()
        self._show_hint('3 秒后记录光标位置 —— 请把鼠标移到目标点并停住', 3)

    def _show_hint(self, text, seconds):
        hint = tk.Toplevel(self.root)
        hint.overrideredirect(True)
        hint.attributes('-topmost', True)
        lbl = tk.Label(hint, text='', bg='#111827', fg='white', font=self.F_B,
                       padx=px(18), pady=px(12))
        lbl.pack()
        hint.update_idletasks()
        sw = hint.winfo_screenwidth()
        hint.geometry('+%d+%d' % ((sw - hint.winfo_width()) // 2, int(hint.winfo_screenheight() * 0.12)))
        self.hint_win = hint

        def tick(n):
            if n <= 0:
                try:
                    hint.destroy()
                except Exception:
                    pass
                self.hint_win = None
                self._finish_pick()
                return
            try:
                lbl.config(text='%s   (%d)' % (text, n))
            except Exception:
                return
            self.root.after(1000, lambda: tick(n - 1))

        tick(seconds)

    def _finish_pick(self):
        x, y = get_cursor_pos()
        idx = self.pick_row
        self.pick_row = None
        if idx is not None and 0 <= idx < len(self.cfg['mappings']):
            self.cfg['mappings'][idx]['x'] = x
            self.cfg['mappings'][idx]['y'] = y
            if idx < len(self.row_widgets):
                self.row_widgets[idx]['xy'].config(text='%d, %d' % (x, y))
            save_config(self.cfg)
        self.root.deiconify()
        self.root.lift()

    def test_row(self, idx):
        mp = dict(self.cfg['mappings'][idx])
        self.root.withdraw()
        if self.panel_win and self.cfg['panel']['visible']:
            self.panel_win.withdraw()

        def restore():
            self.root.deiconify()
            self.root.lift()
            if self.panel_win and self.cfg['panel']['visible']:
                self.panel_win.deiconify()

        def go():
            self.perform(mp)
            self.root.after(500, restore)

        self.root.after(700, go)

    # ---------------------------------------------------------------- 执行点击
    def perform(self, mp):
        opts = {
            'move_delay_ms': self.cfg['move_delay_ms'],
            'use_absolute': self.cfg['use_absolute'],
            'restore_cursor': self.cfg['restore_cursor'],
        }
        try:
            do_click(mp['x'], mp['y'], mp.get('button', 'left'),
                     mp.get('double', False), opts)
            if self.cfg['show_marker']:
                self.ui_queue.put(('marker', (mp['x'], mp['y'])))
        except Exception:
            log_error('执行点击失败')

    # ---------------------------------------------------------------- 钩子回调
    def on_key_event(self, vk):
        """在钩子线程执行，必须极快返回。返回 True 表示吞掉该按键。"""
        if self.capture_vk_target is not None:
            self.ui_queue.put(('capture', vk))
            return True

        # 拖动调整坐标期间：只认 Esc（结束拖动），其余按键一律不触发映射
        if self.drag_mode:
            if vk == 0x1B:
                self.ui_queue.put(('exit_drag', None))
                return True
            return False

        # 主窗口在前台时不触发映射，方便正常输入
        if self.is_own_window_foreground():
            return False

        if vk == self.cfg['toggle_vk']:
            now = time.time()
            if now - self.last_fire.get('__toggle__', 0) > 0.4:
                self.last_fire['__toggle__'] = now
                self.ui_queue.put(('toggle', None))
            return True

        if not self.cfg['enabled']:
            return False

        now = time.time()
        deb = max(0.02, self.cfg['debounce_ms'] / 1000.0)
        for mp in self.cfg['mappings']:
            if mp['vk'] == vk:
                if now - self.last_fire.get(vk, 0) < deb:
                    return bool(self.cfg['swallow'])
                self.last_fire[vk] = now
                threading.Thread(target=self.perform, args=(dict(mp),),
                                 daemon=True).start()
                return bool(self.cfg['swallow'])
        return False

    def is_own_window_foreground(self):
        try:
            fg = user32.GetForegroundWindow()
            if not fg:
                return False
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            return pid.value == os.getpid()
        except Exception:
            return False

    # ---------------------------------------------------------------- 消息泵
    def _pump(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == 'capture':
                    if payload == 0x1B:          # Esc 取消
                        self.end_capture(cancel=True)
                    else:
                        self.end_capture(vk=payload)
                elif kind == 'toggle':
                    self.toggle_enabled()
                elif kind == 'exit_drag':
                    self.exit_drag_mode()
                elif kind == 'marker':
                    self.flash_marker(*payload)
        except queue.Empty:
            pass
        except Exception:
            log_error('消息泵异常')
        self.root.after(40, self._pump)

    # ---------------------------------------------------------------- 点击标记
    def flash_marker(self, x, y):
        try:
            if self.marker_win is None:
                w = tk.Toplevel(self.root)
                w.overrideredirect(True)
                w.attributes('-topmost', True)
                key = '#ff00fe'
                try:
                    w.attributes('-transparentcolor', key)
                except Exception:
                    pass
                w.configure(bg=key)
                size = px(56)
                c = tk.Canvas(w, width=size, height=size, bg=key, highlightthickness=0)
                c.pack()
                w.withdraw()
                w.update_idletasks()
                set_ex_style(w, WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
                self.marker_win = w
                self.marker_canvas = c
            c = self.marker_canvas
            size = px(56)
            pad = px(4)
            half = size // 2
            arm = px(5)
            c.delete('all')
            c.create_oval(pad, pad, size - pad, size - pad, outline='#ff3b30', width=px(3))
            c.create_line(half - arm, half, half + arm, half, fill='#ff3b30', width=px(2))
            c.create_line(half, half - arm, half, half + arm, fill='#ff3b30', width=px(2))
            self.marker_win.geometry('+%d+%d' % (int(x) - half, int(y) - half))
            self.marker_win.deiconify()
            self.marker_win.lift()
            self.root.after(280, self._hide_marker)
        except Exception:
            log_error('绘制点击标记失败')

    def _hide_marker(self):
        try:
            if self.marker_win:
                self.marker_win.withdraw()
        except Exception:
            pass

    # ---------------------------------------------------------------- 悬浮按钮面板
    def toggle_panel(self):
        self.cfg['panel']['visible'] = not self.cfg['panel']['visible']
        save_config(self.cfg)
        self.refresh_panel()

    def refresh_panel(self):
        if not self.cfg['panel']['visible']:
            if self.panel_win:
                self.panel_win.withdraw()
            return
        if self.panel_win is None:
            self._build_panel()
        self._fill_panel()
        self.panel_win.deiconify()
        self.panel_win.lift()

    def _build_panel(self):
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes('-topmost', True)
        w.configure(bg=C_CARD)
        w.geometry('+%d+%d' % (self.cfg['panel']['x'], self.cfg['panel']['y']))

        s = self.cfg['button_scale']
        head = tk.Frame(w, bg='#e9edf5', cursor='fleur')
        head.pack(fill='x')
        tk.Label(head, text='≡ 悬浮按钮（拖这里移动）', bg='#e9edf5', fg=C_MUTED,
                 font=self._sf(self.F_S, s), cursor='fleur'
                 ).pack(side='left', padx=max(2, px(int(round(8 * s)))),
                        pady=max(0, px(int(round(3 * s)))))
        tk.Button(head, text='✕', bg='#e9edf5', fg=C_DANGER, relief='flat', bd=0,
                  font=self._sf(self.F_S, s), cursor='hand2',
                  command=self.toggle_panel
                  ).pack(side='right', padx=max(2, px(int(round(4 * s)))))

        body = tk.Frame(w, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)
        body.pack(fill='both', expand=True)
        self.panel_body = body

        def drag_start(e):
            w._dx, w._dy = e.x, e.y

        def drag_move(e):
            nx = w.winfo_x() + e.x - w._dx
            ny = w.winfo_y() + e.y - w._dy
            w.geometry('+%d+%d' % (nx, ny))
            self.cfg['panel']['x'] = nx
            self.cfg['panel']['y'] = ny

        head.bind('<Button-1>', drag_start)
        head.bind('<B1-Motion>', drag_move)
        head.bind('<ButtonRelease-1>', lambda e: save_config(self.cfg))

        w.update_idletasks()
        set_ex_style(w, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.panel_win = w

    def _fill_panel(self):
        for c in self.panel_body.winfo_children():
            c.destroy()
        s = self.cfg['button_scale']
        if not self.cfg['mappings']:
            tk.Label(self.panel_body, text='（无映射）', bg=C_CARD, fg=C_MUTED,
                     font=self._sf(self.F_S, s)).pack(padx=max(4, px(int(round(14 * s)))),
                                                      pady=max(2, px(int(round(8 * s)))))
            return
        fnt = self._sf(self.F_B, s)
        pad_x = max(3, px(int(round(10 * s))))
        pad_y = max(0, px(int(round(5 * s))))
        gap_x = max(2, px(int(round(6 * s))))
        gap_y = max(0, px(int(round(3 * s))))
        # 字符宽度只在「缩小」时跟着收；放大只让字号变大，
        # 否则字符宽 × 字号会产生平方级膨胀（2.0 倍时面板宽到 840px 以上）。
        width = max(4, int(round(16 * min(1.0, s))))
        for mp in self.cfg['mappings']:
            txt = vk_name(mp['vk'])
            if mp.get('label'):
                txt += '  ' + mp['label']
            tk.Button(self.panel_body, text=txt, font=fnt, bg=C_SOFT,
                      fg=C_ACCENT if self.cfg['enabled'] else C_MUTED,
                      activebackground='#dfe7ff', relief='flat', cursor='hand2',
                      anchor='w', padx=pad_x, pady=pad_y, width=width,
                      command=lambda m=dict(mp): self.perform(m)
                      ).pack(fill='x', padx=gap_x, pady=gap_y)

    # ---------------------------------------------------------------- 导入导出
    def export_cfg(self):
        p = filedialog.asksaveasfilename(title='导出配置', defaultextension='.json',
                                         initialfile='keyclicker-config.json',
                                         filetypes=[('JSON', '*.json')])
        if not p:
            return
        try:
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            messagebox.showinfo(APP_NAME, '已导出到：\n' + p)
        except Exception as e:
            messagebox.showerror(APP_NAME, '导出失败：%s' % e)

    def import_cfg(self):
        p = filedialog.askopenfilename(title='导入配置', filetypes=[('JSON', '*.json')])
        if not p:
            return
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.cfg = deep_merge(DEFAULT_CONFIG, data)
            self._clamp_sizes()
            self.var_debounce.set(str(self.cfg['debounce_ms']))
            self.var_marker.set(bool(self.cfg['show_marker']))
            self.var_restore.set(bool(self.cfg['restore_cursor']))
            self.var_swallow.set(bool(self.cfg['swallow']))
            self.btn_toggle_key.config(text=vk_name(self.cfg['toggle_vk']))
            self._refresh_size_labels()
            self.refresh_rows()
            self.refresh_status()
            self.rebuild_panel()
            save_config(self.cfg)
            messagebox.showinfo(APP_NAME, '导入成功')
        except Exception as e:
            messagebox.showerror(APP_NAME, '导入失败：%s' % e)

    def open_dir(self):
        try:
            os.startfile(APP_DIR)
        except Exception:
            pass

    # ---------------------------------------------------------------- 首次运行
    def show_first_run(self):
        self.cfg['first_run'] = False
        save_config(self.cfg)
        messagebox.showinfo(
            APP_NAME + ' —— 三步上手',
            '1) 点【＋ 添加映射】新增一行；\n'
            '2) 点行里的【按键】按钮，然后按下你想用的键（例如 1）；\n'
            '3) 点行里的【坐标】按钮 —— 屏幕上会出现一个可拖动的准星标记，\n'
            '   直接把它拖到目标位置，松手即生效（不用等倒计时）。\n\n'
            '之后无论你在哪个程序里，按 1 就会在目标位置单击一次。\n\n'
            '· 【拖动调整坐标】可一次把所有标记都拖出来一起调\n'
            '· 拖动模式下按 Esc 结束；拖动期间映射自动暂停\n'
            '· 坐标按钮上点右键 = 老式 3 秒光标拾取（全屏游戏里拖不动时用）\n'
            '· 按 %s 可随时全局启用/停用（紧急停用）\n'
            '· 点【显示/隐藏悬浮按钮】得到屏幕上可点的按钮面板\n'
            '· 底部【悬浮按钮 / 拖动标记】两组 − ＋ 可调大小，面板挡视线就调小\n'
            '· 配置自动保存在同目录 config.json'
            % vk_name(self.cfg['toggle_vk']))

    # ---------------------------------------------------------------- 退出
    def on_close(self):
        try:
            if self.drag_mode:
                self.exit_drag_mode()
            self.apply_opts()
            self.cfg['panel']['visible'] = bool(
                self.panel_win and self.panel_win.winfo_viewable())
            save_config(self.cfg)
        except Exception:
            pass
        try:
            self.hook.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# =====================================================================
# 7. 入口
# =====================================================================
def main():
    if not acquire_single_instance():
        focus_existing()      # 已经在运行：把窗口带到前台，本次直接退出
        return
    dpi_mode = setup_dpi_awareness()
    try:
        app = App()
        app.run()
    except Exception:
        log_error('程序启动/运行失败（DPI 模式=%s）' % dpi_mode)
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(APP_NAME, '程序出错，详情见 error.log\n\n%s'
                                 % traceback.format_exc())
            r.destroy()
        except Exception:
            pass
        raise


if __name__ == '__main__':
    main()
