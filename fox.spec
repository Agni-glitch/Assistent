# PyInstaller recipe for Fox.exe. Run it with scripts\build.ps1.
#
# One folder rather than one file: Fox bundles hundreds of MB, and a one-file exe would unpack all of it
# on every launch. Never add .env or data here, because they hold your keys and chats.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "fox" / "gui" / "static"), "fox/gui/static"),
    (str(ROOT / ".env.example"), "defaults"),
    (str(ROOT / "agents"), "defaults/agents"),
]
# Only the shared examples. Plugins you write for yourself stay on your computer, like they stay out of git.
datas += [(str(path), "defaults/plugins") for path in sorted((ROOT / "plugins").glob("_example*.py"))]
binaries = []
# Fox and LangChain import these by name while running, so PyInstaller can't find them by reading the code.
hiddenimports = collect_submodules("fox") + ["pyttsx3.drivers.sapi5", "tiktoken_ext.openai_public", "tiktoken_ext"]

for package in (
    "langchain_core", "langchain_openai", "langchain_google_genai", "langchain_groq", "langchain_anthropic",
    "langchain_ollama", "openai", "anthropic", "groq", "ollama", "google.genai", "tiktoken",
    "faster_whisper", "ctranslate2", "tokenizers", "onnxruntime", "av", "sounddevice", "miniaudio", "pyttsx3",
    "edge_tts", "comtypes", "pycaw", "screen_brightness_control", "winotify", "ddgs", "primp", "docx", "webview",
):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

analysis = Analysis(
    [str(ROOT / "scripts" / "fox_app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["pytest", "IPython", "matplotlib", "notebook", "torch", "tensorflow", "transformers", "PyInstaller"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Fox",
    console=False,
    icon=str(ROOT / "assets" / "fox.ico"),
    upx=False,
)
COLLECT(exe, analysis.binaries, analysis.datas, name="Fox", upx=False)
