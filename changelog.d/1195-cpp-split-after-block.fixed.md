- C++ code export: a statement that starts with an expression character
  (`++n;`, `*p = 1;`, `(void)x;`) right after a function body or another block
  now becomes its own item and moves into the slide function, instead of
  staying attached to the function before it. Brace initializers, lambdas and
  class bodies still continue into what follows. Bumps the notebook output
  epoch (#1195).
