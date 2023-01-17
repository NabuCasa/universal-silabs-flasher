import { LitElement, html, css } from 'lit';
import { customElement } from 'lit/decorators.js';
import './flashing-form.js';

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

  render() {
    return html`
      <h1>
        <img
          src="https://skyconnect.home-assistant.io/static/skyconnect_header.png"
          alt="SkyConnect logo"
        />
        SkyConnect Flasher
      </h1>

      <section>
        <p>
          Flash new firmware to your SkyConnect! In case something doesn't work,
          just unplug the SkyConnect and plug it back in.
        </p>

        <p>
          Note: on macOS, make sure to select
          <code>cu.SLAB_USBtoUART</code> as the serial port.
          <code>cu.usbserial*10</code> does not work.
        </p>

        <flashing-form></flashing-form>
      </section>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'unversal-silabs-flasher': UniversalSilabsFlasher;
  }
}
