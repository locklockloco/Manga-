"""Resumable staged orchestration extending the existing importer and detector."""
from pathlib import Path
import json
import os
import re
import time
import traceback
from .storage import Store, JobPaused, JobCancelled, preflight, resource_usage
from . import detection
from .identity import validate_request, processing_options


def _kill_hook(stage,current,point='after_commit'):
    if (os.environ.get('MANGA_TEST_EXIT_STAGE')==stage and
        int(os.environ.get('MANGA_TEST_EXIT_AT','-1'))==int(current) and
        os.environ.get('MANGA_TEST_EXIT_POINT','after_commit')==point):
        os._exit(86)


def run_chapter(input_path,output_dir,chapter='CH01',include_all=False,min_gutter=12,padding=4,vertical=True,reading_direction='ltr',generate_contact_sheets=True,preflight=False):
    from . import pipeline as p
    from .importer import import_chapter
    from .chapter import Chapter
    from .qa import run_qa
    root=Path(output_dir).expanduser().resolve()
    input_path=Path(input_path).expanduser().resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',chapter):
        raise ValueError('Use letters, digits, _ or - for the chapter ID')
    if reading_direction not in {'ltr','rtl'} or not 4<=int(min_gutter)<=2000 or not 0<=int(padding)<=100:
        raise ValueError('Invalid reading direction, gutter or padding')
    root.mkdir(parents=True,exist_ok=True)
    options=dict(include_all=include_all,min_gutter=int(min_gutter),padding=int(padding),vertical=bool(vertical),reading_direction=reading_direction,generate_contact_sheets=bool(generate_contact_sheets))
    store=Store(root)
    with p.writing(root):
        job=store.job()
        m=store.load_manifest()
        if m is None and (root/'reports/manifest.json').exists():
            m=p.load_manifest(root)
        # Validate the complete request and preserved bytes before any completed
        # return, migration, projection repair or mutation of the old job state.
        # Identity failures remain outside the processing try/FAILED handler.
        input_identity=validate_request(root,input_path,chapter,options,job,m,store)
        if job.get('status') in {'REVIEW_READY','REVIEW_REQUIRED','COMPLETED'} and m is not None:
            p.sync_outputs(root,m)
            if not job.get('input_identity'):
                store.update_job(input_identity=input_identity,processing_options=processing_options(options))
            return m
        # A legacy completed chapter is migrated, never silently resegmented.
        if m is not None and m.get('panels') and not job.get('input'):
            m['input_identity']=input_identity
            for panel in m['panels']:
                if panel.get('status')=='VERIFIED' and not p.human_verified(panel):
                    panel.update(status='NEEDS_REVIEW',review={})
                    panel.setdefault('flags',[]).append('LEGACY_APPROVAL_REQUIRES_HUMAN_REVIEW')
                panel['classification']=p._ALIASES.get(panel.get('classification'),panel.get('classification','illustration'))
                p.export_panel(root,m,panel)
            p.save_manifest(root,m,operation='migrate_existing_workspace')
            qa=run_qa(root,m)
            m['qa']=qa;m['qa_stale']=False;m['production_status']='REVIEW_REQUIRED'
            p.save_manifest(root,m,operation='checkpoint')
            store.update_job(input=str(input_path),chapter=chapter,options=options,input_identity=input_identity,processing_options=processing_options(options),status='REVIEW_REQUIRED',stage='REVIEW_READY',production_status='REVIEW_REQUIRED')
            return m
        start=time.monotonic()
        previous_elapsed=float(job.get('elapsed_seconds',0))
        store.request_control('resume')
        store.update_job(status='RUNNING',input=str(input_path),chapter=chapter,options=options,input_identity=input_identity,processing_options=processing_options(options),pid=os.getpid(),algorithm_version=detection.ALGORITHM_VERSION,error=None,attempt=int(job.get('attempt',0))+1)
        stage_seconds=dict(job.get('stage_seconds',{}))
        stage_clock=time.monotonic();last_stage='SETUP'
        def checkpoint(stage,current,total,*args,**info):
            nonlocal stage_clock,last_stage
            current_clock=time.monotonic()
            stage_seconds[last_stage]=stage_seconds.get(last_stage,0)+(current_clock-stage_clock)
            stage_clock=current_clock;last_stage=stage
            current,total=int(current),int(total)
            if args:info['item_id']=args[0]
            sid=info.get('source_id')
            if sid:
                clean={k:v for k,v in info.items() if k!='status'}
                store.item('source',sid,info.get('status','INDEXED' if stage in {'INDEX','SEGMENT'} else 'IMPORTED'),**clean)
            observed=resource_usage(root)
            store.checkpoint(stage,current,total,stage_seconds=dict(stage_seconds),elapsed_seconds=previous_elapsed+time.monotonic()-start,**observed,**info)
            if stage!='EXPORT':
                _kill_hook(stage,current)
        try:
            estimates=globals()['preflight'](root,estimated_output_bytes=input_path.stat().st_size*4 if input_path.is_file() else 0,estimated_memory_bytes=32*1024*1024)
            store.update_job(resources=estimates)
            for folder in ('originals','proposals','approved_artwork','needs_review','contact_sheets','reports','review/revisions'):
                (root/folder).mkdir(parents=True,exist_ok=True)
            # Partial staging files are never valid originals or committed artwork.
            for folder in ('review/revisions','proposals','approved_artwork'):
                for tmp in (root/folder).glob('*.tmp'):
                    tmp.unlink(missing_ok=True)
            imported=import_chapter(input_path,root,include_all=include_all,checkpoint=checkpoint)
            sources=imported['sources']
            if not sources:
                raise ValueError('No valid source images; inspect reports/import.json')
            store.update_job(resources=globals()['preflight'](root,estimated_memory_bytes=max(s['width']*s['height']*48 for s in sources)))
            if m is None:
                m=dict(version=3,chapter=chapter,created_at=p.now(),input=str(input_path),input_identity=input_identity,sources=sources,panels=[],history=[],options=options,chapter_width=max(s['width'] for s in sources),total_height=max(s['y1'] for s in sources),joins=[],excluded=imported.get('excluded',[]),warnings=imported.get('warnings',[]),invalid_sources=imported.get('invalid_sources',[]),preflight_warnings=imported.get('preflight_warnings',[]),production_status='REVIEW_REQUIRED',algorithm_version=detection.ALGORITHM_VERSION)
            elif m.get('panels') and [(s['id'],s['sha256']) for s in sources]!=[(s['id'],s['sha256']) for s in m['sources']]:
                raise ValueError('Source bytes/order changed since saved regions. Resolve preflight and use a new analysis revision.')
            else:
                m.update(sources=sources,chapter_width=max(s['width'] for s in sources),total_height=max(s['y1'] for s in sources))
            p.save_manifest(root,m,operation='checkpoint',project=False)
            rows,stats=detection.index_profiles(root,sources,checkpoint=checkpoint)
            m['analysis']={'sources':stats,'method':'multiscale regional separators and continuity graph','limitations':'Geometry cannot prove semantic illustration completeness.'}
            m['joins']=detection.join_evidence(root,sources)
            for j in m['joins']:
                right=next(s for s in sources if s['id']==j['right_source'])
                if right.get('discontinuity_before'):
                    j.update(blocked=True,assessment='missing_or_invalid_source_before_join')
                    j.setdefault('flags',[]).append('SOURCE_SEQUENCE_GAP')
            p.save_manifest(root,m,operation='checkpoint',project=False)
            if preflight and not job.get('preflight_passed'):
                store.update_job(status='PAUSED',stage='INDEX',preflight_passed=True,preflight_ready=True,current=len(sources),total=len(sources))
                store.request_control('pause')
                raise JobPaused('Preflight is ready. Inspect thumbnails and reading order, then resume.')
            if not m.get('panels'):
                # Record the algorithm used for these new proposals without
                # rewriting version evidence on an already reviewed workspace.
                m['algorithm_version']=detection.ALGORITHM_VERSION
                checkpoint('SEGMENT',0,len(sources))
                proposals,stats,gutters=detection.propose_compositions(root,sources,int(min_gutter),int(padding),vertical,checkpoint=lambda stage,current,total,**info:checkpoint('SEGMENT' if stage=='INDEX' else stage,current,total,**info))
                proposals.sort(key=lambda q:(q['bounds'][1],q['bounds'][0] if reading_direction=='ltr' else -q['bounds'][2],q.get('proposal_role')=='child'))
                groups={}
                for i,proposal in enumerate(proposals,1):
                    checkpoint('SEGMENT',i,len(proposals),region_id=f'{chapter}_P{i:03d}')
                    child=proposal.get('proposal_role')=='child'
                    panel=dict(proposal,id=f'{chapter}_P{i:03d}',revision=1,status='ALTERNATIVE' if child else 'NEEDS_REVIEW',active=not child,classification='illustration',review={})
                    if proposal.get('proposal_role')=='parent':panel['classification']='compound_layout'
                    if proposal.get('review_task'):
                        panel.update(status='SOURCE_REVIEW',classification='uncertain',active=False,
                                     source_regions=Chapter(root,sources).regions(panel['bounds']),
                                     flags=proposal.get('ambiguity_flags',[]),confidence=0,join_ids=[])
                        m['panels'].append(panel)
                        continue
                    panel.update(detection.assess(Chapter(root,sources),panel['bounds'],m['joins']))
                    if 'COMPOUND_PANEL_LAYOUT_REQUIRES_REVIEW' in panel['flags']:
                        panel['classification']='compound_layout'
                    composition=proposal.get('composition_evidence',{})
                    attached_composition=(proposal.get('recommended_classification')=='illustration'
                        and composition.get('type') in {'single_enclosing_frame_with_attached_text',
                                                        'single_enclosing_frame','floating_bubble_full_composition',
                                                        'illustration_with_nearby_outlined_balloon'})
                    attached_composition=(attached_composition and not proposal.get('source_untrusted',False)
                        and not any(join.get('blocked') and panel['bounds'][1]<join['y']<panel['bounds'][3]
                                    for join in m['joins']))
                    if attached_composition:
                        panel['classification']='illustration'
                        panel['classification_note']='Full enclosing composition with attached text retained; human review remains required.'
                    panel['flags']=list(dict.fromkeys(panel['flags']+proposal.get('ambiguity_flags',[])+(['SIDE_BY_SIDE_READING_ORDER_REQUIRES_REVIEW'] if proposal.get('column_split') else [])))
                    if panel['classification']=='illustration' and panel.get('classification_suggestion') and not attached_composition:
                        panel['classification']=panel['classification_suggestion']
                    if child:panel['classification']='uncertain' if panel.get('requires_mask_review') else 'illustration'
                    if proposal.get('metadata_only'):
                        panel.update(classification=proposal['recommended_classification'],active=False,status='IGNORED')
                        panel['source_regions']=Chapter(root,sources).regions(panel['bounds'])
                    group=panel.get('group_key')
                    if group:groups.setdefault(group,[]).append(panel)
                    m['panels'].append(panel)
                for group,panels in groups.items():
                    parent=next((q for q in panels if q.get('proposal_role')=='parent'),None)
                    if parent:
                        parent['children']=[q['id'] for q in panels if q is not parent]
                        for q in panels:
                            if q is not parent:q['parent_id']=parent['id']
                m['analysis']['gutters']=gutters
                m['analysis']['artwork_policy']={'priority':'complete_illustrated_artwork','speech_preservation_required':False,'metadata_records':sum(bool(q.get('metadata_only')) for q in m['panels']),'source_tasks':sum(bool(q.get('review_task')) for q in m['panels']),'confirmed_text_metadata':sum(q.get('classification') in {'dialogue_or_narration_only','standalone_sfx'} and bool(q.get('metadata_only')) for q in m['panels']),'human_verified':False}
                p.audit(m,'v3_segmentation',regions=len(m['panels']),algorithm=detection.ALGORITHM_VERSION)
                p.save_manifest(root,m,operation='checkpoint',project=False)
            reader=Chapter(root,sources)
            for i,panel in enumerate(m['panels'],1):
                checkpoint('EXPORT',i,len(m['panels']),region_id=panel['id'])
                if panel.get('metadata_only'):
                    store.item('region',panel['id'],panel['status'],panel=panel)
                    continue
                if panel.get('file') and (root/panel['file']).exists() and p.digest(root/panel['file'])==panel.get('sha256'):
                    store.item('region',panel['id'],'EXPORTED',panel=panel,source_checksum=[sources[r['source_order']-1]['sha256'] for r in panel.get('source_regions',[])])
                    continue
                try:
                    p.export_panel(root,m,panel)
                    store.item('region',panel['id'],'EXPORTED',panel=panel)
                except (ValueError,OSError,MemoryError) as exc:
                    panel['error']=str(exc)
                    panel.setdefault('flags',[]).append('EXPORT_FAILED_REVIEW_REQUIRED')
                    store.item('region',panel['id'],'FAILED',error=str(exc),panel=panel)
                    # Filesystem/resource failures pause safely rather than pretending success.
                    if isinstance(exc,(OSError,MemoryError)):raise
                p.save_manifest(root,m,operation='checkpoint',project=False)
                _kill_hook('EXPORT',i)
            p.sync_outputs(root,m)
            checkpoint('QA',0,len(sources))
            report=run_qa(root,m,checkpoint=checkpoint)
            m.update(qa=report,qa_stale=False,production_status='REVIEW_REQUIRED')
            if generate_contact_sheets:
                checkpoint('CONTACT_SHEETS',0,1)
                p.generate_sheets(root,m)
            p.save_manifest(root,m,operation='checkpoint')
            checkpoint('COMPLETE',len(m['panels']),len(m['panels']))
            store.update_job(stage='REVIEW_READY',status='REVIEW_REQUIRED',current=len(m['panels']),total=len(m['panels']),production_status='REVIEW_REQUIRED',elapsed_seconds=previous_elapsed+time.monotonic()-start,error=None)
            p.atomic_json(root/'reports/performance.json',{'elapsed_seconds':previous_elapsed+time.monotonic()-start,
                'stage_seconds':dict(stage_seconds),'resources':resource_usage(root),
                'note':'Stage wall durations include checkpoint work; peak RSS is process-lifetime, excludes OCR child peak. Not a write-I/O measurement.',
                'output_disk_bytes':sum(f.stat().st_size for f in root.rglob('*') if f.is_file()),
                'algorithm_version':detection.ALGORITHM_VERSION,'status':'REVIEW_REQUIRED'})
            return m
        except (JobPaused,JobCancelled):
            raise
        except BaseException as exc:
            store.update_job(status='FAILED',error=str(exc),error_type=type(exc).__name__,elapsed_seconds=previous_elapsed+time.monotonic()-start)
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
            raise
