import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from clm.core.course import Course
from clm.core.course_specs import (
    CourseSpec,
    create_course_spec_file,
    update_course_spec_file,
)
from clm.core.output_spec import create_default_output_specs

import click


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit()


@cli.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=False, resolve_path=True, allow_dash=True),
)
@click.argument(
    "course_dir",
    type=click.Path(
        exists=True, resolve_path=True, dir_okay=True, file_okay=False, allow_dash=True
    ),
)
@click.argument(
    "target_dir",
    type=click.Path(
        exists=False, resolve_path=True, dir_okay=True, file_okay=False, allow_dash=True
    ),
)
@click.option(
    "--remove", help="Should the old spec file be removed?.", default=False, type=bool
)
def create_spec_file(spec_file: str, course_dir: str, target_dir: str, remove: bool):
    spec_file_path = Path(spec_file)
    course_dir_path = Path(course_dir)
    target_dir_path = Path(target_dir)
    relative_path = spec_file_path.relative_to(os.getcwd())
    try:
        create_course_spec_file(
            spec_file_path,
            course_dir_path,
            target_dir_path,
            remove_existing=remove,
        )
        click.echo(f"Created spec file '{relative_path}'.")
    except FileExistsError:
        click.echo(
            f"File '{relative_path}' already exists. "
            "Use --remove=true option to delete."
        )


@cli.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=True, resolve_path=True, allow_dash=True),
)
def update_spec_file(spec_file: str):
    spec_file_path = Path(spec_file)
    relative_path = spec_file_path.relative_to(os.getcwd())
    try:
        new_spec, deleted_doc_specs = update_course_spec_file(spec_file_path)
        if deleted_doc_specs:
            click.echo(f"Deleted {len(deleted_doc_specs)} specs:")
            for spec in deleted_doc_specs:
                click.echo(f"  {spec.source_file}")
        spec_file_path.unlink()
        new_spec.to_csv(spec_file_path)
        click.echo(f"Updated spec file '{relative_path}'.")
    except FileNotFoundError:
        click.echo(f"File '{relative_path}' does not exist. ")


@cli.command()
@click.argument("spec-file", type=click.Path(exists=True, resolve_path=True))
@click.option("--lang", help="The language to generate.", default="", type=str)
@click.option(
    "--remove", help="Should the old directory be removed?.", default=True, type=bool
)
def create_course(spec_file, lang, remove):
    course_spec = CourseSpec.read_csv(spec_file)
    if not lang:
        lang = course_spec.lang
    if remove:
        click.echo(f"Removing target dir '{course_spec.target_dir}'...", nl=False)
        shutil.rmtree(course_spec.target_dir, ignore_errors=True)
        click.echo("done.")
    click.echo("Generating course")
    click.echo(f"  lang: {course_spec.lang}")
    click.echo(f"  dir:  {course_spec.target_dir}")
    course = Course.from_spec(course_spec)
    output_specs = create_default_output_specs(lang)
    executor = ProcessPoolExecutor(max_workers=8)
    for output_kind in output_specs:
        executor.submit(course.process_for_output_spec, output_kind)
    executor.shutdown(wait=True)
    click.echo("Done.")


if __name__ == "__main__":
    cli()
