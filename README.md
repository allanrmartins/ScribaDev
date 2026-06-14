# Scriba

> 🎙️ Gravação, transcrição e ata automáticas das suas reuniões — Teams, Zoom, Google Meet e afins no navegador — 100% local e privado. **Gratuito durante o beta.**

[![License: Elastic 2.0](https://img.shields.io/badge/license-Elastic%202.0-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Windows 11](https://img.shields.io/badge/platform-Windows%2011-0078d4.svg)

🇧🇷 Português | 🇺🇸 [English](README.en.md)

<p align="center">
  <img src="docs/pilula.png" alt="Pílula de gravação" width="300">
  <br><br>
  <img src="docs/janela.png" alt="Scriba — janela principal com status dos serviços" width="480">
  <br><br>
  <img src="docs/notas.png" alt="Scriba — leitor de notas com títulos" width="780">
</p>

O Scriba fica na bandeja do Windows e **detecta sozinho quando você entra em uma call do Teams, do Zoom — ou de uma reunião no navegador** (Google Meet, Teams web, Zoom web…). Ele grava o áudio (sem nenhum bot entrando na reunião), transcreve localmente com Whisper na sua GPU/CPU e gera um Markdown **com título, cliente e resumo estruturado** (participantes, decisões, requisitos, objetos SAP citados, pendências) mais a **transcrição completa**, com as falas separadas entre **Eu** (seu microfone) e **Participantes** (o áudio que você ouve) — ou **Participante 1/2/3** com a diarização opcional. É um "Granola" caseiro, minimalista e de código aberto — pensado para quem usa as notas como contexto de desenvolvimento (por exemplo, no Claude Code).

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
notas.md ──► exportado para Documentos\Scriba\
        │
        ▼
áudio arquivado em Opus (~20 MB/h) ──► pasta renomeada com o título da nota
```

- **Detecção sem API**: o Windows registra quando o Teams/Zoom abre o microfone (`HKCU\...\CapabilityAccessManager\ConsentStore\microphone`). O Scriba só observa esse registro — funciona com qualquer conta, sem Graph API, sem admin.
- **Reuniões no navegador**: mic aberto no Chrome/Edge/Firefox pode ser qualquer site, então a call só é confirmada quando alguma janela do navegador tem um título de reunião ("Meet", "Microsoft Teams", "Zoom", "Webex" — configurável). Confirmada uma vez, a call segue viva enquanto o mic estiver aberto: trocar de aba não derruba a gravação.
- **"Eu" vs "Participantes" por construção**: sua voz vem do microfone e a dos outros vem do loopback da saída de áudio — duas trilhas separadas. Com a diarização opcional, os remotos ainda se dividem por voz em **Participante 1/2/3**.
- **Tudo local**: o áudio nunca sai da sua máquina. Só o *texto* transcrito é enviado (e apenas se o resumo via Claude estiver habilitado).

## Requisitos

| Item | Observação |
|---|---|
| Windows 11 | usa WASAPI loopback e toasts modernos |
| Python 3.12–3.14 | `winget install Python.Python.3.12` se não tiver |
| Teams/Zoom (desktop) ou reuniões no navegador | detecção automática em ambos; Meet, Teams web e afins são confirmados pelo título da janela |
| GPU NVIDIA *(opcional)* | transcrição ~10× mais rápida; sem GPU cai para CPU automaticamente |
| [Claude Code](https://claude.com/claude-code) *(opcional)* | só para o resumo estruturado; sem ele, sai a transcrição pura |
| Fone de ouvido *(recomendado)* | com caixas de som, a voz dos outros vaza no seu microfone e aparece duplicada como "Eu" |

## Instalação

A instalação é **automática**: o `setup.ps1` faz tudo — cria um ambiente Python isolado, instala as dependências (com suporte CUDA se houver GPU NVIDIA, ~1,3 GB), baixa o modelo Whisper (~1,6 GB na primeira vez), coloca o comando `scriba` no PATH e termina rodando o `scriba doctor`, que confere GPU, Teams, áudio, modelo e pastas.

**Opção A — com git:**

```powershell
git clone https://github.com/allanrmartins/Scriba.git
cd Scriba
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

**Opção B — sem git:** no GitHub, clique em **Code → Download ZIP**, extraia, abra um PowerShell na pasta extraída e rode `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

### Onde ficam os arquivos

| O quê | Onde (padrão) | Configurável? |
|---|---|---|
| Notas finais (`.md`) | `Documentos\Scriba\` | ✅ janela de Configurações |
| Gravações (uma pasta por reunião, em árvore por data e nomeada com o título da nota: `2026\06\12\16-34_Boleto não gerado em produção\`) | `C:\temp\scriba\gravacoes\` | ✅ janela de Configurações |
| Prompt do resumo (`prompt.md`) | `%LOCALAPPDATA%\Scriba\prompt.md` | ✅ aba Resumo (editor embutido) |
| Config, logs e ambiente Python | `%LOCALAPPDATA%\Scriba\` | — |

Todas as pastas de saída são **criadas automaticamente na primeira utilização** — não precisa criar nada à mão em uma máquina nova.

## Uso

```powershell
scriba run            # inicia o monitoramento (ícone na bandeja)
scriba autostart on   # opcional: iniciar junto com o Windows
```

Pronto. Entre numa call do Teams (ou num Meet no navegador) e a pílula aparece no topo da tela, **acompanhando a call inteira**:

- **● 12:34** — gravando, com cronômetro (arraste para onde quiser; a posição fica salva)
- **■** — encerra a gravação agora e processa
- **×** — descarta esta gravação (call que não deve virar nota)
- **⏺ Gravar reunião** — modo espera: aparece quando a gravação automática está desligada, ou depois de você parar/descartar no meio da call; um clique inicia (outra) gravação

A pílula só some quando a call termina. Com `auto_record` desligado (Configurações), nada é gravado sem você clicar no ⏺. Ao sair da call: toast "Transcrevendo…", e em seguida "Notas prontas" com botão para abrir o `.md`.

**Duplo clique no ícone da bandeja** abre a **janela principal**: status de todos os serviços (detecção, áudio, Whisper/GPU, Claude, diarização, autostart), a **ligação em andamento com duração ao vivo** e o botão **⏺ Gravar**. Minimizar mantém o app na barra de tarefas; fechar (X) tira da barra mas o monitoramento continua na bandeja.

O botão **Notas** abre a **janela de Notas**: leitor embutido das atas (markdown renderizado, agrupado por dia, com **título e cliente identificados pela IA e editáveis** — quando o cliente não dá para inferir da conversa, o campo fica vazio para você digitar), **busca por conteúdo** que destaca as ocorrências, e uma **barra de progresso ao vivo** para reuniões ainda transcrevendo/resumindo. A nota é dividida em **blocos colapsáveis** (clique no título da seção): a transcrição completa começa fechada e as tabelas (ex.: **Objetos SAP citados**) viram **tabelas de verdade**, cada uma com seu próprio **⧉ copiar (Excel)** ao lado — TSV colável direto em células. Cópias independentes: **"Copiar p/ Claude"** (só o contexto da atividade, sem a transcrição já resumida) e **"Copiar transcrição"**.

O botão **⚙** abre as **Configurações**, com abas **Gravação / Pastas / Resumo** — configurações agrupadas por categoria, incluindo **atalho de teclado global** para gravar/parar (capture a combinação com o botão "Gravar atalho"); na aba **Resumo**, um editor do `prompt.md`: o markdown com as instruções que geram a ata (seções, regras, vocabulário) — personalize à vontade e restaure o padrão quando quiser.

<p align="center">
  <img src="docs/configuracoes.png" alt="Scriba — Configurações, aba Resumo com o editor do prompt.md" width="640">
</p>

Na primeira execução, o **Assistente de perfil** abre sozinho: você descreve profissão, área, stack e jargão, e o Scriba **escreve as instruções da ata sob medida para o seu trabalho** — geradas por IA (e validadas contra o formato que o leitor de notas espera) ou por um modelo pronto que funciona offline — e ainda preenche as **hotwords** que guiam a transcrição com o seu vocabulário. Tudo com prévia antes de aplicar (o prompt anterior fica em `prompt.md.bak`); reacesse quando quiser pelo link **Assistente de perfil…** da aba Resumo ou por `scriba wizard`.

<p align="center">
  <img src="docs/wizard.png" alt="Scriba — Assistente de perfil (wizard de prompt por profissão)" width="640">
</p>

### Comandos

| Comando | Para quê |
|---|---|
| `scriba run` | app de bandeja: detecção + gravação + processamento |
| `scriba doctor` | diagnóstico do ambiente (`--toast` testa a notificação) |
| `scriba devices` | lista microfones e loopbacks disponíveis |
| `scriba record 60` | gravação manual de N segundos (ex.: reunião fora do Teams) |
| `scriba transcribe <pasta>` | (re)transcreve uma reunião (`--cpu` força CPU) |
| `scriba summarize <pasta>` | (re)gera o resumo e o notas.md |
| `scriba process <pasta>` | tudo de uma vez: transcreve + resume + exporta |
| `scriba detect` | debug: mostra as transições de detecção em tempo real |
| `scriba wizard` | assistente de perfil: gera o prompt.md e as hotwords da sua profissão |
| `scriba autostart on\|off` | atalho na pasta Inicializar do Windows |
| `scriba shortcut` | (re)cria os atalhos na Área de Trabalho e no menu Iniciar |
| `scriba purge` | apaga gravações já transcritas além do prazo de retenção (`--days N` sobrepõe, `--dry-run` só lista) |

Menu da bandeja: **Gravar agora/Parar** (cobre reuniões presenciais ou apps fora da detecção), **Abrir pasta de reuniões/notas**, **Processar pendentes** (retoma o que ficou pela metade se o PC desligou no meio) e **Sair**.

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

O `notas.md` é formatado para ser **usado como contexto numa IA** (Claude Code, etc.): o **Objetivo** classifica a atividade — a call pode ser desenvolvimento, análise/debug de standard, estimativa de esforço, suporte a funcional ou a outro ABAP — e o **Detalhamento** se molda a ela; **Regras de negócio** captura algoritmos e definições funcionais ditos na conversa, e **Pendências** aponta o que ainda não está definido para a IA não presumir. A cópia final vai para `Documentos\Scriba\` — e cada reunião vive numa pasta própria em `C:\temp\scriba\gravacoes\` (árvore `AAAA\MM\DD\`, renomeada com o título da nota), onde a gravação termina **arquivada em Opus 16 kHz mono** (~20 MB/hora em vez de ~1,3 GB de WAV cru — exatamente o formato que o Whisper consome, sem perda para transcrição). Se a gravação vier incompleta (app encerrado no meio da call, microfone que parou), a nota **avisa no topo** a partir de que minuto o áudio se perdeu. Tudo configurável na janela de Configurações, incluindo a **retenção**: gravações já transcritas são apagadas após N dias (padrão 30; a nota final nunca é tocada).

## Configuração

Arquivo criado no primeiro uso em `%LOCALAPPDATA%\Scriba\config.toml`:

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
loopback_device = ""     # vazio = saída padrão; senão, parte do nome (scriba devices)
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
export_dir = ""          # vazio = Documentos\Scriba
recordings_dir = ""      # vazio = C:\temp\scriba\gravacoes (criada automaticamente)
```

O Scriba é focado em pt-BR por padrão, mas `language` e `hotwords` aceitam qualquer idioma/vocabulário suportado pelo Whisper.

### Separar participantes por voz (opcional)

Com a **diarização** ativa, as falas dos outros participantes saem como **Participante 1/2/3** em vez de um "Participantes" único — e o resumo tenta associar cada um ao nome/papel citado na conversa. Roda 100% local (pyannote.audio na GPU/CPU):

1. No venv do Scriba: `pip install torch --index-url https://download.pytorch.org/whl/cu128` (sem GPU NVIDIA: só `pip install torch`) e depois `pip install pyannote.audio`;
2. Crie um token de leitura gratuito em [hf.co/settings/tokens](https://huggingface.co/settings/tokens) e **aceite os termos** nas páginas dos modelos [speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) (o que o pyannote 4.x realmente usa), [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) e [segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0);
3. Na aba **Gravação**: ligue "Separar participantes por voz" e cole o token. O modelo baixa uma vez e o resto é offline.

## Privacidade

- O **áudio nunca sai da sua máquina** — gravação e transcrição são 100% locais.
- Com `[summary] enabled = true`, o **texto** da transcrição é enviado à Anthropic via Claude Code (a mesma conta/assinatura que você já usa). Desabilite para um fluxo 100% offline.
- Gravações e transcrições ficam em `C:\temp\scriba\gravacoes` (configurável), fora de pastas sincronizadas — e são **apagadas automaticamente** após o prazo de retenção (padrão 30 dias; `0` mantém para sempre); config e logs em `%LOCALAPPDATA%\Scriba`. Só o `.md` final vai para Documentos.

## Aviso legal

O Scriba grava **localmente** o áudio que entra e sai da sua máquina, sem avisar os demais participantes (nenhum bot entra na call). As leis sobre gravação de conversas variam por país, e políticas de empresas/clientes podem exigir consentimento explícito. **Verifique as regras do seu contexto antes de usar — a responsabilidade pelo uso é sua.**

## Limitações conhecidas

- Na detecção **em navegador**, a call é confirmada pelo título de uma janela — se você entrar na reunião e trocar de aba no mesmo instante, a gravação começa quando a aba da call voltar a ficar ativa (o mic seguir aberto mantém a espera). A pílula, o atalho e **Gravar agora** continuam cobrindo qualquer caso.
- Se o Teams tocar áudio numa saída diferente da padrão do Windows, configure `loopback_device`.
- Trocar de fone **no meio** da call pode silenciar a trilha dos participantes (reabertura de stream está no roadmap).
- Sem fone (som nas caixas), a fala dos outros entra também pelo microfone e duplica como "Eu".

## Roadmap

- Reabertura de stream ao trocar de dispositivo no meio da call
- Resumo via outros provedores (Ollama local, OpenAI)

## Contribuindo

Issues com bugs e sugestões são muito bem-vindas — rode `scriba doctor` antes de abrir e inclua a saída. Por ora o projeto **não aceita pull requests de código**: o Scriba está em transição para um produto por assinatura, e contribuições externas complicariam o licenciamento. O código segue pequeno e modular — cada responsabilidade em seu arquivo: `detector`, `recorder`, `transcriber`, `diarize`, `merge`, `notes`, `overlay`, `main_window`, `notes_ui`, `settings_ui`, `tray`, `main` — e aberto para leitura e auditoria.

## Licença

O Scriba é **source-available** sob a [Elastic License 2.0](LICENSE) © Allan Martins. O código fica aberto para leitura e auditoria — transparência importa num app que grava as suas reuniões — e o uso é **gratuito durante o beta**, inclusive no trabalho. A licença não permite: oferecer o Scriba a terceiros como produto ou serviço, contornar funcionalidades de chave de licença, nem remover avisos de licença. No go-live, o Scriba passa a ser um produto por assinatura.
