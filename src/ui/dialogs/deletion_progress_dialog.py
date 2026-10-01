import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from send2trash import send2trash

from src.config import format_size, log_error
from src.photo_engine import upgrade_photo_in_folder


class DeletionProgressDialog:
    """Janela de progresso com cancelamento seguro durante exclusão em massa e promoção de qualidade."""
    def __init__(self, parent, paths_to_delete, upgrades=None, meta=None, on_file_deleted=None, on_complete=None):
        self.parent = parent
        self.paths = list(paths_to_delete)
        self.upgrades = list(upgrades or [])
        self.total = len(self.upgrades) + len(self.paths)
        self.meta = meta or {}
        self.on_file_deleted = on_file_deleted
        self.on_complete = on_complete

        self.upgraded_count = 0
        self.deleted_count = 0
        self.error_count = 0
        self.bytes_freed = 0
        self.stop_event = threading.Event()
        self.started_time = time.time()

        self.top = tk.Toplevel(parent)
        self.top.title("Exclusão e Promoção de Fotos — Lixeira do Windows")
        self.top.geometry("540x270")
        self.top.minsize(480, 240)
        self.top.transient(parent)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", self.request_cancel)

        self._build_ui()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def _build_ui(self):
        pad = ttk.Frame(self.top, padding=(20, 16, 20, 16))
        pad.pack(fill="both", expand=True)

        self.title_lbl = ttk.Label(pad, text="Enviando fotos para a Lixeira do Windows...", font=("Segoe UI", 10, "bold"))
        self.title_lbl.pack(anchor="w", pady=(0, 8))

        self.progress = ttk.Progressbar(pad, maximum=max(1, self.total), mode="determinate")
        self.progress.pack(fill="x", pady=(0, 6))

        self.file_lbl = ttk.Label(pad, text="Preparando...", font=("Segoe UI", 8), foreground="#555555")
        self.file_lbl.pack(anchor="w", pady=(0, 4))

        self.stats_lbl = ttk.Label(pad, text=f"0 / {self.total} fotos", font=("Segoe UI", 9))
        self.stats_lbl.pack(anchor="w", pady=(0, 12))

        btn_row = ttk.Frame(pad)
        btn_row.pack(fill="x")
        self.cancel_btn = ttk.Button(btn_row, text="Cancelar", command=self.request_cancel)
        self.cancel_btn.pack(side="right")

    def request_cancel(self):
        if not self.stop_event.is_set():
            if messagebox.askyesno("Confirmar Interrupção", "Deseja interromper a exclusão agora?\n\nAs fotos já movidas permanecerão na Lixeira.", parent=self.top):
                self.stop_event.set()
                self.cancel_btn.config(text="Interrompendo...", state="disabled")
                self.file_lbl.config(text="Aguardando operação atual concluir...")

    def _worker(self):
        processed = 0

        # 1. Executar promoções de maior qualidade para a pasta mantida
        for larger, smaller in self.upgrades:
            if self.stop_event.is_set():
                break
            processed += 1
            cur_name = Path(larger).name
            dest_name = Path(smaller).parent.name
            self.top.after(0, self._update_ui, processed, f"Promovendo {cur_name} para {dest_name}...")

            try:
                if Path(larger).exists() and Path(smaller).exists():
                    upgraded_target = upgrade_photo_in_folder(larger, smaller)
                    self.upgraded_count += 1
                    if self.on_file_deleted:
                        self.on_file_deleted(smaller)
                        self.on_file_deleted(larger)
            except Exception as e:
                self.error_count += 1
                log_error(f"Erro ao promover {larger} para {smaller}", e)

        # 2. Executar exclusões regulares das duplicatas
        for p in self.paths:
            if self.stop_event.is_set():
                break

            sz = 0
            try:
                sz = self.meta.get(str(p), {}).get("size") or (Path(p).stat().st_size if Path(p).exists() else 0)
            except Exception:
                pass

            processed += 1
            cur_filename = Path(p).name
            self.top.after(0, self._update_ui, processed, f"Excluindo: {cur_filename}")

            try:
                if Path(p).exists():
                    send2trash(p)
                    self.deleted_count += 1
                    self.bytes_freed += sz
                    if self.on_file_deleted:
                        self.on_file_deleted(p)
            except Exception as e:
                self.error_count += 1
                log_error(f"Erro ao enviar {p} para a lixeira", e)

        self.top.after(0, self._finish)

    def _update_ui(self, count, action_text):
        if not self.top.winfo_exists():
            return
        self.progress["value"] = count
        self.file_lbl.config(text=action_text)
        stats_text = f"{count} / {self.total} fotos processadas • {format_size(self.bytes_freed)} liberados"
        if self.upgraded_count > 0:
            stats_text += f" • {self.upgraded_count} promoções"
        self.stats_lbl.config(text=stats_text)

    def _finish(self):
        if self.on_complete:
            try:
                self.on_complete(self.deleted_count, self.error_count, self.stop_event.is_set(), self.upgraded_count)
            except TypeError:
                self.on_complete(self.deleted_count, self.error_count, self.stop_event.is_set())
        if self.top.winfo_exists():
            self.top.destroy()
