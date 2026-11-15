"""Compare real reviewed compositions without changing frozen annotations."""
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from manga_tool.pipeline import load_manifest,font,pasted_thumb

def iou(a,b):
    n=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]));return n/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-n)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('before');ap.add_argument('after');ap.add_argument('gold');ap.add_argument('--ids',nargs='+',required=True);args=ap.parse_args()
    roots=[Path(args.before),Path(args.after)];ms=[load_manifest(r) for r in roots];gold=json.loads(Path(args.gold).read_text());out=roots[1]/'contact_sheets';out.mkdir(exist_ok=True);proof=[]
    for off in range(0,len(args.ids),2):
        page=Image.new('RGB',(1400,1600),'#e9edf2');d=ImageDraw.Draw(page);d.text((22,15),'REAL LEVIATHAN | previous hypotheses → revised proposals',fill='#18304c',font=font(25))
        for row,id in enumerate(args.ids[off:off+2]):
            g=next(g for g in gold['regions'] if g['id']==id);entry={'gold_id':id,'gold_bounds':g['bounds'],'versions':[]};y=65+row*750
            d.text((25,y),id+' | original composition, natural proportions',fill='#18304c',font=font(20))
            for col,(root,m) in enumerate(zip(roots,ms)):
                p=max([p for p in m['panels'] if p.get('status') not in {'ALTERNATIVE','SUPERSEDED','MERGED','REJECTED'}],key=lambda p:iou(p['bounds'],g['bounds']))
                im=Image.open(root/p['file']).convert('RGB');draw=ImageDraw.Draw(im)
                if col==0:
                    for cid in p.get('children',[]):
                        child=next(q for q in m['panels'] if q['id']==cid);b=child['bounds'];c=p['bounds'];draw.rectangle([b[0]-c[0],b[1]-c[1],b[2]-c[0]-1,b[3]-c[1]-1],outline='#f44336',width=3)
                x=col*700
                d.text((x+25,y+34),('BEFORE: ' if col==0 else 'AFTER: ')+p['classification'],fill='#9d3926' if col==0 else '#21554d',font=font(19))
                d.text((x+25,y+61),p['id']+' | '+p['status'],fill='#43516a',font=font(15))
                pasted_thumb(page,im,(x+20,y+93,x+680,y+695))
                entry['versions'].append({'region_id':p['id'],'classification':p['classification'],'status':p['status'],'bounds':p['bounds'],'source_regions':p['source_regions'],'file':p['file'],'sha256':p['sha256']})
            proof.append(entry)
        d.text((22,1560),'Red boxes = previous split hypotheses. All new images remain unapproved; no pixels were invented or repainted.',fill='#43516a',font=font(16))
        page.save(out/f'recall_before_after_{off//2+1:02d}.png')
    (out/'recall_source_maps.json').write_text(json.dumps(proof,indent=2))
if __name__=='__main__':main()
