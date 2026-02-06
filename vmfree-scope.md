# VMFree — Open Source VMware-to-KVM Migration Tool

## Project Scope & Implementation Guide

**Author:** Justin (Architecture & Design)
**Implementer:** Claude Code
**Date:** February 6, 2026
**License:** MIT
**Repository:** To be created on GitHub

---

## 1. Executive Summary

VMFree is a CLI tool that takes VMware virtual machine files (VMX, VMDK, OVA/OVF) and migrates them to run on KVM-based hypervisors (bare KVM/libvirt, Proxmox VE) with working network connectivity. One command. No PhD required.

The Broadcom acquisition of VMware has displaced 2,800+ engineers, enraged 74% of IT leaders (per Gartner), and created massive demand for migration tooling. Existing tools (virt-v2v, MigrateKit, Proxmox import wizard) each solve pieces of the problem but nothing provides a clean, end-to-end "give me your VMware files and I'll figure out the rest" experience.

**This project fills that gap.**

### Why This Matters Right Now

- Broadcom price hikes: 150% to 1,000% increases reported across the industry
- 74% of IT leaders actively exploring VMware alternatives (Gartner)
- Proxmox VE 9's import wizard only works with ESXi — not standalone VMDK files
- virt-v2v is powerful but synchronous, CLI-intimidating, and requires manual config
- MigrateKit targets OpenStack only, not bare KVM or Proxmox
- Thousands of displaced VMware engineers looking for open source projects to contribute to

---

## 2. Target Users

1. **Sysadmins migrating from VMware to Proxmox/KVM** — The primary audience. They have VMDK files sitting on a NAS and want them running on their new hypervisor.
2. **Homelab enthusiasts** — Running VMware Workstation/Fusion, want to move to Proxmox or bare KVM.
3. **MSPs and consultants** — Doing batch migrations for clients fleeing Broadcom licensing.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    VMFree CLI                         │
│                                                       │
│  vmfree migrate <path-to-vm> --target <kvm|proxmox>  │
└───────────┬───────────────────────────────────────────┘
            │
            ▼
┌───────────────────────┐
│   1. INPUT PARSER     │  Reads VMX, OVF, or raw VMDK
│   - VMX Parser        │  Extracts: CPU, RAM, disks,
│   - OVF/OVA Parser    │  NICs, boot type, controllers
│   - VMDK Inspector    │
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│   2. DISK CONVERTER   │  Wraps qemu-img convert
│   - VMDK → qcow2/raw │  Handles descriptor + flat files
│   - Snapshot merger   │  Merges delta VMDK chains
│   - Integrity check   │  Validates before/after
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│   3. HARDWARE MAPPER  │  VMware → KVM device translation
│   - vmxnet3 → virtio  │  NIC mapping
│   - PVSCSI → virtio   │  Storage controller mapping
│   - SVGA → VGA/virtio │  Display mapping
│   - LSI Logic → virtio│  SCSI mapping
│   - BIOS/EFI detect   │  Firmware detection
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│   4. CONFIG GENERATOR │  Produces target-specific config
│   - libvirt XML       │  For bare KVM
│   - Proxmox qm cmds  │  For Proxmox VE
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│  5. GUEST OS FIXUP    │  Optional - modifies guest image
│  (via libguestfs)     │
│   - Remove VMware Tools│  Disable services/drivers
│   - Fix bootloader    │  GRUB/BCD updates
│   - Fix NIC names     │  ens192 → ens18 etc.
│   - Inject virtio drv │  Windows guests
│   - Install qemu-agent│  Guest agent setup
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│   6. NETWORK CONFIG   │  Bridge to host network
│   - Detect host bridge│  Find active bridge/bond
│   - Map VM NICs       │  Attach to correct network
│   - Preserve MACs     │  Optional MAC preservation
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│   7. LAUNCH & VERIFY  │  Start VM and health check
│   - Boot VM           │  virsh start / qm start
│   - Check console     │  Verify boot succeeds
│   - Ping test         │  Verify network connectivity
│   - Report status     │  Summary of migration
└───────────────────────┘
```

---

## 4. Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.10+ | Ecosystem compatibility (libguestfs, libvirt bindings), approachable for contributors |
| CLI Framework | Click or Typer | Clean CLI with progress bars and help text |
| Disk Conversion | qemu-img (subprocess) | Industry standard, battle-tested |
| VM Configuration | python-libvirt | Native libvirt XML generation |
| Guest Modification | libguestfs (guestfish/guestmount) | Offline guest image manipulation |
| OVF/OVA Parsing | xml.etree.ElementTree + tarfile | OVA is just a tar; OVF is XML |
| Proxmox Integration | subprocess (qm, pvesm commands) | Direct Proxmox API if available |
| Progress/UX | rich (Python library) | Beautiful terminal output, progress bars |
| Testing | pytest + fixtures with sample VMX/VMDK files | |
| Packaging | pip installable + standalone binary (PyInstaller) | |

---

## 5. Implementation Phases

### Phase 1: Core Parser & Converter (MVP)

**Goal:** Parse VMware config files and convert disks. No guest modification yet.

#### 5.1.1 VMX Parser (`vmfree/parsers/vmx.py`)

VMX files are key-value pairs. Parse them into a structured VM definition.

```
# Example VMX content to parse:
.encoding = "UTF-8"
config.version = "8"
virtualHW.version = "21"
displayName = "MyServer"
guestOS = "ubuntu-64"
memsize = "4096"
numvcpus = "4"
scsi0.virtualDev = "pvscsi"
scsi0:0.fileName = "MyServer.vmdk"
scsi0:0.present = "TRUE"
ethernet0.virtualDev = "vmxnet3"
ethernet0.connectionType = "bridged"
ethernet0.addressType = "generated"
ethernet0.generatedAddress = "00:0c:29:7d:2d:68"
firmware = "efi"
```

**Extract into a dataclass:**

```python
@dataclass
class VMDefinition:
    name: str
    guest_os: str
    memory_mb: int
    vcpus: int
    firmware: str  # "bios" or "efi"
    disks: list[DiskDefinition]
    nics: list[NICDefinition]
    scsi_controller: str  # "pvscsi", "lsilogic", "buslogic"
    display: str

@dataclass
class DiskDefinition:
    path: str          # Path to VMDK
    controller: str    # "scsi0", "ide0", "sata0"
    unit: int          # Unit number on controller
    size_bytes: int    # Virtual size
    disk_type: str     # "sparse", "flat", "sesparse"
    is_boot: bool

@dataclass
class NICDefinition:
    virtual_dev: str   # "vmxnet3", "e1000", "e1000e"
    mac_address: str
    connection_type: str  # "bridged", "nat", "hostonly"
    network_name: str     # VMware network name if set
```

#### 5.1.2 OVF/OVA Parser (`vmfree/parsers/ovf.py`)

- OVA: Extract with `tarfile`, find the `.ovf` inside
- OVF: Parse XML namespace `http://schemas.dmtf.org/ovf/envelope/1`
- Extract same VMDefinition structure from OVF XML
- Handle references to VMDK files (relative paths within the OVA)

#### 5.1.3 VMDK Inspector (`vmfree/parsers/vmdk.py`)

- Read VMDK descriptor files (text-based, NOT binary)
- Identify VMDK type: monolithicSparse, monolithicFlat, twoGbMaxExtentSparse, etc.
- Detect snapshot chains (delta VMDKs reference parent)
- Validate descriptor points to correct flat/data file
- Report virtual size vs actual size

#### 5.1.4 Disk Converter (`vmfree/converter/disk.py`)

```python
def convert_disk(vmdk_path: str, output_path: str, format: str = "qcow2") -> bool:
    """
    Wraps qemu-img convert.
    
    CRITICAL NOTES:
    - Always point qemu-img at the DESCRIPTOR .vmdk, not the -flat.vmdk
    - Both descriptor and flat files must be in the same directory
    - For snapshot chains, merge deltas first with qemu-img commit
    - Show progress via qemu-img's -p flag
    - Validate output with qemu-img check after conversion
    """
```

**Snapshot chain handling:**
1. Identify parent chain: `delta.vmdk` → `base-000001.vmdk` → `base.vmdk`
2. Merge from leaf to root using `qemu-img commit`
3. Convert final merged base to qcow2

#### 5.1.5 Hardware Mapper (`vmfree/mapper/hardware.py`)

Translation table:

```python
CONTROLLER_MAP = {
    "pvscsi":     "virtio-scsi",
    "lsilogic":   "virtio-scsi",    # or lsi53c895a for compat
    "buslogic":   "virtio-scsi",
    "lsisas1068": "virtio-scsi",
    "nvme":       "nvme",           # pass through if KVM supports
    "ahci":       "virtio-scsi",    # SATA → virtio
}

NIC_MAP = {
    "vmxnet3":  "virtio-net",
    "e1000":    "e1000",           # keep as-is, widely supported
    "e1000e":   "e1000e",          # keep as-is
    "vlance":   "virtio-net",
}

DISPLAY_MAP = {
    "svga":     "virtio-vga",      # or "qxl" for SPICE
    "vmware":   "virtio-vga",
}

FIRMWARE_MAP = {
    "efi":  "/usr/share/OVMF/OVMF_CODE.fd",   # OVMF path
    "bios": None,                                # SeaBIOS default
}
```

**IMPORTANT for Windows guests:** If the source VM uses PVSCSI or vmxnet3, the Windows guest has VMware-specific drivers. Switching directly to virtio will cause a BSOD. Two strategies:
1. **Safe mode (default for Windows):** Use `ide` or `sata` for boot disk, `e1000` for NIC. Advise user to install virtio drivers later.
2. **Inject mode (Phase 2):** Use libguestfs to inject virtio drivers into the Windows guest before switching controllers.

---

### Phase 2: Config Generation & Network

#### 5.2.1 Libvirt XML Generator (`vmfree/generators/libvirt.py`)

Generate a complete libvirt domain XML from the VMDefinition + mapped hardware.

```xml
<!-- Example output for a migrated VM -->
<domain type='kvm'>
  <name>MyServer</name>
  <memory unit='MiB'>4096</memory>
  <vcpu>4</vcpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader readonly='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE.fd</loader>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/var/lib/libvirt/images/MyServer.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <interface type='bridge'>
      <mac address='00:0c:29:7d:2d:68'/>
      <source bridge='br0'/>
      <model type='virtio'/>
    </interface>
    <graphics type='vnc' port='-1'/>
    <video>
      <model type='virtio'/>
    </video>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
  </devices>
</domain>
```

#### 5.2.2 Proxmox Command Generator (`vmfree/generators/proxmox.py`)

Generate `qm` commands for Proxmox VE integration:

```bash
# Create VM shell
qm create 100 --name MyServer --memory 4096 --cores 4 --cpu x86-64-v2-AES \
  --scsihw virtio-scsi-single --bios ovmf --machine q35 \
  --net0 virtio,bridge=vmbr0,macaddr=00:0c:29:7d:2d:68

# Import disk
qm importdisk 100 MyServer.qcow2 local-lvm

# Attach disk  
qm set 100 --scsi0 local-lvm:vm-100-disk-0

# Set boot order
qm set 100 --boot order=scsi0

# For EFI, add EFI disk
qm set 100 --efidisk0 local-lvm:1,efitype=4m,pre-enrolled-keys=1
```

**For Windows VMs (safe mode):**
```bash
qm set 100 --scsihw lsi --net0 e1000,bridge=vmbr0
# User installs virtio drivers later from virtio-win ISO
```

#### 5.2.3 Network Bridge Detection (`vmfree/network/detect.py`)

- Auto-detect the host's active bridge interface (`br0`, `vmbr0`, etc.)
- Parse `/sys/class/net/` for bridge interfaces
- Check which bridge has a default route (most likely the one connected to the LAN)
- Allow override via `--bridge` flag
- For Proxmox, default to `vmbr0`

---

### Phase 3: Guest OS Fixup (Offline Image Modification)

This is the "magic" phase that makes VMs actually boot cleanly. Uses `libguestfs` to mount and modify guest images without booting them.

#### 5.3.1 VMware Tools Removal (`vmfree/fixup/vmware_tools.py`)

**Linux guests:**
- Remove packages: `open-vm-tools`, `vmware-tools`
- Disable services: `vmtoolsd`, `vmware-tools`
- Remove kernel modules: `vmw_balloon`, `vmw_vmci`, `vmw_vsock`, `vmxnet3` (replaced by virtio)
- Clean up `/etc/vmware-tools/` directory

**Windows guests:**
- Disable VMware Tools services in registry
- Note: Cannot fully uninstall (installer checks if running on VMware). Advise user to uninstall before migration if possible.

#### 5.3.2 Bootloader Fix (`vmfree/fixup/bootloader.py`)

**Linux (GRUB2):**
- Ensure virtio modules are in initramfs/initrd: `virtio_blk`, `virtio_scsi`, `virtio_net`, `virtio_pci`
- Regenerate initramfs if needed (chroot + `dracut` or `update-initramfs`)
- Update GRUB config if disk device names changed

**Windows:**
- BCD store updates if boot device path changed
- This is tricky — may need to use `hivex` to edit Windows registry hive files

#### 5.3.3 Network Interface Fix (`vmfree/fixup/network.py`)

Linux guests often have persistent interface names tied to VMware MAC addresses.

- **systemd-networkd / Netplan (Ubuntu):** Update `/etc/netplan/*.yaml` interface names
- **ifcfg (RHEL/CentOS):** Update `/etc/sysconfig/network-scripts/ifcfg-*`
- **Debian interfaces:** Update `/etc/network/interfaces`
- **udev rules:** Remove `/etc/udev/rules.d/70-persistent-net.rules` (forces re-detection)

#### 5.3.4 Virtio Driver Injection — Windows (`vmfree/fixup/virtio_windows.py`)

For Windows guests that need virtio drivers:
- Mount the virtio-win ISO (Red Hat's `virtio-win` package)
- Inject drivers into Windows driver store via registry manipulation
- Drivers needed: `viostor` (block), `NetKVM` (network), `vioscsi` (SCSI), `Balloon` (memory)
- Reference: https://github.com/virtio-win/virtio-win-pkg-scripts

**NOTE:** This is the most complex fixup step. Phase 1 should skip this and use the safe-mode approach (IDE + e1000) for Windows VMs.

---

### Phase 4: CLI Interface & UX

#### 5.4.1 Main CLI (`vmfree/cli.py`)

```
Usage: vmfree [OPTIONS] COMMAND [ARGS]

Commands:
  migrate    Migrate a VMware VM to KVM/Proxmox
  inspect    Analyze a VMware VM without migrating
  convert    Convert disk only (VMDK → qcow2)
  validate   Check if a VMware VM can be migrated

Examples:
  vmfree migrate ./MyServer.vmx --target proxmox --vmid 100
  vmfree migrate ./MyServer.ova --target kvm --output /var/lib/libvirt/images/
  vmfree migrate ./MyServer/ --target proxmox --bridge vmbr0 --storage local-lvm
  vmfree inspect ./MyServer.vmx
  vmfree convert ./MyServer.vmdk --format qcow2
```

**Key flags:**

```
--target          kvm | proxmox (required)
--output          Output directory for converted files (default: current dir)
--bridge          Network bridge to attach to (auto-detected if not specified)
--storage         Proxmox storage target (default: local-lvm)
--vmid            Proxmox VM ID (auto-assigned if not specified)
--format          Disk format: qcow2 | raw (default: qcow2)
--no-fixup        Skip guest OS modifications
--windows-safe    Use IDE+e1000 for Windows (default for detected Windows guests)
--preserve-mac    Keep original MAC address
--dry-run         Show what would be done without doing it
--verbose / -v    Detailed output
```

#### 5.4.2 Progress & Output

Use the `rich` library for:
- Progress bars during disk conversion (can take a long time for large disks)
- Colored status indicators (✓ success, ✗ failure, ⚠ warning)
- Summary table at the end showing migration results
- Pre-flight checklist before starting

```
$ vmfree migrate ./MyServer.vmx --target proxmox

  VMFree v0.1.0 — VMware to KVM Migration Tool

  ┌─ Pre-flight Check ────────────────────────────┐
  │ ✓ VMX parsed: MyServer (Ubuntu 22.04, 4 vCPU, 4GB RAM)  │
  │ ✓ VMDK found: MyServer.vmdk (64GB virtual, 38GB actual)  │
  │ ✓ No snapshot chain detected                              │
  │ ✓ Network bridge detected: vmbr0                          │
  │ ✓ Proxmox storage available: local-lvm (412GB free)       │
  │ ✓ Firmware: EFI → OVMF                                    │
  │ ⚠ VMware Tools detected — will be removed                 │
  └───────────────────────────────────────────────────────────┘

  Converting disk ██████████████████████████░░░░ 78% (29.6/38.0 GB)
  
  ┌─ Migration Complete ──────────────────────────┐
  │ VM ID:     100                                 │
  │ Name:      MyServer                            │
  │ Status:    Running ✓                           │
  │ Console:   https://proxmox:8006/?console=kvm&vmid=100  │
  │ Network:   vmbr0 (00:0c:29:7d:2d:68)          │
  │                                                │
  │ Post-migration notes:                          │
  │  • Verify network connectivity in guest        │
  │  • Install qemu-guest-agent for full integration│
  └────────────────────────────────────────────────┘
```

---

## 6. File Structure

```
vmfree/
├── README.md
├── LICENSE                  # MIT
├── pyproject.toml           # Package config
├── setup.py
├── vmfree/
│   ├── __init__.py
│   ├── cli.py               # Main CLI entry point (Click/Typer)
│   ├── models.py            # VMDefinition, DiskDefinition, NICDefinition dataclasses
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── vmx.py           # VMX key-value parser
│   │   ├── ovf.py           # OVF XML + OVA tar parser
│   │   └── vmdk.py          # VMDK descriptor inspector
│   ├── converter/
│   │   ├── __init__.py
│   │   ├── disk.py           # qemu-img wrapper, snapshot merge
│   │   └── validate.py       # Pre/post conversion validation
│   ├── mapper/
│   │   ├── __init__.py
│   │   └── hardware.py       # VMware → KVM device translation tables
│   ├── generators/
│   │   ├── __init__.py
│   │   ├── libvirt.py        # Libvirt XML domain generator
│   │   └── proxmox.py        # Proxmox qm command generator
│   ├── network/
│   │   ├── __init__.py
│   │   └── detect.py         # Host bridge detection
│   ├── fixup/
│   │   ├── __init__.py
│   │   ├── vmware_tools.py   # VMware Tools removal
│   │   ├── bootloader.py     # GRUB/BCD fixup
│   │   ├── network.py        # Guest NIC name fixup
│   │   └── virtio_windows.py # Windows virtio driver injection
│   └── utils/
│       ├── __init__.py
│       ├── subprocess.py     # Wrapper for external commands
│       └── logging.py        # Rich-based logging
├── tests/
│   ├── conftest.py
│   ├── fixtures/             # Sample VMX, OVF, VMDK descriptor files
│   │   ├── sample-ubuntu.vmx
│   │   ├── sample-windows.vmx
│   │   ├── sample-efi.vmx
│   │   ├── sample.ovf
│   │   └── sample-descriptor.vmdk
│   ├── test_parsers/
│   │   ├── test_vmx.py
│   │   ├── test_ovf.py
│   │   └── test_vmdk.py
│   ├── test_mapper/
│   │   └── test_hardware.py
│   ├── test_generators/
│   │   ├── test_libvirt.py
│   │   └── test_proxmox.py
│   └── test_cli.py
└── docs/
    ├── CONTRIBUTING.md
    ├── HARDWARE_MAP.md       # Full device translation reference
    └── TESTED_GUESTS.md      # Matrix of tested guest OS migrations
```

---

## 7. Implementation Order for Claude Code

**Start here. Build in this exact order. Each step should be a commit.**

### Sprint 1: Foundation (Build First)

1. **Set up project skeleton** — `pyproject.toml`, directory structure, basic CLI with `--help`
2. **Implement `models.py`** — All dataclasses (VMDefinition, DiskDefinition, NICDefinition)
3. **Implement `vmx.py` parser** — Parse VMX files into VMDefinition. Write tests with fixture files.
4. **Implement `vmdk.py` inspector** — Read VMDK descriptor files, detect type, parent chain. Write tests.
5. **Implement `hardware.py` mapper** — Translation tables with tests.

### Sprint 2: Disk & Config (Build Second)

6. **Implement `disk.py` converter** — Wrap qemu-img, handle descriptor vs flat, progress reporting.
7. **Implement `libvirt.py` generator** — Generate valid libvirt XML from VMDefinition + mapped hardware.
8. **Implement `proxmox.py` generator** — Generate qm commands from VMDefinition.
9. **Implement `detect.py`** — Network bridge auto-detection.
10. **Wire up CLI `migrate` command** — Connect parsers → converter → generator → output.

### Sprint 3: Polish & Guest Fixup (Build Third)

11. **Implement `ovf.py` parser** — OVF XML + OVA extraction.
12. **Implement `inspect` command** — Pretty-print VM analysis without migrating.
13. **Implement `validate` command** — Pre-flight checks.
14. **Implement `vmware_tools.py`** — Linux guest VMware Tools removal via libguestfs.
15. **Implement `bootloader.py`** — Initramfs regeneration for virtio modules.
16. **Implement `network.py` fixup** — Guest NIC name updates.

### Sprint 4: Community Ready (Build Last)

17. **Write `README.md`** — Clear install instructions, usage examples, screenshots.
18. **Write `CONTRIBUTING.md`** — How to contribute, code style, PR process.
19. **Write `HARDWARE_MAP.md`** — Comprehensive VMware → KVM device reference.
20. **Set up GitHub Actions** — Run pytest on PR, lint with ruff.
21. **Package for pip** — `pip install vmfree`
22. **Create GitHub release** — Tag v0.1.0.

---

## 8. Testing Strategy

### Unit Tests (Every Sprint)

- **Parser tests:** Use fixture files (real VMX/OVF/VMDK descriptors, sanitized)
- **Mapper tests:** Assert every VMware device maps to correct KVM equivalent
- **Generator tests:** Validate generated libvirt XML is well-formed and schema-compliant
- **Proxmox tests:** Assert generated qm commands have correct syntax

### Integration Tests (Sprint 2+)

- Create a tiny test VMDK (1MB) with qemu-img for actual conversion testing
- Validate generated libvirt XML with `virt-xml-validate`
- Test CLI end-to-end with fixture files (no actual VM boot)

### Manual Testing Matrix (Post-Sprint 3)

Document results in `TESTED_GUESTS.md`:

| Guest OS | Source Format | Target | Boot | Network | Notes |
|----------|--------------|--------|------|---------|-------|
| Ubuntu 22.04 | VMX+VMDK | KVM | ✓ | ✓ | |
| Ubuntu 22.04 | VMX+VMDK | Proxmox | ✓ | ✓ | |
| Ubuntu 24.04 | OVA | KVM | ✓ | ✓ | |
| Windows 10 | VMX+VMDK | Proxmox | ✓ | ✓ | IDE safe mode |
| Windows Server 2022 | VMX+VMDK | Proxmox | ✓ | ✓ | IDE safe mode |
| Rocky Linux 9 | VMX+VMDK | KVM | ✓ | ✓ | |
| Debian 12 | OVA | Proxmox | ✓ | ✓ | |

---

## 9. Critical Implementation Notes

### Things That Will Bite You

1. **VMDK descriptor vs flat file:** `qemu-img` must be pointed at the small descriptor `.vmdk`, NOT the large `-flat.vmdk`. Both must be in the same directory. Get this wrong and conversion silently produces a broken image.

2. **Windows BSOD after migration:** If you switch a Windows VM from PVSCSI to virtio without injecting drivers first, it will bluescreen. Default to IDE/SATA + e1000 for Windows and let the user switch to virtio after installing drivers.

3. **EFI boot:** VMware EFI VMs need OVMF in KVM. The OVMF firmware path varies by distro (`/usr/share/OVMF/`, `/usr/share/edk2/`, etc.). Detect it.

4. **Proxmox ZFS storage:** When the target storage is ZFS (not a filesystem), you can't just copy qcow2 files. You need `qm importdisk` which handles the zvol creation. Always use `qm importdisk` for Proxmox.

5. **Snapshot chains:** VMware snapshots create delta VMDKs. These MUST be merged before conversion. The chain goes: current → delta-000002 → delta-000001 → base. Merge in reverse order.

6. **Guest NIC names:** Linux systemd predictable network names are tied to hardware. Moving from vmxnet3 to virtio changes the PCI slot, which changes the name (e.g., ens192 → ens18). If the guest has static network config, it will lose connectivity.

7. **MAC addresses:** Preserving the original VMware MAC is important if the guest has DHCP reservations or license tied to MAC. But VMware MACs start with `00:0c:29` or `00:50:56` — some networks may filter these. Offer `--preserve-mac` as an option.

8. **Large disks take a long time:** A 500GB VMDK can take 30+ minutes to convert. Progress reporting is essential for user experience.

### Dependencies to Verify on Target System

Before running any migration, verify these are installed:
- `qemu-img` (from qemu-utils package)
- `qemu-system-x86` (for KVM target)
- `libguestfs-tools` (for guest fixup — guestfish, virt-customize)
- `libvirt-daemon-system` (for KVM target)
- `ovmf` (for EFI guests)
- `virtio-win` ISO (for Windows driver injection — download from Fedora)

---

## 10. Community & Launch Strategy

### GitHub Repository Setup

- **Name:** `vmfree` (or `vmware-escape`, `liberate`, `vm-exodus` — pick something catchy)
- **Description:** "One-command migration from VMware to KVM/Proxmox. No PhD required."
- **Topics:** `vmware`, `kvm`, `proxmox`, `migration`, `virtualization`, `broadcom`, `open-source`
- **Issue templates:** Bug report, Feature request, Guest OS compatibility report
- **Discussions enabled:** For migration help and community support

### Launch Posts (After v0.1.0)

1. **r/sysadmin** — "I built a free tool to migrate your VMware VMs to Proxmox/KVM with one command"
2. **r/Proxmox** — "VMFree: Automated VMware → Proxmox migration (handles VMDK conversion, hardware mapping, network config)"
3. **r/homelab** — "Made a tool to escape VMware. Point it at your VMDK files and it does the rest."
4. **r/vmware** — "For everyone looking to migrate: open source tool that handles the conversion"
5. **Hacker News** — "Show HN: VMFree — One-command VMware to KVM migration"

### Contribution Areas for Ex-VMware Engineers

Label these as "good first issue" and "help wanted":
- Adding support for more VMware virtual hardware types
- Testing with specific guest OS versions
- Windows virtio driver injection improvements
- ESXi direct import (connecting to ESXi API)
- NSX network topology translation
- vSAN storage migration
- Batch migration support
- Web UI (stretch goal)

---

## 11. Stretch Goals (Post v0.1.0)

These are NOT in scope for the initial build but are documented for future contributors:

- **Direct ESXi import:** Connect to ESXi API, export VM, convert, import to target
- **Warm migration:** Incremental sync while source VM stays running (like MigrateKit)
- **Web UI:** Browser-based migration wizard
- **Batch migration:** Migrate multiple VMs from a manifest file
- **NSX → OVS mapping:** Translate VMware NSX network config to Open vSwitch
- **vSAN migration:** Handle VMs stored on VMware vSAN clusters
- **Cloud targets:** Migrate VMware VMs to AWS, Azure, GCP
- **Rollback:** If migration fails, clean up and provide rollback instructions

---

## 12. License

MIT License. Keep it permissive. We want maximum adoption and contribution.

The entire point is to make it as easy as possible for people to escape vendor lock-in. The license should reflect that philosophy.

---

*"The Broadcom acquisition is your signal to modernize. VMFree is the tool to make it happen."*
