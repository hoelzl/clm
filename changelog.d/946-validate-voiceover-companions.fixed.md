- **`clm validate` on a voiceover companion file no longer reports a
  spurious anchor error per cell (#946).** A separated voiceover companion
  (`voiceover_<stem>.<lang>.<ext>`, beside its deck or in the topic's
  `voiceover/` subdirectory) holds only narration cells bound to their
  slides by `for_slide`; the slide anchors live in the owning deck. Naming
  the companion directly ran the deck-only slide_id anchor walk on it, so
  every companion in the corpus reported one "voiceover/notes cell carries
  slide_id but no preceding slide/subslide anchor" error per cell (13-22 per
  file) while the topic directory validated clean — and an agent verifying
  its own companion edit was steered toward "repairing" a valid file. The
  companion is now validated *as a companion*: `format`/`tags` checks on the
  file itself, and `pairing` resolves each `for_slide` against the owning
  deck with the build-equivalent check the deck side already ran (an
  unresolvable target is the "build drops this narration" error). No owning
  deck → one `info` finding, no errors. `--quick` on a companion runs the
  syntax checks only. The companion→deck mapping `clm slides sync` already
  used moved to `clm.core.voiceover_companions.deck_for_companion` and is
  shared.
