import tkinter as tk
from tkinter import ttk, messagebox
from collections import defaultdict
from pathlib import Path

from src.config import format_size, is_in_or_subdir, normalize_dir_str
from src.photo_engine import partition_groups_by_priority
from src.ui.dialogs.deletion_progress_dialog import DeletionProgressDialog


class FolderRulesDialog:
    def __init__(self, gui):
        self.gui = gui
        self.engine = gui.engine
        self.top = tk.Toplevel(gui.root)
        self.top.title("Regras de Pastas para Fotos Duplicadas")
        self.top.geometry("880x640")
        self.top.minsize(740, 520)
        self.top.transient(gui.root)
        try:
            if gui.root.winfo_viewable():
                self.top.grab_set()
        except Exception:
            pass

        self.include_subdirs = tk.BooleanVar(value=gui.include_subdirs)
        self.checked_keep = set(gui.keep_folders)
        self.checked_del = set(gui.del_folders)

        self._load_folders_with_duplicates()
        self._build_ui()

    def _load_folders_with_duplicates(self):
        folder_counts = defaultdict(int)
        all_duplicate_photos = set()

        if not self.engine:
            self.all_folders = []
            self.folder_counts = folder_counts
            return

        for g in self.engine.exact:
            for p in g:
                all_duplicate_photos.add(p)
        for g in self.engine.phash_groups:
            for p in g:
                all_duplicate_photos.add(p)
        for g in self.engine.groups():
            for p in g:
                all_duplicate_photos.add(p)

        all_paths_in_tree = set()
        root_resolved = Path(self.engine.root).resolve() if (hasattr(self.engine, "root") and self.engine.root) else None

        for p in all_duplicate_photos:
            folder = Path(p).parent.resolve()
            folder_counts[folder] += 1
            curr = folder
            while True:
                all_paths_in_tree.add(curr)
                if (root_resolved and curr == root_resolved) or curr.parent == curr:
                    break
                curr = curr.parent

        self.all_folders = sorted(list(all_paths_in_tree), key=lambda x: str(x).lower())
        self.folder_counts = folder_counts

    def _build_ui(self):
        head_frame = ttk.Frame(self.top, padding=(12, 10, 12, 4))
        head_frame.pack(fill="x")

        ttk.Label(head_frame, text="Gerenciamento de Duplicatas por Pastas e Subdiretórios", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(head_frame, text="Defina quais pastas devem ter suas fotos PRESERVADAS (Prioritárias) e quais devem ter duplicatas EXCLUÍDAS.", font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 2))

        opt_frame = ttk.Frame(self.top, padding=(12, 2, 12, 6))
        opt_frame.pack(fill="x")
        ttk.Checkbutton(
            opt_frame,
            text="Aplicar regras automaticamente aos subdiretórios (hierarquia completa)",
            variable=self.include_subdirs,
            command=self._update_preview_counts
        ).pack(side="left")

        ttk.Button(
            opt_frame,
            text="🚫 Configurar Pastas Excluídas da Varredura...",
            command=self._open_excluded_dialog
        ).pack(side="right")

        paned = ttk.PanedWindow(self.top, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=12, pady=4)

        # KEEP
        left_frame = ttk.LabelFrame(paned, text=" 🟢 Pastas com Fotos a MANTER (Prioritárias) ", padding=6)
        paned.add(left_frame, weight=1)

        self.tree_keep = ttk.Treeview(left_frame, columns=("count",), selectmode="none")
        self.tree_keep.heading("#0", text="Pasta / Subdiretório", anchor="w")
        self.tree_keep.heading("count", text="Fotos Dup.", anchor="e")
        self.tree_keep.column("#0", stretch=True, width=280)
        self.tree_keep.column("count", width=70, anchor="e")
        sb_k = ttk.Scrollbar(left_frame, orient="vertical", command=self.tree_keep.yview)
        self.tree_keep.configure(yscrollcommand=sb_k.set)
        self.tree_keep.pack(side="left", fill="both", expand=True)
        sb_k.pack(side="right", fill="y")
        self.tree_keep.bind("<Button-1>", self._on_click_keep)

        # DELETE
        right_frame = ttk.LabelFrame(paned, text=" 🔴 Pastas com Duplicatas a EXCLUIR ", padding=6)
        paned.add(right_frame, weight=1)

        self.tree_del = ttk.Treeview(right_frame, columns=("count",), selectmode="none")
        self.tree_del.heading("#0", text="Pasta / Subdiretório", anchor="w")
        self.tree_del.heading("count", text="Fotos Dup.", anchor="e")
        self.tree_del.column("#0", stretch=True, width=280)
        self.tree_del.column("count", width=70, anchor="e")
        sb_d = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree_del.yview)
        self.tree_del.configure(yscrollcommand=sb_d.set)
        self.tree_del.pack(side="left", fill="both", expand=True)
        sb_d.pack(side="right", fill="y")
        self.tree_del.bind("<Button-1>", self._on_click_del)

        self._populate_trees()

        info_bar = ttk.Frame(self.top, padding=(12, 6, 12, 4))
        info_bar.pack(fill="x")
        self.summary_lbl = ttk.Label(info_bar, text="", font=("Segoe UI", 9, "bold"))
        self.summary_lbl.pack(side="left")

        actions_bar = ttk.Frame(self.top, padding=(12, 6, 12, 12))
        actions_bar.pack(fill="x")

        ttk.Button(actions_bar, text="Salvar e Fechar", command=self.save_and_close).pack(side="right", padx=4)
        ttk.Button(actions_bar, text="Cancelar", command=self.top.destroy).pack(side="right", padx=4)
        self.del_btn = ttk.Button(actions_bar, text="🗑️ Executar Exclusão Segura (Enviar para Lixeira)", command=self.execute_deletion)
        self.del_btn.pack(side="left")

        self._update_preview_counts()

    def _populate_trees(self):
        self.node_to_path_keep = {}
        self.node_to_path_del = {}
        self.path_to_node_keep = {}
        self.path_to_node_del = {}

        path_to_keep_id = {}
        path_to_del_id = {}
        root_resolved = Path(self.engine.root).resolve() if (self.engine and hasattr(self.engine, "root") and self.engine.root) else None

        for folder in self.all_folders:
            f_norm = normalize_dir_str(folder)
            cnt = self.folder_counts.get(folder, 0)
            cnt_str = f"{cnt}" if cnt > 0 else "-"

            if folder == root_resolved:
                parent_k = ""
                parent_d = ""
                display_name = f"{folder.name or str(folder)} (Raiz)"
            else:
                p_norm = normalize_dir_str(folder.parent)
                parent_k = path_to_keep_id.get(p_norm, "")
                parent_d = path_to_del_id.get(p_norm, "")
                display_name = folder.name

            checked_k = "☑ " if f_norm in self.checked_keep else "☐ "
            nid_k = self.tree_keep.insert(parent_k, "end", text=checked_k + display_name, values=(cnt_str,), open=True)
            path_to_keep_id[f_norm] = nid_k
            self.node_to_path_keep[nid_k] = f_norm
            self.path_to_node_keep[f_norm] = nid_k

            checked_d = "☑ " if f_norm in self.checked_del else "☐ "
            nid_d = self.tree_del.insert(parent_d, "end", text=checked_d + display_name, values=(cnt_str,), open=True)
            path_to_del_id[f_norm] = nid_d
            self.node_to_path_del[nid_d] = f_norm
            self.path_to_node_del[f_norm] = nid_d

    def _propagate_cascade(self, clicked_folder, is_marking, target_set, other_set):
        descendants = [
            f for f in self.all_folders
            if is_in_or_subdir(Path(f) / "dummy.jpg", clicked_folder, include_subdirs=True)
        ]
        ancestors = []
        curr = Path(clicked_folder)
        while curr.parent != curr:
            curr = curr.parent
            ancestors.append(normalize_dir_str(curr))

        dl = normalize_dir_str(clicked_folder)

        if is_marking:
            target_set.add(dl)
            for child in descendants:
                target_set.add(normalize_dir_str(child))
                other_set.discard(normalize_dir_str(child))
            other_set.discard(dl)
            for anc in ancestors:
                other_set.discard(anc)
        else:
            target_set.discard(dl)
            for child in descendants:
                target_set.discard(normalize_dir_str(child))
            for anc in ancestors:
                target_set.discard(anc)

    def _on_click_keep(self, event):
        item_id = self.tree_keep.identify_row(event.y)
        if not item_id:
            return
        folder_str = self.node_to_path_keep.get(item_id)
        if not folder_str:
            return

        is_marking = folder_str not in self.checked_keep
        if self.include_subdirs.get():
            self._propagate_cascade(folder_str, is_marking, self.checked_keep, self.checked_del)
        else:
            if is_marking:
                self.checked_keep.add(folder_str)
                self.checked_del.discard(folder_str)
            else:
                self.checked_keep.discard(folder_str)

        self._refresh_tree_labels()
        self._update_preview_counts()

    def _on_click_del(self, event):
        item_id = self.tree_del.identify_row(event.y)
        if not item_id:
            return
        folder_str = self.node_to_path_del.get(item_id)
        if not folder_str:
            return

        is_marking = folder_str not in self.checked_del
        if self.include_subdirs.get():
            self._propagate_cascade(folder_str, is_marking, self.checked_del, self.checked_keep)
        else:
            if is_marking:
                self.checked_del.add(folder_str)
                self.checked_keep.discard(folder_str)
            else:
                self.checked_del.discard(folder_str)

        self._refresh_tree_labels()
        self._update_preview_counts()

    def _refresh_tree_labels(self):
        root_resolved = Path(self.engine.root).resolve() if (self.engine and hasattr(self.engine, "root") and self.engine.root) else None
        for folder in self.all_folders:
            f_norm = normalize_dir_str(folder)
            display_name = f"{folder.name or str(folder)} (Raiz)" if folder == root_resolved else folder.name

            nid_k = self.path_to_node_keep.get(f_norm)
            if nid_k:
                box_k = "☑ " if f_norm in self.checked_keep else "☐ "
                self.tree_keep.item(nid_k, text=box_k + display_name)

            nid_d = self.path_to_node_del.get(f_norm)
            if nid_d:
                box_d = "☑ " if f_norm in self.checked_del else "☐ "
                self.tree_del.item(nid_d, text=box_d + display_name)

    def _update_preview_counts(self):
        if not self.engine:
            self.summary_lbl.config(
                text="Nenhuma análise de fotos ativa no momento."
            )
            return

        all_groups = []
        for g in self.engine.exact:
            all_groups.append(g)
        for g in self.engine.phash_groups:
            all_groups.append(g)
        for g in self.engine.groups():
            all_groups.append(g)

        to_keep, to_delete = partition_groups_by_priority(
            all_groups, self.checked_keep, self.checked_del, self.include_subdirs.get()
        )

        total_bytes = 0
        for p in to_delete:
            sz = self.engine.meta.get(str(p), {}).get("size") if (self.engine and hasattr(self.engine, "meta")) else None
            if not sz and Path(p).exists():
                try:
                    sz = Path(p).stat().st_size
                except Exception:
                    sz = 0
            total_bytes += sz or 0

        self.summary_lbl.config(
            text=f"Fotos a excluir identificadas: {len(to_delete):,} fotos ({format_size(total_bytes)}) | Fotos mantidas prioritárias: {len(to_keep):,}"
        )

    def execute_deletion(self):
        if not self.engine:
            messagebox.showinfo("Nenhuma Foto Carregada", "Selecione e analise uma pasta antes de executar regras.", parent=self.top)
            return

        all_groups = []
        for g in self.engine.exact:
            all_groups.append(g)
        for g in self.engine.phash_groups:
            all_groups.append(g)
        for g in self.engine.groups():
            all_groups.append(g)

        to_keep, to_delete = partition_groups_by_priority(
            all_groups, self.checked_keep, self.checked_del, self.include_subdirs.get()
        )

        if not to_delete:
            messagebox.showinfo("Nenhuma Foto a Excluir", "Com as regras atuais, nenhuma foto elegível para exclusão foi encontrada.")
            return

        total_bytes = sum(self.engine.meta.get(str(p), {}).get("size") or 0 for p in to_delete)
        confirm_msg = (
            f"Atenção: Você está prestes a excluir duplicatas com base nas regras configuradas.\n\n"
            f"• Fotos que serão EXCLUÍDAS (Lixeira): {len(to_delete):,}\n"
            f"• Espaço a ser liberado: {format_size(total_bytes)}\n"
            f"• Fotos ORIGINAIS que serão mantidas: {len(to_keep):,}\n\n"
            f"Deseja prosseguir com o envio para a Lixeira do Windows?"
        )
        if not messagebox.askyesno("Confirmar Exclusão em Massa", confirm_msg):
            return

        self.gui.keep_folders = set(self.checked_keep)
        self.gui.del_folders = set(self.checked_del)
        self.gui.include_subdirs = self.include_subdirs.get()

        def on_file_deleted(p):
            self.gui.thumb_cache.pop(str(p), None)
            self.gui.info_cache.pop(str(p), None)

        def on_complete(deleted_count, error_count, cancelled):
            if self.gui.engine:
                self.gui.engine.load_partial_state()
            self.gui.refresh_results(keep_page=False)
            res_msg = f"{deleted_count:,} fotos enviadas para a Lixeira."
            if error_count > 0:
                res_msg += f"\n{error_count} erros registrados (veja o log)."
            messagebox.showinfo("Exclusão Concluída", res_msg)
            try:
                self.top.grab_release()
            except Exception:
                pass
            self.top.destroy()

        DeletionProgressDialog(self.top, to_delete, meta=self.engine.meta, on_file_deleted=on_file_deleted, on_complete=on_complete)

    def _open_excluded_dialog(self):
        from src.ui.dialogs.excluded_folders_dialog import ExcludedFoldersDialog
        ExcludedFoldersDialog(self.gui)

    def save_and_close(self):
        self.gui.keep_folders = set(self.checked_keep)
        self.gui.del_folders = set(self.checked_del)
        self.gui.include_subdirs = self.include_subdirs.get()
        self.gui.refresh_results(keep_page=True)
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.destroy()
