import { LitElement, html, css } from 'lit';
import { customElement, state, property } from 'lit/decorators.js';
import { classMap } from 'lit/directives/class-map.js';

import type { Pyodide } from './setup-pyodide.js';
import './usf-file-upload.js';

import '@material/mwc-dialog';
import '@material/mwc-button';
import '@material/mwc-circular-progress';

type GBLImage = any;

enum FirmwareUploadType {
  SKYCONNECT_NCP = './assets/firmwares/NabuCasa_SkyConnect_EZSP_v7.1.3.0_ncp-uart-hw_115200.gbl',
  SKYCONNECT_RCP = './assets/firmwares/NabuCasa_SkyConnect_RCP_v4.2.0_rcp-uart-hw-802154_115200.gbl',
  CUSTOM_GBL = 'custom_gbl',
}

async function readFile(file: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result! as ArrayBuffer);
    reader.onerror = e => reject(e);
    reader.readAsArrayBuffer(file);
  });
}

@customElement('firmware-selector')
export class FirmwareSelector extends LitElement {
  @property()
  public pyodide: Pyodide;

  @state()
  private firmwareUploadType?: FirmwareUploadType;

  private firmwareLoaded(firmware?: GBLImage) {
    this.dispatchEvent(
      new CustomEvent('firmwareLoaded', {
        detail: { firmware },
        bubbles: true,
        composed: true,
      })
    );
  }

  private async firmwareUploadTypeChanged(event: Event) {
    this.firmwareUploadType = (event!.target! as HTMLInputElement)
      .value! as FirmwareUploadType;

    // The GBL file upload element will be rendered empty
    if (this.firmwareUploadType === FirmwareUploadType.CUSTOM_GBL) {
      this.firmwareLoaded(undefined);

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

    let firmware: GBLImage;

    try {
      firmware = GBLImage.from_bytes(this.pyodide.toPy(buffer));
    } catch (e) {
      firmware = undefined;
      alert(`Failed to parse firmware: ${e}`);
    }

    this.firmwareLoaded(firmware);
  }

  public render() {
    return html`
      <div>
        <mwc-formfield label="Zigbee (EZSP)">
          <mwc-radio
            name="firmware"
            .value="${FirmwareUploadType.SKYCONNECT_NCP}"
            @change=${this.firmwareUploadTypeChanged}
          ></mwc-radio>
        </mwc-formfield>
      </div>

      <div>
        <mwc-formfield label="Multi-PAN (RCP)">
          <mwc-radio
            name="firmware"
            .value="${FirmwareUploadType.SKYCONNECT_RCP}"
            @change=${this.firmwareUploadTypeChanged}
          ></mwc-radio>
        </mwc-formfield>
      </div>

      <div>
        <mwc-formfield label="Upload your own firmware">
          <mwc-radio
            name="firmware"
            .value="${FirmwareUploadType.CUSTOM_GBL}"
            @change=${this.firmwareUploadTypeChanged}
          ></mwc-radio>
        </mwc-formfield>

        <usf-file-upload
          class=${classMap({
            hidden: this.firmwareUploadType !== FirmwareUploadType.CUSTOM_GBL,
          })}
          accept=".gbl"
          ?disabled=${this.firmwareUploadType !== FirmwareUploadType.CUSTOM_GBL}
          @change=${this.customFirmwareChosen}
          >Upload</usf-file-upload
        >
      </div>
    `;
  }

  static styles = css`
    .hidden {
      display: none;
    }

    mwc-formfield {
      display: block;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'firmware-selector': FirmwareSelector;
  }
}
