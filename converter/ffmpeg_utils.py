# converter/ffmpeg_utils.py
import json
import math
import os
import shlex
import subprocess
import tempfile
import time
from typing import List, Tuple, Dict, Any, Optional

from .exceptions import FfprobeError, CommandGenerationError, ConversionError, FfmpegError

# --- Constants ---
_TEMP_VIDEO_CODEC = "h264_nvenc"  # Or libx264 for software encoding
_TEMP_VIDEO_PRESET = "fast"
_TEMP_VIDEO_CRF = "18" # Quality for temp files (lower CRF = higher quality)
_TEMP_AUDIO_CODEC = "aac"
_TEMP_AUDIO_BITRATE = "192k"
_MOVING_SPEED = 2
_MOVING_LOGO_RELATIVE_HEIGHT = 1 / 10
_MOVING_LOGO_ALPHA = 0.7

# Constants previously used for banner track, kept for reference or future use if needed
# _BANNER_TRACK_CODEC = "libx264"
# _BANNER_TRACK_PIX_FMT = "yuva420p"
# _BANNER_TRACK_PRESET = "ultrafast"
# _BANNER_TRACK_CRF = "20"
# _BANNER_GAP_COLOR = "black@0.0"


# --- FFprobe Utilities ---

def run_ffprobe(command):
    """Runs an ffprobe command and returns the parsed JSON output."""
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
            "ffprobe не найден. Убедитесь, что FFmpeg (включая ffprobe) установлен и добавлен в системный PATH.")
    except subprocess.CalledProcessError as e:
        stderr_tail = e.stderr[-1000:] if e.stderr else "N/A"
        raise FfprobeError(f"Ошибка выполнения ffprobe: {e}\nКоманда: {' '.join(command)}\nВывод stderr (конец): {stderr_tail}")
    except json.JSONDecodeError as e:
        stdout_content = "N/A"
        try:
            stdout_content = e.doc
        except AttributeError:
            pass
        raise FfprobeError(f"Ошибка декодирования вывода ffprobe: {e}\nКоманда: {' '.join(command)}\nНачало вывода stdout: {stdout_content[:500]}")
    except Exception as e:
        raise FfprobeError(f"Неожиданная ошибка при выполнении ffprobe: {e}\nКоманда: {' '.join(command)}")


def get_media_duration(file_path):
    """Gets media duration using ffprobe. Returns None for images/errors/very short clips."""
    if not file_path or not os.path.exists(file_path):
        return None
    duration = None
    try:
        # Try format duration first
        command_fmt = ["ffprobe", "-v", "quiet", "-i", file_path,
                       "-show_entries", "format=duration",
                       "-print_format", "json"]
        output_fmt = run_ffprobe(command_fmt)
        duration_str_fmt = output_fmt.get("format", {}).get("duration")
        if duration_str_fmt and duration_str_fmt != "N/A":
            try:
                duration = float(duration_str_fmt)
            except (ValueError, TypeError):
                pass # Ignore conversion errors here, try stream

        # If format duration failed or is zero, try first video stream duration
        if duration is None or duration <= 0:
            command_stream = ["ffprobe", "-v", "quiet", "-i", file_path,
                              "-select_streams", "v:0",
                              "-show_entries", "stream=duration",
                              "-print_format", "json"]
            try:
                output_stream = run_ffprobe(command_stream)
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
                 pass

        # Return duration only if it's valid and reasonably long
        if duration and duration > 0.01: # Use small epsilon
             return duration
        else:
             return None

    except FfprobeError as e:
        # print(f"Ошибка ffprobe при получении длительности для {file_path}: {e}") # Less verbose
        return None
    except Exception as e:
        print(f"Неожиданная ошибка в get_media_duration для {file_path}: {e}")
        return None


def get_stream_info(file_path):
    """Gets info about all streams using ffprobe."""
    if not file_path or not os.path.exists(file_path): return {}
    command = ["ffprobe", "-v", "quiet", "-i", file_path,
               "-show_streams", "-show_format", "-print_format", "json"]
    try:
        return run_ffprobe(command)
    except FfprobeError as e:
        # print(f"Ошибка ffprobe при получении информации о потоках для {file_path}: {e}") # Less verbose
        return {}


def get_essential_stream_params(file_path):
    """Gets key video and audio parameters needed for compatibility checks using ffprobe."""
    params = {
        'width': None, 'height': None, 'pix_fmt': None, 'sar': '1:1', 'par': None, 'time_base_v': None, 'fps_str': None,
        'sample_rate': None, 'channel_layout': None, 'sample_fmt': None, 'time_base_a': None, 'has_audio': False
    }
    if not file_path or not os.path.exists(file_path): return None

    # --- Probe Video Stream (v:0) ---
    has_video_stream = False
    try:
        cmd_video = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,pix_fmt,sample_aspect_ratio,display_aspect_ratio,r_frame_rate,time_base,codec_name",
            "-of", "json", file_path
        ]
        data_v = run_ffprobe(cmd_video)
        if data_v.get("streams"):
            stream_v = data_v["streams"][0]
            params['width'] = stream_v.get('width')
            params['height'] = stream_v.get('height')
            params['pix_fmt'] = stream_v.get('pix_fmt')
            sar_str = stream_v.get('sample_aspect_ratio', '1:1')
            params['sar'] = sar_str if ':' in sar_str and len(sar_str.split(':')) == 2 else '1:1'
            params['time_base_v'] = stream_v.get('time_base')
            params['fps_str'] = stream_v.get('r_frame_rate')
            if all([params['width'], params['height'], params['fps_str'], params['time_base_v']]):
                 has_video_stream = True
                 if not params['pix_fmt']: params['pix_fmt'] = 'yuv420p'
            # else: print warning removed for brevity

    except FfprobeError:
         pass # Expected if no video stream
    except Exception as e:
         print(f"Неожиданная ошибка при зондировании видеопотока {file_path}: {e}")

    # --- Handle Images or Files Without Video Stream ---
    if not has_video_stream:
        is_image = False
        try:
             cmd_format = ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "json", file_path]
             data_fmt = run_ffprobe(cmd_format)
             format_name = data_fmt.get("format", {}).get("format_name", "")
             image_formats = ['image2', 'png_pipe', 'mjpeg', 'webp_pipe', 'gif', 'tiff_pipe', 'bmp_pipe', 'jpeg_pipe', 'ppm_pipe', 'pgm_pipe', 'pbm_pipe']
             if any(fmt in format_name for fmt in image_formats):
                 is_image = True
                 cmd_img_stream = ["ffprobe", "-v", "error", "-select_streams", "0",
                                   "-show_entries", "stream=width,height,pix_fmt,codec_type", "-of", "json", file_path]
                 data_img_s = run_ffprobe(cmd_img_stream)
                 if data_img_s.get("streams"):
                     stream_img = data_img_s["streams"][0]
                     params['width'] = stream_img.get('width')
                     params['height'] = stream_img.get('height')
                     params['pix_fmt'] = stream_img.get('pix_fmt', 'rgb24')
                     params['fps_str'] = '25/1'; params['time_base_v'] = '1/25'; params['sar'] = '1:1'
                     has_video_stream = True
                     print(f"Информация: {os.path.basename(file_path)} распознан как изображение ({format_name}).")
                 # else: print warning removed for brevity
             # else: print warning removed for brevity

        except FfprobeError: pass # Ignore errors probing format/stream for non-video
        except Exception as e: print(f"Неожиданная ошибка при обработке файла без видеопотока {file_path}: {e}")

    if not all([params['width'], params['height'], params['fps_str']]):
         print(f"Критическая ошибка: Не удалось определить основные видеопараметры (ширина/высота/fps) для {file_path}.")
         return None

    # --- Probe Audio Stream (a:0) ---
    try:
        cmd_audio = [ "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channel_layout,sample_fmt,time_base", "-of", "json", file_path ]
        data_a = run_ffprobe(cmd_audio)
        if data_a.get("streams"):
            stream_a = data_a["streams"][0]
            params['sample_rate'] = stream_a.get('sample_rate')
            params['channel_layout'] = stream_a.get('channel_layout')
            params['sample_fmt'] = stream_a.get('sample_fmt')
            params['time_base_a'] = stream_a.get('time_base')
            if all([params['sample_rate'], params['channel_layout'], params['sample_fmt'], params['time_base_a']]):
                params['has_audio'] = True
            else: # Incomplete audio params
                params['has_audio'] = False
                # print warning removed for brevity
    except FfprobeError: pass # Expected if no audio stream
    except Exception as e: print(f"Неожиданная ошибка при зондировании аудиопотока {file_path}: {e}"); params['has_audio'] = False

    # --- Final Cleanup/Defaults ---
    common_pix_fmts = ['yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p', 'yuv444p', 'yuvj444p', 'nv12', 'nv21', 'yuva420p', 'rgba', 'bgra', 'rgb24', 'gray']
    if params['pix_fmt'] not in common_pix_fmts:
        params['pix_fmt'] = 'yuv420p'
    if ':' not in params['sar'] or len(params['sar'].split(':')) != 2:
        params['sar'] = '1:1'
    if params['has_audio']:
         if not params['channel_layout']: params['channel_layout'] = 'stereo'
         if not params['sample_fmt']: params['sample_fmt'] = 'fltp'

    return params


# --- FFmpeg Command Generation Helpers ---

def _generate_temp_filename(prefix, index, extension="mkv"):
    """Generates a unique temporary filename with specified extension."""
    temp_dir = tempfile.gettempdir()
    timestamp = int(time.time() * 1000)
    filename = f"{prefix}_{index}_{timestamp}.{extension}"
    return os.path.join(temp_dir, filename)


def _escape_path_for_concat(path):
    """ Prepares a path for the concat demuxer file list. """
    path = path.replace('\\', '/')
    path = path.replace("'", "'\\''")
    return f"'{path}'"


def _timecode_to_seconds(tc: str) -> Optional[float]:
    """Converts MM:SS or HH:MM:SS timecode string to seconds. Returns None on error."""
    try:
        parts = list(map(float, tc.strip().split(':')))
        seconds = 0.0
        if len(parts) == 2: seconds = parts[0] * 60 + parts[1]
        elif len(parts) == 3: seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else: return None
        return max(0.0, seconds)
    except (ValueError, TypeError, AttributeError): return None


def _validate_and_prepare_inputs(input_file: str, output_file: str, main_video_params: Optional[Dict],
                                 main_video_duration: Optional[float], embed_ads: List[Dict],
                                 banner_file: Optional[str], banner_timecodes: Optional[List[str]],
                                 moving_file: Optional[str]) -> Tuple[Dict, float, List[Dict], Optional[str], Optional[List[str]], Optional[str]]:
    """Validates inputs, prepares basic structures, sorts ads, gets ad durations, validates banner timecodes."""
    if not input_file or not output_file: raise CommandGenerationError("Не указаны входной или выходной файл.")
    if not os.path.exists(input_file): raise CommandGenerationError(f"Входной файл не найден: {input_file}")
    if not main_video_params: raise CommandGenerationError("Отсутствуют необходимые параметры основного видео.")
    if main_video_duration is None or main_video_duration <= 0.01:
        print("Предупреждение: Длительность основного видео не предоставлена или некорректна, пытаемся получить...")
        main_video_duration = get_media_duration(input_file)
        if main_video_duration is None or main_video_duration <= 0.01:
             raise CommandGenerationError(f"Не удалось определить допустимую длительность для основного видео: {input_file}")
        print(f"  Полученная длительность: {main_video_duration:.3f}s")

    # Validate Banner File and Timecodes
    valid_banner_file = None
    valid_banner_timecodes = None
    if banner_file and banner_timecodes:
        if os.path.exists(banner_file):
            parsed_timecodes_sec = [_timecode_to_seconds(tc) for tc in banner_timecodes]
            if any(t is None for t in parsed_timecodes_sec):
                 print("Предупреждение: Обнаружены неверные таймкоды баннера. Баннер будет проигнорирован.")
            else:
                 # Filter timecodes within main duration and store corresponding original strings
                 original_tc_map = {sec: tc for tc, sec in zip(banner_timecodes, parsed_timecodes_sec) if sec is not None and sec < main_video_duration}
                 valid_seconds = sorted(original_tc_map.keys())
                 if not valid_seconds:
                     print("Предупреждение: Все таймкоды баннера некорректны или превышают длительность видео. Баннер будет проигнорирован.")
                 else:
                     if len(valid_seconds) < len([t for t in parsed_timecodes_sec if t is not None]):
                          print(f"Предупреждение: Некоторые таймкоды баннера превышают длительность основного видео и будут проигнорированы.")
                     valid_banner_file = banner_file
                     valid_banner_timecodes = [original_tc_map[sec] for sec in valid_seconds] # Sorted original timecodes
        else:
            print(f"Предупреждение: Файл баннера не найден '{banner_file}', будет проигнорирован.")

    # Validate Moving File
    valid_moving_file = None
    if moving_file:
        if os.path.exists(moving_file): valid_moving_file = moving_file
        else: print(f"Предупреждение: Файл движ. рекламы не найден '{moving_file}', будет проигнорирован.")

    # --- Prepare and Validate Ads ---
    ads_with_time = []
    for ad in embed_ads:
        timecode_str, ad_path = ad.get('timecode'), ad.get('path')
        if not timecode_str or not ad_path: continue # Skip ads missing timecode or path
        time_sec = _timecode_to_seconds(timecode_str)
        if time_sec is None or time_sec >= main_video_duration: continue # Skip invalid or out-of-bounds timecodes
        if not os.path.exists(ad_path): continue # Skip non-existent files
        ads_with_time.append({'data': ad, 'time_sec': time_sec, 'path': ad_path})

    sorted_ads_data = sorted(ads_with_time, key=lambda x: x['time_sec'])

    # Get durations and check compatibility AFTER sorting
    ads_with_info = []
    total_valid_ad_duration = 0.0
    for ad_entry in sorted_ads_data:
        ad_path, ad_timecode = ad_entry['path'], ad_entry['data']['timecode']
        ad_duration = get_media_duration(ad_path)
        if ad_duration is None or ad_duration <= 0.01:
             ad_duration = 5.0 # Default duration for images/errors used as ads
             print(f"Предупреждение: Не удалось определить длительность для рекламы '{os.path.basename(ad_path)}' ({ad_timecode}). Используется {ad_duration:.1f}s.")
        ad_params = get_essential_stream_params(ad_path)
        if ad_params is None or ad_params.get('width') is None: continue # Skip if params invalid or no video stream
        if main_video_params.get('has_audio') and not ad_params.get('has_audio'): continue # Skip if audio mismatch

        ads_with_info.append({'path': ad_path, 'timecode': ad_timecode, 'time_sec': ad_entry['time_sec'], 'duration': ad_duration, 'params': ad_params})
        total_valid_ad_duration += ad_duration

    print(f"Подготовлено {len(ads_with_info)} допустимых рекламных вставок. Общая добавленная длительность: {total_valid_ad_duration:.3f}s")
    return main_video_params, main_video_duration, ads_with_info, valid_banner_file, valid_banner_timecodes, valid_moving_file


def _determine_target_parameters(main_video_params: Dict) -> Dict[str, Any]:
    """Determines target parameters, ensuring critical values are present."""
    target_params = {
        'width': main_video_params.get('width'), 'height': main_video_params.get('height'),
        'sar': main_video_params.get('sar', '1:1'), 'fps_str': main_video_params.get('fps_str'),
        'pix_fmt': main_video_params.get('pix_fmt', 'yuv420p'), 'v_timebase': main_video_params.get('time_base_v'),
        'sample_rate': main_video_params.get('sample_rate'), 'channel_layout': main_video_params.get('channel_layout', 'stereo'),
        'sample_fmt': main_video_params.get('sample_fmt', 'fltp'), 'a_timebase': main_video_params.get('time_base_a'),
        'has_audio': main_video_params.get('has_audio', False), 'video_timescale': "90000" }

    if not all([target_params['width'], target_params['height'], target_params['fps_str'], target_params['pix_fmt'], target_params['v_timebase'], target_params['sar']]):
         missing_v = [k for k, v in target_params.items() if k in ['width', 'height', 'fps_str', 'pix_fmt', 'v_timebase', 'sar'] and not v]
         raise CommandGenerationError(f"Не удалось определить ключевые видео параметры для совместимости: {missing_v}")

    if target_params['v_timebase'] and '/' in target_params['v_timebase']:
        try:
            num, den = map(float, target_params['v_timebase'].split('/'))
            if den != 0 and num !=0 :
                timescale = int(round(1.0 / (num / den)))
                if 1000 < timescale < 1000000: target_params['video_timescale'] = str(timescale)
        except ValueError: pass

    if target_params['has_audio'] and not all([target_params['sample_rate'], target_params['channel_layout'], target_params['sample_fmt'], target_params['a_timebase']]):
        missing_a = [k for k, v in target_params.items() if k in ['sample_rate', 'channel_layout', 'sample_fmt', 'a_timebase'] and not v]
        print(f"Предупреждение: Не удалось определить ключевые аудио параметры ({missing_a}). Аудиодорожка будет игнорироваться.")
        target_params['has_audio'] = False; target_params['sample_rate'] = None; target_params['channel_layout'] = None; target_params['sample_fmt'] = None; target_params['a_timebase'] = None

    print(f"Определены целевые параметры: Res={target_params['width']}x{target_params['height']}, FPS={target_params['fps_str']}, PixFmt={target_params['pix_fmt']}, SAR={target_params['sar']}, Audio={target_params['has_audio']}")
    if target_params['has_audio']: print(f"  Аудио: Rate={target_params['sample_rate']}, Layout={target_params['channel_layout']}, Fmt={target_params['sample_fmt']}")
    return target_params


def _create_segment_command(input_path: str, output_path: str, target_params: Dict,
                            start_time: Optional[float] = None, duration: Optional[float] = None,
                            output_pix_fmt: Optional[str] = None,
                            output_audio: bool = True,
                            banner_scaling: bool = False
                            ) -> str:
    """ Helper function to create a single segment transcoding/generation command. """
    sar_value = target_params['sar'].replace(':', '/') if ':' in target_params['sar'] else '1/1'
    final_pix_fmt = output_pix_fmt if output_pix_fmt else target_params['pix_fmt']
    vf_parts = []

    if banner_scaling:
        print(f"    (Segment Cmd) Применено масштабирование баннера: scale={target_params['width']}:-1")
        vf_parts.extend([
            f"scale={target_params['width']}:-1:flags=bicubic",
            f"setsar=sar={sar_value}",
            f"fps={target_params['fps_str']}",
            f"format=pix_fmts={final_pix_fmt}"
        ])
    else: # Default scaling
        vf_parts.extend([
            f"scale={target_params['width']}:{target_params['height']}:force_original_aspect_ratio=decrease:flags=bicubic",
            f"pad={target_params['width']}:{target_params['height']}:(ow-iw)/2:(oh-ih)/2:color=black",
            f"setsar=sar={sar_value}",
            f"fps={target_params['fps_str']}",
            f"format=pix_fmts={final_pix_fmt}"
        ])
    vf_string = ",".join(vf_parts)

    af_string = None
    create_audio = target_params['has_audio'] and output_audio
    if create_audio:
        af_parts = [f"aresample=resampler=soxr:osr={target_params['sample_rate']}",
                    f"aformat=sample_fmts={target_params['sample_fmt']}:channel_layouts={target_params['channel_layout']}"]
        af_string = ",".join(af_parts)

    cmd_parts = ["ffmpeg", "-y"]
    input_options = []
    # Input seeking/path - handle separately from duration/loop for images
    if start_time is not None and start_time > 0.001:
        input_options.extend(["-ss", f"{start_time:.6f}"])
    input_options.extend(["-i", f'"{input_path}"'])
    cmd_parts.extend(input_options)

    # Duration limit applied after input
    if duration is not None:
         cmd_parts.extend(["-t", f"{duration:.6f}"])

    # Output Options
    cmd_parts.extend(["-avoid_negative_ts", "make_zero"])
    cmd_parts.extend(["-vf", f'"{vf_string}"'])
    if af_string: cmd_parts.extend(["-af", f'"{af_string}"'])
    elif not create_audio: cmd_parts.extend(["-an"])

    # Codec options
    temp_codec_v = _TEMP_VIDEO_CODEC; temp_preset_v = _TEMP_VIDEO_PRESET; temp_crf_v = _TEMP_VIDEO_CRF
    temp_bitrate_a = _TEMP_AUDIO_BITRATE
    # Use different temp settings if needed (e.g., for banner intermediate)
    # if banner_scaling: temp_codec_v = ...; temp_preset_v = ...; etc.

    cmd_parts.extend(["-c:v", temp_codec_v, "-preset", temp_preset_v, "-crf", temp_crf_v, "-b:v", "0"])
    if create_audio: cmd_parts.extend(["-c:a", _TEMP_AUDIO_CODEC, "-b:a", temp_bitrate_a])
    cmd_parts.extend(["-video_track_timescale", target_params['video_timescale']])

    # Mapping
    cmd_parts.extend(["-map", "0:v:0?"])
    if create_audio: cmd_parts.extend(["-map", "0:a:0?"])

    cmd_parts.append(f'"{output_path}"')
    return " ".join([p for p in cmd_parts if p is not None])


def _generate_preprocessing_for_concat(input_file: str, sorted_embed_ads: List[Dict], target_params: Dict,
                                       main_video_duration: float) -> Tuple[List[str], str, List[str], float]:
    """ Generates preprocessing commands for main video segments and ads, and the concat list file. """
    print("--- Фаза 1: Генерация команд предварительной обработки сегментов ---")
    preprocessing_commands, temp_files_generated, concat_list_items = [], [], []
    total_ad_duration_sum, segment_counter, last_original_time = 0.0, 0, 0.0
    unique_ad_files = {}

    print("  Предварительная обработка уникальных рекламных файлов...")
    for ad_data in sorted_embed_ads:
        ad_path = ad_data['path']
        if ad_path not in unique_ad_files:
             temp_ad_path = _generate_temp_filename("ad_segment_uniq", segment_counter)
             cmd = _create_segment_command(ad_path, temp_ad_path, target_params,
                                           duration=ad_data['duration'],
                                           output_audio=target_params['has_audio'])
             preprocessing_commands.append(cmd); temp_files_generated.append(temp_ad_path)
             unique_ad_files[ad_path] = {'data': ad_data, 'temp_path': temp_ad_path}
             segment_counter += 1

    print("  Генерация сегментов основного видео и списка concat...")
    for i, embed in enumerate(sorted_embed_ads):
        embed_original_time_sec, embed_ad_path = embed['time_sec'], embed['path']
        ad_info = unique_ad_files.get(embed_ad_path)
        if not ad_info: continue # Should not happen

        preprocessed_ad_path, ad_duration = ad_info['temp_path'], ad_info['data']['duration']
        main_segment_duration = embed_original_time_sec - last_original_time

        if main_segment_duration > 0.001: # Main Video Segment
            temp_main_path = _generate_temp_filename("main_segment", segment_counter)
            cmd = _create_segment_command(input_file, temp_main_path, target_params,
                                          start_time=last_original_time, duration=main_segment_duration,
                                          output_audio=target_params['has_audio'])
            preprocessing_commands.append(cmd); temp_files_generated.append(temp_main_path)
            concat_list_items.append(temp_main_path); segment_counter += 1
        # elif main_segment_duration < -0.001: print warning removed for brevity

        concat_list_items.append(preprocessed_ad_path); total_ad_duration_sum += ad_duration
        last_original_time = embed_original_time_sec

    # Final Main Video Segment
    if main_video_duration - last_original_time > 0.001:
        final_segment_duration = main_video_duration - last_original_time
        temp_main_path = _generate_temp_filename("main_segment", segment_counter)
        cmd = _create_segment_command(input_file, temp_main_path, target_params,
                                      start_time=last_original_time, duration=final_segment_duration,
                                      output_audio=target_params['has_audio'])
        preprocessing_commands.append(cmd); temp_files_generated.append(temp_main_path)
        concat_list_items.append(temp_main_path)

    if not concat_list_items: raise CommandGenerationError("Нет сегментов для объединения.")

    print("--- Фаза 2: Создание файла списка concat для основного видео+рекламы ---")
    concat_list_filename = f"concat_list_main_{int(time.time())}.txt"
    concat_list_path = os.path.join(tempfile.gettempdir(), concat_list_filename)
    temp_files_generated.append(concat_list_path)

    try:
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            f.write("ffconcat version 1.0\n\n")
            for item_path in concat_list_items: f.write(f"file {_escape_path_for_concat(item_path)}\n")
        print(f"  Создан файл списка: {concat_list_path}")
    except IOError as e: raise CommandGenerationError(f"Не удалось создать файл списка concat: {e}")

    return preprocessing_commands, concat_list_path, temp_files_generated, total_ad_duration_sum


# --- Build Moving Logo Filter ---
def _build_moving_logo_filter(current_video_input_label: str, moving_input_stream_label: str,
                              target_params: Dict, final_duration_estimate: float, moving_speed: float,
                              logo_relative_height: float, logo_alpha: float) -> Tuple[List[str], Optional[str]]:
    """Builds the filter string parts for the moving logo overlay."""
    filter_parts = []
    next_video_output_label = current_video_input_label

    print(f"  Настройка фильтра для движущейся рекламы (Input: {moving_input_stream_label})...")
    moving_input_index = moving_input_stream_label.strip('[]').split(':')[0]
    scaled_moving_stream = f"[moving_scaled_{moving_input_index}]"
    transparent_moving_stream = f"[moving_alpha_{moving_input_index}]"
    overlay_output_label_moving = f"[v_moving_out_{moving_input_index}]"

    # Scaling
    main_h = target_params['height'] if target_params['height'] else 720
    logo_target_h = max(1, int(main_h * logo_relative_height))
    sar_value = target_params['sar'].replace(':', '/')
    moving_scale_filter = f"scale=-1:{logo_target_h}:flags=bicubic"
    filter_parts.append(f"{moving_input_stream_label}{moving_scale_filter},setsar=sar={sar_value}{scaled_moving_stream}")
    # print(f"    Фильтр масштабирования движ. рекламы: {moving_scale_filter}, setsar=sar={sar_value}") # Less verbose

    # Transparency
    clamped_alpha = max(0.0, min(1.0, logo_alpha))
    # Assume input might have alpha, use colorchannelmixer. Add format=rgba for safety.
    alpha_filter = f"format=pix_fmts=rgba,colorchannelmixer=aa={clamped_alpha:.3f}"
    filter_parts.append( f"{scaled_moving_stream}{alpha_filter}{transparent_moving_stream}")
    # print(f"    Фильтр прозрачности движ. рекламы: {alpha_filter}") # Less verbose

    # Animation Expressions
    T_total = max(0.1, final_duration_estimate)
    if not isinstance(moving_speed, (int, float)) or moving_speed <= 0: moving_speed = 1.0
    cycle_T = T_total / moving_speed if moving_speed > 0 else T_total
    x_expr, y_expr = "'0'", "'0'" # Default top-left

    if cycle_T > 0.5: # Animate if cycle is long enough
        t1, t2, t3, seg_dur = cycle_T/4, cycle_T/2, 3*cycle_T/4, max(cycle_T/4, 1e-6)
        mx, my, tv = f"(main_w-overlay_w)", f"(main_h-overlay_h)", f"mod(t,{cycle_T:.6f})"
        x1, x2, x3, x4 = f"{mx}*({tv}/{seg_dur:.6f})", f"{mx}", f"{mx}*(1-(({tv}-{t2:.6f})/{seg_dur:.6f}))", "0"
        y1, y2, y3, y4 = "0", f"{my}*(({tv}-{t1:.6f})/{seg_dur:.6f})", f"{my}", f"{my}*(1-(({tv}-{t3:.6f})/{seg_dur:.6f}))"
        x_expr = f"'if(lt({tv},{t1:.6f}),{x1},if(lt({tv},{t2:.6f}),{x2},if(lt({tv},{t3:.6f}),{x3},{x4})))'"
        y_expr = f"'if(lt({tv},{t1:.6f}),{y1},if(lt({tv},{t2:.6f}),{y2},if(lt({tv},{t3:.6f}),{y3},{y4})))'"
        print(f"    Анимация движ. рекламы: Прямоугольный путь ({cycle_T:.2f}s цикл).")
    else: print(f"    Предупреждение: Длительность цикла ({cycle_T:.3f}s) мала, логотип статичен.")

    # Overlay Filter
    overlay_filter = f"{current_video_input_label}{transparent_moving_stream}overlay=x={x_expr}:y={y_expr}:shortest=0{overlay_output_label_moving}"
    filter_parts.append(overlay_filter)
    next_video_output_label = overlay_output_label_moving
    print(f"    Фильтр overlay для движ. рекламы добавлен. Выход: {next_video_output_label}")
    return filter_parts, next_video_output_label

# --- Build Filter Complex (Using Banner with 'between') ---
def _get_banner_scale_filter(target_params: Dict) -> str:
    """ Generates the scale filter part string for the banner (width=target, height=proportional). """
    return f"scale={target_params['width']}:-1:flags=bicubic"

def _build_filter_complex(
    base_video_specifier: str, base_audio_specifier: Optional[str], target_params: Dict,
    final_duration_estimate: float, is_concat_mode: bool, sorted_embed_ads: List[Dict],
    banner_file: Optional[str], banner_timecodes: Optional[List[str]], banner_input_idx: Optional[int],
    moving_file: Optional[str], moving_input_idx: Optional[int],
    moving_speed: float, moving_logo_relative_height: float, moving_logo_alpha: float) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """ Builds the complete -filter_complex string with 'between' for banner. """
    all_filter_parts = []
    last_filter_video_label = f"[{base_video_specifier}]"
    final_video_output_map_label = base_video_specifier # Default output label
    final_audio_map_label = None # Audio is not processed by filters here

    # --- Banner Filter (with 'between' logic) ---
    if banner_file and banner_timecodes and banner_input_idx is not None:
        try:
            print(f"  Настройка фильтра для баннера (Input: [{banner_input_idx}:v], с between)...")
            banner_input_stream_label = f"[{banner_input_idx}:v]"
            scaled_banner_stream = f"[banner_scaled_{banner_input_idx}]"
            overlay_output_label_banner = f"[v_banner_out_{banner_input_idx}]"
            sar_value = target_params['sar'].replace(':', '/')
            banner_scale_str = _get_banner_scale_filter(target_params)
            current_filter_stage = [] # Filters for this stage (scale + overlay)

            # 1. Scale/SAR filter part
            current_filter_stage.append(f"{banner_input_stream_label}{banner_scale_str},setsar=sar={sar_value}{scaled_banner_stream}")

            # 2. Banner duration
            banner_duration = get_media_duration(banner_file) or 5.0 # Default 5s for images/errors

            # 3. Calculate 'enable' expression
            enable_parts = []
            valid_banner_timecodes_sec = sorted(filter(None, [_timecode_to_seconds(tc) for tc in banner_timecodes]))
            for banner_original_sec in valid_banner_timecodes_sec:
                adjusted_start_time = 0.0
                if is_concat_mode:
                    temp_output_time, temp_original_time = 0.0, 0.0
                    for ad in sorted_embed_ads:
                         if ad['time_sec'] <= banner_original_sec:
                             temp_output_time += (ad['time_sec'] - temp_original_time) + ad['duration']
                             temp_original_time = ad['time_sec']
                         else: break
                    temp_output_time += (banner_original_sec - temp_original_time)
                    adjusted_start_time = temp_output_time
                else: adjusted_start_time = banner_original_sec

                end_time = min(adjusted_start_time + banner_duration, final_duration_estimate)
                if end_time > adjusted_start_time + 0.001 and adjusted_start_time < final_duration_estimate:
                    enable_parts.append(f"between(t,{adjusted_start_time:.3f},{end_time:.3f})")

            # 4. Add overlay filter if enable times exist
            if enable_parts:
                enable_expression = "+".join(enable_parts)
                overlay_y_pos, overlay_x_pos = "main_h-overlay_h", "0" # Bottom-left
                banner_overlay_filter = (f"{last_filter_video_label}{scaled_banner_stream}"
                                         f"overlay=x={overlay_x_pos}:y={overlay_y_pos}:enable='{enable_expression}'"
                                         f"{overlay_output_label_banner}")
                current_filter_stage.append(banner_overlay_filter) # Add overlay to this stage
                all_filter_parts.extend(current_filter_stage) # Add all parts for banner
                last_filter_video_label = overlay_output_label_banner # Update video label
                final_video_output_map_label = last_filter_video_label.strip('[]') # Update final label
                print(f"    Фильтр overlay для баннера (с between) добавлен. Выход: {last_filter_video_label}")
            else: print("    Предупреждение: Не удалось создать таймкоды 'enable' для баннера, фильтр не добавлен.")
        except Exception as e: print(f"Предупреждение: Ошибка при построении фильтра баннера: {e}.")

    # --- Moving Logo Filter ---
    if moving_file and moving_input_idx is not None:
        try:
            moving_input_stream_label = f"[{moving_input_idx}:v]"
            logo_filters, last_video_label_after_logo = _build_moving_logo_filter(
                last_filter_video_label, moving_input_stream_label, target_params,
                final_duration_estimate, moving_speed, moving_logo_relative_height, moving_logo_alpha )
            if last_video_label_after_logo: # Check if logo filter was actually added
                all_filter_parts.extend(logo_filters)
                last_filter_video_label = last_video_label_after_logo # Update label again
                final_video_output_map_label = last_filter_video_label.strip('[]') # Update final label
        except Exception as e: print(f"Предупреждение: Ошибка при построении фильтра движ. лого: {e}.")

    # --- Final Assembly ---
    if not all_filter_parts:
        print("--- Фильтры не применялись ---")
        return None, base_video_specifier, base_audio_specifier # Return base specifiers

    filter_complex_str = ";".join(all_filter_parts)
    print(f"--- Итоговый filter_complex сгенерирован. Видео выход: [{final_video_output_map_label}] ---")
    return filter_complex_str, final_video_output_map_label, final_audio_map_label # final_audio_map_label is None


# --- Generate Main Command ---
def _generate_main_ffmpeg_command(
    input_file: str, output_file: str, encoding_params_str: str, target_params: Dict,
    main_video_duration: float, track_data: Dict,
    banner_file: Optional[str], banner_timecodes: Optional[List[str]], # Original banner info
    moving_file: Optional[str], moving_speed: float, moving_logo_relative_height: float, moving_logo_alpha: float,
    is_concat_mode: bool, concat_list_path: Optional[str],
    sorted_embed_ads: List[Dict], total_embed_duration_added: float
    ) -> Tuple[str, List[str]]:
    """ Generates the main FFmpeg command string using overlay with 'between' for banner. """
    print("--- Фаза 3: Генерация основной команды конвертации (с overlay/between для баннера) ---")
    main_cmd_parts = ["ffmpeg", "-y"]; input_definitions = []; map_commands = []; metadata_args = []; temp_files_for_main = []

    # Primary Input Definition
    primary_input_options = []; primary_input_path = ""
    base_video_specifier = "0:v:0?"; base_audio_specifier = "0:a:0?" if target_params['has_audio'] else None
    subtitle_input_specifier = None; metadata_input_index = 0; final_duration_estimate = 0.0
    if is_concat_mode:
        if not concat_list_path or not os.path.exists(concat_list_path): raise CommandGenerationError("Concat list not found.")
        primary_input_options = ["-f", "concat", "-safe", "0"]; primary_input_path = concat_list_path
        temp_files_for_main.append(concat_list_path); final_duration_estimate = main_video_duration + total_embed_duration_added
        print(f"Режим: Конкатенация. Input 0: {os.path.basename(concat_list_path)}")
        original_info = get_stream_info(input_file)
        if any(s.get('codec_type') == 'subtitle' for s in original_info.get("streams", [])):
             print(f"  Input 1 (Subs/Metadata): {os.path.basename(input_file)}")
             input_definitions.append(([], input_file)); subtitle_input_specifier = "1:s?"; metadata_input_index = 1
        else: metadata_input_index = 0
    else:
        primary_input_path = input_file; final_duration_estimate = main_video_duration; metadata_input_index = 0
        original_info = get_stream_info(input_file)
        if any(s.get('codec_type') == 'subtitle' for s in original_info.get("streams", [])): subtitle_input_specifier = "0:s?"
        print(f"Режим: Прямая конвертация. Input 0: {os.path.basename(input_file)}")
    input_definitions.insert(0, (primary_input_options, primary_input_path))
    print(f"Расчетная финальная длительность: {final_duration_estimate:.3f}s")

    # Overlay Inputs Definition
    current_input_index = len(input_definitions); banner_input_idx = None; moving_input_idx = None
    # Add Original Banner Input with loop options
    if banner_file and banner_timecodes and os.path.exists(banner_file):
        banner_options = []
        banner_duration = get_media_duration(banner_file)
        if banner_duration is not None and banner_duration > 0.01: banner_options.extend(["-stream_loop", "-1"]) # Loop video
        else: banner_options.extend(["-loop", "1"]) # Loop image
        print(f"  Input {current_input_index} (Banner): {os.path.basename(banner_file)} ({'Video' if banner_duration else 'Image'})")
        input_definitions.append((banner_options, banner_file)); banner_input_idx = current_input_index; current_input_index += 1
    # Add Moving Logo Input
    if moving_file and os.path.exists(moving_file):
        moving_options = ["-loop", "1"]
        if get_media_duration(moving_file) is None: moving_options.extend(["-r", target_params['fps_str']]) # Add rate for images
        print(f"  Input {current_input_index} (Moving Logo): {os.path.basename(moving_file)}")
        input_definitions.append((moving_options, moving_file)); moving_input_idx = current_input_index; current_input_index += 1

    # Assemble Input Part
    for options, path in input_definitions: main_cmd_parts.extend(options); main_cmd_parts.extend(["-i", f'"{path}"'])

    # Build Filter Complex
    filter_complex_str, final_video_map_label, final_audio_map_label = _build_filter_complex(
        base_video_specifier, base_audio_specifier, target_params, final_duration_estimate, is_concat_mode,
        sorted_embed_ads, banner_file, banner_timecodes, banner_input_idx, # Pass original banner info
        moving_file, moving_input_idx, moving_speed, moving_logo_relative_height, moving_logo_alpha )

    # Mapping
    if filter_complex_str:
        main_cmd_parts.extend([f'-filter_complex', f'"{filter_complex_str}"'])
        map_commands.append(f'-map "[{final_video_map_label}]"') # Map video from filter label
        # Audio is NOT from filter, map base if exists
        if base_audio_specifier and target_params['has_audio']: map_commands.append(f'-map {base_audio_specifier}')
    else: # No filters
        map_commands.append(f'-map {base_video_specifier}')
        if base_audio_specifier and target_params['has_audio']: map_commands.append(f'-map {base_audio_specifier}')
    if subtitle_input_specifier: map_commands.append(f"-map {subtitle_input_specifier}") # Map subtitles
    main_cmd_parts.extend(map_commands)

    # Metadata
    metadata_args.extend([f'-map_metadata {metadata_input_index}', "-movflags", "use_metadata_tags"])
    source_file_for_metadata = input_file
    if source_file_for_metadata: # Simplified metadata logic from previous attempts
        original_info = get_stream_info(source_file_for_metadata)
        # Simplified output index simulation
        mapped_output_indices = {} # original_spec -> output_index
        output_stream_counter = {'v': 0, 'a': 0, 's': 0}
        # Determine map keys based on actual map commands generated above
        video_map_key = map_commands[0].split()[1] # First map is video
        audio_map_key = None
        subs_map_keys = []
        for cmd in map_commands[1:]:
             key = cmd.split()[1]
             if ':a' in key: audio_map_key = key
             elif ':s' in key: subs_map_keys.append(key)
        # Assign output indices based on determined map keys
        if video_map_key: mapped_output_indices[video_map_key] = output_stream_counter['v']; output_stream_counter['v'] += 1
        if audio_map_key: mapped_output_indices[audio_map_key] = output_stream_counter['a']; output_stream_counter['a'] += 1
        for key in subs_map_keys: mapped_output_indices[key] = output_stream_counter['s']; output_stream_counter['s'] += 1

        print(f"  Карта выходных индексов (для метаданных): {mapped_output_indices}") # Debug print
        for track_id_from_user, edits in track_data.items(): # Process edits
            parts = track_id_from_user.split(':')
            normalized_track_id = None
            if len(parts) == 2: normalized_track_id = f"{metadata_input_index}:{parts[0]}:{parts[1]}"
            elif len(parts) == 3 and parts[0] == str(metadata_input_index): normalized_track_id = track_id_from_user
            else: continue

            # Try to find the direct output index mapping for the original stream specifier
            output_idx = None
            # We need to search mapped_output_indices using the original specifier (normalized_track_id)
            # The keys in mapped_output_indices are the map command targets (e.g., "[label]" or "0:a:0?")
            # This link is complex. Simplified: Assume simple 1-to-1 mapping if types match
            # Example: if normalized is "0:a:0", find the output index mapped by "0:a:0?"
            target_map_key_search = normalized_track_id + "?" # Add optional marker for base specifiers
            found_key = None
            if target_map_key_search in mapped_output_indices:
                found_key = target_map_key_search
            elif normalized_track_id in mapped_output_indices: # Exact match (e.g., for subs)
                 found_key = normalized_track_id

            if found_key:
                 output_idx = mapped_output_indices[found_key]
                 stream_type_char = normalized_track_id.split(':')[1]
                 output_metadata_specifier = f"s:{stream_type_char}:{output_idx}"
                 print(f"    Применение метаданных к вых. потоку {output_metadata_specifier} (из {normalized_track_id})")
                 if 'title' in edits and edits['title']:
                      metadata_args.extend([f"-metadata:{output_metadata_specifier}", f"title={shlex.quote(str(edits['title']))}"])
                 if 'language' in edits and edits['language']:
                      lang = str(edits['language']).lower()
                      if len(lang)==3 and lang.isalpha(): metadata_args.extend([f"-metadata:{output_metadata_specifier}", f"language={lang}"])
            # else: print warning removed for brevity

    main_cmd_parts.extend(metadata_args)

    # Encoding Parameters
    if encoding_params_str:
        try:
            user_params = shlex.split(encoding_params_str); has_t_flag = any(p in ['-t', '-to'] for p in user_params)
            main_cmd_parts.extend(user_params)
            if not has_t_flag and final_duration_estimate > 0: main_cmd_parts.extend(['-t', f"{final_duration_estimate:.6f}"])
        except ValueError as e: raise CommandGenerationError(f"Неверный синтаксис в параметрах кодирования: {e}")
    elif final_duration_estimate > 0: main_cmd_parts.extend(['-t', f"{final_duration_estimate:.6f}"])

    # MP4 Flags (ensure faststart, avoid duplicates)
    if output_file.lower().endswith(".mp4"):
        movflags_val = "+faststart"; movflags_present = False
        new_cmd_parts = []
        skip_next = False
        for i, part in enumerate(main_cmd_parts):
             if skip_next: skip_next = False; continue
             if part == "-movflags":
                  movflags_present = True
                  if i + 1 < len(main_cmd_parts):
                       existing_flags = main_cmd_parts[i+1]
                       if "+faststart" not in existing_flags: movflags_val = f"{existing_flags}+faststart"
                       else: movflags_val = existing_flags
                       skip_next = True # Skip the original value part
                  # else: -movflags with no value? Ignore for merge
             else:
                  new_cmd_parts.append(part)
        main_cmd_parts = new_cmd_parts # Replace with filtered parts
        if movflags_val: main_cmd_parts.extend(["-movflags", movflags_val]) # Add the final movflags

    # Output File
    main_cmd_parts.append(f'"{output_file}"')
    final_main_cmd = " ".join(main_cmd_parts)
    return final_main_cmd, temp_files_for_main


# --- Main Orchestrator Function ---
def generate_ffmpeg_commands(
        input_file: str, output_file: str, encoding_params_str: str,
        track_data: Dict, embed_ads: List[Dict],
        banner_file: Optional[str], banner_timecodes: Optional[List[str]],
        moving_file: Optional[str], moving_speed: float = _MOVING_SPEED,
        moving_logo_relative_height: float = _MOVING_LOGO_RELATIVE_HEIGHT,
        moving_logo_alpha: float = _MOVING_LOGO_ALPHA):
    """
    Generates FFmpeg commands for conversion, handling ads via concat,
    and overlays (banner using 'between', moving logo).

    Returns:
        tuple: (list[preprocessing_cmds], main_command, list[temp_files])
    """
    all_preprocessing_commands = [] # Only for main video + ads
    all_temp_files = []
    concat_list_path = None
    total_embed_duration_added = 0.0

    print("--- Получение параметров основного видео ---")
    main_video_params = get_essential_stream_params(input_file)
    if not main_video_params: raise CommandGenerationError(f"Не удалось получить параметры из: {input_file}")
    main_video_duration = get_media_duration(input_file)

    print("--- Проверка и подготовка входных данных ---")
    try:
        valid_params, valid_duration, sorted_embed_ads_info, valid_banner_file, valid_banner_timecodes, valid_moving_file = _validate_and_prepare_inputs(
            input_file, output_file, main_video_params, main_video_duration, embed_ads, banner_file, banner_timecodes, moving_file)
        banner_file, banner_timecodes, moving_file = valid_banner_file, valid_banner_timecodes, valid_moving_file
    except CommandGenerationError as e: print(f"Ошибка проверки вх. данных: {e}"); raise

    print("--- Определение целевых параметров кодирования ---")
    target_params = _determine_target_parameters(valid_params)

    is_concat_mode = bool(sorted_embed_ads_info)
    if is_concat_mode:
        try:
            prep_cmds_main, concat_list_path, prep_temp_files_main, total_embed_duration_added = \
                _generate_preprocessing_for_concat( input_file, sorted_embed_ads_info, target_params, valid_duration )
            all_preprocessing_commands.extend(prep_cmds_main)
            all_temp_files.extend(prep_temp_files_main)
        except CommandGenerationError as e: _cleanup_temp_files(all_temp_files); print(f"Ошибка препроцессинга видео+рекламы: {e}"); raise e
    else:
        print("--- Предварительная обработка видео+рекламы: Пропущена ---"); total_embed_duration_added = 0.0

    print("--- Генерация основной команды FFmpeg ---")
    try:
        main_command, main_temp_files = _generate_main_ffmpeg_command(
            input_file, output_file, encoding_params_str, target_params, valid_duration, track_data,
            banner_file, banner_timecodes, # Pass original (validated) banner info
            moving_file, moving_speed, moving_logo_relative_height, moving_logo_alpha,
            is_concat_mode, concat_list_path, sorted_embed_ads_info, total_embed_duration_added )
        all_temp_files.extend(main_temp_files)
    except CommandGenerationError as e: _cleanup_temp_files(all_temp_files); print(f"Ошибка генерации основной команды: {e}"); raise e

    return all_preprocessing_commands, main_command, all_temp_files


# --- FFmpeg Execution ---
def run_ffmpeg_command(cmd, step_name):
    """Executes a single FFmpeg command using subprocess.run and handles errors."""
    print(f"\n--- Запуск шага: {step_name} ---")
    if len(cmd) > 1000: print(f"Команда: {cmd[:500]}... (всего {len(cmd)} симв.)")
    else: print(f"Команда: {cmd}")
    try:
        startupinfo = None
        if os.name == 'nt': startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startupinfo.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', startupinfo=startupinfo)
        stderr_output, progress_line = "", ""
        while True:
            line = process.stderr.readline()
            if not line: break
            stderr_output += line; stripped = line.strip()
            if stripped.startswith(('frame=', 'size=')): progress_line = stripped; print(f"  {progress_line}", end='\r')
            elif progress_line: print(f"\n  [stderr] {stripped}"); progress_line = ""
            else: print(f"  [stderr] {stripped}")
        if progress_line: print() # Newline after progress
        process.stdout.close(); return_code = process.wait()
        if return_code != 0: raise ConversionError(f"Ошибка '{step_name}' (код {return_code}).\nКоманда:\n{cmd}\nStderr (конец):\n{stderr_output[-2000:]}")
        print(f"--- {step_name}: Успешно завершено ---")
        # Optional: print last few lines of stderr on success
        # stderr_lines = stderr_output.splitlines()[-20:]; print("\n".join(stderr_lines))
        return True
    except FileNotFoundError: raise FfmpegError("FFmpeg не найден. Убедитесь, что он установлен и в PATH.") from None
    except Exception as e: raise FfmpegError(f"Неожиданная ошибка при запуске '{step_name}': {type(e).__name__} - {e}") from e


# --- Cleanup Helper ---
def _cleanup_temp_files(temp_files: List[str]):
    """Attempts to delete temporary files."""
    if not temp_files: return
    print(f"\n--- Очистка временных файлов ({len(temp_files)}) ---")
    deleted_count, failed_count = 0, 0
    for f in temp_files:
        try:
            if f and os.path.exists(f): os.remove(f); deleted_count += 1
        except OSError as e: print(f"  Ошибка удаления {f}: {e}"); failed_count += 1
        except Exception as e: print(f"  Неожиданная ошибка при удалении {f}: {e}"); failed_count += 1
    print(f"--- Очистка завершена (Удалено: {deleted_count}, Ошибок: {failed_count}/{len(temp_files)}) ---")


# --- Example Usage (Conceptual) ---
# if __name__ == '__main__':
#     # --- Create Dummy Files ---
#     dummy_files_to_clean = []
#     def create_dummy(cmd, outfile):
#         if not os.path.exists(outfile):
#             print(f"Создание dummy файла: {outfile}")
#             try: subprocess.run(cmd, shell=True, check=True, capture_output=True); dummy_files_to_clean.append(outfile)
#             except Exception as e: print(f"Не удалось создать {outfile}: {e}")
#         else: dummy_files_to_clean.append(outfile) # Assume existing is ok

#     in_file = "input_main.mp4"
#     create_dummy(f'ffmpeg -y -f lavfi -i testsrc=duration=60:size=1280x720:rate=25 -f lavfi -i sine=f=1000:r=44100:d=60 -vf "drawtext=text=\'MAIN %{{pts:hms}}\':x=(w-tw)/2:y=(h-th)/2:fontcolor=white:fontsize=50:box=1:boxcolor=black@0.5" -c:v libx264 -pix_fmt yuv420p -c:a aac -ar 44100 -shortest "{in_file}"', in_file)
#     ad1_file = "ad1.mp4"
#     create_dummy(f'ffmpeg -y -f lavfi -i testsrc=duration=5:size=1280x720:rate=25:pattern=7 -f lavfi -i sine=f=440:r=44100:d=5 -vf "drawtext=text=\'AD 1\':x=(w-tw)/2:y=(h-th)/2:fontcolor=yellow:fontsize=50:box=1:boxcolor=red@0.5" -c:v libx264 -pix_fmt yuv420p -c:a aac -ar 44100 -shortest "{ad1_file}"', ad1_file)
#     banner_img_file = "banner_image.png"
#     create_dummy(f'ffmpeg -y -f lavfi -i color=c=blue:s=1280x80:d=1 -vf "drawtext=text=\'BANNER\':x=(w-tw)/2:y=(h-th)/2:fontcolor=white:fontsize=30" "{banner_img_file}"', banner_img_file)
#     moving_logo_file = "moving_logo.png"
#     create_dummy(f'ffmpeg -y -f lavfi -i color=c=red@0.8:s=150x150:d=1 -vf "drawtext=text=\'LOGO\':x=(w-tw)/2:y=(h-th)/2:fontcolor=white:fontsize=30" "{moving_logo_file}"', moving_logo_file)

#     # --- Test Case Setup ---
#     out_file = "output_test_between.mp4"
#     enc_params = "-c:v libx264 -preset medium -crf 24 -c:a aac -b:a 128k"
#     ads = [{'path': ad1_file, 'timecode': '0:15'}, {'path': ad1_file, 'timecode': '0:45'}]
#     use_banner_file = banner_img_file
#     use_banner_tc = ["0:05", "0:25", "0:55"]
#     use_moving_file = moving_logo_file
#     tracks = {"0:a:0": {"language": "eng", "title": "Main Audio"}}

#     # --- Run Generation ---
#     generated_temp_files = []
#     try:
#         prep_cmds, main_cmd, generated_temp_files = generate_ffmpeg_commands(
#             input_file=in_file, output_file=out_file, encoding_params_str=enc_params, track_data=tracks,
#             embed_ads=ads, banner_file=use_banner_file, banner_timecodes=use_banner_tc, moving_file=use_moving_file )

#         print("\n" + "="*20 + " Generated Commands (Using 'between') " + "="*20)
#         print(f"Preprocessing Commands ({len(prep_cmds)}):")
#         for i, cmd in enumerate(prep_cmds): print(f"  {i+1}: {cmd[:150]}...")
#         print("\nMain Command:")
#         print(f"  {main_cmd}")
#         print("\nTemp Files Generated:")
#         for f in generated_temp_files: print(f"  {f}")
#         print("="*70)

#         # --- Execute Commands (Optional) ---
#         # execute = True
#         # if execute:
#         #     for i, cmd in enumerate(prep_cmds): run_ffmpeg_command(cmd, f"Prep {i+1}/{len(prep_cmds)}")
#         #     run_ffmpeg_command(main_cmd, "Main Conversion")
#         #     print("\n--- CONVERSION FINISHED (Presumably) ---")

#     except (CommandGenerationError, ConversionError, FfprobeError, FfmpegError) as e: print(f"\n--- ERROR ---\n{type(e).__name__}: {e}")
#     finally: _cleanup_temp_files(generated_temp_files); print("\n--- Cleaning dummy files ---"); _cleanup_temp_files(dummy_files_to_clean)