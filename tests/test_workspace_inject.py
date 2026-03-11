#!/usr/bin/env python3
"""
Unit tests for workspace_inject.py
"""

import unittest
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "lib"))

from workspace_inject import inject_scene


class TestWorkspaceInject(unittest.TestCase):
    
    def setUp(self):
        """Create a minimal test workspace"""
        self.test_workspace = tempfile.NamedTemporaryFile(mode='w', suffix='.qxw', delete=False)
        self.test_workspace.write('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Workspace>
<Workspace xmlns="http://www.qlcplus.org/Workspace" CurrentWindow="VC">
  <Engine>
    <Function ID="0" Type="Scene" Name="Existing Scene">
      <Speed FadeIn="0" FadeOut="0" Duration="0"/>
    </Function>
  </Engine>
</Workspace>''')
        self.test_workspace.close()
        
        self.test_scene = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Function>
<Function Type="Scene" Name="Test Scene">
  <Speed FadeIn="500" FadeOut="500" Duration="0"/>
  <FixtureVal ID="0">1,255,2,255,3,255</FixtureVal>
</Function>'''
    
    def tearDown(self):
        """Clean up test files"""
        Path(self.test_workspace.name).unlink(missing_ok=True)
    
    def test_inject_scene_success(self):
        """Test successful scene injection"""
        output_file = tempfile.NamedTemporaryFile(mode='w', suffix='.qxw', delete=False)
        output_file.close()
        
        try:
            result = inject_scene(self.test_workspace.name, self.test_scene, output_file.name, 1)
            self.assertTrue(result)
            
            # Verify output file exists and is valid XML
            tree = ET.parse(output_file.name)
            root = tree.getroot()
            
            # Check that we have 2 functions now
            functions = root.findall('.//{http://www.qlcplus.org/Workspace}Function')
            self.assertEqual(len(functions), 2)
            
            # Check new function name
            new_function = functions[1]
            name_elem = new_function.find('{http://www.qlcplus.org/Workspace}Name')
            self.assertEqual(name_elem.text, "Test Scene")
            
        finally:
            Path(output_file.name).unlink(missing_ok=True)
    
    def test_inject_scene_invalid_workspace(self):
        """Test with invalid workspace file"""
        result = inject_scene("/nonexistent/file.qxw", self.test_scene, "/tmp/output.qxw", 1)
        self.assertFalse(result)
    
    def test_inject_scene_invalid_xml(self):
        """Test with invalid scene XML"""
        output_file = tempfile.NamedTemporaryFile(mode='w', suffix='.qxw', delete=False)
        output_file.close()
        
        try:
            invalid_scene = "not valid xml"
            result = inject_scene(self.test_workspace.name, invalid_scene, output_file.name, 1)
            self.assertFalse(result)
        finally:
            Path(output_file.name).unlink(missing_ok=True)
    
    def test_inject_scene_assigns_new_id(self):
        """Test that new scene gets assigned ID"""
        output_file = tempfile.NamedTemporaryFile(mode='w', suffix='.qxw', delete=False)
        output_file.close()
        
        try:
            inject_scene(self.test_workspace.name, self.test_scene, output_file.name, 42)
            
            tree = ET.parse(output_file.name)
            root = tree.getroot()
            
            # Get all function IDs
            functions = root.findall('.//{http://www.qlcplus.org/Workspace}Function')
            ids = [int(f.get('ID')) for f in functions]
            
            # Check new ID is 42
            self.assertIn(42, ids)
            
        finally:
            Path(output_file.name).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
