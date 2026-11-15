"""Conservative artwork-first geometry; never edits source pixels.

This is evidence from components, not a semantic segmentation model. Uncertain
monochrome drawings are retained for review instead of being discarded as text.
"""
import cv2
import numpy as np


def analyze(rgb, padding=4):
    from .detection import foreground
    from .text_evidence import inspect
    mask,ocr=inspect(rgb)
    if ocr.get('standalone_text'):
        return None,{'standalone_text':True,'reason':'ocr_explained_ink','ocr':ocr,'review_required':True}
    # Link outlines and internal shading without bridging distant balloons.
    linked=cv2.morphologyEx(mask.astype('uint8'),cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    n,labels,stats,_=cv2.connectedComponentsWithStats(linked,8)
    records=[]
    for i,(x,y,w,h,area) in enumerate(stats[1:],1):
        if area<5:
            continue
        values=rgb[y:y+h,x:x+w][(labels[y:y+h,x:x+w]==i)&mask[y:y+h,x:x+w]]
        if not len(values):
            continue
        sample=values[::max(1,len(values)//12000)]
        quant=sample.astype('int16')//24
        _,counts=np.unique(quant,axis=0,return_counts=True)
        diverse=len(counts)>=14 and float(counts.max()/len(sample))<.72
        records.append(dict(box=[int(x),int(y),int(x+w),int(y+h)],area=int(area),
                            diverse=bool(diverse),palette=len(counts)))
    art=[r for r in records if r['diverse'] and r['area']>=max(120,mask.size*.0007)]
    evidence={'method':'component_palette_artwork_priority_v1','semantic_certainty':False,
              'speech_preservation_required':False,'review_required':True,'ocr':ocr,
              'geometry_limitation':ocr.get('geometry_limitation',False)}
    if not art:
        # Repeated similarly sized, solid-color glyphs on otherwise empty paper.
        # One glyph, outlines, and B/W drawings remain uncertain.
        glyphs=[r for r in records if r['area']>=12]
        if len(glyphs)>=5 and mask.mean()<.24:
            heights=np.array([r['box'][3]-r['box'][1] for r in glyphs])
            centers=np.array([(r['box'][1]+r['box'][3])/2 for r in glyphs])
            aligned=np.max([np.sum(np.abs(centers-c)<np.median(heights)*.55) for c in centers])
            similar=np.mean((heights>np.median(heights)*.65)&(heights<np.median(heights)*1.5))
            if aligned>=5 and similar>=.85 and all(r['palette']<=6 for r in glyphs):
                return None,dict(evidence,standalone_text=False,review_task=True,reason='unconfirmed_repeated_glyph_pattern')
        return None,dict(evidence,standalone_text=False,reason='insufficient_artwork_evidence_preserve_for_review')
    # Only exclude positive repeated-glyph evidence. Every unexplained mark,
    # however small, expands the crop so a detached weapon tip is protected.
    text=[]
    for r in records:
        b=r['box'];height=b[3]-b[1];cy=(b[1]+b[3])/2
        peers=[q for q in records if q['palette']<=6 and
               .65*height<q['box'][3]-q['box'][1]<1.5*height and
               abs((q['box'][1]+q['box'][3])/2-cy)<height*.55]
        if r['palette']<=6 and len(peers)>=5:
            text.append(r)
    balloons=[]
    for r in records:
        b=r['box'];area=(b[2]-b[0])*(b[3]-b[1])
        enclosed=[q for q in text if b[0]<q['box'][0] and b[1]<q['box'][1]
                  and b[2]>q['box'][2] and b[3]>q['box'][3]]
        if r not in art and r['area']/max(1,area)<.16 and len(enclosed)>=3:
            balloons.append(r)
    protected=[r for r in records if r not in text and r not in balloons]
    boxes=[r['box'] for r in protected]
    if not boxes:
        return None,dict(evidence,standalone_text=False,reason='ambiguous_content_preserved')
    x0=min(b[0] for b in boxes);y0=min(b[1] for b in boxes)
    x1=max(b[2] for b in boxes);y1=max(b[3] for b in boxes)
    bound=[max(0,x0-padding),max(0,y0-padding),min(rgb.shape[1],x1+padding),min(rgb.shape[0],y1+padding)]
    return bound,dict(evidence,standalone_text=False,reason='protected_analysis_ink_envelope_not_semantic_completion')


def refine(chapter, proposals, padding):
    from .detection import rgb_array
    output=[]
    for proposal in proposals:
        if proposal.get('review_task'):
            output.append(proposal)
            continue
        original=list(proposal['bounds'])
        # Do not change uncertain layout topology or make a preview-edge cut.
        if original[3]-original[1]>3600:
            proposal.setdefault('ambiguity_flags',[]).append('OVERSIZED_MULTI_SCENE_REQUIRES_REVIEW')
            if original[3]-original[1]>7200:
                proposal.update(metadata_only=True,review_task=True,recommended_classification='uncertain')
            output.append(proposal)
            continue
        with chapter.preview(original,max_size=1800) as im:
            rgb=rgb_array(im)
        scale_x=(original[2]-original[0])/rgb.shape[1]
        scale_y=(original[3]-original[1])/rgb.shape[0]
        bound,evidence=analyze(rgb,padding=padding)
        proposal['artwork_priority']=dict(evidence,original_proposal_bounds=original)
        if evidence.get('geometry_limitation'):
            proposal.setdefault('ambiguity_flags',[]).append('RECTANGULAR_TEXT_REMOVAL_GEOMETRY_UNRESOLVED')
        if evidence.get('review_task'):
            proposal.update(metadata_only=True,review_task=True,recommended_classification='uncertain')
            proposal.setdefault('ambiguity_flags',[]).append('TEXT_OR_SPARSE_ART_CLASSIFICATION_REQUIRED')
        if evidence.get('standalone_text'):
            proposal['metadata_only']=True
            proposal['recommended_classification']='dialogue_or_narration_only'
        elif bound and proposal.get('proposal_role')=='primary':
            proposal['bounds']=[original[0]+int(bound[0]*scale_x),original[1]+int(bound[1]*scale_y),
                                original[0]+int(np.ceil(bound[2]*scale_x)),original[1]+int(np.ceil(bound[3]*scale_y))]
            if proposal['bounds']!=original:
                proposal.setdefault('ambiguity_flags',[]).append('ARTWORK_PRIORITY_TRIM_REQUIRES_REVIEW')
        output.append(proposal)
    return output
