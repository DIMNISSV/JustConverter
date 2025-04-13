# converter/gui.py
import os
import re
import time
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
# shlex is not strictly needed here anymore as metadata quoting is handled in ffmpeg_utils
# but keep it if you plan other shell-like splitting/quoting elsewhere.
# import shlex
from typing import List, Dict, Tuple, Optional, Any  # Import necessary types

# Import functions from the sibling module
from . import ffmpeg_utils
from .exceptions import FfmpegError, CommandGenerationError, ConversionError  # Import custom exceptions


class VideoConverterGUI:
    """
    Main class for the Video Converter GUI using Tkinter.

    Handles user interaction, input validation, command generation preview,
    and orchestrates the conversion process using ffmpeg_utils.
    """

    def __init__(self, master: tk.Tk):
        """
        Initializes the GUI application.

        Args:
            master: The root Tkinter window.
        """
        self.master = master
        master.title("Простой Конвертер Видео (Concat Demuxer)")

        self.notebook = ttk.Notebook(master)

        self.main_tab = ttk.Frame(self.notebook)
        self.advertisement_tab = ttk.Frame(self.notebook)
        self.transcode_tab = ttk.Frame(self.notebook)
        self.start_tab = ttk.Frame(self.notebook)

        # --- Common Data ---
        # Dictionary to store user edits for track metadata.
        # Key: Track identifier string (e.g., "0:1").
        # Value: Dict with {"title": "New Title", "language": "eng"}.
        self.track_data: Dict[str, Dict[str, str]] = {}
        # Duration of the main input video in seconds.
        self.main_video_duration: Optional[float] = None
        # Key parameters extracted from the main video for compatibility checks.
        self.main_video_params: Dict[str, Any] = {}
        # List of dictionaries representing ads to be embedded.
        # Each dict: {'path': str, 'timecode': str (MM:SS), 'duration': float}
        self.embed_ads: List[Dict[str, Any]] = []
        # List of timecode strings (MM:SS) for displaying the banner overlay.
        self.banner_timecodes: List[str] = []
        # List to keep track of temporary files created during preprocessing.
        self.temp_files_to_clean: List[str] = []

        # --- Widgets ---
        self._create_main_tab_widgets()
        self._create_advertisement_tab_widgets()
        self._create_transcode_tab_widgets()
        self._create_start_tab_widgets()

        # --- Layout Notebook ---
        self.notebook.add(self.main_tab, text="Файлы")
        self.notebook.add(self.advertisement_tab, text="Реклама")
        self.notebook.add(self.transcode_tab, text="Транскодирование")
        self.notebook.add(self.start_tab, text="Начать")

        self.notebook.grid(row=0, column=0, sticky="nsew")
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)

        # --- Cleanup on Close ---
        master.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _create_main_tab_widgets(self) -> None:
        """Creates widgets for the 'Files' tab."""
        # Input File
        self.input_file_label = tk.Label(self.main_tab, text="Входной файл:")
        self.input_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.input_file_entry = tk.Entry(self.main_tab, width=50)
        self.input_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.input_file_button = tk.Button(self.main_tab, text="Выбрать", command=self.browse_input_file)
        self.input_file_button.grid(row=0, column=2, padx=5, pady=5)

        # Track Tree
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
        """Creates widgets for the 'Advertisement' tab."""
        # Embed Ad File
        self.embed_file_label = tk.Label(self.advertisement_tab, text="Встраиваемая реклама (видео):")
        self.embed_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.embed_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.embed_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.embed_file_button = tk.Button(self.advertisement_tab, text="Выбрать",
                                           command=lambda: self.browse_ad_file(self.embed_file_entry, video_only=True))
        self.embed_file_button.grid(row=0, column=2, padx=5, pady=5)
        self.advertisement_tab.grid_columnconfigure(1, weight=1)

        # Embed Ad Timecodes
        self.embed_timecodes_label = tk.Label(self.advertisement_tab, text="Таймкоды вставки (MM:SS):")
        self.embed_timecodes_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.embed_timecodes_entry = tk.Entry(self.advertisement_tab, width=20)
        self.embed_timecodes_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.add_embed_timecode_button = tk.Button(self.advertisement_tab, text="Добавить",
                                                   command=self.add_embed_timecode)
        self.add_embed_timecode_button.grid(row=1, column=2, padx=5, pady=5, sticky="w")

        # Embed Ad Listbox
        self.embed_timecodes_listbox = tk.Listbox(self.advertisement_tab, width=60, height=4)
        self.embed_timecodes_listbox.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.embed_timecodes_listbox.bind("<Double-1>", self.delete_embed_timecode)
        embed_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical",
                                        command=self.embed_timecodes_listbox.yview)
        embed_scrollbar.grid(row=2, column=3, sticky='ns')
        self.embed_timecodes_listbox.configure(yscrollcommand=embed_scrollbar.set)

        # Banner Ad File
        self.banner_file_label = tk.Label(self.advertisement_tab, text="Баннерная реклама (видео/картинка):")
        self.banner_file_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.banner_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.banner_file_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")
        self.banner_file_button = tk.Button(self.advertisement_tab, text="Выбрать",
                                            command=lambda: self.browse_ad_file(self.banner_file_entry))
        self.banner_file_button.grid(row=3, column=2, padx=5, pady=5)

        # Banner Ad Timecodes
        self.banner_timecodes_label = tk.Label(self.advertisement_tab, text="Таймкоды показа (MM:SS):")
        self.banner_timecodes_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.banner_timecodes_entry = tk.Entry(self.advertisement_tab, width=20)
        self.banner_timecodes_entry.grid(row=4, column=1, padx=5, pady=5, sticky="w")
        self.add_banner_timecode_button = tk.Button(self.advertisement_tab, text="Добавить",
                                                    command=self.add_banner_timecode)
        self.add_banner_timecode_button.grid(row=4, column=2, padx=5, pady=5, sticky="w")

        # Banner Ad Listbox
        self.banner_timecodes_listbox = tk.Listbox(self.advertisement_tab, width=60, height=4)
        self.banner_timecodes_listbox.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.banner_timecodes_listbox.bind("<Double-1>", self.delete_banner_timecode)
        banner_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical",
                                         command=self.banner_timecodes_listbox.yview)
        banner_scrollbar.grid(row=5, column=3, sticky='ns')
        self.banner_timecodes_listbox.configure(yscrollcommand=banner_scrollbar.set)

        # Moving Ad File
        self.moving_file_label = tk.Label(self.advertisement_tab, text="Движущаяся реклама (картинка):")
        self.moving_file_label.grid(row=6, column=0, padx=5, pady=5, sticky="w")
        self.moving_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.moving_file_entry.grid(row=6, column=1, padx=5, pady=5, sticky="ew")
        self.moving_file_button = tk.Button(self.advertisement_tab, text="Выбрать",
                                            command=lambda: self.browse_ad_file(self.moving_file_entry, image=True))
        self.moving_file_button.grid(row=6, column=2, padx=5, pady=5)

    def _create_transcode_tab_widgets(self) -> None:
        """Creates widgets for the 'Transcoding' tab."""
        # Encoding Params
        self.encoding_label = tk.Label(self.transcode_tab, text="Дополнительные параметры FFmpeg:")
        self.encoding_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.encoding_entry = tk.Entry(self.transcode_tab, width=60)  # Wider entry
        # Default using NVENC. Use 'libx264 -preset medium -crf 23' if NVENC unavailable/not desired
        self.encoding_entry.insert(0,
                                   "-c:v h264_nvenc -preset p6 -tune hq -cq 23 -b:v 0 -c:a aac -b:a 192k -movflags +faststart")  # Added movflags
        self.encoding_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")
        self.transcode_tab.grid_columnconfigure(1, weight=1)  # Allow entry to expand

    def _create_start_tab_widgets(self) -> None:
        """Creates widgets for the 'Start' tab."""
        # Output File
        self.output_file_label = tk.Label(self.start_tab, text="Выходной файл:")
        self.output_file_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.output_file_entry = tk.Entry(self.start_tab, width=50)
        self.output_file_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")
        self.output_file_button = tk.Button(self.start_tab, text="Выбрать", command=self.browse_output_file)
        self.output_file_button.grid(row=4, column=2, padx=5, pady=5)
        self.start_tab.grid_columnconfigure(1, weight=1)  # Allow entry to expand

        # Generate/Show Command Button
        self.generate_command_button = tk.Button(self.start_tab, text="Показать команды FFmpeg",
                                                 command=self.show_ffmpeg_commands)
        self.generate_command_button.grid(row=5, column=0, columnspan=3, pady=10)

        # Output Info Text Area
        self.output_info_label = tk.Label(self.start_tab, text="Команды FFmpeg и Лог:")
        self.output_info_label.grid(row=6, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        self.output_info = tk.Text(self.start_tab, height=12, wrap=tk.WORD, relief=tk.SUNKEN,
                                   borderwidth=1)  # Added relief/border
        self.output_info.grid(row=7, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
        self.output_scrollbar = tk.Scrollbar(self.start_tab, command=self.output_info.yview)
        self.output_scrollbar.grid(row=7, column=3, sticky='nsew')
        self.output_info['yscrollcommand'] = self.output_scrollbar.set
        self.start_tab.grid_rowconfigure(7, weight=2)

        # Start Conversion Button
        self.start_conversion_button = tk.Button(self.start_tab, text="Начать конвертацию",
                                                 command=self.start_conversion,
                                                 font=('Helvetica', 10, 'bold'))  # Added font
        self.start_conversion_button.grid(row=8, column=0, columnspan=3, pady=10)

    def on_closing(self) -> None:
        """
        Handles the window closing event.

        Ensures temporary files are cleaned up before destroying the window.
        """
        self.cleanup_temp_files()
        self.master.destroy()

    def cleanup_temp_files(self) -> None:
        """
        Safely removes all temporary files tracked in self.temp_files_to_clean.

        Prints status messages about the cleanup process.
        """
        if not self.temp_files_to_clean:
            return
        print(f"Начинаю очистку временных файлов ({len(self.temp_files_to_clean)})...")
        cleaned_count = 0
        # Make a copy in case list is modified during iteration (less likely here)
        files_to_remove = list(self.temp_files_to_clean)
        self.temp_files_to_clean.clear()  # Clear original list immediately

        for temp_file in files_to_remove:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    # print(f"Удален временный файл: {temp_file}") # Can uncomment for verbose logging
                    cleaned_count += 1
                except OSError as e:
                    print(f"Ошибка удаления временного файла {temp_file}: {e}")
                except Exception as e:
                    print(f"Неожиданная ошибка при удалении временного файла {temp_file}: {e}")
        print(f"Очистка завершена. Удалено {cleaned_count} из {len(files_to_remove)} файлов.")

    def _clear_state(self) -> None:
        """
        Resets the GUI and internal state to default values.

        Cleans up temp files, clears entry fields, resets data structures,
        and clears the track tree and output log. Called when loading a new
        input file or encountering a critical error during input analysis.
        """
        print("Сброс состояния GUI...")
        self.cleanup_temp_files()  # Clean up any existing temp files first
        # Clear entry fields
        self.input_file_entry.delete(0, tk.END)
        self.output_file_entry.delete(0, tk.END)
        self.embed_file_entry.delete(0, tk.END)
        self.embed_timecodes_entry.delete(0, tk.END)
        self.banner_file_entry.delete(0, tk.END)
        self.banner_timecodes_entry.delete(0, tk.END)
        self.moving_file_entry.delete(0, tk.END)
        # self.encoding_entry.delete(0, tk.END) # Keep encoding params? Optional.

        # Clear data structures
        self.track_data = {}
        self.main_video_duration = None
        self.main_video_params = {}
        self.embed_ads = []
        self.banner_timecodes = []
        self.temp_files_to_clean = []  # Ensure this is cleared too

        # Clear listboxes
        self.embed_timecodes_listbox.delete(0, tk.END)
        self.banner_timecodes_listbox.delete(0, tk.END)

        # Clear Treeview
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)

        # Clear output log
        self.output_info.delete('1.0', tk.END)

        # Force GUI update to reflect changes
        self.master.update_idletasks()
        print("Сброс состояния завершен.")

    # --- Browse Methods ---
    def browse_input_file(self) -> None:
        """
        Opens a file dialog to select the main input video file.

        Clears previous state, populates input/output fields, and analyzes
        the selected file using `populate_track_table` and
        `ffmpeg_utils.get_essential_stream_params`. Shows errors if analysis fails.
        """
        self._clear_state()  # Clear everything before loading new

        file_path = filedialog.askopenfilename(
            title="Выберите входной видеофайл",
            filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")])
        if not file_path:
            print("Выбор входного файла отменен.")
            return  # User cancelled

        self.input_file_entry.insert(0, file_path)
        print(f"Выбран входной файл: {file_path}")

        # Suggest output file name based on input
        try:
            base, ext = os.path.splitext(file_path)
            suggested_output_base = f"{base}_converted"
            suggested_output = f"{suggested_output_base}{ext}"
            # Avoid overwriting if suggested name exists by adding a counter
            counter = 1
            while os.path.exists(suggested_output):
                suggested_output = f"{suggested_output_base}_{counter}{ext}"
                counter += 1
            self.output_file_entry.insert(0, suggested_output)
            print(f"Предложен выходной файл: {suggested_output}")
        except Exception as e:
            print(f"Ошибка при предложении имени выходного файла: {e}")
            # Continue without suggesting output name if error occurs

        # Populate track table and get essential info using ffmpeg_utils
        try:
            self.output_info.insert('1.0', f"Анализ файла: {os.path.basename(file_path)}...\n")
            self.master.update_idletasks()
            self.populate_track_table(file_path)  # Handles ffprobe calls internally now
            self.main_video_params = ffmpeg_utils.get_essential_stream_params(file_path)
            if not self.main_video_params:
                warning_msg = "Не удалось получить все ключевые параметры основного видео (разрешение, fps и т.д.) с помощью ffprobe. Некоторые функции (например, вставка рекламы) могут работать некорректно."
                messagebox.showwarning("Параметры", warning_msg)
                self.output_info.insert(tk.END, f"ПРЕДУПРЕЖДЕНИЕ: {warning_msg}\n")
                # Allow proceeding, but concat might fail later if params are truly missing
            else:
                print("Основные параметры видео для совместимости:", self.main_video_params)
                self.output_info.insert(tk.END,
                                        f"Параметры видео (ШxВ): {self.main_video_params.get('width')}x{self.main_video_params.get('height')}, FPS: {self.main_video_params.get('fps_str')}\n")
                self.output_info.insert(tk.END,
                                        f"Параметры аудио: {self.main_video_params.get('sample_rate')} Hz, {self.main_video_params.get('channel_layout')}\n")
                # Set main duration from params if available, otherwise keep from populate_track_table
                if self.main_video_params.get('width') is None:  # Check if video params were actually found
                    error_msg = "Не удалось определить параметры видеопотока. Выберите другой файл."
                    messagebox.showerror("Ошибка видео", error_msg)
                    self.output_info.insert(tk.END, f"ОШИБКА: {error_msg}\n")
                    self._clear_state()
            self.output_info.insert(tk.END, "Анализ завершен.\n")

        except FfmpegError as e:
            error_msg = f"Не удалось проанализировать входной файл:\n{e}"
            messagebox.showerror("Ошибка FFprobe", error_msg)
            self.output_info.insert(tk.END, f"ОШИБКА FFPROBE: {error_msg}\n")
            self._clear_state()  # Clear the invalid input

    def browse_output_file(self) -> None:
        """
        Opens a file dialog to select the output video file path.

        Suggests a default name based on the input file or current output field.
        Defaults to MKV format.
        """
        default_name = ""
        initial_dir = os.getcwd()  # Start in current directory or input file's dir?
        current_output = self.output_file_entry.get()
        input_path = self.input_file_entry.get()

        if current_output:
            default_name = os.path.basename(current_output)
            initial_dir = os.path.dirname(current_output) or initial_dir
        elif input_path:
            try:
                base, ext = os.path.splitext(input_path)
                default_name = f"{os.path.basename(base)}_converted.mkv"  # Default to mkv
                initial_dir = os.path.dirname(input_path) or initial_dir
            except Exception:
                pass  # Ignore errors generating default name

        file_path = filedialog.asksaveasfilename(
            title="Выберите выходной файл",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".mkv",  # Default to MKV as it's flexible
            filetypes=[("MKV Video", "*.mkv"), ("MP4 Video", "*.mp4"), ("All Files", "*.*")])
        if file_path:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, file_path)
            print(f"Выбран выходной файл: {file_path}")
        else:
            print("Выбор выходного файла отменен.")

    def browse_ad_file(self, entry_widget: tk.Entry, image: bool = False, video_only: bool = False) -> None:
        """
        Opens a file dialog to select an advertisement file (video or image).

        Args:
            entry_widget: The tk.Entry widget to update with the selected path.
            image: If True, only allow image file types.
            video_only: If True, only allow video file types. Ignored if image=True.
        """
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
        else:  # Allow video or image (for banners)
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

    # --- Track Table Methods ---
    def populate_track_table(self, file_path: str) -> None:
        """
        Populates the track Treeview with stream information from the input file.

        Uses `ffmpeg_utils.get_stream_info` to fetch data. Also attempts to set
        `self.main_video_duration` from the format or first video stream info.

        Args:
            file_path: Path to the input media file.

        Raises:
            FfmpegError: If `ffmpeg_utils.get_stream_info` fails.
        """
        # Clear existing items
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)
        self.main_video_duration = None  # Reset duration before analysis
        self.track_data = {}  # Reset edited track data

        try:
            # Use the utility function (raises FfprobeError on failure)
            output = ffmpeg_utils.get_stream_info(file_path)

            # Get duration from format section first
            format_duration_str = output.get("format", {}).get("duration")
            if format_duration_str:
                try:
                    self.main_video_duration = float(format_duration_str)
                    print(f"Основная длительность (формат): {self.main_video_duration:.3f}s")
                except (ValueError, TypeError) as e:
                    print(f"Ошибка преобразования длительности формата: {e}")

            # Populate treeview with streams
            for i, stream in enumerate(output.get("streams", [])):
                stream_index = stream.get('index', i)
                # Use a consistent track identifier format (input_index:stream_index)
                # Assuming only one input file here, so input_index is 0.
                track_id_str = f"0:{stream_index}"
                track_type = stream.get("codec_type", "N/A")
                # Extract metadata (title, language) - use 'und' (undetermined) if missing
                tags = stream.get("tags", {})
                track_title = tags.get("title", "")
                track_language = tags.get("language", "und")

                details = []
                if track_type == "video":
                    details.append(f"{stream.get('codec_name', '?')}")
                    details.append(f"{stream.get('width')}x{stream.get('height')}")
                    details.append(f"{stream.get('pix_fmt', '?')}")
                    fps = stream.get('r_frame_rate', '?')  # Rational number (e.g., 30000/1001)
                    details.append(f"{fps} fps")
                    # Try to get duration from this video stream if format duration failed
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
                else:  # Data streams etc.
                    details.append(f"{stream.get('codec_name', '?')}")

                # Format details string nicely
                details_str = ", ".join(filter(None, map(str, details)))

                # Insert into tree
                self.track_tree.insert("", tk.END, iid=track_id_str,  # Use track_id as item id
                                       values=(track_id_str, track_type, details_str, track_title, track_language))

            if self.main_video_duration is None:
                warning_msg = "Не удалось определить основную длительность видео из ffprobe. Расчеты времени могут быть неверны."
                messagebox.showwarning("Длительность", warning_msg)
                self.output_info.insert(tk.END, f"ПРЕДУПРЕЖДЕНИЕ: {warning_msg}\n")
                # Proceed, but time calculations might be wrong

        except FfmpegError as e:
            # Let the calling function handle showing the error and clearing state
            print(f"Ошибка FFprobe при заполнении таблицы дорожек: {e}")
            raise  # Re-raise the exception

    def edit_track_data(self, event: tk.Event) -> None:
        """
        Handles double-click events on the track Treeview.

        Allows editing the 'Title' and 'Language' columns by opening a simple
        dialog box. Updates the Treeview and the internal `self.track_data` store.

        Args:
            event: The Tkinter event object associated with the double-click.
        """
        item_iid = self.track_tree.identify_row(event.y)  # Get item identifier (our "0:x" string)
        column_id = self.track_tree.identify_column(event.x)  # Get column identifier (e.g., #4)

        if not item_iid or not column_id:
            print("Клик не попал на строку или столбец.")
            return

        # Use column ID to get the internal column name ('id', 'type', 'title', etc.)
        column_name_internal = self.track_tree.column(column_id, "id")

        # Allow editing only for 'title' and 'language' columns
        if column_name_internal not in {'title', 'language'}:
            # Optionally provide feedback or just ignore
            # print(f"Редактирование столбца '{column_name_internal}' не поддерживается.")
            return

        # Get current values for the selected row
        item_values = list(self.track_tree.item(item_iid, "values"))
        track_path_id = item_values[0]  # The '0:x' ID stored in the first value column
        # Get the index of the editable column based on its internal name
        try:
            column_index = self.track_tree['columns'].index(column_name_internal)
            current_value = item_values[column_index]
            column_name_display = self.track_tree.heading(column_id)['text']  # Get user-visible heading text
        except (ValueError, IndexError, KeyError) as e:
            print(f"Ошибка получения данных столбца для редактирования: {e}")
            messagebox.showerror("Ошибка", "Не удалось получить данные для редактирования.")
            return

        # Ask user for new value
        new_value = simpledialog.askstring(f"Изменить {column_name_display}",
                                           f"Введите новое значение для '{column_name_display}' (Дорожка ID: {track_path_id}):",
                                           initialvalue=current_value)

        # If user entered a value (didn't cancel)
        if new_value is not None:
            # Optional: Add validation for language code (e.g., 3 letters)
            if column_name_internal == 'language':
                new_value = new_value.strip().lower()
                if not re.fullmatch(r'[a-z]{3}', new_value):
                    messagebox.showerror("Неверный язык",
                                         "Код языка должен состоять из 3 латинских букв (напр., eng, rus, und).")
                    return

            # Update Treeview visually
            item_values[column_index] = new_value
            self.track_tree.item(item_iid, values=item_values)

            # Update internal track_data store for command generation
            if track_path_id not in self.track_data:
                self.track_data[track_path_id] = {}
            self.track_data[track_path_id][column_name_internal] = new_value
            print(f"Сохранено изменение для {track_path_id}: {column_name_internal} = '{new_value}'")
            self.output_info.insert(tk.END,
                                    f"Обновлена метадата для {track_path_id}: {column_name_internal} = '{new_value}'\n")

    # --- Timecode and Duration Methods ---
    def validate_timecode(self, timecode: Any) -> bool:
        """
        Checks if a string is in MM:SS format (00:00 to 59:59).

        Args:
            timecode: The value to validate.

        Returns:
            True if the input is a string in MM:SS format, False otherwise.
        """
        if not isinstance(timecode, str):
            return False
        # Regex allows minutes and seconds from 00 to 59
        return re.fullmatch(r"([0-5]?\d):([0-5]\d)", timecode) is not None

    def timecode_to_seconds(self, timecode: str) -> float:
        """
        Converts an MM:SS timecode string to seconds (float).

        Args:
            timecode: The timecode string in MM:SS format.

        Returns:
            The timecode converted to seconds as a float.

        Raises:
            ValueError: If the timecode format is invalid.
        """
        if not self.validate_timecode(timecode):
            raise ValueError(f"Неверный формат таймкода: {timecode}")
        minutes, seconds = map(int, timecode.split(':'))
        return float(minutes * 60 + seconds)

    # --- Ad Timecode List Management ---
    def add_embed_timecode(self) -> None:
        """
        Adds an embedded ad entry based on the current file and timecode fields.

        Validates inputs, checks time bounds, retrieves ad duration, prevents
        duplicates, updates the internal `self.embed_ads` list (sorted),
        and refreshes the embed listbox.
        """
        timecode = self.embed_timecodes_entry.get().strip()
        embed_file = self.embed_file_entry.get().strip()

        # --- Input Validations ---
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

        # --- Timecode Bounds Check ---
        try:
            time_sec = self.timecode_to_seconds(timecode)
            # Allow inserting exactly at the end (time_sec == duration)
            if time_sec > self.main_video_duration:
                messagebox.showwarning("Предупреждение",
                                       f"Таймкод {timecode} ({time_sec:.2f}s) превышает длительность основного видео ({self.main_video_duration:.2f}s). Реклама не будет добавлена.")
                return
            elif time_sec == self.main_video_duration:
                if not messagebox.askyesno("Предупреждение",
                                           f"Таймкод {timecode} совпадает с концом видео.\nРеклама будет добавлена в самый конец.\nПродолжить?"):
                    return
        except ValueError:  # Should not happen if validate_timecode passed
            messagebox.showerror("Ошибка таймкода", f"Не удалось преобразовать таймкод: {timecode}")
            return

        # --- Get Ad Duration ---
        try:
            embed_duration = ffmpeg_utils.get_media_duration(embed_file)
            if embed_duration is None or embed_duration <= 0:
                # Check if it's an image (duration might be None/0) - maybe allow images as static ads? For now, require video duration.
                messagebox.showerror("Ошибка длительности рекламы",
                                     f"Не удалось определить положительную длительность файла рекламы:\n{embed_file}\nУбедитесь, что это действительный видеофайл.")
                return
        except FfmpegError as e:
            messagebox.showerror("Ошибка FFprobe (реклама)", f"Не удалось получить длительность рекламы:\n{e}")
            return

        # --- Duplicate Check ---
        # Check if this specific timecode is already used for *any* embed ad
        for ad in self.embed_ads:
            if ad['timecode'] == timecode:
                messagebox.showwarning("Дубликат",
                                       f"Таймкод {timecode} уже добавлен для встраиваемой рекламы.\nДважды щелкните запись в списке для удаления.")
                return

        # --- Store Ad Data ---
        ad_data = {'path': embed_file, 'timecode': timecode, 'duration': embed_duration}
        self.embed_ads.append(ad_data)
        # Sort by timecode (converted to seconds) for correct processing order later
        # This is crucial for the concat logic.
        self.embed_ads.sort(key=lambda x: self.timecode_to_seconds(x['timecode']))
        print(f"Добавлена вставка: {ad_data}")

        # --- Update GUI ---
        self._update_embed_listbox()  # Refresh the listbox to show the sorted list
        self.embed_timecodes_entry.delete(0, tk.END)  # Clear entry field for next input

    def delete_embed_timecode(self, event: tk.Event) -> None:
        """
        Handles double-click events on the embedded ad listbox to delete an item.

        Confirms deletion with the user, removes the item from `self.embed_ads`,
        and updates the listbox.

        Args:
            event: The Tkinter event object.
        """
        selected_indices = self.embed_timecodes_listbox.curselection()
        if not selected_indices:
            return  # Nothing selected

        # Get index relative to the *currently displayed* (and sorted) list
        index_to_delete = selected_indices[0]
        try:
            # Get the corresponding ad data from our internal sorted list
            ad_info = self.embed_ads[index_to_delete]
            # Confirm deletion with user
            confirm = messagebox.askyesno("Удалить вставку?",
                                          f"Удалить вставку рекламы:\n"
                                          f"Файл: {os.path.basename(ad_info['path'])}\n"
                                          f"Таймкод: {ad_info['timecode']}\n"
                                          f"Длительность: {ad_info['duration']:.2f}s")
            if confirm:
                deleted_ad = self.embed_ads.pop(index_to_delete)  # Remove using index
                print(f"Удалена вставка: {deleted_ad}")
                self._update_embed_listbox()  # Refresh listbox display
        except IndexError:
            # This might happen if the listbox and self.embed_ads get out of sync
            print(f"Ошибка индекса при удалении встроенной рекламы: индекс {index_to_delete}, список {self.embed_ads}")
            messagebox.showerror("Ошибка",
                                 "Не удалось удалить выбранную запись (ошибка синхронизации). Попробуйте обновить список.")
            self._update_embed_listbox()  # Refresh to attempt resync

    def _update_embed_listbox(self) -> None:
        """
        Helper method to clear and refresh the embedded ad listbox.

        Reads the current (sorted) state of `self.embed_ads` and populates
        the listbox with formatted strings.
        """
        self.embed_timecodes_listbox.delete(0, tk.END)
        # self.embed_ads is assumed to be sorted correctly before calling this
        for ad in self.embed_ads:
            # Display timecode, basename of the ad file, and its duration
            display_text = f"{ad['timecode']} ({os.path.basename(ad['path'])}, {ad['duration']:.2f}s)"
            self.embed_timecodes_listbox.insert(tk.END, display_text)

    def add_banner_timecode(self) -> None:
        """
        Adds a banner display timecode to the list.

        Validates the timecode format, checks bounds (optional warning),
        prevents duplicates, updates the internal `self.banner_timecodes` list (sorted),
        and refreshes the banner listbox.
        """
        timecode = self.banner_timecodes_entry.get().strip()

        if not self.validate_timecode(timecode):
            messagebox.showerror("Ошибка таймкода", "Неверный формат таймкода баннера (требуется MM:SS).")
            return

        if self.main_video_duration is None:
            messagebox.showerror("Ошибка длительности", "Сначала выберите и проанализируйте основной видеофайл.")
            return

        # Check bounds against *original* duration (user needs to consider shifts)
        try:
            time_sec = self.timecode_to_seconds(timecode)
            if time_sec >= self.main_video_duration:
                # Warn but allow adding, as the final duration might be longer due to ads
                messagebox.showwarning("Предупреждение",
                                       f"Таймкод баннера {timecode} ({time_sec:.2f}s) равен или превышает *оригинальную* длительность видео ({self.main_video_duration:.2f}s).\nУбедитесь, что это желаемое поведение, учитывая возможный сдвиг времени из-за вставок.")
        except ValueError:
            messagebox.showerror("Ошибка таймкода", f"Не удалось преобразовать таймкод: {timecode}")
            return

        # Prevent duplicates
        if timecode in self.banner_timecodes:
            messagebox.showwarning("Дубликат",
                                   f"Таймкод {timecode} уже добавлен для баннера.\nДважды щелкните запись для удаления.")
            return

        # Add and sort
        self.banner_timecodes.append(timecode)
        # Sort by timecode (converted to seconds) for clarity in listbox and processing
        self.banner_timecodes.sort(key=self.timecode_to_seconds)
        print(f"Добавлен таймкод баннера: {timecode}. Текущий список: {self.banner_timecodes}")

        # Update GUI
        self._update_banner_listbox()
        self.banner_timecodes_entry.delete(0, tk.END)  # Clear entry

    def delete_banner_timecode(self, event: tk.Event) -> None:
        """
        Handles double-click events on the banner timecode listbox to delete an item.

        Confirms deletion, removes the item from `self.banner_timecodes`,
        and updates the listbox.

        Args:
            event: The Tkinter event object.
        """
        selected_indices = self.banner_timecodes_listbox.curselection()
        if not selected_indices:
            return  # Nothing selected

        index = selected_indices[0]
        try:
            tc_to_remove = self.banner_timecodes_listbox.get(index)  # Get the string MM:SS directly from listbox
            # Confirm deletion
            if messagebox.askyesno("Удалить таймкод?", f"Удалить таймкод баннера: {tc_to_remove}?"):
                if tc_to_remove in self.banner_timecodes:
                    self.banner_timecodes.remove(tc_to_remove)
                    print(f"Удален таймкод баннера: {tc_to_remove}. Текущий список: {self.banner_timecodes}")
                else:
                    # Should not happen if listbox reflects self.banner_timecodes
                    print(f"Предупреждение: Таймкод {tc_to_remove} не найден во внутреннем списке для удаления.")
                # No need to re-sort after removal, just update listbox
                self._update_banner_listbox()
        except (IndexError, tk.TclError) as e:
            # Catch potential errors accessing listbox or internal list
            print(f"Ошибка при удалении таймкода баннера: индекс {index}, Ошибка: {e}")
            messagebox.showerror("Ошибка", "Не удалось удалить выбранную запись.")
            self._update_banner_listbox()  # Refresh to be safe

    def _update_banner_listbox(self) -> None:
        """
        Helper method to clear and refresh the banner timecode listbox.

        Reads the current (sorted) state of `self.banner_timecodes` and
        populates the listbox.
        """
        self.banner_timecodes_listbox.delete(0, tk.END)
        # self.banner_timecodes is assumed to be sorted correctly before calling this
        for tc in self.banner_timecodes:
            self.banner_timecodes_listbox.insert(tk.END, tc)

    # --- Command Generation and Execution ---

    def _prepare_and_generate_commands(self) -> Optional[Tuple[List[str], str, List[str]]]:
        """
        Validates all necessary user inputs and application state.

        If valid, calls `ffmpeg_utils.generate_ffmpeg_commands` to get the
        preprocessing and main FFmpeg commands, along with a list of temp files.
        Stores the temp file list in `self.temp_files_to_clean`.

        Returns:
            A tuple (preprocessing_cmds, main_cmd, temp_files_list) on success.
            None if validation fails or command generation raises an expected error
            (which is handled with a messagebox).

        Raises:
            Catches and shows message boxes for CommandGenerationError, FfmpegError,
            and unexpected Exceptions during command generation. Cleans up temp
            files if an error occurs.
        """
        self.cleanup_temp_files()  # Start with a clean slate for temp files for this run
        self.output_info.delete('1.0', tk.END)  # Clear log area before generation attempt
        self.output_info.insert('1.0', "Подготовка и генерация команд FFmpeg...\n")
        self.master.update_idletasks()

        input_file = self.input_file_entry.get().strip()
        output_file = self.output_file_entry.get().strip()
        encoding_params_str = self.encoding_entry.get().strip()
        banner_file = self.banner_file_entry.get().strip() or None  # Use None if empty
        moving_file = self.moving_file_entry.get().strip() or None  # Use None if empty

        # --- Input Validations ---
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
            # Clear the field if file not found? Or just warn? Let's just warn.
            self.output_info.insert(tk.END,
                                    f"ПРЕДУПРЕЖДЕНИЕ: Файл баннера '{banner_file}' не найден, он будет проигнорирован.\n")
            banner_file = None  # Ignore non-existent file
        if moving_file and not os.path.exists(moving_file):
            error_messages.append(f"- Файл движущейся рекламы не найден: {moving_file}")
            self.output_info.insert(tk.END,
                                    f"ПРЕДУПРЕЖДЕНИЕ: Файл движ. рекламы '{moving_file}' не найден, он будет проигнорирован.\n")
            moving_file = None  # Ignore non-existent file
        if banner_file and not self.banner_timecodes:
            self.output_info.insert(tk.END,
                                    "ПРЕДУПРЕЖДЕНИЕ: Выбран файл баннера, но не указаны таймкоды показа. Баннер не будет добавлен.\n")
            # We could add a message box, but maybe just log is sufficient here. Filter logic will skip it.

        if error_messages:
            full_error_msg = "Пожалуйста, исправьте следующие ошибки:\n" + "\n".join(error_messages)
            messagebox.showerror("Ошибка валидации", full_error_msg)
            self.output_info.insert(tk.END, f"ОШИБКА ВАЛИДАЦИИ:\n{full_error_msg}\n")
            return None

        # --- Generate Commands using ffmpeg_utils ---
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
            result = ffmpeg_utils.generate_ffmpeg_commands(
                input_file=input_file,
                output_file=output_file,
                encoding_params_str=encoding_params_str,
                # main_video_params=self.main_video_params,
                # main_video_duration=self.main_video_duration,
                track_data=self.track_data,
                embed_ads=self.embed_ads,
                banner_file=banner_file,
                banner_timecodes=self.banner_timecodes,  # Pass even if empty, function handles it
                moving_file=moving_file
                # Other params like speed, codec constants use defaults in ffmpeg_utils
            )
            # Store temp files generated for cleanup later
            # result is (preproc_cmds, main_cmd, temp_files_list)
            self.temp_files_to_clean = result[2] if result and len(result) > 2 else []
            self.output_info.insert(tk.END, "Команды успешно сгенерированы.\n")
            print(f"Сгенерированные временные файлы: {self.temp_files_to_clean}")
            return result

        except (CommandGenerationError, FfmpegError) as e:
            error_msg = f"Ошибка генерации команды:\n{e}"
            messagebox.showerror("Ошибка генерации", error_msg)
            self.output_info.insert(tk.END, f"ОШИБКА ГЕНЕРАЦИИ КОМАНДЫ:\n{error_msg}\n")
            self.cleanup_temp_files()  # Clean up any partial temp files
            return None
        except Exception as e:
            # Catch unexpected errors
            error_msg = f"Произошла неожиданная ошибка при генерации команд:\n{type(e).__name__}: {e}"
            messagebox.showerror("Неожиданная ошибка", error_msg)
            self.output_info.insert(tk.END, f"НЕОЖИДАННАЯ ОШИБКА:\n{error_msg}\n")
            # Include traceback in console for debugging
            import traceback
            traceback.print_exc()
            self.cleanup_temp_files()
            return None

    def show_ffmpeg_commands(self) -> None:
        """
        Generates the FFmpeg commands using `_prepare_and_generate_commands`
        and displays them in the output text area for user review.

        Does not execute the commands.
        """
        # Generate commands (handles validation and potential errors)
        result = self._prepare_and_generate_commands()
        # Clear previous output regardless of success/failure of generation
        self.output_info.delete('1.0', tk.END)

        if result:
            preproc_cmds, main_cmd, temp_files_generated = result
            output_text = "--- Временные файлы для создания ---\n"
            if temp_files_generated:
                # Display only basenames for brevity in GUI
                output_text += "\n".join([f"  - {os.path.basename(f)}" for f in temp_files_generated])
            else:
                output_text += "  (нет)"
            output_text += "\n\n"

            if preproc_cmds:
                output_text += f"--- Команды предварительной обработки ({len(preproc_cmds)}) ---\n"
                for i, cmd in enumerate(preproc_cmds):
                    # Add line breaks for better readability of long commands
                    # Simple word wrap might be complex, just add separators
                    output_text += f"[{i + 1}]: {cmd}\n{'-' * 40}\n"
                output_text += "\n"
            else:
                output_text += "--- Нет команд предварительной обработки ---\n\n"

            if main_cmd:
                output_text += "--- Основная команда конвертации ---\n"
                output_text += main_cmd + "\n"
            else:
                # Should not happen if _prepare_and_generate_commands succeeded, but check anyway
                output_text += "--- ОШИБКА: Не удалось сгенерировать основную команду ---"

            self.output_info.insert('1.0', output_text)
            # Scroll to top
            self.output_info.yview_moveto(0.0)
        else:
            # Error messages were already shown by _prepare_and_generate_commands
            self.output_info.insert('1.0',
                                    "Ошибка генерации команд. Проверьте настройки и сообщения об ошибках выше/в консоли.")
        # Do not clean up temp files here, they are needed if user presses "Start Conversion" next

    def start_conversion(self) -> None:
        """
        Initiates the video conversion process.

        1. Prepares and generates FFmpeg commands using `_prepare_and_generate_commands`.
        2. Asks the user for confirmation.
        3. Executes preprocessing commands (if any) one by one using `ffmpeg_utils.run_ffmpeg_command`.
        4. Executes the main conversion command using `ffmpeg_utils.run_ffmpeg_command`.
        5. Displays progress and logs in the output text area.
        6. Shows success or error messages.
        7. Cleans up temporary files in a `finally` block.
        """
        # Step 1: Generate Commands (handles validation and errors)
        result = self._prepare_and_generate_commands()

        if not result:
            messagebox.showerror("Отмена", "Не удалось подготовить команды FFmpeg. Конвертация отменена.")
            # _prepare_and_generate_commands already cleaned up temps if needed
            return

        preproc_cmds, main_cmd, _ = result  # Temp file list is already stored in self.temp_files_to_clean

        # --- Step 2: Confirmation ---
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
            self.cleanup_temp_files()  # Clean up temp files if user cancels here
            return

        # --- Step 3: Execute Steps ---
        self.output_info.delete('1.0', tk.END)  # Clear log area for execution output
        self.output_info.insert('1.0', "Начало процесса конвертации...\n\n")
        self.master.update()  # Show initial message

        try:
            # --- Execute Pre-processing Commands ---
            if preproc_cmds:
                self.output_info.insert(tk.END,
                                        f"--- Этап 1: Предварительная обработка ({len(preproc_cmds)} команд) ---\n")
                self.master.update()
                start_time_preproc = time.time()
                for i, cmd in enumerate(preproc_cmds):
                    step_name = f"Предварительная обработка {i + 1}/{len(preproc_cmds)}"
                    self.output_info.insert(tk.END, f"\nЗапуск: {step_name}...\n")
                    self.output_info.see(tk.END)  # Scroll down
                    self.master.update()
                    start_time_step = time.time()
                    # Run command using utility function (raises ConversionError on failure)
                    ffmpeg_utils.run_ffmpeg_command(cmd, step_name)
                    end_time_step = time.time()
                    # Append success message or let error propagate
                    self.output_info.insert(tk.END,
                                            f"Успешно завершено: {step_name} (за {end_time_step - start_time_step:.2f} сек)\n")
                    self.output_info.see(tk.END)
                    self.master.update()  # Update GUI frequently during long steps
                end_time_preproc = time.time()
                self.output_info.insert(tk.END,
                                        f"\n--- Предварительная обработка завершена (общее время: {end_time_preproc - start_time_preproc:.2f} сек) ---\n")

            # --- Execute Main Conversion Command ---
            if main_cmd:
                step_name = "Основная конвертация"
                self.output_info.insert(tk.END, f"\n--- Этап 2: {step_name} ---\n")
                self.output_info.see(tk.END)
                self.master.update()
                start_time_main = time.time()
                # Run command (raises ConversionError on failure)
                ffmpeg_utils.run_ffmpeg_command(main_cmd, step_name)
                end_time_main = time.time()
                self.output_info.insert(tk.END,
                                        f"\nУспешно завершено: {step_name} (за {end_time_main - start_time_main:.2f} сек)\n")
                self.output_info.see(tk.END)
                self.master.update()
            else:
                # Should have been caught by validation, but handle defensively
                raise ConversionError("Нет основной команды FFmpeg для выполнения.")

            # --- Success ---
            success_msg = "\n--- УСПЕХ: Конвертация успешно завершена! ---"
            self.output_info.insert(tk.END, success_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showinfo("Успех", "Конвертация успешно завершена!")

        except ConversionError as e:
            # Error message is already formatted well in the exception
            error_msg = f"\n--- ОШИБКА КОНВЕРТАЦИИ ---\n{e}\n--- КОНВЕРТАЦИЯ ПРЕРВАНА ---"
            print(error_msg)  # Log full error to console
            self.output_info.insert(tk.END, error_msg + "\n")
            self.output_info.see(tk.END)
            # Show a simplified version in the messagebox
            messagebox.showerror("Сбой конвертации", f"Произошла ошибка во время конвертации:\n\n{e}")
        except Exception as e:
            # Catch unexpected errors during the process (e.g., issues with GUI updates)
            error_msg = f"\n--- НЕОЖИДАННАЯ ОШИБКА ---\n{type(e).__name__}: {e}\n--- КОНВЕРТАЦИЯ ПРЕРВАНА ---"
            print(error_msg)
            import traceback
            traceback.print_exc()  # Print stack trace to console
            self.output_info.insert(tk.END, error_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showerror("Критический сбой",
                                 f"Произошла непредвиденная ошибка:\n{type(e).__name__}: {e}\n\nПроверьте консоль для деталей.")

        finally:
            # --- Final Cleanup ---
            self.output_info.insert(tk.END, "\nЗапуск финальной очистки временных файлов...\n")
            self.output_info.see(tk.END)
            self.master.update()
            self.cleanup_temp_files()  # Clean up all accumulated temp files
            self.output_info.insert(tk.END, "Очистка завершена.\n")
            self.output_info.see(tk.END)

# --- Main Application Execution ---
# (Keep the __main__ block if you want to run this file directly)
# if __name__ == "__main__":
#     root = tk.Tk()
#     # Optional: Set minimum size, theme, etc.
#     # root.minsize(600, 400)
#     # style = ttk.Style(root)
#     # style.theme_use('clam') # Or 'alt', 'default', 'vista', etc.
#     app = VideoConverterGUI(root)
#     root.mainloop()
