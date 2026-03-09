"""synthshop list — Show all products with status and links."""

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from synthshop.core.models import ProductStatus
from synthshop.core.product_store import ProductStore

console = Console()

STATUS_COLORS = {
    ProductStatus.DRAFT: "yellow",
    ProductStatus.LISTED: "green",
    ProductStatus.SOLD: "blue",
    ProductStatus.UNLISTED: "dim",
}


def list_products(
    status: Annotated[
        ProductStatus | None,
        typer.Option("--status", "-s", help="Filter by status."),
    ] = None,
) -> None:
    """List all products with status and links."""
    store = ProductStore()

    if status:
        products = store.list_by_status(status)
    else:
        products = store.list_all()

    if not products:
        console.print("[dim]No products found.[/dim]")
        return

    table = Table(title=f"Products ({len(products)})")
    table.add_column("ID", style="dim", width=12)
    table.add_column("Title", min_width=25)
    table.add_column("Price", justify="right")
    table.add_column("Status")
    table.add_column("Reverb")

    for p in products:
        color = STATUS_COLORS.get(p.status, "white")
        reverb_info = ""
        if p.reverb:
            reverb_info = p.reverb.url or f"#{p.reverb.listing_id}"

        table.add_row(
            p.id,
            p.title,
            f"${p.price:,.2f}",
            f"[{color}]{p.status.value}[/{color}]",
            reverb_info,
        )

    console.print(table)
