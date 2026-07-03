"""Diálogos de participantes em PySide6 (fase B / #51). Porta `scriba/speakers_ui.py`.

- `ask_num_speakers`: "Quantas vozes?" ao fim da call (não-bloqueante, always-on-top,
  timeout que cai no automático). Trava num_speakers da diarização (#2).
- `label_speakers_dialog`: "Quem é cada voz?" (#1) — nomeia as vozes de voices.json,
  aprende as MARCADAS e corrige a nota via notes.relabel_speakers. É o diálogo que o
  slice 2 das notas (#49) chama no "Rotular vozes".
- `has_labelable_voices`: puro (reusado pelas notas).

Mesma API pública/comportamento da versão tk; integração real com ScribaApp = fase C.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from . import theme, widgets

log = logging.getLogger("scriba.qt.speakers_ui")


def has_labelable_voices(folder) -> bool:
    """True se a pasta da gravação tem vozes (voices.json) para rotular (#1)."""
    try:
        voices = json.loads((Path(folder) / "voices.json").read_text(encoding="utf-8"))
        return bool(voices)
    except (OSError, ValueError):
        return False


def voice_label_state(folder) -> str:
    """Estado da rotulagem de vozes de uma gravação, para a pendência nas notas:

    - "none": sem vozes suficientes para rotular (voices.json ausente ou < 2 vozes);
    - "pending": há voz anônima ("Participante N") ainda não resolvida E o usuário
      ainda não rotulou nenhuma — a ação está PENDENTE (merece um chamado à ação);
    - "done": as vozes já estão resolvidas (auto-reconhecidas ou com nome próprio) ou
      o usuário já rotulou pelo menos uma — nada mais a cobrar (ícone verde).

    Uma voz conta como resolvida se foi reconhecida na hora (`auto`), rotulada à mão
    (`labeled`, que o `notes.relabel_speakers` grava no voices.json) ou já tem nome
    próprio (rótulo que não começa por "Participante ").

    O voices.json guarda SÓ as vozes do loopback (os OUTROS participantes, nunca o seu
    mic) — por isso 1 voz já é um participante real (não "só você"): basta ela existir."""
    try:
        voices = json.loads((Path(folder) / "voices.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "none"
    if not isinstance(voices, dict) or not voices:
        return "none"

    def _resolved(label, info) -> bool:
        info = info or {}
        return bool(info.get("auto") or info.get("labeled")
                    or not str(label).startswith("Participante "))

    any_labeled = any((info or {}).get("labeled") for info in voices.values())
    has_unresolved = any(not _resolved(k, v) for k, v in voices.items())
    return "pending" if (has_unresolved and not any_labeled) else "done"


def _center_on_screen(win: QWidget, div_y: int = 3) -> None:
    win.adjustSize()
    scr = win.screen().availableGeometry() if win.screen() else None
    if scr is None:
        from PySide6.QtWidgets import QApplication

        scr = QApplication.primaryScreen().availableGeometry()
    x = scr.x() + (scr.width() - win.width()) // 2
    y = scr.y() + (scr.height() - win.height()) // div_y
    win.move(x, y)


class AskNumSpeakers(QWidget):
    """Janela "Quantas vozes?" — chama on_result(int|None) EXATAMENTE uma vez."""

    def __init__(self, on_result: Callable[[int | None], None], last_value: int | None = None,
                 timeout_seconds: int = 90):
        super().__init__()
        self._on_result = on_result
        self._done = False
        self._left = int(timeout_seconds or 0)

        self.setWindowTitle("ScribaDev — Quantas vozes?")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)

        cap = QLabel("Reunião encerrada"); cap.setProperty("role", "muted")
        lay.addWidget(cap)
        q = QLabel("Quantas pessoas, além de você, participaram?")
        q.setStyleSheet("font-size:12pt; font-weight:bold;"); q.setWordWrap(True)
        lay.addWidget(q)
        sub = QLabel("Conte só as outras vozes (não conte você). Isso ajuda a separar os "
                     "participantes na transcrição.")
        sub.setProperty("role", "muted"); sub.setWordWrap(True); sub.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        lay.addWidget(sub)

        self._entry = QLineEdit(str(last_value) if last_value and last_value >= 1 else "")
        self._entry.setPlaceholderText("ex.: 3")
        self._entry.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,3}")))
        self._entry.setAlignment(Qt.AlignCenter)
        self._entry.setStyleSheet("font-size:22pt; font-weight:bold;")
        self._entry.textChanged.connect(self._sync_confirm)
        self._entry.returnPressed.connect(self._confirm)
        lay.addWidget(self._entry)

        btns = QHBoxLayout()
        self._confirm_btn = widgets.ModernButton("Confirmar", self._confirm, kind="primary")
        btns.addWidget(self._confirm_btn)
        btns.addWidget(widgets.ModernButton("Não sei / automático", lambda: self._finish(None)))
        btns.addStretch(1)
        lay.addLayout(btns)

        self._hint = QLabel(""); self._hint.setProperty("role", "muted")
        self._hint.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;"); self._hint.setWordWrap(True)
        lay.addWidget(self._hint)
        self._sync_confirm()

        if self._left > 0:
            self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(1000)
            self._tick()
        # re-afirma o topmost periodicamente (a janela pode ficar atrás enquanto conta)
        self._keep = QTimer(self); self._keep.timeout.connect(self.raise_); self._keep.start(2000)

    def _sync_confirm(self) -> None:
        s = self._entry.text().strip()
        self._confirm_btn.setText(f"Confirmar {s}" if s.isdigit() and int(s) >= 1 else "Confirmar")
        if s != "0":
            self._hint.setText("")

    def _confirm(self) -> None:
        s = self._entry.text().strip()
        if s == "0":
            self._hint.setText('0 não vale — use "Não sei / automático".')
            return
        self._finish(int(s) if s.isdigit() and int(s) >= 1 else None)

    def _tick(self) -> None:
        if self._done:
            return
        if self._left <= 0:
            self._finish(None)
            return
        self._hint.setText(f"Sem resposta em {self._left}s → modo automático.")
        self._left -= 1

    def _finish(self, value: int | None) -> None:
        if self._done:
            return
        self._done = True
        self.close()
        self._on_result(value)

    def closeEvent(self, event) -> None:
        # fechar no X = automático (uma vez só)
        if not self._done:
            self._done = True
            self._on_result(None)
        super().closeEvent(event)

    def show(self) -> None:  # noqa: A003
        super().show()
        _center_on_screen(self)
        self.raise_()
        self.activateWindow()
        widgets.enable_dark_titlebar(self)
        self._entry.setFocus()
        self._entry.selectAll()


def ask_num_speakers(on_result: Callable[[int | None], None], last_value: int | None = None,
                     timeout_seconds: int = 90) -> AskNumSpeakers:
    """Abre a janela (não-bloqueante) e devolve a instância (o chamador guarda a ref)."""
    win = AskNumSpeakers(on_result, last_value, timeout_seconds)
    win.show()
    return win


class LabelSpeakersDialog(QWidget):
    """"Quem é cada voz?" — nomeia as vozes e aprende as MARCADAS (#1)."""

    def __init__(self, folder, on_saved: Callable[[], None] | None = None,
                 guesses: dict[str, str] | None = None):
        super().__init__()
        self._folder = Path(folder)
        self._on_saved = on_saved
        self._closed = False
        self._rows: list[tuple[str, QLineEdit, QCheckBox]] = []
        guesses = guesses or {}

        self.setWindowTitle("ScribaDev — Quem é cada voz?")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)

        try:
            voices: dict = json.loads((self._folder / "voices.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            voices = {}
        if not voices:
            lay.addWidget(QLabel("Esta reunião não tem vozes separadas para rotular."))
            lay.addWidget(widgets.ModernButton("Fechar", self.close, kind="primary"))
            return

        title = QLabel("Rotular participantes")
        title.setStyleSheet("font-size:12pt; font-weight:bold;")
        lay.addWidget(title)
        sub = QLabel("Revise o nome de cada voz e MARQUE o check só nas que tiver certeza — só as "
                     "marcadas são salvas. O ScribaDev aprende as marcadas e corrige esta nota na hora.")
        sub.setProperty("role", "muted"); sub.setWordWrap(True); sub.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        lay.addWidget(sub)

        t = theme.active()
        for label, info in voices.items():
            slabel = str(label)
            info = info or {}
            auto = bool(info.get("auto"))
            labeled = bool(info.get("labeled"))
            anon = slabel.startswith("Participante ")
            guess = (guesses.get(slabel) or "").strip()
            prefill = guess or ("" if anon else slabel)
            if auto:
                base, color = "✓ reconhecido", t.ok
            elif labeled:
                base, color = "✎ você rotulou", t.text
            elif anon and guess:
                base, color = "★ palpite", t.warn
            else:
                base, color = "○ confirmar", t.muted

            row = QHBoxLayout()
            name_lbl = QLabel(slabel); name_lbl.setFixedWidth(120); name_lbl.setStyleSheet("font-weight:bold;")
            row.addWidget(name_lbl)
            entry = QLineEdit(prefill)
            entry.setPlaceholderText("nome desta pessoa")
            row.addWidget(entry, 1)
            chk = widgets.AnimatedCheckBox()
            chk.setChecked(bool(auto or labeled))

            def _sync(on, _chk=chk, _base=base, _color=color):
                _chk.setText("✓ Confirmado" if on else _base)
                _chk.setStyleSheet(f"color:{t.ok if on else _color}; font-size:{t.font_size_small}pt;")

            chk.toggled.connect(lambda on, f=_sync: f(on))
            _sync(chk.isChecked())
            row.addWidget(chk)
            lay.addLayout(row)
            self._rows.append((slabel, entry, chk))

        self._status = QLabel(""); self._status.setWordWrap(True); self._status.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        lay.addWidget(self._status)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(widgets.ModernButton("Cancelar", self._cancel))
        btns.addWidget(widgets.ModernButton("Salvar", self._save, kind="primary"))
        lay.addLayout(btns)

    def _save(self) -> None:
        if self._closed:
            return
        t = theme.active()
        if any(chk.isChecked() and not entry.text().strip() for _l, entry, chk in self._rows):
            self._status.setStyleSheet(f"color:{t.warn}; font-size:{t.font_size_small}pt;")
            self._status.setText("Dê um nome às vozes marcadas antes de salvar (ou desmarque-as).")
            return
        renames = {label: entry.text().strip() for label, entry, chk in self._rows
                   if chk.isChecked() and entry.text().strip() and entry.text().strip() != label}
        if renames:
            from .. import notes

            try:
                notes.relabel_speakers(self._folder, renames)
            except Exception:
                log.exception("falha ao rotular vozes")
            n = len(renames)
            self._status.setStyleSheet(f"color:{t.ok}; font-size:{t.font_size_small}pt;")
            self._status.setText(f"✓ Aprendi {n} voz{'es' if n != 1 else ''} para as próximas reuniões.")
            QTimer.singleShot(1400, self._close)
        else:
            self._close()

    def _cancel(self) -> None:
        self._closed = True
        self.close()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._on_saved is not None:
            self._on_saved()
        self.close()

    def show(self) -> None:  # noqa: A003
        super().show()
        _center_on_screen(self)
        self.raise_()
        self.activateWindow()
        widgets.enable_dark_titlebar(self)


def label_speakers_dialog(folder, on_saved: Callable[[], None] | None = None,
                          guesses: dict[str, str] | None = None) -> LabelSpeakersDialog:
    win = LabelSpeakersDialog(folder, on_saved, guesses)
    win.show()
    return win


# --------------------------------------------------------------- harness ------

def main() -> int:
    import sys
    import tempfile

    from PySide6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)

    folder = Path(tempfile.mkdtemp(prefix="scriba_qt_spk_"))
    (folder / "voices.json").write_text(json.dumps({
        "Ana": {"auto": True}, "Participante 2": {}, "Participante 3": {},
    }), encoding="utf-8")

    ask = ask_num_speakers(lambda n: log.info("num_speakers = %r", n), last_value=2, timeout_seconds=30)  # noqa: F841
    dlg = label_speakers_dialog(folder, on_saved=lambda: log.info("rotulado"),  # noqa: F841
                                guesses={"Participante 2": "Bruno"})
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()
    os._exit(rc)
