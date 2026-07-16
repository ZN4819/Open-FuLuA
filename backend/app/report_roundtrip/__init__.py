"""受控 Word 回收的独立安全内核。"""

from .keys import RoundtripSigningKey, generate_signing_key
from .manifest import (
    ManifestSecurityError,
    build_signed_manifest,
    canonical_json_bytes,
    compute_writable_contract_hash,
    parse_manifest_json,
    verify_signed_manifest,
)
from .package import (
    OpcPackage,
    OpcSecurityError,
    embed_manifest,
    extract_manifest,
    read_safe_opc,
    validate_roundtrip_opc,
)
from .structure import (
    RoundtripStructure,
    StructureSecurityError,
    extract_roundtrip_structure,
    find_unresolved_revisions,
    readonly_document_hash,
    validate_field_instructions,
)

__all__ = [
    "ManifestSecurityError",
    "OpcPackage",
    "OpcSecurityError",
    "RoundtripSigningKey",
    "RoundtripStructure",
    "StructureSecurityError",
    "build_signed_manifest",
    "canonical_json_bytes",
    "compute_writable_contract_hash",
    "embed_manifest",
    "extract_manifest",
    "extract_roundtrip_structure",
    "find_unresolved_revisions",
    "generate_signing_key",
    "parse_manifest_json",
    "read_safe_opc",
    "readonly_document_hash",
    "validate_field_instructions",
    "validate_roundtrip_opc",
    "verify_signed_manifest",
]
