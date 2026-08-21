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

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

log = logging.getLogger("scriba.addons")

# Carimbo da ABI de quem instalou os addons. Um `pip --target` produz wheels do
# interpretador que rodou o pip: se o app for reconstruído com outro Python minor
# (3.12 -> 3.14), o numpy/torch de lá passam a SHADOWAR os do bundle e o import
# explode com "_multiarray_umath.cpython-312-darwin.so ... The Python version is
# 3.14" — sintoma: faster_whisper/ctranslate2 param de importar (nada a ver com o
# que o usuário fez). Melhor ignorar o addons incompatível e seguir sem diarização
# (o wizard reinstala) do que quebrar a transcrição, que é o núcleo do app.
_ABI_STAMP = ".scriba-abi"


def addons_dir() -> Path:
    """Pasta dos addons (APP_DIR/addons). Import tardio de util: este módulo roda
    no import do PACOTE (scriba/__init__) e não pode custar nada no caminho não-congelado."""
    from . import util

    return util.APP_DIR / "addons"


def abi_atual() -> str:
    """Tag de ABI do Python em execução ("cpython-314")."""
    return f"cpython-{sys.version_info.major}{sys.version_info.minor}"


def abi_dos_addons(d: Path) -> str | None:
    """ABI dos wheels instalados em `d`, ou None quando não há como saber.

    Prefere o carimbo deixado por `install_to_addons`; sem ele (pasta escrita por
    uma versão antiga do app) sonda o nome de UM .so — wheel binário carrega a tag
    no nome do arquivo (`..._multiarray_umath.cpython-312-darwin.so`). A varredura
    é rasa e para no primeiro achado; wheels abi3/puros não carimbam e caem em None
    (nesses casos não há incompatibilidade de ABI para detectar de todo jeito).
    """
    try:
        marca = (d / _ABI_STAMP).read_text(encoding="utf-8").strip()
        if marca:
            return marca
    except OSError:
        pass
    for padrao in ("*/*.so", "*/*/*.so", "*/*/*/*.so", "*/*.pyd", "*/*/*.pyd"):
        for lib in d.glob(padrao):
            m = re.search(r"(cpython-3\d+)", lib.name)
            if m:
                return m.group(1)
    return None


def bootstrap() -> bool:
    """No bundle congelado, põe APP_DIR/addons no sys.path (se existir, ainda não
    estiver e a ABI casar). Devolve True se adicionou. Fora do bundle: no-op.
    Nunca levanta."""
    try:
        if not getattr(sys, "frozen", False):
            return False
        d = addons_dir()
        if not d.is_dir():
            return False
        abi = abi_dos_addons(d)
        if abi and abi != abi_atual():
            log.warning("addons ignorados: instalados p/ %s, este app é %s — reinstale "
                        "os componentes no wizard (%s)", abi, abi_atual(), d)
            return False
        s = str(d)
        if s not in sys.path:
            # SEMPRE no FIM (#174) — o insert(1) daqui punha o addons ANTES do
            # bundle: o PyInstaller 6 resolve o bundle pelo sys.path (PyiFrozenFinder
            # em sys.path_hooks), NÃO pelo meta_path, e o sys.path do congelado
            # começa em [<_MEIPASS>/base_library.zip, <_MEIPASS>]. Resultado de campo:
            # o numpy do addons shadowava o do app e a transcrição morria com
            # "partially initialized module numpy.fft"; e o typing_extensions de lá
            # (com leitura bloqueada na máquina do usuário) derrubava até os toasts.
            # O contrato sempre foi este: os addons só COMPLETAM o que o bundle não tem.
            sys.path.append(s)
            log.info("addons no fim do sys.path: %s", s)
            return True
        return False
    except Exception:
        log.exception("bootstrap de addons falhou (seguindo sem)")
        return False


# Marcador "instalação de componentes em andamento" (#167): o pip in-process com
# --target --upgrade REESCREVE a pasta addons — um subprocesso de processamento
# importando dali no meio ganha EACCES e a reunião "falha" sem culpa nenhuma
# (caso real: 5 reuniões numa instalação de 71 min). Enquanto o marcador estiver
# ATIVO, o pipeline adia o processamento; a varredura de pendentes readota tudo
# quando a instalação termina.
_INSTALLING_MARKER = ".installing"
_INSTALLING_MAX_AGE_S = 12 * 3600  # trava de segurança p/ PID reciclado


def _installing_marker() -> Path:
    return addons_dir() / _INSTALLING_MARKER


def set_installing(on: bool) -> None:
    """Liga/desliga o marcador (JSON {pid, started}, espelho do .lock de reunião).
    Nunca levanta: o marcador é proteção extra, não pode derrubar a instalação."""
    try:
        if on:
            d = addons_dir()
            d.mkdir(parents=True, exist_ok=True)
            _installing_marker().write_text(
                json.dumps({"pid": os.getpid(), "started": time.time()}),
                encoding="utf-8")
        else:
            _installing_marker().unlink(missing_ok=True)
    except OSError:
        log.debug("marcador .installing falhou", exc_info=True)


def is_installing() -> bool:
    """True se há instalação de componentes EM ANDAMENTO (marcador com PID vivo).

    PID morto = órfão de crash: o marcador é removido na hora (diferente do .lock
    de reunião, aqui o dono é sempre um processo longo — app ou CLI — e a morte
    dele encerra a instalação por definição). Marcador ilegível respeita a idade;
    idade acima da trava de segurança nunca vale (PID reciclado)."""
    m = _installing_marker()
    try:
        if not m.exists():
            return False
        data = json.loads(m.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
        age = time.time() - float(data.get("started", 0))
    except (OSError, ValueError):
        return True  # ilegível (ex.: sendo escrito agora): precaução
    if age > _INSTALLING_MAX_AGE_S:
        set_installing(False)
        return False
    if pid:
        try:
            os.kill(pid, 0)  # sinal 0: só verifica existência
            return True
        except (OSError, PermissionError):
            pass
    log.info("marcador .installing órfão (pid=%s morto) — removendo", pid)
    set_installing(False)
    return False


# Componentes DANIFICADOS (#170): um arquivo do addons que o sistema recusa ler
# (ACL/antivírus barrando .py gravado em AppData) derrubava TODA transcrição com um
# traceback cru de PermissionError, a cada tentativa, sem dizer nada ao usuário.
# Reproduzido em bancada com `icacls /deny`; o gatilho é externo, mas a degradação
# é nossa. Pegadinha: com o .pyc do __pycache__ legível o import PASSA mesmo com o
# .py bloqueado - daí o problema parecer intermitente.
DAMAGED_HINT = ("componentes baixados estão inacessíveis (o sistema recusa ler "
                "arquivos da pasta de componentes - antivírus ou permissão). "
                "Repare em Configurações → Sobre → Reparar componentes.")


def is_damaged_error(exc: BaseException | None) -> bool:
    """True se `exc` (ou alguma causa dela) é falha de ACESSO a arquivo dentro do
    addons. Best-effort e sem levantar: serve para trocar um traceback cru por uma
    mensagem acionável, nunca para decidir algo destrutivo."""
    try:
        alvo = str(addons_dir()).casefold()
    except Exception:
        return False
    vistos = set()
    while exc is not None and id(exc) not in vistos:
        vistos.add(id(exc))
        if isinstance(exc, OSError):
            for cand in (getattr(exc, "filename", None), getattr(exc, "filename2", None)):
                if cand and alvo in str(cand).casefold():
                    return True
            if alvo in str(exc).casefold():
                return True
        exc = exc.__cause__ or exc.__context__
    return False


def looks_damaged_text(text: str) -> bool:
    """Mesma detecção sobre um TEXTO de erro já capturado (tail de process.log,
    meta['error']): o subprocesso morre e só sobra o texto."""
    t = (text or "").casefold()
    return ("permission denied" in t or "errno 13" in t) and "addons" in t


def reset_addons() -> tuple[bool, str]:
    """Apaga a pasta de componentes para reinstalação limpa (o reparo do #170).

    Devolve (ok, mensagem). NÃO apaga durante uma instalação em andamento; falha
    graciosa se o Windows segurar arquivos em uso (.pyd já carregado por este
    processo) - nesse caso o caminho é fechar o app e apagar a pasta."""
    import shutil

    d = addons_dir()
    if is_installing():
        return (False, "há uma instalação de componentes em andamento — espere terminar")
    if not d.exists():
        return (True, "não havia componentes baixados")
    try:
        shutil.rmtree(d)
    except OSError as e:
        log.warning("reparo do addons falhou: %s", e)
        return (False, f"não consegui apagar {d} ({e.strerror or e}) — feche o "
                       "ScribaDev e apague essa pasta manualmente")
    log.info("addons apagado para reinstalação limpa: %s", d)
    return (True, "componentes removidos — baixe de novo para reinstalar")


def pip_log_path() -> Path:
    """logs/pip.log — TODA a saída do pip in-process vai aqui (ver install_to_addons)."""
    from . import util

    return util.LOGS_DIR / "pip.log"


def _pip_log_tail(n: int = 3) -> str:
    """Últimas linhas úteis do pip.log p/ a mensagem de erro (prioriza ERROR/Traceback)."""
    try:
        lines = [l.strip() for l in pip_log_path().read_text(
            encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        return ""
    errs = [l for l in lines if "error" in l.lower()]
    return " | ".join((errs or lines)[-n:])


def install_to_addons(packages: list[str], progress=print) -> tuple[bool, str]:
    """Instala `packages` em APP_DIR/addons com o pip IN-PROCESS (o bundle não tem
    `python -m pip`; o pip precisa estar coletado no bundle — ver installer/windows/
    scribadev.spec). Devolve (ok, mensagem). Usado pelo wizard na instalação
    congelada; na instalação git/venv o wizard usa `sys.executable -m pip` normal.

    Blindagens do caminho congelado (relato de campo: "pip retornou 2" sem NENHUMA
    pista — rc 2 = crash interno do pip, e o traceback ia p/ um stderr que não
    existe num app sem console):
    - stdout/stderr redirecionados p/ logs/pip.log DURANTE o pip: o traceback de um
      crash e toda a saída ficam gravados; a mensagem de erro aponta o arquivo;
    - `--only-binary=:all:`: proibido resolver sdist — o build rodaria
      `sys.executable` (que no bundle é o PRÓPRIO ScribaDev.exe, não um Python);
    - `--no-input` (sem console p/ perguntar) e barra de progresso desligada."""
    import contextlib

    d = addons_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        from pip._internal.cli.main import main as pip_main  # type: ignore
    except Exception:
        return (False, "pip não disponível no bundle — atualize o app (instalador novo) "
                "ou instale via git (CONTRIBUTING.md).")
    progress(f"instalando em {d}: {', '.join(packages)}")
    args = ["install", "--target", str(d), "--upgrade", "--only-binary=:all:",
            "--no-input", "--disable-pip-version-check", "--progress-bar", "off",
            *packages]
    plog = pip_log_path()
    root_handlers = list(logging.getLogger().handlers)
    try:
        plog.parent.mkdir(parents=True, exist_ok=True)
        with open(plog, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n=== pip {' '.join(args)}\n")
            with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
                try:
                    rc = pip_main(args)
                except SystemExit as e:  # pip às vezes sai via SystemExit
                    rc = int(e.code or 0)
    except Exception as e:
        log.exception("pip in-process falhou")
        return (False, f"instalação falhou: {e} — detalhes em {plog}")
    finally:
        # o pip configura o PRÓPRIO logging no root (rich console) e não desfaz:
        # o handler órfão fica preso ao pip.log já fechado e TODO log do app dali
        # em diante viraria "--- Logging error ---: I/O operation on closed file"
        root = logging.getLogger()
        for h in root.handlers[:]:
            if h not in root_handlers:
                root.removeHandler(h)
        # ...e também REMOVE os nossos: 71 min de blackout total do scriba.log no
        # caso real da #167. O que o pip tirou, volta.
        for h in root_handlers:
            if h not in root.handlers:
                root.addHandler(h)
    if rc != 0:
        log.warning("pip retornou %d — saída completa em %s", rc, plog)
        tail = _pip_log_tail()
        return (False, f"pip retornou {rc} ({tail}) — saída completa em {plog}"
                if tail else f"pip retornou {rc} — saída completa em {plog}")
    try:  # carimba a ABI: um app futuro com outro Python minor sabe ignorar isto
        (d / _ABI_STAMP).write_text(abi_atual(), encoding="utf-8")
    except OSError:
        log.debug("não deu para carimbar a ABI do addons", exc_info=True)
    bootstrap()  # entra no sys.path já nesta execução
    return (True, "instalado")
