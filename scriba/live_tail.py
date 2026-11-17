"""Acompanha a transcrição ao vivo de uma gravação para mostrar na capa.

O helper da Apple (stt_apple.LiveSession) escreve um `<stream>.live.jsonl` por
stream (mic = você, loopback = os outros) enquanto a call acontece; cada linha é um
trecho já finalizado. Aqui só LEMOS esses arquivos, a partir de onde paramos da
última vez, sem nunca tocar no helper nem no gravador.

- a última linha pode estar pela metade (o helper ainda escrevendo): só consome até
  a última quebra de linha e deixa o resto para a próxima leitura;
- o arquivo pode ainda não existir (o helper compila na 1ª vez) ou ser recriado:
  ausente = nada novo; menor que o já lido = recomeça do zero;
- mic e loopback chegam fora de ordem: a lista sai ordenada pelo início do trecho.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .stt_apple import LIVE_SUFFIX, LiveSession, _parse

VOCE, OUTROS = "mic", "loopback"


Pedacos = list  # [(texto, original | None)] - ver vocab_fix.corrigir_trechos


@dataclass(frozen=True)
class LiveLine:
    stream: str    # "mic" (você) | "loopback" (os outros)
    start: float   # segundos desde o início da gravação
    text: str
    # texto já com o vocabulário, em pedaços (original != None = palavra trocada)
    pieces: tuple = field(default=(), compare=False)
    chegou: float = field(default=0.0, compare=False)   # time.monotonic() da leitura

    @property
    def corrigido(self) -> bool:
        return any(orig is not None for _t, orig in self.pieces)


class LiveTail:
    """Leitor incremental dos `.live.jsonl` de UMA pasta de gravação."""

    def __init__(self, folder: Path, corretor: Callable[[str], Pedacos] | None = None):
        self.folder = Path(folder)
        self._corretor = corretor   # vocabulário: texto -> pedaços (None = sem correção)
        self._pos = {s: 0 for s in LiveSession.STREAMS}
        self._rest = {s: b"" for s in LiveSession.STREAMS}
        self.lines: list[LiveLine] = []

    def poll(self) -> bool:
        """Lê o que chegou desde a última vez. True se apareceu trecho novo."""
        novos: list[LiveLine] = []
        for stream in LiveSession.STREAMS:
            path = self.folder / f"{stream}{LIVE_SUFFIX}"
            try:
                size = path.stat().st_size
                if size < self._pos[stream]:   # recriado: recomeça
                    self._pos[stream], self._rest[stream] = 0, b""
                    self.lines = [ln for ln in self.lines if ln.stream != stream]
                if size == self._pos[stream]:
                    continue
                with open(path, "rb") as f:
                    f.seek(self._pos[stream])
                    chunk = f.read(size - self._pos[stream])
            except OSError:
                continue
            self._pos[stream] += len(chunk)
            data = self._rest[stream] + chunk
            corte = data.rfind(b"\n")
            if corte < 0:
                self._rest[stream] = data
                continue
            completas, self._rest[stream] = data[:corte], data[corte + 1:]
            segs, _done = _parse(completas.decode("utf-8", errors="replace").splitlines())
            agora = time.monotonic()
            novos += [LiveLine(stream, s.start, s.text.strip(), self._pedacos(s.text.strip()), agora)
                      for s in segs if s.text.strip()]
        if not novos:
            return False
        self.lines = sorted(self.lines + novos, key=lambda ln: ln.start)
        return True

    def _pedacos(self, texto: str) -> tuple:
        if self._corretor is not None:
            try:
                return tuple(self._corretor(texto))
            except Exception:
                pass   # correção é enfeite: falhou, mostra o texto como veio
        return ((texto, None),)


def corretor_do_vocabulario(hotwords: str) -> Callable[[str], Pedacos] | None:
    """O mesmo vocabulário do processamento (digitado + aprendidos aceitos + a regra do
    "Z" para perfil SAP), pronto para a tela. None se não houver termo nenhum."""
    from . import promptgen, vocab_fix, vocab_learn

    termos = vocab_learn.all_terms(hotwords or "")
    try:
        perfil = promptgen.load_profile()
        sap = promptgen.is_sap(perfil) if perfil is not None else None
    except Exception:
        sap = None
    if not termos and not sap:
        return None
    return lambda texto: vocab_fix.corrigir_trechos(texto, termos, sap=sap)
