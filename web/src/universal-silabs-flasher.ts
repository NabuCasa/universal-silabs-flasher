import { LitElement, html, css, PropertyValues } from 'lit';
import { customElement, state, query } from 'lit/decorators.js';

enum LoadState {
  LOADING_PYODIDE,
  INSTALLING_DEPENDENCIES,
  INSTALLING_TRANSPORT,
  READY,
}

async function loadPyodide(config?: object) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');

    script.onerror = e => reject(e);
    script.onload = async () => {
      resolve(await (window as any).loadPyodide(config));
    };

    script.src = 'https://cdn.jsdelivr.net/pyodide/v0.22.0/full/pyodide.js';
    document.body.appendChild(script);
  });
}

async function readFile(file: Blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = e => reject(e);
    reader.readAsArrayBuffer(file);
  });
}

@customElement('universal-silabs-flasher')
class UniversalSilabsFlasher extends LitElement {
  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-start;
      font-size: 1em;
      background-color: var(--universal-silabs-flasher-background-color);

      padding: 5em;

      max-width: 600px;
      margin-left: auto;
      margin-right: auto;
    }

    main,
    :host > section {
      flex-grow: 1;
      width: 100%;
    }

    li {
      margin-top: 1em;
    }

    .metadata {
      font-size: 0.8em;
    }

    progress {
      width: 100%;
    }

    .debuglog {
      width: 100%;
      font-size: 0.8em;

      min-height: 100px;
      max-height: 500px;

      cursor: text;
      user-select: text;

      border: 1px solid gray;
      background-color: white;
      border-radius: 1em;

      padding: 1em;
      overflow: auto;
    }

    .stderr {
      color: firebrick;
    }

    img {
      vertical-align: middle;
    }
  `;

  @query('.debuglog')
  private debugLog!: HTMLTextAreaElement;

  @state()
  private loadState: LoadState = LoadState.LOADING_PYODIDE;

  private pyodide?: any;

  @state()
  private selectedFirmware?: any;

  @state()
  private serialPort?: SerialPort;

  @state()
  private uploadProgress?: Number;

  private async downloadModule(moduleName: string, path: string) {
    console.debug('Downloading module', moduleName, 'from', path);

    const contents = await (await fetch(path)).text();

    this.pyodide.FS.mkdir('modules');
    this.pyodide.FS.writeFile(`modules/${moduleName}.py`, contents, {
      encoding: 'utf8',
    });
  }

  private async setupPyodide() {
    this.loadState = LoadState.LOADING_PYODIDE;
    this.pyodide = await loadPyodide({
      stdout: (msg: string) => {
        console.log(msg);

        let div = document.createElement('div');
        div.classList.add('stdout');
        div.textContent = msg;
        this.debugLog.appendChild(div);
      },
      stderr: (msg: string) => {
        console.warn(msg);

        let div = document.createElement('div');
        div.classList.add('stderr');
        div.textContent = msg;
        this.debugLog.appendChild(div);
      },
    });

    this.loadState = LoadState.INSTALLING_DEPENDENCIES;
    await this.pyodide.loadPackage('micropip');
    const micropip = this.pyodide.pyimport('micropip');

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

    this.loadState = LoadState.INSTALLING_TRANSPORT;
    // Prepare the Python path for external modules
    this.pyodide.runPython(`
      import coloredlogs
      coloredlogs.install(level="DEBUG")

      import sys
      sys.path.insert(0, "./modules/")
    `);

    // Download our webserial transport
    await this.downloadModule(
      'webserial_transport',
      './assets/webserial_transport.py'
    );

    // And run it
    this.pyodide.runPython(`
      import webserial_transport
      webserial_transport.patch_pyserial()
    `);
    this.loadState = LoadState.READY;
  }

  protected firstUpdated(changedProperties: PropertyValues): void {
    super.firstUpdated(changedProperties);
    this.setupPyodide();
  }

  private async fileChosen(event: Event) {
    const file = (event.target! as HTMLInputElement).files![0];
    const contents = await readFile(file);

    const { GBLImage } = this.pyodide.pyimport(
      'universal_silabs_flasher.flasher'
    );

    try {
      this.selectedFirmware = GBLImage.from_bytes(this.pyodide.toPy(contents));
    } catch (e) {
      this.selectedFirmware = undefined;
      alert(`Failed to parse image: ${e}`);
    }
  }

  private getFirmwareMetadataString() {
    if (!this.selectedFirmware) {
      return '';
    }

    try {
      return this.selectedFirmware.get_nabucasa_metadata().toString();
    } catch (e) {
      return 'unknown';
    }
  }

  private async selectSerialPort() {
    if (!('serial' in navigator)) {
      alert(
        'Your browser unfortunately does not support Web Serial. Use Chrome or Edge.'
      );
      return;
    }

    try {
      this.serialPort = await navigator.serial.requestPort();
    } catch {
      this.serialPort = undefined;
    }
  }

  private async flashFirmware() {
    this.pyodide
      .pyimport('webserial_transport')
      .set_global_serial_port(this.serialPort);

    const { Flasher } = this.pyodide.pyimport(
      'universal_silabs_flasher.flasher'
    );
    const flasher = Flasher.callKwargs({
      bootloader_baudrate: 115200,
      app_baudrate: 115200,
      device: '/dev/webserial', // the device name is ignored
    });

    await flasher.probe_app_type();
    await flasher.enter_bootloader();

    await flasher.flash_firmware.callKwargs(this.selectedFirmware, {
      progress_callback: (current: number, total: number) => {
        console.log('Firmware upload progress', current, total);
        this.uploadProgress = (100.0 * current) / total;
      },
    });
  }

  render() {
    const header = html`
      <h1>
        <img
          src="https://skyconnect.home-assistant.io/static/skyconnect_header.png"
        />
        SkyConnect Flasher
      </h1>
    `;

    if (this.loadState === LoadState.LOADING_PYODIDE) {
      return html`${header}Loading Pyodide (this may take a minute)...`;
    }
    if (this.loadState === LoadState.INSTALLING_DEPENDENCIES) {
      return html`${header}Installing Python dependencies (this may take a
      minute)...`;
    }
    if (this.loadState === LoadState.INSTALLING_TRANSPORT) {
      return html`${header}Setting up serial transport...`;
    }
    if (this.loadState === LoadState.READY) {
      return html`
        ${header}
        <p>
          Flash new firmware to your SkyConnect! In case something doesn't work,
          just unplug the SkyConnect and plug it back in.
        </p>

        <p>
          Note: on macOS, make sure to select <code>cu.SLAB_USBtoUART</code> as
          the serial port. <code>cu.usbserial*10</code> does not work.
        </p>

        <main>
          <ol>
            <li>
              <label
                >Choose <code>.gbl</code> firmware
                <input
                  type="file"
                  accept=".gbl"
                  @change=${this.fileChosen}
              /></label>

              ${this.selectedFirmware
                ? html`
                    <div class="metadata">
                      <code>${this.getFirmwareMetadataString()}</code>
                    </div>
                  `
                : ''}
            </li>

            <li>
              <label
                >Connect to your SkyConnect
                <button
                  ?disabled=${!this.selectedFirmware}
                  @click=${this.selectSerialPort}
                >
                  Connect
                </button></label
              >

              ${this.serialPort
                ? html`
                    <div class="metadata">
                      <code>${JSON.stringify(this.serialPort.getInfo())}</code>
                    </div>
                  `
                : ''}
            </li>

            <li>
              <label
                >Flash the firmware
                <button
                  ?disabled=${!this.serialPort}
                  @click=${this.flashFirmware}
                >
                  Flash
                </button></label
              >

              <div class="metadata">
                <progress
                  .value=${this.serialPort
                    ? this.uploadProgress === undefined
                      ? null
                      : this.uploadProgress
                    : 0}
                  ?disabled=${!this.serialPort}
                  max="100"
                ></progress>
              </div>
            </li>
          </ol>
        </main>

        <section>
          <h3>Debug Log</h3>
          <pre><div class="debuglog"></div></pre>
        </section>
      `;
    }
  }
}
