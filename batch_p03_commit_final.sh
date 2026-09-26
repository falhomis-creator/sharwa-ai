#!/usr/bin/env bash
set -euo pipefail
cd /home/sharwa/sharwa_ai

echo "=== 0) sanity: repo root ==="
pwd
git rev-parse --show-toplevel

echo "=== 1) re-stage exactly the P0.3-related files (idempotent, safe if already staged) ==="
git add gateway/src/media.js \
        gateway/src/__tests__/media_e2e_minio.test.js \
        gateway/src/__tests__/_media_e2e_kill_child.mjs \
        docker-compose.test.yml \
        docs/P0_FINDINGS.md \
        docs/P0_DEVIATIONS.md \
        docs/P0_OPEN_QUESTIONS.md \
        docs/P0_PROGRESS.md \
        .env.example

echo "=== 2) final diff review before commit ==="
git status
echo "---"
git diff --cached --stat

echo "=== 3) write commit message and commit ==="
base64 -d > /tmp/p03_commit_msg.txt <<'B64EOF'
ZmVhdChtZWRpYSk6IGNsb3NlIFAwLjMg4oCUIHJlYWwgTWluSU8gRTJFIGFjY2VwdGFuY2UgdGVz
dHMgKyBiYWNrcHJlc3N1cmUgZml4CgotIEFkZCBnYXRld2F5L3NyYy9fX3Rlc3RzX18vbWVkaWFf
ZTJlX21pbmlvLnRlc3QuanM6IHRoZSBvZmZpY2lhbCBQMC4zCiAgYWNjZXB0YW5jZSBzdWl0ZSAo
RTEtRTQpLCBydW4gYWdhaW5zdCByZWFsIE1pbklPICsgcmVhbCByZWRpcy1kdXJhYmxlIHZpYQog
IGRvY2tlci1jb21wb3NlLnRlc3QueW1sIChhZGRpdGl2ZS1vbmx5LCBSVU5fTUVESUFfRTJFX1RF
U1RTPTEgZ2F0ZWQpLgogIEUxOiA1ME1CIHN0cmVhbWVkLCBSU1MgZ3Jvd3RoIDMzLjBNQiAoPDEw
ME1CKS4gRTI6IDEweDUwTUIgY29uY3VycmVudCwKICBwZWFrIFJTUyAxOTkuN01CICg8NjAwTUIp
LCBtZWRpYV9pbmZsaWdodCBuZXZlciBleGNlZWRzIDMuIEUzOiBraWxsIC05CiAgbWlkLXVwbG9h
ZCwgemVybyBvcnBoYW5lZCBtdWx0aXBhcnQgdXBsb2FkcywgY2xlYW4gcmV0cnkuIEU0YS9iOiBv
dmVyLWNhcAogIGFuZCBkaXNndWlzZWQtdHlwZSBmaWxlcyByZWplY3RlZCB3aXRoIHplcm8gbmV0
d29yayBieXRlcyAvIHJlamVjdGVkX3R5cGUuCi0gRml4IGEgcmVhbCBiYWNrcHJlc3N1cmUgYnVn
IGluIG1lZGlhLmpzIGZvdW5kIHdoaWxlIGJ1aWxkaW5nIEUxOiB0aGUKICBQYXNzVGhyb3VnaCB3
cml0ZSBsb29wIGlnbm9yZWQgc3RyZWFtLndyaXRlKCkncyBmYWxzZSByZXR1cm4gdmFsdWUsCiAg
bGV0dGluZyB0aGUgYnVmZmVyIGJldHdlZW4gZG93bmxvYWQgYW5kIFMzIG11bHRpcGFydCB1cGxv
YWQgZ3JvdwogIHVuYm91bmRlZCB3aGVuIHVwbG9hZCBpcyBzbG93ZXIgdGhhbiBkb3dubG9hZCAt
IHRoZSBleGFjdCBmYWlsdXJlIGNsYXNzCiAgUDAuMyBleGlzdHMgdG8gcHJldmVudCAoZGlzYXN0
ZXJzICMxNi8jMjEpLiBOb3cgYXdhaXRzICdkcmFpbicgYmVmb3JlCiAgY29udGludWluZy4gUmVk
dWNlZCA1ME1CLXVwbG9hZCBSU1MgZ3Jvd3RoIGZyb20gYSBib3JkZXJsaW5lIH4xMDBNQiB0byBh
CiAgc3RhYmxlIH4zMy01NE1CLCBhbmQgMTAtY29uY3VycmVudCBwZWFrIGZyb20gfjQ0MS01MzNN
QiB0byB+MTk5LTI3NU1CLgotIEFkZCBnZXRNZWRpYU1ldHJpY3MoKS9yZXNldE1lZGlhTWV0cmlj
cygpIGluLXByb2Nlc3MgYWNjb3VudGluZyBmb3IgdGhlCiAgZm91ciBQMC4zIHNwZWMgbWV0cmlj
cyAobWVkaWFfYnl0ZXNfdG90YWwvaW5mbGlnaHQvZmFpbHVyZXMvZHVyYXRpb24pOwogIHB1Ymxp
YyAvbWV0cmljcyBIVFRQIGV4cG9zdXJlIGRlZmVycmVkIHRvIFAwLjYgcGVyIHNwZWMncyBvd24g
cGhhc2luZy4KLSBkb2NzL1AwX0ZJTkRJTkdTLm1kIEY1LCBkb2NzL1AwX0RFVklBVElPTlMubWQg
RC0xNS9ELTE2L0QtMTcsCiAgZG9jcy9QMF9PUEVOX1FVRVNUSU9OUy5tZCBPUS03LCBkb2NzL1Aw
X1BST0dSRVNTLm1kIFAwLjMgY2xvc3VyZSBzZWN0aW9uCiAgd2l0aCByZWFsIHJ1biBldmlkZW5j
ZS4KCkNvLUF1dGhvcmVkLUJ5OiBDbGF1ZGUgU29ubmV0IDUgPG5vcmVwbHlAYW50aHJvcGljLmNv
bT4KQ2xhdWRlLVNlc3Npb246IGh0dHBzOi8vY2xhdWRlLmFpL2NvZGUvc2Vzc2lvbl8wMUNreEFL
TWZnRXc4RGZOUHBtNmk3YjMK
B64EOF
git commit -F /tmp/p03_commit_msg.txt
rm -f /tmp/p03_commit_msg.txt

echo "=== 4) verify ==="
git log -1 --stat
echo "---"
git status
