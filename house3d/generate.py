"""座標表 (coords.json) からシーンJSON (scene.json) と3Dビューア (index.html) を生成する。

使い方:
    python3 generate.py                     # scene.json と index.html を出力
    python3 generate.py --check 1F.png 2F.png OUT_DIR
        図面画像に生成した壁を赤線で重ねた確認画像を出力 (差分チェック用)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
DOOR_H = 2100


def mm(v):
    return round(v / 1000, 4)


def rect_poly(r):
    x1, y1, x2, y2 = r
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def centroid(poly):
    a = cx = cy = 0
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        c = x1 * y2 - x2 * y1
        a += c
        cx += (x1 + x2) * c
        cy += (y1 + y2) * c
    a /= 2
    return cx / (6 * a), cy / (6 * a)


class Scene:
    def __init__(self, ox, oz):
        self.ox, self.oz = ox, oz
        self.boxes, self.slabs, self.labels = [], [], []

    def box(self, group, mat, x1, y1, x2, y2, z1, z2, name=None):
        """平面 (x1,y1)-(x2,y2) mm、高さ z1..z2 mm の直方体。"""
        b = {
            "g": group, "m": mat,
            "p": [mm((x1 + x2) / 2 - self.ox), mm((z1 + z2) / 2), mm((y1 + y2) / 2 - self.oz)],
            "s": [mm(abs(x2 - x1)), mm(z2 - z1), mm(abs(y2 - y1))],
        }
        if name:
            b["n"] = name
        self.boxes.append(b)

    def slab(self, group, mat, poly, holes, z, thick):
        to = lambda pts: [[mm(x - self.ox), mm(y - self.oz)] for x, y in pts]
        self.slabs.append({"g": group, "m": mat, "poly": to(poly),
                           "holes": [to(h) for h in holes], "y": mm(z), "t": mm(thick)})

    def label(self, group, text, x, y, z):
        self.labels.append({"g": group, "t": text, "p": [mm(x - self.ox), mm(z), mm(y - self.oz)]})


def add_wall(sc, g, w, base, height):
    (ax, ay), (bx, by) = w["a"], w["b"]
    t = w.get("t", 120)
    horiz = ay == by
    if not horiz and ax != bx:
        raise ValueError(f"斜めの壁は未対応: {w}")
    start, end = (min(ax, bx), max(ax, bx)) if horiz else (min(ay, by), max(ay, by))
    fixed = ay if horiz else ax
    mat = "glass" if w.get("kind") == "glass" else "wall"

    def seg(s, e, z1, z2, m=mat, pad=0):
        if e - s <= 0 or z2 - z1 <= 0:
            return
        s, e = s - pad, e + pad
        if horiz:
            sc.box(g, m, s, fixed - t / 2, e, fixed + t / 2, base + z1, base + z2)
        else:
            sc.box(g, m, fixed - t / 2, s, fixed + t / 2, e, base + z1, base + z2)

    if mat == "glass":
        seg(start, end, 0, height, "glass")
        seg(start, end, height - 60, height, "frame")
        return

    cur = start
    for o in sorted(w.get("open", []), key=lambda o: o["from"]):
        os_, oe = start + o["from"], start + o["from"] + o["w"]
        seg(cur, os_, 0, height, pad=t / 2 if cur == start else 0)
        if o["type"] == "door":
            seg(os_, oe, DOOR_H, height)
        else:
            sill, top = o["sill"], o["sill"] + o["h"]
            seg(os_, oe, 0, sill)
            seg(os_, oe, top, height)
            if horiz:
                sc.box(g, "glass", os_, fixed - 15, oe, fixed + 15, base + sill, base + top)
            else:
                sc.box(g, "glass", fixed - 15, os_, fixed + 15, oe, base + sill, base + top)
        cur = oe
    seg(cur, end, 0, height, pad=t / 2)


def add_furniture(sc, g, f, base):
    x, y, w, d, h = f["x"], f["y"], f["w"], f["d"], f["h"]
    kind = f["kind"]
    if kind == "sofa":
        sc.box(g, "sofa", x, y, x + w, y + d, base, base + h, f["name"])
        # 背もたれは長辺側の壁寄り
        if w >= d:
            sc.box(g, "sofa", x, y, x + w, y + 180, base + h, base + 750)
        else:
            sc.box(g, "sofa", x, y, x + 180, y + d, base + h, base + 750)
    elif kind == "table":
        sc.box(g, "table", x, y, x + w, y + d, base + h - 40, base + h, f["name"])
        for lx, ly in ((x + 40, y + 40), (x + w - 80, y + 40), (x + 40, y + d - 80), (x + w - 80, y + d - 80)):
            sc.box(g, "table", lx, ly, lx + 40, ly + 40, base, base + h - 40)
    elif kind == "round":
        sc.box(g, "round", x, y, x + w, y + d, base, base + h, f["name"])
    elif kind == "chair":
        sc.box(g, "chair", x, y, x + w, y + d, base, base + h, f["name"])
        sc.box(g, "chair", x, y + d - 120, x + w, y + d, base + h, base + 850)
    else:
        sc.box(g, kind, x, y, x + w, y + d, base, base + h, f["name"])


def build(data):
    H, SLAB = data["storeyHeight"], data["slab"]
    W = max(f["size"][0] for f in data["floors"])
    D = max(f["size"][1] for f in data["floors"])
    sc = Scene(W / 2, D / 2)

    for i, fl in enumerate(data["floors"]):
        g, base = fl["name"], i * H
        top = i == len(data["floors"]) - 1
        wall_h = H - (0 if not top else 300)
        for w in fl["walls"]:
            add_wall(sc, g, w, base, wall_h)
        for r in fl["rooms"]:
            poly = r.get("poly") or rect_poly(r["rect"])
            cx, cy = centroid(poly)
            sc.label(g, r["name"], cx, cy, base + 1200)
            if r.get("void"):
                continue
            mat = r.get("floor", "wood")
            if i == 0:
                sc.slab(g, mat, poly, r.get("holes", []), base, 30)
            else:
                sc.slab(g, mat, poly, r.get("holes", []), base, SLAB)
            if top and not r.get("outdoor"):
                sc.slab("roof", "roof", poly, r.get("holes", [])[:0], base + wall_h + SLAB, SLAB)
        for s in fl.get("stairs", []):
            n = s["steps"]
            tread = s["len"] / n
            for k in range(n):
                rise = (k + 1) * H / n
                if s["dir"] == "west":
                    x2 = s["x"] + s["len"] - k * tread
                    x1 = x2 - tread
                else:
                    x1 = s["x"] + k * tread
                    x2 = x1 + tread
                sc.box(g, "stair", x1, s["y"], x2, s["y"] + s["w"], rise - 40, rise)
                sc.box(g, "stringer", x1, s["y"], x2, s["y"] + s["w"], max(0, rise - 400), rise - 40)
        for rl in fl.get("railings", []):
            (ax, ay), (bx, by) = rl["a"], rl["b"]
            sc.box(g, "frame", min(ax, bx) - 20, min(ay, by) - 20, max(ax, bx) + 20, max(ay, by) + 20,
                   base + 1000, base + 1050)
            sc.box(g, "glass", min(ax, bx) - 8, min(ay, by) - 8, max(ax, bx) + 8, max(ay, by) + 8,
                   base + 100, base + 1000)
        for f in fl.get("furniture", []):
            add_furniture(sc, g, f, base)

    # 敷地の地面
    sc.box("site", "ground", -1500, -1500, W + 1500, D + 1500, -100, -10)
    return {
        "unit": "m",
        "size": [mm(W), mm(D)],
        "storeyHeight": mm(H),
        "groups": [f["name"] for f in data["floors"]] + ["roof"],
        "boxes": sc.boxes,
        "slabs": sc.slabs,
        "labels": sc.labels,
    }


def check(data, images, out_dir):
    """図面画像の上に壁の中心線を赤で描く。座標→画素の変換は図面の寸法線から求めた値。"""
    from PIL import Image, ImageDraw

    # (原点の画素, mm→px の倍率) — 2倍拡大した切り抜き画像に対する値
    calib = {"1F": ((120, 130), (0.1168, 0.1193)), "2F": ((100, 140), (0.1181, 0.1180))}
    for fl, path in zip(data["floors"], images):
        (px0, py0), (kx, ky) = calib[fl["name"]]
        im = Image.open(path).convert("RGB")
        dr = ImageDraw.Draw(im)
        P = lambda p: (px0 + p[0] * kx, py0 + p[1] * ky)
        for w in fl["walls"]:
            dr.line([P(w["a"]), P(w["b"])], fill=(230, 0, 0), width=3)
            (ax, ay), (bx, by) = w["a"], w["b"]
            horiz = ay == by
            for o in w.get("open", []):
                s = min(ax, bx) + o["from"] if horiz else min(ay, by) + o["from"]
                e = s + o["w"]
                a, b = ((s, ay), (e, ay)) if horiz else ((ax, s), (ax, e))
                dr.line([P(a), P(b)], fill=(0, 90, 255) if o["type"] == "window" else (255, 200, 0), width=5)
        for r in fl["rooms"]:
            poly = r.get("poly") or rect_poly(r["rect"])
            dr.polygon([P(p) for p in poly], outline=(255, 0, 255))
        im.save(Path(out_dir) / f"check_{fl['name']}.png")


def main():
    data = json.loads((HERE / "coords.json").read_text(encoding="utf-8"))
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check(data, sys.argv[2:4], sys.argv[4])
        return
    scene = build(data)
    text = json.dumps(scene, ensure_ascii=False, separators=(",", ":"))
    (HERE / "scene.json").write_text(text, encoding="utf-8")
    html = (HERE / "viewer_template.html").read_text(encoding="utf-8")
    (HERE / "index.html").write_text(html.replace("/*SCENE_JSON*/null", text), encoding="utf-8")
    print(f"boxes={len(scene['boxes'])} slabs={len(scene['slabs'])} labels={len(scene['labels'])}")


if __name__ == "__main__":
    main()
