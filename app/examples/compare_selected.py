"""Make selected before/after evidence sheets; no changes to artwork or manifest."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from manga_tool.pipeline import load_manifest,font,pasted_thumb
from manga_tool.chapter import Chapter


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--ids',nargs='+',required=True)
    parser.add_argument('--prefix',default='acceptance')
    args=parser.parse_args()
    root=Path(args.output)
    m=load_manifest(root)
    reader=Chapter(root,m['sources'])
    for offset in range(0,len(args.ids),2):
        page=Image.new('RGB',(1600,1450),'#e8edf3')
        draw=ImageDraw.Draw(page)
        draw.text((20,12),f"{m['chapter']} | original source rows → natural-proportion artwork",fill='#172b43',font=font(24))
        for i,id in enumerate(args.ids[offset:offset+2]):
            p=next(p for p in m['panels'] if p['id']==id)
            sourceids={r['source_id'] for r in p['source_regions']}
            sources=[s for s in m['sources'] if s['id'] in sourceids]
            context=[0,min(s['y0'] for s in sources),reader.width,max(s['y1'] for s in sources)]
            im=reader.preview(context,max_size=1500).convert('RGB')
            d=ImageDraw.Draw(im)
            sx,sy=im.width/reader.width,im.height/(context[3]-context[1])
            x0,y0,x1,y1=p['bounds']
            d.rectangle((x0*sx,(y0-context[1])*sy,x1*sx-1,(y1-context[1])*sy-1),outline='#e25242',width=3)
            for s in sources[:-1]:
                y=(s['y1']-context[1])*sy
                d.line((0,y,im.width,y),fill='#24b6db',width=3)
                d.text((4,max(0,y-22)),f"{s['id']} end",fill='#24b6db',font=font(16))
            x=i*800
            draw.text((x+18,58),f"{id} | {p['status']}",fill='#172b43',font=font(22))
            draw.text((x+18,90),'Sources '+', '.join(str(s['order']) for s in sources),fill='#44556b',font=font(18))
            draw.text((x+18,124),'BEFORE: complete source strips',fill='#44556b',font=font(15))
            draw.text((x+420,124),f"AFTER: {p['dimensions'][0]} × {p['dimensions'][1]}",fill='#44556b',font=font(15))
            pasted_thumb(page,im,(x+15,160,x+390,1370))
            pasted_thumb(page,reader.preview(p['bounds'],max_size=1500),(x+415,160,x+790,1370))
        draw.text((18,1410),'Red = crop bounds. Cyan = source seam. Display previews resize; delivered PNGs retain original pixels.',fill='#44556b',font=font(18))
        dest=root/'contact_sheets'/f'{args.prefix}_{offset//2+1:02d}.png'
        page.save(dest)
        print(dest)


if __name__=='__main__':
    main()
