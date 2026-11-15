"""Final-build stress entry points and fast harness safety checks.

Heavy tests are opt-in. They require fresh output directories and the final
frozen detector version; fixture files alone may be reused, never old profiles.
"""
import os
from pathlib import Path

import pytest

from tools.stress_v3 import (generate_fixture, require_fresh_output,
                             run_final_suite, sha256, temporary_test_damage,
                             validate_full_size_fixture)


def test_final_harness_refuses_old_output_state(tmp_path):
    output=tmp_path/'output'
    (output/'review/cache').mkdir(parents=True)
    (output/'review/cache/old.npz').write_bytes(b'old analysis')
    with pytest.raises(ValueError,match='new output'):
        require_fresh_output(tmp_path)


def test_final_fixture_validation_checks_real_dimensions_and_bytes(tmp_path):
    fixture=tmp_path/'fixture'
    generate_fixture(fixture,strips=2)
    proof=validate_full_size_fixture(fixture,2)
    assert proof['dimensions']==[690,1280]
    assert proof['total_pixels']==2*690*1280
    source=next((fixture/'input').iterdir())
    source.write_bytes(source.read_bytes()+b'changed bytes')
    with pytest.raises(AssertionError,match='Fixture source changed'):
        validate_full_size_fixture(fixture,2)


def test_deliberate_damage_restores_exact_bytes_even_on_failure(tmp_path):
    source=tmp_path/'synthetic.png'
    source.write_bytes(b'exact test fixture bytes')
    original=sha256(source)
    with pytest.raises(RuntimeError):
        with temporary_test_damage(source,tmp_path/'backups',missing=True):
            assert not source.exists()
            raise RuntimeError('simulate rejected processing')
    assert sha256(source)==original
    assert not list((tmp_path/'backups').iterdir())
    assert not list(tmp_path.rglob('*.tmp'))


@pytest.mark.large
@pytest.mark.skipif(os.environ.get('MANGA_RUN_FINAL_STRESS')!='1',reason='Opt-in frozen 200-strip full-size SIGKILL/fault run')
def test_final_fresh_200_with_faults(tmp_path):
    fixture=os.environ.get('MANGA_FINAL_FIXTURE_200')
    if not fixture:
        fixture=tmp_path/'fixture'
        generate_fixture(fixture,strips=200)
    alternate=os.environ.get('MANGA_FINAL_FIXTURE_500')
    result=run_final_suite(tmp_path/'final200',fixture,200,interrupts=True,source_faults=True,alternate_fixture=alternate)
    assert result['passed'] and result['all_attempts_final_version']
    assert result['pixel_mismatches']==0 and result['verified_region_count']==0


@pytest.mark.large
@pytest.mark.skipif(os.environ.get('MANGA_RUN_FINAL_STRESS')!='1',reason='Opt-in frozen 500-strip full-size cold run')
def test_final_fresh_500(tmp_path):
    fixture=os.environ.get('MANGA_FINAL_FIXTURE_500')
    if not fixture:
        fixture=tmp_path/'fixture'
        generate_fixture(fixture,strips=500)
    result=run_final_suite(tmp_path/'final500',fixture,500,interrupts=False)
    assert result['passed'] and result['all_attempts_final_version']
    assert result['original_files_compared']==500 and result['pixel_mismatches']==0
