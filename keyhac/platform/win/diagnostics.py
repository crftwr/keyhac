"""What Windows can say about this process that Python cannot."""

import ctypes
from ctypes import wintypes

from keyhac.platform.base import ProcessMemory


class _ProcessMemoryCountersEx(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS_EX of <psapi.h>."""
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.K32GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCountersEx), wintypes.DWORD]
_kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
_kernel32.GetProcessHandleCount.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetProcessHandleCount.restype = wintypes.BOOL
_user32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_user32.GetGuiResources.restype = wintypes.DWORD

_GR_GDIOBJECTS = 0
_GR_USEROBJECTS = 1


def process_memory() -> ProcessMemory:
    """Private bytes are what the process alone is charged for, and unlike the
    working set they do not fall when Windows trims it, so they are the ones
    that show accumulation. A handle or GDI/USER object leak does not show in
    memory for a long time, and has limits of its own (10,000 GDI objects)."""
    process = _kernel32.GetCurrentProcess()
    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    if not _kernel32.K32GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    handles = wintypes.DWORD()
    if not _kernel32.GetProcessHandleCount(process, ctypes.byref(handles)):
        raise ctypes.WinError(ctypes.get_last_error())
    megabytes = lambda n: f"{n / (1024 * 1024):,.1f} MB"
    return ProcessMemory(counters.PrivateUsage, {
        "Private bytes": megabytes(counters.PrivateUsage),
        "Private bytes, peak": megabytes(counters.PeakPagefileUsage),
        "Working set": megabytes(counters.WorkingSetSize),
        "Working set, peak": megabytes(counters.PeakWorkingSetSize),
        "Handles": str(handles.value),
        "GDI objects": str(_user32.GetGuiResources(process, _GR_GDIOBJECTS)),
        "USER objects": str(_user32.GetGuiResources(process, _GR_USEROBJECTS)),
    })
