- The Docker notebook image's package resolution now mirrors the PythonCourses
  course venv: `exclude-newer` 2026-09-23 plus the course's per-package dates,
  and its `openai<3` cap (also added to `course-runtime-requirements.txt`). The
  1.34.1 image had resolved openai 3.6.0 while clm did not yet tag `httpx2`
  (fixed in 1.34.2, #1055), so LLM calls in Docker-mode builds with that image
  bypassed the topic cassettes — re-record decks recorded with it.
