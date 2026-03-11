#!/usr/bin/env python3
"""
Fixture Groups QLC+ Workspace Sync
Bidirectional sync between JSON groups and QLC+ workspace XML
"""

import sys
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def import_from_workspace(workspace_file, groups_file):
    """
    Import fixture groups from QLC+ workspace XML to JSON
    
    Args:
        workspace_file: Path to QLC+ workspace XML
        groups_file: Path to groups JSON file
    
    Returns:
        int: Number of groups imported
    """
    
    try:
        # Parse workspace XML
        tree = ET.parse(workspace_file)
        root = tree.getroot()
    except FileNotFoundError:
        print(f"Error: Workspace file not found: {workspace_file}", file=sys.stderr)
        return 0
    except ET.ParseError as e:
        print(f"Error: Invalid workspace XML: {e}", file=sys.stderr)
        return 0
    
    # Find all FixtureGroup elements (handle namespace)
    groups = {}
    
    for fg in root.findall('.//{*}FixtureGroup'):
        group_id = fg.get('ID')
        name_elem = fg.find('{*}Name')
        
        if name_elem is None or name_elem.text is None:
            continue
        
        name = name_elem.text.strip()
        
        # Extract fixture IDs from Head elements
        fixtures = []
        for head in fg.findall('{*}Head'):
            fixture_id = head.get('Fixture')
            if fixture_id:
                fixtures.append(fixture_id)
        
        # Store group
        groups[name] = {
            "fixtures": fixtures,
            "description": f"Imported from QLC+ (ID: {group_id})",
            "qlc_id": group_id
        }
    
    # Load existing groups file or create new
    try:
        if Path(groups_file).exists():
            with open(groups_file, 'r') as f:
                data = json.load(f)
        else:
            data = {"groups": {}}
    except json.JSONDecodeError as e:
        print(f"Warning: Invalid groups file, creating new: {e}", file=sys.stderr)
        data = {"groups": {}}
    except Exception as e:
        print(f"Error reading groups file: {e}", file=sys.stderr)
        return 0
    
    # Merge imported groups (preserve existing if not in workspace)
    for name, group_data in groups.items():
        data["groups"][name] = group_data
    
    # Save updated groups
    try:
        with open(groups_file, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error writing groups file: {e}", file=sys.stderr)
        return 0
    
    print(f"✓ Imported {len(groups)} group(s) from workspace")
    return len(groups)


def export_to_workspace(groups_file, workspace_file, output_file):
    """
    Export fixture groups from JSON to QLC+ workspace XML
    
    Args:
        groups_file: Path to groups JSON file
        workspace_file: Path to input workspace XML
        output_file: Path to output workspace XML
    
    Returns:
        int: Number of groups exported
    """
    
    # Load groups
    try:
        with open(groups_file, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Groups file not found: {groups_file}", file=sys.stderr)
        return 0
    except json.JSONDecodeError as e:
        print(f"Error: Invalid groups JSON: {e}", file=sys.stderr)
        return 0
    
    groups = data.get("groups", {})
    
    if not groups:
        print("No groups to export")
        return 0
    
    # Register namespace
    ET.register_namespace('', 'http://www.qlcplus.org/Workspace')
    
    # Parse workspace XML
    try:
        tree = ET.parse(workspace_file)
        root = tree.getroot()
    except FileNotFoundError:
        print(f"Error: Workspace file not found: {workspace_file}", file=sys.stderr)
        return 0
    except ET.ParseError as e:
        print(f"Error: Invalid workspace XML: {e}", file=sys.stderr)
        return 0
    
    # Remove existing FixtureGroup elements
    engine = root.find('.//{http://www.qlcplus.org/Workspace}Engine')
    if engine is not None:
        for fg in list(engine.findall('{http://www.qlcplus.org/Workspace}FixtureGroup')):
            engine.remove(fg)
    
    if engine is None:
        print("Error: Engine element not found in workspace")
        return 0
    
    # Add new FixtureGroup elements
    group_id = 0
    for name, group_data in groups.items():
        fixtures = group_data.get("fixtures", [])
        
        if not fixtures:
            continue
        
        # Create FixtureGroup element
        fg = ET.SubElement(engine, '{http://www.qlcplus.org/Workspace}FixtureGroup', ID=str(group_id))
        
        # Add Name
        name_elem = ET.SubElement(fg, '{http://www.qlcplus.org/Workspace}Name')
        name_elem.text = name
        
        # Add Size (arrange fixtures in a row)
        size_elem = ET.SubElement(fg, '{http://www.qlcplus.org/Workspace}Size', X=str(len(fixtures)), Y="1")
        
        # Add Head elements for each fixture
        for idx, fixture_id in enumerate(fixtures):
            head_elem = ET.SubElement(
                fg, '{http://www.qlcplus.org/Workspace}Head',
                X=str(idx),
                Y="0",
                Fixture=str(fixture_id)
            )
            head_elem.text = "0"  # Head index (usually 0 for single-head fixtures)
        
        group_id += 1
    
    # Write output
    try:
        tree.write(output_file, encoding='utf-8', xml_declaration=True)
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        return 0
    
    print(f"✓ Exported {len(groups)} group(s) to workspace")
    return len(groups)


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Import: fixture_groups_sync.py import <workspace.qxw> <groups.json>")
        print("  Export: fixture_groups_sync.py export <groups.json> <workspace.qxw> <output.qxw>")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "import":
        if len(sys.argv) != 4:
            print("Usage: fixture_groups_sync.py import <workspace.qxw> <groups.json>")
            sys.exit(1)
        
        workspace_file = sys.argv[2]
        groups_file = sys.argv[3]
        
        import_from_workspace(workspace_file, groups_file)
    
    elif command == "export":
        if len(sys.argv) != 5:
            print("Usage: fixture_groups_sync.py export <groups.json> <workspace.qxw> <output.qxw>")
            sys.exit(1)
        
        groups_file = sys.argv[2]
        workspace_file = sys.argv[3]
        output_file = sys.argv[4]
        
        export_to_workspace(groups_file, workspace_file, output_file)
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
