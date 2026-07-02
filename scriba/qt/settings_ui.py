"""Janela de Configurações em PySide6 (fase B / #50). Porta `scriba/settings_ui.py`.

É uma forma sobre os dataclasses de `config.py` (7 seções). Em vez de código
manual por campo (como no tk), usa um REGISTRO declarativo: cada linha registra
(widget, seção, atributo, tipo), e `_load`/`_save` iteram esse registro. Isso
mantém o round-trip (a parte crítica — não gravar campo pela metade por cima da
config boa) simples e testável. Guard `_loaded`: só salva depois de carregar
inteiro (paridade com o `_fields_loaded` do tk).

SLICE 1 (esta): abas Gravação, Transcrição, IA, Detecção, Pastas + load/save de
TODOS os campos do config. SLICE 2 (a seguir): editor do prompt.md + botão do
wizard, aba Sobre (saúde dos componentes/updates) e o medidor "Testar microfone".
Integração real com ScribaApp = fase C.
"""

from __future__ import annotations

import dataclasses
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import util
from . import theme, widgets

log = logging.getLogger("scriba.qt.settings_ui")

_ARCHIVE = {"Opus (~20 MB/h)": "opus", "FLAC (~110 MB/h)": "flac", "WAV (cru)": "wav"}
_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
_WHISPER_DEVICES = {"Automático": "auto", "GPU (CUDA)": "cuda", "CPU": "cpu"}
_WHISPER_LANGS = {"Detectar automaticamente": "", "Português": "pt", "Inglês": "en",
                  "Espanhol": "es", "Francês": "fr", "Alemão": "de", "Italiano": "it"}
_STT_ENGINES = {"Local (faster-whisper)": "local", "Nuvem (Groq / OpenAI-compat)": "cloud"}
_PROVIDERS = {"Claude (CLI)": "claude", "Ollama (local)": "ollama", "OpenAI-compatível": "openai"}
_SUMMARY_MODELS = {"Haiku 4.5": "claude-haiku-4-5", "Sonnet 4.6": "claude-sonnet-4-6",
                   "Opus 4.8": "claude-opus-4-8"}
_CHAT_MODELS = {"Mesmo do resumo": "", "Haiku 4.5": "claude-haiku-4-5",
                "Sonnet 4.6": "claude-sonnet-4-6", "Opus 4.8": "claude-opus-4-8"}


class SettingsWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._titlebar_done = False
        self._loaded = False
        # registro: (widget, seção, atributo, kind, choices|None)
        self._fields: list[tuple] = []

        self.setWindowTitle("ScribaDev — Configurações")
        self.setMinimumSize(720, 480)
        self.setWindowOpacity(0.98)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs, 1)
        self._build_recording_tab()
        self._build_transcription_tab()
        self._build_ia_tab()
        self._build_detection_tab()
        self._build_dirs_tab()

        foot = QHBoxLayout()
        self._saved = QLabel(""); self._saved.setProperty("role", "ok")
        foot.addWidget(self._saved); foot.addStretch(1)
        foot.addWidget(widgets.ModernButton("Fechar", self.hide))
        foot.addWidget(widgets.ModernButton("Salvar", self._save, kind="primary"))
        root.addLayout(foot)

        self._load()

    # -- helpers de construção -----------------------------------------------

    def _tab(self, title: str) -> QFormLayout:
        page = QWidget()
        outer = QVBoxLayout(page); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        form = QFormLayout(inner)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignLeft)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self._tabs.addTab(page, title)
        return form

    def _group(self, form: QFormLayout, title: str) -> QFormLayout:
        box = QGroupBox(title)
        gl = QFormLayout(box)
        gl.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.addRow(box)
        return gl

    def _text(self, form, label, section, attr, secret=False, hint=None):
        e = QLineEdit()
        if secret:
            e.setEchoMode(QLineEdit.Password)
        self._fields.append((e, section, attr, "text", None))
        self._row(form, label, e, hint)
        return e

    def _check(self, form, label, section, attr, hint=None):
        c = QCheckBox()
        self._fields.append((c, section, attr, "bool", None))
        self._row(form, label, c, hint)
        return c

    def _int(self, form, label, section, attr, lo=0, hi=100000, hint=None):
        s = QSpinBox(); s.setRange(lo, hi)
        self._fields.append((s, section, attr, "int", None))
        self._row(form, label, s, hint)
        return s

    def _float(self, form, label, section, attr, lo=0.0, hi=100000.0, step=0.5, hint=None):
        s = QDoubleSpinBox(); s.setRange(lo, hi); s.setSingleStep(step); s.setDecimals(2)
        self._fields.append((s, section, attr, "float", None))
        self._row(form, label, s, hint)
        return s

    def _choice(self, form, label, section, attr, choices: dict, editable=False, hint=None):
        c = QComboBox(); c.setEditable(editable)
        for lbl, val in choices.items():
            c.addItem(lbl, val)
        self._fields.append((c, section, attr, "choice", choices))
        self._row(form, label, c, hint)
        return c

    def _list_choice(self, form, label, section, attr, values: tuple, hint=None):
        """Combo editável cujo TEXTO é o valor (modelos Whisper: preset + passthrough)."""
        c = QComboBox(); c.setEditable(True)
        c.addItems(list(values))
        self._fields.append((c, section, attr, "editable_text", None))
        self._row(form, label, c, hint)
        return c

    def _row(self, form: QFormLayout, label: str, widget: QWidget, hint: str | None) -> None:
        if hint:
            wrap = QWidget()
            v = QVBoxLayout(wrap); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(1)
            v.addWidget(widget)
            h = QLabel(hint); h.setProperty("role", "muted"); h.setWordWrap(True)
            h.setStyleSheet("font-size:8pt;")
            v.addWidget(h)
            form.addRow(label, wrap)
        else:
            form.addRow(label, widget)

    # -- abas ----------------------------------------------------------------

    def _build_recording_tab(self) -> None:
        f = self._tab("Gravação")
        au = self._group(f, "Áudio")
        self._text(au, "Microfone", "audio", "mic_device", hint="vazio = padrão do Windows (veja: scribadev devices)")
        self._text(au, "Áudio do sistema", "audio", "loopback_device", hint="vazio = saída padrão do Windows")
        self._check(au, "Manter o áudio após transcrever", "audio", "keep_audio")
        self._choice(au, "Formato do áudio", "audio", "archive_format", _ARCHIVE)
        self._int(au, "Apagar gravação após (dias)", "audio", "retention_days", 0, 3650,
                  hint="0 = nunca; só a pasta da gravação, a nota .md não é tocada")

        dz = self._group(f, "Diarização (separar vozes)")
        self._check(dz, "Ativar diarização", "diarization", "enabled")
        self._text(dz, "Token Hugging Face", "diarization", "hf_token", secret=True,
                   hint="hf.co/settings/tokens (grátis); cifrado com DPAPI ao salvar")
        self._int(dz, "Máx. de vozes", "diarization", "max_speakers", 0, 50, hint="0 = automático")
        self._int(dz, "Mín. de vozes", "diarization", "min_speakers", 0, 50, hint="0 = automático")
        self._check(dz, "Perguntar nº de participantes ao fim", "diarization", "ask_speakers")
        self._int(dz, "Timeout da pergunta (s)", "diarization", "ask_speakers_timeout", 0, 600,
                  hint="0 = espera indefinida")
        self._int(dz, "Diarizar em blocos de (min)", "diarization", "chunk_minutes", 0, 60,
                  hint="0 = inteiro; blocos evitam estourar a VRAM em reuniões longas")

        ui = self._group(f, "Interface e atalhos")
        self._check(ui, "Pílula flutuante durante a gravação", "ui", "overlay")
        self._text(ui, "Atalho gravar/parar", "ui", "hotkey", hint="ex.: ctrl+alt+r (vazio desativa)")
        self._text(ui, "Atalho nova call (dividir)", "ui", "hotkey_split", hint="vazio desativa")

    def _build_transcription_tab(self) -> None:
        f = self._tab("Transcrição")
        self._choice(f, "Motor", "whisper", "engine", _STT_ENGINES)
        self._list_choice(f, "Modelo", "whisper", "model", _WHISPER_MODELS)
        self._choice(f, "Dispositivo", "whisper", "device", _WHISPER_DEVICES)
        self._choice(f, "Idioma", "whisper", "language", _WHISPER_LANGS)
        self._text(f, "Vocabulário (hotwords)", "whisper", "hotwords",
                   hint="termos da sua área que guiam a transcrição")
        av = self._group(f, "Avançado")
        self._int(av, "Batch size", "whisper", "batch_size", 0, 64, hint="0 desliga o lote")
        self._int(av, "Beam size", "whisper", "beam_size", 0, 10)
        self._int(av, "Threads de CPU", "whisper", "cpu_threads", 0, 64, hint="0 = automático")
        self._int(av, "VAD silêncio mín. (ms)", "whisper", "vad_min_silence_ms", 0, 5000, hint="0 = padrão")
        self._float(av, "VAD limiar (0..1)", "whisper", "vad_threshold", 0.0, 1.0, 0.05, hint="0 = padrão")
        cl = self._group(f, "Nuvem (só com motor = Nuvem)")
        self._text(cl, "Endpoint", "whisper", "cloud_base_url", hint="vazio = Groq")
        self._text(cl, "Chave da API", "whisper", "cloud_api_key", secret=True)
        self._text(cl, "Modelo (nuvem)", "whisper", "cloud_model")

    def _build_ia_tab(self) -> None:
        f = self._tab("IA")
        self._check(f, "Gerar resumo (IA)", "summary", "enabled")
        self._choice(f, "Provedor", "summary", "provider", _PROVIDERS)
        self._choice(f, "Modelo (Claude)", "summary", "model", _SUMMARY_MODELS, editable=True)
        self._choice(f, "Modelo do chat", "summary", "chat_model", _CHAT_MODELS, editable=True)
        self._int(f, "Timeout (s)", "summary", "timeout_seconds", 30, 3600)
        ol = self._group(f, "Ollama")
        self._text(ol, "Modelo", "summary", "ollama_model")
        self._text(ol, "Endpoint", "summary", "ollama_base_url", hint="vazio = http://localhost:11434")
        op = self._group(f, "OpenAI-compatível")
        self._text(op, "Modelo", "summary", "openai_model")
        self._text(op, "Endpoint", "summary", "openai_base_url", hint="inclua /v1")
        self._text(op, "Chave da API", "summary", "openai_api_key", secret=True)

    def _build_detection_tab(self) -> None:
        f = self._tab("Detecção")
        self._check(f, "Gravar sozinho ao detectar a call", "detection", "auto_record")
        self._text(f, "Apps (desktop)", "detection", "apps", hint="ex.: teams, zoom")
        self._text(f, "Navegadores", "detection", "browsers", hint="vazio desliga a detecção no navegador")
        self._text(f, "Títulos de reunião", "detection", "browser_titles",
                   hint="vazio = qualquer site usando o mic")
        self._float(f, "Poll (s)", "detection", "poll_seconds", 0.5, 60)
        self._float(f, "Tolerância / grace (s)", "detection", "grace_seconds", 0, 120)
        self._float(f, "Gap p/ dividir call (s)", "detection", "split_gap_seconds", 0, 60, hint="0 = nunca")
        self._float(f, "Duração mínima (s)", "detection", "min_call_seconds", 0, 600)
        self._float(f, "Duração máxima (h)", "detection", "max_call_hours", 0.5, 24)

    def _build_dirs_tab(self) -> None:
        f = self._tab("Pastas")
        self._dir_row(f, "Notas (.md)", "output", "export_dir", "vazio = Documentos\\ScribaDev")
        self._dir_row(f, "Gravações", "output", "recordings_dir", "vazio = C:\\temp\\scribadev\\gravacoes")

    def _dir_row(self, form, label, section, attr, hint) -> None:
        e = QLineEdit()
        self._fields.append((e, section, attr, "text", None))
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(e, 1)
        h.addWidget(widgets.ModernButton("…", lambda: self._pick_dir(e)))
        self._row(form, label, row, hint)

    def _pick_dir(self, entry: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Escolher pasta", entry.text() or "")
        if d:
            entry.setText(d)

    # -- load / save ---------------------------------------------------------

    def _widget_get(self, widget, kind, choices):
        if kind == "text":
            return widget.text().strip()
        if kind == "bool":
            return widget.isChecked()
        if kind in ("int", "float"):
            return widget.value()
        if kind == "editable_text":
            return widget.currentText().strip()
        if kind == "choice":
            return widget.currentData()
        return None

    def _widget_set(self, widget, kind, choices, value) -> None:
        if kind == "text":
            widget.setText(str(value or ""))
        elif kind == "bool":
            widget.setChecked(bool(value))
        elif kind == "int":
            widget.setValue(int(value or 0))
        elif kind == "float":
            widget.setValue(float(value or 0))
        elif kind == "editable_text":
            widget.setCurrentText(str(value or ""))   # passthrough: mostra valor fora da lista
        elif kind == "choice":
            idx = widget.findData(value)
            if idx < 0:                                 # passthrough: valor fora do mapa
                widget.addItem(str(value), value)
                idx = widget.findData(value)
            widget.setCurrentIndex(idx)

    def _load(self) -> None:
        cfg = self.app.cfg
        try:
            for widget, section, attr, kind, choices in self._fields:
                self._widget_set(widget, kind, choices, getattr(getattr(cfg, section), attr))
        except Exception:
            log.exception("settings: falha ao carregar os campos")
            self._loaded = False
            return
        self._loaded = True

    def _save(self) -> None:
        if not self._loaded:
            self._saved.setText("")   # guard: não grava por cima da config boa se não carregou
            return
        cfg = self.app.cfg
        by_section: dict[str, dict] = {}
        for widget, section, attr, kind, choices in self._fields:
            by_section.setdefault(section, {})[attr] = self._widget_get(widget, kind, choices)
        new = cfg
        for section, values in by_section.items():
            new = dataclasses.replace(new, **{section: dataclasses.replace(getattr(new, section), **values)})
        config_mod.save(new)
        if self.app is not None:
            try:
                self.app.reload_config()
            except Exception:
                log.exception("settings: reload_config falhou")
        self._saved.setText("✓ Salvo — as próximas reuniões usam estas configurações.")

    # -- janela --------------------------------------------------------------

    def show(self) -> None:  # noqa: A003
        super().show()
        self.raise_()
        self.activateWindow()
        if not self._titlebar_done:
            self._titlebar_done = True
            widgets.enable_dark_titlebar(self)
        self._load()   # relê (o config pode ter mudado)

    def hide(self) -> None:  # noqa: A003
        super().hide()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


# --------------------------------------------------------------- harness ------

def main() -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)

    class _App:
        cfg = config_mod.load()

        def reload_config(self):
            self.cfg = config_mod.load()

    win = SettingsWindow(_App())
    win.resize(760, 640)
    win.show()
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()
    os._exit(rc)
