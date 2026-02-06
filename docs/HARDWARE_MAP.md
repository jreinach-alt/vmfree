# VMware to KVM Hardware Translation Reference

This is the definitive reference for how VMware virtual hardware maps to KVM/QEMU equivalents. Bookmark this page. You'll need it.

## Storage Controllers

| VMware Device | KVM Equivalent | Bus | Device Prefix | Notes |
|--------------|----------------|-----|---------------|-------|
| PVSCSI (paravirtual) | virtio-scsi | scsi | `/dev/sd*` | Best performance. Requires virtio drivers in guest. |
| LSI Logic SAS | virtio-scsi | scsi | `/dev/sd*` | Common default for Linux VMs. |
| LSI Logic Parallel | virtio-scsi | scsi | `/dev/sd*` | Legacy controller, still works. |
| BusLogic | virtio-scsi | scsi | `/dev/sd*` | Very old. Rare in modern VMs. |
| AHCI (SATA) | virtio-scsi | scsi | `/dev/sd*` | Used by some desktop VMs. |
| NVMe | nvme | nvme | `/dev/nvme*` | Modern VMs (HW version 13+). Maps directly. |
| IDE | ide | ide | `/dev/hd*` | Legacy. Used as Windows safe fallback. |

**Default mapping**: Any unrecognized SCSI controller maps to `virtio-scsi`.

### Why virtio-scsi?

virtio-scsi is the recommended storage controller for KVM guests:
- Near-native I/O performance (vs. emulated IDE/AHCI)
- Supports SCSI features (TRIM/discard, SCSI reservations)
- Supports up to 16,384 devices per controller
- Hot-plug capable

The only exception is **Windows without virtio drivers** - see the Windows Safe Mode section below.

## Network Interfaces

| VMware Device | KVM Equivalent | Notes |
|--------------|----------------|-------|
| vmxnet3 | virtio-net | Best performance. Requires virtio drivers. |
| e1000 | e1000 | **Kept as-is.** Intel emulation, universally supported. |
| e1000e | e1000e | **Kept as-is.** Newer Intel emulation. |
| vlance (AMD PCNet) | virtio-net | Very old NIC. Upgrade to virtio. |

**Why virtio-net?** It's the paravirtual NIC for KVM, equivalent to what vmxnet3 is for VMware. Same concept: guest-cooperative I/O for maximum throughput and minimum CPU overhead.

**MAC address note**: VMware uses two MAC prefixes:
- `00:0c:29:xx:xx:xx` (auto-generated)
- `00:50:56:xx:xx:xx` (manually assigned / vSphere managed)

Use `--preserve-mac` to keep these addresses. This matters if you have DHCP reservations, firewall rules, or monitoring that keys on MAC address.

## Display Adapters

| VMware Device | KVM Equivalent | Notes |
|--------------|----------------|-------|
| SVGA (VMware SVGA II) | virtio-vga | Default for all VMs. |
| VMware display | virtio-vga | Same mapping. |

virtio-vga provides:
- VNC access via libvirt/Proxmox console
- SPICE support for better remote desktop experience
- 3D acceleration support (with virgl)

## Firmware / Boot

| VMware Setting | KVM Equivalent | Notes |
|---------------|----------------|-------|
| EFI | OVMF (pflash) | Requires `ovmf` package on host. Path: `/usr/share/OVMF/OVMF_CODE.fd` |
| BIOS (default) | SeaBIOS | KVM default. No extra packages needed. |

### OVMF Paths by Distribution

| Distribution | OVMF Code Path |
|-------------|----------------|
| Debian/Ubuntu | `/usr/share/OVMF/OVMF_CODE.fd` |
| RHEL/CentOS/Rocky | `/usr/share/edk2/ovmf/OVMF_CODE.fd` |
| Fedora | `/usr/share/edk2/ovmf/OVMF_CODE.fd` |
| Proxmox | `/usr/share/pve-edk2-firmware/OVMF_CODE.fd` |
| Arch Linux | `/usr/share/edk2-ovmf/x64/OVMF_CODE.fd` |

VMFree currently defaults to the Debian/Ubuntu path. If you're on a different distro and OVMF isn't found, VMFree will warn you during validation.

### EFI + Proxmox

Proxmox EFI guests need an `efidisk0` — a small disk that stores UEFI variables (boot order, Secure Boot state, etc.). VMFree automatically generates the `qm set --efidisk0` command with pre-enrolled keys enabled.

## Machine Type

All migrated VMs use the **q35** machine type:

| VMware | KVM |
|--------|-----|
| Any | q35 (PCIe chipset) |

q35 provides:
- PCIe bus (vs. legacy PCI on i440fx)
- Native AHCI/NVMe support
- Better IOMMU support for passthrough
- Required for UEFI boot on most setups

## USB Controllers

| VMware Device | KVM Equivalent | Notes |
|--------------|----------------|-------|
| USB 2.0 (EHCI) | qemu-xhci | Upgraded to USB 3.0 by default. |
| USB 3.0 (xHCI) | qemu-xhci | Direct mapping. |

VMFree does not currently auto-configure USB controllers. If the guest needs USB devices, add them manually after migration.

## Serial / Parallel Ports

| VMware Device | KVM Equivalent | Notes |
|--------------|----------------|-------|
| Serial port | isa-serial | Available via `virsh console` or Proxmox serial terminal. |
| Parallel port | (not mapped) | Rarely needed. Add manually if required. |

## Audio

| VMware Device | KVM Equivalent | Notes |
|--------------|----------------|-------|
| HD Audio | ich9-intel-hda | For desktop VMs that need audio. |
| Sound Blaster | (not mapped) | Legacy. Not commonly needed. |

VMFree does not currently auto-configure audio devices. Most server VMs don't need them.

## Guest Agent

VMFree automatically configures the **QEMU Guest Agent** channel in libvirt XML:

```xml
<channel type="unix">
  <target type="virtio" name="org.qemu.guest_agent.0"/>
</channel>
```

Install `qemu-guest-agent` inside the guest for:
- Proper shutdown from hypervisor
- Filesystem freeze for consistent snapshots
- IP address reporting to the hypervisor

---

## Windows Safe Mode

This is the most critical section if you're migrating Windows VMs.

### The Problem

Windows does not include virtio drivers by default. If you migrate a Windows VM from VMware (using PVSCSI + vmxnet3) to KVM (using virtio-scsi + virtio-net), Windows can't find its boot disk on startup. Result: **BSOD** (Blue Screen of Death) with `INACCESSIBLE_BOOT_DEVICE`.

### VMFree's Solution: Safe Mode

When VMFree detects a Windows guest (from the `guestOS` field in VMX/OVF), it automatically enables **safe mode**:

| Component | Normal Mode | Windows Safe Mode |
|-----------|-------------|-------------------|
| Storage controller | virtio-scsi | **IDE** |
| Network adapter | virtio-net | **e1000** |
| Disk bus | scsi (`/dev/sd*`) | **ide** (`/dev/hd*`) |
| SCSI HW (Proxmox) | virtio-scsi-single | **lsi** |

IDE and e1000 drivers are **built into every version of Windows** since Windows XP. The VM will boot successfully without any additional drivers.

### The Trade-Off

Safe mode works, but it's slower:
- **IDE**: No TRIM/discard support, slower I/O, limited to 4 devices
- **e1000**: ~1 Gbps max, higher CPU usage than virtio-net

### Upgrading to Virtio (Post-Migration)

After the VM boots successfully on KVM:

1. **Download** the [virtio-win ISO](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/)
2. **Attach** the ISO to the VM (via libvirt or Proxmox console)
3. **Install** the virtio drivers from the ISO:
   - Open Device Manager
   - Update drivers for storage controller and network adapter
   - Point to the appropriate directory on the ISO (`amd64/w10` for Windows 10, etc.)
4. **Shut down** the VM
5. **Switch** the storage controller to virtio-scsi and NIC to virtio-net
6. **Boot** - Windows will now use the fast virtio drivers

### Forcing Safe Mode

Safe mode is automatic for detected Windows guests. You can also force it for any guest:

```bash
vmfree migrate ./MyVM.vmx --target kvm --windows-safe
```

This is useful for:
- Guests where the `guestOS` field is wrong or missing
- Linux guests with broken virtio support
- Any situation where you want maximum compatibility over performance

---

## Complete Translation Table

For quick reference, here's every mapping in one table:

| Category | VMware | KVM/QEMU | Auto? |
|----------|--------|----------|-------|
| SCSI: PVSCSI | pvscsi | virtio-scsi | Yes |
| SCSI: LSI Logic SAS | lsisas1068 | virtio-scsi | Yes |
| SCSI: LSI Logic Parallel | lsilogic | virtio-scsi | Yes |
| SCSI: BusLogic | buslogic | virtio-scsi | Yes |
| SCSI: AHCI | ahci | virtio-scsi | Yes |
| Storage: NVMe | nvme | nvme | Yes |
| NIC: vmxnet3 | vmxnet3 | virtio-net | Yes |
| NIC: e1000 | e1000 | e1000 | Yes (kept) |
| NIC: e1000e | e1000e | e1000e | Yes (kept) |
| NIC: vlance | vlance | virtio-net | Yes |
| Display: SVGA | svga | virtio-vga | Yes |
| Boot: EFI | efi | OVMF | Yes |
| Boot: BIOS | bios | SeaBIOS | Yes (default) |
| Machine | any | q35 | Yes |
| CPU | any | host-passthrough | Yes |
| Guest Agent | VMware Tools | QEMU Guest Agent channel | Yes |
| Win Storage | any | **IDE** (safe) | Auto for Windows |
| Win NIC | any | **e1000** (safe) | Auto for Windows |

---

## Further Reading

- [libvirt domain XML format](https://libvirt.org/formatdomain.html)
- [Proxmox qm manual](https://pve.proxmox.com/pve-docs/qm.1.html)
- [QEMU device emulation](https://www.qemu.org/docs/master/system/devices/index.html)
- [virtio-win drivers](https://github.com/virtio-win/virtio-win-pkg-scripts)
- [OVMF firmware](https://github.com/tianocore/edk2)
