# VMFree

[![CI](https://github.com/jreinach-alt/vmfree/actions/workflows/ci.yml/badge.svg)](https://github.com/jreinach-alt/vmfree/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**One-command migration from VMware to KVM/Proxmox.**

You got the Broadcom renewal quote. You've seen the numbers. Now you need a way out.

VMFree takes your VMware virtual machines and migrates them to KVM-based hypervisors. Point it at a VMX file, an OVF export, or an OVA archive. It handles the disk conversion, hardware translation, network config, and guest OS fixup. You get a running VM on the other side.

No manual XML editing. No 47-step wiki guides. One command.

> *Built by sysadmins, for sysadmins, because Broadcom won't stop until we make them irrelevant.*

---

## Terminal Demo

```
$ vmfree migrate ./Ubuntu-Server-22.vmx --target proxmox --vmid 100

  VMFree v0.1.0 - VMware to KVM Migration Tool

  +-- Pre-flight Check ------------------------------------------+
  | ok  VMX parsed: Ubuntu-Server-22 (ubuntu-64, 4 vCPU, 4096 MB)|
  | ok  Disk: Ubuntu-Server-22.vmdk [boot]                       |
  | ok  Firmware: EFI (OVMF)                                     |
  | ok  Controller: pvscsi -> virtio-scsi                         |
  | ok  NIC: vmxnet3 -> virtio-net                                |
  | ok  Network bridge: vmbr0                                     |
  | ok  Proxmox storage: local-lvm                                |
  +--------------------------------------------------------------+

  Converting Ubuntu-Server-22.vmdk...
  ok Converted Ubuntu-Server-22.vmdk -> Ubuntu-Server-22.qcow2

  ok Proxmox commands written to Ubuntu-Server-22-proxmox.sh

  +-- Migration Complete ----------------------------------------+
  | VM Name:     Ubuntu-Server-22                                 |
  | Target:      PROXMOX                                          |
  | Firmware:    EFI (OVMF)                                       |
  | Controller:  virtio-scsi                                      |
  | NIC:         virtio-net                                        |
  | Bridge:      vmbr0                                            |
  | VM ID:       100                                              |
  +--------------------------------------------------------------+
```

*(Rich terminal output with colors. This is the plain-text preview.)*

---

## Quick Start

```bash
# Install
pip install vmfree

# Migrate to Proxmox
vmfree migrate ./MyServer.vmx --target proxmox --vmid 100

# Migrate to bare KVM/libvirt
vmfree migrate ./MyServer.vmx --target kvm --bridge br0

# Inspect a VM without migrating
vmfree inspect ./MyServer.vmx

# Run pre-flight checks
vmfree validate ./MyServer.vmx

# Dry run - see what would happen
vmfree migrate ./MyServer.vmx --target kvm --dry-run
```

## What It Does

VMFree runs a 7-stage pipeline:

1. **Parse** - Reads VMX, OVF, or OVA files and extracts the full VM definition
2. **Convert** - Wraps `qemu-img` to convert VMDK disks to qcow2, handles snapshot chains
3. **Map** - Translates VMware hardware to KVM equivalents (vmxnet3 to virtio, PVSCSI to virtio-scsi)
4. **Generate** - Produces libvirt XML or Proxmox `qm` commands, ready to execute
5. **Fix Guest** - Removes VMware Tools, rebuilds initramfs with virtio modules, fixes NIC names
6. **Network** - Auto-detects host bridges, preserves MAC addresses for DHCP reservations
7. **Launch** - Boots the VM on your new hypervisor

Every stage is independently testable. Every subprocess call is injectable for testing. No magic.

## Supported Guests

| Guest OS | Status | Notes |
|----------|--------|-------|
| Ubuntu 20.04/22.04/24.04 | Tested | EFI + PVSCSI + vmxnet3 |
| Debian 11/12 | Tested | BIOS and EFI |
| CentOS 7/Stream 8/9 | Tested | BIOS + lsilogic |
| Rocky Linux 8/9 | Expected | Same as CentOS |
| RHEL 7/8/9 | Expected | Same as CentOS |
| Fedora 38+ | Expected | Dracut-based initramfs |
| Windows Server 2016/2019/2022 | Tested | Auto-detects, uses IDE+e1000 safe mode |
| Windows 10/11 | Expected | Same safe mode strategy |

**"Tested"** = verified in our test suite with representative fixtures.
**"Expected"** = uses the same code paths as a tested guest, should work without changes.

## Requirements

### Python

- Python 3.10 or later

### System Packages

Install these on the host where you'll run VMFree:

```bash
# Debian/Ubuntu
apt install qemu-utils libguestfs-tools ovmf

# RHEL/CentOS/Rocky
dnf install qemu-img libguestfs-tools-c edk2-ovmf

# For KVM/libvirt target
apt install libvirt-daemon-system virtinst    # Debian/Ubuntu
dnf install libvirt qemu-kvm virt-install     # RHEL family

# For Proxmox target - qemu-img is pre-installed
# Just run VMFree on the Proxmox node itself
```

| Package | Purpose | Required? |
|---------|---------|-----------|
| `qemu-img` | Disk format conversion (VMDK to qcow2) | Yes |
| `libguestfs-tools` | Offline guest OS modification (VMware Tools removal, initramfs rebuild) | For guest fixup |
| `ovmf` | UEFI firmware for EFI guests | For EFI guests |
| `libvirt` | VM management on KVM | For KVM target |

## CLI Reference

### `vmfree migrate`

The main command. Converts and migrates a VMware VM.

```bash
vmfree migrate <SOURCE> --target <kvm|proxmox> [OPTIONS]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--target` | Target hypervisor: `kvm` or `proxmox` | Required |
| `--output` | Output directory for converted files | Source dir |
| `--bridge` | Network bridge name | Auto-detected |
| `--storage` | Proxmox storage target | `local-lvm` |
| `--vmid` | Proxmox VM ID | `100` |
| `--format` | Disk format: `qcow2` or `raw` | `qcow2` |
| `--windows-safe` | Force IDE+e1000 (auto-enabled for Windows) | Off |
| `--preserve-mac` | Keep original MAC addresses | Off |
| `--execute` | Run Proxmox qm commands directly (Proxmox target only) | Off |
| `--dry-run` | Show plan without executing | Off |
| `--no-fixup` | Skip guest OS modifications | Off |
| `-v, --verbose` | Detailed output | Off |

**Examples:**

```bash
# Basic Proxmox migration
vmfree migrate ./WebServer.vmx --target proxmox --vmid 200

# KVM with custom bridge and MAC preservation
vmfree migrate ./DB-Server.vmx --target kvm --bridge br0 --preserve-mac

# Dry run to preview what would happen
vmfree migrate ./AppServer.ovf --target proxmox --dry-run

# Windows VM (safe mode auto-detected, or force it)
vmfree migrate ./WinDC.vmx --target kvm --windows-safe

# From an OVA export
vmfree migrate ./exported-vm.ova --target proxmox --storage ceph-pool

# Execute Proxmox commands directly after conversion
vmfree migrate ./WebServer.vmx --target proxmox --vmid 200 --execute
```

### `vmfree inspect`

Analyze a VM without migrating. Shows hardware details and KVM mapping preview.

```bash
vmfree inspect <SOURCE>
```

```bash
# Inspect a VMX file
vmfree inspect ./MyServer.vmx

# Inspect an OVF export
vmfree inspect ./MyServer.ovf
```

### `vmfree validate`

Run pre-flight checks before migrating.

```bash
vmfree validate <SOURCE> [OPTIONS]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--target` | Target hypervisor context | `kvm` |
| `--output` | Output directory for space check | `.` |

```bash
# Check if everything is ready
vmfree validate ./MyServer.vmx --target proxmox --output /var/lib/vz
```

### `vmfree convert`

Convert a VMDK disk file to qcow2 or raw format.

```bash
vmfree convert <SOURCE> [OPTIONS]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--format` | Output format: `qcow2` or `raw` | `qcow2` |
| `--output` | Output directory | `.` |

### Global Options

```bash
vmfree --version    # Show version
vmfree --help       # Show all commands
```

## How It Handles Windows

Windows VMs are the hardest to migrate. Switching from PVSCSI/vmxnet3 to virtio without driver injection causes a bluescreen (BSOD) on boot.

VMFree's approach:

1. **Auto-detects** Windows guests from the `guestOS` field
2. **Falls back** to IDE + e1000 (safe mode) - these drivers are built into Windows
3. The VM **boots successfully** on KVM without virtio drivers
4. You install virtio drivers **after** the VM is running (from the [virtio ISO](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/))
5. Then switch to virtio for full performance

See [docs/HARDWARE_MAP.md](docs/HARDWARE_MAP.md) for the complete device translation reference.

## Project Structure

```
vmfree/
  cli.py               # CLI entry point (Click + Rich)
  models.py            # VMDefinition, DiskDefinition, NICDefinition
  parsers/
    vmx.py             # VMX key-value parser
    ovf.py             # OVF XML + OVA tar parser
    vmdk.py            # VMDK descriptor inspector
  converter/
    disk.py            # qemu-img wrapper, snapshot chain merge
    validate.py        # Pre/post conversion validation
  mapper/
    hardware.py        # VMware -> KVM device translation
  generators/
    libvirt.py         # Libvirt domain XML generator
    proxmox.py         # Proxmox qm command generator
  network/
    detect.py          # Host bridge auto-detection
  fixup/
    vmware_tools.py    # VMware Tools removal (libguestfs)
    bootloader.py      # Initramfs rebuild + GRUB update
    network.py         # Guest NIC name fixup
```

## Development

```bash
# Clone and install in dev mode
git clone https://github.com/jreinach-alt/vmfree.git
cd vmfree
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check vmfree/ tests/

# Run a specific test file
pytest tests/test_vmx.py -v
```

839 tests. All passing. Every module has comprehensive test coverage.

## Real-World Testing

VMFree has been tested against real VMware artifacts from multiple sources:

**Parser validation** — 10 OVF fixtures from VMware govmomi, OpenStack Nova, ManageIQ, and Canonical cloud-init. Covers OVF v1 and v2 namespaces, VirtualSystemCollection (vCloud vApps), multi-NIC appliances, and EFI/BIOS guests.

**Output validation** — Generated libvirt XML is validated against the official libvirt RelaxNG schema (the same schema used by `virt-xml-validate` and `virsh define`). Proxmox `qm` commands are validated structurally for correct subcommands, required flags, and valid values.

**Live migration** — Successfully migrated a VMware Workstation Ubuntu 24.04 VM (split sparse VMDK, lsilogic controller, e1000 NIC, BIOS firmware) to Proxmox VE 9. The VM booted and ran correctly on the first attempt.

## Contributing

We welcome contributions from everyone. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for the full guide.

Whether you're an ex-VMware engineer who knows where the bodies are buried, a sysadmin fleeing Broadcom pricing, or someone who just believes infrastructure should be open - we'd love your help.

Specific areas where we need contributors:
- ESXi direct import (bypassing the export step)
- Windows virtio driver auto-injection
- vSAN to Ceph migration path
- More guest OS compatibility testing

## License

MIT - because vendor lock-in is the problem, not the solution.
