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

interface PythonPackageSpec {
  // The PyPI package name can differ from the module name
  package: string;
  modules: string[];
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
    { package: 'aiohttp', modules: ['aiohttp'], version: '999.0.0' },
    { package: 'pure_pcapy3', modules: ['pure_pcapy'], version: '1.0.1' },
    {
      package: 'cryptography',
      modules: [
        'cryptography.hazmat.primitives.ciphers',
        'cryptography.hazmat.primitives.ciphers.modes',
        'cryptography.hazmat.primitives.ciphers.algorithms',
      ],
      version: '999.0.0',
    },
  ] as PythonPackageSpec[]) {
    micropip.add_mock_package.callKwargs({
      name: spec.package,
      version: spec.version,
      persistent: true,
      modules: new Map(
        // Allows recursive submodule imports
        spec.modules.map(module => [
          module,
          '__getattr__ = __import__("unittest.mock").mock.MagicMock()',
        ])
      ),
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
