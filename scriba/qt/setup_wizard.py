"""Wizard de PRIMEIRO USO (#147, épico #138): análise da máquina + downloads.

Não confundir com o wizard de PERFIL (wizard_ui.py, prompt.md/hotwords). Este
aqui roda uma vez após a instalação e resolve o que o instalador enxuto (#142)
deixou de fora, guiado pela sonda de hardware (scriba.sysprobe):

  1. Sua máquina   — retrato + recomendações; escolha Expressa/Avançada
  2. Modelo        — (só Avançada) tiny…large-v3-turbo com tamanho/velocidade
  3. Vozes         — termos do pyannote + token HF, SEMPRE skippável
  4. Downloads     — progresso do que foi escolhido
  5. Pronto

Expressa = aceita as recomendações e pula direto p/ a página de Vozes (o aceite
dos termos do HF é inerentemente manual) e então Downloads. Tudo que o wizard
grava usa o config.toml normal (config_mod.save) — as Configurações continuam
mandando depois. `_inline_bg = True` nos testes roda os workers na própria
thread (padrão da casa).
"""

from __future__ import annotations

import dataclasses
import logging
import sys
import threading
import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import sysprobe, updates, util
from . import theme, widgets

log = logging.getLogger("scriba.qt.setup_wizard")

_STATE_KEY = "setup_wizard_done"

# (modelo, rótulo de velocidade) na ordem do combo — tamanhos de download vêm de
# sysprobe.MODEL_DOWNLOAD_MB
_MODELS = (
    ("tiny", "o mais rápido, qualidade básica"),
    ("base", "rápido, qualidade simples"),
    ("small", "equilíbrio bom em CPU"),
    ("medium", "qualidade alta, CPU forte ou GPU"),
    ("large-v3-turbo", "a melhor qualidade (GPU/Apple Silicon)"),
)

# os TRÊS modelos gated: o community-1 é o que o pyannote 4.x realmente usa; o
# 3.1 e o segmentation seguem no fluxo (fallback/config antiga) — mesma lista do README
HF_TERMS_URLS = (
    "https://huggingface.co/pyannote/speaker-diarization-community-1",
    "https://huggingface.co/pyannote/speaker-diarization-3.1",
    "https://huggingface.co/pyannote/segmentation-3.0",
)


def is_done() -> bool:
    return bool(util.read_state().get(_STATE_KEY))


def mark_done() -> None:
    util.update_state(**{_STATE_KEY: True})


def should_run() -> bool:
    """Gate do 1º uso: instalação congelada que ainda não completou o wizard.
    Instalação git/venv (dev e contribuidores, já configurada) nunca o vê."""
    return updates.is_frozen_install() and not is_done()


class SetupWizardWindow(QWidget):
    """Janela do wizard de 1º uso. `on_finish` (opcional) roda ao concluir."""

    _progress = Signal(str)          # linha de status do download (marshal p/ GUI)
    _done_dl = Signal(bool, str)     # fim do worker de downloads
    _inline_bg = False               # classe (padrão NotesWindow): testes ligam
                                     # ANTES de construir p/ a sonda rodar inline

    def __init__(self, app=None, on_finish=None):
        super().__init__()
        self.app = app
        self.on_finish = on_finish
        self._titlebar_done = False
        self.probe: sysprobe.Probe | None = None
        self.rec: sysprobe.Recommendation | None = None
        self.express = True
        self.skip_voices = False

        self.setWindowTitle("ScribaDev - Primeiros passos")
        self.setMinimumSize(620, 520)
        widgets.remember_geometry(self, "qt_setup_wizard", default=(220, 140, 680, 560))

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(10)
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        self._stack.addWidget(self._page_machine())   # 0
        self._stack.addWidget(self._page_model())     # 1
        self._stack.addWidget(self._page_voices())    # 2
        self._stack.addWidget(self._page_download())  # 3
        self._stack.addWidget(self._page_ready())     # 4

        self._progress.connect(self._append_progress)
        self._done_dl.connect(self._downloads_finished)
        self._run_probe()

    # ---------------------------------------------------------------- páginas --

    def _title(self, lay, text: str, sub: str = "") -> None:
        t = theme.active()
        big = QLabel(text)
        big.setStyleSheet(f"font-size:{t.font_size + 5}pt; font-weight:bold;")
        lay.addWidget(big)
        if sub:
            s = QLabel(sub)
            s.setWordWrap(True)
            s.setStyleSheet(f"color:{t.muted};")
            lay.addWidget(s)

    def _nav(self, lay, back=None, next_label="Continuar", next_cb=None, skip=None) -> None:
        row = QHBoxLayout()
        if back is not None:
            row.addWidget(widgets.ModernButton("Voltar", back))
        row.addStretch(1)
        if skip is not None:
            row.addWidget(widgets.ModernButton(skip[0], skip[1]))
        b = widgets.ModernButton(next_label, next_cb, kind="primary")
        row.addWidget(b)
        lay.addLayout(row)

    def _page_machine(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._title(lay, "Vamos preparar o ScribaDev",
                    "Analisamos sua máquina e recomendamos a melhor configuração. "
                    "Nada sai do seu computador.")
        self._machine_box = QLabel("Analisando sua máquina…")
        self._machine_box.setWordWrap(True)
        self._machine_box.setTextFormat(Qt.RichText)
        lay.addWidget(self._machine_box)
        lay.addSpacing(6)
        self._rb_express = QRadioButton("Instalação Expressa - aplicar as recomendações e baixar tudo")
        self._rb_custom = QRadioButton("Instalação Avançada - escolher modelo e componentes")
        self._rb_express.setChecked(True)
        g = QButtonGroup(w)
        g.addButton(self._rb_express)
        g.addButton(self._rb_custom)
        lay.addWidget(self._rb_express)
        lay.addWidget(self._rb_custom)
        lay.addStretch(1)
        self._nav(lay, next_cb=self._from_machine)
        return w

    def _page_model(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._title(lay, "Modelo de transcrição",
                    "Modelos maiores transcrevem melhor, mas pesam mais. "
                    "A opção marcada é a recomendada para a sua máquina.")
        self._model_group = QButtonGroup(w)
        self._model_radios: dict[str, QRadioButton] = {}
        for name, hint in _MODELS:
            mb = sysprobe.MODEL_DOWNLOAD_MB.get(name, 0)
            rb = QRadioButton(f"{name}  ·  ~{mb} MB  ·  {hint}")
            self._model_group.addButton(rb)
            self._model_radios[name] = rb
            lay.addWidget(rb)
        lay.addStretch(1)
        self._nav(lay, back=lambda: self._stack.setCurrentIndex(0),
                  next_cb=lambda: self._stack.setCurrentIndex(2))
        return w

    def _step_row(self, lay, num: int, html: str, btn_label: str = "", btn_cb=None) -> None:
        """Um passo do tutorial: número em destaque + texto explicativo (com quebra)
        e, quando faz sentido, o botão que abre a página certa ao lado."""
        t = theme.active()
        row = QHBoxLayout()
        n = QLabel(str(num))
        n.setFixedWidth(26)
        n.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        n.setStyleSheet(f"font-size:{t.font_size + 3}pt; font-weight:bold; color:{t.accent_hover};")
        row.addWidget(n, 0, Qt.AlignTop)
        lbl = QLabel(html)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        row.addWidget(lbl, 1)
        if btn_label:
            row.addWidget(widgets.ModernButton(btn_label, btn_cb), 0, Qt.AlignTop)
        lay.addLayout(row)
        lay.addSpacing(8)

    def _page_voices(self) -> QWidget:
        from .settings_ui import _wrap_secret

        w = QWidget()
        lay = QVBoxLayout(w)
        self._title(lay, "Separar quem falou (opcional)",
                    "Com a separação de vozes, a nota mostra quem disse o quê "
                    "(\"Participante 1\", \"Participante 2\"…). Ela usa os modelos pyannote - "
                    "gratuitos, mas você precisa aceitar os termos e usar um token do "
                    "Hugging Face. Leva uns 2 minutos; dá para pular e fazer depois.")
        self._voices_hint = QLabel("")
        self._voices_hint.setWordWrap(True)
        lay.addWidget(self._voices_hint)
        lay.addSpacing(10)
        self._step_row(
            lay, 1,
            "<b>Aceite os termos dos três modelos.</b><br>"
            "Entre (ou crie uma conta gratuita) no Hugging Face; em cada uma das "
            "páginas que vão abrir, preencha o formulário curto e clique em "
            "<b>\"Agree and access repository\"</b>.",
            "Abrir as três páginas", lambda: [webbrowser.open(u) for u in HF_TERMS_URLS])
        self._step_row(
            lay, 2,
            "<b>Gere um token de acesso.</b><br>"
            "Na página de tokens, clique em <b>\"Create new token\"</b>, escolha o tipo "
            "<b>Read</b> (basta), dê um nome (ex.: \"scriba\") e clique em "
            "<b>\"Create token\"</b>. Copie o valor - ele começa com <b>hf_</b> e só "
            "aparece uma vez.",
            "Abrir a página de tokens",
            lambda: webbrowser.open("https://huggingface.co/settings/tokens"))
        self._step_row(lay, 3, "<b>Cole o token aqui:</b>")
        self._hf_token = QLineEdit()
        self._hf_token.setEchoMode(QLineEdit.Password)
        self._hf_token.setPlaceholderText("hf_...")
        form = QHBoxLayout()
        form.addSpacing(34)  # alinha com o texto dos passos (26 do número + espaçamento)
        form.addWidget(_wrap_secret(self._hf_token), 1)
        lay.addLayout(form)
        lay.addStretch(1)
        self._nav(lay, back=self._voices_back,
                  skip=("Pular por enquanto", self._skip_voices),
                  next_label="Ativar e continuar", next_cb=self._accept_voices)
        return w

    def _page_download(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._title(lay, "Baixando o que falta",
                    "Só nesta primeira vez. Os downloads vão para a sua máquina e ficam lá.")
        self._dl_summary = QLabel("")
        self._dl_summary.setWordWrap(True)
        lay.addWidget(self._dl_summary)
        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # indeterminado; % real fica p/ evolução da issue
        lay.addWidget(self._bar)
        self._dl_log = QLabel("")
        self._dl_log.setWordWrap(True)
        self._dl_log.setStyleSheet(f"color:{theme.active().muted};")
        lay.addWidget(self._dl_log)
        lay.addStretch(1)
        return w

    def _page_ready(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._title(lay, "Tudo pronto!",
                    "O ScribaDev fica na bandeja e detecta suas calls sozinho. "
                    "Boa reunião!")
        self._ready_box = QLabel("")
        self._ready_box.setWordWrap(True)
        lay.addWidget(self._ready_box)
        lay.addStretch(1)
        self._nav(lay, next_label="Começar a usar", next_cb=self._finish)
        return w

    # ------------------------------------------------------------ sonda/fluxo --

    def _run_probe(self) -> None:
        def work():
            p = sysprobe.probe()
            r = sysprobe.recommend(p)
            ui = (lambda f: f()) if self._inline_bg else (
                lambda f: self.app.ui(f) if self.app else f())
            ui(lambda: self._show_probe(p, r))

        if self._inline_bg:
            work()
        else:
            threading.Thread(target=work, daemon=True, name="setup-probe").start()

    def _show_probe(self, p: sysprobe.Probe, r: sysprobe.Recommendation) -> None:
        self.probe, self.rec = p, r
        t = theme.active()
        gpu = (f"GPU NVIDIA ({round((p.vram_mb or 0) / 1024)} GB)" if p.gpu_nvidia
               else "Apple Silicon" if p.apple_silicon else "sem GPU dedicada")
        facts = (f"<b>Sua máquina:</b> {p.cpu_cores or '?'} núcleos · "
                 f"{p.ram_gb or '?'} GB RAM · {gpu} · {p.disk_free_gb or '?'} GB livres")
        reasons = "".join(f"<li>{x}</li>" for x in r.reasons)
        self._machine_box.setText(
            f"{facts}<ul style='color:{t.muted}'>{reasons}</ul>")
        rb = self._model_radios.get(r.whisper_model)
        if rb is not None:
            rb.setChecked(True)
            rb.setText(rb.text() + "  ← recomendado")
            f = rb.font()
            f.setBold(True)
            rb.setFont(f)
        self._voices_hint.setText({
            "recomendada": "Recomendado para a sua máquina: a GPU dá conta sem esforço.",
            "opcional": "Funciona nesta máquina, mas mais lento - você decide.",
            "desaconselhada": "Nesta máquina tende a ficar lento - sugerimos pular por enquanto.",
        }.get(r.diarization, ""))

    def _from_machine(self) -> None:
        self.express = self._rb_express.isChecked()
        # Expressa pula a escolha de modelo (fica a recomendada); os termos do
        # pyannote são inerentemente manuais → a página de Vozes aparece SEMPRE
        # que a diarização não for desaconselhada
        if not self.express:
            self._stack.setCurrentIndex(1)
        elif self.rec and self.rec.diarization == "desaconselhada":
            self._skip_voices()
        else:
            self._stack.setCurrentIndex(2)

    def _voices_back(self) -> None:
        self._stack.setCurrentIndex(0 if self.express else 1)

    def _selected_model(self) -> str:
        for name, rb in self._model_radios.items():
            if rb.isChecked():
                return name
        return (self.rec.whisper_model if self.rec else "small")

    def _skip_voices(self) -> None:
        self.skip_voices = True
        self._start_downloads()

    def _accept_voices(self) -> None:
        self.skip_voices = not bool(self._hf_token.text().strip())
        self._start_downloads()

    # ------------------------------------------------------------- downloads --

    def _plan(self) -> list[str]:
        """Itens a baixar, já em texto de exibição."""
        model = self._selected_model()
        items = [f"modelo de transcrição {model} "
                 f"(~{sysprobe.MODEL_DOWNLOAD_MB.get(model, 0)} MB)"]
        if self.rec and self.rec.needs_cuda_libs and updates.is_frozen_install():
            items.append("bibliotecas NVIDIA (cuBLAS/cuDNN, ~3 GB)")
        if not self.skip_voices:
            items.append("separação de vozes (torch + pyannote, ~3 GB)")
        return items

    def _start_downloads(self) -> None:
        self._save_config()
        self._stack.setCurrentIndex(3)
        self._dl_summary.setText("Vai baixar: " + "; ".join(self._plan()) + ".")
        if self._inline_bg:
            self._download_worker()
        else:
            threading.Thread(target=self._download_worker, daemon=True,
                             name="setup-downloads").start()

    def _download_worker(self) -> None:
        """Baixa modelo + extras. Instalação git/venv: pip na venv (sys.executable);
        congelada: addons in-process (scriba.addons). Erro não trava o wizard - vira
        aviso e o item fica p/ as Configurações."""
        import subprocess

        ok_all, notes = True, []
        emit = self._progress.emit
        try:
            model = self._selected_model()
            emit(f"baixando o modelo {model}…")
            try:
                from faster_whisper import WhisperModel

                WhisperModel(model, device="cpu", compute_type="int8")  # só baixa/cacheia
                emit(f"modelo {model} pronto.")
            except Exception as e:
                ok_all = False
                notes.append(f"modelo: {e}")
                emit(f"modelo falhou ({e}) - o app baixa na primeira transcrição.")
            extras = []
            if self.rec and self.rec.needs_cuda_libs and updates.is_frozen_install():
                extras += ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
            if not self.skip_voices:
                extras += ["torch", "pyannote.audio>=4,<5"]
            if extras:
                emit("instalando componentes: " + ", ".join(extras) + "…")
                if updates.is_frozen_install():
                    from .. import addons

                    ok, msg = addons.install_to_addons(extras, progress=emit)
                else:
                    r = subprocess.run(
                        [updates._pip_interpreter(sys.executable), "-m", "pip",
                         "install", *extras],
                        capture_output=True, text=True, timeout=3600,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    ok, msg = r.returncode == 0, (r.stderr or r.stdout or "")[-400:]
                if not ok:
                    ok_all = False
                    notes.append(f"componentes: {msg}")
                    emit("componentes falharam - dá para tentar de novo nas Configurações.")
                else:
                    emit("componentes instalados.")
        except Exception as e:  # rede caiu no meio etc.: wizard nunca trava
            log.exception("downloads do wizard falharam")
            ok_all, notes = False, notes + [str(e)]
        self._done_dl.emit(ok_all, "; ".join(notes))

    def _append_progress(self, line: str) -> None:
        self._dl_log.setText((self._dl_log.text() + "\n" + line).strip())

    def _downloads_finished(self, ok: bool, notes: str) -> None:
        self._bar.setRange(0, 1)
        self._bar.setValue(1)
        extra = "" if ok else (
            "<br><br><b>Alguns itens não baixaram</b> - sem problema: o app funciona "
            f"e você tenta de novo nas Configurações. Detalhe: {notes}")
        model = self._selected_model()
        voices = "pulada (ative nas Configurações)" if self.skip_voices else "ativada"
        self._ready_box.setText(
            f"Transcrição: <b>{model}</b> · Separação de vozes: <b>{voices}</b>.{extra}")
        self._stack.setCurrentIndex(4)

    # ------------------------------------------------------------ persistência --

    def _save_config(self) -> None:
        """Grava as escolhas no config.toml (mesmo caminho das Configurações)."""
        try:
            cfg = config_mod.load()
            device = {"cuda": "auto", "mlx": "auto", "cpu": "cpu"}.get(
                self.rec.device if self.rec else "cpu", "auto")
            whisper = dataclasses.replace(cfg.whisper,
                                          model=self._selected_model(), device=device)
            dz = cfg.diarization
            if not self.skip_voices:
                dz = dataclasses.replace(dz, enabled=True,
                                         hf_token=self._hf_token.text().strip())
            cfg = dataclasses.replace(cfg, whisper=whisper, diarization=dz)
            config_mod.save(cfg)
        except Exception:
            log.exception("wizard: falha ao salvar config (seguindo)")

    def _finish(self) -> None:
        mark_done()
        if callable(self.on_finish):
            self.on_finish()
        self.close()

    # ------------------------------------------------------------- ciclo de vida --

    def show(self):
        super().show()
        self.raise_()
        self.activateWindow()
        if not self._titlebar_done:
            self._titlebar_done = True
            widgets.enable_dark_titlebar(self)
