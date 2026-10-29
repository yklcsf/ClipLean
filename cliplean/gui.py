"""Sürükle-bırak penceresi."""
from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, update
from .core import (APP_NAME, CREATE_NO_WINDOW, VIDEO_EXTS, Job, Processor, check_ffmpeg,
                   find_ffmpeg, human_size, keep_awake, log_dir, plan)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # sürükle-bırak olmadan da çalışsın
    TkinterDnD = None

# Logo renkleri: lacivert ve turkuaz.
NAVY, TEAL, TEAL_DARK = "#16365f", "#00BFAF", "#00796f"
ZONE_BG, ZONE_BG_ACTIVE = "#f1fbf9", "#dcf6f1"
ZONE_BORDER, ZONE_BORDER_ACTIVE = "#a9e3d9", TEAL

CSIDL_SENDTO, CSIDL_DESKTOPDIRECTORY = 0x09, 0x10


# --- Kısayollar -----------------------------------------------------------

def _special_folder(csidl: int) -> Path | None:
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) != 0:
        return None
    return Path(buf.value)


def _shortcut_path(csidl: int) -> Path | None:
    folder = _special_folder(csidl)
    return folder / f"{APP_NAME}.lnk" if folder else None


def _write_shortcut(lnk: Path) -> bool:
    if getattr(sys, "frozen", False):
        target, args, workdir = sys.executable, "", str(Path(sys.executable).parent)
        icon = f"{sys.executable},0"
    else:  # geliştirme ortamı
        target = str(Path(sys.executable).with_name("pythonw.exe"))
        args, workdir = "-m cliplean", str(Path(__file__).resolve().parent.parent)
        icon = ""
    # Yollar ortam değişkeniyle geçiliyor; böylece tırnak/Türkçe karakter sorunu olmuyor.
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:VK_LNK);"
              "$s.TargetPath=$env:VK_TARGET;$s.Arguments=$env:VK_ARGS;"
              "$s.WorkingDirectory=$env:VK_DIR;if($env:VK_ICON){$s.IconLocation=$env:VK_ICON};$s.Save()")
    env = dict(os.environ, VK_LNK=str(lnk), VK_TARGET=target, VK_ARGS=args, VK_DIR=workdir, VK_ICON=icon)
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, creationflags=CREATE_NO_WINDOW)
    return result.returncode == 0 and lnk.exists()


# --- Pencere --------------------------------------------------------------

STATUS_TEXT = {
    "bekliyor": "Sırada",
    "isleniyor": "İşleniyor…",
    "kopyalandi": "Aynen kopyalandı",
    "atlandi": "Zaten küçültülmüş",
    "iptal": "Durduruldu",
}


class App:
    def __init__(self, root: tk.Tk, ffmpeg: str | None):
        self.root = root
        self.ffmpeg = ffmpeg
        self.events: queue.Queue = queue.Queue()
        self.pending: deque[Job] = deque()
        self.lock = threading.Lock()
        self.seen: set[str] = set()
        self.processor: Processor | None = None
        self.worker: threading.Thread | None = None
        self.batch: list[Job] = []
        self.rows: dict[int, str] = {}
        self.output_roots: list[Path] = []
        self.started_at = 0.0
        self.current: Job | None = None
        self.current_fraction = 0.0

        root.title(APP_NAME)
        root.geometry("720x540")
        root.minsize(560, 420)
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)
        threading.Thread(target=lambda: self.events.put((None, "guncelleme", update.check())),
                         daemon=True).start()

    # Arayüz ---------------------------------------------------------------

    def _build(self) -> None:
        root = self.root
        style = ttk.Style(root)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 16), foreground=NAVY)
        style.configure("Sub.TLabel", foreground="#555")
        style.configure("Treeview", rowheight=26)

        outer = ttk.Frame(root, padding=(16, 14, 16, 12))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{__version__}", style="Sub.TLabel").pack(side="right", anchor="n")
        # Yeni sürüm bulunursa görünür olur.
        self.update_link = tk.Label(header, fg=TEAL_DARK, cursor="hand2",
                                    font=("Segoe UI Semibold", 10, "underline"))
        ttk.Label(outer, style="Sub.TLabel",
                  text="Videoları kaliteyi bozmadan küçültür. Orijinal dosyalara dokunulmaz."
                  ).pack(anchor="w", pady=(0, 10))

        self.zone = tk.Frame(outer, bg=ZONE_BG, highlightthickness=2,
                             highlightbackground=ZONE_BORDER, highlightcolor=ZONE_BORDER)
        self.zone.pack(fill="x")
        hint = "Videoları veya klasörleri buraya sürükleyin" if TkinterDnD else "Küçültmek istediklerinizi seçin"
        self.zone_label = tk.Label(self.zone, text=hint, bg=ZONE_BG, fg=NAVY,
                                   font=("Segoe UI", 12))
        self.zone_label.pack(pady=(18, 8))
        buttons = tk.Frame(self.zone, bg=ZONE_BG)
        buttons.pack(pady=(0, 16))
        ttk.Button(buttons, text="Klasör seç…", command=self._pick_folder).pack(side="left", padx=4)
        ttk.Button(buttons, text="Video seç…", command=self._pick_files).pack(side="left", padx=4)

        # Alt kısım önce yerleşiyor ki pencere küçülünce taşan liste olsun, düğmeler değil.
        bottom = ttk.Frame(outer)
        bottom.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(outer, maximum=1000)
        self.progress.pack(side="bottom", fill="x", pady=(4, 10))
        self.status = ttk.Label(outer, text="")
        self.status.pack(side="bottom", anchor="w")

        table = ttk.Frame(outer)
        table.pack(fill="both", expand=True, pady=(12, 8))
        self.tree = ttk.Treeview(table, columns=("name", "size", "status"), show="headings",
                                 selectmode="browse")
        self.tree.heading("name", text="Video", anchor="w")
        self.tree.heading("size", text="Boyut", anchor="w")
        self.tree.heading("status", text="Durum", anchor="w")
        self.tree.column("name", width=280, stretch=True)
        self.tree.column("size", width=160, stretch=False)
        self.tree.column("status", width=220, stretch=True)
        self.tree.tag_configure("hata", foreground="#c62828")
        self.tree.tag_configure("tamam", foreground="#2e7d32")
        self.tree.tag_configure("soluk", foreground="#777")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double_click)

        self.sendto_var = tk.BooleanVar()
        self.desktop_var = tk.BooleanVar()
        ttk.Checkbutton(bottom, text="Sağ tık \"Gönder\" menüsünde göster", variable=self.sendto_var,
                        command=lambda: self._toggle_shortcut(CSIDL_SENDTO, self.sendto_var)
                        ).pack(side="left")
        ttk.Checkbutton(bottom, text="Masaüstü kısayolu", variable=self.desktop_var,
                        command=lambda: self._toggle_shortcut(CSIDL_DESKTOPDIRECTORY, self.desktop_var)
                        ).pack(side="left", padx=(12, 0))
        self.stop_btn = ttk.Button(bottom, text="Durdur", command=self._stop, state="disabled")
        self.stop_btn.pack(side="right")
        self.open_btn = ttk.Button(bottom, text="Klasörü aç", command=self._open_output, state="disabled")
        self.open_btn.pack(side="right", padx=(0, 6))

        if TkinterDnD:
            for widget in (root, self.zone, self.zone_label, self.tree):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DropEnter>>", lambda e: self._highlight(True))
                widget.dnd_bind("<<DropLeave>>", lambda e: self._highlight(False))

        self._sync_shortcuts()

    def _highlight(self, on: bool) -> str:
        bg = ZONE_BG_ACTIVE if on else ZONE_BG
        border = ZONE_BORDER_ACTIVE if on else ZONE_BORDER
        self.zone.configure(bg=bg, highlightbackground=border, highlightcolor=border)
        self.zone_label.configure(bg=bg)
        return "copy"

    # Kısayollar -----------------------------------------------------------

    def _sync_shortcuts(self) -> None:
        stale = []
        for csidl, var in ((CSIDL_SENDTO, self.sendto_var), (CSIDL_DESKTOPDIRECTORY, self.desktop_var)):
            lnk = _shortcut_path(csidl)
            exists = bool(lnk and lnk.exists())
            var.set(exists)
            if exists:
                stale.append(lnk)
        # Program başka klasöre taşındıysa kısayollar yeni konumu göstersin.
        if stale and getattr(sys, "frozen", False):
            threading.Thread(target=lambda: [_write_shortcut(p) for p in stale], daemon=True).start()

    def _toggle_shortcut(self, csidl: int, var: tk.BooleanVar) -> None:
        lnk = _shortcut_path(csidl)
        if not lnk:
            var.set(False)
            return
        if var.get():
            if not _write_shortcut(lnk):
                var.set(False)
                messagebox.showerror(APP_NAME, "Kısayol oluşturulamadı.")
        else:
            try:
                lnk.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                var.set(True)
                messagebox.showerror(APP_NAME, f"Kısayol silinemedi: {e}")

    # Dosya ekleme ---------------------------------------------------------

    def _on_drop(self, event) -> str:
        self._highlight(False)
        self.add_paths(self.root.tk.splitlist(event.data))
        return event.action

    def _pick_folder(self) -> None:
        folder = filedialog.askdirectory(title="Küçültülecek klasörü seçin")
        if folder:
            self.add_paths([folder])

    def _pick_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTS))
        files = filedialog.askopenfilenames(title="Küçültülecek videoları seçin",
                                            filetypes=[("Videolar", patterns), ("Tüm dosyalar", "*.*")])
        if files:
            self.add_paths(list(files))

    def add_paths(self, paths: list[str]) -> None:
        if not self.ffmpeg:
            return
        jobs, roots = plan(paths, self.seen)
        for r in roots:
            if r not in self.output_roots:
                self.output_roots.append(r)
        videos = [j for j in jobs if j.is_video]
        if not videos and not jobs:
            if not self._running():
                self.status.configure(text="Bu seçimde yeni video bulunamadı.")
            return
        if not self._running():
            self.batch = []
            self.progress.configure(value=0)
        self.batch += jobs
        for job in videos:
            self.rows[id(job)] = self.tree.insert("", "end", tags=("soluk",), values=(
                job.label, human_size(job.src.stat().st_size), "Sırada"))
        with self.lock:
            self.pending.extend(jobs)
        self._start_worker()

    # İşleme ---------------------------------------------------------------

    def _running(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def _start_worker(self) -> None:
        if self._running():
            return
        self.processor = Processor(self.ffmpeg, lambda job, kind, value: self.events.put((job, kind, value)))
        self.worker = threading.Thread(target=self._work, args=(self.processor,), daemon=True)
        self.stop_btn.configure(state="normal")
        self.worker.start()

    def _work(self, processor: Processor) -> None:
        keep_awake(True)
        try:
            while not processor.cancelled:
                with self.lock:
                    if not self.pending:
                        break
                    job = self.pending.popleft()
                processor.process(job)
        finally:
            keep_awake(False)
            self.events.put((None, "hepsi_bitti", 0.0))

    def _stop(self) -> None:
        if self.processor:
            self.processor.cancel()
        with self.lock:
            leftover = list(self.pending)
            self.pending.clear()
        for job in leftover:
            job.status, job.note = "iptal", "durduruldu"
            self._update_row(job)
            self.seen.discard(os.path.normcase(str(job.src)))  # tekrar eklenebilsin
        self.stop_btn.configure(state="disabled")

    def _poll(self) -> None:
        try:
            while True:
                job, kind, value = self.events.get_nowait()
                if kind == "hepsi_bitti":
                    self._finish_batch()
                    continue
                if kind == "guncelleme":
                    if value:
                        self._show_update(*value)
                    continue
                if kind == "basladi":
                    self.current, self.current_fraction, self.started_at = job, 0.0, time.monotonic()
                elif kind == "ilerleme":
                    self.current_fraction = value
                elif kind == "bitti":
                    if job is self.current:
                        self.current, self.current_fraction = None, 0.0
                    if job.status == "iptal":
                        self.seen.discard(os.path.normcase(str(job.src)))
                if job.is_video:
                    self._update_row(job)
                self._update_progress()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_update(self, version: str, url: str) -> None:
        self.update_link.configure(text=f"Yeni sürüm var: {version} — indir")
        self.update_link.bind("<Button-1>", lambda e: os.startfile(url))
        self.update_link.pack(side="right", padx=(0, 10), anchor="n")

    def _update_row(self, job: Job) -> None:
        row = self.rows.get(id(job))
        if not row:
            return
        size = human_size(job.src_size or job.src.stat().st_size)
        tag = "soluk"
        if job.status == "isleniyor":
            pct = f" %{self.current_fraction * 100:.0f}" if job is self.current and self.current_fraction else ""
            status = f"İşleniyor{pct}" + (f" · {job.note}" if job.note else "")
            tag = ""
        elif job.status == "tamam":
            size = f"{size} → {human_size(job.dst_size)}"
            status, tag = f"✓ %{(1 - job.dst_size / job.src_size) * 100:.0f} küçüldü", "tamam"
        elif job.status == "hata":
            status, tag = f"✗ {job.note}", "hata"
        else:
            status = STATUS_TEXT.get(job.status, job.status)
            if job.status == "kopyalandi" and job.note:
                status = job.note[0].upper() + job.note[1:]
            if job.status == "atlandi" and job.dst_size:
                size = f"{size} → {human_size(job.dst_size)}"
        self.tree.item(row, values=(job.label, size, status), tags=(tag,))
        if job.status == "isleniyor":
            self.tree.see(row)

    def _update_progress(self) -> None:
        videos = [j for j in self.batch if j.is_video]
        if not videos:
            return
        done = sum(1 for j in videos if j.status not in ("bekliyor", "isleniyor"))
        self.progress.configure(value=(done + self.current_fraction) / len(videos) * 1000)
        job = self.current
        if not job:
            return
        text = f"{min(done + 1, len(videos))}/{len(videos)} · {job.src.name}"
        if self.current_fraction > 0.03:
            elapsed = time.monotonic() - self.started_at
            remaining = elapsed / self.current_fraction * (1 - self.current_fraction)
            text += f" · bu video için {self._fmt_duration(remaining)} kaldı"
        elif job.note:
            text += " · hazırlanıyor"
        self.status.configure(text=text)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        if seconds < 60:
            return "1 dakikadan az"
        minutes = round(seconds / 60)
        return f"yaklaşık {minutes} dk" if minutes < 60 else f"yaklaşık {minutes // 60} sa {minutes % 60} dk"

    def _finish_batch(self) -> None:
        if self._running():  # bu arada yeni dosya eklendiyse
            return
        if self.pending:  # bitiş olayından hemen önce eklenenler
            self._start_worker()
            return
        self.stop_btn.configure(state="disabled")
        videos = [j for j in self.batch if j.is_video]
        shrunk = [j for j in videos if j.status in ("tamam", "kopyalandi")]
        before, after = sum(j.src_size for j in shrunk), sum(j.dst_size for j in shrunk)
        parts = []
        if shrunk:
            parts.append(f"{len(shrunk)} video: {human_size(before)} → {human_size(after)}"
                         + (f" (%{(1 - after / before) * 100:.0f} daha küçük)" if before else ""))
        counts = [(sum(1 for j in videos if j.status == s), label)
                  for s, label in (("atlandi", "zaten küçültülmüştü"),
                                   ("hata", "hatalı (ayrıntı için çift tıklayın)"),
                                   ("iptal", "durduruldu"))]
        parts += [f"{n} {label}" for n, label in counts if n]
        copied = sum(1 for j in self.batch if not j.is_video and j.status == "kopyalandi")
        if copied:
            parts.append(f"{copied} diğer dosya kopyalandı")
        self.status.configure(text=("Bitti. " if any(j.status != "iptal" for j in videos) else "")
                              + " · ".join(parts))
        self.progress.configure(value=1000 if not any(j.status == "iptal" for j in videos) else
                                self.progress["value"])
        if any(r.exists() for r in self.output_roots):
            self.open_btn.configure(state="normal")
        self.root.bell()

    # Diğer ---------------------------------------------------------------

    def _open_output(self) -> None:
        existing = [r for r in self.output_roots if r.exists()]
        if existing:
            os.startfile(existing[-1])

    def _on_double_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        job = next((j for j in self.batch if self.rows.get(id(j)) == row), None)
        if job and job.dst.exists():
            subprocess.Popen(["explorer", "/select,", str(job.dst)])
        elif job and job.status == "hata":
            messagebox.showerror(APP_NAME, f"{job.src}\n\n{job.note}\n\n"
                                           f"Teknik ayrıntılar: {log_dir() / 'hatalar.log'}")

    def _on_close(self) -> None:
        if self._running():
            if not messagebox.askyesno(APP_NAME, "İşlem sürüyor. Durdurup çıkmak istiyor musunuz?\n"
                                                 "Yarım kalan video kaydedilmez."):
                return
            self._stop()
            self.worker.join(timeout=5)
        self.root.destroy()


def run(paths: list[str]) -> int:
    if os.name == "nt":
        try:  # yüksek DPI ekranlarda bulanık görünmesin
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        # Görev çubuğunda Python simgesi yerine kendi simgesi görünsün.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ClipLean")
    root = TkinterDnD.Tk() if TkinterDnD else tk.Tk()
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets" / "icon.ico"
    if icon.exists():
        root.iconbitmap(default=str(icon))

    ffmpeg = find_ffmpeg()
    app = App(root, ffmpeg)
    problem = check_ffmpeg(ffmpeg)
    if problem:
        app.ffmpeg = None
        root.after(200, lambda: messagebox.showerror(APP_NAME, problem))
    elif paths:
        root.after(200, lambda: app.add_paths(paths))
    root.mainloop()
    return 0
