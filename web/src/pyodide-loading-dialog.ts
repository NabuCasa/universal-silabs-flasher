import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { Pyodide, setupPyodide, PyodideLoadState } from './setup-pyodide.js';

import '@material/mwc-dialog';
import '@material/mwc-button';
import '@material/mwc-circular-progress';

@customElement('pyodide-loading-dialog')
class PyodideLoadingDialog extends LitElement {
  public close() {
    this.parentNode!.removeChild(this);
  }

  @state()
  private pyodideLoadState: PyodideLoadState = PyodideLoadState.LOADING_PYODIDE;

  public async setupPyodide() {
    const pyodide = await setupPyodide(newLoadState => {
      this.pyodideLoadState = newLoadState;
    });

    return pyodide;
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
      <mwc-dialog
        open
        heading="Initializing installer"
        scrimClickAction=""
        escapeKeyAction=""
      >
        <h3>
          <mwc-circular-progress
            class="progress"
            indeterminate
          ></mwc-circular-progress>
          <span class="title">${heading}</span>
        </h3>
        <p>This may take a minute...</p>
      </mwc-dialog>
    `;
  }

  static styles = css`
    :host {
      --mdc-dialog-min-width: 450px;
      --mdc-dialog-max-width: 560px;
    }

    mwc-dialog h3 {
      display: flex;
    }

    mwc-dialog h3 .progress,
    mwc-dialog h3 .title {
      align-self: center;
      display: inline-flex;
    }

    mwc-dialog h3 .title {
      margin-left: 1em;
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    'pyodide-loading-dialog': PyodideLoadingDialog;
  }
}

export async function loadPyodideWithDialog(): Promise<Pyodide> {
  const dialog = document.createElement('pyodide-loading-dialog');
  document.body.appendChild(dialog);

  const pyodide = await dialog.setupPyodide();
  dialog.close();

  return pyodide;
}
