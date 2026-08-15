"""Assistente de perfil (wizard) em PySide6 (fase B / #51). Porta `scriba/wizard_ui.py`.

Descreve profissão/área/stack/jargão; a IA (ou um modelo pronto offline) escreve o
prompt.md que molda o resumo + as hotwords da transcrição. Nada é gravado sem o
usuário ver a prévia e clicar Aplicar. A geração (promptgen, pura) roda em thread e
volta à UI por Signal. API: WizardWindow(app=None, on_applied=None).show()/hide().
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import promptgen
from . import theme, widgets

log = logging.getLogger("scriba.qt.wizard_ui")


class WizardWindow(QWidget):
    _prompt_ready = Signal(object)   # (prompt, hotwords) | None
    _jargon_ready = Signal(object)   # str | None

    def __init__(self, app=None, on_applied=None, standalone=False):
        super().__init__()
        self.app = app
        self.on_applied = on_applied
        self._standalone = standalone   # `scriba wizard`: fechar encerra (não esconde)
        self._result: tuple[str, str] | None = None
        self._busy = False
        self._titlebar_done = False

        self.setWindowTitle("ScribaDev — Assistente de perfil")
        self.setMinimumSize(780, 640)
        self.setWindowOpacity(0.98)
        self.resize(840, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)

        h = QLabel("Atas na língua da sua profissão")
        h.setStyleSheet(f"font-size:{theme.zpt(13)}pt; font-weight:bold;"); root.addWidget(h)
        sub = QLabel("Descreva seu perfil e o ScribaDev escreve as instruções (prompt.md) que moldam o "
                     "resumo das suas reuniões — e o vocabulário que guia a transcrição. Você revisa antes.")
        sub.setProperty("role", "muted"); sub.setWordWrap(True); root.addWidget(sub)
        warn = QLabel("Ao gerar com IA, os dados deste formulário são enviados ao provedor de IA configurado.")
        warn.setProperty("role", "warn"); warn.setWordWrap(True); warn.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        root.addWidget(warn)

        base_row = QHBoxLayout()
        base_lbl = QLabel("Perfil base"); base_lbl.setFixedWidth(150); base_row.addWidget(base_lbl)
        self._base = QComboBox()
        self._base.addItems(list(promptgen.BASE_LABELS.values()))
        self._base.setCurrentText(promptgen.BASE_LABELS["generic"])
        base_row.addWidget(self._base); base_row.addStretch(1)
        root.addLayout(base_row)

        self._role = self._entry_row(root, "Profissão / papel", "ex.: Gerente de projetos de TI")
        self._area = self._entry_row(root, "Área / indústria", "ex.: varejo, projetos SAP, logística")
        self._stack = self._entry_row(root, "Stack / ferramentas", "ex.: Jira, MS Project, SAP, Excel")

        self._jargon, self._jargon_link = self._text_row(
            root, "Jargão e termos frequentes",
            "termos que aparecem nas suas calls (a transcrição aprende com eles)",
            action=("Sugerir com IA", self._suggest_jargon))
        self._musthave, _ = self._text_row(root, "O que não pode faltar na ata",
                                           "ex.: ações com responsável e prazo, riscos, decisões")

        # apontamento de horas (#118/#126): opt-in do onboarding. Só oferece quando o
        # módulo está DORMENTE; pular (desmarcado) = zero side effects na máquina.
        self._timesheet_opt = None
        try:
            from .. import config as _config

            ts_dormente = not _config.load().timesheet.enabled
        except Exception:
            ts_dormente = False   # em dúvida, não oferece: ativação exige certeza
        if ts_dormente:
            from PySide6.QtWidgets import QFrame

            t = theme.active()
            card = QFrame()
            card.setObjectName("wizTimesheet")
            card.setStyleSheet(
                f"QFrame#wizTimesheet {{ background:{t.surface}; border:1.5px solid {t.warn};"
                f" border-radius:{t.radius}px; }}"
                f"QFrame#wizTimesheet QLabel {{ border:none; background:transparent; }}")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 8, 12, 10)
            cl.setSpacing(4)
            ct = QLabel("Novo: apontamento de horas (opcional)")
            ct.setStyleSheet(f"font-weight:bold; color:{t.warn};")
            cd = QLabel("Reuniões processadas viram sugestões de apontamento (cliente, horários, "
                        "descrição), com entrada manual e exportação Excel. Ativar cria o banco "
                        "local timesheet.db; sem ativar, nada roda nem é criado.")
            cd.setProperty("role", "muted")
            cd.setWordWrap(True)
            self._timesheet_opt = widgets.AnimatedCheckBox("Ativar o apontamento de horas ao aplicar")
            cl.addWidget(ct)
            cl.addWidget(cd)
            cl.addWidget(self._timesheet_opt)
            root.addWidget(card)

        btns = QHBoxLayout()
        self._ai_btn = widgets.ModernButton("Gerar com IA (recomendado)", self._generate_ai, kind="primary")
        self._tpl_btn = widgets.ModernButton("Usar modelo pronto", self._generate_template)
        self._apply_btn = widgets.ModernButton("Aplicar", self._apply)
        btns.addWidget(self._ai_btn); btns.addWidget(self._tpl_btn); btns.addStretch(1); btns.addWidget(self._apply_btn)
        root.addLayout(btns)

        self._status = QLabel(""); self._status.setProperty("role", "muted")
        self._status.setStyleSheet("font-style:italic;"); root.addWidget(self._status)

        pv = QLabel("Prévia do prompt (o que vai reger as suas atas)"); pv.setStyleSheet("font-weight:bold;")
        root.addWidget(pv)
        self._preview = QPlainTextEdit(); self._preview.setReadOnly(True)
        root.addWidget(self._preview, 1)
        self._hot = QLabel(""); self._hot.setProperty("role", "muted")
        self._hot.setWordWrap(True); self._hot.setStyleSheet(f"font-size:{theme.active().font_size_small}pt;")
        root.addWidget(self._hot)

        self._prompt_ready.connect(self._on_prompt)
        self._jargon_ready.connect(self._on_jargon)

    # -- blocos --------------------------------------------------------------

    def _entry_row(self, root, label: str, hint: str) -> QLineEdit:
        row = QHBoxLayout()
        lbl = QLabel(label); lbl.setFixedWidth(150); row.addWidget(lbl)
        e = widgets.make_entry(hint); row.addWidget(e, 1)
        root.addLayout(row)
        return e

    def _text_row(self, root, label: str, hint: str, action=None):
        head = QHBoxLayout()
        head.addWidget(QLabel(label)); head.addStretch(1)
        link = None
        if action:
            link = widgets.ModernButton(action[0], action[1])
            head.addWidget(link)
        root.addLayout(head)
        txt = QPlainTextEdit(); txt.setFixedHeight(48); txt.setPlaceholderText(hint)
        root.addWidget(txt)
        return txt, link

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for b in (self._ai_btn, self._tpl_btn, self._apply_btn):
            b.setEnabled(not busy)

    # -- geração -------------------------------------------------------------

    def _profile(self) -> "promptgen.Profile":
        label_to_key = {v: k for k, v in promptgen.BASE_LABELS.items()}
        return promptgen.Profile(
            base=label_to_key.get(self._base.currentText(), "generic"),
            role=self._role.text().strip(),
            area=self._area.text().strip(),
            stack=self._stack.text().strip(),
            jargon=self._jargon.toPlainText().strip(),
            must_have=self._musthave.toPlainText().strip(),
        )

    def _generate_template(self) -> None:
        if self._busy:
            return
        prompt, hotwords = promptgen.template_prompt(self._profile())
        self._show_result(prompt, hotwords, "modelo pronto")

    def _generate_ai(self) -> None:
        if self._busy:
            return
        profile = self._profile()
        self._set_busy(True)
        self._ai_btn.setText("Gerando…")
        self._status.setText("Gerando com IA… isso leva por volta de um minuto.")
        threading.Thread(target=lambda: self._prompt_ready.emit(_safe(promptgen.ai_prompt, profile)),
                         daemon=True, name="wizard-ai").start()

    def _on_prompt(self, outcome) -> None:
        self._set_busy(False)
        self._ai_btn.setText("Gerar com IA (recomendado)")
        if outcome:
            prompt, hotwords = outcome
            self._show_result(prompt, hotwords, "gerado por IA e validado")
        else:
            self._status.setText('Não consegui gerar com IA agora (claude indisponível ou resposta '
                                 'fora do padrão). O botão "Usar modelo pronto" funciona offline.')

    def _suggest_jargon(self) -> None:
        if self._busy:
            return
        profile = self._profile()
        self._set_busy(True)
        self._jargon_link.setText("Sugerindo…")
        self._status.setText("Buscando o jargão típico do seu perfil…")
        threading.Thread(target=lambda: self._jargon_ready.emit(_safe(promptgen.suggest_jargon, profile)),
                         daemon=True, name="wizard-jargon").start()

    def _on_jargon(self, suggested) -> None:
        self._set_busy(False)
        self._jargon_link.setText("Sugerir com IA")
        if not suggested:
            self._status.setText("Não consegui sugerir agora (claude indisponível). Digite alguns termos "
                                 "que você ouve nas reuniões — ou siga sem: o campo é opcional.")
            return
        current = [t.strip() for t in self._jargon.toPlainText().split(",") if t.strip()]
        merged = list(current)
        for term in (t.strip() for t in suggested.split(",")):
            if term and term.lower() not in (m.lower() for m in merged):
                merged.append(term)
        self._jargon.setPlainText(", ".join(merged))
        self._status.setText(f"{len(merged) - len(current)} termo(s) sugerido(s) — corte o que não fizer sentido.")

    def _show_result(self, prompt: str, hotwords: str, origin: str) -> None:
        self._result = (prompt, hotwords)
        self._preview.setPlainText(prompt)
        self._hot.setText(f"Vocabulário para a transcrição (hotwords): {hotwords or '(mantém o atual)'}")
        self._status.setText(f"Prévia pronta ({origin}). Revise e clique em Aplicar.")

    def _apply(self) -> None:
        if self._busy:
            return
        if not self._result:
            self._status.setText("Gere uma prévia primeiro (IA ou modelo pronto).")
            return
        profile = self._profile()
        if not any((profile.role, profile.area, profile.stack, profile.jargon, profile.must_have)):
            if QMessageBox.question(self, "Aplicar perfil genérico?",
                                    "Nenhum campo preenchido — aplicar o modelo genérico?") != QMessageBox.Yes:
                return
        prompt, hotwords = self._result
        context_note = promptgen.context_note_for(profile)
        backup = promptgen.apply_prompt(prompt, hotwords or None, context_note)
        promptgen.mark_wizard_done()
        if self.app is not None:
            try:
                self.app.reload_config()
            except Exception:
                pass
        extra = f" O prompt anterior ficou em {backup.name}." if backup else ""
        if self._timesheet_opt is not None and self._timesheet_opt.isChecked():
            threading.Thread(target=self._activate_timesheet, daemon=True,
                             name="wizard-timesheet").start()
            extra += " Ativando o apontamento de horas…"
        self._status.setText(f"Aplicado ✓ — as próximas atas usam este perfil.{extra}")
        if self.on_applied:
            QTimer.singleShot(1200, self.on_applied)

    def _activate_timesheet(self) -> None:
        """Opt-in do onboarding (#126): roda a rotina única de ativação em thread."""
        try:
            from .. import timesheet_suggest

            n = timesheet_suggest.activate()
            log.info("timesheet ativado pelo wizard (%d sugestão(ões) inicial(is))", n)
        except Exception:
            log.exception("ativação do timesheet pelo wizard falhou")
            return
        if self.app is not None:
            try:
                self.app.reload_config()
            except Exception:
                pass

    # -- janela --------------------------------------------------------------

    def show(self) -> None:  # noqa: A003
        super().show()
        self.raise_()
        self.activateWindow()
        if not self._titlebar_done:
            self._titlebar_done = True
            widgets.enable_dark_titlebar(self)

    def hide(self) -> None:  # noqa: A003
        super().hide()

    def closeEvent(self, event) -> None:
        if self._standalone:      # cli `scriba wizard`: fechar realmente encerra
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


# --------------------------------------------------------------- harness ------

def main() -> int:
    import sys

    from PySide6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    theme.apply(app)
    win = WizardWindow()
    win.resize(820, 700)
    win.show()
    return app.exec()


if __name__ == "__main__":
    import os

    rc = main()
    logging.shutdown()
    os._exit(rc)
