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

from dataclasses import dataclass
from pathlib import Path

from .stt_apple import LIVE_SUFFIX, LiveSession, _parse

VOCE, OUTROS = "mic", "loopback"


@dataclass(frozen=True)
class LiveLine:
    stream: str    # "mic" (você) | "loopback" (os outros)
    start: float   # segundos desde o início da gravação
    text: str


class LiveTail:
    """Leitor incremental dos `.live.jsonl` de UMA pasta de gravação."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
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
            novos += [LiveLine(stream, s.start, s.text.strip()) for s in segs if s.text.strip()]
        if not novos:
            return False
        self.lines = sorted(self.lines + novos, key=lambda ln: ln.start)
        return True
