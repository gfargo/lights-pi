# Contributing to Raspberry Pi Studio Lighting Controller

Thank you for your interest in contributing! This project aims to make DMX lighting control accessible and reliable for studio environments.

## 🤝 How to Contribute

### Reporting Issues

Before creating an issue, please:

1. **Search existing issues** to avoid duplicates
2. **Run diagnostics** to gather relevant information:
   ```bash
   ./lightsctl.sh doctor
   ./lightsctl.sh diagnose
   ```
3. **Include system information**:
   - Raspberry Pi model
   - OS version (`./lightsctl.sh os-version`)
   - QLC+ version
   - ENTTEC USB Pro model

### Suggesting Features

We welcome feature suggestions! Please:

- Check the [Future Enhancements](README.md#-future-enhancements) section first
- Describe the use case and expected behavior
- Consider backward compatibility
- Explain how it benefits the broader community

### Submitting Pull Requests

1. **Fork the repository** and create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Follow existing conventions**:
   - Use bash best practices (`set -euo pipefail`)
   - Add functions to `lightsctl.sh` following the existing pattern
   - Update the Makefile with shortcuts
   - Keep functions focused and single-purpose

3. **Test your changes**:
   ```bash
   # Validate syntax
   shellcheck lightsctl.sh scripts/*.sh
   
   # Test on actual hardware if possible
   ./lightsctl.sh validate
   ./lightsctl.sh doctor
   ```

4. **Update documentation**:
   - Add command to README.md command reference
   - Update usage text in `lightsctl.sh`
   - Add workflow examples if applicable

5. **Commit with clear messages**:
   ```bash
   git commit -m "feat: add new-command for specific purpose
   
   - Detailed description of what it does
   - Why it's useful
   - Any breaking changes or requirements"
   ```

6. **Submit the PR** with:
   - Clear description of changes
   - Screenshots/output examples if relevant
   - Reference to related issues

## 📝 Coding Standards

### Bash Scripts

- Use `#!/usr/bin/env bash` shebang
- Enable strict mode: `set -euo pipefail`
- Quote variables: `"${VARIABLE}"`
- Use `local` for function variables
- Provide helpful error messages
- Use `run` and `run_sudo` helpers for remote commands

### Function Naming

- Commands: `command_<name>` (e.g., `command_health`)
- Helpers: descriptive names (e.g., `load_env_file`)
- Keep functions under 50 lines when possible

### Documentation

- Add inline comments for complex logic
- Update help text in `usage()` function
- Keep README examples practical and tested
- Use collapsible sections for detailed info

## 🧪 Testing

### Manual Testing Checklist

Before submitting, verify:

- [ ] Command works on fresh Pi installation
- [ ] Command works on existing installation
- [ ] Error handling works (test failure cases)
- [ ] Help text is clear and accurate
- [ ] Makefile shortcut works
- [ ] No unintended side effects

### Test Environment

Recommended test setup:
- Raspberry Pi 3B+ or newer
- Fresh Raspberry Pi OS Lite installation
- ENTTEC DMX USB Pro (for DMX-related features)

## 🎯 Priority Areas

We especially welcome contributions in:

- **Performance improvements** - Faster operations, reduced resource usage
- **Error handling** - Better error messages and recovery
- **Hardware compatibility** - Support for additional DMX interfaces
- **Documentation** - Clearer examples, troubleshooting guides
- **Testing** - Automated tests, validation scripts
- **Security** - Hardening, vulnerability fixes

## 🚫 What We Don't Accept

- Breaking changes without discussion
- Features that require GUI on the Pi
- Proprietary or closed-source dependencies
- Changes that significantly increase complexity
- Untested code

## 💬 Communication

- **Issues**: For bugs, feature requests, questions
- **Discussions**: For general questions and ideas
- **Pull Requests**: For code contributions

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.

## 🙏 Recognition

Contributors will be acknowledged in release notes and the project README. Thank you for helping make this project better!

---

## Quick Reference

### Adding a New Command

1. Add function to `lightsctl.sh`:
   ```bash
   function command_my_feature() {
     echo "=== My Feature ==="
     # Implementation
   }
   ```

2. Add to case statement:
   ```bash
   my-feature) command_my_feature ;;
   ```

3. Add to usage text:
   ```bash
   my-feature                    description of what it does
   ```

4. Add Makefile target:
   ```makefile
   my-feature:
   	$(CTL) my-feature
   ```

5. Update README.md command reference

6. Test thoroughly and submit PR!
