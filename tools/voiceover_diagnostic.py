"""Diagnostic prototype for voiceover slide-transition detection.

This script extracts frames from a video, computes frame-to-frame
difference scores, identifies transition candidates, and produces
a diagnostic plot + optional OCR analysis of transition frames.

Usage:
    uv run python tools/voiceover_diagnostic.py VIDEO_PATH [--fps 2] [--output-dir OUTPUT]
    uv run python tools/voiceover_diagnostic.py VIDEO_PATH --ocr --slides SLIDES_PATH --lang de

Examples:
    # Basic frame-diff analysis with plot
    uv run python tools/voiceover_diagnostic.py "D:/OBS/Recordings/AZAV Software-Engineering/06 Sequenzen/13 Iterations-Muster (Teil 2).mp4"

    # With OCR matching against slides
    uv run python tools/voiceover_diagnostic.py "D:/OBS/Recordings/AZAV Software-Engineering/06 Sequenzen/13 Iterations-Muster (Teil 2).mp4" --ocr --slides "C:/Users/tc/Programming/Python/Courses/Own/PythonCourses/slides/module_150_collections/topic_360_iteration_patterns2/slides_iteration_patterns2.py" --lang de
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Slide parser (minimal, for prototype)
# ---------------------------------------------------------------------------

@dataclass
class SlideCell:
    """A single cell from a .py slide file."""
    line_number: int
    header: str
    content: str
    cell_type: str  # "markdown", "code"
    lang: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class SlideGroup:
    """A visual slide: one or more cells shown together."""
    index: int
    slide_type: str  # "slide" or "subslide"
    title: str
    text_content: str  # all text, stripped of markdown formatting
    cells: list[SlideCell] = field(default_factory=list)


def parse_cell_header(header: str) -> dict:
    """Extract metadata from a cell header like '# %% [markdown] lang="de" tags=["slide"]'."""
    info: dict = {}

    if "[markdown]" in header:
        info["cell_type"] = "markdown"
    else:
        info["cell_type"] = "code"

    lang_match = re.search(r'lang="(\w+)"', header)
    if lang_match:
        info["lang"] = lang_match.group(1)

    tags_match = re.search(r'tags=\["([^"]*)"(?:,\s*"([^"]*)")*\]', header)
    if tags_match:
        info["tags"] = [g for g in tags_match.groups() if g is not None]
    else:
        info["tags"] = []

    return info


def parse_slides(path: Path, lang: str) -> list[SlideGroup]:
    """Parse a .py slide file into slide groups for a given language."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Split into cells
    cells: list[SlideCell] = []
    current_header = None
    current_lines: list[str] = []
    current_line_number = 0

    for i, line in enumerate(lines, 1):
        if line.startswith("# %%") or line.startswith("# j2 "):
            if current_header is not None:
                content = "\n".join(current_lines).strip()
                info = parse_cell_header(current_header)
                cells.append(SlideCell(
                    line_number=current_line_number,
                    header=current_header,
                    content=content,
                    cell_type=info["cell_type"],
                    lang=info.get("lang"),
                    tags=info["tags"],
                ))
            current_header = line
            current_lines = []
            current_line_number = i
        else:
            current_lines.append(line)

    # Last cell
    if current_header is not None:
        content = "\n".join(current_lines).strip()
        info = parse_cell_header(current_header)
        cells.append(SlideCell(
            line_number=current_line_number,
            header=current_header,
            content=content,
            cell_type=info["cell_type"],
            lang=info.get("lang"),
            tags=info["tags"],
        ))

    # Group into slides, filtering by language
    groups: list[SlideGroup] = []
    current_group: SlideGroup | None = None

    for cell in cells:
        # Skip cells in the other language
        if cell.lang is not None and cell.lang != lang:
            continue

        is_slide = "slide" in cell.tags or "subslide" in cell.tags
        slide_type = "slide" if "slide" in cell.tags else "subslide" if "subslide" in cell.tags else None

        if is_slide:
            # Start a new group
            if current_group is not None:
                groups.append(current_group)

            # Extract title from markdown content
            title = ""
            if cell.cell_type == "markdown":
                for content_line in cell.content.split("\n"):
                    stripped = content_line.lstrip("# ").strip()
                    if stripped.startswith("#"):
                        title = stripped.lstrip("# ").strip()
                        break
                    elif stripped:
                        title = stripped
                        break

            current_group = SlideGroup(
                index=len(groups),
                slide_type=slide_type or "slide",
                title=title,
                text_content="",
                cells=[cell],
            )
        elif current_group is not None:
            current_group.cells.append(cell)

    if current_group is not None:
        groups.append(current_group)

    # Build text content for each group (for OCR matching)
    for group in groups:
        text_parts = []
        for cell in group.cells:
            if cell.cell_type == "markdown":
                # Strip comment prefixes and markdown formatting
                for content_line in cell.content.split("\n"):
                    stripped = content_line.lstrip("# ").strip()
                    # Remove markdown formatting
                    stripped = re.sub(r'[*_`#]', '', stripped)
                    stripped = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', stripped)
                    if stripped:
                        text_parts.append(stripped)
            else:
                # Include code as-is (OCR might pick it up)
                for content_line in cell.content.split("\n"):
                    stripped = content_line.lstrip("# ").strip()
                    if stripped:
                        text_parts.append(stripped)
        group.text_content = " ".join(text_parts)

    return groups


# ---------------------------------------------------------------------------
# Frame extraction and differencing
# ---------------------------------------------------------------------------

def extract_frames(video_path: str, fps: float = 2.0) -> list[tuple[float, np.ndarray]]:
    """Extract frames from video at the given fps rate.

    Returns list of (timestamp_seconds, frame) tuples.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Cannot open video '{video_path}'", file=sys.stderr)
        sys.exit(1)

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / video_fps if video_fps > 0 else 0
    frame_interval = int(video_fps / fps) if fps > 0 else 1

    print(f"Video: {video_fps:.1f} fps, {total_frames} frames, {duration:.1f}s duration")
    print(f"Sampling every {frame_interval} frames ({fps} fps) -> ~{int(duration * fps)} samples")

    frames: list[tuple[float, np.ndarray]] = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % frame_interval == 0:
            timestamp = frame_idx / video_fps
            # Convert to grayscale for comparison
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frames.append((timestamp, gray))
        frame_idx += 1

    cap.release()
    print(f"Extracted {len(frames)} frames")
    return frames


def compute_differences(frames: list[tuple[float, np.ndarray]]) -> list[tuple[float, float]]:
    """Compute frame-to-frame difference scores.

    Returns list of (timestamp, difference_score) tuples.
    Uses normalized absolute pixel difference (0.0 = identical, 1.0 = completely different).
    """
    diffs: list[tuple[float, float]] = []

    for i in range(1, len(frames)):
        ts, frame = frames[i]
        _, prev_frame = frames[i - 1]

        # Normalized mean absolute difference
        diff = np.mean(np.abs(frame.astype(float) - prev_frame.astype(float))) / 255.0
        diffs.append((ts, float(diff)))

    return diffs


def find_transition_candidates(
    diffs: list[tuple[float, float]],
    window_size: int = 10,
    threshold_factor: float = 3.0,
    min_absolute: float = 0.05,
) -> list[tuple[float, float, float]]:
    """Find transition candidates as spikes in the difference signal.

    Returns list of (timestamp, diff_score, confidence) tuples, sorted by
    confidence descending.

    A candidate is a frame where the difference exceeds both:
    - threshold_factor * rolling_median (relative threshold)
    - min_absolute (absolute threshold, to avoid noise in static sections)
    """
    if len(diffs) < window_size:
        return [(ts, score, score) for ts, score in diffs if score > min_absolute]

    scores = np.array([d for _, d in diffs])
    timestamps = [ts for ts, _ in diffs]

    candidates: list[tuple[float, float, float]] = []

    for i in range(len(scores)):
        # Rolling median over a window centered on i
        start = max(0, i - window_size // 2)
        end = min(len(scores), i + window_size // 2 + 1)
        median = float(np.median(scores[start:end]))

        threshold = max(median * threshold_factor, min_absolute)

        if scores[i] > threshold:
            # Confidence: how far above the threshold
            confidence = float(scores[i] / max(threshold, 1e-6))
            candidates.append((timestamps[i], float(scores[i]), confidence))

    # Sort by confidence descending
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# OCR (optional)
# ---------------------------------------------------------------------------

def ocr_frame(frame: np.ndarray) -> str:
    """Run Tesseract OCR on a grayscale frame."""
    try:
        import pytesseract
    except ImportError:
        print("pytesseract not installed. Run: uv pip install pytesseract", file=sys.stderr)
        sys.exit(1)

    # Tesseract works better with some preprocessing
    # Threshold to binary (white background, dark text)
    _, binary = cv2.threshold(frame, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    text = pytesseract.image_to_string(binary, lang="deu+eng")
    return text.strip()


def fuzzy_match_slide(ocr_text: str, slides: list[SlideGroup]) -> list[tuple[int, float]]:
    """Match OCR text against slide content using fuzzy matching.

    Returns list of (slide_index, score) sorted by score descending.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        print("rapidfuzz not installed. Run: uv pip install rapidfuzz", file=sys.stderr)
        sys.exit(1)

    results = []
    for slide in slides:
        score = fuzz.token_set_ratio(ocr_text.lower(), slide.text_content.lower())
        results.append((slide.index, score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

def plot_differences(
    diffs: list[tuple[float, float]],
    candidates: list[tuple[float, float, float]],
    output_path: Path,
    title: str = "Frame Differences",
):
    """Plot frame difference scores with transition candidates marked."""
    import matplotlib.pyplot as plt

    timestamps = [ts for ts, _ in diffs]
    scores = [s for _, s in diffs]

    fig, ax = plt.subplots(figsize=(16, 5))
    ax.plot(timestamps, scores, linewidth=0.5, alpha=0.8, label="Frame difference")

    # Mark candidates
    if candidates:
        cand_ts = [ts for ts, _, _ in candidates]
        cand_scores = [s for _, s, _ in candidates]
        ax.scatter(cand_ts, cand_scores, color="red", s=30, zorder=5,
                   label=f"Transition candidates ({len(candidates)})")

    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel("Normalized difference")
    ax.set_title(title)
    ax.legend()
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    print(f"Plot saved to {output_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Voiceover slide-transition diagnostic")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--fps", type=float, default=2.0, help="Frame sampling rate (default: 2.0)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory (default: same as video)")
    parser.add_argument("--threshold-factor", type=float, default=3.0,
                        help="Spike detection threshold factor (default: 3.0)")
    parser.add_argument("--min-absolute", type=float, default=0.05,
                        help="Minimum absolute difference threshold (default: 0.05)")
    parser.add_argument("--ocr", action="store_true", help="Run OCR on transition candidates")
    parser.add_argument("--slides", type=str, default=None,
                        help="Path to .py slide file (required with --ocr)")
    parser.add_argument("--lang", type=str, default="de",
                        help="Slide language for matching (default: de)")
    parser.add_argument("--save-frames", action="store_true",
                        help="Save transition candidate frames as images")

    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"Error: Video not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else video_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem

    # Step 1: Extract frames
    print(f"\n=== Extracting frames at {args.fps} fps ===")
    frames = extract_frames(str(video_path), fps=args.fps)

    # Step 2: Compute differences
    print("\n=== Computing frame differences ===")
    diffs = compute_differences(frames)

    if not diffs:
        print("Error: Not enough frames to compute differences", file=sys.stderr)
        sys.exit(1)

    scores = [s for _, s in diffs]
    print(f"Difference stats: min={min(scores):.4f}, max={max(scores):.4f}, "
          f"mean={np.mean(scores):.4f}, median={np.median(scores):.4f}")

    # Step 3: Find transition candidates
    print(f"\n=== Finding transition candidates (factor={args.threshold_factor}, "
          f"min_abs={args.min_absolute}) ===")
    candidates = find_transition_candidates(
        diffs,
        threshold_factor=args.threshold_factor,
        min_absolute=args.min_absolute,
    )
    print(f"Found {len(candidates)} candidates:")
    for ts, score, conf in candidates[:20]:
        minutes = int(ts // 60)
        seconds = ts % 60
        print(f"  {minutes:02d}:{seconds:05.2f}  diff={score:.4f}  confidence={conf:.2f}")

    # Step 4: Plot
    print("\n=== Generating plot ===")
    plot_path = output_dir / f"{stem}_framediff.png"
    plot_differences(diffs, candidates, plot_path,
                     title=f"Frame Differences: {video_path.name}")

    # Step 5: Save transition frames (optional)
    if args.save_frames and candidates:
        frames_dir = output_dir / f"{stem}_frames"
        frames_dir.mkdir(exist_ok=True)
        print(f"\n=== Saving transition frames to {frames_dir} ===")

        # Build a timestamp -> frame lookup
        frame_dict = {round(ts, 2): frame for ts, frame in frames}

        for i, (ts, score, conf) in enumerate(candidates[:20]):
            # Find closest frame
            closest_ts = min(frame_dict.keys(), key=lambda t: abs(t - ts))
            frame = frame_dict[closest_ts]
            frame_path = frames_dir / f"candidate_{i:02d}_{ts:.1f}s_diff{score:.3f}.png"
            cv2.imwrite(str(frame_path), frame)

        print(f"Saved {min(len(candidates), 20)} candidate frames")

    # Step 6: OCR matching (optional)
    if args.ocr:
        if not args.slides:
            print("Error: --slides required with --ocr", file=sys.stderr)
            sys.exit(1)

        slides_path = Path(args.slides)
        if not slides_path.exists():
            print(f"Error: Slides not found: {slides_path}", file=sys.stderr)
            sys.exit(1)

        print(f"\n=== Parsing slides ({args.lang}) ===")
        slides = parse_slides(slides_path, args.lang)
        print(f"Found {len(slides)} slide groups:")
        for sg in slides:
            print(f"  [{sg.index}] ({sg.slide_type}) {sg.title}")
            print(f"       text: {sg.text_content[:80]}...")

        print(f"\n=== OCR on top {min(len(candidates), 10)} candidates ===")
        frame_dict = {round(ts, 2): frame for ts, frame in frames}

        ocr_results = []
        for ts, score, conf in candidates[:10]:
            closest_ts = min(frame_dict.keys(), key=lambda t: abs(t - ts))
            frame = frame_dict[closest_ts]

            ocr_text = ocr_frame(frame)
            matches = fuzzy_match_slide(ocr_text, slides)

            minutes = int(ts // 60)
            seconds = ts % 60
            best_match = matches[0] if matches else (-1, 0)

            print(f"\n  @{minutes:02d}:{seconds:05.2f} (diff={score:.4f}):")
            print(f"    OCR text: {ocr_text[:100]}...")
            print(f"    Best match: slide [{best_match[0]}] score={best_match[1]:.1f}")
            if len(matches) > 1:
                print(f"    Runner-up: slide [{matches[1][0]}] score={matches[1][1]:.1f}")

            ocr_results.append({
                "timestamp": ts,
                "diff_score": score,
                "confidence": conf,
                "ocr_text": ocr_text[:200],
                "best_slide": best_match[0],
                "best_score": best_match[1],
            })

        # Save OCR results
        results_path = output_dir / f"{stem}_ocr_results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(ocr_results, f, indent=2, ensure_ascii=False)
        print(f"\nOCR results saved to {results_path}")

    # Save diff data as JSON for further analysis
    data_path = output_dir / f"{stem}_framediff.json"
    with open(data_path, "w") as f:
        json.dump({
            "video": str(video_path),
            "fps": args.fps,
            "diffs": [{"timestamp": ts, "score": s} for ts, s in diffs],
            "candidates": [
                {"timestamp": ts, "score": s, "confidence": c}
                for ts, s, c in candidates
            ],
        }, f, indent=2)
    print(f"\nDiff data saved to {data_path}")


if __name__ == "__main__":
    main()
