#!/usr/bin/env bash
# fix_qlc_workspace_autoload.sh
#
# Diagnoses and fixes QLC+ workspace auto-loading on the Pi.
#
# The most reliable way to auto-load a workspace in QLC+ --nogui --web mode
# is via ~/.qlcplus/autostart.qxw. QLC+ checks for this file on startup and
# loads it automatically — no command-line flag needed.
#
# Usage (run on the Pi):
#   bash scripts/debug/fix_qlc_workspace_autoload.sh
#
# Or copy and run remotely:
#   scp scripts/debug/fix_qlc_workspace_autoload.sh riversway@lights.local:/tmp/
#   ssh riversway@lights.local "bash /tmp/fix_qlc_workspace_autoload.sh"

set -euo pipefail

QLCPLUS_DIR="$HOME/.qlcplus"
WORKSPACE="$QLCPLUS_DIR/default.qxw"
AUTOSTART="$QLCPLUS_DIR/autostart.qxw"
SERVICE="qlcplus-web.service"

echo "=== QLC+ Workspace Auto-Load Fix ==="
echo ""

# 1. Check workspace exists
echo "--- Checking workspace ---"
if [ -f "$WORKSPACE" ]; then
    echo "✓ Workspace found: $WORKSPACE"
    echo "  Size: $(wc -c < "$WORKSPACE") bytes"
else
    echo "✗ Workspace NOT found at $WORKSPACE"
    echo "  You need to create or copy your workspace file there first."
    exit 1
fi

# 2. Check / create autostart.qxw symlink
echo ""
echo "--- Setting up autostart.qxw ---"
if [ -L "$AUTOSTART" ]; then
    TARGET=$(readlink "$AUTOSTART")
    echo "✓ autostart.qxw already exists as symlink → $TARGET"
    if [ "$TARGET" != "$WORKSPACE" ] && [ "$TARGET" != "default.qxw" ]; then
        echo "  Updating symlink to point to $WORKSPACE"
        ln -sf "$WORKSPACE" "$AUTOSTART"
        echo "  ✓ Updated"
    fi
elif [ -f "$AUTOSTART" ]; then
    echo "  autostart.qxw exists as a regular file."
    echo "  Replacing with symlink to $WORKSPACE"
    rm "$AUTOSTART"
    ln -sf "$WORKSPACE" "$AUTOSTART"
    echo "  ✓ Replaced with symlink"
else
    echo "  Creating symlink: autostart.qxw → $WORKSPACE"
    ln -sf "$WORKSPACE" "$AUTOSTART"
    echo "  ✓ Created"
fi

ls -la "$AUTOSTART"

# 3. Check systemd service ExecStart
echo ""
echo "--- Checking systemd service ---"
SERVICE_FILE="/etc/systemd/system/$SERVICE"
if [ -f "$SERVICE_FILE" ]; then
    EXEC_LINE=$(grep "ExecStart" "$SERVICE_FILE" || true)
    echo "  ExecStart: $EXEC_LINE"

    # Check if --open flag is present (belt-and-suspenders alongside autostart.qxw)
    if echo "$EXEC_LINE" | grep -q -- "--open\|-o "; then
        echo "  ✓ --open flag present in service"
    else
        echo "  ℹ --open flag not in service (autostart.qxw will handle loading)"
    fi
else
    echo "  Service file not found at $SERVICE_FILE"
    echo "  Checking via systemctl..."
    systemctl cat "$SERVICE" 2>/dev/null | grep ExecStart || echo "  (could not read service)"
fi

# 4. Restart service
echo ""
echo "--- Restarting QLC+ service ---"
sudo systemctl restart "$SERVICE"
echo "  Waiting 5 seconds for QLC+ to start..."
sleep 5

# 5. Check service status
echo ""
echo "--- Service status ---"
systemctl is-active "$SERVICE" && echo "✓ Service is active" || echo "✗ Service is NOT active"
systemctl status "$SERVICE" --no-pager -l | tail -20

# 6. Probe WebSocket to verify workspace loaded
echo ""
echo "--- Probing WebSocket for channel values ---"
python3 - <<'PYEOF'
import asyncio, sys
try:
    import websockets
except ImportError:
    print("websockets not installed, skipping probe")
    sys.exit(0)

async def probe():
    try:
        async with websockets.connect("ws://localhost:9999/qlcplusWS", open_timeout=5) as ws:
            await ws.send("QLC+API|getChannelsValues|1|1|32")
            import time
            deadline = time.time() + 3
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    if "getChannelsValues" in msg:
                        parts = msg.split("|")
                        non_zero = []
                        i = 3
                        while i + 1 < len(parts):
                            try:
                                ch = int(parts[i])
                                val = int(parts[i+1])
                                if val > 0:
                                    non_zero.append(f"ch{ch}={val}")
                            except ValueError:
                                pass
                            i += 3
                        if non_zero:
                            print(f"✓ Non-zero channels: {', '.join(non_zero)}")
                        else:
                            print("ℹ All channels are zero (no active scene — this is normal at boot)")
                        # Check if we got fixture data at all
                        print(f"  Got {(len(parts)-3)//3} channel entries in response")
                        return
                except asyncio.TimeoutError:
                    break
            print("✗ No getChannelsValues response received")
    except Exception as e:
        print(f"✗ WebSocket error: {e}")

asyncio.run(probe())
PYEOF

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "  1. Visit http://lights.local:9999/ — you should see your Virtual Console widgets"
echo "  2. Visit http://lights.local:9999/simpleDesk — fixtures should appear"
echo "  3. If still blank, check: journalctl -u $SERVICE -n 50"
