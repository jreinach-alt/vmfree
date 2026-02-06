"""Tests for the Proxmox qm command generator.

Tests all three fixture VM types and verifies correct command structure
for qm create, importdisk, set, and efidisk operations.
"""

from vmfree.generators.proxmox import generate_proxmox_commands
from vmfree.mapper.hardware import map_hardware
from vmfree.models import (
    DiskDefinition,
    Firmware,
    NICDefinition,
    VMDefinition,
)


def _make_ubuntu_vm() -> VMDefinition:
    return VMDefinition(
        name="Ubuntu-Server-22",
        guest_os="ubuntu-64",
        memory_mb=4096,
        vcpus=4,
        firmware=Firmware.EFI,
        disks=[DiskDefinition(path="/tmp/Ubuntu-Server-22.vmdk", controller="scsi0",
                              unit=0, is_boot=True)],
        nics=[NICDefinition(virtual_dev="vmxnet3", mac_address="00:0c:29:7d:2d:68",
                            connection_type="bridged")],
        scsi_controller="pvscsi",
        display="svga",
    )


def _make_windows_vm() -> VMDefinition:
    return VMDefinition(
        name="Win2022-DC",
        guest_os="windows2019srvnext-64",
        memory_mb=8192,
        vcpus=2,
        firmware=Firmware.BIOS,
        disks=[
            DiskDefinition(path="/tmp/Win2022-DC.vmdk", controller="scsi0",
                           unit=0, is_boot=True),
            DiskDefinition(path="/tmp/Win2022-DC-data.vmdk", controller="scsi0",
                           unit=1, is_boot=False),
        ],
        nics=[NICDefinition(virtual_dev="vmxnet3", mac_address="00:50:56:a1:b2:c3",
                            connection_type="custom")],
        scsi_controller="lsilogic",
        display="svga",
    )


def _make_centos_vm() -> VMDefinition:
    return VMDefinition(
        name="CentOS-7",
        guest_os="centos-64",
        memory_mb=2048,
        vcpus=1,
        firmware=Firmware.BIOS,
        disks=[DiskDefinition(path="/tmp/CentOS-7.vmdk", controller="scsi0",
                              unit=0, is_boot=True)],
        nics=[NICDefinition(virtual_dev="e1000e", mac_address="00:0c:29:aa:bb:cc",
                            connection_type="nat")],
        scsi_controller="lsilogic",
        display="svga",
    )


# ---------------------------------------------------------------------------
# Ubuntu EFI + PVSCSI + vmxnet3
# ---------------------------------------------------------------------------

class TestUbuntuProxmox:
    """Test Proxmox commands for Ubuntu EFI VM."""

    def setup_method(self):
        self.vm = _make_ubuntu_vm()
        self.hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        self.cmds = generate_proxmox_commands(
            self.vm, self.hw,
            vmid=100,
            disk_paths=["/converted/Ubuntu-Server-22.qcow2"],
            storage="local-lvm",
            bridge="vmbr0",
        )

    def test_command_count(self):
        # create + importdisk + attach + boot order + efidisk
        assert len(self.cmds) == 5

    def test_create_command(self):
        create = self.cmds[0]
        assert "qm create 100" in create
        assert "--name Ubuntu-Server-22" in create
        assert "--memory 4096" in create
        assert "--cores 4" in create
        assert "--machine q35" in create

    def test_create_scsihw(self):
        assert "--scsihw virtio-scsi-single" in self.cmds[0]

    def test_create_bios_ovmf(self):
        assert "--bios ovmf" in self.cmds[0]

    def test_create_nic(self):
        create = self.cmds[0]
        assert "--net0 virtio,bridge=vmbr0,macaddr=00:0c:29:7d:2d:68" in create

    def test_import_disk(self):
        assert self.cmds[1] == "qm importdisk 100 /converted/Ubuntu-Server-22.qcow2 local-lvm"

    def test_attach_disk(self):
        assert "qm set 100 --scsi0 local-lvm:vm-100-disk-0" in self.cmds[2]

    def test_boot_order(self):
        assert "qm set 100 --boot order=scsi0" in self.cmds[3]

    def test_efidisk(self):
        eficmd = self.cmds[4]
        assert "qm set 100 --efidisk0" in eficmd
        assert "efitype=4m" in eficmd
        assert "pre-enrolled-keys=1" in eficmd


# ---------------------------------------------------------------------------
# Windows safe-mode (IDE + e1000)
# ---------------------------------------------------------------------------

class TestWindowsProxmox:
    """Test Proxmox commands for Windows VM in safe mode."""

    def setup_method(self):
        self.vm = _make_windows_vm()
        self.hw = map_hardware("lsilogic", "vmxnet3", "svga", "bios", windows_safe=True)
        self.cmds = generate_proxmox_commands(
            self.vm, self.hw,
            vmid=200,
            disk_paths=["/conv/Win2022-DC.qcow2", "/conv/Win2022-DC-data.qcow2"],
            storage="local-zfs",
            bridge="vmbr1",
        )

    def test_scsihw_lsi(self):
        assert "--scsihw lsi" in self.cmds[0]

    def test_bios_seabios(self):
        assert "--bios seabios" in self.cmds[0]

    def test_e1000_nic(self):
        assert "--net0 e1000,bridge=vmbr1,macaddr=00:50:56:a1:b2:c3" in self.cmds[0]

    def test_two_disk_imports(self):
        assert "qm importdisk 200 /conv/Win2022-DC.qcow2 local-zfs" in self.cmds[1]
        assert "qm importdisk 200 /conv/Win2022-DC-data.qcow2 local-zfs" in self.cmds[2]

    def test_ide_disk_attach(self):
        assert "qm set 200 --ide0 local-zfs:vm-200-disk-0" in self.cmds[3]
        assert "qm set 200 --ide1 local-zfs:vm-200-disk-1" in self.cmds[4]

    def test_boot_order_ide(self):
        assert "qm set 200 --boot order=ide0" in self.cmds[5]

    def test_no_efidisk(self):
        # BIOS VM should not have efidisk
        for cmd in self.cmds:
            assert "efidisk" not in cmd

    def test_command_count(self):
        # create + 2 imports + 2 attaches + boot order = 6
        assert len(self.cmds) == 6


# ---------------------------------------------------------------------------
# CentOS BIOS + lsilogic + e1000e
# ---------------------------------------------------------------------------

class TestCentosProxmox:
    """Test Proxmox commands for CentOS BIOS VM."""

    def setup_method(self):
        self.vm = _make_centos_vm()
        self.hw = map_hardware("lsilogic", "e1000e", "svga", "bios")
        self.cmds = generate_proxmox_commands(
            self.vm, self.hw,
            vmid=300,
            disk_paths=["/conv/CentOS-7.qcow2"],
            bridge="vmbr0",
        )

    def test_name(self):
        assert "--name CentOS-7" in self.cmds[0]

    def test_memory(self):
        assert "--memory 2048" in self.cmds[0]

    def test_cores(self):
        assert "--cores 1" in self.cmds[0]

    def test_scsihw_virtio(self):
        assert "--scsihw virtio-scsi-single" in self.cmds[0]

    def test_e1000_nic(self):
        # e1000e maps to e1000 in Proxmox
        assert "--net0 e1000,bridge=vmbr0,macaddr=00:0c:29:aa:bb:cc" in self.cmds[0]

    def test_scsi_disk_attach(self):
        assert "qm set 300 --scsi0 local-lvm:vm-300-disk-0" in self.cmds[2]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestProxmoxEdgeCases:
    """Test edge cases and options."""

    def test_no_mac_when_not_preserving(self):
        vm = _make_ubuntu_vm()
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        cmds = generate_proxmox_commands(vm, hw, preserve_mac=False)
        create = cmds[0]
        assert "macaddr" not in create
        assert "virtio,bridge=vmbr0" in create

    def test_default_disk_paths(self):
        vm = _make_ubuntu_vm()
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        cmds = generate_proxmox_commands(vm, hw, vmid=100)
        # Should have a placeholder importdisk command
        assert any("importdisk" in cmd for cmd in cmds)

    def test_no_disks(self):
        vm = _make_ubuntu_vm()
        vm.disks = []
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        cmds = generate_proxmox_commands(vm, hw, disk_paths=[])
        # Should only have create + efidisk
        assert len(cmds) == 2
        assert "qm create" in cmds[0]
        assert "efidisk" in cmds[1]

    def test_no_nics_empty_mac(self):
        vm = _make_ubuntu_vm()
        vm.nics = []
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        cmds = generate_proxmox_commands(vm, hw)
        create = cmds[0]
        # NIC line should still be present but without macaddr
        assert "--net0 virtio,bridge=vmbr0" in create

    def test_custom_storage(self):
        vm = _make_ubuntu_vm()
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        cmds = generate_proxmox_commands(
            vm, hw, storage="ceph-pool", disk_paths=["/d.qcow2"],
        )
        assert any("ceph-pool" in cmd for cmd in cmds)

    def test_cpu_flag(self):
        vm = _make_ubuntu_vm()
        hw = map_hardware("pvscsi", "vmxnet3", "svga", "efi")
        cmds = generate_proxmox_commands(vm, hw)
        assert "--cpu x86-64-v2-AES" in cmds[0]
