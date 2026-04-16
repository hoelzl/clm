"""JupyterLite site-builder worker.

Consumes the already-built ``notebook``-format output tree for a given
``(target, language, kind)`` tuple and produces a deployable JupyterLite
static site by shelling out to ``jupyter lite build``.

Opt-in only: the worker is dispatched exclusively when a course spec
lists ``jupyterlite`` in a target's ``<formats>`` and provides an
effective ``<jupyterlite>`` config (see ``clm info jupyterlite``).
"""
