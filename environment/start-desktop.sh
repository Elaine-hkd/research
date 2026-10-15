#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--healthcheck" ]]; then
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1
    xprop -display "$DISPLAY" -root _NET_SUPPORTING_WM_CHECK | grep -q 'window id'
    python3 -c 'import socket, urllib.request; socket.create_connection(("127.0.0.1", 5900), 1).close(); urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:6080/vnc.html", timeout=2).close()'
    exit 0
fi

pids=()
cleanup() {
    if ((${#pids[@]})); then
        kill "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT

if [[ "$(id -u)" != 0 ]]; then
    echo '[desktop] This desktop configuration requires root.' >&2
    exit 1
fi
mkdir -p "$XDG_RUNTIME_DIR" /root/Desktop
chmod 700 "$XDG_RUNTIME_DIR"
touch "$XAUTHORITY"

Xvfb "$DISPLAY" -screen 0 1280x800x24 -ac -nolisten tcp &
pids+=("$!")

# Xfce and VNC must wait until their shared X server accepts connections.
for attempt in {1..100}; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "${pids[0]}" 2>/dev/null; then
        echo '[desktop] Xvfb exited before becoming ready.' >&2
        exit 1
    fi
    sleep 0.1
done
xdpyinfo -display "$DISPLAY" >/dev/null

dbus-run-session -- xfce4-session &
pids+=("$!")

x11vnc -display "$DISPLAY" -rfbport 5900 -localhost -forever -shared -nopw &
pids+=("$!")

websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 &
pids+=("$!")

# Keep a task's existing command, including any application/server startup.
"$@" &
pids+=("$!")

echo '[desktop] Xfce/noVNC starting on container port 6080.'
if wait -n "${pids[@]}"; then
    status=0
else
    status=$?
fi
echo "[desktop] A desktop component or the task command exited (status=$status)." >&2
exit "$status"
