"""Tkinter GUI for mrc2png.

All slow work (header scanning, conversion) runs in a single background
thread. The thread never touches Tk: it posts messages to a queue that the
main thread polls with ``root.after``.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
from tkinter.scrolledtext import ScrolledText

from . import __version__, core
from .core import Status

APP_NAME = "mrc2png"
POLL_MS = 100

# Foreground colours readable on both light and dark backgrounds.
COLORS = {"ok": "", "warn": "#c77700", "error": "#d03b2f", "done": "#2e9e4f", "muted": "#888888"}

try:  # optional drag-and-drop; enabled only if it is installed and loads
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
except Exception:  # noqa: BLE001
    TkinterDnD = None
    DND_FILES = None


# --------------------------------------------------------------------------
# Settings (remember folders between sessions)
# --------------------------------------------------------------------------
def _settings_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_settings(settings: dict) -> None:
    try:
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(settings, indent=2))
    except Exception:  # noqa: BLE001 - settings are a convenience only
        pass


def open_folder(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
@dataclass
class Row:
    info: core.FileInfo
    override_nm: float | None = None
    status: str = Status.OK        # current classification (depends on target)
    message: str = ""
    result: core.ConvertResult | None = None

    @property
    def px_size_nm(self) -> float | None:
        return self.override_nm if self.override_nm is not None else self.info.px_size_nm

    def classify(self, target_nm: float) -> None:
        if self.info.status in (Status.STACK, Status.UNREADABLE):
            self.status, self.message = self.info.status, self.info.message
        elif self.px_size_nm is None:
            self.status = Status.MISSING_PX
            self.message = "Pixel size missing from the file header. Double-click to enter it."
        else:
            self.status, self.message = core.classify_scale(self.px_size_nm, target_nm, self.info.dtype)

    @property
    def convertible(self) -> bool:
        return self.status in (Status.OK, Status.COARSER)


def status_text(row: Row) -> tuple[str, str]:
    """(text, colour key) for the Status column."""
    r = row.result
    if r is not None:
        return {
            "converted": ("Converted", "done"),
            "not_upsampled": ("Converted at native scale (not upsampled)", "done"),
            "skipped": ("Skipped: PNG already exists", "muted"),
            "cancelled": ("Cancelled", "muted"),
        }.get(r.status, (f"Failed: {r.message}", "error"))
    return {
        Status.OK: ("Ready", "ok"),
        Status.COARSER: ("Ready, will not be upsampled", "warn"),
        Status.MISSING_PX: ("Needs pixel size (double-click)", "warn"),
        Status.STACK: ("Not supported: multiple sections", "error"),
        Status.UNSUPPORTED: ("Not supported: image data type", "error"),
        Status.UNREADABLE: ("Cannot read file", "error"),
    }.get(row.status, (row.status, "ok"))


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.rows: dict[str, Row] = {}            # iid (resolved path) -> Row
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.worker_kind: str | None = None       # "scan" or "convert"
        self.cancel_event = threading.Event()
        self.settings = load_settings()
        self.last_output: Path | None = None

        out = self.settings.get("output_dir", "")
        self.output_var = tk.StringVar(value=out if out and Path(out).is_dir() else "")
        self.target_var = tk.StringVar(value=f"{core.DEFAULT_TARGET_NM:g}")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.progress_var = tk.StringVar(value="")
        self.detail_var = tk.StringVar(value="")
        self.target_nm = core.DEFAULT_TARGET_NM

        # tkdnd is usable only if the root was created by TkinterDnD and loaded it.
        self.dnd = TkinterDnD is not None and getattr(root, "TkdndVersion", None) is not None
        self.bold = tkfont.nametofont("TkDefaultFont").copy()
        self.bold.configure(weight="bold")
        # Windows: pixel sizes below are designed for 96 DPI; scale them on
        # high-DPI screens (125 %, 150 %…). macOS already handles this.
        self.scale = 1.0
        if sys.platform == "win32":
            self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
            linespace = tkfont.nametofont("TkDefaultFont").metrics("linespace")
            ttk.Style(root).configure("Treeview", rowheight=int(linespace + 6 * self.scale))
        root.title(f"{APP_NAME} {__version__}")
        root.minsize(self.px(760), self.px(560))
        self._build()
        self._refresh_controls()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(POLL_MS, self._poll)

    def px(self, n: int) -> int:
        """Scale a 96-DPI pixel size for the current screen (Windows only)."""
        return int(round(n * self.scale))

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        # 1. Inputs
        top = ttk.Frame(main)
        top.pack(fill="x")
        ttk.Label(top, text="1. Add MRC files", font=self.bold).pack(side="left")
        self.btn_add_files = ttk.Button(top, text="Add files…", command=self.ask_files)
        self.btn_add_folder = ttk.Button(top, text="Add folder…", command=self.ask_folder)
        self.btn_remove = ttk.Button(top, text="Remove selected", command=self.remove_selected)
        self.btn_clear = ttk.Button(top, text="Clear list", command=self.clear)
        for b in (self.btn_add_files, self.btn_add_folder, self.btn_remove, self.btn_clear):
            b.pack(side="left", padx=(8, 0))
        if self.dnd:
            ttk.Label(top, text="or drop files/folders below", foreground=COLORS["muted"]).pack(
                side="left", padx=8)

        table = ttk.Frame(main)
        table.pack(fill="both", expand=True, pady=(6, 0))
        cols = ("name", "size", "px", "status")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", selectmode="extended", height=10)
        for c, label, w, anchor, stretch in (
            ("name", "File", 260, "w", True),
            ("size", "Size (px)", 110, "e", False),
            ("px", "Pixel size (nm/px)", 130, "e", False),
            ("status", "Status", 260, "w", True),
        ):
            self.tree.heading(c, text=label, anchor=anchor)
            self.tree.column(c, width=self.px(w), anchor=anchor, stretch=stretch)
        for key, color in COLORS.items():
            if color:
                self.tree.tag_configure(key, foreground=color)
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<BackSpace>", lambda e: self.remove_selected())
        if self.dnd:
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind("<<Drop>>", self._on_drop)

        ttk.Label(main, textvariable=self.detail_var, foreground=COLORS["muted"],
                  wraplength=self.px(720), justify="left").pack(fill="x", pady=(2, 6))

        # 2. Settings
        opts = ttk.Frame(main)
        opts.pack(fill="x")
        opts.columnconfigure(1, weight=1)
        ttk.Label(opts, text="2. Output folder", font=self.bold).grid(row=0, column=0, sticky="w")
        out_entry = ttk.Entry(opts, textvariable=self.output_var, state="readonly")
        out_entry.grid(row=0, column=1, sticky="ew", **pad)
        self.btn_output = ttk.Button(opts, text="Choose…", command=self.ask_output)
        self.btn_output.grid(row=0, column=2, sticky="e")

        ttk.Label(opts, text="Target pixel size").grid(row=1, column=0, sticky="w")
        tf = ttk.Frame(opts)
        tf.grid(row=1, column=1, columnspan=2, sticky="w", **pad)
        self.target_entry = ttk.Entry(tf, textvariable=self.target_var, width=8, justify="right")
        self.target_entry.pack(side="left")
        ttk.Label(tf, text="nm/px").pack(side="left", padx=(4, 16))
        self.target_entry.bind("<Return>", lambda e: self._apply_target())
        self.target_entry.bind("<FocusOut>", lambda e: self._apply_target())
        self.chk_overwrite = ttk.Checkbutton(tf, text="Overwrite existing PNGs", variable=self.overwrite_var)
        self.chk_overwrite.pack(side="left")

        # 3. Run
        run = ttk.Frame(main)
        run.pack(fill="x", pady=(8, 0))
        ttk.Label(run, text="3.", font=self.bold).pack(side="left")
        self.btn_convert = ttk.Button(run, text="Convert", command=self.start_convert)
        self.btn_cancel = ttk.Button(run, text="Cancel", command=self.cancel)
        self.btn_open = ttk.Button(run, text="Open output folder", command=self._open_output)
        self.btn_convert.pack(side="left", padx=(8, 0))
        self.btn_cancel.pack(side="left", padx=(8, 0))
        self.btn_open.pack(side="right")
        self.progress = ttk.Progressbar(run, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(main, textvariable=self.progress_var).pack(fill="x", pady=(4, 0))

        # Log
        self.log_box = ScrolledText(main, height=8, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=False, pady=(6, 0))

    # ------------------------------------------------------------- helpers
    def log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _output_dir(self) -> Path | None:
        v = self.output_var.get().strip()
        return Path(v) if v else None

    def _refresh_row(self, iid: str) -> None:
        row = self.rows[iid]
        text, color = status_text(row)
        px = f"{row.px_size_nm:.4g}" if row.px_size_nm else "?"
        if row.override_nm is not None:
            px += " (entered)"
        size = f"{row.info.nx} × {row.info.ny}" if row.info.nx else ""
        if row.info.nz > 1:
            size += f" × {row.info.nz}"
        values = (row.info.path.name, size, px, text)
        if self.tree.exists(iid):
            self.tree.item(iid, values=values, tags=(color,))
        else:
            self.tree.insert("", "end", iid=iid, values=values, tags=(color,))

    def _refresh_controls(self) -> None:
        busy = self.busy
        converting = busy and self.worker_kind == "convert"
        ready = [r for r in self.rows.values() if r.convertible]
        can_convert = not busy and bool(ready) and self._output_dir() is not None
        st = lambda ok: "normal" if ok else "disabled"  # noqa: E731
        for b in (self.btn_add_files, self.btn_add_folder, self.btn_remove, self.btn_clear, self.btn_output):
            b.configure(state=st(not busy))
        self.target_entry.configure(state=st(not busy))
        self.chk_overwrite.configure(state=st(not busy))
        self.btn_convert.configure(state=st(can_convert))
        self.btn_cancel.configure(state=st(converting and not self.cancel_event.is_set()))
        self.btn_open.configure(state=st(self._output_dir() is not None and self._output_dir().is_dir()))
        if not busy:
            if not self.rows:
                self.progress_var.set("Add MRC files or a folder to start.")
            elif not ready:
                self.progress_var.set("No file is ready to convert.")
            elif self._output_dir() is None:
                self.progress_var.set("Choose an output folder.")
            else:
                n_attention = len(self.rows) - len(ready)
                msg = f"{len(ready)} file(s) ready to convert."
                if n_attention:
                    msg += f" {n_attention} file(s) need attention and will not be converted."
                self.progress_var.set(msg)

    # -------------------------------------------------------------- inputs
    def ask_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="Add MRC files", initialdir=self.settings.get("input_dir") or None,
            filetypes=[("MRC files", "*.mrc *.mrcs *.rec *.MRC"), ("All files", "*")],
        )
        if files:
            self.settings["input_dir"] = str(Path(files[0]).parent)
            self.add_paths([Path(f) for f in files])

    def ask_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Add a folder (searched recursively)", initialdir=self.settings.get("input_dir") or None,
            mustexist=True,
        )
        if folder:
            self.settings["input_dir"] = folder
            self.add_paths([Path(folder)])

    def _on_drop(self, event) -> None:
        if not self.busy:
            self.add_paths([Path(p) for p in self.root.tk.splitlist(event.data)])

    def add_paths(self, paths: list[Path]) -> None:
        """Scan headers in the background, then add rows."""
        if self.busy:
            return
        known = set(self.rows)
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        self.progress_var.set("Reading file headers…")

        def work():
            files = core.find_mrc_files(paths)
            new = [f for f in files if str(f) not in known]
            if not new:
                self.events.put(("log", "No new .mrc/.mrcs/.rec files found."))
            for f in new:
                self.events.put(("scanned", core.read_info(f, self.target_nm)))

        self._start_worker("scan", work)

    def remove_selected(self) -> None:
        if self.busy:
            return
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.rows.pop(iid, None)
        self.detail_var.set("")
        self._refresh_controls()

    def clear(self) -> None:
        if self.busy:
            return
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.detail_var.set("")
        self._refresh_controls()

    def _on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if len(sel) != 1:
            self.detail_var.set("")
            return
        row = self.rows[sel[0]]
        parts = [str(row.info.path)]
        msg = row.result.message if row.result and row.result.message else row.message
        if msg:
            parts.append(msg)
        parts += row.info.warnings
        if row.result and row.result.output and row.result.status in ("converted", "not_upsampled"):
            parts.append(f"→ {row.result.output.name} "
                         f"({row.result.out_shape[1]} × {row.result.out_shape[0]} px)")
        self.detail_var.set("\n".join(parts))

    def _on_double_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid or self.busy:
            return
        row = self.rows[iid]
        if row.info.status in (Status.STACK, Status.UNREADABLE):
            return
        if row.info.px_size_nm is not None:
            messagebox.showinfo(
                APP_NAME, f"The pixel size of {row.info.path.name} comes from the file header "
                          f"({row.info.px_size_nm:.4g} nm/px) and cannot be changed.", parent=self.root)
            return
        self.ask_pixel_size(iid)

    def ask_pixel_size(self, iid: str) -> None:
        row = self.rows[iid]
        value = simpledialog.askfloat(
            "Enter pixel size",
            f"{row.info.path.name} has no pixel size in its header.\n\n"
            "Enter its pixel size in nm/px (e.g. 1.412):",
            parent=self.root, minvalue=1e-6, maxvalue=1e6,
            initialvalue=row.override_nm,
        )
        if value is None:
            return
        row.override_nm = value
        row.result = None
        row.classify(self.target_nm)
        self._refresh_row(iid)
        self._on_select()
        self.log(f"{row.info.path.name}: pixel size set to {value:g} nm/px.")
        self._refresh_controls()

    # ------------------------------------------------------------ settings
    def ask_output(self) -> None:
        folder = filedialog.askdirectory(
            title="Choose the output folder", initialdir=self.output_var.get() or None, mustexist=False)
        if folder:
            self.set_output(Path(folder))

    def set_output(self, folder: Path) -> None:
        self.output_var.set(str(folder))
        self.settings["output_dir"] = str(folder)
        save_settings(self.settings)
        self._refresh_controls()

    def _apply_target(self) -> bool:
        try:
            value = float(self.target_var.get().replace(",", "."))
            if not (0 < value < 1e6):
                raise ValueError
        except ValueError:
            self.target_var.set(f"{self.target_nm:g}")
            messagebox.showerror(APP_NAME, "The target pixel size must be a positive number (nm/px).",
                                 parent=self.root)
            return False
        if value != self.target_nm:
            self.target_nm = value
            for iid, row in self.rows.items():
                row.classify(value)
                self._refresh_row(iid)
            self.log(f"Target pixel size set to {value:g} nm/px.")
            self._refresh_controls()
        return True

    # ----------------------------------------------------------- conversion
    def start_convert(self) -> None:
        if self.busy or not self._apply_target():
            return
        out_dir = self._output_dir()
        if out_dir is None:
            return
        todo = [iid for iid in self.tree.get_children() if self.rows[iid].convertible]
        if not todo:
            return
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Cannot create the output folder:\n{e}", parent=self.root)
            return

        for iid in todo:
            self.rows[iid].result = None
            self._refresh_row(iid)
        paths = [self.rows[iid].info.path for iid in todo]
        overrides = {self.rows[iid].info.path: self.rows[iid].override_nm
                     for iid in todo if self.rows[iid].override_nm is not None}
        target, overwrite = self.target_nm, self.overwrite_var.get()
        self.cancel_event.clear()
        self.last_output = out_dir
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=len(paths), value=0)
        self.log(f"Converting {len(paths)} file(s) to {out_dir} at {target:g} nm/px.")

        def progress_cb(i, total, path, result):
            self.events.put(("progress", i, total, path, result))

        def work():
            results = core.convert_batch(
                paths, out_dir, target_nm=target, px_size_overrides_nm=overrides,
                overwrite=overwrite, progress_cb=progress_cb, cancel_event=self.cancel_event)
            self.events.put(("finished", results))

        self._start_worker("convert", work)

    def cancel(self) -> None:
        if self.busy and self.worker_kind == "convert":
            self.cancel_event.set()
            self.progress_var.set("Cancelling… the file in progress will finish first.")
            self.log("Cancel requested; stopping after the current file.")
            self._refresh_controls()

    def _open_output(self) -> None:
        d = self._output_dir()
        if d and d.is_dir():
            open_folder(d)

    # --------------------------------------------------------------- worker
    def _start_worker(self, kind: str, fn) -> None:
        def run():
            try:
                fn()
            except Exception:  # noqa: BLE001
                self.events.put(("crash", traceback.format_exc()))
            finally:
                self.events.put(("worker_done", kind))

        self.worker_kind = kind
        self.worker = threading.Thread(target=run, name=f"mrc2png-{kind}", daemon=True)
        self.worker.start()
        self._refresh_controls()

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _handle(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "log":
            self.log(ev[1])
        elif kind == "scanned":
            info: core.FileInfo = ev[1]
            iid = str(info.path)
            if iid not in self.rows:
                row = Row(info)
                row.classify(self.target_nm)
                self.rows[iid] = row
                self._refresh_row(iid)
                if not row.convertible or row.status == Status.COARSER:
                    self.log(f"{info.path.name}: {row.message}")
        elif kind == "progress":
            _, i, total, path, result = ev
            iid = str(path)
            if result is None:
                self.progress_var.set(f"Converting {i + 1} of {total}: {path.name}")
                if iid in self.rows:
                    self.tree.see(iid)
            else:
                self.progress.configure(value=i + 1)
                if iid in self.rows:
                    self.rows[iid].result = result
                    self._refresh_row(iid)
                text, _ = status_text(self.rows[iid]) if iid in self.rows else (result.status, "")
                self.log(f"{path.name}: {text}" + (f" ({result.message})" if result.message
                                                    and result.status == "not_upsampled" else ""))
        elif kind == "finished":
            self._show_summary(ev[1])
        elif kind == "crash":
            self.log("Unexpected error:\n" + ev[1])
            messagebox.showerror(APP_NAME, "An unexpected error occurred. See the log for details.",
                                 parent=self.root)
        elif kind == "worker_done":
            self.worker = None
            if ev[1] == "scan":
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
            save_settings(self.settings)
            self._refresh_controls()

    def _show_summary(self, results: list[core.ConvertResult]) -> None:
        counts: dict[str, int] = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        not_ready = sum(1 for r in self.rows.values() if not r.convertible)
        lines = [f"Converted: {counts.get('converted', 0) + counts.get('not_upsampled', 0)}"]
        if counts.get("not_upsampled"):
            lines.append(f"   of which at native scale (not upsampled): {counts['not_upsampled']}")
        if counts.get("skipped"):
            lines.append(f"Skipped (PNG already exists): {counts['skipped']}")
        if counts.get("failed"):
            lines.append(f"Failed: {counts['failed']}")
        if counts.get("cancelled"):
            lines.append(f"Cancelled: {counts['cancelled']}")
        if not_ready:
            lines.append(f"Not converted (need attention): {not_ready}")
        summary = "\n".join(lines)
        self.log("Done. " + summary.replace("\n", "; ").replace(";    ", " "))
        title = "Conversion cancelled" if counts.get("cancelled") else "Conversion finished"
        self.progress_var.set(title + ".")
        if messagebox.askyesno(APP_NAME, f"{title}.\n\n{summary}\n\nOpen the output folder?",
                               parent=self.root):
            self._open_output()

    # ---------------------------------------------------------------- close
    def _on_close(self) -> None:
        if self.busy and self.worker_kind == "convert":
            if not messagebox.askyesno(
                APP_NAME, "A conversion is running. Stop it and quit?\n\n"
                          "The file currently being converted will not be saved.", parent=self.root):
                return
            self.cancel_event.set()
        save_settings(self.settings)
        self.root.destroy()


def create_root() -> tk.Tk:
    if TkinterDnD is not None:
        try:
            return TkinterDnD.Tk()
        except Exception:  # noqa: BLE001 - fall back to plain Tk if tkdnd fails to load
            pass
    return tk.Tk()


def _enable_windows_dpi_awareness() -> None:
    """Without this, Windows bitmap-stretches the window on scaled displays (blurry text)."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system DPI aware
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _set_windows_icon(root: tk.Tk) -> None:
    """Replace Tk's default feather icon in the title bar and taskbar (Windows)."""
    if sys.platform != "win32":
        return
    # Packaged: files are unpacked under sys._MEIPASS. From source: repo root.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    ico = base / "assets" / "icon.ico"
    if ico.exists():
        try:
            root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass


def main() -> None:
    _enable_windows_dpi_awareness()
    root = create_root()
    _set_windows_icon(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
