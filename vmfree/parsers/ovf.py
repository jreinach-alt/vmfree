"""OVF/OVA file parser.

OVF (Open Virtualization Format) is an XML-based standard for packaging
virtual machines. VMware exports VMs as either:
  - Standalone .ovf (XML descriptor) + .vmdk files in a directory
  - Packaged .ova (tar archive containing .ovf + .vmdk files)

This module handles both cases, parsing the OVF XML (with its
namespace-heavy structure) into a VMDefinition that the rest of
the pipeline can consume.

Key namespace: http://schemas.dmtf.org/ovf/envelope/1
Also uses:     http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/*
               http://www.vmware.com/schema/ovf
"""

from __future__ import annotations

import contextlib
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from vmfree.models import DiskDefinition, DiskType, Firmware, NICDefinition, VMDefinition

# OVF XML namespaces
NS_OVF_V1 = "http://schemas.dmtf.org/ovf/envelope/1"
NS_OVF_V2 = "http://schemas.dmtf.org/ovf/envelope/2"
NS_RASD = "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
NS_VSSD = "http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
NS_VMW = "http://www.vmware.com/schema/ovf"

# Legacy alias for backwards compatibility
NS_OVF = NS_OVF_V1

# OVF ResourceType constants (CIM standard)
RESOURCE_CPU = "3"
RESOURCE_MEMORY = "4"
RESOURCE_IDE_CONTROLLER = "5"
RESOURCE_SCSI_CONTROLLER = "6"
RESOURCE_ETHERNET = "10"
RESOURCE_DISK_DRIVE = "17"
RESOURCE_SATA_CONTROLLER = "20"


def parse_ovf_file(path: str | Path) -> VMDefinition:
    """Parse an OVF XML file and return a VMDefinition.

    Args:
        path: Path to the .ovf file.

    Returns:
        A fully populated VMDefinition.

    Raises:
        FileNotFoundError: If the OVF file does not exist.
        ValueError: If the file is not valid OVF XML.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"OVF file not found: {path}")

    tree = ET.parse(path)
    root = tree.getroot()
    return _parse_ovf_root(root, path)


def parse_ova_file(path: str | Path) -> VMDefinition:
    """Parse an OVA tar archive and return a VMDefinition.

    Extracts the .ovf descriptor from the tar, parses it, and
    resolves VMDK references relative to the extracted location.

    Args:
        path: Path to the .ova file.

    Returns:
        A fully populated VMDefinition with VMDK paths pointing
        to the extracted files.

    Raises:
        FileNotFoundError: If the OVA file does not exist.
        ValueError: If the OVA doesn't contain a .ovf file.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"OVA file not found: {path}")

    return _extract_and_parse_ova(path)


def parse_ovf_or_ova(path: str | Path) -> VMDefinition:
    """Parse either an OVF or OVA file based on extension.

    Args:
        path: Path to the .ovf or .ova file.

    Returns:
        A fully populated VMDefinition.
    """
    path = Path(path)
    if path.suffix.lower() == ".ova":
        return parse_ova_file(path)
    return parse_ovf_file(path)


# ---------------------------------------------------------------------------
# Internal parsing
# ---------------------------------------------------------------------------

def _detect_ovf_namespace(root: ET.Element) -> str:
    """Detect the OVF namespace from the root element tag.

    Supports both OVF v1 (envelope/1) and OVF v2 (envelope/2).
    Falls back to v1 if the namespace can't be determined.
    """
    tag = root.tag
    if "}" in tag:
        ns = tag.split("}")[0].lstrip("{")
        if ns in (NS_OVF_V1, NS_OVF_V2):
            return ns
    return NS_OVF_V1


def _find_virtual_system(root: ET.Element, ns: str) -> ET.Element | None:
    """Find the first VirtualSystem element, including inside VirtualSystemCollection.

    vCloud vApp exports and multi-VM OVFs wrap VirtualSystem elements
    inside a VirtualSystemCollection container. This function checks
    both direct children and one level of nesting.
    """
    # Try direct child first (standard single-VM OVF)
    vs = root.find(f"{{{ns}}}VirtualSystem")
    if vs is not None:
        return vs

    # Try inside VirtualSystemCollection (vCloud vApps, multi-VM)
    vsc = root.find(f"{{{ns}}}VirtualSystemCollection")
    if vsc is not None:
        vs = vsc.find(f"{{{ns}}}VirtualSystem")
        if vs is not None:
            return vs

    # Fallback: recursive search by local tag name (handles unknown namespaces)
    for elem in root.iter():
        if _local_tag(elem.tag) == "VirtualSystem":
            return elem

    return None


def _parse_ovf_root(root: ET.Element, source_path: Path) -> VMDefinition:
    """Parse an OVF root element into a VMDefinition."""
    # Validate this is an OVF envelope
    tag = root.tag
    local_tag = _local_tag(tag)
    if local_tag != "Envelope":
        raise ValueError(f"Not an OVF file: root element is {tag}")

    # Detect namespace version (v1 or v2)
    ns = _detect_ovf_namespace(root)

    # Extract file references (DiskSection, References)
    file_refs = _extract_file_references(root, ns)
    disk_refs = _extract_disk_references(root, ns)

    # Find the VirtualSystem element (handles VirtualSystemCollection nesting)
    vs = _find_virtual_system(root, ns)
    if vs is None:
        raise ValueError("OVF missing VirtualSystem element")

    # VM name
    name = vs.get(f"{{{ns}}}id", "")
    name_elem = vs.find(f"{{{ns}}}Name")
    if name_elem is None:
        # Fallback: search by local tag name
        for child in vs:
            if _local_tag(child.tag) == "Name" and child.text:
                name = child.text
                break
    elif name_elem.text:
        name = name_elem.text

    # Operating system
    os_section = vs.find(f"{{{ns}}}OperatingSystemSection")
    if os_section is None:
        # Fallback: search by local tag
        for child in vs:
            if _local_tag(child.tag) == "OperatingSystemSection":
                os_section = child
                break
    guest_os = ""
    if os_section is not None:
        vmw_os = os_section.get(f"{{{NS_VMW}}}osType", "")
        if vmw_os:
            guest_os = vmw_os
        elif os_section.get(f"{{{ns}}}id"):
            guest_os = os_section.get(f"{{{ns}}}id", "")
        desc = os_section.find(f"{{{ns}}}Description")
        if desc is None:
            for child in os_section:
                if _local_tag(child.tag) == "Description" and child.text:
                    desc = child
                    break
        if desc is not None and desc.text and not guest_os:
            guest_os = desc.text

    # Virtual hardware section
    vhs = vs.find(f"{{{ns}}}VirtualHardwareSection")
    if vhs is None:
        # Fallback: search by local tag
        for child in vs:
            if _local_tag(child.tag) == "VirtualHardwareSection":
                vhs = child
                break
    if vhs is None:
        raise ValueError("OVF missing VirtualHardwareSection")

    # System info (hardware version, firmware)
    hw_version, firmware = _extract_system_info(vhs, ns)

    # Parse resource items
    vcpus = 1
    memory_mb = 1024
    scsi_controller = ""
    controller_map: dict[str, str] = {}  # instanceID -> controller type
    nics: list[NICDefinition] = []
    disk_items: list[tuple[str, str, int]] = []  # (host_resource, parent, address)

    for item in vhs:
        tag_local = _local_tag(item.tag)

        # OVF v2 uses EthernetPortItem for NICs
        if tag_local == "EthernetPortItem":
            nic = _parse_nic_item(item)
            nics.append(nic)
            continue

        # OVF v2 uses StorageItem for disks — skip for now
        # (these use ResourceType 31 with inline capacity, not disk references)
        if tag_local != "Item":
            continue

        resource_type = _get_rasd(item, "ResourceType")
        if not resource_type:
            continue

        if resource_type == RESOURCE_CPU:
            qty = _get_rasd(item, "VirtualQuantity")
            if qty:
                vcpus = int(qty)

        elif resource_type == RESOURCE_MEMORY:
            qty = _get_rasd(item, "VirtualQuantity")
            units = _get_rasd(item, "AllocationUnits") or ""
            if qty:
                memory_mb = _parse_memory(int(qty), units)

        elif resource_type == RESOURCE_SCSI_CONTROLLER:
            instance_id = _get_rasd(item, "InstanceID")
            sub_type = _get_rasd(item, "ResourceSubType") or ""
            if instance_id:
                controller_map[instance_id] = sub_type.lower()
                if not scsi_controller:
                    scsi_controller = sub_type.lower()

        elif resource_type == RESOURCE_ETHERNET:
            nic = _parse_nic_item(item)
            nics.append(nic)

        elif resource_type == RESOURCE_DISK_DRIVE:
            host_res = _get_rasd(item, "HostResource")
            parent = _get_rasd(item, "Parent") or ""
            address = _get_rasd(item, "AddressOnParent") or "0"
            if host_res:
                disk_items.append((host_res, parent, int(address)))

    # Resolve disks
    disks = _resolve_disks(
        disk_items, disk_refs, file_refs, source_path.parent, controller_map,
    )

    return VMDefinition(
        name=name,
        guest_os=guest_os,
        memory_mb=memory_mb,
        vcpus=vcpus,
        firmware=firmware,
        disks=disks,
        nics=nics,
        scsi_controller=scsi_controller,
        display="svga",
        source_file=source_path,
        hardware_version=hw_version,
    )


def _extract_file_references(root: ET.Element, ns: str = NS_OVF_V1) -> dict[str, str]:
    """Extract References/File elements: {id: href}."""
    refs: dict[str, str] = {}
    references = root.find(f"{{{ns}}}References")
    if references is None:
        # Fallback: search by local tag
        for child in root:
            if _local_tag(child.tag) == "References":
                references = child
                break
    if references is None:
        return refs
    for file_elem in references:
        if _local_tag(file_elem.tag) != "File":
            continue
        file_id = file_elem.get(f"{{{ns}}}id", "")
        href = file_elem.get(f"{{{ns}}}href", "")
        if file_id and href:
            refs[file_id] = href
    return refs


def _extract_disk_references(
    root: ET.Element, ns: str = NS_OVF_V1,
) -> dict[str, tuple[str, int]]:
    """Extract DiskSection/Disk elements: {diskId: (fileRef, capacity_bytes)}."""
    disks: dict[str, tuple[str, int]] = {}
    disk_section = root.find(f"{{{ns}}}DiskSection")
    if disk_section is None:
        # Fallback: search by local tag
        for child in root:
            if _local_tag(child.tag) == "DiskSection":
                disk_section = child
                break
    if disk_section is None:
        return disks
    for disk_elem in disk_section:
        if _local_tag(disk_elem.tag) != "Disk":
            continue
        disk_id = disk_elem.get(f"{{{ns}}}diskId", "")
        file_ref = disk_elem.get(f"{{{ns}}}fileRef", "")
        capacity = disk_elem.get(f"{{{ns}}}capacity", "0")
        cap_units = disk_elem.get(f"{{{ns}}}capacityAllocationUnits", "byte")

        cap_bytes = _parse_capacity(capacity, cap_units)

        if disk_id:
            disks[disk_id] = (file_ref, cap_bytes)
    return disks


def _parse_capacity(capacity: str, units: str) -> int:
    """Convert capacity + units to bytes."""
    try:
        cap_val = int(capacity)
    except ValueError:
        return 0

    units_lower = units.lower().strip()
    if "giga" in units_lower or "gb" in units_lower or "byte * 2^30" in units_lower:
        return cap_val * (1024 ** 3)
    if "mega" in units_lower or "mb" in units_lower or "byte * 2^20" in units_lower:
        return cap_val * (1024 ** 2)
    if "kilo" in units_lower or "kb" in units_lower or "byte * 2^10" in units_lower:
        return cap_val * 1024
    # Default: bytes
    return cap_val


def _parse_memory(value: int, units: str) -> int:
    """Convert a memory value + units string to megabytes.

    Handles OVF allocation units like:
      - "byte * 2^20" (OVF v1 with spaces)
      - "byte*2^30" (OVF v2 without spaces)
      - "MegaBytes", "GigaBytes", "GB", "MB"
    """
    u = units.lower().replace(" ", "")
    if "2^30" in u or "giga" in u or "gb" in u:
        return value * 1024
    if "2^20" in u or "mega" in u or "mb" in u:
        return value
    if "2^10" in u or "kilo" in u or "kb" in u:
        return max(1, value // 1024)
    # Default: assume megabytes (most common in OVF)
    return value


def _extract_system_info(
    vhs: ET.Element, ns: str = NS_OVF_V1,
) -> tuple[int, Firmware]:
    """Extract hardware version and firmware from VirtualHardwareSection."""
    hw_version = 0
    firmware = Firmware.BIOS

    system = vhs.find(f"{{{ns}}}System")
    if system is None:
        # Fallback: search by local tag
        for child in vhs:
            if _local_tag(child.tag) == "System":
                system = child
                break
    if system is None:
        return hw_version, firmware

    vssd_type = system.find(f"{{{NS_VSSD}}}VirtualSystemType")
    if vssd_type is not None and vssd_type.text:
        # Format: "vmx-21" or "virtualbox-2.2"
        text = vssd_type.text
        if "vmx-" in text:
            with contextlib.suppress(ValueError, IndexError):
                hw_version = int(text.split("vmx-")[1].split()[0])

    # Check for firmware/boot type
    for item in vhs:
        tag_local = _local_tag(item.tag)
        if tag_local != "Item":
            continue
        desc = _get_rasd(item, "Description")
        elem_name = _get_rasd(item, "ElementName")
        combined = f"{desc} {elem_name}".lower()
        if "efi" in combined or "uefi" in combined:
            firmware = Firmware.EFI

    # Check vmw:Config for firmware
    for config in vhs.findall(f"{{{NS_VMW}}}Config"):
        key = config.get(f"{{{NS_VMW}}}key", "")
        val = config.get(f"{{{NS_VMW}}}value", "")
        if key == "firmware" and val.lower() == "efi":
            firmware = Firmware.EFI

    return hw_version, firmware


def _parse_nic_item(item: ET.Element) -> NICDefinition:
    """Parse a network adapter Item element into a NICDefinition."""
    sub_type = (_get_rasd(item, "ResourceSubType") or "e1000").lower()
    connection = _get_rasd(item, "Connection") or ""

    # VMware maps subtypes like "VmxNet3" to our virtual_dev names
    dev_map = {
        "vmxnet3": "vmxnet3",
        "vmxnet 3": "vmxnet3",
        "e1000": "e1000",
        "e1000e": "e1000e",
        "vlance": "vlance",
        "pcnet32": "vlance",
    }
    virtual_dev = dev_map.get(sub_type, sub_type)

    # Check for MAC address in vmw:Config
    mac = ""
    for config in item.findall(f"{{{NS_VMW}}}Config"):
        key = config.get(f"{{{NS_VMW}}}key", "")
        val = config.get(f"{{{NS_VMW}}}value", "")
        if "mac" in key.lower():
            mac = val

    # Also check the Address element
    address = _get_rasd(item, "Address")
    if address and not mac:
        mac = address

    return NICDefinition(
        virtual_dev=virtual_dev,
        mac_address=mac,
        connection_type="bridged",
        network_name=connection,
    )


def _resolve_disks(
    disk_items: list[tuple[str, str, int]],
    disk_refs: dict[str, tuple[str, int]],
    file_refs: dict[str, str],
    base_dir: Path,
    controller_map: dict[str, str],
) -> list[DiskDefinition]:
    """Resolve disk drive items to DiskDefinition objects."""
    disks: list[DiskDefinition] = []

    for i, (host_resource, parent, address) in enumerate(disk_items):
        # host_resource looks like "ovf:/disk/vmdisk1" or just "vmdisk1"
        disk_id = host_resource.rsplit("/", 1)[-1]

        # Look up in disk references
        file_ref, capacity = disk_refs.get(disk_id, ("", 0))

        # Resolve file path
        filename = file_refs.get(file_ref, "")
        disk_path = str(base_dir / filename) if filename else ""

        # Determine controller
        ctrl_type = controller_map.get(parent, "")
        if "lsi" in ctrl_type or "pvscsi" in ctrl_type or "paravirtual" in ctrl_type:
            controller = "scsi0"
        else:
            controller = "scsi0"

        disks.append(DiskDefinition(
            path=disk_path,
            controller=controller,
            unit=address,
            size_bytes=capacity,
            disk_type=DiskType.STREAM_OPTIMIZED,
            is_boot=(i == 0),
        ))

    return disks


# ---------------------------------------------------------------------------
# OVA extraction
# ---------------------------------------------------------------------------

def _extract_and_parse_ova(ova_path: Path) -> VMDefinition:
    """Extract OVA tar archive and parse the contained OVF."""
    if not tarfile.is_tarfile(str(ova_path)):
        raise ValueError(f"Not a valid OVA (tar) file: {ova_path}")

    with tarfile.open(str(ova_path), "r:*") as tar:
        # Find the .ovf file inside the archive
        ovf_member = None
        for member in tar.getmembers():
            if member.name.lower().endswith(".ovf"):
                ovf_member = member
                break

        if ovf_member is None:
            raise ValueError(f"OVA archive does not contain an .ovf file: {ova_path}")

        # Extract to a temp directory
        tmpdir = tempfile.mkdtemp(prefix="vmfree-ova-")
        tmpdir_path = Path(tmpdir)

        # Security: only extract safe members
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                continue
            tar.extract(member, tmpdir, filter="data")

        ovf_path = tmpdir_path / ovf_member.name
        vm = parse_ovf_file(ovf_path)
        # Update source_file to point to the original OVA
        vm.source_file = ova_path
        return vm


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _local_tag(tag: str) -> str:
    """Strip namespace from an XML tag: {ns}local -> local."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _get_rasd(item: ET.Element, field: str) -> str:
    """Get a RASD field value from an Item element.

    Tries the standard RASD namespace first, then falls back to
    a plain-name search.
    """
    elem = item.find(f"{{{NS_RASD}}}{field}")
    if elem is not None and elem.text:
        return elem.text.strip()

    # Fallback: search by local tag name
    for child in item:
        if _local_tag(child.tag) == field and child.text:
            return child.text.strip()

    return ""
