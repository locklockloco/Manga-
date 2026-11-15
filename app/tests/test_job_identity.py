"""A completed or partial output folder must never stand in for another request."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import zipfile

from PIL import Image, ImageDraw
import pytest

from manga_tool import pipeline
from manga_tool.identity import JobIdentityError
from manga_tool.storage import Store, JobPaused


def make_input(path, color='#294a67'):
    path.mkdir()
    image=Image.new('RGB',(180,260),'white')
    ImageDraw.Draw(image).rectangle((15,20,164,239),fill=color)
    image.save(path/'strip001.png')
    return path


def run(source, output, **kwargs):
    return pipeline.process_chapter(source,output,chapter=kwargs.pop('chapter','ID'),
                                    generate_contact_sheets=kwargs.pop('generate_contact_sheets',False),**kwargs)


@pytest.fixture
def complete(tmp_path):
    source=make_input(tmp_path/'input')
    output=tmp_path/'output'
    manifest=run(source,output)
    return source,output,manifest


def evidence(output):
    return (Store(output).job(),Store(output).load_manifest(),
            {str(path.relative_to(output)):path.read_bytes()
             for directory in ('proposals','approved_artwork','needs_review','reports')
             for path in (output/directory).rglob('*') if path.is_file()})


def rejected_unchanged(source,output,**kwargs):
    before=evidence(output)
    with pytest.raises((JobIdentityError,ValueError),match='different|differs|changed|exist|identity|inventory|checksum'):
        run(source,output,**kwargs)
    assert evidence(output)==before


def test_completed_different_input_and_chapter_refused_without_old_job_mutation(complete,tmp_path):
    source,output,_=complete
    other=make_input(tmp_path/'different')
    rejected_unchanged(other,output)
    rejected_unchanged(source,output,chapter='OTHER')


@pytest.mark.parametrize('setting,value',[('include_all',True),('min_gutter',36),('padding',8),('vertical',False),('reading_direction','rtl')])
def test_completed_processing_setting_mismatch_refused(complete,setting,value):
    source,output,_=complete
    rejected_unchanged(source,output,**{setting:value})


@pytest.mark.parametrize('change',['add','delete','replace_same_size_and_mtime'])
def test_folder_content_inventory_changes_detected(complete,change):
    source,output,_=complete
    path=source/'strip001.png'
    if change=='add':
        shutil.copyfile(path,source/'strip002.png')
    elif change=='delete':
        path.unlink()
    else:
        stat=path.stat()
        raw=bytearray(path.read_bytes())
        raw[-20]^=1
        path.write_bytes(raw)
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        assert path.stat().st_size==stat.st_size and path.stat().st_mtime_ns==stat.st_mtime_ns
    rejected_unchanged(source,output)


def test_missing_input_refused_before_completed_return(complete):
    source,output,_=complete
    shutil.rmtree(source)
    rejected_unchanged(source,output)


@pytest.mark.parametrize('suffix',['.zip','.webarchive'])
def test_archive_bytes_changed_even_same_size_and_mtime_refused(tmp_path,suffix):
    from examples.make_demo import make_demo
    fixtures=tmp_path/'fixtures'
    make_demo(fixtures)
    source=fixtures/('synthetic_chapter'+suffix)
    output=tmp_path/'output'
    run(source,output)
    stat=source.stat()
    raw=bytearray(source.read_bytes())
    raw[len(raw)//2]^=1
    source.write_bytes(raw)
    os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    rejected_unchanged(source,output)


def test_original_and_immutable_corruption_refused_before_repair(complete):
    source,output,m=complete
    for relative in (m['sources'][0]['path'],m['panels'][0]['file']):
        path=output/relative
        original=path.read_bytes()
        stat=path.stat()
        raw=bytearray(original);raw[-10]^=1
        path.write_bytes(raw)
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        rejected_unchanged(source,output)
        path.write_bytes(original)


def test_unchanged_input_idempotent_retains_manual_edit_and_allows_display_options(complete):
    source,output,m=complete
    panel=m['panels'][0]
    bounds=list(panel['bounds']);bounds[0]+=1
    edited=pipeline.update_panel(output,panel['id'],bounds=bounds)
    before=deepcopy(Store(output).job())
    repeated=run(source,output,generate_contact_sheets=True,preflight=True)
    assert repeated==edited
    assert repeated['panels'][0]['bounds']==bounds
    assert Store(output).job()==before
    assert repeated['panels'][0]['status']!='VERIFIED'


def test_identity_persisted_before_import_and_changed_paused_source_refused(tmp_path,monkeypatch):
    source=make_input(tmp_path/'input')
    output=tmp_path/'output'
    real=Store.checkpoint
    def pause(self,stage,current,total,**info):
        if stage=='IMPORT' and current==1:
            assert self.job()['input_identity']['entries'][0]['sha256']
            self.request_control('pause')
        return real(self,stage,current,total,**info)
    with monkeypatch.context() as patch:
        patch.setattr(Store,'checkpoint',pause)
        with pytest.raises(JobPaused):run(source,output)
    assert Store(output).job()['status']=='PAUSED'
    # Changes after IMPORT must be refused before cached originals can be reused.
    shutil.copyfile(source/'strip001.png',source/'strip002.png')
    rejected_unchanged(source,output)


def test_partial_original_cache_tampering_refused(tmp_path,monkeypatch):
    source=make_input(tmp_path/'input')
    output=tmp_path/'output'
    real=Store.checkpoint
    def pause(self,stage,current,total,**info):
        if stage=='IMPORT':self.request_control('pause')
        return real(self,stage,current,total,**info)
    with monkeypatch.context() as patch:
        patch.setattr(Store,'checkpoint',pause)
        with pytest.raises(JobPaused):run(source,output)
    original=next((output/'originals').glob('*.png'))
    raw=bytearray(original.read_bytes());raw[-10]^=1
    stat=original.stat();original.write_bytes(raw)
    os.utime(original,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    rejected_unchanged(source,output)


def test_preflight_reorder_does_not_change_input_identity(tmp_path):
    source=make_input(tmp_path/'input')
    shutil.copyfile(source/'strip001.png',source/'strip002.png')
    output=tmp_path/'output'
    with pytest.raises(JobPaused):run(source,output,preflight=True)
    original_plan=Store(output).job()['input_identity']
    m=pipeline.load_manifest(output)
    pipeline.reorder_sources(output,list(reversed([s['id'] for s in m['sources']])))
    run(source,output)
    assert Store(output).job()['input_identity']==original_plan
    assert pipeline.load_manifest(output)['sources'][0]['id']=='S0002'


def legacy_workspace(tmp_path):
    source=make_input(tmp_path/'input')
    output=tmp_path/'legacy'
    m=run(source,output)
    # Emulate a JSON-only earlier workspace. Preserve originals and evidence.
    for path in (output/'review').glob('state.sqlite3*'):path.unlink()
    m.pop('input_identity',None)
    (output/'reports/manifest.json').write_text(json.dumps(m),encoding='utf-8')
    return source,output,m


def test_legacy_migration_refuses_different_input_chapter_and_settings(tmp_path):
    source,output,_=legacy_workspace(tmp_path)
    other=make_input(tmp_path/'other')
    rejected_unchanged(source,output,chapter='WRONG')
    rejected_unchanged(other,output)
    rejected_unchanged(source,output,padding=9)


def test_legacy_same_request_migrates_only_matching_preserved_source_bytes(tmp_path):
    source,output,m=legacy_workspace(tmp_path)
    adopted=run(source,output)
    assert adopted['chapter']=='ID'
    assert Store(output).job()['input_identity']['entries'][0]['sha256']==m['sources'][0]['sha256']
    assert Store(output).job()['status']=='REVIEW_REQUIRED'


def test_legacy_changed_content_or_original_refused(tmp_path):
    source,output,m=legacy_workspace(tmp_path)
    path=source/'strip001.png'
    raw=bytearray(path.read_bytes());raw[-10]^=1;path.write_bytes(raw)
    rejected_unchanged(source,output)


def test_paused_import_rejects_changed_source_not_yet_copied(tmp_path,monkeypatch):
    source=make_input(tmp_path/'input')
    for index in (2,3):
        shutil.copyfile(source/'strip001.png',source/f'strip{index:03d}.png')
    output=tmp_path/'output'
    real=Store.checkpoint
    def pause(self,stage,current,total,**info):
        if stage=='IMPORT' and current==1:self.request_control('pause')
        return real(self,stage,current,total,**info)
    with monkeypatch.context() as patch:
        patch.setattr(Store,'checkpoint',pause)
        with pytest.raises(JobPaused):run(source,output)
    assert len(Store(output).job()['input_identity']['entries'])==3
    assert len(list((output/'originals').glob('*.png')))==1
    path=source/'strip003.png'
    stamp=path.stat();raw=bytearray(path.read_bytes());raw[-10]^=1
    path.write_bytes(raw);os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    rejected_unchanged(source,output)
