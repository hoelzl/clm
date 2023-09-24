# Coding-Academy Lecture Manager

This is a tool to manage the lectures for my courses at Coding Academy Munich.
It may also be useful in other situation when you need to generate a set of
documents from a relatively large number of templates and snippets.

## Installation

To build the project use

```shell script
python -m build
```
in the root directory (i.e., the directory where `pyproject.toml` and
`setup.cfg` live).

After building the package you can install it with pip:
```shell script
pip install dist/clm-0.6.3-py3-none-any.whl
```

To install the package so that it can be used for development purposes
install it with
```shell script
pip install -e .
```
in the root directory.

## Working with the project

The project is configured to run `pytest` tests. Tests are in the `tests`
directory, outside the main package directory.  Therefore, you have to install
the package before running the tests. Once the package is installed, enter
`pytest` in the root of the project to run the tests:

```shell script
$ pytest
```

*Note:* If you install the package from a wheel, the tests will run against the
installed package; install in editable mode (i.e., using the `-e` option) to
test against the development package.

To check that the package works correctly with different Python versions run

```shell script
$ tox
```

Currently, Python versions 3.8, 3.9 and 3.10 are tested; dependencies for `tox`
are installed using `tox-conda`.

## Setting up Completions

### Bash

If you are using Bash on Ubuntu: Evaluate:

```shell script
$ _CLM_COMPLETE=bash_source clm --help > ~/.local/share/bash-completion/completions/clm
```

This requires the `bash-completion` package to be installed.

### Other shells

See [the Click
documentation](https://click.palletsprojects.com/en/8.1.x/shell-completion/) for
instructions.

I will probably add
[`click_completion`](https://github.com/click-contrib/click-completion) support
in the future to simplify this.