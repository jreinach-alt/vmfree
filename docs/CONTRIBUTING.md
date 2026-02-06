# Contributing to VMFree

Thanks for wanting to help free sysadmins from VMware vendor lock-in. Whether you're fixing a typo, adding support for a new guest OS, or building an entire ESXi direct-import feature, we appreciate it.

## Quick Start

```bash
# Fork and clone
git clone https://github.com/YOUR-USERNAME/vmfree.git
cd vmfree

# Install in dev mode
pip install -e ".[dev]"

# Run tests (should all pass)
pytest tests/ -v

# Lint (should be clean)
ruff check vmfree/ tests/

# Make your changes, then...
pytest tests/ -v       # Tests still pass?
ruff check vmfree/     # Lint still clean?
git commit             # Ship it
```

## Development Workflow

1. **Fork** the repository on GitHub
2. **Create a branch** from `main`: `git checkout -b feature/my-feature`
3. **Make your changes** with tests
4. **Run the test suite**: `pytest tests/ -v`
5. **Run the linter**: `ruff check vmfree/ tests/`
6. **Commit** with a clear message describing what and why
7. **Push** to your fork and **open a PR**

We review PRs promptly. Small, focused PRs are easier to review and merge.

## Code Style

### Python

- **Python 3.10+** - use modern syntax (`X | Y` unions, `match` statements where appropriate)
- **Line length**: 100 characters max (configured in pyproject.toml)
- **Linter**: ruff with `E, F, W, I, N, UP, B, SIM` rules
- **Type hints**: on all public function signatures
- **Docstrings**: on every module, class, and public function (Google style)

### Conventions

- **Subprocess calls**: always accept a `run_command` parameter for testability. Never call `subprocess.run` directly in a way that can't be mocked.
- **File operations**: use `pathlib.Path`, not `os.path`
- **Imports**: sorted by ruff (isort rules enabled)
- **Tests**: use pytest, not unittest. Group related tests in classes.

### Example Function

```python
def convert_disk(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    disk_format: str = "qcow2",
    run_command: object = None,
) -> ConversionResult:
    """Convert a VMDK disk to qcow2 or raw format.

    Args:
        source_path: Path to the VMDK descriptor file.
        output_dir: Directory to write the converted disk into.
        disk_format: Target format - "qcow2" or "raw".
        run_command: Optional callable for subprocess execution.

    Returns:
        ConversionResult with success status and output path.
    """
    runner = run_command or subprocess.run
    # ...
```

## Running Tests

```bash
# Full suite
pytest tests/ -v

# Single file
pytest tests/test_vmx.py -v

# Single test class
pytest tests/test_vmx.py::TestParseVmxSampleUbuntu -v

# With coverage
pytest tests/ --cov=vmfree --cov-report=term-missing
```

The test suite uses:
- **Fixture files** in `tests/fixtures/` (VMX, OVF, VMDK descriptors)
- **Mocked subprocess** calls for all external tools (qemu-img, virt-customize, etc.)
- **tmp_path** for tests that need temporary directories

No test requires real VMware files, qemu-img, or libguestfs to be installed.

## Adding Support for a New Guest OS

Guest OS support mostly lives in the fixup modules. Here's how to add a new one:

1. **Add a test fixture**: Create a VMX file in `tests/fixtures/` that represents the guest
2. **Verify parsing works**: The VMX parser should handle it. If the guest has unusual settings, add handling to `vmfree/parsers/vmx.py`
3. **Check hardware mapping**: Verify `vmfree/mapper/hardware.py` translates the guest's devices correctly
4. **Test guest fixup**: If the guest needs special handling during fixup (unusual package manager, different initramfs tool, non-standard network config), add support to the relevant fixup module
5. **Update the Supported Guests table** in README.md

### Guest Fixup Modules

| Module | What It Does |
|--------|-------------|
| `fixup/vmware_tools.py` | Removes VMware packages (RPM and DEB) |
| `fixup/bootloader.py` | Rebuilds initramfs with virtio modules (dracut, update-initramfs) |
| `fixup/network.py` | Updates NIC names in guest network config (Netplan, ifcfg, networkd, NM) |

## Reporting Migration Compatibility

If you've migrated a real VM using VMFree (or attempted to), we'd love to hear about it. Open an issue with the `guest-compat` label and include:

- **Guest OS** (distro, version, architecture)
- **Source VMware version** (ESXi version, Workstation version)
- **VMware hardware** (SCSI controller type, NIC type, firmware)
- **Target hypervisor** (KVM/libvirt version, Proxmox version)
- **Result**: did it work? If not, what broke?
- **Workarounds**: anything you had to do manually

This data directly improves VMFree for everyone.

## Issue Labels

| Label | Meaning |
|-------|---------|
| `good-first-issue` | Small, well-defined tasks for new contributors |
| `help-wanted` | We need help with this, and it's ready to be worked on |
| `guest-compat` | Migration compatibility report for a specific guest OS |
| `bug` | Something is broken |
| `enhancement` | New feature or improvement |
| `documentation` | Docs improvement needed |
| `windows` | Related to Windows guest migration |

## High-Impact Contribution Areas

### For ex-VMware Engineers

You know things the rest of us don't. Here's where that knowledge is most valuable:

- **ESXi direct import**: Connect to ESXi/vCenter API, export VMs without manual OVA step. This would be a massive UX improvement.
- **NSX translation**: Map NSX network segments to Linux bridges/VLANs/OVS. Complex but hugely valuable for enterprise migrations.
- **vSAN migration**: Handle VMs stored on vSAN clusters. Currently we require the VMDK to be on accessible storage.
- **VDDK integration**: VMware's Virtual Disk Development Kit can do incremental disk transfer. Much faster than full export for large VMs.

### For KVM/Proxmox Experts

- **Live migration**: Import a VM while it's still running on VMware (similar to V2V live migration)
- **Proxmox API integration**: Use the Proxmox REST API instead of `qm` CLI commands
- **Ceph/ZFS storage backends**: Optimize disk import for non-local storage
- **GPU passthrough**: Detect and configure GPU passthrough during migration

### For Everyone

- **Windows virtio driver injection**: Automatically inject virtio drivers into the Windows disk image before first boot, eliminating the manual driver install step
- **More guest OS testing**: Try VMFree with your VMs and report results
- **Better error messages**: When something fails, the error should tell you exactly what to do
- **Performance**: Large disk conversions are slow. Progress reporting, parallel conversion, and incremental transfer would help

## Code of Conduct

Be respectful. We're all here because we believe in open infrastructure. Disagreements about code are fine; personal attacks are not.

## License

By contributing, you agree that your contributions will be licensed under the MIT license.
