import math
from tkinter import ttk

from src.config import APP_CONFIG
from src.ui.components.photo_card import PhotoCard


def render_fullscreen_comparison(gui):
    """
    Renderiza o grupo ativo em modo de tela cheia responsivo, ocupando 100% do viewport
    para comparação lado a lado de nitidez, foco e qualidade.
    """
    for child in gui.frame.winfo_children():
        child.destroy()

    groups = gui.current_groups()
    total_groups = len(groups)

    if not groups:
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
                text="Os comparativos em tela cheia carregarão automaticamente assim que duplicatas forem detectadas.",
                font=("Segoe UI", 9, "italic"),
                foreground="#777777",
                justify="center"
            ).pack(pady=4)
            gui.page_label_var.set("Processando...")
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
        gui.page_label_var.set("Sem grupos")
        return

    if gui.compare_group_idx >= total_groups:
        gui.compare_group_idx = max(0, total_groups - 1)

    group = groups[gui.compare_group_idx]

    # Se o grupo foi esvaziado (todas fotos excluídas)
    if not group or len(group) == 0:
        if group in groups:
            try:
                groups.remove(group)
            except ValueError:
                pass
        total_groups = len(groups)
        if total_groups == 0:
            render_fullscreen_comparison(gui)
            return
        if gui.compare_group_idx >= total_groups:
            gui.compare_group_idx = max(0, total_groups - 1)
        group = groups[gui.compare_group_idx]
        if not group or len(group) == 0:
            render_fullscreen_comparison(gui)
            return

    num_photos = len(group)
    raw_g = gui.raw_groups_for_tab() if hasattr(gui, "raw_groups_for_tab") else groups
    if len(raw_g) > total_groups:
        gui.page_label_var.set(f"Grupo Comparativo {gui.compare_group_idx + 1} de {total_groups} ({num_photos} fotos • {len(raw_g)} total)")
    else:
        gui.page_label_var.set(f"Grupo Comparativo {gui.compare_group_idx + 1} de {total_groups} ({num_photos} fotos)")

    # Dimensões disponíveis na área de desenho
    canvas_width = max(600, gui.canvas.winfo_width() - 16)
    canvas_height = max(400, gui.canvas.winfo_height() - 16)

    # Cabeçalho do grupo ativo compacto
    header = ttk.Frame(gui.frame, padding=(4, 2, 4, 2))
    header.pack(fill="x")

    header_title = f"🔍 Comparativo — Grupo #{gui.compare_group_idx + 1}"
    if num_photos == 1:
        header_title += " (Última foto restante — duplicatas já excluídas)"

    ttk.Label(
        header,
        text=header_title,
        font=("Segoe UI", 10, "bold")
    ).pack(side="left")

    # Botão para apagar todo o grupo com timer de 2s (Atalho *)
    ttk.Button(
        header,
        text="🗑️ Apagar Grupo [*]",
        style="Danger.TButton",
        command=lambda g=group: gui._trigger_group_deletion(g)
    ).pack(side="right", padx=2)

    if num_photos >= 2:
        ttk.Button(
            header,
            text="≠ São fotos diferentes [/]",
            command=gui.mark_active_comparison_as_different
        ).pack(side="right", padx=2)

        ttk.Button(
            header,
            text="🗑️ Manter Maior e Apagar Cópias",
            command=lambda g=group: gui.keep_largest_and_delete_others(g)
        ).pack(side="right", padx=2)

        ttk.Button(
            header,
            text="⭐ Promover Maior",
            command=lambda g=group: gui.promote_largest_in_group(g)
        ).pack(side="right", padx=2)

        ttk.Button(
            header,
            text="🔄 Inverter (PgUp/PgDn)",
            command=gui.swap_active_comparison_photos
        ).pack(side="right", padx=2)

    # Cálculo dinâmico da grade para preencher 100% da tela disponível sem barra de rolagem
    if num_photos <= 1:
        cols = 1
        rows = 1
    elif num_photos == 2:
        cols = 2
        rows = 1
    elif num_photos == 3:
        cols = 3
        rows = 1
    elif num_photos == 4:
        cols = 2
        rows = 2
    elif num_photos <= 6:
        cols = 3
        rows = 2
    else:
        cols = min(4, num_photos)
        rows = math.ceil(num_photos / cols)

    cols = max(1, cols)
    rows = max(1, rows)

    col_width = (canvas_width // cols) - 12
    avail_h = canvas_height - 38
    card_non_img_height = 95
    max_thumb_h = max(60, (avail_h // rows) - card_non_img_height)
    max_thumb_w = max(80, col_width - 12)
    target_img_size = (max_thumb_w, max_thumb_h)

    grid_container = ttk.Frame(gui.frame)
    grid_container.pack(fill="both", expand=True, padx=2, pady=2)

    for col_i in range(cols):
        grid_container.columnconfigure(col_i, weight=1)
    for row_i in range(rows):
        grid_container.rowconfigure(row_i, weight=1)

    for p_idx, p in enumerate(group):
        r = p_idx // cols
        c = p_idx % cols
        PhotoCard(grid_container, p, p_idx, group, gui, row=r, col=c, max_size=target_img_size)
