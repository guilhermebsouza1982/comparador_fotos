from tkinter import ttk

from src.config import APP_CONFIG
from src.ui.components.photo_card import PhotoCard


def render_grid_view(gui):
    """Renderiza os grupos de fotos na grade padrão da página atual."""
    page_groups = gui.get_current_page_groups()
    groups = gui.current_groups()
    total_g = len(groups)
    tot_pages = gui.total_pages()
    raw_g = gui.raw_groups_for_tab() if hasattr(gui, "raw_groups_for_tab") else groups
    if len(raw_g) > total_g:
        gui.page_label_var.set(f"Página {gui.current_page} de {tot_pages} ({total_g} ativos / {len(raw_g)} total)")
    else:
        gui.page_label_var.set(f"Página {gui.current_page} de {tot_pages} ({total_g} grupos)")

    if not page_groups:
        if getattr(gui, "processing", False):
            card = ttk.Frame(gui.frame, padding=30)
            card.pack(expand=True, fill="both", pady=40)

            spin_frame = ttk.Frame(card)
            spin_frame.pack(pady=(10, 8))

            spin_char = gui._spinner_frames[gui._spinner_idx] if hasattr(gui, "_spinner_frames") and hasattr(gui, "_spinner_idx") else "⚡"
            spin_icon = ttk.Label(
                spin_frame,
                text=spin_char,
                font=("Segoe UI", 22, "bold"),
                foreground="#0066cc"
            )
            spin_icon.pack(side="left", padx=8)
            gui._processing_spinner_lbl = spin_icon

            ttk.Label(
                spin_frame,
                text="Processando Fotos com Inteligência Artificial...",
                font=("Segoe UI", 13, "bold"),
                foreground="#1d1d1f"
            ).pack(side="left")

            ttk.Label(
                card,
                textvariable=gui.status,
                font=("Segoe UI", 10),
                foreground="#444444",
                justify="center"
            ).pack(pady=6)

            ttk.Label(
                card,
                text="Os grupos de fotos duplicadas e similares aparecerão aqui em tempo real conforme forem identificados.",
                font=("Segoe UI", 9, "italic"),
                foreground="#777777",
                justify="center"
            ).pack(pady=4)
            return

        if gui.active_tab == "web" and not APP_CONFIG.get("enable_web_detector", True):
            msg = "A análise de Fotos da Web & Memes está desativada nas configurações.\nAtive-a no menu 'Fotos da Web & Memes' ou na janela 'Desempenho & Cache' para analisar e categorizar memes."
        else:
            msg = "Nenhum grupo de fotos similares encontrado para os filtros atuais."
        ttk.Label(
            gui.frame,
            text=msg,
            font=("Segoe UI", 11, "italic"),
            justify="center"
        ).pack(pady=50)
        return

    for g_idx, group in enumerate(page_groups):
        global_g_idx = (gui.current_page - 1) * gui.page_size + g_idx
        render_group_box(gui, global_g_idx, group)


def render_group_box(gui, group_num, group):
    """Renderiza a caixa individual de um grupo de fotos na visualização em grade."""
    if not group or len(group) == 0:
        return

    header_text = f" Grupo #{group_num + 1} — {len(group)} fotos "
    if len(group) == 1:
        header_text = f" Grupo #{group_num + 1} — 1 foto restante (duplicatas excluídas) "

    box = ttk.LabelFrame(
        gui.frame,
        text=header_text,
        padding=(8, 6, 8, 8)
    )
    box.pack(fill="x", expand=True, padx=6, pady=6)

    # Barra de ações rápidas no topo do grupo
    action_row = ttk.Frame(box)
    action_row.pack(fill="x", pady=(0, 4))

    # Botão para abrir em tela cheia comparativa
    ttk.Button(
        action_row,
        text="⛶ Comparar em Tela Cheia",
        command=lambda idx=group_num: gui.open_group_in_fullscreen(idx)
    ).pack(side="left", padx=(0, 4))

    if len(group) >= 2:
        ttk.Button(
            action_row,
            text="⭐ Promover Maior",
            command=lambda g=group: gui.promote_largest_in_group(g)
        ).pack(side="left", padx=2)

        ttk.Button(
            action_row,
            text="🗑️ Manter Maior e Apagar Cópias",
            command=lambda g=group: gui.keep_largest_and_delete_others(g)
        ).pack(side="left", padx=2)

        ttk.Button(
            action_row,
            text="≠ São fotos diferentes [/]",
            command=lambda g=group: gui.mark_group_as_different(g)
        ).pack(side="left", padx=2)

    # Botão para apagar todo o grupo com timer de 2s
    ttk.Button(
        action_row,
        text="🗑️ Apagar Grupo [*]",
        style="Danger.TButton",
        command=lambda g=group: gui._trigger_group_deletion(g)
    ).pack(side="right", padx=4)

    # Grade interna de cartões de fotos
    cards_frame = ttk.Frame(box)
    cards_frame.pack(fill="x", expand=True)

    for p_idx, p in enumerate(group):
        PhotoCard(cards_frame, p, p_idx, group, gui, row=0, col=p_idx, max_size=(140, 140))
