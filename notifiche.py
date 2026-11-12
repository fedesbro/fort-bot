"""Notifiche sul telefono tramite ntfy (https://ntfy.sh): il bot pubblica su un "topic"
e l'app ntfy iscritta a quel topic mostra la notifica.

Prova veloce (usa il topic di config.yaml):
    python notifiche.py                       manda una notifica di prova
    python notifiche.py "testo a piacere"
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.request

log = logging.getLogger("bot")

TOPIC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ntfy_topic.txt")


def _local_topic() -> str:
    """Topic segreto fuori da config.yaml: variabile NTFY_TOPIC oppure file ntfy_topic.txt
    (escluso da git), così il topic non finisce nella repo."""
    env = os.environ.get("NTFY_TOPIC", "").strip()
    if env:
        return env
    try:
        with open(TOPIC_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


class Notifier:
    def __init__(self, cfg: dict | None):
        c = cfg or {}
        self.topic = str(c.get("topic") or "").strip() or _local_topic()
        self.server = str(c.get("server") or "https://ntfy.sh").rstrip("/")
        self.enabled = bool(c.get("enabled", False)) and bool(self.topic)
        self.timeout = float(c.get("timeout", 10))
        self.events: dict = c.get("events") or {}
        self.throttle = float(c.get("throttle", 300))
        self._last: dict[str, float] = {}
        self._q: queue.Queue = queue.Queue()
        self._failed = False
        if self.enabled:
            # invio in un thread a parte: una rete lenta non deve mai rallentare il bot
            threading.Thread(target=self._worker, daemon=True).start()

    def send(self, event: str, title: str, message: str = "", priority: int = 3,
             tags: list[str] | None = None, force: bool = False) -> bool:
        """Mette in coda una notifica di tipo `event` (vedi notifications.events).
        `problem` arriva al massimo una volta ogni `throttle` secondi (salvo force=True).
        Restituisce False se la notifica non viene inviata."""
        if not self.enabled or not self.events.get(event, True):
            return False
        now = time.time()
        if event == "problem" and not force and now - self._last.get(event, -1e9) < self.throttle:
            return False
        self._last[event] = now
        self._q.put({"topic": self.topic, "title": title, "message": message or title,
                     "priority": max(1, min(5, int(priority))), "tags": list(tags or [])})
        return True

    def close(self, timeout: float = 8) -> None:
        """Aspetta (poco) che le notifiche in coda partano prima di chiudere il programma."""
        if not self.enabled:
            return
        end = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < end:
            time.sleep(0.05)

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            try:
                self._post(item)
                self._failed = False
            except Exception as e:  # noqa: BLE001 - una notifica persa non deve fermare il bot
                if not self._failed:  # avvisa una volta sola finché non torna a funzionare
                    log.warning("   🔕 notifica non inviata (%s)", e)
                self._failed = True
            finally:
                self._q.task_done()

    def _post(self, item: dict) -> None:
        # pubblicazione in JSON: titolo e testo possono contenere accenti ed emoji
        req = urllib.request.Request(self.server, data=json.dumps(item).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            r.read()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import yaml

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = yaml.safe_load((Path(__file__).resolve().parent / "config.yaml").read_text(encoding="utf-8"))
    n = Notifier({**(cfg.get("notifications") or {}), "enabled": True})
    if not n.topic:
        sys.exit("Nessun topic: scrivilo in ntfy_topic.txt o in notifications.topic di config.yaml")
    text = " ".join(sys.argv[1:]) or "Se leggi questo messaggio, le notifiche del bot funzionano."
    try:
        n._post({"topic": n.topic, "title": "Prova notifiche", "message": text, "priority": 3, "tags": ["bell"]})
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Invio NON riuscito verso {n.server}: {e}")
    print(f"Notifica inviata al topic '{n.topic}' su {n.server}: controlla il telefono.")
