#!/usr/bin/env node
/**
 * Lemo Opuscar for Agents CLI.
 * Installs the shared Agent Skill for Antigravity, Gemini CLI, Codex CLI,
 * OpenCode, GitHub Copilot, and Claude Code, lists styles, and checks setup.
 *
 * Based on Lemo-Opuscar by Lemomo (LemoLab):
 * https://github.com/lemomo-ai/lemo-opuscar
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const PKG_ROOT = path.resolve(path.dirname(__filename), '..');
const SKILL_NAME = 'lemo-opuscar';
const CANONICAL_SKILL_DIR = path.join(PKG_ROOT, '.agents', 'skills', SKILL_NAME);
const CLAUDE_SKILL_DIR = path.join(PKG_ROOT, 'plugin', 'skills', SKILL_NAME);
const UPSTREAM_CREDIT = 'Based on Lemo-Opuscar by Lemomo (LemoLab) — https://github.com/lemomo-ai/lemo-opuscar';

const VALID_AGENTS = new Set([
  'all',
  'agents',
  'antigravity',
  'gemini',
  'codex',
  'opencode',
  'copilot',
  'claude',
]);

const SKIP_COPY_NAMES = new Set(['__pycache__', '.DS_Store', 'Thumbs.db']);

/** Read version and metadata from package.json. */
function readPkg() {
  const pkgPath = path.join(PKG_ROOT, 'package.json');
  return JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
}

/** Print CLI help message. */
function printHelp(version) {
  console.log(`Lemo Opuscar for Agents v${version}
${UPSTREAM_CREDIT}

Usage:
  lemo-opuscar-for-agents <command> [options]
  npx lemo-opuscar-for-agents init [dir] [options]

Commands:
  init [dir]        Install the Lemo-Opuscar Agent Skill into [dir] (default: .)
                    Alias: install
  styles [query]    List or search available film styles (optional keyword filter)
  doctor            Check system tools (Node, Python/uv, ffmpeg, git, Chromium)
  path              Print the local library / package root path
  help              Show this help message

Options for init / install:
  --agent <name>    Target platform: agents, antigravity, gemini, codex,
                    opencode, copilot, claude, or all
                    (default in project scope: shared .agents/ + .gemini/)
                    (default in global scope: all unique agent global paths)
                    Repeat --agent or pass a comma-separated list.
  --scope <mode>    project (default) or global
  --force, -f       Overwrite existing skill files
  --dry-run, -n     Preview actions without writing files

Options:
  --version, -v     Print package version
  --help, -h        Show this help message

Examples:
  npx lemo-opuscar-for-agents init
  npx lemo-opuscar-for-agents init --scope global --agent codex
  npx lemo-opuscar-for-agents init --agent all --dry-run
  npx lemo-opuscar-for-agents styles crayon
  npx lemo-opuscar-for-agents doctor
`);
}

/** Parse positional arguments and flags for a subcommand. */
function parseArgs(argv) {
  const positional = [];
  const agents = [];
  let scope = 'project';
  let force = false;
  let dryRun = false;
  let help = false;
  let version = false;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      help = true;
    } else if (arg === '--version' || arg === '-v') {
      version = true;
    } else if (arg === '--force' || arg === '-f') {
      force = true;
    } else if (arg === '--dry-run' || arg === '-n') {
      dryRun = true;
    } else if (arg === '--scope') {
      const next = argv[++i];
      if (!next) throw new Error('--scope requires "project" or "global".');
      scope = next.toLowerCase();
    } else if (arg.startsWith('--scope=')) {
      scope = arg.slice('--scope='.length).toLowerCase();
    } else if (arg === '--agent') {
      const next = argv[++i];
      if (!next) throw new Error('--agent requires a value.');
      for (const item of next.split(',')) {
        const clean = item.trim().toLowerCase();
        if (clean) agents.push(clean);
      }
    } else if (arg.startsWith('--agent=')) {
      const val = arg.slice('--agent='.length);
      for (const item of val.split(',')) {
        const clean = item.trim().toLowerCase();
        if (clean) agents.push(clean);
      }
    } else if (arg.startsWith('-')) {
      throw new Error(`Unknown option: ${arg}`);
    } else {
      positional.push(arg);
    }
  }

  if (scope !== 'project' && scope !== 'global') {
    throw new Error(`Invalid --scope "${scope}". Use "project" or "global".`);
  }
  for (const agent of agents) {
    if (!VALID_AGENTS.has(agent)) {
      throw new Error(
        `Unknown agent "${agent}". Valid options: ${[...VALID_AGENTS].join(', ')}.`,
      );
    }
  }

  return { positional, agents, scope, force, dryRun, help, version };
}

/** Resolve source skill directory (canonical .agents/ or plugin/ fallback). */
function getSkillSourceDir() {
  if (fs.existsSync(path.join(CANONICAL_SKILL_DIR, 'SKILL.md'))) {
    return CANONICAL_SKILL_DIR;
  }
  if (fs.existsSync(path.join(CLAUDE_SKILL_DIR, 'SKILL.md'))) {
    return CLAUDE_SKILL_DIR;
  }
  throw new Error(`Skill files missing inside package root: ${PKG_ROOT}`);
}

/** Return destination skill directories for requested agents and scope. */
function resolveSkillTargets(agents, scope, projectRoot) {
  const home = os.homedir();
  const chosen = agents.length > 0 ? agents : scope === 'project' ? ['agents', 'gemini'] : ['all'];
  const expanded = new Set();

  for (const agent of chosen) {
    if (agent === 'all') {
      if (scope === 'project') {
        expanded.add('agents');
        expanded.add('gemini');
        expanded.add('claude');
      } else {
        expanded.add('antigravity');
        expanded.add('codex');
        expanded.add('gemini');
        expanded.add('opencode');
        expanded.add('copilot');
        expanded.add('claude');
      }
    } else {
      expanded.add(agent);
    }
  }

  const targetMap = new Map();
  const addTarget = (dest, label) => {
    const resolved = path.resolve(dest);
    const existing = targetMap.get(resolved);
    if (existing) {
      if (!existing.labels.includes(label)) existing.labels.push(label);
    } else {
      targetMap.set(resolved, { dest: resolved, labels: [label] });
    }
  };

  let configureGeminiContext = false;

  for (const agent of expanded) {
    if (scope === 'project') {
      if (agent === 'claude') {
        addTarget(path.join(projectRoot, '.claude', 'skills', SKILL_NAME), 'Claude Code');
      } else {
        addTarget(
          path.join(projectRoot, '.agents', 'skills', SKILL_NAME),
          agent === 'agents' ? 'Shared Agent Skills' : agent,
        );
        if (agent === 'gemini') configureGeminiContext = true;
      }
    } else {
      const globalDirs = {
        agents: [path.join(home, '.agents', 'skills', SKILL_NAME), 'Shared Agent Skills (~/.agents)'],
        codex: [path.join(home, '.agents', 'skills', SKILL_NAME), 'OpenAI Codex CLI'],
        antigravity: [path.join(home, '.gemini', 'config', 'skills', SKILL_NAME), 'Google Antigravity'],
        gemini: [path.join(home, '.gemini', 'skills', SKILL_NAME), 'Gemini CLI'],
        opencode: [path.join(home, '.config', 'opencode', 'skills', SKILL_NAME), 'OpenCode'],
        copilot: [path.join(home, '.copilot', 'skills', SKILL_NAME), 'GitHub Copilot'],
        claude: [path.join(home, '.claude', 'skills', SKILL_NAME), 'Claude Code'],
      };
      const entry = globalDirs[agent];
      if (entry) addTarget(entry[0], entry[1]);
    }
  }

  return { targets: [...targetMap.values()], configureGeminiContext };
}

/** Recursively copy a directory with real files, skipping cache artifacts. */
function copySkillTree(srcDir, destDir) {
  fs.mkdirSync(destDir, { recursive: true });
  for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
    if (SKIP_COPY_NAMES.has(entry.name) || entry.name.endsWith('.pyc')) {
      continue;
    }
    const srcPath = path.join(srcDir, entry.name);
    const destPath = path.join(destDir, entry.name);
    if (entry.isDirectory()) {
      copySkillTree(srcPath, destPath);
    } else if (entry.isFile()) {
      fs.copyFileSync(srcPath, destPath);
      if (process.platform !== 'win32' && entry.name.endsWith('.sh')) {
        fs.chmodSync(destPath, 0o755);
      }
    }
  }
}

/** Safely create or update .gemini/settings.json so AGENTS.md is autoloaded. */
function ensureGeminiSettings(projectRoot, { dryRun, force }) {
  const settingsPath = path.resolve(projectRoot, '.gemini', 'settings.json');
  const pkgSettings = path.resolve(PKG_ROOT, '.gemini', 'settings.json');
  if (settingsPath === pkgSettings) {
    return { status: 'self', path: settingsPath };
  }

  const existed = fs.existsSync(settingsPath);
  let data = {};
  if (existed) {
    try {
      const raw = fs.readFileSync(settingsPath, 'utf8');
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Root JSON value is not an object');
      }
      data = parsed;
    } catch (err) {
      if (!force) {
        return { status: 'invalid-json', path: settingsPath, message: err.message };
      }
      data = {};
    }
  }

  const ctx = data.context && typeof data.context === 'object' && !Array.isArray(data.context)
    ? { ...data.context }
    : {};
  const existingNames = Array.isArray(ctx.fileName)
    ? [...ctx.fileName]
    : typeof ctx.fileName === 'string' && ctx.fileName.trim()
      ? [ctx.fileName.trim()]
      : [];

  if (existingNames.includes('AGENTS.md')) {
    return { status: 'unchanged', path: settingsPath };
  }

  const nextNames = ['AGENTS.md', ...existingNames];
  if (!nextNames.includes('GEMINI.md')) nextNames.push('GEMINI.md');
  ctx.fileName = nextNames;
  const updated = { ...data, context: ctx };

  if (!dryRun) {
    fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
    fs.writeFileSync(settingsPath, `${JSON.stringify(updated, null, 2)}\n`, 'utf8');
  }
  return { status: existed ? 'updated' : 'created', path: settingsPath };
}

/** Run `init` / `install` subcommand. */
function cmdInit(opts) {
  const targetDir = path.resolve(opts.positional[0] || '.');
  const srcDir = getSkillSourceDir();
  const { targets, configureGeminiContext } = resolveSkillTargets(opts.agents, opts.scope, targetDir);

  if (opts.scope === 'project' && fs.existsSync(targetDir) && !fs.statSync(targetDir).isDirectory()) {
    throw new Error(`Target path exists and is not a directory: ${targetDir}`);
  }

  console.log(`Lemo Opuscar for Agents — ${opts.scope} install`);
  if (opts.dryRun) console.log('[dry-run] No files will be written.\n');

  let hadSkip = false;
  for (const { dest, labels } of targets) {
    const labelText = labels.join(', ');
    if (path.resolve(srcDir) === path.resolve(dest)) {
      console.log(`• ${dest} (${labelText}) — already canonical source, skipped`);
      continue;
    }

    const exists = fs.existsSync(dest);
    if (exists && !opts.force) {
      hadSkip = true;
      console.log(`! ${dest} (${labelText}) — already exists (pass --force to overwrite)`);
      continue;
    }

    if (!opts.dryRun) {
      if (exists && opts.force) {
        fs.rmSync(dest, { recursive: true, force: true });
      }
      copySkillTree(srcDir, dest);
    }
    const verb = opts.dryRun
      ? exists ? 'would overwrite' : 'would install'
      : exists ? 'updated' : 'installed';
    console.log(`✓ ${verb} ${dest} (${labelText})`);
  }

  if (configureGeminiContext) {
    const res = ensureGeminiSettings(targetDir, opts);
    if (res.status === 'self') {
      console.log(`• ${res.path} — already configured in repository`);
    } else if (res.status === 'invalid-json') {
      console.log(`! ${res.path} — invalid JSON (${res.message}); skipped (use --force to reset)`);
    } else if (res.status === 'unchanged') {
      console.log(`• ${res.path} — already includes AGENTS.md`);
    } else {
      const verb = opts.dryRun ? `would ${res.status === 'created' ? 'create' : 'update'}` : res.status;
      console.log(`✓ ${verb} ${res.path} (Gemini CLI AGENTS.md autoload)`);
    }
  }

  const isWin = process.platform === 'win32';
  const setupHint = opts.scope === 'project'
    ? (isWin
      ? 'python3 .agents/skills/lemo-opuscar/scripts/setup.py'
      : 'sh .agents/skills/lemo-opuscar/scripts/setup.sh')
    : 'Ask your agent to start the lemo-opuscar skill (or run lemo-opuscar-for-agents doctor)';

  console.log(`\nNext steps:`);
  if (hadSkip) {
    console.log(`  - Re-run with --force to update existing directories.`);
  }
  console.log(`  - Run ${setupHint} to check dependencies / library location.`);
  console.log(`  - Optional: set LEMO_OPUSCAR_HOME=${PKG_ROOT} to reuse this npm package as your local library.`);
  console.log(`  - ${UPSTREAM_CREDIT}`);
}

/** Run `styles [query]` subcommand. */
function cmdStyles(opts) {
  const stylesDir = path.join(PKG_ROOT, 'styles');
  if (!fs.existsSync(stylesDir)) {
    throw new Error(`Styles directory not found at ${stylesDir}`);
  }
  const query = opts.positional.join(' ').trim().toLowerCase();
  const entries = [];

  for (const dirEntry of fs.readdirSync(stylesDir, { withFileTypes: true })) {
    if (!dirEntry.isDirectory()) continue;
    const jsonPath = path.join(stylesDir, dirEntry.name, 'style.json');
    if (!fs.existsSync(jsonPath)) continue;
    try {
      const meta = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
      const needsImage = Array.isArray(meta.needs) && meta.needs.includes('image-generation');
      const haystack = [
        meta.slug || dirEntry.name,
        meta.en || '',
        meta.cn || '',
        meta.category_en || '',
        meta.category_cn || '',
        meta.line || '',
        meta.line_cn || '',
      ]
        .join('\n')
        .toLowerCase();

      if (!query || haystack.includes(query)) {
        entries.push({
          num: String(meta.num || '').padStart(2, '0'),
          slug: meta.slug || dirEntry.name,
          en: meta.en || dirEntry.name,
          cn: meta.cn || '',
          category: meta.category_en || '',
          needsImage,
        });
      }
    } catch {
      // Ignore malformed style.json rather than aborting listing.
    }
  }

  entries.sort((a, b) => a.num.localeCompare(b.num) || a.slug.localeCompare(b.slug));
  if (entries.length === 0) {
    console.log(`No styles matching "${query}".`);
    return;
  }

  console.log(`Lemo Opuscar for Agents — ${entries.length} style(s)${query ? ` matching "${query}"` : ''}:\n`);
  for (const s of entries) {
    const marker = s.needsImage ? ' ✦' : '';
    const cnPart = s.cn ? ` · ${s.cn}` : '';
    console.log(`  ${s.num || '--'}  ${s.slug.padEnd(24)} ${s.en}${cnPart}${marker}  [${s.category}]`);
  }
  if (entries.some((s) => s.needsImage)) {
    console.log(`\n✦ Requires an image generator (Codex CLI login, OPENAI_API_KEY, or GEMINI_API_KEY).`);
  }
}

/** Probe a binary and return its first output line or null. */
function probeCommand(cmd, args) {
  try {
    const file = process.platform === 'win32' ? 'cmd.exe' : cmd;
    const argv = process.platform === 'win32' ? ['/d', '/c', cmd, ...args] : args;
    const out = execFileSync(file, argv, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
      timeout: 5000,
    });
    return out.trim().split(/\r?\n/)[0] || 'ok';
  } catch {
    return null;
  }
}

/** Run `doctor` subcommand to inspect runtime readiness. */
function cmdDoctor() {
  console.log(`Lemo Opuscar for Agents — Environment Check`);
  console.log(`Package root: ${PKG_ROOT}\n`);

  const nodeMajor = Number(process.versions.node.split('.')[0]);
  console.log(`  ${nodeMajor >= 20 ? '✓' : '✗'} Node.js: v${process.versions.node} (need >= 20)`);

  const pyCmds = process.platform === 'win32'
    ? [['python3', ['--version']], ['python', ['--version']], ['py', ['-3', '--version']]]
    : [['python3', ['--version']], ['python', ['--version']]];
  let pyInfo = null;
  let pyOk = false;
  for (const [bin, args] of pyCmds) {
    const res = probeCommand(bin, args);
    const match = res && res.match(/Python\s+3\.(\d+)/i);
    if (match) {
      pyInfo = `${bin} (${res})`;
      pyOk = Number(match[1]) >= 11;
      break;
    }
  }
  console.log(`  ${pyOk ? '✓' : '✗'} Python 3: ${pyInfo || 'not found'} (need >= 3.11)`);

  const npmVer = probeCommand('npm', ['--version']);
  console.log(`  ${npmVer ? '✓' : '✗'} npm: ${npmVer || 'not found'}`);

  const uv = probeCommand('uv', ['--version']);
  console.log(`  ${uv ? '✓' : '·'} uv: ${uv || 'not installed (optional, speeds up .venv setup)'}`);

  const git = probeCommand('git', ['--version']);
  console.log(`  ${git ? '✓' : '✗'} git: ${git || 'not found'}`);

  const ffmpeg = probeCommand('ffmpeg', ['-version']);
  console.log(`  ${ffmpeg ? '✓' : '✗'} ffmpeg: ${ffmpeg || 'not found (need >= 7)'}`);

  const pwPkg = fs.existsSync(path.join(PKG_ROOT, 'node_modules', 'playwright-core', 'package.json'))
    || fs.existsSync(path.join(process.cwd(), 'node_modules', 'playwright-core', 'package.json'));
  console.log(`  ${pwPkg ? '✓' : '·'} playwright-core: ${pwPkg ? 'installed' : 'run deps setup below to install'}`);

  const webgpuBrowser = findWebGPUBrowser();
  console.log(`  ${webgpuBrowser ? '✓' : '·'} WebGPU browser (--webgpu): ${webgpuBrowser || 'no system Chrome/Edge detected (uses chromium fallback or set PLAYWRIGHT_CHROME)'}`);

  const setupCmd = process.platform === 'win32'
    ? 'python3 .agents/skills/lemo-opuscar/scripts/setup.py deps'
    : 'sh .agents/skills/lemo-opuscar/scripts/setup.sh deps';
  console.log(`\nRun inside your project or repo root to install Node + Python runtime deps:\n  ${setupCmd}`);
}

/** Detect an installed Chrome or Edge binary capable of hardware --webgpu rendering. */
function findWebGPUBrowser() {
  if (process.env.PLAYWRIGHT_CHROME && fs.existsSync(process.env.PLAYWRIGHT_CHROME)) {
    return `PLAYWRIGHT_CHROME (${process.env.PLAYWRIGHT_CHROME})`;
  }
  const candidates = process.platform === 'win32'
    ? [
        ['Chrome', 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'],
        ['Chrome', 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'],
        ['Chrome', path.join(os.homedir(), 'AppData', 'Local', 'Google', 'Chrome', 'Application', 'chrome.exe')],
        ['Edge', 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'],
        ['Edge', 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'],
      ]
    : process.platform === 'darwin'
      ? [
          ['Chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'],
          ['Edge', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'],
        ]
      : [
          ['Chrome', '/usr/bin/google-chrome'],
          ['Chrome', '/usr/bin/google-chrome-stable'],
          ['Edge', '/usr/bin/microsoft-edge'],
          ['Chromium', '/usr/bin/chromium'],
        ];
  for (const [label, binPath] of candidates) {
    if (fs.existsSync(binPath)) return `${label} (${binPath})`;
  }
  return null;
}

/** Main entrypoint. */
function main() {
  const pkg = readPkg();
  const rawArgs = process.argv.slice(2);
  if (rawArgs.length === 0) {
    printHelp(pkg.version);
    return;
  }

  const first = rawArgs[0];
  if (first === '--help' || first === '-h' || first === 'help') {
    printHelp(pkg.version);
    return;
  }
  if (first === '--version' || first === '-v' || first === 'version') {
    console.log(pkg.version);
    return;
  }

  const isSubcommand = ['init', 'install', 'styles', 'doctor', 'path'].includes(first);
  const subcommand = isSubcommand ? first : 'init';
  const subArgs = isSubcommand ? rawArgs.slice(1) : rawArgs;
  const opts = parseArgs(subArgs);

  if (opts.version) {
    console.log(pkg.version);
    return;
  }
  if (opts.help) {
    printHelp(pkg.version);
    return;
  }

  if (subcommand === 'path') {
    console.log(PKG_ROOT);
  } else if (subcommand === 'styles') {
    cmdStyles(opts);
  } else if (subcommand === 'doctor') {
    cmdDoctor();
  } else {
    cmdInit(opts);
  }
}

try {
  main();
} catch (err) {
  console.error(`Error: ${err.message}`);
  process.exit(1);
}
