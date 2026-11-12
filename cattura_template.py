"""Crea un template ritagliando una parte dello schermo (o di uno screenshot già salvato).

Uso:
    python cattura_template.py lobby                     cattura lo schermo tra 3 secondi
    python cattura_template.py lobby --da-file shot.png  ritaglia da uno screenshot esistente

Si apre una finestra: trascina un rettangolo attorno al bottone/scritta e premi INVIO
(o SPAZIO). C per annullare. Il ritaglio viene salvato in templates/<nome>.png
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from vision import Screen, make_dpi_aware, save_image

ROOT = Path(__file__).resolve().parent
MAX_W, MAX_H = 1600, 900


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("nome", help="nome del template (senza .png)")
    ap.add_argument("--da-file", help="screenshot da cui ritagliare")
    ap.add_argument("--monitor", type=int, default=1)
    ap.add_argument("--attesa", type=float, default=3, help="secondi prima della cattura")
    args = ap.parse_args()
    make_dpi_aware()

    if args.da_file:
        img = cv2.imdecode(np.fromfile(args.da_file, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            sys.exit(f"Impossibile leggere {args.da_file}")
    else:
        for i in range(int(args.attesa), 0, -1):
            print(f"Cattura tra {i}... (vai sulla finestra del gioco)")
            time.sleep(1)
        img = Screen(args.monitor).grab()

    h, w = img.shape[:2]
    scale = min(MAX_W / w, MAX_H / h, 1.0)
    view = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img

    title = "Seleziona l'area e premi INVIO (C = annulla)"
    x, y, rw, rh = cv2.selectROI(title, view, showCrosshair=True)
    cv2.destroyAllWindows()
    if rw == 0 or rh == 0:
        sys.exit("Annullato.")

    x, y, rw, rh = (int(round(v / scale)) for v in (x, y, rw, rh))
    crop = img[y:y + rh, x:x + rw]
    if cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).std() < 8:
        sys.exit("Area troppo uniforme (quasi un solo colore): verrebbe trovata ovunque. "
                 "Seleziona una zona con scritte o dettagli.")
    name = args.nome if args.nome.lower().endswith(".png") else args.nome + ".png"
    out = ROOT / "templates" / name
    save_image(out, crop)
    print(f"Salvato {out.relative_to(ROOT)}  ({rw}x{rh} px, posizione {x},{y})")


if __name__ == "__main__":
    main()
