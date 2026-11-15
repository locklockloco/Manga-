"""Generate release evidence from saved runs and unchanged benchmark results."""
from pathlib import Path
import argparse,json,hashlib,xml.etree.ElementTree as ET
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manga_tool.pipeline import load_manifest

def read(path):return json.loads(Path(path).read_text())
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('workspace');ap.add_argument('--evidence',default='/workspace/v3-fix-evidence');ap.add_argument('--tests',required=True);ap.add_argument('--stress200',required=True);ap.add_argument('--stress500',required=True);a=ap.parse_args()
 root=Path(a.workspace);e=Path(a.evidence);m=load_manifest(root);project=Path(__file__).resolve().parents[1]
 xml=ET.parse(a.tests).getroot();suites=[xml] if xml.tag=='testsuite' else list(xml.iter('testsuite'))
 tests={k:sum(float(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped','time')}
 s200,s500=read(a.stress200),read(a.stress500)
 processing=['runner.py','identity.py','pipeline.py','detection.py','qa.py','chapter.py','importer.py','storage.py']
 hashes={}
 frozen=s500['python_source_hashes']
 for name in processing:
  rel='manga_tool/'+name;expected=frozen.get(rel);actual=digest(project/rel);hashes[rel]={'current':actual,'validated':expected,'unchanged':expected==actual}
 assert all(v['unchanged'] for v in hashes.values()),'Processing source changed after final validation'
 assert s200['passed'] and s500['passed']
 out={'release':'3.1.0','detector':m['algorithm_version'],'release_readiness':'SUPERVISED_REVIEW_ONLY','production_export_enabled':False,'regular_tests':tests,'critical_before':read(e/'reproduction/baseline_reproduction.json'),'critical_after':read(e/'reproduction/fixed_reproduction.json'),'actual_chapter_repair':read(e/'actual_chapter_repair.json'),'actual_idempotent_return':read(e/'actual_idempotent_proof.json'),'frozen_benchmark_proof':read(e/'benchmark_frozen_proof.json'),'real_file_integrity':read(root/'reports/real_file_integrity.json'),'real_counts':{'sources':len(m['sources']),'regions_including_alternatives_and_history':len(m['panels']),'status_counts':{st:sum(p.get('status')==st for p in m['panels']) for st in sorted({p['status'] for p in m['panels']})}},'qa_totals':m['qa']['totals'],'qa_required_checks':m['qa']['required_checks'],'baseline_automatic':read(e/'baseline_automatic.json'),'baseline_equivalent_review':read(e/'baseline_benchmark.json'),'final_automatic':read(root/'reports/benchmark_automatic.json'),'final_equivalent_review':read(root/'reports/benchmark.json'),'stress200':s200,'stress500':s500,'processing_hashes':hashes,'ui_provenance_patch':'review.py display-only provenance fix applied after processing validation; separately regression/browser tested. Processing modules and detector hashes above remain identical.','native_platforms':{'Linux':'tested','Windows':'untested','macOS':'untested'},'remaining_review_regions':[{'id':p['id'],'bounds':p['bounds'],'classification':p.get('classification'),'flags':p.get('flags',[])} for p in m['panels'] if p.get('status')=='NEEDS_REVIEW' and p.get('classification')=='compound_layout']}
 (project/'FIX_RESULTS.json').write_text(json.dumps(out,indent=2));(root/'reports/FIX_RESULTS.json').write_text(json.dumps(out,indent=2))
 print(json.dumps({'tests':tests,'processing_frozen':True,'recall_automatic':out['final_automatic']['panel_recall'],'recall_after_same_review':out['final_equivalent_review']['panel_recall'],'approved':out['qa_totals']['approved_artwork'],'remaining_groups':len(out['remaining_review_regions'])},indent=2))
if __name__=='__main__':main()
