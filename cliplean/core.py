"""Video küçültme çekirdeği: dosyaları bulur, içeriği analiz eder, ffmpeg ile kodlar.

Arayüzden bağımsızdır; hem pencere hem konsol modu bunu kullanır.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

APP_NAME = "ClipLean"
OUTPUT_DIR_NAME = "Küçültülmüş"
FOLDER_SUFFIX = f" ({OUTPUT_DIR_NAME})"

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv",
    ".ts", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg",
}
IGNORED_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}
PART_SUFFIX = ".part"

# Kodlama ayarları; gerçek ekran kaydı ve telefon videosu üzerinde VMAF ile ölçülerek seçildi.
# Ekran kaydı: HandBrake x264 RF30 720p ayarına göre ~%44 daha küçük, daha yüksek kalite.
# Kamera: 1080p korunuyor; 720p'ye küçültmek aynı boyutta belirgin kalite kaybı veriyordu.
# CRF 40, HandBrake "Fast 1080p30" ile aynı VMAF'ı üçte bir boyutta veriyor.
MAX_SHORT_SIDE_SCREEN = 720
MAX_SHORT_SIDE_CAMERA = 1080
MAX_FPS = 60
# Preset: yüksek = hızlı. Ekran kaydında 9, 6'ya göre 2 kat hızlı ve kalite farkı ölçülemeyecek kadar az.
SVT_PRESET_SCREEN = 9
SVT_PRESET_CAMERA = 8
CRF_SCREEN = 48
CRF_CAMERA = 40
AUDIO_BITRATE = "48k"
# Kareler arası parlaklık farkının (YDIF) medyanı bunun altındaysa içerik ekran kaydıdır.
# Ölçümler: ekran kaydı ~0.0, kamera/oyun görüntüsü 2+.
SCREEN_YDIF_THRESHOLD = 0.5

_NT = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if _NT else 0
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000 if _NT else 0


class Cancelled(Exception):
    pass


class EncodeError(Exception):
    pass


@dataclass
class Job:
    src: Path
    dst: Path
    is_video: bool
    label: str = ""  # arayüzde gösterilecek ad, örn. "Kayıtlar\\Ocak\\a.mp4"
    status: str = "bekliyor"  # bekliyor, isleniyor, tamam, kopyalandi, atlandi, hata, iptal
    note: str = ""
    src_size: int = 0
    dst_size: int = 0


@dataclass
class MediaInfo:
    duration: float | None
    has_video: bool
    has_audio: bool


# --- Planlama -------------------------------------------------------------

def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def is_output_dir(path: Path) -> bool:
    return path.name == OUTPUT_DIR_NAME or path.name.endswith(FOLDER_SUFFIX)


def folder_output_root(folder: Path) -> Path:
    """'D:\\Kayıtlar' -> 'D:\\Kayıtlar (Küçültülmüş)'; sürücü kökü için kökün içine."""
    if folder.parent == folder:
        return folder / OUTPUT_DIR_NAME
    return folder.parent / (folder.name + FOLDER_SUFFIX)


def plan(paths: list[str | Path], seen: set[str] | None = None) -> tuple[list[Job], list[Path]]:
    """Verilen dosya/klasörler için iş listesi ve çıktı kök klasörlerini döndürür.

    `seen`, aynı kaynağın iki kez eklenmesini önlemek için çağrılar arasında paylaşılabilir.
    """
    seen = set() if seen is None else seen
    taken: set[str] = set()
    jobs: list[Job] = []
    roots: list[Path] = []

    def add(src: Path, dst: Path, label: str) -> None:
        key = os.path.normcase(str(src))
        if key in seen:
            return
        seen.add(key)
        video = is_video(src)
        if video:
            dst = dst.with_suffix(".mp4")
            # Aynı klasörde 'a.mkv' ve 'a.mp4' varsa 'a.mp4' adı gerçek mp4'te kalsın, mkv 'a_mkv.mp4' olsun.
            clash = src.suffix.lower() != ".mp4" and src.with_suffix(".mp4").exists()
            if clash or os.path.normcase(str(dst)) in taken:
                dst = dst.with_name(f"{src.stem}_{src.suffix[1:].lower()}.mp4")
        taken.add(os.path.normcase(str(dst)))
        jobs.append(Job(src=src, dst=dst, is_video=video, label=label))

    for raw in paths:
        p = Path(os.path.abspath(raw))
        if p.is_dir():
            if is_output_dir(p):
                continue
            out_root = folder_output_root(p)
            roots.append(out_root)
            for dirpath, dirnames, filenames in os.walk(p):
                d = Path(dirpath)
                dirnames[:] = sorted(n for n in dirnames if not is_output_dir(d / n))
                for name in sorted(filenames, key=str.lower):
                    if name.lower() in IGNORED_FILES or name.endswith(PART_SUFFIX):
                        continue
                    src = d / name
                    rel = src.relative_to(p)
                    add(src, out_root / rel, str(Path(p.name) / rel))
        elif p.is_file() and is_video(p) and not is_output_dir(p.parent):
            out_root = p.parent / OUTPUT_DIR_NAME
            if out_root not in roots:
                roots.append(out_root)
            add(p, out_root / p.name, p.name)
    return jobs, roots


# --- ffmpeg ---------------------------------------------------------------

def _base_dir() -> Path:
    # PyInstaller paketinde _MEIPASS, geliştirmede proje kökü.
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def find_ffmpeg() -> str | None:
    base = _base_dir()
    for candidate in (base / "ffmpeg" / "ffmpeg.exe", base / "ffmpeg.exe",
                      Path(sys.executable).parent / "ffmpeg.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=CREATE_NO_WINDOW, **kw)


def check_ffmpeg(ffmpeg: str | None) -> str | None:
    """Sorun varsa kullanıcıya gösterilecek mesajı, yoksa None döndürür."""
    if not ffmpeg:
        return "ffmpeg bulunamadı. Programı indirdiğiniz zip dosyasındaki tüm dosyaları birlikte çıkarın."
    try:
        out = _run([ffmpeg, "-hide_banner", "-encoders"]).stdout
    except OSError as e:
        return f"ffmpeg çalıştırılamadı: {e}"
    missing = [enc for enc in ("libsvtav1", "libopus") if enc not in out]
    if missing:
        return f"ffmpeg sürümünde gerekli kodlayıcılar yok: {', '.join(missing)}"
    return None


def probe(ffmpeg: str, src: Path) -> MediaInfo:
    err = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(src)]).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    streams = [line for line in err.splitlines() if re.match(r"\s*Stream #", line)]
    has_video = any(": Video:" in s and "(attached pic)" not in s for s in streams)
    has_audio = any(": Audio:" in s for s in streams)
    return MediaInfo(duration, has_video, has_audio)


def looks_like_screen(ffmpeg: str, src: Path, duration: float | None) -> bool:
    """Videodan birkaç kısa parça örnekleyip görüntünün ne kadar durağan olduğuna bakar."""
    if duration and duration > 30:
        samples = [(duration * f, 6) for f in (0.15, 0.5, 0.85)]
    else:
        samples = [(0.0, 20)]
    diffs: list[float] = []
    for start, length in samples:
        err = _run([ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{start:.2f}", "-t", str(length),
                    "-i", str(src), "-an", "-sn", "-vf",
                    "scale=320:-2,signalstats,metadata=print:key=lavfi.signalstats.YDIF",
                    "-f", "null", "-"]).stderr
        values = [float(v) for v in re.findall(r"YDIF=([\d.]+)", err)]
        diffs += values[1:]  # ilk karenin önceki karesi yok, değeri hep 0
    return bool(diffs) and statistics.median(diffs) < SCREEN_YDIF_THRESHOLD


def encode_args(ffmpeg: str, src: Path, out: Path, screen: bool, has_audio: bool) -> list[str]:
    # Kısa kenara sınır konuyor; dikey telefon videoları da doğru küçülür.
    side = MAX_SHORT_SIDE_SCREEN if screen else MAX_SHORT_SIDE_CAMERA
    scale = (f"scale=w='if(gte(iw,ih),-2,min({side},iw))'"
             f":h='if(gte(iw,ih),min({side},ih),-2)'")
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src),
            "-map", "0:V:0", "-map_metadata", "0",
            "-vf", scale, "-fpsmax", str(MAX_FPS),
            "-c:v", "libsvtav1", "-preset", str(SVT_PRESET_SCREEN if screen else SVT_PRESET_CAMERA),
            "-crf", str(CRF_SCREEN if screen else CRF_CAMERA),
            "-pix_fmt", "yuv420p10le", "-svtav1-params", "tune=0:keyint=10s"]
    if has_audio:
        args += ["-map", "0:a:0", "-c:a", "libopus", "-b:a", AUDIO_BITRATE, "-ac", "2"]
    args += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-f", "mp4", str(out)]
    return args


# --- Dosya yardımcıları ---------------------------------------------------

def _copy_times(src: Path, dst: Path) -> None:
    """Değiştirilme ve (Windows'ta) oluşturulma tarihini kaynaktan kopyalar."""
    st = src.stat()
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
    if not _NT:
        return
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = k32.CreateFileW(str(dst), 0x100, 0, None, 3, 0x80, None)  # FILE_WRITE_ATTRIBUTES, OPEN_EXISTING
    if handle == ctypes.c_void_p(-1).value:
        return
    try:
        ft = st.st_birthtime_ns // 100 + 116444736000000000
        created = wintypes.FILETIME(ft & 0xFFFFFFFF, ft >> 32)
        k32.SetFileTime(handle, ctypes.byref(created), None, None)
    finally:
        k32.CloseHandle(handle)


def keep_awake(on: bool) -> None:
    """İşlem sürerken bilgisayarın uykuya geçmesini engeller (çağıran iş parçacığı için)."""
    if _NT:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x1 if on else 0))


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / APP_NAME


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return (f"{n:.0f}" if unit in ("B", "KB") else f"{n:.1f}").replace(".", ",") + " " + unit
        n /= 1024
    return f"{n:.1f}".replace(".", ",") + " TB"


# --- İşleyici -------------------------------------------------------------

EventFn = Callable[[Job, str, float], None]  # (iş, olay, değer); olay: "basladi", "ilerleme", "bitti"


class Processor:
    """İşleri tek tek yürütür. `cancel()` başka bir iş parçacığından çağrılabilir."""

    def __init__(self, ffmpeg: str, on_event: EventFn):
        self.ffmpeg = ffmpeg
        self.on_event = on_event
        self._proc: subprocess.Popen | None = None
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.kill()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def process(self, job: Job) -> None:
        part = job.dst.with_name(job.dst.name + PART_SUFFIX)
        try:
            job.src_size = job.src.stat().st_size
            existing = self._existing_output(job)
            if existing:
                job.status, job.note, job.dst_size = "atlandi", "daha önce küçültülmüş", existing.stat().st_size
                return
            if self.cancelled:
                raise Cancelled
            job.status = "isleniyor"
            self.on_event(job, "basladi", 0.0)
            job.dst.parent.mkdir(parents=True, exist_ok=True)
            final = job.dst
            if job.is_video:
                final = self._encode(job, part)
            else:
                shutil.copyfile(job.src, part)
                job.status = "kopyalandi"
            os.replace(part, final)
            # Yeniden adlandırmadan sonra: Windows "file tunneling" aksi halde eski tarihi geri yazıyor.
            _copy_times(job.src, final)
            job.dst, job.dst_size = final, final.stat().st_size
        except Cancelled:
            job.status, job.note = "iptal", "durduruldu"
        except Exception as e:  # tek dosyanın hatası tüm işi durdurmasın
            job.status, job.note = "hata", str(e) or e.__class__.__name__
            self._log_error(job, e)
        finally:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass
            self.on_event(job, "bitti", 1.0)

    def _existing_output(self, job: Job) -> Path | None:
        if job.dst.exists():
            return job.dst
        # Kodlama dosyayı büyüttüyse orijinal uzantısıyla kopyalanmış olabilir.
        same_ext = job.dst.with_suffix(job.src.suffix)
        return same_ext if job.is_video and same_ext.exists() else None

    def _encode(self, job: Job, part: Path) -> Path:
        info = probe(self.ffmpeg, job.src)
        if not info.has_video and not info.has_audio:
            raise EncodeError("dosya okunamadı, bozuk olabilir")
        if not info.has_video:
            shutil.copyfile(job.src, part)
            job.status, job.note = "kopyalandi", "görüntü yok, aynen kopyalandı"
            return job.dst.with_suffix(job.src.suffix)

        screen = looks_like_screen(self.ffmpeg, job.src, info.duration)
        if self.cancelled:
            raise Cancelled
        job.note = "ekran kaydı" if screen else "kamera / hareketli"
        self._run_ffmpeg(encode_args(self.ffmpeg, job.src, part, screen, info.has_audio),
                         job, info.duration)

        out = probe(self.ffmpeg, part)
        if info.duration and (out.duration is None
                              or abs(out.duration - info.duration) > max(2.0, info.duration * 0.02)):
            raise EncodeError("çıktı videosu eksik görünüyor, kaynak dosya bozuk olabilir")

        if part.stat().st_size >= job.src_size:
            part.unlink()
            shutil.copyfile(job.src, part)
            job.status, job.note = "kopyalandi", "zaten küçüktü, aynen kopyalandı"
            return job.dst.with_suffix(job.src.suffix)
        job.status = "tamam"
        return job.dst

    def _run_ffmpeg(self, args: list[str], job: Job, duration: float | None) -> None:
        with tempfile.TemporaryFile() as errfile:
            self._proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=errfile, stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS)
            try:
                for raw in self._proc.stdout:
                    key, _, value = raw.decode("ascii", "replace").strip().partition("=")
                    if key == "out_time_us" and duration and value.isdigit():
                        self.on_event(job, "ilerleme", min(int(value) / 1e6 / duration, 1.0))
                code = self._proc.wait()
            finally:
                self._proc = None
            if self.cancelled:
                raise Cancelled
            if code != 0:
                errfile.seek(0)
                tail = errfile.read().decode("utf-8", "replace").strip().splitlines()[-15:]
                error = EncodeError("video işlenemedi, dosya bozuk ya da desteklenmiyor olabilir")
                error.detail = "\n".join(tail)
                raise error

    def _log_error(self, job: Job, error: Exception) -> None:
        try:
            d = log_dir()
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "hatalar.log", "a", encoding="utf-8") as f:
                f.write(f"--- {job.src}\n{error}\n{getattr(error, 'detail', '')}\n")
        except OSError:
            pass
