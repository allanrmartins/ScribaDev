# -*- coding: utf-8 -*-
"""Simula a transcrição ao vivo do motor da Apple EM SILÊNCIO (nada toca no alto-falante).

Regrava um áudio PCM 16-bit mono 48 kHz aos poucos, em tempo real, num WAV, roda o
helper no modo `live` e mede: atraso do trecho fixado depois do fim da frase, ritmo e
atraso do texto provisório e o uso do processo. Serve para comparar opções do
SpeechTranscriber (ex.: com e sem .fastResults) sem precisar de call real.

Preparar o áudio (trecho de uma gravação local):
    ffmpeg -ss 30 -t 120 -i loopback.opus -ac 1 -ar 48000 -f s16le audio.raw
Uso:
    python tools/simula_ao_vivo.py <helper> audio.raw <pasta-saida> provisorio|so-fixado

O texto transcrito fica só na pasta de saída: não publique (é fala de reunião real).
"""
import json, os, struct, subprocess, sys, threading, time
from pathlib import Path
helper, raw, outdir, modo = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
outdir.mkdir(parents=True, exist_ok=True)
wav = outdir / "loopback.wav"
RATE, CH = 48000, 1
hdr = b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, CH, RATE, RATE * CH * 2, CH * 2, 16) + b"data" + struct.pack("<I", 0xFFFFFFFF)
wav.write_bytes(hdr)
dados = raw.read_bytes()
args = [helper, "live", "pt", str(wav)]
pass_fds = ()
r = w = None
if modo == "provisorio":
    r, w = os.pipe(); args.append(str(w)); pass_fds = (w,)
out = open(outdir / "finais.jsonl", "w")
p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=out, stderr=open(outdir / "err.log", "w"), pass_fds=pass_fds)
if w is not None: os.close(w)
t0 = None
parciais = []
def le():
    with os.fdopen(r, "rb") as f:
        for linha in f:
            parciais.append((time.monotonic(), json.loads(linha)))
if r is not None: threading.Thread(target=le, daemon=True).start()
chegadas = []   # (instante, trecho fixado)
parar_obs = threading.Event()
def observa():
    n = 0
    while not parar_obs.is_set():
        linhas = [l for l in open(outdir / "finais.jsonl").read().splitlines() if l.strip()]
        for l in linhas[n:]:
            o = json.loads(l)
            if "text" in o: chegadas.append((time.monotonic(), o))
        n = len(linhas); time.sleep(0.1)
threading.Thread(target=observa, daemon=True).start()
# grava em tempo real, 0,25 s por vez
passo = RATE * CH * 2 // 4
t0 = time.monotonic()
with open(wav, "ab") as fw:
    for i in range(0, len(dados), passo):
        fw.write(dados[i:i + passo]); fw.flush()
        alvo = t0 + (i + passo) / (RATE * CH * 2)
        time.sleep(max(0, alvo - time.monotonic()))
fim_audio = time.monotonic()
p.stdin.close()
_, status, ru = os.wait4(p.pid, 0)
fim = time.monotonic()
time.sleep(0.3); parar_obs.set()
out.close()
finais = [json.loads(l) for l in open(outdir / "finais.jsonl") if l.strip()]
finais = [f for f in finais if "text" in f]
cpu = ru.ru_utime + ru.ru_stime
print(f"modo={modo} audio={len(dados)/(RATE*2):.0f}s cpu={cpu:.1f}s ({100*cpu/(fim-t0):.0f}% de um núcleo) rss_max={ru.ru_maxrss/1e6:.0f}MB fim_apos_parar={fim-fim_audio:.1f}s trechos={len(finais)}")
json.dump({"t0": t0, "parciais": [(t - t0, o) for t, o in parciais]}, open(outdir / "parciais.json", "w"))
if chegadas:
    import statistics
    lat = [t - t0 - o["end"] for t, o in chegadas if t <= fim_audio]
    if lat: print(f"fixado: chega {statistics.median(lat):.1f}s (mediana) depois do fim da frase; máx {max(lat):.1f}s")
if parciais:
    import statistics
    # atraso do provisório: momento em que chega x posição do áudio onde o trecho começou
    atrasos = []
    vistos = set()
    for t, o in parciais:
        if o.get("text") and o["start"] not in vistos:
            vistos.add(o["start"]); atrasos.append(t - t0 - o["start"])
    print(f"provisorio: {len(parciais)} atualizações ({len(parciais)/(fim_audio-t0):.1f}/s); "
          f"1º texto de cada trecho chega {statistics.median(atrasos):.1f}s (mediana) depois do início da fala")
