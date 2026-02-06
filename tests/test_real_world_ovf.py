"""Tests against real-world OVF files from public repositories.

These fixtures are NOT hand-crafted — they come from VMware's govmomi,
OpenStack Nova, ManageIQ, and Canonical cloud-init. They test the parser
against actual OVF exports from production tools.

Sources:
  - govmomi: https://github.com/vmware/govmomi/tree/main/ovf/fixtures
  - OpenStack: https://github.com/openstack/nova (VMware driver tests)
  - ManageIQ: https://github.com/ManageIQ/manageiq-providers-vmware
  - Canonical: https://github.com/canonical/cloud-init
"""

from pathlib import Path

import pytest

from vmfree.models import Firmware
from vmfree.parsers.ovf import parse_ovf_file

FIXTURES = Path(__file__).parent / "fixtures" / "real-world"


# ---------------------------------------------------------------------------
# VMware govmomi fixtures
# ---------------------------------------------------------------------------

class TestGovmomiPhoton5:
    """Photon OS 5.0 — VMware's own Linux distro, EFI, vmx-15."""

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "govmomi-photon5.ovf")

    def test_name(self, vm):
        assert vm.name == "Photon OS"

    def test_guest_os(self, vm):
        assert vm.guest_os == "vmwarePhoton64Guest"

    def test_efi_firmware(self, vm):
        assert vm.firmware == Firmware.EFI

    def test_hw_version_15(self, vm):
        assert vm.hardware_version == 15

    def test_vcpus(self, vm):
        assert vm.vcpus == 1

    def test_memory_2gb(self, vm):
        assert vm.memory_mb == 2048

    def test_single_disk(self, vm):
        assert len(vm.disks) == 1

    def test_disk_size_16gb(self, vm):
        assert vm.disks[0].size_bytes == 17179869184

    def test_vmxnet3_nic(self, vm):
        assert len(vm.nics) == 1
        assert vm.nics[0].virtual_dev == "vmxnet3"

    def test_scsi_controller(self, vm):
        assert vm.scsi_controller == "virtualscsi"


class TestGovmomiTtylinux:
    """ttylinux — tiny VM, BIOS, vmx-09, E1000 NIC, IDE controller."""

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "govmomi-ttylinux.ovf")

    def test_name(self, vm):
        assert vm.name == "ttylinux-pc_i486-16.1"

    def test_guest_os(self, vm):
        assert vm.guest_os == "otherLinuxGuest"

    def test_bios_firmware(self, vm):
        assert vm.firmware == Firmware.BIOS

    def test_hw_version_9(self, vm):
        assert vm.hardware_version == 9

    def test_tiny_memory(self, vm):
        assert vm.memory_mb == 32

    def test_e1000_nic(self, vm):
        assert len(vm.nics) == 1
        assert vm.nics[0].virtual_dev == "e1000"

    def test_single_disk(self, vm):
        assert len(vm.disks) == 1
        assert vm.disks[0].size_bytes == 31457280  # 30 MB


class TestGovmomiUbuntu24:
    """Ubuntu 24.10 cloud image — vmx-10, VmxNet3, SCSI."""

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "govmomi-ubuntu24.ovf")

    def test_name(self, vm):
        assert "ubuntu" in vm.name.lower()

    def test_guest_os(self, vm):
        assert vm.guest_os == "ubuntu64Guest"

    def test_bios_firmware(self, vm):
        assert vm.firmware == Firmware.BIOS

    def test_hw_version_10(self, vm):
        assert vm.hardware_version == 10

    def test_memory(self, vm):
        assert vm.memory_mb == 1024

    def test_vmxnet3_nic(self, vm):
        assert vm.nics[0].virtual_dev == "vmxnet3"

    def test_disk_size_10gb(self, vm):
        assert vm.disks[0].size_bytes == 10737418240


class TestGovmomiConfigspec:
    """HAProxy load balancer appliance — 3 NICs, vmx-13."""

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "govmomi-configspec.ovf")

    def test_name(self, vm):
        assert vm.name == "haproxy"

    def test_three_nics(self, vm):
        assert len(vm.nics) == 3

    def test_nic_networks(self, vm):
        networks = [n.network_name for n in vm.nics]
        assert "Management" in networks
        assert "Workload" in networks
        assert "Frontend" in networks

    def test_all_vmxnet3(self, vm):
        assert all(n.virtual_dev == "vmxnet3" for n in vm.nics)

    def test_hw_version_13(self, vm):
        assert vm.hardware_version == 13

    def test_memory_4gb(self, vm):
        assert vm.memory_mb == 4096


class TestGovmomiProperties:
    """Test VM with vApp properties, no disks."""

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "govmomi-properties.ovf")

    def test_name(self, vm):
        assert vm.name == "test-vm"

    def test_no_disks(self, vm):
        assert len(vm.disks) == 0

    def test_has_nic(self, vm):
        assert len(vm.nics) == 1

    def test_hw_version_19(self, vm):
        assert vm.hardware_version == 19


# ---------------------------------------------------------------------------
# VirtualSystemCollection support (the bugs we fixed)
# ---------------------------------------------------------------------------

class TestGovmomiVirtualSystemCollection:
    """DMTF OVF v2 spec Example 3 — VirtualSystemCollection with 2 VMs.

    Uses OVF envelope/2 namespace, EthernetPortItem, StorageItem.
    Parser extracts the first VirtualSystem from the collection.
    """

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "govmomi-virtualsystemcollection.ovf")

    def test_parses_without_error(self, vm):
        assert vm is not None

    def test_name_from_first_vs(self, vm):
        assert vm.name == "Virtual Appliance One"

    def test_memory_1gb(self, vm):
        # AllocationUnits="byte*2^30", VirtualQuantity=1 → 1024 MB
        assert vm.memory_mb == 1024

    def test_nic_from_ethernet_port_item(self, vm):
        assert len(vm.nics) == 1

    def test_nic_has_mac(self, vm):
        # OVF v2 EthernetPortItem has epasd:Address with MAC
        assert vm.nics[0].mac_address == "00-16-8B-DB-00-5E"

    def test_nic_network(self, vm):
        assert vm.nics[0].network_name == "VS Network"


class TestManageiqVcloud:
    """ManageIQ vCloud vApp export — 2 VMs inside VirtualSystemCollection.

    Uses ovf: prefixed elements. Parser extracts the first VirtualSystem.
    """

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "manageiq-vcloud.ovf")

    def test_parses_without_error(self, vm):
        assert vm is not None

    def test_name(self, vm):
        assert vm.name == "VM1"

    def test_guest_os_centos(self, vm):
        assert vm.guest_os == "centos64Guest"

    def test_hw_version_10(self, vm):
        assert vm.hardware_version == 10

    def test_memory(self, vm):
        assert vm.memory_mb == 2048

    def test_vcpus(self, vm):
        assert vm.vcpus == 2

    def test_vmxnet3_nic(self, vm):
        assert len(vm.nics) == 1
        assert vm.nics[0].virtual_dev == "vmxnet3"

    def test_real_mac_address(self, vm):
        # This is a real VMware MAC from the vCloud export
        assert vm.nics[0].mac_address == "00:50:56:01:00:4d"

    def test_scsi_controller(self, vm):
        assert "lsilogic" in vm.scsi_controller


# ---------------------------------------------------------------------------
# Other real-world sources
# ---------------------------------------------------------------------------

class TestOpenStackDSL:
    """Damn Small Linux from OpenStack Nova test fixtures."""

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "openstack-dsl.ovf")

    def test_name(self, vm):
        assert vm.name == "Damn Small Linux"

    def test_guest_os(self, vm):
        assert vm.guest_os == "debian4Guest"

    def test_vlance_nic(self, vm):
        assert vm.nics[0].virtual_dev == "vlance"

    def test_disk_256mb(self, vm):
        assert vm.disks[0].size_bytes == 268435456


class TestCanonicalCloudInit:
    """Ubuntu Server OVF from Canonical's cloud-init project."""

    @pytest.fixture()
    def vm(self):
        return parse_ovf_file(FIXTURES / "canonical-cloud-init.ovf")

    def test_name(self, vm):
        assert vm.name == "Ubuntu"

    def test_e1000_nic(self, vm):
        assert vm.nics[0].virtual_dev == "e1000"

    def test_lsilogic_controller(self, vm):
        assert vm.scsi_controller == "lsilogic"

    def test_hw_version_7(self, vm):
        assert vm.hardware_version == 7

    def test_memory(self, vm):
        assert vm.memory_mb == 256


class TestTruncatedFile:
    """vmware-archive-centos.ovf is truncated XML from the source repo."""

    def test_raises_parse_error(self):
        with pytest.raises(Exception):
            parse_ovf_file(FIXTURES / "vmware-archive-centos.ovf")
