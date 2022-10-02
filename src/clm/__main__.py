import click


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        say_hi()


@cli.command()
@click.option("--name", help="The name of the person to greet.", default="world")
def say_hi(name="world"):
    click.echo(f"Hello, {name}!")


if __name__ == "__main__":
    cli()
