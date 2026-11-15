"""Explicit real acceptance checks; run against a completed actual-source workspace.

This does not guess the missing audited V2 CH05_P010 ID. The independently
reviewed two-scene source coordinates are recorded instead.
"""
import json,os
from pathlib import Path
import pytest
from PIL import Image
ROOT=os.environ.get('MANGA_REAL_OUTPUT')
pytestmark=pytest.mark.skipif(not ROOT,reason='Set MANGA_REAL_OUTPUT to the completed real Chapter 5 workspace')

def manifest():
    from manga_tool.pipeline import load_manifest
    return load_manifest(ROOT)

@pytest.mark.parametrize('orders,bounds',[( [39,40],[0,48815,690,51177]),([41,42],[0,51519,690,53552])])
def test_actual_egg_scene_exact_join(orders,bounds):
    m=manifest();p=next(p for p in m['panels'] if p['bounds']==bounds)
    assert [r['source_order'] for r in p['source_regions']]==orders
    assert p['status']!='VERIFIED'
    with Image.open(Path(ROOT)/p['file']) as image:
        assert image.size==(bounds[2]-bounds[0],bounds[3]-bounds[1])
    assert all(bounds[1] < m['sources'][n-1]['y1'] < bounds[3] for n in orders[:-1])

def test_actual_two_scene_source_window_has_separate_reviewed_choices():
    m=manifest()
    candidates=[p for p in m['panels'] if p['status']=='NEEDS_REVIEW' and p.get('active',True) and p['bounds'][1]>=9128 and p['bounds'][3]<=10968]
    assert len(candidates)>=2
    assert any(p['bounds'][3]<=9765 for p in candidates)
    assert any(p['bounds'][1]>=9765 for p in candidates)
    assert all(p['status']!='VERIFIED' for p in candidates)
    report=json.loads((Path(ROOT)/'reports/benchmark.json').read_text())
    assert report['false_verified_claims']==0


def test_seven_previously_unresolved_compositions_are_intact_unapproved_primaries():
    import hashlib
    gold_path=Path(__file__).resolve().parents[1]/'benchmarks/leviathan5_gold.json'
    assert hashlib.sha256(gold_path.read_bytes()).hexdigest()=='c08988c70e5e5fa629226deaa31e21fe2abca367fd90a556f9a2719940a86c52'
    gold=json.loads(gold_path.read_text())
    cases={'LV5_A03','LV5_A04','LV5_A07','LV5_A14','G_B060','G_B073','G_B074'}
    m=manifest()
    for label in [g for g in gold['regions'] if g['id'] in cases]:
        b=label['bounds'];area=(b[2]-b[0])*(b[3]-b[1])
        matches=[]
        for p in m['panels']:
            q=p['bounds'];overlap=max(0,min(q[2],b[2])-max(q[0],b[0]))*max(0,min(q[3],b[3])-max(q[1],b[1]))
            if overlap/area>=.95 and p.get('status')=='NEEDS_REVIEW' and p.get('classification')=='illustration' and p.get('proposal_role')=='primary' and not p.get('group_key'):
                matches.append(p)
        assert matches,label['id']
        assert all((Path(ROOT)/p['file']).is_file() for p in matches)
    assert not any(p.get('status')=='VERIFIED' for p in m['panels'])
