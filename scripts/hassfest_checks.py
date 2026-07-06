#!/usr/bin/env python3
"""Local hassfest-style sanity checks.

Run via the pre-commit framework (`.pre-commit-config.yaml`) or directly:

    python scripts/hassfest_checks.py manifest-order
    python scripts/hassfest_checks.py strings-shape
    python scripts/hassfest_checks.py all

These mirror the structural checks the upstream `home-assistant/hassfest`
container enforces via JSON schema validation. We do NOT reimplement the
schema — we focus on the three categories of breakage we've actually
caused in this repo:

1. manifest.json key order — hassfest requires `domain`, then `name`,
   then alphabetical. Manual JSON editing drops the ordering invariant.
2. strings.json / translations/*.json shape — hassfest schema rejects
   unknown top-level keys (`reauth`, `reconfigure`, `entity`) and
   any string containing a literal URL.
3. strict JSON parse — if a comma is missing, every subsequent check
   fails with a confusing error. Validate parses first.

Exit code 0 = clean, 1 = one or more checks failed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = ROOT / "custom_components" / "traefik"
TRANSLATIONS = INTEGRATION / "translations"
URL_RE = re.compile(r"https?://|wss?://", re.IGNORECASE)

# hassfest's MANIFEST_KEY_ORDER requirement, mirrored here.
# After the first two keys ("domain", "name"), the rest must be alphabetical.
MANIFEST_FIRST_KEYS = ("domain", "name")


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise SystemExit(f"[hassfest] {path.relative_to(ROOT)} is not valid JSON: {err}") from err


def check_manifest_order() -> list[str]:
    errors: list[str] = []
    path = INTEGRATION / "manifest.json"
    manifest = _load_json(path)
    if not isinstance(manifest, dict):
        return [f"{path}: expected object at root, got {type(manifest).__name__}"]
    keys = list(manifest.keys())
    expected_prefix = list(MANIFEST_FIRST_KEYS)
    actual_prefix = keys[: len(expected_prefix)]
    if actual_prefix != expected_prefix:
        errors.append(
            f"{path}: first keys must be {expected_prefix!r}, got {actual_prefix!r}"
        )
    rest = keys[len(expected_prefix) :]
    rest_sorted = sorted(rest)
    if rest != rest_sorted:
        errors.append(f"{path}: keys after {expected_prefix!r} must be alphabetical. Got {rest!r}, expected {rest_sorted!r}")
    return errors


def _check_strings_shape(path: Path) -> list[str]:
    errors: list[str] = []
    obj = _load_json(path)
    if not isinstance(obj, dict):
        return [f"{path}: expected object at root"]
    forbidden_top = {"entity", "reauth", "reconfigure"}
    bad_top = forbidden_top & obj.keys()
    if bad_top:
        errors.append(f"{path}: top-level keys {sorted(bad_top)!r} not allowed (HA derives these from config.step.* automatically)")
    config_obj = obj.get("config")
    if isinstance(config_obj, dict):
        step_obj = config_obj.get("step")
        if isinstance(step_obj, dict):
            for step_id, step_body in step_obj.items():
                if not isinstance(step_body, dict):
                    continue
                for label_key, label_val in (step_body.get("data") or {}).items():
                    if isinstance(label_val, str) and URL_RE.search(label_val):
                        errors.append(
                            f"{path}: config.step.{step_id}.data.{label_key} contains a literal URL — hassfest rejects URLs in string values (use data_description or description_placeholders via the form instead)"
                        )
        error_obj = config_obj.get("error")
        if isinstance(error_obj, dict):
            for err_key, err_val in error_obj.items():
                if isinstance(err_val, str) and URL_RE.search(err_val):
                    errors.append(
                        f"{path}: config.error.{err_key} contains a literal URL"
                    )
    return errors


def check_strings_shape() -> list[str]:
    paths = [INTEGRATION / "strings.json"]
    if TRANSLATIONS.is_dir():
        paths.extend(sorted(TRANSLATIONS.glob("*.json")))
    errors: list[str] = []
    for path in paths:
        errors.extend(_check_strings_shape(path))
    return errors


def main() -> int:
    targets = sys.argv[1:] or ["all"]
    checks = {
        "manifest-order": check_manifest_order,
        "strings-shape": check_strings_shape,
        "all": lambda: check_manifest_order() + check_strings_shape(),
    }
    errors: list[str] = []
    for t in targets:
        fn = checks.get(t)
        if fn is None:
            errors.append(f"unknown check: {t} (use one of {sorted(checks)!r})")
        else:
            errors.extend(fn())
    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        return 1
    print("✅ hassfest-style checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
