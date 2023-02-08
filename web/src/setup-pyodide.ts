import dummyModuleLoaderPy from './dummy_module_loader.py';
import webSerialTransportPy from './webserial_transport.py';

export type Pyodide = any;

export enum PyodideLoadState {
  LOADING_PYODIDE = 0,
  INSTALLING_DEPENDENCIES = 1,
  INSTALLING_TRANSPORT = 2,
  READY = 3,
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

function writeModule(pyodide: Pyodide, moduleName: string, contents: string) {
  pyodide.FS.mkdir('modules');
  pyodide.FS.writeFile(`modules/${moduleName}.py`, contents, {
    encoding: 'utf8',
  });
}

interface PythonPackageSpec {
  // The PyPI package name can differ from the module name
  package: string;
  module: string;
  version: string;
}

export async function setupPyodide(
  onStateChange: (newState: PyodideLoadState) => any,
  flasherPackagePath?: string
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
    flasherPackagePath || 'universal-silabs-flasher',
  ]);

  onStateChange(PyodideLoadState.INSTALLING_TRANSPORT);
  // Prepare the Python path for external modules
  pyodide.runPython(`
    import coloredlogs
    coloredlogs.install(level="DEBUG")

    import sys
    sys.path.insert(0, "./modules/")
  `);

  // Include our webserial transport
  writeModule(pyodide, 'webserial_transport', webSerialTransportPy);

  // And run it
  pyodide.runPython(`
    import webserial_transport
    webserial_transport.patch_pyserial()
  `);
  onStateChange(PyodideLoadState.READY);

  return pyodide;
}
