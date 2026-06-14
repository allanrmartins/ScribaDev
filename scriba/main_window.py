"""Janela principal do ScribaDev: status dos serviços, call ao vivo e gravação manual.

Comportamento de janela "de verdade": minimizar mantém na barra de tarefas;
fechar (X) tira da barra mas o app continua na bandeja.
"""

from __future__ import annotations

import threading
import tkinter as tk

from .widgets import FONT, FONT_BOLD, PALETTE, LinkLabel, ModernButton, enable_dark_titlebar

_BG = PALETTE["bg"]
_CARD = "#2d2d37"
_LEVEL_COLORS = {"ok": PALETTE["ok"], "warn": "#e0b341", "off": PALETTE["muted"]}


def _fmt(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


class MainWindow:
    def __init__(self, root: tk.Tk, app):
        self.app = app
        self.root = root
        self._titlebar_done = False
        root.title("ScribaDev")
        root.configure(bg=_BG)
        root.minsize(520, 560)
        root.protocol("WM_DELETE_WINDOW", self.hide)  # X: sai da barra, fica na bandeja
        self._set_icon(root)

        body = tk.Frame(root, bg=_BG, padx=18, pady=14)
        body.pack(fill="both", expand=True)

        # ---- cabeçalho --------------------------------------------------------
        head = tk.Frame(body, bg=_BG)
        head.pack(fill="x")
        tk.Label(head, text="ScribaDev", bg=_BG, fg=PALETTE["text"], font=("Segoe UI", 16, "bold")).pack(side="left")
        ModernButton(head, "⚙", lambda: self.app.show_settings(), width=42, height=32).pack(side="right")
        ModernButton(head, "Notas", lambda: self.app.show_notes(), height=32).pack(
            side="right", padx=(0, 8)
        )

        # ---- call ao vivo -----------------------------------------------------
        card = tk.Frame(body, bg=_CARD, padx=16, pady=14)
        card.pack(fill="x", pady=(14, 4))
        top = tk.Frame(card, bg=_CARD)
        top.pack(fill="x")
        self._call_dot = tk.Canvas(top, width=10, height=10, bg=_CARD, highlightthickness=0)
        self._call_dot_item = self._call_dot.create_oval(1, 1, 9, 9, fill=PALETTE["muted"], outline="")
        self._call_dot.pack(side="left", pady=2)
        self.call_state = tk.StringVar(value="Nenhuma ligação em andamento")
        tk.Label(top, textvariable=self.call_state, bg=_CARD, fg=PALETTE["muted"], font=FONT).pack(
            side="left", padx=7
        )
        self.call_timer = tk.StringVar(value="—")
        tk.Label(card, textvariable=self.call_timer, bg=_CARD, fg=PALETTE["text"],
                 font=("Segoe UI", 28, "bold")).pack(anchor="w", pady=(2, 8))
        self.rec_btn = ModernButton(card, "⏺  Gravar agora", self._toggle_record, kind="primary",
                                    width=190, height=36)
        self.rec_btn.pack(anchor="w")

        # ---- serviços ----------------------------------------------------------
        sect_head = tk.Frame(body, bg=_BG)
        sect_head.pack(fill="x", pady=(16, 4))
        tk.Label(sect_head, text="Serviços", bg=_BG, fg=PALETTE["text"], font=FONT_BOLD).pack(side="left")
        LinkLabel(sect_head, "atualizar", self.refresh_status).pack(side="right")
        self.rows = tk.Frame(body, bg=_BG)
        self.rows.pack(fill="both", expand=True)

        self._tick()

    # ---- comportamento ----------------------------------------------------------

    def _set_icon(self, root: tk.Tk) -> None:
        """Ícone da janela/taskbar: .ico (multi-resolução, mais nítido) com fallback PNG."""
        from . import util

        try:
            root.iconbitmap(default=str(util.ICON_ICO))
            return
        except Exception:
            pass
        try:
            from PIL import ImageTk

            from .tray import _icon_image

            self._icon = ImageTk.PhotoImage(_icon_image(False))
            root.iconphoto(True, self._icon)
        except Exception:
            pass

    def _toggle_record(self) -> None:
        if self.app.is_recording():
            threading.Thread(target=self.app.stop_recording, kwargs={"keep": True}, daemon=True).start()
        else:
            threading.Thread(target=self.app.start_recording, args=("manual",), daemon=True).start()

    def _tick(self) -> None:
        try:
            visible = self.root.state() not in ("withdrawn", "iconic")
        except tk.TclError:
            return
        if visible:
            recording = self.app.is_recording()
            app_name = self.app.current_call_app()
            if recording:
                self.call_state.set(f"Gravando ({app_name or 'manual'})")
                self.call_timer.set(_fmt(self.app.recording_duration()))
                self.rec_btn.set_text("■  Parar e processar")
                self._call_dot.itemconfigure(self._call_dot_item, fill=PALETTE["accent"])
            elif self.app.call_active:
                self.call_state.set(f"Em call ({app_name or '?'}) — sem gravar")
                self.call_timer.set(_fmt(self.app.call_duration()))
                self.rec_btn.set_text("⏺  Gravar agora")
                self._call_dot.itemconfigure(self._call_dot_item, fill="#e0b341")
            else:
                self.call_state.set("Nenhuma ligação em andamento")
                self.call_timer.set("—")
                self.rec_btn.set_text("⏺  Gravar agora")
                self._call_dot.itemconfigure(self._call_dot_item, fill=PALETTE["muted"])
        self.root.after(1000, self._tick)

    # ---- status dos serviços ------------------------------------------------------

    def refresh_status(self) -> None:
        threading.Thread(target=self._collect_status, daemon=True, name="status").start()

    def _collect_status(self) -> None:
        items: list[tuple[str, str, str]] = []
        cfg = self.app.cfg

        try:
            from .detector import app_key_status, patterns_from

            for name, exists in app_key_status(patterns_from(cfg.detection)).items():
                items.append(
                    ("ok" if exists else "warn", f"Detecção {name}",
                     "ativa" if exists else "entre numa call dele uma vez")
                )
        except Exception as e:
            items.append(("warn", "Detecção", str(e)))

        try:
            from .detector import (
                browser_key_status,
                browser_patterns_from,
                desktop_names_from,
                title_patterns_from,
                web_service_label,
            )

            bpats = browser_patterns_from(cfg.detection)
            if bpats:
                desktop = desktop_names_from(cfg.detection)
                svcs = ", ".join(dict.fromkeys(
                    web_service_label(t, desktop) for t in title_patterns_from(cfg.detection)
                )) or "qualquer site com mic aberto"
                ready = [b for b, ok in browser_key_status(bpats).items() if ok]
                items.append(
                    ("ok" if ready else "warn", "Detecção no navegador",
                     f"{svcs} — via {', '.join(ready)}"
                     if ready else f"{svcs} — entre numa call no navegador uma vez")
                )
        except Exception as e:
            items.append(("warn", "Detecção no navegador", str(e)))

        # enumeração de áudio em subprocesso: o PortAudio pode abortar com assert
        # de CRT quando os dispositivos mudam — isolado, nunca derruba o app
        from . import util as util_probe

        audio = util_probe.run_audio_probe()
        if audio:
            items.append(("ok", "Microfone", audio.get("mic", "?")))
            items.append(("ok", "Áudio do sistema", audio.get("loopback", "?")))
        else:
            items.append(("warn", "Áudio", "dispositivos indisponíveis no momento — clique em atualizar"))

        gpu = False
        try:
            import ctypes

            ctypes.WinDLL("nvcuda.dll")
            gpu = True
        except OSError:
            pass
        w = cfg.whisper
        if (w.engine or "local").strip().lower() == "cloud":
            ready = bool(w.cloud_api_key)
            items.append(("ok" if ready else "warn", "Transcrição (nuvem)",
                          f"{w.cloud_model} · nuvem" if ready else "falta chave — configure na aba Gravação"))
        else:
            device = "GPU" if gpu and w.device != "cpu" else "CPU"
            items.append(("ok", "Transcrição (Whisper)", f"{w.model} · {device}"))

        from . import util as util_mod

        if cfg.summary.enabled:
            s = cfg.summary
            prov = (s.provider or "claude").strip().lower()
            if prov == "ollama":
                items.append(("ok", "Resumo (Ollama)", f"{s.ollama_model} · {s.base_url or 'localhost:11434'}"))
            elif prov == "openai":
                ready = bool(s.base_url and s.api_key)
                items.append(("ok" if ready else "warn", "Resumo (OpenAI-compat)",
                              s.openai_model if ready else "falta endpoint/chave — configure na aba Resumo"))
            else:
                has_claude = util_mod.claude_command() is not None
                items.append(("ok" if has_claude else "warn", "Resumo (Claude)",
                              s.model if has_claude else "claude CLI não encontrado"))
        else:
            items.append(("off", "Resumo", "desativado"))

        if cfg.diarization.enabled:
            import importlib.util as ilu

            deps = ilu.find_spec("pyannote.audio") is not None
            if deps and cfg.diarization.hf_token:
                items.append(("ok", "Diarização", "separando participantes por voz"))
            else:
                items.append(("warn", "Diarização", "sem token HF" if deps else "dependências ausentes"))
        else:
            items.append(("off", "Diarização", "desativada"))

        from . import autostart

        on = autostart.is_enabled()
        items.append(("ok" if on else "off", "Iniciar com o Windows", "ligado" if on else "desligado"))

        self.app.ui(lambda: self._render_status(items))

    def _render_status(self, items: list[tuple[str, str, str]]) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        for level, name, detail in items:
            row = tk.Frame(self.rows, bg=_BG)
            row.pack(fill="x", pady=3)
            dot = tk.Canvas(row, width=10, height=10, bg=_BG, highlightthickness=0)
            dot.create_oval(1, 1, 9, 9, fill=_LEVEL_COLORS.get(level, PALETTE["muted"]), outline="")
            dot.pack(side="left", pady=2)
            tk.Label(row, text=name, bg=_BG, fg=PALETTE["text"], font=FONT, width=22, anchor="w").pack(
                side="left", padx=(8, 4)
            )
            tk.Label(row, text=detail, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                     anchor="w", justify="left", wraplength=260).pack(side="left", fill="x")

    # ---- público -------------------------------------------------------------------

    def show(self) -> None:
        self.root.deiconify()
        if not self._titlebar_done:
            self._titlebar_done = True
            enable_dark_titlebar(self.root)
        self.root.lift()
        self.root.focus_force()
        self.refresh_status()

    def hide(self) -> None:
        self.root.withdraw()
