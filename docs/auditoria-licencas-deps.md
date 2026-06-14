# Auditoria de licenças das dependências (Fase 0.3 do empacotamento)

**Data:** 2026-06-14 · **Escopo:** redistribuição comercial do Scriba como `.exe` (produto pago, Elastic-2.0).
**Aviso:** levantamento técnico para orientar a engenharia — **não é parecer jurídico**. Antes do go-live, revisar com advogado e gerar um `THIRD-PARTY-LICENSES.txt` no instalador.

## Veredito

A pilha é, na maior parte, **permissiva (MIT/Apache-2.0/BSD)** e segura para redistribuir, **desde que se inclua os textos de licença/atribuição** no instalador. Há **4 itens que exigem ação**, nenhum bloqueante:

1. **`pystray` é LGPL-3.0** → num `.exe` congelado, o usuário precisa poder **substituir/relincar** o componente. (ação principal)
2. **FFmpeg** (via PyAV, transitivo; e via o `ffmpeg` CLI que o app chama) é **LGPL** → manter build LGPL + DLLs substituíveis.
3. **cuDNN/cuBLAS** (`nvidia-*`) são **proprietárias da NVIDIA** → manter como **download opcional**, fora do instalador base, sob os termos da NVIDIA.
4. **Atribuição** (Apache/BSD/MIT) → obrigatório embarcar os avisos de copyright + textos de licença.

## Tabela

| Pacote | Versão | Papel | Licença | Binário nativo | Obrigatório? | Risco | Ação |
|---|---|---|---|---|---|---|---|
| pyaudiowpatch | 0.2.12.8 | captura WASAPI loopback | **Apache-2.0** | sim (PortAudio) | sim (core) | baixo | atribuição |
| faster-whisper | 1.2.1 | transcrição (STT) | **MIT** | não | sim (core) | baixo | atribuição |
| ctranslate2 | ~4.8 | engine do Whisper | **MIT** | sim (.dll) | sim (core) | baixo | atribuição |
| Pillow | ≥11 | imagens (ícones tray) | **MIT-CMU (HPND)** | sim | sim (core) | baixo | atribuição |
| windows-toasts | 1.3.1 | notificações toast | **Apache-2.0** | não (WinRT) | sim (core) | baixo | atribuição |
| **pystray** | 0.19.5 | ícone de bandeja | **LGPL-3.0** | não | sim (core) | **médio** | **relinkável no bundle (ver abaixo)** |
| av (PyAV) | (transitivo de faster-whisper) | decodificação de áudio | **BSD-3** (código) + **FFmpeg LGPL** nas wheels | sim (FFmpeg .dll) | sim (core) | **médio** | manter wheel LGPL + DLLs substituíveis |
| tokenizers / huggingface-hub | (transitivos) | download/tokenização | **Apache-2.0** | parcial | sim (core) | baixo | atribuição |
| onnxruntime | (transitivo) | VAD/aux | **MIT** | sim | sim (core) | baixo | atribuição |
| numpy | (transitivo) | numérico | **BSD-3** | sim | sim (core) | baixo | atribuição |
| nvidia-cublas-cu12 | ~12.9 | GPU (cuBLAS) | **Proprietária NVIDIA** | sim | **opcional** `[cuda]` | **médio** | download opcional, fora do base; seguir SLA NVIDIA |
| nvidia-cudnn-cu12 | ~9.23 | GPU (cuDNN) | **Proprietária NVIDIA** | sim | **opcional** `[cuda]` | **médio** | idem cuBLAS |
| pyannote.audio | ≥3.1 | diarização (separar vozes) | **MIT** (código e pesos) | via torch | **opcional** `[diarization]` | baixo | opt-in; usuário aceita termos no HF |
| torch | (de pyannote) | runtime ML | **BSD-3** | sim (grande) | **opcional** `[diarization]` | baixo | atribuição; módulo opcional baixável |
| **pesos do Whisper** (large-v3-turbo) | runtime (HF) | modelo STT | **MIT** (OpenAI/Systran) | — (dados) | sim (baixado no 1º uso) | baixo | confirmar o repo HF exato; baixar no 1º uso |

## Itens de risco — detalhe e mitigação

### 1. pystray — LGPL-3.0 (ação principal)
LGPL permite uso em produto fechado, **mas** exige que o destinatário possa **trocar a biblioteca** por outra versão (relink). Empacotadores como o PyInstaller embutem tudo "estaticamente", o que conflita com isso. Mitigações (escolher uma na Fase 1):
- **Manter o `pystray` relinkável**: deixar o código do `pystray` como **dados ao lado do `.exe`** (não embutido), equivalente a linkar dinamicamente; OU
- **Substituir** por uma lib de bandeja permissiva (ex.: `Infi.Systray` MIT, ou implementação WinAPI própria via `ctypes` — o app já usa `ctypes`/`windll` à vontade); OU
- Fornecer os objetos/instruções de relink + o aviso LGPL.
> Recomendo avaliar a substituição: remove a única dependência copyleft do core e simplifica o bundle.

### 2. FFmpeg — LGPL (via PyAV e via CLI)
Duas entradas: (a) **PyAV** (decodifica áudio na transcrição) embute FFmpeg nas wheels — as **wheels oficiais do PyAV são LGPLv3** (≠ as GPLv3 do upstream); (b) `util.ffmpeg_command()` chama um **`ffmpeg` externo** no PATH (compactação opus/flac). Mitigação:
- Manter o FFmpeg como **DLLs/EXE separados** (PyAV já entrega assim) → linkagem dinâmica, satisfaz a LGPL; incluir o aviso LGPL + oferta de código.
- Se for **bundlar um `ffmpeg.exe`** para o caminho CLI, usar um **build LGPL** (sem `--enable-gpl`/libx264 etc.). Hoje o `ffmpeg` é externo (não redistribuído) — decidir na Fase 1 se bundla.

### 3. cuDNN / cuBLAS — proprietárias NVIDIA
Distribuídas via PyPI mas sob a **licença proprietária da NVIDIA** (cuDNN SLA), não OSI. A redistribuição do runtime junto do app é permitida **sob os termos da NVIDIA**. Como CUDA já é **extra opcional** (`[cuda]`), a mitigação casa com o roadmap (Fase 1.3): **base CPU-only**; CUDA/cuDNN como **add-on baixado** sob aceite dos termos NVIDIA — fora do instalador base.

### 4. pyannote (diarização) — MIT, mas gated
O código `pyannote.audio` e os pesos `speaker-diarization-3.1`/`segmentation-3.0` são **MIT** e permitem **uso comercial** ("will always remain open-source"); são apenas **gated** no Hugging Face (coletam dados do usuário no aceite). Como a diarização já é **opt-in** e o app já conduz o usuário a aceitar os termos do modelo + token HF, o risco é baixo. Confirmar na geração final que o repo HF usado é o oficial `pyannote/*`.

## Recomendações por fase
- **Fase 1.1 (escolha do empacotador):** ao decidir Nuitka vs PyInstaller, tratar `pystray` (LGPL) e FFmpeg (LGPL) como **relinkáveis** — não embutir opaco. Reavaliar substituir o `pystray`.
- **Fase 1.3 (GPU/CPU):** base **CPU-only**; `nvidia-*` (cuDNN/cuBLAS) e `torch`/`pyannote` como **add-ons baixáveis opcionais**, fora do instalador base.
- **Fase 2 (instalador):** incluir `THIRD-PARTY-LICENSES.txt` com os textos de **Apache-2.0, MIT, MIT-CMU/HPND, BSD-3, LGPL-3.0** e a **oferta de código-fonte** dos componentes LGPL (pystray, FFmpeg).
- **Antes do go-live:** revisão jurídica do `THIRD-PARTY-LICENSES.txt` e da estratégia LGPL.

## Fontes
- PyPI metadata (campo license/classifiers) de cada pacote · repos oficiais.
- pyannote 3.1 (MIT, comercial OK, gated): [HF model card](https://huggingface.co/pyannote/speaker-diarization-3.1).
- LGPL + PyInstaller (relink): [velovix — LGPL/GPL compliance with PyInstaller](https://velovix.github.io/post/lgpl-gpl-license-compliance-with-pyinstaller/).
- PyAV wheels LGPLv3 + FFmpeg GPL se compilar componentes GPL: [FFmpeg legal](https://www.ffmpeg.org/legal.html) · [PyAV](https://github.com/pyav-org/pyav).
