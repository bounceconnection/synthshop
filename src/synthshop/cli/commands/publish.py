"""synthshop publish — Full listing workflow: identify → upload → list on Reverb."""

from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console

from synthshop.cli.commands.identify import identify
from synthshop.core.models import Condition, PriceRange, Product, ProductStatus
from synthshop.core.product_store import ProductStore
from synthshop.integrations.claude_vision import SynthIdentification
from synthshop.integrations.reverb import ReverbClient

console = Console()


def publish(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    photos: Annotated[
        list[Path],
        typer.Argument(help="Image files of the item (JPEG, PNG, GIF, WebP)."),
    ],
    price: Annotated[
        float,
        typer.Option("--price", "-p", help="Asking price in USD."),
    ],
    make: Annotated[
        str | None,
        typer.Option("--make", help="Manufacturer (skip identification)."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Model name (skip identification)."),
    ] = None,
    condition: Annotated[
        Condition,
        typer.Option("--condition", "-c", help="Item condition."),
    ] = Condition.GOOD,
    shipping: Annotated[
        float,
        typer.Option("--shipping", "-s", help="Shipping price in USD."),
    ] = 0.0,
    live: Annotated[
        bool,
        typer.Option("--live", help="Publish immediately (default: draft)."),
    ] = False,
    skip_reverb: Annotated[
        bool,
        typer.Option("--skip-reverb", help="Save product locally without listing on Reverb."),
    ] = False,
) -> None:
    """Full listing workflow: identify synth, upload images, create Reverb listing."""
    # Step 1: Identify or use manual input
    if make and model:
        console.print(f"\n[bold]Using manual input: {make} {model}[/bold]\n")
        identification = None
    else:
        if make or model:
            console.print("[red]Both --make and --model are required to skip identification.[/red]")
            raise typer.Exit(1)

        identification = identify(photos)

        if not typer.confirm("Use this identification?"):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    # Step 2: Build product
    product = _build_product(
        identification=identification,
        make=make,
        model=model,
        price=price,
        condition=condition,
        shipping_price=shipping,
        local_image_paths=[str(p) for p in photos],
    )

    console.print(f"\n[bold]Product:[/bold] {product.title}")
    console.print(f"[bold]Price:[/bold] ${product.price:,.2f}")
    if product.price_range:
        console.print(
            f"[bold]Market range:[/bold] "
            f"${product.price_range.low:,.0f} – ${product.price_range.high:,.0f}"
        )

    # Step 3: Publish to Reverb
    if not skip_reverb:
        console.print(f"\n[bold]Creating Reverb listing ({'live' if live else 'draft'})...[/bold]")
        try:
            with ReverbClient() as client:
                reverb_listing = client.create_listing(product, live=live)
                product.reverb = reverb_listing

                # Upload images
                console.print(f"Uploading {len(photos)} image(s) to Reverb...")
                client.upload_images(reverb_listing.listing_id, photos)

                product.status = ProductStatus.LISTED if live else ProductStatus.DRAFT
        except (OSError, httpx.HTTPError) as e:
            console.print(f"[red]Reverb error: {e}[/red]")
            console.print("[yellow]Saving product locally without Reverb listing.[/yellow]")

    # Step 4: Save product
    store = ProductStore()
    path = store.save(product)

    console.print(f"\n[green bold]Saved:[/green bold] {path}")
    if product.reverb:
        console.print(f"[green bold]Reverb:[/green bold] {product.reverb.url}")
        if not live:
            console.print("[dim]Listing is in draft — go to Reverb to review and publish.[/dim]")


def _build_product(  # pylint: disable=too-many-arguments
    *,
    identification: SynthIdentification | None,
    make: str | None,
    model: str | None,
    price: float,
    condition: Condition,
    shipping_price: float,
    local_image_paths: list[str],
) -> Product:
    """Build a Product from identification results or manual input."""
    if identification:
        # Map the string condition from Claude to our Condition enum
        try:
            mapped_condition = Condition(identification.condition)
        except ValueError:
            mapped_condition = condition

        return Product(
            make=identification.make,
            model=identification.model,
            year=identification.year,
            variant=identification.variant,
            category=identification.category,
            description=identification.description,
            features=identification.features,
            condition=mapped_condition,
            condition_notes=identification.condition_notes,
            price=price,
            price_range=PriceRange(
                low=identification.price_low,
                high=identification.price_high,
            ),
            shipping_price=shipping_price,
            local_image_paths=local_image_paths,
        )

    return Product(
        make=make,  # type: ignore[arg-type]
        model=model,  # type: ignore[arg-type]
        condition=condition,
        price=price,
        shipping_price=shipping_price,
        local_image_paths=local_image_paths,
    )
