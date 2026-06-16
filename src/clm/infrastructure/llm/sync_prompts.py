"""Prompt templates for the cross-language sync judge.

Used by :class:`clm.infrastructure.llm.ollama_client.OllamaSyncJudge`
(Phase 7 of the slide-format-redesign). The judge is asked to propose
an updated version of one half of a DE/EN pair when its sibling has
drifted. The system + user prompts here are deliberately kept in a
separate module so the version constant can be bumped without
touching the client.

Bump :data:`SYNC_PROMPT_VERSION` whenever the system message or the
user-prompt format changes in a way that invalidates cached
proposals — the cache key embeds this version (see ``SyncCache`` in
``cache.py``).
"""

from __future__ import annotations

# Embedded in the SyncCache key. Bump on prompt-shape changes.
SYNC_PROMPT_VERSION = "v1"

# JSON Schema for the judge's reply. Passed to the model as a structured-output
# constraint so the response is guaranteed valid JSON even when proposed_text
# carries markdown tables, fenced code, escaped newlines, or characters like
# ``&lt;`` / ``⌃⌘`` that previously broke ``json.loads`` (the "sync response is
# not valid JSON" failure). The shape matches what :func:`parse_sync_response`
# expects. ``strict`` mode requires every property in ``required`` and forbids
# extras, so all three keys are listed.
SYNC_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["in_sync", "update"]},
        "proposed_text": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "proposed_text", "reason"],
}

# OpenAI/OpenRouter ``response_format`` wrapper around the bare schema above.
# (Ollama takes the bare schema as its ``format`` value instead.)
SYNC_RESPONSE_JSON_SCHEMA = {
    "name": "sync_proposal",
    "strict": True,
    "schema": SYNC_RESPONSE_SCHEMA,
}

SYNC_SYSTEM_PROMPT = (
    "You help instructors keep bilingual course slides in sync. The DE "
    "(German) and EN (English) versions of one slide cell have "
    "potentially drifted apart. You will be given the *source* cell "
    "(the side that was edited) and the *target* cell (the side that "
    "needs to catch up). Decide whether the target cell already "
    "adequately reflects the source cell, or whether it needs to be "
    "updated.\n\n"
    "If an update is needed, propose a revised target-cell body that:\n"
    "  - reflects the meaning of the source cell,\n"
    "  - reads idiomatically in the target language (idiomatic English "
    "when the target is EN; idiomatic German when the target is DE),\n"
    "  - preserves the markdown / cell structure of the original target "
    "(headings, bullets, code blocks, image tags, line breaks),\n"
    "  - leaves identifiers, code snippets, URLs, file paths, "
    "slide_id values, and other technical strings unchanged,\n"
    "  - does NOT add explanations, framing, or commentary that wasn't "
    "in the source cell.\n\n"
    "Reply with a single JSON object and no other text. The object has "
    "three keys:\n"
    '  "verdict": either "in_sync" (target already adequately reflects '
    'source, no edit needed) or "update" (target should be replaced '
    "by proposed_text).\n"
    '  "proposed_text": when verdict is "update", the full replacement '
    "body of the target cell, verbatim, with the same line-breaks as a "
    'normal jupytext markdown cell (each non-blank line prefixed with "# "). '
    'When verdict is "in_sync", the existing target cell body verbatim. '
    "No surrounding code fences either way.\n"
    '  "reason": one short sentence describing the verdict.'
)


def build_sync_user_prompt(
    *,
    source_text: str,
    target_text: str,
    source_lang: str,
    target_lang: str,
) -> str:
    """Format the user-side message for one sync request.

    ``source_lang`` and ``target_lang`` are short codes (``"de"`` /
    ``"en"``). ``source_text`` and ``target_text`` are the full cell
    bodies including their jupytext ``# `` prefixes (we pass them
    verbatim so the LLM matches the on-disk shape).
    """
    return "\n".join(
        [
            f"Source language: {source_lang}",
            f"Target language: {target_lang}",
            "",
            f"Source cell ({source_lang}):",
            source_text.strip("\n") or "(empty)",
            "",
            f"Current target cell ({target_lang}):",
            target_text.strip("\n") or "(empty)",
            "",
            "Propose the updated target cell.",
        ]
    )
