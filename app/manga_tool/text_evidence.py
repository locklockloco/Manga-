"""Optional bounded Tesseract evidence. Analysis masks never edit source pixels."""
import csv
import io
import shutil
import subprocess
import cv2
import numpy as np
from PIL import Image


def inspect(rgb):
    from .detection import foreground
    mask,_=foreground(rgb)
    evidence={'engine':'tesseract','available':bool(shutil.which('tesseract')),
              'words':[],'balloons':[],'semantic_proof':False}
    if mask.mean()>.70:
        evidence['reason']='DENSE_CONTENT_NOT_SUBMITTED_TO_OCR'
        return mask,evidence
    if not evidence['available']:
        evidence['reason']='OCR_NOT_INSTALLED'
        return mask,evidence
    buf=io.BytesIO();Image.fromarray(rgb).save(buf,format='PNG')
    try:
        result=subprocess.run(['tesseract','stdin','stdout','--psm','11','tsv'],input=buf.getvalue(),
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=12,check=True)
    except (OSError,subprocess.SubprocessError) as exc:
        evidence['reason']='OCR_FAILED_OR_TIMED_OUT';evidence['error']=type(exc).__name__
        return mask,evidence
    textmask=np.zeros(mask.shape,np.uint8)
    for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8','replace')),delimiter='\t'):
        text=row.get('text','').strip()
        if len(text)<2 or not any(c.isalpha() for c in text) or float(row.get('conf',-1))<65:
            continue
        x,y,w,h=[int(row[k]) for k in ('left','top','width','height')]
        box=[max(0,x-2),max(0,y-2),min(mask.shape[1],x+w+2),min(mask.shape[0],y+h+2)]
        textmask[box[1]:box[3],box[0]:box[2]]=1
        evidence['words'].append({'text':text,'confidence':float(row['conf']),'box':box})
    # Sparse closed light envelopes containing confirmed words are balloon
    # hypotheses. Rectangular narration frames are also optional external text.
    contours,_=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    cleaned=mask.copy()
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if w*h<400 or not evidence['words']:
            continue
        inside=[r for r in evidence['words'] if x<r['box'][0] and y<r['box'][1] and
                x+w>r['box'][2] and y+h>r['box'][3]]
        if not inside:
            continue
        fill=np.zeros(mask.shape,np.uint8);cv2.drawContours(fill,[contour],-1,1,-1)
        interior=cv2.erode(fill,np.ones((7,7),np.uint8))>0
        if interior.sum()<200 or mask[interior].mean()>.20:
            continue
        unexplained=interior&mask&~textmask.astype(bool)
        if unexplained.sum()>max(15,interior.sum()*.015):
            continue
        cleaned[fill>0]=False
        evidence['balloons'].append([x,y,x+w,y+h])
    # Only remove lettering geometrically separated from remaining drawing.
    # Internal text is preserved by the final rectangular extraction regardless.
    residual=cleaned&~textmask.astype(bool)
    explained=1-float(residual.sum()/max(1,mask.sum()))
    evidence['text_explained_ink_fraction']=round(explained,4)
    evidence['standalone_text']=bool(evidence['words'] and explained>=.94)
    evidence['geometry_limitation']=bool(evidence['words'] and not evidence['balloons'] and mask.mean()>.20)
    if evidence['balloons']:
        return residual,evidence
    return mask,evidence
