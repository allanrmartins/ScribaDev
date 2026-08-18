"""Entry do ScribaDevApp.exe (windowed, sem console) no bundle congelado.
Equivale ao gui-script scribadev-tray da instalação via pip (scriba.cli:main_tray)."""
# freeze_support() ANTES de qualquer coisa: o PyInstaller reescreve essa função p/
# desviar os subprocessos auxiliares do multiprocessing (resource_tracker,
# forkserver), que o CPython lança como `sys.executable -c "from
# multiprocessing.resource_tracker import main; ..."`. Sem a chamada, esse `-c`
# cai no argparse do app ("invalid choice") e o auxiliar morre — dependências de
# transcrição usam multiprocessing e o processo passava a vazar/reiniciar.
import multiprocessing
import sys

from scriba.cli import main_tray

multiprocessing.freeze_support()

sys.exit(main_tray())
