# converter/gui.py
import os
import subprocess
import time
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from typing import List, Dict, Tuple, Optional, Any

# Import local modules
from . import ffmpeg_utils, utils, config
from .exceptions import FfmpegError, CommandGenerationError, ConversionError


class VideoConverterGUI:
    """
    Main class for the Video Converter GUI application using Tkinter.
    """

    def __init__(self, master: tk.Tk):
        """
        Initializes the main application window and its components.

        Args:
            master: The root Tkinter window.
        """
        self.master = master
        self.VERSION = '0.0.1'
        self.TITLE = "JustConverter + AdBurner"
        self.AUTHOR = "dimnissv"
        master.title(f'{self.TITLE} ({self.AUTHOR}) {self.VERSION}')

        self.notebook = ttk.Notebook(master)

        # Create tabs
        self.main_tab = ttk.Frame(self.notebook)
        self.advertisement_tab = ttk.Frame(self.notebook)
        self.transcode_tab = ttk.Frame(self.notebook)
        self.start_tab = ttk.Frame(self.notebook)
        self.about_tab = ttk.Frame(self.notebook)

        # State variables
        self.track_data: Dict[str, Dict[str, str]] = {}  # Stores user edits for track metadata {track_id: {key: value}}
        self.main_video_duration: Optional[float] = None  # Duration of the input video in seconds
        self.main_video_params: Dict[str, Any] = {}  # Essential parameters of the input video
        self.embed_ads: List[
            Dict[str, Any]] = []  # List of ads to embed {'path': str, 'timecode': str, 'duration': float}
        self.banner_timecodes: List[str] = []  # List of timecodes (MM:SS) for banner display
        self.temp_files_to_clean: List[str] = []  # List of temporary files generated during the process

        # Build UI elements for each tab
        self._create_main_tab_widgets()
        self._create_advertisement_tab_widgets()
        self._create_transcode_tab_widgets()
        self._create_start_tab_widgets()
        self._create_about_tab_widgets()

        # Add tabs to the notebook
        self.notebook.add(self.main_tab, text="Files")
        self.notebook.add(self.advertisement_tab, text="Advertisement")
        self.notebook.add(self.transcode_tab, text="Transcoding")
        self.notebook.add(self.start_tab, text="Start")
        self.notebook.add(self.about_tab, text="About")

        self.notebook.grid(row=0, column=0, sticky="nsew")
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)

        # Handle window closing
        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.ffmpeg_instance: Optional[ffmpeg_utils.FFMPEG] = None  # Instance of the ffmpeg helper class

    def _create_main_tab_widgets(self) -> None:
        """Creates widgets for the 'Files' tab."""
        self.input_file_label = tk.Label(self.main_tab, text="Input File:")
        self.input_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.input_file_entry = tk.Entry(self.main_tab, width=50)
        self.input_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.input_file_button = tk.Button(self.main_tab, text="Browse", command=self.browse_input_file)
        self.input_file_button.grid(row=0, column=2, padx=5, pady=5)

        self.track_label = tk.Label(self.main_tab, text="Tracks (double-click Title/Language to edit):")
        self.track_label.grid(row=1, column=0, columnspan=3, padx=5, pady=2, sticky="w")

        # Treeview for displaying media tracks
        self.track_tree = ttk.Treeview(self.main_tab,
                                       columns=("id", "type", "details", "title", "language"),
                                       show="headings")
        self.track_tree.heading("id", text="ID")
        self.track_tree.heading("type", text="Type")
        self.track_tree.heading("details", text="Details")
        self.track_tree.heading("title", text="Title")
        self.track_tree.heading("language", text="Lang")  # Shortened for space

        self.track_tree.column("id", width=50, stretch=tk.NO, anchor='center')
        self.track_tree.column("type", width=60, stretch=tk.NO)
        self.track_tree.column("details", width=250, stretch=tk.YES)  # Increased width
        self.track_tree.column("title", width=150, stretch=tk.YES)
        self.track_tree.column("language", width=40, stretch=tk.NO, anchor='center')  # Adjusted width

        self.track_tree.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
        tree_scrollbar = ttk.Scrollbar(self.main_tab, orient="vertical", command=self.track_tree.yview)
        tree_scrollbar.grid(row=2, column=3, sticky='ns')
        self.track_tree.configure(yscrollcommand=tree_scrollbar.set)

        # Configure resizing behavior
        self.main_tab.grid_rowconfigure(2, weight=1)
        self.main_tab.grid_columnconfigure(1, weight=1)
        # Bind double-click event for editing
        self.track_tree.bind("<Double-1>", self.edit_track_data)

    def _create_advertisement_tab_widgets(self) -> None:
        """Creates widgets for the 'Advertisement' tab."""
        # --- Embedded Ads ---
        self.embed_file_label = tk.Label(self.advertisement_tab, text="Embed Ad Video:")
        self.embed_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.embed_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.embed_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.embed_file_button = tk.Button(self.advertisement_tab, text="Browse",
                                           command=lambda: self.browse_ad_file(self.embed_file_entry, video_only=True))
        self.embed_file_button.grid(row=0, column=2, padx=5, pady=5)
        self.advertisement_tab.grid_columnconfigure(1, weight=1)  # Allow entry to expand

        self.embed_timecodes_label = tk.Label(self.advertisement_tab, text="Insert Timecodes (MM:SS or HH:MM:SS):")
        self.embed_timecodes_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.embed_timecodes_entry = tk.Entry(self.advertisement_tab, width=20)
        self.embed_timecodes_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.add_embed_timecode_button = tk.Button(self.advertisement_tab, text="Add",
                                                   command=self.add_embed_timecode)
        self.add_embed_timecode_button.grid(row=1, column=2, padx=5, pady=5, sticky="w")

        # Listbox to display added embed timecodes
        self.embed_timecodes_listbox = tk.Listbox(self.advertisement_tab, width=60, height=4)
        self.embed_timecodes_listbox.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.embed_timecodes_listbox.bind("<Double-1>", self.delete_embed_timecode)  # Bind double-click to delete
        embed_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical",
                                        command=self.embed_timecodes_listbox.yview)
        embed_scrollbar.grid(row=2, column=3, sticky='ns')
        self.embed_timecodes_listbox.configure(yscrollcommand=embed_scrollbar.set)

        # --- Banner Ads ---
        self.banner_file_label = tk.Label(self.advertisement_tab, text="Banner Ad (Video/Image):")
        self.banner_file_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
        self.banner_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.banner_file_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")
        self.banner_file_button = tk.Button(self.advertisement_tab, text="Browse",
                                            command=lambda: self.browse_ad_file(self.banner_file_entry))
        self.banner_file_button.grid(row=3, column=2, padx=5, pady=5)

        self.banner_timecodes_label = tk.Label(self.advertisement_tab, text="Display Timecodes (MM:SS or HH:MM:SS):")
        self.banner_timecodes_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        self.banner_timecodes_entry = tk.Entry(self.advertisement_tab, width=20)
        self.banner_timecodes_entry.grid(row=4, column=1, padx=5, pady=5, sticky="w")
        self.add_banner_timecode_button = tk.Button(self.advertisement_tab, text="Add",
                                                    command=self.add_banner_timecode)
        self.add_banner_timecode_button.grid(row=4, column=2, padx=5, pady=5, sticky="w")

        # Listbox to display added banner timecodes
        self.banner_timecodes_listbox = tk.Listbox(self.advertisement_tab, width=60, height=4)
        self.banner_timecodes_listbox.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.banner_timecodes_listbox.bind("<Double-1>", self.delete_banner_timecode)  # Bind double-click to delete
        banner_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical",
                                         command=self.banner_timecodes_listbox.yview)
        banner_scrollbar.grid(row=5, column=3, sticky='ns')
        self.banner_timecodes_listbox.configure(yscrollcommand=banner_scrollbar.set)

        # Banner specific settings
        self.banner_track_pix_fmt_label = tk.Label(self.advertisement_tab, text='Banner Pixel Format:')
        self.banner_track_pix_fmt_label.grid(row=6, column=0, padx=5, pady=5, sticky='w')
        self.banner_track_pix_fmt_entry = tk.Entry(self.advertisement_tab, width=15)
        self.banner_track_pix_fmt_entry.grid(row=6, column=1, padx=5, pady=5, sticky='w')
        self.banner_track_pix_fmt_entry.insert(0, config.BANNER_TRACK_PIX_FMT)  # Default from config

        self.banner_gap_color_label = tk.Label(self.advertisement_tab, text='Banner Gap Color:')
        self.banner_gap_color_label.grid(row=7, column=0, padx=5, pady=5, sticky='w')
        self.banner_gap_color_entry = tk.Entry(self.advertisement_tab, width=15)
        self.banner_gap_color_entry.grid(row=7, column=1, padx=5, pady=5, sticky='w')
        self.banner_gap_color_entry.insert(0, config.BANNER_GAP_COLOR)  # Default from config

        # --- Moving Logo ---
        self.moving_file_label = tk.Label(self.advertisement_tab, text="Moving Logo (Image):")
        self.moving_file_label.grid(row=8, column=0, padx=5, pady=5, sticky="w")
        self.moving_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.moving_file_entry.grid(row=8, column=1, padx=5, pady=5, sticky="ew")
        self.moving_file_button = tk.Button(self.advertisement_tab, text="Browse",
                                            command=lambda: self.browse_ad_file(self.moving_file_entry,
                                                                                image_only=True))
        self.moving_file_button.grid(row=8, column=2, padx=5, pady=5)

        # Moving logo settings
        self.moving_speed_label = tk.Label(self.advertisement_tab, text="Moving Speed Factor:")
        self.moving_speed_label.grid(row=9, column=0, padx=5, pady=5, sticky="w")
        self.moving_speed_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_speed_entry.grid(row=9, column=1, padx=5, pady=5, sticky="w")
        self.moving_speed_entry.insert(0, str(config.MOVING_SPEED))  # Default from config

        self.moving_logo_relative_height_label = tk.Label(self.advertisement_tab, text="Logo Height (Relative):")
        self.moving_logo_relative_height_label.grid(row=10, column=0, padx=5, pady=5, sticky="w")
        self.moving_logo_relative_height_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_logo_relative_height_entry.grid(row=10, column=1, padx=5, pady=5, sticky="w")
        self.moving_logo_relative_height_entry.insert(0,
                                                      f"{config.MOVING_LOGO_RELATIVE_HEIGHT:.3f}")  # Default from config

        self.moving_logo_alpha_label = tk.Label(self.advertisement_tab, text="Logo Alpha (0.0-1.0):")
        self.moving_logo_alpha_label.grid(row=11, column=0, padx=5, pady=5, sticky="w")
        self.moving_logo_alpha_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_logo_alpha_entry.grid(row=11, column=1, padx=5, pady=5, sticky="w")
        self.moving_logo_alpha_entry.insert(0, str(config.MOVING_LOGO_ALPHA))  # Default from config

    def _create_transcode_tab_widgets(self) -> None:
        """Creates widgets for the 'Transcoding' tab."""
        # Video Settings
        self.video_codec_label = tk.Label(self.transcode_tab, text='Video Codec:')
        self.video_codec_label.grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.video_codec_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_codec_entry.grid(row=0, column=1, padx=5, pady=5, sticky='w')
        self.video_codec_entry.insert(0, config.VIDEO_CODEC)  # Default from config

        self.video_preset_label = tk.Label(self.transcode_tab, text='Preset:')
        self.video_preset_label.grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.video_preset_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_preset_entry.grid(row=1, column=1, padx=5, pady=5, sticky='w')
        self.video_preset_entry.insert(0, config.VIDEO_PRESET)  # Default from config

        self.video_cq_label = tk.Label(self.transcode_tab, text='CQ/CRF (Quality):')
        self.video_cq_label.grid(row=2, column=0, padx=5, pady=5, sticky='w')
        self.video_cq_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_cq_entry.grid(row=2, column=1, padx=5, pady=5, sticky='w')
        self.video_cq_entry.insert(0, config.VIDEO_CQ)  # Default from config

        self.video_bitrate_label = tk.Label(self.transcode_tab, text='Video Bitrate (e.g., 5000k, 0=CQ):')
        self.video_bitrate_label.grid(row=3, column=0, padx=5, pady=5, sticky='w')
        self.video_bitrate_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_bitrate_entry.grid(row=3, column=1, padx=5, pady=5, sticky='w')
        self.video_bitrate_entry.insert(0, config.VIDEO_BITRATE)  # Default from config

        self.video_fps_label = tk.Label(self.transcode_tab, text='Video FPS (Optional):')
        self.video_fps_label.grid(row=4, column=0, padx=5, pady=5, sticky='w')
        self.video_fps_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_fps_entry.grid(row=4, column=1, padx=5, pady=5, sticky='w')
        # Default FPS is blank (uses source FPS)

        # Audio Settings
        self.audio_codec_label = tk.Label(self.transcode_tab, text='Audio Codec:')
        self.audio_codec_label.grid(row=5, column=0, padx=5, pady=5, sticky='w')
        self.audio_codec_entry = tk.Entry(self.transcode_tab, width=20)
        self.audio_codec_entry.grid(row=5, column=1, padx=5, pady=5, sticky='w')
        self.audio_codec_entry.insert(0, config.AUDIO_CODEC)  # Default from config

        self.audio_bitrate_label = tk.Label(self.transcode_tab, text='Audio Bitrate (e.g., 192k):')
        self.audio_bitrate_label.grid(row=6, column=0, padx=5, pady=5, sticky='w')
        self.audio_bitrate_entry = tk.Entry(self.transcode_tab, width=20)
        self.audio_bitrate_entry.grid(row=6, column=1, padx=5, pady=5, sticky='w')
        self.audio_bitrate_entry.insert(0, config.AUDIO_BITRATE)  # Default from config

        # Hardware Acceleration
        self.hwaccel_label = tk.Label(self.transcode_tab, text="Hardware Acceleration:")
        self.hwaccel_label.grid(row=7, column=0, padx=5, pady=5, sticky="w")
        self.hwaccel_combo = ttk.Combobox(self.transcode_tab, values=["none"] + self.detect_hwaccels(),
                                          state="readonly")
        self.hwaccel_combo.grid(row=7, column=1, padx=5, pady=5, sticky="w")
        self.hwaccel_combo.set(config.HWACCEL)  # Default from config

        # Additional Parameters
        self.additional_encoding_label = tk.Label(self.transcode_tab, text="Additional Params:")
        self.additional_encoding_label.grid(row=8, column=0, padx=5, pady=5, sticky="w")
        self.additional_encoding_entry = tk.Entry(self.transcode_tab, width=60)
        self.additional_encoding_entry.grid(row=8, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self.additional_encoding_entry.insert(0, config.ADDITIONAL_ENCODING)  # Default from config

        # Manual Override
        self.encoding_label = tk.Label(self.transcode_tab, text="Manual Params (Overrides Above):")
        self.encoding_label.grid(row=9, column=0, padx=5, pady=5, sticky="w")
        self.encoding_entry = tk.Entry(self.transcode_tab, width=60)
        self.encoding_entry.grid(row=9, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        # Default is empty

        # Configure resizing
        self.transcode_tab.grid_columnconfigure(1, weight=1)

    def _create_start_tab_widgets(self) -> None:
        """Creates widgets for the 'Start' tab."""
        # Output File Selection
        self.output_file_label = tk.Label(self.start_tab, text="Output File:")
        self.output_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")  # Moved to top
        self.output_file_entry = tk.Entry(self.start_tab, width=50)
        self.output_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.output_file_button = tk.Button(self.start_tab, text="Browse", command=self.browse_output_file)
        self.output_file_button.grid(row=0, column=2, padx=5, pady=5)
        self.start_tab.grid_columnconfigure(1, weight=1)  # Allow entry to expand

        # Button to show generated commands
        self.generate_command_button = tk.Button(self.start_tab, text="Show ffmpeg Commands",
                                                 command=self.show_ffmpeg_commands)
        self.generate_command_button.grid(row=1, column=0, columnspan=3, pady=10)  # Moved up

        # Output Info/Log Area
        self.output_info_label = tk.Label(self.start_tab, text="ffmpeg Commands & Log:")
        self.output_info_label.grid(row=2, column=0, columnspan=3, padx=5, pady=2, sticky="w")  # Moved up
        self.output_info = tk.Text(self.start_tab, height=15, wrap=tk.WORD, relief=tk.SUNKEN,
                                   borderwidth=1)  # Increased height
        self.output_info.grid(row=3, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")  # Moved up
        self.output_scrollbar = tk.Scrollbar(self.start_tab, command=self.output_info.yview)
        self.output_scrollbar.grid(row=3, column=3, sticky='nsew')  # Moved up
        self.output_info['yscrollcommand'] = self.output_scrollbar.set
        self.start_tab.grid_rowconfigure(3, weight=2)  # Configure text area resizing

        # Start Button
        self.start_conversion_button = tk.Button(self.start_tab, text="Start Conversion",
                                                 command=self.start_conversion,
                                                 font=('Helvetica', 10, 'bold'),
                                                 bg="lightblue")  # Added background color
        self.start_conversion_button.grid(row=4, column=0, columnspan=3, pady=10)  # Moved up

    def _create_about_tab_widgets(self) -> None:
        """Creates widgets for the 'About' tab."""
        about_frame = ttk.LabelFrame(self.about_tab, text="About JustConverter + AdBurner", padding=10)
        about_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.about_tab.grid_columnconfigure(0, weight=1)

        info_text_content = (
            f"Program: {self.TITLE}\n"
            f"Version: {self.VERSION}\n"
            f"Author: {self.AUTHOR}\n\n"
            "This tool allows converting video files while optionally embedding advertisements, adding banner overlays, and adding moving logos using ffmpeg.\n\n"
        )

        info_label = tk.Label(about_frame, text=info_text_content, justify=tk.LEFT)
        info_label.grid(row=0, column=0, sticky="w")

        # Links Frame
        links_frame = ttk.Frame(about_frame)
        links_frame.grid(row=1, column=0, pady=10, sticky="w")

        self._insert_link(links_frame, "GitHub:", "https://github.com/DIMNISSV/JustConverter", 0)
        self._insert_link(links_frame, "Wiki:", "https://github.com/DIMNISSV/JustConverter/wiki", 1)
        self._insert_link(links_frame, "Telegram:", "https://t.me/dimnissv", 2)

    def _insert_link(self, parent: ttk.Frame, label_text: str, url: str, row_num: int) -> None:
        """Helper to insert a clickable link label."""
        label = tk.Label(parent, text=label_text, justify=tk.LEFT)
        label.grid(row=row_num, column=0, sticky="w")
        link = tk.Label(parent, text=url, fg="blue", cursor="hand2", justify=tk.LEFT)
        link.grid(row=row_num, column=1, sticky="w")
        link.bind("<Button-1>", lambda e, link_url=url: self._open_url(link_url))

    def _open_url(self, url: str) -> None:
        """Opens the given URL in a web browser."""
        import webbrowser
        try:
            webbrowser.open_new(url)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open link:\n{e}")

    def on_closing(self) -> None:
        """Handles the window close event, ensuring temporary files are cleaned up."""
        print("Close requested. Cleaning up temporary files...")
        self.cleanup_temp_files()
        self.master.destroy()

    def cleanup_temp_files(self) -> None:
        """Calls the utility function to clean up temporary files."""
        if self.temp_files_to_clean:
            utils.cleanup_temp_files(self.temp_files_to_clean)
            self.temp_files_to_clean.clear()  # Clear the list after attempting cleanup
        else:
            print("No temporary files to clean.")

    def _clear_state(self) -> None:
        """Resets the GUI state, clearing inputs and internal data."""
        print("Resetting GUI state...")
        self.cleanup_temp_files()  # Clean up any remnants first

        # Clear input fields
        self.input_file_entry.delete(0, tk.END)
        self.output_file_entry.delete(0, tk.END)
        self.embed_file_entry.delete(0, tk.END)
        self.embed_timecodes_entry.delete(0, tk.END)
        self.banner_file_entry.delete(0, tk.END)
        self.banner_timecodes_entry.delete(0, tk.END)
        self.moving_file_entry.delete(0, tk.END)

        # Reset internal data structures
        self.track_data = {}
        self.main_video_duration = None
        self.main_video_params = {}
        self.embed_ads = []
        self.banner_timecodes = []
        self.temp_files_to_clean = []

        # Clear listboxes
        self.embed_timecodes_listbox.delete(0, tk.END)
        self.banner_timecodes_listbox.delete(0, tk.END)

        # Clear track treeview
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)

        # Clear output log
        self.output_info.delete('1.0', tk.END)

        # Reset potentially derived fields to defaults (or clear)
        self.video_fps_entry.delete(0, tk.END)
        self.video_codec_entry.delete(0, tk.END)
        self.video_codec_entry.insert(0, config.VIDEO_CODEC)
        self.video_preset_entry.delete(0, tk.END)
        self.video_preset_entry.insert(0, config.VIDEO_PRESET)
        # ... reset other transcode fields if desired ...

        self.master.update_idletasks()  # Ensure UI updates
        print("State reset complete.")

    def browse_input_file(self) -> None:
        """Handles browsing for and selecting the input video file."""
        self._clear_state()  # Reset everything before loading a new file

        file_path = filedialog.askopenfilename(
            title="Select Input Video File",
            filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")])
        if not file_path:
            print("Input file selection cancelled.")
            return

        self.input_file_entry.delete(0, tk.END)  # Clear previous entry first
        self.input_file_entry.insert(0, file_path)
        print(f"Input file selected: {file_path}")

        # Suggest an output file name
        try:
            base, ext = os.path.splitext(file_path)
            suggested_output_base = f"{base}_converted"
            suggested_output = f"{suggested_output_base}{ext if ext else '.mkv'}"  # Ensure extension
            counter = 1
            # Avoid overwriting existing files by appending a counter
            while os.path.exists(suggested_output):
                suggested_output = f"{suggested_output_base}_{counter}{ext if ext else '.mkv'}"
                counter += 1
            self.output_file_entry.delete(0, tk.END)  # Clear previous entry
            self.output_file_entry.insert(0, suggested_output)
            print(f"Suggested output file: {suggested_output}")
        except Exception as e:
            print(f"Error suggesting output file name: {e}")

        # Analyze the selected file
        try:
            self.output_info.insert('1.0', f"Analyzing file: {os.path.basename(file_path)}...\n")
            self.master.update_idletasks()  # Show message immediately

            # Populate track table and get essential parameters
            self.populate_track_table(file_path)  # This also sets self.main_video_duration
            ffmpeg_helper = ffmpeg_utils.FFMPEG()  # Use default FFMPEG instance for analysis
            self.main_video_params = ffmpeg_helper.get_essential_stream_params(file_path)

            if not self.main_video_params:
                warning_msg = "Could not retrieve all key parameters from the main video."
                messagebox.showwarning("Parameter Issue", warning_msg)
                self.output_info.insert(tk.END, f"WARNING: {warning_msg}\n")
            else:
                print("Essential video parameters retrieved:", self.main_video_params)
                # Display basic info in the log
                self.output_info.insert(tk.END,
                                        f"Video Params (WxH): {self.main_video_params.get('width')}x{self.main_video_params.get('height')}, "
                                        f"FPS: {self.main_video_params.get('fps_str')}, "
                                        f"PixFmt: {self.main_video_params.get('pix_fmt')}\n")
                if self.main_video_params.get('has_audio'):
                    self.output_info.insert(tk.END,
                                            f"Audio Params: {self.main_video_params.get('sample_rate')} Hz, "
                                            f"{self.main_video_params.get('channel_layout')}, "
                                            f"Fmt: {self.main_video_params.get('sample_fmt')}\n")
                else:
                    self.output_info.insert(tk.END,
                                            "Audio Params: No audio stream detected or parameters incomplete.\n")

                # Check specifically for width, as it's critical
                if self.main_video_params.get('width') is None:
                    error_msg = "Failed to determine video stream parameters. Please select a different file."
                    messagebox.showerror("Video Error", error_msg)
                    self.output_info.insert(tk.END, f"ERROR: {error_msg}\n")
                    self._clear_state()  # Reset as the file is unusable
                    return  # Stop further processing

                # Pre-fill FPS field if available
                if self.main_video_params.get('fps_str'):
                    self.video_fps_entry.delete(0, tk.END)
                    self.video_fps_entry.insert(0, self.main_video_params.get('fps_str'))

            if self.main_video_duration is None:
                self.output_info.insert(tk.END, "WARNING: Could not determine main video duration from ffprobe.\n")

            self.output_info.insert(tk.END, "Analysis complete.\n")

        except FfmpegError as e:
            error_msg = f"Failed to analyze input file:\n{e}"
            messagebox.showerror("ffprobe Error", error_msg)
            self.output_info.insert(tk.END, f"FFPROBE ERROR: {error_msg}\n")
            self._clear_state()  # Reset on analysis failure

    def browse_output_file(self) -> None:
        """Handles browsing for and selecting the output file path."""
        default_name = ""
        initial_dir = os.getcwd()
        current_output = self.output_file_entry.get()
        input_path = self.input_file_entry.get()

        # Determine initial directory and filename for the dialog
        if current_output:
            default_name = os.path.basename(current_output)
            initial_dir = os.path.dirname(current_output) or initial_dir
        elif input_path:
            try:
                base, ext = os.path.splitext(input_path)
                default_name = f"{os.path.basename(base)}_converted.mkv"  # Suggest .mkv
                initial_dir = os.path.dirname(input_path) or initial_dir
            except Exception:
                pass  # Ignore errors in suggestion generation

        file_path = filedialog.asksaveasfilename(
            title="Select Output File",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".mkv",  # Default extension
            filetypes=[("MKV Video", "*.mkv"), ("MP4 Video", "*.mp4"), ("All Files", "*.*")])
        if file_path:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, file_path)
            print(f"Output file set to: {file_path}")
        else:
            print("Output file selection cancelled.")

    def browse_ad_file(self, entry_widget: tk.Entry, image_only: bool = False, video_only: bool = False) -> None:
        """Handles browsing for advertisement files (video or image)."""
        initial_dir = os.getcwd()
        current_ad_path = entry_widget.get()
        if current_ad_path and os.path.exists(os.path.dirname(current_ad_path)):
            initial_dir = os.path.dirname(current_ad_path)

        # Define file types based on requirements
        if image_only:
            filetypes = [("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All Files", "*.*")]
            title = "Select Image File"
        elif video_only:
            filetypes = [("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")]
            title = "Select Video File"
        else:  # Allow both video and image
            filetypes = [("Media Files", "*.mp4 *.avi *.mkv *.mov *.webm *.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                         ("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"),
                         ("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                         ("All Files", "*.*")]
            title = "Select Media File"

        file_path = filedialog.askopenfilename(title=title, initialdir=initial_dir, filetypes=filetypes)
        if file_path:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, file_path)
            # Identify which entry widget this is for logging
            widget_name = "Unknown Ad"
            if entry_widget == self.embed_file_entry:
                widget_name = "Embed Ad"
            elif entry_widget == self.banner_file_entry:
                widget_name = "Banner Ad"
            elif entry_widget == self.moving_file_entry:
                widget_name = "Moving Logo"
            print(f"File selected for '{widget_name}': {file_path}")
        else:
            print(f"File selection cancelled for '{entry_widget}'.")

    def populate_track_table(self, file_path: str) -> None:
        """Populates the track Treeview with stream information from the file."""
        # Clear existing entries
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)
        # Reset state variables related to the previous file
        self.main_video_duration = None
        self.track_data = {}

        try:
            # Get stream info using the helper class (static method call is okay here)
            stream_info = ffmpeg_utils.FFMPEG.get_stream_info(file_path)
            if not stream_info:
                messagebox.showerror("Analysis Error", f"Could not retrieve stream information from:\n{file_path}")
                return

            # Attempt to get duration from format info first
            format_duration_str = stream_info.get("format", {}).get("duration")
            if format_duration_str and format_duration_str != "N/A":
                try:
                    self.main_video_duration = float(format_duration_str)
                    print(f"Main duration from format: {self.main_video_duration:.3f}s")
                except (ValueError, TypeError) as e:
                    print(f"Error converting format duration '{format_duration_str}': {e}")

            # Iterate through streams and add them to the Treeview
            for i, stream in enumerate(stream_info.get("streams", [])):
                stream_index = stream.get('index', i)
                # Use "input_index:stream_index" format for unique ID
                track_id_str = f"0:{stream_index}"  # Assuming single input file analysis
                track_type = stream.get("codec_type", "N/A")
                tags = stream.get("tags", {})
                track_title = tags.get("title", "")  # Get title from tags
                track_language = tags.get("language", "und")  # Get language, default to 'und'

                # Build details string based on stream type
                details = []
                if track_type == "video":
                    details.append(f"{stream.get('codec_name', '?')}")
                    if stream.get('width') and stream.get('height'):
                        details.append(f"{stream.get('width')}x{stream.get('height')}")
                    if stream.get('pix_fmt'): details.append(f"{stream.get('pix_fmt')}")
                    fps = stream.get('r_frame_rate', '?')  # Frames per second
                    details.append(f"{fps} fps")
                    bitrate = stream.get('bit_rate')
                    if bitrate: details.append(f"{int(bitrate) // 1000} kb/s")

                    # If format duration failed, try getting from video stream
                    if self.main_video_duration is None:
                        stream_dur_str = stream.get("duration")
                        if stream_dur_str and stream_dur_str != "N/A":
                            try:
                                self.main_video_duration = float(stream_dur_str)
                                print(f"Main duration from video stream: {self.main_video_duration:.3f}s")
                            except (ValueError, TypeError) as e:
                                print(f"Error converting video stream duration '{stream_dur_str}': {e}")

                elif track_type == "audio":
                    details.append(f"{stream.get('codec_name', '?')}")
                    if stream.get('sample_rate'): details.append(f"{stream.get('sample_rate')} Hz")
                    if stream.get('channel_layout'): details.append(f"{stream.get('channel_layout')}")
                    if stream.get('sample_fmt'): details.append(f"{stream.get('sample_fmt')}")
                    bitrate = stream.get('bit_rate')
                    if bitrate: details.append(f"{int(bitrate) // 1000} kb/s")

                elif track_type == "subtitle":
                    details.append(f"{stream.get('codec_name', '?')}")
                else:  # Other stream types (data, attachment)
                    details.append(f"{stream.get('codec_name', '?')}")

                details_str = ", ".join(filter(None, map(str, details)))

                # Insert row into Treeview
                self.track_tree.insert("", tk.END, iid=track_id_str,
                                       values=(track_id_str, track_type, details_str, track_title, track_language))

            # Final warning if duration couldn't be determined
            if self.main_video_duration is None:
                warning_msg = "Could not determine main video duration from ffprobe analysis."
                # Don't show messagebox here, just log it, as analysis might still be useful
                self.output_info.insert(tk.END, f"WARNING: {warning_msg}\n")
                print(f"Warning: {warning_msg}")

        except FfmpegError as e:
            # Handle errors during ffprobe execution for stream info
            print(f"ffprobe error during track table population: {e}")
            messagebox.showerror("ffprobe Error", f"Failed to get stream info:\n{e}")
            # Don't clear state here, allow user to potentially fix path/file
        except Exception as e:
            print(f"Unexpected error during track table population: {e}")
            messagebox.showerror("Error", f"An unexpected error occurred while analyzing tracks:\n{e}")

    def edit_track_data(self, event: tk.Event) -> None:
        """Handles double-click events on the track table to edit Title or Language."""
        item_iid = self.track_tree.identify_row(event.y)  # Get IID of the clicked row
        column_id = self.track_tree.identify_column(event.x)  # Get ID of the clicked column (e.g., #4)

        if not item_iid or not column_id:
            print("Click did not hit a valid row or column.")
            return

        # Get the internal name of the column (e.g., "title", "language")
        try:
            column_name_internal = self.track_tree.column(column_id, "id")
        except tk.TclError:
            print(f"Could not identify column for ID: {column_id}")
            return  # Invalid column clicked

        # Allow editing only for 'title' and 'language' columns
        if column_name_internal not in {'title', 'language'}:
            print(f"Editing not allowed for column: {column_name_internal}")
            return

        # Get current values for the selected row
        item_values = list(self.track_tree.item(item_iid, "values"))
        track_path_id = item_values[0]  # The unique ID (e.g., "0:v:0")

        try:
            # Find the index corresponding to the internal column name
            column_index = self.track_tree['columns'].index(column_name_internal)
            current_value = item_values[column_index]
            # Get the display name of the column (from heading)
            column_name_display = self.track_tree.heading(column_id)['text']
        except (ValueError, IndexError, KeyError) as e:
            print(f"Error getting column data for editing: {e}")
            messagebox.showerror("Error", "Could not retrieve data for editing.")
            return

        # Ask user for new value using a simple dialog
        new_value = simpledialog.askstring(f"Edit {column_name_display}",
                                           f"Enter new value for '{column_name_display}' (Track ID: {track_path_id}):",
                                           initialvalue=current_value)

        # Process the new value if user didn't cancel
        if new_value is not None:
            # Special validation for language code
            if column_name_internal == 'language':
                new_value = new_value.strip().lower()
                # Basic check for 3-letter ISO 639-2 code
                if not (len(new_value) == 3 and new_value.isalpha()):
                    messagebox.showerror("Invalid Language Code",
                                         "Language code must be 3 letters (e.g., eng, rus, und).")
                    return

            # Update the value in the Treeview display
            item_values[column_index] = new_value
            self.track_tree.item(item_iid, values=tuple(item_values))  # Update requires tuple

            # Store the change in our internal track_data dictionary
            if track_path_id not in self.track_data:
                self.track_data[track_path_id] = {}
            self.track_data[track_path_id][column_name_internal] = new_value
            print(f"Metadata edit stored for {track_path_id}: {column_name_internal} = '{new_value}'")
            self.output_info.insert(tk.END,
                                    f"Metadata updated for {track_path_id}: {column_name_internal} = '{new_value}'\n")

    def add_embed_timecode(self) -> None:
        """Adds an embed ad timecode and file to the list."""
        timecode = self.embed_timecodes_entry.get().strip()
        embed_file = self.embed_file_entry.get().strip()

        # --- Input Validation ---
        if not embed_file:
            messagebox.showerror("File Error", "Please select a file for the embed ad.")
            return
        if not os.path.exists(embed_file):
            messagebox.showerror("File Error", f"Ad file not found:\n{embed_file}")
            return
        if not timecode:
            messagebox.showerror("Timecode Error", "Please enter an insertion timecode (MM:SS or HH:MM:SS).")
            return

        time_sec = utils.timecode_to_seconds(timecode)
        if time_sec is None:
            messagebox.showerror("Timecode Error", f"Invalid timecode format: {timecode}.\nUse MM:SS or HH:MM:SS.")
            return

        if self.main_video_duration is None:
            messagebox.showerror("Duration Error",
                                 "Please select and analyze the main video file first to determine its duration.")
            return

        # Check if timecode exceeds main video duration
        if time_sec > self.main_video_duration:
            messagebox.showwarning("Warning",
                                   f"Timecode {timecode} ({time_sec:.2f}s) exceeds main video duration ({self.main_video_duration:.2f}s). Ad will not be added.")
            return
        elif time_sec == self.main_video_duration:
            # Ask for confirmation if inserting exactly at the end
            if not messagebox.askyesno("Confirmation",
                                       f"Timecode {timecode} matches the video end.\nThe ad will be appended after the main video.\nContinue?"):
                return

        # Get duration of the ad file itself
        try:
            # Use a temporary FFMPEG instance just for getting duration
            ffmpeg_helper = ffmpeg_utils.FFMPEG()
            embed_duration = ffmpeg_helper.get_media_duration(embed_file)
            if embed_duration is None or embed_duration <= 0.01:
                # Handle cases where duration is invalid or the file might be an image
                messagebox.showerror("Ad Duration Error",
                                     f"Could not determine a valid positive duration for the ad file:\n{embed_file}\nEnsure it's a valid video file.")
                return
        except FfmpegError as e:
            messagebox.showerror("ffprobe Error (Ad)", f"Failed to get ad duration:\n{e}")
            return

        # Check for duplicate timecodes (using the string representation)
        for ad in self.embed_ads:
            if ad['timecode'] == timecode:
                messagebox.showwarning("Duplicate Timecode",
                                       f"Timecode {timecode} is already added for an embed ad.\nDouble-click the entry in the list to remove it.")
                return

        # Add the ad data
        ad_data = {'path': embed_file, 'timecode': timecode, 'duration': embed_duration}
        self.embed_ads.append(ad_data)
        # Keep the list sorted by time in seconds for processing logic
        self.embed_ads.sort(key=lambda x: utils.timecode_to_seconds(x['timecode']))
        print(f"Embed ad added: {ad_data}")

        # Update the listbox display and clear the entry field
        self._update_embed_listbox()
        self.embed_timecodes_entry.delete(0, tk.END)

    def delete_embed_timecode(self, event: tk.Event) -> None:
        """Deletes the selected embed ad entry from the list."""
        selected_indices = self.embed_timecodes_listbox.curselection()
        if not selected_indices:  # No item selected
            return

        index_to_delete = selected_indices[0]
        try:
            # Get the corresponding ad data from our internal list
            ad_info = self.embed_ads[index_to_delete]
            # Ask for confirmation
            confirm = messagebox.askyesno("Delete Embed Ad?",
                                          f"Remove the following embed ad insertion:\n"
                                          f"File: {os.path.basename(ad_info['path'])}\n"
                                          f"Timecode: {ad_info['timecode']}\n"
                                          f"Duration: {ad_info['duration']:.2f}s")
            if confirm:
                deleted_ad = self.embed_ads.pop(index_to_delete)
                print(f"Embed ad removed: {deleted_ad}")
                self._update_embed_listbox()  # Refresh the listbox display
        except IndexError:
            # This might happen if the listbox and internal list get out of sync
            print(f"Error: Index out of range when deleting embed ad. Index: {index_to_delete}, List: {self.embed_ads}")
            messagebox.showerror("Error", "Could not delete selected entry (sync error). Listbox refreshed.")
            self._update_embed_listbox()  # Refresh to potentially fix sync issue

    def _update_embed_listbox(self) -> None:
        """Updates the embed ad listbox based on the self.embed_ads list."""
        self.embed_timecodes_listbox.delete(0, tk.END)  # Clear existing items
        # Add each ad with its details
        for ad in self.embed_ads:
            display_text = f"{ad['timecode']} ({os.path.basename(ad['path'])}, {ad['duration']:.2f}s)"
            self.embed_timecodes_listbox.insert(tk.END, display_text)

    def add_banner_timecode(self) -> None:
        """Adds a banner display timecode to the list."""
        timecode = self.banner_timecodes_entry.get().strip()
        banner_file = self.banner_file_entry.get().strip()  # Check if banner file is selected

        # --- Input Validation ---
        if not banner_file:
            messagebox.showerror("File Error", "Please select a banner file before adding timecodes.")
            return
        if not timecode:
            messagebox.showerror("Timecode Error", "Please enter a display timecode (MM:SS or HH:MM:SS).")
            return

        time_sec = utils.timecode_to_seconds(timecode)
        if time_sec is None:
            messagebox.showerror("Timecode Error", f"Invalid timecode format: {timecode}.\nUse MM:SS or HH:MM:SS.")
            return

        if self.main_video_duration is None:
            messagebox.showerror("Duration Error", "Please select and analyze the main video file first.")
            return

        # Check if timecode exceeds *original* video duration (adjusted time is handled later)
        if time_sec >= self.main_video_duration:
            messagebox.showwarning("Warning",
                                   f"Banner timecode {timecode} ({time_sec:.2f}s) is at or after the *original* video end ({self.main_video_duration:.2f}s).\nEnsure this is intended, considering potential time shifts from embedded ads.")
            # Allow adding it anyway, validation during command generation will handle filtering

        # Check for duplicate timecodes
        if timecode in self.banner_timecodes:
            messagebox.showwarning("Duplicate Timecode",
                                   f"Timecode {timecode} is already added for the banner.\nDouble-click the entry to remove it.")
            return

        # Add the timecode
        self.banner_timecodes.append(timecode)
        # Keep the list sorted by time for display and processing
        self.banner_timecodes.sort(key=lambda tc: utils.timecode_to_seconds(tc) or float('inf'))  # Sort numerically
        print(f"Banner timecode added: {timecode}. Current list: {self.banner_timecodes}")

        # Update the listbox display and clear the entry field
        self._update_banner_listbox()
        self.banner_timecodes_entry.delete(0, tk.END)

    def delete_banner_timecode(self, event: tk.Event) -> None:
        """Deletes the selected banner timecode from the list."""
        selected_indices = self.banner_timecodes_listbox.curselection()
        if not selected_indices:  # No item selected
            return

        index = selected_indices[0]
        try:
            # Get the timecode string directly from the listbox
            tc_to_remove = self.banner_timecodes_listbox.get(index)
            # Ask for confirmation
            if messagebox.askyesno("Delete Timecode?", f"Remove banner timecode: {tc_to_remove}?"):
                # Remove from the internal list if present
                if tc_to_remove in self.banner_timecodes:
                    self.banner_timecodes.remove(tc_to_remove)
                    print(f"Banner timecode removed: {tc_to_remove}. Current list: {self.banner_timecodes}")
                else:
                    # Should not happen if lists are in sync
                    print(f"Warning: Timecode {tc_to_remove} not found in internal list during deletion.")
                # Refresh the listbox display regardless
                self._update_banner_listbox()
        except (IndexError, tk.TclError) as e:
            # Handle potential errors getting value from listbox or list index issues
            print(f"Error deleting banner timecode: Index {index}, Error: {e}")
            messagebox.showerror("Error", "Could not delete selected entry.")
            self._update_banner_listbox()  # Refresh listbox

    def _update_banner_listbox(self) -> None:
        """Updates the banner timecode listbox based on the self.banner_timecodes list."""
        self.banner_timecodes_listbox.delete(0, tk.END)  # Clear existing items
        # Add each timecode from the sorted list
        for tc in self.banner_timecodes:
            self.banner_timecodes_listbox.insert(tk.END, tc)

    def detect_hwaccels(self) -> List[str]:
        """Attempts to detect available ffmpeg hardware acceleration methods."""
        try:
            # Run ffmpeg -hwaccels to list available methods
            process = subprocess.Popen(["ffmpeg", "-hwaccels", "-hide_banner"],
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', errors='replace')
            output, _ = process.communicate(timeout=5)  # Add timeout
            # Parse the output, skipping header lines
            hwaccels = [line.strip() for line in output.splitlines()
                        if line.strip() != "" and "Hardware acceleration methods" not in line]
            print(f"Detected HW Accels: {hwaccels}")
            return hwaccels if hwaccels else ["none available"]
        except FileNotFoundError:
            print("Error detecting HW Accels: ffmpeg not found.")
            return ["ffmpeg not found"]
        except subprocess.TimeoutExpired:
            print("Error detecting HW Accels: ffmpeg timed out.")
            return ["detection timed out"]
        except Exception as e:
            print(f"Error detecting HW Accels: {e}")
            return ["error detecting"]

    def _prepare_and_generate_commands(self) -> Optional[Tuple[List[str], str, List[str]]]:
        """Gathers all inputs, validates them, creates an FFMPEG instance, and generates commands."""
        # 1. Cleanup previous state and log
        self.cleanup_temp_files()  # Clear any old temp files first
        self.output_info.delete('1.0', tk.END)
        self.output_info.insert('1.0', "Preparing and generating ffmpeg commands...\n")
        self.master.update_idletasks()  # Show message immediately

        # 2. Gather inputs from GUI fields
        input_file = self.input_file_entry.get().strip()
        output_file = self.output_file_entry.get().strip()

        # Transcoding parameters
        video_codec = self.video_codec_entry.get().strip() or None
        video_preset = self.video_preset_entry.get().strip() or None
        video_cq = self.video_cq_entry.get().strip() or None
        video_bitrate = self.video_bitrate_entry.get().strip() or None
        audio_codec = self.audio_codec_entry.get().strip() or None
        audio_bitrate = self.audio_bitrate_entry.get().strip() or None
        video_fps = self.video_fps_entry.get().strip() or None
        hwaccel = self.hwaccel_combo.get().strip() or None
        additional_encoding = self.additional_encoding_entry.get().strip() or None
        # Manual override parameter string
        encoding_params_str = self.encoding_entry.get().strip()  # This is passed separately

        # Ad/Banner parameters
        banner_file = self.banner_file_entry.get().strip() or None
        moving_file = self.moving_file_entry.get().strip() or None
        banner_track_pix_fmt = self.banner_track_pix_fmt_entry.get().strip() or None
        banner_gap_color = self.banner_gap_color_entry.get().strip() or None

        # Ad/Banner numerical parameters with error handling
        try:
            moving_speed = float(
                self.moving_speed_entry.get().strip()) if self.moving_speed_entry.get().strip() else None
            moving_logo_relative_height = float(
                self.moving_logo_relative_height_entry.get().strip()) if self.moving_logo_relative_height_entry.get().strip() else None
            moving_logo_alpha = float(
                self.moving_logo_alpha_entry.get().strip()) if self.moving_logo_alpha_entry.get().strip() else None
        except ValueError as e:
            messagebox.showerror("Parameter Error", f"Invalid numeric value for ad parameter: {e}")
            self.output_info.insert(tk.END, f"ERROR: Invalid numeric input: {e}\n")
            return None

        # 3. Basic Validation
        error_messages = []
        if not input_file: error_messages.append("- Input file not selected.")
        if not output_file: error_messages.append("- Output file not selected.")
        if input_file and not os.path.exists(input_file): error_messages.append(f"- Input file not found: {input_file}")
        # Use the stored duration/params from analysis
        if self.main_video_duration is None or self.main_video_duration <= 0:
            error_messages.append(
                "- Could not determine a valid duration for the main video. Please re-select the input file.")
        if not self.main_video_params or self.main_video_params.get('width') is None:
            error_messages.append(
                "- Could not get essential parameters from the main video. Please re-select the input file.")

        # Warnings for missing files (actual handling is done in ffmpeg_utils)
        if banner_file and not os.path.exists(banner_file):
            self.output_info.insert(tk.END, f"WARNING: Banner file '{banner_file}' not found, it will be ignored.\n")
            # banner_file = None # Let ffmpeg_utils handle the final decision
        if moving_file and not os.path.exists(moving_file):
            self.output_info.insert(tk.END,
                                    f"WARNING: Moving logo file '{moving_file}' not found, it will be ignored.\n")
            # moving_file = None # Let ffmpeg_utils handle the final decision
        if banner_file and not self.banner_timecodes:
            # This is a configuration error the user should fix
            self.output_info.insert(tk.END,
                                    "WARNING: Banner file is selected, but no display timecodes are added. Banner will not be shown.\n")
            # Don't add to error_messages, as it's just a warning for now

        if error_messages:
            full_error_msg = "Please fix the following errors:\n" + "\n".join(error_messages)
            messagebox.showerror("Validation Error", full_error_msg)
            self.output_info.insert(tk.END, f"VALIDATION ERROR:\n{full_error_msg}\n")
            return None

        # 4. Create FFMPEG instance with gathered parameters
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
                hwaccel=hwaccel,
                additional_encoding=additional_encoding  # Pass additional params string
            )
        except Exception as e:
            messagebox.showerror("ffmpeg Setup Error", f"Failed to initialize ffmpeg settings: {e}")
            self.output_info.insert(tk.END, f"FFMPEG INIT ERROR:\nFailed to initialize settings: {e}\n")
            return None

        # 5. Generate Commands
        print("Calling generate_ffmpeg_commands with parameters:")
        print(f"  input_file: {input_file}")
        print(f"  output_file: {output_file}")
        print(f"  encoding_params_str (Manual Override): '{encoding_params_str}'")  # Log manual override
        # print(f"  main_video_params: {self.main_video_params}") # Already logged during analysis
        print(f"  main_video_duration: {self.main_video_duration}")
        print(f"  track_data: {self.track_data}")
        print(f"  embed_ads: {self.embed_ads}")
        print(f"  banner_file: {banner_file}")
        print(f"  banner_timecodes: {self.banner_timecodes}")
        print(f"  moving_file: {moving_file}")

        try:
            # Call the main command generation method
            result = self.ffmpeg_instance.generate_ffmpeg_commands(
                input_file=input_file,
                output_file=output_file,
                encoding_params_str=encoding_params_str,  # Pass the manual override string here
                track_data=self.track_data,
                embed_ads=self.embed_ads,
                banner_file=banner_file,
                banner_timecodes=self.banner_timecodes,
                moving_file=moving_file
            )
            # Store the list of temp files that *might* be created
            self.temp_files_to_clean = result[2] if result and len(result) > 2 else []
            self.output_info.insert(tk.END, "Commands generated successfully.\n")
            print(f"Potential temporary files: {self.temp_files_to_clean}")
            return result  # Tuple: (preproc_cmds, main_cmd, temp_files)

        except (CommandGenerationError, FfmpegError) as e:
            error_msg = f"Command Generation Error:\n{e}"
            messagebox.showerror("Generation Error", error_msg)
            self.output_info.insert(tk.END, f"COMMAND GENERATION ERROR:\n{error_msg}\n")
            self.cleanup_temp_files()  # Clean up any partial temp files
            return None
        except Exception as e:
            # Catch unexpected errors during command generation
            error_msg = f"An unexpected error occurred during command generation:\n{type(e).__name__}: {e}"
            messagebox.showerror("Unexpected Error", error_msg)
            self.output_info.insert(tk.END, f"UNEXPECTED GENERATION ERROR:\n{error_msg}\n")
            import traceback
            traceback.print_exc()  # Print stack trace to console
            self.cleanup_temp_files()
            return None

    def show_ffmpeg_commands(self) -> None:
        """Generates and displays the ffmpeg commands in the output area."""
        result = self._prepare_and_generate_commands()
        self.output_info.delete('1.0', tk.END)  # Clear previous output

        if result:
            preproc_cmds, main_cmd, temp_files_generated = result
            output_text = "--- Generated ffmpeg Commands ---\n\n"

            output_text += "--- Potential Temporary Files ---\n"
            if temp_files_generated:
                # Display only basenames for brevity
                output_text += "\n".join([f"  - {os.path.basename(f)}" for f in temp_files_generated])
            else:
                output_text += "  (None)"
            output_text += "\n\n"

            if preproc_cmds:
                output_text += f"--- Preprocessing Commands ({len(preproc_cmds)}) ---\n"
                for i, cmd in enumerate(preproc_cmds):
                    # Display command, add separator
                    output_text += f"[{i + 1}]: {cmd}\n{'-' * 60}\n"
                output_text += "\n"
            else:
                output_text += "--- No Preprocessing Commands Needed ---\n\n"

            if main_cmd:
                output_text += "--- Main Conversion Command ---\n"
                output_text += main_cmd + "\n"
            else:
                # This case should ideally be caught earlier, but handle defensively
                output_text += "--- ERROR: Main conversion command could not be generated ---"

            self.output_info.insert('1.0', output_text)
            self.output_info.yview_moveto(0.0)  # Scroll to top
        else:
            # Error occurred during preparation/generation
            self.output_info.insert('1.0',
                                    "Failed to generate commands. Check settings and error messages above/in console.")

    def start_conversion(self) -> None:
        """Initiates the ffmpeg conversion process after confirmation."""
        # 1. Prepare and Generate Commands
        result = self._prepare_and_generate_commands()

        if not result:
            messagebox.showerror("Cancelled", "Failed to prepare ffmpeg commands. Conversion cancelled.")
            return

        preproc_cmds, main_cmd, _ = result  # We already stored temp files list

        # 2. Confirmation Dialog
        num_preproc = len(preproc_cmds) if preproc_cmds else 0
        confirm_message_parts = ["The following steps will be executed:"]
        steps = []
        if num_preproc > 0:
            steps.append(f"Preprocess {num_preproc} segments/ads (creating temporary files).")
        if main_cmd:
            steps.append("Perform main conversion with merging and overlays.")
        else:
            # This shouldn't happen if _prepare_and_generate_commands worked, but check anyway
            messagebox.showerror("Error", "No main conversion command generated. Cannot proceed.")
            return

        if not steps:
            messagebox.showerror("Error", "No commands to execute!")
            return

        for i, step_desc in enumerate(steps):
            confirm_message_parts.append(f"\n{i + 1}. {step_desc}")

        confirm_message_parts.append(
            "\n\nThe process might take a significant amount of time, especially preprocessing.")
        confirm_message_parts.append("\n\nContinue?")
        confirm_message = "".join(confirm_message_parts)

        if not messagebox.askyesno("Confirm Conversion", confirm_message):
            print("Conversion cancelled by user.")
            self.output_info.insert(tk.END, "\nConversion cancelled by user.\n")
            self.cleanup_temp_files()  # Clean up files generated during preparation
            return

        # 3. Execute Commands
        self.output_info.delete('1.0', tk.END)  # Clear previous log/commands
        self.output_info.insert('1.0', "Starting conversion process...\n\n")
        self.master.update()  # Show message

        try:
            start_time_total = time.time()

            # Execute Preprocessing Commands
            if preproc_cmds:
                self.output_info.insert(tk.END, f"--- Stage 1: Preprocessing ({len(preproc_cmds)} commands) ---\n")
                self.master.update()
                start_time_preproc = time.time()
                for i, cmd in enumerate(preproc_cmds):
                    step_name = f"Preprocessing {i + 1}/{len(preproc_cmds)}"
                    self.output_info.insert(tk.END, f"\nRunning: {step_name}...\n")
                    self.output_info.see(tk.END)  # Scroll to show current step
                    self.master.update()
                    start_time_step = time.time()
                    # Use the static method from FFMPEG class to run the command
                    ffmpeg_utils.FFMPEG.run_ffmpeg_command(cmd, step_name)
                    end_time_step = time.time()
                    self.output_info.insert(tk.END,
                                            f"Success: {step_name} (took {end_time_step - start_time_step:.2f}s)\n")
                    self.output_info.see(tk.END)
                    self.master.update()
                end_time_preproc = time.time()
                self.output_info.insert(tk.END,
                                        f"\n--- Preprocessing finished (Total time: {end_time_preproc - start_time_preproc:.2f}s) ---\n")

            # Execute Main Command
            if main_cmd:
                step_name = "Main Conversion"
                self.output_info.insert(tk.END, f"\n--- Stage 2: {step_name} ---\n")
                self.output_info.see(tk.END)
                self.master.update()
                start_time_main = time.time()
                ffmpeg_utils.FFMPEG.run_ffmpeg_command(main_cmd, step_name)
                end_time_main = time.time()
                self.output_info.insert(tk.END,
                                        f"\nSuccess: {step_name} (took {end_time_main - start_time_main:.2f}s)\n")
                self.output_info.see(tk.END)
                self.master.update()
            # We already checked that main_cmd exists before confirmation

            end_time_total = time.time()
            success_msg = (f"\n--- SUCCESS: Conversion completed successfully! ---"
                           f"\n--- Total time: {end_time_total - start_time_total:.2f}s ---")
            self.output_info.insert(tk.END, success_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showinfo("Success", "Conversion completed successfully!")

        except (ConversionError, FfmpegError) as e:
            # Handle errors reported by run_ffmpeg_command or FfmpegError during setup
            error_msg = f"\n--- CONVERSION FAILED ---\n{e}\n--- PROCESS HALTED ---"
            print(error_msg)
            self.output_info.insert(tk.END, error_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showerror("Conversion Failed", f"An error occurred during conversion:\n\n{e}")
        except Exception as e:
            # Catch any other unexpected errors during the execution loop
            error_msg = f"\n--- UNEXPECTED ERROR ---\n{type(e).__name__}: {e}\n--- PROCESS HALTED ---"
            print(error_msg)
            import traceback
            traceback.print_exc()  # Print stack trace to console
            self.output_info.insert(tk.END, error_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showerror("Critical Error",
                                 f"An unexpected error occurred:\n{type(e).__name__}: {e}\n\nCheck console for details.")

        finally:
            # 4. Final Cleanup (always run)
            self.output_info.insert(tk.END, "\nRunning final cleanup of temporary files...\n")
            self.output_info.see(tk.END)
            self.master.update()
            self.cleanup_temp_files()  # Call cleanup method
            self.output_info.insert(tk.END, "Cleanup finished.\n")
            self.output_info.see(tk.END)
