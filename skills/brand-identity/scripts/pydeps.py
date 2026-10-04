"""Python dependencies without a manual install: re-run the calling script inside an environment that has them.

`ensure()` is called at the top of every entry script whose command line needs the font/outline packages
(brand.py, logo_audit.py, identity_card.py, identity_board.py, kit_build.py). When one of them is missing it
re-runs the same script with the same arguments:

  1. through `uv run --no-project --with-requirements requirements.txt` (uv found on PATH or in its default install
     folders): an isolated environment that uv builds from its cache, outside any project; nothing is installed
     into the user's Python. A first `uv run` imports the packages so a failure (offline first run) is reported
     instead of ending the command;
  2. else through the venv at ${XDG_CACHE_HOME:-~/.cache}/brand-identity/venv when it exists (the pip fallback
     that `brand.py check` prints);
  3. else it does nothing, and `brand.py check` explains what is missing and prints the install lines.

uv itself is never downloaded or installed here. BRAND_IDENTITY_ENV marks the re-run (no second hop, `check` names
the environment). Child processes started with sys.executable inherit the environment the script runs in.
Standard library only; importable on any Python 3 so the hop also works from an older interpreter.
"""
import importlib.util
import os
import shutil
import subprocess
import sys

ENV_FLAG = "BRAND_IDENTITY_ENV"
# module, requirement, what degrades without it (brand.py check prints these)
MODULES = (
    ("fontTools", "fonttools[woff]>=4.50", "font audit, wordmark outlines, kit fonts"),
    ("brotli", "fonttools[woff]>=4.50", "reading .woff2 font files"),
    ("uharfbuzz", "uharfbuzz>=0.40", "kerned wordmark outlines"),
    ("pathops", "skia-pathops>=0.8", "boolean shapes in symbols, false-hole checks"),
)
UV_INSTALL = {
    "posix": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    "nt": 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
}


def missing():
    """Modules from MODULES that this interpreter cannot import."""
    return [m for m, _, _ in MODULES if importlib.util.find_spec(m) is None]


def requirements():
    """requirements.txt beside the skill (zip install) or at the repo root (clone, plugin, symlinked skill)."""
    skill = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    for path in (os.path.join(skill, "requirements.txt"),
                 os.path.join(os.path.dirname(os.path.dirname(skill)), "requirements.txt")):
        if os.path.isfile(path):
            return path
    return None


def find_uv():
    """uv on PATH, else in the folders its installers use (a fresh install before the shell reloads PATH)."""
    found = shutil.which("uv")
    if found:
        return found
    home = os.path.expanduser("~")
    exe = "uv.exe" if os.name == "nt" else "uv"
    dirs = [os.environ.get("UV_INSTALL_DIR"), os.environ.get("XDG_BIN_HOME"), os.path.join(home, ".local", "bin"),
            os.path.join(home, ".cargo", "bin")]
    for d in filter(None, dirs):
        p = os.path.join(d, exe)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def venv_dir():
    """Where the pip fallback puts its venv (brand.py check prints the commands)."""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return os.path.join(os.environ["LOCALAPPDATA"], "brand-identity", "venv")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "brand-identity", "venv")


def venv_python():
    d = venv_dir()
    p = os.path.join(d, "Scripts", "python.exe") if os.name == "nt" else os.path.join(d, "bin", "python")
    return p if os.path.isfile(p) else None


def uv_command(uv, *command):
    """`uv run` line that runs `command` in a cached environment built from requirements.txt (outside any project).
    The current interpreter is the base when it is 3.10+, else uv picks one (>= 3.10)."""
    req = requirements()
    deps = ["--with-requirements", req] if req else [x for _, r, _ in MODULES for x in ("--with", r)]
    py = sys.executable if sys.version_info >= (3, 10) and sys.executable else ">=3.10"
    return [uv, "run", "--no-project", "--quiet", "--python", py, *deps, *command]


def uv_ready(uv):
    """Build (or reuse from uv's cache) the environment once and import the packages in it: (True, None) or
    (False, uv's last error line). Separate from the re-run so a failure (offline first run) is reported here."""
    probe = "import fontTools, brotli, uharfbuzz, pathops"
    try:
        r = subprocess.run(uv_command(uv, "python", "-c", probe), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if r.returncode == 0:
        return True, None
    return False, (r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1]


def install_lines():
    """What `brand.py check` prints when packages are missing: uv first, then a pip venv that never touches the
    system Python (PEP 668 safe)."""
    req = requirements() or "requirements.txt"
    d = venv_dir()
    py = os.path.join(d, "Scripts", "python.exe") if os.name == "nt" else os.path.join(d, "bin", "python")
    base = "py -3" if os.name == "nt" else "python3"
    return [f"install uv (then run the command again; it sets itself up): {UV_INSTALL.get(os.name, UV_INSTALL['posix'])}",
            f"or with pip, in a venv the scripts find by themselves: {base} -m venv \"{d}\" && \"{py}\" -m pip install "
            f"-r \"{req}\""]


def _run(cmd):
    """Replace this process with cmd (POSIX), or run it and exit with its code (Windows: exec would return early)."""
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        try:
            code = subprocess.run(cmd).returncode
        except KeyboardInterrupt:
            code = 130
        sys.exit(code)
    os.execv(cmd[0], cmd)


def ensure(script=None, argv=None):
    """Re-run `script` (default: the script being run) where the packages are, when any is missing here.
    Returns only when nothing is missing, when this is already the re-run, or when no environment can be found."""
    if os.environ.get(ENV_FLAG) or not missing():
        return
    script = os.path.abspath(script or sys.argv[0])
    args = list(sys.argv[1:] if argv is None else argv)
    cmd, kind = None, None
    uv = find_uv()
    if uv:
        ok, err = uv_ready(uv)
        if ok:
            cmd, kind = uv_command(uv, "python", script, *args), "uv"
        else:  # offline first run, no matching Python...: say so, then try the venv, else run as is
            print(f"note: uv could not prepare the Python packages ({err}); continuing without them", file=sys.stderr)
    if not cmd:
        venv = venv_python()
        if venv and os.path.realpath(venv) != os.path.realpath(sys.executable):
            cmd, kind = [venv, script, *args], "venv"
    if not cmd:
        return
    os.environ[ENV_FLAG] = kind
    try:
        _run(cmd)
    except OSError as exc:  # could not start: carry on here, check explains what is missing
        os.environ.pop(ENV_FLAG, None)
        print(f"note: could not start {cmd[0]}: {exc}", file=sys.stderr)
