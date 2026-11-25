"""シーンJSONをBlenderに読み込み、CGパースをレンダリングする。

使い方:
    blender -b -P render_perspective.py -- scene_hiraya.json 出力先 [サンプル数]
    (Blenderのpythonモジュール bpy が入っていれば python3 render_perspective.py ... でも動く)

出力: 出力先に perspective_*.png と、他のソフトで使える model.glb
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

# ビューアと同じ色。値は (色, 粗さ) と、ガラスなど一部だけ追加の指定
MATS = {
    "wall": (0xf3f1ec, 0.85), "frame": (0x3a3f44, 0.5), "glass": (0xdcecf2, 0.02),
    "wood": (0xc9a272, 0.55), "tatami": (0xb3c98a, 0.9), "aqua": (0xe3eeee, 0.4), "cyan": (0x5fc9d4, 0.3),
    "brown": (0x7a4a12, 0.7), "lawn": (0x4f8f3e, 1.0), "concrete": (0xbdbab0, 0.9), "tile": (0xcfcabb, 0.6),
    "darktile": (0x4a4d50, 0.6), "gray": (0xc9c6c0, 0.5), "charcoal": (0x45474a, 0.6), "beige": (0xd6d1bd, 0.8),
    "roof": (0x50585f, 0.7), "ground": (0x8fa37a, 1.0), "stair": (0xf3efe6, 0.6), "stringer": (0xd9d4c7, 0.7),
    "sofa": (0xd9c6bb, 0.95), "table": (0xb98a55, 0.45), "round": (0xa8743f, 0.45), "chair": (0xe3d9bd, 0.85),
    "counter": (0xf2efe8, 0.3), "cabinet": (0xf4f2ee, 0.4), "appliance": (0xffffff, 0.15), "wood2": (0x7a5130, 0.6),
    "bike": (0xb8322b, 0.35), "bed": (0xf3efe6, 0.95), "car": (0x5f6c78, 0.2), "deck": (0x9a7149, 0.75),
    "trunk": (0x5e412a, 0.9), "leaf": (0x46803c, 0.9),
}


def hex_rgb(h):
    # sRGB → リニア
    return tuple(((h >> s & 255) / 255) ** 2.2 for s in (16, 8, 0)) + (1.0,)


def make_material(key):
    color, rough = MATS.get(key, (0xff00ff, 0.8))
    m = bpy.data.materials.new(key)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = hex_rgb(color)
    p.inputs["Roughness"].default_value = rough
    if key == "glass":
        p.inputs["Transmission Weight"].default_value = 1.0
        p.inputs["IOR"].default_value = 1.45
    if key in ("car", "appliance", "counter"):
        p.inputs["Coat Weight"].default_value = 0.3
    if key in ("lawn", "leaf", "ground"):
        # 芝や葉のむら
        nt = m.node_tree
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = 40.0
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.color_ramp.elements[0].color = tuple(c * 0.7 for c in hex_rgb(color)[:3]) + (1,)
        ramp.color_ramp.elements[1].color = hex_rgb(color)
        nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], p.inputs["Base Color"])
    return m


def to_blender(p):
    """ビューアの座標 (x, 上, 南) → Blender (x, 北, 上)"""
    x, y, z = p
    return Vector((x, -z, y))


def build(scene):
    mats = {}
    get = lambda k: mats.setdefault(k, make_material(k))
    coll = bpy.data.collections.new("house")
    bpy.context.scene.collection.children.link(coll)

    for b in scene["boxes"]:
        mesh = bpy.data.meshes.new("box")
        sx, sy, sz = b["s"]
        hx, hy, hz = sx / 2, sz / 2, sy / 2
        verts = [(x, y, z) for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        mesh.from_pydata(verts, [], faces)
        key = "wood2" if b["m"] == "wood" and b.get("n") else b["m"]
        mesh.materials.append(get(key))
        ob = bpy.data.objects.new(b.get("n", b["m"]), mesh)
        ob.location = to_blender(b["p"])
        coll.objects.link(ob)

    for s in scene["slabs"]:
        # 外形と穴を2Dカーブにして押し出す (穴は自動でくり抜かれる)
        cu = bpy.data.curves.new("slab", "CURVE")
        cu.dimensions = "2D"
        cu.fill_mode = "BOTH"
        cu.extrude = s["t"] / 2
        for loop in [s["poly"]] + s["holes"]:
            sp = cu.splines.new("POLY")
            sp.points.add(len(loop) - 1)
            for pt, (x, z) in zip(sp.points, loop):
                pt.co = (x, -z, 0, 1)
            sp.use_cyclic_u = True
        cu.materials.append(get(s["m"]))
        ob = bpy.data.objects.new(s["m"], cu)
        ob.location = (0, 0, s["y"] - s["t"] / 2)
        coll.objects.link(ob)
    # 敷地の外まで続く地面
    bpy.ops.mesh.primitive_plane_add(size=400, location=(0, 0, -0.11))
    bpy.context.object.data.materials.append(get("ground"))
    return coll


def setup_world(strength=1.0):
    world = bpy.data.worlds.new("sky")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    try:
        sky = nt.nodes.new("ShaderNodeTexSky")
        sky.sun_elevation = math.radians(38)
        sky.sun_rotation = math.radians(150)
        nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.18 * strength
    except Exception:
        bg.inputs["Color"].default_value = (0.55, 0.7, 0.9, 1)
        bg.inputs["Strength"].default_value = strength
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = 2.6
    sun.angle = math.radians(1.5)
    ob = bpy.data.objects.new("sun", sun)
    # 南西寄りの午後の日差し (Blenderの -y が南)
    ob.rotation_euler = (math.radians(52), 0, math.radians(-35))
    bpy.context.scene.collection.objects.link(ob)


def camera(name, eye, target, lens):
    cam = bpy.data.cameras.new(name)
    cam.lens = lens
    ob = bpy.data.objects.new(name, cam)
    ob.location = Vector(eye)
    ob.rotation_euler = (Vector(target) - Vector(eye)).to_track_quat("-Z", "Y").to_euler()
    # 建築パースらしく縦の線をまっすぐにする (2点透視)
    if abs(ob.rotation_euler.x - math.pi / 2) < math.radians(12):
        dz = target[2] - eye[2]
        cam.shift_y = dz / max(1e-6, (Vector(target) - Vector(eye)).length) * 0.9
        ob.rotation_euler.x = math.pi / 2
    bpy.context.scene.collection.objects.link(ob)
    return ob


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    src, out = Path(args[0]), Path(args[1])
    samples = int(args[2]) if len(args) > 2 else 96
    out.mkdir(parents=True, exist_ok=True)
    scene = json.loads(src.read_text(encoding="utf-8"))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    build(scene)
    setup_world()

    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = 1600, 1000
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"

    bpy.ops.export_scene.gltf(filepath=str(out / "model.glb"), export_format="GLB")

    W, D = scene["size"]
    shots = scene.get("meta", {}).get("cameras") or [
        {"name": "exterior", "eye": [W * 0.75, -D * 1.55, 1.6], "target": [0, 0, 1.4], "lens": 26},
        {"name": "aerial", "eye": [W * 1.05, -D * 1.35, D * 0.95], "target": [0, -1, 0], "lens": 30},
    ]
    for shot in shots:
        sc.camera = camera(shot["name"], shot["eye"], shot["target"], shot["lens"])
        sc.view_settings.exposure = shot.get("exposure", 0.0)
        sc.render.filepath = str(out / f"perspective_{shot['name']}.png")
        bpy.ops.render.render(write_still=True)
        print("rendered", sc.render.filepath, flush=True)


if __name__ == "__main__":
    main()
