import shutil
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from PIL import Image, ImageTk
from send2trash import send2trash

from src.config import format_size, log_error, log_info, APP_CONFIG


class BeforeAfterCompareDialog:
    """
    Diálogo para comparação detalhada 'Antes e Depois' da remoção de sombras.
    Oferece 3 botões interativos para alternar instantaneamente entre os algoritmos:
      - Alt. 1 + 2: Ajuste Interativo com Preservação de Altas Luzes & Slider em Tempo Real.
      - Alt. 3: Rede Neural Zero-DCE calibrada com aceleração por GPU (PyTorch CUDA).
      - Alt. 4: Compensação Auto-Adaptativa por Histograma (Auto-Exposure Aware).
    A versão não escolhida é descartada/enviada com segurança para a Lixeira do Windows.
    """
    def __init__(self, parent_gui, original_path, enhanced_path, on_decision=None):
        self.gui = parent_gui
        self.orig_path = Path(original_path).resolve()
        self.enh_path = Path(enhanced_path).resolve()
        self.on_decision = on_decision
        self._is_closed = False

        self.root_parent = self.gui.root if hasattr(self.gui, "root") else None
        self.top = tk.Toplevel(self.root_parent)
        self.top.title(f"Antes e Depois — Remoção de Sombras — {self.orig_path.name}")

        # Dimensionamento seguro: Altura reduzida para caber confortavelmente acima da barra de tarefas do Windows
        screen_w = self.top.winfo_screenwidth()
        screen_h = self.top.winfo_screenheight()
        win_w = min(1280, max(820, int(screen_w * 0.88)))
        win_h = min(680, max(460, int(screen_h * 0.78)))
        pos_x = max(0, (screen_w - win_w) // 2)
        pos_y = max(10, (screen_h - win_h - 80) // 2)
        self.top.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.top.minsize(760, 420)

        self.top.grab_set()

        # Estado dos algoritmos
        initial_mode = APP_CONFIG.get("shadow_removal_mode", "interactive")
        if initial_mode not in ("interactive", "deep", "histogram"):
            initial_mode = "interactive"
        self.current_mode = initial_mode
        self.intensity_val = 0.50
        self.is_recomputing = False
        self._debounce_timer = None
        self._temp_files_to_clean = []
        if self.enh_path.exists():
            self._temp_files_to_clean.append(self.enh_path)

        self._img_orig_tk = None
        self._img_enh_tk = None
        self._resize_job = None
        self._startup_job = None

        self._build_ui()
        self._bind_keys()
        self._update_algorithm_buttons_style()
        self._update_control_panel_visibility()

        # Força cálculo de geometria do Tkinter para que os Canvas tenham dimensões reais
        self.top.update_idletasks()
        self._startup_job = self.top.after(40, self._render_images)

    def _build_ui(self):
        # 1. Header Bar (Topo)
        hdr = ttk.Frame(self.top, padding=(12, 4, 12, 4), style="Card.TFrame")
        hdr.pack(side="top", fill="x")

        ttk.Label(
            hdr,
            text="✨ Comparador Antes e Depois — Remoção de Sombras com IA",
            font=("Segoe UI", 11, "bold"),
            foreground="#0066cc"
        ).pack(side="left")

        ttk.Label(
            hdr,
            text="Escolha qual versão manter. A foto descartada irá para a Lixeira do Windows.",
            font=("Segoe UI", 8, "italic"),
            foreground="#555555"
        ).pack(side="right")

        # 2. Barra de Seleção de Algoritmos (3 Botões Grandes e Destacados)
        alg_bar = ttk.Frame(self.top, padding=(10, 4, 10, 4), relief="groove", borderwidth=1)
        alg_bar.pack(side="top", fill="x", padx=8, pady=(2, 2))

        ttk.Label(
            alg_bar,
            text="Algoritmo:",
            font=("Segoe UI", 9, "bold"),
            foreground="#111111"
        ).pack(side="left", padx=(0, 8))

        # Botão 1: Alt. 1 + 2
        self.btn_alt1_2 = tk.Button(
            alg_bar,
            text="🎛️ Alt. 1 + 2: Ajuste Interativo (Anti-Estouro)",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=10,
            pady=3,
            command=lambda: self.switch_mode("interactive")
        )
        self.btn_alt1_2.pack(side="left", padx=(0, 6))

        # Botão 2: Alt. 3
        self.btn_alt3 = tk.Button(
            alg_bar,
            text="🧠 Alt. 3: IA Neural Zero-DCE (GPU CUDA)",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=10,
            pady=3,
            command=lambda: self.switch_mode("deep")
        )
        self.btn_alt3.pack(side="left", padx=(0, 6))

        # Botão 3: Alt. 4
        self.btn_alt4 = tk.Button(
            alg_bar,
            text="📊 Alt. 4: Auto-Adaptativo por Histograma",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=10,
            pady=3,
            command=lambda: self.switch_mode("histogram")
        )
        self.btn_alt4.pack(side="left", padx=(0, 6))

        # Botão 4: Gemini Vision AI Pro (Nuvem Google)
        self.btn_gemini = tk.Button(
            alg_bar,
            text="💎 Gemini Vision (Plano AI Pro)",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=10,
            pady=3,
            command=lambda: self.switch_mode("gemini")
        )
        self.btn_gemini.pack(side="left", padx=(0, 6))

        # 3. Sub-painel Dinâmico de Controles e Informações
        self.ctrl_panel = ttk.Frame(self.top, padding=(10, 2, 10, 2))
        self.ctrl_panel.pack(side="top", fill="x", padx=8, pady=(0, 2))

        # Elementos do Modo Interativo (Alt. 1 + 2): Slider de Intensidade e Presets
        self.frame_interactive = ttk.Frame(self.ctrl_panel)
        ttk.Label(self.frame_interactive, text="Intensidade:", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 4))

        self.slider_var = tk.DoubleVar(value=50.0)
        self.slider = ttk.Scale(
            self.frame_interactive,
            from_=10.0,
            to=100.0,
            variable=self.slider_var,
            orient="horizontal",
            length=160,
            command=self._on_slider_moved
        )
        self.slider.pack(side="left", padx=(0, 6))

        self.lbl_slider_val = ttk.Label(self.frame_interactive, text="50%", font=("Segoe UI", 9, "bold"), foreground="#0066cc")
        self.lbl_slider_val.pack(side="left", padx=(0, 12))

        # Presets rápidos
        ttk.Label(self.frame_interactive, text="Presets:", font=("Segoe UI", 8, "italic"), foreground="#666").pack(side="left", padx=(0, 4))
        for p_name, p_val in [("Suave (25%)", 25.0), ("Equilibrado (50%)", 50.0), ("Marcante (75%)", 75.0), ("Intenso (95%)", 95.0)]:
            tk.Button(
                self.frame_interactive,
                text=p_name,
                font=("Segoe UI", 8),
                padx=5,
                pady=1,
                cursor="hand2",
                command=lambda val=p_val: self._set_intensity_preset(val)
            ).pack(side="left", padx=2)

        # Elementos de Informação para Alt. 3, Alt. 4 e Gemini
        self.lbl_deep_info = ttk.Label(
            self.ctrl_panel,
            text="🧠 Rede Neural Zero-DCE ativa: estima curvas de iluminação na GPU com amortecimento de superexposição (E = 0.60).",
            font=("Segoe UI", 8, "italic"),
            foreground="#005500"
        )
        self.lbl_hist_info = ttk.Label(
            self.ctrl_panel,
            text="📊 Compensação Auto-Adaptativa ativa: calculando distribuição de luz...",
            font=("Segoe UI", 8, "italic"),
            foreground="#773300"
        )
        self.frame_gemini = ttk.Frame(self.ctrl_panel)
        self.lbl_gemini_info = ttk.Label(
            self.frame_gemini,
            text="💎 Google Gemini Vision (Plano AI Pro): análise multimodal de sombras, altas luzes e balanço de branco na nuvem.",
            font=("Segoe UI", 8, "italic"),
            foreground="#004488"
        )
        self.lbl_gemini_info.pack(side="left", padx=(0, 8))
        ttk.Button(
            self.frame_gemini,
            text="🔑 Configurar Chave Gemini...",
            command=lambda: self.gui.prompt_gemini_api_key() if hasattr(self.gui, "prompt_gemini_api_key") else None
        ).pack(side="left")

        # 4. Bottom Action Bar (Empacotado no Rodapé antes da área central para NUNCA sumir ou ficar sob a barra de tarefas)
        footer = ttk.Frame(self.top, padding=(12, 6, 12, 6), relief="groove", borderwidth=1)
        footer.pack(side="bottom", fill="x", padx=8, pady=(2, 6))

        # Botão 1: Manter Original
        btn_keep_orig = tk.Button(
            footer,
            text="[Tecla 1 ou ←]  ⬅️ Manter FOTO ORIGINAL (Descartar)",
            font=("Segoe UI", 9, "bold"),
            bg="#f0f0f0",
            fg="#333333",
            activebackground="#e0e0e0",
            padx=14,
            pady=5,
            relief="solid",
            borderwidth=1,
            cursor="hand2",
            command=self.keep_original
        )
        btn_keep_orig.pack(side="left", padx=(0, 8))

        # Botão 2: Manter Melhorada
        btn_keep_enh = tk.Button(
            footer,
            text="[Tecla 2 ou →]  ➡️ Manter FOTO MELHORADA (Substituir Original)",
            font=("Segoe UI", 9, "bold"),
            bg="#28a745",
            fg="#ffffff",
            activebackground="#218838",
            activeforeground="#ffffff",
            padx=16,
            pady=5,
            relief="raised",
            borderwidth=2,
            cursor="hand2",
            command=self.keep_enhanced
        )
        btn_keep_enh.pack(side="left")

        # Botão Cancelar
        btn_cancel = ttk.Button(
            footer,
            text="Cancelar [ESC]",
            command=self.cancel
        )
        btn_cancel.pack(side="right", padx=(8, 0))

        # 5. Main Compare Area (Two Equal Columns) - Ocupa todo o espaço vertical restante
        self.content_area = ttk.Frame(self.top, padding=(8, 2, 8, 2))
        self.content_area.pack(side="top", fill="both", expand=True)

        self.content_area.columnconfigure(0, weight=1, uniform="group")
        self.content_area.columnconfigure(1, weight=1, uniform="group")
        self.content_area.rowconfigure(0, weight=1)

        # Left Column: ANTES (Original)
        self.left_frame = ttk.Frame(self.content_area, relief="solid", borderwidth=1, padding=3)
        self.left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 3), pady=1)

        left_top = ttk.Frame(self.left_frame)
        left_top.pack(fill="x", pady=(0, 2))
        ttk.Label(left_top, text="📷 ANTES (Foto Original)", font=("Segoe UI", 9, "bold"), foreground="#444444").pack(side="left")
        self.lbl_orig_info = ttk.Label(left_top, text="", font=("Segoe UI", 8), foreground="#666666")
        self.lbl_orig_info.pack(side="right")

        self.canvas_orig = tk.Canvas(self.left_frame, bg="#181818", highlightthickness=0)
        self.canvas_orig.pack(fill="both", expand=True)

        # Right Column: DEPOIS (Melhorada)
        self.right_frame = ttk.Frame(self.content_area, relief="solid", borderwidth=1, padding=3)
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=(3, 0), pady=1)

        right_top = ttk.Frame(self.right_frame)
        right_top.pack(fill="x", pady=(0, 2))
        self.lbl_enh_title = ttk.Label(right_top, text="✨ DEPOIS", font=("Segoe UI", 9, "bold"), foreground="#008800")
        self.lbl_enh_title.pack(side="left")
        self.lbl_enh_info = ttk.Label(right_top, text="", font=("Segoe UI", 8), foreground="#008800")
        self.lbl_enh_info.pack(side="right")

        self.canvas_enh = tk.Canvas(self.right_frame, bg="#181818", highlightthickness=0)
        self.canvas_enh.pack(fill="both", expand=True)

        self.content_area.bind("<Configure>", self._on_window_resize)

    def _update_algorithm_buttons_style(self):
        """Atualiza a aparência dos 4 botões indicando visualmente qual algoritmo está ativo."""
        active_style = {"bg": "#0066cc", "fg": "#ffffff", "activebackground": "#0052a3", "activeforeground": "#ffffff", "relief": "sunken"}
        inactive_style = {"bg": "#f0f4f8", "fg": "#222222", "activebackground": "#e2e8f0", "activeforeground": "#000000", "relief": "raised"}

        self.btn_alt1_2.config(**(active_style if self.current_mode == "interactive" else inactive_style))
        self.btn_alt3.config(**(active_style if self.current_mode == "deep" else inactive_style))
        self.btn_alt4.config(**(active_style if self.current_mode == "histogram" else inactive_style))
        self.btn_gemini.config(**(active_style if self.current_mode == "gemini" else inactive_style))

    def _update_control_panel_visibility(self):
        """Alterna quais controles específicos são exibidos no subpainel superior."""
        self.frame_interactive.pack_forget()
        self.lbl_deep_info.pack_forget()
        self.lbl_hist_info.pack_forget()
        self.frame_gemini.pack_forget()

        if self.current_mode == "interactive":
            self.frame_interactive.pack(side="left", fill="x")
            self.lbl_enh_title.config(text=f"✨ DEPOIS (Alt. 1+2 • {int(self.slider_var.get())}%)")
        elif self.current_mode == "deep":
            self.lbl_deep_info.pack(side="left", fill="x")
            self.lbl_enh_title.config(text="✨ DEPOIS (Alt. 3 • IA Zero-DCE)")
        elif self.current_mode == "histogram":
            self.lbl_hist_info.pack(side="left", fill="x")
            self.lbl_enh_title.config(text="✨ DEPOIS (Alt. 4 • Auto-Histograma)")
        elif self.current_mode == "gemini":
            self.frame_gemini.pack(side="left", fill="x")
            self.lbl_enh_title.config(text="💎 DEPOIS (Gemini Vision AI Pro)")

    def switch_mode(self, new_mode):
        """Alterna para um dos 3 algoritmos e recalcula a imagem aprimorada."""
        if self.current_mode == new_mode and not self.is_recomputing:
            return
        self.current_mode = new_mode
        self._update_algorithm_buttons_style()
        self._update_control_panel_visibility()
        self._trigger_recompute()

    def _on_slider_moved(self, val):
        """Callback acionado ao arrastar o controle deslizante de intensidade."""
        ival = int(float(val))
        self.lbl_slider_val.config(text=f"{ival}%")
        self.lbl_enh_title.config(text=f"✨ DEPOIS (Alt. 1+2 • {ival}%)")
        self.intensity_val = float(ival) / 100.0

        if self._debounce_timer is not None:
            try:
                self.top.after_cancel(self._debounce_timer)
            except Exception:
                pass
        self._debounce_timer = self.top.after(140, self._trigger_recompute)

    def _set_intensity_preset(self, val):
        """Aplica um valor pré-determinado de intensidade e recalcula."""
        self.slider_var.set(val)
        self.slider.set(val)
        self._on_slider_moved(val)

    def _trigger_recompute(self):
        """Inicia o recálculo assíncrono da imagem melhorada com o método selecionado."""
        if not self.orig_path.exists() or self._is_closed:
            return

        self.lbl_enh_info.config(text="⏳ Calculando nova imagem...", foreground="#0066cc")

        def _worker():
            try:
                from src.shadow_remover import (
                    remove_shadows_strict_highlight,
                    remove_shadows_deep_local,
                    remove_shadows_auto_histogram,
                    remove_shadows_gemini
                )

                if self.current_mode == "interactive":
                    res_img = remove_shadows_strict_highlight(self.orig_path, intensity=self.intensity_val)
                    detail = f"Altas Luzes Preservadas • Intensidade {int(self.intensity_val * 100)}%"
                elif self.current_mode == "deep":
                    res_img = remove_shadows_deep_local(self.orig_path)
                    detail = "IA Zero-DCE (PyTorch GPU CUDA)"
                elif self.current_mode == "histogram":
                    res_img, meta = remove_shadows_auto_histogram(self.orig_path)
                    detail = f"{meta['scenario']} ({int(meta['auto_intensity'] * 100)}%)"
                elif self.current_mode == "gemini":
                    res_img = remove_shadows_gemini(self.orig_path)
                    detail = "Google Gemini Vision (AI Pro)"

                # Salva em arquivo temporário
                cache_dir = Path(tempfile.gettempdir()) / "comparador_fotos_enhanced"
                cache_dir.mkdir(parents=True, exist_ok=True)
                tmp_out = cache_dir / f"{self.orig_path.stem}_preview_{self.current_mode}_{int(time.time()*1000)%10000}{self.orig_path.suffix}"

                try:
                    with Image.open(self.orig_path) as orig_im:
                        exif = orig_im.info.get("exif")
                    if exif and self.orig_path.suffix.lower() in [".jpg", ".jpeg"]:
                        res_img.save(tmp_out, quality=95, exif=exif)
                    else:
                        res_img.save(tmp_out, quality=95)
                except Exception:
                    res_img.save(tmp_out, quality=95)

                self._temp_files_to_clean.append(tmp_out)

                def _apply():
                    if getattr(self, "_is_closed", False) or not hasattr(self, "top") or not self.top.winfo_exists():
                        return
                    self.enh_path = tmp_out
                    if self.current_mode == "histogram" and "meta" in locals():
                        self.lbl_hist_info.config(text=f"📊 Cenário: {meta['scenario']} — {meta['description']}")
                    self.lbl_enh_info.config(foreground="#008800")
                    self._render_images()

                if not getattr(self, "_is_closed", False) and hasattr(self, "top"):
                    try:
                        self.top.after(0, _apply)
                    except Exception:
                        pass
            except Exception as e:
                log_error("Erro no recálculo da imagem de sombras", e)
                def _err():
                    if getattr(self, "_is_closed", False) or not hasattr(self, "top") or not self.top.winfo_exists():
                        return
                    self.lbl_enh_info.config(text=f"Erro ao processar: {e}", foreground="#cc0000")
                if not getattr(self, "_is_closed", False) and hasattr(self, "top"):
                    try:
                        self.top.after(0, _err)
                    except Exception:
                        pass

        threading.Thread(target=_worker, name=f"RecomputeShadow-{self.current_mode}", daemon=True).start()

    def _bind_keys(self):
        self.top.bind("<Key-1>", lambda e: self.keep_original())
        self.top.bind("<KP_1>", lambda e: self.keep_original())
        self.top.bind("<Left>", lambda e: self.keep_original())

        self.top.bind("<Key-2>", lambda e: self.keep_enhanced())
        self.top.bind("<KP_2>", lambda e: self.keep_enhanced())
        self.top.bind("<Right>", lambda e: self.keep_enhanced())

        self.top.bind("<F1>", lambda e: self.switch_mode("interactive"))
        self.top.bind("<F2>", lambda e: self.switch_mode("deep"))
        self.top.bind("<F3>", lambda e: self.switch_mode("histogram"))
        self.top.bind("<F4>", lambda e: self.switch_mode("gemini"))

        self.top.bind("<Escape>", lambda e: self.cancel())

    def _on_window_resize(self, event):
        if self._resize_job is not None:
            try:
                self.top.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.top.after(60, self._render_images)

    def _render_images(self):
        if not self.top.winfo_exists() or self._is_closed:
            return

        # Dimensões reais da janela para cálculo de fallback robusto
        win_w = max(700, self.top.winfo_width())
        win_h = max(450, self.top.winfo_height())

        w_orig = self.canvas_orig.winfo_width()
        if w_orig < 180:
            w_orig = max(200, (win_w // 2) - 30)
        h_orig = self.canvas_orig.winfo_height()
        if h_orig < 180:
            h_orig = max(200, win_h - 190)

        w_enh = self.canvas_enh.winfo_width()
        if w_enh < 180:
            w_enh = max(200, (win_w // 2) - 30)
        h_enh = self.canvas_enh.winfo_height()
        if h_enh < 180:
            h_enh = max(200, win_h - 190)

        # 1. Render Foto Original em Alta Resolução (Aspect-Fit Preenchendo 100% da Área)
        try:
            if self.orig_path.exists():
                from src.auto_orienter import load_image_with_orientation
                im = load_image_with_orientation(self.orig_path)
                orig_w, orig_h = im.size
                orig_sz = format_size(self.orig_path.stat().st_size)
                self.lbl_orig_info.config(text=f"{orig_w}x{orig_h} • {orig_sz}")

                # Escala proporcional máxima preenchendo o canvas
                scale = min((w_orig - 6) / orig_w, (h_orig - 6) / orig_h)
                target_w = max(1, int(orig_w * scale))
                target_h = max(1, int(orig_h * scale))

                im_scaled = im.resize((target_w, target_h), Image.Resampling.LANCZOS)
                self._img_orig_tk = ImageTk.PhotoImage(im_scaled)
                self.canvas_orig.delete("all")
                self.canvas_orig.create_image(w_orig // 2, h_orig // 2, image=self._img_orig_tk, anchor="center")
        except Exception as e:
            log_error("Erro ao renderizar foto original na comparação", e)

        # 2. Render Foto Melhorada em Alta Resolução (Aspect-Fit Preenchendo 100% da Área)
        try:
            if self.enh_path.exists():
                from src.auto_orienter import load_image_with_orientation
                im = load_image_with_orientation(self.enh_path)
                enh_w, enh_h = im.size
                enh_sz = format_size(self.enh_path.stat().st_size)
                mode_names = {
                    "interactive": "Alt. 1+2 Interativo",
                    "deep": "Alt. 3 IA Zero-DCE",
                    "histogram": "Alt. 4 Auto-Histograma",
                    "gemini": "Gemini Vision AI Pro"
                }
                mode_label = mode_names.get(self.current_mode, self.current_mode)
                self.lbl_enh_info.config(text=f"{enh_w}x{enh_h} • {enh_sz} • {mode_label}")

                scale = min((w_enh - 6) / enh_w, (h_enh - 6) / enh_h)
                target_w = max(1, int(enh_w * scale))
                target_h = max(1, int(enh_h * scale))

                im_scaled = im.resize((target_w, target_h), Image.Resampling.LANCZOS)
                self._img_enh_tk = ImageTk.PhotoImage(im_scaled)
                self.canvas_enh.delete("all")
                self.canvas_enh.create_image(w_enh // 2, h_enh // 2, image=self._img_enh_tk, anchor="center")
        except Exception as e:
            log_error("Erro ao renderizar foto melhorada na comparação", e)

    def _cleanup_jobs(self):
        if self._startup_job is not None:
            try:
                self.top.after_cancel(self._startup_job)
            except Exception:
                pass
            self._startup_job = None
        if self._resize_job is not None:
            try:
                self.top.after_cancel(self._resize_job)
            except Exception:
                pass
            self._resize_job = None
        if self._debounce_timer is not None:
            try:
                self.top.after_cancel(self._debounce_timer)
            except Exception:
                pass
            self._debounce_timer = None

    def keep_original(self):
        """Descarta a versão melhorada e mantém a foto original intacta."""
        self._is_closed = True
        self._cleanup_jobs()
        for f in self._temp_files_to_clean:
            try:
                if f.exists():
                    f.unlink(missing_ok=True)
            except Exception:
                pass

        if hasattr(self.gui, "status"):
            self.gui.status.set(f"Melhoria descartada. Foto original mantida: {self.orig_path.name}")

        try:
            self.top.destroy()
        except Exception:
            pass

        if self.on_decision:
            self.on_decision("original", self.orig_path)

    def keep_enhanced(self):
        """
        Substitui com segurança a foto original pela foto melhorada selecionada.
        A foto original é enviada para a Lixeira do Windows (send2trash).
        """
        self._is_closed = True
        self._cleanup_jobs()
        if not self.enh_path.exists():
            messagebox.showerror("Erro", "Arquivo com melhorias não encontrado.", parent=self.top)
            return

        try:
            # 1. Envia a foto original para a Lixeira do Windows
            if self.orig_path.exists():
                send2trash(str(self.orig_path))

            # 2. Move a foto melhorada para o caminho original
            shutil.move(str(self.enh_path), str(self.orig_path))
            log_info(f"Foto original substituída com sucesso pela versão sem sombras: {self.orig_path}")

            # 3. Limpa outros arquivos temporários criados nas trocas de modo
            for f in self._temp_files_to_clean:
                try:
                    if f.exists() and f != self.orig_path:
                        f.unlink(missing_ok=True)
                except Exception:
                    pass

            # 4. Invalida caches na interface para recarregar miniaturas e dados novos
            if hasattr(self.gui, "thumb_cache"):
                keys_to_del = [k for k in self.gui.thumb_cache.keys() if str(self.orig_path) in k]
                for k in keys_to_del:
                    self.gui.thumb_cache.pop(k, None)

            if hasattr(self.gui, "info_cache"):
                self.gui.info_cache.pop(str(self.orig_path), None)

            if hasattr(self.gui, "sharpness_cache"):
                self.gui.sharpness_cache.pop(str(self.orig_path), None)

            if hasattr(self.gui, "status"):
                self.gui.status.set(f"✨ Foto substituída com sucesso pela versão sem sombras: {self.orig_path.name}")

            # 5. Atualiza a tela
            if hasattr(self.gui, "refresh_results"):
                self.gui.refresh_results(keep_page=True)

        except Exception as e:
            log_error(f"Erro ao salvar foto melhorada em {self.orig_path}", e)
            messagebox.showerror("Erro ao Salvar", f"Não foi possível substituir a foto:\n{e}", parent=self.top)
            return

        try:
            self.top.destroy()
        except Exception:
            pass

        if self.on_decision:
            self.on_decision("enhanced", self.orig_path)

    def cancel(self):
        """Fecha o diálogo mantendo a original e descartando os arquivos temporários."""
        self.keep_original()
