- Slide-transition detection (`clm harvest report`, `clm voiceover …`) no longer
  holds every sampled frame in memory. Frame differences are now computed while
  the video is read, keeping only the previous frame, so memory stays under
  1 GB regardless of length; previously a 30–40 minute 4K recording needed
  30–40 GB. Non-sampled frames are skipped without decoding into images, which
  also makes detection several times faster. Difference scores and detected
  transitions are unchanged, so cached results stay valid (#1096).
