"""A/B comparison utility for comparing audio processing pipelines.

Extracts audio from two versions of a recording (e.g., one processed with
iZotope RX, one with DeepFilterNet) and produces a side-by-side comparison
HTML page with audio players for quick quality evaluation.
"""

from __future__ import annotations

import base64
from pathlib import Path

from .utils import run_subprocess


def extract_audio_segment(
    ffmpeg: Path,
    input_file: Path,
    output_file: Path,
    *,
    start_seconds: float = 0,
    duration_seconds: float = 60,
) -> None:
    """Extract a segment of audio as WAV for comparison."""
    args: list[str | Path] = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        input_file,
    ]
    if start_seconds > 0:
        args.extend(["-ss", str(start_seconds)])
    if duration_seconds > 0:
        args.extend(["-t", str(duration_seconds)])
    args.extend(["-vn", "-acodec", "pcm_s16le", output_file])
    run_subprocess(args)


def audio_to_base64(audio_file: Path) -> str:
    """Read an audio file and return its base64-encoded content."""
    data = audio_file.read_bytes()
    return base64.b64encode(data).decode("ascii")


def generate_comparison_html(
    *,
    original_b64: str | None,
    version_a_b64: str,
    version_b_b64: str,
    label_a: str,
    label_b: str,
) -> str:
    """Generate an HTML page with embedded audio players for A/B comparison."""
    original_section = ""
    if original_b64:
        original_section = f"""
        <div class="player-card">
            <h2>Original (unprocessed)</h2>
            <audio controls preload="metadata">
                <source src="data:audio/wav;base64,{original_b64}" type="audio/wav">
            </audio>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Audio A/B Comparison</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1a1a2e; color: #e0e0e0;
            padding: 2rem; max-width: 900px; margin: 0 auto;
        }}
        h1 {{ text-align: center; margin-bottom: 2rem; color: #e0e0e0; }}
        .player-card {{
            background: #16213e; border-radius: 12px; padding: 1.5rem;
            margin-bottom: 1.5rem; border: 1px solid #0f3460;
        }}
        .player-card h2 {{
            margin-bottom: 1rem; font-size: 1.1rem; color: #a0c4ff;
        }}
        audio {{ width: 100%; }}
        .controls {{
            display: flex; gap: 1rem; justify-content: center;
            margin: 2rem 0; flex-wrap: wrap;
        }}
        button {{
            padding: 0.75rem 1.5rem; border: none; border-radius: 8px;
            cursor: pointer; font-size: 1rem; font-weight: 600;
            transition: transform 0.1s;
        }}
        button:active {{ transform: scale(0.97); }}
        .btn-a {{ background: #e94560; color: white; }}
        .btn-b {{ background: #533483; color: white; }}
        .btn-stop {{ background: #0f3460; color: white; }}
        .instructions {{
            text-align: center; color: #888; margin-bottom: 2rem;
            font-size: 0.95rem; line-height: 1.6;
        }}
        .blind-mode {{
            background: #0f3460; border-radius: 12px; padding: 1.5rem;
            margin-top: 2rem; text-align: center;
        }}
        .blind-mode h2 {{ color: #a0c4ff; margin-bottom: 1rem; }}
        .reveal {{ display: none; margin-top: 1rem; font-size: 1.1rem; }}
        .reveal.show {{ display: block; }}
    </style>
</head>
<body>
    <h1>Audio A/B Comparison</h1>
    <p class="instructions">
        Listen to each version and compare quality.<br>
        Use the blind test below for an unbiased comparison.
    </p>

    {original_section}

    <div class="player-card">
        <h2>Version A: {label_a}</h2>
        <audio id="audioA" controls preload="metadata">
            <source src="data:audio/wav;base64,{version_a_b64}" type="audio/wav">
        </audio>
    </div>

    <div class="player-card">
        <h2>Version B: {label_b}</h2>
        <audio id="audioB" controls preload="metadata">
            <source src="data:audio/wav;base64,{version_b_b64}" type="audio/wav">
        </audio>
    </div>

    <div class="blind-mode">
        <h2>Blind Test</h2>
        <p style="color: #888; margin-bottom: 1rem;">
            Versions are randomly assigned to X and Y. Listen, pick your
            favourite, then reveal.
        </p>
        <div class="controls">
            <button class="btn-a" onclick="playBlind('X')">Play X</button>
            <button class="btn-b" onclick="playBlind('Y')">Play Y</button>
            <button class="btn-stop" onclick="stopAll()">Stop</button>
        </div>
        <div class="controls">
            <button class="btn-a" onclick="reveal()">Reveal</button>
        </div>
        <div id="reveal" class="reveal"></div>
    </div>

    <script>
        const audioA = document.getElementById('audioA');
        const audioB = document.getElementById('audioB');
        const swap = Math.random() < 0.5;
        const blindX = swap ? audioB : audioA;
        const blindY = swap ? audioA : audioB;

        function stopAll() {{
            audioA.pause(); audioA.currentTime = 0;
            audioB.pause(); audioB.currentTime = 0;
        }}

        function playBlind(label) {{
            stopAll();
            (label === 'X' ? blindX : blindY).play();
        }}

        function reveal() {{
            const el = document.getElementById('reveal');
            el.innerHTML = swap
                ? 'X = {label_b}, Y = {label_a}'
                : 'X = {label_a}, Y = {label_b}';
            el.classList.add('show');
        }}
    </script>
</body>
</html>"""
