"""Значок Джарвиса в левом верхнем углу экрана: появляется, когда ассистент слушает, думает или говорит.

Отдельное «слоистое» окно Windows (UpdateLayeredWindow) с попиксельной прозрачностью: всегда поверх,
пропускает клики мыши, не появляется в панели задач и не забирает фокус. Рисуется в собственном
потоке (Pillow, ~1 мс на кадр), плавно появляется и исчезает через общую прозрачность окна.
"""

import ctypes
import logging
import math
import threading
import time
from ctypes import wintypes
from typing import Callable

from . import logo

log = logging.getLogger("jarvis.overlay")

# --- Константы Win32 -----------------------------------------------------------------------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020  # клики проходят насквозь
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080  # нет в панели задач и Alt+Tab
WS_EX_NOACTIVATE = 0x08000000  # не забирает фокус
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_RGB = 0
PM_REMOVE = 0x0001
WM_DESTROY = 0x0002
SPI_GETWORKAREA = 0x0030
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
HWND_TOPMOST = -1

LRESULT = ctypes.c_ssize_t
# WINFUNCTYPE есть только в Windows; запасной вариант нужен, чтобы модуль импортировался везде (тесты, --selftest)
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

MARGIN = 18  # отступ от угла рабочей области, px при 100 % масштабе
SIZE = 84  # размер значка, px при 100 % масштабе
MAX_ALPHA = 232  # слегка прозрачный
HOLD_SECONDS = 1.2  # сколько ещё показывать значок после того, как ассистент затих


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _bind():
    """Загружает функции Win32 с точными типами (без них на 64-битной Windows возможны падения)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sig = [
        (user32.DefWindowProcW, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], LRESULT),
        (user32.RegisterClassExW, [ctypes.POINTER(WNDCLASSEXW)], wintypes.ATOM),
        (user32.CreateWindowExW, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                  wintypes.HINSTANCE, wintypes.LPVOID], wintypes.HWND),
        (user32.DestroyWindow, [wintypes.HWND], wintypes.BOOL),
        (user32.ShowWindow, [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        (user32.SetWindowPos, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT], wintypes.BOOL),
        (user32.UpdateLayeredWindow, [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                      ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                      wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD],
         wintypes.BOOL),
        (user32.GetDC, [wintypes.HWND], wintypes.HDC),
        (user32.ReleaseDC, [wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (user32.PeekMessageW, [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                               wintypes.UINT], wintypes.BOOL),
        (user32.TranslateMessage, [ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
        (user32.DispatchMessageW, [ctypes.POINTER(wintypes.MSG)], LRESULT),
        (user32.SystemParametersInfoW, [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT], wintypes.BOOL),
        (gdi32.CreateCompatibleDC, [wintypes.HDC], wintypes.HDC),
        (gdi32.CreateDIBSection, [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                  ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD], wintypes.HBITMAP),
        (gdi32.SelectObject, [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ),
        (gdi32.DeleteObject, [wintypes.HGDIOBJ], wintypes.BOOL),
        (gdi32.DeleteDC, [wintypes.HDC], wintypes.BOOL),
        (kernel32.GetModuleHandleW, [wintypes.LPCWSTR], wintypes.HMODULE),
    ]
    for func, argtypes, restype in sig:
        func.argtypes = argtypes
        func.restype = restype
    return user32, gdi32, kernel32


def _dpi_scale(user32) -> float:
    try:
        return user32.GetDpiForSystem() / 96.0  # Windows 10+
    except (AttributeError, OSError):
        return 1.0


def to_bgra_premultiplied(image) -> bytes:
    """RGBA (Pillow) -> BGRA с умноженной на альфу яркостью — формат, который ждёт UpdateLayeredWindow."""
    import numpy as np

    rgba = np.asarray(image, dtype=np.uint16)
    out = np.empty(rgba.shape, dtype=np.uint8)
    alpha = rgba[..., 3:4]
    out[..., :3] = (rgba[..., 2::-1] * alpha // 255).astype(np.uint8)  # R,G,B -> B,G,R
    out[..., 3] = rgba[..., 3]
    return out.tobytes()


class Overlay:
    """Потокобезопасный значок: set_state() из любого потока, рисует собственный поток."""

    def __init__(self, level: Callable[[], dict] | None = None):
        self.enabled = True
        self._level = level or (lambda: {"mic": 0.0, "speech": 0.0})
        self._state = "idle"
        self._last_active = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_state(self, state: str) -> None:
        self._state = state
        if state != "idle":
            self._last_active = time.monotonic()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="overlay")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # --- Поток окна ---------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:  # noqa: BLE001 — значок не должен ронять ассистента
            log.exception("Значок в углу экрана отключён из-за ошибки")

    def _loop(self) -> None:
        user32, gdi32, kernel32 = _bind()
        scale = _dpi_scale(user32)
        size = max(48, round(SIZE * scale))
        work = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
        x, y = work.left + round(MARGIN * scale), work.top + round(MARGIN * scale)

        def wndproc(hwnd, msg, wparam, lparam):
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # ссылку держим, иначе сборщик мусора уронит процесс
        instance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = instance
        wc.lpszClassName = "JarvisOverlay"
        user32.RegisterClassExW(ctypes.byref(wc))
        ex_style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        hwnd = user32.CreateWindowExW(ex_style, "JarvisOverlay", "Jarvis", WS_POPUP, x, y, size, size,
                                      None, None, instance, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        screen_dc = user32.GetDC(None)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = size
        info.bmiHeader.biHeight = -size  # отрицательная высота — строки сверху вниз
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(info), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        old_bitmap = gdi32.SelectObject(mem_dc, bitmap)

        position = wintypes.POINT(x, y)
        dims = wintypes.SIZE(size, size)
        source = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 0, AC_SRC_ALPHA)
        msg = wintypes.MSG()
        alpha = 0.0
        shown = False
        started = time.monotonic()
        try:
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

                now = time.monotonic()
                state = self._state
                visible = self.enabled and (state != "idle" or now - self._last_active < HOLD_SECONDS)
                target = 1.0 if visible else 0.0
                alpha += (target - alpha) * (0.25 if visible else 0.18)  # плавное появление/исчезание
                if abs(alpha - target) < 0.01:
                    alpha = target

                if alpha > 0:
                    levels = self._level() if state in ("listening", "speaking") else {}
                    level = levels.get("mic" if state == "listening" else "speech", 0.0)
                    frame = logo.render(size, t=now - started, state=state, level=level, opacity=0.92)
                    data = to_bgra_premultiplied(frame)
                    ctypes.memmove(bits, data, len(data))
                    blend.SourceConstantAlpha = int(MAX_ALPHA * self._ease(alpha))
                    user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(position), ctypes.byref(dims), mem_dc,
                                               ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA)
                    if not shown:
                        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                        user32.SetWindowPos(hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                        shown = True
                    self._stop.wait(1 / 30)
                else:
                    if shown:
                        user32.ShowWindow(hwnd, SW_HIDE)
                        shown = False
                    self._stop.wait(0.1)  # в покое почти не тратим процессор
        finally:
            gdi32.SelectObject(mem_dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)
            user32.DestroyWindow(hwnd)

    @staticmethod
    def _ease(x: float) -> float:
        return 1 - math.pow(1 - x, 3)
