# main.py
import subprocess
import sys
import tkinter as tk

from converter.gui import VideoConverterGUI

# Optional: Add project root to sys.path if running from a different directory
# current_dir = os.path.dirname(os.path.abspath(__file__))
# project_root = os.path.dirname(current_dir) # Go up one level if main.py is inside converter/
# sys.path.insert(0, project_root)
# from converter.gui import VideoConverterGUI


if __name__ == "__main__":
    # Basic check for ffmpeg/ffprobe before starting GUI
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, text=True)
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True, text=True)
        print("ffmpeg и ffprobe найдены.")
    except FileNotFoundError:
        print("ОШИБКА: ffmpeg или ffprobe не найдены в системном PATH.")
        print("Пожалуйста, установите ffmpeg и убедитесь, что он доступен в PATH перед запуском.")
        # Optionally show a simple Tk message box here too
        root = tk.Tk()
        root.withdraw()  # Hide the main window
        tk.messagebox.showerror("Ошибка запуска",
                                "ffmpeg или ffprobe не найдены.\nУстановите ffmpeg и добавьте его в PATH.")
        sys.exit(1)
    except Exception as e:
        print(f"Ошибка при проверке ffmpeg/ffprobe: {e}")
        # Decide if you want to proceed or exit

    # Start the GUI
    root = tk.Tk()
    app = VideoConverterGUI(root)
    root.mainloop()
