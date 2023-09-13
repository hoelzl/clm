import os
import shutil
import subprocess
import time
from functools import partial
from pathlib import Path

import click

# These imports are needed to get the corresponding plugins registered.
import clm.specs.course_layouts  # type: ignore
import clm.data_sources  # type: ignore
from clm.cli.notifier_manager import NotifierManager
from clm.core.course import Course
from clm.core.output_spec import (
    create_default_output_specs,
)
from clm.specs.course_spec_factory import (
    create_course_spec_file,
    update_course_spec_file,
)
from clm.specs.course_spec_readers import CourseSpecCsvReader
from clm.specs.course_spec_writers import CourseSpecCsvWriter
from clm.utils.executor import create_executor
from clm.utils.location import FileSystemLocation, Location
from clm.utils.path_utils import zip_directory


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit()


@cli.command()
def show_config():
    from clm.utils import config

    click.echo(f"User config file: {config.user_config_file}")
    click.echo()
    for key, value in config.config.items():
        click.echo(f"{key}: {value!r}")
    click.echo("Done.")


@cli.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=False, resolve_path=True, allow_dash=True),
)
@click.argument(
    "course_dir",
    type=click.Path(
        exists=True,
        resolve_path=True,
        dir_okay=True,
        file_okay=False,
        allow_dash=True,
    ),
)
@click.argument(
    "target_dir",
    type=click.Path(
        exists=False,
        resolve_path=True,
        dir_okay=True,
        file_okay=False,
        allow_dash=True,
    ),
)
@click.option("--lang", help="The language of the course", type=str, default="en")
@click.option(
    "--prog-lang",
    help="The programming language of the course",
    type=str,
    default="python",
)
@click.option("--layout", help="The course layout", type=str, default="legacy_python")
@click.option(
    "--remove/--no-remove",
    help="Should the old spec file be removed?",
    default=False,
    type=bool,
)
@click.option(
    "--starting-spec",
    help="Take initial data-source specs from this file.",
    type=click.Path(exists=True, resolve_path=True, dir_okay=False, file_okay=True),
)
def create_spec_file(
    spec_file: str,
    course_dir: str,
    target_dir: str,
    lang: str,
    prog_lang: str,
    layout: str,
    remove: bool,
    starting_spec: str,
):
    spec_file_path = Path(spec_file)
    course_dir_path = Path(course_dir)
    target_dir_path = Path(target_dir)
    starting_spec_path = Path(starting_spec) if starting_spec else None
    pretty_path = make_pretty_path(spec_file_path)
    try:
        create_course_spec_file(
            spec_file_path,
            course_dir_path,
            target_dir_path,
            lang=lang,
            prog_lang=prog_lang,
            course_layout=layout,
            remove_existing=remove,
            starting_spec_file=starting_spec_path,
        )
        click.echo(f"Created spec file '{pretty_path}'.")
    except FileExistsError:
        click.echo(
            f"File '{pretty_path}' already exists. " "Use --remove option to delete."
        )


@cli.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=True, resolve_path=True, allow_dash=True),
)
def update_spec_file(spec_file: str):
    spec_file_path = Path(spec_file)
    pretty_path = make_pretty_path(spec_file_path)
    try:
        new_spec, deleted_doc_specs = update_course_spec_file(spec_file_path)
        if deleted_doc_specs:
            click.echo(f"Deleted {len(deleted_doc_specs)} specs:")
            for spec in deleted_doc_specs:
                click.echo(f"  {spec.source_loc}")
        spec_file_path.unlink()
        CourseSpecCsvWriter.to_csv(new_spec, spec_file_path)
        click.echo(f"Updated spec file '{pretty_path}'.")
    except FileNotFoundError:
        click.echo(f"File '{pretty_path}' does not exist. ")


def make_pretty_path(path: Path | Location):
    try:
        if isinstance(path, Location):
            path = path.absolute()
        pretty_path = path.relative_to(os.getcwd())
    except ValueError:
        pretty_path = path
    return pretty_path


@cli.command()
@click.argument("spec-file", type=click.Path(exists=True, resolve_path=True))
@click.option("--lang", help="The language to generate.", default="", type=str)
@click.option(
    "-v", "--verbose/--no-verbose", help="Verbose output.", default=False, type=bool
)
@click.option(
    "--remove/--no-remove",
    help="Should the old directory be removed?",
    default=True,
    type=bool,
)
@click.option(
    "--html/--no-html",
    help="Should HTML output be generated?",
    default=False,
    type=bool,
)
@click.option(
    "--jupyterlite/--no-jupyterlite",
    help="Should a Jupyterlite repository be created?",
    default=False,
    type=bool,
)
@click.option("--log", help="The log level.", default="warning", type=str)
def create_course(spec_file, lang, verbose, remove, html, jupyterlite, log):
    import logging

    logging.basicConfig(level=log.upper())
    course_spec = CourseSpecCsvReader.read_csv(spec_file, FileSystemLocation)
    prog_lang = course_spec.prog_lang
    if not lang:
        lang = course_spec.lang
    if remove:
        click.echo(f"Removing target dir '{course_spec.target_loc}'...", nl=False)
        shutil.rmtree(course_spec.target_loc.absolute(), ignore_errors=True)
        click.echo("done.")
    click.echo("Generating course")
    click.echo(f"  lang: {course_spec.lang}")
    click.echo(f"  prog: {prog_lang}")
    click.echo(f"   dir: {course_spec.target_loc}")
    course = Course.from_spec(course_spec)
    click.echo(f"Course has {len(course.data_sources)} data_sources.")

    start_time = time.time()
    output_specs = create_default_output_specs(lang, prog_lang=prog_lang, add_html=html)
    manager = NotifierManager()
    manager.start()
    # noinspection PyUnresolvedReferences
    notifier = manager.ClickNotifier(verbose=verbose)
    with create_executor() as executor:
        for output_spec in output_specs:
            for future in course.process_for_output_spec(
                executor, output_spec, notifier
            ):
                future.add_done_callback(lambda f: notifier.completed_processing())

    if jupyterlite:
        click.echo("\nCopying Jupyterlab files.", nl=False)
        course_spec.target_loc.mkdir(exist_ok=True, parents=True)
        shutil.copytree(
            course_spec.source_loc.absolute() / "metadata/jupyterlite",
            course_spec.target_loc.absolute() / "jupyterlite",
            dirs_exist_ok=True,
        )
        shutil.copytree(
            course_spec.target_loc.absolute() / "public/Notebooks",
            course_spec.target_loc.absolute() / "jupyterlite/content/Notebooks",
            dirs_exist_ok=True,
        )
        if (course_spec.target_loc / "public/examples").exists():
            shutil.copytree(
                course_spec.target_loc.absolute() / "public/examples",
                course_spec.target_loc.absolute() / "jupyterlite/content/examples",
                dirs_exist_ok=True,
            )

    click.echo(f"\nCourse generated in {time.time() - start_time:.2f} seconds.")

    click.echo(f"Generating zips.")
    with create_executor() as executor:
        executor.map(
            partial(zip_directory, course_spec.target_loc),
            ["public", "private"],
        )

    click.echo("Done.")


@cli.command()
@click.argument("spec-file", type=click.Path(exists=True, resolve_path=True))
@click.option(
    "--owner", help="The owner of the repository.", default="hoelzl", type=str
)
def create_jupyterlite_repo(spec_file: Path, owner: str):
    course_spec = CourseSpecCsvReader.read_csv(spec_file, FileSystemLocation)
    jupyterlite_dir = course_spec.target_loc / "jupyterlite"
    # timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    # repo_name = f"{course_spec.target_dir.name}-{timestamp}"
    repo_name = f"{course_spec.target_loc.name}"

    os.chdir(jupyterlite_dir.absolute())
    click.echo(f"Initializing repo {os.getcwd()}.")
    cp = subprocess.run(["git", "init"])
    if cp.returncode != 0:
        click.echo("Could not init git repository. Exiting.")
        return cp.returncode
    subprocess.run(["git", "add", "-A"])
    subprocess.run(["git", "commit", "-m", "Initial version"])
    click.echo(f"Creating git directory {repo_name}.")
    try:
        subprocess.run(["gh", "repo", "create", repo_name, "--public", "--source", "."])
    except Exception:  # noqa
        click.echo("Repository creation failed. Continuing.")
    click.echo("Pushing to GitHub.")
    subprocess.run(["git", "push", "-u", "origin", "master"])
    github_endpoint = f"repos/{owner}/{repo_name}/pages"
    click.echo(f"Configuring pages for {github_endpoint},")
    subprocess.run(
        [
            "gh",
            "api",
            github_endpoint,
            "--method",
            "POST",
            "--field",
            "build_type=workflow",
        ]
    )
    click.echo("Enabling Build and Deploy workflow.")
    time.sleep(5)
    subprocess.run(["gh", "workflow", "enable", "deploy.yml"])
    subprocess.run(["gh", "browse"])
    click.echo("Done.")


if __name__ == "__main__":
    cli()
