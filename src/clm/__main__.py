import shutil
from concurrent.futures import ProcessPoolExecutor

from clm.core.course import Course
from clm.core.course_specs import CourseSpec
from clm.core.output_spec import CompletedOutput, CodeAlongOutput, SpeakerOutput

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


def get_output_specs(lang):
    match lang:
        case "de":
            return [
                CompletedOutput("de", "public/Folien"),
                CodeAlongOutput("de", "public/CodeAlong"),
                SpeakerOutput("de", "private/Speaker"),
            ]
        case "en":
            return [
                CompletedOutput("en", "public/Slides"),
                CodeAlongOutput("en", "public/CodeAlong"),
                SpeakerOutput("en", "private/Speaker"),
            ]
        case _:
            raise ValueError(f"Bad language: {lang}")


@cli.command()
@click.option("--lang", help="The language to generate.", default="en", type=str)
@click.option(
    "--remove", help="Should the old directory be removed?.", default=True, type=bool
)
@click.argument("spec-file", type=click.Path(exists=True))
def create_course(spec_file, lang="en", remove=True):
    course_spec = CourseSpec.read_csv(spec_file)
    if remove:
        click.echo(f"Removing target dir '{course_spec.target_dir}'...", nl=False)
        shutil.rmtree(course_spec.target_dir)
        click.echo("done.")
    click.echo("Generating course...", nl=False)
    course = Course.from_spec(course_spec)
    output_specs = get_output_specs(lang)
    executor = ProcessPoolExecutor(max_workers=8)
    for output_kind in output_specs:
        executor.submit(course.process_for_output_spec, output_kind)
    executor.shutdown(wait=True)
    click.echo("done.")


if __name__ == "__main__":
    cli()
