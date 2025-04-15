# converter/ffmpeg_utils.py
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
    fps_str: Optional[str] = None
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
    fps_str: Optional[str] = None
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
            video_codec: Target video codec (e.g., 'libx264', 'h264_nvenc'). Defaults to config.
            video_preset: Encoding preset (e.g., 'medium', 'fast'). Defaults to config.
            video_cq: Constant Quality value (e.g., '23'). Defaults to config.
            video_bitrate: Target video bitrate (e.g., '5000k', '0' to disable). Defaults to config.
            audio_codec: Target audio codec (e.g., 'aac', 'copy'). Defaults to config.
            audio_bitrate: Target audio bitrate (e.g., '192k'). Defaults to config.
            video_fps: Target video framerate (e.g., '25', '30000/1001'). Defaults to None (no change).
            moving_speed: Speed factor for moving logo animation. Defaults to config.
            moving_logo_relative_height: Height of moving logo relative to video height. Defaults to config.
            moving_logo_alpha: Alpha transparency of moving logo (0.0 to 1.0). Defaults to config.
            banner_track_pix_fmt: Pixel format for the banner track. Defaults to config.
            banner_gap_color: Color for gaps in the banner track (FFmpeg color syntax). Defaults to config.
            hwaccel: Hardware acceleration method (e.g., 'cuda', 'd3d11va', 'auto'). Defaults to config.
            additional_encoding: Extra FFmpeg command-line parameters for the main encoding step. Defaults to config.
        """
        self.video_codec = video_codec if video_codec is not None else config.VIDEO_CODEC
        self.video_preset = video_preset if video_preset is not None else config.VIDEO_PRESET
        self.video_cq = video_cq if video_cq is not None else config.VIDEO_CQ  # Allow empty string from GUI
        self.video_bitrate = video_bitrate if video_bitrate is not None else config.VIDEO_BITRATE
        self.audio_codec = audio_codec if audio_codec is not None else config.AUDIO_CODEC
        self.audio_bitrate = audio_bitrate if audio_bitrate is not None else config.AUDIO_BITRATE
        self.video_fps = video_fps  # Keep None if not specified, don't default
        self.moving_speed = moving_speed if moving_speed is not None else config.MOVING_SPEED
        self.moving_logo_relative_height = moving_logo_relative_height if moving_logo_relative_height is not None else config.MOVING_LOGO_RELATIVE_HEIGHT
        self.moving_logo_alpha = moving_logo_alpha if moving_logo_alpha is not None else config.MOVING_LOGO_ALPHA
        self.banner_track_pix_fmt = banner_track_pix_fmt if banner_track_pix_fmt is not None else config.BANNER_TRACK_PIX_FMT
        self.banner_gap_color = banner_gap_color if banner_gap_color is not None else config.BANNER_GAP_COLOR
        self.hwaccel = hwaccel if hwaccel is not None else config.HWACCEL
        # Allow additional_encoding to be None if not provided or empty string from GUI
        self.additional_encoding = additional_encoding if additional_encoding else config.ADDITIONAL_ENCODING

    @staticmethod
    def run_ffprobe(command: List[str]) -> Dict[str, Any]:
        """Runs a ffprobe command and returns the parsed JSON output."""
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            result = subprocess.run(command, capture_output=True, text=True, check=True,
                                    encoding='utf-8', errors='replace', startupinfo=startupinfo)
            return json.loads(result.stdout)
        except FileNotFoundError:
            raise FfprobeError(
                "ffprobe not found. Ensure FFmpeg (including ffprobe) is installed and in the system PATH.")
        except subprocess.CalledProcessError as e:
            stderr_tail = e.stderr[-1000:] if e.stderr else "N/A"
            raise FfprobeError(
                f"ffprobe execution failed: {e}\nCommand: {' '.join(command)}\nStderr (tail): {stderr_tail}")
        except json.JSONDecodeError as e:
            stdout_content = getattr(e, 'doc', "N/A")[:500]
            raise FfprobeError(
                f"Error decoding ffprobe output: {e}\nCommand: {' '.join(command)}\nStdout (start): {stdout_content}")
        except Exception as e:
            raise FfprobeError(f"Unexpected error during ffprobe execution: {e}\nCommand: {' '.join(command)}")

    def get_media_duration(self, file_path: str) -> Optional[float]:
        """Gets media duration using ffprobe. Returns None for images, errors, or very short clips."""
        if not file_path or not os.path.exists(file_path):
            return None
        duration = None
        try:
            # First try format duration
            command_fmt = ["ffprobe", "-v", "quiet", "-i", file_path,
                           "-show_entries", "format=duration",
                           "-print_format", "json"]
            output_fmt = self.run_ffprobe(command_fmt)
            duration_str_fmt = output_fmt.get("format", {}).get("duration")
            if duration_str_fmt and duration_str_fmt != "N/A":
                try:
                    duration = float(duration_str_fmt)
                except (ValueError, TypeError):
                    pass  # Ignore conversion errors here

            # If format duration failed or is zero, try the first video stream duration
            if duration is None or duration <= 0:
                command_stream = ["ffprobe", "-v", "quiet", "-i", file_path,
                                  "-select_streams", "v:0", "-show_entries", "stream=duration",
                                  "-print_format", "json"]
                try:
                    output_stream = self.run_ffprobe(command_stream)
                    stream_info = output_stream.get("streams", [])
                    if stream_info:
                        duration_str_stream = stream_info[0].get("duration")
                        if duration_str_stream and duration_str_stream != "N/A":
                            try:
                                stream_duration = float(duration_str_stream)
                                if stream_duration > 0:
                                    duration = stream_duration
                            except (ValueError, TypeError):
                                pass  # Ignore conversion errors
                except FfprobeError:
                    pass  # Ignore if stream probing fails (e.g., audio-only)

            # Return duration only if it's meaningfully positive
            if duration and duration > 0.01:
                return duration
            else:
                # Could be an image or invalid file
                return None

        except FfprobeError:
            # Likely an invalid file or ffprobe issue
            return None
        except Exception as e:
            print(f"Unexpected error in get_media_duration for {file_path}: {e}")
            return None

    @classmethod
    def get_stream_info(cls, file_path: str) -> Dict[str, Any]:
        """Gets info about all streams and format using ffprobe."""
        if not file_path or not os.path.exists(file_path):
            return {}
        command = ["ffprobe", "-v", "quiet", "-i", file_path,
                   "-show_streams", "-show_format", "-print_format", "json"]
        try:
            return cls.run_ffprobe(command)
        except FfprobeError as e:
            print(f"Failed to get stream info for {file_path}: {e}")
            return {}
        except Exception as e:
            print(f"Unexpected error getting stream info for {file_path}: {e}")
            return {}

    def get_essential_stream_params(self, file_path: str) -> Optional[StreamParams]:
        """Gets key video and audio parameters needed for compatibility checks using ffprobe."""
        params = StreamParams()  # Instantiate the dataclass with defaults
        if not file_path or not os.path.exists(file_path): return None

        has_video_stream = False
        try:
            # Probe video stream first
            cmd_video = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries",
                         "stream=width,height,pix_fmt,sample_aspect_ratio,display_aspect_ratio,r_frame_rate,time_base,codec_name",
                         "-of", "json", file_path]
            data_v = self.run_ffprobe(cmd_video)
            if data_v.get("streams"):
                stream_v = data_v["streams"][0]
                # Use integer conversion where appropriate
                params.width = stream_v.get('width')
                params.height = stream_v.get('height')
                params.pix_fmt = stream_v.get('pix_fmt')
                # Ensure SAR is valid or default
                sar_str = stream_v.get('sample_aspect_ratio', '1:1')
                params.sar = sar_str if ':' in sar_str and len(sar_str.split(':')) == 2 else '1:1'
                params.time_base_v = stream_v.get('time_base')
                params.fps_str = stream_v.get('r_frame_rate')  # Keep as string (e.g., "30000/1001")

                # Check if essential video params were found
                if all([params.width, params.height, params.fps_str, params.time_base_v]):
                    has_video_stream = True
                    # Provide a default pix_fmt if missing but other params are present
                    if not params.pix_fmt: params.pix_fmt = 'yuv420p'
            # Clean up SAR again just in case ffprobe returned something weird
            if ':' not in params.sar or len(params.sar.split(':')) != 2: params.sar = '1:1'

        except FfprobeError:
            pass  # Expected if no video stream exists
        except Exception as e:
            print(f"Unexpected error probing video stream for {file_path}: {e}")

        # If no video stream, check if it's a known image format
        if not has_video_stream:
            try:
                cmd_format = ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "json", file_path]
                data_fmt = self.run_ffprobe(cmd_format)
                format_name = data_fmt.get("format", {}).get("format_name", "")
                # List of common image muxer/pipe formats reported by ffprobe
                image_formats = ['image2', 'png_pipe', 'mjpeg', 'webp_pipe', 'gif', 'tiff_pipe', 'bmp_pipe',
                                 'jpeg_pipe',
                                 'ppm_pipe', 'pgm_pipe', 'pbm_pipe', 'apng']
                if any(fmt in format_name for fmt in image_formats):
                    # Try getting image stream info
                    cmd_img_stream = ["ffprobe", "-v", "error", "-select_streams", "0",  # Select first stream
                                      "-show_entries", "stream=width,height,pix_fmt,codec_type", "-of", "json",
                                      file_path]
                    data_img_s = self.run_ffprobe(cmd_img_stream)
                    if data_img_s.get("streams"):
                        stream_img = data_img_s["streams"][0]
                        if stream_img.get('codec_type') == 'video':  # Ensure it's treated as video
                            params.width = stream_img.get('width')
                            params.height = stream_img.get('height')
                            params.pix_fmt = stream_img.get('pix_fmt', 'rgb24')  # Default for images
                            params.fps_str = '25/1'  # Assign a default FPS for images
                            params.time_base_v = '1/25'  # Assign a default timebase
                            params.sar = '1:1'  # Assume square pixels
                            print(
                                f"Info: {os.path.basename(file_path)} detected as image format ({format_name}). Using defaults.")
                            # has_video_stream = True # Treat image as video for processing
            except FfprobeError:
                pass  # Expected if format or image stream probe fails
            except Exception as e:
                print(f"Unexpected error handling potential image file {file_path}: {e}")

        # If we still lack essential video parameters, fail
        if not all([params.width, params.height, params.fps_str]):
            print(f"Critical Error: Could not determine essential video parameters (width/height/fps) for {file_path}.")
            return None

        # Now probe for audio stream
        try:
            cmd_audio = ["ffprobe", "-v", "error", "-select_streams", "a:0",
                         "-show_entries", "stream=sample_rate,channel_layout,sample_fmt,time_base", "-of", "json",
                         file_path]
            data_a = self.run_ffprobe(cmd_audio)
            if data_a.get("streams"):
                stream_a = data_a["streams"][0]
                try:
                    params.sample_rate = int(stream_a.get('sample_rate')) if stream_a.get('sample_rate') else None
                except ValueError:
                    params.sample_rate = None  # Handle non-integer sample rate
                params.channel_layout = stream_a.get('channel_layout')
                params.sample_fmt = stream_a.get('sample_fmt')
                params.time_base_a = stream_a.get('time_base')
                # Check if essential audio params were found
                if all([params.sample_rate, params.channel_layout, params.sample_fmt, params.time_base_a]):
                    params.has_audio = True
                else:
                    params.has_audio = False  # Mark as no audio if params are incomplete
            else:
                params.has_audio = False  # No audio stream found
        except FfprobeError:
            params.has_audio = False  # Expected if no audio stream
        except Exception as e:
            print(f"Unexpected error probing audio stream for {file_path}: {e}")
            params.has_audio = False

        # Normalize/Default potentially problematic values
        common_pix_fmts = ['yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p', 'yuv444p', 'yuvj444p', 'nv12', 'nv21',
                           'yuva420p', 'rgba', 'bgra', 'rgb24', 'gray', 'gbrp', 'yuv420p10le']
        # Use a safe default if the detected pix_fmt is uncommon or potentially incompatible
        if params.pix_fmt not in common_pix_fmts:
            print(f"Warning: Uncommon pix_fmt '{params.pix_fmt}' detected for {file_path}. Defaulting to 'yuv420p'.")
            params.pix_fmt = 'yuv420p'

        # Ensure SAR is valid one last time
        if ':' not in params.sar or len(params.sar.split(':')) != 2: params.sar = '1:1'

        # Default audio parameters if audio exists but some info was missing
        if params.has_audio:
            if not params.channel_layout: params.channel_layout = 'stereo'  # Common default
            if not params.sample_fmt: params.sample_fmt = 'fltp'  # Common default for AAC/Opus

        return params

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
        target_params.fps_str = main_video_params.fps_str  # Keep as string
        target_params.pix_fmt = main_video_params.pix_fmt or 'yuv420p'  # Already validated/defaulted
        target_params.v_timebase = main_video_params.time_base_v
        target_params.sample_rate = main_video_params.sample_rate
        target_params.channel_layout = main_video_params.channel_layout or 'stereo'  # Defaulted if needed
        target_params.sample_fmt = main_video_params.sample_fmt or 'fltp'  # Defaulted if needed
        target_params.a_timebase = main_video_params.time_base_a
        target_params.has_audio = main_video_params.has_audio

        # Validate that essential parameters were successfully determined from main video
        essential_video_attrs = ['width', 'height', 'fps_str', 'pix_fmt', 'v_timebase', 'sar']
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

        print(
            f"Target parameters determined: Res={target_params.width}x{target_params.height}, FPS={target_params.fps_str}, PixFmt={target_params.pix_fmt}, SAR={target_params.sar}, Audio={target_params.has_audio}")
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
        """ Helper function to create a single segment transcoding/generation command. """
        sar_value = target_params.sar.replace(':', '/')  # Format for setsar filter
        # Use specific output pix_fmt if provided, otherwise target pix_fmt
        final_pix_fmt = output_pix_fmt if output_pix_fmt else target_params.pix_fmt
        vf_parts = []

        # Different scaling/padding logic for banner vs. main/ad content
        if is_banner_segment:
            # Determine scaled height based on target width, maintaining banner aspect ratio if possible
            banner_params: Optional[StreamParams] = self.get_essential_stream_params(
                input_path)  # Get banner dimensions
            target_w = target_params.width
            # Default scaled height to 1/10th of main video height if banner info fails
            scaled_h = target_params.height // 10 if target_params.height else 72  # Fallback if target height unknown
            if banner_params and banner_params.width and banner_params.height:
                orig_w, orig_h = banner_params.width, banner_params.height
                # Calculate height to maintain aspect ratio at target width
                scaled_h = max(1, int(orig_h * (target_w / orig_w))) if orig_w > 0 else scaled_h

            # Scale banner to target width and calculated height, set SAR
            vf_parts.extend([
                f"scale={target_w}:{scaled_h}:flags=bicubic",
                f"setsar=sar={sar_value}"  # Keep SAR consistent
            ])
            # Override pix_fmt for banner track using instance setting
            final_pix_fmt = self.banner_track_pix_fmt
        else:
            # For main/ad content: scale to fit within target dimensions, pad if needed
            # Ensure target dimensions are available
            if target_params.width and target_params.height:
                vf_parts.extend([
                    f"scale={target_params.width}:{target_params.height}:force_original_aspect_ratio=decrease:flags=bicubic",
                    # Scale down to fit
                    f"pad={target_params.width}:{target_params.height}:(ow-iw)/2:(oh-ih)/2:color=black",
                    # Pad to target size
                    f"setsar=sar={sar_value}"  # Ensure target SAR
                ])
            else:
                # Fallback if target dimensions unknown - just set SAR
                vf_parts.append(f"setsar=sar={sar_value}")

        # Apply FPS conversion if requested and target FPS is set
        if force_fps and target_params.fps_str:
            vf_parts.append(f"fps={target_params.fps_str}")  # Force target framerate
        # Ensure final pixel format
        vf_parts.append(f"format=pix_fmts={final_pix_fmt}")
        vf_string = ",".join(vf_parts)

        # --- Audio Filter Setup ---
        af_string = None
        create_audio = target_params.has_audio and output_audio and not is_banner_segment
        if create_audio:
            # Ensure required audio parameters are present
            if target_params.sample_rate and target_params.sample_fmt and target_params.channel_layout:
                af_parts = [
                    f"aresample=resampler=soxr:osr={target_params.sample_rate}",  # Resample audio
                    f"aformat=sample_fmts={target_params.sample_fmt}:channel_layouts={target_params.channel_layout}"
                    # Format audio
                ]
                af_string = ",".join(af_parts)
            else:
                print("Warning: Target audio parameters missing, cannot create audio stream for segment.")
                create_audio = False  # Disable audio creation

        # --- Command Assembly ---
        cmd_parts = ["ffmpeg", "-y"]  # Overwrite output

        # Input options
        input_options = []
        # Check if input is likely an image (no duration) to add loop
        is_image_input = self.get_media_duration(input_path) is None
        if is_image_input:
            input_options.extend(["-loop", "1"])  # Loop images indefinitely

        # Add start time if specified
        if start_time is not None and start_time > 0.001:
            input_options.extend(["-ss", f"{start_time:.6f}"])

        input_options.extend(["-i", f'"{input_path}"'])  # Input file
        cmd_parts.extend(input_options)

        # Add duration if specified
        if duration is not None:
            cmd_parts.extend(["-t", f"{duration:.6f}"])

        # Core processing options
        cmd_parts.extend(["-avoid_negative_ts", "make_zero"])  # Handle potential timestamp issues
        cmd_parts.extend(["-vf", f'"{vf_string}"'])  # Video filtergraph

        if af_string:
            cmd_parts.extend(["-af", f'"{af_string}"'])  # Audio filtergraph
        elif not create_audio:
            cmd_parts.extend(["-an"])  # Disable audio explicitly

        # Encoding options (using instance settings)
        # Use CQ for segments unless bitrate is explicitly set (might reconsider this logic)
        # For intermediate files, high quality is usually desired, so CQ/CRF is good.
        cmd_parts.extend(["-c:v", self.video_codec, "-preset", self.video_preset])
        # Prefer CQ for intermediate files if available, otherwise maybe a high fixed bitrate?
        # Using CQ=0 or CRF=0 often means lossless, which might be too large. Let's stick to the configured CQ.
        if self.video_cq:
            cmd_parts.extend(["-cq:v", str(self.video_cq)])  # Use CQ if set
        elif self.video_bitrate and self.video_bitrate != "0":
            cmd_parts.extend(["-b:v", self.video_bitrate])  # Fallback to bitrate if CQ not set
        else:
            cmd_parts.extend(["-crf", "18"])  # Default to a high quality CRF if neither CQ nor bitrate specified

        if create_audio:
            cmd_parts.extend(["-c:a", self.audio_codec, "-b:a", self.audio_bitrate])  # Audio encoding

        # Set timescale for consistent segment concatenation
        cmd_parts.extend(["-video_track_timescale", target_params.video_timescale])

        # Mapping (simple for single input)
        cmd_parts.extend(["-map", "0:v:0?"])  # Map first video stream if exists
        if create_audio:
            cmd_parts.extend(["-map", "0:a:0?"])  # Map first audio stream if exists

        # Output file
        cmd_parts.append(f'"{output_path}"')

        # Filter out None values just in case (though unlikely)
        return " ".join([p for p in cmd_parts if p is not None])

    def _generate_preprocessing_for_concat(self, input_file: str, sorted_embed_ads: List[AdInsertionInfo],
                                           target_params: TargetParams,
                                           main_video_duration: float) -> Tuple[List[str], str, List[str], float]:
        """ Generates preprocessing commands for main video segments and ads, and the concat list file. """
        print("--- Phase 1.1: Generating Segment Preprocessing Commands (Video + Ads) ---")
        preprocessing_commands, temp_files_generated, concat_list_items = [], [], []
        total_ad_duration_sum, segment_counter, last_original_time = 0.0, 0, 0.0
        # Cache preprocessed ads to avoid re-encoding the same ad file multiple times
        unique_ad_files_cache: Dict[
            str, Dict[str, Any]] = {}  # {original_ad_path: {'data': AdInsertionInfo, 'temp_path': processed_path}}

        print("  Preprocessing unique ad files...")
        for ad_data in sorted_embed_ads:
            ad_path = ad_data.path
            if ad_path not in unique_ad_files_cache:
                # Generate a unique temp filename for this processed ad
                temp_ad_path = utils.generate_temp_filename("ad_segment_uniq", segment_counter)
                # Create the command to transcode this ad to target parameters
                cmd = self._create_segment_command(
                    input_path=ad_path,
                    output_path=temp_ad_path,
                    target_params=target_params,
                    duration=ad_data.duration,  # Use the determined duration
                    output_audio=target_params.has_audio,  # Include audio if target has it
                    force_fps=True  # Ensure consistent FPS
                )
                preprocessing_commands.append(cmd)
                temp_files_generated.append(temp_ad_path)
                # Store info about the processed ad in the cache
                unique_ad_files_cache[ad_path] = {'data': ad_data, 'temp_path': temp_ad_path}
                segment_counter += 1

        print("  Generating main video segments and concat list...")
        # Iterate through sorted ads to create main video segments between them
        for i, embed_ad_info in enumerate(sorted_embed_ads):
            embed_original_time_sec = embed_ad_info.time_sec
            original_ad_path = embed_ad_info.path

            # Retrieve the preprocessed ad info from the cache
            cached_ad_info = unique_ad_files_cache.get(original_ad_path)
            if not cached_ad_info:
                print(f"Error: Could not find preprocessed ad for {original_ad_path}. Skipping insertion.")
                continue  # Should not happen if preprocessing logic is correct

            preprocessed_ad_path = cached_ad_info['temp_path']
            ad_duration = cached_ad_info['data'].duration  # Access duration from cached AdInsertionInfo

            # Calculate duration of the main video segment *before* this ad
            main_segment_duration = embed_original_time_sec - last_original_time

            # Create a segment command for the main video if duration is significant
            if main_segment_duration > 0.001:
                temp_main_path = utils.generate_temp_filename("main_segment", segment_counter)
                cmd = self._create_segment_command(
                    input_path=input_file,
                    output_path=temp_main_path,
                    target_params=target_params,
                    start_time=last_original_time,  # Start from end of last segment/ad
                    duration=main_segment_duration,
                    output_audio=target_params.has_audio,
                    force_fps=True
                )
                preprocessing_commands.append(cmd)
                temp_files_generated.append(temp_main_path)
                # Add the path of the processed main segment to the concat list
                concat_list_items.append(temp_main_path)
                segment_counter += 1

            # Add the path of the *preprocessed* ad segment to the concat list
            concat_list_items.append(preprocessed_ad_path)
            total_ad_duration_sum += ad_duration  # Accumulate duration from cached data
            # Update the time marker to the end of this ad's *original* time position
            last_original_time = embed_original_time_sec

        # Create the final segment of the main video (after the last ad)
        if main_video_duration - last_original_time > 0.001:
            final_segment_duration = main_video_duration - last_original_time
            temp_main_path = utils.generate_temp_filename("main_segment", segment_counter)
            cmd = self._create_segment_command(
                input_path=input_file,
                output_path=temp_main_path,
                target_params=target_params,
                start_time=last_original_time,
                duration=final_segment_duration,
                output_audio=target_params.has_audio,
                force_fps=True
            )
            preprocessing_commands.append(cmd)
            temp_files_generated.append(temp_main_path)
            concat_list_items.append(temp_main_path)

        if not concat_list_items:
            raise CommandGenerationError("No main video/ad segments generated for concatenation.")

        # --- Phase 1.2: Create Concat List File ---
        print("--- Phase 1.2: Creating Concat List File for Main Video + Ads ---")
        concat_list_filename = f"concat_list_main_{int(time.time())}.txt"
        concat_list_path = os.path.join(utils.generate_temp_filename("", 0, "").rsplit(os.sep, 1)[0],
                                        concat_list_filename)  # Use helper to get temp dir
        temp_files_generated.append(concat_list_path)  # Ensure list file is cleaned up

        try:
            with open(concat_list_path, 'w', encoding='utf-8') as f:
                f.write("ffconcat version 1.0\n\n")  # Required header
                for item_path in concat_list_items:
                    # Escape path for safety in the concat file
                    f.write(f"file {utils.escape_path_for_concat(item_path)}\n")
            print(f"  Concat list file created: {concat_list_path}")
        except IOError as e:
            raise CommandGenerationError(f"Failed to create concat list file for video+ads: {e}")

        return preprocessing_commands, concat_list_path, temp_files_generated, total_ad_duration_sum

    def _generate_banner_preprocessing_commands(self,
                                                banner_file: str, banner_timecodes: List[str],
                                                original_banner_duration: float,
                                                target_params: TargetParams, final_duration_estimate: float,
                                                is_concat_mode: bool, sorted_embed_ads: List[AdInsertionInfo]
                                                ) -> Tuple[List[str], str, List[str], str]:
        """ Generates commands to create banner segments, black screen gaps, the concat list for them, and the command to concatenate them into a single banner track. """
        print("--- Phase 2.1: Generating Segment Preprocessing Commands (Banner) ---")
        preprocessing_cmds, temp_files, concat_list_items = [], [], []
        segment_counter = 0
        last_banner_track_time = 0.0  # Tracks the end time of the last placed element (gap or banner)

        # --- Preprocess the Banner File Once ---
        # Determine the target dimensions for the banner track video
        if not target_params.width or not target_params.height:
            raise CommandGenerationError("Target width/height missing, cannot determine banner dimensions.")

        banner_scaled_width = target_params.width
        # Calculate height based on aspect ratio (using helper function call)
        banner_params: Optional[StreamParams] = self.get_essential_stream_params(banner_file)
        banner_scaled_height = target_params.height // 10  # Default fallback
        if banner_params and banner_params.width and banner_params.height:
            orig_w, orig_h = banner_params.width, banner_params.height
            banner_scaled_height = max(1, int(orig_h * (
                    banner_scaled_width / orig_w))) if orig_w > 0 else banner_scaled_height
        print(f"  Target banner track dimensions: {banner_scaled_width}x{banner_scaled_height}")

        # Create a single, preprocessed banner segment video
        temp_banner_segment_path = utils.generate_temp_filename("banner_segment_uniq", segment_counter)
        banner_segment_cmd = self._create_segment_command(
            input_path=banner_file,
            output_path=temp_banner_segment_path,
            target_params=target_params,
            duration=original_banner_duration,  # Use full duration for the segment source
            output_audio=False,  # Banners shouldn't have audio track
            force_fps=True,  # Ensure target FPS
            is_banner_segment=True  # Use banner-specific scaling/pix_fmt logic
        )
        preprocessing_cmds.append(banner_segment_cmd)
        temp_files.append(temp_banner_segment_path)
        segment_counter += 1

        # --- Calculate Adjusted Banner Timings ---
        # Convert original timecodes to seconds and adjust for ad insertions
        adjusted_banner_times_sec = []
        valid_banner_timecodes_sec = sorted(
            filter(None, [utils.timecode_to_seconds(tc) for tc in banner_timecodes]))
        for banner_original_sec in valid_banner_timecodes_sec:
            # Calculate start time in the potentially longer timeline (with ads)
            adjusted_start = self._calculate_adjusted_times(banner_original_sec, is_concat_mode, sorted_embed_ads)
            # Calculate end time, ensuring it doesn't exceed the estimated final duration
            adjusted_end = min(adjusted_start + original_banner_duration, final_duration_estimate)

            # Add the interval only if it's valid and has positive duration
            if adjusted_end > adjusted_start + 0.001 and adjusted_start < final_duration_estimate:
                adjusted_banner_times_sec.append((adjusted_start, adjusted_end))

        # Sort the adjusted time intervals
        adjusted_banner_times_sec.sort(key=lambda x: x[0])

        # --- Generate Gaps and Banner Entries for Concat List ---
        max_banner_track_duration = 0.0  # Track the maximum extent of the banner track
        for i, (start_time, end_time) in enumerate(adjusted_banner_times_sec):
            # Calculate duration of the gap needed before this banner instance
            gap_duration = start_time - last_banner_track_time
            if gap_duration > 0.001:
                # Create a command to generate a gap video segment
                temp_gap_path = utils.generate_temp_filename("banner_gap", segment_counter)
                # Use lavfi color source for the gap
                # Ensure target_params.fps_str is not None
                if target_params.fps_str is None:
                    raise CommandGenerationError("Target FPS is required for generating banner gaps.")
                gap_cmd_parts = [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i",
                    f"color=c={self.banner_gap_color}:s={banner_scaled_width}x{banner_scaled_height}:d={gap_duration:.6f}:r={target_params.fps_str}",
                    # Ensure the gap also has the correct banner pixel format
                    "-vf", f"format=pix_fmts={self.banner_track_pix_fmt}",
                    # Encode gap efficiently (e.g., using lossless or near-lossless settings)
                    "-c:v", self.video_codec, "-preset", self.video_preset, "-crf", "0",
                    # Use CRF 0 for lossless (if supported) or a low value like 10
                    "-an",  # No audio
                    "-video_track_timescale", target_params.video_timescale,
                    "-t", f"{gap_duration:.6f}",  # Explicit duration
                    f'"{temp_gap_path}"'
                ]
                preprocessing_cmds.append(" ".join(gap_cmd_parts))
                temp_files.append(temp_gap_path)
                # Add the gap file to the concat list
                concat_list_items.append(temp_gap_path)
                segment_counter += 1

            # Add the *preprocessed* banner segment file to the concat list
            # Specify the exact duration this instance of the banner should play for
            current_banner_duration = end_time - start_time
            concat_list_items.append(f"{temp_banner_segment_path}\nduration {current_banner_duration:.6f}")

            # Update the time marker to the end of this banner instance
            last_banner_track_time = end_time
            # Update the maximum duration reached by the banner track
            max_banner_track_duration = max(max_banner_track_duration, end_time)

        if not concat_list_items:
            raise CommandGenerationError("No segments were generated for the banner track.")

        # --- Phase 2.2: Create Concat List File for Banner Track ---
        print(f"--- Phase 2.2: Creating Concat List File for Banner Track ---")
        banner_concat_list_filename = f"concat_list_banner_{int(time.time())}.txt"
        banner_concat_list_path = os.path.join(utils.generate_temp_filename("", 0, "").rsplit(os.sep, 1)[0],
                                               banner_concat_list_filename)
        temp_files.append(banner_concat_list_path)

        try:
            with open(banner_concat_list_path, 'w', encoding='utf-8') as f:
                f.write("ffconcat version 1.0\n\n")
                for item in concat_list_items:
                    # Handle items with duration directives
                    if '\n' in item:
                        path_part, duration_part = item.split('\n', 1)
                        f.write(f"file {utils.escape_path_for_concat(path_part)}\n")
                        f.write(f"{duration_part}\n")  # Write the duration directive on the next line
                    else:
                        # Item is just a file path (gap segment)
                        f.write(f"file {utils.escape_path_for_concat(item)}\n")
            print(f"  Banner concat list file created: {banner_concat_list_path}")
        except IOError as e:
            raise CommandGenerationError(f"Failed to create banner concat list file: {e}")

        # --- Phase 2.3: Generate Command to Concatenate Banner Track ---
        print(f"--- Phase 2.3: Generating Command for Banner Track Concatenation ---")
        # Generate the final output path for the single, concatenated banner track video
        concatenated_banner_path = utils.generate_temp_filename("banner_track_final", 0)
        temp_files.append(concatenated_banner_path)  # Ensure this final track is cleaned up later

        # Command to concatenate the segments listed in the banner concat file
        concat_cmd_parts = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",  # Use concat demuxer, allow unsafe paths
            "-i", f'"{banner_concat_list_path}"',  # Input is the list file
            "-c", "copy",  # Crucially, copy streams without re-encoding
            # Add -t to ensure the concatenated banner track has the correct maximum duration
            # This prevents the track from being longer than the last banner's end time if there are timing nuances.
            "-t", f"{max_banner_track_duration:.6f}",
            f'"{concatenated_banner_path}"'  # Output is the single banner track file
        ]
        banner_concat_cmd = " ".join(concat_cmd_parts)
        print(f"  Banner concatenation command created. Output: {concatenated_banner_path}")

        # Add the concatenation command itself to the list of preprocessing commands to run
        preprocessing_cmds.append(banner_concat_cmd)

        return preprocessing_cmds, banner_concat_list_path, temp_files, concatenated_banner_path

    def _build_moving_logo_filter(self, current_video_input_label: str, moving_input_stream_label: str,
                                  target_params: TargetParams, final_duration_estimate: float) -> Tuple[
        List[str], Optional[str]]:
        """Builds the filtergraph string parts for the moving logo overlay."""
        filter_parts = []

        print(f"  Setting up filter for moving ad (Input: {moving_input_stream_label})...")
        # Extract input index for unique labeling
        moving_input_index = moving_input_stream_label.strip('[]').split(':')[0]
        # Define intermediate stream labels
        scaled_moving_stream = f"[moving_scaled_{moving_input_index}]"
        transparent_moving_stream = f"[moving_alpha_{moving_input_index}]"
        overlay_output_label_moving = f"[v_moving_out_{moving_input_index}]"  # Final output of this filter stage

        # --- Scaling ---
        main_h = target_params.height if target_params.height else 720  # Use target height or fallback
        # Calculate target logo height based on relative setting
        logo_target_h = max(1, int(main_h * self.moving_logo_relative_height))
        sar_value = target_params.sar.replace(':', '/')  # Format for setsar
        # Scale logo: maintain aspect ratio (-1 width), set target height, use good scaling algorithm
        moving_scale_filter = f"scale=-1:{logo_target_h}:flags=bicubic"
        # Chain scaling and SAR setting
        filter_parts.append(
            f"{moving_input_stream_label}{moving_scale_filter},setsar=sar={sar_value}{scaled_moving_stream}")

        # --- Transparency ---
        clamped_alpha = max(0.0, min(1.0, self.moving_logo_alpha))  # Ensure alpha is between 0 and 1
        # Ensure input is RGBA for colorchannelmixer, then apply alpha
        alpha_filter = f"format=pix_fmts=rgba,colorchannelmixer=aa={clamped_alpha:.3f}"
        filter_parts.append(f"{scaled_moving_stream}{alpha_filter}{transparent_moving_stream}")

        # --- Movement (Overlay) ---
        # Calculate cycle duration based on total video duration and speed factor
        t_total = max(0.1, final_duration_estimate)  # Avoid division by zero
        # Ensure moving speed is valid
        if not isinstance(self.moving_speed, (int, float)) or self.moving_speed <= 0:
            moving_speed = 1.0  # Default speed if invalid
            print("    Warning: Invalid moving speed, using default 1.0.")
        else:
            moving_speed = self.moving_speed

        cycle_t = t_total / moving_speed  # Time for one full cycle (rect path)
        # Default to static position if cycle time is too short
        x_expr, y_expr = "'0'", "'0'"  # Top-left corner default

        # Define rectangular path animation only if cycle duration is meaningful
        if cycle_t > 0.5:  # Arbitrary threshold for meaningful animation
            # Define time points within one cycle (0, T/4, T/2, 3T/4, T)
            t1, t2, t3 = cycle_t / 4, cycle_t / 2, 3 * cycle_t / 4
            # Duration of each segment (T/4)
            seg_dur = max(cycle_t / 4, 1e-6)  # Avoid division by zero
            # Max x and y coordinates (main dimensions minus overlay dimensions)
            mx, my = f"(main_w-overlay_w)", f"(main_h-overlay_h)"
            # Time variable within the current cycle: mod(t, cycle_t)
            tv = f"mod(t,{cycle_t:.6f})"

            # X-coordinate expressions for each segment
            # 0 -> t1: Move right (0 to mx)
            x1 = f"{mx}*({tv}/{seg_dur:.6f})"
            # t1 -> t2: Stay at right edge (mx)
            x2 = f"{mx}"
            # t2 -> t3: Move left (mx to 0)
            x3 = f"{mx}*(1-(({tv}-{t2:.6f})/{seg_dur:.6f}))"
            # t3 -> cycle_t: Stay at left edge (0)
            x4 = "0"

            # Y-coordinate expressions for each segment
            # 0 -> t1: Stay at top (0)
            y1 = "0"
            # t1 -> t2: Move down (0 to my)
            y2 = f"{my}*(({tv}-{t1:.6f})/{seg_dur:.6f})"
            # t2 -> t3: Stay at bottom (my)
            y3 = f"{my}"
            # t3 -> cycle_t: Move up (my to 0)
            y4 = f"{my}*(1-(({tv}-{t3:.6f})/{seg_dur:.6f}))"

            # Combine expressions using nested if conditions
            x_expr = f"'if(lt({tv},{t1:.6f}),{x1},if(lt({tv},{t2:.6f}),{x2},if(lt({tv},{t3:.6f}),{x3},{x4})))'"
            y_expr = f"'if(lt({tv},{t1:.6f}),{y1},if(lt({tv},{t2:.6f}),{y2},if(lt({tv},{t3:.6f}),{y3},{y4})))'"
            print(f"    Moving ad animation: Rectangular path ({cycle_t:.2f}s cycle).")
        else:
            print(
                f"    Warning: Animation cycle duration ({cycle_t:.3f}s) is too short, logo will be static at top-left.")

        # Build the overlay filter
        # Inputs: [current video stream], [transparent logo stream]
        # Outputs: [overlay_output_label_moving]
        # shortest=0: Ensure overlay lasts the duration of the main video stream
        overlay_filter = (f"{current_video_input_label}{transparent_moving_stream}"
                          f"overlay=x={x_expr}:y={y_expr}:shortest=0"
                          f"{overlay_output_label_moving}")
        filter_parts.append(overlay_filter)

        # The output label of this stage becomes the input for the next
        next_video_output_label = overlay_output_label_moving
        print(f"    Overlay filter for moving ad added. Output: {next_video_output_label}")
        return filter_parts, next_video_output_label

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
        # Check if we have a valid banner track, timecodes, and duration
        if concatenated_banner_track_idx is not None and banner_timecodes and original_banner_duration is not None:
            try:
                print(
                    f"  Setting up overlay filter for banner track (Input: [{concatenated_banner_track_idx}:v], using 'between')...")
                banner_track_input_label = f"[{concatenated_banner_track_idx}:v]"  # Input label for banner stream
                # Define output label for this filter stage
                overlay_output_label_banner = f"[v_banner_out_{concatenated_banner_track_idx}]"

                # Build the 'enable' expression using 'between' for each adjusted time interval
                enable_parts = []
                # Recalculate adjusted times here to build the 'enable' condition
                valid_banner_timecodes_sec = sorted(
                    filter(None, [utils.timecode_to_seconds(tc) for tc in banner_timecodes]))
                for banner_original_sec in valid_banner_timecodes_sec:
                    adjusted_start_time = self._calculate_adjusted_times(banner_original_sec, is_concat_mode,
                                                                         sorted_embed_ads)
                    end_time = min(adjusted_start_time + original_banner_duration, final_duration_estimate)
                    # Add 'between' clause if interval is valid
                    if end_time > adjusted_start_time + 0.001 and adjusted_start_time < final_duration_estimate:
                        enable_parts.append(f"between(t,{adjusted_start_time:.3f},{end_time:.3f})")

                if enable_parts:
                    # Join 'between' clauses with '+' (logical OR for enable)
                    enable_expression = "+".join(enable_parts)
                    # Define overlay position (bottom-left)
                    overlay_y_pos, overlay_x_pos = "main_h-overlay_h", "0"
                    # Build the overlay filter string
                    banner_overlay_filter = (
                        f"{last_filter_video_label}{banner_track_input_label}"  # Inputs: current video, banner track
                        f"overlay=x={overlay_x_pos}:y={overlay_y_pos}:enable='{enable_expression}':shortest=0"  # Overlay params
                        f"{overlay_output_label_banner}"  # Output label
                    )
                    all_filter_parts.append(banner_overlay_filter)
                    # Update the label for the *next* filter stage
                    last_filter_video_label = overlay_output_label_banner
                    # Update the final label to be mapped for video output
                    final_video_output_map_label = last_filter_video_label.strip('[]')
                    print(
                        f"    Overlay filter for banner track (using 'between') added. Output: {last_filter_video_label}")
                else:
                    print(
                        "    Warning: Could not generate valid 'enable' time ranges for banner overlay. Filter not added.")
            except Exception as e:
                print(f"Warning: Error building banner overlay filter: {e}. Skipping banner.")

        # --- Moving Logo Filter ---
        # Check if we have a valid moving logo file and input index
        if moving_file and moving_input_idx is not None:
            try:
                moving_input_stream_label = f"[{moving_input_idx}:v]"  # Input label for logo stream
                # Call helper to build logo filter parts, using the *output* of the previous stage as input
                logo_filters, last_video_label_after_logo = self._build_moving_logo_filter(
                    last_filter_video_label,  # Input is the output of banner overlay (or base video)
                    moving_input_stream_label,  # Input is the logo file stream
                    target_params,
                    final_duration_estimate)

                # If logo filters were successfully generated
                if last_video_label_after_logo:
                    all_filter_parts.extend(logo_filters)
                    # Update the label for the next stage (if any)
                    last_filter_video_label = last_video_label_after_logo
                    # Update the final label to be mapped for video output
                    final_video_output_map_label = last_filter_video_label.strip('[]')
            except Exception as e:
                print(f"Warning: Error building moving logo filter: {e}. Skipping logo.")

        # --- Final Assembly ---
        if not all_filter_parts:
            print("--- No filters applied ---")
            # Return None for filter string, use base specifiers for mapping
            return None, base_video_specifier, base_audio_specifier

        # Join all filter parts with semicolons
        filter_complex_str = ";".join(all_filter_parts)
        print(
            f"--- Final filter_complex generated ({len(all_filter_parts)} stages). Video output: [{final_video_output_map_label}] ---")
        # DEBUG: Print the generated filter string
        # print(f"DEBUG filter_complex:\n{filter_complex_str}\n")
        # Return the full filter string and the final output labels for video and audio
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

        if is_concat_mode:
            if not concat_list_path or not os.path.exists(concat_list_path):
                raise CommandGenerationError("Concat list file (main video + ads) not found or provided.")
            primary_input_options = ["-f", "concat", "-safe", "0"]
            primary_input_path = concat_list_path
            print(f"Mode: Concatenation. Input 0 (Video/Audio): {os.path.basename(concat_list_path)}")

            # Use original input file for metadata/subtitles if needed
            original_info = self.get_stream_info(input_file)
            if any(s.get('codec_type') == 'subtitle' for s in original_info.get("streams", [])):
                print(f"  Input 1 (Subs/Metadata Source): {os.path.basename(input_file)}")
                input_definitions.append(([], input_file))
                subtitle_input_specifier = "1:s?"
                metadata_input_index = 1
        else:
            primary_input_path = input_file
            metadata_input_index = 0
            original_info = self.get_stream_info(input_file)
            if any(s.get('codec_type') == 'subtitle' for s in original_info.get("streams", [])):
                subtitle_input_specifier = "0:s?"
            print(f"Mode: Direct Conversion. Input 0 (Video/Audio/Subs/Metadata): {os.path.basename(input_file)}")

        # Add the primary video/audio input (either file or concat list)
        input_definitions.insert(0, (primary_input_options if is_concat_mode else [], primary_input_path))

        current_input_index = len(input_definitions)
        banner_track_input_idx = None
        if concatenated_banner_track_path:
            print(
                f"  Input {current_input_index} (Concatenated Banner Track): {os.path.basename(concatenated_banner_track_path)}")
            input_definitions.append(([], concatenated_banner_track_path))
            banner_track_input_idx = current_input_index
            current_input_index += 1

        moving_input_idx = None
        if moving_file and os.path.exists(moving_file):
            moving_options = ["-loop", "1"]
            # If moving file is an image, set its frame rate to match target
            if self.get_media_duration(moving_file) is None and target_params.fps_str:
                moving_options.extend(["-r", target_params.fps_str])
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

            # Video FPS (optional override)
            if self.video_fps:
                print(f"    Video: Target FPS={self.video_fps}")
                main_cmd_parts.extend(['-r', self.video_fps])  # Note: -r applies to output stream

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

    def _generate_main_ffmpeg_command(self,
                                      input_file: str, output_file: str, encoding_params_str: str,
                                      target_params: TargetParams,
                                      main_video_duration: float, track_data_dict: Dict[str, Dict[str, str]],
                                      # Input from GUI
                                      concatenated_banner_track_path: Optional[str],
                                      original_banner_duration: Optional[float],
                                      banner_timecodes: Optional[List[str]],
                                      moving_file: Optional[str],
                                      is_concat_mode: bool, concat_list_path: Optional[str],
                                      sorted_embed_ads: List[AdInsertionInfo], total_embed_duration_added: float
                                      ) -> Tuple[str, List[str]]:
        """ Generates the main FFmpeg command string using preprocessed inputs and overlays. """
        print("--- Phase 3: Generating Main Conversion Command ---")
        main_cmd_parts = ["ffmpeg", "-y", '-hide_banner']  # Start building command list
        # Add HW acceleration flag if specified
        if self.hwaccel and self.hwaccel != "none":
            main_cmd_parts.extend(['-hwaccel', self.hwaccel])

        # Estimate final duration
        final_duration_estimate = main_video_duration + total_embed_duration_added
        print(f"Estimated final duration: {final_duration_estimate:.3f}s")

        # Step 3.1: Define Inputs
        input_definitions, base_video_specifier, base_audio_specifier, \
            subtitle_input_specifier, banner_track_input_idx, moving_input_idx, \
            metadata_input_index = self._define_main_command_inputs(
            input_file, target_params, concatenated_banner_track_path, moving_file,
            is_concat_mode, concat_list_path
        )
        # Add input definitions to command list
        for options, path in input_definitions:
            main_cmd_parts.extend(options)
            main_cmd_parts.extend(["-i", f'"{path}"'])

        # Step 3.2: Build Filter Complex
        filter_complex_str, final_video_map_label, final_audio_map_label = self._build_filter_complex(
            base_video_specifier.rstrip('?'),  # Provide base specifier without '?' for filter input label
            base_audio_specifier.rstrip('?') if base_audio_specifier else None,
            target_params, final_duration_estimate, is_concat_mode,
            sorted_embed_ads,
            banner_track_input_idx,
            original_banner_duration,
            banner_timecodes,
            moving_file,
            moving_input_idx
        )

        # Step 3.3: Apply Filters and Mapping (modifies main_cmd_parts in place)
        map_commands = self._apply_filters_and_mapping(
            main_cmd_parts, filter_complex_str, final_video_map_label, final_audio_map_label,
            base_video_specifier, base_audio_specifier, subtitle_input_specifier, target_params
        )

        # Step 3.4: Handle Metadata
        # Convert the input Dict[str, Dict[str, str]] to Dict[str, TrackMetadataEdits]
        track_data_edits: Dict[str, TrackMetadataEdits] = {
            track_id: TrackMetadataEdits(title=edits.get('title'), language=edits.get('language'))
            for track_id, edits in track_data_dict.items()
        }
        temp_files_for_main = self._handle_metadata(
            main_cmd_parts, track_data_edits, metadata_input_index, input_definitions, map_commands,
            base_video_specifier, filter_complex_str
        )

        # Step 3.5: Build Encoding Parameters (modifies main_cmd_parts in place)
        self._build_encoding_parameters(main_cmd_parts, encoding_params_str, map_commands)

        # Step 3.6: Finalize Command (Duration, Output, Flags - modifies main_cmd_parts in place)
        self._finalize_main_command(main_cmd_parts, final_duration_estimate, output_file)

        # Join all parts into the final command string
        final_main_cmd = " ".join(main_cmd_parts)
        return final_main_cmd, temp_files_for_main

    def generate_ffmpeg_commands(self,
                                 input_file: str, output_file: str, encoding_params_str: str,
                                 track_data: Dict[str, Dict[str, str]],  # Still Dict[Dict] from GUI
                                 embed_ads: List[Dict],  # Still List[Dict] from GUI
                                 banner_file: Optional[str], banner_timecodes: Optional[List[str]],
                                 moving_file: Optional[str]):
        """
        Generates all necessary FFmpeg commands for the conversion process.

        Handles preprocessing for ad concatenation, banner track generation,
        and the final conversion command with overlays.

        Args:
            input_file: Path to the main input video file.
            output_file: Path for the final output video file.
            encoding_params_str: Manual FFmpeg encoding parameters (overrides individual settings).
            track_data: Dictionary with metadata edits for tracks (from GUI).
            embed_ads: List of dictionaries for embedded ad insertions (from GUI).
            banner_file: Path to the banner media file (video or image).
            banner_timecodes: List of timecodes (MM:SS) for banner display.
            moving_file: Path to the moving logo image file.

        Returns:
            A tuple containing:
            - List[str]: Preprocessing FFmpeg command strings.
            - str: The main FFmpeg conversion command string.
            - List[str]: A list of temporary file paths created during generation.

        Raises:
            CommandGenerationError: If command generation fails due to invalid inputs or logic errors.
            FfprobeError: If ffprobe fails during analysis.
        """
        all_preprocessing_commands = []
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
            # _validate_and_prepare_inputs returns dataclasses StreamParams and List[AdInsertionInfo]
            valid_params, valid_duration, sorted_embed_ads_info, \
                valid_banner_file, valid_banner_timecodes, valid_moving_file, \
                original_banner_duration = self._validate_and_prepare_inputs(
                input_file, output_file, main_video_params, main_video_duration,
                embed_ads, banner_file, banner_timecodes, moving_file)
            # Update local variables with validated/processed data
            banner_file = valid_banner_file
            banner_timecodes = valid_banner_timecodes
            moving_file = valid_moving_file
            main_video_params = valid_params  # Now a StreamParams object
            main_video_duration = valid_duration
            # sorted_embed_ads_info is now List[AdInsertionInfo]
        except CommandGenerationError as e:
            print(f"Input validation failed: {e}")
            raise

        print("--- Determining Target Encoding Parameters ---")
        target_params: TargetParams = self._determine_target_parameters(main_video_params)

        is_concat_mode = bool(sorted_embed_ads_info)
        if is_concat_mode:
            total_embed_duration_added = sum(ad.duration for ad in sorted_embed_ads_info)  # Use attribute access
        final_duration_estimate = main_video_duration + total_embed_duration_added
        print(f"Estimated final duration (with ads, if any): {final_duration_estimate:.3f}s")

        # Generate preprocessing for concatenation if needed
        if is_concat_mode:
            try:
                prep_cmds_main, concat_list_path_main, prep_temp_files_main, _ = \
                    self._generate_preprocessing_for_concat(
                        input_file, sorted_embed_ads_info, target_params, main_video_duration
                    )
                all_preprocessing_commands.extend(prep_cmds_main)
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
                prep_cmds_banner, _, prep_temp_files_banner, concatenated_banner_path = \
                    self._generate_banner_preprocessing_commands(
                        banner_file, banner_timecodes, original_banner_duration, target_params,
                        final_duration_estimate,
                        is_concat_mode, sorted_embed_ads_info
                    )
                all_preprocessing_commands.extend(prep_cmds_banner)
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

        print("--- Generating Main FFmpeg Command ---")
        try:
            main_command, main_temp_files = self._generate_main_ffmpeg_command(
                input_file=input_file,
                output_file=output_file,
                encoding_params_str=encoding_params_str,
                target_params=target_params,  # Pass TargetParams
                main_video_duration=main_video_duration,
                track_data_dict=track_data,  # Pass original Dict[Dict]
                concatenated_banner_track_path=concatenated_banner_track_path,
                original_banner_duration=original_banner_duration,
                banner_timecodes=banner_timecodes,
                moving_file=moving_file,
                is_concat_mode=is_concat_mode,
                concat_list_path=concat_list_path_main,
                sorted_embed_ads=sorted_embed_ads_info,  # Pass List[AdInsertionInfo]
                total_embed_duration_added=total_embed_duration_added
            )
            all_temp_files.extend(main_temp_files)
        except CommandGenerationError as e:
            utils.cleanup_temp_files(all_temp_files)
            print(f"Error generating main command: {e}")
            raise e
        except Exception as e:
            utils.cleanup_temp_files(all_temp_files)
            print(f"Unexpected error generating main command: {type(e).__name__} - {e}")
            raise CommandGenerationError(f"Failed to generate main command: {e}") from e

        unique_temp_files = sorted(list(set(all_temp_files)))
        return all_preprocessing_commands, main_command, unique_temp_files

    @staticmethod
    def run_ffmpeg_command(cmd: str, step_name: str):
        """Executes a single FFmpeg command using subprocess.Popen() and handles errors."""
        print(f"\n--- Running Step: {step_name} ---")
        # Log command, truncated if too long
        if len(cmd) > 1000:
            print(f"Command: {cmd[:500]}... (total {len(cmd)} chars)")
        else:
            print(f"Command: {cmd}")

        try:
            startupinfo = None
            if os.name == 'nt':  # Hide console window on Windows
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE

            # Use Popen for real-time stderr processing
            process = subprocess.Popen(cmd, shell=True,  # Shell=True needed due to quotes and complex filters
                                       stdout=subprocess.PIPE,  # Capture stdout (though usually empty)
                                       stderr=subprocess.PIPE,  # Capture stderr for progress/errors
                                       text=True,  # Decode as text
                                       encoding='utf-8', errors='replace',  # Specify encoding
                                       startupinfo=startupinfo)

            stderr_output = ""
            progress_line = ""  # Store the last progress line (frame=...)

            # Read stderr line by line
            while True:
                line = process.stderr.readline()
                if not line:  # End of output
                    break
                stderr_output += line
                stripped = line.strip()
                # Look for standard FFmpeg progress indicators
                if stripped.startswith(('frame=', 'size=', 'time=', 'bitrate=', 'speed=')):
                    progress_line = stripped
                    # Print progress line, using carriage return to overwrite previous one
                    print(f"  {progress_line}", end='\r')
                elif progress_line:
                    # If we were printing progress, print a newline before the next non-progress line
                    print()  # Move to next line
                    print(f"  [stderr] {stripped}")
                    progress_line = ""  # Reset progress line state
                else:
                    # Print regular stderr lines
                    print(f"  [stderr] {stripped}")

            # Ensure cursor is on a new line after progress reporting finishes
            if progress_line:
                print()

            process.stdout.close()  # Close stdout pipe
            return_code = process.wait()  # Wait for process to finish

            # Check return code for errors
            if return_code != 0:
                raise ConversionError(
                    f"Error during '{step_name}' (exit code {return_code}).\n"
                    f"Command:\n{cmd}\n"
                    f"Stderr (last 2000 chars):\n{stderr_output[-2000:]}")

            print(f"--- {step_name}: Successfully completed ---")
            return True  # Indicate success

        except FileNotFoundError:
            # Specific error if ffmpeg executable isn't found
            raise FfmpegError("FFmpeg command not found. Ensure FFmpeg is installed and in the system PATH.") from None
        except ConversionError as e:
            # Re-raise specific conversion errors
            raise e
        except Exception as e:
            # Catch other potential errors during execution
            raise FfmpegError(f"Unexpected error running '{step_name}': {type(e).__name__} - {e}") from e
