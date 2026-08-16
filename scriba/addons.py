"""Addons da instalação CONGELADA (#147/#142): deps pesadas fora do bundle.

O instalador (#142) é enxuto de propósito — torch/pyannote/nvidia-* não vêm no
setup.exe. Quando o usuário as pede no wizard de 1º uso, elas são instaladas em
`APP_DIR/addons` (pip --target, rodado in-process — o bundle não tem `python -m
pip`), e este módulo as coloca no sys.path NO BOOT, antes de qualquer import
lazy (`scriba.diarize` importa pyannote só na hora de usar).

Instalação git/venv nunca passa por aqui: `bootstrap()` é no-op fora do bundle
congelado (lá o pip normal instala os extras `[cuda]`/`[diarization]` na venv).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("scriba.addons")


def addons_dir() -> Path:
    """Pasta dos addons (APP_DIR/addons). Import tardio de util: este módulo roda
    no import do PACOTE (scriba/__init__) e não pode custar nada no caminho não-congelado."""
    from . import util

    return util.APP_DIR / "addons"


def bootstrap() -> bool:
    """No bundle congelado, põe APP_DIR/addons no sys.path (se existir e ainda não
    estiver). Devolve True se adicionou. Fora do bundle: no-op. Nunca levanta."""
    try:
        if not getattr(sys, "frozen", False):
            return False
        d = addons_dir()
        if not d.is_dir():
            return False
        s = str(d)
        if s not in sys.path:
            # depois do _internal do bundle (índice 1, não 0): o código do APP vem
            # do bundle; os addons só COMPLETAM com o que o bundle não tem
            sys.path.insert(1, s)
            log.info("addons no sys.path: %s", s)
            return True
        return False
    except Exception:
        log.exception("bootstrap de addons falhou (seguindo sem)")
        return False


def install_to_addons(packages: list[str], progress=print) -> tuple[bool, str]:
    """Instala `packages` em APP_DIR/addons com o pip IN-PROCESS (o bundle não tem
    `python -m pip`; o pip precisa estar coletado no bundle — ver installer/windows/
    scribadev.spec). Devolve (ok, mensagem). Usado pelo wizard na instalação
    congelada; na instalação git/venv o wizard usa `sys.executable -m pip` normal."""
    d = addons_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        from pip._internal.cli.main import main as pip_main  # type: ignore
    except Exception:
        return (False, "pip não disponível no bundle — atualize o app (instalador novo) "
                "ou instale via git (CONTRIBUTING.md).")
    progress(f"instalando em {d}: {', '.join(packages)}")
    try:
        rc = pip_main(["install", "--target", str(d), "--upgrade", *packages])
    except SystemExit as e:  # pip às vezes sai via SystemExit
        rc = int(e.code or 0)
    except Exception as e:
        log.exception("pip in-process falhou")
        return (False, f"instalação falhou: {e}")
    if rc != 0:
        return (False, f"pip retornou {rc} — veja o log")
    bootstrap()  # entra no sys.path já nesta execução
    return (True, "instalado")
