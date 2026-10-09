"""Capture a window's own pixels with ``PrintWindow`` (Windows only).

Unlike grabbing screen pixels, this asks the window to render itself, so it
works while other windows cover the game and can never include another app's
content. ``PW_RENDERFULLCONTENT`` (Windows 8.1+) makes it work for many
DirectX-rendered games. Some games still return a black image; the caller
decides what to do then.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

import numpy as np

from tanaw.frames import Frame

logger = logging.getLogger(__name__)

_PW_CLIENTONLY = 0x1
_PW_RENDERFULLCONTENT = 0x2
_BI_RGB = 0
_DIB_RGB_COLORS = 0


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


# Private DLL handles with explicit prototypes, so 64-bit handles aren't truncated
# and we don't change prototypes other libraries set on ctypes.windll.
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_user32.GetWindowDC.argtypes = (wintypes.HWND,)
_user32.GetWindowDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
_user32.ReleaseDC.restype = ctypes.c_int
_user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
_user32.PrintWindow.restype = wintypes.BOOL
_gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = (wintypes.HDC,)
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.GetDIBits.argtypes = (
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(_BitmapInfoHeader), wintypes.UINT,
)
_gdi32.GetDIBits.restype = ctypes.c_int


def print_window_client(hwnd: int, width: int, height: int) -> Frame | None:
    """Render the window's client area into a BGR frame, or None if Windows refused."""
    if width <= 0 or height <= 0:
        return None
    window_dc = _user32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    mem_dc = None
    bitmap = None
    try:
        mem_dc = _gdi32.CreateCompatibleDC(window_dc)
        bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
        if not mem_dc or not bitmap:
            return None
        previous = _gdi32.SelectObject(mem_dc, bitmap)
        ok = _user32.PrintWindow(hwnd, mem_dc, _PW_CLIENTONLY | _PW_RENDERFULLCONTENT)
        _gdi32.SelectObject(mem_dc, previous)  # GetDIBits needs the bitmap deselected
        if not ok:
            logger.debug("PrintWindow failed (error %d)", ctypes.get_last_error())
            return None

        header = _BitmapInfoHeader()
        header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height  # negative: top-down rows
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = _BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = _gdi32.GetDIBits(
            mem_dc, bitmap, 0, height, buffer, ctypes.byref(header), _DIB_RGB_COLORS
        )
        if rows != height:
            return None
    finally:
        if bitmap:
            _gdi32.DeleteObject(bitmap)
        if mem_dc:
            _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(hwnd, window_dc)

    bgra = np.frombuffer(buffer.raw, dtype=np.uint8).reshape(height, width, 4)
    image: Frame = np.ascontiguousarray(bgra[:, :, :3])
    return image
