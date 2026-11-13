"""The prototype sync core, now :mod:`clm.slides.doc_core`.

Phase A of ``docs/claude/design/sync-sequence-core.md`` moved the prototype
into the package unchanged. This name stays so the commands and references in
``docs/claude/sync-structural-core-investigation.md`` keep working: importing
``sync_model`` gives the library module itself.
"""

import sys

from clm.slides import doc_core

sys.modules[__name__] = doc_core
