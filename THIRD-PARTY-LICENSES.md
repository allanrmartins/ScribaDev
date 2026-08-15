# Licenças de terceiros

O ScribaDev é MIT (ver [LICENSE](LICENSE)).
Este arquivo lista as dependências redistribuídas ou baixadas pelo app e suas licenças.
Baseado na [auditoria de 2026-06-14](docs/auditoria-licencas-deps.md), atualizado após o corte para Qt e o port macOS (PR #137, que removeu o pystray/Pillow do runtime).
Levantamento técnico, não parecer jurídico.

## Runtime (todas as plataformas)

| Componente | Licença | Observação |
|---|---|---|
| PySide6 / Qt | LGPL-3.0 | Vinculação dinâmica (as bibliotecas Qt ficam como arquivos separados no bundle, substituíveis) |
| faster-whisper | MIT | |
| ctranslate2 | MIT | |
| av (PyAV) | BSD-3 (código) + FFmpeg LGPL nas wheels | FFmpeg permanece como bibliotecas dinâmicas separadas |
| onnxruntime | MIT | |
| numpy | BSD-3 | |
| tokenizers / huggingface-hub | Apache-2.0 | |

## Runtime (Windows)

| Componente | Licença | Observação |
|---|---|---|
| pyaudiowpatch (PortAudio) | Apache-2.0 / licença PortAudio | Captura WASAPI loopback |
| windows-toasts | Apache-2.0 | |

## Runtime (macOS)

| Componente | Licença | Observação |
|---|---|---|
| sounddevice (PortAudio) | MIT / licença PortAudio | |
| pyobjc (CoreAudio, ApplicationServices) | MIT | |
| mlx-whisper / MLX | MIT | Apple Silicon (arm64) |

## Opcionais baixados sob demanda (instalação Expressa/Avançada do wizard)

| Componente | Licença | Observação |
|---|---|---|
| nvidia-cublas-cu12 / nvidia-cudnn-cu12 | Proprietária NVIDIA (SLA) | Nunca embarcadas no instalador; download sob os termos da NVIDIA, só com GPU NVIDIA |
| torch | BSD-3 | Só para diarização |
| pyannote.audio (código e pesos) | MIT | Pesos "gated" no Hugging Face: o usuário aceita os termos e usa o próprio token |

## Modelos baixados no primeiro uso

| Modelo | Licença |
|---|---|
| Whisper (pesos, ex.: large-v3-turbo via Systran/OpenAI) | MIT |
| pyannote speaker-diarization / segmentation | MIT (gated no HF) |

## Ferramentas externas

- `ffmpeg` (CLI no PATH, opcional para compactar áudio em opus/flac): instalado pelo usuário, não redistribuído pelo projeto.
  Se um binário vier a ser embarcado no instalador, deve ser build LGPL (sem componentes GPL).
- `claude` CLI / Ollama / endpoint OpenAI-compatível (provider de IA do resumo): escolhidos e instalados pelo usuário.

## Assets

- Ícones Fluent UI System Icons (Microsoft), MIT - vendorizados em `scriba/assets/`.

Os textos integrais das licenças (MIT, Apache-2.0, BSD-3, LGPL-3.0, PortAudio) serão embarcados nos instaladores (issues #142/#143 do épico #138).
