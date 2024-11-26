#!/bin/env python3
import os
import json
import datetime

import nixpack
import spack

try:
    from spack.package_base import PackageBase
except ImportError:
    from spack.package import PackageBase

root = nixpack.getVar('out')
name = nixpack.getVar('name')
modtype = nixpack.getVar('modtype')

coreCompilers = [nixpack.NixSpec.get(p, top=False) for p in nixpack.getJson('coreCompilers')]
coreCompilers.append(nixpack.nullCompilerSpec)

modconf = nixpack.getJson('config')
modconf.setdefault('core_compilers', [])
modconf['core_compilers'].extend(str(comp.as_compiler) for comp in coreCompilers)
core_specs = modconf.setdefault('core_specs', [])

cls = spack.modules.module_types[modtype]

class FakePackage(PackageBase):
    extendees = ()
    provided = {}

class FakeSpec(nixpack.NixSpec):
    def __init__(self, desc):
        h = spack.util.hash.b32_hash(json.dumps(desc, sort_keys=True))
        nixspec = {
            'name': f'static-module-{h}',
            'namespace': 'dummy',
            'version': '0',
            'variants': {},
            'flags': {},
            'tests': False,
            'paths': {},
            'depends': desc.get('depends', {}),
            'deptypes': {},
            'patches': []
        }

        prefix = desc.get('prefix', f"/{nixspec['namespace']}/{nixspec['name']}")
        nixspec['extern'] = prefix
        for n, d in nixspec['depends'].items():
            try:
                t = d['deptype']
            except Exception:
                t = ('run',)
            nixspec['deptypes'][n] = t

        super().__init__(nixspec, prefix, True)
        self._package = FakePackage(self)

    def concretize(self):
        self._mark_concrete()

    @property
    def package_class(self):
        return self._package

class ConfigModule:
    "Override per-package configuration normally exposed by BaseConfiguration.module.configuration"
    def __init__(self, module, projection=None):
        self.module = module
        self.projection = projection

    def configuration(self, name):
        conf = self.module.configuration(name)
        if self.projection:
            conf = conf.copy()
            conf['projections'] = {'all': self.projection}
        return conf

    def __getattr__(self, attr):
        return getattr(self.module, attr)

class ModSpec:
    def __init__(self, p):
        if isinstance(p, str) or 'spec' in p:
            self.pkg = p
            p = {}
        else:
            self.pkg = p.get('pkg', None)
        if self.pkg:
            self.spec = nixpack.NixSpec.get(self.pkg)
            if self.spec.nixspec['compiler_spec'] != self.spec.nixspec['name']:
                # override name with the compiler_spec (special nixpack case for compiler class)
                self.spec.name = str(self.spec.as_compiler.name)
        else:
            self.spec = FakeSpec(p)

        if 'name' in p:
            self.spec.name = p['name']
        if 'version' in p:
            self.spec.versions = spack.version.VersionList([spack.version.Version(p['version'])])
        self.default = p.get('default', False)
        self.static = p.get('static', None)
        self.path = p.get('path', None)
        self.environment = p.get('environment', {})
        self.context = p.get('context', {})
        if p.get('core', False):
            core_specs.append(self.spec.format())
        self.projection = p.get('projection')
        self.autoload = p.get('autoload', [])
        self.prerequisites = p.get('prerequisites', [])
        self.postscript = p.get('postscript', '')

    @property
    def writer(self):
        try:
            return self._writer
        except AttributeError:
            self.spec.concretize()
            self._writer = cls(self.spec, name)
            self._writer.conf.module = ConfigModule(self._writer.conf.module, self.projection)
            for t in ('autoload', 'prerequisites'):
                self._writer.conf.conf[t].extend(map(nixpack.NixSpec.get, getattr(self, t)))
            if 'unlocked_paths' in self.context:
                for i, p in enumerate(self.context['unlocked_paths']):
                    if not os.path.isabs(p):
                        self.context['unlocked_paths'][i] = os.path.join(self._writer.layout.arch_dirname, p)
            elif self.spec in coreCompilers:
                # messy hack to prevent core compilers from unlocking themselves (should be handled in spack)
                self.context['unlocked_paths'] = []
            for t in ('environment', 'context'):
                spack.modules.common.update_dictionary_extending_lists(
                        self._writer.conf.conf.setdefault(t, {}),
                        getattr(self, t))
            return self._writer

    @property
    def filename(self):
        layout = self.writer.layout
        if self.path:
            base, name = os.path.split(self.path)
            return os.path.join(layout.arch_dirname, base or 'Core', name) + "." + layout.extension
        else:
            return layout.filename

    def __str__(self):
        try:
            default_format = spack.spec.DEFAULT_FORMAT
        except AttributeError:
            default_format = spack.spec.default_format
        return self.spec.cformat(default_format + ' {prefix}')

    def write(self, fn):
        dn = os.path.dirname(fn)
        if self.static:
            os.makedirs(dn, exist_ok=True)
            content = self.static
            if isinstance(content, dict):
                template = spack.tengine.make_environment().get_template(self.writer.default_template)
                content.setdefault('spec', content)
                content['spec'].setdefault('target', nixpack.basetarget)
                content['spec'].setdefault('name', self.spec.name)
                content['spec'].setdefault('short_spec', 'static module via nixpack')
                content.setdefault('timestamp', datetime.datetime.now())
                content = template.render(content)
            with open(fn, 'x') as f:
                f.write(content)
        else:
            self.writer.write()
            if self.postscript:
                with open(fn, 'a') as f:
                    f.write(self.postscript)
        if self.default:
            bn = os.path.basename(fn)
            os.symlink(bn, os.path.join(dn, "default"))

specs = [ModSpec(p) for p in nixpack.getJson('pkgs')]

config = {
    'prefix_inspections': modconf.pop('prefix_inspections', {}),
    name: {
        'enable': [modtype],
        'roots': { modtype: root },
        modtype: modconf
    }
}
spack.config.set('modules', config, 'nixpack')
spack.repo.PATH.provider_index # precompute

print(f"Generating {len(specs)} {modtype} modules in {root}...")
def write(s):
    fn = s.filename
    print(f"  {os.path.relpath(fn, root)}: {s}")
    s.write(fn)
    return fn

def proc(si):
    return write(specs[si])

if nixpack.cores > 1:
    import multiprocessing
    pool = multiprocessing.Pool(nixpack.cores)
    paths = pool.imap_unordered(proc, range(len(specs)))
    pool.close()
else:
    pool = None
    paths = map(write, specs)

seen = set()
for fn in paths:
    assert fn not in seen, f"Duplicate path: {fn}"
    seen.add(fn)

if pool:
    pool.join()
