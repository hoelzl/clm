"""Prompt templates for LLM-powered course summarization and slide judgement.

This module is deliberately **model-free**: it holds prompts, prompt
versions, and the verdict dataclasses shared by the embedded clients
(:mod:`clm.infrastructure.llm.ollama_client`) and the agent-toolkit
framing (``clm.slides.coverage_task``) — so a toolkit's read path can
frame the exact prompt an embedded model would use without importing any
client module (#963).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# assign-ids title suggestions (Phase 2)
# ---------------------------------------------------------------------------

# Bumped whenever the prompt or system message changes in a way that
# invalidates cached suggestions. Embedded into the cache key per §2.3.
TITLE_PROMPT_VERSION = "v1"

TITLE_SYSTEM_PROMPT = (
    "You are helping an instructor pick a short title for a slide that "
    "currently has no heading. The slide content will be given to you. "
    "Reply with a single English title of 3-7 words, written in title "
    "case. Do not include quotes, punctuation, or any other text — just "
    "the bare title on one line."
)

# ---------------------------------------------------------------------------
# Voiceover coverage judgement (Phase 4)
# ---------------------------------------------------------------------------

# Bumped whenever the coverage prompt changes in a way that invalidates
# cached verdicts. Embedded into the cache key per §2.5.
COVERAGE_PROMPT_VERSION = "v1"

COVERAGE_SYSTEM_PROMPT = (
    "You check whether a voiceover script covers all of the bullet points "
    "from a slide. For each bullet, decide whether the voiceover already "
    "mentions or explains it. Do not require word-for-word match — "
    "semantic coverage is enough. A bullet is covered when a reasonable "
    "listener of the voiceover would hear the idea behind it; a bullet "
    "is uncovered when the voiceover never addresses it.\n\n"
    "Reply with a single JSON object and no other text. The object has "
    "two keys:\n"
    '  "bullets": a list of objects, one per slide bullet, each with '
    '"text" (the bullet, verbatim), "covered" (true or false), and '
    '"reason" (one short sentence).\n'
    '  "verdict": either "covered" (every bullet is covered) or "gaps" '
    "(at least one bullet is uncovered)."
)


def build_coverage_user_prompt(bullets: list[str], voiceover: str, *, lang: str) -> str:
    lines = [f"Language: {lang}", "", "Slide bullets:"]
    for i, bullet in enumerate(bullets, start=1):
        lines.append(f"{i}. {bullet}")
    lines.append("")
    lines.append("Voiceover:")
    lines.append(voiceover.strip() or "(no voiceover)")
    return "\n".join(lines)


@dataclass(frozen=True)
class BulletVerdict:
    """One bullet's coverage assessment."""

    text: str
    covered: bool
    reason: str = ""


@dataclass(frozen=True)
class CoverageVerdict:
    """A judge's verdict for one (slide, voiceover) pair.

    ``verdict`` is ``"covered"`` when every bullet is covered or
    ``"gaps"`` when at least one is missing. ``bullets`` lists the
    per-bullet decisions in slide order. ``raw`` is the verbatim
    response from the LLM, retained for debugging / ``--dump``.
    """

    verdict: str
    bullets: tuple[BulletVerdict, ...] = field(default_factory=tuple)
    raw: str = ""

    @property
    def has_gaps(self) -> bool:
        return self.verdict != "covered"

    @property
    def uncovered_bullets(self) -> tuple[BulletVerdict, ...]:
        return tuple(b for b in self.bullets if not b.covered)

    def to_json(self) -> str:
        """Serialize for storage in the cache's ``gap_details`` column."""
        return json.dumps(
            {
                "verdict": self.verdict,
                "bullets": [
                    {"text": b.text, "covered": b.covered, "reason": b.reason} for b in self.bullets
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, payload: str) -> CoverageVerdict:
        """Reconstruct a verdict from cache JSON."""
        data = json.loads(payload)
        bullets = tuple(
            BulletVerdict(
                text=str(item.get("text", "")),
                covered=bool(item.get("covered", False)),
                reason=str(item.get("reason", "")),
            )
            for item in data.get("bullets", [])
        )
        return cls(verdict=str(data.get("verdict", "gaps")), bullets=bullets)


# ---------------------------------------------------------------------------
# English prompts
# ---------------------------------------------------------------------------

CLIENT_SYSTEM_PROMPT_EN = """\
You are writing short descriptions of individual topics within a training \
course outline. Each piece of content you receive covers ONE topic (or a \
small group of topics) inside a larger course — it is NOT the whole course.

Rules:
- Describe what this topic covers. Use phrases like "Covers …", \
"Introduces …", "Explores …", or "Participants learn …".
- Do NOT say "In this course …" — the content is only one topic, not the \
entire course.
- Do NOT mention notebooks, Jupyter, slides, cells, or any delivery format. \
Describe the subject matter only.
- Do NOT describe teaching methodology, specific exercises, code examples, \
or internal structure.
- Focus on *what* participants will learn, not *how*.
- Write {length_instruction}."""

TRAINER_SYSTEM_PROMPT_EN = """\
You are writing internal summaries of individual topics within a training \
course for trainers. Each piece of content covers ONE topic (or a small \
group of topics) — it is NOT the whole course.

Rules:
- Describe key topics, teaching approach, and important code examples.
- Note whether this topic contains a workshop or hands-on exercise.
- Do NOT say "In this course …" — the content is only one topic.
- Do NOT refer to "notebooks" or "Jupyter" — use "this topic" or "this \
section" instead when you need to refer to the material.
- Be specific about content structure.
- Do NOT use Markdown headings (#, ##, etc.) — your output is embedded \
under a heading already. Use **bold text** for sub-sections if needed.
- Write {length_instruction}."""

AGENT_SYSTEM_PROMPT_EN = """\
You are writing a factual reference note about ONE topic of a training \
course, for another AI assistant that will later author or revise course \
material. Your reader needs to know what this topic already taught so it can \
reference it and avoid re-teaching it. Each piece of content you receive \
covers ONE topic (or a small group of topics) — it is NOT the whole course.

Rules:
- State concretely what is introduced: the concepts, the terminology/named \
definitions, and any APIs, functions, classes, commands, or syntax shown.
- If the topic contains a workshop or hands-on exercise, say so and state \
briefly what it asks the participant to do.
- Be dense and factual. No marketing language, no praise, no "this topic is \
important" framing — just what was covered.
- Do NOT say "In this course …" — the content is only one topic.
- Do NOT mention notebooks, Jupyter, slides, or cells — describe the subject \
matter and refer to "this topic" if you must name the material.
- Do NOT use Markdown headings (#, ##, …) — your output is embedded under a \
heading already. Use **bold** for sub-points if needed.
- Write {length_instruction}."""

CLIENT_USER_TEMPLATE_EN = """\
Course: {course_name}
Section: {section_name}
Topic: {notebook_title}

Topic content:

{content}"""

AGENT_USER_TEMPLATE_EN = """\
Course: {course_name}
Section: {section_name}
Topic: {notebook_title}
{workshop_info}
Topic content:

{content}"""

TRAINER_USER_TEMPLATE_EN = """\
Course: {course_name}
Section: {section_name}
Topic: {notebook_title}
{workshop_info}
Topic content:

{content}"""

# ---------------------------------------------------------------------------
# German prompts
# ---------------------------------------------------------------------------

CLIENT_SYSTEM_PROMPT_DE = """\
Du schreibst kurze Beschreibungen einzelner Themen innerhalb einer \
Schulungskurs-Gliederung. Jeder Inhalt, den du erhältst, behandelt EIN \
Thema (oder eine kleine Gruppe von Themen) innerhalb eines größeren \
Kurses — es ist NICHT der gesamte Kurs.

Regeln:
- Beschreibe, was dieses Thema behandelt. Verwende Formulierungen wie \
"Behandelt …", "Führt ein in …", "Erkundet …" oder \
"Die Teilnehmer lernen …".
- Sage NICHT "In diesem Kurs …" — der Inhalt ist nur ein einzelnes Thema, \
nicht der gesamte Kurs.
- Erwähne KEINE Notebooks, Jupyter, Folien, Zellen oder irgendein \
Vermittlungsformat. Beschreibe nur den Lerninhalt.
- Gib KEINE Details zur Lehrmethodik, zu konkreten Übungen, \
Codebeispielen oder zur internen Struktur an.
- Konzentriere dich darauf, *was* die Teilnehmer lernen werden, \
nicht *wie*.
- Schreibe {length_instruction} auf Deutsch."""

TRAINER_SYSTEM_PROMPT_DE = """\
Du schreibst interne Zusammenfassungen einzelner Themen innerhalb eines \
Schulungskurses für Trainer. Jeder Inhalt behandelt EIN Thema (oder eine \
kleine Gruppe von Themen) — es ist NICHT der gesamte Kurs.

Regeln:
- Beschreibe Schlüsselthemen, Lehransatz und wichtige Codebeispiele.
- Vermerke, ob dieses Thema einen Workshop oder eine praktische Übung \
enthält.
- Sage NICHT "In diesem Kurs …" — der Inhalt ist nur ein einzelnes Thema.
- Verwende nicht "Notebook" oder "Jupyter" — benutze stattdessen \
"dieses Thema" oder "dieser Abschnitt", wenn du auf das Material \
verweisen musst.
- Sei konkret bezüglich der Inhaltsstruktur.
- Verwende KEINE Markdown-Überschriften (#, ##, usw.) — deine Ausgabe \
wird unter einer bestehenden Überschrift eingebettet. Nutze **fetten \
Text** für Unterabschnitte, falls nötig.
- Schreibe {length_instruction} auf Deutsch."""

AGENT_SYSTEM_PROMPT_DE = """\
Du schreibst eine sachliche Referenznotiz über EIN Thema eines \
Schulungskurses, für einen anderen KI-Assistenten, der später Kursmaterial \
verfassen oder überarbeiten wird. Dein Leser muss wissen, was dieses Thema \
bereits vermittelt hat, um darauf verweisen zu können und es nicht erneut zu \
vermitteln. Jeder Inhalt, den du erhältst, behandelt EIN Thema (oder eine \
kleine Gruppe von Themen) — es ist NICHT der gesamte Kurs.

Regeln:
- Benenne konkret, was eingeführt wird: die Konzepte, die Fachbegriffe/ \
benannten Definitionen sowie alle gezeigten APIs, Funktionen, Klassen, \
Befehle oder Syntax.
- Wenn das Thema einen Workshop oder eine praktische Übung enthält, vermerke \
das und beschreibe kurz, was die Teilnehmer tun sollen.
- Sei dicht und sachlich. Keine Werbesprache, kein Lob, keine Formulierungen \
wie "dieses Thema ist wichtig" — nur, was behandelt wurde.
- Sage NICHT "In diesem Kurs …" — der Inhalt ist nur ein einzelnes Thema.
- Erwähne KEINE Notebooks, Jupyter, Folien oder Zellen — beschreibe den \
Lerninhalt und verweise mit "dieses Thema" auf das Material, falls nötig.
- Verwende KEINE Markdown-Überschriften (#, ##, …) — deine Ausgabe wird unter \
einer bestehenden Überschrift eingebettet. Nutze **fetten Text** für \
Unterpunkte, falls nötig.
- Schreibe {length_instruction} auf Deutsch."""

CLIENT_USER_TEMPLATE_DE = """\
Kurs: {course_name}
Abschnitt: {section_name}
Thema: {notebook_title}

Themeninhalt:

{content}"""

AGENT_USER_TEMPLATE_DE = """\
Kurs: {course_name}
Abschnitt: {section_name}
Thema: {notebook_title}
{workshop_info}
Themeninhalt:

{content}"""

TRAINER_USER_TEMPLATE_DE = """\
Kurs: {course_name}
Abschnitt: {section_name}
Thema: {notebook_title}
{workshop_info}
Themeninhalt:

{content}"""

# ---------------------------------------------------------------------------
# Length instructions per style
# ---------------------------------------------------------------------------

_LENGTH_INSTRUCTIONS = {
    "en": {
        "prose": {
            "client": "1-3 sentences",
            "trainer": "a short paragraph",
            "agent": "a dense paragraph (2-5 sentences) listing the concepts, "
            "terms, and APIs introduced",
        },
        "bullets": {
            "client": "a concise bullet-point list (3-6 bullets, no full sentences needed)",
            "trainer": "a bullet-point list covering key points (4-8 bullets)",
            "agent": "a compact bullet-point list of the concepts, terms, and APIs "
            "introduced (no full sentences needed)",
        },
    },
    "de": {
        "prose": {
            "client": "1-3 Sätze",
            "trainer": "einen kurzen Absatz",
            "agent": "einen dichten Absatz (2-5 Sätze), der die eingeführten "
            "Konzepte, Begriffe und APIs benennt",
        },
        "bullets": {
            "client": "eine knappe Aufzählung (3-6 Stichpunkte, keine ganzen Sätze nötig)",
            "trainer": "eine Aufzählung der wichtigsten Punkte (4-8 Stichpunkte)",
            "agent": "eine kompakte Aufzählung der eingeführten Konzepte, Begriffe "
            "und APIs (keine ganzen Sätze nötig)",
        },
    },
}

# ---------------------------------------------------------------------------
# Prompt selection
# ---------------------------------------------------------------------------

_PROMPTS = {
    "en": {
        "client": (CLIENT_SYSTEM_PROMPT_EN, CLIENT_USER_TEMPLATE_EN),
        "trainer": (TRAINER_SYSTEM_PROMPT_EN, TRAINER_USER_TEMPLATE_EN),
        "agent": (AGENT_SYSTEM_PROMPT_EN, AGENT_USER_TEMPLATE_EN),
    },
    "de": {
        "client": (CLIENT_SYSTEM_PROMPT_DE, CLIENT_USER_TEMPLATE_DE),
        "trainer": (TRAINER_SYSTEM_PROMPT_DE, TRAINER_USER_TEMPLATE_DE),
        "agent": (AGENT_SYSTEM_PROMPT_DE, AGENT_USER_TEMPLATE_DE),
    },
}


def get_prompts(
    audience: str,
    course_name: str,
    section_name: str,
    notebook_title: str,
    content: str,
    has_workshop: bool = False,
    language: str = "en",
    style: str = "prose",
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the given audience, language, and style.

    Args:
        audience: "client", "trainer", or "agent"
        course_name: Name of the course
        section_name: Name of the section
        notebook_title: Title of the notebook
        content: Extracted notebook content
        has_workshop: Whether the notebook contains a workshop
        language: "en" or "de"
        style: "prose" or "bullets"

    Returns:
        Tuple of (system_prompt, user_message)
    """
    lang_prompts = _PROMPTS.get(language, _PROMPTS["en"])
    system_template, user_template = lang_prompts[audience]

    lang_lengths = _LENGTH_INSTRUCTIONS.get(language, _LENGTH_INSTRUCTIONS["en"])
    length_instruction = lang_lengths[style][audience]
    system_prompt = system_template.format(length_instruction=length_instruction)

    if audience == "client":
        user = user_template.format(
            course_name=course_name,
            section_name=section_name,
            notebook_title=notebook_title,
            content=content,
        )
    else:
        if language == "de":
            workshop_info = (
                "Dieses Thema enthält einen Workshop/eine praktische Übung." if has_workshop else ""
            )
        else:
            workshop_info = (
                "This topic contains a workshop/hands-on exercise." if has_workshop else ""
            )
        user = user_template.format(
            course_name=course_name,
            section_name=section_name,
            notebook_title=notebook_title,
            workshop_info=workshop_info,
            content=content,
        )
    return system_prompt, user
