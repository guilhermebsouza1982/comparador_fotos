import tkinter as tk


def build_app_menus(gui):
    """
    Constrói a barra de menus do sistema organizada e separada por categorias:
    1. Arquivo (pastas, desempenho, logs e encerramento)
    2. Fotos Duplicadas & Similares (modos de visualização, abas CLIP/pHash/SHA e ações de duplicatas)
    3. Fotos da Web & Memes (aba web, ativação/desativação e filtros de confiança)
    """
    menubar = tk.Menu(gui.root)

    # =========================================================================
    # 1. MENU ARQUIVO
    # =========================================================================
    file_menu = tk.Menu(menubar, tearoff=0)
    file_menu.add_command(
        label="📁 Selecionar Nova Pasta...",
        command=gui.select_folder_and_start,
        accelerator="Ctrl+O"
    )
    file_menu.add_command(
        label="🔄 Recarregar Última Pasta Salva",
        command=gui.reload_last_folder,
        accelerator="F5"
    )
    file_menu.add_separator()
    file_menu.add_command(
        label="⚡ Desempenho & Cache...",
        command=gui.open_performance_dialog
    )
    file_menu.add_command(
        label="📁 Regras de Pastas Prioritárias...",
        command=gui.open_folder_rules
    )
    file_menu.add_command(
        label="🚫 Pastas Excluídas do Processamento...",
        command=gui.open_excluded_folders_dialog
    )
    file_menu.add_command(
        label="📋 Abrir Log de Erros...",
        command=gui.view_log_file
    )
    file_menu.add_command(
        label="🗑️ Limpar Arquivo de Log...",
        command=gui.clear_log_file
    )
    file_menu.add_separator()
    file_menu.add_checkbutton(
        label="⏳ Exibir Tela de Confirmação de 2 Segundos",
        variable=gui.confirm_delete_2s_var,
        command=gui.toggle_confirm_delete_2s
    )
    file_menu.add_separator()
    file_menu.add_command(
        label="❌ Sair",
        command=gui.close
    )
    menubar.add_cascade(label="Arquivo", menu=file_menu)

    # =========================================================================
    # 2. MENU TIPOS DE BUSCA
    # =========================================================================
    search_menu = tk.Menu(menubar, tearoff=0)
    search_menu.add_checkbutton(
        label="🔬 Busca por Hash / Quase Idênticas (SHA-256 e pHash)",
        variable=gui.enable_hash_var,
        command=gui.toggle_enable_hash
    )
    search_menu.add_checkbutton(
        label="🧠 Busca Visual por IA (OpenCLIP / GPU)",
        variable=gui.enable_clip_var,
        command=gui.toggle_enable_clip
    )
    search_menu.add_checkbutton(
        label="🌐 Detecção de Memes & Fotos da Web",
        variable=gui.web_detector_menu_var,
        command=gui.toggle_web_detector
    )
    search_menu.add_separator()
    search_menu.add_command(
        label="🧠 Baixar / Atualizar Componentes de IA...",
        command=gui.open_ai_installer_dialog
    )
    menubar.add_cascade(label="Tipos de Busca", menu=search_menu)

    # =========================================================================
    # 2. MENU FOTOS DUPLICADAS & SIMILARES
    # =========================================================================
    dup_menu = tk.Menu(menubar, tearoff=0)
    dup_menu.add_command(
        label="⛶ Alternar Tela Cheia / Grade",
        command=gui.toggle_view_mode,
        accelerator="F11"
    )
    dup_menu.add_separator()
    dup_menu.add_command(
        label="🔍 Visualmente Parecidas (CLIP)",
        command=lambda: gui.switch_tab("clip")
    )
    dup_menu.add_command(
        label="🔬 Quase Idênticas (pHash)",
        command=lambda: gui.switch_tab("phash")
    )
    dup_menu.add_command(
        label="🏷️ Duplicatas Exatas (SHA-256)",
        command=lambda: gui.switch_tab("exact")
    )
    dup_menu.add_command(
        label="📑 Todos os Grupos de Duplicatas",
        command=lambda: gui.switch_tab("all")
    )
    dup_menu.add_separator()
    dup_menu.add_command(
        label="⭐ Promover Maior para Pasta Principal",
        command=gui.promote_active_comparison_photo
    )
    dup_menu.add_command(
        label="🗑️ Manter Maior e Apagar Cópias",
        command=gui.keep_largest_in_active_comparison
    )
    dup_menu.add_command(
        label="🔄 Inverter Locais de Disco",
        command=gui.swap_active_comparison_photos,
        accelerator="PgUp / PgDn"
    )
    dup_menu.add_command(
        label="≠ Marcar como fotos diferentes",
        command=gui.mark_active_comparison_as_different,
        accelerator="/"
    )
    dup_menu.add_command(
        label="🗑️ Apagar Grupo Inteiro",
        command=gui._handle_asterisk_shortcut,
        accelerator="*"
    )
    menubar.add_cascade(label="Fotos Duplicadas & Similares", menu=dup_menu)

    # =========================================================================
    # 3. MENU FOTOS DA WEB & MEMES
    # =========================================================================
    web_menu = tk.Menu(menubar, tearoff=0)
    web_menu.add_command(
        label="🌐 Exibir Fotos da Web & Memes",
        command=lambda: gui.switch_tab("web"),
        accelerator="Alt+W"
    )
    web_menu.add_command(
        label="🔄 Reanalisar Memes e Fotos da Web...",
        command=gui.reanalyze_web_photos
    )
    web_menu.add_separator()
    web_menu.add_checkbutton(
        label="☑️ Habilitar Análise de Memes & Fotos da Web",
        variable=gui.web_detector_menu_var,
        command=gui.toggle_web_detector
    )
    web_menu.add_separator()

    web_filter_submenu = tk.Menu(web_menu, tearoff=0)
    web_filter_submenu.add_command(
        label="Rigor 20% (Muito Amplo — Exibe Todos os Memes Sutis e Suspeitos)",
        command=lambda: gui.set_web_threshold(0.20)
    )
    web_filter_submenu.add_command(
        label="Rigor 30% (Amplo — Alta Sensibilidade)",
        command=lambda: gui.set_web_threshold(0.30)
    )
    web_filter_submenu.add_command(
        label="Rigor 40% (Moderado Amplo)",
        command=lambda: gui.set_web_threshold(0.40)
    )
    web_filter_submenu.add_command(
        label="Rigor 50% (Equilibrado Padrão)",
        command=lambda: gui.set_web_threshold(0.50)
    )
    web_filter_submenu.add_command(
        label="Rigor 60% (Recomendado — Boa Precisão)",
        command=lambda: gui.set_web_threshold(0.60)
    )
    web_filter_submenu.add_command(
        label="Rigor 70% (Mais Rigoroso)",
        command=lambda: gui.set_web_threshold(0.70)
    )
    web_filter_submenu.add_command(
        label="Rigor 85% (Altíssima Certeza)",
        command=lambda: gui.set_web_threshold(0.85)
    )
    web_filter_submenu.add_separator()
    web_filter_submenu.add_command(
        label="🎯 Digitar Percentual Personalizado...",
        command=gui.prompt_custom_web_threshold
    )
    web_menu.add_cascade(label="🎯 Rigor do Filtro Web", menu=web_filter_submenu)
    menubar.add_cascade(label="Fotos da Web & Memes", menu=web_menu)

    # =========================================================================
    # 4. MENU FERRAMENTAS & IA
    # =========================================================================
    tools_menu = tk.Menu(menubar, tearoff=0)
    tools_menu.add_command(
        label="✨ Remover Sombras da Foto Selecionada...",
        command=gui.remove_shadows_active_comparison_photo
    )
    tools_menu.add_separator()

    shadow_mode_menu = tk.Menu(tools_menu, tearoff=0)
    shadow_mode_menu.add_radiobutton(
        label="🎛️ Alt. 1 + 2: Ajuste Interativo (Anti-Estouro)",
        variable=gui.shadow_mode_var,
        value="interactive",
        command=lambda: gui.set_shadow_removal_mode("interactive")
    )
    shadow_mode_menu.add_radiobutton(
        label="🧠 Alt. 3: Modo Local IA Profunda (Zero-DCE PyTorch GPU CUDA)",
        variable=gui.shadow_mode_var,
        value="deep",
        command=lambda: gui.set_shadow_removal_mode("deep")
    )
    shadow_mode_menu.add_radiobutton(
        label="📊 Alt. 4: Auto-Adaptativo por Histograma",
        variable=gui.shadow_mode_var,
        value="histogram",
        command=lambda: gui.set_shadow_removal_mode("histogram")
    )
    shadow_mode_menu.add_radiobutton(
        label="💎 Google Gemini Vision (Plano AI Pro)",
        variable=gui.shadow_mode_var,
        value="gemini",
        command=lambda: gui.set_shadow_removal_mode("gemini")
    )
    tools_menu.add_cascade(label="⚙️ Algoritmo Padrão de Remoção de Sombras", menu=shadow_mode_menu)

    tools_menu.add_separator()
    tools_menu.add_command(
        label="🔑 Configurar Chave de API Google Gemini...",
        command=gui.prompt_gemini_api_key
    )
    menubar.add_cascade(label="Ferramentas & IA", menu=tools_menu)

    # =========================================================================
    # 5. MENU AJUDA & ATUALIZAÇÕES
    # =========================================================================
    help_menu = tk.Menu(menubar, tearoff=0)
    help_menu.add_command(
        label="🔄 Verificar Atualizações Agora...",
        command=gui.check_for_updates_manual,
        accelerator="Ctrl+U"
    )
    help_menu.add_checkbutton(
        label="☑️ Verificar Atualizações ao Iniciar",
        variable=gui.check_updates_startup_var,
        command=gui.toggle_check_updates_startup
    )
    help_menu.add_command(
        label="⚙️ Configurar Repositório do GitHub...",
        command=gui.prompt_github_repo
    )
    help_menu.add_separator()
    help_menu.add_command(
        label="🌐 Abrir Página do Projeto no GitHub",
        command=gui.open_github_page
    )
    help_menu.add_command(
        label="ℹ️ Sobre o Comparador de Fotos...",
        command=gui.show_about_dialog
    )
    menubar.add_cascade(label="Ajuda", menu=help_menu)

    gui.root.config(menu=menubar)
