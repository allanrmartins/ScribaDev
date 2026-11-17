#!/bin/bash
# Mede o custo de energia (CPU, GPU e Neural Engine) da transcrição ao vivo do motor
# da Apple, em SILÊNCIO (nada toca no alto-falante), com o powermetrics.
#
# Fases (cada uma com o powermetrics amostrando a cada 1 s):
#   1. repouso  - 60 s sem nada, a base para descontar
#   2. hoje     - ao vivo só com trechos fixados (como na 1.4.28)
#   3. novo     - ao vivo com a frase em andamento + baixa latência (.fastResults)
#   4. arquivo  - a retranscrição do arquivo no modo preciso (o que a nota passa a usar)
#
# Pré-requisitos em .medicao/ (ignorada pelo git): bin/apple-stt-hoje, bin/apple-stt-fast
# e audio.raw (PCM 16-bit mono 48 kHz de uma gravação local). Rodar FORA de call (a
# transcrição ao vivo da call também usa o Neural Engine e mistura os números) e com o
# Mac na tomada. Pede a senha do sudo uma vez (só o powermetrics roda como root).
#
# Uso:  bash tools/mede_energia_ao_vivo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
M=.medicao
PY="$HOME/Library/Application Support/ScribaDev/venv/bin/python"
OUT="$M/resultado-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
for f in "$M/bin/apple-stt-hoje" "$M/bin/apple-stt-fast" "$M/audio.raw"; do
    [ -e "$f" ] || { echo "falta $f (veja o cabeçalho deste script)"; exit 1; }
done
AUDIO_S=$(( $(stat -f %z "$M/audio.raw") / 96000 ))
echo "áudio de teste: ${AUDIO_S} s. Duração total: uns $(( (AUDIO_S * 2 + 60) / 60 )) min."
sudo -v
# mantém o sudo vivo enquanto o script roda
while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done 2>/dev/null &
KEEP=$!
trap 'kill $KEEP 2>/dev/null || true; sudo pkill -f "powermetrics.*$OUT" 2>/dev/null || true' EXIT

fase() {   # fase <nome> <comando...>
    local nome=$1; shift
    echo "== $nome"
    sudo powermetrics -s cpu_power -i 1000 -o "$OUT/$nome.pm" 2>/dev/null &
    sleep 2
    local t0; t0=$(date +%s)
    "$@" > "$OUT/$nome.log" 2>&1
    local t1; t1=$(date +%s)
    sleep 1
    sudo pkill -f "powermetrics.*$OUT/$nome.pm" || true
    echo "$nome $((t1 - t0))" >> "$OUT/duracoes.txt"
    # só as linhas de medida: o log do modo arquivo traz o TEXTO transcrito (fala de reunião)
    grep -E "^(modo=|fixado:|provisorio:)" "$OUT/$nome.log" | sed 's/^/   /' || true
}

fase repouso sleep 60
fase hoje "$PY" tools/simula_ao_vivo.py "$M/bin/apple-stt-hoje" "$M/audio.raw" "$OUT/sim-hoje" so-fixado
fase novo "$PY" tools/simula_ao_vivo.py "$M/bin/apple-stt-fast" "$M/audio.raw" "$OUT/sim-novo" provisorio
# a nota no modo preciso: o helper transcreve o WAV inteiro de uma vez
"$PY" - "$M/audio.raw" "$OUT/arquivo.wav" <<'EOF'
import struct, sys
raw = open(sys.argv[1], "rb").read()
hdr = (b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVEfmt "
       + struct.pack("<IHHIIHH", 16, 1, 1, 48000, 96000, 2, 16) + b"data" + struct.pack("<I", len(raw)))
open(sys.argv[2], "wb").write(hdr + raw)
EOF
fase arquivo "$M/bin/apple-stt-hoje" file pt "$OUT/arquivo.wav"

"$PY" - "$OUT" "$AUDIO_S" <<'EOF'
import re, sys
from pathlib import Path
out, audio_s = Path(sys.argv[1]), int(sys.argv[2])
BATERIA_WH = 52.6   # MacBook Air 13" Apple Silicon (aprox.): só para dar escala
def amostras(nome):
    txt = (out / f"{nome}.pm").read_text(errors="replace")
    return {k: [int(x) for x in re.findall(rf"^{k} Power[^:]*:\s*(\d+)\s*mW", txt, re.M)]
            for k in ("CPU", "GPU", "ANE", "Combined")}
import statistics as st
# a CPU do Mac oscila muito (picos de outros programas, desvio de ~1,5 W): MEDIANA, não
# média - com média o repouso saiu mais alto que as fases e o "extra" ficou negativo
rep = amostras("repouso")
base = {k: (st.median(v) if v else 0.0) for k, v in rep.items()}
print(f"\nrepouso (mediana): CPU {base['CPU']:.0f} mW · GPU {base['GPU']:.0f} mW · ANE {base['ANE']:.0f} mW · total {base['Combined']:.0f} mW")
for nome, rotulo in (("hoje", "ao vivo como hoje"), ("novo", "ao vivo novo (fastResults)")):
    am = amostras(nome)
    extra = {k: (st.median(v) - base[k]) if v else 0.0 for k, v in am.items()}
    ane = st.mean(am["ANE"]) if am["ANE"] else 0.0
    print(f"{rotulo:28s} acima do repouso (mediana): CPU {extra['CPU']:+.0f} mW · total {extra['Combined']:+.0f} mW"
          f" · ANE média {ane:.1f} mW (pico {max(am['ANE'] or [0])}) -> por hora de call:"
          f" {extra['Combined'] / (BATERIA_WH * 10):+.2f}% da bateria")
# a nota no modo preciso é um pico curto: aí vale a energia somada da janela
am = amostras("arquivo")
extra = sum(x - base["Combined"] for x in am["Combined"]) / 3600   # mWh
por_hora = extra * 3600 / audio_s
print(f"{'nota no modo preciso':28s} energia extra {extra:+.2f} mWh para {audio_s} s de áudio"
      f" (pico ANE {max(am['ANE'] or [0])} mW) -> por hora de call: {por_hora:+.0f} mWh = {por_hora / (BATERIA_WH * 10):+.2f}% da bateria")
print(f"\ndetalhes em {out}")
EOF
