# AGENTS.md — Diretrizes, Papéis e Arquitetura do Comparador de Fotos com IA

Este documento define os papéis de engenharia, arquitetura de software, convenções de código e harness de testes para o projeto **Comparador de Fotos com IA**.

---

## 👥 Papéis Especializados de Agentes (Agent Roles)

### 1. `ComputerVisionEngineer`
* **Escopo**: Extração de características visuais, modelos de embeddings e hashing perceptual.
* **Tecnologias**: PyTorch, OpenCLIP (`ViT-B-32`), `imagehash` (pHash), PIL/Pillow-SIMD, CUDA/cuDNN.
* **Responsabilidades**:
  * Otimizar inferência em lotes (batching) na GPU sem exceder a VRAM.
  * Garantir integridade de leitura de formatos (`.jpg`, `.png`, `.webp`, `.heic`, etc.).
  * Tratar arquivos corrompidos, incompletos ou formatos disfarçados (ex: pacotes de figurinhas em ZIP).

### 2. `PerformanceArchitect`
* **Escopo**: Eficiência de hardware, escalabilidade de I/O de disco e limites de memória do sistema operacional.
* **Tecnologias**: Concurrency (`concurrent.futures`, `threading`, `queue`), Win32 Memory API (`GlobalMemoryStatusEx`), PowerShell disk detection (`Get-PhysicalDisk`).
* **Responsabilidades**:
  * Manter pre-fetching assíncrono para que a GPU opere em 100% de ocupação contínua.
  * Respeitar rigorosamente o teto de 80% de RAM total do sistema Windows para evitar travamentos.
  * Separar leitura e escrita em disco rápido (SSD NVMe) vs disco de armazenamento em massa (HDD mecânico 5400 RPM).

### 3. `TkinterUIDesigner`
* **Escopo**: Interface gráfica de alta responsividade, visualização em tela cheia e experiência do usuário (UX).
* **Tecnologias**: Python Tkinter, `ttk`, Canvas responsivo com rolagem suave, manipulação de eventos de teclado/mouse.
* **Responsabilidades**:
  * Modo de comparação dinâmica em tela cheia (expande fotos para ocupar 100% do viewport disponível para comparação detalhada de nitidez e qualidade).
  * Atalhos de teclado ergonômicos:
    * `1`, `2`, `3`, `4`: seleção/exclusão direta de fotos de um grupo.
    * `*`: exclusão rápida de todas as fotos do grupo atual com contagem de 2s e cancelamento via ESC.
    * `Page Up` / `Page Down`: inversão atômica dos locais físicos das fotos comparadas no disco.
  * Promoção inteligente de qualidade ("Promover Maior para Pasta Principal"): substitui cópias inferiores por originais de alta resolução sem perder o diretório de destino.
  * Diálogos de ação com feedback visual e temporizadores seguros (ex: confirmação com contagem regressiva de 2s e cancelamento instantâneo via tecla `ESC`).

### 4. `QualityAssuranceAgent`
* **Escopo**: Harness de validação contínua, testes de regressão e garantia de segurança de dados.
* **Tecnologias**: `unittest`, `send2trash` (Lixeira do Windows), scripts de benchmark e estresse sintético.
* **Responsabilidades**:
  * Assegurar que nenhuma foto seja deletada permanentemente sem ir para a Lixeira do Windows (`send2trash`).
  * Validar robustez contra arquivos de 0 bytes, imagens truncadas, blocos de bytes nulos e permutações de caminhos de arquivos.
  * Validar preservação de integridade de dados durante trocas atômicas (`swap_file_locations`) e promoções (`upgrade_photo_in_folder`).

---

## 🏛️ Estrutura Modular da Arquitetura (`src/`)

```text
comparador_fotos/
├── AGENTS.md                  # Este guia de governança e papéis
├── config_app.json            # Configurações persistentes de hardware e caminhos
├── comparador_fotos.py        # Ponto de entrada leve (CLI + GUI launcher)
├── comparar_fotos.bat         # Script de execução rápida para o usuário
├── src/                       # Código-fonte modular refatorado
│   ├── __init__.py
│   ├── config.py              # Gerenciamento de configurações e caminhos de cache
│   ├── hardware.py            # Diagnóstico de RAM (Win32), detecção de SSD/HDD e disco
│   ├── scanner.py             # Varredura inteligente de arquivos com filtros de exclusão
│   ├── hasher.py              # Cálculo protegido de SHA-256 e pHash
│   ├── embeddings.py          # Pipeline CLIP multithread com controle de RAM
│   ├── clustering.py          # Agrupamento exato, pHash e grafo KNN
│   ├── web_photo_detector.py  # Classificador Zero-Shot de Memes e Fotos Web
│   ├── photo_engine.py        # Orquestrador central da análise progressiva
│   └── ui/                    # Interface gráfica modular
│       ├── __init__.py
│       ├── styles.py          # Estilos ttk unificados
│       ├── menus.py           # Menus organizados (Duplicatas vs Web/Memes)
│       ├── actions.py         # Handlers de ações (swap, promote, delete, mark different)
│       ├── components/
│       │   └── photo_card.py  # Card visual de cada foto com atalhos numéricos
│       ├── views/
│       │   ├── grid_view.py   # Renderizador da visão em grade
│       │   └── fullscreen_view.py # Renderizador do comparativo em tela cheia
│       ├── dialogs/
│       │   ├── timed_confirm_dialog.py  # Modal com timer de 2s e tecla ESC
│       │   ├── folder_rules_dialog.py    # Gerenciador de pastas prioritárias/alvo
│       │   ├── deletion_progress_dialog.py # Barra de progresso de exclusão
│       │   └── performance_dialog.py     # Painel de recursos e migração de SSD
│       └── main_window.py     # Janela principal e coordenador de ciclo de vida
└── harness/                   # Suíte de validação e testes automatizados
    ├── __init__.py
    ├── test_suite.py          # Testes automatizados de regressão
    └── benchmark.py           # Medição de taxa de decodificação e inferência
```

---

## 🛡️ Regras Invioláveis de Segurança e Estabilidade

1. **Segurança de Arquivos**: Toda operação de exclusão deve SEMPRE usar `send2trash` (enviando para a Lixeira do Windows), nunca `os.remove` ou `os.unlink`.
2. **Estabilidade de Memória**: O pre-fetcher deve monitorar `GlobalMemoryStatusEx` e pausar o enfileiramento quando o consumo global de RAM do Windows ultrapassar o limite configurado (padrão 80%).
3. **Resiliência a Falhas de I/O**: Arquivos truncados devem ser aceitos (`ImageFile.LOAD_TRUNCATED_IMAGES = True`), e arquivos corrompidos ou de 0 bytes devem ser tratados sem quebrar a execução ou poluir o log com tracebacks desnecessários.
4. **Ergonomia e Rapidez**: O usuário deve conseguir comparar fotos em tela cheia e tomar ações rápidas com uma única tecla (`1`, `2`, `3`, `4`, `*`), com segurança garantida pelo timer de 2s e cancelamento via `ESC`.
