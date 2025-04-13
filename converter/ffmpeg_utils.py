# converter/ffmpeg_utils.py
import json
import os
import shlex
import subprocess
import tempfile
import time

from .exceptions import FfprobeError, CommandGenerationError, ConversionError, FfmpegError

# --- Constants ---
_TEMP_VIDEO_CODEC = "h264_nvenc"  # Or libx264
_TEMP_VIDEO_PRESET = "fast"
_TEMP_VIDEO_CRF = "18"
_TEMP_AUDIO_CODEC = "aac"
_TEMP_AUDIO_BITRATE = "192k"
_MOVING_SPEED = 2
_MOVING_LOGO_RELATIVE_HEIGHT = 1 / 10
_MOVING_LOGO_ALPHA = 0.7


# --- FFprobe Utilities ---

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


# --- FFmpeg Command Generation ---

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


def generate_ffmpeg_commands_concat(
        input_file, output_file, encoding_params_str, main_video_params, main_video_duration, track_data,
        embed_ads, banner_file, banner_timecodes, moving_file, moving_speed=_MOVING_SPEED,
        temp_video_codec=_TEMP_VIDEO_CODEC, temp_video_preset=_TEMP_VIDEO_PRESET, temp_video_crf=_TEMP_VIDEO_CRF,
        temp_audio_codec=_TEMP_AUDIO_CODEC,
        temp_audio_bitrate=_TEMP_AUDIO_BITRATE, moving_logo_relative_height=_MOVING_LOGO_RELATIVE_HEIGHT,
        moving_logo_alpha=_MOVING_LOGO_ALPHA):
    """
    Generates FFmpeg commands. Uses concat demuxer IF embed_ads is present,
    otherwise generates a direct command.

    Returns:
        tuple: (list[preprocessing_cmds], main_command, list[temp_files])
    Raises:
        CommandGenerationError: If command generation fails.
    """
    preprocessing_commands = []
    temp_files_generated = []

    # --- Validations ---
    if not all([input_file, output_file, main_video_params, main_video_duration]):
        raise CommandGenerationError("Отсутствуют необходимые входные данные (файл, параметры, длительность).")
    if not os.path.exists(input_file):
        raise CommandGenerationError(f"Входной файл не найден: {input_file}")
    if main_video_duration <= 0:
        raise CommandGenerationError("Недопустимая длительность основного видео.")

    # --- Define Target Parameters (needed mainly for ads/segments) ---
    target_w = main_video_params.get('width')
    target_h = main_video_params.get('height')
    target_sar = main_video_params.get('sar', '1:1')
    target_fps_str = main_video_params.get('fps_str')
    target_pix_fmt = main_video_params.get('pix_fmt', 'yuv420p')
    target_v_timebase = main_video_params.get('time_base_v')
    target_sample_rate = main_video_params.get('sample_rate')
    target_channel_layout = main_video_params.get('channel_layout', 'stereo')
    target_sample_fmt = main_video_params.get('sample_fmt', 'fltp')
    target_a_timebase = main_video_params.get('time_base_a')
    has_audio = bool(target_sample_rate)

    # --- Sort ads (needed early to decide the logic path) ---
    def timecode_to_seconds(tc):
        try:
            m, s = map(int, tc.split(':'))
            return float(m * 60 + s)
        except:
            return 0.0

    sorted_embed_ads = sorted(embed_ads, key=lambda ad: timecode_to_seconds(ad['timecode']))

    # --- Function to create a preprocessing command (only used if ads exist) ---
    def create_segment_command(input_path, output_path, start_time=None, duration=None, is_ad=False):
        nonlocal temp_files_generated
        temp_files_generated.append(output_path)
        # Calculate video timescale, ensuring denominator is not zero
        video_timescale = "90000"  # Default value
        if target_v_timebase and '/' in target_v_timebase:
            try:
                num, den = map(float, target_v_timebase.split('/'))
                if den != 0: video_timescale = str(int(1.0 / (num / den)))
            except ValueError:
                pass  # Keep default if eval fails

        cmd_parts = [
            "ffmpeg", "-hwaccel", "d3d12va", "-y",
            *(["-ss", f"{start_time:.6f}"] if start_time is not None and start_time > 0.001 else []),
            "-i", f'"{input_path}"',
            *(["-t", f"{duration:.6f}"] if duration is not None else []),
            "-avoid_negative_ts", "make_zero",
            "-vf", f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=bicubic,"
                   f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
                   f"setsar={target_sar},"
                   f"fps={target_fps_str},"
                   f"format=pix_fmts={target_pix_fmt}",
            *(["-af", f"aresample={target_sample_rate},"
                      f"aformat=sample_fmts={target_sample_fmt}:channel_layouts={target_channel_layout}"] if has_audio else [
                "-an"]),
            "-c:v", _TEMP_VIDEO_CODEC, "-preset", _TEMP_VIDEO_PRESET, "-crf", _TEMP_VIDEO_CRF,
            "-b:v", "0",
            *(["-c:a", _TEMP_AUDIO_CODEC, "-b:a", _TEMP_AUDIO_BITRATE] if has_audio else []),
            "-video_track_timescale", video_timescale,  # Use calculated or default timescale
            f'"{output_path}"'
        ]
        safe_cmd_parts = [part for part in cmd_parts if part is not None]
        return " ".join(safe_cmd_parts)

    # ==================================================================
    # Phase 1 & 2: Preprocessing and Concat List (ONLY IF ADS ARE PRESENT)
    # ==================================================================
    concat_list_items = []
    total_embed_duration_added = 0.0
    concat_list_path = None

    if sorted_embed_ads:
        print("--- Фаза 1: Генерация команд предварительной обработки сегментов (реклама присутствует) ---")
        segment_counter = 0
        last_original_time = 0.0

        # Check compatibility params needed for concat
        if not all([target_w, target_h, target_fps_str, target_pix_fmt, target_v_timebase]):
            raise CommandGenerationError("Не удалось определить ключевые видео параметры для совместимости concat.")
        if has_audio and not all([target_sample_rate, target_channel_layout, target_sample_fmt, target_a_timebase]):
            raise CommandGenerationError("Не удалось определить ключевые аудио параметры для совместимости concat.")

        # Process unique ads first
        unique_ad_files = {ad['path']: ad for ad in embed_ads}.values()
        preprocessed_ad_paths = {}
        for ad_data in unique_ad_files:
            ad_path = ad_data['path']
            if not os.path.exists(ad_path):
                print(f"Предупреждение: Файл рекламы не найден '{ad_path}', пропускается.")
                continue
            temp_ad_path = _generate_temp_filename("ad_segment", segment_counter)
            cmd = create_segment_command(ad_path, temp_ad_path, is_ad=True)
            preprocessing_commands.append(cmd)
            preprocessed_ad_paths[ad_path] = temp_ad_path
            segment_counter += 1

        # Iterate through time, creating main segments and adding ad segments
        for i, embed in enumerate(sorted_embed_ads):
            embed_original_time_sec = timecode_to_seconds(embed['timecode'])
            embed_ad_path = embed['path']

            if embed_ad_path not in preprocessed_ad_paths:
                print(
                    f"Предупреждение: Пропуск рекламы '{os.path.basename(embed_ad_path)}' в {embed['timecode']} т.к. предварительная обработка не удалась.")
                continue

            # Main Video Segment (Before Ad)
            main_segment_duration = embed_original_time_sec - last_original_time
            if main_segment_duration > 0.01:
                temp_main_path = _generate_temp_filename("main_segment", segment_counter)
                cmd = create_segment_command(input_file, temp_main_path,
                                             start_time=last_original_time,
                                             duration=main_segment_duration)
                preprocessing_commands.append(cmd)
                concat_list_items.append(temp_main_path)
                segment_counter += 1
            elif main_segment_duration < -0.01:
                print(
                    f"Предупреждение: Отрицательная или нулевая длительность ({main_segment_duration:.3f}s) сегмента основного видео перед {embed['timecode']}, проверьте таймкоды.")

            # Add the Pre-processed Ad Segment
            preprocessed_ad_path = preprocessed_ad_paths[embed_ad_path]
            concat_list_items.append(preprocessed_ad_path)
            total_embed_duration_added += embed.get('duration', 0)

            # Update time for next main segment start
            last_original_time = embed_original_time_sec

        # Final Main Video Segment (After Last Ad)
        if last_original_time < main_video_duration - 0.01:
            final_segment_duration = main_video_duration - last_original_time
            temp_main_path = _generate_temp_filename("main_segment", segment_counter)
            cmd = create_segment_command(input_file, temp_main_path,
                                         start_time=last_original_time,
                                         duration=final_segment_duration)
            preprocessing_commands.append(cmd)
            concat_list_items.append(temp_main_path)
            segment_counter += 1

        # --- Phase 2: Create Concat List File ---
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

    else:  # Case WITHOUT Ads
        print("--- Фаза 1 и 2: Пропущены (встраиваемая реклама отсутствует) ---")

    # ==================================================================
    # Phase 3: Generate Main Command (Handles both cases: with/without ads)
    # ==================================================================
    print("--- Фаза 3: Генерация основной команды конвертации ---")
    main_cmd_parts = ["ffmpeg", "-hwaccel", "d3d12va", "-y"]
    inputs = []
    filter_complex_parts = []
    map_commands = []
    metadata_args = []

    # Define Inputs and Base Stream Specifiers
    primary_input_index = 0
    if sorted_embed_ads:
        if not concat_list_path:
            raise CommandGenerationError("Ошибка: Список concat не был создан, хотя реклама присутствует.")
        inputs.append(f'-f concat -safe 0 -i "{concat_list_path}"')  # Input 0 = concat
        inputs.append(f'-i "{input_file}"')  # Input 1 = original
        base_video_specifier = f"{primary_input_index}:v"  # "0:v"
        base_audio_specifier = f"{primary_input_index}:a?"  # "0:a?"
        subtitle_input_specifier = "1:s?"
        metadata_input_index = 1
        overlay_input_index_start = 2
        print("Режим: Конкатенация с рекламой")
    else:
        inputs.append(f'-i "{input_file}"')  # Input 0 = original
        base_video_specifier = f"{primary_input_index}:v"  # "0:v"
        base_audio_specifier = f"{primary_input_index}:a?"  # "0:a?"
        subtitle_input_specifier = "0:s?"
        metadata_input_index = 0
        overlay_input_index_start = 1
        print("Режим: Прямая конвертация (без рекламы)")

    current_overlay_input_index = overlay_input_index_start

    # --- Overlay Inputs (Banner, Moving) ---
    banner_input_stream = None
    banner_duration = 0
    banner_is_video = False
    if banner_file and banner_timecodes:
        if os.path.exists(banner_file):
            try:
                banner_duration_probe = get_media_duration(banner_file)
                if banner_duration_probe is None or banner_duration_probe <= 0:
                    inputs.append("-loop 1")
                    banner_duration = 5.0  # Default display time
                    print(f"Баннер (изображение/ошибка): используется {banner_duration}s.")
                else:
                    banner_is_video = True
                    banner_duration = banner_duration_probe
                    print(f"Баннер (видео): длительность {banner_duration:.3f}s.")

                inputs.append(f'-i "{banner_file}"')
                banner_input_index = current_overlay_input_index
                banner_input_stream = f"[{banner_input_index}:v]"
                current_overlay_input_index += 1
            except Exception as e:
                print(f"Предупреждение: Ошибка при обработке файла баннера '{banner_file}': {e}. Пропускается.")
                banner_file = None
        else:
            print(f"Предупреждение: Файл баннера не найден '{banner_file}', пропускается.")
            banner_file = None

    moving_input_stream = None
    if moving_file:
        if os.path.exists(moving_file):
            try:
                inputs.append("-loop 1")
                inputs.append(f'-i "{moving_file}"')
                moving_input_index = current_overlay_input_index
                moving_input_stream = f"[{moving_input_index}:v]"
                current_overlay_input_index += 1
            except Exception as e:
                print(f"Предупреждение: Ошибка при обработке файла движ. рекламы '{moving_file}': {e}. Пропускается.")
                moving_file = None
        else:
            print(f"Предупреждение: Файл движущейся рекламы не найден '{moving_file}', пропускается.")
            moving_file = None

    # --- Calculate Final Output Duration ---
    if sorted_embed_ads:
        final_duration_estimate = main_video_duration + total_embed_duration_added
    else:
        final_duration_estimate = main_video_duration
    print(f"Расчетная финальная длительность: {final_duration_estimate:.3f}s")

    # --- Build Filter Complex for Overlays ---
    last_filter_label = None
    current_video_input_for_filter = f"[{base_video_specifier}]"  # Input for the first filter

    # Banner Overlay Filter
    if banner_file and banner_timecodes and banner_input_stream:
        # Define labels for intermediate and final streams in this filter step
        scaled_banner_stream = f"[banner_scaled_{banner_input_index}]"  # Unique label
        overlay_output_label_banner = f"[v_banner_out_{banner_input_index}]"  # Unique label

        # Add scaling/SAR filter part
        filter_complex_parts.append(
            f"{banner_input_stream}scale=iw:-1:flags=bicubic,"  # Example scale
            f"setsar={target_sar if target_sar else '1/1'}{scaled_banner_stream}"
        )

        # Calculate enable expression
        enable_parts = []
        sorted_banner_timecodes_sec = sorted([timecode_to_seconds(tc) for tc in banner_timecodes])
        if sorted_embed_ads:
            current_time_in_output = 0.0
            original_time_processed = 0.0
            ad_idx = 0
            for banner_original_sec in sorted_banner_timecodes_sec:
                while ad_idx < len(sorted_embed_ads):
                    ad = sorted_embed_ads[ad_idx]
                    ad_original_sec = timecode_to_seconds(ad['timecode'])
                    if ad_original_sec < banner_original_sec:
                        current_time_in_output += (ad_original_sec - original_time_processed) + ad.get('duration', 0)
                        original_time_processed = ad_original_sec
                        ad_idx += 1
                    else:
                        break
                current_time_in_output += (banner_original_sec - original_time_processed)
                original_time_processed = banner_original_sec
                start_time = current_time_in_output
                end_time = min(start_time + banner_duration, final_duration_estimate)
                if start_time < final_duration_estimate - 0.01: enable_parts.append(
                    f"between(t,{start_time:.3f},{end_time:.3f})")
        else:
            for banner_original_sec in sorted_banner_timecodes_sec:
                start_time = banner_original_sec
                end_time = min(start_time + banner_duration, final_duration_estimate)
                if start_time < final_duration_estimate - 0.01: enable_parts.append(
                    f"between(t,{start_time:.3f},{end_time:.3f})")

        # Add overlay filter part if enable times were generated
        if enable_parts:
            enable_expression = "+".join(enable_parts)
            overlay_y_pos = f"{target_h if target_h else 'main_h'}-overlay_h"  # Use target_h or main_h
            filter_complex_parts.append(
                f"{current_video_input_for_filter}{scaled_banner_stream}"
                f"overlay=x=0:y={overlay_y_pos}:enable='{enable_expression}'"
                f"{overlay_output_label_banner}"
            )
            current_video_input_for_filter = overlay_output_label_banner  # Update input for the next filter
            last_filter_label = overlay_output_label_banner
        else:
            print("Предупреждение: Не удалось создать таймкоды для баннера, фильтр не добавлен.")

    # Moving Ad Overlay Filter (Rectangular Path - Top-Left Start, Linear, Transparent)
    if moving_file and moving_input_stream:
        print(f"  Настройка фильтра для движущейся рекламы (скорость: {moving_speed})...")
        scaled_moving_stream = f"[moving_scaled_{moving_input_index}]"
        transparent_moving_stream = f"[moving_alpha_{moving_input_index}]"  # New label for alpha step
        overlay_output_label_moving = f"[v_moving_out_{moving_input_index}]"

        # --- Scaling Filter ---
        # Calculate target height based on the main video height and the constant
        logo_target_h = (target_h or 720) * _MOVING_LOGO_RELATIVE_HEIGHT
        moving_scale_filter = f"scale=-1:{logo_target_h:.0f}:flags=bicubic"
        filter_complex_parts.append(
            f"{moving_input_stream}{moving_scale_filter},"
            f"setsar={target_sar if target_sar else '1/1'}{scaled_moving_stream}"
        )
        print(f"    Фильтр масштабирования движ. рекламы: {moving_scale_filter}")

        # --- Transparency Filter ---
        # Apply alpha using colorchannelmixer. Assumes input might not have alpha channel.
        # Format: colorchannelmixer=aa=ALPHA_VALUE
        alpha_filter = f"colorchannelmixer=aa={_MOVING_LOGO_ALPHA:.2f}"
        filter_complex_parts.append(
            f"{scaled_moving_stream}{alpha_filter}{transparent_moving_stream}"  # Apply alpha to scaled stream
        )
        print(f"    Фильтр прозрачности движ. рекламы: {alpha_filter}")

        # --- Overlay Filter Expressions ---
        T_total = final_duration_estimate  # Total duration of the output video

        # Ensure moving_speed is valid
        if not isinstance(moving_speed, (int, float)) or moving_speed <= 0:
            print(
                f"Предупреждение: Некорректное значение moving_speed ({moving_speed}). Используется значение по умолчанию 1.")
            moving_speed = 1.0

        # Calculate the duration of a single cycle
        cycle_T = T_total / moving_speed
        print(f"    Общая длительность: {T_total:.3f}s, Длительность цикла ({moving_speed}x): {cycle_T:.3f}s")

        x_expr = "'0'"
        y_expr = "'0'"

        # Only animate if the cycle duration is reasonably long
        if cycle_T > 0.2:
            # Time points *within a single cycle*
            cycle_t1, cycle_t2, cycle_t3 = cycle_T / 4.0, cycle_T / 2.0, 3.0 * cycle_T / 4.0
            # Duration of each segment *within a single cycle*
            cycle_seg_dur = max(cycle_T / 4.0, 1e-6)  # Avoid division by zero if cycle_T is tiny

            max_x = f"(main_w-overlay_w)"
            max_y = f"(main_h-overlay_h)"

            # Use 'tc = mod(t, cycle_T)' for time within the current cycle
            time_var = f"mod(t,{cycle_T:.6f})"

            # X coordinate expression based on time within the cycle (tc)
            seg1_x = f"{max_x}*({time_var}/{cycle_seg_dur:.6f})"
            seg2_x = f"{max_x}"
            seg3_x = f"{max_x}*(1-(({time_var}-{cycle_t2:.6f})/{cycle_seg_dur:.6f}))"
            seg4_x = "0"
            x_expr = f"'if(lt({time_var},{cycle_t1:.6f}),{seg1_x},if(lt({time_var},{cycle_t2:.6f}),{seg2_x},if(lt({time_var},{cycle_t3:.6f}),{seg3_x},{seg4_x})))'"

            # Y coordinate expression based on time within the cycle (tc)
            seg1_y = "0"
            seg2_y = f"{max_y}*(({time_var}-{cycle_t1:.6f})/{cycle_seg_dur:.6f})"
            seg3_y = f"{max_y}"
            seg4_y = f"{max_y}*(1-(({time_var}-{cycle_t3:.6f})/{cycle_seg_dur:.6f}))"
            y_expr = f"'if(lt({time_var},{cycle_t1:.6f}),{seg1_y},if(lt({time_var},{cycle_t2:.6f}),{seg2_y},if(lt({time_var},{cycle_t3:.6f}),{seg3_y},{seg4_y})))'"

            print(f"    Анимация движ. рекламы: Прямоугольный путь, {moving_speed} цикл(ов) за {T_total:.3f}s.")
        else:
            # If total duration or cycle duration is too short, keep logo static
            print(f"Предупреждение: Длительность цикла ({cycle_T:.3f}s) или видео мала, логотип статичен в ЛВ углу.")
            x_expr = "'0'"
            y_expr = "'0'"

        # --- Add Overlay Filter to Graph ---
        base_video_input_label = current_video_input_for_filter

        filter_complex_parts.append(
            # Input video and the NOW TRANSPARENT logo stream
            f"{base_video_input_label}{transparent_moving_stream}"
            f"overlay=x={x_expr}:y={y_expr}:shortest=0"  # Overlay the transparent logo
            f"{overlay_output_label_moving}"  # Output label
        )
        # Update state for next filter
        current_video_input_for_filter = overlay_output_label_moving
        last_filter_label = overlay_output_label_moving
        print(f"    Фильтр overlay для движ. рекламы добавлен. Выход: {last_filter_label}")

    # --- Finalize Main Command ---
    main_cmd_parts.extend(inputs)

    if filter_complex_parts:
        # Ensure base audio stream is referenced if not used by filters
        # Here, we assume audio passes through unaffected by video filters
        # A more complex audio graph might need separate handling
        filter_complex_string = ";".join(filter_complex_parts)
        main_cmd_parts.append(f'-filter_complex "{filter_complex_string}"')
        video_map_target = f'"{last_filter_label}"'  # Map the final video label from the filter
        audio_map_target = f'"{base_audio_specifier}"'  # Map original audio unless filtered
    else:
        video_map_target = f'"{base_video_specifier}"'  # Map base video directly
        audio_map_target = f'"{base_audio_specifier}"'  # Map base audio directly

    # --- Mapping ---
    map_commands.append(f'-map {video_map_target}')
    if has_audio:
        map_commands.append(f'-map {audio_map_target}')
    else:
        print("Предупреждение: В выходном файле не будет аудиодорожки (исходный файл не имел аудио).")

    map_commands.append(f"-map {subtitle_input_specifier}")
    if sorted_embed_ads:
        print("ПРЕДУПРЕЖДЕНИЕ: Субтитры (если есть) из оригинального файла могут быть рассинхронизированы!")

    main_cmd_parts.extend(map_commands)

    # --- Metadata ---
    metadata_args.append(f'-map_metadata {metadata_input_index}')
    stream_index_map = {}
    try:
        original_info = get_stream_info(input_file)
        for stream in original_info.get("streams", []):
            idx = str(stream.get('index'))
            codec_type = stream.get('codec_type', '?')[0]
            stream_index_map[idx] = codec_type
    except FfmpegError:
        print("Предупреждение: Не удалось получить информацию о потоках оригинала для применения метаданных.")

    for track_path, edits in track_data.items():
        if ':' in track_path:
            try:
                input_idx_orig, stream_idx_str = track_path.split(':')
                if int(input_idx_orig) == 0:
                    stream_type_char = stream_index_map.get(stream_idx_str, '?')
                    if stream_type_char != '?':
                        specifier = f"s:{stream_type_char}:{stream_idx_str}"
                        if 'title' in edits:
                            quoted_title = shlex.quote(edits['title'])
                            metadata_args.append(f"-metadata:{specifier} title={quoted_title}")
                        if 'language' in edits:
                            metadata_args.append(f"-metadata:{specifier} language={edits['language']}")
            except ValueError:
                pass
    main_cmd_parts.extend(metadata_args)

    # --- Encoding Parameters ---
    if encoding_params_str:
        try:
            main_cmd_parts.extend(shlex.split(encoding_params_str))
            if ' -t ' not in encoding_params_str:
                main_cmd_parts.extend(['-t', str(final_duration_estimate)])
        except ValueError as e:
            raise CommandGenerationError(f"Неверный синтаксис в параметрах кодирования: {e}")

    # --- Output File ---
    main_cmd_parts.append(f'"{output_file}"')

    final_main_cmd = " ".join(main_cmd_parts)

    return preprocessing_commands, final_main_cmd, temp_files_generated


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

        # Using shell=True for simplicity with complex quoted commands.
        process = subprocess.run(cmd, shell=True, check=True, capture_output=True,
                                 text=True, encoding='utf-8', errors='replace',
                                 startupinfo=startupinfo)
        # Print stderr for progress/info, even on success
        print(f"--- {step_name}: Вывод STDERR ---")
        print(process.stderr)
        print(f"--- {step_name}: Успешно завершено ---")
        return True
    except subprocess.CalledProcessError as e:
        raise ConversionError(
            f"Ошибка во время шага '{step_name}' (код {e.returncode}).\n\n"
            f"Команда:\n{e.cmd}\n\n"
            f"Stderr:\n{e.stderr[-2000:]}"
        )
    except FileNotFoundError:
        raise ConversionError("FFmpeg не найден. Убедитесь, что он установлен и в PATH.")
    except Exception as e:
        raise ConversionError(f"Неожиданная ошибка при запуске '{step_name}': {e}")
