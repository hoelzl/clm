- **Direct-mode notebook execution can now run its Python kernel in a separate
  interpreter (Wave 2b).** Point clm at a course venv and the notebook kernel
  runs there — so the course-runtime ML/data-science stack (torch/pandas/…) lives
  in a separate environment from clm's own, while clm keeps driving nbconvert
  (mirroring what the Docker notebook image already does). Register a course
  interpreter with the new `clm provision kernel-env --python <path>`, then
  select it via the `CLM_NOTEBOOK_KERNEL_PYTHON` env var, a course-spec
  `<kernel-python>` element, or `clm.toml` `[jupyter] kernel_python` (that is the
  precedence, most specific first). The value can be the **venv directory** (clm
  picks the platform interpreter inside it — `Scripts/python.exe` on Windows,
  `bin/python` on POSIX) and a **relative** value is resolved against the project
  root, so a single committed setting like `kernel_python = ".venv"` works
  cross-platform and lets a globally-installed clm build a course whose runtime
  stack lives in the course's own venv. Unset ⇒ the kernel runs in clm's own
  environment, exactly as before — this is fully opt-in. Only the `python3`
  kernel is affected; C++/C#/Java/TS kernels and Docker mode are unchanged. See
  `clm info commands` / `clm info spec-files` and
  `docs/claude/design/dependency-environment-isolation.md`.
