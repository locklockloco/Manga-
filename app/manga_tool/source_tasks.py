"""Unresolved source spans are tasks, not exported mega-illustrations."""
from .detection import runs


def make(bounds,arrays,sources):
    x0,y0,x1,y1=bounds
    # Local low-edge bands suggest visual separators on ANY background color.
    # They do not prove scene independence and never authorize an automatic cut.
    bands=runs(arrays['edge_fraction'][y0:y1]<.004)
    markers=[]
    for a,b in bands:
        a+=y0;b+=y0
        if b-a>=6 and a>y0+12 and b<y1-12:
            markers.append({'y':(a+b)//2,'band':[a,b],
                            'reason':'local_low_edge_band_any_background',
                            'source_seam':any(s['y1']==(a+b)//2 for s in sources[:-1]),
                            'confirmed':False})
    return {'bounds':bounds,'metadata_only':True,'review_task':True,
            'recommended_classification':'uncertain','proposal_role':'primary',
            'detection_reason':'unresolved_source_span_no_artwork_export',
            'ambiguity_flags':['OVERSIZED_MULTI_SCENE_REQUIRES_REVIEW'],
            'source_review':{'original_bounds':bounds,'boundary_markers':markers,
                             'resolution':'MANUAL_SCENE_BOUNDARIES_REQUIRED',
                             'source_ids':[s['id'] for s in sources if s['y0']<y1 and s['y1']>y0]}}
