- **`slides sync`**: mirroring a group split with **two or more** id'd slides
  interleaved into one run of id-less shared cells no longer clumps the
  inserted slides adjacently on the twin (with the shared cells trailing after
  all of them). The writer's mirrored insert now skips past target-only cells
  whose content matches the source cells the insert was placed after, so each
  shared cell stays under the slide it moved into (#646).
