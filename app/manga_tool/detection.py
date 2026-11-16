"""Conservative V3 chapter-coordinate region proposals.

Morphology, paper models, contours and edge maps affect analysis only. Every
proposal refers to untouched original pixels. Ambiguous separations are retained
as reversible parent/child alternatives; none of these functions verifies art.
"""
from pathlib import Path
import hashlib
import json
import os
import cv2
import numpy as np
from PIL import Image
from .chapter import Chapter

# Avoid OpenCV creating one native thread per CPU for every local job.
cv2.setNumThreads(1)

ALGORITHM_VERSION = 'v3.3-review-tasks-1'
MAX_WINDOW_HEIGHT = 3600
MAX_ANALYSIS_DIMENSION = 1800


def rgb_array(image):
    if image.mode == 'RGBA' or 'transparency' in image.info or image.mode == 'LA':
        with image.convert('RGBA') as rgba:
            with Image.new('RGBA', image.size, 'white') as bg:
                bg.alpha_composite(rgba)
                return np.asarray(bg.convert('RGB')).copy()
    return np.asarray(image.convert('RGB'))


def _edge_map(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return (np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)) +
            np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1)))


def _paper_model(rgb, colored=False):
    """Estimate an EXTERIOR paper hypothesis, never a semantic blank label.

    Dark backgrounds are ineligible. Colored hypotheses are used to propose
    review alternatives only; foreground/coverage remains conservative.
    """
    h,w = rgb.shape[:2]
    k = max(1,min(6,h,w))
    samples = np.concatenate([rgb[:k].reshape(-1,3),rgb[-k:].reshape(-1,3),
                              rgb[:,:k].reshape(-1,3),rgb[:,-k:].reshape(-1,3)])
    a = samples.astype(np.int16)
    eligible = (a.min(1)>=165) & (a.mean(1)>=190)
    if not colored:
        eligible &= (a.min(1)>=225) & (np.ptp(a,axis=1)<=22)
    values = a[eligible]
    if len(values)<20:
        return {'rgb':[255,255,255],'support':0.0,'kind':'white_fallback','reliable':False}
    # A modal bin, rather than a median of unrelated art and margin colors.
    quant = np.clip(values//8,0,31)
    bins = quant[:,0]*1024+quant[:,1]*32+quant[:,2]
    unique,counts = np.unique(bins,return_counts=True)
    best = unique[np.argmax(counts)]
    center = np.median(values[bins==best],axis=0)
    distances = np.max(np.abs(a-center),axis=1)
    stable = distances<=9
    support = float(stable.mean())
    reliable = support>=.26
    kind = 'colored' if np.ptp(center)>22 else ('off_white' if center.min()<247 else 'white')
    return {'rgb':[int(round(v)) for v in center], 'support':round(support,4),
            'kind':kind,'reliable':bool(reliable)}


def foreground(rgb):
    """Protect ink, faint edges, and all dark/colored illustration backgrounds."""
    a = rgb.astype(np.int16)
    model = _paper_model(rgb,colored=False)
    paper = np.array(model['rgb']) if model['reliable'] else np.array([255,255,255])
    # Only neutral light paper is removable by this conservative coverage mask.
    distance = np.max(np.abs(a-paper),axis=2)
    near_paper = (distance<=7) & (a.min(2)>=min(238,int(paper.min())-5)) & (np.ptp(a,axis=2)<=24)
    near_paper |= (a.min(2)>=250) & (np.ptp(a,axis=2)<=10)
    edge_strength = _edge_map(rgb)
    mask = (~near_paper) | (edge_strength>28)
    return mask, {'paper_estimate':round(float(paper.mean()),2),
                  'paper_rgb':paper.tolist(),'paper_support':model['support'],
                  'white_threshold':float(max(220,paper.min()-7))}


def runs(bits):
    a=np.asarray(bits,dtype=np.int8)
    d=np.diff(np.r_[0,a,0])
    return list(zip(np.where(d==1)[0].tolist(),np.where(d==-1)[0].tolist()))


def _notify(checkpoint,stage,current,total,**info):
    if checkpoint:
        checkpoint(stage,current,total,**info)


def _sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def _profile(rgb):
    mask,metadata=foreground(rgb)
    a=rgb.astype(np.int16)
    edge=_edge_map(rgb)
    colored=_paper_model(rgb,colored=True)
    center=np.array(colored['rgb']) if colored['reliable'] else np.array([255,255,255])
    distance=np.max(np.abs(a-center),axis=2)
    # JPEG feather/noise tolerance is evidence for a corridor, not permission to
    # erase those pixels. Short corridors preserve a parent alternative.
    light=((a.min(2)>=238)&(np.ptp(a,axis=2)<=24))
    model_match=distance<=9
    dark=(a.min(2)<min(220,int(center.min())-15))
    soft_fraction=light.mean(1)
    model_fraction=model_match.mean(1)
    edge_fraction=(edge>55).mean(1)
    dark_fraction=dark.mean(1)
    metadata.update(separator_background=colored)
    return {'rows':mask.sum(1).astype(np.int32),
            'light_fraction':soft_fraction.astype(np.float32),
            'model_fraction':model_fraction.astype(np.float32),
            'edge_fraction':edge_fraction.astype(np.float32),
            'dark_fraction':dark_fraction.astype(np.float32)},metadata


def _cache_path(root,checksum,settings,source):
    payload={'algorithm':ALGORITHM_VERSION,'checksum':checksum,'settings':settings,
             'width':source['width'],'height':source['height']}
    key=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return Path(root)/'review'/'cache'/'profiles'/f'{key}.npz',key


def index_profiles(root,sources,checkpoint=None,algorithm_settings=None):
    """Checksum/settings-keyed strip profiles, reusable after pause/crash/resume."""
    root=Path(root)
    settings=algorithm_settings or {}
    rows=np.zeros(max(s['y1'] for s in sources),dtype=np.int32)
    stats=[]
    for i,s in enumerate(sources,1):
        path=root/s['path']
        checksum=_sha256(path)
        cached,key=_cache_path(root,checksum,settings,s)
        hit=False
        profile=None
        metadata=None
        if cached.exists():
            try:
                with np.load(cached,allow_pickle=False) as data:
                    profile={k:data[k].copy() for k in ('rows','light_fraction','model_fraction','edge_fraction','dark_fraction')}
                    metadata=json.loads(str(data['metadata'].item()))
                hit=all(len(a)==s['height'] for a in profile.values())
            except (OSError,ValueError,KeyError,json.JSONDecodeError):
                hit=False
        if not hit:
            with Image.open(path) as im:
                if im.width*im.height>80_000_000:
                    raise ValueError(f"{s['id']}: source exceeds 80 MP decode limit")
                profile,metadata=_profile(rgb_array(im))
            cached.parent.mkdir(parents=True,exist_ok=True)
            temporary=cached.with_suffix('.npz.tmp')
            try:
                with temporary.open('wb') as f:
                    np.savez_compressed(f,**profile,metadata=np.array(json.dumps(metadata,sort_keys=True)))
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary,cached)
            finally:
                temporary.unlink(missing_ok=True)
        rows[s['y0']:s['y1']]=profile['rows']
        stat={'source_id':s['id'],**metadata,'foreground_fraction':round(float(profile['rows'].sum()/(s['width']*s['height'])),5),
              'source_checksum':checksum,'algorithm_version':ALGORITHM_VERSION,'cache_key':key,
              'profile_cache':cached.relative_to(root).as_posix(),'cache_hit':bool(hit)}
        if s.get('sha256') and checksum!=s['sha256']:
            stat['warning']='SOURCE_CHECKSUM_CHANGED'
        stats.append(stat)
        _notify(checkpoint,'INDEX',i,len(sources),source_id=s['id'],source_checksum=checksum,cache_hit=hit,algorithm_version=ALGORITHM_VERSION)
    return rows,stats


def _profile_arrays(root,sources,stats):
    h=max(s['y1'] for s in sources)
    arrays={k:np.zeros(h,dtype=np.float32) for k in ('light_fraction','model_fraction','edge_fraction','dark_fraction')}
    colored=np.zeros(h,dtype=bool)
    for s,st in zip(sources,stats):
        with np.load(Path(root)/st['profile_cache'],allow_pickle=False) as data:
            for k in arrays:
                arrays[k][s['y0']:s['y1']]=data[k]
        model=st.get('separator_background',{})
        if model.get('reliable') and model.get('kind')=='colored':
            colored[s['y0']:s['y1']]=True
    arrays['colored_model']=colored
    return arrays


def join_evidence(root,sources):
    """Inspect contexts and gradients. Evidence never changes original row order."""
    joins=[]
    for left,right in zip(sources,sources[1:]):
        with Image.open(Path(root)/left['path']) as im:
            a=rgb_array(im.crop((0,max(0,im.height-160),im.width,im.height)))
        with Image.open(Path(root)/right['path']) as im:
            b=rgb_array(im.crop((0,0,im.width,min(160,im.height))))
        same_width=left['width']==right['width']
        w=min(a.shape[1],b.shape[1])
        aa,bb=a[-1,:w].astype(np.float32),b[0,:w].astype(np.float32)
        mae=float(np.abs(aa-bb).mean())
        edge_a=aa.min(1)<246
        edge_b=bb.min(1)<246
        union=np.logical_or(edge_a,edge_b).sum()
        occupancy=float(np.logical_and(edge_a,edge_b).sum()/max(1,union))
        context_a=a[-min(32,len(a)):,:w].astype(np.float32)
        context_b=b[:min(32,len(b)),:w].astype(np.float32)
        context_mae=float(np.abs(context_a.mean(0)-context_b.mean(0)).mean())
        internal=[]
        for part in (context_a,context_b):
            if len(part)>1:
                internal.append(float(np.abs(np.diff(part,axis=0)).mean()))
        internal_mae=float(np.mean(internal)) if internal else 0
        gradient_ratio=mae/max(2.0,internal_mae)
        grad_a=cv2.Sobel(cv2.cvtColor(a[:,:w],cv2.COLOR_RGB2GRAY),cv2.CV_32F,1,0)[-16:].mean(0)
        grad_b=cv2.Sobel(cv2.cvtColor(b[:,:w],cv2.COLOR_RGB2GRAY),cv2.CV_32F,1,0)[:16].mean(0)
        denom=float(np.linalg.norm(grad_a)*np.linalg.norm(grad_b))
        gradient_correlation=float(np.dot(grad_a,grad_b)/denom) if denom>1e-6 else None
        overlaps=[]
        for n in range(8,min(len(a),len(b),96)+1):
            if same_width and np.array_equal(a[-n:],b[:n]) and np.std(a[-n:])>4:
                overlaps.append(n)
        empty=bool(edge_a.mean()<.002 and edge_b.mean()<.002)
        assessment='uncertain'
        if same_width and not empty and mae<40 and context_mae<50 and gradient_ratio<5:
            assessment='probable_continuation'
        if empty:
            assessment='blank_at_seam_check_gutter'
        flags=[]
        if not same_width:
            assessment='alignment_unknown'
            flags.extend(['WIDTH_MISMATCH','ALIGNMENT_UNKNOWN_JOIN_BLOCKED'])
        if overlaps:
            flags.append('SUSPECTED_OVERLAP_ROWS_UNCHANGED')
        if not empty and (mae>70 or (gradient_ratio>7 and mae>25)):
            flags.append('DISCONTINUITY_OR_MISSING_CONTENT')
        discontinuity=bool(right.get('discontinuity_before'))
        if discontinuity:
            flags.append('MISSING_OR_EXCLUDED_SOURCE_BETWEEN_STRIPS')
        suspicious=bool(flags)
        ordinary=(not suspicious and (assessment=='probable_continuation' or empty))
        risk='suspicious' if suspicious else ('ordinary_continuation' if ordinary else 'uncertain')
        joins.append({'id':f"{left['id']}--{right['id']}",'left_source':left['id'],'right_source':right['id'],
                      'y':left['y1'],'border_rgb_mae':round(mae,3),'edge_occupancy_match':round(occupancy,4),
                      'context_rgb_mae':round(context_mae,3),'within_strip_row_mae':round(internal_mae,3),
                      'seam_gradient_ratio':round(gradient_ratio,3),
                      'horizontal_gradient_correlation':None if gradient_correlation is None else round(gradient_correlation,4),
                      'context_rows':min(160,len(a),len(b)),'assessment':assessment,'blocked':not same_width or discontinuity,
                      'risk':risk,'risk_requires_review':risk!='ordinary_continuation',
                      'geometry_operation':'unchanged_original_rows',
                      'possible_overlap_rows':max(overlaps,default=0),'flags':flags,'status':'UNVERIFIED',
                      'operation':'exact_row_append_no_adjustment' if same_width and not discontinuity else 'blocked_pending_alignment_or_order_review'})
    return joins


def tight_bounds(chapter,bounds,padding=4):
    x0,y0,x1,y1=chapter.validate(bounds)
    left,top,right,bottom=x1,y1,x0,y0
    content=False
    for _,a,piece in chapter.pieces(bounds):
        mask,_=foreground(rgb_array(piece))
        ys,xs=np.nonzero(mask)
        if len(xs):
            content=True
            left,right=min(left,x0+int(xs.min())),max(right,x0+int(xs.max())+1)
            top,bottom=min(top,a+int(ys.min())),max(bottom,a+int(ys.max())+1)
    if not content:
        return None
    return [max(x0,left-padding),max(y0,top-padding),min(x1,right+padding),min(y1,bottom+padding)]


def _component_boxes(mask,min_area_fraction=.018):
    # Two scales retain small protrusions while testing island stability.
    boxes=[]
    area=mask.size
    for kernel_size in (3,7):
        kernel=np.ones((kernel_size,kernel_size),np.uint8)
        connected=cv2.morphologyEx(mask.astype(np.uint8),cv2.MORPH_CLOSE,kernel)
        n,labels,stats,_=cv2.connectedComponentsWithStats(connected,8)
        current=[]
        for label,st in enumerate(stats[1:],1):
            x,y,w,h,count=map(int,st)
            if count>=max(220,int(area*min_area_fraction)) and w>=max(24,mask.shape[1]*.13) and h>=35:
                current.append({'box':[x,y,x+w,y+h],'area':count,'label':label,'scale':kernel_size})
        if len(current)>=2:
            boxes=current
            break
    return boxes



def _outlined_balloon(rgb):
    """Closed light balloon hypothesis; never reads or removes original text."""
    mask,_=foreground(rgb)
    contours,hierarchy=cv2.findContours(mask.astype(np.uint8),cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None or mask.mean()>.52:
        return {'outlined_balloon':False}
    area=mask.size
    hierarchy=np.asarray(hierarchy).reshape(-1,4)
    for i,contour in enumerate(contours):
        outside=float(cv2.contourArea(contour))
        if not area*.15<=outside<=area*.96:
            continue
        x,y,w,h=cv2.boundingRect(contour)
        if w<rgb.shape[1]*.42 or h<rgb.shape[0]*.42:
            continue
        children=[]
        child=int(hierarchy[i,2])
        while child>=0:
            children.append(float(cv2.contourArea(contours[child])))
            child=int(hierarchy[child,0])
        hole=max(children,default=0)
        if hole<max(area*.06,outside*.20):
            continue
        approx=cv2.approxPolyDP(contour,max(1,cv2.arcLength(contour,True)*.012),True)
        # An ordinary rectangular line-art inset is not a speech balloon.
        if len(approx)==4 and outside/(w*h)>.88:
            continue
        return {'outlined_balloon':True,'outer_fraction':round(outside/area,4),
                'interior_hole_fraction':round(hole/max(1,outside),4),
                'contour_vertices':len(approx),'semantic_meaning':'UNRESOLVED'}
    return {'outlined_balloon':False}


def _single_enclosing_frame(rgb):
    """Protect a complete outer composition from spurious interior rectangles.

    A wall seam, glasses, stairs or a weapon may form Hough rectangles. A large
    containing frame with no empty corridor through its interior is stronger
    evidence for retaining one composition. This recommendation never verifies
    that composition or removes the possibility of later manual splitting.
    """
    h,w=rgb.shape[:2]
    if min(h,w)<65:
        return None
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    edges=cv2.Canny(gray,70,160)
    lines=cv2.HoughLinesP(edges,1,np.pi/180,threshold=35,
                          minLineLength=max(35,min(w,h)//7),maxLineGap=10)
    if lines is None:
        return None
    horizontal=[];vertical=[]
    for xa,ya,xb,yb in np.asarray(lines).reshape(-1,4):
        xa,ya,xb,yb=map(int,(xa,ya,xb,yb))
        if abs(xa-xb)<=2 and abs(ya-yb)>=h*.25:
            vertical.append([round((xa+xb)/2),min(ya,yb),max(ya,yb)])
        if abs(ya-yb)<=2 and abs(xa-xb)>=w*.25:
            horizontal.append([round((ya+yb)/2),min(xa,xb),max(xa,xb)])
    # Include the exact image edge only when the original is full bleed there.
    # This is boundary evidence, not permission to add or extrapolate pixels.
    def cluster(values):
        groups=[]
        for value in sorted(values):
            if groups and value-groups[-1][-1]<=5:
                groups[-1].append(value)
            else:
                groups.append([value])
        return [int(round(np.median(g))) for g in groups]
    vertical=sorted(vertical,key=lambda v:v[2]-v[1],reverse=True)[:16]
    horizontal=sorted(horizontal,key=lambda v:v[2]-v[1],reverse=True)[:18]
    xs=cluster([v[0] for v in vertical])
    # A balloon can cover the entire visible top edge; a long side's end
    # still supplies its original corner ordinate.
    ys=cluster([v[0] for v in horizontal]+[v[1] for v in vertical]+[v[2] for v in vertical])
    support=cv2.dilate((edges>0).astype(np.uint8),np.ones((5,5),np.uint8))
    candidates=[]
    for left in xs:
        if left>w*.37:
            continue
        for right in xs:
            if right< w*.76 or right-left<w*.60:
                continue
            for top in ys:
                if top>h*.35:
                    continue
                for bottom in ys:
                    if bottom<h*.76 or bottom-top<h*.60:
                        continue
                    area=(right-left)*(bottom-top)
                    if area<w*h*.45:
                        continue
                    fractions=[float(support[top:bottom+1,left].mean()),float(support[top:bottom+1,right].mean()),
                               float(support[top,left:right+1].mean()),float(support[bottom,left:right+1].mean())]
                    if sum(v>=.80 for v in fractions)<2 or sum(fractions)<2.50 or min(fractions)<.10:
                        continue
                    candidates.append((area,left,top,right,bottom,fractions))
    # Examine the largest geometrically supported enclosing frame first. Each
    # local bitmap is analyzed at most a few times, never once per Hough pair.
    for area,left,top,right,bottom,fractions in sorted(candidates,reverse=True)[:12]:
        inset=max(4,round(min(right-left,bottom-top)*.015))
        interior=rgb[top+inset:bottom-inset,left+inset:right-inset]
        if interior.size==0:
            continue
        native=interior.astype(np.int16)
        near_white=(native.min(2)>=238)&(np.ptp(native,axis=2)<=24)
        separators=[]
        for axis in (0,1):
            # Sparse JPEG speckles are retained original pixels, but must not
            # conceal an otherwise empty narrow corridor from grouping logic.
            center=np.median(native,axis=axis).astype(np.int16)
            expanded=center[None,:,:] if axis==0 else center[:,None,:]
            uniform=(np.max(np.abs(native-expanded),axis=2)<=9).mean(axis=axis)>=.99
            light_color=(center.mean(1)>=190)&(center.min(1)>=165)
            paper=(near_white.mean(axis=axis)>=.99)|(uniform&light_color)
            for a,b in runs(paper):
                if b-a<6 or a<=5 or b>=len(paper)-5:
                    continue
                before=paper[max(0,a-70):a]
                after=paper[b:min(len(paper),b+70)]
                if len(before) and len(after) and (1-before.mean())>.25 and (1-after.mean())>.25:
                    separators.append([axis,int(a),int(b)])
        if separators:
            continue
        return {'type':'single_enclosing_frame_with_attached_text',
                'frame_bounds_preview':[left,top,right+1,bottom+1],
                'side_edge_support':[round(v,3) for v in fractions],
                'frame_area_fraction':round(area/(w*h),4),
                'interior_separator_count':0,'requires_review':True,
                'semantic_completeness':'UNRESOLVED'}
    return None


def _composition_evidence(chapter,bounds):
    if bounds[3]-bounds[1]>MAX_WINDOW_HEIGHT:
        return None
    with chapter.preview(bounds,max_size=MAX_ANALYSIS_DIMENSION) as preview:
        return _single_enclosing_frame(rgb_array(preview))

def _frame_boxes(rgb):
    """Long near-axis borders find inset/staggered layouts, as alternatives only."""
    h,w=rgb.shape[:2]
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    edges=cv2.Canny(gray,70,160)
    if (edges>0).mean()>.28:
        return []  # Dense texture has spurious Hough rectangles, not reliable frames.
    lines=cv2.HoughLinesP(edges,1,np.pi/180,threshold=45,minLineLength=max(45,min(w,h)//6),maxLineGap=8)
    if lines is None:
        return []
    vertical=[]
    horizontal=[]
    for x0,y0,x1,y1 in np.asarray(lines).reshape(-1,4):
        x0,y0,x1,y1=map(int,(x0,y0,x1,y1))
        if abs(x1-x0)<=2 and abs(y1-y0)>=max(45,h*.08):
            vertical.append([round((x0+x1)/2),min(y0,y1),max(y0,y1)])
        if abs(y1-y0)<=2 and abs(x1-x0)>=max(45,w*.18):
            horizontal.append([round((y0+y1)/2),min(x0,x1),max(x0,x1)])
    vertical=sorted(vertical,key=lambda p:p[2]-p[1],reverse=True)[:22]
    horizontal=sorted(horizontal,key=lambda p:p[2]-p[1],reverse=True)[:25]
    found=[]
    for left in vertical:
        for right in vertical:
            x0,x1=left[0],right[0]
            if x1-x0<max(55,w*.20):
                continue
            tops=[p[0] for p in horizontal if p[1]<=x0+10 and p[2]>=x1-10 and abs(p[0]-max(left[1],right[1]))<14]
            bottoms=[p[0] for p in horizontal if p[1]<=x0+10 and p[2]>=x1-10 and abs(p[0]-min(left[2],right[2]))<14]
            for y0 in tops:
                for y1 in bottoms:
                    if y1-y0<max(70,h*.15) or (x1-x0)*(y1-y0)<w*h*.07:
                        continue
                    box=[max(0,x0),max(0,y0),min(w,x1+1),min(h,y1+1)]
                    if not any(_iou(box,b)>.8 for b in found):
                        found.append(box)
    # Speech balloons and SFX can interrupt two sides of a printed frame.
    # Test a small graph of long-line coordinates rather than requiring four
    # uninterrupted corners. These are review hypotheses, never destructive
    # crop decisions.
    if len(found)<2 and len(vertical)>=2 and len(horizontal)>=2:
        def clusters(values):
            groups=[]
            for value in sorted(values):
                if groups and value-groups[-1][-1]<=6:
                    groups[-1].append(value)
                else:
                    groups.append([value])
            return [int(round(np.median(g))) for g in groups]
        xs=clusters([v[0] for v in vertical[:16]])
        ys=clusters([v[0] for v in horizontal[:18]])
        support=cv2.dilate((edges>0).astype(np.uint8),np.ones((5,5),np.uint8))
        for xi,x0 in enumerate(xs):
            for x1 in xs[xi+1:]:
                if x1-x0<max(55,w*.20):
                    continue
                for yi,y0 in enumerate(ys):
                    for y1 in ys[yi+1:]:
                        if y1-y0<max(70,h*.15) or (x1-x0)*(y1-y0)<w*h*.07:
                            continue
                        fractions=[float(support[y0:y1+1,x0].mean()),float(support[y0:y1+1,x1].mean()),
                                   float(support[y0,x0:x1+1].mean()),float(support[y1,x0:x1+1].mean())]
                        if min(fractions)<.18 or sum(fractions)<2.4 or max(fractions[:2])<.65 or max(fractions[2:])<.65:
                            continue
                        box=[max(0,x0-2),max(0,y0-2),min(w,x1+3),min(h,y1+3)]
                        if not any(_iou(box,b)>.8 for b in found):
                            found.append(box)
    # Full-width bands inside one drawing (title bars, perspective stripes,
    # clothing, windows) are not evidence for two different illustrations. This
    # interrupted-frame fallback is reserved for genuinely staggered x extents.
    # Vertically stacked scenes have the separate corridor/island hypotheses.
    selected=[]
    for i,a in enumerate(found):
        for b in found[i+1:]:
            overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))
            width_a=a[2]-a[0];width_b=b[2]-b[0]
            different_x=(overlap/max(1,min(width_a,width_b))<.85 and
                         abs((a[0]+a[2])-(b[0]+b[2]))/2>w*.10 and
                         min(width_a,width_b)<w*.90)
            if different_x:
                for box in (a,b):
                    if box not in selected:
                        selected.append(box)
    return sorted(selected,key=lambda b:(b[2]-b[0])*(b[3]-b[1]),reverse=True)[:8]


def _iou(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    area_a=(a[2]-a[0])*(a[3]-a[1]);area_b=(b[2]-b[0])*(b[3]-b[1])
    return inter/max(1,area_a+area_b-inter)


def _local_islands(chapter,bounds,padding):
    """Bounded 2-D windows: never allocate the whole continuous chapter."""
    x0,y0,x1,y1=bounds
    nodes=[]
    frame_nodes=[]
    step=MAX_WINDOW_HEIGHT-256
    for start in range(y0,y1,step):
        end=min(y1,start+MAX_WINDOW_HEIGHT)
        with chapter.preview([x0,start,x1,end],max_size=MAX_ANALYSIS_DIMENSION) as im:
            rgb=rgb_array(im)
        if _single_enclosing_frame(rgb):
            return []
        mask,_=foreground(rgb)
        model=_paper_model(rgb,colored=True)
        if model['reliable'] and model['kind']=='colored':
            # Only the alternate analysis mask sees colored exterior paper.
            distance=np.max(np.abs(rgb.astype(np.int16)-np.array(model['rgb'])),axis=2)
            alternate=(distance>10)|(_edge_map(rgb)>28)
            if .025<alternate.mean()<.9:
                mask=alternate
        sx=(x1-x0)/mask.shape[1];sy=(end-start)/mask.shape[0]
        boxes=_component_boxes(mask)
        # Preserve small lettering/lines near a large island by assigning their
        # components to the nearest island; uncertain assignments stay flagged.
        n,labels,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
        bases=[p['box'][:] for p in boxes]
        for st in stats[1:]:
            xx,yy,ww,hh,count=map(int,st)
            if count<3 or not bases:
                continue
            if any(b[0]<=xx and b[1]<=yy and b[2]>=xx+ww and b[3]>=yy+hh for b in bases):
                continue
            distances=[]
            for b in bases:
                dx=max(b[0]-(xx+ww),xx-b[2],0);dy=max(b[1]-(yy+hh),yy-b[3],0)
                distances.append((dx*dx+dy*dy)**.5)
            nearest=int(np.argmin(distances))
            if distances[nearest]<=max(25,mask.shape[1]*.08):
                b=bases[nearest]
                b[:]=[min(b[0],xx),min(b[1],yy),max(b[2],xx+ww),max(b[3],yy+hh)]
        for box in bases:
            box=[max(x0,x0+int(box[0]*sx)-padding),max(y0,start+int(box[1]*sy)-padding),
                 min(x1,x0+int(np.ceil(box[2]*sx))+padding),min(y1,start+int(np.ceil(box[3]*sy))+padding)]
            if not any(_iou(box,n['bounds'])>.7 for n in nodes):
                nodes.append({'bounds':box,'evidence':'stable_connected_content_island_multiscale'})
        if len(bases)<2:
            frames=_frame_boxes(rgb)
            frames=[b for b in frames if any(b is not q and abs(b[0]-q[0])+abs(b[2]-q[2])>rgb.shape[1]*.30 and _iou(b,q)<.65 for q in frames)]
            # Attach nearby original ink to its nearest frame for a conservative
            # child bound. The uncut parent protects ambiguous lettering and
            # overlapping areas; children explicitly require visual correction.
            if len(frames)>=2:
                yy,xx=np.ogrid[:mask.shape[0],:mask.shape[1]]
                best=np.full(mask.shape,np.inf,dtype=np.float32)
                owner=np.full(mask.shape,-1,dtype=np.int16)
                for fi,box in enumerate(frames):
                    dx=np.maximum(np.maximum(box[0]-xx,xx-(box[2]-1)),0)
                    dy=np.maximum(np.maximum(box[1]-yy,yy-(box[3]-1)),0)
                    dist=(dx*dx+dy*dy).astype(np.float32)
                    choose=dist<best
                    best[choose]=dist[choose];owner[choose]=fi
                expanded=[]
                reach=max(45,mask.shape[1]*.22)
                for fi,box in enumerate(frames):
                    ys0,xs0=np.nonzero(mask&(owner==fi)&(best<=reach*reach))
                    if len(xs0):
                        box=[min(box[0],int(xs0.min())),min(box[1],int(ys0.min())),
                             max(box[2],int(xs0.max())+1),max(box[3],int(ys0.max())+1)]
                    expanded.append(box)
                frames=expanded
            for box in frames:
                mapped=[max(x0,x0+int(box[0]*sx)-padding),max(y0,start+int(box[1]*sy)-padding),
                        min(x1,x0+int(np.ceil(box[2]*sx))+padding),min(y1,start+int(np.ceil(box[3]*sy))+padding)]
                if not any(_iou(mapped,n['bounds'])>.75 for n in frame_nodes):
                    frame_nodes.append({'bounds':mapped,'evidence':'interrupted_panel_borders_in_compound'})
        if end==y1:
            break
    # Window-edge islands are not offered as pieces of long single artwork.
    # Only a bounded candidate, or nodes proven not to touch window truncation,
    # can create child alternatives. Cross-strip drawings remain uncut.
    if y1-y0>MAX_WINDOW_HEIGHT:
        return []
    nodes=[n for n in nodes if (n['bounds'][2]-n['bounds'][0])*(n['bounds'][3]-n['bounds'][1])<(x1-x0)*(y1-y0)*.92]
    if len(nodes)>=2:
        return nodes[:12]
    independent=[]
    for n in frame_nodes:
        b=n['bounds']
        if (b[2]-b[0])*(b[3]-b[1])>(x1-x0)*(y1-y0)*.88:
            continue
        if not any(_iou(b,q['bounds'])>.7 for q in independent):
            independent.append(n)
    return independent[:8] if len(independent)>=2 else []


def _proposal(bounds,**extra):
    return {'bounds':list(map(int,bounds)),'detection_reason':'v3_multiscale_chapter_regions',
            'automatic_classification':'uncertain','proposal_role':'primary','recommended_active':True,
            'algorithm_version':ALGORITHM_VERSION,**extra}


def _make_group(bounds,children,kind,number,recommended_children=False):
    key=f'G{number:05d}_{bounds[1]}_{bounds[3]}'
    parent=_proposal(bounds,proposal_role='parent',group_key=key,recommended_active=not recommended_children,
                     automatic_classification='compound_layout',ambiguity_flags=['GROUPING_REQUIRES_REVIEW',kind],
                     relation_evidence={'type':kind,'children':len(children),'preserves_full_parent':True})
    output=[parent]
    for i,child in enumerate(children,1):
        b=child['bounds'] if isinstance(child,dict) else child
        evidence=child.get('evidence',kind) if isinstance(child,dict) else kind
        output.append(_proposal(b,proposal_role='child',group_key=key,parent_group_key=key,
                                alternative_index=i,recommended_active=recommended_children,
                                ambiguity_flags=['CHILD_BOUNDARIES_REQUIRE_REVIEW']+(['RECTANGULAR_CHILD_MAY_INCLUDE_ADJACENT_ART'] if 'border' in evidence else []),
                                relation_evidence={'type':evidence,'parent_bounds':bounds,'not_semantic_proof':True}))
    return output



def _floating_text_attachments(chapter,ranges,padding):
    """Retain a possible floating balloon/SFX with its neighboring illustration.

    A light outlined or sparse text island is not an independent scene merely
    because a 6–100 px paper gap separates it from the drawing. The resulting
    union stays an active parent, with reversible separate alternatives.
    """
    bound_cache={}
    descriptor_cache={}
    def bounds(i):
        if i not in bound_cache:
            a,b=ranges[i]
            bound_cache[i]=tight_bounds(chapter,[0,a,chapter.width,b],padding)
        return bound_cache[i]
    def descriptor(i):
        if i not in descriptor_cache:
            box=bounds(i)
            if box is None:
                descriptor_cache[i]=None
            else:
                with chapter.preview(box,max_size=600) as im:
                    rgb=rgb_array(im)
                mask,_=foreground(rgb)
                a=rgb.astype(np.int16)
                colorful=((np.ptp(a,axis=2)>22)&(a.min(2)<240)&mask).sum()/max(1,mask.sum())
                descriptor_cache[i]={'ink':float(mask.mean()),'colorful':float(colorful),**_outlined_balloon(rgb)}
        return descriptor_cache[i]
    links=[]
    max_gap=min(160,max(12,int(chapter.width*.15)))
    for i in range(len(ranges)-1):
        gap=ranges[i+1][0]-ranges[i][1]
        if not 0<=gap<=max_gap:
            continue
        one,two=bounds(i),bounds(i+1)
        if one is None or two is None:
            continue
        for small_index,art_index,small,art in ((i,i+1,one,two),(i+1,i,two,one)):
            sw,sh=small[2]-small[0],small[3]-small[1]
            aw,ah=art[2]-art[0],art[3]-art[1]
            overlap=max(0,min(small[2],art[2])-max(small[0],art[0]))
            if sh>chapter.width*.65 or sw>chapter.width*.75 or aw<sw*1.25 or ah<max(sh*1.2,100) or overlap<sw*.55:
                continue
            sd,ad=descriptor(small_index),descriptor(art_index)
            if sd and ad and sd['ink']<.45 and sd['colorful']<.30 and ad['ink']>.38:
                links.append((gap,i,small_index,art_index))
    chosen={}
    used=set()
    for gap,index,small_index,art_index in sorted(links):
        if index in used or index+1 in used:
            continue
        chosen[index]=(small_index,art_index)
        used.update((index,index+1))
    attached=[]
    evidence={}
    index=0
    while index<len(ranges):
        if index in chosen:
            small_index,art_index=chosen[index]
            union=(ranges[index][0],ranges[index+1][1])
            evidence[union]={'type':'floating_bubble_or_lettering_near_illustration',
                             'small_bounds':bounds(small_index),'illustration_bounds':bounds(art_index),
                             'gap_bounds':[ranges[index][1],ranges[index+1][0]],
                             'relationship_requires_review':True,
                             'balloon_evidence':descriptor(small_index)}
            attached.append(union)
            index+=2
        else:
            attached.append(ranges[index]);index+=1
    return attached,evidence

def propose(root,sources,min_gutter=36,padding=4,vertical=True,checkpoint=None):
    chapter=Chapter(root,sources)
    settings={'min_gutter':int(min_gutter),'padding':int(padding),'vertical':bool(vertical)}
    rows,stats=index_profiles(root,sources,checkpoint=checkpoint,algorithm_settings=settings)
    arrays=_profile_arrays(root,sources,stats)
    # Strict corridors remain the highest-confidence geometry. Near-white noise
    # and colored separators generate explicit alternatives below.
    strict=(rows==0)
    neutral=(arrays['light_fraction']>=.994)&(arrays['dark_fraction']<=.0015)
    colored=(arrays['colored_model'])&(arrays['model_fraction']>=.994)&(arrays['dark_fraction']<=.0015)
    soft=neutral|colored
    strict_gutters=[(a,b) for a,b in runs(strict) if b-a>=min_gutter]
    # Off-white/JPEG corridors that are long and genuinely low-ink can be used
    # as geometry, but retain their explicit type and all pixels in originals.
    soft_long=[]  # Noisy/color-only corridors retain an uncut parent alternative.
    cuts=sorted(strict_gutters+soft_long)
    merged=[]
    for a,b in cuts:
        if merged and a<=merged[-1][1]:
            merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    blocked=[s['y1'] for s,t in zip(sources,sources[1:]) if s['width']!=t['width'] or t.get('discontinuity_before')]
    ranges=[]
    start=0
    for a,b in merged:
        if a>start and rows[start:a].any():
            ranges.append((start,a))
        start=max(start,b)
    if start<chapter.height and rows[start:].any():
        ranges.append((start,chapter.height))
    # Dialogue is optional; source bounds remain available in the manifest.
    attachment_evidence={}  # Artwork-first: never enlarge bounds to attach dialogue.
    # Unknown-width alignment is an actual join stop, independent of gutters.
    split_ranges=[]
    for a,b in ranges:
        points=[a]+[y for y in blocked if a<y<b]+[b]
        split_ranges.extend(zip(points,points[1:]))
    proposals=[]
    gutters=[{'y0':a,'y1':b,'kind':'strict_or_long_neutral_corridor','requires_review':True} for a,b in merged]
    for index,(a,b) in enumerate(split_ranges,1):
        if b-a>MAX_WINDOW_HEIGHT*2:
            from .source_tasks import make
            proposals.append(make([0,a,chapter.width,b],arrays,sources))
            _notify(checkpoint,'SEGMENT',index,len(split_ranges),candidate_count=len(proposals))
            continue
        bound=tight_bounds(chapter,[0,a,chapter.width,b],padding)
        if bound is None:
            continue
        evidence={'preceding_empty_rows':next((d-c for c,d in merged if d==a),0),
                  'following_empty_rows':next((d-c for c,d in merged if c==b),0)}
        soft_cuts=[]
        for c,d in runs(soft[a:b]):
            c,d=a+c,a+d
            if d-c>=6 and c>=bound[1]+8 and d<=bound[3]-8:
                # A light/colored band must have real content on both sides;
                # no arbitrary fixed-height panels and no source-boundary cuts.
                if rows[max(a,c-100):c].sum()>100 and rows[d:min(b,d+100)].sum()>100:
                    soft_cuts.append((c,d))
        children=[]
        if soft_cuts:
            last=bound[1]
            for c,d in soft_cuts:
                child=tight_bounds(chapter,[bound[0],last,bound[2],c],padding)
                if child:
                    children.append({'bounds':child,'evidence':'short_noisy_or_colored_corridor'})
                last=d
                gutters.append({'y0':c,'y1':d,'kind':'short_or_colored_alternative_corridor','requires_review':True})
            child=tight_bounds(chapter,[bound[0],last,bound[2],bound[3]],padding)
            if child:
                children.append({'bounds':child,'evidence':'short_noisy_or_colored_corridor'})
        if vertical and (a,b) not in attachment_evidence:
            columns=np.zeros(bound[2]-bound[0],dtype=np.int64)
            for _,_,piece in chapter.pieces(bound):
                cm,_=foreground(rgb_array(piece));columns+=cm.sum(0)
            cg=[(x,y) for x,y in runs(columns==0) if y-x>=max(24,min_gutter) and x>0 and y<len(columns)]
            if cg:
                last=bound[0];direct=[]
                for x,y in cg:
                    cb=tight_bounds(chapter,[last,bound[1],bound[0]+x,bound[3]],padding)
                    if cb:direct.append(cb)
                    last=bound[0]+y
                cb=tight_bounds(chapter,[last,bound[1],bound[2],bound[3]],padding)
                if cb:direct.append(cb)
                if len(direct)>1:
                    proposals.extend(_proposal(cb,column_split=True,boundary_evidence=evidence,ambiguity_flags=['SIDE_BY_SIDE_READING_ORDER_REQUIRES_REVIEW']) for cb in direct)
                    continue
        # Regional islands can refine several staggered artworks even when a
        # whole-row corridor alone would split only one section from the rest.
        composition=_composition_evidence(chapter,bound)
        regional_islands=_local_islands(chapter,bound,padding) if vertical and not composition else []
        if len(regional_islands)>max(1,len(children)):
            children=regional_islands
        if composition and (a,b) not in attachment_evidence:
            proposals.append(_proposal(bound,boundary_evidence=evidence,
                                       recommended_classification='illustration',composition_evidence=composition))
        elif len(children)>=2:
            group_kind=('FLOATING_BUBBLE_ATTACHMENT_ALTERNATIVES' if (a,b) in attachment_evidence else
                        ('SHORT_OR_COLORED_CORRIDOR_ALTERNATIVES' if soft_cuts else 'SPATIAL_ISLAND_OR_BORDER_ALTERNATIVES'))
            attachment=attachment_evidence.get((a,b))
            if attachment and attachment.get('balloon_evidence',{}).get('outlined_balloon'):
                # One drawing plus its floating balloon is a complete primary
                # composition. Preserve split hypotheses as reversible metadata,
                # not a false unresolved multi-illustration group.
                proposals.append(_proposal(bound,boundary_evidence=evidence,
                    recommended_classification='illustration',
                    composition_evidence={'type':'illustration_with_nearby_outlined_balloon',
                                          'attachment':attachment,'requires_review':True,
                                          'semantic_completeness':'UNRESOLVED'},
                    alternative_bounds=[child['bounds'] for child in children],
                    ambiguity_flags=['ATTACHED_BALLOON_RELATIONSHIP_REQUIRES_REVIEW']))
            else:
                group=_make_group(bound,children,group_kind,len(proposals)+1)
                group[0]['boundary_evidence']=evidence
                if attachment:
                    group[0]['relation_evidence']['attachment']=attachment
                    group[0]['ambiguity_flags'].append('POSSIBLE_FLOATING_BUBBLE_BELONGS_TO_ILLUSTRATION')
                proposals.extend(group)
        else:
            # A completely empty full-height column corridor is stronger than a
            # component guess and can directly separate side-by-side panels.
            column_children=[]
            if vertical:
                columns=np.zeros(bound[2]-bound[0],dtype=np.int64)
                for _,_,piece in chapter.pieces(bound):
                    mask,_=foreground(rgb_array(piece))
                    columns+=mask.sum(0)
                gaps=[(x,y) for x,y in runs(columns==0) if y-x>=max(6,min_gutter//2) and x>0 and y<len(columns)]
                last=bound[0]
                for x,y in gaps:
                    child=tight_bounds(chapter,[last,bound[1],bound[0]+x,bound[3]],padding)
                    if child:
                        column_children.append(child)
                    last=bound[0]+y
                if gaps:
                    child=tight_bounds(chapter,[last,bound[1],bound[2],bound[3]],padding)
                    if child:
                        column_children.append(child)
            if len(column_children)>=2:
                for child in column_children:
                    proposals.append(_proposal(child,column_split=True,boundary_evidence=evidence,
                                               ambiguity_flags=['SIDE_BY_SIDE_READING_ORDER_REQUIRES_REVIEW']))
                _notify(checkpoint,'SEGMENT',index,len(split_ranges),candidate_count=len(proposals),chapter_y0=a,chapter_y1=b)
                continue
            islands=regional_islands
            if len(islands)>=2:
                group=_make_group(bound,islands,'SPATIAL_ISLAND_OR_BORDER_ALTERNATIVES',len(proposals)+1)
                group[0]['boundary_evidence']=evidence
                proposals.extend(group)
            else:
                proposals.append(_proposal(bound,boundary_evidence=evidence))
        if a in blocked or b in blocked:
            for p in proposals:
                if p['bounds'][1]>=a and p['bounds'][3]<=b:
                    p.setdefault('ambiguity_flags',[]).append('ALIGNMENT_UNKNOWN_JOIN_BLOCKED')
        _notify(checkpoint,'SEGMENT',index,len(split_ranges),candidate_count=len(proposals),chapter_y0=a,chapter_y1=b)
    from .artwork import refine
    return refine(chapter,proposals,padding),stats,gutters



def _classification_from_rgb(rgb):
    """Weak routing evidence; visual geometry does not prove text semantics."""
    mask,_=foreground(rgb)
    ink=float(mask.mean())
    a=rgb.astype(np.int16)
    colored=(np.ptp(a,axis=2)>22)&(a.min(2)<240)&mask
    colored_ink=float(colored.sum()/max(1,mask.sum()))
    n,_,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    components=[st for st in stats[1:] if st[cv2.CC_STAT_AREA]>=4]
    small_repeated=(len(components)>=3 and np.median([st[cv2.CC_STAT_HEIGHT] for st in components])<rgb.shape[0]*.55)
    monochrome=colored_ink<.035
    sparse=ink<.30
    values=rgb[mask]
    if len(values):
        sample=values[::max(1,len(values)//4000)]//16
        _,counts=np.unique(sample,axis=0,return_counts=True)
        palette_dominance=float(counts.max()/len(sample))
    else:
        palette_dominance=0.0
    text_possible=monochrome or sparse or (ink<.75 and palette_dominance>.60)
    evidence={'method':'weak_color_sparsity_and_component_geometry_no_ocr',
              'foreground_fraction':round(ink,4),'colored_foreground_fraction':round(colored_ink,4),
              'component_count':len(components),'dominant_ink_color_fraction':round(palette_dominance,4),'small_repeated_components':bool(small_repeated),
              'semantic_completeness':'UNRESOLVED','classification_requires_review':True,
              'never_delete_text':True}
    if text_possible:
        # B/W artwork, colored lettering and narrative text cannot be reliably
        # distinguished here. Keep all candidates rather than false SFX labels.
        return {'classification':'uncertain',
                'classification_candidates':['illustration','embedded_sfx_or_bubble','standalone_sfx',
                                             'dialogue_or_narration_only','title_or_credits'],
                'evidence':evidence}
    return {'classification':'illustration',
            'classification_candidates':['illustration','embedded_sfx_or_bubble'],
            'evidence':evidence}


def classify_region(chapter,bounds):
    """Recoverable classification suggestion; never grants approval/deletion."""
    with chapter.preview(bounds,max_size=900) as preview:
        return _classification_from_rgb(rgb_array(preview))

def assess(chapter,bounds,joins):
    x0,y0,x1,y1=chapter.validate(bounds)
    flags=['AUTOMATIC_BOUNDARIES_REQUIRE_REVIEW']
    regions=chapter.regions(bounds)
    crossed=[j for j in joins if y0<j['y']<y1]
    if crossed:
        flags.append('UNVERIFIED_JOIN')
        for j in crossed:
            flags.extend(j.get('flags',[]))
        if any(j.get('blocked') for j in crossed):
            flags.append('ALIGNMENT_UNKNOWN_JOIN_BLOCKED')
    w,h=x1-x0,y1-y0
    if min(w,h)<80 or w*h<18000:
        flags.append('SMALL_OR_NARROW_REGION')
    if h>w*4:
        flags.append('TALL_ARTWORK_PRESERVED')
    if h>w*12:
        flags.append('POSSIBLE_MULTIPLE_PANELS_NO_CLEAR_GUTTER')
    with chapter.preview(bounds,max_size=1400) as preview:
        preview_rgb=rgb_array(preview)
        mask,_=foreground(preview_rgb)
        classification_suggestion=_classification_from_rgb(preview_rgb)
    content=float(mask.mean())
    boundary=float(np.r_[mask[0],mask[-1],mask[:,0],mask[:,-1]].mean())
    if boundary>.015:
        flags.append('BOUNDARY_INTERSECTS_CONTENT_OR_SOURCE_EDGE')
    band=max(1,min(mask.shape)//12)
    bands=[mask[:band],mask[-band:],mask[:,:band],mask[:,-band:]]
    if sum(float(b.mean())<.002 for b in bands)>=2:
        flags.append('POSSIBLE_LARGE_BLANK_MARGINS')
    if content<.10:
        flags.append('SPARSE_REGION_POSSIBLE_TEXT_ONLY')
    n,_,component_stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    if n>5 and content<.28 and np.median(component_stats[1:,cv2.CC_STAT_HEIGHT])<max(25,mask.shape[0]*.08):
        flags.append('POSSIBLE_TEXT_ONLY_CLASSIFY_IN_REVIEW')
    overlapping=[s for s in chapter.sources if max(y0,s['y0'])<min(y1,s['y1'])]
    if any(s['width']<x1 for s in overlapping):
        flags.append('MIXED_WIDTH_TRANSPARENT_PADDING_NOT_ORIGINAL_ART')
    if len({s.get('mode') for s in overlapping})>1:
        flags.append('MIXED_SOURCE_COLOR_MODES_CHECK_DISPLAY')
    internal=[]
    for _,a,piece in chapter.pieces(bounds):
        rgb=rgb_array(piece).astype(np.int16)
        paper=(rgb.min(2)>=238)&(np.ptp(rgb,axis=2)<=24)
        for c,d in runs(paper.mean(1)>=.994):
            c,d=a+c,a+d
            if d-c>=3 and c>y0+5 and d<y1-5:
                internal.append([c,d])
    if internal:
        flags.append('POSSIBLE_INTERNAL_GUTTER')
    candidates=[st for st in component_stats[1:]
                if st[cv2.CC_STAT_AREA]>=max(800,mask.size*.05)
                and st[cv2.CC_STAT_WIDTH]>=mask.shape[1]*.18
                and st[cv2.CC_STAT_HEIGHT]>=mask.shape[0]*.12]
    boxes=[[int(st[0]),int(st[1]),int(st[0]+st[2]),int(st[1]+st[3])] for st in candidates]
    if len(candidates)>1:
        flags.append('COMPOUND_PANEL_LAYOUT_REQUIRES_REVIEW')
    score=max(.1,.88-.10*len(flags[1:]))
    return {'flags':list(dict.fromkeys(flags)),'confidence':round(score,2),
            'metrics':{'foreground_fraction':round(content,4),'boundary_content_fraction':round(boundary,4)},
            'qa_checks':{'panel_boundaries':'UNRESOLVED','joining':'UNRESOLVED' if crossed else 'NOT_APPLICABLE',
                         'classification':'UNRESOLVED','human_verification':'NOT_VERIFIED'},
            'classification_suggestion':classification_suggestion['classification'],
            'classification_candidates':classification_suggestion['classification_candidates'],
            'classification_evidence':classification_suggestion['evidence'],
            'internal_gutter_candidates':internal,'large_component_boxes_preview':boxes,
            'source_regions':regions,'join_ids':[j['id'] for j in crossed]}
