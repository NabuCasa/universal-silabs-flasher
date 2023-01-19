import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { setupPyodide, PyodideLoadState } from '../setup-pyodide.js';

import '@material/mwc-dialog';
import '@material/mwc-button';
import '@material/mwc-circular-progress';

@customElement('pyodide-loader')
export class PyodideLoader extends LitElement {
  @state()
  private pyodideLoadState: PyodideLoadState = PyodideLoadState.LOADING_PYODIDE;

  public async setupPyodide() {
    const pyodide = await setupPyodide(newLoadState => {
      this.pyodideLoadState = newLoadState;
    });

    this.dispatchEvent(
      new CustomEvent('load', {
        detail: {
          pyodide,
        },
        bubbles: true,
        composed: true,
      })
    );
  }

  connectedCallback() {
    super.connectedCallback();
    this.setupPyodide();
  }

  public render() {
    let heading;

    if (this.pyodideLoadState === PyodideLoadState.LOADING_PYODIDE) {
      heading = html`Loading Pyodide`;
    } else if (
      this.pyodideLoadState === PyodideLoadState.INSTALLING_DEPENDENCIES
    ) {
      heading = html`Installing Python dependencies`;
    } else if (
      this.pyodideLoadState === PyodideLoadState.INSTALLING_TRANSPORT
    ) {
      heading = html`Setting up serial transport`;
    } else {
      heading = html`Loading`;
    }

    return html`
      <h3>
        <mwc-circular-progress
          class="progress"
          indeterminate
        ></mwc-circular-progress>
        <span class="title">${heading}</span>
      </h3>
      <p>This may take a minute...</p>
    `;
  }

  static styles = css`
    :host {
      --mdc-dialog-min-width: 450px;
      --mdc-dialog-max-width: 560px;
    }

    h3 {
      display: flex;
    }

    h3 .progress,
    h3 .title {
      align-self: center;
      display: inline-flex;
    }

    h3 .title {
      margin-left: 1em;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'pyodide-loader': PyodideLoader;
  }
}
