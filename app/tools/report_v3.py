"""Generate release facts from actual saved metadata; never invent test results."""
from pathlib import Path
import argparse,json,xml.etree.ElementTree as ET
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manga_tool.pipeline import load_manifest,job_status
from manga_tool.detection import ALGORITHM_VERSION

def main():
 p=argparse.ArgumentParser();p.add_argument('workspace');p.add_argument('--tests');p.add_argument('--stress',nargs='*',default=[]);p.add_argument('--destination',default='V3_RESULTS.json');a=p.parse_args()
 root=Path(a.workspace);m=load_manifest(root)
 result={'workspace':str(root),'algorithm_version':m.get('algorithm_version'),'current_code_algorithm':ALGORITHM_VERSION,'job':job_status(root),'counts':{},'qa':json.loads((root/'reports/qa.json').read_text()) if (root/'reports/qa.json').exists() else {'status':'NOT_RUN'},'benchmark':json.loads((root/'reports/benchmark.json').read_text()) if (root/'reports/benchmark.json').exists() else {'status':'NOT_RUN'},'stress':[],'tests':{'status':'NOT_RECORDED'},'baseline':{'brief_supplied_facts':{'non_flask_tests':34,'strips':85,'regions':100,'illustrations':92,'text_candidates':4,'compound_layouts':4,'human_verified':0},'exact_named_v2_zip_available':False,'available_predecessor':'Earlier source preserved in /workspace/manga-reconstruction; not claimed identical to audited V2.'}}
 for key in ('sources','panels'):result['counts'][key]=len(m.get(key,[]))
 for status in ('NEEDS_REVIEW','ALTERNATIVE','VERIFIED','REJECTED','SUPERSEDED'):result['counts'][status]=sum(q.get('status')==status for q in m['panels'])
 if a.tests:
  x=ET.parse(a.tests).getroot();suites=[x] if x.tag=='testsuite' else list(x.iter('testsuite'));result['tests']={k:sum(float(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped','time')}
 for path in a.stress:
  f=Path(path);result['stress'].append(json.loads(f.read_text()) if f.exists() else {'file':str(f),'status':'NOT_COMPLETED'})
 Path(a.destination).write_text(json.dumps(result,indent=2))
 print(json.dumps({'counts':result['counts'],'tests':result['tests'],'stress_reports':len(result['stress'])},indent=2))
if __name__=='__main__':main()
