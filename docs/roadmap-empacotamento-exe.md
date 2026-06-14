# Roadmap — Empacotamento do Scriba como aplicativo instalável (.exe)

**Status:** planejado · **Pré-requisito de:** go-live (deploy) · **Criado:** 2026-06-13

## Objetivo

Transformar o Scriba — hoje um pacote Python rodado a partir do código-fonte — num **aplicativo Windows instalável**: a pessoa baixa um instalador (`setup.exe`), instala e usa, **sem Python, sem ver o código-fonte e sem depender de nada da máquina de desenvolvimento**. Isto é **pré-requisito do deploy/go-live** e passa a ser **restrição de todas as decisões de design** a partir de agora.

## Por quê

1. **Código exposto.** Distribuir `.py`/wheel deixa o código-fonte legível — inaceitável para um produto pago (Elastic-2.0, source-available só sob nossos termos). Precisamos de binário, idealmente compilado.
2. **Dependências da máquina de dev.** O app hoje assume coisas que só existem no ambiente do desenvolvedor — o caso gritante é o **resumo via `claude` CLI** (`scriba/notes.py:198`, `scriba/cli.py:214`): roda `claude -p` na conta do dev. O usuário final não tem isso. O próprio código já marca: *"BETA: claude -p na conta do usuário. No go-live vira provider configurável"* (`scriba/notes.py:200`).
3. **Experiência de produto.** "Baixar e instalar" é a expectativa de um SaaS desktop. `pip install` + CLI não é distribuível ao público-alvo (SAP/ABAP, PMs, empresas).

## Estado atual (baseline)

**A favor:**
- Transcrição usa `faster-whisper` + `ctranslate2` (não torch direto) → core relativamente leve e empacotável.
- CUDA é **opcional** (extra `[cuda]`) e diarização/torch é **opcional** (extra `[diarization]`) → o peso-pesado já é separável.
- Já existe infra de app: ícone (.ico/.png), atalhos `.lnk` (`scriba/shortcuts.py`), autostart, AppUserModelID, tray (`pystray`), config em `%LOCALAPPDATA%\Scriba`, toasts.

**Bloqueadores:**
- **Resumo acoplado ao `claude` CLI** (bloqueador #1, arquitetural).
- Deps com **binários nativos**: `pyaudiowpatch` (PortAudio/WASAPI loopback), `ctranslate2` (+CUDA opcional) — precisam ser empacotadas corretamente.
- **Modelos Whisper** baixados em runtime (cache HF) — decidir bundlar vs baixar no 1º uso.
- Nenhuma configuração de build de binário ainda (sem `.spec`/Nuitka).

## Fases

### Fase 0 — Pré-requisitos arquiteturais (antes de empacotar)
- [ ] **0.1 — Desacoplar o resumo do `claude` CLI** → provider distribuível. **(PARCIAL, 2026-06-14: resumo + wizard já passam pelo provider configurável `scriba/ai.py` — claude CLI / Ollama local / OpenAI-compatível BYO; falta só o modo gerenciado/backend = Fase 4.)** Duas trilhas (alinhadas ao roadmap SaaS e à regra anti-abuso):
  - **Backend/gateway de assinatura**: o app autentica com a assinatura; o *servidor* chama a IA (chave no backend, cota por plano, billing). Nenhuma chave no cliente.
  - **Modelo local (Ollama)** para o tier privado/grátis (sem COGS, 100% local — preserva o "local e privado").
  - Manter a UI de seleção de modelo (`settings_ui`), trocando o backing de `claude -p` para o provider configurável.
- [x] **0.2 — Resolução de recursos compatível com bundle** — FEITO (2026-06-14). Auditoria: o único acesso por `__file__` era `util.ASSETS_DIR` (ícones); todos os outros módulos já usavam `util.ICON_*`. Centralizado em `util.resource_path()` (prefere `sys._MEIPASS`, fallback `__file__`/Nuitka) — seam único a ajustar na Fase 1. Templates (`DEFAULT_CONFIG`/`DEFAULT_SUMMARY_PROMPT`) são constantes em código; dados do usuário ficam em `APP_DIR`. Coberto por `tests/test_util.py`.
- [x] **0.3 — Auditoria de dependências** — FEITO (2026-06-14), ver [docs/auditoria-licencas-deps.md](auditoria-licencas-deps.md). Pilha majoritariamente permissiva (MIT/Apache/BSD). 4 itens com ação: **pystray (LGPL-3.0)** e **FFmpeg/PyAV (LGPL)** → manter relinkáveis no bundle (avaliar trocar o pystray por lib MIT); **cuDNN/cuBLAS (proprietárias NVIDIA)** → add-on opcional fora do base; **atribuição** (THIRD-PARTY-LICENSES). pyannote 3.1 é MIT + comercial OK (só gated). Nada bloqueante.

### Fase 1 — Empacotamento (Python → binário)
- [ ] **1.1 — Escolher a ferramenta** (PoC com o core: faster-whisper/ctranslate2/pyaudiowpatch):
  - **Nuitka** — compila para C; **melhor proteção do código** (atende diretamente "o Python deixa o código visível"). Recomendado, sujeito à viabilidade com as deps nativas.
  - **PyInstaller** — mais simples e maduro com essas libs, mas bytecode recuperável (proteção fraca).
  - *Honestidade:* nenhum protege 100%, mas Nuitka eleva muito a barra de engenharia reversa.
- [ ] **1.2 — Estratégia de modelos**: baixar Whisper no 1º uso (instalador enxuto — recomendado) vs bundlar (instalador grande).
- [ ] **1.3 — GPU/CPU**: empacotar core **CPU** funcional; CUDA/cuDNN como add-on opcional (download); diarização (torch) como módulo opcional baixável. Fallback CPU obrigatório.
- [ ] **1.4 — DLLs nativas**: garantir PortAudio (`pyaudiowpatch`) e `ctranslate2` no bundle; testar gravação WASAPI loopback dentro do `.exe`.
- [ ] **1.5 — Subprocessos que assumem layout de venv** (achado na auditoria da 0.2): `util.run_audio_probe` e o bootstrap CUDA usam `Path(sys.prefix)/Scripts/python.exe` e `.../site-packages/nvidia` — caminhos que NÃO existem no `.exe` congelado. Rever para `sys.executable` / recursos embarcados ao empacotar.

### Fase 2 — Instalador
- [ ] **2.1 — Inno Setup** (recomendado): `setup.exe` único; instala em Program Files; cria atalhos (reusar/relayar `shortcuts.py`), autostart opt-in, AppUserModelID; **desinstalador** limpo (preservar/limpar `%LOCALAPPDATA%\Scriba` conforme escolha).
- [ ] **2.2 — Primeiro-run**: wizard de perfil (já existe) + download do modelo + login/ativação da assinatura.

### Fase 3 — Confiança e distribuição
- [ ] **3.1 — Code signing**: certificado (OV/EV). Sem assinatura, o **SmartScreen barra/avisa** no "baixar e instalar"; EV evita o aviso de reputação inicial. Custo recorrente de go-live/COGS.
- [ ] **3.2 — Canal de download**: página/link do instalador (versão + checksum).
- [ ] **3.3 — Auto-update**: checagem de versão + download do novo instalador (ou delta).

### Fase 4 — Licenciamento (amarra com o SaaS)
- [ ] **4.1 — Ativação por assinatura**: login/token contra o backend (mesmo gateway do resumo); cota por plano como teto (anti-abuso no backend).

## Restrição permanente de design (a partir de agora)

Toda decisão de design/PR daqui pra frente deve passar por:
1. **Empacota como .exe?** Nada que dependa de ferramenta instalada na máquina do dev (o `claude` CLI é o exemplo a eliminar).
2. **Recursos** acessados de forma compatível com bundle (`frozen`/`_MEIPASS`).
3. **Nova dependência?** Checar: empacotável, binário nativo, peso no instalador, licença comercial, necessidade de GPU.
4. **Segredos** (chaves de API) **nunca no cliente** — só no backend.
5. **Código compilável** (se Nuitka) — evitar padrões dinâmicos que ele não suporta bem.
6. **Privacidade preservada**: enviar dados ao backend é opt-in; manter um caminho 100% local (Ollama).

## Ordem recomendada

`0 (desacoplar claude + bundle de recursos)` → `1 (PoC de empacotamento)` → `2 (instalador)` → `3 (signing/distribuição)` → `4 (licença)`.

A **Fase 0.1** é o maior bloqueador e o item mais alavancado: destrava tanto o empacotamento quanto o modelo de negócio.
