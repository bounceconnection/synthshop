"""Typer launcher; publication exists only in the reviewed local UI."""

import getpass
import os
import sys
import warnings
import webbrowser
from pathlib import Path
from threading import Timer
from typing import Annotated

import typer
import uvicorn
from rich.console import Console

from synthshop.core.config import Settings
from synthshop.core.openai_setup import env_parts, read_env, save_openai_key
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
def setup_openai() -> None:
    """Save an OpenAI key with hidden input in this launch directory's .env."""
    path = Path.cwd() / ".env"
    typer.echo(f"OpenAI setup for {path}")
    typer.echo("Run here only if this is the directory you use for synthshop serve.")
    typer.echo("Get an API key at https://platform.openai.com/api-keys.")
    typer.echo("No API request is made. Never put the key in a command or browser form.")
    if not sys.stdin.isatty():
        typer.echo("Open a local interactive terminal to use hidden input. No changes made.")
        raise typer.Exit(1)
    if any(name.lower() == "openai_api_key" for name in os.environ):
        typer.echo(
            "OPENAI_API_KEY is set in this process environment and overrides .env. "
            "Remove that override in your launch terminal before setup. No changes made."
        )
        raise typer.Exit(1)
    try:
        previous = read_env(path)
        _, existing = env_parts(previous)
        if not typer.confirm("Save to this .env?", default=False):
            typer.echo("Cancelled. No changes made.")
            return
        if existing and not typer.confirm("Replace the existing OpenAI key?", default=False):
            typer.echo("Cancelled. Existing key kept.")
            return
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("OpenAI API key (hidden; Enter cancels): ").strip()
        if not key:
            typer.echo("Cancelled. No changes made.")
            return
        save_openai_key(path, previous, key)
    except (EOFError, KeyboardInterrupt):
        typer.echo("\nCancelled. No changes made.")
        raise typer.Exit(1) from None
    except ValueError as exc:
        typer.echo(f"Could not save safely. {exc}")
        raise typer.Exit(1) from None
    except (OSError, getpass.GetPassWarning):
        typer.echo(
            "Could not save safely. No key was saved by this command. "
            "Check that .env and its directory are writable, "
            "and use a terminal with hidden input."
        )
        raise typer.Exit(1) from None
    typer.echo(
        "Saved OpenAI key to .env with owner-only permissions. Authentication not tested.\n"
        "Restart synthshop serve from this same directory with your usual options; "
        "open its new unlock link. Vision should show Configured (presence only).\n"
        "Analyze sends photos to OpenAI and may incur API charges only when you choose it."
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
