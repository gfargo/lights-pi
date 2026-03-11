#!/usr/bin/env python3
"""
Unit tests for fixture_groups_sync.py
"""

import unittest
import tempfile
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))

from fixture_groups_sync import import_from_workspace, export_to_workspace


class TestFixtureGroupsSync(unittest.TestCase):
    
    def setUp(self):
        """Create test workspace and groups file"""
        self.test_workspace = tempfile.NamedTemporaryFile(mode='w', suffix='.qxw', delete=False)
        self.test_workspace.write('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Workspace>
<Workspace xmlns="http://www.qlcplus.org/Workspace" CurrentWindow="VC">
  <Engine>
    <FixtureGroup ID="0">
      <Name>Test Group</Name>
      <Size X="2" Y="1"/>
      <Head X="0" Y="0" Fixture="0">0</Head>
      <Head X="1" Y="0" Fixture="1">0</Head>
    </FixtureGroup>
  </Engine>
</Workspace>''')
        self.test_workspace.close()
        
        self.test_groups = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        self.test_groups.write('{"groups":{}}')
        self.test_groups.close()
    
    def tearDown(self):
        """Clean up test files"""
        Path(self.test_workspace.name).unlink(missing_ok=True)
        Path(self.test_groups.name).unlink(missing_ok=True)
    
    def test_import_from_workspace(self):
        """Test importing groups from workspace"""
        count = import_from_workspace(self.test_workspace.name, self.test_groups.name)
        
        self.assertEqual(count, 1)
        
        # Verify JSON file
        with open(self.test_groups.name, 'r') as f:
            data = json.load(f)
        
        self.assertIn("Test Group", data["groups"])
        self.assertEqual(data["groups"]["Test Group"]["fixtures"], ["0", "1"])
    
    def test_export_to_workspace(self):
        """Test exporting groups to workspace"""
        # Create groups JSON
        groups_data = {
            "groups": {
                "My Group": {
                    "fixtures": ["2", "3"],
                    "description": "Test group"
                }
            }
        }
        
        with open(self.test_groups.name, 'w') as f:
            json.dump(groups_data, f)
        
        output_file = tempfile.NamedTemporaryFile(mode='w', suffix='.qxw', delete=False)
        output_file.close()
        
        try:
            count = export_to_workspace(self.test_groups.name, self.test_workspace.name, output_file.name)
            
            self.assertEqual(count, 1)
            
            # Verify XML file
            import xml.etree.ElementTree as ET
            tree = ET.parse(output_file.name)
            root = tree.getroot()
            
            groups = root.findall('.//{http://www.qlcplus.org/Workspace}FixtureGroup')
            self.assertEqual(len(groups), 1)
            
            name_elem = groups[0].find('{http://www.qlcplus.org/Workspace}Name')
            self.assertEqual(name_elem.text, "My Group")
            
        finally:
            Path(output_file.name).unlink(missing_ok=True)
    
    def test_import_empty_workspace(self):
        """Test importing from workspace with no groups"""
        empty_workspace = tempfile.NamedTemporaryFile(mode='w', suffix='.qxw', delete=False)
        empty_workspace.write('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Workspace>
<Workspace xmlns="http://www.qlcplus.org/Workspace">
  <Engine></Engine>
</Workspace>''')
        empty_workspace.close()
        
        try:
            count = import_from_workspace(empty_workspace.name, self.test_groups.name)
            self.assertEqual(count, 0)
        finally:
            Path(empty_workspace.name).unlink(missing_ok=True)
    
    def test_export_empty_groups(self):
        """Test exporting when no groups defined"""
        output_file = tempfile.NamedTemporaryFile(mode='w', suffix='.qxw', delete=False)
        output_file.close()
        
        try:
            count = export_to_workspace(self.test_groups.name, self.test_workspace.name, output_file.name)
            self.assertEqual(count, 0)
        finally:
            Path(output_file.name).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
