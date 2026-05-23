#!/usr/bin/env python3
"""Capture camera frames, detect QR codes, save decoded data to a file, and beep on each detection."""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from pathlib import Path
from typing import Optional, Set

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from pyzbar.pyzbar import ZBarSymbol, decode as pyzbar_decode
except ImportError:  # pragma: no cover
    pyzbar_decode = None
    ZBarSymbol = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Use the default camera to scan QR codes in real time. "
            "Each decoded QR payload is appended to the output file and a beep is played."
        )
    )
    parser.add_argument(
        "camera",
        nargs="?",
        type=int,
        default=0,
        help="Camera index to use (default: %(default)s).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("decoded_qr.txt"),
        help="Path to the file where decoded QR data will be saved.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Optional maximum number of frames to process (0 means run until interrupted).",
    )
    parser.add_argument(
        "--unique",
        action="store_true",
        help="Only save unique QR payloads once.",
    )
    return parser


def beep() -> None:
    if os.name == "nt":
        try:
            import winsound

            winsound.Beep(1000, 150)
        except Exception:  # pragma: no cover
            print("\a", end="", flush=True)
    else:
        print("\a", end="", flush=True)


def append_payload(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        timestamp = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
        handle.write(f"[{timestamp}] {payload}\n")


def preprocess_frame(frame: "cv2.Mat") -> "cv2.Mat":
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # Dense QR versions need more pixels per module for reliable decode.
    min_side = min(gray.shape[:2])
    if min_side < 480:
        scale = 4.0
    elif min_side < 720:
        scale = 2.0
    else:
        scale = 1.0
    if scale != 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return gray


def threshold_frame(gray: "cv2.Mat") -> "cv2.Mat":
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    return cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        41,
        10,
    )


def sharpen_frame(gray: "cv2.Mat") -> "cv2.Mat":
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.2)
    return cv2.addWeighted(gray, 1.7, blurred, -0.7, 0)


def open_camera(index: int) -> "cv2.VideoCapture":
    if cv2 is None:
        sys.exit(
            "Error: OpenCV is required for camera capture. "
            "Install it with `pip install opencv-python`."
        )
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        sys.exit(f"Error: Cannot open camera index {index}.")
    return cap


def decode_with_pyzbar(gray: "cv2.Mat") -> list[tuple[str, list[int]]]:
    if pyzbar_decode is None:
        return []
    results: list[tuple[str, list[int]]] = []
    try:
        symbols = [ZBarSymbol.QRCODE] if ZBarSymbol is not None else None
        decoded = pyzbar_decode(gray, symbols=symbols) if symbols is not None else pyzbar_decode(gray)
    except Exception:
        decoded = []

    for obj in decoded:
        try:
            payload = obj.data.decode("utf-8", errors="replace")
        except Exception:
            payload = obj.data.decode("latin-1", errors="replace")
        if not payload:
            continue
        pts = []
        if hasattr(obj, "polygon") and obj.polygon:
            pts = [coord for point in obj.polygon for coord in (point.x, point.y)]
        elif hasattr(obj, "rect") and obj.rect:
            rect = obj.rect
            pts = [rect.left, rect.top, rect.left + rect.width, rect.top, rect.left + rect.width, rect.top + rect.height, rect.left, rect.top + rect.height]
        if pts:
            results.append((payload, pts))
    return results


def decode_with_opencv(frame: "cv2.Mat", detector: "cv2.QRCodeDetector") -> list[tuple[str, list[int]]]:
    results: list[tuple[str, list[int]]] = []
    try:
        payload, points, _ = detector.detectAndDecode(frame)
    except Exception:
        payload, points = "", None
    if payload:
        if points is not None and hasattr(points, "reshape"):
            pts = [int(coord) for coord in points.reshape(-1, 2).flatten()]
        elif points is not None:
            pts = [int(coord) for pair in points for coord in pair]
        else:
            pts = []
        results.append((payload, pts))

    try:
        result = detector.detectAndDecodeMulti(frame)
    except Exception:
        return results
    if not result:
        return results
    if len(result) == 4:
        _, data, points, _ = result
    else:
        data, points, _ = result
    decoded: list[str] = []
    if isinstance(data, (list, tuple)):
        decoded = [item for item in data if item]
    elif isinstance(data, str) and data:
        decoded = [data]
    if points is None:
        for payload in decoded:
            results.append((payload, []))
        return results
    for payload, qr_points in zip(decoded, points):
        if not payload or qr_points is None:
            continue
        if hasattr(qr_points, "reshape"):
            pts = [int(coord) for coord in qr_points.reshape(-1, 2).flatten()]
        else:
            pts = [int(coord) for pair in qr_points for coord in pair]
        results.append((payload, pts))
    return results


def merge_results(results: list[tuple[str, list[int]]]) -> tuple[list[str], list[list[int]]]:
    merged: dict[str, list[int]] = {}
    for payload, pts in results:
        if payload not in merged or (pts and len(pts) > len(merged[payload])):
            merged[payload] = pts
    return list(merged), list(merged.values())


def detect_qr(frame: "cv2.Mat", detector: "cv2.QRCodeDetector") -> tuple[list[str], list[list[int]]]:
    gray = preprocess_frame(frame)
    sharp = sharpen_frame(gray)
    thresh = threshold_frame(gray)
    sharp_thresh = threshold_frame(sharp)
    candidates: list[tuple[str, list[int]]] = []
    candidates.extend(decode_with_pyzbar(frame))
    candidates.extend(decode_with_pyzbar(gray))
    candidates.extend(decode_with_pyzbar(sharp))
    candidates.extend(decode_with_pyzbar(thresh))
    candidates.extend(decode_with_pyzbar(sharp_thresh))
    candidates.extend(decode_with_opencv(frame, detector))
    candidates.extend(decode_with_opencv(gray, detector))
    candidates.extend(decode_with_opencv(sharp, detector))
    candidates.extend(decode_with_opencv(thresh, detector))
    candidates.extend(decode_with_opencv(sharp_thresh, detector))

    payloads, boxes = merge_results(candidates)
    return payloads, boxes


def draw_boxes(frame: "cv2.Mat", boxes: list[list[int]]) -> "cv2.Mat":
    for pts in boxes:
        if len(pts) < 8:
            continue
        points = [(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)]
        for j in range(len(points)):
            start = points[j]
            end = points[(j + 1) % len(points)]
            cv2.line(frame, start, end, (0, 255, 0), 2)
    return frame


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if cv2 is None:
        sys.exit(
            "Error: OpenCV is not installed. Install it with `pip install opencv-python`."
        )

    if pyzbar_decode is None:
        print("Warning: pyzbar is not installed. Falling back to OpenCV-only detection.")

    detector = cv2.QRCodeDetector()
    for attr, value in (("setEpsX", 0.2), ("setEpsY", 0.2)):
        setter = getattr(detector, attr, None)
        if callable(setter):
            setter(value)
    cap = open_camera(args.camera)
    print(f"Opening camera #{args.camera}. Press ESC or Q to quit.")
    seen: Set[str] = set()
    last_seen: dict[str, float] = {}
    frame_count = 0
    repeat_delay = 2.0

    while True:
        if args.max_frames and frame_count >= args.max_frames:
            break
        frame_count += 1
        success, frame = cap.read()
        if not success:
            print("Warning: failed to read camera frame.")
            time.sleep(0.1)
            continue

        decoded, boxes = detect_qr(frame, detector)
        for payload in decoded:
            if not payload:
                continue
            now = time.monotonic()
            if args.unique and payload in seen:
                continue
            if payload in last_seen and now - last_seen[payload] < repeat_delay:
                continue
            last_seen[payload] = now
            seen.add(payload)
            append_payload(args.output, payload)
            beep()
            print(f"Detected QR payload: {payload}")

        frame = draw_boxes(frame, boxes)
        cv2.imshow("qr_2_file", frame)
        key = cv2.waitKey(1) & 0xFF
        if key in {27, ord("q"), ord("Q")}:
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"Saved decoded data to {args.output}")


if __name__ == "__main__":
    main()
