import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from pathlib import Path

from src.config import APP_CONFIG, save_app_config, normalize_dir_str


class ExcludedFoldersDialog:
    """Janela modal para gerenciamento de pastas e subdiretórios excluídos do processamento de imagens."""
    def __init__(self, gui):
        self.gui = gui
        self.top = tk.Toplevel(gui.root)
        self.top.title("🚫 Configuração de Pastas Excluídas do Processamento")
        self.top.geometry("820x620")
        self.top.minsize(720, 500)
        self.top.transient(gui.root)
        try:
            if gui.root.winfo_viewable():
                self.top.grab_set()
        except Exception:
            pass

        # Carrega cópia de trabalho das regras
        raw_rules = APP_CONFIG.get("excluded_folders", [])
        self.rules = []
        for r in raw_rules:
            if isinstance(r, str):
                self.rules.append({"path": r, "include_subdirs": True, "enabled": True})
            elif isinstance(r, dict):
                self.rules.append({
                    "path": str(r.get("path", "")),
                    "include_subdirs": bool(r.get("include_subdirs", True)),
                    "enabled": bool(r.get("enabled", True))
                })

        self.default_subdirs_var = tk.BooleanVar(value=True)
        self._build_ui()
        self._refresh_tree()

    def _build_ui(self):
        main = ttk.Frame(self.top, padding=(16, 12, 16, 12))
        main.pack(fill="both", expand=True)

        # Cabeçalho
        head = ttk.Frame(main)
        head.pack(fill="x", pady=(0, 10))

        ttk.Label(
            head,
            text="🚫 Pastas e Subdiretórios Excluídos da Varredura",
            font=("Segoe UI", 12, "bold"),
            foreground="#cc0000"
        ).pack(anchor="w")

        ttk.Label(
            head,
            text="As pastas configuradas abaixo serão ignoradas durante a coleta de imagens, extração de hashes e análise por IA.\n"
                 "Você pode especificar pastas completas do disco ou nomes de subpastas recorrentes (como 'WhatsApp Stickers').",
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(3, 0))

        # Painel central com Tabela e Botões Laterais
        table_container = ttk.Frame(main)
        table_container.pack(fill="both", expand=True, pady=6)

        # Treeview de regras
        tree_frame = ttk.Frame(table_container)
        tree_frame.pack(side="left", fill="both", expand=True)

        columns = ("path", "subdirs")
        self.tree = ttk.Treeview(tree_frame, columns=columns, selectmode="browse")
        self.tree.heading("#0", text="Status", anchor="center")
        self.tree.heading("path", text="Pasta / Padrão de Subdiretório", anchor="w")
        self.tree.heading("subdirs", text="Incluir Subdiretórios?", anchor="center")

        self.tree.column("#0", width=100, minwidth=80, stretch=False, anchor="center")
        self.tree.column("path", width=420, stretch=True, anchor="w")
        self.tree.column("subdirs", width=180, minwidth=140, stretch=False, anchor="center")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<Double-Button-1>", lambda e: self._toggle_selected_subdirs())

        # Botões de Ação na Lateral Direita
        btn_box = ttk.Frame(table_container, padding=(10, 0, 0, 0))
        btn_box.pack(side="right", fill="y")

        ttk.Button(
            btn_box,
            text="📁 Adicionar Pasta do Disco...",
            command=self._add_folder_from_disk,
            width=28
        ).pack(fill="x", pady=3)

        ttk.Button(
            btn_box,
            text="➕ Adicionar Nome de Subpasta...",
            command=self._add_folder_pattern,
            width=28
        ).pack(fill="x", pady=3)

        ttk.Separator(btn_box, orient="horizontal").pack(fill="x", pady=6)

        ttk.Button(
            btn_box,
            text="🔄 Alternar Subdiretórios",
            command=self._toggle_selected_subdirs,
            width=28
        ).pack(fill="x", pady=3)

        ttk.Button(
            btn_box,
            text="☑️ Ativar / Desativar Regra",
            command=self._toggle_selected_enabled,
            width=28
        ).pack(fill="x", pady=3)

        ttk.Button(
            btn_box,
            text="🗑️ Remover Regra Selecionada",
            command=self._remove_selected,
            style="Danger.TButton",
            width=28
        ).pack(fill="x", pady=3)

        # Seção de Presets Rápidos
        preset_frame = ttk.LabelFrame(main, text=" ⚡ Atalhos Rápidos para Pastas Comuns ", padding=8)
        preset_frame.pack(fill="x", pady=(6, 8))

        p_row = ttk.Frame(preset_frame)
        p_row.pack(fill="x")

        ttk.Button(
            p_row,
            text="+ Figurinhas WhatsApp (Stickers)",
            command=lambda: self._add_preset_rule("WhatsApp Stickers")
        ).pack(side="left", padx=3)

        ttk.Button(
            p_row,
            text="+ GIFs WhatsApp",
            command=lambda: self._add_preset_rule("WhatsApp Animated Gifs")
        ).pack(side="left", padx=3)

        ttk.Button(
            p_row,
            text="+ Miniaturas (.thumbnails)",
            command=lambda: self._add_preset_rule(".thumbnails")
        ).pack(side="left", padx=3)

        ttk.Button(
            p_row,
            text="+ Enviadas (Sent / Private)",
            command=self._add_sent_private_presets
        ).pack(side="left", padx=3)

        ttk.Button(
            p_row,
            text="+ Lixeira (.trash)",
            command=lambda: self._add_preset_rule(".trash")
        ).pack(side="left", padx=3)

        # Opção global padrão
        opt_box = ttk.Frame(main)
        opt_box.pack(fill="x", pady=(2, 6))
        ttk.Checkbutton(
            opt_box,
            text="Excluir subdiretórios recursivamente por padrão para novas pastas adicionadas",
            variable=self.default_subdirs_var
        ).pack(side="left")

        # Barra de rodapé com resumo e botões de fechar
        footer = ttk.Frame(main)
        footer.pack(fill="x", pady=(8, 0))

        self.status_label = ttk.Label(footer, text="", font=("Segoe UI", 9, "bold"))
        self.status_label.pack(side="left")

        ttk.Button(footer, text="Salvar e Aplicar", command=self._save_and_apply, width=16).pack(side="right", padx=4)
        ttk.Button(footer, text="Cancelar", command=self.top.destroy, width=12).pack(side="right", padx=4)

    def _refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        active_count = 0
        for idx, rule in enumerate(self.rules):
            enabled = rule.get("enabled", True)
            if enabled:
                active_count += 1
            status_txt = "🟢 Ativo" if enabled else "⚪ Inativo"
            subdirs_txt = "✅ Sim (Recursivo)" if rule.get("include_subdirs", True) else "❌ Não (Apenas esta)"
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                text=status_txt,
                values=(rule.get("path", ""), subdirs_txt)
            )

        self.status_label.config(text=f"Total: {len(self.rules)} regras ({active_count} ativas)")

    def _add_folder_from_disk(self):
        folder = filedialog.askdirectory(
            title="Selecione a pasta para excluir do processamento",
            parent=self.top
        )
        if not folder:
            return

        folder_path = str(Path(folder).resolve())
        # Verifica se já existe
        for r in self.rules:
            if normalize_dir_str(r.get("path", "")) == normalize_dir_str(folder_path):
                messagebox.showinfo("Pasta já cadastrada", "Esta pasta já está na lista de exclusão.", parent=self.top)
                return

        self.rules.append({
            "path": folder_path,
            "include_subdirs": bool(self.default_subdirs_var.get()),
            "enabled": True
        })
        self._refresh_tree()

    def _add_folder_pattern(self):
        pattern = simpledialog.askstring(
            "Adicionar Nome de Subpasta / Padrão",
            "Digite o nome da subpasta que deve ser excluída em qualquer local da varredura:\n(Exemplo: 'WhatsApp Stickers', 'Private', 'Sent', '.thumbnails')",
            parent=self.top
        )
        if not pattern or not pattern.strip():
            return

        clean = pattern.strip()
        for r in self.rules:
            if r.get("path", "").lower() == clean.lower():
                messagebox.showinfo("Regra já existe", "Esta regra de exclusão já está cadastrada.", parent=self.top)
                return

        self.rules.append({
            "path": clean,
            "include_subdirs": bool(self.default_subdirs_var.get()),
            "enabled": True
        })
        self._refresh_tree()

    def _add_preset_rule(self, name):
        for r in self.rules:
            if r.get("path", "").lower() == name.lower():
                r["enabled"] = True
                self._refresh_tree()
                return

        self.rules.append({
            "path": name,
            "include_subdirs": True,
            "enabled": True
        })
        self._refresh_tree()

    def _add_sent_private_presets(self):
        self._add_preset_rule("Sent")
        self._add_preset_rule("Private")

    def _get_selected_index(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Nenhuma seleção", "Por favor, selecione uma regra na tabela.", parent=self.top)
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _toggle_selected_subdirs(self):
        idx = self._get_selected_index()
        if idx is None or idx >= len(self.rules):
            return
        curr = self.rules[idx].get("include_subdirs", True)
        self.rules[idx]["include_subdirs"] = not curr
        self._refresh_tree()
        self.tree.selection_set(str(idx))

    def _toggle_selected_enabled(self):
        idx = self._get_selected_index()
        if idx is None or idx >= len(self.rules):
            return
        curr = self.rules[idx].get("enabled", True)
        self.rules[idx]["enabled"] = not curr
        self._refresh_tree()
        self.tree.selection_set(str(idx))

    def _remove_selected(self):
        idx = self._get_selected_index()
        if idx is None or idx >= len(self.rules):
            return
        del self.rules[idx]
        self._refresh_tree()

    def _save_and_apply(self):
        APP_CONFIG["excluded_folders"] = self.rules
        save_app_config(APP_CONFIG)

        if self.gui.engine is not None:
            self.gui.engine.excluded_folders = self.rules

        self.gui.status.set(f"Configuração salva: {len([r for r in self.rules if r.get('enabled')])} regras de exclusão ativas.")
        messagebox.showinfo(
            "Regras Salvas",
            "As regras de pastas e subdiretórios excluídos foram salvas com sucesso!\n\n"
            "Elas serão aplicadas automaticamente nas próximas varreduras e análises de imagens.",
            parent=self.top
        )
        self.top.destroy()
