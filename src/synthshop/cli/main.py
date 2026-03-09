"""SynthShop CLI — Typer application entry point."""

import typer

from synthshop.cli.commands.identify import identify
from synthshop.cli.commands.list import list_products
from synthshop.cli.commands.publish import publish
from synthshop.cli.commands.unpublish import sold, unpublish

app = typer.Typer(
    name="synthshop",
    help="List synths on Reverb and your direct shop with one command.",
    no_args_is_help=True,
)

app.command()(identify)
app.command()(publish)
app.command(name="list")(list_products)
app.command()(unpublish)
app.command()(sold)

if __name__ == "__main__":
    app()
