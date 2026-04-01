"""Recording management commands for CLM courses.

This module provides the ``clm recordings`` command group with subcommands
for checking dependencies, processing recordings, viewing status, and
starting the recording manager web UI.

Requires the ``[recordings]`` extra (for the web UI server).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()


@click.group("recordings")
def recordings_group():
    """Manage video recordings for courses.

    Record, process, and track educational video recordings
    tied to CLM course lecture structures.
    """


@recordings_group.command()
def check():
    """Check that recording dependencies (ffmpeg, onnxruntime) are installed."""
    from clm.recordings.processing.utils import check_dependencies

    deps = check_dependencies()
    all_ok = True

    table = Table(title="Recording Dependencies")
    table.add_column("Tool", style="cyan")
    table.add_column("Status")
    table.add_column("Info")

    for name, value in deps.items():
        if value:
            table.add_row(name, "[green]found[/green]", str(value))
        else:
            table.add_row(name, "[red]NOT FOUND[/red]", "")
            all_ok = False

    console.print(table)

    if all_ok:
        console.print("\n[green]All dependencies found.[/green]")
    else:
        console.print("\n[red]Some dependencies are missing. See above for details.[/red]")
        raise SystemExit(1)


@recordings_group.command()
@click.argument("input_file", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None, help="Output file.")
@click.option(
    "-c",
    "--config",
    "config_file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Config JSON file.",
)
@click.option("--keep-temp", is_flag=True, help="Keep intermediate files for debugging.")
def process(input_file: Path, output: Path | None, config_file: Path | None, keep_temp: bool):
    """Process a single recording through the audio pipeline.

    Extracts audio, applies DeepFilterNet3 noise reduction (ONNX),
    FFmpeg filters (highpass, compressor, loudness normalization),
    and muxes back.
    """
    from clm.recordings.processing.pipeline import ProcessingPipeline

    config = _load_pipeline_config(config_file)
    if keep_temp:
        config.keep_temp = True

    if output is None:
        output = input_file.parent / f"{input_file.stem}_final.{config.output_extension}"

    console.print(f"[bold]Input:[/bold]  {input_file}")
    console.print(f"[bold]Output:[/bold] {output}")
    console.print()

    pipeline = ProcessingPipeline(config)
    start = time.monotonic()

    def on_step(step: int, name: str, total: int) -> None:
        console.print(f"  [{step}/{total}] {name}...")

    result = pipeline.process(input_file, output, on_step=on_step)
    elapsed = time.monotonic() - start

    console.print()
    if result.success:
        in_size = input_file.stat().st_size / (1024 * 1024)
        out_size = output.stat().st_size / (1024 * 1024)
        console.print(f"[green]Done in {elapsed:.1f}s[/green]")
        console.print(f"  Output:   {result.output_file}")
        console.print(f"  Duration: {result.duration_seconds:.0f}s")
        console.print(f"  Size:     {in_size:.1f} MB -> {out_size:.1f} MB")
    else:
        console.print(f"[red]Failed: {result.error}[/red]")
        raise SystemExit(1)


@recordings_group.command()
@click.argument("input_dir", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o", "--output-dir", type=click.Path(path_type=Path), default=None, help="Output directory."
)
@click.option(
    "-c",
    "--config",
    "config_file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Config JSON file.",
)
@click.option("-r", "--recursive", is_flag=True, help="Search subdirectories.")
def batch(input_dir: Path, output_dir: Path | None, config_file: Path | None, recursive: bool):
    """Batch-process all recordings in a directory.

    Finds video files (.mkv, .mp4, .avi, .mov, .webm, .ts) and processes
    each through the audio pipeline. Skips files that already have output.
    """
    from clm.recordings.processing.batch import process_batch

    config = _load_pipeline_config(config_file)
    if output_dir is None:
        output_dir = input_dir / "processed"

    console.print(f"[bold]Input:[/bold]  {input_dir}")
    console.print(f"[bold]Output:[/bold] {output_dir}")
    console.print()

    def on_file(i: int, f: Path, total: int) -> None:
        console.print(f"\n[bold][{i + 1}/{total}] {f.name}[/bold]")

    def on_step(step: int, name: str, total: int) -> None:
        console.print(f"  [{step}/{total}] {name}...")

    start = time.monotonic()
    result = process_batch(
        input_dir,
        output_dir,
        config=config,
        recursive=recursive,
        on_file=on_file,
        on_step=on_step,
    )
    elapsed = time.monotonic() - start

    console.print(f"\n{result.summary()}")
    console.print(f"Total time: {elapsed:.1f}s")

    if result.failed:
        raise SystemExit(1)


@recordings_group.command()
@click.argument("course_id")
def status(course_id: str):
    """Show recording status for a course.

    Displays a table of lectures with their recording status,
    including file paths and part counts.
    """
    from clm.recordings.state import load_state

    state = load_state(course_id)
    if state is None:
        console.print(f"[yellow]No recording state found for course '{course_id}'.[/yellow]")
        console.print(
            "Initialize recording state first via the web UI or by importing a spec file."
        )
        raise SystemExit(1)

    recorded, total = state.progress
    console.print(f"[bold]Course:[/bold] {course_id}")
    console.print(f"[bold]Progress:[/bold] {recorded}/{total} lectures recorded")
    console.print(
        f"[bold]Continue mode:[/bold] {'on' if state.continue_current_lecture else 'off'}"
    )
    console.print()

    table = Table(title=f"Lectures — {course_id}")
    table.add_column("#", style="dim")
    table.add_column("Lecture ID", style="cyan")
    table.add_column("Name")
    table.add_column("Parts", style="green")
    table.add_column("Status")

    for i, lecture in enumerate(state.lectures):
        status_str = ""
        if lecture.parts:
            statuses = [p.status for p in lecture.parts]
            if all(s == "processed" for s in statuses):
                status_str = "[green]processed[/green]"
            elif any(s == "failed" for s in statuses):
                status_str = "[red]failed[/red]"
            elif any(s == "processing" for s in statuses):
                status_str = "[yellow]processing[/yellow]"
            else:
                status_str = "[blue]pending[/blue]"
        else:
            status_str = "[dim]unrecorded[/dim]"

        marker = ""
        if i == state.next_lecture_index:
            marker = " [bold yellow]*[/bold yellow]"

        table.add_row(
            str(i + 1),
            lecture.lecture_id + marker,
            lecture.display_name,
            str(len(lecture.parts)),
            status_str,
        )

    console.print(table)
    console.print("[dim]* = next lecture to record[/dim]")


@recordings_group.command()
@click.argument(
    "version_a",
    type=click.Path(exists=True, path_type=Path),
)
@click.argument(
    "version_b",
    type=click.Path(exists=True, path_type=Path),
)
@click.option("--label-a", default="Version A", help="Label for version A.")
@click.option("--label-b", default="Version B", help="Label for version B.")
@click.option(
    "--original",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Original unprocessed file.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=Path("comparison.html"),
    help="Output HTML file.",
)
@click.option("--start", type=float, default=0, help="Start time in seconds.")
@click.option("--duration", type=float, default=60, help="Duration in seconds (0=full).")
def compare(
    version_a: Path,
    version_b: Path,
    label_a: str,
    label_b: str,
    original: Path | None,
    output: Path,
    start: float,
    duration: float,
):
    """Generate an A/B audio comparison HTML page.

    Creates a self-contained HTML file with embedded audio players
    and a blind test mode for comparing two audio processing pipelines.
    """
    import tempfile

    from clm.recordings.processing.compare import (
        audio_to_base64,
        extract_audio_segment,
        generate_comparison_html,
    )
    from clm.recordings.processing.utils import find_ffmpeg

    ffmpeg = find_ffmpeg()

    with tempfile.TemporaryDirectory(prefix="clm_compare_") as tmp:
        tmp_dir = Path(tmp)

        # Extract segments
        console.print("[bold]Extracting audio segments...[/bold]")

        a_wav = tmp_dir / "a.wav"
        b_wav = tmp_dir / "b.wav"
        extract_audio_segment(
            ffmpeg, version_a, a_wav, start_seconds=start, duration_seconds=duration
        )
        extract_audio_segment(
            ffmpeg, version_b, b_wav, start_seconds=start, duration_seconds=duration
        )

        original_b64 = None
        if original:
            orig_wav = tmp_dir / "orig.wav"
            extract_audio_segment(
                ffmpeg, original, orig_wav, start_seconds=start, duration_seconds=duration
            )
            original_b64 = audio_to_base64(orig_wav)

        # Generate HTML
        html = generate_comparison_html(
            original_b64=original_b64,
            version_a_b64=audio_to_base64(a_wav),
            version_b_b64=audio_to_base64(b_wav),
            label_a=label_a,
            label_b=label_b,
        )

        output.write_text(html, encoding="utf-8")
        console.print(f"[green]Comparison page written to {output}[/green]")


def _load_pipeline_config(config_file: Path | None):
    """Load pipeline config from a JSON file or CLM config, falling back to defaults."""
    from clm.recordings.processing.config import AudioFilterConfig, PipelineConfig

    if config_file:
        return PipelineConfig.model_validate_json(config_file.read_text())

    # Try to load from CLM's global config
    try:
        from clm.infrastructure.config import get_config

        clm_config = get_config()
        rec = clm_config.recordings.processing
        return PipelineConfig(
            denoise_atten_lim=rec.denoise_atten_lim,
            sample_rate=rec.sample_rate,
            audio_bitrate=rec.audio_bitrate,
            video_codec=rec.video_codec,
            output_extension=rec.output_extension,
            audio_filters=AudioFilterConfig(
                highpass_freq=rec.highpass_freq,
                loudnorm_target=rec.loudnorm_target,
            ),
        )
    except Exception:
        return PipelineConfig()
