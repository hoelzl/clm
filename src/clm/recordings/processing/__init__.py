"""Cross-platform video post-processing pipeline.

Replaces bash scripts with a Python implementation that works on
both Windows and Linux. The processing steps are:

1. Extract audio from the recording (FFmpeg)
2. Run DeepFilterNet noise reduction
3. Apply FFmpeg audio filters (highpass, compressor, loudness normalization)
4. Encode cleaned audio to AAC
5. Mux processed audio back into the original video
"""
