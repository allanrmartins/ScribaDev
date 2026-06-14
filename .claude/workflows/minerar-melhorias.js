export const meta = {
  name: 'minerar-melhorias',
  description: 'Minera melhorias (codigo interno + concorrentes), valida vs rubrica SaaS e abre issues no GitHub',
  phases: [
    { title: 'Context' },
    { title: 'Gerar' },
    { title: 'Precos' },
    { title: 'Dedup' },
    { title: 'Validar' },
    { title: 'Arbitrar', model: 'opus' },
    { title: 'Sintetizar', model: 'opus' },
    { title: 'Criar' },
  ],
}

// ---------------------------------------------------------------- args/config --
const A = args || {}
const posture = A.posture ?? 'completo'
const competitors = A.competitors ?? ['Granola', 'Fathom', 'tl;dv']
const dryRun = A.dryRun ?? false
const maxCandidates = A.maxCandidates ?? 10
const doPricing = A.pricing ?? true   // pesquisa de precificacao competitiva (foco Brasil)

// --- ESFORCO POR AGENTE = tier de modelo (a API agent() nao expoe reasoning-effort por chamada; #25669) ---
// Regras: (1) NENHUMA analise em haiku — haiku so na criacao da issue/relatorio (passo 'criar').
//         (2) sonnet e o PISO de qualquer analise.
//         (3) opus posicionado nos pontos de maior raciocinio: TODA a geracao (mineradores + web da fase
//             'Gerar'), dedup semantico, sintese e arbitragem.
// ESFORCO DE RACIOCINIO desejado (intencao): opus -> max, sonnet -> medium, haiku -> low.
//   ATENCAO: NAO da pra aplicar reasoning-effort POR AGENTE hoje (a API agent() ignora; #25669). O unico
//   controle real e o /effort da SESSAO (afeta TODOS os agentes juntos). Para rodar a geracao opus em
//   "max effort", acione /effort xhigh (ou ultracode) antes da rodada — os agentes sonnet sobem junto,
//   nao da pra deixar so eles em medium enquanto os opus ficam em max.
// Ajuste o esforco de cada agente AQUI (uma fonte unica), por posture.
const EFFORT = {
  completo: { context:'sonnet', miner:'opus',   web:'opus',   pricing:'sonnet', dedup:'opus',   voto:'sonnet', arbitro:'opus', sintese:'opus',   criar:'haiku' },
  enxuto:   { context:'sonnet', miner:'sonnet', web:'sonnet', pricing:'sonnet', dedup:'sonnet', voto:'sonnet', arbitro:'opus', sintese:'sonnet', criar:'haiku' },
}
const EFF = EFFORT[posture] || EFFORT.completo
// minerProfundo forca os mineradores em opus mesmo no 'enxuto'; default segue o posture.
const minerProfundo = A.minerProfundo ?? (posture === 'completo')
const minerModel = minerProfundo ? 'opus' : EFF.miner
// painel: 3 votos no "completo", 1 no "enxuto"; cai p/ 1 se houver teto de budget apertado
const tight = Boolean(budget.total && budget.total < 200000)
const VOTERS = posture === 'completo' && !tight ? 3 : 1
const REPO = 'C:/Users/User/OneDrive/Documents/SandBox/scriba'
const SLUG = 'allanrmartins/Scriba'

log(`minerar-melhorias · posture=${posture} · miners=${minerModel} · votos=${VOTERS} · dryRun=${dryRun} · concorrentes=${competitors.join(', ')}`)

// ----------------------------------------------------------------- schemas --
const CAND_ITEM = {
  type: 'object',
  required: ['title', 'problem', 'proposal', 'source', 'area'],
  properties: {
    title: { type: 'string' },
    problem: { type: 'string' },
    proposal: { type: 'string' },
    source: { type: 'string', enum: ['interno', 'concorrente'] },
    evidence: { type: 'array', items: { type: 'string' } },
    area: { type: 'string' },
  },
}
const CANDS = { type: 'object', required: ['candidates'], properties: { candidates: { type: 'array', items: CAND_ITEM } } }
const CTX = {
  type: 'object',
  required: ['existingIssues', 'roadmapItems', 'features'],
  properties: {
    existingIssues: { type: 'array', items: { type: 'string' } },
    roadmapItems: { type: 'array', items: { type: 'string' } },
    features: { type: 'array', items: { type: 'string' } },
  },
}
const VERDICT = {
  type: 'object',
  required: ['verdict', 'rationale'],
  properties: {
    verdict: { type: 'string', enum: ['build', 'maybe', 'skip'] },
    scores: {
      type: 'object',
      properties: {
        impacto: { type: 'integer' }, esforco: { type: 'integer' },
        cogs: { type: 'integer' }, privacidade: { type: 'integer' }, fit_roadmap: { type: 'integer' },
      },
    },
    rationale: { type: 'string' },
  },
}
const DRAFTS = {
  type: 'object',
  required: ['drafts'],
  properties: {
    drafts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['title', 'body_md', 'labels', 'priority'],
        properties: {
          title: { type: 'string' },
          body_md: { type: 'string' },
          labels: { type: 'array', items: { type: 'string' } },
          priority: { type: 'integer' },
        },
      },
    },
  },
}

const PRICING = {
  type: 'object',
  required: ['competitors', 'recomendacao_scriba'],
  properties: {
    competitors: {
      type: 'array',
      items: {
        type: 'object',
        required: ['nome', 'planos'],
        properties: {
          nome: { type: 'string' },
          planos: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                plano: { type: 'string' }, preco_usd: { type: 'string' },
                periodo: { type: 'string' }, limites: { type: 'string' },
              },
            },
          },
          free_tier: { type: 'string' },
          fonte: { type: 'string' },
        },
      },
    },
    mercado_brasil: { type: 'string' },
    recomendacao_scriba: {
      type: 'object',
      required: ['modelo', 'planos_sugeridos'],
      properties: {
        modelo: { type: 'string' },
        planos_sugeridos: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              plano: { type: 'string' }, preco_usd: { type: 'string' }, publico: { type: 'string' },
            },
          },
        },
        justificativa: { type: 'string' },
      },
    },
  },
}

const RUBRICA = `RUBRICA DE PRODUTO (Scriba, SaaS por assinatura — de docs/ROADMAP.md):
- Serve ao ASSINANTE? (notebook corporativo, em geral SEM GPU, SEM Claude Code local, leigo que nao edita TOML).
- COGS por hora aceitavel? (transcricao/resumo na nuvem custam; usuario com GPU local custa ~zero).
- Segredo NUNCA no app (Python e inspecionavel): chaves de API vivem num backend proxy.
- Privacidade e feature de venda (LGPD): audio nunca sai por padrao; qualquer envio a nuvem e opt-in, zero-retention.
- Fit com a ordem do go-live: provider IA configuravel -> backend+license key -> transcricao nuvem (Groq) -> instalador -> fragmentacao audio -> macOS -> Linux.
- Impacto x esforco (preferir item claro e pequeno a refatoracao gigante).`

// ------------------------------------------------------------- Fase 0: contexto --
phase('Context')
const ctx = await agent(
  `Voce e o coletor de contexto do projeto Scriba (repo local em ${REPO}). SOMENTE leitura.
Reuna o estado atual para deduplicacao posterior:
1. Issues existentes: rode \`gh issue list --repo ${SLUG} --state all --limit 100 --json number,title,state\` e devolva cada uma como "#<num> <title> (<state>)".
2. Itens do roadmap: leia ${REPO}/docs/ROADMAP.md e liste os itens/fases planejados (um titulo curto cada).
3. Funcionalidades atuais: a partir de ${REPO}/README.md, liste as features que o Scriba JA tem (um titulo curto cada).
Retorne o objeto do schema.`,
  { model: EFF.context, schema: CTX, label: 'context-pack' }
)
log(`contexto: ${ctx?.existingIssues?.length || 0} issues, ${ctx?.roadmapItems?.length || 0} roadmap, ${ctx?.features?.length || 0} features`)
const ctxBlock = `JA EXISTE (NAO reproponha nada coberto por isto):
- Issues: ${(ctx?.existingIssues || []).join(' | ') || '(nenhuma)'}
- Roadmap: ${(ctx?.roadmapItems || []).join(' | ') || '(vazio)'}
- Features atuais: ${(ctx?.features || []).join(' | ') || '(vazio)'}`

// ------------------------------------------------------------- Fase 1: geracao --
phase('Gerar')
const found = await parallel([
  () => agent(
    `Voce e um revisor senior cacando MELHORIAS e FRAGILIDADES reais no codigo do Scriba (Python, app desktop Windows de gravacao/transcricao de reunioes).
Area: PIPELINE / GRAVACAO / TRANSCRICAO / RESUMO. Leia o codigo em ${REPO}/scriba (foque em recorder.py, pipeline.py, transcriber.py, diarize.py, merge.py, notes.py, promptgen.py, retention.py, config.py).
Procure: bugs latentes, perda de dados, erros nao tratados, fragilidades de robustez (ex.: reescrever arquivo sem backup), gargalos e melhorias que ajudem um PRODUTO PAGO. Cada achado vira um candidato com evidence = referencias de arquivo no formato caminho:linha. source='interno'.
${ctxBlock}
Gere ate 6 candidatos de alta qualidade (qualidade > quantidade). Retorne o schema.`,
    { model: minerModel, schema: CANDS, label: 'miner:pipeline', phase: 'Gerar' }
  ),
  () => agent(
    `Voce e um revisor senior cacando MELHORIAS e FRAGILIDADES reais no codigo do Scriba (Python, app desktop Windows).
Area: UI / EMPACOTAMENTO / PRODUTO / DETECCAO. Leia o codigo em ${REPO}/scriba (foque em settings_ui.py, wizard_ui.py, notes_ui.py, main_window.py, tray.py, overlay.py, detector.py, autostart.py, hotkey.py, cli.py).
Procure: bugs de UI, estados inconsistentes, perda de config ao salvar, lacunas de UX para leigos, problemas de empacotamento/instalacao/onboarding, e melhorias que ajudem um PRODUTO PAGO. Cada achado vira candidato com evidence = caminho:linha. source='interno'.
${ctxBlock}
Gere ate 6 candidatos de alta qualidade. Retorne o schema.`,
    { model: minerModel, schema: CANDS, label: 'miner:ui', phase: 'Gerar' }
  ),
  () => agent(
    `Voce pesquisa CONCORRENTES do Scriba na web para achar funcionalidades que valeria a pena ter. O Scriba grava e transcreve reunioes LOCALMENTE e de forma privada (estilo Granola), no Windows, com Whisper local e resumo por IA, virando um SaaS por assinatura.
Concorrentes-alvo: ${competitors.join(', ')}. Use WebSearch (e WebFetch quando o dominio permitir) para levantar as features de destaque de cada um (pagina de produto, changelog, comparativos recentes). Para cada feature relevante que o Scriba NAO tem e que faca sentido no posicionamento dele, gere um candidato (source='concorrente', evidence = URLs).
${ctxBlock}
Gere ate 6 candidatos, os mais relevantes. Retorne o schema.`,
    { model: EFF.web, schema: CANDS, label: 'web:concorrentes', phase: 'Gerar' }
  ),
])
const candidates = found.filter(Boolean).flatMap(r => r.candidates || [])
const competitorScan = candidates.filter(c => c && c.source === 'concorrente')   // varredura crua de concorrentes (p/ relatorio, mesmo se cortada depois)
log(`candidatos brutos: ${candidates.length} (${competitorScan.length} de concorrentes)`)

// --------------------------------------------------- Fase 1b: precos (Brasil) --
phase('Precos')
let pricing = null
if (doPricing) {
  pricing = await agent(
    `Voce e analista de pricing e go-to-market do Scriba — gravador/transcritor de reunioes LOCAL e privado (estilo Granola), virando SaaS por assinatura. FOCO DE MERCADO: BRASIL. Publico-alvo: profissionais brasileiros que vivem em reuniao — consultores e devs SAP/ABAP, analistas de CRM (Salesforce, Dynamics...), analistas funcionais, PMs e EMPRESAS — um mercado POUCO PENETRADO no Brasil (muita gente/empresa ainda nao usa gravador de reuniao). A precificacao no app sera em DOLAR (USD), mas o publico e brasileiro (sensibilidade a preco diferente de US/EU).

Pesquise na web (WebSearch; WebFetch quando o dominio permitir) quanto cobram os concorrentes: ${competitors.join(', ')}, e tambem Otter.ai, Fireflies.ai, Fathom, tl;dv, Granola e Fellow. Para CADA um levante: planos, preco em USD, periodo (mes/ano), free tier e limites (horas/minutos). Cite a URL da fonte do preco.

Depois:
1. Analise o MERCADO BRASILEIRO: disposicao a pagar do profissional e da empresa BR, e o gap de adocao (por que poucos usam e a oportunidade).
2. RECOMENDE um modelo de preco em USD MUITO ATRATIVO para o publico brasileiro — planos sugeridos (nome, preco_usd, publico) e a justificativa de por que e competitivo frente aos concorrentes E cabe no bolso BR (ancore nos diferenciais do Scriba: 100% local/privado, sem bot na call, custo ~zero com GPU local).
Retorne o schema.`,
    { model: EFF.pricing, schema: PRICING, label: 'pricing:brasil' }
  )
  log(`pricing: ${pricing?.competitors?.length || 0} concorrentes analisados`)
}

// ------------------------------------------------------------- Fase 2: dedup --
phase('Dedup')
const deduped = candidates.length
  ? await agent(
      `Voce deduplica e filtra candidatos de melhoria do Scriba. Candidatos brutos (JSON):
${JSON.stringify(candidates)}

CONTEXTO do que JA existe:
${ctxBlock}

Tarefas:
1. Funda candidatos que sao a MESMA ideia (mesmo vindos de fontes diferentes) num so, preservando a melhor evidence.
2. DESCARTE qualquer candidato ja coberto por uma issue existente OU por um item do roadmap — julgue por SEMANTICA, nao por texto identico (ex.: "deixar o usuario escolher o modelo de resumo" ja esta coberto por "provider de IA configuravel").
3. Devolva os candidatos restantes ordenados do mais valioso ao menos, no maximo ${maxCandidates}.
Retorne o schema.`,
      { model: EFF.dedup, schema: CANDS, label: 'dedup' }
    )
  : { candidates: [] }
const fresh = (deduped?.candidates || []).slice(0, maxCandidates)
log(`apos dedup: ${fresh.length} candidatos`)

// ----------------------------------------------- Fase 3+3b: painel + arbitro --
phase('Validar')
const LENTES = [
  'impacto para o assinante e fit com a ordem do go-live',
  'custo/COGS, privacidade (LGPD) e risco de implementacao',
  'esforco x retorno e maturidade da ideia',
]
const judged = await pipeline(
  fresh,
  (c, _item, idx) =>
    parallel(
      Array.from({ length: VOTERS }, (_, i) => () =>
        agent(
          `Voce e um validador de produto do Scriba. Avalie se ESTE candidato vale a pena implementar, contra a rubrica.
${RUBRICA}

CANDIDATO:
${JSON.stringify(c)}

Lente desta avaliacao: ${LENTES[i % LENTES.length]}.
De um verdict (build/maybe/skip), notas 1-5 em cada criterio (impacto, esforco, cogs, privacidade, fit_roadmap) e uma justificativa curta. Retorne o schema.`,
          { model: EFF.voto, schema: VERDICT, label: `voto:${idx}.${i}`, phase: 'Validar' }
        )
      )
    ).then(votes => {
      const v = votes.filter(Boolean)
      const yes = v.filter(x => x.verdict === 'build').length
      return { c, votes: v, panelBuild: yes > VOTERS / 2, split: yes > 0 && yes <= VOTERS / 2 }
    }),
  // arbitro Opus: so revisa o que o painel aprovou OU empatou (o gate antes da issue)
  (x, _item, idx) =>
    x && (x.panelBuild || x.split)
      ? agent(
          `Voce e o ARBITRO final (criterioso) do Scriba. O painel avaliou este candidato e ficou ${x.panelBuild ? 'majoritariamente A FAVOR' : 'DIVIDIDO'}. Decida com cautela: um 'build' aqui vira issue publica no tracker.
${RUBRICA}

CANDIDATO:
${JSON.stringify(x.c)}

VOTOS DO PAINEL:
${JSON.stringify(x.votes)}

De o verdict final (build/maybe/skip) e uma justificativa apontando o principal risco ou condicao. Na duvida, prefira 'maybe' (nao cria issue). Retorne o schema.`,
          { model: EFF.arbitro, schema: VERDICT, label: `arbitro:${idx}`, phase: 'Arbitrar' }
        ).then(vf => ({ ...x, final: vf, build: vf?.verdict === 'build' }))
      : { ...(x || {}), build: false }
)
const winners = judged.filter(x => x && x.build)
const cortados = judged.filter(x => x && !x.build).map(x => ({
  title: x.c && x.c.title, source: x.c && x.c.source,
  motivo: (x.final && x.final.rationale) || 'reprovado no painel de validacao',
}))
log(`aprovados para virar issue: ${winners.length}/${fresh.length}`)

// ------------------------------------------------------------- Fase 4: sintese --
phase('Sintetizar')
let drafts = { drafts: [] }
if (winners.length) {
  drafts = await agent(
    `Voce e o redator-chefe do Scriba. Recebe as melhorias APROVADAS e: (1) ranqueia por prioridade, (2) escreve o corpo de cada issue do GitHub no padrao do repo.
Padrao do corpo (markdown): secao **Contexto** (o problema, citando arquivo:linha ou URLs do evidence), **Proposta** (o que fazer), **Criterios de aceite** (checklist "- [ ]"), **Esforco estimado** (P/M/G + 1 linha de justificativa). Tom tecnico, direto, em portugues do Brasil. Sem cercas de codico em volta do corpo.

MELHORIAS APROVADAS (candidato + veredicto do arbitro):
${JSON.stringify(winners.map(w => ({ candidate: w.c, arbiter: w.final })))}

Para cada uma gere um draft: title (curto, imperativo), body_md (no padrao acima), labels (sempre ['melhoria','enhancement','auto-triagem']; acrescente 'bug' se for correcao de defeito), priority (1=alta .. 5=baixa). Retorne o schema.`,
    { model: EFF.sintese, schema: DRAFTS, label: 'sintese' }
  )
}
log(`rascunhos de issue: ${drafts?.drafts?.length || 0}`)

// ------------------------------------------------------------- Fase 5: criar --
phase('Criar')
const result = await agent(
  `Voce finaliza a rodada do minerador do Scriba (repo ${SLUG}, local em ${REPO}).
MODO: ${dryRun ? 'DRY-RUN — NAO crie nenhuma issue' : 'PRODUCAO — crie as issues'}.

RASCUNHOS APROVADOS (JSON, ja ranqueados por priority):
${JSON.stringify(drafts?.drafts || [])}

VARREDURA DE CONCORRENTES (todos os candidatos crus vindos de concorrentes, mesmo os que NAO viraram issue):
${JSON.stringify(competitorScan)}

CANDIDATOS CORTADOS na validacao (com motivo):
${JSON.stringify(cortados)}

ANALISE DE PRECIFICACAO (concorrentes + recomendacao p/ Brasil; pode ser null):
${JSON.stringify(pricing)}

Passos:
1. Descubra data e hora de hoje no shell:
   - data: \`Get-Date -Format yyyy-MM-dd\` (se falhar, use "sem-data").
   - TAG DA RODADA: rode \`Get-Date -Format "yyyy-MM-dd-HHmm"\` e monte o nome da label como "run-<valor>" (ex.: run-2026-06-13-1851). USE A MESMA tag em todos os passos abaixo (se Get-Date falhar, use "run-sem-data").
2. Escreva SEMPRE um relatorio em ${REPO}/docs/melhorias-<data>.md com:
   a. cabecalho (data, TAG DA RODADA, posture=${posture}, minerProfundo=${minerProfundo}, dryRun=${dryRun}, total de rascunhos aprovados);
   b. por prioridade, cada rascunho APROVADO (title, labels, body_md completo);
   c. uma secao "## Concorrentes (varredura)" listando CADA item de VARREDURA DE CONCORRENTES (title, proposta, evidence/URLs) — para a analise de concorrente ficar visivel mesmo quando nada virou issue. Se a lista vier vazia, escreva "Nenhum candidato de concorrente sobreviveu ou foi gerado nesta rodada.";
   d. uma secao "## Rejeitados (com motivo)" listando os CANDIDATOS CORTADOS (title, source, motivo);
   e. se ANALISE DE PRECIFICACAO nao for null, uma secao "## Precificacao competitiva (foco Brasil)" com: uma TABELA de concorrentes (Concorrente | Plano | Preco USD | Periodo | Limites | Free tier | Fonte), um paragrafo sobre o mercado BR (gap + disposicao a pagar), e a RECOMENDACAO do Scriba (TABELA: Plano | Preco USD | Publico) + a justificativa.
3. Se NAO for dry-run:
   i.  CRIE a label da rodada (idempotente): \`gh label create "<runtag>" --repo ${SLUG} --color 0E8A16 --description "Issues da rodada do minerador (<data e hora>)" --force\` (o --force cria ou atualiza, sem falhar se ja existir).
   ii. Para cada rascunho, crie a issue com \`gh issue create --repo ${SLUG} --title "<title>" --body-file <arquivo_temp> --label melhoria --label enhancement --label auto-triagem --label "<runtag>"\` (use --body-file para preservar o markdown; apague o temp depois). Capture a URL de cada issue.
   Se for dry-run: NAO crie label nem issues.
Retorne um resumo em texto: TAG DA RODADA usada + caminho do relatorio + (lista de URLs das issues criadas) OU "dry-run: nenhuma issue criada".`,
  { model: EFF.criar, label: dryRun ? 'relatorio (dry-run)' : 'criar issues' }
)

return {
  posture, dryRun, minerModel, votos: VOTERS,
  brutos: candidates.length, posDedup: fresh.length, aprovados: winners.length,
  drafts: (drafts?.drafts || []).map(d => ({ priority: d.priority, title: d.title, labels: d.labels })),
  pricing: pricing ? { concorrentes: pricing.competitors?.length || 0, planos_sugeridos: pricing.recomendacao_scriba?.planos_sugeridos } : null,
  finalizacao: result,
}
