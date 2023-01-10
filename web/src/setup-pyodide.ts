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

export async function setupPyodide(
  onStateChange: (newState: PyodideLoadState) => any
): Promise<Pyodide> {
  onStateChange(PyodideLoadState.LOADING_PYODIDE);
  const pyodide = await loadPyodide();

  onStateChange(PyodideLoadState.INSTALLING_DEPENDENCIES);
  await pyodide.loadPackage('micropip');
  const micropip = pyodide.pyimport('micropip');

  // Install dependencies
  await micropip.install([
    // All `aio-libs` packages have been compiled as pure-Python modules
    './assets/multidict-4.7.6-py3-none-any.whl',
    './assets/yarl-1.8.1-py3-none-any.whl',
    './assets/frozenlist-1.3.1-py3-none-any.whl',
    './assets/aiosignal-1.2.0-py3-none-any.whl',
    './assets/aiohttp-3.8.3-py3-none-any.whl',
    // This one also did not seem to have a wheel despite being pure-Python
    './assets/pure_pcapy3-1.0.1-py3-none-any.whl',
    // Finally, install the main module
    './assets/universal_silabs_flasher-0.0.8-py3-none-any.whl',
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
