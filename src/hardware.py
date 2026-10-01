import ctypes
import json
import shutil
import subprocess
import time
from pathlib import Path

from src.config import log_error

_CACHED_DISKS_INFO = None
_CACHED_DISKS_TIME = 0


def get_system_disks_info():
    global _CACHED_DISKS_INFO, _CACHED_DISKS_TIME
    if _CACHED_DISKS_INFO is not None and time.time() - _CACHED_DISKS_TIME < 60:
        return _CACHED_DISKS_INFO

    disks = {}
    try:
        cmd1 = 'Get-PhysicalDisk | Select-Object DeviceId, MediaType, BusType, FriendlyName | ConvertTo-Json'
        p1 = subprocess.run(['powershell', '-Command', cmd1], capture_output=True, text=True, timeout=10)
        raw_disks = json.loads(p1.stdout) if p1.stdout.strip() else []
        if isinstance(raw_disks, dict):
            raw_disks = [raw_disks]
        phys = {str(d.get('DeviceId')): d for d in raw_disks}

        cmd2 = 'Get-Partition | Where-Object DriveLetter | Select-Object DiskNumber, DriveLetter, Size | ConvertTo-Json'
        p2 = subprocess.run(['powershell', '-Command', cmd2], capture_output=True, text=True, timeout=10)
        raw_parts = json.loads(p2.stdout) if p2.stdout.strip() else []
        if isinstance(raw_parts, dict):
            raw_parts = [raw_parts]

        for part in raw_parts:
            dl = str(part.get('DriveLetter', '')).upper()
            dn = str(part.get('DiskNumber', ''))
            if dl and dn in phys:
                p_info = phys[dn]
                m_type = p_info.get('MediaType', 'Desconhecido')
                bus = p_info.get('BusType', '')
                model = p_info.get('FriendlyName', '')
                is_ssd = 'ssd' in m_type.lower() or 'nvme' in bus.lower()
                desc = f'SSD ({bus}) ⚡ Rápido' if is_ssd else f'HDD ({bus}) 🐢 Mecânico'
                disks[dl] = {
                    'drive': dl + ':',
                    'media_type': m_type,
                    'bus': bus,
                    'model': model,
                    'is_ssd': is_ssd,
                    'type_desc': desc
                }
    except Exception as e:
        log_error("Erro ao detectar tipo de mídia dos discos", e)

    if not disks:
        import string
        for letter in string.ascii_uppercase:
            drive_p = Path(f"{letter}:\\")
            if drive_p.exists():
                is_c = (letter == 'C')
                disks[letter] = {
                    'drive': f"{letter}:",
                    'media_type': 'SSD' if is_c else 'Desconhecido',
                    'bus': 'NVMe' if is_c else 'Local',
                    'model': 'Disco do Sistema' if is_c else 'Disco Local',
                    'is_ssd': is_c,
                    'type_desc': 'SSD ⚡ Rápido' if is_c else 'Disco Local'
                }

    _CACHED_DISKS_INFO = disks
    _CACHED_DISKS_TIME = time.time()
    return disks


def get_drive_free_space(drive_letter):
    try:
        dl = str(drive_letter).rstrip(':').upper()
        usage = shutil.disk_usage(f"{dl}:\\")
        return usage.free, usage.total
    except Exception:
        return 0, 0


def get_path_disk_desc(path):
    try:
        p = Path(path).resolve()
        dl = p.drive.rstrip(':').upper()
        disks = get_system_disks_info()
        if dl in disks:
            return disks[dl]['type_desc']
    except Exception:
        pass
    return "Disco Local"


def get_system_ram_info():
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total_gb = stat.ullTotalPhys / (1024 ** 3)
        avail_gb = stat.ullAvailPhys / (1024 ** 3)
        used_gb = total_gb - avail_gb
        return {
            "load_pct": stat.dwMemoryLoad,
            "total_gb": total_gb,
            "avail_gb": avail_gb,
            "used_gb": used_gb,
            "total_bytes": stat.ullTotalPhys,
            "avail_bytes": stat.ullAvailPhys,
        }
    except Exception as e:
        log_error("Erro ao obter uso de memória do sistema", e)
        return {
            "load_pct": 50,
            "total_gb": 16.0,
            "avail_gb": 8.0,
            "used_gb": 8.0,
            "total_bytes": 16 * 1024**3,
            "avail_bytes": 8 * 1024**3
        }
