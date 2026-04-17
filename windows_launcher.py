"""Windows tray launcher for project-backed BIN Viewer hosting."""
from __future__ import annotations

from pathlib import Path
import logging
import subprocess
import sys
import random
import socket
import threading
import webbrowser

from PIL import Image, ImageDraw
import pystray
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from werkzeug.serving import make_server

from app import app, configure_lan_access
from project_store import DEFAULT_SAVE_TEMPLATE, ProjectStore


DEFAULT_PORT = 5000
LOG_PATH = Path.home() / "AppData" / "Roaming" / "BinViewer" / "launcher.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


class ServerController:
    """Owns the embedded Flask server used by the tray app."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.host = "127.0.0.1"
        self.public_url_mode = "local"
        self.pin = ""
        self._server = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def lan_enabled(self) -> bool:
        return self.running and self.host == "0.0.0.0"

    def start(self, *, lan: bool = False, tailscale: bool = False) -> str:
        if self.running:
            self.stop()
        remote = lan or tailscale
        self.host = "0.0.0.0" if remote else "127.0.0.1"
        self.public_url_mode = "tailscale" if tailscale else ("lan" if lan else "local")
        self.pin = f"{random.randint(0, 999999):06d}" if remote else ""
        configure_lan_access(enabled=remote, pin=self.pin)
        self._server = make_server(self.host, self.port, app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        configure_lan_access(enabled=False)
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        self._thread = None
        self.pin = ""
        self.host = "127.0.0.1"
        self.public_url_mode = "local"

    @property
    def url(self) -> str:
        if self.public_url_mode == "tailscale":
            return f"http://{get_tailscale_ip()}:{self.port}"
        if self.public_url_mode == "lan":
            return f"http://{get_lan_ip()}:{self.port}"
        return f"http://127.0.0.1:{self.port}"


class LauncherApp:
    def __init__(self) -> None:
        self.server = ServerController()
        self.store = ProjectStore()
        self.icon = pystray.Icon(
            "bin-viewer",
            create_icon_image(),
            "BIN Viewer",
            self._menu(),
        )

    def run(self) -> None:
        logging.info("Starting BIN Viewer tray launcher")
        self.icon.run()

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("Start Local Server", self.start_local, enabled=lambda _: not self.server.running),
            pystray.MenuItem("Host on LAN", self.start_lan, enabled=lambda _: not self.server.running),
            pystray.MenuItem("Host on Tailscale", self.start_tailscale, enabled=lambda _: not self.server.running),
            pystray.MenuItem("Stop Server", self.stop_server, enabled=lambda _: self.server.running),
            pystray.MenuItem("Open Workspace", self.open_workspace, enabled=lambda _: self.server.running),
            pystray.MenuItem("Copy Server URL", self.copy_url, enabled=lambda _: self.server.running),
            pystray.MenuItem("Projects", self.open_projects),
            pystray.MenuItem("Quit", self.quit),
        )

    def start_local(self, *_args) -> None:
        try:
            url = self.server.start(lan=False)
            self.icon.title = "BIN Viewer - local"
            logging.info("Started local server at %s", url)
            webbrowser.open(url)
        except OSError as exc:
            logging.exception("Local server failed")
            show_message("Server failed", str(exc), error=True)

    def start_lan(self, *_args) -> None:
        try:
            url = self.server.start(lan=True)
            self.icon.title = f"BIN Viewer - LAN PIN {self.server.pin}"
            copy_to_clipboard(url)
            logging.info("Started LAN server at %s with PIN %s", url, self.server.pin)
            self.icon.notify(f"Open {url} on another device. PIN: {self.server.pin}", "LAN hosting started")
            webbrowser.open(url)
        except OSError as exc:
            logging.exception("LAN server failed")
            show_message("Server failed", str(exc), error=True)

    def start_tailscale(self, *_args) -> None:
        try:
            url = self.server.start(tailscale=True)
            self.icon.title = f"BIN Viewer - Tailscale PIN {self.server.pin}"
            copy_to_clipboard(url)
            logging.info("Started Tailscale server at %s with PIN %s", url, self.server.pin)
            self.icon.notify(f"Open {url} from your laptop. PIN: {self.server.pin}", "Tailscale hosting started")
            webbrowser.open(url)
        except OSError as exc:
            logging.exception("Tailscale server failed")
            show_message("Server failed", str(exc), error=True)

    def stop_server(self, *_args) -> None:
        self.server.stop()
        self.icon.title = "BIN Viewer"
        logging.info("Stopped server")

    def open_workspace(self, *_args) -> None:
        if self.server.running:
            webbrowser.open(self.server.url)

    def copy_url(self, *_args) -> None:
        if self.server.running:
            copy_to_clipboard(self.server.url)

    def open_projects(self, *_args) -> None:
        try:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--projects"], close_fds=True)
            logging.info("Opened project config dialog")
        except OSError as exc:
            logging.exception("Project dialog failed to start")
            show_message("Projects failed", str(exc), error=True)

    def quit(self, *_args) -> None:
        self.stop_server()
        self.icon.stop()


class ProjectDialog:
    """Native project editor for desktop-only paths and save settings."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self.root = tk.Tk()
        self.root.title("BIN Viewer Projects")
        self.root.geometry("700x430")
        self.root.resizable(False, False)
        self.store_data = self.store.load()
        self.projects = self.store_data["projects"]
        self.active_project_id = self.store_data.get("active_project_id")
        self.selected_project = tk.StringVar(value=self._label_for_id(self.active_project_id))
        self.vars = {
            "name": tk.StringVar(),
            "xdf_path": tk.StringVar(),
            "bin_folder": tk.StringVar(),
            "last_bin_path": tk.StringVar(),
            "output_folder": tk.StringVar(),
            "log_folder": tk.StringVar(),
            "draft_path": tk.StringVar(),
            "save_template": tk.StringVar(value=DEFAULT_SAVE_TEMPLATE),
            "description": tk.StringVar(),
            "remote_save_mode": tk.StringVar(value="new_revision"),
        }

    def show(self) -> None:
        self._build()
        self._load_selected()
        self.root.mainloop()

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Project").grid(row=0, column=0, sticky="w")
        self.project_combo = ttk.Combobox(
            frame,
            state="readonly",
            textvariable=self.selected_project,
            values=[self._project_label(project) for project in self.projects],
            width=52,
        )
        self.project_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_selected())
        ttk.Button(frame, text="New", command=self._new_project).grid(row=0, column=2, sticky="ew")

        row = 1
        row = self._path_row(frame, row, "Name", "name")
        row = self._path_row(frame, row, "XDF path", "xdf_path", self._browse_xdf)
        row = self._path_row(frame, row, "BIN folder", "bin_folder", self._browse_bin_folder)
        row = self._path_row(frame, row, "Last BIN", "last_bin_path", self._browse_last_bin)
        row = self._path_row(frame, row, "Output folder", "output_folder", self._browse_output_folder)
        row = self._path_row(frame, row, "Change log folder", "log_folder", self._browse_log_folder)
        row = self._path_row(frame, row, "Draft path", "draft_path", self._browse_draft_path)
        row = self._path_row(frame, row, "Save template", "save_template")
        row = self._path_row(frame, row, "Description", "description")

        ttk.Label(frame, text="Remote save mode").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Combobox(
            frame,
            state="readonly",
            textvariable=self.vars["remote_save_mode"],
            values=["new_revision", "small_edit"],
            width=30,
        ).grid(row=row, column=1, sticky="w", padx=8)
        row += 1

        hint = "Template fields: {stem}, {source_stem}, {token}, {n}, {date}, {time}"
        ttk.Label(frame, text=hint).grid(row=row, column=1, sticky="w", padx=8, pady=(2, 10))
        row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=10)
        ttk.Button(buttons, text="Save Active Project", command=self._save).pack(side="right", padx=4)
        ttk.Button(buttons, text="Close", command=self.root.destroy).pack(side="right", padx=4)

        frame.columnconfigure(1, weight=1)

    def _path_row(self, frame, row: int, label: str, key: str, browse_command=None) -> int:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.vars[key], width=72).grid(row=row, column=1, sticky="ew", padx=8)
        if browse_command:
            ttk.Button(frame, text="Browse", command=browse_command).grid(row=row, column=2, sticky="ew")
        return row + 1

    def _load_selected(self) -> None:
        project = self._current_project()
        if not project:
            self._new_project()
            return
        for key, value in self.vars.items():
            value.set(project.get(key, DEFAULT_SAVE_TEMPLATE if key == "save_template" else ""))

    def _new_project(self) -> None:
        self.selected_project.set("")
        for value in self.vars.values():
            value.set("")
        self.vars["save_template"].set(DEFAULT_SAVE_TEMPLATE)
        self.vars["description"].set("")
        self.vars["remote_save_mode"].set("new_revision")
        self.vars["name"].set("New Project")

    def _current_project(self) -> dict | None:
        label = self.selected_project.get()
        for project in self.projects:
            if self._project_label(project) == label or project["id"] == label:
                return project
        return None

    def _save(self) -> None:
        existing = self._current_project() or {}
        payload = {key: value.get().strip() for key, value in self.vars.items()}
        payload["id"] = existing.get("id", "")
        project = self.store.upsert_project(payload, make_active=True)
        self.store_data = self.store.load()
        self.projects = self.store_data["projects"]
        self.active_project_id = self.store_data.get("active_project_id")
        self.selected_project.set(self._project_label(project))
        self.project_combo["values"] = [self._project_label(item) for item in self.projects]
        show_message("Project saved", f"{project['name']} is now the active project.")

    def _project_label(self, project: dict) -> str:
        marker = "active" if project.get("id") == self.active_project_id else project.get("id", "")[:6]
        return f"{project.get('name', 'Project')} [{marker}]"

    def _label_for_id(self, project_id: str | None) -> str:
        for project in self.projects:
            if project.get("id") == project_id:
                return self._project_label(project)
        return ""

    def _browse_xdf(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("XDF files", "*.xdf *.xml"), ("All files", "*.*")])
        if path:
            self.vars["xdf_path"].set(path)

    def _browse_bin_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.vars["bin_folder"].set(path)

    def _browse_last_bin(self) -> None:
        initial = self.vars["bin_folder"].get() or None
        path = filedialog.askopenfilename(initialdir=initial, filetypes=[("BIN files", "*.bin *.hex"), ("All files", "*.*")])
        if path:
            self.vars["last_bin_path"].set(path)
            self.vars["bin_folder"].set(str(Path(path).parent))

    def _browse_output_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.vars["output_folder"].set(path)

    def _browse_log_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.vars["log_folder"].set(path)

    def _browse_draft_path(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if path:
            self.vars["draft_path"].set(path)


def create_icon_image() -> Image.Image:
    image = Image.new("RGB", (64, 64), "#1f2933")
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 18, 52, 46), fill="#f2f7f8")
    draw.line((16, 36, 24, 36, 29, 24, 36, 50, 41, 36, 48, 36), fill="#0f766e", width=4)
    return image


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def get_tailscale_ip() -> str:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            value = line.strip()
            if value:
                return value
    except (OSError, subprocess.SubprocessError):
        logging.exception("Could not detect Tailscale IP")
    return get_lan_ip()


def show_message(title: str, message: str, *, error: bool = False) -> None:
    root = tk.Tk()
    root.withdraw()
    if error:
        messagebox.showerror(title, message)
    else:
        messagebox.showinfo(title, message)
    root.destroy()


def copy_to_clipboard(text: str) -> None:
    root = tk.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    root.destroy()


if __name__ == "__main__":
    if "--projects" in sys.argv:
        ProjectDialog(ProjectStore()).show()
    elif "--tailscale" in sys.argv:
        controller = ServerController()
        url = controller.start(tailscale=True)
        copy_to_clipboard(url)
        print(f"Tailscale server: {url}")
        print(f"PIN: {controller.pin}")
        print("Press Ctrl+C to stop.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            controller.stop()
    else:
        LauncherApp().run()
