"""Novidades por versão: a tela "O que há de novo" que abre depois de um update (#228).

Escrito junto com as release notes, no ritual de release: as notes falam com quem lê o
GitHub; isto aqui fala com quem USA o app - o que mudou, o que dá para fazer a mais e
onde ativar. Cada item diz para qual sistema vale (`so`): quem está no Windows não vê
o motor de fala da Apple.

- destaques: recurso novo importante, em cartão (título + o que faz + onde ativar);
  `abrir` = aba das Configurações que o botão "Experimentar" abre
- melhorias / correcoes: uma linha cada, agrupadas

Mais nova primeiro. A tela junta todas as versões que o usuário ainda não viu.
"""

from __future__ import annotations

import sys

MAC, WIN = "mac", "win"
TODOS = (MAC, WIN)

NOVIDADES: list[dict] = [
    {
        "versao": "1.4.26",
        "data": "20/10/2026",
        "destaques": [
            {
                "titulo": "Transcrição pelo motor de fala da Apple",
                "texto": "O próprio macOS transcreve a reunião, 100% no Mac: numa call de 12 min, "
                         "6 s em vez de 27 s, com o Mac bem mais frio. Opcional: transcrever ao "
                         "vivo, durante a call.",
                "onde": "Transcrição → Motor: Apple",
                "abrir": "Transcrição",
                "so": (MAC,),
            },
            {
                "titulo": "O vocabulário aprende com as suas reuniões",
                "texto": "Os termos técnicos que se repetem nas suas calls viram sugestões; você "
                         "decide quais entram. E a grafia do vocabulário agora é corrigida na "
                         "transcrição (\"S 80\" vira SE80) e respeitada nas notas.",
                "onde": "Transcrição → Termos aprendidos",
                "abrir": "Transcrição",
                "so": TODOS,
            },
        ],
        "melhorias": [
            {"texto": "Assistente de perfil pesquisa na web o jargão da sua área para montar o "
                      "vocabulário inicial", "so": TODOS},
            {"texto": "A pílula fica visível durante a call, no topo da tela", "so": (MAC,)},
            {"texto": "O nome da reunião vem da janela da call do Teams", "so": (MAC,)},
            {"texto": "Notebook na bateria: a nota sai na hora e as vozes são separadas ao "
                      "ligar na tomada", "so": TODOS},
            {"texto": "Iniciar com o sistema abre o app com as permissões certas", "so": (MAC,)},
        ],
        "correcoes": [
            {"texto": "Janelas minimizadas (como Notas) voltam ao clicar para abrir", "so": TODOS},
            {"texto": "Aviso quando a separação de vozes está ligada sem os componentes", "so": TODOS},
            {"texto": "Configurações sem espaços vazios ao maximizar a janela", "so": TODOS},
        ],
    },
]


def _so_atual(platform: str | None = None) -> str:
    return MAC if (platform or sys.platform) == "darwin" else WIN


def _ver(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except (AttributeError, ValueError):
        return (0,)


def para_o_so(entrada: dict, platform: str | None = None) -> dict:
    """A versão só com os itens deste sistema (seções vazias somem)."""
    so = _so_atual(platform)
    out = {k: v for k, v in entrada.items() if k not in ("destaques", "melhorias", "correcoes")}
    for secao in ("destaques", "melhorias", "correcoes"):
        out[secao] = [i for i in entrada.get(secao, []) if so in i.get("so", TODOS)]
    return out


def pendentes(versao_atual: str, ultima_vista: str | None, platform: str | None = None) -> list[dict]:
    """Versões a mostrar: as maiores que a última vista e até a atual, filtradas pelo
    sistema, sem as que ficaram vazias. `ultima_vista` None (o app atualizou de uma
    versão sem esta tela) = todas as que existem até a atual."""
    base = _ver(ultima_vista) if ultima_vista else (0,)
    out = []
    for entrada in NOVIDADES:
        v = _ver(entrada["versao"])
        if base < v <= _ver(versao_atual):
            filtrada = para_o_so(entrada, platform)
            if any(filtrada[s] for s in ("destaques", "melhorias", "correcoes")):
                out.append(filtrada)
    return out
