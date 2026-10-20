"""Correção dos termos do vocabulário na transcrição (hotwords do usuário).

Motores sem suporte a vocabulário (o da Apple ignora o contexto em pt-BR, testado
em 2026-10-20) escrevem o jargão errado: "S 80" por SE80, "enancement" por
enhancement, "smartformes" por SmartForms. Aqui comparamos o texto com o
vocabulário por semelhança de escrita e trocamos pela grafia certa - antes de
gravar o transcript.json, então nota, busca e consultas futuras saem certas.

Conservador por desenho (trocar palavra boa por termo errado é pior que não
corrigir):
- janelas de 1 a 3 palavras, comparadas emendadas ("S 80" -> "s80", "smart form"
  -> "smartform"); a mais longa que casa vence, e só troca se casar com um termo
  pelas regras abaixo;
- termo curto (<= 4) sem dígito: só igualdade exata (acerta caixa: "abap" -> ABAP);
- termo com dígito (transações, SE80): dígitos idênticos e no máximo 1 letra de
  diferença;
- termo longo: semelhança alta, mesma inicial, e nunca uma flexão do próprio
  termo ("transportes" não vira "transporte");
- palavra já igual ao termo (ignorando caixa) fica como está.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

_MAX_JUNTAR = 3        # janelas de até 3 tokens
_SIMILAR = 0.88        # termos longos: razão mínima do difflib


def parse_terms(hotwords: str) -> list[str]:
    """Termos do vocabulário. Vírgula/ponto e vírgula/linha separam frases ("user
    exit, tabela Z"); sem nenhum desses, cada palavra é um termo (o formato comum:
    "SAP ABAP BAPI SE80 ...")."""
    text = (hotwords or "").strip()
    if not text:
        return []
    if re.search(r"[,;\n]", text):
        parts = re.split(r"[,;\n]+", text)
    else:
        parts = text.split()
    seen: dict[str, str] = {}
    for p in (x.strip() for x in parts):
        # termo de 1 caractere ("Z" de "tabela Z" separado por espaço) casaria qualquer letra solta
        if len(_norm(p)) >= 2 and _norm(p) not in seen:
            seen[_norm(p)] = p
    return list(seen.values())


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _letras_diferentes(a: str, b: str) -> int:
    """Distância de edição (Levenshtein) - termos curtos, custo desprezível."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _tecnico(termo: str) -> bool:
    """Sigla/código/nome próprio (tem maiúscula, dígito ou _) x palavra comum do
    vocabulário ("tabela", "transporte"), que pede muito mais cautela."""
    return any(c.isupper() or c.isdigit() or c == "_" for c in termo)


def _casa(cand: str, termo: str, juntou: bool) -> bool:
    """`cand` (normalizado; `juntou` = veio de 2+ palavras emendadas) é uma grafia
    errada de `termo`? Regras calibradas em 142 mil palavras de reuniões reais."""
    alvo = _norm(termo)
    if cand == alvo:
        return True
    if not cand or cand[0] != alvo[0]:
        return False   # "de 11" não é SE11, "na 1" não é KNA1, "ZSM30" não é SM30
    if any(c.isdigit() for c in alvo):
        digitos = lambda x: re.sub(r"\D", "", x)   # noqa: E731
        return digitos(cand) == digitos(alvo) and _letras_diferentes(cand, alvo) <= 1
    if juntou or len(alvo) <= 4:
        return False   # "o dia tá" não é OData; sigla curta só exata
    if not _tecnico(termo) and len(alvo) < 8:
        return False   # "capô" não é campo, "tá ela" não é tabela
    if cand.startswith(alvo) or alvo.startswith(cand):
        return False   # pedaço do termo ou plural ("SmartForm", "transportes")
    if not _tecnico(termo) and cand[:len(alvo) - 2] == alvo[:len(alvo) - 2]:
        return False   # flexão de palavra comum ("transporta" não vira transporte)
    return difflib.SequenceMatcher(None, cand, alvo).ratio() >= _SIMILAR


@dataclass
class Correcao:
    original: str
    termo: str


_TOKEN = re.compile(r"\S+")
_BORDA = re.compile(r"^(\W*)(.*?)(\W*)$", re.S)


def _sobra_exata(janela, alvos) -> bool:
    """A janela (a anterior sem a 1ª ou sem a última palavra) casa EXATAMENTE com algum termo?"""
    cand = _norm("".join(_BORDA.match(t.group()).group(2) for t in janela))
    return any(cand == a for _, a in alvos)


# Só p/ quem usa SAP (vocabulário com SAP/ABAP - ver `usa_sap`):
# "Z" isolado no SAP = objeto customizado (tabela Z, programa Z, tela Z). Os motores já
# escrevem "Z" (medido: Whisper e Apple), mas "z" minúsculo solto e "zê"/"zé" logo
# depois de um objeto também aparecem. "Zé" sozinho é nome de gente: só nesse contexto.
_OBJETOS_SAP = (r"tabelas?|programas?|programinhas?|telas?|transaç(?:ão|ões)|campos?|"
                r"classes?|funç(?:ão|ões)|módulos?|reports?|relatórios?|objetos?|includes?|"
                r"desenvolvimentos?|workflows?|autorizaç(?:ão|ões)|views?|estruturas?|"
                r"elementos?|domínios?|bapis?|badis?|enhancements?|exits?|fórmulas?")
_Z_DEPOIS_DE_OBJETO = re.compile(rf"(?i)\b({_OBJETOS_SAP})\s+(?:zê|zé|zeta|z)(?![\w-])")
_Z_SOLTO = re.compile(r"(?<![\w-])z(?![\w-])")


def usa_sap(termos: list[str]) -> bool:
    """O vocabulário é de quem trabalha com SAP? A regra do "Z" só faz sentido aí: nem
    todo usuário é de SAP, e para os outros um "z" solto é só uma letra."""
    return any(_norm(t) in ("sap", "abap") for t in termos)


def _corrigir_z(texto: str) -> tuple[str, list[Correcao]]:
    feitas: list[Correcao] = []

    def troca(m: re.Match) -> str:
        novo = f"{m.group(1)} Z"
        if m.group(0) != novo:
            feitas.append(Correcao(m.group(0), novo))
        return novo

    texto = _Z_DEPOIS_DE_OBJETO.sub(troca, texto)
    n = len(_Z_SOLTO.findall(texto))
    if n:
        feitas.extend(Correcao("z", "Z") for _ in range(n))
        texto = _Z_SOLTO.sub("Z", texto)
    return texto, feitas


def corrigir(texto: str, termos: list[str], sap: bool | None = None) -> tuple[str, list[Correcao]]:
    """Texto com os termos corrigidos + o que foi trocado (p/ o log da reunião).
    `sap`: o perfil do usuário é de SAP (liga a regra do "Z"); None = deduz pelo
    vocabulário (quem nunca rodou o assistente de perfil)."""
    if not texto:
        return texto, []
    if sap is None:
        sap = usa_sap(termos)
    texto, feitas_z = _corrigir_z(texto) if sap else (texto, [])
    if not termos:
        return texto, feitas_z
    alvos = {_norm(t): t for t in termos if _norm(t)}
    tokens = list(_TOKEN.finditer(texto))
    miolos = [_norm(_BORDA.match(t.group()).group(2)) for t in tokens]

    def exato(a: int, b: int) -> bool:
        return "".join(miolos[a:b]) in alvos

    out: list[str] = []
    feitas: list[Correcao] = []
    pos = 0
    i = 0
    while i < len(tokens):
        achou = None
        for n in range(min(_MAX_JUNTAR, len(tokens) - i), 0, -1):
            cand = "".join(miolos[i:i + n])
            if n > 1 and not exato(i, i + n) and any(
                    exato(a, b) for a in range(i, i + n) for b in range(a + 1, i + n + 1) if b - a < n):
                continue   # "é o enhancement": o pedaço exato vence, "é o" fica
            termo = next((t for a, t in alvos.items() if _casa(cand, t, juntou=n > 1)), None)
            if termo is not None:
                achou = (n, termo)
                break
        if achou is None:
            i += 1
            continue
        n, termo = achou
        janela = tokens[i:i + n]
        original = texto[janela[0].start():janela[-1].end()]
        pre = _BORDA.match(janela[0].group()).group(1)
        suf = _BORDA.match(janela[-1].group()).group(3)
        miolo = original[len(pre):len(original) - len(suf)] if suf else original[len(pre):]
        # palavra comum só se corrige quando a grafia muda - caixa ("CAMPO") é do falante
        if miolo != termo and (_tecnico(termo) or _norm(miolo) != _norm(termo)):
            out.append(texto[pos:janela[0].start()])
            out.append(f"{pre}{termo}{suf}")
            pos = janela[-1].end()
            feitas.append(Correcao(original, termo))
        i += n
    out.append(texto[pos:])
    return "".join(out), feitas_z + feitas
