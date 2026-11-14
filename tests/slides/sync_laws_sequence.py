"""The sync laws (``sync_laws.py``) on the sequence core's pass.

Phase C of ``docs/claude/design/sync-sequence-core.md``: the laws ratchet
runs against both engines. :func:`run` is a :data:`~tests.slides.sync_laws.
Syncer` that drives :func:`clm.slides.doc_pass.run_pass` the way ``clm
slides sync report`` / ``apply`` do under ``CLM_SYNC_CORE=sequence``: record
the base with ``record``, rewrite the halves, then report and apply with
every question refused until the report is clean or asks.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from clm.slides import doc_ledger, doc_pass
from clm.slides.doc_lenses import load_bundle
from tests.slides.sync_laws import MAX_PASSES, Run, Word, deck_text
from tests.slides.sync_probe import _record, parse_labels


def run(base: Word, de: Word, en: Word) -> Run:
    passes: list[tuple[tuple[str, str], ...]] = []
    with tempfile.TemporaryDirectory() as tmp:
        de_path = Path(tmp) / "slides_t.de.py"
        en_path = Path(tmp) / "slides_t.en.py"

        def write(path: Path, lang: str, word: Word) -> None:
            path.write_text(deck_text(lang, word), encoding="utf-8", newline="\n")

        write(de_path, "de", base)
        write(en_path, "en", base)
        _record(de_path, en_path)
        write(de_path, "de", de)
        write(en_path, "en", en)

        def done(status: str, detail: str = "") -> Run:
            got = []
            for lang, path in (("de", de_path), ("en", en_path)):
                found, anomalies = parse_labels(path.read_text(encoding="utf-8"), lang)
                got.append(tuple(found))
                if anomalies and status == "clean":
                    status = "lang_or_kind_anomaly"
            return Run(status, detail, got[0], got[1], tuple(passes))

        ledger_path = doc_ledger.ledger_path_for(de_path)
        key = doc_ledger.deck_key_for(de_path)
        for _ in range(MAX_PASSES):
            bundle = load_bundle(de_path, en_path)
            ledger = doc_ledger.load(ledger_path)
            result = doc_pass.run_pass(bundle, ledger.decks.get(key))
            if result.refusal is not None:
                return done("apply_error", "refused")
            if result.error is not None:
                return done("apply_error", "assertion")
            if result.is_clean:
                return done("clean")
            passes.append(tuple((r.key, r.action) for r in result.rows))
            asks = sorted({r.action for r in result.rows if r.answers and r.is_open})
            if asks:
                return done("asks", "+".join(asks))
            stalls = sorted({r.action for r in result.rows if r.manual})
            if stalls:
                return done("stalls", "+".join(stalls))
            try:
                doc_pass.land(bundle, ledger, key, result)
            except Exception as exc:  # noqa: BLE001 - a crash is an outcome to report
                return done("apply_exception", type(exc).__name__)
            doc_ledger.save(ledger, ledger_path)
        return done("livelock_stalled")
