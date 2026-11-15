"""Optional actual Chromium test: python tests/browser_smoke.py (requires playwright)."""
from pathlib import Path
import sys
import tempfile
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect
from examples.make_demo import make_demo
from manga_tool.pipeline import process_chapter, load_manifest
from manga_tool.review import create_app


def main():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        make_demo(root/'demo')
        out=root/'output'
        process_chapter(root/'demo/input',out,chapter='BROWSER')
        server=make_server('127.0.0.1',0,create_app(out),threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1550,'height':1100})
                errors=[]
                page.on('pageerror',lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.locator('#panelTitle').filter(has_text='BROWSER_P001').wait_for()
                expect(page.locator("#sourceCanvas")).to_have_attribute("width","690")
                # Drag a genuine orange corner out by a few original pixels.
                page.locator('#sourceCanvas').scroll_into_view_if_needed()
                bounds=page.locator('#sourceCanvas').bounding_box()
                x0=float(page.locator('#x0').input_value())
                y0=float(page.locator('#y0').input_value())
                info=page.request.get(f'http://127.0.0.1:{server.server_port}/api/panels/BROWSER_P001').json()
                context=info['context_bounds']
                x=bounds['x']+x0/690*bounds['width']
                y=bounds['y']+(y0-context[1])/(context[3]-context[1])*bounds['height']
                page.mouse.move(x,y)
                page.mouse.down()
                page.mouse.move(x-4,y-4,steps=5)
                page.mouse.up()
                assert float(page.locator('#x0').input_value()) < x0
                page.locator('#save').click()
                expect(page.locator("#message")).to_contain_text("Boundaries saved")
                page.locator('#approve').click()
                expect(page.locator("#message")).to_contain_text("Complete both")
                page.locator('#artChecked').check()
                page.locator('#joinChecked').check()
                page.locator('#approve').click()
                expect(page.locator("#status")).to_have_text("VERIFIED")
                page.reload()
                expect(page.locator("#status")).to_have_text("VERIFIED")
                assert load_manifest(out)['panels'][0]['review']['artwork_checked'] is True
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
    print('Chromium smoke passed: drag, save, approval checks, persistence, zero JS errors (synthetic temp data).')


if __name__=='__main__':
    main()
