"""Read-only file/pixel proof for a completed webarchive chapter, including alternatives."""
from pathlib import Path
import argparse,hashlib,json,plistlib,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from manga_tool.pipeline import load_manifest

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('workspace');ap.add_argument('--archive');a=ap.parse_args();root=Path(a.workspace);m=load_manifest(root);archive=Path(a.archive or m['input'])
    plist=plistlib.loads(archive.read_bytes());raws={r['WebResourceURL']:r['WebResourceData'] for r in plist['WebSubresources']};sources={s['id']:s for s in m['sources']}
    for s in sources.values():
        raw=(root/s['path']).read_bytes();assert raw==raws[s['origin']];assert hashlib.sha256(raw).hexdigest()==s['sha256']
    pixels=0;projections=0
    for p in m['panels']:
        assert sha(root/p['file'])==p['sha256']
        with Image.open(root/p['file']) as image:
            assert image.format=='PNG';b=p['bounds'];assert image.size==(b[2]-b[0],b[3]-b[1]);covered=0
            if p.get('polygon'):raise ValueError('This proof handles the unmasked real regression set; use streamed QA for polygon exports')
            for region in p['source_regions']:
                s=sources[region['source_id']];c=region['chapter_bounds']
                with Image.open(root/s['path']) as source:expected=np.asarray(source.convert('RGB').crop(region['source_bounds']))
                actual=np.asarray(image.crop((c[0]-b[0],c[1]-b[1],c[2]-b[0],c[3]-b[1])).convert('RGB'))
                np.testing.assert_array_equal(actual,expected);covered+=expected.shape[0]*expected.shape[1]
            assert covered==image.width*image.height;pixels+=covered
        if p.get('status') in {'NEEDS_REVIEW','VERIFIED','REJECTED','ALTERNATIVE'}:
            assert sha(root/p['output_file'])==p['sha256'];projections+=1
    result={'archive_sha256':sha(archive),'originals_compared_to_actual_embedded_resources':len(sources),'immutable_regions_including_history_and_alternatives':len(m['panels']),'named_outputs_compared':projections,'source_coordinate_pixels_compared':pixels,'pixel_mismatches':0,'manifest_file_hash_mismatches':0,'scope':'Exact source/file/coordinate proof; does not automatically approve semantic panel boundaries.'}
    target=root/'reports/real_file_integrity.json';target.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
