The C++ code export no longer splits an expression after a brace-init
temporary (#333): `RequestBuilder{}.setTimeout(10).send();` or
`auto n = std::vector<int>{1, 2}.size();` previously broke apart at the
closing `}`, leaving a stray `.setTimeout(...)` item that cannot compile.
A `}` at depth 0 now only ends an item when the next token cannot
continue an expression.
