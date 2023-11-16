import os
import shutil
import subprocess
import sys
import time
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

import click
import tomli_w

import clm.data_sources  # type: ignore

# These imports are needed to get the corresponding plugins registered.
import clm.specs.course_layouts  # type: ignore
from clm import __version__
from clm.cli.notifier_manager import NotifierManager
from clm.core.course import Course
from clm.core.course_layout import course_layout_from_dict, course_layout_to_dict
from clm.core.output_spec import (
    create_default_output_specs,
)
from clm.specs.course_spec_factory import (
    create_course_spec_file,
    update_course_spec_file,
)
from clm.specs.course_spec_readers import CourseSpecCsvReader
from clm.specs.course_spec_writers import CourseSpecCsvWriter
from clm.utils import config
from clm.utils.config import config_to_python
from clm.utils.executor import create_executor
from clm.utils.jupyterlite_utils import (
    copy_files_to_jupyterlite_repo,
    jupyterlite_dir,
    jupyterlite_git_dir,
)
from clm.utils.location import FileSystemLocation, Location
from clm.utils.path_utils import zip_directory

ALIASES = {}


class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        try:
            cmd_name = ALIASES[cmd_name].name
        except KeyError:
            pass
        return super().get_command(ctx, cmd_name)


def build_alias_help_text():
    alias_len = max(len(alias) for alias in ALIASES)
    help_text = "Aliases:"
    for alias, command in ALIASES.items():
        help_text += f"\n  {alias:<{alias_len}} -> {command.name}"
    return help_text


@click.group(invoke_without_command=True, cls=AliasedGroup)
@click.pass_context
@click.option("--version", help="Show the version and exit.", is_flag=True)
def cli(ctx, version):
    """The Coding Academy Lecture Manager."""
    if ctx.invoked_subcommand is None:
        if version:
            click.echo(f"clm version {__version__}")
        else:
            show_help_text(ctx)
        ctx.exit()


def show_help_text(ctx):
    click.echo(ctx.get_help())
    click.echo()
    click.echo(note_aliases_available())


def note_aliases_available():
    program_name = os.path.basename(sys.argv[0])
    return f"Invoke '{program_name} show-aliases' for available aliases."


@cli.command()
def show_aliases():
    """Show the available aliases."""
    click.echo(build_alias_help_text())


ALIASES["sa"] = show_aliases


@cli.command()
def show_config():
    """Show location and contents of the config file."""
    click.echo(f"User config file: {config.user_config_file}")
    click.echo()
    toml_str = tomli_w.dumps(config_to_python(config.config))
    click.echo(toml_str)


ALIASES["sc"] = show_config


@cli.command()
@click.argument("command_name", required=False)
@click.pass_context
def show_help(ctx, command_name):
    """Show the help text (same as --help option)."""
    parent_ctx = ctx.parent
    if command_name:
        command = cli.get_command(parent_ctx, command_name)
        if command:
            # Create a new context for the command with the help flag
            cmd_ctx = click.Context(command, info_name=command_name, parent=parent_ctx)
            cmd_ctx.params["help"] = True
            click.echo(cmd_ctx.get_help())
            ctx.exit()
        else:
            click.echo(f"Command '{command_name}' not found.")
    else:
        show_help_text(parent_ctx)


ALIASES["help"] = show_help
ALIASES["sh"] = show_help


@cli.command()
@click.option(
    "--notebook-regex/-no-notebook-regex",
    help="Show the regex for notebook files.",
    type=bool,
    default=False,
)
def show_course_layouts(notebook_regex: bool):
    """Show the available course layouts."""
    click.echo("Available course layouts:")
    for layout_dict in config.config.course_layouts:
        # Create a layout from the config so that we get the defaults.
        # Convert back to a dictionary to convert all values to strings.
        layout = course_layout_to_dict(course_layout_from_dict(layout_dict))
        max_pattern_len = max(len(p[0]) for p in layout["directory_patterns"])
        click.echo(f"  {layout['name']}:")
        click.echo(f"    default directory kind: {layout['default_directory_kind']}")
        click.echo(f"    directory patterns:")
        for pattern, directory_kind in layout["directory_patterns"]:
            click.echo(f"      {pattern:<{max_pattern_len}} -> {directory_kind}")
        if notebook_regex:
            click.echo(f"    notebook regex: {layout['notebook_regex']}")
    click.echo("Done.")


ALIASES["scl"] = show_course_layouts


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
    """Create a spec file from the course sources."""
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


ALIASES["create-spec"] = create_spec_file
ALIASES["csf"] = create_spec_file
ALIASES["cs"] = create_spec_file


@cli.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=True, resolve_path=True, allow_dash=True),
)
def update_spec_file(spec_file: str):
    """Update the spec file from the course sources."""
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


ALIASES["update-spec"] = update_spec_file
ALIASES["usf"] = update_spec_file
ALIASES["us"] = update_spec_file


def make_pretty_path(path: Path | Location):
    try:
        if isinstance(path, Location):
            path = path.absolute()
        pretty_path = path.relative_to(os.getcwd())
    except ValueError:
        pretty_path = path
    return pretty_path


def build_course_options(f):
    f = click.argument("spec-file", type=click.Path(exists=True, resolve_path=True))(f)
    f = click.option("--lang", default="", help="The language to generate.")(f)
    f = click.option(
        "-v", "--verbose/--no-verbose", default=False, help="Verbose output."
    )(f)
    f = click.option(
        "--remove/--no-remove",
        help="Should the old directory be removed?",
        default=True,
        type=bool,
    )(f)
    f = click.option(
        "--html/--no-html",
        help="Should HTML output be generated?",
        default=False,
        type=bool,
    )(f)
    f = click.option(
        "--jupyterlite",
        help="Should a Jupyterlite repository be created?",
        is_flag=True,
        default=False,
        type=bool,
    )(f)
    f = click.option("--log", help="The log level.", default="warning", type=str)(f)
    f = click.option(
        "--single-threaded",
        help="Run file-processing in a single thread.",
        is_flag=True,
        default=False,
        type=bool,
    )(f)
    f = click.option(
        "--zip-single-threaded",
        help="Run zip-file creation in a single thread",
        is_flag=True,
        default=False,
        type=bool,
    )(f)
    return f


def common_build_course(
    spec_file,
    lang,
    verbose,
    remove,
    html,
    jupyterlite,
    log,
    single_threaded,
    zip_single_threaded,
):
    import logging

    logging.basicConfig(level=log.upper())
    course_spec = CourseSpecCsvReader.read_csv(spec_file, FileSystemLocation)
    prog_lang = course_spec.prog_lang

    manager = NotifierManager()
    manager.start()
    # noinspection PyUnresolvedReferences
    notifier = manager.ClickNotifier(verbose=verbose)

    with TemporaryDirectory() as tmp_dir:
        try:
            if not lang:
                lang = course_spec.lang
            if remove:
                maybe_save_jupyterlite_git_dir(course_spec, notifier, tmp_dir)

                click.echo(
                    f"Removing target dir '{course_spec.target_loc}'...", nl=False
                )
                shutil.rmtree(course_spec.target_loc.absolute(), ignore_errors=True)
                click.echo("done.")
            click.echo("Generating course")
            click.echo(f"  lang: {course_spec.lang}")
            click.echo(f"  prog: {prog_lang}")
            click.echo(f"   dir: {course_spec.target_loc}")
            course = Course.from_spec(course_spec)
            click.echo(f"Course has {len(course.data_sources)} data_sources.")

            start_time = time.time()
            output_specs = create_default_output_specs(
                lang, prog_lang=prog_lang, add_html=html
            )
            with create_executor(single_threaded=single_threaded) as executor:
                for output_spec in output_specs:
                    for future in course.process_for_output_spec(
                        executor, output_spec, notifier
                    ):
                        future.add_done_callback(
                            lambda f: notifier.completed_processing()
                        )

            if jupyterlite:
                click.echo("\nCopying Jupyterlab files.", nl=False)
                copy_files_to_jupyterlite_repo(course_spec)
        finally:
            if remove:
                maybe_restore_jupyterlite_git_dir(course_spec, notifier, tmp_dir)

    click.echo(f"\nCourse generated in {time.time() - start_time:.2f} seconds.")

    click.echo(f"Generating zips.")
    with create_executor(single_threaded=zip_single_threaded) as executor:
        executor.map(
            partial(zip_directory, course_spec.target_loc),
            ["public", "private"],
        )

    click.echo("Done.")


def maybe_save_jupyterlite_git_dir(course_spec, notifier, tmp_dir):
    if jupyterlite_git_dir(course_spec).exists():
        notifier.message("Saving Jupyterlite git directory...")
        shutil.move(jupyterlite_git_dir(course_spec), Path(tmp_dir) / "jupyterlite-git")
        notifier.newline("done.")


def maybe_restore_jupyterlite_git_dir(course_spec, notifier, tmp_dir):
    if (Path(tmp_dir) / "jupyterlite-git").exists():
        notifier.newline()
        notifier.message("Restoring Jupyterlite git directory...")
        jupyterlite_dir(course_spec).mkdir(exist_ok=True, parents=True)
        shutil.move(
            Path(tmp_dir) / "jupyterlite-git",
            jupyterlite_dir(course_spec) / ".git",
        )
        notifier.newline("done.")


# Build course command
@cli.command()
@build_course_options
def build_course(
    spec_file,
    lang,
    verbose,
    remove,
    html,
    jupyterlite,
    log,
    single_threaded,
    zip_single_threaded,
):
    """Build a course from a spec file."""
    common_build_course(
        spec_file,
        lang,
        verbose,
        remove,
        html,
        jupyterlite,
        log,
        single_threaded,
        zip_single_threaded,
    )


ALIASES["build"] = build_course
ALIASES["cc"] = build_course
ALIASES["bc"] = build_course


# Create course command (deprecated)
@cli.command()
@build_course_options
def zdeprecated_create_course(
    spec_file,
    lang,
    verbose,
    remove,
    html,
    jupyterlite,
    log,
    single_threaded,
    zip_single_threaded,
):
    """DEPRECATED: use build-course instead."""
    click.echo(
        "Warning: 'create-course' is deprecated, please use 'build-course' instead.",
        err=True,
    )
    common_build_course(
        spec_file,
        lang,
        verbose,
        remove,
        html,
        jupyterlite,
        log,
        single_threaded,
        zip_single_threaded,
    )


ALIASES["create-course"] = zdeprecated_create_course


@cli.command()
@click.argument("spec-file", type=click.Path(exists=True, resolve_path=True))
@click.option(
    "--owner", help="The owner of the repository.", default="hoelzl", type=str
)
def create_jupyterlite_repo(spec_file: Path, owner: str):
    """Create a Jupyterlite repository for the course."""
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
