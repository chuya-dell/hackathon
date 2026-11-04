# results/

`examples/` 5本の通し実行の結果(取り出し JSON、規則帳、前後比較の表)を置く場所。

2026-11-04 時点: 通し実行は未実施。実行後にここへ保存する。

```bash
for i in 01 02 03 04 05; do
  saihatsu extract examples/${i}_*.md -o results/extract_$i.json
  saihatsu add results/extract_$i.json --rules results/rules.md
done
for i in 01 02 03 04 05; do
  saihatsu check examples/${i}_*.md --rules results/rules.md --extraction results/extract_$i.json -o results/check_$i.md
done
```
