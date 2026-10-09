- **C++ export with Docker workers on a Windows host names its header and
  workshop files correctly (#1039).** The notebook worker converted the
  job's output path to its container path only for writing, and handed the
  processor the raw Windows host path. The Linux container read that path
  as one long file name, so the `.hpp` header and the `_workshop_N.cpp`
  files were named after the whole host path (`Errno 36`, or stray
  `C:Users…` files), and every lecture `.cpp` started with
  `#include "C:\…\deck.hpp"`. The payload now carries the container path, so
  the include is `#include "deck.hpp"` and the companions are written next
  to the lecture file, as on Linux hosts.
