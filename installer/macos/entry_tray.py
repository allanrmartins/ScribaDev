"""Entry do executável principal do ScribaDev.app (menu bar/janelas, sem console).
Equivale ao gui-script scribadev-tray da instalação via pip (scriba.cli:main_tray)."""
import sys

from scriba.cli import main_tray

sys.exit(main_tray())
