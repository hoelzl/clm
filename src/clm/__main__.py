from pathlib import Path

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


def complete_path(_ctx, _param, incomplete):
    cwd = Path.cwd()
    return [
        f"{path.relative_to(cwd).as_posix().strip()}"
        for path in cwd.glob(f"{incomplete}*")
    ]


@cli.command()
@click.option("--lang", help="The language to generate.", default="en")
@click.argument("spec-file", shell_complete=complete_path)
def create_course(spec_file, lang="en"):
    click.echo("Generating course...", nl=False)
    course_spec = CourseSpec.read_csv(spec_file)
    course = Course.from_spec(course_spec)
    output_specs = get_output_specs(lang)
    for output_kind in output_specs:
        course.process_for_output_spec(output_kind)
    click.echo("done.")


if __name__ == "__main__":
    cli()
