# PyInstaller spec — build with:  pyinstaller ContractLens.spec --noconfirm
# Produces dist/ContractLens/ContractLens.exe (one-folder build; recommended because ChromaDB/onnx ship native libraries).
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("chromadb", "pymupdf", "pyqtgraph", "tzdata", "docx"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("app")
datas += [("migrations", "migrations"), ("data/samples", "data/samples"), (".env.example", ".")]

a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports, excludes=["tkinter", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ContractLens", console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ContractLens")
