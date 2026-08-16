# ScribaDev

> 🎙️ Gravação, transcrição e ata automáticas das suas reuniões — Teams, Zoom, Google Meet e afins no navegador — 100% local e privado.

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Windows 11 | macOS](https://img.shields.io/badge/platform-Windows%2011%20%7C%20macOS-0078d4.svg)

🇧🇷 Português | 🇺🇸 [English](README.en.md)

> ℹ️ **Fork pessoal e local** do Scriba, fixado em `b62e901`. É a minha ferramenta do dia a dia como dev — roda inteira na máquina (Whisper na **minha GPU** + resumo via `claude -p`). **Não é um produto.** O módulo Python continua `scriba`, mas a identidade no Windows é **ScribaDev** (pasta em `%LOCALAPPDATA%\ScribaDev`, atalhos e comando `scribadev`) para conviver com a Scriba na mesma máquina sem colidir.

<p align="center">
  <img src="docs/pilula.png" alt="Pílula de gravação" width="300">
  <br><br>
  <img src="docs/janela.png" alt="ScribaDev — janela principal com reuniões recentes, pendências e status dos serviços" width="480">
  <br><br>
  <img src="docs/notas.png" alt="ScribaDev — leitor de notas com títulos" width="780">
</p>

O ScribaDev fica na bandeja do Windows e **detecta sozinho quando você entra em uma call do Teams, do Zoom — ou de uma reunião no navegador** (Google Meet, Teams web, Zoom web…). Ele grava o áudio (sem nenhum bot entrando na reunião), transcreve localmente com Whisper na sua GPU/CPU e gera um Markdown **com título, cliente e resumo estruturado** (participantes, decisões, requisitos, objetos SAP citados, pendências) mais a **transcrição completa**, com as falas separadas entre **Eu** (seu microfone) e **Participantes** (o áudio que você ouve) — ou **Participante 1/2/3** com a diarização opcional. É um "Granola" caseiro, minimalista e local — pensado para quem usa as notas como contexto de desenvolvimento (por exemplo, no Claude Code).

## Como funciona

```
registro do Windows ──► call detectada (mic em uso no Teams/Zoom — ou no navegador,
        │                confirmada pelo título da janela: Meet, Teams web, Zoom web…)
        │
        ▼
grava 2 trilhas WAV ──► microfone ("Eu") + loopback da saída ("Participantes")
        │  (pílula flutuante na tela enquanto grava: ● 12:34  ■ ×)
        ▼
call termina ──► Whisper local (faster-whisper large-v3-turbo, GPU ou CPU)
        │            o modelo só carrega quando a call termina: a GPU fica livre durante a reunião
        ▼
pyannote local (opcional) ──► separa as vozes em Participante 1/2/3
        │
        ▼
claude -p (opcional) ──► título + cliente + resumo estruturado da reunião
        │
        ▼
notas.md ──► exportado para %LOCALAPPDATA%\ScribaDev\Notas\
        │
        ▼
áudio arquivado em Opus (~20 MB/h) ──► pasta renomeada com o título da nota
```

- **Detecção sem API**: o Windows registra quando o Teams/Zoom abre o microfone (`HKCU\...\CapabilityAccessManager\ConsentStore\microphone`). O ScribaDev só observa esse registro — funciona com qualquer conta, sem Graph API, sem admin.
- **Reuniões no navegador**: mic aberto no Chrome/Edge/Firefox pode ser qualquer site, então a call só é confirmada quando alguma janela do navegador tem um título de reunião ("Meet", "Microsoft Teams", "Zoom", "Webex" — configurável). Confirmada uma vez, a call segue viva enquanto o mic estiver aberto: trocar de aba não derruba a gravação.
- **"Eu" vs "Participantes" por construção**: sua voz vem do microfone e a dos outros vem do loopback da saída de áudio — duas trilhas separadas. Com a diarização opcional, os remotos ainda se dividem por voz em **Participante 1/2/3**.
- **Tudo local**: o áudio nunca sai da sua máquina. Só o *texto* transcrito é enviado (e apenas se o resumo via Claude estiver habilitado).

## Requisitos

| Item | Observação |
|---|---|
| Windows 11 **ou** macOS 14.2+ (Apple Silicon) | Windows usa WASAPI loopback e toasts; macOS usa process tap do CoreAudio e transcrição Metal (MLX) |
| Python 3.12–3.14 *(só na instalação via código-fonte)* | o **instalador** não precisa de Python — `winget install Python.Python.3.12` se for contribuir |
| Teams/Zoom (desktop) ou reuniões no navegador | detecção automática em ambos; Meet, Teams web e afins são confirmados pelo título da janela |
| GPU NVIDIA *(opcional)* | transcrição ~10× mais rápida; sem GPU cai para CPU automaticamente |
| [Claude Code](https://claude.com/claude-code) *(opcional)* | só para o resumo estruturado; sem ele, sai a transcrição pura |
| [ffmpeg](https://ffmpeg.org/download.html) *(recomendado)* | comprime o áudio guardado: WAV cru ~1,3 GB/h → **opus ~20 MB/h**. Instale com `winget install ffmpeg` — ele entra no **PATH do Windows** sozinho (não é uma pasta do ScribaDev) — e **reabra o app**; confira com `where ffmpeg`. Sem ele, as gravações ficam em `.wav` cru e gigantes |
| Fone de ouvido *(recomendado)* | com caixas de som, a voz dos outros vaza no seu microfone e aparece duplicada como "Eu" |

## Instalação

### Opção A — Instalador (recomendado)

Baixe na [página de Releases](https://github.com/allanrmartins/ScribaDev/releases) e instale — **sem Python, sem terminal**:

- **Windows**: `ScribaDev-X.Y.Z-setup.exe`. O instalador não é assinado digitalmente (projeto open source sem certificado pago), então o SmartScreen avisa na primeira execução: clique em **"Mais informações" → "Executar mesmo assim"**. A instalação é por usuário (sem pedir administrador).
- **macOS** (14.2+, Apple Silicon): `ScribaDev-X.Y.Z.dmg`. Arraste o app para **Applications** e, na primeira abertura, use **clique-direito → Abrir** (mesmo motivo: app não assinado). Conceda as permissões de **Microfone**, **Gravação de Tela e Áudio do Sistema** e **Acessibilidade** quando pedidas.

Na primeira abertura, o **wizard de primeiro uso** analisa sua máquina (GPU, memória, disco) e recomenda a melhor configuração:

- **Instalação Expressa** aceita as recomendações e baixa tudo de uma vez (modelo Whisper, bibliotecas CUDA se houver GPU NVIDIA);
- **Instalação Avançada** deixa você escolher o modelo de transcrição (tiny → large-v3-turbo, com tamanho e velocidade de cada um) e os componentes;
- A **separação de vozes** (pyannote) tem um passo a passo guiado para aceitar os termos e criar o token do Hugging Face — e pode ser **pulada** e ativada depois nas Configurações.

### Opção B — via código-fonte (para contribuir)

O `setup.ps1` faz tudo: cria um ambiente Python isolado em `%LOCALAPPDATA%\ScribaDev\venv`, instala as dependências (com suporte CUDA se houver GPU NVIDIA, ~1,3 GB), baixa o modelo Whisper (~1,6 GB na primeira vez), coloca o comando `scribadev` no PATH e termina rodando o `scribadev doctor`.

```powershell
git clone https://github.com/allanrmartins/ScribaDev.git
cd ScribaDev
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

No macOS, o equivalente é `./setup.sh`. Veja o [CONTRIBUTING.md](CONTRIBUTING.md).

**Sem git (ZIP):** no GitHub, clique em **Code → Download ZIP**, extraia, abra um PowerShell na pasta extraída e rode `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

> 💡 A **diarização** (separar as falas em *Participante 1/2/3*) é **opcional**. No instalador, o wizard cuida dela; na instalação via código-fonte, siga [Separar participantes por voz](#separar-participantes-por-voz-opcional) se ela aparecer como `dependências ausentes` no painel **Serviços**.

### Onde ficam os arquivos

| O quê | Onde (padrão) | Configurável? |
|---|---|---|
| Notas finais (`.md`) | `%LOCALAPPDATA%\ScribaDev\Notas\` | ✅ janela de Configurações |
| Gravações (uma pasta por reunião, em árvore por data e nomeada com o título da nota: `2026\06\12\16-34_Boleto não gerado em produção\`) | `C:\temp\scribadev\gravacoes\` | ✅ janela de Configurações |
| Prompt do resumo (`prompt.md`) | `%LOCALAPPDATA%\ScribaDev\prompt.md` | ✅ aba Resumo (editor embutido) |
| Config, logs e ambiente Python | `%LOCALAPPDATA%\ScribaDev\` | — |

Todas as pastas de saída são **criadas automaticamente na primeira utilização** — não precisa criar nada à mão em uma máquina nova.

## Atualização

A versão instalada aparece na **barra de título** e na **capa** da janela (e em `scribadev --version`).

**Instalador (Opção A)**: quando sai versão nova, a capa mostra o aviso com o botão **Baixar**, que leva direto ao instalador do seu sistema. Instale por cima — **config, notas e gravações são preservadas**.

**Código-fonte com git (Opção B)** — atualiza por um comando:

```powershell
scribadev update --check    # só verifica se há versão nova
scribadev update            # aplica: git pull + reinstala se as dependências mudaram
```

Depois de atualizar, **feche e reabra** o ScribaDev (bandeja → **Sair**, depois reabra pelo atalho) para carregar a nova versão.

**Sem git (ZIP)**: baixe o ZIP novo no GitHub (**Code → Download ZIP**), extraia por cima da pasta e rode o `setup.ps1` de novo — ele reaproveita o venv e atualiza só o que mudou.

## Uso

```powershell
scribadev run            # inicia o monitoramento (ícone na bandeja)
scribadev autostart on   # opcional: iniciar junto com o Windows
```

Pronto. Entre numa call do Teams (ou num Meet no navegador) e a pílula aparece no topo da tela, **acompanhando a call inteira**:

- **● 12:34** — gravando, com cronômetro (arraste para onde quiser; a posição fica salva)
- **■** — encerra a gravação agora e processa
- **×** — descarta esta gravação (call que não deve virar nota)
- **⏺ Gravar reunião** — modo espera: aparece quando a gravação automática está desligada, ou depois de você parar/descartar no meio da call; um clique inicia (outra) gravação

A pílula só some quando a call termina. Com `auto_record` desligado (Configurações), nada é gravado sem você clicar no ⏺. Ao sair da call: toast "Transcrevendo…", e em seguida "Notas prontas" com botão para abrir o `.md`.

**Duplo clique no ícone da bandeja** abre a **janela principal** — a antessala das suas notas: as **reuniões recentes** (clique para abrir a nota), um resumo em números (**quantas reuniões, tempo total gravado, clientes**), as **pendências abertas** de todas as atas com contador, a **ligação em andamento com duração ao vivo** com o botão **⏺ Gravar**, e o botão **Notas** em destaque. O diagnóstico dos serviços (detecção, áudio, Whisper/GPU, Claude, diarização, autostart) fica numa seção **Serviços** recolhível. Minimizar mantém o app na barra de tarefas; fechar (X) tira da barra mas o monitoramento continua na bandeja.

O botão **Notas** abre a **janela de Notas** — o coração do app: à esquerda, as reuniões **agrupadas por dia** com **busca por conteúdo** (destaca as ocorrências) e **filtros colapsáveis** (data, cliente, participante); à direita, o leitor da ata em markdown, com **título e cliente identificados pela IA e editáveis** (quando o cliente não dá para inferir da conversa, o campo fica vazio para você digitar) e uma **barra de progresso ao vivo** para reuniões ainda transcrevendo/resumindo. Uma **barra de ações** oferece gerar o prompt de contexto, copiar, **perguntar à reunião** (um chat que busca na transcrição), rotular vozes e excluir. As tabelas (ex.: **Objetos SAP citados**) viram **tabelas de verdade** com **⧉ copiar (Excel)** ao lado — TSV colável direto em células; a **transcrição completa** fica atrás de um link no fim do documento (mostra/oculta sem "pular" a leitura). Ainda: **atalhos de teclado** e **menu de contexto** na lista de reuniões.

O botão **⚙** abre as **Configurações**, com abas **Gravação / Transcrição / IA / Detecção / Pastas / Aparência / Sobre** — agrupadas por categoria, incluindo **atalho de teclado global** para gravar/parar (capture a combinação com o botão "Gravar atalho"). Na aba **IA**, um editor do `prompt.md`: o markdown com as instruções que geram a ata (seções, regras, vocabulário) — personalize à vontade e restaure o padrão quando quiser. Na aba **Aparência**, escolha o **tema**: *Automático* (segue o modo claro/escuro do Windows) ou um dos quatro — **VS Code**, **Sublime**, **Claude** e **Claro** — com troca na hora. A aba **Sobre** mostra a saúde dos componentes (GPU, diarização, ffmpeg) e as atualizações.

<p align="center">
  <img src="docs/configuracoes.png" alt="ScribaDev — Configurações, aba IA com o editor do prompt.md" width="640">
  <br><br>
  <img src="docs/tema.png" alt="ScribaDev — Configurações, aba Aparência com a grade de temas e prévia" width="640">
</p>

Na primeira execução, o **Assistente de perfil** abre sozinho: você descreve profissão, área, stack e jargão, e o ScribaDev **escreve as instruções da ata sob medida para o seu trabalho** — geradas por IA (e validadas contra o formato que o leitor de notas espera) ou por um modelo pronto que funciona offline — e ainda preenche as **hotwords** que guiam a transcrição com o seu vocabulário. Tudo com prévia antes de aplicar (o prompt anterior fica em `prompt.md.bak`); reacesse quando quiser pelo link **Assistente de perfil…** da aba **IA** ou por `scribadev wizard`.

<p align="center">
  <img src="docs/wizard.png" alt="ScribaDev — Assistente de perfil (wizard de prompt por profissão)" width="640">
</p>

### Comandos

| Comando | Para quê |
|---|---|
| `scribadev run` | app de bandeja: detecção + gravação + processamento |
| `scribadev update` | checa e aplica atualizações (git pull); `--check` só verifica |
| `scribadev --version` | mostra a versão instalada |
| `scribadev doctor` | diagnóstico do ambiente (`--toast` testa a notificação) |
| `scribadev devices` | lista microfones e loopbacks disponíveis |
| `scribadev record 60` | gravação manual de N segundos (ex.: reunião fora do Teams) |
| `scribadev transcribe <pasta>` | (re)transcreve uma reunião (`--cpu` força CPU) |
| `scribadev summarize <pasta>` | (re)gera o resumo e o notas.md |
| `scribadev process <pasta>` | tudo de uma vez: transcreve + resume + exporta |
| `scribadev detect` | debug: mostra as transições de detecção em tempo real |
| `scribadev wizard` | assistente de perfil: gera o prompt.md e as hotwords da sua profissão |
| `scribadev autostart on\|off` | atalho na pasta Inicializar do Windows |
| `scribadev shortcut` | (re)cria os atalhos na Área de Trabalho e no menu Iniciar |
| `scribadev purge` | apaga gravações já transcritas além do prazo de retenção (`--days N` sobrepõe, `--dry-run` só lista) |
| `scribadev search "termos"` | busca nas reuniões indexadas (texto, `--client`, `--participant`, datas); `--json` p/ agentes de IA |
| `scribadev show <id-ou-pasta>` | mostra a nota de uma reunião (`--transcript` p/ a transcrição; `--json` p/ agentes) |
| `scribadev reindex` | reconstrói o índice de busca a partir das pastas |

Menu da bandeja: **Gravar agora/Parar** (cobre reuniões presenciais ou apps fora da detecção), **Tema** (troca rápida entre os temas), **Abrir pasta de reuniões/notas**, **Processar pendentes** (retoma o que ficou pela metade se o PC desligou no meio) e **Sair**.

### Perguntar sobre as suas reuniões no Claude Code

O repo traz a skill **`scriba-reunioes`** (`.claude/skills/`): abrindo o [Claude Code](https://claude.com/claude-code) na pasta do projeto, você pergunta em linguagem natural — *"qual foi a última reunião do cliente X?"*, *"o que ficou pendente na call de ontem?"* — e a IA consulta o índice local via `scribadev search --json` / `show`. Tudo 100% na sua máquina. Para usar a skill a partir de **qualquer pasta**, crie uma junction/symlink dela em `~/.claude/skills/`.

## O que sai no `notas.md`

```markdown
---
titulo: Relatório ALV de materiais ZMM001     ← gerado pela IA, editável no app
cliente: Aurora                               ← identificado na conversa; vazio = digite no app
data: 2026-06-10T14:30:12
duracao_minutos: 47
origem: scriba
whisper: large-v3-turbo (cuda)
---

# Relatório ALV de materiais ZMM001

*2026-06-10 14:30 · 47 min · Cliente: Aurora*

> **Contexto para IA:** registro técnico da reunião — execute a atividade descrita em Objetivo.

## Objetivo                      ← **Tipo:** desenvolvimento | análise/debug | estimativa | suporte… + o que fazer
## Contexto                      ← módulo SAP, sistema/release, ambiente
## Detalhamento                  ← molda-se à atividade: spec funcional, roteiro de debug, insumos de estimativa…
### Critérios de aceite          ← checklist verificável `- [ ]`
## Regras de negócio             ← RN-01, RN-02… algoritmos e definições funcionais ditos na call
## Objetos SAP citados           ← tabela: Tipo | Objeto | Observação | Quando
## Decisões                      ← autossuficientes, com timestamps [HH:MM:SS]
## Pendências e Ações            ← o que NÃO ficou definido (lacunas para a IA sinalizar)
## Participantes                 ← com a diarização: "Participante 1 — João, funcional"

---

## Transcrição completa

**[00:00:12] Participante 1:** Bom dia! Precisamos de um relatório na ZMM001...
**[00:00:45] Eu:** Entendi. O campo MATNR vem da MARA?
```

O `notas.md` é formatado para ser **usado como contexto numa IA** (Claude Code, etc.): o **Objetivo** classifica a atividade — a call pode ser desenvolvimento, análise/debug de standard, estimativa de esforço, suporte a funcional ou a outro ABAP — e o **Detalhamento** se molda a ela; **Regras de negócio** captura algoritmos e definições funcionais ditos na conversa, e **Pendências** aponta o que ainda não está definido para a IA não presumir. A cópia final vai para `%LOCALAPPDATA%\ScribaDev\Notas\` — e cada reunião vive numa pasta própria em `C:\temp\scribadev\gravacoes\` (árvore `AAAA\MM\DD\`, renomeada com o título da nota), onde a gravação termina **arquivada em Opus 16 kHz mono** (~20 MB/hora em vez de ~1,3 GB de WAV cru — exatamente o formato que o Whisper consome, sem perda para transcrição). Se a gravação vier incompleta (app encerrado no meio da call, microfone que parou), a nota **avisa no topo** a partir de que minuto o áudio se perdeu. Tudo configurável na janela de Configurações, incluindo a **retenção**: gravações já transcritas são apagadas após N dias (padrão 30; a nota final nunca é tocada).

## Configuração

Arquivo criado no primeiro uso em `%LOCALAPPDATA%\ScribaDev\config.toml`:

```toml
[detection]
apps = "teams, zoom"     # apps desktop monitorados no registro de uso do microfone
# Reuniões no navegador: mic aberto num destes processos + título de janela
# casando browser_titles = call detectada. browsers="" desliga a camada web;
# browser_titles="" grava qualquer site que use o mic.
browsers = "chrome, msedge, firefox, brave, opera, vivaldi"
browser_titles = "Meet, Microsoft Teams, Zoom, Webex"
min_call_seconds = 30    # gravações mais curtas são ignoradas (pré-join, teste de mic)
grace_seconds = 8        # mic liberado por até X s ainda é a mesma call
auto_record = true       # gravar sozinho ao detectar a call; desligado, a pílula espera o ⏺

[audio]
loopback_device = ""     # vazio = saída padrão; senão, parte do nome (scribadev devices)
keep_audio = true        # manter o áudio da reunião após transcrever
archive_format = "opus"  # opus (~20 MB/h) | flac (lossless ~110 MB/h) | wav (cru ~1,3 GB/h)
retention_days = 30      # apaga gravações já transcritas após N dias (0 = nunca)

[whisper]
model = "large-v3-turbo" # qualquer modelo do faster-whisper
device = "auto"          # auto | cuda | cpu
language = "pt"
batch_size = 8           # inferência em lote (~2x mais rápido); 0 desliga
hotwords = "SAP ABAP BAPI BAdI CDS SE16N MARA ..."  # vocabulário que guia a transcrição

[diarization]
enabled = false          # Participante 1/2/3 por voz (ver seção abaixo)
hf_token = ""            # token de leitura do Hugging Face

[summary]
enabled = true           # resumo via Claude Code CLI (claude -p)
model = "claude-sonnet-4-6"  # ou claude-opus-4-8 (dropdown na aba Resumo)

[ui]
overlay = true           # pílula flutuante
hotkey = "ctrl+alt+r"    # atalho global gravar/parar; vazio desativa

[output]
export_dir = ""          # vazio = %LOCALAPPDATA%\ScribaDev\Notas
recordings_dir = ""      # vazio = C:\temp\scribadev\gravacoes (criada automaticamente)
```

O ScribaDev é focado em pt-BR por padrão, mas `language` e `hotwords` aceitam qualquer idioma/vocabulário suportado pelo Whisper.

### Separar participantes por voz (opcional)

Com a **diarização** ativa, as falas dos outros participantes saem como **Participante 1/2/3** em vez de um "Participantes" único — e o resumo tenta associar cada um ao nome/papel citado na conversa. Roda 100% local (pyannote.audio na GPU/CPU):

1. **Instale o torch e o pyannote no venv do ScribaDev** — cole no PowerShell:

   ```powershell
   $py = "$env:LOCALAPPDATA\ScribaDev\venv\Scripts\python.exe"
   & $py -m pip install torch --index-url https://download.pytorch.org/whl/cu128   # sem GPU NVIDIA: & $py -m pip install torch
   & $py -m pip install "pyannote.audio>=4,<5"
   ```

2. **Aceite os termos dos três modelos** (gratuitos, mas "gated"): entre — ou crie uma conta gratuita — no Hugging Face e, em cada uma destas páginas, preencha o formulário curto e clique em **"Agree and access repository"**: [speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) (o que o pyannote 4.x realmente usa), [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) e [segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0);
3. **Gere um token de acesso** em [hf.co/settings/tokens](https://huggingface.co/settings/tokens): clique em **"Create new token"**, escolha o tipo **Read** (basta), dê um nome (ex.: `scriba`) e clique em **"Create token"**. Copie o valor — ele começa com `hf_` e **só aparece uma vez**;
4. Na aba **Gravação**: ligue "Separar participantes por voz" e cole o token. O modelo baixa uma vez e o resto é offline.

> 💡 Na instalação pelo **instalador** (setup.exe/DMG), o **wizard de primeiro uso** guia esses passos — inclusive o download do torch/pyannote — sem precisar de PowerShell.

## Privacidade

- O **áudio nunca sai da sua máquina** — gravação e transcrição são 100% locais.
- Com `[summary] enabled = true`, o **texto** da transcrição é enviado à Anthropic via Claude Code (a mesma conta/assinatura que você já usa). Desabilite para um fluxo 100% offline.
- Gravações e transcrições ficam em `C:\temp\scribadev\gravacoes` (configurável), fora de pastas sincronizadas — e são **apagadas automaticamente** após o prazo de retenção (padrão 30 dias; `0` mantém para sempre); config e logs em `%LOCALAPPDATA%\ScribaDev`. Só o `.md` final vai para `%LOCALAPPDATA%\ScribaDev\Notas`.

## Aviso legal

O ScribaDev grava **localmente** o áudio que entra e sai da sua máquina, sem avisar os demais participantes (nenhum bot entra na call). As leis sobre gravação de conversas variam por país, e políticas de empresas/clientes podem exigir consentimento explícito. **Verifique as regras do seu contexto antes de usar — a responsabilidade pelo uso é sua.**

## Limitações conhecidas

- Na detecção **em navegador**, a call é confirmada pelo título de uma janela — se você entrar na reunião e trocar de aba no mesmo instante, a gravação começa quando a aba da call voltar a ficar ativa (o mic seguir aberto mantém a espera). A pílula, o atalho e **Gravar agora** continuam cobrindo qualquer caso.
- Se o Teams tocar áudio numa saída diferente da padrão do Windows, configure `loopback_device`.
- Trocar de fone **no meio** da call pode silenciar a trilha dos participantes (reabertura de stream está no roadmap).
- Sem fone (som nas caixas), a fala dos outros entra também pelo microfone e duplica como "Eu".

## Roadmap

- Reabertura de stream ao trocar de dispositivo no meio da call

## Desenvolvimento

Contribuições são bem-vindas - veja o [CONTRIBUTING.md](CONTRIBUTING.md). O código segue pequeno e modular: o **backend** (`detector`, `recorder`, `transcriber`, `diarize`, `merge`, `notes`, `meetings_index`, `main`) e a **UI em PySide6 (Qt)** em `scriba/qt/` (`theme`, `widgets`, `main_window`, `notes_ui`, `settings_ui`, `chat_ui`, `overlay`, `tray`, `log_ui`, `speakers_ui`, `wizard_ui`). Antes de mexer: `scribadev doctor` (diagnóstico do ambiente) e `python -m unittest discover -s tests` (suíte unittest da stdlib, sem pytest).

## Licença

ScribaDev é open source sob a licença [MIT](LICENSE) © Allan Martins.
As licenças das dependências estão em [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md).
