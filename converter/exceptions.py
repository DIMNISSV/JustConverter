# converter/exceptions.py
"""Custom exceptions for the video converter application."""


class FfmpegError(Exception):
    """Base exception for FFmpeg related errors."""
    pass


class FfprobeError(FfmpegError):
    """Exception raised for errors during ffprobe execution."""
    pass


class CommandGenerationError(FfmpegError):
    """Exception raised when FFmpeg command generation fails."""
    pass


class PreprocessingError(FfmpegError):
    """Exception raised during the preprocessing stage."""
    pass


class ConversionError(FfmpegError):
    """Exception raised during the main conversion stage."""
    pass
