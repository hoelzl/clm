"""Pipeline configuration with sensible defaults for educational video processing."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AudioFilterConfig(BaseModel):
    """FFmpeg audio filter settings."""

    # Highpass filter frequency in Hz. Removes low-end rumble below this.
    # 80 is safe for voice; raise to 100-120 for persistent hum.
    highpass_freq: int = 80

    # Compressor settings — evens out volume differences.
    # These defaults are gentle and suitable for spoken word.
    compressor_attacks: float = 0.05
    compressor_decays: float = 0.3
    compressor_points: str = "-80/-80|-45/-30|-27/-20|0/-10"
    compressor_gain: float = 5.0

    # Loudness normalization (EBU R128).
    # -16 LUFS is standard for online video (YouTube, etc.)
    loudnorm_target: float = -16.0
    loudnorm_true_peak: float = -1.5
    loudnorm_lra: float = 11.0

    @property
    def compressor_filter(self) -> str:
        return (
            f"compand=attacks={self.compressor_attacks}"
            f":decays={self.compressor_decays}"
            f":points={self.compressor_points}"
            f":gain={self.compressor_gain}"
        )

    @property
    def loudnorm_filter(self) -> str:
        return (
            f"loudnorm=I={self.loudnorm_target}"
            f":TP={self.loudnorm_true_peak}"
            f":LRA={self.loudnorm_lra}"
        )


class PipelineConfig(BaseModel):
    """Full pipeline configuration."""

    # DeepFilterNet attenuation limit in dB.
    # 30-40 is moderate; 50+ is aggressive. Start with 35.
    deepfilter_atten_lim: float = 35.0

    # Audio sample rate. 48000 is standard for video.
    sample_rate: int = 48000

    # Output video codec: "copy" keeps original (fast, no quality loss).
    video_codec: str = "copy"

    # Output audio bitrate for AAC encoding.
    audio_bitrate: str = "192k"

    # Output container format.
    output_extension: str = "mp4"

    # Keep intermediate files for debugging.
    keep_temp: bool = False

    # Audio filter settings.
    audio_filters: AudioFilterConfig = Field(default_factory=AudioFilterConfig)
