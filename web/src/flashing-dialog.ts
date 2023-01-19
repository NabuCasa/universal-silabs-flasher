import { LitElement, html, css } from 'lit';
import { customElement, state, query, property } from 'lit/decorators.js';
import type { Pyodide } from './setup-pyodide';

import '@material/mwc-button';
import '@material/mwc-linear-progress';
import '@material/mwc-formfield';
import '@material/mwc-radio';
import '@material/mwc-dialog';
import './mwc-file-upload.js';

import './dialogs/pyodide-loader.js';
import './dialogs/firmware-selector.js';

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
  DONE,
}

@customElement('flashing-dialog')
export class FlashingDialog extends LitElement {
  static styles = css`
    li {
      margin-top: 1em;
    }

    .metadata {
      font-size: 0.8em;
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
  private flashingStep: FlashingStep = FlashingStep.IDLE;

  @property()
  public pyodide?: Pyodide;

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
    this.flashingStep = FlashingStep.SELECTING_PORT;

    try {
      this.serialPort = await navigator.serial.requestPort();
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

    // Set up the flasher
    this.pyodide
      .pyimport('webserial_transport')
      .set_global_serial_port(this.serialPort);

    const { Flasher } = this.pyodide.pyimport(
      'universal_silabs_flasher.flasher'
    );

    this.pyFlasher = Flasher.callKwargs({
      bootloader_baudrate: 115200,
      app_baudrate: 115200,
      device: '/dev/webserial', // the device name is ignored
    });

    await this.detectRunningFirmware();
  }

  private async detectRunningFirmware() {
    this.flashingStep = FlashingStep.PROBING;

    try {
      await this.pyFlasher.probe_app_type();
    } catch {
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

    await this.pyFlasher.flash_firmware.callKwargs(this.selectedFirmware, {
      progress_callback: (current: number, total: number) => {
        this.uploadProgress = current / total;
      },
    });

    this.flashingStep = FlashingStep.DONE;
  }

  private close() {
    this.parentNode!.removeChild(this);
  }

  render() {
    let content = html``;
    let heading = 'Connecting';

    if (this.flashingStep === FlashingStep.SELECTING_PORT) {
      heading = 'Select a serial port';
      content = html`<p>
        <mwc-circular-progress indeterminate></mwc-circular-progress> Waiting
        for serial port...
      </p>`;
    } else if (this.flashingStep === FlashingStep.PORT_SELECTION_CANCELLED) {
      heading = 'Serial port was not selected';
      content = html`<p>
          If you didn't select a serial port because the SkyConnect was missing,
          make sure the USB port it's plugged into works. If you are using
          Windows or macOS, sure you have installed the
          <a
            href="https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers?tab=downloads"
            >Silicon Labs CP2102 driver</a
          >.
        </p>

        <mwc-button slot="primaryAction" @click=${this.close}>
          Done
        </mwc-button> `;
    } else if (this.flashingStep === FlashingStep.LOADING_PYODIDE) {
      heading = 'Loading environment';
      content = html`<pyodide-loader
        @load=${this.onPyodideLoaded}
      ></pyodide-loader>`;
    } else if (this.flashingStep === FlashingStep.PROBING) {
      heading = 'Detecting current firmware';
      content = html`<p>
        <mwc-circular-progress indeterminate></mwc-circular-progress> Detecting
        the current firmware...
      </p>`;
    } else if (this.flashingStep === FlashingStep.PROBING_FAILED) {
      heading = 'Connection failed';
      content = html`<p>
        The running firmware could not be detected. Make sure the USB port works
        and if you are using a USB extension cable, make sure the cable can
        transfer data. Unplug the SkyConnect and plug it back in to reset it and
        to try again.
      </p>`;
    } else if (this.flashingStep === FlashingStep.PROBING_COMPLETE) {
      heading = 'Connection successful';
      content = html`<p>
          Current firmware type: <code>${this.pyFlasher.app_type.name}</code>
        </p>
        <p>
          Current firmware version: <code>${this.pyFlasher.app_version}</code>
        </p>

        <mwc-button slot="primaryAction" @click=${this.selectFirmware}>
          Next
        </mwc-button> `;
    } else if (this.flashingStep === FlashingStep.SELECT_FIRMWARE) {
      heading = 'Select new firmware to install';
      content = html`
        <firmware-selector
          .pyodide=${this.pyodide}
          @firmwareLoaded=${this.onFirmwareLoaded}
        ></firmware-selector>

        ${this.selectedFirmware
          ? html`<p>
              <code>${this.getFirmwareMetadataString()}</code>
            </p>`
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
      heading = 'Installing firmware';
      content = html`
        <p>
          The new firmware is now installing. Do not disconnect the device or
          close this browser window!
        </p>
        <p>
          <span>Progress: ${(+this.uploadProgress * 100).toFixed(1)}%</span>
          <mwc-linear-progress
            .progress=${this.uploadProgress}
            ?indeterminate=${this.uploadProgress < 0.01}
          ></mwc-linear-progress>
        </p>
      `;
    } else if (this.flashingStep === FlashingStep.DONE) {
      heading = 'Installation success';
      content = html`
        <p>Firmware installation is successful.</p>

        <mwc-button slot="primaryAction" @click=${this.close}>
          Done
        </mwc-button>
      `;
    }

    return html`
      <mwc-dialog
        open
        heading="${heading}"
        scrimClickAction=""
        escapeKeyAction=""
      >
        ${content}
      </mwc-dialog>

      <section>
        <h3>Debug Log</h3>
        <pre><div class="debuglog"></div></pre>
      </section>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'flashing-dialog': FlashingDialog;
  }
}
