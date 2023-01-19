import { LitElement, html, css } from 'lit';
import { customElement } from 'lit/decorators.js';
import './flashing-dialog.js';

import '@material/mwc-button';

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

    #webserial-unsupported {
    }
  `;

  static openFlasherDialog() {
    const dialog = document.createElement('flashing-dialog');
    document.body.appendChild(dialog);
  }

  render() {
    const supportsWebSerial = 'serial' in navigator;

    return html`
      <h1>
        <img
          src="https://skyconnect.home-assistant.io/static/skyconnect_header.png"
          alt="SkyConnect logo"
        />
        SkyConnect Flasher
      </h1>

      <section>
        <p>To get started, plug your SkyConnect into this computer.</p>

        ${supportsWebSerial
          ? html`<mwc-button
              raised
              @click=${UniversalSilabsFlasher.openFlasherDialog}
              >Connect</mwc-button
            >`
          : html`<p id="webserial-unsupported">
              Unfortunately, your browser does not support Web Serial. Open this
              page in Google Chrome or Microsoft Edge.
            </p>`}
      </section>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'unversal-silabs-flasher': UniversalSilabsFlasher;
  }
}
