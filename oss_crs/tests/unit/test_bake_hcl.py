# SPDX-License-Identifier: MIT
"""Tests for evaluating docker-bake.hcl without `buildx bake`."""

import textwrap

import pytest

from oss_crs.src.bake_hcl import parse_bake_hcl


def test_parse_bake_hcl_resolves_variables_and_tags_function(tmp_path):
    hcl = tmp_path / "docker-bake.hcl"
    hcl.write_text(
        textwrap.dedent(
            """\
            variable "REGISTRY" {
              default = "ghcr.io/team-atlanta"
            }
            variable "VERSION" {
              default = "latest"
            }
            variable "CLAUDE_CODE_CLI_VERSION" {
              default = "2.1.168"
            }
            function "tags" {
              params = [name]
              result = [
                "${REGISTRY}/${name}:${VERSION}",
                "${name}:latest"
              ]
            }
            group "default" {
              targets = ["my-base"]
            }
            target "my-base" {
              context = "."
              dockerfile = "oss-crs/base.Dockerfile"
              tags = tags("my-base")
              args = {
                CLI_VER = "2.0"
                CLAUDE_CODE_CLI_VERSION = CLAUDE_CODE_CLI_VERSION
              }
            }
            """
        )
    )
    plan = parse_bake_hcl(hcl, {"VERSION": "1.2.3"})
    assert plan["group"]["default"] == {"targets": ["my-base"]}
    t = plan["target"]["my-base"]
    assert t["dockerfile"] == "oss-crs/base.Dockerfile"
    assert t["tags"] == ["ghcr.io/team-atlanta/my-base:1.2.3", "my-base:latest"]
    assert t["args"] == {"CLI_VER": "2.0", "CLAUDE_CODE_CLI_VERSION": "2.1.168"}


def test_parse_bake_hcl_resolves_blocks_in_any_order(tmp_path):
    hcl = tmp_path / "docker-bake.hcl"
    hcl.write_text(
        textwrap.dedent(
            """\
            target "my-base" {
              tags = tags("my-base")
              args = { BASE = BASE_IMAGE }
            }
            group "default" {
              targets = ["${PREFIX}-base"]
            }
            variable "PREFIX" {
              default = "my"
            }
            variable "BASE_IMAGE" {
              default = ref("base")
            }
            function "tags" {
              params = [name]
              result = [ref(name)]
            }
            function "ref" {
              params = [name]
              result = "${REGISTRY}/${name}:${VERSION}"
            }
            variable "VERSION" {
              default = "latest"
            }
            variable "REGISTRY" {
              default = "ghcr.io/${ORG}"
            }
            variable "ORG" {
              default = "team"
            }
            """
        )
    )
    plan = parse_bake_hcl(hcl, {"VERSION": "1.2.3"})
    assert plan["group"]["default"] == {"targets": ["my-base"]}
    t = plan["target"]["my-base"]
    assert (t["context"], t["dockerfile"]) == (".", "Dockerfile")
    assert t["tags"] == ["ghcr.io/team/my-base:1.2.3"]
    assert t["args"] == {"BASE": "ghcr.io/team/base:1.2.3"}


def test_parse_bake_hcl_env_overrides_self_referencing_default(tmp_path):
    hcl = tmp_path / "docker-bake.hcl"
    hcl.write_text(
        textwrap.dedent(
            """\
            variable "A" {
              default = "${B}"
            }
            variable "B" {
              default = "${A}"
            }
            target "t" {
              tags = ["${A}"]
            }
            """
        )
    )
    assert parse_bake_hcl(hcl, {"B": "x"})["target"]["t"]["tags"] == ["x"]
    with pytest.raises(ValueError, match="refers to itself"):
        parse_bake_hcl(hcl, {})
