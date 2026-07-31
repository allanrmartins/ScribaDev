---
name: scriba-reunioes
description: >-
  Consulta a base LOCAL de reuniões do ScribaDev (índice SQLite+FTS5 com resumos,
  transcrições, participantes, clientes e pendências). Use sempre que o usuário
  perguntar sobre reuniões ou calls gravadas, mesmo sem citar o Scriba: "qual foi a
  última reunião do cliente X", "o que foi discutido/decidido sobre Y", "quem
  participou da call de sexta", "quais pendências ficaram da reunião", "em que
  reunião falamos de Z", "me resume a reunião de ontem".
---

# Consultar a base de reuniões do ScribaDev

O ScribaDev grava e transcreve reuniões e mantém um índice de busca local em `%LOCALAPPDATA%\ScribaDev\index.db`.
O índice é derivado das pastas de gravação (fonte da verdade: `meta.json` + `notas.md` de cada reunião) e cobre título, cliente, participantes, resumo E transcrição completa (FTS5).
Tudo é local da máquina de quem roda: cada contribuidor consulta as próprias reuniões.

## Como invocar a CLI

1. Preferir o comando `scribadev` (a instalação padrão via `setup.ps1` o coloca no PATH).
2. Se não estiver no PATH, usar o Python do venv da instalação: `& "$env:LOCALAPPDATA\ScribaDev\venv\Scripts\python.exe" -m scriba.cli <comando>`.
3. Último recurso (checkout do repo sem instalação): `python -m scriba.cli <comando>` a partir da raiz do repo (os comandos `search`/`show`/`reindex` só usam stdlib).

Se `search` devolver `[]` e a pessoa tem reuniões gravadas, rodar `scribadev reindex` uma vez e buscar de novo.

## Comandos de consulta

### `search` - achar reuniões

```
scribadev search [termos...] [--client X] [--participant NOME] [--since AAAA-MM-DD] [--until AAAA-MM-DD] [--status done] [--limit N] --json
```

- Sempre usar `--json`: devolve registros completos com `id` (aceito pelo `show`), `folder`, `title`, `client`, `meeting_title`, `started_at`, `duration_s`, `status`, `export_path`, `participants` e, quando há termos de busca, `snippet` (o trecho indexado onde os termos casaram, marcados com « »).
- Os termos são full-text (AND implícito) sobre título + cliente + participantes + resumo + transcrição.
- `--status` tem default `done` (reuniões prontas); `--status ""` traz todas (inclusive em processamento).
- Ordenação: mais recente primeiro.

### `show` - ler uma reunião

```
scribadev show <id-ou-pasta> [--transcript] [--json]
```

- `show <id> --json`: meta + participantes (presentes/mencionados) + pendências (`action_items`, com `state` open/done/...) + `summary` (o resumo completo da nota).
- `show <id> --transcript`: só a transcrição completa (pode ter dezenas de milhares de linhas - ver o funil abaixo antes de usar).
- O `id` vem do `search --json`; também aceita o caminho da pasta da gravação.

## Receitas por tipo de pergunta

| Pergunta | Comando |
|---|---|
| "Última reunião do cliente X?" | `search --client X --limit 5 --json` e pegar a primeira; se vier vazio, tentar `search X --json` (nem toda reunião tem o campo `client` preenchido, mas o nome costuma aparecer no título/resumo) |
| "O que foi decidido sobre Y?" | `search Y --json` → escolher pelo snippet/data → `show <id> --json` e responder pelo `summary` |
| "Quem participou da reunião Z?" | `search Z --json` (participants já vem na busca) |
| "Quais pendências ficaram?" | `show <id> --json` → `action_items` (filtrar `state == "open"`) |
| "Reuniões com a pessoa P este mês?" | `search --participant P --since AAAA-MM-01 --json` |
| "Em que momento falamos de W?" | `search W --json` → Grep por W no `<folder>\notas.md` (as falas têm timestamp `[HH:MM:SS]`) |

## Funil de leitura (importante)

1. `search --json` primeiro; julgar relevância por título, data, cliente e `snippet`.
2. `show <id> --json` e responder com o `summary` sempre que possível.
3. Só descer à transcrição quando o resumo não basta: preferir Grep (com contexto `-C`) no `<folder>\notas.md` a despejar `show --transcript` inteiro; a transcrição fica depois do marcador `## Transcrição completa`.
4. Na resposta, sempre citar a reunião usada: título, data em `DD/MM/AAAA` e cliente.

## Regras

- A saída da CLI é UTF-8; rodar os comandos como estão, sem redirecionar por conversores de encoding.
- Nunca reproduzir a transcrição inteira no chat; citar só os trechos relevantes.
- Os dados são de clientes e são privados: nunca copiar conteúdo de reunião para issues, PRs, Gists ou qualquer destino fora da máquina.
- Não editar `notas.md`, `meta.json` nem o `index.db` ao responder perguntas; esta skill é somente leitura (exceto o `reindex`, que só reconstrói o cache).
