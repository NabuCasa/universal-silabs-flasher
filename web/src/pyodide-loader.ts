import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { setupPyodide, PyodideLoadState } from './setup-pyodide';

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
    const steps = [];

    if (this.pyodideLoadState >= PyodideLoadState.LOADING_PYODIDE) {
      steps.push(html`<li>Loading Python</li>`);
    }

    if (this.pyodideLoadState >= PyodideLoadState.INSTALLING_DEPENDENCIES) {
      steps.push(html`<li>Installing dependencies</li>`);
    }

    if (this.pyodideLoadState >= PyodideLoadState.INSTALLING_TRANSPORT) {
      steps.push(html`<li>Setting up transport</li>`);
    }

    if (this.pyodideLoadState >= PyodideLoadState.READY) {
      steps.push(html`<li>Initializing</li>`);
    }

    return html`
      <p>This may take a minute...</p>

      <div id="container">
        <mwc-circular-progress
          class="progress"
          indeterminate
          density="8"
        ></mwc-circular-progress>
        <ol>
          ${steps}
        </ol>
      </div>
    `;
  }

  static styles = css`
    #container {
      display: flex;
      font-size: 0.9em;
    }

    #container > * {
      align-self: center;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'pyodide-loader': PyodideLoader;
  }
}
