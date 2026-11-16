"""Run with the test extra plus Playwright/Chromium installed.

Uses a temporary, explicitly synthetic review workspace; never changes chapter
results or treats these interaction checks as artwork boundary verification.
"""
import argparse
import hashlib
from pathlib import Path
import runpy
import sys
import tempfile
from threading import Thread

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from werkzeug.serving import make_server
from manga_tool.review import create_app
from manga_tool.pipeline import load_manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--screenshot',type=Path)
    args=parser.parse_args()
    from playwright.sync_api import sync_playwright
    make_workspace=runpy.run_path(str(Path(__file__).with_name('test_review_v3.py')))['make_review_workspace']
    with tempfile.TemporaryDirectory(prefix='manga-v3-browser-') as temporary:
        root=make_workspace(Path(temporary)/'output')
        server=make_server('127.0.0.1',0,create_app(root),threaded=True)
        Thread(target=server.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'])
                # Evaluation helpers are test-only; the served app keeps its strict CSP.
                page=browser.new_page(viewport={'width':1500,'height':1100},bypass_csp=True)
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.wait_for_function("document.getElementById('sourceCanvas').width>300 && document.getElementById('panelTitle').textContent==='UI_P001'")
                original=int(page.locator('#x0').input_value())
                box=page.locator('#sourceCanvas').bounding_box()
                geometry=page.evaluate('({bounds:state.bounds,context:state.context})')
                b,c=geometry['bounds'],geometry['context']
                x=box['x']+(b[0]-c[0])/(c[2]-c[0])*box['width']
                y=box['y']+(b[1]+35-c[1])/(c[3]-c[1])*box['height']
                page.mouse.move(x,y);page.mouse.down();page.mouse.move(x-8,y);page.mouse.up()
                assert int(page.locator('#x0').input_value())<original
                page.locator('#save').click()
                page.wait_for_function("document.getElementById('message').textContent.startsWith('Boundaries saved')")
                saved_left=int(page.locator('#x0').input_value())
                page.locator('.polygon-tools > summary').click()
                page.locator('#rectanglePolygon').click()
                page.locator('#sourceCanvas').evaluate("el=>el.scrollIntoView({block:'start'})")
                geometry=page.evaluate('({bounds:state.bounds,context:state.context})')
                box=page.locator('#sourceCanvas').bounding_box();b,c=geometry['bounds'],geometry['context']
                vx=box['x']+(b[0]-c[0])/(c[2]-c[0])*box['width']
                vy=box['y']+(b[1]-c[1])/(c[3]-c[1])*box['height']
                page.mouse.move(vx,vy);page.mouse.down();page.mouse.move(vx+8,vy+5);page.mouse.up()
                assert page.evaluate('state.polygon[0][0]')>saved_left
                page.locator('#save').click()
                page.wait_for_function("document.getElementById('message').textContent.startsWith('Boundaries saved')")
                page.locator('#clearPolygon').click();page.locator('#save').click()
                page.wait_for_function('state.originalPolygon.length===0')
                page.wait_for_function('!state.busy')
                page.locator('#classification').select_option('dialogue_or_narration_only')
                page.locator('#classify').click()
                page.wait_for_function("document.getElementById('classificationInfo').textContent.includes('dialogue or narration only')")
                page.wait_for_function('!state.busy')
                page.locator('#classification').select_option('illustration')
                page.locator('#classify').click()
                page.wait_for_function("document.getElementById('classificationInfo').textContent==='Saved type: illustration'")
                page.wait_for_function('!state.busy')
                page.locator('#panelTitle').click()
                page.keyboard.press('f')
                page.wait_for_function("state.panel.flags.includes('USER_FLAGGED')")
                page.keyboard.press('z')
                page.wait_for_function("!state.panel.flags.includes('USER_FLAGGED')")
                page.keyboard.press('y')
                page.wait_for_function("state.panel.flags.includes('USER_FLAGGED')")
                page.locator('#splitAxis').select_option('x')
                page.locator('#splitY').fill('160')
                page.locator('#split').click()
                page.wait_for_function("document.querySelectorAll('.panel-item').length===2")
                page.locator('.panel-item input[type=checkbox]').nth(0).check()
                page.locator('.panel-item input[type=checkbox]').nth(1).check()
                page.locator('#merge').click()
                page.wait_for_function("document.querySelectorAll('.panel-item').length===1")
                page.locator('#approve').click()
                page.wait_for_function("document.getElementById('message').textContent.includes('Complete both visual inspection checks')")
                page.locator('#artChecked').check();page.locator('#joinChecked').check()
                page.locator('#approve').click()
                page.wait_for_function("document.getElementById('status').textContent==='VERIFIED'")
                manifest=load_manifest(root)
                panel=next(p for p in manifest['panels'] if p['id']=='UI_P001')
                assert panel['output_file'].startswith('approved_artwork/')
                with page.expect_download() as pending:
                    page.locator('#download').click()
                downloaded=Path(pending.value.path())
                assert hashlib.sha256(downloaded.read_bytes()).hexdigest()==panel['sha256']
                page.locator('#reject').click()
                page.wait_for_function("document.getElementById('status').textContent==='REJECTED'")
                page.reload()
                page.wait_for_function("document.getElementById('status').textContent==='REJECTED'")
                assert int(page.locator('#x0').input_value())==saved_left
                page.locator('#splitY').fill('999999')
                page.locator('#split').click()
                page.wait_for_function("document.getElementById('message').classList.contains('error')")
                page.locator('#overview').evaluate('(element)=>element.open=true')
                page.locator('#thumbnailSize').fill('220')
                page.locator('#riskFilter').select_option('unresolved')
                page.locator('#nativeZoom').click()
                page.locator('#viewportImage').wait_for()
                page.locator('#closeViewport').click()
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(args.screenshot),full_page=True)
                # Real UI import pauses at preflight, allows source review, then resumes.
                page.locator('#setup').evaluate('(element)=>element.open=true')
                page.locator('#inputPath').fill(str(root/'originals'))
                page.locator('#outputPath').fill(str(Path(temporary)/'preflight-workspace'))
                page.locator('#preflightFirst').check()
                page.locator('#import').click()
                page.wait_for_function("document.getElementById('jobStage').textContent.includes('PAUSED') && document.querySelectorAll('#sourcePreflight img').length===2")
                assert page.locator('.editor').is_hidden()
                page.locator('#continueAnalysis').click()
                page.wait_for_function("/REVIEW_REQUIRED|REVIEW_READY/.test(document.getElementById('jobStage').textContent) && document.querySelectorAll('.panel-item').length>0")
                assert page.locator('.editor').is_visible()
                assert not errors,errors
                browser.close()
                print('PASS: mouse drag, polygon vertex drag/clear, save, keyboard flag/undo/redo, split/merge, V3 classification, explicit approval, rejection, reload persistence, exact download, invalid request display, overview filters, native viewport, actual preflight pause/resume; zero JS errors.')
        finally:
            server.shutdown()


if __name__=='__main__':
    main()
