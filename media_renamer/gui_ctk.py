# -*- coding: utf-8 -*-
"""
CustomTkinter version of Media Renamer GUI.
Modern UI with dark/light mode support, rounded corners, and better styling.
"""
import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk
import customtkinter as ctk

from .config import (
    DEFAULT_LANGUAGE,
    DEFAULT_STRM_TEMPLATE,
    GOOGLE_DRIVE_API_KEY,
    find_yt_dlp,
)
from .organizer import MediaOrganizer, _calculate_confidence
from .metadata import available_tv_providers, create_metadata_clients
from .utils import clean_query_string, clean_book_query, sanitize_filename
from .gdrive import GoogleDriveClient, GoogleDriveError, parse_drive_target, write_strm_files
from .youtube_dl import (
    DEST_JELLYFIN,
    DEST_JELLYFIN_DIRECT,
    DEST_VLC,
    YTDLPExtractor,
    detect_youtube_url_type,
    is_youtube_url,
)


# Configure CustomTkinter appearance
ctk.set_appearance_mode("system")  # "system", "dark", "light"
ctk.set_default_color_theme("blue")  # "blue", "green", "dark-blue"


# Etiqueta visible -> constante de destino en youtube_dl
YT_DESTINATIONS = {
    "VLC / Kodi (URL de YouTube)": DEST_VLC,
    "Jellyfin (marcador · el plugin resuelve)": DEST_JELLYFIN,
    "Jellyfin (resolver URL directa ahora)": DEST_JELLYFIN_DIRECT,
}

# Presets de plantilla de nombre; None = no tocar el campo (edición manual)
YT_TEMPLATE_PRESETS = {
    "Título (id)": "{title} ({id}).strm",
    "Serie · S01E01 - Título": "S{season:02d}E{ep:02d} - {title}.strm",
    "Solo título": "{title}.strm",
    "Personalizada": None,
}

# Etiqueta visible -> modo de identificación para los .strm de Google Drive.
GD_MODES = {
    "Automático (detectar)": "auto",
    "Serie (TMDb / TheTVDB)": "tv",
    "Películas (TMDb)": "movie",
    "Solo .strm (sin metadatos)": "none",
}


class MediaRenamerApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Media Renamer · Organizador multimedia")
        self.root.minsize(900, 620)
        self.root.geometry("1060x720")

        self.organizer = MediaOrganizer(*create_metadata_clients())
        self.plan = None
        self.selected_moves = set()
        self._active_folder = None
        self._busy = False
        self._reselect_dialog_open = False

        # YouTube downloader
        self.yt_extractor = YTDLPExtractor()

        # Variables
        self.path_var = ctk.StringVar()
        self.query_var = ctk.StringVar()
        self.lang_var = ctk.StringVar(value=DEFAULT_LANGUAGE)
        self.type_var = ctk.StringVar(value="auto")
        self.tv_providers = [*available_tv_providers(), "Sin proveedor (limpiar)"]
        self.tv_provider_var = ctk.StringVar(value=self.tv_providers[0])
        self.season_var = ctk.StringVar()
        self.scan_mode_var = ctk.StringVar(value="Solo esta carpeta")
        self.dry_run_var = ctk.BooleanVar(value=False)
        self.status_var = ctk.StringVar(value="Elegí una carpeta o archivo y pulsá Planificar.")
        self.total_var = ctk.StringVar(value="Seleccionados: 0 de 0 archivo(s)")

        # YouTube variables
        self.yt_url_var = ctk.StringVar()
        self.yt_output_var = ctk.StringVar()
        self.yt_naming_var = ctk.StringVar(value=DEFAULT_STRM_TEMPLATE)
        self.yt_status_var = ctk.StringVar(value="")
        self.yt_ytdlp_var = ctk.StringVar(value=find_yt_dlp())
        self.yt_dest_var = ctk.StringVar(value="VLC / Kodi (URL de YouTube)")
        self.yt_season_var = ctk.StringVar(value="1")
        self.yt_episode_var = ctk.StringVar(value="1")
        self.yt_overwrite_var = ctk.BooleanVar(value=True)
        self.yt_series_var = ctk.StringVar()
        self.yt_series_rename_var = ctk.BooleanVar(value=False)
        self.yt_force_season_var = ctk.BooleanVar(value=False)
        self.yt_playlist_title = ""
        self.yt_entries = []
        self.yt_selected = set()

        # Google Drive .strm
        self.gd_client = GoogleDriveClient(api_key=GOOGLE_DRIVE_API_KEY)
        self.gd_url_var = ctk.StringVar()
        self.gd_output_var = ctk.StringVar()
        self.gd_api_key_var = ctk.StringVar(value=GOOGLE_DRIVE_API_KEY)
        self.gd_mode_var = ctk.StringVar(value="Automático (detectar)")
        self.gd_query_var = ctk.StringVar()
        self.gd_recursive_var = ctk.BooleanVar(value=True)
        self.gd_overwrite_var = ctk.BooleanVar(value=True)
        self.gd_status_var = ctk.StringVar(value="")
        self.gd_root_name = ""
        self.gd_entries = []
        self.gd_selected = set()

        self._build()

    def _build(self):
        # Configure grid
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)

        # Header
        self._build_header()

        # Main content area (includes status bar)
        self._build_main_content()

        # Initial UI state
        self._on_type_change()

    def _build_header(self):
        header = ctk.CTkFrame(self.root, corner_radius=0, fg_color=("gray90", "gray13"))
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_columnconfigure(0, weight=1)

        title_label = ctk.CTkLabel(
            header,
            text="Media Renamer",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=("gray10", "gray90")
        )
        title_label.grid(row=0, column=0, padx=20, pady=(15, 0), sticky="w")

        subtitle_label = ctk.CTkLabel(
            header,
            text="Ordená, limpiá y revisá tus videos antes de modificar archivos.",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=("gray40", "gray60")
        )
        subtitle_label.grid(row=1, column=0, padx=20, pady=(0, 15), sticky="w")

        # Theme toggle
        self.theme_switch = ctk.CTkSwitch(
            header,
            text="Modo oscuro",
            command=self._toggle_theme,
            font=ctk.CTkFont(size=11),
            progress_color="#3B8ED0"
        )
        self.theme_switch.grid(row=0, column=1, rowspan=2, padx=20, pady=10, sticky="e")
        header.grid_columnconfigure(1, weight=0)

        # Set initial switch state based on current appearance mode
        current_mode = ctk.get_appearance_mode()
        self.theme_switch.select() if current_mode == "Dark" else self.theme_switch.deselect()

    def _toggle_theme(self):
        """Toggle between dark and light mode."""
        current = ctk.get_appearance_mode()
        new_mode = "Light" if current == "Dark" else "Dark"
        ctk.set_appearance_mode(new_mode)
        self.theme_switch.configure(text="Modo claro" if new_mode == "Light" else "Modo oscuro")

    def _build_main_content(self):
        # Main container - Tab View
        self.tab_view = ctk.CTkTabview(self.root, corner_radius=12)
        self.tab_view.grid(row=1, column=0, sticky="nsew", padx=16, pady=16)
        self.tab_view.grid_columnconfigure(0, weight=1)

        # Add tabs
        self.tab_organizer = self.tab_view.add("📁 Organizador")
        self.tab_youtube = self.tab_view.add("📺 YouTube .strm")
        self.tab_gdrive = self.tab_view.add("☁️ Google Drive .strm")

        # Configure tab grids
        self.tab_organizer.grid_columnconfigure(0, weight=1)
        self.tab_organizer.grid_rowconfigure(0, weight=1)
        self.tab_youtube.grid_columnconfigure(0, weight=1)
        self.tab_youtube.grid_rowconfigure(0, weight=1)
        self.tab_gdrive.grid_columnconfigure(0, weight=1)
        self.tab_gdrive.grid_rowconfigure(0, weight=1)

        # Build Organizer tab content
        self._build_organizer_tab()

        # Build YouTube tab
        self._build_youtube_tab()

        # Build Google Drive tab
        self._build_gdrive_tab()

    def _build_organizer_tab(self):
        """Build the original organizer tab content."""
        # Use a scrollable frame for the organizer content
        self.main_frame = ctk.CTkScrollableFrame(self.tab_organizer, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.main_frame.grid_columnconfigure(0, weight=1)

        # Source & Metadata Card
        self._build_source_card()

        # Preview Card
        self._build_preview_card()

        # Action Buttons
        self._build_action_buttons()

    def _build_source_card(self):
        card = ctk.CTkFrame(self.main_frame, corner_radius=12)
        card.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        card.grid_columnconfigure(1, weight=1)
        card.grid_columnconfigure(3, weight=1)

        # Card title
        ctk.CTkLabel(
            card,
            text="Origen y metadatos",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, columnspan=6, padx=16, pady=(16, 8), sticky="w")

        # Row 0: Path
        ctk.CTkLabel(card, text="Ruta:", font=ctk.CTkFont(size=12)).grid(row=1, column=0, padx=(16, 8), pady=4, sticky="w")
        
        self.path_entry = ctk.CTkEntry(card, textvariable=self.path_var, placeholder_text="Seleccioná una carpeta o archivo...")
        self.path_entry.grid(row=1, column=1, columnspan=3, padx=8, pady=4, sticky="ew")

        ctk.CTkButton(card, text="Carpeta…", command=self._browse_folder, width=100).grid(row=1, column=4, padx=8, pady=4)
        ctk.CTkButton(card, text="Archivo…", command=self._browse_file, width=100, fg_color="transparent", border_width=1).grid(row=1, column=5, padx=(8, 16), pady=4)

        # Row 2: Type (full width)
        ctk.CTkLabel(card, text="Tipo:", font=ctk.CTkFont(size=12)).grid(row=2, column=0, padx=(16, 8), pady=4, sticky="w")

        type_frame = ctk.CTkFrame(card, fg_color="transparent")
        type_frame.grid(row=2, column=1, columnspan=5, padx=8, pady=4, sticky="w")

        type_options = [
            ("auto", "Auto"),
            ("tv", "Serie"),
            ("movie", "Película"),
            ("bulk-movie", "Múltiples Películas (Bulk)"),
            ("book", "Libros / Cómics"),
        ]
        self.type_radios = []
        for value, label in type_options:
            rb = ctk.CTkRadioButton(
                type_frame,
                text=label,
                value=value,
                variable=self.type_var,
                command=self._on_type_change,
                font=ctk.CTkFont(size=11)
            )
            rb.pack(side="left", padx=(0, 12))
            self.type_radios.append(rb)

        # Row 3: Language + TV provider (separate columns, no overlap)
        ctk.CTkLabel(card, text="Idioma:", font=ctk.CTkFont(size=12)).grid(row=3, column=0, padx=(16, 8), pady=4, sticky="w")
        self.lang_entry = ctk.CTkEntry(card, textvariable=self.lang_var, width=80)
        self.lang_entry.grid(row=3, column=1, padx=8, pady=4, sticky="w")

        ctk.CTkLabel(card, text="Proveedor series:", font=ctk.CTkFont(size=12)).grid(row=3, column=2, padx=(20, 8), pady=4, sticky="e")

        self.provider_combo = ctk.CTkComboBox(
            card,
            values=self.tv_providers,
            variable=self.tv_provider_var,
            state="readonly",
            width=200,
            font=ctk.CTkFont(size=11),
            dropdown_font=ctk.CTkFont(size=11)
        )
        self.provider_combo.grid(row=3, column=3, columnspan=3, padx=8, pady=4, sticky="w")

        # Row 4: Season (full width)
        ctk.CTkLabel(card, text="Temporada:", font=ctk.CTkFont(size=12)).grid(row=4, column=0, padx=(16, 8), pady=4, sticky="w")

        season_frame = ctk.CTkFrame(card, fg_color="transparent")
        season_frame.grid(row=4, column=1, columnspan=5, padx=8, pady=4, sticky="w")
        
        self.season_entry = ctk.CTkEntry(season_frame, textvariable=self.season_var, width=60, placeholder_text="Opcional")
        self.season_entry.pack(side="left", padx=(0, 8))
        
        ctk.CTkLabel(
            season_frame,
            text="(número, opcional — fuerza la temporada para series sin acrónimo conocido)",
            font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60")
        ).pack(side="left")

        # Row 5: Scan mode
        ctk.CTkLabel(card, text="Exploración:", font=ctk.CTkFont(size=12)).grid(row=5, column=0, padx=(16, 8), pady=4, sticky="w")

        self.scan_mode_combo = ctk.CTkComboBox(
            card,
            values=("Solo esta carpeta", "Incluir subcarpetas (lote)"),
            variable=self.scan_mode_var,
            state="readonly",
            width=220,
            font=ctk.CTkFont(size=11),
            dropdown_font=ctk.CTkFont(size=11)
        )
        self.scan_mode_combo.grid(row=5, column=1, columnspan=2, padx=8, pady=4, sticky="w")

        # Row 6: Query
        self.query_label = ctk.CTkLabel(card, text="Serie/película:", font=ctk.CTkFont(size=12))
        self.query_label.grid(row=6, column=0, padx=(16, 8), pady=4, sticky="w")

        self.query_entry = ctk.CTkEntry(card, textvariable=self.query_var, placeholder_text="(vacío = autodetección)")
        self.query_entry.grid(row=6, column=1, columnspan=3, padx=8, pady=4, sticky="ew")

        self.query_hint = ctk.CTkLabel(
            card,
            text="(vacío = autodetección)",
            font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60")
        )
        self.query_hint.grid(row=6, column=4, columnspan=2, padx=8, pady=4, sticky="w")

        # Row 7: Buttons
        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.grid(row=7, column=0, columnspan=6, padx=16, pady=(8, 16), sticky="w")
        
        self.plan_btn = ctk.CTkButton(
            btn_frame,
            text="Planificar",
            command=self._on_plan,
            font=ctk.CTkFont(size=12, weight="bold"),
            height=36,
            corner_radius=8
        )
        self.plan_btn.pack(side="left", padx=(0, 8))
        
        self.apply_btn = ctk.CTkButton(
            btn_frame,
            text="Aplicar",
            command=self._on_apply,
            state="disabled",
            font=ctk.CTkFont(size=12),
            height=36,
            corner_radius=8,
            fg_color=("gray70", "gray30"),
            hover_color=("gray60", "gray40")
        )
        self.apply_btn.pack(side="left", padx=8)
        
        self.undo_btn = ctk.CTkButton(
            btn_frame,
            text="Deshacer última",
            command=self._on_undo,
            font=ctk.CTkFont(size=12),
            height=36,
            corner_radius=8,
            fg_color=("gray70", "gray30"),
            hover_color=("gray60", "gray40")
        )
        self.undo_btn.pack(side="left", padx=8)
        
        self.dry_run_check = ctk.CTkCheckBox(
            btn_frame,
            text="Dry-run (simular)",
            variable=self.dry_run_var,
            font=ctk.CTkFont(size=11)
        )
        self.dry_run_check.pack(side="left", padx=(16, 0))

    def _build_preview_card(self):
        card = ctk.CTkFrame(self.main_frame, corner_radius=12)
        card.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        self.main_frame.grid_rowconfigure(1, weight=1)

        # Card header
        header_frame = ctk.CTkFrame(card, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header_frame,
            text="Vista previa · clic en ✓ para incluir/excluir · doble clic en amarillo/rojo para cambiar coincidencia",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, sticky="w")

        # Confidence legend
        legend_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        legend_frame.grid(row=0, column=1, sticky="e")

        ctk.CTkLabel(legend_frame, text="Confianza:", font=ctk.CTkFont(size=11), text_color=("gray40", "gray60")).pack(side="left", padx=(0, 8))
        
        self._make_legend_item(legend_frame, "Alta", "#2e7d32")
        self._make_legend_item(legend_frame, "Media", "#f9a825")
        self._make_legend_item(legend_frame, "Baja", "#c62828")

        # Preview table (using CTkScrollableFrame with custom rows)
        self.preview_frame = ctk.CTkScrollableFrame(card, corner_radius=8, fg_color=("gray92", "gray17"))
        self.preview_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.preview_frame.grid_columnconfigure(0, weight=1)

        # Column headers
        header_row = ctk.CTkFrame(self.preview_frame, fg_color=("gray80", "gray20"), corner_radius=6)
        header_row.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        header_row.grid_columnconfigure(1, weight=2)
        header_row.grid_columnconfigure(2, weight=2)

        ctk.CTkLabel(header_row, text="✓", font=ctk.CTkFont(weight="bold"), width=40, anchor="center").grid(row=0, column=0, padx=8, pady=8)
        ctk.CTkLabel(header_row, text="Archivo actual", font=ctk.CTkFont(weight="bold"), anchor="w").grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkLabel(header_row, text="Destino relativo", font=ctk.CTkFont(weight="bold"), anchor="w").grid(row=0, column=2, padx=8, pady=8, sticky="ew")

        # Content rows container
        self.preview_rows_frame = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        self.preview_rows_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.preview_rows_frame.grid_columnconfigure(0, weight=1)

        # Select all / none buttons
        select_btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        select_btn_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        
        ctk.CTkButton(
            select_btn_frame,
            text="Seleccionar todos",
            command=lambda: self._set_all_moves(True),
            width=140,
            height=32,
            font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(16, 8))
        
        ctk.CTkButton(
            select_btn_frame,
            text="Quitar todos",
            command=lambda: self._set_all_moves(False),
            width=140,
            height=32,
            font=ctk.CTkFont(size=11),
            fg_color=("gray70", "gray30"),
            hover_color=("gray60", "gray40")
        ).pack(side="left", padx=8)

    def _make_legend_item(self, parent, text, color):
        item = ctk.CTkFrame(parent, fg_color="transparent")
        item.pack(side="left", padx=(8, 2))
        
        canvas = ctk.CTkCanvas(item, width=12, height=12, highlightthickness=0, bg="#ffffff" if ctk.get_appearance_mode() == "Light" else "#1a1a1a")
        canvas.create_oval(2, 2, 10, 10, fill=color, outline=color)
        canvas.pack(side="left", padx=(0, 4))
        
        ctk.CTkLabel(item, text=text, font=ctk.CTkFont(size=10), text_color=("gray40", "gray60")).pack(side="left", padx=(0, 10))

    def _build_action_buttons(self):
        # Progress and hint
        action_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        action_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        action_frame.grid_columnconfigure(1, weight=1)

        self.progress = ctk.CTkProgressBar(action_frame, mode="indeterminate", width=130)
        self.progress.grid(row=0, column=0, padx=(0, 12))
        self.progress.grid_remove()  # Hidden initially

        self.hint_var = ctk.StringVar(value="💡 Doble clic en filas amarillas/rojas para cambiar coincidencia")
        ctk.CTkLabel(
            action_frame,
            textvariable=self.hint_var,
            font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60")
        ).grid(row=0, column=2, padx=(0, 12), sticky="e")

        # Log area
        log_frame = ctk.CTkFrame(self.main_frame, corner_radius=8)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 16))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)
        self.main_frame.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            log_frame,
            text="Log:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, padx=16, pady=(12, 4), sticky="w")

        self.log = ctk.CTkTextbox(log_frame, height=120, wrap="word", font=ctk.CTkFont(family="Consolas", size=10))
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log.configure(state="disabled")

        # Status bar at bottom of main content
        status_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        status_frame.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 16))
        status_frame.grid_columnconfigure(1, weight=1)

        self.total_label = ctk.CTkLabel(
            status_frame,
            textvariable=self.total_var,
            font=ctk.CTkFont(size=11)
        )
        self.total_label.grid(row=0, column=0, padx=(0, 12), sticky="w")

        self.status_label = ctk.CTkLabel(
            status_frame,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            anchor="e"
        )
        self.status_label.grid(row=0, column=1, sticky="e")

    # ==================== YouTube Tab ====================

    def _build_youtube_tab(self):
        """Build the YouTube .strm generator tab."""
        # Use a scrollable frame for the YouTube content
        self.yt_frame = ctk.CTkScrollableFrame(self.tab_youtube, corner_radius=0, fg_color="transparent")
        self.yt_frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.yt_frame.grid_columnconfigure(0, weight=1)

        # URL Input Card
        url_card = ctk.CTkFrame(self.yt_frame, corner_radius=12)
        url_card.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        url_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            url_card,
            text="🔗 URL de YouTube",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")

        ctk.CTkLabel(
            url_card,
            text="Soporta: Canales, Playlists, Videos individuales",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60")
        ).grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 8), sticky="w")

        ctk.CTkLabel(url_card, text="URL:", font=ctk.CTkFont(size=12)).grid(row=2, column=0, padx=(16, 8), pady=8, sticky="w")
        
        self.yt_url_entry = ctk.CTkEntry(url_card, textvariable=self.yt_url_var, placeholder_text="https://www.youtube.com/playlist?list=... o https://www.youtube.com/@canal")
        self.yt_url_entry.grid(row=2, column=1, padx=8, pady=8, sticky="ew")

        self.yt_fetch_btn = ctk.CTkButton(
            url_card,
            text="Obtener lista",
            command=self._yt_fetch_playlist,
            width=120,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.yt_fetch_btn.grid(row=2, column=2, padx=(8, 16), pady=8)

        # Output Card
        output_card = ctk.CTkFrame(self.yt_frame, corner_radius=12)
        output_card.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        output_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            output_card,
            text="📁 Carpeta de salida",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")

        ctk.CTkLabel(output_card, text="Carpeta:", font=ctk.CTkFont(size=12)).grid(row=1, column=0, padx=(16, 8), pady=8, sticky="w")
        
        self.yt_output_entry = ctk.CTkEntry(output_card, textvariable=self.yt_output_var, placeholder_text="Seleccioná carpeta destino...")
        self.yt_output_entry.grid(row=1, column=1, padx=8, pady=8, sticky="ew")

        ctk.CTkButton(
            output_card,
            text="Examinar…",
            command=self._yt_browse_output,
            width=100,
            fg_color="transparent",
            border_width=1
        ).grid(row=1, column=2, padx=(8, 16), pady=8)

        # Naming Template Card
        naming_card = ctk.CTkFrame(self.yt_frame, corner_radius=12)
        naming_card.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
        naming_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            naming_card,
            text="📝 Plantilla de nombres",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")

        ctk.CTkLabel(naming_card, text="Plantilla:", font=ctk.CTkFont(size=12)).grid(row=1, column=0, padx=(16, 8), pady=8, sticky="w")
        
        self.yt_naming_entry = ctk.CTkEntry(naming_card, textvariable=self.yt_naming_var, placeholder_text="{title} ({id}).strm")
        self.yt_naming_entry.grid(row=1, column=1, padx=8, pady=8, sticky="ew")

        self.yt_preset_combo = ctk.CTkOptionMenu(
            naming_card,
            values=list(YT_TEMPLATE_PRESETS.keys()),
            width=200,
            command=self._yt_apply_template_preset
        )
        self.yt_preset_combo.grid(row=1, column=2, padx=(8, 16), pady=8)
        self.yt_preset_combo.set("Título (id)")

        # Numeración de episodios (para que Jellyfin empareje la serie)
        num_frame = ctk.CTkFrame(naming_card, fg_color="transparent")
        num_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 8))

        ctk.CTkLabel(num_frame, text="Temporada:", font=ctk.CTkFont(size=12)).grid(row=0, column=0, padx=(0, 8), pady=4, sticky="w")
        ctk.CTkEntry(num_frame, textvariable=self.yt_season_var, width=60).grid(row=0, column=1, padx=(0, 24), pady=4, sticky="w")

        ctk.CTkLabel(num_frame, text="Episodio inicial:", font=ctk.CTkFont(size=12)).grid(row=0, column=2, padx=(0, 8), pady=4, sticky="w")
        ctk.CTkEntry(num_frame, textvariable=self.yt_episode_var, width=60).grid(row=0, column=3, padx=(0, 12), pady=4, sticky="w")

        ctk.CTkLabel(
            num_frame,
            text="(se usan con {season} y {ep} en la plantilla)",
            font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60")
        ).grid(row=0, column=4, padx=(0, 8), pady=4, sticky="w")

        ctk.CTkLabel(
            naming_card,
            text="Variables: {title}, {id}, {url}, {season}, {ep}, {index}",
            font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60")
        ).grid(row=3, column=0, columnspan=3, padx=16, pady=(0, 16), sticky="w")

        # Series Renaming Card (opcional)
        series_card = ctk.CTkFrame(self.yt_frame, corner_radius=12)
        series_card.grid(row=3, column=0, sticky="ew", padx=16, pady=8)
        series_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            series_card,
            text="🎬 Renombrado de series (opcional)",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")

        ctk.CTkCheckBox(
            series_card,
            text="Al terminar, armar el plan de series con metadatos (TMDb / TheTVDB)",
            variable=self.yt_series_rename_var,
            font=ctk.CTkFont(size=12)
        ).grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 10), sticky="w")

        ctk.CTkLabel(series_card, text="Serie:", font=ctk.CTkFont(size=12)).grid(row=2, column=0, padx=(16, 8), pady=8, sticky="w")

        ctk.CTkEntry(
            series_card,
            textvariable=self.yt_series_var,
            placeholder_text="vacío = se usa el nombre de la carpeta de salida"
        ).grid(row=2, column=1, padx=8, pady=8, sticky="ew")

        ctk.CTkCheckBox(
            series_card,
            text="Forzar temporada",
            variable=self.yt_force_season_var,
            font=ctk.CTkFont(size=12)
        ).grid(row=2, column=2, padx=(8, 16), pady=8, sticky="w")

        ctk.CTkLabel(
            series_card,
            text=("El plan aparece en la pestaña Organizador para que lo confirmes con «Aplicar», y usa el "
                  "proveedor e idioma elegidos ahí. La carpeta de salida se toma como raíz de la biblioteca: "
                  "los .strm quedan en «Serie (Año)/Season NN/». Para que se lea la temporada y el episodio "
                  "del título original, conviene dejar la plantilla en «Título (id)»."),
            font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60"),
            justify="left",
            wraplength=780
        ).grid(row=3, column=0, columnspan=3, padx=16, pady=(0, 16), sticky="w")

        # Output Format Card
        options_card = ctk.CTkFrame(self.yt_frame, corner_radius=12)
        options_card.grid(row=4, column=0, sticky="ew", padx=16, pady=8)
        options_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            options_card,
            text="⚙️ Formato de salida y yt-dlp",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")

        ctk.CTkLabel(options_card, text="Destino:", font=ctk.CTkFont(size=12)).grid(row=1, column=0, padx=(16, 8), pady=8, sticky="w")

        self.yt_dest_combo = ctk.CTkOptionMenu(
            options_card,
            values=list(YT_DESTINATIONS.keys()),
            variable=self.yt_dest_var
        )
        self.yt_dest_combo.grid(row=1, column=1, columnspan=2, padx=(8, 16), pady=8, sticky="ew")

        ctk.CTkLabel(
            options_card,
            text=("VLC/Kodi: la URL de YouTube. · Jellyfin (marcador): agrega #YtDlpStrm:source= "
                  "para que el plugin la resuelva. · Jellyfin (URL directa): la resuelve ahora con "
                  "yt-dlp (lento, ~3 s por video)."),
            font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60"),
            justify="left",
            wraplength=780
        ).grid(row=2, column=0, columnspan=3, padx=16, pady=(0, 8), sticky="w")

        ctk.CTkCheckBox(
            options_card,
            text="Sobrescribir archivos existentes (si no, se agregan sufijos «(1)»)",
            variable=self.yt_overwrite_var,
            font=ctk.CTkFont(size=12)
        ).grid(row=3, column=0, columnspan=3, padx=16, pady=(0, 12), sticky="w")

        ctk.CTkLabel(options_card, text="yt-dlp:", font=ctk.CTkFont(size=12)).grid(row=4, column=0, padx=(16, 8), pady=(0, 16), sticky="w")

        ctk.CTkEntry(options_card, textvariable=self.yt_ytdlp_var).grid(row=4, column=1, padx=8, pady=(0, 16), sticky="ew")

        ctk.CTkButton(
            options_card,
            text="Examinar…",
            command=self._yt_browse_ytdlp,
            width=100,
            fg_color="transparent",
            border_width=1
        ).grid(row=4, column=2, padx=(8, 16), pady=(0, 16))

        # Preview List Card
        preview_card = ctk.CTkFrame(self.yt_frame, corner_radius=12)
        preview_card.grid(row=5, column=0, sticky="nsew", padx=16, pady=8)
        preview_card.grid_columnconfigure(0, weight=1)
        preview_card.grid_rowconfigure(1, weight=1)
        self.yt_frame.grid_rowconfigure(5, weight=1)

        preview_header = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        preview_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            preview_header,
            text="📋 Videos a procesar",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w"
        ).grid(row=0, column=0, sticky="w")

        self.yt_count_label = ctk.CTkLabel(
            preview_header,
            text="0 videos",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60")
        )
        self.yt_count_label.grid(row=0, column=1, sticky="e", padx=16)

        # Preview list
        self.yt_list_frame = ctk.CTkScrollableFrame(preview_card, corner_radius=8, fg_color=("gray92", "gray17"))
        self.yt_list_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.yt_list_frame.grid_columnconfigure(0, weight=1)

        # Header row
        yt_header = ctk.CTkFrame(self.yt_list_frame, fg_color=("gray80", "gray20"), corner_radius=6)
        yt_header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        yt_header.grid_columnconfigure(1, weight=2)
        yt_header.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(yt_header, text="✓", font=ctk.CTkFont(weight="bold"), width=40, anchor="center").grid(row=0, column=0, padx=8, pady=8)
        ctk.CTkLabel(yt_header, text="Título", font=ctk.CTkFont(weight="bold"), anchor="w").grid(row=0, column=1, padx=8, pady=8, sticky="ew")
        ctk.CTkLabel(yt_header, text="Duración", font=ctk.CTkFont(weight="bold"), anchor="w").grid(row=0, column=2, padx=8, pady=8, sticky="ew")

        # Content rows container
        self.yt_rows_frame = ctk.CTkFrame(self.yt_list_frame, fg_color="transparent")
        self.yt_rows_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.yt_list_frame.grid_rowconfigure(1, weight=1)
        self.yt_rows_frame.grid_columnconfigure(0, weight=1)

        # Select all / none buttons
        yt_select_frame = ctk.CTkFrame(preview_card, fg_color="transparent")
        yt_select_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        
        ctk.CTkButton(
            yt_select_frame,
            text="Seleccionar todos",
            command=lambda: self._yt_set_all_selected(True),
            width=140,
            height=32,
            font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(16, 8))
        
        ctk.CTkButton(
            yt_select_frame,
            text="Quitar todos",
            command=lambda: self._yt_set_all_selected(False),
            width=140,
            height=32,
            font=ctk.CTkFont(size=11),
            fg_color=("gray70", "gray30"),
            hover_color=("gray60", "gray40")
        ).pack(side="left", padx=8)

        # Generate Button Card
        gen_card = ctk.CTkFrame(self.yt_frame, corner_radius=12)
        gen_card.grid(row=6, column=0, sticky="ew", padx=16, pady=(8, 16))
        gen_card.grid_columnconfigure(1, weight=1)

        gen_info = ctk.CTkFrame(gen_card, fg_color="transparent")
        gen_info.grid(row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(16, 8))

        self.yt_status_label = ctk.CTkLabel(
            gen_info,
            textvariable=self.yt_status_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#3B8ED0", "#64B5F6"),
            anchor="w"
        )
        self.yt_status_label.pack(side="left", padx=16, pady=8)

        self.yt_generate_btn = ctk.CTkButton(
            gen_card,
            text="🎬 Generar archivos .strm",
            command=self._yt_generate_strm,
            font=ctk.CTkFont(size=14, weight="bold"),
            height=48,
            corner_radius=10,
            fg_color=("#2E7D32", "#1B5E20"),
            hover_color=("#1B5E20", "#1B5E20")
        )
        self.yt_generate_btn.grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 16))
        self.yt_generate_btn.configure(state="disabled")

    # ==================== Google Drive Tab ====================

    def _build_gdrive_tab(self):
        """Generador de .strm para carpetas públicas de Google Drive."""
        frame = ctk.CTkScrollableFrame(self.tab_gdrive, corner_radius=0, fg_color="transparent")
        frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        frame.grid_columnconfigure(0, weight=1)
        self.gd_frame = frame

        # URL card
        url_card = ctk.CTkFrame(frame, corner_radius=12)
        url_card.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        url_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            url_card,
            text="☁️ Carpeta de Google Drive",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 4), sticky="w")

        ctk.CTkLabel(
            url_card,
            text=("Compartí la carpeta como «Cualquier persona con el enlace». "
                  "Acepta carpetas (/folders/…) y archivos (/file/d/…)."),
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            anchor="w",
        ).grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 8), sticky="w")

        ctk.CTkLabel(url_card, text="URL:", font=ctk.CTkFont(size=12)).grid(
            row=2, column=0, padx=(16, 8), pady=8, sticky="w"
        )
        self.gd_url_entry = ctk.CTkEntry(
            url_card,
            textvariable=self.gd_url_var,
            placeholder_text="https://drive.google.com/drive/folders/…",
        )
        self.gd_url_entry.grid(row=2, column=1, padx=8, pady=8, sticky="ew")

        self.gd_fetch_btn = ctk.CTkButton(
            url_card,
            text="Obtener lista",
            command=self._gd_fetch,
            width=120,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.gd_fetch_btn.grid(row=2, column=2, padx=(8, 16), pady=8)

        # Output card
        output_card = ctk.CTkFrame(frame, corner_radius=12)
        output_card.grid(row=1, column=0, sticky="ew", padx=16, pady=8)
        output_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            output_card,
            text="📁 Carpeta de salida",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")

        ctk.CTkLabel(output_card, text="Carpeta:", font=ctk.CTkFont(size=12)).grid(
            row=1, column=0, padx=(16, 8), pady=8, sticky="w"
        )
        self.gd_output_entry = ctk.CTkEntry(
            output_card,
            textvariable=self.gd_output_var,
            placeholder_text="Carpeta raíz de la biblioteca (ej. D:\\Jellyfin\\Series)",
        )
        self.gd_output_entry.grid(row=1, column=1, padx=8, pady=8, sticky="ew")

        ctk.CTkButton(
            output_card,
            text="Examinar…",
            command=self._gd_browse_output,
            width=100,
            fg_color="transparent",
            border_width=1,
        ).grid(row=1, column=2, padx=(8, 16), pady=8)

        # Options card
        options_card = ctk.CTkFrame(frame, corner_radius=12)
        options_card.grid(row=2, column=0, sticky="ew", padx=16, pady=8)
        options_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            options_card,
            text="⚙️ Identificación y opciones",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")

        ctk.CTkLabel(options_card, text="Tipo:", font=ctk.CTkFont(size=12)).grid(
            row=1, column=0, padx=(16, 8), pady=8, sticky="w"
        )
        self.gd_mode_combo = ctk.CTkOptionMenu(
            options_card,
            values=list(GD_MODES.keys()),
            variable=self.gd_mode_var,
        )
        self.gd_mode_combo.grid(row=1, column=1, columnspan=2, padx=(8, 16), pady=8, sticky="ew")

        ctk.CTkLabel(options_card, text="Buscar:", font=ctk.CTkFont(size=12)).grid(
            row=2, column=0, padx=(16, 8), pady=8, sticky="w"
        )
        self.gd_query_entry = ctk.CTkEntry(
            options_card,
            textvariable=self.gd_query_var,
            placeholder_text="Serie o película (vacío = nombre de la carpeta de Drive)",
        )
        self.gd_query_entry.grid(row=2, column=1, columnspan=2, padx=(8, 16), pady=8, sticky="ew")

        ctk.CTkCheckBox(
            options_card,
            text="Incluir subcarpetas (recomendado: temporadas)",
            variable=self.gd_recursive_var,
            font=ctk.CTkFont(size=12),
        ).grid(row=3, column=0, columnspan=2, padx=16, pady=(0, 8), sticky="w")

        ctk.CTkCheckBox(
            options_card,
            text="Sobrescribir .strm existentes",
            variable=self.gd_overwrite_var,
            font=ctk.CTkFont(size=12),
        ).grid(row=3, column=2, padx=(8, 16), pady=(0, 8), sticky="w")

        ctk.CTkLabel(options_card, text="API key:", font=ctk.CTkFont(size=12)).grid(
            row=4, column=0, padx=(16, 8), pady=8, sticky="w"
        )
        self.gd_api_key_entry = ctk.CTkEntry(
            options_card,
            textvariable=self.gd_api_key_var,
            placeholder_text="Opcional · sólo para carpetas muy grandes",
        )
        self.gd_api_key_entry.grid(row=4, column=1, columnspan=2, padx=(8, 16), pady=8, sticky="ew")

        ctk.CTkLabel(
            options_card,
            text=("Sin API key se lee el listado público de Drive. Con GOOGLE_DRIVE_API_KEY "
                  "se usa la API oficial (pagina carpetas grandes). Los .strm apuntan a la "
                  "descarga directa de Drive y requieren que la carpeta siga siendo pública."),
            font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60"),
            justify="left",
            wraplength=780,
        ).grid(row=5, column=0, columnspan=3, padx=16, pady=(0, 16), sticky="w")

        # Preview list
        preview_card = ctk.CTkFrame(frame, corner_radius=12)
        preview_card.grid(row=3, column=0, sticky="nsew", padx=16, pady=8)
        preview_card.grid_columnconfigure(0, weight=1)
        preview_card.grid_rowconfigure(1, weight=1)
        frame.grid_rowconfigure(3, weight=1)

        preview_header = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        preview_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            preview_header,
            text="📋 Videos encontrados",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        self.gd_count_label = ctk.CTkLabel(
            preview_header,
            text="0 videos",
            font=ctk.CTkFont(size=12),
            text_color=("gray40", "gray60"),
        )
        self.gd_count_label.grid(row=0, column=1, sticky="e", padx=16)

        self.gd_list_frame = ctk.CTkScrollableFrame(
            preview_card, corner_radius=8, fg_color=("gray92", "gray17")
        )
        self.gd_list_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.gd_list_frame.grid_columnconfigure(0, weight=1)

        gd_header = ctk.CTkFrame(self.gd_list_frame, fg_color=("gray80", "gray20"), corner_radius=6)
        gd_header.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        gd_header.grid_columnconfigure(1, weight=3)
        gd_header.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(gd_header, text="✓", font=ctk.CTkFont(weight="bold"), width=40, anchor="center").grid(
            row=0, column=0, padx=8, pady=8
        )
        ctk.CTkLabel(gd_header, text="Archivo", font=ctk.CTkFont(weight="bold"), anchor="w").grid(
            row=0, column=1, padx=8, pady=8, sticky="ew"
        )
        ctk.CTkLabel(gd_header, text="Temporada / Ep.", font=ctk.CTkFont(weight="bold"), anchor="w").grid(
            row=0, column=2, padx=8, pady=8, sticky="ew"
        )

        self.gd_rows_frame = ctk.CTkFrame(self.gd_list_frame, fg_color="transparent")
        self.gd_rows_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.gd_list_frame.grid_rowconfigure(1, weight=1)
        self.gd_rows_frame.grid_columnconfigure(0, weight=1)

        select_frame = ctk.CTkFrame(preview_card, fg_color="transparent")
        select_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        ctk.CTkButton(
            select_frame, text="Seleccionar todos", command=lambda: self._gd_set_all_selected(True),
            width=140, height=32, font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(16, 8))
        ctk.CTkButton(
            select_frame, text="Quitar todos", command=lambda: self._gd_set_all_selected(False),
            width=140, height=32, font=ctk.CTkFont(size=11),
            fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
        ).pack(side="left", padx=8)

        # Generate card
        gen_card = ctk.CTkFrame(frame, corner_radius=12)
        gen_card.grid(row=4, column=0, sticky="ew", padx=16, pady=(8, 16))
        gen_card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            gen_card,
            textvariable=self.gd_status_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#3B8ED0", "#64B5F6"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")

        self.gd_generate_btn = ctk.CTkButton(
            gen_card,
            text="🎬 Generar archivos .strm",
            command=self._gd_generate,
            font=ctk.CTkFont(size=14, weight="bold"),
            height=48,
            corner_radius=10,
            fg_color=("#2E7D32", "#1B5E20"),
            hover_color=("#1B5E20", "#1B5E20"),
        )
        self.gd_generate_btn.grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 16))
        self.gd_generate_btn.configure(state="disabled")

    def _make_legend_item(self, parent, text, color):
        item = ctk.CTkFrame(parent, fg_color="transparent")
        item.pack(side="left", padx=(8, 2))
        
        canvas = ctk.CTkCanvas(item, width=12, height=12, highlightthickness=0, 
                               bg="#ffffff" if ctk.get_appearance_mode() == "Light" else "#1a1a1a")
        canvas.create_oval(2, 2, 10, 10, fill=color, outline=color)
        canvas.pack(side="left", padx=(0, 4))
        
        ctk.CTkLabel(item, text=text, font=ctk.CTkFont(size=10), 
                     text_color=("gray40", "gray60")).pack(side="left", padx=(0, 10))

    # ==================== Event Handlers ====================

    def _toggle_theme(self):
        """Toggle between dark and light mode."""
        current = ctk.get_appearance_mode()
        new_mode = "Light" if current == "Dark" else "Dark"
        ctk.set_appearance_mode(new_mode)
        self.theme_switch.configure(text="Modo claro" if new_mode == "Light" else "Modo oscuro")

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
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        self.root.after(0, write)

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.plan_btn.configure(state=state)
        self.undo_btn.configure(state=state)
        self.provider_combo.configure(state="disabled" if busy else "readonly")
        self.scan_mode_combo.configure(state="disabled" if busy else "readonly")
        if busy:
            self.progress.grid()
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.grid_remove()
        apply_state = "disabled" if busy or not self.plan else "normal"
        self.apply_btn.configure(state=apply_state)

    def _yt_browse_output(self):
        """Browse for output folder for .strm files."""
        path = filedialog.askdirectory(title="Elegí la carpeta para archivos .strm")
        if path:
            self.yt_output_var.set(path)

    def _yt_browse_ytdlp(self):
        """Browse for the yt-dlp executable."""
        path = filedialog.askopenfilename(
            title="Elegí el ejecutable de yt-dlp",
            filetypes=[("Ejecutable", "*.exe"), ("Todos", "*.*")],
        )
        if path:
            self.yt_ytdlp_var.set(path)

    def _yt_apply_template_preset(self, choice: str):
        """Aplica una plantilla predefinida al campo de plantilla."""
        template = YT_TEMPLATE_PRESETS.get(choice)
        if template:
            self.yt_naming_var.set(template)

    def _yt_sync_extractor(self):
        """Pasa a yt-dlp la ruta elegida en la GUI."""
        path = self.yt_ytdlp_var.get().strip()
        if path:
            self.yt_extractor.yt_dlp_path = path

    def _yt_forced_season(self):
        """Temporada a forzar en el plan de series, o None si no se pidió forzarla."""
        if not self.yt_force_season_var.get():
            return None
        try:
            return int(self.yt_season_var.get().strip())
        except ValueError:
            return None

    def _yt_plan_series_rename(self, output_dir: str, log_fn, query: str | None,
                               lang: str, provider: str, forced_season: int | None) -> dict | None:
        """Arma el plan de renombrado de series reusando el organizador.

        Reutiliza tal cual ``MediaOrganizer.plan_tv_show`` (el mismo que usa la pestaña
        Organizador): busca la serie en el proveedor de metadatos y arma los movimientos
        a ``Serie (Año)/Season NN/SxxEyy - Título oficial``.

        Se ejecuta en el hilo de trabajo, así que los valores de la interfaz llegan ya
        capturados (igual que en ``_on_plan``).
        """
        if provider == "Sin proveedor (limpiar)":
            log_fn("[!] El renombrado de series necesita un proveedor de metadatos (TMDb o TheTVDB).")
            return None

        organizer = MediaOrganizer(*create_metadata_clients(provider))
        self.organizer = organizer

        # _pick_tmdb_result usa esta carpeta para mostrar el contexto en el diálogo.
        self._active_folder = output_dir
        try:
            return organizer.plan_tv_show(
                output_dir,
                query,
                lang=lang,
                target_base_dir=output_dir,
                log_fn=log_fn,
                pick_result_fn=self._pick_tmdb_result,
                forced_season=forced_season,
            )
        finally:
            self._active_folder = None

    def _yt_fetch_playlist(self):
        """Fetch playlist/channel info from YouTube URL."""
        url = self.yt_url_var.get().strip()
        if not url:
            self.yt_status_var.set("❌ Ingresá una URL de YouTube")
            return
        
        if not is_youtube_url(url):
            self.yt_status_var.set("❌ URL no válida de YouTube")
            return
        
        self.yt_status_var.set("🔄 Obteniendo información...")
        self.yt_fetch_btn.configure(state="disabled", text="⏳ Cargando...")
        self.yt_status_var.set("🔄 Obteniendo información de YouTube...")
        self._yt_sync_extractor()
        
        def work():
            try:
                result = self.yt_extractor.extract_playlist_info(self.yt_url_var.get().strip())
                
                def done():
                    self.yt_fetch_btn.configure(state="normal", text="Obtener lista")
                    if result and result.get('entries'):
                        self.yt_entries = result['entries']
                        # Los videos no disponibles (borrados o privados) no se
                        # marcan: su .strm nunca se podría reproducir.
                        self.yt_selected = self._yt_available_indices()
                        self._populate_yt_list(preserve_selection=True)
                        self._update_yt_selection_count()
                        playlist_title = (result.get('playlist') or {}).get('title') or 'Playlist'
                        self.yt_playlist_title = playlist_title
                        # Sugerencia editable para el renombrado de series.
                        if not self.yt_series_var.get().strip():
                            self.yt_series_var.set(playlist_title)
                        no_disponibles = len(self.yt_entries) - len(self.yt_selected)
                        aviso = (
                            f" ({no_disponibles} no disponibles, sin seleccionar)"
                            if no_disponibles else ""
                        )
                        self.yt_status_var.set(
                            f"✅ {len(self.yt_entries)} videos encontrados en: {playlist_title}{aviso}"
                        )
                        self.yt_generate_btn.configure(state="normal")
                    else:
                        self.yt_status_var.set("❌ No se encontraron videos")
                        self.yt_entries = []
                        self._populate_yt_list()
                        self.yt_generate_btn.configure(state="disabled")
                self.root.after(0, done)
            except Exception as e:
                def done():
                    self.yt_fetch_btn.configure(state="normal", text="Obtener lista")
                    self.yt_status_var.set(f"❌ Error: {e}")
                self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _populate_yt_list(self, preserve_selection: bool = False):
        """Populate the YouTube video list."""
        # Clear existing rows
        for widget in self.yt_rows_frame.winfo_children():
            widget.destroy()

        if not preserve_selection:
            self.yt_selected = set()

        for index, entry in enumerate(self.yt_entries):
            self._add_yt_row(index, entry)

    def _add_yt_row(self, index: int, entry: dict):
        """Add a row to the YouTube video list."""
        video_id = entry.get('id', '')
        # «or» y no «get(default)»: los videos borrados o privados llegan con
        # title=None; como la clave existe, el default no se aplica y el corte
        # del texto reventaba con "TypeError: 'NoneType' is not subscriptable".
        disponible = entry.get('available', True)
        title = entry.get('title') or f'Video {video_id}'
        if not disponible:
            title = f"(no disponible) {title}"
        duration = entry.get('duration')
        
        # Format duration
        if duration:
            mins, secs = divmod(duration, 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                dur_str = f"{hours}:{mins:02d}:{secs:02d}"
            else:
                dur_str = f"{mins}:{secs:02d}"
        else:
            dur_str = "—"

        row = ctk.CTkFrame(self.yt_rows_frame, fg_color=("gray92", "gray17"), corner_radius=4)
        row.grid(row=index, column=0, sticky="ew", padx=4, pady=2)
        row.grid_columnconfigure(1, weight=2)
        row.grid_columnconfigure(2, weight=1)

        # Checkbox
        checked = index in self.yt_selected
        check_var = ctk.BooleanVar(value=checked)
        check = ctk.CTkCheckBox(
            row,
            text="",
            variable=check_var,
            width=30,
            command=lambda i=index, v=check_var: self._yt_toggle_check(i, v)
        )
        check.grid(row=0, column=0, padx=8, pady=6)

        # Title
        ctk.CTkLabel(
            row,
            text=title[:80] + ("..." if len(title) > 80 else ""),
            font=ctk.CTkFont(size=11),
            text_color=("gray20", "gray80") if disponible else ("gray50", "gray50"),
            anchor="w"
        ).grid(row=0, column=1, padx=8, pady=6, sticky="ew")

        # Duration
        ctk.CTkLabel(
            row,
            text=dur_str,
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            anchor="w"
        ).grid(row=0, column=2, padx=8, pady=6, sticky="ew")

        # Bind double-click to toggle
        row.bind("<Double-Button-1>", lambda e, idx=index: self._yt_toggle_check(idx, None))
        for child in row.winfo_children():
            child.bind("<Double-Button-1>", lambda e, idx=index: self._yt_toggle_check(idx, None))

    def _yt_toggle_check(self, index: int, var: ctk.BooleanVar = None):
        if var is not None:
            if var.get():
                self.yt_selected.add(index)
            else:
                self.yt_selected.discard(index)
        else:
            # Toggle
            if index in self.yt_selected:
                self.yt_selected.discard(index)
            else:
                self.yt_selected.add(index)
        self._update_yt_selection_count()

    def _yt_available_indices(self) -> set:
        """Índices de los videos que existen en YouTube (no borrados ni privados)."""
        return {
            i for i, entry in enumerate(self.yt_entries)
            if entry.get('available', True)
        }

    def _yt_set_all_selected(self, selected: bool):
        if selected:
            self.yt_selected = self._yt_available_indices()
        else:
            self.yt_selected.clear()
        self._populate_yt_list(preserve_selection=True)
        self._update_yt_selection_count()

    def _update_yt_selection_count(self):
        count = len(self.yt_selected)
        total = len(self.yt_entries)
        self.yt_count_label.configure(text=f"{total} videos ({count} seleccionados)")
        self.yt_status_var.set(f"{count} de {total} videos seleccionados")

    def _yt_generate_strm(self):
        """Generate .strm files for selected videos."""
        if not self.yt_entries:
            self.yt_status_var.set("❌ No hay videos para procesar")
            return
        
        if not self.yt_selected:
            self.yt_status_var.set("❌ Seleccioná al menos un video")
            return
        
        output_dir = self.yt_output_var.get().strip()
        if not output_dir:
            self.yt_status_var.set("❌ Seleccioná carpeta de salida")
            return
        
        naming_template = self.yt_naming_var.get().strip() or DEFAULT_STRM_TEMPLATE
        destination = YT_DESTINATIONS.get(self.yt_dest_var.get(), DEST_VLC)
        overwrite = bool(self.yt_overwrite_var.get())

        try:
            season = int(self.yt_season_var.get().strip() or "1")
        except ValueError:
            season = 1

        episode_text = self.yt_episode_var.get().strip()
        try:
            episode_start = int(episode_text) if episode_text else None
        except ValueError:
            episode_start = None

        self._yt_sync_extractor()
        series_rename = bool(self.yt_series_rename_var.get())
        # Se capturan en el hilo principal: el plan de series se arma en segundo plano.
        series_options = None
        if series_rename:
            series_options = {
                "query": self.yt_series_var.get().strip() or None,
                "lang": self.lang_var.get().strip() or DEFAULT_LANGUAGE,
                "provider": self.tv_provider_var.get(),
                "forced_season": self._yt_forced_season(),
            }

        self.yt_generate_btn.configure(state="disabled", text="⏳ Generando...")
        self.yt_status_var.set("🔄 Generando archivos .strm...")
        
        def work():
            try:
                # Filter selected entries
                selected_entries = [self.yt_entries[i] for i in self.yt_selected]
                
                creados, errores = self.yt_extractor.generate_strm_files(
                    selected_entries,
                    output_dir,
                    naming_template=naming_template,
                    log_fn=self._append_log,
                    overwrite=overwrite,
                    destination=destination,
                    season=season,
                    episode_start=episode_start
                )

                # Renombrado de series opcional: reusa el planificador de la pestaña
                # Organizador sobre los .strm recién creados.
                plan = None
                if series_options and creados:
                    self._append_log("[i] Armando el plan de renombrado de series…")
                    plan = self._yt_plan_series_rename(output_dir, self._append_log, **series_options)
                
                def done():
                    self.yt_generate_btn.configure(state="normal", text="🎬 Generar archivos .strm")
                    if errores == 0:
                        self.yt_status_var.set(f"✅ {creados} archivos .strm generados en {output_dir}")
                    elif creados:
                        detalle = "sin URL directa" if destination == DEST_JELLYFIN_DIRECT else "no se pudieron generar"
                        self.yt_status_var.set(f"⚠️ {creados} generados, {errores} {detalle}")
                    else:
                        self.yt_status_var.set("❌ No se generó ningún archivo (mirá el log)")

                    if series_rename:
                        if plan and plan.get("moves"):
                            self._show_plan(plan)
                            self.tab_view.set("📁 Organizador")
                            self.yt_status_var.set(
                                f"🎬 Plan de series listo: {len(plan['moves'])} archivo(s) a renombrar. "
                                f"Revisalo en «Organizador» y pulsá Aplicar."
                            )
                        elif plan is not None:
                            self.yt_status_var.set("✅ .strm generados; ya tenían el nombre final de la serie.")
                        else:
                            self.yt_status_var.set(
                                "⚠️ .strm generados, pero no se pudo armar el plan de series (mirá el log)."
                            )
                
                self.root.after(0, done)
            except Exception as e:
                def done():
                    self.yt_generate_btn.configure(state="normal", text="🎬 Generar archivos .strm")
                    self.yt_status_var.set(f"❌ Error: {e}")
                self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    # ==================== Google Drive: handlers ====================

    def _gd_browse_output(self):
        """Elegir la carpeta raíz donde se escribirán los .strm."""
        path = filedialog.askdirectory(title="Elegí la carpeta de salida para los .strm")
        if path:
            self.gd_output_var.set(path)

    def _gd_sync_client(self):
        """Crea el cliente de Drive con la API key escrita en la GUI (si hay)."""
        self.gd_client = GoogleDriveClient(
            api_key=self.gd_api_key_var.get().strip(),
            log_fn=self._append_log,
        )

    def _gd_fetch(self):
        """Lee la carpeta de Google Drive y muestra los videos encontrados."""
        url = self.gd_url_var.get().strip()
        if not url:
            self.gd_status_var.set("❌ Ingresá una URL de Google Drive")
            return
        try:
            parse_drive_target(url)
        except GoogleDriveError as exc:
            self.gd_status_var.set(f"❌ {exc}")
            return

        self.gd_fetch_btn.configure(state="disabled", text="⏳ Cargando...")
        self.gd_status_var.set("🔄 Leyendo Google Drive...")
        self._gd_sync_client()
        recursive = bool(self.gd_recursive_var.get())

        def work():
            try:
                result = self.gd_client.scan(url, recursive=recursive)

                def done():
                    self.gd_fetch_btn.configure(state="normal", text="Obtener lista")
                    self.gd_entries = list(result.videos)
                    self.gd_selected = set(range(len(self.gd_entries)))
                    self.gd_root_name = result.root_name
                    self._gd_populate_list()
                    self._gd_update_count()

                    if self.gd_entries:
                        if not self.gd_query_var.get().strip():
                            suggested, _ = clean_query_string(result.root_name)
                            self.gd_query_var.set(suggested or result.root_name)
                        self.gd_generate_btn.configure(state="normal")
                        seasons = sorted({
                            video.season for video in self.gd_entries
                            if video.season is not None
                        })
                        detail = f" · temporadas {', '.join(str(s) for s in seasons)}" if seasons else ""
                        self.gd_status_var.set(
                            f"✅ {len(self.gd_entries)} videos encontrados en "
                            f"«{result.root_name}»{detail}"
                        )
                    else:
                        self.gd_status_var.set("❌ No se encontraron videos en la carpeta")
                        self.gd_generate_btn.configure(state="disabled")

                    if result.errors:
                        self._append_log(
                            f"[!] {len(result.errors)} subcarpeta(s) no se pudieron leer."
                        )

                self.root.after(0, done)
            except GoogleDriveError as exc:
                def done():
                    self.gd_fetch_btn.configure(state="normal", text="Obtener lista")
                    self.gd_status_var.set(f"❌ {exc}")
                    self._append_log(f"[!] {exc}")
                    self.gd_entries = []
                    self._gd_populate_list()
                    self.gd_generate_btn.configure(state="disabled")
                self.root.after(0, done)
            except Exception as exc:  # noqa: BLE001 - se muestra en la GUI
                def done():
                    self.gd_fetch_btn.configure(state="normal", text="Obtener lista")
                    self.gd_status_var.set(f"❌ Error: {exc}")
                    self._append_log(f"[!] Error leyendo Google Drive: {exc}")
                self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _gd_populate_list(self):
        for widget in self.gd_rows_frame.winfo_children():
            widget.destroy()
        for index, video in enumerate(self.gd_entries):
            self._gd_add_row(index, video)

    def _gd_add_row(self, index: int, video):
        title = video.name or f"Video {video.id}"
        if video.season is not None:
            season_text = f"S{video.season:02d}"
            if video.episode is not None:
                season_text += f"E{video.episode:02d}"
        else:
            season_text = "—"

        row = ctk.CTkFrame(self.gd_rows_frame, fg_color=("gray92", "gray17"), corner_radius=4)
        row.grid(row=index, column=0, sticky="ew", padx=4, pady=2)
        row.grid_columnconfigure(1, weight=3)
        row.grid_columnconfigure(2, weight=1)

        checked = index in self.gd_selected
        check_var = ctk.BooleanVar(value=checked)
        ctk.CTkCheckBox(
            row,
            text="",
            variable=check_var,
            width=30,
            command=lambda i=index, v=check_var: self._gd_toggle_check(i, v),
        ).grid(row=0, column=0, padx=8, pady=6)

        folder = "/".join(video.path)
        label = f"{title[:90]}{'...' if len(title) > 90 else ''}"
        if folder:
            label += f"   [{folder}]"
        ctk.CTkLabel(
            row,
            text=label,
            font=ctk.CTkFont(size=11),
            text_color=("gray20", "gray80"),
            anchor="w",
        ).grid(row=0, column=1, padx=8, pady=6, sticky="ew")

        ctk.CTkLabel(
            row,
            text=season_text,
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
            anchor="w",
        ).grid(row=0, column=2, padx=8, pady=6, sticky="ew")

        row.bind("<Double-Button-1>", lambda e, idx=index: self._gd_toggle_check(idx, None))
        for child in row.winfo_children():
            child.bind("<Double-Button-1>", lambda e, idx=index: self._gd_toggle_check(idx, None))

    def _gd_toggle_check(self, index: int, var: ctk.BooleanVar = None):
        if var is not None:
            if var.get():
                self.gd_selected.add(index)
            else:
                self.gd_selected.discard(index)
        elif index in self.gd_selected:
            self.gd_selected.discard(index)
        else:
            self.gd_selected.add(index)
        self._gd_update_count()

    def _gd_set_all_selected(self, selected: bool):
        self.gd_selected = set(range(len(self.gd_entries))) if selected else set()
        self._gd_populate_list()
        self._gd_update_count()

    def _gd_update_count(self):
        count = len(self.gd_selected)
        total = len(self.gd_entries)
        self.gd_count_label.configure(text=f"{total} videos ({count} seleccionados)")

    def _gd_plan_metadata(self, paths: list[str], mode: str, query: str | None,
                          lang: str, provider: str, output_dir: str) -> dict | None:
        """Arma el plan de renombrado con metadatos sobre los .strm recién creados."""
        organizer = MediaOrganizer(*create_metadata_clients(provider))
        self.organizer = organizer
        self._active_folder = output_dir
        try:
            if mode == "tv":
                return organizer.plan_tv_show(
                    paths,
                    query,
                    lang=lang,
                    target_base_dir=output_dir,
                    log_fn=self._append_log,
                    pick_result_fn=self._pick_tmdb_result,
                )
            return organizer.plan_bulk_movies(
                paths,
                target_base_dir=output_dir,
                lang=lang,
                log_fn=self._append_log,
                pick_result_fn=self._pick_tmdb_result,
            )
        finally:
            self._active_folder = None

    def _gd_generate(self):
        """Genera los .strm y, si se pidió, el plan de identificación con metadatos."""
        if not self.gd_entries:
            self.gd_status_var.set("❌ No hay videos para procesar")
            return
        if not self.gd_selected:
            self.gd_status_var.set("❌ Seleccioná al menos un video")
            return

        output_dir = self.gd_output_var.get().strip()
        if not output_dir:
            self.gd_status_var.set("❌ Seleccioná la carpeta de salida")
            return

        selected = [self.gd_entries[i] for i in sorted(self.gd_selected)]
        mode = GD_MODES.get(self.gd_mode_var.get(), "auto")
        if mode == "auto":
            mode = "tv" if any(video.season is not None for video in selected) else "movie"

        provider = self.tv_provider_var.get()
        if provider == "Sin proveedor (limpiar)":
            mode = "none"

        capture = {
            "mode": mode,
            "query": self.gd_query_var.get().strip() or None,
            "lang": self.lang_var.get().strip() or DEFAULT_LANGUAGE,
            "provider": provider,
            "overwrite": bool(self.gd_overwrite_var.get()),
        }

        self.gd_generate_btn.configure(state="disabled", text="⏳ Generando...")
        self.gd_status_var.set("🔄 Generando archivos .strm...")

        def work():
            created = []
            errors = 0
            plan = None
            try:
                created, errors = write_strm_files(
                    selected,
                    output_dir,
                    overwrite=capture["overwrite"],
                    log_fn=self._append_log,
                )

                if created and capture["mode"] != "none":
                    self._append_log("[i] Armando el plan de identificación con metadatos…")
                    plan = self._gd_plan_metadata(
                        created,
                        capture["mode"],
                        capture["query"],
                        capture["lang"],
                        capture["provider"],
                        output_dir,
                    )
            except Exception as exc:  # noqa: BLE001 - se muestra en la GUI
                self._append_log(f"[!] Error generando .strm: {exc}")

            def done():
                self.gd_generate_btn.configure(
                    state="normal", text="🎬 Generar archivos .strm"
                )
                if errors == 0 and created:
                    self.gd_status_var.set(
                        f"✅ {len(created)} archivos .strm generados en {output_dir}"
                    )
                elif created:
                    self.gd_status_var.set(
                        f"⚠️ {len(created)} generados, {errors} con error (mirá el log)"
                    )
                else:
                    self.gd_status_var.set("❌ No se generó ningún archivo (mirá el log)")

                if capture["mode"] == "none" and created:
                    self.gd_status_var.set(
                        f"✅ {len(created)} archivos .strm generados (sin renombrar)"
                    )
                elif plan and plan.get("moves"):
                    self._show_plan(plan)
                    self.tab_view.set("📁 Organizador")
                    self.gd_status_var.set(
                        f"🎬 Plan de identificación listo: {len(plan['moves'])} archivo(s). "
                        f"Revisalo en «Organizador» y pulsá Aplicar."
                    )
                elif created:
                    self.gd_status_var.set(
                        "⚠️ .strm generados, pero no se pudo armar el plan de metadatos (mirá el log)."
                    )

            self.root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    # ==================== Organizer: plan / preview ====================

    def _on_type_change(self):
        """Enable/disable fields based on selected media type."""
        media_type = self.type_var.get()
        is_bulk_movie = media_type == "bulk-movie"
        is_tv = media_type == "tv"
        is_book = media_type == "book"
        season_state = "normal" if is_tv else "disabled"
        try:
            self.season_entry.configure(state=season_state)
        except Exception:
            pass
        scan_state = "readonly" if (is_tv or media_type == "auto" or is_book) else "disabled"
        try:
            self.scan_mode_combo.configure(state=scan_state)
        except Exception:
            pass
        if is_book:
            self.query_label.configure(text="Libro/título:")
            self.query_entry.configure(placeholder_text="(vacío = autodetección por archivo)")
            self.query_hint.configure(text="(vacío = autodetección por archivo)")
        elif is_bulk_movie:
            self.query_label.configure(text="Serie/película:")
            self.query_entry.configure(placeholder_text="(vacío = autodetección por archivo)")
            self.query_hint.configure(text="(vacío = autodetección por archivo)")
        else:
            self.query_label.configure(text="Serie/película:")
            self.query_entry.configure(placeholder_text="(vacío = autodetección)")
            self.query_hint.configure(text="(vacío = autodetección)")

    def _clear_plan_view(self):
        for widget in self.preview_rows_frame.winfo_children():
            widget.destroy()
        self.plan = None
        self.selected_moves.clear()
        self.total_var.set("Seleccionados: 0 de 0 archivo(s)")
        self.apply_btn.configure(state="disabled")

    def _show_plan(self, plan: dict | None):
        self._clear_plan_view()
        if not plan:
            self.status_var.set("No se pudo armar un plan. Revisá el log.")
            return
        self.plan = plan
        self.selected_moves = set(range(len(plan["moves"])))
        is_batch = plan.get("type") == "batch"
        for index, move in enumerate(plan["moves"]):
            if is_batch:
                try:
                    source_name = os.path.relpath(move["src"], plan["dest_main_folder"])
                    destination_name = os.path.relpath(move["dest"], plan["dest_main_folder"])
                except Exception:
                    source_name = os.path.basename(move["src"])
                    destination_name = move.get("rel_dest", "")
            else:
                source_name = os.path.basename(move["src"])
                destination_name = move.get("rel_dest", "")
            confidence = move.get("confidence", "medium")
            self._add_preview_row(index, source_name, destination_name, confidence)
        self._update_selection_total()
        self.status_var.set(f"Plan: {plan['title']} → {plan['dest_main_folder']}")
        self.apply_btn.configure(state="normal" if plan["moves"] else "disabled")

    def _add_preview_row(self, index: int, source: str, dest: str, confidence: str):
        colors = {
            "high": ("#d4f6d4", "#1b5e20"),
            "medium": ("#fff9c4", "#f57f17"),
            "low": ("#ffcdd2", "#c62828"),
        }
        bg_color, text_color = colors.get(confidence, ("#fff9c4", "#f57f17"))
        row = ctk.CTkFrame(self.preview_rows_frame, fg_color=bg_color, corner_radius=4)
        row.grid(row=index, column=0, sticky="ew", padx=4, pady=2)
        row.grid_columnconfigure(1, weight=2)
        row.grid_columnconfigure(2, weight=2)
        checked = index in self.selected_moves
        check_var = ctk.BooleanVar(value=checked)
        check = ctk.CTkCheckBox(
            row, text="", variable=check_var, width=30,
            command=lambda i=index, v=check_var: self._toggle_move_check(i, v),
        )
        check.grid(row=0, column=0, padx=8, pady=6)
        ctk.CTkLabel(
            row, text=source, font=ctk.CTkFont(size=11),
            text_color=("gray20", "gray80"), anchor="w",
        ).grid(row=0, column=1, padx=8, pady=6, sticky="ew")
        ctk.CTkLabel(
            row, text=dest, font=ctk.CTkFont(size=11),
            text_color=text_color, anchor="w",
        ).grid(row=0, column=2, padx=8, pady=6, sticky="ew")
        if self.plan["moves"][index].get("candidates"):
            row.bind("<Double-Button-1>", lambda e, idx=index: self._on_double_click_reselect(idx))
            for child in row.winfo_children():
                child.bind("<Double-Button-1>", lambda e, idx=index: self._on_double_click_reselect(idx))

    def _toggle_move_check(self, index: int, var: ctk.BooleanVar):
        if var.get():
            self.selected_moves.add(index)
        else:
            self.selected_moves.discard(index)
        self._update_selection_total()

    def _set_all_moves(self, selected: bool):
        if not self.plan:
            return
        self.selected_moves = set(range(len(self.plan["moves"]))) if selected else set()
        self._show_plan(self.plan)

    def _update_selection_total(self):
        total = len(self.plan["moves"]) if self.plan else 0
        self.total_var.set(f"Seleccionados: {len(self.selected_moves)} de {total} archivo(s)")

    def _on_double_click_reselect(self, index: int):
        if getattr(self, "_reselect_dialog_open", False):
            return
        self._reselect_dialog_open = True
        try:
            if not self.plan or index >= len(self.plan["moves"]):
                return
            move = self.plan["moves"][index]
            candidates = move.get("candidates")
            if not candidates:
                return
            chosen = self._show_candidate_selection_dialog(candidates, move)
            if chosen:
                self._update_move_with_choice(index, chosen)
        finally:
            self._reselect_dialog_open = False

    def _show_candidate_selection_dialog(self, candidates: list, move: dict) -> dict | None:
        result_holder = [None]

        def show_dialog():
            win = ctk.CTkToplevel(self.root)
            win.title("Seleccionar coincidencia correcta")
            win.grab_set()
            win.resizable(False, False)
            self.root.update_idletasks()
            x = self.root.winfo_x() + self.root.winfo_width() // 2 - 300
            y = self.root.winfo_y() + self.root.winfo_height() // 2 - 250
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(0, min(x, screen_w - 600))
            y = max(0, min(y, screen_h - 500))
            win.geometry(f"600x500+{x}+{y}")
            win.lift()
            ctk.CTkLabel(
                win, text=f"Múltiples coincidencias para: {os.path.basename(move['src'])}",
                font=ctk.CTkFont(size=12), wraplength=560,
            ).pack(padx=20, pady=(20, 6), anchor="w")
            ctk.CTkLabel(
                win, text=f"Destino actual: {move.get('rel_dest', '')}",
                font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"), wraplength=560,
            ).pack(padx=20, pady=(0, 6), anchor="w")
            frame = ctk.CTkFrame(win)
            frame.pack(fill="both", expand=True, padx=20, pady=10)
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
                    values = (c.get("name"), c.get("author") or "", c.get("year") or "", c.get("source") or "")
                else:
                    values = (c.get("name"), c.get("year"), c.get("id"))
                tree.insert("", tk.END, iid=str(i), values=values)
            tree.selection_set("0")
            tree.focus("0")
            btn_frame = ctk.CTkFrame(win, fg_color="transparent")
            btn_frame.pack(fill=tk.X, padx=20, pady=(6, 20))

            def on_select():
                sel = tree.selection()
                if sel:
                    result_holder[0] = candidates[int(sel[0])]
                win.destroy()

            def on_cancel():
                result_holder[0] = None
                win.destroy()

            ctk.CTkButton(btn_frame, text="Seleccionar", command=on_select).pack(side="left", padx=(0, 8))
            ctk.CTkButton(
                btn_frame, text="Cancelar", command=on_cancel,
                fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40"),
            ).pack(side="left")
            tree.bind("<Double-1>", lambda _: on_select())
            win.protocol("WM_DELETE_WINDOW", on_cancel)
            win.wait_window()

        show_dialog()
        return result_holder[0]

    def _update_move_with_choice(self, index: int, chosen: dict):
        if not self.plan or index >= len(self.plan["moves"]):
            return
        move = self.plan["moves"][index]
        if move.get("book"):
            self.organizer.rebuild_book_move(move, chosen)
            self._show_plan(self.plan)
            self._append_log(f"[i] Re-seleccionado: {move.get('rel_dest', '')} (confianza: high)")
            return
        title = sanitize_filename(chosen.get("name", ""))
        year = chosen.get("year")
        year_str = f" ({year})" if year and year != "?" else ""
        ext = os.path.splitext(move["src"])[1]
        canonical = f"{title}{year_str}{ext}"
        move["dest"] = os.path.join(self.plan["dest_main_folder"], canonical)
        move["rel_dest"] = canonical
        move["confidence"] = "high"
        move["confidence_score"] = 0.95
        move["title"] = title
        move["year"] = chosen.get("year")
        move.pop("candidates", None)
        self._show_plan(self.plan)
        self._append_log(f"[i] Re-seleccionado: {canonical} (confianza: high)")

    def _on_plan(self):
        if self._busy:
            return
        path = self.path_var.get().strip(' "\'')
        if not path or not os.path.exists(path.rstrip("*?")):
            self.status_var.set("La ruta no existe.")
            self._append_log("[!] La ruta no existe.")
            return
        self._set_busy(True)
        self.status_var.set(
            "Preparando limpieza local…" if self.tv_provider_var.get() == "Sin proveedor (limpiar)"
            else "Consultando metadatos…"
        )
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
                        item_plan = organizer.plan_clean_filenames(path, show_title=query, log_fn=self._append_log)
                    else:
                        item_plan = organizer.plan_bulk_movies(
                            path, lang=lang, log_fn=self._append_log,
                            pick_result_fn=self._pick_tmdb_result,
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
                                target, local_query, lang=lang, log_fn=self._append_log,
                                pick_result_fn=self._pick_tmdb_result,
                            )
                        else:
                            default_name = os.path.basename(os.path.abspath(target.rstrip("*?")))
                            movie_query = local_query or default_name
                            item_plan = organizer.plan_movie(
                                target, movie_query, lang=lang, log_fn=self._append_log,
                                pick_result_fn=self._pick_tmdb_result,
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
                ok = self.organizer.execute_plan(plan, dry_run=dry_run, auto_yes=True, log_fn=self._append_log)
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

    def _show_review_queue(self, folders: list[str], tv_provider: str, lang: str, root_path: str):
        if not folders:
            return
        win = ctk.CTkToplevel(self.root)
        win.title("Carpetas pendientes de reconocer")
        win.geometry("680x540")
        win.minsize(620, 460)
        win.transient(self.root)
        win.lift()
        win.grab_set()
        ctk.CTkLabel(
            win, text="Estas carpetas no se incorporaron al lote. Elegí una y decidí cómo procesarla.",
            font=ctk.CTkFont(size=12), wraplength=570,
        ).pack(anchor="w", padx=20, pady=(20, 10))
        win_frame = ctk.CTkFrame(win)
        win_frame.pack(fill="both", expand=True, padx=20, pady=10)
        cols = ("folder",)
        tree = ttk.Treeview(win_frame, columns=cols, show="headings", selectmode="browse", height=8)
        tree.heading("folder", text="Carpeta pendiente")
        tree.column("folder", width=540, stretch=True)
        scroll = ttk.Scrollbar(win_frame, orient=tk.VERTICAL, command=tree.yview)
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
        controls = ctk.CTkFrame(win, fg_color="transparent")
        controls.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(controls, text="Nombre:").grid(row=0, column=0, sticky="w")
        query_var = ctk.StringVar()
        ctk.CTkEntry(controls, textvariable=query_var, width=300).grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(controls, text="Procesar como:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        mode_var = ctk.StringVar(value="Serie")
        ctk.CTkComboBox(
            controls, variable=mode_var, values=("Serie", "Película", "Solo limpiar nombres"),
            state="readonly", width=200,
        ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        ctk.CTkLabel(controls, text="Forzar temporada:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        season_var = ctk.StringVar(value="")
        season_combo = ctk.CTkComboBox(controls, variable=season_var, state="readonly", width=300, values=("",))
        season_combo.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        load_seasons_btn = ctk.CTkButton(controls, text="Buscar temporadas")
        load_seasons_btn.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
        ctk.CTkLabel(
            controls, text="Vacío = automático", font=ctk.CTkFont(size=10),
            text_color=("gray40", "gray60"),
        ).grid(row=2, column=3, sticky="w", padx=(8, 0), pady=(8, 0))
        controls.grid_columnconfigure(3, weight=1)
        status_var = ctk.StringVar(value="Elegí una carpeta para continuar.")
        ctk.CTkLabel(win, textvariable=status_var, text_color=("gray40", "gray60")).pack(anchor="w", padx=20)
        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=(10, 20))

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
                        item_plan = self.organizer.plan_clean_filenames(
                            folder, show_title=query or None, log_fn=self._append_log
                        )
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
                        label = (
                            f"Temporada {number} · {season_name}" if season_name
                            else f"Temporada {number} ({count} episodios)"
                        )
                        season_numbers[label] = number
                        options.append(label)
                    season_combo.configure(values=options)
                    season_var.set("")
                    status_var.set("Elegí una temporada solo si querés forzarla; vacío mantiene el modo automático.")

                self.root.after(0, done)

            threading.Thread(target=work, daemon=True).start()

        ctk.CTkButton(actions, text="Agregar al plan", command=add_to_plan,
                      font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        ctk.CTkButton(actions, text="Omitir", command=remove_selected,
                      fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40")).pack(side="left", padx=(8, 0))
        ctk.CTkButton(actions, text="Cerrar", command=win.destroy,
                      fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40")).pack(side="right")
        tree.bind("<<TreeviewSelect>>", suggest_name)
        load_seasons_btn.configure(command=load_seasons)
        suggest_name()

    def _pick_tmdb_result(self, candidates: list) -> dict | None:
        result_holder = [None]
        done_event = threading.Event()

        def show_dialog():
            win = ctk.CTkToplevel(self.root)
            win.title("Seleccionar resultado")
            win.grab_set()
            win.resizable(False, False)
            self.root.update_idletasks()
            x = self.root.winfo_x() + self.root.winfo_width() // 2 - 280
            y = self.root.winfo_y() + self.root.winfo_height() // 2 - 200
            win.geometry(f"560x420+{x}+{y}")
            ctk.CTkLabel(
                win, text="Se encontraron varios resultados. Elegí el correcto:",
                font=ctk.CTkFont(size=12), wraplength=520,
            ).pack(padx=20, pady=(20, 6), anchor="w")
            if self._active_folder:
                ctk.CTkLabel(
                    win, text=f"Carpeta: {os.path.basename(self._active_folder)}",
                    font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"), wraplength=520,
                ).pack(padx=20, pady=(0, 6), anchor="w")
            frame = ctk.CTkFrame(win)
            frame.pack(fill="both", expand=True, padx=20, pady=10)
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
                tree.insert("", tk.END, iid=str(i), values=(c.get("name"), c.get("year"), c.get("id")))
            tree.selection_set("0")
            tree.focus("0")
            btn_frame = ctk.CTkFrame(win, fg_color="transparent")
            btn_frame.pack(fill=tk.X, padx=20, pady=(6, 20))

            def on_select():
                sel = tree.selection()
                if sel:
                    result_holder[0] = candidates[int(sel[0])]
                win.destroy()
                done_event.set()

            def on_cancel():
                result_holder[0] = None
                win.destroy()
                done_event.set()

            ctk.CTkButton(btn_frame, text="Seleccionar", command=on_select).pack(side="left", padx=(0, 8))
            ctk.CTkButton(btn_frame, text="Cancelar", command=on_cancel,
                          fg_color=("gray70", "gray30"), hover_color=("gray60", "gray40")).pack(side="left")
            tree.bind("<Double-1>", lambda _: on_select())
            win.protocol("WM_DELETE_WINDOW", on_cancel)

        self.root.after(0, show_dialog)
        done_event.wait()
        return result_holder[0]

    # ==================== Helper Methods ====================

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

# ==================== Entry Point ====================

def run_gui():
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    MediaRenamerApp(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
