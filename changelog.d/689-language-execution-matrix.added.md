- **Every course language the images support is now executed by CI.** The Docker
  tier runs one deck per language — Python, C++, C#, Java and TypeScript — each
  written in that language's own percent format, executed in a real container,
  and required to render a value the code *computes*, so a kernel that stops
  running code cannot pass. Previously only Python was ever executed, and C#,
  Java and TypeScript were configured but exercised by nothing; all five pass in
  88s, with no new images and no added build time.
- The matrix is guarded against silently shrinking: a test fails if the case list
  and the `prog_lang` configuration drift apart, so a new language needs either a
  case or an explicit entry in the known-missing-kernel set.
