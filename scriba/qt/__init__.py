"""Camada de UI em PySide6 (épico #44).

Durante a migração (fases A-B do épico), NADA aqui é importado pelo app em
produção — o ScribaDev segue 100% tkinter até o corte (fase C, #53). Cada módulo
é testável isolado por um harness próprio (`python -m scriba.qt.<modulo>`), porque
tkinter e Qt não convivem no mesmo processo (dois event loops disputando a main
thread). Só no corte o `main.py` troca o loop do Tk por um QApplication e passa a
importar estas janelas.
"""
