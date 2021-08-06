#!/bin/env python3
import os
import functools
import shutil
import json

import nixpack
import spack
import llnl.util.lang

# monkeypatch store.layout for the few things we need
class NixLayout():
    metadata_dir = '.spack'
    hidden_file_paths = (metadata_dir,)
    def metadata_path(self, spec):
        return os.path.join(spec.prefix, self.metadata_dir)
    def build_packages_path(self, spec):
        return os.path.join(self.metadata_path(spec), 'repos')
class NixStore():
    layout = NixLayout()
spack.store.store = NixStore()

# disable post_install hooks (sbang, permissions)
def post_install(spec):
    pass
spack.hooks.post_install = post_install

spack.config.set('config:build_stage', [os.environ.pop('NIX_BUILD_TOP')], 'command_line')
cores = int(os.environ.pop('NIX_BUILD_CORES', 0))
if cores > 0:
    spack.config.set('config:build_jobs', cores, 'command_line')

nixLogFd = int(os.environ.pop('NIX_LOG_FD', -1))
nixLogFile = None
if nixLogFd >= 0:
    import json
    nixLogFile = os.fdopen(nixLogFd, 'w')

def nixLog(j):
    if nixLogFile:
        print("@nix", json.dumps(j), file=nixLogFile)

system = os.environ.pop('system')
target = os.environ.pop('target')
platform = os.environ.pop('platform')
archos = os.environ.pop('os')

nullCompiler = spack.spec.CompilerSpec('gcc', '0')
nixStore = os.environ.pop('NIX_STORE')

class NixSpec(spack.spec.Spec):
    # to re-use identical specs so id is reasonable
    specCache = dict()
    nixSpecFile = '.nixpack.spec';

    def __init__(self, nixspec, prefix):
        if isinstance(nixspec, str):
            self.specCache[nixspec] = self
            with open(nixspec, 'r') as sf:
                nixspec = json.load(sf)

        super().__init__()
        self.nixspec = nixspec
        self.name = nixspec['name']
        self.namespace = nixspec['namespace']
        version = nixspec['version']
        self.versions = spack.version.VersionList([spack.version.Version(version)])
        self._set_architecture(target=target, platform=platform, os=archos)
        self._prefix = spack.util.prefix.Prefix(prefix)
        self.external_path = nixspec['extern']

        variants = nixspec['variants']
        assert variants.keys() == self.package.variants.keys(), f"{self.name} has mismatching variants {variants.keys()} vs. {self.packages.variants.keys()}"
        for n, s in variants.items():
            if isinstance(s, bool):
                v = spack.variant.BoolValuedVariant(n, s)
            elif isinstance(s, list):
                v = spack.variant.MultiValuedVariant(n, s)
            elif isinstance(s, dict):
                v = spack.variant.MultiValuedVariant(n, [k for k,v in s.items() if v])
            else:
                v = spack.variant.SingleValuedVariant(n, s)
            self.variants[n] = v
        self.tests = nixspec['tests']
        self.paths = {n: os.path.join(prefix, p) for n, p in nixspec['paths'].items()}
        self.compiler = nullCompiler
        self._as_compiler = None
        # would be nice to use nix hash, but nix and python use different base32 alphabets
        #if not nixspec['extern'] and prefix.startswith(nixStore):
        #    self._hash, nixname = prefix[len(nixStore):].lstrip('/').split('-', 1)

        for n, d in list(nixspec['depends'].items()):
            if not d:
                continue
            if isinstance(d, str):
                key = d
            else:
                # extern: name + prefix should be enough
                key = f"{d['name']}:{d['out']}"
            try:
                spec = self.specCache[key]
            except KeyError:
                if isinstance(d, str):
                    spec = NixSpec(os.path.join(d, self.nixSpecFile), d)
                else:
                    spec = NixSpec(d['spec'], d['out'])
                self.specCache[key] = spec
            dtype = nixspec['deptypes'][n]
            if n == 'compiler':
                self.compiler_spec = spec
                self.compiler = spec.as_compiler
            else:
                self._add_dependency(spec, tuple(dtype))
            if not ('link' in dtype or 'run' in dtype):
                # trim build dep references
                del nixspec['depends'][n]

        for f in self.compiler_flags.valid_compiler_flags():
            self.compiler_flags[f] = []

        if nixspec['patches']:
            patches = self.package.patches.setdefault(spack.directives.make_when_spec(True), [])
            for i, p in enumerate(nixspec['patches']):
                patches.append(spack.patch.FilePatch(self.package, p, 1, '.', ordering_key = ('~nixpack', i)))
            spack.repo.path.patch_index.update_package(self.fullname)

    @property
    def as_compiler(self):
        if not self._as_compiler:
            self._as_compiler = spack.spec.CompilerSpec(self.name, self.versions)
        return self._as_compiler

os.environ.pop('name')
nixspec = os.environ.pop('specPath')
spec = NixSpec(nixspec, os.environ.pop('out'))
if spec.compiler != nullCompiler:
    spack.config.set('compilers', [{'compiler': {
        'spec': str(spec.compiler),
        'paths': spec.compiler_spec.paths,
        'modules': [],
        'operating_system': spec.compiler_spec.architecture.os,
        'target': system.split('-', 1)[0],
    }}], 'command_line')
conc = spack.concretize.Concretizer()
conc.adjust_target(spec)
spack.spec.Spec.inject_patches_variant(spec)
spec._mark_concrete()

pkg = spec.package
print(spec.tree(cover='edges', format=spack.spec.default_format + ' {prefix}'))

opts = {
        'install_deps': False,
        'verbose': False,
        'tests': spec.tests,
    }

# create and stash some metadata
spack.build_environment.setup_package(pkg, True)
os.makedirs(pkg.metadata_dir, exist_ok=True)
with open(os.path.join(spec.prefix, NixSpec.nixSpecFile), 'w') as sf:
    json.dump(spec.nixspec, sf)

# log build phases to nix
def wrapPhase(p, f, *args):
    nixLog({'action': 'setPhase', 'phase': p})
    return f(*args)

for pn, pa in zip(pkg.phases, pkg._InstallPhase_phases):
    pf = getattr(pkg, pa)
    setattr(pkg, pa, functools.partial(wrapPhase, pn, pf))

# do the actual install
spack.installer.build_process(pkg, opts)

# cleanup spack logs (to avoid spurious references)
#shutil.rmtree(pkg.metadata_dir)
