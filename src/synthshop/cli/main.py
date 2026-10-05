"""Typer launcher; publication exists only in the reviewed local UI."""

import webbrowser
from pathlib import Path
from threading import Timer
from typing import Annotated

import typer
import uvicorn
from rich.console import Console

from synthshop.core.config import Settings
from synthshop.web.app import create_app

app = typer.Typer(
    name="synthshop",
    help="Local photo-to-Reverb drafting and explicit review.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Local photo-to-Reverb drafting and explicit review."""


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
    application = create_app(settings, port=port)
    url = f"http://127.0.0.1:{port}/unlock?key={application.state.unlock}"
    Console().print(
        f"SynthShop local review (one-time unlock link): {url}", markup=False, soft_wrap=True
    )
    if open_browser:
        timer = Timer(1.0, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    uvicorn.run(application, host="127.0.0.1", port=port, access_log=False)


if __name__ == "__main__":
    app()
