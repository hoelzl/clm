"""The ``clm slides`` group — deck-level slide-authoring tools.

One module per subcommand: ``clm slides <cmd>`` lives in
``commands/slides/<cmd>.py`` (dashes become underscores).
"""

from __future__ import annotations

import click

from clm.cli.commands.shared import hidden_alias


@click.group("slides")
def slides_group() -> None:
    """Slide authoring: normalize, sync, search, language tools, etc."""


from clm.cli.commands.slides.assign_ids import assign_ids_cmd  # noqa: E402
from clm.cli.commands.slides.coverage import coverage_cmd  # noqa: E402
from clm.cli.commands.slides.coverage_report import coverage_report_cmd  # noqa: E402
from clm.cli.commands.slides.language_view import language_view_cmd  # noqa: E402
from clm.cli.commands.slides.normalize import normalize_slides_cmd  # noqa: E402
from clm.cli.commands.slides.reconcile_vo_ids import reconcile_vo_ids_cmd  # noqa: E402
from clm.cli.commands.slides.referenced_by import referenced_by_cmd  # noqa: E402
from clm.cli.commands.slides.rename_id import rename_id_cmd  # noqa: E402
from clm.cli.commands.slides.rules import authoring_rules_cmd  # noqa: E402
from clm.cli.commands.slides.search import search_slides_cmd  # noqa: E402
from clm.cli.commands.slides.slug_report import slug_report_cmd  # noqa: E402
from clm.cli.commands.slides.split import split_cmd  # noqa: E402
from clm.cli.commands.slides.suggest_sync import suggest_sync_cmd  # noqa: E402
from clm.cli.commands.slides.sync import slides_sync_group  # noqa: E402
from clm.cli.commands.slides.tidy import tidy_cmd  # noqa: E402
from clm.cli.commands.slides.translate import slides_translate_cmd  # noqa: E402
from clm.cli.commands.slides.unify import unify_cmd  # noqa: E402

slides_group.add_command(normalize_slides_cmd, name="normalize")
slides_group.add_command(assign_ids_cmd, name="assign-ids")
slides_group.add_command(coverage_cmd, name="coverage")
slides_group.add_command(split_cmd, name="split")
slides_group.add_command(unify_cmd, name="unify")
slides_group.add_command(language_view_cmd, name="language-view")
slides_group.add_command(suggest_sync_cmd, name="suggest-sync")
slides_group.add_command(slides_sync_group, name="sync")
slides_group.add_command(reconcile_vo_ids_cmd, name="reconcile-vo-ids")
slides_group.add_command(rename_id_cmd, name="rename-id")
slides_group.add_command(slides_translate_cmd, name="translate")
# `bootstrap` is the cold-start direction of `translate`; keep it
# invocable but list the command only once in --help.
slides_group.add_command(hidden_alias(slides_translate_cmd, "bootstrap"))
slides_group.add_command(search_slides_cmd, name="search")
slides_group.add_command(tidy_cmd, name="tidy")
slides_group.add_command(referenced_by_cmd, name="referenced-by")
slides_group.add_command(slug_report_cmd, name="slug-report")
slides_group.add_command(coverage_report_cmd, name="coverage-report")
slides_group.add_command(authoring_rules_cmd, name="rules")

# `polish` needs the [summarize] extra (LLM client); skip when absent.
try:
    from clm.cli.commands.slides.polish import polish as polish_cmd  # noqa: E402
except ImportError:
    polish_cmd = None  # type: ignore[assignment]

if polish_cmd is not None:
    slides_group.add_command(polish_cmd, name="polish")
