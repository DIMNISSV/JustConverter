# converter/config.py
"""
Default configuration values for ffmpeg settings used in the converter.
These values are used if specific parameters are not provided via the GUI.
"""

# --- Video Encoding ---
VIDEO_CODEC: str = "h264_nvenc"  # Default video codec (e.g., "libx264", "h264_nvenc", "hevc_nvenc")
VIDEO_PRESET: str = "fast"  # Default encoding preset (e.g., "ultrafast", "medium", "slow")
VIDEO_CQ: str = "24"  # Default Constant Quality level (lower is better, ignored if bitrate is set)
VIDEO_BITRATE: str = "0"  # Default video bitrate (e.g., "5000k", "0" to disable/use CQ)

# --- Audio Encoding ---
AUDIO_CODEC: str = "aac"  # Default audio codec (e.g., "aac", "opus", "copy")
AUDIO_BITRATE: str = "192k"  # Default audio bitrate (e.g., "128k", "192k", "256k")

# --- Ad/Overlay Settings ---
MOVING_SPEED: float = 2.0  # Default speed factor for moving logo (1.0 = one cycle over video duration)
MOVING_LOGO_RELATIVE_HEIGHT: float = 1 / 12  # Default height of moving logo relative to video height
MOVING_LOGO_ALPHA: float = 0.5  # Default alpha transparency of moving logo (0.0 to 1.0)
BANNER_TRACK_PIX_FMT: str = "yuva420p"  # Default pixel format for the temporary banner track (needs alpha)
BANNER_GAP_COLOR: str = "black@0.0"  # Default color for gaps in banner track (ffmpeg color syntax, black transparent)

# --- Hardware Acceleration ---
HWACCEL: str = "auto"  # Default hardware acceleration method (e.g., "auto", "cuda", "d3d11va", "none")

# --- Additional Parameters ---
ADDITIONAL_ENCODING: str = ""  # Default additional ffmpeg parameters for the main encoding command (e.g., "-tune film")
