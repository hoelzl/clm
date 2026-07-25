- **`CLM_WORKER_API_PORT` pins the Worker API's port.** The default `8765` is
  advisory — containers learn the real port from `CLM_API_URL` — so setting this
  variable is how you make a port a requirement: if it is taken, the build fails
  instead of moving. `0` asks the OS for any free port, which is what the Docker
  test tier now uses so each test gets a private server. Documented under
  "Worker API (Docker mode)" in `docs/user-guide/configuration.md`.
