"""Prompt templates for LLM-powered course summarization."""

# --- English prompts ---

CLIENT_SYSTEM_PROMPT_EN = """\
You are summarizing a training course notebook for prospective clients. \
Provide a concise description of the topics covered. Do NOT include details \
about teaching methodology, exact exercises, code examples, or internal \
structure. Focus on *what* participants will learn, not *how*. \
Write 1-3 sentences."""

TRAINER_SYSTEM_PROMPT_EN = """\
You are summarizing a training course notebook for internal trainers. Include: \
key topics covered, teaching approach, important code examples mentioned, and \
whether this notebook contains a workshop/hands-on exercise. Be specific about \
content structure. Write a short paragraph."""

CLIENT_USER_TEMPLATE_EN = """\
Course: {course_name}
Section: {section_name}
Notebook: {notebook_title}

Notebook content (markdown cells):

{content}"""

TRAINER_USER_TEMPLATE_EN = """\
Course: {course_name}
Section: {section_name}
Notebook: {notebook_title}
{workshop_info}
Notebook content:

{content}"""

# --- German prompts ---

CLIENT_SYSTEM_PROMPT_DE = """\
Du fasst ein Schulungskurs-Notebook für potenzielle Kunden zusammen. \
Beschreibe kurz die behandelten Themen. Gib KEINE Details zur Lehrmethodik, \
zu konkreten Übungen, Codebeispielen oder zur internen Struktur an. \
Konzentriere dich darauf, *was* die Teilnehmer lernen werden, nicht *wie*. \
Schreibe 1-3 Sätze auf Deutsch."""

TRAINER_SYSTEM_PROMPT_DE = """\
Du fasst ein Schulungskurs-Notebook für interne Trainer zusammen. Beschreibe: \
behandelte Schlüsselthemen, Lehransatz, wichtige erwähnte Codebeispiele und \
ob dieses Notebook einen Workshop oder eine praktische Übung enthält. Sei \
konkret bezüglich der Inhaltsstruktur. Schreibe einen kurzen Absatz auf Deutsch."""

CLIENT_USER_TEMPLATE_DE = """\
Kurs: {course_name}
Abschnitt: {section_name}
Notebook: {notebook_title}

Notebook-Inhalt (Markdown-Zellen):

{content}"""

TRAINER_USER_TEMPLATE_DE = """\
Kurs: {course_name}
Abschnitt: {section_name}
Notebook: {notebook_title}
{workshop_info}
Notebook-Inhalt:

{content}"""

# --- Prompt selection ---

_PROMPTS = {
    "en": {
        "client": (CLIENT_SYSTEM_PROMPT_EN, CLIENT_USER_TEMPLATE_EN),
        "trainer": (TRAINER_SYSTEM_PROMPT_EN, TRAINER_USER_TEMPLATE_EN),
    },
    "de": {
        "client": (CLIENT_SYSTEM_PROMPT_DE, CLIENT_USER_TEMPLATE_DE),
        "trainer": (TRAINER_SYSTEM_PROMPT_DE, TRAINER_USER_TEMPLATE_DE),
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
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the given audience and language.

    Args:
        audience: "client" or "trainer"
        course_name: Name of the course
        section_name: Name of the section
        notebook_title: Title of the notebook
        content: Extracted notebook content
        has_workshop: Whether the notebook contains a workshop
        language: "en" or "de"

    Returns:
        Tuple of (system_prompt, user_message)
    """
    lang_prompts = _PROMPTS.get(language, _PROMPTS["en"])
    system_template, user_template = lang_prompts[audience]

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
                "Dieses Notebook enthält einen Workshop/eine praktische Übung."
                if has_workshop
                else ""
            )
        else:
            workshop_info = (
                "This notebook contains a workshop/hands-on exercise." if has_workshop else ""
            )
        user = user_template.format(
            course_name=course_name,
            section_name=section_name,
            notebook_title=notebook_title,
            workshop_info=workshop_info,
            content=content,
        )
    return system_template, user
