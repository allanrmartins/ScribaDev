"""Janela de Configurações do ScribaDev (botão ⚙ do dashboard).

Abas: Gravação, Pastas e Resumo (com editor do prompt.md). As notas têm janela
própria (notes_ui.py). Fechar não encerra o app: a janela só se esconde.
"""

from __future__ import annotations

import dataclasses
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import config as config_mod
from . import util
from .widgets import (
    FONT,
    FONT_BOLD,
    PALETTE,
    LinkLabel,
    ModernButton,
    ScrollableFrame,
    Stepper,
    ToggleSwitch,
    enable_dark_titlebar,
    group_box,
    make_entry,
    make_secret_entry,
    make_text,
    separator,
    style_notebook,
)

_BG = PALETTE["bg"]

# rótulo amigável -> ID real passado ao claude via --model
_SUMMARY_MODELS = {
    "Haiku 4.5": "claude-haiku-4-5",
    "Sonnet 4.6": "claude-sonnet-4-6",
    "Opus 4.8": "claude-opus-4-8",
}
_MODEL_LABELS = {v: k for k, v in _SUMMARY_MODELS.items()}
_MODEL_LABELS.update({"sonnet": "Sonnet 4.6", "opus": "Opus 4.8", "haiku": "Haiku 4.5"})  # aliases antigos

_CHAT_MODEL_DEFAULT = "Mesmo do resumo"  # rótulo p/ chat_model vazio (#22)

# rótulo amigável -> id do provedor de IA (camada em scriba/ai.py)
_PROVIDERS = {"Claude (CLI)": "claude", "Ollama (local)": "ollama", "OpenAI-compatível": "openai"}
_PROVIDER_LABELS = {v: k for k, v in _PROVIDERS.items()}

# Modelos/devices do Whisper (transcrição) na UI; modelo fora da lista é preservado
# via passthrough (igual ao modelo do resumo).
_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
_WHISPER_DEVICES = ("auto", "cuda", "cpu")

# rótulo amigável -> código de idioma do Whisper ("" = autodetecção). Código fora da
# lista é preservado por passthrough (igual ao modelo do resumo).
_WHISPER_LANGS = {
    "Detectar automaticamente": "",
    "Português": "pt",
    "Inglês": "en",
    "Espanhol": "es",
    "Francês": "fr",
    "Alemão": "de",
    "Italiano": "it",
}
_WHISPER_LANG_LABELS = {v: k for k, v in _WHISPER_LANGS.items()}

# Motor de transcrição (STT): local (faster-whisper) ou nuvem (Groq/OpenAI-compat)
_STT_ENGINES = {"Local (faster-whisper)": "local", "Nuvem (Groq / OpenAI-compat)": "cloud"}
_STT_ENGINE_LABELS = {v: k for k, v in _STT_ENGINES.items()}

# presets da aba Detecção: somam padrões de app desktop e/ou título de janela no
# navegador aos campos correspondentes (Meet é só web; Slack é só app desktop).
_DETECTION_PRESETS = {
    "Teams": {"apps": ["teams"], "titles": ["Microsoft Teams"]},
    "Zoom": {"apps": ["zoom"], "titles": ["Zoom"]},
    "Meet": {"titles": ["Meet"]},
    "Webex": {"apps": ["webex"], "titles": ["Webex"]},
    "Slack": {"apps": ["slack"]},
}


def _csv_merge(existing: str, additions: list[str]) -> str:
    """Acrescenta `additions` a uma lista CSV, sem duplicar (case-insensitive),
    preservando a ordem e o formato 'a, b, c' que detector.*_from consome."""
    items = [p.strip() for p in (existing or "").split(",") if p.strip()]
    seen = {i.lower() for i in items}
    for a in additions:
        if a.lower() not in seen:
            items.append(a)
            seen.add(a.lower())
    return ", ".join(items)


class SettingsWindow:
    def __init__(self, root: tk.Misc, app):
        self.app = app
        self._titlebar_done = False
        self._fitted = False  # clamp de tamanho à tela: só na 1ª exibição (#20)
        self._level_probe = None  # medidor "Testar microfone" (#22)
        self._level_job = None
        self._level_starting = False
        # só vira True quando _load_fields() roda inteiro sem exceção; protege o
        # _save() de gravar campos pela metade (defaults em branco) por cima da
        # config boa do usuário (perda de dados já vista na prática).
        self._fields_loaded = False
        self._refresh_job: str | None = None  # id do after() da cadeia _refresh_status
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.title("ScribaDev — Configurações")
        self.win.configure(bg=_BG)
        self.win.attributes("-alpha", 0.97)
        # altura mínima baixa de propósito: as abas rolam, então o rodapé (Salvar/
        # Fechar) cabe até em telas de notebook baixas. show() faz o clamp à tela. (#20)
        self.win.minsize(720, 380)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        style_notebook(self.win)
        try:
            self.win.iconbitmap(str(util.ICON_ICO))
        except Exception:
            try:
                from PIL import ImageTk

                from .tray import _icon_image

                self._icon = ImageTk.PhotoImage(_icon_image(False))
                self.win.iconphoto(False, self._icon)
            except Exception:
                pass

        body = tk.Frame(self.win, bg=_BG, padx=14, pady=10)
        body.pack(fill="both", expand=True)

        # -- status (global, acima das abas) -----------------------------------
        status_row = tk.Frame(body, bg=_BG)
        status_row.pack(fill="x", pady=(0, 8))
        self._dot = tk.Canvas(status_row, width=10, height=10, bg=_BG, highlightthickness=0)
        self._dot_item = self._dot.create_oval(1, 1, 9, 9, fill=PALETTE["muted"], outline="")
        self._dot.pack(side="left", pady=1)
        self.status_var = tk.StringVar(value="")
        tk.Label(status_row, textvariable=self.status_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 9, "italic")).pack(side="left", padx=6)

        # -- rodapé (fixo na base, empacotado ANTES do notebook: em telas baixas o
        #    conteúdo das abas rola, mas Salvar/Fechar nunca saem da tela) ---- (#20)
        foot = tk.Frame(body, bg=_BG)
        foot.pack(fill="x", side="bottom", pady=(10, 0))
        separator(body).pack(fill="x", side="bottom")
        self.saved_var = tk.StringVar(value="")
        self._saved_label = tk.Label(foot, textvariable=self.saved_var, bg=_BG, fg=PALETTE["ok"], font=FONT)
        self._saved_label.pack(side="left")
        ModernButton(foot, "Salvar", self._save, kind="primary").pack(side="right")
        ModernButton(foot, "Fechar", self.hide).pack(side="right", padx=8)

        # -- abas (cada uma rola sozinha quando o conteúdo não couber) ------------
        self.nb = ttk.Notebook(body)
        self._scrolls: list[ScrollableFrame] = []
        self.tab_rec = self._add_tab("Gravação")
        self.tab_transcription = self._add_tab("Transcrição")
        self.tab_ia = self._add_tab("IA")
        self.tab_detect = self._add_tab("Detecção")
        self.tab_dirs = self._add_tab("Pastas")
        self.tab_about = self._add_tab("Sobre")
        self.nb.pack(fill="both", expand=True)

        self._build_recording_tab()
        self._build_transcription_tab()
        self._build_ia_tab()
        self._build_detection_tab()
        self._build_dirs_tab()
        self._build_about_tab()
        for s in self._scrolls:
            s.bind_mousewheel()

    def _add_tab(self, title: str) -> ScrollableFrame:
        """Cria uma aba rolável no notebook e a devolve como página (para nb.select).
        Os _build_*_tab empacotam o conteúdo em `.body` (mesmo padding das abas antigas)."""
        scroll = ScrollableFrame(self.nb)
        self.nb.add(scroll, text=title)
        scroll.body.configure(padx=14, pady=14)
        self._scrolls.append(scroll)
        return scroll

    def _group(self, tab, title: str) -> tk.LabelFrame:
        """Cria um grupo (box com título) já empacotado na aba; popule o retorno. (#22)"""
        box = group_box(tab, title)
        box.pack(fill="x", pady=(0, 12))
        return box

    # As notas têm janela própria (notes_ui.NotesWindow); aqui ficam só os
    # atalhos de pasta usados pela aba Pastas.

    def _notes_dir(self) -> Path:
        d = self.app.cfg.output.resolved_export_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _open_notes_dir(self) -> None:
        util.open_path(self._notes_dir())

    # ======================================================= aba Sobre =========

    def _build_about_tab(self) -> None:
        from . import updates

        tab = self.tab_about.body
        tk.Label(tab, text="ScribaDev", bg=_BG, fg=PALETTE["text"],
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(tab, text=updates.build_string(), bg=_BG, fg=PALETTE["muted"], font=FONT).pack(anchor="w")
        tk.Label(tab, text="Gravação e transcrição automática de reuniões (Teams, Zoom, Meet) — "
                          "100% local e privado.", bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 9), wraplength=700, justify="left").pack(anchor="w", pady=(2, 10))

        repo = tk.Frame(tab, bg=_BG)
        repo.pack(fill="x", pady=2)
        tk.Label(repo, text="Repositório", bg=_BG, fg=PALETTE["text"], font=FONT_BOLD,
                 width=16, anchor="w").pack(side="left")
        LinkLabel(repo, "github.com/allanrmartins/ScribaDev",
                  lambda: self._open_url("https://github.com/allanrmartins/ScribaDev")).pack(side="left")

        lic = tk.Frame(tab, bg=_BG)
        lic.pack(fill="x", pady=2)
        tk.Label(lic, text="Licença", bg=_BG, fg=PALETTE["text"], font=FONT_BOLD,
                 width=16, anchor="w").pack(side="left")
        tk.Label(lic, text="Elastic License 2.0 © Allan Martins", bg=_BG, fg=PALETTE["muted"],
                 font=FONT).pack(side="left")

        separator(tab).pack(fill="x", pady=(8, 4))
        upd = tk.Frame(tab, bg=_BG)
        upd.pack(fill="x", pady=(2, 2))
        ModernButton(upd, "Verificar atualizações", self._check_updates_about, width=190).pack(side="left")
        # "Atualizar agora" — empacotado só quando há versão nova (em _show_about_update)
        self._about_update_btn = ModernButton(upd, "Atualizar agora", self._do_update_about,
                                              kind="primary", width=150)
        self._about_update_status = tk.StringVar(value="")
        self._about_update_lbl = tk.Label(tab, textvariable=self._about_update_status, bg=_BG,
                                          fg=PALETTE["muted"], font=FONT, anchor="w", wraplength=600, justify="left")
        self._about_update_lbl.pack(anchor="w", pady=(4, 0))

        separator(tab).pack(fill="x", pady=(8, 4))
        tk.Label(tab, text="Componentes e versões", bg=_BG, fg=PALETTE["text"],
                 font=FONT_BOLD).pack(anchor="w", pady=(2, 4))
        _LVL = {"ok": PALETTE["muted"], "warn": "#e0b341", "err": PALETTE["accent"]}
        comps = self._about_components()
        for label, value, level in comps:
            row = tk.Frame(tab, bg=_BG)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=_BG, fg=PALETTE["text"], font=FONT,
                     width=18, anchor="nw").pack(side="left")
            tk.Label(row, text=value, bg=_BG, fg=_LVL.get(level, PALETTE["muted"]), font=FONT,
                     anchor="w", justify="left", wraplength=520).pack(side="left")
        if any(lvl == "err" for _, _, lvl in comps):
            tk.Label(tab, text="⚠ Há um problema na instalação (em vermelho acima). Veja o README "
                              "para instalar/atualizar o que falta.", bg=_BG, fg=PALETTE["accent"],
                     font=("Segoe UI", 9, "bold"), wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))

    def _about_components(self) -> list:
        """[(rótulo, valor, nível)] — nível: ok | warn | err. 'err' vira vermelho na UI."""
        import sys

        def ver(pkg: str):
            try:
                from importlib.metadata import version

                return version(pkg)
            except Exception:
                return None  # não instalado

        def core(label: str, text: str, present: bool) -> tuple:
            return (label, text if present else text + " — AUSENTE (reinstale o app)",
                    "ok" if present else "err")

        fw, ct, pa = ver("faster-whisper"), ver("ctranslate2"), ver("pyaudiowpatch")
        diar_msg, diar_lvl = self._diarization_health(ver)
        torch_v = ver("torch")
        ff_msg, ff_lvl = self._ffmpeg_health()
        return [
            ("Python", sys.version.split()[0], "ok"),
            core("Transcrição", f"faster-whisper {fw or '?'} · ctranslate2 {ct or '?'}", bool(fw and ct)),
            ("Diarização", diar_msg, diar_lvl),
            ("PyTorch", torch_v or "não instalado (opcional — só p/ a diarização)", "ok" if torch_v else "warn"),
            ("GPU", self._gpu_str(), "ok"),
            core("Áudio (WASAPI)", f"pyaudiowpatch {pa or '?'}", bool(pa)),
            ("Compressão de áudio", ff_msg, ff_lvl),
            ("Bandeja / imagens", f"pystray {ver('pystray') or '—'} · Pillow {ver('Pillow') or '—'}", "ok"),
            ("Notificações", f"windows-toasts {ver('windows-toasts') or '—'}", "ok"),
        ]

    def _ffmpeg_health(self) -> tuple:
        """(mensagem, nível) do ffmpeg p/ a aba Sobre. O nível vem de util.ffmpeg_status
        (mesma regra do `scriba doctor`); aqui só montamos o texto exibido."""
        a = self.app.cfg.audio
        fmt = (getattr(a, "archive_format", "opus") or "wav").strip().lower()
        lvl = util.ffmpeg_status(getattr(a, "keep_audio", False), fmt)
        if lvl == "ok":
            return ("ffmpeg no PATH — áudio guardado é compactado", "ok")
        if lvl == "err":
            return (f"ffmpeg AUSENTE no PATH do Windows — o áudio guardado fica em WAV cru (~1,3 GB/h), "
                    f"não {fmt} (~20 MB/h). Instale com 'winget install ffmpeg' (entra no PATH do Windows "
                    f"sozinho — não é uma pasta do ScribaDev), feche e reabra o app. Confira no terminal "
                    f"com 'where ffmpeg'.", "err")
        return ("ffmpeg ausente no PATH do Windows — preciso dele p/ compactar o áudio guardado e p/ "
                "re-transcrever áudio já comprimido", "warn")

    def _diarization_health(self, ver) -> tuple:
        """Saúde da diarização (pyannote) — (mensagem amigável, nível). Leve: usa
        find_spec (não importa o torch pesado, que travaria a janela)."""
        import importlib.util as ilu

        try:
            present = ilu.find_spec("pyannote.audio") is not None
        except Exception as e:
            return (f"erro ao checar a instalação — {self._friendly(e)}", "err")
        if not present:
            return ("não instalada — falta o extra [diarization] (pyannote + torch). Veja o README.", "err")
        try:
            has_torch = ilu.find_spec("torch") is not None
        except Exception:
            has_torch = False
        if not has_torch:
            return ("pyannote presente, mas o PyTorch (torch) está faltando — reinstale o torch.", "err")
        pa = ver("pyannote.audio") or "?"
        dz = self.app.cfg.diarization
        model = getattr(dz, "model", "") or "?"
        if not getattr(dz, "hf_token", ""):
            return (f"pyannote.audio {pa} — falta o token Hugging Face (configure na aba Gravação).", "warn")
        if not getattr(dz, "enabled", False):
            return (f"pyannote.audio {pa} · modelo: {model} — desligada na aba Gravação.", "warn")
        return (f"pyannote.audio {pa} · modelo: {model}", "ok")

    @staticmethod
    def _friendly(e) -> str:
        """Mensagem de erro curta e legível (sem traceback)."""
        if isinstance(e, ModuleNotFoundError) and getattr(e, "name", None):
            return f"falta o pacote '{e.name}'"
        return (str(e) or type(e).__name__).splitlines()[0][:140]

    @staticmethod
    def _gpu_str() -> str:
        try:
            import ctypes

            ctypes.WinDLL("nvcuda.dll")
            return "NVIDIA (CUDA)"
        except OSError:
            return "CPU (sem GPU NVIDIA)"

    def _open_url(self, url: str) -> None:
        import webbrowser

        webbrowser.open(url)

    # -- verificar atualizações na aba Sobre -----------------------------------

    def _set_about_status(self, text: str, color: str) -> None:
        self._about_update_status.set(text)
        self._about_update_lbl.configure(fg=color)

    def _check_updates_about(self) -> None:
        import threading

        self._about_update_btn.pack_forget()
        self._set_about_status("Verificando…", PALETTE["muted"])
        threading.Thread(target=self._about_update_worker, daemon=True).start()

    def _about_update_worker(self) -> None:
        from . import updates

        try:
            latest = updates.latest_version()
        except Exception:
            latest = None
        self.win.after(0, lambda: self._show_about_update(latest))

    def _show_about_update(self, latest) -> None:
        from . import __version__, updates

        self._about_update_btn.pack_forget()
        if latest is None:
            self._set_about_status("Não consegui verificar — sem internet ou serviço indisponível.",
                                   PALETTE["muted"])
        elif updates._is_newer(__version__, latest):
            self._set_about_status(f"Nova versão v{latest} disponível — recomendado atualizar.",
                                   PALETTE["accent"])
            self._about_update_btn.set_text("Atualizar agora" if updates.is_git_install() else "Baixar")
            self._about_update_btn.pack(side="left", padx=(8, 0))
        else:
            self._set_about_status(f"✓ Você está na versão mais recente (v{__version__}).", PALETTE["ok"])

    def _do_update_about(self) -> None:
        from . import updates

        if self.app.is_recording():
            self._set_about_status("Pare a gravação antes de atualizar.", PALETTE["accent"])
            return
        if updates.is_git_install():
            self._set_about_status("Atualizando… (git pull)", PALETTE["muted"])
            self.app.apply_update(self._about_update_done)
        else:
            import webbrowser

            webbrowser.open(updates.download_url())

    def _about_update_done(self, ok: bool, msg: str) -> None:
        if ok:
            self._set_about_status("✓ Atualizado — reiniciando o ScribaDev…", PALETTE["ok"])
            self.win.after(1500, self.app.relaunch)
        else:
            self._set_about_status("✗ " + msg, PALETTE["accent"])

    # ==================================================== aba Gravação =========

    def _build_recording_tab(self) -> None:
        tab = self.tab_rec.body
        dev = self._group(tab, "Dispositivos de áudio")
        self._build_device_selectors(dev)

        auto = self._group(tab, "Gravação automática")
        self.auto_record_var = tk.BooleanVar()
        self._toggle_row(auto, "Gravar automaticamente ao detectar a call", self.auto_record_var)
        self.overlay_var = tk.BooleanVar()
        self._toggle_row(auto, "Mostrar pílula flutuante durante a call", self.overlay_var)
        self.autostart_var = tk.BooleanVar()
        self._toggle_row(auto, "Iniciar com o Windows", self.autostart_var)

        hk_row = tk.Frame(auto, bg=_BG)
        hk_row.pack(fill="x", pady=6)
        tk.Label(hk_row, text="Atalho global gravar/parar", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.hotkey_var = tk.StringVar(value="")
        LinkLabel(hk_row, "limpar", lambda: self.hotkey_var.set("")).pack(side="right", padx=(8, 0))
        ModernButton(hk_row, "Gravar atalho", self._capture_hotkey).pack(side="right", padx=(8, 0))
        self.hotkey_label_var = tk.StringVar(value="desativado")
        tk.Label(hk_row, textvariable=self.hotkey_label_var, bg=PALETTE["field"], fg=PALETTE["text"],
                 font=("Consolas", 9), padx=10, pady=4).pack(side="right")
        self.hotkey_var.trace_add(
            "write", lambda *a: self.hotkey_label_var.set(self.hotkey_var.get() or "desativado")
        )

        min_row = tk.Frame(auto, bg=_BG)
        min_row.pack(fill="x", pady=6)
        tk.Label(min_row, text="Duração mínima para virar nota", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.min_secs_var = tk.IntVar(value=30)
        Stepper(min_row, self.min_secs_var, step=5, lo=0, hi=600).pack(side="right")
        tk.Label(
            auto,
            text="Gravações automáticas mais curtas que isso são ignoradas (tela de pré-join, teste de microfone).\n"
            "Não vale para gravações iniciadas por você (⏺ ou ■).",
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left",
        ).pack(anchor="w", pady=(2, 0))

    # ================================================= aba Transcrição =========

    def _build_transcription_tab(self) -> None:
        tab = self.tab_transcription.body

        motor = self._group(tab, "Motor de transcrição")
        self.stt_conn_status_var = tk.StringVar(value="")
        eng_row = tk.Frame(motor, bg=_BG)
        eng_row.pack(fill="x", pady=(0, 6))
        tk.Label(eng_row, text="Transcrição", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.stt_engine_var = tk.StringVar()
        self._stt_engine_combo = ttk.Combobox(
            eng_row, textvariable=self.stt_engine_var, values=list(_STT_ENGINES),
            state="readonly", width=26, font=FONT,
        )
        self._stt_engine_combo.pack(side="right")
        self._stt_engine_combo.bind("<<ComboboxSelected>>", lambda e: self._on_stt_engine_change())
        self._stt_area = tk.Frame(motor, bg=_BG)
        self._stt_area.pack(fill="x")
        self._build_stt_frames(self._stt_area)

        diar = self._group(tab, "Diarização (separar participantes por voz)")
        self.diar_var = tk.BooleanVar()
        self._toggle_row(diar, "Separar participantes por voz (local)", self.diar_var)
        tok_row = tk.Frame(diar, bg=_BG)
        tok_row.pack(fill="x", pady=(0, 2))
        tk.Label(tok_row, text="Token Hugging Face", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.hf_token_var = tk.StringVar()
        make_secret_entry(tok_row, self.hf_token_var, width=34).pack(side="right")
        hint_row = tk.Frame(diar, bg=_BG)
        hint_row.pack(fill="x", pady=(0, 6))
        tk.Label(
            hint_row,
            text="Token gratuito de leitura em hf.co/settings/tokens — aceite também os termos do modelo:",
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
        ).pack(side="left")
        for label, url_name in (
            ("community-1", "speaker-diarization-community-1"),
            ("diarization-3.1", "speaker-diarization-3.1"),
            ("segmentation-3.0", "segmentation-3.0"),
        ):
            LinkLabel(
                hint_row, label,
                lambda n=url_name: __import__("webbrowser").open(f"https://huggingface.co/pyannote/{n}"),
            ).pack(side="left", padx=3)

        self.ask_speakers_var = tk.BooleanVar()
        self._toggle_row(diar, "Perguntar o nº de participantes ao fim da call", self.ask_speakers_var)
        to_row = tk.Frame(diar, bg=_BG)
        to_row.pack(fill="x", pady=(0, 2))
        tk.Label(to_row, text="Tempo até cair no automático", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.ask_timeout_var = tk.IntVar(value=90)
        Stepper(to_row, self.ask_timeout_var, step=15, lo=0, hi=600).pack(side="right")
        tk.Label(
            diar,
            text="Com a diarização ligada, ao encerrar a call abre uma janela perguntando quantas "
                 "pessoas (além de você) participaram — trava a separação nesse número.\n"
                 "0 s = espera você responder; senão, sem resposta cai no automático.",
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left", wraplength=640,
        ).pack(anchor="w", pady=(2, 0))

        mv_row = tk.Frame(diar, bg=_BG)
        mv_row.pack(fill="x", pady=(6, 0))
        tk.Label(mv_row, text="Limitar vozes (0 = automático)", bg=_BG,
                 fg=PALETTE["text"], font=FONT).pack(side="left")
        self.max_speakers_var = tk.IntVar(value=0)
        Stepper(mv_row, self.max_speakers_var, step=1, lo=0, hi=12, suffix="").pack(side="right")
        tk.Label(mv_row, text="máx", bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(side="right", padx=(8, 4))
        self.min_speakers_var = tk.IntVar(value=0)
        Stepper(mv_row, self.min_speakers_var, step=1, lo=0, hi=12, suffix="").pack(side="right")
        tk.Label(mv_row, text="mín", bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(side="right", padx=(12, 4))
        tk.Label(
            diar,
            text="Independe do popup acima: vale quando ele está desligado ou cai no automático. "
                 "Útil em reuniões recorrentes de tamanho conhecido (ex.: daily de 4).",
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left", wraplength=640,
        ).pack(anchor="w", pady=(2, 0))

        vocab = self._group(tab, "Vocabulário / hotwords")
        tk.Label(
            vocab,
            text="Termos e siglas da sua área p/ o Whisper acertar (ex.: SAP ABAP BAPI Fiori). "
            "Separe por espaço. Em branco = sem vocabulário guiado.",
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left", wraplength=640,
        ).pack(anchor="w", pady=(0, 4))
        self.hotwords = make_text(vocab, height=3)
        self.hotwords.pack(fill="x", pady=(0, 2))

    # -- seletores de dispositivo (mic / loopback) na aba Gravação (#22) -------
    _DEV_DEFAULT = "Padrão do Windows"

    def _build_device_selectors(self, tab) -> None:
        self.mic_device_var = tk.StringVar()
        self.loopback_device_var = tk.StringVar()
        mic_row = tk.Frame(tab, bg=_BG)
        mic_row.pack(fill="x", pady=2)
        tk.Label(mic_row, text="Microfone (Eu)", bg=_BG, fg=PALETTE["text"], font=FONT,
                 width=24, anchor="w").pack(side="left")
        self._mic_combo = ttk.Combobox(mic_row, textvariable=self.mic_device_var, state="readonly",
                                       font=FONT, values=[self._DEV_DEFAULT])
        self._mic_combo.pack(side="right", fill="x", expand=True)
        lb_row = tk.Frame(tab, bg=_BG)
        lb_row.pack(fill="x", pady=2)
        tk.Label(lb_row, text="Capturar saída (Participantes)", bg=_BG, fg=PALETTE["text"], font=FONT,
                 width=24, anchor="w").pack(side="left")
        self._lb_combo = ttk.Combobox(lb_row, textvariable=self.loopback_device_var, state="readonly",
                                      font=FONT, values=[self._DEV_DEFAULT])
        self._lb_combo.pack(side="right", fill="x", expand=True)
        hint = tk.Frame(tab, bg=_BG)
        hint.pack(fill="x", pady=(2, 6))
        tk.Label(hint, text="Vazio = padrão do Windows. Útil no notebook que alterna headset/mic interno.",
                 bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(side="left")
        LinkLabel(hint, "atualizar lista", self._refresh_device_lists).pack(side="right")

        # medidor "Testar microfone" (#22): confere ANTES da call se está entrando som
        test_row = tk.Frame(tab, bg=_BG)
        test_row.pack(fill="x", pady=(2, 2))
        self._mic_test_btn = ModernButton(test_row, "Testar microfone", self._toggle_level_test, width=150)
        self._mic_test_btn.pack(side="left")
        self._level_status = tk.StringVar(value="")
        tk.Label(test_row, textvariable=self._level_status, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8)).pack(side="left", padx=8)
        self._mic_level = self._level_bar(tab, "Eu (microfone)")
        self._lb_level = self._level_bar(tab, "Participantes (saída)")

    def _refresh_device_lists(self) -> None:
        """Enumera os dispositivos num SUBPROCESSO (o PortAudio pode abortar com assert
        de CRT ao enumerar) e popula os combos, sem travar a janela."""
        import threading

        threading.Thread(target=self._device_list_worker, daemon=True, name="devlist").start()

    def _device_list_worker(self) -> None:
        data = util.list_audio_devices()
        self.win.after(0, lambda: self._apply_device_lists(data))

    def _apply_device_lists(self, data) -> None:
        data = data or {}
        self._fill_combo(self._mic_combo, self.mic_device_var, data.get("mics") or [])
        self._fill_combo(self._lb_combo, self.loopback_device_var, data.get("loopbacks") or [])

    def _fill_combo(self, combo, var, names) -> None:
        # preserva o valor atual (substring vinda do config, ou device offline agora)
        seen, vals = set(), []
        for v in [self._DEV_DEFAULT, *names, var.get()]:
            if v and v not in seen:
                seen.add(v)
                vals.append(v)
        combo.configure(values=vals)

    def _dev_value(self, var) -> str:
        v = var.get().strip()
        return "" if v == self._DEV_DEFAULT else v

    # -- medidor de nível "Testar microfone" (#22) ----------------------------

    def _level_bar(self, parent, label: str):
        row = tk.Frame(parent, bg=_BG)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=label, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                 width=22, anchor="w").pack(side="left")
        pb = ttk.Progressbar(row, orient="horizontal", mode="determinate", maximum=100,
                             style="Horizontal.TProgressbar")
        pb.pack(side="left", fill="x", expand=True)
        return pb

    def _toggle_level_test(self) -> None:
        if self._level_probe is not None or self._level_starting:
            self._stop_level_test()
            return
        if self.app.is_recording():
            self._level_status.set("Pare a gravação para testar.")
            return
        import threading

        self._level_starting = True
        self._level_status.set("abrindo… (verificando o áudio)")
        self._mic_test_btn.set_text("Parar teste")
        threading.Thread(target=self._level_probe_worker, daemon=True, name="leveltest").start()

    def _level_probe_worker(self) -> None:
        # pré-checagem em subprocesso (evita o assert de CRT do PortAudio na bandeja)
        ok = util.run_audio_probe() is not None
        self.win.after(0, lambda: self._level_probe_ready(ok))

    def _level_probe_ready(self, ok: bool) -> None:
        if not self._level_starting:  # usuário parou enquanto a sonda verificava
            return
        self._level_starting = False
        if not ok:
            self._level_status.set("áudio indisponível agora.")
            self._mic_test_btn.set_text("Testar microfone")
            return
        try:
            from .recorder import LevelProbe

            self._level_probe = LevelProbe(config_mod.load().audio)
        except Exception as e:
            self._level_status.set("falhou: " + (str(e).splitlines() or [""])[0][:50])
            self._mic_test_btn.set_text("Testar microfone")
            return
        self._level_status.set("fale algo — as barras devem mexer")
        self._poll_levels()

    def _poll_levels(self) -> None:
        if self._level_probe is None:
            return
        try:
            self._mic_level["value"] = min(100.0, self._level_probe.level("mic") * 100)
            self._lb_level["value"] = min(100.0, self._level_probe.level("loopback") * 100)
        except tk.TclError:
            return
        self._level_job = self.win.after(80, self._poll_levels)

    def _stop_level_test(self) -> None:
        self._level_starting = False
        if self._level_job is not None:
            try:
                self.win.after_cancel(self._level_job)
            except Exception:
                pass
            self._level_job = None
        if self._level_probe is not None:
            self._level_probe.stop()
            self._level_probe = None
        try:
            self._mic_level["value"] = 0
            self._lb_level["value"] = 0
        except Exception:
            pass
        self._level_status.set("")
        self._mic_test_btn.set_text("Testar microfone")

    def _build_stt_frames(self, parent) -> None:
        # Local (faster-whisper)
        self._stt_local_frame = tk.Frame(parent, bg=_BG)
        wm = tk.Frame(self._stt_local_frame, bg=_BG)
        wm.pack(fill="x", pady=6)
        tk.Label(wm, text="Modelo do Whisper", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.whisper_model_var = tk.StringVar()
        self._whisper_model_combo = ttk.Combobox(wm, textvariable=self.whisper_model_var,
                                                 values=list(_WHISPER_MODELS), state="readonly", width=16, font=FONT)
        self._whisper_model_combo.pack(side="right")
        wd = tk.Frame(self._stt_local_frame, bg=_BG)
        wd.pack(fill="x", pady=6)
        tk.Label(wd, text="Processamento", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.whisper_device_var = tk.StringVar()
        ttk.Combobox(wd, textvariable=self.whisper_device_var, values=list(_WHISPER_DEVICES),
                     state="readonly", width=10, font=FONT).pack(side="right")
        wl = tk.Frame(self._stt_local_frame, bg=_BG)
        wl.pack(fill="x", pady=6)
        tk.Label(wl, text="Idioma", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.whisper_lang_var = tk.StringVar()
        self._whisper_lang_combo = ttk.Combobox(wl, textvariable=self.whisper_lang_var, values=list(_WHISPER_LANGS),
                                                state="readonly", width=22, font=FONT)
        self._whisper_lang_combo.pack(side="right")
        tk.Label(self._stt_local_frame, justify="left", bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                 text="Modelos maiores transcrevem melhor, porém mais devagar e com mais memória.\n"
                      "Processamento: auto = GPU se houver, senão CPU. Idioma fixo ajuda em trechos curtos/ruído.").pack(anchor="w", pady=(2, 0))

        # Nuvem (Groq / OpenAI-compatível) — opt-in
        self._stt_cloud_frame = tk.Frame(parent, bg=_BG)
        tk.Label(self._stt_cloud_frame, justify="left", bg=_BG, fg=PALETTE["accent"], font=FONT_BOLD, wraplength=640,
                 text="⚠️ O áudio sai da sua máquina e é enviado ao provedor. Avalie os termos de "
                      "privacidade/retenção. O padrão do ScribaDev é 100% local.").pack(anchor="w", pady=(4, 4))
        links = tk.Frame(self._stt_cloud_frame, bg=_BG)
        links.pack(fill="x", pady=(0, 4))
        tk.Label(links, text="Gerar chave Groq:", bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(side="left")
        LinkLabel(links, "console.groq.com/keys",
                  lambda: __import__("webbrowser").open("https://console.groq.com/keys")).pack(side="left", padx=3)
        self.cloud_base_url_var = tk.StringVar()
        self._labeled_entry(self._stt_cloud_frame, "URL base", self.cloud_base_url_var,
                            "Em branco = Groq (https://api.groq.com/openai/v1). Outro provedor: inclua /v1.")
        self.cloud_api_key_var = tk.StringVar()
        self._labeled_entry(self._stt_cloud_frame, "Chave de API (fica só neste computador)", self.cloud_api_key_var,
                            "Sua chave (BYO), guardada em texto no config.toml local.", secret=True)
        self.cloud_model_var = tk.StringVar()
        self._labeled_entry(self._stt_cloud_frame, "Modelo", self.cloud_model_var,
                            "Ex.: whisper-large-v3-turbo · whisper-large-v3.")
        crow = tk.Frame(self._stt_cloud_frame, bg=_BG)
        crow.pack(fill="x", pady=(2, 2))
        ModernButton(crow, "Testar conexão", self._test_stt_connection).pack(side="left")
        tk.Label(crow, textvariable=self.stt_conn_status_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8)).pack(side="left", padx=8)

    def _on_stt_engine_change(self) -> None:
        eng = _STT_ENGINES.get(self.stt_engine_var.get(), "local")
        self.stt_conn_status_var.set("")
        for f in (self._stt_local_frame, self._stt_cloud_frame):
            f.pack_forget()
        (self._stt_cloud_frame if eng == "cloud" else self._stt_local_frame).pack(fill="x")

    def _test_stt_connection(self) -> None:
        from . import stt_cloud

        base = config_mod.load().whisper
        cfg = dataclasses.replace(
            base, engine="cloud",
            cloud_base_url=self.cloud_base_url_var.get().strip(),
            cloud_api_key=self.cloud_api_key_var.get().strip(),
            cloud_model=self.cloud_model_var.get().strip() or base.cloud_model,
        )
        self.stt_conn_status_var.set("testando…")
        self.win.update_idletasks()
        ok, msg = stt_cloud.test_connection(cfg)
        self.stt_conn_status_var.set(("✓ " if ok else "✗ ") + msg)

    def _capture_hotkey(self) -> None:
        """Janelinha que captura a próxima combinação de teclas (Esc cancela)."""
        cap = tk.Toplevel(self.win)
        cap.title("Gravar atalho")
        cap.configure(bg=_BG, padx=24, pady=18)
        cap.resizable(False, False)
        cap.transient(self.win)
        cap.grab_set()
        enable_dark_titlebar(cap)
        info = tk.StringVar(value="Pressione a combinação desejada…\n(precisa ter Ctrl, Alt ou Shift; Esc cancela)")
        tk.Label(cap, textvariable=info, bg=_BG, fg=PALETTE["text"], font=FONT, justify="center").pack()

        def on_key(e: tk.Event) -> str:
            keysym = e.keysym
            if keysym == "Escape":
                cap.destroy()
                return "break"
            if keysym in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Win_L", "Win_R"):
                return "break"  # só modificador: espera a tecla principal
            mods = []
            if e.state & 0x4:
                mods.append("ctrl")
            if e.state & 0x20000:
                mods.append("alt")
            if e.state & 0x1:
                mods.append("shift")
            key = keysym.lower()
            from .hotkey import parse

            spec = "+".join(mods + [key])
            if parse(spec) is None:
                info.set(f"'{spec}' não é suportado.\nUse Ctrl/Alt/Shift + letra, número ou F1–F12.")
                return "break"
            self.hotkey_var.set(spec)
            cap.destroy()
            return "break"

        cap.bind("<KeyPress>", on_key)
        cap.focus_force()

    # ==================================================== aba Detecção =========

    def _build_detection_tab(self) -> None:
        tab = self.tab_detect.body
        box = self._group(tab, "Apps e reuniões monitorados")
        tk.Label(
            box,
            text="Quais apps e reuniões no navegador o ScribaDev monitora (mic aberto = call ativa).\n"
            "Listas separadas por vírgula. Os presets SOMAM aos campos, não apagam.",
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left",
        ).pack(anchor="w", pady=(0, 6))

        self.apps_var = tk.StringVar()
        self._labeled_entry(
            box, "Apps de reunião monitorados", self.apps_var,
            "Parte do nome do processo: teams, zoom, webex, slack.",
        )
        self.browsers_var = tk.StringVar()
        self._labeled_entry(
            box, "Navegadores monitorados", self.browsers_var,
            "Reunião na web é detectada nestes navegadores. Em branco desliga a camada web.",
        )
        self.titles_var = tk.StringVar()
        self._labeled_entry(
            box, "Títulos de janela de reunião no navegador", self.titles_var,
            "Palavra no título da aba: Meet, Microsoft Teams, Zoom, Webex. "
            "Em branco = qualquer site usando o microfone conta.",
        )

        preset_row = tk.Frame(box, bg=_BG)
        preset_row.pack(fill="x", pady=(8, 0))
        tk.Label(preset_row, text="Presets:", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left", padx=(0, 4))
        for name in _DETECTION_PRESETS:
            ModernButton(preset_row, name, lambda n=name: self._apply_detection_preset(n)).pack(side="left", padx=3)

    def _labeled_entry(self, parent, label: str, var: tk.StringVar, hint: str, *, secret: bool = False) -> None:
        tk.Label(parent, text=label, bg=_BG, fg=PALETTE["text"], font=FONT_BOLD).pack(anchor="w", pady=(6, 3))
        if secret:
            make_secret_entry(parent, var).pack(fill="x")
        else:
            make_entry(parent, var).pack(fill="x", ipady=4)
        tk.Label(parent, text=hint, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                 justify="left", wraplength=660).pack(anchor="w", pady=(2, 2))

    def _apply_detection_preset(self, name: str) -> None:
        """Soma os padrões do preset aos campos (app desktop e/ou título no navegador),
        sem duplicar nem apagar o que o usuário já tem."""
        preset = _DETECTION_PRESETS.get(name)
        if not preset:
            return
        if preset.get("apps"):
            self.apps_var.set(_csv_merge(self.apps_var.get(), preset["apps"]))
        if preset.get("titles"):
            self.titles_var.set(_csv_merge(self.titles_var.get(), preset["titles"]))

    # ====================================================== aba Pastas =========

    def _build_dirs_tab(self) -> None:
        tab = self.tab_dirs.body
        pastas = self._group(tab, "Pastas")
        self.export_var = tk.StringVar()
        self._path_row(pastas, "Pasta das notas (.md)", self.export_var, self._browse_export,
                       "Vazio = Documentos\\ScribaDev")
        self.recordings_var = tk.StringVar()
        self._path_row(pastas, "Pasta das gravações", self.recordings_var, self._browse_recordings,
                       "Vazio = C:\\temp\\scribadev\\gravacoes (criada automaticamente)")
        actions = tk.Frame(pastas, bg=_BG)
        actions.pack(anchor="w", pady=(6, 0))
        LinkLabel(actions, "Abrir notas", self._open_notes_dir).pack(side="left")
        tk.Label(actions, text="·", bg=_BG, fg=PALETTE["muted"]).pack(side="left", padx=6)
        LinkLabel(
            actions, "Abrir gravações",
            lambda: util.open_path(self.app.cfg.output.resolved_recordings_dir()),
        ).pack(side="left")

        guardado = self._group(tab, "Áudio guardado")
        self.keep_audio_var = tk.BooleanVar()
        self._toggle_row(guardado, "Manter o áudio da reunião após transcrever", self.keep_audio_var)
        ret_row = tk.Frame(guardado, bg=_BG)
        ret_row.pack(fill="x", pady=6)
        tk.Label(ret_row, text="Apagar gravações já transcritas após (dias)", bg=_BG,
                 fg=PALETTE["text"], font=FONT).pack(side="left")
        self.retention_var = tk.IntVar(value=30)
        Stepper(ret_row, self.retention_var, step=5, lo=0, hi=3650, suffix="").pack(side="right")
        tk.Label(
            guardado,
            text="0 = nunca apagar. Remove a pasta da gravação (áudio + transcrição) em disco;\n"
            "a nota final (.md) na pasta de notas NÃO é tocada.",
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left",
        ).pack(anchor="w", pady=(2, 0))

    # ====================================================== aba Resumo =========

    def _build_ia_tab(self) -> None:
        tab = self.tab_ia.body

        res = self._group(tab, "Resumo automático")
        self.summary_var = tk.BooleanVar()
        self._toggle_row(res, "Gerar resumo estruturado da reunião (IA)", self.summary_var)
        self.conn_status_var = tk.StringVar(value="")
        prov_row = tk.Frame(res, bg=_BG)
        prov_row.pack(fill="x", pady=6)
        tk.Label(prov_row, text="Provedor de IA", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.provider_var = tk.StringVar()
        self._provider_combo = ttk.Combobox(
            prov_row, textvariable=self.provider_var, values=list(_PROVIDERS),
            state="readonly", width=20, font=FONT,
        )
        self._provider_combo.pack(side="right")
        self._provider_combo.bind("<<ComboboxSelected>>", lambda e: self._on_provider_change())
        # área que troca conforme o provedor (frames empilhados, só o ativo é exibido)
        self._prov_area = tk.Frame(res, bg=_BG)
        self._prov_area.pack(fill="x")
        self._build_provider_frames(self._prov_area)

        chat_row = tk.Frame(res, bg=_BG)
        chat_row.pack(fill="x", pady=(10, 0))
        tk.Label(chat_row, text="Modelo do chat \"Perguntar à reunião\"", bg=_BG, fg=PALETTE["text"],
                 font=FONT).pack(side="left")
        self.chat_model_var = tk.StringVar()
        self._chat_model_combo = ttk.Combobox(
            chat_row, textvariable=self.chat_model_var, values=[_CHAT_MODEL_DEFAULT, *_SUMMARY_MODELS],
            state="readonly", width=18, font=FONT,
        )
        self._chat_model_combo.pack(side="right")
        tk.Label(
            res,
            text="Use um modelo mais barato/rápido (ex.: Haiku) p/ perguntar à transcrição e mantenha "
            "o resumo num modelo mais capaz. Aplica-se ao provedor Claude.",
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left", wraplength=620,
        ).pack(anchor="w", pady=(2, 0))

        instr = self._group(tab, "Instruções da ata (prompt.md)")
        head = tk.Frame(instr, bg=_BG)
        head.pack(fill="x")
        LinkLabel(head, "Assistente de perfil…", lambda: self.app.show_wizard()).pack(side="right")
        tk.Label(
            instr,
            text="Este markdown define as seções e regras da ata. A transcrição é anexada após ele. "
            "Salvar grava em " + str(util.PROMPT_PATH),
            bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left", wraplength=620,
        ).pack(anchor="w", pady=(0, 6))
        editor_frame = tk.Frame(instr, bg=_BG)
        editor_frame.pack(fill="x")
        # altura fixa (a aba inteira rola): expand não estica dentro do ScrollableFrame.
        self.prompt_editor = make_text(editor_frame, height=10)
        psb = ttk.Scrollbar(editor_frame, orient="vertical", command=self.prompt_editor.yview)
        self.prompt_editor.configure(yscrollcommand=psb.set)
        psb.pack(side="right", fill="y")
        self.prompt_editor.pack(side="left", fill="both", expand=True)
        ModernButton(instr, "Restaurar prompt padrão", self._restore_default_prompt).pack(anchor="e", pady=(8, 0))

        ctx = self._group(tab, "Contexto para IA (cabeçalho da nota)")
        ctx_head = tk.Frame(ctx, bg=_BG)
        ctx_head.pack(fill="x")
        ModernButton(ctx_head, "Restaurar padrão", self._restore_default_context, width=140).pack(side="right")
        tk.Label(ctx, text="Vai no topo de cada nota dizendo à IA como ler o documento. Personalize "
                          "para a sua área (ou deixe em branco para remover).", bg=_BG,
                 fg=PALETTE["muted"], font=("Segoe UI", 8), justify="left", wraplength=620).pack(anchor="w", pady=(0, 4))
        self.context_editor = make_text(ctx, height=4)
        self.context_editor.pack(fill="x", pady=(0, 2))

    def _restore_default_prompt(self) -> None:
        from .notes import DEFAULT_SUMMARY_PROMPT

        self.prompt_editor.delete("1.0", "end")
        self.prompt_editor.insert("1.0", DEFAULT_SUMMARY_PROMPT)

    def _restore_default_context(self) -> None:
        from .notes import AI_CONTEXT_NOTE

        self.context_editor.delete("1.0", "end")
        self.context_editor.insert("1.0", AI_CONTEXT_NOTE)

    # -- provedor de IA (claude CLI / Ollama / OpenAI-compatível) -------------

    def _build_provider_frames(self, parent) -> None:
        """Monta os 3 frames de provedor; só o ativo é exibido (_on_provider_change)."""
        # Claude (CLI)
        self._claude_frame = tk.Frame(parent, bg=_BG)
        cm = tk.Frame(self._claude_frame, bg=_BG)
        cm.pack(fill="x", pady=6)
        tk.Label(cm, text="Modelo do Claude", bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        self.summary_model_var = tk.StringVar()
        self._model_combo = ttk.Combobox(cm, textvariable=self.summary_model_var,
                                         values=list(_SUMMARY_MODELS), state="readonly", width=14, font=FONT)
        self._model_combo.pack(side="right")
        tk.Label(self._claude_frame, justify="left", bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                 text="Sonnet 4.6: rápido e ótimo para atas. Opus 4.8: mais capaz, porém mais caro.\n"
                      "Requer o claude CLI instalado e logado na máquina.").pack(anchor="w")

        # Ollama (local, sem chave)
        self._ollama_frame = tk.Frame(parent, bg=_BG)
        self.ollama_base_url_var = tk.StringVar()
        self.ollama_model_var = tk.StringVar()
        self._labeled_entry(self._ollama_frame, "URL do Ollama", self.ollama_base_url_var,
                            "Em branco = http://localhost:11434 (Ollama na própria máquina).")
        self._labeled_entry(self._ollama_frame, "Modelo", self.ollama_model_var,
                            "Modelo já baixado (rode `ollama pull <modelo>`). Ex.: llama3.1, qwen2.5, gemma2.")
        self._conn_row(self._ollama_frame)

        # OpenAI-compatível (BYO key) — passo a passo guiado
        self._openai_frame = tk.Frame(parent, bg=_BG)
        self.openai_base_url_var = tk.StringVar()
        self.openai_api_key_var = tk.StringVar()
        self.openai_model_var = tk.StringVar()
        tk.Label(self._openai_frame, justify="left", bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8),
                 wraplength=640,
                 text="Serve OpenAI, Groq, OpenRouter, Qwen e afins. Passo a passo:\n"
                      "1) gere uma chave no painel do provedor  ·  2) cole a chave abaixo  ·  "
                      "3) informe a URL base (inclua /v1)  ·  4) clique em Testar conexão.").pack(anchor="w", pady=(2, 4))
        links = tk.Frame(self._openai_frame, bg=_BG)
        links.pack(fill="x", pady=(0, 4))
        tk.Label(links, text="Gerar chave:", bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(side="left")
        for _label, _url in (("OpenAI", "https://platform.openai.com/api-keys"),
                             ("Groq", "https://console.groq.com/keys"),
                             ("OpenRouter", "https://openrouter.ai/keys")):
            LinkLabel(links, _label, lambda u=_url: __import__("webbrowser").open(u)).pack(side="left", padx=3)
        self._labeled_entry(self._openai_frame, "URL base (inclua /v1)", self.openai_base_url_var,
                            "Ex.: https://api.openai.com/v1 · https://api.groq.com/openai/v1")
        self._labeled_entry(self._openai_frame, "Chave de API (fica só neste computador)", self.openai_api_key_var,
                            "Sua própria chave (BYO), guardada em texto no config.toml local.", secret=True)
        self._labeled_entry(self._openai_frame, "Modelo", self.openai_model_var,
                            "ID do modelo no provedor. Ex.: gpt-4o-mini · llama-3.1-70b-versatile · qwen-2.5-72b.")
        self._conn_row(self._openai_frame)

    def _conn_row(self, frame) -> None:
        row = tk.Frame(frame, bg=_BG)
        row.pack(fill="x", pady=(2, 2))
        ModernButton(row, "Testar conexão", self._test_connection).pack(side="left")
        tk.Label(row, textvariable=self.conn_status_var, bg=_BG, fg=PALETTE["muted"],
                 font=("Segoe UI", 8)).pack(side="left", padx=8)

    def _on_provider_change(self) -> None:
        prov = _PROVIDERS.get(self.provider_var.get(), "claude")
        self.conn_status_var.set("")
        for f in (self._claude_frame, self._ollama_frame, self._openai_frame):
            f.pack_forget()
        {"ollama": self._ollama_frame, "openai": self._openai_frame}.get(prov, self._claude_frame).pack(fill="x")

    def _test_connection(self) -> None:
        from . import ai

        base = config_mod.load().summary
        prov = _PROVIDERS.get(self.provider_var.get(), "claude")
        cfg = dataclasses.replace(
            base, provider=prov,
            model=_SUMMARY_MODELS.get(self.summary_model_var.get(), base.model),
            ollama_base_url=self.ollama_base_url_var.get().strip(),
            openai_base_url=self.openai_base_url_var.get().strip(),
            openai_api_key=self.openai_api_key_var.get().strip(),
            ollama_model=self.ollama_model_var.get().strip() or base.ollama_model,
            openai_model=self.openai_model_var.get().strip() or base.openai_model,
        )
        self.conn_status_var.set("testando…")
        self.win.update_idletasks()
        ok, msg = ai.test_connection(cfg)
        self.conn_status_var.set(("✓ " if ok else "✗ ") + msg)

    # ================================================== blocos reutilizáveis ====

    def _path_row(self, parent, label: str, var: tk.StringVar, browse, hint: str) -> None:
        tk.Label(parent, text=label, bg=_BG, fg=PALETTE["text"], font=FONT_BOLD).pack(anchor="w", pady=(2, 3))
        row = tk.Frame(parent, bg=_BG)
        row.pack(fill="x")
        make_entry(row, var).pack(side="left", fill="x", expand=True, ipady=4)
        ModernButton(row, "Procurar…", browse).pack(side="left", padx=(8, 0))
        tk.Label(parent, text=hint, bg=_BG, fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 10))

    def _toggle_row(self, parent, label: str, var: tk.BooleanVar) -> None:
        row = tk.Frame(parent, bg=_BG)
        row.pack(fill="x", pady=6)
        tk.Label(row, text=label, bg=_BG, fg=PALETTE["text"], font=FONT).pack(side="left")
        ToggleSwitch(row, var).pack(side="right")

    # ===================================================== comportamento ========

    def _browse_export(self) -> None:
        current = self.export_var.get() or str(self.app.cfg.output.resolved_export_dir())
        chosen = filedialog.askdirectory(parent=self.win, initialdir=current, title="Pasta das notas")
        if chosen:
            self.export_var.set(os.path.normpath(chosen))

    def _browse_recordings(self) -> None:
        current = self.recordings_var.get() or str(self.app.cfg.output.resolved_recordings_dir())
        chosen = filedialog.askdirectory(parent=self.win, initialdir=current, title="Pasta das gravações")
        if chosen:
            self.recordings_var.set(os.path.normpath(chosen))

    def _refresh_status(self) -> None:
        # padrão idêntico ao notes_ui._schedule_poll: limpa o id antes de qualquer
        # saída antecipada, para que show() sempre parta de estado limpo
        self._refresh_job = None
        if not self.win.winfo_viewable():
            return
        recording = self.app.is_recording()
        if recording:
            app_name = getattr(self.app, "current_call_app", lambda: None)() or "manual"
            self.status_var.set(f"Gravando ({app_name}) agora")
        else:
            label = getattr(self.app, "detection_label", lambda: "Teams")()
            self.status_var.set(f"Monitorando {label} — tudo certo")
        self._dot.itemconfigure(self._dot_item, fill=PALETTE["accent"] if recording else PALETTE["ok"])
        self._refresh_job = self.win.after(1000, self._refresh_status)

    def _load_fields(self) -> None:
        from . import autostart
        from .notes import ensure_context_file, ensure_prompt_file

        # marca como "não carregado" no começo; só volta a True na última linha,
        # então qualquer exceção no meio deixa a flag em False e bloqueia o _save().
        self._fields_loaded = False
        # sempre do disco: o config pode ter sido editado fora do app
        cfg = config_mod.load()
        self.export_var.set(cfg.output.export_dir)
        self.recordings_var.set(cfg.output.recordings_dir)
        self.auto_record_var.set(cfg.detection.auto_record)
        self.summary_var.set(cfg.summary.enabled)
        # passthrough de modelo customizado: se o ID não está nos 2 fixos, registra
        # o próprio ID como rótulo de si mesmo em ambos os mapas — assim o combobox
        # mostra o ID cru e o Salvar o devolve intacto (sem cair no fallback "Sonnet 4.6")
        _MODEL_LABELS.setdefault(cfg.summary.model, cfg.summary.model)
        _SUMMARY_MODELS.setdefault(cfg.summary.model, cfg.summary.model)
        self.summary_model_var.set(_MODEL_LABELS[cfg.summary.model])
        # atualiza a lista do combobox para o ID customizado aparecer como opção
        self._model_combo.configure(values=list(_SUMMARY_MODELS))
        self.overlay_var.set(cfg.ui.overlay)
        self.keep_audio_var.set(cfg.audio.keep_audio)
        self.retention_var.set(int(getattr(cfg.audio, "retention_days", 30)))
        self.mic_device_var.set(cfg.audio.mic_device or self._DEV_DEFAULT)
        self.loopback_device_var.set(cfg.audio.loopback_device or self._DEV_DEFAULT)
        self._refresh_device_lists()  # enumera em subprocesso e popula os combos
        self.autostart_var.set(autostart.is_enabled())
        self.diar_var.set(cfg.diarization.enabled)
        self.hf_token_var.set(cfg.diarization.hf_token)
        self.ask_speakers_var.set(cfg.diarization.ask_speakers)
        self.ask_timeout_var.set(int(cfg.diarization.ask_speakers_timeout))
        self.max_speakers_var.set(int(getattr(cfg.diarization, "max_speakers", 0)))
        self.min_speakers_var.set(int(getattr(cfg.diarization, "min_speakers", 0)))
        self.hotkey_var.set(cfg.ui.hotkey)
        self.min_secs_var.set(int(cfg.detection.min_call_seconds))
        self.apps_var.set(cfg.detection.apps)
        self.browsers_var.set(cfg.detection.browsers)
        self.titles_var.set(cfg.detection.browser_titles)
        self.hotwords.delete("1.0", "end")
        self.hotwords.insert("1.0", cfg.whisper.hotwords)
        wmodels = list(_WHISPER_MODELS)
        if cfg.whisper.model not in wmodels:   # passthrough: modelo custom no TOML aparece
            wmodels.append(cfg.whisper.model)
        self._whisper_model_combo.configure(values=wmodels)
        self.whisper_model_var.set(cfg.whisper.model)
        self.whisper_device_var.set(cfg.whisper.device if cfg.whisper.device in _WHISPER_DEVICES else "auto")
        # idioma: passthrough de código fora da lista (ex.: 'ja') — igual ao modelo
        lang = cfg.whisper.language or ""
        if lang not in _WHISPER_LANG_LABELS:
            _WHISPER_LANG_LABELS[lang] = lang
            _WHISPER_LANGS[lang] = lang
        self._whisper_lang_combo.configure(values=list(_WHISPER_LANGS))
        self.whisper_lang_var.set(_WHISPER_LANG_LABELS[lang])
        self.stt_engine_var.set(_STT_ENGINE_LABELS.get(cfg.whisper.engine, "Local (faster-whisper)"))
        self.cloud_base_url_var.set(cfg.whisper.cloud_base_url)
        self.cloud_api_key_var.set(cfg.whisper.cloud_api_key)
        self.cloud_model_var.set(cfg.whisper.cloud_model)
        self._on_stt_engine_change()
        self.prompt_editor.delete("1.0", "end")
        self.prompt_editor.insert("1.0", ensure_prompt_file().read_text(encoding="utf-8"))
        self.context_editor.delete("1.0", "end")
        self.context_editor.insert("1.0", ensure_context_file().read_text(encoding="utf-8"))
        self.provider_var.set(_PROVIDER_LABELS.get(cfg.summary.provider, "Claude (CLI)"))
        # campos por-provider com fallback ao legado compartilhado (migra ao salvar);
        # o base_url legado pertencia ao provider que estava ativo quando foi setado
        _prov = cfg.summary.provider
        self.ollama_base_url_var.set(cfg.summary.ollama_base_url or (cfg.summary.base_url if _prov == "ollama" else ""))
        self.openai_base_url_var.set(cfg.summary.openai_base_url or (cfg.summary.base_url if _prov == "openai" else ""))
        self.openai_api_key_var.set(cfg.summary.openai_api_key or cfg.summary.api_key)
        self.ollama_model_var.set(cfg.summary.ollama_model)
        self.openai_model_var.set(cfg.summary.openai_model)
        # modelo do chat: rótulo do ID (passthrough de ID custom), ou "Mesmo do resumo"
        ccm = cfg.summary.chat_model
        if not ccm:
            self.chat_model_var.set(_CHAT_MODEL_DEFAULT)
        else:
            label = _MODEL_LABELS.get(ccm, ccm)
            vals = list(self._chat_model_combo.cget("values"))
            if label not in vals:
                self._chat_model_combo.configure(values=[*vals, label])
            self.chat_model_var.set(label)
        self._on_provider_change()
        self._fields_loaded = True

    def _save(self) -> None:
        # guarda: se _load_fields() não terminou (campos podem estar nos defaults
        # em branco), NÃO salva — senão sobrescreveria a config boa com vazios.
        if not self._fields_loaded:
            self._saved_label.configure(fg=PALETTE["accent"])  # vermelho: aviso de erro, não sucesso
            self.saved_var.set(
                "Não salvei: os campos não carregaram corretamente. "
                "Feche e reabra as Configurações."
            )
            return
        # base = disco (não o cache do app): preserva chaves editadas fora da UI
        # (hotwords, modelos etc.) em vez de revertê-las com valores antigos
        cfg = config_mod.load()
        model = _SUMMARY_MODELS.get(self.summary_model_var.get(), cfg.summary.model)
        _cm = self.chat_model_var.get()
        chat_model = "" if _cm == _CHAT_MODEL_DEFAULT else _SUMMARY_MODELS.get(_cm, _cm)
        new_cfg = dataclasses.replace(
            cfg,
            output=dataclasses.replace(
                cfg.output,
                export_dir=self.export_var.get().strip(),
                recordings_dir=self.recordings_var.get().strip(),
            ),
            summary=dataclasses.replace(
                cfg.summary, enabled=self.summary_var.get(), model=model,
                provider=_PROVIDERS.get(self.provider_var.get(), cfg.summary.provider),
                base_url="",  # legados migrados para os campos por-provider abaixo
                api_key="",
                ollama_base_url=self.ollama_base_url_var.get().strip(),
                openai_base_url=self.openai_base_url_var.get().strip(),
                openai_api_key=self.openai_api_key_var.get().strip(),
                chat_model=chat_model,
                ollama_model=self.ollama_model_var.get().strip() or cfg.summary.ollama_model,
                openai_model=self.openai_model_var.get().strip() or cfg.summary.openai_model,
            ),
            whisper=dataclasses.replace(
                cfg.whisper,
                hotwords=self.hotwords.get("1.0", "end").strip(),
                model=self.whisper_model_var.get().strip() or cfg.whisper.model,
                device=self.whisper_device_var.get().strip() or cfg.whisper.device,
                language=_WHISPER_LANGS.get(self.whisper_lang_var.get(), cfg.whisper.language),
                engine=_STT_ENGINES.get(self.stt_engine_var.get(), cfg.whisper.engine),
                cloud_base_url=self.cloud_base_url_var.get().strip(),
                cloud_api_key=self.cloud_api_key_var.get().strip(),
                cloud_model=self.cloud_model_var.get().strip() or cfg.whisper.cloud_model,
            ),
            ui=dataclasses.replace(cfg.ui, overlay=self.overlay_var.get(), hotkey=self.hotkey_var.get().strip()),
            audio=dataclasses.replace(
                cfg.audio,
                mic_device=self._dev_value(self.mic_device_var),
                loopback_device=self._dev_value(self.loopback_device_var),
                keep_audio=self.keep_audio_var.get(),
                retention_days=int(self.retention_var.get()),
            ),
            detection=dataclasses.replace(
                cfg.detection,
                apps=self.apps_var.get().strip(),
                browsers=self.browsers_var.get().strip(),
                browser_titles=self.titles_var.get().strip(),
                min_call_seconds=float(self.min_secs_var.get()),
                auto_record=self.auto_record_var.get(),
            ),
            diarization=dataclasses.replace(
                cfg.diarization,
                enabled=self.diar_var.get(),
                hf_token=self.hf_token_var.get().strip(),
                ask_speakers=self.ask_speakers_var.get(),
                ask_speakers_timeout=int(self.ask_timeout_var.get()),
                max_speakers=int(self.max_speakers_var.get()),
                min_speakers=int(self.min_speakers_var.get()),
            ),
        )
        config_mod.save(new_cfg)
        self.app.reload_config()

        # prompt.md (vazio = volta ao padrão na geração)
        util.atomic_write_text(util.PROMPT_PATH, self.prompt_editor.get("1.0", "end").strip() + "\n")
        # context.md (cabeçalho "Contexto para IA"; vazio = sem cabeçalho na nota)
        util.atomic_write_text(util.CONTEXT_PATH, self.context_editor.get("1.0", "end").strip() + "\n")

        from . import autostart

        msg = "Salvo ✓"
        warn = False
        if self.autostart_var.get() != autostart.is_enabled():
            if autostart.set_autostart(self.autostart_var.get()) != 0:
                msg = "Salvo, mas o autostart falhou — veja o log"
                warn = True
                self.autostart_var.set(autostart.is_enabled())
        # aviso não-silencioso: apps E navegadores vazios deixam a detecção automática
        # quase inerte (sobra só o fallback interno) — o usuário precisa saber
        if not self.apps_var.get().strip() and not self.browsers_var.get().strip():
            msg = "Salvo — atenção: apps e navegadores vazios, detecção automática quase desligada"
            warn = True
        self._saved_label.configure(fg=PALETTE["accent"] if warn else PALETTE["ok"])
        self.saved_var.set(msg)
        self.win.after(4000, lambda: self.saved_var.set(""))

    # ========================================================= público ==========

    def show(self) -> None:
        # cancela cadeia anterior antes de iniciar nova — padrão de notes_ui._schedule_poll;
        # sem isso, abrir/fechar/reabrir em <1s deixa cadeias paralelas correndo a 1 Hz
        if self._refresh_job:
            self.win.after_cancel(self._refresh_job)
            self._refresh_job = None
        self._load_fields()
        self.win.deiconify()
        if not self._fitted:
            self._fitted = True
            self._fit_to_screen()
        if not self._titlebar_done:
            self._titlebar_done = True
            enable_dark_titlebar(self.win)
        self.win.lift()
        self.win.focus_force()
        self._refresh_status()

    def _fit_to_screen(self) -> None:
        """Dimensiona a janela para mostrar o máximo do conteúdo SEM ultrapassar a
        tela e a centraliza. Em telas baixas (notebook / DPI alto) encolhe e as abas
        rolam — o rodapé com Salvar/Fechar fica sempre visível. (#20)"""
        self.win.update_idletasks()
        canvas_req = self._scrolls[0].canvas_reqheight() if self._scrolls else 420
        chrome = self.win.winfo_reqheight() - canvas_req  # status + abas + rodapé + paddings
        content = max((s.body.winfo_reqheight() for s in self._scrolls), default=canvas_req)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        # teto a 84% da tela: deixa folga p/ barra de tarefas + título e garante o rodapé
        # (Salvar/Fechar) visível mesmo quando a maior aba é alta (#20/#22)
        avail = int(sh * 0.84)
        w = min(max(self.win.winfo_reqwidth(), 720), sw - 40)
        # teto absoluto (760): abre compacta — a aba mais alta rola; o usuário amplia se quiser
        h = max(360, min(chrome + content, avail, 760))
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 5)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    def hide(self) -> None:
        self._stop_level_test()  # não deixa streams de teste de áudio abertos ao fechar
        self.win.withdraw()
