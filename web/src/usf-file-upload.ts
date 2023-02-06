import { LitElement, html } from 'lit';
import { customElement, query, property } from 'lit/decorators.js';

import '@material/mwc-button';

@customElement('usf-file-upload')
export class FileUpload extends LitElement {
  fileChanged(e: Event) {
    this.requestUpdate();
    this.dispatchEvent(new Event(e.type, e));
  }

  buttonClicked() {
    this.fileInput.click();
  }

  @query('#file')
  private fileInput!: HTMLInputElement;

  @property({ type: Boolean })
  public disabled = false;

  @property()
  public accept?: string;

  get files() {
    if (!this.fileInput) {
      return null;
    }

    return this.fileInput.files;
  }

  render() {
    return html`
      <input
        id="file"
        type="file"
        accept=${this.accept}
        hidden
        @change=${this.fileChanged}
      />

      <mwc-button
        raised
        ?disabled=${this.disabled}
        @click=${this.buttonClicked}
      >
        <slot></slot>
      </mwc-button>

      ${this.files
        ? html`<span
            >${this.files.length > 0
              ? this.files[0].name
              : 'No file selected'}</span
          >`
        : ''}
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'usf-file-upload': FileUpload;
  }
}
