"""python3 -m twinbrick_gui [LAT,LON] [--screenshot PNG]"""
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser(prog="twinbrick-it", description="TwinBrick it: a place on earth as a brick model")
    ap.add_argument("place", nargs="?", help="the centre: LAT,LON")
    ap.add_argument("--window", choices=("normal", "maximized", "full"), default="normal")
    ap.add_argument("--screenshot", default=None, metavar="PNG", help="open, wait for the map, save the window, quit")
    ap.add_argument("--wait", type=float, default=6.0, help="--screenshot: seconds to wait for the map tiles")
    a = ap.parse_args()
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    app.setApplicationName("TwinBrick it")
    app.setDesktopFileName("twinbrick-it")
    from .app import MainWindow
    w = MainWindow(a.place)
    if a.window == "maximized":
        w.showMaximized()
    elif a.window == "full":
        w.showFullScreen()
    else:
        w.show()
    if a.screenshot:
        def shoot():
            w.grab().save(a.screenshot)
            app.quit()
        QTimer.singleShot(int(a.wait * 1000), shoot)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
