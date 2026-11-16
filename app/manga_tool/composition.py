"""Streaming chapter composition graph. Native pixels are never altered.

Color seeds discover painted regions; neutral ink remains coverage/review
material, not a proven text label. Window components stitch by shared pixels,
never by the mere presence of a source seam. Rectangle envelopes are proposals.
"""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from .chapter import Chapter

SCALE=4


def backgrounds(root,sources):
    samples=[]
    for s in sources:
        with Image.open(Path(root)/s['path']) as im:
            a=np.asarray(im.convert('RGB').resize((173,320)))
        flat=a.std(axis=1).max(axis=1)<3.5
        if flat.any():samples.append(np.median(a[flat],axis=1))
    if not samples:return [np.array([252,252,252]),np.array([4,4,4])]
    a=np.concatenate(samples).astype(np.uint8)
    colors,counts=np.unique(a//8,axis=0,return_counts=True)
    candidates=[]
    for i in np.argsort(counts)[::-1]:
        c=colors[i].astype(np.int16)*8+4
        if counts[i]<len(a)*.003:break
        if c.max()<110 or c.min()>235:candidates.append(c)
        if len(candidates)>=12:break
    return candidates


def paint_mask(rgb,colors):
    a=rgb.astype(np.int16)
    exterior=(a.min(2)>242)
    for c in colors:exterior|=np.max(np.abs(a-c),axis=2)<15
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    edge=np.abs(cv2.Sobel(gray,cv2.CV_32F,1,0))+np.abs(cv2.Sobel(gray,cv2.CV_32F,0,1))
    return cv2.morphologyEx(((~exterior)|(edge>35)).astype(np.uint8),cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))


def discover(root,sources,checkpoint=None,padding=4):
    chapter=Chapter(root,sources)
    colors=backgrounds(root,sources)
    width=max(1,round(chapter.width/SCALE));window=4096;overlap=1024
    parent={};nodes={};previous=None;offset=0;serial=0
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(a,b):
        a,b=find(a),find(b)
        if a==b:return
        parent[b]=a
        x=nodes[a];y=nodes[b]
        x[:]=[min(x[0],y[0]),min(x[1],y[1]),max(x[2],y[2]),max(x[3],y[3]),x[4]+y[4]]
    blocked=[t['y0'] for s,t in zip(sources,sources[1:]) if t.get('discontinuity_before') or s['width']!=t['width']]
    # Never stitch across missing content or unverified width alignment.
    spans=[0]+blocked+[chapter.height]
    for lo,hi in zip(spans,spans[1:]):
        previous=None
        for start in range(lo,hi,window-overlap):
            end=min(hi,start+window)
            with chapter.preview([0,start,chapter.width,end],max_size=window) as im:
                small=im.convert('RGB').resize((width,max(1,(end-start)//SCALE)),Image.Resampling.BOX)
                rgb=np.asarray(small);small.close()
            mask=paint_mask(rgb,colors)
            n,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
            local=np.zeros(n,np.int32)
            sx=chapter.width/width;sy=(end-start)/mask.shape[0]
            for i,(x,y,w,h,area) in enumerate(stats[1:],1):
                if area<4:continue
                serial+=1;parent[serial]=serial;local[i]=serial
                nodes[serial]=[int(x*sx),start+int(y*sy),min(chapter.width,int(np.ceil((x+w)*sx))),min(hi,start+int(np.ceil((y+h)*sy))),int(area)]
            ids=local[labels]
            if previous is not None:
                common=min(previous.shape[0],ids.shape[0])
                pairs=np.stack([previous[-common:].ravel(),ids[:common].ravel()],axis=1)
                pairs=pairs[(pairs[:,0]>0)&(pairs[:,1]>0)]
                if len(pairs):
                    pairs,counts=np.unique(pairs,axis=0,return_counts=True)
                    for (a,b),count in zip(pairs,counts):
                        if count>=3:union(int(a),int(b))
            previous=ids[-min(overlap//SCALE,len(ids)):].copy()
            if checkpoint:checkpoint('SEGMENT',end,chapter.height,method='streaming_shared_pixel_component_graph')
            if end==hi:break
    boxes=[nodes[i][:] for i in nodes if find(i)==i and nodes[i][4]>=200 and nodes[i][2]-nodes[i][0]>=40 and nodes[i][3]-nodes[i][1]>=45]
    # Spatial islands inside one frame overlap as rectangular envelopes. Merge
    # those before classification, rather than creating object/letter closeups.
    # Independent diagonal panels can have overlapping rectangular envelopes.
    # Overlap alone is not a merge edge. Contained small fragments are metadata,
    # not additional object/word PNG alternatives.
    boxes=sorted(boxes,key=lambda b:(b[2]-b[0])*(b[3]-b[1]),reverse=True)
    kept=[]
    for box in boxes:
        if any(p[0]<=box[0]+4 and p[1]<=box[1]+4 and p[2]>=box[2]-4 and p[3]>=box[3]-4 for p in kept):
            continue
        kept.append(box)
    boxes=kept
    # Preserve detached colored details close to a much larger painted scene.
    # White-backed lettering is ineligible. Store the attachment as uncertainty,
    # not proof that the fragment is an independent finished panel.
    boxes=sorted(boxes,key=lambda b:b[4],reverse=True)
    attached=set()
    for j,b in enumerate(boxes):
        for i,a in enumerate(boxes[:j]):
            if i in attached or b[4]>.04*a[4]:continue
            dx=max(a[0]-b[2],b[0]-a[2],0);dy=max(a[1]-b[3],b[1]-a[3],0)
            if max(dx,dy)>chapter.width*.12:continue
            with chapter.preview(b[:4],max_size=250) as fragment:
                arr=np.asarray(fragment.convert('RGB'))
            if (arr.min(2)>238).mean()>.30:continue
            vals=arr[(np.ptp(arr.astype(np.int16),axis=2)>=16)&(arr.min(2)<242)]
            if len(vals):
                _,counts=np.unique(vals//24,axis=0,return_counts=True)
                probabilities=counts/counts.sum();ent=float(-np.sum(probabilities*np.log2(probabilities)))
                ev=text_group_evidence(arr,ent)
                if ev.get('sparse_unclassified') or ev.get('text_only') or (len(vals)/arr.shape[0]/arr.shape[1]<.12 and ent<3.2):continue
            boxes[i]=[min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3]),a[4]+b[4]]
            attached.add(j);break
    boxes=[b for i,b in enumerate(boxes) if i not in attached]
    proposals=[]
    for candidate_index,box in enumerate(sorted(boxes,key=lambda b:(b[1],b[0])),1):
        if checkpoint:checkpoint('CLASSIFY',candidate_index,len(boxes),method='composition_quality_gate')
        bound=[max(0,box[0]-padding),max(0,box[1]-padding),min(chapter.width,box[2]+padding),min(chapter.height,box[3]+padding)]
        with chapter.preview(bound,max_size=900) as im:rgb=np.asarray(im.convert('RGB'))
        color=(np.ptp(rgb.astype(np.int16),axis=2)>=16)&(rgb.min(2)<242)
        vals=rgb[color]
        if len(vals):
            vals=vals[::max(1,len(vals)//12000)]//24
            _,counts=np.unique(vals,axis=0,return_counts=True)
            dominance=float(counts.max()/counts.sum());entropy=float(-np.sum(counts/counts.sum()*np.log2(counts/counts.sum())))
        else:dominance=1;entropy=0
        # Weak flat-color lettering is not certified as artwork. Its original
        # location is retained for source review, never silently discarded.
        white_fraction=float((rgb.min(2)>238).mean())
        uncertain=bool(((entropy<3.0 or dominance>.64) and white_fraction>.55) or box[4]<500 or color.mean()<.035 or (color.mean()<.12 and entropy<3.2))
        text_evidence=text_group_evidence(rgb,entropy)
        if text_evidence['text_only'] or text_evidence.get('sparse_unclassified'):uncertain=True
        original_bound=list(bound)
        if not uncertain and color.any():
            # Tighten only when a substantial straight frame side supports the
            # painted envelope. Otherwise retain neutral/monochrome context.
            yy,xx=np.nonzero(color)
            cb=[int(xx.min()),int(yy.min()),int(xx.max())+1,int(yy.max())+1]
            edges=cv2.Canny(cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY),60,160)
            lines=cv2.HoughLinesP(edges,1,np.pi/180,threshold=50,minLineLength=max(50,int(rgb.shape[0]*.42)),maxLineGap=8)
            supported=False
            if lines is not None:
                supported=any(abs(int(x)-int(x1))<=2 and abs(int(y)-int(y1))>rgb.shape[0]*.42 and
                              min(abs(int(x)-cb[0]),abs(int(x)-cb[2]))<8
                              for x,y,x1,y1 in lines.reshape(-1,4))
            balloon=False
            dark=(cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)<150).astype(np.uint8)
            contours,hierarchy=cv2.findContours(dark,cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
            if hierarchy is not None:
                for contour,relation in zip(contours,hierarchy[0]):
                    if relation[3]<0 or cv2.contourArea(contour)<rgb.shape[0]*rgb.shape[1]*.045:continue
                    x,y,w,h=cv2.boundingRect(contour)
                    interior=rgb[y:y+h,x:x+w]
                    if (interior.min(2)>235).mean()>.65 and (x<cb[0]-6 or y<cb[1]-6):
                        balloon=True;break
            if supported and balloon:
                sx=(bound[2]-bound[0])/rgb.shape[1];sy=(bound[3]-bound[1])/rgb.shape[0]
                trim=[max(bound[0],bound[0]+int(cb[0]*sx)-padding),max(bound[1],bound[1]+int(cb[1]*sy)-padding),
                      min(bound[2],bound[0]+int(np.ceil(cb[2]*sx))+padding),min(bound[3],bound[1]+int(np.ceil(cb[3]*sy))+padding)]
                # Preserve every non-neutral painted island, not just a face.
                bound=trim
        proposals.append({'bounds':bound,'proposal_role':'primary','recommended_classification':'dialogue_or_narration_only' if text_evidence['text_only'] else ('uncertain' if uncertain else 'illustration'),
            'metadata_only':uncertain,'review_task':uncertain,'detection_reason':'streaming_painted_composition_graph',
            'ambiguity_flags':['COMPOSITION_REQUIRES_VISUAL_REVIEW']+(['FLAT_COLOR_TEXT_OR_ART_UNRESOLVED'] if uncertain else []),
            'text_evidence':text_evidence,'composition_graph':{'original_envelope':original_bound,'rectangular_frame_supported_trim':bound!=original_bound,'seed_area':box[4],'color_entropy':round(entropy,4),'dominant_color_fraction':round(dominance,4),'semantic_proof':False,'native_rectangle_limitation':True}})
    art=[p for p in proposals if not p['metadata_only']]
    pending=[p for p in proposals if p['metadata_only']]
    grouped=[]
    for p in sorted(pending,key=lambda p:p['bounds'][1]):
        b=p['bounds'];match=None
        for q in grouped:
            a=q['bounds'];dx=max(a[0]-b[2],b[0]-a[2],0);dy=max(a[1]-b[3],b[1]-a[3],0)
            if dx<80 and dy<56 and max(a[3],b[3])-min(a[1],b[1])<1100:
                match=q;break
        if match:
            a=match['bounds'];match['bounds']=[min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])]
        else:grouped.append(p)
    for i,p in enumerate(art):
        a=p['bounds']
        for q in art[i+1:]:
            b=q['bounds']
            if abs(a[1]-b[1])<8 and (a[2]<b[0] or b[2]<a[0]):
                p['column_split']=q['column_split']=True
    proposals=art+grouped
    return proposals,{'background_colors':[c.tolist() for c in colors],'analysis_scale':SCALE,'window_rows':window,'shared_pixel_stitching':True}


def text_group_evidence(rgb,entropy):
    """Secondary OCR on low-diversity groups with a stable exterior canvas."""
    import shutil,subprocess,io,csv
    if entropy>4.2:
        return {'text_only':False,'method':'not_confirmed'}
    samples=np.concatenate([rgb[:3].reshape(-1,3),rgb[-3:].reshape(-1,3),rgb[:,:3].reshape(-1,3),rgb[:,-3:].reshape(-1,3)])
    bins,counts=np.unique(samples//12,axis=0,return_counts=True)
    color=bins[counts.argmax()].astype(np.int16)*12+6
    stable=(np.max(np.abs(samples.astype(np.int16)-color),axis=1)<18).mean()
    if stable<.35:return {'text_only':False,'method':'unstable_exterior'}
    material=np.max(np.abs(rgb.astype(np.int16)-color),axis=2)>20
    if material.sum()<40:return {'text_only':False,'method':'no_material'}
    if not shutil.which('tesseract'):
        return {'text_only':False,'sparse_unclassified':bool(stable>.65 and material.mean()<.35),'method':'OCR_UNAVAILABLE_GEOMETRY_ONLY','exterior_support':float(stable),'material_fraction':float(material.mean()),'semantic_proof':False}
    buf=io.BytesIO();Image.fromarray(rgb).save(buf,format='PNG')
    words=[];covered=np.zeros(material.shape,bool)
    try:
        result=subprocess.run(['tesseract','stdin','stdout','--psm','6','tsv'],input=buf.getvalue(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8,check=True)
        for r in csv.DictReader(io.StringIO(result.stdout.decode('utf-8','replace')),delimiter='\t'):
            text=r.get('text','').strip()
            if len(text)<2 or float(r.get('conf',-1))<65 or not any(c.isalpha() for c in text):continue
            x,y,w,h=[int(r[k]) for k in ('left','top','width','height')]
            covered[max(0,y-3):min(rgb.shape[0],y+h+3),max(0,x-3):min(rgb.shape[1],x+w+3)]=True
            words.append({'text':text,'confidence':float(r['conf'])})
    except (OSError,subprocess.SubprocessError):
        return {'text_only':False,'method':'ocr_failed_or_timed_out'}
    fraction=float((covered&material).sum()/material.sum())
    return {'text_only':bool(words and fraction>.94),'sparse_unclassified':bool((stable>.65 and material.mean()<.35) or (len(words)>=8 and entropy<3.1)),'exterior_support':float(stable),'material_fraction':float(material.mean()),'method':'group_ocr_and_exterior_material','words':words,'material_explained':fraction,'semantic_proof':False}

