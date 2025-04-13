# converter/gui.py
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
import os
import re
import shlex # Keep for metadata quoting

# Import functions from the sibling module
from . import ffmpeg_utils
from .exceptions import FfmpegError, CommandGenerationError, ConversionError # Import custom exceptions

class VideoConverterGUI:
    def __init__(self, master):
        self.master = master
        master.title("Простой Конвертер Видео (Concat Demuxer)") # Title updated

        self.notebook = ttk.Notebook(master)

        self.main_tab = ttk.Frame(self.notebook)
        self.advertisement_tab = ttk.Frame(self.notebook)
        self.transcode_tab = ttk.Frame(self.notebook)
        self.start_tab = ttk.Frame(self.notebook)

        # --- Common Data ---
        self.track_data = {} # Stores user edits { "0:1": {"title": "New Title", "language": "eng"} }
        self.main_video_duration = None
        self.main_video_params = {} # Store key params from ffprobe
        self.embed_ads: list[dict] = [] # List of dicts: {'path': str, 'timecode': str (MM:SS), 'duration': float}
        self.banner_timecodes: list[str] = [] # List of str (MM:SS)
        self.temp_files_to_clean = [] # Store paths of temp files

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

    def _create_main_tab_widgets(self):
        # Input File
        self.input_file_label = tk.Label(self.main_tab, text="Входной файл:")
        self.input_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.input_file_entry = tk.Entry(self.main_tab, width=50)
        self.input_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.input_file_button = tk.Button(self.main_tab, text="Выбрать", command=self.browse_input_file)
        self.input_file_button.grid(row=0, column=2, padx=5, pady=5)

        # Track Tree
        self.track_label = tk.Label(self.main_tab, text="Дорожки (дважды щелкните Название/Язык для редактирования):") # Hint added
        self.track_label.grid(row=1, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        self.track_tree = ttk.Treeview(self.main_tab,
                                       columns=("id", "type", "details", "title", "language"), # Simplified columns
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
        # Scrollbar for Treeview
        tree_scrollbar = ttk.Scrollbar(self.main_tab, orient="vertical", command=self.track_tree.yview)
        tree_scrollbar.grid(row=2, column=3, sticky='ns')
        self.track_tree.configure(yscrollcommand=tree_scrollbar.set)

        self.main_tab.grid_rowconfigure(2, weight=1)
        self.main_tab.grid_columnconfigure(1, weight=1)
        self.track_tree.bind("<Double-1>", self.edit_track_data)

    def _create_advertisement_tab_widgets(self):
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
        self.embed_timecodes_listbox.bind("<Double-1>", self.delete_embed_timecode) # Double-click to delete
        embed_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical", command=self.embed_timecodes_listbox.yview)
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
        self.banner_timecodes_listbox.bind("<Double-1>", self.delete_banner_timecode) # Double-click to delete
        banner_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical", command=self.banner_timecodes_listbox.yview)
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

    def _create_transcode_tab_widgets(self):
        # Encoding Params
        self.encoding_label = tk.Label(self.transcode_tab, text="Дополнительные параметры:") # Clarified
        self.encoding_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.encoding_entry = tk.Entry(self.transcode_tab, width=50)
        # Default using NVENC. Use 'libx264 -preset medium -crf 23' if NVENC unavailable/not desired
        self.encoding_entry.insert(0, "-c:v h264_nvenc -preset p6 -tune hq -cq 23 -b:v 0 -c:a aac -b:a 192k")
        self.encoding_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")

    def _create_start_tab_widgets(self):
        # Output File
        self.output_file_label = tk.Label(self.start_tab, text="Выходной файл:")
        self.output_file_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.output_file_entry = tk.Entry(self.start_tab, width=50)
        self.output_file_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")
        self.output_file_button = tk.Button(self.start_tab, text="Выбрать", command=self.browse_output_file)
        self.output_file_button.grid(row=4, column=2, padx=5, pady=5)

        # Generate/Show Command Button
        self.generate_command_button = tk.Button(self.start_tab, text="Показать команды FFmpeg", # Updated text
                                                 command=self.show_ffmpeg_commands) # Updated command
        self.generate_command_button.grid(row=5, column=0, columnspan=3, pady=10)

        # Output Info Text Area
        self.output_info_label = tk.Label(self.start_tab, text="Команды FFmpeg и Лог:") # Updated text
        self.output_info_label.grid(row=6, column=0, padx=5, pady=2, sticky="w")
        self.output_info = tk.Text(self.start_tab, height=12, wrap=tk.WORD) # Increased height
        self.output_info.grid(row=7, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
        self.output_scrollbar = tk.Scrollbar(self.start_tab, command=self.output_info.yview)
        self.output_scrollbar.grid(row=7, column=3, sticky='nsew')
        self.output_info['yscrollcommand'] = self.output_scrollbar.set
        self.start_tab.grid_rowconfigure(7, weight=2) # Give more weight to text area

        # Start Conversion Button
        self.start_conversion_button = tk.Button(self.start_tab, text="Начать конвертацию",
                                                 command=self.start_conversion)
        self.start_conversion_button.grid(row=8, column=0, columnspan=3, pady=5)

    def on_closing(self):
        """Cleanup temporary files before closing."""
        self.cleanup_temp_files()
        self.master.destroy()

    def cleanup_temp_files(self):
        """Safely remove all tracked temporary files."""
        if not self.temp_files_to_clean:
            return
        print(f"Начинаю очистку временных файлов ({len(self.temp_files_to_clean)})...")
        cleaned_count = 0
        # Make a copy in case list is modified during iteration (less likely here)
        files_to_remove = list(self.temp_files_to_clean)
        self.temp_files_to_clean.clear() # Clear original list immediately

        for temp_file in files_to_remove:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    # print(f"Удален временный файл: {temp_file}")
                    cleaned_count += 1
                except OSError as e:
                    print(f"Ошибка удаления временного файла {temp_file}: {e}")
                except Exception as e:
                     print(f"Неожиданная ошибка при удалении временного файла {temp_file}: {e}")
        print(f"Очистка завершена. Удалено {cleaned_count} из {len(files_to_remove)} файлов.")


    def _clear_state(self):
        """Resets internal state when loading a new file or on error."""
        self.cleanup_temp_files()
        self.input_file_entry.delete(0, tk.END)
        self.output_file_entry.delete(0, tk.END)
        self.track_data = {}
        self.main_video_duration = None
        self.main_video_params = {}
        self.embed_ads = []
        self.banner_timecodes = []
        self.embed_timecodes_listbox.delete(0, tk.END)
        self.banner_timecodes_listbox.delete(0, tk.END)
        self.embed_file_entry.delete(0, tk.END)
        self.banner_file_entry.delete(0, tk.END)
        self.moving_file_entry.delete(0, tk.END)
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)
        self.output_info.delete('1.0', tk.END)
        self.master.update_idletasks() # Ensure GUI clears visually


    # --- Browse Methods ---
    def browse_input_file(self):
        self._clear_state() # Clear everything before loading new

        file_path = filedialog.askopenfilename(
            title="Выберите входной видеофайл",
            filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")])
        if not file_path:
            return # User cancelled

        self.input_file_entry.insert(0, file_path)
        # Suggest output file name
        base, ext = os.path.splitext(file_path)
        suggested_output = f"{base}_converted{ext}"
        # Avoid overwriting if suggested name exists, unless user confirms later
        counter = 1
        while os.path.exists(suggested_output):
            suggested_output = f"{base}_converted_{counter}{ext}"
            counter += 1
        self.output_file_entry.insert(0, suggested_output)

        # Populate track table and get essential info using ffmpeg_utils
        try:
            self.populate_track_table(file_path) # Handles ffprobe calls internally now
            self.main_video_params = ffmpeg_utils.get_essential_stream_params(file_path)
            if not self.main_video_params:
                 messagebox.showwarning("Параметры", "Не удалось получить все ключевые параметры основного видео (разрешение, fps и т.д.) с помощью ffprobe. Некоторые функции (например, вставка рекламы) могут работать некорректно.")
                 # Allow proceeding, but concat might fail later if params are truly missing
            else:
                print("Основные параметры видео для совместимости:", self.main_video_params)
                # Set main duration from params if available, otherwise keep from populate_track_table
                if self.main_video_params.get('width') is None: # Check if video params were actually found
                     messagebox.showerror("Ошибка видео", "Не удалось определить параметры видеопотока. Выберите другой файл.")
                     self._clear_state()


        except FfmpegError as e:
            messagebox.showerror("Ошибка FFprobe", f"Не удалось проанализировать входной файл:\n{e}")
            self._clear_state() # Clear the invalid input

    def browse_output_file(self):
        default_name = ""
        current_output = self.output_file_entry.get()
        if current_output:
            default_name = os.path.basename(current_output)
        elif self.input_file_entry.get():
             base, ext = os.path.splitext(self.input_file_entry.get())
             default_name = f"{os.path.basename(base)}_converted{ext}"

        file_path = filedialog.asksaveasfilename(
            title="Выберите выходной файл",
            initialfile=default_name,
            defaultextension=".mkv", # Default to MKV as it's flexible
            filetypes=[("MKV Video", "*.mkv"), ("MP4 Video", "*.mp4"), ("All Files", "*.*")])
        if file_path:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, file_path)

    def browse_ad_file(self, entry_widget, image=False, video_only=False):
        """Browses for an ad file and updates the entry."""
        if video_only:
             filetypes = [("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")]
        elif image:
            filetypes = [("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All Files", "*.*")]
        else: # Allow video or image for banners
            filetypes = [("Media Files", "*.mp4 *.avi *.mkv *.mov *.webm *.png *.jpg *.jpeg *.bmp *.gif"),
                         ("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"),
                         ("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif"),
                         ("All Files", "*.*")]
        file_path = filedialog.askopenfilename(title="Выберите файл рекламы", filetypes=filetypes)
        if file_path:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, file_path)

    # --- Track Table Methods ---
    def populate_track_table(self, file_path):
        """Populates the track table using ffmpeg_utils.get_stream_info."""
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)
        self.main_video_duration = None # Reset duration

        try:
            # Use the utility function (raises FfprobeError on failure)
            output = ffmpeg_utils.get_stream_info(file_path)

            # Get duration from format section first
            format_duration_str = output.get("format", {}).get("duration")
            if format_duration_str:
                try:
                    self.main_video_duration = float(format_duration_str)
                    print(f"Основная длительность (формат): {self.main_video_duration:.3f}s")
                except (ValueError, TypeError): pass

            # Populate treeview
            for i, stream in enumerate(output.get("streams", [])):
                stream_index = stream.get('index', i)
                track_id = f"0:{stream_index}" # Assume input 0
                track_type = stream.get("codec_type", "N/A")
                track_title = stream.get("tags", {}).get("title", "")
                track_language = stream.get("tags", {}).get("language", "und") # Default und

                details = []
                if track_type == "video":
                    details.append(f"{stream.get('codec_name', 'N/A')}")
                    details.append(f"{stream.get('width')}x{stream.get('height')}")
                    details.append(f"{stream.get('pix_fmt')}")
                    fps = stream.get('r_frame_rate', 'N/A')
                    details.append(f"{fps} fps")
                    # Try to get duration from stream if format duration failed
                    if self.main_video_duration is None:
                        stream_dur_str = stream.get("duration")
                        if stream_dur_str:
                             try:
                                 self.main_video_duration = float(stream_dur_str)
                                 print(f"Основная длительность (видеопоток): {self.main_video_duration:.3f}s")
                             except (ValueError, TypeError): pass

                elif track_type == "audio":
                    details.append(f"{stream.get('codec_name', 'N/A')}")
                    details.append(f"{stream.get('sample_rate')} Hz")
                    details.append(f"{stream.get('channel_layout', 'N/A')}")
                    details.append(f"{stream.get('sample_fmt')}")
                elif track_type == "subtitle":
                    details.append(f"{stream.get('codec_name', 'N/A')}")
                else:
                     details.append(f"{stream.get('codec_name', 'N/A')}")

                # Format details string
                details_str = ", ".join(filter(None, map(str, details)))

                # Insert into tree
                self.track_tree.insert("", tk.END, values=(track_id, track_type, details_str, track_title, track_language))

            if self.main_video_duration is None:
                 messagebox.showwarning("Длительность", "Не удалось определить основную длительность видео из ffprobe.")
                 # Proceed, but time calculations might be wrong

        except FfmpegError as e:
             # Let the calling function handle showing the error and clearing state
             raise # Re-raise the exception


    def edit_track_data(self, event):
        """Allows editing Title and Language columns in the track tree."""
        item_id = self.track_tree.identify_row(event.y)
        column_id = self.track_tree.identify_column(event.x)

        if not item_id or not column_id:
            return

        column_name_internal = self.track_tree.column(column_id, "id") # Get the internal name ('title', 'language')

        if column_name_internal not in {'title', 'language'}:
            # messagebox.showinfo("Редактирование", f"Редактирование столбца '{column_name_display}' не поддерживается.")
            return # Silently ignore clicks on non-editable columns

        item_values = list(self.track_tree.item(item_id, "values"))
        track_path = item_values[0] # The '0:x' ID
        column_index = self.track_tree['columns'].index(column_name_internal) # Get index based on internal name
        current_value = item_values[column_index]
        column_name_display = self.track_tree.heading(column_id)['text']


        new_value = simpledialog.askstring(f"Изменить {column_name_display}",
                                           f"Введите новое значение для '{column_name_display}' (ID: {track_path}):",
                                           initialvalue=current_value)

        if new_value is not None: # User didn't cancel
            # Update Treeview
            item_values[column_index] = new_value
            self.track_tree.item(item_id, values=item_values)

            # Update internal track_data store for later use in command generation
            if track_path not in self.track_data:
                self.track_data[track_path] = {}
            self.track_data[track_path][column_name_internal] = new_value
            print(f"Сохранено изменение для {track_path}: {column_name_internal} = '{new_value}'")


    # --- Timecode and Duration Methods ---
    def validate_timecode(self, timecode):
        """Checks if a string is in MM:SS format."""
        if not isinstance(timecode, str): return False
        return re.fullmatch(r"([0-5]?\d):([0-5]\d)", timecode) is not None

    def timecode_to_seconds(self, timecode):
        """Converts MM:SS string to seconds (float). Raises ValueError on invalid format."""
        if not self.validate_timecode(timecode):
            raise ValueError(f"Неверный формат таймкода: {timecode}")
        minutes, seconds = map(int, timecode.split(':'))
        return float(minutes * 60 + seconds)

    # --- Ad Timecode List Management ---
    def add_embed_timecode(self):
        timecode = self.embed_timecodes_entry.get().strip()
        embed_file = self.embed_file_entry.get().strip()

        if not self.validate_timecode(timecode):
             messagebox.showerror("Ошибка таймкода", "Неверный формат таймкода для вставки (MM:SS).")
             return
        if not embed_file:
             messagebox.showerror("Ошибка файла", "Выберите файл для встраиваемой рекламы.")
             return
        if not os.path.exists(embed_file):
             messagebox.showerror("Ошибка файла", f"Файл рекламы не найден:\n{embed_file}")
             return
        if self.main_video_duration is None:
             messagebox.showerror("Ошибка длительности", "Сначала выберите и проанализируйте основной видеофайл.")
             return

        # Check timecode bounds (allow inserting exactly at the end)
        time_sec = self.timecode_to_seconds(timecode)
        if time_sec > self.main_video_duration:
            messagebox.showwarning("Предупреждение", f"Таймкод {timecode} ({time_sec:.2f}s) превышает длительность основного видео ({self.main_video_duration:.2f}s). Реклама не будет добавлена.")
            return
        elif time_sec == self.main_video_duration:
             if not messagebox.askyesno("Предупреждение", f"Таймкод {timecode} совпадает с концом видео.\nРеклама будет добавлена в самый конец.\nПродолжить?"):
                 return


        # Get ad duration using the utility function
        embed_duration = ffmpeg_utils.get_media_duration(embed_file)
        if embed_duration is None or embed_duration <= 0:
             messagebox.showerror("Ошибка длительности рекламы", f"Не удалось определить положительную длительность файла рекламы:\n{embed_file}\nУбедитесь, что это действительный видеофайл.")
             return

        # Check for duplicate timecode entry (allowing same file at different times)
        for ad in self.embed_ads:
             if ad['timecode'] == timecode:
                 messagebox.showwarning("Дубликат", f"Таймкод {timecode} уже добавлен для встраиваемой рекламы.\nДважды щелкните запись в списке для удаления.")
                 return

        # Store ad data
        ad_data = {'path': embed_file, 'timecode': timecode, 'duration': embed_duration}
        self.embed_ads.append(ad_data)
        # Sort by timecode (converted to seconds) for correct processing order later
        self.embed_ads.sort(key=lambda x: self.timecode_to_seconds(x['timecode']))

        # Update listbox
        self._update_embed_listbox()
        self.embed_timecodes_entry.delete(0, tk.END) # Clear entry field

    def delete_embed_timecode(self, event):
        """Deletes selected item from the embed ad list."""
        selected_indices = self.embed_timecodes_listbox.curselection()
        if selected_indices:
            # Get index relative to the *sorted* internal list
            index_to_delete = selected_indices[0]
            try:
                # Confirm deletion
                ad_info = self.embed_ads[index_to_delete]
                confirm = messagebox.askyesno("Удалить вставку?",
                                             f"Удалить вставку рекламы:\n"
                                             f"Файл: {os.path.basename(ad_info['path'])}\n"
                                             f"Таймкод: {ad_info['timecode']}\n"
                                             f"Длительность: {ad_info['duration']:.2f}s")
                if confirm:
                    del self.embed_ads[index_to_delete]
                    self._update_embed_listbox() # Refresh listbox
            except IndexError:
                 print(f"Ошибка индекса при удалении встроенной рекламы: {index_to_delete}")
                 messagebox.showerror("Ошибка", "Не удалось удалить выбранную запись.")
                 self._update_embed_listbox() # Refresh to be safe

    def _update_embed_listbox(self):
        """Helper to refresh the embed listbox based on self.embed_ads."""
        self.embed_timecodes_listbox.delete(0, tk.END)
        for ad in self.embed_ads:
            # Display timecode, basename, and duration
            display_text = f"{ad['timecode']} ({os.path.basename(ad['path'])}, {ad['duration']:.2f}s)"
            self.embed_timecodes_listbox.insert(tk.END, display_text)

    def add_banner_timecode(self):
        timecode = self.banner_timecodes_entry.get().strip()
        if self.validate_timecode(timecode):
             if self.main_video_duration is None:
                 messagebox.showerror("Ошибка длительности", "Сначала выберите и проанализируйте основной видеофайл.")
                 return

             time_sec = self.timecode_to_seconds(timecode)
             if time_sec >= self.main_video_duration:
                 messagebox.showwarning("Предупреждение", f"Таймкод баннера {timecode} ({time_sec:.2f}s) равен или превышает *оригинальную* длительность видео ({self.main_video_duration:.2f}s).\nУчтите сдвиг от вставок при проверке.")
                 # Allow adding it anyway, filter might handle it

             if timecode not in self.banner_timecodes:
                self.banner_timecodes.append(timecode)
                # Sort by timecode for clarity in listbox and processing
                self.banner_timecodes.sort(key=self.timecode_to_seconds)
                self._update_banner_listbox()
                self.banner_timecodes_entry.delete(0, tk.END)
             else:
                messagebox.showwarning("Дубликат", f"Таймкод {timecode} уже добавлен для баннера.\nДважды щелкните запись для удаления.")
        else:
            messagebox.showerror("Ошибка таймкода", "Неверный формат таймкода баннера (MM:SS).")

    def delete_banner_timecode(self, event):
        """Deletes selected item from the banner timecode list."""
        selected_indices = self.banner_timecodes_listbox.curselection()
        if selected_indices:
            index = selected_indices[0]
            try:
                tc_to_remove = self.banner_timecodes_listbox.get(index) # Get the string MM:SS
                # Confirm deletion
                if messagebox.askyesno("Удалить таймкод?", f"Удалить таймкод баннера: {tc_to_remove}?"):
                    if tc_to_remove in self.banner_timecodes:
                         self.banner_timecodes.remove(tc_to_remove)
                    # No need to re-sort after removal, just update listbox
                    self._update_banner_listbox()
            except (IndexError, tk.TclError):
                 print(f"Ошибка при удалении таймкода баннера: индекс {index}")
                 messagebox.showerror("Ошибка", "Не удалось удалить выбранную запись.")
                 self._update_banner_listbox() # Refresh to be safe

    def _update_banner_listbox(self):
        """Helper to refresh the banner listbox based on self.banner_timecodes."""
        self.banner_timecodes_listbox.delete(0, tk.END)
        # self.banner_timecodes should already be sorted
        for tc in self.banner_timecodes:
            self.banner_timecodes_listbox.insert(tk.END, tc)


    # --- Command Generation and Execution ---

    def _prepare_and_generate_commands(self):
        """Validates inputs and calls the command generation utility."""
        self.cleanup_temp_files() # Clean slate for temp files
        self.output_info.delete('1.0', tk.END) # Clear log area

        input_file = self.input_file_entry.get()
        output_file = self.output_file_entry.get()
        encoding_params_str = self.encoding_entry.get().strip()

        # --- Validations ---
        if not input_file or not output_file:
            messagebox.showerror("Ошибка", "Пожалуйста, выберите входной и выходной файлы.")
            return None
        if not os.path.exists(input_file):
             messagebox.showerror("Ошибка", f"Входной файл не найден: {input_file}")
             return None
        if self.main_video_duration is None or self.main_video_duration <= 0:
             messagebox.showerror("Ошибка", "Не удалось определить допустимую длительность основного видео. Попробуйте выбрать файл заново.")
             return None
        if not self.main_video_params or self.main_video_params.get('width') is None:
            messagebox.showerror("Ошибка параметров", "Не удалось получить параметры основного видео, необходимые для конвертации. Попробуйте выбрать файл заново.")
            return None

        banner_file = self.banner_file_entry.get().strip()
        moving_file = self.moving_file_entry.get().strip()

        # --- Generate Commands using ffmpeg_utils ---
        try:
            result = ffmpeg_utils.generate_ffmpeg_commands(
                input_file=input_file,
                output_file=output_file,
                encoding_params_str=encoding_params_str,
                main_video_params=self.main_video_params,
                main_video_duration=self.main_video_duration,
                track_data=self.track_data,
                embed_ads=self.embed_ads,
                banner_file=banner_file,
                banner_timecodes=self.banner_timecodes,
                moving_file=moving_file
            )
            # Store temp files generated for cleanup later
            self.temp_files_to_clean = result[2] if result else []
            return result # (preproc_cmds, main_cmd, temp_files_list) or None if error handled inside

        except (CommandGenerationError, FfmpegError) as e:
             messagebox.showerror("Ошибка генерации команды", f"{e}")
             self.cleanup_temp_files() # Clean up any partial temp files
             return None
        except Exception as e:
             messagebox.showerror("Неожиданная ошибка", f"Произошла неожиданная ошибка при генерации команд:\n{e}")
             self.cleanup_temp_files()
             return None


    def show_ffmpeg_commands(self):
         """Generates and displays the FFmpeg commands."""
         result = self._prepare_and_generate_commands()
         self.output_info.delete('1.0', tk.END) # Clear previous output

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
                     output_text += f"[{i+1}] {cmd}\n\n"
                 output_text += "---\n\n"
             else:
                  output_text += "--- Нет команд предварительной обработки ---\n\n"


             if main_cmd:
                 output_text += "--- Основная команда конвертации ---\n"
                 output_text += main_cmd + "\n"
             else:
                 # Should not happen if prepare succeeded, but check anyway
                 output_text += "--- ОШИБКА: Не удалось сгенерировать основную команду ---"

             self.output_info.insert('1.0', output_text)
         else:
             self.output_info.insert('1.0', "Ошибка генерации команд. Проверьте настройки и сообщения об ошибках в консоли/логе.")
         # Do not clean up temp files here, they are needed if user presses "Start"

    def start_conversion(self):
        """Generates commands and executes them step-by-step."""
        result = self._prepare_and_generate_commands()

        if not result:
            messagebox.showerror("Отмена", "Не удалось подготовить команды FFmpeg. Конвертация отменена.")
            return

        preproc_cmds, main_cmd, _ = result # Temp file list is stored in self.temp_files_to_clean

        # --- Confirmation ---
        num_preproc = len(preproc_cmds) if preproc_cmds else 0
        confirm_message = "Будут выполнены следующие шаги:\n"
        if num_preproc > 0:
             confirm_message += f"\n1. Предварительная обработка {num_preproc} сегментов/рекламы (создание временных файлов)."
        if main_cmd:
            step_num_main = "1." if num_preproc == 0 else "2."
            confirm_message += f"\n{step_num_main} Основная конвертация с объединением и наложением."
        confirm_message += "\n\nПредварительная обработка может занять значительное время."
        confirm_message += "\n\nПродолжить?"

        if not messagebox.askyesno("Подтверждение конвертации", confirm_message):
            print("Конвертация отменена пользователем.")
            self.cleanup_temp_files() # Clean up temp files if user cancels here
            return

        # --- Execute Steps ---
        self.output_info.delete('1.0', tk.END) # Clear log area for execution output
        self.output_info.insert('1.0', "Начало процесса конвертации...\n\n")
        self.master.update() # Show initial message

        try:
            # Step 1: Pre-processing
            if preproc_cmds:
                self.output_info.insert(tk.END, f"--- Шаг 1: Предварительная обработка ({len(preproc_cmds)} команд) ---\n")
                self.master.update()
                for i, cmd in enumerate(preproc_cmds):
                     step_name = f"Предварительная обработка {i+1}/{len(preproc_cmds)}"
                     self.output_info.insert(tk.END, f"\nЗапуск: {step_name}...\n")
                     self.master.update()
                     # Run command using utility function (raises ConversionError on failure)
                     ffmpeg_utils.run_ffmpeg_command(cmd, step_name)
                     # Append success message or let error propagate
                     self.output_info.insert(tk.END, f"Завершено: {step_name}\n")
                     self.master.update() # Update GUI frequently during long steps

            # Step 2: Main Conversion
            if main_cmd:
                 step_name = "Основная конвертация"
                 self.output_info.insert(tk.END, f"\n--- Шаг 2: {step_name} ---\n")
                 self.master.update()
                 # Run command (raises ConversionError on failure)
                 ffmpeg_utils.run_ffmpeg_command(main_cmd, step_name)
                 self.output_info.insert(tk.END, f"\nЗавершено: {step_name}\n")
                 self.master.update()
            else:
                 # Should have been caught earlier, but handle defensively
                 raise ConversionError("Нет основной команды для выполнения.")

            # If all steps succeeded
            self.output_info.insert(tk.END, "\n--- УСПЕХ: Конвертация успешно завершена! ---\n")
            messagebox.showinfo("Успех", "Конвертация успешно завершена!")

        except ConversionError as e:
            # Error message is already formatted in the exception
            error_msg = f"\n--- ОШИБКА КОНВЕРТАЦИИ ---\n{e}\n--- КОНВЕРТАЦИЯ ПРЕРВАНА ---"
            print(error_msg) # Log to console as well
            self.output_info.insert(tk.END, error_msg)
            messagebox.showerror("Сбой конвертации", f"Произошла ошибка:\n{e}")
        except Exception as e:
             # Catch unexpected errors during the process
             error_msg = f"\n--- НЕОЖИДАННАЯ ОШИБКА ---\n{e}\n--- КОНВЕРТАЦИЯ ПРЕРВАНА ---"
             print(error_msg)
             self.output_info.insert(tk.END, error_msg)
             messagebox.showerror("Сбой", f"Произошла непредвиденная ошибка:\n{e}")

        finally:
            # --- Cleanup ---
            self.output_info.insert(tk.END, "\nЗапуск финальной очистки временных файлов...\n")
            self.master.update()
            self.cleanup_temp_files() # Clean up all accumulated temp files
            self.output_info.insert(tk.END, "Очистка завершена.\n")