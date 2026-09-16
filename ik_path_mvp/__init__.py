bl_info = {
    "name": "IK Path MVP",
    "author": "YourName",
    "version": (0, 5, 4),
    "blender": (4, 0, 0),
    "location": "3D Viewport > Sidebar > IK Path MVP",
    "description": "Draw path + live preview. Inverted body lean direction, horizontal level root, timeline preview stability",
    "category": "Animation",
}


import bpy
from bpy_extras import view3d_utils
from mathutils import Vector, Matrix, Quaternion, Euler
from math import radians, degrees


# ============================================================
# Live Settings Update & Preview State
# ============================================================

_is_updating = False
_preview_rest_state = None


def clear_preview_rest_state():
    global _preview_rest_state
    _preview_rest_state = None


def _on_setting_updated(self, context):
    global _is_updating
    if _is_updating:
        return
    if not hasattr(context, "scene") or not context.scene:
        return
    s = getattr(context.scene, "ik_path_mvp", None)
    if s and getattr(s, "is_preview", False) and s.path_name:
        path_obj = bpy.data.objects.get(s.path_name)
        if path_obj:
            _is_updating = True
            try:
                run_bake(context, is_preview=True)
            finally:
                _is_updating = False


# ============================================================
# Settings
# ============================================================

class IKPathMVPSettings(bpy.types.PropertyGroup):
    is_preview: bpy.props.BoolProperty(
        name="Preview Active",
        description="True while user is interactively tweaking sliders before final bake",
        default=False,
    )

    path_name: bpy.props.StringProperty(
        name="Path Object",
        default="",
    )

    effector_object: bpy.props.StringProperty(
        name="Controller Object",
        default="",
    )

    effector_bone: bpy.props.StringProperty(
        name="Controller Bone",
        default="",
    )

    auto_capture: bpy.props.BoolProperty(
        name="Auto Capture Controller",
        description="Remember active pose bone while you are in Pose Mode",
        default=True,
    )

    chain_override: bpy.props.StringProperty(
        name="Chain Override",
        description="Comma separated bone names from root to effector. Empty = auto chain by parents",
        default="",
    )

    # --- фильтры для Rigify и подобных ригов ---
    filter_rigify: bpy.props.BoolProperty(
        name="Filter Rigify/MCH/DEF",
        description="Skip bones with prefixes MCH-, DEF-, ORG-, WGT- (Rigify internals)",
        default=True,
    )

    filter_constrained: bpy.props.BoolProperty(
        name="Filter Constrained",
        description="Skip bones that have Copy Transforms/Rotation/Scale/Location/Armature constraints",
        default=True,
    )

    draw_anchor: bpy.props.EnumProperty(
        name="Draw Depth",
        items=(
            ('CONTROLLER', "Controller", "Draw plane passes through the controller"),
            ('CURSOR', "3D Cursor", "Draw plane passes through the 3D cursor"),
        ),
        default='CONTROLLER',
    )

    path_mode: bpy.props.EnumProperty(
        name="Path Mode",
        items=(
            ('ABSOLUTE', "Absolute", "Controller moves exactly onto path points"),
            ('RELATIVE', "Relative", "Path shape is applied as offset from controller start"),
        ),
        default='ABSOLUTE',
        update=_on_setting_updated,
    )

    solve_mode: bpy.props.EnumProperty(
        name="Solve Mode",
        description="How the controller reaches the path",
        items=(
            ('DIRECT', "Direct", "Move controller bone directly onto path"),
            ('BODY_DRAG', "Body Drag", "Bend FK controls toward the path, leftover goes to root. No world matrix writes to bones"),
        ),
        default='DIRECT',
        update=_on_setting_updated,
    )

    bend_bones: bpy.props.StringProperty(
        name="Bend Bones",
        description="Comma separated FK controls to bend, root to head order. Empty = auto: spine_fk*, neck (head/effector excluded)",
        default="",
    )

    bend_per_step_deg: bpy.props.FloatProperty(
        name="Bend Per Step (deg)",
        description="Max rotation applied per bone per solver iteration",
        default=2.0,
        min=0.05,
        max=15.0,
    )

    bend_max_per_bone_deg: bpy.props.FloatProperty(
        name="Max Bend Per Bone (deg)",
        description="Max rotation each bend bone may apply FROM its rest pose per keyframe. "
                    "90° allows full crane-neck extension. 30° gives subtle tilts only",
        default=90.0,
        min=1.0,
        max=170.0,
    )

    root_bone: bpy.props.StringProperty(
        name="Root Bone",
        description="Bone that carries the leftover translation (whole body, no stretching). Empty = auto detect",
        default="",
        update=_on_setting_updated,
    )

    body_bone: bpy.props.StringProperty(
        name="Body Bone",
        description="Bone that receives body lean/tilt (e.g. torso, hips). Empty = auto detect",
        default="",
        update=_on_setting_updated,
    )

    root_max_translate: bpy.props.FloatProperty(
        name="Root Max Translate",
        description="Max distance the root bone may move. 0 = unlimited",
        default=0.0,
        min=0.0,
        max=1000.0,
        soft_max=100.0,
        update=_on_setting_updated,
    )

    body_follow: bpy.props.FloatProperty(
        name="Body Follow",
        description="Fraction of the excess displacement applied to the root bone",
        default=1.0,
        min=0.0,
        max=1.0,
        update=_on_setting_updated,
    )

    body_rotate: bpy.props.FloatProperty(
        name="Body Lean",
        description="Amount of body/root tilt towards pull direction (0 = none, positive = lean forward into pull, negative = lean back)",
        default=0.0,
        min=-1.0,
        max=1.0,
        update=_on_setting_updated,
    )

    leg_dangle: bpy.props.BoolProperty(
        name="Leg Drag & Dangle",
        description="Legs stay on floor until body lifts higher than leg length, then dangle and trail behind body",
        default=True,
        update=_on_setting_updated,
    )

    leg_dangle_amount: bpy.props.FloatProperty(
        name="Toe Point",
        description="How much feet point down along the leg when dangling in air",
        default=0.75,
        min=0.0,
        max=1.0,
        update=_on_setting_updated,
    )

    leg_stretch_limit: bpy.props.FloatProperty(
        name="Leg Ground Reach",
        description="Multiplier for how far the leg can reach diagonally while staying on the ground before lifting (1.0 = rest length, 1.25 = stretch out before lifting)",
        default=1.25,
        min=1.0,
        max=2.0,
        update=_on_setting_updated,
    )

    pinned_bones: bpy.props.StringProperty(
        name="Pinned Bones",
        description="Comma-separated list of bones locked in world space during bake",
        default="",
    )

    max_reach: bpy.props.FloatProperty(
        name="Max Reach",
        description="Max unstretched reach of neck/limb. 0 = auto-detect from deform bone lengths",
        default=0.0,
        min=0.0,
        max=100.0,
        soft_max=10.0,
        update=_on_setting_updated,
    )

    limit_stretch: bpy.props.BoolProperty(
        name="Limit Stretch",
        description="Prevent neck/limb from stretching beyond maximum natural length",
        default=True,
        update=_on_setting_updated,
    )

    chain_max_length: bpy.props.IntProperty(
        name="Max Chain Bones",
        description="0 = whole chain up to top parent, otherwise limit bone count (before filter)",
        default=0,
        min=0,
        max=256,
    )

    ik_iterations: bpy.props.IntProperty(
        name="Iterations",
        description="Max bend solver iterations per key. Stops at 1 mm accuracy",
        default=15,
        min=1,
        max=100,
    )

    key_count: bpy.props.IntProperty(
        name="Key Count",
        default=3,
        min=2,
        max=500,
        update=_on_setting_updated,
    )

    smooth_path: bpy.props.BoolProperty(
        name="Smooth Path",
        default=True,
    )

    delete_path_after_bake: bpy.props.BoolProperty(
        name="Delete Path After Bake",
        default=True,
    )

    frame_start: bpy.props.IntProperty(
        name="Start Frame",
        default=1,
        min=0,
    )

    frame_end: bpy.props.IntProperty(
        name="End Frame",
        default=120,
        min=1,
    )

    preserve_offsets: bpy.props.BoolProperty(
        name="Preserve Offsets",
        default=True,
    )

    set_frame_range: bpy.props.BoolProperty(
        name="Set Frame Range",
        default=True,
    )


# ============================================================
# Controller auto capture timer
# ============================================================

_TIMER_REGISTERED = False


def _controller_poll():
    try:
        scene = bpy.context.scene

        if scene and hasattr(scene, "ik_path_mvp"):
            s = scene.ik_path_mvp

            if s.auto_capture:
                obj = bpy.context.active_object

                if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
                    pb = bpy.context.active_pose_bone

                    if pb:
                        if s.effector_object != obj.name or s.effector_bone != pb.name:
                            s.effector_object = obj.name
                            s.effector_bone = pb.name
    except Exception:
        pass

    return 0.25


# ============================================================
# Version-safe helpers
# ============================================================

def _pose_bone_selected(pb):
    v = getattr(pb, "select", None)
    if isinstance(v, bool):
        return v

    try:
        spb = bpy.context.selected_pose_bones
        if spb and pb in spb:
            return True
    except Exception:
        pass

    bone = getattr(pb, "bone", None)
    if bone is not None:
        v = getattr(bone, "select", None)
        if isinstance(v, bool):
            return v

    return False


def _iter_action_fcurves(action):
    old = getattr(action, "fcurves", None)

    if old is not None:
        for fc in old:
            yield fc
        return

    layers = getattr(action, "layers", None)

    if not layers:
        return

    slots = getattr(action, "slots", None)

    for layer in layers:
        strips = getattr(layer, "strips", None)

        if not strips:
            continue

        for strip in strips:
            cbs = getattr(strip, "channelbags", None)

            if cbs:
                for cb in cbs:
                    for fc in cb.fcurves:
                        yield fc
                continue

            if slots:
                for slot in slots:
                    cb = None

                    try:
                        cb = strip.channelbag(slot)
                    except Exception:
                        cb = None

                    if cb:
                        for fc in cb.fcurves:
                            yield fc


# ============================================================
# Chain helpers
# ============================================================

RIGIFY_PREFIXES = ("MCH-", "DEF-", "ORG-", "WGT-")

COPY_CONSTRAINTS = {
    'COPY_TRANSFORMS',
    'COPY_ROTATION',
    'COPY_SCALE',
    'COPY_LOCATION',
    'ARMATURE',
    'CHILD_OF',
}


def _should_exclude(pb, s):
    name = pb.name

    if s.filter_rigify:
        for pfx in RIGIFY_PREFIXES:
            if name.startswith(pfx):
                return True

    if s.filter_constrained:
        for c in pb.constraints:
            if c.type in COPY_CONSTRAINTS:
                return True

    return False


def build_chain(effector_pb, max_len):
    chain = [effector_pb]

    cur = effector_pb.parent

    while cur is not None:
        chain.append(cur)

        if max_len > 0 and len(chain) >= max_len:
            break

        cur = cur.parent

    chain.reverse()

    return chain


def get_chain_for(arm, effector_pb, s):
    """
    Возвращает цепочку с учётом override и фильтров.
    Если после фильтра цепочка пустая — fallback к полной (без фильтра).
    """
    raw = s.chain_override.strip()

    if raw:
        names = [x.strip() for x in raw.split(",") if x.strip()]

        chain = []

        for nm in names:
            b = arm.pose.bones.get(nm)

            if b is not None:
                chain.append(b)

        if chain:
            return chain, []

    raw_chain = build_chain(effector_pb, s.chain_max_length)

    filtered = [b for b in raw_chain if not _should_exclude(b, s)]
    excluded = [b.name for b in raw_chain if _should_exclude(b, s)]

    if not filtered:
        # если фильтр выкинул всё — fallback на сырую цепочку
        return raw_chain, excluded

    # эффектор должен оставаться в цепи всегда, даже если фильтр его выкинул
    if effector_pb not in filtered:
        filtered.append(effector_pb)

    return filtered, excluded


def _world_head(arm, pb):
    return (arm.matrix_world @ pb.matrix).translation.copy()


def _find_root_bone(arm, chain, s):
    """Автопоиск кости-носителя тела: по имени, затем верхняя не-MCH кость предков."""
    candidates = ("root", "ROOT", "Root", "master", "root_fk", "torso")

    for nm in candidates:
        pb = arm.pose.bones.get(nm)

        if pb is not None and not _should_exclude(pb, s):
            return pb

    cur = chain[0] if chain else None
    best = None

    while cur is not None:
        if not _should_exclude(cur, s):
            best = cur

        cur = cur.parent

    return best


def _find_body_bone(arm, root_pb, s=None):
    """
    Finds the main body / torso / hips control bone to receive body lean/pitch rotation,
    leaving the root bone strictly level and horizontal on the floor.
    """
    if s and hasattr(s, "body_bone") and s.body_bone.strip():
        b = arm.pose.bones.get(s.body_bone.strip())
        if b:
            return b

    candidates = ('torso', 'Torso', 'hips', 'Hips', 'spine_master.002', 'spine_master', 'chest', 'spine', 'pelvis')
    for nm in candidates:
        b = arm.pose.bones.get(nm)
        if b is not None and b is not root_pb:
            return b

    for pb in arm.pose.bones:
        nm = pb.name.lower()
        if any(k in nm for k in ['torso', 'hips', 'chest', 'spine']) and not any(p in nm for p in ['mch-', 'def-', 'org-', 'wgt-', 'vis_']):
            if pb is not root_pb:
                return pb

    return None


# ============================================================
# Grease Pencil helpers
# ============================================================

def _iter_gp_layers(layers):
    for layer in layers:
        yield layer
        children = getattr(layer, "children", None)
        if children:
            yield from _iter_gp_layers(children)


def get_last_stroke_world(gp_obj):
    data = gp_obj.data

    if data is None:
        return []

    layers = getattr(data, "layers", None)

    if not layers or len(layers) == 0:
        return []

    first = layers[0]

    if hasattr(first, "active_frame"):
        layer = getattr(layers, "active", None) or first

        frame = layer.active_frame

        if not frame and len(layer.frames) > 0:
            frame = layer.frames[0]

        if not frame or not hasattr(frame, "strokes") or len(frame.strokes) == 0:
            return []

        stroke = frame.strokes[len(frame.strokes) - 1]

        return [gp_obj.matrix_world @ p.co for p in stroke.points]

    best_key = None
    best_stroke = None

    for layer in _iter_gp_layers(layers):
        frames = getattr(layer, "frames", None)

        if not frames:
            continue

        for fr in frames:
            drawing = getattr(fr, "drawing", None)

            if not drawing:
                continue

            strokes = getattr(drawing, "strokes", None)

            if not strokes or len(strokes) == 0:
                continue

            key = getattr(fr, "frame_number", 0)

            if best_key is None or key >= best_key:
                best_key = key
                best_stroke = strokes[len(strokes) - 1]

    if best_stroke is None:
        return []

    points = []

    for p in best_stroke.points:
        co = getattr(p, "position", None)

        if co is None:
            co = getattr(p, "co", None)

        if co is None:
            continue

        points.append(gp_obj.matrix_world @ Vector(co))

    return points


# ============================================================
# Curve / path helpers
# ============================================================

def create_curve_from_points(context, name, points):
    curve_data = bpy.data.curves.new(name, type='CURVE')
    curve_data.dimensions = '3D'

    spline = curve_data.splines.new('POLY')

    need = len(points)
    have = len(spline.points)

    if need > have:
        spline.points.add(need - have)

    for i, co in enumerate(points):
        spline.points[i].co = (co.x, co.y, co.z, 1.0)

    curve_obj = bpy.data.objects.new(name, curve_data)
    context.collection.objects.link(curve_obj)

    return curve_obj


def get_curve_world_points(context, path_obj):
    if not path_obj or path_obj.type != 'CURVE':
        return []

    depsgraph = context.evaluated_depsgraph_get()

    try:
        mesh = path_obj.to_mesh(depsgraph=depsgraph)
    except RuntimeError:
        mesh = None

    if mesh is None:
        return []

    points = [path_obj.matrix_world @ v.co for v in mesh.vertices]

    path_obj.to_mesh_clear()

    if len(points) < 2:
        return points

    cleaned = [points[0]]

    for p in points[1:]:
        if (p - cleaned[-1]).length > 0.00001:
            cleaned.append(p)

    return cleaned


def smooth_polyline(points, iterations=2):
    pts = list(points)

    for _ in range(iterations):
        if len(pts) < 3:
            break

        new = [pts[0]]

        for i in range(1, len(pts) - 1):
            new.append((pts[i - 1] + pts[i] * 2.0 + pts[i + 1]) * 0.25)

        new.append(pts[-1])
        pts = new

    return pts


def get_lengths(points):
    lengths = [0.0]
    total = 0.0

    for i in range(len(points) - 1):
        total += (points[i + 1] - points[i]).length
        lengths.append(total)

    return lengths, total


def eval_polyline(points, lengths, total, u):
    if total == 0.0:
        return points[0]

    target = max(0.0, min(1.0, u)) * total

    for i in range(len(points) - 1):
        if lengths[i + 1] >= target:
            seg_len = lengths[i + 1] - lengths[i]

            if seg_len <= 0.0:
                return points[i]

            t = (target - lengths[i]) / seg_len
            return points[i].lerp(points[i + 1], t)

    return points[-1]


# ============================================================
# Keyframe helpers
# ============================================================

def keyframe_object(obj, frame):
    obj.keyframe_insert("location", frame=frame)

    if obj.rotation_mode == 'QUATERNION':
        obj.keyframe_insert("rotation_quaternion", frame=frame)
    elif obj.rotation_mode == 'AXIS_ANGLE':
        obj.keyframe_insert("rotation_axis_angle", frame=frame)
    else:
        obj.keyframe_insert("rotation_euler", frame=frame)

    obj.keyframe_insert("scale", frame=frame)


def keyframe_pose_bone(pb, frame):
    pb.keyframe_insert("location", frame=frame)

    if pb.rotation_mode == 'QUATERNION':
        pb.keyframe_insert("rotation_quaternion", frame=frame)
    elif pb.rotation_mode == 'AXIS_ANGLE':
        pb.keyframe_insert("rotation_axis_angle", frame=frame)
    else:
        pb.keyframe_insert("rotation_euler", frame=frame)

    pb.keyframe_insert("scale", frame=frame)


def keyframe_pose_bone_rotation(pb, frame):
    if pb.rotation_mode == 'QUATERNION':
        pb.keyframe_insert("rotation_quaternion", frame=frame)
    elif pb.rotation_mode == 'AXIS_ANGLE':
        pb.keyframe_insert("rotation_axis_angle", frame=frame)
    else:
        pb.keyframe_insert("rotation_euler", frame=frame)


def keyframe_pose_bone_location(pb, frame):
    pb.keyframe_insert("location", frame=frame)


def _reset_rotation(pb, base):
    if pb.rotation_mode == 'QUATERNION':
        pb.rotation_quaternion = base
    elif pb.rotation_mode == 'AXIS_ANGLE':
        pb.rotation_axis_angle = base
    else:
        pb.rotation_euler = base


def _quat_mul(a, b):
    """
    Hamilton product of two quaternions.
    Blender 5.x: quat * quat is COMPONENTWISE, so compose via matrices.
    """
    return (a.to_matrix() @ b.to_matrix()).to_quaternion()


def _apply_local_rotation(pb, delta_q):
    """Rotate pb around a bone-local axis: post-compose with current rotation."""
    rot_mode = pb.rotation_mode

    if rot_mode == 'QUATERNION':
        pb.rotation_quaternion = _quat_mul(pb.rotation_quaternion, delta_q).normalized()
        return

    if rot_mode == 'AXIS_ANGLE':
        aa = pb.rotation_axis_angle
        q = Quaternion((aa[1], aa[2], aa[3]), aa[0])
        q = _quat_mul(q, delta_q).normalized()

        if q.angle < 1e-9:
            pb.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        else:
            pb.rotation_axis_angle = (q.angle, q.axis.x, q.axis.y, q.axis.z)

        return

    m = Euler(pb.rotation_euler, rot_mode).to_matrix() @ delta_q.to_matrix()
    pb.rotation_euler = m.to_euler(rot_mode)


def _rotate_bone_toward(arm, b, eff_head, target, step_angle, budget):
    """
    One small local rotation of FK control b that pulls the effector head
    toward target. Picks the local axis with the best gain, never exceeds
    step_angle or the bone's angular budget.
    """
    mw = arm.matrix_world @ b.matrix

    r = eff_head - mw.translation
    d = target - eff_head
    dl = d.length

    if dl < 1e-9 or r.length < 1e-9:
        return

    dirv = d / dl

    best_axis = None
    best_gain = 0.0

    for axis in (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))):
        v = (mw.to_3x3() @ axis).cross(r)
        g = v.dot(dirv)

        if abs(g) > abs(best_gain):
            best_gain = g
            best_axis = axis

    if best_axis is None or abs(best_gain) < 1e-9:
        return

    if best_gain > 0.0:
        hit = dl / best_gain
        ang = step_angle if step_angle < hit else hit
    else:
        hit = dl / -best_gain
        ang = -(step_angle if step_angle < hit else hit)

    left = budget.get(b.name, 0.0)

    if left <= 1e-9:
        return

    if abs(ang) > left:
        ang = left if ang > 0.0 else -left

    budget[b.name] = left - abs(ang)

    _apply_local_rotation(b, Quaternion(best_axis, ang))


def _predict_bend_reduction(arm, b, eff_head, target, step_angle, budget):
    """Predicted |head error| reduction for one clamped rotation of bone b."""
    if budget.get(b.name, 0.0) <= 1e-9:
        return 0.0

    mw = arm.matrix_world @ b.matrix

    r = eff_head - mw.translation
    d = target - eff_head
    dl = d.length

    if dl < 1e-9 or r.length < 1e-9:
        return 0.0

    dirv = d / dl

    best_g = 0.0

    for axis in (Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))):
        g = ((mw.to_3x3() @ axis).cross(r)).dot(dirv)

        if abs(g) > abs(best_g):
            best_g = g

    if abs(best_g) < 1e-9:
        return 0.0

    hit = dl / abs(best_g)
    ang = step_angle if step_angle < hit else hit

    left = budget.get(b.name, 0.0)

    if ang > left:
        ang = left

    return abs(best_g) * ang


def _align_bone_to_target(arm, b, eff_head, target, max_angle_rad):
    """
    One-shot alignment: rotate FK control b in its LOCAL space so that
    the arm (b world-origin → eff_head) points toward target.

    Uses rotation_difference so the bone can crane through 90°+ in one
    step — unlike _rotate_bone_toward which is limited to tiny linear
    increments.  Clamped to max_angle_rad.  Returns actual angle applied.

    Safe for Rigify rigs: only writes local rotation of the FK control,
    never touches world matrices of constrained bones.
    """
    mw = arm.matrix_world @ b.matrix
    P_b = mw.to_translation()

    from_w = eff_head - P_b
    to_w = target - P_b

    if from_w.length < 1e-9 or to_w.length < 1e-9:
        return 0.0

    try:
        rot_inv = mw.to_3x3().inverted()
    except Exception:
        return 0.0

    # Both vectors in bone-local axes so the quaternion is already local.
    from_l = (rot_inv @ from_w).normalized()
    to_l = (rot_inv @ to_w).normalized()

    dot = max(-1.0, min(1.0, from_l.dot(to_l)))
    if dot >= (1.0 - 1e-9):
        return 0.0  # already aligned

    q = from_l.rotation_difference(to_l)

    actual = q.angle
    if actual > max_angle_rad:
        q = Quaternion(q.axis, max_angle_rad)

    _apply_local_rotation(b, q)
    return min(actual, max_angle_rad)


def _world_delta_to_bone_location(arm, pb, world_delta):
    """Convert a translation delta from World space to pb.location space (parent space)."""
    arm_rot_inv = arm.matrix_world.to_3x3().inverted()
    arm_delta = arm_rot_inv @ world_delta

    if pb.parent:
        parent_mat = pb.parent.matrix.to_3x3()
        return parent_mat.inverted() @ arm_delta
    else:
        return arm_delta


def set_smooth_keys_obj(obj):
    try:
        if not obj.animation_data:
            return

        action = obj.animation_data.action

        if not action:
            return

        for fc in _iter_action_fcurves(action):
            for kp in fc.keyframe_points:
                kp.interpolation = 'BEZIER'
                kp.handle_type = 'AUTO_CLAMPED'

            fc.update()
    except Exception:
        pass


def _world_delta_to_bone_location(arm, pb, world_delta):
    """
    Converts a world-space translation delta into the delta for pb.location.

    pb.location lives in parent space (relative to parent bone's tail in
    parent-bone's local axes), so we must undo:
      world = arm_world @ parent_world @ local_rot @ location
    and extract only the location component change.

    For a root bone (no parent), location is in armature-object space.
    """
    parent = pb.parent

    if parent:
        parent_world = arm.matrix_world @ parent.matrix
        # world_delta in parent-bone axes (no scale from arm, already in world)
        return parent_world.inverted().to_3x3() @ world_delta
    else:
        # No parent: location is in armature object space
        return arm.matrix_world.inverted().to_3x3() @ world_delta


def _get_chain_reach_and_base(arm, pb, user_max_reach=0.0):
    """
    Finds the anchor/base bone on the body (e.g. spine/chest) and the maximum
    natural unstretched reach from that base to the controller pb.
    """
    # 1. Look for matching DEF- bone (or use pb directly)
    def_name = 'DEF-' + pb.name
    def_pb = arm.pose.bones.get(def_name) or pb

    bones = []
    cur = def_pb
    base_pb = None

    while cur:
        p = cur.parent
        if p and ('spine' in p.name.lower() or 'torso' in p.name.lower() or 'root' in p.name.lower() or 'chest' in p.name.lower()):
            bones.append(cur)
            base_pb = p
            break
        bones.append(cur)
        cur = p

    if base_pb is None:
        base_pb = arm.pose.bones.get('root') or arm.pose.bones.get('torso')

    if user_max_reach > 1e-4:
        return user_max_reach, base_pb

    # Sum rest lengths of all segment deform bones
    total_reach = 0.0
    for b in bones:
        d_bone = arm.data.bones.get(b.name)
        if d_bone:
            total_reach += d_bone.length

    # Fallback if no deform bones were found
    if total_reach < 1e-4 and base_pb is not None:
        p_base = (arm.matrix_world @ base_pb.matrix).translation
        p_head = (arm.matrix_world @ pb.matrix).translation
        total_reach = (p_head - p_base).length * 1.5

    return total_reach, base_pb


def _capture_rot(pb):
    if pb.rotation_mode == 'QUATERNION':
        return ('QUATERNION', pb.rotation_quaternion.copy())
    elif pb.rotation_mode == 'AXIS_ANGLE':
        return ('AXIS_ANGLE', tuple(pb.rotation_axis_angle))
    else:
        return (pb.rotation_mode, pb.rotation_euler.copy())


def _restore_rot(pb, saved):
    mode, val = saved
    if mode == 'QUATERNION':
        pb.rotation_quaternion = val.copy()
    elif mode == 'AXIS_ANGLE':
        pb.rotation_axis_angle = val
    else:
        pb.rotation_euler = val.copy()


def _apply_world_rotation_to_bone(arm, pb, q_world, base_rot):
    """
    Applies a world-space rotation q_world to pb, starting from base_rot.
    """
    arm_q = arm.matrix_world.to_quaternion()
    arm_q_inv = arm_q.inverted()
    local_q = arm_q_inv @ q_world @ arm_q

    mode, base_val = base_rot
    if mode == 'QUATERNION':
        pb.rotation_quaternion = _quat_mul(local_q, base_val).normalized()
    elif mode == 'AXIS_ANGLE':
        base_q = Quaternion((base_val[1], base_val[2], base_val[3]), base_val[0])
        res_q = _quat_mul(local_q, base_q).normalized()
        pb.rotation_axis_angle = (res_q.angle, res_q.axis.x, res_q.axis.y, res_q.axis.z)
    else:
        m = local_q.to_matrix() @ Euler(base_val, mode).to_matrix()
        pb.rotation_euler = m.to_euler(mode)


_apply_world_rotation_to_root = _apply_world_rotation_to_bone


# ============================================================
# Live Bone Pinning Helpers (COPY_TRANSFORMS constraints)
# ============================================================

def _get_or_create_pin_collection(context):
    col = bpy.data.collections.get('IKPath_Pins')
    if not col:
        col = bpy.data.collections.new('IKPath_Pins')
        context.scene.collection.children.link(col)
    return col


def _pin_bone_live(context, arm, pb):
    col = _get_or_create_pin_collection(context)
    e_name = f"PIN_{arm.name}_{pb.name}"
    empty = bpy.data.objects.get(e_name)
    if not empty:
        empty = bpy.data.objects.new(e_name, None)
        empty.empty_display_type = 'PLAIN_AXES'
        empty.empty_display_size = 0.05
        col.objects.link(empty)

    empty.matrix_world = arm.matrix_world @ pb.matrix

    c = pb.constraints.get('IKPath_Pin')
    if not c:
        c = pb.constraints.new('COPY_TRANSFORMS')
        c.name = 'IKPath_Pin'
    c.target = empty
    return empty


def _unpin_all_live(context, arm=None):
    if arm and arm.type == 'ARMATURE':
        for pb in arm.pose.bones:
            c = pb.constraints.get('IKPath_Pin')
            if c:
                pb.constraints.remove(c)
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            for pb in obj.pose.bones:
                c = pb.constraints.get('IKPath_Pin')
                if c:
                    pb.constraints.remove(c)

    col = bpy.data.collections.get('IKPath_Pins')
    if col:
        for obj in list(col.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        try:
            bpy.data.collections.remove(col)
        except Exception:
            pass


# ============================================================
# Bake core
# ============================================================

def run_bake(context, is_preview=False):
    global _preview_rest_state
    s = context.scene.ik_path_mvp

    if s.frame_end <= s.frame_start:
        return ('CANCELLED', "End frame must be greater than start frame")

    path_obj = bpy.data.objects.get(s.path_name)

    if not path_obj:
        return ('CANCELLED', "Path object not found. Draw or create path first")

    points = get_curve_world_points(context, path_obj)

    if len(points) < 2:
        return ('CANCELLED', "Path has too few points")

    if s.smooth_path:
        points = smooth_polyline(points, 2)

    lengths, total = get_lengths(points)

    if total == 0.0:
        return ('CANCELLED', "Path length is zero")

    if s.set_frame_range:
        context.scene.frame_start = s.frame_start
        context.scene.frame_end = s.frame_end

    frame_count = s.frame_end - s.frame_start

    path_origin = points[0].copy()

    key_count = max(2, s.key_count)

    key_specs = []

    for i in range(key_count):
        t = i / (key_count - 1)
        frame = s.frame_start + int(round(t * frame_count))
        key_specs.append((frame, t))

    cur_playhead_frame = context.scene.frame_current

    # Always evaluate from frame_start so calculations are pristine
    context.scene.frame_set(s.frame_start)
    context.view_layer.update()

    # ------------------------------------------------
    # Режим Pose Mode: контроллер = кость
    # ------------------------------------------------
    arm = bpy.data.objects.get(s.effector_object)

    if arm and arm.type == 'ARMATURE' and s.effector_bone:
        pb = arm.pose.bones.get(s.effector_bone)

        if not pb:
            if is_preview:
                context.scene.frame_set(cur_playhead_frame)
            return ('CANCELLED', f"Bone not found: {s.effector_bone}")

        chain, excluded = get_chain_for(arm, pb, s)

        chain_names = [b.name for b in chain]

        others = []

        if s.preserve_offsets:
            for b in arm.pose.bones:
                if b in chain:
                    continue
                if _pose_bone_selected(b):
                    others.append(b)

        deltas = {b: pb.matrix.inverted() @ b.matrix for b in others}

        inv = arm.matrix_world.inverted()

        # Collect pinned bones (lock in world space)
        pinned_bones_list = []
        if s.pinned_bones.strip():
            for b_name in s.pinned_bones.split(','):
                bn = b_name.strip()
                if bn and bn in arm.pose.bones:
                    pinned_bones_list.append(arm.pose.bones[bn])
        for pb_cand in arm.pose.bones:
            if pb_cand.constraints.get('IKPath_Pin') and pb_cand not in pinned_bones_list:
                pinned_bones_list.append(pb_cand)

        pinned_matrices = {}
        for b in pinned_bones_list:
            c = b.constraints.get('IKPath_Pin')
            if c and c.target:
                pinned_matrices[b] = c.target.matrix_world.copy()
            else:
                pinned_matrices[b] = (arm.matrix_world @ b.matrix).copy()

        # =============================================
        # BODY DRAG  —  Direct head placement + Root Follow with Slack
        # =============================================
        if s.solve_mode == 'BODY_DRAG':
            start_matrix_world = (arm.matrix_world @ pb.matrix).copy()
            start_translation = start_matrix_world.translation.copy()

            # Find root bone for whole-body carrier
            root_pb = None
            if s.root_bone.strip():
                root_pb = arm.pose.bones.get(s.root_bone.strip())
            if root_pb is None:
                root_pb = _find_root_bone(arm, chain, s)

            root_start = root_pb.location.copy() if root_pb else None
            root_base_rot = _capture_rot(root_pb) if root_pb else None
            root_world_start = (arm.matrix_world @ root_pb.matrix).translation.copy() if root_pb else None

            # Find body / torso bone for body lean
            body_pb = _find_body_bone(arm, root_pb, s)
            body_base_rot = _capture_rot(body_pb) if body_pb else None

            # Find natural unstretched reach and body anchor (spine/chest)
            max_reach, base_pb = _get_chain_reach_and_base(arm, pb, s.max_reach)
            base_world_start = (arm.matrix_world @ base_pb.matrix).translation.copy() if base_pb else None

            # Legs auto-drag / dangling setup
            leg_controllers = []
            if s.leg_dangle:
                for b_cand in arm.pose.bones:
                    nm = b_cand.name.lower()
                    if ('foot_ik' in nm or ('foot' in nm and 'ik' in nm)) and ('parent' not in nm and 'target' not in nm and 'tweak' not in nm and 'heel' not in nm and 'roll' not in nm and 'pole' not in nm):
                        if b_cand not in pinned_bones_list and b_cand is not pb and b_cand is not root_pb:
                            side = '.L' if '.l' in nm else ('.R' if '.r' in nm else '')
                            hip_cand = None
                            for hn in [f'thigh_parent{side}', f'DEF-thigh{side}', f'thigh_fk{side}']:
                                if hn in arm.pose.bones:
                                    hip_cand = arm.pose.bones[hn]
                                    break
                            f_start_w = (arm.matrix_world @ b_cand.matrix).translation.copy()
                            h_start_w = (arm.matrix_world @ hip_cand.matrix).translation.copy() if hip_cand else None
                            leg_l = (f_start_w - h_start_w).length if h_start_w else 0.46
                            leg_controllers.append({
                                'bone': b_cand,
                                'hip': hip_cand,
                                'foot_start_w': f_start_w,
                                'hip_start_w': h_start_w,
                                'leg_len': leg_l,
                                'foot_start_m': (arm.matrix_world @ b_cand.matrix).copy(),
                            })

            # Store or recall preview rest state to guarantee pristine transforms during live slider tweaks
            if is_preview and _preview_rest_state is None:
                _preview_rest_state = {
                    'mode': 'BODY_DRAG',
                    'bone_name': pb.name,
                    'start_matrix_world': start_matrix_world.copy(),
                    'start_translation': start_translation.copy(),
                    'root_name': root_pb.name if root_pb else None,
                    'root_start': root_start.copy() if root_start is not None else None,
                    'root_base_rot': root_base_rot,
                    'root_world_start': root_world_start.copy() if root_world_start is not None else None,
                    'body_name': body_pb.name if body_pb else None,
                    'body_base_rot': body_base_rot,
                    'base_world_start': base_world_start.copy() if base_world_start is not None else None,
                    'max_reach': max_reach,
                    'leg_controllers': [
                        {
                            'bone_name': leg['bone'].name,
                            'hip_name': leg['hip'].name if leg['hip'] else None,
                            'foot_start_w': leg['foot_start_w'].copy(),
                            'hip_start_w': leg['hip_start_w'].copy() if leg['hip_start_w'] else None,
                            'leg_len': leg['leg_len'],
                            'foot_start_m': leg['foot_start_m'].copy(),
                        }
                        for leg in leg_controllers
                    ],
                }
            elif _preview_rest_state is not None and _preview_rest_state.get('mode') == 'BODY_DRAG':
                start_matrix_world = _preview_rest_state['start_matrix_world'].copy()
                start_translation = _preview_rest_state['start_translation'].copy()
                if _preview_rest_state['root_name']:
                    root_pb = arm.pose.bones.get(_preview_rest_state['root_name'])
                    root_start = _preview_rest_state['root_start'].copy() if _preview_rest_state['root_start'] is not None else None
                    root_base_rot = _preview_rest_state['root_base_rot']
                    root_world_start = _preview_rest_state['root_world_start'].copy() if _preview_rest_state['root_world_start'] is not None else None
                if _preview_rest_state.get('body_name'):
                    body_pb = arm.pose.bones.get(_preview_rest_state['body_name'])
                    body_base_rot = _preview_rest_state['body_base_rot']
                base_world_start = _preview_rest_state['base_world_start']
                if s.max_reach <= 1e-4:
                    max_reach = _preview_rest_state['max_reach']
                if s.leg_dangle:
                    leg_controllers = []
                    for leg_data in _preview_rest_state['leg_controllers']:
                        b_cand = arm.pose.bones.get(leg_data['bone_name'])
                        hip_cand = arm.pose.bones.get(leg_data['hip_name']) if leg_data['hip_name'] else None
                        if b_cand:
                            leg_controllers.append({
                                'bone': b_cand,
                                'hip': hip_cand,
                                'foot_start_w': leg_data['foot_start_w'].copy(),
                                'hip_start_w': leg_data['hip_start_w'].copy() if leg_data['hip_start_w'] else None,
                                'leg_len': leg_data['leg_len'],
                                'foot_start_m': leg_data['foot_start_m'].copy(),
                            })

            arm_scale = arm.matrix_world.to_scale()
            smin = min((abs(x) for x in arm_scale if abs(x) > 1e-9), default=1.0)

            n_keys = len(key_specs)

            for i, (frame, u) in enumerate(key_specs):
                if i == 0:
                    pos = start_translation.copy()
                    body_move_world = Vector((0.0, 0.0, 0.0))
                    head_pos = start_translation.copy()
                else:
                    p = eval_polyline(points, lengths, total, u)

                    if s.path_mode == 'RELATIVE':
                        pos = start_translation + (p - path_origin)
                    else:
                        pos = p.copy()

                    # --- 1. Calculate Slack & Pull Vector ---
                    body_move_world = Vector((0.0, 0.0, 0.0))

                    if base_world_start is not None and max_reach > 1e-4:
                        v = pos - base_world_start
                        cur_dist = v.length

                        if cur_dist > max_reach:
                            # Head has extended beyond natural unstretched neck length!
                            excess = (cur_dist - max_reach) * s.body_follow
                            pull_dir = v.normalized()
                            body_move_world = pull_dir * excess

                            if s.root_max_translate > 0.0:
                                cap = s.root_max_translate / smin
                                if body_move_world.length > cap:
                                    body_move_world = body_move_world * (cap / body_move_world.length)

                    # Determine head position first for tension vector
                    if base_world_start is not None and max_reach > 1e-4 and s.limit_stretch:
                        current_base = base_world_start + body_move_world
                        to_target = pos - current_base
                        if to_target.length > max_reach:
                            head_pos = current_base + to_target.normalized() * max_reach
                        else:
                            head_pos = pos
                    else:
                        head_pos = pos

                # 1. ROOT BONE: Translation ONLY, rotation strictly neutral/horizontal (ZERO tilt, feet and floor stay flat)
                if root_pb is not None:
                    if body_move_world.length > 1e-6:
                        root_pb.location = root_start + _world_delta_to_bone_location(
                            arm, root_pb, body_move_world
                        )
                    else:
                        root_pb.location = root_start.copy()

                    if root_base_rot is not None:
                        _restore_rot(root_pb, root_base_rot)

                    context.view_layer.update()
                    keyframe_pose_bone_location(root_pb, frame)
                    keyframe_pose_bone_rotation(root_pb, frame)

                # 2. BODY/TORSO/HIPS BONE: Lean and tilt towards pull direction
                if body_pb is not None and body_base_rot is not None:
                    if (
                        s.body_rotate != 0.0
                        and body_move_world.length > 1e-4
                        and i > 0
                        and root_world_start is not None
                    ):
                        current_root_world = root_world_start + body_move_world
                        tension_vec = head_pos - current_root_world
                        v_0 = start_translation - root_world_start
                        if tension_vec.length > 1e-4 and v_0.length > 1e-4:
                            q_diff = v_0.normalized().rotation_difference(tension_vec.normalized())
                            if q_diff.angle > 1e-5:
                                factor = min(1.0, abs(s.body_rotate) * 2.0)
                                q_lean = q_diff.inverted() if s.body_rotate > 0 else q_diff
                                q_applied = Quaternion((1.0, 0.0, 0.0, 0.0)).slerp(q_lean, factor)
                                _apply_world_rotation_to_bone(arm, body_pb, q_applied, body_base_rot)
                            else:
                                _restore_rot(body_pb, body_base_rot)
                        else:
                            _restore_rot(body_pb, body_base_rot)
                    else:
                        _restore_rot(body_pb, body_base_rot)

                    context.view_layer.update()
                    keyframe_pose_bone_rotation(body_pb, frame)

                # Head placement
                m = start_matrix_world.copy()
                m.translation = head_pos
                pb.matrix = inv @ m
                context.view_layer.update()
                keyframe_pose_bone(pb, frame)

                # Legs auto-drag / dangling (Two-phase: Grounded diagonal stance -> Trailing lift)
                for leg in leg_controllers:
                    b_cand = leg['bone']
                    hip = leg['hip']
                    f_start_w = leg['foot_start_w']
                    h_start_w = leg['hip_start_w']
                    leg_len = leg['leg_len']
                    f_start_m = leg['foot_start_m']

                    if hip and h_start_w:
                        h_cur_w = (arm.matrix_world @ hip.matrix).translation
                        v_leg = f_start_w - h_cur_w
                        cur_leg_dist = v_leg.length
                        max_ground_dist = leg_len * s.leg_stretch_limit

                        # Phase 1: Grounded (standing or stretching diagonally to reach ground)
                        if (
                            i == 0
                            or body_move_world.length <= 1e-4
                            or (cur_leg_dist <= max_ground_dist and (h_cur_w.z - f_start_w.z) <= leg_len)
                        ):
                            m_f = f_start_m.copy()
                            m_f.translation = f_start_w
                        else:
                            # Phase 2: Lifted & Trailing behind hip along v_leg towards original ground point
                            foot_dir = v_leg.normalized()
                            t_pos = h_cur_w + foot_dir * leg_len
                            # Smoothly transition toe tilt as foot lifts
                            lift = min(1.0, (cur_leg_dist - leg_len) / 0.15) if cur_leg_dist > leg_len else 0.0
                            pitch = radians(-55.0 * s.leg_dangle_amount * lift)
                            rot_down = Quaternion((1.0, 0.0, 0.0), pitch)
                            m_f = f_start_m.copy() @ rot_down.to_matrix().to_4x4()
                            m_f.translation = t_pos

                        b_cand.matrix = inv @ m_f
                        context.view_layer.update()
                        keyframe_pose_bone(b_cand, frame)

                # Apply pinned bones (lock in world space)
                for b_pin, m_w in pinned_matrices.items():
                    b_pin.matrix = inv @ m_w
                context.view_layer.update()
                for b_pin in pinned_matrices.keys():
                    keyframe_pose_bone(b_pin, frame)

                # Preserve offsets of other selected bones
                for b, d in deltas.items():
                    if b not in pinned_matrices:
                        b.matrix = pb.matrix @ d
                context.view_layer.update()

                for b in deltas.keys():
                    if b not in pinned_matrices:
                        keyframe_pose_bone(b, frame)

            set_smooth_keys_obj(arm)
            if is_preview:
                context.scene.frame_set(cur_playhead_frame)
            else:
                context.scene.frame_set(s.frame_start)
            context.view_layer.update()

            msg = f"Body Drag: baked head {pb.name}"
            if root_pb is not None:
                msg += f" + root {root_pb.name} (reach={max_reach:.2f}, follow={s.body_follow:.2f})"
            if body_pb is not None and s.body_rotate != 0.0:
                msg += f" + body lean {body_pb.name}"
            if pinned_matrices:
                msg += f", {len(pinned_matrices)} pinned"
            msg += f", {key_count} keys"

            if not is_preview and s.delete_path_after_bake:
                bpy.data.objects.remove(path_obj, do_unlink=True)
                s.path_name = ""
                s.is_preview = False
                msg += ", path deleted"
            elif is_preview:
                s.is_preview = True
                msg += " [Preview active]"

            return ('FINISHED', msg)
        # DIRECT
        # =============================================
        start_matrix_world = (arm.matrix_world @ pb.matrix).copy()
        start_translation = start_matrix_world.translation.copy()

        if is_preview and _preview_rest_state is None:
            _preview_rest_state = {
                'mode': 'DIRECT',
                'bone_name': pb.name,
                'start_matrix_world': start_matrix_world.copy(),
                'start_translation': start_translation.copy(),
            }
        elif _preview_rest_state is not None and _preview_rest_state.get('mode') == 'DIRECT':
            start_matrix_world = _preview_rest_state['start_matrix_world'].copy()
            start_translation = _preview_rest_state['start_translation'].copy()

        for i, (frame, u) in enumerate(key_specs):
            if i == 0:
                pos = start_translation.copy()
            elif s.path_mode == 'RELATIVE':
                p = eval_polyline(points, lengths, total, u)
                pos = start_translation + (p - path_origin)
            else:
                p = eval_polyline(points, lengths, total, u)
                pos = p

            m = start_matrix_world.copy()
            m.translation = pos

            pb.matrix = inv @ m
            context.view_layer.update()

            for b, d in deltas.items():
                if b not in pinned_matrices:
                    b.matrix = pb.matrix @ d

            for b_pin, m_w in pinned_matrices.items():
                b_pin.matrix = inv @ m_w

            context.view_layer.update()

            keyframe_pose_bone(pb, frame)

            for b in deltas.keys():
                if b not in pinned_matrices:
                    keyframe_pose_bone(b, frame)

            for b_pin in pinned_matrices.keys():
                keyframe_pose_bone(b_pin, frame)

        set_smooth_keys_obj(arm)
        if is_preview:
            context.scene.frame_set(cur_playhead_frame)
        else:
            context.scene.frame_set(s.frame_start)
        context.view_layer.update()

        msg = f"Direct baked bone {pb.name} (+{len(deltas)} bones), {key_count} keys"

        if not is_preview and s.delete_path_after_bake:
            bpy.data.objects.remove(path_obj, do_unlink=True)
            s.path_name = ""
            s.is_preview = False
            msg += ", path deleted"
        elif is_preview:
            s.is_preview = True
            msg += " [Preview active]"

        return ('FINISHED', msg)

    # ------------------------------------------------
    # Режим объектов
    # ------------------------------------------------
    selected = []

    for obj in context.selected_objects:
        if obj == path_obj:
            continue

        if obj.type == 'GREASEPENCIL':
            continue

        selected.append(obj)

    active = context.active_object

    if (
        active and
        active not in selected and
        active != path_obj and
        active.type != 'GREASEPENCIL'
    ):
        selected.append(active)

    if not selected:
        return ('CANCELLED', "No controller: capture a pose bone or select objects")

    if arm and arm in selected:
        root = arm
    elif active in selected:
        root = active
    else:
        root = selected[0]

    root_start_translation = root.matrix_world.translation.copy()

    offsets = {}

    if s.preserve_offsets:
        for obj in selected:
            if obj != root:
                offsets[obj] = root.matrix_world.inverted() @ obj.matrix_world

    for i, (frame, u) in enumerate(key_specs):
        if i == 0:
            pos = root_start_translation.copy()
        elif s.path_mode == 'RELATIVE':
            p = eval_polyline(points, lengths, total, u)
            pos = root_start_translation + (p - path_origin)
        else:
            p = eval_polyline(points, lengths, total, u)
            pos = p

        mat = root.matrix_world.copy()
        mat.translation = pos
        root.matrix_world = mat

        context.view_layer.update()

        keyframe_object(root, frame)

        for obj, delta in offsets.items():
            obj.matrix_world = root.matrix_world @ delta
            context.view_layer.update()
            keyframe_object(obj, frame)

    set_smooth_keys_obj(root)

    for obj in offsets.keys():
        set_smooth_keys_obj(obj)

    if is_preview:
        context.scene.frame_set(cur_playhead_frame)
    else:
        context.scene.frame_set(s.frame_start)

    msg = f"Baked {len(selected)} object(s), {key_count} keys"

    if not is_preview and s.delete_path_after_bake:
        bpy.data.objects.remove(path_obj, do_unlink=True)
        s.path_name = ""
        s.is_preview = False
        msg += ", path deleted"
    elif is_preview:
        s.is_preview = True
        msg += " [Preview active]"

    return ('FINISHED', msg)


# ============================================================
# Operators
# ============================================================

class IKPATHMVP_OT_capture_controller(bpy.types.Operator):
    bl_idname = "ikpathmvp.capture_controller"
    bl_label = "Capture Active Controller"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.ik_path_mvp

        obj = context.active_object

        if not obj:
            self.report({'WARNING'}, "No active object")
            return {'CANCELLED'}

        if obj.type == 'ARMATURE' and obj.mode == 'POSE':
            pb = context.active_pose_bone

            if not pb:
                self.report({'WARNING'}, "No active pose bone")
                return {'CANCELLED'}

            s.effector_object = obj.name
            s.effector_bone = pb.name
            clear_preview_rest_state()

            self.report({'INFO'}, f"Controller: {obj.name} / {pb.name}")
            return {'FINISHED'}

        s.effector_object = obj.name
        s.effector_bone = ""
        clear_preview_rest_state()

        self.report({'INFO'}, f"Controller object: {obj.name}")
        return {'FINISHED'}


class IKPATHMVP_OT_clear_controller(bpy.types.Operator):
    bl_idname = "ikpathmvp.clear_controller"
    bl_label = "Clear Controller"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.ik_path_mvp
        s.effector_object = ""
        s.effector_bone = ""
        clear_preview_rest_state()
        self.report({'INFO'}, "Controller cleared")
        return {'FINISHED'}


class IKPATHMVP_OT_draw_path(bpy.types.Operator):
    bl_idname = "ikpathmvp.draw_path"
    bl_label = "Draw Path and Bake"
    bl_description = "Hold LMB and drag in the viewport to draw a path. Release to create path and bake. ESC to cancel"

    _preview = None
    _preview_count = 0
    _points = []
    _drawing = False
    _anchor = None
    _area = None

    def _create_preview(self, context):
        curve_data = bpy.data.curves.new("IKPathMVP_preview", 'CURVE')
        curve_data.dimensions = '3D'
        curve_data.bevel_depth = 0.004
        curve_data.bevel_resolution = 2

        curve_data.splines.new('POLY')

        obj = bpy.data.objects.new("IKPathMVP_preview", curve_data)
        context.collection.objects.link(obj)

        try:
            obj.select_set(False)
        except Exception:
            pass

        return obj

    def _append_preview_point(self, co):
        if self._preview is None:
            return

        spline = self._preview.data.splines[0]

        if self._preview_count < len(spline.points):
            idx = self._preview_count
        else:
            spline.points.add(1)
            idx = len(spline.points) - 1

        self._preview_count += 1

        spline.points[idx].co = (co.x, co.y, co.z, 1.0)

    def _remove_preview(self):
        if self._preview is not None:
            obj = self._preview
            self._preview = None

            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass

    def _get_anchor(self, context, s):
        if s.draw_anchor == 'CONTROLLER':
            arm = bpy.data.objects.get(s.effector_object)

            if arm and arm.type == 'ARMATURE' and s.effector_bone:
                pb = arm.pose.bones.get(s.effector_bone)

                if pb:
                    return (arm.matrix_world @ pb.matrix).translation.copy()

            obj = bpy.data.objects.get(s.effector_object)

            if obj:
                return obj.matrix_world.translation.copy()

        return context.scene.cursor.location.copy()

    def _add_point(self, context, event):
        area = self._area

        if not area:
            return

        region = None

        for r in area.regions:
            if r.type == 'WINDOW':
                region = r
                break

        if region is None:
            return

        rv3d = area.spaces.active.region_3d

        co = view3d_utils.region_2d_to_location_3d(
            region,
            rv3d,
            Vector((event.mouse_region_x, event.mouse_region_y)),
            self._anchor,
        )

        if self._points and (co - self._points[-1]).length < 0.001:
            return

        self._points.append(co)
        self._append_preview_point(co)

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'WARNING'}, "Use this from the 3D Viewport")
            return {'CANCELLED'}

        s = context.scene.ik_path_mvp

        for obj in list(bpy.data.objects):
            if obj.name.startswith("IKPathMVP_preview"):
                bpy.data.objects.remove(obj, do_unlink=True)

        self._anchor = self._get_anchor(context, s)
        self._points = []
        self._preview_count = 0
        self._drawing = False
        self._area = context.area

        self._preview = self._create_preview(context)

        context.window_manager.modal_handler_add(self)

        self.report({'INFO'}, "Drag with LMB to draw. Release to bake. ESC to cancel")

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._drawing = True
            self._add_point(context, event)

        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._drawing = False
            return self._finish(context)

        elif event.type == 'MOUSEMOVE' and self._drawing:
            self._add_point(context, event)

        elif event.type in {'ESC', 'RIGHTMOUSE'}:
            self._remove_preview()

            if self._area:
                self._area.tag_redraw()

            self.report({'INFO'}, "Draw cancelled")
            return {'CANCELLED'}

        if self._area:
            self._area.tag_redraw()

        return {'RUNNING_MODAL'}

    def _finish(self, context):
        if len(self._points) < 2:
            self._remove_preview()

            if self._area:
                self._area.tag_redraw()

            self.report({'WARNING'}, "Path too short, nothing baked")
            return {'CANCELLED'}

        s = context.scene.ik_path_mvp

        for obj in list(bpy.data.objects):
            if obj.name.startswith("IKPathMVP") and obj != self._preview:
                bpy.data.objects.remove(obj, do_unlink=True)

        self._preview.name = "IKPathMVP"
        self._preview.data.name = "IKPathMVP"
        self._preview.data.bevel_depth = 0.002

        path_obj = self._preview
        self._preview = None

        clear_preview_rest_state()
        s.path_name = path_obj.name
        s.is_preview = True

        status, msg = run_bake(context, is_preview=True)

        self.report({'INFO' if status == 'FINISHED' else 'WARNING'}, f"{msg} (Preview active: adjust sliders or click Apply Bake)")

        if self._area:
            self._area.tag_redraw()

        return {'FINISHED'}


class IKPATHMVP_OT_create_path(bpy.types.Operator):
    bl_idname = "ikpathmvp.create_path"
    bl_label = "Create Path from Last GP Stroke"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.ik_path_mvp

        gp_obj = context.active_object

        if not gp_obj or gp_obj.type != 'GREASEPENCIL':
            gp_obj = None

            for obj in context.selected_objects:
                if obj.type == 'GREASEPENCIL':
                    gp_obj = obj
                    break

        if not gp_obj:
            self.report({'WARNING'}, "No active or selected Grease Pencil object")
            return {'CANCELLED'}

        points = get_last_stroke_world(gp_obj)

        if len(points) < 2:
            self.report({'WARNING'}, "No Grease Pencil stroke with 2+ points found")
            return {'CANCELLED'}

        if s.path_name:
            old_obj = bpy.data.objects.get(s.path_name)

            if old_obj and old_obj.name.startswith("IKPathMVP"):
                bpy.data.objects.remove(old_obj, do_unlink=True)

        path_obj = create_curve_from_points(
            context=context,
            name="IKPathMVP",
            points=points,
        )

        clear_preview_rest_state()
        s.path_name = path_obj.name

        self.report({'INFO'}, f"Created path: {path_obj.name}")

        return {'FINISHED'}


class IKPATHMVP_OT_bake(bpy.types.Operator):
    bl_idname = "ikpathmvp.bake"
    bl_label = "Bake Along Path"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        status, msg = run_bake(context)
        self.report({'INFO' if status == 'FINISHED' else 'WARNING'}, msg)
        return {status}


class IKPATHMVP_OT_clear_path(bpy.types.Operator):
    bl_idname = "ikpathmvp.clear_path"
    bl_label = "Clear Path Setting"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.ik_path_mvp.path_name = ""
        self.report({'INFO'}, "Path setting cleared")
        return {'FINISHED'}


class IKPATHMVP_OT_debug(bpy.types.Operator):
    bl_idname = "ikpathmvp.debug"
    bl_label = "Debug Info"
    bl_options = {'REGISTER'}

    def execute(self, context):
        lines = []

        lines.append(f"Blender version: {bpy.app.version_string}")

        s = context.scene.ik_path_mvp

        lines.append(f"Controller: {s.effector_object} / {s.effector_bone}")
        lines.append(f"Path: {s.path_name}")
        lines.append(
            f"Solve: {s.solve_mode}, path: {s.path_mode}, depth: {s.draw_anchor}, "
            f"keys: {s.key_count}, smooth: {s.smooth_path}"
        )
        lines.append(
            f"Filters: rigify={s.filter_rigify}, constrained={s.filter_constrained}"
        )

        if s.solve_mode == 'BODY_DRAG':
            lines.append(
                f"Bend: per_step={s.bend_per_step_deg}deg, "
                f"max_bone={s.bend_max_per_bone_deg}deg, iters={s.ik_iterations}"
            )
            lines.append(
                f"Root: bone='{s.root_bone or '(auto)'}', "
                f"max_translate={s.root_max_translate:.3f}"
            )
            lines.append(
                f"Body Bone: '{s.body_bone or '(auto)'}', lean={s.body_rotate:.2f}"
            )

        arm = bpy.data.objects.get(s.effector_object)

        if arm and arm.type == 'ARMATURE':
            lines.append(f"Armature: {arm.name}, pose bones: {len(arm.pose.bones)}")

            if s.effector_bone:
                pb = arm.pose.bones.get(s.effector_bone)

                if pb:
                    chain, excluded = get_chain_for(arm, pb, s)

                    lines.append(f"Chain ({len(chain)}):")

                    for b in chain:
                        bone = b.bone
                        cons = [c.type for c in b.constraints]

                        lines.append(
                            f"  {b.name} | parent={bone.parent.name if bone.parent else None} "
                            f"| len={bone.length:.4f} | cons={cons}"
                        )

                    if excluded:
                        lines.append(f"Excluded ({len(excluded)}):")

                        for nm in excluded:
                            lines.append(f"  {nm}")

            if len(arm.pose.bones) > 0:
                pb0 = arm.pose.bones[0]
                lines.append(f"PoseBone has 'select': {hasattr(pb0, 'select')}")
                lines.append(f"Bone has 'select': {hasattr(pb0.bone, 'select')}")

            if arm.animation_data and arm.animation_data.action:
                act = arm.animation_data.action
                lines.append(
                    f"Action has 'fcurves': {hasattr(act, 'fcurves')}, "
                    f"has 'layers': {hasattr(act, 'layers')}"
                )

        for obj in bpy.data.objects:
            if obj.type == 'GREASEPENCIL':
                data = obj.data
                layers = getattr(data, "layers", None)

                variant = "no layers"

                if layers and len(layers) > 0:
                    if hasattr(layers[0], "active_frame"):
                        variant = "v2 (old)"
                    else:
                        variant = "v3 (new)"

                lines.append(f"GP object: {obj.name}, api={variant}")

        path_obj = bpy.data.objects.get(s.path_name)

        if path_obj:
            pts = get_curve_world_points(context, path_obj)
            lines.append(f"Path points: {len(pts)}")

        text = "\n".join(lines)

        print(text)

        self.report({'INFO'}, "Debug info printed to system console")

        return {'FINISHED'}


class IKPATHMVP_OT_pin_bones(bpy.types.Operator):
    bl_idname = "ikpathmvp.pin_bones"
    bl_label = "Pin Selected"
    bl_description = "Lock selected pose bones in world space via constraints so they remain frozen when other bones move"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.ik_path_mvp
        arm = bpy.data.objects.get(s.effector_object) or context.active_object
        if not arm or arm.type != 'ARMATURE':
            self.report({'WARNING'}, "Select an armature in Pose Mode first")
            return {'CANCELLED'}

        selected_bones = []
        if context.selected_pose_bones:
            selected_bones = [b for b in context.selected_pose_bones if b.id_data == arm]
        if not selected_bones:
            selected_bones = [b for b in arm.pose.bones if _pose_bone_selected(b)]

        existing = [b.strip() for b in s.pinned_bones.split(',') if b.strip()]
        added = []
        for pb in selected_bones:
            if pb.name != s.root_bone:
                _pin_bone_live(context, arm, pb)
                if pb.name not in existing:
                    existing.append(pb.name)
                    added.append(pb.name)

        s.pinned_bones = ", ".join(existing)
        context.view_layer.update()

        if added:
            self.report({'INFO'}, f"Pinned in world: {', '.join(added)}")
        else:
            self.report({'INFO'}, "No new bones to pin (select bones first)")
        return {'FINISHED'}


class IKPATHMVP_OT_unpin_bones(bpy.types.Operator):
    bl_idname = "ikpathmvp.unpin_bones"
    bl_label = "Unpin All"
    bl_description = "Clear all pinned bones and remove pin constraints"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.ik_path_mvp
        arm = bpy.data.objects.get(s.effector_object) or context.active_object
        _unpin_all_live(context, arm)
        s.pinned_bones = ""
        context.view_layer.update()
        self.report({'INFO'}, "All bone pins and constraints cleared")
        return {'FINISHED'}


class IKPATHMVP_OT_apply_bake(bpy.types.Operator):
    bl_idname = "ikpathmvp.apply_bake"
    bl_label = "Apply Bake"
    bl_description = "Finalize the animation and delete the preview path"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global _preview_rest_state
        s = context.scene.ik_path_mvp
        path_obj = bpy.data.objects.get(s.path_name)
        if path_obj and s.delete_path_after_bake:
            bpy.data.objects.remove(path_obj, do_unlink=True)
            s.path_name = ""

        clear_preview_rest_state()
        s.is_preview = False
        self.report({'INFO'}, "Animation finalized and baked!")
        return {'FINISHED'}


class IKPATHMVP_OT_cancel_preview(bpy.types.Operator):
    bl_idname = "ikpathmvp.cancel_preview"
    bl_label = "Cancel Preview"
    bl_description = "Discard preview animation and delete the path"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global _preview_rest_state
        s = context.scene.ik_path_mvp
        path_obj = bpy.data.objects.get(s.path_name)
        if path_obj:
            bpy.data.objects.remove(path_obj, do_unlink=True)
            s.path_name = ""

        arm = bpy.data.objects.get(s.effector_object)
        if arm and arm.type == 'ARMATURE':
            if arm.animation_data and arm.animation_data.action:
                action = arm.animation_data.action
                fstart = s.frame_start
                fend = s.frame_end
                for fcurve in _iter_action_fcurves(action):
                    kps = fcurve.keyframe_points
                    for idx in range(len(kps) - 1, -1, -1):
                        if fstart <= kps[idx].co.x <= fend:
                            kps.remove(kps[idx])

            # Restore pristine rest pose if available
            if _preview_rest_state:
                inv = arm.matrix_world.inverted()
                if _preview_rest_state.get('root_name') and _preview_rest_state.get('root_start') is not None:
                    rpb = arm.pose.bones.get(_preview_rest_state['root_name'])
                    if rpb:
                        rpb.location = _preview_rest_state['root_start'].copy()
                        _restore_rot(rpb, _preview_rest_state['root_base_rot'])
                if _preview_rest_state.get('body_name') and _preview_rest_state.get('body_base_rot') is not None:
                    bpb = arm.pose.bones.get(_preview_rest_state['body_name'])
                    if bpb:
                        _restore_rot(bpb, _preview_rest_state['body_base_rot'])
                if _preview_rest_state.get('bone_name') and _preview_rest_state.get('start_matrix_world') is not None:
                    epb = arm.pose.bones.get(_preview_rest_state['bone_name'])
                    if epb:
                        epb.matrix = inv @ _preview_rest_state['start_matrix_world']
                for leg in _preview_rest_state.get('leg_controllers', []):
                    lb = arm.pose.bones.get(leg['bone_name'])
                    if lb and 'foot_start_m' in leg:
                        lb.matrix = inv @ leg['foot_start_m']

            context.scene.frame_set(s.frame_start)
            context.view_layer.update()

        clear_preview_rest_state()
        s.is_preview = False
        self.report({'INFO'}, "Preview discarded")
        return {'FINISHED'}


# ============================================================
# UI Panel
# ============================================================

class VIEW3D_PT_ikpath_mvp(bpy.types.Panel):
    bl_label = "IK Path MVP v0.5.4"
    bl_idname = "VIEW3D_PT_ikpath_mvp"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "IK Path MVP"

    def draw(self, context):
        layout = self.layout
        s = context.scene.ik_path_mvp

        # Preview Mode Banner & Actions
        if s.is_preview:
            box = layout.box()
            box.alert = True
            box.label(text="Preview Active: Tweak sliders below", icon='RESTRICT_VIEW_OFF')
            row = box.row(align=True)
            row.scale_y = 1.3
            row.operator("ikpathmvp.apply_bake", text="Apply Bake", icon='CHECKMARK')
            row.operator("ikpathmvp.cancel_preview", text="Cancel", icon='CANCEL')

        # Draw
        box = layout.box()
        box.label(text="Draw", icon='GREASEPENCIL')

        box.operator(
            "ikpathmvp.draw_path",
            text="Draw Path and Bake",
            icon='GREASEPENCIL'
        )

        box.prop(s, "draw_anchor")

        # Controller
        box = layout.box()
        box.label(text="Controller", icon='BONE_DATA')

        if s.effector_bone:
            box.label(text=f"{s.effector_object} / {s.effector_bone}")
        elif s.effector_object:
            box.label(text=f"{s.effector_object}")
        else:
            box.label(text="not set")

        box.prop(s, "auto_capture")

        row = box.row(align=True)
        row.operator("ikpathmvp.capture_controller", text="Capture", icon='EYEDROPPER')
        row.operator("ikpathmvp.clear_controller", text="Clear", icon='X')

        # Solver
        box = layout.box()
        box.label(text="Solver", icon='CON_KINEMATIC')

        box.prop(s, "solve_mode")

        if s.solve_mode == 'BODY_DRAG':
            box.prop(s, "body_follow", slider=True)
            box.prop(s, "body_rotate", slider=True)
            box.prop(s, "leg_dangle")
            if s.leg_dangle:
                box.prop(s, "leg_stretch_limit", slider=True)
                box.prop(s, "leg_dangle_amount", slider=True)
            box.prop(s, "limit_stretch")
            box.prop(s, "max_reach")
            box.prop(s, "root_bone")
            box.prop(s, "body_bone")
            box.prop(s, "root_max_translate")

        if s.solve_mode != 'DIRECT':
            box.prop(s, "chain_max_length")

        box.prop(s, "path_mode")

        # Pinned Bones (Lock in World)
        box = layout.box()
        box.label(text="Pinned Bones (Lock in World)", icon='PINNED')
        row = box.row(align=True)
        row.operator("ikpathmvp.pin_bones", text="Pin Selected", icon='RESTRICT_SELECT_OFF')
        row.operator("ikpathmvp.unpin_bones", text="Unpin All", icon='X')
        if s.pinned_bones.strip():
            box.label(text=f"Locked: {s.pinned_bones}", icon='CHECKMARK')

        # Chain & Filters
        box = layout.box()
        box.label(text="Chain & Filters", icon='OUTLINER_OB_ARMATURE')
        box.prop(s, "filter_rigify")
        box.prop(s, "filter_constrained")
        box.prop(s, "chain_override", text="Override")

        # Path
        box = layout.box()
        box.label(text="Path", icon='CURVE_DATA')

        box.operator(
            "ikpathmvp.create_path",
            text="Create Path from Last GP Stroke",
            icon='GREASEPENCIL'
        )

        box.prop_search(
            s,
            "path_name",
            bpy.data,
            "objects",
            text="Path Object",
            icon='OUTLINER_OB_CURVE'
        )

        box.operator("ikpathmvp.clear_path", text="Clear Path", icon='X')

        # Animation
        box = layout.box()
        box.label(text="Animation", icon='ACTION')

        box.prop(s, "key_count")
        box.prop(s, "smooth_path")
        box.prop(s, "delete_path_after_bake")

        row = box.row(align=True)
        row.prop(s, "frame_start")
        row.prop(s, "frame_end")

        box.prop(s, "preserve_offsets")
        box.prop(s, "set_frame_range")

        layout.separator()

        layout.operator(
            "ikpathmvp.bake",
            text="Bake Along Path",
            icon='KEYFRAME_HLT'
        )

        layout.separator()

        layout.operator("ikpathmvp.debug", text="Debug Info", icon='CONSOLE')


# ============================================================
# Menu
# ============================================================

class VIEW3D_MT_ikpath_mvp_menu(bpy.types.Menu):
    bl_label = "IK Path MVP"
    bl_idname = "VIEW3D_MT_ikpath_mvp_menu"

    def draw(self, context):
        layout = self.layout

        layout.operator("ikpathmvp.capture_controller", icon='EYEDROPPER')
        layout.operator("ikpathmvp.draw_path", icon='GREASEPENCIL')
        layout.operator("ikpathmvp.create_path", icon='GREASEPENCIL')
        layout.operator("ikpathmvp.bake", icon='KEYFRAME_HLT')

        layout.separator()

        layout.operator("ikpathmvp.clear_controller", icon='X')
        layout.operator("ikpathmvp.clear_path", icon='X')


def menu_func(self, context):
    self.layout.separator()
    self.layout.menu(VIEW3D_MT_ikpath_mvp_menu.bl_idname, icon='CON_FOLLOWPATH')


# ============================================================
# Register
# ============================================================

classes = (
    IKPathMVPSettings,
    IKPATHMVP_OT_capture_controller,
    IKPATHMVP_OT_clear_controller,
    IKPATHMVP_OT_draw_path,
    IKPATHMVP_OT_create_path,
    IKPATHMVP_OT_bake,
    IKPATHMVP_OT_apply_bake,
    IKPATHMVP_OT_cancel_preview,
    IKPATHMVP_OT_clear_path,
    IKPATHMVP_OT_pin_bones,
    IKPATHMVP_OT_unpin_bones,
    IKPATHMVP_OT_debug,
    VIEW3D_PT_ikpath_mvp,
    VIEW3D_MT_ikpath_mvp_menu,
)


TIMER_NS_KEY = "ikpathmvp_timer_fn"


def register():
    global _TIMER_REGISTERED

    # clean stale classes left by a previous script run (F8 / re-execute)
    for cls in reversed(classes):
        if hasattr(bpy.types, cls.__name__):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass

    if hasattr(bpy.types.Scene, "ik_path_mvp"):
        try:
            del bpy.types.Scene.ik_path_mvp
        except Exception:
            pass

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.ik_path_mvp = bpy.props.PointerProperty(
        type=IKPathMVPSettings
    )

    object_menu = getattr(bpy.types, "VIEW3D_MT_object", None)
    pose_menu = getattr(bpy.types, "VIEW3D_MT_pose", None)

    for menu in (object_menu, pose_menu):
        if menu:
            try:
                menu.remove(menu_func)
            except Exception:
                pass

            menu.append(menu_func)

    # kill stale timer from a previous module instance, then start a fresh one
    ns = bpy.app.driver_namespace
    old_fn = ns.get(TIMER_NS_KEY)

    if old_fn is not None:
        try:
            bpy.app.timers.unregister(old_fn)
        except Exception:
            pass

    if not _TIMER_REGISTERED:
        bpy.app.timers.register(_controller_poll, first_interval=0.3)
        _TIMER_REGISTERED = True

    ns[TIMER_NS_KEY] = _controller_poll


def unregister():
    global _TIMER_REGISTERED

    if _TIMER_REGISTERED:
        try:
            bpy.app.timers.unregister(_controller_poll)
        except Exception:
            pass
        _TIMER_REGISTERED = False

    bpy.app.driver_namespace.pop(TIMER_NS_KEY, None)

    object_menu = getattr(bpy.types, "VIEW3D_MT_object", None)
    pose_menu = getattr(bpy.types, "VIEW3D_MT_pose", None)

    if object_menu:
        try:
            object_menu.remove(menu_func)
        except Exception:
            pass

    if pose_menu:
        try:
            pose_menu.remove(menu_func)
        except Exception:
            pass

    if hasattr(bpy.types.Scene, "ik_path_mvp"):
        del bpy.types.Scene.ik_path_mvp

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()