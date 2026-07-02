- **The `[ml]` optional-dependency extra was removed (Wave 2b-2).** The multi-GB
  machine-learning / data-science stack (PyTorch, transformers, pandas,
  scikit-learn, the LangGraph deep-agents deck, the Postgres deployment decks, …)
  is *course-runtime* — clm never imports it, only notebook kernels do — so it no
  longer belongs in clm's own venv. `pip install "coding-academy-lecture-manager[ml]"`
  and `pip install -e ".[all,ml]"` now fail on the unknown extra. It ships instead
  as the self-contained `course-runtime-requirements.txt` (which includes
  `ipykernel`): install it into a separate course venv and point clm at it with
  `clm provision kernel-env --python <path>` (Wave 2b-1), or run the notebook
  worker in Docker mode (the image already bakes an equivalent stack in). See
  `clm info migration`, `clm info commands` (`provision kernel-env`), and the
  installation guide's "Running ML course decks in Direct mode" section. (#516)
