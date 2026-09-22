#!/usr/bin/env node
// =============================================================================
// scripts/hunt_gate.mjs — Hunt Constitution (H1–H6) mechanical gate.
//
// Governed by: PROMPT_P0_foundation_for_deepseek.md §3.1
// Run:         node scripts/hunt_gate.mjs [root]
// Exit code:   0 = PASS (no violations) ; 1 = FAIL (violations printed).
//
// This script prints exclusively via console.error (never console.log) so it
// does not trip its own rule #4. It is cross-platform (the owner is on
// Windows) and depends only on Node built-ins.
// =============================================================================

import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const TARGET = process.argv[2] ? path.resolve(process.argv[2]) : ROOT;

// A linter must not lint itself: the gate source and its own test suite both
// contain literal banned markers as pattern definitions / fixtures, not as
// violations. They are skipped by absolute path.
const SELF_FILES = new Set([
  path.resolve(__dirname, 'hunt_gate.mjs'),
  path.resolve(__dirname, '__tests__', 'hunt_gate.test.mjs'),
]);

// Directories that are never scanned (third-party, generated, or out of scope).
const SKIP_DIRS = new Set([
  'node_modules',
  '.git',
  'dist',
  'build',
  'coverage',
  '__pycache__',
  '.venv',
  'venv',
  '.pytest_cache',
  '.mypy_cache',
  '.ruff_cache',
  'auth_sessions',
  'p0_screens',
]);

// File extensions treated as code by the H1/H2/H6 rules.
const CODE_EXTS = new Set([
  '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.py', '.sql', '.sh',
  '.yml', '.yaml', '.conf', '.ini', '.toml',
]);

// File types that MUST use LF line endings (executed/parsed in Linux containers).
const LF_REQUIRED_EXTS = new Set(['.sh', '.yml', '.conf', '.ini', '.sql']);

// Rule 1 — H1 placeholders. Forbidden in every non-test file.
const H1_PATTERNS = [
  { name: 'TODO', re: /\bTODO\b/ },
  { name: 'FIXME', re: /\bFIXME\b/ },
  { name: 'XXX', re: /\bXXX\b/ },
  { name: 'HACK', re: /\bHACK\b/ },
  { name: 'raise NotImplementedError', re: /\braise\s+NotImplementedError\b/ },
  { name: "throw new Error('not implemented')", re: /throw\s+new\s+Error\(\s*['"`]not implemented['"`]\s*\)/i },
  { name: 'lorem', re: /\blorem\b/i },
  // A3: allow the HTML/JSX attribute `placeholder=` but still ban the bare
  // word used as text or as a variable name.
  { name: 'placeholder', re: /\bplaceholder\b(?!=)/i },
  { name: 'dummy', re: /\bdummy\b/i },
  { name: 'foo', re: /\bfoo\b/ },
  { name: 'bar', re: /\bbar\b/ },
];

// Rule 7 — secret patterns that must never be committed.
const SECRET_PATTERNS = [
  { name: 'AWS access key', re: /\bAKIA[0-9A-Z]{16}\b/ },
  { name: 'PEM private key', re: /-----BEGIN\s+(RSA|EC|OPENSSH|PGP|PRIVATE)\s/ },
  { name: 'sk- secret', re: /\bsk-[A-Za-z0-9]{20,}\b/ },
  { name: 'google api key', re: /\bAIza[0-9A-Za-z_-]{35}\b/ },
];

function isTestFile(rel) {
  return /(^|[\\/])(__tests__|tests?)([\\/]|$)/.test(rel)
    || /\.(test|spec)\.(js|mjs|cjs|ts|tsx|jsx)$/.test(rel);
}

function isCodeFile(p) {
  const base = path.basename(p);
  if (base === 'Dockerfile' || base === 'docker-compose.yml' || base === 'docker-compose.test.yml') return true;
  return CODE_EXTS.has(path.extname(p).toLowerCase());
}

function walk(dir, out, base) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    const rel = path.relative(base, full);
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name)) continue;
      // The architecture reference files are read-only and out of scope for the gate.
      if (rel === path.join('docs', 'reference')) continue;
      walk(full, out, base);
    } else if (e.isFile()) {
      out.push({ full, rel });
    }
  }
}

function readLines(full) {
  try {
    return fs.readFileSync(full, 'utf8').split(/\r?\n/);
  } catch {
    return null;
  }
}

function isLfRequired(full) {
  const base = path.basename(full);
  if (base === 'Dockerfile') return true;
  return LF_REQUIRED_EXTS.has(path.extname(full).toLowerCase());
}

function hasCR(full) {
  try {
    return fs.readFileSync(full, 'utf8').includes('\r');
  } catch {
    return false;
  }
}

// Rule 7 (.env) must only flag a .env that would actually be committed, i.e. one
// that is NOT ignored by git. A gitignored `.env` (the project's normal, required
// local config) is legitimate and must not fail the gate on a real host.
function isGitIgnored(root, rel) {
  try {
    execFileSync('git', ['-C', root, 'check-ignore', '--quiet', '--', rel], { stdio: 'ignore' });
    return true; // exit 0: ignored
  } catch {
    return false; // exit 1 (not ignored) / 128 (not a repo) / git missing => not proven ignored
  }
}

/**
 * Scan every file under `root` and return all Hunt Gate violations.
 * Pure with respect to stdout: it returns results and never prints. The CLI
 * entry point (main) is the only place that writes to console.error.
 *
 * @param {string} root  Directory to scan (defaults to the repository root).
 * @returns {{ files: Array<{full:string, rel:string}>, violations: Array<{file:string, line:number, rule:string, detail:string}> }}
 */
export function runGate(root = ROOT) {
  const files = [];
  walk(root, files, root);

  /** @type {Array<{file:string, line:number, rule:string, detail:string}>} */
  const violations = [];

  for (const { full, rel } of files) {
    // A linter must not lint itself or its own tests (see SELF_FILES).
    if (SELF_FILES.has(path.resolve(full))) continue;
    const test = isTestFile(rel);
    const code = isCodeFile(full);

    // Rule 7 (secrets) applies to every scanned file, code or not.
    const raw = readLines(full);
    if (raw) {
      raw.forEach((line, idx) => {
        for (const p of SECRET_PATTERNS) {
          if (p.re.test(line)) {
            violations.push({
              file: rel, line: idx + 1, rule: 'H5/rule7-secret',
              detail: `possible secret (${p.name})`,
            });
          }
        }
      });
    }

    // Rule 9 (B1) — CRLF line endings are forbidden in files executed/parsed
    // inside Linux containers (shell scripts, Dockerfile, YAML, configs, SQL).
    if (raw && isLfRequired(full) && hasCR(full)) {
      violations.push({
        file: rel, line: 1, rule: 'CRLF/rule9',
        detail: 'CRLF line endings (use LF; see .gitattributes/.editorconfig)',
      });
    }

    // Rule 7 (.env committed): a real .env that is not the documented example
    // and is not git-ignored (i.e. would be committed).
    const base = path.basename(full);
    if (/^\.env(\.|$)/.test(base) && base !== '.env.example' && !isGitIgnored(root, rel)) {
      violations.push({
        file: rel, line: 1, rule: 'H5/rule7-env',
        detail: 'real .env file present (use .env.example, keep secrets out of the repo)',
      });
    }

    if (!code || !raw) continue;

    // Rule 1 — H1 placeholders in non-test files.
    if (!test) {
      raw.forEach((line, idx) => {
        for (const p of H1_PATTERNS) {
          if (p.re.test(line)) {
            violations.push({
              file: rel, line: idx + 1, rule: 'H1',
              detail: `placeholder marker "${p.name}"`,
            });
          }
        }
      });
    }

    const ext = path.extname(full).toLowerCase();

    // Rule 2/6/8 — Python-specific checks.
    if (ext === '.py') {
      raw.forEach((line, idx) => {
        if (/^\s*pass\s*(#.*)?$/.test(line)) {
          violations.push({ file: rel, line: idx + 1, rule: 'H1/rule2', detail: 'standalone `pass` statement' });
        }
        if (/^\s*except\s*:\s*$/.test(line)) {
          violations.push({ file: rel, line: idx + 1, rule: 'H1/rule2', detail: 'bare `except:`' });
        }
        if (/^\s*except\s+Exception\s*:\s*$/.test(line)) {
          const next = (raw[idx + 1] ?? '').trim();
          if (next === 'pass' || next === '...' || next.startsWith('pass ') || next === 'pass;') {
            violations.push({ file: rel, line: idx + 1, rule: 'H1/rule2', detail: '`except Exception:` with pass/... body' });
          }
        }
        const relf = rel.replaceAll('\\', '/');
        if (/^\s*(import|from)\s+(psycopg|asyncpg)\b/.test(line) && !relf.startsWith('core/app/db/')) {
          violations.push({ file: rel, line: idx + 1, rule: 'H2/rule6', detail: 'psycopg/asyncpg import outside core/app/db/' });
        }
        if (relf.startsWith('core/') && !relf.startsWith('core/app/db/') && !relf.startsWith('core/app/repos/')) {
          // A4: raw SQL must be UPPERCASE and inside a string literal, so prose
          // like "select the best option" is never flagged. Single/double-quoted
          // strings on one line are matched; multi-line SQL strings belong to the
          // migration pipeline and are not this gate's concern.
          if (/(["'])(?:(?!\1).)*\b(SELECT|INSERT|UPDATE|DELETE)\b(?:(?!\1).)*\1/.test(line)) {
            violations.push({ file: rel, line: idx + 1, rule: 'H2/rule8', detail: 'raw SQL outside core/app/db/ or core/app/repos/' });
          }
        }
      });
    }

    // Rule 3/4/5 — JS/TS-specific checks.
    if (['.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx'].includes(ext)) {
      const content = raw.join('\n');

      // Rule 3 (A1) — empty catch block, with or without a bound variable:
      //   catch {}  and  catch (err) {}  are both swallowed errors.
      const emptyCatchRe = /catch\s*(?:\([^)]*\))?\s*\{\s*(?:(?:\/\/[^\n]*)|(?:\/\*[\s\S]*?\*\/))?\s*\}/g;
      let m;
      while ((m = emptyCatchRe.exec(content)) !== null) {
        const upto = content.slice(0, m.index);
        const line = upto.split('\n').length;
        violations.push({ file: rel, line, rule: 'H1/rule3', detail: 'empty catch block' });
      }

      // Rule 3 (A2) — swallowed promise rejection (H3):
      //   .catch(() => {})  and  .catch(() => undefined)  and  .catch(() => null).
      const swallowedCatchRe = /\.catch\s*\([^)]*\)\s*=>\s*(?:\{\s*(?:(?:\/\/[^\n]*)|(?:\/\*[\s\S]*?\*\/))?\s*\}|\b(?:undefined|null)\b)\s*\)/g;
      while ((m = swallowedCatchRe.exec(content)) !== null) {
        const upto = content.slice(0, m.index);
        const line = upto.split('\n').length;
        violations.push({ file: rel, line, rule: 'H3/rule3-swallowed', detail: 'swallowed promise rejection (.catch with empty/undefined body)' });
      }

      if (!test) {
        raw.forEach((line, idx) => {
          if (/console\.log\s*\(/.test(line)) {
            violations.push({ file: rel, line: idx + 1, rule: 'H12/rule4', detail: 'console.log outside test files (use pino)' });
          }
        });
      }

      if (test) {
        raw.forEach((line, idx) => {
          if (/\.(skip|only)\s*\(/.test(line) || /\b(xit|xdescribe)\s*\(/.test(line)) {
            violations.push({ file: rel, line: idx + 1, rule: 'H7/rule5', detail: 'skipped or focused test' });
          }
        });
      }
    }

    // Rule 5 (Python) — skipped or xfail tests (A4): pytest.mark.skip/skipif/xfail
    // decorators and pytest.skip()/pytest.xfail() calls.
    if (ext === '.py' && test) {
      raw.forEach((line, idx) => {
        if (/@pytest\.mark\.(skip|skipif|xfail)\b/.test(line) || /\bpytest\.(skip|xfail)\s*\(/.test(line)) {
          violations.push({ file: rel, line: idx + 1, rule: 'H7/rule5', detail: 'skipped or xfail test' });
        }
      });
    }

    // Rule 4 (Python) — print( outside test files.
    if (ext === '.py' && !test) {
      raw.forEach((line, idx) => {
        if (/print\s*\(/.test(line)) {
          violations.push({ file: rel, line: idx + 1, rule: 'H12/rule4', detail: 'print( in Python' });
        }
      });
    }
  }

  return { files, violations };
}

function main() {
  const { files, violations } = runGate(TARGET);

  if (violations.length === 0) {
    console.error(`HUNT GATE PASSED — ${files.length} files scanned, 0 violations.`);
    process.exitCode = 0;
    return;
  }

  for (const v of violations) {
    console.error(`${v.file}:${v.line}: [${v.rule}] ${v.detail}`);
  }
  console.error(`HUNT GATE FAILED — ${violations.length} violation(s).`);
  process.exitCode = 1;
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main();
}
