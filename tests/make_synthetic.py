# -*- coding: utf-8 -*-
"""合成データ(真の値が分かる原子間力顕微鏡の像)を作る。
使い方: python make_synthetic.py   (numpy が必要)
出力: data/*.csv (行列。値の単位はナノメートル)と data/*_truth.json (真の値)
"""
import json, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(20261102)


def supersample_mask(fn, n, ss=4):
    """画素の内側を ss×ss で細かく見て、ピラーが占める割合(0〜1)を返す。"""
    g = (np.arange(n * ss) + 0.5) / ss
    X, Y = np.meshgrid(g, g)
    m = fn(X, Y).astype(float)
    return m.reshape(n, ss, n, ss).mean(axis=(1, 3))


def background(n, size_nm, tilt_x, tilt_y, bowl, cubic=0.0):
    """既知の傾き(一次)・湾曲(二次)・三次の背景。x,y は -1〜1 に正規化。"""
    u = np.linspace(-1, 1, n)
    X, Y = np.meshgrid(u, u)
    return tilt_x * X + tilt_y * Y + bowl * (X ** 2 + Y ** 2) + cubic * (X ** 3 - 2 * X * Y ** 2)


def square_pillars(n, size_nm, count, diameter_nm, height_nm):
    px = size_nm / n
    pitch = size_nm / count
    r = diameter_nm / 2 / px  # 画素単位の半径
    pp = pitch / px
    def inside(X, Y):
        cx = (np.floor(X / pp) + 0.5) * pp
        cy = (np.floor(Y / pp) + 0.5) * pp
        return (X - cx) ** 2 + (Y - cy) ** 2 <= r ** 2
    return height_nm * supersample_mask(inside, n), pitch


def hex_pillars(n, size_nm, pitch_nm, diameter_nm, height_nm):
    """六方格子。行ごとに半ピッチずらす。中心の一覧も返す。"""
    px = size_nm / n
    a = pitch_nm
    centers = []
    row = 0
    y = a * 0.5
    while y < size_nm - a * 0.25:
        x0 = a * 0.5 + (a * 0.5 if row % 2 else 0)
        x = x0
        while x < size_nm - a * 0.25:
            centers.append((x, y))
            x += a
        y += a * np.sqrt(3) / 2
        row += 1
    centers = np.array(centers) / px
    r = diameter_nm / 2 / px
    ss = 4
    g = (np.arange(n * ss) + 0.5) / ss
    X, Y = np.meshgrid(g, g)
    m = np.zeros_like(X, dtype=bool)
    for cx, cy in centers:
        m |= (X - cx) ** 2 + (Y - cy) ** 2 <= r ** 2
    return height_nm * m.astype(float).reshape(n, ss, n, ss).mean(axis=(1, 3)), len(centers)


def save(name, z, meta):
    np.savetxt(os.path.join(OUT, name + ".csv"), z, fmt="%.4f", delimiter=",")
    with open(os.path.join(OUT, name + "_truth.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# 1) 依頼どおり: 30 マイクロメートル、256×256、高さ 250 ナノメートル、10×10 の円柱、一次+二次+少量のノイズ
n, size = 256, 30000.0
h = 250.0
pil, pitch = square_pillars(n, size, 10, 1500.0, h)
bg = background(n, size, tilt_x=600.0, tilt_y=-400.0, bowl=150.0)
noise_sd = 2.0
z = pil + bg + rng.normal(0, noise_sd, (n, n))
save("synthetic_square_30um", z, dict(
    説明="30マイクロメートル四方・256x256・円柱ピラー10x10(間隔3000nm・直径1500nm)・高さ250nm・一次の傾き+二次の湾曲+ノイズ",
    scan_nm=size, n=n, pillar_height_nm=h, pillar_rows=10, pillar_cols=10, pitch_nm=pitch,
    noise_sd_nm=noise_sd, tilt_x_nm=600.0, tilt_y_nm=-400.0, bowl_nm=150.0,
    floor_noise_sq_nm=noise_sd))

# 2) 一次だけ(補正の次数の確認用)
z1 = pil + background(n, size, 800.0, 500.0, 0.0) + rng.normal(0, noise_sd, (n, n))
save("synthetic_square_30um_tilt1", z1, dict(説明="一次の傾きのみ", scan_nm=size, n=n, pillar_height_nm=h,
                                             pillar_rows=10, pillar_cols=10, noise_sd_nm=noise_sd))

# 3) 三次の背景を足したもの
z3 = pil + background(n, size, 500.0, -300.0, 120.0, cubic=80.0) + rng.normal(0, noise_sd, (n, n))
save("synthetic_square_30um_cubic", z3, dict(説明="一次+二次+三次の背景", scan_nm=size, n=n, pillar_height_nm=h,
                                              pillar_rows=10, pillar_cols=10, noise_sd_nm=noise_sd))

# 4) 実試料に近い: 3マイクロメートル四方・六方格子・間隔460nm・直径230nm・高さ250nm
pil_h, cnt = hex_pillars(n, 3000.0, 460.0, 230.0, h)
zh = pil_h + background(n, 3000.0, 60.0, -40.0, 20.0) + rng.normal(0, 1.0, (n, n))
save("synthetic_hex_3um", zh, dict(説明="3マイクロメートル四方・六方格子(間隔460nm・直径230nm)・高さ250nm",
                                  scan_nm=3000.0, n=n, pillar_height_nm=h, pillar_count=int(cnt), noise_sd_nm=1.0))
print("ok: hex count =", cnt)
