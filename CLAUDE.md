# VMFree — Project Context for Claude Code

## What This Is

VMFree is a Python CLI tool that migrates VMware virtual machines to KVM-based hypervisors (bare KVM/libvirt, Proxmox VE). One command. Point at VMware files, get a running VM on KVM.

## Architecture

7-stage pipeline:
1. **Input Parser** — Reads VMX, OVF/OVA, or raw VMDK files → extracts VM definition
2. **Disk Converter** — Wraps `qemu-img convert` (VMDK → qcow2), handles snapshot chains
3. **Hardware Mapper** — Translates VMware devices to KVM equivalents (vmxnet3→virtio, PVSCSI→virtio-scsi)
4. **Config Generator** — Produces libvirt XML or Proxmox `qm` commands
5. **Guest OS Fixup** — Uses libguestfs to remove VMware Tools, fix bootloader, inject virtio drivers
6. **Network Config** — Auto-detects host bridge, maps VM NICs
7. **Launch & Verify** — Boots VM, checks console and network

## Tech Stack

- Python 3.10+ with Click (CLI) and Rich (terminal UI)
- qemu-img (disk conversion), libguestfs (guest modification), python-libvirt (VM management)
- pytest for testing, ruff for linting

## Project Structure

```
vmfree/
├── cli.py               # Main CLI entry point
├── models.py            # VMDefinition, DiskDefinition, NICDefinition dataclasses
├── parsers/
│   ├── vmx.py           # VMX key-value parser
│   ├── ovf.py           # OVF XML + OVA tar parser
│   └── vmdk.py          # VMDK descriptor inspector
├── converter/
│   ├── disk.py          # qemu-img wrapper, snapshot merge
│   └── validate.py      # Pre/post conversion validation
├── mapper/
│   └── hardware.py      # VMware → KVM device translation tables
├── generators/
│   ├── libvirt.py       # Libvirt XML domain generator
│   └── proxmox.py       # Proxmox qm command generator
├── network/
│   └── detect.py        # Host bridge detection
├── fixup/
│   ├── vmware_tools.py  # VMware Tools removal
│   ├── bootloader.py    # GRUB/BCD fixup
│   ├── network.py       # Guest NIC name fixup
│   └── virtio_windows.py # Windows virtio driver injection
└── utils/
    ├── subprocess.py    # Wrapper for external commands
    └── logging.py       # Rich-based logging
```

## Implementation Order

Build in this exact order. Each step should be a commit.

### Sprint 1: Foundation
1. Project skeleton — pyproject.toml, directory structure, basic CLI with --help
2. models.py — All dataclasses
3. vmx.py parser — Parse VMX files into VMDefinition. Tests with fixtures.
4. vmdk.py inspector — Read VMDK descriptors, detect type/parent chain. Tests.
5. hardware.py mapper — Translation tables with tests.

### Sprint 2: Disk & Config
6. disk.py converter — Wrap qemu-img, progress reporting
7. libvirt.py generator — Valid libvirt XML from VMDefinition
8. proxmox.py generator — qm commands from VMDefinition
9. detect.py — Network bridge auto-detection
10. Wire up CLI migrate command — Connect pipeline end to end

### Sprint 3: Polish & Guest Fixup
11. ovf.py parser — OVF XML + OVA extraction
12. inspect command — Pretty-print VM analysis
13. validate command — Pre-flight checks
14. vmware_tools.py — Linux VMware Tools removal via libguestfs
15. bootloader.py — Initramfs regeneration for virtio modules
16. network.py fixup — Guest NIC name updates

### Sprint 4: Community Ready
17. README.md — Install instructions, usage examples
18. CONTRIBUTING.md — Contribution guide
19. HARDWARE_MAP.md — VMware → KVM device reference
20. GitHub Actions — pytest + ruff on PR
21. pip packaging
22. Tag v0.1.0

## Critical Gotchas

- **VMDK descriptor vs flat:** `qemu-img` must target the small descriptor `.vmdk`, NOT the `-flat.vmdk`. Both must be in the same directory.
- **Windows BSOD:** Switching from PVSCSI/vmxnet3 to virtio without driver injection causes bluescreen. Default to IDE+e1000 for Windows, let user install virtio drivers later.
- **EFI boot:** VMware EFI VMs need OVMF. Path varies by distro. Detect it.
- **Proxmox ZFS:** Can't just copy qcow2 to ZFS storage. Must use `qm importdisk`.
- **Snapshot chains:** Must merge delta VMDKs before conversion. Chain: current → delta-000002 → delta-000001 → base. Merge in reverse.
- **Guest NIC names:** Moving vmxnet3→virtio changes PCI slot, which changes Linux predictable names (ens192→ens18). Static network configs will break.
- **MAC addresses:** Preserving VMware MACs (00:0c:29:xx / 00:50:56:xx) matters for DHCP reservations. Offer --preserve-mac flag.

## Hardware Translation Tables

```
Controllers: pvscsi→virtio-scsi, lsilogic→virtio-scsi, buslogic→virtio-scsi, nvme→nvme, ahci→virtio-scsi
NICs:        vmxnet3→virtio-net, e1000→e1000 (keep), e1000e→e1000e (keep), vlance→virtio-net
Display:     svga→virtio-vga, vmware→virtio-vga
Firmware:    efi→OVMF, bios→SeaBIOS (default)
```

## Commands

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check vmfree/

# Install in dev mode
pip install -e ".[dev]"

# Example usage
vmfree migrate ./MyServer.vmx --target proxmox --vmid 100
vmfree inspect ./MyServer.vmx
vmfree convert ./MyServer.vmdk --format qcow2
```

## Full Scope Document

See `docs/SCOPE.md` for the complete project scope with detailed implementation notes, architecture diagrams, testing strategy, and community launch plan.
