"""Print this: four ArUco DICT_4X4_50 markers (ids 0-3) to tape at the mat corners. Writes vision/aruco_sheet.png.

Cut them out and place: 0 top-left, 1 top-right, 2 bottom-right, 3 bottom-left (looking at the mat from the camera).
"""
import cv2
import numpy as np

d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
size, pad = 400, 60
sheet = np.full((2 * (size + 2 * pad), 2 * (size + 2 * pad)), 255, np.uint8)
for i in range(4):
    m = cv2.aruco.generateImageMarker(d, i, size)
    r, c = divmod(i, 2)
    y, x = r * (size + 2 * pad) + pad, c * (size + 2 * pad) + pad
    sheet[y:y + size, x:x + size] = m
    cv2.putText(sheet, f"id {i}  (place at {'TL TR BR BL'.split()[i]})", (x, y + size + 40), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
cv2.imwrite("vision/aruco_sheet.png", sheet)
print("wrote vision/aruco_sheet.png  (print at 100%, each marker ~5 cm)")
