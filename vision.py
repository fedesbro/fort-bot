"""Cattura schermo e riconoscimento immagini (template matching con OpenCV)."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path

import cv2
import mss
import numpy as np


def make_dpi_aware() -> None:
    """Allinea le coordinate di cattura e clic quando Windows ha lo scaling (125%, 150%...)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


@dataclass
class Match:
    name: str
    score: float
    x: int  # centro, coordinate assolute dello schermo
    y: int
    w: int
    h: int


class Screen:
    def __init__(self, monitor: int = 1, region: list[int] | None = None):
        self._sct = mss.mss()
        mon = self._sct.monitors[monitor]
        if region:  # [x, y, larghezza, altezza] relativi al monitor
            rx, ry, rw, rh = region
            self.area = {"left": mon["left"] + rx, "top": mon["top"] + ry, "width": rw, "height": rh}
        else:
            self.area = {"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]}

    def grab(self) -> np.ndarray:
        """Screenshot in BGR (formato OpenCV)."""
        img = np.array(self._sct.grab(self.area))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    def to_abs(self, x: int, y: int) -> tuple[int, int]:
        return self.area["left"] + x, self.area["top"] + y


class Templates:
    def __init__(self, folder: Path):
        self.folder = folder
        self._cache: dict[str, np.ndarray] = {}

    def get(self, name: str) -> np.ndarray:
        if name not in self._cache:
            path = self.folder / name
            if not path.exists():
                raise FileNotFoundError(f"Template mancante: {path}")
            # imdecode gestisce anche percorsi con spazi/accenti su Windows
            img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise ValueError(f"Immagine non leggibile: {path}")
            if img.std() < 8:
                # un ritaglio quasi tutto di un colore "combacia" con qualsiasi zona uniforme
                raise ValueError(f"Template troppo uniforme (quasi un solo colore): {path}. "
                                 "Ritaglia una zona con scritte/dettagli.")
            self._cache[name] = img
        return self._cache[name]

    def exists(self, name: str) -> bool:
        return (self.folder / name).exists()


def find(frame_gray: np.ndarray, tpl: np.ndarray, name: str, screen: Screen,
         scales: list[float] | None = None) -> Match:
    """Cerca il template nel frame; restituisce il risultato migliore (anche se basso)."""
    best = Match(name, -1.0, 0, 0, 0, 0)
    for s in scales or [1.0]:
        t = tpl if s == 1.0 else cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        th, tw = t.shape[:2]
        if th > frame_gray.shape[0] or tw > frame_gray.shape[1] or th < 4 or tw < 4:
            continue
        res = cv2.matchTemplate(frame_gray, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best.score:
            ax, ay = screen.to_abs(loc[0] + tw // 2, loc[1] + th // 2)
            best = Match(name, float(score), ax, ay, tw, th)
    return best


def save_image(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", img)
    if ok:
        buf.tofile(str(path))
