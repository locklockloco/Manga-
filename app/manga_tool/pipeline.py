"""Persistent proposals, provenance, review operations, and lossless exports."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import csv
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from PIL import Image, ImageDraw, ImageFont
from .chapter import Chapter
from .detection import assess, propose, join_evidence
from .importer import import_chapter
from .storage import Store, writer_lock

_LOCK = threading.RLock()
ACTIVE = {'NEEDS_REVIEW','VERIFIED','REJECTED','ALTERNATIVE'}
CATEGORIES = {'illustration','embedded_sfx_or_bubble','standalone_sfx','dialogue_or_narration_only','title_or_credits','compound_layout','uncertain'}
_ALIASES={'text_only':'dialogue_or_narration_only','title_card':'title_or_credits'}
_DEPTH=threading.local()


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp,path)


@contextmanager
def writing(root):
    root=Path(root).resolve()
    with _LOCK:
        held=getattr(_DEPTH,'roots',set())
        if str(root) in held:
            yield
            return
        with writer_lock(root):
            _DEPTH.roots=held|{str(root)}
            try:
                yield
            finally:
                _DEPTH.roots=held


def load_manifest(output_dir):
    root=Path(output_dir)
    if (root/'review/state.sqlite3').exists():
        saved=Store(root).load_manifest()
        if saved is not None:
            return saved
    return json.loads((root/'reports/manifest.json').read_text(encoding='utf-8'))


def panel_sort_key(manifest,p):
    x = p['bounds'][0] if manifest.get('options',{}).get('reading_direction','ltr') == 'ltr' else -p['bounds'][2]
    return (p.get('manual_order',p['bounds'][1]), x)


def save_manifest(output_dir, manifest, operation="review_edit", project=True):
    root = Path(output_dir)
    for order,p in enumerate(sorted((p for p in manifest['panels'] if p['status'] in ACTIVE),key=lambda p:panel_sort_key(manifest,p)),1):
        p['order'] = order
    manifest['updated_at'] = now()
    manifest['version']=3
    store=Store(root)
    previous=store.load_manifest()
    store.commit_manifest(manifest,operation=operation,before=previous if operation!='checkpoint' else None)
    atomic_json(root/'reports'/'manifest.json',manifest)
    csvpath = root/'reports'/'panels.csv'
    tmp = csvpath.with_suffix('.csv.tmp')
    columns = ['id','order','status','classification','file','bounds','source_ids','source_regions','join_ids','reconstruction_group','confidence','flags','sha256','review']
    with tmp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        for p in manifest['panels']:
            row = {k:p.get(k,'') for k in columns}
            row['source_ids'] = [r['source_id'] for r in p.get('source_regions',[])]
            for k in columns:
                if isinstance(row[k],(list,dict)):
                    row[k]=json.dumps(row[k],ensure_ascii=False)
            writer.writerow(row)
    os.replace(tmp,csvpath)
    if project:
        sync_outputs(root,manifest)


def audit(m,operation,**details):
    m.setdefault('history',[]).append({'at':now(),'operation':operation,**details})


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def human_verified(panel):
    review=panel.get('review',{})
    return (panel.get('status')=='VERIFIED' and panel.get('classification','illustration')=='illustration'
            and review.get('method')=='explicit_human_visual_review' and review.get('reviewer') in {'user','human'}
            and review.get('artwork_checked') is True)


def output_folder(panel):
    if panel['status']=='REJECTED':
        return 'needs_review/rejected'
    if human_verified(panel):
        return 'approved_artwork'
    category=_ALIASES.get(panel.get('classification'),panel.get('classification','illustration'))
    if panel.get('status')=='ALTERNATIVE':
        return 'needs_review/alternatives'
    if category in {'compound_layout','uncertain','standalone_sfx','dialogue_or_narration_only','title_or_credits'}:
        return 'needs_review/'+category
    return 'proposals'


def _projection_directory_fsync(directory):
    """Flush directory entries where supported (Windows may reject directory FDs)."""
    try:
        fd=os.open(str(directory),os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        if os.name!='nt':
            raise


def _projection_target(root,relative):
    """Projection metadata cannot authorize writes/deletes in original/history paths."""
    relative=Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] not in {'proposals','approved_artwork','needs_review'}:
        raise ValueError(f'Invalid projected artwork path: {relative}')
    target=root/relative
    if not target.resolve().is_relative_to(root):
        raise ValueError(f'Projected artwork path escapes the workspace: {relative}')
    return target


def _cleanup_projection_temporaries(root):
    # Called only with the writer lock. A killed writer cannot leave an active
    # staging operation, so recognizable projection temp files are safe to remove.
    changed=set()
    for folder in ('proposals','approved_artwork','needs_review'):
        directory=root/folder
        if directory.exists() and directory.resolve().is_relative_to(root):
            for path in directory.rglob('*.tmp'):
                if not path.parent.resolve().is_relative_to(root):
                    continue
                if re.fullmatch(r'.+\.png\.[0-9a-f]{32}\.tmp',path.name):
                    path.unlink(missing_ok=True)
                    changed.add(path.parent)
    review=root/'review'
    if review.exists() and review.resolve().is_relative_to(root):
        for path in review.glob('projections.json*.tmp'):
            if path.name=='projections.json.tmp' or re.fullmatch(r'projections\.json\.[0-9a-f]{32}\.tmp',path.name):
                path.unlink(missing_ok=True)
                changed.add(path.parent)
    for directory in changed:
        _projection_directory_fsync(directory)


def _commit_projection_marker(marker,desired):
    marker.parent.mkdir(parents=True,exist_ok=True)
    temporary=marker.with_name(marker.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as handle:
            json.dump(desired,handle,indent=2,ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,marker)
        _projection_directory_fsync(marker.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_outputs(root,manifest):
    """Verify and repair named copies from checksum-validated immutable revisions.

    projections.json describes desired names, never proves a destination is
    intact. Every call checks actual revision and destination bytes; timestamps,
    file size and an unchanged marker cannot skip integrity validation.
    """
    root=Path(root).resolve()
    with writing(root):
        _cleanup_projection_temporaries(root)
        marker=root/'review/projections.json'
        try:
            old=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
            if not isinstance(old,dict):
                old={}
        except (ValueError,UnicodeError):
            old={}
        desired={}
        copies=[]
        # Validate every authoritative revision before changing any projection.
        for panel in manifest.get('panels',[]):
            if panel.get('metadata_only') or panel.get('status') not in ACTIVE or not panel.get('file'):
                continue
            expected=panel.get('sha256','')
            if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected):
                raise ValueError(f"Missing/invalid immutable revision checksum for {panel.get('id','unknown panel')}")
            relative=panel.get('output_file') or f"{output_folder(panel)}/{panel['id']}.png"
            target=_projection_target(root,relative)
            revision=root/panel['file']
            if not revision.resolve().is_relative_to(root) or revision.resolve()==target.resolve():
                raise ValueError(f"Invalid immutable revision path for {panel.get('id','unknown panel')}: {panel['file']}")
            try:
                actual_revision=digest(revision)
            except OSError as exc:
                raise ValueError(f"Immutable revision is missing/unreadable for {panel.get('id','unknown panel')}: {panel['file']}") from exc
            if actual_revision!=expected:
                raise ValueError(f"Immutable revision checksum mismatch for {panel.get('id','unknown panel')}: {panel['file']}; existing artwork copies were preserved")
            if relative in desired and desired[relative]!=expected:
                raise ValueError(f'Conflicting projected artwork name: {relative}')
            desired[relative]=expected
            copies.append((revision,target,expected))
        repaired=0
        for revision,target,expected in copies:
            try:
                intact=target.is_file() and digest(target)==expected
            except OSError:
                intact=False
            if intact:
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                shutil.copyfile(revision,temporary)
                with temporary.open('r+b') as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                if digest(temporary)!=expected:
                    raise ValueError(f'Projection staging checksum mismatch: {revision.relative_to(root)}; destination preserved')
                os.replace(temporary,target)
                _projection_directory_fsync(target.parent)
                repaired+=1
            finally:
                temporary.unlink(missing_ok=True)
        for relative in old.keys()-desired.keys():
            # Only generated copies can be removed. Original/revision/history
            # files are never deletion candidates, even with malformed metadata.
            try:
                target=_projection_target(root,relative)
            except (ValueError,TypeError):
                continue
            if target.exists():
                target.unlink()
                _projection_directory_fsync(target.parent)
        _commit_projection_marker(marker,desired)
        return {'checked':len(copies),'repaired':repaired,'intact':len(copies)-repaired}


def export_panel(output_dir, manifest, panel, prepared_file=None):
    root=Path(output_dir)
    chapter=Chapter(root,manifest['sources'])
    chapter.validate(panel['bounds'])
    if panel.get('review_task'):
        panel.update(metadata_only=True,status='SOURCE_REVIEW',active=False)
        panel['source_regions']=chapter.regions(panel['bounds'])
        panel.pop('output_file',None)
        return panel
    if panel.get('classification') in {'standalone_sfx','dialogue_or_narration_only'}:
        panel.update(metadata_only=True,status='IGNORED',active=False)
        panel['source_regions']=chapter.regions(panel['bounds'])
        panel.pop('output_file',None)
        return panel
    for join in manifest.get('joins',[]):
        if join.get('blocked') and panel['bounds'][1] < join['y'] < panel['bounds'][3]:
            raise ValueError('Join alignment/order is unresolved; export across this source boundary is blocked')
    geometry=json.dumps({'bounds':panel['bounds'],'polygon':panel.get('polygon'),'sources':[s['sha256'] for s in manifest['sources'] if max(s['y0'],panel['bounds'][1])<min(s['y1'],panel['bounds'][3])]},sort_keys=True)
    key=hashlib.sha256(geometry.encode()).hexdigest()[:14]
    rel=f"review/revisions/{panel['id']}_r{panel.get('revision',1):04d}_{key}.png"
    target=root/rel
    if not target.exists():
        if prepared_file is None:
            chapter.save_png(panel['bounds'],target,polygon=panel.get('polygon'))
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            os.replace(prepared_file,target)
    panel['file']=rel
    panel['source_regions']=chapter.regions(panel['bounds'])
    panel['sha256']=digest(target)
    panel['dimensions']=[panel['bounds'][2]-panel['bounds'][0],panel['bounds'][3]-panel['bounds'][1]]
    panel['output_file']=f"{output_folder(panel)}/{panel['id']}.png"
    panel['reconstruction_group']=panel['id']+'_R' if len(panel['source_regions'])>1 else None
    panel.pop('reconstructed_file',None)
    if human_verified(panel) and len(panel['source_regions'])>1 and panel['review'].get('joins_checked'):
        # The immutable original is already the confirmed full reconstruction.
        panel['reconstructed_file']=rel
    return panel


def flag_duplicates(manifest):
    groups={}
    for p in manifest['panels']:
        p['flags']=[f for f in p.get('flags',[]) if f!='DUPLICATE_CROP']
        if p['status'] in ACTIVE and p.get('sha256'):
            groups.setdefault(p['sha256'],[]).append(p)
    for group in groups.values():
        if len(group)>1:
            for p in group:
                p['flags'].append('DUPLICATE_CROP')


def process_chapter(input_path,output_dir,chapter='CH01',include_all=False,min_gutter=12,padding=4,vertical=True,reading_direction='ltr',generate_contact_sheets=True,preflight=False):
    from .runner import run_chapter
    return run_chapter(input_path,output_dir,chapter=chapter,include_all=include_all,min_gutter=min_gutter,padding=padding,vertical=vertical,reading_direction=reading_direction,generate_contact_sheets=generate_contact_sheets,preflight=preflight)


def font(size=16):
    for name in ('DejaVuSans.ttf','Arial.ttf'):
        try:
            return ImageFont.truetype(name,size)
        except OSError:
            pass
    return ImageFont.load_default()


def pasted_thumb(canvas, im, box):
    im=im.convert('RGB')
    im.thumbnail((box[2]-box[0],box[3]-box[1]),Image.Resampling.LANCZOS)
    canvas.paste(im,(box[0]+(box[2]-box[0]-im.width)//2,box[1]+(box[3]-box[1]-im.height)//2))


def generate_sheets(output_dir,manifest):
    root=Path(output_dir)
    reader=Chapter(root,manifest['sources'])
    panels=sorted([p for p in manifest['panels'] if p['status'] in ACTIVE],key=lambda p:panel_sort_key(manifest,p))
    (root/'contact_sheets').mkdir(exist_ok=True)
    # Old surplus pages must not masquerade as current results after merges.
    for old in (root/'contact_sheets').glob('*.png'):
        if re.fullmatch(r'(contact|comparison)_\d+\.png',old.name):
            old.unlink()
    for offset in range(0,len(panels),12):
        page=Image.new('RGB',(1200,1170),'#e6eaf0')
        draw=ImageDraw.Draw(page)
        draw.text((18,12),f"{manifest['chapter']} | review overview — inspect each region",fill='#142236',font=font(20))
        for i,p in enumerate(panels[offset:offset+12]):
            col,row=i%4,i//4
            x,y=col*300+10,row*370+50
            draw.rectangle((x,y,x+280,y+350),fill='white')
            preview=reader.preview(p['bounds'],max_size=700)
            pasted_thumb(page,preview,(x+8,y+38,x+272,y+308))
            draw.text((x+8,y+8),p['id'],fill='#152538',font=font(17))
            draw.text((x+8,y+314),f"{p['status']} | {p.get('dimensions',[p['bounds'][2]-p['bounds'][0],p['bounds'][3]-p['bounds'][1]])[0]}x{p.get('dimensions',[p['bounds'][2]-p['bounds'][0],p['bounds'][3]-p['bounds'][1]])[1]}",fill='#8b3a16',font=font(12))
            draw.text((x+8,y+333),f"{p.get('classification','illustration')} | {','.join(r['source_id'] for r in p['source_regions'])}",fill='#42516a',font=font(11))
        page.save(root/'contact_sheets'/f'contact_{offset//12+1:03d}.png')
    for offset in range(0,len(panels),4):
        page=Image.new('RGB',(1200,1530),'#e6eaf0')
        draw=ImageDraw.Draw(page)
        draw.text((20,10),'SOURCE CONTEXT + proposed bounds                       LOSSLESS CROP',fill='#142236',font=font(19))
        for i,p in enumerate(panels[offset:offset+4]):
            x0,y0,x1,y1=p['bounds']
            context=[0,max(0,y0-100),reader.width,min(reader.height,y1+100)]
            im=reader.preview(context,max_size=1100).convert('RGB')
            d=ImageDraw.Draw(im)
            sx,sy=im.width/reader.width,im.height/(context[3]-context[1])
            d.rectangle((x0*sx,(y0-context[1])*sy,max(x0*sx+1,x1*sx-1),max(1,(y1-context[1])*sy-1)),outline='#e3483c',width=3)
            for s in reader.sources:
                if context[1]<s['y1']<context[3]:
                    yy=(s['y1']-context[1])*sy
                    d.line((0,yy,im.width,yy),fill='#2285db',width=2)
                    d.text((4,yy+3),s['id']+' end',fill='#2285db',font=font(12))
            y=50+i*365
            draw.text((16,y),f"{p['id']} | {p['status']} | red=cut, blue=source seam",fill='#142236',font=font(15))
            pasted_thumb(page,im,(20,y+28,580,y+346))
            pasted_thumb(page,reader.preview(p['bounds'],max_size=800),(620,y+28,1180,y+346))
        page.save(root/'contact_sheets'/f'comparison_{offset//4+1:03d}.png')

    manifest['contact_sheets_stale']=False
    save_manifest(root,manifest,operation="checkpoint")


def get_panel(m,panel_id):
    for p in m['panels']:
        if p['id']==panel_id and (p['status'] in ACTIVE or p['status']=='SOURCE_REVIEW'):
            return p
    raise ValueError('Active panel not found')


def finish(root,m):
    flag_duplicates(m)
    m['contact_sheets_stale']=True
    m['qa_stale']=True
    save_manifest(root,m)
    return m


def update_panel(output_dir,panel_id,bounds=None,status=None,checked=False,joins_checked=False,reviewer='user',note='',polygon=None):
    root=Path(output_dir)
    with writing(root):
        m=load_manifest(root)
        p=get_panel(m,panel_id)
        before=json.loads(json.dumps(p))
        reader=Chapter(root,m['sources'])
        if polygon is not None:
            if not isinstance(polygon,list) or (polygon and not 3<=len(polygon)<=1024):
                raise ValueError('Polygon must have 3–1024 chapter-coordinate vertices')
            p['polygon']=polygon or None
            p['status']='NEEDS_REVIEW'
            p['review']={}
        if bounds is not None:
            if p.get('review_task'):
                if list(bounds)==p['bounds']:
                    raise ValueError('Choose a complete illustration range before exporting this source task')
                p.update(review_task=False,metadata_only=False,active=True)
                p['source_review_original_bounds']=before['bounds']
            p['bounds']=list(reader.validate(bounds))
            p.update(assess(reader,p['bounds'],m['joins']))
            p['status']='NEEDS_REVIEW'
            p['review']={}
        if status:
            if status not in ACTIVE:
                raise ValueError('Invalid review status')
            if status=='VERIFIED':
                if p.get('metadata_only') or p.get('review_task'):
                    raise ValueError('Source review tasks cannot be approved as artwork')
                if before.get('status')=='ALTERNATIVE':
                    raise ValueError('Choose this alternative before verifying artwork')
                if p.get('classification') in {'compound_layout','uncertain'}:
                    raise ValueError('An unresolved compound layout cannot be verified as one complete illustration')
                if not checked:
                    raise ValueError('Explicitly confirm that complete artwork and crop boundaries were visually checked')
                if p['join_ids'] and not joins_checked:
                    raise ValueError('Cross-source joins must also be explicitly checked')
                p['flags']=[{'AUTOMATIC_BOUNDARIES_REQUIRE_REVIEW':'BOUNDARIES_VISUALLY_CHECKED','UNVERIFIED_JOIN':'JOINS_VISUALLY_CHECKED'}.get(f,f) for f in p['flags']]
                p['review']={'artwork_checked':True,'joins_checked':bool(joins_checked),'at':now(),'method':'explicit_human_visual_review' if reviewer=='user' else 'assistant_visual_inspection','reviewer':reviewer,'note':str(note)}
                for j in m['joins']:
                    if j['id'] in p['join_ids']:
                        j['status']='VISUALLY_CHECKED'
            else:
                p['review']={}
            if status=='VERIFIED' and reviewer!='user':
                raise ValueError('Only an explicit human review can mark artwork VERIFIED in V3')
            p['status']=status
        p['revision']+=1
        export_panel(root,m,p)
        audit(m,'update_panel',panel_id=panel_id,before=before,after=json.loads(json.dumps(p)))
        return finish(root,m)


def retire(root,p,status):
    # Immutable revision is kept for rollback/undo; only status changes.
    p['status']=status
    p['active']=False


def merge_panels(output_dir,ids):
    root=Path(output_dir)
    with writing(root):
        m=load_manifest(root)
        if len(set(ids))<2:
            raise ValueError('Select at least two adjacent panels')
        active=sorted([p for p in m['panels'] if p['status'] in ACTIVE],key=lambda p:panel_sort_key(m,p))
        selected=[get_panel(m,i) for i in dict.fromkeys(ids)]
        indexes=sorted(active.index(p) for p in selected)
        if indexes!=list(range(indexes[0],indexes[-1]+1)):
            raise ValueError('Merge only adjacent panels in chapter reading order')
        selected.sort(key=lambda p:panel_sort_key(m,p))
        bounds=[min(p['bounds'][0] for p in selected),min(p['bounds'][1] for p in selected),
                max(p['bounds'][2] for p in selected),max(p['bounds'][3] for p in selected)]
        before=json.loads(json.dumps(selected))
        first=selected[0]
        stage=root/'review'/f"{first['id']}_merge_staged.png"
        Chapter(root,m['sources']).save_png(bounds,stage)
        # Render succeeds before any existing outputs are retired.
        for p in selected:
            retire(root,p,'MERGED')
        first.update(bounds=bounds,polygon=None,status='NEEDS_REVIEW',active=True,revision=first['revision']+1,review={})
        first.update(assess(Chapter(root,m['sources']),bounds,m['joins']))
        first['flags'].append('MANUAL_MERGE_CHECK_CONTENT')
        export_panel(root,m,first,prepared_file=stage)
        audit(m,'merge',ids=ids,before=before,result_id=first['id'])
        return finish(root,m)


def split_panel(output_dir,id,y,axis="y"):
    root=Path(output_dir)
    with writing(root):
        m=load_manifest(root)
        p=get_panel(m,id)
        if isinstance(y,bool) or int(y)!=y:
            raise ValueError('Split coordinate must be an integer')
        y=int(y)
        x0,y0,x1,y1=p['bounds']
        if axis not in {'x','y'}:
            raise ValueError('Split axis must be x or y')
        if not ((y0<y<y1) if axis=='y' else (x0<y<x1)):
            raise ValueError('Split must be inside selected panel')
        before=json.loads(json.dumps(p))
        ids={q['id'] for q in m['panels']}
        n=1
        while f'{id}_s{n:02d}' in ids:
            n+=1
        bottom={'id':f'{id}_s{n:02d}','bounds':([x0,y,x1,y1] if axis=='y' else [y,y0,x1,y1]),'revision':1,'review':{},'status':'NEEDS_REVIEW','derived_from':id,'classification':p.get('classification','illustration'),'polygon':p.get('polygon')}
        p.update(bounds=([x0,y0,x1,y] if axis=='y' else [x0,y0,y,y1]),status='NEEDS_REVIEW',review={},revision=p['revision']+1)
        if before.get('review_task'):
            for child in (p,bottom):
                child.update(review_task=False,metadata_only=False,active=True,source_review_original_bounds=before['bounds'])
        m['panels'].append(bottom)
        reader=Chapter(root,m['sources'])
        for q in (p,bottom):
            q.update(assess(reader,q['bounds'],m['joins']))
            q['flags'].append('MANUAL_SPLIT_CHECK_CONTENT')
            export_panel(root,m,q)
        audit(m,'split',panel_id=id,y=y,before=before,result_ids=[id,bottom['id']])
        return finish(root,m)


def reprocess_panels(output_dir,ids):
    """Reassess/export ONLY selected saved bounds, retaining human corrections.

    This deliberately does not rerun chapter-wide segmentation over approved work.
    """
    root=Path(output_dir)
    with writing(root):
        m=load_manifest(root)
        reader=Chapter(root,m['sources'])
        selected=[get_panel(m,id) for id in dict.fromkeys(ids)]
        for p in selected:
            was_verified=human_verified(p)
            previous_review=p.get('review',{}).copy()
            p.update(assess(reader,p['bounds'],m['joins']))
            if was_verified:
                p.update(status='VERIFIED',review=previous_review)
                p['flags']=[{'AUTOMATIC_BOUNDARIES_REQUIRE_REVIEW':'BOUNDARIES_VISUALLY_CHECKED','UNVERIFIED_JOIN':'JOINS_VISUALLY_CHECKED'}.get(f,f) for f in p['flags']]
            else:
                p.update(status='NEEDS_REVIEW',review={},revision=p['revision']+1)
                export_panel(root,m,p)
        audit(m,'reprocess_selected',ids=ids)
        return finish(root,m)


def classify_panel(output_dir,id,classification,note=''):
    classification=_ALIASES.get(classification,classification)
    if classification not in CATEGORIES:
        raise ValueError('Unknown region classification')
    root=Path(output_dir)
    with writing(root):
        m=load_manifest(root)
        p=next((q for q in m['panels'] if q['id']==id and (q['status'] in ACTIVE or q['status'] in {'IGNORED','SOURCE_REVIEW'})),None)
        if p is None:
            raise ValueError('Panel not found')
        before=json.loads(json.dumps(p))
        p.update(classification=classification,status='NEEDS_REVIEW',review={},revision=p['revision']+1,metadata_only=False,review_task=False,active=True)
        p['classification_note']=str(note)
        p['flags']=[f for f in p['flags'] if not f.startswith('CLASSIFIED_')]
        if classification!='illustration':
            p['flags'].append('CLASSIFIED_'+classification.upper())
        export_panel(root,m,p)
        audit(m,'classify',panel_id=id,before=before,classification=classification,note=str(note))
        return finish(root,m)


def job_status(output_dir):
    from .storage import resource_usage
    root=Path(output_dir)
    result=Store(root).job()
    result['resources_now']=resource_usage(root)
    return result


def job_control(output_dir,action):
    return Store(output_dir).request_control(action)


def undo(output_dir):
    with writing(output_dir):
        m=Store(output_dir).undo()
        save_manifest(output_dir,m,operation='checkpoint')
        return m


def redo(output_dir):
    with writing(output_dir):
        m=Store(output_dir).redo()
        save_manifest(output_dir,m,operation='checkpoint')
        return m


def reorder_panels(output_dir,ids):
    with writing(output_dir):
        m=load_manifest(output_dir)
        active=[p for p in m['panels'] if p['status'] in ACTIVE]
        if len(ids)!=len(set(ids)) or set(ids)!={p['id'] for p in active}:
            raise ValueError('Provide every active region ID exactly once')
        for i,id in enumerate(ids,1):
            get_panel(m,id)['manual_order']=i
        audit(m,'reorder_regions',ids=ids)
        return finish(output_dir,m)


def reorder_sources(output_dir,ids):
    from .importer import reorder_sources as reorder
    with writing(output_dir):
        m=load_manifest(output_dir)
        if m.get('panels'):
            raise ValueError('Source order changes would invalidate saved pixel coordinates. Reorder during preflight, before segmentation, or create a new workspace.')
        sources=reorder(output_dir,ids)
        m['sources']=sources
        m['reading_order_reviewed']=True
        m['total_height']=max(s['y1'] for s in sources)
        audit(m,'reorder_sources',ids=ids)
        return finish(output_dir,m)


def choose_alternative(output_dir,parent_id,child_ids):
    with writing(output_dir):
        m=load_manifest(output_dir)
        parent=get_panel(m,parent_id)
        if not child_ids or len(child_ids)!=len(set(child_ids)) or not set(child_ids)<=set(parent.get('children',[])):
            raise ValueError('Select children belonging to this parent grouping')
        for id in child_ids:
            child=get_panel(m,id)
            child.update(status='NEEDS_REVIEW',active=True,revision=child['revision']+1,review={})
            child['flags']=[f for f in child['flags'] if f!='ALTERNATIVE_NOT_SELECTED']
            export_panel(output_dir,m,child)
        parent.update(status='SUPERSEDED',active=False,selected_children=list(child_ids))
        audit(m,'choose_alternative',parent_id=parent_id,children=child_ids)
        return finish(output_dir,m)


def refresh_qa(output_dir):
    from .qa import run_qa
    with writing(output_dir):
        m=load_manifest(output_dir)
        result=run_qa(output_dir,m)
        m.update(qa=result,qa_stale=False,production_status='REVIEW_REQUIRED')
        save_manifest(output_dir,m,operation='checkpoint')
        return result


def redetect_panels(output_dir,ids):
    """Stage differences as alternatives, preserving every existing approval."""
    with writing(output_dir):
        m=load_manifest(output_dir)
        reader=Chapter(output_dir,m['sources'])
        for id in ids:
            parent=get_panel(m,id)
            if parent.get('review_task'):
                parent.setdefault('flags',[]).append('SOURCE_TASK_USE_PROPOSED_MARKERS')
                continue
            metrics=assess(reader,parent['bounds'],m['joins'])
            gutters=metrics.get('internal_gutter_candidates',[])
            if not gutters:
                parent.setdefault('flags',[]).append('REDETECT_NO_SAFE_SEPARATOR_FOUND')
                continue
            # Internal corridor evidence is offered for review, never auto-accepted.
            cuts=[parent['bounds'][1]]+[int((a+b)//2) for a,b in gutters]+[parent['bounds'][3]]
            children=[]
            for n,(a,b) in enumerate(zip(cuts,cuts[1:]),1):
                if b-a<8:continue
                childid=f'{id}_d{parent["revision"]:03d}_{n:02d}'
                if any(p['id']==childid for p in m['panels']):continue
                bounds=[parent['bounds'][0],a,parent['bounds'][2],b]
                child=dict(id=childid,bounds=bounds,status='ALTERNATIVE',active=False,revision=1,review={},classification='illustration',parent_id=id,proposal_role='child')
                child.update(assess(reader,bounds,m['joins']))
                export_panel(output_dir,m,child)
                m['panels'].append(child);children.append(childid)
            parent['children']=list(dict.fromkeys(parent.get('children',[])+children))
            parent['difference_preview']={'previous_bounds':parent['bounds'],'proposed_children':children,'existing_approval_preserved':parent['status']=='VERIFIED'}
        audit(m,'targeted_redetection_differences',ids=ids)
        return finish(output_dir,m)
