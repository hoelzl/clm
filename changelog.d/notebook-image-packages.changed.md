- Notebook worker image: the Python packages now mirror the PythonCourses
  course venv (`pyproject.toml` floors plus the modules its decks import
  directly). The full variant gains `deepeval` (RAG-evaluation decks),
  `deepagents`, `langchain-openrouter`, `langgraph-checkpoint-sqlite`,
  `langchain-mcp-adapters`, `fastmcp`, `langfuse`, CPU `fastembed` (arm64
  had no embedding backend), `psycopg`, `pytorch-model-summary`, and skorch
  from its git main; both variants gain `openpyxl`, `ipytest`, `pytest`,
  `icecream`, `PyGitHub`, `cookiecutter`, `click`, `python-dotenv`,
  `pydantic-settings`, `appdirs`, `loguru`, `fastapi`, `uvicorn`, `cython`,
  `joblib`, `plotly`, `pillow`. The old `~=` pins that held numpy at 2.0,
  pandas at 2.2 and scikit-learn at 1.5 are replaced by the course's floors
  with a `UV_EXCLUDE_NEWER=2026-08-29` resolution cutoff (the PyTorch step
  is exempt, like in the course venv); `ragas`, which no deck imports, is
  gone. The full stage now builds on the lite stage instead of duplicating
  its list. `course-runtime-requirements.txt` is synced with the same
  additions.
