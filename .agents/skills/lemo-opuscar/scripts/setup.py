#!/usr/bin/env python3
"""Cross-platform launcher for sibling setup.sh (POSIX sh, Windows Git Bash / MSYS2 / WSL).

Passes all CLI arguments verbatim to setup.sh, configures an LF-safe environment,
and exits with setup.sh's exit code (or prints install guidance to stderr).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

INTERNAL_WSL_DISTROS = frozenset({
    'docker-desktop',
    'docker-desktop-data',
    'rancher-desktop',
    'rancher-desktop-data',
})

WSL_DISTRO_RE = re.compile(r'^\s*(\*?)\s*(.+?)\s+\S+\s+\d+\s*$')


def _is_executable(path):
    """Return True if `path` exists as a regular runnable file."""
    if not path or not os.path.isfile(path):
        return False
    if os.name == 'nt':
        return True
    return os.access(path, os.X_OK)


def _resolve_command(cmd):
    """Resolve an explicit command name or path from an env override like SH."""
    if not cmd:
        return None
    has_sep = os.sep in cmd or (os.altsep and os.altsep in cmd)
    if has_sep or os.path.isabs(cmd):
        if _is_executable(cmd):
            return os.path.abspath(cmd)
        return shutil.which(cmd)
    return shutil.which(cmd)


def _first_existing(candidates):
    """Return the first executable file path from `candidates`, preserving order."""
    seen = set()
    for path in candidates:
        if not path:
            continue
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        if _is_executable(path):
            return os.path.abspath(path)
    return None


def _windows_sh_candidates():
    """Collect standard Windows Git Bash, MSYS2, and Cygwin sh.exe candidate paths."""
    candidates = []
    env = os.environ

    prog_dirs = [
        env.get('ProgramFiles', ''),
        env.get('ProgramW6432', ''),
        env.get('ProgramFiles(x86)', ''),
    ]
    for base in prog_dirs:
        if base:
            candidates.append(os.path.join(base, 'Git', 'bin', 'sh.exe'))
            candidates.append(os.path.join(base, 'Git', 'usr', 'bin', 'sh.exe'))

    local_app = env.get('LocalAppData', '')
    if local_app:
        candidates.append(os.path.join(local_app, 'Programs', 'Git', 'bin', 'sh.exe'))
        candidates.append(os.path.join(local_app, 'Programs', 'Git', 'usr', 'bin', 'sh.exe'))
        candidates.append(os.path.join(local_app, 'Git', 'bin', 'sh.exe'))
        candidates.append(os.path.join(local_app, 'Git', 'usr', 'bin', 'sh.exe'))

    user_profile = env.get('UserProfile', '')
    if user_profile:
        candidates.append(os.path.join(user_profile, 'scoop', 'apps', 'git', 'current', 'bin', 'sh.exe'))
        candidates.append(os.path.join(user_profile, 'scoop', 'apps', 'git', 'current', 'usr', 'bin', 'sh.exe'))

    git_exe = shutil.which('git')
    if git_exe:
        for git_path in (git_exe, os.path.realpath(git_exe)):
            parent = os.path.dirname(git_path)
            root = os.path.dirname(parent)
            for base in (root, parent):
                candidates.append(os.path.join(base, 'bin', 'sh.exe'))
                candidates.append(os.path.join(base, 'usr', 'bin', 'sh.exe'))

    # MSYS2 / Cygwin locations
    sys_drive = env.get('SystemDrive', '')
    drive_root = sys_drive.rstrip('\\/') + os.sep if sys_drive else ''
    if drive_root:
        candidates.append(os.path.join(drive_root, 'msys64', 'usr', 'bin', 'sh.exe'))
        candidates.append(os.path.join(drive_root, 'msys32', 'usr', 'bin', 'sh.exe'))
        candidates.append(os.path.join(drive_root, 'cygwin64', 'bin', 'sh.exe'))
        candidates.append(os.path.join(drive_root, 'cygwin', 'bin', 'sh.exe'))

    if local_app:
        candidates.append(os.path.join(local_app, 'msys64', 'usr', 'bin', 'sh.exe'))
        candidates.append(os.path.join(local_app, 'Programs', 'msys64', 'usr', 'bin', 'sh.exe'))

    for base in prog_dirs:
        if base:
            candidates.append(os.path.join(base, 'msys64', 'usr', 'bin', 'sh.exe'))

    if user_profile:
        candidates.append(os.path.join(user_profile, 'msys64', 'usr', 'bin', 'sh.exe'))
        candidates.append(os.path.join(user_profile, 'scoop', 'apps', 'msys2', 'current', 'usr', 'bin', 'sh.exe'))

    return candidates


def _find_windows_sh():
    """Locate a native Windows POSIX sh.exe (SH override, PATH, Git Bash, MSYS2/Cygwin)."""
    sh_override = _resolve_command(os.environ.get('SH', '').strip())
    if sh_override:
        return sh_override

    on_path = shutil.which('sh')
    if on_path and _is_executable(on_path):
        return on_path

    return _first_existing(_windows_sh_candidates())


def _decode_wsl_output(raw):
    """Decode wsl.exe output (UTF-16-LE on older builds, UTF-8 when WSL_UTF8=1)."""
    if not raw:
        return ''
    if b'\x00' in raw:
        return raw.decode('utf-16-le', errors='replace').lstrip('\ufeff')
    return raw.decode('utf-8', errors='replace').lstrip('\ufeff')


def _find_wsl():
    """Return (wsl_exe, distro_name) if a usable WSL Linux distribution is installed."""
    env = os.environ
    wsl_candidates = [shutil.which('wsl') or shutil.which('wsl.exe')]
    local_app = env.get('LocalAppData', '')
    if local_app:
        wsl_candidates.append(os.path.join(local_app, 'Microsoft', 'WindowsApps', 'wsl.exe'))
    sys_root = env.get('SystemRoot', '')
    if sys_root:
        wsl_candidates.append(os.path.join(sys_root, 'System32', 'wsl.exe'))
        wsl_candidates.append(os.path.join(sys_root, 'Sysnative', 'wsl.exe'))

    wsl_exe = _first_existing(wsl_candidates)
    if not wsl_exe:
        return None

    probe_env = env.copy()
    probe_env['WSL_UTF8'] = '1'
    try:
        proc = subprocess.run(
            [wsl_exe, '--list', '--verbose'],
            capture_output=True,
            env=probe_env,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if proc.returncode != 0:
        return None

    text = _decode_wsl_output(proc.stdout)
    default_distro = None
    first_usable = None
    for line in text.splitlines():
        match = WSL_DISTRO_RE.match(line)
        if not match:
            continue
        is_default = bool(match.group(1))
        name = match.group(2).strip()
        if not name or name.lower() in INTERNAL_WSL_DISTROS:
            continue
        if is_default:
            default_distro = name
            break
        if first_usable is None:
            first_usable = name

    distro = default_distro or first_usable
    if not distro:
        return None
    return wsl_exe, distro


def _find_posix_sh():
    """Locate POSIX sh on macOS / Linux."""
    sh_override = _resolve_command(os.environ.get('SH', '').strip())
    if sh_override:
        return sh_override

    on_path = shutil.which('sh')
    if on_path and _is_executable(on_path):
        return on_path

    return _first_existing(['/bin/sh', '/usr/bin/sh'])


def _lf_safe_script(setup_sh):
    """Return (path_to_run, is_temp) ensuring the executed script has LF line endings."""
    with open(setup_sh, 'rb') as f:
        raw = f.read()
    if b'\r\n' not in raw:
        return setup_sh, False
    fd, tmp_path = tempfile.mkstemp(prefix='lemo-setup-', suffix='.sh')
    try:
        os.write(fd, raw.replace(b'\r\n', b'\n'))
    finally:
        os.close(fd)
    return tmp_path, True


def _build_native_env(sh_exe):
    """Build an LF-safe, UTF-8-safe environment for native POSIX sh or Git Bash/MSYS2."""
    env = os.environ.copy()
    env.pop('CDPATH', None)

    if os.name == 'nt':
        opts = [o for o in env.get('SHELLOPTS', '').split(':') if o]
        if 'igncr' not in opts:
            opts.append('igncr')
        env['SHELLOPTS'] = ':'.join(opts)

        env.setdefault('PYTHONUTF8', '1')
        env.setdefault('PYTHONIOENCODING', 'utf-8')

        sh_dir = os.path.dirname(os.path.abspath(sh_exe))
        path_val = env.get('PATH', '')
        parts = [p for p in path_val.split(os.pathsep) if p]
        norm_dir = os.path.normcase(os.path.normpath(sh_dir))
        if not any(os.path.normcase(os.path.normpath(p)) == norm_dir for p in parts):
            parts.append(sh_dir)
            env['PATH'] = os.pathsep.join(parts)

    return env


def _build_wsl_env():
    """Build environment for running setup.sh through WSL."""
    env = os.environ.copy()
    env.pop('CDPATH', None)

    wslenv = [e for e in env.get('WSLENV', '').split(':') if e]
    if 'LEMO_OPUSCAR_HOME' in env:
        val = env['LEMO_OPUSCAR_HOME']
        spec = 'LEMO_OPUSCAR_HOME/p' if ((len(val) >= 2 and val[1] == ':') or '\\' in val) else 'LEMO_OPUSCAR_HOME'
        if not any(e == 'LEMO_OPUSCAR_HOME' or e.startswith('LEMO_OPUSCAR_HOME/') for e in wslenv):
            wslenv.append(spec)
    if 'LEMO_OPUSCAR_REPO' in env:
        if not any(e == 'LEMO_OPUSCAR_REPO' or e.startswith('LEMO_OPUSCAR_REPO/') for e in wslenv):
            wslenv.append('LEMO_OPUSCAR_REPO')
    if wslenv:
        env['WSLENV'] = ':'.join(wslenv)

    return env


def _no_shell_message():
    """Return clear stderr instructions when no POSIX sh or WSL is available."""
    if os.name == 'nt':
        return (
            "Error: No POSIX 'sh' (Git Bash / MSYS2 / Cygwin) or usable WSL Linux distribution was found.\n"
            "\n"
            "Install one of the following and re-run this command:\n"
            "  1. Git for Windows (includes Git Bash, recommended):\n"
            "       winget install --id Git.Git -e --source winget\n"
            "     or download from https://git-scm.com/download/win\n"
            "  2. Windows Subsystem for Linux (WSL):\n"
            "       wsl --install\n"
            "     (then launch the installed Linux distro once to complete setup)\n"
            "  3. MSYS2: https://www.msys2.org/\n"
            "\n"
            "If sh.exe is already installed in a custom location, set the SH environment variable\n"
            "to its full path (for example in PowerShell: $env:SH = 'C:\\path\\to\\Git\\bin\\sh.exe')."
        )
    return (
        "Error: No POSIX 'sh' executable was found (checked SH, PATH, /bin/sh, /usr/bin/sh).\n"
        "Ensure a POSIX shell is installed or set SH=/path/to/sh and re-run."
    )


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    script_dir = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
    setup_sh = os.path.join(script_dir, 'setup.sh')
    if not os.path.isfile(setup_sh):
        print(f'Error: sibling setup.sh not found at {setup_sh}', file=sys.stderr)
        return 1

    if os.name == 'nt':
        sh_exe = _find_windows_sh()
        wsl_info = None if sh_exe else _find_wsl()
        if not sh_exe and not wsl_info:
            print(_no_shell_message(), file=sys.stderr)
            return 1
    else:
        sh_exe = _find_posix_sh()
        wsl_info = None
        if not sh_exe:
            print(_no_shell_message(), file=sys.stderr)
            return 1

    run_script, is_temp = _lf_safe_script(setup_sh)
    try:
        if sh_exe:
            cmd = [sh_exe, run_script] + args
            env = _build_native_env(sh_exe)
        else:
            wsl_exe, distro = wsl_info
            cmd = [
                wsl_exe,
                '-d',
                distro,
                '-e',
                'sh',
                '-c',
                'p=$(wslpath -u "$1") || exit 1; shift; exec sh "$p" "$@"',
                'sh',
                run_script,
            ] + args
            env = _build_wsl_env()

        proc = subprocess.run(cmd, env=env)
        return proc.returncode
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f'Error: failed to launch setup.sh: {exc}', file=sys.stderr)
        return 1
    finally:
        if is_temp:
            try:
                os.unlink(run_script)
            except OSError:
                pass


if __name__ == '__main__':
    sys.exit(main())
