# -*- coding: utf-8 -*-
import os
import sys
import argparse
from dotenv import load_dotenv

from .metadata import create_metadata_clients
from .organizer import MediaOrganizer
from .utils import clean_query_string
from .config import DEFAULT_LANGUAGE, GOOGLE_DRIVE_API_KEY
from .gdrive import GoogleDriveClient, GoogleDriveError, write_strm_files


# Load .env early
load_dotenv(override=False)


def run_gui() -> None:
    """Abre la GUI moderna (CustomTkinter) y cae a la clásica si no está instalada.

    La GUI moderna es la que documenta el README y la que incluye las pestañas de
    YouTube y Google Drive. ``customtkinter`` es opcional, así que se importa acá y
    no al cargar el módulo: el CLI debe funcionar sin él.
    """
    try:
        from .gui_ctk import run_gui as run_modern_gui
    except ImportError:
        from .gui import run_gui as run_classic_gui

        run_classic_gui()
    else:
        run_modern_gui()


def _cli_pick_result(candidates: list) -> dict | None:
    """
    Muestra los candidatos en consola y pide al usuario que elija uno.
    Retorna el candidato elegido, o None si cancela.
    """
    print("\nSe encontraron varios resultados:")
    for i, c in enumerate(candidates, 1):
        author = f" — {c['author']}" if c.get('author') else ""
        print(f"  [{i}] {c['name']}{author}  ({c['year']})  [ID: {c['id']}]")
    print(f"  [0] Cancelar")
    while True:
        try:
            choice = input(f"\nElegí una opción (0-{len(candidates)}): ").strip()
            idx = int(choice)
            if idx == 0:
                return None
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1]
        except (ValueError, EOFError):
            pass
        print("  [!] Opción inválida, intentá de nuevo.")


def interactive_menu(organizer: MediaOrganizer):
    print("\n" + "=" * 50)
    print("      ORGANIZADOR MULTIMEDIA MODULAR (TMDb)      ")
    print("=" * 50)
    print("1. Organizar Serie de TV (carpeta única o patrón)")
    print("2. Organizar Película")
    print("3. Organizar Libros o Cómics")
    print("4. Deshacer última operación (Undo)")
    print("5. Salir")
    opc = input("\nElige una opción (1-5): ").strip()
    if opc == '1':
        paths = input("Ruta de la carpeta o patrón (ej. C:\\Series\\TMNT*): ").strip(' "\'')
        if not paths:
            return
        default_q, _ = clean_query_string(os.path.basename(os.path.abspath(paths.rstrip('*?'))))
        q = input(f"Nombre de la serie en TMDb [Enter para autodetección o '{default_q}']: ").strip()
        plan = organizer.plan_tv_show(paths, q or None, pick_result_fn=_cli_pick_result)
        if plan:
            organizer.execute_plan(plan)
    elif opc == '2':
        path = input("Ruta de la carpeta o archivo de la película: ").strip(' "\'')
        if not path or not os.path.exists(path):
            print("[!] La ruta no existe.")
            return
        default_q, _ = clean_query_string(os.path.basename(os.path.abspath(path)))
        q = input(f"Nombre de la película en TMDb [Enter para '{default_q}']: ").strip()
        plan = organizer.plan_movie(path, q or default_q, pick_result_fn=_cli_pick_result)
        if plan:
            organizer.execute_plan(plan)
    elif opc == '3':
        path = input("Ruta de la carpeta o archivo de libros: ").strip(' "\'')
        if not path or not os.path.exists(path):
            print("[!] La ruta no existe.")
            return
        q = input("Título/autor a buscar [Enter para autodetección por archivo]: ").strip()
        plan = organizer.plan_books(path, q or None, pick_result_fn=_cli_pick_result)
        if plan:
            organizer.execute_plan(plan)
    elif opc == '4':
        organizer.undo_last_operation()

def run_drive_cli(args) -> None:
    """Genera .strm desde Google Drive y, opcionalmente, planifica el renombrado."""
    if not args.drive_output:
        print("[!] Indicá la carpeta de salida con --drive-output.")
        raise SystemExit(2)

    organizer = MediaOrganizer(*create_metadata_clients(args.drive_provider))

    client = GoogleDriveClient(
        api_key=args.drive_key or GOOGLE_DRIVE_API_KEY,
        log_fn=print,
    )
    try:
        result = client.scan(args.drive, recursive=not args.drive_no_recursive)
    except GoogleDriveError as exc:
        print(f"[!] {exc}")
        raise SystemExit(1) from exc

    if not result.videos:
        print("[!] No se encontraron videos en la carpeta de Google Drive.")
        raise SystemExit(1)

    print(f"[i] {len(result.videos)} video(s) en «{result.root_name}».")
    created, errors = write_strm_files(
        result.videos,
        args.drive_output,
        overwrite=args.drive_overwrite,
        log_fn=print,
    )
    print(f"[i] .strm creados: {len(created)} · errores: {errors}")
    if not created:
        raise SystemExit(1)

    mode = args.drive_mode
    if mode == "auto":
        mode = "tv" if any(video.season is not None for video in result.videos) else "movie"
    if mode == "none":
        return

    query = args.query or clean_query_string(result.root_name)[0] or None
    if mode == "tv":
        plan = organizer.plan_tv_show(
            created,
            query,
            lang=args.lang,
            target_base_dir=args.drive_output,
            log_fn=print,
            pick_result_fn=_cli_pick_result,
        )
    else:
        plan = organizer.plan_bulk_movies(
            created,
            target_base_dir=args.drive_output,
            lang=args.lang,
            log_fn=print,
            pick_result_fn=_cli_pick_result,
        )

    if not plan:
        print("[!] No se pudo armar el plan de identificación.")
        raise SystemExit(1)
    organizer.execute_plan(plan, dry_run=args.dry_run, auto_yes=args.yes)


def main():
    parser = argparse.ArgumentParser(description="Media Renamer & Organizer (Series, Películas, Libros y Cómics)")
    parser.add_argument("paths", nargs="*", help="Ruta(s) o patrón(es) a organizar (ej. 'TMNT*')")
    parser.add_argument("--type", choices=['tv', 'movie', 'bulk-movie', 'book'], help="Tipo de medio: tv, movie, bulk-movie, book")
    parser.add_argument("--query", help="Nombre a buscar en TMDb/Open Library (ignorado en bulk-movie)")
    parser.add_argument("--lang", default=DEFAULT_LANGUAGE, help=f"Código de idioma (por defecto: {DEFAULT_LANGUAGE})")
    parser.add_argument("--dry-run", action="store_true", help="Simula los cambios sin mover archivos")
    parser.add_argument("--yes", "-y", action="store_true", help="Acepta automáticamente sin pedir confirmación")
    parser.add_argument("--undo", action="store_true", help="Deshace la última operación realizada")
    parser.add_argument("--gui", action="store_true", help="Abre la interfaz gráfica")
    parser.add_argument("--cli", action="store_true", help="Abre el menú interactivo de consola")
    parser.add_argument(
        "--drive",
        metavar="URL",
        help="Genera .strm desde una carpeta pública de Google Drive",
    )
    parser.add_argument("--drive-output", help="Carpeta de salida para los .strm de Google Drive")
    parser.add_argument(
        "--drive-mode",
        choices=["auto", "tv", "movie", "none"],
        default="auto",
        help="Identificación de los .strm de Drive: auto, tv, movie o none (solo generar)",
    )
    parser.add_argument("--drive-key", help="API key de Google Drive (opcional)")
    parser.add_argument(
        "--drive-provider",
        choices=["TMDb", "TheTVDB"],
        default="TMDb",
        help="Proveedor de metadatos para el renombrado de Drive (por defecto: TMDb)",
    )
    parser.add_argument(
        "--drive-no-recursive",
        action="store_true",
        help="No entrar en subcarpetas de Drive",
    )
    parser.add_argument("--drive-overwrite", action="store_true", help="Sobrescribe .strm existentes")

    args = parser.parse_args()

    if args.drive:
        run_drive_cli(args)
        return

    tv_client, movie_client = create_metadata_clients()
    organizer = MediaOrganizer(tv_client, movie_client)

    if args.undo:
        organizer.undo_last_operation()
        return

    if not args.paths:
        if args.cli:
            interactive_menu(organizer)
        else:
            run_gui()
        return

    paths = args.paths if len(args.paths) > 1 else args.paths[0]
    first_path = args.paths[0]

    media_type = args.type
    if not media_type:
        files = organizer.find_files_in_paths(paths)
        book_files = organizer.find_book_files_in_paths(paths)
        if files:
            media_type = 'movie' if len(files) == 1 else 'tv'
        elif book_files:
            media_type = 'book'
        else:
            media_type = 'tv'

    if media_type == 'tv':
        plan = organizer.plan_tv_show(paths, args.query, lang=args.lang, pick_result_fn=_cli_pick_result)
    elif media_type == 'bulk-movie':
        plan = organizer.plan_bulk_movies(paths, lang=args.lang, pick_result_fn=_cli_pick_result)
    elif media_type == 'book':
        plan = organizer.plan_books(paths, args.query, lang=args.lang, pick_result_fn=_cli_pick_result)
    else:
        default_name = os.path.basename(os.path.abspath(first_path.rstrip('*?')))
        query = args.query or default_name
        plan = organizer.plan_movie(first_path, query, lang=args.lang, pick_result_fn=_cli_pick_result)

    if not plan:
        sys.exit(1)

    organizer.execute_plan(plan, dry_run=args.dry_run, auto_yes=args.yes)

if __name__ == "__main__":
    main()
