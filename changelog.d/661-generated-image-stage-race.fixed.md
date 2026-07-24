- Builds are now byte-reproducible for generated diagrams. A PNG rendered from
  a DrawIO or PlantUML source is written into the source tree at
  `<topic>/img/<name>.png`, and the copy of that file into the output tree is
  meant to run in the later `COPY_GENERATED_IMAGES_STAGE`. The stage was chosen
  by asking whether the image already existed on disk, so a generated PNG that
  is *committed* to the course repo — the normal case, since checking them in is
  what makes notebooks render on GitHub — was misread as a static image and
  copied in stage 1, concurrently with the conversion overwriting that same
  path. Whichever finished first decided the bytes, so two consecutive builds of
  an unchanged course could differ. `Course` now marks every diagram output
  explicitly, independent of whether the file is present.
