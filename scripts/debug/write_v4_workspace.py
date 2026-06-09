#!/usr/bin/env python3
"""
Generate a QLC+ 4.14.x compatible workspace from the fixtures
previously extracted from the v5.2.0 default.qxw.

Run on the Pi:
  python3 write_v4_workspace.py
"""

WORKSPACE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Workspace>
<Workspace xmlns="http://www.qlcplus.org/Workspace" CurrentWindow="VirtualConsole">
 <Creator>
  <Name>Q Light Controller Plus</Name>
  <Version>4.14.1</Version>
  <Author>Griffen Fargo</Author>
 </Creator>
 <Engine>
  <InputOutputMap>
   <Universe Name="Universe 1" ID="0">
    <Output Plugin="DMX USB" UID="FT232R" Line="0"/>
   </Universe>
   <Universe Name="Universe 2" ID="1"/>
   <Universe Name="Universe 3" ID="2"/>
   <Universe Name="Universe 4" ID="3"/>
  </InputOutputMap>
  <Fixture>
   <Manufacturer>Chauvet</Manufacturer>
   <Model>SlimPAR Pro H USB</Model>
   <Mode>7 Channel</Mode>
   <ID>0</ID>
   <Name>SlimPAR Pro H USB [1]</Name>
   <Universe>0</Universe>
   <Address>0</Address>
   <Channels>7</Channels>
  </Fixture>
  <Fixture>
   <Manufacturer>Chauvet</Manufacturer>
   <Model>SlimPAR Pro W USB</Model>
   <Mode>9 Channel</Mode>
   <ID>1</ID>
   <Name>SlimPAR Pro W USB [2]</Name>
   <Universe>0</Universe>
   <Address>20</Address>
   <Channels>9</Channels>
  </Fixture>
  <Fixture>
   <Manufacturer>Chauvet</Manufacturer>
   <Model>SlimPAR 56</Model>
   <Mode>3-Ch</Mode>
   <ID>3</ID>
   <Name>SlimPAR 56 [4]</Name>
   <Universe>0</Universe>
   <Address>7</Address>
   <Channels>3</Channels>
  </Fixture>
  <Fixture>
   <Manufacturer>Chauvet</Manufacturer>
   <Model>SlimPAR 56</Model>
   <Mode>3-Ch</Mode>
   <ID>4</ID>
   <Name>SlimPAR 56 [5]</Name>
   <Universe>0</Universe>
   <Address>10</Address>
   <Channels>3</Channels>
  </Fixture>
  <Fixture>
   <Manufacturer>Chauvet</Manufacturer>
   <Model>SlimPAR Pro H USB</Model>
   <Mode>7 Channel</Mode>
   <ID>5</ID>
   <Name>SlimPAR Pro H USB [6]</Name>
   <Universe>0</Universe>
   <Address>13</Address>
   <Channels>7</Channels>
  </Fixture>
 </Engine>
 <VirtualConsole>
  <Frame Caption="">
   <Appearance>
    <FrameStyle>None</FrameStyle>
    <ForegroundColor>Default</ForegroundColor>
    <BackgroundColor>Default</BackgroundColor>
    <BackgroundImage>None</BackgroundImage>
    <Font>Default</Font>
   </Appearance>
  </Frame>
  <Properties>
   <Size Width="1920" Height="1080"/>
   <GrandMaster ChannelMode="Intensity" ValueMode="Reduce" SliderMode="Normal"/>
  </Properties>
 </VirtualConsole>
 <SimpleDesk>
  <Engine/>
 </SimpleDesk>
</Workspace>
"""

import os

qlcdir = os.path.expanduser("~/.qlcplus")
os.makedirs(qlcdir, exist_ok=True)

for fname in ("default.qxw", "autostart.qxw"):
    path = os.path.join(qlcdir, fname)
    with open(path, "w") as f:
        f.write(WORKSPACE)
    print(f"Wrote {path} ({len(WORKSPACE)} bytes)")

print("Done. Restart QLC+: sudo systemctl restart qlcplus-web.service")
