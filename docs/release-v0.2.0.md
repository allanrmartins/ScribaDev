Segunda versão do Scriba — de utilitário de bandeja a aplicativo completo. 🎙️

## Novidades

### Interface
- **Janela principal nova**: status ao vivo de todos os serviços (detecção, áudio, Whisper/GPU, Claude, diarização, autostart), painel da ligação em andamento com duração e botão ⏺ Gravar
- Comportamento de app de verdade: **minimizar mantém na barra de tarefas; fechar (X) volta para a bandeja** com o monitoramento ativo
- Telas de configuração reorganizadas em **abas por categoria** atrás do botão ⚙ (Notas / Gravação / Pastas / Resumo)
- **Leitor de notas embutido** com markdown renderizado, agrupamento por dia (Hoje/Ontem), **busca por conteúdo** com destaque e **títulos gerados por IA, editáveis**
- Tema escuro semi-transparente com botões modernos, toggles estilo Windows 11 e barra de título escura
- Pílula flutuante segue a call inteira: modo espera (⏺) quando a gravação automática está desligada ou após parar/descartar

### Transcrição e atas
- **Diarização local opcional** (pyannote.audio): participantes remotos separados por voz em *Participante 1/2/3*, com associação a nomes/papéis no resumo
- **Título curto por reunião** gerado pelo Claude na mesma chamada do resumo
- Ata estruturada como **especificação funcional ABAP**: tela de seleção, fontes de dados TABELA-CAMPO, regras, validações, layout e a nova seção **Objetos SAP citados** (tabela com timestamps)
- Normalização de termos SAP corrompidos pela fala (*S e 16N* → SE16N)
- **Inferência em lote** (~2× mais rápida) e **pré-carga do modelo durante a call**: notas prontas segundos após desligar
- Prompt da ata externalizado em `prompt.md`, editável no app

### Detecção e controle
- **Detecção multi-app**: Teams e Zoom (configurável)
- **Atalho de teclado global** gravar/parar, com captura da combinação na UI
- Descarte por duração mínima agora avisa por toast; ■ sempre mantém a gravação

### Robustez
- Pipeline de processamento, sonda de áudio e abertura de pastas isolados em subprocessos — crashes nativos (PortAudio/CUDA/winrt) não derrubam mais o app
- Pré-checagem de áudio antes de gravar; reparo automático de gravações pós-crash

Instalação e documentação completas no README (PT-BR e EN).
