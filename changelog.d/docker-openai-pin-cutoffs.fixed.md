- The Docker notebook image (and `course-runtime-requirements.txt`) now cap
  `openai<3` like the PythonCourses course venv. The 1.34.x full image shipped
  openai 3.6.0, whose httpx2 transport clm's HTTP replay does not tag, so LLM
  calls in Docker-mode builds bypassed the topic cassettes and went live. The
  image's package resolution cutoffs now mirror the course venv's
  `exclude-newer` (2026-09-23) including its per-package dates.
