"""Entry do binário `scribadev` (CLI) dentro do ScribaDev.app congelado.
Uso: ScribaDev.app/Contents/MacOS/scribadev <comando>."""
import sys

from scriba.cli import main

sys.exit(main())
