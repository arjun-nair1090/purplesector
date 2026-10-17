# PyInstaller build of PurpleSector.exe (one folder). Run scripts\build_exe.ps1 rather than this directly.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH is set by PyInstaller)

datas, binaries, hidden = [], [], []
for pkg in ("streamlit", "plotly"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
datas += copy_metadata("streamlit") + copy_metadata("plotly") + copy_metadata("pandas") + copy_metadata("numpy")
hidden += collect_submodules("comtypes") + ["comtypes.client"]
# the app itself: app.py runs as a script inside Streamlit, so it and its assets travel as data
datas += [(str(ROOT / "app.py"), "."), (str(ROOT / ".streamlit" / "config.toml"), ".streamlit"),
          (str(ROOT / "static"), "static"), (str(ROOT / "assets"), "assets"),
          (str(ROOT / "scripts" / "diagnostics.py"), "scripts")]

a = Analysis([str(ROOT / "packaging" / "launcher.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=["pytest", "ruff", "tkinter.test"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PurpleSector", console=True,
          icon=str(ROOT / "assets" / "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="PurpleSector")
