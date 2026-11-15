#!/usr/bin/env python3
"""Validate Lemo-Opuscar multi-agent skill packaging, sync parity, docs, and plugin builds.

    python3 tools/validate_skills.py             run static + sync + packaging checks
    python3 tools/validate_skills.py --with-cli  also invoke installed agent validation CLIs (claude, tools/directory.py)
"""
import argparse
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import directory  # noqa: E402
import sync_skills  # noqa: E402

REQUIRED_PLATFORMS = (
    'Google Antigravity',
    'Gemini CLI',
    'OpenAI Codex CLI',
    'OpenCode',
    'GitHub Copilot',
    'Claude Code',
)

SKILL_ROOTS = [
    ('.agents/skills/lemo-opuscar', True),
    ('plugin/skills/lemo-opuscar', True),
    ('tools/directory', False),  # Claude Plugin Directory source dir; copied to skills/lemo-opuscar in builds
]


def parse_frontmatter(text, rel_path):
    """Parse YAML frontmatter header without requiring third-party YAML packages."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        raise ValueError(f'{rel_path}: missing opening YAML frontmatter delimiter ---')
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == '---':
            end = idx
            break
    if end is None:
        raise ValueError(f'{rel_path}: missing closing YAML frontmatter delimiter ---')

    meta = {}
    i = 1
    while i < end:
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if ':' not in line or line[0].isspace():
            raise ValueError(f'{rel_path}: unexpected frontmatter syntax on line {i}: {line!r}')
        key, val = line.split(':', 1)
        key = key.strip()
        val = val.strip()
        if not key:
            raise ValueError(f'{rel_path}: empty frontmatter key on line {i}')
        if val in ('|', '>', '|-', '>-'):
            block = []
            while i < end and (not lines[i].strip() or lines[i].startswith((' ', '\t'))):
                block.append(lines[i].strip())
                i += 1
            val = ' '.join(part for part in block if part)
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key in meta:
            raise ValueError(f'{rel_path}: duplicate frontmatter key {key!r}')
        meta[key] = val
    return meta, '\n'.join(lines[end + 1:])


def compile_python(rel_path, errors, tmp_dir):
    """Compile a Python file into a temp .pyc to verify syntax without dirtying __pycache__."""
    full_path = os.path.join(ROOT, rel_path.replace('/', os.sep))
    if not os.path.isfile(full_path) or os.path.getsize(full_path) == 0:
        errors.append(f'missing or empty {rel_path}')
        return False
    cfile = os.path.join(tmp_dir, rel_path.replace('/', '_') + 'c')
    try:
        py_compile.compile(full_path, cfile=cfile, doraise=True)
        return True
    except (py_compile.PyCompileError, OSError) as e:
        errors.append(f'{rel_path}: py_compile failed: {e}')
        return False


def check_skill_dirs(errors, tmp_dir):
    """Validate SKILL.md frontmatter, folder name, scripts, and relative script/doc references."""
    for rel_dir, strict_dir_match in SKILL_ROOTS:
        skill_dir = os.path.join(ROOT, rel_dir)
        skill_md = os.path.join(skill_dir, 'SKILL.md')
        if not os.path.isfile(skill_md):
            errors.append(f'missing {rel_dir}/SKILL.md')
            continue

        text = open(skill_md, encoding='utf-8').read()
        try:
            meta, body = parse_frontmatter(text, f'{rel_dir}/SKILL.md')
        except ValueError as e:
            errors.append(str(e))
            continue

        name = meta.get('name', '')
        desc = meta.get('description', '')
        if not name:
            errors.append(f'{rel_dir}/SKILL.md: missing frontmatter field `name`')
        else:
            if len(name) > 64:
                errors.append(f'{rel_dir}/SKILL.md: `name` exceeds 64 chars ({len(name)})')
            if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', name):
                errors.append(f'{rel_dir}/SKILL.md: `name` must be lowercase alphanumeric + single hyphens, got {name!r}')
            if strict_dir_match and name != os.path.basename(skill_dir):
                errors.append(f'{rel_dir}/SKILL.md: `name` ({name!r}) must match directory ({os.path.basename(skill_dir)!r})')

        if not desc:
            errors.append(f'{rel_dir}/SKILL.md: missing frontmatter field `description`')
        elif len(desc) > 1024:
            errors.append(f'{rel_dir}/SKILL.md: `description` exceeds 1024 chars ({len(desc)})')

        # Supporting setup script exists (plugin/canonical keep it in scripts/, tools/directory holds setup.sh at root).
        if strict_dir_match:
            script_rel = 'scripts/setup.sh'
        else:
            script_rel = 'setup.sh'
        script_path = os.path.join(skill_dir, script_rel)
        if not os.path.isfile(script_path) or os.path.getsize(script_path) == 0:
            errors.append(f'missing or empty {rel_dir}/{script_rel}')
        else:
            raw = open(script_path, 'rb').read()
            if b'\r\n' in raw:
                errors.append(f'{rel_dir}/{script_rel}: contains CRLF line endings')
            if shutil.which('sh'):
                r = subprocess.run(['sh', '-n', script_path], capture_output=True, text=True)
                if r.returncode != 0:
                    errors.append(f'{rel_dir}/{script_rel}: shell syntax error: {r.stderr.strip()}')

        if strict_dir_match:
            py_rel = f'{rel_dir}/scripts/setup.py'
            py_path = os.path.join(ROOT, py_rel.replace('/', os.sep))
            if os.path.islink(py_path):
                errors.append(f'{py_rel}: symlink not allowed')
            compile_python(py_rel, errors, tmp_dir)

        # Verify relative links referenced inside SKILL.md body resolve against skill_dir or repo root.
        for lib_ref in re.findall(r'\$LIB/([A-Za-z0-9_./-]+)', body):
            clean = lib_ref.rstrip('.,:)`')
            if '<' in clean or clean.endswith('/'):
                continue
            target = os.path.join(ROOT, clean)
            if not os.path.exists(target):
                errors.append(f'{rel_dir}/SKILL.md: referenced $LIB/{clean} does not exist in repo')
        if strict_dir_match:
            for skill_ref in re.findall(r'<skill directory>/([A-Za-z0-9_./-]+)', body):
                clean = skill_ref.rstrip('.,:)`')
                if '<' in clean:
                    continue
                target = os.path.join(skill_dir, clean)
                if not os.path.exists(target):
                    errors.append(f'{rel_dir}/SKILL.md: referenced <skill directory>/{clean} does not exist')


def check_portability(errors):
    """Ensure shared guides/tools avoid hard-coding Claude-only tool names or stale setup paths."""
    shared_docs = [
        '.agents/skills/lemo-opuscar/SKILL.md',
        'plugin/skills/lemo-opuscar/SKILL.md',
        'AGENTS.md',
        'MY-STYLES.md',
        'DIRECTOR.md',
        'TECHNIQUE.md',
        'core/README.md',
    ]
    for rel in shared_docs:
        text = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        if '<skill base directory>' in text:
            errors.append(f'{rel}: still references Claude-specific placeholder `<skill base directory>`')
        for m in re.finditer(r'AskUserQuestion', text):
            ctx = text[max(0, m.start() - 60):min(len(text), m.end() + 40)]
            if 'such as `AskUserQuestion`' not in ctx:
                errors.append(f'{rel}: unconditional reference to `AskUserQuestion` near {ctx!r}')

    no_stale_setup = shared_docs + [
        'core/render/page.mjs',
        'core/tts/tts.py',
        'core/tts/tts_zh.py',
        'core/tts/asr_check.py',
        'core/audio/pluck.py',
        'tools/fetch.sh',
        'README.md',
        'README.zh-CN.md',
    ]
    for rel in no_stale_setup:
        text = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        for stale in ('plugin/skills/lemo-opuscar/scripts/setup.sh', 'plugin/skills/lemo-opuscar/scripts/setup.py'):
            if stale in text:
                errors.append(f'{rel}: still references stale `{stale}` instead of `.agents/...`')


def check_gemini_settings(errors):
    """Verify .gemini/settings.json autoloads AGENTS.md without duplicating .gemini/skills/."""
    settings_path = os.path.join(ROOT, '.gemini', 'settings.json')
    if not os.path.isfile(settings_path):
        errors.append('missing .gemini/settings.json')
    else:
        try:
            data = json.load(open(settings_path, encoding='utf-8'))
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f'.gemini/settings.json: invalid JSON: {e}')
        else:
            ctx = data.get('context') if isinstance(data, dict) else None
            file_name = ctx.get('fileName') if isinstance(ctx, dict) else None
            has_agents = file_name == 'AGENTS.md' or (isinstance(file_name, list) and 'AGENTS.md' in file_name)
            if not has_agents:
                errors.append('.gemini/settings.json: expected `AGENTS.md` in `context.fileName`')

    dup_dir = os.path.join(ROOT, '.gemini', 'skills')
    if os.path.exists(dup_dir):
        errors.append('.gemini/skills/ should not exist (use .agents/skills/lemo-opuscar/ + .gemini/settings.json instead)')


def _fallback_check_workflow_yaml(text, rel_path, errors):
    """Minimal stdlib YAML check when PyYAML and Ruby are both absent (e.g. bare CI Python)."""
    if '\t' in text:
        errors.append(f'{rel_path}: tabs are not allowed in YAML')
        return
    top_keys = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if not raw[0].isspace():
            if ':' not in raw:
                errors.append(f'{rel_path}:{lineno}: expected top-level YAML mapping key, got {raw!r}')
                return
            top_keys.add(raw.split(':', 1)[0].strip())
    for required in ('name', 'on', 'jobs'):
        if required not in top_keys:
            errors.append(f'{rel_path}: missing required workflow key `{required}`')


def check_workflows_yaml(errors):
    """Verify .github/workflows/skills.yml exists and is valid YAML."""
    rel_path = '.github/workflows/skills.yml'
    wf_path = os.path.join(ROOT, '.github', 'workflows', 'skills.yml')
    if not os.path.isfile(wf_path) or os.path.getsize(wf_path) == 0:
        errors.append(f'missing or empty {rel_path}')
        return
    text = open(wf_path, encoding='utf-8').read()
    try:
        import yaml  # optional third-party parser
    except ImportError:
        yaml = None

    if yaml is not None:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            errors.append(f'{rel_path}: invalid YAML: {e}')
            return
        if not isinstance(data, dict) or 'jobs' not in data or ('on' not in data and True not in data):
            errors.append(f'{rel_path}: expected YAML mapping with `on` and `jobs`')
        return

    if shutil.which('ruby'):
        r = subprocess.run(
            ['ruby', '-ryaml', '-e',
             "d = YAML.safe_load(File.read(ARGV[0])); abort('invalid workflow') unless d.is_a?(Hash) && d.key?('jobs') && (d.key?('on') || d.key?(true))",
             wf_path],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            errors.append(f'{rel_path}: invalid YAML: {r.stderr.strip() or r.stdout.strip()}')
        return

    _fallback_check_workflow_yaml(text, rel_path, errors)


def check_python_tools(errors, tmp_dir):
    """Verify core/image/gen_image.py and styleboard modules compile and gen_image --help works."""
    if compile_python('core/image/gen_image.py', errors, tmp_dir):
        gen_path = os.path.join(ROOT, 'core', 'image', 'gen_image.py')
        r = subprocess.run([sys.executable, gen_path, '--help'], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            errors.append(f'core/image/gen_image.py --help failed (exit {r.returncode}): {r.stderr.strip()}')
        elif '--provider' not in r.stdout:
            errors.append('core/image/gen_image.py --help output missing `--provider`')

    for rel in ('styleboard/gallery.py', 'styleboard/home.py', 'styleboard/pages.py'):
        compile_python(rel, errors, tmp_dir)


def check_directory_edits(errors):
    """Verify every tools/directory.py EDITS replacement matches cleanly in memory."""
    fetch_sh_path = os.path.join(ROOT, 'tools', 'fetch.sh')
    if not os.path.isfile(fetch_sh_path):
        errors.append('missing tools/fetch.sh for tools/directory.py EDITS check')
        return
    fetch_sh = open(fetch_sh_path, encoding='utf-8').read()
    matches = directory.FETCH_HEREDOC.findall(fetch_sh)
    m = directory.FETCH_HEREDOC.search(fetch_sh)
    if not m or len(matches) != 1:
        errors.append('tools/fetch.sh: FETCH_HEREDOC in tools/directory.py no longer matches uniquely')
        return
    virtual_files = {
        'tools/fetch.sh': fetch_sh[:m.start()] + '"$PY" ' + m.group(1) + 'tools/fetch.py $PACKS\n' + fetch_sh[m.end():],
        'tools/fetch.py': '# Downloads the asset packs named on the command line from the "assets" release; run by tools/fetch.sh.\n' + m.group(2),
    }

    setup_targets = dict(directory.SETUP_OLD)
    for expected_old, expected_new in (
        ('.agents/skills/lemo-opuscar/scripts/setup.sh', 'tools/setup.sh'),
        ('plugin/skills/lemo-opuscar/scripts/setup.sh', 'tools/setup.sh'),
        ('.agents/skills/lemo-opuscar/scripts/setup.py', 'tools/setup.py'),
        ('plugin/skills/lemo-opuscar/scripts/setup.py', 'tools/setup.py'),
    ):
        if setup_targets.get(expected_old) != expected_new:
            errors.append(f'tools/directory.py SETUP_OLD missing rewrite {expected_old!r} -> {expected_new!r}')

    contents = {}
    for rel, old, new in directory.EDITS:
        if rel not in contents:
            if rel in virtual_files:
                text = virtual_files[rel]
            else:
                full = os.path.join(ROOT, rel.replace('/', os.sep))
                if not os.path.isfile(full):
                    errors.append(f'tools/directory.py EDITS target missing: {rel}')
                    continue
                text = open(full, encoding='utf-8').read()
            for old_setup, new_setup in directory.SETUP_OLD:
                text = text.replace(old_setup, new_setup)
            contents[rel] = text

        count = contents[rel].count(old)
        if count != 1:
            errors.append(f'tools/directory.py EDITS mismatch in {rel} (count={count}): {old[:100]!r}')
            continue
        contents[rel] = contents[rel].replace(old, new)

    for rel, text in contents.items():
        for leftover in ('.agents/skills', 'plugin/skills'):
            if leftover in text:
                errors.append(f'tools/directory.py rewrite leaves `{leftover}` in {rel}')

    lic_path = os.path.join(ROOT, 'LICENSE')
    if not os.path.isfile(lic_path):
        errors.append('missing LICENSE for tools/directory.py LICENSE_EDIT check')
    elif open(lic_path, encoding='utf-8').read().count(directory.LICENSE_EDIT[0]) != 1:
        errors.append('tools/directory.py LICENSE_EDIT no longer matches LICENSE uniquely')


def _verify_directory_build(rel_dir_out, errors):
    """Verify packaged setup.py copies, zero leaked agent/plugin dirs, and clean setup rewrites."""
    src_setup_py = os.path.join(ROOT, '.agents', 'skills', 'lemo-opuscar', 'scripts', 'setup.py')
    src_bytes = open(src_setup_py, 'rb').read() if os.path.isfile(src_setup_py) else b''
    for built_rel in ('skills/lemo-opuscar/scripts/setup.py', 'skills/lemo-opuscar/library/tools/setup.py'):
        built_path = os.path.join(rel_dir_out, built_rel.replace('/', os.sep))
        if not os.path.isfile(built_path):
            errors.append(f'directory build missing {built_rel}')
        elif src_bytes and open(built_path, 'rb').read() != src_bytes:
            errors.append(f'directory build {built_rel} differs from .agents/skills/lemo-opuscar/scripts/setup.py')

    for dirpath, dirnames, names in os.walk(rel_dir_out):
        rel_dir = os.path.relpath(dirpath, rel_dir_out)
        parts = () if rel_dir == '.' else rel_dir.split(os.sep)
        if any(p in ('.agents', 'plugin') for p in parts):
            errors.append(f'directory build leaked unexpected directory: {rel_dir}')
        for name in names:
            if not name.endswith(('.md', '.txt', '.py', '.sh', '.mjs', '.js')):
                continue
            full = os.path.join(dirpath, name)
            text = open(full, encoding='utf-8').read()
            rel_file = os.path.relpath(full, rel_dir_out).replace(os.sep, '/')
            for leftover in ('.agents/skills', 'plugin/skills'):
                if leftover in text:
                    errors.append(f'directory build {rel_file} still contains `{leftover}`')


def check_claude_plugin(errors, with_cli=False):
    """Validate Claude Code marketplace and plugin manifests (and optionally run `claude plugin validate` & `directory.py`)."""
    mkt_path = os.path.join(ROOT, '.claude-plugin', 'marketplace.json')
    plg_path = os.path.join(ROOT, 'plugin', '.claude-plugin', 'plugin.json')
    for p in (mkt_path, plg_path):
        if not os.path.isfile(p):
            errors.append(f'missing {os.path.relpath(p, ROOT)}')
            return
    try:
        mkt = json.load(open(mkt_path, encoding='utf-8'))
        plg = json.load(open(plg_path, encoding='utf-8'))
    except json.JSONDecodeError as e:
        errors.append(f'invalid Claude plugin JSON: {e}')
        return

    if plg.get('name') != 'lemo-opuscar':
        errors.append(f'plugin/.claude-plugin/plugin.json: expected name "lemo-opuscar", got {plg.get("name")!r}')
    sources = [entry.get('source') for entry in mkt.get('plugins', []) if entry.get('name') == 'lemo-opuscar']
    if './plugin' not in sources:
        errors.append('.claude-plugin/marketplace.json: expected plugin source "./plugin" for lemo-opuscar')

    if not with_cli:
        return

    if shutil.which('claude'):
        for target in ('.', 'plugin'):
            r = subprocess.run(['claude', 'plugin', 'validate', target], cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                errors.append(f'claude plugin validate {target} failed (exit {r.returncode}): {r.stdout}\n{r.stderr}')

    rel_dir_out = os.path.join(ROOT, '.release', 'directory')
    existed_before = os.path.exists(rel_dir_out)
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'directory.py'), '9.9.9'],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        errors.append(f'tools/directory.py 9.9.9 failed (exit {r.returncode}): {r.stdout}\n{r.stderr}')
    else:
        _verify_directory_build(rel_dir_out, errors)
        if shutil.which('claude'):
            r2 = subprocess.run(['claude', 'plugin', 'validate', rel_dir_out], cwd=ROOT, capture_output=True, text=True)
            if r2.returncode != 0:
                errors.append(f'claude plugin validate on built directory edition failed: {r2.stdout}\n{r2.stderr}')
    if not existed_before and os.path.exists(rel_dir_out):
        shutil.rmtree(rel_dir_out, ignore_errors=True)
        parent = os.path.dirname(rel_dir_out)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)


def check_docs(errors):
    """Verify README sections cover all 6 platforms, Agent vs Model distinction, and valid repo paths."""
    readme = open(os.path.join(ROOT, 'README.md'), encoding='utf-8').read()
    if '## Supported AI Coding Agents' not in readme:
        errors.append('README.md: missing `## Supported AI Coding Agents` heading')
    for platform in REQUIRED_PLATFORMS:
        if platform not in readme:
            errors.append(f'README.md: missing platform documentation for {platform!r}')
    if 'Agent compatibility' not in readme or 'Model compatibility' not in readme.replace('model compatibility', 'Model compatibility'):
        errors.append('README.md: missing explicit Agent compatibility vs. Model compatibility distinction')

    for repo_path in (
        '.agents/skills/lemo-opuscar/',
        '.agents/skills/lemo-opuscar/SKILL.md',
        '.agents/skills/lemo-opuscar/scripts/setup.sh',
        '.agents/skills/lemo-opuscar/scripts/setup.py',
        '.gemini/settings.json',
        'plugin/skills/lemo-opuscar/',
        'AGENTS.md',
        'CLAUDE.md',
        'DIRECTOR.md',
        'TECHNIQUE.md',
        'MY-STYLES.md',
        'tools/sync_skills.py',
    ):
        if repo_path not in readme:
            errors.append(f'README.md: expected documented repo path {repo_path!r}')
        full = os.path.join(ROOT, repo_path.rstrip('/'))
        if not os.path.exists(full):
            errors.append(f'missing documented repo path: {repo_path}')

    zh = open(os.path.join(ROOT, 'README.zh-CN.md'), encoding='utf-8').read()
    if '## 支持的 AI 编程 Agent' not in zh:
        errors.append('README.zh-CN.md: missing `## 支持的 AI 编程 Agent` heading')
    for platform in REQUIRED_PLATFORMS:
        if platform not in zh:
            errors.append(f'README.zh-CN.md: missing platform documentation for {platform!r}')


def main():
    ap = argparse.ArgumentParser(description='Validate Lemo-Opuscar multi-agent Agent Skills compatibility')
    ap.add_argument('--with-cli', action='store_true', help='also run `claude plugin validate` and a dry `tools/directory.py` build')
    args = ap.parse_args()

    errors = []
    if sync_skills.check_skills() != 0:
        errors.append('skill sync check failed')
    with tempfile.TemporaryDirectory() as tmp_dir:
        check_skill_dirs(errors, tmp_dir)
        check_portability(errors)
        check_gemini_settings(errors)
        check_workflows_yaml(errors)
        check_python_tools(errors, tmp_dir)
        check_directory_edits(errors)
        check_claude_plugin(errors, with_cli=args.with_cli)
        check_docs(errors)

    if errors:
        print('\nValidation failures:', file=sys.stderr)
        for err in errors:
            print(f'✗ {err}', file=sys.stderr)
        return 1
    print('✓ All Lemo-Opuscar multi-agent compatibility checks passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
