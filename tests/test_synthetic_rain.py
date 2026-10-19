import csv
import hashlib
import json
from pathlib import Path

from PIL import Image

from augment_synthetic_rain import augment, write_augmented_manifest


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_augmentation_is_deterministic_and_preserves_geometry(tmp_path):
    source = tmp_path / "AutoScenario_1.png"
    Image.new("RGB", (160, 90), (140, 155, 170)).save(source)
    source_before = digest(source)

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    record = augment(source, first, preset_name="heavy", master_seed=42, angle_deg=10)
    augment(source, second, preset_name="heavy", master_seed=42, angle_deg=10)

    assert digest(source) == source_before
    assert digest(first) == digest(second)
    assert digest(first) != source_before
    assert Image.open(first).size == Image.open(source).size
    assert record["geometry_changed"] is False
    assert record["labels_may_be_copied_unchanged"] is True


def test_augmented_manifest_keeps_rows_and_records_provenance(tmp_path):
    source_root = tmp_path / "capture"
    output = tmp_path / "augmented"
    source_root.mkdir()
    output.mkdir()
    (source_root / "manifest.csv").write_text(
        "image_file,image_sha256,label_x\nimages/AutoScenario_1.png,old,123\n",
        encoding="utf-8",
    )
    records = [{
        "source_file": "AutoScenario_1.png",
        "output_file": "AutoScenario_1.png",
        "source_sha256": "source-hash",
        "output_sha256": "output-hash",
        "image_seed": 12345,
    }]

    write_augmented_manifest(source_root, output, records)

    with (output / "manifest.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["label_x"] == "123"
    assert rows[0]["image_sha256"] == "output-hash"
    assert rows[0]["source_image_sha256"] == "source-hash"
    assert rows[0]["synthetic_effect"] == "synthetic_heavy_rain_v1"
    assert rows[0]["synthetic_image_seed"] == "12345"
