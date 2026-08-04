"""Containment: the write gate refuses every pair ``clm validate`` calls corrupt (Q4).

CLM judges the health of a split DE/EN pair from **two** places that grew
independently:

* ``clm validate``'s split-pair family — :func:`_check_shared_cell_parity`,
  :func:`_check_split_untranslated_text`, :func:`_check_split_tag_parity`,
  :func:`_check_split_slide_id_parity`,
  :func:`_check_split_companion_for_slide_parity` — the authoring-time
  detective, run by the pre-commit gate;
* :func:`~clm.slides.sync_verify.gate_projected_pair` — the sync engine's
  **write gate**, the thing that decides whether a pair may enter the trust
  store.

Two oracles for one question is the setup for the failure the review named Q4:
validate calls a pair corrupt, the gate blesses it anyway, and the ledger banks
the corruption as "verified". Nothing in the code prevented that — the two
families share no computation, and *no test related them at all*. This module
is that missing relation, stated as a property:

    **containment** — if validate's split-pair family reports an *error* on a
    pair, ``gate_projected_pair`` must return a non-empty list — for every
    shape except the one declared in :data:`CONTAINMENT_EXEMPT`.

Direction matters. The gate being **stricter** than validate is fine and in fact
routine (see :class:`TestGateIsStrictlyStronger`); the gate being *laxer* is the
bug class. So this pins one-way containment, not equivalence.

The exemptions are pinned too (:class:`TestDeliberateNonContainment`). Tag parity
is a warning in *both* oracles by design — an error there would make the write
gate refuse a pair the apply pass is in the middle of reconciling. And the
shared-cell German-text check (#772) is a validate *error* the gate never sees
(#782): a content-language heuristic belongs to validate, the advisory oracle,
never to the gate, the structural trust oracle. A test that did not say so
would leave a future reader unable to tell a deliberate exemption from an
oversight.

Measured over the 730-pair PythonCourses corpus at the time of writing:
containment holds on every pair, 0 gaps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.slides.sync_verify import VerifyViolation, gate_projected_pair, structural_gate
from clm.slides.validator import (
    Finding,
    _check_shared_cell_parity,
    _check_split_companion_for_slide_parity,
    _check_split_slide_id_parity,
    _check_split_tag_parity,
    _check_split_untranslated_text,
)

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title\") }}\n\n"


def _slide(sid: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{sid}"\n#\n# ## {title}\n\n'


def _shared_code(value: str = "1", tags: str = '["keep"]') -> str:
    return f"# %% tags={tags}\nx = {value}\n\n"


def _vo(sid: str, lang: str | None, owner: str, text: str) -> str:
    lang_attr = f' lang="{lang}"' if lang is not None else ""
    return (
        f'# %% [markdown]{lang_attr} tags=["voiceover"] for_slide="{owner}" '
        f'slide_id="{sid}"\n#\n# - {text}\n\n'
    )


def _deck(
    lang: str,
    *,
    code: str = "1",
    tags: str = '["keep"]',
    extra: str = "",
    header: str | None = None,
) -> str:
    head = header if header is not None else (HEADER_DE if lang == "de" else HEADER_EN)
    titles = {"de": ("Einführung", "Ende"), "en": ("Introduction", "The End")}[lang]
    return (
        head
        + _slide("intro", lang, titles[0])
        + _shared_code(code, tags)
        + extra
        + _slide("ende", lang, titles[1])
    )


def _validate_split_family(de: Path, en: Path) -> list[Finding]:
    """Exactly the five cross-file checks ``clm validate`` runs on a split pair.

    Kept as an explicit list rather than driven off ``validate_slide_file`` so the
    containment claim names the functions it covers: if a sixth split-pair check is
    added, this test does not silently keep passing while ignoring it — the
    companion :func:`test_the_split_pair_family_is_fully_enumerated` fails instead.
    """
    return [
        *_check_shared_cell_parity(de, en),
        *_check_split_untranslated_text(de, en),
        *_check_split_tag_parity(de, en),
        *_check_split_slide_id_parity(de, en),
        *_check_split_companion_for_slide_parity(de, en),
    ]


def _write_pair(
    folder: Path,
    de_text: str,
    en_text: str,
    *,
    de_comp: str | None = None,
    en_comp: str | None = None,
) -> tuple[Path, Path]:
    de = folder / "slides_t.de.py"
    en = folder / "slides_t.en.py"
    de.write_text(de_text, encoding="utf-8")
    en.write_text(en_text, encoding="utf-8")
    if de_comp is not None:
        (folder / "voiceover_t.de.py").write_text(de_comp, encoding="utf-8")
    if en_comp is not None:
        (folder / "voiceover_t.en.py").write_text(en_comp, encoding="utf-8")
    return de, en


# One entry per corruption shape either oracle can see. ``(de, en, de_comp, en_comp)``.
CORRUPTIONS: dict[str, tuple[str, str, str | None, str | None]] = {
    # --- shared-cell divergence: validate ERROR, must reach the gate -------------
    "shared-code-body-diverged": (_deck("de", code="1"), _deck("en", code="2"), None, None),
    "shared-code-whitespace-only": (
        _deck("de", code="1"),
        _deck("en").replace("x = 1\n", "x = 1 \n"),
        None,
        None,
    ),
    "shared-cell-count-mismatch": (
        _deck("de", extra=_shared_code("9")),
        _deck("en"),
        None,
        None,
    ),
    "shared-cell-tags-diverged": (
        _deck("de", tags='["keep"]'),
        _deck("en", tags='["keep", "extra"]'),
        None,
        None,
    ),
    "shared-markdown-diverged": (
        _deck("de", extra="# %% [markdown]\n#\n# Neutral A\n\n"),
        _deck("en", extra="# %% [markdown]\n#\n# Neutral B\n\n"),
        None,
        None,
    ),
    # --- shared-cell German text (#772): validate ERROR, gate passes (exempt) ----
    # Byte-identical across the halves, so structurally it is *valid* trust —
    # which is exactly why the validator (not the gate) must be the one to
    # flag it before ``sync record`` banks it as ``shared``. The one declared
    # containment exemption (#782): see CONTAINMENT_EXEMPT.
    "shared-code-german-text": (
        _deck("de", extra="# %%\n# Das ist ein geteilter Kommentar.\ny = 2\n\n"),
        _deck("en", extra="# %%\n# Das ist ein geteilter Kommentar.\ny = 2\n\n"),
        None,
        None,
    ),
    "preamble-diverged": (
        _deck("de", header=HEADER_DE + "# %%\nimport os\n\n"),
        _deck("en", header=HEADER_EN + "# %%\nimport sys\n\n"),
        None,
        None,
    ),
    # --- id-family divergence: validate WARNING, gate ERROR (stricter) -----------
    "id-set-diverged": (
        _deck("de"),
        HEADER_EN
        + _slide("intro", "en", "Introduction")
        + _shared_code()
        + _slide("finito", "en", "The End"),
        None,
        None,
    ),
    "id-order-diverged": (
        _deck("de"),
        HEADER_EN
        + _slide("ende", "en", "The End")
        + _shared_code()
        + _slide("intro", "en", "Introduction"),
        None,
        None,
    ),
    "duplicate-id-within-a-half": (
        HEADER_DE + _slide("intro", "de", "A") + _shared_code() + _slide("intro", "de", "B"),
        HEADER_EN + _slide("intro", "en", "A") + _shared_code() + _slide("ende", "en", "B"),
        None,
        None,
    ),
    # --- companion divergence ---------------------------------------------------
    "companion-for-slide-diverged": (
        _deck("de"),
        _deck("en"),
        _vo("intro-vo", "de", "intro", "Willkommen."),
        _vo("ende-vo", "en", "ende", "Bye."),
    ),
    "companion-one-sided": (
        _deck("de"),
        _deck("en"),
        _vo("intro-vo", "de", "intro", "Willkommen."),
        None,
    ),
    "companion-shared-body-diverged": (
        _deck("de"),
        _deck("en"),
        _vo("intro-vo", None, "intro", "Neutral A."),
        _vo("intro-vo", None, "intro", "Neutral B."),
    ),
}


#: The severity validate's split-pair family reports for each shape. Declared rather
#: than discovered: a check silently downgraded from ``error`` to ``warning`` would
#: otherwise shrink what the containment property covers while every test here stayed
#: green. ``None`` means validate does not see the shape at all.
VALIDATE_SEVERITY: dict[str, str | None] = {
    "shared-code-body-diverged": "error",
    "shared-code-whitespace-only": "error",
    "shared-cell-count-mismatch": "error",
    "shared-cell-tags-diverged": "error",
    "shared-markdown-diverged": "error",
    "shared-code-german-text": "error",
    "preamble-diverged": "error",
    "id-set-diverged": "warning",
    "id-order-diverged": "warning",
    "duplicate-id-within-a-half": "warning",
    "companion-for-slide-diverged": "warning",
    "companion-one-sided": "warning",
    "companion-shared-body-diverged": None,
}

#: Error shapes deliberately outside the containment property — validate errors,
#: the gate still records. Exactly one member (#782): German text in a
#: byte-identical shared code cell. The halves agree byte-for-byte, so
#: structurally the pair IS valid trust; the objection is to the *content*, and
#: content-language policing is a heuristic the trust oracle must never adopt
#: (a false positive at the gate would block mechanical recording outright,
#: while at validate it is a CI message with a per-cell opt-out). The
#: promotion to ``error`` (the corpus reached 0 findings, so the boundary
#: became categorical) hard-fails CI instead — the pair is stopped before it
#: is ever committed, not at the trust store. Every OTHER validate error must
#: reach the gate; growing this set needs the same class of argument.
CONTAINMENT_EXEMPT = frozenset({"shared-code-german-text"})


@pytest.fixture
def pair_factory(tmp_path: Path):
    counter = {"n": 0}

    def make(name: str) -> tuple[Path, Path]:
        counter["n"] += 1
        folder = tmp_path / f"{counter['n']:02d}_{name}"
        folder.mkdir()
        de_text, en_text, de_comp, en_comp = CORRUPTIONS[name]
        return _write_pair(folder, de_text, en_text, de_comp=de_comp, en_comp=en_comp)

    return make


class TestContainment:
    """The property: a pair validate errors on can never be recorded."""

    @pytest.mark.parametrize("name", sorted(CORRUPTIONS), ids=sorted(CORRUPTIONS))
    def test_validate_error_implies_the_gate_refuses(self, name: str, pair_factory) -> None:
        de, en = pair_factory(name)
        findings = _validate_split_family(de, en)
        validate_errors = [f for f in findings if f.severity == "error"]

        # Pin the severity first: a check downgraded to ``warning`` would silently
        # remove its shape from the containment claim, and the assertion below
        # would still pass vacuously.
        expected = VALIDATE_SEVERITY[name]
        severities = {f.severity for f in findings}
        if expected is None:
            assert not findings, f"{name}: validate now sees this — give it a severity"
        else:
            assert expected in severities, (
                f"{name}: expected validate to report {expected!r}, got "
                f"{sorted(severities) or 'nothing'}"
            )

        if not validate_errors:
            return  # not an error shape; the gate relation is pinned in the classes below
        if name in CONTAINMENT_EXEMPT:
            return  # declared exemption; pinned in TestDeliberateNonContainment
        assert gate_projected_pair(de, en, "#"), (
            f"CONTAINMENT VIOLATED for {name}: validate reports "
            f"{len(validate_errors)} error(s) but the write gate would record "
            f"this pair.\n" + "\n".join(f"  validate: {f.message}" for f in validate_errors)
        )

    def test_every_shape_has_a_declared_severity(self) -> None:
        """A new corruption shape must declare what validate makes of it."""
        assert set(CORRUPTIONS) == set(VALIDATE_SEVERITY)

    def test_the_family_still_errors_on_something(self, pair_factory) -> None:
        """The containment property is only meaningful while validate has errors to contain.

        If every split-pair check became a warning, the parametrized test above would
        pass on every shape without gating anything.
        """
        error_shapes = [n for n, sev in VALIDATE_SEVERITY.items() if sev == "error"]
        assert error_shapes, "no validate-error shapes left — containment is now vacuous"
        for name in error_shapes:
            de, en = pair_factory(name)
            assert any(f.severity == "error" for f in _validate_split_family(de, en))

    def test_a_clean_pair_passes_both(self, tmp_path: Path) -> None:
        """The control. Without it, a gate that refused everything would pass above."""
        de, en = _write_pair(tmp_path, _deck("de"), _deck("en"))
        assert _validate_split_family(de, en) == []
        assert gate_projected_pair(de, en, "#") == []


class TestGateIsStrictlyStronger:
    """The permitted direction — pinned so the asymmetry stays deliberate."""

    @pytest.mark.parametrize(
        "name",
        ["id-set-diverged", "id-order-diverged", "duplicate-id-within-a-half"],
    )
    def test_id_family_is_a_validate_warning_but_a_gate_error(
        self, name: str, pair_factory
    ) -> None:
        """Validate warns (it must not hard-fail CI on committed divergence); the gate blocks.

        The trust store has the stricter duty: recording a pair whose halves
        disagree about the id set or their order is the #652 corruption the
        ledger once certified.
        """
        de, en = pair_factory(name)
        findings = _validate_split_family(de, en)
        assert findings, "expected validate to see this at some severity"
        assert [f.severity for f in findings] == ["warning"] * len(findings)
        assert gate_projected_pair(de, en, "#")

    def test_gate_sees_companion_body_drift_validate_misses_entirely(self, pair_factory) -> None:
        """The gate's projection catches what validate's for_slide set comparison cannot.

        ``_check_split_companion_for_slide_parity`` compares which *slides* the
        companions narrate, never the narration bytes. A shared (language-neutral)
        narration cell whose halves differ is invisible to it — and is exactly the
        Y2 divergence the projecting gate exists to catch.
        """
        de, en = pair_factory("companion-shared-body-diverged")
        assert _validate_split_family(de, en) == []
        assert [v.kind for v in gate_projected_pair(de, en, "#")] == ["unify"]


class TestDeliberateNonContainment:
    """Documented exemptions. A future reader must be able to tell these from oversights."""

    def test_localized_tag_asymmetry_is_a_warning_in_both_and_never_gates(
        self, tmp_path: Path
    ) -> None:
        """Tag parity is warning-only on both sides — by design, not by omission.

        Error severity here would hard-fail CI on pre-existing committed
        asymmetries *and* — because the gate is the error subset — make the write
        gate refuse a pair the apply pass is in the middle of reconciling. A tag
        mismatch corrupts neither pairing nor unification, so it stays out.
        """
        de, en = _write_pair(
            tmp_path,
            HEADER_DE
            + '# %% [markdown] lang="de" tags=["slide", "keep"] slide_id="intro"\n#\n# ## E\n\n'
            + _shared_code()
            + _slide("ende", "de", "Ende"),
            _deck("en"),
        )
        findings = _validate_split_family(de, en)
        assert [f.severity for f in findings] == ["warning"]
        assert "mismatched tags" in findings[0].message
        assert gate_projected_pair(de, en, "#") == []

    def test_german_shared_text_is_a_validate_error_the_gate_never_sees(self, pair_factory) -> None:
        """German text in a byte-identical shared cell (#772) errors and never gates.

        The two oracles disagree here *by design*: the halves agree byte-for-byte,
        so structurally the pair is valid trust and the gate must record it — the
        sync engine can even bank the cell as ``shared`` via ``record_neutral``.
        That bankability is precisely the hazard: a later one-sided fix frames
        ``propagate_shared_edit``, a mechanical byte copy that would overwrite the
        German half with English. Content-language policing is therefore
        validate's job (an advisory oracle allowed to use heuristics), not the
        gate's (a trust oracle that must not). The check was born a warning while
        the corpus carried pre-existing German shared cells; the cleanup reached
        0 findings across all 659 split pairs, so #782 promoted it to ``error``
        — making this the one shape where a validate error does NOT imply a gate
        refusal (:data:`CONTAINMENT_EXEMPT`). The ``allow-untranslated`` tag is
        the per-cell opt-out.
        """
        de, en = pair_factory("shared-code-german-text")
        findings = _validate_split_family(de, en)
        assert [f.severity for f in findings] == ["error"]
        assert "German text" in findings[0].message
        assert gate_projected_pair(de, en, "#") == []

    def test_the_exemption_set_names_real_error_shapes(self, pair_factory) -> None:
        """A stale exemption must fail loudly, not exempt vacuously.

        An entry that is no longer an error shape (or no longer a shape at all)
        would silently widen the containment claim back without anyone deciding
        that. And an entry the gate actually *refuses* would be a vacuous
        exemption: the parametrized containment test skips exempt shapes, so
        without the gate-emptiness pin here nothing anywhere would state what
        the gate does with them.
        """
        assert CONTAINMENT_EXEMPT <= set(CORRUPTIONS)
        assert all(VALIDATE_SEVERITY[n] == "error" for n in CONTAINMENT_EXEMPT)
        for name in sorted(CONTAINMENT_EXEMPT):
            de, en = pair_factory(name)
            assert gate_projected_pair(de, en, "#") == [], (
                f"{name}: the gate refuses this pair, so exempting it from "
                f"containment is vacuous — remove it from CONTAINMENT_EXEMPT"
            )


class TestPromotedViolationsAreLabelledBlocking:
    """Everything :func:`structural_gate` returns is blocking — the labels must say so.

    ``order-parity`` is born a warning (the CLI must not hard-fail CI on committed
    divergence) and promoted to blocking by the whole-deck gate. It used to be
    returned *still labelled* ``warning``: harmless while every caller treats a
    non-empty return as a refusal, but a trap for the next one, since re-filtering
    the gate's own output on ``severity == "error"`` is an entirely reasonable thing
    to write and would silently reopen #652.
    """

    def test_whole_deck_gate_relabels_order_parity_as_error(self, pair_factory) -> None:
        de, en = pair_factory("id-order-diverged")
        gate = gate_projected_pair(de, en, "#")
        assert [v.kind for v in gate] == ["order-parity"]
        assert [v.severity for v in gate] == ["error"]

    def test_no_gate_result_is_ever_labelled_warning(self, pair_factory) -> None:
        """The invariant behind the previous test, over every corruption shape."""
        offenders: list[tuple[str, VerifyViolation]] = []
        for name in sorted(CORRUPTIONS):
            de, en = pair_factory(name)
            offenders += [
                (name, v) for v in gate_projected_pair(de, en, "#") if v.severity != "error"
            ]
        assert not offenders, (
            "the gate returned non-error-severity violations, but everything it "
            "returns is blocking: " + ", ".join(f"{n}:{v.kind}={v.severity}" for n, v in offenders)
        )

    def test_the_cli_severity_split_survives(self, pair_factory) -> None:
        """Promotion must not leak back into ``verify``'s reporting severity.

        ``verify`` reads :func:`verify_pair`, so the relabel is scoped to the gate;
        pinned here because the two severities living in one violation kind is the
        subtle part.
        """
        from clm.slides.sync_verify import verify_pair

        de, en = pair_factory("id-order-diverged")
        order = [v for v in verify_pair(de, en).violations if v.kind == "order-parity"]
        assert [v.severity for v in order] == ["warning"]

    def test_scoped_gate_does_not_promote(self, pair_factory) -> None:
        """A whole-pair order divergence must not block recording one reconciled slide."""
        de, en = pair_factory("id-order-diverged")
        de_text = de.read_text(encoding="utf-8")
        en_text = en.read_text(encoding="utf-8")
        assert structural_gate(de_text, en_text, "#", slide_id="intro") == []


#: The split-pair family this module claims to cover.
SPLIT_PAIR_CHECKS = {
    "_check_shared_cell_parity",
    "_check_split_untranslated_text",
    "_check_split_tag_parity",
    "_check_split_slide_id_parity",
    "_check_split_companion_for_slide_parity",
}


def test_the_split_pair_family_is_fully_enumerated() -> None:
    """Guard the guard: a new split-pair check must be added to this test.

    :func:`_validate_split_family` names its four checks explicitly, so a fifth one
    added to the validator would be silently left out of the containment claim while
    every test here kept passing. Compare the covered set against what the module
    actually defines.
    """
    from clm.slides import validator

    defined = {
        name
        for name in dir(validator)
        if name.startswith("_check_split_") or name == "_check_shared_cell_parity"
    }
    assert defined == SPLIT_PAIR_CHECKS, (
        f"unaccounted split-pair check(s): {sorted(defined - SPLIT_PAIR_CHECKS)} — add "
        f"them to `_validate_split_family` and to CORRUPTIONS so the containment "
        f"property covers them"
    )


@pytest.mark.parametrize("entry", ["validate_file", "validate_files", "validate_course"])
def test_every_validation_scope_runs_the_whole_family(entry: str) -> None:
    """All three scopes must run all five checks.

    A check wired into the directory scope but not the single-file one would make
    ``clm validate <file>`` quietly weaker than ``clm validate <dir>`` — and the
    containment argument is only as strong as the scope the pre-commit gate uses.
    """
    import inspect

    from clm.slides import validator

    source = inspect.getsource(getattr(validator, entry))
    missing = {name for name in SPLIT_PAIR_CHECKS if name not in source}
    assert not missing, f"{entry} does not run {sorted(missing)}"
