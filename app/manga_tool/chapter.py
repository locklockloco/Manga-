"""A virtual chapter. Source rows are never resampled, aligned, or blended."""
from pathlib import Path
import math
import os
import struct
import zlib
from PIL import Image, ImageDraw


class Chapter:
    def __init__(self, output_dir, sources):
        self.root = Path(output_dir)
        self.sources = sources
        self.width = max(s['width'] for s in sources)
        self.height = max(s['y1'] for s in sources)
        self.mixed_widths = len({s['width'] for s in sources}) > 1
        self.mode = 'RGBA' if self.mixed_widths or any(s.get('has_transparency') or 'A' in s.get('mode', '') or s.get('mode') == 'P' for s in sources) else 'RGB'
        self.background = (255, 255, 255, 0) if self.mixed_widths else 'white'

    def validate(self, bounds):
        if len(bounds) != 4 or any(isinstance(v, bool) or int(v) != v for v in bounds):
            raise ValueError('Bounds must contain four integer pixel coordinates')
        x0, y0, x1, y1 = map(int, bounds)
        if not (0 <= x0 < x1 <= self.width and 0 <= y0 < y1 <= self.height):
            raise ValueError('Crop outside chapter or empty crop')
        return x0, y0, x1, y1

    def regions(self, bounds):
        x0, y0, x1, y1 = self.validate(bounds)
        result = []
        for s in self.sources:
            a, b, right = max(y0, s['y0']), min(y1, s['y1']), min(x1, s['width'])
            if a < b and x0 < right:
                result.append({'source_id': s['id'], 'source_order': s['order'],
                               'source_bounds': [x0, a-s['y0'], right, b-s['y0']],
                               'chapter_bounds': [x0, a, right, b]})
        return result

    def pieces(self, bounds):
        """Yield at most one decoded source strip at a time (caller closes pieces)."""
        x0, y0, x1, y1 = self.validate(bounds)
        for s in self.sources:
            a, b = max(y0, s['y0']), min(y1, s['y1'])
            if a >= b:
                continue
            with Image.open(self.root / s['path']) as im:
                if im.width * im.height > 80_000_000:
                    raise ValueError('Individual source exceeds the 80 MP decode limit')
                piece = Image.new(self.mode, (x1-x0, b-a), self.background)
                right = min(x1, im.width)
                if right > x0:
                    part = im.crop((x0, a-s['y0'], right, b-s['y0'])).convert(self.mode)
                    piece.paste(part, (0, 0))
                    part.close()
                yield s, a, piece
                piece.close()

    def render(self, bounds):
        x0, y0, x1, y1 = self.validate(bounds)
        if (x1-x0)*(y1-y0) > 80_000_000:
            raise ValueError('Use preview or save_png for crops larger than 80 MP')
        out = Image.new(self.mode, (x1-x0, y1-y0), self.background)
        for _, a, piece in self.pieces(bounds):
            out.paste(piece, (0, a-y0))
        return out

    def preview(self, bounds, max_size=1600):
        x0, y0, x1, y1 = self.validate(bounds)
        scale = min(1, max_size / max(x1-x0, y1-y0))
        w, h = max(1, round((x1-x0)*scale)), max(1, round((y1-y0)*scale))
        out = Image.new(self.mode, (w, h), self.background)
        for _, a, piece in self.pieces(bounds):
            top = round((a-y0)*scale)
            bottom = round((a+piece.height-y0)*scale)
            if bottom > top:
                thumb = piece.resize((w, bottom-top), Image.Resampling.LANCZOS)
                out.paste(thumb, (0, top))
                thumb.close()
        return out

    def save_png(self, bounds, path, polygon=None):
        """Stream a lossless PNG; memory is one decoded strip, even for huge panels."""
        x0, y0, x1, y1 = self.validate(bounds)
        if polygon is not None:
            if not 3 <= len(polygon) <= 1024:
                raise ValueError('A polygon needs 3–1024 vertices')
            for point in polygon:
                if len(point)!=2 or any(isinstance(v,bool) or int(v)!=v for v in point) or not(0 <= point[0] <= self.width and 0 <= point[1] <= self.height):
                    raise ValueError('Polygon vertices must be integer chapter pixels inside the chapter')
        png_mode = 'RGBA' if polygon else self.mode
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.png.tmp')
        def chunk(f, name, data):
            f.write(struct.pack('!I', len(data)))
            f.write(name + data)
            f.write(struct.pack('!I', zlib.crc32(name + data) & 0xffffffff))
        try:
            from .storage import Store
            job_store=Store(self.root) if (self.root/'review/state.sqlite3').exists() else None
            with tmp.open('wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n')
                chunk(f, b'IHDR', struct.pack('!2I5B', x1-x0, y1-y0, 8, 6 if png_mode == 'RGBA' else 2, 0, 0, 0))
                profiles = []
                for s in self.sources:
                    if max(y0, s['y0']) < min(y1, s['y1']):
                        with Image.open(self.root / s['path']) as im:
                            profiles.append(im.info.get('icc_profile'))
                if profiles and profiles[0] and all(p == profiles[0] for p in profiles):
                    chunk(f, b'iCCP', b'original\0\0' + zlib.compress(profiles[0]))
                compressor = zlib.compressobj(6)
                for _, a, piece in self.pieces(bounds):
                    if job_store is not None and job_store.control()!='run':
                        job=job_store.job()
                        job_store.checkpoint(job.get('stage','EXPORT'),job.get('current',0),job.get('total',0))
                    if polygon:
                        piece = piece.convert('RGBA')
                        alpha = Image.new('L',piece.size,0)
                        ImageDraw.Draw(alpha).polygon([(px-x0,py-a) for px,py in polygon],fill=255)
                        # Only exterior transparency changes; retained RGB values are exact.
                        original_alpha=piece.getchannel('A')
                        from PIL import ImageChops
                        piece.putalpha(ImageChops.multiply(original_alpha,alpha))
                    # Row chunks keep output working memory bounded for very wide strips too.
                    for row in range(piece.height):
                        # PNG Sub is exactly reversible and keeps one scanline
                        # in memory. It compresses adjacent painted pixels much
                        # better than the prior always-None scanline filter.
                        import numpy as np
                        raw=np.frombuffer(piece.crop((0,row,piece.width,row+1)).tobytes(),dtype=np.uint8)
                        filtered=raw.copy();channels=4 if png_mode=='RGBA' else 3
                        filtered[channels:]=raw[channels:]-raw[:-channels]
                        data=compressor.compress(b'\1'+filtered.tobytes())
                        if data:
                            chunk(f, b'IDAT', data)
                chunk(f, b'IDAT', compressor.flush())
                chunk(f, b'IEND', b'')
            with tmp.open('rb') as completed:
                os.fsync(completed.fileno())
            if os.environ.get('MANGA_TEST_EXIT_STAGE')=='EXPORT' and os.environ.get('MANGA_TEST_EXIT_POINT') in {'before_commit','before_replace'}:
                from .storage import Store
                job=Store(self.root).job()
                if int(os.environ.get('MANGA_TEST_EXIT_AT','-1'))==int(job.get('current',-2)):
                    os._exit(86)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
