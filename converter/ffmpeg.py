# converter/ffmpeg.py
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional

from . import config, utils
from .exceptions import FfprobeError, CommandGenerationError, ConversionError, FfmpegError


# --- Data Structures ---
@dataclass
class StreamParams:
    """Represents essential parameters of a media stream."""
    width: Optional[int] = None
    height: Optional[int] = None
    pix_fmt: Optional[str] = None
    sar: str = '1:1'
    par: Optional[str] = None  # Note: PAR is often derived, SAR is usually more fundamental from probe
    time_base_v: Optional[str] = None
    fps: Optional[float] = None  # <<< CHANGED: Changed from fps_str to fps (float)
    sample_rate: Optional[int] = None
    channel_layout: Optional[str] = None
    sample_fmt: Optional[str] = None
    time_base_a: Optional[str] = None
    has_audio: bool = False


@dataclass
class TargetParams:
    """Represents the target parameters for the output video."""
    width: Optional[int] = None
    height: Optional[int] = None
    sar: str = '1:1'
    fps: Optional[float] = None  # <<< CHANGED: Changed from fps_str to fps (float)
    pix_fmt: str = 'yuv420p'
    v_timebase: Optional[str] = None
    sample_rate: Optional[int] = None
    channel_layout: str = 'stereo'
    sample_fmt: str = 'fltp'
    a_timebase: Optional[str] = None
    has_audio: bool = False
    video_timescale: str = "90000"


@dataclass
class AdInsertionInfo:
    """Represents information about an embedded ad."""
    path: str
    timecode: str  # Original user-provided timecode string
    time_sec: float  # Calculated time in seconds
    duration: float
    params: Optional[StreamParams] = None  # Parameters of the ad video itself


@dataclass
class TrackMetadataEdits:
    """Represents user-specified metadata edits for a track."""
    title: Optional[str] = None
    language: Optional[str] = None


class FFMPEG:
    """
    Handles FFmpeg and ffprobe operations for video conversion,
    including ad insertion and overlays.
    """

    def __init__(self,
                 ffmpeg_path: Optional[str] = None,  # Added: Custom FFmpeg path
                 video_codec: Optional[str] = None,
                 video_preset: Optional[str] = None,
                 video_cq: Optional[str] = None,
                 video_bitrate: Optional[str] = None,
                 audio_codec: Optional[str] = None,
                 audio_bitrate: Optional[str] = None,
                 video_fps: Optional[str] = None,
                 moving_speed: Optional[float] = None,
                 moving_logo_relative_height: Optional[float] = None,
                 moving_logo_alpha: Optional[float] = None,
                 banner_track_pix_fmt: Optional[str] = None,
                 banner_gap_color: Optional[str] = None,
                 hwaccel: Optional[str] = None,
                 additional_encoding: Optional[str] = None):
        """
        Initializes the FFMPEG helper with specific or default encoding parameters.

        Args:
            ffmpeg_path: Path to the FFmpeg executable (optional). If None, assumes in PATH.
            # ... other args remain the same ...
        """
        # Determine ffmpeg and ffprobe executable names/paths
        self.ffmpeg_cmd = self._resolve_executable(ffmpeg_path, "ffmpeg")
        self.ffprobe_cmd = self._resolve_executable(ffmpeg_path, "ffprobe")
        print(f"Using ffmpeg: '{self.ffmpeg_cmd}', ffprobe: '{self.ffprobe_cmd}'")

        self.video_codec = video_codec if video_codec is not None else config.VIDEO_CODEC
        self.video_preset = video_preset if video_preset is not None else config.VIDEO_PRESET
        self.video_cq = video_cq if video_cq is not None else config.VIDEO_CQ
        self.video_bitrate = video_bitrate if video_bitrate is not None else config.VIDEO_BITRATE
        self.audio_codec = audio_codec if audio_codec is not None else config.AUDIO_CODEC
        self.audio_bitrate = audio_bitrate if audio_bitrate is not None else config.AUDIO_BITRATE
        self.video_fps = video_fps
        self.moving_speed = moving_speed if moving_speed is not None else config.MOVING_SPEED
        self.moving_logo_relative_height = moving_logo_relative_height if moving_logo_relative_height is not None else config.MOVING_LOGO_RELATIVE_HEIGHT
        self.moving_logo_alpha = moving_logo_alpha if moving_logo_alpha is not None else config.MOVING_LOGO_ALPHA
        self.banner_track_pix_fmt = banner_track_pix_fmt if banner_track_pix_fmt is not None else config.BANNER_TRACK_PIX_FMT
        self.banner_gap_color = banner_gap_color if banner_gap_color is not None else config.BANNER_GAP_COLOR
        self.hwaccel = hwaccel if hwaccel is not None else config.HWACCEL
        self.additional_encoding = additional_encoding if additional_encoding else config.ADDITIONAL_ENCODING

    def run_ffprobe(self, command_args: List[str]) -> Dict[str, Any]:
        """Runs a ffprobe command using the configured path and returns the parsed JSON output."""
        # command_args should NOT include the 'ffprobe' itself, just the options/input
        full_command = [self.ffprobe_cmd] + command_args
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            result = subprocess.run(full_command, capture_output=True, text=True, check=True,
                                    encoding='utf-8', errors='replace', startupinfo=startupinfo)
            return json.loads(result.stdout)
        except FileNotFoundError:
            raise FfprobeError(
                f"'{self.ffprobe_cmd}' not found. Ensure FFmpeg (including ffprobe) is installed and in the system PATH, or configure the path in Settings.")
        except subprocess.CalledProcessError as e:
            stderr_tail = e.stderr[-1000:] if e.stderr else "N/A"
            raise FfprobeError(
                f"ffprobe execution failed: {e}\nCommand: {' '.join(full_command)}\nStderr (tail): {stderr_tail}")
        except json.JSONDecodeError as e:
            stdout_content = getattr(e, 'doc', "N/A")[:500]
            raise FfprobeError(
                f"Error decoding ffprobe output: {e}\nCommand: {' '.join(full_command)}\nStdout (start): {stdout_content}")
        except Exception as e:
            raise FfprobeError(f"Unexpected error during ffprobe execution: {e}\nCommand: {' '.join(full_command)}")

    @staticmethod
    def run_ffmpeg_command(cmd: str, step_name: str, ffmpeg_executable: str = "ffmpeg"):
        """
        Executes a single FFmpeg command string using subprocess.Popen() and handles errors.

        Args:
            cmd: The complete FFmpeg command string (options and paths).
                 This string should NOT start with 'ffmpeg', as ffmpeg_executable is prepended.
            step_name: A descriptive name for the step being executed.
            ffmpeg_executable: The path or name of the FFmpeg executable to use.
        """
        # Prepend the correct executable path/name to the command string
        # Use shlex.quote to handle spaces in the executable path robustly
        quoted_executable = shlex.quote(ffmpeg_executable)
        full_cmd_str = f"{quoted_executable} {cmd}"  # Prepend executable

        print(f"\n--- Running Step: {step_name} ---")
        if len(full_cmd_str) > 1000:
            print(f"Command: {full_cmd_str[:500]}... (total {len(full_cmd_str)} chars)")
        else:
            print(f"Command: {full_cmd_str}")

        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            process = subprocess.Popen(full_cmd_str, shell=True,  # Needs shell=True because of complexity
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       text=True,
                                       encoding='utf-8', errors='replace',
                                       startupinfo=startupinfo)

            stderr_output = ""
            progress_line = ""

            while True:
                line = process.stderr.readline()
                if not line: break
                stderr_output += line
                stripped = line.strip()
                if stripped.startswith(('frame=', 'size=', 'time=', 'bitrate=', 'speed=')):
                    progress_line = stripped
                    print(f"  {progress_line}", end='\r')
                elif progress_line:
                    print()
                    print(f"  [stderr] {stripped}")
                    progress_line = ""
                else:
                    print(f"  [stderr] {stripped}")

            if progress_line: print()

            process.stdout.close()
            return_code = process.wait()

            if return_code != 0:
                raise ConversionError(
                    f"Error during '{step_name}' (exit code {return_code}).\n"
                    f"Command:\n{full_cmd_str}\n"
                    f"Stderr (last 2000 chars):\n{stderr_output[-2000:]}")

            print(f"--- {step_name}: Successfully completed ---")
            return True

        except FileNotFoundError:
            # This error now specifically means the *shell* couldn't find the command
            # which implies the ffmpeg_executable wasn't found or PATH issue
            raise FfmpegError(
                f"Command execution failed. Ensure '{ffmpeg_executable}' is a valid command or path.") from None
        except ConversionError as e:
            raise e
        except Exception as e:
            raise FfmpegError(f"Unexpected error running '{step_name}': {type(e).__name__} - {e}") from e

    @staticmethod
    def _resolve_executable(user_path: Optional[str], base_cmd: str) -> str:
        """Determines the actual path to ffmpeg or ffprobe."""
        exe_suffix = ".exe" if os.name == 'nt' else ""
        cmd_with_suffix = f"{base_cmd}{exe_suffix}"

        if user_path:
            if os.path.isfile(user_path):
                # User provided full path to the specific executable
                # We need to derive the *other* executable's path assuming it's in the same dir
                dir_path = os.path.dirname(user_path)
                other_cmd_path = os.path.join(dir_path, cmd_with_suffix)
                if os.path.isfile(other_cmd_path):
                    return other_cmd_path
                else:
                    # Fallback if the other cmd isn't found next to the specified one
                    print(
                        f"Warning: Could not find '{cmd_with_suffix}' alongside specified path '{user_path}'. Falling back to PATH for '{base_cmd}'.")
                    return base_cmd  # Assume in PATH
            elif os.path.isdir(user_path):
                # User provided a directory path
                full_path = os.path.join(user_path, cmd_with_suffix)
                if os.path.isfile(full_path):
                    return full_path
                else:
                    print(
                        f"Warning: '{cmd_with_suffix}' not found in specified directory '{user_path}'. Falling back to PATH for '{base_cmd}'.")
                    return base_cmd  # Assume in PATH
            else:
                # User provided something invalid
                print(f"Warning: Invalid path specified '{user_path}'. Falling back to PATH for '{base_cmd}'.")
                return base_cmd  # Assume in PATH
        else:
            # No path provided, assume in system PATH
            return base_cmd  # Assume in PATH

    @staticmethod
    def _calculate_adjusted_times(original_time_sec: float, is_concat_mode: bool,
                                  sorted_embed_ads: List[AdInsertionInfo]) -> float:
        """Calculates the time in the final output considering ad insertions."""
        if not is_concat_mode or not sorted_embed_ads:
            return original_time_sec

        adjusted_time = 0.0
        last_original_time = 0.0
        for ad in sorted_embed_ads:
            # If the ad insertion point is before or at the original time
            if ad.time_sec <= original_time_sec:
                # Add the duration of the main segment before this ad
                adjusted_time += (ad.time_sec - last_original_time)
                # Add the duration of the ad itself
                adjusted_time += ad.duration
                # Update the last original time point processed
                last_original_time = ad.time_sec
            else:
                # The original time is before the next ad, stop accumulating ad durations
                break
        # Add the remaining duration of the main segment after the last processed ad
        adjusted_time += (original_time_sec - last_original_time)
        return adjusted_time

    def get_media_duration(self, file_path: str) -> Optional[float]:
        """Gets media duration using ffprobe. Returns None for images, errors, or very short clips."""
        if not file_path or not os.path.exists(file_path): return None
        duration = None
        try:
            # Command args for format duration probe
            args_fmt = ["-v", "quiet", "-i", file_path, "-show_entries", "format=duration", "-print_format", "json"]
            output_fmt = self.run_ffprobe(args_fmt)  # Use instance method
            duration_str_fmt = output_fmt.get("format", {}).get("duration")
            if duration_str_fmt and duration_str_fmt != "N/A":
                try:
                    duration = float(duration_str_fmt)
                except (ValueError, TypeError):
                    pass

            if duration is None or duration <= 0:
                # Command args for stream duration probe
                args_stream = ["-v", "quiet", "-i", file_path, "-select_streams", "v:0", "-show_entries",
                               "stream=duration", "-print_format", "json"]
                try:
                    output_stream = self.run_ffprobe(args_stream)  # Use instance method
                    stream_info = output_stream.get("streams", [])
                    if stream_info:
                        duration_str_stream = stream_info[0].get("duration")
                        if duration_str_stream and duration_str_stream != "N/A":
                            try:
                                stream_duration = float(duration_str_stream)
                                if stream_duration > 0: duration = stream_duration
                            except (ValueError, TypeError):
                                pass
                except FfprobeError:
                    pass  # Ignore if stream probing fails

            if duration and duration > 0.01:
                return duration
            else:
                return None
        except FfprobeError:
            return None  # Handled by run_ffprobe
        except Exception as e:
            print(f"Unexpected error in get_media_duration for {file_path}: {e}")
            return None

    def get_stream_info(self, file_path: str) -> Dict[str, Any]:
        """Gets info about all streams and format using ffprobe."""
        if not file_path or not os.path.exists(file_path): return {}
        # Command args for stream/format info
        command_args = ["-v", "quiet", "-i", file_path, "-show_streams", "-show_format", "-print_format", "json"]
        try:
            return self.run_ffprobe(command_args)  # Use instance method
        except FfprobeError as e:
            print(f"Failed to get stream info for {file_path}: {e}")
            return {}
        except Exception as e:
            print(f"Unexpected error getting stream info for {file_path}: {e}")
            return {}

    def get_essential_stream_params(self, file_path: str) -> Optional[StreamParams]:
        """Gets key video and audio parameters needed for compatibility checks using ffprobe."""
        params = StreamParams()
        if not file_path or not os.path.exists(file_path): return None
        has_video_stream = False
        try:
            # Video probe args
            args_video = ["-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,pix_fmt,sample_aspect_ratio,display_aspect_ratio,r_frame_rate,time_base,codec_name",
                          "-of", "json", file_path]
            data_v = self.run_ffprobe(args_video)  # Use instance method
            if data_v.get("streams"):
                stream_v = data_v["streams"][0]
                params.width = stream_v.get('width')
                params.height = stream_v.get('height')
                params.pix_fmt = stream_v.get('pix_fmt')
                sar_str = stream_v.get('sample_aspect_ratio', '1:1')
                params.sar = sar_str if ':' in sar_str and len(sar_str.split(':')) == 2 else '1:1'
                params.time_base_v = stream_v.get('time_base')
                fps_str = stream_v.get('r_frame_rate')
                if fps_str and '/' in fps_str:
                    try:
                        num_str, den_str = fps_str.split('/')
                        num, den = float(num_str), float(den_str)
                        if den != 0:
                            params.fps = num / den
                        else:
                            params.fps = None
                    except (ValueError, TypeError):
                        params.fps = None
                elif fps_str:
                    try:
                        params.fps = float(fps_str)
                    except (ValueError, TypeError):
                        params.fps = None
                else:
                    params.fps = None

                if all([params.width, params.height, params.fps, params.time_base_v]):
                    has_video_stream = True
                    if not params.pix_fmt: params.pix_fmt = 'yuv420p'
            if ':' not in params.sar or len(params.sar.split(':')) != 2: params.sar = '1:1'
        except FfprobeError:
            pass
        except Exception as e:
            print(f"Unexpected error probing video stream for {file_path}: {e}")

        if not has_video_stream:
            try:
                # Format probe args
                args_format = ["-v", "error", "-show_entries", "format=format_name", "-of", "json", file_path]
                data_fmt = self.run_ffprobe(args_format)  # Use instance method
                format_name = data_fmt.get("format", {}).get("format_name", "")
                image_formats = ['image2', 'png_pipe', 'mjpeg', 'webp_pipe', 'gif', 'tiff_pipe', 'bmp_pipe',
                                 'jpeg_pipe', 'ppm_pipe', 'pgm_pipe', 'pbm_pipe', 'apng']
                if any(fmt in format_name for fmt in image_formats):
                    # Image stream probe args
                    args_img_stream = ["-v", "error", "-select_streams", "0", "-show_entries",
                                       "stream=width,height,pix_fmt,codec_type", "-of", "json", file_path]
                    data_img_s = self.run_ffprobe(args_img_stream)  # Use instance method
                    if data_img_s.get("streams"):
                        stream_img = data_img_s["streams"][0]
                        if stream_img.get('codec_type') == 'video':
                            params.width = stream_img.get('width')
                            params.height = stream_img.get('height')
                            params.pix_fmt = stream_img.get('pix_fmt', 'rgb24')
                            params.fps = 25.0
                            params.time_base_v = '1/25'
                            params.sar = '1:1'
                            print(
                                f"Info: {os.path.basename(file_path)} detected as image format ({format_name}). Using defaults.")
            except FfprobeError:
                pass
            except Exception as e:
                print(f"Unexpected error handling potential image file {file_path}: {e}")

        if not all([params.width, params.height, params.fps]):
            print(f"Critical Error: Could not determine essential video parameters (width/height/fps) for {file_path}.")
            return None

        try:
            # Audio probe args
            args_audio = ["-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=sample_rate,channel_layout,sample_fmt,time_base", "-of", "json", file_path]
            data_a = self.run_ffprobe(args_audio)  # Use instance method
            if data_a.get("streams"):
                stream_a = data_a["streams"][0]
                try:
                    params.sample_rate = int(stream_a.get('sample_rate')) if stream_a.get('sample_rate') else None
                except ValueError:
                    params.sample_rate = None
                params.channel_layout = stream_a.get('channel_layout')
                params.sample_fmt = stream_a.get('sample_fmt')
                params.time_base_a = stream_a.get('time_base')
                if all([params.sample_rate, params.channel_layout, params.sample_fmt, params.time_base_a]):
                    params.has_audio = True
                else:
                    params.has_audio = False
            else:
                params.has_audio = False
        except FfprobeError:
            params.has_audio = False
        except Exception as e:
            print(f"Unexpected error probing audio stream for {file_path}: {e}")
            params.has_audio = False

        common_pix_fmts = ['yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p', 'yuv444p', 'yuvj444p', 'nv12', 'nv21',
                           'yuva420p', 'rgba', 'bgra', 'rgb24', 'gray', 'gbrp', 'yuv420p10le']
        if params.pix_fmt not in common_pix_fmts:
            print(f"Warning: Uncommon pix_fmt '{params.pix_fmt}' detected for {file_path}. Defaulting to 'yuv420p'.")
            params.pix_fmt = 'yuv420p'
        if ':' not in params.sar or len(params.sar.split(':')) != 2: params.sar = '1:1'
        if params.has_audio:
            if not params.channel_layout: params.channel_layout = 'stereo'
            if not params.sample_fmt: params.sample_fmt = 'fltp'
        return params

    def _validate_and_prepare_inputs(self, input_file: str, output_file: str, main_video_params: Optional[StreamParams],
                                     main_video_duration: Optional[float], embed_ads: List[Dict],
                                     banner_file: Optional[str], banner_timecodes: Optional[List[str]],
                                     moving_file: Optional[str]
                                     ) -> Tuple[
        StreamParams, float, List[AdInsertionInfo], Optional[str], Optional[List[str]], Optional[str], Optional[float]]:
        """Validates inputs, prepares basic structures, sorts ads, gets ad durations, validates banner timecodes. Returns original banner duration if valid."""
        if not input_file or not output_file:
            raise CommandGenerationError("Input or output file not specified.")
        if not os.path.exists(input_file):
            raise CommandGenerationError(f"Input file not found: {input_file}")
        if not main_video_params:
            raise CommandGenerationError("Essential main video parameters are missing.")

        # Re-check duration if missing or invalid
        if main_video_duration is None or main_video_duration <= 0.01:
            print("Warning: Main video duration not provided or invalid, attempting to get it again...")
            main_video_duration = self.get_media_duration(input_file)
            if main_video_duration is None or main_video_duration <= 0.01:
                raise CommandGenerationError(f"Could not determine a valid duration for the main video: {input_file}")
            print(f"  Retrieved duration: {main_video_duration:.3f}s")

        valid_banner_file = None
        valid_banner_timecodes = None
        original_banner_duration = None
        if banner_file and banner_timecodes:
            if os.path.exists(banner_file):
                original_banner_duration = self.get_media_duration(banner_file)
                # If duration is None, assume it's an image and use a default duration
                if original_banner_duration is None:
                    original_banner_duration = 5.0  # Default duration for image banners
                    print(
                        f"Warning: Could not determine duration for banner '{os.path.basename(banner_file)}'. Assuming image, using {original_banner_duration:.1f}s.")

                # Validate timecodes format and range
                parsed_timecodes_sec = [utils.timecode_to_seconds(tc) for tc in banner_timecodes]
                if any(t is None for t in parsed_timecodes_sec):
                    print("Warning: Invalid banner timecodes detected. Banner will be ignored.")
                    original_banner_duration = None  # Invalidate banner
                else:
                    # Filter timecodes that are within the main video duration
                    original_tc_map = {sec: tc for tc, sec in zip(banner_timecodes, parsed_timecodes_sec) if
                                       sec is not None and sec < main_video_duration}
                    valid_seconds = sorted(original_tc_map.keys())

                    if not valid_seconds:
                        print(
                            "Warning: All banner timecodes are invalid or exceed video duration. Banner will be ignored.")
                        original_banner_duration = None  # Invalidate banner
                    else:
                        if len(valid_seconds) < len([t for t in parsed_timecodes_sec if t is not None]):
                            print(f"Warning: Some banner timecodes exceed the main video duration and will be ignored.")
                        valid_banner_file = banner_file
                        # Keep original timecode strings, but only the valid ones, sorted by seconds
                        valid_banner_timecodes = [original_tc_map[sec] for sec in valid_seconds]
            else:
                print(f"Warning: Banner file not found '{banner_file}', it will be ignored.")

        # Validate moving file
        valid_moving_file = None
        if moving_file:
            if os.path.exists(moving_file):
                valid_moving_file = moving_file
            else:
                print(f"Warning: Moving ad file not found '{moving_file}', it will be ignored.")

        # Prepare embedded ads: validate timecodes, paths, get durations
        # Input `embed_ads` is List[Dict] from GUI
        ads_with_time = []
        for ad_dict in embed_ads:
            timecode_str, ad_path = ad_dict.get('timecode'), ad_dict.get('path')
            if not timecode_str or not ad_path: continue  # Skip incomplete entries
            time_sec = utils.timecode_to_seconds(timecode_str)
            # Check if timecode is valid and within bounds
            if time_sec is None or time_sec >= main_video_duration: continue
            if not os.path.exists(ad_path): continue  # Skip non-existent files

            # Temporarily store the original dict and calculated seconds
            ads_with_time.append({'data': ad_dict, 'time_sec': time_sec, 'path': ad_path})

        # Sort ads based on their insertion time
        sorted_ads_data = sorted(ads_with_time, key=lambda x: x['time_sec'])

        # Get duration and essential params for each valid ad, create AdInsertionInfo list
        ads_with_info: List[AdInsertionInfo] = []
        total_valid_ad_duration = 0.0
        for ad_entry in sorted_ads_data:
            ad_path = ad_entry['path']
            ad_timecode = ad_entry['data']['timecode']  # Get original timecode string back
            ad_duration = self.get_media_duration(ad_path)
            # If duration is invalid, assign a default and warn
            if ad_duration is None or ad_duration <= 0.01:
                ad_duration = 5.0  # Default duration for problematic ads
                print(
                    f"Warning: Could not determine duration for ad '{os.path.basename(ad_path)}' ({ad_timecode}). Using default {ad_duration:.1f}s.")

            ad_params: Optional[StreamParams] = self.get_essential_stream_params(ad_path)
            # Ad must have basic video parameters to be used
            if ad_params is None or ad_params.width is None:
                print(
                    f"Warning: Skipping ad '{os.path.basename(ad_path)}' ({ad_timecode}) because essential video parameters could not be determined.")
                continue

            ads_with_info.append(
                AdInsertionInfo(
                    path=ad_path,
                    timecode=ad_timecode,
                    time_sec=ad_entry['time_sec'],
                    duration=ad_duration,
                    params=ad_params  # Store StreamParams for the ad
                )
            )
            total_valid_ad_duration += ad_duration

        print(
            f"Prepared {len(ads_with_info)} valid ad insertions. Total added duration: {total_valid_ad_duration:.3f}s")
        # Return the validated/retrieved main video params and duration, and the list of AdInsertionInfo
        return main_video_params, main_video_duration, ads_with_info, valid_banner_file, valid_banner_timecodes, valid_moving_file, original_banner_duration

    @staticmethod
    def _determine_target_parameters(main_video_params: StreamParams) -> TargetParams:
        """Determines target parameters based on the main video, ensuring critical values are present."""
        target_params = TargetParams()  # Instantiate target params

        # Populate from main video params
        target_params.width = main_video_params.width
        target_params.height = main_video_params.height
        target_params.sar = main_video_params.sar  # Already validated
        target_params.fps = main_video_params.fps  # <<< CHANGED: Copy float fps
        target_params.pix_fmt = main_video_params.pix_fmt or 'yuv420p'  # Already validated/defaulted
        target_params.v_timebase = main_video_params.time_base_v
        target_params.sample_rate = main_video_params.sample_rate
        target_params.channel_layout = main_video_params.channel_layout or 'stereo'  # Defaulted if needed
        target_params.sample_fmt = main_video_params.sample_fmt or 'fltp'  # Defaulted if needed
        target_params.a_timebase = main_video_params.time_base_a
        target_params.has_audio = main_video_params.has_audio

        # Validate that essential parameters were successfully determined from main video
        essential_video_attrs = ['width', 'height', 'fps', 'pix_fmt', 'v_timebase', 'sar']  # <<< CHANGED: Check 'fps'
        if not all(getattr(target_params, k, None) for k in essential_video_attrs):
            missing_v = [k for k in essential_video_attrs if not getattr(target_params, k, None)]
            raise CommandGenerationError(f"Could not determine key video parameters for compatibility: {missing_v}")

        # Attempt to calculate a more precise video timescale from timebase if possible
        if target_params.v_timebase and '/' in target_params.v_timebase:
            try:
                num, den = map(float, target_params.v_timebase.split('/'))
                if den != 0 and num != 0:
                    # Timescale is the inverse of the timebase
                    timescale = int(round(1.0 / (num / den)))
                    # Use calculated timescale only if it seems reasonable
                    if 1000 < timescale < 1000000:
                        target_params.video_timescale = str(timescale)
            except ValueError:
                pass  # Ignore errors, keep default 90k

        # Validate audio parameters if audio is expected
        essential_audio_attrs = ['sample_rate', 'channel_layout', 'sample_fmt', 'a_timebase']
        if target_params.has_audio and not all(getattr(target_params, k, None) for k in essential_audio_attrs):
            missing_a = [k for k in essential_audio_attrs if not getattr(target_params, k, None)]
            print(f"Warning: Could not determine key audio parameters ({missing_a}). Audio track will be ignored.")
            target_params.has_audio = False
            # Nullify audio params if disabling audio
            for k in essential_audio_attrs:
                setattr(target_params, k, None)

        print(  # <<< CHANGED: Log float fps
            f"Target parameters determined: Res={target_params.width}x{target_params.height}, FPS={target_params.fps:.3f}, PixFmt={target_params.pix_fmt}, SAR={target_params.sar}, Audio={target_params.has_audio}")
        if target_params.has_audio:
            print(
                f"  Audio Details: Rate={target_params.sample_rate}, Layout={target_params.channel_layout}, Fmt={target_params.sample_fmt}")
        return target_params

    def _create_segment_command(self, input_path: str, output_path: str, target_params: TargetParams,
                                start_time: Optional[float] = None, duration: Optional[float] = None,
                                output_pix_fmt: Optional[str] = None,
                                output_audio: bool = True,
                                force_fps: bool = True,
                                is_banner_segment: bool = False) -> str:
        """ Helper function to create a single segment transcoding/generation command string (without 'ffmpeg' prefix). """
        sar_value = target_params.sar.replace(':', '/')
        final_pix_fmt = output_pix_fmt if output_pix_fmt else target_params.pix_fmt
        vf_parts = []

        if is_banner_segment:
            banner_params: Optional[StreamParams] = self.get_essential_stream_params(input_path)
            target_w = target_params.width
            scaled_h = target_params.height // 10 if target_params.height else 72
            if banner_params and banner_params.width and banner_params.height:
                orig_w, orig_h = banner_params.width, banner_params.height
                scaled_h = max(1, int(orig_h * (target_w / orig_w))) if orig_w > 0 else scaled_h
            vf_parts.extend([f"scale={target_w}:{scaled_h}:flags=bicubic", f"setsar=sar={sar_value}"])
            final_pix_fmt = self.banner_track_pix_fmt
        else:
            if target_params.width and target_params.height:
                vf_parts.extend([
                    f"scale={target_params.width}:{target_params.height}:force_original_aspect_ratio=decrease:flags=bicubic",
                    f"pad={target_params.width}:{target_params.height}:(ow-iw)/2:(oh-ih)/2:color=black",
                    f"setsar=sar={sar_value}"
                ])
            else:
                vf_parts.append(f"setsar=sar={sar_value}")

        if force_fps and target_params.fps is not None:
            vf_parts.append(f"fps={str(target_params.fps)}")
        vf_parts.append(f"format=pix_fmts={final_pix_fmt}")
        vf_string = ",".join(vf_parts)

        af_string = None
        create_audio = target_params.has_audio and output_audio and not is_banner_segment
        if create_audio:
            if target_params.sample_rate and target_params.sample_fmt and target_params.channel_layout:
                af_parts = [
                    f"aresample=resampler=soxr:osr={target_params.sample_rate}",
                    f"aformat=sample_fmts={target_params.sample_fmt}:channel_layouts={target_params.channel_layout}"
                ]
                af_string = ",".join(af_parts)
            else:
                print("Warning: Target audio parameters missing, cannot create audio stream for segment.")
                create_audio = False

        # Command parts *without* the initial 'ffmpeg'
        cmd_parts = ["-y"]  # Start with options
        input_options = []
        is_image_input = self.get_media_duration(input_path) is None
        if is_image_input: input_options.extend(["-loop", "1"])
        if start_time is not None and start_time > 0.001: input_options.extend(["-ss", f"{start_time:.6f}"])
        input_options.extend(["-i", f'"{input_path}"'])
        cmd_parts.extend(input_options)
        if duration is not None: cmd_parts.extend(["-t", f"{duration:.6f}"])
        cmd_parts.extend(["-avoid_negative_ts", "make_zero"])
        cmd_parts.extend(["-vf", f'"{vf_string}"'])
        if af_string:
            cmd_parts.extend(["-af", f'"{af_string}"'])
        elif not create_audio:
            cmd_parts.extend(["-an"])

        cmd_parts.extend(["-c:v", self.video_codec, "-preset", self.video_preset])
        if self.video_cq:
            cmd_parts.extend(["-cq:v", str(self.video_cq)])
        elif self.video_bitrate and self.video_bitrate != "0":
            cmd_parts.extend(["-b:v", self.video_bitrate])
        else:
            cmd_parts.extend(["-crf", "18"])
        if create_audio: cmd_parts.extend(["-c:a", self.audio_codec, "-b:a", self.audio_bitrate])
        cmd_parts.extend(["-video_track_timescale", target_params.video_timescale])
        cmd_parts.extend(["-map", "0:v:0?"])
        if create_audio: cmd_parts.extend(["-map", "0:a:0?"])
        cmd_parts.append(f'"{output_path}"')
        return " ".join([p for p in cmd_parts if p is not None])

    def _generate_preprocessing_for_concat(self, input_file: str, sorted_embed_ads: List[AdInsertionInfo],
                                           target_params: TargetParams,
                                           main_video_duration: float) -> Tuple[List[str], str, List[str], float]:
        """ Generates preprocessing command args for main video segments and ads, and the concat list file. """
        preprocessing_commands_args, temp_files_generated, concat_list_items = [], [], []
        total_ad_duration_sum, segment_counter, last_original_time = 0.0, 0, 0.0
        unique_ad_files_cache: Dict[str, Dict[str, Any]] = {}

        print("  Preprocessing unique ad files...")
        for ad_data in sorted_embed_ads:
            ad_path = ad_data.path
            if ad_path not in unique_ad_files_cache:
                temp_ad_path = utils.generate_temp_filename("ad_segment_uniq", segment_counter)
                cmd_args = self._create_segment_command(  # Gets command args string
                    input_path=ad_path, output_path=temp_ad_path, target_params=target_params,
                    duration=ad_data.duration, output_audio=target_params.has_audio, force_fps=True)
                preprocessing_commands_args.append(cmd_args)
                temp_files_generated.append(temp_ad_path)
                unique_ad_files_cache[ad_path] = {'data': ad_data, 'temp_path': temp_ad_path}
                segment_counter += 1

        print("  Generating main video segments and concat list...")
        for i, embed_ad_info in enumerate(sorted_embed_ads):
            embed_original_time_sec = embed_ad_info.time_sec
            original_ad_path = embed_ad_info.path
            cached_ad_info = unique_ad_files_cache.get(original_ad_path)
            if not cached_ad_info: continue
            preprocessed_ad_path = cached_ad_info['temp_path']
            ad_duration = cached_ad_info['data'].duration
            main_segment_duration = embed_original_time_sec - last_original_time

            if main_segment_duration > 0.001:
                temp_main_path = utils.generate_temp_filename("main_segment", segment_counter)
                cmd_args = self._create_segment_command(  # Gets command args string
                    input_path=input_file, output_path=temp_main_path, target_params=target_params,
                    start_time=last_original_time, duration=main_segment_duration,
                    output_audio=target_params.has_audio, force_fps=True)
                preprocessing_commands_args.append(cmd_args)
                temp_files_generated.append(temp_main_path)
                concat_list_items.append(temp_main_path)
                segment_counter += 1

            concat_list_items.append(preprocessed_ad_path)
            total_ad_duration_sum += ad_duration
            last_original_time = embed_original_time_sec

        if main_video_duration - last_original_time > 0.001:
            final_segment_duration = main_video_duration - last_original_time
            temp_main_path = utils.generate_temp_filename("main_segment", segment_counter)
            cmd_args = self._create_segment_command(  # Gets command args string
                input_path=input_file, output_path=temp_main_path, target_params=target_params,
                start_time=last_original_time, duration=final_segment_duration,
                output_audio=target_params.has_audio, force_fps=True)
            preprocessing_commands_args.append(cmd_args)
            temp_files_generated.append(temp_main_path)
            concat_list_items.append(temp_main_path)

        if not concat_list_items: raise CommandGenerationError("No segments for concatenation.")

        concat_list_filename = f"concat_list_main_{int(time.time())}.txt"
        concat_list_path = os.path.join(utils.generate_temp_filename("", 0, "").rsplit(os.sep, 1)[0],
                                        concat_list_filename)
        temp_files_generated.append(concat_list_path)

        try:
            with open(concat_list_path, 'w', encoding='utf-8') as f:
                f.write("ffconcat version 1.0\n\n")
                for item_path in concat_list_items:
                    f.write(f"file {utils.escape_path_for_concat(item_path)}\n")
            print(f"  Concat list file created: {concat_list_path}")
        except IOError as e:
            raise CommandGenerationError(f"Failed to create concat list file: {e}")

        return preprocessing_commands_args, concat_list_path, temp_files_generated, total_ad_duration_sum

    def _generate_banner_preprocessing_commands(self,
                                                banner_file: str, banner_timecodes: List[str],
                                                original_banner_duration: float,
                                                target_params: TargetParams, final_duration_estimate: float,
                                                is_concat_mode: bool, sorted_embed_ads: List[AdInsertionInfo]
                                                ) -> Tuple[List[str], str, List[str], str]:
        """ Generates command args to create banner segments, gaps, concat list, and the final banner track. """
        print("--- Phase 2.1: Generating Segment Preprocessing Command Args (Banner) ---")
        preprocessing_cmds_args, temp_files, concat_list_items = [], [], []
        segment_counter = 0
        last_banner_track_time = 0.0

        if not target_params.width or not target_params.height: raise CommandGenerationError(
            "Target dimensions missing.")
        banner_scaled_width = target_params.width
        banner_params: Optional[StreamParams] = self.get_essential_stream_params(banner_file)
        banner_scaled_height = target_params.height // 10
        if banner_params and banner_params.width and banner_params.height:
            orig_w, orig_h = banner_params.width, banner_params.height
            banner_scaled_height = max(1, int(orig_h * (
                        banner_scaled_width / orig_w))) if orig_w > 0 else banner_scaled_height
        print(f"  Target banner track dimensions: {banner_scaled_width}x{banner_scaled_height}")

        temp_banner_segment_path = utils.generate_temp_filename("banner_segment_uniq", segment_counter)
        banner_segment_cmd_args = self._create_segment_command(  # Gets command args
            input_path=banner_file, output_path=temp_banner_segment_path, target_params=target_params,
            duration=original_banner_duration, output_audio=False, force_fps=True, is_banner_segment=True)
        preprocessing_cmds_args.append(banner_segment_cmd_args)
        temp_files.append(temp_banner_segment_path)
        segment_counter += 1

        adjusted_banner_times_sec = []
        valid_banner_timecodes_sec = sorted(filter(None, [utils.timecode_to_seconds(tc) for tc in banner_timecodes]))
        for banner_original_sec in valid_banner_timecodes_sec:
            adjusted_start = self._calculate_adjusted_times(banner_original_sec, is_concat_mode, sorted_embed_ads)
            adjusted_end = min(adjusted_start + original_banner_duration, final_duration_estimate)
            if adjusted_end > adjusted_start + 0.001 and adjusted_start < final_duration_estimate:
                adjusted_banner_times_sec.append((adjusted_start, adjusted_end))
        adjusted_banner_times_sec.sort(key=lambda x: x[0])

        max_banner_track_duration = 0.0
        for i, (start_time, end_time) in enumerate(adjusted_banner_times_sec):
            gap_duration = start_time - last_banner_track_time
            if gap_duration > 0.001:
                temp_gap_path = utils.generate_temp_filename("banner_gap", segment_counter)
                if target_params.fps is None: raise CommandGenerationError("Target FPS required for gaps.")
                # Generate gap command args using helper
                gap_cmd_args = self._generate_gap_command(
                    output_path=temp_gap_path, width=banner_scaled_width, height=banner_scaled_height,
                    fps=target_params.fps, duration=gap_duration,
                    pix_fmt=self.banner_track_pix_fmt, color=self.banner_gap_color)
                preprocessing_cmds_args.append(gap_cmd_args)
                temp_files.append(temp_gap_path)
                concat_list_items.append(temp_gap_path)
                segment_counter += 1

            current_banner_duration = end_time - start_time
            concat_list_items.append(f"{temp_banner_segment_path}\nduration {current_banner_duration:.6f}")
            last_banner_track_time = end_time
            max_banner_track_duration = max(max_banner_track_duration, end_time)

        if not concat_list_items: raise CommandGenerationError("No segments for banner track.")

        print(f"--- Phase 2.2: Creating Concat List File for Banner Track ---")
        banner_concat_list_filename = f"concat_list_banner_{int(time.time())}.txt"
        banner_concat_list_path = os.path.join(utils.generate_temp_filename("", 0, "").rsplit(os.sep, 1)[0],
                                               banner_concat_list_filename)
        temp_files.append(banner_concat_list_path)
        try:
            with open(banner_concat_list_path, 'w', encoding='utf-8') as f:
                f.write("ffconcat version 1.0\n\n")
                for item in concat_list_items:
                    if '\n' in item:
                        path_part, duration_part = item.split('\n', 1)
                        f.write(f"file {utils.escape_path_for_concat(path_part)}\n")
                        f.write(f"{duration_part}\n")
                    else:
                        f.write(f"file {utils.escape_path_for_concat(item)}\n")
            print(f"  Banner concat list file created: {banner_concat_list_path}")
        except IOError as e:
            raise CommandGenerationError(f"Failed to create banner concat list file: {e}")

        print(f"--- Phase 2.3: Generating Command Args for Banner Track Concatenation ---")
        concatenated_banner_path = utils.generate_temp_filename("banner_track_final", 0)
        temp_files.append(concatenated_banner_path)
        # Generate concat command args using helper
        banner_concat_cmd_args = self._generate_concat_command(
            list_path=banner_concat_list_path, output_path=concatenated_banner_path,
            duration=max_banner_track_duration)
        print(f"  Banner concatenation command args created. Output: {concatenated_banner_path}")
        preprocessing_cmds_args.append(banner_concat_cmd_args)

        return preprocessing_cmds_args, banner_concat_list_path, temp_files, concatenated_banner_path

    def _build_moving_logo_filter(self,
                                  current_video_input_label: str,  # e.g., [v_banner_out_1] or [0:v]
                                  moving_input_stream_label: str,  # e.g., [2:v]
                                  transparent_canvas_label: str,  # e.g., [transparent_canvas]
                                  target_params: TargetParams,
                                  final_duration_estimate: float
                                  ) -> Tuple[List[str], Optional[str]]:
        """
        Builds filtergraph parts for:
        1. Preparing the logo (scale, alpha).
        2. Overlaying the animated logo onto a transparent canvas.
        3. Optionally applying motion blur (tmix) to the animated logo.
        4. Overlaying the result onto the main video stream.
        Returns the list of filter parts and the label of the final output stream.
        """
        filter_parts = []
        moving_input_index = moving_input_stream_label.strip('[]').split(':')[0]  # For unique labels

        # Define intermediate stream labels
        scaled_moving_stream = f"[moving_scaled_{moving_input_index}]"
        prepared_logo_stream = f"[logo_prepared_{moving_input_index}]"  # Logo with alpha
        logo_anim_on_canvas_stream = f"[logo_anim_canvas_{moving_input_index}]"  # Logo animated on transparent canvas
        logo_blurred_stream = f"[logo_blurred_{moving_input_index}]"  # Changed from tblend to tmix conceptually
        final_moving_overlay_output_label = f"[v_moving_out_{moving_input_index}]"  # Final output after overlaying on main video

        print(
            f"  Setting up filter for moving ad (Input: {moving_input_stream_label}, Canvas: {transparent_canvas_label})...")

        # --- 1. Prepare Logo (Scale + Alpha) ---
        main_h = target_params.height if target_params.height else 720
        if not main_h:
            raise CommandGenerationError("Cannot determine main video height for logo scaling.")
        logo_target_h = max(1, int(main_h * self.moving_logo_relative_height))
        sar_value = target_params.sar.replace(':', '/')
        moving_scale_filter = f"scale=-1:{logo_target_h}:flags=bicubic"
        filter_parts.append(
            f"{moving_input_stream_label}{moving_scale_filter},setsar=sar={sar_value}{scaled_moving_stream}")
        clamped_alpha = max(0.0, min(1.0, self.moving_logo_alpha))
        alpha_filter = f"format=pix_fmts=rgba,colorchannelmixer=aa={clamped_alpha:.3f}"
        filter_parts.append(f"{scaled_moving_stream}{alpha_filter}{prepared_logo_stream}")
        print(f"    Logo prepared: {prepared_logo_stream}")

        # --- 2. Animate Logo on Transparent Canvas ---
        t_total = max(0.1, final_duration_estimate)
        if not isinstance(self.moving_speed, (int, float)) or self.moving_speed <= 0:
            moving_speed = 1.0
        else:
            moving_speed = self.moving_speed
        cycle_t = t_total / moving_speed
        x_expr, y_expr = "'0'", "'0'"  # Default static

        if cycle_t > 0.5:
            canvas_w = target_params.width if target_params.width else 1280
            canvas_h = target_params.height if target_params.height else 720

            t1, t2, t3 = cycle_t / 4, cycle_t / 2, 3 * cycle_t / 4
            seg_dur = max(cycle_t / 4, 1e-6)
            mx, my = f"(W-overlay_w)", f"(H-overlay_h)"  # W/H refer to canvas (1st input)
            tv = f"mod(t,{cycle_t:.6f})"

            x1 = f"{mx}*({tv}/{seg_dur:.6f})"
            x2 = f"{mx}"
            x3 = f"{mx}*(1-(({tv}-{t2:.6f})/{seg_dur:.6f}))"
            x4 = "0"
            y1 = "0"
            y2 = f"{my}*(({tv}-{t1:.6f})/{seg_dur:.6f})"
            y3 = f"{my}"
            y4 = f"{my}*(1-(({tv}-{t3:.6f})/{seg_dur:.6f}))"

            x_expr = f"'if(lt({tv},{t1:.6f}),{x1},if(lt({tv},{t2:.6f}),{x2},if(lt({tv},{t3:.6f}),{x3},{x4})))'"
            y_expr = f"'if(lt({tv},{t1:.6f}),{y1},if(lt({tv},{t2:.6f}),{y2},if(lt({tv},{t3:.6f}),{y3},{y4})))'"
            print(f"    Moving ad animation calculated: Rectangular path ({cycle_t:.2f}s cycle).")
        else:
            print(
                f"    Warning: Animation cycle duration ({cycle_t:.3f}s) is too short, logo will be static at top-left.")

        overlay_on_canvas_filter = (f"{transparent_canvas_label}{prepared_logo_stream}"
                                    f"overlay=x={x_expr}:y={y_expr}:shortest=0"
                                    f"{logo_anim_on_canvas_stream}")
        filter_parts.append(overlay_on_canvas_filter)
        print(f"    Animated logo on canvas: {logo_anim_on_canvas_stream}")

        # --- 3. Apply Motion Blur (Conditional) ---
        stream_for_final_overlay = logo_anim_on_canvas_stream  # Default to non-blurred stream

        if config.MOVING_LOGO_MOTION_BLUR:
            print("    Motion blur enabled. Calculating parameters...")
            final_blur_frames = 1
            try:
                if target_params.fps is None or target_params.fps <= 0:
                    print("      Warning: Cannot apply motion blur, target FPS is invalid.")
                elif target_params.width is None or target_params.height is None:
                    print("      Warning: Cannot apply motion blur, target dimensions are unknown.")
                elif cycle_t <= 0.5:
                    print("      Skipping motion blur for static logo.")
                    final_blur_frames = 1
                else:
                    fps = target_params.fps
                    width = target_params.width
                    height = target_params.height
                    max_travel_distance = float(max(width, height))
                    time_to_cross_half_screen = cycle_t / 2.0
                    pixels_per_second = 0.0
                    if time_to_cross_half_screen > 1e-6:
                        pixels_per_second = max_travel_distance / time_to_cross_half_screen
                    print(f"      Estimated speed: {pixels_per_second:.2f} pixels/sec (approx)")
                    base_blur_frames = 1.0
                    if pixels_per_second > 1e-6:
                        base_blur_frames = fps / pixels_per_second
                    blur_scaling_factor = 5.0
                    adjusted_blur_frames = (base_blur_frames
                                            * config.MOVING_LOGO_BLUR_INTENSITY
                                            * blur_scaling_factor)
                    final_blur_frames = max(1, round(adjusted_blur_frames))
                    print(
                        f"      Calculated tmix frames: {final_blur_frames} (Intensity: {config.MOVING_LOGO_BLUR_INTENSITY})")  # <<< UPDATED Log message

                if final_blur_frames > 1:
                    # <<< CHANGED: Use tmix instead of tblend >>>
                    tmix_filter = (f"{logo_anim_on_canvas_stream}"
                                   f"tmix=frames={final_blur_frames}"  # Removed weights (default is average)
                                   f"{logo_blurred_stream}")
                    # <<< END OF CHANGE >>>
                    filter_parts.append(tmix_filter)
                    stream_for_final_overlay = logo_blurred_stream
                    print(f"    Applied tmix filter. Output: {stream_for_final_overlay}")  # <<< UPDATED Log message
                else:
                    print("    Calculated blur frames <= 1, skipping tmix filter.")  # <<< UPDATED Log message

            except Exception as e:
                print(f"      Warning: Error calculating motion blur frames: {e}. Skipping blur.")

        else:
            print("    Motion blur disabled in config.")

        # --- 4. Final Overlay on Main Video ---
        final_overlay_filter = (f"{current_video_input_label}{stream_for_final_overlay}"
                                f"overlay=x=0:y=0:shortest=0"
                                f"{final_moving_overlay_output_label}")
        filter_parts.append(final_overlay_filter)

        print(f"    Final logo overlay added. Output: {final_moving_overlay_output_label}")
        return filter_parts, final_moving_overlay_output_label

    def _build_filter_complex(self,
                              base_video_specifier: str, base_audio_specifier: Optional[str],
                              target_params: TargetParams,
                              final_duration_estimate: float, is_concat_mode: bool,
                              sorted_embed_ads: List[AdInsertionInfo],
                              concatenated_banner_track_idx: Optional[int],
                              original_banner_duration: Optional[float],
                              banner_timecodes: Optional[List[str]],
                              moving_file: Optional[str], moving_input_idx: Optional[int]
                              ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """ Builds the complete -filter_complex string for the main command, incorporating banner and moving logo overlays. """
        all_filter_parts = []
        # Start with the base video stream as the current input
        last_filter_video_label = f"[{base_video_specifier}]"
        # Initialize final map labels to the base specifiers
        final_video_output_map_label = base_video_specifier
        final_audio_map_label = base_audio_specifier

        # --- Banner Overlay Filter ---
        if concatenated_banner_track_idx is not None and banner_timecodes and original_banner_duration is not None:
            try:
                print(
                    f"  Setting up overlay filter for banner track (Input: [{concatenated_banner_track_idx}:v], using 'between')...")
                banner_track_input_label = f"[{concatenated_banner_track_idx}:v]"
                overlay_output_label_banner = f"[v_banner_out_{concatenated_banner_track_idx}]"

                enable_parts = []
                valid_banner_timecodes_sec = sorted(
                    filter(None, [utils.timecode_to_seconds(tc) for tc in banner_timecodes]))
                for banner_original_sec in valid_banner_timecodes_sec:
                    adjusted_start_time = self._calculate_adjusted_times(banner_original_sec, is_concat_mode,
                                                                         sorted_embed_ads)
                    end_time = min(adjusted_start_time + original_banner_duration, final_duration_estimate)
                    if end_time > adjusted_start_time + 0.001 and adjusted_start_time < final_duration_estimate:
                        enable_parts.append(f"between(t,{adjusted_start_time:.3f},{end_time:.3f})")

                if enable_parts:
                    enable_expression = "+".join(enable_parts)
                    overlay_y_pos, overlay_x_pos = "main_h-overlay_h", "0"
                    banner_overlay_filter = (
                        f"{last_filter_video_label}{banner_track_input_label}"
                        f"overlay=x={overlay_x_pos}:y={overlay_y_pos}:enable='{enable_expression}':shortest=0"
                        f"{overlay_output_label_banner}"
                    )
                    all_filter_parts.append(banner_overlay_filter)
                    last_filter_video_label = overlay_output_label_banner  # Update input for next stage
                    final_video_output_map_label = last_filter_video_label.strip('[]')  # Update final map target
                    print(
                        f"    Overlay filter for banner track (using 'between') added. Output: {last_filter_video_label}")
                else:
                    print(
                        "    Warning: Could not generate valid 'enable' time ranges for banner overlay. Filter not added.")
            except Exception as e:
                print(f"Warning: Error building banner overlay filter: {e}. Skipping banner.")

        # --- Moving Logo Filter ---
        if moving_file and moving_input_idx is not None:
            # --- <<< CHANGED: Generate transparent canvas FIRST >>> ---
            transparent_canvas_label = "[transparent_canvas]"
            if not target_params.width or not target_params.height or not target_params.fps:
                print(
                    "Warning: Cannot generate transparent canvas for logo - missing target dimensions or FPS. Skipping logo.")
            else:
                try:
                    canvas_width = target_params.width
                    canvas_height = target_params.height
                    canvas_fps = str(target_params.fps)  # Convert float fps to string for filter
                    canvas_duration = f"{final_duration_estimate:.6f}"
                    # Create transparent canvas with correct dimensions, fps, duration, and RGBA format
                    canvas_filter = (
                        f"color=c=black@0.0:s={canvas_width}x{canvas_height}:r={canvas_fps}:d={canvas_duration},"
                        f"format=rgba{transparent_canvas_label}")
                    all_filter_parts.append(canvas_filter)
                    print(f"    Generated transparent canvas for logo: {transparent_canvas_label}")

                    # --- <<< CHANGED: Call _build_moving_logo_filter with canvas label >>> ---
                    moving_input_stream_label = f"[{moving_input_idx}:v]"
                    logo_filters, last_video_label_after_logo = self._build_moving_logo_filter(
                        current_video_input_label=last_filter_video_label,
                        # Input is the output of banner overlay (or base video)
                        moving_input_stream_label=moving_input_stream_label,
                        transparent_canvas_label=transparent_canvas_label,  # Pass the canvas label
                        target_params=target_params,
                        final_duration_estimate=final_duration_estimate)

                    if last_video_label_after_logo:
                        all_filter_parts.extend(logo_filters)
                        last_filter_video_label = last_video_label_after_logo  # Update input for next stage
                        final_video_output_map_label = last_filter_video_label.strip('[]')  # Update final map target
                except Exception as e:
                    print(f"Warning: Error building moving logo filter: {e}. Skipping logo.")
            # --- <<< END OF CHANGES for Moving Logo >>> ---

        # --- Final Assembly ---
        if not all_filter_parts:
            print("--- No filters applied ---")
            return None, base_video_specifier, base_audio_specifier

        filter_complex_str = ";".join(all_filter_parts)
        print(
            f"--- Final filter_complex generated ({len(all_filter_parts)} stages). Video output: [{final_video_output_map_label}] ---")
        # print(f"DEBUG filter_complex:\n{filter_complex_str}\n") # Uncomment for debugging
        return filter_complex_str, final_video_output_map_label, final_audio_map_label

    def _define_main_command_inputs(self,
                                    input_file: str, target_params: TargetParams,
                                    concatenated_banner_track_path: Optional[str],
                                    moving_file: Optional[str],
                                    is_concat_mode: bool, concat_list_path: Optional[str]
                                    ) -> Tuple[
        List[Tuple[List[str], str]], str, Optional[str], Optional[str], Optional[int], Optional[int], int]:

        input_definitions = []
        base_video_specifier = "0:v:0?"
        base_audio_specifier = "0:a:0?" if target_params.has_audio else None
        subtitle_input_specifier = None
        metadata_input_index = 0
        primary_input_options: List[str] = []  # Initialize with default empty list

        if is_concat_mode:
            if not concat_list_path or not os.path.exists(concat_list_path):
                raise CommandGenerationError("Concat list file (main video + ads) not found or provided.")
            primary_input_options = ["-f", "concat", "-safe", "0"]  # Overwrite if concat mode
            primary_input_path = concat_list_path
            print(f"Mode: Concatenation. Input 0 (Video/Audio): {os.path.basename(concat_list_path)}")

            # Use original input file for metadata/subtitles if needed
            original_info = self.get_stream_info(input_file)
            if any(s.get('codec_type') == 'subtitle' for s in original_info.get("streams", [])):
                print(f"  Input 1 (Subs/Metadata Source): {os.path.basename(input_file)}")
                input_definitions.append(([], input_file))  # Metadata source has no specific options here
                subtitle_input_specifier = "1:s?"
                metadata_input_index = 1
        else:
            # primary_input_options remains [] (initialized above)
            primary_input_path = input_file
            metadata_input_index = 0
            original_info = self.get_stream_info(input_file)
            if any(s.get('codec_type') == 'subtitle' for s in original_info.get("streams", [])):
                subtitle_input_specifier = "0:s?"
            print(f"Mode: Direct Conversion. Input 0 (Video/Audio/Subs/Metadata): {os.path.basename(input_file)}")

        # Add the primary video/audio input (either file or concat list)
        # Now primary_input_options is guaranteed to be defined
        input_definitions.insert(0, (primary_input_options, primary_input_path))

        current_input_index = len(input_definitions)
        banner_track_input_idx = None
        if concatenated_banner_track_path:
            print(
                f"  Input {current_input_index} (Concatenated Banner Track): {os.path.basename(concatenated_banner_track_path)}")
            input_definitions.append(
                ([], concatenated_banner_track_path))  # Banner track needs no specific options here
            banner_track_input_idx = current_input_index
            current_input_index += 1

        moving_input_idx = None
        if moving_file and os.path.exists(moving_file):
            moving_options = ["-loop", "1"]
            # If moving file is an image, set its frame rate to match target
            if self.get_media_duration(moving_file) is None and target_params.fps is not None:
                moving_options.extend(["-r", str(target_params.fps)])
            print(f"  Input {current_input_index} (Moving Logo): {os.path.basename(moving_file)}")
            input_definitions.append((moving_options, moving_file))
            moving_input_idx = current_input_index
            current_input_index += 1
        elif moving_file:
            print(f"Warning: Moving logo file specified but not found: {moving_file}. Ignoring logo.")

        return input_definitions, base_video_specifier, base_audio_specifier, subtitle_input_specifier, banner_track_input_idx, moving_input_idx, metadata_input_index

    @staticmethod
    def _apply_filters_and_mapping(main_cmd_parts: List[str],
                                   filter_complex_str: Optional[str],
                                   final_video_map_label: Optional[str],
                                   final_audio_map_label: Optional[str],
                                   base_video_specifier: str,
                                   base_audio_specifier: Optional[str],
                                   subtitle_input_specifier: Optional[str],
                                   target_params: TargetParams
                                   ) -> List[str]:
        """Applies filter_complex and sets up -map options."""
        map_commands = []
        if filter_complex_str:
            main_cmd_parts.extend(['-filter_complex', f'"{filter_complex_str}"'])
            # Map the final video output label from the filter complex
            map_commands.append(f'-map "[{final_video_map_label}]"')
            # Map the final audio output label if audio exists and is mapped from filter complex
            if final_audio_map_label and target_params.has_audio:
                map_commands.append(f'-map {final_audio_map_label}?')
            # If target should have no audio, disable it explicitly
            elif not target_params.has_audio:
                map_commands.append('-an')
        else:
            # No filter complex, map directly from input
            map_commands.append(f'-map {base_video_specifier}')
            if base_audio_specifier and target_params.has_audio:
                map_commands.append(f'-map {base_audio_specifier}')
            elif not target_params.has_audio:
                map_commands.append('-an')

        # Map subtitles if specified
        if subtitle_input_specifier:
            map_commands.append(f"-map {subtitle_input_specifier}")
            map_commands.extend(["-c:s", "copy"])  # Copy subtitles

        # Add the map commands to the main command parts
        main_cmd_parts.extend(map_commands)
        return map_commands  # Return map commands for potential use in metadata handling

    @staticmethod
    def _handle_metadata(main_cmd_parts: List[str],
                         track_data: Dict[str, TrackMetadataEdits],  # Expect dataclass here
                         metadata_input_index: int,
                         input_definitions: List[Tuple[List[str], str]],
                         map_commands: List[str],
                         base_video_specifier: str,
                         filter_complex_str: Optional[str]) -> List[str]:
        """Handles metadata mapping and stream-specific metadata edits."""
        metadata_args = []
        temp_files_for_metadata = []  # Kept for structure, but currently unused

        # Map global metadata from the specified input index
        metadata_args.extend([f'-map_metadata', str(metadata_input_index)])
        # Ensure metadata tags are carried over correctly for MP4/MOV
        metadata_args.extend(
            ["-movflags", "+use_metadata_tags"])  # Note: might be merged later in _finalize_main_command

        # Apply track-specific metadata edits
        source_file_for_metadata = input_definitions[metadata_input_index][1]
        if track_data and os.path.exists(source_file_for_metadata):
            # --- Build a mapping from original stream specifier to output stream specifier ---
            # Output specifiers are like s:v:0, s:a:0, s:s:0 etc.
            out_v_idx, out_a_idx, out_s_idx = 0, 0, 0
            output_stream_map: Dict[str, str] = {}  # { "input_idx:type:stream_idx" : "s:type:output_idx" }

            for map_cmd_str in map_commands:
                parts = map_cmd_str.split()
                if len(parts) < 2 or parts[0] != '-map': continue
                spec = parts[1].strip('"')

                # Handle mapping from filter complex output (only video handled here for simplicity)
                if spec.startswith('['):
                    # Assume filter output maps to the first video stream from the metadata source
                    # This is a heuristic and might need refinement for complex filters
                    original_spec_key = f"{base_video_specifier.replace('?', '')}"  # e.g., "0:v:0"
                    if original_spec_key not in output_stream_map:
                        output_stream_map[original_spec_key] = f"s:v:{out_v_idx}"
                        out_v_idx += 1
                # Handle direct mapping from inputs
                elif ':' in spec:
                    try:
                        in_idx_str, stream_info = spec.split(':', 1)
                        in_idx = int(in_idx_str)
                    except ValueError:
                        continue  # Skip invalid map specifiers

                    # Only consider streams mapped from the designated metadata source input
                    if in_idx == metadata_input_index:
                        stream_type = stream_info[0]  # 'v', 'a', 's'
                        stream_index_str = '0'  # Default to first stream of type
                        # Extract specific stream index if present (e.g., a:1?)
                        if ':' in stream_info.strip('?'):
                            stream_index_str = stream_info.split(':')[-1].strip('?')

                        # Construct the key representing the original stream specifier
                        original_spec_key = f"{metadata_input_index}:{stream_type}:{stream_index_str}"

                        # Assign the corresponding output stream specifier
                        # (Ensure video isn't double-counted if also mapped from filter)
                        if stream_type == 'v' and not spec.startswith('[') and not filter_complex_str:
                            if original_spec_key not in output_stream_map:
                                output_stream_map[original_spec_key] = f"s:v:{out_v_idx}"
                                out_v_idx += 1
                        elif stream_type == 'a':
                            if original_spec_key not in output_stream_map:
                                output_stream_map[original_spec_key] = f"s:a:{out_a_idx}"
                                out_a_idx += 1
                        elif stream_type == 's':
                            if original_spec_key not in output_stream_map:
                                output_stream_map[original_spec_key] = f"s:s:{out_s_idx}"
                                out_s_idx += 1
            # --- End of output stream mapping ---

            print(f"  Metadata Map (Source Input {metadata_input_index}): {output_stream_map}")

            # Apply edits based on the mapping
            for track_id_from_user, edits in track_data.items():
                # Normalize the user track ID (e.g., "0:v:0") - should already be in this format
                # If the normalized ID exists in our map
                if track_id_from_user in output_stream_map:
                    output_metadata_specifier = output_stream_map[track_id_from_user]
                    print(
                        f"    Applying metadata to output stream {output_metadata_specifier} (from {track_id_from_user})")
                    # Apply title if present in edits dataclass
                    if edits.title is not None and edits.title:  # Check for non-empty string
                        metadata_args.extend(
                            [f"-metadata:{output_metadata_specifier}", f"title={shlex.quote(str(edits.title))}"])
                    # Apply language if present and valid
                    if edits.language is not None and edits.language:
                        lang = str(edits.language).lower()
                        if len(lang) == 3 and lang.isalpha():  # Basic 3-letter validation
                            metadata_args.extend([f"-metadata:{output_metadata_specifier}", f"language={lang}"])
                        else:
                            print(
                                f"    Warning: Invalid language code '{edits.language}' for {output_metadata_specifier}. Skipping.")
                else:
                    print(
                        f"    Warning: Could not map original track {track_id_from_user} to an output stream for metadata edits.")

        else:
            print("  Skipping track-specific metadata edits (no data provided or source invalid).")

        main_cmd_parts.extend(metadata_args)
        return temp_files_for_metadata

    def _build_encoding_parameters(self,
                                   main_cmd_parts: List[str],
                                   encoding_params_str: str,
                                   map_commands: List[str]):
        """Builds the encoding part of the FFmpeg command based on settings or manual override."""

        # If manual parameters are provided, use them exclusively for encoding
        if encoding_params_str:
            print(f"  Using manual encoding parameters: {encoding_params_str}")
            try:
                # Split the manual string into arguments respecting quotes
                user_params = shlex.split(encoding_params_str)
                main_cmd_parts.extend(user_params)
            except ValueError as e:
                raise CommandGenerationError(f"Invalid syntax in manual encoding parameters: {e}")
        else:
            # Otherwise, build parameters from individual settings
            print("  Using encoding parameters from settings:")
            # Video encoding
            main_cmd_parts.extend(['-c:v', self.video_codec])
            if self.video_preset: main_cmd_parts.extend(['-preset', self.video_preset])

            # Video rate control (Bitrate has priority over CQ/CRF)
            if self.video_bitrate and self.video_bitrate != "0":
                print(f"    Video: Bitrate={self.video_bitrate}")
                main_cmd_parts.extend(['-b:v', self.video_bitrate])
                if self.video_cq: print("      (Note: CQ setting ignored because bitrate is specified)")
            elif self.video_cq:  # Use CQ/CRF if bitrate is not set (or set to 0)
                print(f"    Video: CQ/CRF={self.video_cq}")
                # Add both -cq and -crf for broader codec compatibility (ffmpeg usually picks the right one)
                main_cmd_parts.extend(['-cq:v', self.video_cq])
                main_cmd_parts.extend(['-crf', self.video_cq])
                main_cmd_parts.extend(['-b:v', '0'])  # Explicitly disable fixed bitrate
            else:
                print("    Video: No bitrate or CQ specified, using codec defaults.")

            # Audio encoding (only if audio is being mapped)
            if any(cmd.startswith('-map') and (':a:' in cmd or '[a' in cmd) for cmd in map_commands):
                print(f"    Audio: Codec={self.audio_codec}, Bitrate={self.audio_bitrate}")
                main_cmd_parts.extend(['-c:a', self.audio_codec])
                # Apply bitrate only if not copying audio
                if self.audio_bitrate and self.audio_codec != 'copy':
                    main_cmd_parts.extend(['-b:a', self.audio_bitrate])

            # Video FPS (optional override from GUI/config using self.video_fps string)
            # <<< CHANGED: Use the string override directly >>>
            if self.video_fps:
                print(f"    Video: Target FPS Override (user input)={self.video_fps}")
                # Use the original string from user input for the -r option
                main_cmd_parts.extend(['-r', self.video_fps])

            # Additional user-defined parameters
            if self.additional_encoding:
                print(f"    Additional Params: {self.additional_encoding}")
                try:
                    add_params = shlex.split(self.additional_encoding)
                    main_cmd_parts.extend(add_params)
                except ValueError as e:
                    raise CommandGenerationError(f"Invalid syntax in additional encoding parameters: {e}")

    @staticmethod
    def _finalize_main_command(main_cmd_parts: List[str],
                               final_duration_estimate: float,
                               output_file: str):
        """Adds final options like duration, output file, and format-specific flags."""
        # Ensure a duration is set if not already present (important for concat/overlay)
        has_duration_flag = any(part == '-t' for part in main_cmd_parts)
        if not has_duration_flag and final_duration_estimate > 0:
            main_cmd_parts.extend(['-t', f"{final_duration_estimate:.6f}"])

        # Add +faststart flag for MP4 output if not already handled by movflags
        if output_file.lower().endswith(".mp4"):
            movflags_val = "+faststart"
            movflags_present = False
            temp_cmd_parts = []
            skip_next = False
            for i, part in enumerate(main_cmd_parts):
                if skip_next:
                    skip_next = False
                    continue
                if part == "-movflags":
                    movflags_present = True
                    current_val = movflags_val
                    # Check if a value follows -movflags
                    if i + 1 < len(main_cmd_parts) and not main_cmd_parts[i + 1].startswith('-'):
                        existing_flags_str = main_cmd_parts[i + 1]
                        skip_next = True  # Skip the existing value in the next iteration
                        # Merge flags: remove leading '+' if present, split by '+', add faststart, join back
                        flags = set(f.strip() for f in existing_flags_str.replace('+', ' ').split() if f.strip())
                        flags.add("faststart")
                        current_val = "+" + "+".join(sorted(list(flags)))
                        print(f"  Merging -movflags: '{existing_flags_str}' -> '{current_val}'")
                    else:  # -movflags was present without a value, just add +faststart
                        print(f"  Adding -movflags value: '{current_val}'")
                    temp_cmd_parts.extend([part, current_val])
                else:
                    temp_cmd_parts.append(part)

            # If -movflags wasn't present at all, add it
            if not movflags_present:
                print(f"  Adding -movflags: '{movflags_val}' for MP4 output.")
                temp_cmd_parts.extend(["-movflags", movflags_val])
            main_cmd_parts[:] = temp_cmd_parts  # Modify list in place

        # Add the output file path (quoted)
        main_cmd_parts.append(f'"{output_file}"')

    def _generate_main_ffmpeg_command(self,  # This still generates the full command string *args*
                                      input_file: str, output_file: str, encoding_params_str: str,
                                      target_params: TargetParams,
                                      main_video_duration: float, track_data_dict: Dict[str, Dict[str, str]],
                                      concatenated_banner_track_path: Optional[str],
                                      original_banner_duration: Optional[float],
                                      banner_timecodes: Optional[List[str]],
                                      moving_file: Optional[str],
                                      is_concat_mode: bool, concat_list_path: Optional[str],
                                      sorted_embed_ads: List[AdInsertionInfo], total_embed_duration_added: float
                                      ) -> Tuple[str, List[str]]:
        """ Generates the main FFmpeg command *arguments* string (without 'ffmpeg' prefix). """
        print("--- Phase 3: Generating Main Conversion Command Arguments ---")
        # Start building command list *without* 'ffmpeg'
        main_cmd_parts = ["-y", '-hide_banner']
        if self.hwaccel and self.hwaccel != "none":
            main_cmd_parts.extend(['-hwaccel', self.hwaccel])

        final_duration_estimate = main_video_duration + total_embed_duration_added
        print(f"Estimated final duration: {final_duration_estimate:.3f}s")

        # Define Inputs (returns list of (options, path))
        input_definitions, base_video_specifier, base_audio_specifier, \
            subtitle_input_specifier, banner_track_input_idx, moving_input_idx, \
            metadata_input_index = self._define_main_command_inputs(
            input_file, target_params, concatenated_banner_track_path, moving_file,
            is_concat_mode, concat_list_path
        )
        for options, path in input_definitions:
            main_cmd_parts.extend(options)
            main_cmd_parts.extend(["-i", f'"{path}"'])

        # Build Filter Complex
        filter_complex_str, final_video_map_label, final_audio_map_label = self._build_filter_complex(
            base_video_specifier.rstrip('?'),
            base_audio_specifier.rstrip('?') if base_audio_specifier else None,
            target_params, final_duration_estimate, is_concat_mode,
            sorted_embed_ads,
            banner_track_input_idx,
            original_banner_duration,
            banner_timecodes,
            moving_file,
            moving_input_idx
        )

        # Apply Filters and Mapping
        map_commands = self._apply_filters_and_mapping(
            main_cmd_parts, filter_complex_str, final_video_map_label, final_audio_map_label,
            base_video_specifier, base_audio_specifier, subtitle_input_specifier, target_params
        )

        # Handle Metadata
        track_data_edits: Dict[str, TrackMetadataEdits] = {
            track_id: TrackMetadataEdits(title=edits.get('title'), language=edits.get('language'))
            for track_id, edits in track_data_dict.items()
        }
        temp_files_for_main = self._handle_metadata(
            main_cmd_parts, track_data_edits, metadata_input_index, input_definitions, map_commands,
            base_video_specifier, filter_complex_str
        )

        # Build Encoding Parameters
        self._build_encoding_parameters(main_cmd_parts, encoding_params_str, map_commands)

        # Finalize Command (Duration, Output, Flags)
        self._finalize_main_command(main_cmd_parts, final_duration_estimate, output_file)

        # Join all parts into the final command *arguments* string
        final_main_cmd_args = " ".join(main_cmd_parts)
        return final_main_cmd_args, temp_files_for_main

    def generate_ffmpeg_commands(self,
                                 input_file: str, output_file: str, encoding_params_str: str,
                                 track_data: Dict[str, Dict[str, str]],
                                 embed_ads: List[Dict],
                                 banner_file: Optional[str], banner_timecodes: Optional[List[str]],
                                 moving_file: Optional[str]):
        """
        Generates all necessary FFmpeg command **argument strings** for the conversion process.
        These strings should be passed to run_ffmpeg_command along with the executable path.

        Returns:
            A tuple containing:
            - List[str]: Preprocessing FFmpeg command argument strings.
            - str: The main FFmpeg conversion command argument string.
            - List[str]: A list of temporary file paths created during generation.
        """
        all_preprocessing_commands_args = []  # Store argument strings now
        all_temp_files = []
        concatenated_banner_track_path = None
        total_embed_duration_added = 0.0
        concat_list_path_main = None

        print("--- Getting Main Video Parameters ---")
        main_video_params: Optional[StreamParams] = self.get_essential_stream_params(input_file)
        if not main_video_params:
            raise CommandGenerationError(f"Could not get essential parameters from: {input_file}")
        main_video_duration: Optional[float] = self.get_media_duration(input_file)

        print("--- Validating and Preparing Inputs ---")
        try:
            valid_params, valid_duration, sorted_embed_ads_info, \
                valid_banner_file, valid_banner_timecodes, valid_moving_file, \
                original_banner_duration = self._validate_and_prepare_inputs(
                input_file, output_file, main_video_params, main_video_duration,
                embed_ads, banner_file, banner_timecodes, moving_file)
            banner_file = valid_banner_file
            banner_timecodes = valid_banner_timecodes
            moving_file = valid_moving_file
            main_video_params = valid_params
            main_video_duration = valid_duration
        except CommandGenerationError as e:
            print(f"Input validation failed: {e}")
            raise

        print("--- Determining Target Encoding Parameters ---")
        target_params: TargetParams = self._determine_target_parameters(main_video_params)

        is_concat_mode = bool(sorted_embed_ads_info)
        if is_concat_mode:
            total_embed_duration_added = sum(ad.duration for ad in sorted_embed_ads_info)
        final_duration_estimate = main_video_duration + total_embed_duration_added
        print(f"Estimated final duration (with ads, if any): {final_duration_estimate:.3f}s")

        # Generate preprocessing for concatenation if needed
        if is_concat_mode:
            try:
                prep_cmds_main_args, concat_list_path_main, prep_temp_files_main, _ = \
                    self._generate_preprocessing_for_concat(  # Returns command args strings
                        input_file, sorted_embed_ads_info, target_params, main_video_duration
                    )
                all_preprocessing_commands_args.extend(prep_cmds_main_args)
                all_temp_files.extend(prep_temp_files_main)
            except CommandGenerationError as e:
                utils.cleanup_temp_files(all_temp_files)
                print(f"Error during video+ad preprocessing: {e}")
                raise e
        else:
            print("--- Preprocessing: Video+Ad concatenation not required ---")

        # Generate preprocessing for banner track if needed
        if banner_file and banner_timecodes and original_banner_duration is not None:
            try:
                prep_cmds_banner_args, _, prep_temp_files_banner, concatenated_banner_path = \
                    self._generate_banner_preprocessing_commands(  # Returns command args strings
                        banner_file, banner_timecodes, original_banner_duration, target_params,
                        final_duration_estimate,
                        is_concat_mode, sorted_embed_ads_info
                    )
                all_preprocessing_commands_args.extend(prep_cmds_banner_args)
                all_temp_files.extend(prep_temp_files_banner)
                concatenated_banner_track_path = concatenated_banner_path
            except CommandGenerationError as e:
                utils.cleanup_temp_files(all_temp_files)
                print(f"Error generating banner track: {e}")
                raise e
            except Exception as e:
                utils.cleanup_temp_files(all_temp_files)
                print(f"Unexpected error generating banner track: {type(e).__name__} - {e}")
                raise CommandGenerationError(f"Failed to generate banner track: {e}") from e
        else:
            print("--- Preprocessing: Banner track not required or invalid ---")

        print("--- Generating Main FFmpeg Command Arguments ---")
        try:
            # Returns command args string
            main_command_args, main_temp_files = self._generate_main_ffmpeg_command(
                input_file=input_file,
                output_file=output_file,
                encoding_params_str=encoding_params_str,
                target_params=target_params,
                main_video_duration=main_video_duration,
                track_data_dict=track_data,
                concatenated_banner_track_path=concatenated_banner_track_path,
                original_banner_duration=original_banner_duration,
                banner_timecodes=banner_timecodes,
                moving_file=moving_file,
                is_concat_mode=is_concat_mode,
                concat_list_path=concat_list_path_main,
                sorted_embed_ads=sorted_embed_ads_info,
                total_embed_duration_added=total_embed_duration_added
            )
            all_temp_files.extend(main_temp_files)
        except CommandGenerationError as e:
            utils.cleanup_temp_files(all_temp_files)
            print(f"Error generating main command args: {e}")
            raise e
        except Exception as e:
            utils.cleanup_temp_files(all_temp_files)
            print(f"Unexpected error generating main command args: {type(e).__name__} - {e}")
            raise CommandGenerationError(f"Failed to generate main command args: {e}") from e

        unique_temp_files = sorted(list(set(all_temp_files)))
        # Return the *argument* strings
        return all_preprocessing_commands_args, main_command_args, unique_temp_files

    def _generate_gap_command(self, output_path: str, width: int, height: int, fps: float, duration: float,
                              pix_fmt: str, color: str) -> str:
        """Generates command args for creating a solid color gap segment."""
        target_fps_str = str(fps)
        # Command parts *without* the initial 'ffmpeg'
        gap_cmd_parts = [
            "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s={width}x{height}:d={duration:.6f}:r={target_fps_str}",
            "-vf", f"format=pix_fmts={pix_fmt}",
            "-c:v", self.video_codec, "-preset", self.video_preset, "-crf", "0",  # Encode efficiently
            "-an",
            "-video_track_timescale", "90000",  # Use a common timescale
            "-t", f"{duration:.6f}",
            f'"{output_path}"'
        ]
        return " ".join(gap_cmd_parts)

    def _generate_concat_command(self, list_path: str, output_path: str, duration: Optional[float] = None) -> str:
        """Generates command args for concatenating using a list file."""
        # Command parts *without* the initial 'ffmpeg'
        concat_cmd_parts = [
            "-y",
            "-f", "concat", "-safe", "0",
            "-i", f'"{list_path}"',
            "-c", "copy"
        ]
        if duration is not None:
            concat_cmd_parts.extend(["-t", f"{duration:.6f}"])
        concat_cmd_parts.append(f'"{output_path}"')
        return " ".join(concat_cmd_parts)
