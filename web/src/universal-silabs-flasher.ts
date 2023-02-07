import { LitElement, html } from 'lit';
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
      ${supportsWebSerial
        ? html`<mwc-button raised @click=${this.openFlasherDialog}
            ><slot name="button">Connect</slot></mwc-button
          >`
        : html`<slot name="no-webserial"
            ><strong>
              Unfortunately, your browser does not support Web Serial. Open this
              page in Google Chrome or Microsoft Edge.
            </strong></slot
          >`}
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'unversal-silabs-flasher': UniversalSilabsFlasher;
  }
}
