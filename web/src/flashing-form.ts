import { LitElement, html, css } from 'lit';
import { customElement, state, query, property } from 'lit/decorators.js';
import type { Pyodide } from './setup-pyodide';

async function readFile(file: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result! as ArrayBuffer);
    reader.onerror = e => reject(e);
    reader.readAsArrayBuffer(file);
  });
}

enum FirmwareUploadType {
  SKYCONNECT_NCP = './assets/firmwares/NabuCasa_SkyConnect_EZSP_v7.1.3.0_ncp-uart-hw_115200.gbl',
  SKYCONNECT_RCP = './assets/firmwares/NabuCasa_SkyConnect_RCP_v4.2.0_rcp-uart-hw-802154_115200.gbl',
  CUSTOM_GBL = 'custom_gbl',
}

@customElement('flashing-form')
export class FlashingForm extends LitElement {
  static styles = css`
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

  @property()
  public pyodide: Pyodide;

  @state()
  private firmwareUploadType?: FirmwareUploadType;

  @state()
  private selectedFirmware?: any;

  @state()
  private serialPort?: SerialPort;

  @state()
  private uploadProgress?: Number;

  connectedCallback(): void {
    super.connectedCallback();

    this.pyodide.setStdout({
      batched: (msg: string) => {
        console.log(msg);

        const div = document.createElement('div');
        div.classList.add('stdout');
        div.textContent = msg;
        this.debugLog.appendChild(div);
      },
    });

    this.pyodide.setStderr({
      batched: (msg: string) => {
        console.warn(msg);

        const div = document.createElement('div');
        div.classList.add('stderr');
        div.textContent = msg;
        this.debugLog.appendChild(div);
      },
    });
  }

  firstUpdated(): void {
    // Pre-select the first option
    (
      this.shadowRoot!.querySelector('.firmware input')! as HTMLInputElement
    ).click();
  }

  private async firmwareUploadTypeChanged(event: Event) {
    this.selectedFirmware = null;
    this.firmwareUploadType = (event!.target! as HTMLInputElement)
      .value! as FirmwareUploadType;

    // The GBL file upload element will be rendered empty
    if (this.firmwareUploadType === FirmwareUploadType.CUSTOM_GBL) {
      return;
    }

    // Download the firmware
    const response = await fetch(this.firmwareUploadType as string);

    if (!response.ok) {
      alert(`Failed to download firmware: ${response}`);
      return;
    }

    const firmwareData = await response.arrayBuffer();

    this.loadFirmware(firmwareData);
  }

  private async customFirmwareChosen(event: Event) {
    const file = (event.target! as HTMLInputElement).files![0];
    const firmwareData = await readFile(file);

    this.loadFirmware(firmwareData);
  }

  private loadFirmware(buffer: ArrayBuffer) {
    const { GBLImage } = this.pyodide.pyimport(
      'universal_silabs_flasher.flasher'
    );

    try {
      this.selectedFirmware = GBLImage.from_bytes(this.pyodide.toPy(buffer));
    } catch (e) {
      this.selectedFirmware = undefined;
      alert(`Failed to parse image: ${e}`);
    }
  }

  private getFirmwareMetadataString(): string {
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
        this.uploadProgress = (100.0 * current) / total;
      },
    });
  }

  render() {
    return html`
      <main>
        <ol>
          <li class="firmware">
              <div>Select firmware to flash:</div>

              <div>
                <label
                  ><input
                    type="radio"
                    name="firmware"
                    .value="${FirmwareUploadType.SKYCONNECT_NCP}"
                    .checked=${
                      this.firmwareUploadType ===
                      FirmwareUploadType.SKYCONNECT_NCP
                    }
                    @change=${this.firmwareUploadTypeChanged}
                  />Zigbee</label
                >
              </div>

              <div>
                <label
                  ><input
                    type="radio"
                    name="firmware"
                    .value="${FirmwareUploadType.SKYCONNECT_RCP}"
                    .checked=${
                      this.firmwareUploadType ===
                      FirmwareUploadType.SKYCONNECT_RCP
                    }
                    @change=${this.firmwareUploadTypeChanged}
                  />Multi-PAN (beta)</label
                >
              </div>

              <div>
                <label
                  ><input
                    type="radio"
                    name="firmware"
                    .value="${FirmwareUploadType.CUSTOM_GBL}"
                    .checked=${
                      this.firmwareUploadType === FirmwareUploadType.CUSTOM_GBL
                    }
                    @change=${this.firmwareUploadTypeChanged}
                  />Custom <code>.gbl</code> firmware ${
                    this.firmwareUploadType === FirmwareUploadType.CUSTOM_GBL
                      ? html`<input
                          type="file"
                          accept=".gbl"
                          @change=${this.customFirmwareChosen}
                        />`
                      : ''
                  }</label>
              </div>
            </fieldset>

            ${
              this.selectedFirmware
                ? html`
                    <div class="metadata">
                      <code>${this.getFirmwareMetadataString()}</code>
                    </div>
                  `
                : ''
            }
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

            ${
              this.serialPort
                ? html`<div class="metadata">
                    <code>${JSON.stringify(this.serialPort.getInfo())}</code>
                  </div>`
                : ''
            }
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
                .value=${this.serialPort ? this.uploadProgress : 0}
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

declare global {
  interface HTMLElementTagNameMap {
    'flashing-form': FlashingForm;
  }
}
