"""Busca por trechos DENTRO da transcrição de UMA reunião (self-ask RAG do chat, #22).

O chat nunca recebe a transcrição inteira: monta-se aqui um FTS5 **:memory: efêmero**
sobre a transcrição fatiada em blocos, a IA decide QUE termos buscar (ver chat_rag) e só
os trechos melhor ranqueados (`bm25()`) entram no prompt da resposta.

Descartável e reconstruível — SEM relação com o `index.db` persistente (meetings_index):
aquele indexa 1 linha por REUNIÃO (busca no dashboard); este é intra-transcrição, vive só
enquanto a janela de chat está aberta. Zero dependência nova (sqlite3 é stdlib).
"""

from __future__ import annotations

import re
import sqlite3

# Turno já vem ≤600 chars do merge.MAX_TURN_CHARS; agrupamos turnos consecutivos até ~isto
# para cada bloco carregar algum contexto (um "Sim, concordo." solto não recupera nada útil).
_CHUNK_CHARS = 800

_K_PER_QUERY = 5   # top trechos por busca da IA
_K_TOTAL = 10      # teto de trechos que vão para a resposta (bounded: relevância, não posição)


def chunk_transcript(transcript_md: str, max_chars: int = _CHUNK_CHARS) -> list[str]:
    """Fatia a transcrição renderizada em blocos para indexar.

    `render_markdown` separa os turnos por linha em branco, cada um `**[HH:MM:SS] Falante:**
    texto`. Agrupamos turnos consecutivos até ~`max_chars`, preservando o carimbo/falante de
    cada um (a IA usa para citar e se orientar cronologicamente).
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", transcript_md or "") if b.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for b in blocks:
        if cur and size + len(b) > max_chars:
            chunks.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(b)
        size += len(b) + 2
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _fts_match(text: str) -> str:
    """Query FTS5 de recall: cada termo vira uma string entre aspas unida por OR (um trecho
    que casa QUALQUER termo é candidato, ranqueado por bm25). Neutraliza a sintaxe do FTS5
    (hífen/aspas/operadores) que a IA possa emitir. "" se não sobrar termo."""
    terms = [t for t in re.split(r"\s+", str(text).strip()) if t]
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


class TranscriptSearcher:
    """FTS5 :memory: efêmero sobre os blocos da transcrição de UMA reunião."""

    def __init__(self, transcript_md: str, max_chars: int = _CHUNK_CHARS):
        self._chunks = chunk_transcript(transcript_md, max_chars)
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
        if self._chunks:
            self._conn.executemany(
                "INSERT INTO t (rowid, body) VALUES (?, ?)",
                [(i + 1, c) for i, c in enumerate(self._chunks)],
            )

    def search(self, queries, k_per_query: int = _K_PER_QUERY, k_total: int = _K_TOTAL) -> list[str]:
        """Roda cada busca da IA, une os resultados por `bm25()` (melhor score por trecho) e
        devolve até `k_total` trechos **em ordem cronológica** (a IA lê a fala na sequência).
        Aceita uma string ou uma lista de buscas. [] se nada casar / transcrição vazia."""
        if not self._chunks:
            return []
        if isinstance(queries, str):
            queries = [queries]
        best: dict[int, float] = {}  # rowid -> melhor (menor) bm25
        for q in queries or []:
            fq = _fts_match(q)
            if not fq:
                continue
            try:
                rows = self._conn.execute(
                    "SELECT rowid, bm25(t) AS s FROM t WHERE t MATCH ? ORDER BY s LIMIT ?",
                    (fq, k_per_query),
                ).fetchall()
            except sqlite3.OperationalError:
                continue  # query degenerada — ignora, não derruba o chat
            for rowid, s in rows:
                if rowid not in best or s < best[rowid]:
                    best[rowid] = s
        if not best:
            return []
        top_ids = [rid for rid, _ in sorted(best.items(), key=lambda kv: kv[1])[:k_total]]
        return [self._chunks[rid - 1] for rid in sorted(top_ids)]  # ordem original = cronológica

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
