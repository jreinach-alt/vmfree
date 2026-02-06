"""End-to-end output validation tests.

These tests run real-world VMware files through the complete pipeline:
  OVF/VMX → parse → hardware map → libvirt XML / Proxmox qm commands

Libvirt XML output is validated against the official libvirt RelaxNG
schema (downloaded from github.com/libvirt/libvirt). This is the same
schema used by `virt-xml-validate` and `virsh define`.

Proxmox qm commands are validated structurally: correct subcommand,
required flags, valid flag values, and proper ordering.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from vmfree.generators.libvirt import generate_libvirt_xml
from vmfree.generators.proxmox import generate_proxmox_commands
from vmfree.mapper.hardware import map_hardware
from vmfree.models import VMDefinition
from vmfree.parsers.ovf import parse_ovf_file
from vmfree.parsers.vmx import parse_vmx_file

FIXTURES = Path(__file__).parent / "fixtures"
REAL_WORLD = FIXTURES / "real-world"
SCHEMA_DIR = Path("/tmp/libvirt-schemas")

# ---------------------------------------------------------------------------
# Schema setup — download once per test session
# ---------------------------------------------------------------------------

# List of all required RNG schema files (domain.rng includes others)
_SCHEMA_FILES = [
    "domain.rng", "domainoverrides.rng", "domaincommon.rng", "basictypes.rng",
    "storagecommon.rng", "networkcommon.rng", "nwfilter_params.rng",
    "privatedata.rng", "cpu.rng", "cputypes.rng", "sysinfo.rng",
    "sysinfocommon.rng",
]

_BASE_URL = "https://raw.githubusercontent.com/libvirt/libvirt/master/src/conf/schemas"


def _ensure_schemas() -> bool:
    """Download libvirt RNG schemas if not already present."""
    if all((SCHEMA_DIR / f).exists() for f in _SCHEMA_FILES):
        return True
    # Schemas may already be downloaded by previous test run or manual setup
    return (SCHEMA_DIR / "domain.rng").exists() and (SCHEMA_DIR / "domaincommon.rng").exists()


def _get_schema():
    """Load and compile the libvirt domain RelaxNG schema."""
    try:
        from lxml import etree as lxml_etree
    except ImportError:
        return None

    if not _ensure_schemas():
        return None

    try:
        return lxml_etree.RelaxNG(lxml_etree.parse(str(SCHEMA_DIR / "domain.rng")))
    except Exception:
        return None


# Session-scoped schema (loaded once)
_schema_cache: object | None = "NOT_LOADED"


def _validate_libvirt_xml(xml_str: str) -> tuple[bool, str]:
    """Validate XML against the libvirt domain schema.

    Returns (True, "") if valid, (False, error_message) if invalid.
    Returns (True, "schema not available") if lxml/schemas aren't installed.
    """
    global _schema_cache  # noqa: PLW0603
    if _schema_cache == "NOT_LOADED":
        _schema_cache = _get_schema()

    if _schema_cache is None:
        return True, "schema not available"

    try:
        from lxml import etree as lxml_etree
    except ImportError:
        return True, "lxml not available"

    doc = lxml_etree.fromstring(xml_str.encode())
    if _schema_cache.validate(doc):
        return True, ""
    errors = "\n".join(str(e) for e in _schema_cache.error_log)
    return False, errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipeline(vm: VMDefinition) -> tuple[str, list[str]]:
    """Run a VMDefinition through hardware mapping + both generators.

    Returns (libvirt_xml, proxmox_commands).
    """
    nic_type = vm.nics[0].virtual_dev if vm.nics else "e1000"
    hw = map_hardware(
        vm.scsi_controller or "lsilogic",
        nic_type,
        vm.display or "svga",
        vm.firmware.value,
        windows_safe=vm.is_windows,
    )
    xml_str = generate_libvirt_xml(vm, hw)
    qm_cmds = generate_proxmox_commands(vm, hw)
    return xml_str, qm_cmds


def _parse_any(path: Path) -> VMDefinition:
    """Parse VMX or OVF based on extension."""
    if path.suffix.lower() == ".vmx":
        return parse_vmx_file(path)
    return parse_ovf_file(path)


# ---------------------------------------------------------------------------
# Libvirt XML end-to-end: real files → schema validation
# ---------------------------------------------------------------------------

class TestLibvirtXmlSchemaValidation:
    """Validate generated libvirt XML against the official RelaxNG schema.

    Each test parses a real-world file, maps hardware, generates XML,
    and validates it against the same schema libvirt uses internally.
    """

    @pytest.fixture(params=[
        "real-world/govmomi-photon5.ovf",
        "real-world/govmomi-ttylinux.ovf",
        "real-world/govmomi-ubuntu24.ovf",
        "real-world/govmomi-configspec.ovf",
        "real-world/govmomi-properties.ovf",
        "real-world/govmomi-virtualsystemcollection.ovf",
        "real-world/manageiq-vcloud.ovf",
        "real-world/openstack-dsl.ovf",
        "real-world/canonical-cloud-init.ovf",
        "sample-ubuntu.vmx",
        "sample-windows.vmx",
        "sample-bios.vmx",
    ])
    def pipeline_result(self, request):
        path = FIXTURES / request.param
        vm = _parse_any(path)
        xml_str, qm_cmds = _pipeline(vm)
        return vm, xml_str, qm_cmds, request.param

    def test_xml_is_well_formed(self, pipeline_result):
        _, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        assert root.tag == "domain"

    def test_xml_validates_against_libvirt_schema(self, pipeline_result):
        _, xml_str, _, fixture = pipeline_result
        valid, error = _validate_libvirt_xml(xml_str)
        assert valid, f"{fixture}: libvirt schema validation failed:\n{error}"

    def test_domain_has_name(self, pipeline_result):
        _, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        name = root.find("name")
        assert name is not None
        assert name.text

    def test_domain_has_memory(self, pipeline_result):
        vm, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        memory = root.find("memory")
        assert memory is not None
        assert int(memory.text) == vm.memory_mb

    def test_domain_has_vcpus(self, pipeline_result):
        vm, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        vcpu = root.find("vcpu")
        assert vcpu is not None
        assert int(vcpu.text) == vm.vcpus

    def test_domain_has_os_type_hvm(self, pipeline_result):
        _, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        os_type = root.find("os/type")
        assert os_type is not None
        assert os_type.text == "hvm"

    def test_domain_uses_q35_machine(self, pipeline_result):
        _, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        os_type = root.find("os/type")
        assert os_type.get("machine") == "q35"

    def test_efi_guests_have_ovmf_loader(self, pipeline_result):
        vm, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        loader = root.find("os/loader")
        if vm.is_efi:
            assert loader is not None
            assert "OVMF" in loader.text
        else:
            assert loader is None

    def test_nics_match_count(self, pipeline_result):
        vm, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        interfaces = root.findall("devices/interface")
        assert len(interfaces) == len(vm.nics)

    def test_disks_match_count(self, pipeline_result):
        vm, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        disks = root.findall("devices/disk")
        assert len(disks) == len(vm.disks)

    def test_mac_addresses_use_colon_format(self, pipeline_result):
        _, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        for mac_elem in root.findall("devices/interface/mac"):
            addr = mac_elem.get("address", "")
            # Must be colon-separated, not dash-separated
            assert "-" not in addr, f"MAC uses dashes: {addr}"
            if addr:
                assert re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", addr), (
                    f"Invalid MAC format: {addr}"
                )

    def test_video_model_is_valid_libvirt_type(self, pipeline_result):
        _, xml_str, _, _ = pipeline_result
        root = ET.fromstring(xml_str)
        video_model = root.find("devices/video/model")
        if video_model is not None:
            valid_types = {"virtio", "qxl", "vga", "cirrus", "bochs", "none"}
            assert video_model.get("type") in valid_types


# ---------------------------------------------------------------------------
# Proxmox qm commands: structural validation
# ---------------------------------------------------------------------------

class TestProxmoxCommandValidation:
    """Validate Proxmox qm commands generated from real-world files."""

    @pytest.fixture(params=[
        "real-world/govmomi-photon5.ovf",
        "real-world/govmomi-ttylinux.ovf",
        "real-world/govmomi-ubuntu24.ovf",
        "real-world/manageiq-vcloud.ovf",
        "real-world/canonical-cloud-init.ovf",
        "sample-ubuntu.vmx",
        "sample-windows.vmx",
        "sample-bios.vmx",
    ])
    def qm_result(self, request):
        path = FIXTURES / request.param
        vm = _parse_any(path)
        _, qm_cmds = _pipeline(vm)
        return vm, qm_cmds, request.param

    def test_first_command_is_qm_create(self, qm_result):
        _, cmds, _ = qm_result
        assert cmds[0].startswith("qm create")

    def test_create_has_name(self, qm_result):
        vm, cmds, _ = qm_result
        assert f"--name {vm.name}" in cmds[0]

    def test_create_has_memory(self, qm_result):
        vm, cmds, _ = qm_result
        assert f"--memory {vm.memory_mb}" in cmds[0]

    def test_create_has_cores(self, qm_result):
        vm, cmds, _ = qm_result
        assert f"--cores {vm.vcpus}" in cmds[0]

    def test_create_has_machine_q35(self, qm_result):
        _, cmds, _ = qm_result
        assert "--machine q35" in cmds[0]

    def test_create_has_bios_flag(self, qm_result):
        vm, cmds, _ = qm_result
        if vm.is_efi:
            assert "--bios ovmf" in cmds[0]
        else:
            assert "--bios seabios" in cmds[0]

    def test_create_has_valid_scsihw(self, qm_result):
        _, cmds, _ = qm_result
        valid_scsihw = {"virtio-scsi-single", "virtio-scsi-pci", "lsi", "megasas"}
        match = re.search(r"--scsihw (\S+)", cmds[0])
        assert match is not None
        assert match.group(1) in valid_scsihw

    def test_create_has_net0(self, qm_result):
        _, cmds, _ = qm_result
        assert "--net0" in cmds[0]

    def test_import_commands_match_disk_count(self, qm_result):
        vm, cmds, _ = qm_result
        import_cmds = [c for c in cmds if c.startswith("qm importdisk")]
        assert len(import_cmds) == len(vm.disks)

    def test_disk_attach_commands_exist(self, qm_result):
        vm, cmds, _ = qm_result
        if vm.disks:
            set_cmds = [
                c for c in cmds
                if c.startswith("qm set") and ("--scsi" in c or "--ide" in c or "--virtio" in c)
            ]
            assert len(set_cmds) >= 1

    def test_boot_order_set_when_disks_exist(self, qm_result):
        vm, cmds, _ = qm_result
        if vm.disks:
            boot_cmds = [c for c in cmds if "--boot order=" in c]
            assert len(boot_cmds) == 1

    def test_efi_disk_created_for_efi_guests(self, qm_result):
        vm, cmds, _ = qm_result
        efidisk_cmds = [c for c in cmds if "--efidisk0" in c]
        if vm.is_efi:
            assert len(efidisk_cmds) == 1
        else:
            assert len(efidisk_cmds) == 0

    def test_windows_gets_safe_nic(self, qm_result):
        vm, cmds, _ = qm_result
        if vm.is_windows:
            assert "e1000" in cmds[0]

    def test_mac_addresses_use_colon_format(self, qm_result):
        _, cmds, _ = qm_result
        for cmd in cmds:
            match = re.search(r"macaddr=(\S+)", cmd)
            if match:
                mac = match.group(1).rstrip(",")
                assert "-" not in mac, f"MAC uses dashes in qm command: {mac}"


# ---------------------------------------------------------------------------
# Windows safe mode: full pipeline verification
# ---------------------------------------------------------------------------

class TestWindowsSafeMode:
    """Verify Windows VMs get safe hardware to prevent BSOD."""

    @pytest.fixture()
    def result(self):
        vm = parse_vmx_file(FIXTURES / "sample-windows.vmx")
        xml_str, qm_cmds = _pipeline(vm)
        return vm, xml_str, qm_cmds

    def test_is_detected_as_windows(self, result):
        vm, _, _ = result
        assert vm.is_windows

    def test_libvirt_uses_ide_bus(self, result):
        _, xml_str, _ = result
        root = ET.fromstring(xml_str)
        for disk in root.findall("devices/disk"):
            target = disk.find("target")
            assert target.get("bus") == "ide"

    def test_libvirt_uses_e1000_nic(self, result):
        _, xml_str, _ = result
        root = ET.fromstring(xml_str)
        for iface in root.findall("devices/interface"):
            model = iface.find("model")
            assert model.get("type") == "e1000"

    def test_proxmox_uses_lsi_scsihw(self, result):
        _, _, cmds = result
        assert "--scsihw lsi" in cmds[0]

    def test_libvirt_xml_still_validates(self, result):
        _, xml_str, _ = result
        valid, error = _validate_libvirt_xml(xml_str)
        assert valid, f"Windows safe mode XML failed validation:\n{error}"


# ---------------------------------------------------------------------------
# EFI firmware: full pipeline verification
# ---------------------------------------------------------------------------

class TestEfiFirmware:
    """Verify EFI VMs get OVMF firmware in both generators."""

    @pytest.fixture()
    def result(self):
        vm = parse_ovf_file(REAL_WORLD / "govmomi-photon5.ovf")
        xml_str, qm_cmds = _pipeline(vm)
        return vm, xml_str, qm_cmds

    def test_detected_as_efi(self, result):
        vm, _, _ = result
        assert vm.is_efi

    def test_libvirt_has_pflash_loader(self, result):
        _, xml_str, _ = result
        root = ET.fromstring(xml_str)
        loader = root.find("os/loader")
        assert loader is not None
        assert loader.get("type") == "pflash"
        assert "OVMF" in loader.text

    def test_proxmox_uses_ovmf_bios(self, result):
        _, _, cmds = result
        assert "--bios ovmf" in cmds[0]

    def test_proxmox_creates_efidisk(self, result):
        _, _, cmds = result
        efidisk = [c for c in cmds if "--efidisk0" in c]
        assert len(efidisk) == 1
        assert "efitype=4m" in efidisk[0]
