# nixpack = [nix](https://nixos.org/nix)+[spack](https://spack.io/)

A hybrid of the [nix package manager](https://github.com/NixOS/nix) and [spack](https://github.com/spack/spack) where nix (without nixpkgs) is used to solve and manage packages, using the package repository, builds, and modules from spack.

If you love nix's expressiveness and efficiency, but don't need the purity of nixpkgs (in the sense of independence from the host system)... if you like the spack packages and package.py format, but are tired of managing roots and concretizations, this may be for you.
Nix on the outside, spack on the inside.

## Usage

1. Install and configure [nix](https://nixos.org/manual/nix/stable/#chap-installation), sufficient to build derivations.
1. Edit (or copy) [`default.nix`](default.nix).
   - It's recommended to set `packs.spackSrc.rev` to a fixed version of spack.  Changing the spack version requires all packages to be rebuilt.  If you want to update individual packages without a rebuild, you can put them in `spack/repo/packages` (or another repo in `packs.repos`).
   - Set `packs.os` and `packs.global.target`.
   - Set `packs.spackConfig.config.source_cache` and add any other custom spack config you want (nixpack ignores system and user spack config for purity, but will load default and site config from the spack repo itself).
   - Set `bootstrapPacks.package.compiler` to a pre-existing (system/external) compiler to be used to bootstrap.
   - Set `packs.package.gcc` to choose your default compiler, or `packs.package.compiler` to use something other than gcc.
   - Add any other package preferences to `packs.package` (versions, variants, virtual providers, etc.)
   - See `packs.global.fixedDeps`: by default multiple different instances of any given package may be built in order to satisfy requirements, but you may prefer to force only one version of each package, which will improve performance and build times.
1. Run `nix-build -A pkgs.foo` to build the spack package `foo`.
1. To build modules, configure `packs.mods` and run `nix-build -A mods`.

## Flatiron Specific

We have our local Flatiron-specific configuration and repositories in [`fi`](fi), complete with views and modules, some of which may be more generally useful or at least helpful reference or template for creating a full working system.
See the [README](fi/README.md) in that directory for more information.

## Compatibility

nixpack uses an unmodified checkout of spack (as specified in `spackSrc`), and should work with other forks as well.
However, it makes many assumptions about the internals of spack builds, so may not work on much older (or newer) versions.

## Implementation and terminology

In nixpkgs, there's mainly the concept of package, and arguments that can be overridden.
In spack, there are packages and specs, and "spec" is used in many different ways.
We define a few more specific concepts to merge the two.

### package descriptor

The metadata for a spack package.
These are generated by [`spack/generate.py`](spack/generate.py) from the spack repo `package.py`s and loaded into `packs.repo`.
They look like this:

```nix
example = {
  namespace = "builtin";
  version = ["2.0" "1.2" "1.0"]; # in decreasing order of preference
  variants = {
    flag = true;
    option = ["a" "b" "c"]; # single-valued, first is default
    multi = {
      a = true;
      b = false;
    };
  };
  depends = {
    /* package preferences for dependencies (see below) */
    compiler = { # added implicitly if missing
      deptype = ["build" "link"];
    };
    deppackage = {
      version = "1.5:2.1";
      deptype = ["run" "test"];
    };
    notused = null;
  };
  provides = {
    virtual = "2:";
  };
  paths = {}; # paths to tools provided by this package (like `cc` for compilers)
  patches = []; # extra patches to apply (in addition to those in spack)
  conflicts = []; # any conflicts (non-empty means invalid)
};
```

Most things default to empty.
This is not a complete build description, just the metadata necessary to resolve dependencies (concretize).
In practice, these are constructed as functions that take a resolved package spec as an argument, so that dependencies and such be conditional on a specific version and variants.

You can build the repo using `nix-build -A spackRepo` (and see `result`).

### package preferences

Constraints for a package that come from the user, or a depending package.
These are used in package descriptor depends and in user global and per-package preferences.
They look similar to package descriptors and can be used to override or constrain some of those values.

```nix
example = {
  version = "1.3:1.5";
  variants = {
    flag = true;
    option = "b";
    /* multi options can be specified as list of trues or explicitly */
    multi = ["a"];
    multi = {
      a = true;
      b = false;
    };
  };
  depends = {
    compiler = {
      name = "clang"; # use clang as the compiler virtual provider
    };
    deppackage = {
      version = ... # use a specific version for a dependency
    };
    virtualdep = {
      name = "provider";
      version = ...;
      ...
    };
    # dependencies can also be set to a specific package:
    builddep = packs.pkgs.builddep;
  };
  provides = {
    virtual = "version"; # this requires that this package provides virtual (not that it does)
  };
  patches = []; # extra patches to apply (in additon to those in the descriptor)
  extern = "/opt/local/mypackage"; # a prefix string or derivation (e.g., nixpkgs package) for an external installation (overrides depends)
  fixedDeps = false; # only use user preferences to resolve dependencies (see default.nix)
  target = "microarch"; # defaults to currentSystem (e.g., x86_64)
  verbose = true; # to enable nix-build -Q and nix-store -l (otherwise only spack keeps build logs)
  tests = false; # run tests (not implemented)
  resolver = ...; # where to find dependent packages (see default.nix)
};
```

### package spec

A resolved (concrete) package specifier created by applying (optional) package preferences to a package descriptor.
This looks just like a package descriptior but with concrete values.
It also includes settings from prefereces like `extern` and `target`.

### package

An actual derivation.
These contain a `spec` metadata attribute.
They also have a `withPrefs` function that can be used to make a new version of this package with updated prefs (unless they are extern).

### compiler

Rather than spack's dedicated `%compiler` concept, we introduce a new virtual "compiler" that all packages depend on and is provided by gcc and llvm (by default).
By setting the package preference for compiler, you determine which compiler to use.

### `packs`

The world, like `nixpkgs`.
It contains `pkgs` with actual packages, as well as `repo`, `view`, `modules`, and other functions.
See [`packs/default.nix`](packs/default.nix) for full details.

You can have one or more `packs` instances.
Each instance is defined by a set of global user preferences, as passed to `import ./packs`.
You can also create additional sets based on an existing one using `packs.withPrefs`.
Thus, difference package sets can have different providers or package settings (like a different compiler, mpi version, blas provider, variants, etc.).

See [`default.nix`](default.nix) for preferences that can be set and their descriptions.

### Bootstrapping

The default compiler is specified in [`default.nix`](default.nix) by `compiler = bootstrapPacks.pkgs.gcc` which means that the compiler used to build everything is `packs` comes from `bootstrapPacks`, and is built with the preferences and compiler defined there.
`bootstrapPacks` in turn specifies a compiler of gcc with `extern` set, i.e., one from the host system.
This compiler is used to build any other bootstrap packages, which are then used to build the main compiler.
You could specify more extern packages in bootstrap to speed up bootstrapping.

You could also add additional bootstrap layers by setting the bootstrap compiler `resolver` to a different set.
You could also replace specific dependencies or packages from a different `packs` set to bootstrap or modify other packages.
