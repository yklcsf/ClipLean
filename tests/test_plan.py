from pathlib import Path

from cliplean.core import folder_output_root, human_size, plan


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def dsts(jobs):
    return {j.src.name: j.dst for j in jobs}


def test_folder_goes_to_sibling_and_keeps_structure(tmp_path):
    src = tmp_path / "Kayıtlar"
    touch(src / "a.mp4")
    touch(src / "Alt Klasör ğüş" / "b.mov")
    touch(src / "foto.jpg")
    jobs, roots = plan([src])
    out = tmp_path / "Kayıtlar (Küçültülmüş)"
    assert roots == [out]
    assert dsts(jobs) == {
        "a.mp4": out / "a.mp4",
        "b.mov": out / "Alt Klasör ğüş" / "b.mp4",
        "foto.jpg": out / "foto.jpg",
    }
    assert {j.src.name for j in jobs if j.is_video} == {"a.mp4", "b.mov"}
    assert next(j for j in jobs if j.src.name == "b.mov").label == str(Path("Kayıtlar", "Alt Klasör ğüş", "b.mov"))


def test_single_files_go_to_subfolder(tmp_path):
    video = touch(tmp_path / "kayıt.MKV")
    jobs, roots = plan([video])
    assert roots == [tmp_path / "Küçültülmüş"]
    assert jobs[0].dst == tmp_path / "Küçültülmüş" / "kayıt.mp4"


def test_mp4_keeps_its_name_when_stems_clash(tmp_path):
    touch(tmp_path / "d" / "a.mkv")
    touch(tmp_path / "d" / "a.mp4")
    jobs, _ = plan([tmp_path / "d"])
    assert dsts(jobs)["a.mp4"].name == "a.mp4"
    assert dsts(jobs)["a.mkv"].name == "a_mkv.mp4"


def test_output_folders_and_junk_are_ignored(tmp_path):
    src = tmp_path / "d"
    touch(src / "a.mp4")
    touch(src / "Küçültülmüş" / "a.mp4")
    touch(src / "eski (Küçültülmüş)" / "b.mp4")
    touch(src / "Thumbs.db")
    touch(src / "c.mp4.part")
    jobs, _ = plan([src])
    assert [j.src.name for j in jobs] == ["a.mp4"]
    # Çıktı klasörünün kendisi ya da içindeki dosya bırakılırsa hiçbir şey yapılmaz.
    assert plan([tmp_path / "d (Küçültülmüş)"])[0] == []
    assert plan([src / "Küçültülmüş" / "a.mp4"])[0] == []


def test_non_video_single_file_is_ignored(tmp_path):
    assert plan([touch(tmp_path / "belge.pdf")])[0] == []


def test_same_source_added_twice_is_planned_once(tmp_path):
    video = touch(tmp_path / "d" / "a.mp4")
    seen: set[str] = set()
    first, _ = plan([tmp_path / "d"], seen)
    second, _ = plan([video, tmp_path / "d"], seen)
    assert len(first) == 1 and second == []


def test_drive_root_output_stays_inside():
    root = Path("D:\\")
    assert folder_output_root(root) == root / "Küçültülmüş"


def test_human_size_uses_turkish_decimal_comma():
    assert human_size(512) == "512 B"
    assert human_size(2.5 * 1024 * 1024) == "2,5 MB"
    assert human_size(3 * 1024 ** 3) == "3,0 GB"
