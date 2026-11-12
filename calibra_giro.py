"""Taratura del cerchio: quanti conteggi del mouse servono per un giro completo (360°) della visuale.

Uso (in partita, personaggio fermo, guardando un punto di riferimento):
    python calibra_giro.py               prova il valore di config.yaml (movement.mouse_counts_per_360)
    python calibra_giro.py 2400          prova 2400 conteggi

Dopo 5 secondi la visuale gira lentamente del numero di conteggi indicato:
- se torna esattamente sul punto di partenza, il valore è giusto;
- se si ferma PRIMA del punto, il valore è troppo basso; se lo SUPERA, è troppo alto.
Scrivi il valore giusto in config.yaml → movement.mouse_counts_per_360.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

from input_titan import DriverMancante, TitanInput

ROOT = Path(__file__).resolve().parent


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    counts = int(sys.argv[1]) if len(sys.argv) > 1 else int((cfg.get("movement") or {}).get("mouse_counts_per_360", 2000))
    try:
        inp = TitanInput(cfg.get("titan"))
    except DriverMancante as e:
        sys.exit(str(e))
    for i in range(5, 0, -1):
        print(f"Giro di {counts} conteggi tra {i}... (porta in primo piano il gioco)")
        time.sleep(1)
    secs, hz = 4.0, 500
    n = int(secs * hz)
    sent = 0
    for i in range(1, n + 1):
        target = round(counts * i / n)
        inp.move_relative(target - sent, 0)
        sent = target
        time.sleep(1 / hz)
    inp.close()
    print(f"Fatto: inviati {sent} conteggi. Torna sul punto di partenza? (prima = alza il valore, oltre = abbassalo)")


if __name__ == "__main__":
    main()
