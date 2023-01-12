export type Pyodide = any;

export enum PyodideLoadState {
  LOADING_PYODIDE,
  INSTALLING_DEPENDENCIES,
  INSTALLING_TRANSPORT,
  READY,
}

async function loadPyodide(): Promise<Pyodide> {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');

    script.onerror = e => reject(e);
    script.onload = async () => {
      const pyodide = await (window as any).loadPyodide();
      resolve(pyodide);
    };

    script.src = 'https://cdn.jsdelivr.net/pyodide/v0.22.0/full/pyodide.js';
    document.body.appendChild(script);
  });
}

async function downloadModule(
  pyodide: Pyodide,
  moduleName: string,
  path: string
) {
  console.debug('Downloading module', moduleName, 'from', path);

  const contents = await (await fetch(path)).text();

  pyodide.FS.mkdir('modules');
  pyodide.FS.writeFile(`modules/${moduleName}.py`, contents, {
    encoding: 'utf8',
  });
}

const dummyModuleLoaderPy = `
import sys
import unittest.mock

class DummyFinder:
    """
    Combined module loader and finder that recursively returns Mock objects.
    """

    def __init__(self, name):
        self.name = name

    def find_module(self, fullname, path=None):
        if fullname.startswith(self.name):
            return self

    def load_module(self, fullname):
        return sys.modules.setdefault(fullname, unittest.mock.MagicMock(__path__=[]))

sys.meta_path.append(DummyFinder(__name__))
`.trim();

interface PythonPackageSpec {
  // The PyPI package name can differ from the module name
  package: string;
  module: string;
  version: string;
}

export async function setupPyodide(
  onStateChange: (newState: PyodideLoadState) => any
): Promise<Pyodide> {
  onStateChange(PyodideLoadState.LOADING_PYODIDE);
  const pyodide = await loadPyodide();

  onStateChange(PyodideLoadState.INSTALLING_DEPENDENCIES);
  await pyodide.loadPackage('micropip');
  const micropip = pyodide.pyimport('micropip');

  // Mock a few packages to significantly reduce dependencies
  for (const spec of [
    { package: 'aiohttp', module: 'aiohttp', version: '999.0.0' },
    { package: 'pure_pcapy3', module: 'pure_pcapy', version: '1.0.1' },
    { package: 'cryptography', module: 'cryptography', version: '999.0.0' },
  ] as PythonPackageSpec[]) {
    micropip.add_mock_package.callKwargs({
      name: spec.package,
      version: spec.version,
      persistent: true,
      modules: new Map([[spec.module, dummyModuleLoaderPy]]),
    });
  }

  // Install dependencies
  await micropip.install([
    'zigpy>=0.53.1',
    './assets/wheels/universal_silabs_flasher-0.0.8-py3-none-any.whl',
  ]);

  onStateChange(PyodideLoadState.INSTALLING_TRANSPORT);
  // Prepare the Python path for external modules
  pyodide.runPython(`
    import coloredlogs
    coloredlogs.install(level="DEBUG")

    import sys
    sys.path.insert(0, "./modules/")
  `);

  // Download our webserial transport
  await downloadModule(
    pyodide,
    'webserial_transport',
    './assets/webserial_transport.py'
  );

  // And run it
  pyodide.runPython(`
    import webserial_transport
    webserial_transport.patch_pyserial()
  `);
  onStateChange(PyodideLoadState.READY);

  return pyodide;
}
