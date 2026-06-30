"""Configuração do ScribaDev: TOML em %LOCALAPPDATA%/ScribaDev/config.toml (criado no 1º uso)."""

from __future__ import annotations

import logging
import shutil
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from . import util

log = logging.getLogger("scriba.config")

# Um config gravado pela metade (kill/queda de energia/antivírus no meio do write)
# chega aqui como TOMLDecodeError; lixo binário, como UnicodeDecodeError.
_DECODE_ERRORS = (tomllib.TOMLDecodeError, UnicodeDecodeError)

DEFAULT_CONFIG = """\
# Configuração do ScribaDev.
# Este arquivo é criado uma única vez com os padrões; edite à vontade.

[detection]
# Apps desktop monitorados: o ScribaDev observa o registro de uso do microfone do
# Windows e considera "call ativa" quando um app cujo nome contenha um destes
# padrões está com o mic aberto.
apps = "teams, zoom"
# Reuniões no navegador (Meet, Teams web, Zoom web...): quando um destes
# navegadores está com o mic aberto E alguma janela dele tem um título de
# reunião (browser_titles, palavra inteira, sem caixa), a call é detectada.
# browsers = "" desliga; browser_titles = "" grava qualquer site usando o mic.
browsers = "chrome, msedge, firefox, brave, opera, vivaldi"
browser_titles = "Meet, Microsoft Teams, Zoom, Webex"
poll_seconds = 2
grace_seconds = 8        # mic liberado por até X s ainda conta como a mesma call
min_call_seconds = 30    # gravações mais curtas são descartadas (pré-join, teste de mic)
max_call_hours = 4       # parada de segurança
auto_record = true       # gravar sozinho ao detectar a call; desligado, a pílula espera o ⏺

[audio]
mic_device = ""          # vazio = microfone padrão do Windows; senão, parte do nome (veja: scribadev devices)
loopback_device = ""     # vazio = saída padrão do Windows; senão, parte do nome (veja: scribadev devices)
keep_audio = true        # manter o áudio depois de transcrever
# Formato do áudio guardado (só com keep_audio=true). Whisper/pyannote usam 16 kHz
# mono, então comprimir não afeta o resultado. opus ~20 MB/h · flac ~110 MB/h · wav = cru.
archive_format = "opus"  # opus | flac | wav
# Apaga a gravação já transcrita (status done) após N dias. 0 = nunca apagar.
# Remove só a pasta em recordings_dir; a nota .md exportada não é tocada.
retention_days = 30

[whisper]
model = "large-v3-turbo"
device = "auto"          # auto | cuda | cpu
language = "pt"
batch_size = 4           # inferência em lote; 0 desliga (turnos mais granulares). 16 picava ~7,6 GB p/ só 1,16x vs 4 (#7)
beam_size = 3            # feixes da decodificação; menor = mais rápido (a lib usa 5)
cpu_threads = 0          # threads quando cai para CPU (0 = automático)
# Filtro de voz (VAD): 0 = padrão da lib. Suba vad_min_silence_ms / vad_threshold só
# após testar com áudio real — pode recuperar voz baixa, mas arrisca alucinar em silêncio.
vad_min_silence_ms = 0   # silêncio mínimo para cortar um trecho (ms); 0 = padrão
vad_threshold = 0        # limiar de detecção de voz (0..1); 0 = padrão
# Vocabulário para guiar a transcrição (troque pelo jargão da sua área):
hotwords = "SAP ABAP BAPI BAdI CDS RAP Fiori OData ALV IDoc SE80 SE11 SE16N SE37 SE38 SM30 SM37 ST22 VA01 ME21N MIGO MARA MATNR VBAK VBAP EKKO BSEG KNA1 SmartForms HANA user exit enhancement request transporte mandante tabela Z campo Z SU01 SU53 PFCG ST01 SAP_ALL"
# Motor de transcrição: local (faster-whisper, 100% na máquina) ou cloud (envia o
# áudio a um endpoint OpenAI-compatível /audio/transcriptions — opt-in explícito).
engine = "local"
cloud_base_url = ""                # vazio = Groq (https://api.groq.com/openai/v1)
cloud_api_key = ""                 # só engine=cloud; chave BYO. Definida pela UI, fica cifrada (DPAPI).
cloud_model = "whisper-large-v3-turbo"

[diarization]
# Separar os participantes remotos por voz ("Participante 1/2/3") — 100% local.
# Requer: pip extra de diarização instalado + token gratuito do Hugging Face
# (hf.co/settings/tokens) com os termos do modelo aceitos na página dele.
enabled = false
hf_token = ""
model = "pyannote/speaker-diarization-3.1"
max_speakers = 0         # 0 = automático
# Ao fim da call, perguntar nº de participantes (vozes remotas) numa janela e
# travar a diarização nesse número — separa muito melhor que o automático.
ask_speakers = true
ask_speakers_timeout = 90   # s sem resposta -> automático (0 = espera indefinida)
# Áudio longo: diariza em blocos de N min p/ não estourar a VRAM (re-liga as vozes
# pelo embedding). Resolve o spill VRAM->RAM em reuniões longas. 0 = sempre inteiro.
chunk_minutes = 3

[summary]
enabled = true           # gerar resumo estruturado da reunião (IA)
# provider de IA: claude (CLI local) | ollama (modelo local, sem chave) | openai (endpoint OpenAI-compatível, com chave)
provider = "claude"
model = "claude-sonnet-4-6"  # provider claude: ou "claude-opus-4-8" (mais capaz, mais caro)
ollama_model = "llama3.1"    # provider ollama (rode `ollama pull <modelo>` antes)
openai_model = "gpt-4o-mini" # provider openai-compatível
# base_url: ollama vazio = http://localhost:11434; openai = endpoint completo (inclua /v1)
base_url = ""
# api_key: só o provider openai; sua chave (BYO). Definida pela UI, fica cifrada (DPAPI do Windows).
api_key = ""
timeout_seconds = 600

[ui]
overlay = true           # pílula flutuante durante a gravação
hotkey = ""              # atalho global gravar/parar (ex.: "ctrl+alt+r"); vazio desativa

[output]
# Pasta para onde o notas.md final é copiado. Vazio = Documentos\\ScribaDev
export_dir = ""
# Pasta das gravações (áudio + transcrição de cada reunião). Vazio = C:\\temp\\scribadev\\gravacoes
recordings_dir = ""
"""


@dataclass(frozen=True)
class Detection:
    apps: str = "teams, zoom"
    registry_key: str = ""  # legado (v0.1): subchave única; use 'apps'
    # Reuniões no navegador: mic aberto num destes processos + título de janela
    # casando browser_titles = call detectada. browsers="" desliga a camada web;
    # browser_titles="" aceita qualquer site com o mic aberto.
    browsers: str = "chrome, msedge, firefox, brave, opera, vivaldi"
    browser_titles: str = "Meet, Microsoft Teams, Zoom, Webex"
    poll_seconds: float = 2.0
    grace_seconds: float = 8.0
    min_call_seconds: float = 30.0
    max_call_hours: float = 4.0
    auto_record: bool = True


@dataclass(frozen=True)
class Audio:
    mic_device: str = ""      # vazio = microfone padrão do Windows; senão, parte do nome
    loopback_device: str = ""
    keep_audio: bool = True
    # Formato do áudio guardado (só vale com keep_audio=true). Whisper e pyannote
    # usam 16 kHz mono, então comprimir não muda a transcrição/diarização.
    # opus = 16 kHz mono ~20 MB/h · flac = lossless ~110 MB/h · wav = cru (~1,3 GB/h).
    archive_format: str = "opus"
    # Apaga a pasta da gravação já transcrita (status=done) após N dias. 0 = nunca.
    # A nota .md exportada (em output.export_dir) nunca é tocada.
    retention_days: int = 30


@dataclass(frozen=True)
class Whisper:
    model: str = "large-v3-turbo"
    device: str = "auto"
    language: str = "pt"
    batch_size: int = 4                # #7: 16 picava ~7,6 GB (só ~1,16x vs 4); 4 ~4,3 GB, folga segura
    beam_size: int = 3                 # #6: feixes da decodificação; menor = mais rápido (lib usa 5)
    cpu_threads: int = 0               # #6: threads no fallback CPU (0 = automático)
    # VAD opt-in (#6): 0 = defaults da lib (sem mudança). >0 recupera voz baixa/pausas.
    vad_min_silence_ms: int = 0        # silêncio mínimo p/ cortar (ms); 0 = default
    vad_threshold: float = 0.0         # limiar de voz do Silero (0..1); 0 = default
    hotwords: str = ""
    engine: str = "local"              # local (faster-whisper) | cloud (Groq/OpenAI-compat STT)
    cloud_base_url: str = ""           # vazio = Groq (https://api.groq.com/openai/v1)
    cloud_api_key: str = ""            # só engine=cloud; chave BYO (cifrada com DPAPI ao gravar)
    cloud_model: str = "whisper-large-v3-turbo"


@dataclass(frozen=True)
class Diarization:
    enabled: bool = False
    hf_token: str = ""
    model: str = "pyannote/speaker-diarization-3.1"
    max_speakers: int = 0
    # Ao fim de cada call, perguntar quantos participantes remotos houve e travar
    # a diarização em num_speakers=N (separa muito melhor que o automático). 0 no
    # timeout = espera indefinida; senão cai no automático após N segundos.
    ask_speakers: bool = True
    ask_speakers_timeout: int = 90
    # Áudio longo: diariza em blocos deste tamanho (min) p/ não estourar a VRAM e
    # escalar p/ reuniões de 1 h; as vozes são re-ligadas pelo embedding. 0 = sempre inteiro.
    chunk_minutes: int = 3


@dataclass(frozen=True)
class Summary:
    enabled: bool = True
    provider: str = "claude"          # claude (CLI) | ollama (HTTP local) | openai (HTTP BYO key)
    model: str = "claude-sonnet-4-6"  # provider claude
    ollama_model: str = "llama3.1"    # provider ollama
    openai_model: str = "gpt-4o-mini"  # provider openai-compativel
    base_url: str = ""                # ollama: vazio=localhost:11434 · openai: obrigatorio (.../v1)
    api_key: str = ""                 # so openai; chave BYO (cifrada com DPAPI ao gravar)
    timeout_seconds: int = 600


@dataclass(frozen=True)
class Ui:
    overlay: bool = True
    hotkey: str = ""


@dataclass(frozen=True)
class Output:
    export_dir: str = ""
    recordings_dir: str = ""

    def resolved_export_dir(self) -> Path:
        if self.export_dir:
            return Path(self.export_dir).expanduser()
        return util.documents_dir() / "ScribaDev"

    def resolved_recordings_dir(self) -> Path:
        """Pasta das gravações, criada na hora se não existir."""
        d = Path(self.recordings_dir).expanduser() if self.recordings_dir else Path(r"C:\temp\scribadev\gravacoes")
        d.mkdir(parents=True, exist_ok=True)
        return d


@dataclass(frozen=True)
class Config:
    detection: Detection
    audio: Audio
    whisper: Whisper
    diarization: Diarization
    summary: Summary
    ui: Ui
    output: Output


def _section(cls, data: dict, name: str):
    raw = data.get(name) or {}
    known = cls.__dataclass_fields__.keys()
    return cls(**{k: v for k, v in raw.items() if k in known})


def _s(v: str) -> str:
    """Serializa string para TOML (literal se possível, senão básica escapada)."""
    if "'" not in v and "\n" not in v:
        return f"'{v}'"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _n(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _b(v: bool) -> str:
    return "true" if v else "false"


# -- segredos (chaves de API) cifrados com DPAPI: nunca em texto plano no disco ----

def _maybe_protect(v: str) -> str:
    """Cifra um segredo plaintext (DPAPI) para gravar; vazio/já-cifrado passa direto.
    Se a DPAPI falhar (não-Windows), grava plaintext — degradação graciosa."""
    if not v or v.startswith(util.DPAPI_PREFIX):
        return v
    return util.dpapi_encrypt(v) or v


def _maybe_unprotect(v: str) -> str:
    """Decifra um 'dpapi:...' ao carregar; plaintext/vazio passa direto. Falha (token
    de outra máquina/usuário) → vazio (chave inutilizável aqui)."""
    if v and v.startswith(util.DPAPI_PREFIX):
        return util.dpapi_decrypt(v) or ""
    return v


def _decrypt_keys(cfg: Config) -> Config:
    """`cfg` com as chaves secretas decifradas (api_key / cloud_api_key / hf_token)."""
    return replace(
        cfg,
        summary=replace(cfg.summary, api_key=_maybe_unprotect(cfg.summary.api_key)),
        whisper=replace(cfg.whisper, cloud_api_key=_maybe_unprotect(cfg.whisper.cloud_api_key)),
        diarization=replace(cfg.diarization, hf_token=_maybe_unprotect(cfg.diarization.hf_token)),
    )


def save(cfg: Config) -> None:
    """Reescreve o config.toml preservando os valores atuais (comentários padrão)."""
    util.ensure_app_dirs()
    d, a, w, s, u, o = cfg.detection, cfg.audio, cfg.whisper, cfg.summary, cfg.ui, cfg.output
    dz = cfg.diarization
    text = f"""\
# Configuração do ScribaDev.
# Edite à vontade — ou use a janela de Configurações (duplo clique no ícone da bandeja).

[detection]
# Apps desktop monitorados (padrões de nome no registro de uso do microfone do Windows).
apps = {_s(d.apps)}
# Reuniões no navegador: mic aberto + título de janela casando browser_titles.
# browsers = "" desliga; browser_titles = "" grava qualquer site usando o mic.
browsers = {_s(d.browsers)}
browser_titles = {_s(d.browser_titles)}
poll_seconds = {_n(d.poll_seconds)}
grace_seconds = {_n(d.grace_seconds)}        # mic liberado por até X s ainda conta como a mesma call
min_call_seconds = {_n(d.min_call_seconds)}    # gravações mais curtas são descartadas (pré-join, teste de mic)
max_call_hours = {_n(d.max_call_hours)}       # parada de segurança
auto_record = {_b(d.auto_record)}       # gravar sozinho ao detectar a call; desligado, a pílula espera o ⏺

[audio]
mic_device = {_s(a.mic_device)}          # vazio = microfone padrão do Windows (veja: scribadev devices)
loopback_device = {_s(a.loopback_device)}     # vazio = saída padrão do Windows (veja: scribadev devices)
keep_audio = {_b(a.keep_audio)}        # manter o áudio depois de transcrever
archive_format = {_s(a.archive_format)}  # opus (~20 MB/h) | flac (~110 MB/h) | wav (cru ~1,3 GB/h)
retention_days = {_n(a.retention_days)}  # apaga gravação transcrita após N dias (0 = nunca)

[whisper]
model = {_s(w.model)}
device = {_s(w.device)}          # auto | cuda | cpu
language = {_s(w.language)}
batch_size = {_n(w.batch_size)}           # inferência em lote (~2x mais rápido); 0 desliga
beam_size = {_n(w.beam_size)}            # feixes da decodificação; menor = mais rápido (a lib usa 5)
cpu_threads = {_n(w.cpu_threads)}          # threads quando cai para CPU (0 = automático)
vad_min_silence_ms = {_n(w.vad_min_silence_ms)}   # silêncio mínimo para cortar (ms); 0 = padrão
vad_threshold = {_n(w.vad_threshold)}        # limiar de detecção de voz (0..1); 0 = padrão
# Vocabulário para guiar a transcrição (troque pelo jargão da sua área):
hotwords = {_s(w.hotwords)}
engine = {_s(w.engine)}          # local | cloud (envia o áudio à nuvem — opt-in)
cloud_base_url = {_s(w.cloud_base_url)}   # vazio = Groq (.../openai/v1)
cloud_api_key = {_s(_maybe_protect(w.cloud_api_key))}     # só engine=cloud; chave BYO, cifrada (DPAPI)
cloud_model = {_s(w.cloud_model)}

[diarization]
# Separar os participantes remotos por voz ("Participante 1/2/3") — 100% local.
# Requer token gratuito do Hugging Face (hf.co/settings/tokens) com os termos
# do modelo aceitos na página dele.
enabled = {_b(dz.enabled)}
hf_token = {_s(_maybe_protect(dz.hf_token))}
model = {_s(dz.model)}
max_speakers = {_n(dz.max_speakers)}         # 0 = automático
# Ao fim da call, perguntar nº de participantes (vozes remotas) e travar a
# diarização nesse número — separa muito melhor que o automático.
ask_speakers = {_b(dz.ask_speakers)}
ask_speakers_timeout = {_n(dz.ask_speakers_timeout)}   # s sem resposta -> automático (0 = espera indefinida)
chunk_minutes = {_n(dz.chunk_minutes)}      # diariza em blocos de N min (0 = inteiro) p/ não estourar a VRAM

[summary]
enabled = {_b(s.enabled)}           # gerar resumo estruturado da reunião (IA)
# provider: claude (CLI local) | ollama (modelo local, sem chave) | openai (OpenAI-compatível, com chave)
provider = {_s(s.provider)}
model = {_s(s.model)}                # provider claude
ollama_model = {_s(s.ollama_model)}  # provider ollama
openai_model = {_s(s.openai_model)}  # provider openai-compatível
base_url = {_s(s.base_url)}           # ollama: vazio=localhost:11434 · openai: endpoint (inclua /v1)
api_key = {_s(_maybe_protect(s.api_key))}             # só openai; chave BYO, cifrada (DPAPI do Windows)
timeout_seconds = {_n(s.timeout_seconds)}

[ui]
overlay = {_b(u.overlay)}           # pílula flutuante durante a gravação
hotkey = {_s(u.hotkey)}              # atalho global gravar/parar (ex.: "ctrl+alt+r"); vazio desativa

[output]
# Pasta para onde o notas.md final é copiado. Vazio = Documentos\\ScribaDev
export_dir = {_s(o.export_dir)}
# Pasta das gravações (áudio + transcrição de cada reunião). Vazio = C:\\temp\\scribadev\\gravacoes
recordings_dir = {_s(o.recordings_dir)}
"""
    # Rede de segurança contra sobrescrita acidental: antes de reescrever, guarda
    # uma cópia do config atual em config.toml.bak (mesma pasta). Falha ao gravar
    # o backup NUNCA pode impedir o save — só registramos/ignoramos.
    if util.CONFIG_PATH.exists():
        try:
            backup = util.CONFIG_PATH.with_name(util.CONFIG_PATH.name + ".bak")
            shutil.copyfile(util.CONFIG_PATH, backup)
        except Exception:
            pass
    util.atomic_write_text(util.CONFIG_PATH, text)


def _build(data: dict) -> Config:
    return _decrypt_keys(Config(
        detection=_section(Detection, data, "detection"),
        audio=_section(Audio, data, "audio"),
        whisper=_section(Whisper, data, "whisper"),
        diarization=_section(Diarization, data, "diarization"),
        summary=_section(Summary, data, "summary"),
        ui=_section(Ui, data, "ui"),
        output=_section(Output, data, "output"),
    ))


def load() -> Config:
    """Lê o config.toml, tolerante a corrupção.

    Se o TOML estiver ilegível (write cortado por kill, queda de energia ou
    antivírus), tenta o backup `config.toml.bak` e, só em último caso, recria os
    padrões. NUNCA deixa a exceção subir e travar a inicialização antes da bandeja
    — para um app em autostart sem supervisão, isso seria perda de config + app
    que não abre.
    """
    util.ensure_app_dirs()
    if not util.CONFIG_PATH.exists():
        util.atomic_write_text(util.CONFIG_PATH, DEFAULT_CONFIG)
    try:
        with open(util.CONFIG_PATH, "rb") as f:
            return _build(tomllib.load(f))
    except _DECODE_ERRORS as e:
        log.error("config.toml ilegível (%s) — tentando o backup .bak", e)

    # 1) backup ANTES de descartar a config do usuário (paths, futura license key)
    backup = util.CONFIG_PATH.with_name(util.CONFIG_PATH.name + ".bak")
    if backup.exists():
        try:
            with open(backup, "rb") as f:
                data = tomllib.load(f)
        except _DECODE_ERRORS as e:
            log.error("backup config.toml.bak também ilegível (%s)", e)
        else:
            log.warning("config.toml restaurado a partir de %s", backup.name)
            try:  # cura o arquivo principal p/ o próximo boot subir limpo
                util.atomic_write_text(util.CONFIG_PATH, backup.read_text(encoding="utf-8"))
            except Exception:
                log.exception("não consegui regravar config.toml a partir do backup")
            return _build(data)

    # 2) último recurso: recriar os padrões — a config anterior foi perdida
    log.warning(
        "config.toml e backup ilegíveis — recriando os padrões; "
        "a configuração anterior do usuário foi perdida"
    )
    util.atomic_write_text(util.CONFIG_PATH, DEFAULT_CONFIG)
    return _build(tomllib.loads(DEFAULT_CONFIG))
