Code-along C++ code exports now emit blanked cells as `slide_NN()` stubs
with a `// TODO` body, called in order from the generated `main()` (#333,
phase 4). The exported project compiles as-is and gives students one
function per blanked notebook cell to live-code into. Applies to all
code-along-style variants, including the post-workshop part of `partial`.
