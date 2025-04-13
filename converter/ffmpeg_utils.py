# converter/ffmpeg_utils.py
import json
import os
import shlex
import subprocess
import tempfile
import time
from typing import List, Tuple, Dict, Any, Optional

from . import config
from .exceptions import FfprobeError, CommandGenerationError, ConversionError, FfmpegError


# --- FFprobe Utilities ---

class FFMPEG:
    def __init__(self, video_codec=None,
                 video_preset=None,
                 video_cq=None,
                 video_bitrate=None,
                 audio_codec=None,
                 audio_bitrate=None,
                 video_fps=None,
                 moving_speed=None,
                 moving_logo_relative_height=None,
                 moving_logo_alpha=None,
                 banner_track_pix_fmt=None,
                 banner_gap_color=None,
                 hwaccel=None):
        self.video_codec = video_codec if video_codec is not None else config.VIDEO_CODEC
        self.video_preset = video_preset if video_preset is not None else config.VIDEO_PRESET
        self.video_cq = video_cq if video_cq else config.VIDEO_CQ
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
                "ffprobe не найден. Убедитесь, что FFmpeg (включая ffprobe) установлен и добавлен в системный PATH.")
        except subprocess.CalledProcessError as e:
            stderr_tail = e.stderr[-1000:] if e.stderr else "N/A"
            raise FfprobeError(
                f"Ошибка выполнения ffprobe: {e}\nКоманда: {' '.join(command)}\nВывод stderr (конец): {stderr_tail}")
        except json.JSONDecodeError as e:
            stdout_content = getattr(e, 'doc', "N/A")[:500]
            raise FfprobeError(
                f"Ошибка декодирования вывода ffprobe: {e}\nКоманда: {' '.join(command)}\nНачало вывода stdout: {stdout_content}")
        except Exception as e:
            raise FfprobeError(f"Неожиданная ошибка при выполнении ffprobe: {e}\nКоманда: {' '.join(command)}")

    def get_media_duration(self, file_path: str) -> Optional[float]:
        """Gets media duration using ffprobe. Returns None for images/errors/very short clips."""
        if not file_path or not os.path.exists(file_path):
            return None
        duration = None
        try:
            command_fmt = ["ffprobe", "-v", "quiet", "-i", file_path,
                           "-show_entries", "format=duration",
                           "-print_format", "json"]
            output_fmt = self.run_ffprobe(command_fmt)
            duration_str_fmt = output_fmt.get("format", {}).get("duration")
            if duration_str_fmt and duration_str_fmt != "N/A":
                try:
                    duration = float(duration_str_fmt)
                except (ValueError, TypeError):
                    pass

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
                                if stream_duration > 0: duration = stream_duration
                            except (ValueError, TypeError):
                                pass
                except FfprobeError:
                    pass

            if duration and duration > 0.01:
                return duration
            else:
                return None

        except FfprobeError:
            return None
        except Exception as e:
            print(f"Неожиданная ошибка в get_media_duration для {file_path}: {e}")
            return None

    def get_stream_info(self, file_path: str) -> Dict[str, Any]:
        """Gets info about all streams using ffprobe."""
        if not file_path or not os.path.exists(file_path): return {}
        command = ["ffprobe", "-v", "quiet", "-i", file_path,
                   "-show_streams", "-show_format", "-print_format", "json"]
        try:
            return self.run_ffprobe(command)
        except FfprobeError:
            return {}

    def get_essential_stream_params(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Gets key video and audio parameters needed for compatibility checks using ffprobe."""
        params = {
            'width': None, 'height': None, 'pix_fmt': None, 'sar': '1:1', 'par': None, 'time_base_v': None,
            'fps_str': None,
            'sample_rate': None, 'channel_layout': None, 'sample_fmt': None, 'time_base_a': None, 'has_audio': False
        }
        if not file_path or not os.path.exists(file_path): return None

        has_video_stream = False
        try:
            cmd_video = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries",
                         "stream=width,height,pix_fmt,sample_aspect_ratio,display_aspect_ratio,r_frame_rate,time_base,codec_name",
                         "-of", "json", file_path]
            data_v = self.run_ffprobe(cmd_video)
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
        except FfprobeError:
            pass
        except Exception as e:
            print(f"Неожиданная ошибка при зондировании видеопотока {file_path}: {e}")

        if not has_video_stream:
            try:
                cmd_format = ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "json", file_path]
                data_fmt = self.run_ffprobe(cmd_format)
                format_name = data_fmt.get("format", {}).get("format_name", "")
                image_formats = ['image2', 'png_pipe', 'mjpeg', 'webp_pipe', 'gif', 'tiff_pipe', 'bmp_pipe',
                                 'jpeg_pipe',
                                 'ppm_pipe', 'pgm_pipe', 'pbm_pipe']
                if any(fmt in format_name for fmt in image_formats):
                    cmd_img_stream = ["ffprobe", "-v", "error", "-select_streams", "0",
                                      "-show_entries", "stream=width,height,pix_fmt,codec_type", "-of", "json",
                                      file_path]
                    data_img_s = self.run_ffprobe(cmd_img_stream)
                    if data_img_s.get("streams"):
                        stream_img = data_img_s["streams"][0]
                        params['width'] = stream_img.get('width')
                        params['height'] = stream_img.get('height')
                        params['pix_fmt'] = stream_img.get('pix_fmt', 'rgb24')
                        params['fps_str'] = '25/1'
                        params['time_base_v'] = '1/25'
                        params['sar'] = '1:1'
                        print(f"Информация: {os.path.basename(file_path)} распознан как изображение ({format_name}).")
            except FfprobeError:
                pass
            except Exception as e:
                print(f"Неожиданная ошибка при обработке файла без видеопотока {file_path}: {e}")

        if not all([params['width'], params['height'], params['fps_str']]):
            print(
                f"Критическая ошибка: Не удалось определить основные видеопараметры (ширина/высота/fps) для {file_path}.")
            return None

        try:
            cmd_audio = ["ffprobe", "-v", "error", "-select_streams", "a:0",
                         "-show_entries", "stream=sample_rate,channel_layout,sample_fmt,time_base", "-of", "json",
                         file_path]
            data_a = self.run_ffprobe(cmd_audio)
            if data_a.get("streams"):
                stream_a = data_a["streams"][0]
                params['sample_rate'] = stream_a.get('sample_rate')
                params['channel_layout'] = stream_a.get('channel_layout')
                params['sample_fmt'] = stream_a.get('sample_fmt')
                params['time_base_a'] = stream_a.get('time_base')
                if all([params['sample_rate'], params['channel_layout'], params['sample_fmt'], params['time_base_a']]):
                    params['has_audio'] = True
                else:
                    params['has_audio'] = False
        except FfprobeError:
            pass
        except Exception as e:
            print(f"Неожиданная ошибка при зондировании аудиопотока {file_path}: {e}")
            params['has_audio'] = False

        common_pix_fmts = ['yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p', 'yuv444p', 'yuvj444p', 'nv12', 'nv21',
                           'yuva420p',
                           'rgba', 'bgra', 'rgb24', 'gray']
        if params['pix_fmt'] not in common_pix_fmts: params['pix_fmt'] = 'yuv420p'
        if ':' not in params['sar'] or len(params['sar'].split(':')) != 2: params['sar'] = '1:1'
        if params['has_audio']:
            if not params['channel_layout']: params['channel_layout'] = 'stereo'
            if not params['sample_fmt']: params['sample_fmt'] = 'fltp'

        return params

    # --- FFmpeg Command Generation Helpers ---

    @staticmethod
    def _generate_temp_filename(prefix: str, index: int, extension: str = "mkv") -> str:
        """Generates a unique temporary filename with specified extension."""
        temp_dir = tempfile.gettempdir()
        timestamp = int(time.time() * 1000)
        filename = f"{prefix}_{index}_{timestamp}.{extension}"
        return os.path.join(temp_dir, filename)

    @staticmethod
    def _escape_path_for_concat(path: str) -> str:
        """ Prepares a path for the concat demuxer file list. """
        path = path.replace('\\', '/')
        path = path.replace("'", "'\\''")
        return f"'{path}'"

    @staticmethod
    def _timecode_to_seconds(tc: str) -> Optional[float]:
        """Converts MM:SS or HH:MM:SS timecode string to seconds. Returns None on error."""
        try:
            parts = list(map(float, tc.strip().split(':')))
            if len(parts) == 2:
                seconds = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
            else:
                return None
            return max(0.0, seconds)
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _calculate_adjusted_times(original_time_sec: float, is_concat_mode: bool,
                                  sorted_embed_ads: List[Dict]) -> float:
        """Calculates the time in the final output considering ad insertions."""
        if not is_concat_mode or not sorted_embed_ads: return original_time_sec
        adjusted_time = 0.0
        last_original_time = 0.0
        for ad in sorted_embed_ads:
            if ad['time_sec'] <= original_time_sec:
                adjusted_time += (ad['time_sec'] - last_original_time) + ad['duration']
                last_original_time = ad['time_sec']
            else:
                break
        adjusted_time += (original_time_sec - last_original_time)
        return adjusted_time

    def _validate_and_prepare_inputs(self, input_file: str, output_file: str, main_video_params: Optional[Dict],
                                     main_video_duration: Optional[float], embed_ads: List[Dict],
                                     banner_file: Optional[str], banner_timecodes: Optional[List[str]],
                                     moving_file: Optional[str]
                                     ) -> Tuple[
        Dict, float, List[Dict], Optional[str], Optional[List[str]], Optional[str], Optional[float]]:
        """Validates inputs, prepares basic structures, sorts ads, gets ad durations, validates banner timecodes. Returns original banner duration if valid."""
        if not input_file or not output_file: raise CommandGenerationError("Не указаны входной или выходной файл.")
        if not os.path.exists(input_file): raise CommandGenerationError(f"Входной файл не найден: {input_file}")
        if not main_video_params: raise CommandGenerationError("Отсутствуют необходимые параметры основного видео.")
        if main_video_duration is None or main_video_duration <= 0.01:
            print("Предупреждение: Длительность основного видео не предоставлена или некорректна, пытаемся получить...")
            main_video_duration = self.get_media_duration(input_file)
            if main_video_duration is None or main_video_duration <= 0.01:
                raise CommandGenerationError(
                    f"Не удалось определить допустимую длительность для основного видео: {input_file}")
            print(f"  Полученная длительность: {main_video_duration:.3f}s")

        valid_banner_file = None
        valid_banner_timecodes = None
        original_banner_duration = None
        if banner_file and banner_timecodes:
            if os.path.exists(banner_file):
                original_banner_duration = self.get_media_duration(banner_file)
                if original_banner_duration is None:
                    original_banner_duration = 5.0
                    print(
                        f"Предупреждение: Не удалось определить длительность баннера '{os.path.basename(banner_file)}', используется {original_banner_duration:.1f}s (возможно, изображение).")

                parsed_timecodes_sec = [self._timecode_to_seconds(tc) for tc in banner_timecodes]
                if any(t is None for t in parsed_timecodes_sec):
                    print("Предупреждение: Обнаружены неверные таймкоды баннера. Баннер будет проигнорирован.")
                    original_banner_duration = None
                else:
                    original_tc_map = {sec: tc for tc, sec in zip(banner_timecodes, parsed_timecodes_sec) if
                                       sec is not None and sec < main_video_duration}
                    valid_seconds = sorted(original_tc_map.keys())
                    if not valid_seconds:
                        print(
                            "Предупреждение: Все таймкоды баннера некорректны или превышают длительность видео. Баннер будет проигнорирован.")
                        original_banner_duration = None
                    else:
                        if len(valid_seconds) < len([t for t in parsed_timecodes_sec if t is not None]):
                            print(
                                f"Предупреждение: Некоторые таймкоды баннера превышают длительность основного видео и будут проигнорированы.")
                        valid_banner_file = banner_file
                        valid_banner_timecodes = [original_tc_map[sec] for sec in valid_seconds]
            else:
                print(f"Предупреждение: Файл баннера не найден '{banner_file}', будет проигнорирован.")

        valid_moving_file = None
        if moving_file:
            if os.path.exists(moving_file):
                valid_moving_file = moving_file
            else:
                print(f"Предупреждение: Файл движ. рекламы не найден '{moving_file}', будет проигнорирован.")

        ads_with_time = []
        for ad in embed_ads:
            timecode_str, ad_path = ad.get('timecode'), ad.get('path')
            if not timecode_str or not ad_path: continue
            time_sec = self._timecode_to_seconds(timecode_str)
            if time_sec is None or time_sec >= main_video_duration: continue
            if not os.path.exists(ad_path): continue
            ads_with_time.append({'data': ad, 'time_sec': time_sec, 'path': ad_path})

        sorted_ads_data = sorted(ads_with_time, key=lambda x: x['time_sec'])

        ads_with_info = []
        total_valid_ad_duration = 0.0
        for ad_entry in sorted_ads_data:
            ad_path, ad_timecode = ad_entry['path'], ad_entry['data']['timecode']
            ad_duration = self.get_media_duration(ad_path)
            if ad_duration is None or ad_duration <= 0.01:
                ad_duration = 5.0
                print(
                    f"Предупреждение: Не удалось определить длительность для рекламы '{os.path.basename(ad_path)}' ({ad_timecode}). Используется {ad_duration:.1f}s.")
            ad_params = self.get_essential_stream_params(ad_path)
            if ad_params is None or ad_params.get('width') is None: continue

            ads_with_info.append(
                {'path': ad_path, 'timecode': ad_timecode, 'time_sec': ad_entry['time_sec'], 'duration': ad_duration,
                 'params': ad_params})
            total_valid_ad_duration += ad_duration

        print(
            f"Подготовлено {len(ads_with_info)} допустимых рекламных вставок. Общая добавленная длительность: {total_valid_ad_duration:.3f}s")
        return main_video_params, main_video_duration, ads_with_info, valid_banner_file, valid_banner_timecodes, valid_moving_file, original_banner_duration

    @staticmethod
    def _determine_target_parameters(main_video_params: Dict) -> Dict[str, Any]:
        """Determines target parameters, ensuring critical values are present."""
        target_params = {
            'width': main_video_params.get('width'), 'height': main_video_params.get('height'),
            'sar': main_video_params.get('sar', '1:1'), 'fps_str': main_video_params.get('fps_str'),
            'pix_fmt': main_video_params.get('pix_fmt', 'yuv420p'), 'v_timebase': main_video_params.get('time_base_v'),
            'sample_rate': main_video_params.get('sample_rate'),
            'channel_layout': main_video_params.get('channel_layout', 'stereo'),
            'sample_fmt': main_video_params.get('sample_fmt', 'fltp'),
            'a_timebase': main_video_params.get('time_base_a'),
            'has_audio': main_video_params.get('has_audio', False), 'video_timescale': "90000"}

        if not all([target_params['width'], target_params['height'], target_params['fps_str'], target_params['pix_fmt'],
                    target_params['v_timebase'], target_params['sar']]):
            missing_v = [k for k, v in target_params.items() if
                         k in ['width', 'height', 'fps_str', 'pix_fmt', 'v_timebase', 'sar'] and not v]
            raise CommandGenerationError(
                f"Не удалось определить ключевые видео параметры для совместимости: {missing_v}")

        if target_params['v_timebase'] and '/' in target_params['v_timebase']:
            try:
                num, den = map(float, target_params['v_timebase'].split('/'))
                if den != 0 and num != 0:
                    timescale = int(round(1.0 / (num / den)))
                    if 1000 < timescale < 1000000: target_params['video_timescale'] = str(timescale)
            except ValueError:
                pass

        if target_params['has_audio'] and not all(
                [target_params['sample_rate'], target_params['channel_layout'], target_params['sample_fmt'],
                 target_params['a_timebase']]):
            missing_a = [k for k, v in target_params.items() if
                         k in ['sample_rate', 'channel_layout', 'sample_fmt', 'a_timebase'] and not v]
            print(
                f"Предупреждение: Не удалось определить ключевые аудио параметры ({missing_a}). Аудиодорожка будет игнорироваться.")
            target_params['has_audio'] = False
            target_params['sample_rate'] = None
            target_params['channel_layout'] = None
            target_params['sample_fmt'] = None
            target_params['a_timebase'] = None

        print(
            f"Определены целевые параметры: Res={target_params['width']}x{target_params['height']}, FPS={target_params['fps_str']}, PixFmt={target_params['pix_fmt']}, SAR={target_params['sar']}, Audio={target_params['has_audio']}")
        if target_params['has_audio']: print(
            f"  Аудио: Rate={target_params['sample_rate']}, Layout={target_params['channel_layout']}, Fmt={target_params['sample_fmt']}")
        return target_params

    def _create_segment_command(self, input_path: str, output_path: str, target_params: Dict,
                                start_time: Optional[float] = None, duration: Optional[float] = None,
                                output_pix_fmt: Optional[str] = None,
                                output_audio: bool = True,
                                force_fps: bool = True,
                                is_banner_segment: bool = False) -> str:
        """ Helper function to create a single segment transcoding/generation command. """
        sar_value = target_params['sar'].replace(':', '/') if ':' in target_params['sar'] else '1/1'
        final_pix_fmt = output_pix_fmt if output_pix_fmt else target_params['pix_fmt']
        vf_parts = []

        if is_banner_segment:
            banner_params = self.get_essential_stream_params(input_path)
            target_w = target_params['width']
            scaled_h = target_params['height'] // 10
            if banner_params and banner_params.get('width') and banner_params.get('height'):
                orig_w, orig_h = banner_params['width'], banner_params['height']
                scaled_h = max(1, int(orig_h * (target_w / orig_w))) if orig_w > 0 else scaled_h
            vf_parts.extend([
                f"scale={target_w}:{scaled_h}:flags=bicubic",
                f"setsar=sar={sar_value}"
            ])
            final_pix_fmt = self.banner_track_pix_fmt
        else:
            vf_parts.extend([
                f"scale={target_params['width']}:{target_params['height']}:force_original_aspect_ratio=decrease:flags=bicubic",
                f"pad={target_params['width']}:{target_params['height']}:(ow-iw)/2:(oh-ih)/2:color=black",
                f"setsar=sar={sar_value}"
            ])

        if force_fps: vf_parts.append(f"fps={target_params['fps_str']}")
        vf_parts.append(f"format=pix_fmts={final_pix_fmt}")
        vf_string = ",".join(vf_parts)

        af_string = None
        create_audio = target_params['has_audio'] and output_audio and not is_banner_segment
        if create_audio:
            af_parts = [f"aresample=resampler=soxr:osr={target_params['sample_rate']}",
                        f"aformat=sample_fmts={target_params['sample_fmt']}:channel_layouts={target_params['channel_layout']}"]
            af_string = ",".join(af_parts)

        cmd_parts = ["ffmpeg", "-y"]
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

        temp_codec_v = self.video_codec
        temp_preset_v = self.video_preset
        temp_cq_v = self.video_cq
        temp_bitrate_a = self.audio_bitrate

        cmd_parts.extend(["-c:v", temp_codec_v, "-preset", temp_preset_v, "-cq:v", temp_cq_v, "-b:v", "0"])
        if create_audio: cmd_parts.extend(["-c:a", self.audio_codec, "-b:a", temp_bitrate_a])
        cmd_parts.extend(["-video_track_timescale", target_params['video_timescale']])

        cmd_parts.extend(["-map", "0:v:0?"])
        if create_audio: cmd_parts.extend(["-map", "0:a:0?"])

        cmd_parts.append(f'"{output_path}"')
        return " ".join([p for p in cmd_parts if p is not None])

    def _generate_preprocessing_for_concat(self, input_file: str, sorted_embed_ads: List[Dict], target_params: Dict,
                                           main_video_duration: float) -> Tuple[List[str], str, List[str], float]:
        """ Generates preprocessing commands for main video segments and ads, and the concat list file. """
        print("--- Фаза 1.1: Генерация команд предварительной обработки сегментов (Видео + Реклама) ---")
        preprocessing_commands, temp_files_generated, concat_list_items = [], [], []
        total_ad_duration_sum, segment_counter, last_original_time = 0.0, 0, 0.0
        unique_ad_files = {}

        print("  Предварительная обработка уникальных рекламных файлов...")
        for ad_data in sorted_embed_ads:
            ad_path = ad_data['path']
            if ad_path not in unique_ad_files:
                temp_ad_path = self._generate_temp_filename("ad_segment_uniq", segment_counter)
                cmd = self._create_segment_command(ad_path, temp_ad_path, target_params,
                                                   duration=ad_data['duration'],
                                                   output_audio=target_params['has_audio'], force_fps=True)
                preprocessing_commands.append(cmd)
                temp_files_generated.append(temp_ad_path)
                unique_ad_files[ad_path] = {'data': ad_data, 'temp_path': temp_ad_path}
                segment_counter += 1

        print("  Генерация сегментов основного видео и списка concat...")
        for i, embed in enumerate(sorted_embed_ads):
            embed_original_time_sec, embed_ad_path = embed['time_sec'], embed['path']
            ad_info = unique_ad_files.get(embed_ad_path)
            if not ad_info: continue

            preprocessed_ad_path, ad_duration = ad_info['temp_path'], ad_info['data']['duration']
            main_segment_duration = embed_original_time_sec - last_original_time

            if main_segment_duration > 0.001:
                temp_main_path = self._generate_temp_filename("main_segment", segment_counter)
                cmd = self._create_segment_command(input_file, temp_main_path, target_params,
                                                   start_time=last_original_time, duration=main_segment_duration,
                                                   output_audio=target_params['has_audio'], force_fps=True)
                preprocessing_commands.append(cmd)
                temp_files_generated.append(temp_main_path)
                concat_list_items.append(temp_main_path)
                segment_counter += 1

            concat_list_items.append(preprocessed_ad_path)
            total_ad_duration_sum += ad_duration
            last_original_time = embed_original_time_sec

        if main_video_duration - last_original_time > 0.001:
            final_segment_duration = main_video_duration - last_original_time
            temp_main_path = self._generate_temp_filename("main_segment", segment_counter)
            cmd = self._create_segment_command(input_file, temp_main_path, target_params,
                                               start_time=last_original_time, duration=final_segment_duration,
                                               output_audio=target_params['has_audio'], force_fps=True)
            preprocessing_commands.append(cmd)
            temp_files_generated.append(temp_main_path)
            concat_list_items.append(temp_main_path)

        if not concat_list_items: raise CommandGenerationError("Нет сегментов основного видео/рекламы для объединения.")

        print("--- Фаза 1.2: Создание файла списка concat для основного видео+рекламы ---")
        concat_list_filename = f"concat_list_main_{int(time.time())}.txt"
        concat_list_path = os.path.join(tempfile.gettempdir(), concat_list_filename)
        temp_files_generated.append(concat_list_path)

        try:
            with open(concat_list_path, 'w', encoding='utf-8') as f:
                f.write("ffconcat version 1.0\n\n")
                for item_path in concat_list_items: f.write(f"file {self._escape_path_for_concat(item_path)}\n")
            print(f"  Создан файл списка: {concat_list_path}")
        except IOError as e:
            raise CommandGenerationError(f"Не удалось создать файл списка concat для видео+рекламы: {e}")

        return preprocessing_commands, concat_list_path, temp_files_generated, total_ad_duration_sum

    # --- Generate Banner Preprocessing ---
    def _generate_banner_preprocessing_commands(self,
                                                banner_file: str, banner_timecodes: List[str],
                                                original_banner_duration: float,
                                                target_params: Dict, final_duration_estimate: float,
                                                is_concat_mode: bool, sorted_embed_ads: List[Dict]
                                                ) -> Tuple[List[str], str, List[str], str]:
        """ Generates commands to create banner segments, black screen gaps, the concat list for them, and the command to concatenate them. """
        print("--- Фаза 2.1: Генерация команд предварительной обработки сегментов (Баннер) ---")
        preprocessing_cmds, temp_files, concat_list_items = [], [], []
        segment_counter = 0
        last_banner_track_time = 0.0
        banner_scaled_width = target_params['width']

        banner_params = self.get_essential_stream_params(banner_file)
        banner_scaled_height = target_params['height'] // 10
        if banner_params and banner_params.get('width') and banner_params.get('height'):
            orig_w, orig_h = banner_params['width'], banner_params['height']
            banner_scaled_height = max(1,
                                       int(orig_h * (
                                               banner_scaled_width / orig_w))) if orig_w > 0 else banner_scaled_height
        print(f"  Определены размеры трека баннера: {banner_scaled_width}x{banner_scaled_height}")

        temp_banner_segment_path = self._generate_temp_filename("banner_segment_uniq", segment_counter)
        banner_segment_cmd = self._create_segment_command(
            banner_file, temp_banner_segment_path, target_params,
            duration=original_banner_duration, output_audio=False,
            force_fps=True, is_banner_segment=True
        )
        preprocessing_cmds.append(banner_segment_cmd)
        temp_files.append(temp_banner_segment_path)
        segment_counter += 1

        adjusted_banner_times_sec = []
        valid_banner_timecodes_sec = sorted(filter(None, [self._timecode_to_seconds(tc) for tc in banner_timecodes]))
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
                temp_gap_path = self._generate_temp_filename("banner_gap", segment_counter)
                gap_cmd_parts = [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i",
                    f"color=c={self.banner_gap_color}:s={banner_scaled_width}x{banner_scaled_height}:d={gap_duration:.6f}:r={target_params['fps_str']}",
                    "-vf", f"format=pix_fmts={self.banner_track_pix_fmt}",
                    "-c:v", self.video_codec, "-preset", self.video_preset, "-crf", "0",
                    "-an", "-video_track_timescale", target_params['video_timescale'],
                    "-t", f"{gap_duration:.6f}", f'"{temp_gap_path}"'
                ]
                preprocessing_cmds.append(" ".join(gap_cmd_parts))
                temp_files.append(temp_gap_path)
                concat_list_items.append(temp_gap_path)
                segment_counter += 1

            current_banner_duration = end_time - start_time
            # Write path and duration directive for the *same* banner segment file
            concat_list_items.append(f"{temp_banner_segment_path}\nduration {current_banner_duration:.6f}")

            last_banner_track_time = end_time
            max_banner_track_duration = max(max_banner_track_duration, end_time)

        if not concat_list_items:
            raise CommandGenerationError("Не удалось создать сегменты для трека баннера.")

        print(f"--- Фаза 2.2: Создание файла списка concat для трека баннера ---")
        banner_concat_list_filename = f"concat_list_banner_{int(time.time())}.txt"
        banner_concat_list_path = os.path.join(tempfile.gettempdir(), banner_concat_list_filename)
        temp_files.append(banner_concat_list_path)

        try:
            with open(banner_concat_list_path, 'w', encoding='utf-8') as f:
                f.write("ffconcat version 1.0\n\n")
                for item in concat_list_items:
                    if '\n' in item:
                        path_part, duration_part = item.split('\n', 1)
                        f.write(f"file {self._escape_path_for_concat(path_part)}\n")
                        f.write(f"{duration_part}\n")
                    else:
                        f.write(f"file {self._escape_path_for_concat(item)}\n")
            print(f"  Создан файл списка: {banner_concat_list_path}")
        except IOError as e:
            raise CommandGenerationError(f"Не удалось создать файл списка concat для баннера: {e}")

        print(f"--- Фаза 2.3: Генерация команды для конкатенации трека баннера ---")
        concatenated_banner_path = self._generate_temp_filename("banner_track_final", 0)
        temp_files.append(concatenated_banner_path)

        concat_cmd_parts = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", f'"{banner_concat_list_path}"',
            "-c", "copy",  # Copy streams is essential here
            # Add -t to ensure the concatenated banner track has the correct maximum duration
            "-t", f"{max_banner_track_duration:.6f}",
            f'"{concatenated_banner_path}"'
        ]
        banner_concat_cmd = " ".join(concat_cmd_parts)
        print(f"  Команда конкатенации баннера создана. Выход: {concatenated_banner_path}")

        preprocessing_cmds.append(banner_concat_cmd)

        return preprocessing_cmds, banner_concat_list_path, temp_files, concatenated_banner_path

    # --- Build Moving Logo Filter ---
    def _build_moving_logo_filter(self, current_video_input_label: str, moving_input_stream_label: str,
                                  target_params: Dict, final_duration_estimate: float) -> Tuple[
        List[str], Optional[str]]:
        """Builds the filter string parts for the moving logo overlay."""
        filter_parts = []

        print(f"  Настройка фильтра для движущейся рекламы (Input: {moving_input_stream_label})...")
        moving_input_index = moving_input_stream_label.strip('[]').split(':')[0]
        scaled_moving_stream = f"[moving_scaled_{moving_input_index}]"
        transparent_moving_stream = f"[moving_alpha_{moving_input_index}]"
        overlay_output_label_moving = f"[v_moving_out_{moving_input_index}]"

        main_h = target_params['height'] if target_params['height'] else 720
        logo_target_h = max(1, int(main_h * self.moving_logo_relative_height))
        sar_value = target_params['sar'].replace(':', '/')
        moving_scale_filter = f"scale=-1:{logo_target_h}:flags=bicubic"
        filter_parts.append(
            f"{moving_input_stream_label}{moving_scale_filter},setsar=sar={sar_value}{scaled_moving_stream}")

        clamped_alpha = max(0.0, min(1.0, self.moving_logo_alpha))
        # Add format=rgba before colorchannelmixer for safety, assume input could be anything
        alpha_filter = f"format=pix_fmts=rgba,colorchannelmixer=aa={clamped_alpha:.3f}"
        filter_parts.append(f"{scaled_moving_stream}{alpha_filter}{transparent_moving_stream}")

        t_total = max(0.1, final_duration_estimate)
        if not isinstance(self.moving_speed, (int, float)) or self.moving_speed <= 0:
            moving_speed = 1.0
        else:
            moving_speed = self.moving_speed
        cycle_t = t_total / moving_speed if moving_speed > 0 else t_total
        x_expr, y_expr = "'0'", "'0'"

        if cycle_t > 0.5:
            t1, t2, t3, seg_dur = cycle_t / 4, cycle_t / 2, 3 * cycle_t / 4, max(cycle_t / 4, 1e-6)
            mx, my, tv = f"(main_w-overlay_w)", f"(main_h-overlay_h)", f"mod(t,{cycle_t:.6f})"
            x1, x2, x3, x4 = f"{mx}*({tv}/{seg_dur:.6f})", f"{mx}", f"{mx}*(1-(({tv}-{t2:.6f})/{seg_dur:.6f}))", "0"
            y1, y2, y3, y4 = "0", f"{my}*(({tv}-{t1:.6f})/{seg_dur:.6f})", f"{my}", f"{my}*(1-(({tv}-{t3:.6f})/{seg_dur:.6f}))"
            x_expr = f"'if(lt({tv},{t1:.6f}),{x1},if(lt({tv},{t2:.6f}),{x2},if(lt({tv},{t3:.6f}),{x3},{x4})))'"
            y_expr = f"'if(lt({tv},{t1:.6f}),{y1},if(lt({tv},{t2:.6f}),{y2},if(lt({tv},{t3:.6f}),{y3},{y4})))'"
            print(f"    Анимация движ. рекламы: Прямоугольный путь ({cycle_t:.2f}s цикл).")
        else:
            print(f"    Предупреждение: Длительность цикла ({cycle_t:.3f}s) мала, логотип статичен.")

        overlay_filter = f"{current_video_input_label}{transparent_moving_stream}overlay=x={x_expr}:y={y_expr}:shortest=0{overlay_output_label_moving}"
        filter_parts.append(overlay_filter)
        next_video_output_label = overlay_output_label_moving
        print(f"    Фильтр overlay для движ. рекламы добавлен. Выход: {next_video_output_label}")
        return filter_parts, next_video_output_label

    # --- Build Filter Complex for Main Command ---
    def _build_filter_complex(self,
                              base_video_specifier: str, base_audio_specifier: Optional[str], target_params: Dict,
                              final_duration_estimate: float, is_concat_mode: bool, sorted_embed_ads: List[Dict],
                              concatenated_banner_track_idx: Optional[int],
                              original_banner_duration: Optional[float],
                              banner_timecodes: Optional[List[str]],
                              moving_file: Optional[str], moving_input_idx: Optional[int]
                              ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """ Builds the complete -filter_complex string for the main command, using the pre-concatenated banner track. """
        all_filter_parts = []
        last_filter_video_label = f"[{base_video_specifier}]"
        final_video_output_map_label = base_video_specifier
        final_audio_map_label = base_audio_specifier

        # --- Banner Overlay Filter ---
        if concatenated_banner_track_idx is not None and banner_timecodes and original_banner_duration is not None:
            try:
                print(
                    f"  Настройка фильтра overlay для трека баннера (Input: [{concatenated_banner_track_idx}:v], с between)...")
                banner_track_input_label = f"[{concatenated_banner_track_idx}:v]"
                overlay_output_label_banner = f"[v_banner_out_{concatenated_banner_track_idx}]"

                enable_parts = []
                valid_banner_timecodes_sec = sorted(
                    filter(None, [self._timecode_to_seconds(tc) for tc in banner_timecodes]))
                for banner_original_sec in valid_banner_timecodes_sec:
                    adjusted_start_time = self._calculate_adjusted_times(banner_original_sec, is_concat_mode,
                                                                         sorted_embed_ads)
                    end_time = min(adjusted_start_time + original_banner_duration, final_duration_estimate)
                    if end_time > adjusted_start_time + 0.001 and adjusted_start_time < final_duration_estimate:
                        enable_parts.append(f"between(t,{adjusted_start_time:.3f},{end_time:.3f})")

                if enable_parts:
                    enable_expression = "+".join(enable_parts)
                    overlay_y_pos, overlay_x_pos = "main_h-overlay_h", "0"
                    banner_overlay_filter = (f"{last_filter_video_label}{banner_track_input_label}"
                                             f"overlay=x={overlay_x_pos}:y={overlay_y_pos}:enable='{enable_expression}':shortest=0"
                                             f"{overlay_output_label_banner}")
                    all_filter_parts.append(banner_overlay_filter)
                    last_filter_video_label = overlay_output_label_banner  # Update label for next filter stage
                    final_video_output_map_label = last_filter_video_label.strip('[]')  # Update final map label
                    print(
                        f"    Фильтр overlay для трека баннера (с between) добавлен. Выход: {last_filter_video_label}")
                else:
                    print("    Предупреждение: Не удалось создать таймкоды 'enable' для баннера, фильтр не добавлен.")
            except Exception as e:
                print(f"Предупреждение: Ошибка при построении фильтра баннера: {e}.")

        # --- Moving Logo Filter ---
        if moving_file and moving_input_idx is not None:
            try:
                moving_input_stream_label = f"[{moving_input_idx}:v]"
                # Use the output of the previous stage (banner or base video) as input
                logo_filters, last_video_label_after_logo = self._build_moving_logo_filter(
                    last_filter_video_label, moving_input_stream_label, target_params,
                    final_duration_estimate)
                if last_video_label_after_logo:  # If the logo filter was successfully added
                    all_filter_parts.extend(logo_filters)
                    last_filter_video_label = last_video_label_after_logo  # Update label again
                    final_video_output_map_label = last_filter_video_label.strip('[]')  # Update final map label
            except Exception as e:
                print(f"Предупреждение: Ошибка при построении фильтра движ. лого: {e}.")

        # --- Final Assembly ---
        if not all_filter_parts:
            print("--- Фильтры не применялись ---")
            return None, base_video_specifier, base_audio_specifier

        filter_complex_str = ";".join(all_filter_parts)
        print(
            f"--- Итоговый filter_complex сгенерирован ({len(all_filter_parts)} этапов). Видео выход: [{final_video_output_map_label}] ---")
        # DEBUG: Print the generated filter string
        # print(f"DEBUG filter_complex:\n{filter_complex_str}\n")
        return filter_complex_str, final_video_output_map_label, final_audio_map_label

    # --- Generate Main Command ---
    def _generate_main_ffmpeg_command(self,
                                      input_file: str, output_file: str, encoding_params_str: str, target_params: Dict,
                                      main_video_duration: float, track_data: Dict,
                                      concatenated_banner_track_path: Optional[str],
                                      original_banner_duration: Optional[float],
                                      banner_timecodes: Optional[List[str]],
                                      moving_file: Optional[str],
                                      is_concat_mode: bool, concat_list_path: Optional[str],
                                      sorted_embed_ads: List[Dict], total_embed_duration_added: float
                                      ) -> Tuple[str, List[str]]:
        """ Generates the main FFmpeg command string using the pre-concatenated banner track. """
        print("--- Фаза 3: Генерация основной команды конвертации (с треком баннера) ---")
        main_cmd_parts = ["ffmpeg", "-y", '-hide_banner', '-hwaccel', self.hwaccel]
        input_definitions = []
        map_commands = []
        metadata_args = []
        temp_files_for_main = []

        primary_input_options = []
        base_video_specifier = "0:v:0?"
        base_audio_specifier = "0:a:0?" if target_params['has_audio'] else None
        subtitle_input_specifier = None
        if is_concat_mode:
            if not concat_list_path or not os.path.exists(concat_list_path): raise CommandGenerationError(
                "Concat list (main) not found.")
            primary_input_options = ["-f", "concat", "-safe", "0"]
            primary_input_path = concat_list_path
            final_duration_estimate = main_video_duration + total_embed_duration_added
            print(f"Режим: Конкатенация. Input 0: {os.path.basename(concat_list_path)}")
            original_info = self.get_stream_info(input_file)
            if any(s.get('codec_type') == 'subtitle' for s in original_info.get("streams", [])):
                print(f"  Input 1 (Subs/Metadata): {os.path.basename(input_file)}")
                input_definitions.append(([], input_file))
                subtitle_input_specifier = "1:s?"
                metadata_input_index = 1
            else:
                metadata_input_index = 0
        else:
            primary_input_path = input_file
            final_duration_estimate = main_video_duration
            metadata_input_index = 0
            original_info = self.get_stream_info(input_file)
            if any(s.get('codec_type') == 'subtitle' for s in
                   original_info.get("streams", [])): subtitle_input_specifier = "0:s?"
            print(f"Режим: Прямая конвертация. Input 0: {os.path.basename(input_file)}")
        input_definitions.insert(0, (primary_input_options, primary_input_path))
        print(f"Расчетная финальная длительность: {final_duration_estimate:.3f}s")

        current_input_index = len(input_definitions)
        banner_track_input_idx = None
        moving_input_idx = None
        if concatenated_banner_track_path:
            banner_options = []
            print(
                f"  Input {current_input_index} (Concatenated Banner Track): {os.path.basename(concatenated_banner_track_path)}")
            input_definitions.append((banner_options, concatenated_banner_track_path))
            banner_track_input_idx = current_input_index
            current_input_index += 1
        if moving_file and os.path.exists(moving_file):
            moving_options = ["-loop", "1"]
            if self.get_media_duration(moving_file) is None: moving_options.extend(["-r", target_params['fps_str']])
            print(f"  Input {current_input_index} (Moving Logo): {os.path.basename(moving_file)}")
            input_definitions.append((moving_options, moving_file))
            moving_input_idx = current_input_index
            current_input_index += 1

        for options, path in input_definitions:
            main_cmd_parts.extend(options)
            main_cmd_parts.extend(["-i", f'"{path}"'])

        filter_complex_str, final_video_map_label, final_audio_map_label = self._build_filter_complex(
            base_video_specifier.rstrip('?'),
            base_audio_specifier.rstrip('?') if base_audio_specifier else None,
            target_params, final_duration_estimate, is_concat_mode,
            sorted_embed_ads,
            banner_track_input_idx,
            original_banner_duration,
            banner_timecodes,
            moving_file, moving_input_idx)

        # --- Apply Filters and Mapping ---
        # *CRITICAL FIX:* Ensure filter_complex string and mapping are added correctly
        if filter_complex_str:
            main_cmd_parts.extend(['-filter_complex', f'"{filter_complex_str}"'])
            map_commands.append(f'-map "[{final_video_map_label}]"')  # Map video from filter output
            if final_audio_map_label and target_params['has_audio']:  # Map audio (usually the base audio specifier)
                map_commands.append(f'-map {final_audio_map_label}?')
            elif not target_params['has_audio']:
                map_commands.append('-an')
        else:  # No filters applied
            map_commands.append(f'-map {base_video_specifier}')
            if base_audio_specifier and target_params['has_audio']:
                map_commands.append(f'-map {base_audio_specifier}')
            elif not target_params['has_audio']:
                map_commands.append('-an')

        if subtitle_input_specifier: map_commands.append(f"-map {subtitle_input_specifier}")
        main_cmd_parts.extend(map_commands)
        # --- End Filter and Mapping Section ---

        # Metadata Handling
        metadata_args.extend([f'-map_metadata {metadata_input_index}', "-movflags", "+use_metadata_tags"])
        source_file_for_metadata = input_file if not is_concat_mode or subtitle_input_specifier else None

        if source_file_for_metadata and os.path.exists(source_file_for_metadata):
            out_v_idx, out_a_idx, out_s_idx = 0, 0, 0
            stream_map = {}

            for map_cmd in map_commands:
                spec = map_cmd.split()[1].strip('"')
                if spec == '-an': continue

                is_filter_map = spec.startswith('[')

                if is_filter_map:
                    if out_v_idx == 0:
                        original_spec_key = f"{metadata_input_index}:v:0"
                        stream_map[original_spec_key] = f"s:v:{out_v_idx}"
                        out_v_idx += 1
                elif ':' in spec:
                    in_idx_str, stream_info = spec.split(':', 1)
                    try:
                        in_idx = int(in_idx_str)
                    except ValueError:
                        continue

                    if in_idx == metadata_input_index:
                        stream_type = stream_info[0]
                        stream_index_str = '0'
                        if ':' in stream_info.strip('?'): stream_index_str = stream_info.split(':')[1].strip('?')
                        original_spec_key = f"{metadata_input_index}:{stream_type}:{stream_index_str}"

                        if stream_type == 'v':
                            stream_map[original_spec_key] = f"s:v:{out_v_idx}"
                            out_v_idx += 1
                        elif stream_type == 'a':
                            stream_map[original_spec_key] = f"s:a:{out_a_idx}"
                            out_a_idx += 1
                        elif stream_type == 's':
                            stream_map[original_spec_key] = f"s:s:{out_s_idx}"
                            out_s_idx += 1

            print(f"  Карта метаданных (источник {metadata_input_index}, предполагаемая): {stream_map}")

            for track_id_from_user, edits in track_data.items():
                parts = track_id_from_user.split(':')
                if len(parts) == 2:
                    norm_track_id = f"{metadata_input_index}:{parts[0]}:{parts[1]}"
                elif len(parts) == 3:
                    norm_track_id = track_id_from_user
                else:
                    continue

                if norm_track_id in stream_map:
                    output_metadata_specifier = stream_map[norm_track_id]
                    print(f"    Применение метаданных к вых. потоку {output_metadata_specifier} (из {norm_track_id})")
                    if 'title' in edits and edits['title']:
                        metadata_args.extend(
                            [f"-metadata:{output_metadata_specifier}", f"title={shlex.quote(str(edits['title']))}"])
                    if 'language' in edits and edits['language']:
                        lang = str(edits['language']).lower()
                        if len(lang) == 3 and lang.isalpha(): metadata_args.extend(
                            [f"-metadata:{output_metadata_specifier}", f"language={lang}"])

        main_cmd_parts.extend(metadata_args)

        # Encoding Parameters
        if encoding_params_str:
            try:
                user_params = shlex.split(encoding_params_str)
                main_cmd_parts.extend(user_params)
            except ValueError as e:
                raise CommandGenerationError(f"Неверный синтаксис в параметрах кодирования: {e}")
        else:
            main_cmd_parts.extend(['-c:v', self.video_codec, '-c:a', self.audio_codec,
                                   '-preset', self.video_preset, '-b:a', self.audio_bitrate])
            if self.video_bitrate != "0":
                main_cmd_parts.extend(['-b:v', self.video_bitrate])
            elif self.video_cq:
                main_cmd_parts.extend(['-cq:v', self.video_cq])
            if self.video_fps:
                main_cmd_parts.extend(['-r', self.video_fps])

        # Ensure faststart for MP4
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
                    current_val = "+faststart"
                    if i + 1 < len(main_cmd_parts) and not main_cmd_parts[i + 1].startswith('-'):
                        existing_flags = main_cmd_parts[i + 1]
                        skip_next = True
                        flags = set(f.strip() for f in existing_flags.replace('+', ' ').split())
                        flags.add("faststart")
                        current_val = "+" + "+".join(sorted(list(flags)))
                    temp_cmd_parts.extend([part, current_val])
                else:
                    temp_cmd_parts.append(part)
            if not movflags_present: temp_cmd_parts.extend(["-movflags", movflags_val])
            main_cmd_parts = temp_cmd_parts

        main_cmd_parts.append(f'"{output_file}"')
        final_main_cmd = " ".join(main_cmd_parts)
        return final_main_cmd, temp_files_for_main

    # --- Main Orchestrator Function ---
    def generate_ffmpeg_commands(self,
                                 input_file: str, output_file: str, encoding_params_str: str,
                                 track_data: Dict, embed_ads: List[Dict],
                                 banner_file: Optional[str], banner_timecodes: Optional[List[str]],
                                 moving_file: Optional[str]):
        """ Generates FFmpeg commands for conversion, handling ads via concat, generating a banner track via concat, and applying overlays. """
        all_preprocessing_commands = []
        all_temp_files = []
        concatenated_banner_track_path = None
        total_embed_duration_added = 0.0

        print("--- Получение параметров основного видео ---")
        main_video_params = self.get_essential_stream_params(input_file)
        if not main_video_params: raise CommandGenerationError(f"Не удалось получить параметры из: {input_file}")
        main_video_duration = self.get_media_duration(input_file)

        print("--- Проверка и подготовка входных данных ---")
        try:
            valid_params, valid_duration, sorted_embed_ads_info, \
                valid_banner_file, valid_banner_timecodes, valid_moving_file, \
                original_banner_duration = self._validate_and_prepare_inputs(
                input_file, output_file, main_video_params, main_video_duration,
                embed_ads, banner_file, banner_timecodes, moving_file)
            banner_file, banner_timecodes, moving_file = valid_banner_file, valid_banner_timecodes, valid_moving_file
        except CommandGenerationError as e:
            print(f"Ошибка проверки вх. данных: {e}")
            raise

        print("--- Определение целевых параметров кодирования ---")
        target_params = self._determine_target_parameters(valid_params)

        is_concat_mode = bool(sorted_embed_ads_info)
        if is_concat_mode:
            total_embed_duration_added = sum(ad['duration'] for ad in sorted_embed_ads_info)
        final_duration_estimate = valid_duration + total_embed_duration_added
        print(f"Расчетная финальная длительность (с рекламой, если есть): {final_duration_estimate:.3f}s")

        # --- Preprocessing Step 1: Generate Main Video/Ad Segments (if concat needed) ---
        if is_concat_mode:
            try:
                prep_cmds_main, concat_list_path_main, prep_temp_files_main, _ = \
                    self._generate_preprocessing_for_concat(input_file, sorted_embed_ads_info, target_params,
                                                            valid_duration)
                all_preprocessing_commands.extend(prep_cmds_main)
                all_temp_files.extend(prep_temp_files_main)
            except CommandGenerationError as e:
                self._cleanup_temp_files(all_temp_files)
                print(f"Ошибка препроцессинга видео+рекламы: {e}")
                raise e
        else:
            print("--- Предварительная обработка: Конкатенация видео+рекламы не требуется ---")
            concat_list_path_main = None

        # --- Preprocessing Step 2: Generate Banner Track (if applicable) ---
        if banner_file and banner_timecodes and original_banner_duration is not None:
            try:
                prep_cmds_banner, _, prep_temp_files_banner, concatenated_banner_path = \
                    self._generate_banner_preprocessing_commands(
                        banner_file, banner_timecodes, original_banner_duration, target_params,
                        final_duration_estimate, is_concat_mode, sorted_embed_ads_info
                    )
                all_preprocessing_commands.extend(prep_cmds_banner)
                all_temp_files.extend(prep_temp_files_banner)
                concatenated_banner_track_path = concatenated_banner_path
            except CommandGenerationError as e:
                self._cleanup_temp_files(all_temp_files)
                print(f"Ошибка генерации трека баннера: {e}")
                raise e
            except Exception as e:
                self._cleanup_temp_files(all_temp_files)
                print(
                    f"Неожиданная ошибка генерации трека баннера: {e}")
                raise CommandGenerationError(
                    f"Ошибка трека баннера: {e}") from e
        else:
            print("--- Предварительная обработка: Трек баннера не требуется или невалиден ---")

        # --- Generate Main Command ---
        print("--- Генерация основной команды FFmpeg ---")
        try:
            main_command, main_temp_files = self._generate_main_ffmpeg_command(
                input_file, output_file, encoding_params_str, target_params, valid_duration, track_data,
                concatenated_banner_track_path,
                original_banner_duration,
                banner_timecodes,
                moving_file,
                is_concat_mode, concat_list_path_main,
                sorted_embed_ads_info, total_embed_duration_added)
            all_temp_files.extend(main_temp_files)
        except CommandGenerationError as e:
            self._cleanup_temp_files(all_temp_files)
            print(f"Ошибка генерации основной команды: {e}")
            raise e
        except Exception as e:
            self._cleanup_temp_files(all_temp_files)
            print(
                f"Неожиданная ошибка генерации основной команды: {e}")
            raise CommandGenerationError(
                f"Ошибка основной команды: {e}") from e

        unique_temp_files = sorted(list(set(all_temp_files)))
        return all_preprocessing_commands, main_command, unique_temp_files

    # --- FFmpeg Execution ---
    @staticmethod
    def run_ffmpeg_command(cmd: str, step_name: str):
        """Executes a single FFmpeg command using subprocess.run() and handles errors."""
        print(f"\n--- Запуск шага: {step_name} ---")
        if len(cmd) > 1000:
            print(f"Команда: {cmd[:500]}... (всего {len(cmd)} симв.)")
        else:
            print(f"Команда: {cmd}")
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       encoding='utf-8', errors='replace', startupinfo=startupinfo)
            stderr_output, progress_line = "", ""
            while True:
                line = process.stderr.readline()
                if not line: break
                stderr_output += line
                stripped = line.strip()
                if stripped.startswith(('frame=', 'size=')):
                    progress_line = stripped
                    print(f"  {progress_line}", end='\r')
                elif progress_line:
                    print(f"\n  [stderr] {stripped}")
                    progress_line = ""
                else:
                    print(f"  [stderr] {stripped}")
            if progress_line: print()  # Newline after progress
            process.stdout.close()
            return_code = process.wait()
            if return_code != 0: raise ConversionError(
                f"Ошибка '{step_name}' (код {return_code}).\nКоманда:\n{cmd}\nStderr (конец):\n{stderr_output[-2000:]}")
            print(f"--- {step_name}: Успешно завершено ---")
            return True
        except FileNotFoundError:
            raise FfmpegError("FFmpeg не найден. Убедитесь, что он установлен и в PATH.") from None
        except ConversionError as e:
            raise e
        except Exception as e:
            raise FfmpegError(f"Неожиданная ошибка при запуске '{step_name}': {type(e).__name__} - {e}") from e

    # --- Cleanup Helper ---
    @staticmethod
    def _cleanup_temp_files(temp_files: List[str]):
        """Attempts to delete temporary files."""
        if not temp_files: return
        print(f"\n--- Очистка временных файлов ({len(temp_files)}) ---")
        deleted_count, failed_count = 0, 0
        for f in list(temp_files):
            try:
                if f and os.path.exists(f):
                    try:
                        os.chmod(f, 0o777)
                    except Exception as e:
                        print(f'Возникла ошибка при очистке временных файлов: {e}')
                    os.remove(f)
                    deleted_count += 1
            except OSError as e:
                print(f"  Ошибка удаления {os.path.basename(f)}: {e}")
                failed_count += 1
            except Exception as e:
                print(f"  Неожиданная ошибка при удалении {os.path.basename(f)}: {e}")
                failed_count += 1
        print(f"--- Очистка завершена (Удалено: {deleted_count}, Ошибок: {failed_count}/{len(temp_files)}) ---")
