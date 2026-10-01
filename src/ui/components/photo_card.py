import tkinter as tk
from tkinter import ttk
from pathlib import Path

from src.config import is_in_or_subdir


class PhotoCard(ttk.Frame):
    """Componente visual para exibição de cada foto com atalhos numéricos, caminho completo e ações."""
    def __init__(self, parent, path, p_idx, group, gui, row=0, col=0, max_size=(140, 140)):
        super().__init__(parent, relief="solid", borderwidth=1, padding=5)
        self.path = Path(path)
        self.p_idx = p_idx
        self.group = group
        self.gui = gui
        self.max_size = max_size

        self.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
        self._build_ui()

    def _build_ui(self):
        # 1. Top Bar: Shortcut Badge on Left, Rule/Web Badge on Right
        top_bar = ttk.Frame(self)
        top_bar.pack(fill="x", pady=(0, 2))

        shortcut_num = self.p_idx + 1
        shortcut_text = f" Tecla {shortcut_num} " if shortcut_num <= 9 else f" #{shortcut_num} "
        lbl_shortcut = tk.Label(
            top_bar,
            text=shortcut_text,
            font=("Segoe UI", 8, "bold"),
            bg="#0066cc",
            fg="#ffffff",
            padx=4,
            pady=1
        )
        lbl_shortcut.pack(side="left")

        # Badges on the right of top_bar
        web_info = self.gui.get_photo_web_info(self.path) if hasattr(self.gui, "get_photo_web_info") else None
        current_th = getattr(self.gui, "web_threshold", 0.50)
        is_web_tab = getattr(self.gui, "active_tab", "") == "web"
        show_web_badge = False
        if web_info:
            score = float(web_info.get("score", 0))
            if score >= current_th or is_web_tab:
                show_web_badge = True
                score_pct = int(score * 100)
                raw_label = str(web_info.get("label", "Web / Meme")).replace("\x00", "").strip()
                color = "#d9534f" if score_pct >= 70 else ("#f0ad4e" if score_pct >= 50 else "#5bc0de")
                ttk.Label(
                    top_bar,
                    text=f"🌐 {score_pct}% {raw_label}",
                    font=("Segoe UI", 8, "bold"),
                    foreground=color
                ).pack(side="right")

        in_keep = any(is_in_or_subdir(self.path, kf, self.gui.include_subdirs) for kf in self.gui.keep_folders)
        in_del = any(is_in_or_subdir(self.path, df, self.gui.include_subdirs) for df in self.gui.del_folders)

        if in_keep:
            ttk.Label(top_bar, text="🟢 Manter", font=("Segoe UI", 8, "bold"), foreground="#008800").pack(side="right", padx=(2, 0))
        elif in_del:
            ttk.Label(top_bar, text="🔴 Excluir", font=("Segoe UI", 8, "bold"), foreground="#cc0000").pack(side="right", padx=(2, 0))
        elif not show_web_badge:
            badge_text = "Principal" if self.p_idx == 0 else f"#{self.p_idx + 1}"
            ttk.Label(top_bar, text=badge_text, font=("Segoe UI", 8, "italic")).pack(side="right")

        # 2. Thumbnail Preview
        thumb = self.gui.get_thumb(self.path, size=self.max_size)
        if thumb:
            try:
                img_lbl = ttk.Label(self, image=thumb, cursor="hand2")
                img_lbl.image = thumb
                img_lbl.pack(pady=1)
                img_lbl.bind("<Double-Button-1>", lambda e: self.gui.open_file(self.path))
            except Exception:
                ttk.Label(self, text="[Sem prévia]", width=14, anchor="center").pack(pady=15)
        else:
            ttk.Label(self, text="[Sem prévia]", width=14, anchor="center").pack(pady=15)

        # 3. Filename & Dimensions
        name = self.path.name
        wrap_val = max(180, self.max_size[0] + 40)
        short_name = (name[:30] + "...") if len(name) > 32 else name
        name_lbl = ttk.Label(self, text=short_name, font=("Segoe UI", 8, "bold"), wraplength=wrap_val, justify="center")
        name_lbl.pack(pady=(1, 0))

        info_text = self.gui.get_file_info(self.path)
        ttk.Label(self, text=info_text, font=("Segoe UI", 7)).pack(pady=(0, 1))

        # 4. Full Directory Path (Clickable)
        folder_str = str(self.path.parent)
        dir_lbl = ttk.Label(
            self,
            text=f"📁 {folder_str}",
            font=("Segoe UI", 8),
            foreground="#0b3861",
            wraplength=wrap_val,
            justify="center",
            cursor="hand2"
        )
        dir_lbl.pack(pady=(1, 1), fill="x")
        dir_lbl.bind("<Button-1>", lambda e: self.gui.open_folder(self.path))

        # 5. Action Buttons (Abrir, Pasta, Sombras, Lixeira)
        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(1, 0))

        ttk.Button(
            btns, text="Abrir", width=4, style="Card.TButton",
            command=lambda: self.gui.open_file(self.path)
        ).pack(side="left", fill="x", expand=True, padx=1)

        ttk.Button(
            btns, text="Pasta", width=4, style="Card.TButton",
            command=lambda: self.gui.open_folder(self.path)
        ).pack(side="left", fill="x", expand=True, padx=1)

        ttk.Button(
            btns, text="↻ 90°", width=4, style="Card.TButton",
            command=self.rotate_clockwise
        ).pack(side="left", fill="x", expand=True, padx=1)

        ttk.Button(
            btns, text="✨ Sombras", width=6, style="Card.TButton",
            command=self.remove_shadows
        ).pack(side="left", fill="x", expand=True, padx=1)

        ttk.Button(
            btns, text="Lixeira", width=5, style="Card.TButton",
            command=self.delete_with_confirmation
        ).pack(side="left", fill="x", expand=True, padx=1)

    def rotate_clockwise(self):
        """Gira a foto em 90 graus no sentido horário."""
        if hasattr(self.gui, "rotate_photo"):
            self.gui.rotate_photo(self.path, degrees=90)

    def remove_shadows(self):
        """Inicia a remoção de sombras com IA e exibe o comparativo Antes e Depois em tela cheia."""
        if hasattr(self.gui, "remove_photo_shadows"):
            self.gui.remove_photo_shadows(self.path)

    def delete_with_confirmation(self):
        """Abre o diálogo de confirmação com temporizador de 2s e cancelamento via ESC."""
        self.gui._trigger_photo_deletion(self.path, self.group)
