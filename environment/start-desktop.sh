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
    echo '[桌面] 此桌面配置需要 root 权限。' >&2
    exit 1
fi
mkdir -p "$XDG_RUNTIME_DIR" /root/Desktop
chmod 700 "$XDG_RUNTIME_DIR"
touch "$XAUTHORITY"

Xvfb "$DISPLAY" -screen 0 1280x800x24 -ac -nolisten tcp &
pids+=("$!")

# Xfce 和 VNC 必须等待共用的 X 服务器开始接受连接。
for attempt in {1..100}; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "${pids[0]}" 2>/dev/null; then
        echo '[桌面] Xvfb 在就绪前退出。' >&2
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

# 保留任务已有命令，包括应用或服务的启动命令。
"$@" &
pids+=("$!")

echo '[桌面] 正在容器端口 6080 上启动 Xfce/noVNC。'
if wait -n "${pids[@]}"; then
    status=0
else
    status=$?
fi
echo "[桌面] 某个桌面组件或任务命令已退出（状态=$status）。" >&2
exit "$status"
