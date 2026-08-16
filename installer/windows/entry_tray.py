"""Entry do ScribaDevApp.exe (windowed, sem console) no bundle congelado.
Equivale ao gui-script scribadev-tray da instalação via pip (scriba.cli:main_tray)."""
import sys

from scriba.cli import main_tray

sys.exit(main_tray())
