# converter/ffmpeg_utils.py
import json
import os
import shlex
import subprocess
import tempfile
import time
from typing import List, Tuple, Dict, Any, Optional

from .exceptions import FfprobeError, CommandGenerationError, ConversionError, FfmpegError

# --- Constants ---
# (Keep existing constants)
_TEMP_VIDEO_CODEC = "h264_nvenc"  # Or libx264
_TEMP_VIDEO_PRESET = "fast"
_TEMP_VIDEO_CRF = "18"
_TEMP_AUDIO_CODEC = "aac"
_TEMP_AUDIO_BITRATE = "192k"
_MOVING_SPEED = 2
_MOVING_LOGO_RELATIVE_HEIGHT = 1 / 10
_MOVING_LOGO_ALPHA = 0.7


# --- FFprobe Utilities ---
# (Keep existing ffprobe functions: run_ffprobe, get_media_duration, get_stream_info, get_essential_stream_params)

def run_ffprobe(command):
    """Runs an ffprobe command and returns the parsed JSON output."""
    try:
        # Use startupinfo to prevent console window flashing on Windows
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
        raise FfprobeError(f"Ошибка выполнения ffprobe: {e}\nВывод stderr: {e.stderr}")
    except json.JSONDecodeError as e:
        # Try to get stdout even if JSON parsing fails
        stdout_content = "N/A"
        try:
            stdout_content = e.doc  # The document that failed to parse
        except AttributeError:
            pass  # Or try to get from a potential result variable if stored before raising
        raise FfprobeError(f"Ошибка декодирования вывода ffprobe: {e}\nНачало вывода stdout: {stdout_content[:500]}")
    except Exception as e:
        raise FfprobeError(f"Неожиданная ошибка при выполнении ffprobe: {e}")


def get_media_duration(file_path):
    """Gets media duration using ffprobe."""
    if not file_path or not os.path.exists(file_path):
        print(f"get_media_duration: Файл не найден или путь пуст: {file_path}")
        return None
    try:
        command = ["ffprobe", "-v", "quiet", "-i", file_path,
                   "-show_entries", "format=duration",
                   "-print_format", "json"]
        output = run_ffprobe(command)
        duration_str = output.get("format", {}).get("duration")
        if duration_str:
            return float(duration_str)
        else:
            # Fallback: Try getting video stream duration
            command_stream = ["ffprobe", "-v", "quiet", "-i", file_path,
                              "-select_streams", "v:0",
                              "-show_entries", "stream=duration",
                              "-print_format", "json"]
            try:
                output_stream = run_ffprobe(command_stream)
                duration_str_stream = output_stream.get("streams", [{}])[0].get("duration")
                if duration_str_stream:
                    return float(duration_str_stream)
            except (FfprobeError, IndexError, KeyError, ValueError, TypeError):
                pass  # Couldn't get stream duration either
            print(f"get_media_duration: Не найдена длительность (format или stream) для {file_path}")
            return None  # Indicates image or error reading duration

    except FfprobeError as e:
        print(f"Ошибка ffprobe при получении длительности для {file_path}: {e}")
        return None
    except (ValueError, TypeError) as e:
        print(f"Ошибка преобразования длительности для {file_path}: {e}")
        return None


def get_stream_info(file_path):
    """Gets info about all streams using ffprobe."""
    command = ["ffprobe", "-v", "quiet", "-i", file_path,
               "-show_streams", "-show_format", "-print_format", "json"]
    return run_ffprobe(command)


def get_essential_stream_params(file_path):
    """Gets key video and audio parameters needed for compatibility checks using ffprobe."""
    params = {
        'width': None, 'height': None, 'pix_fmt': None, 'sar': '1:1', 'par': None, 'time_base_v': None, 'fps_str': None,
        'sample_rate': None, 'channel_layout': None, 'sample_fmt': None, 'time_base_a': None
    }
    if not file_path or not os.path.exists(file_path): return None

    try:
        # Get video stream info (first video stream)
        cmd_video = [
            "ffprobe", "-v", "quiet", "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,pix_fmt,sample_aspect_ratio,display_aspect_ratio,r_frame_rate,time_base",
            "-of", "json", file_path
        ]
        data_v = run_ffprobe(cmd_video)
        if data_v.get("streams"):
            stream_v = data_v["streams"][0]
            params['width'] = stream_v.get('width')
            params['height'] = stream_v.get('height')
            params['pix_fmt'] = stream_v.get('pix_fmt')
            params['sar'] = stream_v.get('sample_aspect_ratio', '1:1')
            params['time_base_v'] = stream_v.get('time_base', '1/25')
            params['fps_str'] = stream_v.get('r_frame_rate', '25/1')
        else:
            print(f"Предупреждение: Не найден видеопоток в {file_path}")

        # Get audio stream info (first audio stream)
        cmd_audio = [
            "ffprobe", "-v", "quiet", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channel_layout,sample_fmt,time_base",
            "-of", "json", file_path
        ]
        try:
            data_a = run_ffprobe(cmd_audio)
            if data_a.get("streams"):
                stream_a = data_a["streams"][0]
                params['sample_rate'] = stream_a.get('sample_rate')
                params['channel_layout'] = stream_a.get('channel_layout')
                params['sample_fmt'] = stream_a.get('sample_fmt')
                params['time_base_a'] = stream_a.get('time_base', '1/44100')
            else:
                print(f"Предупреждение: Не найден аудиопоток в {file_path}")
        except FfprobeError:
            print(f"Предупреждение: Ошибка получения аудиопотока из {file_path} (может отсутствовать).")

        if not params['width'] or not params['height'] or not params['fps_str']:
            print("Предупреждение: Не удалось получить основные видеопараметры (ширина/высота/fps).")
        if not params['sample_rate'] and params.get(
                'time_base_a') is None:  # Check if audio stream was likely present but failed
            print("Предупреждение: Не удалось получить параметры аудио (если аудио есть).")

        return params

    except FfprobeError as e:
        print(f"Ошибка ffprobe при получении параметров потока: {e}")
        return None
    except Exception as e:
        print(f"Неожиданная ошибка в get_essential_stream_params: {e}")
        return None

# --- FFmpeg Command Generation Helpers ---

def _generate_temp_filename(prefix, index):
    """Generates a unique temporary filename."""
    temp_dir = tempfile.gettempdir()
    timestamp = int(time.time() * 1000)
    filename = f"{prefix}_{index}_{timestamp}.mkv"
    return os.path.join(temp_dir, filename)


def _escape_path_for_concat(path):
    """ Prepares a path for the concat demuxer file list. """
    path = path.replace('\\', '/')
    path = path.replace("'", "'\\''")
    return f"'{path}'"


def _timecode_to_seconds(tc: str) -> float:
    """Converts MM:SS or HH:MM:SS timecode string to seconds."""
    parts = list(map(float, tc.split(':')))
    if len(parts) == 2:  # MM:SS
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3: # HH:MM:SS
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    else:
        print(f"Предупреждение: Неверный формат таймкода '{tc}', используется 0.0")
        return 0.0


def _validate_and_prepare_inputs(input_file: str, output_file: str, main_video_params: Optional[Dict],
                                 main_video_duration: Optional[float], embed_ads: List[Dict],
                                 banner_file: Optional[str], moving_file: Optional[str]) -> Tuple[Dict, float, List[Dict]]:
    """Validates inputs and prepares basic structures."""
    if not all([input_file, output_file, main_video_params, main_video_duration]):
        raise CommandGenerationError("Отсутствуют необходимые входные данные (файл, параметры, длительность).")
    if not os.path.exists(input_file):
        raise CommandGenerationError(f"Входной файл не найден: {input_file}")
    if main_video_duration <= 0:
        raise CommandGenerationError("Недопустимая длительность основного видео.")

    # Validate overlay files exist if specified
    if banner_file and not os.path.exists(banner_file):
        print(f"Предупреждение: Файл баннера не найден '{banner_file}', будет проигнорирован.")
        banner_file = None
    if moving_file and not os.path.exists(moving_file):
        print(f"Предупреждение: Файл движущейся рекламы не найден '{moving_file}', будет проигнорирован.")
        moving_file = None

    # Sort ads by timecode
    sorted_embed_ads = sorted(embed_ads, key=lambda ad: _timecode_to_seconds(ad.get('timecode', '0:0')))

    # Get essential ad durations (needed for concat logic and final duration estimate)
    ads_with_duration = []
    for ad in sorted_embed_ads:
        ad_path = ad.get('path')
        if not ad_path or not os.path.exists(ad_path):
            print(f"Предупреждение: Пропуск рекламы с отсутствующим/не найденным файлом: {ad_path}")
            continue
        ad_duration = get_media_duration(ad_path)
        if ad_duration is None or ad_duration <= 0:
             # Attempt to get duration from main_video if ad duration fails (e.g., if it's an image/short clip)
             ad_duration = ad.get('duration', None)
             if ad_duration is None:
                 print(f"Предупреждение: Не удалось определить длительность для '{ad_path}', используется 5с.")
                 ad_duration = 5.0 # Fallback duration
        ad['duration'] = ad_duration # Store duration back into the dict
        ads_with_duration.append(ad)


    return main_video_params, main_video_duration, ads_with_duration


def _determine_target_parameters(main_video_params: Dict) -> Dict[str, Any]:
    """Determines target parameters based on the main video."""
    target_params = {
        'width': main_video_params.get('width'),
        'height': main_video_params.get('height'),
        'sar': main_video_params.get('sar', '1:1'),
        'fps_str': main_video_params.get('fps_str'),
        'pix_fmt': main_video_params.get('pix_fmt', 'yuv420p'),
        'v_timebase': main_video_params.get('time_base_v'),
        'sample_rate': main_video_params.get('sample_rate'),
        'channel_layout': main_video_params.get('channel_layout', 'stereo'),
        'sample_fmt': main_video_params.get('sample_fmt', 'fltp'),
        'a_timebase': main_video_params.get('time_base_a'),
        'has_audio': bool(main_video_params.get('sample_rate')),
        'video_timescale': "90000" # Default
    }

    # Calculate video timescale from timebase
    if target_params['v_timebase'] and '/' in target_params['v_timebase']:
        try:
            num, den = map(float, target_params['v_timebase'].split('/'))
            if den != 0: target_params['video_timescale'] = str(int(1.0 / (num / den)))
        except ValueError: pass # Keep default if calculation fails

    # Compatibility checks needed for concat
    if not all([target_params['width'], target_params['height'], target_params['fps_str'], target_params['pix_fmt'], target_params['v_timebase']]):
         raise CommandGenerationError("Не удалось определить ключевые видео параметры для совместимости.")
    if target_params['has_audio'] and not all([target_params['sample_rate'], target_params['channel_layout'], target_params['sample_fmt'], target_params['a_timebase']]):
         raise CommandGenerationError("Не удалось определить ключевые аудио параметры для совместимости (если аудио есть).")

    return target_params


def _create_segment_command(input_path: str, output_path: str, target_params: Dict,
                            start_time: Optional[float] = None, duration: Optional[float] = None,
                            is_ad: bool = False) -> str:
    """Helper function to create a single segment transcoding command."""
    cmd_parts = [
        "ffmpeg", "-hwaccel", "d3d12va", "-y",
        *(["-ss", f"{start_time:.6f}"] if start_time is not None and start_time > 0.001 else []),
        "-i", f'"{input_path}"',
        *(["-t", f"{duration:.6f}"] if duration is not None else []),
        "-avoid_negative_ts", "make_zero",
        "-vf", f"scale={target_params['width']}:{target_params['height']}:force_original_aspect_ratio=decrease:flags=bicubic,"
               f"pad={target_params['width']}:{target_params['height']}:(ow-iw)/2:(oh-ih)/2:color=black,"
               f"setsar={target_params['sar']},"
               f"fps={target_params['fps_str']},"
               f"format=pix_fmts={target_params['pix_fmt']}",
        *(["-af", f"aresample={target_params['sample_rate']},"
                  f"aformat=sample_fmts={target_params['sample_fmt']}:channel_layouts={target_params['channel_layout']}"]
          if target_params['has_audio'] else ["-an"]),
        "-c:v", _TEMP_VIDEO_CODEC, "-preset", _TEMP_VIDEO_PRESET, "-crf", _TEMP_VIDEO_CRF,
        "-b:v", "0",
        *(["-c:a", _TEMP_AUDIO_CODEC, "-b:a", _TEMP_AUDIO_BITRATE] if target_params['has_audio'] else []),
        "-video_track_timescale", target_params['video_timescale'],
        f'"{output_path}"'
    ]
    safe_cmd_parts = [part for part in cmd_parts if part is not None]
    return " ".join(safe_cmd_parts)


def _generate_preprocessing_for_concat(input_file: str, sorted_embed_ads: List[Dict], target_params: Dict,
                                       main_video_duration: float) -> Tuple[List[str], str, List[str], float]:
    """
    Generates preprocessing commands and concat list if ads are present.

    Returns:
        tuple: (list[preprocessing_cmds], concat_list_path, list[temp_files], total_ad_duration)
    """
    print("--- Фаза 1: Генерация команд предварительной обработки сегментов (реклама присутствует) ---")
    preprocessing_commands = []
    temp_files_generated = []
    concat_list_items = []
    total_embed_duration_added = 0.0
    segment_counter = 0
    last_original_time = 0.0

    # Preprocess unique ads first to avoid redundant work
    unique_ad_files = {ad['path']: ad for ad in sorted_embed_ads}.values()
    preprocessed_ad_paths = {}
    for ad_data in unique_ad_files:
        ad_path = ad_data['path']
        # Path existence already checked in _validate_and_prepare_inputs
        temp_ad_path = _generate_temp_filename("ad_segment", segment_counter)
        cmd = _create_segment_command(ad_path, temp_ad_path, target_params, is_ad=True)
        preprocessing_commands.append(cmd)
        temp_files_generated.append(temp_ad_path)
        preprocessed_ad_paths[ad_path] = temp_ad_path
        segment_counter += 1

    # Iterate through time, creating main segments and adding ad segments
    for i, embed in enumerate(sorted_embed_ads):
        embed_original_time_sec = _timecode_to_seconds(embed['timecode'])
        embed_ad_path = embed['path']
        ad_duration = embed.get('duration', 0) # Duration retrieved earlier

        if embed_ad_path not in preprocessed_ad_paths:
            # This case should ideally not happen due to prior checks, but good to keep
            print(f"Предупреждение: Пропуск рекламы '{os.path.basename(embed_ad_path)}' в {embed['timecode']} т.к. предварительная обработка не удалась.")
            continue

        # Main Video Segment (Before Ad)
        main_segment_duration = embed_original_time_sec - last_original_time
        if main_segment_duration > 0.01:
            temp_main_path = _generate_temp_filename("main_segment", segment_counter)
            cmd = _create_segment_command(input_file, temp_main_path, target_params,
                                          start_time=last_original_time,
                                          duration=main_segment_duration)
            preprocessing_commands.append(cmd)
            temp_files_generated.append(temp_main_path)
            concat_list_items.append(temp_main_path)
            segment_counter += 1
        elif main_segment_duration < -0.01:
            print(f"Предупреждение: Отрицательная или нулевая длительность ({main_segment_duration:.3f}s) сегмента основного видео перед {embed['timecode']}, проверьте таймкоды.")

        # Add the Pre-processed Ad Segment
        preprocessed_ad_path = preprocessed_ad_paths[embed_ad_path]
        concat_list_items.append(preprocessed_ad_path)
        total_embed_duration_added += ad_duration

        # Update time for next main segment start
        last_original_time = embed_original_time_sec

    # Final Main Video Segment (After Last Ad)
    if last_original_time < main_video_duration - 0.01:
        final_segment_duration = main_video_duration - last_original_time
        temp_main_path = _generate_temp_filename("main_segment", segment_counter)
        cmd = _create_segment_command(input_file, temp_main_path, target_params,
                                      start_time=last_original_time,
                                      duration=final_segment_duration)
        preprocessing_commands.append(cmd)
        temp_files_generated.append(temp_main_path)
        concat_list_items.append(temp_main_path)
        segment_counter += 1

    # --- Create Concat List File ---
    if not concat_list_items:
        raise CommandGenerationError("Нет сегментов для объединения (ошибка в логике обработки рекламы).")

    print("--- Фаза 2: Создание файла списка concat ---")
    concat_list_filename = f"concat_list_{int(time.time())}.txt"
    concat_list_path = os.path.join(tempfile.gettempdir(), concat_list_filename)
    temp_files_generated.append(concat_list_path)

    try:
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            f.write("ffconcat version 1.0\n")
            for item_path in concat_list_items:
                escaped_path = _escape_path_for_concat(item_path)
                f.write(f"file {escaped_path}\n")
        print(f"  Создан файл списка: {concat_list_path}")
    except IOError as e:
        raise CommandGenerationError(f"Не удалось создать файл списка concat: {e}")

    return preprocessing_commands, concat_list_path, temp_files_generated, total_embed_duration_added


def _build_banner_filter(current_video_input_label: str, banner_input_stream_label: str,
                         target_params: Dict, banner_timecodes: List[str], banner_duration: float,
                         final_duration_estimate: float, is_concat_mode: bool,
                         sorted_embed_ads: List[Dict], total_embed_duration_added: float) -> Tuple[List[str], Optional[str]]:
    """Builds the filter string parts for the banner overlay."""
    filter_parts = []
    next_video_output_label = None

    # Define labels for intermediate and final streams in this filter step
    banner_input_index = banner_input_stream_label.strip('[]').split(':')[0] # Extract index
    scaled_banner_stream = f"[banner_scaled_{banner_input_index}]"
    overlay_output_label_banner = f"[v_banner_out_{banner_input_index}]"

    # Add scaling/SAR filter part
    filter_parts.append(
        f"{banner_input_stream_label}scale=iw:-1:flags=bicubic," # Scale width proportionally
        f"setsar={target_params['sar']}{scaled_banner_stream}"
    )

    # Calculate enable expression based on output timeline
    enable_parts = []
    sorted_banner_timecodes_sec = sorted([_timecode_to_seconds(tc) for tc in banner_timecodes])

    if is_concat_mode:
        current_time_in_output = 0.0
        original_time_processed = 0.0
        ad_idx = 0
        for banner_original_sec in sorted_banner_timecodes_sec:
            # Advance output time based on ads occurring *before* this banner's original time
            while ad_idx < len(sorted_embed_ads):
                ad = sorted_embed_ads[ad_idx]
                ad_original_sec = _timecode_to_seconds(ad['timecode'])
                if ad_original_sec < banner_original_sec:
                    # Add duration of main segment before ad + ad duration
                    current_time_in_output += (ad_original_sec - original_time_processed) + ad.get('duration', 0)
                    original_time_processed = ad_original_sec
                    ad_idx += 1
                else:
                    break # Stop adding ads, this banner comes next or concurrently
            # Add duration of main segment from last ad (or start) up to this banner
            current_time_in_output += (banner_original_sec - original_time_processed)
            original_time_processed = banner_original_sec # Update position in original timeline

            start_time = current_time_in_output
            end_time = min(start_time + banner_duration, final_duration_estimate)
            if start_time < final_duration_estimate - 0.01: # Avoid adding if start time is beyond estimated end
                enable_parts.append(f"between(t,{start_time:.3f},{end_time:.3f})")
    else: # Direct conversion mode (no ads)
        for banner_original_sec in sorted_banner_timecodes_sec:
            start_time = banner_original_sec
            end_time = min(start_time + banner_duration, final_duration_estimate)
            if start_time < final_duration_estimate - 0.01:
                enable_parts.append(f"between(t,{start_time:.3f},{end_time:.3f})")

    # Add overlay filter part if enable times were generated
    if enable_parts:
        enable_expression = "+".join(enable_parts)
        # Use target height if available, otherwise rely on ffmpeg's main_h variable
        overlay_y_pos = f"{target_params['height'] if target_params['height'] else 'main_h'}-overlay_h"
        filter_parts.append(
            f"{current_video_input_label}{scaled_banner_stream}"
            f"overlay=x=0:y={overlay_y_pos}:enable='{enable_expression}'"
            f"{overlay_output_label_banner}"
        )
        next_video_output_label = overlay_output_label_banner
        print(f"    Фильтр overlay для баннера добавлен. Выход: {next_video_output_label}")
    else:
        print("Предупреждение: Не удалось создать таймкоды для баннера, фильтр не добавлен.")
        next_video_output_label = current_video_input_label # Pass through the input label

    return filter_parts, next_video_output_label


def _build_moving_logo_filter(current_video_input_label: str, moving_input_stream_label: str,
                              target_params: Dict, final_duration_estimate: float, moving_speed: float,
                              logo_relative_height: float, logo_alpha: float) -> Tuple[List[str], Optional[str]]:
    """Builds the filter string parts for the moving logo overlay."""
    filter_parts = []
    next_video_output_label = None

    print(f"  Настройка фильтра для движущейся рекламы (скорость: {moving_speed})...")
    moving_input_index = moving_input_stream_label.strip('[]').split(':')[0]
    scaled_moving_stream = f"[moving_scaled_{moving_input_index}]"
    transparent_moving_stream = f"[moving_alpha_{moving_input_index}]"
    overlay_output_label_moving = f"[v_moving_out_{moving_input_index}]"

    # --- Scaling Filter ---
    logo_target_h = (target_params['height'] or 720) * logo_relative_height
    moving_scale_filter = f"scale=-1:{logo_target_h:.0f}:flags=bicubic"
    filter_parts.append(
        f"{moving_input_stream_label}{moving_scale_filter},"
        f"setsar={target_params['sar']}{scaled_moving_stream}"
    )
    print(f"    Фильтр масштабирования движ. рекламы: {moving_scale_filter}")

    # --- Transparency Filter ---
    alpha_filter = f"colorchannelmixer=aa={logo_alpha:.2f}"
    filter_parts.append(
        f"{scaled_moving_stream}{alpha_filter}{transparent_moving_stream}"
    )
    print(f"    Фильтр прозрачности движ. рекламы: {alpha_filter}")

    # --- Overlay Filter Expressions (Animation) ---
    T_total = final_duration_estimate

    if not isinstance(moving_speed, (int, float)) or moving_speed <= 0:
        print(f"Предупреждение: Некорректное значение moving_speed ({moving_speed}). Используется значение по умолчанию 1.")
        moving_speed = 1.0

    cycle_T = T_total / moving_speed if moving_speed > 0 else T_total # Avoid division by zero
    print(f"    Общая длительность: {T_total:.3f}s, Длительность цикла ({moving_speed}x): {cycle_T:.3f}s")

    x_expr, y_expr = "'0'", "'0'" # Default static position

    if cycle_T > 0.2 and T_total > 0.2: # Only animate if cycle/video is long enough
        cycle_t1, cycle_t2, cycle_t3 = cycle_T / 4.0, cycle_T / 2.0, 3.0 * cycle_T / 4.0
        cycle_seg_dur = max(cycle_T / 4.0, 1e-6) # Avoid division by zero

        max_x = f"(main_w-overlay_w)"
        max_y = f"(main_h-overlay_h)"
        time_var = f"mod(t,{cycle_T:.6f})"

        # X coordinate expression
        seg1_x = f"{max_x}*({time_var}/{cycle_seg_dur:.6f})"
        seg2_x = f"{max_x}"
        seg3_x = f"{max_x}*(1-(({time_var}-{cycle_t2:.6f})/{cycle_seg_dur:.6f}))"
        seg4_x = "0"
        x_expr = f"'if(lt({time_var},{cycle_t1:.6f}),{seg1_x},if(lt({time_var},{cycle_t2:.6f}),{seg2_x},if(lt({time_var},{cycle_t3:.6f}),{seg3_x},{seg4_x})))'"

        # Y coordinate expression
        seg1_y = "0"
        seg2_y = f"{max_y}*(({time_var}-{cycle_t1:.6f})/{cycle_seg_dur:.6f})"
        seg3_y = f"{max_y}"
        seg4_y = f"{max_y}*(1-(({time_var}-{cycle_t3:.6f})/{cycle_seg_dur:.6f}))"
        y_expr = f"'if(lt({time_var},{cycle_t1:.6f}),{seg1_y},if(lt({time_var},{cycle_t2:.6f}),{seg2_y},if(lt({time_var},{cycle_t3:.6f}),{seg3_y},{seg4_y})))'"

        print(f"    Анимация движ. рекламы: Прямоугольный путь, {moving_speed} цикл(ов) за {T_total:.3f}s.")
    else:
        print(f"Предупреждение: Длительность цикла ({cycle_T:.3f}s) или видео мала, логотип статичен в ЛВ углу.")

    # --- Add Overlay Filter to Graph ---
    filter_parts.append(
        f"{current_video_input_label}{transparent_moving_stream}"
        f"overlay=x={x_expr}:y={y_expr}:shortest=0" # shortest=0 ensures overlay lasts full duration
        f"{overlay_output_label_moving}"
    )
    next_video_output_label = overlay_output_label_moving
    print(f"    Фильтр overlay для движ. рекламы добавлен. Выход: {next_video_output_label}")

    return filter_parts, next_video_output_label


def _build_filter_complex(base_video_specifier: str, target_params: Dict, final_duration_estimate: float,
                          is_concat_mode: bool, sorted_embed_ads: List[Dict], total_embed_duration_added: float,
                          banner_file: Optional[str], banner_timecodes: Optional[List[str]], banner_input_idx: Optional[int],
                          moving_file: Optional[str], moving_input_idx: Optional[int],
                          moving_speed: float, moving_logo_relative_height: float, moving_logo_alpha: float
                          ) -> Tuple[Optional[str], Optional[str]]:
    """Builds the complete -filter_complex string."""
    all_filter_parts = []
    last_filter_video_label = f"[{base_video_specifier}]" # Start with base video input label

    # --- Banner Filter ---
    banner_duration = 0
    if banner_file and banner_timecodes and banner_input_idx is not None:
        try:
            banner_duration_probe = get_media_duration(banner_file)
            if banner_duration_probe is None or banner_duration_probe <= 0:
                 banner_duration = 5.0 # Default for images/errors
                 print(f"Баннер (изображение/ошибка): используется {banner_duration}s.")
            else:
                 banner_duration = banner_duration_probe
                 print(f"Баннер (видео): длительность {banner_duration:.3f}s.")

            banner_input_stream_label = f"[{banner_input_idx}:v]"
            banner_filters, last_filter_video_label = _build_banner_filter(
                last_filter_video_label, banner_input_stream_label, target_params,
                banner_timecodes, banner_duration, final_duration_estimate,
                is_concat_mode, sorted_embed_ads, total_embed_duration_added
            )
            all_filter_parts.extend(banner_filters)
        except Exception as e:
             print(f"Предупреждение: Ошибка при построении фильтра баннера: {e}. Пропускается.")

    # --- Moving Logo Filter ---
    if moving_file and moving_input_idx is not None:
        try:
            moving_input_stream_label = f"[{moving_input_idx}:v]"
            logo_filters, last_filter_video_label = _build_moving_logo_filter(
                last_filter_video_label, moving_input_stream_label, target_params,
                final_duration_estimate, moving_speed,
                moving_logo_relative_height, moving_logo_alpha
            )
            all_filter_parts.extend(logo_filters)
        except Exception as e:
             print(f"Предупреждение: Ошибка при построении фильтра движ. лого: {e}. Пропускается.")


    if not all_filter_parts:
        return None, None # No complex filter needed

    # Ensure the final label doesn't contain the brackets for mapping
    final_video_output_map_label = last_filter_video_label.strip('[]') if last_filter_video_label else None

    return ";".join(all_filter_parts), final_video_output_map_label


def _generate_main_ffmpeg_command(
    input_file: str,
    output_file: str,
    encoding_params_str: str,
    target_params: Dict,
    main_video_duration: float,
    track_data: Dict,
    banner_file: Optional[str],
    banner_timecodes: Optional[List[str]],
    moving_file: Optional[str],
    # Parameters passed via constants originally, now arguments for clarity
    moving_speed: float,
    moving_logo_relative_height: float,
    moving_logo_alpha: float,
    # Results from preprocessing
    is_concat_mode: bool,
    concat_list_path: Optional[str],
    sorted_embed_ads: List[Dict], # Needed for banner time calculation if concat
    total_embed_duration_added: float
    ) -> Tuple[str, List[str]]:
    """Generates the main FFmpeg command string."""
    print("--- Фаза 3: Генерация основной команды конвертации ---")
    main_cmd_parts = ["ffmpeg", "-hwaccel", "d3d12va", "-y"]
    inputs = []
    map_commands = []
    metadata_args = []
    temp_files_for_main = [] # e.g., concat list itself

    # --- Define Inputs ---
    overlay_inputs = [] # Store paths for later indexing
    primary_input_index = 0
    subtitle_input_specifier = ""
    metadata_input_index = 0
    final_duration_estimate = 0.0

    if is_concat_mode:
        if not concat_list_path:
            raise CommandGenerationError("Ошибка: Список concat не был создан, хотя режим конкатенации активен.")
        inputs.append(f'-f concat -safe 0 -i "{concat_list_path}"') # Input 0 = concat
        temp_files_for_main.append(concat_list_path) # Mark concat list for cleanup
        inputs.append(f'-i "{input_file}"') # Input 1 = original (for subs/metadata)
        base_video_specifier = f"{primary_input_index}:v" # "0:v" from concat
        base_audio_specifier = f"{primary_input_index}:a?" # "0:a?" from concat
        subtitle_input_specifier = "1:s?" # Subtitles from original input 1
        metadata_input_index = 1 # Metadata from original input 1
        overlay_input_index_start = 2
        final_duration_estimate = main_video_duration + total_embed_duration_added
        print("Режим: Конкатенация с рекламой")
    else:
        inputs.append(f'-i "{input_file}"') # Input 0 = original
        base_video_specifier = f"{primary_input_index}:v" # "0:v" from original
        base_audio_specifier = f"{primary_input_index}:a?" # "0:a?" from original
        subtitle_input_specifier = "0:s?" # Subtitles from original input 0
        metadata_input_index = 0 # Metadata from original input 0
        overlay_input_index_start = 1
        final_duration_estimate = main_video_duration
        print("Режим: Прямая конвертация (без рекламы)")

    print(f"Расчетная финальная длительность: {final_duration_estimate:.3f}s")

    current_overlay_input_index = overlay_input_index_start
    banner_input_idx = None
    moving_input_idx = None

    # Add Banner Input
    if banner_file and banner_timecodes and os.path.exists(banner_file): # Check existence again just in case
        # Check if banner is image or video to decide on -loop
        try:
             banner_duration_probe = get_media_duration(banner_file)
             if banner_duration_probe is None or banner_duration_probe <= 0:
                 inputs.append("-loop 1") # Loop images
        except Exception:
             inputs.append("-loop 1") # Assume image on error
        inputs.append(f'-i "{banner_file}"')
        banner_input_idx = current_overlay_input_index
        overlay_inputs.append(banner_file)
        current_overlay_input_index += 1

    # Add Moving Logo Input
    if moving_file and os.path.exists(moving_file): # Check existence
        inputs.append("-loop 1") # Assume moving logo is an image/short clip to loop
        inputs.append(f'-i "{moving_file}"')
        moving_input_idx = current_overlay_input_index
        overlay_inputs.append(moving_file)
        current_overlay_input_index += 1

    main_cmd_parts.extend(inputs)

    # --- Build Filter Complex ---
    filter_complex_string, final_video_map_label = _build_filter_complex(
        base_video_specifier, target_params, final_duration_estimate,
        is_concat_mode, sorted_embed_ads, total_embed_duration_added,
        banner_file, banner_timecodes, banner_input_idx,
        moving_file, moving_input_idx,
        moving_speed, moving_logo_relative_height, moving_logo_alpha
    )

    # --- Mapping ---
    if filter_complex_string:
        main_cmd_parts.append(f'-filter_complex "{filter_complex_string}"')
        # Map the final output label from the filter complex
        if final_video_map_label:
             map_commands.append(f'-map "[{final_video_map_label}]"')
        else:
             # Fallback if filter created but label generation failed (shouldn't happen ideally)
             print("Предупреждение: Filter complex сгенерирован, но финальная метка видео не найдена, используется базовая.")
             map_commands.append(f'-map {base_video_specifier}')
        # Audio is assumed to pass through filters unchanged unless explicitly handled
        audio_map_target = f"{base_audio_specifier}" # Map audio from primary input
    else:
        # No filter complex, map directly
        map_commands.append(f'-map {base_video_specifier}')
        audio_map_target = f"{base_audio_specifier}"

    if target_params['has_audio']:
        map_commands.append(f'-map {audio_map_target}')
    else:
        print("Предупреждение: В выходном файле не будет аудиодорожки (исходный файл не имел аудио).")

    # Map subtitles
    map_commands.append(f"-map {subtitle_input_specifier}")
    if is_concat_mode:
        print("ПРЕДУПРЕЖДЕНИЕ: Субтитры (если есть) из оригинального файла могут быть рассинхронизированы с конкатенированным видео!")

    main_cmd_parts.extend(map_commands)

    # --- Metadata ---
    metadata_args.append(f'-map_metadata {metadata_input_index}')
    # Get stream info from the *original* file for metadata mapping indices
    stream_index_map = {}
    try:
        original_info = get_stream_info(input_file)
        for stream in original_info.get("streams", []):
            idx = str(stream.get('index'))
            codec_type = stream.get('codec_type', '?')[0]
            stream_index_map[idx] = codec_type
    except FfmpegError as e:
        print(f"Предупреждение: Не удалось получить информацию о потоках оригинала для применения метаданных: {e}")

    for track_id, edits in track_data.items():
        # Track ID should be "0:s:1", "0:a:0" etc. referring to *original* input's streams
        if ':' in track_id:
            try:
                input_idx_orig, stream_type_char, stream_idx_str = track_id.split(':')
                # We only care about metadata from the original file (input index 0 in direct, 1 in concat)
                # The metadata_input_index variable points to the correct input index for this
                if int(input_idx_orig) == metadata_input_index:
                     # Find the corresponding output stream index based on mapping
                     # This is complex without parsing ffmpeg output. Let's apply to *all* output streams of that type.
                     # Or better: Use the specific stream index from the original file if we know it.
                     # We fetched original indices into stream_index_map. Let's target the original index.
                     if stream_idx_str in stream_index_map and stream_index_map[stream_idx_str] == stream_type_char:
                         # Construct the metadata specifier targeting the output stream
                         # derived from the original input's stream index.
                         # Assumes simple 1-to-1 mapping for metadata purposes.
                         # Specifier format is "-metadata:s:a:0", "-metadata:s:v:0", "-metadata:s:s:0" etc.
                         # Needs careful thought based on how map works.
                         # Simpler approach: Use the input specifier format? Let's try applying directly.
                         # Output stream index determination is tricky. Applying by type might be safer.
                         # Let's target *output* stream based on type and *original* index
                         # Example: -metadata:s:a:<original_audio_index> language=eng
                         # This might not work reliably. FFmpeg docs suggest targeting output stream index.

                         # Safer bet: apply metadata based on output stream index after mapping.
                         # This requires knowing the output indices. FFmpeg assigns them sequentially based on -map.
                         # Video is usually 0, Audio 1, Subs 2...
                         # Let's apply based on type for simplicity, acknowledging potential ambiguity if multiple streams of same type exist.
                         output_stream_specifier = f"s:{stream_type_char}:{stream_idx_str}" # Try targeting original index? Risky.
                         # Let's try a simpler specifier: just by type like "s:a", "s:v". Might affect all streams of that type.
                         # output_stream_specifier = f"s:{stream_type_char}" # Affects ALL streams of this type

                         # Best simple approach: target based on original index, hoping ffmpeg understands
                         # This relies on `-map_metadata input_idx` correctly linking original stream indices.
                         # Example: if original audio 0 maps to output audio 0, this should work.
                         specifier = f"s:{stream_type_char}:{stream_idx_str}"

                         if 'title' in edits and edits['title'] is not None:
                             quoted_title = shlex.quote(str(edits['title']))
                             metadata_args.append(f"-metadata:{specifier} title={quoted_title}")
                         if 'language' in edits and edits['language'] is not None:
                              # Basic validation for 3-letter codes
                              lang_code = str(edits['language']).lower()
                              if len(lang_code) == 3 and lang_code.isalpha():
                                  metadata_args.append(f"-metadata:{specifier} language={lang_code}")
                              else:
                                  print(f"Предупреждение: Неверный код языка '{edits['language']}' для трека {track_id}, пропущен.")

            except ValueError as e:
                print(f"Предупреждение: Неверный формат track_id '{track_id}' для метаданных: {e}")
            except Exception as e: # Catch broader errors during metadata processing
                print(f"Предупреждение: Ошибка при обработке метаданных для трека {track_id}: {e}")


    main_cmd_parts.extend(metadata_args)


    # --- Encoding Parameters ---
    if encoding_params_str:
        try:
            # Split user params respecting quotes
            user_params = shlex.split(encoding_params_str)
            # Check if user specified duration, if not, add our calculated one
            has_t_flag = any(p == '-t' for p in user_params)
            main_cmd_parts.extend(user_params)
            if not has_t_flag:
                 main_cmd_parts.extend(['-t', f"{final_duration_estimate:.6f}"])

        except ValueError as e:
            raise CommandGenerationError(f"Неверный синтаксис в параметрах кодирования: {e}\nПараметры: {encoding_params_str}")
    else:
         # Add default duration if no params provided at all
         main_cmd_parts.extend(['-t', f"{final_duration_estimate:.6f}"])


    # --- Output File ---
    main_cmd_parts.append(f'"{output_file}"')

    final_main_cmd = " ".join(main_cmd_parts)

    return final_main_cmd, temp_files_for_main


# --- Main Orchestrator Function ---

def generate_ffmpeg_commands(
        input_file: str,
        output_file: str,
        encoding_params_str: str,
        main_video_params: Optional[Dict],
        main_video_duration: Optional[float],
        track_data: Dict,
        embed_ads: List[Dict],
        banner_file: Optional[str],
        banner_timecodes: Optional[List[str]],
        moving_file: Optional[str],
        # Pass constants as parameters for better testability/configurability
        moving_speed: float = _MOVING_SPEED,
        temp_video_codec: str = _TEMP_VIDEO_CODEC,
        temp_video_preset: str = _TEMP_VIDEO_PRESET,
        temp_video_crf: str = _TEMP_VIDEO_CRF,
        temp_audio_codec: str = _TEMP_AUDIO_CODEC,
        temp_audio_bitrate: str = _TEMP_AUDIO_BITRATE,
        moving_logo_relative_height: float = _MOVING_LOGO_RELATIVE_HEIGHT,
        moving_logo_alpha: float = _MOVING_LOGO_ALPHA):
    """
    Generates FFmpeg commands for conversion, handling ads via concat and overlays.

    Returns:
        tuple: (list[preprocessing_cmds], main_command, list[temp_files])
    Raises:
        CommandGenerationError: If command generation fails.
    """
    all_temp_files = []
    preprocessing_commands = []
    concat_list_path = None
    total_embed_duration_added = 0.0

    # --- Step 1: Validate Inputs & Prepare ---
    # This also sorts ads and fetches their durations
    valid_params, valid_duration, sorted_embed_ads_with_duration = _validate_and_prepare_inputs(
        input_file, output_file, main_video_params, main_video_duration, embed_ads, banner_file, moving_file
    )

    # --- Step 2: Determine Target Parameters ---
    target_params = _determine_target_parameters(valid_params)

    # --- Step 3: Generate Preprocessing Commands (if needed) ---
    is_concat_mode = bool(sorted_embed_ads_with_duration)
    if is_concat_mode:
        try:
            preprocessing_commands, concat_list_path, prep_temp_files, total_embed_duration_added = \
                _generate_preprocessing_for_concat(
                    input_file, sorted_embed_ads_with_duration, target_params, valid_duration
                )
            all_temp_files.extend(prep_temp_files)
        except CommandGenerationError as e:
            # Clean up any temp files created *before* the error during preprocessing
            _cleanup_temp_files(all_temp_files)
            raise e # Re-raise the error
    else:
        print("--- Фаза 1 и 2: Пропущены (встраиваемая реклама отсутствует) ---")

    # --- Step 4: Generate Main Command ---
    try:
        main_command, main_temp_files = _generate_main_ffmpeg_command(
            input_file=input_file,
            output_file=output_file,
            encoding_params_str=encoding_params_str,
            target_params=target_params,
            main_video_duration=valid_duration,
            track_data=track_data,
            banner_file=banner_file,
            banner_timecodes=banner_timecodes,
            moving_file=moving_file,
            moving_speed=moving_speed,
            moving_logo_relative_height=moving_logo_relative_height,
            moving_logo_alpha=moving_logo_alpha,
            is_concat_mode=is_concat_mode,
            concat_list_path=concat_list_path,
            sorted_embed_ads=sorted_embed_ads_with_duration, # Pass ads with duration for banner calc
            total_embed_duration_added=total_embed_duration_added
        )
        all_temp_files.extend(main_temp_files) # Add files like concat list
    except CommandGenerationError as e:
         # Clean up all temp files created so far if main command generation fails
        _cleanup_temp_files(all_temp_files)
        raise e # Re-raise the error

    return preprocessing_commands, main_command, all_temp_files


# --- FFmpeg Execution ---

def run_ffmpeg_command(cmd, step_name):
    """Executes a single FFmpeg command using subprocess.run and handles errors."""
    print(f"\n--- Запуск шага: {step_name} ---")
    print(f"Команда: {cmd}")

    try:
        # Use startupinfo to prevent console window flashing on Windows
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        # Using shell=True for simplicity with complex quoted commands and filters.
        # Ensure proper quoting within the command string itself.
        process = subprocess.run(cmd, shell=True, check=True, capture_output=True,
                                 text=True, encoding='utf-8', errors='replace',
                                 startupinfo=startupinfo)
        # Print stderr for progress/info, even on success
        # Limit stderr output length to avoid flooding console
        stderr_output = process.stderr
        max_stderr_lines = 50
        stderr_lines = stderr_output.splitlines()
        if len(stderr_lines) > max_stderr_lines:
             print(f"--- {step_name}: Вывод STDERR (последние {max_stderr_lines} строк) ---")
             print("\n".join(stderr_lines[-max_stderr_lines:]))
        else:
             print(f"--- {step_name}: Вывод STDERR ---")
             print(stderr_output)

        print(f"--- {step_name}: Успешно завершено ---")
        return True
    except subprocess.CalledProcessError as e:
        # Ensure full command and relevant stderr are in the exception
        stderr_tail = e.stderr[-2000:] if e.stderr else "N/A"
        raise ConversionError(
            f"Ошибка во время шага '{step_name}' (код {e.returncode}).\n\n"
            f"Команда:\n{cmd}\n\n" # Use the cmd string passed to run
            f"Stderr (конец):\n{stderr_tail}"
        ) from e # Chain the original exception
    except FileNotFoundError:
        # This error might occur if ffmpeg itself is not found by the shell
        raise ConversionError("FFmpeg не найден. Убедитесь, что он установлен и в PATH.") from None
    except Exception as e:
        # Catch other potential errors during subprocess execution
        raise ConversionError(f"Неожиданная ошибка при запуске '{step_name}': {type(e).__name__} - {e}") from e


# --- Cleanup Helper ---
def _cleanup_temp_files(temp_files: List[str]):
    """Attempts to delete temporary files."""
    print(f"\n--- Очистка временных файлов ({len(temp_files)}) ---")
    deleted_count = 0
    for f in temp_files:
        try:
            if os.path.exists(f):
                os.remove(f)
                print(f"  Удален: {f}")
                deleted_count += 1
            else:
                print(f"  Не найден (уже удален?): {f}")
        except OSError as e:
            print(f"  Ошибка удаления {f}: {e}")
    print(f"--- Очистка завершена (удалено {deleted_count}/{len(temp_files)}) ---")

# Example Usage (conceptual - replace with your actual call structure)
# if __name__ == '__main__':
#     # Dummy data for testing
#     in_file = "input.mp4"
#     out_file = "output.mp4"
#     enc_params = "-c:v libx264 -crf 23 -preset medium -c:a aac -b:a 128k -movflags +faststart"
#     # Create dummy input file for testing
#     if not os.path.exists(in_file):
#         print(f"Creating dummy input file: {in_file}")
#         # You might need ffmpeg installed to run this dummy creation part
#         try:
#             subprocess.run(f'ffmpeg -y -f lavfi -i testsrc=duration=60:size=1280x720:rate=25 -f lavfi -i sine=frequency=1000:duration=60 -vf "drawtext=text=\'MAIN VIDEO\':x=(w-text_w)/2:y=(h-text_h)/2:fontsize=50:fontcolor=white" -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "{in_file}"', shell=True, check=True, capture_output=True)
#         except Exception as e:
#             print(f"Failed to create dummy input file. Ensure ffmpeg is in PATH. Error: {e}")
#             exit(1)
#     if not os.path.exists("ad1.mp4"):
#          subprocess.run(f'ffmpeg -y -f lavfi -i testsrc=duration=5:size=1280x720:rate=25 -f lavfi -i sine=frequency=500:duration=5 -vf "drawtext=text=\'AD 1\':x=(w-text_w)/2:y=(h-text_h)/2:fontsize=50:fontcolor=yellow" -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "ad1.mp4"', shell=True, check=True, capture_output=True)
#     if not os.path.exists("banner.png"):
#         subprocess.run(f'ffmpeg -y -f lavfi -i color=c=blue:s=300x50:d=1 -vf "drawtext=text=\'BANNER\':x=(w-text_w)/2:y=(h-text_h)/2:fontsize=20:fontcolor=white" "banner.png"', shell=True, check=True, capture_output=True)
#     if not os.path.exists("moving_logo.png"):
#         subprocess.run(f'ffmpeg -y -f lavfi -i color=c=red:s=100x100:d=1 -vf "drawtext=text=\'LOGO\':x=(w-text_w)/2:y=(h-text_h)/2:fontsize=15:fontcolor=white" "moving_logo.png"', shell=True, check=True, capture_output=True)


#     main_params = get_essential_stream_params(in_file)
#     main_dur = get_media_duration(in_file)
#     ads = [{'path': 'ad1.mp4', 'timecode': '0:10'}, {'path': 'ad1.mp4', 'timecode': '0:35'}]
#     banner = "banner.png"
#     banner_tc = ["0:05", "0:25", "0:45"]
#     moving = "moving_logo.png"
#     tracks = {"0:a:0": {"language": "eng", "title": "Original Audio"}, "0:s:0": {"language": "rus"}} # Example assuming 1 audio, 1 sub

#     if main_params and main_dur:
#         temp_files = []
#         try:
#             prep_cmds, main_cmd, temp_files = generate_ffmpeg_commands(
#                 input_file=in_file,
#                 output_file=out_file,
#                 encoding_params_str=enc_params,
#                 main_video_params=main_params,
#                 main_video_duration=main_dur,
#                 track_data=tracks,
#                 embed_ads=ads,
#                 banner_file=banner,
#                 banner_timecodes=banner_tc,
#                 moving_file=moving
#             )

#             print("\n--- Generated Commands ---")
#             print("Preprocessing:")
#             for cmd in prep_cmds: print(f"  {cmd}")
#             print("\nMain Command:")
#             print(f"  {main_cmd}")
#             print("\nTemp Files to clean:")
#             for f in temp_files: print(f"  {f}")

#             # --- Execute Commands ---
#             # for i, cmd in enumerate(prep_cmds):
#             #     run_ffmpeg_command(cmd, f"Preprocessing Step {i+1}")
#             #
#             # run_ffmpeg_command(main_cmd, "Main Conversion")

#             print("\n--- Conversion Process Simulated ---")

#         except (CommandGenerationError, ConversionError, FfprobeError) as e:
#             print(f"\n--- ОШИБКА ---")
#             print(e)
#         finally:
#             # Cleanup temporary files
#             _cleanup_temp_files(temp_files)
#             # Optional: Clean dummy input files
#             # for f in [in_file, "ad1.mp4", "banner.png", "moving_logo.png"]:
#             #     if os.path.exists(f): os.remove(f)

#     else:
#         print("Не удалось получить параметры или длительность основного видео.")