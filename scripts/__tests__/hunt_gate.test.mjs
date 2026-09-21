import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { runGate } from '../hunt_gate.mjs';

// A5 — the gate itself is tested (H7). Each sample file is written into a
// throwaway directory OUTSIDE any `__tests__`/`tests` path, so the scanner
// classifies the violations as production code (not test files). runGate() is
// pure: it returns results and never prints, so assertions read the returned
// violations array directly.

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'hunt-gate-'));
}

function write(dir, relPath, content) {
  const full = path.join(dir, relPath);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, content);
  return full;
}

function cleanup(dir) {
  fs.rmSync(dir, { recursive: true, force: true });
}

test('a clean codebase passes with zero violations', () => {
  const dir = tmpDir();
  try {
    write(dir, 'app.js', 'export function add(a, b) {\n  return a + b;\n}\n');
    write(dir, 'app.py', 'def add(a, b):\n    return a + b\n');
    const { violations } = runGate(dir);
    assert.equal(violations.length, 0);
  } finally {
    cleanup(dir);
  }
});

test('rule1 (H1): TODO/FIXME/dummy/foo/bar markers are flagged', () => {
  const dir = tmpDir();
  try {
    write(dir, 'todo.js', '// TODO: implement later\nconst foo = 1;\nconst dummy = "x";\n');
    const { violations } = runGate(dir);
    const h1 = violations.filter((v) => v.rule === 'H1');
    assert.ok(h1.some((v) => v.detail.includes('TODO')));
    assert.ok(h1.some((v) => v.detail.includes('foo')));
    assert.ok(h1.some((v) => v.detail.includes('dummy')));
  } finally {
    cleanup(dir);
  }
});

test('A3: placeholder= attribute is allowed; placeholder as a word/variable is banned', () => {
  const dir = tmpDir();
  try {
    write(dir, 'field.jsx', 'export const Input = () => <input placeholder="Name" />;\n');
    const clean = runGate(dir);
    assert.equal(
      clean.violations.filter((v) => v.rule === 'H1' && v.detail.includes('placeholder')).length,
      0,
    );

    write(dir, 'placeholder.js', 'const placeholder = "later text";\n');
    const dirty = runGate(dir);
    assert.ok(dirty.violations.some((v) => v.rule === 'H1' && v.detail.includes('placeholder')));
  } finally {
    cleanup(dir);
  }
});

test('A1: catch {} (no binding) and catch (err) {} are both flagged as empty catch', () => {
  const dir = tmpDir();
  try {
    write(dir, 'catch.js', 'try { risky(); } catch { /* silent */ }\ntry { risky(); } catch (err) {}\n');
    const { violations } = runGate(dir);
    const caught = violations.filter((v) => v.rule === 'H1/rule3');
    assert.equal(caught.length, 2);
  } finally {
    cleanup(dir);
  }
});

test('A2: .catch(() => {}) and .catch(() => undefined) are flagged as swallowed rejections', () => {
  const dir = tmpDir();
  try {
    write(dir, 'swallow.js', 'p().catch(() => {});\np().catch(() => undefined);\n');
    const { violations } = runGate(dir);
    const caught = violations.filter((v) => v.rule === 'H3/rule3-swallowed');
    assert.equal(caught.length, 2);
  } finally {
    cleanup(dir);
  }
});

test('rule2 (H1): standalone pass and bare except are flagged in Python', () => {
  const dir = tmpDir();
  try {
    write(dir, 'bad.py', 'def f():\n    pass\n\ntry:\n    pass\nexcept:\n    pass\n');
    const { violations } = runGate(dir);
    assert.ok(violations.some((v) => v.rule === 'H1/rule2'));
  } finally {
    cleanup(dir);
  }
});

test('rule4 (H12): console.log (JS) and print() (Python) outside tests are flagged', () => {
  const dir = tmpDir();
  try {
    write(dir, 'log.js', 'console.log("hi");\n');
    write(dir, 'log.py', 'print("hi")\n');
    const { violations } = runGate(dir);
    assert.ok(violations.some((v) => v.rule === 'H12/rule4' && v.detail.includes('console.log')));
    assert.ok(violations.some((v) => v.rule === 'H12/rule4' && v.detail.includes('print(')));
  } finally {
    cleanup(dir);
  }
});

test('rule5 (H7): .skip/.only (JS) and pytest skip/xfail (Python) are flagged in tests', () => {
  const dir = tmpDir();
  try {
    write(dir, 'tests/a.test.js', 'it.skip("todo", () => {});\n');
    write(
      dir,
      'tests/b_test.py',
      'import pytest\n@pytest.mark.skip\ndef test_a():\n    return None\n@pytest.mark.xfail\ndef test_b():\n    return None\ndef test_c():\n    pytest.skip("flaky")\n',
    );
    const { violations } = runGate(dir);
    assert.ok(violations.some((v) => v.rule === 'H7/rule5' && v.file.includes('.test.js')));
    assert.ok(violations.some((v) => v.rule === 'H7/rule5' && v.file.includes('.py')));
  } finally {
    cleanup(dir);
  }
});

test('rule6 (H2): psycopg/asyncpg import outside core/app/db/ is flagged', () => {
  const dir = tmpDir();
  try {
    write(dir, 'core/app/service/x.py', 'import psycopg\n');
    assert.ok(runGate(dir).violations.some((v) => v.rule === 'H2/rule6'));

    write(dir, 'core/app/db/conn.py', 'import asyncpg\n');
    const again = runGate(dir);
    assert.equal(again.violations.filter((v) => v.rule === 'H2/rule6' && v.file.includes('db/conn')).length, 0);
  } finally {
    cleanup(dir);
  }
});

test('rule7 (H5): AWS access key and a committed .env file are flagged', () => {
  const dir = tmpDir();
  try {
    write(dir, 'secret.js', 'const key = "AKIAIOSFODNN7EXAMPLE";\n');
    write(dir, '.env', 'POSTGRES_PASSWORD=secret\n');
    const { violations } = runGate(dir);
    assert.ok(violations.some((v) => v.rule === 'H5/rule7-secret'));
    assert.ok(violations.some((v) => v.rule === 'H5/rule7-env'));
  } finally {
    cleanup(dir);
  }
});

test('A4/rule8 (H2): uppercase SQL inside a string is flagged; lowercase prose is not', () => {
  const dirty = tmpDir();
  try {
    write(dirty, 'core/app/service/q.py', 'sql = "SELECT * FROM users"\n');
    assert.ok(runGate(dirty).violations.some((v) => v.rule === 'H2/rule8'));
  } finally {
    cleanup(dirty);
  }

  const clean = tmpDir();
  try {
    write(
      clean,
      'core/app/service/prose.py',
      '# select the best option for the user\nx = "select the best option"\n',
    );
    assert.equal(runGate(clean).violations.filter((v) => v.rule === 'H2/rule8').length, 0);
  } finally {
    cleanup(clean);
  }
});

test('B1: CRLF (\\r) in LF-required files is flagged; LF-only files pass', () => {
  const dirty = tmpDir();
  try {
    write(dirty, 'crlf.sh', '#!/bin/bash\r\nset -euo pipefail\r\n');
    write(dirty, 'crlf.yml', 'services:\r\n  x: {}\r\n');
    write(dirty, 'Dockerfile', 'FROM alpine:3.19\r\n');
    const { violations } = runGate(dirty);
    const cr = violations.filter((v) => v.rule === 'CRLF/rule9');
    assert.equal(cr.length, 3);
  } finally {
    cleanup(dirty);
  }

  const clean = tmpDir();
  try {
    write(clean, 'clean.sh', '#!/bin/bash\nset -euo pipefail\n');
    write(clean, 'clean.yml', 'services:\n  x: {}\n');
    write(clean, 'clean.conf', 'maxmemory 128mb\n');
    write(clean, 'clean.ini', '[pgbouncer]\npool_mode = transaction\n');
    write(clean, 'clean.sql', 'SELECT 1;\n');
    write(clean, 'Dockerfile', 'FROM alpine:3.19\n');
    assert.equal(runGate(clean).violations.filter((v) => v.rule === 'CRLF/rule9').length, 0);
  } finally {
    cleanup(clean);
  }
});
