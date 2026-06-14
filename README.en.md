# ScribaDev

> 🎙️ Automatic recording, transcription and minutes for your meetings — Teams, Zoom, Google Meet and browser friends — 100% local and private.

[![License: Elastic 2.0](https://img.shields.io/badge/license-Elastic%202.0-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![Windows 11](https://img.shields.io/badge/platform-Windows%2011-0078d4.svg)

🇧🇷 [Português](README.md) | 🇺🇸 English

> ℹ️ **Personal, local fork** of Scriba, pinned at `b62e901`. It's my daily driver as a dev — it runs entirely on the machine (Whisper on **my GPU** + summary via `claude -p`). **Not a product.** The Python module is still `scriba`, but the Windows identity is **ScribaDev** (folder at `%LOCALAPPDATA%\ScribaDev`, shortcuts and the `scribadev` command) so it coexists with Scriba on the same machine without clashing.

<p align="center">
  <img src="docs/pilula.png" alt="Recording pill" width="300">
  <br><br>
  <img src="docs/janela.png" alt="ScribaDev — main window with service status" width="480">
  <br><br>
  <img src="docs/notas.png" alt="ScribaDev — notes reader with titles" width="780">
</p>

ScribaDev lives in the Windows tray and **detects by itself when you join a Teams or Zoom call — or a meeting in the browser** (Google Meet, Teams web, Zoom web…). It records the audio (no bot ever joins the meeting), transcribes locally with Whisper on your GPU/CPU and produces a Markdown file **with a title, client and structured summary** (participants, decisions, requirements, SAP objects mentioned, action items) plus the **full transcript**, with turns attributed to **Me** (your microphone) and **Participants** (the audio you hear) — or **Participante 1/2/3** with optional diarization. Think of it as a minimalist, local, homemade "Granola" — built for people who feed meeting notes into development tools such as Claude Code.

## How it works

```
Windows registry ──► call detected (mic in use by Teams/Zoom — or by the browser,
        │             confirmed via window title: Meet, Teams web, Zoom web…)
        │
        ▼
records 2 WAV tracks ──► microphone ("Eu") + output loopback ("Participantes")
        │  (floating pill on screen while recording: ● 12:34  ■ ×)
        ▼
call ends ──► local Whisper (faster-whisper large-v3-turbo, GPU or CPU)
        │       the model only loads when the call ends: the GPU stays free during the meeting
        ▼
local pyannote (optional) ──► splits voices into Participante 1/2/3
        │
        ▼
claude -p (optional) ──► meeting title + client + structured summary
        │
        ▼
notas.md ──► exported to Documents\ScribaDev\
        │
        ▼
audio archived as Opus (~20 MB/h) ──► folder renamed with the note's title
```

- **Detection without APIs**: Windows tracks when Teams/Zoom opens the microphone (`HKCU\...\CapabilityAccessManager\ConsentStore\microphone`). ScribaDev just watches that key — any account, no Graph API, no admin rights.
- **Browser meetings**: an open mic in Chrome/Edge/Firefox could be any website, so the call is only confirmed when some browser window bears a meeting title ("Meet", "Microsoft Teams", "Zoom", "Webex" — configurable). Once confirmed, the call stays alive for as long as the mic is open: switching tabs doesn't drop the recording.
- **"Me" vs "Participants" by construction**: your voice comes from the mic and everyone else's from the output loopback — two separate tracks. With optional diarization, remote speakers are further split into **Participante 1/2/3**.
- **Everything local**: audio never leaves your machine. Only the transcribed *text* is sent out (and only if the Claude summary is enabled).

## Requirements

| Item | Notes |
|---|---|
| Windows 11 | uses WASAPI loopback and modern toasts |
| Python 3.12–3.14 | `winget install Python.Python.3.12` |
| Teams/Zoom (desktop) or browser meetings | auto-detection for both; Meet, Teams web and friends are confirmed via the window title |
| NVIDIA GPU *(optional)* | ~10× faster transcription; falls back to CPU automatically |
| [Claude Code](https://claude.com/claude-code) *(optional)* | only for the structured summary; without it you get the plain transcript |
| Headphones *(recommended)* | with speakers, other people's voices leak into your mic and get duplicated as "Me" |

## Install

Installation is **automatic**: `setup.ps1` does everything — creates an isolated Python environment at `%LOCALAPPDATA%\ScribaDev\venv`, installs dependencies (with CUDA support if an NVIDIA GPU is present, ~1.3 GB), downloads the Whisper model (~1.6 GB on first run), puts the `scribadev` command on your PATH and finishes by running `scribadev doctor`, which checks GPU, Teams, audio, model and folders.

**Option A — with git:**

```powershell
git clone https://github.com/allanrmartins/ScribaDev.git
cd ScribaDev
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

**Option B — without git:** on GitHub, click **Code → Download ZIP**, extract it, open a PowerShell in the extracted folder and run `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

### Where files live

| What | Where (default) | Configurable? |
|---|---|---|
| Final notes (`.md`) | `Documents\ScribaDev\` | ✅ Settings window |
| Recordings (one folder per meeting, in a date tree named after the note's title: `2026\06\12\16-34_Boleto não gerado em produção\`) | `C:\temp\scribadev\gravacoes\` | ✅ Settings window |
| Summary prompt (`prompt.md`) | `%LOCALAPPDATA%\ScribaDev\prompt.md` | ✅ Summary tab (built-in editor) |
| Config, logs and Python environment | `%LOCALAPPDATA%\ScribaDev\` | — |

All output folders are **created automatically on first use** — nothing to create by hand on a fresh machine.

## Usage

```powershell
scribadev run            # start monitoring (tray icon)
scribadev autostart on   # optional: start with Windows
```

Join a Teams call (or a Meet in the browser) and the pill shows up at the top of the screen, **staying for the whole call**:

- **● 12:34** — recording, with a timer (drag it anywhere; position is remembered)
- **■** — stop now and process
- **×** — discard this recording (calls that shouldn't become notes)
- **⏺ Record** — idle mode: shown when auto-record is off, or after you stop/discard mid-call; one click starts a (new) recording

The pill only disappears when the call ends. With `auto_record` off (Settings), nothing is recorded until you click ⏺. When you leave the call: a "Transcribing…" toast, then "Notes ready" with a button that opens the `.md`.

**Double-click the tray icon** to open the **main window**: status of every service (detection, audio, Whisper/GPU, Claude, diarization, autostart), the **live call with duration** and the **⏺ Record** button. Minimizing keeps the app in the taskbar; closing (X) removes it from the taskbar while monitoring continues in the tray.

The **Notas** button opens the **Notes window**: a built-in reader for the generated notes (rendered markdown, grouped by day, with **AI-identified, editable title and client** — when the client can't be inferred from the conversation, the field is left blank for you to type), **content search** that highlights matches, and a **live progress bar** for meetings still transcribing/summarizing. Notes are split into **collapsible blocks** (click a section title): the full transcript starts collapsed and tables (e.g. **SAP objects**) render as **real tables**, each with its own **⧉ copy (Excel)** button — TSV that pastes straight into cells. Independent copy actions: **"Copiar p/ Claude"** (just the activity context — the transcript is already summarized) and **"Copiar transcrição"**.

The **⚙** button opens **Settings**, with **Gravação / Pastas / Resumo** tabs — settings grouped by category, including a **global record/stop hotkey** (capture the combo with the "Gravar atalho" button); in the **Resumo** tab, a `prompt.md` editor: the markdown instructions that drive the minutes (sections, rules, vocabulary) — customize freely, restore the default anytime.

<p align="center">
  <img src="docs/configuracoes.png" alt="ScribaDev — Settings, Resumo tab with the prompt.md editor" width="640">
</p>

On first run, the **profile assistant** opens by itself: describe your role, area, stack and jargon, and ScribaDev **writes the meeting-notes instructions tailored to your work** — AI-generated (and validated against the structure the notes reader expects) or from an offline-ready template — and also fills the **hotwords** that guide transcription with your vocabulary. Everything is previewed before applying (the previous prompt is kept as `prompt.md.bak`); reopen it anytime via the **Assistente de perfil…** link in the Resumo tab or with `scribadev wizard`.

<p align="center">
  <img src="docs/wizard.png" alt="ScribaDev — profile assistant (prompt wizard by role)" width="640">
</p>

### Commands

| Command | Purpose |
|---|---|
| `scribadev run` | tray app: detection + recording + processing |
| `scribadev doctor` | environment diagnostic (`--toast` tests notifications) |
| `scribadev devices` | list available microphones and loopbacks |
| `scribadev record 60` | manual N-second recording (e.g. meetings outside Teams) |
| `scribadev transcribe <folder>` | (re)transcribe a meeting (`--cpu` forces CPU) |
| `scribadev summarize <folder>` | (re)generate the summary and notas.md |
| `scribadev process <folder>` | everything at once: transcribe + summarize + export |
| `scribadev detect` | debug: print detection state transitions live |
| `scribadev wizard` | profile assistant: generates prompt.md and hotwords for your role |
| `scribadev autostart on\|off` | shortcut in the Windows Startup folder |
| `scribadev shortcut` | (re)create the Desktop and Start Menu shortcuts |
| `scribadev purge` | delete already-transcribed recordings past the retention window (`--days N` overrides, `--dry-run` lists only) |

Tray menu: **Record now/Stop** (covers in-person meetings and apps outside detection), **Open meetings/notes folder**, **Process pending** (resumes work interrupted by a shutdown) and **Quit**.

## Output (`notas.md`)

```markdown
---
titulo: Relatório ALV de materiais ZMM001     ← AI-generated, editable in the app
cliente: Aurora                               ← identified from the conversation; blank = type it in the app
data: 2026-06-10T14:30:12
duracao_minutos: 47
origem: scriba
whisper: large-v3-turbo (cuda)
---

# Relatório ALV de materiais ZMM001

*2026-06-10 14:30 · 47 min · Cliente: Aurora*

> **Context for AI:** technical record of the meeting — perform the activity stated in Objetivo.

## Objetivo                      ← **Tipo:** development | analysis/debug | estimate | support… + what to do
## Contexto                      ← SAP module, system/release, environment
## Detalhamento                  ← shaped by the activity: functional spec, debug roadmap, estimation inputs…
### Critérios de aceite          ← verifiable `- [ ]` checklist
## Regras de negócio             ← RN-01, RN-02… business rules and algorithms spoken in the call
## Objetos SAP citados           ← table: Type | Object | Note | When
## Decisões                      ← self-contained, with [HH:MM:SS] timestamps
## Pendências e Ações            ← what's NOT defined yet (gaps for the AI to flag)
## Participantes                 ← with diarization: "Participante 1 — João, functional"

---

## Transcrição completa

**[00:00:12] Participante 1:** Bom dia! Precisamos de um relatório na ZMM001...
**[00:00:45] Eu:** Entendi. O campo MATNR vem da MARA?
```

`notas.md` is formatted to be **used as context in an AI** (Claude Code, etc.): **Objetivo** classifies the activity — a call can be development, standard-code debugging, effort estimation, or helping a functional analyst or another ABAPer — and **Detalhamento** adapts to it; **Regras de negócio** captures business rules and algorithms spoken in the conversation, and **Pendências** points out what's still undefined so the AI flags it instead of assuming. The final copy goes to `Documents\ScribaDev\` — and each meeting lives in its own folder under `C:\temp\scribadev\gravacoes\` (a `YYYY\MM\DD\` tree, renamed with the note's title), where the recording ends up **archived as 16 kHz mono Opus** (~20 MB/hour instead of ~1.3 GB of raw WAV — exactly the format Whisper consumes, lossless for transcription purposes). If a recording comes out incomplete (app killed mid-call, a mic stream that died), the note **warns at the top** from which minute the audio was lost. All configurable in the Settings window, including **retention**: already-transcribed recordings are deleted after N days (default 30; the final note is never touched).

## Configuration

Created on first run at `%LOCALAPPDATA%\ScribaDev\config.toml`:

```toml
[detection]
apps = "teams, zoom"     # monitored desktop apps in the Windows mic-usage registry
# Browser meetings: mic open in one of these processes + a window title matching
# browser_titles = call detected. browsers="" disables the web layer;
# browser_titles="" records any website using the mic.
browsers = "chrome, msedge, firefox, brave, opera, vivaldi"
browser_titles = "Meet, Microsoft Teams, Zoom, Webex"
min_call_seconds = 30    # shorter recordings are ignored (pre-join screen, mic test)
grace_seconds = 8        # mic released for up to X s still counts as the same call
auto_record = true       # record on call detection; when off, the pill waits for ⏺

[audio]
loopback_device = ""     # empty = default output; otherwise part of the name (scribadev devices)
keep_audio = true        # keep the meeting audio after transcribing
archive_format = "opus"  # opus (~20 MB/h) | flac (lossless ~110 MB/h) | wav (raw ~1.3 GB/h)
retention_days = 30      # delete already-transcribed recordings after N days (0 = never)

[whisper]
model = "large-v3-turbo" # any faster-whisper model
device = "auto"          # auto | cuda | cpu
language = "pt"
batch_size = 8           # batched inference (~2x faster); 0 disables
hotwords = "SAP ABAP BAPI BAdI CDS SE16N MARA ..."  # vocabulary that guides transcription

[diarization]
enabled = false          # Participante 1/2/3 by voice (see section below)
hf_token = ""            # Hugging Face read token

[summary]
enabled = true           # summary via Claude Code CLI (claude -p)
model = "claude-sonnet-4-6"  # or claude-opus-4-8 (dropdown in the Summary tab)

[ui]
overlay = true           # floating pill
hotkey = "ctrl+alt+r"    # global record/stop hotkey; empty disables

[output]
export_dir = ""          # empty = Documents\ScribaDev
recordings_dir = ""      # empty = C:\temp\scribadev\gravacoes (created automatically)
```

ScribaDev defaults to Brazilian Portuguese, but `language`, `hotwords` and the summary prompt work with anything Whisper supports.

### Speaker separation (optional)

With **diarization** on, the other participants come out as **Participante 1/2/3** instead of a single "Participantes" — and the summary tries to map each one to names/roles mentioned in the conversation. Runs 100% locally (pyannote.audio on GPU/CPU):

1. In ScribaDev's venv: `pip install torch --index-url https://download.pytorch.org/whl/cu128` (no NVIDIA GPU: just `pip install torch`) then `pip install pyannote.audio`;
2. Create a free read token at [hf.co/settings/tokens](https://huggingface.co/settings/tokens) and **accept the terms** on [speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) (what pyannote 4.x actually uses), [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) and [segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0);
3. In the **Gravação** tab: enable "Separar participantes por voz" and paste the token. The model downloads once; everything else is offline.

## Privacy

- **Audio never leaves your machine** — recording and transcription are 100% local.
- With `[summary] enabled = true`, the transcript **text** is sent to Anthropic through Claude Code (the account/subscription you already use). Disable it for a fully offline flow.
- Recordings and transcripts live in `C:\temp\scribadev\gravacoes` (configurable), away from synced folders — and are **deleted automatically** after the retention window (default 30 days; `0` keeps forever); config and logs in `%LOCALAPPDATA%\ScribaDev`. Only the final `.md` goes to Documents.

## Legal notice

ScribaDev records **locally** the audio entering and leaving your machine, without notifying the other participants (no bot joins the call). Call-recording laws vary by country, and company/client policies may require explicit consent. **Check the rules that apply to you before using it — you are responsible for how you use this tool.**

## Known limitations

- **Browser** detection confirms the call via a window title — if you join a meeting and switch tabs in the same instant, recording starts once the call tab becomes active again (the wait holds as long as the mic stays open). The pill, hotkey and **Record now** still cover every case.
- If Teams plays audio on a non-default output device, set `loopback_device`.
- Swapping headsets **mid-call** can silence the participants' track (stream reopen is on the roadmap).
- Without headphones, other people's voices also enter your mic and get duplicated as "Me".

## Roadmap

- Stream reopen on mid-call device switch

## Development

Personal fork (private repo), no external contribution model. The codebase stays small and modular — one responsibility per file: `detector`, `recorder`, `transcriber`, `diarize`, `merge`, `notes`, `overlay`, `main_window`, `notes_ui`, `settings_ui`, `tray`, `main`. Before hacking: `scribadev doctor` (environment check) and `python -m unittest discover -s tests` (stdlib unittest suite, no pytest).

## License

ScribaDev is a **personal** fork of Scriba, under the [Elastic License 2.0](LICENSE) © Allan Martins (inherited from upstream), for personal use. See the [LICENSE](LICENSE) file for terms.
