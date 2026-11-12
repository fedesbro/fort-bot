"""Controlla quanto ogni template viene riconosciuto sullo schermo attuale (o su uno screenshot).

Uso:
    python prova_template.py                      cattura lo schermo tra 3 secondi
    python prova_template.py --da-file shot.png   usa uno screenshot salvato
    python prova_template.py --mostra             apre un'immagine con i riquadri trovati
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

from vision import Screen, Templates, find, make_dpi_aware

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--da-file")
    ap.add_argument("--mostra", action="store_true")
    ap.add_argument("--attesa", type=float, default=3)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    make_dpi_aware()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    conf = float(cfg.get("confidence", 0.85))
    screen = Screen(cfg.get("monitor", 1), cfg.get("region"))
    folder = ROOT / cfg.get("templates_folder", "templates")
    tpls = Templates(folder)

    if args.da_file:
        frame = cv2.imdecode(np.fromfile(args.da_file, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            sys.exit(f"Impossibile leggere {args.da_file}")
        screen.area = {"left": 0, "top": 0, "width": frame.shape[1], "height": frame.shape[0]}
    else:
        for i in range(int(args.attesa), 0, -1):
            print(f"Cattura tra {i}...")
            time.sleep(1)
        frame = screen.grab()

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    files = sorted(folder.glob("*.png"))
    if not files:
        sys.exit(f"Nessun template in {folder}")

    print(f"\n{'template':<30} {'somiglianza':>11}   posizione")
    print("-" * 60)
    for f in files:
        m = find(gray, tpls.get(f.name), f.name, screen, cfg.get("scales") or [1.0])
        ok = m.score >= conf
        print(f"{f.name:<30} {m.score*100:>10.1f}%   ({m.x}, {m.y})  {'✅ TROVATO' if ok else ''}")
        if args.mostra and ok:
            x0 = m.x - screen.area["left"] - m.w // 2
            y0 = m.y - screen.area["top"] - m.h // 2
            cv2.rectangle(frame, (x0, y0), (x0 + m.w, y0 + m.h), (0, 255, 0), 3)
            cv2.putText(frame, f.stem, (x0, max(y0 - 8, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    print(f"\nSoglia attuale (confidence): {conf*100:.0f}%")

    if args.mostra:
        h, w = frame.shape[:2]
        s = min(1600 / w, 900 / h, 1.0)
        cv2.imshow("Risultato (premi un tasto per chiudere)", cv2.resize(frame, None, fx=s, fy=s))
        cv2.waitKey(0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
