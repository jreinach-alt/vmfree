# 🔓 VMFree

**One-command migration from VMware to KVM/Proxmox. No PhD required.**

VMFree takes your VMware virtual machine files and migrates them to KVM-based hypervisors. Point it at a VMX, VMDK, or OVA — it handles disk conversion, hardware mapping, network config, and guest OS fixup.

> *Built by sysadmins, for sysadmins, because Broadcom won't stop until we make them irrelevant.*

## Quick Start

```bash
pip install vmfree

# Migrate a VMware VM to Proxmox
vmfree migrate ./MyServer.vmx --target proxmox

# Migrate to bare KVM/libvirt
vmfree migrate ./MyServer.vmx --target kvm

# Inspect without migrating
vmfree inspect ./MyServer.vmx

# Convert disk only
vmfree convert ./MyServer.vmdk --format qcow2
```

## What It Does

1. **Parses** VMware config files (VMX, OVF, OVA)
2. **Converts** VMDK disks to qcow2 (handles snapshot chains)
3. **Maps** VMware hardware to KVM equivalents (vmxnet3 → virtio, PVSCSI → virtio-scsi)
4. **Generates** libvirt XML or Proxmox `qm` commands
5. **Fixes** guest OS (removes VMware Tools, updates bootloader, fixes NIC names)
6. **Launches** the VM on your new hypervisor with working network

## Requirements

- Python 3.10+
- `qemu-img` (from qemu-utils)
- `libvirt` (for KVM target)
- `libguestfs-tools` (for guest OS fixup)
- `ovmf` (for EFI guests)

## Status

🚧 **Under active development** — not yet ready for production use.

## Contributing

We welcome contributions. See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines.

Whether you're an ex-VMware engineer, a sysadmin fleeing Broadcom pricing, or someone who believes in open source — we'd love your help.

## License

MIT — because vendor lock-in is the problem, not the solution.
