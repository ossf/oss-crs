{
  description = "libCRS — Nix-built Docker image providing libCRS + rsync";
  inputs.nixpkgs.url =
    "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { self, nixpkgs }:
    let
      forAll = f: nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ]
        (system: f nixpkgs.legacyPackages.${system});
    in {
      packages = forAll (pkgs: rec {
        libCRS-pkg = pkgs.python3Packages.buildPythonPackage {
          pname = "libcrs";
          version = "0.1.0";
          src = ./.;
          pyproject = true;
          build-system = [ pkgs.python3Packages.setuptools ];
          dependencies = with pkgs.python3Packages; [ watchdog requests ];
          doCheck = false;
        };

        # A single Nix closure containing Python (with libCRS installed) and
        # rsync.  All three are reachable via <closure>/bin/{libCRS,rsync,python3}.
        libcrs-runtime = pkgs.buildEnv {
          name = "libcrs-runtime";
          paths = [
            (pkgs.python3.withPackages (_: [ libCRS-pkg ]))
            pkgs.rsync
          ];
        };

        # Tools used by libCRS/deps.Dockerfile to export a wheelhouse of
        # libCRS and its locked dependencies (from uv.lock), so CRSs can
        # install the libCRS Python API into their own interpreter offline.
        wheelhouse-tools = pkgs.buildEnv {
          name = "libcrs-wheelhouse-tools";
          paths = [
            (pkgs.python3.withPackages (ps: [ ps.pip ps.setuptools ps.wheel ]))
            pkgs.uv
          ];
        };

        # The runtime closure is consumed by libCRS/deps.Dockerfile, which
        # runs `nix build .#libcrs-runtime` inside a nixos/nix container and
        # copies the closure into the "oss-crs-deps" image via `docker build`.
        default = libcrs-runtime;
      });
    };
}
