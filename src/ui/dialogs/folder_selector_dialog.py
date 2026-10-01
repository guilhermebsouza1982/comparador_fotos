import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path

from src.config import APP_CONFIG, save_app_config, load_app_config


class FolderSelectorDialog:
    """
    Diálogo hierárquico moderno para seleção múltipla de pastas e exclusão de diretórios ignorados.
    Suporta seleção direta de diretórios de análise, definição de pastas excluídas e tipos de busca.
    """
    def __init__(self, parent_gui, on_start=None):
        self.gui = parent_gui
        self.on_start = on_start
        self.selected_folders = []

        # Sempre recarrega as configurações mais recentes do disco para garantir consistência
        try:
            APP_CONFIG.update(load_app_config())
        except Exception:
            pass

        # Coleta candidatos de pastas anteriores:
        # 1. Pastas atualmente ativas no motor (se houver análise em andamento ou aberta)
        # 2. A última pasta utilizada (last_folder)
        # 3. A lista de últimas pastas salvas (last_folders)
        raw_candidates = []

        if hasattr(self.gui, "engine") and self.gui.engine:
            eng_roots = getattr(self.gui.engine, "roots", []) or [getattr(self.gui.engine, "root", None)]
            for r in eng_roots:
                if r:
                    raw_candidates.append(str(r))

        last_single = APP_CONFIG.get("last_folder")
        if last_single:
            raw_candidates.append(str(last_single))

        saved_folders = APP_CONFIG.get("last_folders", [])
        if isinstance(saved_folders, list):
            raw_candidates.extend([str(f) for f in saved_folders if f])
        elif isinstance(saved_folders, str) and saved_folders:
            raw_candidates.append(str(saved_folders))

        for f in raw_candidates:
            clean_f = str(f).strip().strip('"').strip("'")
            if not clean_f:
                continue
            try:
                p = Path(clean_f).resolve()
                if p.exists() and p.is_dir() and p not in self.selected_folders:
                    self.selected_folders.append(p)
            except Exception:
                pass

        # Carrega regras de pastas excluídas
        raw_excluded = APP_CONFIG.get("excluded_folders", [])
        self.excluded_rules = []
        for r in raw_excluded:
            if isinstance(r, str):
                self.excluded_rules.append({"path": r, "include_subdirs": True, "enabled": True})
            elif isinstance(r, dict):
                self.excluded_rules.append({
                    "path": str(r.get("path", "")),
                    "include_subdirs": bool(r.get("include_subdirs", True)),
                    "enabled": bool(r.get("enabled", True))
                })

        self.top = tk.Toplevel(self.gui.root if hasattr(self.gui, "root") else None)
        self.top.title("Seleção de Pastas para Análise — Comparador de Fotos")
        self.top.minsize(700, 580)
        self.top.transient(self.gui.root if hasattr(self.gui, "root") else None)

        self._build_ui()
        self._center_window(width=820, height=720)
        self._refresh_listbox()
        self._refresh_excluded_listbox()

        try:
            self.top.grab_set()
        except Exception:
            pass

    def _center_window(self, width=820, height=720):
        """Centraliza o diálogo de seleção de pastas na tela ou sobre a janela principal."""
        self.top.update_idletasks()
        sw = self.top.winfo_screenwidth()
        sh = self.top.winfo_screenheight()
        w = min(width, max(680, sw - 60))
        h = min(height, max(560, sh - 80))

        parent = getattr(self.gui, "root", None)
        if parent and parent.winfo_exists() and parent.winfo_viewable():
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            if pw > 200 and ph > 200:
                x = px + (pw - w) // 2
                y = py + (ph - h) // 2
            else:
                x = (sw - w) // 2
                y = (sh - h) // 2
        else:
            x = (sw - w) // 2
            y = (sh - h) // 2

        x = max(0, min(x, sw - w))
        y = max(10, min(y, sh - h - 40))
        self.top.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self):
        main_frame = ttk.Frame(self.top, padding=12)
        main_frame.pack(fill="both", expand=True)

        # 1. Painel de Pastas para Analisar
        lbl_target = ttk.Label(
            main_frame,
            text="📁 Pastas que DEVEM ser analisadas:",
            font=("Segoe UI", 10, "bold")
        )
        lbl_target.pack(anchor="w", pady=(0, 4))

        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill="both", expand=True, pady=(0, 6))

        self.listbox = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            font=("Consolas", 9),
            height=6,
            bg="#ffffff",
            fg="#222222",
            selectbackground="#0066cc",
            selectforeground="#ffffff",
            relief="solid",
            borderwidth=1
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)

        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        mgmt_bar = ttk.Frame(main_frame)
        mgmt_bar.pack(fill="x", pady=(0, 10))

        btn_add = ttk.Button(mgmt_bar, text="➕ Adicionar Pasta...", command=self._add_folder)
        btn_add.pack(side="left", padx=(0, 6))

        btn_remove = ttk.Button(mgmt_bar, text="✖ Remover Selecionada(s)", command=self._remove_selected)
        btn_remove.pack(side="left", padx=(0, 6))

        btn_clear = ttk.Button(mgmt_bar, text="🗑 Limpar Todas", command=self._clear_all)
        btn_clear.pack(side="left")

        # 2. Painel de Pastas que NÃO devem ser analisadas (Excluídas)
        excl_frame = ttk.LabelFrame(main_frame, text=" 🚫 Pastas que NÃO devem ser analisadas (Excluídas / Ignoradas) ", padding=8)
        excl_frame.pack(fill="both", expand=True, pady=(0, 10))

        excl_list_box = ttk.Frame(excl_frame)
        excl_list_box.pack(fill="both", expand=True, pady=(0, 6))

        self.excl_listbox = tk.Listbox(
            excl_list_box,
            selectmode=tk.EXTENDED,
            font=("Consolas", 9),
            height=8,
            bg="#ffffff",
            fg="#771111",
            selectbackground="#cc0000",
            selectforeground="#ffffff",
            relief="solid",
            borderwidth=1
        )
        excl_scroll = ttk.Scrollbar(excl_list_box, orient="vertical", command=self.excl_listbox.yview)
        self.excl_listbox.configure(yscrollcommand=excl_scroll.set)
        self.excl_listbox.pack(side="left", fill="both", expand=True)
        excl_scroll.pack(side="right", fill="y")

        excl_btn_bar = ttk.Frame(excl_frame)
        excl_btn_bar.pack(fill="x")

        ttk.Button(excl_btn_bar, text="➕ Adicionar Pasta a Ignorar...", command=self._add_excluded_folder).pack(side="left", padx=(0, 6))
        ttk.Button(excl_btn_bar, text="✏️ Digitar Subpasta/Nome...", command=self._add_excluded_pattern).pack(side="left", padx=(0, 6))
        ttk.Button(excl_btn_bar, text="✖ Remover da Lista", command=self._remove_excluded).pack(side="left", padx=(0, 6))
        ttk.Button(excl_btn_bar, text="⚙️ Regras Avançadas...", command=self._open_advanced_excluded_dialog).pack(side="right")

        # 3. Seletor de Tipos de Busca a Realizar
        opts_frame = ttk.LabelFrame(main_frame, text=" Tipos de Busca a Realizar ", padding=8)
        opts_frame.pack(fill="x", pady=(0, 10))

        self.var_enable_hash = tk.BooleanVar(value=bool(APP_CONFIG.get("enable_hash", True)))
        self.var_enable_clip = tk.BooleanVar(value=bool(APP_CONFIG.get("enable_clip", True)))
        self.var_enable_memes = tk.BooleanVar(value=bool(APP_CONFIG.get("enable_web_detector", True)))

        chk_hash = ttk.Checkbutton(
            opts_frame,
            text="🔬 Busca por Hash / Quase Idênticas (SHA-256 e pHash — Rápido)",
            variable=self.var_enable_hash
        )
        chk_hash.pack(anchor="w", pady=2)

        chk_clip = ttk.Checkbutton(
            opts_frame,
            text="🧠 Busca Visual por IA (OpenCLIP / GPU — Fotos Parecidas)",
            variable=self.var_enable_clip
        )
        chk_clip.pack(anchor="w", pady=2)

        chk_memes = ttk.Checkbutton(
            opts_frame,
            text="🌐 Detecção Multimodal de Memes, Prints e Fotos Web",
            variable=self.var_enable_memes
        )
        chk_memes.pack(anchor="w", pady=2)

        # 4. Rodapé com Iniciar e Cancelar
        footer = ttk.Frame(main_frame)
        footer.pack(fill="x", side="bottom")

        self.lbl_count = ttk.Label(footer, text="0 pastas selecionadas", font=("Segoe UI", 9, "italic"))
        self.lbl_count.pack(side="left")

        btn_cancel = ttk.Button(footer, text="Cancelar", command=self.top.destroy)
        btn_cancel.pack(side="right", padx=(6, 0))

        btn_start = ttk.Button(footer, text="🚀 Iniciar Análise", style="Accent.TButton", command=self._confirm_and_start)
        btn_start.pack(side="right")
        btn_start.focus_set()

        # Teclas de atalho convenientes
        self.top.bind("<Return>", lambda e: self._confirm_and_start())
        self.top.bind("<KP_Enter>", lambda e: self._confirm_and_start())
        self.top.bind("<Escape>", lambda e: self.top.destroy())

    def _refresh_listbox(self):
        self.listbox.delete(0, tk.END)
        for p in self.selected_folders:
            self.listbox.insert(tk.END, f" 📁  {p}")
        cnt = len(self.selected_folders)
        if cnt > 0:
            self.listbox.selection_set(0, tk.END)
            self.listbox.see(tk.END)
        self.lbl_count.config(text=f"{cnt} pasta{'s' if cnt != 1 else ''} selecionada{'s' if cnt != 1 else ''}")

    def _refresh_excluded_listbox(self):
        self.excl_listbox.delete(0, tk.END)
        for r in self.excluded_rules:
            p_text = r.get("path", "")
            sub_text = " (inclui subpastas)" if r.get("include_subdirs", True) else " (apenas pasta raiz)"
            self.excl_listbox.insert(tk.END, f" 🚫  {p_text}{sub_text}")

    def _add_folder(self):
        initial = str(self.selected_folders[-1]) if self.selected_folders else (APP_CONFIG.get("last_folder") or None)
        chosen = filedialog.askdirectory(parent=self.top, title="Selecione uma pasta para analisar", initialdir=initial)
        if chosen:
            p = Path(chosen).resolve()
            if p not in self.selected_folders:
                self.selected_folders.append(p)
                p_str = str(p)
                APP_CONFIG["last_folder"] = p_str
                cur_list = APP_CONFIG.get("last_folders", [])
                if not isinstance(cur_list, list):
                    cur_list = []
                if p_str not in cur_list:
                    cur_list.append(p_str)
                APP_CONFIG["last_folders"] = cur_list
                save_app_config(APP_CONFIG)
                self._refresh_listbox()

    def _remove_selected(self):
        sel_indices = list(self.listbox.curselection())
        if not sel_indices:
            return
        for idx in reversed(sorted(sel_indices)):
            if 0 <= idx < len(self.selected_folders):
                self.selected_folders.pop(idx)
        self._refresh_listbox()

    def _clear_all(self):
        self.selected_folders.clear()
        self._refresh_listbox()

    def _add_excluded_folder(self):
        initial = str(self.selected_folders[0]) if self.selected_folders else (APP_CONFIG.get("last_folder") or None)
        chosen = filedialog.askdirectory(parent=self.top, title="Selecione uma pasta que NÃO deve ser analisada", initialdir=initial)
        if chosen:
            p_str = str(Path(chosen).resolve())
            if not any(r.get("path", "").lower() == p_str.lower() for r in self.excluded_rules):
                self.excluded_rules.append({"path": p_str, "include_subdirs": True, "enabled": True})
                self._refresh_excluded_listbox()

    def _add_excluded_pattern(self):
        val = simpledialog.askstring(
            "Nome ou Padrão de Pasta a Ignorar",
            "Digite o nome da subpasta que deve ser ignorada (ex: WhatsApp Stickers, .thumbnails, Lixeira):",
            parent=self.top
        )
        if val and val.strip():
            clean_val = val.strip()
            if not any(r.get("path", "").lower() == clean_val.lower() for r in self.excluded_rules):
                self.excluded_rules.append({"path": clean_val, "include_subdirs": True, "enabled": True})
                self._refresh_excluded_listbox()

    def _remove_excluded(self):
        sel_indices = list(self.excl_listbox.curselection())
        if not sel_indices:
            return
        for idx in reversed(sorted(sel_indices)):
            if 0 <= idx < len(self.excluded_rules):
                self.excluded_rules.pop(idx)
        self._refresh_excluded_listbox()

    def _open_advanced_excluded_dialog(self):
        from src.ui.dialogs.excluded_folders_dialog import ExcludedFoldersDialog
        ExcludedFoldersDialog(self.gui)
        # Recarrega regras que possam ter sido alteradas no diálogo avançado
        raw_excluded = APP_CONFIG.get("excluded_folders", [])
        self.excluded_rules = [dict(r) if isinstance(r, dict) else {"path": r, "include_subdirs": True, "enabled": True} for r in raw_excluded]
        self._refresh_excluded_listbox()

    def _confirm_and_start(self):
        if not self.selected_folders:
            messagebox.showwarning(
                "Nenhuma Pasta Selecionada",
                "Por favor, adicione pelo menos uma pasta para iniciar a análise.",
                parent=self.top
            )
            return

        enable_h = self.var_enable_hash.get()
        enable_c = self.var_enable_clip.get()
        enable_m = self.var_enable_memes.get()

        if not enable_h and not enable_c and not enable_m:
            messagebox.showwarning(
                "Nenhum Tipo de Busca Selecionado",
                "Por favor, selecione pelo menos um tipo de busca (Hash, CLIP ou Memes) para continuar.",
                parent=self.top
            )
            return

        # Salva nas configurações persistentes
        folder_strs = [str(p) for p in self.selected_folders]
        APP_CONFIG["last_folders"] = folder_strs
        APP_CONFIG["last_folder"] = folder_strs[-1]
        APP_CONFIG["enable_hash"] = enable_h
        APP_CONFIG["enable_clip"] = enable_c
        APP_CONFIG["enable_web_detector"] = enable_m
        APP_CONFIG["excluded_folders"] = self.excluded_rules
        save_app_config(APP_CONFIG)

        if hasattr(self.gui, "enable_hash_var"):
            self.gui.enable_hash_var.set(enable_h)
        if hasattr(self.gui, "enable_clip_var"):
            self.gui.enable_clip_var.set(enable_c)
        if hasattr(self.gui, "web_detector_menu_var"):
            self.gui.web_detector_menu_var.set(enable_m)

        selected_copy = list(self.selected_folders)
        try:
            self.top.destroy()
        except Exception:
            pass

        if self.on_start:
            self.on_start(selected_copy)

