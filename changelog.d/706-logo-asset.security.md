- **Studio tier-2 preview: the course logo is now a same-origin asset, and
  `data:` is gone from the sanitizer entirely.** The bundled header macros
  still embed the logo as a `data:` URI for self-contained student notebooks,
  but the Studio's render-cell endpoint rewrites the *bundled* logo's URI to
  `GET /api/studio/asset/logo/<prog_lang>` (a new route serving the packaged
  logo files) **before** sanitizing. `ALLOWED_URL_SCHEMES` drops `data`, and
  the `<img src>`-only confinement rule — the sanitizer's most complex
  hand-rolled piece and the site of the first bypass found in the #704
  adversarial rounds — is deleted rather than hardened. The `clm serve` CSP
  (`#705`) no longer needs a `data:` exception in `img-src` and loses it too.
  A course author's own `data:` images in custom macros no longer render in
  the phone's tier-2 preview (they are refused like any other unlisted
  scheme); student-facing slides and notebooks are unaffected.
