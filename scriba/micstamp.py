"""Carimbos start/stop sintetizados a partir de "o mic está aberto agora?".

O detector consome `(chave, start, stop)` no formato do ConsentStore do Windows:
FILETIME (100 ns desde 1601), `stop == 0` enquanto o app está com o mic aberto e
`start` reescrito toda vez que o app REABRE o mic (fronteira entre calls, #34).

Fontes que só dizem o estado atual (CoreAudio no macOS, sessões WASAPI no
Windows) não guardam histórico; esta classe o sintetiza observando as transições
entre polls. Puro Python, sem nada de SO: testável em qualquer plataforma.
"""

from __future__ import annotations

import time

# segundos entre 1601-01-01 (época do FILETIME) e 1970-01-01 (época do Unix)
_FILETIME_EPOCH_DELTA = 11_644_473_600


def now_filetime() -> int:
    return int((time.time() + _FILETIME_EPOCH_DELTA) * 1e7)


class MicStamps:
    """Histórico sintetizado: chave -> [start_filetime, stop_filetime (0 = mic aberto)]."""

    def __init__(self) -> None:
        self.state: dict[str, list[int]] = {}

    def reset(self) -> None:
        self.state.clear()

    def update(self, aberto_por_chave: dict[str, bool]) -> list[tuple[str, int, int]]:
        """Aplica a observação deste poll e devolve (chave, start, stop) de todo app
        com histórico. Chave ausente da observação = processo sumiu: se estava com o
        mic aberto (crash/quit), a sessão é fechada agora."""
        now = now_filetime()
        for key, aberto in aberto_por_chave.items():
            st = self.state.get(key)
            if aberto:
                if st is None or st[1] != 0:  # (re)abriu o mic: novo start, stop zerado
                    self.state[key] = [now, 0]
            elif st is not None and st[1] == 0:  # fechou o mic agora
                st[1] = now
        for key, st in self.state.items():
            if key not in aberto_por_chave and st[1] == 0:
                st[1] = now
        return [(k, s[0], s[1]) for k, s in self.state.items()]
