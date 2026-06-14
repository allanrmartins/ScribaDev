# Modos de IA × requisitos de hardware × público-alvo

**Data:** 2026-06-14 · **Por quê:** decidir, por segmento de usuário, qual modo de IA (full local / parcial local / full nuvem) é viável — o público-alvo do Scriba (consultores SAP/ABAP, analistas CRM, PMs em **notebook corporativo, em geral SEM GPU dedicada**) não consegue rodar tudo localmente. Isto amarra com [pricing](project-pricing-brasil) e com a camada de providers já implementada (`scriba/ai.py`).
**Aviso:** números são ordens de grandeza (variam com modelo, quantização e tamanho do contexto); não são garantia.

## As duas cargas de IA do Scriba
1. **Transcrição (STT)** — Whisper (`faster-whisper` + `ctranslate2`), HOJE sempre local.
2. **Resumo + wizard (LLM)** — agora configurável: claude CLI / **Ollama (local)** / OpenAI-compatible (nuvem).

Os "modos" são a combinação de onde cada uma roda.

## Os 3 modos

| Modo | STT (transcrição) | LLM (resumo) | Áudio sai do PC? | COGS | Hardware |
|---|---|---|---|---|---|
| **Full local** | Whisper local | Ollama local | **Não, nada sai** | **zero** | precisa GPU boa (ou CPU lento) |
| **Parcial local** | Whisper **local** | LLM **nuvem** (OpenAI-compat/gerenciado) | **Áudio não; só o texto da transcrição** | médio (só LLM) | modesto |
| **Full nuvem** | STT nuvem (ex.: Groq) | LLM nuvem | **Sim (áudio + texto)** | alto | qualquer notebook |

> **Estado no código hoje:** os **3 modos já funcionam com BYO key** — **full local** (Whisper + Ollama), **parcial local** (Whisper local + LLM nuvem) e **full nuvem** (STT nuvem via Groq/OpenAI-compat — #23 — + LLM nuvem). Falta só o modo **gerenciado** (chave no nosso backend em vez de o usuário trazer a dele) = #15 / Fase 4.

## Requisitos — Transcrição (Whisper `large-v3-turbo`, o default)
- **GPU, int8**: ~**3 GB** de VRAM (o turbo tem 4 camadas de decoder vs 32 do v3 → bem mais leve; fp16 seria ~10 GB). Próximo de tempo real.
- **CPU, int8**: roda sem GPU, mas **~10-20× mais lento** que GPU. Modelos menores (`small`/`medium`) são bem mais rápidos no CPU; o `large-v3-turbo` no CPU pesa em reunião longa.
- **Disco (modelo baixado, int8)**: `large-v3-turbo` ~**1,5 GB**; `medium` ~**0,8 GB**; `small` ~**0,5 GB**. Baixado uma vez e cacheado (`~/.cache/huggingface`). Se a transcrição local usar GPU, somar as libs CUDA (cuBLAS/cuDNN) empacotadas, **~2-3 GB**.
- **Implicação:** no-GPU + reunião de 1h → transcrição local fica lenta. Para esse perfil, **STT na nuvem (Groq)** ou um modelo Whisper menor é o caminho prático.

## Requisitos — Resumo (Ollama, Q4_K_M)
Fórmula prática: **VRAM ≈ params(B) × bits/8 + KV cache (1-2 GB)**; deixar 2-4 GB de folga pro contexto.

| Modelo | VRAM (GPU, Q4) | RAM (CPU-only) | Velocidade | Qualidade p/ ata |
|---|---|---|---|---|
| 1-3B (Llama 3.2 3B, Qwen2.5 3B) | ~2-3 GB | ~4-6 GB | GPU rápida; CPU ~5 tok/s | fraca/ok |
| **7-8B (Llama 3.1 8B, Qwen2.5 7B)** | **~6-8 GB** | **~8-10 GB** | GPU 40+ tok/s; **CPU 3-8 tok/s** | **boa (recomendado)** |
| 14B+ | 10 GB+ | 16 GB+ | CPU sofrível | ótima |
| 70B | 40 GB+ | — | — | não-consumidor |

- **Reunião longa = contexto grande**: a transcrição de 1h pode ter dezenas de milhares de tokens → o KV cache cresce e **8 GB de VRAM estoura**. 12-16 GB conforta o 7-8B com contexto longo.
- **CPU é viável porque o resumo é assíncrono** (background pós-call): a 3-8 tok/s, uma ata de ~800-1500 tokens leva ~2-8 min — tolerável sem o usuário esperar na tela.

### Disco (Ollama, Q4_K_M)
O que pesa no HD é o **runtime do Ollama** + o **arquivo do modelo** (cada modelo é um download independente; não compartilham peso).

| Item | Disco |
|---|---|
| Runtime do Ollama (Windows, instalado) | ~**1,5-4 GB** |
| Modelo 1-3B (Llama 3.2 3B) | ~**2-3 GB** |
| **Modelo 7-8B (Llama 3.1 8B — recomendado)** | ~**4,5-5 GB** |
| Modelo 14B | ~**9 GB** |
| Modelo 70B | ~**40 GB** |

- **Disco ≠ VRAM/RAM**: o modelo ocupa o HD **e** precisa caber em VRAM/RAM ao rodar.
- No Windows os modelos vão para `%USERPROFILE%\.ollama\models` por padrão — em notebook corporativo o `C:` costuma ser apertado; vale documentar a troca de pasta (`OLLAMA_MODELS`).

## Requisitos mínimos por versão do Scriba
Três versões = os três modos. "Mínimo" = roda; "recomendado" = roda confortável. Disco já inclui o app empacotado (.exe) + modelos baixados.

| Versão | STT / LLM | GPU / VRAM | RAM | Disco livre | Internet |
|---|---|---|---|---|---|
| **Full nuvem** | STT nuvem (Groq) + LLM nuvem | **nenhuma** | **4-8 GB** | ~**0,5-1 GB** (só o app, sem modelos locais) | **obrigatória** (áudio + texto saem) |
| **Só resumo na nuvem** (parcial local) | Whisper **local** + LLM nuvem | opcional: ~3 GB ajuda; sem GPU serve p/ reuniões curtas | **mín. 8 GB / rec. 16 GB** | ~**3-5 GB** (app + Whisper ~1,5 GB + libs CUDA se GPU) | só p/ o resumo (áudio **não** sai) |
| **Full local** | Whisper local + Ollama local | **mín. 8 GB / ideal 12 GB** (ou CPU lento) | **mín. 16 GB** | ~**10-15 GB** (app + Whisper ~1,5 GB + runtime Ollama + modelo 8B ~5 GB) | **nenhuma** (nada sai) |

Notas por versão:
- **Full nuvem** — qualquer notebook corporativo. O peso está na rede/COGS, não no hardware. Depende de #13/#23 (STT nuvem), já BYO key.
- **Só resumo na nuvem** — **melhor equilíbrio para o público-alvo** (sem GPU forte; áudio fica na máquina = argumento de privacidade). Whisper local pode usar GPU modesta (~3 GB) **ou** CPU (ok p/ reuniões curtas; longa fica lenta).
- **Full local** — GPU **≥ 8 GB** roda Whisper turbo (~3 GB VRAM) e um 7-8B (~6-8 GB) **em sequência**, não simultâneo (ex.: a RTX 3070 Ti de 8 GB do dev dá conta). Sem GPU é possível "no talo do CPU" (16 GB RAM, 8 núcleos), mas lento — entusiasta paciente, não corporativo médio. Reserve disco com folga: cada modelo Ollama extra soma o tamanho dele.

## Mapeamento público-alvo → modo
- **Notebook corporativo SEM GPU (a maioria do alvo: SAP/ABAP, CRM, PMs):** **parcial local** (áudio transcrito no PC, só o texto vai pra nuvem — argumento de privacidade forte) ou **full nuvem** (quando #13 existir). Full local não é realista aqui.
- **Dev/power-user com GPU de jogo (8 GB+):** **full local** (Ollama) — zero COGS, 100% privado. É o caso do próprio dev.
- **Empresa com NDA/compliance:** **parcial local** vende sozinho ("o áudio nunca sai da máquina"); full local onde houver hardware.

## Implicações de produto / pricing (amarrar com [pricing](project-pricing-brasil) e [providers](project-providers-ia-planos))
- **Full local = COGS zero** → sustenta plano básico/grátis para quem tem hardware; é o diferencial "local e privado".
- **Parcial local** → COGS só do LLM (texto é barato; o caro é STT, que fica local) → bom custo/privacidade; provável **default do público-alvo**.
- **Full nuvem** → maior COGS (STT nuvem é o pesado); plano gerenciado/maior. Já funciona com **BYO key** (#13 + #23); o modo **gerenciado** (chave no backend, cota por plano) é #15 / Fase 4.
- **Próximo passo natural de produto:** o STT na nuvem (#23) já destravou o full nuvem **BYO** para o público sem GPU; o próximo é o **backend gerenciado** (#15 / Fase 4) — chave no servidor + cota por plano — que vira o plano pago "zero-setup".

## Fontes
- VRAM Ollama por tamanho/quant: [LocalLLM.in — Ollama VRAM guide](https://localllm.in/blog/ollama-vram-requirements-for-local-llms) · [Local AI Master — VRAM 2026](https://localaimaster.com/blog/vram-requirements-2026).
- Ollama CPU-only (RAM, tok/s): [Local AI Master — system requirements](https://localaimaster.com/blog/ollama-system-requirements) · [PromptQuorum — best CPU-only models](https://www.promptquorum.com/prompt-bites/best-ollama-models-cpu-only).
- Whisper large-v3-turbo (VRAM int8, CPU): [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [faster-whisper turbo benchmark #1030](https://github.com/SYSTRAN/faster-whisper/issues/1030).
