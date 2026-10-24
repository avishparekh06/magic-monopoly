"""Camera probe: list what opens, what settings macOS honors, and show a live view.

Usage:  .venv/bin/python vision/probe.py --list     save a thumbnail from every camera index to vision/samples/idx<N>.jpg
        .venv/bin/python vision/probe.py [index]    live view of one camera
Keys:   q quit, s save a frame to vision/samples/

macOS renumbers cameras whenever one is plugged in, so run --list to find the C920 each session,
then put that number in zones.json under camera.index.
"""
import sys, time, os
import cv2

os.makedirs("vision/samples", exist_ok=True)
if "--list" in sys.argv:
    for i in range(5):
        cap = cv2.VideoCapture(i, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            print(f"index {i}: not available"); continue
        for _ in range(5):
            cap.read()
        ok, f = cap.read()
        if ok:
            path = f"vision/samples/idx{i}.jpg"
            cv2.imwrite(path, cv2.resize(f, (640, 360)))
            print(f"index {i}: {f.shape[1]}x{f.shape[0]} -> {path}")
        cap.release()
    print("open the thumbnails and set camera.index in zones.json to the C920's number")
    sys.exit(0)

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
cap = cv2.VideoCapture(idx, cv2.CAP_AVFOUNDATION)
if not cap.isOpened():
    sys.exit(f"camera {idx} did not open. Grant Camera access to your terminal app in "
             "System Settings > Privacy & Security > Camera, then retry. Try index 1 or 2.")

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
cap.set(cv2.CAP_PROP_FPS, 30)

PROPS = [("width", cv2.CAP_PROP_FRAME_WIDTH), ("height", cv2.CAP_PROP_FRAME_HEIGHT), ("fps", cv2.CAP_PROP_FPS),
         ("autofocus", cv2.CAP_PROP_AUTOFOCUS), ("focus", cv2.CAP_PROP_FOCUS),
         ("auto_exposure", cv2.CAP_PROP_AUTO_EXPOSURE), ("exposure", cv2.CAP_PROP_EXPOSURE),
         ("auto_wb", cv2.CAP_PROP_AUTO_WB), ("backend", cv2.CAP_PROP_BACKEND)]
for name, p in PROPS:
    print(f"{name:14s} get={cap.get(p)}")
print("set autofocus=0 ->", cap.set(cv2.CAP_PROP_AUTOFOCUS, 0), "(False means macOS ignored it)")

t0, n = time.time(), 0
while True:
    ok, frame = cap.read()
    if not ok:
        print("frame grab failed"); break
    n += 1
    raw = frame.copy()
    fps = n / max(time.time() - t0, 1e-6)
    cv2.putText(frame, f"{frame.shape[1]}x{frame.shape[0]} {fps:.1f} fps  q=quit s=save", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.imshow("probe", frame)
    k = cv2.waitKey(1) & 0xFF
    if k == ord("q"):
        break
    if k == ord("s"):
        path = f"vision/samples/frame_{int(time.time())}.jpg"
        cv2.imwrite(path, raw); print("saved", path)
cap.release(); cv2.destroyAllWindows()
