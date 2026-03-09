"""synthshop identify — Identify a synth from photos using Claude Vision."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table

from synthshop.integrations.claude_vision import SynthIdentification, identify_from_photos
from synthshop.integrations.modulargrid import search_modulargrid
from synthshop.integrations.reverb import ReverbClient

console = Console()

# Categories that suggest a eurorack module
EURORACK_CATEGORIES = {"synthesizers", "effects", "drum-machines", "samplers", "studio-gear"}


def identify(
    photos: Annotated[
        list[Path],
        typer.Argument(help="Image files to identify (JPEG, PNG, GIF, WebP)."),
    ],
    model: Annotated[
        str,
        typer.Option("--model", "-m", help="Claude model to use."),
    ] = "claude-sonnet-4-20250514",
    no_modulargrid: Annotated[
        bool,
        typer.Option("--no-modulargrid", help="Skip ModularGrid verification."),
    ] = False,
) -> SynthIdentification | None:
    """Identify a synth from photos using Claude Vision.

    Returns the identification result, or None if the user rejects it.
    """
    # Validate files exist
    for photo in photos:
        if not photo.exists():
            console.print(f"[red]File not found: {photo}[/red]")
            raise typer.Exit(1)

    console.print()
    try:
        with Status(
            f"Analyzing {len(photos)} photo(s) with Claude Vision...",
            console=console,
            spinner="dots",
        ):
            result = identify_from_photos(photos, model=model)
    except Exception as e:
        console.print(f"[red]Identification failed: {e}[/red]")
        raise typer.Exit(1)
    console.print()

    # Cross-reference with ModularGrid for eurorack modules
    if not no_modulargrid and result.category in EURORACK_CATEGORIES:
        result = _verify_with_modulargrid(result)

    # Look up real market pricing on Reverb
    result = _check_reverb_pricing(result)

    _display_result(result)
    return result


def _verify_with_modulargrid(result: SynthIdentification) -> SynthIdentification:
    """Search ModularGrid to verify/correct the identification.

    Claude Vision often misidentifies niche eurorack manufacturers, and sometimes
    swaps make/model. We try multiple search strategies to find the right module.
    """
    # Try model name first, then make name (Claude sometimes swaps them)
    search_terms = [result.model]
    if result.make.lower() != result.model.lower():
        search_terms.append(result.make)

    mg = None
    with Status("Checking ModularGrid...", console=console, spinner="dots") as status:
        for term in search_terms:
            mg = search_modulargrid(
                term,
                make_hint=result.make,
                on_progress=lambda msg: status.update(f"ModularGrid: {msg}"),
            )
            if mg:
                break

    if not mg:
        console.print("[dim]No ModularGrid match found. Verify manufacturer manually.[/dim]\n")
        return result

    mg_manufacturer = mg["manufacturer"]
    mg_model = mg["model"]
    mg_url = mg["url"]

    # Check if Claude got the make or model wrong
    make_wrong = mg_manufacturer.lower() != result.make.lower()
    model_wrong = mg_model.lower() != result.model.lower()

    old_make = result.make
    old_model = result.model

    if make_wrong or model_wrong:
        console.print(
            f"[yellow]ModularGrid correction:[/yellow] "
            f"[bold]{mg_manufacturer} {mg_model}[/bold]"
        )
        if make_wrong:
            console.print(f"  Manufacturer: {old_make} → {mg_manufacturer}")
        if model_wrong:
            console.print(f"  Model: {old_model} → {mg_model}")
        console.print(f"[dim]Source: {mg_url}[/dim]\n")

        result.make = mg_manufacturer
        result.model = mg_model
    else:
        console.print(
            f"[green]ModularGrid confirmed: {mg_manufacturer} {mg_model}[/green]\n"
        )

    # Replace Claude's description with ModularGrid's authoritative one
    if mg.get("description"):
        result.description = mg["description"]
    elif make_wrong:
        # Fallback: fix manufacturer name in Claude's description
        result.description = result.description.replace(old_make, mg_manufacturer)

    # Build features list from ModularGrid data
    mg_features: list[str] = []
    if mg.get("subtitle"):
        mg_features.append(mg["subtitle"])
    if mg.get("hp"):
        mg_features.append(f"{mg['hp']}HP Eurorack module")
    # Add the actual feature list from the ModularGrid page
    if mg.get("features"):
        mg_features.extend(mg["features"])
    if mg.get("discontinued"):
        mg_features.append("Discontinued — increasingly rare")

    if mg_features:
        result.features = mg_features

    # Fix wrong manufacturer name in notes
    if make_wrong and result.notes:
        result.notes = result.notes.replace(old_make, mg_manufacturer)

    # Add discontinued to notes
    if mg.get("discontinued") and "discontinued" not in result.notes.lower():
        result.notes = f"Discontinued. {result.notes}".strip()

    return result


def _check_reverb_pricing(result: SynthIdentification) -> SynthIdentification:
    """Look up real market pricing on Reverb to replace Claude's estimate."""
    try:
        with Status("Checking Reverb pricing...", console=console, spinner="dots"):
            with ReverbClient() as client:
                query = f"{result.make} {result.model}"
                price_data = client.get_price_guide(query)
    except Exception:
        console.print("[dim]Could not fetch Reverb pricing.[/dim]\n")
        return result

    if not price_data:
        console.print("[dim]No Reverb listings found for pricing.[/dim]\n")
        return result

    old_range = f"${result.price_low:,.0f}–${result.price_high:,.0f}"
    result.price_low = price_data["low"]
    result.price_high = price_data["high"]
    new_range = f"${result.price_low:,.0f}–${result.price_high:,.0f}"

    console.print(
        f"[green]Reverb pricing:[/green] {new_range} "
        f"(from {price_data['count']} active listings, "
        f"median ${price_data['median']:,.0f})"
    )
    if old_range != new_range:
        console.print(f"[dim]Claude estimated {old_range}[/dim]\n")
    else:
        console.print()

    return result


def _display_result(result: SynthIdentification) -> None:
    """Display identification results in a rich formatted panel."""
    # Confidence color
    conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(
        result.confidence, "white"
    )

    # Header
    title = f"{result.make} {result.model}"
    if result.variant:
        title += f" {result.variant}"
    if result.year:
        title += f" ({result.year})"

    # Details table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan", width=14)
    table.add_column("Value")

    table.add_row("Make", result.make)
    table.add_row("Model", result.model)
    if result.year:
        table.add_row("Year", str(result.year))
    if result.variant:
        table.add_row("Variant", result.variant)
    table.add_row("Category", result.category)
    table.add_row("Condition", result.condition)
    if result.condition_notes:
        table.add_row("", f"[dim]{result.condition_notes}[/dim]")
    table.add_row("Price Range", f"${result.price_low:,.0f} – ${result.price_high:,.0f}")
    table.add_row(
        "Confidence",
        f"[{conf_color}]{result.confidence}[/{conf_color}]",
    )

    console.print(Panel(table, title=f"[bold]{title}[/bold]", border_style="blue"))

    # Features
    if result.features:
        console.print("\n[bold]Features:[/bold]")
        for feat in result.features:
            console.print(f"  • {feat}")

    # Description
    console.print(f"\n[bold]Description:[/bold]\n{result.description}")

    # Notes
    if result.notes:
        console.print(f"\n[bold yellow]Notes:[/bold yellow] {result.notes}")

    console.print()
