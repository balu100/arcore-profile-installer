"""Bounded runtime diagnostics; logs stay local in the ignored work directory."""
import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

PACKAGE = "com.google.ar.core"
MAPS = "com.google.android.apps.maps"


def verify_runtime(adb, folder: Path, seconds: int):
    """Bounded, fresh, process-filtered log capture. No global log clearing."""
    lines = queue.Queue(maxsize=20000)
    stop = threading.Event()
    log_path = folder / "liveview.txt"
    proc = subprocess.Popen(adb.command("logcat", "-v", "threadtime", "-T", "1"),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def reader():
        for line in iter(proc.stdout.readline, b""):
            if stop.is_set():
                break
            try:
                lines.put(line.decode("utf-8", "replace"), timeout=0.1)
            except queue.Full:
                continue

    worker = threading.Thread(target=reader, daemon=True)
    worker.start()
    pids = set()
    custom = vio = inertial = False
    failures = []
    saved = 0
    dropped = 0
    deadline = time.monotonic() + seconds
    print(f"Open Maps Live View now. Capturing {seconds}s to {log_path}", flush=True)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            refresh = 0
            while time.monotonic() < deadline:
                if time.monotonic() >= refresh:
                    for package in (PACKAGE, MAPS):
                        pids.update(adb.run("shell", f"pidof {package} || true").split())
                    refresh = time.monotonic() + 2
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    if proc.poll() is not None:
                        raise RuntimeError("logcat exited; see the captured log")
                    continue
                match = re.match(r"\d\d-\d\d\s+\S+\s+(\d+)\s+\d+\s", line)
                if not match or match[1] not in pids:
                    continue
                if not re.search(r"arcore|profile|tracking|vio|calibr|intrinsic|geospatial|VPS|fatal|exception|camera.*error", line, re.I):
                    continue
                if saved + len(line.encode("utf-8")) > 2_000_000:
                    dropped += 1
                    continue
                log.write(line)
                log.flush()
                saved += len(line.encode("utf-8"))
                custom |= bool(re.search(r"Successfully loaded custom (?:v\d+ )?device profile", line, re.I))
                vio |= bool(re.search(r"kVio tracking|TrackingSource:\s*Vio.*TrackingState:\s*Tracking", line, re.I))
                inertial |= "TrackingSource: Inertial TrackingState: Tracking" in line
                if re.search(r"FATAL EXCEPTION|Fatal signal|CameraNotAvailableException|AR_UNAVAILABLE_DEVICE_NOT_COMPATIBLE|No device profile available", line):
                    failures.append(line.strip())
    finally:
        stop.set()
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)
        worker.join(timeout=2)
        proc.stdout.close()
    result = {"installed": adb.installed(), "custom_profile_loaded": custom,
              "vio_tracking_seen": vio, "inertial_tracking_seen": inertial,
              "errors": failures, "log_bytes": saved, "lines_dropped_at_size_limit": dropped}
    (folder / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if failures or not custom:
        print("Runtime test inconclusive/failed; no automatic data reset was performed.")
        return 2
    print("Custom profile load observed. Tracking quality/calibration accuracy is not certified by this test.")
    return 0
