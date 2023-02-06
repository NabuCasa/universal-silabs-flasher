import { LitElement, html, css } from 'lit';
import { customElement, state, property } from 'lit/decorators.js';
import type { Pyodide } from './setup-pyodide';

import '@material/mwc-button';
import '@material/mwc-icon-button';
import '@material/mwc-linear-progress';
import '@material/mwc-formfield';
import '@material/mwc-radio';
import '@material/mwc-dialog';
import './usf-file-upload.js';

import './pyodide-loader.js';
import './firmware-selector.js';
import type { Manifest } from './const';

import { downloadFile } from './utils.js';

enum UploadProgressState {
  IDLE,
  CONNECTING,
  FLASHING,
}

enum FlashingStep {
  IDLE,
  SELECTING_PORT,
  PORT_SELECTION_CANCELLED,
  LOADING_PYODIDE,
  PROBING,
  PROBING_COMPLETE,
  PROBING_FAILED,
  SELECT_FIRMWARE,
  INSTALLING,
  INSTALL_FAILED,
  DONE,
}

@customElement('flashing-dialog')
export class FlashingDialog extends LitElement {
  static styles = css`
    mwc-dialog {
      --mdc-dialog-min-width: 330px;
    }

    .metadata {
      font-size: 0.8em;
    }

    img {
      vertical-align: middle;
    }

    mwc-icon-button[icon='close'] {
      position: absolute;
      top: 10px;
      right: 10px;
    }

    p.spinner {
      text-align: center;
      font-size: 2em;
    }

    p.firmware-metadata {
      font-size: 0.8em;
      line-height: 1.2;
      overflow: auto;
    }

    span.progress-text {
      font-size: 0.8em;
    }
  `;

  @state()
  private flashingStep: FlashingStep = FlashingStep.IDLE;

  @property()
  public pyodide?: Pyodide;

  @property()
  public manifest!: Manifest;

  private debugLog: string = '';

  @state()
  private selectedFirmware?: any;

  @state()
  private serialPort?: SerialPort;

  private pyFlasher?: any;

  @state()
  private uploadProgress: Number = 0;

  @state()
  private progressState: UploadProgressState = UploadProgressState.IDLE;

  public connectedCallback() {
    super.connectedCallback();

    // Immediately open the serial port selection interface on connect
    this.selectSerialPort();
  }

  public firstUpdated(changedProperties: Map<string, any>) {
    super.firstUpdated(changedProperties);

    this.shadowRoot!.querySelector('mwc-dialog')!.addEventListener(
      'close',
      this.close
    );
  }

  private getFirmwareMetadata() {
    if (!this.selectedFirmware) {
      return html``;
    }

    let metadata;

    try {
      metadata = this.selectedFirmware.get_nabucasa_metadata();
    } catch (e) {
      return html`<code>unknown</code>`;
    }

    return html`
      <table>
        <tbody>
          <tr>
            <th>Type</th>
            <td>${metadata.fw_type.name}</td>
          </tr>
          <tr>
            <th>SDK Version</th>
            <td>${metadata.sdk_version}</td>
          </tr>
          <tr>
            <th>EZSP Version</th>
            <td>${metadata.ezsp_version || '-'}</td>
          </tr>
        </tbody>
      </table>
    `;
  }

  private async selectSerialPort() {
    this.flashingStep = FlashingStep.SELECTING_PORT;

    try {
      this.serialPort = await navigator.serial.requestPort({
        filters: this.manifest.usb_filters.map(f => ({
          usbProductId: f.pid,
          usbVendorId: f.vid,
        })),
      });
    } catch {
      this.serialPort = undefined;
      this.flashingStep = FlashingStep.PORT_SELECTION_CANCELLED;
      return;
    }

    this.flashingStep = FlashingStep.LOADING_PYODIDE;
  }

  private async onPyodideLoaded(e: CustomEvent) {
    this.pyodide = e.detail.pyodide;

    this.pyodide.setStdout({
      batched: (msg: string) => {
        console.log(msg);
        this.debugLog += `${msg}\n`;
      },
    });

    this.pyodide.setStderr({
      batched: (msg: string) => {
        console.warn(msg);
        this.debugLog += `${msg}\n`;
      },
    });

    // Set up the flasher
    this.pyodide
      .pyimport('webserial_transport')
      .set_global_serial_port(this.serialPort);

    const { Flasher } = this.pyodide.pyimport(
      'universal_silabs_flasher.flasher'
    );

    this.pyFlasher = Flasher.callKwargs({
      bootloader_baudrate: this.manifest.bootloader_baudrate,
      app_baudrate: this.manifest.application_baudrate,
      device: '/dev/webserial', // the device name is ignored
    });

    await this.detectRunningFirmware();
  }

  private async detectRunningFirmware() {
    this.flashingStep = FlashingStep.PROBING;

    try {
      await this.pyFlasher.probe_app_type();
    } catch (e) {
      this.pyFlasher = undefined;
      this.serialPort = undefined;

      this.flashingStep = FlashingStep.PROBING_FAILED;
      return;
    }

    this.flashingStep = FlashingStep.PROBING_COMPLETE;
  }

  private selectFirmware() {
    this.flashingStep = FlashingStep.SELECT_FIRMWARE;
  }

  private onFirmwareLoaded(e: CustomEvent) {
    this.selectedFirmware = e.detail.firmware;
  }

  private async flashFirmware() {
    this.flashingStep = FlashingStep.INSTALLING;
    await this.pyFlasher.enter_bootloader();

    try {
      await this.pyFlasher.flash_firmware.callKwargs(this.selectedFirmware, {
        progress_callback: (current: number, total: number) => {
          this.uploadProgress = current / total;
        },
      });
      this.flashingStep = FlashingStep.DONE;
    } catch (e) {
      this.flashingStep = FlashingStep.INSTALL_FAILED;
    }
  }

  private async close() {
    if (this.serialPort) {
      await this.serialPort.close();
    }

    this.parentNode!.removeChild(this);
  }

  private showDebugLog() {
    const debugText = `data:text/plain;charset=utf-8,${encodeURIComponent(
      this.debugLog
    )}`;

    downloadFile(debugText, 'silabs_flasher.log');
  }

  render() {
    let content = html``;
    let headingText = 'Connecting';
    let showDebugLogButton = true;
    let showCloseButton = true;

    if (this.flashingStep === FlashingStep.SELECTING_PORT) {
      showDebugLogButton = false;
      headingText = 'Select a serial port';
      content = html`<p>
        <p class="spinner"><mwc-circular-progress indeterminate density=8></mwc-circular-progress></p>
        <p>Plug in and select your SkyConnect</p>
      </p>`;
    } else if (this.flashingStep === FlashingStep.PORT_SELECTION_CANCELLED) {
      showDebugLogButton = false;
      headingText = 'Serial port was not selected';
      content = html`<p>
          If you didn't select a serial port because the SkyConnect was missing,
          make sure the USB port it's plugged into works and the SkyConnect is
          detected by your operating system.
        </p>
        <p>
          If you are using Windows or macOS, install the
          <a
            href="https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers?tab=downloads"
            target="_blank"
            rel="noreferrer noopener"
            >Silicon Labs CP2102 driver</a
          >.
        </p>

        <mwc-button slot="primaryAction" @click=${this.selectSerialPort}>
          Retry
        </mwc-button> `;
    } else if (this.flashingStep === FlashingStep.LOADING_PYODIDE) {
      showDebugLogButton = false;
      headingText = 'Loading environment';
      content = html`<pyodide-loader
        @load=${this.onPyodideLoaded}
      ></pyodide-loader>`;
    } else if (this.flashingStep === FlashingStep.PROBING) {
      headingText = 'Detecting firmware';
      content = html`<p>
        <p class="spinner"><mwc-circular-progress indeterminate density=8></mwc-circular-progress></p>
        <p>
          Detecting the current firmware.
          <br />
          This can take a few seconds...
        </p>
      </p>`;
    } else if (this.flashingStep === FlashingStep.PROBING_FAILED) {
      headingText = 'Connection failed';
      content = html`<p>The running firmware could not be detected.</p>

        <p>
          Make sure the USB port works and if you are using a USB extension
          cable, make sure the cable can transfer data. Unplug the SkyConnect
          and plug it back in to reset and try again.
        </p>

        <mwc-button slot="primaryAction" @click=${this.selectSerialPort}>
          Retry
        </mwc-button>`;
    } else if (this.flashingStep === FlashingStep.PROBING_COMPLETE) {
      headingText = 'Connection successful';
      content = html`<p>
          Current firmware type: <code>${this.pyFlasher.app_type.name}</code>
          <br />
          Current firmware version: <code>${this.pyFlasher.app_version}</code>
        </p>

        <mwc-button slot="primaryAction" @click=${this.selectFirmware}>
          Next
        </mwc-button> `;
    } else if (this.flashingStep === FlashingStep.SELECT_FIRMWARE) {
      headingText = 'Select firmware';
      content = html`
        <p>Select new firmware to install to your SkyConnect.</p>

        <firmware-selector
          .pyodide=${this.pyodide}
          .manifest=${this.manifest}
          @firmwareLoaded=${this.onFirmwareLoaded}
        ></firmware-selector>

        ${this.selectedFirmware
          ? html`<p class="firmware-metadata">${this.getFirmwareMetadata()}</p>`
          : ''}

        <mwc-button
          slot="primaryAction"
          @click=${this.flashFirmware}
          .disabled=${!this.selectedFirmware}
        >
          Install
        </mwc-button>
      `;
    } else if (this.flashingStep === FlashingStep.INSTALLING) {
      // Hide the close button to prevent it from being accidentally clicked during flashing.
      // The bootloader is resilient so nothing will actually break that can't be fixed by retrying.
      showCloseButton = false;
      headingText = 'Installing firmware';
      content = html`
        <p>
          The new firmware is now installing. Do not disconnect the SkyConnect
          or close this browser window.
        </p>
        <p>
          <span class="progress-text"
            >Progress: ${(+this.uploadProgress * 100).toFixed(1)}%</span
          >
          <mwc-linear-progress
            .progress=${this.uploadProgress}
            ?indeterminate=${this.uploadProgress < 0.01}
          ></mwc-linear-progress>
        </p>
      `;
    } else if (this.flashingStep === FlashingStep.INSTALL_FAILED) {
      headingText = 'Installation failed';
      content = html`
        <p>
          Firmware installation failed. Unplug your SkyConnect and plug it back
          in to retry.
        </p>

        <mwc-button slot="primaryAction" @click=${this.selectSerialPort}>
          Retry
        </mwc-button>
      `;
    } else if (this.flashingStep === FlashingStep.DONE) {
      headingText = 'Installation success';
      content = html`
        <p>Firmware has been successfully installed.</p>

        <mwc-button slot="primaryAction" dialogAction="close">
          Done
        </mwc-button>
      `;
    }

    return html`
      <mwc-dialog
        open
        heading="${headingText}"
        scrimClickAction=""
        escapeKeyAction=""
      >
        ${showCloseButton
          ? html`
              <mwc-icon-button
                icon="close"
                dialogAction="close"
              ></mwc-icon-button>
            `
          : ''}
        ${content}
        ${showDebugLogButton
          ? html`
              <mwc-button slot="secondaryAction" @click=${this.showDebugLog}>
                Debug Log
              </mwc-button>
            `
          : ''}
      </mwc-dialog>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'flashing-dialog': FlashingDialog;
  }
}
