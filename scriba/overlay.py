"""Pílula flutuante de gravação (estilo Granola): sempre no topo, arrastável."""

from __future__ import annotations

import json
import time
import tkinter as tk
from typing import Callable

from . import util

_BG = "#1e1e28"
_FG = "#f0f0f5"
_MUTED = "#9a9aa8"
_RED = "#e54545"
_RED_DIM = "#7a2626"
_KEY = "#000001"  # cor-chave que vira transparente (cantos arredondados)
_W, _H, _R = 220, 44, 21


def _load_pos() -> list[int] | None:
    try:
        return json.loads(util.STATE_PATH.read_text(encoding="utf-8")).get("overlay_pos")
    except Exception:
        return None


def _save_pos(x: int, y: int) -> None:
    try:
        data = {}
        if util.STATE_PATH.exists():
            data = json.loads(util.STATE_PATH.read_text(encoding="utf-8"))
        data["overlay_pos"] = [x, y]
        # atômico + merge: não perde outras chaves do state.json se o write for cortado
        util.atomic_write_text(util.STATE_PATH, json.dumps(data))
    except Exception:
        pass


def _pos_in_bounds(x: int, y: int, vx: int, vy: int, vw: int, vh: int) -> bool:
    """A pílula (_W×_H) em (x,y) cabe inteira dentro do retângulo visível
    (vx,vy,vw,vh)? Usado para descartar coordenadas salvas que caíram fora de
    qualquer tela — ex.: monitor desconectado depois de arrastar a pílula p/ lá."""
    return vx <= x <= vx + vw - _W and vy <= y <= vy + vh - _H


def _round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return c.create_polygon(pts, smooth=True, **kw)


class RecordingPill:
    """Janela-pílula da call.

    Modo "recording": ● pulsante, cronômetro, ■ encerrar, × descartar.
    Modo "idle": call em andamento sem gravar — botão ⏺ "Gravar reunião".
    """

    def __init__(
        self,
        root: tk.Misc,
        on_stop: Callable[[], None],
        on_discard: Callable[[], None],
        on_record: Callable[[], None] | None = None,
    ):
        self.on_stop = on_stop
        self.on_discard = on_discard
        self.on_record = on_record
        self.mode = "recording"
        self._destroyed = False
        self._pulse_on = True
        self._status_mode = False  # True = mostrando texto fixo (ex.: "finalizando…")

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.94)
        try:
            self.win.attributes("-transparentcolor", _KEY)
        except tk.TclError:
            pass

        c = tk.Canvas(self.win, width=_W, height=_H, bg=_KEY, highlightthickness=0)
        c.pack()
        self.canvas = c
        _round_rect(c, 1, 1, _W - 1, _H - 1, _R, fill=_BG, outline="#34343f")

        self.dot = c.create_oval(16, _H // 2 - 6, 28, _H // 2 + 6, fill=_RED, outline="")
        self.time_text = c.create_text(
            44, _H // 2, anchor="w", text="00:00", fill=_FG, font=("Segoe UI", 12, "bold")
        )
        self.stop_btn = c.create_text(
            _W - 62, _H // 2, text="■", fill=_FG, font=("Segoe UI", 13), tags=("btn", "stop")
        )
        self.discard_btn = c.create_text(
            _W - 28, _H // 2, text="×", fill=_MUTED, font=("Segoe UI", 15, "bold"), tags=("btn", "discard")
        )

        # modo idle: botão de gravar (anel + ponto, estilo REC) + rótulo
        cy = _H // 2
        self.idle_ring = c.create_oval(15, cy - 8, 31, cy + 8, outline=_RED, width=2,
                                       state="hidden", tags=("btn", "rec"))
        self.idle_dot = c.create_oval(19, cy - 4, 27, cy + 4, fill=_RED, outline="",
                                      state="hidden", tags=("btn", "rec"))
        self.idle_label = c.create_text(44, cy, anchor="w", text="Gravar reunião", fill=_FG,
                                        font=("Segoe UI", 10, "bold"), state="hidden", tags=("btn", "rec"))

        c.tag_bind("stop", "<Button-1>", lambda e: self._click(self.on_stop))
        c.tag_bind("discard", "<Button-1>", lambda e: self._click(self.on_discard))
        c.tag_bind("rec", "<Button-1>", lambda e: self._click(self._record))
        for tag, item in (("stop", self.stop_btn), ("discard", self.discard_btn)):
            c.tag_bind(tag, "<Enter>", lambda e, i=item: c.itemconfigure(i, fill=_RED))
            c.tag_bind(tag, "<Leave>", lambda e, i=item, f=(_FG if tag == "stop" else _MUTED): c.itemconfigure(i, fill=f))
        c.tag_bind("rec", "<Enter>", lambda e: c.itemconfigure(self.idle_label, fill=_RED))
        c.tag_bind("rec", "<Leave>", lambda e: c.itemconfigure(self.idle_label, fill=_FG))

        # arrastar (em qualquer ponto que não seja botão)
        c.bind("<Button-1>", self._drag_start)
        c.bind("<B1-Motion>", self._drag_move)
        c.bind("<ButtonRelease-1>", self._drag_end)
        self._drag_offset = None

        self._pulse()

    # -- interação -----------------------------------------------------------

    def _click(self, handler: Callable[[], None]) -> str:
        handler()
        return "break"

    def _record(self) -> None:
        if self.on_record is not None:
            self.on_record()

    def _on_button(self) -> bool:
        return any("btn" in self.canvas.gettags(i) for i in self.canvas.find_withtag("current"))

    def _drag_start(self, e):
        if self._on_button():
            self._drag_offset = None
            return
        self._drag_offset = (e.x, e.y)

    def _drag_move(self, e):
        if self._drag_offset is None:
            return
        x = self.win.winfo_pointerx() - self._drag_offset[0]
        y = self.win.winfo_pointery() - self._drag_offset[1]
        self.win.geometry(f"+{x}+{y}")

    def _drag_end(self, _e):
        if self._drag_offset is not None:
            self._drag_offset = None
            _save_pos(self.win.winfo_x(), self.win.winfo_y())

    # -- estado --------------------------------------------------------------

    def _pulse(self):
        if self._destroyed:
            return
        self._pulse_on = not self._pulse_on
        try:
            if self.mode == "recording":
                self.canvas.itemconfigure(self.dot, fill=_RED if self._pulse_on else _RED_DIM)
            self.win.after(600, self._pulse)
        except tk.TclError:
            pass

    def set_mode(self, mode: str) -> None:
        """Alterna entre "recording" (●/cronômetro/■/×) e "idle" (⏺ Gravar reunião)."""
        self.mode = mode
        self._status_mode = False
        rec_items = (self.dot, self.time_text, self.stop_btn, self.discard_btn)
        idle_items = (self.idle_ring, self.idle_dot, self.idle_label)
        show, hide = (rec_items, idle_items) if mode == "recording" else (idle_items, rec_items)
        try:
            for item in show:
                self.canvas.itemconfigure(item, state="normal")
            for item in hide:
                self.canvas.itemconfigure(item, state="hidden")
        except tk.TclError:
            pass

    def _pos_visible(self, x: int, y: int) -> bool:
        """Valida (x,y) contra a área visível: bounding rect de todos os monitores
        (util.virtual_screen_rect); se indisponível, cai no monitor primário."""
        rect = util.virtual_screen_rect()
        if rect is None:
            rect = (0, 0, self.win.winfo_screenwidth(), self.win.winfo_screenheight())
        return _pos_in_bounds(x, y, *rect)

    def show(self):
        self.clear_status()
        pos = _load_pos()
        x = y = None
        if pos:
            try:
                px, py = int(pos[0]), int(pos[1])
            except (TypeError, ValueError, IndexError):
                px = py = None
            if px is not None and self._pos_visible(px, py):
                x, y = px, py
        if x is None:  # sem posição salva, inválida, ou fora de qualquer tela
            x = (self.win.winfo_screenwidth() - _W) // 2
            y = 10
        self.win.geometry(f"{_W}x{_H}+{x}+{y}")
        self.win.deiconify()
        self.win.lift()

    def hide(self):
        try:
            self.win.withdraw()
        except tk.TclError:
            pass

    def set_elapsed(self, seconds: float):
        if self._status_mode:
            return
        s = int(seconds)
        text = f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"
        try:
            self.canvas.itemconfigure(self.time_text, text=text)
        except tk.TclError:
            pass

    def set_status(self, text: str):
        """Modo texto fixo sem botões (ex.: 'finalizando…')."""
        self._status_mode = True
        try:
            self.canvas.itemconfigure(self.time_text, text=text)
            self.canvas.itemconfigure(self.stop_btn, state="hidden")
            self.canvas.itemconfigure(self.discard_btn, state="hidden")
        except tk.TclError:
            pass

    def set_processing(self, text: str):
        """Pós-call: ponto âmbar + estágio ('Transcrevendo…', 'Gerando resumo…'), sem botões."""
        self._status_mode = True
        self.mode = "processing"  # impede o _pulse de pintar o ponto de vermelho
        try:
            self.canvas.itemconfigure(self.dot, fill="#e0b341", state="normal")
            self.canvas.itemconfigure(self.time_text, text=text, state="normal")
            for it in (self.stop_btn, self.discard_btn, self.idle_ring, self.idle_dot, self.idle_label):
                self.canvas.itemconfigure(it, state="hidden")
        except tk.TclError:
            pass

    def clear_status(self):
        """Sai do modo texto fixo, restaurando os controles do modo atual."""
        self.set_mode(self.mode)

    def destroy(self):
        self._destroyed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def run_countdown(seconds: int) -> str:
    """Pílula bloqueante para gravações manuais (`scriba record N`).

    Retorna "ok" (tempo esgotado ou ■), ou "discarded" (×).
    """
    root = tk.Tk()
    root.withdraw()
    outcome = {"v": "ok"}

    def stop():
        outcome["v"] = "ok"
        root.quit()

    def discard():
        outcome["v"] = "discarded"
        root.quit()

    pill = RecordingPill(root, on_stop=stop, on_discard=discard)
    pill.show()
    t0 = time.monotonic()

    def tick():
        elapsed = time.monotonic() - t0
        if elapsed >= seconds:
            root.quit()
            return
        pill.set_elapsed(elapsed)
        root.after(250, tick)

    tick()
    root.mainloop()
    pill.destroy()
    root.destroy()
    return outcome["v"]
