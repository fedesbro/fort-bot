"""Bot visivo: clicca "gioca", corre in tondo per un tempo casuale (10-14 minuti), poi torna alla
lobby (Esc → menu → torna alla lobby) e ricomincia.

Uso:
    python bot.py               avvia il bot
    python bot.py --dry-run     riconosce le schermate ma NON clicca/preme niente
    python bot.py --debug       stampa i punteggi di somiglianza a ogni controllo

Tasti (configurabili in config.yaml):  F8 pausa/riprendi   F9 stop
Emergenza: porta il mouse nell'angolo in alto a sinistra dello schermo.
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import math
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import yaml

from input_titan import DriverMancante, cursor_pos
from notifiche import Notifier
from umano import CircleNav, Human, TurnHand, min_jerk
from vision import Screen, Templates, find, make_dpi_aware, save_image

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("bot")
IN_MATCH = "in partita"   # partita avviata: si guarda solo il timer, nessuna schermata


class AbortSequence(Exception):
    """Una wait_for non ha trovato la schermata attesa: interrompe la sequenza corrente."""


class Interrupted(Exception):
    """Sequenza interrotta di proposito (tempo scaduto, schermata cambiata, pausa, stop)."""


# --------------------------------------------------------------------- hotkey
_VK = {f"f{i}": 0x6F + i for i in range(1, 13)}
_VK.update({"pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "home": 0x24, "end": 0x23})


class Hotkeys:
    """F8/F9 letti con GetAsyncKeyState (sola lettura dello stato dei tasti, nessun hook)."""

    def __init__(self, mapping: dict[str, callable]):
        self.items = []
        for name, fn in mapping.items():
            vk = _VK.get(str(name).lower())
            if vk is None:
                raise KeyError(f"hotkey non supportata: {name!r} (usa f1-f12, pause, insert, home, end)")
            self.items.append([vk, fn, False])
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self) -> None:
        get = ctypes.windll.user32.GetAsyncKeyState
        while True:
            for it in self.items:
                down = bool(get(it[0]) & 0x8000)
                if down and not it[2]:
                    try:
                        it[1]()
                    except Exception:  # noqa: BLE001
                        log.exception("errore nella hotkey")
                it[2] = down
            time.sleep(0.03)


class Bot:
    def __init__(self, cfg: dict, dry_run: bool, debug: bool):
        self.cfg = cfg
        self.dry_run = dry_run
        self.debug = debug
        self.screen = Screen(cfg.get("monitor", 1), cfg.get("region"))
        self.tpl = Templates(ROOT / cfg.get("templates_folder", "templates"))
        self.confidence = float(cfg.get("confidence", 0.85))
        self.interval = float(cfg.get("check_interval", 0.3))
        self.scales = cfg.get("scales") or [1.0]
        self.states: list[dict] = cfg["states"]
        self.recovery: list = cfg.get("recovery", [])
        self.unknown_timeout = float(cfg.get("unknown_timeout", 60))
        self.max_repeats = int(cfg.get("max_repeats", 5))

        self.paused = False
        self.running = True
        self.matches_done = 0
        self.durations: list[float] = []
        self.stop_reason = "fermato"

        self.human = Human(cfg.get("human"))

        # timer della partita: parte dal clic su "gioca" (stato con starts_match: true)
        self.match_cfg: dict = cfg.get("match") or {}
        self.match_started_at: float | None = None
        self.match_limit: float | None = None
        self.match_timeout_hit = False
        self._move_from = 0.0
        self._last_timeout_try = 0.0
        self._timeout_tries = 0
        self._last_click_at: float | None = None

        # corsa in tondo
        self.mov: dict = cfg.get("movement") or {}
        self.radius = 3.0
        self.nav: CircleNav | None = None   # centro del cerchio: fissato a ogni partita
        self._circle_dir = random.choice((-1, 1))

        # avvisi: immagini che fanno partire una notifica quando compaiono sullo schermo
        self.alerts_cfg: dict = cfg.get("alerts") or {}
        self.alerts: list[dict] = list(self.alerts_cfg.get("templates") or [])
        self._alert_seen: dict[str, bool] = {}
        self._alert_sent: dict[str, float] = {}
        self._last_alert_check = 0.0

        self.notifier = Notifier(cfg.get("notifications"))

        self._last_frame = None
        self._last_gray = None
        self._held_keys: list[str] = []
        self._held_mouse: list[str] = []
        self.input_cfg: dict = cfg.get("titan") or {}
        self.cursor_mode = str(self.input_cfg.get("cursor_mode", "relative"))
        self._input = None
        if not dry_run:
            from input_titan import TitanInput
            self._input = TitanInput(self.input_cfg)

    # ------------------------------------------------------------------ setup
    def check_templates(self) -> list[str]:
        names = set()
        for st in self.states:
            names.update(self._state_templates(st))
            names.update(self._action_templates(st.get("actions", [])))
            names.update(self._action_templates(st.get("on_timeout", [])))
        names.update(self._action_templates(self.recovery))
        names.update(self._action_templates(self.match_cfg.get("on_timeout", [])))
        names.update(self._action_templates(self.match_cfg.get("actions", [])))
        names.update(a["template"] for a in self.alerts)
        return sorted(n for n in names if not self.tpl.exists(n))

    def check_keys(self) -> list[str]:
        """Tasti / pulsanti del mouse / testi di config.yaml che il driver non sa inviare."""
        from input_titan import resolve_button, resolve_key
        keys: set[str] = {str(self.mov.get("jump_key", "space"))}
        groups = [st.get(k, []) for st in self.states for k in ("actions", "on_timeout")]
        groups += [self.recovery, self.match_cfg.get("on_timeout", []), self.match_cfg.get("actions", [])]
        for g in groups:
            keys.update(self._action_keys(g))
        out = []
        for k in sorted(keys):
            kind, _, name = k.partition(":") if k.startswith(("mouse:", "type:")) else ("key", "", k)
            try:
                if kind == "mouse":
                    resolve_button(name)
                elif kind == "type":
                    for ch in name:
                        resolve_key(ch, self.input_cfg)
                else:
                    resolve_key(name, self.input_cfg)
            except KeyError as e:
                out.append(e.args[0] + (f" (nel testo di 'type': {name!r})" if kind == "type" else ""))
        return out

    @staticmethod
    def _action_keys(actions: list) -> list[str]:
        out: list[str] = []
        for a in actions:
            if not isinstance(a, dict):
                continue
            for opt in a.get("choose", []):
                out += Bot._action_keys(opt.get("do", []))
            for k in ("key", "nav"):
                v = a.get(k)
                if v is not None:
                    out += [str(x) for x in (v if isinstance(v, list) else [v])]
            for k in ("hold", "circle"):
                v = a.get(k)
                if isinstance(v, dict):
                    if k == "circle":
                        ks = [v.get("forward_key", "w"), v.get("sprint_key", "shift")]
                    else:
                        ks = v.get("keys") or v.get("key") or []
                    out += [str(x) for x in (ks if isinstance(ks, list) else [ks])]
                    if v.get("mouse"):
                        out.append(f"mouse:{v['mouse']}")
            if "type" in a:
                out.append(f"type:{a['type']}")
        return out

    @staticmethod
    def _state_templates(st: dict) -> list[str]:
        t = st.get("template")
        return [t] if isinstance(t, str) else list(t or [])

    @staticmethod
    def _action_templates(actions: list) -> list[str]:
        out = []
        for a in actions:
            if not isinstance(a, dict):
                continue
            for opt in a.get("choose", []):
                out += Bot._action_templates(opt.get("do", []))
            for k in ("click", "double_click", "right_click", "move"):
                if isinstance(a.get(k), str) and a[k] != "self":
                    out.append(a[k])
            for k in ("wait_for", "wait_gone"):
                v = a.get(k)
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, dict):
                    out.append(v["template"])
        return out

    def setup_hotkeys(self) -> None:
        hk = self.cfg.get("hotkeys", {})
        self._hotkeys = Hotkeys({hk.get("pause", "f8"): self.toggle_pause, hk.get("stop", "f9"): self.stop})

    def check_failsafe(self) -> None:
        """Mouse nell'angolo in alto a sinistra = fermata di emergenza."""
        try:
            x, y = cursor_pos()
        except Exception:
            return
        if x <= 2 and y <= 2 and self.running:
            log.warning("Mouse nell'angolo: fermata di emergenza.")
            self.stop_reason = "fermata di emergenza (mouse nell'angolo)"
            self.running = False

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        log.info("⏸  PAUSA" if self.paused else "▶  RIPRESO")

    def stop(self) -> None:
        log.info("⏹  Stop richiesto")
        self.stop_reason = "stop richiesto da tastiera"
        self.running = False

    def notify(self, event: str, title: str, message: str = "", **kw) -> None:
        """Notifica sul telefono (vedi notifications in config.yaml). Non blocca mai il bot."""
        self.notifier.send(event, title, message, **kw)

    # ------------------------------------------------------- timer della partita
    def start_match(self, at: float | None = None) -> None:
        self.match_started_at = at or time.time()
        self.match_timeout_hit = False
        self._last_timeout_try = 0.0
        self._timeout_tries = 0
        self._circle_dir = random.choice((-1, 1))
        # un solo raggio per tutta la partita: corsa e camminata girano sullo stesso cerchio
        max_r = float(self.mov.get("max_diameter", 6.0)) / 2
        self.radius = max_r * self.human.val(self.mov.get("radius_fill", [0.6, 0.74]), jitter=False)
        self.nav = None
        self.match_limit = self.human.val(self.match_cfg.get("seconds", [600, 840]), jitter=False)
        self._move_from = time.time() + self.human.val(self.match_cfg.get("start_moving_after", 0), jitter=False)
        log.info("⏱  Partita avviata: torno alla lobby dopo %dm %02ds · cerchio di %.1f m di diametro, verso %s",
                 self.match_limit // 60, self.match_limit % 60, self.radius * 2,
                 "destra" if self._circle_dir > 0 else "sinistra")

    def match_active(self) -> bool:
        return self.match_started_at is not None

    def match_expired(self) -> bool:
        return (self.match_active() and self.match_limit is not None
                and time.time() - self.match_started_at >= self.match_limit)

    def end_match(self, reason: str) -> None:
        if not self.match_active():
            return
        d = time.time() - self.match_started_at
        self.durations.append(d)
        self.matches_done += 1
        log.info("🏁 Partita #%d terminata (%s) dopo %dm %02ds", self.matches_done, reason, d // 60, d % 60)
        self.notify("match_end", f"Partita #{self.matches_done} terminata",
                    f"Motivo: {reason} · durata {int(d // 60)}m {int(d % 60):02d}s", priority=2,
                    tags=["checkered_flag"])
        self.match_started_at = None
        self.match_limit = None
        self.match_timeout_hit = False
        limit = self.cfg.get("max_matches")
        if limit and self.matches_done >= int(limit):
            log.info("Raggiunto il limite di %s partite.", limit)
            self.stop_reason = f"raggiunto il limite di {limit} partite"
            self.running = False

    # ------------------------------------------------------------------ avvisi
    def check_alerts(self, gray=None) -> None:
        """Se compare una delle immagini di `alerts` manda una notifica (una per comparsa)."""
        if not self.alerts:
            return
        now = time.time()
        if now - self._last_alert_check < float(self.alerts_cfg.get("every", 1.0)):
            return
        self._last_alert_check = now
        gray = self.grab_gray() if gray is None else gray
        cooldown = float(self.alerts_cfg.get("cooldown", 120))
        for a in self.alerts:
            name = a["template"]
            conf = float(a.get("confidence", self.confidence))
            m = find(gray, self.tpl.get(name), name, self.screen, self.scales)
            visible = m.score >= conf
            was = self._alert_seen.get(name, False)
            self._alert_seen[name] = visible
            if visible and not was and now - self._alert_sent.get(name, 0) >= cooldown:
                self._alert_sent[name] = now
                title = a.get("title") or f"{Path(name).stem} sullo schermo"
                msg = a.get("message") or f"Ho visto '{name}' alle {datetime.now():%H:%M:%S}."
                log.info("🔔 %s (somiglianza %.0f%%)", title, m.score * 100)
                self.notify("alert", title, msg, priority=int(a.get("priority", 4)),
                            tags=list(a.get("tags") or ["bell"]))

    # ------------------------------------------------- tasti premuti / rilascio
    def _key_down(self, k: str) -> None:
        if self._input:
            self._input.key_down(k)
        if k not in self._held_keys:
            self._held_keys.append(k)

    def _key_up(self, k: str) -> None:
        if self._input:
            self._input.key_up(k)
        if k in self._held_keys:
            self._held_keys.remove(k)

    def _mouse_down(self, btn: str) -> None:
        if self._input:
            self._input.mouse_down(btn)
        if btn not in self._held_mouse:
            self._held_mouse.append(btn)

    def _mouse_up(self, btn: str) -> None:
        if self._input:
            self._input.mouse_up(btn)
        self._last_click_at = time.time()
        if btn in self._held_mouse:
            self._held_mouse.remove(btn)

    def _rel(self, dx: int, dy: int) -> None:
        if self._input and (dx or dy):
            self._input.move_relative(dx, dy)

    def _tap(self, key: str) -> None:
        self._key_down(key)
        self._sleep(self.human.t(self.human.key_hold) or 0.05)
        self._key_up(key)

    def release_all(self) -> None:
        """Rilascia tutto ciò che il bot tiene premuto, in ordine sparso e con tempi umani."""
        if self._held_keys or self._held_mouse:
            log.info("   ↩  rilascio %s", ", ".join(self._held_keys + [f"mouse {b}" for b in self._held_mouse]))
        for b in list(self._held_mouse):
            try:
                self._mouse_up(b)
            except Exception as e:
                log.warning("   impossibile rilasciare mouse %s: %s", b, e)
        keys = list(self._held_keys)
        random.shuffle(keys)
        for i, k in enumerate(keys):
            try:
                if i:
                    self._sleep(self.human.t([0.01, 0.07]))
                self._key_up(k)
            except Exception as e:
                log.warning("   impossibile rilasciare %s: %s", k, e)
        self._held_keys.clear()
        self._held_mouse.clear()

    def close_input(self) -> None:
        self.release_all()
        if self._input:
            self._input.close()

    # ---------------------------------------------------------------- attese
    def make_watch(self, state_name: str | None):
        """Controllo da chiamare spesso durante un'azione lunga: solleva Interrupted per stop,
        pausa, emergenza, tempo della partita scaduto (IN_MATCH) o schermata cambiata."""
        grace = float(self.cfg.get("lost_grace", 1.0))
        ctx = {"lost": None}

        def check() -> None:
            self.check_failsafe()
            if not self.running:
                raise Interrupted("stop")
            if self.paused:
                raise Interrupted("pausa")
            if state_name == IN_MATCH:
                if self.match_expired():
                    raise Interrupted("tempo della partita scaduto")
                self.check_alerts()
                return
            if state_name is None:
                return
            st, _ = self.detect_state()
            self.check_alerts(self._last_gray)
            if st is not None and st["name"] != state_name:
                raise Interrupted(f"schermata cambiata in '{st['name']}'")
            if st is None:
                ctx["lost"] = ctx["lost"] or time.time()
                if time.time() - ctx["lost"] > grace:
                    raise Interrupted(f"schermata '{state_name}' non più visibile")
            else:
                ctx["lost"] = None

        return check

    def watched_sleep(self, secs: float, state_name: str | None) -> None:
        end = time.time() + secs
        check = self.make_watch(state_name)
        while True:
            left = end - time.time()
            if left <= 0:
                return
            t0 = time.time()
            check()
            step = 0.1 if state_name is None else self.interval - (time.time() - t0)
            time.sleep(max(0.0, min(end - time.time(), step)))

    def _sleep(self, secs: float) -> None:
        if secs > 0:
            time.sleep(secs)

    # ------------------------------------------------------------ riconoscimento
    def grab_gray(self):
        frame = self.screen.grab()
        self._last_frame = frame
        self._last_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return self._last_gray

    def locate(self, name: str, gray=None, confidence: float | None = None):
        gray = self.grab_gray() if gray is None else gray
        m = find(gray, self.tpl.get(name), name, self.screen, self.scales)
        return m if m.score >= (confidence or self.confidence) else None

    def detect_state(self):
        gray = self.grab_gray()
        scores = []
        for st in self.states:
            conf = float(st.get("confidence", self.confidence))
            for name in self._state_templates(st):
                m = find(gray, self.tpl.get(name), name, self.screen, self.scales)
                scores.append(f"{st['name']}={m.score:.2f}")
                if m.score >= conf:
                    if self.debug:
                        log.debug("punteggi: %s", "  ".join(scores))
                    return st, m
        if self.debug:
            log.debug("punteggi: %s", "  ".join(scores))
        return None, None

    # ------------------------------------------------------------- mouse umano
    def _glide(self, dx: float, dy: float, secs: float, profile: bool = True) -> None:
        """Spostamento relativo distribuito sui report del mouse (es. 500 al secondo), con
        velocità a campana: è così che arriva al PC il movimento di un mouse vero."""
        hz = self.human.mouse_hz
        n = max(1, int(round(secs * hz)))
        period = secs / n if n else 0
        done_x = done_y = 0
        nxt = time.perf_counter()
        for i in range(1, n + 1):
            s = min_jerk(i / n) if profile else i / n
            tx, ty = int(round(dx * s)), int(round(dy * s))
            self._rel(tx - done_x, ty - done_y)
            done_x, done_y = tx, ty
            nxt += period
            left = nxt - time.perf_counter()
            if left > 0:
                time.sleep(left)

    def move_mouse(self, x: int, y: int) -> None:
        """Porta il cursore in (x, y) lungo una traiettoria umana. Con cursor_mode: relative
        (predefinito) il mouse invia solo spostamenti relativi, come un mouse fisico, e la
        posizione viene corretta guardando dove è davvero il cursore."""
        if not self._input:
            return
        x0, y0 = cursor_pos()
        if self.cursor_mode == "absolute":
            for px, py, wait in self.human.path(x0, y0, x, y):
                self._sleep(wait)
                if not self.running:
                    raise Interrupted("stop")
                self._input.move_absolute(px, py)
            return
        for px, py, wait in self.human.path(x0, y0, x, y):
            if not self.running:
                raise Interrupted("stop")
            cx, cy = cursor_pos()
            self._glide(px - cx, py - cy, max(wait, 0.002), profile=False)
        for _ in range(10):  # ultimi aggiustamenti, piccoli e lenti, come una mano che "centra"
            cx, cy = cursor_pos()
            ex, ey = x - cx, y - cy
            if abs(ex) <= 1 and abs(ey) <= 1:
                break
            self._sleep(random.uniform(0.012, 0.035))
            self._glide(ex, ey, random.uniform(0.02, 0.06))

    def look(self, dx: float, dy: float, secs: float) -> None:
        """Gira la visuale di (dx, dy) conteggi del mouse in `secs` secondi, con velocità a campana."""
        secs = secs or self.human.t(self.cfg.get("human", {}).get("look_time", [0.15, 0.35])) or 0.05
        self._glide(dx, dy, secs)

    def navigate(self, nav) -> None:
        """Preme, uno alla volta e con tempi umani, i tasti di `nav` (es. [tab, down])."""
        H = self.human
        for i, k in enumerate(nav if isinstance(nav, list) else [nav]):
            if not self.running:
                raise Interrupted("stop")
            if i:
                self._sleep(H.t(H.reaction) * 0.6)
            self._tap(str(k))

    # ----------------------------------------------------------------- azioni
    def run_actions(self, actions: list, found=None, state_name: str | None = None) -> None:
        """Esegue le azioni; i tasti tenuti premuti vengono sempre rilasciati alla fine."""
        try:
            for a in actions:
                self.check_failsafe()
                if not self.running:
                    return
                if self.paused:
                    raise Interrupted("pausa")
                if isinstance(a, str):
                    a = {a: True}
                self.do(a, found, state_name)
        finally:
            self.release_all()

    def _point(self, target, found):
        """Bersaglio (self / template / [x, y]) → (x, y, larghezza, altezza) assoluti."""
        if target == "self" or target is True:
            if found is None:
                raise AbortSequence("'self' usato senza una schermata trovata")
            return found.x, found.y, found.w, found.h
        if isinstance(target, str):
            m = self.locate(target)
            if m is None:
                raise AbortSequence(f"bottone '{target}' non trovato sullo schermo")
            return m.x, m.y, m.w, m.h
        x, y = target
        ax, ay = self.screen.to_abs(int(self.human.val(x, jitter=False)), int(self.human.val(y, jitter=False)))
        return ax, ay, 0, 0

    def do(self, a: dict, found, state_name: str | None = None) -> None:
        off = a.get("offset", [0, 0])
        H = self.human

        if "wait" in a:
            self.watched_sleep(H.val(a["wait"]), state_name)

        elif any(k in a for k in ("click", "double_click", "right_click")):
            kind = next(k for k in ("click", "double_click", "right_click") if k in a)
            self._sleep(H.t(H.reaction))  # tempo di "reazione" prima di muoversi
            x, y, w, h = self._point(a[kind], found)
            btn = "right" if kind == "right_click" else "left"
            px, py = H.click_point(x + off[0], y + off[1], w, h)
            nav = a.get("nav") or []
            nav = nav if isinstance(nav, list) else [nav]
            log.info("   🖱  %s su (%d, %d)%s", kind, px, py, f" dopo {' '.join(map(str, nav))}" if nav else "")
            if nav:
                self.navigate(nav)
            self.move_mouse(px, py)
            self._sleep(H.tiny(0.07))  # il dito si prepara
            for i in range(2 if kind == "double_click" else 1):
                if i:
                    self._sleep(H.t(H.double_click_gap) or 0.05)
                self._mouse_down(btn)
                hold = H.t(H.click_hold) or 0.05
                if H.enabled and random.random() < 0.18:   # premendo, il mouse si sposta di un niente
                    self._sleep(hold * 0.5)
                    self._rel(random.choice((-1, 0, 1)), random.choice((-1, 1)))
                    self._sleep(hold * 0.5)
                else:
                    self._sleep(hold)
                self._mouse_up(btn)

        elif "move" in a:
            x, y, w, h = self._point(a["move"], found)
            px, py = H.click_point(x + off[0], y + off[1], w, h)
            log.info("   🖱  sposto il mouse su (%d, %d)", px, py)
            if a.get("nav"):
                self.navigate(a["nav"])
            self._sleep(H.t(H.reaction) * 0.5)
            self.move_mouse(px, py)

        elif "nav" in a:
            log.info("   🎮 %s", " ".join(map(str, a["nav"] if isinstance(a["nav"], list) else [a["nav"]])))
            self._sleep(H.t(H.reaction) * 0.5)
            self.navigate(a["nav"])

        elif "key" in a:
            keys = a["key"] if isinstance(a["key"], list) else [a["key"]]
            log.info("   ⌨  tasto %s", "+".join(map(str, keys)))
            if a.get("reaction", True):
                self._sleep(H.t(H.reaction))
            for i, k in enumerate(keys):  # combinazione, es. [ctrl, r]
                if i:
                    self._sleep(H.t(H.between_keys) or 0.03)
                self._key_down(str(k))
            self._sleep(H.t(H.key_hold) or 0.05)
            for i, k in enumerate(reversed(keys)):
                if i:
                    self._sleep(H.t(H.between_keys) * 0.6 or 0.02)
                self._key_up(str(k))

        elif "hold" in a:
            h = a["hold"]
            keys = h.get("keys") or h.get("key") or []
            keys = [str(k) for k in (keys if isinstance(keys, list) else [keys])]
            mouse = h.get("mouse")
            secs = H.val(h.get("seconds", 1))
            log.info("   ⌨  tengo premuto %s per %.1fs", "+".join(keys + ([f"mouse {mouse}"] if mouse else [])), secs)
            try:
                for k in keys:
                    self._key_down(k)
                    self._sleep(H.t(H.between_keys) or 0.02)
                if mouse:
                    self._mouse_down(mouse)
                self.watched_sleep(secs, state_name)
            finally:
                if mouse and mouse in self._held_mouse:
                    self._mouse_up(mouse)
                for k in reversed(keys):
                    if k in self._held_keys:
                        self._key_up(k)

        elif "look" in a:
            spec = a["look"]
            if isinstance(spec, dict):
                dx, dy, secs = H.val(spec.get("dx", 0)), H.val(spec.get("dy", 0)), H.val(spec.get("seconds", 0))
            else:
                dx, dy, secs = H.val(spec[0]), H.val(spec[1]), 0.0
            log.info("   🖱  giro la visuale di (%d, %d)", dx, dy)
            self.look(dx, dy, secs)

        elif "type" in a:
            text = str(a["type"])
            log.info("   ⌨  scrivo %r", text)
            if a.get("reaction", True):
                self._sleep(H.t(H.reaction) * 0.5)
            for i, ch in enumerate(text):
                if not self.running:
                    raise Interrupted("stop")
                if i:
                    self._sleep(H.t(H.typing) or 0.03)
                self._key_down(ch)
                self._sleep(H.t(H.key_hold) or 0.03)
                self._key_up(ch)

        elif "wait_for" in a or "wait_gone" in a:
            gone = "wait_gone" in a
            spec = a["wait_gone" if gone else "wait_for"]
            name = spec if isinstance(spec, str) else spec["template"]
            timeout = float(a.get("timeout", spec.get("timeout", 30) if isinstance(spec, dict) else 30))
            log.info("   ⏳ aspetto che '%s' %s (max %ds)", name, "sparisca" if gone else "compaia", timeout)
            end = time.time() + timeout
            while time.time() < end and self.running:
                present = self.locate(name) is not None
                self.check_alerts(self._last_gray)
                if present != gone:
                    return
                time.sleep(self.interval)
            raise AbortSequence(f"timeout aspettando '{name}'")

        elif "circle" in a:
            self.do_circle(a["circle"], state_name)

        elif "choose" in a:
            opts = a["choose"]
            pick = random.choices(opts, weights=[float(o.get("weight", 1)) for o in opts])[0]
            if pick.get("name"):
                log.info("   🎲 %s", pick["name"])
            for sub in pick.get("do", []):
                self.do(sub if isinstance(sub, dict) else {sub: True}, found, state_name)

        elif "log" in a:
            log.info("   📝 %s", a["log"])

        else:
            log.warning("   azione sconosciuta ignorata: %s", a)

    # ----------------------------------------------------------- corsa in tondo
    def _speed(self, gait: str) -> float:
        return float(self.mov.get("walk_speed" if gait == "walk" else "run_speed", 3.0 if gait == "walk" else 6.0))

    def do_circle(self, c: dict, state_name: str | None) -> None:
        """Corre in tondo senza fermarsi: W resta premuto, l'andatura cambia premendo o lasciando
        Shift (corsa ↔ camminata). Una "mano" (umano.TurnHand) gira la visuale a passate, con la
        velocità angolare che tiene il personaggio sul cerchio scelto a inizio partita (diametro
        massimo movement.max_diameter). Intanto, come una persona vera, ogni tanto riassesta le
        dita sui tasti, salta, e raramente si ferma un attimo."""
        H = self.human
        fwd, sprint = str(c.get("forward_key", "w")), str(c.get("sprint_key", "shift"))
        jump_key = str(self.mov.get("jump_key", "space"))
        gaits = {"run": c.get("run") or {"weight": 6, "seconds": [8, 20]},
                 "walk": c.get("walk") or {"weight": 2, "seconds": [4, 9]}}
        jump = float(c.get("jump_chance", 0.03))           # al secondo
        stop_chance = float(c.get("stop_chance", 0.01))    # al secondo
        regrip = float(self.mov.get("regrip_chance", 0.012))
        total = H.val(c["seconds"]) if c.get("seconds") else None   # None = fino a fine partita
        if self.nav is None:
            self.nav = CircleNav(self.radius, self._circle_dir, float(self.mov.get("mouse_counts_per_360", 2000)))

        def pick() -> tuple[str, float]:
            g = random.choices(list(gaits), weights=[float(v.get("weight", 1)) for v in gaits.values()])[0]
            return g, time.time() + H.val(gaits[g].get("seconds", [5, 10]))

        gait, phase_end = pick()
        log.info("   ⭕ in tondo verso %s (Ø %.1f m) · %s", "destra" if self._circle_dir > 0 else "sinistra",
                 self.radius * 2, "corsa" if gait == "run" else "camminata")
        hand = TurnHand(self.nav, self._speed(gait), enabled=H.enabled,
                        accel_tau=float(self.mov.get("accel_time", 0.1)))
        stop = threading.Event()
        errors: list[Exception] = []

        def turner() -> None:
            # un report del mouse ogni 1/mouse_hz secondi, scandito con perf_counter
            try:
                period = 1.0 / max(60, H.mouse_hz)
                nxt = last = time.perf_counter()
                while not stop.is_set():
                    nxt += period
                    left = nxt - time.perf_counter()
                    if left > 0:
                        time.sleep(left)
                    elif left < -10 * period:   # rimasto molto indietro: riparte senza raffiche
                        nxt = time.perf_counter()
                    now = time.perf_counter()
                    dx, dy = hand.tick(now - last)
                    last = now
                    self._rel(dx, dy)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def press_gait(keys: list[str]) -> None:
            if len(keys) > 1 and random.random() < 0.45:   # l'ordine delle dita cambia
                keys = keys[::-1]
            for i, k in enumerate(keys):
                if i:
                    self._sleep(H.t([0.025, 0.16]))
                self._key_down(k)

        check = self.make_watch(state_name)
        th = threading.Thread(target=turner, daemon=True)
        end = time.time() + total if total else None
        try:
            press_gait([sprint, fwd] if gait == "run" else [fwd])
            th.start()
            while end is None or time.time() < end:
                t0 = time.time()
                check()
                if errors:
                    raise errors[0]
                dt = self.interval
                if time.time() >= phase_end:                       # cambio di andatura
                    new, phase_end = pick()
                    if new != gait:
                        log.info("   ⭕ %s", "corsa" if new == "run" else "camminata")
                        self._sleep(H.t([0.0, 0.1]))
                        (self._key_down if new == "run" else self._key_up)(sprint)
                        gait = new
                        hand.set_speed(self._speed(gait))
                elif H.enabled and random.random() < stop_chance * dt:   # si ferma un attimo
                    log.info("   ⏸  fermo un attimo")
                    held = [k for k in (fwd, sprint) if k in self._held_keys]
                    random.shuffle(held)
                    for i, k in enumerate(held):
                        if i:
                            self._sleep(H.t([0.01, 0.07]))
                        self._key_up(k)
                    hand.set_speed(0)
                    self.watched_sleep(H.val(c.get("stop_seconds", [0.3, 1.0])), state_name)
                    press_gait([sprint, fwd] if gait == "run" else [fwd])
                    hand.set_speed(self._speed(gait))
                elif H.enabled and random.random() < regrip * dt:    # riassesta il dito
                    k = random.choice([k for k in (fwd, sprint) if k in self._held_keys] or [fwd])
                    self._key_up(k)
                    self._sleep(H.t([0.04, 0.13]))
                    self._key_down(k)
                elif jump and random.random() < jump * dt:
                    log.info("   ⌨  salto")
                    self._sleep(H.t([0.0, 0.12]))
                    self._tap(jump_key)
                left = self.interval - (time.time() - t0)
                if end is not None:
                    left = min(left, end - time.time())
                time.sleep(max(0.0, left))
        finally:
            stop.set()
            if th.is_alive():
                th.join(timeout=1)
            rel = [k for k in (fwd, sprint) if k in self._held_keys]
            random.shuffle(rel)
            for i, k in enumerate(rel):
                if i:
                    self._sleep(H.t([0.01, 0.08]))
                self._key_up(k)

    # ---------------------------------------------------------------- recovery
    def debug_shot(self, reason: str) -> None:
        if self._last_frame is None:
            return
        path = ROOT / "debug" / f"{datetime.now():%Y%m%d_%H%M%S}_{reason}.png"
        save_image(path, self._last_frame)
        log.warning("   📸 screenshot salvato: %s", path.relative_to(ROOT))

    def recover(self, reason: str) -> None:
        log.warning("⚠  %s → eseguo recovery", reason)
        self.notify("problem", "Bot in difficoltà", f"{reason[:1].upper()}{reason[1:]}: eseguo il recovery.",
                    priority=4, tags=["warning"])
        self.debug_shot(reason.split()[0])
        try:
            self.run_actions(self.recovery)
        except (AbortSequence, Interrupted) as e:
            log.warning("   recovery interrotta: %s", e)

    def handle_match_timeout(self, now: float) -> None:
        """Tempo scaduto: esegue match.on_timeout (Esc → menu), riprovando se il menu non compare."""
        retry = self.human.val(self.match_cfg.get("retry_every", [6, 10]))
        if self.match_timeout_hit and now - self._last_timeout_try < retry:
            time.sleep(self.interval)
            return
        if not self.match_timeout_hit:
            log.info("⏱  Tempo scaduto (%.0fs dal clic su gioca) → torno alla lobby", now - self.match_started_at)
        else:
            log.info("   il menu non è comparso, riprovo")
        self._timeout_tries += 1
        if self._timeout_tries == 4:
            self.notify("problem", "Non riesco a uscire dalla partita",
                        "Tempo scaduto, ma dopo l'Esc il menu non compare. Continuo a riprovare.",
                        priority=4, tags=["warning"], force=True)
        self.match_timeout_hit = True
        self._last_timeout_try = now
        try:
            self.run_actions(self.match_cfg.get("on_timeout", []))
        except AbortSequence as e:
            log.warning("   uscita interrotta: %s", e)
            self.debug_shot("uscita_tempo")
        except Interrupted as e:
            log.info("   uscita interrotta: %s", e)
        self._last_timeout_try = time.time()   # il prossimo tentativo si conta da adesso

    # -------------------------------------------------------------- main loop
    def loop(self) -> None:
        last_state = None
        state_since = time.time()
        max_s: float | None = None
        repeats = 0
        last_known = time.time()

        log.info("Bot avviato%s. %s pausa, %s stop.", " (DRY-RUN: nessun input)" if self.dry_run else "",
                 str(self.cfg.get("hotkeys", {}).get("pause", "F8")).upper(),
                 str(self.cfg.get("hotkeys", {}).get("stop", "F9")).upper())

        while self.running:
            self.check_failsafe()
            if not self.running:
                break
            if self.paused:
                time.sleep(0.2)
                last_known = state_since = time.time()
                continue
            now = time.time()

            # ---- in partita: conta solo il timer (10-14 minuti dal clic su "gioca")
            if self.match_active() and not self.match_timeout_hit:
                if last_state != IN_MATCH:
                    log.info("▶ %s", IN_MATCH)
                    last_state, repeats = IN_MATCH, 0
                if self.match_expired():
                    self.handle_match_timeout(now)
                    last_known = time.time()
                    continue
                try:
                    if now < self._move_from:
                        self.watched_sleep(self._move_from - now, IN_MATCH)
                    elif self.match_cfg.get("actions"):
                        self.run_actions(self.match_cfg["actions"], None, state_name=IN_MATCH)
                    else:
                        self.watched_sleep(self.interval, IN_MATCH)
                except AbortSequence as e:
                    log.warning("   azioni in partita interrotte: %s", e)
                except Interrupted as e:
                    log.info("   ⏹ %s", e)
                last_known = time.time()
                continue

            # ---- fuori partita (o in uscita dopo il tempo scaduto): schermate
            st, m = self.detect_state()
            self.check_alerts(self._last_gray)
            now = time.time()

            if st is None:
                if self.match_active():          # uscita in corso: Esc non ha ancora aperto il menu
                    self.handle_match_timeout(now)
                    last_known = time.time()
                    continue
                if now - last_known > self.unknown_timeout:
                    self.recover(f"nessuna schermata riconosciuta da {self.unknown_timeout:.0f}s")
                    last_known = time.time()
                time.sleep(self.interval)
                continue
            last_known = now

            name = st["name"]
            if name != last_state:
                log.info("▶ Schermata: %s (somiglianza %.0f%%)", name, m.score * 100)
                last_state, state_since, repeats = name, now, 0
                max_s = self.human.val(st["max_seconds"]) if st.get("max_seconds") else None

            if max_s and now - state_since > max_s:
                if st.get("on_timeout"):
                    log.info("⏱  '%s' da più di %.0fs → eseguo on_timeout", name, max_s)
                    try:
                        self.run_actions(st["on_timeout"], m)
                    except AbortSequence as e:
                        log.warning("   on_timeout interrotto: %s", e)
                        self.debug_shot("on_timeout_interrotto")
                    except Interrupted as e:
                        log.info("   on_timeout interrotto: %s", e)
                else:
                    self.recover(f"bloccato in '{name}' da più di {max_s:.0f}s")
                state_since = time.time()
                max_s = self.human.val(st["max_seconds"])
                continue

            actions = st.get("actions") or []
            if not actions:
                time.sleep(self.interval)
                continue
            repeats += 1
            if repeats > self.max_repeats:
                self.recover(f"azioni di '{name}' ripetute {repeats - 1} volte senza cambiare schermata")
                repeats = 0
                continue
            try:
                if st.get("starts_match") and self.match_active():
                    self.end_match("tempo scaduto")   # tornati in lobby senza passare dalla scheda
                self.run_actions(actions, m)
                if st.get("ends_match") and self.running:
                    self.end_match("tempo scaduto")
                if st.get("starts_match") and self.running:
                    # il timer parte dall'istante del clic su "gioca", non dalla fine del caricamento
                    self.start_match(self._last_click_at)
            except AbortSequence as e:
                log.warning("   sequenza interrotta: %s", e)
                self.debug_shot("sequenza_interrotta")
            except Interrupted as e:
                log.info("   ⏹ azioni di '%s' interrotte: %s", name, e)
                continue
            time.sleep(self.human.val(st.get("cooldown", 1.0)))

        self.release_all()
        self.print_stats()

    def stats_lines(self) -> list[str]:
        out = [f"Partite completate: {self.matches_done}"]
        if self.durations:
            avg = sum(self.durations) / len(self.durations)
            out.append("Durata media: %dm %02ds (min %dm %02ds, max %dm %02ds)" % (
                avg // 60, avg % 60, min(self.durations) // 60, min(self.durations) % 60,
                max(self.durations) // 60, max(self.durations) % 60))
        return out

    def print_stats(self) -> None:
        log.info("──────── Riepilogo ────────")
        for line in self.stats_lines():
            log.info("%s", line)


def set_window_title(name: str) -> None:
    """Dà alla finestra del terminale un titolo chiaro, così chi la vede capisce cos'è."""
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(name)
    except Exception:
        pass
    bar = "═" * (len(name) + 8)
    print(f"\n╔{bar}╗\n║    {name}    ║\n╚{bar}╝")
    print("Script di test per il gioco. Chiudi questa finestra per fermarlo.\n")


def setup_logging(debug: bool) -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(ROOT / "logs" / f"bot_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)
    log.setLevel(logging.DEBUG if debug else logging.INFO)


def main() -> None:
    ap = argparse.ArgumentParser(description="Bot visivo per riavviare le partite")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="riconosce ma non clicca")
    ap.add_argument("--debug", action="store_true", help="mostra i punteggi di somiglianza")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    make_dpi_aware()
    setup_logging(args.debug)

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    set_window_title(cfg.get("app_name", "Test automatico del gioco"))

    try:
        bot = Bot(cfg, args.dry_run, args.debug)
    except DriverMancante as e:
        log.error("%s", e)
        log.error("(Per provare il riconoscimento senza driver: python bot.py --dry-run)")
        sys.exit(2)
    bad_keys = bot.check_keys()
    if bad_keys:
        log.error("Tasti non utilizzabili con il driver Titan (controlla config.yaml):")
        for n in bad_keys:
            log.error("   - %s", n)
        bot.close_input()
        sys.exit(1)
    missing = bot.check_templates()
    if missing:
        log.error("Mancano questi template nella cartella '%s':", cfg.get("templates_folder", "templates"))
        for n in missing:
            log.error("   - %s", n)
        log.error("Creali con:  python cattura_template.py <nome>")
        bot.close_input()
        sys.exit(1)

    bot.setup_hotkeys()
    delay = float(cfg.get("start_delay", 3))
    log.info("Parto tra %.0f secondi: porta in primo piano la finestra del gioco...", delay)
    time.sleep(delay)
    if bot.notifier.enabled:
        log.info("🔔 Notifiche attive sul topic '%s'", bot.notifier.topic)
    bot.notify("start", "Bot avviato", "Il bot è partito" + (" in modalità prova (dry-run)." if args.dry_run else "."),
               priority=2, tags=["robot"])
    crash = None
    try:
        bot.loop()
    except KeyboardInterrupt:
        bot.stop_reason = "interrotto con Ctrl+C"
        bot.print_stats()
    except Exception as e:
        log.exception("Errore imprevisto: il bot si ferma")
        crash = f"{type(e).__name__}: {e}"
        raise
    finally:
        bot.close_input()
        summary = "\n".join(bot.stats_lines())
        if crash:
            bot.notify("problem", "Bot fermato da un errore", f"{crash}\n{summary}",
                       priority=5, tags=["rotating_light"], force=True)
        else:
            bot.notify("stop", "Bot fermato", f"Motivo: {bot.stop_reason}\n{summary}", tags=["stop_sign"])
        bot.notifier.close()


if __name__ == "__main__":
    main()
