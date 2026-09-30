"""m75map - onboard profile engine for the Corsair M75 (USB 1B1C:1BF0). No iCUE needed afterwards.

CLI:
  python m75map.py status            show the current onboard profile in readable form
  python m75map.py backup [name]     save all onboard profile files + handedness to backup\\<name>\\
  python m75map.py apply             push the saved config (m75map.json) to the mouse
  python m75map.py restore [name]    write a backup back (only if the file layout is unchanged)
The interactive utility is m75.bat (m75.py); its "Load saved" option does the same as apply.

What the tool touches on the mouse, and nothing else:
  * the 8-byte key-flags file and the key-assignment list (only the 4 side-button entries; all other entries,
    incl. DPI, are copied through unchanged)
  * the keyboard action files of the 4 side buttons (created / rewritten / deleted as needed)
  * property 0x52 (handedness) and a software->hardware mode bounce so the firmware re-reads the profile
    (the current DPI stage is put back after the bounce)
DPI stages, lighting, polling rate, profile name etc. are never written.

Onboard profile layout (decoded from iCUE-written dumps and a trace of iCUE's own writes):
  res 0x0F : [.. .. .. .. rootLo rootHi]        active profile root file id
  root     : 1e f1 .. .. .. .. <5 x u16 file ids: assignments, dpi, lighting, refs, key flags> ... name
  flags    : af 0b <8 bytes>   per key: Mouse1 Mouse2 Mouse3 Mouse4 Mouse5 RightBack RightForward Dpi
                               1 = firmware default action, 0 = custom entry (or disabled when no entry)
  assign   : 3d ac 00 00 { <key u16 = 0x0100+idx> 00 00 <action file u16> <u16 = 0x0061> }*
  action   : 6e 07 01 00 00 00 00 00 01 <HID usage>     keyboard key
  Bragi commands: 0x0B <id u16> create file, 0x0C <id u16> delete file, OPEN/WRITE/CLOSE as usual.
  Property 0x52 = handedness (0 right, 1 left). In left-handed mode the firmware mirrors the physical buttons:
  physical left pair = logical RightBack/RightForward, physical right pair = logical Mouse4/Mouse5, and swaps clicks.
  Files are (re)read only at power-up and on a mode switch.
"""
import json
import os
import struct
import subprocess
import sys
import time

import bragi

HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
CONFIG = os.path.join(HERE, "m75map.json")
BACKUPS = os.path.join(HERE, "backup")
KEYS = ["Mouse1", "Mouse2", "Mouse3", "Mouse4", "Mouse5", "MouseRightBack", "MouseRightForward", "MouseDpiToggle"]
SIDE = ["Mouse4", "Mouse5", "MouseRightBack", "MouseRightForward"]  # logical keys we manage
# physical buttons as the user sees them
PHYS = ["left_back", "left_front", "right_back", "right_front"]
LABEL = {"left_back": "Left back", "left_front": "Left front", "right_back": "Right back",
         "right_front": "Right front"}
DEFAULT_NAME = {"left_back": "Mouse Back", "left_front": "Mouse Forward", "right_back": "Mouse Back",
                "right_front": "Mouse Forward"}
# physical button -> logical key, per handedness
LOGICAL = {
    "right": {"left_back": "Mouse4", "left_front": "Mouse5", "right_back": "MouseRightBack",
              "right_front": "MouseRightForward"},
    "left": {"left_back": "MouseRightBack", "left_front": "MouseRightForward", "right_back": "Mouse4",
             "right_front": "Mouse5"},
}
BIND_TYPES = ("keyboard", "default", "unbound")
PROP_HAND = 0x52
PROP_DPI_INDEX = 0x1E
FILE_HANDLE = 1
FILE_RANGE = range(0x6D60, 0x6EF0)  # ids iCUE and this tool use for profile files
KBD_ENTRY_PARAM = 0x0061
KBD_ACTION = b"\x6e\x07\x01\x00\x00\x00\x00\x00\x01"

# (virtual-key, HID usage, name)
KEYTABLE = (
    [(0x41 + i, 0x04 + i, chr(0x41 + i)) for i in range(26)]
    + [(0x31 + i, 0x1E + i, str(i + 1)) for i in range(9)] + [(0x30, 0x27, "0")]
    + [(0x0D, 0x28, "Enter"), (0x09, 0x2B, "Tab"), (0x20, 0x2C, "Space"),
       (0xBD, 0x2D, "-"), (0xBB, 0x2E, "="), (0xDB, 0x2F, "["), (0xDD, 0x30, "]"), (0xDC, 0x31, "\\"),
       (0xBA, 0x33, ";"), (0xDE, 0x34, "'"), (0xC0, 0x35, "`"), (0xBC, 0x36, ","), (0xBE, 0x37, "."),
       (0xBF, 0x38, "/"), (0x14, 0x39, "CapsLock")]
    + [(0x70 + i, 0x3A + i, f"F{i + 1}") for i in range(12)]
    + [(0x2C, 0x46, "PrintScreen"), (0x91, 0x47, "ScrollLock"), (0x13, 0x48, "Pause"), (0x2D, 0x49, "Insert"),
       (0x24, 0x4A, "Home"), (0x21, 0x4B, "PageUp"), (0x23, 0x4D, "End"), (0x22, 0x4E, "PageDown"),
       (0x27, 0x4F, "Right"), (0x25, 0x50, "Left"), (0x28, 0x51, "Down"), (0x26, 0x52, "Up"),
       (0x90, 0x53, "NumLock"), (0x6F, 0x54, "Num /"), (0x6A, 0x55, "Num *"), (0x6D, 0x56, "Num -"),
       (0x6B, 0x57, "Num +")]
    + [(0x61 + i, 0x59 + i, f"Num {i + 1}") for i in range(9)] + [(0x60, 0x62, "Num 0"), (0x6E, 0x63, "Num .")]
    + [(0x5D, 0x65, "Menu")]
    + [(0x7C + i, 0x68 + i, f"F{i + 13}") for i in range(12)]
    + [(0xA2, 0xE0, "Left Ctrl"), (0xA0, 0xE1, "Left Shift"), (0xA4, 0xE2, "Left Alt"), (0x5B, 0xE3, "Left Win"),
       (0xA3, 0xE4, "Right Ctrl"), (0xA1, 0xE5, "Right Shift"), (0xA5, 0xE6, "Right Alt"), (0x5C, 0xE7, "Right Win")]
)
USAGE_NAME = {u: n for _vk, u, n in KEYTABLE}
VK_TO_USAGE = {vk: u for vk, u, _n in KEYTABLE}


class M75Error(Exception):
    pass


def icue_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq iCUE.exe", "/NH"], capture_output=True, text=True).stdout
    return "iCUE.exe" in out


def kill_icue():
    subprocess.run(["taskkill", "/F", "/IM", "iCUE.exe"], capture_output=True)
    for _ in range(20):
        if not icue_running():
            return
        time.sleep(0.25)


def close_icue():
    if icue_running():
        kill_icue()
        if icue_running():
            raise M75Error("could not close iCUE")


class Dev:
    def __init__(self):
        info = bragi.find_bragi()
        if not info:
            raise M75Error("M75 not found. Is the mouse plugged in?")
        self.b = bragi.Bragi(info)

    def close(self):
        self.b.close()

    # ---- files
    def read(self, res):
        if self.b.open_handle(FILE_HANDLE, res) != 0:
            return None
        try:
            return self.b.read_handle(FILE_HANDLE)
        finally:
            self.b.close_handle(FILE_HANDLE)

    def _put(self, res, data):
        if self.b.open_handle(FILE_HANDLE, res) != 0:
            raise M75Error(f"cannot open 0x{res:04X} for writing")
        try:
            err = self.b.write_handle(FILE_HANDLE, data)
        finally:
            self.b.close_handle(FILE_HANDLE)
        if err:
            raise M75Error(f"write to 0x{res:04X} failed, status 0x{err:02X}")
        back = self.read(res)
        if back != data:
            raise M75Error(f"verify failed for 0x{res:04X}: device has {back.hex(' ') if back else None}")

    def write(self, res, data, resize=False):
        """Overwrite an existing file and read it back. Returns False if it already held data."""
        cur = self.read(res)
        if cur is None:
            raise M75Error(f"file 0x{res:04X} not present on device")
        if len(cur) != len(data) and not resize:
            raise M75Error(f"file 0x{res:04X}: length {len(data)} != device length {len(cur)}")
        if cur == data:
            return False
        self._put(res, data)
        return True

    def exists(self, res):
        st = self.b.open_handle(FILE_HANDLE, res)
        if st == 0:
            self.b.close_handle(FILE_HANDLE)
        return st == 0

    def create(self, data):
        """Create a new file (first free id) holding data; returns its id."""
        for fid in FILE_RANGE[1:]:
            if not self.exists(fid):
                r = self.b.xfer(0x0B, fid & 0xFF, fid >> 8)
                if r[2]:
                    raise M75Error(f"create file 0x{fid:04X} failed, status 0x{r[2]:02X}")
                self._put(fid, data)
                return fid
        raise M75Error("no free file id on the mouse")

    def delete(self, fid):
        r = self.b.xfer(0x0C, fid & 0xFF, fid >> 8)
        return r[2] == 0

    # ---- properties
    def mode(self):
        return self.b.get_prop(bragi.PROP_MODE)[0]

    def hand_raw(self):
        return self.b.get_prop(PROP_HAND)[0]

    def hand(self):
        return "left" if self.hand_raw() == 1 else "right"

    def set_hand(self, hand):
        """Returns True if it changed."""
        want = 1 if hand == "left" else 0
        if self.hand_raw() == want:
            return False
        self.b.set_prop(PROP_HAND, want)
        if self.hand_raw() != want:
            raise M75Error("handedness did not change")
        return True

    def ensure_hw_mode(self):
        if self.mode() != bragi.MODE_HW:
            self.b.set_prop(bragi.PROP_MODE, bragi.MODE_HW)

    def reload(self):
        """The firmware only re-reads the profile at power-up and on a mode switch: bounce sw -> hw.
        The bounce resets the current DPI stage to the profile default, so put the user's stage back."""
        dpi = self.b.get_prop(PROP_DPI_INDEX)[0]
        self.b.set_prop(bragi.PROP_MODE, bragi.MODE_SW)
        time.sleep(0.4)
        self.b.set_prop(bragi.PROP_MODE, bragi.MODE_HW)
        time.sleep(0.4)
        if self.mode() != bragi.MODE_HW:
            raise M75Error("mouse did not return to hardware mode")
        if dpi is not None and self.b.get_prop(PROP_DPI_INDEX)[0] != dpi:
            self.b.set_prop(PROP_DPI_INDEX, dpi)


def layout(d):
    r0f = d.read(0x0F)
    if not r0f or len(r0f) < 6:
        raise M75Error("unexpected onboard layout (res 0x0F)")
    root_id = struct.unpack_from("<H", r0f, 4)[0]
    root = d.read(root_id)
    if not root or root[:2] != b"\x1e\xf1":
        raise M75Error(f"root file 0x{root_id:04X} has unexpected magic")
    assign_id, _dpi, _light, _refs, flags_id = struct.unpack_from("<5H", root, 6)
    flags = d.read(flags_id)
    assign = d.read(assign_id)
    if not flags or flags[:2] != b"\xaf\x0b" or len(flags) != 10:
        raise M75Error(f"flags file 0x{flags_id:04X} has unexpected format: {flags.hex(' ') if flags else None}")
    if not assign or assign[:2] != b"\x3d\xac":
        raise M75Error(f"assignment list 0x{assign_id:04X} has unexpected format")
    entries = {}
    for pos in range(4, len(assign) - 7, 8):
        key, _z, ref, par = struct.unpack_from("<HHHH", assign, pos)
        entries[key] = (ref, par)
    return dict(root_id=root_id, flags_id=flags_id, flags=flags, assign_id=assign_id, entries=entries,
                name=root[32:].split(b"\0")[0].decode("latin1"))


def is_kbd_action(data):
    return bool(data) and data[:3] == KBD_ACTION[:3] and len(data) == 10


def describe_action(data):
    if data is None:
        return "?"
    if data[:2] == b"\x6e\x07":
        t = data[2]
        if t == 1 and len(data) == 10:
            return f"key {USAGE_NAME.get(data[9], hex(data[9]))}"
        if t == 2:
            return f"mouse button {data[8]}"
        if t == 4:
            return "dpi"
    return data.hex(" ")


# ---- logical (per firmware key) read/write
def read_logical(d):
    L = layout(d)
    fl = L["flags"][2:]
    out = {}
    for k in SIDE:
        idx = KEYS.index(k)
        ent = L["entries"].get(0x0100 + idx)
        if ent:
            act = d.read(ent[0])
            if is_kbd_action(act):
                out[k] = {"type": "keyboard", "usage": act[9]}
            else:
                out[k] = {"type": "other", "info": describe_action(act)}
        else:
            out[k] = {"type": "default"} if fl[idx] == 1 else {"type": "unbound"}
    return out


def write_logical(d, want):
    """Rebuild the onboard assignment for the 4 side keys. Every other entry and flag is copied unchanged."""
    L = layout(d)
    entries = L["entries"]
    side_ids = {0x0100 + KEYS.index(k) for k in SIDE}
    kept = {key: v for key, v in entries.items() if key not in side_ids}
    kept_refs = {ref for ref, _p in kept.values()}
    old_side = {key: ref for key, (ref, _p) in entries.items() if key in side_ids}
    new_entries = dict(kept)
    used = set()
    for k in SIDE:
        b = want[k]
        if b["type"] != "keyboard":
            continue
        key = 0x0100 + KEYS.index(k)
        data = KBD_ACTION + bytes([b["usage"]])
        ref = old_side.get(key)
        if ref and ref not in used and ref not in kept_refs and is_kbd_action(d.read(ref)):
            d.write(ref, data)
        else:
            ref = d.create(data)
        used.add(ref)
        new_entries[key] = (ref, KBD_ENTRY_PARAM)
    flags = bytearray(L["flags"])
    for k in SIDE:
        flags[2 + KEYS.index(k)] = 1 if want[k]["type"] == "default" else 0
    lst = b"\x3d\xac\x00\x00" + b"".join(struct.pack("<HHHH", key, 0, ref, par)
                                         for key, (ref, par) in sorted(new_entries.items()))
    d.write(L["assign_id"], lst, resize=True)
    d.write(L["flags_id"], bytes(flags))
    still = {ref for ref, _p in new_entries.values()}
    for ref in set(old_side.values()) - still:
        d.delete(ref)
    got = read_logical(d)
    for k in SIDE:
        if (got[k]["type"], got[k].get("usage")) != (want[k]["type"], want[k].get("usage")):
            raise M75Error(f"verify failed for {k}: {got[k]}")


# ---- physical (what the user sees) view
def read_buttons(d, hand):
    lg = read_logical(d)
    return {p: lg[LOGICAL[hand][p]] for p in PHYS}


def write_buttons(d, hand, buttons):
    """Set handedness, write the binds for that handedness, and make the firmware reload them."""
    d.ensure_hw_mode()
    d.set_hand(hand)
    write_logical(d, {LOGICAL[hand][p]: buttons[p] for p in PHYS})
    d.reload()


# ---- config (by physical button)
def load_config():
    """Returns (hand, buttons) or None if there is no valid config."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        hand = cfg["hand"]
        buttons = {p: {k: v for k, v in cfg["buttons"][p].items() if k in ("type", "usage")} for p in PHYS}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if hand not in LOGICAL:
        return None
    for b in buttons.values():
        if b.get("type") not in BIND_TYPES or (b["type"] == "keyboard" and b.get("usage") not in USAGE_NAME):
            return None
    return hand, buttons


def save_config(hand, buttons):
    cfg = {"_comment": "buttons: type = keyboard (usage = HID usage id) | default (mouse Back/Forward) | unbound",
           "hand": hand, "buttons": {}}
    for p in PHYS:
        b = {k: v for k, v in buttons[p].items() if k in ("type", "usage")}
        if b["type"] == "keyboard":
            b["name"] = USAGE_NAME.get(b["usage"], hex(b["usage"]))
        cfg["buttons"][p] = b
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)


# ---- CLI
def cmd_status(d):
    L = layout(d)
    mode = "hardware" if d.mode() == bragi.MODE_HW else "software"
    print(f"profile : {L['name']!r}  root 0x{L['root_id']:04X}  mode={mode}")
    print(f"handed  : {d.hand()}")
    fl = L["flags"][2:]
    for i, k in enumerate(KEYS):
        ent = L["entries"].get(0x0100 + i)
        act = describe_action(d.read(ent[0])) if ent else "default"
        print(f"  {k:18s} flag={fl[i]}  action={act}")


def collect(d):
    """All onboard profile files: the active-profile pointer plus every file id in the profile-file range."""
    files = {0x0F: d.read(0x0F)}
    for i in FILE_RANGE:
        data = d.read(i)
        if data is not None:
            files[i] = data
    return files


def cmd_backup(d, name=None):
    name = name or time.strftime("m75_%Y%m%d_%H%M%S")
    out = os.path.join(BACKUPS, name)
    os.makedirs(out, exist_ok=True)
    files = collect(d)
    for i, data in files.items():
        with open(os.path.join(out, f"res_{i:04X}.bin"), "wb") as f:
            f.write(data)
    meta = {"handedness": d.hand_raw(), "mode": d.mode(), "files": [f"{i:04X}" for i in files]}
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    print(f"backup saved: {out}  ({len(files)} files)")
    return out


def latest_backup():
    cands = [os.path.join(BACKUPS, x) for x in os.listdir(BACKUPS)] if os.path.isdir(BACKUPS) else []
    cands = [c for c in cands if os.path.isfile(os.path.join(c, "meta.json"))]
    if not cands:
        raise M75Error("no backups found")
    return os.path.basename(max(cands, key=os.path.getmtime))


def cmd_apply(d):
    cfg = load_config()
    if not cfg:
        raise M75Error(f"no valid config at {CONFIG}")
    cmd_backup(d)
    write_buttons(d, *cfg)
    print("applied.")
    cmd_status(d)


def cmd_restore(d, name=None):
    name = name or latest_backup()
    src = os.path.join(BACKUPS, name)
    if not os.path.isfile(os.path.join(src, "meta.json")):
        raise M75Error(f"not a backup: {src}")
    with open(os.path.join(src, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    saved = {}
    for fn in os.listdir(src):
        if fn.startswith("res_") and fn.endswith(".bin"):
            with open(os.path.join(src, fn), "rb") as f:
                saved[int(fn[4:-4], 16)] = f.read()
    cur_ids = {i for i in FILE_RANGE if d.exists(i)}
    if d.read(0x0F) != saved.get(0x0F) or cur_ids != {i for i in saved if i in FILE_RANGE}:
        raise M75Error("onboard file layout changed since this backup. Refusing to restore over it; "
                       "use iCUE's hardware profile reset/save instead.")
    n = 0
    for i, data in saved.items():
        if i in FILE_RANGE and d.write(i, data, resize=True):
            n += 1
            print(f"restored file 0x{i:04X}")
    d.set_hand("left" if meta["handedness"] else "right")
    d.reload()
    print(f"restore from {name} complete ({n} files changed)")
    cmd_status(d)


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    arg = a[1] if len(a) > 1 else None
    cmds = {"status": cmd_status, "backup": lambda d: cmd_backup(d, arg),
            "apply": cmd_apply, "restore": lambda d: cmd_restore(d, arg)}
    if cmd not in cmds:
        print(__doc__)
        return 1
    try:
        if icue_running():
            raise M75Error("iCUE is running. Quit it first (tray icon -> Quit).")
        d = Dev()
        try:
            cmds[cmd](d)
        finally:
            d.close()
    except M75Error as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
