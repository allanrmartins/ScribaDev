# Roadmap do Scriba

> Repo privado — este documento orienta o desenvolvimento rumo ao **produto por assinatura**.
> Regra de ouro: toda feature é desenhada para o ASSINANTE (notebook corporativo sem GPU,
> sem Claude Code, leigo que não edita TOML), não para uso pessoal.
> Beta gratuito → go-live cobrando.

## Modelo de produto (decidido 2026-06)

- **Licença**: source-available (Elastic 2.0), repo privado, sem PRs externos (sem CLA).
- **Planos alvo**: Básico ~R$ 49/mês (resumo com modelo médio, teto ~30 h de reunião/mês) ·
  Pro ~R$ 89–99/mês (resumo Sonnet, ~80 h, diarização, modo 100% local). Anual com desconto.
- **COGS por hora de call** (referência 2026-06): transcrição em nuvem (Groq
  whisper-large-v3-turbo, 2 trilhas) ~US$ 0,08 · resumo US$ 0,01 (modelo médio) a US$ 0,10
  (Sonnet). Usuário com GPU local custa ~zero (margem ~100%).
- **Segredos nunca no app** (Python é inspecionável): chaves de API vivem num backend proxy —
  license key do assinante → backend → OpenRouter/Groq. A Elastic 2.0 já proíbe burlar a chave.
- **Privacidade é feature de venda (LGPD)**: áudio nunca sai da máquina por padrão; qualquer
  envio à nuvem é opt-in explícito, com provedores zero-data-retention.
- **Anti-abuso**: todo endpoint do backend assume usuário hostil — cota por plano (o teto de
  horas é a defesa principal), rate-limit por license key, revogação de chave e alerta de
  anomalia de custo. Limite implementado no app é decorativo (código inspecionável); defesa
  de verdade vive só no servidor. Features 100% locais não têm superfície de abuso.

## Caminho para o go-live (ordem sugerida)

1. ✅ **Wizard de prompt por perfil (onboarding)** — **entregue 2026-06-12** (issue #2):
   `scriba wizard` / aba Resumo / 1º boot. IA gera `prompt.md` + hotwords sob medida
   (validador estrutural garante o contrato com o leitor); 5 templates locais como
   fallback offline; backup `prompt.md.bak`. No produto, a geração por IA migra para o
   backend com cota por license key.
2. **Provider de IA configurável** — `claude` (beta) | `openrouter` (produto, via backend)
   | `ollama` (modo 100% privado). UI na aba Resumo. Hoje é hardcode `claude -p` (Sonnet) em
   DOIS pontos únicos de troca: `notes.generate_summary` (resumo/título/cliente) e
   `promptgen._call_claude` (wizard: gerar prompt + sugerir jargão).
3. **Backend + license key** — edge function (Supabase/Cloudflare): valida assinatura, repassa
   ao OpenRouter/Groq e mede horas/custo por usuário (insumo do billing e dos tetos de plano).
4. **Transcrição em nuvem opt-in** — para notebooks sem GPU (em CPU, 1 h de call ≈ 45–90 min
   de processamento). Groq whisper-large-v3-turbo (mesmo modelo do local, qualidade idêntica).
   VAD local antes do upload corta ~30–50% do custo. Aviso de privacidade explícito.
5. **Instalador / distribuição do beta** — com o repo privado, beta entra por convite
   (collaborator read) ou instalador; no go-live, instalador + license key.
6. **Fragmentação de áudio — fases B/C** — resiliência (rotação a cada 10 min com costura) e
   transcrição em-call (especificadas; fase A — callout de áudio incompleto — já entregue).
7. **Port macOS** — ✅ **código entregue 2026-08-15** (docs/port-mac.md): captura via
   CoreAudio process taps (sem driver), detecção via process objects, STT Metal via
   mlx-whisper, Keychain, LaunchAgent, hotkey Carbon, menu bar template. Pendente:
   permissões TCC no Mac de dev + checklist de call real + empacotamento/.app com
   notarização Apple (US$ 99/ano) para atribuição TCC própria. BlackHole (GPL-3)
   continua banido. Expande o produto além do nicho SAP.
8. **Port Linux** — prioridade menor (PipeWire/PulseAudio; fragmentação de distros).

## Pendências menores

- Reabertura de stream ao trocar de fone no meio da call (limitação conhecida do README).
- Validação da detecção no navegador em call real — issue #1.
