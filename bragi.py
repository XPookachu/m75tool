"""Minimal Windows HID (ctypes) + Corsair Bragi framing. No third-party deps."""
import ctypes
import struct
import time
from ctypes import wintypes as w
from dataclasses import dataclass

VID = 0x1B1C
PID = 0x1BF0

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
hid = ctypes.WinDLL("hid")
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
DIGCF_PRESENT = 2
DIGCF_DEVICEINTERFACE = 0x10

LPVOID = ctypes.c_void_p


class GUID(ctypes.Structure):
    _fields_ = [("a", w.DWORD), ("b", w.WORD), ("c", w.WORD), ("d", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("Guid", GUID), ("Flags", w.DWORD), ("Reserved", LPVOID)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", w.USHORT), ("UsagePage", w.USHORT),
        ("InputReportByteLength", w.USHORT), ("OutputReportByteLength", w.USHORT),
        ("FeatureReportByteLength", w.USHORT), ("Reserved", w.USHORT * 17),
        ("NumberLinkCollectionNodes", w.USHORT),
        ("NumberInputButtonCaps", w.USHORT), ("NumberInputValueCaps", w.USHORT),
        ("NumberInputDataIndices", w.USHORT), ("NumberOutputButtonCaps", w.USHORT),
        ("NumberOutputValueCaps", w.USHORT), ("NumberOutputDataIndices", w.USHORT),
        ("NumberFeatureButtonCaps", w.USHORT), ("NumberFeatureValueCaps", w.USHORT),
        ("NumberFeatureDataIndices", w.USHORT),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", LPVOID), ("InternalHigh", LPVOID), ("Offset", w.DWORD),
                ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]


hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetPreparsedData.argtypes = [w.HANDLE, ctypes.POINTER(LPVOID)]
hid.HidD_FreePreparsedData.argtypes = [LPVOID]
hid.HidP_GetCaps.argtypes = [LPVOID, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetCaps.restype = ctypes.c_long

setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD]
setupapi.SetupDiGetClassDevsW.restype = LPVOID
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [LPVOID, LPVOID, ctypes.POINTER(GUID), w.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [LPVOID, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), LPVOID]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [LPVOID]

k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, LPVOID, w.DWORD, w.DWORD, w.HANDLE]
k32.CreateFileW.restype = w.HANDLE
k32.CloseHandle.argtypes = [w.HANDLE]
k32.CreateEventW.argtypes = [LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
k32.CreateEventW.restype = w.HANDLE
k32.ResetEvent.argtypes = [w.HANDLE]
k32.ReadFile.argtypes = [w.HANDLE, LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
k32.WriteFile.argtypes = [w.HANDLE, LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(OVERLAPPED)]
k32.GetOverlappedResult.argtypes = [w.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(w.DWORD), w.BOOL]
k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k32.CancelIoEx.argtypes = [w.HANDLE, LPVOID]


@dataclass
class Info:
    path: str
    usage_page: int
    usage: int
    in_len: int
    out_len: int
    feat_len: int

    @property
    def iface(self):
        p = self.path.lower()
        i = p.find("&mi_")
        return int(p[i + 4:i + 6], 16) if i >= 0 else -1

    def __repr__(self):
        return (f"MI_{self.iface:02d} page=0x{self.usage_page:04X} usage=0x{self.usage:04X} "
                f"in={self.in_len} out={self.out_len} feat={self.feat_len}  {self.path}")


def enumerate_devices(vid=VID, pid=PID):
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev in (None, INVALID_HANDLE):
        return []
    out = []
    idx = 0
    try:
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), idx, ctypes.byref(did)):
                break
            idx += 1
            need = w.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            buf = ctypes.create_string_buffer(need.value)
            # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W: 8 on 64-bit, 6 on 32-bit
            struct.pack_into("I", buf, 0, 8 if ctypes.sizeof(LPVOID) == 8 else 6)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(did), buf, need.value, None, None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if f"vid_{vid:04x}&pid_{pid:04x}" not in path.lower():
                continue
            h = k32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
            if h in (None, INVALID_HANDLE):
                continue
            try:
                pp = LPVOID()
                if not hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                    continue
                caps = HIDP_CAPS()
                hid.HidP_GetCaps(pp, ctypes.byref(caps))
                hid.HidD_FreePreparsedData(pp)
                out.append(Info(path, caps.UsagePage, caps.Usage, caps.InputReportByteLength,
                                caps.OutputReportByteLength, caps.FeatureReportByteLength))
            finally:
                k32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


class HidDev:
    """Overlapped HID handle with timeouts. Reads/writes include the report-id byte."""

    def __init__(self, info: Info, write=True):
        self.info = info
        access = GENERIC_READ | (GENERIC_WRITE if write else 0)
        self.h = k32.CreateFileW(info.path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                 OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if self.h in (None, INVALID_HANDLE):
            raise OSError(f"CreateFile failed ({ctypes.get_last_error()}) for {info.path}")
        self.ev = k32.CreateEventW(None, True, False, None)
        self._pending = None  # (overlapped, buffer) of an outstanding read

    def close(self):
        if self._pending:
            k32.CancelIoEx(self.h, None)
        k32.CloseHandle(self.h)
        k32.CloseHandle(self.ev)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def write(self, data: bytes, timeout_ms=1000):
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        ov = OVERLAPPED()
        ov.hEvent = self.ev
        k32.ResetEvent(self.ev)
        n = w.DWORD(0)
        ok = k32.WriteFile(self.h, buf, len(data), ctypes.byref(n), ctypes.byref(ov))
        if not ok:
            if ctypes.get_last_error() != ERROR_IO_PENDING:
                raise OSError(f"WriteFile failed {ctypes.get_last_error()}")
            if k32.WaitForSingleObject(self.ev, timeout_ms) != WAIT_OBJECT_0:
                k32.CancelIoEx(self.h, ctypes.byref(ov))
                raise TimeoutError("write timeout")
            k32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(n), False)
        return n.value

    def read(self, timeout_ms=1000):
        """Return one input report (bytes incl. report id) or None on timeout."""
        if self._pending is None:
            buf = ctypes.create_string_buffer(max(self.info.in_len, 1))
            ov = OVERLAPPED()
            ov.hEvent = self.ev
            k32.ResetEvent(self.ev)
            n = w.DWORD(0)
            ok = k32.ReadFile(self.h, buf, len(buf), ctypes.byref(n), ctypes.byref(ov))
            if not ok and ctypes.get_last_error() != ERROR_IO_PENDING:
                raise OSError(f"ReadFile failed {ctypes.get_last_error()}")
            self._pending = (ov, buf)
        ov, buf = self._pending
        if k32.WaitForSingleObject(self.ev, timeout_ms) != WAIT_OBJECT_0:
            return None
        n = w.DWORD(0)
        k32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(n), False)
        self._pending = None
        return bytes(buf.raw[:n.value])


# ---------------------------------------------------------------- Bragi
MAGIC = 0x08
SET, GET, CLOSE, WRITE, CONTINUE, READ, PROBE, OPEN = 0x01, 0x02, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0D
PROP_MODE = 0x03
MODE_HW, MODE_SW = 1, 2


class Bragi:
    """Bragi command pipe on the vendor HID collection (report id 0, 64-byte-ish payload)."""

    def __init__(self, info: Info):
        self.dev = HidDev(info)
        self.plen = info.out_len - 1  # payload length without report id
        self.rlen = info.in_len

    def close(self):
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _send(self, pkt):
        self.dev.write(b"\x00" + bytes(pkt))

    def _reply(self, cmd, timeout_ms=1000):
        """Wait for the reply to cmd, discarding unsolicited reports (button notifications etc.)."""
        deadline = time.time() + timeout_ms / 1000
        while True:
            left = int((deadline - time.time()) * 1000)
            if left <= 0:
                raise TimeoutError(f"no reply to cmd 0x{cmd:02X}")
            r = self.dev.read(left)
            if r is None:
                continue
            body = r[1:] if self.rlen == len(r) and r[0] == 0 else r
            if len(body) >= 3 and body[0] == 0 and body[1] == cmd:
                return body

    def xfer(self, cmd: int, *args: int, raw: bytes = b"", timeout_ms=1000):
        pkt = bytearray(self.plen)
        pkt[0] = MAGIC
        pkt[1] = cmd
        body = bytes(args) + raw
        pkt[2:2 + len(body)] = body
        self._send(pkt)
        return self._reply(cmd, timeout_ms)

    # -- properties
    def get_prop(self, prop: int):
        r = self.xfer(GET, prop, 0)
        if r[2]:
            return None, r[2]
        return r[3] | (r[4] << 8) | (r[5] << 16), 0

    def set_prop(self, prop: int, val: int):
        r = self.xfer(SET, prop, 0, val & 0xFF, (val >> 8) & 0xFF)
        return r[2]

    # -- file handles
    def open_handle(self, handle: int, resource: int):
        r = self.xfer(OPEN, handle, resource & 0xFF, resource >> 8)
        if r[2] == 0x03:  # already open
            self.close_handle(handle)
            r = self.xfer(OPEN, handle, resource & 0xFF, resource >> 8)
        return r[2]

    def close_handle(self, handle: int):
        r = self.xfer(CLOSE, 1, handle)
        return r[2]

    def probe_handle(self, handle: int):
        r = self.xfer(PROBE, handle)
        if r[2]:
            return None
        return struct.unpack_from("<I", r, 5)[0]

    def read_handle(self, handle: int):
        n = self.probe_handle(handle)
        if not n:
            return None
        out = bytearray()
        while len(out) < n:
            r = self.xfer(READ, handle)
            if r[2]:
                return None
            out += r[3:self.plen]
        return bytes(out[:n])

    def write_handle(self, handle: int, data: bytes):
        """WRITE header: magic,cmd,handle,len(u32 LE) then 4 zero bytes, payload; CONTINUE for the rest."""
        first_cap = self.plen - 7
        pkt = bytearray(self.plen)
        pkt[0], pkt[1], pkt[2] = MAGIC, WRITE, handle
        struct.pack_into("<I", pkt, 3, len(data))
        pkt[7:7 + min(first_cap, len(data))] = data[:first_cap]
        self._send(pkt)
        r = self._reply(WRITE)
        if r[2]:
            return r[2]
        pos = first_cap
        cont_cap = self.plen - 3
        while pos < len(data):
            pkt = bytearray(self.plen)
            pkt[0], pkt[1], pkt[2] = MAGIC, CONTINUE, handle
            piece = data[pos:pos + cont_cap]
            pkt[3:3 + len(piece)] = piece
            self._send(pkt)
            r = self._reply(CONTINUE)
            if r[2]:
                return r[2]
            pos += cont_cap
        return 0


def find_bragi(infos=None):
    infos = infos or enumerate_devices()
    for i in infos:
        if i.usage_page == 0xFF42:
            return i
    return None
