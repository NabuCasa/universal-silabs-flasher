import { LitElement, html, css } from 'lit';
import { customElement, property } from 'lit/decorators.js';
import type { Manifest } from './const';
import './flashing-dialog';

import '@material/mwc-button';

@customElement('universal-silabs-flasher')
class UniversalSilabsFlasher extends LitElement {
  @property()
  public manifest!: string;

  async openFlasherDialog() {
    const response = await fetch(this.manifest);
    const manifest: Manifest = await response.json();

    const dialog = document.createElement('flashing-dialog');
    dialog.manifest = manifest;
    document.body.appendChild(dialog);
  }

  render() {
    const supportsWebSerial = 'serial' in navigator;

    return html`
      <h1>
        <img
          src="https://skyconnect.home-assistant.io/images/skyconnect-logo.png"
          alt="SkyConnect logo"
        />
        Flasher
      </h1>

      <section>
        <p>To get started, plug your SkyConnect into this computer.</p>

        ${supportsWebSerial
          ? html`<mwc-button raised @click=${this.openFlasherDialog}
              >Connect</mwc-button
            >`
          : html`<p id="webserial-unsupported">
              Unfortunately, your browser does not support Web Serial. Open this
              page in Google Chrome or Microsoft Edge.
            </p>`}
      </section>
    `;
  }

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: flex-start;
      font-size: 1em;

      padding: 5em;

      max-width: 600px;
      margin-left: auto;
      margin-right: auto;
    }

    img {
      vertical-align: middle;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'unversal-silabs-flasher': UniversalSilabsFlasher;
  }
}
