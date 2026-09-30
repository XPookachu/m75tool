"""M75 side buttons - tiny no-iCUE binder. Launch with m75.bat (closes iCUE first, does not change the mouse on launch)."""
import ctypes
import msvcrt
import os
import sys
import time
from ctypes import wintypes as w

import m75map as M

U = ctypes.WinDLL("user32")
K = ctypes.WinDLL("kernel32")
U.GetForegroundWindow.restype = ctypes.c_void_p
U.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD)]
U.GetAsyncKeyState.argtypes = [ctypes.c_int]
U.GetAsyncKeyState.restype = ctypes.c_short
K.GetConsoleWindow.restype = ctypes.c_void_p
K.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
K.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
K.CloseHandle.argtypes = [ctypes.c_void_p]

ESC, DEL, BKSP = 0x1B, 0x2E, 0x08
MOUSE_VKS = {1, 2, 4, 5, 6}
SKIP_VKS = {0x10, 0x11, 0x12, 0x15, 0x17, 0x18, 0x19, 0x1C, 0x1D, 0x1E, 0x1F}  # generic shift/ctrl/alt, IME
ITEMS = 6
os.system("")  # enable ANSI escapes

# ---- look
BOLD, DIM, INV, OFF = "\x1b[1m", "\x1b[2m", "\x1b[7m", "\x1b[0m"
CYAN, GREEN, YELLOW, RED = "\x1b[36m", "\x1b[32m", "\x1b[33m", "\x1b[31m"
W = 54


def enter_screen():
    sys.stdout.write("\x1b[?1049h\x1b[?25l")  # alternate screen (no scrollback history) + hide cursor
    sys.stdout.flush()


def leave_screen():
    sys.stdout.write("\x1b[?25h\x1b[?1049l")
    sys.stdout.flush()
    flush()


def frame(lines):
    """Repaint in place: home, each line erased to its end, then clear the rest."""
    sys.stdout.write("\x1b[H" + "".join(f"{ln}\x1b[K\n" for ln in lines) + "\x1b[J")
    sys.stdout.flush()


# ---- input: physical key state only, so right-click paste / stray console text can never act as a key
class PE(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("pid", w.DWORD), ("heap", ctypes.c_size_t),
                ("module", w.DWORD), ("threads", w.DWORD), ("ppid", w.DWORD), ("pri", ctypes.c_long),
                ("flags", w.DWORD), ("exe", w.WCHAR * 260)]


K.Process32FirstW.argtypes = K.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PE)]
K.Process32FirstW.restype = K.Process32NextW.restype = w.BOOL


def ancestor_pids():
    snap = K.CreateToolhelp32Snapshot(2, 0)
    if snap in (None, ctypes.c_void_p(-1).value):
        return {os.getpid()}
    parent = {}
    pe = PE()
    pe.dwSize = ctypes.sizeof(pe)
    ok = K.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        parent[pe.pid] = pe.ppid
        ok = K.Process32NextW(snap, ctypes.byref(pe))
    K.CloseHandle(snap)
    out, pid = set(), os.getpid()
    while pid in parent and pid not in out:
        out.add(pid)
        pid = parent[pid]
    return out


ANCESTORS = ancestor_pids()
START_FG = U.GetForegroundWindow()


def ours():
    """True while this terminal window is the active window (keys pressed elsewhere are ignored)."""
    fg = U.GetForegroundWindow()
    if fg in (START_FG, K.GetConsoleWindow()):
        return True
    pid = w.DWORD(0)
    U.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    return pid.value in ANCESTORS


def pressed(vk):
    return U.GetAsyncKeyState(vk) & 0x8000 != 0


def flush():
    while msvcrt.kbhit():
        msvcrt.getwch()


NAV = {0x26: "up", 0x28: "down", 0x0D: "enter", 0x1B: "esc"}
for _i in range(1, ITEMS + 1):
    NAV[0x30 + _i] = str(_i)
    NAV[0x60 + _i] = str(_i)
_down = set()


def menu_key():
    global _down
    while True:
        flush()
        cur = {vk for vk in NAV if pressed(vk)} if ours() else set()
        new = cur - _down
        _down = cur
        if new:
            return NAV[min(new)]
        time.sleep(0.01)


def capture_vk():
    """Wait for the next key press (any key incl. modifiers). Returns its virtual-key code."""
    watch = [v for v in range(8, 255) if v not in MOUSE_VKS and v not in SKIP_VKS]
    while any(pressed(v) for v in watch):
        time.sleep(0.02)
    while True:
        flush()
        if ours():
            for v in watch:
                if pressed(v):
                    while pressed(v):
                        time.sleep(0.01)
                    flush()
                    return v
        time.sleep(0.01)


# ---- screens
def show(b, p):
    t = b["type"]
    if t == "keyboard":
        return M.USAGE_NAME.get(b["usage"], f"key 0x{b['usage']:02X}")
    if t == "default":
        return f"{M.DEFAULT_NAME[p]} {DIM}(default){OFF}"
    if t == "unbound":
        return f"{DIM}unbound{OFF}"
    return b.get("info", "custom")


def visible_len(s):
    n, i = 0, 0
    while i < len(s):
        if s[i] == "\x1b":
            i = s.index("m", i) + 1
        else:
            n += 1
            i += 1
    return n


def row(num, label, value, selected):
    body = f" {num}  {label:<12s}{value}"
    pad = " " * max(0, W - visible_len(body))
    if selected:
        return f"  {INV}{body}{pad}{OFF}"
    return f"  {body}"


def draw(hand, buttons, sel, msg="", kind="ok"):
    rule = f"  {DIM}{'-' * W}{OFF}"
    lines = ["", f"  {BOLD}{CYAN}M75 side buttons{OFF}   {DIM}no iCUE needed{OFF}", rule]
    for i, p in enumerate(M.PHYS):
        lines.append(row(i + 1, M.LABEL[p], show(buttons[p], p), sel == i))
    lines.append(rule)
    lines.append(row(5, "Hand", f"{hand.capitalize()}-handed", sel == 4))
    lines.append(row(6, "Load saved", f"{DIM}re-apply after using iCUE{OFF}", sel == 5))
    lines.append(rule)
    color = {"ok": GREEN, "warn": YELLOW, "err": RED}[kind]
    lines.append(f"  {color}{msg}{OFF}" if msg else "")
    lines.append(f"  {DIM}1-{ITEMS} or Up/Down + Enter: pick    Esc: quit{OFF}")
    frame(lines)


def draw_bind(p, buttons, note=""):
    lines = ["", f"  {BOLD}{CYAN}{M.LABEL[p]}{OFF}   {DIM}now:{OFF} {show(buttons[p], p)}", "",
             "  Press the key you want on this button.", "",
             f"  {DIM}Esc{OFF} cancel    {DIM}Del{OFF} unbind    {DIM}Backspace{OFF} back to default"]
    if note:
        lines += ["", f"  {YELLOW}{note}{OFF}"]
    frame(lines)


def bind_prompt(p, buttons):
    draw_bind(p, buttons)
    while True:
        vk = capture_vk()
        if vk == ESC:
            return None
        if vk == DEL:
            return {"type": "unbound"}
        if vk == BKSP:
            return {"type": "default"}
        if vk in M.VK_TO_USAGE:
            return {"type": "keyboard", "usage": M.VK_TO_USAGE[vk]}
        draw_bind(p, buttons, "That key can't be bound, try another.")


def plan(sel, hand, buttons):
    """What menu item sel wants on the mouse: (hand, buttons, busy msg, fail prefix, done msg) or an error str."""
    if sel == 5:
        saved = M.load_config()
        if saved is None:
            return "Load failed: no valid saved config"
        return saved + ("Loading saved config...", "Load failed", "Saved config loaded onto the mouse.")
    if sel == 4:
        new_hand = "left" if hand == "right" else "right"
        return (new_hand, buttons, "Switching hand...", "Switch failed",
                f"Now {new_hand}-handed. Binds stay on the same physical buttons.")
    p = M.PHYS[sel]
    new = bind_prompt(p, buttons)
    if new is None:
        return None
    return hand, {**buttons, p: new}, "Saving to the mouse...", "Save failed", f"{M.LABEL[p]} saved. Try it now."


def interactive():
    M.close_icue()
    d = M.Dev()
    started = False
    try:
        hand = d.hand()  # show what the mouse has right now; never auto-apply the saved config
        buttons = M.read_buttons(d, hand)
        buttons = {p: ({"type": "default"} if b["type"] == "other" else b) for p, b in buttons.items()}
        if not os.path.exists(M.CONFIG):  # first run: back up and adopt whatever the mouse has now
            M.cmd_backup(d, "m75_first_run")
            M.save_config(hand, buttons)
        enter_screen()
        started = True
        sel, msg, kind = 0, "", "ok"
        while True:
            draw(hand, buttons, sel, msg, kind)
            msg, kind = "", "ok"
            key = menu_key()
            if key == "esc":
                break
            if key in ("up", "down"):
                sel = (sel + (1 if key == "down" else -1)) % ITEMS
                continue
            if key.isdigit():
                sel = int(key) - 1
            todo = plan(sel, hand, buttons)
            if todo is None:
                msg, kind = "Cancelled.", "warn"
                continue
            if isinstance(todo, str):
                msg, kind = todo, "err"
                continue
            new_hand, new_buttons, busy, fail, done = todo
            draw(hand, buttons, sel, busy, "warn")
            try:
                M.close_icue()
                M.write_buttons(d, new_hand, new_buttons)
            except M.M75Error as e:
                msg, kind = f"{fail}: {e}", "err"
                continue
            hand, buttons, msg = new_hand, new_buttons, done
            M.save_config(hand, buttons)
    finally:
        d.close()
        if started:
            leave_screen()


if __name__ == "__main__":
    try:
        interactive()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(e if isinstance(e, M.M75Error) else f"error: {type(e).__name__}: {e}")
        input("Press Enter to close...")
