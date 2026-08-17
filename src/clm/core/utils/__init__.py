# This package is deliberately import-pure: importing it must not configure
# the root logger (no ``logging.basicConfig`` at import time).
#
# History: this ``__init__`` used to run ``logging.basicConfig(level=WARNING)``
# on import (a relic from the clx era). Every clm worker entry point imports
# clm modules *before* its own ``logging.basicConfig(level=INFO)``, so the
# import-time handler made the worker's own call a silent no-op — worker logs
# were empty in production. Entry points that want logging configure it
# themselves (``clm.cli.main`` and each worker ``main()``).
