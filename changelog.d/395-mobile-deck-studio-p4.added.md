- **Mobile Deck Studio P4 — preview + installable PWA.** The phone authoring
  surface (`clm serve --spec`) now renders cell previews correctly (markdown
  bodies no longer show every line as a heading) and **expands Jinja header
  macros** server-side (no kernel) so `is_j2` cells preview as the real header
  instead of raw `{{ … }}`. The Studio is now an **installable PWA** with a
  read-only **offline cache** of opened decks (view the last decks you opened
  when the desktop is briefly unreachable; editing still requires the desktop).
  (#395)
