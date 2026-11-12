"""Tastiera e mouse tramite il driver TitanCSA Interceptor-Keyboard-Mouse.

https://github.com/TitanCSA/Interceptor-Keyboard-Mouse

L'SDK è in C++: un piccolo processo (titan/titan_helper.exe, vedi titan/build.ps1) tiene aperto il
driver e riceve da qui i comandi, una riga per volta. Tasti = codici di scansione, mouse = movimenti
RELATIVI. Non esiste nessun altro canale di input nel progetto.

Requisiti: driver installato con InterceptorSetup.exe e bot lanciato come AMMINISTRATORE.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("bot")

LINK = "https://github.com/TitanCSA/Interceptor-Keyboard-Mouse/releases"
HELPER = Path(__file__).resolve().parent / "titan" / "titan_helper.exe"


class DriverMancante(RuntimeError):
    """Il driver Titan non è installato, non risponde o manca l'helper."""


# Codici di scansione "set 1" dei tasti fisici: non dipendono dal layout (italiano, US...).
# (codice, esteso_E0)
_SCAN: dict[str, tuple[int, bool]] = {
    "esc": (0x01, False), "1": (0x02, False), "2": (0x03, False), "3": (0x04, False),
    "4": (0x05, False), "5": (0x06, False), "6": (0x07, False), "7": (0x08, False),
    "8": (0x09, False), "9": (0x0A, False), "0": (0x0B, False), "backspace": (0x0E, False),
    "tab": (0x0F, False), "q": (0x10, False), "w": (0x11, False), "e": (0x12, False),
    "r": (0x13, False), "t": (0x14, False), "y": (0x15, False), "u": (0x16, False),
    "i": (0x17, False), "o": (0x18, False), "p": (0x19, False), "enter": (0x1C, False),
    "ctrl": (0x1D, False), "a": (0x1E, False), "s": (0x1F, False), "d": (0x20, False),
    "f": (0x21, False), "g": (0x22, False), "h": (0x23, False), "j": (0x24, False),
    "k": (0x25, False), "l": (0x26, False), "shift": (0x2A, False), "z": (0x2C, False),
    "x": (0x2D, False), "c": (0x2E, False), "v": (0x2F, False), "b": (0x30, False),
    "n": (0x31, False), "m": (0x32, False), "rshift": (0x36, False), "alt": (0x38, False),
    "space": (0x39, False), "capslock": (0x3A, False),
    "f1": (0x3B, False), "f2": (0x3C, False), "f3": (0x3D, False), "f4": (0x3E, False),
    "f5": (0x3F, False), "f6": (0x40, False), "f7": (0x41, False), "f8": (0x42, False),
    "f9": (0x43, False), "f10": (0x44, False), "f11": (0x57, False), "f12": (0x58, False),
    "up": (0x48, True), "down": (0x50, True), "left": (0x4B, True), "right": (0x4D, True),
    "home": (0x47, True), "end": (0x4F, True), "pageup": (0x49, True), "pagedown": (0x51, True),
    "insert": (0x52, True), "delete": (0x53, True), "rctrl": (0x1D, True), "ralt": (0x38, True),
}
_ALIAS = {
    "escape": "esc", "return": "enter", "spacebar": "space", " ": "space", "lshift": "shift",
    "lctrl": "ctrl", "control": "ctrl", "lalt": "alt", "del": "delete", "ins": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "\t": "tab", "\n": "enter",
}
_BUTTONS = {  # (giù, su) – flag dei pulsanti del mouse (stessi valori di RAWMOUSE)
    "left": (0x001, 0x002), "right": (0x004, 0x008), "middle": (0x010, 0x020),
    "mouse4": (0x040, 0x080), "mouse5": (0x100, 0x200),
}


def resolve_key(name: str, cfg: dict | None = None) -> tuple[int, bool, bool]:
    """Nome del tasto (o singolo carattere) → (codice di scansione, E0, serve shift)."""
    aliases = {str(k).lower(): str(v) for k, v in ((cfg or {}).get("keys") or {}).items()}
    raw = str(name)
    key = aliases.get(raw.lower(), raw)
    if len(key) == 1 and key.isalpha() and key.lower() in _SCAN:   # lettera (maiuscola = con shift)
        code, ext = _SCAN[key.lower()]
        return code, ext, key.isupper()
    low = _ALIAS.get(key if len(key) == 1 else key.lower(), key.lower())
    if low in _SCAN:
        code, ext = _SCAN[low]
        return code, ext, False
    if len(key) == 1:
        try:  # altri caratteri: dipendono dal layout di tastiera di Windows
            u = ctypes.windll.user32
            res = u.VkKeyScanW(ord(key))
            if res != -1:
                vk, mods = res & 0xFF, (res >> 8) & 0xFF
                scan = u.MapVirtualKeyW(vk, 0)          # MAPVK_VK_TO_VSC
                if scan > 0 and not (mods & 0b110):     # niente ctrl/alt
                    return scan & 0xFF, False, bool(mods & 1)
        except Exception:
            pass
    raise KeyError(f"tasto sconosciuto: {name!r}")


def resolve_button(name: str) -> tuple[int, int]:
    try:
        return _BUTTONS[str(name).lower()]
    except KeyError:
        raise KeyError(f"pulsante del mouse sconosciuto: {name!r} (usa {', '.join(_BUTTONS)})") from None


def cursor_pos() -> tuple[int, int]:
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class TitanInput:
    """Invia tasti e mouse attraverso titan_helper.exe (driver TitanCSA).

    cfg: `keyboard` / `mouse` = id del dispositivo secondo il driver (null = il primo trovato).
    """

    def __init__(self, cfg: dict | None):
        self.cfg = cfg or {}
        if not HELPER.exists():
            raise DriverMancante(
                f"Manca {HELPER.name}: compilalo con  powershell -File titan\\build.ps1  "
                "(serve Visual Studio Build Tools con C++).")
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise DriverMancante("Il driver Titan richiede privilegi di amministratore: "
                                 "avvia il terminale / avvia_bot.bat come amministratore.")
        kb = self.cfg.get("keyboard")
        ms = self.cfg.get("mouse")
        try:
            self._proc = subprocess.Popen(
                [str(HELPER), str(-1 if kb is None else int(kb)), str(-1 if ms is None else int(ms))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            raise DriverMancante(f"Impossibile avviare {HELPER.name}: {e}") from e
        self._lock = threading.Lock()
        self._down: set[tuple[int, bool]] = set()

        first = (self._proc.stdout.readline() or "").strip()
        if not first.startswith("READY"):
            self._kill()
            msg = first[6:] if first.startswith("FATAL") else "l'helper si è chiuso senza rispondere"
            raise DriverMancante(
                f"Driver Titan non utilizzabile: {msg}. Installa/ripara il driver con "
                f"InterceptorSetup.exe ({LINK}), riavvia il PC se richiesto.")
        _, kb_id, ms_id = first.split()
        log.info("🎛  Titan Interceptor: tastiera id %s, mouse id %s", kb_id, ms_id)

    # ------------------------------------------------------------------ canale
    def _cmd(self, line: str) -> None:
        with self._lock:
            try:
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
                resp = (self._proc.stdout.readline() or "").strip()
            except (OSError, ValueError) as e:
                raise DriverMancante(f"helper Titan non raggiungibile: {e}") from e
        if resp != "OK":
            if not resp:
                raise DriverMancante("helper Titan terminato inaspettatamente")
            log.warning("Titan: comando %r rifiutato (%s)", line.split()[0], resp)

    # ---------------------------------------------------------------- tastiera
    def _key(self, code: int, ext: bool, up: bool) -> None:
        self._cmd(f"K {code} {int(ext)} {int(up)}")

    def key_down(self, name: str) -> None:
        code, ext, shift = resolve_key(name, self.cfg)
        if shift:
            self._key(*_SCAN["shift"], up=False)
        self._key(code, ext, up=False)
        self._down.add((code, ext))

    def key_up(self, name: str) -> None:
        code, ext, shift = resolve_key(name, self.cfg)
        self._key(code, ext, up=True)
        self._down.discard((code, ext))
        if shift:
            self._key(*_SCAN["shift"], up=True)

    # ------------------------------------------------------------------- mouse
    def move_relative(self, dx: int, dy: int) -> None:
        """Un "report" del mouse: spostamento relativo in conteggi del sensore."""
        if dx or dy:
            self._cmd(f"M {int(dx)} {int(dy)}")

    def move_absolute(self, x: int, y: int) -> None:
        """Solo per cursor_mode: absolute (giochi dove i movimenti relativi non muovono il cursore)."""
        u = ctypes.windll.user32
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        vw, vh = max(1, u.GetSystemMetrics(78) - 1), max(1, u.GetSystemMetrics(79) - 1)
        nx = int(round((x - vx) * 65535 / vw))
        ny = int(round((y - vy) * 65535 / vh))
        self._cmd(f"A {nx} {ny}")

    def mouse_down(self, btn: str) -> None:
        self._cmd(f"B {resolve_button(btn)[0]}")

    def mouse_up(self, btn: str) -> None:
        self._cmd(f"B {resolve_button(btn)[1]}")

    def _kill(self) -> None:
        try:
            self._proc.kill()
        except Exception:
            pass

    def close(self) -> None:
        for code, ext in list(self._down):
            try:
                self._key(code, ext, up=True)
            except Exception:
                pass
        self._down.clear()
        try:
            self._cmd("Q")
            self._proc.wait(timeout=2)
        except Exception:
            self._kill()
