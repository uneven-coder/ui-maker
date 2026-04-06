import csv
import json
import msvcrt
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import websocket


@dataclass(frozen=True)
class AppConfig:
    # Centralize external process configuration for predictable launch behavior.

    workspace_root: Path
    csproj_path: Path
    steam_uri: str = "steam://rungameid/1718870"
    build_timeout_seconds: int = 300
    attach_timeout_seconds: int = 120
    graceful_shutdown_timeout_seconds: int = 10
    msbuild_command: str = "msbuild"
    websocket_url: str = "ws://127.0.0.1:18650/ws"


class SingleInstanceLock:
    # Prevent parallel orchestrator sessions that could compete for process ownership.

    def __init__(self, lock_name: str):
        self._path = Path(tempfile.gettempdir()) / f"{lock_name}.lock"
        self._handle = None

    def acquire(self) -> bool:
        # Acquire an exclusive non-blocking lock; return False when already owned.

        self._handle = open(self._path, "a+")
        try:
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            self._handle.seek(0)
            self._handle.truncate(0)
            self._handle.write(str(os.getpid()))
            self._handle.flush()
            return True
        except OSError:
            self.release()
            return False

    def release(self) -> None:
        # Release held lock resources if they were acquired.

        if self._handle is None:
            return

        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None


class SFSOrchestrator:
    # Coordinate build, launch, attach, and shutdown lifecycle for SFS.

    PROCESS_NAME_HINTS = ("Spaceflight Simulator.exe", "Space Flight Simulator.exe", "SpaceFlight Simulator.exe")

    def __init__(self, config: AppConfig, log: Callable[[str], None], set_status: Callable[[str], None]):
        self._config = config
        self._log = log
        self._set_status = set_status
        self._attached_pid: Optional[int] = None
        self._mutex = threading.Lock()

    def build_mod(self) -> bool:
        # Compile the mod project before launch so the latest DLL is deployed.

        self._set_status("Building mod")
        self._log("Starting MSBuild.")

        if not self._config.csproj_path.exists():
            self._log(f"Build failed: project not found at {self._config.csproj_path}")
            self._set_status("Build failed")
            return False

        command = [
            self._config.msbuild_command,
            str(self._config.csproj_path),
            "/t:Build",
            "/p:Configuration=Debug",
        ]
        self._log(f"Command: {' '.join(command)}")

        try:
            result = subprocess.run(
                command,
                cwd=self._config.workspace_root,
                text=True,
                capture_output=True,
                timeout=self._config.build_timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            self._log("Build failed: msbuild was not found in PATH.")
            self._set_status("Build failed")
            return False
        except subprocess.TimeoutExpired:
            self._log(f"Build failed: timeout after {self._config.build_timeout_seconds}s.")
            self._set_status("Build timeout")
            return False

        if result.stdout.strip():
            self._log("MSBuild stdout:")
            for line in result.stdout.splitlines()[-40:]:
                self._log(f"  {line}")

        if result.stderr.strip():
            self._log("MSBuild stderr:")
            for line in result.stderr.splitlines()[-20:]:
                self._log(f"  {line}")

        if result.returncode != 0:
            self._log(f"Build failed: exit code {result.returncode}.")
            self._set_status("Build failed")
            return False

        self._log("Build succeeded.")
        self._set_status("Build complete")
        return True

    def launch_and_attach(self) -> bool:
        # Launch SFS through Steam and attach to the spawned process.

        self._set_status("Launching SFS")
        before = self._get_candidate_pids()
        self._log(f"Known SFS process IDs before launch: {sorted(before)}")

        try:
            os.startfile(self._config.steam_uri)
        except OSError as ex:
            self._log(f"Launch failed: {ex}")
            self._set_status("Launch failed")
            return False

        self._log(f"Steam launch URI sent: {self._config.steam_uri}")
        attached = self._wait_for_process_attachment(before)
        if not attached:
            self._set_status("Attach failed")
            return False

        self._set_status(f"Attached to SFS PID {self._attached_pid}")
        return True

    def full_startup(self) -> bool:
        # Run the full lifecycle startup pipeline in strict sequence.

        if not self.build_mod():
            return False
        return self.launch_and_attach()

    def shutdown_sfs(self) -> None:
        # Close attached SFS process with graceful-first policy and forced fallback.

        with self._mutex:
            pid = self._attached_pid

        if pid is None:
            self._log("Shutdown skipped: no attached SFS process.")
            return

        self._set_status("Shutting down SFS")
        self._log(f"Requesting graceful shutdown for PID {pid}.")
        subprocess.run(["taskkill", "/PID", str(pid)], check=False, capture_output=True, text=True)

        deadline = time.time() + self._config.graceful_shutdown_timeout_seconds
        while time.time() < deadline:
            if not self._is_pid_alive(pid):
                self._log("SFS closed gracefully.")
                with self._mutex:
                    self._attached_pid = None
                self._set_status("SFS closed")
                return
            time.sleep(0.3)

        self._log(f"Graceful shutdown timed out after {self._config.graceful_shutdown_timeout_seconds}s; forcing close.")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], check=False, capture_output=True, text=True)

        if self._is_pid_alive(pid):
            self._log("Forced shutdown failed: process still running.")
            self._set_status("Shutdown failed")
            with self._mutex:
                self._attached_pid = None
            return

        self._log("SFS terminated forcefully.")
        with self._mutex:
            self._attached_pid = None
        self._set_status("SFS closed")

    def _wait_for_process_attachment(self, before_launch: set[int]) -> bool:
        # Attach to a newly created SFS process, with explicit timeout and diagnostics.

        self._set_status("Attaching to SFS process")
        start = time.time()
        timeout = self._config.attach_timeout_seconds
        seen_candidates: set[int] = set()

        while time.time() - start <= timeout:
            current = self._get_candidate_pids()
            new_pids = current.difference(before_launch)
            if new_pids:
                pid = max(new_pids)
                with self._mutex:
                    self._attached_pid = pid
                self._log(f"Attached to newly detected SFS process PID {pid}.")
                return True

            if current and not seen_candidates:
                seen_candidates = set(current)
                self._log(f"Detected existing SFS candidates (waiting for new PID): {sorted(current)}")

            time.sleep(0.5)

        self._log(f"Attach failed: no SFS process attached within {timeout}s.")
        return False

    def _get_candidate_pids(self) -> set[int]:
        # Enumerate candidate SFS process IDs from tasklist output.

        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self._log(f"Process listing failed with exit code {result.returncode}.")
            return set()

        rows = csv.reader(result.stdout.splitlines())
        pids: set[int] = set()
        for row in rows:
            if len(row) < 2:
                continue

            image_name = row[0].strip().lower()
            pid_text = row[1].strip()
            if not any(hint.lower() in image_name for hint in self.PROCESS_NAME_HINTS):
                continue

            try:
                pids.add(int(pid_text))
            except ValueError:
                continue

        return pids

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        # Check whether a process with PID currently exists.

        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False

        output = result.stdout.strip().lower()
        return bool(output and "no tasks are running" not in output)


class RealtimeBridge:
    # Manage websocket session to the in-game C# server for visualization and sync.

    def __init__(self, on_log: Callable[[str], None], on_state: Callable[[str], None], on_json: Callable[[dict], None]):
        self._on_log = on_log
        self._on_state = on_state
        self._on_json = on_json
        self._app: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._url: Optional[str] = None

    def connect(self, url: str) -> None:
        # Open websocket session on background thread and forward messages to UI.

        if self.is_connected:
            self._on_log("Bridge already connected.")
            return

        self._url = url
        self._on_state("Connecting")
        self._app = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
            on_error=self._on_error,
        )

        def run() -> None:
            if self._app is not None:
                self._app.run_forever(ping_interval=20, ping_timeout=10)

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        # Close websocket session and update state explicitly.

        if self._app is not None:
            self._app.close()
        self._on_state("Disconnected")

    def send_text(self, text: str) -> bool:
        # Send raw text command to game server.

        if not self.is_connected:
            self._on_log("Send failed: bridge is not connected.")
            return False

        app = self._app
        if app is None:
            self._on_log("Send failed: websocket app is unavailable.")
            return False

        app.send(text)
        self._on_log(f"-> {text}")
        return True

    def send_json(self, payload: dict) -> bool:
        # Serialize and send JSON payload through websocket.

        return self.send_text(json.dumps(payload, separators=(",", ":")))

    @property
    def is_connected(self) -> bool:
        # Report active websocket readiness.

        return self._app is not None and self._app.sock is not None and self._app.sock.connected

    def _on_open(self, _ws: websocket.WebSocketApp) -> None:
        # Notify UI when websocket handshake is complete.

        self._on_log(f"Bridge connected to {self._url}.")
        self._on_state("Connected")

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        # Parse message if possible and relay for visualizer updates.

        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self._on_log(f"<- {message[:256]}")
            return

        msg_type = payload.get("type")
        if msg_type == "frame":
            self._on_log("<- [frame]")
        else:
            rendered = message if len(message) <= 512 else (message[:512] + "...")
            self._on_log(f"<- {rendered}")
        self._on_json(payload)

    def _on_close(self, _ws: websocket.WebSocketApp, status_code: int, reason: str) -> None:
        # Surface close events with reason for explicit diagnostics.

        self._on_log(f"Bridge closed ({status_code}): {reason}")
        self._on_state("Disconnected")

    def _on_error(self, _ws: websocket.WebSocketApp, error: Exception) -> None:
        # Surface websocket errors directly without suppression.

        self._on_log(f"Bridge error: {error}")
        self._on_state("Error")
