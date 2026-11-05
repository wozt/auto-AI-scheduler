"""Private local OCR reference files and generated regression specifications.

These are intentionally NOT stored inside the Git working tree. A generated
OCR test describes expected text and ROI but cannot trigger desktop actions.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

PROOF_DIR = Path.home()/'.local'/'share'/'auto-ai-scheduler'/'proofs'


def normalize(text):
    return ' '.join(text.casefold().split())


def parse_roi(value):
    try:
        x,y,w,h=(float(s.strip()) for s in value.split(','))
    except (ValueError,AttributeError) as exc:
        raise ValueError('ROI must be x,y,width,height in percentages') from exc
    if not (0<=x<100 and 0<=y<100 and 0<w<=100-x and 0<h<=100-y):
        raise ValueError('ROI outside image')
    return x,y,w,h


def crop_roi(image, roi):
    x,y,w,h=parse_roi(roi)
    return image.crop((round(image.width*x/100), round(image.height*y/100),
                       round(image.width*(x+w)/100),round(image.height*(y+h)/100)))


def ocr_image(image,roi='0,0,100,100'):
    import pytesseract
    return pytesseract.image_to_string(crop_roi(image,roi).convert('RGB'),lang='eng',config='--psm 11').strip()


def match_text(actual,expected):
    return bool(expected.strip()) and normalize(expected) in normalize(actual)


@dataclass(frozen=True)
class ProofTest:
    proof_id: str
    expected: str
    roi: str


class ProofStore:
    def __init__(self,directory=PROOF_DIR):
        self.directory=Path(directory)
        self.manifest=self.directory/'tests.json'

    def _ensure(self):
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.directory.chmod(0o700)

    def _file(self, proof_id):
        if not re.fullmatch(r'[0-9a-f]{32}',proof_id):
            raise ValueError('Invalid reference id')
        return self.directory/(proof_id+'.png')

    def import_image(self,source):
        from PIL import Image
        source=Path(source)
        if not source.is_file() or source.stat().st_size>16*1024*1024:
            raise ValueError('Expected an image file smaller than 16 MiB')
        with Image.open(source) as image:
            image.verify()
        with Image.open(source) as image:
            if image.width<40 or image.height<20 or image.width*image.height>16_000_000:
                raise ValueError('Unsupported image dimensions')
            image=image.convert('RGB')
            self._ensure()
            proof_id=uuid.uuid4().hex
            path=self._file(proof_id)
            fd,temp=tempfile.mkstemp(dir=self.directory,prefix='.private-proof-',suffix='.png')
            try:
                with os.fdopen(fd,'wb') as handle:
                    image.save(handle,format='PNG')
                os.chmod(temp,0o600)
                os.replace(temp,path)
            finally:
                if os.path.exists(temp): os.unlink(temp)
        return proof_id

    def get_image(self,proof_id):
        from PIL import Image
        with Image.open(self._file(proof_id)) as img:
            return img.copy()

    def generate_test(self,proof_id,expected='',roi='0,0,100,100'):
        """Record baseline test from user-supplied image, with no internet access."""
        actual=ocr_image(self.get_image(proof_id),roi)
        if not expected:
            expected=next((line.strip() for line in actual.splitlines() if len(line.strip())>=5),'')
        if not expected:
            raise ValueError('OCR found no stable text; select a better screenshot')
        spec=ProofTest(proof_id,expected,roi)
        self._ensure()
        tests=self.read_tests()
        tests[proof_id]={'expected':expected,'roi':roi}
        data=json.dumps(tests,indent=2,ensure_ascii=False).encode('utf-8')
        fd,temp=tempfile.mkstemp(dir=self.directory,prefix='.proof-tests-')
        try:
            with os.fdopen(fd,'wb') as f:f.write(data)
            os.chmod(temp,0o600)
            os.replace(temp,self.manifest)
        finally:
            if os.path.exists(temp):os.unlink(temp)
        return spec,actual

    def read_tests(self):
        if not self.manifest.exists():return {}
        return json.loads(self.manifest.read_text(encoding='utf-8'))

    def test(self,proof_id,observed_image=None):
        config=self.read_tests().get(proof_id)
        if not config:
            raise ValueError('Generate the OCR test first')
        source=observed_image if observed_image is not None else self.get_image(proof_id)
        actual=ocr_image(source,config['roi'])
        return match_text(actual,config['expected']),actual
