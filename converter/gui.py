# converter/gui.py
import os
import re
import subprocess
import time
import tkinter as tk
from doctest import master
from tkinter import ttk, filedialog, simpledialog, messagebox
from typing import List, Dict, Tuple, Optional, Any

from . import ffmpeg_utils, config
from .exceptions import FfmpegError, CommandGenerationError, ConversionError


class VideoConverterGUI:
    def __init__(self, master: tk.Tk):
        self.master = master
        self.VERSION = '0.1b'
        self.TITLE = f"JustConverter + AdBurner"
        self.AUTHOR = f"dimnissv"
        master.title(f'{self.TITLE} ({self.AUTHOR}) {self.VERSION}')

        self.notebook = ttk.Notebook(master)

        self.main_tab = ttk.Frame(self.notebook)
        self.advertisement_tab = ttk.Frame(self.notebook)
        self.transcode_tab = ttk.Frame(self.notebook)
        self.start_tab = ttk.Frame(self.notebook)
        self.about_tab = ttk.Frame(self.notebook)

        self.track_data: Dict[str, Dict[str, str]] = {}
        self.main_video_duration: Optional[float] = None
        self.main_video_params: Dict[str, Any] = {}
        self.embed_ads: List[Dict[str, Any]] = []
        self.banner_timecodes: List[str] = []
        self.temp_files_to_clean: List[str] = []

        self._create_main_tab_widgets()
        self._create_advertisement_tab_widgets()
        self._create_transcode_tab_widgets()
        self._create_start_tab_widgets()
        self._create_about_tab_widgets()

        self.notebook.add(self.main_tab, text="Файлы")
        self.notebook.add(self.advertisement_tab, text="Реклама")
        self.notebook.add(self.transcode_tab, text="Транскодирование")
        self.notebook.add(self.start_tab, text="Начать")
        self.notebook.add(self.about_tab, text="О программе")

        self.notebook.grid(row=0, column=0, sticky="nsew")
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)

        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.ffmpeg_instance = None

    def _create_main_tab_widgets(self) -> None:
        self.input_file_label = tk.Label(self.main_tab, text="Входной файл:")
        self.input_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.input_file_entry = tk.Entry(self.main_tab, width=50)
        self.input_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.input_file_button = tk.Button(self.main_tab, text="Выбрать", command=self.browse_input_file)
        self.input_file_button.grid(row=0, column=2, padx=5, pady=5)

        self.track_label = tk.Label(self.main_tab, text="Дорожки (дважды щелкните Название/Язык для редактирования):")
        self.track_label.grid(row=1, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        self.track_tree = ttk.Treeview(self.main_tab,
                                       columns=("id", "type", "details", "title", "language"),
                                       show="headings")
        self.track_tree.heading("id", text="ID")
        self.track_tree.heading("type", text="Тип")
        self.track_tree.heading("details", text="Детали")
        self.track_tree.heading("title", text="Название")
        self.track_tree.heading("language", text="Язык")

        self.track_tree.column("id", width=40, stretch=tk.NO, anchor='center')
        self.track_tree.column("type", width=60, stretch=tk.NO)
        self.track_tree.column("details", width=200, stretch=tk.YES)
        self.track_tree.column("title", width=150, stretch=tk.YES)
        self.track_tree.column("language", width=60, stretch=tk.NO, anchor='center')

        self.track_tree.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
        tree_scrollbar = ttk.Scrollbar(self.main_tab, orient="vertical", command=self.track_tree.yview)
        tree_scrollbar.grid(row=2, column=3, sticky='ns')
        self.track_tree.configure(yscrollcommand=tree_scrollbar.set)

        self.main_tab.grid_rowconfigure(2, weight=1)
        self.main_tab.grid_columnconfigure(1, weight=1)
        self.track_tree.bind("<Double-1>", self.edit_track_data)

    def _create_advertisement_tab_widgets(self) -> None:
        self.embed_file_label = tk.Label(self.advertisement_tab, text="Встраиваемая реклама (видео):")
        self.embed_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.embed_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.embed_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.embed_file_button = tk.Button(self.advertisement_tab, text="Выбрать",
                                           command=lambda: self.browse_ad_file(self.embed_file_entry, video_only=True))
        self.embed_file_button.grid(row=0, column=2, padx=5, pady=5)
        self.advertisement_tab.grid_columnconfigure(1, weight=1)

        self.embed_timecodes_label = tk.Label(self.advertisement_tab, text="Таймкоды вставки (MM:SS):")
        self.embed_timecodes_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.embed_timecodes_entry = tk.Entry(self.advertisement_tab, width=20)
        self.embed_timecodes_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.add_embed_timecode_button = tk.Button(self.advertisement_tab, text="Добавить",
                                                   command=self.add_embed_timecode)
        self.add_embed_timecode_button.grid(row=1, column=2, padx=5, pady=5, sticky="w")

        self.embed_timecodes_listbox = tk.Listbox(self.advertisement_tab, width=60, height=4)
        self.embed_timecodes_listbox.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.embed_timecodes_listbox.bind("<Double-1>", self.delete_embed_timecode)
        embed_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical",
                                        command=self.embed_timecodes_listbox.yview)
        embed_scrollbar.grid(row=2, column=3, sticky='ns')
        self.embed_timecodes_listbox.configure(yscrollcommand=embed_scrollbar.set)

        self.banner_file_label = tk.Label(self.advertisement_tab, text="Баннерная реклама (видео/картинка):")
        self.banner_file_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.banner_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.banner_file_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")
        self.banner_file_button = tk.Button(self.advertisement_tab, text="Выбрать",
                                            command=lambda: self.browse_ad_file(self.banner_file_entry))
        self.banner_file_button.grid(row=3, column=2, padx=5, pady=5)

        self.banner_timecodes_label = tk.Label(self.advertisement_tab, text="Таймкоды показа (MM:SS):")
        self.banner_timecodes_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.banner_timecodes_entry = tk.Entry(self.advertisement_tab, width=20)
        self.banner_timecodes_entry.grid(row=4, column=1, padx=5, pady=5, sticky="w")
        self.add_banner_timecode_button = tk.Button(self.advertisement_tab, text="Добавить",
                                                    command=self.add_banner_timecode)
        self.add_banner_timecode_button.grid(row=4, column=2, padx=5, pady=5, sticky="w")

        self.banner_timecodes_listbox = tk.Listbox(self.advertisement_tab, width=60, height=4)
        self.banner_timecodes_listbox.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.banner_timecodes_listbox.bind("<Double-1>", self.delete_banner_timecode)
        banner_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical",
                                         command=self.banner_timecodes_listbox.yview)
        banner_scrollbar.grid(row=5, column=3, sticky='ns')
        self.banner_timecodes_listbox.configure(yscrollcommand=banner_scrollbar.set)

        self.banner_track_pix_fmt_label = tk.Label(self.advertisement_tab, text='PIX_FMT для баннера')
        self.banner_track_pix_fmt_label.grid(row=6, column=0, padx=5, pady=5, sticky='w')
        self.banner_track_pix_fmt_entry = tk.Entry(self.advertisement_tab, width=10)
        self.banner_track_pix_fmt_entry.grid(row=6, column=1, padx=5, pady=5, sticky='w')
        self.banner_track_pix_fmt_entry.insert(0, config.BANNER_TRACK_PIX_FMT)
        self.banner_gap_color_label = tk.Label(self.advertisement_tab, text='Цвет фона для временного файла')
        self.banner_gap_color_label.grid(row=7, column=0, padx=5, pady=5, sticky='w')
        self.banner_gap_color_entry = tk.Entry(self.advertisement_tab, width=10)
        self.banner_gap_color_entry.grid(row=7, column=1, padx=5, pady=5, sticky='w')
        self.banner_gap_color_entry.insert(0, config.BANNER_GAP_COLOR)

        self.moving_file_label = tk.Label(self.advertisement_tab, text="Движущаяся реклама (картинка):")
        self.moving_file_label.grid(row=8, column=0, padx=5, pady=5, sticky="w")
        self.moving_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.moving_file_entry.grid(row=8, column=1, padx=5, pady=5, sticky="ew")
        self.moving_file_button = tk.Button(self.advertisement_tab, text="Выбрать",
                                            command=lambda: self.browse_ad_file(self.moving_file_entry, image=True))
        self.moving_file_button.grid(row=8, column=2, padx=5, pady=5)

        self.moving_speed_label = tk.Label(self.advertisement_tab, text="Скорость движущейся рекламы:")
        self.moving_speed_label.grid(row=9, column=0, padx=5, pady=5, sticky="w")
        self.moving_speed_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_speed_entry.grid(row=9, column=1, padx=5, pady=5, sticky="w")
        self.moving_speed_entry.insert(0, "1.0")

        self.moving_logo_relative_height_label = tk.Label(self.advertisement_tab, text="Высота движущейся рекламы (%):")
        self.moving_logo_relative_height_label.grid(row=10, column=0, padx=5, pady=5, sticky="w")
        self.moving_logo_relative_height_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_logo_relative_height_entry.grid(row=10, column=1, padx=5, pady=5, sticky="w")
        self.moving_logo_relative_height_entry.insert(0, "0.1")

        self.moving_logo_alpha_label = tk.Label(self.advertisement_tab, text="Прозрачность движущейся рекламы:")
        self.moving_logo_alpha_label.grid(row=11, column=0, padx=5, pady=5, sticky="w")
        self.moving_logo_alpha_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_logo_alpha_entry.grid(row=11, column=1, padx=5, pady=5, sticky="w")
        self.moving_logo_alpha_entry.insert(0, "1.0")

    def _create_transcode_tab_widgets(self) -> None:
        self.video_codec_label = tk.Label(self.transcode_tab, text='Видео-кодек:')
        self.video_codec_label.grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.video_codec_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_codec_entry.grid(row=0, column=1, padx=5, pady=5, sticky='w')
        self.video_codec_entry.insert(0, config.VIDEO_CODEC)

        self.video_preset_label = tk.Label(self.transcode_tab, text='Пресет:')
        self.video_preset_label.grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.video_preset_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_preset_entry.grid(row=1, column=1, padx=5, pady=5, sticky='w')
        self.video_preset_entry.insert(0, config.VIDEO_PRESET)

        self.video_cq_label = tk.Label(self.transcode_tab, text='CQ:')
        self.video_cq_label.grid(row=2, column=0, padx=5, pady=5, sticky='w')
        self.video_cq_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_cq_entry.grid(row=2, column=1, padx=5, pady=5, sticky='w')

        self.video_cq_entry.insert(0, config.VIDEO_CQ)
        self.video_bitrate_label = tk.Label(self.transcode_tab, text='Битрейт видео:')
        self.video_bitrate_label.grid(row=3, column=0, padx=5, pady=5, sticky='w')
        self.video_bitrate_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_bitrate_entry.grid(row=3, column=1, padx=5, pady=5, sticky='w')
        self.video_bitrate_entry.insert(0, config.VIDEO_BITRATE)

        self.audio_codec_label = tk.Label(self.transcode_tab, text='Аудио-кодек:')
        self.audio_codec_label.grid(row=4, column=0, padx=5, pady=5, sticky='w')
        self.audio_codec_entry = tk.Entry(self.transcode_tab, width=20)
        self.audio_codec_entry.grid(row=4, column=1, padx=5, pady=5, sticky='w')

        self.audio_codec_entry.insert(0, config.AUDIO_CODEC)
        self.audio_bitrate_label = tk.Label(self.transcode_tab, text='Битрейт аудио:')
        self.audio_bitrate_label.grid(row=5, column=0, padx=5, pady=5, sticky='w')
        self.audio_bitrate_entry = tk.Entry(self.transcode_tab, width=20)
        self.audio_bitrate_entry.grid(row=5, column=1, padx=5, pady=5, sticky='w')
        self.audio_bitrate_entry.insert(0, config.AUDIO_BITRATE)

        self.video_fps_label = tk.Label(self.transcode_tab, text='FPS видео:')
        self.video_fps_label.grid(row=6, column=0, padx=5, pady=5, sticky='w')
        self.video_fps_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_fps_entry.grid(row=6, column=1, padx=5, pady=5, sticky='w')

        self.hwaccel_label = tk.Label(self.transcode_tab, text="Аппаратное ускорение:")
        self.hwaccel_label.grid(row=7, column=0, padx=5, pady=5, sticky="w")
        self.hwaccel_combo = ttk.Combobox(self.transcode_tab, values=self.detect_hwaccels(), state="readonly")
        self.hwaccel_combo.grid(row=7, column=1, padx=5, pady=5, sticky="w")
        self.hwaccel_combo.set(config.HWACCEL)

        self.additional_encoding_label = tk.Label(self.transcode_tab, text="Дополнительные параметры:")
        self.additional_encoding_label.grid(row=8, column=0, padx=5, pady=5, sticky="w")
        self.additional_encoding_entry = tk.Entry(self.transcode_tab, width=60)
        self.additional_encoding_entry.insert(0, "-movflags +faststart -g 50")
        self.additional_encoding_entry.grid(row=8, column=1, padx=5, pady=5, sticky="ew")

        self.encoding_label = tk.Label(self.transcode_tab, text="Указать параметры вручную:")
        self.encoding_label.grid(row=9, column=0, padx=5, pady=5, sticky="w")
        self.encoding_entry = tk.Entry(self.transcode_tab, width=60)
        self.encoding_entry.grid(row=9, column=1, padx=5, pady=5, sticky="ew")

        self.transcode_tab.grid_columnconfigure(1, weight=1)

    def _create_start_tab_widgets(self) -> None:
        self.output_file_label = tk.Label(self.start_tab, text="Выходной файл:")
        self.output_file_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.output_file_entry = tk.Entry(self.start_tab, width=50)
        self.output_file_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")
        self.output_file_button = tk.Button(self.start_tab, text="Выбрать", command=self.browse_output_file)
        self.output_file_button.grid(row=4, column=2, padx=5, pady=5)
        self.start_tab.grid_columnconfigure(1, weight=1)

        self.generate_command_button = tk.Button(self.start_tab, text="Показать команды FFmpeg",
                                                 command=self.show_ffmpeg_commands)
        self.generate_command_button.grid(row=5, column=0, columnspan=3, pady=10)

        self.output_info_label = tk.Label(self.start_tab, text="Команды FFmpeg и Лог:")
        self.output_info_label.grid(row=6, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        self.output_info = tk.Text(self.start_tab, height=12, wrap=tk.WORD, relief=tk.SUNKEN,
                                   borderwidth=1)
        self.output_info.grid(row=7, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
        self.output_scrollbar = tk.Scrollbar(self.start_tab, command=self.output_info.yview)
        self.output_scrollbar.grid(row=7, column=3, sticky='nsew')
        self.output_info['yscrollcommand'] = self.output_scrollbar.set
        self.start_tab.grid_rowconfigure(7, weight=2)

        self.start_conversion_button = tk.Button(self.start_tab, text="Начать конвертацию",
                                                 command=self.start_conversion,
                                                 font=('Helvetica', 10, 'bold'))
        self.start_conversion_button.grid(row=8, column=0, columnspan=3, pady=10)

    def _create_about_tab_widgets(self) -> None:
        self.info_text_widget = tk.Text(self.about_tab, wrap=tk.WORD, borderwidth=0, highlightthickness=0,
                                        state=tk.DISABLED)
        self.info_text_widget.grid(row=2, column=0, padx=5, pady=5, sticky="ew")
        self.info_text_widget.tag_config("url", foreground="blue", underline=True)
        self.info_text_widget.tag_bind("url", "<Button-1>", self._open_url)

        self.info_text_widget.config(state=tk.NORMAL)
        self.info_text_widget.insert(tk.END, "Программа: ")
        self.info_text_widget.insert(tk.END, self.TITLE + "\n")
        self.info_text_widget.insert(tk.END, "Автор: ")
        self.info_text_widget.insert(tk.END, f"{self.AUTHOR}\n")
        self.info_text_widget.insert(tk.END, f"Версия: ")
        self.info_text_widget.insert(tk.END, f"{self.VERSION}\n")

        self._insert_link(self.info_text_widget, "GitHub: ", "https://github.com/DIMNISSV/JustConverter")
        self._insert_link(self.info_text_widget, "Wiki: ", "https://github.com/DIMNISSV/JustConverter/wiki")
        self._insert_link(self.info_text_widget, "Telegram: ", "https://t.me/dimnissv")

        self.info_text_widget.config(state=tk.DISABLED)

    def _insert_link(self, text_widget: tk.Text, label: str, url: str) -> None:
        text_widget.insert(tk.END, label)
        text_widget.insert(tk.END, url, "url")
        text_widget.insert(tk.END, "\n")

    def _open_url(self, event: tk.Event) -> None:
        import webbrowser
        index = self.info_text_widget.index(tk.CURRENT)
        tag_indices = self.info_text_widget.tag_ranges("url")
        for start, end in zip(tag_indices[::2], tag_indices[1::2]):
            if self.info_text_widget.compare(start, "<=", index) and self.info_text_widget.compare(index, "<", end):
                url = self.info_text_widget.get(start, end)
                webbrowser.open_new(url)
                break

    def on_closing(self) -> None:
        self.cleanup_temp_files()
        self.master.destroy()

    def cleanup_temp_files(self) -> None:
        if not self.temp_files_to_clean:
            return
        print(f"Начинаю очистку временных файлов ({len(self.temp_files_to_clean)})...")
        cleaned_count = 0
        files_to_remove = list(self.temp_files_to_clean)
        self.temp_files_to_clean.clear()

        for temp_file in files_to_remove:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    cleaned_count += 1
                except OSError as e:
                    print(f"Ошибка удаления временного файла {temp_file}: {e}")
                except Exception as e:
                    print(f"Неожиданная ошибка при удалении временного файла {temp_file}: {e}")
        print(f"Очистка завершена. Удалено {cleaned_count} из {len(files_to_remove)} файлов.")

    def _clear_state(self) -> None:
        print("Сброс состояния GUI...")
        self.cleanup_temp_files()
        self.input_file_entry.delete(0, tk.END)
        self.output_file_entry.delete(0, tk.END)
        self.embed_file_entry.delete(0, tk.END)
        self.embed_timecodes_entry.delete(0, tk.END)
        self.banner_file_entry.delete(0, tk.END)
        self.banner_timecodes_entry.delete(0, tk.END)
        self.moving_file_entry.delete(0, tk.END)

        self.track_data = {}
        self.main_video_duration = None
        self.main_video_params = {}
        self.embed_ads = []
        self.banner_timecodes = []
        self.temp_files_to_clean = []

        self.embed_timecodes_listbox.delete(0, tk.END)
        self.banner_timecodes_listbox.delete(0, tk.END)

        for item in self.track_tree.get_children():
            self.track_tree.delete(item)

        self.output_info.delete('1.0', tk.END)

        self.master.update_idletasks()
        print("Сброс состояния завершен.")

    def browse_input_file(self) -> None:
        self._clear_state()

        file_path = filedialog.askopenfilename(
            title="Выберите входной видеофайл",
            filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")])
        if not file_path:
            print("Выбор входного файла отменен.")
            return

        self.input_file_entry.insert(0, file_path)
        print(f"Выбран входной файл: {file_path}")

        try:
            base, ext = os.path.splitext(file_path)
            suggested_output_base = f"{base}_converted"
            suggested_output = f"{suggested_output_base}{ext}"
            counter = 1
            while os.path.exists(suggested_output):
                suggested_output = f"{suggested_output_base}_{counter}{ext}"
                counter += 1
            self.output_file_entry.insert(0, suggested_output)
            print(f"Предложен выходной файл: {suggested_output}")
        except Exception as e:
            print(f"Ошибка при предложении имени выходного файла: {e}")

        try:
            self.output_info.insert('1.0', f"Анализ файла: {os.path.basename(file_path)}...\n")
            self.master.update_idletasks()
            self.populate_track_table(file_path)
            self.main_video_params = ffmpeg_utils.FFMPEG().get_essential_stream_params(file_path)
            if not self.main_video_params:
                warning_msg = "Не удалось получить все ключевые параметры основного видео."
                messagebox.showwarning("Параметры", warning_msg)
                self.output_info.insert(tk.END, f"ПРЕДУПРЕЖДЕНИЕ: {warning_msg}\n")
            else:
                print("Основные параметры видео для совместимости:", self.main_video_params)
                self.output_info.insert(tk.END,
                                        f"Параметры видео (ШxВ): {self.main_video_params.get('width')}x{self.main_video_params.get('height')}, FPS: {self.main_video_params.get('fps_str')}\n")
                self.output_info.insert(tk.END,
                                        f"Параметры аудио: {self.main_video_params.get('sample_rate')} Hz, {self.main_video_params.get('channel_layout')}\n")
                if self.main_video_params.get('width') is None:
                    error_msg = "Не удалось определить параметры видеопотока. Выберите другой файл."
                    messagebox.showerror("Ошибка видео", error_msg)
                    self.output_info.insert(tk.END, f"ОШИБКА: {error_msg}\n")
                    self._clear_state()

                if self.main_video_params.get('fps_str'):
                    self.video_fps_entry.delete(0, tk.END)
                    self.video_fps_entry.insert(0, self.main_video_params.get('fps_str'))

            self.output_info.insert(tk.END, "Анализ завершен.\n")

        except FfmpegError as e:
            error_msg = f"Не удалось проанализировать входной файл:\n{e}"
            messagebox.showerror("Ошибка FFprobe", error_msg)
            self.output_info.insert(tk.END, f"ОШИБКА FFPROBE: {error_msg}\n")
            self._clear_state()

    def browse_output_file(self) -> None:
        default_name = ""
        initial_dir = os.getcwd()
        current_output = self.output_file_entry.get()
        input_path = self.input_file_entry.get()

        if current_output:
            default_name = os.path.basename(current_output)
            initial_dir = os.path.dirname(current_output) or initial_dir
        elif input_path:
            try:
                base, ext = os.path.splitext(input_path)
                default_name = f"{os.path.basename(base)}_converted.mkv"
                initial_dir = os.path.dirname(input_path) or initial_dir
            except Exception:
                pass

        file_path = filedialog.asksaveasfilename(
            title="Выберите выходной файл",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".mkv",
            filetypes=[("MKV Video", "*.mkv"), ("MP4 Video", "*.mp4"), ("All Files", "*.*")])
        if file_path:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, file_path)
            print(f"Выбран выходной файл: {file_path}")
        else:
            print("Выбор выходного файла отменен.")

    def browse_ad_file(self, entry_widget: tk.Entry, image: bool = False, video_only: bool = False) -> None:
        initial_dir = os.getcwd()
        current_ad_path = entry_widget.get()
        if current_ad_path and os.path.exists(os.path.dirname(current_ad_path)):
            initial_dir = os.path.dirname(current_ad_path)

        if image:
            filetypes = [("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All Files", "*.*")]
            title = "Выберите файл изображения"
        elif video_only:
            filetypes = [("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")]
            title = "Выберите видеофайл"
        else:
            filetypes = [("Media Files", "*.mp4 *.avi *.mkv *.mov *.webm *.png *.jpg *.jpeg *.bmp *.gif"),
                         ("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"),
                         ("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif"),
                         ("All Files", "*.*")]
            title = "Выберите файл медиа"

        file_path = filedialog.askopenfilename(title=title, initialdir=initial_dir, filetypes=filetypes)
        if file_path:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, file_path)
            print(f"Выбран файл для '{entry_widget.winfo_parent()}': {file_path}")
        else:
            print(f"Выбор файла для '{entry_widget.winfo_parent()}' отменен.")

    def populate_track_table(self, file_path: str) -> None:
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)
        self.main_video_duration = None
        self.track_data = {}

        try:
            output = ffmpeg_utils.FFMPEG().get_stream_info(file_path)

            format_duration_str = output.get("format", {}).get("duration")
            if format_duration_str:
                try:
                    self.main_video_duration = float(format_duration_str)
                    print(f"Основная длительность (формат): {self.main_video_duration:.3f}s")
                except (ValueError, TypeError) as e:
                    print(f"Ошибка преобразования длительности формата: {e}")

            for i, stream in enumerate(output.get("streams", [])):
                stream_index = stream.get('index', i)
                track_id_str = f"0:{stream_index}"
                track_type = stream.get("codec_type", "N/A")
                tags = stream.get("tags", {})
                track_title = tags.get("title", "")
                track_language = tags.get("language", "und")

                details = []
                if track_type == "video":
                    details.append(f"{stream.get('codec_name', '?')}")
                    details.append(f"{stream.get('width')}x{stream.get('height')}")
                    details.append(f"{stream.get('pix_fmt', '?')}")
                    fps = stream.get('r_frame_rate', '?')
                    details.append(f"{fps} fps")
                    if self.main_video_duration is None:
                        stream_dur_str = stream.get("duration")
                        if stream_dur_str:
                            try:
                                self.main_video_duration = float(stream_dur_str)
                                print(f"Основная длительность (видеопоток): {self.main_video_duration:.3f}s")
                            except (ValueError, TypeError) as e:
                                print(f"Ошибка преобразования длительности видеопотока: {e}")

                elif track_type == "audio":
                    details.append(f"{stream.get('codec_name', '?')}")
                    details.append(f"{stream.get('sample_rate', '?')} Hz")
                    details.append(f"{stream.get('channel_layout', '?')}")
                    details.append(f"{stream.get('sample_fmt', '?')}")
                elif track_type == "subtitle":
                    details.append(f"{stream.get('codec_name', '?')}")
                else:
                    details.append(f"{stream.get('codec_name', '?')}")

                details_str = ", ".join(filter(None, map(str, details)))

                self.track_tree.insert("", tk.END, iid=track_id_str,
                                       values=(track_id_str, track_type, details_str, track_title, track_language))

            if self.main_video_duration is None:
                warning_msg = "Не удалось определить основную длительность видео из ffprobe."
                messagebox.showwarning("Длительность", warning_msg)
                self.output_info.insert(tk.END, f"ПРЕДУПРЕЖДЕНИЕ: {warning_msg}\n")

        except FfmpegError as e:
            print(f"Ошибка FFprobe при заполнении таблицы дорожек: {e}")
            raise

    def edit_track_data(self, event: tk.Event) -> None:
        item_iid = self.track_tree.identify_row(event.y)
        column_id = self.track_tree.identify_column(event.x)

        if not item_iid or not column_id:
            print("Клик не попал на строку или столбец.")
            return

        column_name_internal = self.track_tree.column(column_id, "id")

        if column_name_internal not in {'title', 'language'}:
            return

        item_values = list(self.track_tree.item(item_iid, "values"))
        track_path_id = item_values[0]
        try:
            column_index = self.track_tree['columns'].index(column_name_internal)
            current_value = item_values[column_index]
            column_name_display = self.track_tree.heading(column_id)['text']
        except (ValueError, IndexError, KeyError) as e:
            print(f"Ошибка получения данных столбца для редактирования: {e}")
            messagebox.showerror("Ошибка", "Не удалось получить данные для редактирования.")
            return

        new_value = simpledialog.askstring(f"Изменить {column_name_display}",
                                           f"Введите новое значение для '{column_name_display}' (Дорожка ID: {track_path_id}):",
                                           initialvalue=current_value)

        if new_value is not None:
            if column_name_internal == 'language':
                new_value = new_value.strip().lower()
                if not re.fullmatch(r'[a-z]{3}', new_value):
                    messagebox.showerror("Неверный язык",
                                         "Код языка должен состоять из 3 латинских букв (напр., eng, rus, und).")
                    return

            item_values[column_index] = new_value
            self.track_tree.item(item_iid, values=item_values)

            if track_path_id not in self.track_data:
                self.track_data[track_path_id] = {}
            self.track_data[track_path_id][column_name_internal] = new_value
            print(f"Сохранено изменение для {track_path_id}: {column_name_internal} = '{new_value}'")
            self.output_info.insert(tk.END,
                                    f"Обновлена метадата для {track_path_id}: {column_name_internal} = '{new_value}'\n")

    def validate_timecode(self, timecode: Any) -> bool:
        if not isinstance(timecode, str):
            return False
        return re.fullmatch(r"(\d+):([0-5]\d)", timecode) is not None

    def timecode_to_seconds(self, timecode: str) -> float:
        if not self.validate_timecode(timecode):
            raise ValueError(f"Неверный формат таймкода: {timecode}")
        minutes, seconds = map(int, timecode.split(':'))
        return float(minutes * 60 + seconds)

    def add_embed_timecode(self) -> None:
        timecode = self.embed_timecodes_entry.get().strip()
        embed_file = self.embed_file_entry.get().strip()

        if not self.validate_timecode(timecode):
            messagebox.showerror("Ошибка таймкода", "Неверный формат таймкода для вставки (требуется MM:SS).")
            return
        if not embed_file:
            messagebox.showerror("Ошибка файла", "Выберите файл для встраиваемой рекламы.")
            return
        if not os.path.exists(embed_file):
            messagebox.showerror("Ошибка файла", f"Файл рекламы не найден:\n{embed_file}")
            return
        if self.main_video_duration is None:
            messagebox.showerror("Ошибка длительности",
                                 "Сначала выберите и проанализируйте основной видеофайл, чтобы определить его длительность.")
            return

        try:
            time_sec = self.timecode_to_seconds(timecode)
            if time_sec > self.main_video_duration:
                messagebox.showwarning("Предупреждение",
                                       f"Таймкод {timecode} ({time_sec:.2f}s) превышает длительность основного видео ({self.main_video_duration:.2f}s). Реклама не будет добавлена.")
                return
            elif time_sec == self.main_video_duration:
                if not messagebox.askyesno("Предупреждение",
                                           f"Таймкод {timecode} совпадает с концом видео.\nРеклама будет добавлена в самый конец.\nПродолжить?"):
                    return
        except ValueError:
            messagebox.showerror("Ошибка таймкода", f"Не удалось преобразовать таймкод: {timecode}")
            return

        try:
            embed_duration = ffmpeg_utils.FFMPEG().get_media_duration(embed_file)
            if embed_duration is None or embed_duration <= 0:
                messagebox.showerror("Ошибка длительности рекламы",
                                     f"Не удалось определить положительную длительность файла рекламы:\n{embed_file}\nУбедитесь, что это действительный видеофайл.")
                return
        except FfmpegError as e:
            messagebox.showerror("Ошибка FFprobe (реклама)", f"Не удалось получить длительность рекламы:\n{e}")
            return

        for ad in self.embed_ads:
            if ad['timecode'] == timecode:
                messagebox.showwarning("Дубликат",
                                       f"Таймкод {timecode} уже добавлен для встраиваемой рекламы.\nДважды щелкните запись в списке для удаления.")
                return

        ad_data = {'path': embed_file, 'timecode': timecode, 'duration': embed_duration}
        self.embed_ads.append(ad_data)
        self.embed_ads.sort(key=lambda x: self.timecode_to_seconds(x['timecode']))
        print(f"Добавлена вставка: {ad_data}")

        self._update_embed_listbox()
        self.embed_timecodes_entry.delete(0, tk.END)

    def delete_embed_timecode(self, event: tk.Event) -> None:
        selected_indices = self.embed_timecodes_listbox.curselection()
        if not selected_indices:
            return

        index_to_delete = selected_indices[0]
        try:
            ad_info = self.embed_ads[index_to_delete]
            confirm = messagebox.askyesno("Удалить вставку?",
                                          f"Удалить вставку рекламы:\n"
                                          f"Файл: {os.path.basename(ad_info['path'])}\n"
                                          f"Таймкод: {ad_info['timecode']}\n"
                                          f"Длительность: {ad_info['duration']:.2f}s")
            if confirm:
                deleted_ad = self.embed_ads.pop(index_to_delete)
                print(f"Удалена вставка: {deleted_ad}")
                self._update_embed_listbox()
        except IndexError:
            print(f"Ошибка индекса при удалении встроенной рекламы: индекс {index_to_delete}, список {self.embed_ads}")
            messagebox.showerror("Ошибка",
                                 "Не удалось удалить выбранную запись (ошибка синхронизации). Попробуйте обновить список.")
            self._update_embed_listbox()

    def _update_embed_listbox(self) -> None:
        self.embed_timecodes_listbox.delete(0, tk.END)
        for ad in self.embed_ads:
            display_text = f"{ad['timecode']} ({os.path.basename(ad['path'])}, {ad['duration']:.2f}s)"
            self.embed_timecodes_listbox.insert(tk.END, display_text)

    def add_banner_timecode(self) -> None:
        timecode = self.banner_timecodes_entry.get().strip()

        if not self.validate_timecode(timecode):
            messagebox.showerror("Ошибка таймкода", "Неверный формат таймкода баннера (требуется MM:SS).")
            return

        if self.main_video_duration is None:
            messagebox.showerror("Ошибка длительности", "Сначала выберите и проанализируйте основной видеофайл.")
            return

        try:
            time_sec = self.timecode_to_seconds(timecode)
            if time_sec >= self.main_video_duration:
                messagebox.showwarning("Предупреждение",
                                       f"Таймкод баннера {timecode} ({time_sec:.2f}s) равен или превышает *оригинальную* длительность видео ({self.main_video_duration:.2f}s).\nУбедитесь, что это желаемое поведение, учитывая возможный сдвиг времени из-за вставок.")
        except ValueError:
            messagebox.showerror("Ошибка таймкода", f"Не удалось преобразовать таймкод: {timecode}")
            return

        if timecode in self.banner_timecodes:
            messagebox.showwarning("Дубликат",
                                   f"Таймкод {timecode} уже добавлен для баннера.\nДважды щелкните запись для удаления.")
            return

        self.banner_timecodes.append(timecode)
        self.banner_timecodes.sort(key=self.timecode_to_seconds)
        print(f"Добавлен таймкод баннера: {timecode}. Текущий список: {self.banner_timecodes}")

        self._update_banner_listbox()
        self.banner_timecodes_entry.delete(0, tk.END)

    def delete_banner_timecode(self, event: tk.Event) -> None:
        selected_indices = self.banner_timecodes_listbox.curselection()
        if not selected_indices:
            return

        index = selected_indices[0]
        try:
            tc_to_remove = self.banner_timecodes_listbox.get(index)
            if messagebox.askyesno("Удалить таймкод?", f"Удалить таймкод баннера: {tc_to_remove}?"):
                if tc_to_remove in self.banner_timecodes:
                    self.banner_timecodes.remove(tc_to_remove)
                    print(f"Удален таймкод баннера: {tc_to_remove}. Текущий список: {self.banner_timecodes}")
                else:
                    print(f"Предупреждение: Таймкод {tc_to_remove} не найден во внутреннем списке для удаления.")
                self._update_banner_listbox()
        except (IndexError, tk.TclError) as e:
            print(f"Ошибка при удалении таймкода баннера: индекс {index}, Ошибка: {e}")
            messagebox.showerror("Ошибка", "Не удалось удалить выбранную запись.")
            self._update_banner_listbox()

    def _update_banner_listbox(self) -> None:
        self.banner_timecodes_listbox.delete(0, tk.END)
        for tc in self.banner_timecodes:
            self.banner_timecodes_listbox.insert(tk.END, tc)

    def detect_hwaccels(self) -> List[str]:
        try:
            process = subprocess.Popen(["ffmpeg", "-hwaccels", "-hide_banner"], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       text=True)
            output, _ = process.communicate()
            hwaccels = [line.strip() for line in output.splitlines() if line.strip() != "" and "ffmpeg" not in line]
            return hwaccels
        except FileNotFoundError:
            return ["ffmpeg not found"]
        except Exception:
            return ["error detecting hwaccels"]

    def _prepare_and_generate_commands(self) -> Optional[Tuple[List[str], str, List[str]]]:
        self.cleanup_temp_files()
        self.output_info.delete('1.0', tk.END)
        self.output_info.insert('1.0', "Подготовка и генерация команд FFmpeg...\n")
        self.master.update_idletasks()

        input_file = self.input_file_entry.get().strip()
        output_file = self.output_file_entry.get().strip()
        encoding_params_str = self.encoding_entry.get().strip()
        banner_file = self.banner_file_entry.get().strip() or None
        moving_file = self.moving_file_entry.get().strip() or None

        video_codec = self.video_codec_entry.get().strip() or None
        video_preset = self.video_preset_entry.get().strip() or None
        video_cq = self.video_cq_entry.get().strip() or None
        video_bitrate = self.video_bitrate_entry.get().strip() or None
        audio_codec = self.audio_codec_entry.get().strip() or None
        audio_bitrate = self.audio_bitrate_entry.get().strip() or None
        video_fps = self.video_fps_entry.get().strip() or None
        moving_speed = self.moving_speed_entry.get().strip() or None
        moving_logo_relative_height = self.moving_logo_relative_height_entry.get().strip() or None
        moving_logo_alpha = self.moving_logo_alpha_entry.get().strip() or None
        banner_track_pix_fmt = self.banner_track_pix_fmt_entry.get().strip() or None
        banner_gap_color = self.banner_gap_color_entry.get().strip() or None
        hwaccel = self.hwaccel_combo.get().strip()

        error_messages = []
        if not input_file: error_messages.append("- Не выбран входной файл.")
        if not output_file: error_messages.append("- Не выбран выходной файл.")
        if input_file and not os.path.exists(input_file): error_messages.append(
            f"- Входной файл не найден: {input_file}")
        if self.main_video_duration is None or self.main_video_duration <= 0:
            error_messages.append("- Не удалось определить допустимую длительность основного видео. Перевыберите файл.")
        if not self.main_video_params or self.main_video_params.get('width') is None:
            error_messages.append("- Не удалось получить параметры основного видео. Перевыберите файл.")
        if banner_file and not os.path.exists(banner_file):
            error_messages.append(f"- Файл баннера не найден: {banner_file}")
            self.output_info.insert(tk.END,
                                    f"ПРЕДУПРЕЖДЕНИЕ: Файл баннера '{banner_file}' не найден, он будет проигнорирован.\n")
            banner_file = None
        if moving_file and not os.path.exists(moving_file):
            error_messages.append(f"- Файл движущейся рекламы не найден: {moving_file}")
            self.output_info.insert(tk.END,
                                    f"ПРЕДУПРЕЖДЕНИЕ: Файл движ. рекламы '{moving_file}' не найден, он будет проигнорирован.\n")
            moving_file = None
        if banner_file and not self.banner_timecodes:
            self.output_info.insert(tk.END,
                                    "ПРЕДУПРЕЖДЕНИЕ: Выбран файл баннера, но не указаны таймкоды показа. Баннер не будет добавлен.\n")

        if error_messages:
            full_error_msg = "Пожалуйста, исправьте следующие ошибки:\n" + "\n".join(error_messages)
            messagebox.showerror("Ошибка валидации", full_error_msg)
            self.output_info.insert(tk.END, f"ОШИБКА ВАЛИДАЦИИ:\n{full_error_msg}\n")
            return None

        try:
            moving_speed = float(moving_speed) if moving_speed else 1.0
            moving_logo_relative_height = float(moving_logo_relative_height) if moving_logo_relative_height else 0.1
            moving_logo_alpha = float(moving_logo_alpha) if moving_logo_alpha else 1.0
        except ValueError as e:
            messagebox.showerror("Ошибка параметров", f"Неверное значение для параметра рекламы: {e}")
            return None

        try:
            self.ffmpeg_instance = ffmpeg_utils.FFMPEG(
                video_codec=video_codec,
                video_preset=video_preset,
                video_cq=video_cq,
                video_bitrate=video_bitrate,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
                video_fps=video_fps,
                moving_speed=moving_speed,
                moving_logo_relative_height=moving_logo_relative_height,
                moving_logo_alpha=moving_logo_alpha,
                banner_track_pix_fmt=banner_track_pix_fmt,
                banner_gap_color=banner_gap_color,
                hwaccel=hwaccel)
        except Exception as e:
            messagebox.showerror("Ошибка FFmpeg", f"Не удалось создать экземпляр FFmpeg: {e}")
            self.output_info.insert(tk.END, f"ОШИБКА FFmpeg:\nНе удалось создать экземпляр FFmpeg: {e}\n")
            return None

        print("Вызов generate_ffmpeg_commands с параметрами:")
        print(f"  input_file: {input_file}")
        print(f"  output_file: {output_file}")
        print(f"  encoding_params_str: {encoding_params_str}")
        print(f"  main_video_params: {self.main_video_params}")
        print(f"  main_video_duration: {self.main_video_duration}")
        print(f"  track_data: {self.track_data}")
        print(f"  embed_ads: {self.embed_ads}")
        print(f"  banner_file: {banner_file}")
        print(f"  banner_timecodes: {self.banner_timecodes}")
        print(f"  moving_file: {moving_file}")

        try:
            result = self.ffmpeg_instance.generate_ffmpeg_commands(
                input_file=input_file,
                output_file=output_file,
                encoding_params_str=encoding_params_str,
                track_data=self.track_data,
                embed_ads=self.embed_ads,
                banner_file=banner_file,
                banner_timecodes=self.banner_timecodes,
                moving_file=moving_file
            )
            self.temp_files_to_clean = result[2] if result and len(result) > 2 else []
            self.output_info.insert(tk.END, "Команды успешно сгенерированы.\n")
            print(f"Сгенерированные временные файлы: {self.temp_files_to_clean}")
            return result

        except (CommandGenerationError, FfmpegError) as e:
            error_msg = f"Ошибка генерации команды:\n{e}"
            messagebox.showerror("Ошибка генерации", error_msg)
            self.output_info.insert(tk.END, f"ОШИБКА ГЕНЕРАЦИИ КОМАНДЫ:\n{error_msg}\n")
            self.cleanup_temp_files()
            return None
        except Exception as e:
            error_msg = f"Произошла неожиданная ошибка при генерации команд:\n{type(e).__name__}: {e}"
            messagebox.showerror("Неожиданная ошибка", error_msg)
            self.output_info.insert(tk.END, f"НЕОЖИДАННАЯ ОШИБКА:\n{error_msg}\n")
            import traceback
            traceback.print_exc()
            self.cleanup_temp_files()
            return None

    def show_ffmpeg_commands(self) -> None:
        result = self._prepare_and_generate_commands()
        self.output_info.delete('1.0', tk.END)

        if result:
            preproc_cmds, main_cmd, temp_files_generated = result
            output_text = "--- Временные файлы для создания ---\n"
            if temp_files_generated:
                output_text += "\n".join([f"  - {os.path.basename(f)}" for f in temp_files_generated])
            else:
                output_text += "  (нет)"
            output_text += "\n\n"

            if preproc_cmds:
                output_text += f"--- Команды предварительной обработки ({len(preproc_cmds)}) ---\n"
                for i, cmd in enumerate(preproc_cmds):
                    output_text += f"[{i + 1}]: {cmd}\n{'-' * 40}\n"
                output_text += "\n"
            else:
                output_text += "--- Нет команд предварительной обработки ---\n\n"

            if main_cmd:
                output_text += "--- Основная команда конвертации ---\n"
                output_text += main_cmd + "\n"
            else:
                output_text += "--- ОШИБКА: Не удалось сгенерировать основную команду ---"

            self.output_info.insert('1.0', output_text)
            self.output_info.yview_moveto(0.0)
        else:
            self.output_info.insert('1.0',
                                    "Ошибка генерации команд. Проверьте настройки и сообщения об ошибках выше/в консоли.")

    def start_conversion(self) -> None:
        result = self._prepare_and_generate_commands()

        if not result:
            messagebox.showerror("Отмена", "Не удалось подготовить команды FFmpeg. Конвертация отменена.")
            return

        preproc_cmds, main_cmd, _ = result

        num_preproc = len(preproc_cmds) if preproc_cmds else 0
        confirm_message_parts = ["Будут выполнены следующие шаги:"]
        steps = []
        if num_preproc > 0:
            steps.append(f"Предварительная обработка {num_preproc} сегментов/рекламы (создание временных файлов).")
        if main_cmd:
            steps.append("Основная конвертация с объединением и наложением.")

        if not steps:
            messagebox.showerror("Ошибка", "Нет команд для выполнения!")
            return

        for i, step_desc in enumerate(steps):
            confirm_message_parts.append(f"\n{i + 1}. {step_desc}")

        confirm_message_parts.append("\n\nПроцесс может занять значительное время, особенно предварительная обработка.")
        confirm_message_parts.append("\n\nПродолжить?")
        confirm_message = "".join(confirm_message_parts)

        if not messagebox.askyesno("Подтверждение конвертации", confirm_message):
            print("Конвертация отменена пользователем.")
            self.output_info.insert(tk.END, "\nКонвертация отменена пользователем.\n")
            self.cleanup_temp_files()
            return

        self.output_info.delete('1.0', tk.END)
        self.output_info.insert('1.0', "Начало процесса конвертации...\n\n")
        self.master.update()

        try:
            if preproc_cmds:
                self.output_info.insert(tk.END,
                                        f"--- Этап 1: Предварительная обработка ({len(preproc_cmds)} команд) ---\n")
                self.master.update()
                start_time_preproc = time.time()
                for i, cmd in enumerate(preproc_cmds):
                    step_name = f"Предварительная обработка {i + 1}/{len(preproc_cmds)}"
                    self.output_info.insert(tk.END, f"\nЗапуск: {step_name}...\n")
                    self.output_info.see(tk.END)
                    self.master.update()
                    start_time_step = time.time()
                    ffmpeg_utils.FFMPEG.run_ffmpeg_command(cmd, step_name)
                    end_time_step = time.time()
                    self.output_info.insert(tk.END,
                                            f"Успешно завершено: {step_name} (за {end_time_step - start_time_step:.2f} сек)\n")
                    self.output_info.see(tk.END)
                    self.master.update()
                end_time_preproc = time.time()
                self.output_info.insert(tk.END,
                                        f"\n--- Предварительная обработка завершена (общее время: {end_time_preproc - start_time_preproc:.2f} сек) ---\n")

            if main_cmd:
                step_name = "Основная конвертация"
                self.output_info.insert(tk.END, f"\n--- Этап 2: {step_name} ---\n")
                self.output_info.see(tk.END)
                self.master.update()
                start_time_main = time.time()
                ffmpeg_utils.FFMPEG.run_ffmpeg_command(main_cmd, step_name)
                end_time_main = time.time()
                self.output_info.insert(tk.END,
                                        f"\nУспешно завершено: {step_name} (за {end_time_main - start_time_main:.2f} сек)\n")
                self.output_info.see(tk.END)
                self.master.update()
            else:
                raise ConversionError("Нет основной команды FFmpeg для выполнения.")

            success_msg = "\n--- УСПЕХ: Конвертация успешно завершена! ---"
            self.output_info.insert(tk.END, success_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showinfo("Успех", "Конвертация успешно завершена!")

        except ConversionError as e:
            error_msg = f"\n--- ОШИБКА КОНВЕРТАЦИИ ---\n{e}\n--- КОНВЕРТАЦИЯ ПРЕРВАНА ---"
            print(error_msg)
            self.output_info.insert(tk.END, error_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showerror("Сбой конвертации", f"Произошла ошибка во время конвертации:\n\n{e}")
        except Exception as e:
            error_msg = f"\n--- НЕОЖИДАННАЯ ОШИБКА ---\n{type(e).__name__}: {e}\n--- КОНВЕРТАЦИЯ ПРЕРВАНА ---"
            print(error_msg)
            import traceback
            traceback.print_exc()
            self.output_info.insert(tk.END, error_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showerror("Критический сбой",
                                 f"Произошла непредвиденная ошибка:\n{type(e).__name__}: {e}\n\nПроверьте консоль для деталей.")

        finally:
            self.output_info.insert(tk.END, "\nЗапуск финальной очистки временных файлов...\n")
            self.output_info.see(tk.END)
            self.master.update()
            self.cleanup_temp_files()
            self.output_info.insert(tk.END, "Очистка завершена.\n")
            self.output_info.see(tk.END)
