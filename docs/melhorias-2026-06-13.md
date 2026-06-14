# Relatório do Minerador de Melhorias — Rodada 2026-06-13

**Data:** 2026-06-13  
**Hora da rodada:** 14:05 UTC  
**Tag da rodada:** run-2026-06-13-1905  
**Modo:** PRODUÇÃO (issues criadas)  
**Postura:** completo  
**MinerProfundo:** ativado (Opus)  
**Dry run:** NÃO (issues foram criadas)  
**Total de rascunhos aprovados:** 5

---

## Rascunhos Aprovados (por prioridade)

### Priority 1

#### Escrita atômica do config.toml + fallback de leitura tolerante a corrupção

**Labels:** melhoria, enhancement, auto-triagem, bug  

A campanha de escrita atômica (#3 e #7) cobriu `transcript.json`, `meta.json` e `notas.md`, mas o `config.toml` — que guarda TODA a config do usuário — ficou de fora. Dois problemas confirmados:

1. `.bak` morto: backup em config.py:251 mas nunca restaurado
2. Leitura sem try/except: TOML truncado derruba o app inteiro

Proposta: Trocar dois `write_text` por `util.atomic_write_text` e envolver `tomllib.load` em try/except com fallback: (1) tentar `.bak`, (2) recriar DEFAULT_CONFIG com aviso.

---

#### Validar posição da pílula flutuante contra monitores ativos

**Labels:** melhoria, enhancement, auto-triagem, bug  

Pílula salva posição em state.json sem clamp. Se monitor desconectar, coordenadas caem fora da tela e pílula fica invisível durante gravação real.

Proposta: Clamp em overlay.show() contra bounds do monitor primário + troca de `_save_pos` para atomic_write_text.

---

### Priority 2

#### Tornar split_header tolerante a preâmbulo do modelo

**Labels:** melhoria, enhancement, auto-triagem, bug  

`split_header` só funciona se TITULO:/CLIENTE: são exatamente nas 2 primeiras linhas. Modelos às vezes emitem linha em branco, ```markdown, "Aqui está:", etc. que quebram o parser.

Proposta: Varrer ~5 linhas não-vazias procurando headers, ignorando em branco e cercas de código.

---

#### Guard por .lock no re-enfileiramento de pendentes

**Labels:** melhoria, enhancement, auto-triagem, bug  

`scan_pending()` re-adota pastas sem verificar `.lock` ativo. Colisão ocorre quando usuário roda `scriba process <pasta>` manualmente enquanto worker já processa.

Proposta: Extrair `_is_locked` para `util` e adicionar guards em scan_pending e process_folder.

---

### Priority 3

#### Marcar audio_removed no meta ao apagar WAVs (keep_audio=false)

**Labels:** melhoria, enhancement, auto-triagem, bug  

Em `archive_audio`, ramo `keep_audio=false` apaga WAVs sem atualizar meta.json. Depois, `scriba transcribe` marca status='no_audio' enganosamente.

Proposta: Marcar `audio_removed: true` no meta após apagar WAVs, e em `transcribe_folder` distinguir de stream sem áudio.

---

#### Expor detecção e hotwords na UI de Configurações

**Labels:** melhoria, enhancement, auto-triagem  

settings_ui.py não expõe detection.apps/browsers/titles nem whisper.hotwords. Usuários com Webex/Slack/navegador fora da lista precisam editar config.toml à mão.

Proposta: Nova aba "Detecção" com CSV apps/browsers/titles + presets (Teams, Zoom, Meet, Webex, Slack) + campo hotwords em Resumo.

---

## Concorrentes (varredura)

1. **Notas ao vivo durante a call que se fundem com transcrição** (Granola Enhance style) — painel rápido com timestamp na pílula flutuante
2. **Chat/perguntas em linguagem natural sobre acervo** — RAG local multi-reunião com citações
3. **Templates/receitas de resumo por tipo** — múltiplos prompts + e-mail follow-up
4. **Player de áudio com destaques** — sincronizado com transcrição, export clips
5. **Itens de ação rastreáveis (checklist)** — export para Excel/.ics
6. **Integração com calendário** — pre-fill título/cliente/participantes

---

## Rejeitados

- **Live notes + enhance:** entraria em conflito com refactor item 2 (provider plugável); deve esperar
- **Wizard + detecção proativa:** wizard já entregue (ROADMAP), gap real é faixa estreita em main_window; não criar issue com premissa errada
- **Checklist / Chat acervo:** reprovados no painel

---

## Preçificação (foco Brasil)

Concorrentes praticamente ignoram LATAM. Scriba diferencia por: local-first (zero custo GPU), privacidade total (NDA-safe), processamento offline.

**Recomendação Scriba:**
- **Free:** 10 reuniões/mês, sem IA (gancho hábito)
- **Solo:** USD 7/mês anual (USD 84/ano) — metade do Granola, 1% do salário BR (~R$37/mês)
- **Team:** USD 12/usuário/mês anual (mín 3) — workspace compartilhado, lower LTV risk de LATAM

Argumento: local = zero GPU cost = sustentabilidade de preço 40-60% abaixo do mercado.

---

**Fim do relatório (2026-06-13 14:05 UTC)**
