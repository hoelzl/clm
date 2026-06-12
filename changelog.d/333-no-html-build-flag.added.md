`clm build --no-html` skips HTML generation for every topic (#333), as if
each carried `html="no"` in the spec. HTML is the only output format whose
generation executes notebooks, so a `--no-html` build needs no Jupyter
kernel — intended for the code-export compile CI and other kernel-free
environments, where the HTML jobs would otherwise fail with `NoSuchKernel`
and could wedge the worker pool until the job timeout.
