"""Movimenti e tempi "umani" a livello di input.

- tempi (reazione, durata dei tasti, pause) con distribuzione asimmetrica come quella reale:
  quasi sempre vicini al valore tipico, ogni tanto un po' più lenti, mai tutti uguali;
- traiettorie del mouse curve, con profilo di velocità a campana e qualche correzione;
- rotazione della visuale fatta a "passate" della mano (TurnHand), come chi gira il mouse.

Ogni valore numerico in config.yaml può essere:
    2.0          → numero fisso, a cui si applica la variazione casuale `jitter` (es. ±15%)
    [1.5, 2.5]   → range: estratto a caso ogni volta tra min e max
"""
from __future__ import annotations

import math
import random


def _rand_range(v) -> float:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = float(v[0]), float(v[1])
        return random.uniform(min(lo, hi), max(lo, hi))
    return float(v)


def _human_time(v) -> float:
    """Tempo umano nel range [lo, hi]: distribuzione triangolare con il picco al 30% del range
    (i tempi umani hanno la "coda" verso i valori lenti, non sono uniformi)."""
    if isinstance(v, (list, tuple)) and len(v) == 2:
        lo, hi = sorted((float(v[0]), float(v[1])))
        return random.triangular(lo, hi, lo + (hi - lo) * 0.3)
    return float(v)


def min_jerk(t: float) -> float:
    """Posizione lungo un movimento umano (0→1): parte piano, accelera, rallenta all'arrivo."""
    t = min(1.0, max(0.0, t))
    return 10 * t**3 - 15 * t**4 + 6 * t**5


class Human:
    def __init__(self, cfg: dict | None):
        c = cfg or {}
        self.enabled = bool(c.get("enabled", True))
        self.jitter = float(c.get("jitter", 0.15))               # ±15% sui valori fissi
        self.reaction = c.get("reaction", [0.18, 0.55])          # pausa prima di agire
        self.click_hold = c.get("click_hold", [0.065, 0.14])     # durata del clic
        self.double_click_gap = c.get("double_click_gap", [0.08, 0.16])
        self.key_hold = c.get("key_hold", [0.06, 0.13])          # durata di un tasto premuto
        self.between_keys = c.get("between_keys", [0.03, 0.14])  # tra i tasti di una combinazione
        self.typing = c.get("typing", [0.07, 0.22])              # tra un carattere e l'altro
        self.click_spread = float(c.get("click_spread", 0.3))    # quanto il clic si sposta dal centro
        self.point_spread = float(c.get("point_spread", 3))      # px per i clic su coordinate fisse
        self.speed = c.get("mouse_speed", [900, 1700])           # velocità media del mouse (px/s)
        self.curve = float(c.get("curve", 0.2))                  # quanto è curva la traiettoria
        self.overshoot_chance = float(c.get("overshoot_chance", 0.25))
        self.mouse_hz = int(c.get("mouse_hz", 500))              # frequenza dei report del mouse

    # ------------------------------------------------------------ valori/tempi
    def val(self, v, jitter: bool = True) -> float:
        """Range → valore casuale; numero fisso → numero ± jitter (se l'umanizzazione è attiva)."""
        if not self.enabled:
            return float(v[0] + v[1]) / 2 if isinstance(v, (list, tuple)) else float(v)
        if isinstance(v, (list, tuple)):
            return _rand_range(v)
        x = float(v)
        if jitter and x:
            x *= 1 + random.uniform(-self.jitter, self.jitter)
        return x

    def t(self, rng) -> float:
        """Tempo umano da uno dei range interni (0 se umanizzazione spenta)."""
        return _human_time(rng) if self.enabled else 0.0

    def tiny(self, default: float) -> float:
        return _human_time([default * 0.7, default * 1.6]) if self.enabled else default

    # ---------------------------------------------------------------- clic
    def click_point(self, x: float, y: float, w: int = 0, h: int = 0) -> tuple[int, int]:
        """Punto di clic vicino al centro ma mai identico (distribuzione gaussiana, dentro al bottone)."""
        if not self.enabled:
            return int(x), int(y)
        if w and h:
            sx, sy = w * self.click_spread / 2, h * self.click_spread / 2
            dx = max(-w * 0.4, min(w * 0.4, random.gauss(0, sx / 1.5)))
            dy = max(-h * 0.4, min(h * 0.4, random.gauss(0, sy / 1.5)))
        else:
            dx = random.gauss(0, self.point_spread / 1.5)
            dy = random.gauss(0, self.point_spread / 1.5)
        return int(round(x + dx)), int(round(y + dy))

    # ---------------------------------------------------- traiettorie del mouse
    def path(self, x0: float, y0: float, x1: float, y1: float) -> list[tuple[int, int, float]]:
        """Punti (x, y, durata_del_tratto) da (x0,y0) a (x1,y1): curva, velocità a campana,
        ogni tanto supera un po' il bersaglio e corregge."""
        if not self.enabled:
            return [(int(x1), int(y1), 0.0)]
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist < 2:
            return [(int(x1), int(y1), 0.0)]

        pts: list[tuple[int, int, float]] = []
        if dist > 120 and random.random() < self.overshoot_chance:
            ux, uy = (x1 - x0) / dist, (y1 - y0) / dist
            over = random.uniform(4, min(18, dist * 0.06))
            side = random.uniform(-4, 4)
            ox, oy = x1 + ux * over - uy * side, y1 + uy * over + ux * side
            pts += self._segment(x0, y0, ox, oy, dist)
            pts.append((pts[-1][0], pts[-1][1], random.uniform(0.04, 0.12)))  # si accorge e si ferma
            pts += self._segment(ox, oy, x1, y1, math.hypot(x1 - ox, y1 - oy), correction=True)
        else:
            pts += self._segment(x0, y0, x1, y1, dist)
        return pts

    def _segment(self, x0, y0, x1, y1, dist, correction: bool = False):
        if correction:
            duration = random.uniform(0.08, 0.18)
        else:
            # legge di Fitts semplificata: i movimenti lunghi durano di più, ma non in proporzione
            duration = 0.08 + dist / _rand_range(self.speed) + random.uniform(0, 0.08)
            duration = max(0.12, min(duration, 1.3))
        steps = max(6, int(duration / random.uniform(0.007, 0.011)))

        # due punti di controllo spostati di lato → curva di Bézier cubica
        nx, ny = -(y1 - y0), (x1 - x0)
        norm = math.hypot(nx, ny) or 1.0
        nx, ny = nx / norm, ny / norm
        bend = dist * self.curve * (0.3 if correction else 1.0)
        c1 = random.uniform(-bend, bend)
        c2 = random.uniform(-bend, bend) * 0.6
        p1 = (x0 + (x1 - x0) * random.uniform(0.2, 0.4) + nx * c1,
              y0 + (y1 - y0) * random.uniform(0.2, 0.4) + ny * c1)
        p2 = (x0 + (x1 - x0) * random.uniform(0.6, 0.85) + nx * c2,
              y0 + (y1 - y0) * random.uniform(0.6, 0.85) + ny * c2)
        skew = random.uniform(0.8, 1.15)   # picco di velocità un po' prima o un po' dopo la metà

        out = []
        prev = (round(x0), round(y0))
        dt = duration / steps
        for i in range(1, steps + 1):
            s = min_jerk((i / steps) ** skew)
            u = 1 - s
            bx = u**3 * x0 + 3 * u**2 * s * p1[0] + 3 * u * s**2 * p2[0] + s**3 * x1
            by = u**3 * y0 + 3 * u**2 * s * p1[1] + 3 * u * s**2 * p2[1] + s**3 * y1
            if i < steps:  # leggero tremolio della mano, mai sull'ultimo punto
                bx += random.gauss(0, 0.35)
                by += random.gauss(0, 0.35)
            p = (round(bx), round(by))
            step_dt = dt * random.uniform(0.85, 1.15)
            if p != prev or i == steps:
                out.append((p[0], p[1], step_dt))
                prev = p
            elif out:  # stesso pixel: accumula il tempo nel tratto precedente
                x, y, d = out[-1]
                out[-1] = (x, y, d + step_dt)
        return out


# guadagni della correzione "a occhio" (tarati in simulazione)
K_RADIAL = 1.1      # quanto punta verso l'interno/esterno per errore di raggio
K_HEADING = 2.2     # quanto in fretta riallinea la direzione (1/s)
K_ADAPT = 0.45      # quanto la passata in corso si adegua a ogni sguardo


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class CircleNav:
    """Dove dovrebbe trovarsi il personaggio rispetto al centro del cerchio della partita.

    Il centro viene fissato all'inizio della partita; poi, sapendo quanto ha girato la visuale
    (conteggi del mouse inviati) e a che velocità si muove il personaggio, la posizione viene
    stimata passo per passo. Così la mano può correggere come farebbe chi guarda dove va: se
    sta "uscendo" dal cerchio stringe un po', se sta entrando allarga. Senza questa correzione
    le piccole irregolarità della mano farebbero scivolare il cerchio sulla mappa.
    """

    def __init__(self, radius: float, sign: int, counts_per_360: float):
        self.r = float(radius)
        self.sign = 1 if sign >= 0 else -1
        self.cpr = float(counts_per_360) / (2 * math.pi)   # conteggi per radiante
        self.x = self.y = self.th = 0.0
        self.cx = -self.sign * self.r * math.sin(self.th)
        self.cy = self.sign * self.r * math.cos(self.th)

    def advance(self, counts_x: int, speed: float, dt: float) -> None:
        self.th += counts_x / self.cpr
        self.x += speed * math.cos(self.th) * dt
        self.y += speed * math.sin(self.th) * dt

    def omega(self, speed: float) -> float:
        """Velocità angolare (rad/s, con segno) da tenere adesso per restare sul cerchio."""
        if speed <= 0:
            return 0.0
        dx, dy = self.x - self.cx, self.y - self.cy
        dist = math.hypot(dx, dy) or self.r
        e = max(-0.6, min(0.6, (dist - self.r) / self.r))       # >0 = troppo all'esterno
        want = math.atan2(dy, dx) + self.sign * (math.pi / 2 + K_RADIAL * e)
        err = _wrap(want - self.th)
        base = speed / self.r
        w = self.sign * base + K_HEADING * err
        mag = min(2.6 * base, max(0.3 * base, self.sign * w))  # mai al contrario, mai a scatti
        return self.sign * mag


class TurnHand:
    """La mano che gira la visuale mentre il personaggio corre in tondo.

    Una persona non gira il mouse a velocità costante: fa delle "passate" (il polso spinge il
    mouse per mezzo secondo circa, con velocità a campana), si ferma un istante, riprende; ogni
    tanto riprende la presa e la pausa è più lunga. In verticale la mano non è mai perfettamente
    orizzontale: ogni passata sale o scende un po' e descrive un piccolo arco, e chi gioca tiene
    la visuale più o meno all'orizzonte (le derive verticali vengono corrette nelle passate dopo).

    L'ampiezza di ogni passata viene decisa all'inizio della passata guardando la posizione
    stimata (CircleNav): è così che il cerchio resta dentro il diametro massimo.

    `tick(dt)` viene chiamata a ogni report del mouse (es. 500 al secondo) e restituisce lo
    spostamento intero (dx, dy) di quel report; (0, 0) = nessun report, come un mouse fermo.
    """

    def __init__(self, nav: CircleNav, speed: float, enabled: bool = True, accel_tau: float = 0.1):
        self.nav = nav
        self.target = float(speed)          # velocità a cui il personaggio sta andando (m/s)
        self.v = 0.0                        # velocità stimata adesso (parte da fermo)
        self.tau = max(0.01, float(accel_tau))   # tempo di accelerazione/frenata del personaggio
        self.enabled = enabled
        self.t = 0.0
        self.mult = random.uniform(0.96, 1.04)     # variazione lenta dell'ampiezza delle passate
        self.pitch = 0.0                    # somma dei movimenti verticali inviati
        self.acc_x = self.acc_y = 0.0       # frazioni di conteggio non ancora inviate
        self.stroke: dict | None = None
        self.next_at = random.uniform(0.03, 0.12)   # prima le dita sui tasti, poi la mano
        self.trem_f = random.uniform(8.0, 11.5)       # tremore fisiologico della mano (Hz)
        self.trem_a = random.uniform(0.08, 0.25)      # ampiezza in conteggi
        self.trem_ph = random.uniform(0, 2 * math.pi)

    def set_speed(self, speed: float) -> None:
        """Cambio di andatura (corsa ↔ camminata, oppure 0 = fermo): il personaggio ci arriva
        in modo graduale e la mano adegua la rotazione."""
        self.target = float(speed)

    def _v(self) -> float:
        return self.v

    def _steer_speed(self) -> float:
        return 0.0 if self.target <= 0 else max(self.v, 0.5 * self.target)

    # ------------------------------------------------------------------
    def _gap(self) -> float:
        r = random.random()
        if r < 0.75:
            g = random.uniform(0.008, 0.055)          # quasi senza pausa
        elif r < 0.96:
            g = random.uniform(0.055, 0.12)           # breve esitazione
        else:
            g = random.uniform(0.12, 0.22)            # riprende la presa del mouse
        # su un cerchio stretto chi gioca non può fermare la mano a lungo: le pause si accorciano
        base = self.target / self.nav.r if self.target > 0 else 0.0
        return g * min(1.0, 0.9 / base) if base else g

    def _start_stroke(self) -> None:
        base = self.target / self.nav.r if self.target > 0 else 0.0
        med = 0.45 * (min(1.0, 1.2 / base) if base else 1.0)   # curva stretta → passate più corte
        dur = min(0.85, max(0.16, random.lognormvariate(math.log(max(med, 0.2)), 0.3)))
        gap_mean = 0.05
        w = abs(self.nav.omega(self._steer_speed()))
        bias = random.uniform(0.92, 1.08)
        amount = w * self.nav.cpr * (dur + gap_mean) * self.mult * bias
        slope = random.gauss(0, 0.028)                # la passata sale o scende un po'
        self.stroke = {
            "start": self.t, "dur": dur, "ax": amount, "win": dur + gap_mean, "bias": bias,
            "look_at": self.t + random.uniform(0.12, 0.2),   # l'occhio corregge con un po' di ritardo
            "ay": amount * slope - 0.4 * self.pitch,  # e riporta la visuale verso l'orizzonte
            "arc": amount * random.gauss(0, 0.02),    # arco del polso (torna a zero a fine passata)
            "skew": random.uniform(0.8, 1.2),
            "pos": 0.0, "bump": 0.0,
        }

    def tick(self, dt: float) -> tuple[int, int]:
        self.t += dt
        self.v += (self.target - self.v) * min(1.0, dt / self.tau)
        sign = self.nav.sign
        if not self.enabled:   # rotazione continua senza umanizzazione (solo per prove)
            self.acc_x += self.nav.omega(self._v()) * self.nav.cpr * dt
            ix = int(self.acc_x)
            self.acc_x -= ix
            self.nav.advance(ix, self._v(), dt)
            return ix, 0

        self.mult += -(self.mult - 1.0) * 0.6 * dt + random.gauss(0, 0.04) * math.sqrt(dt)
        self.mult = min(1.1, max(0.9, self.mult))

        fx = fy = 0.0
        if self.stroke is None and self.t >= self.next_at:
            self._start_stroke()
        s = self.stroke
        if s is not None:
            if self.t >= s["look_at"]:
                # a passata in corso l'occhio vede dove sta andando e la mano allarga o stringe
                w = abs(self.nav.omega(self._steer_speed()))
                want = w * self.nav.cpr * s["win"] * self.mult * s["bias"]
                s["ax"] += K_ADAPT * (want - s["ax"])
                s["look_at"] = self.t + random.uniform(0.06, 0.12)
            tau = min(1.0, (self.t - s["start"]) / s["dur"])
            pos = min_jerk(tau ** s["skew"])
            bump = math.sin(math.pi * tau)
            d = pos - s["pos"]
            fx = s["ax"] * d
            fy = s["ay"] * d + s["arc"] * (bump - s["bump"])
            # tremore: solo mentre la mano spinge il mouse (da fermo il sensore non lo vede)
            w = 2 * math.pi * self.trem_f
            tr = self.trem_a * (math.sin(w * self.t + self.trem_ph) - math.sin(w * (self.t - dt) + self.trem_ph))
            fx += tr
            fy += 0.7 * tr * math.cos(self.trem_ph)
            s["pos"], s["bump"] = pos, bump
            if tau >= 1.0:
                self.stroke = None
                self.next_at = self.t + self._gap()

        self.acc_x += sign * fx
        self.acc_y += fy
        ix, iy = int(self.acc_x), int(self.acc_y)   # il sensore manda solo conteggi interi
        self.acc_x -= ix
        self.acc_y -= iy
        self.pitch += iy
        self.nav.advance(ix, self._v(), dt)
        return ix, iy
