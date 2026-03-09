"""synthshop unpublish / sold — Change product status and update Reverb."""

from typing import Annotated

import httpx
import typer
from rich.console import Console

from synthshop.core.models import ProductStatus
from synthshop.core.product_store import ProductStore
from synthshop.integrations.reverb import ReverbClient

console = Console()


def unpublish(
    product_id: Annotated[str, typer.Argument(help="Product ID to unpublish.")],
) -> None:
    """Remove a listing from Reverb and mark as unlisted."""
    store = ProductStore()

    try:
        product = store.load(product_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Product not found: {product_id}[/red]")
        raise typer.Exit(1) from exc

    if product.reverb:
        console.print(f"Ending Reverb listing #{product.reverb.listing_id}...")
        try:
            with ReverbClient() as client:
                client.end_listing(product.reverb.listing_id)
            console.print("[green]Reverb listing ended.[/green]")
        except (OSError, httpx.HTTPError) as e:
            console.print(f"[red]Reverb error: {e}[/red]")
            console.print("[yellow]Updating local status anyway.[/yellow]")
    else:
        console.print("[dim]No Reverb listing to end.[/dim]")

    product.status = ProductStatus.UNLISTED
    store.save(product)
    console.print(f"[green]Product {product_id} marked as unlisted.[/green]")


def sold(
    product_id: Annotated[str, typer.Argument(help="Product ID to mark as sold.")],
) -> None:
    """Mark a product as sold."""
    store = ProductStore()

    try:
        product = store.load(product_id)
    except FileNotFoundError as exc:
        console.print(f"[red]Product not found: {product_id}[/red]")
        raise typer.Exit(1) from exc

    if product.reverb and product.status == ProductStatus.LISTED:
        console.print(f"Ending Reverb listing #{product.reverb.listing_id}...")
        try:
            with ReverbClient() as client:
                client.end_listing(product.reverb.listing_id)
            console.print("[green]Reverb listing ended.[/green]")
        except (OSError, httpx.HTTPError) as e:
            console.print(f"[red]Reverb error: {e}[/red]")

    product.status = ProductStatus.SOLD
    store.save(product)
    console.print(f"[green]Product {product_id} marked as sold.[/green]")
