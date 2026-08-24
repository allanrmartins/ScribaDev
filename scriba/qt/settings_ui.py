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
import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QTimer, Signal

from .. import config as config_mod
from .. import plat, util
from . import theme, widgets

log = logging.getLogger("scriba.qt.settings_ui")


def _pkg_version(pkg: str, module: str | None = None) -> str | None:
    """Versão de um pacote p/ a aba Sobre — honesta também no app CONGELADO.

    `importlib.metadata.version()` lê o dist-info, que o bundle PyInstaller NÃO
    coleta: no instalador ela falha com o pacote PRESENTE e funcionando, e o Sobre
    acusava "AUSENTE (reinstale o app)" p/ faster-whisper/ctranslate2/pyaudiowpatch
    (report de usuário). Antes de acusar ausência, confere o MÓDULO via find_spec
    (sem importar — torch é pesado demais p/ abrir uma aba): achou → "presente"
    (sem número mesmo, o bundle não tem a metadata). None = ausente de verdade."""
    try:
        from importlib.metadata import version

        return version(pkg)
    except Exception:
        pass
    try:
        import importlib.util as ilu

        if ilu.find_spec(module or pkg) is not None:
            return "presente"
    except Exception:
        pass
    return None


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


def _csv_merge(existing: str, additions: list[str]) -> str:
    """Acrescenta `additions` a uma lista CSV, sem duplicar (case-insensitive),
    preservando a ordem e o formato 'a, b, c' que detector.*_from consome. Puro;
    usado pelos presets da aba Detecção (#21 — UI dos presets fica p/ o #54)."""
    items = [p.strip() for p in (existing or "").split(",") if p.strip()]
    seen = {i.lower() for i in items}
    for a in additions:
        if a.lower() not in seen:
            items.append(a)
            seen.add(a.lower())
    return ", ".join(items)


# --- campo secreto com botão de mostrar/ocultar (#71) -----------------------

def _wrap_secret(field) -> QWidget:
    """Envolve um QLineEdit-senha com um botão de OLHO (mostrar/ocultar), pedido no #71
    (o token do Hugging Face e as chaves de API ficavam sempre mascarados, sem conferir).
    Alterna o echoMode e troca o ícone eye<->eye-off. `field` segue sendo o widget de
    valor (registrado em _fields); só o container visual muda."""
    row = QWidget()
    h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
    btn = widgets.icon_button("eye", "Mostrar o valor")

    def toggle() -> None:
        hidden = field.echoMode() == QLineEdit.Password
        field.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
        btn.setIcon(theme.qicon("eye-off" if hidden else "eye"))
        btn.setToolTip("Ocultar o valor" if hidden else "Mostrar o valor")

    btn.clicked.connect(toggle)
    h.addWidget(field, 1)
    h.addWidget(btn, 0)
    return row


# --- seletor de temas com preview (#70) -------------------------------------

def _pl(text: str, style: str = "", *, wrap: bool = False) -> QLabel:
    """QLabel de texto puro com estilo inline — átomo dos previews de tema."""
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.PlainText)
    if wrap:
        lbl.setWordWrap(True)
    if style:
        lbl.setStyleSheet(style)
    return lbl


def _mini_home(th: theme.Theme) -> QWidget:
    """Miniatura ENXUTA da capa nas cores de `th` (um tema específico, não o ativo), com
    dados fictícios: header + pílulas, card de call com o botão de acento, stats e o card
    de Notas (a borda de ACENTO — o que o bug #70 sujava). Sem separadores/linhas e sem a
    seção de pendências: só o essencial p/ ler o tema, como a capa (que não tem linhas)."""
    xs, xxs = f"font-size:{theme.zpt(7)}pt;", f"font-size:{theme.zpt(6)}pt;"
    # Seletor #id em CADA QFrame: um stylesheet sem seletor vaza `border`/`background`
    # para os QLabels filhos (era o que desenhava "linhas"/caixas em volta das notas).
    root = QFrame(); root.setObjectName("miniRoot")
    root.setStyleSheet(f"#miniRoot {{ background:{th.bg}; border:1px solid {th.border_strong}; border-radius:7px; }}")
    v = QVBoxLayout(root); v.setContentsMargins(7, 6, 7, 7); v.setSpacing(4)

    hd = QHBoxLayout(); hd.setSpacing(3)
    hd.addWidget(_pl("ScribaDev", f"color:{th.text}; font-weight:bold; {xs}"))
    hd.addStretch(1)
    hd.addWidget(_pl("Notas", f"background:{th.accent}; color:{th.on_accent}; {xxs} padding:1px 5px; border-radius:4px;"))
    hd.addWidget(_pl("Log", f"background:{th.surface}; color:{th.text}; {xxs} padding:1px 5px; border-radius:4px;"))
    v.addLayout(hd)

    call = QFrame(); call.setObjectName("miniCall")
    call.setStyleSheet(f"#miniCall {{ background:{th.surface}; border-radius:5px; }}")
    cl = QHBoxLayout(call); cl.setContentsMargins(6, 4, 6, 4); cl.setSpacing(5)
    cl.addWidget(_pl("Nenhuma ligação", f"color:{th.muted}; {xxs}"))
    cl.addStretch(1)
    cl.addWidget(_pl("● Gravar", f"background:{th.accent}; color:{th.on_accent}; {xxs} font-weight:bold; padding:2px 6px; border-radius:4px;"))
    v.addWidget(call)

    v.addWidget(_pl("46 reuniões · 17h59 · 11 clientes", f"color:{th.muted}; {xxs}"))

    nc = QFrame(); nc.setObjectName("miniNotes")
    nc.setStyleSheet(f"#miniNotes {{ background:{th.surface}; border:1px solid {th.accent}; border-radius:5px; }}")
    nl = QVBoxLayout(nc); nl.setContentsMargins(7, 5, 7, 6); nl.setSpacing(3)
    nl.addWidget(_pl("Notas · recentes", f"color:{th.text}; font-weight:bold; {xxs}"))
    nl.addWidget(_pl("Nota 1", f"color:{th.text}; {xs}"))
    nl.addWidget(_pl("Nota 2", f"color:{th.text}; {xs}"))
    v.addWidget(nc)
    return root


class _AutoOption(QFrame):
    """Opção 'Automático' (segue o SO) no topo do seletor — sem miniatura própria,
    porque não é uma cor fixa. Clicar aplica o modo automático (on_pick(None))."""

    def __init__(self, selected, on_pick):
        super().__init__()
        self._value = None
        self._on_pick = on_pick
        self.setObjectName("autoopt")
        self.setCursor(Qt.PointingHandCursor)
        act = theme.active()
        edge = act.accent if selected else act.border
        self.setStyleSheet(f"#autoopt {{ border:{2 if selected else 1}px solid {edge};"
                           f" border-radius:9px; background:{act.surface}; }}")
        h = QHBoxLayout(self); h.setContentsMargins(12, 9, 12, 9); h.setSpacing(9)
        h.addWidget(_pl("●" if selected else "○",
                        f"color:{act.accent if selected else act.muted}; font-size:{theme.zpt(12)}pt;"))
        col = QVBoxLayout(); col.setSpacing(1)
        col.addWidget(_pl("Automático", f"color:{act.text}; font-weight:bold; font-size:{theme.zpt(10)}pt;"))
        col.addWidget(_pl(f"Segue o modo claro/escuro do {util.so_nome()}",
                          f"color:{act.muted}; font-size:{theme.zpt(8)}pt;"))
        h.addLayout(col); h.addStretch(1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._on_pick(None)


class _ThemeCard(QFrame):
    """Card selecionável de UM tema: rótulo + marcador + a miniatura da capa nas cores do
    tema. `value` = slug. Clicar chama on_pick(value). O 'chrome' (borda/fundo/rótulo) usa
    o tema ATIVO; a miniatura usa `mini_th`."""

    def __init__(self, mini_th, label, value, selected, on_pick):
        super().__init__()
        self._value = value
        self._on_pick = on_pick
        self.setObjectName("tcard")
        self.setCursor(Qt.PointingHandCursor)
        act = theme.active()
        edge = act.accent if selected else act.border
        self.setStyleSheet(f"#tcard {{ border:{2 if selected else 1}px solid {edge};"
                           f" border-radius:9px; background:{act.surface}; }}")
        v = QVBoxLayout(self); v.setContentsMargins(9, 8, 9, 9); v.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(_pl(label, f"color:{act.text}; font-weight:bold; font-size:{theme.zpt(9.5)}pt;"))
        top.addStretch(1)
        top.addWidget(_pl("●" if selected else "○",
                          f"color:{act.accent if selected else act.muted}; font-size:{theme.zpt(11)}pt;"))
        v.addLayout(top)
        v.addWidget(_mini_home(mini_th))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._on_pick(self._value)


class SettingsWindow(QWidget):
    _about_ready = Signal(object)     # marshaling da checagem de update (thread -> UI)
    _devices_ready = Signal(object)   # marshaling da enumeração de dispositivos (thread -> UI)
    _ts_done = Signal(str)            # marshaling da ativação/desativação do timesheet (#126)
    _comp_progress = Signal(str)      # linha de status do download de componentes (#154)
    _comp_frac = Signal(int)          # 0..1000: % real da barra de componentes
    _comp_done = Signal(bool, str)    # fim do worker de componentes
    _inline_bg = False                # testes: roda o worker de componentes na própria thread

    def __init__(self, app):
        super().__init__()
        self.app = app
        self._titlebar_done = False
        self._loaded = False
        self._level_probe = None
        # registro: (widget, seção, atributo, kind, choices|None)
        self._fields: list[tuple] = []
        self._device_combos: list[tuple] = []   # (combo, 'mics'|'loopbacks') p/ popular async
        self._devices_loaded = False

        self.setWindowTitle("ScribaDev — Configurações")
        self.setMinimumSize(760, 480)
        self.setWindowOpacity(0.98)
        widgets.remember_geometry(self, "qt_settings", default=(180, 120, 880, 680))

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)

        self._tabs = QTabWidget()
        self._tabs.setUsesScrollButtons(False)   # 7 abas: elide em telas estreitas, sem setas ◄►
        self._tabs.setElideMode(Qt.ElideRight)
        root.addWidget(self._tabs, 1)
        self._build_recording_tab()
        self._build_transcription_tab()
        self._build_ia_tab()
        self._build_detection_tab()
        self._build_dirs_tab()
        self._build_appearance_tab()
        self._build_timesheet_tab()
        self._build_about_tab()
        self._about_ready.connect(self._show_about_update)
        self._devices_ready.connect(self._fill_devices)
        self._ts_done.connect(self._on_timesheet_done)

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

    def _text(self, form, label, section, attr, secret=False, placeholder="", tooltip="", hint=None):
        e = QLineEdit()
        if placeholder:
            e.setPlaceholderText(placeholder)
        if tooltip:
            widgets.add_tooltip(e, tooltip)
        self._fields.append((e, section, attr, "text", None))
        if secret:
            e.setEchoMode(QLineEdit.Password)
            self._row(form, label, _wrap_secret(e), hint)   # campo + botão de olho (#71)
        else:
            self._row(form, label, e, hint)
        return e

    def _bigtext(self, form, label, section, attr, placeholder="", tooltip="", rows=3):
        """Campo de VÁRIAS linhas (QPlainTextEdit, com word-wrap) p/ valores longos como
        hotwords. O config guarda uma string única: o get normaliza o whitespace (quebras
        e espaços viram espaço simples), então digitar em linhas não muda o formato salvo."""
        e = QPlainTextEdit()
        e.setPlaceholderText(placeholder)
        if tooltip:
            widgets.add_tooltip(e, tooltip)
        e.setFixedHeight(e.fontMetrics().lineSpacing() * rows + 16)
        self._fields.append((e, section, attr, "bigtext", None))
        self._row(form, label, e, None)
        return e

    def _check(self, form, label, section, attr, hint=None):
        c = widgets.AnimatedCheckBox()
        self._fields.append((c, section, attr, "bool", None))
        self._row(form, label, c, hint)
        return c

    def _int(self, form, label, section, attr, lo=0, hi=100000, hint=None):
        s = QSpinBox(); s.setRange(lo, hi); widgets.no_wheel_steal(s)
        self._fields.append((s, section, attr, "int", None))
        self._row(form, label, s, hint)
        return s

    def _float(self, form, label, section, attr, lo=0.0, hi=100000.0, step=0.5, hint=None):
        s = QDoubleSpinBox(); s.setRange(lo, hi); s.setSingleStep(step); s.setDecimals(2)
        widgets.no_wheel_steal(s)
        self._fields.append((s, section, attr, "float", None))
        self._row(form, label, s, hint)
        return s

    def _choice(self, form, label, section, attr, choices: dict, editable=False, hint=None):
        c = QComboBox(); c.setEditable(editable); widgets.no_wheel_steal(c)
        for lbl, val in choices.items():
            c.addItem(lbl, val)
        self._fields.append((c, section, attr, "choice", choices))
        self._row(form, label, c, hint)
        return c

    def _list_choice(self, form, label, section, attr, values: tuple, hint=None):
        """Dropdown NÃO editável cujo TEXTO é o valor (modelos Whisper). Abre ao clicar; um
        valor salvo fora da lista é preservado como item no _widget_set (só exibição)."""
        c = QComboBox(); widgets.no_wheel_steal(c)
        c.addItems(list(values))
        self._fields.append((c, section, attr, "editable_text", None))
        self._row(form, label, c, hint)
        return c

    def _device_choice(self, form, label, section, attr, key, tooltip=""):
        """Combo EDITÁVEL de dispositivo de áudio: '(padrão do SO)' [valor ''] + a
        lista enumerada (populada em thread no 1º show). Editável = passthrough do valor
        salvo mesmo se o aparelho não estiver conectado agora. `key`: 'mics'|'loopbacks'."""
        c = QComboBox(); widgets.no_wheel_steal(c)
        c.addItem(f"(padrão do {util.so_nome()})", "")
        if tooltip:
            widgets.add_tooltip(c, tooltip)
        self._fields.append((c, section, attr, "device", None))
        self._device_combos.append((c, key))
        self._row(form, label, c, None)
        return c

    def _row(self, form: QFormLayout, label: str, widget: QWidget, hint: str | None) -> None:
        if hint:
            wrap = QWidget()
            v = QVBoxLayout(wrap); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(1)
            v.addWidget(widget)
            h = QLabel(hint); h.setProperty("role", "muted"); h.setWordWrap(True)
            h.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
            v.addWidget(h)
            form.addRow(label, wrap)
        else:
            form.addRow(label, widget)

    # -- abas ----------------------------------------------------------------

    def _build_recording_tab(self) -> None:
        f = self._tab("Gravação")
        au = self._group(f, "Áudio")
        self._device_choice(au, "Microfone", "audio", "mic_device", "mics",
                             tooltip=f"Escolha o microfone na lista ou deixe no padrão do {util.so_nome()}.")
        self._device_choice(au, "Áudio do sistema", "audio", "loopback_device", "loopbacks",
                             tooltip=f"Escolha a saída a capturar (loopback) ou use o padrão do {util.so_nome()}.")
        self._check(au, "Manter o áudio após transcrever", "audio", "keep_audio")
        self._choice(au, "Formato do áudio", "audio", "archive_format", _ARCHIVE)
        self._int(au, "Apagar gravação após (dias)", "audio", "retention_days", 0, 3650,
                  hint="0 = nunca; só a pasta da gravação, a nota .md não é tocada")
        mic = QWidget(); mr = QHBoxLayout(mic); mr.setContentsMargins(0, 0, 0, 0)
        self._mic_btn = widgets.ModernButton("Testar microfone", self._toggle_mic_test)
        mr.addWidget(self._mic_btn)
        self._mic_status = QLabel(""); self._mic_status.setProperty("role", "muted")
        self._mic_status.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        mr.addWidget(self._mic_status); mr.addStretch(1)
        au.addRow(mic)
        self._mic_bar = QProgressBar(); self._mic_bar.setRange(0, 100); self._mic_bar.setTextVisible(False)
        self._lb_bar = QProgressBar(); self._lb_bar.setRange(0, 100); self._lb_bar.setTextVisible(False)
        au.addRow("Eu (microfone)", self._mic_bar)
        au.addRow("Participantes (saída)", self._lb_bar)

        dz = self._group(f, "Diarização (separar vozes)")
        self._check(dz, "Ativar diarização", "diarization", "enabled")
        self._text(dz, "Token Hugging Face", "diarization", "hf_token", secret=True,
                   placeholder="hf_… (cole seu token)",
                   tooltip="Gere em hf.co/settings/tokens (grátis). Cifrado com DPAPI ao salvar.")
        self._int(dz, "Máx. de vozes", "diarization", "max_speakers", 0, 50, hint="0 = automático")
        self._int(dz, "Mín. de vozes", "diarization", "min_speakers", 0, 50, hint="0 = automático")
        self._check(dz, "Perguntar nº de participantes ao fim", "diarization", "ask_speakers")
        self._int(dz, "Timeout da pergunta (s)", "diarization", "ask_speakers_timeout", 0, 600,
                  hint="0 = espera indefinida")
        self._int(dz, "Diarizar em blocos de (min)", "diarization", "chunk_minutes", 0, 60,
                  hint="0 = inteiro; blocos evitam estourar a VRAM em reuniões longas")

        ui = self._group(f, "Interface e atalhos")
        self._check(ui, "Pílula flutuante durante a gravação", "ui", "overlay")
        self._text(ui, "Atalho gravar/parar", "ui", "hotkey", placeholder="ex.: ctrl+alt+r",
                   tooltip="Atalho global de gravar/parar. Vazio desativa.")
        self._text(ui, "Atalho nova call (dividir)", "ui", "hotkey_split", placeholder="ex.: ctrl+alt+n",
                   tooltip="Divide a call atual em duas. Vazio desativa.")
        self._int(ui, "Pendências ativas: últimos (dias)", "ui", "pending_window_days", 0, 3650,
                  hint="0 = sem recorte (conta todas); idade conta pela data da reunião")

    def _build_transcription_tab(self) -> None:
        f = self._tab("Transcrição")
        self._choice(f, "Motor", "whisper", "engine", _STT_ENGINES)
        self._list_choice(f, "Modelo", "whisper", "model", _WHISPER_MODELS)
        self._choice(f, "Dispositivo", "whisper", "device", _WHISPER_DEVICES)
        self._choice(f, "Idioma", "whisper", "language", _WHISPER_LANGS)
        self._bigtext(f, "Vocabulário (hotwords)", "whisper", "hotwords", rows=4,
                      placeholder="Termos, siglas e nomes próprios que aparecem nas suas reuniões e "
                                  "guiam a transcrição.  Ex.: SAP ABAP BAPI CDS Fiori OData, nomes "
                                  "de clientes, produtos e projetos, jargão da sua área.",
                      tooltip="Quanto mais específico do seu dia a dia, melhor a transcrição acerta "
                              "os termos difíceis. Pode escrever em várias linhas.")
        av = self._group(f, "Avançado")
        self._int(av, "Batch size", "whisper", "batch_size", 0, 64, hint="0 desliga o lote")
        self._int(av, "Beam size", "whisper", "beam_size", 0, 10)
        self._int(av, "Threads de CPU", "whisper", "cpu_threads", 0, 64, hint="0 = automático")
        self._int(av, "VAD silêncio mín. (ms)", "whisper", "vad_min_silence_ms", 0, 5000, hint="0 = padrão")
        self._float(av, "VAD limiar (0..1)", "whisper", "vad_threshold", 0.0, 1.0, 0.05, hint="0 = padrão")
        cl = self._group(f, "Nuvem (só com motor = Nuvem)")
        self._text(cl, "Endpoint", "whisper", "cloud_base_url", placeholder="vazio = Groq",
                   tooltip="URL do serviço compatível com OpenAI. Vazio usa a Groq.")
        self._text(cl, "Chave da API", "whisper", "cloud_api_key", secret=True,
                   placeholder="cole a chave da API", tooltip="Cifrada com DPAPI ao salvar.")
        self._text(cl, "Modelo (nuvem)", "whisper", "cloud_model", placeholder="ex.: whisper-large-v3")

    def _build_ia_tab(self) -> None:
        f = self._tab("IA")
        self._check(f, "Gerar resumo (IA)", "summary", "enabled")
        self._choice(f, "Provedor", "summary", "provider", _PROVIDERS)
        self._choice(f, "Modelo (Claude)", "summary", "model", _SUMMARY_MODELS)
        self._choice(f, "Modelo do chat", "summary", "chat_model", _CHAT_MODELS)
        self._int(f, "Timeout (s)", "summary", "timeout_seconds", 30, 3600)
        ol = self._group(f, "Ollama")
        self._text(ol, "Modelo", "summary", "ollama_model", placeholder="ex.: llama3.1, qwen2.5")
        self._text(ol, "Endpoint", "summary", "ollama_base_url", placeholder="vazio = http://localhost:11434")
        op = self._group(f, "OpenAI-compatível")
        self._text(op, "Modelo", "summary", "openai_model", placeholder="ex.: gpt-4o-mini")
        self._text(op, "Endpoint", "summary", "openai_base_url", placeholder="inclua /v1 no final",
                   tooltip="URL base do provedor compatível com OpenAI, terminando em /v1.")
        self._text(op, "Chave da API", "summary", "openai_api_key", secret=True,
                   placeholder="cole a chave da API", tooltip="Cifrada com DPAPI ao salvar.")

        pr = self._group(f, "Prompt do resumo (prompt.md)")
        actions = QWidget(); ar = QHBoxLayout(actions); ar.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Instruções que moldam o resumo das suas reuniões.")
        note.setProperty("role", "muted"); note.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        ar.addWidget(note); ar.addStretch(1)
        ar.addWidget(widgets.ModernButton("Assistente de perfil…", self._open_wizard))
        ar.addWidget(widgets.ModernButton("Restaurar padrão", self._restore_prompt))
        pr.addRow(actions)
        self._prompt_editor = QPlainTextEdit()
        self._prompt_editor.setMinimumHeight(180)
        self._prompt_editor.setStyleSheet(
            f"font-family:'{theme.active().font_mono}','Consolas',monospace; font-size:{theme.zpt(9)}pt;")
        pr.addRow(self._prompt_editor)

        # o cabeçalho da nota era o único texto sem edição pela interface (#181):
        # quem não é da área SAP levava "reunião SAP/ABAP" em toda ata e não tinha
        # onde mexer, porque só o assistente de perfil escrevia o context.md.
        cx = self._group(f, "Contexto para IA da nota (context.md)")
        cx_actions = QWidget(); cr = QHBoxLayout(cx_actions); cr.setContentsMargins(0, 0, 0, 0)
        cx_note = QLabel("Opcional: abre a nota e orienta a IA que for ler a ata. "
                         "Vazio, a nota sai sem cabeçalho.")
        cx_note.setProperty("role", "muted")
        cx_note.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        cr.addWidget(cx_note); cr.addStretch(1)
        cr.addWidget(widgets.ModernButton("Usar o texto sugerido", self._restore_context))
        cx.addRow(cx_actions)
        self._context_editor = QPlainTextEdit()
        # sem teto de altura de propósito: com um, a QFormLayout do QGroupBox
        # espalha as linhas do grupo e abre vãos entre o rótulo e a caixa. Deixar
        # o editor absorver a sobra é o que o grupo do prompt já faz aqui em cima.
        self._context_editor.setMinimumHeight(90)
        self._context_editor.setPlaceholderText(
            "Sem cabeçalho. O botão acima preenche com um texto pronto, que dá para editar.")
        self._context_editor.setStyleSheet(
            f"font-family:'{theme.active().font_mono}','Consolas',monospace; font-size:{theme.zpt(9)}pt;")
        cx.addRow(self._context_editor)

    def _open_wizard(self) -> None:
        from .wizard_ui import WizardWindow

        self._wizard = WizardWindow(app=self.app, on_applied=self._after_wizard)  # ref evita GC
        self._wizard.show()

    def _after_wizard(self) -> None:
        if self.app is not None:
            try:
                self.app.reload_config()
            except Exception:
                pass
        self._load()   # recarrega campos + prompt (o wizard reescreveu ambos)

    def _restore_prompt(self) -> None:
        from .. import notes

        self._prompt_editor.setPlainText(notes.default_summary_prompt())

    def _restore_context(self) -> None:
        from .. import notes

        self._context_editor.setPlainText(notes.suggested_context_note())

    def _build_detection_tab(self) -> None:
        f = self._tab("Detecção")
        self._check(f, "Gravar sozinho ao detectar a call", "detection", "auto_record")
        self._text(f, "Apps (desktop)", "detection", "apps", placeholder="ex.: teams, zoom")
        self._text(f, "Navegadores", "detection", "browsers", placeholder="ex.: chrome, msedge, firefox",
                   tooltip="Vazio desliga a detecção de calls no navegador.")
        self._text(f, "Títulos de reunião", "detection", "browser_titles",
                   placeholder="vazio = qualquer site usando o mic")
        self._float(f, "Poll (s)", "detection", "poll_seconds", 0.5, 60)
        self._float(f, "Tolerância / grace (s)", "detection", "grace_seconds", 0, 120)
        self._float(f, "Gap p/ dividir call (s)", "detection", "split_gap_seconds", 0, 60, hint="0 = nunca")
        self._float(f, "Duração mínima (s)", "detection", "min_call_seconds", 0, 600)
        self._float(f, "Duração máxima (h)", "detection", "max_call_hours", 0.5, 24)

    def _build_dirs_tab(self) -> None:
        f = self._tab("Pastas")
        self._dir_row(f, "Notas (.md)", "output", "export_dir",
                      placeholder=f"{util.APP_DIR / 'Notas'} (padrão, local)")
        self._dir_row(f, "Gravações", "output", "recordings_dir",
                      placeholder=f"{plat.default_recordings_dir()} (padrão)")

    def _dir_row(self, form, label, section, attr, placeholder="", tooltip="") -> None:
        e = QLineEdit()
        if placeholder:
            e.setPlaceholderText(placeholder)
        if tooltip:
            widgets.add_tooltip(e, tooltip)
        self._fields.append((e, section, attr, "text", None))
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(e, 1)
        h.addWidget(widgets.ModernButton("…", lambda: self._pick_dir(e)))
        self._row(form, label, row, None)

    def _pick_dir(self, entry: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Escolher pasta", entry.text() or "")
        if d:
            entry.setText(d)

    # -- seletores de dispositivo de áudio -----------------------------------

    def _devices_worker(self) -> None:
        try:
            data = util.list_audio_devices()
        except Exception:
            data = None
        self._devices_ready.emit(data)

    def _fill_devices(self, data) -> None:
        """Popula os combos de dispositivo com a lista enumerada, preservando o valor
        atual (selecionado se aparecer na lista; senão mantido como texto = passthrough)."""
        if not data:
            return
        for combo, key in self._device_combos:
            current = combo.currentData()   # valor atual (data); "" = padrão
            names = data.get(key) or []
            while combo.count() > 1:        # mantém só o "(padrão do SO)" no topo
                combo.removeItem(1)
            for n in names:
                combo.addItem(n, n)
            idx = combo.findData(current)
            if idx < 0 and current:         # valor salvo não enumerado: preserva como item
                combo.addItem(current, current)
                idx = combo.findData(current)
            combo.setCurrentIndex(max(0, idx))

    # -- aba Sobre -----------------------------------------------------------

    def _build_appearance_tab(self) -> None:
        f = self._tab("Aparência")
        intro = QLabel("Clique para aplicar na hora. Cada bloco é uma prévia da capa com "
                       "as cores daquele tema.")
        intro.setProperty("role", "muted"); intro.setWordWrap(True)
        f.addRow(intro)
        # largura contida: os blocos não devem esticar p/ preencher a janela (a capa real é
        # estreita) — teto de ~760px alinhado à esquerda, mesmo com a janela maximizada.
        row = QWidget()
        rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
        self._theme_host = QWidget()
        self._theme_host.setMaximumWidth(760)
        self._theme_grid = QGridLayout(self._theme_host)
        self._theme_grid.setContentsMargins(0, 8, 0, 0)
        self._theme_grid.setHorizontalSpacing(14)
        self._theme_grid.setVerticalSpacing(14)
        self._theme_grid.setColumnStretch(0, 1)
        self._theme_grid.setColumnStretch(1, 1)
        rl.addWidget(self._theme_host)
        rl.addStretch(1)   # empurra os blocos p/ a esquerda; o excesso fica vazio à direita
        f.addRow(row)
        self._populate_theme_cards()

        # tamanho da interface (#104): no macOS o Qt mapeia pontos a 72 dpi (vs 96 no
        # Windows) e a UI "encolhe" — o padrão automático lá é 133%, que devolve a
        # proporção visual do Windows. Escala os tokens do tema + os font-size via
        # theme.zpt(); aplica a quente pelo mesmo caminho da troca de tema.
        zrow = QWidget()
        zl = QHBoxLayout(zrow); zl.setContentsMargins(0, 12, 0, 0); zl.setSpacing(8)
        zl.addWidget(QLabel("Tamanho da interface:"))
        self._zoom_combo = QComboBox(); widgets.no_wheel_steal(self._zoom_combo)
        widgets.add_tooltip(self._zoom_combo,
                            "Escala todo o texto da interface. Aplica na hora; janelas já "
                            "abertas podem precisar ser reabertas para ajustar tudo.")
        auto_pct = round(theme.default_zoom() * 100)
        self._zoom_combo.addItem(f"Automático ({auto_pct}% — padrão do {util.so_nome()})", None)
        for z in theme.ZOOM_OPTIONS:
            self._zoom_combo.addItem(f"{round(z * 100)}%", z)
        choice = theme.zoom_choice()
        if choice is not None:
            for i in range(1, self._zoom_combo.count()):
                if abs(self._zoom_combo.itemData(i) - choice) < 0.005:
                    self._zoom_combo.setCurrentIndex(i)
                    break
        self._zoom_combo.currentIndexChanged.connect(self._pick_zoom)
        zl.addWidget(self._zoom_combo)
        zhint = QLabel("Aplica na hora; janelas já abertas podem precisar ser reabertas.")
        zhint.setProperty("role", "muted")
        zl.addWidget(zhint)
        zl.addStretch(1)
        f.addRow(zrow)

    def _pick_zoom(self, index: int) -> None:
        """Persiste a escolha de zoom (state.json, não config.toml — preferência de
        exibição é por máquina). None = automático do SO."""
        theme.set_zoom_choice(self._zoom_combo.itemData(index))

    def _populate_theme_cards(self) -> None:
        """(Re)constrói o seletor: a opção 'Automático' no topo (largura cheia) + os 4
        temas em blocos 2x2, marcando o vigente. Reconstruir (em vez de mutar) mantém tudo
        pintado no tema ATIVO e o marcador de seleção correto após a troca a quente (#70)."""
        grid = self._theme_grid
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        choice = theme.current_choice()   # None = automático
        grid.addWidget(_AutoOption(choice is None, self._pick_theme), 0, 0, 1, 2)
        for i, th in enumerate(theme.themes()):
            card = _ThemeCard(th, th.label, th.name, choice == th.name, self._pick_theme)
            grid.addWidget(card, 1 + i // 2, i % 2)

    def _pick_theme(self, value) -> None:
        """Aplica o tema clicado a quente (state.json, não config.toml). O apply dispara
        theme._restyle_top_levels -> self.restyle_theme, que reconstrói a grade marcando
        o novo vigente. `value` = slug do tema, ou None para o Automático."""
        from PySide6.QtWidgets import QApplication

        theme.apply_choice(value, app=QApplication.instance())

    def restyle_theme(self) -> None:
        """Após a troca a quente, reconstrói a grade de temas (repinta os cards no tema
        ativo + marca o novo selecionado). Contrato `restyle_theme` de theme.apply (#70)."""
        if getattr(self, "_theme_grid", None) is not None:
            self._populate_theme_cards()
        if getattr(self, "_ts_off", None) is not None:
            self._style_timesheet_cards()

    # -- aba Apontamento (#118/#126): ativação opt-in do timesheet -------------

    def _build_timesheet_tab(self) -> None:
        from PySide6.QtWidgets import QFrame, QPushButton

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        # cartão DORMENTE: destaque deliberado (borda/botão âmbar, fora do padrão
        # visual da tela) para quem recebe o módulo num update perceber a novidade
        self._ts_off = QFrame()
        self._ts_off.setObjectName("tsActivateCard")
        off = QVBoxLayout(self._ts_off)
        off.setContentsMargins(16, 12, 16, 14)
        off.setSpacing(6)
        self._ts_off_title = QLabel("Apontamento de horas — NOVO")
        self._ts_off_desc = QLabel(
            "Suas reuniões processadas viram sugestões de apontamento (cliente, horários "
            "e descrição), com entrada manual para o resto do dia, fila do que falta lançar "
            "no seu sistema de horas e exportação Excel no layout da sua planilha.\n"
            "Nada roda nem é criado até você ativar — a ativação cria o banco local "
            "(timesheet.db) e já varre as reuniões recentes.")
        self._ts_off_desc.setWordWrap(True)
        self._ts_activate_btn = QPushButton("✨  Ativar apontamento de horas")
        self._ts_activate_btn.setObjectName("tsActivateBtn")
        self._ts_activate_btn.setCursor(Qt.PointingHandCursor)
        self._ts_activate_btn.clicked.connect(self._activate_timesheet)
        off.addWidget(self._ts_off_title)
        off.addWidget(self._ts_off_desc)
        brow = QHBoxLayout()
        brow.addWidget(self._ts_activate_btn)
        brow.addStretch(1)
        off.addLayout(brow)
        outer.addWidget(self._ts_off)

        # cartão ATIVADO: controles normais (sem destaque)
        self._ts_on = QFrame()
        on = QVBoxLayout(self._ts_on)
        on.setContentsMargins(2, 2, 2, 2)
        on.setSpacing(6)
        ok = QLabel("Apontamento de horas está ativado ✓")
        ok.setProperty("role", "ok")
        hint = QLabel(f"Banco local em {util.APP_DIR / 'timesheet.db'} (backup diário "
                      "automático). Desativar NÃO apaga seus dados.")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        on.addWidget(ok)
        on.addWidget(hint)
        # ajustes finos (#125, fase 2): campos padrão da tela (seção 'timesheet'
        # no _fields), gravados pelo Salvar como as demais abas. db_path fica
        # DELIBERADAMENTE só no TOML (apontar para OneDrive corrompe o banco).
        box = QGroupBox("Ajustes")
        gl = QFormLayout(box)
        gl.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self._check(gl, "Sugestões automáticas:", "timesheet", "suggest",
                    hint="Reuniões processadas viram sugestões de apontamento.")
        self._int(gl, "Arredondar horários (min):", "timesheet", "round_minutes",
                  lo=0, hi=60,
                  hint="Início/fim sugeridos em passos de N minutos (0 = exato).")
        self._int(gl, "Reunião mínima (min):", "timesheet", "min_meeting_minutes",
                  lo=0, hi=240,
                  hint="Reunião mais curta que isto não vira sugestão.")
        self._text(gl, "Cliente padrão:", "timesheet", "default_client",
                   placeholder="(vazio = o último usado)",
                   hint="Pré-seleção do lançamento manual.")
        self._text(gl, "Pasta do export Excel:", "timesheet", "export_dir",
                   placeholder=f"(vazio = {Path.home() / 'Documents' / 'ScribaDev' / 'Apontamentos'})")
        self._text(gl, "Pasta do backup diário:", "timesheet", "backup_dir",
                   placeholder=f"(vazio = {util.APP_DIR / 'backups'})",
                   hint="Arquivo morto - pode apontar para OneDrive.")
        on.addWidget(box)
        orow = QHBoxLayout()
        orow.setSpacing(6)
        orow.addWidget(widgets.ModernButton(
            "Abrir apontamentos", lambda: getattr(self.app, "show_timesheet", lambda: None)(),
            kind="primary"))
        orow.addWidget(widgets.ModernButton("Desativar…", self._deactivate_timesheet))
        orow.addStretch(1)
        on.addLayout(orow)
        outer.addWidget(self._ts_on)

        self._ts_status = QLabel("")
        self._ts_status.setProperty("role", "muted")
        self._ts_status.setWordWrap(True)
        outer.addWidget(self._ts_status)
        outer.addStretch(1)
        self._tabs.addTab(page, "Apontamento")
        self._ts_tab_index = self._tabs.indexOf(page)
        self._style_timesheet_cards()
        self._sync_timesheet_tab()

    def _style_timesheet_cards(self) -> None:
        """Estilo dependente de tema do cartão de destaque; reaplicado no restyle (#70).
        Texto do botão por luminância do âmbar (contraste nos temas claro E escuro)."""
        from PySide6.QtGui import QColor

        t = theme.active()
        fg = "#1b1b1b" if QColor(t.warn).lightnessF() > 0.5 else "#f5f5f5"
        self._ts_off.setStyleSheet(
            f"QFrame#tsActivateCard {{ background:{t.surface}; border:2px solid {t.warn};"
            f" border-radius:{t.radius}px; }}"
            f"QFrame#tsActivateCard QLabel {{ border:none; background:transparent; }}"
            f"QPushButton#tsActivateBtn {{ background:{t.warn}; color:{fg}; font-weight:bold;"
            f" font-size:{theme.zpt(11)}pt; padding:9px 20px; border:none; border-radius:{t.radius}px; }}"
            f"QPushButton#tsActivateBtn:hover {{ background:{t.accent}; color:{t.on_accent}; }}"
            f"QPushButton#tsActivateBtn:disabled {{ background:{t.border}; color:{t.muted}; }}")
        self._ts_off_title.setStyleSheet(f"font-size:{theme.zpt(12)}pt; font-weight:bold; color:{t.warn};")
        self._ts_off_desc.setStyleSheet(f"color:{t.muted};")

    def _timesheet_enabled(self) -> bool:
        try:
            return bool(self.app.cfg.timesheet.enabled)
        except Exception:
            return False

    def _sync_timesheet_tab(self) -> None:
        enabled = self._timesheet_enabled()
        self._ts_off.setVisible(not enabled)
        self._ts_on.setVisible(enabled)
        self._tabs.setTabText(self._ts_tab_index,
                              "Apontamento" if enabled else "Apontamento ✨")

    def _activate_timesheet(self) -> None:
        self._ts_activate_btn.setEnabled(False)
        self._ts_status.setText("Ativando — criando o banco e varrendo as reuniões recentes…")

        def work() -> None:
            try:
                from .. import timesheet_suggest

                n = timesheet_suggest.activate()
                msg = (f"Ativado ✓ — {n} sugestão(ões) inicial(is) esperando revisão "
                       "em Apontamentos." if n else
                       "Ativado ✓ — o item Apontamento de horas já aparece na bandeja.")
            except Exception:
                log.exception("ativação do timesheet falhou")
                msg = "Falha ao ativar — detalhes na janela de Log."
            self._ts_done.emit(msg)

        threading.Thread(target=work, daemon=True, name="ts-activate").start()

    def _deactivate_timesheet(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(
                self, "Desativar apontamento de horas",
                "As sugestões automáticas, o item da bandeja e a CLI param.\n"
                "Seus apontamentos NÃO são apagados — reativar reencontra tudo.\n\n"
                "Desativar?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        def work() -> None:
            try:
                from .. import timesheet_suggest

                timesheet_suggest.deactivate()
                msg = "Desativado — os dados foram preservados; reative quando quiser."
            except Exception:
                log.exception("desativação do timesheet falhou")
                msg = "Falha ao desativar — detalhes na janela de Log."
            self._ts_done.emit(msg)

        threading.Thread(target=work, daemon=True, name="ts-deactivate").start()

    def _on_timesheet_done(self, msg: str) -> None:
        if self.app is not None:
            try:
                self.app.reload_config()
            except Exception:
                log.exception("settings: reload_config pós-ativação falhou")
        self._ts_status.setText(msg)
        self._ts_activate_btn.setEnabled(True)
        self._sync_timesheet_tab()
        main_win = getattr(self.app, "main_win", None)
        if main_win is not None:  # capa aberta: o botão "Horas" do header segue na hora
            try:
                main_win.refresh_home()
            except Exception:
                log.exception("settings: refresh da capa pós-ativação falhou")

    def _build_about_tab(self) -> None:
        from .. import updates

        page = QWidget()
        outer = QVBoxLayout(page); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget(); lay = QVBoxLayout(inner); lay.setContentsMargins(14, 14, 14, 14)
        title = QLabel("ScribaDev"); title.setStyleSheet(f"font-size:{theme.zpt(18)}pt; font-weight:bold;")
        lay.addWidget(title)
        ver = QLabel(updates.build_string()); ver.setProperty("role", "muted"); lay.addWidget(ver)
        desc = QLabel("Gravação e transcrição automática de reuniões (Teams, Zoom, Meet) — 100% local e privado.")
        desc.setProperty("role", "muted"); desc.setWordWrap(True); lay.addWidget(desc)
        link = QLabel('Repositório: <a href="https://github.com/allanrmartins/ScribaDev">'
                      'github.com/allanrmartins/ScribaDev</a>')
        link.setOpenExternalLinks(True); lay.addWidget(link)
        lic = QLabel("Licença: MIT © Allan Martins e contribuidores")
        lic.setProperty("role", "muted"); lay.addWidget(lic)

        upd = QHBoxLayout()
        upd.addWidget(widgets.ModernButton("Verificar atualizações", self._check_updates_about))
        self._about_update_btn = widgets.ModernButton("Atualizar agora", self._do_update_about, kind="primary")
        self._about_update_btn.setVisible(False)
        upd.addWidget(self._about_update_btn); upd.addStretch(1)
        lay.addLayout(upd)
        self._about_status = QLabel(""); self._about_status.setWordWrap(True); self._about_status.setProperty("role", "muted")
        lay.addWidget(self._about_status)

        head = QLabel("Componentes e versões"); head.setStyleSheet("font-weight:bold; margin-top:8px;")
        lay.addWidget(head)
        t = theme.active()
        for label, value, level in self._about_components():
            color = {"ok": t.muted, "warn": t.warn, "err": t.accent}.get(level, t.muted)
            row = QLabel(f"<b>{label}:</b> {value}")
            row.setWordWrap(True); row.setStyleSheet(f"color:{color}; font-size:{theme.zpt(9)}pt;")
            lay.addWidget(row)
        self._build_components_section(lay)
        lay.addStretch(1)
        scroll.setWidget(inner); outer.addWidget(scroll)
        self._tabs.addTab(page, "Sobre")

    def _build_components_section(self, lay) -> None:
        """Seção "Baixar componentes" (#154) — só na instalação CONGELADA (setup.exe):
        o retry do que o wizard de 1º uso pulou/falhou (o instalador é enxuto de
        propósito). Na instalação git/venv os extras entram via pip, como sempre."""
        from .. import updates

        if not updates.is_frozen_install():
            return
        import sys as _sys

        head = QLabel("Baixar componentes"); head.setStyleSheet("font-weight:bold; margin-top:8px;")
        lay.addWidget(head)
        info = widgets.WrapLabel(
            "O instalador é enxuto: estes itens são baixados sob demanda. Selecione o que "
            "faltou (ou o que quer re-baixar) — o app continua funcionando durante o download.")
        info.setProperty("role", "muted")
        lay.addWidget(info)

        model = getattr(self.app.cfg.whisper, "model", "small")
        self._comp_model = widgets.AnimatedCheckBox(f"Modelo de transcrição ({model})")
        self._comp_cuda = widgets.AnimatedCheckBox("Bibliotecas NVIDIA (cuBLAS/cuDNN, ~3 GB)")
        self._comp_voices = widgets.AnimatedCheckBox("Separação de vozes (torch + pyannote, ~3 GB)")
        try:  # default: pré-marca o que está faltando de verdade
            import importlib.util as ilu

            self._comp_voices.setChecked(ilu.find_spec("pyannote") is None)
        except Exception:
            pass
        lay.addWidget(self._comp_model)
        if _sys.platform == "win32":
            lay.addWidget(self._comp_cuda)
        lay.addWidget(self._comp_voices)

        row = QHBoxLayout()
        self._comp_btn = widgets.ModernButton("Baixar selecionados", self._download_components,
                                              kind="primary")
        row.addWidget(self._comp_btn)
        # reinstalação limpa (#170/#176): apagar tudo e baixar de novo
        self._comp_repair_btn = widgets.ModernButton("Reinstalar componentes…",
                                                     self._repair_components)
        widgets.add_tooltip(self._comp_repair_btn,
                            "Apaga os componentes já baixados e baixa de novo — use quando "
                            "o app disser que estão inacessíveis/danificados.")
        row.addWidget(self._comp_repair_btn)
        # aparece SÓ quando um download falha: zip do diagnóstico + issue pré-preenchida
        self._comp_report_btn = widgets.ModernButton("Reportar erro no GitHub",
                                                     self._report_components_error)
        self._comp_report_btn.setVisible(False)
        row.addWidget(self._comp_report_btn); row.addStretch(1)
        lay.addLayout(row)
        self._comp_bar = QProgressBar(); self._comp_bar.setRange(0, 1000); self._comp_bar.setValue(0)
        self._comp_bar.setVisible(False)
        lay.addWidget(self._comp_bar)
        self._comp_status = widgets.WrapLabel(""); self._comp_status.setProperty("role", "muted")
        lay.addWidget(self._comp_status)
        self._comp_last_notes = ""

        self._comp_progress.connect(self._comp_status.setText)
        self._comp_frac.connect(self._comp_bar.setValue)
        self._comp_done.connect(self._components_done)

    def _download_components(self) -> None:
        from .. import components

        plan = components.plan_items(
            model=(getattr(self.app.cfg.whisper, "model", "small")
                   if self._comp_model.isChecked() else None),
            cuda=self._comp_cuda.isChecked(),
            voices=self._comp_voices.isChecked())
        if not plan:
            self._comp_status.setText("Nada selecionado.")
            return
        self._comp_btn.setEnabled(False)
        self._comp_bar.setValue(0); self._comp_bar.setVisible(True)
        self._comp_status.setText("Baixando: " + "; ".join(l for _k, l, _mb in plan) + "…")

        def work():
            ok, notes = components.run(plan, progress=self._comp_progress.emit,
                                       frac=self._comp_frac.emit, poll=not self._inline_bg)
            self._comp_done.emit(ok, notes)

        if self._inline_bg:
            work()
        else:
            threading.Thread(target=work, daemon=True, name="settings-components").start()

    def _components_done(self, ok: bool, notes: str) -> None:
        # a instalação segurou a fila (#167): readota o que ficou esperando
        # (e o que "falhou" por EACCES do addons em versões antigas)
        try:
            if getattr(self.app, "scan_pending", None):
                threading.Thread(target=self.app.scan_pending, daemon=True,
                                 name="post-install-sweep").start()
        except Exception:
            log.exception("varredura pós-instalação falhou")
        self._comp_btn.setEnabled(True)
        self._comp_bar.setValue(1000)
        self._comp_last_notes = notes
        self._comp_report_btn.setVisible(not ok)
        self._comp_status.setText(
            "Pronto — componentes instalados e já disponíveis (sem reiniciar)." if ok
            else f"Alguns itens falharam — dá para tentar de novo, ou reporte o erro "
                 f"(o botão salva o log e abre a issue). Detalhe: {notes}")

    def _reuniao_em_processamento(self) -> str | None:
        """Nome da reunião com processamento ATIVO, ou None. Apagar o addons no meio
        de uma transcrição mata a transcrição (foi o que aconteceu na #176)."""
        try:
            for meta_path in Path(self.app.cfg.output.resolved_recordings_dir()).rglob("meta.json"):
                if util.is_locked(meta_path.parent):
                    return meta_path.parent.name
        except Exception:
            log.debug("não consegui checar reuniões em processamento", exc_info=True)
        return None

    def _repair_components(self) -> None:
        """Reinstalação limpa dos componentes (#170/#176): apaga APP_DIR/addons e já
        baixa de novo o que estava instalado.

        Destrutivo (são GB), então confirma antes. Duas recusas próprias: instalação
        em andamento e reunião sendo processada. Quando o SO segura arquivos em uso
        (.pyd carregado, subprocesso órfão), o apagamento é AGENDADO para o próximo
        início - antes era um beco sem saída com a pasta aberta no Explorer.
        """
        from PySide6.QtWidgets import QMessageBox

        from .. import addons

        d = addons.addons_dir()
        if addons.is_installing():
            self._comp_status.setText(
                "Há uma instalação de componentes em andamento - espere ela terminar.")
            return
        ocupada = self._reuniao_em_processamento()
        if ocupada:
            self._comp_status.setText(
                f"A reunião {ocupada} está sendo processada agora - os componentes estão "
                f"em uso. Espere terminar e tente de novo.")
            return
        pergunta = "\n".join((
            "Isto APAGA os componentes já baixados (bibliotecas NVIDIA e separação de vozes):",
            str(d),
            "",
            "Em seguida eu baixo de novo o que você já usava — são vários GB.",
            "Suas reuniões e configurações NÃO são tocadas.",
            "",
            "Reinstalar agora?",
        ))
        if QMessageBox.question(self, "Reinstalar componentes", pergunta,
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        # o que reinstalar depois: medir ANTES de apagar (o cache do modelo fica
        # fora do addons e não é tocado, então ele não entra no replano)
        try:
            tinha_cuda = any(d.glob("nvidia*"))
        except OSError:
            tinha_cuda = False
        try:
            import importlib.util as ilu

            tinha_voices = ilu.find_spec("pyannote") is not None
        except Exception:
            tinha_voices = False

        ok, msg = addons.reset_addons()
        if not ok:
            self._agendar_reinstalacao(msg)
            return
        self._comp_model.setChecked(False)
        self._comp_cuda.setChecked(tinha_cuda)
        self._comp_voices.setChecked(tinha_voices)
        if tinha_cuda or tinha_voices:
            self._comp_status.setText(f"{msg}. Baixando de novo…")
            self._download_components()
        else:
            self._comp_status.setText(
                f"{msg}. Marque os itens acima e clique em Baixar selecionados.")

    def _agendar_reinstalacao(self, motivo: str) -> None:
        """Arquivo em uso: oferece apagar no próximo início (e não deixa o usuário
        sem saída, como antes)."""
        from PySide6.QtWidgets import QMessageBox

        from .. import addons

        pergunta = "\n".join((
            motivo + ".",
            "",
            "Posso apagar os componentes no PRÓXIMO INÍCIO do ScribaDev, antes de "
            "qualquer coisa usá-los. Depois é só marcar o que você usa e baixar de novo.",
            "",
            "Agendar para o próximo início?",
        ))
        if QMessageBox.question(self, "Reinstalar componentes", pergunta,
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.Yes) != QMessageBox.Yes:
            self._comp_status.setText(motivo + ".")
            return
        if addons.schedule_reset():
            self._comp_status.setText(
                "Agendado: os componentes serão apagados no próximo início do ScribaDev. "
                "Feche e abra o app, depois baixe os componentes de novo.")
        else:
            self._comp_status.setText(motivo + " - e não consegui agendar o apagamento.")

    def _report_components_error(self) -> None:
        """Botão do erro: zip de diagnóstico (com o pip.log) + issue pré-preenchida."""
        from .. import support

        try:
            rec = self.app.cfg.output.resolved_recordings_dir()
        except Exception:
            rec = None
        z = support.open_report("Download de componentes falhou (Configurações → Sobre)",
                                self._comp_last_notes, rec)
        self._comp_status.setText(
            f"Abri a página da issue no navegador. Para anexar, arraste o arquivo "
            f"{z.name} (deixei selecionado no gerenciador de arquivos)." if z else
            "Abri a página da issue no navegador — não consegui gerar o zip; "
            "descreva o cenário na issue, por favor.")

    def _about_components(self) -> list:
        import sys

        ver = _pkg_version

        def core(label, text, present):
            return (label, text if present else text + " — AUSENTE (reinstale o app)", "ok" if present else "err")

        fw, ct, pa = ver("faster-whisper", "faster_whisper"), ver("ctranslate2"), ver("pyaudiowpatch")
        torch_v = ver("torch")
        if sys.platform == "win32":
            audio_row = core("Áudio (WASAPI)", f"pyaudiowpatch {pa or '?'}", bool(pa))
        elif sys.platform == "darwin":
            sd_v = ver("sounddevice")
            audio_row = core("Áudio (CoreAudio)", f"sounddevice {sd_v or '?'} + process tap", bool(sd_v))
        else:
            audio_row = ("Áudio (captura)", "não suportada neste SO ainda", "warn")
        return [
            ("Python", sys.version.split()[0], "ok"),
            core("Transcrição", f"faster-whisper {fw or '?'} · ctranslate2 {ct or '?'}", bool(fw and ct)),
            ("Diarização", *self._diar_health(ver)),
            ("PyTorch", torch_v or "não instalado (opcional — só p/ a diarização)", "ok" if torch_v else "warn"),
            ("GPU", self._gpu_str(), "ok"),
            audio_row,
            ("Compressão de áudio", *self._ffmpeg_health()),
        ]

    def _diar_health(self, ver) -> tuple:
        import importlib.util as ilu

        try:
            present = ilu.find_spec("pyannote.audio") is not None
        except ModuleNotFoundError:
            # nem o namespace 'pyannote' existe (find_spec de submódulo LEVANTA em
            # vez de devolver None): é ausência normal, não "erro ao checar"
            present = False
        except Exception as e:
            return (f"erro ao checar — {e}", "err")
        if not present:
            from .. import updates

            if updates.is_frozen_install():
                return ("não instalada — baixe logo abaixo, em \"Baixar componentes\" "
                        "(separação de vozes)", "err")
            return ("não instalada — falta o extra [diarization] (pyannote + torch)", "err")
        pa = ver("pyannote.audio") or "?"
        dz = self.app.cfg.diarization
        if not getattr(dz, "hf_token", ""):
            return (f"pyannote.audio {pa} — falta o token Hugging Face (aba Gravação)", "warn")
        if not getattr(dz, "enabled", False):
            return (f"pyannote.audio {pa} — desligada na aba Gravação", "warn")
        return (f"pyannote.audio {pa} · modelo: {getattr(dz, 'model', '?')}", "ok")

    def _ffmpeg_health(self) -> tuple:
        a = self.app.cfg.audio
        fmt = (getattr(a, "archive_format", "opus") or "wav").strip().lower()
        lvl = util.ffmpeg_status(getattr(a, "keep_audio", False), fmt)
        if lvl == "ok":
            return ("ffmpeg no PATH — áudio guardado é compactado", "ok")
        if lvl == "err":
            return ("ffmpeg AUSENTE — o áudio fica em WAV cru. Instale com 'winget install ffmpeg'", "err")
        return ("ffmpeg ausente no PATH (necessário p/ compactar o áudio guardado)", "warn")

    @staticmethod
    def _gpu_str() -> str:
        from .. import plat

        if plat.has_nvidia_gpu():
            return "NVIDIA (CUDA disponível)"
        return "sem GPU CUDA — usa CPU"

    def _check_updates_about(self) -> None:
        self._about_update_btn.setVisible(False)
        self._about_status.setText("Verificando…")
        threading.Thread(target=self._about_update_worker, daemon=True).start()

    def _about_update_worker(self) -> None:
        from .. import updates

        try:
            latest = updates.latest_version()
        except Exception:
            latest = None
        self._about_ready.emit(latest)

    def _show_about_update(self, latest) -> None:
        from .. import __version__, updates

        self._about_update_btn.setVisible(False)
        if latest is None:
            self._about_status.setText("Não consegui verificar — sem internet ou serviço indisponível.")
        elif updates._is_newer(__version__, latest):
            self._about_status.setText(f"Nova versão v{latest} disponível — recomendado atualizar.")
            self._about_update_btn.setText("Atualizar agora" if updates.is_git_install() else "Baixar")
            self._about_update_btn.setVisible(True)
        else:
            self._about_status.setText(f"✓ Você está na versão mais recente (v{__version__}).")

    def _do_update_about(self) -> None:
        from .. import updates

        if getattr(self.app, "is_recording", lambda: False)():
            self._about_status.setText("Pare a gravação antes de atualizar.")
            return
        if updates.is_git_install():
            self._about_status.setText("Atualizando… (git pull)")
            self.app.apply_update(self._about_update_done)
        else:
            import webbrowser

            webbrowser.open(updates.download_url())

    def _about_update_done(self, ok: bool, msg: str) -> None:
        if ok:
            self._about_status.setText("✓ Atualizado — reiniciando o ScribaDev…")
            QTimer.singleShot(1500, self.app.relaunch)
        else:
            self._about_status.setText("✗ " + msg)

    # -- teste de microfone (medidor de nível) -------------------------------

    def _toggle_mic_test(self) -> None:
        if self._level_probe is not None:
            self._stop_mic_test()
            return
        if getattr(self.app, "is_recording", lambda: False)():
            self._mic_status.setText("Pare a gravação para testar.")
            return
        self._mic_status.setText("abrindo…")
        try:
            from ..recorder import LevelProbe

            self._level_probe = LevelProbe(config_mod.load().audio)
        except Exception as e:
            self._mic_status.setText("falhou: " + (str(e).splitlines() or [""])[0][:50])
            self._level_probe = None
            return
        self._mic_btn.setText("Parar teste")
        self._mic_status.setText("fale algo — as barras devem mexer")
        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._poll_levels)
        self._level_timer.start(80)

    def _poll_levels(self) -> None:
        if self._level_probe is None:
            return
        try:
            self._mic_bar.setValue(int(min(100.0, self._level_probe.level("mic") * 100)))
            self._lb_bar.setValue(int(min(100.0, self._level_probe.level("loopback") * 100)))
        except Exception:
            self._stop_mic_test()

    def _stop_mic_test(self) -> None:
        if getattr(self, "_level_timer", None) is not None:
            self._level_timer.stop()
            self._level_timer = None
        if self._level_probe is not None:
            try:
                self._level_probe.stop()
            except Exception:
                pass
            self._level_probe = None
        self._mic_bar.setValue(0)
        self._lb_bar.setValue(0)
        self._mic_btn.setText("Testar microfone")
        self._mic_status.setText("")

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
        if kind == "bigtext":
            return " ".join(widget.toPlainText().split())   # normaliza p/ string única
        if kind == "choice":
            return widget.currentData()
        if kind == "device":
            return widget.currentData() or ""         # item selecionado ('(padrão)' -> '')
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
            v = str(value or "")
            if v and widget.findText(v) < 0:
                widget.addItem(v)                     # passthrough de EXIBIÇÃO (não editável)
            widget.setCurrentText(v)
        elif kind == "bigtext":
            widget.setPlainText(str(value or ""))
        elif kind == "choice":
            idx = widget.findData(value)
            if idx < 0:                                 # passthrough: valor fora do mapa
                widget.addItem(str(value), value)
                idx = widget.findData(value)
            widget.setCurrentIndex(idx)
        elif kind == "device":
            v = str(value or "")
            idx = widget.findData(v)
            if idx < 0:                                 # aparelho salvo não enumerado: exibe como item
                widget.addItem(v, v)
                idx = widget.findData(v)
            widget.setCurrentIndex(idx)                 # '' -> '(padrão)'; ou aparelho

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
        self._sync_timesheet_tab()  # o config pode ter mudado fora (wizard/CLI)
        if hasattr(self, "_prompt_editor"):   # prompt.md (não-crítico p/ o guard)
            try:
                from .. import notes

                self._prompt_editor.setPlainText(notes.ensure_prompt_file().read_text(encoding="utf-8"))
            except Exception:
                log.exception("settings: falha ao carregar o prompt.md")
        if hasattr(self, "_context_editor"):  # context.md (não-crítico p/ o guard)
            try:
                from .. import notes

                # load_ e não ensure_: abrir a janela não cria arquivo (o padrão é
                # não ter cabeçalho, e gravar vazio em disco não guarda nada)
                self._context_editor.setPlainText(notes.load_context_note())
            except Exception:
                log.exception("settings: falha ao carregar o context.md")

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
        if hasattr(self, "_prompt_editor"):
            try:
                util.atomic_write_text(util.PROMPT_PATH, self._prompt_editor.toPlainText().strip() + "\n")
            except Exception:
                log.exception("settings: falha ao salvar o prompt.md")
        if hasattr(self, "_context_editor"):
            # editor VAZIO grava vazio de propósito: é como se tira o cabeçalho da
            # nota (load_context_note trata arquivo vazio como "sem cabeçalho").
            try:
                util.atomic_write_text(util.CONTEXT_PATH,
                                       self._context_editor.toPlainText().strip() + "\n")
            except Exception:
                log.exception("settings: falha ao salvar o context.md")
        if self.app is not None:
            try:
                self.app.reload_config()
            except Exception:
                log.exception("settings: reload_config falhou")
        self._saved.setText("✓ Salvo — as próximas reuniões usam estas configurações.")

    # -- janela --------------------------------------------------------------

    def show(self) -> None:  # noqa: A003
        super().show()
        widgets.bring_to_front(self)   # macOS: ativa o app explicitamente (ver qt/__init__)
        if not self._titlebar_done:
            self._titlebar_done = True
            widgets.enable_dark_titlebar(self)
        self._load()   # relê (o config pode ter mudado)
        if not self._devices_loaded:   # enumera dispositivos 1x, em thread (subprocesso lento)
            self._devices_loaded = True
            threading.Thread(target=self._devices_worker, daemon=True, name="devices").start()

    def hide(self) -> None:  # noqa: A003
        self._stop_mic_test()
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
