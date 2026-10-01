import os
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
try:
    import torch
except ImportError:
    torch = None

from src.config import (
    APP_CONFIG, save_app_config, get_current_cache_dir,
    get_cache_size_bytes, format_size, log_error
)
from src.hardware import get_system_disks_info, get_drive_free_space, get_system_ram_info


class PerformanceDialog:
    """Janela de controle de desempenho do sistema, limites de memória RAM, threads de CPU e migração de cache."""
    def __init__(self, gui):
        self.gui = gui
        self.engine = gui.engine
        self.top = tk.Toplevel(gui.root)
        self.top.title("Desempenho & Armazenamento de Cache")
        self.top.geometry("700x700")
        self.top.minsize(650, 620)
        self.top.transient(gui.root)
        try:
            if gui.root.winfo_viewable():
                self.top.grab_set()
        except Exception:
            pass

        self.cache_dir_var = tk.StringVar(value=str(get_current_cache_dir()))
        self.ram_limit_var = tk.IntVar(value=int(APP_CONFIG.get("max_ram_pct", 80)))
        self.ram_progress_var = tk.IntVar(value=50)
        self.workers_var = tk.IntVar(value=int(APP_CONFIG.get("cpu_workers", os.cpu_count() or 8)))
        self.batch_var = tk.IntVar(value=int(APP_CONFIG.get("batch_size", 48 if torch.cuda.is_available() else 16)))
        self.web_detector_var = tk.BooleanVar(value=bool(APP_CONFIG.get("enable_web_detector", True)))

        self.cache_desc_var = tk.StringVar(value="")
        self.cache_size_var = tk.StringVar(value="")
        self.ram_status_var = tk.StringVar(value="")
        self.ram_limit_desc_var = tk.StringVar(value="")

        self._build_ui()
        self._refresh_disk_info()
        self._refresh_ram_info()

    def _build_ui(self):
        main = ttk.Frame(self.top, padding=(16, 12, 16, 12))
        main.pack(fill="both", expand=True)

        head = ttk.Frame(main)
        head.pack(fill="x", pady=(0, 10))
        ttk.Label(head, text="⚡ Ajustes de Desempenho e Localização de Cache", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(head, text="Otimize o uso de CPU, RAM e GPU e escolha a unidade de disco mais rápida (SSD) para os índices.", font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

        # --- SEÇÃO 1: CACHE E DISCOS ---
        cache_frame = ttk.LabelFrame(main, text=" 💾 Localização do Cache de Indexação & Discos ", padding=10)
        cache_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(cache_frame, text="Unidades de Disco Detectadas no Computador:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        disks_box = ttk.Frame(cache_frame, padding=(4, 2, 4, 6))
        disks_box.pack(fill="x")

        disks = get_system_disks_info()
        for dl, dinfo in sorted(disks.items()):
            free_b, tot_b = get_drive_free_space(dl)
            free_gb = free_b / (1024 ** 3)
            tot_gb = tot_b / (1024 ** 3)
            tag = "⚡ Rápido (Recomendado para Cache)" if dinfo.get("is_ssd") else "🐢 Mecânico (Lento)"
            text = f"• {dl}: [{dinfo.get('type_desc', '')}] {dinfo.get('model', '')} — {free_gb:.1f} GB livres de {tot_gb:.1f} GB  →  {tag}"
            ttk.Label(disks_box, text=text, font=("Segoe UI", 8)).pack(anchor="w", pady=1)

        curr_row = ttk.Frame(cache_frame)
        curr_row.pack(fill="x", pady=4)
        ttk.Label(curr_row, text="Pasta atual do cache:", font=("Segoe UI", 9, "bold")).pack(side="left")
        entry = ttk.Entry(curr_row, textvariable=self.cache_dir_var, font=("Consolas", 8), state="readonly")
        entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(curr_row, text="📁 Mover / Alterar...", command=self._choose_and_migrate_cache).pack(side="right")

        info_row = ttk.Frame(cache_frame)
        info_row.pack(fill="x", pady=(2, 2))
        ttk.Label(info_row, textvariable=self.cache_desc_var, font=("Segoe UI", 8, "bold")).pack(side="left")
        ttk.Label(info_row, textvariable=self.cache_size_var, font=("Segoe UI", 8), foreground="#0066cc").pack(side="right")

        # --- SEÇÃO 2: MEMÓRIA RAM ---
        ram_frame = ttk.LabelFrame(main, text=" 🧠 Memória RAM & Pre-fetching Paralelo ", padding=10)
        ram_frame.pack(fill="x", pady=(0, 10))

        ram_top = ttk.Frame(ram_frame)
        ram_top.pack(fill="x")
        ttk.Label(ram_top, textvariable=self.ram_status_var, font=("Segoe UI", 9)).pack(side="left")
        ttk.Label(ram_top, textvariable=self.ram_limit_desc_var, font=("Segoe UI", 9, "bold"), foreground="#007acc").pack(side="right")

        self.ram_bar = ttk.Progressbar(ram_frame, variable=self.ram_progress_var, maximum=100)
        self.ram_bar.pack(fill="x", pady=(4, 6))

        slider_row = ttk.Frame(ram_frame)
        slider_row.pack(fill="x")
        ttk.Label(slider_row, text="Limite Máximo de Uso de RAM (Total do Windows):", font=("Segoe UI", 9)).pack(side="left")
        scale = ttk.Scale(slider_row, from_=50, to=95, variable=self.ram_limit_var, command=self._on_ram_slider_change)
        scale.pack(side="left", fill="x", expand=True, padx=10)
        self.ram_val_lbl = ttk.Label(slider_row, text=f"{self.ram_limit_var.get()}%", font=("Segoe UI", 9, "bold"), width=5)
        self.ram_val_lbl.pack(side="right")

        ttk.Label(
            ram_frame,
            text="💡 O pre-fetcher mantém lotes de imagens decodificadas na RAM para alimentar a GPU a 100% de uso contínuo.\n"
                 "Ao atingir a porcentagem definida (ex: 80%), o consumo é pausado, impedindo que o Windows fique sem memória.",
            font=("Segoe UI", 8, "italic"),
            foreground="#555"
        ).pack(anchor="w", pady=(4, 0))

        # --- SEÇÃO 3: CPU & GPU EM 2 COLUNAS ---
        cols = ttk.Frame(main)
        cols.pack(fill="x", pady=(0, 12))

        # CPU
        cpu_col = ttk.LabelFrame(cols, text=" ⚙️ Processador (CPU) ", padding=8)
        cpu_col.pack(side="left", fill="both", expand=True, padx=(0, 6))

        cpu_count = os.cpu_count() or 8
        ttk.Label(cpu_col, text=f"Processadores Lógicos: {cpu_count} threads", font=("Segoe UI", 8)).pack(anchor="w")

        cpu_row = ttk.Frame(cpu_col)
        cpu_row.pack(fill="x", pady=4)
        ttk.Label(cpu_row, text="Threads de Leitura/Decod.:", font=("Segoe UI", 9)).pack(side="left")
        ttk.Spinbox(cpu_row, from_=2, to=32, textvariable=self.workers_var, width=5).pack(side="right")

        ttk.Label(
            cpu_col,
            text=f"Decodificação paralela PIL e hashes.\nRecomendado: {cpu_count} threads.",
            font=("Segoe UI", 8, "italic"),
            foreground="#555"
        ).pack(anchor="w", pady=(2, 0))

        # GPU
        gpu_col = ttk.LabelFrame(cols, text=" 🎮 Placa de Vídeo (GPU) ", padding=8)
        gpu_col.pack(side="right", fill="both", expand=True, padx=(6, 0))

        if torch is not None and hasattr(torch, "cuda") and torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            gpu_desc = f"{gpu_name} ({vram_gb:.1f} GB VRAM)"
        else:
            gpu_desc = "Processamento em CPU (Sem CUDA)"

        ttk.Label(gpu_col, text=f"Dispositivo: {gpu_desc}", font=("Segoe UI", 8)).pack(anchor="w")

        gpu_row = ttk.Frame(gpu_col)
        gpu_row.pack(fill="x", pady=4)
        ttk.Label(gpu_row, text="Imagens por Lote (Batch):", font=("Segoe UI", 9)).pack(side="left")
        ttk.Spinbox(gpu_row, from_=8, to=128, increment=8, textvariable=self.batch_var, width=5).pack(side="right")

        ttk.Label(
            gpu_col,
            text="Lote otimizado para a GPU (~750 MB VRAM).\nEvita sobrecarga e mantém a GPU rápida.",
            font=("Segoe UI", 8, "italic"),
            foreground="#555"
        ).pack(anchor="w", pady=(2, 0))

        # --- SEÇÃO 4: ANÁLISE DE MEMES E IMAGENS DA WEB ---
        web_frame = ttk.LabelFrame(main, text=" 🌐 Detecção de Memes & Fotos da Web ", padding=10)
        web_frame.pack(fill="x", pady=(0, 10))

        ttk.Checkbutton(
            web_frame,
            text="Habilitar análise e classificação Zero-Shot de Memes, Banners e Fotos da Web",
            variable=self.web_detector_var
        ).pack(anchor="w")

        ttk.Label(
            web_frame,
            text="💡 Identifica e agrupa memes, capturas de tela e imagens baixadas da internet via embeddings CLIP.\n"
                 "Desmarque para acelerar a indexação caso queira comparar apenas fotos parecidas e duplicatas.",
            font=("Segoe UI", 8, "italic"),
            foreground="#555"
        ).pack(anchor="w", pady=(2, 0))

        # --- BOTÕES FINAIS ---
        btn_bar = ttk.Frame(main)
        btn_bar.pack(fill="x", pady=(4, 0))

        ttk.Button(btn_bar, text="💾 Salvar e Aplicar", command=self._save_and_apply).pack(side="right", padx=4)
        ttk.Button(btn_bar, text="Cancelar", command=self.top.destroy).pack(side="right", padx=4)

    def _refresh_disk_info(self):
        curr = Path(self.cache_dir_var.get()).resolve()
        dl = curr.drive.rstrip(':').upper()
        disks = get_system_disks_info()
        desc = disks.get(dl, {}).get("type_desc", "Disco Local")
        free_b, tot_b = get_drive_free_space(dl)
        free_gb = free_b / (1024 ** 3)
        tot_gb = tot_b / (1024 ** 3)
        self.cache_desc_var.set(f"Unidade Atual: {dl}: ({desc}) — {free_gb:.1f} GB livres de {tot_gb:.1f} GB")
        sz = get_cache_size_bytes(curr)
        self.cache_size_var.set(f"Tamanho do cache: {format_size(sz)}")

    def _refresh_ram_info(self):
        ram = get_system_ram_info()
        self.ram_progress_var.set(ram["load_pct"])
        self.ram_status_var.set(
            f"RAM do Sistema: {ram['used_gb']:.1f} GB de {ram['total_gb']:.1f} GB em uso ({ram['load_pct']}%)"
        )
        lim = self.ram_limit_var.get()
        lim_gb = lim * ram['total_gb'] / 100.0
        self.ram_limit_desc_var.set(f"Teto: {lim}% (aprox. {lim_gb:.1f} GB)")
        self.ram_val_lbl.config(text=f"{lim}%")

    def _on_ram_slider_change(self, val):
        try:
            lim = int(float(val))
            self.ram_limit_var.set(lim)
            self.ram_val_lbl.config(text=f"{lim}%")
            ram = get_system_ram_info()
            lim_gb = lim * ram['total_gb'] / 100.0
            self.ram_limit_desc_var.set(f"Teto: {lim}% (aprox. {lim_gb:.1f} GB)")
        except Exception:
            pass

    def _choose_and_migrate_cache(self):
        current_cache = Path(self.cache_dir_var.get()).resolve()
        new_folder = filedialog.askdirectory(
            parent=self.top,
            title="Selecione a pasta para armazenar o cache de indexação",
            initialdir=str(current_cache)
        )
        if not new_folder:
            return
        new_cache = Path(new_folder).resolve()
        if new_cache == current_cache:
            messagebox.showinfo("Aviso", "O diretório selecionado já é o diretório de cache atual.", parent=self.top)
            return

        cache_size = get_cache_size_bytes(current_cache)
        files_to_move = list(current_cache.iterdir()) if current_cache.exists() else []

        if files_to_move:
            ans = messagebox.askyesno(
                "Mover Dados de Cache?",
                f"Foram encontrados {format_size(cache_size)} em arquivos de índice na pasta atual.\n\n"
                f"Deseja MOVER esses arquivos para a nova pasta?\n\n"
                f"• Origem: {current_cache}\n"
                f"• Destino: {new_cache}\n\n"
                f"Escolha 'Sim' para transferir os dados e não precisar reprocessar as fotos.\n"
                f"Escolha 'Não' para apenas alterar o caminho sem mover os arquivos.",
                parent=self.top
            )
            if ans:
                new_cache.mkdir(parents=True, exist_ok=True)
                err_count = 0
                for item in files_to_move:
                    dest = new_cache / item.name
                    try:
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                            shutil.rmtree(item)
                        else:
                            shutil.copy2(item, dest)
                            item.unlink()
                    except Exception as e:
                        log_error(f"Erro ao mover arquivo de cache {item} para {dest}", e)
                        err_count += 1
                if err_count > 0:
                    messagebox.showwarning(
                        "Aviso na Migração",
                        f"{err_count} arquivos não puderam ser movidos (detalhes no log).",
                        parent=self.top
                    )

        self.cache_dir_var.set(str(new_cache))
        APP_CONFIG["cache_dir"] = str(new_cache)
        save_app_config(APP_CONFIG)
        self._refresh_disk_info()

        if self.engine:
            try:
                self.engine.load_partial_state()
                self.gui.refresh_results(keep_page=True)
            except Exception:
                pass

        messagebox.showinfo(
            "Cache Alterado com Sucesso",
            f"A nova pasta de cache foi configurada para:\n{new_cache}",
            parent=self.top
        )

    def _save_and_apply(self):
        lim = int(self.ram_limit_var.get())
        workers = int(self.workers_var.get())
        batch = int(self.batch_var.get())
        enable_web = bool(self.web_detector_var.get())
        cache_dir = str(Path(self.cache_dir_var.get()).resolve())

        APP_CONFIG["max_ram_pct"] = max(40, min(95, lim))
        APP_CONFIG["cpu_workers"] = max(1, min(64, workers))
        APP_CONFIG["batch_size"] = max(4, min(256, batch))
        APP_CONFIG["enable_web_detector"] = enable_web
        APP_CONFIG["cache_dir"] = cache_dir

        save_app_config(APP_CONFIG)

        if self.engine:
            self.engine.max_ram_pct = APP_CONFIG["max_ram_pct"]
            self.engine.workers = APP_CONFIG["cpu_workers"]
            self.engine.batch = APP_CONFIG["batch_size"]
            self.engine.enable_web_detector = enable_web

        if hasattr(self.gui, "refresh_results"):
            try:
                self.gui.refresh_results(keep_page=True)
            except Exception:
                pass

        status_web = "Ativada" if enable_web else "Desativada"
        messagebox.showinfo(
            "Configurações Salvas",
            "Configurações de desempenho e análise atualizadas com sucesso:\n\n"
            f"• Limite de RAM do Sistema: {APP_CONFIG['max_ram_pct']}%\n"
            f"• Threads de CPU: {APP_CONFIG['cpu_workers']}\n"
            f"• Lote GPU: {APP_CONFIG['batch_size']}\n"
            f"• Detecção de Memes & Fotos da Web: {status_web}\n"
            f"• Local do Cache: {APP_CONFIG['cache_dir']}",
            parent=self.top
        )
        self.top.destroy()
