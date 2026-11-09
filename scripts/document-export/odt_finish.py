# -*- coding: utf-8 -*-
"""Update the contents index of .odt files and export each to PDF.

Run with LibreOffice's bundled python (it carries the `uno` module):

    python.exe odt_finish.py [--config FILE]
        build every [[document]] in the config from its Markdown, then finish it

    python.exe odt_finish.py [--config FILE] <file.odt> [<file.odt> ...] [<pdf dir>]
        finish existing .odt files only

Finishing = open hidden, update all indexes (twice - filling the contents can
move page breaks), save the .odt in place, write <name>.pdf with clickable
contents and heading bookmarks. A private LibreOffice profile is used, so an
open LibreOffice window is not disturbed.
"""
import os, subprocess, sys, tempfile, time
import uno
from com.sun.star.beans import PropertyValue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import md2odt                                          # config loading + Markdown -> ODT

SOFFICE = os.path.join(os.path.dirname(sys.executable), "soffice.exe")
PIPE = "wikicrop_odt_finish"

def prop(name, value):
    p = PropertyValue(); p.Name = name; p.Value = value
    return p

def connect():
    profile = uno.systemPathToFileUrl(os.path.join(tempfile.gettempdir(), "wikicrop-lo-profile"))
    proc = subprocess.Popen([SOFFICE, "--headless", "--invisible", "--nologo", "--norestore",
                             "--nodefault", "-env:UserInstallation=" + profile,
                             "--accept=pipe,name=%s;urp;" % PIPE])
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(120):
        try:
            ctx = resolver.resolve("uno:pipe,name=%s;urp;StarOffice.ComponentContext" % PIPE)
            return proc, ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("could not reach LibreOffice")

def finish(desktop, odt, pdf_dir, pdf_opts):
    doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(os.path.abspath(odt)),
                                       "_blank", 0, (prop("Hidden", True),))
    idx = doc.getDocumentIndexes()
    for _ in range(2):
        for i in range(idx.getCount()):
            idx.getByIndex(i).update()
        doc.refresh()
    doc.store()
    pdf_dir = pdf_dir or os.path.dirname(os.path.abspath(odt))
    os.makedirs(pdf_dir, exist_ok=True)
    pdf = os.path.join(pdf_dir, os.path.splitext(os.path.basename(odt))[0] + ".pdf")
    fd = uno.Any("[]com.sun.star.beans.PropertyValue", tuple([
        prop("ExportBookmarks", bool(pdf_opts["bookmarks"])),
        prop("UseTaggedPDF", bool(pdf_opts["tagged"])),
        prop("ExportLinksRelativeFsys", False)]))
    uno.invoke(doc, "storeToURL", (uno.systemPathToFileUrl(os.path.abspath(pdf)),
               (prop("FilterName", "writer_pdf_Export"), prop("FilterData", fd))))
    doc.close(True)
    return pdf

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")   # Vietnamese file names on a Windows console
    args = sys.argv[1:]
    cfg = md2odt.load_config(md2odt.pop_config(args))
    odts = [a for a in args if a.lower().endswith(".odt")]
    dirs = [a for a in args if not a.lower().endswith(".odt")]
    if odts:
        jobs = [(o, dirs[-1] if dirs else None) for o in odts]
    else:
        jobs = []
        for d in md2odt.documents(cfg):
            out, n = md2odt.make(d["source"], d["odt"], d["title"], d["cut"], d["contents_label"])
            print("wrote %s (%d headings)" % (out, n))
            jobs.append((d["odt"], d["pdf_dir"]))
        if not jobs:
            raise SystemExit("no [[document]] entries in the config, and no files given")
    proc, desktop = connect()
    try:
        for odt, pdf_dir in jobs:
            print("finished", odt, "->", finish(desktop, odt, pdf_dir, cfg["pdf"]))
    finally:
        try: desktop.terminate()
        except Exception: pass
        proc.wait(timeout=30)
