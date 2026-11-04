# results/

`examples/` 5本の通し実行の結果(取り出し JSON、規則帳、前後比較の表)を置く場所。

2026-11-04 時点: 2026-11-05: `saihatsu --model haiku` で通し実行済み。`run_2026-11-05_manual.md` は、認証が通らなかったときの手動の代行の記録(参考)。

```bash
for i in 01 02 03 04 05; do
  saihatsu extract examples/${i}_*.md -o results/extract_$i.json
  saihatsu add results/extract_$i.json --rules results/rules.md
done
for i in 01 02 03 04 05; do
  saihatsu check examples/${i}_*.md --rules results/rules.md --extraction results/extract_$i.json -o results/check_$i.md
done
```
