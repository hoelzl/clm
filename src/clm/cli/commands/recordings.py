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


@recordings_group.command()
@click.argument("root_dir", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--raw-suffix", default=None, help="Override raw file suffix (default: from config or --RAW)."
)
@click.option("--dry-run", is_flag=True, help="Show pending pairs without assembling.")
def assemble(root_dir: Path, raw_suffix: str | None, dry_run: bool):
    """Assemble processed recordings (mux video + audio, archive originals).

    Scans ROOT_DIR/to-process/ for matched video + audio pairs
    (e.g. topic--RAW.mp4 + topic--RAW.wav), muxes them into
    ROOT_DIR/final/, and archives the originals to ROOT_DIR/archive/.
    """
    from clm.recordings.workflow.directories import (
        find_pending_pairs,
        to_process_dir,
        validate_root,
    )

    if raw_suffix is None:
        raw_suffix = _get_raw_suffix()

    errors = validate_root(root_dir)
    if errors:
        for err in errors:
            console.print(f"[red]{err}[/red]")
        console.print(
            "\n[yellow]Run with a valid recordings root, or create the structure first.[/yellow]"
        )
        raise SystemExit(1)

    pairs = find_pending_pairs(to_process_dir(root_dir), raw_suffix=raw_suffix)

    if not pairs:
        console.print("[yellow]No pending video + audio pairs found.[/yellow]")
        return

    console.print(f"[bold]Found {len(pairs)} pair(s) ready for assembly:[/bold]")
    for pair in pairs:
        console.print(f"  {pair.relative_dir / pair.video.name}")

    if dry_run:
        console.print("\n[dim]Dry run — no files changed.[/dim]")
        return

    from clm.recordings.workflow.assembler import assemble_all

    console.print()

    def on_pair(i: int, pair, total: int) -> None:
        console.print(f"[{i + 1}/{total}] Assembling {pair.video.name}...")

    result = assemble_all(root_dir, raw_suffix=raw_suffix, on_pair=on_pair)

    console.print(f"\n{result.summary()}")
    if result.failed:
        raise SystemExit(1)


@recordings_group.command("serve")
@click.argument(
    "root_dir",
    type=click.Path(path_type=Path),
)
@click.option("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1).")
@click.option("--port", type=int, default=8008, help="Port to bind to (default: 8008).")
@click.option(
    "--spec-file",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="CLM course spec XML file for lecture listing.",
)
@click.option("--obs-host", default=None, help="OBS WebSocket host (default: from config).")
@click.option(
    "--obs-port", type=int, default=None, help="OBS WebSocket port (default: from config)."
)
@click.option("--obs-password", default=None, help="OBS WebSocket password.")
@click.option("--no-browser", is_flag=True, help="Do not auto-open browser.")
def serve_recordings(
    root_dir: Path,
    host: str,
    port: int,
    spec_file: Path | None,
    obs_host: str | None,
    obs_port: int | None,
    obs_password: str | None,
    no_browser: bool,
):
    """Start the recordings dashboard web UI.

    Launches an HTMX-based web dashboard for the recording workflow.
    Connects to OBS Studio via WebSocket, lets you arm topics for
    recording, and shows pending pairs and assembly status.

    ROOT_DIR is the recordings root containing to-process/, final/,
    and archive/ subdirectories (created automatically if missing).
    """
    try:
        import uvicorn
    except ImportError as exc:
        console.print("[red]uvicorn is required. It should be a core CLM dependency.[/red]")
        raise SystemExit(1) from exc

    from clm.recordings.web.app import create_app

    # Resolve OBS settings from CLI args or CLM config
    cfg_obs_host, cfg_obs_port, cfg_obs_password = _get_obs_config()
    obs_host = obs_host or cfg_obs_host
    obs_port = obs_port or cfg_obs_port
    obs_password = obs_password if obs_password is not None else cfg_obs_password
    raw_suffix = _get_raw_suffix()

    # Resolve watcher settings from CLM config
    cfg_backend, cfg_stab_interval, cfg_stab_count = _get_watcher_config()
    cfg_auphonic_key, cfg_auphonic_preset = _get_auphonic_config()

    app = create_app(
        recordings_root=root_dir,
        obs_host=obs_host,
        obs_port=obs_port,
        obs_password=obs_password,
        spec_file=spec_file,
        raw_suffix=raw_suffix,
        processing_backend=cfg_backend,
        stability_check_interval=cfg_stab_interval,
        stability_check_count=cfg_stab_count,
        auphonic_api_key=cfg_auphonic_key,
        auphonic_preset=cfg_auphonic_preset,
    )

    url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}"

    if not no_browser:
        import webbrowser

        console.print(f"Opening browser to {url}...")
        webbrowser.open(url)

    console.print(f"[bold]Recordings dashboard:[/bold] {url}")
    console.print(f"[bold]Recordings root:[/bold]     {root_dir}")
    console.print("Press CTRL+C to stop")

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except Exception as exc:
        console.print(f"[red]Server error: {exc}[/red]")
        raise SystemExit(1) from exc


def _get_obs_config() -> tuple[str, int, str]:
    """Get OBS connection settings from CLM config, falling back to defaults."""
    try:
        from clm.infrastructure.config import get_config

        cfg = get_config().recordings
        return cfg.obs_host, cfg.obs_port, cfg.obs_password
    except Exception:
        return "localhost", 4455, ""


def _get_raw_suffix() -> str:
    """Get the raw suffix from CLM config, falling back to the default."""
    try:
        from clm.infrastructure.config import get_config

        suffix = get_config().recordings.raw_suffix
        if suffix:
            return suffix
    except Exception:
        pass
    from clm.recordings.workflow.naming import DEFAULT_RAW_SUFFIX

    return DEFAULT_RAW_SUFFIX


def _get_watcher_config() -> tuple[str, float, int]:
    """Get watcher settings from CLM config, falling back to defaults."""
    try:
        from clm.infrastructure.config import get_config

        cfg = get_config().recordings
        return cfg.processing_backend, cfg.stability_check_interval, cfg.stability_check_count
    except Exception:
        return "onnx", 2.0, 3


def _get_auphonic_config() -> tuple[str, str]:
    """Get Auphonic api_key and preset from CLM config, falling back to empty."""
    try:
        from clm.infrastructure.config import get_config

        cfg = get_config().recordings.auphonic
        return cfg.api_key, cfg.preset
    except Exception:
        return "", ""


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


# ----------------------------------------------------------------------
# Phase C: backend/job/auphonic subcommands
# ----------------------------------------------------------------------


def _resolve_recordings_root(cli_root: Path | None) -> Path:
    """Resolve the recordings root directory.

    Priority: explicit CLI flag → ``recordings.root_dir`` from config →
    raise. Matches the Phase B factory pattern (callers pass a concrete
    Path). Reads config via :func:`_build_recordings_config` so tests can
    monkeypatch a single seam.
    """
    if cli_root is not None:
        return cli_root
    try:
        configured = _build_recordings_config().root_dir
    except Exception:  # pragma: no cover — defensive
        configured = ""
    if configured:
        return Path(configured)
    raise click.ClickException(
        "No recordings root. Pass --root <dir> or set "
        "recordings.root_dir in your CLM config "
        "(CLM_RECORDINGS__ROOT_DIR env var)."
    )


def _build_recordings_config():
    """Build a :class:`RecordingsConfig` from the current CLM config."""
    from clm.infrastructure.config import RecordingsConfig, get_config

    try:
        return get_config().recordings
    except Exception:
        return RecordingsConfig()


def _make_job_manager_for_root(root_dir: Path):
    """Construct a :class:`JobManager` wired to the active backend.

    Used by the CLI ``submit``/``jobs`` commands so they operate against
    the same state as the web dashboard. The returned manager loads any
    persisted jobs from ``<root_dir>/.clm/jobs.json`` on construction.
    """
    from clm.recordings.workflow.backends import make_backend
    from clm.recordings.workflow.event_bus import EventBus
    from clm.recordings.workflow.job_manager import JobManager
    from clm.recordings.workflow.job_store import DEFAULT_JOBS_FILE, JsonFileJobStore

    config = _build_recordings_config()
    backend = make_backend(config, root_dir=root_dir)
    store = JsonFileJobStore(root_dir / DEFAULT_JOBS_FILE)
    bus = EventBus()
    return JobManager(
        backend=backend,
        root_dir=root_dir,
        store=store,
        bus=bus,
        raw_suffix=config.raw_suffix,
    )


@recordings_group.command("backends")
def list_backends():
    """List available processing backends and their capabilities."""
    from clm.recordings.workflow.backends.auphonic import AuphonicBackend
    from clm.recordings.workflow.backends.external import ExternalAudioFirstBackend
    from clm.recordings.workflow.backends.onnx import OnnxAudioFirstBackend

    entries = [
        ("onnx", OnnxAudioFirstBackend.capabilities),
        ("external", ExternalAudioFirstBackend.capabilities),
        ("auphonic", AuphonicBackend.capabilities),
    ]

    active = _build_recordings_config().processing_backend

    table = Table(title="Recording Processing Backends")
    table.add_column("Name", style="cyan")
    table.add_column("Active", style="green")
    table.add_column("Display name")
    table.add_column("Model")
    table.add_column("Features")

    for name, caps in entries:
        model_bits = []
        if caps.video_in_video_out:
            model_bits.append("video-in/video-out")
        else:
            model_bits.append("audio-first")
        model_bits.append("async" if not caps.is_synchronous else "sync")
        if caps.requires_internet:
            model_bits.append("internet")
        if caps.requires_api_key:
            model_bits.append("api-key")

        features = []
        if caps.supports_cut_lists:
            features.append("cut lists")
        if caps.supports_filler_removal:
            features.append("filler removal")
        if caps.supports_silence_removal:
            features.append("silence removal")
        if caps.supports_transcript:
            features.append("transcript")
        if caps.supports_chapter_detection:
            features.append("chapters")

        table.add_row(
            name,
            "[bold green]✓[/bold green]" if name == active else "",
            caps.display_name,
            ", ".join(model_bits),
            ", ".join(features) or "[dim]—[/dim]",
        )

    console.print(table)
    console.print(f"\n[dim]Active backend from config: [bold]{active}[/bold][/dim]")


@recordings_group.command("submit")
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--root",
    "cli_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Recordings root (defaults to recordings.root_dir from config).",
)
@click.option(
    "--request-cut-list",
    is_flag=True,
    help="Ask the backend to produce a cut list (Auphonic only today).",
)
@click.option("--title", default=None, help="Metadata title override (defaults to filename stem).")
def submit_job(
    input_file: Path,
    cli_root: Path | None,
    request_cut_list: bool,
    title: str | None,
):
    """Submit INPUT_FILE to the configured processing backend.

    Wraps :meth:`JobManager.submit`. For synchronous backends (``onnx``,
    ``external``) this blocks until completion; for asynchronous backends
    (``auphonic``) it returns once the job is uploaded and processing
    has started, then the watcher or ``clm recordings jobs`` can be used
    to track progress.
    """
    from clm.recordings.workflow.jobs import JobState, ProcessingOptions

    root = _resolve_recordings_root(cli_root)
    manager = _make_job_manager_for_root(root)

    options = ProcessingOptions(
        request_cut_list=request_cut_list,
        title=title,
    )

    console.print(f"[bold]Input:[/bold]   {input_file}")
    console.print(f"[bold]Backend:[/bold] {manager.backend.capabilities.display_name}")
    console.print()

    try:
        job = manager.submit(input_file, options=options)
    except Exception as exc:
        console.print(f"[red]Submit failed: {exc}[/red]")
        raise SystemExit(1) from exc

    console.print(f"[bold]Job id:[/bold] {job.id}")
    console.print(f"[bold]State:[/bold]  {job.state.value}")
    if job.message:
        console.print(f"[bold]Status:[/bold] {job.message}")

    if job.state == JobState.COMPLETED:
        console.print(f"\n[green]Done.[/green] Output: {job.final_path}")
    elif job.state == JobState.FAILED:
        console.print(f"\n[red]Failed: {job.error}[/red]")
        manager.shutdown()
        raise SystemExit(1)
    else:
        console.print(
            "\n[dim]Job is running on the backend. "
            "Use 'clm recordings jobs' to check progress.[/dim]"
        )

    manager.shutdown()


@recordings_group.group("jobs")
def jobs_group():
    """List and manage recording processing jobs."""


@jobs_group.command("list")
@click.option(
    "--root",
    "cli_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Recordings root (defaults to recordings.root_dir from config).",
)
@click.option("--all", "show_all", is_flag=True, help="Include terminal jobs (completed/failed).")
@click.option("-n", "--limit", type=int, default=20, help="Max number of jobs to show.")
def list_jobs(cli_root: Path | None, show_all: bool, limit: int):
    """List recording processing jobs from the on-disk store."""
    root = _resolve_recordings_root(cli_root)
    manager = _make_job_manager_for_root(root)

    try:
        jobs = manager.list_jobs()
        if not show_all:
            jobs = [j for j in jobs if not j.is_terminal]
        jobs = jobs[:limit]

        if not jobs:
            console.print("[yellow]No jobs found.[/yellow] Use 'clm recordings submit' to add one.")
            return

        # Only show the "Last poll error" column if at least one job
        # actually has one — otherwise it's dead space for the common
        # healthy-state listing.
        show_poll_error_column = any(j.last_poll_error for j in jobs)

        table = Table(title=f"Jobs under {root}")
        table.add_column("ID", style="dim")
        table.add_column("Backend", style="cyan")
        table.add_column("State")
        table.add_column("Progress")
        table.add_column("Input")
        table.add_column("Message")
        if show_poll_error_column:
            table.add_column("Last poll error", style="yellow")

        for job in jobs:
            state_style = {
                "completed": "green",
                "failed": "red",
                "cancelled": "yellow",
            }.get(job.state.value, "blue")
            row = [
                job.id[:8],
                job.backend_name,
                f"[{state_style}]{job.state.value}[/{state_style}]",
                f"{int(job.progress * 100)}%",
                job.raw_path.name,
                (job.error or job.message or "")[:60],
            ]
            if show_poll_error_column:
                row.append((job.last_poll_error or "")[:60])
            table.add_row(*row)

        console.print(table)
    finally:
        manager.shutdown()


def _resolve_job_by_prefix(manager, job_id: str):
    """Resolve *job_id* against the manager's jobs, allowing a prefix.

    Exits with a helpful message on no-match or ambiguous-match so
    every CLI subcommand that takes a job id behaves identically.
    """
    candidates = [j for j in manager.list_jobs() if j.id.startswith(job_id)]
    if not candidates:
        console.print(f"[red]No job matching id prefix {job_id!r}.[/red]")
        raise SystemExit(1)
    if len(candidates) > 1:
        console.print(f"[red]Ambiguous prefix {job_id!r} matches {len(candidates)} jobs:[/red]")
        for job in candidates:
            console.print(f"  {job.id}  {job.state.value}  {job.raw_path.name}")
        raise SystemExit(1)
    return candidates[0]


@jobs_group.command("cancel")
@click.argument("job_id")
@click.option(
    "--root",
    "cli_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Recordings root (defaults to recordings.root_dir from config).",
)
def cancel_job(job_id: str, cli_root: Path | None):
    """Cancel an in-flight job by id (prefix matches are accepted)."""
    root = _resolve_recordings_root(cli_root)
    manager = _make_job_manager_for_root(root)

    try:
        target = _resolve_job_by_prefix(manager, job_id)
        job = manager.cancel(target.id)
        if job is None:
            console.print(f"[red]Unknown job id {job_id!r}.[/red]")
            raise SystemExit(1)
        console.print(f"[green]Cancelled[/green] job {job.id} (state={job.state.value})")
    finally:
        manager.shutdown()


def _render_poll_result_table(polled, *, title: str) -> Table:
    """Build a Rich Table summarizing the result of a poll tick."""
    table = Table(title=title)
    table.add_column("ID", style="dim")
    table.add_column("State")
    table.add_column("Progress")
    table.add_column("Message")
    table.add_column("Last poll error", style="yellow")
    for job in polled:
        state_style = {
            "completed": "green",
            "failed": "red",
            "cancelled": "yellow",
        }.get(job.state.value, "blue")
        table.add_row(
            job.id[:8],
            f"[{state_style}]{job.state.value}[/{state_style}]",
            f"{int(job.progress * 100)}%",
            (job.error or job.message or "")[:60],
            (job.last_poll_error or "")[:60],
        )
    return table


@jobs_group.command("fail")
@click.argument("job_id")
@click.option(
    "--root",
    "cli_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Recordings root (defaults to recordings.root_dir from config).",
)
@click.option(
    "--reason",
    default="Manually marked failed by user",
    show_default=True,
    help="Error text stored on the job (shown by 'jobs list').",
)
def fail_job(job_id: str, cli_root: Path | None, reason: str):
    """Manually mark JOB_ID as FAILED without touching the backend.

    Unlike ``jobs cancel`` — which deletes the remote production so
    no credits are burned — ``fail`` only changes the local job
    state. Use this for rescuing stuck jobs where the remote work is
    actually fine (so you still want to download/inspect it) but the
    local poll loop is wedged with repeated transient errors.

    Refuses already-terminal jobs (completed/failed/cancelled) so you
    can't accidentally overwrite a real completion.
    """
    root = _resolve_recordings_root(cli_root)
    manager = _make_job_manager_for_root(root)

    try:
        target = _resolve_job_by_prefix(manager, job_id)
        if target.is_terminal:
            console.print(
                f"[yellow]Job {target.id[:8]} is already {target.state.value}; "
                "refusing to overwrite.[/yellow]"
            )
            raise SystemExit(1)

        updated = manager.mark_failed(target.id, reason=reason)
        if updated is None:
            # Race: something else changed the job between the prefix
            # resolve and the mark_failed call. Rare, but surface it.
            console.print(
                f"[red]Could not mark job {target.id[:8]} as failed "
                "(state changed concurrently?).[/red]"
            )
            raise SystemExit(1)
        console.print(
            f"[red]Marked failed[/red] job {updated.id[:8]} "
            f"(reason: {updated.error}). The backend production was "
            "[bold]not[/bold] cancelled — use 'jobs cancel' if you "
            "also want to delete it upstream."
        )
    finally:
        manager.shutdown()


@jobs_group.command("poll")
@click.argument("job_id", required=False)
@click.option(
    "--root",
    "cli_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Recordings root (defaults to recordings.root_dir from config).",
)
@click.option(
    "-w",
    "--watch",
    type=int,
    default=1,
    show_default=True,
    help="Run this many poll cycles back-to-back, sleeping --interval "
    "seconds between ticks. Stops early when no jobs remain in-flight.",
)
@click.option(
    "-i",
    "--interval",
    type=float,
    default=30.0,
    show_default=True,
    help="Seconds to sleep between ticks when --watch > 1.",
)
def poll_jobs(
    job_id: str | None,
    cli_root: Path | None,
    watch: int,
    interval: float,
):
    """Run one or more poll cycles and print job state after each tick.

    Without an argument, polls every in-flight job. With JOB_ID (prefix
    match accepted), polls only that job. Useful for asynchronous
    backends like Auphonic when you don't want to run the full
    dashboard just to check on progress — with ``--watch 1`` (the
    default) it runs a single tick and exits, with higher values it
    loops for a bounded amount of time.

    Transient errors (network blips, HTTP 5xx, schema drift) are
    recorded on the job's ``last_poll_error`` but do NOT mark the job
    failed; the next poll tick will retry. Permanent errors (HTTP
    401/403/404/410, explicit Auphonic ERROR status) mark the job
    failed immediately.
    """
    import time as _time

    if watch < 1:
        raise click.BadParameter("--watch must be >= 1")

    root = _resolve_recordings_root(cli_root)
    manager = _make_job_manager_for_root(root)

    try:
        target_id: str | None = None
        if job_id is not None:
            target = _resolve_job_by_prefix(manager, job_id)
            if target.is_terminal:
                console.print(
                    f"[yellow]Job {target.id[:8]} is already {target.state.value}; "
                    "nothing to poll.[/yellow]"
                )
                return
            target_id = target.id

        for tick in range(1, watch + 1):
            polled = manager.poll_once(job_id=target_id)
            if not polled:
                if target_id is not None:
                    console.print(
                        "[yellow]Job is not in a pollable state "
                        "(must be processing/uploading/downloading).[/yellow]"
                    )
                else:
                    console.print("[dim]No in-flight jobs to poll.[/dim]")
                return

            title = "Poll result" if watch == 1 else f"Poll tick {tick}/{watch}"
            console.print(_render_poll_result_table(polled, title=title))

            # If every polled job reached terminal state, stop early —
            # nothing more to do. This matters most when watching a
            # single JOB_ID that has just completed.
            if all(job.is_terminal for job in polled):
                if watch > 1:
                    console.print("[dim]All polled jobs are terminal; stopping.[/dim]")
                return

            # Sleep between ticks, but not after the last one.
            if tick < watch:
                _time.sleep(interval)
    finally:
        manager.shutdown()


@jobs_group.command("wait")
@click.argument("job_id")
@click.option(
    "--root",
    "cli_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Recordings root (defaults to recordings.root_dir from config).",
)
@click.option(
    "--interval",
    type=float,
    default=30.0,
    show_default=True,
    help="Seconds between poll ticks.",
)
@click.option(
    "--timeout",
    type=float,
    default=None,
    help="Give up after this many seconds (default: wait forever).",
)
def wait_job(
    job_id: str,
    cli_root: Path | None,
    interval: float,
    timeout: float | None,
):
    """Block until JOB_ID reaches a terminal state.

    Runs poll cycles at ``--interval`` (default 30s) and prints each
    state transition, exiting when the job is completed, failed, or
    cancelled. Use ``--timeout`` to give up after N seconds. A simple
    alternative to ``clm recordings serve`` when you only care about
    one specific job.

    Transient poll errors are surfaced but do NOT end the wait — the
    next tick retries. Use Ctrl+C to abort.
    """
    import time

    from clm.recordings.workflow.jobs import JobState

    root = _resolve_recordings_root(cli_root)
    manager = _make_job_manager_for_root(root)

    try:
        target = _resolve_job_by_prefix(manager, job_id)
        if target.is_terminal:
            console.print(f"[yellow]Job {target.id[:8]} is already {target.state.value}.[/yellow]")
            return

        started_at = time.monotonic()
        last_state: JobState | None = None
        last_message: str | None = None
        last_poll_error: str | None = None

        while True:
            polled = manager.poll_once(job_id=target.id)
            current = manager.get(target.id)
            if current is None:
                console.print(f"[red]Job {target.id[:8]} disappeared from store.[/red]")
                raise SystemExit(1)

            # Print on any state/message/error transition.
            if (
                current.state != last_state
                or current.message != last_message
                or current.last_poll_error != last_poll_error
            ):
                state_style = {
                    "completed": "green",
                    "failed": "red",
                    "cancelled": "yellow",
                }.get(current.state.value, "blue")
                line = (
                    f"[{state_style}]{current.state.value}[/{state_style}]"
                    f"  {int(current.progress * 100):3d}%  {current.message or ''}"
                )
                if current.last_poll_error:
                    line += f"  [yellow](transient: {current.last_poll_error[:60]})[/yellow]"
                console.print(line)
                last_state = current.state
                last_message = current.message
                last_poll_error = current.last_poll_error

            if current.is_terminal:
                if current.state == JobState.COMPLETED:
                    console.print(f"\n[green]Done.[/green] Output: {current.final_path}")
                elif current.state == JobState.FAILED:
                    console.print(f"\n[red]Failed: {current.error}[/red]")
                    raise SystemExit(1)
                else:
                    console.print(f"\n[yellow]Terminated: {current.state.value}[/yellow]")
                return

            if polled == []:
                # Nothing happened — job state didn't allow a poll. That
                # means someone else (dashboard) moved it out of an
                # in-flight state between our get() and our poll. Bail.
                console.print(
                    f"[yellow]Job {target.id[:8]} is no longer in a pollable state.[/yellow]"
                )
                return

            if timeout is not None and (time.monotonic() - started_at) >= timeout:
                console.print(
                    f"[yellow]Timed out after {timeout}s; job is still {current.state.value}.[/yellow]"
                )
                raise SystemExit(2)

            time.sleep(interval)
    finally:
        manager.shutdown()


@jobs_group.command("prune")
@click.option(
    "--root",
    "cli_root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Recordings root (defaults to recordings.root_dir from config).",
)
@click.option(
    "--state",
    "states",
    type=click.Choice(["completed", "failed", "cancelled", "terminal"]),
    multiple=True,
    default=("failed", "cancelled"),
    help=(
        "Job state(s) to prune. May be given multiple times. "
        "'terminal' expands to completed+failed+cancelled. "
        "Default: failed+cancelled."
    ),
)
@click.option(
    "--id",
    "job_id",
    default=None,
    help="Prune only the job with this id (prefix match). "
    "Overrides --state; refuses to prune in-flight jobs.",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    help="Skip the confirmation prompt.",
)
def prune_jobs(
    cli_root: Path | None,
    states: tuple[str, ...],
    job_id: str | None,
    assume_yes: bool,
):
    """Delete terminal jobs from the on-disk store.

    In-flight jobs (queued/uploading/processing/downloading) are
    never pruned — cancel them first with ``jobs cancel`` if you
    want them gone.
    """
    from clm.recordings.workflow.jobs import JobState

    root = _resolve_recordings_root(cli_root)
    manager = _make_job_manager_for_root(root)

    try:
        if job_id is not None:
            target = _resolve_job_by_prefix(manager, job_id)
            if not target.is_terminal:
                console.print(
                    f"[red]Refusing to prune in-flight job {target.id[:8]} "
                    f"(state={target.state.value}). Cancel it first with "
                    f"'clm recordings jobs cancel {target.id[:8]}'.[/red]"
                )
                raise SystemExit(1)
            victims = [target]
        else:
            # Expand 'terminal' shorthand.
            wanted_states: set[JobState] = set()
            for s in states:
                if s == "terminal":
                    wanted_states.update({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED})
                else:
                    wanted_states.add(JobState(s))
            victims = [j for j in manager.list_jobs() if j.state in wanted_states]

        if not victims:
            console.print("[dim]No matching jobs to prune.[/dim]")
            return

        table = Table(title=f"About to prune {len(victims)} job(s)")
        table.add_column("ID", style="dim")
        table.add_column("State")
        table.add_column("Input")
        table.add_column("Message")
        for job in victims:
            state_style = {
                "completed": "green",
                "failed": "red",
                "cancelled": "yellow",
            }.get(job.state.value, "blue")
            table.add_row(
                job.id[:8],
                f"[{state_style}]{job.state.value}[/{state_style}]",
                job.raw_path.name,
                (job.error or job.message or "")[:60],
            )
        console.print(table)

        if not assume_yes:
            if not click.confirm(f"Delete these {len(victims)} job(s) from the store?"):
                console.print("[yellow]Aborted.[/yellow]")
                return

        deleted = 0
        for job in victims:
            if manager.delete_job(job.id):
                deleted += 1
        console.print(f"[green]Pruned[/green] {deleted} job(s).")
    finally:
        manager.shutdown()


# ----- Auphonic-specific helpers -------------------------------------


@recordings_group.group("auphonic")
def auphonic_group():
    """Auphonic-specific commands (presets, accounts, …)."""


#: JSON payload for the managed preset created by ``preset sync``.
#: Mirrors :data:`~clm.recordings.workflow.backends.auphonic.DEFAULT_INLINE_ALGORITHMS`
#: so "preset mode" and "inline mode" behave identically.
_MANAGED_PRESET_PAYLOAD: dict = {
    "preset_name": "CLM Lecture Recording",
    "short_name": "clm-lecture",
    "algorithms": {
        "denoise": True,
        "denoisemethod": "dynamic",
        "denoiseamount": 0,
        "leveler": True,
        "normloudness": True,
        "loudnesstarget": -16,
        "filtering": True,
        "filler_cutter": False,
        "silence_cutter": False,
    },
    "output_files": [
        {"format": "video", "ending": "mp4"},
    ],
}


def _build_auphonic_client():
    """Build an :class:`AuphonicClient` from the user's CLM config."""
    from clm.recordings.workflow.backends.auphonic_client import AuphonicClient

    config = _build_recordings_config().auphonic
    if not config.api_key:
        raise click.ClickException(
            "recordings.auphonic.api_key is not set. Configure it via TOML "
            "or CLM_RECORDINGS__AUPHONIC__API_KEY before running auphonic commands."
        )
    return AuphonicClient(
        api_key=config.api_key,
        base_url=config.base_url,
        chunk_size=config.upload_chunk_size,
    )


@auphonic_group.group("preset")
def preset_group():
    """Manage the CLM-managed Auphonic preset."""


@preset_group.command("list")
def list_presets():
    """List presets in the authenticated Auphonic account."""
    client = _build_auphonic_client()
    presets = client.list_presets()
    if not presets:
        console.print("[yellow]No presets found in your Auphonic account.[/yellow]")
        return

    table = Table(title="Auphonic presets")
    table.add_column("Short name", style="cyan")
    table.add_column("Display name")
    table.add_column("UUID", style="dim")

    for preset in presets:
        table.add_row(preset.short_name or "-", preset.preset_name, preset.uuid)
    console.print(table)


@preset_group.command("sync")
def sync_preset():
    """Create or update the managed ``CLM Lecture Recording`` preset.

    Idempotent: if a preset named ``CLM Lecture Recording`` already
    exists, it is updated in place; otherwise a new one is created. The
    preset's algorithm config mirrors the inline defaults so both modes
    produce the same output.
    """
    from clm.recordings.workflow.backends.auphonic import DEFAULT_MANAGED_PRESET_NAME

    client = _build_auphonic_client()

    console.print("[bold]Fetching existing presets…[/bold]")
    existing = client.list_presets()
    match = next(
        (p for p in existing if p.preset_name == DEFAULT_MANAGED_PRESET_NAME),
        None,
    )

    if match is None:
        console.print(f"Creating new preset {DEFAULT_MANAGED_PRESET_NAME!r}…")
        preset = client.create_preset(preset_data=_MANAGED_PRESET_PAYLOAD)
        console.print(f"[green]Created[/green] preset {preset.preset_name} ({preset.uuid})")
    else:
        console.print(f"Updating existing preset {DEFAULT_MANAGED_PRESET_NAME!r}…")
        preset = client.update_preset(match.uuid, preset_data=_MANAGED_PRESET_PAYLOAD)
        console.print(f"[green]Updated[/green] preset {preset.preset_name} ({preset.uuid})")

    console.print(
        f"\n[dim]Set [bold]recordings.auphonic.preset = "
        f"{DEFAULT_MANAGED_PRESET_NAME!r}[/bold] in your CLM config "
        f"to reference this preset by name.[/dim]"
    )
