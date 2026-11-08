#!/usr/bin/env python3
"""
Inject a scene into a QLC+ workspace XML file
"""

import sys
import xml.etree.ElementTree as ET


def _local(tag):
    return tag.split('}')[-1]


def fixture_channel_counts(root):
    """Map fixture ID -> channel count, read from the workspace's <Fixture> elements."""
    counts = {}
    for el in root.iter():
        if _local(el.tag) != 'Fixture':
            continue
        vals = {_local(c.tag): c.text for c in el}
        if vals.get('ID') is not None and vals.get('Channels'):
            counts[vals['ID']] = int(vals['Channels'])
    return counts


def normalize_fixture_vals(scene_root, counts):
    """Rewrite <FixtureVal> channels to QLC+'s native 0-based form.

    Generators historically wrote 1-based channels, which QLC+ misreads (every value lands one
    channel high and the last falls off the end). Same rule as the control server's
    _decode_fixture_val_pairs: a 0 proves 0-based, the fixture's channel count proves 1-based,
    anything else is ambiguous and left alone. Returns how many FixtureVals changed.
    """
    changed = 0
    for fv in scene_root:
        if _local(fv.tag) != 'FixtureVal' or not fv.text:
            continue
        parts = [x.strip() for x in fv.text.split(',') if x.strip() != '']
        try:
            pairs = [(int(parts[i]), int(parts[i + 1])) for i in range(0, len(parts) - 1, 2)]
        except ValueError:
            continue
        n = counts.get(fv.get('ID'))
        if not pairs or not n:
            continue
        chans = [c for c, _ in pairs]
        if 0 not in chans and n in chans:
            fv.text = ','.join(f'{c - 1},{v}' for c, v in pairs)
            changed += 1
    return changed


def inject_scene(workspace_file, scene_xml, output_file, next_id):
    """
    Inject a scene into the workspace
    
    Args:
        workspace_file: Path to input workspace XML
        scene_xml: Scene XML string to inject
        output_file: Path to output workspace XML
        next_id: ID to assign to the new scene
    
    Returns:
        bool: True if successful, False otherwise
    """
    
    try:
        # Parse workspace
        tree = ET.parse(workspace_file)
        root = tree.getroot()
    except FileNotFoundError:
        print(f"Error: Workspace file not found: {workspace_file}", file=sys.stderr)
        return False
    except ET.ParseError as e:
        print(f"Error: Invalid workspace XML: {e}", file=sys.stderr)
        return False
    
    try:
        # Parse scene XML
        scene_root = ET.fromstring(scene_xml)
    except ET.ParseError as e:
        print(f"Error: Invalid scene XML: {e}", file=sys.stderr)
        return False
    
    # Set the ID attribute
    scene_root.set('ID', str(next_id))

    # Store channels 0-based whatever the generator wrote
    normalize_fixture_vals(scene_root, fixture_channel_counts(root))
    
    # Register namespace to preserve it in output
    ET.register_namespace('', 'http://www.qlcplus.org/Workspace')
    
    # Find the Engine element
    engine = root.find('.//{http://www.qlcplus.org/Workspace}Engine')
    
    if engine is None:
        # Try without namespace
        engine = root.find('.//Engine')
    
    if engine is None:
        print("Error: Could not find Engine element", file=sys.stderr)
        return False
    
    # Convert scene to use QLC+ namespace
    # Create new element with namespace
    ns_scene = ET.Element('{http://www.qlcplus.org/Workspace}Function')
    ns_scene.set('ID', str(next_id))
    ns_scene.set('Type', scene_root.get('Type', 'Scene'))
    ns_scene.set('Name', scene_root.get('Name', 'Unnamed'))
    if scene_root.get('Path'):
        ns_scene.set('Path', scene_root.get('Path'))
    
    # Copy all child elements
    for child in scene_root:
        ns_child = ET.Element(f'{{http://www.qlcplus.org/Workspace}}{child.tag}')
        ns_child.text = child.text
        for key, value in child.attrib.items():
            ns_child.set(key, value)
        ns_scene.append(ns_child)
    
    # Append to Engine
    engine.append(ns_scene)
    
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
