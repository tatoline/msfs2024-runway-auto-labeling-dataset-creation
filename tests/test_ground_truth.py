"""Offline tests for the human-review ground-truth gate."""
import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from export_training_ground_truth import main as export_main
from review_training_labels import POLICY, SCHEMA, valid_polygon


class GroundTruthTests(unittest.TestCase):
    def test_polygon_requires_convex_in_frame_target(self):
        self.assertTrue(valid_polygon([[70,70],[30,70],[35,30],[65,30]],100,100))
        self.assertFalse(valid_polygon([[70,70],[30,30],[35,70],[65,30]],100,100))
        self.assertFalse(valid_polygon([[170,70],[30,70],[35,30],[65,30]],100,100))

    def test_export_refuses_incomplete_and_exports_exact_pixels_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);package=root/'package';(package/'images').mkdir(parents=True);(package/'qa').mkdir()
            Image.new('RGB',(100,100)).save(package/'images/one.png')
            with (package/'manifest.csv').open('w',newline='',encoding='utf-8') as f:
                w=csv.DictWriter(f,fieldnames=['scenario_id','image_file','icao','hour']);w.writeheader();w.writerow({'scenario_id':'one','image_file':'images/one.png','icao':'TEST','hour':12})
            report={'images':[{'scenario_id':'one','status':'candidate'}]};(package/'qa/label_report.json').write_text(json.dumps(report))
            digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            review={'schema':SCHEMA,'status':'IN_PROGRESS_NOT_GROUND_TRUTH','annotator':'Tester','manifest_sha256':digest(package/'manifest.csv'),
                    'draft_report_sha256':digest(package/'qa/label_report.json'),'annotation_policy_sha256':hashlib.sha256(POLICY.encode()).hexdigest(),
                    'annotation_policy':POLICY,'corner_order':['near_right','near_left','far_left','far_right'],'images':{}}
            path=root/'review.json';path.write_text(json.dumps(review))
            args=['export_training_ground_truth.py','--package-dir',str(package),'--review',str(path),'--output-dir',str(root/'bad')]
            with patch.object(sys,'argv',args),contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):export_main()
            self.assertFalse((root/'bad').exists())
            points=[[70,70],[30,70],[35,30],[65,30]]
            review['status']='COMPLETE_READY_TO_EXPORT';review['images']['one']={'scenario_id':'one','image_file':'images/one.png','image_sha256':digest(package/'images/one.png'),
                    'image_size':[100,100],'corners_px':points,'decision':'accepted_corrected','exclusion_reason':None,'notes':'','reviewed_utc':'2026-09-01T00:00:00Z'}
            review['images']['one']['full_frame_checked']=True
            path.write_text(json.dumps(review));args[-1]=str(root/'good')
            with patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):export_main()
            label=json.loads((root/'good/labels_px/one.json').read_text())
            self.assertEqual(label['polygon_px'],points)
            self.assertEqual(label['bbox_xyxy_px'],[30,30,70,70])
            self.assertEqual(json.loads((root/'good/release_receipt.json').read_text())['status'],'HUMAN_REVIEWED_TRAINING_GROUND_TRUTH')


if __name__=='__main__':unittest.main()
