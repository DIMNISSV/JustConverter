# converter/gui.py
import json  # Import json module
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
        # Increment patch version for new feature
        self.VERSION = '0.8.1'
        self.TITLE = "JustConverter + AdBurner"
        self.AUTHOR = "dimnissv"
        master.title(f'{self.TITLE} ({self.AUTHOR}) {self.VERSION}')

        self.notebook = ttk.Notebook(master)

        # Create tabs
        self.main_tab = ttk.Frame(self.notebook)
        self.advertisement_tab = ttk.Frame(self.notebook)
        self.transcode_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.start_tab = ttk.Frame(self.notebook)
        self.about_tab = ttk.Frame(self.notebook)

        # State variables
        self.track_data: Dict[str, Dict[str, str]] = {}
        self.main_video_duration: Optional[float] = None
        self.main_video_params: Optional[StreamParams] = None
        self.embed_ads: List[Dict[str, Any]] = []
        self.banner_timecodes: List[str] = []
        self.temp_files_to_clean: List[str] = []
        self.widget_map: dict[str, tk.Entry | ttk.Combobox] = {}

        # Build UI elements for each tab
        self._create_main_tab_widgets()
        self._create_advertisement_tab_widgets()
        self._create_transcode_tab_widgets()
        self._create_settings_tab_widgets()
        self._create_start_tab_widgets()
        self._create_about_tab_widgets()

        # Add tabs to the notebook (Settings tab inserted before Start)
        self.notebook.add(self.main_tab, text="Files")
        self.notebook.add(self.advertisement_tab, text="Advertisement")
        self.notebook.add(self.transcode_tab, text="Transcoding")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.start_tab, text="Start")
        self.notebook.add(self.about_tab, text="About")

        self.notebook.grid(row=0, column=0, sticky="nsew")
        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)

        # Load settings after widgets are created
        self._load_settings()

        # Handle window closing
        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.ffmpeg_instance: Optional[ffmpeg.FFMPEG] = None

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
        # Value loaded/set by _load_settings or defaults

        self.banner_gap_color_label = tk.Label(self.advertisement_tab, text='Banner Gap Color:')
        self.banner_gap_color_label.grid(row=7, column=0, padx=5, pady=5, sticky='w')
        self.banner_gap_color_entry = tk.Entry(self.advertisement_tab, width=15)
        self.banner_gap_color_entry.grid(row=7, column=1, padx=5, pady=5, sticky='w')
        # Value loaded/set by _load_settings or defaults

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
        # Value loaded/set by _load_settings or defaults

        self.moving_logo_relative_height_label = tk.Label(self.advertisement_tab, text="Logo Height (Relative):")
        self.moving_logo_relative_height_label.grid(row=10, column=0, padx=5, pady=5, sticky="w")
        self.moving_logo_relative_height_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_logo_relative_height_entry.grid(row=10, column=1, padx=5, pady=5, sticky="w")
        # Value loaded/set by _load_settings or defaults

        self.moving_logo_alpha_label = tk.Label(self.advertisement_tab, text="Logo Alpha (0.0-1.0):")
        self.moving_logo_alpha_label.grid(row=11, column=0, padx=5, pady=5, sticky="w")
        self.moving_logo_alpha_entry = tk.Entry(self.advertisement_tab, width=10)
        self.moving_logo_alpha_entry.grid(row=11, column=1, padx=5, pady=5, sticky="w")
        # Value loaded/set by _load_settings or defaults

    def _create_transcode_tab_widgets(self) -> None:
        """Creates widgets for the 'Transcoding' tab."""
        # Video Settings
        self.video_codec_label = tk.Label(self.transcode_tab, text='Video Codec:')
        self.video_codec_label.grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.video_codec_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_codec_entry.grid(row=0, column=1, padx=5, pady=5, sticky='w')
        # Value loaded/set by _load_settings or defaults

        self.video_preset_label = tk.Label(self.transcode_tab, text='Preset:')
        self.video_preset_label.grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.video_preset_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_preset_entry.grid(row=1, column=1, padx=5, pady=5, sticky='w')
        # Value loaded/set by _load_settings or defaults

        self.video_cq_label = tk.Label(self.transcode_tab, text='CQ/CRF (Quality):')
        self.video_cq_label.grid(row=2, column=0, padx=5, pady=5, sticky='w')
        self.video_cq_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_cq_entry.grid(row=2, column=1, padx=5, pady=5, sticky='w')
        # Value loaded/set by _load_settings or defaults

        self.video_bitrate_label = tk.Label(self.transcode_tab, text='Video Bitrate (e.g., 5000k, 0=CQ):')
        self.video_bitrate_label.grid(row=3, column=0, padx=5, pady=5, sticky='w')
        self.video_bitrate_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_bitrate_entry.grid(row=3, column=1, padx=5, pady=5, sticky='w')
        # Value loaded/set by _load_settings or defaults

        self.video_fps_label = tk.Label(self.transcode_tab, text='Video FPS (Optional Override):')
        self.video_fps_label.grid(row=4, column=0, padx=5, pady=5, sticky='w')
        self.video_fps_entry = tk.Entry(self.transcode_tab, width=20)
        self.video_fps_entry.grid(row=4, column=1, padx=5, pady=5, sticky='w')
        # Value loaded/set by _load_settings or defaults (or after analysis)

        # Audio Settings
        self.audio_codec_label = tk.Label(self.transcode_tab, text='Audio Codec:')
        self.audio_codec_label.grid(row=5, column=0, padx=5, pady=5, sticky='w')
        self.audio_codec_entry = tk.Entry(self.transcode_tab, width=20)
        self.audio_codec_entry.grid(row=5, column=1, padx=5, pady=5, sticky='w')
        # Value loaded/set by _load_settings or defaults

        self.audio_bitrate_label = tk.Label(self.transcode_tab, text='Audio Bitrate (e.g., 192k):')
        self.audio_bitrate_label.grid(row=6, column=0, padx=5, pady=5, sticky='w')
        self.audio_bitrate_entry = tk.Entry(self.transcode_tab, width=20)
        self.audio_bitrate_entry.grid(row=6, column=1, padx=5, pady=5, sticky='w')
        # Value loaded/set by _load_settings or defaults

        # Hardware Acceleration
        self.hwaccel_label = tk.Label(self.transcode_tab, text="Hardware Acceleration:")
        self.hwaccel_label.grid(row=7, column=0, padx=5, pady=5, sticky="w")
        self.hwaccel_combo = ttk.Combobox(self.transcode_tab, values=self.detect_hwaccels(),
                                          state="readonly")
        self.hwaccel_combo.grid(row=7, column=1, padx=5, pady=5, sticky="w")
        # Value loaded/set by _load_settings or defaults

        # Additional Parameters
        self.additional_encoding_label = tk.Label(self.transcode_tab, text="Additional Params:")
        self.additional_encoding_label.grid(row=8, column=0, padx=5, pady=5, sticky="w")
        self.additional_encoding_entry = tk.Entry(self.transcode_tab, width=60)
        self.additional_encoding_entry.grid(row=8, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        # Value loaded/set by _load_settings or defaults

        # Manual Override
        self.encoding_label = tk.Label(self.transcode_tab, text="Manual Params (Overrides Above):")
        self.encoding_label.grid(row=9, column=0, padx=5, pady=5, sticky="w")
        self.encoding_entry = tk.Entry(self.transcode_tab, width=60)
        self.encoding_entry.grid(row=9, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        # Manual entry, not usually saved/loaded by default unless specified

        # Configure resizing
        self.transcode_tab.grid_columnconfigure(1, weight=1)

    def _create_settings_tab_widgets(self) -> None:
        """Creates widgets for the 'Settings' tab."""
        self.widget_map = {
            "video_codec": getattr(self, 'video_codec_entry', None),
            "video_preset": getattr(self, 'video_preset_entry', None),
            "video_cq": getattr(self, 'video_cq_entry', None),
            "video_bitrate": getattr(self, 'video_bitrate_entry', None),
            "video_fps": getattr(self, 'video_fps_entry', None),
            "audio_codec": getattr(self, 'audio_codec_entry', None),
            "audio_bitrate": getattr(self, 'audio_bitrate_entry', None),
            "hwaccel": getattr(self, 'hwaccel_combo', None),
            "additional_encoding": getattr(self, 'additional_encoding_entry', None),
            "banner_track_pix_fmt": getattr(self, 'banner_track_pix_fmt_entry', None),
            "banner_gap_color": getattr(self, 'banner_gap_color_entry', None),
            "moving_speed": getattr(self, 'moving_speed_entry', None),
            "moving_logo_relative_height": getattr(self, 'moving_logo_relative_height_entry', None),
            "moving_logo_alpha": getattr(self, 'moving_logo_alpha_entry', None),
        }
        settings_frame = ttk.LabelFrame(self.settings_tab, text="Manage Settings", padding=10)
        settings_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.settings_tab.grid_columnconfigure(0, weight=1)  # Allow frame to expand

        # Save Settings Button
        self.save_settings_button = tk.Button(settings_frame, text="Save Current Settings",
                                              command=self._save_settings)
        self.save_settings_button.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        # Load Settings Button
        self.load_settings_button = tk.Button(settings_frame, text="Load Settings from File",
                                              command=self._load_settings_manual)  # Use manual load function
        self.load_settings_button.grid(row=1, column=0, padx=5, pady=5, sticky="w")

        # Reset Settings Button
        self.reset_settings_button = tk.Button(settings_frame, text="Reset Settings to Defaults",
                                               command=self._reset_settings_to_defaults)
        self.reset_settings_button.grid(row=2, column=0, padx=5, pady=5, sticky="w")

        # Add some explanation
        explanation_label = tk.Label(settings_frame,
                                     text="Settings are automatically saved on exit and loaded on startup.\n"
                                          f"Default file: {config.SETTINGS_FILENAME}",
                                     justify=tk.LEFT, wraplength=350)
        explanation_label.grid(row=3, column=0, columnspan=2, padx=5, pady=10, sticky="w")

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
        """Handles the window close event, ensuring temporary files are cleaned up and settings saved."""
        print("Close requested. Saving settings and cleaning up temporary files...")
        self._save_settings()  # Save settings before closing
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
        """Resets the GUI state, clearing inputs and internal data. Does NOT reset saved settings fields."""
        print("Resetting GUI state (excluding saved settings fields)...")
        self.cleanup_temp_files()

        # Clear input/output files
        self.input_file_entry.delete(0, tk.END)
        self.output_file_entry.delete(0, tk.END)

        # Clear state variables related to the specific conversion
        self.track_data = {}
        self.main_video_duration = None
        self.main_video_params = None
        self.embed_ads = []
        self.banner_timecodes = []
        self.temp_files_to_clean = []

        # Clear listboxes and ad file entries
        self.embed_file_entry.delete(0, tk.END)  # Clear ad files as they are specific to input
        self.banner_file_entry.delete(0, tk.END)
        self.moving_file_entry.delete(0, tk.END)
        self.embed_timecodes_entry.delete(0, tk.END)
        self.banner_timecodes_entry.delete(0, tk.END)
        self.embed_timecodes_listbox.delete(0, tk.END)
        self.banner_timecodes_listbox.delete(0, tk.END)

        # Clear track treeview
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)

        # Clear output log
        self.output_info.delete('1.0', tk.END)

        # DO NOT reset the Advertisement and Transcoding fields here,
        # as they should retain their values from _load_settings() or user edits.
        # Only fields specific to a single run (like input/output file) are cleared.

        self.master.update_idletasks()
        print("Partial state reset complete.")

    def _load_settings(self, settings_file_path: Optional[str] = None) -> None:
        """
        Loads settings from the specified JSON file (or default) and populates the GUI fields.

        Args:
            settings_file_path: Path to the settings file. If None, uses default from config.
        """
        if settings_file_path is None:
            settings_file_path = config.SETTINGS_FILENAME

        settings = {}
        defaults = {
            # Transcoding Tab
            "video_codec": config.VIDEO_CODEC,
            "video_preset": config.VIDEO_PRESET,
            "video_cq": config.VIDEO_CQ,
            "video_bitrate": config.VIDEO_BITRATE,
            "video_fps": "",  # Default FPS override is empty
            "audio_codec": config.AUDIO_CODEC,
            "audio_bitrate": config.AUDIO_BITRATE,
            "hwaccel": config.HWACCEL,
            "additional_encoding": config.ADDITIONAL_ENCODING,
            # Advertisement Tab
            "banner_track_pix_fmt": config.BANNER_TRACK_PIX_FMT,
            "banner_gap_color": config.BANNER_GAP_COLOR,
            "moving_speed": str(config.MOVING_SPEED),
            "moving_logo_relative_height": f"{config.MOVING_LOGO_RELATIVE_HEIGHT:.3f}",
            "moving_logo_alpha": str(config.MOVING_LOGO_ALPHA),
        }

        try:
            if os.path.exists(settings_file_path):
                with open(settings_file_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                print(f"Loaded settings from {settings_file_path}")
            else:
                print(f"Settings file not found ({settings_file_path}). Using defaults.")
                settings = defaults  # Use defaults if file doesn't exist
        except (json.JSONDecodeError, IOError, Exception) as e:
            print(f"Error loading settings from {settings_file_path}: {e}. Using defaults.")
            settings = defaults  # Use defaults on error

        # --- Helper to safely set widget values ---
        def set_widget_value(widget, value):
            if widget is None: return
            try:
                if isinstance(widget, ttk.Combobox):
                    if value in widget['values']:
                        widget.set(str(value))
                    else:
                        print(f"Warning: Value '{value}' not found in Combobox options. Using default.")
                        default_val = defaults.get(widget.settings_key, "")
                        if default_val in widget['values']:
                            widget.set(default_val)
                        else:
                            widget.set(widget['values'][0] if widget['values'] else "")
                elif isinstance(widget, tk.Entry):
                    widget.delete(0, tk.END)
                    widget.insert(0, str(value))
            except Exception as e:
                print(f"Error setting widget value '{value}': {e}")

        # --- Populate widgets ---
        applied_settings = 0
        for key, widget in self.widget_map.items():
            if widget:
                widget.settings_key = key  # Assign key for default lookup
                value_to_set = settings.get(key, defaults.get(key, ""))
                set_widget_value(widget, value_to_set)
                applied_settings += 1
            else:
                print(f"Warning: Widget for setting '{key}' not found during load.")

        print(f"Applied {applied_settings} settings to GUI widgets.")

    def _load_settings_manual(self) -> None:
        """Handles the 'Load Settings' button click."""
        # For now, just reload from the default file.
        # Could be extended later to ask for a file path.
        if messagebox.askokcancel("Load Settings",
                                  f"This will reload settings from '{config.SETTINGS_FILENAME}' and overwrite current fields.\nContinue?"):
            print("Manual settings load requested.")
            self._load_settings()
            messagebox.showinfo("Settings Loaded", f"Settings loaded from '{config.SETTINGS_FILENAME}'.")

    def _reset_settings_to_defaults(self) -> None:
        """Resets settings fields in the GUI to their default values from config.py."""
        if not messagebox.askyesno("Reset Settings?",
                                   "Reset all transcoding and advertisement parameters to their default values?"):
            return

        print("Resetting settings to defaults...")
        defaults = {
            # Transcoding Tab
            "video_codec": config.VIDEO_CODEC,
            "video_preset": config.VIDEO_PRESET,
            "video_cq": config.VIDEO_CQ,
            "video_bitrate": config.VIDEO_BITRATE,
            "video_fps": "",  # Default FPS override is empty
            "audio_codec": config.AUDIO_CODEC,
            "audio_bitrate": config.AUDIO_BITRATE,
            "hwaccel": config.HWACCEL,
            "additional_encoding": config.ADDITIONAL_ENCODING,
            # Advertisement Tab
            "banner_track_pix_fmt": config.BANNER_TRACK_PIX_FMT,
            "banner_gap_color": config.BANNER_GAP_COLOR,
            "moving_speed": str(config.MOVING_SPEED),
            "moving_logo_relative_height": f"{config.MOVING_LOGO_RELATIVE_HEIGHT:.3f}",
            "moving_logo_alpha": str(config.MOVING_LOGO_ALPHA),
        }

        # --- Helper to safely set widget values (same as in _load_settings) ---
        def set_widget_value(widget, value):
            if widget is None: return
            try:
                if isinstance(widget, ttk.Combobox):
                    if value in widget['values']:
                        widget.set(str(value))
                    else:  # Should not happen with defaults if lists are correct
                        widget.set(widget['values'][0] if widget['values'] else "")
                elif isinstance(widget, tk.Entry):
                    widget.delete(0, tk.END)
                    widget.insert(0, str(value))
            except Exception as e:
                print(f"Error setting widget default value '{value}': {e}")

        # --- Reset widgets ---
        reset_count = 0
        for key, widget in self.widget_map.items():
            if widget:
                widget.settings_key = key  # Assign key (though not strictly needed for reset)
                value_to_set = defaults.get(key, "")  # Get default value
                set_widget_value(widget, value_to_set)
                reset_count += 1
            else:
                print(f"Warning: Widget for setting '{key}' not found during reset.")

        print(f"Reset {reset_count} settings fields to defaults.")
        messagebox.showinfo("Settings Reset", "Settings fields have been reset to their default values.")

    def _save_settings(self) -> None:
        """Saves current settings from GUI fields to the JSON file."""
        settings_to_save = {}
        settings_file_path = config.SETTINGS_FILENAME

        # --- Collect values from widgets ---
        collected_count = 0
        for key, widget in self.widget_map.items():
            if widget:
                try:
                    value = widget.get().strip()
                    settings_to_save[key] = value
                    collected_count += 1
                except Exception as e:
                    print(f"Error getting value from widget for '{key}': {e}")
            else:
                print(f"Warning: Widget for setting '{key}' not found during save.")

        # --- Write to JSON file ---
        if not settings_to_save:
            print("No settings collected to save.")
            return

        try:
            with open(settings_file_path, 'w', encoding='utf-8') as f:
                json.dump(settings_to_save, f, indent=4, ensure_ascii=False)
            print(f"Saved {collected_count} settings to {settings_file_path}")
        except (IOError, TypeError, Exception) as e:
            print(f"Error saving settings to {settings_file_path}: {e}")
            # Optionally show an error message to the user
            # messagebox.showerror("Settings Error", f"Failed to save settings:\n{e}")

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

            # Use a temporary FFMPEG instance for analysis
            ffmpeg_analyzer = ffmpeg.FFMPEG()

            # Populate track table and get essential parameters/duration
            self.populate_track_table(file_path, ffmpeg_analyzer)
            self.main_video_params = ffmpeg_analyzer.get_essential_stream_params(file_path)

            if not self.main_video_params:
                warning_msg = "Could not retrieve all key parameters from the main video."
                messagebox.showwarning("Parameter Issue", warning_msg)
                self.output_info.insert(tk.END, f"WARNING: {warning_msg}\n")
            else:
                print("Essential video parameters retrieved:", self.main_video_params)
                fps_display = f"{self.main_video_params.fps:.3f}" if self.main_video_params.fps else "N/A"
                # Display basic info in the log
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

                # Check specifically for width and fps, as they're critical
                if self.main_video_params.width is None or self.main_video_params.fps is None:
                    error_msg = "Failed to determine video stream parameters (width/height/fps). Please select a different file."
                    messagebox.showerror("Video Error", error_msg)
                    self.output_info.insert(tk.END, f"ERROR: {error_msg}\n")
                    self._clear_state()
                    return

                # Pre-fill FPS override field with detected value
                if self.main_video_params.fps is not None:
                    # Avoid overwriting if the field already has a *different* value
                    # (which might have been loaded from settings or entered manually)
                    current_fps_entry_val = self.video_fps_entry.get().strip()
                    detected_fps_str = f"{self.main_video_params.fps:.3f}"
                    if detected_fps_str.endswith('.000'):
                        detected_fps_str = detected_fps_str[:-4]

                    # Only update if entry is empty or matches the old default (which is empty)
                    if not current_fps_entry_val:
                        self.video_fps_entry.delete(0, tk.END)
                        self.video_fps_entry.insert(0, detected_fps_str)
                        print(f"Pre-filled FPS override field with detected value: {detected_fps_str}")
                    elif current_fps_entry_val != detected_fps_str:
                        print(
                            f"FPS override field already contains '{current_fps_entry_val}', not overwriting with detected '{detected_fps_str}'.")
                    # else: current value matches detected, no action needed

            if self.main_video_duration is None:
                self.output_info.insert(tk.END, "WARNING: Could not determine main video duration from ffprobe.\n")

            self.output_info.insert(tk.END, "Analysis complete.\n")

        except FfmpegError as e:
            error_msg = f"Failed to analyze input file:\n{e}"
            messagebox.showerror("ffprobe Error", error_msg)
            self.output_info.insert(tk.END, f"FFPROBE ERROR: {error_msg}\n")
            self._clear_state()  # Reset on analysis failure

    # ... (Keep remaining methods like browse_output_file, browse_ad_file, populate_track_table, etc., as they are) ...
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

    def populate_track_table(self, file_path: str, analyzer: ffmpeg.FFMPEG) -> None:
        """Populates the track Treeview using the provided FFMPEG instance."""
        # Clear existing entries
        for item in self.track_tree.get_children():
            self.track_tree.delete(item)
        # Reset state variables related to the previous file
        self.main_video_duration = None  # Reset duration
        self.track_data = {}

        try:
            # Get stream info using the provided analyzer instance
            stream_info = analyzer.get_stream_info(file_path)
            if not stream_info:
                messagebox.showerror("Analysis Error", f"Could not retrieve stream information from:\n{file_path}")
                return

            # Attempt to get duration from format info first
            # We also need the duration, so call get_media_duration as well
            self.main_video_duration = analyzer.get_media_duration(file_path)
            if self.main_video_duration:
                print(f"Main duration from get_media_duration: {self.main_video_duration:.3f}s")

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
                    # Use original r_frame_rate string for display
                    fps_str_display = stream.get('r_frame_rate', '?')  # Frames per second string
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
                else:  # Other stream types (data, attachment)
                    details.append(f"{stream.get('codec_name', '?')}")

                details_str = ", ".join(filter(None, map(str, details)))

                # Insert row into Treeview
                self.track_tree.insert("", tk.END, iid=track_id_str,
                                       values=(track_id_str, track_type, details_str, track_title, track_language))

            # Final warning if duration couldn't be determined by get_media_duration
            if self.main_video_duration is None:
                warning_msg = "Could not determine main video duration from ffprobe analysis."
                self.output_info.insert(tk.END, f"WARNING: {warning_msg}\n")
                print(f"Warning: {warning_msg}")

        except FfmpegError as e:
            # Handle errors during ffprobe execution for stream info
            print(f"ffprobe error during track table population: {e}")
            messagebox.showerror("ffprobe Error", f"Failed to get stream info:\n{e}")
        except Exception as e:
            print(f"Unexpected error during track table population: {e}")
            messagebox.showerror("Error", f"An unexpected error occurred while analyzing tracks:\n{e}")

    def edit_track_data(self, event: tk.Event) -> None:
        """Handles double-click events on the track table to edit Title or Language."""
        item_iid = self.track_tree.identify_row(event.y)  # Get IID of the clicked row
        column_id = self.track_tree.identify_column(event.x)  # Get ID of the clicked column (e.g., #4)

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
            ffmpeg_helper = ffmpeg.FFMPEG()
            embed_duration = ffmpeg_helper.get_media_duration(embed_file)
            if embed_duration is None or embed_duration <= 0.01:
                messagebox.showerror("Ad Duration Error",
                                     f"Could not determine a valid positive duration for the ad file:\n{embed_file}\nEnsure it's a valid video file.")
                return
        except FfmpegError as e:
            messagebox.showerror("ffprobe Error (Ad)", f"Failed to get ad duration:\n{e}")
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

    @staticmethod
    def detect_hwaccels() -> List[str]:
        """Attempts to detect available ffmpeg hardware acceleration methods."""
        try:
            process = subprocess.Popen(["ffmpeg", "-hwaccels", "-hide_banner"],
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', errors='replace')
            output, _ = process.communicate(timeout=5)
            hwaccels = [line.strip() for line in output.splitlines()
                        if line.strip() != "" and "Hardware acceleration methods" not in line]
            hwaccels.extend(["auto", "none"])
            print(f"Detected HW Accels: {hwaccels}")
            return hwaccels
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
        self.cleanup_temp_files()
        self.output_info.delete('1.0', tk.END)
        self.output_info.insert('1.0', "Preparing and generating ffmpeg commands...\n")
        self.master.update_idletasks()

        input_file = self.input_file_entry.get().strip()
        output_file = self.output_file_entry.get().strip()
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
            error_messages.append(
                "- Could not determine a valid duration for the main video. Please re-select the input file.")
        if not self.main_video_params or self.main_video_params.width is None or self.main_video_params.fps is None:
            error_messages.append(
                "- Could not get essential parameters (width/height/fps) from the main video. Please re-select the input file.")

        if banner_file and not os.path.exists(banner_file):
            self.output_info.insert(tk.END, f"WARNING: Banner file '{banner_file}' not found, it will be ignored.\n")
        if moving_file and not os.path.exists(moving_file):
            self.output_info.insert(tk.END,
                                    f"WARNING: Moving logo file '{moving_file}' not found, it will be ignored.\n")
        if banner_file and not self.banner_timecodes:
            self.output_info.insert(tk.END,
                                    "WARNING: Banner file is selected, but no display timecodes are added. Banner will not be shown.\n")

        if error_messages:
            full_error_msg = "Please fix the following errors:\n" + "\n".join(error_messages)
            messagebox.showerror("Validation Error", full_error_msg)
            self.output_info.insert(tk.END, f"VALIDATION ERROR:\n{full_error_msg}\n")
            return None

        try:
            self.ffmpeg_instance = ffmpeg.FFMPEG(
                video_codec=video_codec, video_preset=video_preset, video_cq=video_cq,
                video_bitrate=video_bitrate, audio_codec=audio_codec, audio_bitrate=audio_bitrate,
                video_fps=video_fps, moving_speed=moving_speed,
                moving_logo_relative_height=moving_logo_relative_height,
                moving_logo_alpha=moving_logo_alpha, banner_track_pix_fmt=banner_track_pix_fmt,
                banner_gap_color=banner_gap_color, hwaccel=hwaccel,
                additional_encoding=additional_encoding
            )
        except Exception as e:
            messagebox.showerror("ffmpeg Setup Error", f"Failed to initialize ffmpeg settings: {e}")
            self.output_info.insert(tk.END, f"FFMPEG INIT ERROR:\nFailed to initialize settings: {e}\n")
            return None

        print("Calling generate_ffmpeg_commands with parameters:")
        print(f"  input_file: {input_file}")
        print(f"  output_file: {output_file}")
        print(f"  encoding_params_str (Manual Override): '{encoding_params_str}'")
        print(f"  main_video_params (has float fps): {self.main_video_params}")
        print(f"  main_video_duration: {self.main_video_duration}")
        print(f"  track_data: {self.track_data}")
        print(f"  embed_ads: {self.embed_ads}")
        print(f"  banner_file: {banner_file}")
        print(f"  banner_timecodes: {self.banner_timecodes}")
        print(f"  moving_file: {moving_file}")

        try:
            result = self.ffmpeg_instance.generate_ffmpeg_commands(
                input_file=input_file, output_file=output_file,
                encoding_params_str=encoding_params_str, track_data=self.track_data,
                embed_ads=self.embed_ads, banner_file=banner_file,
                banner_timecodes=self.banner_timecodes, moving_file=moving_file
            )
            self.temp_files_to_clean = result[2] if result and len(result) > 2 else []
            self.output_info.insert(tk.END, "Commands generated successfully.\n")
            print(f"Potential temporary files: {self.temp_files_to_clean}")
            return result

        except (CommandGenerationError, FfmpegError) as e:
            error_msg = f"Command Generation Error:\n{e}"
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
        result = self._prepare_and_generate_commands()

        if not result:
            messagebox.showerror("Cancelled", "Failed to prepare ffmpeg commands. Conversion cancelled.")
            return

        preproc_cmds, main_cmd, _ = result

        num_preproc = len(preproc_cmds) if preproc_cmds else 0
        confirm_message_parts = ["The following steps will be executed:"]
        steps = []
        if num_preproc > 0:
            steps.append(f"Preprocess {num_preproc} segments/ads (creating temporary files).")
        if main_cmd:
            steps.append("Perform main conversion with merging and overlays.")
        else:
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
            self.cleanup_temp_files()
            return

        self.output_info.delete('1.0', tk.END)
        self.output_info.insert('1.0', "Starting conversion process...\n\n")
        self.master.update()

        try:
            start_time_total = time.time()

            if preproc_cmds:
                self.output_info.insert(tk.END, f"--- Stage 1: Preprocessing ({len(preproc_cmds)} commands) ---\n")
                self.master.update()
                start_time_preproc = time.time()
                for i, cmd in enumerate(preproc_cmds):
                    step_name = f"Preprocessing {i + 1}/{len(preproc_cmds)}"
                    self.output_info.insert(tk.END, f"\nRunning: {step_name}...\n")
                    self.output_info.see(tk.END)
                    self.master.update()
                    start_time_step = time.time()
                    ffmpeg.FFMPEG.run_ffmpeg_command(cmd, step_name)
                    end_time_step = time.time()
                    self.output_info.insert(tk.END,
                                            f"Success: {step_name} (took {end_time_step - start_time_step:.2f}s)\n")
                    self.output_info.see(tk.END)
                    self.master.update()
                end_time_preproc = time.time()
                self.output_info.insert(tk.END,
                                        f"\n--- Preprocessing finished (Total time: {end_time_preproc - start_time_preproc:.2f}s) ---\n")

            if main_cmd:
                step_name = "Main Conversion"
                self.output_info.insert(tk.END, f"\n--- Stage 2: {step_name} ---\n")
                self.output_info.see(tk.END)
                self.master.update()
                start_time_main = time.time()
                ffmpeg.FFMPEG.run_ffmpeg_command(main_cmd, step_name)
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
            error_msg = f"\n--- CONVERSION FAILED ---\n{e}\n--- PROCESS HALTED ---"
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
