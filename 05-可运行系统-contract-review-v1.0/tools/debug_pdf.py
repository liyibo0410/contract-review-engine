# -*- coding: utf-8 -*-
"""PDF 内部结构调试：定位内容流/字体/CMap 抽取失败原因。"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import parsers                                          # noqa: E402

out = []


def dump(path):
    out.append("=" * 78)
    out.append("FILE: %s" % os.path.basename(path))
    with open(path, "rb") as f:
        data = f.read()
    out.append("size=%d header=%r" % (len(data), data[:8]))
    objs = parsers._PdfObjects(data)
    out.append("objects=%d  nums=%s" % (len(objs.objs), sorted(objs.objs)[:40]))
    pages = parsers._page_objects(objs)
    out.append("pages=%s" % pages)

    for onum in pages[:1]:
        body = objs.get(onum)
        out.append("-" * 60)
        out.append("PAGE OBJ %s body[:500]=%r" % (onum, body[:500]))

        # /Contents
        m = re.search(rb"/Contents\s*(\d+)\s+\d+\s+R", body)
        out.append("Contents ref match: %r" % (m.group(0) if m else None))
        m2 = re.search(rb"/Contents\s*(\[.*?\])", body, re.S)
        out.append("Contents array match: %r" % (m2.group(1)[:200] if m2 else None))

        nums = []
        if m:
            nums = [int(m.group(1))]
        elif m2:
            nums = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", m2.group(1))]
        for cn in nums:
            cbody = objs.get(cn)
            out.append("CONTENT OBJ %s head=%r" % (cn, cbody[:160]))
            dec = objs.decode(cbody)
            out.append("  decoded len=%s" % (len(dec) if dec else None))
            if dec:
                out.append("  decoded head=%r" % dec[:400])

        # Resources
        rm = re.search(rb"/Resources\s*(\d+)\s+\d+\s+R", body)
        out.append("Resources ref: %r" % (rm.group(0) if rm else None))
        rbody = objs.get(int(rm.group(1))) if rm else b""
        if not rm:
            rm2 = re.search(rb"/Resources\s*(<<.*?>>)", body, re.S)
            rbody = rm2.group(1) if rm2 else b""
        out.append("RESOURCES body[:400]=%r" % rbody[:400])

        fm = re.search(rb"/Font\s*(\d+)\s+\d+\s+R", rbody)
        fbody = objs.get(int(fm.group(1))) if fm else b""
        if not fm:
            fm2 = re.search(rb"/Font\s*(<<.*?>>)", rbody, re.S)
            fbody = fm2.group(1) if fm2 else b""
        out.append("FONT DICT=%r" % fbody[:300])

        for name, num in re.findall(rb"/([A-Za-z0-9]+)\s+(\d+)\s+\d+\s+R", fbody):
            fb = objs.get(int(num))
            out.append("  FONT %s -> obj %s  body[:400]=%r" % (name.decode(), num.decode(), fb[:400]))
            tu = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", fb)
            out.append("    ToUnicode ref: %r" % (tu.group(0) if tu else None))
            if tu:
                tb = objs.get(int(tu.group(1)))
                td = objs.decode(tb)
                out.append("    ToUnicode decoded len=%s head=%r" % (
                    len(td) if td else None, (td or b"")[:300]))
                if td:
                    cmap, nb = parsers._parse_cmap(td)
                    out.append("    cmap size=%d nbytes=%s sample=%s" % (
                        len(cmap), nb, list(cmap.items())[:6]))
        out.append("  _font_maps() -> keys=%s" % list(parsers._font_maps(objs, body).keys()))


for fn in sys.argv[1:]:
    dump(fn)

with io.open(os.path.join(ROOT, "verify", "pdf_debug.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(str(x) for x in out))
print("ok")
