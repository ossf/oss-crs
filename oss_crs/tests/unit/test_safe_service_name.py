# SPDX-License-Identifier: MIT
"""Unit tests for _safe_service_name and service log helpers in CRSCompose."""

import sys
from unittest.mock import MagicMock

# Polyfill fcntl on platforms (such as Windows) where it is missing
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = MagicMock()

from oss_crs.src.crs_compose import CRSCompose


class TestSafeServiceName:
    def test_standard_service_name(self):
        assert CRSCompose._safe_service_name("fuzzer_1") == "fuzzer_1"
        assert CRSCompose._safe_service_name("builder.service-a") == "builder.service-a"

    def test_path_traversal_sanitized(self):
        # Traversal attempts should be converted to hyphens and stripped of leading/trailing dots/hyphens
        assert CRSCompose._safe_service_name("../../escape/service") == "escape-service"
        assert CRSCompose._safe_service_name("..") == "unknown-service"

    def test_special_characters_sanitized(self):
        assert CRSCompose._safe_service_name("svc@name#1$2") == "svc-name-1-2"

    def test_empty_or_all_dots_and_separators(self):
        assert CRSCompose._safe_service_name("") == "unknown-service"
        assert CRSCompose._safe_service_name("...") == "unknown-service"
        assert CRSCompose._safe_service_name("---") == "unknown-service"
