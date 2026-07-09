# Port Linux/macOS — Marco 1: rodar e transcrever no Linux (épico #104)

**Status:** em andamento · **Criado:** 2026-07-09

O Marco 1 entrega: o app instala, importa e **transcreve um arquivo de áudio** no Linux (CPU).
Features de SO (captura ao vivo, detecção de call, toasts, hotkey, autostart) degradam com graça — a reimplementação nativa vem em marcos futuros.

## Setup num Linux (ex.: Cowork, headless)

```bash
# 1. dependências do sistema
sudo apt install ffmpeg          # compressão/decodificação de áudio

# 2. o app (os markers do pyproject deixam pyaudiowpatch/windows-toasts de fora no POSIX)
pip install -e .

# 3. modelo pequeno para o teste (o default large-v3-turbo baixa ~1,6 GB;
#    o tiny baixa ~75 MB e resolve o critério de aceite)
mkdir -p ~/.local/share/ScribaDev
cat > ~/.local/share/ScribaDev/config.toml <<'EOF'
[whisper]
model = "tiny"
EOF
```

Requisitos de rede: acesso ao HuggingFace Hub (download do modelo na primeira transcrição).
Para smoke de GUI sem display: `QT_QPA_PLATFORM=offscreen`.

## Critério de aceite do Marco 1

```bash
# import da cadeia completa
python -c "import scriba.main"

# diagnóstico: exit 0, CPU + ffmpeg OK, itens Windows-only como não-aplicáveis
scribadev doctor

# transcrição E2E com a fixture versionada (copiar antes: o transcribe escreve na pasta)
cp -r tests/fixtures/reuniao_exemplo /tmp/fixture-marco1
scribadev transcribe /tmp/fixture-marco1
grep -i "teste de grava" /tmp/fixture-marco1/transcript.json && echo "MARCO 1 OK"
```

A fixture (`tests/fixtures/reuniao_exemplo/`) é uma "reunião" sintética: `mic.wav` com fala pt-BR
(TTS, 16 kHz mono, ~20 s) + `meta.json` mínimo com só o stream `mic` (sem `loopback`, para não
acionar diarização). Frase falada: "Bom dia. Isto é um teste de gravação do Scriba. A reunião de
hoje é sobre o projeto de transcrição. Um, dois, três, quatro, cinco. Obrigado e até a próxima."
Validada com `large-v3-turbo` (GPU, palavra a palavra) e `tiny` (CPU — erra só o nome próprio
"Scriba"; o grep de aceite usa "teste de grava", que o tiny acerta).

Gotcha registrado: o WAV precisa do header canônico de 44 bytes (fmt de 16 bytes) — o
`repair_wav_header` do pipeline patcha os offsets 4 e 40 às cegas e corrompe WAVs com fmt de
18 bytes (como os que o SAPI gera; a fixture foi reescrita pelo módulo `wave` por isso).

## O que ainda NÃO funciona fora do Windows (marcos futuros)

- Captura de áudio do sistema ao vivo (Linux: PipeWire/PulseAudio; macOS: CoreAudio taps/ScreenCaptureKit).
- Detecção automática de reunião (o ConsentStore do registro não tem equivalente POSIX).
- Aceleração de GPU no macOS (CTranslate2 não tem backend Metal; caminho futuro: whisper.cpp/MLX).
- Notificações nativas, hotkey global, autostart, atalhos.
