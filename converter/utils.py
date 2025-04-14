# converter/utils.py
import os
import tempfile
import time
from typing import List, Optional


def generate_temp_filename(prefix: str, index: int, extension: str = "mkv") -> str:
    """
    Generates a unique temporary filename with a specified prefix, index, and extension.

    Args:
        prefix: A string prefix for the filename (e.g., "segment", "ad").
        index: An integer index to incorporate into the filename.
        extension: The desired file extension (default is "mkv").

    Returns:
        The full path to the generated temporary filename.
    """
    temp_dir = tempfile.gettempdir()
    # Use milliseconds for higher chance of uniqueness
    timestamp = int(time.time() * 1000)
    filename = f"{prefix}_{index}_{timestamp}.{extension}"
    return os.path.join(temp_dir, filename)


def escape_path_for_concat(path: str) -> str:
    """
    Prepares a file path string for safe inclusion in an FFmpeg concat demuxer list file.
    Handles backslashes and single quotes.

    Args:
        path: The original file path.

    Returns:
        The escaped path, suitable for the concat file format (e.g., 'C:/path/to/file\'s name.mkv').
    """
    # Replace backslashes with forward slashes (more cross-platform for FFmpeg)
    path = path.replace('\\', '/')
    # Escape single quotes by replacing ' with '\''
    path = path.replace("'", "'\\''")
    # Enclose the result in single quotes
    return f"'{path}'"


def timecode_to_seconds(tc: str) -> Optional[float]:
    """
    Converts an MM:SS or HH:MM:SS timecode string to seconds.

    Args:
        tc: The timecode string (e.g., "10:35", "01:22:15.5").

    Returns:
        The time in seconds as a float, or None if the format is invalid.
    """
    try:
        parts = list(map(float, tc.strip().split(':')))
        seconds = 0.0
        if len(parts) == 2:  # MM:SS
            seconds = parts[0] * 60 + parts[1]
        elif len(parts) == 3:  # HH:MM:SS
            seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            # Invalid number of parts
            return None
        # Ensure result is not negative
        return max(0.0, seconds)
    except (ValueError, TypeError, AttributeError):
        # Handles non-numeric parts, non-string input, etc.
        return None


def cleanup_temp_files(temp_files: List[str]):
    """
    Attempts to delete a list of temporary files, logging successes and failures.

    Args:
        temp_files: A list of file paths to delete.
    """
    if not temp_files:
        return
    print(f"\n--- Cleaning up temporary files ({len(temp_files)}) ---")
    deleted_count, failed_count = 0, 0
    # Iterate over a copy of the list in case the original list is modified elsewhere (though unlikely here)
    for f_path in list(temp_files):
        try:
            # Check if the path exists and is a file before attempting deletion
            if f_path and os.path.isfile(f_path):
                # Attempt to set permissions to allow deletion (might help on some systems)
                try:
                    os.chmod(f_path, 0o777)
                except Exception:
                    pass  # Ignore chmod errors, proceed with removal attempt
                os.remove(f_path)
                # print(f"  Deleted: {os.path.basename(f_path)}") # Optional: Log each deletion
                deleted_count += 1
            elif f_path and os.path.exists(f_path):
                print(f"  Skipping non-file path: {os.path.basename(f_path)}")
            # else: file doesn't exist, no need to delete

        except OSError as e:
            print(f"  Error deleting {os.path.basename(f_path)}: {e}")
            failed_count += 1
        except Exception as e:
            # Catch any other unexpected errors during cleanup
            print(f"  Unexpected error deleting {os.path.basename(f_path)}: {type(e).__name__} - {e}")
            failed_count += 1
    print(f"--- Cleanup finished (Deleted: {deleted_count}, Errors: {failed_count}/{len(temp_files)}) ---")
