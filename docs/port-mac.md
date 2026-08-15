# Port macOS — épico #104

**Status:** código dos marcos M0–M7 ENTREGUE (2026-08-15) · **Alvo:** macOS 14.2+ / Apple Silicon
**Pendências:** só as permissões TCC (uma vez, manual — ver abaixo) e o checklist de call real.

Regra de ouro aplicada (mesma do `scriba/plat/`): o código Windows ficou byte-idêntico
(há teste de regressão do template da config); o macOS entrou como ramo novo nos seams
existentes. **BlackHole é GPL-3 — nunca embutir nem sugerir.**

## O que foi entregue por marco

- **M0 — baseline (verificado):** `pip install -e .` + importa + `doctor` exit 0 +
  transcreve a fixture; suite completa verde; smoke GUI cocoa (bandeja, lock, socket).
  Ambiente validado: macOS 26/arm64, Python 3.14.5, ffmpeg 8.1.1 (brew).
- **M1 — fundamentos:** tema segue o claro/escuro do macOS (Qt `colorScheme` +
  fallback `defaults read AppleInterfaceStyle`); template da config com textos por SO
  (win32 byte-idêntico — teste); **segredos no Keychain** via `security` (token
  `keychain:<conta>` no TOML; segredo por stdin, nunca argv; roundtrip real validado);
  doctor macOS; job CI `test-macos`; `setup.sh`; pystray/Pillow fora do runtime.
- **M2 — spikes:** ver seção de aprendizados abaixo — tudo GO.
- **M3 — captura:** `mac_tap.py` (process tap global + aggregate PRIVADO; CATapDescription
  via PyObjC, aggregate via ctypes) + `recorder_mac.py` (sounddevice int16 → mesma
  fila/writer/CrashSafeWav; `pick_mic/pick_loopback`, `LevelProbeMac`) atrás dos gates
  de `recorder.py`. meta.json idêntico → pipeline/merge intocados. `scribadev devices`,
  `record`, sonda (`audioprobe` com ramo darwin) e meters funcionam. Gravação E2E
  verificada: mic com áudio real; loopback zerado só por falta da permissão TCC.
- **M4 — detecção automática:** `micusage_mac.py` (process objects do CoreAudio via
  ctypes puro; carimbos sintetizados em FILETIME) alimentando a MESMA máquina de
  estados do `detector.py` (`_iter_mic_keys` despacha; `_browser_key_match` por SO);
  títulos via `mactitles.py` (AX API) atrás do `wintitles.window_titles` de sempre
  (prazo do #114 preservado). **E2E verificado:** started → grace → ended com um
  processo real segurando o mic. `scribadev detect` funciona.
- **M5 — STT Metal:** `stt_mlx.py` (`MlxWhisperProvider`, hotwords→`initial_prompt`,
  fallback runtime p/ faster-whisper CPU) atrás do `make_transcriber` (darwin+arm64
  automático; `engine = "mlx"` força). **Verificado:** fixture transcrita "em metal",
  `whisper_device: metal` no meta.
- **M6 — desktop A:** `_MacNotifier` (osascript, com escaping), autostart via
  LaunchAgent `~/Library/LaunchAgents/dev.scribadev.tray.plist` (`plutil -lint` OK,
  roundtrip verificado), `autostart.label()` nos textos, ícone TEMPLATE da menu bar
  (`scriba_template.png`, `setIsMask`; GRAVANDO continua vermelho — cor é informação).
- **M6.1 — tamanho da interface (Aparência):** o Qt mapeia pontos a 72 dpi no mac
  (vs 96 no Windows) e a UI "encolhia" ~25%. Novo seletor "Tamanho da interface"
  na aba Aparência (state.json `ui_zoom`, por máquina): padrão automático = 133%
  no macOS (paridade visual com o Windows) e 100% nos demais — no Windows padrão
  o tema ativo é o MESMO objeto de sempre (QSS byte-idêntico; há teste). Escala os
  tokens do tema + todos os font-size inline via `theme.zpt()`; aplica a quente
  pelo caminho da troca de tema. QT_FONT_DPI foi descartado: o QPA cocoa ignora.
- **M7 — desktop B:** hotkey global via Carbon `RegisterEventHotKey`
  (`hotkey_mac.py`, ctypes, SEM permissão; "cmd" = alias de "win") bombeada pelo run
  loop Cocoa do Qt; pílula fora do compartilhamento via `NSWindow.sharingType = None`
  (`qt/mac.py` + guard de QPA cocoa em `widgets.exclude_from_capture`). Registro e
  aplicação verificados ao vivo; o disparo sem foco e a invisibilidade num share
  real são checklist manual.

## Permissões TCC (ação manual, UMA vez, no Mac de dev)

Rodando de terminal/venv, o macOS atribui os pedidos ao app do TERMINAL — e nega o
áudio do sistema EM SILÊNCIO (CLI sem `NSAudioCaptureUsageDescription`). Em
**Ajustes → Privacidade e Segurança**:

1. **Gravação de Tela e Áudio do Sistema** → adicionar o app do terminal (ex.: Warp).
   Sem isso o loopback grava zeros (só a sua voz entra na ata). ← ÚNICO bloqueio real
   da captura hoje; toda a cadeia foi validada com referência nativa em Swift.
2. **Microfone** → normalmente o prompt aparece sozinho na 1ª gravação.
3. **Acessibilidade** → habilita títulos de janela: detecção de call NO NAVEGADOR
   e nome da reunião na ata. Sem ela, Teams/Zoom desktop funcionam 100%.

A solução definitiva (prompts atribuídos ao "ScribaDev") é o bundle `.app` — M8.

## Setup num Mac (resumo; `./setup.sh` faz tudo)

```bash
brew install ffmpeg python@3.12   # 3.12–3.14; validado com 3.14
./setup.sh                        # venv em ~/Library/Application Support/ScribaDev/venv
scribadev doctor                  # deve terminar "Tudo pronto"
scribadev run                     # bandeja; autostart: scribadev autostart on
```

Gotcha de config: o 1º boot gera o config.toml completo — EDITE a chave na seção
existente (TOML rejeita tabela duplicada).

## Aprendizados dos spikes (M3 codifica isto — não regredir)

1. PyObjC serve p/ `CATapDescription` + `AudioHardwareCreateProcessTap`;
   `AudioHardwareCreateAggregateDevice` via PyObjC **segfaulta** → ctypes +
   `objc.pyobjc_id(NSDictionary)`.
2. As chaves de composição no PyObjC vêm como **bytes** (`b'uid'`) — decodificar,
   senão o HAL segfaulta.
3. O aggregate precisa do output default como **sub-device (clock)** +
   `MainSubDeviceKey` + drift compensation no tap; só-de-tap = IO errático.
4. Formato do tap: float32 48 kHz estéreo; o PortAudio converte p/ int16 de graça.
5. IOProc direto no objeto do tap: **'!dev'** — aggregate é obrigatório.
6. `sd._terminate()/_initialize()` atualiza a lista do PortAudio após criar o
   aggregate (API semi-privada; validada).
7. Detecção (`prs#`/`piri`) é ctypes puro e NÃO pede TCC; `ctypes` sem `argtypes`
   trunca ponteiros arm64 (segfault) — sempre declarar.
8. Aggregates/taps privados morrem com o processo — sem vazamento pós-crash.
9. Diarização (vale p/ QUALQUER SO com pyannote.audio 4.x): o pipeline
   `speaker-diarization-3.1` exige aceitar os termos de TRÊS repositórios gated no
   Hugging Face — `speaker-diarization-3.1`, `segmentation-3.0` **e**
   `speaker-diarization-community-1` (o 4.x redireciona o `xvec_transform.npz`
   p/ lá) — e, com token fine-grained, a permissão "read gated repos". Validado
   E2E no mac em 2026-08-15 (~20s de áudio → 10s de diarização em CPU).

## Checklist manual (Mac real)

- [x] M0/M1/M3/M4/M5/M6: aceites automatizáveis acima (2026-08-15).
- [ ] Conceder as 3 permissões TCC e:
  - [ ] `scribadev record 30` com música tocando → `loopback.wav` com áudio (peak > 0).
  - [ ] Call real de Teams/Zoom desktop → auto-record dispara, ata com 2 falantes.
  - [ ] Call no navegador (Meet) → detecção confirma pelo título; ata nomeada.
  - [ ] Hotkey (`[ui] hotkey = "cmd+shift+r"` p. ex.) com o app SEM foco.
  - [ ] Compartilhando a tela numa call: a pílula NÃO aparece do outro lado
        (risco conhecido: engines SCK podem ignorar sharingType — se falhar,
        contingência = auto-ocultar a pílula durante o share).
  - [ ] Notificação de teste visível (habilitar "Editor de Scripts" em Notificações).
  - [ ] `autostart on` + re-login → app na bandeja.
  - [ ] Mac em modo claro → app abre claro.
  - [ ] Aparência → "Tamanho da interface": padrão lê "Automático (133% — padrão do
        macOS)" e o texto tem o mesmo tamanho aparente do Windows; trocar o valor
        redimensiona na hora.
- [ ] `./setup.sh` do zero numa máquina limpa.

## Fora de escopo (próximos)

- **M8 — empacotamento:** `.app` (briefcase/py2app) + assinatura + notarização
  (US$ 99/ano). Destrava: prompts TCC atribuídos ao ScribaDev,
  UNUserNotificationCenter com botão "Abrir notas", atalho em /Applications.
  Nada de M1–M7 precisa ser refeito.
- Diarização no MPS (pyannote/torch) — funciona em CPU hoje; acelerar é tuning.
- Linux (PipeWire/PulseAudio) — item 8 do ROADMAP.
