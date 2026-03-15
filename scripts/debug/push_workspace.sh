#!/usr/bin/env bash
# push_workspace.sh
#
# Copies studio.qxw to the Pi as both default.qxw AND autostart.qxw (real files,
# not symlinks — QLC+ 4.14.1 doesn't follow symlinks for autostart).
# Then restarts QLC+ and verifies fixtures loaded.
#
# Usage (from repo root):
#   bash scripts/debug/push_workspace.sh

set -euo pipefail

PI_USER="${PI_USER:-riversway}"
PI_HOST="${PI_HOST:-lights.local}"
QLC_PORT="${QLC_PORT:-9999}"
WORKSPACE="studio.qxw"

if [ ! -f "$WORKSPACE" ]; then
  echo "✗ $WORKSPACE not found. Run from repo root."
  exit 1
fi

echo "=== Pushing workspace to ${PI_USER}@${PI_HOST} ==="
echo ""

# Copy as both default.qxw and autostart.qxw (real copies, not symlinks)
echo "--- Copying workspace files ---"
scp "$WORKSPACE" "${PI_USER}@${PI_HOST}:~/.qlcplus/default.qxw"
scp "$WORKSPACE" "${PI_USER}@${PI_HOST}:~/.qlcplus/autostart.qxw"
echo "✓ Copied as default.qxw and autostart.qxw"

# Verify on Pi
ssh "${PI_USER}@${PI_HOST}" "
  echo ''
  echo '--- Files on Pi ---'
  ls -la ~/.qlcplus/*.qxw
  echo ''
  echo '--- Fixture count in autostart.qxw ---'
  python3 -c \"
import xml.etree.ElementTree as ET
tree = ET.parse('/home/${PI_USER}/.qlcplus/autostart.qxw')
root = tree.getroot()
ns = 'http://www.qlcplus.org/Workspace'
fixtures = list(root.iter('{' + ns + '}Fixture'))
print(f'  {len(fixtures)} fixtures found')
for f in fixtures:
    name = f.find('{' + ns + '}Name')
    addr = f.find('{' + ns + '}Address')
    chs  = f.find('{' + ns + '}Channels')
    print(f'    - {name.text}  addr={addr.text}  ch={chs.text}')
\"
  echo ''
  echo '--- Restarting QLC+ ---'
  sudo systemctl restart qlcplus-web.service
  sleep 6
  systemctl is-active qlcplus-web.service && echo '✓ Service active' || echo '✗ Service failed'
  echo ''
  echo '--- Checking journalctl for workspace load ---'
  journalctl -u qlcplus-web.service -n 20 --no-pager
"

echo ""
echo "--- Probing WebSocket for channel values ---"
python3 - <<PYEOF
import asyncio, sys
try:
    import websockets
except ImportError:
    print("websockets not installed locally, skipping probe")
    sys.exit(0)

async def probe():
    url = "ws://${PI_HOST}:${QLC_PORT}/qlcplusWS"
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            await ws.send("QLC+API|getChannelsValues|1|1|32")
            import time
            deadline = time.time() + 4
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    if "getChannelsValues" in msg:
                        parts = msg.split("|")
                        count = (len(parts) - 3) // 3
                        print(f"✓ Got response with {count} channel entries")
                        if count > 0:
                            print("  Fixtures are loaded!")
                        else:
                            print("  ✗ Zero channels — workspace still not loading")
                        return
                except asyncio.TimeoutError:
                    break
            print("✗ No response from QLC+ WebSocket")
    except Exception as e:
        print(f"✗ WebSocket error: {e}")

asyncio.run(probe())
PYEOF

echo ""
echo "=== Done ==="
echo "Visit: http://${PI_HOST}:${QLC_PORT}/simpleDesk"
