"""Remove axis ids that a chart references but never declares (pptxgenjs quirk that makes PowerPoint reject the file)."""
import re
import shutil
import sys
import tempfile
import zipfile

src = sys.argv[1]
tmp = tempfile.mktemp(suffix=".pptx")
with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    # [Content_Types].xml first, no directory entries
    items = sorted((i for i in zin.infolist() if not i.filename.endswith("/")), key=lambda i: i.filename != "[Content_Types].xml")
    for item in items:
        data = zin.read(item.filename)
        if re.match(r"ppt/charts/chart\d+\.xml$", item.filename):
            s = data.decode("utf-8")
            declared = set(re.findall(r"<c:(?:catAx|valAx|serAx|dateAx)>\s*<c:axId val=\"(\d+)\"", s))

            def keep(m):
                return m.group(0) if m.group(1) in declared else ""

            # only touch axId references inside chart-type elements, not the axis declarations themselves
            def clean_block(b):
                return re.sub(r"<c:axId val=\"(\d+)\"/>", keep, b.group(0))

            s2 = re.sub(r"<c:(bar|line|area|scatter|pie|doughnut|radar|bubble)Chart>.*?</c:\1Chart>", clean_block, s, flags=re.S)
            removed = s.count("<c:axId") - s2.count("<c:axId")
            if removed:
                print(f"{item.filename}: removed {removed} undeclared axis id(s)")
            data = s2.encode("utf-8")
        zout.writestr(item, data)
shutil.move(tmp, src)
