## Introduction

`JustConverter + AdBurner` is a graphical user interface (GUI) application designed to simplify video conversion using the powerful `ffmpeg` library. Beyond basic transcoding, it specializes in integrating advertisements directly into video files through various methods:

*   **Embedding:** Inserting video ad clips at specific timecodes.
*   **Banner Overlays:** Displaying video or image banners at specified times.
*   **Moving Logos:** Overlaying an image logo that moves around the screen.

The tool provides default settings for common conversion tasks but allows detailed customization of encoding parameters, track metadata, and ad placement.

---

## Features

*   **Video Conversion:** Transcode videos using various codecs (e.g., H.264, HEVC) and settings.
*   **Ad Embedding:** Insert video advertisements seamlessly at multiple points in the main video.
*   **Banner Ad Overlays:** Add static or animated banners (from video or image files) visible during specific time intervals.
*   **Moving Logo:** Overlay a customizable moving logo (image) for branding or watermarking.
*   **Track Management:** View video, audio, and subtitle tracks. Edit Title and Language metadata for tracks.
*   **Parameter Control:** Adjust video/audio codecs, bitrates, quality (CQ/CRF), presets, FPS, and hardware acceleration.
*   **Hardware Acceleration:** Detects and utilizes available ffmpeg hardware acceleration methods (e.g., NVENC, QSV, VAAPI) for faster encoding (if supported by hardware and ffmpeg build).
*   **Command Preview:** View the generated `ffmpeg` commands before starting the conversion.
*   **Logging:** Displays `ffmpeg` output and progress during conversion.
*   **Cross-Platform:** Built with Python and Tkinter, aiming for compatibility with Linux and Windows.
*   **Temporary File Management:** Automatically generates and cleans up temporary files used during processing.

---

## Wiki

You can read how to install and use this program on the [wiki page](https://github.com/DIMNISSV/JustConverter/wiki).

---
