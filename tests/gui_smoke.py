"""Run on a Windows desktop runner, or Linux under Xvfb."""
import tempfile
import tkinter as tk
from pathlib import Path

from photo_sort_ai.config import Config
from photo_sort_ai.gui import App

with tempfile.TemporaryDirectory() as directory:
    root = tk.Tk()
    cfg = Config(state_dir=str(Path(directory) / "state"))
    app = App(root, cfg, Path(directory) / "config.json")
    root.update()
    assert len(app.tree["columns"]) == 5
    assert len(app.vars) >= 15
    app.close()
    root.after(500, root.quit)
    root.mainloop()
