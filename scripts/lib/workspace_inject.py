#!/usr/bin/env python3
"""
Inject a scene into a QLC+ workspace XML file
"""

import sys
import xml.etree.ElementTree as ET

def inject_scene(workspace_file, scene_xml, output_file, next_id):
    """Inject a scene into the workspace"""
    
    # Parse workspace
    tree = ET.parse(workspace_file)
    root = tree.getroot()
    
    # Parse scene XML
    scene_root = ET.fromstring(scene_xml)
    
    # Set the ID attribute
    scene_root.set('ID', str(next_id))
    
    # Find the Engine element
    # Handle namespace
    ns = {'qlc': 'http://www.qlcplus.org/Workspace'}
    engine = root.find('.//qlc:Engine', ns)
    
    if engine is None:
        # Try without namespace
        engine = root.find('.//Engine')
    
    if engine is None:
        print("Error: Could not find Engine element", file=sys.stderr)
        return False
    
    # Append the scene to Engine
    engine.append(scene_root)
    
    # Write output
    tree.write(output_file, encoding='UTF-8', xml_declaration=True)
    
    return True

if __name__ == '__main__':
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} <workspace_file> <scene_xml> <output_file> <next_id>", file=sys.stderr)
        sys.exit(1)
    
    workspace_file = sys.argv[1]
    scene_xml = sys.argv[2]
    output_file = sys.argv[3]
    next_id = int(sys.argv[4])
    
    if inject_scene(workspace_file, scene_xml, output_file, next_id):
        print(f"Scene injected with ID: {next_id}")
        sys.exit(0)
    else:
        sys.exit(1)
