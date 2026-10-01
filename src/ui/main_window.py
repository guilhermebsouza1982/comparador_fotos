import math
import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path
from PIL import Image, ImageTk

from src.config import (
    APP_CONFIG, LOG_FILE, PROGRAM_DIR, format_size, log_error, log_critical, save_app_config
)
from src.ui.styles import apply_app_styles
from src.ui.dialogs.performance_dialog import PerformanceDialog
from src.ui.dialogs.folder_rules_dialog import FolderRulesDialog
from src.ui.dialogs.excluded_folders_dialog import ExcludedFoldersDialog
from src.ui.menus import build_app_menus
from src.ui.views.grid_view import render_grid_view
from src.ui.views.fullscreen_view import render_fullscreen_comparison
from src.ui.actions import (
    remove_group_from_engine_collections,
    mark_group_different_action,
    trigger_single_photo_deletion,
    trigger_group_deletion,
    swap_photos_in_group,
    promote_largest_photo_action,
    keep_largest_and_delete_others_action,
    trigger_shadow_removal_action
)


class MainWindow:
    """Janela Principal modular do Comparador de Fotos com suporte a IA (CLIP, pHash, Zero-Shot Web Detector)."""
    def __init__(self, engine=None, processing=False, auto_open_selector=False):
        self.engine = engine
        self.processing = processing
        self.thumb_cache = {}
        self.info_cache = {}
        self.sharpness_cache = {}
        self.expanded_groups = set()
        self.keep_folders = set()
        self.del_folders = set()
        self.include_subdirs = True
        self._last_exact_count = 0
        self._last_edges_count = 0
        self._last_phash_count = 0
        self._last_web_count = 0
        self._last_progressive_refresh = 0
        self._slider_job = None
        self._poll_job = None
        self._spinner_job = None
        self._is_closing = False
        self._is_recalculating = False
        self._pending_threshold = None
        self._cached_clip_groups = None
        self._cached_web_groups = None

        self._spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._spinner_idx = 0
        self._processing_spinner_lbl = None

        self.current_page = 1
        self.page_size = 15
        self.active_tab = "clip"
        self.web_threshold = 0.50

        # Modo de visualização: "grid" ou "fullscreen_compare"
        self.view_mode = "grid"
        self.compare_group_idx = 0

        self.root = tk.Tk()
        self.root.title("Comparador de Fotos com IA — CLIP, pHash & Detecção Web")
        self.root.geometry("1320x900")
        self.root.minsize(1040, 700)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self._on_tk_exception

        try:
            ico_file = PROGRAM_DIR / "icon.ico"
            png_file = PROGRAM_DIR / "icon.png"
            if ico_file.exists():
                self.root.iconbitmap(default=str(ico_file))
            elif png_file.exists():
                self._icon_img = ImageTk.PhotoImage(file=str(png_file))
                self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass

        apply_app_styles(self.root)

        default_th = engine.threshold if engine is not None else float(APP_CONFIG.get("clip_threshold", 0.82))
        self.threshold = tk.DoubleVar(value=default_th)
        if engine is not None:
            init_status = "Iniciando processamento..." if processing else "Pronto"
        else:
            last_f = APP_CONFIG.get("last_folder", "")
            init_status = f"Pronto. Última pasta salva: {last_f}" if last_f else "Selecione uma pasta no menu Arquivo para iniciar a análise."
        self.status = tk.StringVar(value=init_status)
        self.info = tk.StringVar(value="")
        self.spinner_var = tk.StringVar(value="")
        self.page_label_var = tk.StringVar(value="Página 1")
        self.mode_btn_var = tk.StringVar(value="⛶ Comparativo em Tela Cheia")
        has_ai = False
        try:
            from src.dependency_installer import is_ai_available
            has_ai = is_ai_available()
        except Exception:
            has_ai = False

        self.enable_clip_var = tk.BooleanVar(value=bool(APP_CONFIG.get("enable_clip", True)) and has_ai)
        self.enable_hash_var = tk.BooleanVar(value=bool(APP_CONFIG.get("enable_hash", True)) or not has_ai)
        self.web_detector_menu_var = tk.BooleanVar(value=bool(APP_CONFIG.get("enable_web_detector", True)) and has_ai)
        self.confirm_delete_2s_var = tk.BooleanVar(value=bool(APP_CONFIG.get("confirm_delete_2s", True)))
        self.check_updates_startup_var = tk.BooleanVar(value=bool(APP_CONFIG.get("check_updates_on_startup", True)))
        self.max_groups_var = tk.IntVar(value=int(APP_CONFIG.get("max_active_groups", 0)))
        self.shadow_mode_var = tk.StringVar(value=str(APP_CONFIG.get("shadow_removal_mode", "deep")))

        build_app_menus(self)
        self._build_toolbar()
        self._build_status_and_tabs()
        self._build_content_area()
        self._bind_global_shortcuts()
        self._animate_spinner()

        if bool(APP_CONFIG.get("check_updates_on_startup", True)):
            self.root.after(2000, self._check_updates_background)

        if self.engine is None and not self.processing:
            self.render_page()
            if auto_open_selector:
                self._startup_job = self.root.after(100, self.open_folder_selector)

    # =========================================================================
    # TOOLBAR E CONTROLES DE TOPO
    # =========================================================================
    def _build_toolbar(self):
        top = ttk.Frame(self.root, padding=(8, 4, 8, 2))
        top.pack(fill="x")

        # Controles principais à esquerda
        ttk.Button(top, text="📁 Abrir...", command=self.select_folder_and_start).pack(side="left", padx=(0, 4))

        ttk.Label(top, text="CLIP:", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(2, 1))
        self.scale = ttk.Scale(top, from_=0.70, to=0.999, variable=self.threshold, command=self.on_slider, length=85)
        self.scale.pack(side="left", padx=(0, 2))
        self.scale.bind("<ButtonRelease-1>", lambda e: self._apply_threshold_change(immediate=True))
        cur_th = self.threshold.get()
        self.value_label = ttk.Label(top, text=f"{cur_th*100:.1f}%", font=("Segoe UI", 8, "bold"), width=5)
        self.value_label.pack(side="left", padx=(0, 4))

        ttk.Label(top, text="Limite:", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(2, 1))
        self.spin_max_groups = ttk.Spinbox(
            top,
            from_=0,
            to=5000,
            increment=25,
            textvariable=self.max_groups_var,
            width=4,
            command=self.on_max_groups_change
        )
        self.spin_max_groups.pack(side="left", padx=(0, 6))
        self.spin_max_groups.bind("<Return>", lambda e: self.on_max_groups_change())
        self.spin_max_groups.bind("<FocusOut>", lambda e: self.on_max_groups_change())

        self.btn_toggle_mode = ttk.Button(
            top, textvariable=self.mode_btn_var, command=self.toggle_view_mode
        )
        self.btn_toggle_mode.pack(side="left", padx=2)

        ttk.Button(top, text="⚡ Desempenho", command=self.open_performance_dialog).pack(side="left", padx=2)
        ttk.Button(top, text="📁 Regras", command=self.open_folder_rules).pack(side="left", padx=2)
        ttk.Button(top, text="🚫 Excluídas", command=self.open_excluded_folders_dialog).pack(side="left", padx=2)
        ttk.Button(top, text="📋 Log", command=self.view_log_file).pack(side="left", padx=2)

        # Controles à direita
        self.close_btn = ttk.Button(top, text="Fechar", command=self.close)
        self.close_btn.pack(side="right", padx=2)

        self.chk_confirm_delete = ttk.Checkbutton(
            top,
            text="Confirmar 2s",
            variable=self.confirm_delete_2s_var,
            command=self.toggle_confirm_delete_2s
        )
        self.chk_confirm_delete.pack(side="right", padx=4)

        self.stop_btn = ttk.Button(top, text="Interromper", command=self.stop)
        self.stop_btn.pack(side="right", padx=2)
        if self.engine is None or not self.processing:
            self.stop_btn.state(["disabled"])

    def _build_status_and_tabs(self):
        nav_bar = ttk.Frame(self.root, padding=(8, 2, 8, 2))
        nav_bar.pack(fill="x")

        # 1. Abas compactas na esquerda
        self.tab_clip_btn = ttk.Button(nav_bar, text="CLIP (0)", command=lambda: self.switch_tab("clip"))
        self.tab_clip_btn.pack(side="left", padx=1)
        self.tab_phash_btn = ttk.Button(nav_bar, text="pHash (0)", command=lambda: self.switch_tab("phash"))
        self.tab_phash_btn.pack(side="left", padx=1)
        self.tab_exact_btn = ttk.Button(nav_bar, text="SHA-256 (0)", command=lambda: self.switch_tab("exact"))
        self.tab_exact_btn.pack(side="left", padx=1)
        self.tab_web_btn = ttk.Button(nav_bar, text="🌐 Web (0)", command=lambda: self.switch_tab("web"))
        self.tab_web_btn.pack(side="left", padx=1)
        self.tab_all_btn = ttk.Button(nav_bar, text="Todos (0)", command=lambda: self.switch_tab("all"))
        self.tab_all_btn.pack(side="left", padx=1)

        ttk.Separator(nav_bar, orient="vertical").pack(side="left", fill="y", padx=5, pady=2)

        # 2. Paginação compacta no centro com botões de salto de 50 fotos/grupos
        self.btn_first = ttk.Button(nav_bar, text="⏮", width=3, command=self.page_first)
        self.btn_first.pack(side="left", padx=1)
        self.btn_step_back_50 = ttk.Button(nav_bar, text="⏪ 50", width=4, command=self.page_step_back_50)
        self.btn_step_back_50.pack(side="left", padx=1)
        self.btn_prev = ttk.Button(nav_bar, text="◀", width=3, command=self.page_prev)
        self.btn_prev.pack(side="left", padx=1)
        self.lbl_page = ttk.Label(nav_bar, textvariable=self.page_label_var, font=("Segoe UI", 9, "bold"))
        self.lbl_page.pack(side="left", padx=6)
        self.btn_next = ttk.Button(nav_bar, text="▶", width=3, command=self.page_next)
        self.btn_next.pack(side="left", padx=1)
        self.btn_step_fwd_50 = ttk.Button(nav_bar, text="50 ⏩", width=4, command=self.page_step_fwd_50)
        self.btn_step_fwd_50.pack(side="left", padx=1)
        self.btn_last = ttk.Button(nav_bar, text="⏭", width=3, command=self.page_last)
        self.btn_last.pack(side="left", padx=1)

        # 3. Status dinâmico / resumo na direita com indicador animado
        status_box = ttk.Frame(nav_bar)
        status_box.pack(side="right", padx=(4, 2))

        self.spinner_lbl = ttk.Label(
            status_box, textvariable=self.spinner_var, font=("Segoe UI", 9, "bold"), foreground="#0066cc"
        )
        self.spinner_lbl.pack(side="left", padx=(0, 4))

        self.status_lbl = ttk.Label(
            status_box, textvariable=self.status, font=("Segoe UI", 8), foreground="#444444"
        )
        self.status_lbl.pack(side="left")

        # 4. Barra de progresso discreta e fina
        prog_frame = ttk.Frame(self.root, padding=(8, 0, 8, 1))
        prog_frame.pack(fill="x")
        self.progress = ttk.Progressbar(prog_frame, maximum=100, mode="determinate")
        self.progress.pack(fill="x")

    def _build_content_area(self):
        content_frame = ttk.Frame(self.root)
        content_frame.pack(fill="both", expand=True, padx=12, pady=4)

        self.canvas = tk.Canvas(content_frame, highlightthickness=0, bg="#f5f5f7")
        self.scroll = ttk.Scrollbar(content_frame, orient="vertical", command=self.canvas.yview)
        self.frame = ttk.Frame(self.canvas)
        self.frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas_window = self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

    def _bind_global_shortcuts(self):
        for char in "123456789":
            self.root.bind(f"<Key-{char}>", lambda e, c=char: self._handle_number_shortcut(c))
            self.root.bind(f"<KP_{char}>", lambda e, c=char: self._handle_number_shortcut(c))

        for ev in ("<asterisk>", "<KP_Multiply>", "<Key-asterisk>", "<Key-multiply>"):
            try:
                self.root.bind(ev, lambda e: self._handle_asterisk_shortcut())
            except Exception:
                pass

        for ev in ("<slash>", "<KP_Divide>", "<Key-slash>", "<Key-KP_Divide>"):
            try:
                self.root.bind(ev, lambda e: self.mark_active_comparison_as_different())
            except Exception:
                pass

        self.root.bind("<Left>", lambda e: self.page_prev())
        self.root.bind("<Right>", lambda e: self.page_next())
        self.root.bind("<Control-Left>", lambda e: self.page_step_back_50())
        self.root.bind("<Control-Right>", lambda e: self.page_step_fwd_50())
        self.root.bind("<Shift-Left>", lambda e: self.page_step_back_50())
        self.root.bind("<Shift-Right>", lambda e: self.page_step_fwd_50())
        self.root.bind("<Prior>", lambda e: self.swap_active_comparison_photos())
        self.root.bind("<Next>", lambda e: self.swap_active_comparison_photos())
        self.root.bind("<Control-o>", lambda e: self.select_folder_and_start())
        self.root.bind("<Control-O>", lambda e: self.select_folder_and_start())
        self.root.bind("<Control-d>", lambda e: self.mark_active_comparison_as_different())
        self.root.bind("<Control-D>", lambda e: self.mark_active_comparison_as_different())
        self.root.bind("<Alt-w>", lambda e: self.switch_tab("web"))
        self.root.bind("<Alt-W>", lambda e: self.switch_tab("web"))
        self.root.bind("<Control-u>", lambda e: self.check_for_updates_manual())
        self.root.bind("<Control-U>", lambda e: self.check_for_updates_manual())
        self.root.bind("<F5>", lambda e: self.reload_last_folder())
        self.root.bind("<F11>", lambda e: self.toggle_view_mode())

    # =========================================================================
    # DIÁLOGOS E PASTAS
    # =========================================================================
    def open_folder_selector(self):
        """Abre a interface hierárquica e moderna para seleção múltipla de pastas."""
        from src.ui.dialogs.folder_selector_dialog import FolderSelectorDialog
        FolderSelectorDialog(self, on_start=self.start_engine_for_folders)

    def select_folder_and_start(self):
        """Abre o seletor de pastas (diálogo hierárquico com suporte a múltiplas pastas)."""
        self.open_folder_selector()

    def reload_last_folder(self):
        """Recarrega as últimas pastas selecionadas persistidas na configuração."""
        last_folders = APP_CONFIG.get("last_folders", [])
        if last_folders:
            existing = [Path(f).resolve() for f in last_folders if Path(f).exists()]
            if existing:
                self.start_engine_for_folders(existing)
                return
        last_f = APP_CONFIG.get("last_folder", "")
        if last_f and Path(last_f).exists():
            self.start_engine_for_folders([Path(last_f).resolve()])
            return

        messagebox.showinfo(
            "Nenhuma Pasta Salva",
            "Nenhuma pasta anterior foi encontrada no histórico.\nPor favor, selecione as pastas para análise.",
            parent=self.root
        )

    def start_engine_for_folders(self, folders):
        """Inicializa o motor PhotoEngine com suporte a uma ou múltiplas pastas."""
        if not folders:
            return

        folder_objs = [Path(f).resolve() for f in folders if Path(f).exists()]
        if not folder_objs:
            messagebox.showwarning("Pastas Inválidas", "Nenhuma das pastas fornecidas existe no disco.", parent=self.root)
            return

        folder_strs = [str(f) for f in folder_objs]
        APP_CONFIG["last_folders"] = folder_strs
        APP_CONFIG["last_folder"] = folder_strs[-1]
        save_app_config(APP_CONFIG)

        if self.processing and self.engine:
            self.engine.stop()

        if len(folder_objs) == 1:
            names = folder_objs[0].name
            self.status.set(f"Inicializando análise para: {names}...")
            engine_target = folder_objs[0]
        else:
            names = ", ".join(f.name for f in folder_objs[:3])
            if len(folder_objs) > 3:
                names += f" (+{len(folder_objs) - 3} pastas)"
            self.status.set(f"Inicializando análise para {len(folder_objs)} pastas ({names})...")
            engine_target = folder_objs

        self.stop_btn.state(["!disabled"])

        t = float(self.threshold.get())
        from src.photo_engine import PhotoEngine
        self.engine = PhotoEngine(
            engine_target,
            threshold=t,
            workers=APP_CONFIG.get("cpu_workers") or None,
            batch=APP_CONFIG.get("batch_size") or None,
            enable_clip=bool(self.enable_clip_var.get()),
            enable_hash=bool(self.enable_hash_var.get()),
            enable_web_detector=bool(self.web_detector_menu_var.get()),
            excluded_folders=APP_CONFIG.get("excluded_folders", None)
        )
        self.processing = True
        self.render_page()
        self.run_processing(self.engine.run)

    def start_engine_for_folder(self, folder_path):
        """Compatibilidade reversa: inicia análise para uma pasta única."""
        self.start_engine_for_folders([folder_path])

    def open_performance_dialog(self):
        PerformanceDialog(self)

    def open_folder_rules(self):
        FolderRulesDialog(self)

    def open_excluded_folders_dialog(self):
        ExcludedFoldersDialog(self)

    def view_log_file(self):
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 0:
            try:
                os.startfile(LOG_FILE)
            except Exception as e:
                messagebox.showerror("Erro ao abrir log", str(e))
        else:
            messagebox.showinfo("Log de Erros", "Nenhum erro registrado até o momento.\nO sistema está operando normalmente.")

    def clear_log_file(self):
        """Limpa o arquivo comparador_erros.log e notifica o usuário."""
        from src.config import clear_log_file
        if clear_log_file():
            messagebox.showinfo("Log de Erros", "O arquivo de log de erros foi limpo com sucesso!", parent=self.root)
        else:
            messagebox.showerror("Erro", "Não foi possível limpar o arquivo de log.", parent=self.root)

    def toggle_enable_clip(self):
        enabled = bool(self.enable_clip_var.get())
        if enabled:
            from src.dependency_installer import is_ai_available
            if not is_ai_available():
                if messagebox.askyesno(
                    "Aceleradores de IA Necessários",
                    "Os módulos de Inteligência Artificial (PyTorch / OpenCLIP) não estão instalados neste computador.\n\n"
                    "Deseja baixar e configurar os aceleradores de IA agora para habilitar a busca visual?",
                    parent=self.root
                ):
                    self.open_ai_installer_dialog()
                self.enable_clip_var.set(False)
                return

        APP_CONFIG["enable_clip"] = enabled
        save_app_config(APP_CONFIG)
        if self.engine:
            self.engine.enable_clip = enabled
        self.status.set(f"Busca Visual por Similaridade (CLIP) {'ativada' if enabled else 'desativada'}.")
        self.refresh_results(keep_page=True)

    def toggle_enable_hash(self):
        enabled = bool(self.enable_hash_var.get())
        APP_CONFIG["enable_hash"] = enabled
        save_app_config(APP_CONFIG)
        if self.engine:
            self.engine.enable_hash = enabled
        self.status.set(f"Busca por Hash (SHA-256 e pHash) {'ativada' if enabled else 'desativada'}.")
        self.refresh_results(keep_page=True)

    def toggle_web_detector(self):
        enabled = bool(self.web_detector_menu_var.get())
        if enabled:
            from src.dependency_installer import is_ai_available
            if not is_ai_available():
                if messagebox.askyesno(
                    "Aceleradores de IA Necessários",
                    "A detecção de Memes e Fotos da Web requer os módulos de IA (PyTorch / OpenCLIP).\n\n"
                    "Deseja baixar e configurar os aceleradores de IA agora?",
                    parent=self.root
                ):
                    self.open_ai_installer_dialog()
                self.web_detector_menu_var.set(False)
                return

        APP_CONFIG["enable_web_detector"] = enabled
        save_app_config(APP_CONFIG)
        if self.engine:
            self.engine.enable_web_detector = enabled
        self.status.set(f"Detecção de Memes & Fotos da Web {'ativada' if enabled else 'desativada'}.")
        self.refresh_results(keep_page=True)

    def toggle_confirm_delete_2s(self):
        enabled = bool(self.confirm_delete_2s_var.get())
        APP_CONFIG["confirm_delete_2s"] = enabled
        save_app_config(APP_CONFIG)
        self.status.set(f"Tela de confirmação de 2s {'ativada' if enabled else 'desativada'}.")

    def on_max_groups_change(self):
        try:
            val = int(self.max_groups_var.get())
            if val < 0:
                val = 0
                self.max_groups_var.set(0)
        except Exception:
            val = 0
            self.max_groups_var.set(0)
        APP_CONFIG["max_active_groups"] = val
        save_app_config(APP_CONFIG)
        if val == 0:
            self.status.set("Limite de grupos desativado (0 = sem limite / todos os grupos).")
        else:
            self.status.set(f"Limite de grupos ativos ajustado para {val}.")
        self.refresh_results(keep_page=True)

    def _on_tk_exception(self, exc_type, exc_value, exc_traceback):
        """Captura exceções de callbacks do Tkinter para evitar fechamento inesperado da janela."""
        import traceback
        err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        log_critical(f"Exceção não tratada capturada no loop de eventos Tkinter:\n{err_msg}")
        try:
            messagebox.showerror(
                "Aviso do Sistema",
                f"Ocorreu um erro durante a operação:\n\n{exc_value}\n\nO detalhe do erro foi gravado no arquivo de log ({LOG_FILE.name}).",
                parent=self.root
            )
        except Exception:
            pass

    def set_web_threshold(self, threshold):
        try:
            self.web_threshold = float(threshold)
            self._cached_web_groups = None
            pct = int(round(self.web_threshold * 100))
            self.status.set(f"Filtro de fotos da web ajustado para rigor de {pct}%.")
            self.refresh_results(keep_page=True)
        except Exception as e:
            log_error(f"Erro ao ajustar rigor web para {threshold}", e)

    def prompt_custom_web_threshold(self):
        try:
            cur_pct = int(round(self.web_threshold * 100))
            val = simpledialog.askinteger(
                "Rigor Personalizado",
                "Digite o percentual mínimo de confiança para Fotos da Web / Memes (1 a 99%):\n"
                "(Valores menores exibem mais imagens suspeitas)",
                parent=self.root,
                minvalue=1,
                maxvalue=99,
                initialvalue=cur_pct
            )
            if val is not None:
                self.set_web_threshold(val / 100.0)
        except Exception as e:
            log_error("Erro no diálogo de rigor personalizado", e)

    def reanalyze_web_photos(self):
        """Força a reanálise de todas as fotos carregadas com o classificador multimodal atualizado."""
        if self.engine is None or not self.engine.clip_paths:
            messagebox.showinfo(
                "Nenhuma Análise Ativa",
                "Por favor, selecione e analise uma pasta antes de reanalisar memes e fotos da web.",
                parent=self.root
            )
            return

        if self.processing:
            messagebox.showwarning(
                "Processamento em Andamento",
                "Aguarde a conclusão da análise atual para iniciar a reanálise de memes.",
                parent=self.root
            )
            return

        total_photos = len(self.engine.clip_paths)
        if not messagebox.askyesno(
            "Reanalisar Memes e Fotos da Web",
            f"Deseja reanalisar as {total_photos} fotos desta pasta com o algoritmo multimodal aprimorado?\n\n"
            f"Serão avaliados sinais visuais do CLIP, metadados ópticos de câmera, proporção de tela e assinaturas de nome.",
            parent=self.root
        ):
            return

        self.processing = True
        self.stop_btn.state(["!disabled"])
        self.status.set("Iniciando reanálise multimodal de memes e fotos da web...")

        def _worker():
            try:
                def _prog_cb(done, tot):
                    pct = int((done / tot) * 100) if tot > 0 else 0
                    self.root.after(0, lambda: self._update_web_prog(done, tot, pct))

                self.engine.recompute_web_photos(callback=_prog_cb)
                self.root.after(0, self._finish_web_reanalysis)
            except Exception as e:
                log_critical("Erro na reanálise de memes", e)
                self.root.after(0, lambda: self._fail_web_reanalysis(str(e)))

        threading.Thread(target=_worker, name="WebReanalyzeThread", daemon=True).start()

    def _update_web_prog(self, done, total, pct):
        self.progress["value"] = pct
        self.status.set(f"Reanalisando Memes & Web: {done}/{total} fotos ({pct}%)...")

    def _finish_web_reanalysis(self):
        self.processing = False
        self.stop_btn.state(["disabled"])
        self.progress["value"] = 100
        found = len(self.engine.get_web_photos(self.web_threshold))
        self.status.set(f"Reanálise concluída: {found} memes e imagens da web identificados.")
        self.switch_tab("web")
        messagebox.showinfo(
            "Reanálise Concluída",
            f"A reanálise multimodal foi concluída com sucesso!\n\n"
            f"Total de imagens identificadas como memes/web: {found}\n"
            f"As fotos foram agrupadas na aba 'Fotos da Web & Memes'.",
            parent=self.root
        )

    def _fail_web_reanalysis(self, err_msg):
        self.processing = False
        self.stop_btn.state(["disabled"])
        self.status.set("Erro na reanálise de memes.")
        messagebox.showerror("Erro na Reanálise", f"Ocorreu um erro ao reanalisar as fotos:\n{err_msg}", parent=self.root)

    # =========================================================================
    # AÇÕES DE FOTOS E GRUPOS (DELEGADAS PARA actions.py)
    # =========================================================================
    def _get_active_comparison_group(self):
        groups = self.current_groups()
        if not groups:
            return None
        if self.view_mode == "fullscreen_compare":
            if 0 <= self.compare_group_idx < len(groups):
                return groups[self.compare_group_idx]
            return groups[0] if groups else None
        else:
            page_groups = self.get_current_page_groups()
            return page_groups[0] if page_groups else None

    def _remove_group_from_engine_lists(self, group):
        remove_group_from_engine_collections(self, group)

    def mark_group_as_different(self, group):
        mark_group_different_action(self, group)

    def mark_active_comparison_as_different(self):
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox)):
            return
        group = self._get_active_comparison_group()
        if group and len(group) >= 2:
            self.mark_group_as_different(group)
        else:
            self.status.set("⚠️ Selecione um grupo com pelo menos 2 fotos para marcar como diferentes.")

    def _trigger_photo_deletion(self, path, group):
        trigger_single_photo_deletion(self, path, group)

    def _trigger_group_deletion(self, group):
        trigger_group_deletion(self, group)

    def swap_active_comparison_photos(self, idx_a=0, idx_b=1):
        swap_photos_in_group(self, idx_a, idx_b)

    def promote_largest_in_group(self, group):
        promote_largest_photo_action(self, group)

    def promote_active_comparison_photo(self):
        group = self._get_active_comparison_group()
        if group and len(group) >= 2:
            self.promote_largest_in_group(group)
        else:
            self.status.set("⚠️ Selecione um grupo com fotos comparadas para promover.")

    def keep_largest_and_delete_others(self, group):
        keep_largest_and_delete_others_action(self, group)

    def keep_largest_in_active_comparison(self):
        group = self._get_active_comparison_group()
        if group and len(group) >= 2:
            self.keep_largest_and_delete_others(group)
        else:
            self.status.set("⚠️ Selecione um grupo com duplicatas para manter a maior.")

    def open_ai_installer_dialog(self):
        """Abre a janela de download e configuração dinâmica de aceleradores de IA."""
        from src.dependency_installer import DependencyInstallerDialog

        def on_finish(success):
            if success:
                self.enable_clip_var.set(True)
                self.web_detector_menu_var.set(True)
                APP_CONFIG["enable_clip"] = True
                APP_CONFIG["enable_web_detector"] = True
                save_app_config(APP_CONFIG)
                self.status.set("Módulos de IA ativados com sucesso! CLIP e Detector Web habilitados.")
                self.refresh_results(keep_page=True)

        DependencyInstallerDialog(parent=self.root, on_finish=on_finish)

    def remove_photo_shadows(self, photo_path):
        """Inicia a remoção de sombras com IA e exibe o comparativo Antes e Depois."""
        trigger_shadow_removal_action(self, photo_path)

    def remove_shadows_active_comparison_photo(self):
        group = self._get_active_comparison_group()
        if group and len(group) > 0:
            self.remove_photo_shadows(group[0])
        else:
            messagebox.showinfo("Remover Sombras", "Abra um grupo ou clique no botão '✨ Sombras' no card da foto desejada.", parent=self.root)

    def rotate_photo(self, photo_path, degrees=90):
        """Gira a foto no sentido horário e recarrega a exibição."""
        from src.ui.actions import rotate_photo_action
        rotate_photo_action(self, photo_path, degrees=degrees)

    # =========================================================================
    # MECANISMO DE AUTO-ATUALIZAÇÃO
    # =========================================================================
    def _check_updates_background(self):
        """Verifica atualizações em segundo plano ao iniciar o app, sem bloquear ou exibir erros se offline."""
        def _worker():
            try:
                from src.updater import check_for_updates
                info = check_for_updates(timeout=5)
                if info.get("has_update"):
                    latest_v = info.get("latest_version")
                    def _notify():
                        self.status.set(f"⭐ Nova versão v{latest_v} disponível! Acesse o menu Ajuda para atualizar.")
                    self.root.after(0, _notify)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="BackgroundUpdateCheck").start()

    def check_for_updates_manual(self):
        """Verifica atualizações manualmente via clique do usuário no menu ou Ctrl+U."""
        self.status.set("🔍 Verificando atualizações no GitHub Releases...")

        def _worker():
            from src.updater import check_for_updates, APP_VERSION
            info = check_for_updates(timeout=8)

            def _show_result():
                if info.get("has_update"):
                    self.status.set(f"⭐ Nova versão v{info.get('latest_version')} encontrada!")
                    from src.ui.dialogs.update_dialog import UpdateDialog
                    UpdateDialog(parent=self.root, update_info=info)
                elif info.get("success"):
                    self.status.set(f"Você já está utilizando a versão mais recente (v{APP_VERSION}).")
                    messagebox.showinfo(
                        "Atualizações",
                        f"Você já está utilizando a versão mais recente do Comparador de Fotos (v{APP_VERSION}).",
                        parent=self.root
                    )
                else:
                    err = info.get("error_message") or "Não foi possível conectar ao servidor do GitHub."
                    self.status.set("Falha ao verificar atualizações.")
                    messagebox.showwarning(
                        "Verificação de Atualizações",
                        f"Não foi possível verificar atualizações no momento:\n\n{err}",
                        parent=self.root
                    )

            self.root.after(0, _show_result)

        threading.Thread(target=_worker, daemon=True, name="ManualUpdateCheck").start()

    def toggle_check_updates_startup(self):
        enabled = bool(self.check_updates_startup_var.get())
        APP_CONFIG["check_updates_on_startup"] = enabled
        save_app_config(APP_CONFIG)
        self.status.set(f"Verificação de atualizações ao iniciar {'ativada' if enabled else 'desativada'}.")

    def prompt_github_repo(self):
        from src.updater import DEFAULT_GITHUB_REPO
        cur_repo = APP_CONFIG.get("github_repo", DEFAULT_GITHUB_REPO)
        new_repo = simpledialog.askstring(
            "Repositório GitHub",
            "Informe o repositório do GitHub no formato 'usuario/repositorio':\n\n"
            "Exemplo: Guilherme-ai/comparador_fotos",
            parent=self.root,
            initialvalue=cur_repo
        )
        if new_repo and new_repo.strip():
            APP_CONFIG["github_repo"] = new_repo.strip()
            save_app_config(APP_CONFIG)
            messagebox.showinfo("Repositório Atualizado", f"Repositório configurado para:\n{new_repo.strip()}", parent=self.root)

    def open_github_page(self):
        from src.updater import DEFAULT_GITHUB_REPO
        repo = APP_CONFIG.get("github_repo", DEFAULT_GITHUB_REPO)
        webbrowser.open(f"https://github.com/{repo}")

    def show_about_dialog(self):
        from src.updater import APP_VERSION
        from src.dependency_installer import get_ai_details
        ai = get_ai_details()
        ai_desc = "Ativo (GPU CUDA)" if ai.get("cuda_available") else ("Ativo (CPU)" if ai.get("installed") else "Não instalado (Modo Hashes)")

        msg = (
            f"Comparador de Fotos com IA\n"
            f"Versão: v{APP_VERSION}\n\n"
            f"• Aceleradores de IA: {ai_desc}\n"
            f"• Busca Visual: OpenCLIP (ViT-B-32)\n"
            f"• Hashes Perceptuais: pHash + SHA-256\n"
            f"• Detecção Web: Multimodal Zero-Shot\n"
            f"• Sistema: Windows 10/11 (64-bit)\n\n"
            f"Desenvolvido para organização, deduplicação e limpeza de fotos em alta performance."
        )
        messagebox.showinfo("Sobre o Comparador de Fotos", msg, parent=self.root)

    def prompt_gemini_api_key(self):
        try:
            cur_key = APP_CONFIG.get("gemini_api_key", "")
            masked = (cur_key[:6] + "..." + cur_key[-4:]) if len(cur_key) > 10 else cur_key
            val = simpledialog.askstring(
                "Chave de API Gemini",
                f"Digite sua chave de API do Google Gemini (ou deixe em branco para usar o motor local IA/CV):\n\n"
                f"Chave atual: {masked if masked else '(nenhuma / usando motor local)'}",
                parent=self.root,
                initialvalue=cur_key
            )
            if val is not None:
                APP_CONFIG["gemini_api_key"] = val.strip()
                save_app_config(APP_CONFIG)
                if val.strip():
                    self.status.set("🔑 Chave de API Gemini configurada com sucesso!")
                else:
                    self.status.set("Motor local de remoção de sombras selecionado como padrão.")
        except Exception as e:
            log_error("Erro no diálogo de chave Gemini", e)

    def set_shadow_removal_mode(self, mode):
        APP_CONFIG["shadow_removal_mode"] = mode
        save_app_config(APP_CONFIG)
        self.shadow_mode_var.set(mode)
        mode_names = {
            "interactive": "Alt. 1 + 2: Ajuste Interativo Anti-Estouro (Slider & Altas Luzes)",
            "deep": "Alt. 3: Rede Neural Zero-DCE (Modo Local IA Profunda PyTorch GPU CUDA)",
            "histogram": "Alt. 4: Compensação Auto-Adaptativa por Histograma",
            "msrcr": "Modo Local Retinex Clássico",
            "gemini": "Google Gemini Vision (Nuvem)"
        }
        name = mode_names.get(mode, mode)
        self.status.set(f"Algoritmo de remoção de sombras ajustado para: {name}")

    def _handle_number_shortcut(self, char):
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox)):
            return
        idx = int(char) - 1
        active_group = self._get_active_comparison_group()
        if not active_group or idx >= len(active_group):
            return
        self._trigger_photo_deletion(active_group[idx], active_group)

    def _handle_asterisk_shortcut(self):
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox)):
            return
        active_group = self._get_active_comparison_group()
        if not active_group or len(active_group) == 0:
            self.status.set("⚠️ Nenhum grupo ativo para excluir.")
            return
        self._trigger_group_deletion(active_group)

    # =========================================================================
    # RENDERIZAÇÃO DE TELAS (DELEGADAS PARA views/)
    # =========================================================================
    def toggle_view_mode(self):
        if self.view_mode == "grid":
            self.view_mode = "fullscreen_compare"
            self.mode_btn_var.set("▦ Modo Grade Padrão")
        else:
            self.view_mode = "grid"
            self.mode_btn_var.set("⛶ Comparativo em Tela Cheia")
        if hasattr(self, "canvas"):
            self.canvas.yview_moveto(0)
            self.canvas.xview_moveto(0)
        self.render_page()

    def open_group_in_fullscreen(self, group_index):
        self.view_mode = "fullscreen_compare"
        self.compare_group_idx = max(0, min(group_index, len(self.current_groups()) - 1))
        self.mode_btn_var.set("▦ Modo Grade Padrão")
        if hasattr(self, "canvas"):
            self.canvas.yview_moveto(0)
            self.canvas.xview_moveto(0)
        self.render_page()

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)
        if self.view_mode == "fullscreen_compare":
            self.render_fullscreen_comparison()

    def _on_mousewheel(self, event):
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def render_page(self):
        for child in self.frame.winfo_children():
            child.destroy()

        if self.engine is None:
            welcome = ttk.Frame(self.frame, padding=40)
            welcome.pack(expand=True, fill="both")
            ttk.Label(
                welcome,
                text="Comparador de Fotos com IA",
                font=("Segoe UI", 16, "bold"),
                foreground="#1d1d1f"
            ).pack(pady=(20, 10))

            last_f = APP_CONFIG.get("last_folder", "")
            if last_f and Path(last_f).exists():
                ttk.Label(
                    welcome,
                    text=f"Última pasta utilizada:\n{last_f}",
                    font=("Segoe UI", 10),
                    justify="center",
                    foreground="#555555"
                ).pack(pady=10)
                ttk.Button(
                    welcome,
                    text=f"🔄 Analisar Última Pasta ({Path(last_f).name})",
                    command=self.reload_last_folder,
                    width=34
                ).pack(pady=6)

            ttk.Button(
                welcome,
                text="📁 Selecionar Pasta para Análise...",
                command=self.select_folder_and_start,
                width=34
            ).pack(pady=6)
            return

        if self.view_mode == "fullscreen_compare":
            self.render_fullscreen_comparison()
        else:
            self.render_grid_view()

    def render_fullscreen_comparison(self):
        render_fullscreen_comparison(self)

    def render_grid_view(self):
        render_grid_view(self)

    # =========================================================================
    # PAGINAÇÃO, NAVEGAÇÃO E GRUPOS
    # =========================================================================
    def raw_groups_for_tab(self, tab_name=None):
        if self.engine is None:
            return []
        tab = tab_name or self.active_tab
        if tab == "exact":
            return [g for g in self.engine.exact if len(g) > 1]
        elif tab == "phash":
            return [g for g in self.engine.phash_groups if len(g) > 1]
        elif tab == "web":
            if hasattr(self.engine, "get_web_photo_groups") and APP_CONFIG.get("enable_web_detector", True):
                if self._cached_web_groups is None:
                    self._cached_web_groups = self.engine.get_web_photo_groups(self.web_threshold)
                return self._cached_web_groups
            return []
        elif tab == "all":
            all_g = []
            for g in self.raw_groups_for_tab("exact"):
                all_g.append(g)
            for g in self.raw_groups_for_tab("phash"):
                all_g.append(g)
            for g in self.raw_groups_for_tab("clip"):
                all_g.append(g)
            return all_g
        else:
            if self._cached_clip_groups is not None:
                return [g for g in self._cached_clip_groups if len(g) > 1]
            return [g for g in self.engine.groups() if len(g) > 1]

    def current_groups(self):
        raw = self.raw_groups_for_tab()
        try:
            limit = self.max_groups_var.get() if hasattr(self, "max_groups_var") else int(APP_CONFIG.get("max_active_groups", 0))
        except Exception:
            limit = 0
        if limit and limit > 0:
            return raw[:limit]
        return raw

    def total_pages(self):
        if self.view_mode == "fullscreen_compare":
            return max(1, len(self.current_groups()))
        return max(1, math.ceil(len(self.current_groups()) / self.page_size))

    def get_current_page_groups(self):
        groups = self.current_groups()
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        return groups[start:end]

    def page_first(self):
        if self.view_mode == "fullscreen_compare":
            self.compare_group_idx = 0
        else:
            self.current_page = 1
        self.render_page()

    def page_prev(self):
        if self.view_mode == "fullscreen_compare":
            if self.compare_group_idx > 0:
                self.compare_group_idx -= 1
                self.render_page()
        else:
            if self.current_page > 1:
                self.current_page -= 1
                self.render_page()

    def page_step_back_50(self):
        """Retrocede 50 fotos/grupos no modo tela cheia ou 50 páginas na grade."""
        if self.view_mode == "fullscreen_compare":
            if self.compare_group_idx > 0:
                self.compare_group_idx = max(0, self.compare_group_idx - 50)
                self.render_page()
        else:
            if self.current_page > 1:
                self.current_page = max(1, self.current_page - 50)
                self.render_page()

    def page_next(self):
        if self.view_mode == "fullscreen_compare":
            groups = self.current_groups()
            if self.compare_group_idx < len(groups) - 1:
                self.compare_group_idx += 1
                self.render_page()
        else:
            if self.current_page < self.total_pages():
                self.current_page += 1
                self.render_page()

    def page_step_fwd_50(self):
        """Avança 50 fotos/grupos no modo tela cheia ou 50 páginas na grade."""
        if self.view_mode == "fullscreen_compare":
            groups = self.current_groups()
            max_idx = max(0, len(groups) - 1)
            if self.compare_group_idx < max_idx:
                self.compare_group_idx = min(max_idx, self.compare_group_idx + 50)
                self.render_page()
        else:
            max_p = self.total_pages()
            if self.current_page < max_p:
                self.current_page = min(max_p, self.current_page + 50)
                self.render_page()

    def page_last(self):
        if self.view_mode == "fullscreen_compare":
            self.compare_group_idx = max(0, len(self.current_groups()) - 1)
        else:
            self.current_page = self.total_pages()
        self.render_page()

    def switch_tab(self, tab_name):
        self.active_tab = tab_name
        self.current_page = 1
        self.compare_group_idx = 0
        self.render_page()

    # =========================================================================
    def set_clip_threshold(self, threshold: float):
        """Define o valor de threshold do CLIP programaticamente e atualiza interface/recálculo."""
        val = max(0.50, min(0.999, float(threshold)))
        self.threshold.set(val)
        self.value_label.config(text=f"{val*100:.1f}%")
        APP_CONFIG["clip_threshold"] = val
        save_app_config(APP_CONFIG)
        self._apply_threshold_change(immediate=True)

    def _step_threshold(self, delta: float):
        """Incrementa ou decrementa finamente o threshold do CLIP."""
        cur = float(self.threshold.get())
        new_val = max(0.50, min(0.999, round(cur + delta, 4)))
        self.set_clip_threshold(new_val)

    def on_slider(self, _=None):
        t = float(self.threshold.get())
        self.value_label.config(text=f"{t*100:.1f}%")
        if self._slider_job is not None:
            try:
                self.root.after_cancel(self._slider_job)
            except Exception:
                pass
        self._slider_job = self.root.after(350, self._apply_threshold_change)

    def _apply_threshold_change(self, immediate=False):
        if self._slider_job is not None:
            try:
                self.root.after_cancel(self._slider_job)
            except Exception:
                pass
            self._slider_job = None

        if self.engine is None:
            return

        t = round(float(self.threshold.get()), 3)
        APP_CONFIG["clip_threshold"] = t
        save_app_config(APP_CONFIG)

        if self._is_recalculating:
            self._pending_threshold = t
            return

        self._is_recalculating = True
        self.status.set("Atualizando grupos de similaridade CLIP...")

        def calculate_async():
            new_groups = self.engine.groups(t)
            self._cached_clip_groups = new_groups
            self.root.after(0, self._finish_threshold_recalc, t, new_groups)

        threading.Thread(target=calculate_async, daemon=True).start()

    def _finish_threshold_recalc(self, applied_t, new_groups):
        self._is_recalculating = False
        APP_CONFIG["clip_threshold"] = float(applied_t)
        save_app_config(APP_CONFIG)

        try:
            limit = self.max_groups_var.get() if hasattr(self, "max_groups_var") else int(APP_CONFIG.get("max_active_groups", 100))
        except Exception:
            limit = 100
        tot = len(new_groups)
        cnt_str = f"{limit}/{tot}" if (limit and tot > limit) else str(tot)
        self.tab_clip_btn.config(text=f"CLIP ({cnt_str})")
        self.status.set(f"Similaridade recalculada para {applied_t*100:.1f}% ({tot} grupos encontrados)")

        if self._pending_threshold is not None:
            pending = self._pending_threshold
            self._pending_threshold = None
            if pending != applied_t:
                self._apply_threshold_change(immediate=True)
                return

        self.refresh_results(keep_page=True)

    # =========================================================================
    # ABERTURA DE ARQUIVOS E PASTAS NO SISTEMA OPERACIONAL
    # =========================================================================
    def open_file(self, path):
        try:
            p = Path(path).resolve()
            if not p.exists():
                messagebox.showwarning("Aviso", f"Arquivo não encontrado: {path}")
                return
            if os.name == "nt":
                os.startfile(p)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            log_error(f"Erro ao abrir arquivo {path}", e)

    def open_folder(self, path):
        try:
            p = Path(path).resolve()
            if not p.exists():
                messagebox.showwarning("Aviso", f"Pasta ou arquivo não encontrado: {path}")
                return
            if os.name == "nt":
                if p.is_file():
                    subprocess.Popen(f'explorer /select,"{p}"')
                else:
                    os.startfile(p)
            elif sys.platform == "darwin":
                folder = p if p.is_dir() else p.parent
                subprocess.Popen(["open", str(folder)])
            else:
                folder = p if p.is_dir() else p.parent
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            log_error(f"Erro ao abrir pasta para {path}", e)

    # =========================================================================
    # CACHE DE MINIATURAS E METADADOS
    # =========================================================================
    def get_thumb(self, path, size=(140, 140)):
        sp = f"{path}_{size[0]}x{size[1]}"
        if sp in self.thumb_cache:
            return self.thumb_cache[sp]
        try:
            p = Path(path)
            if not p.exists() or p.stat().st_size == 0:
                return None
            from src.auto_orienter import load_image_with_orientation
            im = load_image_with_orientation(p)
            im.thumbnail(size, Image.Resampling.LANCZOS if size[0] > 200 else Image.Resampling.BILINEAR)
            tk_img = ImageTk.PhotoImage(im)
            self.thumb_cache[sp] = tk_img
            return tk_img
        except Exception as e:
            log_error(f"Erro ao gerar miniatura para {path}", e)
            return None

    def get_file_info(self, path):
        sp = str(path)
        if sp in self.info_cache:
            return self.info_cache[sp]
        try:
            p = Path(sp)
            if not p.exists():
                return ""
            st = p.stat()
            size_str = format_size(st.st_size)
            dim_str = "0x0"
            try:
                with Image.open(p) as im:
                    w, h = im.size
                    dim_str = f"{w}x{h}"
            except Exception:
                pass
            res = f"{dim_str} • {size_str}"
            self.info_cache[sp] = res
            return res
        except Exception:
            return ""

    def get_photo_web_info(self, path):
        if self.engine and hasattr(self.engine, "web_photos") and self.engine.web_photos:
            return self.engine.web_photos.get(str(path))
        return None

    def get_photo_sharpness(self, path):
        sp = str(path)
        if sp in self.sharpness_cache:
            return self.sharpness_cache[sp]
        from src.hasher import compute_sharpness_score
        score = compute_sharpness_score(path)
        self.sharpness_cache[sp] = score
        return score

    def get_group_focus_ranks(self, group):
        """
        Analisa o foco/nitidez de todas as fotos de um grupo comparativo e retorna
        as classificações relativas com ícones coloridos:
        - 🟢 Verde: Foto(s) mais nítida(s) / melhor foco
        - 🔴 Vermelho: Foto menos nítida / menor foco
        - 🟡 Amarelo: Fotos intermediárias
        """
        if not group or len(group) <= 1:
            return {}

        scores = {}
        for p in group:
            scores[str(p)] = self.get_photo_sharpness(p)

        s_vals = list(scores.values())
        max_s = max(s_vals)
        min_s = min(s_vals)
        diff = max_s - min_s

        ranks = {}
        # Se todas as fotos tiverem praticamente a mesma nitidez (diferença <= 2%)
        if diff <= 1e-4 or (max_s > 0 and diff / max_s <= 0.02):
            for p in group:
                ranks[str(p)] = ("🟢", "Mais Nítida", "#008800", "#e6f4ea")
            return ranks

        if len(group) == 2:
            for p in group:
                sp = str(p)
                val = scores[sp]
                if val == max_s or (max_s > 0 and (max_s - val) / max_s <= 0.02):
                    ranks[sp] = ("🟢", "Mais Nítida", "#008800", "#e6f4ea")
                else:
                    ranks[sp] = ("🔴", "Menos Nítida", "#cc0000", "#fce8e6")
            return ranks

        # Grupo com 3 ou mais fotos
        sorted_photos = sorted(group, key=lambda x: scores[str(x)], reverse=True)
        best_p = str(sorted_photos[0])
        worst_p = str(sorted_photos[-1])

        for p in group:
            sp = str(p)
            val = scores[sp]
            if sp == best_p or (max_s > 0 and (max_s - val) / max_s <= 0.02):
                ranks[sp] = ("🟢", "Mais Nítida", "#008800", "#e6f4ea")
            elif sp == worst_p or (min_s > 0 and (val - min_s) / min_s <= 0.02):
                ranks[sp] = ("🔴", "Menos Nítida", "#cc0000", "#fce8e6")
            else:
                ranks[sp] = ("🟡", "Nitidez Média", "#b8860b", "#fef9e7")

        return ranks

    # =========================================================================
    # ATUALIZAÇÃO PROGRESSIVA EM TEMPO REAL E POLLING
    # =========================================================================
    def refresh_results(self, keep_page=False):
        clip_enabled = bool(APP_CONFIG.get("enable_clip", True))
        hash_enabled = bool(APP_CONFIG.get("enable_hash", True))
        web_enabled = bool(APP_CONFIG.get("enable_web_detector", True))
        self.enable_clip_var.set(clip_enabled)
        self.enable_hash_var.set(hash_enabled)
        self.web_detector_menu_var.set(web_enabled)

        if self.engine is None:
            self.tab_clip_btn.config(text="CLIP (Desativado)" if not clip_enabled else "CLIP (0)")
            self.tab_phash_btn.config(text="pHash (Desativado)" if not hash_enabled else "pHash (0)")
            self.tab_exact_btn.config(text="SHA-256 (Desativado)" if not hash_enabled else "SHA-256 (0)")
            self.tab_web_btn.config(text="🌐 Web (Desativado)" if not web_enabled else "🌐 Web (0)")
            self.tab_all_btn.config(text="Todos (0)")
            self.render_page()
            return

        threshold = float(self.threshold.get())
        if not clip_enabled:
            clip_groups = []
            self._cached_clip_groups = []
        elif self._cached_clip_groups is not None:
            clip_groups = [g for g in self._cached_clip_groups if len(g) > 1]
            self._cached_clip_groups = clip_groups
        else:
            clip_groups = [g for g in self.engine.groups(threshold) if len(g) > 1]
            self._cached_clip_groups = clip_groups

        if not hash_enabled:
            exact_groups = []
            phash_groups = []
            self.engine.exact = []
            self.engine.phash_groups = []
        else:
            self.engine.exact = [g for g in self.engine.exact if len(g) > 1]
            self.engine.phash_groups = [g for g in self.engine.phash_groups if len(g) > 1]
            exact_groups = self.engine.exact
            phash_groups = self.engine.phash_groups

        web_groups = self.engine.get_web_photo_groups(self.web_threshold) if (web_enabled and hasattr(self.engine, "get_web_photo_groups")) else []
        self._cached_web_groups = web_groups

        try:
            limit = self.max_groups_var.get() if hasattr(self, "max_groups_var") else int(APP_CONFIG.get("max_active_groups", 0))
        except Exception:
            limit = 0

        def _fmt(lbl, cnt):
            if limit and limit > 0 and cnt > limit:
                return f"{lbl} ({limit}/{cnt})"
            return f"{lbl} ({cnt})"

        if not clip_enabled:
            self.tab_clip_btn.config(text="CLIP (Desativado)")
        else:
            self.tab_clip_btn.config(text=_fmt("CLIP", len(clip_groups)))

        if not hash_enabled:
            self.tab_phash_btn.config(text="pHash (Desativado)")
            self.tab_exact_btn.config(text="SHA-256 (Desativado)")
        else:
            self.tab_phash_btn.config(text=_fmt("pHash", len(phash_groups)))
            self.tab_exact_btn.config(text=_fmt("SHA-256", len(exact_groups)))

        if not web_enabled:
            self.tab_web_btn.config(text="🌐 Web (Desativado)")
        else:
            self.tab_web_btn.config(text=_fmt("🌐 Web", len(web_groups)))

        total_all = len(clip_groups) + len(phash_groups) + len(exact_groups)
        self.tab_all_btn.config(text=_fmt("Todos", total_all))

        if not keep_page:
            self.current_page = 1
            self.compare_group_idx = 0
        else:
            self.current_page = min(self.current_page, self.total_pages())
            self.compare_group_idx = min(self.compare_group_idx, max(0, len(self.current_groups()) - 1))

        self.render_page()

    def poll(self):
        if getattr(self, "_is_closing", False):
            return
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return

        if not hasattr(self, "holder"):
            return

        # 1. Verifica se a thread já terminou, parou ou deu erro
        if self.holder.get("stopped") or self.holder.get("done") or self.holder.get("error"):
            self.processing = False
            self.stop_btn.state(["disabled"])
            if self.holder.get("error"):
                self.status.set("Erro durante a análise")
                log_error("Erro fatal durante o processamento em background", self.holder["error"])
                messagebox.showerror("Erro", self.holder["error"])
            else:
                self.status.set("Análise interrompida — dados salvos." if self.holder.get("stopped") else "Análise concluída com sucesso.")
            self.engine.load_partial_state()
            self.refresh_results()
            return

        # 2. Atualiza status da análise com a última mensagem
        d = self.holder.get("d")
        if d:
            stage = d.get("stage", "")
            done = d.get("done", 0)
            total = d.get("total", 0)
            elapsed = d.get("elapsed", 0)
            rate = d.get("rate", 0)
            eta = d.get("eta", 0)
            extra = d.get("extra", "")

            pct = int((done / total) * 100) if total > 0 else 0
            self.progress["value"] = pct

            elapsed_str = f"{int(elapsed)}s" if elapsed < 60 else f"{int(elapsed//60)}m {int(elapsed%60):02d}s"
            eta_str = f"{int(eta)}s" if eta < 60 else f"{int(eta//60)}m {int(eta%60):02d}s"

            if total > 0:
                self.status.set(f"{stage}: {done}/{total} ({pct}%) • {rate:.1f} fotos/s • decorrido: {elapsed_str} • restante: ~{eta_str}")
            else:
                self.status.set(f"{stage}: {done} processados • {extra}")

            self.info.set(extra)

            # 3. Atualização leve dos botões das abas durante o processamento (sem recalcular grafos pesados nem re-renderizar a tela)
            curr_exact = len(self.engine.exact) if self.engine else 0
            curr_phash = len(self.engine.phash_groups) if self.engine else 0
            curr_web = len(self.engine.get_web_photo_groups(self.web_threshold)) if (self.engine and hasattr(self.engine, "get_web_photo_groups")) else 0

            try:
                limit = self.max_groups_var.get() if hasattr(self, "max_groups_var") else int(APP_CONFIG.get("max_active_groups", 0))
            except Exception:
                limit = 0

            counts_changed = False
            if curr_exact != self._last_exact_count:
                self._last_exact_count = curr_exact
                ex_str = f"{limit}/{curr_exact}" if (limit and limit > 0 and curr_exact > limit) else str(curr_exact)
                self.tab_exact_btn.config(text=f"SHA-256 ({ex_str})")
                counts_changed = True

            if curr_phash != self._last_phash_count:
                self._last_phash_count = curr_phash
                ph_str = f"{limit}/{curr_phash}" if (limit and limit > 0 and curr_phash > limit) else str(curr_phash)
                self.tab_phash_btn.config(text=f"pHash ({ph_str})")
                counts_changed = True

            if curr_web != self._last_web_count:
                self._last_web_count = curr_web
                web_enabled = bool(APP_CONFIG.get("enable_web_detector", True))
                if web_enabled:
                    wb_str = f"{limit}/{curr_web}" if (limit and limit > 0 and curr_web > limit) else str(curr_web)
                    self.tab_web_btn.config(text=f"🌐 Web ({wb_str})")
                counts_changed = True

            # Atualização progressiva da tela durante o processamento em background:
            # Se novos grupos foram detectados e se passaram pelo menos 1.5s, atualiza a tela
            # dinamicamente mantendo a página atual para permitir exclusão antecipada!
            now = time.time()
            if counts_changed and (now - self._last_progressive_refresh > 1.5):
                self._last_progressive_refresh = now
                self.refresh_results(keep_page=True)

        try:
            if not getattr(self, "_is_closing", False) and self.root.winfo_exists():
                self._poll_job = self.root.after(150, self.poll)
        except Exception:
            self._poll_job = None

    def run_processing(self, fn, *args):
        self.holder = {}

        def cb(d):
            self.holder["d"] = d

        def target():
            try:
                self.engine.callback = cb
                fn(*args)
                self.holder["done"] = True
            except Exception as e:
                log_error("Erro na thread de processamento", e)
                self.holder["error"] = str(e)

        t = threading.Thread(target=target, daemon=True)
        t.start()
        self.poll()

    def _animate_spinner(self):
        if getattr(self, "_is_closing", False):
            return
        if getattr(self, "processing", False):
            self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_frames)
            current_frame = self._spinner_frames[self._spinner_idx]
            self.spinner_var.set(current_frame)
            if hasattr(self, "_processing_spinner_lbl") and self._processing_spinner_lbl and self._processing_spinner_lbl.winfo_exists():
                try:
                    self._processing_spinner_lbl.config(text=current_frame)
                except Exception:
                    pass
        else:
            self.spinner_var.set("")
            if hasattr(self, "_processing_spinner_lbl") and self._processing_spinner_lbl and self._processing_spinner_lbl.winfo_exists():
                try:
                    self._processing_spinner_lbl.config(text="")
                except Exception:
                    pass

        try:
            if not getattr(self, "_is_closing", False) and self.root.winfo_exists():
                self._spinner_job = self.root.after(80, self._animate_spinner)
        except Exception:
            self._spinner_job = None

    def stop(self):
        if self.engine:
            self.engine.stop()
            self.status.set("Interrompendo processamento...")

    def run(self):
        """Inicia o loop de eventos principal da interface Tkinter."""
        self.root.mainloop()

    def close(self):
        self._is_closing = True
        if self._spinner_job is not None:
            try:
                self.root.after_cancel(self._spinner_job)
            except Exception:
                pass
            self._spinner_job = None
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        if self.processing and self.engine:
            self.engine.stop()
        try:
            self.root.destroy()
        except Exception:
            pass
