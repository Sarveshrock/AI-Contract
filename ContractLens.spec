# PyInstaller spec — build with:  pyinstaller ContractLens.spec --noconfirm
# Produces dist/ContractLens/ContractLens.exe (one-folder build; recommended because ChromaDB/onnx ship native libraries).
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("chromadb", "pymupdf", "pyqtgraph", "tzdata", "docx"):
    d, b, h = collect_all(pkg)
    if pkg == "pyqtgraph":  # its bundled demo programs pull in many unrelated libraries and slow the build a lot
        d = [x for x in d if "examples" not in x[0].replace("\\", "/")]
        h = [x for x in h if ".examples" not in x]
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("app")
datas += [("migrations", "migrations"), ("data/samples", "data/samples"), (".env.example", ".")]

a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports, excludes=["tkinter", "pytest", "PyQt5", "PySide2", "PySide6", "matplotlib", "IPython", "notebook", "torch", "tensorflow", "pyqtgraph.examples"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ContractLens", console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ContractLens")
