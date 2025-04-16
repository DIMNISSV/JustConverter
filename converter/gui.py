# converter/gui.py
import json
import os
import subprocess
import time
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from typing import List, Dict, Tuple, Optional, Any

# Import local modules
from . import ffmpeg, utils, config
from .exceptions import FfmpegError, CommandGenerationError, ConversionError
# Import the specific dataclass needed here
from .ffmpeg import StreamParams


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
        self.VERSION = '0.1.5'
        self.TITLE = "JustConverter + AdBurner"
        self.AUTHOR = "dimnissv"
        master.title(f'{self.TITLE} ({self.AUTHOR}) {self.VERSION}')

        self.notebook = ttk.Notebook(master)

        # Create tabs
        self.main_tab = ttk.Frame(self.notebook)
        self.advertisement_tab = ttk.Frame(self.notebook)
        self.transcode_tab = ttk.Frame(self.notebook)
        self.start_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.about_tab = ttk.Frame(self.notebook)

        # State variables
        self.track_data: Dict[str, Dict[str, str]] = {}
        self.main_video_duration: Optional[float] = None
        self.main_video_params: Optional[StreamParams] = None
        self.embed_ads: List[Dict[str, Any]] = []
        self.banner_timecodes: List[str] = []
        self.temp_files_to_clean: List[str] = []

        # --- CHANGED ORDER ---
        # 1. Build UI elements for ALL tabs FIRST
        self._create_settings_tab_widgets()  # Important to be first
        self._create_main_tab_widgets()
        self._create_advertisement_tab_widgets()
        self._create_transcode_tab_widgets()
        self._create_start_tab_widgets()
        self._create_about_tab_widgets()

        # 2. Define the widget map AFTER all widgets exist
        self._define_widget_map()
        # --- END OF CHANGED ORDER ---

        # Add tabs to the notebook
        self.notebook.add(self.main_tab, text="Files")
        self.notebook.add(self.advertisement_tab, text="Advertisement")
        self.notebook.add(self.transcode_tab, text="Transcoding")
        self.notebook.add(self.start_tab, text="Start")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.about_tab, text="About")

        self.notebook.grid(row=0, column=0, sticky="nsew")
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)

        # Handle window closing
        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.ffmpeg_instance: Optional[ffmpeg.FFMPEG] = None

        self._load_settings()

    def _define_widget_map(self):
        """Defines the mapping between setting keys and GUI widgets."""
        # Maps: setting_key -> (widget_instance, widget_type)
        self.widget_map: Dict[str, Tuple[tk.Widget, str]] = {
            # Transcoding Tab
            "video_codec": (self.video_codec_entry, 'entry'),
            "video_preset": (self.video_preset_entry, 'entry'),
            "video_cq": (self.video_cq_entry, 'entry'),
            "video_bitrate": (self.video_bitrate_entry, 'entry'),
            "video_fps": (self.video_fps_entry, 'entry'),
            "audio_codec": (self.audio_codec_entry, 'entry'),
            "audio_bitrate": (self.audio_bitrate_entry, 'entry'),
            "hwaccel": (self.hwaccel_combo, 'combo'),
            "additional_encoding": (self.additional_encoding_entry, 'entry'),
            "encoding_params_str": (self.encoding_entry, 'entry'),  # Manual override
            # Advertisement Tab
            "banner_track_pix_fmt": (self.banner_track_pix_fmt_entry, 'entry'),
            "banner_gap_color": (self.banner_gap_color_entry, 'entry'),
            "moving_speed": (self.moving_speed_entry, 'entry'),
            "moving_logo_relative_height": (self.moving_logo_relative_height_entry, 'entry'),
            "moving_logo_alpha": (self.moving_logo_alpha_entry, 'entry'),
            # Settings Tab
            "ffmpeg_path": (self.ffmpeg_path_entry, 'entry'),
            "settings_path": (self.settings_path_entry, 'entry'),
        }

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

        self.track_tree = ttk.Treeview(self.main_tab,
                                       columns=("id", "type", "details", "title", "language"),
                                       show="headings")
        self.track_tree.heading("id", text="ID")
        self.track_tree.heading("type", text="Type")
        self.track_tree.heading("details", text="Details")
        self.track_tree.heading("title", text="Title")
        self.track_tree.heading("language", text="Lang")

        self.track_tree.column("id", width=50, stretch=tk.NO, anchor='center')
        self.track_tree.column("type", width=60, stretch=tk.NO)
        self.track_tree.column("details", width=250, stretch=tk.YES)
        self.track_tree.column("title", width=150, stretch=tk.YES)
        self.track_tree.column("language", width=40, stretch=tk.NO, anchor='center')

        self.track_tree.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
        tree_scrollbar = ttk.Scrollbar(self.main_tab, orient="vertical", command=self.track_tree.yview)
        tree_scrollbar.grid(row=2, column=3, sticky='ns')
        self.track_tree.configure(yscrollcommand=tree_scrollbar.set)

        self.main_tab.grid_rowconfigure(2, weight=1)
        self.main_tab.grid_columnconfigure(1, weight=1)
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
        self.advertisement_tab.grid_columnconfigure(1, weight=1)

        self.embed_timecodes_label = tk.Label(self.advertisement_tab, text="Insert Timecodes (MM:SS or HH:MM:SS):")
        self.embed_timecodes_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.embed_timecodes_entry = tk.Entry(self.advertisement_tab, width=20)
        self.embed_timecodes_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.add_embed_timecode_button = tk.Button(self.advertisement_tab, text="Add",
                                                   command=self.add_embed_timecode)
        self.add_embed_timecode_button.grid(row=1, column=2, padx=5, pady=5, sticky="w")

        self.embed_timecodes_listbox = tk.Listbox(self.advertisement_tab, width=60, height=4)
        self.embed_timecodes_listbox.grid(row=2, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.embed_timecodes_listbox.bind("<Double-1>", self.delete_embed_timecode)
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

        self.banner_timecodes_listbox = tk.Listbox(self.advertisement_tab, width=60, height=4)
        self.banner_timecodes_listbox.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.banner_timecodes_listbox.bind("<Double-1>", self.delete_banner_timecode)
        banner_scrollbar = ttk.Scrollbar(self.advertisement_tab, orient="vertical",
                                         command=self.banner_timecodes_listbox.yview)
        banner_scrollbar.grid(row=5, column=3, sticky='ns')
        self.banner_timecodes_listbox.configure(yscrollcommand=banner_scrollbar.set)

        self.banner_track_pix_fmt_label = tk.Label(self.advertisement_tab, text='Banner Pixel Format:')
        self.banner_track_pix_fmt_label.grid(row=6, column=0, padx=5, pady=5, sticky='w')
        self.banner_track_pix_fmt_entry = tk.Entry(self.advertisement_tab, width=15)
        self.banner_track_pix_fmt_entry.grid(row=6, column=1, padx=5, pady=5, sticky='w')
        self.banner_track_pix_fmt_entry.insert(0, config.BANNER_TRACK_PIX_FMT)

        self.banner_gap_color_label = tk.Label(self.advertisement_tab, text='Banner Gap Color:')
        self.banner_gap_color_label.grid(row=7, column=0, padx=5, pady=5, sticky='w')
        self.banner_gap_color_entry = tk.Entry(self.advertisement_tab, width=15)
        self.banner_gap_color_entry.grid(row=7, column=1, padx=5, pady=5, sticky='w')
        self.banner_gap_color_entry.insert(0, config.BANNER_GAP_COLOR)

        # --- Moving Logo ---
        self.moving_file_label = tk.Label(self.advertisement_tab, text="Moving Logo (Image):")
        self.moving_file_label.grid(row=8, column=0, padx=5, pady=5, sticky="w")
        self.moving_file_entry = tk.Entry(self.advertisement_tab, width=50)
        self.moving_file_entry.grid(row=8, column=1, padx=5, pady=5, sticky="ew")
        self.moving_file_button = tk.Button(self.advertisement_tab, text="Browse",
                                            command=lambda: self.browse_ad_file(self.moving_file_entry,
                                                                                image_only=True))
        self.moving_file_button.grid(row=8, column=2, padx=5, pady=5)

        self.moving_speed_label = tk.Label(self.advertisement_tab, text="Moving Speed Factor:")
        self.moving_speed_label.grid(row=9, column=0, padx=5, pady=5, sticky="w")
        self.moving_speed_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_speed_entry.grid(row=9, column=1, padx=5, pady=5, sticky="w")
        self.moving_speed_entry.insert(0, str(config.MOVING_SPEED))

        self.moving_logo_relative_height_label = tk.Label(self.advertisement_tab, text="Logo Height (Relative):")
        self.moving_logo_relative_height_label.grid(row=10, column=0, padx=5, pady=5, sticky="w")
        self.moving_logo_relative_height_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_logo_relative_height_entry.grid(row=10, column=1, padx=5, pady=5, sticky="w")
        self.moving_logo_relative_height_entry.insert(0,
                                                      f"{config.MOVING_LOGO_RELATIVE_HEIGHT:.3f}")

        self.moving_logo_alpha_label = tk.Label(self.advertisement_tab, text="Logo Alpha (0.0-1.0):")
        self.moving_logo_alpha_label.grid(row=11, column=0, padx=5, pady=5, sticky="w")
        self.moving_logo_alpha_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_logo_alpha_entry.grid(row=11, column=1, padx=5, pady=5, sticky="w")
        self.moving_logo_alpha_entry.insert(0, str(config.MOVING_LOGO_ALPHA))

    def _create_transcode_tab_widgets(self) -> None:
        """Creates widgets for the 'Transcoding' tab."""
        self.video_codec_label = tk.Label(self.transcode_tab, text='Video Codec:')
        self.video_codec_label.grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.video_codec_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_codec_entry.grid(row=0, column=1, padx=5, pady=5, sticky='w')
        self.video_codec_entry.insert(0, config.VIDEO_CODEC)

        self.video_preset_label = tk.Label(self.transcode_tab, text='Preset:')
        self.video_preset_label.grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.video_preset_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_preset_entry.grid(row=1, column=1, padx=5, pady=5, sticky='w')
        self.video_preset_entry.insert(0, config.VIDEO_PRESET)

        self.video_cq_label = tk.Label(self.transcode_tab, text='CQ/CRF (Quality):')
        self.video_cq_label.grid(row=2, column=0, padx=5, pady=5, sticky='w')
        self.video_cq_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_cq_entry.grid(row=2, column=1, padx=5, pady=5, sticky='w')
        self.video_cq_entry.insert(0, config.VIDEO_CQ)

        self.video_bitrate_label = tk.Label(self.transcode_tab, text='Video Bitrate (e.g., 5000k, 0=CQ):')
        self.video_bitrate_label.grid(row=3, column=0, padx=5, pady=5, sticky='w')
        self.video_bitrate_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_bitrate_entry.grid(row=3, column=1, padx=5, pady=5, sticky='w')
        self.video_bitrate_entry.insert(0, config.VIDEO_BITRATE)

        self.video_fps_label = tk.Label(self.transcode_tab, text='Video FPS (Optional Override):')
        self.video_fps_label.grid(row=4, column=0, padx=5, pady=5, sticky='w')
        self.video_fps_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_fps_entry.grid(row=4, column=1, padx=5, pady=5, sticky='w')

        self.audio_codec_label = tk.Label(self.transcode_tab, text='Audio Codec:')
        self.audio_codec_label.grid(row=5, column=0, padx=5, pady=5, sticky='w')
        self.audio_codec_entry = tk.Entry(self.transcode_tab, width=20)
        self.audio_codec_entry.grid(row=5, column=1, padx=5, pady=5, sticky='w')
        self.audio_codec_entry.insert(0, config.AUDIO_CODEC)

        self.audio_bitrate_label = tk.Label(self.transcode_tab, text='Audio Bitrate (e.g., 192k):')
        self.audio_bitrate_label.grid(row=6, column=0, padx=5, pady=5, sticky='w')
        self.audio_bitrate_entry = tk.Entry(self.transcode_tab, width=20)
        self.audio_bitrate_entry.grid(row=6, column=1, padx=5, pady=5, sticky='w')
        self.audio_bitrate_entry.insert(0, config.AUDIO_BITRATE)

        self.hwaccel_label = tk.Label(self.transcode_tab, text="Hardware Acceleration:")
        self.hwaccel_label.grid(row=7, column=0, padx=5, pady=5, sticky="w")
        self.hwaccel_combo = ttk.Combobox(self.transcode_tab, values=["none"] + self.detect_hwaccels(),
                                          state="readonly")
        self.hwaccel_combo.grid(row=7, column=1, padx=5, pady=5, sticky="w")
        self.hwaccel_combo.set(config.HWACCEL)

        self.additional_encoding_label = tk.Label(self.transcode_tab, text="Additional Params:")
        self.additional_encoding_label.grid(row=8, column=0, padx=5, pady=5, sticky="w")
        self.additional_encoding_entry = tk.Entry(self.transcode_tab, width=60)
        self.additional_encoding_entry.grid(row=8, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self.additional_encoding_entry.insert(0, config.ADDITIONAL_ENCODING)

        self.encoding_label = tk.Label(self.transcode_tab, text="Manual Params (Overrides Above):")
        self.encoding_label.grid(row=9, column=0, padx=5, pady=5, sticky="w")
        self.encoding_entry = tk.Entry(self.transcode_tab, width=60)
        self.encoding_entry.grid(row=9, column=1, columnspan=2, padx=5, pady=5, sticky="ew")

        self.transcode_tab.grid_columnconfigure(1, weight=1)

    def _create_start_tab_widgets(self) -> None:
        """Creates widgets for the 'Start' tab."""
        self.output_file_label = tk.Label(self.start_tab, text="Output File:")
        self.output_file_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.output_file_entry = tk.Entry(self.start_tab, width=50)
        self.output_file_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.output_file_button = tk.Button(self.start_tab, text="Browse", command=self.browse_output_file)
        self.output_file_button.grid(row=0, column=2, padx=5, pady=5)
        self.start_tab.grid_columnconfigure(1, weight=1)

        self.generate_command_button = tk.Button(self.start_tab, text="Show ffmpeg Commands",
                                                 command=self.show_ffmpeg_commands)
        self.generate_command_button.grid(row=1, column=0, columnspan=3, pady=10)

        self.output_info_label = tk.Label(self.start_tab, text="ffmpeg Commands & Log:")
        self.output_info_label.grid(row=2, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        self.output_info = tk.Text(self.start_tab, height=15, wrap=tk.WORD, relief=tk.SUNKEN,
                                   borderwidth=1)
        self.output_info.grid(row=3, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
        self.output_scrollbar = tk.Scrollbar(self.start_tab, command=self.output_info.yview)
        self.output_scrollbar.grid(row=3, column=3, sticky='nsew')
        self.output_info['yscrollcommand'] = self.output_scrollbar.set
        self.start_tab.grid_rowconfigure(3, weight=2)

        self.start_conversion_button = tk.Button(self.start_tab, text="Start Conversion",
                                                 command=self.start_conversion,
                                                 font=('Helvetica', 10, 'bold'),
                                                 bg="lightblue")
        self.start_conversion_button.grid(row=4, column=0, columnspan=3, pady=10)

    def _create_settings_tab_widgets(self) -> None:
        """Creates widgets for the 'Settings' tab."""
        settings_frame = ttk.LabelFrame(self.settings_tab, text="Application Settings", padding=10)
        settings_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.settings_tab.grid_columnconfigure(0, weight=1)
        settings_frame.grid_columnconfigure(1, weight=1)  # Allow entry fields to expand

        # --- Paths Configuration ---
        paths_frame = ttk.Frame(settings_frame)
        paths_frame.grid(row=0, column=0, columnspan=3, pady=(0, 10), sticky="ew")
        paths_frame.grid_columnconfigure(1, weight=1)

        # FFmpeg Path
        ffmpeg_path_label = tk.Label(paths_frame, text="FFmpeg Executable Path:")
        ffmpeg_path_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        # <<< FIX: Initialize the widget here >>>
        self.ffmpeg_path_entry = tk.Entry(paths_frame, width=50)
        self.ffmpeg_path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.ffmpeg_path_button = tk.Button(paths_frame, text="Browse", command=self._browse_ffmpeg_path)
        self.ffmpeg_path_button.grid(row=0, column=2, padx=5, pady=5)

        # Settings File Path
        settings_path_label = tk.Label(paths_frame, text="Settings File Path:")
        settings_path_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.settings_path_entry = tk.Entry(paths_frame, width=50)
        self.settings_path_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        self.settings_path_button = tk.Button(paths_frame, text="Browse", command=self._browse_settings_path)
        self.settings_path_button.grid(row=1, column=2, padx=5, pady=5)
        # Initialize with default path for display
        # Moved initialization to _load_settings to handle cases where file not found on startup
        # self.settings_path_entry.insert(0, self._get_settings_filepath(use_gui_field=False))

        # --- Button Frame ---
        button_frame = ttk.Frame(settings_frame)
        button_frame.grid(row=1, column=0, columnspan=3, pady=5, sticky="ew")  # Changed row index

        self.save_settings_button = tk.Button(button_frame, text="Save Current Settings",
                                              command=self._save_settings_manual)  # Use manual save
        self.save_settings_button.grid(row=0, column=0, padx=5, pady=5)

        self.load_settings_button = tk.Button(button_frame, text="Load Settings from File",
                                              command=self._load_settings_manual)
        self.load_settings_button.grid(row=0, column=1, padx=5, pady=5)

        self.reset_settings_button = tk.Button(button_frame, text="Reset to Defaults",
                                               command=self._reset_settings_to_defaults)
        self.reset_settings_button.grid(row=0, column=2, padx=5, pady=5)

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
        """Handles the window close event, saving settings and cleaning up temp files."""
        print("Close requested. Saving settings...")
        self._save_settings()  # Save using potentially configured path
        print("Cleaning up temporary files...")
        self.cleanup_temp_files()
        self.master.destroy()

    def cleanup_temp_files(self) -> None:
        """Calls the utility function to clean up temporary files."""
        if self.temp_files_to_clean:
            utils.cleanup_temp_files(self.temp_files_to_clean)
            self.temp_files_to_clean.clear()
        else:
            print("No temporary files to clean.")

    def _clear_state(self) -> None:
        """Resets the GUI state, clearing inputs and internal data."""
        print("Resetting GUI state...")
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
        self.main_video_params = None
        self.embed_ads = []
        self.banner_timecodes = []
        self.temp_files_to_clean = []

        self.embed_timecodes_listbox.delete(0, tk.END)
        self.banner_timecodes_listbox.delete(0, tk.END)

        for item in self.track_tree.get_children():
            self.track_tree.delete(item)

        self.output_info.delete('1.0', tk.END)

        # Reset settings fields to defaults (does not reset path fields)
        self._reset_settings_to_defaults(ask_confirm=False)  # Reset without confirmation dialog

        self.master.update_idletasks()
        print("State reset complete.")

    def _get_ffmpeg_executable_path(self) -> Optional[str]:
        """Gets the FFmpeg executable path from the settings field."""
        path = self.ffmpeg_path_entry.get().strip()
        return path if path else None  # Return None if empty, ffmpeg.py will assume PATH

    def _get_effective_ffmpeg_path(self) -> str:
        """Determines the executable name or path to use."""
        configured_path = self._get_ffmpeg_executable_path()
        if configured_path and os.path.isfile(configured_path):
            return configured_path
        elif configured_path and os.path.isdir(configured_path):
            # If user provided a directory, assume standard executable names
            base_cmd = "ffmpeg"
            if os.name == 'nt':
                base_cmd += ".exe"
            full_path = os.path.join(configured_path, base_cmd)
            if os.path.isfile(full_path):
                return full_path
            else:
                print(
                    f"Warning: ffmpeg executable not found in specified directory: {configured_path}. Falling back to PATH.")
                return "ffmpeg"  # Fallback
        else:
            if configured_path:  # Path was specified but invalid
                print(f"Warning: Invalid ffmpeg path specified: {configured_path}. Falling back to PATH.")
            return "ffmpeg"  # Default: assume in PATH

    def _create_ffmpeg_analyzer_instance(self) -> ffmpeg.FFMPEG:
        """Creates an FFMPEG instance for analysis, using configured path."""
        # This helper is needed because analysis happens before the main
        # instance used for conversion is created.
        ffmpeg_path = self._get_ffmpeg_executable_path()
        # Pass the path (or None) to the constructor
        return ffmpeg.FFMPEG(ffmpeg_path=ffmpeg_path)

    def browse_input_file(self) -> None:
        """Handles browsing for and selecting the input video file."""
        self._clear_state()

        file_path = filedialog.askopenfilename(
            title="Select Input Video File",
            filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")])
        if not file_path:
            print("Input file selection cancelled.")
            return

        self.input_file_entry.delete(0, tk.END)
        self.input_file_entry.insert(0, file_path)
        print(f"Input file selected: {file_path}")

        try:
            base, ext = os.path.splitext(file_path)
            suggested_output_base = f"{base}_converted"
            suggested_output = f"{suggested_output_base}{ext if ext else '.mkv'}"
            counter = 1
            while os.path.exists(suggested_output):
                suggested_output = f"{suggested_output_base}_{counter}{ext if ext else '.mkv'}"
                counter += 1
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, suggested_output)
            print(f"Suggested output file: {suggested_output}")
        except Exception as e:
            print(f"Error suggesting output file name: {e}")

        try:
            self.output_info.insert('1.0', f"Analyzing file: {os.path.basename(file_path)}...\n")
            self.master.update_idletasks()

            # Use an instance with the configured path for analysis
            ffmpeg_analyzer = self._create_ffmpeg_analyzer_instance()

            self.populate_track_table(file_path, ffmpeg_analyzer)
            self.main_video_params = ffmpeg_analyzer.get_essential_stream_params(file_path)

            if not self.main_video_params:
                warning_msg = "Could not retrieve all key parameters from the main video."
                messagebox.showwarning("Parameter Issue", warning_msg)
                self.output_info.insert(tk.END, f"WARNING: {warning_msg}\n")
            else:
                print("Essential video parameters retrieved:", self.main_video_params)
                fps_display = f"{self.main_video_params.fps:.3f}" if self.main_video_params.fps else "N/A"
                self.output_info.insert(tk.END,
                                        f"Video Params (WxH): {self.main_video_params.width}x{self.main_video_params.height}, "
                                        f"FPS: {fps_display}, "
                                        f"PixFmt: {self.main_video_params.pix_fmt}\n")
                if self.main_video_params.has_audio:
                    self.output_info.insert(tk.END,
                                            f"Audio Params: {self.main_video_params.sample_rate} Hz, "
                                            f"{self.main_video_params.channel_layout}, "
                                            f"Fmt: {self.main_video_params.sample_fmt}\n")
                else:
                    self.output_info.insert(tk.END,
                                            "Audio Params: No audio stream detected or parameters incomplete.\n")

                if self.main_video_params.width is None or self.main_video_params.fps is None:
                    error_msg = "Failed to determine video stream parameters (width/height/fps). Please select a different file."
                    messagebox.showerror("Video Error", error_msg)
                    self.output_info.insert(tk.END, f"ERROR: {error_msg}\n")
                    self._clear_state()
                    return

                if self.main_video_params.fps is not None:
                    self.video_fps_entry.delete(0, tk.END)
                    fps_str_for_entry = f"{self.main_video_params.fps:.3f}"
                    if fps_str_for_entry.endswith('.000'):
                        fps_str_for_entry = fps_str_for_entry[:-4]
                    self.video_fps_entry.insert(0, fps_str_for_entry)
                    print(f"Pre-filled FPS override field with: {fps_str_for_entry}")

            if self.main_video_duration is None:
                self.output_info.insert(tk.END, "WARNING: Could not determine main video duration from ffprobe.\n")

            self.output_info.insert(tk.END, "Analysis complete.\n")

        except FfmpegError as e:
            error_msg = f"Failed to analyze input file:\n{e}\n\nCheck FFmpeg path in Settings."
            messagebox.showerror("ffprobe Error", error_msg)
            self.output_info.insert(tk.END, f"FFPROBE ERROR: {error_msg}\n")
            self._clear_state()

    def browse_output_file(self) -> None:
        """Handles browsing for and selecting the output file path."""
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
            title="Select Output File",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".mkv",
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

        if image_only:
            filetypes = [("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All Files", "*.*")]
            title = "Select Image File"
        elif video_only:
            filetypes = [("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All Files", "*.*")]
            title = "Select Video File"
        else:
            filetypes = [("Media Files", "*.mp4 *.avi *.mkv *.mov *.webm *.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                         ("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm"),
                         ("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                         ("All Files", "*.*")]
            title = "Select Media File"

        file_path = filedialog.askopenfilename(title=title, initialdir=initial_dir, filetypes=filetypes)
        if file_path:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, file_path)
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

    def _browse_ffmpeg_path(self) -> None:
        """Handles browsing for the FFmpeg executable."""
        initial_dir = os.getcwd()
        current_path = self.ffmpeg_path_entry.get()
        if current_path:
            if os.path.isfile(current_path):
                initial_dir = os.path.dirname(current_path)
            elif os.path.isdir(current_path):
                initial_dir = current_path

        # On Windows, look for .exe; on other systems, just the name
        exe_filter = ("ffmpeg.exe", "*.exe") if os.name == 'nt' else ("ffmpeg", "*")
        filetypes = [(f"FFmpeg Executable ({exe_filter[0]})", exe_filter[1]), ("All files", "*.*")]

        filepath = filedialog.askopenfilename(
            title="Select FFmpeg Executable",
            initialdir=initial_dir,
            filetypes=filetypes
        )
        if filepath:
            self.ffmpeg_path_entry.delete(0, tk.END)
            self.ffmpeg_path_entry.insert(0, filepath)
            print(f"FFmpeg path set to: {filepath}")
        else:
            print("FFmpeg path selection cancelled.")

    def _browse_settings_path(self) -> None:
        """Handles browsing for the settings file location."""
        initial_dir = os.getcwd()
        initial_file = config.SETTINGS_FILENAME
        current_path = self.settings_path_entry.get()

        if current_path:
            if os.path.isfile(current_path):
                initial_dir = os.path.dirname(current_path)
                initial_file = os.path.basename(current_path)
            elif os.path.isdir(current_path):
                initial_dir = current_path

        filepath = filedialog.asksaveasfilename(
            title="Select Settings File Location",
            initialdir=initial_dir,
            initialfile=initial_file,
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
        )
        if filepath:
            self.settings_path_entry.delete(0, tk.END)
            self.settings_path_entry.insert(0, filepath)
            print(f"Settings file path set to: {filepath}")
            # Maybe ask user if they want to load from this new location now?
            # Or save current settings to this new location?
            # if messagebox.askyesno("Save Settings?", "Save current settings to this new location?"):
            #    self._save_settings(filepath=filepath)
        else:
            print("Settings file path selection cancelled.")

    def populate_track_table(self, file_path: str, analyzer: ffmpeg.FFMPEG) -> None:
        """Populates the track Treeview using the provided FFMPEG instance."""
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)
        self.main_video_duration = None
        self.track_data = {}

        try:
            # Use the passed analyzer instance (which has the correct path)
            stream_info = analyzer.get_stream_info(file_path)
            if not stream_info:
                messagebox.showerror("Analysis Error", f"Could not retrieve stream information from:\n{file_path}")
                return

            self.main_video_duration = analyzer.get_media_duration(file_path)
            if self.main_video_duration:
                print(f"Main duration from get_media_duration: {self.main_video_duration:.3f}s")

            for i, stream in enumerate(stream_info.get("streams", [])):
                stream_index = stream.get('index', i)
                track_id_str = f"0:{stream_index}"
                track_type = stream.get("codec_type", "N/A")
                tags = stream.get("tags", {})
                track_title = tags.get("title", "")
                track_language = tags.get("language", "und")

                details = []
                if track_type == "video":
                    details.append(f"{stream.get('codec_name', '?')}")
                    if stream.get('width') and stream.get('height'):
                        details.append(f"{stream.get('width')}x{stream.get('height')}")
                    if stream.get('pix_fmt'): details.append(f"{stream.get('pix_fmt')}")
                    fps_str_display = stream.get('r_frame_rate', '?')
                    details.append(f"{fps_str_display} fps")
                    bitrate = stream.get('bit_rate')
                    if bitrate: details.append(f"{int(bitrate) // 1000} kb/s")

                elif track_type == "audio":
                    details.append(f"{stream.get('codec_name', '?')}")
                    if stream.get('sample_rate'): details.append(f"{stream.get('sample_rate')} Hz")
                    if stream.get('channel_layout'): details.append(f"{stream.get('channel_layout')}")
                    if stream.get('sample_fmt'): details.append(f"{stream.get('sample_fmt')}")
                    bitrate = stream.get('bit_rate')
                    if bitrate: details.append(f"{int(bitrate) // 1000} kb/s")

                elif track_type == "subtitle":
                    details.append(f"{stream.get('codec_name', '?')}")
                else:
                    details.append(f"{stream.get('codec_name', '?')}")

                details_str = ", ".join(filter(None, map(str, details)))

                self.track_tree.insert("", tk.END, iid=track_id_str,
                                       values=(track_id_str, track_type, details_str, track_title, track_language))

            if self.main_video_duration is None:
                warning_msg = "Could not determine main video duration from ffprobe analysis."
                self.output_info.insert(tk.END, f"WARNING: {warning_msg}\n")
                print(f"Warning: {warning_msg}")

        except FfmpegError as e:
            print(f"ffprobe error during track table population: {e}")
            messagebox.showerror("ffprobe Error", f"Failed to get stream info:\n{e}\n\nCheck FFmpeg path in Settings.")
        except Exception as e:
            print(f"Unexpected error during track table population: {e}")
            messagebox.showerror("Error", f"An unexpected error occurred while analyzing tracks:\n{e}")

    def edit_track_data(self, event: tk.Event) -> None:
        """Handles double-click events on the track table to edit Title or Language."""
        item_iid = self.track_tree.identify_row(event.y)
        column_id = self.track_tree.identify_column(event.x)

        if not item_iid or not column_id:
            return

        track_path_id: str = item_iid

        try:
            column_name_internal = self.track_tree.column(column_id, "id")
        except tk.TclError:
            print(f"Could not identify column name for column ID: {column_id}")
            return

        if column_name_internal not in {'title', 'language'}:
            return

        try:
            item_values = list(self.track_tree.item(item_iid, "values"))
            column_index = self.track_tree['columns'].index(column_name_internal)
            current_value = item_values[column_index]
            column_name_display = self.track_tree.heading(column_id)['text']
        except (ValueError, IndexError, KeyError, tk.TclError) as e:
            print(f"Error getting column data for editing: {e}")
            messagebox.showerror("Error", "Could not retrieve data for editing.")
            return

        new_value = simpledialog.askstring(f"Edit {column_name_display}",
                                           f"Enter new value for '{column_name_display}' (Track ID: {track_path_id}):",
                                           initialvalue=str(current_value))

        if new_value is not None:
            if column_name_internal == 'language':
                new_value = new_value.strip().lower()
                if not (len(new_value) == 3 and new_value.isalpha()) and new_value != "":
                    if new_value == "":
                        print(f"Clearing language for track {track_path_id}")
                    else:
                        messagebox.showerror("Invalid Language Code",
                                             "Language code must be 3 letters (e.g., eng, rus, und) or empty.")
                        return

            current_item_values = list(self.track_tree.item(item_iid, "values"))
            try:
                current_item_values[column_index] = new_value
                self.track_tree.item(item_iid, values=tuple(current_item_values))
            except (IndexError, tk.TclError) as e:
                print(f"Error updating treeview item {item_iid}: {e}")
                messagebox.showerror("Error", "Failed to update the display table.")
                return

            if track_path_id not in self.track_data:
                self.track_data[track_path_id] = {}

            if new_value:
                self.track_data[track_path_id][column_name_internal] = new_value
                print(f"Metadata edit stored for {track_path_id}: {column_name_internal} = '{new_value}'")
                self.output_info.insert(tk.END,
                                        f"Metadata updated for {track_path_id}: {column_name_internal} = '{new_value}'\n")
            else:
                if column_name_internal in self.track_data[track_path_id]:
                    del self.track_data[track_path_id][column_name_internal]
                    print(f"Metadata edit removed for {track_path_id}: {column_name_internal}")
                    self.output_info.insert(tk.END, f"Metadata cleared for {track_path_id}: {column_name_internal}\n")
                if not self.track_data[track_path_id]:
                    del self.track_data[track_path_id]

    def add_embed_timecode(self) -> None:
        """Adds an embed ad timecode and file to the list."""
        timecode = self.embed_timecodes_entry.get().strip()
        embed_file = self.embed_file_entry.get().strip()

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

        if time_sec > self.main_video_duration:
            messagebox.showwarning("Warning",
                                   f"Timecode {timecode} ({time_sec:.2f}s) exceeds main video duration ({self.main_video_duration:.2f}s). Ad will not be added.")
            return
        elif time_sec == self.main_video_duration:
            if not messagebox.askyesno("Confirmation",
                                       f"Timecode {timecode} matches the video end.\nThe ad will be appended after the main video.\nContinue?"):
                return

        try:
            # Use an instance with the configured path for analysis
            ffmpeg_helper = self._create_ffmpeg_analyzer_instance()
            embed_duration = ffmpeg_helper.get_media_duration(embed_file)
            if embed_duration is None or embed_duration <= 0.01:
                messagebox.showerror("Ad Duration Error",
                                     f"Could not determine a valid positive duration for the ad file:\n{embed_file}\nEnsure it's a valid video file.")
                return
        except FfmpegError as e:
            messagebox.showerror("ffprobe Error (Ad)",
                                 f"Failed to get ad duration:\n{e}\n\nCheck FFmpeg path in Settings.")
            return

        for ad in self.embed_ads:
            if ad['timecode'] == timecode:
                messagebox.showwarning("Duplicate Timecode",
                                       f"Timecode {timecode} is already added for an embed ad.\nDouble-click the entry in the list to remove it.")
                return

        ad_data = {'path': embed_file, 'timecode': timecode, 'duration': embed_duration}
        self.embed_ads.append(ad_data)
        self.embed_ads.sort(key=lambda x: utils.timecode_to_seconds(x['timecode']))
        print(f"Embed ad added: {ad_data}")

        self._update_embed_listbox()
        self.embed_timecodes_entry.delete(0, tk.END)

    def delete_embed_timecode(self, event: tk.Event) -> None:
        """Deletes the selected embed ad entry from the list."""
        selected_indices = self.embed_timecodes_listbox.curselection()
        if not selected_indices:
            return

        index_to_delete = selected_indices[0]
        try:
            ad_info = self.embed_ads[index_to_delete]
            confirm = messagebox.askyesno("Delete Embed Ad?",
                                          f"Remove the following embed ad insertion:\n"
                                          f"File: {os.path.basename(ad_info['path'])}\n"
                                          f"Timecode: {ad_info['timecode']}\n"
                                          f"Duration: {ad_info['duration']:.2f}s")
            if confirm:
                deleted_ad = self.embed_ads.pop(index_to_delete)
                print(f"Embed ad removed: {deleted_ad}")
                self._update_embed_listbox()
        except IndexError:
            print(f"Error: Index out of range when deleting embed ad. Index: {index_to_delete}, List: {self.embed_ads}")
            messagebox.showerror("Error", "Could not delete selected entry (sync error). Listbox refreshed.")
            self._update_embed_listbox()

    def _update_embed_listbox(self) -> None:
        """Updates the embed ad listbox based on the self.embed_ads list."""
        self.embed_timecodes_listbox.delete(0, tk.END)
        for ad in self.embed_ads:
            display_text = f"{ad['timecode']} ({os.path.basename(ad['path'])}, {ad['duration']:.2f}s)"
            self.embed_timecodes_listbox.insert(tk.END, display_text)

    def add_banner_timecode(self) -> None:
        """Adds a banner display timecode to the list."""
        timecode = self.banner_timecodes_entry.get().strip()
        banner_file = self.banner_file_entry.get().strip()

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

        if time_sec >= self.main_video_duration:
            messagebox.showwarning("Warning",
                                   f"Banner timecode {timecode} ({time_sec:.2f}s) is at or after the *original* video end ({self.main_video_duration:.2f}s).\nEnsure this is intended, considering potential time shifts from embedded ads.")

        if timecode in self.banner_timecodes:
            messagebox.showwarning("Duplicate Timecode",
                                   f"Timecode {timecode} is already added for the banner.\nDouble-click the entry to remove it.")
            return

        self.banner_timecodes.append(timecode)
        self.banner_timecodes.sort(key=lambda tc: utils.timecode_to_seconds(tc) or float('inf'))
        print(f"Banner timecode added: {timecode}. Current list: {self.banner_timecodes}")

        self._update_banner_listbox()
        self.banner_timecodes_entry.delete(0, tk.END)

    def delete_banner_timecode(self, event: tk.Event) -> None:
        """Deletes the selected banner timecode from the list."""
        selected_indices = self.banner_timecodes_listbox.curselection()
        if not selected_indices:
            return

        index = selected_indices[0]
        try:
            tc_to_remove = self.banner_timecodes_listbox.get(index)
            if messagebox.askyesno("Delete Timecode?", f"Remove banner timecode: {tc_to_remove}?"):
                if tc_to_remove in self.banner_timecodes:
                    self.banner_timecodes.remove(tc_to_remove)
                    print(f"Banner timecode removed: {tc_to_remove}. Current list: {self.banner_timecodes}")
                else:
                    print(f"Warning: Timecode {tc_to_remove} not found in internal list during deletion.")
                self._update_banner_listbox()
        except (IndexError, tk.TclError) as e:
            print(f"Error deleting banner timecode: Index {index}, Error: {e}")
            messagebox.showerror("Error", "Could not delete selected entry.")
            self._update_banner_listbox()

    def _update_banner_listbox(self) -> None:
        """Updates the banner timecode listbox based on the self.banner_timecodes list."""
        self.banner_timecodes_listbox.delete(0, tk.END)
        for tc in self.banner_timecodes:
            self.banner_timecodes_listbox.insert(tk.END, tc)

    def detect_hwaccels(self) -> List[str]:
        """Attempts to detect available ffmpeg hardware acceleration methods using configured path."""
        ffmpeg_cmd = self._get_effective_ffmpeg_path()  # Use configured path or fallback
        try:
            process = subprocess.Popen([ffmpeg_cmd, "-hwaccels", "-hide_banner"],
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', errors='replace')
            output, _ = process.communicate(timeout=5)
            hwaccels = [line.strip() for line in output.splitlines()
                        if line.strip() != "" and "Hardware acceleration methods" not in line]
            print(f"Detected HW Accels (using '{ffmpeg_cmd}'): {hwaccels}")
            return hwaccels if hwaccels else ["none available"]
        except FileNotFoundError:
            print(f"Error detecting HW Accels: '{ffmpeg_cmd}' not found.")
            return ["ffmpeg not found"]
        except subprocess.TimeoutExpired:
            print(f"Error detecting HW Accels: '{ffmpeg_cmd}' timed out.")
            return ["detection timed out"]
        except Exception as e:
            print(f"Error detecting HW Accels using '{ffmpeg_cmd}': {e}")
            return ["error detecting"]

    def _prepare_and_generate_commands(self) -> Optional[Tuple[List[str], str, List[str]]]:
        """Gathers all inputs, validates them, creates an FFMPEG instance with path, and generates commands."""
        self.cleanup_temp_files()
        self.output_info.delete('1.0', tk.END)
        self.output_info.insert('1.0', "Preparing and generating ffmpeg commands...\n")
        self.master.update_idletasks()

        input_file = self.input_file_entry.get().strip()
        output_file = self.output_file_entry.get().strip()
        ffmpeg_path = self._get_ffmpeg_executable_path()  # Get configured path or None

        video_codec = self.video_codec_entry.get().strip() or None
        video_preset = self.video_preset_entry.get().strip() or None
        video_cq = self.video_cq_entry.get().strip() or None
        video_bitrate = self.video_bitrate_entry.get().strip() or None
        audio_codec = self.audio_codec_entry.get().strip() or None
        audio_bitrate = self.audio_bitrate_entry.get().strip() or None
        video_fps = self.video_fps_entry.get().strip() or None
        hwaccel = self.hwaccel_combo.get().strip() or None
        additional_encoding = self.additional_encoding_entry.get().strip() or None
        encoding_params_str = self.encoding_entry.get().strip()

        banner_file = self.banner_file_entry.get().strip() or None
        moving_file = self.moving_file_entry.get().strip() or None
        banner_track_pix_fmt = self.banner_track_pix_fmt_entry.get().strip() or None
        banner_gap_color = self.banner_gap_color_entry.get().strip() or None

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

        error_messages = []
        if not input_file: error_messages.append("- Input file not selected.")
        if not output_file: error_messages.append("- Output file not selected.")
        if input_file and not os.path.exists(input_file): error_messages.append(f"- Input file not found: {input_file}")
        if self.main_video_duration is None or self.main_video_duration <= 0:
            error_messages.append("- Could not determine main video duration. Re-select input file.")
        if not self.main_video_params or self.main_video_params.width is None or self.main_video_params.fps is None:
            error_messages.append("- Could not get essential main video parameters. Re-select input file.")

        if banner_file and not os.path.exists(banner_file):
            self.output_info.insert(tk.END, f"WARNING: Banner file '{banner_file}' not found, ignored.\n")
        if moving_file and not os.path.exists(moving_file):
            self.output_info.insert(tk.END, f"WARNING: Moving logo file '{moving_file}' not found, ignored.\n")
        if banner_file and not self.banner_timecodes:
            self.output_info.insert(tk.END, "WARNING: Banner file selected, but no timecodes added. Banner ignored.\n")

        if error_messages:
            full_error_msg = "Please fix the following errors:\n" + "\n".join(error_messages)
            messagebox.showerror("Validation Error", full_error_msg)
            self.output_info.insert(tk.END, f"VALIDATION ERROR:\n{full_error_msg}\n")
            return None

        try:
            # Create FFMPEG instance, passing the configured ffmpeg path
            self.ffmpeg_instance = ffmpeg.FFMPEG(
                ffmpeg_path=ffmpeg_path,  # Pass the configured path
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
                additional_encoding=additional_encoding
            )
        except Exception as e:
            messagebox.showerror("ffmpeg Setup Error", f"Failed to initialize ffmpeg settings: {e}")
            self.output_info.insert(tk.END, f"FFMPEG INIT ERROR:\nFailed to initialize settings: {e}\n")
            return None

        print("Calling generate_ffmpeg_commands...")
        # Log parameters passed to FFMPEG constructor above

        try:
            # The instance now knows the ffmpeg path to use internally for ffprobe
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
            self.output_info.insert(tk.END, "Commands generated successfully.\n")
            print(f"Potential temporary files: {self.temp_files_to_clean}")
            return result

        except (CommandGenerationError, FfmpegError) as e:
            error_msg = f"Command Generation Error:\n{e}\n\nCheck FFmpeg path in Settings."
            messagebox.showerror("Generation Error", error_msg)
            self.output_info.insert(tk.END, f"COMMAND GENERATION ERROR:\n{error_msg}\n")
            self.cleanup_temp_files()
            return None
        except Exception as e:
            error_msg = f"An unexpected error occurred during command generation:\n{type(e).__name__}: {e}"
            messagebox.showerror("Unexpected Error", error_msg)
            self.output_info.insert(tk.END, f"UNEXPECTED GENERATION ERROR:\n{error_msg}\n")
            import traceback
            traceback.print_exc()
            self.cleanup_temp_files()
            return None

    def show_ffmpeg_commands(self) -> None:
        """Generates and displays the ffmpeg commands in the output area."""
        result = self._prepare_and_generate_commands()
        self.output_info.delete('1.0', tk.END)

        if result:
            preproc_cmds, main_cmd, temp_files_generated = result
            output_text = "--- Generated ffmpeg Commands ---\n\n"

            output_text += "--- Potential Temporary Files ---\n"
            if temp_files_generated:
                output_text += "\n".join([f"  - {os.path.basename(f)}" for f in temp_files_generated])
            else:
                output_text += "  (None)"
            output_text += "\n\n"

            if preproc_cmds:
                output_text += f"--- Preprocessing Commands ({len(preproc_cmds)}) ---\n"
                for i, cmd in enumerate(preproc_cmds):
                    output_text += f"[{i + 1}]: {cmd}\n{'-' * 60}\n"
                output_text += "\n"
            else:
                output_text += "--- No Preprocessing Commands Needed ---\n\n"

            if main_cmd:
                output_text += "--- Main Conversion Command ---\n"
                output_text += main_cmd + "\n"
            else:
                output_text += "--- ERROR: Main conversion command could not be generated ---"

            self.output_info.insert('1.0', output_text)
            self.output_info.yview_moveto(0.0)
        else:
            self.output_info.insert('1.0',
                                    "Failed to generate commands. Check settings and error messages above/in console.")

    def start_conversion(self) -> None:
        """Initiates the ffmpeg conversion process after confirmation."""
        result = self._prepare_and_generate_commands()  # Gets command *argument* strings

        if not result:
            messagebox.showerror("Cancelled", "Failed to prepare ffmpeg commands. Conversion cancelled.")
            return

        preproc_cmds_args, main_cmd_args, _ = result  # Argument strings now
        ffmpeg_exe_to_run = self._get_effective_ffmpeg_path()  # Get path to run commands with

        num_preproc = len(preproc_cmds_args) if preproc_cmds_args else 0
        confirm_message_parts = [f"Will use FFmpeg: '{ffmpeg_exe_to_run}'\n"]
        confirm_message_parts.append("The following steps will be executed:")
        steps = []
        if num_preproc > 0:
            steps.append(f"Preprocess {num_preproc} segments/ads.")
        if main_cmd_args:  # Check if main command args exist
            steps.append("Perform main conversion.")
        else:
            messagebox.showerror("Error", "No main conversion command generated.")
            return

        if not steps:
            messagebox.showerror("Error", "No commands to execute!")
            return

        for i, step_desc in enumerate(steps):
            confirm_message_parts.append(f"\n{i + 1}. {step_desc}")

        confirm_message_parts.append("\n\nContinue?")
        confirm_message = "".join(confirm_message_parts)

        if not messagebox.askyesno("Confirm Conversion", confirm_message):
            print("Conversion cancelled by user.")
            self.output_info.insert(tk.END, "\nConversion cancelled by user.\n")
            self.cleanup_temp_files()
            return

        self.output_info.delete('1.0', tk.END)
        self.output_info.insert('1.0', f"Starting conversion process (Using FFmpeg: {ffmpeg_exe_to_run})...\n\n")
        self.master.update()

        try:
            start_time_total = time.time()

            if preproc_cmds_args:  # Use arg strings
                self.output_info.insert(tk.END, f"--- Stage 1: Preprocessing ({len(preproc_cmds_args)} commands) ---\n")
                self.master.update()
                start_time_preproc = time.time()
                for i, cmd_args in enumerate(preproc_cmds_args):  # Iterate args
                    step_name = f"Preprocessing {i + 1}/{len(preproc_cmds_args)}"
                    self.output_info.insert(tk.END, f"\nRunning: {step_name}...\n")
                    self.output_info.see(tk.END)
                    self.master.update()
                    start_time_step = time.time()
                    # Call static method with command args and executable path
                    ffmpeg.FFMPEG.run_ffmpeg_command(cmd_args, step_name, ffmpeg_executable=ffmpeg_exe_to_run)
                    end_time_step = time.time()
                    self.output_info.insert(tk.END,
                                            f"Success: {step_name} (took {end_time_step - start_time_step:.2f}s)\n")
                    self.output_info.see(tk.END)
                    self.master.update()
                end_time_preproc = time.time()
                self.output_info.insert(tk.END,
                                        f"\n--- Preprocessing finished (Total time: {end_time_preproc - start_time_preproc:.2f}s) ---\n")

            if main_cmd_args:  # Use arg string
                step_name = "Main Conversion"
                self.output_info.insert(tk.END, f"\n--- Stage 2: {step_name} ---\n")
                self.output_info.see(tk.END)
                self.master.update()
                start_time_main = time.time()
                # Call static method with command args and executable path
                ffmpeg.FFMPEG.run_ffmpeg_command(main_cmd_args, step_name, ffmpeg_executable=ffmpeg_exe_to_run)
                end_time_main = time.time()
                self.output_info.insert(tk.END,
                                        f"\nSuccess: {step_name} (took {end_time_main - start_time_main:.2f}s)\n")
                self.output_info.see(tk.END)
                self.master.update()

            end_time_total = time.time()
            success_msg = (f"\n--- SUCCESS: Conversion completed successfully! ---"
                           f"\n--- Total time: {end_time_total - start_time_total:.2f}s ---")
            self.output_info.insert(tk.END, success_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showinfo("Success", "Conversion completed successfully!")

        except (ConversionError, FfmpegError) as e:
            error_msg = f"\n--- CONVERSION FAILED ---\n{e}\n--- PROCESS HALTED ---\nCheck FFmpeg path and command output."
            print(error_msg)
            self.output_info.insert(tk.END, error_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showerror("Conversion Failed", f"An error occurred during conversion:\n\n{e}")
        except Exception as e:
            error_msg = f"\n--- UNEXPECTED ERROR ---\n{type(e).__name__}: {e}\n--- PROCESS HALTED ---"
            print(error_msg)
            import traceback
            traceback.print_exc()
            self.output_info.insert(tk.END, error_msg + "\n")
            self.output_info.see(tk.END)
            messagebox.showerror("Critical Error",
                                 f"An unexpected error occurred:\n{type(e).__name__}: {e}\n\nCheck console for details.")

        finally:
            self.output_info.insert(tk.END, "\nRunning final cleanup of temporary files...\n")
            self.output_info.see(tk.END)
            self.master.update()
            self.cleanup_temp_files()
            self.output_info.insert(tk.END, "Cleanup finished.\n")
            self.output_info.see(tk.END)

    def _get_settings_filepath(self, use_gui_field=True) -> str:
        """Gets the full path to the settings file, using GUI field if specified."""
        if use_gui_field:
            gui_path = self.settings_path_entry.get().strip()
            if gui_path:
                # Basic validation: check if it looks like a plausible path
                if os.path.isdir(os.path.dirname(gui_path)) or not os.path.dirname(
                        gui_path):  # Allow filename in current dir
                    # Ensure it ends with .json if not specified otherwise
                    if not gui_path.lower().endswith(".json"):
                        print(f"Warning: Settings path '{gui_path}' doesn't end with .json. Appending.")
                        gui_path += ".json"
                    return gui_path
                else:
                    print(f"Warning: Invalid directory in settings path: {gui_path}. Falling back to default.")

        # Fallback to default location
        try:
            base_path = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            base_path = os.getcwd()
        return os.path.join(base_path, config.SETTINGS_FILENAME)

    def _load_settings(self, filepath: Optional[str] = None) -> None:
        """Loads settings from the JSON file and applies them to the GUI."""
        if filepath is None:
            # Get path using the GUI field value by default when loading
            filepath = self._get_settings_filepath(use_gui_field=True)

        print(f"Attempting to load settings from: {filepath}")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                settings_data = json.load(f)
            print("Settings loaded successfully.")

            # Apply to widgets mapped in self.widget_map
            for key, value in settings_data.items():
                if key in self.widget_map:
                    widget, widget_type = self.widget_map[key]
                    try:
                        if widget_type == 'entry':
                            if isinstance(widget, (tk.Entry, ttk.Entry)):
                                widget.delete(0, tk.END)
                                widget.insert(0, str(value))
                            else:
                                print(f"  Warning: Widget for key '{key}' is not an Entry.")
                        elif widget_type == 'combo':
                            if isinstance(widget, ttk.Combobox):
                                current_values = widget['values']
                                if value in current_values:
                                    widget.set(str(value))
                                else:
                                    print(
                                        f"  Warning: Loaded value '{value}' for key '{key}' not in Combobox options {current_values}. Skipping.")
                            else:
                                print(f"  Warning: Widget for key '{key}' is not a Combobox.")
                    except Exception as e:
                        print(f"  Warning: Could not set widget for key '{key}': {e}")
                else:
                    print(f"  Warning: Setting key '{key}' not found in widget map.")

            # Explicitly update the settings path entry field display after loading
            # This ensures it shows the path we just loaded from, even if it was the default
            self.settings_path_entry.delete(0, tk.END)
            self.settings_path_entry.insert(0, filepath)


        except FileNotFoundError:
            print(f"Settings file not found at '{filepath}'. Using default values.")
            # Display default path in the entry field if file wasn't found
            self.settings_path_entry.delete(0, tk.END)
            self.settings_path_entry.insert(0, self._get_settings_filepath(use_gui_field=False))
            if filepath != self._get_settings_filepath(
                    use_gui_field=False):  # Show warning only if user specified a path that wasn't found
                messagebox.showwarning("Load Error", f"Settings file not found:\n{filepath}")
        except json.JSONDecodeError:
            print(f"Error decoding JSON from '{filepath}'. File might be corrupt. Using default values.")
            messagebox.showwarning("Settings Error",
                                   f"Could not read settings file:\n{filepath}\n\nIt might be corrupt. Using default values.")
            self.settings_path_entry.delete(0, tk.END)
            self.settings_path_entry.insert(0, self._get_settings_filepath(use_gui_field=False))
        except Exception as e:
            print(f"Unexpected error loading settings: {e}")
            messagebox.showerror("Settings Error", f"An unexpected error occurred while loading settings:\n{e}")
            self.settings_path_entry.delete(0, tk.END)
            self.settings_path_entry.insert(0, self._get_settings_filepath(use_gui_field=False))

    def _save_settings_manual(self) -> None:
        """Handles the manual saving of settings via the button."""
        # Save to the path specified in the settings path entry field
        target_filepath = self._get_settings_filepath(use_gui_field=True)
        print(f"Manually saving settings to {target_filepath}...")
        self._save_settings(filepath=target_filepath)
        messagebox.showinfo("Settings Saved", f"Settings saved to\n{target_filepath}")

    def _save_settings(self, filepath: Optional[str] = None) -> None:
        """Saves current settings from the GUI to the JSON file."""
        settings_to_save = {}
        print("Gathering settings to save...")

        for key, (widget, widget_type) in self.widget_map.items():
            try:
                value = None
                if widget_type == 'entry':
                    if isinstance(widget, (tk.Entry, ttk.Entry)):
                        value = widget.get()
                    else:
                        continue  # Skip warning
                elif widget_type == 'combo':
                    if isinstance(widget, ttk.Combobox):
                        value = widget.get()
                    else:
                        continue  # Skip warning

                if value is not None: settings_to_save[key] = value
                # else: pass # No need to warn for unhandled types

            except Exception as e:
                print(f"  Warning: Could not get value for key '{key}': {e}")

        if filepath is None:
            # Get path using the GUI field value by default when saving
            filepath = self._get_settings_filepath(use_gui_field=True)

        print(f"Attempting to save settings to: {filepath}")
        try:
            # Ensure the target directory exists
            target_dir = os.path.dirname(filepath)
            if target_dir and not os.path.exists(target_dir):
                print(f"Creating directory for settings file: {target_dir}")
                os.makedirs(target_dir)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(settings_to_save, f, indent=4)
            print("Settings saved successfully.")
        except IOError as e:
            print(f"Error writing settings file to '{filepath}': {e}")
            messagebox.showerror("Settings Error", f"Could not save settings to:\n{filepath}\n\n{e}")
        except Exception as e:
            print(f"Unexpected error saving settings: {e}")
            messagebox.showerror("Settings Error", f"An unexpected error occurred while saving settings:\n{e}")

    def _load_settings_manual(self) -> None:
        """Handles the manual loading of settings via the button."""
        # Ask user to select a settings file
        initial_dir = os.path.dirname(self._get_settings_filepath(use_gui_field=True))
        filepath = filedialog.askopenfilename(
            title="Select Settings File to Load",
            initialdir=initial_dir,
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            defaultextension=".json"
        )
        if not filepath:
            print("Settings load cancelled.")
            return

        # Load from the selected path
        print(f"Manually loading settings from {filepath}...")
        self._load_settings(filepath=filepath)
        messagebox.showinfo("Settings Loaded", f"Settings loaded from\n{filepath}")

    def _reset_settings_to_defaults(self, ask_confirm=True) -> None:
        """Resets GUI fields to default values from config.py."""
        if ask_confirm:
            confirm = messagebox.askyesno("Reset Settings",
                                          "Are you sure you want to reset all transcoding and advertisement settings to their default values? (Paths will not be reset)")
            if not confirm:
                print("Reset cancelled by user.")
                return

        print("Resetting settings to defaults...")
        try:
            default_settings = {
                "video_codec": config.VIDEO_CODEC,
                "video_preset": config.VIDEO_PRESET,
                "video_cq": config.VIDEO_CQ,
                "video_bitrate": config.VIDEO_BITRATE,
                "video_fps": "",
                "audio_codec": config.AUDIO_CODEC,
                "audio_bitrate": config.AUDIO_BITRATE,
                "hwaccel": config.HWACCEL,
                "additional_encoding": config.ADDITIONAL_ENCODING,
                "encoding_params_str": "",
                "banner_track_pix_fmt": config.BANNER_TRACK_PIX_FMT,
                "banner_gap_color": config.BANNER_GAP_COLOR,
                "moving_speed": str(config.MOVING_SPEED),
                "moving_logo_relative_height": f"{config.MOVING_LOGO_RELATIVE_HEIGHT:.3f}",
                "moving_logo_alpha": str(config.MOVING_LOGO_ALPHA),
                # NOTE: We are NOT resetting the path fields here
                # "ffmpeg_path": "",
                # "settings_path": self._get_settings_filepath(use_gui_field=False),
            }

            for key, default_value in default_settings.items():
                if key in self.widget_map:
                    widget, widget_type = self.widget_map[key]
                    try:
                        if widget_type == 'entry':
                            if isinstance(widget, (tk.Entry, ttk.Entry)):
                                widget.delete(0, tk.END)
                                widget.insert(0, default_value)
                        elif widget_type == 'combo':
                            if isinstance(widget, ttk.Combobox):
                                if default_value in widget['values']:
                                    widget.set(default_value)
                                elif widget['values']:
                                    widget.set(widget['values'][0])
                                else:
                                    widget.set("")
                    except Exception as e:
                        print(f"  Warning: Could not reset widget for key '{key}': {e}")

            print("Settings reset to defaults.")
            if ask_confirm:  # Show message only if user initiated
                messagebox.showinfo("Settings Reset", "Settings have been reset to their default values.")

        except Exception as e:
            print(f"Error resetting settings to defaults: {e}")
            messagebox.showerror("Reset Error", f"An error occurred while resetting settings:\n{e}")


# Main execution
if __name__ == '__main__':
    root = tk.Tk()
    app = VideoConverterGUI(root)
    root.mainloop()
