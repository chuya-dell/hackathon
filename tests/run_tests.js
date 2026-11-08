// 計算の正しさを、合成データで確かめる。使い方: node run_tests.js [HTMLのパス]   (Node.js 18 以上)
// 画面の HTML(afm_analyzer.html)に埋め込まれた計算の部分を、そのまま取り出して使う。
// 作る物: 結果_計算の確認.md と 結果_計算の確認.json (この tests フォルダの中)
'use strict';
const fs = require('fs'), path = require('path');
const HERE = __dirname;
const htmlPath = process.argv[2] || path.join(HERE, '..', 'afm_analyzer.html');
const src = fs.readFileSync(htmlPath, 'utf8');
const a = src.indexOf('/* ===== 計算の部分(');
const b = src.indexOf('/* ===== 計算の部分、ここまで');
if (a < 0 || b < 0) throw new Error('計算の部分が見つかりません: ' + htmlPath);
const AFMCore = new Function(src.slice(a, b) + '\nreturn AFMCore;')();

const rows = [];   // {name, cond, expected, got, diff, tol, unit, ok}
function check(name, cond, expected, got, tol, unit) {
  const diff = got - expected, ok = Math.abs(diff) <= tol;
  rows.push({ name, cond, expected, got, diff, tol, unit: unit || '', ok });
  return ok;
}
function checkEq(name, cond, expected, got) {
  const ok = expected === got;
  rows.push({ name, cond, expected, got, diff: got - expected, tol: 0, unit: '', ok });
  return ok;
}
function note(name, cond, expected, got, diff, unit, ok) {
  rows.push({ name, cond, expected, got, diff, tol: '参考', unit, ok });
}
function loadCsv(name) {
  const m = AFMCore.parseMatrixText(fs.readFileSync(path.join(HERE, 'data', name + '.csv'), 'utf8'));
  const truth = JSON.parse(fs.readFileSync(path.join(HERE, 'data', name + '_truth.json'), 'utf8'));
  return { ...m, truth };
}
const R = (x0, y0, x1, y1) => ({ x0, y0, x1, y1 });

// ---- 0) 部品の確認: 答えが分かっている関数 ----
{
  const W = 64, H = 48, z = new Float64Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const X = (x - (W - 1) / 2) / ((W - 1) / 2), Y = (y - (H - 1) / 2) / ((H - 1) / 2);
    z[y * W + x] = 5 + 30 * X - 20 * Y + 12 * X * Y + 7 * X * X - 4 * Y * Y + 9 * X * X * X - 3 * X * Y * Y;
  }
  for (const order of [1, 2, 3]) {
    const r = AFMCore.flatten(z, W, H, null, order);
    let mx = 0; for (const v of r.out) mx = Math.max(mx, Math.abs(v));
    if (order === 3) check('三次の多項式を三次で補正', 'ノイズなしの厳密な三次関数。補正後は 0 になるはず', 0, mx, 1e-9, 'nm');
    else note(order + '次で補正(三次の多項式)', '次数が足りないので、残るのが正しい', '残る', mx.toFixed(3), '', 'nm', mx > 0.1);
  }
  const alt = new Float64Array(16 * 16); for (let i = 0; i < alt.length; i++) alt[i] = (i % 2) * 10;
  const q = AFMCore.roughness(alt, 16, 16, null);
  check('算術平均粗さ', '値が 0 と 10 の交互', 5, q.sa, 1e-12, 'nm');
  check('二乗平均平方根粗さ', '値が 0 と 10 の交互', 5, q.sq, 1e-12, 'nm');
  check('最大高さ', '値が 0 と 10 の交互', 10, q.sz, 1e-12, 'nm');
  check('平均の高さ', '値が 0 と 10 の交互', 5, q.mean, 1e-12, 'nm');
  const sn = new Float64Array(1000); for (let i = 0; i < 1000; i++) sn[i] = 8 * Math.sin(2 * Math.PI * i / 1000);
  const s2 = AFMCore.roughness(sn, 1000, 1, null);
  check('算術平均粗さ', '振幅 8 の正弦波(理論値 2A/π)', 16 / Math.PI, s2.sa, 0.01, 'nm');
  check('二乗平均平方根粗さ', '振幅 8 の正弦波(理論値 A/√2)', 8 / Math.SQRT2, s2.sq, 0.01, 'nm');
}

// ---- 1) 依頼の合成データ: 30 マイクロメートル、10×10 のピラー、高さ 250 ナノメートル ----
const D = loadCsv('synthetic_square_30um');
{
  const { data, W, H } = D, px = D.truth.scan_nm / W;
  const T = D.truth.pillar_height_nm;
  const pitchPx = D.truth.pitch_nm / px, floorRects = [], topRects = [];
  for (let i = 1; i < 10; i++) for (let j = 1; j < 10; j++) {   // 床: ピラー 4 個の間の交点 6 画素角を 81 か所
    const cx = i * pitchPx, cy = j * pitchPx;
    floorRects.push(R(cx - 3, cy - 3, cx + 3, cy + 3));
  }
  for (let i = 0; i < 10; i++) for (let j = 0; j < 10; j++) {   // 頂上: ピラーの中心 8 画素角を 100 か所
    const cx = (i + 0.5) * pitchPx, cy = (j + 0.5) * pitchPx;
    topRects.push(R(cx - 4, cy - 4, cx + 4, cy + 4));
  }
  const floorMask = AFMCore.rectsMask(floorRects, W, H), topMask = AFMCore.rectsMask(topRects, W, H);
  { // 次数不足の確認(参考): この合成データには二次の湾曲を足してあるので、一次では残る
    const g1 = AFMCore.flatten(data, W, H, null, 1);
    note('ピラーの高さ(方法イ・1次)', '二次の湾曲を含むデータを一次で補正 → 次数不足で外れるのが正しい', T, AFMCore.heightAuto(g1.out, W, H, null).height.toFixed(1), (AFMCore.heightAuto(g1.out, W, H, null).height - T).toFixed(1), 'nm', true);
  }
  for (const order of [2, 3]) {
    const f = AFMCore.flatten(data, W, H, floorMask, order);
    check('ピラーの高さ(方法ア)', order + '次・床の四角形で補正 → 頂上の平均 − 床の平均', T,
      AFMCore.maskMean(f.out, topMask) - AFMCore.maskMean(f.out, floorMask), 2, 'nm');
    const g = AFMCore.flatten(data, W, H, null, order);
    check('ピラーの高さ(方法ア)', order + '次・画像全体で補正 → 頂上の平均 − 床の平均', T,
      AFMCore.maskMean(g.out, topMask) - AFMCore.maskMean(g.out, floorMask), 2, 'nm');
    check('ピラーの高さ(方法イ・推定)', order + '次・画像全体で補正 → ヒストグラムの山の差', T, AFMCore.heightAuto(g.out, W, H, null).height, 2, 'nm');
    check('ピラーの高さ(方法イ・推定)', order + '次・床の四角形で補正 → ヒストグラムの山の差', T, AFMCore.heightAuto(f.out, W, H, null).height, 2, 'nm');
  }
  const raw = AFMCore.heightAuto(data, W, H, null);
  note('ピラーの高さ(方法イ・補正なし)', '補正しないと大きく外れるのが正しい', T, raw.height.toFixed(1), (raw.height - T).toFixed(1), 'nm', Math.abs(raw.height - T) > 5);
  const g2 = AFMCore.flatten(data, W, H, null, 2);
  const c = AFMCore.countPillars(g2.out, W, H, px, px, null);
  checkEq('ピラーの個数(全体)', '二次で補正 → 中間値で二値化', 100, c.count);
  checkEq('ピラーの行数', '縦に並ぶ数', 10, c.rows);
  checkEq('1行あたりの個数', '横に並ぶ数', 10, c.perRow);
  check('中心間の間隔', '真の値 3000 nm', D.truth.pitch_nm, c.spacing, 15, 'nm');
  let mu = 0, cnt = 0;
  for (const r of floorRects) { const q = AFMCore.roughness(g2.out, W, H, r); mu += q.sq * q.sq * q.n; cnt += q.n; }
  check('二乗平均平方根粗さ(床 81 か所をまとめて)', '真の値 = ノイズの標準偏差 2 nm', D.truth.noise_sd_nm, Math.sqrt(mu / cnt), 0.2, 'nm');
  const q1 = AFMCore.roughness(g2.out, W, H, topRects[44]);
  check('二乗平均平方根粗さ(頂上 1 か所)', '真の値 = ノイズの標準偏差 2 nm(標本が 64 画素なので ±0.8 を許す)', D.truth.noise_sd_nm, q1.sq, 0.8, 'nm');
  // 基準の平行移動: 初期(補正に使った範囲=画像全体の平均を 0)では、床は負の側に下がる。床の平均を 0 にする設定では頂上が 250 nm になる。
  const frac = AFMCore.maskMean(g2.out, topMask) - AFMCore.maskMean(g2.out, floorMask);
  const g3 = AFMCore.flatten(data, W, H, null, 2, floorMask);
  check('平均の高さ(頂上 1 か所)', '「床の四角形の平均を 0 にする」設定。頂上の平均は 250 nm', T, AFMCore.roughness(g3.out, W, H, topRects[44]).mean, 2, 'nm');
  check('平均の高さ(床 81 か所)', '「床の四角形の平均を 0 にする」設定。床の平均は 0 nm', 0, AFMCore.maskMean(g3.out, floorMask), 1e-9, 'nm');
  check('補正後の画像全体の平均', '初期設定(補正に使った範囲の平均を 0)。画像全体で補正したので 0 nm', 0, AFMCore.maskMean(g2.out, null), 1e-9, 'nm');
  void frac;
}

// ---- 2) 一次だけの背景 / 三次を含む背景 ----
{
  const d = loadCsv('synthetic_square_30um_tilt1');
  for (const order of [1, 2, 3]) {
    const r = AFMCore.flatten(d.data, d.W, d.H, null, order);
    check('ピラーの高さ(方法イ・推定)', '一次の背景のみ・' + order + '次・画像全体', d.truth.pillar_height_nm, AFMCore.heightAuto(r.out, d.W, d.H, null).height, 2, 'nm');
  }
  const e = loadCsv('synthetic_square_30um_cubic');
  const r3 = AFMCore.flatten(e.data, e.W, e.H, null, 3);
  check('ピラーの高さ(方法イ・推定)', '三次を含む背景・3次・画像全体', e.truth.pillar_height_nm, AFMCore.heightAuto(r3.out, e.W, e.H, null).height, 2, 'nm');
  const r2 = AFMCore.flatten(e.data, e.W, e.H, null, 2), au = AFMCore.heightAuto(r2.out, e.W, e.H, null);
  note('次数不足の確認', '三次を含む背景を二次で補正した場合の高さ(誤差が大きければ次数を上げる判断に使う)', e.truth.pillar_height_nm, au.height.toFixed(1), (au.height - e.truth.pillar_height_nm).toFixed(1), 'nm', true);
}

// ---- 3) 実試料に近い: 3 マイクロメートル、六方格子 ----
{
  const d = loadCsv('synthetic_hex_3um'), { data, W, H } = d, px = d.truth.scan_nm / W;
  for (const order of [1, 2]) {
    const g = AFMCore.flatten(data, W, H, null, order);
    const hh = AFMCore.heightAuto(g.out, W, H, null).height;
    note('ピラーの高さ(方法イ・推定)', '六方格子 3 µm・' + order + '次・画像全体で補正(ピラーが偏って並ぶ小さい像では、補正が数 nm ずれうる。参考)', d.truth.pillar_height_nm, hh.toFixed(1), (hh - d.truth.pillar_height_nm).toFixed(1), 'nm', Math.abs(hh - d.truth.pillar_height_nm) <= 6);
  }
  { // 床の四角形(ピラー 3 個の真ん中の隙間 6 画素角)を数か所指定して補正する
    const a = 460, floorR = [], topR = [];
    let row = 0;
    for (let y = a * 0.5; y < 3000 - a * 0.25; y += a * Math.sqrt(3) / 2, row++) {
      for (let x = a * 0.5 + (row % 2 ? a * 0.5 : 0); x < 3000 - a * 0.25; x += a) {
        topR.push(R(x / px - 4, y / px - 4, x / px + 4, y / px + 4));
        const fx = (x + a / 2) / px, fy = (y + a * Math.sqrt(3) / 6) / px;
        if (fx < W - 4 && fy < H - 4) floorR.push(R(fx - 3, fy - 3, fx + 3, fy + 3));
      }
    }
    const fm = AFMCore.rectsMask(floorR, W, H), tm = AFMCore.rectsMask(topR, W, H);
    for (const order of [1, 2]) {
      const f = AFMCore.flatten(data, W, H, fm, order);
      check('ピラーの高さ(方法ア)', '六方格子 3 µm・' + order + '次・床の四角形(' + floorR.length + ' か所)で補正', d.truth.pillar_height_nm, AFMCore.maskMean(f.out, tm) - AFMCore.maskMean(f.out, fm), 2, 'nm');
    }
  }
  const g = AFMCore.flatten(data, W, H, null, 2);
  const c = AFMCore.countPillars(g.out, W, H, px, px, null);
  checkEq('ピラーの個数(六方格子・全体)', '真の値(作った個数)', d.truth.pillar_count, c.count);
  check('中心間の間隔(六方格子)', '真の値 460 nm', 460, c.spacing, 10, 'nm');
}

// ---- 4) 表の読み込み ----
{
  const m = AFMCore.parseMatrixText('Z map\nunit: nm\n1\t2\t3\t4\n5\t6\t7\t8\n9\t10\t11\t12\n13\t14\t15\t16\n');
  checkEq('表の読み込み(タブ区切り・見出し行つき)', '4行4列なら 16 個', 16, m.data.length);
  const m2 = AFMCore.parseMatrixText('1,2,3,4\n5,6,7,8\n9,10,11,12\n13,14,15,16');
  checkEq('表の読み込み(コンマ区切り)', '4行4列の合計 136', 136, Array.from(m2.data).reduce((s, v) => s + v, 0));
  const m3 = AFMCore.parseMatrixText('1 2 3 4\r\n5 6 7 8\r\n9 10 11 12\r\n13 14 15 16\r\n');
  checkEq('表の読み込み(空白区切り・改行が CR+LF)', '列数 4', 4, m3.W);
}

// ---- 結果の書き出し ----
const pass = rows.filter(r => r.ok).length;
const f = v => (typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : String(v));
let md = '# 計算の確認の結果\n\n';
md += '- 実行日時: ' + new Date().toISOString() + '\n- 読み込んだ HTML: `' + path.basename(htmlPath) + '`\n- Node.js: ' + process.version + '\n';
md += '- 合成データ: `tests/data/*.csv`(`make_synthetic.py` で作成。真の値は `*_truth.json`)\n';
md += '- 合格の目安: ピラーの高さは真の値(250 nm)から ±2 nm 以内。ピラーの個数は縦横 10 個(合計 100 個)\n\n';
md += '**結果: ' + rows.length + ' 項目のうち ' + pass + ' 項目が合格' + (pass === rows.length ? '(すべて合格)' : '(不合格あり)') + '**\n\n';
md += '| 項目 | 条件 | 期待(真の値) | 計算結果 | 差 | 許容 | 判定 |\n|---|---|---|---|---|---|---|\n';
for (const r of rows) md += '| ' + [r.name, r.cond, f(r.expected) + (r.unit ? ' ' + r.unit : ''), f(r.got) + (r.unit ? ' ' + r.unit : ''), r.diff === '' ? '' : f(Number(r.diff)), (r.tol === '' ? '' : (typeof r.tol === 'number' ? '±' + r.tol : r.tol)), r.ok ? '合格' : '**不合格**'].join(' | ') + ' |\n';
fs.writeFileSync(path.join(HERE, '結果_計算の確認.md'), md, 'utf8');
fs.writeFileSync(path.join(HERE, '結果_計算の確認.json'), JSON.stringify(rows, null, 1), 'utf8');
console.log(md);
process.exitCode = pass === rows.length ? 0 : 1;
