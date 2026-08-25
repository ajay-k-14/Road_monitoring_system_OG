# save as probe_cams.py and run: python probe_cams.py
import cv2
backends = [('CAP_DSHOW', cv2.CAP_DSHOW), ('CAP_MSMF', cv2.CAP_MSMF), ('DEFAULT', None)]
for i in range(6):
    ok = False
    for name, b in backends:
        try:
            cap = cv2.VideoCapture(i, b) if b is not None else cv2.VideoCapture(i)
            if cap and cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    print(f'Index {i} OK with {name}')
                    ok = True
                    break
        except Exception:
            pass
    if not ok:
        print(f'Index {i} NOT available')