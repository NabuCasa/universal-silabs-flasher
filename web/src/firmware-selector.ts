import { LitElement, html, css } from 'lit';
import { customElement, state, property } from 'lit/decorators.js';
import { classMap } from 'lit/directives/class-map.js';
import { mdiFileUpload } from '@mdi/js';

import type { Pyodide } from './setup-pyodide';
import { FirmwareIcons } from './const';
import type { Manifest } from './const';
import './usf-file-upload';
import './usf-icon';

import '@material/mwc-dialog';
import '@material/mwc-button';
import '@material/mwc-formfield';
import '@material/mwc-radio';
import '@material/mwc-circular-progress';

type GBLImage = any;
const CUSTOM_UPLOAD_INDEX = 9999;

async function readFile(file: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result! as ArrayBuffer);
    reader.onerror = e => reject(e);
    reader.readAsArrayBuffer(file);
  });
}

export async function parseFirmwareBuffer(
  pyodide: Pyodide,
  buffer: ArrayBuffer
): Promise<GBLImage> {
  const { GBLImage } = pyodide.pyimport('universal_silabs_flasher.flasher');

  return await GBLImage.from_bytes.callKwargs(pyodide.toPy(buffer), {});
}

@customElement('firmware-selector')
export class FirmwareSelector extends LitElement {
  @property()
  public pyodide: Pyodide;

  @property()
  public manifest!: Manifest;

  @state()
  private firmwareUploadIndex = 0;

  private firmwareLoaded(firmware?: GBLImage) {
    this.dispatchEvent(
      new CustomEvent('firmwareLoaded', {
        detail: { firmware },
        bubbles: true,
        composed: true,
      })
    );
  }

  firstUpdated() {
    this.renderRoot
      .querySelector('mwc-radio')!
      .dispatchEvent(new Event('change'));
  }

  private async firmwareUploadTypeChanged(event: Event) {
    this.firmwareUploadIndex = parseInt(
      (event!.target! as HTMLInputElement).value!,
      10
    );

    // The GBL file upload element will be rendered empty
    if (this.firmwareUploadIndex === CUSTOM_UPLOAD_INDEX) {
      this.firmwareLoaded(undefined);

      return;
    }

    // Download the firmware
    const firmware = this.manifest.firmwares[this.firmwareUploadIndex];
    const response = await fetch(firmware.url);

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
    let firmware: GBLImage;

    try {
      firmware = parseFirmwareBuffer(this.pyodide, buffer);
    } catch (e) {
      firmware = undefined;
      alert(`Failed to parse firmware: ${e}`);
    }

    this.firmwareLoaded(firmware);
  }

  public render() {
    return html`
      ${this.manifest.firmwares.map(
        (fw, index) =>
          html`
            <div>
              <mwc-formfield label="${fw.name}">
                <mwc-radio
                  name="firmware"
                  .value=${index}
                  @change=${this.firmwareUploadTypeChanged}
                  ?checked=${index === 0}
                ></mwc-radio>

                <usf-icon .icon=${FirmwareIcons[fw.type]}></usf-icon>
              </mwc-formfield>
            </div>
          `
      )}
      ${this.manifest.allow_custom_firmware_upload
        ? html`
            <div>
              <mwc-formfield label="Upload your own firmware">
                <mwc-radio
                  name="firmware"
                  .value="${CUSTOM_UPLOAD_INDEX}"
                  @change=${this.firmwareUploadTypeChanged}
                  ?checked=${this.manifest.firmwares.length === 0}
                ></mwc-radio>

                <usf-icon .icon=${mdiFileUpload}></usf-icon>
              </mwc-formfield>

              <usf-file-upload
                class=${classMap({
                  hidden: this.firmwareUploadIndex !== CUSTOM_UPLOAD_INDEX,
                })}
                accept=".gbl"
                ?disabled=${this.firmwareUploadIndex !== CUSTOM_UPLOAD_INDEX}
                @change=${this.customFirmwareChosen}
                >Upload</usf-file-upload
              >
            </div>
          `
        : ''}
    `;
  }

  static styles = css`
    .hidden {
      display: none;
    }

    mwc-formfield {
      display: block;
    }

    usf-icon {
      margin-right: 1em;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'firmware-selector': FirmwareSelector;
  }
}
