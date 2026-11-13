# -*- coding: utf-8 -*-
import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from dotenv import load_dotenv

from .config import DEFAULT_LANGUAGE
from .organizer import MediaOrganizer, _calculate_confidence
from .metadata import available_tv_providers, create_metadata_clients
from .utils import clean_query_string, clean_book_query, sanitize_filename


# Load .env early
load_dotenv(override=False)


class MediaRenamerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Media Renamer · Organizador multimedia")
        self.root.minsize(900, 620)
        self.root.geometry("1060x720")

        self.organizer = MediaOrganizer(*create_metadata_clients())
        self.plan = None
        self.selected_moves = set()
        self._active_folder = None
        self._busy = False

        self.path_var = tk.StringVar()
        self.query_var = tk.StringVar()
        self.lang_var = tk.StringVar(value=DEFAULT_LANGUAGE)
        self.type_var = tk.StringVar(value="auto")
        self.tv_providers = [*available_tv_providers(), "Sin proveedor (limpiar)"]
        self.tv_provider_var = tk.StringVar(value=self.tv_providers[0])
        self.season_var = tk.StringVar()   # Temporada forzada (opcional, solo series)
        self.scan_mode_var = tk.StringVar(value="Solo esta carpeta")
        self.dry_run_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Elegí una carpeta o archivo y pulsá Planificar.")
        self.total_var = tk.StringVar(value="Total: 0 archivo(s)")

        self._configure_style()
        self._build()

    def _configure_style(self):
        """Define una apariencia sobria y legible sin depender de temas externos."""
        self.root.configure(background="#f4f7fb")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#f4f7fb")
        style.configure("Header.TFrame", background="#1f3a5f")
        style.configure("Header.TLabel", background="#1f3a5f", foreground="white",
                        font=("Segoe UI", 16, "bold"))
        style.configure("HeaderSub.TLabel", background="#1f3a5f", foreground="#d9e7f7",
                        font=("Segoe UI", 9))
        style.configure("Card.TLabelframe", background="#f4f7fb", padding=8)
        style.configure("Card.TLabelframe.Label", background="#f4f7fb", foreground="#1f3a5f",
                        font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"), padding=(12, 6))
        style.configure("Treeview", rowheight=27, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#dcecff")], foreground=[("selected", "#14263d")])

    def _build(self):
        pad = {"padx": 8, "pady": 4}
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(18, 12))
        header.pack(fill=tk.X)
        ttk.Label(header, text="Media Renamer", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(header, text="Ordená, limpiá y revisá tus videos antes de modificar archivos.",
                  style="HeaderSub.TLabel").pack(anchor=tk.W, pady=(2, 0))

        top = ttk.LabelFrame(self.root, text="Origen y metadatos", style="Card.TLabelframe", padding=8)
        top.pack(fill=tk.X, padx=12, pady=(12, 6))

        ttk.Label(top, text="Ruta:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(top, textvariable=self.path_var).grid(row=0, column=1, columnspan=3, sticky=tk.EW, **pad)
        ttk.Button(top, text="Carpeta…", command=self._browse_folder).grid(row=0, column=4, **pad)
        ttk.Button(top, text="Archivo…", command=self._browse_file).grid(row=0, column=5, **pad)

        ttk.Label(top, text="Tipo:").grid(row=1, column=0, sticky=tk.W)
        type_row = ttk.Frame(top)
        type_row.grid(row=1, column=1, sticky=tk.W)
        for value, label in (("auto", "Auto"), ("tv", "Serie"), ("movie", "Película"), ("bulk-movie", "Múltiples Películas (Bulk)"), ("book", "Libros / Cómics")):
            ttk.Radiobutton(type_row, text=label, value=value, variable=self.type_var, command=self._on_type_change).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(top, text="Idioma:").grid(row=1, column=2, sticky=tk.E)
        ttk.Entry(top, textvariable=self.lang_var, width=10).grid(row=1, column=3, sticky=tk.W, **pad)

        ttk.Label(top, text="Proveedor series:").grid(row=1, column=4, sticky=tk.E)
        self.provider_combo = ttk.Combobox(
            top, textvariable=self.tv_provider_var, values=self.tv_providers,
            state="readonly", width=22,
        )
        self.provider_combo.grid(row=1, column=5, sticky=tk.W, **pad)

        self.query_label = ttk.Label(top, text="Serie/película:")
        self.query_label.grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(top, textvariable=self.query_var).grid(row=2, column=1, columnspan=3, sticky=tk.EW, **pad)
        self.query_hint = ttk.Label(top, text="(vacío = autodetección)")
        self.query_hint.grid(row=2, column=4, columnspan=2, sticky=tk.W)

        ttk.Label(top, text="Temporada:").grid(row=3, column=0, sticky=tk.W)
        season_row = ttk.Frame(top)
        season_row.grid(row=3, column=1, sticky=tk.W)
        ttk.Entry(season_row, textvariable=self.season_var, width=6).pack(side=tk.LEFT)
        ttk.Label(season_row, text="  (número, opcional — fuerza la temporada para series sin acrónimo conocido)",
                  foreground="gray").pack(side=tk.LEFT)

        ttk.Label(top, text="Exploración:").grid(row=3, column=4, sticky=tk.E)
        self.scan_mode_combo = ttk.Combobox(
            top, textvariable=self.scan_mode_var,
            values=("Solo esta carpeta", "Incluir subcarpetas (lote)"),
            state="readonly", width=25,
        )
        self.scan_mode_combo.grid(row=3, column=5, sticky=tk.W, **pad)

        btns = ttk.Frame(top)
        btns.grid(row=4, column=0, columnspan=6, sticky=tk.W, pady=(6, 0))
        self.plan_btn = ttk.Button(btns, text="Planificar", command=self._on_plan, style="Accent.TButton")
        self.plan_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.apply_btn = ttk.Button(btns, text="Aplicar", command=self._on_apply, state=tk.DISABLED)
        self.apply_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.undo_btn = ttk.Button(btns, text="Deshacer última", command=self._on_undo)
        self.undo_btn.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Checkbutton(btns, text="Dry-run (simular)", variable=self.dry_run_var).pack(side=tk.LEFT, padx=(12, 0))

        top.columnconfigure(1, weight=1)

        mid = ttk.LabelFrame(self.root, text="Vista previa · clic en ✓ para incluir o excluir un archivo",
                             style="Card.TLabelframe", padding=8)
        mid.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        columns = ("use", "src", "dest")
        self.tree = ttk.Treeview(mid, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("use", text="Usar")
        self.tree.heading("src", text="Archivo actual")
        self.tree.heading("dest", text="Destino relativo")
        self.tree.column("use", width=52, stretch=False, anchor=tk.CENTER)
        self.tree.column("src", width=420, stretch=True)
        self.tree.column("dest", width=470, stretch=True)
        scroll_y = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Button-1>", self._toggle_move)

        select_actions = ttk.Frame(mid)
        select_actions.pack(side=tk.BOTTOM, fill=tk.X, pady=(7, 0))
        ttk.Button(select_actions, text="Seleccionar todos", command=lambda: self._set_all_moves(True)).pack(side=tk.LEFT)
        ttk.Button(select_actions, text="Quitar todos", command=lambda: self._set_all_moves(False)).pack(side=tk.LEFT, padx=(6, 0))

        bottom = ttk.Frame(self.root, style="App.TFrame", padding=(12, 2, 12, 10))
        bottom.pack(fill=tk.BOTH)
        meta = ttk.Frame(bottom)
        meta.pack(fill=tk.X)
        ttk.Label(meta, textvariable=self.total_var).pack(side=tk.LEFT)
        
        # Confidence legend
        legend = ttk.Frame(meta)
        legend.pack(side=tk.LEFT, padx=(20, 0))
        ttk.Label(legend, text="Confianza:", foreground="#555").pack(side=tk.LEFT)
        self._make_legend_item(legend, "conf_high", "  ●  ", "Alta", "#2e7d32")
        self._make_legend_item(legend, "conf_medium", "  ●  ", "Media", "#f9a825")
        self._make_legend_item(legend, "conf_low", "  ●  ", "Baja", "#c62828")
        
        ttk.Label(meta, textvariable=self.status_var).pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(meta, mode="indeterminate", length=130)
        self.progress.pack(side=tk.RIGHT, padx=(0, 14))

        # Hint about double-click for re-selection
        self.hint_var = tk.StringVar(value="💡 Doble clic en filas amarillas/rojas para cambiar coincidencia")
        ttk.Label(meta, textvariable=self.hint_var, foreground="#666", font=("Segoe UI", 8)).pack(side=tk.RIGHT, padx=(0, 14))

        ttk.Label(bottom, text="Log:").pack(anchor=tk.W, pady=(8, 2))
        self.log = tk.Text(bottom, height=8, wrap=tk.WORD, state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(bottom, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_frame = ttk.Frame(bottom)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log.pack(in_=log_frame, side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(in_=log_frame, side=tk.RIGHT, fill=tk.Y)

        # Initial UI state based on default type
        self._on_type_change()

    def _make_legend_item(self, parent, tag, symbol, text, color):
        """Crea un elemento de la leyenda de confianza."""
        lbl = tk.Label(parent, text=symbol + text, foreground=color, font=("Segoe UI", 9))
        lbl.pack(side=tk.LEFT, padx=(8, 2))
        # Para que el color se vea bien, usamos un canvas pequeño
        canvas = tk.Canvas(parent, width=14, height=14, highlightthickness=0, bg=self.root.cget("bg"))
        canvas.create_oval(2, 2, 12, 12, fill=color, outline=color)
        canvas.pack(side=tk.LEFT, padx=(0, 8))

    def _on_type_change(self):
        """Enable/disable fields based on selected media type."""
        media_type = self.type_var.get()
        is_bulk_movie = media_type == "bulk-movie"
        is_tv = media_type == "tv"
        is_book = media_type == "book"
        
        # Season field only for TV
        season_state = "normal" if is_tv else "disabled"
        for child in self.root.winfo_children():
            if isinstance(child, ttk.LabelFrame) and "Origen y metadatos" in str(child.cget("text")):
                for widget in child.winfo_children():
                    if isinstance(widget, ttk.Frame):
                        for w in widget.winfo_children():
                            if isinstance(w, ttk.Entry) and w.cget("textvariable") == str(self.season_var):
                                w.configure(state=season_state)
                            if isinstance(w, ttk.Label) and "Temporada" in str(w.cget("text")):
                                w.configure(foreground="gray" if not is_tv else "#1f3a5f")
        
        # Scan mode only relevant for TV batch and books
        scan_state = "readonly" if (is_tv or media_type == "auto" or is_book) else "disabled"
        self.scan_mode_combo.configure(state=scan_state)
        
        # Query field label
        if is_book:
            self.query_label.configure(text="Libro/título:")
        elif is_bulk_movie:
            self.query_label.configure(text="Carpeta de películas:")
        else:
            self.query_label.configure(text="Serie/película:")
        self.query_hint.configure(
            text="(vacío = autodetección por archivo)" if (is_bulk_movie or is_book)
            else "(vacío = autodetección)"
        )

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Elegí la carpeta a organizar")
        if path:
            self.path_var.set(path)
            self._suggest_query(path)

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Elegí el archivo de video, libro o cómic",
            filetypes=[
                ("Video", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.webm *.m4v *.strm"),
                ("Libros", "*.epub *.mobi *.azw *.azw3 *.pdf *.fb2 *.djvu *.lit"),
                ("Cómics", "*.cbz *.cbr"),
                ("Todos", "*.*"),
            ],
        )
        if path:
            self.path_var.set(path)
            self._suggest_query(path)

    def _suggest_query(self, path: str):
        if self.query_var.get().strip():
            return
        base = os.path.basename(os.path.abspath(path.rstrip("*?")))
        raw = os.path.splitext(base)[0] if os.path.isfile(path) else base
        if self.type_var.get() == "book":
            cleaned, _ = clean_book_query(raw)
        else:
            cleaned, _ = clean_query_string(raw)
        if cleaned:
            self.query_var.set(cleaned)

    def _append_log(self, message: str):
        if message is None:
            return
        text = str(message).rstrip("\n")
        if not text:
            return

        def write():
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, text + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

        self.root.after(0, write)

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.plan_btn.configure(state=state)
        self.undo_btn.configure(state=state)
        self.provider_combo.configure(state=tk.DISABLED if busy else "readonly")
        self.scan_mode_combo.configure(state=tk.DISABLED if busy else "readonly")
        self.progress.start(12) if busy else self.progress.stop()
        apply_state = tk.DISABLED if busy or not self.plan else tk.NORMAL
        self.apply_btn.configure(state=apply_state)

    def _clear_plan_view(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.plan = None
        self.selected_moves.clear()
        self.total_var.set("Seleccionados: 0 de 0 archivo(s)")
        self.apply_btn.configure(state=tk.DISABLED)

    def _show_plan(self, plan: dict | None):
        self._clear_plan_view()
        if not plan:
            self.status_var.set("No se pudo armar un plan. Revisá el log.")
            return
        self.plan = plan
        self.selected_moves = set(range(len(plan["moves"])))
        is_batch = plan.get("type") == "batch"
        self.tree.heading("src", text="Carpeta / archivo" if is_batch else "Archivo actual")
        self.tree.heading("dest", text="Destino en el lote" if is_batch else "Destino relativo")

        # Configure confidence color tags
        self.tree.tag_configure("conf_high", background="#d4f6d4")    # Verde claro
        self.tree.tag_configure("conf_medium", background="#fff9c4")  # Amarillo claro
        self.tree.tag_configure("conf_low", background="#ffcdd2")     # Rojo claro

        for index, move in enumerate(plan["moves"]):
            if is_batch:
                source_name = os.path.relpath(move["src"], plan["dest_main_folder"])
                destination_name = os.path.relpath(move["dest"], plan["dest_main_folder"])
            else:
                source_name = os.path.basename(move["src"])
                destination_name = move["rel_dest"]

            # Determine confidence tag
            confidence = move.get("confidence", "medium")
            tag = f"conf_{confidence}"

            self.tree.insert("", tk.END, iid=f"move-{index}", values=("✓", source_name, destination_name), tags=(tag,))

        # Bind double-click for re-selection (only for items with candidates)
        self.tree.bind("<Double-Button-1>", self._on_double_click_reselect, add='+')
        # Guard to prevent double-dialog
        self._reselect_dialog_open = False

        self._update_selection_total()
        self.status_var.set(f"Plan: {plan['title']} → {plan['dest_main_folder']}")
        self.apply_btn.configure(state=tk.NORMAL if plan["moves"] else tk.DISABLED)

    def _toggle_move(self, event):
        if self.tree.identify_column(event.x) != "#1":
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        index = int(row.removeprefix("move-"))
        if index in self.selected_moves:
            self.selected_moves.remove(index)
            self.tree.set(row, "use", "")
        else:
            self.selected_moves.add(index)
            self.tree.set(row, "use", "✓")
        self._update_selection_total()
        return "break"

    def _on_double_click_reselect(self, event):
        """Handle double-click on a move item to re-select from candidates."""
        # Prevent double-dialog if already open
        if getattr(self, '_reselect_dialog_open', False):
            return
        self._reselect_dialog_open = True
        try:
            row = self.tree.identify_row(event.y)
            if not row:
                return
            # Only allow double-click on non-checkbox columns
            col = self.tree.identify_column(event.x)
            if col == "#1":
                return
            index = int(row.removeprefix("move-"))
            if not self.plan or index >= len(self.plan["moves"]):
                return
            
            move = self.plan["moves"][index]
            candidates = move.get("candidates")
            if not candidates:
                return  # No alternatives to choose from
            
            # Show selection dialog
            chosen = self._show_candidate_selection_dialog(candidates, move)
            if chosen:
                self._update_move_with_choice(index, chosen)
        finally:
            self._reselect_dialog_open = False

    def _show_candidate_selection_dialog(self, candidates: list, move: dict) -> dict | None:
        """Show dialog to select from TMDb candidates. Returns chosen candidate or None."""
        result_holder = [None]
        dialog_done = [False]

        def show_dialog():
            win = tk.Toplevel(self.root)
            win.title("Seleccionar coincidencia correcta")
            win.grab_set()  # Modal dialog
            win.resizable(False, False)

            # Center on main window and force to front
            self.root.update_idletasks()
            x = self.root.winfo_x() + self.root.winfo_width() // 2 - 300
            y = self.root.winfo_y() + self.root.winfo_height() // 2 - 250
            # Ensure window is on screen
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            if x < 0: x = 0
            if y < 0: y = 0
            if x + 600 > screen_w: x = screen_w - 600
            if y + 500 > screen_h: y = screen_h - 500
            win.geometry(f"600x500+{x}+{y}")
            # Force to front
            win.lift()
            win.attributes('-topmost', True)
            win.after(100, lambda: win.attributes('-topmost', False))

            ttk.Label(
                win,
                text=f"Múltiples coincidencias para: {os.path.basename(move['src'])}",
                wraplength=560
            ).pack(padx=12, pady=(12, 6), anchor=tk.W)
            
            current_dest = move.get('rel_dest', '')
            ttk.Label(
                win,
                text=f"Destino actual: {current_dest}",
                foreground="#555",
                wraplength=560,
            ).pack(padx=12, pady=(0, 6), anchor=tk.W)

            frame = ttk.Frame(win)
            frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

            is_book = bool(candidates and (
                candidates[0].get('author') is not None
                or candidates[0].get('source') in ('Open Library', 'Google Books')
            ))
            if is_book:
                cols = ("name", "author", "year", "source")
                tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse", height=12)
                tree.heading("name", text="Título")
                tree.heading("author", text="Autor")
                tree.heading("year", text="Año")
                tree.heading("source", text="Fuente")
                tree.column("name", width=260, stretch=True)
                tree.column("author", width=150, stretch=True)
                tree.column("year", width=55, stretch=False)
                tree.column("source", width=95, stretch=False)
            else:
                cols = ("name", "year", "id")
                tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse", height=12)
                tree.heading("name", text="Título")
                tree.heading("year", text="Año")
                tree.heading("id", text="ID TMDb")
                tree.column("name", width=380, stretch=True)
                tree.column("year", width=60, stretch=False)
                tree.column("id", width=90, stretch=False)

            sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sb.pack(side=tk.RIGHT, fill=tk.Y)

            for i, c in enumerate(candidates):
                if is_book:
                    values = (c.get('name'), c.get('author') or '', c.get('year') or '', c.get('source') or '')
                else:
                    values = (c['name'], c['year'], c['id'])
                tree.insert("", tk.END, iid=str(i), values=values)

            tree.selection_set("0")
            tree.focus("0")

            btn_frame = ttk.Frame(win)
            btn_frame.pack(fill=tk.X, padx=12, pady=(6, 12))

            def on_select():
                sel = tree.selection()
                if sel:
                    idx = int(sel[0])
                    result_holder[0] = candidates[idx]
                dialog_done[0] = True
                win.destroy()

            def on_cancel():
                result_holder[0] = None
                dialog_done[0] = True
                win.destroy()

            ttk.Button(btn_frame, text="Seleccionar", command=on_select).pack(side=tk.LEFT, padx=(0, 8))
            ttk.Button(btn_frame, text="Cancelar", command=on_cancel).pack(side=tk.LEFT)

            tree.bind("<Double-1>", lambda _: on_select())
            win.protocol("WM_DELETE_WINDOW", on_cancel)

            # Wait for dialog to complete using local event loop
            win.wait_window()  # This blocks until window is destroyed

        # Show dialog immediately (no after() needed since we use wait_window)
        show_dialog()
        return result_holder[0]

    def _update_move_with_choice(self, index: int, chosen: dict):
        """Update the move with the newly selected candidate."""
        if not self.plan or index >= len(self.plan["moves"]):
            return
        
        move = self.plan["moves"][index]
        if move.get("book"):
            self.organizer.rebuild_book_move(move, chosen)
            self.tree.set(f"move-{index}", "dest", move.get('rel_dest', ''))
            self.tree.item(f"move-{index}", tags=("conf_high",))
            self._append_log(f"[i] Re-seleccionado: {move.get('rel_dest', '')} (confianza: high)")
            return

        # Update move info with chosen candidate
        title = sanitize_filename(chosen.get('name', ''))
        year_str = f" ({chosen.get('year')})" if chosen.get('year') else ""
        ext = os.path.splitext(move['src'])[1]
        canonical = f"{title}{year_str}{ext}"
        
        # Update destination
        dest_base = self.plan['dest_main_folder']
        new_dest = os.path.join(dest_base, canonical)
        
        move['dest'] = new_dest
        move['rel_dest'] = canonical
        
        # Update treeview
        source_name = os.path.basename(move['src'])
        self.tree.set(f"move-{index}", "dest", canonical)
        
        # Recalculate confidence
        from media_renamer.organizer import _calculate_confidence
        clean_title = chosen['name'].lower()
        detected_year = chosen['year'] if chosen['year'] != '?' else None
        confidence_level, confidence_score = _calculate_confidence(
            clean_title, detected_year, chosen,
            candidates_count=len(move.get('candidates', [])),
            auto_selected=False,
            user_selected=True,
        )
        move['confidence'] = confidence_level
        move['confidence_score'] = confidence_score
        move['title'] = title
        move['year'] = chosen['year']
        
        # Update tag/color
        tag = f"conf_{confidence_level}"
        self.tree.item(f"move-{index}", tags=(tag,))
        
        # Remove candidates since user made a choice
        move.pop('candidates', None)
        
        self._append_log(f"[i] Re-seleccionado: {canonical} (confianza: {confidence_level})")

    def _set_all_moves(self, selected: bool):
        if not self.plan:
            return
        self.selected_moves = set(range(len(self.plan["moves"]))) if selected else set()
        for index in range(len(self.plan["moves"])):
            self.tree.set(f"move-{index}", "use", "✓" if selected else "")
        self._update_selection_total()

    def _update_selection_total(self):
        total = len(self.plan["moves"]) if self.plan else 0
        self.total_var.set(f"Seleccionados: {len(self.selected_moves)} de {total} archivo(s)")

    def _resolve_media_type(self, path: str) -> str:
        chosen = self.type_var.get()
        if chosen in ("tv", "movie", "bulk-movie", "book"):
            return chosen
        files = self.organizer.find_files_in_paths(path)
        if files:
            return "movie" if len(files) == 1 else "tv"
        if self.organizer.find_book_files_in_paths(path):
            return "book"
        return "tv"

    @staticmethod
    def _combine_plans(plans: list[dict], root_path: str) -> dict | None:
        """Une planes de carpetas independientes en una sola operación reversible."""
        if not plans:
            return None
        if len(plans) == 1:
            return plans[0]
        return {
            "type": "batch",
            "title": f"Lote de {len(plans)} carpetas",
            "source_folders": [folder for plan in plans for folder in plan["source_folders"]],
            "dest_main_folder": root_path,
            "moves": [move for plan in plans for move in plan["moves"]],
        }

    def _append_plan(self, item_plan: dict, root_path: str):
        """Incorpora una carpeta revisada manualmente al plan actual."""
        if not self.plan:
            self._show_plan(item_plan)
            return
        combined = {
            "type": "batch",
            "title": "Lote con carpetas revisadas",
            "source_folders": [*self.plan["source_folders"], *item_plan["source_folders"]],
            "dest_main_folder": root_path,
            "moves": [*self.plan["moves"], *item_plan["moves"]],
        }
        self._show_plan(combined)

    def _show_review_queue(self, folders: list[str], tv_provider: str, lang: str, root_path: str):
        """Permite resolver manualmente las carpetas que no devolvieron metadatos."""
        if not folders:
            return

        win = tk.Toplevel(self.root)
        win.title("Carpetas pendientes de reconocer")
        win.geometry("680x540")
        win.minsize(620, 460)
        win.transient(self.root)
        win.lift()

        ttk.Label(
            win,
            text="Estas carpetas no se incorporaron al lote. Elegí una y decidí cómo procesarla.",
            wraplength=570,
        ).pack(anchor=tk.W, padx=14, pady=(14, 8))

        frame = ttk.Frame(win, padding=(14, 0))
        frame.pack(fill=tk.BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=("folder",), show="headings", selectmode="browse", height=8)
        tree.heading("folder", text="Carpeta pendiente")
        tree.column("folder", width=540, stretch=True)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        folder_by_id = {}
        for index, folder in enumerate(folders):
            iid = f"pending-{index}"
            folder_by_id[iid] = folder
            tree.insert("", tk.END, iid=iid, values=(os.path.basename(folder),))
        first = next(iter(folder_by_id), None)
        if first:
            tree.selection_set(first)

        controls = ttk.Frame(win, padding=14)
        controls.pack(fill=tk.X)
        ttk.Label(controls, text="Nombre:").grid(row=0, column=0, sticky=tk.W)
        query_var = tk.StringVar()
        ttk.Entry(controls, textvariable=query_var).grid(row=0, column=1, columnspan=3, sticky=tk.EW, padx=(8, 0))
        ttk.Label(controls, text="Procesar como:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        mode_var = tk.StringVar(value="Serie")
        ttk.Combobox(controls, textvariable=mode_var, state="readonly", width=22,
                     values=("Serie", "Película", "Solo limpiar nombres")).grid(
                         row=1, column=1, sticky=tk.W, padx=(8, 0), pady=(8, 0)
                     )
        ttk.Label(controls, text="Forzar temporada:").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        season_var = tk.StringVar(value="")
        season_combo = ttk.Combobox(controls, textvariable=season_var, state="readonly", width=32, values=("",))
        season_combo.grid(row=2, column=1, sticky=tk.W, padx=(8, 0), pady=(8, 0))
        load_seasons_btn = ttk.Button(controls, text="Buscar temporadas")
        load_seasons_btn.grid(row=2, column=2, sticky=tk.W, padx=(8, 0), pady=(8, 0))
        ttk.Label(controls, text="Vacío = automático", foreground="#45627f").grid(
            row=2, column=3, sticky=tk.W, padx=(8, 0), pady=(8, 0)
        )
        controls.columnconfigure(3, weight=1)

        status_var = tk.StringVar(value="Elegí una carpeta para continuar.")
        ttk.Label(win, textvariable=status_var, foreground="#45627f").pack(anchor=tk.W, padx=14)
        actions = ttk.Frame(win, padding=(14, 8, 14, 14))
        actions.pack(fill=tk.X)

        def selected_folder():
            selected = tree.selection()
            return folder_by_id.get(selected[0]) if selected else None

        def suggest_name(_=None):
            folder = selected_folder()
            if folder:
                suggested, _ = clean_query_string(os.path.basename(folder))
                query_var.set(suggested)

        def remove_selected():
            selected = tree.selection()
            if not selected:
                return
            for iid in selected:
                folder_by_id.pop(iid, None)
                tree.delete(iid)
            if not tree.get_children():
                win.destroy()
            else:
                tree.selection_set(tree.get_children()[0])
                suggest_name()

        def add_to_plan():
            folder = selected_folder()
            query = query_var.get().strip()
            if not folder:
                status_var.set("Elegí una carpeta pendiente.")
                return
            if not query and mode_var.get() != "Solo limpiar nombres":
                status_var.set("Escribí el nombre para buscar antes de agregarla.")
                return

            status_var.set("Buscando y preparando la carpeta…")
            self._set_busy(True)
            forced_season = season_numbers.get(season_var.get())

            def work():
                item_plan = None
                error = None
                try:
                    if mode_var.get() == "Solo limpiar nombres":
                        item_plan = self.organizer.plan_clean_filenames(folder, show_title=query or None,
                                                                         log_fn=self._append_log)
                    else:
                        self._active_folder = folder
                        organizer = MediaOrganizer(*create_metadata_clients(tv_provider))
                        if mode_var.get() == "Serie":
                            item_plan = organizer.plan_tv_show(
                                folder, query, lang=lang, log_fn=self._append_log,
                                pick_result_fn=self._pick_tmdb_result, forced_season=forced_season,
                            )
                        else:
                            item_plan = organizer.plan_movie(
                                folder, query, lang=lang, log_fn=self._append_log,
                                pick_result_fn=self._pick_tmdb_result,
                            )
                except Exception as exc:
                    error = str(exc)

                def done():
                    self._active_folder = None
                    self._set_busy(False)
                    if item_plan:
                        self._append_plan(item_plan, root_path)
                        status_var.set("Carpeta agregada al plan.")
                        remove_selected()
                    else:
                        status_var.set(f"No se pudo preparar la carpeta.{': ' + error if error else ''}")

                self.root.after(0, done)

            threading.Thread(target=work, daemon=True).start()

        season_numbers = {}

        def load_seasons():
            folder = selected_folder()
            query = query_var.get().strip()
            if mode_var.get() != "Serie":
                status_var.set("La temporada forzada solo se usa para series.")
                return
            if not folder or not query:
                status_var.set("Elegí una carpeta y escribí el nombre de la serie.")
                return
            status_var.set("Buscando temporadas disponibles…")
            self._set_busy(True)

            def work():
                show_info = None
                error = None
                try:
                    self._active_folder = folder
                    organizer = MediaOrganizer(*create_metadata_clients(tv_provider))
                    show_info = organizer.tv.search_tv(query, lang=lang, pick_result_fn=self._pick_tmdb_result)
                except Exception as exc:
                    error = str(exc)

                def done():
                    self._active_folder = None
                    self._set_busy(False)
                    if not show_info:
                        status_var.set(f"No se encontraron temporadas.{': ' + error if error else ''}")
                        return
                    season_numbers.clear()
                    options = [""]
                    for number, count in sorted(show_info.get("season_counts", {}).items()):
                        season_name = show_info.get("season_names", {}).get(number)
                        label = (f"Temporada {number} · {season_name}" if season_name
                                 else f"Temporada {number} ({count} episodios)")
                        season_numbers[label] = number
                        options.append(label)
                    season_combo.configure(values=options)
                    season_var.set("")
                    status_var.set("Elegí una temporada solo si querés forzarla; vacío mantiene el modo automático.")

                self.root.after(0, done)

            threading.Thread(target=work, daemon=True).start()

        ttk.Button(actions, text="Agregar al plan", command=add_to_plan, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(actions, text="Omitir", command=remove_selected).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Cerrar", command=win.destroy).pack(side=tk.RIGHT)
        tree.bind("<<TreeviewSelect>>", suggest_name)
        load_seasons_btn.configure(command=load_seasons)
        suggest_name()

    def _pick_tmdb_result(self, candidates: list) -> dict | None:
        """
        Muestra un diálogo modal para que el usuario elija entre varios resultados de TMDb.
        Se llama desde el hilo worker (usa root.after + Event para sincronizar con Tkinter).
        Retorna el candidato elegido, o None si cancela.
        """
        import threading
        result_holder = [None]
        done_event = threading.Event()

        def show_dialog():
            win = tk.Toplevel(self.root)
            win.title("Seleccionar resultado")
            win.grab_set()
            win.resizable(False, False)

            # Centrar sobre la ventana principal
            self.root.update_idletasks()
            x = self.root.winfo_x() + self.root.winfo_width() // 2 - 280
            y = self.root.winfo_y() + self.root.winfo_height() // 2 - 200
            win.geometry(f"560x420+{x}+{y}")

            ttk.Label(
                win,
                text="Se encontraron varios resultados. Elegí el correcto:",
                wraplength=520
            ).pack(padx=12, pady=(12, 6), anchor=tk.W)
            if self._active_folder:
                ttk.Label(
                    win,
                    text=f"Carpeta: {os.path.basename(self._active_folder)}",
                    foreground="#45627f",
                    wraplength=520,
                ).pack(padx=12, pady=(0, 6), anchor=tk.W)

            frame = ttk.Frame(win)
            frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

            cols = ("name", "year", "id")
            tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse", height=10)
            tree.heading("name", text="Nombre")
            tree.heading("year", text="Año")
            tree.heading("id", text="ID")
            tree.column("name", width=350, stretch=True)
            tree.column("year", width=60, stretch=False)
            tree.column("id", width=90, stretch=False)

            sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            sb.pack(side=tk.RIGHT, fill=tk.Y)

            for i, c in enumerate(candidates):
                tree.insert("", tk.END, iid=str(i), values=(c['name'], c['year'], c['id']))

            # Seleccionar el primero por defecto
            if candidates:
                tree.selection_set("0")
                tree.focus("0")

            btn_frame = ttk.Frame(win)
            btn_frame.pack(fill=tk.X, padx=12, pady=(6, 12))

            def on_select():
                sel = tree.selection()
                if sel:
                    idx = int(sel[0])
                    result_holder[0] = candidates[idx]
                win.destroy()
                done_event.set()

            def on_cancel():
                result_holder[0] = None
                win.destroy()
                done_event.set()

            ttk.Button(btn_frame, text="Seleccionar", command=on_select).pack(side=tk.LEFT, padx=(0, 8))
            ttk.Button(btn_frame, text="Cancelar", command=on_cancel).pack(side=tk.LEFT)

            # Doble-clic también confirma
            tree.bind("<Double-1>", lambda _: on_select())
            win.protocol("WM_DELETE_WINDOW", on_cancel)

        self.root.after(0, show_dialog)
        done_event.wait()  # Bloquea el hilo worker hasta que el usuario decida
        return result_holder[0]

    def _on_plan(self):
        if self._busy:
            return
        path = self.path_var.get().strip(' "\'')
        if not path or not os.path.exists(path.rstrip("*?")):
            self.status_var.set("La ruta no existe.")
            self._append_log("[!] La ruta no existe.")
            return

        self._set_busy(True)
        self.status_var.set("Preparando limpieza local…" if self.tv_provider_var.get() == "Sin proveedor (limpiar)"
                            else "Consultando metadatos…")
        query = self.query_var.get().strip() or None
        lang = self.lang_var.get().strip() or DEFAULT_LANGUAGE
        tv_provider = self.tv_provider_var.get()
        recursive = self.scan_mode_var.get() == "Incluir subcarpetas (lote)"
        media_type = self.type_var.get()

        def work():
            unrecognized = []
            try:
                organizer = self.organizer if tv_provider == "Sin proveedor (limpiar)" else MediaOrganizer(
                    *create_metadata_clients(tv_provider)
                )
                self.organizer = organizer

                # Bulk movie mode: process all video files directly, not per-folder
                if media_type == "book":
                    targets = organizer.find_book_folders(path) if recursive and os.path.isdir(path) else [path]
                    if not targets:
                        raise ValueError("No se encontraron carpetas con libros.")
                    if recursive and len(targets) > 1 and query:
                        self._append_log("[i] La búsqueda escrita se omitió en el lote para no asignar el mismo libro a todos los archivos.")
                    plans = []
                    for target in targets:
                        local_query = query if len(targets) == 1 else None
                        self._active_folder = target
                        item_plan = organizer.plan_books(
                            target, local_query, lang=lang,
                            target_base_dir=path if os.path.isdir(path) else None,
                            log_fn=self._append_log,
                            pick_result_fn=self._pick_tmdb_result if len(targets) == 1 else None,
                            use_metadata=(tv_provider != "Sin proveedor (limpiar)"),
                        )
                        if item_plan:
                            plans.append(item_plan)
                    plan = self._combine_plans(plans, path)
                elif media_type == "bulk-movie":
                    if tv_provider == "Sin proveedor (limpiar)":
                        # For bulk movie, clean mode doesn't make much sense, but allow it
                        item_plan = organizer.plan_clean_filenames(path, show_title=query, log_fn=self._append_log)
                    else:
                        item_plan = organizer.plan_bulk_movies(
                            path, lang=lang,
                            log_fn=self._append_log,
                            pick_result_fn=self._pick_tmdb_result
                        )
                    plan = item_plan
                else:
                    if recursive and os.path.isdir(path):
                        targets = organizer.find_media_folders(path)
                        if not targets:
                            targets = organizer.find_book_folders(path)
                    else:
                        targets = [path]
                    if not targets:
                        raise ValueError("No se encontraron carpetas con videos ni libros.")
                    if recursive and len(targets) > 1 and query:
                        self._append_log("[i] La búsqueda escrita se omitió en el lote para no asignar la misma serie a todas las carpetas.")

                    plans = []
                    for target in targets:
                        local_query = query if len(targets) == 1 else None
                        self._active_folder = target
                        resolved_type = self._resolve_media_type(target)
                        if resolved_type == "book":
                            item_plan = organizer.plan_books(
                                target, local_query, lang=lang,
                                target_base_dir=path if recursive and os.path.isdir(path) else None,
                                log_fn=self._append_log,
                                pick_result_fn=self._pick_tmdb_result if len(targets) == 1 else None,
                                use_metadata=(tv_provider != "Sin proveedor (limpiar)"),
                            )
                        elif tv_provider == "Sin proveedor (limpiar)":
                            item_plan = organizer.plan_clean_filenames(target, show_title=local_query, log_fn=self._append_log)
                        elif resolved_type == "tv":
                            item_plan = organizer.plan_tv_show(
                                target, local_query, lang=lang,
                                log_fn=self._append_log,
                                pick_result_fn=self._pick_tmdb_result
                            )
                        else:
                            default_name = os.path.basename(os.path.abspath(target.rstrip("*?")))
                            movie_query = local_query or default_name
                            item_plan = organizer.plan_movie(
                                target, movie_query, lang=lang,
                                log_fn=self._append_log,
                                pick_result_fn=self._pick_tmdb_result
                            )
                        if item_plan:
                            plans.append(item_plan)
                        elif tv_provider != "Sin proveedor (limpiar)" and resolved_type != "book":
                            unrecognized.append(target)
                    plan = self._combine_plans(plans, path)
            except Exception as exc:
                plan = None
                self._append_log(f"[!] Error al planificar: {exc}")
            finally:
                self._active_folder = None

            def done():
                self._show_plan(plan)
                self._set_busy(False)
                if unrecognized:
                    self._show_review_queue(unrecognized, tv_provider, lang, path)

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _on_apply(self):
        if self._busy or not self.plan:
            return
        if not self.selected_moves:
            self.status_var.set("Seleccioná al menos un archivo para aplicar el plan.")
            return
        self._set_busy(True)
        dry_run = self.dry_run_var.get()
        self.status_var.set("Simulando…" if dry_run else "Aplicando cambios…")
        plan = {**self.plan, "moves": [
            move for index, move in enumerate(self.plan["moves"]) if index in self.selected_moves
        ]}

        def work():
            ok = False
            try:
                ok = self.organizer.execute_plan(
                    plan, dry_run=dry_run, auto_yes=True, log_fn=self._append_log
                )
            except Exception as exc:
                self._append_log(f"[!] Error al aplicar: {exc}")

            def done():
                if ok and dry_run:
                    self.status_var.set("Simulación lista. Ningún archivo se movió.")
                elif ok:
                    self.status_var.set("Organización completada.")
                    self._clear_plan_view()
                else:
                    self.status_var.set("No se aplicaron los cambios.")
                self._set_busy(False)

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _on_undo(self):
        if self._busy:
            return
        self._set_busy(True)
        self.status_var.set("Deshaciendo última operación…")

        def work():
            ok = False
            try:
                ok = self.organizer.undo_last_operation(log_fn=self._append_log)
            except Exception as exc:
                self._append_log(f"[!] Error al deshacer: {exc}")

            def done():
                self.status_var.set("Última operación revertida." if ok else "No hay nada para deshacer.")
                self._set_busy(False)

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()


def run_gui():
    root = tk.Tk()
    MediaRenamerApp(root)
    root.mainloop()
