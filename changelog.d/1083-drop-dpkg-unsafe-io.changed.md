- The plantuml, drawio and notebook-lite Docker images no longer set dpkg's
  `force-unsafe-io` in their apt layers. Build-history measurements showed it
  gave no speed-up. The slow apt unpack (~3 s per package) came from Docker
  Desktop's data disk sitting on a high-fsync-latency drive (#1083).
