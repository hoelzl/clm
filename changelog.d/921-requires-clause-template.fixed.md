- **C++ code export: function templates with a `requires` clause between the
  template head and the declarator are emitted at namespace scope again**
  instead of being display-wrapped inside a `slide_NN()` body, which MSVC
  rejected (C2760/C3878) and left the call site with an unknown identifier
  (#921). The classifier now skips the constraint — concept-ids,
  parenthesized expressions, and `&&`/`||` conjunctions — before classifying
  the declarator; the trailing `requires` form and the constrained-parameter
  form were already recognized and are unchanged.
