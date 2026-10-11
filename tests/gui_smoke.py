"""Run on a Windows desktop runner, or Linux under Xvfb."""
import json
import os
import tempfile
import tkinter as tk
from pathlib import Path

from photo_sort_ai.config import Config, save_config
from photo_sort_ai.gui import PRESET_LABELS, App

with tempfile.TemporaryDirectory() as directory:
    root = tk.Tk()
    cfg = Config(state_dir=str(Path(directory) / "state"))
    app = App(root, cfg, Path(directory) / "config.json")
    root.update()
    assert len(app.tree["columns"]) == 5
    assert len(app.vars) >= 15
    app.vars["preset"].set(PRESET_LABELS["openai"])
    app.vars["openai_model"].set("test-vision")
    app.vars["openai_api_key_env"].set("PHOTO_SORT_GUI_TEST_KEY")
    app.api_key.set("test-session-secret")
    cfg = app.collect(require_folders=False)
    assert cfg.backend == "openai" and cfg.openai_model == "test-vision"
    assert os.environ["PHOTO_SORT_GUI_TEST_KEY"] == "test-session-secret"
    save_config(cfg, Path(directory) / "config.json")
    saved = (Path(directory) / "config.json").read_text(encoding="utf-8")
    assert "test-session-secret" not in saved
    assert json.loads(saved)["openai_api_mode"] == "chat_completions"
    os.environ.pop("PHOTO_SORT_GUI_TEST_KEY", None)
    app.close()
    root.after(500, root.quit)
    root.mainloop()
