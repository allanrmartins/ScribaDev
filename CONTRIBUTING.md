# Contribuindo com o ScribaDev

Obrigado pelo interesse!
O ScribaDev é uma ferramenta local e privada de gravação/transcrição de reuniões - toda contribuição que preserve esse espírito é bem-vinda.

## Rodando do código-fonte

- **Windows**: `powershell -ExecutionPolicy Bypass -File setup.ps1` (cria o venv em `%LOCALAPPDATA%\ScribaDev\venv`, instala as dependências e põe `scribadev` no PATH).
- **macOS**: `./setup.sh` (equivalente; STT acelerado por Metal em Apple Silicon).
- **Linux**: suportado para desenvolvimento e transcrição de arquivos (captura ao vivo ainda não - épico #104).

## Testes

A suíte roda sem dependências nativas (elas são mockadas) e sem display:

```bash
python -m unittest discover -s tests
```

- Rode a suíte antes de qualquer push: o CI executa exatamente isso em Windows, Linux e macOS a cada push/PR.
- Teste novo acompanha toda correção de bug ou feature; testes de UI Qt rodam offscreen (`QT_QPA_PLATFORM=offscreen`).
- Testes nunca devem tocar dados reais do usuário (`%LOCALAPPDATA%\ScribaDev`): isole `util.APP_DIR` num tempdir (padrão dos testes existentes).

## Convenções de PR

- Commits no padrão `tipo(escopo): resumo` (ex.: `feat(timesheet): ...`, `fix(qt): ...`), mensagem em português.
- Um PR por assunto; PRs de épico podem ter vários commits organizados por marco.
- Para fechar issue automaticamente no merge, use `Closes #NN` no corpo do PR.
- Descreva como validou (suíte + teste manual quando envolver UI/áudio).

## Princípios do projeto

- **Local e privado**: áudio, transcrições e notas nunca saem da máquina do usuário; nenhuma telemetria.
- **Windows é a plataforma de referência**: mudanças multiplataforma não podem regredir o comportamento no Windows (o CI protege isso).
- **Qualidade acima de custo**: prefira a solução robusta e de manutenção simples; lint e testes quebrados se consertam ao ver.
