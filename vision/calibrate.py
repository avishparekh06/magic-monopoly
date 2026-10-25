"""Calibrate the camera -> mat mapping and write zones.json.

  .venv/bin/python vision/calibrate.py            live camera, click corners
  .venv/bin/python vision/calibrate.py --aruco    live camera, auto-detect ArUco DICT_4X4_50 ids 0-3 at the corners
  .venv/bin/python vision/calibrate.py --image f  calibrate from a saved frame

Click order: top-left, top-right, bottom-right, bottom-left of the mat (as seen in the camera image).
With ArUco, put marker 200 at TL, 201 at TR, 202 at BR, 203 at BL; the outermost corner of each marker is used.
Then a preview shows the warped mat with the dice and card zones; edit zones.json by hand to adjust them.
"""
import argparse
import sys
import cv2
import numpy as np
from zones import Zones, DEFAULT_PATH

MAT_IDS = (200, 201, 202, 203)              # TL, TR, BR, BL (DICT_4X4_250); cards use 0-31, player cards 100-102


def open_camera(z):
    cam = z.d["camera"]
    cap = cv2.VideoCapture(cam["index"], cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam["height"])
    if not cap.isOpened():
        sys.exit("camera did not open (check index in zones.json and camera permission for your terminal)")
    return cap


def grab(cap):
    for _ in range(5):
        cap.read()                     # let exposure settle
    ok, frame = cap.read()
    if not ok:
        sys.exit("no frame")
    return frame


def click_corners(frame):
    pts = []
    win = "click mat corners: TL, TR, BR, BL  (r = reset, q = quit)"

    def on_mouse(ev, x, y, flags, _):
        if ev == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append([x, y])

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        view = frame.copy()
        for i, p in enumerate(pts):
            cv2.circle(view, tuple(p), 8, (0, 255, 0), -1)
            cv2.putText(view, "TL TR BR BL".split()[i], (p[0] + 10, p[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        if len(pts) == 4:
            cv2.polylines(view, [np.int32(pts)], True, (0, 255, 0), 2)
        cv2.imshow(win, view)
        k = cv2.waitKey(30) & 0xFF
        if k == ord("r"):
            pts.clear()
        elif k == ord("q"):
            sys.exit("cancelled")
        elif len(pts) == 4 and k in (13, 32):   # enter or space to accept
            break
        elif len(pts) == 4 and k == 255:
            cv2.putText(view, "press enter to accept", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            cv2.imshow(win, view)
    cv2.destroyWindow(win)
    return pts


def aruco_corners(frame):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250)
    det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(frame)
    if ids is None:
        return None
    found = {int(i): c.reshape(4, 2) for i, c in zip(ids.ravel(), corners)}
    if not all(i in found for i in MAT_IDS):
        print("saw markers", sorted(found), "need", MAT_IDS)
        return None
    centre = np.mean([found[i].mean(0) for i in MAT_IDS], axis=0)
    out = []
    for i in MAT_IDS:                       # outermost corner of each marker = mat corner
        c = found[i]
        out.append(c[np.argmax(np.linalg.norm(c - centre, axis=1))].tolist())
    return out


def preview(z, frame):
    mat = z.warp(frame)
    for key, col in (("dice_zone_mm", (0, 255, 0)), ("card_zone_mm", (255, 160, 0))):
        x0, y0, x1, y1 = z.rect_px(key)
        cv2.rectangle(mat, (x0, y0), (x1, y1), col, 2)
        cv2.putText(mat, key.replace("_mm", ""), (x0 + 6, y0 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
    cv2.imshow("warped mat preview (any key to close)", mat)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aruco", action="store_true")
    ap.add_argument("--image")
    ap.add_argument("--out", default=DEFAULT_PATH)
    a = ap.parse_args()
    z = Zones.load(a.out)
    if a.image:
        frame = cv2.imread(a.image)
        if frame is None:
            sys.exit(f"could not read {a.image}")
    else:
        cap = open_camera(z)
        if a.aruco:
            pts = None
            while pts is None:
                frame = grab(cap)
                pts = aruco_corners(frame)
                cv2.imshow("looking for ArUco 200-203 (q to quit)", frame)
                if cv2.waitKey(200) & 0xFF == ord("q"):
                    sys.exit("cancelled")
            cv2.destroyAllWindows()
        else:
            frame = grab(cap)
        cap.release()
    if a.aruco and a.image:
        pts = aruco_corners(frame)
        if pts is None:
            sys.exit("ArUco markers 0-3 not all found")
    elif not a.aruco:
        pts = click_corners(frame)
    z.d["corners_px"] = pts
    z = Zones(z.d)
    z.save(a.out)
    print("saved", a.out, "corners", pts)
    preview(z, frame)


if __name__ == "__main__":
    main()
