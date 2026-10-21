"""One-time logo preparation.

Input : transparent PNG of the icon (black ink + orange dot) and the cream
        lockup image (used only to lift the wordmark).
Output: assets/ with
          stroke.png, dot.png, bar_0..4.png, wordmark.png   (RGBA, cropped)
          param_map.npy   pen-order position (0..1) for every stroke pixel
          meta.json       bounding boxes, colours, waypoints used

Run:  python logo_prep.py --logo assets_src/logo_white.png \
                          --lockup assets_src/logo_cream_lockup.png \
                          --out assets
"""
import argparse
import json
import os

import cv2
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from skimage.morphology import skeletonize

CREAM_RGB = (238, 230, 219)   # sampled from the paper of the lockup image

# Pen order for the current icon, in the pixel coordinates of the 1254x1254
# logo file. They are snapped to the nearest skeleton pixel, so they only need
# to be close. Order: nib, down the stem, round the bottom loop, back up
# through the knot, up the S-curve, out along the top swoosh.
DEFAULT_WAYPOINTS = [
    (650, 50), (636, 420), (638, 700), (560, 1130), (500, 1172),
    (372, 1010), (500, 810), (632, 680), (545, 540), (585, 470),
    (632, 400), (720, 280), (765, 200), (692, 130),
]


def save_rgba(path, rgb, alpha):
    out = np.dstack([rgb[..., ::-1], alpha]).astype(np.uint8)  # to BGRA for cv2
    cv2.imwrite(path, out)


def crop_box(mask, pad=4):
    ys, xs = np.nonzero(mask)
    return (int(xs.min()) - pad, int(ys.min()) - pad,
            int(xs.max()) + pad + 1, int(ys.max()) + pad + 1)


def split_logo(logo_path):
    im = cv2.imread(logo_path, cv2.IMREAD_UNCHANGED)
    if im is None or im.ndim != 3 or im.shape[2] != 4:
        raise SystemExit("logo must be a PNG with a transparency channel")
    bgr, alpha = im[..., :3], im[..., 3]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    H, S, V = [hsv[..., i].astype(int) for i in range(3)]
    solid = alpha > 128
    orange = solid & (S > 110) & (V > 90) & (H >= 5) & (H <= 25)
    orange_near = cv2.dilate(orange.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    ink = solid & ~orange_near
    n, lab, st, cen = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    comps = [i for i in range(1, n) if st[i, 4] > 300]
    comps.sort(key=lambda i: -st[i, 4])
    stroke_id, bar_ids = comps[0], sorted(comps[1:], key=lambda i: cen[i][0])
    return bgr, alpha, orange, orange_near, lab, stroke_id, bar_ids


def ink_layer(alpha, lab, comp_id, grow=3):
    """Alpha restricted to one connected component (grown a little to keep the
    soft anti-aliased edge), tinted cream."""
    region = cv2.dilate((lab == comp_id).astype(np.uint8), np.ones((grow * 2 + 1,) * 2, np.uint8)) > 0
    return np.where(region, alpha, 0).astype(np.uint8)


def skeleton_graph(mask):
    skel = skeletonize(mask > 0)
    ys, xs = np.nonzero(skel)
    idx = -np.ones(mask.shape, np.int64)
    idx[ys, xs] = np.arange(len(ys))
    rows, cols, w = [], [], []
    for dy, dx in [(0, 1), (1, 0), (1, 1), (1, -1)]:
        y2, x2 = ys + dy, xs + dx
        ok = (y2 >= 0) & (y2 < mask.shape[0]) & (x2 >= 0) & (x2 < mask.shape[1])
        a = np.nonzero(ok)[0]
        b = idx[y2[a], x2[a]]
        keep = b >= 0
        a, b = a[keep], b[keep]
        rows += [a, b]
        cols += [b, a]
        w += [np.full(len(a) * 2, np.hypot(dy, dx))]
    g = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))),
                   shape=(len(ys), len(ys))).tocsr()
    return np.c_[xs, ys], g


def pen_chain(pts, graph, waypoints):
    """Ordered skeleton points along the pen path through the waypoints."""
    tree = cKDTree(pts)
    snapped = [int(tree.query(w)[1]) for w in waypoints]
    chain = []
    for a, b in zip(snapped[:-1], snapped[1:]):
        _, pred = dijkstra(graph, directed=False, indices=a, return_predecessors=True)
        path, cur = [b], b
        while cur != a:
            cur = pred[cur]
            if cur < 0:
                raise SystemExit("waypoints are not connected on the skeleton; adjust them")
            path.append(cur)
        chain += path[::-1][1:] if chain else path[::-1]
    return pts[chain]


def build_param_map(stroke_alpha, waypoints):
    mask = (stroke_alpha > 128).astype(np.uint8)
    pts, g = skeleton_graph(mask)
    chain = pen_chain(pts, g, waypoints)
    step = np.r_[0, np.hypot(*np.diff(chain, axis=0).T)]
    arc = np.cumsum(step)
    arc /= arc[-1]
    # first occurrence wins where the pen crosses its own path
    _, first = np.unique(chain, axis=0, return_index=True)
    first.sort()
    tree = cKDTree(chain[first])
    ys, xs = np.nonzero(stroke_alpha > 0)
    _, j = tree.query(np.c_[xs, ys])
    param = np.full(stroke_alpha.shape, 2.0, np.float32)
    param[ys, xs] = arc[first][j]
    return param, chain, pts


def extract_wordmark(lockup_path, y_min=872):
    """Lift the wordmark off the textured paper as an alpha matte.

    y_min must sit below the icon's lowest point in the lockup image and above
    the macron of the wordmark."""
    im = cv2.imread(lockup_path, cv2.IMREAD_COLOR)
    lum = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32)
    bg = cv2.GaussianBlur(cv2.morphologyEx(lum, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8)), (0, 0), 15)
    a = np.clip((bg - lum) / np.maximum(bg - 45.0, 1.0), 0, 1)
    a = np.clip((a - 0.22) / 0.55, 0, 1)          # drop paper grain, keep ink edge
    a[:y_min] = 0
    box = crop_box(a > 0.5, pad=6)
    x0, y0, x1, y1 = box
    return (a[y0:y1, x0:x1] * 255).astype(np.uint8), box


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", required=True)
    ap.add_argument("--lockup", required=True)
    ap.add_argument("--out", default="assets")
    ap.add_argument("--waypoints", help="JSON file: [[x, y], ...] in logo pixel coordinates")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    waypoints = DEFAULT_WAYPOINTS
    if args.waypoints:
        waypoints = [tuple(p) for p in json.load(open(args.waypoints))]

    bgr, alpha, orange, orange_near, lab, stroke_id, bar_ids = split_logo(args.logo)
    cream = np.zeros(bgr.shape, np.uint8)
    cream[:] = CREAM_RGB[::-1]            # BGR
    cream_rgb = cream[..., ::-1]

    meta = {"canvas": list(alpha.shape[::-1]), "cream_rgb": CREAM_RGB, "layers": {}}

    stroke_a = ink_layer(alpha, lab, stroke_id)
    layers = {"stroke": stroke_a}
    for k, i in enumerate(bar_ids):
        layers[f"bar_{k}"] = ink_layer(alpha, lab, i)

    for name, a in layers.items():
        x0, y0, x1, y1 = crop_box(a > 0)
        save_rgba(os.path.join(args.out, f"{name}.png"), cream_rgb[y0:y1, x0:x1], a[y0:y1, x0:x1])
        meta["layers"][name] = [x0, y0, x1, y1]

    # orange dot keeps its own brush texture; un-premultiply from the edge
    dot_a = np.where(orange_near, alpha, 0).astype(np.uint8)
    x0, y0, x1, y1 = crop_box(dot_a > 0)
    save_rgba(os.path.join(args.out, "dot.png"), bgr[y0:y1, x0:x1][..., ::-1], dot_a[y0:y1, x0:x1])
    meta["layers"]["dot"] = [x0, y0, x1, y1]

    param, chain, _ = build_param_map(stroke_a, waypoints)
    sx0, sy0, sx1, sy1 = meta["layers"]["stroke"]
    np.save(os.path.join(args.out, "param_map.npy"), param[sy0:sy1, sx0:sx1])
    meta["waypoints"] = [list(map(int, w)) for w in waypoints]

    wm_a, wm_box = extract_wordmark(args.lockup)
    wm_rgb = np.zeros(wm_a.shape + (3,), np.uint8)
    wm_rgb[:] = CREAM_RGB
    save_rgba(os.path.join(args.out, "wordmark.png"), wm_rgb, wm_a)
    meta["wordmark_size"] = [int(wm_a.shape[1]), int(wm_a.shape[0])]

    ys, xs = np.nonzero(alpha > 128)
    meta["icon_bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    json.dump(meta, open(os.path.join(args.out, "meta.json"), "w"), indent=1)

    # debug picture: skeleton chain coloured by pen order
    dbg = np.full(alpha.shape + (3,), 255, np.uint8)
    dbg[stroke_a > 128] = (200, 200, 200)
    for k, (x, y) in enumerate(chain):
        c = cv2.applyColorMap(np.uint8([[int(255 * k / len(chain))]]), cv2.COLORMAP_TURBO)[0, 0]
        cv2.circle(dbg, (int(x), int(y)), 3, tuple(int(v) for v in c), -1)
    for i, (x, y) in enumerate(waypoints):
        cv2.putText(dbg, str(i), (int(x) + 8, int(y)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    cv2.imwrite(os.path.join(args.out, "debug_pen_chain.png"), dbg)
    print("layers:", list(meta["layers"]), "| chain points:", len(chain))


if __name__ == "__main__":
    main()
