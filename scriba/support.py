"""Reportar erro como issue no GitHub — zip de diagnóstico + página pré-preenchida.

Fluxo do botão "Reportar erro no GitHub" (erro de download de componentes, crash):
gera o zip de diagnóstico (diagnostics.export_zip), SELECIONA o arquivo no
gerenciador (util.reveal_path) e abre a página de nova issue com título/corpo
prontos. O GitHub NÃO aceita anexo por URL — por isso o corpo pede o arraste do
zip, que já está selecionado a um alt-tab de distância.

Privacidade: o zip e o "detalhe do erro" do corpo passam pelo Scrubber do
diagnostics (nomes de cliente/pessoa/reunião viram tokens, usuário do SO vira
[usuario]); o corpo ainda lembra o usuário de revisar antes de anexar. Tudo
aqui é best-effort: sem zip, a issue abre mesmo assim (só descrever o cenário
já ajuda) - mas detalhe que falhou na ofuscação é OMITIDO, nunca sai cru.
"""

from __future__ import annotations

import logging
import platform
import webbrowser

from . import __version__, updates, util

log = logging.getLogger("scriba.support")

REPO_ISSUES_NEW = "https://github.com/allanrmartins/ScribaDev/issues/new"


def issue_url(title: str, body: str) -> str:
    """URL de nova issue com título e corpo pré-preenchidos (query string)."""
    from urllib.parse import urlencode

    return REPO_ISSUES_NEW + "?" + urlencode({"title": title, "body": body})


def build_report(context: str, detail: str, recordings_dir=None):
    """(zip_path | None, url da issue). O zip é best-effort — falhou, a issue sai
    sem ele; `detail` (ex.: a nota de erro do pip) entra truncado no corpo."""
    from . import diagnostics

    zip_path = None
    try:
        zip_path = diagnostics.export_zip(recordings_dir)
    except Exception:
        log.exception("reportar erro: export do diagnóstico falhou (issue sai sem zip)")
    try:
        # mesmo tratamento do zip: nomes ofuscados; falhou = OMITIR, nunca sair cru
        detail = diagnostics.build_scrubber(recordings_dir).scrub(detail or "")
    except Exception:
        log.exception("reportar erro: ofuscação do detalhe falhou (detalhe omitido)")
        detail = "(detalhe omitido — falha na ofuscação; o zip de diagnóstico cobre)"
    origem = "instalador" if updates.is_frozen_install() else "git/fonte"
    anexo = (f"⬇️ **Arraste aqui o arquivo `{zip_path.name}`** — deixei ele selecionado no seu "
             "gerenciador de arquivos. Nomes de clientes, pessoas e reuniões já saem "
             "ofuscados (`[cliente-1]`, `[pessoa-2]`…), mas vale a olhada final antes "
             "de anexar."
             if zip_path else
             "(o zip de diagnóstico não pôde ser gerado — descreva o cenário, por favor)")
    body = (f"**O que aconteceu**\n{context}\n\n"
            f"**Detalhe do erro**\n```\n{(detail or '(sem detalhe)').strip()[:900]}\n```\n\n"
            f"**Ambiente**\n- ScribaDev v{__version__} ({origem})\n- {platform.platform()}\n\n"
            f"**Diagnóstico**\n{anexo}\n")
    return zip_path, issue_url(f"[erro] {context}"[:120], body)


def open_report(context: str, detail: str, recordings_dir=None):
    """Ação do botão: zip + seleção no gerenciador + navegador na issue.
    Devolve o caminho do zip (ou None) p/ a UI informar onde ficou."""
    zip_path, url = build_report(context, detail, recordings_dir)
    if zip_path is not None:
        try:
            util.reveal_path(zip_path)
        except Exception:
            log.debug("reveal do zip falhou", exc_info=True)
    webbrowser.open(url)
    return zip_path
