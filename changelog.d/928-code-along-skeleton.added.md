- **C++ code export: the code-along skeleton is real study material**
  (#928, phase 2). In `code-along` and `partial` code outputs a blanked
  cell now leaves one descriptive marker where its code would have gone —
  `// TODO: define twice` at namespace scope for a blanked definition or
  promoted variable, `// TODO: <section heading>` in the section body for
  statements — instead of a bare `// TODO` in the body. A `keep` cell that
  depends on code the student has yet to type (a name defined by an
  earlier blanked cell, by a `completed`/`alt` solution cell the view
  drops, or by another such cell) is emitted commented out behind
  `// depends on code you'll type above — uncomment after`, transitively,
  so every code-along deck compiles as shipped; operator overloads are
  tracked through their operand types. An originally empty cell is no
  longer a TODO in partial output, and section names match the completed
  output even when the section's slide_id collides with a blanked
  definition.
