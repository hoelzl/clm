- **The recordings dashboard can no longer be driven by another web page.**
  `clm recordings serve` has no login, and every action route was a plain form
  post, so any site open in the same browser could auto-submit one — arming a
  deck, starting a recording, or submitting a file for processing. Mutating
  requests now have to come from the dashboard's own origin (checked via
  `Sec-Fetch-Site` and `Origin`), and every request has to carry a `Host` that
  names this server, which closes the DNS-rebinding path that an origin check
  alone cannot. Reads are unaffected, and requests with neither header
  (`curl`, scripts) still work — this guards against other *pages*, not other
  processes. `--allowed-host` / `--allowed-origin` opt in to a Tailscale
  hostname or a reverse proxy.
- **`POST /process` no longer uploads arbitrary local files.** It took a path
  from the form and checked only that it existed, then handed it to the
  configured backend — with Auphonic, that streams the file to a third party.
  Submitted paths must now resolve under the recordings root, the same check
  its sibling `/open-explorer` already performed.
- **Course, section and deck names from the dashboard are validated.**
  `course_slug` reached `get_state_path()` unsanitized, so `../../../evil`
  wrote a state file outside CLM's config directory; a bare `..` escaped the
  recordings root, which the existing filename sanitizer strips separators
  from but otherwise leaves intact. Names containing a separator, a drive
  letter, a null byte, or a `.`/`..` component are now rejected with `400`.
