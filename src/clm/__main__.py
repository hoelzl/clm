import shutil
from concurrent.futures import ProcessPoolExecutor

from clm.core.course import Course
from clm.core.course_specs import CourseSpec
from clm.core.output_spec import create_default_output_specs

import click


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        print_help(ctx)


def print_help(ctx):
    click.echo(ctx.get_help())
    ctx.exit()


@cli.command()
@click.option("--lang", help="The language to generate.", default="", type=str)
@click.option(
    "--remove", help="Should the old directory be removed?.", default=True, type=bool
)
@click.argument("spec-file", type=click.Path(exists=True))
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
