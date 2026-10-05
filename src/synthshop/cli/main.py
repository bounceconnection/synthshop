"""Typer launcher and local inventory inspection; publication exists only in reviewed UI."""

import webbrowser
from pathlib import Path
from threading import Timer
from typing import Annotated

import typer
import uvicorn
from rich.console import Console

from synthshop.core.application import Application
from synthshop.core.config import Settings
from synthshop.web.app import create_app

app = typer.Typer(
    name="synthshop",
    help="Local photo-to-Reverb drafting and explicit review.",
    no_args_is_help=True,
)


@app.command()
def serve(
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
    data_dir: Annotated[
        Path | None, typer.Option(help="Managed private local storage directory.")
    ] = None,
) -> None:
    """Start the local website on 127.0.0.1 only. No listing is published by launching."""
    settings = Settings()
    if data_dir:
        settings.data_dir = data_dir
    url = f"http://127.0.0.1:{port}"
    Console().print(f"SynthShop local review: {url}", markup=False)
    application = create_app(settings, port=port)
    if open_browser:
        timer = Timer(1.0, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    uvicorn.run(application, host="127.0.0.1", port=port, access_log=False)


@app.command(name="list")
def list_drafts() -> None:
    """List local drafts; never create, end, sell or publish remote listings."""
    service = Application(Settings())
    console = Console()
    for error in service.import_errors:
        console.print(error, markup=False)
    for draft in service.store.list_all():
        console.print(
            f"{draft.id}  revision {draft.revision}  {draft.title or '(untitled)'}", markup=False
        )


if __name__ == "__main__":
    app()
