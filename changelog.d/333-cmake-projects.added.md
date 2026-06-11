C++ courses now get a generated `CMakeLists.txt` in every code-output
directory (#333, phase 2): one executable target per deck, C++20, grouped
per language × kind. Students can open `Slides/Cpp/Completed` (or
`Code-Along`) as a CMake project in VS Code, CLion, or Visual Studio and
build any deck with real compiler diagnostics — a kernel-free way to work
with the course code. Deck-local headers are already copied next to the
translation units, so no include-path configuration is needed.
