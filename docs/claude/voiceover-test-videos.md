# Voiceover Prototype: Test Videos and Slides

## Test Video Inventory

All videos are in `D:\OBS\Recordings\AZAV Software-Engineering\`.
All slides are in `C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\slides\`.
The course spec is `C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\course-specs\software-engineering-azav.xml`.

### 1. Iteration Patterns Part 1 (HARD - worst case)

- **Video:** `06 Sequenzen\12 Iterations-Muster (Teil 1).mp4`
- **Slides:** `module_150_collections\topic_350_iteration_patterns1\slides_iteration_patterns1.py`
- **Notes:** Very hard case. Missing `subslide` tags caused the trainer to
  leave presentation mode and scroll the notebook manually. Use as a stress
  test, not as the primary prototype target. It's OK if this case isn't
  handled.

### 2. Iteration Patterns Part 2 (SHORT - clean)

- **Video:** `06 Sequenzen\13 Iterations-Muster (Teil 2).mp4`
- **Slides:** `module_150_collections\topic_360_iteration_patterns2\slides_iteration_patterns2.py`
- **Notes:** Short video without the presentation-mode issues of Part 1.
  Good first target for the diagnostic prototype.

### 3. Inheritance Motivation (EDGE CASE - VS Code demo)

- **Video:** `11 Vererbung und Polymorphie\01 Vererbung Motivation.mp4`
- **Slides:** (corresponding slides TBD)
- **Notes:** Contains a live demo in VS Code at the end of the video (not
  in slides). Useful for testing how the matcher handles non-slide content
  at the end of a video. Lower priority.

### 4. Comprehensions (TYPICAL - representative)

- **Video:** `12 Fortgeschrittene Kontrollstrukturen\01 Komprehensionen - Elegantere Iteration.mp4`
- **Slides:** `module_150_collections\topic_600_comprehension\slides_comprehension_part1.py`
- **Notes:** Representative of a typical video. Multiple slides with live
  coding. No unusual features. Good for validating the full pipeline once
  the prototype works on simpler cases.

## Recommended Test Order

1. **Video #2** (Iteration Patterns Part 2) — short, clean, first prototype
2. **Video #4** (Comprehensions) — typical, validates at scale
3. **Video #1** (Iteration Patterns Part 1) — stress test
4. **Video #3** (Inheritance Motivation) — edge case with VS Code demo

## Finding More Test Videos

The folder structure under `D:\OBS\Recordings\AZAV Software-Engineering\`
is organized by section (matching the course spec sections). Video titles
correspond roughly to the `header` element in the slide files. For slides
with particular features, find the corresponding AZAV slide and look for
a matching video in this folder. Some videos from earlier courses may be
missing.
