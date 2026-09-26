#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 0) sanity: repo root ==="
pwd
git rev-parse --show-toplevel

echo "=== 1) pre-commit review (no changes made yet) ==="
git status
echo "---"
git diff --stat

echo "=== 2) write the commit message file ==="
base64 -d > /tmp/p03_commit_msg.txt <<'B64EOF'
ZmVhdChnYXRld2F5KTogUDAuMyBtZWRpYSBoYW5kbGluZyAtIHN0cmVhbWluZyBkb3dubG9hZC91
cGxvYWQsIHF1b3RhcywgbWFnaWMtYnl0ZSB2ZXJpZmljYXRpb24KCkltcGxlbWVudHMgUDAuMyAo
RzMsIGRpc2FzdGVycyAjMTYvIzIxKSBwZXIgcHJvbXB0cy9QMF9ERUVQU0VFS19QUk9NUFQubWQg
wqc2OgoKLSBOZXcgZ2F0ZXdheS9zcmMvbWVkaWEuanM6IHN0cmVhbWluZyBtZWRpYSBkb3dubG9h
ZCB2aWEgQmFpbGV5cycKICBkb3dubG9hZENvbnRlbnRGcm9tTWVzc2FnZSAobm8gZnVsbC1idWZm
ZXIgcmVhZHMpLCBwZXItdHlwZSBzaXplIGNhcHMKICBmcm9tIGVudiAoaW1hZ2UvYXVkaW8gMTZN
QiwgdmlkZW8vZG9jdW1lbnQgNjRNQiwgc3RpY2tlciAyTUIpIGNoZWNrZWQKICBhZ2FpbnN0IHRo
ZSBkZWNsYXJlZCBmaWxlTGVuZ3RoIGJlZm9yZSBkb3dubG9hZCBzdGFydHMsIHN0cmVhbWluZwog
IG11bHRpcGFydCB1cGxvYWQgdmlhIEBhd3Mtc2RrL2xpYi1zdG9yYWdlJ3MgVXBsb2FkLCBhIGds
b2JhbCBkb3dubG9hZAogIGNvbmN1cnJlbmN5IHNlbWFwaG9yZSAoZGVmYXVsdCAzKSwgYSBwZXIt
c3RvcmUgZGFpbHkgcXVvdGEgKGZpbGVzICsKICBieXRlcykgdHJhY2tlZCBhdG9taWNhbGx5IGlu
IHJlZGlzLWR1cmFibGUgdmlhIEx1YSBzY3JpcHRzLCBtYWdpYy1ieXRlCiAgY29udGVudC10eXBl
IHZlcmlmaWNhdGlvbiB2aWEgZmlsZS10eXBlIG9uIHRoZSBmaXJzdCB+NEtCLCBhbmQgdGhlCiAg
c3BlYydzIG9iamVjdC1rZXkgZm9ybWF0IHtzZXNzaW9uX2lkfS97WVlZWX0ve01NfS97cHJvdmlk
ZXJfbWVzc2FnZV9pZH0ue2V4dH0uCgotIGdhdGV3YXkvc3JjL3Nlc3Npb25zLmpzOiB3aXJlIHRo
ZSBuZXcgc3RyZWFtaW5nIHBhdGggaW50bwogIHByb2Nlc3NJbmJvdW5kTWVzc2FnZSBhcyB0aGUg
ZGVmYXVsdCBmb3IgaXNNZWRpYSBoYW5kbGluZzsgZXh0ZW5kCiAgaXNNZWRpYSB0byBpbmNsdWRl
ICdzdGlja2VyJyAocHJldmlvdXNseSBzaWxlbnRseSBza2lwcGVkKTsga2VlcCB0aGUKICBsZWdh
Y3kgYnVmZmVyLWJhc2VkIGRlcHMuZG93bmxvYWRGbi91cGxvYWRGbiBwYXRoIHVuY2hhbmdlZCBm
b3IKICBiYWNrd2FyZCBjb21wYXRpYmlsaXR5IHdpdGggZXhpc3RpbmcgY2FsbGVycy90ZXN0cy4K
Ci0gZ2F0ZXdheS9wYWNrYWdlLmpzb24gKyBwYWNrYWdlLWxvY2suanNvbjogYWRkIEBhd3Mtc2Rr
L2xpYi1zdG9yYWdlIGFuZAogIGZpbGUtdHlwZSBkZXBlbmRlbmNpZXMuCgotIE5ldyBnYXRld2F5
L3NyYy9fX3Rlc3RzX18vbWVkaWFfbW9kdWxlLnRlc3QuanM6IGhlcm1ldGljIHVuaXQgdGVzdHMK
ICBmb3IgbWVkaWEuanMgKGNhcHMsIHF1b3RhIGxpbWl0cywgb2JqZWN0LWtleSBmb3JtYXQsIG1h
Z2ljLWJ5dGUKICBtYXRjaGluZywgZnVsbCBwaXBlbGluZSBoYXBweS9yZWplY3Rpb24gcGF0aHMs
IGNvbmN1cnJlbmN5IGNhcCkuCgotIGdhdGV3YXkvc3JjL19fdGVzdHNfXy9yZWFsX3JlZGlzX2lu
dGVncmF0aW9uLnRlc3QuanM6IGFkZCBhIHJlYWwtUmVkaXMKICB0ZXN0IHByb3ZpbmcgMjAwIGNv
bmN1cnJlbnQgcXVvdGEgcmVzZXJ2YXRpb25zIGFnYWluc3QgYSAyMC1maWxlIGNhcAogIHJlc29s
dmUgdG8gZXhhY3RseSAyMCBzdWNjZXNzZXMuCgotIGRvY3MvUDBfREVQRU5ERU5DSUVTLm1kLCBk
b2NzL1AwX0RFVklBVElPTlMubWQ6IGRvY3VtZW50IHRoZSB0d28gbmV3CiAgZGVwZW5kZW5jaWVz
IGFuZCB0d28gZGV2aWF0aW9ucyAoRC0xMyBvYmplY3Qta2V5IGZvcm1hdCBjb3JyZWN0aW9uLAog
IEQtMTQgcXVvdGEgc2NvcGUgZmFsbGluZyBiYWNrIHRvIHNlc3Npb25faWQgcGVuZGluZyBhIHRl
bmFudF9pZAogIGNvbmNlcHQpLgoKVmFsaWRhdGVkOiA3OC83OCBmYXN0IGhlcm1ldGljIHRlc3Rz
LCA0LzQgcmVhbC1yZWRpcy1kdXJhYmxlIHRlc3RzLAowIHJlYWwgaHVudF9nYXRlIHZpb2xhdGlv
bnMsIGxpdmUgZ2F0ZXdheS9nYXRld2F5LWZvcndhcmRlciBjb250YWluZXJzCmxlZnQgdW5kaXN0
dXJiZWQgKG5vdCB5ZXQgcmVzdGFydGVkIHdpdGggdGhpcyBjb2RlKS4KCkNvLUF1dGhvcmVkLUJ5
OiBDbGF1ZGUgU29ubmV0IDUgPG5vcmVwbHlAYW50aHJvcGljLmNvbT4KQ2xhdWRlLVNlc3Npb246
IGh0dHBzOi8vY2xhdWRlLmFpL2NvZGUvc2Vzc2lvbl8wMUNreEFLTWZnRXc4RGZOUHBtNmk3YjMK
B64EOF

echo "=== 3) stage EXACTLY the intended P0.3 files (no scratch/batch/log files) ==="
git add \
  docs/P0_DEPENDENCIES.md \
  docs/P0_DEVIATIONS.md \
  gateway/package-lock.json \
  gateway/package.json \
  gateway/src/__tests__/real_redis_integration.test.js \
  gateway/src/sessions.js \
  gateway/src/media.js \
  gateway/src/__tests__/media_module.test.js

echo "=== 4) verify the staged set is EXACTLY the expected 8 files - abort otherwise ==="
EXPECTED="$(cat <<'EOF' | sort
docs/P0_DEPENDENCIES.md
docs/P0_DEVIATIONS.md
gateway/package-lock.json
gateway/package.json
gateway/src/__tests__/real_redis_integration.test.js
gateway/src/sessions.js
gateway/src/media.js
gateway/src/__tests__/media_module.test.js
EOF
)"
STAGED="$(git diff --cached --name-only | sort)"

if [ "$EXPECTED" != "$STAGED" ]; then
  echo "----- EXPECTED -----"
  echo "$EXPECTED"
  echo "----- STAGED -----"
  echo "$STAGED"
  echo "ABORT: staged file set does not match the expected P0.3 file set - unstaging and refusing to commit."
  git reset
  exit 1
fi
echo "staged set OK: exactly the 8 expected P0.3 files"

echo "=== 5) commit ==="
git commit -F /tmp/p03_commit_msg.txt

echo "=== 6) confirm ==="
git log -1 --stat
echo "---"
git status
