# Design: content-independent `--image-format` and `--inline-images`

**Date:** 2026-06-14  
**Status:** Draft / discussion document  
**Scope:** `clm build` image handling for `.drawio`/`.pu` generated images and `--inline-images`.

## Problem

`clm build` has two flags that should be orthogonal to slide content:

- `--image-format {png,svg}`: selects the output format for diagrams generated from `.drawio` and `.pu` sources.
- `--inline-images`: embeds images as base64 data URLs in the generated output.

There are two practical issues:

1. **Source-tree pollution.** Generated images are currently written into the source `img/` folder (mixed with original, hand-authored images). Running a build with `--image-format=png` and later with `--image-format=svg` leaves both `foo.png` and `foo.svg` in `img/`, even though only one is referenced by the slides. The whole `img/` folder is copied to the output, so stale variants leak into builds.
2. **Content is not format-agnostic.** Slides currently reference a concrete file, e.g. `img/foo.png`. Switching formats therefore requires the build to rewrite slide references. Today only HTML-style `<img src="img/...">` tags are rewritten; Markdown `![...](img/...)` is not, so SVG builds can end up with broken image references in Markdown cells.

The goal is to make `--image-format` and `--inline-images` work without manual slide changes, preserve the distinction between generated and original images, and ensure the output contains only the images actually used.

## Current behavior (as of this writing)

Key files and flows:

- `src/clm/cli/commands/build.py`: defines `--image-format` and `--inline-images`.
- `src/clm/core/course_files/image_file.py`: `ImageFile.img_path` returns `.../img/<stem>.{course.image_format}`. Because `self.path` is the `.drawio`/`.pu` source file, this currently resolves to a path **inside the source tree**.
- `src/clm/core/course.py:_add_source_output_files`: adds the generated image path as a new course file, which is then classified as `DuplicatedImageFile` or `SharedImageFile`.
- `src/clm/core/operations/convert_drawio_file.py` / `convert_plantuml_file.py`: create conversion payloads. They do **not** set `ImagePayload.output_format`, so cache metadata defaults to `"png"` even for SVG jobs.
- `src/clm/core/utils/execution_utils.py`: stage 1 runs conversions; stage 2 copies generated images.
- `src/clm/workers/notebook/notebook_processor.py`:
  - `_rewrite_png_to_svg()` rewrites HTML `<img src="img/foo.png">` to `.svg` for known generated stems.
  - `_rewrite_image_paths()` prepends the shared-mode prefix for HTML `<img>`/`<video>` tags.
  - Markdown `![alt](img/foo.png)` is **not** rewritten.
  - `_inject_data_urls()` only inlines HTML `<img>` tags; Markdown images are not inlined.
- `src/clm/infrastructure/backends/sqlite_backend.py`: generated image bytes are stored in `clm_cache.db` (`processed_files` table) as `ImageResult` blobs, **after** the worker has already written the file to disk. The DB cache replays on cache hits, but it does not prevent source-tree writes on cache misses.

## Constraints & requirements

From discussion:

- Generated and original image names do **not** collide in the current courses (stem uniqueness).
- Both HTML `<img>` and Markdown `![...](...)` image references are used in slides.
- Output should keep the current layout: generated images live in the same `img/` folder as original images.
- Stale generated files in source `img/` should be cleaned up automatically by `clm build`.
- Switching formats and toggling inlining should require no manual slide edits; if content changes are needed they must be scriptable.
- The output must not include unnecessary image variants.

## Candidate approaches

### Approach 1: In-source cleanup + stem-based rewrite

Keep generating images into source `img/`, but make the process deterministic and content-agnostic.

1. **Source cleanup.** Before any conversion, derive every generated image stem from all `.drawio`/`.pu` files and delete `img/<stem>.png` and `img/<stem>.svg` for those stems only. Original images are untouched.
2. **Ordering fix.** Force generated-image copy operations to `COPY_GENERATED_IMAGES_STAGE` unconditionally, so a stale file is never copied before conversion overwrites it.
3. **Content rewriting.** Extend the notebook processor to rewrite both:
   - HTML `<img src="img/foo.png">`
   - Markdown `![alt](img/foo.png)`
   
   For references whose stem is in the generated set, replace the extension with the active `--image-format`; then prepend the shared-mode relative prefix. Non-generated references keep their original extension.
4. **Inlining fix.** Make `--inline-images` also inline Markdown image references, not just raw `<img>` tags.
5. **Cache metadata fix.** Set `ImagePayload.output_format` from `course.image_format` so the SQLite cache key is correct.

**Pros:**
- Smallest code change.
- No new storage abstraction.
- Output layout stays `img/`.

**Cons:**
- `clm build` still mutates the source tree on every cache miss.
- Relies on generated/original stems not colliding.
- Cleanup step must be careful not to delete original images that happen to share a stem.

### Approach 2: Build cache directory (recommended)

Move generated-image output out of the source tree while preserving the current output layout.

1. **Separate generation directory.** Change `ImageFile.img_path` to point to a build cache, e.g. `.clm/generated-img/<topic-path>/<stem>.{png,svg}` (or another ignored location under the project root).
2. **Virtual generated image files.** Represent generated images as virtual `CourseFile`s with:
   - `virtual_path = <topic>/img/<stem>.{ext}` (logical location used for output mapping)
   - `source_origin = <cache path>` (actual bytes)
   
   Copy operations then write from the cache to the output `img/` folder.
3. **One-time source cleanup.** On first build, delete stale `img/<stem>.png`/`img/<stem>.svg` files for known generated stems from the source tree. Future conversions never recreate them there.
4. **Content rewriting and inlining.** Same as Approach 1: rewrite Markdown + HTML references for generated stems, inline Markdown images too, fix `ImagePayload.output_format`.

**Pros:**
- Source tree is no longer modified by conversions.
- Output `img/` layout unchanged.
- Minimal worker/backend contract changes.
- Existing SQLite DB cache continues to work as a secondary acceleration layer.

**Cons:**
- New cache directory must be created and git-ignored.
- Slightly more virtual-file plumbing.
- Cache directory must be accessible to Docker workers (if used).

### Approach 3: Database as primary store

Make the existing `processed_files` cache the authoritative place for generated image bytes.

1. **Worker contract change.** DrawIO/PlantUML workers return image bytes instead of (or in addition to) writing a file. The backend stores an `ImageResult` in `processed_files` keyed by the source diagram path, content hash, and `image_format`.
2. **Copy-from-DB.** The generated-image copy operation looks up the cached bytes and writes them to each output variant.
3. **Content rewriting and inlining.** Same as the other approaches.
4. **Fix `ImagePayload.output_format`** so cache keys are format-correct.

**Pros:**
- Very clean separation: no generated files in the working tree at all.
- Leverages the existing SQLite cache infrastructure.

**Cons:**
- Broad architectural change across all backends (SQLite, FastStream, API, Docker workers).
- `CopyFileOperation` needs a new DB-backed source path.
- Generated image blobs can be large; DB size and retention/eviction become critical.
- Copy stage must know the original source diagram's content hash to retrieve the result.

## Recommendation

**Approach 2** is the best balance. It eliminates source-tree pollution with modest changes, keeps the output layout stable, and does not require redesigning the worker/backend contract.

Approach 1 is acceptable if minimizing code churn is more important than avoiding source-tree writes. Approach 3 is the cleanest conceptually but should only be pursued if the team is willing to refactor the backend abstraction.

## Open questions

1. Where exactly should the build cache live? Options:
   - `.clm/generated-img/` under the project root.
   - A per-output-root cache directory.
   - A system temp directory.
2. Should the cleanup of stale source `img/` files be unconditional, or guarded by a flag/confirmation?
3. Should `--inline-images` inline **all** local images (original + generated) or only generated diagrams?
4. How should collision detection work if a future course accidentally creates an original image with the same stem as a generated one?
5. Do we need a migration/deprecation period for any existing source `img/` files that were generated by older CLM versions?

## Affected files (all approaches)

- `src/clm/core/course_files/image_file.py`
- `src/clm/core/course.py`
- `src/clm/core/course_files/duplicated_image_file.py`
- `src/clm/core/course_files/shared_image_file.py`
- `src/clm/core/operations/convert_drawio_file.py`
- `src/clm/core/operations/convert_plantuml_file.py`
- `src/clm/workers/notebook/notebook_processor.py`
- `src/clm/infrastructure/messaging/base_classes.py` (`ImagePayload.output_format`)
- `.gitignore` (for Approach 2)
