import { LitElement, html, css } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import '@material/mwc-dialog';
import '@material/mwc-button';
import '@material/mwc-circular-progress';

@customElement('connect-dialog')
class ConnectDialog extends LitElement {
  @property()
  public pyFlasher!: any;

  public close() {
    this.parentNode!.removeChild(this);
  }

  public render() {
    return html`
      <mwc-dialog
        open
        heading="Detecting current firmware"
        scrimClickAction=""
        escapeKeyAction=""
      >
        <p>
          <mwc-circular-progress
            class="progress"
            indeterminate
          ></mwc-circular-progress>

          This can take a minute.
        </p>
      </mwc-dialog>
    `;
  }

  static styles = css`
    :host {
      --mdc-dialog-min-width: 450px;
      --mdc-dialog-max-width: 560px;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'connect-dialog': ConnectDialog;
  }
}

export async function showConnectDialog(pyFlasher: any): Promise<void> {
  const dialog = document.createElement('connect-dialog') as ConnectDialog;
  dialog.pyFlasher = pyFlasher;
  document.body.appendChild(dialog);
}
