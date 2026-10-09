- **C++ export hoists concept definitions to namespace scope (#1045).** A
  code cell that holds only `template <...> concept X = ...;` was classified
  as a variable declaration and stayed inside its section function, which
  g++ rejects (`a template declaration cannot appear at block scope`). Concept
  definitions now go to namespace scope like other templates, so the `global`
  tag workaround is no longer needed.
