"""Camada de UI em PySide6 (épico #44).

Durante a migração (fases A-B do épico), NADA aqui é importado pelo app em
produção — o ScribaDev segue 100% tkinter até o corte (fase C, #53). Cada módulo
é testável isolado por um harness próprio (`python -m scriba.qt.<modulo>`), porque
tkinter e Qt não convivem no mesmo processo (dois event loops disputando a main
thread). Só no corte o `main.py` troca o loop do Tk por um QApplication e passa a
importar estas janelas.
"""

import os
import sys

# macOS: o QPA cocoa termina TODO `QCocoaWindow::raise()` com
# `[NSApp activateIgnoringOtherApps:YES]`, isto é, traz o processo INTEIRO para a
# frente. Isso inclui o raise IMPLÍCITO que o `QWidget::show()` faz em janelas
# `Qt.Tool`/`Qt.ToolTip` — a pílula de gravação e o seu hint de hover. Resultado:
# mostrar a pílula roubava o foco de quem estava no navegador durante a call.
# Desligamos o comportamento globalmente e trazemos o app para a frente
# EXPLICITAMENTE onde isso é intenção do usuário (`widgets.bring_to_front`, usado
# pelas janelas que abrem pela bandeja). Precisa valer antes do primeiro raise():
# a env é lida uma única vez, num `static` dentro do QCocoaWindow::raise().
if sys.platform == "darwin":
    os.environ.setdefault("QT_MAC_SET_RAISE_PROCESS", "0")
