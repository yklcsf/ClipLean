"""Konsol modu: python -m cliplean --konsol <dosya veya klasörler>"""
from __future__ import annotations

import sys

from .core import Job, Processor, check_ffmpeg, find_ffmpeg, human_size, keep_awake, plan

STATUS_TEXT = {"tamam": "tamam", "kopyalandi": "kopyalandı", "atlandi": "atlandı",
               "hata": "HATA", "iptal": "iptal"}


def main(paths: list[str]) -> int:
    ffmpeg = find_ffmpeg()
    problem = check_ffmpeg(ffmpeg)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    jobs, _ = plan(paths)
    if not jobs:
        print("İşlenecek video bulunamadı.", file=sys.stderr)
        return 1

    started: set[int] = set()

    def on_event(job: Job, kind: str, value: float) -> None:
        if not job.is_video:
            return
        if kind == "ilerleme":
            print(f"\r  %{value * 100:5.1f}", end="", flush=True)
        elif kind == "basladi":
            started.add(id(job))
            print(f"{job.src}")
        elif kind == "bitti":
            if id(job) not in started:
                print(f"{job.src}")
            sizes = f"{human_size(job.src_size)} -> {human_size(job.dst_size)}" if job.dst_size else ""
            print(f"\r  {STATUS_TEXT.get(job.status, job.status)}  {sizes}  {job.note}")

    processor = Processor(ffmpeg, on_event)
    keep_awake(True)
    try:
        for job in jobs:
            processor.process(job)
    except KeyboardInterrupt:
        processor.cancel()
        print("\nDurduruldu.")
        return 130
    finally:
        keep_awake(False)

    videos = [j for j in jobs if j.is_video and j.status in ("tamam", "kopyalandi")]
    before, after = sum(j.src_size for j in videos), sum(j.dst_size for j in videos)
    if before:
        print(f"\n{len(videos)} video: {human_size(before)} -> {human_size(after)} "
              f"(%{(1 - after / before) * 100:.0f} daha küçük)")
    return 1 if any(j.status == "hata" for j in jobs) else 0
