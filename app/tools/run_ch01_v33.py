"""Run the reported real windows and full chapter; never claims visual acceptance.
Usage: python tools/run_ch01_v33.py CH01_ORIGINALS_FOLDER OUTPUT_ROOT
Input must be 194 already extracted, correctly ordered ORIGINAL image files.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manga_tool.importer import natural_key,IMAGE_EXTENSIONS
from manga_tool.pipeline import process_chapter


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('originals',type=Path);ap.add_argument('output',type=Path)
    ap.add_argument('--windows-only',action='store_true')
    args=ap.parse_args()
    paths=sorted([p for p in args.originals.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS],key=lambda p:natural_key(p.name))
    if len(paths)!=194:ap.error('Requires exactly 194 original Chapter 1 strips; verify reading order first')
    from PIL import Image
    for p in paths:
        with Image.open(p) as im:
            if im.size!=(690,1280):ap.error(f'Expected 690x1280 original: {p}')
    before={str(p):sha(p) for p in paths}
    report={'visual_acceptance':'NOT_PERFORMED','source_before':before,'runs':{}}
    for name,a,b in [('opening',1,18),('text',55,65),('portrait',175,179),('full',1,194)]:
        if args.windows_only and name=='full':continue
        folder=args.output/(name+'_input');folder.mkdir(parents=True,exist_ok=True)
        for i,p in enumerate(paths[a-1:b],a):
            dest=folder/f'S{i:04d}{p.suffix.lower()}'
            if dest.exists():
                if sha(dest)!=before[str(p)]:raise ValueError(f'Changed subset source: {dest}')
            else:shutil.copyfile(p,dest)
        out=args.output/(name+'_output')
        m=process_chapter(folder,out,chapter='CH01',include_all=True,generate_contact_sheets=True)
        report['runs'][name]={'original_source_orders':[a,b],'panels':[{'id':p['id'],'bounds':p['bounds'],'metadata_only':p.get('metadata_only',False),'review_task':p.get('review_task',False),'source_regions':p['source_regions']} for p in m['panels']],
            'performance':json.loads((out/'reports/performance.json').read_text())}
        report['originals_unchanged']=all(sha(p)==before[str(p)] for p in paths)
        (args.output/'real_ch01_runs.json').write_text(json.dumps(report,indent=2))
    if not report['originals_unchanged']:raise ValueError('Original hash mismatch')
    print('Run complete. Inspect original context, all candidates and review tasks; no visual acceptance is granted.')

if __name__=='__main__':main()
