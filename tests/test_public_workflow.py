"""Portable input validation and reviewed-export integration tests, using toy data."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
from PIL import Image
from validate_scenarios import read_scenarios, validate_flat
from validate_dataset import validate
from review_training_labels import POLICY, SCHEMA

ROOT=Path(__file__).resolve().parents[1]


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def test_both_supplied_input_formats_and_exact_selection(tmp_path):
    assert len(read_scenarios(ROOT/'examples/scenarios'))==5
    assert len(read_scenarios(ROOT/'examples/scenarios.json'))==5
    output=tmp_path/'batch.json'
    subprocess.run([sys.executable,str(ROOT/'tools/build_batch.py'),'--src',str(ROOT/'examples/scenarios'),
                    '--output',str(output),'--weather','Broken Clouds','--count','3'],check=True)
    assert len(read_scenarios(output))==3


def test_validator_rejects_unsafe_ids_and_inconsistent_units():
    row=read_scenarios(ROOT/'examples/scenarios.json')[0]
    with pytest.raises(ValueError,match='safe'):
        validate_flat(dict(row,sid='../escape'))
    with pytest.raises(ValueError,match='alt_m and alt_ft'):
        validate_flat(dict(row,alt_ft=row['alt_ft']+20))


def test_saved_capture_integrity():
    result=validate(ROOT/'examples/sample_capture')
    assert result['images']==5 and result['status']=='PASS'


def test_capture_augmentation_preserves_camera_and_binds_derivative(tmp_path):
    source=ROOT/'examples/sample_capture';output=tmp_path/'rain'
    subprocess.run([sys.executable,str(ROOT/'tools/augment_capture.py'),'--source-dir',str(source),
                    '--output-dir',str(output)],check=True,capture_output=True,text=True)
    assert validate(output)['images']==5
    name=next((source/'telemetry').glob('*.json')).name
    original=json.loads((source/'telemetry'/name).read_text())
    derivative=json.loads((output/'telemetry'/name).read_text())
    assert derivative['before']==original['before']
    assert derivative['after']==original['after']
    assert derivative['source_image_sha256']==original['image_sha256']
    assert derivative['image_sha256']!=original['image_sha256']
    assert derivative['synthetic_appearance']['native_precipitation'] is False


def test_review_export_qa_packaging_and_corrupt_label_detection(tmp_path):
    package=tmp_path/'review';(package/'images').mkdir(parents=True);(package/'qa').mkdir()
    rows=[];decisions={}
    points=[[110,70],[40,70],[65,25],[85,25]]
    for index,sid in enumerate(['toy_accept','toy_exclude']):
        image=package/'images'/f'{sid}.png';Image.new('RGB',(160,90),(30+index,60,90)).save(image)
        row=dict(scenario_id=sid,image_file=f'images/{sid}.png',image_sha256=sha(image),image_w=160,image_h=90)
        rows.append(row)
        decisions[sid]=dict(scenario_id=sid,image_file=row['image_file'],image_sha256=row['image_sha256'],
            image_size=[160,90],corners_px=points,full_frame_checked=True,
            decision='accepted_projection' if index==0 else 'excluded',
            exclusion_reason=None if index==0 else 'toy fixture: obscured',reviewed_utc='2026-10-18T00:00:00Z')
    with (package/'manifest.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    report=package/'qa/label_report.json';report.write_text(json.dumps({'images':[]}))
    review=dict(schema=SCHEMA,status='COMPLETE_READY_TO_EXPORT',annotator='Test fixture',annotation_policy=POLICY,
                annotation_policy_sha256=hashlib.sha256(POLICY.encode()).hexdigest(),
                manifest_sha256=sha(package/'manifest.csv'),draft_report_sha256=sha(report),
                corner_order=['near_right','near_left','far_left','far_right'],images=decisions)
    review_file=package/'qa/review.json';review_file.write_text(json.dumps(review))
    output=tmp_path/'release'
    subprocess.run([sys.executable,str(ROOT/'tools/export_training_ground_truth.py'),'--package-dir',str(package),
                    '--review',str(review_file),'--output-dir',str(output)],check=True,capture_output=True)
    assert validate(output)['images']==1
    assert (output/'excluded/images/toy_exclude.png').is_file()
    archive=tmp_path/'release.zip'
    subprocess.run([sys.executable,str(ROOT/'tools/package_dataset.py'),'--dataset-dir',str(output),
                    '--output',str(archive)],check=True,capture_output=True)
    assert (tmp_path/'release.zip.sha256').read_text().startswith(sha(archive))
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert 'release/images/toy_accept.png' in zipped.namelist()
    (output/'labels_seg/toy_accept.txt').write_text('0 0 0\n')
    with pytest.raises(ValueError,match='labels_seg'):
        validate(output)
